import argparse, os, traceback
import orjson
from dex.contract.contract_wrapper import ContractWrapper
from dex.lib.logger import get_logger
from dex.models.swapper_models import CowSwapConfig, SwapRequest, SwapperType
from dex.swappers.swapper_factory import SwapperFactory


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--pool-address', required=True)
    p.add_argument('--fee-pct', required=True, type=float)
    p.add_argument('--cowswap-api-timeout-sec', type=int, default=10)
    p.add_argument('--cowswap-wait-timeout-sec', type=int, default=300)
    p.add_argument('--cowswap-poll-interval-sec', type=int, default=3)
    return p.parse_args()


### Main ###
def main():
    args = parse_args()
    logger = get_logger('liquidity_example')

    if 'PRIVATE_KEY' not in os.environ:
        raise RuntimeError('PRIVATE_KEY not found in environment variables')

    private_key = os.environ['PRIVATE_KEY']
    wallet_address = None

    if 'WALLET_ADDRESS' in os.environ:
        wallet_address = os.environ['WALLET_ADDRESS']

    cw = ContractWrapper(
        rpc_url=str(args.rpc_url),
        pool_address=str(args.pool_address),
        network=str(args.network),
        private_key=str(private_key),
        wallet_address=str(wallet_address) if wallet_address is not None else None,
    )

    token0_address = cw.get_token0_address()
    token1_address = cw.get_token1_address()
    quote_address = cw.get_quote_token_address()
    token0_decimals = cw.get_token0_decimals()
    token1_decimals = cw.get_token1_decimals()

    init_balance0_raw = cw.get_balance(str(token0_address))
    init_balance1_raw = cw.get_balance(str(token1_address))

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

    def _dump(data: dict) -> str:
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode('utf-8')

    logger.info('Commands: ADD <price_lower|-> <price_upper|-> <total_quote> | STATE <token_id> | REMOVE <token_id> | COLLECT <token_id> | REBALANCE | HELP')

    while True:
        cmd = input().strip()
        if len(cmd) == 0:
            continue

        try:
            parts = cmd.split()
            action = parts[0].upper()

            if action == 'HELP':
                logger.info('Commands: ADD <price_lower|-> <price_upper|-> <total_quote> | STATE <token_id> | REMOVE <token_id> | COLLECT <token_id> | REBALANCE | HELP')
                continue

            if action == 'ADD':
                if len(parts) != 4:
                    raise RuntimeError('ADD requires price_lower price_upper total_quote')
                price_lower = None if str(parts[1]).strip() == '-' else float(parts[1])
                price_upper = None if str(parts[2]).strip() == '-' else float(parts[2])
                total_quote = float(parts[3])
                result = cw.add_liquidity_traditional(
                    fee_pct=float(args.fee_pct),
                    price_lower=price_lower,
                    price_upper=price_upper,
                    total_quote=float(total_quote),
                )
                logger.info(f'ADD result: {_dump(result.model_dump())}')
                continue

            if action == 'STATE':
                if len(parts) != 2:
                    raise RuntimeError('STATE requires token_id')
                state = cw.get_position_state(int(parts[1]))
                logger.info(f'STATE: {_dump(state.model_dump())}')
                continue

            if action == 'REMOVE':
                if len(parts) != 2:
                    raise RuntimeError('REMOVE requires token_id')
                res = cw.decrease_liquidity(int(parts[1]), liquidity_percent=100)
                logger.info(f'REMOVE result: {_dump(res.model_dump())}')
                continue

            if action == 'COLLECT':
                if len(parts) != 2:
                    raise RuntimeError('COLLECT requires token_id')
                res = cw.collect_fees(int(parts[1]))
                logger.info(f'COLLECT result: {_dump(res.model_dump())}')
                continue

            if action == 'REBALANCE':
                if len(parts) != 1:
                    raise RuntimeError('REBALANCE does not accept arguments')

                balance0_raw = cw.get_balance(str(token0_address))
                balance1_raw = cw.get_balance(str(token1_address))
                current_price = cw.get_current_traditional_price()

                token0_address = str(token0_address).lower()
                token1_address = str(token1_address).lower()
                quote_address = str(quote_address).lower()

                if quote_address == token0_address:
                    quote_decimals = int(token0_decimals)
                    base_decimals = int(token1_decimals)
                    quote_now_raw = int(balance0_raw)
                    base_now_raw = int(balance1_raw)
                    quote_init_raw = int(init_balance0_raw)
                    base_init_raw = int(init_balance1_raw)
                    quote_address_now = str(token0_address)
                    base_address_now = str(token1_address)
                elif quote_address == token1_address:
                    quote_decimals = int(token1_decimals)
                    base_decimals = int(token0_decimals)
                    quote_now_raw = int(balance1_raw)
                    base_now_raw = int(balance0_raw)
                    quote_init_raw = int(init_balance1_raw)
                    base_init_raw = int(init_balance0_raw)
                    quote_address_now = str(token1_address)
                    base_address_now = str(token0_address)
                else:
                    raise RuntimeError('quote token does not match pool tokens')

                quote_now = float(quote_now_raw) / float(10 ** quote_decimals)
                base_now = float(base_now_raw) / float(10 ** base_decimals)
                quote_init = float(quote_init_raw) / float(10 ** quote_decimals)
                base_init = float(base_init_raw) / float(10 ** base_decimals)

                delta_quote = float(quote_now) - float(quote_init)
                delta_base = float(base_now) - float(base_init)

                if float(delta_quote) == 0.0 and float(delta_base) == 0.0:
                    logger.info('REBALANCE: balances already match initial values')
                    continue

                if float(current_price) <= 0:
                    raise RuntimeError('current_price must be > 0')

                if float(delta_quote) > 0.0 and float(delta_base) < 0.0:
                    base_needed = abs(float(delta_base))
                    quote_needed = float(base_needed) * float(current_price)
                    if float(quote_needed) <= 0:
                        raise RuntimeError('quote_needed must be > 0')
                    if float(quote_now) < float(quote_needed):
                        raise RuntimeError('insufficient quote balance for rebalance')

                    sell_token = str(quote_address_now)
                    buy_token = str(base_address_now)
                    sell_amount = float(quote_needed)

                elif float(delta_base) > 0.0 and float(delta_quote) < 0.0:
                    quote_needed = abs(float(delta_quote))
                    base_needed = float(quote_needed) / float(current_price)
                    if float(base_needed) <= 0:
                        raise RuntimeError('base_needed must be > 0')
                    if float(base_now) < float(base_needed):
                        raise RuntimeError('insufficient base balance for rebalance')

                    sell_token = str(base_address_now)
                    buy_token = str(quote_address_now)
                    sell_amount = float(base_needed)

                else:
                    raise RuntimeError('balances are not in opposite directions')

                swap_request = SwapRequest(
                    sell_token=str(sell_token),
                    buy_token=str(buy_token),
                    amount=float(sell_amount),
                    wait_timeout_sec=int(args.cowswap_wait_timeout_sec),
                    poll_interval_sec=int(args.cowswap_poll_interval_sec),
                )
                result = swapper.swap_sync(swap_request)
                logger.info(f'REBALANCE result: {_dump(result.model_dump())}')
                continue

            logger.error('Unknown command. Use HELP')

        except Exception as e:
            logger.error(f'Command error: {e} {traceback.format_exc()}')


if __name__ == '__main__':
    main()
