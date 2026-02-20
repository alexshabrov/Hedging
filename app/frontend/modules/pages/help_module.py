"""
Help frontend module
Date: 2026-02-16
Version: 1.0
"""
from flask import Flask, render_template

from frontend.modules.auth import login_required


class HelpModule:
    def __init__(self, app: Flask):
        if app is None:
            raise RuntimeError('HelpModule: app is None')

        self._register_routes(app)

    def _register_routes(self, app: Flask) -> None:
        @app.route('/help', methods=['GET'])
        @login_required
        def help_page():
            return render_template('modules/help.html', title='Help')
