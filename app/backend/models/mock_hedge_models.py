"""
Mock hedge models
Date: 2026-02-18
Version: 1.0
"""
from enum import Enum

from live.lib.strict_model import StrictModel


### Enums ###
class MockHedgeBoundary(str, Enum):
    LOWER = 'lower'
    UPPER = 'upper'


### Models ###
class MockHedgeSession(StrictModel):
    uid: str
    symbol: str
    start_price: float
    target_lower_price: float
    target_upper_price: float
    started_at_ms: int


class MockHedgeTriggerEvent(StrictModel):
    uid: str
    symbol: str
    boundary: MockHedgeBoundary
    start_price: float
    target_lower_price: float
    target_upper_price: float
    trigger_price: float
    triggered_at_ms: int
