"""Flask application factory for Illinois Campaign Finance tracker."""
from __future__ import annotations

from collections import deque
from datetime import date, datetime, timezone
import hmac
import threading
import time

from flask import Flask, abort, g, jsonify, render_template, request

import config as app_config
from database.connection import close_db, get_db
from webapp.auth import get_csrf_token, get_current_user, validate_csrf_token

DEFAULT_INSECURE_SECRET = "dev-secret-key-change-in-production"
DEFAULT_API_RATE_LIMIT_PER_MINUTE = 120


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _scalar(conn, sql: str, params=(), default=None):
    row = conn.execute(sql, params).fetchone()
    if not row:
        return default
    keys = row.keys() if hasattr(row, "keys") else []
    if not keys:
        return default
    value = row[keys[0]]
    return default if value is None else value


def _parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _age_days(value: str | None) -> int | None:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return (datetime.now(timezone.utc).date() - parsed).days


def _normalize_api_keys(value) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _is_production_like_environment(app: Flask) -> bool:
    env = (app.config.get("APP_ENV") or "").strip().lower()
    return env in {"production", "prod", "staging"}


def _enforce_secret_key_policy(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    if not _is_production_like_environment(app):
        return

    secret_key = (app.config.get("SECRET_KEY") or "").strip()
    if secret_key == DEFAULT_INSECURE_SECRET or len(secret_key) < 32:
        raise RuntimeError(
            "Refusing to start with an insecure SECRET_KEY in production/staging. "
            "Set FLASK_SECRET_KEY to a strong value (32+ chars)."
        )


class InMemoryWindowRateLimiter:
    """Simple in-memory fixed-window limiter for API requests."""

    def __init__(self, *, per_minute: int):
        self.per_minute = max(1, int(per_minute))
        self.window_seconds = 60.0
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int | None]:
        now = time.monotonic()
        threshold = now - self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket

            while bucket and bucket[0] <= threshold:
                bucket.popleft()

            if len(bucket) >= self.per_minute:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return False, retry_after

            bucket.append(now)
            return True, None


def _build_global_data_status(conn, *, local_stale_days: int, federal_stale_days: int) -> dict:
    local_receipt_date = None
    if _table_exists(conn, "bulk_receipts_clean"):
        if _column_exists(conn, "bulk_receipts_clean", "is_archived"):
            local_receipt_date = _scalar(
                conn,
                """
                SELECT MAX(received_date) AS max_date
                FROM bulk_receipts_clean
                WHERE COALESCE(is_archived, 0) = 0
                """,
            )
        else:
            local_receipt_date = _scalar(
                conn,
                "SELECT MAX(received_date) AS max_date FROM bulk_receipts_clean",
            )

    federal_receipt_date = None
    federal_disbursement_date = None
    federal_independent_expenditure_date = None
    federal_sync_updated_at = None
    federal_latest_coverage_date = None
    if _table_exists(conn, "fec_schedule_a_contributions"):
        federal_receipt_date = _scalar(
            conn,
            "SELECT MAX(contribution_receipt_date) AS max_date FROM fec_schedule_a_contributions",
        )
    if _table_exists(conn, "fec_schedule_b_disbursements"):
        federal_disbursement_date = _scalar(
            conn,
            "SELECT MAX(disbursement_date) AS max_date FROM fec_schedule_b_disbursements",
        )
    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        federal_independent_expenditure_date = _scalar(
            conn,
            "SELECT MAX(expenditure_date) AS max_date FROM fec_schedule_e_independent_expenditures",
        )
    if _table_exists(conn, "fec_candidate_cycle_totals"):
        federal_sync_updated_at = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_candidate_cycle_totals",
        )
        federal_latest_coverage_date = _scalar(
            conn,
            """
            SELECT MAX(COALESCE(transaction_coverage_date, coverage_end_date)) AS max_coverage
            FROM fec_candidate_cycle_totals
            """,
        )
    if not federal_sync_updated_at and _table_exists(conn, "raw_extractions"):
        federal_sync_updated_at = _scalar(
            conn,
            """
            SELECT MAX(updated_at) AS max_updated_at
            FROM raw_extractions
            WHERE source_type = 'fec_api:schedules_schedule_a'
               OR source_type = 'fec_api:schedules_schedule_b'
               OR source_type = 'fec_api:schedules_schedule_e'
               OR source_type LIKE 'fec_api:candidate_%_totals'
            """,
        )
    if not federal_sync_updated_at and _table_exists(conn, "fec_schedule_a_contributions"):
        federal_sync_updated_at = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_schedule_a_contributions",
        )
    if not federal_sync_updated_at and _table_exists(conn, "fec_schedule_b_disbursements"):
        federal_sync_updated_at = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_schedule_b_disbursements",
        )
    if not federal_sync_updated_at and _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        federal_sync_updated_at = _scalar(
            conn,
            "SELECT MAX(updated_at) AS max_updated_at FROM fec_schedule_e_independent_expenditures",
        )

    local_age_days = _age_days(local_receipt_date)
    federal_age_days = _age_days(federal_sync_updated_at)
    federal_receipt_age_days = _age_days(federal_receipt_date)
    federal_disbursement_age_days = _age_days(federal_disbursement_date)
    federal_independent_expenditure_age_days = _age_days(federal_independent_expenditure_date)

    local_is_stale = local_age_days is not None and local_age_days > max(0, int(local_stale_days))
    federal_is_stale = federal_age_days is not None and federal_age_days > max(0, int(federal_stale_days))

    has_any_data = bool(
        local_receipt_date
        or federal_receipt_date
        or federal_disbursement_date
        or federal_independent_expenditure_date
        or federal_sync_updated_at
        or federal_latest_coverage_date
    )
    return {
        "local_receipt_date": local_receipt_date,
        "federal_receipt_date": federal_receipt_date,
        "federal_disbursement_date": federal_disbursement_date,
        "federal_independent_expenditure_date": federal_independent_expenditure_date,
        "federal_sync_updated_at": federal_sync_updated_at,
        "federal_latest_coverage_date": federal_latest_coverage_date,
        "local_age_days": local_age_days,
        "federal_age_days": federal_age_days,
        "federal_receipt_age_days": federal_receipt_age_days,
        "federal_disbursement_age_days": federal_disbursement_age_days,
        "federal_independent_expenditure_age_days": federal_independent_expenditure_age_days,
        "local_is_stale": local_is_stale,
        "federal_is_stale": federal_is_stale,
        "is_any_stale": local_is_stale or federal_is_stale,
        "has_any_data": has_any_data,
    }


def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)

    app.config.update(
        {
            "SECRET_KEY": app_config.FLASK_SECRET_KEY,
            "DATABASE_PATH": app_config.DATABASE_PATH,
            "PUBLIC_CONTACT_EMAIL": app_config.PUBLIC_CONTACT_EMAIL,
            "APP_ENV": app_config.APP_ENV,
            "API_KEYS": list(app_config.API_KEYS),
            "API_REQUIRE_KEY": _as_bool(app_config.API_REQUIRE_KEY),
            "API_RATE_LIMIT_PER_MINUTE": int(app_config.API_RATE_LIMIT_PER_MINUTE),
            "SEARCH_MIN_QUERY_LENGTH": int(app_config.SEARCH_MIN_QUERY_LENGTH),
            "SEARCH_MAX_QUERY_LENGTH": int(app_config.SEARCH_MAX_QUERY_LENGTH),
            "SEARCH_QUERY_TIMEOUT_MS": int(app_config.SEARCH_QUERY_TIMEOUT_MS),
            "SEARCH_SLOW_QUERY_MS": int(app_config.SEARCH_SLOW_QUERY_MS),
            "FEDERAL_VIEW_CACHE_ENABLED": _as_bool(app_config.FEDERAL_VIEW_CACHE_ENABLED),
            "FEDERAL_OVERVIEW_CACHE_TTL_SECONDS": int(app_config.FEDERAL_OVERVIEW_CACHE_TTL_SECONDS),
            "FEDERAL_NETWORKS_CACHE_TTL_SECONDS": int(app_config.FEDERAL_NETWORKS_CACHE_TTL_SECONDS),
            "FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS": int(app_config.FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS),
            "FEDERAL_MATCHING_CACHE_TTL_SECONDS": int(app_config.FEDERAL_MATCHING_CACHE_TTL_SECONDS),
            "LOCAL_DATA_STALE_DAYS": int(app_config.LOCAL_DATA_STALE_DAYS),
            "FEDERAL_DATA_STALE_DAYS": int(app_config.FEDERAL_DATA_STALE_DAYS),
        }
    )

    if config:
        app.config.update(config)

    app.config["API_KEYS"] = _normalize_api_keys(app.config.get("API_KEYS"))
    app.config["API_REQUIRE_KEY"] = _as_bool(app.config.get("API_REQUIRE_KEY"))
    if app.config["API_KEYS"] and not app.config.get("API_REQUIRE_KEY"):
        app.config["API_REQUIRE_KEY"] = True

    _enforce_secret_key_policy(app)

    api_limiter = InMemoryWindowRateLimiter(
        per_minute=app.config.get("API_RATE_LIMIT_PER_MINUTE", DEFAULT_API_RATE_LIMIT_PER_MINUTE)
    )
    app.extensions["api_rate_limiter"] = api_limiter
    app.extensions["global_data_status_cache"] = {
        "value": None,
        "expires_at": 0.0,
    }
    app.extensions["global_data_status_cache_lock"] = threading.Lock()

    @app.before_request
    def enforce_api_auth_and_rate_limit():
        if request.blueprint != "api":
            return None

        configured_keys = app.config.get("API_KEYS") or []
        require_key = _as_bool(app.config.get("API_REQUIRE_KEY"))
        api_key = (request.headers.get("X-API-Key") or request.args.get("api_key") or "").strip()

        if require_key:
            if not api_key:
                return jsonify({"error": "api_key_required"}), 401
            if not configured_keys or not any(
                hmac.compare_digest(api_key, configured_key) for configured_key in configured_keys
            ):
                return jsonify({"error": "invalid_api_key"}), 401

        identity = f"key:{api_key}" if api_key else f"ip:{request.remote_addr or 'unknown'}"
        allowed, retry_after = app.extensions["api_rate_limiter"].allow(identity)
        if not allowed:
            response = jsonify(
                {
                    "error": "rate_limit_exceeded",
                    "limit_per_minute": app.config.get(
                        "API_RATE_LIMIT_PER_MINUTE", DEFAULT_API_RATE_LIMIT_PER_MINUTE
                    ),
                }
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after or 1)
            return response
        return None

    @app.before_request
    def enforce_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if request.blueprint == "api" or request.endpoint == "static":
            return None
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if validate_csrf_token(token):
            return None
        abort(400, description="Invalid CSRF token.")

    # Register teardown - close connection at end of each request
    @app.teardown_appcontext
    def teardown_db(exception):
        db = g.pop('_database', None)
        if db is not None:
            close_db(db)

    # Add database helper - creates per-request connection using Flask's g object
    def get_database():
        if '_database' not in g:
            g._database = get_db(app.config['DATABASE_PATH'])
        return g._database

    app.get_database = get_database

    # Register blueprints
    from webapp.routes.main import main_bp
    from webapp.routes.auth import auth_bp
    from webapp.routes.committees import committees_bp
    from webapp.routes.donors import donors_bp
    from webapp.routes.reports import reports_bp
    from webapp.routes.candidate_finance import candidate_finance_bp
    from webapp.routes.federal_finance import federal_finance_bp
    from webapp.routes.d2_receipts_recon import d2_receipts_recon_bp
    from webapp.routes.d2_expenditures_recon import d2_expenditures_recon_bp
    from webapp.routes.analytics import analytics_bp
    from webapp.routes.manual_entry import manual_entry_bp
    from webapp.routes.admin import admin_bp
    from webapp.routes.api import api_bp
    from webapp.routes.lobbying import lobbying_bp
    from webapp.routes.irs527 import irs527_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(committees_bp, url_prefix='/committees')
    app.register_blueprint(donors_bp, url_prefix='/donors')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(candidate_finance_bp, url_prefix='/candidate-finance')
    app.register_blueprint(federal_finance_bp, url_prefix='/federal-finance')
    app.register_blueprint(d2_receipts_recon_bp, url_prefix='/d2-reconciliation')
    app.register_blueprint(d2_expenditures_recon_bp, url_prefix='/d2-expenditures-reconciliation')
    app.register_blueprint(analytics_bp, url_prefix='/analytics')
    app.register_blueprint(manual_entry_bp, url_prefix='/manual-entry')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(lobbying_bp, url_prefix='/lobbying')
    app.register_blueprint(irs527_bp, url_prefix='/527')

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        return render_template("errors/500.html"), 500

    # Confidence label filter for cross-match scores
    def confidence_label(score):
        """Return (label_text, css_class) for a cross-match score."""
        if score is None:
            return ("Unknown", "confidence-unknown")
        if score >= 0.90:
            return ("High confidence", "confidence-high")
        if score >= 0.70:
            return ("Likely match", "confidence-likely")
        if score >= 0.50:
            return ("Possible match", "confidence-possible")
        return ("Review needed", "confidence-review")

    app.jinja_env.filters['confidence_label'] = confidence_label

    # Context processors
    @app.context_processor
    def inject_helpers():
        def format_currency(value):
            if value is None:
                return '$0.00'
            return '${:,.2f}'.format(value)

        data_status = None
        try:
            ttl_seconds = max(0, int(app.config.get("GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS", 45)))
            now = time.monotonic()
            cache = app.extensions.get("global_data_status_cache", {"value": None, "expires_at": 0.0})

            if ttl_seconds > 0 and cache.get("value") is not None and float(cache.get("expires_at", 0.0)) > now:
                data_status = cache.get("value")
            else:
                conn = get_database()
                data_status = _build_global_data_status(
                    conn,
                    local_stale_days=app.config.get("LOCAL_DATA_STALE_DAYS", 45),
                    federal_stale_days=app.config.get("FEDERAL_DATA_STALE_DAYS", 14),
                )
                if ttl_seconds > 0:
                    lock = app.extensions.get("global_data_status_cache_lock")
                    if lock is not None:
                        with lock:
                            app.extensions["global_data_status_cache"] = {
                                "value": data_status,
                                "expires_at": now + float(ttl_seconds),
                            }
        except Exception:
            data_status = None

        return dict(
            format_currency=format_currency,
            confidence_label=confidence_label,
            current_manual_user=get_current_user(),
            public_contact_email=(app.config.get('PUBLIC_CONTACT_EMAIL') or '').strip(),
            csrf_token=get_csrf_token,
            global_data_status=data_status,
        )

    return app
