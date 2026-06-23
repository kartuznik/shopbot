from __future__ import annotations

import secrets
from functools import wraps
from typing import Any, Callable

from flask import abort, flash, redirect, request, session, url_for


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if not session.get('is_admin'):
            flash('Требуется авторизация.', 'warning')
            return redirect(url_for('login'))
        return view(*args, **kwargs)

    return wrapped_view


def get_csrf_token() -> str:
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_hex(16)
        session['_csrf_token'] = token
    return token


def validate_csrf() -> None:
    token = request.form.get('_csrf_token', '')
    if token != session.get('_csrf_token'):
        abort(400, description='CSRF token invalid')
