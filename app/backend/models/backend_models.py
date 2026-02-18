"""
Backend models
Date: 2026-02-13
Version: 1.0
"""
from enum import Enum
from typing import List, Optional
import orjson

from live.lib.strict_model import StrictModel
from backend.models.hedger_models import HedgerConfig, HedgerStats
from backend.models.mock_hedge_models import MockHedgeBoundary
from backend.models.mock_hedge_models import MockHedgeBoundary
from backend.modules.hedger_helper import HedgerPnlStats


### Enums ###
class BackendRunLifecycle(str, Enum):
    INITIALIZED = 'initialized'
    RUNNING = 'running'
    STOPPING = 'stopping'
    FINISHED = 'finished'
    FAILED = 'failed'


### Models ###
class BackendStartRunRequest(StrictModel):
    template_id: Optional[str] = None
    config: HedgerConfig

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'template_id': self.template_id,
            'config': self.config.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'BackendStartRunRequest':
        if data is None:
            raise RuntimeError('BackendStartRunRequest.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'BackendStartRunRequest.from_dict: data is not dict: {type(data)}')
        if 'config' not in data:
            raise RuntimeError('BackendStartRunRequest.from_dict: config is missing')

        template_id = None
        if 'template_id' in data and data['template_id'] is not None:
            template_id = str(data['template_id'])

        return cls(
            template_id=template_id,
            config=HedgerConfig.from_dict(data['config']),
        )

    @classmethod
    def from_json(cls, raw) -> 'BackendStartRunRequest':
        if raw is None:
            raise RuntimeError('BackendStartRunRequest.from_json: raw is None')

        if isinstance(raw, bytes):
            payload = orjson.loads(raw)
        elif isinstance(raw, bytearray):
            payload = orjson.loads(bytes(raw))
        elif isinstance(raw, str):
            payload = orjson.loads(raw.encode('utf-8'))
        else:
            raise RuntimeError(f'BackendStartRunRequest.from_json: raw must be bytes/bytearray/str, got: {type(raw)}')

        if not isinstance(payload, dict):
            raise RuntimeError(f'BackendStartRunRequest.from_json: payload is not dict: {type(payload)}')

        return cls.from_dict(payload)


class BackendRunAggregates(StrictModel):
    iterations_finished: int
    iterations_failed: int
    sum_pool_hold_seconds: float
    sum_cex_pnl_quote: float
    sum_price_pnl_quote: float
    sum_fees_quote: float
    sum_costs_quote: float
    sum_total_pnl_with_hedge_quote: float
    sum_total_pnl_without_hedge_quote: float
    close_trigger_upper_count: int
    close_trigger_lower_count: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'iterations_finished': int(self.iterations_finished),
            'iterations_failed': int(self.iterations_failed),
            'sum_pool_hold_seconds': float(self.sum_pool_hold_seconds),
            'sum_cex_pnl_quote': float(self.sum_cex_pnl_quote),
            'sum_price_pnl_quote': float(self.sum_price_pnl_quote),
            'sum_fees_quote': float(self.sum_fees_quote),
            'sum_costs_quote': float(self.sum_costs_quote),
            'sum_total_pnl_with_hedge_quote': float(self.sum_total_pnl_with_hedge_quote),
            'sum_total_pnl_without_hedge_quote': float(self.sum_total_pnl_without_hedge_quote),
            'close_trigger_upper_count': int(self.close_trigger_upper_count),
            'close_trigger_lower_count': int(self.close_trigger_lower_count),
        }

    @classmethod
    def empty(cls) -> 'BackendRunAggregates':
        return cls(
            iterations_finished=0,
            iterations_failed=0,
            sum_pool_hold_seconds=0.0,
            sum_cex_pnl_quote=0.0,
            sum_price_pnl_quote=0.0,
            sum_fees_quote=0.0,
            sum_costs_quote=0.0,
            sum_total_pnl_with_hedge_quote=0.0,
            sum_total_pnl_without_hedge_quote=0.0,
            close_trigger_upper_count=0,
            close_trigger_lower_count=0,
        )


class BackendIterationRecord(StrictModel):
    id: str
    run_id: str
    iteration_no: int
    started_at_ms: int
    finished_at_ms: int
    status: str
    close_trigger_side: Optional[MockHedgeBoundary]
    error: Optional[str]
    stats: HedgerStats
    pnl: HedgerPnlStats
    swap_cost_quote: float
    costs_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_quote: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'id': self.id,
            'run_id': self.run_id,
            'iteration_no': int(self.iteration_no),
            'started_at_ms': int(self.started_at_ms),
            'finished_at_ms': int(self.finished_at_ms),
            'status': self.status,
            'close_trigger_side': None if self.close_trigger_side is None else str(self.close_trigger_side.value),
            'error': self.error,
            'stats': self.stats.model_dump(),
            'pnl': self.pnl.model_dump(),
            'swap_cost_quote': float(self.swap_cost_quote),
            'costs_quote': float(self.costs_quote),
            'pnl_without_hedge_quote': float(self.pnl_without_hedge_quote),
            'pnl_with_hedge_quote': float(self.pnl_with_hedge_quote),
        }


class BackendPositionView(StrictModel):
    run_id: str
    symbol: str
    first_started_at_ms: int
    runtime_sec: float
    runtime_dhm: str
    avg_iteration_lifetime_sec: float
    iterations_finished: int
    close_trigger_upper_count: int = 0
    close_trigger_lower_count: int = 0
    close_trigger_total_count: int = 0
    close_trigger_upper_pct: float = 0.0
    close_trigger_lower_pct: float = 0.0
    status: BackendRunLifecycle
    market_price: Optional[float]
    price_lower: Optional[float]
    price_upper: Optional[float]
    price_lower_pct: Optional[float]
    price_upper_pct: Optional[float]
    total_quote: float
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    fees_quote: float
    price_pnl_quote: float
    hedge_pnl_quote: float
    costs_quote: float
    token_id: Optional[int]
    last_error: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'run_id': self.run_id,
            'symbol': self.symbol,
            'first_started_at_ms': int(self.first_started_at_ms),
            'runtime_sec': float(self.runtime_sec),
            'runtime_dhm': self.runtime_dhm,
            'avg_iteration_lifetime_sec': float(self.avg_iteration_lifetime_sec),
            'iterations_finished': int(self.iterations_finished),
            'close_trigger_upper_count': int(self.close_trigger_upper_count),
            'close_trigger_lower_count': int(self.close_trigger_lower_count),
            'close_trigger_total_count': int(self.close_trigger_total_count),
            'close_trigger_upper_pct': float(self.close_trigger_upper_pct),
            'close_trigger_lower_pct': float(self.close_trigger_lower_pct),
            'status': self.status.value,
            'market_price': None if self.market_price is None else float(self.market_price),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'price_lower_pct': None if self.price_lower_pct is None else float(self.price_lower_pct),
            'price_upper_pct': None if self.price_upper_pct is None else float(self.price_upper_pct),
            'total_quote': float(self.total_quote),
            'pnl_with_hedge_quote': float(self.pnl_with_hedge_quote),
            'pnl_without_hedge_quote': float(self.pnl_without_hedge_quote),
            'apr_with_hedge_pct': float(self.apr_with_hedge_pct),
            'apr_without_hedge_pct': float(self.apr_without_hedge_pct),
            'fees_quote': float(self.fees_quote),
            'price_pnl_quote': float(self.price_pnl_quote),
            'hedge_pnl_quote': float(self.hedge_pnl_quote),
            'costs_quote': float(self.costs_quote),
            'token_id': None if self.token_id is None else int(self.token_id),
            'last_error': self.last_error,
        }


class BackendRunDetailsView(StrictModel):
    template_id: Optional[str]
    config: HedgerConfig
    position: BackendPositionView
    iterations: List[BackendIterationRecord]

    def model_dump(self) -> dict:  # type: ignore[override]
        out_iterations = []
        for item in self.iterations:
            out_iterations.append(item.model_dump())

        return {
            'template_id': self.template_id,
            'config': self.config.model_dump(),
            'position': self.position.model_dump(),
            'iterations': out_iterations,
        }
