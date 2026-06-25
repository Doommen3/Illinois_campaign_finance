"""Tests for the data-freshness refresh gate (database/data_freshness.py).

Plain SQL only (COUNT/MAX/INSERT) — runs in CI without the `integration`
marker. FEC tables come from schema.sql (present in the test schema); the ISBE
table is ETL-created (absent in CI) which exercises the missing-table path.
"""
from datetime import date, datetime, timedelta

from database.connection import get_db, init_db
from database.data_freshness import (
    DEFAULT_SPECS,
    STATUS_EMPTY,
    STATUS_FRESH,
    STATUS_MISSING,
    STATUS_STALE,
    _coerce_date,
    check_freshness,
    check_table,
)

AS_OF = date(2026, 6, 24)
SCHED_A = next(s for s in DEFAULT_SPECS if s.table == "fec_schedule_a_contributions")
ISBE = next(s for s in DEFAULT_SPECS if s.source == "isbe")


def _conn(tmp_path):
    db_path = str(tmp_path / "freshness.db")
    init_db(db_path)
    return get_db(db_path)


def _insert_sched_a(conn, sub_id, date_str):
    conn.execute(
        "INSERT INTO fec_schedule_a_contributions (sub_id, cycle, contribution_receipt_date) "
        "VALUES (?, ?, ?)",
        (sub_id, 2026, date_str),
    )


def test_coerce_date_text_and_objects():
    # FEC TEXT columns (both ISO forms) + native date/datetime objects.
    assert _coerce_date("2025-12-31") == date(2025, 12, 31)
    assert _coerce_date("2025-12-31T00:00:00") == date(2025, 12, 31)
    assert _coerce_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert _coerce_date(datetime(2026, 1, 2, 9, 30)) == date(2026, 1, 2)
    assert _coerce_date(None) is None
    assert _coerce_date("") is None
    assert _coerce_date("garbage") is None


def test_fresh_data_passes(tmp_path):
    conn = _conn(tmp_path)
    _insert_sched_a(conn, "a1", (AS_OF - timedelta(days=10)).isoformat())
    conn.commit()
    res = check_table(conn, SCHED_A, AS_OF)
    conn.close()
    assert res.status == STATUS_FRESH
    assert res.ok
    assert res.row_count == 1
    assert res.age_days == 10


def test_stale_data_flagged(tmp_path):
    conn = _conn(tmp_path)
    _insert_sched_a(conn, "a1", (AS_OF - timedelta(days=SCHED_A.max_age_days + 30)).isoformat())
    conn.commit()
    res = check_table(conn, SCHED_A, AS_OF)
    conn.close()
    assert res.status == STATUS_STALE
    assert not res.ok


def test_empty_table_flagged(tmp_path):
    conn = _conn(tmp_path)
    res = check_table(conn, SCHED_A, AS_OF)
    conn.close()
    assert res.status == STATUS_EMPTY
    assert not res.ok
    assert res.row_count == 0


def test_missing_table_flagged(tmp_path):
    # isbe_filed_docs is created by the ETL, not schema.sql → absent in CI schema.
    conn = _conn(tmp_path)
    res = check_table(conn, ISBE, AS_OF)
    conn.close()
    assert res.status == STATUS_MISSING
    assert not res.ok


def test_no_op_refresh_regression(tmp_path):
    """Reproduces 2026-06-24: a FEC cache-replay sync left Schedule A stuck at
    2025-12-31 while reporting success. The gate must flag it as stale."""
    conn = _conn(tmp_path)
    for i in range(3):
        _insert_sched_a(conn, f"stuck-{i}", "2025-12-31")
    conn.commit()
    res = check_table(conn, SCHED_A, AS_OF)
    conn.close()
    assert res.status == STATUS_STALE
    assert res.max_date == date(2025, 12, 31)
    assert res.age_days == 175
    assert not res.ok


def test_source_filter(tmp_path):
    conn = _conn(tmp_path)
    fec_only = check_freshness(conn, DEFAULT_SPECS, as_of=AS_OF, source="fec")
    isbe_only = check_freshness(conn, DEFAULT_SPECS, as_of=AS_OF, source="isbe")
    conn.close()
    assert {r.spec.source for r in fec_only} == {"fec"}
    assert {r.spec.source for r in isbe_only} == {"isbe"}
    assert len(fec_only) + len(isbe_only) == len(DEFAULT_SPECS)


def test_max_staleness_override(tmp_path):
    conn = _conn(tmp_path)
    _insert_sched_a(conn, "a1", (AS_OF - timedelta(days=10)).isoformat())
    conn.commit()
    # 10-day-old row passes the default (120d) but fails a 5d override.
    results = check_freshness(conn, (SCHED_A,), as_of=AS_OF, max_staleness_days=5)
    conn.close()
    assert results[0].status == STATUS_STALE
