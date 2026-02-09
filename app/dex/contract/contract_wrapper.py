"""
Contract wrapper
Date: 2026-02-09
Version: 1.0
"""
import json, math
from decimal import Decimal, getcontext
from typing import Dict, List
from web3 import Web3
from dex.lib.logger import get_logger
from dex.models.realtime_models import DexEventType, PriceEvent, SwapEvent
from dex.contract.params import Params

# Contract wrapper
class ContractWrapper:
    def __init__(self, rpc_url: str, pool_address: str, network: str,
                 pool_abi_path: str = 'dex/contract/abi/UniswapV3Pool.json',
                 erc20_abi_path: str = 'dex/contract/abi/ERC20.json') -> None:
        # Params
        self._rpc_url = rpc_url
        self._pool_address = pool_address
        self._pool_abi_path = pool_abi_path
        self._erc20_abi_path = erc20_abi_path
        self._network = network

        # Properties
        self._logger = get_logger('contract_wrapper')
        self._decimals_cache: Dict[str, int] = {}
        self._pool_contract = None
        self._erc20_abi = None
        self._w3 = None
        self._token0_address = None
        self._token1_address = None
        self._stable_addresses: List[str] = []

        # Init
        self._validate_params()
        self._init_web3()
        self._init_contracts()

    def _validate_params(self) -> None:
        if not isinstance(self._rpc_url, str) or len(self._rpc_url) == 0:
            raise RuntimeError('rpc_url is empty')
        if not isinstance(self._pool_address, str) or len(self._pool_address) == 0:
            raise RuntimeError('pool_address is empty')
        if not isinstance(self._pool_abi_path, str) or len(self._pool_abi_path) == 0:
            raise RuntimeError('pool_abi_path is empty')
        if not isinstance(self._erc20_abi_path, str) or len(self._erc20_abi_path) == 0:
            raise RuntimeError('erc20_abi_path is empty')
        if not isinstance(self._network, str) or len(self._network) == 0:
            raise RuntimeError('network is empty')

    def _init_web3(self) -> None:
        self._w3 = Web3(Web3.HTTPProvider(self._rpc_url))

        if not bool(self._w3.is_connected()):
            raise RuntimeError('RPC connection failed')

        getcontext().prec = 60

    def _init_contracts(self) -> None:
        pool_abi = self._load_json(self._pool_abi_path)
        self._erc20_abi = self._load_json(self._erc20_abi_path)

        pool_addr = self._to_checksum_address(self._pool_address)
        self._pool_address = pool_addr
        self._pool_contract = self._w3.eth.contract(address=pool_addr, abi=pool_abi)

        token0 = self._pool_contract.functions.token0().call()
        token1 = self._pool_contract.functions.token1().call()

        self._token0_address = self._to_checksum_address(str(token0))
        self._token1_address = self._to_checksum_address(str(token1))

        self._cache_decimals(self._token0_address)
        self._cache_decimals(self._token1_address)

        self._init_stable_addresses()

    def _init_stable_addresses(self) -> None:
        networks = Params.NETWORKS
        if self._network not in networks:
            raise RuntimeError(f'Network not found: {self._network}')

        net = networks[self._network]
        stables = net['stables']
        if not isinstance(stables, list):
            raise RuntimeError(f'stables must be list for {self._network}')
        if len(stables) == 0:
            raise RuntimeError(f'stables is empty for {self._network}')

        for addr in stables:
            if not isinstance(addr, str) or len(addr) == 0:
                raise RuntimeError(f'stable address is empty for {self._network}')
            self._stable_addresses.append(self._to_checksum_address(str(addr)))

    def _load_json(self, path: str):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise RuntimeError(f'Failed to load json: {path} {e}')

        if not isinstance(data, list):
            raise RuntimeError(f'ABI must be list: {path}')

        return data

    def _to_checksum_address(self, address: str) -> str:
        if not isinstance(address, str) or len(address) == 0:
            raise RuntimeError('Address is empty')

        return self._w3.to_checksum_address(str(address))

    def _cache_decimals(self, token_address: str) -> None:
        if token_address in self._decimals_cache:
            return

        erc20 = self._w3.eth.contract(
            address=self._to_checksum_address(token_address),
            abi=self._erc20_abi,
        )

        decimals = erc20.functions.decimals().call()
        if not isinstance(decimals, int):
            raise RuntimeError('decimals is not int')
        if int(decimals) < 0:
            raise RuntimeError('decimals is negative')

        self._decimals_cache[token_address] = int(decimals)

    def _get_decimals(self, token_address: str) -> int:
        if token_address not in self._decimals_cache:
            self._cache_decimals(token_address)

        return int(self._decimals_cache[token_address])

    # ----------------------------------------------------------------------
    def convet_to_price_event(self, swap_event: SwapEvent) -> PriceEvent:
        if not isinstance(swap_event, SwapEvent):
            raise RuntimeError('swap_event is not SwapEvent')

        pool_address = str(swap_event.pool_address).lower()
        if pool_address != str(self._pool_address).lower():
            raise RuntimeError('Pool address mismatch')

        decimals0 = self._get_decimals(self._token0_address)
        decimals1 = self._get_decimals(self._token1_address)

        sqrt_price_x96 = int(swap_event.sqrt_price_x96)
        if sqrt_price_x96 <= 0:
            raise RuntimeError('sqrt_price_x96 is not positive')

        price_raw = (Decimal(sqrt_price_x96) ** 2) / (Decimal(2) ** 192)
        price_scale = Decimal(10) ** Decimal(int(decimals0) - int(decimals1))
        price_token1_per_token0 = price_raw * price_scale

        if price_token1_per_token0 <= 0:
            raise RuntimeError('price_token1_per_token0 is not positive')

        price_token0_per_token1 = Decimal(1) / price_token1_per_token0

        price_t1 = float(price_token1_per_token0)
        price_t0 = float(price_token0_per_token1)

        if not math.isfinite(price_t1):
            raise RuntimeError('price_token1_per_token0 is not finite')
        if not math.isfinite(price_t0):
            raise RuntimeError('price_token0_per_token1 is not finite')

        traditional_price = self._calc_traditional_price(price_t1, price_t0)

        return PriceEvent(
            event_type=DexEventType.PRICE,
            pool_address=str(self._pool_address),
            token0_address=str(self._token0_address),
            token1_address=str(self._token1_address),
            decimals0=int(decimals0),
            decimals1=int(decimals1),
            block_number=int(swap_event.block_number),
            tx_hash=str(swap_event.tx_hash),
            log_index=int(swap_event.log_index),
            sqrt_price_x96=int(swap_event.sqrt_price_x96),
            tick=int(swap_event.tick),
            price_token1_per_token0=float(price_t1),
            price_token0_per_token1=float(price_t0),
            traditional_price=float(traditional_price),
        )

    def _calc_traditional_price(self, price_token1_per_token0: float, price_token0_per_token1: float) -> float:
        t0 = str(self._token0_address).lower()
        t1 = str(self._token1_address).lower()
        stables = [str(a).lower() for a in self._stable_addresses]

        is_t0_stable = t0 in stables
        is_t1_stable = t1 in stables

        if is_t0_stable and is_t1_stable:
            raise RuntimeError('Both tokens are stable')
        if (not is_t0_stable) and (not is_t1_stable):
            raise RuntimeError('No stable token in pool')

        if is_t1_stable:
            if not math.isfinite(price_token1_per_token0):
                raise RuntimeError('price_token1_per_token0 is not finite')
            return float(price_token1_per_token0)

        if not math.isfinite(price_token0_per_token1):
            raise RuntimeError('price_token0_per_token1 is not finite')
        return float(price_token0_per_token1)