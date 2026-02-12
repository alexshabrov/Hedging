import argparse, os, traceback
import orjson
from dex.contract.contract_wrapper import ContractWrapper
from dex.lib.logger import get_logger
from dex.lib.strict_model import StrictModel
from dex.models.contract_models import CollectFeesResult, DecreaseLiquidityResult, MintResult
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
    init_balance0_raw = int(cw.get_balance(str(token0_address)))
    init_balance1_raw = int(cw.get_balance(str(token1_address)))

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

    def _get_price_now() -> float:
        price_now = float(cw.get_current_traditional_price())
        if float(price_now) <= 0:
            raise RuntimeError('price_now must be > 0')
        return float(price_now)

    class _QuoteBaseState(StrictModel):
        quote_decimals: int
        base_decimals: int
        quote_address: str
        base_address: str
        quote_raw: int
        base_raw: int

        def model_dump(self) -> dict:  # type: ignore[override]
            return {
                'quote_decimals': self.quote_decimals,
                'base_decimals': self.base_decimals,
                'quote_address': self.quote_address,
                'base_address': self.base_address,
                'quote_raw': self.quote_raw,
                'base_raw': self.base_raw,
            }

    class _LiquidityLossReport(StrictModel):
        price_change_pct: float
        value_change_pct: float

        def model_dump(self) -> dict:  # type: ignore[override]
            return {
                'price_change_pct': self.price_change_pct,
                'value_change_pct': self.value_change_pct,
            }

    def _get_quote_base_state(balance0_raw: int, balance1_raw: int) -> _QuoteBaseState:
        token0_lower = str(token0_address).lower()
        token1_lower = str(token1_address).lower()
        quote_lower = str(quote_address).lower()

        if quote_lower == token0_lower:
            return _QuoteBaseState(
                quote_decimals=int(token0_decimals),
                base_decimals=int(token1_decimals),
                quote_address=str(token0_address),
                base_address=str(token1_address),
                quote_raw=int(balance0_raw),
                base_raw=int(balance1_raw),
            )

        if quote_lower == token1_lower:
            return _QuoteBaseState(
                quote_decimals=int(token1_decimals),
                base_decimals=int(token0_decimals),
                quote_address=str(token1_address),
                base_address=str(token0_address),
                quote_raw=int(balance1_raw),
                base_raw=int(balance0_raw),
            )

        raise RuntimeError('quote token does not match pool tokens')

    def _calc_loss_report(price_start: float, price_now: float, quote_start: float, base_start: float,
                          quote_now: float, base_now: float) -> _LiquidityLossReport:
        if float(price_start) <= 0:
            raise RuntimeError('loss report: price_start must be > 0')
        if float(price_now) <= 0:
            raise RuntimeError('loss report: price_now must be > 0')
        if float(quote_start) <= 0:
            raise RuntimeError('loss report: quote_start must be > 0')
        if float(base_start) < 0:
            raise RuntimeError('loss report: base_start must be >= 0')
        if float(quote_now) <= 0:
            raise RuntimeError('loss report: quote_now must be > 0')
        if float(base_now) < 0:
            raise RuntimeError('loss report: base_now must be >= 0')

        total_start = float(quote_start) + float(base_start) * float(price_start)
        total_now = float(quote_now) + float(base_now) * float(price_now)

        if float(total_start) <= 0:
            raise RuntimeError('loss report: total_start must be > 0')

        price_change_pct = (float(price_now) - float(price_start)) / float(price_start) * 100.0
        value_change_pct = (float(total_now) - float(total_start)) / float(total_start) * 100.0

        return _LiquidityLossReport(
            price_change_pct=float(price_change_pct),
            value_change_pct=float(value_change_pct),
        )

    def _log_loss_report(price_start: float, price_now: float, quote_start: float, base_start: float,
                         quote_now: float, base_now: float) -> None:
        report = _calc_loss_report(
            price_start=float(price_start),
            price_now=float(price_now),
            quote_start=float(quote_start),
            base_start=float(base_start),
            quote_now=float(quote_now),
            base_now=float(base_now),
        )
        logger.info(
            'LOSS_REPORT\n'
            f'price_change_pct={report.price_change_pct}\n'
            f'value_change_pct={report.value_change_pct}'
        )

    add_price = None
    add_quote = None
    add_base = None
    last_decrease = None

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
                add_price = float(_get_price_now())
                result = cw.add_liquidity_traditional(
                    fee_pct=float(args.fee_pct),
                    price_lower=price_lower,
                    price_upper=price_upper,
                    total_quote=float(total_quote),
                )
                if result is None:
                    raise RuntimeError('ADD result is None')
                if not isinstance(result, MintResult):
                    raise RuntimeError(f'ADD result is not MintResult: {type(result)}')
                if not bool(result.ok):
                    raise RuntimeError('ADD failed')
                if result.token_id is None or int(result.token_id) <= 0:
                    raise RuntimeError('ADD token_id is missing')
                logger.info(f'ADD result: {_dump(result.model_dump())}')
                add_price = float(_get_price_now())
                if result.amount_quote is None:
                    raise RuntimeError('ADD amount_quote is missing')
                if result.amount_base is None:
                    raise RuntimeError('ADD amount_base is missing')
                add_quote = float(result.amount_quote)
                add_base = float(result.amount_base)
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
                if res is None:
                    raise RuntimeError('REMOVE result is None')
                if not isinstance(res, DecreaseLiquidityResult):
                    raise RuntimeError(f'REMOVE result is not DecreaseLiquidityResult: {type(res)}')
                if not bool(res.ok):
                    raise RuntimeError('REMOVE failed')
                logger.info(f'REMOVE result: {_dump(res.model_dump())}')
                last_decrease = res
                continue

            if action == 'COLLECT':
                if len(parts) != 2:
                    raise RuntimeError('COLLECT requires token_id')
                res = cw.collect_fees(int(parts[1]))
                if res is None:
                    raise RuntimeError('COLLECT result is None')
                if not isinstance(res, CollectFeesResult):
                    raise RuntimeError(f'COLLECT result is not CollectFeesResult: {type(res)}')
                if not bool(res.ok):
                    raise RuntimeError('COLLECT failed')
                logger.info(f'COLLECT result: {_dump(res.model_dump())}')
                if add_price is None or add_quote is None or add_base is None:
                    raise RuntimeError('ADD state is missing')
                if last_decrease is None:
                    raise RuntimeError('REMOVE state is missing')
                if last_decrease.amount_quote is None:
                    raise RuntimeError('REMOVE amount_quote is missing')
                if last_decrease.amount_base is None:
                    raise RuntimeError('REMOVE amount_base is missing')
                price_now = float(_get_price_now())
                _log_loss_report(
                    price_start=float(add_price),
                    price_now=float(price_now),
                    quote_start=float(add_quote),
                    base_start=float(add_base),
                    quote_now=float(last_decrease.amount_quote),
                    base_now=float(last_decrease.amount_base),
                )
                continue

            if action == 'REBALANCE':
                if len(parts) != 1:
                    raise RuntimeError('REBALANCE does not accept arguments')

                balance0_raw = int(cw.get_balance(str(token0_address)))
                balance1_raw = int(cw.get_balance(str(token1_address)))
                current_price = float(_get_price_now())

                state_now = _get_quote_base_state(int(balance0_raw), int(balance1_raw))
                quote_now = float(state_now.quote_raw) / float(10 ** int(state_now.quote_decimals))
                base_now = float(state_now.base_raw) / float(10 ** int(state_now.base_decimals))

                state_init = _get_quote_base_state(int(init_balance0_raw), int(init_balance1_raw))
                quote_init = float(state_init.quote_raw) / float(10 ** int(state_init.quote_decimals))
                base_init = float(state_init.base_raw) / float(10 ** int(state_init.base_decimals))

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

                    sell_token = str(state_now.quote_address)
                    buy_token = str(state_now.base_address)
                    sell_amount = float(quote_needed)

                elif float(delta_base) > 0.0 and float(delta_quote) < 0.0:
                    quote_needed = abs(float(delta_quote))
                    base_needed = float(quote_needed) / float(current_price)
                    if float(base_needed) <= 0:
                        raise RuntimeError('base_needed must be > 0')
                    if float(base_now) < float(base_needed):
                        raise RuntimeError('insufficient base balance for rebalance')

                    sell_token = str(state_now.base_address)
                    buy_token = str(state_now.quote_address)
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
