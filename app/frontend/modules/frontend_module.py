"""
Frontend module
Date: 2026-02-13
Version: 2.0
"""

from flask import Flask, redirect, request, url_for

from frontend.modules.auth import login_required
from frontend.modules.frontend_env import (
    get_front_backend_url,
    get_mongo_collection,
    get_mongo_db,
    get_mongo_uri,
    get_rpc_key,
    get_tick_ms,
    get_gtx_cooldown_ms,
    get_entrance_timeout_ms,
    get_cowswap_api_timeout_sec,
    get_cowswap_wait_timeout_sec,
    get_cowswap_poll_interval_sec,
)
from frontend.modules.pages.dashboard_module import DashboardModule
from frontend.modules.pages.positions_module import PositionsModule
from frontend.modules.pages.runs_module import RunsModule
from frontend.modules.models.frontend_models import FrontendRuntimeConfig
from frontend.modules.services.backend_api_service import BackendApiService
from frontend.modules.services.frontend_service import FrontendService
from frontend.modules.services.storage_service import StorageService


class Frontend:
    def __init__(self, app: Flask):
        if app is None:
            raise RuntimeError('Frontend: app is None')
        if not isinstance(app, Flask):
            raise RuntimeError(f'Frontend: app is not Flask: {type(app)}')

        self._app = app
        self._storage = StorageService(
            mongo_uri=get_mongo_uri(),
            mongo_db=get_mongo_db(),
        )
        self._backend_api = BackendApiService(
            backend_base_url=get_front_backend_url(),
            timeout_sec=10,
        )
        runtime_config = FrontendRuntimeConfig(
            rpc_key=get_rpc_key(),
            mongo_uri=get_mongo_uri(),
            mongo_db=get_mongo_db(),
            mongo_collection=get_mongo_collection(),
            tick_ms=get_tick_ms(),
            gtx_cooldown_ms=get_gtx_cooldown_ms(),
            entrance_timeout_ms=get_entrance_timeout_ms(),
            cowswap_api_timeout_sec=get_cowswap_api_timeout_sec(),
            cowswap_wait_timeout_sec=get_cowswap_wait_timeout_sec(),
            cowswap_poll_interval_sec=get_cowswap_poll_interval_sec(),
        )
        self._service = FrontendService(
            storage=self._storage,
            backend_api=self._backend_api,
            runtime_config=runtime_config,
        )

        self._register_routes(app)
        self._register_context_processors(app)

        self.dashboard_module = DashboardModule(app, self._service)
        self.positions_module = PositionsModule(app, self._service)
        self.runs_module = RunsModule(app, self._service)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/', methods=['GET'])
        @login_required
        def index_page():
            return redirect(url_for('dashboard_page'))

    def _register_context_processors(self, app: Flask) -> None:
        @app.context_processor
        def inject_helpers():
            def utc_ts(ts_ms: int) -> str:
                if not isinstance(ts_ms, int):
                    raise RuntimeError(f'inject_helpers.utc_ts: ts_ms is not int: {type(ts_ms)}')
                if int(ts_ms) <= 0:
                    return '-'

                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
                return dt.strftime('%Y-%m-%d %H:%M:%S')

            def qarg(name: str) -> str:
                if not isinstance(name, str) or len(name) == 0:
                    raise RuntimeError('inject_helpers.qarg: name is empty')

                if name in request.args:
                    return str(request.args[name])
                return ''

            return {
                'utc_ts': utc_ts,
                'qarg': qarg,
            }
