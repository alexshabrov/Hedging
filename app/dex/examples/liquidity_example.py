import argparse, os, traceback, json
from dex.contract.contract_wrapper import ContractWrapper
from dex.lib.logger import get_logger


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--pool-address', required=True)
    p.add_argument('--fee-pct', required=True, type=float)
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

    logger.info('Commands: ADD <price_lower> <price_upper> <total_quote> | STATE <token_id> | REMOVE <token_id> | COLLECT <token_id> | HELP')

    while True:
        cmd = input().strip()
        if len(cmd) == 0:
            continue

        try:
            parts = cmd.split()
            action = parts[0].upper()

            if action == 'HELP':
                logger.info('Commands: ADD <price_lower> <price_upper> <total_quote> | STATE <token_id> | REMOVE <token_id> | COLLECT <token_id> | HELP')
                continue

            if action == 'ADD':
                if len(parts) != 4:
                    raise RuntimeError('ADD requires price_lower price_upper total_quote')
                price_lower = float(parts[1])
                price_upper = float(parts[2])
                total_quote = float(parts[3])
                result = cw.add_liquidity_traditional(
                    fee_pct=float(args.fee_pct),
                    price_lower=float(price_lower),
                    price_upper=float(price_upper),
                    total_quote=float(total_quote),
                )
                logger.info(f'ADD result: {json.dumps(result.model_dump(), indent=4)}')
                continue

            if action == 'STATE':
                if len(parts) != 2:
                    raise RuntimeError('STATE requires token_id')
                state = cw.get_position_state(int(parts[1]))
                logger.info(f'STATE: {json.dumps(state.model_dump(), indent=4)}')
                continue

            if action == 'REMOVE':
                if len(parts) != 2:
                    raise RuntimeError('REMOVE requires token_id')
                res = cw.decrease_liquidity(int(parts[1]), liquidity_percent=100)
                logger.info(f'REMOVE result: {json.dumps(res.model_dump(), indent=4)}')
                continue

            if action == 'COLLECT':
                if len(parts) != 2:
                    raise RuntimeError('COLLECT requires token_id')
                res = cw.collect_fees(int(parts[1]))
                logger.info(f'COLLECT result: {json.dumps(res.model_dump(), indent=4)}')
                continue

            logger.error('Unknown command. Use HELP')

        except Exception as e:
            logger.error(f'Command error: {e} {traceback.format_exc()}')


if __name__ == '__main__':
    main()
