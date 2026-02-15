"""
Wallet on-chain history and realized PnL report
Date: 2026-02-15
Version: 1.1
"""
import argparse, requests
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from web3 import Web3

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

TRANSFER_TOPIC = Web3.keccak(text='Transfer(address,address,uint256)').hex()
INCREASE_LIQ_TOPIC = Web3.keccak(text='IncreaseLiquidity(uint256,uint128,uint256,uint256)').hex()
DECREASE_LIQ_TOPIC = Web3.keccak(text='DecreaseLiquidity(uint256,uint128,uint256,uint256)').hex()
COLLECT_TOPIC = Web3.keccak(text='Collect(uint256,address,uint256,uint256)').hex()


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


class HistoryTx(StrictModel):
    tx_hash: str
    block_number: int
    timestamp_ms: int
    kind: TxKind
    is_uniswap_swap: bool
    gas_eth: float
    weth_delta: float
    usdc_delta: float
    increase_weth: float
    increase_usdc: float
    decrease_weth: float
    decrease_usdc: float
    collect_weth: float
    collect_usdc: float


class PnlBreakdown(StrictModel):
    fees: TokenDelta
    realized_il: TokenDelta
    swaps: TokenDelta
    gas_eth: float
    gas_usdc: float
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

    return p.parse_args()


### Helpers ###
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


def _topic_to_address(topic_hex: str) -> str:
    if not isinstance(topic_hex, str):
        raise RuntimeError(f'topic is not str: {type(topic_hex)}')
    if not topic_hex.startswith('0x'):
        raise RuntimeError(f'topic has no 0x prefix: {topic_hex}')
    if len(topic_hex) != 66:
        raise RuntimeError(f'topic length is invalid: {topic_hex}')

    return Web3.to_checksum_address('0x' + topic_hex[-40:])


def _rpc_call(rpc_url: str, method: str, params: List, request_id: int):
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

    return data['result']


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


def _get_eth_price_usdt(symbol: str) -> float:
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

    return float(price)


def _load_token_meta(w3: Web3, token_address: str) -> TokenMeta:
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    symbol = token.functions.symbol().call()
    decimals = token.functions.decimals().call()

    if not isinstance(symbol, str):
        raise RuntimeError(f'token symbol is not str for {token_address}: {type(symbol)}')
    if not isinstance(decimals, int):
        raise RuntimeError(f'token decimals is not int for {token_address}: {type(decimals)}')
    if int(decimals) < 0:
        raise RuntimeError(f'token decimals is negative for {token_address}: {decimals}')

    return TokenMeta(
        symbol=str(symbol),
        address=Web3.to_checksum_address(token_address),
        decimals=int(decimals),
    )


def _load_pool_tokens(w3: Web3, pool_address: str) -> Tuple[TokenMeta, TokenMeta]:
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)
    token0 = pool.functions.token0().call()
    token1 = pool.functions.token1().call()
    meta0 = _load_token_meta(w3, str(token0))
    meta1 = _load_token_meta(w3, str(token1))
    return meta0, meta1


def _collect_candidate_hashes_alchemy(rpc_url: str, logger, wallet_address: str, weth_token: TokenMeta, usdc_token: TokenMeta, from_block: int, to_block: int, page_size: int) -> List[str]:
    from_block_hex = hex(int(from_block))
    to_block_hex = hex(int(to_block))
    hashes: Set[str] = set()

    token_list = [weth_token, usdc_token]
    for token in token_list:
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

    out = list(hashes)
    out.sort()
    logger.info(f'alchemy_candidate_hashes_loaded n={len(out)}')
    return out


def _build_history_tx(w3: Web3, tx_hash: str, block_ts_cache: Dict[int, int], wallet_lc: str, npm_lc: str, router_lc_set: Set[str], token0_lc: str, weth_token: TokenMeta, usdc_token: TokenMeta) -> Optional[HistoryTx]:
    tx = w3.eth.get_transaction(str(tx_hash))
    receipt = w3.eth.get_transaction_receipt(str(tx_hash))

    if 'from' not in tx:
        raise RuntimeError(f'tx has no from field: {tx_hash}')

    if str(tx['from']).lower() != str(wallet_lc):
        return None

    if 'blockNumber' not in receipt:
        raise RuntimeError(f'receipt has no blockNumber: {tx_hash}')
    block_number = int(receipt['blockNumber'])

    if int(block_number) not in block_ts_cache:
        block = w3.eth.get_block(int(block_number))
        if 'timestamp' not in block:
            raise RuntimeError(f'block has no timestamp: tx={tx_hash} block={block_number}')
        block_ts_cache[int(block_number)] = int(block['timestamp']) * 1000

    timestamp_ms = int(block_ts_cache[int(block_number)])
    tx_to = tx['to']
    tx_to_lc = '' if tx_to is None else str(tx_to).lower()
    is_uniswap_swap = str(tx_to_lc) in router_lc_set

    gas_used = _hex_to_int(receipt['gasUsed'])
    gas_price = _hex_to_int(receipt['effectiveGasPrice'])
    gas_eth = float(int(gas_used) * int(gas_price)) / float(10 ** 18)

    weth_delta_raw = 0
    usdc_delta_raw = 0
    increase0_raw = 0
    increase1_raw = 0
    decrease0_raw = 0
    decrease1_raw = 0
    collect0_raw = 0
    collect1_raw = 0

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
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 3)
                increase0_raw += int(vals[1])
                increase1_raw += int(vals[2])

            if str(topic0) == str(DECREASE_LIQ_TOPIC).lower():
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 3)
                decrease0_raw += int(vals[1])
                decrease1_raw += int(vals[2])

            if str(topic0) == str(COLLECT_TOPIC).lower():
                vals = _decode_uints_from_data(Web3.to_hex(log['data']), 2)
                collect0_raw += int(vals[0])
                collect1_raw += int(vals[1])

    increase_weth_raw = int(increase0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(increase1_raw)
    increase_usdc_raw = int(increase1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(increase0_raw)
    decrease_weth_raw = int(decrease0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(decrease1_raw)
    decrease_usdc_raw = int(decrease1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(decrease0_raw)
    collect_weth_raw = int(collect0_raw) if str(token0_lc) == str(weth_token.address).lower() else int(collect1_raw)
    collect_usdc_raw = int(collect1_raw) if str(token0_lc) == str(weth_token.address).lower() else int(collect0_raw)

    has_add = int(increase_weth_raw) > 0 or int(increase_usdc_raw) > 0
    has_remove = int(decrease_weth_raw) > 0 or int(decrease_usdc_raw) > 0
    has_collect = int(collect_weth_raw) > 0 or int(collect_usdc_raw) > 0

    kind = TxKind.OTHER
    if bool(is_uniswap_swap):
        kind = TxKind.SWAP
    elif bool(has_add):
        kind = TxKind.ADD
    elif bool(has_remove):
        kind = TxKind.REMOVE
    elif bool(has_collect):
        kind = TxKind.COLLECT

    return HistoryTx(
        tx_hash=str(tx_hash),
        block_number=int(block_number),
        timestamp_ms=int(timestamp_ms),
        kind=kind,
        is_uniswap_swap=bool(is_uniswap_swap),
        gas_eth=float(gas_eth),
        weth_delta=float(_to_float_token(weth_delta_raw, weth_token.decimals)),
        usdc_delta=float(_to_float_token(usdc_delta_raw, usdc_token.decimals)),
        increase_weth=float(_to_float_token(increase_weth_raw, weth_token.decimals)),
        increase_usdc=float(_to_float_token(increase_usdc_raw, usdc_token.decimals)),
        decrease_weth=float(_to_float_token(decrease_weth_raw, weth_token.decimals)),
        decrease_usdc=float(_to_float_token(decrease_usdc_raw, usdc_token.decimals)),
        collect_weth=float(_to_float_token(collect_weth_raw, weth_token.decimals)),
        collect_usdc=float(_to_float_token(collect_usdc_raw, usdc_token.decimals)),
    )


def _build_report(w3: Web3, args, logger, weth_token: TokenMeta, usdc_token: TokenMeta, tx_hashes: List[str], npm_address: str, router_set: Set[str], token0_address: str, eth_price: float) -> HistoryReport:
    wallet_lc = str(args.wallet_address).lower()
    npm_lc = str(npm_address).lower()
    token0_lc = str(token0_address).lower()
    block_ts_cache: Dict[int, int] = {}
    txs: List[HistoryTx] = []

    for tx_hash in tx_hashes:
        tx_row = _build_history_tx(
            w3=w3,
            tx_hash=str(tx_hash),
            block_ts_cache=block_ts_cache,
            wallet_lc=str(wallet_lc),
            npm_lc=str(npm_lc),
            router_lc_set=router_set,
            token0_lc=str(token0_lc),
            weth_token=weth_token,
            usdc_token=usdc_token,
        )
        if tx_row is not None:
            txs.append(tx_row)

    txs.sort(key=lambda x: (int(x.block_number), int(x.timestamp_ms), str(x.tx_hash)))
    logger.info(f'history_wallet_txs_loaded n={len(txs)}')

    first_tx_block = int(args.to_block) if len(txs) == 0 else int(txs[0].block_number)
    start_block = int(first_tx_block) - 1
    if int(start_block) < 0:
        start_block = 0

    weth_contract = w3.eth.contract(address=Web3.to_checksum_address(str(weth_token.address)), abi=ERC20_ABI)
    usdc_contract = w3.eth.contract(address=Web3.to_checksum_address(str(usdc_token.address)), abi=ERC20_ABI)
    wallet_checksum = Web3.to_checksum_address(str(args.wallet_address))

    start_weth_raw = int(weth_contract.functions.balanceOf(wallet_checksum).call(block_identifier=int(start_block)))
    start_usdc_raw = int(usdc_contract.functions.balanceOf(wallet_checksum).call(block_identifier=int(start_block)))
    end_weth_raw = int(weth_contract.functions.balanceOf(wallet_checksum).call(block_identifier=int(args.to_block)))
    end_usdc_raw = int(usdc_contract.functions.balanceOf(wallet_checksum).call(block_identifier=int(args.to_block)))

    start_weth = _to_float_token(start_weth_raw, weth_token.decimals)
    start_usdc = _to_float_token(start_usdc_raw, usdc_token.decimals)
    end_weth = _to_float_token(end_weth_raw, weth_token.decimals)
    end_usdc = _to_float_token(end_usdc_raw, usdc_token.decimals)

    delta_weth = float(end_weth) - float(start_weth)
    delta_usdc = float(end_usdc) - float(start_usdc)

    sum_increase_weth = 0.0
    sum_increase_usdc = 0.0
    sum_decrease_weth = 0.0
    sum_decrease_usdc = 0.0
    sum_collect_weth = 0.0
    sum_collect_usdc = 0.0
    sum_swap_weth = 0.0
    sum_swap_usdc = 0.0
    sum_gas_eth = 0.0
    n_swaps = 0

    for tx in txs:
        sum_increase_weth += float(tx.increase_weth)
        sum_increase_usdc += float(tx.increase_usdc)
        sum_decrease_weth += float(tx.decrease_weth)
        sum_decrease_usdc += float(tx.decrease_usdc)
        sum_collect_weth += float(tx.collect_weth)
        sum_collect_usdc += float(tx.collect_usdc)

        if tx.kind == TxKind.SWAP:
            n_swaps += 1
            sum_swap_weth += float(tx.weth_delta)
            sum_swap_usdc += float(tx.usdc_delta)

        if tx.kind == TxKind.ADD or tx.kind == TxKind.REMOVE or tx.kind == TxKind.SWAP:
            sum_gas_eth += float(tx.gas_eth)

    fees_weth = float(sum_collect_weth) - float(sum_decrease_weth)
    fees_usdc = float(sum_collect_usdc) - float(sum_decrease_usdc)
    il_weth = float(sum_decrease_weth) - float(sum_increase_weth)
    il_usdc = float(sum_decrease_usdc) - float(sum_increase_usdc)
    gas_usdc = float(sum_gas_eth) * float(eth_price)

    pnl_by_components = (
        float(fees_usdc) + float(fees_weth) * float(eth_price)
        + float(il_usdc) + float(il_weth) * float(eth_price)
        + float(sum_swap_usdc) + float(sum_swap_weth) * float(eth_price)
        - float(gas_usdc)
    )
    pnl_by_balances = (
        (float(end_usdc) + float(end_weth) * float(eth_price))
        - (float(start_usdc) + float(start_weth) * float(eth_price))
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

    # Validate alchemy endpoint explicitly before work.
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

    eth_price = _get_eth_price_usdt(str(args.binance_symbol))
    report = _build_report(
        w3=w3,
        args=args,
        logger=logger,
        weth_token=weth_token,
        usdc_token=usdc_token,
        tx_hashes=tx_hashes,
        npm_address=str(npm_address),
        router_set=router_lc_set,
        token0_address=str(token0.address),
        eth_price=float(eth_price),
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
        tx_pnl_usdc = float(tx.usdc_delta) + float(tx.weth_delta) * float(report.eth_price_usdt) - float(tx.gas_eth) * float(report.eth_price_usdt)
        print(f'[{idx}] block={tx.block_number} ts_ms={tx.timestamp_ms} kind={tx.kind.value} swap={int(tx.is_uniswap_swap)}')
        print(f'    tx={tx.tx_hash}')
        print(f'    delta:   WETH={tx.weth_delta:.8f} USDC={tx.usdc_delta:.8f}')
        print(f'    pool io: in(WETH={tx.increase_weth:.8f}, USDC={tx.increase_usdc:.8f}) out(WETH={tx.decrease_weth:.8f}, USDC={tx.decrease_usdc:.8f}) collect(WETH={tx.collect_weth:.8f}, USDC={tx.collect_usdc:.8f})')
        print(f'    gas:     ETH={tx.gas_eth:.8f} USDC={float(tx.gas_eth) * float(report.eth_price_usdt):.8f}')
        print(f'    tx pnl (USDC, delta-gas): {tx_pnl_usdc:.8f}')
        print('')

    print('=== SUMMARY ===')
    print(f'WETH start/end: {report.balances.start_weth:.8f} -> {report.balances.end_weth:.8f} (delta {report.balances.delta_weth:.8f})')
    print(f'USDC start/end: {report.balances.start_usdc:.8f} -> {report.balances.end_usdc:.8f} (delta {report.balances.delta_usdc:.8f})')
    print('')
    print(f'Fees from pool: WETH={report.pnl.fees.weth:.8f}, USDC={report.pnl.fees.usdc:.8f}')
    print(f'Realized IL:    WETH={report.pnl.realized_il.weth:.8f}, USDC={report.pnl.realized_il.usdc:.8f}')
    print(f'Uniswap swaps:  WETH={report.pnl.swaps.weth:.8f}, USDC={report.pnl.swaps.usdc:.8f}')
    print(f'Gas (add/remove/rebalance): ETH={report.pnl.gas_eth:.8f}, USDC={report.pnl.gas_usdc:.8f}')
    print('')
    print(f'PnL by components (USDC): {report.pnl.pnl_usdc_by_components:.8f}')
    print(f'PnL by balances   (USDC): {report.pnl_usdc_by_balances:.8f}')


if __name__ == '__main__':
    main()
