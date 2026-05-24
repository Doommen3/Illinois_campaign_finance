"""Configuration for Illinois Campaign Finance Tracker."""
import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


# Database — PostgreSQL is primary (local and production)
DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://devin@localhost/ilcf'
)
# Legacy SQLite path (deprecated — kept for backward compat during transition)
DATABASE_PATH = os.environ.get(
    'DATABASE_PATH',
    str(BASE_DIR / 'data' / 'campaign_finance.db')
)
DATABASE_TARGET = DATABASE_URL or DATABASE_PATH

# Rate limiting
RATE_LIMIT_RPM = int(os.environ.get('RATE_LIMIT_RPM', 30))
RATE_LIMIT_MIN_DELAY = float(os.environ.get('RATE_LIMIT_MIN_DELAY', 1.0))
RATE_LIMIT_MAX_DELAY = float(os.environ.get('RATE_LIMIT_MAX_DELAY', 3.0))
RATE_LIMIT_BACKOFF_MULTIPLIER = float(os.environ.get('RATE_LIMIT_BACKOFF_MULTIPLIER', 2.0))
RATE_LIMIT_MAX_BACKOFF = float(os.environ.get('RATE_LIMIT_MAX_BACKOFF', 60.0))

# Flask
APP_ENV = (os.environ.get('APP_ENV') or 'development').strip().lower()
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production').strip()
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
PUBLIC_CONTACT_EMAIL = os.environ.get('PUBLIC_CONTACT_EMAIL', '').strip()
EXPERIMENTAL_VIZ_LAB_ENABLED = _env_bool(
    'EXPERIMENTAL_VIZ_LAB_ENABLED',
    default=APP_ENV not in {'production', 'prod', 'staging'},
)
EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS = int(os.environ.get('EXPERIMENTAL_VIZ_CACHE_TTL_SECONDS', 180))

# API
API_KEYS = _env_list('API_KEYS')
API_REQUIRE_KEY = _env_bool('API_REQUIRE_KEY', default=bool(API_KEYS))
API_RATE_LIMIT_PER_MINUTE = int(os.environ.get('API_RATE_LIMIT_PER_MINUTE', 120))
API_ALLOW_QUERY_KEY = _env_bool('API_ALLOW_QUERY_KEY', default=APP_ENV not in {'production', 'prod', 'staging'})

# Search guardrails
SEARCH_MIN_QUERY_LENGTH = int(os.environ.get('SEARCH_MIN_QUERY_LENGTH', 2))
SEARCH_MAX_QUERY_LENGTH = int(os.environ.get('SEARCH_MAX_QUERY_LENGTH', 64))
SEARCH_QUERY_TIMEOUT_MS = int(os.environ.get('SEARCH_QUERY_TIMEOUT_MS', 700))
SEARCH_SLOW_QUERY_MS = int(os.environ.get('SEARCH_SLOW_QUERY_MS', 400))
SEARCH_RESULTS_CACHE_TTL_SECONDS = int(os.environ.get('SEARCH_RESULTS_CACHE_TTL_SECONDS', 120))
SEARCH_RESULTS_CACHE_MAX_ENTRIES = int(os.environ.get('SEARCH_RESULTS_CACHE_MAX_ENTRIES', 256))

# Federal route caching
FEDERAL_VIEW_CACHE_ENABLED = _env_bool('FEDERAL_VIEW_CACHE_ENABLED', default=True)
FEDERAL_OVERVIEW_CACHE_TTL_SECONDS = int(os.environ.get('FEDERAL_OVERVIEW_CACHE_TTL_SECONDS', 900))
FEDERAL_NETWORKS_CACHE_TTL_SECONDS = int(os.environ.get('FEDERAL_NETWORKS_CACHE_TTL_SECONDS', 600))
FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS = int(os.environ.get('FEDERAL_DONOR_INTEL_CACHE_TTL_SECONDS', 600))
FEDERAL_MATCHING_CACHE_TTL_SECONDS = int(os.environ.get('FEDERAL_MATCHING_CACHE_TTL_SECONDS', 600))

# Data freshness display
LOCAL_DATA_STALE_DAYS = int(os.environ.get('LOCAL_DATA_STALE_DAYS', 45))
FEDERAL_DATA_STALE_DAYS = int(os.environ.get('FEDERAL_DATA_STALE_DAYS', 14))

# Route-level performance caching. Defaults assume the underlying data
# refreshes ~weekly; long TTLs are fine because Redis-backed RouteCache
# survives gunicorn restarts and the upcoming refresh-time invalidation
# (Phase 1.6) will clear entries when data actually changes.
DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS = int(os.environ.get('DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS', 21600))
DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS = int(os.environ.get('DASHBOARD_CANDIDATE_STATS_CACHE_TTL_SECONDS', 21600))
DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS = int(os.environ.get('DASHBOARD_TOP_DONORS_CACHE_TTL_SECONDS', 21600))
ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS = int(os.environ.get('ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS', 21600))
ANALYTICS_NETWORKS_CACHE_TTL_SECONDS = int(os.environ.get('ANALYTICS_NETWORKS_CACHE_TTL_SECONDS', 21600))
ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS = int(os.environ.get('ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS', 21600))
ANALYTICS_RISK_CACHE_TTL_SECONDS = int(os.environ.get('ANALYTICS_RISK_CACHE_TTL_SECONDS', 21600))
ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS = int(os.environ.get('ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS', 180))
FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS = int(os.environ.get('FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS', 180))
IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS = int(os.environ.get('IRS527_DARK_MONEY_STATS_CACHE_TTL_SECONDS', 180))
DASHBOARD_PREWARM_ENABLED = _env_bool('DASHBOARD_PREWARM_ENABLED', default=True)
ROUTE_PERF_CACHE_ENABLED = _env_bool('ROUTE_PERF_CACHE_ENABLED', default=True)

# Scraping
DEFAULT_START_PAGE = 1
DEFAULT_END_PAGE = 40
DEFAULT_BATCH_SIZE = 20

# OpenBook vendor enrichment
OPENBOOK_SEED_MIN_AMOUNT = float(os.environ.get('OPENBOOK_SEED_MIN_AMOUNT', 10000))
OPENBOOK_SEED_LIMIT_PER_SOURCE = int(os.environ.get('OPENBOOK_SEED_LIMIT_PER_SOURCE', 500))
OPENBOOK_BATCH_RPM = int(os.environ.get('OPENBOOK_BATCH_RPM', 30))
OPENBOOK_BATCH_MIN_DELAY = float(os.environ.get('OPENBOOK_BATCH_MIN_DELAY', 1.0))
OPENBOOK_BATCH_MAX_DELAY = float(os.environ.get('OPENBOOK_BATCH_MAX_DELAY', 2.0))
OPENBOOK_BATCH_MAX_CONSECUTIVE_ERRORS = int(os.environ.get('OPENBOOK_BATCH_MAX_CONSECUTIVE_ERRORS', 10))
OPENBOOK_BATCH_WITH_DETAILS = _env_bool('OPENBOOK_BATCH_WITH_DETAILS', default=True)
OPENBOOK_DETAIL_MAX_ERROR_RETRIES = int(os.environ.get('OPENBOOK_DETAIL_MAX_ERROR_RETRIES', 2))
OPENBOOK_ISBE_SEED_LIMIT = int(os.environ.get('OPENBOOK_ISBE_SEED_LIMIT', 5000))

# Federal Election Commission (FEC) sync
FEC_API_KEY = os.environ.get('FEC_API_KEY', '')

# Socrata / Chicago Open Data
SOCRATA_APP_NAME = os.environ.get('SOCRATA_APP_NAME', 'Illinois_campaignfinance').strip() or 'Illinois_campaignfinance'
SOCRATA_APP_TOKEN = os.environ.get('SOCRATA_APP_TOKEN', '').strip()
SOCRATA_APP_SECRET = os.environ.get('SOCRATA_APP_SECRET', '').strip()
SOCRATA_API_BASE_URL = os.environ.get('SOCRATA_API_BASE_URL', 'https://data.cityofchicago.org').strip().rstrip('/')
SOCRATA_API_PAGE_LIMIT = int(os.environ.get('SOCRATA_API_PAGE_LIMIT', 50000))
SOCRATA_API_TIMEOUT_SECONDS = int(os.environ.get('SOCRATA_API_TIMEOUT_SECONDS', 60))
SOCRATA_API_MAX_RETRIES = int(os.environ.get('SOCRATA_API_MAX_RETRIES', 5))
SOCRATA_API_MIN_INTERVAL_SECONDS = float(os.environ.get('SOCRATA_API_MIN_INTERVAL_SECONDS', 0.25))
