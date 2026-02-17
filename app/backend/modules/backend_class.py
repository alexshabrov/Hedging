"""
Backend service module
Date: 2026-02-13
Version: 1.1
"""
import sys, time, threading, uuid, traceback
from typing import Dict, List, Optional, Tuple
import orjson

from flask import Flask, Response, request
from pymongo import MongoClient  # type: ignore[import-not-found]

from live.lib.logger import get_logger
from backend.models.hedger_models import HedgerConfig, HedgerStats
from backend.models.backend_models import (
    BackendRunLifecycle,
    BackendStartRunRequest,
    BackendRunAggregates,
    BackendIterationRecord,
    BackendPositionView,
)
from backend.modules.hedger_class import Hedger
from backend.modules.hedger_helper import calc_hedger_pnl_stats
from backend.modules.backend_env import read_runtime_secrets
from dex.contract.contract_wrapper import ContractWrapper


### Collections ###
ACTIVE_POSITIONS_COLLECTION = 'backend_positions_active'
ARCHIVE_POSITIONS_COLLECTION = 'backend_positions_archive'
HEDGER_RUNS_COLLECTION = 'backend_hedger_runs'
RUN_TOKENS_COLLECTION = 'backend_run_tokens'


### Run context ###
class BackendRunContext:
    def __init__(
        self,
        run_id: str,
        config: HedgerConfig,
        binance_key: str,
        binance_secret: str,
        private_key: str,
        wallet_address: Optional[str],
        template_id: Optional[str] = None,
    ) -> None:
        self.run_id = str(run_id)
        self.config = config
        self.template_id = None if template_id is None else str(template_id)
        self.binance_key = str(binance_key)
        self.binance_secret = str(binance_secret)
        self.private_key = str(private_key)
        self.wallet_address = str(wallet_address) if wallet_address is not None else None

        self.lifecycle = BackendRunLifecycle.INITIALIZED
        self.created_at_ms = int(time.time() * 1000)
        self.started_at_ms = 0
        self.updated_at_ms = int(self.created_at_ms)
        self.finished_at_ms = 0
        self.stop_requested = False
        self.last_error = None

        self.current_hedger = None
        self.worker_thread = None

        self.aggregates = BackendRunAggregates.empty()
        self.iterations = []

        self.lock = threading.Lock()


### Backend ###
class Backend:
    def __init__(self, app: Flask):
        if app is None:
            raise RuntimeError('Backend: app is None')
        if not isinstance(app, Flask):
            raise RuntimeError(f'Backend: app is not Flask: {type(app)}')

        self._app = app
        self._logger = get_logger('backend')
        self._lock = threading.Lock()
        self._runs = {}

        self._binance_key, self._binance_secret, self._private_key, self._wallet_address = self._read_secrets()
        self._register_routes()

        self._logger.info('backend_ready')

    def _read_secrets(self) -> Tuple[str, str, str, Optional[str]]:
        binance_key, binance_secret, private_key, wallet_address = read_runtime_secrets()
        return (
            str(binance_key),
            str(binance_secret),
            str(private_key),
            None if wallet_address is None else str(wallet_address),
        )

    def _register_routes(self) -> None:
        @self._app.route('/api/health', methods=['GET'])
        def api_health() -> Response:
            return self._json_response({'ok': True}, 200)

        @self._app.route('/api/runs/start', methods=['POST'])
        def api_runs_start() -> Response:
            try:
                payload = self._read_json_body()
                req = BackendStartRunRequest.from_dict(payload)
                run_id = self.start_run(req.config, req.template_id)
                return self._json_response({'ok': True, 'run_id': run_id}, 200)
            except Exception as exc:
                self._logger.error(f'api_runs_start_failed error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

        @self._app.route('/api/runs/<run_id>/stop', methods=['POST'])
        def api_run_stop(run_id: str) -> Response:
            try:
                self.stop_run(str(run_id))
                return self._json_response({'ok': True, 'run_id': str(run_id)}, 200)
            except Exception as exc:
                self._logger.error(f'api_run_stop_failed run_id={run_id} error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

        @self._app.route('/api/runs/<run_id>/collect', methods=['POST'])
        def api_run_collect(run_id: str) -> Response:
            try:
                out = self.collect_run_fees(str(run_id))
                return self._json_response({'ok': True, 'run_id': str(run_id), 'item': out}, 200)
            except Exception as exc:
                self._logger.error(f'api_run_collect_failed run_id={run_id} error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

        @self._app.route('/api/positions', methods=['GET'])
        def api_positions() -> Response:
            try:
                rows = self.list_positions()
                out = []
                for row in rows:
                    out.append(row.model_dump())
                return self._json_response({'ok': True, 'items': out}, 200)
            except Exception as exc:
                self._logger.error(f'api_positions_failed error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

        @self._app.route('/api/positions/active', methods=['GET'])
        def api_positions_active() -> Response:
            try:
                docs = self.list_positions_active_docs()
                return self._json_response({'ok': True, 'items': docs}, 200)
            except Exception as exc:
                self._logger.error(f'api_positions_active_failed error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

        @self._app.route('/api/positions/archive', methods=['GET'])
        def api_positions_archive() -> Response:
            try:
                docs = self.list_positions_archive_docs()
                return self._json_response({'ok': True, 'items': docs}, 200)
            except Exception as exc:
                self._logger.error(f'api_positions_archive_failed error={exc} traceback=\n{traceback.format_exc()}')
                return self._json_response({'ok': False, 'error': str(exc)}, 400)

    def _json_response(self, payload: dict, status_code: int) -> Response:
        body = orjson.dumps(payload)
        return Response(response=body, status=int(status_code), mimetype='application/json')

    def _read_json_body(self) -> dict:
        raw = request.get_data(cache=False, as_text=False)
        if raw is None:
            raise RuntimeError('Backend._read_json_body: body is None')
        if len(raw) == 0:
            raise RuntimeError('Backend._read_json_body: empty body')

        payload = orjson.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError(f'Backend._read_json_body: payload is not dict: {type(payload)}')

        return payload

    def start_run(self, config: HedgerConfig, template_id: Optional[str] = None) -> str:
        if config is None:
            raise RuntimeError('Backend.start_run: config is None')
        if not isinstance(config, HedgerConfig):
            raise RuntimeError(f'Backend.start_run: config is not HedgerConfig: {type(config)}')

        run_id = str(uuid.uuid4().hex)
        ctx = BackendRunContext(
            run_id=run_id,
            config=config,
            binance_key=str(self._binance_key),
            binance_secret=str(self._binance_secret),
            private_key=str(self._private_key),
            wallet_address=str(self._wallet_address) if self._wallet_address is not None else None,
            template_id=None if template_id is None else str(template_id),
        )

        with self._lock:
            if run_id in self._runs:
                raise RuntimeError(f'Backend.start_run: run_id collision: {run_id}')
            self._runs[run_id] = ctx

        worker = threading.Thread(target=self._run_loop, args=(ctx,), name=f'backend_run_{run_id}', daemon=True)
        ctx.worker_thread = worker
        worker.start()

        self._logger.info(f'backend_run_started run_id={run_id} symbol={config.symbol}')
        return run_id

    def stop_run(self, run_id: str) -> None:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('Backend.stop_run: run_id is empty')

        with self._lock:
            if run_id not in self._runs:
                raise RuntimeError(f'Backend.stop_run: run not found: {run_id}')
            ctx = self._runs[run_id]

        hedger_to_stop = None

        with ctx.lock:
            if ctx.lifecycle == BackendRunLifecycle.FINISHED:
                raise RuntimeError(f'Backend.stop_run: run already finished: {run_id}')
            if ctx.lifecycle == BackendRunLifecycle.FAILED:
                raise RuntimeError(f'Backend.stop_run: run already failed: {run_id}')

            ctx.stop_requested = True
            if ctx.lifecycle == BackendRunLifecycle.RUNNING:
                ctx.lifecycle = BackendRunLifecycle.STOPPING

            hedger_to_stop = ctx.current_hedger
            ctx.updated_at_ms = int(time.time() * 1000)

        if hedger_to_stop is not None:
            try:
                hedger_to_stop.request_stop()
            except Exception:
                _t, exc, _tb = sys.exc_info()
                with ctx.lock:
                    ctx.last_error = f'stop_run hedger.request_stop failed: {exc}'
                    ctx.updated_at_ms = int(time.time() * 1000)
                self._write_position_active_doc(ctx)
                raise RuntimeError(f'Backend.stop_run: hedger.request_stop failed: {exc}')

        self._write_position_active_doc(ctx)
        self._logger.info(f'backend_run_stop_requested run_id={run_id}')

    def list_positions(self) -> List[BackendPositionView]:
        contexts = []

        with self._lock:
            for _run_id, ctx in self._runs.items():
                contexts.append(ctx)

        rows = []
        for ctx in contexts:
            rows.append(self._build_position_view(ctx))

        rows.sort(key=lambda x: int(x.first_started_at_ms), reverse=True)
        return rows

    def list_positions_active_docs(self) -> List[dict]:
        docs = []
        with self._lock:
            for _run_id, ctx in self._runs.items():
                docs.append(self._build_position_active_doc(ctx))

        docs.sort(key=lambda x: int(x['started_at_ms']), reverse=True)
        return docs

    def list_positions_archive_docs(self) -> List[dict]:
        out = []

        with self._lock:
            contexts = []
            for _run_id, ctx in self._runs.items():
                contexts.append(ctx)

        for ctx in contexts:
            with ctx.lock:
                if ctx.lifecycle == BackendRunLifecycle.FINISHED or ctx.lifecycle == BackendRunLifecycle.FAILED:
                    out.append(self._build_position_archive_doc(ctx))

        out.sort(key=lambda x: int(x['finished_at_ms']), reverse=True)
        return out

    def _build_position_view(self, ctx: BackendRunContext) -> BackendPositionView:
        with ctx.lock:
            now_ms = int(time.time() * 1000)
            first_started_at_ms = int(ctx.started_at_ms) if int(ctx.started_at_ms) > 0 else int(ctx.created_at_ms)

            runtime_to_ms = int(now_ms)
            if ctx.lifecycle == BackendRunLifecycle.FINISHED or ctx.lifecycle == BackendRunLifecycle.FAILED:
                runtime_to_ms = int(ctx.finished_at_ms) if int(ctx.finished_at_ms) > 0 else int(now_ms)

            runtime_sec = float(max(0, int(runtime_to_ms) - int(first_started_at_ms))) / 1000.0
            runtime_dhm = self._format_dhm(runtime_sec)

            iterations_finished = int(ctx.aggregates.iterations_finished)
            avg_iteration_lifetime_sec = 0.0
            if int(iterations_finished) > 0:
                avg_iteration_lifetime_sec = float(ctx.aggregates.sum_pool_hold_seconds) / float(iterations_finished)

            apr_with_hedge_pct = 0.0
            apr_without_hedge_pct = 0.0
            if float(ctx.aggregates.sum_pool_hold_seconds) > 0.0 and float(ctx.config.total_quote) > 0.0:
                seconds_per_year = 365.0 * 24.0 * 60.0 * 60.0
                apr_with_hedge_pct = (
                    (float(ctx.aggregates.sum_total_pnl_with_hedge_quote) / float(ctx.config.total_quote))
                    * (float(seconds_per_year) / float(ctx.aggregates.sum_pool_hold_seconds))
                    * 100.0
                )
                apr_without_hedge_pct = (
                    (float(ctx.aggregates.sum_total_pnl_without_hedge_quote) / float(ctx.config.total_quote))
                    * (float(seconds_per_year) / float(ctx.aggregates.sum_pool_hold_seconds))
                    * 100.0
                )

            market_price = None
            if ctx.current_hedger is not None:
                if ctx.current_hedger._cw is not None:
                    market_price = float(ctx.current_hedger._cw.get_current_traditional_price())

            token_id = None
            if ctx.current_hedger is not None and ctx.current_hedger._run_token_id is not None:
                if int(ctx.current_hedger._run_token_id) > 0:
                    token_id = int(ctx.current_hedger._run_token_id)
            if token_id is None:
                for item in reversed(ctx.iterations):
                    if item.stats is None or item.stats.uniswap is None:
                        continue
                    if item.stats.uniswap.token_id is None:
                        continue
                    if int(item.stats.uniswap.token_id) <= 0:
                        continue
                    token_id = int(item.stats.uniswap.token_id)
                    break
            if token_id is None:
                token_id = self._read_saved_run_token_id(
                    mongo_uri=str(ctx.config.mongo_uri),
                    mongo_db=str(ctx.config.mongo_db),
                    run_id=str(ctx.run_id),
                )

            return BackendPositionView(
                run_id=str(ctx.run_id),
                symbol=str(ctx.config.symbol),
                first_started_at_ms=int(first_started_at_ms),
                runtime_sec=float(runtime_sec),
                runtime_dhm=str(runtime_dhm),
                avg_iteration_lifetime_sec=float(avg_iteration_lifetime_sec),
                iterations_finished=int(iterations_finished),
                status=ctx.lifecycle,
                market_price=None if market_price is None else float(market_price),
                price_lower=None if ctx.config.price_lower is None else float(ctx.config.price_lower),
                price_upper=None if ctx.config.price_upper is None else float(ctx.config.price_upper),
                price_lower_pct=None if ctx.config.price_lower_pct is None else float(ctx.config.price_lower_pct),
                price_upper_pct=None if ctx.config.price_upper_pct is None else float(ctx.config.price_upper_pct),
                total_quote=float(ctx.config.total_quote),
                pnl_with_hedge_quote=float(ctx.aggregates.sum_total_pnl_with_hedge_quote),
                pnl_without_hedge_quote=float(ctx.aggregates.sum_total_pnl_without_hedge_quote),
                apr_with_hedge_pct=float(apr_with_hedge_pct),
                apr_without_hedge_pct=float(apr_without_hedge_pct),
                fees_quote=float(ctx.aggregates.sum_fees_quote),
                price_pnl_quote=float(ctx.aggregates.sum_price_pnl_quote),
                hedge_pnl_quote=float(ctx.aggregates.sum_cex_pnl_quote),
                costs_quote=float(ctx.aggregates.sum_costs_quote),
                token_id=None if token_id is None else int(token_id),
                last_error=None if ctx.last_error is None else str(ctx.last_error),
            )

    def _extract_token_id_from_hedger(self, hedger: Hedger) -> Optional[int]:
        if hedger is None:
            return None
        if hedger._run_token_id is not None and int(hedger._run_token_id) > 0:
            return int(hedger._run_token_id)
        if hedger.last_stats is not None and hedger.last_stats.uniswap is not None:
            if hedger.last_stats.uniswap.token_id is not None and int(hedger.last_stats.uniswap.token_id) > 0:
                return int(hedger.last_stats.uniswap.token_id)
        return None

    def _try_persist_run_token(self, ctx: BackendRunContext, hedger: Hedger) -> None:
        try:
            token_id = self._extract_token_id_from_hedger(hedger)
            if token_id is None:
                return
            self._save_run_token(
                mongo_uri=str(ctx.config.mongo_uri),
                mongo_db=str(ctx.config.mongo_db),
                run_id=str(ctx.run_id),
                token_id=int(token_id),
                network=str(ctx.config.network),
                pool_address=str(ctx.config.pool_address),
                symbol=str(ctx.config.symbol),
                template_id=None if ctx.template_id is None else str(ctx.template_id),
            )
            # Keep active position doc in sync with backend_run_tokens right away.
            self._write_position_active_doc(ctx)
        except Exception as exc:
            self._logger.warning(f'backend_save_run_token_skipped run_id={ctx.run_id} error={exc}')

    def _save_run_token(
        self,
        mongo_uri: str,
        mongo_db: str,
        run_id: str,
        token_id: int,
        network: str,
        pool_address: str,
        symbol: str,
        template_id: Optional[str],
    ) -> None:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('Backend._save_run_token: run_id is empty')
        if int(token_id) <= 0:
            raise RuntimeError('Backend._save_run_token: token_id must be > 0')

        now_ms = int(time.time() * 1000)
        client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(mongo_db)]
            col = db[str(RUN_TOKENS_COLLECTION)]
            existing = col.find_one({'run_id': str(run_id)})
            created_at_ms = int(now_ms)
            if existing is not None and 'created_at_ms' in existing:
                created_at_ms = int(existing['created_at_ms'])
            doc = {
                'run_id': str(run_id),
                'token_id': int(token_id),
                'network': str(network),
                'pool_address': str(pool_address),
                'symbol': str(symbol),
                'template_id': None if template_id is None else str(template_id),
                'created_at_ms': int(created_at_ms),
                'updated_at_ms': int(now_ms),
            }
            res = col.replace_one({'run_id': str(run_id)}, doc, upsert=True)
            if res is None:
                raise RuntimeError('Backend._save_run_token: replace_one returned None')
        finally:
            client.close()

    def _read_saved_run_token_id(self, mongo_uri: str, mongo_db: str, run_id: str) -> Optional[int]:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('Backend._read_saved_run_token_id: run_id is empty')
        client = MongoClient(str(mongo_uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(mongo_db)]
            col = db[str(RUN_TOKENS_COLLECTION)]
            doc = col.find_one({'run_id': str(run_id)})
            if doc is None:
                return None
            if 'token_id' not in doc or doc['token_id'] is None:
                return None
            token_id = int(doc['token_id'])
            if int(token_id) <= 0:
                return None
            return int(token_id)
        finally:
            client.close()

    def collect_run_fees(self, run_id: str) -> dict:
        if not isinstance(run_id, str) or len(run_id) == 0:
            raise RuntimeError('Backend.collect_run_fees: run_id is empty')

        with self._lock:
            if run_id not in self._runs:
                raise RuntimeError(f'Backend.collect_run_fees: run not found: {run_id}')
            ctx = self._runs[run_id]

        with ctx.lock:
            lifecycle = ctx.lifecycle
            config = ctx.config
            template_id = ctx.template_id

        if lifecycle != BackendRunLifecycle.FAILED:
            raise RuntimeError(f'Backend.collect_run_fees: run is not failed: {lifecycle.value}')

        token_id = self._read_saved_run_token_id(
            mongo_uri=str(config.mongo_uri),
            mongo_db=str(config.mongo_db),
            run_id=str(run_id),
        )
        if token_id is None:
            raise RuntimeError(f'Backend.collect_run_fees: token_id not found for run_id={run_id}')

        cw = ContractWrapper(
            rpc_url=str(config.rpc_url),
            pool_address=str(config.pool_address),
            network=str(config.network),
            private_key=str(self._private_key),
            wallet_address=str(self._wallet_address) if self._wallet_address is not None else None,
        )
        collect_res = cw.collect_fees(int(token_id))

        self._save_run_token(
            mongo_uri=str(config.mongo_uri),
            mongo_db=str(config.mongo_db),
            run_id=str(run_id),
            token_id=int(token_id),
            network=str(config.network),
            pool_address=str(config.pool_address),
            symbol=str(config.symbol),
            template_id=None if template_id is None else str(template_id),
        )

        self._logger.info(f'backend_collect_done run_id={run_id} token_id={token_id} ok={collect_res.ok} tx={collect_res.tx_hash}')
        return {
            'token_id': int(token_id),
            'ok': bool(collect_res.ok),
            'tx_hash': str(collect_res.tx_hash),
            'amount_base': float(collect_res.amount_base),
            'amount_quote': float(collect_res.amount_quote),
        }

    def _format_dhm(self, runtime_sec: float) -> str:
        if float(runtime_sec) < 0.0:
            raise RuntimeError(f'Backend._format_dhm: runtime_sec is negative: {runtime_sec}')

        total_sec = int(runtime_sec)
        day_sec = 24 * 60 * 60
        hour_sec = 60 * 60
        minute_sec = 60

        days = total_sec // day_sec
        total_sec -= days * day_sec

        hours = total_sec // hour_sec
        total_sec -= hours * hour_sec

        minutes = total_sec // minute_sec
        return f'{int(days)}:{int(hours):02d}:{int(minutes):02d}'

    def _run_loop(self, ctx: BackendRunContext) -> None:
        with ctx.lock:
            ctx.lifecycle = BackendRunLifecycle.RUNNING
            ctx.started_at_ms = int(time.time() * 1000)
            ctx.updated_at_ms = int(ctx.started_at_ms)

        self._write_position_active_doc(ctx)
        self._logger.info(f'backend_run_loop_enter run_id={ctx.run_id}')

        try:
            while True:
                with ctx.lock:
                    if bool(ctx.stop_requested):
                        break

                self._run_single_iteration(ctx)

        except BaseException:
            _t, exc, _tb = sys.exc_info()

            with ctx.lock:
                ctx.lifecycle = BackendRunLifecycle.FAILED
                ctx.last_error = str(exc)
                ctx.updated_at_ms = int(time.time() * 1000)
                ctx.finished_at_ms = int(ctx.updated_at_ms)

            self._logger.error(f'backend_run_failed run_id={ctx.run_id} error={exc}')

        else:
            with ctx.lock:
                ctx.lifecycle = BackendRunLifecycle.FINISHED
                ctx.updated_at_ms = int(time.time() * 1000)
                ctx.finished_at_ms = int(ctx.updated_at_ms)

            self._logger.info(f'backend_run_finished run_id={ctx.run_id}')

        finally:
            self._archive_position_doc(ctx)

    def _run_single_iteration(self, ctx: BackendRunContext) -> None:
        started_at_ms = int(time.time() * 1000)

        with ctx.lock:
            iteration_no = int(len(ctx.iterations) + 1)

        hedger = Hedger(
            config=ctx.config,
            binance_key=str(ctx.binance_key),
            binance_secret=str(ctx.binance_secret),
            private_key=str(ctx.private_key),
            wallet_address=str(ctx.wallet_address) if ctx.wallet_address is not None else None,
        )

        with ctx.lock:
            ctx.current_hedger = hedger
            ctx.updated_at_ms = int(time.time() * 1000)

        run_exc = None
        stats = None
        pnl = None

        try:
            stats = hedger.run()
        except BaseException:
            run_exc = sys.exc_info()
        finally:
            self._try_persist_run_token(ctx, hedger)

        report_exc = None
        if run_exc is None:
            try:
                if stats is None:
                    raise RuntimeError('Backend._run_single_iteration: stats is None')
                if not isinstance(stats, HedgerStats):
                    raise RuntimeError(f'Backend._run_single_iteration: stats is not HedgerStats: {type(stats)}')
                pnl = calc_hedger_pnl_stats(stats)
            except BaseException:
                report_exc = sys.exc_info()

        stop_exc = None
        try:
            hedger.stop()
        except BaseException:
            stop_exc = sys.exc_info()
        finally:
            self._try_persist_run_token(ctx, hedger)

        with ctx.lock:
            ctx.current_hedger = None
            ctx.updated_at_ms = int(time.time() * 1000)

        if run_exc is not None and stop_exc is not None:
            _t_run, exc_run, _tb_run = run_exc
            _t_stop, exc_stop, _tb_stop = stop_exc
            raise RuntimeError(f'Backend._run_single_iteration: run failed ({exc_run}) and stop failed ({exc_stop})') from exc_run

        if report_exc is not None and stop_exc is not None:
            _t_report, exc_report, _tb_report = report_exc
            _t_stop, exc_stop, _tb_stop = stop_exc
            raise RuntimeError(f'Backend._run_single_iteration: report failed ({exc_report}) and stop failed ({exc_stop})') from exc_report

        if stop_exc is not None:
            _t_stop, exc_stop, tb_stop = stop_exc
            raise exc_stop.with_traceback(tb_stop)

        if run_exc is not None:
            _t_run, exc_run, tb_run = run_exc
            raise exc_run.with_traceback(tb_run)

        if report_exc is not None:
            _t_report, exc_report, tb_report = report_exc
            raise exc_report.with_traceback(tb_report)

        if stats is None:
            raise RuntimeError('Backend._run_single_iteration: stats is None after run/report')
        if pnl is None:
            raise RuntimeError('Backend._run_single_iteration: pnl is None after report')

        finished_at_ms = int(time.time() * 1000)
        swap_cost_quote = self._calc_swap_cost_quote(stats)
        costs_quote = float(pnl.gas_paid_quote) + float(swap_cost_quote)
        pnl_without_hedge_quote = float(pnl.dex_realized_il_quote) + float(pnl.fees_received_quote) - float(costs_quote)
        pnl_with_hedge_quote = float(pnl_without_hedge_quote) + float(pnl.cex_pnl_quote)

        row = BackendIterationRecord(
            id=str(uuid.uuid4().hex),
            run_id=str(ctx.run_id),
            iteration_no=int(iteration_no),
            started_at_ms=int(started_at_ms),
            finished_at_ms=int(finished_at_ms),
            status=str(stats.status.value),
            error=None if stats.error is None else str(stats.error),
            stats=stats,
            pnl=pnl,
            swap_cost_quote=float(swap_cost_quote),
            costs_quote=float(costs_quote),
            pnl_without_hedge_quote=float(pnl_without_hedge_quote),
            pnl_with_hedge_quote=float(pnl_with_hedge_quote),
        )

        with ctx.lock:
            ctx.iterations.append(row)
            self._accumulate_iteration(ctx, row)
            ctx.updated_at_ms = int(time.time() * 1000)

        self._write_hedger_run_doc(ctx, row)
        self._write_position_active_doc(ctx)
        self._logger.info(f'backend_iteration_done run_id={ctx.run_id} iteration_no={iteration_no} hedger_run_id={row.id}')

    def _accumulate_iteration(self, ctx: BackendRunContext, row: BackendIterationRecord) -> None:
        if row is None:
            raise RuntimeError('Backend._accumulate_iteration: row is None')

        if row.status == 'finished':
            ctx.aggregates.iterations_finished = int(ctx.aggregates.iterations_finished) + 1
        else:
            ctx.aggregates.iterations_failed = int(ctx.aggregates.iterations_failed) + 1

        ctx.aggregates.sum_pool_hold_seconds = float(ctx.aggregates.sum_pool_hold_seconds) + float(row.pnl.pool_hold_seconds)
        ctx.aggregates.sum_cex_pnl_quote = float(ctx.aggregates.sum_cex_pnl_quote) + float(row.pnl.cex_pnl_quote)
        ctx.aggregates.sum_price_pnl_quote = float(ctx.aggregates.sum_price_pnl_quote) + float(row.pnl.dex_realized_il_quote)
        ctx.aggregates.sum_fees_quote = float(ctx.aggregates.sum_fees_quote) + float(row.pnl.fees_received_quote)
        ctx.aggregates.sum_costs_quote = float(ctx.aggregates.sum_costs_quote) + float(row.costs_quote)
        ctx.aggregates.sum_total_pnl_without_hedge_quote = float(ctx.aggregates.sum_total_pnl_without_hedge_quote) + float(row.pnl_without_hedge_quote)
        ctx.aggregates.sum_total_pnl_with_hedge_quote = float(ctx.aggregates.sum_total_pnl_with_hedge_quote) + float(row.pnl_with_hedge_quote)

    def _calc_swap_cost_quote(self, stats: HedgerStats) -> float:
        if stats is None:
            raise RuntimeError('Backend._calc_swap_cost_quote: stats is None')

        rebalance = stats.uniswap.rebalance
        if rebalance is None:
            return 0.0
        if rebalance.order is None:
            return 0.0

        order = rebalance.order
        if float(order.fee_amount) < 0:
            raise RuntimeError(f'Backend._calc_swap_cost_quote: order.fee_amount is negative: {order.fee_amount}')

        return float(order.fee_amount)

    def _build_position_active_doc(self, ctx: BackendRunContext) -> dict:
        position = self._build_position_view(ctx)

        with ctx.lock:
            return {
                'run_id': str(ctx.run_id),
                'status': str(ctx.lifecycle.value),
                'created_at_ms': int(ctx.created_at_ms),
                'started_at_ms': int(ctx.started_at_ms),
                'updated_at_ms': int(ctx.updated_at_ms),
                'stop_requested': bool(ctx.stop_requested),
                'last_error': None if ctx.last_error is None else str(ctx.last_error),
                'template_id': None if ctx.template_id is None else str(ctx.template_id),
                'config': ctx.config.model_dump(),
                'aggregates': ctx.aggregates.model_dump(),
                'position': position.model_dump(),
                'iterations_count': int(len(ctx.iterations)),
            }

    def _build_position_archive_doc(self, ctx: BackendRunContext) -> dict:
        position = self._build_position_view(ctx)

        with ctx.lock:
            return {
                'run_id': str(ctx.run_id),
                'status': str(ctx.lifecycle.value),
                'created_at_ms': int(ctx.created_at_ms),
                'started_at_ms': int(ctx.started_at_ms),
                'finished_at_ms': int(ctx.finished_at_ms),
                'archived_at_ms': int(time.time() * 1000),
                'stop_requested': bool(ctx.stop_requested),
                'last_error': None if ctx.last_error is None else str(ctx.last_error),
                'template_id': None if ctx.template_id is None else str(ctx.template_id),
                'config': ctx.config.model_dump(),
                'aggregates': ctx.aggregates.model_dump(),
                'position': position.model_dump(),
                'iterations_count': int(len(ctx.iterations)),
            }

    def _write_position_active_doc(self, ctx: BackendRunContext) -> None:
        doc = self._build_position_active_doc(ctx)

        uri = str(ctx.config.mongo_uri)
        db_name = str(ctx.config.mongo_db)

        client = MongoClient(str(uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(db_name)]
            col = db[str(ACTIVE_POSITIONS_COLLECTION)]
            res = col.replace_one({'run_id': str(ctx.run_id)}, doc, upsert=True)
            if res is None:
                raise RuntimeError('Backend._write_position_active_doc: replace_one returned None')
        finally:
            client.close()

    def _archive_position_doc(self, ctx: BackendRunContext) -> None:
        archive_doc = self._build_position_archive_doc(ctx)

        uri = str(ctx.config.mongo_uri)
        db_name = str(ctx.config.mongo_db)

        client = MongoClient(str(uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(db_name)]
            col_active = db[str(ACTIVE_POSITIONS_COLLECTION)]
            col_archive = db[str(ARCHIVE_POSITIONS_COLLECTION)]

            res_archive = col_archive.replace_one({'run_id': str(ctx.run_id)}, archive_doc, upsert=True)
            if res_archive is None:
                raise RuntimeError('Backend._archive_position_doc: archive replace_one returned None')

            res_delete = col_active.delete_one({'run_id': str(ctx.run_id)})
            if res_delete is None:
                raise RuntimeError('Backend._archive_position_doc: delete_one returned None')
        finally:
            client.close()

    def _write_hedger_run_doc(self, ctx: BackendRunContext, row: BackendIterationRecord) -> None:
        if row is None:
            raise RuntimeError('Backend._write_hedger_run_doc: row is None')

        uri = str(ctx.config.mongo_uri)
        db_name = str(ctx.config.mongo_db)

        client = MongoClient(str(uri), serverSelectionTimeoutMS=5000)
        try:
            _ = client.server_info()
            db = client[str(db_name)]
            col = db[str(HEDGER_RUNS_COLLECTION)]

            doc = row.model_dump()
            doc['created_at_ms'] = int(time.time() * 1000)
            doc['run_lifecycle'] = str(ctx.lifecycle.value)

            res = col.insert_one(doc)
            if res is None or res.inserted_id is None:
                raise RuntimeError('Backend._write_hedger_run_doc: insert failed')
        finally:
            client.close()
