"""Tests for UIUX improvements: committee SBE routing, lobbying help-text, network graph enhancements."""
import json
from pathlib import Path

import pytest

from database.analytics import save_dashboard_snapshot
from database.connection import get_db, init_db
from database.models import Committee
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_uiux.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Seed a committee with an SBE ID
    Committee.get_or_create(conn, "Test SBE Committee", committee_id_sbe=12345)

    # Seed lobbying data for entity/client detail pages
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Test Entity', 2026)"
    )
    conn.execute(
        "INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'Test Client')"
    )
    conn.execute(
        "INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2026)"
    )

    # Seed a minimal analytics snapshot so heavy_sections_loaded=True for network tests
    empty_network = {
        "nodes": [], "edges": [], "centrality": [],
        "summary": {"node_count": 0, "edge_count": 0, "donor_committee_edges": 0, "committee_candidate_edges": 0, "donor_committee_source": "bulk_receipts"},
    }
    snapshot_params = {
        "min_edge_amount": 1000.0,
        "network_limit": 200,
        "anomaly_limit": 25,
        "concentration_limit": 25,
        "months": 24,
        "geo_state_limit": 15,
        "geo_city_limit": 25,
        "nlp_limit": 20,
        "recon_limit": 20,
        "recon_min_abs_diff": 1000.0,
        "date_from": None,
        "date_to": None,
        "snapshot_version": 2,
    }
    save_dashboard_snapshot(
        conn,
        params=snapshot_params,
        status="completed",
        payload={
            "network": empty_network,
            "anomalies": [],
            "concentration": [],
            "geo_summary": {"by_state": [], "by_city": []},
            "time_series": [],
            "nlp_summary": [],
            "reconciliation": [],
        },
    )
    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Phase 1: Committee SBE routing ──


def test_committee_detail_by_sbe_redirect(client):
    """SBE route redirects to the canonical internal-ID committee page."""
    response = client.get("/committees/sbe/12345")
    assert response.status_code == 302
    assert "/committees/" in response.headers["Location"]


def test_committee_detail_by_sbe_not_found(client):
    """Unknown SBE ID returns 404."""
    response = client.get("/committees/sbe/99999")
    assert response.status_code == 404


# ── Phase 2: Lobbying page explanations ──


def test_entity_detail_has_explanations(client):
    """Entity detail page contains help-text explanation blocks."""
    response = client.get("/lobbying/1")
    assert response.status_code == 200
    html = response.data.decode()
    assert "help-text" in html
    assert "registered lobbying entity" in html
    assert "fuzzy name-matching" in html.lower() or "name-matching" in html.lower()


def test_client_detail_has_explanations(client):
    """Client detail page contains help-text explanation blocks."""
    response = client.get("/lobbying/client/1")
    assert response.status_code == 200
    html = response.data.decode()
    assert "help-text" in html
    assert "lobbying client" in html.lower()
    assert "lobbying firm" in html.lower() or "lobbying firms" in html.lower()


# ── Phases 3-4: Network graph enhancements ──


def test_analytics_networks_page_loads(client):
    """Networks page returns 200."""
    response = client.get("/analytics/networks")
    assert response.status_code == 200


def test_analytics_networks_has_graph_descriptions(client):
    """Networks page contains per-tab help-text descriptions."""
    response = client.get("/analytics/networks?load_mode=full")
    html = response.data.decode()
    assert "Three-column donor" in html
    assert "dark money" in html.lower() or "527" in html


def test_analytics_networks_has_info_panels(client):
    """Networks page contains info panel divs for force-graph tabs."""
    response = client.get("/analytics/networks?load_mode=full")
    html = response.data.decode()
    assert "network-info-panel" in html
    assert "vendor-info-panel" in html
    assert "lobbying-info-panel" in html
    assert "darkmoney-info-panel" in html


def test_analytics_networks_has_color_legend(client):
    """Networks page contains the node color legend."""
    response = client.get("/analytics/networks?load_mode=full")
    html = response.data.decode()
    assert "network-color-legend" in html
    assert "Donor" in html
    assert "Committee" in html
    assert "Candidate" in html
