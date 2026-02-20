"""
Frontend models
Date: 2026-02-13
Version: 3.0
"""
from typing import List, Optional

from live.lib.strict_model import StrictModel
from backend.models.backend_models import (
    BackendPositionView,
    BackendRunAggregates,
    BackendRunLifecycle,
    BackendStartRunRequest,
)
from backend.models.hedger_models import CexTriggerMode, HedgerConfig
from backend.models.mock_hedge_models import MockHedgeBoundary
from dex.contract.params import Params


def frontend_position_view_from_dict(data: dict) -> BackendPositionView:
    close_trigger_upper_count = 0
    if 'close_trigger_upper_count' in data:
        close_trigger_upper_count = int(data['close_trigger_upper_count'])

    close_trigger_lower_count = 0
    if 'close_trigger_lower_count' in data:
        close_trigger_lower_count = int(data['close_trigger_lower_count'])

    close_trigger_total_count = int(close_trigger_upper_count) + int(close_trigger_lower_count)
    if 'close_trigger_total_count' in data:
        close_trigger_total_count = int(data['close_trigger_total_count'])

    close_trigger_upper_pct = 0.0
    if 'close_trigger_upper_pct' in data:
        close_trigger_upper_pct = float(data['close_trigger_upper_pct'])

    close_trigger_lower_pct = 0.0
    if 'close_trigger_lower_pct' in data:
        close_trigger_lower_pct = float(data['close_trigger_lower_pct'])

    token_id = None
    if 'token_id' in data and data['token_id'] is not None:
        token_id = int(data['token_id'])

    last_error = None
    if 'last_error' in data and data['last_error'] is not None:
        last_error = str(data['last_error'])

    return BackendPositionView(
        run_id=str(data['run_id']),
        symbol=str(data['symbol']),
        first_started_at_ms=int(data['first_started_at_ms']),
        runtime_sec=float(data['runtime_sec']),
        runtime_dhm=str(data['runtime_dhm']),
        avg_iteration_lifetime_sec=float(data['avg_iteration_lifetime_sec']),
        iterations_finished=int(data['iterations_finished']),
        close_trigger_upper_count=int(close_trigger_upper_count),
        close_trigger_lower_count=int(close_trigger_lower_count),
        close_trigger_total_count=int(close_trigger_total_count),
        close_trigger_upper_pct=float(close_trigger_upper_pct),
        close_trigger_lower_pct=float(close_trigger_lower_pct),
        status=BackendRunLifecycle(str(data['status'])),
        market_price=None if data['market_price'] is None else float(data['market_price']),
        price_lower=None if data['price_lower'] is None else float(data['price_lower']),
        price_upper=None if data['price_upper'] is None else float(data['price_upper']),
        price_lower_pct=None if data['price_lower_pct'] is None else float(data['price_lower_pct']),
        price_upper_pct=None if data['price_upper_pct'] is None else float(data['price_upper_pct']),
        total_quote=float(data['total_quote']),
        pnl_with_hedge_quote=float(data['pnl_with_hedge_quote']),
        pnl_without_hedge_quote=float(data['pnl_without_hedge_quote']),
        apr_with_hedge_pct=float(data['apr_with_hedge_pct']),
        apr_without_hedge_pct=float(data['apr_without_hedge_pct']),
        fees_quote=float(data['fees_quote']),
        price_pnl_quote=float(data['price_pnl_quote']),
        hedge_pnl_quote=float(data['hedge_pnl_quote']),
        costs_quote=float(data['costs_quote']),
        token_id=token_id,
        last_error=last_error,
    )


def frontend_run_aggregates_from_dict(data: dict) -> BackendRunAggregates:
    payload = BackendRunAggregates.empty().model_dump()
    for key in payload.keys():
        if key in data:
            payload[key] = data[key]
    return BackendRunAggregates(**payload)


### Backend responses ###
class FrontendBackendStartRunResponse(StrictModel):
    ok: bool
    run_id: Optional[str]
    error: Optional[str]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendBackendStartRunResponse':
        run_id = None
        if 'run_id' in data:
            run_id = None if data['run_id'] is None else str(data['run_id'])

        error = None
        if 'error' in data:
            error = None if data['error'] is None else str(data['error'])

        row = cls(
            ok=bool(data['ok']),
            run_id=run_id,
            error=error,
        )
        if bool(row.ok) and row.run_id is None:
            raise RuntimeError('FrontendBackendStartRunResponse.from_dict: ok=true but run_id is missing')
        if not bool(row.ok) and row.error is None:
            raise RuntimeError('FrontendBackendStartRunResponse.from_dict: ok=false but error is missing')
        return row


class FrontendBackendStopRunResponse(StrictModel):
    ok: bool
    run_id: Optional[str]
    error: Optional[str]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendBackendStopRunResponse':
        run_id = None
        if 'run_id' in data:
            run_id = None if data['run_id'] is None else str(data['run_id'])

        error = None
        if 'error' in data:
            error = None if data['error'] is None else str(data['error'])

        row = cls(
            ok=bool(data['ok']),
            run_id=run_id,
            error=error,
        )
        if not bool(row.ok) and row.error is None:
            raise RuntimeError('FrontendBackendStopRunResponse.from_dict: ok=false but error is missing')
        return row


class FrontendBackendPositionsResponse(StrictModel):
    ok: bool
    items: List[BackendPositionView]
    error: Optional[str]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendBackendPositionsResponse':
        items = []
        for item in data['items']:
            items.append(frontend_position_view_from_dict(item))

        error = None
        if 'error' in data:
            error = None if data['error'] is None else str(data['error'])

        row = cls(
            ok=bool(data['ok']),
            items=items,
            error=error,
        )
        if not bool(row.ok) and row.error is None:
            raise RuntimeError('FrontendBackendPositionsResponse.from_dict: ok=false but error is missing')
        return row


class FrontendDexNetworkConfig(StrictModel):
    key: str
    rpc_url_template: str
    ws_url_template: str
    stables: List[str]
    npm: str


def frontend_dex_network_configs_from_params() -> List['FrontendDexNetworkConfig']:
    networks = Params.NETWORKS
    out = []

    for network_key in networks:
        network_data = networks[network_key]

        stables = []
        for stable in network_data['stables']:
            stables.append(str(stable))

        out.append(
            FrontendDexNetworkConfig(
                key=str(network_key),
                rpc_url_template=str(network_data['rpc_url_template']),
                ws_url_template=str(network_data['ws_url_template']),
                stables=stables,
                npm=str(network_data['npm']),
            )
        )

    return out


class FrontendRuntimeConfig(StrictModel):
    rpc_key: str
    mongo_uri: str
    mongo_db: str
    mongo_collection: str
    tick_ms: int
    gtx_cooldown_ms: int
    entrance_timeout_ms: int
    cowswap_api_timeout_sec: int
    cowswap_wait_timeout_sec: int
    cowswap_poll_interval_sec: int


class FrontendRunTemplateDoc(StrictModel):
    template_id: str
    network: str
    symbol: str
    pool_address: str
    fee_pct: float
    cex_ratio: float
    trigger_mode: CexTriggerMode
    trigger_pct: float
    trigger_units: int
    created_at_ms: int
    updated_at_ms: int

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'template_id': str(self.template_id),
            'network': str(self.network),
            'symbol': str(self.symbol),
            'pool_address': str(self.pool_address),
            'fee_pct': float(self.fee_pct),
            'cex_ratio': float(self.cex_ratio),
            'trigger_mode': str(self.trigger_mode.value),
            'trigger_pct': float(self.trigger_pct),
            'trigger_units': int(self.trigger_units),
            'created_at_ms': int(self.created_at_ms),
            'updated_at_ms': int(self.updated_at_ms),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendRunTemplateDoc':
        return cls(
            template_id=str(data['template_id']),
            network=str(data['network']),
            symbol=str(data['symbol']),
            pool_address=str(data['pool_address']),
            fee_pct=float(data['fee_pct']),
            cex_ratio=float(data['cex_ratio']),
            trigger_mode=CexTriggerMode(str(data['trigger_mode'])),
            trigger_pct=float(data['trigger_pct']),
            trigger_units=int(data['trigger_units']),
            created_at_ms=int(data['created_at_ms']),
            updated_at_ms=int(data['updated_at_ms']),
        )


class FrontendCreateTemplateForm(StrictModel):
    network: str
    symbol: str
    pool_address: str
    fee_pct: float
    cex_ratio: float
    trigger_mode: CexTriggerMode
    trigger_pct: float
    trigger_units: int


class FrontendStartFromTemplateForm(StrictModel):
    template_id: str
    total_quote: float
    price_lower_pct: float
    price_upper_pct: float
    dex_only: bool
    mock_source_dex: bool


class FrontendUpdateTemplateForm(StrictModel):
    template_id: str
    network: str
    symbol: str
    pool_address: str
    fee_pct: float
    cex_ratio: float
    trigger_mode: CexTriggerMode
    trigger_pct: float
    trigger_units: int


### Storage models ###
class FrontendActivePositionDoc(StrictModel):
    run_id: str
    status: BackendRunLifecycle
    created_at_ms: int
    started_at_ms: int
    updated_at_ms: int
    stop_requested: bool
    last_error: Optional[str]
    template_id: Optional[str]
    config: HedgerConfig
    aggregates: BackendRunAggregates
    position: BackendPositionView
    iterations_count: int

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendActivePositionDoc':
        return cls(
            run_id=str(data['run_id']),
            status=BackendRunLifecycle(str(data['status'])),
            created_at_ms=int(data['created_at_ms']),
            started_at_ms=int(data['started_at_ms']),
            updated_at_ms=int(data['updated_at_ms']),
            stop_requested=bool(data['stop_requested']),
            last_error=None if data['last_error'] is None else str(data['last_error']),
            template_id=None if ('template_id' not in data or data['template_id'] is None) else str(data['template_id']),
            config=HedgerConfig.from_dict(data['config']),
            aggregates=frontend_run_aggregates_from_dict(data['aggregates']),
            position=frontend_position_view_from_dict(data['position']),
            iterations_count=int(data['iterations_count']),
        )


class FrontendArchivePositionDoc(StrictModel):
    run_id: str
    status: BackendRunLifecycle
    created_at_ms: int
    started_at_ms: int
    finished_at_ms: int
    archived_at_ms: int
    stop_requested: bool
    last_error: Optional[str]
    template_id: Optional[str]
    config: HedgerConfig
    aggregates: BackendRunAggregates
    position: BackendPositionView
    iterations_count: int

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendArchivePositionDoc':
        return cls(
            run_id=str(data['run_id']),
            status=BackendRunLifecycle(str(data['status'])),
            created_at_ms=int(data['created_at_ms']),
            started_at_ms=int(data['started_at_ms']),
            finished_at_ms=int(data['finished_at_ms']),
            archived_at_ms=int(data['archived_at_ms']),
            stop_requested=bool(data['stop_requested']),
            last_error=None if data['last_error'] is None else str(data['last_error']),
            template_id=None if ('template_id' not in data or data['template_id'] is None) else str(data['template_id']),
            config=HedgerConfig.from_dict(data['config']),
            aggregates=frontend_run_aggregates_from_dict(data['aggregates']),
            position=frontend_position_view_from_dict(data['position']),
            iterations_count=int(data['iterations_count']),
        )


class FrontendIterationStatsCalc(StrictModel):
    base_price: float
    price_lower: float
    price_upper: float
    total_quote: float
    cex_ratio: float
    trigger_mode: CexTriggerMode
    trigger_offset_pct_x10000: int
    target_offset_pct_x10000: int
    hedge_quote: float

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationStatsCalc':
        return cls(
            base_price=float(data['base_price']),
            price_lower=float(data['price_lower']),
            price_upper=float(data['price_upper']),
            total_quote=float(data['total_quote']),
            cex_ratio=float(data['cex_ratio']),
            trigger_mode=CexTriggerMode(str(data['trigger_mode'])),
            trigger_offset_pct_x10000=int(data['trigger_offset_pct_x10000']),
            target_offset_pct_x10000=int(data['target_offset_pct_x10000']),
            hedge_quote=float(data['hedge_quote']),
        )


class FrontendUniswapMint(StrictModel):
    amount_base: Optional[float]
    amount_quote: Optional[float]
    gas_cost_eth: Optional[float]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendUniswapMint':
        return cls(
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
        )


class FrontendUniswapDecrease(StrictModel):
    amount_base: Optional[float]
    amount_quote: Optional[float]
    gas_cost_eth: Optional[float]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendUniswapDecrease':
        return cls(
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
        )


class FrontendUniswapCollect(StrictModel):
    amount_base: Optional[float]
    amount_quote: Optional[float]
    gas_cost_eth: Optional[float]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendUniswapCollect':
        return cls(
            amount_base=None if data['amount_base'] is None else float(data['amount_base']),
            amount_quote=None if data['amount_quote'] is None else float(data['amount_quote']),
            gas_cost_eth=None if data['gas_cost_eth'] is None else float(data['gas_cost_eth']),
        )


class FrontendUniswapPosition(StrictModel):
    price_lower: float
    price_upper: float
    price_current: float
    amount_base: float
    amount_quote: float
    uncollected_base: float
    uncollected_quote: float

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendUniswapPosition':
        return cls(
            price_lower=float(data['price_lower']),
            price_upper=float(data['price_upper']),
            price_current=float(data['price_current']),
            amount_base=float(data['amount_base']),
            amount_quote=float(data['amount_quote']),
            uncollected_base=float(data['uncollected_base']),
            uncollected_quote=float(data['uncollected_quote']),
        )


class FrontendRebalanceOrder(StrictModel):
    status: str
    sell_amount: float
    buy_amount: float
    fee_amount: float
    fee_token: str
    created_at: str
    elapsed_sec: int
    url: str

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendRebalanceOrder':
        return cls(
            status=str(data['status']),
            sell_amount=float(data['sell_amount']),
            buy_amount=float(data['buy_amount']),
            fee_amount=float(data['fee_amount']),
            fee_token=str(data['fee_token']),
            created_at=str(data['created_at']),
            elapsed_sec=int(data['elapsed_sec']),
            url=str(data['url']),
        )


class FrontendRebalanceSwap(StrictModel):
    ok: bool
    error: Optional[str]
    order: Optional[FrontendRebalanceOrder]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendRebalanceSwap':
        return cls(
            ok=bool(data['ok']),
            error=None if data['error'] is None else str(data['error']),
            order=None if data['order'] is None else FrontendRebalanceOrder.from_dict(data['order']),
        )


class FrontendIterationStatsUniswap(StrictModel):
    token_id: Optional[int]
    mint: Optional[FrontendUniswapMint]
    mint_tx_timestamp_ms: int
    position: Optional[FrontendUniswapPosition]
    decrease: Optional[FrontendUniswapDecrease]
    decrease_tx_timestamp_ms: int
    collect: Optional[FrontendUniswapCollect]
    rebalance: Optional[FrontendRebalanceSwap]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationStatsUniswap':
        return cls(
            token_id=None if ('token_id' not in data or data['token_id'] is None) else int(data['token_id']),
            mint=None if data['mint'] is None else FrontendUniswapMint.from_dict(data['mint']),
            mint_tx_timestamp_ms=int(data['mint_tx_timestamp_ms']),
            position=None if data['position'] is None else FrontendUniswapPosition.from_dict(data['position']),
            decrease=None if data['decrease'] is None else FrontendUniswapDecrease.from_dict(data['decrease']),
            decrease_tx_timestamp_ms=int(data['decrease_tx_timestamp_ms']),
            collect=None if data['collect'] is None else FrontendUniswapCollect.from_dict(data['collect']),
            rebalance=None if data['rebalance'] is None else FrontendRebalanceSwap.from_dict(data['rebalance']),
        )


class FrontendHedgeChase(StrictModel):
    kind: str
    started_ms: int
    finished_ms: int
    order_side: str
    intended_price_units: int
    target_base_units: int
    filled_base_units: int
    filled_quote_units: int
    orders_created: int
    fills: int
    gtx_violations: int
    exchange_errors: int
    ok: Optional[bool]
    error: Optional[str]
    avg_price_units: int
    slippage_pct_x10000: int

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendHedgeChase':
        return cls(
            kind=str(data['kind']),
            started_ms=int(data['started_ms']),
            finished_ms=int(data['finished_ms']),
            order_side=str(data['order_side']),
            intended_price_units=int(data['intended_price_units']),
            target_base_units=int(data['target_base_units']),
            filled_base_units=int(data['filled_base_units']),
            filled_quote_units=int(data['filled_quote_units']),
            orders_created=int(data['orders_created']),
            fills=int(data['fills']),
            gtx_violations=int(data['gtx_violations']),
            exchange_errors=int(data['exchange_errors']),
            ok=None if data['ok'] is None else bool(data['ok']),
            error=None if data['error'] is None else str(data['error']),
            avg_price_units=int(data['avg_price_units']),
            slippage_pct_x10000=int(data['slippage_pct_x10000']),
        )


class FrontendHedgeMetrics(StrictModel):
    last_mid_price_units: int
    realized_pnl_quote_units: int
    unrealized_pnl_quote_units: int
    opened_ms: int
    closed_ms: int
    chases: List[FrontendHedgeChase]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendHedgeMetrics':
        chases = []
        for item in data['chases']:
            chases.append(FrontendHedgeChase.from_dict(item))

        return cls(
            last_mid_price_units=int(data['last_mid_price_units']),
            realized_pnl_quote_units=int(data['realized_pnl_quote_units']),
            unrealized_pnl_quote_units=int(data['unrealized_pnl_quote_units']),
            opened_ms=int(data['opened_ms']),
            closed_ms=int(data['closed_ms']),
            chases=chases,
        )


class FrontendHedgeSymbolRule(StrictModel):
    price_step: str

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendHedgeSymbolRule':
        return cls(
            price_step=str(data['price_step']),
        )


class FrontendHedgeLastSnapshot(StrictModel):
    close_reason: Optional[str]
    opened_leg: Optional[str]
    opened_base_units: int
    symbol_rule: Optional[FrontendHedgeSymbolRule]
    metrics: FrontendHedgeMetrics

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendHedgeLastSnapshot':
        return cls(
            close_reason=None if data['close_reason'] is None else str(data['close_reason']),
            opened_leg=None if data['opened_leg'] is None else str(data['opened_leg']),
            opened_base_units=int(data['opened_base_units']),
            symbol_rule=None if data['symbol_rule'] is None else FrontendHedgeSymbolRule.from_dict(data['symbol_rule']),
            metrics=FrontendHedgeMetrics.from_dict(data['metrics']),
        )


class FrontendIterationStatsLive(StrictModel):
    last_snapshot: Optional[FrontendHedgeLastSnapshot]

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationStatsLive':
        return cls(
            last_snapshot=None if data['last_snapshot'] is None else FrontendHedgeLastSnapshot.from_dict(data['last_snapshot'])
        )


class FrontendIterationStats(StrictModel):
    calc: FrontendIterationStatsCalc
    uniswap: FrontendIterationStatsUniswap
    live: FrontendIterationStatsLive

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationStats':
        return cls(
            calc=FrontendIterationStatsCalc.from_dict(data['calc']),
            uniswap=FrontendIterationStatsUniswap.from_dict(data['uniswap']),
            live=FrontendIterationStatsLive.from_dict(data['live']),
        )


class FrontendIterationPnl(StrictModel):
    cex_pnl_quote: float
    dex_realized_il_quote: float
    fees_received_quote: float
    gas_paid_eth: float
    gas_paid_quote: float
    pool_hold_seconds: float
    apr_pct: float
    total_pnl_quote: float

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationPnl':
        return cls(
            cex_pnl_quote=float(data['cex_pnl_quote']),
            dex_realized_il_quote=float(data['dex_realized_il_quote']),
            fees_received_quote=float(data['fees_received_quote']),
            gas_paid_eth=float(data['gas_paid_eth']),
            gas_paid_quote=float(data['gas_paid_quote']),
            pool_hold_seconds=float(data['pool_hold_seconds']),
            apr_pct=float(data['apr_pct']),
            total_pnl_quote=float(data['total_pnl_quote']),
        )


class FrontendIterationDoc(StrictModel):
    id: str
    run_id: str
    iteration_no: int
    started_at_ms: int
    finished_at_ms: int
    status: str
    close_trigger_side: Optional[MockHedgeBoundary]
    error: Optional[str]
    stats: FrontendIterationStats
    pnl: FrontendIterationPnl
    swap_cost_quote: float
    costs_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_quote: float
    created_at_ms: int
    run_lifecycle: str

    @classmethod
    def from_dict(cls, data: dict) -> 'FrontendIterationDoc':
        close_trigger_side = None
        if 'close_trigger_side' in data and data['close_trigger_side'] is not None:
            close_trigger_side = MockHedgeBoundary(str(data['close_trigger_side']))

        return cls(
            id=str(data['id']),
            run_id=str(data['run_id']),
            iteration_no=int(data['iteration_no']),
            started_at_ms=int(data['started_at_ms']),
            finished_at_ms=int(data['finished_at_ms']),
            status=str(data['status']),
            close_trigger_side=close_trigger_side,
            error=None if data['error'] is None else str(data['error']),
            stats=FrontendIterationStats.from_dict(data['stats']),
            pnl=FrontendIterationPnl.from_dict(data['pnl']),
            swap_cost_quote=float(data['swap_cost_quote']),
            costs_quote=float(data['costs_quote']),
            pnl_without_hedge_quote=float(data['pnl_without_hedge_quote']),
            pnl_with_hedge_quote=float(data['pnl_with_hedge_quote']),
            created_at_ms=int(data['created_at_ms']),
            run_lifecycle=str(data['run_lifecycle']),
        )


### Derived models ###
class FrontendIterationDerivedPnl(StrictModel):
    run_id: str
    iteration_no: int
    is_finished: bool
    close_price: float
    fees_quote: float
    il_quote: float
    cex_quote: float
    costs_pnl_quote: float
    pnl_without_hedge_quote: float
    pnl_with_hedge_quote: float
    pool_hold_seconds: float


class FrontendPnlRecalcAgg(StrictModel):
    sum_fees_quote: float
    sum_il_quote: float
    sum_cex_quote: float
    sum_costs_pnl_quote: float
    sum_pool_hold_seconds: float
    iterations_finished: int


### View models ###
class FrontendPositionRow(StrictModel):
    run_id: str
    network: str
    symbol: str
    dex_only: bool
    mock_realtime_source: str
    status: BackendRunLifecycle
    first_started_at_ms: int
    runtime_sec: float
    runtime_dhm: str
    avg_iteration_lifetime_sec: float
    iterations_finished: int
    close_trigger_upper_count: int
    close_trigger_lower_count: int
    close_trigger_total_count: int
    close_trigger_upper_pct: float
    close_trigger_lower_pct: float
    market_price: Optional[float]
    price_lower: Optional[float]
    price_upper: Optional[float]
    price_lower_pct: Optional[float]
    price_upper_pct: Optional[float]
    total_quote: float
    pnl_fees_quote: float
    pnl_fees_il_quote: float
    pnl_fees_il_gas_quote: float
    pnl_fees_il_gas_cex_quote: float
    apr_fees_pct: float
    apr_fees_il_pct: float
    apr_fees_il_gas_pct: float
    apr_fees_il_gas_cex_pct: float
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
    token_id: Optional[int]
    pool_url: Optional[str]
    revert_link: Optional[str]
    last_error: Optional[str]
    iterations_count: int
    is_active: bool

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'run_id': self.run_id,
            'network': self.network,
            'symbol': self.symbol,
            'dex_only': bool(self.dex_only),
            'mock_realtime_source': str(self.mock_realtime_source),
            'status': self.status.value,
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
            'market_price': None if self.market_price is None else float(self.market_price),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'price_lower_pct': None if self.price_lower_pct is None else float(self.price_lower_pct),
            'price_upper_pct': None if self.price_upper_pct is None else float(self.price_upper_pct),
            'total_quote': float(self.total_quote),
            'pnl_fees_quote': float(self.pnl_fees_quote),
            'pnl_fees_il_quote': float(self.pnl_fees_il_quote),
            'pnl_fees_il_gas_quote': float(self.pnl_fees_il_gas_quote),
            'pnl_fees_il_gas_cex_quote': float(self.pnl_fees_il_gas_cex_quote),
            'apr_fees_pct': float(self.apr_fees_pct),
            'apr_fees_il_pct': float(self.apr_fees_il_pct),
            'apr_fees_il_gas_pct': float(self.apr_fees_il_gas_pct),
            'apr_fees_il_gas_cex_pct': float(self.apr_fees_il_gas_cex_pct),
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
            'token_id': None if self.token_id is None else int(self.token_id),
            'pool_url': self.pool_url,
            'revert_link': self.revert_link,
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
    close_trigger_side: Optional[MockHedgeBoundary]
    total_quote: float
    price_lower: Optional[float]
    price_upper: Optional[float]
    pnl_fees_quote: float
    pnl_fees_il_quote: float
    pnl_fees_il_gas_quote: float
    pnl_fees_il_gas_cex_quote: float
    apr_fees_pct: float
    apr_fees_il_pct: float
    apr_fees_il_gas_pct: float
    apr_fees_il_gas_cex_pct: float
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
    token_id: Optional[int]
    pool_url: Optional[str]
    revert_link: Optional[str]

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
            'close_trigger_side': None if self.close_trigger_side is None else str(self.close_trigger_side.value),
            'total_quote': float(self.total_quote),
            'price_lower': None if self.price_lower is None else float(self.price_lower),
            'price_upper': None if self.price_upper is None else float(self.price_upper),
            'pnl_fees_quote': float(self.pnl_fees_quote),
            'pnl_fees_il_quote': float(self.pnl_fees_il_quote),
            'pnl_fees_il_gas_quote': float(self.pnl_fees_il_gas_quote),
            'pnl_fees_il_gas_cex_quote': float(self.pnl_fees_il_gas_cex_quote),
            'apr_fees_pct': float(self.apr_fees_pct),
            'apr_fees_il_pct': float(self.apr_fees_il_pct),
            'apr_fees_il_gas_pct': float(self.apr_fees_il_gas_pct),
            'apr_fees_il_gas_cex_pct': float(self.apr_fees_il_gas_cex_pct),
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
            'token_id': None if self.token_id is None else int(self.token_id),
            'pool_url': self.pool_url,
            'revert_link': self.revert_link,
        }


class FrontendRunDetailsView(StrictModel):
    template_id: Optional[str]
    config: HedgerConfig
    position: FrontendPositionRow
    iterations: List[FrontendIterationRow]
    failure_reason: Optional[str]
    failure_error_raw: Optional[str]

    def model_dump(self) -> dict:  # type: ignore[override]
        out_iterations = []
        for item in self.iterations:
            out_iterations.append(item.model_dump())

        return {
            'template_id': self.template_id,
            'config': self.config.model_dump(),
            'position': self.position.model_dump(),
            'iterations': out_iterations,
            'failure_reason': self.failure_reason,
            'failure_error_raw': self.failure_error_raw,
        }


class FrontendIterationDetailsView(StrictModel):
    row: FrontendIterationRow
    lp: 'FrontendIterationLpBlock'
    hedge: 'FrontendIterationHedgeBlock'
    rebalance: 'FrontendIterationRebalanceBlock'
    hedge_chases: List['FrontendIterationHedgeChaseRow']

    def model_dump(self) -> dict:  # type: ignore[override]
        out_chases = []
        for item in self.hedge_chases:
            out_chases.append(item.model_dump())

        return {
            'row': self.row.model_dump(),
            'lp': self.lp.model_dump(),
            'hedge': self.hedge.model_dump(),
            'rebalance': self.rebalance.model_dump(),
            'hedge_chases': out_chases,
        }


class FrontendIterationLpBlock(StrictModel):
    base_price: float
    price_lower: float
    price_upper: float
    minted_base: float
    minted_quote: float
    closed_base: float
    closed_quote: float
    fees_quote: float
    price_pnl_quote: float
    impermanent_loss_quote: float


class FrontendIterationHedgeBlock(StrictModel):
    activated: bool
    activation_price: Optional[float]
    hedge_quote: float
    opened_leg: Optional[str]
    opened_base_units: int
    cex_pnl_quote: float
    realized_pnl_quote_units: int
    unrealized_pnl_quote_units: int
    opened_ms: int
    closed_ms: int
    close_reason: Optional[str]
    close_trigger_side: Optional[MockHedgeBoundary]

    def model_dump(self) -> dict:  # type: ignore[override]
        return {
            'activated': bool(self.activated),
            'activation_price': None if self.activation_price is None else float(self.activation_price),
            'hedge_quote': float(self.hedge_quote),
            'opened_leg': self.opened_leg,
            'opened_base_units': int(self.opened_base_units),
            'cex_pnl_quote': float(self.cex_pnl_quote),
            'realized_pnl_quote_units': int(self.realized_pnl_quote_units),
            'unrealized_pnl_quote_units': int(self.unrealized_pnl_quote_units),
            'opened_ms': int(self.opened_ms),
            'closed_ms': int(self.closed_ms),
            'close_reason': self.close_reason,
            'close_trigger_side': None if self.close_trigger_side is None else str(self.close_trigger_side.value),
        }


class FrontendIterationRebalanceBlock(StrictModel):
    swap_ok: bool
    swap_error: Optional[str]
    sell_amount: Optional[float]
    buy_amount: Optional[float]
    execution_fee: Optional[float]
    fee_token: Optional[str]
    elapsed_sec: Optional[int]
    order_status: Optional[str]
    order_url: Optional[str]
    swap_cost_quote: float


class FrontendIterationHedgeChaseRow(StrictModel):
    kind: str
    started_ms: int
    finished_ms: int
    order_side: str
    filled_base_units: int
    filled_quote_units: int
    avg_price_units: int
    slippage_pct_x10000: int
    gtx_violations: int
    exchange_errors: int
    ok: Optional[bool]
    error: Optional[str]
    cex_quote_balance_units: int
    cex_pnl_quote: float
    is_final_pnl: bool


class FrontendDashboardView(StrictModel):
    active_runs: int
    finished_iterations: int
    close_trigger_upper_count: int
    close_trigger_lower_count: int
    close_trigger_total_count: int
    close_trigger_upper_pct: float
    close_trigger_lower_pct: float
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
            'close_trigger_upper_count': int(self.close_trigger_upper_count),
            'close_trigger_lower_count': int(self.close_trigger_lower_count),
            'close_trigger_total_count': int(self.close_trigger_total_count),
            'close_trigger_upper_pct': float(self.close_trigger_upper_pct),
            'close_trigger_lower_pct': float(self.close_trigger_lower_pct),
            'total_pnl_with_hedge_quote': float(self.total_pnl_with_hedge_quote),
            'total_pnl_without_hedge_quote': float(self.total_pnl_without_hedge_quote),
            'total_costs_quote': float(self.total_costs_quote),
            'apr_with_hedge_pct': float(self.apr_with_hedge_pct),
            'apr_without_hedge_pct': float(self.apr_without_hedge_pct),
            'avg_apr_with_hedge_pct': float(self.avg_apr_with_hedge_pct),
            'avg_iteration_lifetime_sec': float(self.avg_iteration_lifetime_sec),
        }
