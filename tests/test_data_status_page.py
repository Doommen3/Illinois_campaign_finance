"""Tests for the public /data-status page (webapp/routes/data_status.py) and its Data Status strip link."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from database.data_refresh import DATASETS, finish_run, record_check, start_run
from webapp.app import create_app


@pytest.fixture
def db_path(tmp_path: Path):
    path = str(tmp_path / "test_data_status_page.db")
    init_db(path)
    return path


@pytest.fixture
def client(db_path):
    return create_app({"TESTING": True, "DATABASE_PATH": db_path}).test_client()


def _insert_sched_a(conn, sub_id, date_str):
    conn.execute(
        "INSERT INTO fec_schedule_a_contributions (sub_id, cycle, contribution_receipt_date) VALUES (?, ?, ?)",
        (sub_id, 2026, date_str),
    )
    conn.commit()


def test_page_lists_every_dataset_and_a_failed_run(client, db_path):
    conn = get_db(db_path)
    try:
        ok_id = start_run(conn)
        _insert_sched_a(conn, "a1", "2026-10-20")
        finish_run(conn, ok_id, exit_code=0)
        record_check(conn, ok_id, "ok")
        bad_id = start_run(conn)
        finish_run(conn, bad_id, exit_code=4)
        record_check(conn, bad_id, "failed", notes="the run failed (exit code 4)")
    finally:
        conn.close()

    response = client.get("/data-status")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for dataset in DATASETS:
        assert dataset.label in body
    assert "By hand" in body and "Automated" in body
    assert f'id="run-{bad_id}"' in body and f'id="run-{ok_id}"' in body
    bad_row = body.split(f'id="run-{bad_id}"')[1].split("</tr>")[0]
    assert "failed" in bad_row and "exit code 4" in bad_row
    ok_row = body.split(f'id="run-{ok_id}"')[1].split("</tr>")[0]
    assert "FEC Schedule A (contributions) +1" in ok_row and "2026-10-20" in ok_row  # diff summary


def test_page_renders_without_runs_or_run_tables(client, db_path):
    assert "No refresh runs recorded yet" in client.get("/data-status").get_data(as_text=True)
    conn = get_db(db_path)
    try:
        conn.execute("DROP TABLE data_refresh_runs")
        conn.execute("DROP TABLE data_manual_loads")
        conn.commit()
    finally:
        conn.close()
    response = client.get("/data-status")
    assert response.status_code == 200
    assert "No refresh runs recorded yet" in response.get_data(as_text=True)


def test_strip_links_to_data_status(client, db_path):
    conn = get_db(db_path)
    try:
        _insert_sched_a(conn, "a1", "2026-10-20")  # the strip renders only when some source has data
    finally:
        conn.close()
    body = client.get("/").get_data(as_text=True)
    assert "data-freshness-strip" in body
    assert 'href="/data-status"' in body
