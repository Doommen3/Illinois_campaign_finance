"""Authentication routes for manual entry access."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session

from database.models import AppUser
from webapp.auth import SESSION_USER_ID_KEY, get_current_user, rotate_csrf_token


auth_bp = Blueprint('auth', __name__)


def _safe_next_url(value: str | None) -> str:
    if value and value.startswith('/') and not value.startswith('//'):
        return value
    return url_for('manual_entry.queue')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login form for manual entry users."""
    if get_current_user():
        return redirect(url_for('manual_entry.queue'))

    next_url = _safe_next_url(request.args.get('next') or request.form.get('next'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = current_app.get_database()
        user = AppUser.verify_credentials(conn, username, password)
        if not user:
            flash('Invalid username or password.', 'error')
            return render_template('auth/login.html', next_url=next_url)

        session[SESSION_USER_ID_KEY] = user.id
        rotate_csrf_token()
        flash('Signed in.', 'success')
        return redirect(next_url)

    return render_template('auth/login.html', next_url=next_url)


@auth_bp.route('/logout')
def logout():
    """Log out manual entry user."""
    session.pop(SESSION_USER_ID_KEY, None)
    flash('Signed out.', 'success')
    return redirect(url_for('main.index'))
