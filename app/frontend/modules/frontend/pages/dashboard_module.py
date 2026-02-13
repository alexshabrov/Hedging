"""
Dashboard frontend module
Date: 2026-02-13
Version: 2.0
"""
from flask import Flask, render_template

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
