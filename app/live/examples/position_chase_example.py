import argparse, time, os, json
import sys

from ..exchanges.exchange_factory import get_exchange_class, get_realtime_class
from ..exchanges.exchange_models import OrderSide, PositionSide
from ..exchanges.exchange_models import ChaseCommand, PositionCommandType, ChaseResponse, PositionEvent
from ..exchanges.position import Position


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--symbol', required=True)
    p.add_argument('--side', choices=['BUY', 'SELL'], required=True)
    p.add_argument('--quote', type=float, required=True)

    return p.parse_args()

def _reverse_side(side):
    if side == OrderSide.BUY:
        return OrderSide.SELL
    
    if side == OrderSide.SELL:
        return OrderSide.BUY
    
    raise RuntimeError(f'_reverse_side: bad side: {side}')


### Main ###
def main():
    args = parse_args()

    assert 'BINANCE_KEY' in os.environ, 'BINANCE_KEY not found in environment variables'
    assert 'BINANCE_SECRET' in os.environ, 'BINANCE_SECRET not found in environment variables'

    key = os.environ['BINANCE_KEY']
    secret = os.environ['BINANCE_SECRET']

    if args.side == 'BUY':
        side = OrderSide.BUY
    elif args.side == 'SELL':
        side = OrderSide.SELL
    else:
        raise RuntimeError(f'Bad side: {args.side}')

    # Position-side is forced to BOTH (one-way mode)
    position_side = PositionSide.BOTH

    quote = float(args.quote)
    if quote <= 0:
        raise RuntimeError(f'Bad quote: {quote}')

    hedge_mode = False

    ExchangeClass = get_exchange_class('Binance')
    RealtimeClass = get_realtime_class('Binance')

    exchange = ExchangeClass(key=key, secret=secret, hedge_mode=hedge_mode, is_realtime=True)

    rt = RealtimeClass()
    rt.start()

    exchange.wait_for_connect(timeout=60)
    rt.wait_for_connect(timeout=60)

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
    base_units = int(base_volume_float / lot_step_float)

    if base_units <= 0:
        raise RuntimeError(f'Bad base_units: {base_units}')

    pos = Position(symbol=args.symbol, exchange=exchange, realtime=rt, uid='pos_chase', tick_ms=5, gtx_cooldown_ms=5)

    started_cmd_id = None
    started_side = None
    started_base_units = None
    
    back_cmd_id = None
    back_side = None
    back_base_units = None
    
    t0_ms = int(time.time() * 1000)
    
    started_cmd_id = str(t0_ms)
    started_side = side
    started_base_units = int(base_units)
    
    cmd = ChaseCommand(
        cmd=PositionCommandType.CHASE,
        symbol=args.symbol,
        position_side=position_side,
        side=started_side,
        base_volume=int(started_base_units),
        cmd_id=started_cmd_id,
        time_ms=int(t0_ms),
    )
    
    pos.add_command(cmd)

    main_exc = None

    try:
        for it in pos.loop():
            if isinstance(it, PositionEvent):
                print(json.dumps(it.model_dump(), ensure_ascii=False))
                continue

            if isinstance(it, ChaseResponse):
                print(json.dumps(it.model_dump(), ensure_ascii=False))
                
                if not bool(it.ok):
                    raise RuntimeError(f'Chase failed: cmd_id={it.cmd_id} ok={it.ok} error={it.error}')
                
                if started_cmd_id is None:
                    raise RuntimeError('Position chase example: started_cmd_id is None')
                
                if back_cmd_id is None:
                    if it.cmd_id != started_cmd_id:
                        raise RuntimeError(f'Unexpected first chase response: cmd_id={it.cmd_id}, expected={started_cmd_id}')
                    
                    filled = int(it.filled_base_volume)
                    if filled <= 0:
                        raise RuntimeError(f'Bad filled_base_volume from first chase: {filled}')
                    
                    back_cmd_id = str(int(t0_ms) + 1)
                    back_side = _reverse_side(started_side)
                    back_base_units = int(filled)
                    
                    cmd2 = ChaseCommand(
                        cmd=PositionCommandType.CHASE,
                        symbol=args.symbol,
                        position_side=position_side,
                        side=back_side,
                        base_volume=int(back_base_units),
                        cmd_id=back_cmd_id,
                        time_ms=int(time.time() * 1000),
                    )
                    
                    pos.add_command(cmd2)
                    continue
                
                if it.cmd_id != back_cmd_id:
                    raise RuntimeError(f'Unexpected chase response: cmd_id={it.cmd_id}, expected back_cmd_id={back_cmd_id}')
                
                return

            raise RuntimeError(f'Unexpected out item: {type(it)}')

    except Exception:
        main_exc = sys.exc_info()

    finally:
        cleanup_errors = []
        
        try:
            if bool(pos.started):
                pos.stop()
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
            raise RuntimeError(f'Position chase example failed and cleanup failed too: cleanup_errors={cleanup_errors}') from exc

        if main_exc is not None:
            _t, exc, tb = main_exc
            raise exc.with_traceback(tb)

        if len(cleanup_errors) > 0:
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]

            raise RuntimeError(f'Cleanup failed: cleanup_errors={cleanup_errors}')


if __name__ == '__main__':
    main()

