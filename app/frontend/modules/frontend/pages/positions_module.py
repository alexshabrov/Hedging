"""
Positions frontend module
Date: 2026-02-13
Version: 2.0
"""
from flask import Flask, render_template

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
