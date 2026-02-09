from typing import Type

from .exchange_interface import ExchangeInterface
from .exchange_interface import RealtimeInterface
from .binance.binance_trading import Binance
from .binance.binance_realtime import BinanceRealtime

"""
Factory helpers for exchange and realtime backends.

Maps a short exchange name into concrete implementation classes (no fallback, unknown names are errors).
"""

def get_exchange_class(name) -> Type[ExchangeInterface]:
    if name == 'Binance':
        return Binance
    else:
        raise ValueError(f'Unknown exchange {name}')


def get_realtime_class(name) -> Type[RealtimeInterface]:
    if name == 'Binance':
        return BinanceRealtime
    else:
        raise ValueError(f'Unknown realtime {name}')