"""Authentication helpers for protected web routes."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import current_app, g, redirect, request, session, url_for

from database.models import AppUser


SESSION_USER_ID_KEY = "manual_entry_user_id"


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


def login_required(view: Callable):
    """Require authentication for manual-entry routes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped
