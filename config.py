"""Configuration for Illinois Campaign Finance Tracker."""
import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent

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
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
PUBLIC_CONTACT_EMAIL = os.environ.get('PUBLIC_CONTACT_EMAIL', '').strip()

# Scraping
DEFAULT_START_PAGE = 1
DEFAULT_END_PAGE = 40
DEFAULT_BATCH_SIZE = 20

# Federal Election Commission (FEC) sync
FEC_API_KEY = os.environ.get('FEC_API_KEY', '')
