from enum import Enum
from typing import List, Optional

from ..lib.strict_model import StrictModel


### Enums ###
class HedgeMode(str, Enum):
    LONG_ONLY = 'long_only'
    SHORT_ONLY = 'short_only'
    BOTH = 'both'


class HedgeStatus(str, Enum):
    INITIALIZED = 'initialized'
    WAITING_TRIGGER = 'waiting_trigger'
    EXECUTING = 'executing'
    ACTIVE = 'active'
    CLOSING = 'closing'
    CLOSED = 'closed'
    FAILED = 'failed'


class HedgeLeg(str, Enum):
    LONG = 'long'
    SHORT = 'short'


class HedgeCloseReason(str, Enum):
    TARGET = 'target'
    NEUTRAL = 'neutral'
    FORCED = 'forced'


class HedgeChaseKind(str, Enum):
    OPEN = 'open'
    CLOSE = 'close'


### Models ###
class HedgeOffsetsPctX10000(StrictModel):
    # Offsets as percent * 10000 (1 == 0.0001%)
    long: int
    short: int
    
    def model_dump(self):
        return {
            'long': int(self.long),
            'short': int(self.short),
        }


class HedgeExecutionParams(StrictModel):
    # Engine tick parameters
    tick_ms: int

    # Position chase parameters
    gtx_cooldown_ms: int

    # Safety timeouts
    entrance_timeout_ms: int
    
    def model_dump(self):
        return {
            'tick_ms': int(self.tick_ms),
            'gtx_cooldown_ms': int(self.gtx_cooldown_ms),
            'entrance_timeout_ms': int(self.entrance_timeout_ms),
        }


class HedgeConfig(StrictModel):
    hedge_id: str
    symbol: str
    hedge_mode: HedgeMode

    trigger_offset_pct_x10000: HedgeOffsetsPctX10000
    target_offset_pct_x10000: HedgeOffsetsPctX10000

    execution_params: HedgeExecutionParams
    
    def model_dump(self):
        return {
            'hedge_id': self.hedge_id,
            'symbol': self.symbol,
            'hedge_mode': self.hedge_mode.value,
            'trigger_offset_pct_x10000': self.trigger_offset_pct_x10000.model_dump(),
            'target_offset_pct_x10000': self.target_offset_pct_x10000.model_dump(),
            'execution_params': self.execution_params.model_dump(),
        }


class HedgeLines(StrictModel):
    # Exactly like backtest `lines = (top_target, btm_target, top_threshold, btm_threshold)`
    top_target_units: int
    btm_target_units: int
    top_threshold_units: int
    btm_threshold_units: int
    
    def model_dump(self):
        return {
            'top_target_units': int(self.top_target_units),
            'btm_target_units': int(self.btm_target_units),
            'top_threshold_units': int(self.top_threshold_units),
            'btm_threshold_units': int(self.btm_threshold_units),
        }


class HedgeVolumeRequest(StrictModel):
    hedge_id: str
    symbol: str
    leg: HedgeLeg
    price_units: int
    time_ms: int

    base_price_units: int
    lines: HedgeLines
    
    def model_dump(self):
        return {
            'hedge_id': self.hedge_id,
            'symbol': self.symbol,
            'leg': self.leg.value,
            'price_units': int(self.price_units),
            'time_ms': int(self.time_ms),
            'base_price_units': int(self.base_price_units),
            'lines': self.lines.model_dump(),
        }


class HedgeStats(StrictModel):
    chases_started: int = 0
    chases_done: int = 0
    position_events: int = 0
    
    def model_dump(self):
        return {
            'chases_started': int(self.chases_started),
            'chases_done': int(self.chases_done),
            'position_events': int(self.position_events),
        }


class HedgeChaseMetrics(StrictModel):
    cmd_id: str
    kind: HedgeChaseKind

    started_ms: int
    finished_ms: int = 0

    order_side: str
    intended_price_units: int

    target_base_units: int = 0
    filled_base_units: int = 0
    filled_quote_units: int = 0

    orders_created: int = 0
    fills: int = 0

    gtx_violations: int = 0
    exchange_errors: int = 0

    ok: Optional[bool] = None
    error: Optional[str] = None

    avg_price_units: int = 0
    slippage_pct_x10000: int = 0
    
    def model_dump(self):
        return {
            'cmd_id': self.cmd_id,
            'kind': self.kind.value,
            'started_ms': int(self.started_ms),
            'finished_ms': int(self.finished_ms),
            'order_side': self.order_side,
            'intended_price_units': int(self.intended_price_units),
            'target_base_units': int(self.target_base_units),
            'filled_base_units': int(self.filled_base_units),
            'filled_quote_units': int(self.filled_quote_units),
            'orders_created': int(self.orders_created),
            'fills': int(self.fills),
            'gtx_violations': int(self.gtx_violations),
            'exchange_errors': int(self.exchange_errors),
            'ok': self.ok,
            'error': self.error,
            'avg_price_units': int(self.avg_price_units),
            'slippage_pct_x10000': int(self.slippage_pct_x10000),
        }


class HedgeMetrics(StrictModel):
    last_mid_price_units: int = 0

    base_balance_units: int = 0
    quote_balance_units: int = 0
    quote_turnover_units: int = 0

    realized_pnl_quote_units: int = 0
    unrealized_pnl_quote_units: int = 0

    trigger_ms: int = 0
    opened_ms: int = 0
    close_trigger_ms: int = 0
    closed_ms: int = 0

    chases: List[HedgeChaseMetrics] = []
    neutral_excursions_pct_x10000: List[int] = []
    
    def model_dump(self):
        return {
            'last_mid_price_units': int(self.last_mid_price_units),
            'base_balance_units': int(self.base_balance_units),
            'quote_balance_units': int(self.quote_balance_units),
            'quote_turnover_units': int(self.quote_turnover_units),
            'realized_pnl_quote_units': int(self.realized_pnl_quote_units),
            'unrealized_pnl_quote_units': int(self.unrealized_pnl_quote_units),
            'trigger_ms': int(self.trigger_ms),
            'opened_ms': int(self.opened_ms),
            'close_trigger_ms': int(self.close_trigger_ms),
            'closed_ms': int(self.closed_ms),
            'chases': [c.model_dump() for c in self.chases],
            'neutral_excursions_pct_x10000': [int(x) for x in self.neutral_excursions_pct_x10000],
        }


class HedgeSnapshot(StrictModel):
    hedge_id: str
    symbol: str
    status: HedgeStatus

    started_ms: int
    updated_ms: int
    mutation_counter: int

    base_price_units: int = 0
    lines: Optional[HedgeLines] = None
    
    opened_leg: Optional[HedgeLeg] = None
    opened_base_units: int = 0

    last_error: Optional[str] = None
    stats: HedgeStats
    metrics: HedgeMetrics
    
    def model_dump(self):
        return {
            'hedge_id': self.hedge_id,
            'symbol': self.symbol,
            'status': self.status.value,
            'started_ms': int(self.started_ms),
            'updated_ms': int(self.updated_ms),
            'mutation_counter': int(self.mutation_counter),
            'base_price_units': int(self.base_price_units),
            'lines': self.lines.model_dump() if self.lines is not None else None,
            'opened_leg': self.opened_leg.value if self.opened_leg is not None else None,
            'opened_base_units': int(self.opened_base_units),
            'last_error': self.last_error,
            'stats': self.stats.model_dump(),
            'metrics': self.metrics.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'HedgeSnapshot':
        if data is None:
            raise RuntimeError('HedgeSnapshot.from_dict: data is None')
        if not isinstance(data, dict):
            raise RuntimeError(f'HedgeSnapshot.from_dict: data is not dict: {type(data)}')

        metrics_raw = data['metrics']
        if metrics_raw is None:
            raise RuntimeError('HedgeSnapshot.from_dict: metrics is None')
        if not isinstance(metrics_raw, dict):
            raise RuntimeError(f'HedgeSnapshot.from_dict: metrics is not dict: {type(metrics_raw)}')

        chases_raw = metrics_raw['chases']
        if chases_raw is None:
            raise RuntimeError('HedgeSnapshot.from_dict: metrics.chases is None')
        if not isinstance(chases_raw, list):
            raise RuntimeError(f'HedgeSnapshot.from_dict: metrics.chases is not list: {type(chases_raw)}')

        chases = []
        for item in chases_raw:
            if item is None:
                raise RuntimeError('HedgeSnapshot.from_dict: chase is None')
            if not isinstance(item, dict):
                raise RuntimeError(f'HedgeSnapshot.from_dict: chase is not dict: {type(item)}')

            chases.append(
                HedgeChaseMetrics(
                    cmd_id=str(item['cmd_id']),
                    kind=HedgeChaseKind(str(item['kind'])),
                    started_ms=int(item['started_ms']),
                    finished_ms=int(item['finished_ms']),
                    order_side=str(item['order_side']),
                    intended_price_units=int(item['intended_price_units']),
                    target_base_units=int(item['target_base_units']),
                    filled_base_units=int(item['filled_base_units']),
                    filled_quote_units=int(item['filled_quote_units']),
                    orders_created=int(item['orders_created']),
                    fills=int(item['fills']),
                    gtx_violations=int(item['gtx_violations']),
                    exchange_errors=int(item['exchange_errors']),
                    ok=None if item['ok'] is None else bool(item['ok']),
                    error=None if item['error'] is None else str(item['error']),
                    avg_price_units=int(item['avg_price_units']),
                    slippage_pct_x10000=int(item['slippage_pct_x10000']),
                )
            )

        metrics = HedgeMetrics(
            last_mid_price_units=int(metrics_raw['last_mid_price_units']),
            base_balance_units=int(metrics_raw['base_balance_units']),
            quote_balance_units=int(metrics_raw['quote_balance_units']),
            quote_turnover_units=int(metrics_raw['quote_turnover_units']),
            realized_pnl_quote_units=int(metrics_raw['realized_pnl_quote_units']),
            unrealized_pnl_quote_units=int(metrics_raw['unrealized_pnl_quote_units']),
            trigger_ms=int(metrics_raw['trigger_ms']),
            opened_ms=int(metrics_raw['opened_ms']),
            close_trigger_ms=int(metrics_raw['close_trigger_ms']),
            closed_ms=int(metrics_raw['closed_ms']),
            chases=chases,
            neutral_excursions_pct_x10000=[int(x) for x in metrics_raw['neutral_excursions_pct_x10000']],
        )

        lines_raw = data['lines']
        lines = None if lines_raw is None else HedgeLines(
            top_target_units=int(lines_raw['top_target_units']),
            btm_target_units=int(lines_raw['btm_target_units']),
            top_threshold_units=int(lines_raw['top_threshold_units']),
            btm_threshold_units=int(lines_raw['btm_threshold_units']),
        )

        stats_raw = data['stats']
        if stats_raw is None:
            raise RuntimeError('HedgeSnapshot.from_dict: stats is None')
        if not isinstance(stats_raw, dict):
            raise RuntimeError(f'HedgeSnapshot.from_dict: stats is not dict: {type(stats_raw)}')

        return cls(
            hedge_id=str(data['hedge_id']),
            symbol=str(data['symbol']),
            status=HedgeStatus(str(data['status'])),
            started_ms=int(data['started_ms']),
            updated_ms=int(data['updated_ms']),
            mutation_counter=int(data['mutation_counter']),
            base_price_units=int(data['base_price_units']),
            lines=lines,
            opened_leg=None if data['opened_leg'] is None else HedgeLeg(str(data['opened_leg'])),
            opened_base_units=int(data['opened_base_units']),
            last_error=None if data['last_error'] is None else str(data['last_error']),
            stats=HedgeStats(
                chases_started=int(stats_raw['chases_started']),
                chases_done=int(stats_raw['chases_done']),
                position_events=int(stats_raw['position_events']),
            ),
            metrics=metrics,
        )


class _HedgeState(StrictModel):
    status: HedgeStatus = HedgeStatus.INITIALIZED

    started_ms: int = 0
    updated_ms: int = 0
    mutation_counter: int = 0
    
    phase_started_ms: int = 0

    base_price_units: int = 0
    lines: Optional[HedgeLines] = None

    opened_leg: Optional[HedgeLeg] = None
    opened_base_units: int = 0

    opening_cmd_id: Optional[str] = None
    closing_cmd_id: Optional[str] = None
    
    closing_reason: Optional[HedgeCloseReason] = None

    last_error: Optional[str] = None
    stats: HedgeStats = HedgeStats()
    metrics: HedgeMetrics = HedgeMetrics()

    current_chase: Optional[HedgeChaseMetrics] = None

    neutral_excursion_threshold_units: int = 0
    neutral_excursion_max_units: int = 0

    close_requested: bool = False
    close_reason: Optional[str] = None


