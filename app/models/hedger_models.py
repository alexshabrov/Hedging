"""
Hedger models
Date: 2026-02-11
Version: 1.0
"""
from enum import Enum
from typing import List, Optional

from live.lib.strict_model import StrictModel
from live.logic.models import HedgeSnapshot

from dex.models.contract_models import MintResult, DecreaseLiquidityResult, CollectFeesResult, PositionState
from dex.models.swapper_models import SwapResult


### Enums ###
class HedgeRunStatus(str, Enum):
    INITIALIZED = 'initialized'
    RUNNING = 'running'
    FINISHED = 'finished'
    FAILED = 'failed'


### Models ###
class HedgerConfig(StrictModel):
    symbol: str
    rpc_url: str
    network: str
    pool_address: str
    fee_pct: float
    price_lower: float
    price_upper: float
    total_quote: float
    cex_ratio: float = 0.5
    tick_ms: int = 5
    gtx_cooldown_ms: int = 5
    entrance_timeout_ms: int = 60_000
    cowswap_api_timeout_sec: int = 10
    cowswap_wait_timeout_sec: int = 300
    cowswap_poll_interval_sec: int = 3

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'symbol': self.symbol,
            'rpc_url': self.rpc_url,
            'network': self.network,
            'pool_address': self.pool_address,
            'fee_pct': float(self.fee_pct),
            'price_lower': float(self.price_lower),
            'price_upper': float(self.price_upper),
            'total_quote': float(self.total_quote),
            'cex_ratio': float(self.cex_ratio),
            'tick_ms': int(self.tick_ms),
            'gtx_cooldown_ms': int(self.gtx_cooldown_ms),
            'entrance_timeout_ms': int(self.entrance_timeout_ms),
            'cowswap_api_timeout_sec': int(self.cowswap_api_timeout_sec),
            'cowswap_wait_timeout_sec': int(self.cowswap_wait_timeout_sec),
            'cowswap_poll_interval_sec': int(self.cowswap_poll_interval_sec),
        }


class HedgeCalcStats(StrictModel):
    base_price: float
    price_lower: float
    price_upper: float
    total_quote: float
    cex_ratio: float
    trigger_offset_pct_x10000: int
    target_offset_pct_x10000: int
    hedge_quote: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'base_price': float(self.base_price),
            'price_lower': float(self.price_lower),
            'price_upper': float(self.price_upper),
            'total_quote': float(self.total_quote),
            'cex_ratio': float(self.cex_ratio),
            'trigger_offset_pct_x10000': int(self.trigger_offset_pct_x10000),
            'target_offset_pct_x10000': int(self.target_offset_pct_x10000),
            'hedge_quote': float(self.hedge_quote),
        }


class UniswapStats(StrictModel):
    token_id: Optional[int]
    mint: Optional[MintResult]
    position: Optional[PositionState]
    decrease: Optional[DecreaseLiquidityResult]
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
            'position': None if self.position is None else self.position.model_dump(),
            'decrease': None if self.decrease is None else self.decrease.model_dump(),
            'collect': None if self.collect is None else self.collect.model_dump(),
            'rebalance': None if self.rebalance is None else self.rebalance.model_dump(),
            'initial_balance0_raw': int(self.initial_balance0_raw),
            'initial_balance1_raw': int(self.initial_balance1_raw),
            'final_balance0_raw': int(self.final_balance0_raw),
            'final_balance1_raw': int(self.final_balance1_raw),
        }


class LiveStats(StrictModel):
    last_snapshot: Optional[HedgeSnapshot]
    last_snapshot_json: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'last_snapshot': None if self.last_snapshot is None else self.last_snapshot.model_dump(),
            'last_snapshot_json': self.last_snapshot_json,
        }


class HedgerStats(StrictModel):
    status: HedgeRunStatus
    calc: HedgeCalcStats
    uniswap: UniswapStats
    live: LiveStats
    error: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'status': self.status.value,
            'calc': self.calc.model_dump(),
            'uniswap': self.uniswap.model_dump(),
            'live': self.live.model_dump(),
            'error': self.error,
        }
