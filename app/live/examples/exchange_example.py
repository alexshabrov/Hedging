import argparse, time, json, os
import sys

from ..exchanges.exchange_factory import get_exchange_class
from ..exchanges.exchange_models import OrderSide, OrderType, PositionSide
from ..exchanges.fills_aggregator import FillsAggregator, AggregatedFill
from ..exchanges.fills_router import FillsRouter


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    # Params
    p.add_argument('--symbol', required=True)
    p.add_argument('--side', choices=['BUY', 'SELL'], required=True)
    p.add_argument('--position-side', choices=['LONG', 'SHORT', 'BOTH'], default='LONG')
    p.add_argument('--quote', type=float, required=True)

    return p.parse_args()


### Main ###
def main():
    # Params
    args = parse_args()

    # API
    assert 'BINANCE_KEY' in os.environ, 'BINANCE_KEY not found in environment variables'
    assert 'BINANCE_SECRET' in os.environ, 'BINANCE_SECRET not found in environment variables'

    key = os.environ['BINANCE_KEY']
    secret = os.environ['BINANCE_SECRET']

    # Side
    if args.side == 'BUY':
        order_side = OrderSide.BUY
    elif args.side == 'SELL':
        order_side = OrderSide.SELL
    else:
        raise RuntimeError(f'Bad side: {args.side}')

    # Position side
    if args.position_side == 'LONG':
        position_side = PositionSide.LONG
    elif args.position_side == 'SHORT':
        position_side = PositionSide.SHORT
    elif args.position_side == 'BOTH':
        position_side = PositionSide.BOTH
    else:
        raise RuntimeError(f'Bad position side: {args.position_side}')

    # Hedge mode (strict rule: BOTH -> False; otherwise True)
    hedge_mode = args.position_side != 'BOTH'

    # Position side string (used to locate the position in /fapi/v3/account)
    if hedge_mode:
        position_side_str = args.position_side
    else:
        position_side_str = 'BOTH'

    # Applied fills
    fills = []

    # Callbacks
    def on_agg_fill(agg_fill: AggregatedFill):
        fills.append(agg_fill)
        print(json.dumps(agg_fill.model_dump(), ensure_ascii=False))

    def on_update():
        return

    # Exchange
    ExchangeClass = get_exchange_class('Binance')
    exchange = ExchangeClass(key=key, secret=secret, hedge_mode=hedge_mode, is_realtime=True)

    fills_agg = FillsAggregator(handler=on_agg_fill, tick_ms=5)
    fills_rt = FillsRouter()

    fills_rt.set_push(fills_agg.add)

    fills_agg.start()
    fills_rt.start()

    exchange.set_fill_callback(fills_rt.add)
    exchange.set_update_callback(on_update)

    # WS (must be started via is_realtime=True)
    exchange.wait_for_connect(timeout=60)

    # Rules
    rules = exchange.get_rules()
    if args.symbol not in rules:
        raise RuntimeError(f'Rule not found for symbol: {args.symbol}')

    rule = rules[args.symbol]
    
    # Price (units)
    price_units = exchange.get_price(args.symbol)
    if price_units <= 0:
        raise RuntimeError(f'Bad price_units: {price_units}')

    # Volume (quote -> base units) using lot_step (strict units)
    quote = float(args.quote)
    if quote <= 0:
        raise RuntimeError(f'Bad quote: {quote}')

    price_step_float = float(rule.price_step)
    if price_step_float <= 0:
        raise RuntimeError(f'Bad price_step: {rule.price_step}')

    price_float = float(price_units) * price_step_float
    if price_float <= 0:
        raise RuntimeError(f'Bad price_float: {price_float}')

    lot_step_float = float(rule.lot_step)
    if lot_step_float <= 0:
        raise RuntimeError(f'Bad lot_step: {rule.lot_step}')

    base_volume_float = quote / price_float
    base_volume_units = int(base_volume_float / lot_step_float)
    
    if base_volume_units <= 0:
        raise RuntimeError(f'Bad base_volume_units: {base_volume_units}')

    order_id = None
    close_order_id = None

    main_exc = None

    try:
        # Open market order
        order_id, can_repeat, order_error, err = exchange.create_order(
            position_side=position_side,
            order_side=order_side,
            order_type=OrderType.MARKET,
            symbol=args.symbol,
            base_volume=base_volume_units,
            price=None,
            reduce_only=False,
        )

        # Checks
        if err is not None:
            raise err

        if not order_id:
            raise RuntimeError(f'Empty order_id from create_order: can_repeat={can_repeat} order_error={order_error}')

        fills_rt.attach_order(order_id)

        print(f'Opened market order. order_id={order_id} can_repeat={can_repeat} order_error={order_error}')

        # Wait for fills
        time.sleep(10)

        fills_agg.check()
        fills_rt.check()

        # Account (position snapshot)
        account = exchange._get_account()

        # Position volume
        position_amt = None

        for item in account['positions']:
            if item['symbol'] != args.symbol:
                continue

            if item['positionSide'] != position_side_str:
                continue

            position_amt = float(item['positionAmt'])
            break

        # Checks
        if position_amt is None:
            raise RuntimeError(f'Position not found for {args.symbol} {position_side_str}')

        if position_amt == 0:
            raise RuntimeError(f'Position is empty for {args.symbol} {position_side_str}')

        # Close side
        if position_amt > 0:
            close_side = OrderSide.SELL
        else:
            close_side = OrderSide.BUY

        # Close volume
        lot_step_float = float(rule.lot_step)
        if lot_step_float <= 0:
            raise RuntimeError(f'Bad lot_step: {rule.lot_step}')

        close_volume_units = int(abs(position_amt) / lot_step_float)
        if close_volume_units <= 0:
            raise RuntimeError(f'Bad close_volume_units: {close_volume_units}')

        # Close market order
        close_order_id, can_repeat, order_error, err = exchange.create_order(
            position_side=position_side,
            order_side=close_side,
            order_type=OrderType.MARKET,
            symbol=args.symbol,
            base_volume=close_volume_units,
            price=None,
            reduce_only=True,
        )

        # Checks
        if err is not None:
            raise err

        if not close_order_id:
            raise RuntimeError(f'Empty close_order_id from create_order: can_repeat={can_repeat} order_error={order_error}')

        fills_rt.attach_order(close_order_id)

        print(f'Closed position. order_id={close_order_id} can_repeat={can_repeat} order_error={order_error}')

        # Wait for closing fills
        time.sleep(10)

        fills_agg.check()
        fills_rt.check()

    except Exception:
        main_exc = sys.exc_info()
    finally:
        cleanup_errors = []

        try:
            fills_rt.flush_all()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            exchange.stop()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            fills_agg.stop_and_flush()
        except Exception as e:
            cleanup_errors.append(e)

        try:
            fills_rt.stop()
        except Exception as e:
            cleanup_errors.append(e)

        if main_exc is not None and len(cleanup_errors) > 0:
            _t, exc, _tb = main_exc
            raise RuntimeError(f'Exchange example failed and cleanup failed too: cleanup_errors={cleanup_errors}') from exc

        if main_exc is not None:
            _t, exc, tb = main_exc
            raise exc.with_traceback(tb)

        if len(cleanup_errors) > 0:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]

            raise RuntimeError(f'Cleanup failed: cleanup_errors={cleanup_errors}')


if __name__ == '__main__':
    main()
