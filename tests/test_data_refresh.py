"""Tests for the scheduled-refresh run record and data status (database/data_refresh.py).

Plain SQL only. Source tables come from schema.sql; the ISBE tables are ETL-created and absent in
the test schema, which exercises the missing-table path (one test creates a minimal isbe_filed_docs).
"""
from datetime import date, timedelta

import pytest

from database.connection import get_db, init_db
from database.data_refresh import (
    DATASETS,
    dataset_status,
    diff_snapshots,
    finish_run,
    record_check,
    record_manual_load,
    snapshot_counts,
    start_run,
)

AS_OF = date(2026, 10, 29)


def _conn(tmp_path):
    db_path = str(tmp_path / "refresh.db")
    init_db(db_path)
    return get_db(db_path)


def _days_ago(n):
    return (AS_OF - timedelta(days=n)).isoformat()


def _insert_sched_a(conn, sub_id, date_str):
    conn.execute(
        "INSERT INTO fec_schedule_a_contributions (sub_id, cycle, contribution_receipt_date) VALUES (?, ?, ?)",
        (sub_id, 2026, date_str),
    )


def _run_row(conn, run_id):
    return conn.execute("SELECT * FROM data_refresh_runs WHERE id = ?", (run_id,)).fetchone()


def _by_key(rows):
    return {r["key"]: r for r in rows}


def test_datasets_match_the_plan_and_the_schema(tmp_path):
    keys = [d.key for d in DATASETS]
    assert keys == ["isbe", "fec_a", "fec_b", "fec_e", "chicago", "lobbying", "irs527", "openbook"]
    automation = {d.key: d.automation for d in DATASETS}
    assert automation == {
        "isbe": "auto", "fec_a": "auto", "fec_b": "auto", "fec_e": "auto", "chicago": "auto",
        "lobbying": "manual", "irs527": "manual", "openbook": "manual",
    }
    for d in DATASETS:
        assert d.label and d.cadence and d.loader and d.tables
        if d.automation == "manual":
            assert d.folder == f"data/manual/{d.key}"
    irs527 = next(d for d in DATASETS if d.key == "irs527")
    assert "loader fix" in irs527.notes  # import blocked on loader idempotency (P-3, in the runbook only)
    # Notes are public on /data-status: no internal paths, runbook items or commands.
    for d in DATASETS:
        for internal in ("docs/", "scripts/", ".md", "P-3", "run.py"):
            assert internal not in (d.notes or ""), (d.key, internal)
    # Every non-ISBE source table named in DATASETS exists in schema.sql (ISBE tables come from the ETL).
    conn = _conn(tmp_path)
    try:
        for d in DATASETS:
            for table, _expr in d.tables:
                if table.startswith("isbe_"):
                    continue
                found = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?",
                    (table,),
                ).fetchone()
                assert found, f"{d.key}: {table} not in schema.sql"
    finally:
        conn.close()


def test_snapshot_counts_rows_and_max_dates(tmp_path):
    conn = _conn(tmp_path)
    try:
        _insert_sched_a(conn, "a1", "2026-09-01")
        _insert_sched_a(conn, "a2", "2026-10-15")
        # Compact text dates plus a malformed one: a lexicographic MAX would pick "995".
        for form_id, period_end in ((1, "20250630"), (2, "20251231"), (3, "995")):
            conn.execute(
                "INSERT INTO irs527_reports (form_id, ein, period_end) VALUES (?, ?, ?)",
                (form_id, "00-0000001", period_end),
            )
        conn.commit()
        snap = snapshot_counts(conn)
    finally:
        conn.close()
    tables = {t for d in DATASETS for t, _ in d.tables}
    assert set(snap) == tables
    assert snap["fec_schedule_a_contributions"] == {"rows": 2, "max_date": "2026-10-15"}
    assert snap["irs527_reports"] == {"rows": 3, "max_date": "2025-12-31"}
    assert snap["fec_schedule_b_disbursements"] == {"rows": 0, "max_date": None}
    assert snap["isbe_filed_docs"] == {"rows": None, "max_date": None}  # ETL table absent


def test_diff_snapshots():
    before = {
        "t_grow": {"rows": 10, "max_date": "2026-10-01"},
        "t_same": {"rows": 5, "max_date": "2026-09-01"},
        "t_new": {"rows": None, "max_date": None},
        "t_reload": {"rows": 100, "max_date": "2026-10-02"},
    }
    after = {
        "t_grow": {"rows": 13, "max_date": "2026-10-20"},
        "t_same": {"rows": 5, "max_date": "2026-09-01"},
        "t_new": {"rows": 4, "max_date": "2026-10-28"},
        "t_reload": {"rows": 98, "max_date": "2026-10-02"},
    }
    diff = diff_snapshots(before, after)
    assert diff["t_grow"] == {
        "rows_before": 10, "rows_after": 13, "rows_added": 3,
        "max_date_before": "2026-10-01", "max_date_after": "2026-10-20", "new_max_date": True,
    }
    assert diff["t_same"]["rows_added"] == 0 and diff["t_same"]["new_max_date"] is False
    assert diff["t_new"]["rows_added"] == 4 and diff["t_new"]["new_max_date"] is True
    assert diff["t_reload"]["rows_added"] == -2  # drop-and-reload can shrink a table
    # A table present on only one side still appears.
    assert diff_snapshots({}, {"t_x": {"rows": 1, "max_date": None}})["t_x"]["rows_added"] == 1


def test_run_lifecycle_start_finish_check(tmp_path):
    conn = _conn(tmp_path)
    try:
        _insert_sched_a(conn, "a1", "2026-09-01")
        conn.commit()
        run_id = start_run(conn, kind="nightly", log_path="/srv/x/shared/logs/nightly-1.log")
        row = _run_row(conn, run_id)
        assert row["status"] == "running" and row["kind"] == "nightly"
        assert row["finished_at"] is None and row["check_status"] is None
        assert row["log_path"] == "/srv/x/shared/logs/nightly-1.log"
        assert row["snapshot_before"]["fec_schedule_a_contributions"]["rows"] == 1

        _insert_sched_a(conn, "a2", "2026-10-20")
        conn.commit()
        finish_run(conn, run_id, exit_code=0)
        row = _run_row(conn, run_id)
        assert row["status"] == "ok" and row["exit_code"] == 0 and row["finished_at"] is not None
        assert row["snapshot_after"]["fec_schedule_a_contributions"]["rows"] == 2
        a = row["diff"]["fec_schedule_a_contributions"]
        assert a["rows_added"] == 1 and a["max_date_after"] == "2026-10-20" and a["new_max_date"] is True

        record_check(conn, run_id, "ok")
        row = _run_row(conn, run_id)
        assert row["check_status"] == "ok" and row["checked_at"] is not None

        failed_id = start_run(conn, kind="manual")
        finish_run(conn, failed_id, exit_code=3)
        row = _run_row(conn, failed_id)
        assert row["status"] == "failed" and row["exit_code"] == 3 and row["kind"] == "manual"
        record_check(conn, failed_id, "failed", notes="chain exited 3")
        row = _run_row(conn, failed_id)
        assert row["check_status"] == "failed" and row["notes"] == "chain exited 3"
    finally:
        conn.close()


def test_record_manual_load(tmp_path):
    conn = _conn(tmp_path)
    try:
        load_id = record_manual_load(
            conn, "lobbying", "data/manual/lobbying/extract_fictional.csv",
            file_date=date(2026, 10, 20), rows_loaded=1234, notes="fixture",
        )
        row = conn.execute("SELECT * FROM data_manual_loads WHERE id = ?", (load_id,)).fetchone()
        assert row["dataset_key"] == "lobbying" and row["rows_loaded"] == 1234
        assert row["file_date"] == date(2026, 10, 20) and row["loaded_at"] is not None
        with pytest.raises(ValueError):
            record_manual_load(conn, "not_a_dataset", "x.csv")
    finally:
        conn.close()


def test_dataset_status_ages_and_stale_flags(tmp_path):
    conn = _conn(tmp_path)
    try:
        _insert_sched_a(conn, "a1", _days_ago(10))
        conn.execute(
            "INSERT INTO fec_schedule_b_disbursements (sub_id, cycle, disbursement_date) VALUES (?, ?, ?)",
            ("b1", 2026, _days_ago(400)),
        )
        conn.execute(
            "INSERT INTO irs527_reports (form_id, ein, period_end) VALUES (?, ?, ?)",
            (1, "00-0000001", (AS_OF - timedelta(days=30)).strftime("%Y%m%d")),
        )
        conn.commit()
        # Nightly runs: an ok one, then a later failed one. ISBE's last load is the ok one.
        ok_id = start_run(conn)
        finish_run(conn, ok_id, exit_code=0)
        bad_id = start_run(conn)
        finish_run(conn, bad_id, exit_code=1)
        record_manual_load(conn, "lobbying", "data/manual/lobbying/extract_fictional.csv")
        ok_finished = _run_row(conn, ok_id)["finished_at"]

        rows = dataset_status(conn, as_of=AS_OF)
    finally:
        conn.close()

    assert [r["key"] for r in rows] == [d.key for d in DATASETS]
    by = _by_key(rows)

    assert by["fec_a"]["last_data_date"] == AS_OF - timedelta(days=10)
    assert by["fec_a"]["age_days"] == 10 and by["fec_a"]["stale"] is False
    assert by["fec_b"]["age_days"] == 400 and by["fec_b"]["stale"] is True  # threshold 180
    assert by["fec_e"]["present"] is True and by["fec_e"]["last_data_date"] is None
    assert by["fec_e"]["stale"] is True  # empty table with a threshold is not fresh

    assert by["isbe"]["present"] is False and by["isbe"]["last_data_date"] is None
    assert by["isbe"]["stale"] is True  # missing table with a threshold
    assert by["isbe"]["last_load"] == ok_finished

    assert by["irs527"]["age_days"] == 30 and by["irs527"]["stale"] is None  # no threshold for manual sets
    assert by["lobbying"]["last_data_date"] is None and by["lobbying"]["last_load"] is not None
    assert by["lobbying"]["age_days"] == (AS_OF - by["lobbying"]["last_load"].date()).days
    assert by["openbook"]["age_days"] is None and by["openbook"]["last_load"] is None


def test_dataset_status_with_present_isbe_table(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute("CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, received_datetime TIMESTAMP)")
        conn.execute(
            "INSERT INTO isbe_filed_docs (id, received_datetime) VALUES (?, ?)",
            (1, f"{_days_ago(3)} 08:00:00"),
        )
        conn.commit()
        isbe = _by_key(dataset_status(conn, as_of=AS_OF))["isbe"]
    finally:
        conn.close()
    assert isbe["present"] is True and isbe["age_days"] == 3 and isbe["stale"] is False


def test_dataset_status_tolerates_missing_run_tables(tmp_path):
    # A database where init-db has not yet created the two new tables still renders a status.
    conn = _conn(tmp_path)
    try:
        conn.execute("DROP TABLE data_manual_loads")
        conn.execute("DROP TABLE data_refresh_runs")
        conn.commit()
        rows = dataset_status(conn, as_of=AS_OF)
    finally:
        conn.close()
    assert len(rows) == len(DATASETS)
    assert all(r["last_load"] is None for r in rows)
