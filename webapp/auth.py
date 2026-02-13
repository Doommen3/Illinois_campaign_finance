"""Authentication helpers for protected web routes."""
from __future__ import annotations

import hmac
import secrets
from functools import wraps
from typing import Callable

from flask import current_app, g, redirect, request, session, url_for

from database.models import AppUser


SESSION_USER_ID_KEY = "manual_entry_user_id"
SESSION_CSRF_TOKEN_KEY = "csrf_token"


def get_current_user() -> AppUser | None:
    """Return the currently authenticated manual-entry user, if any."""
    if hasattr(g, "_current_manual_user"):
        return g._current_manual_user

    user_id = session.get(SESSION_USER_ID_KEY)
    if not user_id:
        g._current_manual_user = None
        return None

    conn = current_app.get_database()
    user = AppUser.get_by_id(conn, user_id)
    if not user or not user.is_active:
        session.pop(SESSION_USER_ID_KEY, None)
        g._current_manual_user = None
        return None

    g._current_manual_user = user
    return user


def get_csrf_token() -> str:
    """Get or create a per-session CSRF token."""
    token = session.get(SESSION_CSRF_TOKEN_KEY)
    if token:
        return token

    token = secrets.token_urlsafe(32)
    session[SESSION_CSRF_TOKEN_KEY] = token
    return token


def rotate_csrf_token() -> str:
    """Rotate the CSRF token (e.g., after login)."""
    token = secrets.token_urlsafe(32)
    session[SESSION_CSRF_TOKEN_KEY] = token
    return token


def validate_csrf_token(value: str | None) -> bool:
    """Validate a submitted CSRF token."""
    expected = (session.get(SESSION_CSRF_TOKEN_KEY) or "").strip()
    candidate = (value or "").strip()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(expected, candidate)


def login_required(view: Callable):
    """Require authentication for manual-entry routes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped
