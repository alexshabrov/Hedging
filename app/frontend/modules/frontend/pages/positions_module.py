"""
Positions frontend module
Date: 2026-02-13
Version: 1.0
"""
import orjson
from flask import Flask, Response, render_template

from modules.frontend.auth import login_required
from modules.frontend.services.frontend_service import FrontendService


class PositionsModule:
    def __init__(self, app: Flask, service: FrontendService):
        if app is None:
            raise RuntimeError('PositionsModule: app is None')
        if service is None:
            raise RuntimeError('PositionsModule: service is None')

        self._app = app
        self._service = service
        self._register_routes(app)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/positions', methods=['GET'])
        @login_required
        def positions_page():
            rows_raw = self._service.list_positions()
            rows = []
            for row in rows_raw:
                rows.append(row.model_dump())

            return render_template('modules/positions.html', title='Positions', rows=rows)

        @app.route('/api/frontend/positions', methods=['GET'])
        def positions_api():
            rows_raw = self._service.list_positions()
            rows = []
            for row in rows_raw:
                rows.append(row.model_dump())

            return self._json_response({'ok': True, 'items': rows}, 200)

    def _json_response(self, payload: dict, status_code: int) -> Response:
        body = orjson.dumps(payload)
        return Response(response=body, status=int(status_code), mimetype='application/json')
