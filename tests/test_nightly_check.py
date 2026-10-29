"""Tests for the 05:00 nightly refresh check (scripts/nightly_check.py) and the refresh shell scripts.

The check finds the nightly run whose start falls on the given America/Chicago date, stores its
diff when the run never finished, writes <report-dir>/<date>.md and sets check_status. Fixture logs
use the chain's own STEP<N>_START / STEP<N>_END lines (scripts/refresh_prod_chain.sh).
"""
import importlib.util
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from database.data_refresh import finish_run, start_run

REPO = Path(__file__).resolve().parent.parent
DAY = date(2026, 10, 29)
CHAIN_LOG_OK = """\
[2026-10-29 03:02:00] CHAIN_START host=fictional-box python=/srv/x/venv/bin/python3
[2026-10-29 03:02:00] STEP1_START snapshot_row_counts
[2026-10-29 03:02:30] STEP1_END baselines=12
[2026-10-29 03:02:30] STEP2_START download_isbe
[2026-10-29 03:12:30] STEP2_END download_isbe files=12
[2026-10-29 03:12:30] STEP4_START sunshine_import
[2026-10-29 03:42:30] STEP4_END sunshine_import
[2026-10-29 03:42:30] STEP7B_START warm_caches
[2026-10-29 03:45:30] STEP7B_END warm_caches
[2026-10-29 03:45:30] STEP8_SKIPPED restart_service (SKIP_RESTART=1)
[2026-10-29 03:45:30] CHAIN_END
"""
CHAIN_LOG_FAILED = """\
[2026-10-29 03:02:00] STEP1_START snapshot_row_counts
[2026-10-29 03:02:30] STEP1_END baselines=12
[2026-10-29 03:02:30] STEP4_START sunshine_import
[2026-10-29 03:20:00] STEP_FAILED sunshine_import (exit=4)
[2026-10-29 03:20:00] CHAIN_FAILED
"""


def _load_check():
    spec = importlib.util.spec_from_file_location("nightly_check", REPO / "scripts" / "nightly_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _conn(tmp_path):
    db_path = str(tmp_path / "check.db")
    init_db(db_path)
    return get_db(db_path)


def _fresh_sources(conn, a_days_old=10):
    """The nightly sources the freshness gate reads: ISBE filings (an ETL table, created here) and FEC A."""
    conn.execute("CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, received_datetime TIMESTAMP)")
    conn.execute(
        "INSERT INTO isbe_filed_docs (id, received_datetime) VALUES (?, ?)",
        (1, f"{(DAY - timedelta(days=2)).isoformat()} 09:00:00"),
    )
    conn.execute(
        "INSERT INTO fec_schedule_a_contributions (sub_id, cycle, contribution_receipt_date) VALUES (?, ?, ?)",
        ("a1", 2026, (DAY - timedelta(days=a_days_old)).isoformat()),
    )
    conn.commit()


def _run(conn, started_at, exit_code=None, log_path=None):
    """A nightly run started at ``started_at`` (a timestamptz literal); finished unless exit_code is None."""
    run_id = start_run(conn, kind="nightly", log_path=log_path)
    if exit_code is not None:
        finish_run(conn, run_id, exit_code=exit_code)
    conn.execute("UPDATE data_refresh_runs SET started_at = CAST(? AS TIMESTAMPTZ) WHERE id = ?", (started_at, run_id))
    conn.commit()
    return run_id


def _row(conn, run_id):
    return conn.execute("SELECT * FROM data_refresh_runs WHERE id = ?", (run_id,)).fetchone()


def _check(tmp_path, day=DAY):
    report_dir = tmp_path / "refresh_runs"
    code = _load_check().main(["--report-dir", str(report_dir), "--date", day.isoformat()])
    return code, report_dir / f"{day.isoformat()}.md"


def test_parse_chain_log_steps_and_durations():
    steps = _load_check().parse_chain_log(CHAIN_LOG_OK)
    by = {s["step"]: s for s in steps}
    assert [s["step"] for s in steps] == ["1", "2", "4", "7B", "8"]
    assert by["1"]["name"] == "snapshot_row_counts" and by["1"]["status"] == "ok" and by["1"]["minutes"] == 0.5
    assert by["4"]["minutes"] == 30.0
    assert by["8"]["status"] == "skipped" and by["8"]["minutes"] is None

    failed = {s["step"]: s for s in _load_check().parse_chain_log(CHAIN_LOG_FAILED)}
    assert failed["4"]["status"] == "failed" and failed["4"]["minutes"] == 17.5


def test_no_run_today_exits_1_and_leaves_runs_unchecked(tmp_path):
    conn = _conn(tmp_path)
    try:
        yesterday = _run(conn, "2026-10-28 03:01:00-05", exit_code=0)
        code, report = _check(tmp_path)
        assert code == 1
        assert _row(conn, yesterday)["check_status"] is None
    finally:
        conn.close()
    assert "MISSING" in report.read_text()


def test_failed_run_writes_report_and_exits_1(tmp_path):
    log = tmp_path / "nightly-fixture.log"
    log.write_text(CHAIN_LOG_FAILED)
    conn = _conn(tmp_path)
    try:
        _fresh_sources(conn)
        run_id = _run(conn, "2026-10-29 03:01:00-05", exit_code=4, log_path=str(log))
        code, report = _check(tmp_path)
        row = _row(conn, run_id)
    finally:
        conn.close()
    assert code == 1
    assert row["check_status"] == "failed" and row["checked_at"] is not None
    text = report.read_text()
    assert "FAILED" in text and "sunshine_import" in text and str(log) in text


def test_ok_run_writes_diff_and_exits_0(tmp_path):
    log = tmp_path / "nightly-fixture.log"
    log.write_text(CHAIN_LOG_OK)
    conn = _conn(tmp_path)
    try:
        conn.execute("CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, received_datetime TIMESTAMP)")
        conn.commit()
        run_id = start_run(conn, kind="nightly", log_path=str(log))
        # What the chain loads between the two snapshots.
        conn.execute("DROP TABLE isbe_filed_docs")
        conn.commit()
        _fresh_sources(conn)
        finish_run(conn, run_id, exit_code=0)
        conn.execute("UPDATE data_refresh_runs SET started_at = CAST(? AS TIMESTAMPTZ) WHERE id = ?",
                     ("2026-10-29 03:01:00-05", run_id))
        conn.commit()
        code, report = _check(tmp_path)
        row = _row(conn, run_id)
    finally:
        conn.close()
    assert code == 0
    assert row["check_status"] == "ok"
    text = report.read_text()
    assert "OK" in text
    assert "fec_schedule_a_contributions" in text and "2026-10-19" in text  # new latest date in the diff
    assert "sunshine_import" in text and "30.0" in text  # step duration from the log
    assert "FEC Schedule B" in text  # the dataset status table lists the manual and semi datasets too


def test_run_late_evening_utc_counts_for_the_chicago_day(tmp_path):
    # 2026-10-30 03:30 UTC is 2026-10-29 22:30 in Chicago (CDT, UTC-5).
    conn = _conn(tmp_path)
    try:
        _fresh_sources(conn)
        run_id = _run(conn, "2026-10-30 03:30:00+00", exit_code=0)
        code, _report = _check(tmp_path)
        assert code == 0 and _row(conn, run_id)["check_status"] == "ok"
    finally:
        conn.close()


def test_ok_run_with_stale_nightly_source_fails(tmp_path):
    # The silent no-op: the chain exits 0 but FEC Schedule A did not advance past its threshold.
    conn = _conn(tmp_path)
    try:
        _fresh_sources(conn, a_days_old=200)
        run_id = _run(conn, "2026-10-29 03:01:00-05", exit_code=0)
        code, report = _check(tmp_path)
        row = _row(conn, run_id)
    finally:
        conn.close()
    assert code == 1 and row["check_status"] == "failed"
    assert "STALE" in report.read_text()


def test_unfinished_run_gets_its_diff_and_fails(tmp_path):
    conn = _conn(tmp_path)
    try:
        _fresh_sources(conn)
        run_id = _run(conn, "2026-10-29 03:01:00-05", exit_code=None)  # killed or still running
        code, report = _check(tmp_path)
        row = _row(conn, run_id)
    finally:
        conn.close()
    assert code == 1
    assert row["status"] == "running" and row["check_status"] == "failed"
    assert row["diff"] is not None and "fec_schedule_a_contributions" in row["diff"]
    assert "running" in report.read_text()


@pytest.mark.parametrize("script", ["nightly_refresh.sh", "manual_load.sh", "refresh_prod_chain.sh"])
def test_shell_scripts_parse(script):
    result = subprocess.run(["bash", "-n", str(REPO / "scripts" / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_chain_order_swap_with_service_stopped_and_freshness_gate_last():
    """refresh_prod_chain.sh: the compat swap runs with ilcf-web stopped (it deadlocks against live
    readers, 2026-10-27), the FEC sync forces a live pull, and validate-freshness gates the end."""
    chain = (REPO / "scripts" / "refresh_prod_chain.sh").read_text()

    def at(needle):
        assert chain.count(needle) == 1, needle
        return chain.index(needle)

    import_at = at("run.py sunshine-import --bulk-dir Bulk_download --skip-compat-swap")
    stop_at = at("systemctl stop ilcf-web.service")
    swap_at = at("scripts/swap_bulk_to_isbe.py")
    start_at = at("systemctl start ilcf-web.service")
    fec_at = at("run.py sync-fec-il-federal --refresh-cache")
    assert import_at < stop_at < swap_at < start_at < fec_at
    assert at("scripts/fec-schedule-b-catchup.sh") > fec_at  # weekly Schedule B backfill
    analytics_at = at("run.py refresh-analytics --with-snapshot")
    restart_at = at("systemctl restart ilcf-web.service")
    assert at("run.py validate-freshness") > max(analytics_at, restart_at)
