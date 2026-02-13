"""
Runs frontend module
Date: 2026-02-13
Version: 1.0
"""
import sys

import orjson
from flask import Flask, Response, flash, redirect, render_template, request, url_for

from modules.frontend.auth import login_required
from modules.frontend.services.frontend_service import FrontendService


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
                if 'start_payload' not in request.form:
                    raise RuntimeError('runs_start_page: start_payload is missing in form')
                raw = str(request.form['start_payload'])

                try:
                    run_id = self._service.start_run_from_json(raw)
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

        @app.route('/api/frontend/runs/start', methods=['POST'])
        def runs_start_api():
            payload = self._read_json_body()
            if 'config' not in payload:
                raise RuntimeError('runs_start_api: config is missing in payload')
            run_id = self._service.start_run_from_json(orjson.dumps(payload).decode('utf-8'))
            return self._json_response({'ok': True, 'run_id': run_id}, 200)

        @app.route('/api/frontend/runs/<run_id>/stop', methods=['POST'])
        def run_stop_api(run_id: str):
            self._service.stop_run(str(run_id))
            return self._json_response({'ok': True, 'run_id': str(run_id)}, 200)

        @app.route('/api/frontend/runs/<run_id>', methods=['GET'])
        def run_details_api(run_id: str):
            view = self._service.get_run_details(str(run_id))
            return self._json_response({'ok': True, 'item': view.model_dump()}, 200)

        @app.route('/api/frontend/iterations/<iteration_id>', methods=['GET'])
        def iteration_details_api(iteration_id: str):
            view = self._service.get_iteration_details(str(iteration_id))
            return self._json_response({'ok': True, 'item': view.model_dump()}, 200)

        @app.route('/api/frontend/runtime/positions', methods=['GET'])
        def runtime_positions_api():
            rows = self._service.list_runtime_positions()
            return self._json_response({'ok': True, 'items': rows}, 200)

        @app.route('/api/frontend/health', methods=['GET'])
        def frontend_health_api():
            return self._json_response({'ok': True}, 200)

    def _json_response(self, payload: dict, status_code: int) -> Response:
        body = orjson.dumps(payload)
        return Response(response=body, status=int(status_code), mimetype='application/json')

    def _read_json_body(self) -> dict:
        raw = request.get_data(cache=False, as_text=False)
        if raw is None:
            raise RuntimeError('RunsModule._read_json_body: body is None')
        if len(raw) == 0:
            raise RuntimeError('RunsModule._read_json_body: empty body')

        payload = orjson.loads(raw)
        if not isinstance(payload, dict):
            raise RuntimeError(f'RunsModule._read_json_body: payload is not dict: {type(payload)}')
        return payload
