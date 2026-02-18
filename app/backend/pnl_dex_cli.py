"""
Wallet on-chain history and realized PnL report
Date: 2026-02-15
Version: 1.1
"""
import argparse, hashlib, json, os, requests, time
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from web3 import Web3
from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from live.lib.strict_model import StrictModel


### Constants ###
ERC20_ABI = [
    {
        'constant': True,
        'inputs': [],
        'name': 'symbol',
        'outputs': [{'name': '', 'type': 'string'}],
        'type': 'function',
    },
    {
        'constant': True,
        'inputs': [],
        'name': 'decimals',
        'outputs': [{'name': '', 'type': 'uint8'}],
        'type': 'function',
    },
    {
        'constant': True,
        'inputs': [{'name': 'owner', 'type': 'address'}],
        'name': 'balanceOf',
        'outputs': [{'name': '', 'type': 'uint256'}],
        'type': 'function',
    },
]

POOL_ABI = [
    {
        'constant': True,
        'inputs': [],
        'name': 'token0',
        'outputs': [{'name': '', 'type': 'address'}],
        'type': 'function',
    },
    {
        'constant': True,
        'inputs': [],
        'name': 'token1',
        'outputs': [{'name': '', 'type': 'address'}],
        'type': 'function',
    },
    {
        'constant': True,
        'inputs': [],
        'name': 'slot0',
        'outputs': [
            {'name': 'sqrtPriceX96', 'type': 'uint160'},
            {'name': 'tick', 'type': 'int24'},
            {'name': 'observationIndex', 'type': 'uint16'},
            {'name': 'observationCardinality', 'type': 'uint16'},
            {'name': 'observationCardinalityNext', 'type': 'uint16'},
            {'name': 'feeProtocol', 'type': 'uint8'},
            {'name': 'unlocked', 'type': 'bool'},
        ],
        'type': 'function',
    },
]

NETWORK_NPM: Dict[str, str] = {
    'arbitrum': '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
    'ethereum': '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
    'polygon': '0xC36442b4a4522E871399CD717aBDD847Ab11FE88',
    'base': '0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1',
}

NETWORK_UNISWAP_ROUTERS: Dict[str, List[str]] = {
    'arbitrum': [
        '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45',
        '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        '0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B',
    ],
    'ethereum': [
        '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45',
        '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        '0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B',
    ],
    'polygon': [
        '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45',
        '0xE592427A0AEce92De3Edee1F18E0157C05861564',
        '0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B',
    ],
    'base': [
        '0x2626664c2603336E57B271c5C0b26F421741e481',
        '0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD',
    ],
}

ACTIVE_POSITIONS_COLLECTION = 'backend_positions_active'
ARCHIVE_POSITIONS_COLLECTION = 'backend_positions_archive'
HEDGER_RUNS_COLLECTION = 'backend_hedger_runs'
CACHE_DIR = '/tmp/pnl_dex_cli_cache_v2'
CACHE_BINANCE_PRICE_TTL_SEC = 15
UNISWAP_V3_Q96 = 2 ** 96

TRANSFER_TOPIC = Web3.to_hex(Web3.keccak(text='Transfer(address,address,uint256)'))
INCREASE_LIQ_TOPIC = Web3.to_hex(Web3.keccak(text='IncreaseLiquidity(uint256,uint128,uint256,uint256)'))
DECREASE_LIQ_TOPIC = Web3.to_hex(Web3.keccak(text='DecreaseLiquidity(uint256,uint128,uint256,uint256)'))
COLLECT_TOPIC = Web3.to_hex(Web3.keccak(text='Collect(uint256,address,uint256,uint256)'))
POOL_SWAP_TOPIC = Web3.to_hex(Web3.keccak(text='Swap(address,address,int256,int256,uint160,uint128,int24)'))


### Enums ###
class TxKind(str, Enum):
    ADD = 'add'
    REMOVE = 'remove'
    COLLECT = 'collect'
    SWAP = 'swap'
    OTHER = 'other'


### Models ###
class TokenMeta(StrictModel):
    symbol: str
    address: str
    decimals: int


class TokenDelta(StrictModel):
    weth: float
    usdc: float


class AlchemyTransferRawContract(StrictModel):
    value: Optional[str]
    address: Optional[str]
    decimal: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlchemyTransferRawContract':
        if data is None:
            raise RuntimeError('AlchemyTransferRawContract.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'AlchemyTransferRawContract.from_dict: data is not dict: {type(data)}')

        if 'value' not in data:
            raise RuntimeError('AlchemyTransferRawContract.from_dict: value is missing')
        if 'address' not in data:
            raise RuntimeError('AlchemyTransferRawContract.from_dict: address is missing')
        if 'decimal' not in data:
            raise RuntimeError('AlchemyTransferRawContract.from_dict: decimal is missing')

        return cls(
            value=None if data['value'] is None else str(data['value']),
            address=None if data['address'] is None else str(data['address']),
            decimal=None if data['decimal'] is None else str(data['decimal']),
        )


class AlchemyTransfer(StrictModel):
    block_num: str
    tx_hash: str
    from_address: Optional[str]
    to_address: Optional[str]
    asset: Optional[str]
    category: str
    raw_contract: AlchemyTransferRawContract

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlchemyTransfer':
        if data is None:
            raise RuntimeError('AlchemyTransfer.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'AlchemyTransfer.from_dict: data is not dict: {type(data)}')

        if 'blockNum' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: blockNum is missing')
        if 'hash' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: hash is missing')
        if 'from' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: from is missing')
        if 'to' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: to is missing')
        if 'asset' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: asset is missing')
        if 'category' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: category is missing')
        if 'rawContract' not in data:
            raise RuntimeError('AlchemyTransfer.from_dict: rawContract is missing')

        return cls(
            block_num=str(data['blockNum']),
            tx_hash=str(data['hash']),
            from_address=None if data['from'] is None else str(data['from']),
            to_address=None if data['to'] is None else str(data['to']),
            asset=None if data['asset'] is None else str(data['asset']),
            category=str(data['category']),
            raw_contract=AlchemyTransferRawContract.from_dict(data['rawContract']),
        )


class AlchemyTransferPage(StrictModel):
    transfers: List[AlchemyTransfer]
    page_key: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlchemyTransferPage':
        if data is None:
            raise RuntimeError('AlchemyTransferPage.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'AlchemyTransferPage.from_dict: data is not dict: {type(data)}')
        if 'transfers' not in data:
            raise RuntimeError('AlchemyTransferPage.from_dict: transfers is missing')

        raw_transfers = data['transfers']
        if not isinstance(raw_transfers, list):
            raise RuntimeError(f'AlchemyTransferPage.from_dict: transfers is not list: {type(raw_transfers)}')

        transfers: List[AlchemyTransfer] = []
        for raw_item in raw_transfers:
            transfers.append(AlchemyTransfer.from_dict(raw_item))

        page_key = None
        if 'pageKey' in data and data['pageKey'] is not None:
            page_key = str(data['pageKey'])

        return cls(
            transfers=transfers,
            page_key=page_key,
        )


class AlchemyTxRef(StrictModel):
    block_num: str
    tx_hash: str

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlchemyTxRef':
        if data is None:
            raise RuntimeError('AlchemyTxRef.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'AlchemyTxRef.from_dict: data is not dict: {type(data)}')
        if 'blockNum' not in data:
            raise RuntimeError('AlchemyTxRef.from_dict: blockNum is missing')
        if 'hash' not in data:
            raise RuntimeError('AlchemyTxRef.from_dict: hash is missing')

        return cls(
            block_num=str(data['blockNum']),
            tx_hash=str(data['hash']),
        )


class AlchemyTxRefPage(StrictModel):
    transfers: List[AlchemyTxRef]
    page_key: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict) -> 'AlchemyTxRefPage':
        if data is None:
            raise RuntimeError('AlchemyTxRefPage.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'AlchemyTxRefPage.from_dict: data is not dict: {type(data)}')
        if 'transfers' not in data:
            raise RuntimeError('AlchemyTxRefPage.from_dict: transfers is missing')

        raw_transfers = data['transfers']
        if not isinstance(raw_transfers, list):
            raise RuntimeError(f'AlchemyTxRefPage.from_dict: transfers is not list: {type(raw_transfers)}')

        transfers: List[AlchemyTxRef] = []
        for raw_item in raw_transfers:
            transfers.append(AlchemyTxRef.from_dict(raw_item))

        page_key = None
        if 'pageKey' in data and data['pageKey'] is not None:
            page_key = str(data['pageKey'])

        return cls(
            transfers=transfers,
            page_key=page_key,
        )


class LpTokenMove(StrictModel):
    token_id: int
    liquidity: int
    weth: float
    usdc: float


class PendingPrincipal(StrictModel):
    weth: float
    usdc: float


class LpPositionState(StrictModel):
    liquidity_open: int
    principal_weth_open: float
    principal_usdc_open: float
    start_value_target_quote_open: float


class HistoryTx(StrictModel):
    tx_hash: str
    block_number: int
    tx_index: int
    timestamp_ms: int
    kind: TxKind
    is_uniswap_swap: bool
    gas_eth: float
    weth_delta: float
    usdc_delta: float
    swap_weth: float
    swap_usdc: float
    increase_weth: float
    increase_usdc: float
    decrease_weth: float
    decrease_usdc: float
    collect_weth: float
    collect_usdc: float
    increase_moves: List[LpTokenMove]
    decrease_moves: List[LpTokenMove]
    collect_moves: List[LpTokenMove]


class PnlBreakdown(StrictModel):
    fees: TokenDelta
    realized_il: TokenDelta
    swaps: TokenDelta
    gas_eth: float
    gas_usdc: float
    current_price: float
    sum_base_delta: float
    sum_quote_delta: float
    il_quote: float
    costs_pnl_quote: float
    cex_quote: float
    pnl_fees_quote: float
    pnl_fees_il_quote: float
    pnl_fees_il_gas_quote: float
    pnl_fees_il_gas_cex_quote: float
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    hold_time_seconds: float
    capital_quote: float
    apr_fees_pct: float
    apr_fees_il_pct: float
    apr_fees_il_gas_pct: float
    apr_fees_il_gas_cex_pct: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    pnl_usdc_by_components: float


class BalanceSnapshot(StrictModel):
    start_weth: float
    start_usdc: float
    end_weth: float
    end_usdc: float
    delta_weth: float
    delta_usdc: float


class HistoryReport(StrictModel):
    wallet: str
    network: str
    from_block: int
    to_block: int
    eth_price_usdt: float
    balances: BalanceSnapshot
    pnl: PnlBreakdown
    pnl_usdc_by_balances: float
    transactions_total: int
    uniswap_swaps_total: int
    analyzed_transactions: List[HistoryTx]


### CLI ###
def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument('--rpc-url', required=True)
    p.add_argument('--network', required=True)
    p.add_argument('--wallet-address', required=True)
    p.add_argument('--pool-address', required=True)

    p.add_argument('--to-block', type=int)
    p.add_argument('--binance-symbol', default='ETHUSDT')
    p.add_argument('--alchemy-page-size', type=int, default=1000)
    p.add_argument('--mongo-uri', type=str, default='mongodb://hedging_mongo:27017')
    p.add_argument('--mongo-db', type=str, default='hedging')

    return p.parse_args()


### Helpers ###
def _to_jsonable(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bytes):
        return Web3.to_hex(value)
    if isinstance(value, list):
        out_list = []
        for item in value:
            out_list.append(_to_jsonable(item))
        return out_list
    if isinstance(value, tuple):
        out_list = []
        for item in value:
            out_list.append(_to_jsonable(item))
        return out_list
    if isinstance(value, dict):
        out_dict = {}
        for k, v in value.items():
            out_dict[str(k)] = _to_jsonable(v)
        return out_dict
    if hasattr(value, 'items'):
        out_dict = {}
        for k, v in value.items():
            out_dict[str(k)] = _to_jsonable(v)
        return out_dict
    raise RuntimeError(f'unsupported value type for cache serialization: {type(value)}')


def _cache_file_path(namespace: str, cache_key: Dict[str, Any]) -> str:
    if not isinstance(namespace, str) or len(namespace) == 0:
        raise RuntimeError('cache namespace is empty')
    if cache_key is None:
        raise RuntimeError('cache_key is None')
    if not isinstance(cache_key, dict):
        raise RuntimeError(f'cache_key is not dict: {type(cache_key)}')

    key_json = json.dumps(_to_jsonable(cache_key), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(key_json.encode('utf-8')).hexdigest()
    return os.path.join(str(CACHE_DIR), str(namespace), f'{digest}.json')


def _cache_get(namespace: str, cache_key: Dict[str, Any], ttl_seconds: Optional[int]) -> Optional[Any]:
    path = _cache_file_path(str(namespace), cache_key)
    if not os.path.exists(path):
        return None

    with open(path, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise RuntimeError(f'cache payload is not dict: path={path} type={type(payload)}')
    if 'created_at_sec' not in payload:
        raise RuntimeError(f'cache payload has no created_at_sec: path={path}')
    if 'value' not in payload:
        raise RuntimeError(f'cache payload has no value: path={path}')

    created_at_sec = float(payload['created_at_sec'])
    if ttl_seconds is not None and int(ttl_seconds) >= 0:
        if time.time() - float(created_at_sec) > float(ttl_seconds):
            return None

    return payload['value']


def _cache_put(namespace: str, cache_key: Dict[str, Any], value: Any) -> None:
    path = _cache_file_path(str(namespace), cache_key)
    path_dir = os.path.dirname(path)
    os.makedirs(path_dir, exist_ok=True)

    payload = {
        'created_at_sec': float(time.time()),
        'value': _to_jsonable(value),
    }

    tmp_path = f'{path}.tmp.{os.getpid()}.{int(time.time() * 1_000_000)}'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=True, sort_keys=True)
    os.replace(tmp_path, path)


def _hex_to_int(v) -> int:
    if isinstance(v, int):
        return int(v)

    if isinstance(v, bytes):
        return int.from_bytes(v, byteorder='big', signed=False)

    if isinstance(v, str):
        if not v.startswith('0x'):
            raise RuntimeError(f'hex string has no 0x prefix: {v}')
        return int(v, 16)

    raise RuntimeError(f'unsupported numeric type: {type(v)}')


def _decode_uints_from_data(data_hex: str, n_items: int) -> List[int]:
    if not isinstance(data_hex, str):
        raise RuntimeError(f'data_hex is not str: {type(data_hex)}')
    if not data_hex.startswith('0x'):
        raise RuntimeError(f'data_hex has no 0x prefix: {data_hex}')
    if int(n_items) <= 0:
        raise RuntimeError(f'n_items must be > 0: {n_items}')

    payload = data_hex[2:]
    expected_len = 64 * int(n_items)
    if len(payload) != int(expected_len):
        raise RuntimeError(f'data payload length mismatch: expected={expected_len} got={len(payload)}')

    out: List[int] = []
    i = 0
    while i < int(n_items):
        start = int(i) * 64
        end = int(start) + 64
        out.append(int(payload[start:end], 16))
        i += 1

    return out


def _to_float_token(raw: int, decimals: int) -> float:
    if int(decimals) < 0:
        raise RuntimeError(f'decimals is negative: {decimals}')
    return float(int(raw)) / float(10 ** int(decimals))


def _to_quote_pnl(base_delta: float, quote_delta: float, base_price: float) -> float:
    return float(base_delta) * float(base_price) + float(quote_delta)


def _portfolio_quote_value(base_amount: float, quote_amount: float, valuation_price: float) -> float:
    return float(base_amount) * float(valuation_price) + float(quote_amount)


def _avg_quote_cost_per_base(base_accum: float, quote_accum: float) -> float:
    if abs(float(base_accum)) <= 1e-18:
        raise RuntimeError(f'cannot compute avg quote/base cost with base_accum={base_accum}')
    return float(quote_accum) / float(base_accum)


def _load_first_position_started_at_ms(mongo_uri: str, mongo_db: str) -> int:
    if not isinstance(mongo_uri, str) or len(mongo_uri) == 0:
        raise RuntimeError('mongo_uri is empty')
    if not isinstance(mongo_db, str) or len(mongo_db) == 0:
        raise RuntimeError('mongo_db is empty')

    client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
    try:
        _ = client.server_info()
        db = client[str(mongo_db)]
        best_started_at_ms: Optional[int] = None

        for col_name in [ACTIVE_POSITIONS_COLLECTION, ARCHIVE_POSITIONS_COLLECTION]:
            col = db[str(col_name)]
            doc = col.find_one(
                {'started_at_ms': {'$exists': True, '$ne': None}},
                {'_id': 0, 'started_at_ms': 1},
                sort=[('started_at_ms', 1)],
            )
            if doc is None:
                continue
            started_at_ms = int(doc['started_at_ms'])
            if int(started_at_ms) <= 0:
                continue
            if best_started_at_ms is None or int(started_at_ms) < int(best_started_at_ms):
                best_started_at_ms = int(started_at_ms)

        if best_started_at_ms is None:
            raise RuntimeError(
                f'no positions with started_at_ms found in collections: '
                f'{ACTIVE_POSITIONS_COLLECTION}, {ARCHIVE_POSITIONS_COLLECTION}'
            )
        return int(best_started_at_ms)
    finally:
        client.close()


def _load_swap_cost_quote_sum(mongo_uri: str, mongo_db: str, cutoff_started_at_ms: int) -> float:
    if not isinstance(mongo_uri, str) or len(mongo_uri) == 0:
        raise RuntimeError('mongo_uri is empty')
    if not isinstance(mongo_db, str) or len(mongo_db) == 0:
        raise RuntimeError('mongo_db is empty')
    if int(cutoff_started_at_ms) <= 0:
        raise RuntimeError(f'cutoff_started_at_ms must be > 0, got: {cutoff_started_at_ms}')

    client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
    try:
        _ = client.server_info()
        db = client[str(mongo_db)]
        col = db[str(HEDGER_RUNS_COLLECTION)]

        total = 0.0
        cursor = col.find(
            {
                'started_at_ms': {'$gte': int(cutoff_started_at_ms)},
                'swap_cost_quote': {'$exists': True, '$ne': None},
            },
            {'_id': 0, 'swap_cost_quote': 1},
        )
        for item in cursor:
            if not isinstance(item, dict):
                raise RuntimeError(f'bad item type in {HEDGER_RUNS_COLLECTION}: {type(item)}')
            total += float(item.get('swap_cost_quote', 0.0))

        return float(total)
    finally:
        client.close()


def _load_dashboard_apr_basis(mongo_uri: str, mongo_db: str, cutoff_started_at_ms: int) -> Tuple[float, float]:
    if not isinstance(mongo_uri, str) or len(mongo_uri) == 0:
        raise RuntimeError('mongo_uri is empty')
    if not isinstance(mongo_db, str) or len(mongo_db) == 0:
        raise RuntimeError('mongo_db is empty')
    if int(cutoff_started_at_ms) <= 0:
        raise RuntimeError(f'cutoff_started_at_ms must be > 0, got: {cutoff_started_at_ms}')

    client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
    try:
        _ = client.server_info()
        db = client[str(mongo_db)]

        # Sequential runs: working capital is the max configured quote, not sum over runs.
        working_capital_quote = 0.0
        for col_name in [ACTIVE_POSITIONS_COLLECTION, ARCHIVE_POSITIONS_COLLECTION]:
            col = db[str(col_name)]
            cursor = col.find(
                {'started_at_ms': {'$gte': int(cutoff_started_at_ms)}},
                {'_id': 0, 'position.total_quote': 1, 'config.total_quote': 1},
            )
            for item in cursor:
                if not isinstance(item, dict):
                    raise RuntimeError(f'bad item type in {col_name}: {type(item)}')
                total_quote = None
                if 'position' in item and isinstance(item['position'], dict):
                    pos = item['position']
                    if 'total_quote' in pos and pos['total_quote'] is not None:
                        total_quote = float(pos['total_quote'])
                if total_quote is None and 'config' in item and isinstance(item['config'], dict):
                    cfg = item['config']
                    if 'total_quote' in cfg and cfg['total_quote'] is not None:
                        total_quote = float(cfg['total_quote'])
                if total_quote is None:
                    continue
                if float(total_quote) > float(working_capital_quote):
                    working_capital_quote = float(total_quote)

        hold_time_seconds = 0.0
        runs_col = db[str(HEDGER_RUNS_COLLECTION)]
        cursor = runs_col.find(
            {
                'started_at_ms': {'$gte': int(cutoff_started_at_ms)},
                'pnl.pool_hold_seconds': {'$exists': True, '$ne': None},
            },
            {'_id': 0, 'pnl.pool_hold_seconds': 1},
        )
        for item in cursor:
            if not isinstance(item, dict):
                raise RuntimeError(f'bad item type in {HEDGER_RUNS_COLLECTION}: {type(item)}')
            pnl = item.get('pnl')
            if not isinstance(pnl, dict):
                continue
            hold_time_seconds += float(pnl.get('pool_hold_seconds', 0.0))

        if float(working_capital_quote) <= 0.0:
            raise RuntimeError('dashboard apr basis: working_capital_quote <= 0')
        if float(hold_time_seconds) <= 0.0:
            raise RuntimeError('dashboard apr basis: hold_time_seconds <= 0')

        return float(working_capital_quote), float(hold_time_seconds)
    finally:
        client.close()


def _calc_apr(pnl_quote: float, capital_quote: float, hold_seconds: float) -> float:
    if float(capital_quote) <= 0.0:
        return 0.0
    if float(hold_seconds) <= 0.0:
        return 0.0
    seconds_per_year = 365.0 * 24.0 * 60.0 * 60.0
    return (float(pnl_quote) / float(capital_quote)) * (float(seconds_per_year) / float(hold_seconds)) * 100.0


def _topic_to_address(topic_hex: str) -> str:
    if not isinstance(topic_hex, str):
        raise RuntimeError(f'topic is not str: {type(topic_hex)}')
    if not topic_hex.startswith('0x'):
        raise RuntimeError(f'topic has no 0x prefix: {topic_hex}')
    if len(topic_hex) != 66:
        raise RuntimeError(f'topic length is invalid: {topic_hex}')

    return Web3.to_checksum_address('0x' + topic_hex[-40:])


def _topic_to_uint256(topic_obj) -> int:
    topic_hex = Web3.to_hex(topic_obj)
    if not isinstance(topic_hex, str):
        raise RuntimeError(f'topic_hex is not str: {type(topic_hex)}')
    if not topic_hex.startswith('0x'):
        raise RuntimeError(f'topic has no 0x prefix: {topic_hex}')
    if len(topic_hex) != 66:
        raise RuntimeError(f'topic length is invalid: {topic_hex}')
    return int(topic_hex, 16)


def _map_pool_amounts_to_weth_usdc(amount0_raw: int, amount1_raw: int, token0_lc: str, weth_address_lc: str) -> Tuple[int, int]:
    if str(token0_lc) == str(weth_address_lc):
        return int(amount0_raw), int(amount1_raw)
    return int(amount1_raw), int(amount0_raw)


def _rpc_call(rpc_url: str, method: str, params: List, request_id: int):
    cache_key = {
        'rpc_url': str(rpc_url),
        'method': str(method),
        'params': _to_jsonable(params),
    }
    cached_result = _cache_get(
        namespace='rpc',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached_result is not None:
        return cached_result

    payload = {
        'jsonrpc': '2.0',
        'id': int(request_id),
        'method': str(method),
        'params': params,
    }

    response = requests.post(str(rpc_url), json=payload, timeout=30)
    if int(response.status_code) != 200:
        raise RuntimeError(f'rpc call failed: method={method} code={response.status_code} body={response.text}')

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f'rpc result is not dict: method={method} type={type(data)}')
    if 'error' in data:
        raise RuntimeError(f'rpc error for method={method}: {data["error"]}')
    if 'result' not in data:
        raise RuntimeError(f'rpc result has no result field: method={method}')

    result = data['result']
    _cache_put(
        namespace='rpc',
        cache_key=cache_key,
        value=result,
    )
    return result


def _alchemy_list_transfers(rpc_url: str, logger, wallet_address: str, token_address: str, from_block_hex: str, to_block_hex: str, page_size: int, direction: str) -> List[AlchemyTransfer]:
    if str(direction) != 'from' and str(direction) != 'to':
        raise RuntimeError(f'bad direction: {direction}')
    if int(page_size) <= 0:
        raise RuntimeError(f'page_size must be > 0: {page_size}')

    params_obj = {
        'category': ['erc20'],
        'contractAddresses': [str(Web3.to_checksum_address(token_address))],
        'fromBlock': str(from_block_hex),
        'toBlock': str(to_block_hex),
        'withMetadata': False,
        'excludeZeroValue': False,
        'maxCount': hex(int(page_size)),
    }

    wallet_checksum = Web3.to_checksum_address(wallet_address)
    if str(direction) == 'from':
        params_obj['fromAddress'] = str(wallet_checksum)
    else:
        params_obj['toAddress'] = str(wallet_checksum)

    all_items: List[AlchemyTransfer] = []
    page_key = None
    req_id = 1

    while True:
        call_params = [params_obj]
        if page_key is not None:
            params_obj['pageKey'] = str(page_key)
        elif 'pageKey' in params_obj:
            del params_obj['pageKey']

        result = _rpc_call(
            rpc_url=str(rpc_url),
            method='alchemy_getAssetTransfers',
            params=call_params,
            request_id=int(req_id),
        )
        req_id += 1

        page = AlchemyTransferPage.from_dict(result)
        all_items.extend(page.transfers)

        logger.info(f'alchemy_list_transfers direction={direction} token={token_address} loaded={len(page.transfers)} total={len(all_items)}')

        if page.page_key is None:
            break

        page_key = str(page.page_key)

    return all_items


def _alchemy_list_tx_refs(rpc_url: str, logger, params_obj: Dict, log_tag: str) -> List[AlchemyTxRef]:
    if params_obj is None:
        raise RuntimeError('params_obj is None')
    if not isinstance(params_obj, dict):
        raise RuntimeError(f'params_obj is not dict: {type(params_obj)}')
    if not isinstance(log_tag, str) or len(log_tag) == 0:
        raise RuntimeError('log_tag is empty')

    all_items: List[AlchemyTxRef] = []
    page_key = None
    req_id = 50_000

    while True:
        if page_key is not None:
            params_obj['pageKey'] = str(page_key)
        elif 'pageKey' in params_obj:
            del params_obj['pageKey']

        result = _rpc_call(
            rpc_url=str(rpc_url),
            method='alchemy_getAssetTransfers',
            params=[params_obj],
            request_id=int(req_id),
        )
        req_id += 1

        page = AlchemyTxRefPage.from_dict(result)
        all_items.extend(page.transfers)
        logger.info(f'{log_tag} loaded={len(page.transfers)} total={len(all_items)}')

        if page.page_key is None:
            break

        page_key = str(page.page_key)

    return all_items


def _get_eth_price_usdt(symbol: str) -> float:
    cache_key = {'symbol': str(symbol)}
    cached_price = _cache_get(
        namespace='binance_price',
        cache_key=cache_key,
        ttl_seconds=int(CACHE_BINANCE_PRICE_TTL_SEC),
    )
    if cached_price is not None:
        price_cached = float(cached_price)
        if float(price_cached) <= 0:
            raise RuntimeError(f'cached binance price is not positive: {price_cached}')
        return float(price_cached)

    url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
    response = requests.get(url, timeout=10)
    if int(response.status_code) != 200:
        raise RuntimeError(f'binance price request failed: code={response.status_code} body={response.text}')

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f'binance payload is not dict: {type(payload)}')
    if 'price' not in payload:
        raise RuntimeError('binance payload has no price field')

    price = float(payload['price'])
    if float(price) <= 0:
        raise RuntimeError(f'binance price is not positive: {price}')

    _cache_put(
        namespace='binance_price',
        cache_key=cache_key,
        value=float(price),
    )
    return float(price)


def _w3_rpc_hint(w3: Web3) -> str:
    if hasattr(w3, 'provider') and hasattr(w3.provider, 'endpoint_uri'):
        return str(w3.provider.endpoint_uri)
    return ''


def _w3_get_transaction_cached(w3: Web3, tx_hash: str) -> Dict[str, Any]:
    cache_key = {
        'rpc_hint': str(_w3_rpc_hint(w3)),
        'tx_hash': str(tx_hash),
    }
    cached = _cache_get(
        namespace='w3_get_transaction',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        if not isinstance(cached, dict):
            raise RuntimeError(f'cached tx is not dict: tx_hash={tx_hash} type={type(cached)}')
        return cached

    tx = w3.eth.get_transaction(str(tx_hash))
    tx_json = _to_jsonable(tx)
    if not isinstance(tx_json, dict):
        raise RuntimeError(f'tx json is not dict: tx_hash={tx_hash} type={type(tx_json)}')
    _cache_put(
        namespace='w3_get_transaction',
        cache_key=cache_key,
        value=tx_json,
    )
    return tx_json


def _w3_get_transaction_receipt_cached(w3: Web3, tx_hash: str) -> Dict[str, Any]:
    cache_key = {
        'rpc_hint': str(_w3_rpc_hint(w3)),
        'tx_hash': str(tx_hash),
    }
    cached = _cache_get(
        namespace='w3_get_transaction_receipt',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        if not isinstance(cached, dict):
            raise RuntimeError(f'cached receipt is not dict: tx_hash={tx_hash} type={type(cached)}')
        return cached

    receipt = w3.eth.get_transaction_receipt(str(tx_hash))
    receipt_json = _to_jsonable(receipt)
    if not isinstance(receipt_json, dict):
        raise RuntimeError(f'receipt json is not dict: tx_hash={tx_hash} type={type(receipt_json)}')
    _cache_put(
        namespace='w3_get_transaction_receipt',
        cache_key=cache_key,
        value=receipt_json,
    )
    return receipt_json


def _w3_get_block_cached(w3: Web3, block_number: int) -> Dict[str, Any]:
    cache_key = {
        'rpc_hint': str(_w3_rpc_hint(w3)),
        'block_number': int(block_number),
    }
    cached = _cache_get(
        namespace='w3_get_block',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        if not isinstance(cached, dict):
            raise RuntimeError(f'cached block is not dict: block={block_number} type={type(cached)}')
        return cached

    block = w3.eth.get_block(int(block_number))
    block_json = _to_jsonable(block)
    if not isinstance(block_json, dict):
        raise RuntimeError(f'block json is not dict: block={block_number} type={type(block_json)}')
    _cache_put(
        namespace='w3_get_block',
        cache_key=cache_key,
        value=block_json,
    )
    return block_json


def _erc20_balance_of_cached(w3: Web3, token_address: str, wallet_address: str, block_number: int) -> int:
    cache_key = {
        'rpc_hint': str(_w3_rpc_hint(w3)),
        'token_address': str(Web3.to_checksum_address(token_address)),
        'wallet_address': str(Web3.to_checksum_address(wallet_address)),
        'block_number': int(block_number),
    }
    cached = _cache_get(
        namespace='erc20_balance_of',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        return int(cached)

    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    balance_raw = int(token.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call(block_identifier=int(block_number)))
    _cache_put(
        namespace='erc20_balance_of',
        cache_key=cache_key,
        value=int(balance_raw),
    )
    return int(balance_raw)


def _pool_price_weth_usdc_at_block_cached(
    pool_contract,
    pool_address: str,
    block_number: int,
    token0: TokenMeta,
    token1: TokenMeta,
    weth_token: TokenMeta,
) -> float:
    cache_key = {
        'pool_address': str(Web3.to_checksum_address(pool_address)),
        'block_number': int(block_number),
        'token0_address': str(Web3.to_checksum_address(token0.address)),
        'token1_address': str(Web3.to_checksum_address(token1.address)),
        'token0_decimals': int(token0.decimals),
        'token1_decimals': int(token1.decimals),
        'weth_address': str(Web3.to_checksum_address(weth_token.address)),
    }
    cached = _cache_get(
        namespace='pool_price_weth_usdc',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        price_cached = float(cached)
        if float(price_cached) <= 0:
            raise RuntimeError(f'cached pool price <= 0 at block={block_number}: {price_cached}')
        return float(price_cached)

    slot0 = pool_contract.functions.slot0().call(block_identifier=int(block_number))
    if not isinstance(slot0, (list, tuple)):
        raise RuntimeError(f'slot0 return type is invalid: block={block_number} type={type(slot0)}')
    if len(slot0) <= 0:
        raise RuntimeError(f'slot0 return is empty: block={block_number}')

    sqrt_price_x96 = int(slot0[0])
    if int(sqrt_price_x96) <= 0:
        raise RuntimeError(f'slot0 sqrtPriceX96 <= 0 at block={block_number}: {sqrt_price_x96}')

    token1_per_token0 = (float(sqrt_price_x96) / float(UNISWAP_V3_Q96)) ** 2
    token1_per_token0 = float(token1_per_token0) * float(10 ** (int(token0.decimals) - int(token1.decimals)))

    token0_lc = str(token0.address).lower()
    token1_lc = str(token1.address).lower()
    weth_lc = str(weth_token.address).lower()

    if str(token0_lc) == str(weth_lc):
        weth_price_usdc = float(token1_per_token0)
    elif str(token1_lc) == str(weth_lc):
        if float(token1_per_token0) <= 0.0:
            raise RuntimeError(f'token1_per_token0 <= 0 at block={block_number}: {token1_per_token0}')
        weth_price_usdc = 1.0 / float(token1_per_token0)
    else:
        raise RuntimeError(
            f'pool does not contain weth token: pool={pool_address} '
            f'token0={token0.address} token1={token1.address} weth={weth_token.address}'
        )

    if float(weth_price_usdc) <= 0.0:
        raise RuntimeError(f'weth_price_usdc <= 0 at block={block_number}: {weth_price_usdc}')

    _cache_put(
        namespace='pool_price_weth_usdc',
        cache_key=cache_key,
        value=float(weth_price_usdc),
    )
    return float(weth_price_usdc)


def _load_token_meta(w3: Web3, token_address: str) -> TokenMeta:
    rpc_hint = ''
    if hasattr(w3, 'provider') and hasattr(w3.provider, 'endpoint_uri'):
        rpc_hint = str(w3.provider.endpoint_uri)
    checksum = Web3.to_checksum_address(token_address)
    cache_key = {
        'rpc_hint': str(rpc_hint),
        'token_address': str(checksum),
    }
    cached = _cache_get(
        namespace='token_meta',
        cache_key=cache_key,
        ttl_seconds=None,
    )
    if cached is not None:
        if not isinstance(cached, dict):
            raise RuntimeError(f'cached token meta is not dict: {type(cached)}')
        if 'symbol' not in cached or 'decimals' not in cached:
            raise RuntimeError(f'cached token meta is malformed for {token_address}: {cached}')
        return TokenMeta(
            symbol=str(cached['symbol']),
            address=str(checksum),
            decimals=int(cached['decimals']),
        )

    token = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    symbol = token.functions.symbol().call()
    decimals = token.functions.decimals().call()

    if not isinstance(symbol, str):
        raise RuntimeError(f'token symbol is not str for {token_address}: {type(symbol)}')
    if not isinstance(decimals, int):
        raise RuntimeError(f'token decimals is not int for {token_address}: {type(decimals)}')
    if int(decimals) < 0:
        raise RuntimeError(f'token decimals is negative for {token_address}: {decimals}')

    meta = TokenMeta(
        symbol=str(symbol),
        address=str(checksum),
        decimals=int(decimals),
    )
    _cache_put(
        namespace='token_meta',
        cache_key=cache_key,
        value={
            'symbol': str(meta.symbol),
            'decimals': int(meta.decimals),
        },
    )
    return meta


def _load_pool_tokens(w3: Web3, pool_address: str) -> Tuple[TokenMeta, TokenMeta]:
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)
    token0 = pool.functions.token0().call()
    token1 = pool.functions.token1().call()
    meta0 = _load_token_meta(w3, str(token0))
    meta1 = _load_token_meta(w3, str(token1))
    return meta0, meta1


def _collect_candidate_hashes_alchemy(rpc_url: str, logger, wallet_address: str, weth_token: TokenMeta, usdc_token: TokenMeta, from_block: int, to_block: int, page_size: int) -> List[str]:
    t0 = time.perf_counter()
    from_block_hex = hex(int(from_block))
    to_block_hex = hex(int(to_block))
    hashes: Set[str] = set()

    logger.info(
        f'alchemy_collect_start wallet={Web3.to_checksum_address(str(wallet_address))} '
        f'from_block={from_block} to_block={to_block} page_size={page_size}'
    )

    token_list = [weth_token, usdc_token]
    for token in token_list:
        token_t0 = time.perf_counter()
        n_before = len(hashes)
        logger.info(f'alchemy_collect_token_start symbol={token.symbol} token={token.address}')
        from_items = _alchemy_list_transfers(
            rpc_url=str(rpc_url),
            logger=logger,
            wallet_address=str(wallet_address),
            token_address=str(token.address),
            from_block_hex=str(from_block_hex),
            to_block_hex=str(to_block_hex),
            page_size=int(page_size),
            direction='from',
        )
        to_items = _alchemy_list_transfers(
            rpc_url=str(rpc_url),
            logger=logger,
            wallet_address=str(wallet_address),
            token_address=str(token.address),
            from_block_hex=str(from_block_hex),
            to_block_hex=str(to_block_hex),
            page_size=int(page_size),
            direction='to',
        )

        for item in from_items:
            hashes.add(str(item.tx_hash))
        for item in to_items:
            hashes.add(str(item.tx_hash))

        logger.info(
            f'alchemy_collect_token_done symbol={token.symbol} token={token.address} '
            f'from_items={len(from_items)} to_items={len(to_items)} '
            f'unique_added={len(hashes) - n_before} unique_total={len(hashes)} '
            f'elapsed_sec={time.perf_counter() - token_t0:.2f}'
        )

    wallet_checksum = Web3.to_checksum_address(str(wallet_address))
    ext_t0 = time.perf_counter()
    n_before_ext = len(hashes)
    external_params = {
        'category': ['external'],
        'fromAddress': str(wallet_checksum),
        'fromBlock': str(from_block_hex),
        'toBlock': str(to_block_hex),
        'withMetadata': False,
        'excludeZeroValue': False,
        'maxCount': hex(int(page_size)),
    }
    external_items = _alchemy_list_tx_refs(
        rpc_url=str(rpc_url),
        logger=logger,
        params_obj=external_params,
        log_tag='alchemy_list_external_wallet_txs',
    )
    for item in external_items:
        hashes.add(str(item.tx_hash))

    logger.info(
        f'alchemy_collect_external_done items={len(external_items)} '
        f'unique_added={len(hashes) - n_before_ext} unique_total={len(hashes)} '
        f'elapsed_sec={time.perf_counter() - ext_t0:.2f}'
    )

    out = list(hashes)
    out.sort()
    logger.info(f'alchemy_candidate_hashes_loaded n={len(out)} elapsed_sec={time.perf_counter() - t0:.2f}')
    return out


def _build_history_tx(w3: Web3, tx_hash: str, block_ts_cache: Dict[int, int], wallet_lc: str, npm_lc: str, router_lc_set: Set[str], pool_lc: str, token0_lc: str, weth_token: TokenMeta, usdc_token: TokenMeta) -> Optional[HistoryTx]:
    tx = _w3_get_transaction_cached(w3=w3, tx_hash=str(tx_hash))
    receipt = _w3_get_transaction_receipt_cached(w3=w3, tx_hash=str(tx_hash))

    if 'blockNumber' not in receipt:
        raise RuntimeError(f'receipt has no blockNumber: {tx_hash}')
    block_number = int(receipt['blockNumber'])
    if 'transactionIndex' not in receipt:
        raise RuntimeError(f'receipt has no transactionIndex: {tx_hash}')
    tx_index = int(receipt['transactionIndex'])

    if int(block_number) not in block_ts_cache:
        block = _w3_get_block_cached(w3=w3, block_number=int(block_number))
        if 'timestamp' not in block:
            raise RuntimeError(f'block has no timestamp: tx={tx_hash} block={block_number}')
        block_ts_cache[int(block_number)] = int(block['timestamp']) * 1000

    timestamp_ms = int(block_ts_cache[int(block_number)])
    tx_to = tx['to']
    tx_to_lc = '' if tx_to is None else str(tx_to).lower()
    is_uniswap_swap = False

    gas_used = _hex_to_int(receipt['gasUsed'])
    gas_price = _hex_to_int(receipt['effectiveGasPrice'])
    gas_eth = float(int(gas_used) * int(gas_price)) / float(10 ** 18)

    weth_address_lc = str(weth_token.address).lower()
    weth_decimals = int(weth_token.decimals)
    usdc_decimals = int(usdc_token.decimals)

    weth_delta_raw = 0
    usdc_delta_raw = 0
    increase0_raw = 0
    increase1_raw = 0
    decrease0_raw = 0
    decrease1_raw = 0
    collect0_raw = 0
    collect1_raw = 0

    increase_moves: List[LpTokenMove] = []
    decrease_moves: List[LpTokenMove] = []
    collect_moves: List[LpTokenMove] = []

    if 'logs' not in receipt:
        raise RuntimeError(f'receipt has no logs: {tx_hash}')
    logs = receipt['logs']

    for log in logs:
        if 'address' not in log:
            raise RuntimeError(f'log has no address: {tx_hash}')
        if 'topics' not in log:
            raise RuntimeError(f'log has no topics: {tx_hash}')
        if 'data' not in log:
            raise RuntimeError(f'log has no data: {tx_hash}')

        topics = log['topics']
        if len(topics) == 0:
            continue

        log_addr_lc = str(log['address']).lower()
        topic0 = Web3.to_hex(topics[0]).lower()

        if str(topic0) == str(TRANSFER_TOPIC).lower():
            if len(topics) < 3:
                raise RuntimeError(f'transfer log has <3 topics: {tx_hash}')

            from_addr = _topic_to_address(Web3.to_hex(topics[1]))
            to_addr = _topic_to_address(Web3.to_hex(topics[2]))
            amount_raw = _hex_to_int(log['data'])

            if str(log_addr_lc) == str(weth_token.address).lower():
                if str(to_addr).lower() == str(wallet_lc):
                    weth_delta_raw += int(amount_raw)
                if str(from_addr).lower() == str(wallet_lc):
                    weth_delta_raw -= int(amount_raw)

            if str(log_addr_lc) == str(usdc_token.address).lower():
                if str(to_addr).lower() == str(wallet_lc):
                    usdc_delta_raw += int(amount_raw)
                if str(from_addr).lower() == str(wallet_lc):
                    usdc_delta_raw -= int(amount_raw)

        if str(log_addr_lc) == str(npm_lc):
            if str(topic0) == str(INCREASE_LIQ_TOPIC).lower():
                if len(topics) < 2:
                    raise RuntimeError(f'IncreaseLiquidity log has <2 topics: {tx_hash}')
                token_id = int(_topic_to_uint256(topics[1]))
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 3)
                liquidity_raw = int(vals[0])
                amount0_raw = int(vals[1])
                amount1_raw = int(vals[2])
                increase0_raw += int(amount0_raw)
                increase1_raw += int(amount1_raw)

                inc_weth_raw, inc_usdc_raw = _map_pool_amounts_to_weth_usdc(
                    amount0_raw=int(amount0_raw),
                    amount1_raw=int(amount1_raw),
                    token0_lc=str(token0_lc),
                    weth_address_lc=str(weth_address_lc),
                )
                increase_moves.append(
                    LpTokenMove(
                        token_id=int(token_id),
                        liquidity=int(liquidity_raw),
                        weth=float(_to_float_token(int(inc_weth_raw), int(weth_decimals))),
                        usdc=float(_to_float_token(int(inc_usdc_raw), int(usdc_decimals))),
                    )
                )

            if str(topic0) == str(DECREASE_LIQ_TOPIC).lower():
                if len(topics) < 2:
                    raise RuntimeError(f'DecreaseLiquidity log has <2 topics: {tx_hash}')
                token_id = int(_topic_to_uint256(topics[1]))
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 3)
                liquidity_raw = int(vals[0])
                amount0_raw = int(vals[1])
                amount1_raw = int(vals[2])
                decrease0_raw += int(amount0_raw)
                decrease1_raw += int(amount1_raw)

                dec_weth_raw, dec_usdc_raw = _map_pool_amounts_to_weth_usdc(
                    amount0_raw=int(amount0_raw),
                    amount1_raw=int(amount1_raw),
                    token0_lc=str(token0_lc),
                    weth_address_lc=str(weth_address_lc),
                )
                decrease_moves.append(
                    LpTokenMove(
                        token_id=int(token_id),
                        liquidity=int(liquidity_raw),
                        weth=float(_to_float_token(int(dec_weth_raw), int(weth_decimals))),
                        usdc=float(_to_float_token(int(dec_usdc_raw), int(usdc_decimals))),
                    )
                )

            if str(topic0) == str(COLLECT_TOPIC).lower():
                if len(topics) < 2:
                    raise RuntimeError(f'Collect log has <2 topics: {tx_hash}')
                token_id = int(_topic_to_uint256(topics[1]))
                # NPM Collect event data layout:
                # [recipient(address padded to 32 bytes), amount0(uint256), amount1(uint256)]
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 3)
                amount0_raw = int(vals[1])
                amount1_raw = int(vals[2])
                collect0_raw += int(amount0_raw)
                collect1_raw += int(amount1_raw)

                col_weth_raw, col_usdc_raw = _map_pool_amounts_to_weth_usdc(
                    amount0_raw=int(amount0_raw),
                    amount1_raw=int(amount1_raw),
                    token0_lc=str(token0_lc),
                    weth_address_lc=str(weth_address_lc),
                )
                collect_moves.append(
                    LpTokenMove(
                        token_id=int(token_id),
                        liquidity=0,
                        weth=float(_to_float_token(int(col_weth_raw), int(weth_decimals))),
                        usdc=float(_to_float_token(int(col_usdc_raw), int(usdc_decimals))),
                    )
                )

        if str(log_addr_lc) == str(pool_lc) and str(topic0) == str(POOL_SWAP_TOPIC).lower():
            is_uniswap_swap = True

    increase_weth_raw = int(increase0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(increase1_raw)
    increase_usdc_raw = int(increase1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(increase0_raw)
    decrease_weth_raw = int(decrease0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(decrease1_raw)
    decrease_usdc_raw = int(decrease1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(decrease0_raw)
    collect_weth_raw = int(collect0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(collect1_raw)
    collect_usdc_raw = int(collect1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(collect0_raw)

    # Wallet delta coming only from LP add/collect token transfers.
    lp_wallet_weth_raw = int(collect_weth_raw) - int(increase_weth_raw)
    lp_wallet_usdc_raw = int(collect_usdc_raw) - int(increase_usdc_raw)
    swap_weth_raw = int(weth_delta_raw) - int(lp_wallet_weth_raw)
    swap_usdc_raw = int(usdc_delta_raw) - int(lp_wallet_usdc_raw)

    has_add = int(increase_weth_raw) > 0 or int(increase_usdc_raw) > 0
    has_remove = int(decrease_weth_raw) > 0 or int(decrease_usdc_raw) > 0
    has_collect = int(collect_weth_raw) > 0 or int(collect_usdc_raw) > 0
    has_wallet_delta = int(weth_delta_raw) != 0 or int(usdc_delta_raw) != 0
    has_swap_delta = int(swap_weth_raw) != 0 or int(swap_usdc_raw) != 0

    if (not bool(is_uniswap_swap)) and str(tx_to_lc) in router_lc_set and bool(has_swap_delta):
        is_uniswap_swap = True

    if not bool(is_uniswap_swap):
        swap_weth_raw = 0
        swap_usdc_raw = 0
        has_swap_delta = False

    kind = TxKind.OTHER
    if bool(has_swap_delta):
        kind = TxKind.SWAP
    elif bool(has_add):
        kind = TxKind.ADD
    elif bool(has_remove):
        kind = TxKind.REMOVE
    elif bool(has_collect):
        kind = TxKind.COLLECT

    if (not bool(has_wallet_delta)) and (not bool(has_add)) and (not bool(has_remove)) and (not bool(has_collect)) and (not bool(has_swap_delta)):
        return None

    return HistoryTx(
        tx_hash=str(tx_hash),
        block_number=int(block_number),
        tx_index=int(tx_index),
        timestamp_ms=int(timestamp_ms),
        kind=kind,
        is_uniswap_swap=bool(is_uniswap_swap),
        gas_eth=float(gas_eth),
        weth_delta=float(_to_float_token(weth_delta_raw, weth_token.decimals)),
        usdc_delta=float(_to_float_token(usdc_delta_raw, usdc_token.decimals)),
        swap_weth=float(_to_float_token(swap_weth_raw, weth_token.decimals)),
        swap_usdc=float(_to_float_token(swap_usdc_raw, usdc_token.decimals)),
        increase_weth=float(_to_float_token(increase_weth_raw, weth_token.decimals)),
        increase_usdc=float(_to_float_token(increase_usdc_raw, usdc_token.decimals)),
        decrease_weth=float(_to_float_token(decrease_weth_raw, weth_token.decimals)),
        decrease_usdc=float(_to_float_token(decrease_usdc_raw, usdc_token.decimals)),
        collect_weth=float(_to_float_token(collect_weth_raw, weth_token.decimals)),
        collect_usdc=float(_to_float_token(collect_usdc_raw, usdc_token.decimals)),
        increase_moves=increase_moves,
        decrease_moves=decrease_moves,
        collect_moves=collect_moves,
    )


def _build_report(
    w3: Web3,
    args,
    logger,
    weth_token: TokenMeta,
    usdc_token: TokenMeta,
    token0: TokenMeta,
    token1: TokenMeta,
    tx_hashes: List[str],
    npm_address: str,
    router_set: Set[str],
    pool_address: str,
    eth_price: float,
    cutoff_started_at_ms: int,
    swap_cost_quote_sum: float,
    apr_capital_quote: float,
    apr_hold_time_seconds: float,
) -> HistoryReport:
    t_report_start = time.perf_counter()
    wallet_lc = str(args.wallet_address).lower()
    npm_lc = str(npm_address).lower()
    pool_lc = str(pool_address).lower()
    token0_lc = str(token0.address).lower()
    block_ts_cache: Dict[int, int] = {}
    txs: List[HistoryTx] = []
    total_hashes = len(tx_hashes)
    decode_step = 1 if total_hashes == 0 else max(1, min(500, int(total_hashes / 20)))

    logger.info(
        f'build_report_start wallet={Web3.to_checksum_address(str(args.wallet_address))} '
        f'tx_hashes_total={total_hashes} to_block={args.to_block}'
    )

    t_decode_start = time.perf_counter()
    for idx, tx_hash in enumerate(tx_hashes, start=1):
        tx_row = _build_history_tx(
            w3=w3,
            tx_hash=str(tx_hash),
            block_ts_cache=block_ts_cache,
            wallet_lc=str(wallet_lc),
            npm_lc=str(npm_lc),
            router_lc_set=router_set,
            pool_lc=str(pool_lc),
            token0_lc=str(token0_lc),
            weth_token=weth_token,
            usdc_token=usdc_token,
        )
        if tx_row is not None:
            txs.append(tx_row)

        if idx == 1 or idx == total_hashes or idx % decode_step == 0:
            elapsed = time.perf_counter() - t_decode_start
            speed = float(idx) / float(elapsed) if elapsed > 0 else 0.0
            logger.info(
                f'build_report_decode_progress processed={idx}/{total_hashes} '
                f'kept={len(txs)} elapsed_sec={elapsed:.2f} speed_txs_per_sec={speed:.2f}'
            )

    txs.sort(key=lambda x: (int(x.block_number), int(x.tx_index), str(x.tx_hash)))
    txs_before_cutoff = len(txs)
    txs = [tx for tx in txs if int(tx.timestamp_ms) >= int(cutoff_started_at_ms)]
    logger.info(
        f'history_cutoff_applied cutoff_started_at_ms={cutoff_started_at_ms} '
        f'kept={len(txs)} dropped={int(txs_before_cutoff) - len(txs)}'
    )
    logger.info(f'history_wallet_txs_loaded n={len(txs)} elapsed_sec={time.perf_counter() - t_decode_start:.2f}')

    first_tx_block = int(args.to_block) if len(txs) == 0 else int(txs[0].block_number)
    start_block = int(first_tx_block) - 1
    if int(start_block) < 0:
        start_block = 0

    pool_contract = w3.eth.contract(address=Web3.to_checksum_address(str(pool_address)), abi=POOL_ABI)
    pool_price_by_block: Dict[int, float] = {}
    wallet_checksum = Web3.to_checksum_address(str(args.wallet_address))

    start_weth_raw = _erc20_balance_of_cached(
        w3=w3,
        token_address=str(weth_token.address),
        wallet_address=str(wallet_checksum),
        block_number=int(start_block),
    )
    start_usdc_raw = _erc20_balance_of_cached(
        w3=w3,
        token_address=str(usdc_token.address),
        wallet_address=str(wallet_checksum),
        block_number=int(start_block),
    )
    end_weth_raw = _erc20_balance_of_cached(
        w3=w3,
        token_address=str(weth_token.address),
        wallet_address=str(wallet_checksum),
        block_number=int(args.to_block),
    )
    end_usdc_raw = _erc20_balance_of_cached(
        w3=w3,
        token_address=str(usdc_token.address),
        wallet_address=str(wallet_checksum),
        block_number=int(args.to_block),
    )

    start_weth = _to_float_token(start_weth_raw, weth_token.decimals)
    start_usdc = _to_float_token(start_usdc_raw, usdc_token.decimals)
    end_weth = _to_float_token(end_weth_raw, weth_token.decimals)
    end_usdc = _to_float_token(end_usdc_raw, usdc_token.decimals)

    delta_weth = float(end_weth) - float(start_weth)
    delta_usdc = float(end_usdc) - float(start_usdc)

    sum_swap_weth = 0.0
    sum_swap_usdc = 0.0
    sum_gas_eth = 0.0
    sum_fee_weth = 0.0
    sum_fee_usdc = 0.0
    realized_il_weth = 0.0
    realized_il_usdc = 0.0
    realized_il_quote_total = 0.0
    sum_closed_principal_weth = 0.0
    sum_closed_principal_usdc = 0.0
    sum_decrease_weth = 0.0
    sum_decrease_usdc = 0.0
    n_swaps = 0

    pending_principal_by_token: Dict[int, PendingPrincipal] = {}
    lp_positions_by_token: Dict[int, LpPositionState] = {}
    total_txs = len(txs)
    calc_step = 1 if total_txs == 0 else max(1, min(500, int(total_txs / 20)))
    inc_moves_seen = 0
    dec_moves_seen = 0
    col_moves_seen = 0

    t_calc_start = time.perf_counter()
    logger.info(f'build_report_calc_start txs_total={total_txs}')
    for tx_idx, tx in enumerate(txs, start=1):
        for inc_move in tx.increase_moves:
            inc_moves_seen += 1
            token_id = int(inc_move.token_id)
            if int(inc_move.liquidity) <= 0:
                raise RuntimeError(f'increase move has non-positive liquidity: token_id={token_id} liquidity={inc_move.liquidity}')

            prev = LpPositionState(
                liquidity_open=0,
                principal_weth_open=0.0,
                principal_usdc_open=0.0,
                start_value_target_quote_open=0.0,
            )
            if token_id in lp_positions_by_token:
                prev = lp_positions_by_token[token_id]

            tx_block = int(tx.block_number)
            if int(tx_block) not in pool_price_by_block:
                pool_price_by_block[int(tx_block)] = _pool_price_weth_usdc_at_block_cached(
                    pool_contract=pool_contract,
                    pool_address=str(pool_address),
                    block_number=int(tx_block),
                    token0=token0,
                    token1=token1,
                    weth_token=weth_token,
                )
            base_price_at_increase = float(pool_price_by_block[int(tx_block)])
            if float(base_price_at_increase) <= 0.0:
                raise RuntimeError(
                    f'base_price_at_increase <= 0 for token_id={token_id} tx={tx.tx_hash} '
                    f'block={tx_block} value={base_price_at_increase}'
                )
            inc_start_value_target_quote = (
                float(inc_move.weth) * float(base_price_at_increase) + float(inc_move.usdc)
            )
            if float(inc_start_value_target_quote) < 0.0:
                raise RuntimeError(
                    f'inc_start_value_target_quote < 0 for token_id={token_id} tx={tx.tx_hash}: '
                    f'{inc_start_value_target_quote}'
                )

            lp_positions_by_token[token_id] = LpPositionState(
                liquidity_open=int(prev.liquidity_open) + int(inc_move.liquidity),
                principal_weth_open=float(prev.principal_weth_open) + float(inc_move.weth),
                principal_usdc_open=float(prev.principal_usdc_open) + float(inc_move.usdc),
                start_value_target_quote_open=float(prev.start_value_target_quote_open) + float(inc_start_value_target_quote),
            )

        for dec_move in tx.decrease_moves:
            dec_moves_seen += 1
            token_id = int(dec_move.token_id)
            if token_id not in lp_positions_by_token:
                raise RuntimeError(f'decrease for unknown token_id={token_id}')

            prev = lp_positions_by_token[token_id]
            liq_open_before = int(prev.liquidity_open)
            if int(dec_move.liquidity) <= 0:
                raise RuntimeError(f'decrease move has non-positive liquidity: token_id={token_id} liquidity={dec_move.liquidity}')
            if int(liq_open_before) <= 0:
                raise RuntimeError(f'liquidity_open <= 0 before decrease: token_id={token_id} liquidity_open={liq_open_before}')
            if int(dec_move.liquidity) > int(liq_open_before):
                raise RuntimeError(
                    f'decrease liquidity exceeds open liquidity: token_id={token_id} '
                    f'decrease_liquidity={dec_move.liquidity} liquidity_open={liq_open_before}'
                )

            close_fraction = float(dec_move.liquidity) / float(liq_open_before)
            if float(close_fraction) <= 0.0:
                raise RuntimeError(f'close_fraction <= 0 for token_id={token_id}: {close_fraction}')
            if float(close_fraction) > 1.0 + 1e-12:
                raise RuntimeError(f'close_fraction > 1 for token_id={token_id}: {close_fraction}')
            if float(close_fraction) > 1.0:
                close_fraction = 1.0

            closed_principal_weth = float(prev.principal_weth_open) * float(close_fraction)
            closed_principal_usdc = float(prev.principal_usdc_open) * float(close_fraction)
            closed_start_value_target_quote = float(prev.start_value_target_quote_open) * float(close_fraction)

            if float(closed_principal_weth) > float(prev.principal_weth_open):
                closed_principal_weth = float(prev.principal_weth_open)
            if float(closed_principal_usdc) > float(prev.principal_usdc_open):
                closed_principal_usdc = float(prev.principal_usdc_open)
            if float(closed_start_value_target_quote) > float(prev.start_value_target_quote_open):
                closed_start_value_target_quote = float(prev.start_value_target_quote_open)

            tx_block = int(tx.block_number)
            if int(tx_block) not in pool_price_by_block:
                pool_price_by_block[int(tx_block)] = _pool_price_weth_usdc_at_block_cached(
                    pool_contract=pool_contract,
                    pool_address=str(pool_address),
                    block_number=int(tx_block),
                    token0=token0,
                    token1=token1,
                    weth_token=weth_token,
                )
            valuation_price_at_decrease = float(pool_price_by_block[int(tx_block)])
            if float(valuation_price_at_decrease) <= 0.0:
                raise RuntimeError(
                    f'valuation_price_at_decrease <= 0 for token_id={token_id} tx={tx.tx_hash} '
                    f'block={tx_block} value={valuation_price_at_decrease}'
                )
            step_exit_value = float(dec_move.weth) * float(valuation_price_at_decrease) + float(dec_move.usdc)
            step_realized_il_quote = float(step_exit_value) - float(closed_start_value_target_quote)
            realized_il_quote_total += float(step_realized_il_quote)

            realized_il_weth += float(dec_move.weth) - float(closed_principal_weth)
            realized_il_usdc += float(dec_move.usdc) - float(closed_principal_usdc)
            sum_closed_principal_weth += float(closed_principal_weth)
            sum_closed_principal_usdc += float(closed_principal_usdc)
            sum_decrease_weth += float(dec_move.weth)
            sum_decrease_usdc += float(dec_move.usdc)

            next_liquidity_open = int(prev.liquidity_open) - int(dec_move.liquidity)
            next_principal_weth_open = float(prev.principal_weth_open) - float(closed_principal_weth)
            next_principal_usdc_open = float(prev.principal_usdc_open) - float(closed_principal_usdc)
            next_start_value_target_quote_open = (
                float(prev.start_value_target_quote_open) - float(closed_start_value_target_quote)
            )

            if float(next_principal_weth_open) < -1e-12:
                raise RuntimeError(f'next_principal_weth_open < 0 for token_id={token_id}: {next_principal_weth_open}')
            if float(next_principal_usdc_open) < -1e-9:
                raise RuntimeError(f'next_principal_usdc_open < 0 for token_id={token_id}: {next_principal_usdc_open}')
            if float(next_start_value_target_quote_open) < -1e-9:
                raise RuntimeError(
                    f'next_start_value_target_quote_open < 0 for token_id={token_id}: {next_start_value_target_quote_open}'
                )

            if float(next_principal_weth_open) < 0.0:
                next_principal_weth_open = 0.0
            if float(next_principal_usdc_open) < 0.0:
                next_principal_usdc_open = 0.0
            if float(next_start_value_target_quote_open) < 0.0:
                next_start_value_target_quote_open = 0.0

            if int(next_liquidity_open) == 0:
                next_principal_weth_open = 0.0
                next_principal_usdc_open = 0.0
                next_start_value_target_quote_open = 0.0

            lp_positions_by_token[token_id] = LpPositionState(
                liquidity_open=int(next_liquidity_open),
                principal_weth_open=float(next_principal_weth_open),
                principal_usdc_open=float(next_principal_usdc_open),
                start_value_target_quote_open=float(next_start_value_target_quote_open),
            )

            pending_prev = PendingPrincipal(weth=0.0, usdc=0.0)
            if token_id in pending_principal_by_token:
                pending_prev = pending_principal_by_token[token_id]

            pending_principal_by_token[token_id] = PendingPrincipal(
                weth=float(pending_prev.weth) + float(dec_move.weth),
                usdc=float(pending_prev.usdc) + float(dec_move.usdc),
            )

        for col_move in tx.collect_moves:
            col_moves_seen += 1
            token_id = int(col_move.token_id)
            pending = PendingPrincipal(weth=0.0, usdc=0.0)
            if token_id in pending_principal_by_token:
                pending = pending_principal_by_token[token_id]

            principal_weth = float(pending.weth)
            if float(principal_weth) > float(col_move.weth):
                principal_weth = float(col_move.weth)

            principal_usdc = float(pending.usdc)
            if float(principal_usdc) > float(col_move.usdc):
                principal_usdc = float(col_move.usdc)

            fee_weth = float(col_move.weth) - float(principal_weth)
            fee_usdc = float(col_move.usdc) - float(principal_usdc)

            if float(fee_weth) < -1e-12:
                raise RuntimeError(f'fee_weth < 0 for token_id={token_id}: {fee_weth}')
            if float(fee_usdc) < -1e-12:
                raise RuntimeError(f'fee_usdc < 0 for token_id={token_id}: {fee_usdc}')

            if float(fee_weth) < 0.0:
                fee_weth = 0.0
            if float(fee_usdc) < 0.0:
                fee_usdc = 0.0

            pending_weth = float(pending.weth) - float(principal_weth)
            pending_usdc = float(pending.usdc) - float(principal_usdc)

            if float(pending_weth) < -1e-12:
                raise RuntimeError(f'pending_weth < 0 for token_id={token_id}: {pending_weth}')
            if float(pending_usdc) < -1e-9:
                raise RuntimeError(f'pending_usdc < 0 for token_id={token_id}: {pending_usdc}')

            if float(pending_weth) < 0.0:
                pending_weth = 0.0
            if float(pending_usdc) < 0.0:
                pending_usdc = 0.0

            pending_principal_by_token[token_id] = PendingPrincipal(
                weth=float(pending_weth),
                usdc=float(pending_usdc),
            )

            sum_fee_weth += float(fee_weth)
            sum_fee_usdc += float(fee_usdc)

        has_swap = abs(float(tx.swap_weth)) > 1e-18 or abs(float(tx.swap_usdc)) > 1e-9
        if bool(has_swap):
            n_swaps += 1
            sum_swap_weth += float(tx.swap_weth)
            sum_swap_usdc += float(tx.swap_usdc)

        if tx.kind in (TxKind.ADD, TxKind.REMOVE, TxKind.COLLECT, TxKind.SWAP):
            sum_gas_eth += float(tx.gas_eth)

        if tx_idx == 1 or tx_idx == total_txs or tx_idx % calc_step == 0:
            elapsed = time.perf_counter() - t_calc_start
            speed = float(tx_idx) / float(elapsed) if elapsed > 0 else 0.0
            logger.info(
                f'build_report_calc_progress processed={tx_idx}/{total_txs} '
                f'inc_moves={inc_moves_seen} dec_moves={dec_moves_seen} collect_moves={col_moves_seen} '
                f'swaps={n_swaps} open_lp_positions={len(lp_positions_by_token)} '
                f'elapsed_sec={elapsed:.2f} speed_txs_per_sec={speed:.2f}'
            )

    fees_weth = float(sum_fee_weth)
    fees_usdc = float(sum_fee_usdc)
    il_weth = float(realized_il_weth)
    il_usdc = float(realized_il_usdc)
    gas_usdc = float(sum_gas_eth) * float(eth_price)
    current_price = float(eth_price)
    sum_base_delta = float(sum_decrease_weth) - float(sum_closed_principal_weth)
    sum_quote_delta = float(sum_decrease_usdc) - float(sum_closed_principal_usdc)
    fees_quote = _to_quote_pnl(float(fees_weth), float(fees_usdc), float(current_price))
    il_quote = float(realized_il_quote_total)

    # Keep swap delta for diagnostics only.
    swaps_quote = _to_quote_pnl(float(sum_swap_weth), float(sum_swap_usdc), float(current_price))
    cex_quote = 0.0
    costs_pnl_quote = -(float(gas_usdc) + float(swap_cost_quote_sum))
    pnl_fees_quote = float(fees_quote)
    pnl_fees_il_quote = float(pnl_fees_quote) + float(il_quote)
    pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(costs_pnl_quote)
    pnl_fees_il_gas_cex_quote = float(pnl_fees_il_gas_quote) + float(cex_quote)
    pnl_without_hedge_quote = float(pnl_fees_il_gas_quote)
    pnl_with_hedge_quote = float(pnl_fees_il_gas_cex_quote)

    hold_time_seconds = float(apr_hold_time_seconds)
    capital_quote = float(apr_capital_quote)
    apr_fees_pct = _calc_apr(float(pnl_fees_quote), float(capital_quote), float(hold_time_seconds))
    apr_fees_il_pct = _calc_apr(float(pnl_fees_il_quote), float(capital_quote), float(hold_time_seconds))
    apr_fees_il_gas_pct = _calc_apr(float(pnl_fees_il_gas_quote), float(capital_quote), float(hold_time_seconds))
    apr_fees_il_gas_cex_pct = _calc_apr(float(pnl_fees_il_gas_cex_quote), float(capital_quote), float(hold_time_seconds))
    apr_with_hedge_pct = float(apr_fees_il_gas_cex_pct)
    apr_without_hedge_pct = float(apr_fees_il_gas_pct)

    pnl_by_components = (
        float(pnl_with_hedge_quote)
    )
    pnl_by_balances = (
        (float(end_usdc) + float(end_weth) * float(current_price))
        - (float(start_usdc) + float(start_weth) * float(current_price))
    )

    logger.info(
        f'build_report_done txs={len(txs)} swaps={n_swaps} '
        f'fees_weth={fees_weth:.8f} fees_usdc={fees_usdc:.8f} '
        f'il_weth={il_weth:.8f} il_usdc={il_usdc:.8f} '
        f'fees_quote={pnl_fees_quote:.8f} il_quote={il_quote:.8f} '
        f'gas_usdc={gas_usdc:.8f} swap_cost_quote={float(swap_cost_quote_sum):.8f} costs_pnl_quote={costs_pnl_quote:.8f} '
        f'pnl_without_hedge={pnl_without_hedge_quote:.8f} pnl_with_hedge={pnl_with_hedge_quote:.8f} '
        f'pnl_components_usdc={pnl_by_components:.8f} pnl_balances_usdc={pnl_by_balances:.8f} '
        f'elapsed_sec={time.perf_counter() - t_report_start:.2f}'
    )

    return HistoryReport(
        wallet=Web3.to_checksum_address(str(args.wallet_address)),
        network=str(args.network),
        from_block=int(start_block),
        to_block=int(args.to_block),
        eth_price_usdt=float(eth_price),
        balances=BalanceSnapshot(
            start_weth=float(start_weth),
            start_usdc=float(start_usdc),
            end_weth=float(end_weth),
            end_usdc=float(end_usdc),
            delta_weth=float(delta_weth),
            delta_usdc=float(delta_usdc),
        ),
        pnl=PnlBreakdown(
            fees=TokenDelta(weth=float(fees_weth), usdc=float(fees_usdc)),
            realized_il=TokenDelta(weth=float(il_weth), usdc=float(il_usdc)),
            swaps=TokenDelta(weth=float(sum_swap_weth), usdc=float(sum_swap_usdc)),
            gas_eth=float(sum_gas_eth),
            gas_usdc=float(gas_usdc),
            current_price=float(current_price),
            sum_base_delta=float(sum_base_delta),
            sum_quote_delta=float(sum_quote_delta),
            il_quote=float(il_quote),
            costs_pnl_quote=float(costs_pnl_quote),
            cex_quote=float(cex_quote),
            pnl_fees_quote=float(pnl_fees_quote),
            pnl_fees_il_quote=float(pnl_fees_il_quote),
            pnl_fees_il_gas_quote=float(pnl_fees_il_gas_quote),
            pnl_fees_il_gas_cex_quote=float(pnl_fees_il_gas_cex_quote),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            hold_time_seconds=float(hold_time_seconds),
            capital_quote=float(capital_quote),
            apr_fees_pct=float(apr_fees_pct),
            apr_fees_il_pct=float(apr_fees_il_pct),
            apr_fees_il_gas_pct=float(apr_fees_il_gas_pct),
            apr_fees_il_gas_cex_pct=float(apr_fees_il_gas_cex_pct),
            apr_with_hedge_pct=float(apr_with_hedge_pct),
            apr_without_hedge_pct=float(apr_without_hedge_pct),
            pnl_usdc_by_components=float(pnl_by_components),
        ),
        pnl_usdc_by_balances=float(pnl_by_balances),
        transactions_total=len(txs),
        uniswap_swaps_total=int(n_swaps),
        analyzed_transactions=txs,
    )


### Main ###
def main() -> None:
    args = parse_args()
    logger = get_logger('history_cli')
    t_main_start = time.perf_counter()

    if not isinstance(args.rpc_url, str) or len(args.rpc_url) == 0:
        raise RuntimeError('rpc_url is empty')
    if not isinstance(args.network, str) or len(args.network) == 0:
        raise RuntimeError('network is empty')
    if not isinstance(args.wallet_address, str) or len(args.wallet_address) == 0:
        raise RuntimeError('wallet_address is empty')
    if not isinstance(args.pool_address, str) or len(args.pool_address) == 0:
        raise RuntimeError('pool_address is empty')
    if int(args.alchemy_page_size) <= 0:
        raise RuntimeError('alchemy_page_size must be > 0')
    if not isinstance(args.mongo_uri, str) or len(args.mongo_uri) == 0:
        raise RuntimeError('mongo_uri is empty')
    if not isinstance(args.mongo_db, str) or len(args.mongo_db) == 0:
        raise RuntimeError('mongo_db is empty')

    network_lc = str(args.network).lower()
    if network_lc not in NETWORK_NPM:
        raise RuntimeError(f'unsupported network for npm lookup: {args.network}')
    if network_lc not in NETWORK_UNISWAP_ROUTERS:
        raise RuntimeError(f'unsupported network for router lookup: {args.network}')

    w3 = Web3(Web3.HTTPProvider(str(args.rpc_url)))
    if not bool(w3.is_connected()):
        raise RuntimeError('rpc connection failed')

    latest_block = int(w3.eth.block_number)
    if args.to_block is None:
        args.to_block = int(latest_block)
    if int(args.to_block) > int(latest_block):
        raise RuntimeError(f'to_block is above latest block: to_block={args.to_block} latest={latest_block}')

    list_from_block = 0
    logger.info(
        f'cli_args network={args.network} wallet={args.wallet_address} pool={args.pool_address} '
        f'from_block={list_from_block} to_block={args.to_block} page_size={args.alchemy_page_size}'
    )

    # Validate alchemy endpoint explicitly before work.
    t_probe_start = time.perf_counter()
    probe_result = _rpc_call(
        rpc_url=str(args.rpc_url),
        method='alchemy_getAssetTransfers',
        params=[{
            'category': ['erc20'],
            'fromBlock': hex(int(args.to_block)),
            'toBlock': hex(int(args.to_block)),
            'maxCount': hex(1),
            'excludeZeroValue': False,
            'withMetadata': False,
            'fromAddress': Web3.to_checksum_address(str(args.wallet_address)),
        }],
        request_id=777,
    )
    if not isinstance(probe_result, dict):
        raise RuntimeError(f'alchemy probe result is not dict: {type(probe_result)}')
    logger.info(f'alchemy_probe_ok elapsed_sec={time.perf_counter() - t_probe_start:.2f}')

    wallet_checksum = Web3.to_checksum_address(str(args.wallet_address))
    pool_checksum = Web3.to_checksum_address(str(args.pool_address))
    logger.info(f'history_start wallet={wallet_checksum} pool={pool_checksum} to_block={args.to_block}')

    token0, token1 = _load_pool_tokens(w3, str(pool_checksum))
    token0_symbol = str(token0.symbol).upper()
    token1_symbol = str(token1.symbol).upper()

    if token0_symbol == 'WETH':
        weth_token = token0
    elif token1_symbol == 'WETH':
        weth_token = token1
    else:
        raise RuntimeError(f'pool does not contain WETH: token0={token0.symbol} token1={token1.symbol}')

    if token0_symbol == 'USDC':
        usdc_token = token0
    elif token1_symbol == 'USDC':
        usdc_token = token1
    else:
        raise RuntimeError(f'pool does not contain USDC: token0={token0.symbol} token1={token1.symbol}')

    if str(weth_token.address).lower() == str(usdc_token.address).lower():
        raise RuntimeError('weth and usdc token addresses are the same')

    router_lc_set: Set[str] = set()
    router_addresses = NETWORK_UNISWAP_ROUTERS[network_lc]
    for addr in router_addresses:
        router_lc_set.add(str(Web3.to_checksum_address(addr)).lower())

    npm_address = Web3.to_checksum_address(NETWORK_NPM[network_lc])
    logger.info(f'history_addresses weth={weth_token.address} usdc={usdc_token.address} npm={npm_address}')

    t_hashes_start = time.perf_counter()
    tx_hashes = _collect_candidate_hashes_alchemy(
        rpc_url=str(args.rpc_url),
        logger=logger,
        wallet_address=str(wallet_checksum),
        weth_token=weth_token,
        usdc_token=usdc_token,
        from_block=int(list_from_block),
        to_block=int(args.to_block),
        page_size=int(args.alchemy_page_size),
    )
    logger.info(f'candidate_hashes_ready n={len(tx_hashes)} elapsed_sec={time.perf_counter() - t_hashes_start:.2f}')

    t_price_start = time.perf_counter()
    eth_price = _get_eth_price_usdt(str(args.binance_symbol))
    logger.info(f'binance_price_loaded symbol={args.binance_symbol} price={eth_price:.8f} elapsed_sec={time.perf_counter() - t_price_start:.2f}')

    t_cutoff_start = time.perf_counter()
    first_position_started_at_ms = _load_first_position_started_at_ms(
        mongo_uri=str(args.mongo_uri),
        mongo_db=str(args.mongo_db),
    )
    logger.info(
        f'first_position_started_at_loaded ms={first_position_started_at_ms} '
        f'elapsed_sec={time.perf_counter() - t_cutoff_start:.2f}'
    )

    t_swap_cost_start = time.perf_counter()
    swap_cost_quote_sum = _load_swap_cost_quote_sum(
        mongo_uri=str(args.mongo_uri),
        mongo_db=str(args.mongo_db),
        cutoff_started_at_ms=int(first_position_started_at_ms),
    )
    logger.info(
        f'swap_cost_quote_loaded sum={float(swap_cost_quote_sum):.8f} '
        f'elapsed_sec={time.perf_counter() - t_swap_cost_start:.2f}'
    )

    t_apr_basis_start = time.perf_counter()
    apr_capital_quote, apr_hold_time_seconds = _load_dashboard_apr_basis(
        mongo_uri=str(args.mongo_uri),
        mongo_db=str(args.mongo_db),
        cutoff_started_at_ms=int(first_position_started_at_ms),
    )
    logger.info(
        f'apr_basis_loaded capital_quote={float(apr_capital_quote):.8f} hold_time_seconds={float(apr_hold_time_seconds):.8f} '
        f'elapsed_sec={time.perf_counter() - t_apr_basis_start:.2f}'
    )

    t_build_start = time.perf_counter()
    report = _build_report(
        w3=w3,
        args=args,
        logger=logger,
        weth_token=weth_token,
        usdc_token=usdc_token,
        token0=token0,
        token1=token1,
        tx_hashes=tx_hashes,
        npm_address=str(npm_address),
        router_set=router_lc_set,
        pool_address=str(pool_checksum),
        eth_price=float(eth_price),
        cutoff_started_at_ms=int(first_position_started_at_ms),
        swap_cost_quote_sum=float(swap_cost_quote_sum),
        apr_capital_quote=float(apr_capital_quote),
        apr_hold_time_seconds=float(apr_hold_time_seconds),
    )
    logger.info(
        f'report_ready txs={report.transactions_total} swaps={report.uniswap_swaps_total} '
        f'elapsed_sec={time.perf_counter() - t_build_start:.2f} total_elapsed_sec={time.perf_counter() - t_main_start:.2f}'
    )

    print('=== WALLET HISTORY REPORT ===')
    print(f'Wallet: {report.wallet}')
    print(f'Network: {report.network}')
    print(f'Blocks: {report.from_block}..{report.to_block}')
    print(f'ETH price ({args.binance_symbol}): {report.eth_price_usdt:.8f}')
    print(f'Transactions analyzed: {report.transactions_total}')
    print(f'Uniswap swaps: {report.uniswap_swaps_total}')
    print('')

    print('=== TRANSACTIONS ===')
    for idx, tx in enumerate(report.analyzed_transactions, start=1):
        print(f'[{idx}] block={tx.block_number} tx_index={tx.tx_index} ts_ms={tx.timestamp_ms} kind={tx.kind.value} swap={int(tx.is_uniswap_swap)}')
        print(f'    tx={tx.tx_hash}')
        print(f'    delta:   WETH={tx.weth_delta:.8f} USDC={tx.usdc_delta:.8f}')
        print(f'    swap:    WETH={tx.swap_weth:.8f} USDC={tx.swap_usdc:.8f}')
        print(f'    pool io: in(WETH={tx.increase_weth:.8f}, USDC={tx.increase_usdc:.8f}) out(WETH={tx.decrease_weth:.8f}, USDC={tx.decrease_usdc:.8f}) collect(WETH={tx.collect_weth:.8f}, USDC={tx.collect_usdc:.8f})')
        print(f'    gas:     ETH={tx.gas_eth:.8f} USDC={float(tx.gas_eth) * float(report.eth_price_usdt):.8f}')
        if tx.kind == TxKind.SWAP:
            tx_pnl_usdc = float(tx.swap_usdc) + float(tx.swap_weth) * float(report.eth_price_usdt) - float(tx.gas_eth) * float(report.eth_price_usdt)
            print(f'    tx pnl (USDC, swap only): {tx_pnl_usdc:.8f}')
        else:
            print('    tx pnl: N/A (not a swap; inventory move/event)')
        print('')

    print('=== SUMMARY ===')
    print(f'WETH start/end: {report.balances.start_weth:.8f} -> {report.balances.end_weth:.8f} (delta {report.balances.delta_weth:.8f})')
    print(f'USDC start/end: {report.balances.start_usdc:.8f} -> {report.balances.end_usdc:.8f} (delta {report.balances.delta_usdc:.8f})')
    print('')
    realized_il_pnl_usdc = float(report.pnl.il_quote)
    fees_pnl_usdc = _to_quote_pnl(
        base_delta=float(report.pnl.fees.weth),
        quote_delta=float(report.pnl.fees.usdc),
        base_price=float(report.eth_price_usdt),
    )
    print(f'Fees from pool: WETH={report.pnl.fees.weth:.8f}, USDC={report.pnl.fees.usdc:.8f}, PnL(USDC)={fees_pnl_usdc:.8f}')
    print(
        f'Realized IL:    quote={realized_il_pnl_usdc:.8f} '
        f'(diag token delta: WETH={report.pnl.realized_il.weth:.8f}, USDC={report.pnl.realized_il.usdc:.8f})'
    )
    print(f'Uniswap swaps (diag only): WETH={report.pnl.swaps.weth:.8f}, USDC={report.pnl.swaps.usdc:.8f}')
    print(f'Gas (add/remove/collect/rebalance): ETH={report.pnl.gas_eth:.8f}, USDC={report.pnl.gas_usdc:.8f}')
    print(f'Swap costs (from backend_hedger_runs): USDC={float(swap_cost_quote_sum):.8f}')
    print('')
    print('Frontend-compatible chain:')
    print(f'  current_price:              {report.pnl.current_price:.8f} (текущая цена для оценки fees/swap/gas)')
    print(f'  sum_base_delta:             {report.pnl.sum_base_delta:.8f} (diag: Σ(decrease_base - allocated_mint_base))')
    print(f'  sum_quote_delta:            {report.pnl.sum_quote_delta:.8f} (diag: Σ(decrease_quote - allocated_mint_quote))')
    print(f'  il_quote:                   {report.pnl.il_quote:.8f} (Σ(step_exit_value - start_value_target) по decrease-событиям)')
    print(f'  pnl_fees_quote:             {report.pnl.pnl_fees_quote:.8f} (PnL_fees = Σ fees, только чистые комиссии)')
    print(f'  pnl_fees_il_quote:          {report.pnl.pnl_fees_il_quote:.8f} (PnL_fees_il = pnl_fees_quote + il_quote)')
    print(f'  costs_pnl_quote (signed):   {report.pnl.costs_pnl_quote:.8f} (Σ costs как signed PnL-компонент издержек)')
    print(f'  pnl_fees_il_gas_quote:      {report.pnl.pnl_fees_il_gas_quote:.8f} (PnL_fees_il_gas = pnl_fees_il_quote + costs_pnl_quote)')
    print(f'  cex_quote:                  {report.pnl.cex_quote:.8f} (Σ cex, PnL хеджа)')
    print(f'  pnl_fees_il_gas_cex_quote:  {report.pnl.pnl_fees_il_gas_cex_quote:.8f} (PnL_with_hedge = pnl_fees_il_gas_quote + cex_quote)')
    print(f'  pnl_without_hedge_quote:    {report.pnl.pnl_without_hedge_quote:.8f} (итог без хеджа, равен pnl_fees_il_gas_quote)')
    print(f'  pnl_with_hedge_quote:       {report.pnl.pnl_with_hedge_quote:.8f} (итог с хеджем, равен pnl_fees_il_gas_cex_quote)')
    print(f'  capital_quote:              {report.pnl.capital_quote:.8f} (рабочий капитал в quote для APR, max(total_quote) по run)')
    print(f'  hold_time_seconds:          {report.pnl.hold_time_seconds:.8f} (суммарное время удержания по итерациям для APR, сек)')
    print(f'  apr_fees_pct:               {report.pnl.apr_fees_pct:.8f} (APR(pnl_fees_quote, capital_quote, hold_time_seconds))')
    print(f'  apr_fees_il_pct:            {report.pnl.apr_fees_il_pct:.8f} (APR(pnl_fees_il_quote, capital_quote, hold_time_seconds))')
    print(f'  apr_fees_il_gas_pct:        {report.pnl.apr_fees_il_gas_pct:.8f} (APR(pnl_fees_il_gas_quote, capital_quote, hold_time_seconds))')
    print(f'  apr_fees_il_gas_cex_pct:    {report.pnl.apr_fees_il_gas_cex_pct:.8f} (APR(pnl_fees_il_gas_cex_quote, capital_quote, hold_time_seconds))')
    print(f'  apr_without_hedge_pct:      {report.pnl.apr_without_hedge_pct:.8f} (APR(pnl_without_hedge_quote, capital_quote, hold_time_seconds))')
    print(f'  apr_with_hedge_pct:         {report.pnl.apr_with_hedge_pct:.8f} (APR(pnl_with_hedge_quote, capital_quote, hold_time_seconds))')
    print('')
    print(f'PnL by components (USDC): {report.pnl.pnl_usdc_by_components:.8f}')
    print(f'PnL by balances   (USDC): {report.pnl_usdc_by_balances:.8f}')


if __name__ == '__main__':
    main()
