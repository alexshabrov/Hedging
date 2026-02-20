from typing import List, Optional

import envcheck


ENV_FRONT_SECRET_KEY = 'FRONT_SECRET_KEY'
ENV_FRONT_ADMIN_PASSWORD = 'FRONT_ADMIN_PASSWORD'
ENV_FRONT_BACKEND_URL = 'FRONT_BACKEND_URL'
ENV_FRONT_SESSION_COOKIE_DOMAIN = 'FRONT_SESSION_COOKIE_DOMAIN'

ENV_RPC_KEY = 'RPC_KEY'
ENV_MONGO_URI = 'MONGO_URI'
ENV_MONGO_DB = 'MONGO_DB'
ENV_MONGO_COLLECTION = 'MONGO_COLLECTION'
ENV_TICK_MS = 'TICK_MS'
ENV_GTX_COOLDOWN_MS = 'GTX_COOLDOWN_MS'
ENV_ENTRANCE_TIMEOUT_MS = 'ENTRANCE_TIMEOUT_MS'
ENV_COWSWAP_API_TIMEOUT_SEC = 'COWSWAP_API_TIMEOUT_SEC'
ENV_COWSWAP_WAIT_TIMEOUT_SEC = 'COWSWAP_WAIT_TIMEOUT_SEC'
ENV_COWSWAP_POLL_INTERVAL_SEC = 'COWSWAP_POLL_INTERVAL_SEC'


def required_env() -> List[str]:
    return [
        ENV_FRONT_SECRET_KEY,
        ENV_FRONT_ADMIN_PASSWORD,
        ENV_RPC_KEY,
    ]


def check_required_env() -> None:
    envcheck.require(required_env())


def get_front_secret_key() -> str:
    return envcheck.get(ENV_FRONT_SECRET_KEY)


def get_front_admin_password() -> str:
    return envcheck.get(ENV_FRONT_ADMIN_PASSWORD)


def get_front_backend_url() -> str:
    return envcheck.get_default(ENV_FRONT_BACKEND_URL, 'http://127.0.0.1:8080')


def get_front_session_cookie_domain() -> Optional[str]:
    return envcheck.optional(ENV_FRONT_SESSION_COOKIE_DOMAIN)


def get_rpc_key() -> str:
    return envcheck.get(ENV_RPC_KEY)


def get_mongo_uri() -> str:
    return envcheck.get_default(ENV_MONGO_URI, 'mongodb://hedging_mongo:27017')


def get_mongo_db() -> str:
    return envcheck.get_default(ENV_MONGO_DB, 'hedging')


def get_mongo_collection() -> str:
    return envcheck.get_default(ENV_MONGO_COLLECTION, 'hedge_runs')


def get_tick_ms() -> int:
    return int(envcheck.get_default(ENV_TICK_MS, '5'))


def get_gtx_cooldown_ms() -> int:
    return int(envcheck.get_default(ENV_GTX_COOLDOWN_MS, '5'))


def get_entrance_timeout_ms() -> int:
    return int(envcheck.get_default(ENV_ENTRANCE_TIMEOUT_MS, '60000'))


def get_cowswap_api_timeout_sec() -> int:
    return int(envcheck.get_default(ENV_COWSWAP_API_TIMEOUT_SEC, '10'))


def get_cowswap_wait_timeout_sec() -> int:
    return int(envcheck.get_default(ENV_COWSWAP_WAIT_TIMEOUT_SEC, '300'))


def get_cowswap_poll_interval_sec() -> int:
    return int(envcheck.get_default(ENV_COWSWAP_POLL_INTERVAL_SEC, '3'))
