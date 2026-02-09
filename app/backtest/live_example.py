import argparse
import os
import sys
import time

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


def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--symbol", required=True)
    p.add_argument("--mode", choices=["both", "long_only", "short_only"], default="both")

    p.add_argument("--quote", type=float, required=True)

    p.add_argument("--trigger-long-pct", type=float, required=True)
    p.add_argument("--trigger-short-pct", type=float, required=True)

    p.add_argument("--target-long-pct", type=float, required=True)
    p.add_argument("--target-short-pct", type=float, required=True)

    p.add_argument("--entrance-timeout-ms", type=int, default=60_000)

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

    if args.mode == "both":
        hedge_mode = HedgeMode.BOTH
    elif args.mode == "long_only":
        hedge_mode = HedgeMode.LONG_ONLY
    elif args.mode == "short_only":
        hedge_mode = HedgeMode.SHORT_ONLY
    else:
        raise RuntimeError(f"Bad mode: {args.mode}")

    tr_long = _pct_to_x10000(float(args.trigger_long_pct), "trigger_long_pct")
    tr_short = _pct_to_x10000(float(args.trigger_short_pct), "trigger_short_pct")

    tg_long = _pct_to_x10000(float(args.target_long_pct), "target_long_pct")
    tg_short = _pct_to_x10000(float(args.target_short_pct), "target_short_pct")

    if int(args.entrance_timeout_ms) <= 0:
        raise RuntimeError(f"Bad entrance_timeout_ms: {args.entrance_timeout_ms}")

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

        if req.leg == HedgeLeg.LONG:
            pass
        elif req.leg == HedgeLeg.SHORT:
            pass
        else:
            raise RuntimeError(f"on_volume: bad leg: {req.leg}")

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

    main_exc = None

    try:
        hedge.start()

        last_mutation_counter = -1
        while True:
            hedge.check()

            snap = hedge.status()
            mc = int(snap.mutation_counter)

            if mc != int(last_mutation_counter):
                last_mutation_counter = int(mc)
                raw = hedge.status_json()
                print(raw.decode("utf-8"))

            if snap.status.value == "closed" or snap.status.value == "failed":
                return

            time.sleep(0.05)

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
                f"live_example failed and cleanup failed too: cleanup_errors={cleanup_errors}"
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

