import argparse, os
from dex.contract.contract_wrapper import ContractWrapper
from dex.lib.logger import get_logger


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--pool-address', required=True)
    p.add_argument('--fee-pct', required=True, type=float)
    p.add_argument('--price-lower', required=True, type=float)
    p.add_argument('--price-upper', required=True, type=float)
    p.add_argument('--total-quote', required=True, type=float)

    return p.parse_args()


### Main ###
def main():
    args = parse_args()
    logger = get_logger('add_liquidity_example')

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

    result = cw.add_liquidity_traditional(
        fee_pct=float(args.fee_pct),
        price_lower=float(args.price_lower),
        price_upper=float(args.price_upper),
        total_quote=float(args.total_quote),
    )

    logger.info(f'Result: {result}')


if __name__ == '__main__':
    main()
