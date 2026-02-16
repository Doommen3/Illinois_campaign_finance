"""Tests for experimental visualization lab routes."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from webapp.app import create_app


def _seed_state_race_tables(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS bulk_committees_clean")
    conn.execute(
        """
        CREATE TABLE bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_name TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name) VALUES (?, ?)",
        (9001, "Committee 9001"),
    )

    conn.execute("DROP TABLE IF EXISTS bulk_d2_totals_clean")
    conn.execute(
        """
        CREATE TABLE bulk_d2_totals_clean (
            filed_doc_id INTEGER,
            is_archived INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO bulk_d2_totals_clean (filed_doc_id, is_archived) VALUES (?, ?)",
        [(7001, 0), (7002, 0)],
    )

    conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT,
            election_cycle INTEGER,
            sum_total_receipts REAL,
            filing_count INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg (
            candidate_id, candidate_full_name, office_sought, district_type, district,
            committee_id_sbe, committee_name, election_cycle, sum_total_receipts, filing_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (11, "Candidate One", "State Senate", "Senate", "12", 9001, "Committee 9001", 2026, 75000.0, 2),
    )

    conn.execute("DROP TABLE IF EXISTS bulk_receipts_clean")
    conn.execute(
        """
        CREATE TABLE bulk_receipts_clean (
            committee_id_sbe INTEGER,
            first_name TEXT,
            last_or_business_name TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            contributed_by TEXT,
            amount REAL,
            received_date TEXT,
            received_datetime_raw TEXT,
            d2_part_code TEXT,
            filed_doc_id INTEGER,
            is_archived INTEGER
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO bulk_receipts_clean (
            committee_id_sbe, first_name, last_or_business_name, address_line_1, address_line_2,
            city, state, postal_code, contributed_by, amount, received_date, received_datetime_raw,
            d2_part_code, filed_doc_id, is_archived
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                9001,
                "Donor",
                "Alpha",
                "1 Main St",
                None,
                "Springfield",
                "IL",
                "62701",
                "Donor Alpha",
                5000.0,
                "2025-03-10",
                "2025-03-10T12:00:00",
                "1A",
                7001,
                0,
            ),
            (
                9001,
                "Donor",
                "Beta",
                "2 Oak Ave",
                None,
                "Chicago",
                "IL",
                "60601",
                "Donor Beta",
                3000.0,
                "2025-06-20",
                "2025-06-20T09:00:00",
                "1A",
                7002,
                0,
            ),
        ],
    )

    conn.execute("DROP TABLE IF EXISTS bulk_expenditures_clean")
    conn.execute(
        """
        CREATE TABLE bulk_expenditures_clean (
            committee_id_sbe INTEGER,
            payee_last_or_business_name TEXT,
            candidate_name TEXT,
            d2_part_code TEXT,
            is_archived INTEGER,
            amount REAL,
            expended_date TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bulk_expenditures_clean (
            committee_id_sbe, payee_last_or_business_name, candidate_name, d2_part_code, is_archived, amount, expended_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (9001, "Media Vendor", "Candidate One", "9A", 0, 1200.0, "2025-07-01"),
    )
    conn.commit()


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_viz_lab.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_state_race_tables(conn)
    conn.close()
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "EXPERIMENTAL_VIZ_LAB_ENABLED": True,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "DASHBOARD_PREWARM_ENABLED": False,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def test_viz_lab_route_enabled(client):
    response = client.get("/experimental/viz-lab?period=all")
    assert response.status_code == 200
    assert b"Visualization Lab" in response.data
    assert b"Route: <code>/experimental/viz-lab</code>" in response.data


def test_viz_lab_route_disabled(tmp_path: Path):
    db_path = str(tmp_path / "test_viz_lab_disabled.db")
    init_db(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "EXPERIMENTAL_VIZ_LAB_ENABLED": False,
        }
    )
    client = app.test_client()
    response = client.get("/experimental/viz-lab")
    assert response.status_code == 404


def test_viz_lab_data_race_money_pressure(client):
    response = client.get(
        "/experimental/viz-lab/data/race_money_pressure?period=all&date_from=2025-01-01&date_to=2025-12-31"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["prototype_key"] == "race_money_pressure"
    assert payload["available"] is True
    assert payload["supports_date_window"] is True
    assert isinstance(payload["chart_rows"], list)
    assert len(payload["chart_rows"]) >= 1
    assert "query_ms" in payload


def test_viz_lab_data_unknown_prototype(client):
    response = client.get("/experimental/viz-lab/data/does-not-exist")
    assert response.status_code == 404


def test_viz_lab_network_slice_advanced_metrics_payload(client):
    response = client.get(
        "/experimental/viz-lab/data/network_slice"
        "?period=all&compute_advanced=1&compute_communities=1"
        "&mode=fast&k=16&weight_mode=weighted&edge_threshold=1000"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["prototype_key"] == "network_slice"
    assert payload["available"] is True
    assert "node_metrics" in payload
    assert isinstance(payload["node_metrics"], list)
    assert len(payload["node_metrics"]) >= 1
    first = payload["node_metrics"][0]
    assert "degree" in first
    assert "weighted_degree" in first
    assert "betweenness_approx" in first
    assert "community_id" in first
    assert "bridge_ratio" in first
    assert "graph_meta" in payload
    assert payload["graph_meta"]["compute_advanced"] is True
    assert "caps" in payload["graph_meta"]
    assert "edge_type_set" in payload["graph_meta"]


def test_viz_lab_network_slice_param_validation(client):
    response = client.get(
        "/experimental/viz-lab/data/network_slice"
        "?period=all&compute_advanced=1&mode=invalid&k=9999&weight_mode=bad"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["available"] is True
    assert payload["graph_meta"]["mode"] == "fast"
    assert payload["graph_meta"]["weight_mode"] == "weighted"
    actual_nodes = int(payload["graph_meta"]["caps"]["actual_nodes"])
    assert int(payload["graph_meta"]["k"]) == min(128, max(1, actual_nodes))
