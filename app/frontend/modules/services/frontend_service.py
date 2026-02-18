"""
Frontend domain service
Date: 2026-02-13
Version: 3.0
"""
import re
import time, uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, List

from live.lib.logger import get_logger
from backend.models.backend_models import BackendPositionView, BackendRunLifecycle, BackendStartRunRequest
from backend.models.hedger_models import HedgerConfig, CexTriggerMode, MockRealtimeSource
from backend.models.mock_hedge_models import MockHedgeBoundary
from frontend.modules.models.frontend_models import (
    FrontendCreateTemplateForm,
    FrontendActivePositionDoc,
    FrontendArchivePositionDoc,
    FrontendDashboardView,
    FrontendDexNetworkConfig,
    FrontendIterationDetailsView,
    FrontendIterationDoc,
    FrontendIterationRow,
    FrontendIterationHedgeBlock,
    FrontendIterationHedgeChaseRow,
    FrontendIterationLpBlock,
    FrontendIterationRebalanceBlock,
    FrontendRuntimeConfig,
    FrontendStartFromTemplateForm,
    FrontendUpdateTemplateForm,
    FrontendIterationDerivedPnl,
    FrontendPnlRecalcAgg,
    FrontendPositionRow,
    FrontendRunTemplateDoc,
    FrontendRunDetailsView,
    frontend_dex_network_configs_from_params,
)
from frontend.modules.services.backend_api_service import BackendApiService
from frontend.modules.services.storage_service import StorageService


class FrontendService:
    def __init__(self, storage: StorageService, backend_api: BackendApiService, runtime_config: FrontendRuntimeConfig):
        if storage is None:
            raise RuntimeError('FrontendService: storage is None')
        if backend_api is None:
            raise RuntimeError('FrontendService: backend_api is None')
        if runtime_config is None:
            raise RuntimeError('FrontendService: runtime_config is None')
        if not isinstance(runtime_config, FrontendRuntimeConfig):
            raise RuntimeError(f'FrontendService: runtime_config is not FrontendRuntimeConfig: {type(runtime_config)}')

        self._storage = storage
        self._backend_api = backend_api
        self._runtime = runtime_config
        self._logger = get_logger('frontend_service')
        self._network_configs = frontend_dex_network_configs_from_params()
        if len(self._network_configs) == 0:
            raise RuntimeError('FrontendService: network configs are empty')

    def list_run_templates(self) -> List[FrontendRunTemplateDoc]:
        return self._storage.list_run_templates()

    def list_network_configs(self) -> List[FrontendDexNetworkConfig]:
        return self._network_configs

    def get_run_template(self, template_id: str) -> FrontendRunTemplateDoc:
        if not isinstance(template_id, str) or len(template_id) == 0:
            raise RuntimeError('FrontendService.get_run_template: template_id is empty')
        return self._storage.find_run_template(str(template_id))

    def create_run_template(self, form: FrontendCreateTemplateForm) -> FrontendRunTemplateDoc:
        if form is None:
            raise RuntimeError('FrontendService.create_run_template: form is None')
        if not isinstance(form, FrontendCreateTemplateForm):
            raise RuntimeError(f'FrontendService.create_run_template: form is not FrontendCreateTemplateForm: {type(form)}')

        self._validate_template_form(
            network=str(form.network),
            symbol=str(form.symbol),
            pool_address=str(form.pool_address),
            fee_pct=float(form.fee_pct),
            cex_ratio=float(form.cex_ratio),
            trigger_mode=form.trigger_mode,
            trigger_pct=float(form.trigger_pct),
            trigger_units=int(form.trigger_units),
        )

        now_ms = int(time.time() * 1000)
        template = FrontendRunTemplateDoc(
            template_id=str(uuid.uuid4().hex),
            network=str(form.network),
            symbol=str(form.symbol),
            pool_address=str(form.pool_address),
            fee_pct=float(form.fee_pct),
            cex_ratio=float(form.cex_ratio),
            trigger_mode=form.trigger_mode,
            trigger_pct=float(form.trigger_pct),
            trigger_units=int(form.trigger_units),
            created_at_ms=int(now_ms),
            updated_at_ms=int(now_ms),
        )
        self._storage.create_run_template(template)
        self._logger.info(f'frontend_create_template_ok template_id={template.template_id}')
        return template

    def update_run_template(self, form: FrontendUpdateTemplateForm) -> FrontendRunTemplateDoc:
        if form is None:
            raise RuntimeError('FrontendService.update_run_template: form is None')
        if not isinstance(form, FrontendUpdateTemplateForm):
            raise RuntimeError(f'FrontendService.update_run_template: form is not FrontendUpdateTemplateForm: {type(form)}')
        if not isinstance(form.template_id, str) or len(form.template_id) == 0:
            raise RuntimeError('FrontendService.update_run_template: template_id is empty')

        self._validate_template_form(
            network=str(form.network),
            symbol=str(form.symbol),
            pool_address=str(form.pool_address),
            fee_pct=float(form.fee_pct),
            cex_ratio=float(form.cex_ratio),
            trigger_mode=form.trigger_mode,
            trigger_pct=float(form.trigger_pct),
            trigger_units=int(form.trigger_units),
        )

        current = self._storage.find_run_template(str(form.template_id))
        now_ms = int(time.time() * 1000)
        template = FrontendRunTemplateDoc(
            template_id=str(current.template_id),
            network=str(form.network),
            symbol=str(form.symbol),
            pool_address=str(form.pool_address),
            fee_pct=float(form.fee_pct),
            cex_ratio=float(form.cex_ratio),
            trigger_mode=form.trigger_mode,
            trigger_pct=float(form.trigger_pct),
            trigger_units=int(form.trigger_units),
            created_at_ms=int(current.created_at_ms),
            updated_at_ms=int(now_ms),
        )

        self._storage.update_run_template(template)
        self._logger.info(f'frontend_update_template_ok template_id={template.template_id}')
        return template

    def delete_run_template(self, template_id: str) -> None:
        if not isinstance(template_id, str) or len(template_id) == 0:
            raise RuntimeError('FrontendService.delete_run_template: template_id is empty')

        self._storage.delete_run_template(str(template_id))
        self._logger.info(f'frontend_delete_template_ok template_id={template_id}')

    def start_run_from_template(self, form: FrontendStartFromTemplateForm) -> str:
        if form is None:
            raise RuntimeError('FrontendService.start_run_from_template: form is None')
        if not isinstance(form, FrontendStartFromTemplateForm):
            raise RuntimeError(f'FrontendService.start_run_from_template: form is not FrontendStartFromTemplateForm: {type(form)}')
        if float(form.total_quote) <= 0.0:
            raise RuntimeError('FrontendService.start_run_from_template: total_quote must be > 0')
        if float(form.price_lower_pct) <= 0.0:
            raise RuntimeError('FrontendService.start_run_from_template: price_lower_pct must be > 0')
        if float(form.price_upper_pct) <= 0.0:
            raise RuntimeError('FrontendService.start_run_from_template: price_upper_pct must be > 0')
        if bool(form.mock_source_dex) and (not bool(form.dex_only)):
            raise RuntimeError('FrontendService.start_run_from_template: mock_source_dex requires dex_only=true')

        template = self._storage.find_run_template(str(form.template_id))
        rpc_url = self._build_rpc_url_for_network(str(template.network))
        ws_url = self._build_ws_url_for_network(str(template.network))
        mock_realtime_source = MockRealtimeSource.LIVE
        if bool(form.mock_source_dex):
            mock_realtime_source = MockRealtimeSource.DEX

        req = BackendStartRunRequest(
            template_id=str(form.template_id),
            config=HedgerConfig(
                symbol=str(template.symbol),
                rpc_url=str(rpc_url),
                network=str(template.network),
                pool_address=str(template.pool_address),
                fee_pct=float(template.fee_pct),
                price_lower=None,
                price_upper=None,
                price_lower_pct=float(form.price_lower_pct),
                price_upper_pct=float(form.price_upper_pct),
                total_quote=float(form.total_quote),
                cex_ratio=float(template.cex_ratio),
                trigger_mode=template.trigger_mode,
                trigger_pct=float(template.trigger_pct),
                trigger_units=int(template.trigger_units),
                mongo_uri=str(self._runtime.mongo_uri),
                mongo_db=str(self._runtime.mongo_db),
                mongo_collection=str(self._runtime.mongo_collection),
                tick_ms=int(self._runtime.tick_ms),
                gtx_cooldown_ms=int(self._runtime.gtx_cooldown_ms),
                entrance_timeout_ms=int(self._runtime.entrance_timeout_ms),
                cowswap_api_timeout_sec=int(self._runtime.cowswap_api_timeout_sec),
                cowswap_wait_timeout_sec=int(self._runtime.cowswap_wait_timeout_sec),
                cowswap_poll_interval_sec=int(self._runtime.cowswap_poll_interval_sec),
                dex_only=bool(form.dex_only),
                mock_realtime_source=mock_realtime_source,
                dex_ws_url=str(ws_url),
            )
        )

        run_id = self._backend_api.start_run(req)
        self._logger.info(f'frontend_start_run_from_template_ok run_id={run_id} template_id={template.template_id}')
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

    def collect_run(self, run_id: str) -> dict:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('FrontendService.collect_run: run_id is empty')
        out = self._backend_api.collect_run(run_id)
        self._logger.info(f'frontend_collect_run_ok run_id={run_id}')
        return out

    def list_positions(self) -> List[FrontendPositionRow]:
        active_docs = self._storage.list_active_positions()
        archive_docs = self._storage.list_archive_positions()
        iteration_docs = self._storage.list_iterations_all()
        runtime_by_run = self._runtime_positions_index()

        out = []

        for item in active_docs:
            run_id = str(item.run_id)
            runtime_position = runtime_by_run.get(run_id)
            if runtime_position is None:
                out.append(self._build_position_row_from_active(item))
            else:
                is_active = runtime_position.status in [
                    BackendRunLifecycle.INITIALIZED,
                    BackendRunLifecycle.RUNNING,
                    BackendRunLifecycle.STOPPING,
                ]
                out.append(self._build_position_row_common(
                    position=runtime_position,
                    iterations_count=item.iterations_count,
                    is_active=bool(is_active),
                    network=str(item.config.network),
                    dex_only=bool(item.config.dex_only),
                    mock_realtime_source=str(item.config.mock_realtime_source.value),
                ))

        for item in archive_docs:
            out.append(self._build_position_row_from_archive(item))

        out = self._recalc_positions_from_iterations(out, iteration_docs)
        out.sort(key=lambda x: int(x.first_started_at_ms), reverse=True)
        return out

    def get_run_details(self, run_id: str) -> FrontendRunDetailsView:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('FrontendService.get_run_details: run_id is empty')

        active_doc = self._storage.find_position(run_id)
        archive_doc = self._storage.find_position_archive(run_id)
        runtime_by_run = self._runtime_positions_index()
        runtime_position = runtime_by_run.get(str(run_id))

        iteration_docs = self._storage.list_iterations_by_run(run_id)
        iteration_rows = []
        agg = self._init_pnl_recalc_agg()

        position_row = None
        config = None
        template_id = None
        if active_doc is not None and runtime_position is not None:
            is_active = runtime_position.status in [
                BackendRunLifecycle.INITIALIZED,
                BackendRunLifecycle.RUNNING,
                BackendRunLifecycle.STOPPING,
            ]
            position_row = self._build_position_row_common(
                position=runtime_position,
                iterations_count=len(iteration_docs),
                is_active=bool(is_active),
                network=str(active_doc.config.network),
                dex_only=bool(active_doc.config.dex_only),
                mock_realtime_source=str(active_doc.config.mock_realtime_source.value),
            )
            config = active_doc.config
            template_id = active_doc.template_id
        elif active_doc is not None:
            position_row = self._build_position_row_from_active(active_doc)
            config = active_doc.config
            template_id = active_doc.template_id
        elif archive_doc is not None:
            position_row = self._build_position_row_from_archive(archive_doc)
            config = archive_doc.config
            template_id = archive_doc.template_id
        else:
            raise RuntimeError(f'FrontendService.get_run_details: run not found: {run_id}')

        network_key = str(config.network)
        for item in iteration_docs:
            derived = self._derive_iteration_components(item)
            iteration_rows.append(self._build_iteration_row(item, network_key, derived))
            self._accumulate_pnl_recalc_agg(agg, derived)

        if len(iteration_docs) > 0:
            position_row = self._apply_pnl_recalc_to_position_row(position_row, agg, f'get_run_details run_id={run_id}')

        failure_error_raw = self._pick_failure_error_raw(position_row, iteration_rows)
        failure_reason = self._describe_failure_reason(failure_error_raw)

        return FrontendRunDetailsView(
            template_id=None if template_id is None else str(template_id),
            config=config,
            position=position_row,
            iterations=iteration_rows,
            failure_reason=failure_reason,
            failure_error_raw=failure_error_raw,
        )

    def _pick_failure_error_raw(self, position_row: FrontendPositionRow, iteration_rows: List[FrontendIterationRow]) -> str | None:
        if position_row.last_error is not None and len(str(position_row.last_error)) > 0:
            return str(position_row.last_error)

        for row in iteration_rows:
            if row.error is not None and len(str(row.error)) > 0:
                return str(row.error)

        return None

    def _describe_failure_reason(self, raw_error: str | None) -> str | None:
        if raw_error is None:
            return None

        err = str(raw_error).strip()
        if len(err) == 0:
            return None

        m = re.search(r'balance0=(\d+)\s+need0=(\d+)\s+balance1=(\d+)\s+need1=(\d+)', err)
        if m is not None:
            balance0 = int(m.group(1))
            need0 = int(m.group(2))
            balance1 = int(m.group(3))
            need1 = int(m.group(4))
            miss0 = max(0, need0 - balance0)
            miss1 = max(0, need1 - balance1)
            return (
                'Run failed on DEX mint step due to insufficient wallet balance.\n'
                f'token0: available={balance0}, required={need0}, deficit={miss0}\n'
                f'token1: available={balance1}, required={need1}, deficit={miss1}\n'
                'To fix: add missing funds for deficit token(s), or reduce total_quote/range.'
            )

        if 'insufficient balance' in err.lower():
            return (
                'Run failed due to insufficient wallet balance for the configured mint amounts.\n'
                'To fix: add funds to wallet, or reduce total_quote/range.'
            )

        return f'Run failed with backend error: {err}'

    def get_iteration_details(self, iteration_id: str) -> FrontendIterationDetailsView:
        item = self._storage.find_iteration(iteration_id)
        network_key = self._resolve_network_by_run_id(str(item.run_id))
        derived = self._derive_iteration_components(item)
        iteration_row = self._build_iteration_row(item, network_key, derived)
        lp = self._build_lp_block(item, derived)
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
        close_trigger_upper_count = 0
        close_trigger_lower_count = 0
        total_invested_quote = 0.0
        total_pnl_with_hedge_quote = 0.0
        total_pnl_without_hedge_quote = 0.0
        total_costs_quote = 0.0
        hold_seconds_sum = 0.0
        hold_seconds_n = 0
        total_hold_seconds = 0.0

        for row in positions:
            if row.status in [BackendRunLifecycle.INITIALIZED, BackendRunLifecycle.RUNNING, BackendRunLifecycle.STOPPING]:
                active_runs += 1

            finished_iterations += int(row.iterations_finished)
            close_trigger_upper_count += int(row.close_trigger_upper_count)
            close_trigger_lower_count += int(row.close_trigger_lower_count)
            # Dashboard assumes runs are sequential and reuse the same working capital.
            # Use the working-capital base (max run quote), not sum across runs.
            total_invested_quote = max(float(total_invested_quote), float(row.total_quote))
            total_pnl_with_hedge_quote += float(row.pnl_with_hedge_quote)
            total_pnl_without_hedge_quote += float(row.pnl_without_hedge_quote)
            # Frontend row stores costs as signed PnL component (usually negative).
            total_costs_quote += float(row.costs_quote)

            if float(row.avg_iteration_lifetime_sec) > 0.0:
                hold_seconds_sum += float(row.avg_iteration_lifetime_sec)
                hold_seconds_n += 1
            total_hold_seconds += float(row.avg_iteration_lifetime_sec) * float(row.iterations_finished)

        if float(total_invested_quote) <= 0.0 and len(positions) > 0:
            raise RuntimeError('FrontendService.build_dashboard: total_invested_quote <= 0 for non-empty positions')

        apr_with_hedge_pct = 0.0
        apr_without_hedge_pct = 0.0
        if float(total_invested_quote) > 0.0 and float(total_hold_seconds) > 0.0:
            apr_with_hedge_pct = self._calc_apr(
                float(total_pnl_with_hedge_quote),
                float(total_invested_quote),
                float(total_hold_seconds),
            )
            apr_without_hedge_pct = self._calc_apr(
                float(total_pnl_without_hedge_quote),
                float(total_invested_quote),
                float(total_hold_seconds),
            )

        # Keep dashboard "Average APR per run" aligned with dashboard methodology.
        avg_apr_with_hedge_pct = float(apr_with_hedge_pct)

        close_trigger_total_count = int(close_trigger_upper_count) + int(close_trigger_lower_count)
        close_trigger_upper_pct = 0.0
        close_trigger_lower_pct = 0.0
        if int(close_trigger_total_count) > 0:
            close_trigger_upper_pct = float(close_trigger_upper_count) / float(close_trigger_total_count) * 100.0
            close_trigger_lower_pct = float(close_trigger_lower_count) / float(close_trigger_total_count) * 100.0

        avg_iteration_lifetime_sec = 0.0
        if int(hold_seconds_n) > 0:
            avg_iteration_lifetime_sec = float(hold_seconds_sum) / float(hold_seconds_n)

        return FrontendDashboardView(
            active_runs=int(active_runs),
            finished_iterations=int(finished_iterations),
            close_trigger_upper_count=int(close_trigger_upper_count),
            close_trigger_lower_count=int(close_trigger_lower_count),
            close_trigger_total_count=int(close_trigger_total_count),
            close_trigger_upper_pct=float(close_trigger_upper_pct),
            close_trigger_lower_pct=float(close_trigger_lower_pct),
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

    def _runtime_positions_index(self) -> Dict[str, BackendPositionView]:
        out: Dict[str, BackendPositionView] = {}
        try:
            runtime_rows = self.list_runtime_positions()
            for row in runtime_rows:
                out[str(row.run_id)] = row
        except Exception as exc:
            self._logger.warning(f'FrontendService._runtime_positions_index: backend runtime unavailable error={exc}')
        return out

    def _build_position_row_from_active(self, doc: FrontendActivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(
            position=doc.position,
            iterations_count=doc.iterations_count,
            is_active=True,
            network=str(doc.config.network),
            dex_only=bool(doc.config.dex_only),
            mock_realtime_source=str(doc.config.mock_realtime_source.value),
        )

    def _build_position_row_from_archive(self, doc: FrontendArchivePositionDoc) -> FrontendPositionRow:
        return self._build_position_row_common(
            position=doc.position,
            iterations_count=doc.iterations_count,
            is_active=False,
            network=str(doc.config.network),
            dex_only=bool(doc.config.dex_only),
            mock_realtime_source=str(doc.config.mock_realtime_source.value),
        )

    def _build_position_row_common(
        self,
        position: BackendPositionView,
        iterations_count: int,
        is_active: bool,
        network: str,
        dex_only: bool,
        mock_realtime_source: str,
    ) -> FrontendPositionRow:
        total_quote = float(position.total_quote)
        pnl_with_hedge_quote = float(position.pnl_with_hedge_quote)
        pnl_without_hedge_quote = float(position.pnl_without_hedge_quote)
        pnl_fees_quote = float(position.fees_quote)
        pnl_fees_il_quote = float(position.fees_quote) + float(position.price_pnl_quote)
        costs_pnl_quote = -float(position.costs_quote)
        pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(costs_pnl_quote)
        pnl_fees_il_gas_cex_quote = float(pnl_with_hedge_quote)
        hold_sec = float(position.avg_iteration_lifetime_sec) * float(position.iterations_finished)
        apr_fees_pct = 0.0
        apr_fees_il_pct = 0.0
        apr_fees_il_gas_pct = 0.0
        apr_fees_il_gas_cex_pct = 0.0
        if float(hold_sec) > 0.0:
            apr_fees_pct = self._calc_apr(float(pnl_fees_quote), float(total_quote), float(hold_sec))
            apr_fees_il_pct = self._calc_apr(float(pnl_fees_il_quote), float(total_quote), float(hold_sec))
            apr_fees_il_gas_pct = self._calc_apr(float(pnl_fees_il_gas_quote), float(total_quote), float(hold_sec))
            apr_fees_il_gas_cex_pct = self._calc_apr(float(pnl_fees_il_gas_cex_quote), float(total_quote), float(hold_sec))

        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_position_row_common: total_quote <= 0: {total_quote}')
        if not isinstance(network, str) or len(network) == 0:
            raise RuntimeError('FrontendService._build_position_row_common: network is empty')
        if not isinstance(dex_only, bool):
            raise RuntimeError(f'FrontendService._build_position_row_common: dex_only is not bool: {type(dex_only)}')
        if not isinstance(mock_realtime_source, str) or len(mock_realtime_source) == 0:
            raise RuntimeError('FrontendService._build_position_row_common: mock_realtime_source is empty')

        pnl_with_hedge_pct = (float(pnl_with_hedge_quote) / float(total_quote)) * 100.0
        pnl_without_hedge_pct = (float(pnl_without_hedge_quote) / float(total_quote)) * 100.0
        token_id = None if position.token_id is None else int(position.token_id)
        pool_url = None
        revert_link = None
        if token_id is not None and int(token_id) > 0:
            network_key = str(network).lower()
            pool_url = f'https://app.uniswap.org/positions/v3/{network_key}/{int(token_id)}'
            revert_link = f'https://revert.finance/#/uniswap-position/{network_key}/{int(token_id)}'

        return FrontendPositionRow(
            run_id=str(position.run_id),
            network=str(network),
            symbol=str(position.symbol),
            dex_only=bool(dex_only),
            mock_realtime_source=str(mock_realtime_source),
            status=BackendRunLifecycle(str(position.status.value)),
            first_started_at_ms=int(position.first_started_at_ms),
            runtime_sec=float(position.runtime_sec),
            runtime_dhm=str(position.runtime_dhm),
            avg_iteration_lifetime_sec=float(position.avg_iteration_lifetime_sec),
            iterations_finished=int(position.iterations_finished),
            close_trigger_upper_count=int(position.close_trigger_upper_count),
            close_trigger_lower_count=int(position.close_trigger_lower_count),
            close_trigger_total_count=int(position.close_trigger_total_count),
            close_trigger_upper_pct=float(position.close_trigger_upper_pct),
            close_trigger_lower_pct=float(position.close_trigger_lower_pct),
            market_price=None if position.market_price is None else float(position.market_price),
            price_lower=None if position.price_lower is None else float(position.price_lower),
            price_upper=None if position.price_upper is None else float(position.price_upper),
            price_lower_pct=None if position.price_lower_pct is None else float(position.price_lower_pct),
            price_upper_pct=None if position.price_upper_pct is None else float(position.price_upper_pct),
            total_quote=float(total_quote),
            pnl_fees_quote=float(pnl_fees_quote),
            pnl_fees_il_quote=float(pnl_fees_il_quote),
            pnl_fees_il_gas_quote=float(pnl_fees_il_gas_quote),
            pnl_fees_il_gas_cex_quote=float(pnl_fees_il_gas_cex_quote),
            apr_fees_pct=float(apr_fees_pct),
            apr_fees_il_pct=float(apr_fees_il_pct),
            apr_fees_il_gas_pct=float(apr_fees_il_gas_pct),
            apr_fees_il_gas_cex_pct=float(apr_fees_il_gas_cex_pct),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(position.apr_with_hedge_pct),
            apr_without_hedge_pct=float(position.apr_without_hedge_pct),
            fees_quote=float(position.fees_quote),
            price_pnl_quote=float(position.price_pnl_quote),
            hedge_pnl_quote=float(position.hedge_pnl_quote),
            costs_quote=float(costs_pnl_quote),
            token_id=token_id,
            pool_url=pool_url,
            revert_link=revert_link,
            last_error=None if position.last_error is None else str(position.last_error),
            iterations_count=int(iterations_count),
            is_active=bool(is_active),
        )

    def _build_iteration_row(
        self,
        row: FrontendIterationDoc,
        network: str,
        derived: FrontendIterationDerivedPnl,
    ) -> FrontendIterationRow:
        stats = row.stats
        calc = stats.calc
        live = stats.live

        total_quote = float(calc.total_quote)
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: total_quote <= 0: {total_quote}')

        runtime_sec = float(int(row.finished_at_ms) - int(row.started_at_ms)) / 1000.0
        if float(runtime_sec) < 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: runtime_sec is negative: {runtime_sec}')

        pnl_with_hedge_quote = float(derived.pnl_with_hedge_quote)
        pnl_without_hedge_quote = float(derived.pnl_without_hedge_quote)
        pnl_with_hedge_pct = float(pnl_with_hedge_quote) / float(total_quote) * 100.0
        pnl_without_hedge_pct = float(pnl_without_hedge_quote) / float(total_quote) * 100.0
        pnl_fees_quote = float(derived.fees_quote)
        pnl_fees_il_quote = float(derived.fees_quote) + float(derived.il_quote)
        costs_pnl_quote = float(derived.costs_pnl_quote)
        pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(costs_pnl_quote)
        pnl_fees_il_gas_cex_quote = float(pnl_with_hedge_quote)

        pool_hold_seconds = float(derived.pool_hold_seconds)
        if float(pool_hold_seconds) <= 0.0:
            raise RuntimeError(f'FrontendService._build_iteration_row: pool_hold_seconds <= 0: {pool_hold_seconds}')

        apr_with_hedge_pct = self._calc_apr(float(pnl_with_hedge_quote), float(total_quote), float(pool_hold_seconds))
        apr_without_hedge_pct = self._calc_apr(float(pnl_without_hedge_quote), float(total_quote), float(pool_hold_seconds))
        apr_fees_pct = self._calc_apr(float(pnl_fees_quote), float(total_quote), float(pool_hold_seconds))
        apr_fees_il_pct = self._calc_apr(float(pnl_fees_il_quote), float(total_quote), float(pool_hold_seconds))
        apr_fees_il_gas_pct = self._calc_apr(float(pnl_fees_il_gas_quote), float(total_quote), float(pool_hold_seconds))
        apr_fees_il_gas_cex_pct = self._calc_apr(float(pnl_fees_il_gas_cex_quote), float(total_quote), float(pool_hold_seconds))

        close_reason = None
        if live.last_snapshot is not None and live.last_snapshot.close_reason is not None:
            close_reason = str(live.last_snapshot.close_reason)

        token_id = None if stats.uniswap.token_id is None else int(stats.uniswap.token_id)
        pool_url = None
        revert_link = None
        if token_id is not None and int(token_id) > 0:
            network_key = str(network).lower()
            pool_url = f'https://app.uniswap.org/positions/v3/{network_key}/{int(token_id)}'
            revert_link = f'https://revert.finance/#/uniswap-position/{network_key}/{int(token_id)}'

        return FrontendIterationRow(
            id=str(row.id),
            run_id=str(row.run_id),
            iteration_no=int(row.iteration_no),
            started_at_ms=int(row.started_at_ms),
            finished_at_ms=int(row.finished_at_ms),
            runtime_sec=float(runtime_sec),
            status=str(row.status),
            close_reason=close_reason,
            close_trigger_side=row.close_trigger_side,
            total_quote=float(total_quote),
            price_lower=float(calc.price_lower),
            price_upper=float(calc.price_upper),
            pnl_fees_quote=float(pnl_fees_quote),
            pnl_fees_il_quote=float(pnl_fees_il_quote),
            pnl_fees_il_gas_quote=float(pnl_fees_il_gas_quote),
            pnl_fees_il_gas_cex_quote=float(pnl_fees_il_gas_cex_quote),
            apr_fees_pct=float(apr_fees_pct),
            apr_fees_il_pct=float(apr_fees_il_pct),
            apr_fees_il_gas_pct=float(apr_fees_il_gas_pct),
            apr_fees_il_gas_cex_pct=float(apr_fees_il_gas_cex_pct),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_pct=float(pnl_with_hedge_pct),
            pnl_without_hedge_pct=float(pnl_without_hedge_pct),
            apr_with_hedge_pct=float(apr_with_hedge_pct),
            apr_without_hedge_pct=float(apr_without_hedge_pct),
            fees_quote=float(derived.fees_quote),
            price_pnl_quote=float(derived.il_quote),
            hedge_pnl_quote=float(derived.cex_quote),
            costs_quote=float(costs_pnl_quote),
            error=None if row.error is None else str(row.error),
            token_id=token_id,
            pool_url=pool_url,
            revert_link=revert_link,
        )

    def _resolve_network_by_run_id(self, run_id: str) -> str:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('FrontendService._resolve_network_by_run_id: run_id is empty')

        active_doc = self._storage.find_position(run_id)
        if active_doc is not None:
            return str(active_doc.config.network)

        archive_doc = self._storage.find_position_archive(run_id)
        if archive_doc is not None:
            return str(archive_doc.config.network)

        raise RuntimeError(f'FrontendService._resolve_network_by_run_id: run not found: {run_id}')

    def _build_lp_block(
        self,
        row: FrontendIterationDoc,
        derived: FrontendIterationDerivedPnl,
    ) -> FrontendIterationLpBlock:
        stats = row.stats
        calc = stats.calc
        uniswap = stats.uniswap

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

        return FrontendIterationLpBlock(
            base_price=float(calc.base_price),
            price_lower=float(calc.price_lower),
            price_upper=float(calc.price_upper),
            minted_base=float(mint_base),
            minted_quote=float(mint_quote),
            closed_base=float(closed_base),
            closed_quote=float(closed_quote),
            fees_quote=float(derived.fees_quote),
            price_pnl_quote=float(derived.il_quote),
            impermanent_loss_quote=float(derived.il_quote),
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
            close_trigger_side=row.close_trigger_side,
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
                swap_cost_quote=-float(row.swap_cost_quote),
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
                swap_cost_quote=-float(row.swap_cost_quote),
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
            swap_cost_quote=-float(row.swap_cost_quote),
        )

    def _recalc_positions_from_iterations(
        self,
        rows: List[FrontendPositionRow],
        iteration_docs: List[FrontendIterationDoc],
    ) -> List[FrontendPositionRow]:
        by_run: Dict[str, FrontendPnlRecalcAgg] = {}
        for item in iteration_docs:
            derived = self._derive_iteration_components(item)
            run_id = str(derived.run_id)
            if run_id not in by_run:
                by_run[run_id] = self._init_pnl_recalc_agg()

            agg = by_run[run_id]
            self._accumulate_pnl_recalc_agg(agg, derived)

        out: List[FrontendPositionRow] = []
        for row in rows:
            run_id = str(row.run_id)
            if run_id not in by_run:
                out.append(row)
                continue

            out.append(self._apply_pnl_recalc_to_position_row(row, by_run[run_id], f'_recalc_positions run_id={run_id}'))

        return out

    def _init_pnl_recalc_agg(self) -> FrontendPnlRecalcAgg:
        return FrontendPnlRecalcAgg(
            sum_fees_quote=0.0,
            sum_il_quote=0.0,
            sum_cex_quote=0.0,
            sum_costs_pnl_quote=0.0,
            sum_pool_hold_seconds=0.0,
            iterations_finished=0,
        )

    def _accumulate_pnl_recalc_agg(self, agg: FrontendPnlRecalcAgg, calc: FrontendIterationDerivedPnl) -> None:
        agg.sum_fees_quote += float(calc.fees_quote)
        agg.sum_il_quote += float(calc.il_quote)
        agg.sum_cex_quote += float(calc.cex_quote)
        agg.sum_costs_pnl_quote += float(calc.costs_pnl_quote)
        agg.sum_pool_hold_seconds += float(calc.pool_hold_seconds)
        if bool(calc.is_finished):
            agg.iterations_finished += 1

    def _apply_pnl_recalc_to_position_row(
        self,
        row: FrontendPositionRow,
        agg: FrontendPnlRecalcAgg,
        source_tag: str,
    ) -> FrontendPositionRow:
        total_quote = float(row.total_quote)
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService.{source_tag}: total_quote <= 0 for run_id={row.run_id}')

        il_quote = float(agg.sum_il_quote)
        pnl_fees_quote = float(agg.sum_fees_quote)
        pnl_fees_il_quote = float(pnl_fees_quote) + float(il_quote)
        pnl_fees_il_gas_quote = float(pnl_fees_il_quote) + float(agg.sum_costs_pnl_quote)
        pnl_fees_il_gas_cex_quote = float(pnl_fees_il_gas_quote) + float(agg.sum_cex_quote)
        pnl_without_hedge_quote = float(pnl_fees_il_gas_quote)
        pnl_with_hedge_quote = float(pnl_fees_il_gas_cex_quote)
        hold_sec = float(agg.sum_pool_hold_seconds)

        apr_with_hedge_pct = 0.0
        apr_without_hedge_pct = 0.0
        apr_fees_pct = 0.0
        apr_fees_il_pct = 0.0
        apr_fees_il_gas_pct = 0.0
        apr_fees_il_gas_cex_pct = 0.0
        if float(hold_sec) > 0.0:
            apr_with_hedge_pct = self._calc_apr(float(pnl_with_hedge_quote), float(total_quote), float(hold_sec))
            apr_without_hedge_pct = self._calc_apr(float(pnl_without_hedge_quote), float(total_quote), float(hold_sec))
            apr_fees_pct = self._calc_apr(float(pnl_fees_quote), float(total_quote), float(hold_sec))
            apr_fees_il_pct = self._calc_apr(float(pnl_fees_il_quote), float(total_quote), float(hold_sec))
            apr_fees_il_gas_pct = self._calc_apr(float(pnl_fees_il_gas_quote), float(total_quote), float(hold_sec))
            apr_fees_il_gas_cex_pct = self._calc_apr(float(pnl_fees_il_gas_cex_quote), float(total_quote), float(hold_sec))

        avg_iteration_lifetime_sec = 0.0
        iterations_finished = int(agg.iterations_finished)
        if int(iterations_finished) > 0:
            avg_iteration_lifetime_sec = float(hold_sec) / float(iterations_finished)

        return row.model_copy(update={
            'iterations_finished': int(iterations_finished),
            'avg_iteration_lifetime_sec': float(avg_iteration_lifetime_sec),
            'pnl_with_hedge_quote': float(pnl_with_hedge_quote),
            'pnl_without_hedge_quote': float(pnl_without_hedge_quote),
            'pnl_with_hedge_pct': float(pnl_with_hedge_quote / total_quote * 100.0),
            'pnl_without_hedge_pct': float(pnl_without_hedge_quote / total_quote * 100.0),
            'apr_with_hedge_pct': float(apr_with_hedge_pct),
            'apr_without_hedge_pct': float(apr_without_hedge_pct),
            'fees_quote': float(agg.sum_fees_quote),
            'price_pnl_quote': float(il_quote),
            'hedge_pnl_quote': float(agg.sum_cex_quote),
            'costs_quote': float(agg.sum_costs_pnl_quote),
            'pnl_fees_quote': float(pnl_fees_quote),
            'pnl_fees_il_quote': float(pnl_fees_il_quote),
            'pnl_fees_il_gas_quote': float(pnl_fees_il_gas_quote),
            'pnl_fees_il_gas_cex_quote': float(pnl_fees_il_gas_cex_quote),
            'apr_fees_pct': float(apr_fees_pct),
            'apr_fees_il_pct': float(apr_fees_il_pct),
            'apr_fees_il_gas_pct': float(apr_fees_il_gas_pct),
            'apr_fees_il_gas_cex_pct': float(apr_fees_il_gas_cex_pct),
        })

    def _derive_iteration_components(self, item: FrontendIterationDoc) -> FrontendIterationDerivedPnl:
        if item is None:
            raise RuntimeError('FrontendService._derive_iteration_components: item is None')
        if not isinstance(item, FrontendIterationDoc):
            raise RuntimeError(f'FrontendService._derive_iteration_components: item is not FrontendIterationDoc: {type(item)}')

        stats = item.stats
        calc = stats.calc
        uniswap = stats.uniswap
        live = stats.live

        if live.last_snapshot is None:
            raise RuntimeError('FrontendService._derive_iteration_components: stats.live.last_snapshot is None')
        snap = live.last_snapshot

        if snap.symbol_rule is None:
            raise RuntimeError('FrontendService._derive_iteration_components: stats.live.last_snapshot.symbol_rule is None')

        price_step_raw = str(snap.symbol_rule.price_step).strip()
        if len(price_step_raw) == 0:
            raise RuntimeError('FrontendService._derive_iteration_components: symbol_rule.price_step is empty')
        try:
            price_step = Decimal(price_step_raw)
        except InvalidOperation as exc:
            raise RuntimeError(f'FrontendService._derive_iteration_components: invalid price_step: {price_step_raw}') from exc
        if price_step <= 0:
            raise RuntimeError(f'FrontendService._derive_iteration_components: bad price_step: {price_step_raw}')

        last_mid_price_units = int(snap.metrics.last_mid_price_units)
        if int(last_mid_price_units) <= 0:
            raise RuntimeError(
                f'FrontendService._derive_iteration_components: bad last_mid_price_units: {last_mid_price_units}'
            )
        valuation_price = float(Decimal(int(last_mid_price_units)) * price_step)
        if float(valuation_price) <= 0.0:
            raise RuntimeError(f'FrontendService._derive_iteration_components: bad valuation_price: {valuation_price}')

        chases = snap.metrics.chases
        open_filled_quote_units = 0
        for chase in chases:
            if str(chase.kind) == 'open' and bool(chase.ok) and int(chase.filled_quote_units) > 0:
                open_filled_quote_units = int(chase.filled_quote_units)
                break
        if int(open_filled_quote_units) <= 0:
            raise RuntimeError('FrontendService._derive_iteration_components: open_filled_quote_units not found')

        if uniswap.mint is None:
            raise RuntimeError('FrontendService._derive_iteration_components: uniswap.mint is None')
        if uniswap.decrease is None:
            raise RuntimeError('FrontendService._derive_iteration_components: uniswap.decrease is None')
        if uniswap.collect is None:
            raise RuntimeError('FrontendService._derive_iteration_components: uniswap.collect is None')

        mint = uniswap.mint
        decrease = uniswap.decrease
        collect = uniswap.collect

        decrease_base = 0.0 if decrease.amount_base is None else float(decrease.amount_base)
        decrease_quote = 0.0 if decrease.amount_quote is None else float(decrease.amount_quote)
        collect_base = 0.0 if collect.amount_base is None else float(collect.amount_base)
        collect_quote = 0.0 if collect.amount_quote is None else float(collect.amount_quote)

        hedge_quote = float(calc.hedge_quote)
        quote_per_cex_unit = float(hedge_quote) / float(open_filled_quote_units)
        if float(quote_per_cex_unit) <= 0.0:
            raise RuntimeError(f'FrontendService._derive_iteration_components: bad quote_per_cex_unit: {quote_per_cex_unit}')
        cex_units = int(snap.metrics.realized_pnl_quote_units) + int(snap.metrics.unrealized_pnl_quote_units)
        cex_quote = float(cex_units) * float(quote_per_cex_unit)

        if item.close_trigger_side is None:
            raise RuntimeError('FrontendService._derive_iteration_components: close_trigger_side is None')

        exit_price = 0.0
        if item.close_trigger_side == MockHedgeBoundary.UPPER:
            exit_price = float(calc.price_upper)
        elif item.close_trigger_side == MockHedgeBoundary.LOWER:
            exit_price = float(calc.price_lower)
        else:
            raise RuntimeError(
                'FrontendService._derive_iteration_components: unsupported close_trigger_side: '
                f'{item.close_trigger_side}'
            )

        if float(exit_price) <= 0.0:
            raise RuntimeError(f'FrontendService._derive_iteration_components: bad exit_price: {exit_price}')

        total_quote = float(calc.total_quote)
        if float(total_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._derive_iteration_components: bad total_quote: {total_quote}')

        il_quote = float(decrease_base) * float(exit_price) + float(decrease_quote) - float(total_quote)

        fees_quote = (float(collect_quote) - float(decrease_quote)) + (
            (float(collect_base) - float(decrease_base)) * float(valuation_price)
        )

        gas_paid_eth = (
            (0.0 if mint.gas_cost_eth is None else float(mint.gas_cost_eth))
            + (0.0 if decrease.gas_cost_eth is None else float(decrease.gas_cost_eth))
            + (0.0 if collect.gas_cost_eth is None else float(collect.gas_cost_eth))
        )
        gas_paid_quote = float(gas_paid_eth) * float(valuation_price)

        swap_cost_quote = float(item.swap_cost_quote)
        costs_abs_quote = float(gas_paid_quote) + float(swap_cost_quote)
        costs_pnl_quote = -float(costs_abs_quote)

        pnl_without_hedge_quote = float(il_quote) + float(fees_quote) + float(costs_pnl_quote)
        pnl_with_hedge_quote = float(pnl_without_hedge_quote) + float(cex_quote)

        mint_tx_timestamp_ms = int(uniswap.mint_tx_timestamp_ms)
        decrease_tx_timestamp_ms = int(uniswap.decrease_tx_timestamp_ms)
        if int(mint_tx_timestamp_ms) <= 0 or int(decrease_tx_timestamp_ms) <= int(mint_tx_timestamp_ms):
            raise RuntimeError(
                'FrontendService._derive_iteration_components: bad hold timestamps '
                f'mint={mint_tx_timestamp_ms} decrease={decrease_tx_timestamp_ms}'
            )
        pool_hold_seconds = float(int(decrease_tx_timestamp_ms) - int(mint_tx_timestamp_ms)) / 1000.0

        return FrontendIterationDerivedPnl(
            run_id=str(item.run_id),
            iteration_no=int(item.iteration_no),
            is_finished=str(item.status) == 'finished',
            valuation_price=float(valuation_price),
            fees_quote=float(fees_quote),
            il_quote=float(il_quote),
            cex_quote=float(cex_quote),
            costs_pnl_quote=float(costs_pnl_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
            pool_hold_seconds=float(pool_hold_seconds),
        )

    def _build_hedge_chases(self, row: FrontendIterationDoc) -> List[FrontendIterationHedgeChaseRow]:
        out = []
        live = row.stats.live
        if live.last_snapshot is None:
            return out

        chases = live.last_snapshot.metrics.chases
        if len(chases) == 0:
            return out

        hedge_quote = float(row.stats.calc.hedge_quote)
        if float(hedge_quote) <= 0.0:
            raise RuntimeError(f'FrontendService._build_hedge_chases: hedge_quote <= 0: {hedge_quote}')

        open_filled_quote_units = 0
        for chase in chases:
            if str(chase.kind) == 'open' and int(chase.filled_quote_units) > 0:
                open_filled_quote_units = int(chase.filled_quote_units)
                break

        if int(open_filled_quote_units) <= 0:
            raise RuntimeError('FrontendService._build_hedge_chases: open_filled_quote_units not found')

        quote_per_cex_unit = float(hedge_quote) / float(open_filled_quote_units)
        if float(quote_per_cex_unit) <= 0.0:
            raise RuntimeError(f'FrontendService._build_hedge_chases: quote_per_cex_unit <= 0: {quote_per_cex_unit}')

        chases_count = int(len(chases))
        cex_quote_balance_units = 0
        final_cex_pnl_quote_units = (
            int(live.last_snapshot.metrics.realized_pnl_quote_units)
            + int(live.last_snapshot.metrics.unrealized_pnl_quote_units)
        )
        final_cex_pnl_quote = float(final_cex_pnl_quote_units) * float(quote_per_cex_unit)

        for idx in range(chases_count):
            chase = chases[idx]
            if str(chase.order_side) == 'BUY':
                cex_quote_delta_units = -int(chase.filled_quote_units)
            elif str(chase.order_side) == 'SELL':
                cex_quote_delta_units = int(chase.filled_quote_units)
            else:
                raise RuntimeError(f'FrontendService._build_hedge_chases: bad order_side: {chase.order_side}')

            cex_quote_balance_units = int(cex_quote_balance_units) + int(cex_quote_delta_units)
            is_final_pnl = int(idx) == int(chases_count - 1)
            cex_pnl_quote = float(cex_quote_balance_units) * float(quote_per_cex_unit)
            if bool(is_final_pnl):
                cex_pnl_quote = float(final_cex_pnl_quote)

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
                    cex_quote_balance_units=int(cex_quote_balance_units),
                    cex_pnl_quote=float(cex_pnl_quote),
                    is_final_pnl=bool(is_final_pnl),
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

    def _validate_template_form(
        self,
        network: str,
        symbol: str,
        pool_address: str,
        fee_pct: float,
        cex_ratio: float,
        trigger_mode: CexTriggerMode,
        trigger_pct: float,
        trigger_units: int,
    ) -> None:
        if len(str(network)) == 0:
            raise RuntimeError('FrontendService._validate_template_form: network is empty')
        if len(str(symbol)) == 0:
            raise RuntimeError('FrontendService._validate_template_form: symbol is empty')
        if len(str(pool_address)) == 0:
            raise RuntimeError('FrontendService._validate_template_form: pool_address is empty')
        if float(fee_pct) <= 0.0:
            raise RuntimeError('FrontendService._validate_template_form: fee_pct must be > 0')
        if float(cex_ratio) <= 0.0:
            raise RuntimeError('FrontendService._validate_template_form: cex_ratio must be > 0')
        if float(cex_ratio) > 1.0:
            raise RuntimeError('FrontendService._validate_template_form: cex_ratio must be <= 1')

        if trigger_mode == CexTriggerMode.PCT:
            if float(trigger_pct) <= 0.0:
                raise RuntimeError('FrontendService._validate_template_form: trigger_pct must be > 0 for pct mode')
            if int(trigger_units) != 0:
                raise RuntimeError('FrontendService._validate_template_form: trigger_units must be 0 for pct mode')
        elif trigger_mode == CexTriggerMode.UNITS:
            if int(trigger_units) <= 0:
                raise RuntimeError('FrontendService._validate_template_form: trigger_units must be > 0 for units mode')
            if float(trigger_pct) != 0.0:
                raise RuntimeError('FrontendService._validate_template_form: trigger_pct must be 0 for units mode')
        else:
            raise RuntimeError(f'FrontendService._validate_template_form: unsupported trigger_mode: {trigger_mode}')

    def _build_rpc_url_for_network(self, network: str) -> str:
        if not isinstance(network, str) or len(network) == 0:
            raise RuntimeError('FrontendService._build_rpc_url_for_network: network is empty')

        network_config = None
        for item in self._network_configs:
            if str(item.key) == str(network):
                network_config = item
                break

        if network_config is None:
            raise RuntimeError(f'FrontendService._build_rpc_url_for_network: network not found: {network}')

        rpc_url_template = str(network_config.rpc_url_template)
        if len(rpc_url_template) == 0:
            raise RuntimeError(f'FrontendService._build_rpc_url_for_network: rpc_url_template is empty for network={network}')
        if '{RPC_KEY}' not in rpc_url_template:
            raise RuntimeError(f'FrontendService._build_rpc_url_for_network: rpc_url_template does not contain {{RPC_KEY}} for network={network}')

        rpc_key = str(self._runtime.rpc_key)
        if len(rpc_key) == 0:
            raise RuntimeError('FrontendService._build_rpc_url_for_network: rpc_key is empty')

        return str(rpc_url_template).replace('{RPC_KEY}', str(rpc_key))

    def _build_ws_url_for_network(self, network: str) -> str:
        if not isinstance(network, str) or len(network) == 0:
            raise RuntimeError('FrontendService._build_ws_url_for_network: network is empty')

        network_config = None
        for item in self._network_configs:
            if str(item.key) == str(network):
                network_config = item
                break

        if network_config is None:
            raise RuntimeError(f'FrontendService._build_ws_url_for_network: network not found: {network}')

        ws_url_template = str(network_config.ws_url_template)
        if len(ws_url_template) == 0:
            raise RuntimeError(f'FrontendService._build_ws_url_for_network: ws_url_template is empty for network={network}')
        if '{RPC_KEY}' not in ws_url_template:
            raise RuntimeError(f'FrontendService._build_ws_url_for_network: ws_url_template does not contain {{RPC_KEY}} for network={network}')

        rpc_key = str(self._runtime.rpc_key)
        if len(rpc_key) == 0:
            raise RuntimeError('FrontendService._build_ws_url_for_network: rpc_key is empty')

        return str(ws_url_template).replace('{RPC_KEY}', str(rpc_key))
