import argparse, os, traceback
import orjson
from dex.lib.logger import get_logger
from dex.models.swapper_models import CowSwapConfig, SwapRequest, SwapperType
from dex.swappers.swapper_factory import SwapperFactory


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--sell-token', required=True)
    p.add_argument('--buy-token', required=True)
    p.add_argument('--amount', required=True, type=float)
    p.add_argument('--cowswap-api-timeout-sec', type=int, default=10)
    p.add_argument('--cowswap-wait-timeout-sec', type=int, default=300)
    p.add_argument('--cowswap-poll-interval-sec', type=int, default=3)

    return p.parse_args()


### Main ###
def main():
    args = parse_args()
    logger = get_logger('cow_swap_example')

    if 'PRIVATE_KEY' not in os.environ:
        raise RuntimeError('PRIVATE_KEY not found in environment variables')

    private_key = os.environ['PRIVATE_KEY']
    wallet_address = None

    if 'WALLET_ADDRESS' in os.environ:
        wallet_address = os.environ['WALLET_ADDRESS']

    swapper_config = CowSwapConfig(
        swapper_type=SwapperType.COW_SWAP,
        network=str(args.network),
        rpc_url=str(args.rpc_url),
        private_key=str(private_key),
        wallet_address=str(wallet_address) if wallet_address is not None else None,
        api_timeout_sec=int(args.cowswap_api_timeout_sec),
        wait_timeout_sec=int(args.cowswap_wait_timeout_sec),
        poll_interval_sec=int(args.cowswap_poll_interval_sec),
    )
    swapper = SwapperFactory(swapper_config).create()

    swap_request = SwapRequest(
        sell_token=str(args.sell_token),
        buy_token=str(args.buy_token),
        amount=float(args.amount),
        wait_timeout_sec=int(args.cowswap_wait_timeout_sec),
        poll_interval_sec=int(args.cowswap_poll_interval_sec),
    )

    try:
        result = swapper.swap_sync(swap_request)
        payload = orjson.dumps(result.model_dump(), option=orjson.OPT_INDENT_2).decode('utf-8')
        logger.info(payload)
    except Exception as e:
        logger.error(f'Swap error: {e} {traceback.format_exc()}')


if __name__ == '__main__':
    main()
