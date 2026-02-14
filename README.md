# Hedging

Подробная документация по проекту из `app/`: что это за система, как устроены модули, какие форматы данных используются, как запускать сценарии и как читать статистику.

---

## 1) Что это за проект

`Hedging` — это набор инструментов для:

- оффлайн-исследований и бэктестов логики лимитного исполнения/чейза на исторических трейдах Binance UM Futures;
- live-исполнения и хеджирования на Binance (`live` пакет);
- работы с DEX (Uniswap V3 + CowSwap) в реальном времени (`dex` пакет);
- объединения CEX+DEX в единый раннер `app/backend/hedger_cli.py`, который:
  - открывает LP-позицию в Uniswap V3,
  - ведет хедж через live-движок на Binance,
  - закрывает ликвидность,
  - делает ребаланс через CowSwap,
  - сохраняет итоговую статистику в MongoDB.

---

## 2) Структура проекта

Ключевые части:

- `app/backtest` — подготовка исторических данных, индикаторы, симуляторы исполнения и массовые прогоны;
- `app/live` — runtime для live-трейдинга/хеджирования (bookTicker, maker chase, state machine);
- `app/dex` — runtime для DEX: realtime swap events, работа с Uniswap V3 контрактами, CowSwap свопы;
- `app/modules/hedger_class.py` — оркестратор полного цикла CEX+DEX;
- `app/models/hedger_models.py` — итоговые модели статистики раннера;
- `app/backend/hedger_cli.py` — CLI-энтрипойнт полного хедж-сценария;
- `app/uniswapv3.ipynb` — исследовательский ноутбук по Uniswap V3.

---

## 3) Быстрый старт

### Вариант A: через Docker

Из корня репозитория:

```bash
docker compose build
docker compose up -d
docker compose ps
docker exec -it <container_name> bash
cd /hedging/app
```

Данные монтируются в `/_data` (см. `docker-compose.yaml`).

Для web-доступа добавлены `nginx` + `certbot`:

- `nginx` слушает `80/443` и проксирует только frontend в `hedging:8081`.
- `certbot` хранит сертификаты в `/_certbot/conf` и webroot challenge в `/_certbot/www`.

Первичная выдача сертификата и включение HTTPS:

```bash
chmod +x add_domain.sh renew_all.sh
./add_domain.sh hedging.example.com
```

Периодическое обновление сертификатов вручную:

```bash
./renew_all.sh
```

### Вариант B: локальный Python

Минимум: Python 3.10+ и `pip install -r requirements.txt`.

Для `live` и `dex` лучше ставить как editable-пакеты:

```bash
python -m pip install -e app/live
python -m pip install -e app/dex
```

---

## 4) Модули и что делает каждый

## `app/backtest`

Назначение: оффлайн пайплайн от сырых Binance архивов до статистики по стратегиям.

### Скрипты верхнего уровня

- `01_prepare_data.py`
  - скачивает архивы `*-trades-YYYY-MM-DD.zip`;
  - находит минимальные шаги цены/объема по дням;
  - строит split map (сегменты смены шага);
  - конвертирует CSV в квантизованные int64-массивы;
  - сохраняет кеш в pickle.
- `01_download_funding.py`
  - качает историю funding rate через Binance API;
  - сохраняет CSV `timestamp_ms,funding_rate`.
- `07_mass_test.py`
  - multiprocessing раннер массовых тестов;
  - для каждого теста рандомизирует параметры из run-конфига;
  - считает индикаторы (trend/volatility), прогоняет `run_logic`;
  - пишет результаты по воркерам в pickle.
- `live_example.py`
  - пример запуска `live.HedgeEngine` из директории backtest (legacy-совместимость).
- `live_app.py`
  - CLI для одностороннего endless-режима (`long_only`/`short_only`) поверх `HedgeEngine`.

### `backtest/lib` (ядро оффлайн-логики)

- `helders.py`
  - CLI-парсинг (`--symbols`, `--from-date`, `--confs`, `--n-tests`, ...);
  - конструкторы путей кеша/результатов;
  - загрузка/валидация run-конфигов;
  - рандомизация диапазонов и списков параметров.
- `data_load.py`
  - сбор списка недостающих архивов;
  - многопоточное скачивание архивов.
- `steps_splitter.py`
  - определение минимальных шагов цены/объема по дням;
  - построение split map по смене шагов.
- `data_processing.py`
  - CSV/ZIP -> `np.ndarray[int64]` с колонками `[time, price, volume, is_buyer_maker]`;
  - квантование по `price_step`/`volume_step`;
  - кеширование `*.npy` и `*.pkl`.
- `limit_exec.py`
  - симуляция исполнения одиночной post-only лимитки.
- `chase.py`
  - симуляция цепочки лимиток (чейз) с network delay и отступом в шагах.
- `indicators.py`
  - индикаторы тренда/волатильности по time-aligned окнам.
- `logic.py`
  - высокоуровневая стратегия открытия/закрытия через thresholds/targets;
  - сбор трасс, групп ордеров, статистики и PnL.

### Ноутбуки

- `02_limit_exec.ipynb`, `03_chase.ipynb`, `04_indicators.ipynb`, `05_logic_proto.ipynb`, `06_logic.ipynb` — исследование отдельных частей логики;
- `08_mass_test.ipynb` — анализ результатов массовых прогонов;
- `09_*`, `10_*`, `11_*`, `12_*`, `13_*` — отчетные/сравнительные исследования.

---

## `app/live`

Назначение: live runtime для CEX-хеджирования.

### Пакеты

- `live/logic`
  - `models.py` — строгие модели хеджа (`HedgeConfig`, `HedgeSnapshot`, метрики, статусы);
  - `hedging.py` — `HedgeEngine`: state machine, обработка realtime, запуск/контроль `Position`.
- `live/exchanges`
  - `exchange_interface.py` — контракт биржи/realtime;
  - `exchange_models.py` — модели правил, ордеров, филлов, bookTicker, событий Position loop;
  - `exchange_factory.py` — выбор backend (`Binance`);
  - `position.py` — maker-only chase executor (GTX лимитки + обработка филлов);
  - `fills_router.py` — дедуп/буфер филлов;
  - `fills_aggregator.py` — агрегация пачек филлов;
  - `binance/binance_trading.py` — торговый коннектор Binance Futures;
  - `binance/binance_realtime.py` — realtime коннектор bookTicker.
- `live/lib`
  - `strict_model.py`, `ws_client.py`, `logger.py`.
- `live/examples`
  - `realtime_example.py`, `exchange_example.py`, `position_chase_example.py`, `hedge_example.py`.

---

## `app/dex`

Назначение: DEX runtime (Uniswap realtime + onchain liquidity + CowSwap swaps).

### Пакеты

- `dex/realtime`
  - `swaps_realtime.py` — подписка `eth_subscribe logs` на Swap-события пула.
- `dex/contract`
  - `contract_wrapper.py` — обертка над Web3/Uniswap V3:
    - чтение текущей цены,
    - `add_liquidity_traditional`,
    - `get_position_state`,
    - `decrease_liquidity`,
    - `collect_fees`,
    - конвертация swap-событий в price-события;
  - `pool_calc.py` — расчет разбиения капитала по токенам;
  - `params.py` — сетевые параметры (стейблы, NPM-адреса).
- `dex/swappers`
  - `swapper_interface.py` — интерфейс своппера;
  - `swapper_factory.py` — фабрика;
  - `cow_swap/cow_swap_class.py` — синхронный своп через CowSwap.
- `dex/models`
  - `realtime_models.py` — `SwapEvent`, `PriceEvent`, `SwapsRealtimeConfig`;
  - `contract_models.py` — `MintResult`, `DecreaseLiquidityResult`, `CollectFeesResult`, `PositionState`;
  - `swapper_models.py` — конфиги/запросы/результаты свопов.
- `dex/examples`
  - `swaps_realtime_example.py`, `liquidity_example.py`, `cow_swap_example.py`.

---

## `app/modules` и `app/models`

- `modules/hedger_class.py`
  - главный оркестратор end-to-end;
  - валидирует конфиг;
  - поднимает `ContractWrapper` + `HedgeEngine`;
  - запускает mint LP, отслеживает hedge snapshot;
  - в cleanup: decrease liquidity + collect fees + rebalance через CowSwap;
  - сохраняет итоговую статистику в Mongo.
- `models/hedger_models.py`
  - схемы входного конфига и итоговой статистики `HedgerStats`.

---

## `app/backend/hedger_cli.py`

CLI точка входа full-run.

Что делает:

1. Парсит аргументы (`symbol`, `rpc-url`, `network`, `pool-address`, границы цен, бюджет, Mongo, таймауты).
2. Читает секреты из env:
   - `BINANCE_KEY`
   - `BINANCE_SECRET`
   - `PRIVATE_KEY`
   - `WALLET_ADDRESS` (опционально)
3. Создает `HedgerConfig`.
4. Запускает `Hedger(...).run()`.
5. Печатает `HedgerStats` как JSON.

---

## 5) Форматы входа/выхода

## 5.1 Backtest data формат

Внутренний массив трейдов в `backtest/lib`:

- тип: `np.ndarray[int64]`, shape `(N, 4)`
- колонки:
  - `0`: `time_ms`
  - `1`: `price_units` (квантизованная цена)
  - `2`: `volume_units` (квантизованный объем)
  - `3`: `is_buyer_maker` (`0`/`1`)

Преобразование в реальные величины:

- `price_real = price_units * price_step`
- `volume_real = volume_units * volume_step`

## 5.2 Run config (`backtest/run_confs/*.json`)

Обязательные скаляры:

- `max_time_ms`, `network_delay_ms`, `distance_steps`
- `threshold_as_perc`, `d_top_threshold`, `d_btm_threshold`
- `tf_ms`, `period`, `only_side`, `entrance_timeout_ms`

Рандомизируемые поля:

- `test_volume_usd`: `{min,max,step}` или список;
- `d_top_target_perc`, `d_btm_target_perc`: `{min,max,step}` или список.

Опционально:

- `targets_sync: bool` — синхронизировать top/btm target.

Пример:

```json
{
  "test_volume_usd": [10000, 30000, 50000],
  "max_time_ms": 10000,
  "network_delay_ms": 110,
  "distance_steps": 1,
  "threshold_as_perc": true,
  "d_top_threshold": 0.1,
  "d_btm_threshold": 0.1,
  "d_top_target_perc": [1, 2, 3],
  "d_btm_target_perc": [1, 2, 3],
  "targets_sync": true,
  "tf_ms": 300000,
  "period": 30,
  "only_side": [-1, 1],
  "entrance_timeout_ms": 300000
}
```

## 5.3 Backtest mass-test output

Файл: `<base_dir>/<SYMBOL>/results/<conf_name>/<SYMBOL>.mass_test.w{worker}.pkl`

Каждый элемент списка:

```python
{
  "t": float,                    # trend indicator
  "v": float,                    # volatility indicator
  "start_index": int,
  "start_time_ms": int,
  "run_conf": {...},             # конкретные рандомизированные параметры теста
  "stat": {...},                 # get_logic_stat()
  "neutral_excursions": [float]  # для neutral close
}
```

## 5.4 Модель `stat` (из `get_logic_stat`)

```python
{
  "stopped_at_index": int,
  "err": int,
  "lines": (top_target, btm_target, top_threshold, btm_threshold),
  "chases": [ ... per-chase dict ... ],
  "agg": {
    "n_chases": int,
    "success_rate": float,
    "avg_slippage": float,
    "avg_execution_time": float,
    "avg_po_violations_pct": float,
    "total_trades_executed": int,
    "pnl": float,
    "full_execution_time": float
  }
}
```

### Пример per-chase статистики

```python
{
  "index": 123456,
  "side": 1,
  "intended_price": 102345.0,
  "price_real": 2012.34,
  "base_volume": 4.97,
  "test_volume_units": 497,
  "stopped_at_index": 123999,
  "trades_executed": 543,
  "trace_size": 31,
  "volume_left": 0,
  "volume_left_pct": 0.0,
  "po_violations_pct": 12.5,
  "execution_time": 5.4,
  "exec_price": 102350.2,
  "slippage": -0.01
}
```

## 5.5 Live snapshot формат (`HedgeSnapshot`)

Ключевые поля:

- `status`: `initialized|waiting_trigger|executing|active|closing|closed|failed`
- `base_price_units`, `lines`
- `opened_leg`, `opened_base_units`
- `stats`: счетчики событий/chase
- `metrics`: PnL, turnover, список `chases`, `neutral_excursions_pct_x10000`

Пример:

```json
{
  "hedge_id": "1739362800000",
  "symbol": "SOLUSDC",
  "status": "active",
  "started_ms": 1739362800000,
  "updated_ms": 1739362812345,
  "mutation_counter": 42,
  "base_price_units": 201234,
  "lines": {
    "top_target_units": 201536,
    "btm_target_units": 200932,
    "top_threshold_units": 201335,
    "btm_threshold_units": 201133
  },
  "opened_leg": "long",
  "opened_base_units": 1250,
  "last_error": null,
  "stats": {
    "chases_started": 3,
    "chases_done": 2,
    "position_events": 57
  },
  "metrics": {
    "quote_balance_units": 184200,
    "realized_pnl_quote_units": 184200,
    "chases": []
  }
}
```

## 5.6 Итоговая модель full-run (`HedgerStats`)

Выход `app/backend/hedger_cli.py`:

```json
{
  "status": "finished",
  "calc": {
    "base_price": 2012.34,
    "price_lower": 1900.0,
    "price_upper": 2124.68,
    "total_quote": 1000.0,
    "cex_ratio": 0.5,
    "trigger_offset_pct_x10000": 558,
    "target_offset_pct_x10000": 558,
    "hedge_quote": 500.0
  },
  "uniswap": {
    "token_id": 1234567,
    "mint": { "ok": true, "tx_hash": "0x..." },
    "position": { "price_current": 2011.9 },
    "decrease": { "ok": true },
    "collect": { "ok": true },
    "rebalance": { "ok": true, "order": { "status": "fulfilled" } }
  },
  "live": {
    "last_snapshot": { "status": "closed" },
    "last_snapshot_json": "{...}"
  },
  "error": null
}
```

Также этот документ пишется в Mongo (`mongo_db.mongo_collection`) через `insert_one`.

---

## 6) Где что хранится

Базовые пути берутся из `app/backtest/params.py`:

- `PARAMS['base_dir']` (по умолчанию `/_data/base`)
- `PARAMS['download_threads']`
- `PARAMS['split_map_min_days']`

Для символа `<SYMBOL>`:

- `<base_dir>/<SYMBOL>/data` — сырые zip-архивы trades;
- `<base_dir>/<SYMBOL>/cache/steps` — кеш шагов;
- `<base_dir>/<SYMBOL>/cache/npy` — промежуточные квантизованные файлы;
- `<base_dir>/<SYMBOL>/cache/pickle/<SYMBOL>.pkl` — готовые блоки;
- `<base_dir>/<SYMBOL>/csv/funding.csv` — funding;
- `<base_dir>/<SYMBOL>/results/<conf_name>/*.pkl` — массовые результаты.

---

## 7) Примеры запуска

Ниже команды даны из корня проекта.

## 7.1 Подготовка данных и funding

```bash
python app/backtest/01_prepare_data.py --symbols BTCUSDT,ETHUSDT --from-date 2024-01-04
python app/backtest/01_download_funding.py --symbols BTCUSDT,ETHUSDT
```

или через env:

```bash
export SYMBOLS=BTCUSDT,ETHUSDT
export FROM_DATE=2024-01-04
python app/backtest/01_prepare_data.py
python app/backtest/01_download_funding.py
```

## 7.2 Массовые прогоны

```bash
python app/backtest/07_mass_test.py \
  --symbols BTCUSDT \
  --n-workers 6 \
  --n-tests 20000 \
  --confs list_01p_s \
  --drop-chases 1
```

## 7.3 Live examples

После `pip install -e app/live`:

```bash
export BINANCE_KEY="..."
export BINANCE_SECRET="..."

python -m live.examples.realtime_example --symbol=SOLUSDC --seconds=60
python -m live.examples.exchange_example --symbol=SOLUSDC --side=BUY --position-side=LONG --quote=50
python -m live.examples.position_chase_example --symbol=SOLUSDC --side=BUY --quote=500
python -m live.examples.hedge_example --symbol=SOLUSDC --mode=both --quote=500 --trigger-long-pct=0.05 --trigger-short-pct=0.05 --target-long-pct=0.15 --target-short-pct=0.15
```

## 7.4 DEX examples

После `pip install -e app/dex`:

```bash
python -m dex.examples.swaps_realtime_example \
  --ws-url=wss://arbitrum-mainnet.infura.io/ws/v3/<KEY> \
  --rpc-url=https://arbitrum-mainnet.infura.io/v3/<KEY> \
  --network=arbitrum \
  --pool-address=0x...
```

```bash
export PRIVATE_KEY="..."
export WALLET_ADDRESS="0x..."

python -m dex.examples.cow_swap_example \
  --rpc-url=https://arbitrum-mainnet.infura.io/v3/<KEY> \
  --network=arbitrum \
  --sell-token=0x... \
  --buy-token=0x... \
  --amount=1.0
```

```bash
python -m dex.examples.liquidity_example \
  --rpc-url=https://arbitrum-mainnet.infura.io/v3/<KEY> \
  --network=arbitrum \
  --pool-address=0x... \
  --fee-pct=0.05
```

## 7.5 Full CEX+DEX runner (`backend/hedger_cli.py`)

```bash
export BINANCE_KEY="..."
export BINANCE_SECRET="..."
export PRIVATE_KEY="..."
export WALLET_ADDRESS="0x..."

python app/backend/hedger_cli.py \
  --symbol SOLUSDC \
  --rpc-url https://arbitrum-mainnet.infura.io/v3/<KEY> \
  --network arbitrum \
  --pool-address 0x... \
  --fee-pct 0.05 \
  --price-lower 1900 \
  --price-upper 2100 \
  --total-quote 1000 \
  --cex-ratio 0.5 \
  --mongo-uri mongodb://hedging_mongo:27017 \
  --mongo-db hedging \
  --mongo-collection hedge_runs
```

## 7.6 Backend internal service (`app/backend/backend_service.py`)

```bash
export BINANCE_KEY="..."
export BINANCE_SECRET="..."
export PRIVATE_KEY="..."
export WALLET_ADDRESS="0x..."  # optional

python app/backend/backend_service.py
```

По умолчанию сервис слушает `0.0.0.0:8080`.

## 7.7 Frontend admin service (`app/frontend/app.py`)

```bash
export FRONT_SECRET_KEY="..."
export FRONT_ADMIN_PASSWORD="..."
export FRONT_BACKEND_URL="http://hedging:8080"
export RPC_KEY="..."
export MONGO_URI="mongodb://hedging_mongo:27017"
export MONGO_DB="hedging"
export MONGO_COLLECTION="hedge_runs"
export TICK_MS="5"
export GTX_COOLDOWN_MS="5"
export ENTRANCE_TIMEOUT_MS="60000"
export COWSWAP_API_TIMEOUT_SEC="10"
export COWSWAP_WAIT_TIMEOUT_SEC="300"
export COWSWAP_POLL_INTERVAL_SEC="3"

python app/frontend/app.py
```

По умолчанию сервис слушает `0.0.0.0:8081`.

---

## 8) Важные operational notes

- Все критичные компоненты работают в strict-режиме: неверный формат входа приводит к исключению, а не к молчаливому игнору.
- В live-части ядро работает в integer units; преобразование в float нужно только на границах системы.
- `Position.stop()` и `HedgeEngine.stop()` могут закрывать остаточную позицию через reduce-only MARKET (безопасный cleanup).
- В `dex/contract/params.py` полноценно заполнена сеть `arbitrum`; для других сетей адреса стейблов/NPM надо заполнить.
- Для больших `n-workers`/`n-tests` в mass test требуется очень много RAM/CPU/диска.

---

## 9) Что смотреть в коде в первую очередь

Если нужно быстро понять проект:

1. `app/modules/hedger_class.py` — полный end-to-end сценарий.
2. `app/live/logic/hedging.py` + `app/live/logic/models.py` — live state machine.
3. `app/backtest/lib/logic.py` — эталон оффлайн-логики и статистики.
4. `app/dex/contract/contract_wrapper.py` — DEX-пайплайн ликвидности и цен.
5. `app/backtest/07_mass_test.py` — массовый прогон и формат результатов.
