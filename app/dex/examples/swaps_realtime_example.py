import argparse, time, json
from ..lib.logger import get_logger
from ..models.realtime_models import SwapEventAbi, SwapEventAbiInput, SwapsRealtimeConfig
from ..realtime.swaps_realtime import SwapsRealtime
from ..contract.contract_wrapper import ContractWrapper


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--ws-url', required=True)
    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--pool-address', required=True)
    p.add_argument('--pool-abi-path', default='dex/contract/abi/UniswapV3Pool.json')
    p.add_argument('--erc20-abi-path', default='dex/contract/abi/ERC20.json')
    p.add_argument('--swap-topic0', default='0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67')
    p.add_argument('--seconds', type=int, default=60)

    return p.parse_args()


### ABI ###
def build_swap_abi():
    return SwapEventAbi(
        name='Swap',
        type='event',
        inputs=[
            SwapEventAbiInput(name='sender', type='address', indexed=True),
            SwapEventAbiInput(name='recipient', type='address', indexed=True),
            SwapEventAbiInput(name='amount0', type='int256', indexed=False),
            SwapEventAbiInput(name='amount1', type='int256', indexed=False),
            SwapEventAbiInput(name='sqrtPriceX96', type='uint160', indexed=False),
            SwapEventAbiInput(name='liquidity', type='uint128', indexed=False),
            SwapEventAbiInput(name='tick', type='int24', indexed=False),
        ],
    )


### Main ###
def main():
    args = parse_args()
    logger = get_logger('swaps_realtime_example')

    abi = build_swap_abi()

    config = SwapsRealtimeConfig(
        ws_url=str(args.ws_url),
        pool_address=str(args.pool_address),
        swap_topic0=str(args.swap_topic0),
        swap_abi=abi,
    )

    rt = SwapsRealtime(config=config)
    cw = ContractWrapper(
        rpc_url=str(args.rpc_url),
        pool_address=str(args.pool_address),
        network=str(args.network),
        pool_abi_path=str(args.pool_abi_path),
        erc20_abi_path=str(args.erc20_abi_path),
    )

    def on_swap(evt):
        price_evt = cw.convet_to_price_event(evt)
        msg = json.dumps(price_evt.model_dump(), ensure_ascii=False)
        logger.info(msg)

    rt.set_on_swap(on_swap)
    rt.start()

    for i in range(int(args.seconds)):
        logger.info(f'Before stopping: {int(args.seconds) - i}')
        time.sleep(1)

    rt.stop()
    logger.info('Realtime stopped')

    for i in range(10):
        logger.info(f'Before exiting: {10 - i}')
        time.sleep(1)


if __name__ == '__main__':
    main()
