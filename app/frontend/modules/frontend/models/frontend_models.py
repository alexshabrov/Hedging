"""
Frontend models
Date: 2026-02-13
Version: 1.0
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from live.lib.strict_model import StrictModel


### Enums ###
class FrontendRunLifecycle(str, Enum):
    INITIALIZED = 'initialized'
    RUNNING = 'running'
    STOPPING = 'stopping'
    FINISHED = 'finished'
    FAILED = 'failed'


### Storage models ###
class FrontendActivePositionDoc(StrictModel):
    run_id: str
    status: FrontendRunLifecycle
    created_at_ms: int
    started_at_ms: int
    updated_at_ms: int
    stop_requested: bool
    last_error: Optional[str]
    config: Dict[str, Any]
    aggregates: Dict[str, Any]
    position: Dict[str, Any]
    iterations_count: int

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendActivePositionDoc':
        if data is None:
            raise RuntimeError('FrontendActivePositionDoc.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'FrontendActivePositionDoc.from_dict: data is not dict: {type(data)}')

        return cls(
            run_id=str(data['run_id']),
            status=FrontendRunLifecycle(str(data['status'])),
            created_at_ms=int(data['created_at_ms']),
            started_at_ms=int(data['started_at_ms']),
            updated_at_ms=int(data['updated_at_ms']),
            stop_requested=bool(data['stop_requested']),
            last_error=None if data['last_error'] is None else str(data['last_error']),
            config=dict(data['config']),
            aggregates=dict(data['aggregates']),
            position=dict(data['position']),
            iterations_count=int(data['iterations_count']),
        )


class FrontendArchivePositionDoc(StrictModel):
    run_id: str
    status: FrontendRunLifecycle
    created_at_ms: int
    started_at_ms: int
    finished_at_ms: int
    archived_at_ms: int
    stop_requested: bool
    last_error: Optional[str]
    config: Dict[str, Any]
    aggregates: Dict[str, Any]
    position: Dict[str, Any]
    iterations_count: int

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendArchivePositionDoc':
        if data is None:
            raise RuntimeError('FrontendArchivePositionDoc.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'FrontendArchivePositionDoc.from_dict: data is not dict: {type(data)}')

        return cls(
            run_id=str(data['run_id']),
            status=FrontendRunLifecycle(str(data['status'])),
            created_at_ms=int(data['created_at_ms']),
            started_at_ms=int(data['started_at_ms']),
            finished_at_ms=int(data['finished_at_ms']),
            archived_at_ms=int(data['archived_at_ms']),
            stop_requested=bool(data['stop_requested']),
            last_error=None if data['last_error'] is None else str(data['last_error']),
            config=dict(data['config']),
            aggregates=dict(data['aggregates']),
            position=dict(data['position']),
            iterations_count=int(data['iterations_count']),
        )


class FrontendIterationDoc(StrictModel):
    id: str
    run_id: str
    iteration_no: int
    started_at_ms: int
    finished_at_ms: int
    status: str
    error: Optional[str]
    stats: Dict[str, Any]
    pnl: Dict[str, Any]
    swap_cost_quote: float
    costs_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_quote: float
    created_at_ms: int
    run_lifecycle: str

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationDoc':
        if data is None:
            raise RuntimeError('FrontendIterationDoc.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'FrontendIterationDoc.from_dict: data is not dict: {type(data)}')

        return cls(
            id=str(data['id']),
            run_id=str(data['run_id']),
            iteration_no=int(data['iteration_no']),
            started_at_ms=int(data['started_at_ms']),
            finished_at_ms=int(data['finished_at_ms']),
            status=str(data['status']),
            error=None if data['error'] is None else str(data['error']),
            stats=dict(data['stats']),
            pnl=dict(data['pnl']),
            swap_cost_quote=float(data['swap_cost_quote']),
            costs_quote=float(data['costs_quote']),
            pnl_without_hedge_quote=float(data['pnl_without_hedge_quote']),
            pnl_with_hedge_quote=float(data['pnl_with_hedge_quote']),
            created_at_ms=int(data['created_at_ms']),
            run_lifecycle=str(data['run_lifecycle']),
        )


### View models ###
class FrontendPositionRow(StrictModel):
    run_id: str
    symbol: str
    status: FrontendRunLifecycle
    first_started_at_ms: int
    runtime_sec: float
    runtime_dhm: str
    avg_iteration_lifetime_sec: float
    iterations_finished: int
    market_price: Optional[float]
    price_lower: Optional[float]
    price_upper: Optional[float]
    price_lower_pct: Optional[float]
    price_upper_pct: Optional[float]
    total_quote: float
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_pct: float
    pnl_without_hedge_pct: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    fees_quote: float
    price_pnl_quote: float
    hedge_pnl_quote: float
    costs_quote: float
    last_error: Optional[str]
    iterations_count: int
    is_active: bool

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'run_id': self.run_id,
            'symbol': self.symbol,
            'status': self.status.value,
            'first_started_at_ms': int(self.first_started_at_ms),
            'runtime_sec': float(self.runtime_sec),
            'runtime_dhm': self.runtime_dhm,
            'avg_iteration_lifetime_sec': float(self.avg_iteration_lifetime_sec),
            'iterations_finished': int(self.iterations_finished),
            'market_price': None if self.market_price is None else float(self.market_price),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'price_lower_pct': None if self.price_lower_pct is None else float(self.price_lower_pct),
            'price_upper_pct': None if self.price_upper_pct is None else float(self.price_upper_pct),
            'total_quote': float(self.total_quote),
            'pnl_with_hedge_quote': float(self.pnl_with_hedge_quote),
            'pnl_without_hedge_quote': float(self.pnl_without_hedge_quote),
            'pnl_with_hedge_pct': float(self.pnl_with_hedge_pct),
            'pnl_without_hedge_pct': float(self.pnl_without_hedge_pct),
            'apr_with_hedge_pct': float(self.apr_with_hedge_pct),
            'apr_without_hedge_pct': float(self.apr_without_hedge_pct),
            'fees_quote': float(self.fees_quote),
            'price_pnl_quote': float(self.price_pnl_quote),
            'hedge_pnl_quote': float(self.hedge_pnl_quote),
            'costs_quote': float(self.costs_quote),
            'last_error': self.last_error,
            'iterations_count': int(self.iterations_count),
            'is_active': bool(self.is_active),
        }


class FrontendIterationRow(StrictModel):
    id: str
    run_id: str
    iteration_no: int
    started_at_ms: int
    finished_at_ms: int
    runtime_sec: float
    status: str
    close_reason: Optional[str]
    total_quote: float
    price_lower: Optional[float]
    price_upper: Optional[float]
    pnl_with_hedge_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_pct: float
    pnl_without_hedge_pct: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    fees_quote: float
    price_pnl_quote: float
    hedge_pnl_quote: float
    costs_quote: float
    error: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'id': self.id,
            'run_id': self.run_id,
            'iteration_no': int(self.iteration_no),
            'started_at_ms': int(self.started_at_ms),
            'finished_at_ms': int(self.finished_at_ms),
            'runtime_sec': float(self.runtime_sec),
            'status': self.status,
            'close_reason': self.close_reason,
            'total_quote': float(self.total_quote),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'pnl_with_hedge_quote': float(self.pnl_with_hedge_quote),
            'pnl_without_hedge_quote': float(self.pnl_without_hedge_quote),
            'pnl_with_hedge_pct': float(self.pnl_with_hedge_pct),
            'pnl_without_hedge_pct': float(self.pnl_without_hedge_pct),
            'apr_with_hedge_pct': float(self.apr_with_hedge_pct),
            'apr_without_hedge_pct': float(self.apr_without_hedge_pct),
            'fees_quote': float(self.fees_quote),
            'price_pnl_quote': float(self.price_pnl_quote),
            'hedge_pnl_quote': float(self.hedge_pnl_quote),
            'costs_quote': float(self.costs_quote),
            'error': self.error,
        }


class FrontendRunDetailsView(StrictModel):
    position: FrontendPositionRow
    iterations: List[FrontendIterationRow]

    def model_dump(self) -> dict:  # type: ignore[override]
        out_iterations = []
        for item in self.iterations:
            out_iterations.append(item.model_dump())

        return {
            'position': self.position.model_dump(),
            'iterations': out_iterations,
        }


class FrontendIterationDetailsView(StrictModel):
    row: FrontendIterationRow
    stats: Dict[str, Any]
    pnl: Dict[str, Any]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'row': self.row.model_dump(),
            'stats': dict(self.stats),
            'pnl': dict(self.pnl),
        }


class FrontendDashboardView(StrictModel):
    active_runs: int
    finished_iterations: int
    total_invested_quote: float
    total_pnl_with_hedge_quote: float
    total_pnl_without_hedge_quote: float
    total_costs_quote: float
    apr_with_hedge_pct: float
    apr_without_hedge_pct: float
    avg_apr_with_hedge_pct: float
    avg_iteration_lifetime_sec: float

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'active_runs': int(self.active_runs),
            'finished_iterations': int(self.finished_iterations),
            'total_invested_quote': float(self.total_invested_quote),
            'total_pnl_with_hedge_quote': float(self.total_pnl_with_hedge_quote),
            'total_pnl_without_hedge_quote': float(self.total_pnl_without_hedge_quote),
            'total_costs_quote': float(self.total_costs_quote),
            'apr_with_hedge_pct': float(self.apr_with_hedge_pct),
            'apr_without_hedge_pct': float(self.apr_without_hedge_pct),
            'avg_apr_with_hedge_pct': float(self.avg_apr_with_hedge_pct),
            'avg_iteration_lifetime_sec': float(self.avg_iteration_lifetime_sec),
        }
