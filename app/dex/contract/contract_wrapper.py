"""
Contract wrapper
Date: 2026-02-09
Version: 1.0
"""
import json, math
from decimal import Decimal, getcontext
from typing import Dict, List, Optional
from web3 import Web3
from dex.lib.logger import get_logger
from dex.models.realtime_models import DexEventType, PriceEvent, SwapEvent
from dex.contract.params import Params
from dex.contract.pool_calc import split_capital_into_tokens

# Contract wrapper
class ContractWrapper:
    def __init__(self, rpc_url: str, pool_address: str, network: str,
                 pool_abi_path: str = 'dex/contract/abi/UniswapV3Pool.json',
                 erc20_abi_path: str = 'dex/contract/abi/ERC20.json',
                 npm_abi_path: str = 'dex/contract/abi/NonfungiblePositionManager.json',
                 private_key: Optional[str] = None,
                 wallet_address: Optional[str] = None) -> None:
        # Params
        self._rpc_url = rpc_url
        self._pool_address = pool_address
        self._pool_abi_path = pool_abi_path
        self._erc20_abi_path = erc20_abi_path
        self._npm_abi_path = npm_abi_path
        self._network = network
        self._private_key = private_key
        self._wallet_address = wallet_address

        # Properties
        self._logger = get_logger('contract_wrapper')
        self._decimals_cache: Dict[str, int] = {}
        self._symbol_cache: Dict[str, str] = {}
        self._pool_contract = None
        self._erc20_abi = None
        self._npm_abi = None
        self._npm_address = None
        self._npm_contract = None
        self._w3 = None
        self._token0_address = None
        self._token1_address = None
        self._stable_addresses: List[str] = []
        self._base_token_address = None
        self._quote_token_address = None
        self._pair_name = None
        self._account = None

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
        if not isinstance(self._npm_abi_path, str) or len(self._npm_abi_path) == 0:
            raise RuntimeError('npm_abi_path is empty')
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
        self._npm_abi = self._load_json(self._npm_abi_path)

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
        self._init_pair_orientation()
        self._init_position_manager()
        self._init_wallet()

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

    def _init_pair_orientation(self) -> None:
        t0 = str(self._token0_address).lower()
        t1 = str(self._token1_address).lower()
        stables = [str(a).lower() for a in self._stable_addresses]

        is_t0_stable = t0 in stables
        is_t1_stable = t1 in stables

        if is_t0_stable and is_t1_stable:
            raise RuntimeError('Both tokens are stable')
        if (not is_t0_stable) and (not is_t1_stable):
            raise RuntimeError('No stable token in pool')

        if is_t0_stable:
            self._quote_token_address = self._token0_address
            self._base_token_address = self._token1_address
        else:
            self._quote_token_address = self._token1_address
            self._base_token_address = self._token0_address

        base_symbol = self._get_symbol(self._base_token_address)
        quote_symbol = self._get_symbol(self._quote_token_address)
        self._pair_name = f'{base_symbol}{quote_symbol}'

    def _init_position_manager(self) -> None:
        networks = Params.NETWORKS
        if self._network not in networks:
            raise RuntimeError(f'Network not found: {self._network}')

        net = networks[self._network]
        npm = net['npm']
        if not isinstance(npm, str) or len(npm) == 0:
            raise RuntimeError(f'npm address not set for {self._network}')

        self._npm_address = self._to_checksum_address(str(npm))
        self._npm_contract = self._w3.eth.contract(address=self._npm_address, abi=self._npm_abi)

    def _init_wallet(self) -> None:
        if self._private_key is None and self._wallet_address is None:
            return

        if self._private_key is None or len(str(self._private_key)) == 0:
            raise RuntimeError('private_key is empty')

        self._account = self._w3.eth.account.from_key(str(self._private_key))
        if self._wallet_address is None or len(str(self._wallet_address)) == 0:
            self._wallet_address = str(self._account.address)
            return

        if str(self._wallet_address).lower() != str(self._account.address).lower():
            raise RuntimeError('wallet_address does not match private_key')

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

    def _cache_symbol(self, token_address: str) -> None:
        if token_address in self._symbol_cache:
            return

        erc20 = self._w3.eth.contract(
            address=self._to_checksum_address(token_address),
            abi=self._erc20_abi,
        )

        symbol = erc20.functions.symbol().call()
        if isinstance(symbol, bytes):
            symbol = symbol.decode('utf-8').rstrip('\x00')

        if not isinstance(symbol, str):
            raise RuntimeError('symbol is not str')
        if len(symbol) == 0:
            raise RuntimeError('symbol is empty')

        self._symbol_cache[token_address] = str(symbol)

    def _get_decimals(self, token_address: str) -> int:
        if token_address not in self._decimals_cache:
            self._cache_decimals(token_address)

        return int(self._decimals_cache[token_address])

    def _get_symbol(self, token_address: str) -> str:
        if token_address not in self._symbol_cache:
            self._cache_symbol(token_address)

        return str(self._symbol_cache[token_address])

    def is_pair_pool(self, pair: str) -> bool:
        if not isinstance(pair, str) or len(pair) == 0:
            raise RuntimeError('pair is empty')
        if self._pair_name is None:
            raise RuntimeError('pair_name is not initialized')

        return str(pair).upper() == str(self._pair_name).upper()

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
        quote = str(self._quote_token_address).lower()

        if t1 == quote:
            if not math.isfinite(price_token1_per_token0):
                raise RuntimeError('price_token1_per_token0 is not finite')
            return float(price_token1_per_token0)

        if not math.isfinite(price_token0_per_token1):
            raise RuntimeError('price_token0_per_token1 is not finite')
        return float(price_token0_per_token1)

    # ----------------------------------------------------------------------
    def add_liquidity_traditional(self, fee_pct: float, price_lower: float, price_upper: float,
                                  total_quote: float, recipient: Optional[str] = None) -> dict:
        if self._wallet_address is None:
            raise RuntimeError('wallet_address is not set')
        if self._private_key is None:
            raise RuntimeError('private_key is not set')
        if not isinstance(fee_pct, float) and not isinstance(fee_pct, int):
            raise RuntimeError('fee_pct is not number')
        if float(fee_pct) <= 0:
            raise RuntimeError('fee_pct must be > 0')
        if float(price_lower) <= 0 or float(price_upper) <= 0:
            raise RuntimeError('price bounds must be > 0')
        if float(price_lower) >= float(price_upper):
            raise RuntimeError('price_lower must be < price_upper')
        if float(total_quote) <= 0:
            raise RuntimeError('total_quote must be > 0')

        if recipient is None:
            recipient = str(self._wallet_address)

        fee = int(round(float(fee_pct) * 10000))
        tick_spacing = self._get_tick_spacing(fee)

        price_lower_pool = self._traditional_to_pool_price(float(price_lower))
        price_upper_pool = self._traditional_to_pool_price(float(price_upper))

        tick_lower_raw = self._price_to_tick(price_lower_pool)
        tick_upper_raw = self._price_to_tick(price_upper_pool)

        tick_lower = self._align_tick_down(tick_lower_raw, tick_spacing)
        tick_upper = self._align_tick_up(tick_upper_raw, tick_spacing)

        if tick_lower >= tick_upper:
            raise RuntimeError('tick_lower must be < tick_upper')

        current_price = self._get_current_traditional_price()
        amount_base, amount_quote, l_val = split_capital_into_tokens(
            total_quote=float(total_quote),
            price=float(current_price),
            price_lower=float(price_lower),
            price_upper=float(price_upper),
        )

        if float(amount_base) <= 0 or float(amount_quote) <= 0:
            raise RuntimeError('amounts must be > 0')

        amount0_desired, amount1_desired = self._map_amounts(amount_base, amount_quote)
        token0_decimals = self._get_decimals(self._token0_address)
        token1_decimals = self._get_decimals(self._token1_address)

        amount0_wei = int(Decimal(amount0_desired) * (Decimal(10) ** Decimal(token0_decimals)))
        amount1_wei = int(Decimal(amount1_desired) * (Decimal(10) ** Decimal(token1_decimals)))

        self._approve_token(self._token0_address, amount0_wei)
        self._approve_token(self._token1_address, amount1_wei)

        deadline = self._w3.eth.get_block('latest')['timestamp'] + 600

        mint_params = {
            "token0": self._token0_address,
            "token1": self._token1_address,
            "fee": fee,
            "tickLower": tick_lower,
            "tickUpper": tick_upper,
            "amount0Desired": amount0_wei,
            "amount1Desired": amount1_wei,
            "amount0Min": 0,
            "amount1Min": 0,
            "recipient": self._to_checksum_address(str(recipient)),
            "deadline": int(deadline),
        }

        return self._execute_mint(mint_params)

    def _execute_mint(self, mint_params: dict) -> dict:
        nonce = self._w3.eth.get_transaction_count(self._wallet_address)
        try:
            gas_estimate = self._npm_contract.functions.mint(mint_params).estimate_gas({
                "from": self._wallet_address
            })
            gas_limit = int(gas_estimate * 1.2)
        except Exception as e:
            raise RuntimeError(f'Gas estimation failed: {e}')

        base_fee = self._w3.eth.get_block("latest")["baseFeePerGas"]
        max_priority_fee = self._w3.to_wei(0.1, "gwei")
        max_fee_per_gas = base_fee + max_priority_fee

        tx = self._npm_contract.functions.mint(mint_params).build_transaction({
            "from": self._wallet_address,
            "nonce": nonce,
            "gas": gas_limit,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee,
            "type": 2,
        })

        signed = self._w3.eth.account.sign_transaction(tx, private_key=str(self._private_key))
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)

        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)
        success = bool(receipt.status)

        return {
            "ok": success,
            "tx_hash": tx_hash.hex(),
            "receipt": receipt,
        }

    def _approve_token(self, token_address: str, amount: int) -> None:
        erc20 = self._w3.eth.contract(
            address=self._to_checksum_address(token_address),
            abi=self._erc20_abi,
        )

        allowance = erc20.functions.allowance(
            self._wallet_address,
            self._npm_address,
        ).call()

        if int(allowance) >= int(amount):
            return

        nonce = self._w3.eth.get_transaction_count(self._wallet_address)
        base_fee = self._w3.eth.get_block("latest")["baseFeePerGas"]
        max_priority_fee = self._w3.to_wei(0.1, "gwei")
        max_fee_per_gas = base_fee + max_priority_fee

        tx = erc20.functions.approve(self._npm_address, int(amount)).build_transaction({
            "from": self._wallet_address,
            "nonce": nonce,
            "gas": 60000,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee,
            "type": 2,
        })

        signed = self._w3.eth.account.sign_transaction(tx, private_key=str(self._private_key))
        self._w3.eth.send_raw_transaction(signed.raw_transaction)

    def _map_amounts(self, amount_base: float, amount_quote: float) -> tuple:
        if str(self._base_token_address).lower() == str(self._token0_address).lower():
            return float(amount_base), float(amount_quote)

        return float(amount_quote), float(amount_base)

    def _traditional_to_pool_price(self, traditional_price: float) -> float:
        t0 = str(self._token0_address).lower()
        quote = str(self._quote_token_address).lower()
        if t0 == quote:
            return float(1 / float(traditional_price))

        return float(traditional_price)

    def _pool_to_traditional_price(self, pool_price: float) -> float:
        t1 = str(self._token1_address).lower()
        quote = str(self._quote_token_address).lower()
        if t1 == quote:
            return float(pool_price)

        return float(1 / float(pool_price))

    def _price_to_tick(self, price_token1_per_token0: float) -> int:
        token0_decimals = self._get_decimals(self._token0_address)
        token1_decimals = self._get_decimals(self._token1_address)

        adjusted_price = float(price_token1_per_token0) * (10 ** token1_decimals) / (10 ** token0_decimals)
        sqrt_price = math.sqrt(adjusted_price)
        sqrt_price_x96 = int(sqrt_price * (2 ** 96))
        tick = int(math.log((sqrt_price_x96 ** 2) / (2 ** 192)) / math.log(1.0001))
        return int(tick)

    def _get_current_traditional_price(self) -> float:
        slot0 = self._pool_contract.functions.slot0().call()
        sqrt_price_x96 = int(slot0[0])
        if sqrt_price_x96 <= 0:
            raise RuntimeError('sqrt_price_x96 is not positive')

        token0_decimals = self._get_decimals(self._token0_address)
        token1_decimals = self._get_decimals(self._token1_address)

        price_raw = (Decimal(sqrt_price_x96) ** 2) / (Decimal(2) ** 192)
        price_scale = Decimal(10) ** Decimal(int(token0_decimals) - int(token1_decimals))
        price_token1_per_token0 = float(price_raw * price_scale)

        if not math.isfinite(price_token1_per_token0) or price_token1_per_token0 <= 0:
            raise RuntimeError('pool price is invalid')

        return float(self._pool_to_traditional_price(price_token1_per_token0))

    def _get_tick_spacing(self, fee: int) -> int:
        fee_to_tick_spacing = {
            100: 1,
            500: 10,
            3000: 60,
            10000: 200,
        }
        if fee not in fee_to_tick_spacing:
            raise RuntimeError('Unsupported fee tier')
        return int(fee_to_tick_spacing[fee])

    def _align_tick_down(self, tick: int, tick_spacing: int) -> int:
        return int((int(tick) // int(tick_spacing)) * int(tick_spacing))

    def _align_tick_up(self, tick: int, tick_spacing: int) -> int:
        return int(((int(tick) + int(tick_spacing) - 1) // int(tick_spacing)) * int(tick_spacing))