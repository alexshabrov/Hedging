"""
Runs frontend module
Date: 2026-02-13
Version: 3.0
"""
import sys
import traceback

from flask import Flask, flash, redirect, render_template, request, url_for

from backend.models.hedger_models import CexTriggerMode
from live.lib.logger import get_logger
from frontend.modules.auth import login_required
from frontend.modules.models.frontend_models import FrontendCreateTemplateForm, FrontendStartFromTemplateForm, FrontendUpdateTemplateForm
from frontend.modules.services.frontend_service import FrontendService


class RunsModule:
    def __init__(self, app: Flask, service: FrontendService):
        if app is None:
            raise RuntimeError('RunsModule: app is None')
        if service is None:
            raise RuntimeError('RunsModule: service is None')

        self._app = app
        self._service = service
        self._logger = get_logger('frontend_runs')
        self._register_routes(app)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/runs/start', methods=['GET', 'POST'])
        @login_required
        def runs_start_page():
            if request.method == 'POST':
                try:
                    action = self._read_str('action')

                    if str(action) == 'create_template':
                        form = self._read_template_form()
                        template = self._service.create_run_template(form)
                        flash(f'Template created: {template.template_id}')
                        return redirect(url_for('runs_start_page'))

                    if str(action) == 'start_from_template':
                        form = self._read_start_from_template_form()
                        run_id = self._service.start_run_from_template(form)
                        flash(f'Run started: {run_id}')
                        return redirect(url_for('run_details_page', run_id=run_id))

                    if str(action) == 'edit_template':
                        form = self._read_update_template_form()
                        template = self._service.update_run_template(form)
                        flash(f'Template updated: {template.template_id}')
                        return redirect(url_for('runs_start_page', edit_template_id=template.template_id))

                    if str(action) == 'delete_template':
                        template_id = self._read_str('template_id')
                        self._service.delete_run_template(template_id)
                        flash(f'Template deleted: {template_id}')
                        return redirect(url_for('runs_start_page'))

                    raise RuntimeError(f'RunsModule.runs_start_page: unsupported action: {action}')
                except Exception:
                    _t, exc, _tb = sys.exc_info()
                    self._logger.error(f'runs_start_page_failed error={exc} traceback=\n{traceback.format_exc()}')
                    flash(str(exc))

            templates = self._service.list_run_templates()
            template_items = []
            for item in templates:
                template_items.append(item.model_dump())

            networks = self._service.list_network_configs()
            network_items = []
            for item in networks:
                network_items.append(item.model_dump())

            edit_template = None
            if 'edit_template_id' in request.args:
                edit_template_id = str(request.args['edit_template_id']).strip()
                if len(edit_template_id) > 0:
                    edit_template = self._service.get_run_template(edit_template_id).model_dump()

            return render_template(
                'modules/start_run.html',
                title='Run templates',
                templates=template_items,
                networks=network_items,
                edit_template=edit_template,
            )

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
                self._logger.error(f'run_stop_page_failed run_id={run_id} error={exc} traceback=\n{traceback.format_exc()}')
                flash(str(exc))
            return redirect(url_for('run_details_page', run_id=run_id))

        @app.route('/iterations/<iteration_id>', methods=['GET'])
        @login_required
        def iteration_details_page(iteration_id: str):
            view = self._service.get_iteration_details(str(iteration_id))
            return render_template('modules/iteration_details.html', title='Iteration details', details=view.model_dump())

    def _read_template_form(self) -> FrontendCreateTemplateForm:
        return FrontendCreateTemplateForm(
            network=self._read_str('network'),
            symbol=self._read_str('symbol'),
            pool_address=self._read_str('pool_address'),
            fee_pct=self._read_float('fee_pct'),
            cex_ratio=self._read_float('cex_ratio'),
            trigger_mode=CexTriggerMode(self._read_str('trigger_mode')),
            trigger_pct=self._read_float('trigger_pct'),
            trigger_units=self._read_int('trigger_units'),
        )

    def _read_start_from_template_form(self) -> FrontendStartFromTemplateForm:
        return FrontendStartFromTemplateForm(
            template_id=self._read_str('template_id'),
            total_quote=self._read_float('total_quote'),
            price_lower_pct=self._read_float('price_lower_pct'),
            price_upper_pct=self._read_float('price_upper_pct'),
        )

    def _read_update_template_form(self) -> FrontendUpdateTemplateForm:
        return FrontendUpdateTemplateForm(
            template_id=self._read_str('template_id'),
            network=self._read_str('network'),
            symbol=self._read_str('symbol'),
            pool_address=self._read_str('pool_address'),
            fee_pct=self._read_float('fee_pct'),
            cex_ratio=self._read_float('cex_ratio'),
            trigger_mode=CexTriggerMode(self._read_str('trigger_mode')),
            trigger_pct=self._read_float('trigger_pct'),
            trigger_units=self._read_int('trigger_units'),
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
