"""
Frontend domain service
Date: 2026-02-13
Version: 2.0
"""
from typing import List

from live.lib.logger import get_logger
from backend.models.backend_models import BackendPositionView, BackendRunLifecycle, BackendStartRunRequest
from frontend.modules.models.frontend_models import (
    FrontendActivePositionDoc,
    FrontendArchivePositionDoc,
    FrontendDashboardView,
    FrontendIterationDetailsView,
    FrontendIterationDoc,
    FrontendIterationRow,
    FrontendIterationHedgeBlock,
    FrontendIterationHedgeChaseRow,
    FrontendIterationLpBlock,
    FrontendIterationRebalanceBlock,
    FrontendStartRunForm,
    FrontendPositionRow,
    FrontendRunDetailsView,
)
from frontend.modules.services.backend_api_service import BackendApiService
from frontend.modules.services.storage_service import StorageService


class FrontendService:
    def __init__(self, storage: StorageService, backend_api: BackendApiService):
        if storage is None:
            raise RuntimeError('FrontendService: storage is None')
        if backend_api is None:
            raise RuntimeError('FrontendService: backend_api is None')

        self._storage = storage
        self._backend_api = backend_api
        self._logger = get_logger('frontend_service')

    def start_run(self, form: FrontendStartRunForm) -> str:
        if form is None:
            raise RuntimeError('FrontendService.start_run: form is None')
        if not isinstance(form, FrontendStartRunForm):
            raise RuntimeError(f'FrontendService.start_run: form is not FrontendStartRunForm: {type(form)}')

        req = form.to_start_request()
        run_id = self._backend_api.start_run(req)
        self._logger.info(f'frontend_start_run_ok run_id={run_id}')
        return run_id

    def start_run_request(self, req: BackendStartRunRequest) -> str:
        if req is None:
            raise RuntimeError('FrontendService.start_run_request: req is None')
        if not isinstance(req, BackendStartRunRequest):
            raise RuntimeError(f'FrontendService.start_run_request: req is not BackendStartRunRequest: {type(req)}')

        run_id = self._backend_api.start_run(req)
        self._logger.info(f'frontend_start_run_request_ok run_id={run_id}')
        return run_id

    def stop_run(self, run_id: str) -> None:
        self._backend_api.stop_run(run_id)
        self._logger.info(f'frontend_stop_run_ok run_id={run_id}')

    def list_positions(self) -> List[FrontendPositionRow]:
        active_docs = self._storage.list_active_positions()
        archive_docs = self._storage.list_archive_positions()

        out = []

        for item in active_docs:
            out.append(self._build_position_row_from_active(item))

        for item in archive_docs:
            out.append(self._build_position_row_from_archive(item))

        out.sort(key=lambda x: int(x.first_started_at_ms), reverse=True)
        return out

    def get_run_details(self, run_id: str) -> FrontendRunDetailsView:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('FrontendService.get_run_details: run_id is empty')

        active_doc = self._storage.find_position(run_id)
        archive_doc = self._storage.find_position_archive(run_id)

        position_row = None
        if active_doc is not None:
            position_row = self._build_position_row_from_active(active_doc)
        elif archive_doc is not None:
            position_row = self._build_position_row_from_archive(archive_doc)
        else:
            raise RuntimeError(f'FrontendService.get_run_details: run not found: {run_id}')

        iteration_docs = self._storage.list_iterations_by_run(run_id)
        iteration_rows = []
        for item in iteration_docs:
            iteration_rows.append(self._build_iteration_row(item))

        return FrontendRunDetailsView(
            position=position_row,
            iterations=iteration_rows,
        )

    def get_iteration_details(self, iteration_id: str) -> FrontendIterationDetailsView:
        item = self._storage.find_iteration(iteration_id)
        iteration_row = self._build_iteration_row(item)
        lp = self._build_lp_block(item)
        hedge = self._build_hedge_block(item)
        rebalance = self._build_rebalance_block(item)
        hedge_chases = self._build_hedge_chases(item)

        return FrontendIterationDetailsView(
            row=iteration_row,
            lp=lp,
            hedge=hedge,
            rebalance=rebalance,
            hedge_chases=hedge_chases,
        )

    def build_dashboard(self) -> FrontendDashboardView:
        positions = self.list_positions()

        active_runs = 0
        finished_iterations = 0
        total_invested_quote = 0.0
        total_pnl_with_hedge_quote = 0.0
        total_pnl_without_hedge_quote = 0.0
        total_costs_quote = 0.0
        weighted_apr_with = 0.0
        weighted_apr_without = 0.0
        avg_apr_sum = 0.0
        avg_apr_n = 0
        hold_seconds_sum = 0.0
        hold_seconds_n = 0

        for row in positions:
            if row.status in [BackendRunLifecycle.INITIALIZED, BackendRunLifecycle.RUNNING, BackendRunLifecycle.STOPPING]:
                active_runs += 1

            finished_iterations += int(row.iterations_finished)
            total_invested_quote += float(row.total_quote)
            total_pnl_with_hedge_quote += float(row.pnl_with_hedge_quote)
            total_pnl_without_hedge_quote += float(row.pnl_without_hedge_quote)
            total_costs_quote += float(row.costs_quote)

            if float(row.total_quote) > 0.0:
                weighted_apr_with += float(row.apr_with_hedge_pct) * float(row.total_quote)
                weighted_apr_without += float(row.apr_without_hedge_pct) * float(row.total_quote)

            avg_apr_sum += float(row.apr_with_hedge_pct)
            avg_apr_n += 1

            if float(row.avg_iteration_lifetime_sec) > 0.0:
                hold_seconds_sum += float(row.avg_iteration_lifetime_sec)
                hold_seconds_n += 1

        if float(total_invested_quote) <= 0.0 and len(positions) > 0:
            raise RuntimeError('FrontendService.build_dashboard: total_invested_quote <= 0 for non-empty positions')

        apr_with_hedge_pct = 0.0
        apr_without_hedge_pct = 0.0
        if float(total_invested_quote) > 0.0:
            apr_with_hedge_pct = float(weighted_apr_with) / float(total_invested_quote)
            apr_without_hedge_pct = float(weighted_apr_without) / float(total_invested_quote)

        avg_apr_with_hedge_pct = 0.0
        if int(avg_apr_n) > 0:
            avg_apr_with_hedge_pct = float(avg_apr_sum) / float(avg_apr_n)

        avg_iteration_lifetime_sec = 0.0
        if int(hold_seconds_n) > 0:
            avg_iteration_lifetime_sec = float(hold_seconds_sum) / float(hold_seconds_n)

        return FrontendDashboardView(
            active_runs=int(active_runs),
            finished_iterations=int(finished_iterations),
            total_invested_quote=float(total_invested_quote),
            total_pnl_with_hedge_quote=float(total_pnl_with_hedge_quote),
            total_pnl_without_hedge_quote=float(total_pnl_without_hedge_quote),
            total_costs_quote=float(total_costs_quote),
            apr_with_hedge_pct=float(apr_with_hedge_pct),
            apr_without_hedge_pct=float(apr_without_hedge_pct),
            avg_apr_with_hedge_pct=float(avg_apr_with_hedge_pct),
            avg_iteration_lifetime_sec=float(avg_iteration_lifetime_sec),
        )

    def list_runtime_positions(self) -> List[BackendPositionView]:
        return self._backend_api.list_runtime_positions()

    def _build_position_row_from_active(self, doc: FrontendActivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(doc.position, doc.iterations_count, True)

    def _build_position_row_from_archive(self, doc: FrontendArchivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(doc.position, doc.iterations_count, False)

    def _build_position_row_common(self, position: BackendPositionView, iterations_count: int, is_active: bool) -> FrontendPositionRow:
        total_quote = float(position.total_quote)
        pnl_with_hedge_quote = float(position.pnl_with_hedge_quote)
        pnl_without_hedge_quote = float(position.pnl_without_hedge_quote)

        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_position_row_common: total_quote <= 0: {total_quote}')

        pnl_with_hedge_pct = (float(pnl_with_hedge_quote) / float(total_quote)) * 100.0
        pnl_without_hedge_pct = (float(pnl_without_hedge_quote) / float(total_quote)) * 100.0

        return FrontendPositionRow(
            run_id=str(position.run_id),
            symbol=str(position.symbol),
            status=BackendRunLifecycle(str(position.status.value)),
            first_started_at_ms=int(position.first_started_at_ms),
            runtime_sec=float(position.runtime_sec),
            runtime_dhm=str(position.runtime_dhm),
            avg_iteration_lifetime_sec=float(position.avg_iteration_lifetime_sec),
            iterations_finished=int(position.iterations_finished),
            market_price=None if position.market_price is None else float(position.market_price),
            price_lower=None if position.price_lower is None else float(position.price_lower),
            price_upper=None if position.price_upper is None else float(position.price_upper),
            price_lower_pct=None if position.price_lower_pct is None else float(position.price_lower_pct),
            price_upper_pct=None if position.price_upper_pct is None else float(position.price_upper_pct),
            total_quote=float(total_quote),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(position.apr_with_hedge_pct),
            apr_without_hedge_pct=float(position.apr_without_hedge_pct),
            fees_quote=float(position.fees_quote),
            price_pnl_quote=float(position.price_pnl_quote),
            hedge_pnl_quote=float(position.hedge_pnl_quote),
            costs_quote=float(position.costs_quote),
            last_error=None if position.last_error is None else str(position.last_error),
            iterations_count=int(iterations_count),
            is_active=bool(is_active),
        )

    def _build_iteration_row(self, row: FrontendIterationDoc) -> FrontendIterationRow:
        stats = row.stats
        calc = stats.calc
        live = stats.live

        total_quote = float(calc.total_quote)
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: total_quote <= 0: {total_quote}')

        runtime_sec = float(int(row.finished_at_ms) - int(row.started_at_ms)) / 1000.0
        if float(runtime_sec) < 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: runtime_sec is negative: {runtime_sec}')

        pnl_with_hedge_pct = float(row.pnl_with_hedge_quote) / float(total_quote) * 100.0
        pnl_without_hedge_pct = float(row.pnl_without_hedge_quote) / float(total_quote) * 100.0

        pool_hold_seconds = float(row.pnl.pool_hold_seconds)
        if float(pool_hold_seconds) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: pool_hold_seconds <= 0: {pool_hold_seconds}')

        apr_with_hedge_pct = self._calc_apr(float(row.pnl_with_hedge_quote), float(total_quote), float(pool_hold_seconds))
        apr_without_hedge_pct = self._calc_apr(float(row.pnl_without_hedge_quote), float(total_quote), float(pool_hold_seconds))

        close_reason = None
        if live.last_snapshot is not None and live.last_snapshot.close_reason is not None:
            close_reason = str(live.last_snapshot.close_reason)

        return FrontendIterationRow(
            id=str(row.id),
            run_id=str(row.run_id),
            iteration_no=int(row.iteration_no),
            started_at_ms=int(row.started_at_ms),
            finished_at_ms=int(row.finished_at_ms),
            runtime_sec=float(runtime_sec),
            status=str(row.status),
            close_reason=close_reason,
            total_quote=float(total_quote),
            price_lower=float(calc.price_lower),
            price_upper=float(calc.price_upper),
            pnl_with_hedge_quote=float(row.pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(row.pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(apr_with_hedge_pct),
            apr_without_hedge_pct=float(apr_without_hedge_pct),
            fees_quote=float(row.pnl.fees_received_quote),
            price_pnl_quote=float(row.pnl.dex_realized_il_quote),
            hedge_pnl_quote=float(row.pnl.cex_pnl_quote),
            costs_quote=float(row.costs_quote),
            error=None if row.error is None else str(row.error),
        )

    def _build_lp_block(self, row: FrontendIterationDoc) -> FrontendIterationLpBlock:
        stats = row.stats
        calc = stats.calc
        uniswap = stats.uniswap
        pnl = row.pnl

        mint_base = 0.0
        mint_quote = 0.0
        if uniswap.mint is not None:
            mint_base = 0.0 if uniswap.mint.amount_base is None else float(uniswap.mint.amount_base)
            mint_quote = 0.0 if uniswap.mint.amount_quote is None else float(uniswap.mint.amount_quote)

        closed_base = 0.0
        closed_quote = 0.0
        if uniswap.decrease is not None:
            closed_base = 0.0 if uniswap.decrease.amount_base is None else float(uniswap.decrease.amount_base)
            closed_quote = 0.0 if uniswap.decrease.amount_quote is None else float(uniswap.decrease.amount_quote)

        apr_price_pct = self._calc_apr(float(pnl.dex_realized_il_quote), float(calc.total_quote), float(pnl.pool_hold_seconds))
        apr_fees_pct = self._calc_apr(float(pnl.fees_received_quote), float(calc.total_quote), float(pnl.pool_hold_seconds))
        apr_total_pct = self._calc_apr(float(row.pnl_without_hedge_quote), float(calc.total_quote), float(pnl.pool_hold_seconds))

        return FrontendIterationLpBlock(
            base_price=float(calc.base_price),
            price_lower=float(calc.price_lower),
            price_upper=float(calc.price_upper),
            minted_base=float(mint_base),
            minted_quote=float(mint_quote),
            closed_base=float(closed_base),
            closed_quote=float(closed_quote),
            fees_quote=float(pnl.fees_received_quote),
            price_pnl_quote=float(pnl.dex_realized_il_quote),
            impermanent_loss_quote=float(-pnl.dex_realized_il_quote),
            apr_fees_pct=float(apr_fees_pct),
            apr_price_pct=float(apr_price_pct),
            apr_total_pct=float(apr_total_pct),
        )

    def _build_hedge_block(self, row: FrontendIterationDoc) -> FrontendIterationHedgeBlock:
        stats = row.stats
        calc = stats.calc
        live = stats.live
        pnl = row.pnl

        activated = False
        opened_leg = None
        opened_base_units = 0
        close_reason = None
        realized_pnl_quote_units = 0
        unrealized_pnl_quote_units = 0
        opened_ms = 0
        closed_ms = 0

        if live.last_snapshot is not None:
            activated = live.last_snapshot.opened_leg is not None
            opened_leg = None if live.last_snapshot.opened_leg is None else str(live.last_snapshot.opened_leg)
            opened_base_units = int(live.last_snapshot.opened_base_units)
            close_reason = None if live.last_snapshot.close_reason is None else str(live.last_snapshot.close_reason)
            realized_pnl_quote_units = int(live.last_snapshot.metrics.realized_pnl_quote_units)
            unrealized_pnl_quote_units = int(live.last_snapshot.metrics.unrealized_pnl_quote_units)
            opened_ms = int(live.last_snapshot.metrics.opened_ms)
            closed_ms = int(live.last_snapshot.metrics.closed_ms)

        activation_price = None
        if bool(activated):
            activation_price = float(calc.base_price)

        return FrontendIterationHedgeBlock(
            activated=bool(activated),
            activation_price=activation_price,
            hedge_quote=float(calc.hedge_quote),
            opened_leg=opened_leg,
            opened_base_units=int(opened_base_units),
            cex_pnl_quote=float(pnl.cex_pnl_quote),
            realized_pnl_quote_units=int(realized_pnl_quote_units),
            unrealized_pnl_quote_units=int(unrealized_pnl_quote_units),
            opened_ms=int(opened_ms),
            closed_ms=int(closed_ms),
            close_reason=close_reason,
        )

    def _build_rebalance_block(self, row: FrontendIterationDoc) -> FrontendIterationRebalanceBlock:
        rebalance = row.stats.uniswap.rebalance
        if rebalance is None:
            return FrontendIterationRebalanceBlock(
                swap_ok=False,
                swap_error='rebalance is missing',
                sell_amount=None,
                buy_amount=None,
                execution_fee=None,
                fee_token=None,
                elapsed_sec=None,
                order_status=None,
                order_url=None,
                swap_cost_quote=float(row.swap_cost_quote),
            )

        if rebalance.order is None:
            return FrontendIterationRebalanceBlock(
                swap_ok=bool(rebalance.ok),
                swap_error=None if rebalance.error is None else str(rebalance.error),
                sell_amount=None,
                buy_amount=None,
                execution_fee=None,
                fee_token=None,
                elapsed_sec=None,
                order_status=None,
                order_url=None,
                swap_cost_quote=float(row.swap_cost_quote),
            )

        order = rebalance.order
        return FrontendIterationRebalanceBlock(
            swap_ok=bool(rebalance.ok),
            swap_error=None if rebalance.error is None else str(rebalance.error),
            sell_amount=float(order.sell_amount),
            buy_amount=float(order.buy_amount),
            execution_fee=float(order.fee_amount),
            fee_token=str(order.fee_token),
            elapsed_sec=int(order.elapsed_sec),
            order_status=str(order.status),
            order_url=str(order.url),
            swap_cost_quote=float(row.swap_cost_quote),
        )

    def _build_hedge_chases(self, row: FrontendIterationDoc) -> List[FrontendIterationHedgeChaseRow]:
        out = []
        live = row.stats.live
        if live.last_snapshot is None:
            return out

        for chase in live.last_snapshot.metrics.chases:
            out.append(
                FrontendIterationHedgeChaseRow(
                    kind=str(chase.kind),
                    started_ms=int(chase.started_ms),
                    finished_ms=int(chase.finished_ms),
                    order_side=str(chase.order_side),
                    filled_base_units=int(chase.filled_base_units),
                    filled_quote_units=int(chase.filled_quote_units),
                    avg_price_units=int(chase.avg_price_units),
                    slippage_pct_x10000=int(chase.slippage_pct_x10000),
                    gtx_violations=int(chase.gtx_violations),
                    exchange_errors=int(chase.exchange_errors),
                    ok=None if chase.ok is None else bool(chase.ok),
                    error=None if chase.error is None else str(chase.error),
                )
            )

        return out

    def _calc_apr(self, pnl_quote: float, total_quote: float, hold_sec: float) -> float:
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._calc_apr: total_quote <= 0: {total_quote}')
        if float(hold_sec) <= 0.0:
            raise RuntimeError(f'FrontendService._calc_apr: hold_sec <= 0: {hold_sec}')

        seconds_per_year = 365.0 * 24.0 * 60.0 * 60.0
        return (float(pnl_quote) / float(total_quote)) * (float(seconds_per_year) / float(hold_sec)) * 100.0
