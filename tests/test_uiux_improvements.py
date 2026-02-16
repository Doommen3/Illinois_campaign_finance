"""Tests for UIUX improvements: committee SBE routing, lobbying help-text, network graph enhancements."""
import json
from pathlib import Path

import pytest

from database.analytics import save_dashboard_snapshot, get_candidate_competition_networks
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

    # Minimal bulk committee table for SBE fallback route coverage.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_name TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name) VALUES (23456, 'Bulk Fallback Committee')"
    )

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
        "snapshot_version": 3,
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


def test_committee_detail_by_sbe_bulk_fallback(client):
    """SBE route renders fallback detail when committee exists only in bulk tables."""
    response = client.get("/committees/sbe/23456")
    assert response.status_code == 200
    assert b"Bulk Fallback Committee" in response.data


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
    response = client.get("/analytics/networks?load_mode=full&period=all")
    html = response.data.decode()
    assert "Three-column donor" in html
    assert "dark money" in html.lower() or "527" in html
    assert "Graph Density" in html


def test_analytics_networks_has_info_panels(client):
    """Networks page contains info panel divs for force-graph tabs."""
    response = client.get("/analytics/networks?load_mode=full&period=all")
    html = response.data.decode()
    assert "network-info-panel" in html
    assert "vendor-info-panel" in html
    assert "lobbying-info-panel" in html
    assert "darkmoney-info-panel" in html


def test_analytics_networks_has_color_legend(client):
    """Networks page contains the node color legend."""
    response = client.get("/analytics/networks?load_mode=full&period=all")
    html = response.data.decode()
    assert "network-color-legend" in html
    assert "Donor" in html
    assert "Committee" in html
    assert "Candidate" in html


# ── Round 2: Overlapping nodes, info panel, candidate names, donor dropdown ──


def test_network_svg_expanded_viewbox(client):
    """Force-graph SVGs use the expanded 1200x700 viewBox for less overlap."""
    response = client.get("/analytics/networks?load_mode=full&period=all")
    html = response.data.decode()
    assert 'viewBox="0 0 1200 700"' in html


def test_network_js_has_collision_detection(app):
    """analytics_networks.js includes post-layout collision resolution."""
    import os

    js_path = os.path.join(app.static_folder, "js", "analytics_networks.js")
    with open(js_path) as f:
        js = f.read()
    assert "collision resolution" in js.lower() or "collision" in js.lower()
    assert "overlap" in js.lower()


def test_info_panel_has_semantic_metric_markup(app):
    """Info panel JS generates semantic relationship/metric columns and score explanation text."""
    import os

    js_path = os.path.join(app.static_folder, "js", "analytics_networks.js")
    with open(js_path) as f:
        js = f.read()
    assert "Relationship" in js
    assert "Metric" in js
    assert "Flow" in js
    assert "score-based edges" in js.lower()


def test_network_js_has_density_filter_mode(app):
    """Dense graph rendering supports a filter mode toggle."""
    import os

    js_path = os.path.join(app.static_folder, "js", "analytics_networks.js")
    with open(js_path) as f:
        js = f.read()
    assert "applyDensityFilter" in js
    assert "graph-density-mode" in js
    assert "Balanced" in js


def test_candidate_competition_uses_candidates_table(tmp_path):
    """Candidate competition resolves names via bulk_candidates_clean JOIN."""
    db_path = str(tmp_path / "test_competition.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Create bulk download tables (not part of init_db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bulk_cmte_candidate_links_clean (
            link_record_id INTEGER PRIMARY KEY,
            committee_id_sbe INTEGER,
            candidate_id INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );
        CREATE TABLE IF NOT EXISTS bulk_candidates_clean (
            candidate_id INTEGER PRIMARY KEY,
            last_name TEXT,
            first_name TEXT,
            candidate_full_name TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            residence_county TEXT,
            party_affiliation TEXT,
            redaction_requested INTEGER,
            source_file TEXT,
            source_row_number INTEGER
        );
        CREATE TABLE IF NOT EXISTS analytics_donor_committee_agg (
            donor_key TEXT, committee_id TEXT, committee_name TEXT,
            total_amount REAL, contribution_count INTEGER, source TEXT
        );
    """)

    # Seed the data
    conn.execute(
        "INSERT INTO committees (name, committee_id_sbe) VALUES ('TestCmte', 100)"
    )
    conn.execute(
        """INSERT INTO bulk_cmte_candidate_links_clean
           (link_record_id, committee_id_sbe, candidate_id, source_file, source_row_number)
           VALUES (1, 100, 555, 'test', 1)"""
    )
    conn.execute(
        """INSERT INTO bulk_candidates_clean
           (candidate_id, last_name, first_name, candidate_full_name, source_file, source_row_number)
           VALUES (555, 'Smith', 'Alice', 'Alice Smith', 'test', 1)"""
    )
    conn.execute(
        """INSERT INTO analytics_donor_committee_agg
           (donor_key, donor_name, committee_id, committee_name, total_amount, contribution_count, source)
           VALUES ('donor_a', 'Test Donor A', '100', 'TestCmte', 5000.0, 3, 'bulk_receipts')"""
    )
    conn.commit()

    result = get_candidate_competition_networks(conn, candidate_limit=50, edge_limit=50)
    conn.close()

    # The state network should have used the candidate name, not the ID
    state_nodes = result.get("state", {}).get("nodes", [])
    for node in state_nodes:
        if "555" in str(node.get("id", "")):
            assert "Alice Smith" in node.get("label", ""), (
                f"Expected 'Alice Smith' in label, got: {node.get('label')}"
            )
            break


def test_follow_the_money_has_donor_dropdown(client):
    """Follow-the-money page has a donor select dropdown instead of text input."""
    response = client.get("/federal-finance/follow-the-money")
    assert response.status_code == 200
    html = response.data.decode()
    assert "Select a donor" in html
    assert '<select name="follow_donor_key"' in html
    # Should NOT have the old text input
    assert 'Donor entity key (required)' not in html


def test_follow_the_money_dropdown_help_text(client):
    """Follow-the-money page has updated help text about the dropdown."""
    response = client.get("/federal-finance/follow-the-money")
    html = response.data.decode()
    assert "top 200 donors" in html.lower() or "select a donor" in html.lower()
