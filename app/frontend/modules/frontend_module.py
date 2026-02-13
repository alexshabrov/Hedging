"""
Frontend module
Date: 2026-02-13
Version: 1.0
"""
import os

from flask import Flask, redirect, request, url_for

from frontend.modules.frontend.auth import login_required
from frontend.modules.frontend.pages.dashboard_module import DashboardModule
from frontend.modules.frontend.pages.positions_module import PositionsModule
from frontend.modules.frontend.pages.runs_module import RunsModule
from frontend.modules.frontend.services.backend_api_service import BackendApiService
from frontend.modules.frontend.services.frontend_service import FrontendService
from frontend.modules.frontend.services.storage_service import StorageService


class Frontend:
    def __init__(self, app: Flask):
        if app is None:
            raise RuntimeError('Frontend: app is None')
        if not isinstance(app, Flask):
            raise RuntimeError(f'Frontend: app is not Flask: {type(app)}')

        self._app = app
        self._storage = StorageService(
            mongo_uri=str(os.environ['FRONT_MONGO_URI']),
            mongo_db=str(os.environ['FRONT_MONGO_DB']),
        )
        self._backend_api = BackendApiService(
            backend_base_url=str(os.environ['FRONT_BACKEND_URL']),
            timeout_sec=10,
        )
        self._service = FrontendService(
            storage=self._storage,
            backend_api=self._backend_api,
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
