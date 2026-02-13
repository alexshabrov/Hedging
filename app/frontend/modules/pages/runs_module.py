"""
Runs frontend module
Date: 2026-02-13
Version: 2.0
"""
import sys
from typing import Optional

from flask import Flask, flash, redirect, render_template, request, url_for

from backend.models.hedger_models import CexTriggerMode
from frontend.modules.auth import login_required
from frontend.modules.models.frontend_models import FrontendStartRunForm
from frontend.modules.services.frontend_service import FrontendService


class RunsModule:
    def __init__(self, app: Flask, service: FrontendService):
        if app is None:
            raise RuntimeError('RunsModule: app is None')
        if service is None:
            raise RuntimeError('RunsModule: service is None')

        self._app = app
        self._service = service
        self._register_routes(app)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/runs/start', methods=['GET', 'POST'])
        @login_required
        def runs_start_page():
            if request.method == 'POST':
                try:
                    form = self._read_start_form()
                    run_id = self._service.start_run(form)
                    flash(f'Run started: {run_id}')
                    return redirect(url_for('run_details_page', run_id=run_id))
                except Exception:
                    _t, exc, _tb = sys.exc_info()
                    flash(str(exc))

            return render_template('modules/start_run.html', title='Start run')

        @app.route('/runs/<run_id>', methods=['GET'])
        @login_required
        def run_details_page(run_id: str):
            view = self._service.get_run_details(str(run_id))
            return render_template('modules/run_details.html', title='Run details', details=view.model_dump())

        @app.route('/runs/<run_id>/stop', methods=['POST'])
        @login_required
        def run_stop_page(run_id: str):
            try:
                self._service.stop_run(str(run_id))
                flash(f'Stop requested: {run_id}')
            except Exception:
                _t, exc, _tb = sys.exc_info()
                flash(str(exc))
            return redirect(url_for('run_details_page', run_id=run_id))

        @app.route('/iterations/<iteration_id>', methods=['GET'])
        @login_required
        def iteration_details_page(iteration_id: str):
            view = self._service.get_iteration_details(str(iteration_id))
            return render_template('modules/iteration_details.html', title='Iteration details', details=view.model_dump())

    def _read_start_form(self) -> FrontendStartRunForm:
        return FrontendStartRunForm(
            symbol=self._read_str('symbol'),
            rpc_url=self._read_str('rpc_url'),
            network=self._read_str('network'),
            pool_address=self._read_str('pool_address'),
            fee_pct=self._read_float('fee_pct'),
            price_lower=self._read_optional_float('price_lower'),
            price_upper=self._read_optional_float('price_upper'),
            price_lower_pct=self._read_optional_float('price_lower_pct'),
            price_upper_pct=self._read_optional_float('price_upper_pct'),
            total_quote=self._read_float('total_quote'),
            cex_ratio=self._read_float('cex_ratio'),
            trigger_mode=CexTriggerMode(self._read_str('trigger_mode')),
            trigger_pct=self._read_float('trigger_pct'),
            mongo_uri=self._read_str('mongo_uri'),
            mongo_db=self._read_str('mongo_db'),
            mongo_collection=self._read_str('mongo_collection'),
            tick_ms=self._read_int('tick_ms'),
            gtx_cooldown_ms=self._read_int('gtx_cooldown_ms'),
            entrance_timeout_ms=self._read_int('entrance_timeout_ms'),
            cowswap_api_timeout_sec=self._read_int('cowswap_api_timeout_sec'),
            cowswap_wait_timeout_sec=self._read_int('cowswap_wait_timeout_sec'),
            cowswap_poll_interval_sec=self._read_int('cowswap_poll_interval_sec'),
        )

    def _read_str(self, key: str) -> str:
        if key not in request.form:
            raise RuntimeError(f'RunsModule._read_str: {key} is missing in form')
        value = str(request.form[key]).strip()
        if len(value) == 0:
            raise RuntimeError(f'RunsModule._read_str: {key} is empty')
        return value

    def _read_float(self, key: str) -> float:
        return float(self._read_str(key))

    def _read_int(self, key: str) -> int:
        return int(self._read_str(key))

    def _read_optional_float(self, key: str) -> Optional[float]:
        if key not in request.form:
            raise RuntimeError(f'RunsModule._read_optional_float: {key} is missing in form')
        raw = str(request.form[key]).strip()
        if len(raw) == 0:
            return None
        return float(raw)
