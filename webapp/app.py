"""Flask application factory for Illinois Campaign Finance tracker."""
from __future__ import annotations

from collections import deque
from datetime import date, datetime, timezone
import hmac
import threading
import time
from urllib.parse import quote, urlencode

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request
from flask.sessions import SecureCookieSessionInterface

import config as app_config
from database.connection import close_db, get_db
from webapp.auth import get_csrf_token, get_current_user, validate_csrf_token
from webapp.utils.scope_links import scope_form_hidden, scope_presets, scope_url

DEFAULT_INSECURE_SECRET = "dev-secret-key-change-in-production"
DEFAULT_API_RATE_LIMIT_PER_MINUTE = 120

# LD1 (.claude/plans/load-times.md): keep crawlers off search, the geo drill-downs, the non-public tools
# and every cache-busting or scoped query-string variant (scoped views are for readers, not indexes).
ROBOTS_DISALLOW_RULES = (
    "/search",
    "/analytics/geo-drilldown",
    "/federal-finance/geo-drilldown",
    "/api/",
    "/experimental/",
    "/compare",
    "/investigate",
    "/person-intelligence",
    "/admin/",
    "/manual-entry/",
    "/auth/",
    "/*?*refresh_cache=",
    "/*?*refresh_full=",
    "/*?*sync_full=",
    "/*?*load_mode=",
    "/*?*period=",
    "/*?*cycle=",
    "/*?*year=",
    "/*?*date_from=",
)

# LD4: query parameters that force a cache rebuild; honoured only for a signed-in user.
REFRESH_QUERY_PARAMS = ("refresh_cache", "refresh_full", "sync_full")

# LD2: blueprints whose HTML is never marked edge-cacheable.
EDGE_CACHE_EXCLUDED_BLUEPRINTS = frozenset({"auth", "admin", "manual_entry", "api", "experimental"})


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
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
    if hasattr(row, "keys"):
        first_key = next(iter(row.keys()), None)
        if first_key is None:
            return default
        value = row[first_key]
    else:
        value = row[0] if len(row) else default
    return default if value is None else value


def _parse_date(value: str | date | datetime | None) -> date | None:
    # psycopg returns date/datetime objects for DATE/TIMESTAMP columns; the
    # legacy string parsing below silently broke the whole data-status
    # snapshot (and the prod "Data Status" strip) when handed one.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = (value or "").strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _age_days(value: str | date | datetime | None) -> int | None:
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
    # The matview's native DATE/BOOLEAN columns answer from its received_date index (~1 ms); the same
    # MAX through the bulk_receipts_clean compat view is a parallel seq scan (~1.5 s on prod data).
    if _table_exists(conn, "isbe_condensed_receipts"):
        local_receipt_date = _scalar(
            conn,
            "SELECT MAX(received_date) AS max_date FROM isbe_condensed_receipts WHERE archived = FALSE",
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


class EdgeCacheSessionInterface(SecureCookieSessionInterface):
    """LD2: a response to a request that carried no session cookie, whose session stayed empty and
    unmodified, is not marked `Vary: Cookie`: nothing in it came from a cookie and no cookie is set.
    Flask 3.1.3 marks the session "accessed" on any touch of the proxy (base.html's
    get_flashed_messages() touches it on every page), which would put `Vary: Cookie` on every
    anonymous page and defeat the edge cache; Flask 3.0 only counted key reads. A request carrying
    the cookie, or a response that sets one (a flash, a login), varies on Cookie as before."""

    def save_session(self, app, session, response):
        if not session and not session.modified and self.get_cookie_name(app) not in request.cookies:
            return
        super().save_session(app, session, response)


def create_app(config=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.session_interface = EdgeCacheSessionInterface()

    app.config.update(
        {
            "SECRET_KEY": app_config.FLASK_SECRET_KEY,
            "DATABASE_PATH": app_config.DATABASE_PATH,
            "DATABASE_URL": app_config.DATABASE_URL,
            "DATABASE_TARGET": app_config.DATABASE_TARGET,
            "PUBLIC_CONTACT_EMAIL": app_config.PUBLIC_CONTACT_EMAIL,
            "APP_ENV": app_config.APP_ENV,
            "EXPERIMENTAL_VIZ_LAB_ENABLED": _as_bool(app_config.EXPERIMENTAL_VIZ_LAB_ENABLED),
            "EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS": int(app_config.EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS),
            "API_KEYS": list(app_config.API_KEYS),
            "API_REQUIRE_KEY": _as_bool(app_config.API_REQUIRE_KEY),
            "API_RATE_LIMIT_PER_MINUTE": int(app_config.API_RATE_LIMIT_PER_MINUTE),
            "API_ALLOW_QUERY_KEY": _as_bool(app_config.API_ALLOW_QUERY_KEY),
            "SEARCH_MIN_QUERY_LENGTH": int(app_config.SEARCH_MIN_QUERY_LENGTH),
            "SEARCH_MAX_QUERY_LENGTH": int(app_config.SEARCH_MAX_QUERY_LENGTH),
            "SEARCH_QUERY_TIMEOUT_MS": int(app_config.SEARCH_QUERY_TIMEOUT_MS),
            "SEARCH_SLOW_QUERY_MS": int(app_config.SEARCH_SLOW_QUERY_MS),
            "SEARCH_RESULTS_CACHE_TTL_SECONDS": int(app_config.SEARCH_RESULTS_CACHE_TTL_SECONDS),
            "FEDERAL_VIEW_CACHE_ENABLED": _as_bool(app_config.FEDERAL_VIEW_CACHE_ENABLED),
            "FEDERAL_OVERVIEW_CACHE_TTL_SECONDS": int(app_config.FEDERAL_OVERVIEW_CACHE_TTL_SECONDS),
            "FEDERAL_NETWORKS_CACHE_TTL_SECONDS": int(app_config.FEDERAL_NETWORKS_CACHE_TTL_SECONDS),
            "FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS": int(app_config.FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS),
            "FEDERAL_MATCHING_CACHE_TTL_SECONDS": int(app_config.FEDERAL_MATCHING_CACHE_TTL_SECONDS),
            "LOCAL_DATA_STALE_DAYS": int(app_config.LOCAL_DATA_STALE_DAYS),
            "FEDERAL_DATA_STALE_DAYS": int(app_config.FEDERAL_DATA_STALE_DAYS),
            "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": int(app_config.GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS),
            "DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS": int(app_config.DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS),
            "DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS": int(app_config.DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS),
            "DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS": int(app_config.DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS),
            "ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS),
            "ANALYTICS_NETWORKS_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_NETWORKS_CACHE_TTL_SECONDS),
            "ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS),
            "ANALYTICS_RISK_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_RISK_CACHE_TTL_SECONDS),
            "ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS),
            "ANALYTICS_DONORS_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_DONORS_CACHE_TTL_SECONDS),
            "ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS": int(app_config.ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS),
            "ENTITY_ANALYTICS_CACHE_TTL_SECONDS": int(app_config.ENTITY_ANALYTICS_CACHE_TTL_SECONDS),
            "FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS": int(app_config.FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS),
            "IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS": int(app_config.IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS),
            "DASHBOARD_PREWARM_ENABLED": _as_bool(app_config.DASHBOARD_PREWARM_ENABLED),
            "ROUTE_PERF_CACHE_ENABLED": _as_bool(app_config.ROUTE_PERF_CACHE_ENABLED),
            "HTML_EDGE_CACHE_SECONDS": int(app_config.HTML_EDGE_CACHE_SECONDS),
            "REFRESH_PARAMS_REQUIRE_LOGIN": bool(app_config.REFRESH_PARAMS_REQUIRE_LOGIN),
            "WEB_STATEMENT_TIMEOUT_MS": int(app_config.WEB_STATEMENT_TIMEOUT_MS),
            "WEB_MAX_PARALLEL_WORKERS_PER_GATHER": int(app_config.WEB_MAX_PARALLEL_WORKERS_PER_GATHER),
        }
    )

    if config:
        app.config.update(config)

    # When TESTING with an explicit DATABASE_PATH, prefer it over DATABASE_URL
    # so test fixtures use their own isolated SQLite database.
    if app.config.get("TESTING") and config and "DATABASE_PATH" in config and "DATABASE_URL" not in config:
        app.config["DATABASE_URL"] = ""

    resolved_database_target = (
        (app.config.get("DATABASE_URL") or "").strip()
        or (app.config.get("DATABASE_PATH") or "").strip()
        or app_config.DATABASE_PATH
    )
    app.config["DATABASE_TARGET"] = resolved_database_target
    app.config["DATABASE_PATH"] = resolved_database_target

    if app.config.get("TESTING") and (not config or "ROUTE_PERF_CACHE_ENABLED" not in config):
        app.config["ROUTE_PERF_CACHE_ENABLED"] = False

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
        allow_query_key = _as_bool(app.config.get("API_ALLOW_QUERY_KEY"))
        api_key = (request.headers.get("X-API-Key") or "").strip()
        if not api_key and allow_query_key:
            api_key = (request.args.get("api_key") or "").strip()

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

    def has_session_cookie() -> bool:
        # Checking request.cookies leaves the session unread, so a cookie-less response gets no Vary: Cookie.
        return app.config["SESSION_COOKIE_NAME"] in request.cookies

    @app.before_request
    def gate_refresh_params():
        """LD4: only a signed-in user may force a rebuild; anyone else goes to the same URL without the refresh keys."""
        if request.method not in {"GET", "HEAD"}:
            return None
        if not app.config.get("REFRESH_PARAMS_REQUIRE_LOGIN", True):
            return None
        if not any(key in request.args for key in REFRESH_QUERY_PARAMS):
            return None
        if has_session_cookie() and get_current_user() is not None:
            return None
        kept = [(key, value) for key, value in request.args.items(multi=True) if key not in REFRESH_QUERY_PARAMS]
        location = request.script_root + quote(request.path, safe="/")
        if kept:
            location = f"{location}?{urlencode(kept)}"
        return redirect(location, code=302)

    @app.after_request
    def add_edge_cache_header(response):
        """LD2: anonymous public HTML (200/404) may be served by the edge cache for HTML_EDGE_CACHE_SECONDS."""
        seconds = int(app.config.get("HTML_EDGE_CACHE_SECONDS", 300))
        if (
            seconds > 0
            and request.method in {"GET", "HEAD"}
            and response.status_code in {200, 404}
            and response.mimetype == "text/html"
            and request.blueprint not in EDGE_CACHE_EXCLUDED_BLUEPRINTS
            and not has_session_cookie()
        ):
            if g.get("_no_edge_cache"):
                response.headers["Cache-Control"] = "no-store"
            else:
                response.headers["Cache-Control"] = f"public, max-age=0, s-maxage={seconds}"
        return response

    # Register teardown - close connection at end of each request
    @app.teardown_appcontext
    def teardown_db(exception):
        db = g.pop('_database', None)
        if db is not None:
            close_db(db)

    # Add database helper - creates per-request connection using Flask's g object
    def get_database():
        if '_database' not in g:
            g._database = get_db(
                app.config['DATABASE_TARGET'],
                web=True,
                statement_timeout_ms=app.config["WEB_STATEMENT_TIMEOUT_MS"],
                max_parallel_workers_per_gather=app.config["WEB_MAX_PARALLEL_WORKERS_PER_GATHER"],
            )
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
    from webapp.routes.openbook import openbook_bp
    from webapp.routes.experimental import experimental_bp
    from webapp.routes.viz_lab import viz_lab_bp
    from webapp.routes.candidates import candidates_bp
    from webapp.routes.data_status import data_status_bp
    from webapp.routes.entity_analytics import bp as entity_analytics_bp
    from webapp.routes import viz_lab as viz_lab_routes

    app.register_blueprint(main_bp)
    app.register_blueprint(candidates_bp, url_prefix='/candidates')
    app.register_blueprint(entity_analytics_bp)  # /candidates/<id>/<tab>, /analytics/state-races/<key>/<tab>
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
    app.register_blueprint(openbook_bp, url_prefix='/openbook')
    app.register_blueprint(experimental_bp, url_prefix='/experimental')
    app.register_blueprint(viz_lab_bp, url_prefix='/experimental/viz-lab')
    app.register_blueprint(data_status_bp)  # /data-status

    @app.errorhandler(404)
    def not_found(error):
        g._skip_global_data_status = True
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        g._skip_global_data_status = True
        return render_template("errors/500.html"), 500

    @app.route("/robots.txt")
    def robots_txt():
        body = "User-agent: *\n" + "".join(f"Disallow: {rule}\n" for rule in ROBOTS_DISALLOW_RULES)
        return Response(body, mimetype="text/plain")

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

    if _as_bool(app.config.get("DASHBOARD_PREWARM_ENABLED", True)) and not app.config.get("TESTING"):
        def _prewarm_dashboard_cache() -> None:
            try:
                from webapp.routes.main import warm_dashboard_home_cache

                warm_dashboard_home_cache(
                    app.config['DATABASE_TARGET'],
                    insights_ttl_seconds=max(15, int(app.config.get("DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS", 180))),
                    candidate_stats_ttl_seconds=max(15, int(app.config.get("DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS", 180))),
                    top_donors_ttl_seconds=max(15, int(app.config.get("DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS", 180))),
                )
            except Exception:
                return

        threading.Thread(target=_prewarm_dashboard_cache, daemon=True).start()

        def _prewarm_analytics_caches() -> None:
            try:
                from webapp.routes.analytics import ANALYTICS_CACHE_TTL_FALLBACK_SECONDS, warm_analytics_caches

                warm_analytics_caches(
                    app.config['DATABASE_TARGET'],
                    networks_ttl=max(15, int(app.config.get("ANALYTICS_NETWORKS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                    relationships_ttl=max(15, int(app.config.get("ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                    risk_ttl=max(15, int(app.config.get("ANALYTICS_RISK_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                    overview_ttl=max(15, int(app.config.get("ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                    geography_ttl=max(15, int(app.config.get("ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                    donors_ttl=max(15, int(app.config.get("ANALYTICS_DONORS_CACHE_TTL_SECONDS", ANALYTICS_CACHE_TTL_FALLBACK_SECONDS))),
                )
            except Exception:
                return

        threading.Thread(target=_prewarm_analytics_caches, daemon=True).start()

    def get_global_data_status():
        """Cached data-freshness snapshot (per-source latest dates); None on failure.

        Shared by the context processor (Data Status strip) and by routes that
        need a `data_through` date for the time-scope bar (webapp/utils/data_through.py).
        """
        data_status = None
        try:
            ttl_seconds = max(0, int(app.config.get("GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS", 900)))
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
        return data_status

    app.get_global_data_status = get_global_data_status

    # Context processors
    @app.context_processor
    def inject_helpers():
        def format_currency(value):
            if value is None:
                return '$0.00'
            return '${:,.2f}'.format(value)

        # Error pages (404/500) render without the Data Status strip: a bot's 404 runs no freshness queries.
        data_status = None if g.get("_skip_global_data_status") else get_global_data_status()

        # Viz Lab nav link: shown only when the feature flag is on AND graph.json exists under
        # viz_lab._artifact_root() (the artifacts aren't deployed to prod, where it would dead-end in a 404).
        viz_lab_nav_enabled = _as_bool(app.config.get("EXPERIMENTAL_VIZ_LAB_ENABLED", False)) and (
            viz_lab_routes._artifact_root() / "graph.json"
        ).exists()

        return dict(
            format_currency=format_currency,
            confidence_label=confidence_label,
            current_manual_user=get_current_user() if has_session_cookie() else None,
            public_contact_email=(app.config.get('PUBLIC_CONTACT_EMAIL') or '').strip(),
            csrf_token=get_csrf_token,
            global_data_status=data_status,
            scope_url=scope_url,
            scope_form_hidden=scope_form_hidden,
            scope_presets=scope_presets,
            experimental_viz_lab_enabled=viz_lab_nav_enabled,
        )

    return app
