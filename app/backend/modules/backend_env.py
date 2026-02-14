from typing import List, Optional, Tuple

import envcheck


ENV_BINANCE_KEY = 'BINANCE_KEY'
ENV_BINANCE_SECRET = 'BINANCE_SECRET'
ENV_PRIVATE_KEY = 'PRIVATE_KEY'
ENV_WALLET_ADDRESS = 'WALLET_ADDRESS'


def required_env() -> List[str]:
    return [
        ENV_BINANCE_KEY,
        ENV_BINANCE_SECRET,
        ENV_PRIVATE_KEY,
    ]


def check_required_env() -> None:
    envcheck.require(required_env())


def read_runtime_secrets() -> Tuple[str, str, str, Optional[str]]:
    check_required_env()

    binance_key = envcheck.get(ENV_BINANCE_KEY)
    binance_secret = envcheck.get(ENV_BINANCE_SECRET)
    private_key = envcheck.get(ENV_PRIVATE_KEY)
    wallet_address = envcheck.optional(ENV_WALLET_ADDRESS)

    return (
        str(binance_key),
        str(binance_secret),
        str(private_key),
        None if wallet_address is None else str(wallet_address),
    )
