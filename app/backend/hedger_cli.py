import argparse, os, sys

from backend.modules.hedger_class import Hedger
from backend.modules.hedger_helper import calc_hedger_pnl_stats
from backend.models.hedger_models import HedgerConfig, CexTriggerMode


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()
    
    p.add_argument('--symbol', required=True)
    
    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--pool-address', required=True)
    p.add_argument('--fee-pct', type=float, required=True)
    
    p.add_argument('--price-lower', type=float)
    p.add_argument('--price-upper', type=float)
    
    p.add_argument('--price-lower-pct', type=float)
    p.add_argument('--price-upper-pct', type=float)
    
    p.add_argument('--total-quote', type=float, required=True)
    p.add_argument('--cex-ratio', type=float, default=0.255)
    
    p.add_argument('--trigger-mode', choices=['pct', 'units'], required=True)
    p.add_argument('--trigger-pct', type=float, default=0.0)
    p.add_argument('--trigger-units', type=int, default=0)
    p.add_argument('--loop', action='store_true')
    
    p.add_argument('--mongo-uri', type=str, default='mongodb://hedging_mongo:27017')
    p.add_argument('--mongo-db', type=str, default='hedging')
    p.add_argument('--mongo-collection', type=str, default='hedge_runs')
    
    p.add_argument('--tick-ms', type=int, default=5)
    p.add_argument('--gtx-cooldown-ms', type=int, default=5)
    p.add_argument('--entrance-timeout-ms', type=int, default=60_000)
    
    p.add_argument('--cowswap-api-timeout-sec', type=int, default=10)
    p.add_argument('--cowswap-wait-timeout-sec', type=int, default=300)
    p.add_argument('--cowswap-poll-interval-sec', type=int, default=3)
    
    return p.parse_args()


### Main ###
def main():
    args = parse_args()
    
    if str(args.trigger_mode) == CexTriggerMode.PCT.value:
        trigger_mode = CexTriggerMode.PCT
    elif str(args.trigger_mode) == CexTriggerMode.UNITS.value:
        trigger_mode = CexTriggerMode.UNITS
    else:
        raise RuntimeError(f'Unsupported trigger mode: {args.trigger_mode}')
    
    if 'BINANCE_KEY' not in os.environ:
        raise RuntimeError('BINANCE_KEY not found in environment variables')
    if 'BINANCE_SECRET' not in os.environ:
        raise RuntimeError('BINANCE_SECRET not found in environment variables')
    if 'PRIVATE_KEY' not in os.environ:
        raise RuntimeError('PRIVATE_KEY not found in environment variables')
    
    binance_key = os.environ['BINANCE_KEY']
    binance_secret = os.environ['BINANCE_SECRET']
    private_key = os.environ['PRIVATE_KEY']
    
    wallet_address = None
    if 'WALLET_ADDRESS' in os.environ:
        wallet_address = os.environ['WALLET_ADDRESS']
    
    cfg = HedgerConfig(
        symbol=str(args.symbol),
        rpc_url=str(args.rpc_url),
        network=str(args.network),
        pool_address=str(args.pool_address),
        fee_pct=float(args.fee_pct),
        price_lower=None if args.price_lower is None else float(args.price_lower),
        price_upper=None if args.price_upper is None else float(args.price_upper),
        price_lower_pct=None if args.price_lower_pct is None else float(args.price_lower_pct),
        price_upper_pct=None if args.price_upper_pct is None else float(args.price_upper_pct),
        total_quote=float(args.total_quote),
        cex_ratio=float(args.cex_ratio),
        trigger_mode=trigger_mode,
        trigger_pct=float(args.trigger_pct),
        trigger_units=int(args.trigger_units),
        mongo_uri=str(args.mongo_uri),
        mongo_db=str(args.mongo_db),
        mongo_collection=str(args.mongo_collection),
        tick_ms=int(args.tick_ms),
        gtx_cooldown_ms=int(args.gtx_cooldown_ms),
        entrance_timeout_ms=int(args.entrance_timeout_ms),
        cowswap_api_timeout_sec=int(args.cowswap_api_timeout_sec),
        cowswap_wait_timeout_sec=int(args.cowswap_wait_timeout_sec),
        cowswap_poll_interval_sec=int(args.cowswap_poll_interval_sec),
    )
    
    hedger = Hedger(
        config=cfg,
        binance_key=str(binance_key),
        binance_secret=str(binance_secret),
        private_key=str(private_key),
        wallet_address=str(wallet_address) if wallet_address is not None else None,
    )
    
    try:
        n_iter = 0
        while True:
            n_iter += 1
            run_exc = None
            stats = None
            
            try:
                stats = hedger.run()
            except BaseException:
                run_exc = sys.exc_info()
            
            report_exc = None
            
            try:
                report_stats = hedger.last_stats if hedger.last_stats is not None else stats
                if report_stats is None:
                    raise RuntimeError('hedger.py: report_stats is None')
                
                pnl = calc_hedger_pnl_stats(report_stats)
                
                print(f'Hedger PnL report for iteration {n_iter}:')
                print(f'  CEX PnL: {float(pnl.cex_pnl_quote):.8f}')
                print(f'  DEX PnL (realized IL): {float(pnl.dex_realized_il_quote):.8f}')
                print(f'  Fees received: {float(pnl.fees_received_quote):.8f}')
                print(f'  Gas paid (ETH): {float(pnl.gas_paid_eth):.8f}')
                print(f'  Gas paid (quote): {float(pnl.gas_paid_quote):.8f}')
                print(f'  Pool hold seconds: {float(pnl.pool_hold_seconds):.3f}')
                print(f'  APR (%): {float(pnl.apr_pct):.6f}')
                print(f'  Total PnL: {float(pnl.total_pnl_quote):.8f}')
            except Exception:
                report_exc = sys.exc_info()
            
            if run_exc is not None and report_exc is not None:
                _t_run, exc_run, _tb_run = run_exc
                _t_report, exc_report, _tb_report = report_exc
                raise RuntimeError(f'hedger.py: run failed ({exc_run}) and report failed ({exc_report})') from exc_run
            
            if report_exc is not None:
                _t, exc, tb = report_exc
                raise exc.with_traceback(tb)
            
            if run_exc is not None:
                _t, exc, tb = run_exc
                raise exc.with_traceback(tb)
            
            if not bool(args.loop):
                break
    
    finally:
        hedger.stop()


if __name__ == '__main__':
    main()