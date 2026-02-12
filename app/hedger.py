import argparse, os
import orjson

from modules.hedger_class import Hedger
from models.hedger_models import HedgerConfig, CexTriggerMode


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
    p.add_argument('--cex-ratio', type=float, default=0.5)
    
    p.add_argument('--trigger-mode', choices=['one_tick', 'small_pct'], default='small_pct')
    p.add_argument('--trigger-pct', type=float, default=0.05)
    
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
    
    if str(args.trigger_mode) == CexTriggerMode.ONE_TICK.value:
        trigger_mode = CexTriggerMode.ONE_TICK
    elif str(args.trigger_mode) == CexTriggerMode.SMALL_PCT.value:
        trigger_mode = CexTriggerMode.SMALL_PCT
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
    
    stats = hedger.run()
    raw = orjson.dumps(stats.model_dump(), option=orjson.OPT_INDENT_2)
    print(raw.decode('utf-8'))


if __name__ == '__main__':
    main()