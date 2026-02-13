"""
Frontend domain service
Date: 2026-02-13
Version: 1.0
"""
from typing import Any, Dict, List, Optional, Tuple

import orjson

from live.lib.logger import get_logger
from modules.frontend.models.frontend_models import (
    FrontendActivePositionDoc,
    FrontendArchivePositionDoc,
    FrontendDashboardView,
    FrontendIterationDetailsView,
    FrontendIterationDoc,
    FrontendIterationRow,
    FrontendPositionRow,
    FrontendRunDetailsView,
    FrontendRunLifecycle,
)
from modules.frontend.services.backend_api_service import BackendApiService
from modules.frontend.services.storage_service import StorageService


class FrontendService:
    def __init__(self, storage: StorageService, backend_api: BackendApiService):
        if storage is None:
            raise RuntimeError('FrontendService: storage is None')
        if backend_api is None:
            raise RuntimeError('FrontendService: backend_api is None')

        self._storage = storage
        self._backend_api = backend_api
        self._logger = get_logger('frontend_service')

    def start_run_from_json(self, raw_json: str) -> str:
        if not isinstance(raw_json, str) or len(raw_json.strip()) == 0:
            raise RuntimeError('FrontendService.start_run_from_json: raw_json is empty')

        payload = orjson.loads(raw_json.encode('utf-8'))
        if not isinstance(payload, dict):
            raise RuntimeError(f'FrontendService.start_run_from_json: payload is not dict: {type(payload)}')
        if 'config' not in payload:
            raise RuntimeError('FrontendService.start_run_from_json: config is missing in payload')
        if not isinstance(payload['config'], dict):
            raise RuntimeError(f'FrontendService.start_run_from_json: config is not dict: {type(payload["config"])}')

        run_id = self._backend_api.start_run(dict(payload))
        self._logger.info(f'frontend_start_run_ok run_id={run_id}')
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
        row = self._storage.find_iteration(iteration_id)
        iteration_row = self._build_iteration_row(row)

        return FrontendIterationDetailsView(
            row=iteration_row,
            stats=dict(row.stats),
            pnl=dict(row.pnl),
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
            if row.status in [FrontendRunLifecycle.INITIALIZED, FrontendRunLifecycle.RUNNING, FrontendRunLifecycle.STOPPING]:
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

    def list_runtime_positions(self) -> List[Dict]:
        return self._backend_api.list_runtime_positions()

    def _build_position_row_from_active(self, doc: FrontendActivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(doc.position, doc.iterations_count, True)

    def _build_position_row_from_archive(self, doc: FrontendArchivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(doc.position, doc.iterations_count, False)

    def _build_position_row_common(self, position: dict, iterations_count: int, is_active: bool) -> FrontendPositionRow:
        total_quote = float(position['total_quote'])
        pnl_with_hedge_quote = float(position['pnl_with_hedge_quote'])
        pnl_without_hedge_quote = float(position['pnl_without_hedge_quote'])

        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_position_row_common: total_quote <= 0: {total_quote}')

        pnl_with_hedge_pct = (float(pnl_with_hedge_quote) / float(total_quote)) * 100.0
        pnl_without_hedge_pct = (float(pnl_without_hedge_quote) / float(total_quote)) * 100.0

        return FrontendPositionRow(
            run_id=str(position['run_id']),
            symbol=str(position['symbol']),
            status=FrontendRunLifecycle(str(position['status'])),
            first_started_at_ms=int(position['first_started_at_ms']),
            runtime_sec=float(position['runtime_sec']),
            runtime_dhm=str(position['runtime_dhm']),
            avg_iteration_lifetime_sec=float(position['avg_iteration_lifetime_sec']),
            iterations_finished=int(position['iterations_finished']),
            market_price=None if position['market_price'] is None else float(position['market_price']),
            price_lower=None if position['price_lower'] is None else float(position['price_lower']),
            price_upper=None if position['price_upper'] is None else float(position['price_upper']),
            price_lower_pct=None if position['price_lower_pct'] is None else float(position['price_lower_pct']),
            price_upper_pct=None if position['price_upper_pct'] is None else float(position['price_upper_pct']),
            total_quote=float(total_quote),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(position['apr_with_hedge_pct']),
            apr_without_hedge_pct=float(position['apr_without_hedge_pct']),
            fees_quote=float(position['fees_quote']),
            price_pnl_quote=float(position['price_pnl_quote']),
            hedge_pnl_quote=float(position['hedge_pnl_quote']),
            costs_quote=float(position['costs_quote']),
            last_error=None if position['last_error'] is None else str(position['last_error']),
            iterations_count=int(iterations_count),
            is_active=bool(is_active),
        )

    def _build_iteration_row(self, row: FrontendIterationDoc) -> FrontendIterationRow:
        calc, uniswap, close_reason = self._extract_iteration_stat_refs(row.stats)

        total_quote = float(calc['total_quote'])
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: total_quote <= 0: {total_quote}')

        runtime_sec = float(int(row.finished_at_ms) - int(row.started_at_ms)) / 1000.0
        if float(runtime_sec) < 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: runtime_sec is negative: {runtime_sec}')

        pnl_with_hedge_pct = float(row.pnl_with_hedge_quote) / float(total_quote) * 100.0
        pnl_without_hedge_pct = float(row.pnl_without_hedge_quote) / float(total_quote) * 100.0

        pool_hold_seconds = float(row.pnl['pool_hold_seconds'])
        if float(pool_hold_seconds) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: pool_hold_seconds <= 0: {pool_hold_seconds}')

        apr_with_hedge_pct = self._calc_apr(float(row.pnl_with_hedge_quote), float(total_quote), float(pool_hold_seconds))
        apr_without_hedge_pct = self._calc_apr(float(row.pnl_without_hedge_quote), float(total_quote), float(pool_hold_seconds))

        price_lower = None
        price_upper = None
        if 'price_lower' in calc and calc['price_lower'] is not None:
            price_lower = float(calc['price_lower'])
        if 'price_upper' in calc and calc['price_upper'] is not None:
            price_upper = float(calc['price_upper'])

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
            price_lower=price_lower,
            price_upper=price_upper,
            pnl_with_hedge_quote=float(row.pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(row.pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(apr_with_hedge_pct),
            apr_without_hedge_pct=float(apr_without_hedge_pct),
            fees_quote=float(row.pnl['fees_received_quote']),
            price_pnl_quote=float(row.pnl['dex_realized_il_quote']),
            hedge_pnl_quote=float(row.pnl['cex_pnl_quote']),
            costs_quote=float(row.costs_quote),
            error=None if row.error is None else str(row.error),
        )

    def _extract_iteration_stat_refs(self, stats: dict) -> Tuple[dict, dict, Optional[str]]:
        if stats is None:
            raise RuntimeError('FrontendService._extract_iteration_stat_refs: stats is None')
        if not isinstance(stats, dict):
            raise RuntimeError(f'FrontendService._extract_iteration_stat_refs: stats is not dict: {type(stats)}')

        if 'calc' not in stats:
            raise RuntimeError('FrontendService._extract_iteration_stat_refs: calc is missing')
        if 'uniswap' not in stats:
            raise RuntimeError('FrontendService._extract_iteration_stat_refs: uniswap is missing')
        if 'live' not in stats:
            raise RuntimeError('FrontendService._extract_iteration_stat_refs: live is missing')

        calc = stats['calc']
        uniswap = stats['uniswap']
        live = stats['live']

        if not isinstance(calc, dict):
            raise RuntimeError(f'FrontendService._extract_iteration_stat_refs: calc is not dict: {type(calc)}')
        if not isinstance(uniswap, dict):
            raise RuntimeError(f'FrontendService._extract_iteration_stat_refs: uniswap is not dict: {type(uniswap)}')
        if not isinstance(live, dict):
            raise RuntimeError(f'FrontendService._extract_iteration_stat_refs: live is not dict: {type(live)}')

        if 'last_snapshot' not in live:
            raise RuntimeError('FrontendService._extract_iteration_stat_refs: live.last_snapshot is missing')

        close_reason = None
        if live['last_snapshot'] is not None:
            last_snapshot = live['last_snapshot']
            if not isinstance(last_snapshot, dict):
                raise RuntimeError(
                    f'FrontendService._extract_iteration_stat_refs: live.last_snapshot is not dict: {type(last_snapshot)}'
                )
            if 'close_reason' not in last_snapshot:
                raise RuntimeError('FrontendService._extract_iteration_stat_refs: live.last_snapshot.close_reason is missing')
            close_reason = None if last_snapshot['close_reason'] is None else str(last_snapshot['close_reason'])

        return calc, uniswap, close_reason

    def _calc_apr(self, pnl_quote: float, total_quote: float, hold_sec: float) -> float:
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._calc_apr: total_quote <= 0: {total_quote}')
        if float(hold_sec) <= 0.0:
            raise RuntimeError(f'FrontendService._calc_apr: hold_sec <= 0: {hold_sec}')

        seconds_per_year = 365.0 * 24.0 * 60.0 * 60.0
        return (float(pnl_quote) / float(total_quote)) * (float(seconds_per_year) / float(hold_sec)) * 100.0
