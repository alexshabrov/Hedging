"""
Contract models
Date: 2026-02-09
Version: 1.0
"""
from typing import Optional
from dex.lib.strict_model import StrictModel
from dex.models.swapper_models import SwapResult

# Mint result
class MintResult(StrictModel):
    ok: bool
    tx_hash: str
    token_id: Optional[int]
    gas_used: Optional[int]
    gas_price_wei: Optional[int]
    gas_cost_wei: Optional[int]
    gas_cost_eth: Optional[float]
    amount0_used_raw: Optional[int]
    amount1_used_raw: Optional[int]
    amount0_used: Optional[float]
    amount1_used: Optional[float]
    amount_base: Optional[float]
    amount_quote: Optional[float]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'token_id': self.token_id,
            'gas_used': self.gas_used,
            'gas_price_wei': self.gas_price_wei,
            'gas_cost_wei': self.gas_cost_wei,
            'gas_cost_eth': self.gas_cost_eth,
            'amount0_used_raw': self.amount0_used_raw,
            'amount1_used_raw': self.amount1_used_raw,
            'amount0_used': self.amount0_used,
            'amount1_used': self.amount1_used,
            'amount_base': self.amount_base,
            'amount_quote': self.amount_quote,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MintResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            token_id=None if data['token_id'] is None else int(data['token_id']),
            gas_used=None if data['gas_used'] is None else int(data['gas_used']),
            gas_price_wei=None if data['gas_price_wei'] is None else int(data['gas_price_wei']),
            gas_cost_wei=None if data['gas_cost_wei'] is None else int(data['gas_cost_wei']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
            amount0_used_raw=None if data['amount0_used_raw'] is None else int(data['amount0_used_raw']),
            amount1_used_raw=None if data['amount1_used_raw'] is None else int(data['amount1_used_raw']),
            amount0_used=None if data['amount0_used'] is None else float(data['amount0_used']),
            amount1_used=None if data['amount1_used'] is None else float(data['amount1_used']),
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
        )

# Decrease liquidity result
class DecreaseLiquidityResult(StrictModel):
    ok: bool
    tx_hash: str
    gas_used: Optional[int]
    gas_price_wei: Optional[int]
    gas_cost_wei: Optional[int]
    gas_cost_eth: Optional[float]
    amount0_simulated_raw: Optional[int]
    amount1_simulated_raw: Optional[int]
    amount0_simulated: Optional[float]
    amount1_simulated: Optional[float]
    amount_base: Optional[float]
    amount_quote: Optional[float]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'gas_used': self.gas_used,
            'gas_price_wei': self.gas_price_wei,
            'gas_cost_wei': self.gas_cost_wei,
            'gas_cost_eth': self.gas_cost_eth,
            'amount0_simulated_raw': self.amount0_simulated_raw,
            'amount1_simulated_raw': self.amount1_simulated_raw,
            'amount0_simulated': self.amount0_simulated,
            'amount1_simulated': self.amount1_simulated,
            'amount_base': self.amount_base,
            'amount_quote': self.amount_quote,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DecreaseLiquidityResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            gas_used=None if data['gas_used'] is None else int(data['gas_used']),
            gas_price_wei=None if data['gas_price_wei'] is None else int(data['gas_price_wei']),
            gas_cost_wei=None if data['gas_cost_wei'] is None else int(data['gas_cost_wei']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
            amount0_simulated_raw=None if data['amount0_simulated_raw'] is None else int(data['amount0_simulated_raw']),
            amount1_simulated_raw=None if data['amount1_simulated_raw'] is None else int(data['amount1_simulated_raw']),
            amount0_simulated=None if data['amount0_simulated'] is None else float(data['amount0_simulated']),
            amount1_simulated=None if data['amount1_simulated'] is None else float(data['amount1_simulated']),
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
        )

# Collect fees result
class CollectFeesResult(StrictModel):
    ok: bool
    tx_hash: str
    gas_used: Optional[int]
    gas_price_wei: Optional[int]
    gas_cost_wei: Optional[int]
    gas_cost_eth: Optional[float]
    amount0_simulated_raw: Optional[int]
    amount1_simulated_raw: Optional[int]
    amount0_simulated: Optional[float]
    amount1_simulated: Optional[float]
    amount_base: Optional[float]
    amount_quote: Optional[float]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'ok': self.ok,
            'tx_hash': self.tx_hash,
            'gas_used': self.gas_used,
            'gas_price_wei': self.gas_price_wei,
            'gas_cost_wei': self.gas_cost_wei,
            'gas_cost_eth': self.gas_cost_eth,
            'amount0_simulated_raw': self.amount0_simulated_raw,
            'amount1_simulated_raw': self.amount1_simulated_raw,
            'amount0_simulated': self.amount0_simulated,
            'amount1_simulated': self.amount1_simulated,
            'amount_base': self.amount_base,
            'amount_quote': self.amount_quote,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CollectFeesResult':
        return cls(
            ok=bool(data['ok']),
            tx_hash=str(data['tx_hash']),
            gas_used=None if data['gas_used'] is None else int(data['gas_used']),
            gas_price_wei=None if data['gas_price_wei'] is None else int(data['gas_price_wei']),
            gas_cost_wei=None if data['gas_cost_wei'] is None else int(data['gas_cost_wei']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
            amount0_simulated_raw=None if data['amount0_simulated_raw'] is None else int(data['amount0_simulated_raw']),
            amount1_simulated_raw=None if data['amount1_simulated_raw'] is None else int(data['amount1_simulated_raw']),
            amount0_simulated=None if data['amount0_simulated'] is None else float(data['amount0_simulated']),
            amount1_simulated=None if data['amount1_simulated'] is None else float(data['amount1_simulated']),
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
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


class DexRunStats(StrictModel):
    token_id: Optional[int]
    mint: Optional[MintResult]
    mint_tx_timestamp_ms: int
    position: Optional[PositionState]
    decrease: Optional[DecreaseLiquidityResult]
    decrease_tx_timestamp_ms: int
    collect: Optional[CollectFeesResult]
    rebalance: Optional[SwapResult]
    initial_balance0_raw: int
    initial_balance1_raw: int
    final_balance0_raw: int
    final_balance1_raw: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'token_id': self.token_id,
            'mint': None if self.mint is None else self.mint.model_dump(),
            'mint_tx_timestamp_ms': int(self.mint_tx_timestamp_ms),
            'position': None if self.position is None else self.position.model_dump(),
            'decrease': None if self.decrease is None else self.decrease.model_dump(),
            'decrease_tx_timestamp_ms': int(self.decrease_tx_timestamp_ms),
            'collect': None if self.collect is None else self.collect.model_dump(),
            'rebalance': None if self.rebalance is None else self.rebalance.model_dump(),
            'initial_balance0_raw': int(self.initial_balance0_raw),
            'initial_balance1_raw': int(self.initial_balance1_raw),
            'final_balance0_raw': int(self.final_balance0_raw),
            'final_balance1_raw': int(self.final_balance1_raw),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DexRunStats':
        if data is None:
            raise RuntimeError('DexRunStats.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'DexRunStats.from_dict: data is not dict: {type(data)}')

        return cls(
            token_id=None if data['token_id'] is None else int(data['token_id']),
            mint=None if data['mint'] is None else MintResult.from_dict(data['mint']),
            mint_tx_timestamp_ms=int(data['mint_tx_timestamp_ms']),
            position=None if data['position'] is None else PositionState.from_dict(data['position']),
            decrease=None if data['decrease'] is None else DecreaseLiquidityResult.from_dict(data['decrease']),
            decrease_tx_timestamp_ms=int(data['decrease_tx_timestamp_ms']),
            collect=None if data['collect'] is None else CollectFeesResult.from_dict(data['collect']),
            rebalance=None if data['rebalance'] is None else SwapResult.from_dict(data['rebalance']),
            initial_balance0_raw=int(data['initial_balance0_raw']),
            initial_balance1_raw=int(data['initial_balance1_raw']),
            final_balance0_raw=int(data['final_balance0_raw']),
            final_balance1_raw=int(data['final_balance1_raw']),
        )