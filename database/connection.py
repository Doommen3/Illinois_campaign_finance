"""Database connection management for Illinois Campaign Finance tracker."""
import sqlite3
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .pg_compat import PostgresCompatConnection


# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent / 'data' / 'campaign_finance.db'


def _is_postgres_dsn(value: str | None) -> bool:
    text = (value or '').strip()
    if not text:
        return False
    try:
        scheme = (urlparse(text).scheme or '').lower()
    except Exception:
        return False
    return scheme in {'postgres', 'postgresql'}


def _clean_env(value: str | None) -> str:
    return (value or '').strip().lower()


def _is_postgres_connection(conn: sqlite3.Connection) -> bool:
    return conn.__class__.__name__ == "PostgresCompatConnection"


_AUTOINCREMENT_PK_RE = re.compile(
    r"\b(?P<column>[A-Za-z_][A-Za-z0-9_]*)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
    re.IGNORECASE,
)


def _adapt_schema_for_postgres(schema: str) -> str:
    """Translate SQLite AUTOINCREMENT PK definitions to Postgres identity columns."""
    translated = _AUTOINCREMENT_PK_RE.sub(r"\g<column> BIGSERIAL PRIMARY KEY", schema)
    return re.sub(r"\bAUTOINCREMENT\b", "", translated, flags=re.IGNORECASE)


def _resolve_sqlite_tuning_profile() -> str:
    """Resolve SQLite tuning profile from environment.

    Profiles:
    - local: favor faster analytical queries on developer machines.
    - production: favor safer durability defaults.
    - off: disable additional tuning pragmas.
    """
    raw = _clean_env(os.environ.get('SQLITE_TUNING_PROFILE'))
    if raw in {'off', 'none', 'disabled'}:
        return 'off'
    if raw in {'local', 'dev', 'development'}:
        return 'local'
    if raw in {'prod', 'production'}:
        return 'production'

    app_env = _clean_env(os.environ.get('APP_ENV'))
    if app_env in {'prod', 'production', 'staging'}:
        return 'production'
    return 'local'


def _env_int(name: str, default: int) -> int:
    text = (os.environ.get(name) or '').strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _apply_sqlite_tuning(conn: sqlite3.Connection) -> None:
    """Apply SQLite pragmas based on runtime profile.

    Notes:
    - cache_size uses negative values to represent kibibytes.
    - mmap/cache values are intentionally conservative to stay safe across
      machines while still helping query-heavy workloads.
    """
    profile = _resolve_sqlite_tuning_profile()
    if profile == 'off':
        return

    if profile == 'production':
        cache_mb = max(16, _env_int('SQLITE_CACHE_MB', 64))
        mmap_mb = max(64, _env_int('SQLITE_MMAP_MB', 128))
        sync_mode = _clean_env(os.environ.get('SQLITE_SYNCHRONOUS', 'full')).upper()
        temp_store = _clean_env(os.environ.get('SQLITE_TEMP_STORE', 'file'))
    else:
        # Local default profile (optimized for modern developer hardware).
        cache_mb = max(32, _env_int('SQLITE_CACHE_MB', 256))
        mmap_mb = max(64, _env_int('SQLITE_MMAP_MB', 512))
        sync_mode = _clean_env(os.environ.get('SQLITE_SYNCHRONOUS', 'normal')).upper()
        temp_store = _clean_env(os.environ.get('SQLITE_TEMP_STORE', 'memory'))

    temp_store_map = {
        'default': 0,
        'file': 1,
        'memory': 2,
    }
    temp_store_value = temp_store_map.get(temp_store, temp_store_map['memory' if profile == 'local' else 'file'])

    cache_kib = cache_mb * 1024
    mmap_bytes = mmap_mb * 1024 * 1024

    conn.execute(f'PRAGMA cache_size = {-cache_kib}')
    conn.execute(f'PRAGMA mmap_size = {mmap_bytes}')
    conn.execute(f'PRAGMA temp_store = {temp_store_value}')
    conn.execute(f'PRAGMA synchronous = {sync_mode}')


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if the table exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """Return True if a column exists in a table."""
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    """Add a column to an existing table if missing."""
    if not _table_exists(conn, table_name):
        return
    if not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure schema and backward-compatible migrations are applied."""
    schema_path = Path(__file__).parent / 'schema.sql'
    with open(schema_path, 'r') as f:
        schema = f.read()
    if _is_postgres_connection(conn):
        schema = _adapt_schema_for_postgres(schema)
    # Backward-compatible column additions for existing installations.
    # This must happen before running schema.sql so CREATE INDEX statements
    # on these columns do not fail on older databases.
    _ensure_column(conn, 'committees', 'committee_id_sbe', 'committee_id_sbe INTEGER')
    _ensure_column(conn, 'committees', 'detail_url', 'detail_url TEXT')
    _ensure_column(conn, 'committees', 'source_identifier', 'source_identifier TEXT')
    _ensure_column(conn, 'reports', 'source_identifier', 'source_identifier TEXT')
    _ensure_column(conn, 'donors', 'occupation', 'occupation TEXT')
    _ensure_column(conn, 'donors', 'employer', 'employer TEXT')
    _ensure_column(conn, 'contributions', 'transaction_date', 'transaction_date TEXT')
    _ensure_column(conn, 'contributions', 'raw_occupation', 'raw_occupation TEXT')
    _ensure_column(conn, 'contributions', 'raw_employer', 'raw_employer TEXT')
    _ensure_column(conn, 'fec_schedule_a_contributions', 'donor_entity_key', 'donor_entity_key TEXT')
    _ensure_column(conn, 'fec_schedule_a_contributions', 'donor_entity_method', 'donor_entity_method TEXT')
    _ensure_column(conn, 'fec_candidate_cycle_totals', 'disbursements', 'disbursements REAL NOT NULL DEFAULT 0')
    _ensure_column(conn, 'analytics_donor_committee_agg', 'occupation', 'occupation TEXT')
    _ensure_column(conn, 'analytics_donor_committee_agg', 'employer', 'employer TEXT')
    _ensure_column(conn, 'analytics_materialized_meta', 'materialization_version', 'materialization_version INTEGER NOT NULL DEFAULT 1')
    _ensure_column(conn, 'analytics_materialized_meta', 'materialization_notes', 'materialization_notes TEXT')
    _ensure_column(conn, 'bulk_receipts_clean', 'received_datetime_raw', 'received_datetime_raw TEXT')
    _ensure_column(conn, 'lobbying_entities', 'address_1', 'address_1 TEXT')
    _ensure_column(conn, 'lobbying_entities', 'address_2', 'address_2 TEXT')
    _ensure_column(conn, 'lobbying_entities', 'city', 'city TEXT')
    _ensure_column(conn, 'lobbying_entities', 'state', 'state TEXT')
    _ensure_column(conn, 'lobbying_entities', 'postal_code', 'postal_code TEXT')
    _ensure_column(conn, 'lobbying_clients', 'address_1', 'address_1 TEXT')
    _ensure_column(conn, 'lobbying_clients', 'address_2', 'address_2 TEXT')
    _ensure_column(conn, 'lobbying_clients', 'city', 'city TEXT')
    _ensure_column(conn, 'lobbying_clients', 'state', 'state TEXT')
    _ensure_column(conn, 'lobbying_clients', 'postal_code', 'postal_code TEXT')
    _ensure_column(conn, 'lobbying_clients', 'status', 'status TEXT')
    # Bundle A: FEC Schedule A provenance
    _ensure_column(conn, 'fec_schedule_a_contributions', 'amendment_indicator', 'amendment_indicator TEXT')
    _ensure_column(conn, 'fec_schedule_a_contributions', 'file_number', 'file_number TEXT')
    _ensure_column(conn, 'fec_schedule_a_contributions', 'transaction_id', 'transaction_id TEXT')
    # Bundle B: FEC Schedule B provenance + purpose
    _ensure_column(conn, 'fec_schedule_b_disbursements', 'amendment_indicator', 'amendment_indicator TEXT')
    _ensure_column(conn, 'fec_schedule_b_disbursements', 'disbursement_purpose_category', 'disbursement_purpose_category TEXT')
    _ensure_column(conn, 'fec_schedule_b_disbursements', 'file_number', 'file_number TEXT')
    _ensure_column(conn, 'fec_schedule_b_disbursements', 'transaction_id', 'transaction_id TEXT')
    # Bundle C: FEC Schedule E notice + filing lineage
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'is_notice', 'is_notice INTEGER')
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'most_recent', 'most_recent INTEGER')
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'file_number', 'file_number TEXT')
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'previous_file_number', 'previous_file_number TEXT')
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'amendment_indicator', 'amendment_indicator TEXT')
    _ensure_column(conn, 'fec_schedule_e_independent_expenditures', 'transaction_id', 'transaction_id TEXT')
    # Bundle D: FEC Candidate Committee metadata
    _ensure_column(conn, 'fec_candidate_committees', 'last_file_date', 'last_file_date TEXT')
    _ensure_column(conn, 'fec_candidate_committees', 'first_file_date', 'first_file_date TEXT')
    _ensure_column(conn, 'fec_candidate_committees', 'party_full', 'party_full TEXT')

    conn.executescript(schema)

    conn.commit()


_get_db_override = None  # Set by test conftest to redirect all callers


def get_db(db_path: str = None) -> sqlite3.Connection:
    """Get a database connection.

    Args:
        db_path: Optional path to the database file. If not provided,
                 uses the default path in the data directory.

    Returns:
        A sqlite3 connection with row factory set to sqlite3.Row
    """
    if _get_db_override is not None:
        return _get_db_override(db_path)
    db_target = (db_path or '').strip() if isinstance(db_path, str) else db_path
    if not db_target:
        db_target = (os.environ.get('DATABASE_URL') or '').strip()
    if not db_target:
        db_target = os.environ.get('DATABASE_PATH', str(DEFAULT_DB_PATH))

    if _is_postgres_dsn(db_target):
        return PostgresCompatConnection(db_target)

    # Ensure the directory exists
    Path(db_target).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_target, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 60000')
    conn.execute('PRAGMA journal_mode = WAL')
    _apply_sqlite_tuning(conn)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 1000')
    try:
        ensure_schema(conn)
    except sqlite3.OperationalError as exc:
        # Allow read operations to proceed when another long-running writer
        # holds the migration lock; schema sync can be retried later.
        if "locked" in str(exc).lower():
            conn.rollback()
        else:
            raise
    finally:
        conn.execute('PRAGMA busy_timeout = 60000')
    return conn


def close_db(conn: sqlite3.Connection) -> None:
    """Close a database connection."""
    if conn:
        conn.close()


_init_db_override = None  # Set by test conftest to redirect all callers


def init_db(db_path: str = None) -> None:
    """Initialize the database with the schema.

    Args:
        db_path: Optional path to the database file.
    """
    if _init_db_override is not None:
        return _init_db_override(db_path)
    db_target = (db_path or '').strip() if isinstance(db_path, str) else db_path
    if not db_target:
        db_target = (os.environ.get('DATABASE_URL') or '').strip()
    if not db_target:
        db_target = os.environ.get('DATABASE_PATH', str(DEFAULT_DB_PATH))

    conn = get_db(db_target)
    try:
        ensure_schema(conn)
    finally:
        close_db(conn)
