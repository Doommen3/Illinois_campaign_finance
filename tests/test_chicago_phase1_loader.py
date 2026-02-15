"""Tests for Chicago Phase 1 Socrata ingestion."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from database.connection import get_db, init_db
from webapp.app import create_app
from cli.commands import cli

from database.chicago_loader import (
    _decode_json_payload,
    import_chicago_phase1,
)


class _StubFetcher:
    def __init__(self, payload_by_dataset: dict[str, list[dict]]):
        self.payload_by_dataset = payload_by_dataset
        self.calls: list[tuple[str, int | None, int]] = []

    def __call__(self, dataset_id: str, row_limit: int | None, page_limit: int):
        self.calls.append((dataset_id, row_limit, page_limit))
        rows = self.payload_by_dataset.get(dataset_id, [])
        if row_limit is None:
            return list(rows)
        return list(rows[:row_limit])


def _init_conn(tmp_path: Path):
    db_path = str(tmp_path / "test_chicago_phase1.db")
    init_db(db_path)
    return get_db(db_path)


def test_decode_json_payload_handles_bom():
    payload = b'\xef\xbb\xbf{"results": [{"ok": true}]}'
    parsed = _decode_json_payload(payload)
    assert parsed["results"][0]["ok"] is True


def test_decode_json_payload_rejects_malformed_json():
    with pytest.raises(ValueError):
        _decode_json_payload(b"not-json")


def test_import_chicago_phase1_handles_empty_data(tmp_path: Path):
    conn = _init_conn(tmp_path)
    fetcher = _StubFetcher({})

    stats = import_chicago_phase1(
        conn,
        app_token="token",
        full_refresh=True,
        row_limit=100,
        page_limit=1000,
        fetcher=fetcher,
    )

    assert stats["contracts_rows"] == 0
    assert stats["payments_rows"] == 0
    assert stats["lobbyist_contributions_rows"] == 0
    assert stats["lobbying_activity_rows"] == 0

    contracts_count = conn.execute("SELECT COUNT(*) AS c FROM chicago_contracts_raw").fetchone()["c"]
    assert contracts_count == 0
    conn.close()


def test_import_chicago_phase1_parses_and_persists_rows(tmp_path: Path):
    conn = _init_conn(tmp_path)
    fetcher = _StubFetcher(
        {
            "rsxa-ify5": [
                {
                    "socrata_row_id": "1",
                    "purchase_order_contract_number": "C-100",
                    "vendor_id": "VEND-1",
                    "vendor_name": "Vendor One LLC",
                    "approval_date": "2026-02-01T00:00:00.000",
                    "award_amount": "1500.25",
                    "department": "DEPT A",
                    "contract_type": "COMMODITIES",
                    "procurement_type": "BID",
                }
            ],
            "s4vu-giwb": [
                {
                    "socrata_row_id": "2",
                    "voucher_number": "PV123",
                    "contract_number": "C-100",
                    "vendor_name": "Vendor One LLC",
                    "amount": "100.50",
                    "check_date": "02/12/2026",
                    "department_name": "DEPT A",
                }
            ],
            "p9p7-vfqc": [
                {
                    "socrata_row_id": "3",
                    "contribution_id": "10",
                    "contribution_date": "2026-02-03T00:00:00.000",
                    "recipient": "Committee Example",
                    "amount": "500",
                    "lobbyist_id": "7001",
                    "lobbyist_first_name": "Alex",
                    "lobbyist_last_name": "Smith",
                    "period_start": "2026-01-01T00:00:00.000",
                    "period_end": "2026-03-31T00:00:00.000",
                }
            ],
            "pahz-egmi": [
                {
                    "socrata_row_id": "4",
                    "lobbying_activity_id": "44",
                    "period_start": "2026-01-01T00:00:00.000",
                    "period_end": "2026-03-31T00:00:00.000",
                    "action": "ADMINISTRATIVE",
                    "action_sought": "PROCUREMENT",
                    "department": "PROCUREMENT SERVICES",
                    "client_id": "888",
                    "client_name": "Client Corp",
                    "lobbyist_id": "7001",
                    "lobbyist_first_name": "Alex",
                    "lobbyist_last_name": "Smith",
                }
            ],
        }
    )

    stats = import_chicago_phase1(
        conn,
        app_token="token",
        full_refresh=True,
        row_limit=None,
        page_limit=50000,
        fetcher=fetcher,
    )

    assert stats["contracts_rows"] == 1
    assert stats["payments_rows"] == 1
    assert stats["lobbyist_contributions_rows"] == 1
    assert stats["lobbying_activity_rows"] == 1

    contract = conn.execute(
        "SELECT purchase_order_contract_number, vendor_name, award_amount FROM chicago_contracts_raw WHERE socrata_row_id = '1'"
    ).fetchone()
    assert contract["purchase_order_contract_number"] == "C-100"
    assert contract["vendor_name"] == "Vendor One LLC"
    assert contract["award_amount"] == 1500.25

    contribution = conn.execute(
        "SELECT amount, lobbyist_id, recipient FROM chicago_lobbyist_contributions_raw WHERE socrata_row_id = '3'"
    ).fetchone()
    assert contribution["amount"] == 500.0
    assert contribution["lobbyist_id"] == 7001
    assert contribution["recipient"] == "Committee Example"

    conn.close()


def test_import_chicago_phase1_malformed_numeric_is_tolerated(tmp_path: Path):
    conn = _init_conn(tmp_path)
    fetcher = _StubFetcher(
        {
            "rsxa-ify5": [
                {
                    "socrata_row_id": "1",
                    "purchase_order_contract_number": "C-200",
                    "vendor_name": "Vendor Two",
                    "award_amount": "not-a-number",
                }
            ]
        }
    )

    stats = import_chicago_phase1(
        conn,
        app_token="token",
        full_refresh=True,
        row_limit=None,
        page_limit=50000,
        fetcher=fetcher,
    )

    assert stats["contracts_rows"] == 1
    row = conn.execute(
        "SELECT award_amount FROM chicago_contracts_raw WHERE socrata_row_id = '1'"
    ).fetchone()
    assert row["award_amount"] is None
    conn.close()


def test_import_chicago_phase1_preserves_ui_rendering(tmp_path: Path):
    db_path = str(tmp_path / "test_ui_phase1.db")
    init_db(db_path)
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert b"stylesheet" in response.data
    assert b"Dashboard" in response.data


def test_import_chicago_phase1_cli_command_invokes_loader(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "cli_phase1.db")
    init_db(db_path)

    called: dict[str, object] = {}

    def _fake_import(conn, **kwargs):
        called.update(kwargs)
        return {
            "mode": "full_refresh",
            "contracts_rows": 1,
            "payments_rows": 2,
            "lobbyist_contributions_rows": 3,
            "lobbying_activity_rows": 4,
        }

    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setattr("cli.commands.import_chicago_phase1", _fake_import)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "import-chicago-phase1",
            "--app-token",
            "test-token",
            "--row-limit",
            "25",
            "--page-limit",
            "5000",
            "--upsert",
        ],
    )

    assert result.exit_code == 0
    assert "Chicago phase1 import completed" in result.output
    assert called["row_limit"] == 25
    assert called["page_limit"] == 5000
    assert called["full_refresh"] is False
