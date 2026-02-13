"""
Dashboard frontend module
Date: 2026-02-13
Version: 1.0
"""
import orjson
from flask import Flask, Response, render_template

from modules.frontend.auth import login_required
from modules.frontend.services.frontend_service import FrontendService


class DashboardModule:
    def __init__(self, app: Flask, service: FrontendService):
        if app is None:
            raise RuntimeError('DashboardModule: app is None')
        if service is None:
            raise RuntimeError('DashboardModule: service is None')

        self._app = app
        self._service = service
        self._register_routes(app)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/dashboard', methods=['GET'])
        @login_required
        def dashboard_page():
            view = self._service.build_dashboard()
            return render_template('modules/dashboard.html', title='Dashboard', dashboard=view.model_dump())

        @app.route('/api/frontend/dashboard', methods=['GET'])
        def dashboard_api():
            view = self._service.build_dashboard()
            return self._json_response({'ok': True, 'item': view.model_dump()}, 200)

    def _json_response(self, payload: dict, status_code: int) -> Response:
        body = orjson.dumps(payload)
        return Response(response=body, status=int(status_code), mimetype='application/json')
