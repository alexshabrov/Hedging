"""
Frontend service entrypoint
Date: 2026-02-13
Version: 2.0
"""

from flask import Flask, flash, redirect, render_template, request, session, url_for

from frontend.modules.auth import FRONTEND_AUTH_SESSION_KEY
from frontend.modules.frontend_module import Frontend
from frontend.modules.frontend_env import (
    check_required_env,
    get_front_admin_password,
    get_front_secret_key,
    get_front_session_cookie_domain,
)


### Env ###
check_required_env()


### Flask ###
app = Flask(__name__)
app.secret_key = get_front_secret_key()

session_cookie_domain = get_front_session_cookie_domain()
if session_cookie_domain is not None:
    app.config['SESSION_COOKIE_DOMAIN'] = str(session_cookie_domain)


### Modules ###
frontend = Frontend(app)


### Auth ###
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        if 'username' not in request.form:
            raise RuntimeError('login_page: username is missing in form')
        if 'password' not in request.form:
            raise RuntimeError('login_page: password is missing in form')

        username = str(request.form['username'])
        password = str(request.form['password'])

        if username == 'admin' and password == get_front_admin_password():
            session[FRONTEND_AUTH_SESSION_KEY] = username
            return redirect(url_for('dashboard_page'))

        flash('Invalid credentials')

    return render_template('login_admin.html')


@app.route('/logout', methods=['GET'])
def logout_page():
    if FRONTEND_AUTH_SESSION_KEY in session:
        del session[FRONTEND_AUTH_SESSION_KEY]

    return redirect(url_for('login_page'))


### Main ###
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, threaded=True, debug=False)
