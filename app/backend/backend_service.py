"""
Backend service entrypoint
Date: 2026-02-13
Version: 1.0
"""
from flask import Flask

from backend.modules.backend_class import Backend
from backend.modules.backend_env import check_required_env


### Flask ###
check_required_env()
app = Flask(__name__)
backend = Backend(app)


### Main ###
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, threaded=True, debug=False)
