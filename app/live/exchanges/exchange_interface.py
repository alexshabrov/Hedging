from abc import ABC, abstractmethod
from typing import Dict, Callable, Optional, List, Tuple

from .exchange_models import Rule, Fill
from .exchange_models import OrderSide, OrderType, OrderError, PositionSide

"""
Exchange and realtime interfaces.

Defines the required API surface for trading (orders, fills, rules) and for realtime market data (book ticker stream).
All implementations must follow strict models and return structured errors (no silent ignores).
"""

class ExchangeInterface(ABC):
    @abstractmethod
    def is_hedge_mode(self) -> bool:
        pass

    @abstractmethod
    def get_rules(self) -> Dict[str, Rule]:
        pass

    @abstractmethod
    def get_price(self, symbol: str) -> int:
        pass

    @abstractmethod
    def set_fill_callback(self, callback: Callable[[Fill], None]) -> None:
        pass

    @abstractmethod
    def set_update_callback(self, callback: Callable[[], None]) -> None:
        pass

    @abstractmethod
    def wait_for_connect(self, timeout: int = 60) -> None:
        pass
    
    @abstractmethod
    def stop(self) -> None:
        pass
    
    @abstractmethod
    def create_order(self, position_side: PositionSide, order_side: OrderSide, order_type: OrderType, symbol: str,
                     base_volume: int, price: Optional[int] = None, reduce_only: bool = False) -> Tuple[str, bool, Optional[OrderError], Optional[Exception]]:
        pass
    
    @abstractmethod
    def delete_order(self, symbol: str, order_id: str, order_type: OrderType) -> Tuple[bool, Optional[OrderError], Optional[Exception]]:
        pass

    @abstractmethod
    def modify_order(self, symbol: str, order_id: str, position_side: PositionSide, order_side: OrderSide, order_type: OrderType,
                     base_volume: int, price: Optional[int] = None) -> Tuple[bool, Optional[OrderError], Optional[Exception]]:
        pass

    @abstractmethod
    def get_user_trades(self, symbol: str, order_id: Optional[int] = None, from_id: Optional[int] = None) -> List[Fill]:
        pass


class RealtimeInterface(ABC):
    @abstractmethod
    def get_rules(self) -> Dict[str, Rule]:
        pass

    @abstractmethod
    def get_symbols(self) -> List[str]:
        pass

    @abstractmethod
    def subscribe(self, uid: str, symbol: str):
        pass

    @abstractmethod
    def unsubscribe(self, uid: str) -> None:
        pass

    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def wait_for_connect(self, timeout: int = 60) -> None:
        pass