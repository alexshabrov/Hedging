"""
CowSwap swapper
Date: 2026-02-10
Version: 1.0
"""
import asyncio, datetime, time
from typing import Dict, Tuple
import orjson
import requests
from eth_account import Account
from web3 import Web3
from web3.types import Wei
from cowdao_cowpy.cow.swap import swap_tokens
from cowdao_cowpy.common.chains import Chain
from dex.lib.logger import get_logger
from dex.contract.params import Params
from dex.models.swapper_models import CowSwapConfig, CowSwapOrderData, SwapPriceDetails, SwapPriceInfo, SwapRequest, \
    SwapResult, SwapOrderInfo, SwapStatus, SwapperType, TokenInfo
from dex.swappers.swapper_interface import SwapperInterface

# Chains
CHAIN_MAP = {
    'mainnet': Chain.MAINNET,
    'ethereum': Chain.MAINNET,
    'arbitrum': Chain.ARBITRUM_ONE,
    'sepolia': Chain.SEPOLIA,
    'polygon': Chain.POLYGON,
    'base': Chain.BASE,
}

# API base
COW_API_BASE = {
    'mainnet': 'https://api.cow.fi/mainnet/api/v1',
    'ethereum': 'https://api.cow.fi/mainnet/api/v1',
    'arbitrum': 'https://api.cow.fi/arbitrum_one/api/v1',
    'sepolia': 'https://api.cow.fi/sepolia/api/v1',
    'polygon': 'https://api.cow.fi/polygon/api/v1',
    'base': 'https://api.cow.fi/base/api/v1',
}

# ERC20 ABIs
ERC20_DECIMALS_ABI = [
    {
        'constant': True,
        'inputs': [],
        'name': 'decimals',
        'outputs': [{'name': '', 'type': 'uint8'}],
        'type': 'function',
    }
]

ERC20_SYMBOL_ABI = [
    {
        'constant': True,
        'inputs': [],
        'name': 'symbol',
        'outputs': [{'name': '', 'type': 'string'}],
        'type': 'function',
    }
]

ERC20_BALANCE_ABI = [
    {
        'constant': True,
        'inputs': [{'name': '_owner', 'type': 'address'}],
        'name': 'balanceOf',
        'outputs': [{'name': 'balance', 'type': 'uint256'}],
        'type': 'function',
    }
]

ERC20_ALLOWANCE_ABI = [
    {
        'constant': True,
        'inputs': [
            {'name': '_owner', 'type': 'address'},
            {'name': '_spender', 'type': 'address'},
        ],
        'name': 'allowance',
        'outputs': [{'name': 'remaining', 'type': 'uint256'}],
        'type': 'function',
    }
]

ERC20_APPROVE_ABI = [
    {
        'constant': False,
        'inputs': [
            {'name': '_spender', 'type': 'address'},
            {'name': '_value', 'type': 'uint256'},
        ],
        'name': 'approve',
        'outputs': [{'name': 'success', 'type': 'bool'}],
        'type': 'function',
    }
]

# Vault relayer
VAULT_RELAYER_MAP = {
    'mainnet': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
    'ethereum': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
    'arbitrum': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
    'sepolia': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
    'polygon': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
    'base': '0xC92E8bdf79f0507f65a392b0ab4667716BFE0110',
}

# Swapper
class CowSwapSwapper(SwapperInterface):
    def __init__(self, config: CowSwapConfig) -> None:
        # Params
        if not isinstance(config, CowSwapConfig):
            raise RuntimeError('config is not CowSwapConfig')
        if config.swapper_type != SwapperType.COW_SWAP:
            raise RuntimeError('swapper_type is not cow_swap')
        if not isinstance(config.network, str) or len(config.network) == 0:
            raise RuntimeError('network is empty')
        if not isinstance(config.rpc_url, str) or len(config.rpc_url) == 0:
            raise RuntimeError('rpc_url is empty')
        if not isinstance(config.private_key, str) or len(config.private_key) == 0:
            raise RuntimeError('private_key is empty')
        if config.wallet_address is not None and len(str(config.wallet_address)) == 0:
            raise RuntimeError('wallet_address is empty')
        if int(config.api_timeout_sec) <= 0:
            raise RuntimeError('api_timeout_sec must be > 0')
        if int(config.wait_timeout_sec) <= 0:
            raise RuntimeError('wait_timeout_sec must be > 0')
        if int(config.poll_interval_sec) <= 0:
            raise RuntimeError('poll_interval_sec must be > 0')

        # Properties
        self._config = config
        self._logger = get_logger('cow_swap')
        self._network = str(config.network)
        self._rpc_url = str(config.rpc_url)
        self._private_key = str(config.private_key)
        self._wallet_address = None if config.wallet_address is None else str(config.wallet_address)
        self._api_timeout_sec = int(config.api_timeout_sec)
        self._wait_timeout_sec = int(config.wait_timeout_sec)
        self._poll_interval_sec = int(config.poll_interval_sec)

        self._decimals_cache: Dict[str, int] = {}
        self._symbol_cache: Dict[str, str] = {}

        # Init
        self._w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        if not bool(self._w3.is_connected()):
            raise RuntimeError('RPC connection failed')

        self._account = Account.from_key(self._private_key)
        if self._wallet_address is None:
            self._wallet_address = str(self._account.address)
        else:
            if str(self._wallet_address).lower() != str(self._account.address).lower():
                raise RuntimeError('wallet_address does not match private_key')

        if self._network not in CHAIN_MAP:
            raise RuntimeError(f'Network is not supported: {self._network}')
        self._chain = CHAIN_MAP[self._network]

        if self._network not in COW_API_BASE:
            raise RuntimeError(f'CowSwap API is not supported: {self._network}')
        self._api_base = COW_API_BASE[self._network]

        self._stable_addresses = self._load_stable_addresses()
        self._vault_relayer = self._init_vault_relayer()
        self._event_loop = asyncio.get_event_loop_policy().new_event_loop()

    def _load_stable_addresses(self) -> list:
        networks = Params.NETWORKS
        if self._network not in networks:
            raise RuntimeError(f'Network not found: {self._network}')
        net = networks[self._network]
        stables = net['stables']
        if not isinstance(stables, list):
            raise RuntimeError(f'stables must be list for {self._network}')
        if len(stables) == 0:
            raise RuntimeError(f'stables is empty for {self._network}')

        stable_list = []
        for addr in stables:
            if not isinstance(addr, str) or len(addr) == 0:
                raise RuntimeError(f'stable address is empty for {self._network}')
            stable_list.append(str(self._to_checksum_address(str(addr))).lower())
        return stable_list

    def _init_vault_relayer(self) -> str:
        if self._network not in VAULT_RELAYER_MAP:
            raise RuntimeError(f'VaultRelayer not configured for {self._network}')
        return str(self._to_checksum_address(str(VAULT_RELAYER_MAP[self._network])))

    def _to_checksum_address(self, address: str) -> str:
        if not isinstance(address, str) or len(address) == 0:
            raise RuntimeError('address is empty')
        return self._w3.to_checksum_address(str(address))

    def _get_decimals(self, token_address: str) -> int:
        token_address = self._to_checksum_address(token_address)
        if token_address in self._decimals_cache:
            return int(self._decimals_cache[token_address])

        erc20 = self._w3.eth.contract(address=token_address, abi=ERC20_DECIMALS_ABI)
        decimals = erc20.functions.decimals().call()
        if not isinstance(decimals, int):
            raise RuntimeError('decimals is not int')
        if int(decimals) < 0:
            raise RuntimeError('decimals is negative')

        self._decimals_cache[token_address] = int(decimals)
        return int(decimals)

    def _get_symbol(self, token_address: str) -> str:
        token_address = self._to_checksum_address(token_address)
        if token_address in self._symbol_cache:
            return str(self._symbol_cache[token_address])

        erc20 = self._w3.eth.contract(address=token_address, abi=ERC20_SYMBOL_ABI)
        symbol = erc20.functions.symbol().call()
        if isinstance(symbol, bytes):
            symbol = symbol.decode('utf-8').rstrip('\x00')
        if not isinstance(symbol, str):
            raise RuntimeError('symbol is not str')
        if len(symbol) == 0:
            raise RuntimeError('symbol is empty')

        self._symbol_cache[token_address] = str(symbol)
        return str(symbol)

    def _get_balance(self, token_address: str) -> int:
        token_address = self._to_checksum_address(token_address)
        erc20 = self._w3.eth.contract(address=token_address, abi=ERC20_BALANCE_ABI)
        return int(erc20.functions.balanceOf(self._wallet_address).call())

    def _get_allowance(self, token_address: str) -> int:
        token_address = self._to_checksum_address(token_address)
        erc20 = self._w3.eth.contract(address=token_address, abi=ERC20_ALLOWANCE_ABI)
        return int(erc20.functions.allowance(self._wallet_address, self._vault_relayer).call())

    def _approve(self, token_address: str, amount: int) -> None:
        token_address = self._to_checksum_address(token_address)
        erc20 = self._w3.eth.contract(address=token_address, abi=ERC20_APPROVE_ABI)

        nonce = self._w3.eth.get_transaction_count(self._wallet_address)
        base_fee = self._w3.eth.get_block('latest')['baseFeePerGas']
        max_priority_fee = self._w3.to_wei(0.1, 'gwei')
        max_fee_per_gas = int(base_fee) + int(max_priority_fee)

        tx = erc20.functions.approve(self._vault_relayer, int(amount)).build_transaction({
            'from': self._wallet_address,
            'nonce': nonce,
            'gas': 60000,
            'maxFeePerGas': max_fee_per_gas,
            'maxPriorityFeePerGas': max_priority_fee,
            'type': 2,
        })

        signed = self._w3.eth.account.sign_transaction(tx, private_key=str(self._private_key))
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)
        if not bool(receipt.status):
            raise RuntimeError('approve failed')

    def _ensure_allowance(self, token_address: str, amount: int) -> None:
        current = self._get_allowance(token_address)
        if int(current) >= int(amount):
            return
        self._approve(token_address, int(amount))

    def _get_token_info(self, token_address: str) -> TokenInfo:
        addr = self._to_checksum_address(token_address)
        decimals = self._get_decimals(addr)
        symbol = self._get_symbol(addr)
        return TokenInfo(
            address=str(addr),
            symbol=str(symbol),
            decimals=int(decimals),
        )

    def _to_wei(self, amount: float, decimals: int) -> int:
        if not isinstance(amount, float) and not isinstance(amount, int):
            raise RuntimeError('amount is not number')
        if float(amount) <= 0:
            raise RuntimeError('amount must be > 0')
        if int(decimals) < 0:
            raise RuntimeError('decimals is negative')
        return int(float(amount) * (10 ** int(decimals)))

    def _calc_traditional_price(self, price_token1_per_token0: float,
                                price_token0_per_token1: float,
                                token0_address: str,
                                token1_address: str) -> float:
        t0 = str(token0_address).lower()
        t1 = str(token1_address).lower()

        if t0 in self._stable_addresses and t1 in self._stable_addresses:
            raise RuntimeError('Both tokens are stable')
        if t0 not in self._stable_addresses and t1 not in self._stable_addresses:
            raise RuntimeError('No stable token in pair')

        if t1 in self._stable_addresses:
            return float(price_token1_per_token0)
        return float(price_token0_per_token1)

    def _calc_price_info(self, sell_info: TokenInfo, buy_info: TokenInfo,
                         sell_raw: int, buy_raw: int) -> SwapPriceInfo:
        if int(sell_raw) <= 0 or int(buy_raw) <= 0:
            raise RuntimeError('price calc requires positive amounts')

        sell_amount = float(int(sell_raw)) / float(10 ** int(sell_info.decimals))
        buy_amount = float(int(buy_raw)) / float(10 ** int(buy_info.decimals))
        if float(sell_amount) <= 0 or float(buy_amount) <= 0:
            raise RuntimeError('normalized amounts must be > 0')

        price_token1_per_token0 = float(buy_amount) / float(sell_amount)
        price_token0_per_token1 = float(sell_amount) / float(buy_amount)
        traditional_price = self._calc_traditional_price(
            price_token1_per_token0=price_token1_per_token0,
            price_token0_per_token1=price_token0_per_token1,
            token0_address=sell_info.address,
            token1_address=buy_info.address,
        )

        return SwapPriceInfo(
            price_token1_per_token0=float(price_token1_per_token0),
            price_token0_per_token1=float(price_token0_per_token1),
            traditional_price=float(traditional_price),
        )

    def _build_price_details(self, sell_info: TokenInfo, buy_info: TokenInfo,
                             quoted_sell_raw: int, quoted_buy_raw: int,
                             executed_sell_raw: int, executed_buy_raw: int) -> SwapPriceDetails:
        quoted = self._calc_price_info(sell_info, buy_info, quoted_sell_raw, quoted_buy_raw)

        executed = None
        if int(executed_sell_raw) > 0 and int(executed_buy_raw) > 0:
            executed = self._calc_price_info(sell_info, buy_info, executed_sell_raw, executed_buy_raw)

        return SwapPriceDetails(
            quoted=quoted,
            executed=executed,
        )

    def _map_status(self, status: str) -> SwapStatus:
        if not isinstance(status, str) or len(status) == 0:
            raise RuntimeError('status is empty')

        st = str(status).lower()
        if st == 'open':
            return SwapStatus.PENDING
        if st == 'fulfilled':
            return SwapStatus.FULFILLED
        if st == 'expired':
            return SwapStatus.EXPIRED
        if st == 'cancelled':
            return SwapStatus.CANCELLED
        if st == 'failed':
            return SwapStatus.FAILED

        raise RuntimeError(f'Unknown CowSwap status: {status}')

    def _parse_order_data(self, data: dict) -> CowSwapOrderData:
        uid = str(data['uid'])
        status = self._map_status(str(data['status']))
        creation_date = str(data['creationDate'])
        sell_token = str(data['sellToken'])
        buy_token = str(data['buyToken'])
        sell_amount_raw = int(data['sellAmount'])
        buy_amount_raw = int(data['buyAmount'])
        executed_sell_raw = int(data['executedSellAmount'])
        executed_buy_raw = int(data['executedBuyAmount'])
        fee_amount_raw = int(data['executedFee'])
        fee_token = str(data['executedFeeToken'])

        return CowSwapOrderData(
            uid=str(uid),
            status=status,
            creation_date=str(creation_date),
            sell_token=str(sell_token),
            buy_token=str(buy_token),
            sell_amount_raw=int(sell_amount_raw),
            buy_amount_raw=int(buy_amount_raw),
            executed_sell_raw=int(executed_sell_raw),
            executed_buy_raw=int(executed_buy_raw),
            fee_amount_raw=int(fee_amount_raw),
            fee_token=str(fee_token),
        )

    def _get_order_status(self, order_uid: str) -> CowSwapOrderData:
        if not isinstance(order_uid, str) or len(order_uid) == 0:
            raise RuntimeError('order_uid is empty')

        url = f'{self._api_base}/orders/{order_uid}'
        resp = requests.get(url, timeout=self._api_timeout_sec)
        if int(resp.status_code) != 200:
            raise RuntimeError(f'CowSwap API error: {resp.status_code} {resp.text}')

        try:
            data = orjson.loads(resp.content)
        except Exception as e:
            raise RuntimeError(f'Failed to parse CowSwap response: {e}')

        if not isinstance(data, dict):
            raise RuntimeError('CowSwap response is not dict')

        return self._parse_order_data(data)

    def _place_swap_sync(self, sell_token: str, buy_token: str, amount_raw: int) -> Tuple[str, str]:
        loop = self._event_loop
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                swap_tokens(
                    amount=Wei(int(amount_raw)),
                    account=self._account,
                    chain=self._chain,
                    sell_token=self._to_checksum_address(str(sell_token)),
                    buy_token=self._to_checksum_address(str(buy_token)),
                )
            )
        finally:
            asyncio.set_event_loop(None)

        if result is None:
            raise RuntimeError('CowSwap swap result is empty')

        uid = result.uid.root
        url = result.url
        if not isinstance(uid, str) or len(uid) == 0:
            raise RuntimeError('CowSwap uid is empty')
        if not isinstance(url, str) or len(url) == 0:
            raise RuntimeError('CowSwap url is empty')

        return str(uid), str(url)

    def _wait_for_order(self, order_uid: str, timeout_sec: int, poll_interval_sec: int) -> Tuple[CowSwapOrderData, int]:
        start_ts = time.time()
        last_info = None
        errors = 0
        max_errors = 5

        while True:
            elapsed = int(time.time() - start_ts)
            if int(elapsed) > int(timeout_sec):
                if last_info is None:
                    raise RuntimeError('CowSwap wait timeout without order info')
                return CowSwapOrderData(
                    uid=str(last_info.uid),
                    status=SwapStatus.TIMEOUT,
                    creation_date=str(last_info.creation_date),
                    sell_token=str(last_info.sell_token),
                    buy_token=str(last_info.buy_token),
                    sell_amount_raw=int(last_info.sell_amount_raw),
                    buy_amount_raw=int(last_info.buy_amount_raw),
                    executed_sell_raw=int(last_info.executed_sell_raw),
                    executed_buy_raw=int(last_info.executed_buy_raw),
                    fee_amount_raw=int(last_info.fee_amount_raw),
                    fee_token=str(last_info.fee_token),
                ), int(elapsed)

            try:
                info = self._get_order_status(order_uid)
                last_info = info
                if info.status == SwapStatus.FULFILLED:
                    return info, int(elapsed)
                if info.status in [SwapStatus.EXPIRED, SwapStatus.CANCELLED, SwapStatus.FAILED]:
                    return info, int(elapsed)

            except Exception as e:
                errors += 1
                self._logger.error(f'CowSwap status error: {e}')
                if errors >= max_errors:
                    if last_info is None:
                        raise RuntimeError('CowSwap API error without order info')
                    return CowSwapOrderData(
                        uid=str(last_info.uid),
                        status=SwapStatus.API_ERROR,
                        creation_date=str(last_info.creation_date),
                        sell_token=str(last_info.sell_token),
                        buy_token=str(last_info.buy_token),
                        sell_amount_raw=int(last_info.sell_amount_raw),
                        buy_amount_raw=int(last_info.buy_amount_raw),
                        executed_sell_raw=int(last_info.executed_sell_raw),
                        executed_buy_raw=int(last_info.executed_buy_raw),
                        fee_amount_raw=int(last_info.fee_amount_raw),
                        fee_token=str(last_info.fee_token),
                    ), int(elapsed)

            time.sleep(int(poll_interval_sec))

    def swap_sync(self, request: SwapRequest) -> SwapResult:
        if not isinstance(request, SwapRequest):
            raise RuntimeError('request is not SwapRequest')
        if not isinstance(request.sell_token, str) or len(request.sell_token) == 0:
            raise RuntimeError('sell_token is empty')
        if not isinstance(request.buy_token, str) or len(request.buy_token) == 0:
            raise RuntimeError('buy_token is empty')
        if float(request.amount) <= 0:
            raise RuntimeError('amount must be > 0')
        if int(request.wait_timeout_sec) <= 0:
            raise RuntimeError('wait_timeout_sec must be > 0')
        if int(request.poll_interval_sec) <= 0:
            raise RuntimeError('poll_interval_sec must be > 0')

        sell_info = self._get_token_info(request.sell_token)
        buy_info = self._get_token_info(request.buy_token)

        balance_raw = self._get_balance(sell_info.address)
        amount_raw = self._to_wei(float(request.amount), int(sell_info.decimals))
        if int(balance_raw) < int(amount_raw):
            return SwapResult(
                ok=False,
                error=f'Insufficient balance: balance={balance_raw} required={amount_raw}',
                order=None,
            )

        self._ensure_allowance(sell_info.address, int(amount_raw))
        uid, url = self._place_swap_sync(sell_info.address, buy_info.address, amount_raw)
        order_data, elapsed_sec = self._wait_for_order(uid, int(request.wait_timeout_sec), int(request.poll_interval_sec))

        if str(order_data.sell_token).lower() != str(sell_info.address).lower():
            raise RuntimeError('sell_token mismatch with order')
        if str(order_data.buy_token).lower() != str(buy_info.address).lower():
            raise RuntimeError('buy_token mismatch with order')

        sell_amount = float(order_data.sell_amount_raw) / float(10 ** int(sell_info.decimals))
        buy_amount = float(order_data.buy_amount_raw) / float(10 ** int(buy_info.decimals))
        executed_sell = float(order_data.executed_sell_raw) / float(10 ** int(sell_info.decimals))
        executed_buy = float(order_data.executed_buy_raw) / float(10 ** int(buy_info.decimals))

        fee_decimals = self._get_decimals(order_data.fee_token)
        fee_amount = float(order_data.fee_amount_raw) / float(10 ** int(fee_decimals))

        price_details = self._build_price_details(
            sell_info=sell_info,
            buy_info=buy_info,
            quoted_sell_raw=int(order_data.sell_amount_raw),
            quoted_buy_raw=int(order_data.buy_amount_raw),
            executed_sell_raw=int(order_data.executed_sell_raw),
            executed_buy_raw=int(order_data.executed_buy_raw),
        )

        created_at = str(order_data.creation_date)
        try:
            created_at = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00')).isoformat()
        except Exception:
            raise RuntimeError('creation_date is invalid')

        order_info = SwapOrderInfo(
            uid=str(order_data.uid),
            status=order_data.status,
            network=str(self._network),
            created_at=str(created_at),
            sell_token=sell_info,
            buy_token=buy_info,
            sell_amount_raw=int(order_data.sell_amount_raw),
            buy_amount_raw=int(order_data.buy_amount_raw),
            sell_amount=float(sell_amount),
            buy_amount=float(buy_amount),
            executed_sell_raw=int(order_data.executed_sell_raw),
            executed_buy_raw=int(order_data.executed_buy_raw),
            executed_sell=float(executed_sell),
            executed_buy=float(executed_buy),
            fee_amount_raw=int(order_data.fee_amount_raw),
            fee_amount=float(fee_amount),
            fee_token=str(order_data.fee_token),
            price=price_details,
            url=str(url),
            elapsed_sec=int(elapsed_sec),
        )

        ok = order_data.status == SwapStatus.FULFILLED
        error = None if ok else f'status={order_data.status.value}'

        return SwapResult(
            ok=bool(ok),
            error=error,
            order=order_info,
        )