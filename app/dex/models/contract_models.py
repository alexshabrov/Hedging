"""
Contract models
Date: 2026-02-09
Version: 1.0
"""
from typing import Optional, List
from dex.lib.strict_model import StrictModel

# Mint result
class MintResult(StrictModel):
    ok: bool
    tx_hash: str
    token_id: Optional[int]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'token_id': self.token_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MintResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            token_id=None if data['token_id'] is None else int(data['token_id']),
        )

# Decrease liquidity result
class DecreaseLiquidityResult(StrictModel):
    ok: bool
    tx_hash: str
    simulated: List

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'simulated': list(self.simulated),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DecreaseLiquidityResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            simulated=list(data['simulated']),
        )

# Collect fees result
class CollectFeesResult(StrictModel):
    ok: bool
    tx_hash: str
    simulated: List

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'simulated': list(self.simulated),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CollectFeesResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            simulated=list(data['simulated']),
        )

# Position state
class PositionState(StrictModel):
    token0: str
    token1: str
    fee: float
    tick_lower: int
    tick_upper: int
    price_lower: float
    price_upper: float
    price_current: float
    liquidity: int
    amount0: float
    amount1: float
    uncollected0: float
    uncollected1: float
    amount_base: float
    amount_quote: float
    uncollected_base: float
    uncollected_quote: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'token0': self.token0,
            'token1': self.token1,
            'fee': self.fee,
            'tick_lower': self.tick_lower,
            'tick_upper': self.tick_upper,
            'price_lower': self.price_lower,
            'price_upper': self.price_upper,
            'price_current': self.price_current,
            'liquidity': self.liquidity,
            'amount0': self.amount0,
            'amount1': self.amount1,
            'uncollected0': self.uncollected0,
            'uncollected1': self.uncollected1,
            'amount_base': self.amount_base,
            'amount_quote': self.amount_quote,
            'uncollected_base': self.uncollected_base,
            'uncollected_quote': self.uncollected_quote,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'PositionState':
        return cls(
            token0=str(data['token0']),
            token1=str(data['token1']),
            fee=float(data['fee']),
            tick_lower=int(data['tick_lower']),
            tick_upper=int(data['tick_upper']),
            price_lower=float(data['price_lower']),
            price_upper=float(data['price_upper']),
            price_current=float(data['price_current']),
            liquidity=int(data['liquidity']),
            amount0=float(data['amount0']),
            amount1=float(data['amount1']),
            uncollected0=float(data['uncollected0']),
            uncollected1=float(data['uncollected1']),
            amount_base=float(data['amount_base']),
            amount_quote=float(data['amount_quote']),
            uncollected_base=float(data['uncollected_base']),
            uncollected_quote=float(data['uncollected_quote']),
        )