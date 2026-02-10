"""
Swapper models
Date: 2026-02-10
Version: 1.0
"""
from enum import Enum
from typing import Optional
from dex.lib.strict_model import StrictModel

# Enums
class SwapperType(Enum):
    COW_SWAP = 'cow_swap'

class SwapStatus(Enum):
    CREATED = 'created'
    PENDING = 'pending'
    FULFILLED = 'fulfilled'
    EXPIRED = 'expired'
    CANCELLED = 'cancelled'
    FAILED = 'failed'
    TIMEOUT = 'timeout'
    API_ERROR = 'api_error'

# Configs
class CowSwapConfig(StrictModel):
    swapper_type: SwapperType
    network: str
    rpc_url: str
    private_key: str
    wallet_address: Optional[str]
    api_timeout_sec: int
    wait_timeout_sec: int
    poll_interval_sec: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'swapper_type': self.swapper_type.value,
            'network': self.network,
            'rpc_url': self.rpc_url,
            'private_key': self.private_key,
            'wallet_address': self.wallet_address,
            'api_timeout_sec': self.api_timeout_sec,
            'wait_timeout_sec': self.wait_timeout_sec,
            'poll_interval_sec': self.poll_interval_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CowSwapConfig':
        return cls(
            swapper_type=SwapperType(str(data['swapper_type'])),
            network=str(data['network']),
            rpc_url=str(data['rpc_url']),
            private_key=str(data['private_key']),
            wallet_address=None if data['wallet_address'] is None else str(data['wallet_address']),
            api_timeout_sec=int(data['api_timeout_sec']),
            wait_timeout_sec=int(data['wait_timeout_sec']),
            poll_interval_sec=int(data['poll_interval_sec']),
        )

# Swap request
class SwapRequest(StrictModel):
    sell_token: str
    buy_token: str
    amount: float
    wait_timeout_sec: int
    poll_interval_sec: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'sell_token': self.sell_token,
            'buy_token': self.buy_token,
            'amount': self.amount,
            'wait_timeout_sec': self.wait_timeout_sec,
            'poll_interval_sec': self.poll_interval_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapRequest':
        return cls(
            sell_token=str(data['sell_token']),
            buy_token=str(data['buy_token']),
            amount=float(data['amount']),
            wait_timeout_sec=int(data['wait_timeout_sec']),
            poll_interval_sec=int(data['poll_interval_sec']),
        )

# Tokens
class TokenInfo(StrictModel):
    address: str
    symbol: str
    decimals: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'address': self.address,
            'symbol': self.symbol,
            'decimals': self.decimals,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TokenInfo':
        return cls(
            address=str(data['address']),
            symbol=str(data['symbol']),
            decimals=int(data['decimals']),
        )

# Prices
class SwapPriceInfo(StrictModel):
    price_token1_per_token0: float
    price_token0_per_token1: float
    traditional_price: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'price_token1_per_token0': self.price_token1_per_token0,
            'price_token0_per_token1': self.price_token0_per_token1,
            'traditional_price': self.traditional_price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapPriceInfo':
        return cls(
            price_token1_per_token0=float(data['price_token1_per_token0']),
            price_token0_per_token1=float(data['price_token0_per_token1']),
            traditional_price=float(data['traditional_price']),
        )

class SwapPriceDetails(StrictModel):
    quoted: SwapPriceInfo
    executed: Optional[SwapPriceInfo]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'quoted': self.quoted.model_dump(),
            'executed': None if self.executed is None else self.executed.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapPriceDetails':
        return cls(
            quoted=SwapPriceInfo.from_dict(data['quoted']),
            executed=None if data['executed'] is None else SwapPriceInfo.from_dict(data['executed']),
        )

# Order data
class CowSwapOrderData(StrictModel):
    uid: str
    status: SwapStatus
    creation_date: str
    sell_token: str
    buy_token: str
    sell_amount_raw: int
    buy_amount_raw: int
    executed_sell_raw: int
    executed_buy_raw: int
    fee_amount_raw: int
    fee_token: str

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'uid': self.uid,
            'status': self.status.value,
            'creation_date': self.creation_date,
            'sell_token': self.sell_token,
            'buy_token': self.buy_token,
            'sell_amount_raw': self.sell_amount_raw,
            'buy_amount_raw': self.buy_amount_raw,
            'executed_sell_raw': self.executed_sell_raw,
            'executed_buy_raw': self.executed_buy_raw,
            'fee_amount_raw': self.fee_amount_raw,
            'fee_token': self.fee_token,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CowSwapOrderData':
        return cls(
            uid=str(data['uid']),
            status=SwapStatus(str(data['status'])),
            creation_date=str(data['creation_date']),
            sell_token=str(data['sell_token']),
            buy_token=str(data['buy_token']),
            sell_amount_raw=int(data['sell_amount_raw']),
            buy_amount_raw=int(data['buy_amount_raw']),
            executed_sell_raw=int(data['executed_sell_raw']),
            executed_buy_raw=int(data['executed_buy_raw']),
            fee_amount_raw=int(data['fee_amount_raw']),
            fee_token=str(data['fee_token']),
        )

# Swap order info
class SwapOrderInfo(StrictModel):
    uid: str
    status: SwapStatus
    network: str
    created_at: str
    sell_token: TokenInfo
    buy_token: TokenInfo
    sell_amount_raw: int
    buy_amount_raw: int
    sell_amount: float
    buy_amount: float
    executed_sell_raw: int
    executed_buy_raw: int
    executed_sell: float
    executed_buy: float
    fee_amount_raw: int
    fee_amount: float
    fee_token: str
    price: SwapPriceDetails
    url: str
    elapsed_sec: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'uid': self.uid,
            'status': self.status.value,
            'network': self.network,
            'created_at': self.created_at,
            'sell_token': self.sell_token.model_dump(),
            'buy_token': self.buy_token.model_dump(),
            'sell_amount_raw': self.sell_amount_raw,
            'buy_amount_raw': self.buy_amount_raw,
            'sell_amount': self.sell_amount,
            'buy_amount': self.buy_amount,
            'executed_sell_raw': self.executed_sell_raw,
            'executed_buy_raw': self.executed_buy_raw,
            'executed_sell': self.executed_sell,
            'executed_buy': self.executed_buy,
            'fee_amount_raw': self.fee_amount_raw,
            'fee_amount': self.fee_amount,
            'fee_token': self.fee_token,
            'price': self.price.model_dump(),
            'url': self.url,
            'elapsed_sec': self.elapsed_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapOrderInfo':
        return cls(
            uid=str(data['uid']),
            status=SwapStatus(str(data['status'])),
            network=str(data['network']),
            created_at=str(data['created_at']),
            sell_token=TokenInfo.from_dict(data['sell_token']),
            buy_token=TokenInfo.from_dict(data['buy_token']),
            sell_amount_raw=int(data['sell_amount_raw']),
            buy_amount_raw=int(data['buy_amount_raw']),
            sell_amount=float(data['sell_amount']),
            buy_amount=float(data['buy_amount']),
            executed_sell_raw=int(data['executed_sell_raw']),
            executed_buy_raw=int(data['executed_buy_raw']),
            executed_sell=float(data['executed_sell']),
            executed_buy=float(data['executed_buy']),
            fee_amount_raw=int(data['fee_amount_raw']),
            fee_amount=float(data['fee_amount']),
            fee_token=str(data['fee_token']),
            price=SwapPriceDetails.from_dict(data['price']),
            url=str(data['url']),
            elapsed_sec=int(data['elapsed_sec']),
        )

class SwapResult(StrictModel):
    ok: bool
    error: Optional[str]
    order: Optional[SwapOrderInfo]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'error': self.error,
            'order': None if self.order is None else self.order.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SwapResult':
        return cls(
            ok=bool(data['ok']),
            error=None if data['error'] is None else str(data['error']),
            order=None if data['order'] is None else SwapOrderInfo.from_dict(data['order']),
        )