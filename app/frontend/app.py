"""
Frontend service entrypoint
Date: 2026-02-13
Version: 1.0
"""
import os

from flask import Flask, flash, redirect, render_template, request, session, url_for

from modules.frontend.auth import FRONTEND_AUTH_SESSION_KEY
from modules.frontend.frontend_module import Frontend


### Env ###
for _name in [
    'FRONT_SECRET_KEY',
    'FRONT_ADMIN_PASSWORD',
    'FRONT_BACKEND_URL',
    'FRONT_MONGO_URI',
    'FRONT_MONGO_DB',
]:
    if _name not in os.environ:
        raise RuntimeError(f'{_name} is not set')


### Flask ###
app = Flask(__name__)
app.secret_key = str(os.environ['FRONT_SECRET_KEY'])

if 'FRONT_SESSION_COOKIE_DOMAIN' in os.environ:
    app.config['SESSION_COOKIE_DOMAIN'] = str(os.environ['FRONT_SESSION_COOKIE_DOMAIN'])


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

        if username == 'admin' and password == str(os.environ['FRONT_ADMIN_PASSWORD']):
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
