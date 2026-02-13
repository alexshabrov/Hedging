"""
Frontend auth helpers
Date: 2026-02-13
Version: 1.0
"""
from functools import wraps

from flask import redirect, session, url_for


### Constants ###
FRONTEND_AUTH_SESSION_KEY = 'frontend_admin_user'


### Auth ###
def is_logged_in() -> bool:
    return FRONTEND_AUTH_SESSION_KEY in session


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('login_page'))

        return handler(*args, **kwargs)

    return wrapped
