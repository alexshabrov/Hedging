import argparse
import os
import sys
import time

# > python live_app.py --symbol=SOLUSDC --mode=short_only --quote=300 --trigger-long-pct=0.01 --trigger-short-pct=0.01 --target-long-pct=0.25 --target-short-pct=0.25 

from live.exchanges.exchange_factory import get_exchange_class, get_realtime_class
from live.logic.hedging import HedgeEngine
from live.logic.models import (
    HedgeConfig,
    HedgeExecutionParams,
    HedgeLeg,
    HedgeMode,
    HedgeOffsetsPctX10000,
    HedgeVolumeRequest,
)

"""
`live_app.py` — минимальный “клиент” для запуска **одностороннего бесконечного хеджа** (LONG_ONLY / SHORT_ONLY)
на базе `live.logic.hedging.HedgeEngine`.

## 1) Что такое “односторонний бесконечный хедж” здесь

Режимы:
- `--mode=long_only`  → хедж “стреляет” только вверх (LONG).
- `--mode=short_only` → хедж “стреляет” только вниз (SHORT).

“Бесконечный” означает:
- мы **не пересоздаём** `exchange`/`realtime` на каждом заходе,
- мы **не пересчитываем** базовые уровни (`lines/base_price_units`) от новой цены при каждом “раунде”,
- после завершения одного раунда (закрылись по `neutral` или `target`) движок автоматически возвращается в
  `WAITING_TRIGGER` и ждёт следующий вход — на тех же уровнях.

Важный эффект: исчезает пауза 2–3 секунды на reconnect между “раундами”.

---

## 2) Как устроен поток исполнения (high-level)

`main()` делает последовательно:

1) Парсит аргументы CLI и валидирует:
   - наличие ключей `BINANCE_KEY`/`BINANCE_SECRET`,
   - валидность `quote`, `entrance_timeout_ms`, `sleep_s`,
   - валидность процентов (перевод в `pct_x10000`).

2) Создаёт `exchange` и `realtime`:
   - Важно: `exchange` создаётся с `hedge_mode=False`, потому что `Position`/`HedgeEngine` работают в one-way.
   - `rt.start()` поднимает realtime поток.
   - Далее `wait_for_connect()` для обоих — без молчаливых ожиданий.

3) Загружает `rules` и берёт `rule` для `symbol`.
   - `rule` нужен только для корректного пересчёта “quote → base_units” с учётом шагов `price_step/lot_step`.

4) Формирует `HedgeConfig`:
   - `trigger_offset_pct_x10000` и `target_offset_pct_x10000` задают величины в процентах * 10000.
   - Внутри `HedgeEngine` из них вычисляются абсолютные уровни `HedgeLines` при первом тике `BookTicker`
     (инициализация делается один раз и больше не меняется в loop-режиме).

5) Создаёт `HedgeEngine` и задаёт callback печати итераций:
   - `hedge.set_on_loop_iteration_started(...)` — это простой пользовательский callback.
     Он вызывается движком, когда начинается новая итерация loop-а (например `init`, `waiting_timeout`, `close_neutral`).
     Тут мы печатаем “жирный” разделитель, чтобы в консоли визуально было видно границы итераций.

6) Запускает бесконечный цикл:
   - `hedge.run_endless(sleep_s=...)` — блокирующий метод, который:
     - включает внутренний loop-режим (single-side),
     - вызывает `hedge.start()` (если ещё не стартовал),
     - дальше опрашивает `hedge.status()`/`hedge.check()` с периодом `sleep_s`,
       пока движок не завершится (graceful stop / close / failure),
     - и в `finally` гарантирует `hedge.stop()`.

7) Cleanup в `finally` (как в `live_example.py`):
   - если основной код упал и cleanup тоже упал — поднимаем комбинированную ошибку,
     чтобы было понятно, что “сломались и работа, и уборка”.

---

## 3) Как считается объём (что такое `quote`)

CLI параметр `--quote` — это фиксированный “notional” в котируемой валюте (например USDC).
На каждом входе `HedgeEngine` вызывает `on_volume(req)` и просит вернуть `base_units` (целое число лотов).

Мы делаем ровно то, что делал исходный клиент:
1) Берём `req.price_units` (цена в целочисленных units).
2) Переводим её во float цену через `price_step`.
3) Считаем base-объём: `base = quote / price`.
4) Переводим base в `base_units` через `lot_step`.

Важно:
- в one-way single-side режиме мы дополнительно проверяем, что `req.leg` совпадает с ожидаемым (`expected_leg`).
- если по шагам/цене получается `base_units <= 0` — это сразу ошибка (никаких “попробуем потом”).

---

## 4) Как устроена итерация loop внутри HedgeEngine

Движок имеет состояния:
- `WAITING_TRIGGER` → ждём пробой порога для входа
- `EXECUTING` → исполняем вход (maker chase)
- `ACTIVE` → позиция открыта, ждём выхода
- `CLOSING` → исполняем выход (maker chase)
- далее:
  - в loop-режиме single-side: после `NEUTRAL`/`TARGET` возвращаемся в `WAITING_TRIGGER` (новая итерация),
  - при `graceful_stop`: после завершения текущего раунда движок завершится,
  - при `stop()`/`close()`: форс-остановка/закрытие.

Отдельно для `WAITING_TRIGGER` в single-side loop:
- если `entrance_timeout_ms` истёк и входа не было, движок либо:
  - перезапускает ожидание (новая итерация), либо
  - если был `graceful_stop` — завершает работу.

---

## 5) Остановка: graceful vs force

В этом файле мы обрабатываем `KeyboardInterrupt` так:
- первый Ctrl+C → `hedge.graceful_stop("keyboard_interrupt")`
  и затем ещё раз вызываем `run_endless()`, чтобы дать текущему раунду корректно завершиться.

`stop()` остаётся “жёсткой кнопкой” (force): он гасит потоки и чистит ресурсы.
`Position.stop()` внутри может сделать reduce-only MARKET, если осталась экспозиция — это намеренная защита.

---

## 6) Как запускать `live_app.py` из других папок (PYTHONPATH)

Этот скрипт импортирует пакет `live`:

- `from live.exchanges...`
- `from live.logic...`

Чтобы Python находил пакет `live`, в `PYTHONPATH` должна быть добавлена **папка-родитель**,
внутри которой лежит директория `live/`.

Пример (универсальный шаблон, как вы просили):

```bash
export PYTHONPATH="<PATH_WHERE_LIVE_MODULE_IS>"
python /path/to/live_app.py --symbol=... --mode=short_only --quote=... --trigger-long-pct=... --trigger-short-pct=... --target-long-pct=... --target-short-pct=...
```

Важно:
- `<PATH_WHERE_LIVE_MODULE_IS>` — это путь до директории, где лежит папка `live/`
  (т.е. `<PATH_WHERE_LIVE_MODULE_IS>/live/__init__.py` должен существовать).
- Если `PYTHONPATH` не выставлен, `import live` упадёт при запуске из “чужой” папки.

Альтернатива (когда вы стоите в корне, где видно `live/`):

```bash
PYTHONPATH="." python live_app.py --symbol=... --mode=short_only --quote=... --trigger-long-pct=... --trigger-short-pct=... --target-long-pct=... --target-short-pct=...
```
"""


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--symbol", required=True)
    p.add_argument("--mode", choices=["long_only", "short_only"], required=True)

    p.add_argument("--quote", type=float, required=True)

    p.add_argument("--trigger-long-pct", type=float, required=True)
    p.add_argument("--trigger-short-pct", type=float, required=True)

    p.add_argument("--target-long-pct", type=float, required=True)
    p.add_argument("--target-short-pct", type=float, required=True)

    p.add_argument("--entrance-timeout-ms", type=int, default=60_000)
    p.add_argument("--sleep-s", type=float, default=0.05)

    return p.parse_args()


def _pct_to_x10000(pct: float, name: str) -> int:
    if pct is None:
        raise RuntimeError(f"_pct_to_x10000: {name} is None")
    if not isinstance(pct, float):
        raise RuntimeError(f"_pct_to_x10000: {name} is not float: {type(pct)}")
    if float(pct) <= 0:
        raise RuntimeError(f"_pct_to_x10000: {name} must be > 0, got: {pct}")

    v = int(round(float(pct) * 10000.0))
    if v <= 0:
        raise RuntimeError(f"_pct_to_x10000: {name} rounded to non-positive: {v} from {pct}")
    if v > 1_000_000:
        raise RuntimeError(f"_pct_to_x10000: {name} too large: {v} from {pct}")

    return int(v)


def main():
    args = parse_args()

    if "BINANCE_KEY" not in os.environ:
        raise RuntimeError("BINANCE_KEY not found in environment variables")
    if "BINANCE_SECRET" not in os.environ:
        raise RuntimeError("BINANCE_SECRET not found in environment variables")

    key = os.environ["BINANCE_KEY"]
    secret = os.environ["BINANCE_SECRET"]

    quote = float(args.quote)
    if quote <= 0:
        raise RuntimeError(f"Bad quote: {quote}")

    if args.mode == "long_only":
        hedge_mode = HedgeMode.LONG_ONLY
        expected_leg = HedgeLeg.LONG
    elif args.mode == "short_only":
        hedge_mode = HedgeMode.SHORT_ONLY
        expected_leg = HedgeLeg.SHORT
    else:
        raise RuntimeError(f"Bad mode: {args.mode}")

    tr_long = _pct_to_x10000(float(args.trigger_long_pct), "trigger_long_pct")
    tr_short = _pct_to_x10000(float(args.trigger_short_pct), "trigger_short_pct")

    tg_long = _pct_to_x10000(float(args.target_long_pct), "target_long_pct")
    tg_short = _pct_to_x10000(float(args.target_short_pct), "target_short_pct")

    if int(args.entrance_timeout_ms) <= 0:
        raise RuntimeError(f"Bad entrance_timeout_ms: {args.entrance_timeout_ms}")

    sleep_s = float(args.sleep_s)
    if sleep_s <= 0:
        raise RuntimeError(f"Bad sleep_s: {sleep_s}")

    ExchangeClass = get_exchange_class("Binance")
    RealtimeClass = get_realtime_class("Binance")

    # Position (and HedgeEngine) require one-way mode: exchange hedge_mode must be False
    exchange = ExchangeClass(key=key, secret=secret, hedge_mode=False, is_realtime=True)

    rt = RealtimeClass()
    rt.start()

    exchange.wait_for_connect(timeout=60)
    rt.wait_for_connect(timeout=60)

    rules = exchange.get_rules()
    if args.symbol not in rules:
        raise RuntimeError(f"Rule not found for symbol: {args.symbol}")
    rule = rules[args.symbol]

    def on_volume(req: HedgeVolumeRequest) -> int:
        if req is None:
            raise RuntimeError("on_volume: req is None")
        if not isinstance(req, HedgeVolumeRequest):
            raise RuntimeError(f"on_volume: req is not HedgeVolumeRequest: {type(req)}")
        if req.symbol != args.symbol:
            raise RuntimeError(f"on_volume: symbol mismatch: req.symbol={req.symbol} args.symbol={args.symbol}")

        if req.leg != expected_leg:
            raise RuntimeError(f"on_volume: bad leg for single-side: got={req.leg} expected={expected_leg}")

        price_units = int(req.price_units)
        if price_units <= 0:
            raise RuntimeError(f"on_volume: bad price_units: {price_units}")

        price_step_float = float(rule.price_step)
        if price_step_float <= 0:
            raise RuntimeError(f"on_volume: bad price_step: {rule.price_step}")

        lot_step_float = float(rule.lot_step)
        if lot_step_float <= 0:
            raise RuntimeError(f"on_volume: bad lot_step: {rule.lot_step}")

        price_float = float(price_units) * float(price_step_float)
        if price_float <= 0:
            raise RuntimeError(f"on_volume: bad price_float: {price_float}")

        base_volume_float = float(quote) / float(price_float)
        base_units = int(base_volume_float / float(lot_step_float))
        if base_units <= 0:
            raise RuntimeError(
                f"on_volume: bad base_units: {base_units} from quote={quote} price_float={price_float}"
            )

        return int(base_units)

    cfg = HedgeConfig(
        hedge_id=str(int(time.time() * 1000)),
        symbol=args.symbol,
        hedge_mode=hedge_mode,
        trigger_offset_pct_x10000=HedgeOffsetsPctX10000(long=int(tr_long), short=int(tr_short)),
        target_offset_pct_x10000=HedgeOffsetsPctX10000(long=int(tg_long), short=int(tg_short)),
        execution_params=HedgeExecutionParams(
            tick_ms=5,
            gtx_cooldown_ms=5,
            entrance_timeout_ms=int(args.entrance_timeout_ms),
        ),
    )

    hedge = HedgeEngine(config=cfg, exchange=exchange, realtime=rt, on_volume=on_volume)
    
    def on_loop_iteration_started(iteration: int, reason: str, time_ms: int) -> None:
        if iteration is None:
            raise RuntimeError("on_loop_iteration_started: iteration is None")
        if not isinstance(iteration, int):
            raise RuntimeError(f"on_loop_iteration_started: iteration is not int: {type(iteration)}")
        if int(iteration) <= 0:
            raise RuntimeError(f"on_loop_iteration_started: bad iteration: {iteration}")
        
        if reason is None:
            raise RuntimeError("on_loop_iteration_started: reason is None")
        if not isinstance(reason, str):
            raise RuntimeError(f"on_loop_iteration_started: reason is not str: {type(reason)}")
        
        if time_ms is None:
            raise RuntimeError("on_loop_iteration_started: time_ms is None")
        if not isinstance(time_ms, int):
            raise RuntimeError(f"on_loop_iteration_started: time_ms is not int: {type(time_ms)}")
        if int(time_ms) <= 0:
            raise RuntimeError(f"on_loop_iteration_started: bad time_ms: {time_ms}")
        
        # Big bold divider for new loop iteration
        print()
        print("\033[1m" + ("=" * 120) + "\033[0m")
        print("\033[1m" + f" LOOP ITERATION #{int(iteration)}  reason={reason}  time_ms={int(time_ms)} " + "\033[0m")
        print("\033[1m" + ("=" * 120) + "\033[0m")
        print()
    
    hedge.set_on_loop_iteration_started(on_loop_iteration_started)

    main_exc = None

    try:
        try:
            hedge.run_endless(sleep_s=float(sleep_s))
        except KeyboardInterrupt:
            # First Ctrl+C: request graceful stop and let the current round finish.
            if bool(hedge.started):
                hedge.graceful_stop("keyboard_interrupt")
                hedge.run_endless(sleep_s=float(sleep_s))

    except Exception:
        main_exc = sys.exc_info()

    finally:
        cleanup_errors = []

        try:
            if bool(hedge.started):
                hedge.stop()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            rt.stop()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            exchange.stop()
        except Exception as e:
            cleanup_errors.append(e)

        if main_exc is not None and len(cleanup_errors) > 0:
            _t, exc, _tb = main_exc
            raise RuntimeError(
                f"live_app failed and cleanup failed too: cleanup_errors={cleanup_errors}"
            ) from exc

        if main_exc is not None:
            _t, exc, tb = main_exc
            raise exc.with_traceback(tb)

        if len(cleanup_errors) > 0:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise RuntimeError(f"Cleanup failed: cleanup_errors={cleanup_errors}")


if __name__ == "__main__":
    main()