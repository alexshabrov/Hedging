from enum import Enum
from typing import List, Dict, Optional, Any

from ..lib.strict_model import StrictModel

"""
Strict exchange-level models.

Contains enums and StrictModel schemas for rules, orders, fills, realtime BookTicker, and position-loop messages.
All numeric values are represented as integer "units" aligned to exchange rules (no floats in core logic).
"""

# Errors
class OrderError(str, Enum):
    INPUT_FORMAT = "INPUT_FORMAT"
    PRICE_VIOLATION = "PRICE_VIOLATION"
    VOLUME_VIOLATION = "VOLUME_VIOLATION"
    DOES_NOT_EXIST = "DOES_NOT_EXIST"
    NETWORK = "NETWORK"
    EXCHANGE = "EXCHANGE"
    NEEDS_REPAIR = "NEEDS_REPAIR"

# Exchange level
class Rule(StrictModel):
    price_step: str
    lot_step: str
    min_base_volume: str
    max_base_volume: str
    min_quote_volume: Optional[str] = None
    max_quote_volume: Optional[str] = None

    def model_dump(self):
        return {
            'price_step': self.price_step,
            'lot_step': self.lot_step,
            'min_base_volume': self.min_base_volume,
            'max_base_volume': self.max_base_volume,
            'min_quote_volume': self.min_quote_volume,
            'max_quote_volume': self.max_quote_volume
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Rule':
        return cls(
            price_step=data['price_step'],
            lot_step=data['lot_step'],
            min_base_volume=data['min_base_volume'],
            max_base_volume=data['max_base_volume'],
            min_quote_volume=data.get('min_quote_volume'),
            max_quote_volume=data.get('max_quote_volume')
        )

    @staticmethod
    def _parse_decimal_fraction(value, name):
        if value is None:
            raise RuntimeError(f'Rule: {name} is None')

        if not isinstance(value, str):
            raise RuntimeError(f'Rule: {name} is not str: {type(value)}')

        s = value.strip()
        if not s:
            raise RuntimeError(f'Rule: empty {name}')

        if 'e' in s.lower():
            raise RuntimeError(f'Rule: exponent is not allowed for {name}: {value}')

        if s[0] == '+':
            s = s[1:]

        if s.startswith('-'):
            raise RuntimeError(f'Rule: negative {name} is not allowed: {value}')

        if s.count('.') > 1:
            raise RuntimeError(f'Rule: bad {name}: {value}')

        if '.' in s:
            whole, frac = s.split('.', 1)
        else:
            whole, frac = s, ''

        if whole == '':
            whole = '0'

        if not whole.isdigit():
            raise RuntimeError(f'Rule: bad {name} integer part: {value}')

        if frac != '' and not frac.isdigit():
            raise RuntimeError(f'Rule: bad {name} fractional part: {value}')

        den = 10 ** len(frac)
        num = int(whole) * den

        if frac != '':
            num += int(frac)

        if num <= 0:
            raise RuntimeError(f'Rule: non-positive {name}: {value}')

        return num, den

    @staticmethod
    def _format_from_scale(units, scale, name):
        if units is None:
            raise RuntimeError(f'Rule: {name} units is None')

        if not isinstance(units, int):
            raise RuntimeError(f'Rule: {name} units is not int: {type(units)}')

        if units <= 0:
            raise RuntimeError(f'Rule: {name} units must be > 0, got: {units}')

        if scale is None:
            raise RuntimeError(f'Rule: {name} scale is None')

        if not isinstance(scale, int):
            raise RuntimeError(f'Rule: {name} scale is not int: {type(scale)}')

        if scale < 0:
            raise RuntimeError(f'Rule: {name} scale must be >= 0, got: {scale}')

        if scale == 0:
            return str(units)

        s = str(units)
        if len(s) <= scale:
            s = '0' * (scale - len(s) + 1) + s

        head = s[:-scale]
        tail = s[-scale:]

        tail = tail.rstrip('0')

        if tail == '':
            return head

        return head + '.' + tail

    @staticmethod
    def _get_scale(value, name):
        if value is None:
            raise RuntimeError(f'Rule: {name} is None')

        if not isinstance(value, str):
            raise RuntimeError(f'Rule: {name} is not str: {type(value)}')

        s = value.strip()
        if not s:
            raise RuntimeError(f'Rule: empty {name}')

        if 'e' in s.lower():
            raise RuntimeError(f'Rule: exponent is not allowed for {name}: {value}')

        if s[0] == '+':
            s = s[1:]

        if s.startswith('-'):
            raise RuntimeError(f'Rule: negative {name} is not allowed: {value}')

        if s.count('.') > 1:
            raise RuntimeError(f'Rule: bad {name}: {value}')

        if '.' not in s:
            if not s.isdigit():
                raise RuntimeError(f'Rule: bad {name}: {value}')
            return 0

        whole, frac = s.split('.', 1)

        if whole == '':
            whole = '0'

        if not whole.isdigit():
            raise RuntimeError(f'Rule: bad {name} integer part: {value}')

        if frac != '' and not frac.isdigit():
            raise RuntimeError(f'Rule: bad {name} fractional part: {value}')

        # Step strings from Binance often come with trailing zeros like "0.01000000".
        # Trailing zeros must NOT affect scale, otherwise int<->string conversion breaks.
        frac = frac.rstrip('0')

        return len(frac)

    @staticmethod
    def _to_units(value, step, name):
        v_num, v_den = Rule._parse_decimal_fraction(value, name)
        s_num, s_den = Rule._parse_decimal_fraction(step, f'{name}_step')

        num = int(v_num) * int(s_den)
        den = int(v_den) * int(s_num)

        if den <= 0:
            raise RuntimeError(f'Rule: bad denominator for {name}')

        if num % den != 0:
            raise RuntimeError(f'Rule: {name} is not aligned to step: value={value} step={step}')

        units = num // den

        if units <= 0:
            raise RuntimeError(f'Rule: non-positive units for {name}: {units}')

        return int(units)

    def price_to_units(self, price):
        return Rule._to_units(price, self.price_step, 'price')

    def volume_to_units(self, volume):
        return Rule._to_units(volume, self.lot_step, 'base_volume')

    def price_from_units(self, units):
        scale = Rule._get_scale(self.price_step, 'price_step')
        return Rule._format_from_scale(int(units), int(scale), 'price')

    def volume_from_units(self, units):
        scale = Rule._get_scale(self.lot_step, 'lot_step')
        return Rule._format_from_scale(int(units), int(scale), 'base_volume')


# Order level
class OrderSide(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'

class Fill(StrictModel):
    symbol: str
    order_id: str
    trade_id: str
    time_ms: int
    arrived_ms: int
    price: int
    base_volume: int
    from_api: bool
    side: OrderSide
    is_filled: bool

    def model_dump(self):
        return {
            'symbol': self.symbol,
            'order_id': self.order_id,
            'trade_id': self.trade_id,
            'time_ms': self.time_ms,
            'arrived_ms': self.arrived_ms,
            'price': self.price,
            'base_volume': self.base_volume,
            'from_api': self.from_api,
            'side': self.side.value,
            'is_filled': bool(self.is_filled),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Fill':
        return cls(
            symbol=data['symbol'],
            order_id=data['order_id'],
            trade_id=data['trade_id'],
            time_ms=data['time_ms'],
            arrived_ms=data['arrived_ms'],
            price=data['price'],
            base_volume=data['base_volume'],
            from_api=data['from_api'],
            side=OrderSide(data['side']),
            is_filled=bool(data['is_filled']),
        )

class OrderType(str, Enum):
    MARKET = 'MARKET'
    LIMIT = 'LIMIT'
    STOP_MARKET = 'STOP_MARKET'
    STOPLOSS = 'STOPLOSS'
    TAKE_PROFIT = 'TAKE_PROFIT'

class Order(StrictModel):
    symbol: str
    order_id: str
    order_side: OrderSide
    order_type: OrderType
    base_volume: Optional[int] = 0
    filled_volume: Optional[int] = 0
    price: Optional[int] = 0
   
    def model_dump(self):
        return {
            'symbol': self.symbol,
            'order_id': self.order_id,
            'order_side': self.order_side.value,
            'order_type': self.order_type.value,
            'base_volume': self.base_volume,
            'filled_volume': self.filled_volume,
            'price': self.price,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Order':
        return cls(
            symbol=data['symbol'],
            order_id=data['order_id'],
            order_side=OrderSide(data['order_side']),
            order_type=OrderType(data['order_type']),
            base_volume=data.get('base_volume'),
            filled_volume=data.get('filled_volume'),
            price=data.get('price'),
        )
    
# Position level
class PositionSide(str, Enum):
    LONG = 'LONG'
    SHORT = 'SHORT'
    BOTH = 'BOTH'

class Position(StrictModel):
    symbol: str
    base_volume: str
    quote_volume: str
    unrealized_pnl: str
    open_orders: List[Order] = []
  
    def model_dump(self):
        return {
            'symbol': self.symbol,
            'base_volume': self.base_volume,
            'quote_volume': self.quote_volume,
            'unrealized_pnl': self.unrealized_pnl,
            'open_orders': [order.model_dump() for order in self.open_orders],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        return cls(
            symbol=data['symbol'],
            base_volume=data['base_volume'],
            quote_volume=data['quote_volume'],
            unrealized_pnl=data['unrealized_pnl'],
            open_orders=[Order.from_dict(order) for order in data['open_orders']],
        )

class Balance(StrictModel):
    asset: str
    free: str
    locked: str

    def model_dump(self):
        return {
            'asset': self.asset,
            'free': self.free,
            'locked': self.locked,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Balance':
        return cls(
            asset=data['asset'],
            free=data['free'],
            locked=data['locked'],
        )
    
class AccountData(StrictModel):
    positions: List[Position] = []
    balances: List[Balance] = []
    
    def model_dump(self):
        return {
            'positions': [position.model_dump() for position in self.positions],
            'balances': [balance.model_dump() for balance in self.balances],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AccountData':
        return cls(
            positions=[Position.from_dict(position) for position in data['positions']],
            balances=[Balance.from_dict(balance) for balance in data['balances']],
        )

class BookTicker(StrictModel):
    symbol: str
    time_ms: int
    bid_price: int
    bid_volume: int
    ask_price: int
    ask_volume: int

    def model_dump(self):
        return {
            'symbol': self.symbol,
            'time_ms': self.time_ms,
            'bid_price': self.bid_price,
            'bid_volume': self.bid_volume,
            'ask_price': self.ask_price,
            'ask_volume': self.ask_volume,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'BookTicker':
        return cls(
            symbol=data['symbol'],
            time_ms=data['time_ms'],
            bid_price=data['bid_price'],
            bid_volume=data['bid_volume'],
            ask_price=data['ask_price'],
            ask_volume=data['ask_volume'],
        )


### Position loop models ###
class PositionCommandType(str, Enum):
    CHASE = 'CHASE'
    STOP = 'STOP'


class ChaseCommand(StrictModel):
    cmd: PositionCommandType
    symbol: str
    position_side: PositionSide
    side: OrderSide
    base_volume: int
    cmd_id: str
    time_ms: int

    def model_dump(self):
        return {
            'cmd': self.cmd.value,
            'symbol': self.symbol,
            'position_side': self.position_side.value,
            'side': self.side.value,
            'base_volume': self.base_volume,
            'cmd_id': self.cmd_id,
            'time_ms': self.time_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ChaseCommand':
        return cls(
            cmd=PositionCommandType(data['cmd']),
            symbol=data['symbol'],
            position_side=PositionSide(data['position_side']),
            side=OrderSide(data['side']),
            base_volume=data['base_volume'],
            cmd_id=data['cmd_id'],
            time_ms=data['time_ms'],
        )


class PositionEventType(str, Enum):
    CHASE_STARTED = 'CHASE_STARTED'
    CHASE_ORDER_CREATED = 'CHASE_ORDER_CREATED'
    CHASE_ORDER_DELETED = 'CHASE_ORDER_DELETED'
    CHASE_PRICE_VIOLATION = 'CHASE_PRICE_VIOLATION'
    CHASE_EXCHANGE_ERROR = 'CHASE_EXCHANGE_ERROR'
    CHASE_FILLED_BY_DELETION = 'CHASE_FILLED_BY_DELETION'
    CHASE_AGG_FILL = 'CHASE_AGG_FILL'
    CHASE_DONE = 'CHASE_DONE'
    ENTRANCE_TIMEOUT = 'ENTRANCE_TIMEOUT'

class PositionStopCommand(StrictModel):
    cmd: PositionCommandType
    symbol: str
    time_ms: int
    
    def model_dump(self):
        return {
            'cmd': self.cmd.value,
            'symbol': self.symbol,
            'time_ms': int(self.time_ms),
        }


class PositionEvent(StrictModel):
    event_type: PositionEventType
    symbol: str
    time_ms: int
    data: Dict[str, Any] = {}

    def model_dump(self):
        return {
            'event_type': self.event_type.value,
            'symbol': self.symbol,
            'time_ms': self.time_ms,
            'data': dict(self.data) if self.data is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PositionEvent':
        return cls(
            event_type=PositionEventType(data['event_type']),
            symbol=data['symbol'],
            time_ms=data['time_ms'],
            data=data.get('data', {}),
        )


class ChaseResponse(StrictModel):
    cmd_id: str
    symbol: str
    ok: bool
    started_ms: int
    finished_ms: int
    target_base_volume: int
    filled_base_volume: int
    error: Optional[str] = None

    def model_dump(self):
        return {
            'cmd_id': self.cmd_id,
            'symbol': self.symbol,
            'ok': self.ok,
            'started_ms': self.started_ms,
            'finished_ms': self.finished_ms,
            'target_base_volume': self.target_base_volume,
            'filled_base_volume': self.filled_base_volume,
            'error': self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ChaseResponse':
        return cls(
            cmd_id=data['cmd_id'],
            symbol=data['symbol'],
            ok=data['ok'],
            started_ms=data['started_ms'],
            finished_ms=data['finished_ms'],
            target_base_volume=data['target_base_volume'],
            filled_base_volume=data['filled_base_volume'],
            error=data.get('error'),
        )