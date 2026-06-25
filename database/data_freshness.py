"""Data-freshness validation for the refresh pipeline.

Guards against the *silent no-op* failure mode where a refresh command reports
success but advances no data. The motivating incident (2026-06-24): a routine
``sync-fec-il-federal`` replayed the legacy payload cache (``api_calls_made=0``)
and left FEC Schedule A stuck at 2025-12-31 while reporting success — only the
``--refresh-cache`` flag forces a live pull.

Each check reads ``MAX(date_col)`` + ``COUNT(*)`` for a source table and flags
it when the latest row is older than a staleness threshold, or when the table
is empty/missing. Thresholds are deliberately *generous* — they exist to catch
a broken refresh (months-to-years stale), not to police normal filing/backfill
lag. Calibrated 2026-06-24 against freshly-refreshed prod data.

Signal choice matters:
- ISBE uses ``isbe_filed_docs.received_datetime`` (filing-receipt timestamp,
  ~days old after a fresh download). Transaction dates (``received_date``) are
  NOT used — they structurally lag ~4-5 months behind the bulk feed because
  D-2 reports are filed quarterly, so they false-positive on fresh data.
- FEC uses Schedule A/B/E transaction dates, which DO advance on a real pull.
  Schedule B/E get looser thresholds since the routine sync only refreshes
  Schedule A; B/E come from the separate ``backfill-fec-schedule-{b,e}`` jobs.

Used by the ``validate-freshness`` CLI command as a post-refresh gate.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime

STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_EMPTY = "empty"
STATUS_MISSING = "missing"


@dataclass(frozen=True)
class FreshnessSpec:
    """A single freshness check: latest ``date_column`` value in ``table``."""

    label: str
    source: str  # "isbe" | "fec" — lets the gate target the part that was refreshed
    table: str
    date_column: str
    max_age_days: int


# Generous thresholds tuned to catch broken/no-op refreshes (see module docstring).
DEFAULT_SPECS: tuple[FreshnessSpec, ...] = (
    FreshnessSpec("ISBE filings", "isbe", "isbe_filed_docs", "received_datetime", 14),
    FreshnessSpec("FEC Schedule A (contributions)", "fec", "fec_schedule_a_contributions", "contribution_receipt_date", 120),
    FreshnessSpec("FEC Schedule B (disbursements)", "fec", "fec_schedule_b_disbursements", "disbursement_date", 180),
    FreshnessSpec("FEC Schedule E (ind. expenditures)", "fec", "fec_schedule_e_independent_expenditures", "expenditure_date", 240),
)


@dataclass(frozen=True)
class FreshnessResult:
    spec: FreshnessSpec
    status: str
    row_count: int
    max_date: date | None
    age_days: int | None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_FRESH


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _coerce_date(value) -> date | None:
    """Normalize a MAX(date_col) result to a ``date``.

    Handles native DATE/TIMESTAMP columns (date/datetime objects) and FEC's
    TEXT columns holding ISO strings ("2025-12-31" or "2025-12-31T00:00:00").
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def check_table(conn: sqlite3.Connection, spec: FreshnessSpec, as_of: date) -> FreshnessResult:
    if not _table_exists(conn, spec.table):
        return FreshnessResult(spec, STATUS_MISSING, 0, None, None)

    # Table/column names come from the hardcoded specs above, not user input.
    row = conn.execute(
        f"SELECT COUNT(*), MAX({spec.date_column}) FROM {spec.table}"
    ).fetchone()
    count = int(row[0] or 0)
    if count == 0:
        return FreshnessResult(spec, STATUS_EMPTY, 0, None, None)

    max_date = _coerce_date(row[1])
    if max_date is None:
        # Rows exist but no parseable date — can't prove freshness, treat as stale.
        return FreshnessResult(spec, STATUS_STALE, count, None, None)

    age = (as_of - max_date).days
    status = STATUS_STALE if age > spec.max_age_days else STATUS_FRESH
    return FreshnessResult(spec, status, count, max_date, age)


def check_freshness(
    conn: sqlite3.Connection,
    specs: tuple[FreshnessSpec, ...] = DEFAULT_SPECS,
    as_of: date | None = None,
    source: str | None = None,
    max_staleness_days: int | None = None,
) -> list[FreshnessResult]:
    """Run freshness checks. ``source`` filters to "isbe"/"fec"; when set,
    ``max_staleness_days`` overrides every threshold."""
    as_of = as_of or date.today()
    selected = [s for s in specs if source in (None, s.source)]
    if max_staleness_days is not None:
        selected = [replace(s, max_age_days=max_staleness_days) for s in selected]
    return [check_table(conn, s, as_of) for s in selected]


def format_report(results: list[FreshnessResult], as_of: date | None = None) -> str:
    as_of = as_of or date.today()
    lines = [f"Data freshness (as of {as_of.isoformat()}):"]
    for r in results:
        if r.status == STATUS_FRESH:
            detail = f"{r.row_count:,} rows, latest {r.max_date} ({r.age_days}d)"
        elif r.status == STATUS_STALE:
            latest = r.max_date if r.max_date else "unparseable"
            age = f"{r.age_days}d > {r.spec.max_age_days}d" if r.age_days is not None else "no valid date"
            detail = f"{r.row_count:,} rows, latest {latest} ({age})"
        elif r.status == STATUS_EMPTY:
            detail = "table is EMPTY"
        else:  # missing
            detail = "table is MISSING"
        lines.append(f"  [{r.status.upper():<7}] {r.spec.label:<38} {detail}")
    return "\n".join(lines)
