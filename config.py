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


# Database
DATABASE_PATH = os.environ.get(
    'DATABASE_PATH',
    str(BASE_DIR / 'data' / 'campaign_finance.db')
)

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

# API
API_KEYS = _env_list('API_KEYS')
API_REQUIRE_KEY = _env_bool('API_REQUIRE_KEY', default=bool(API_KEYS))
API_RATE_LIMIT_PER_MINUTE = int(os.environ.get('API_RATE_LIMIT_PER_MINUTE', 120))

# Data freshness display
LOCAL_DATA_STALE_DAYS = int(os.environ.get('LOCAL_DATA_STALE_DAYS', 45))
FEDERAL_DATA_STALE_DAYS = int(os.environ.get('FEDERAL_DATA_STALE_DAYS', 14))

# Scraping
DEFAULT_START_PAGE = 1
DEFAULT_END_PAGE = 40
DEFAULT_BATCH_SIZE = 20

# Federal Election Commission (FEC) sync
FEC_API_KEY = os.environ.get('FEC_API_KEY', '')
