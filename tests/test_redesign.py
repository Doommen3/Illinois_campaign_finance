"""Tests for website redesign: nav restructuring, admin hub, new federal pages, tabs."""
import re
from pathlib import Path
from urllib.parse import quote

import pytest

from database.connection import get_db, init_db
from database.models import AppUser
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_redesign.db")
    init_db(db_path)
    conn = get_db(db_path)
    AppUser.create_or_update_password(conn, "admin", "pass123")
    conn.close()
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    login_page = client.get("/auth/login")
    match = re.search(rb'name="csrf_token"\s+value="([^"]+)"', login_page.data)
    assert match
    csrf = match.group(1).decode()
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "pass123", "csrf_token": csrf},
        follow_redirects=True,
    )


# ---------- Navigation restructuring ----------


class TestNavigation:
    def test_nav_has_explore_group(self, client):
        resp = client.get("/")
        assert b"Explore" in resp.data

    def test_nav_has_analytics_group(self, client):
        resp = client.get("/")
        assert b"Analytics" in resp.data

    def test_nav_has_admin_group(self, client):
        resp = client.get("/")
        assert b"Admin" in resp.data

    def test_nav_has_tools_link(self, client):
        resp = client.get("/")
        assert b"Tools" in resp.data

    def test_nav_no_data_ops_group(self, client):
        """Data Ops group was removed in the redesign."""
        resp = client.get("/")
        assert b"Data Ops" not in resp.data

    def test_nav_has_lobbying_link(self, client):
        resp = client.get("/")
        assert b"Lobbying" in resp.data

    def test_nav_has_527s_link(self, client):
        resp = client.get("/")
        assert b"527s" in resp.data

    def test_nav_has_federal_finance_link(self, client):
        resp = client.get("/")
        assert b"Federal Finance" in resp.data


# ---------- Admin hub ----------


class TestAdminHub:
    def test_admin_hub_requires_login(self, client):
        resp = client.get("/admin/")
        assert resp.status_code in (302, 401)

    def test_admin_hub_accessible_when_logged_in(self, client):
        _login(client)
        resp = client.get("/admin/")
        assert resp.status_code == 200
        assert b"Admin Tools" in resp.data or b"admin" in resp.data.lower()

    def test_admin_hub_has_data_quality_section(self, client):
        _login(client)
        resp = client.get("/admin/")
        assert b"Data Quality" in resp.data or b"Reconciliation" in resp.data

    def test_admin_hub_has_data_entry_section(self, client):
        _login(client)
        resp = client.get("/admin/")
        assert b"Data Entry" in resp.data or b"Manual Entry" in resp.data


# ---------- New federal finance routes ----------


class TestNewFederalRoutes:
    def test_money_flow_route(self, client):
        resp = client.get("/federal-finance/money-flow")
        assert resp.status_code == 200
        assert b"Money Flow" in resp.data

    def test_influence_route(self, client):
        resp = client.get("/federal-finance/influence")
        assert resp.status_code == 200
        assert b"Influence" in resp.data

    def test_follow_the_money_route(self, client):
        resp = client.get("/federal-finance/follow-the-money")
        assert resp.status_code == 200
        assert b"Follow the Money" in resp.data

    def test_geography_route(self, client):
        resp = client.get("/federal-finance/geography")
        assert resp.status_code == 200
        assert b"Geography" in resp.data or b"Geographic" in resp.data

    def test_matching_route(self, client):
        resp = client.get("/federal-finance/matching")
        assert resp.status_code == 200

    def test_networks_route_still_works(self, client):
        resp = client.get("/federal-finance/networks")
        assert resp.status_code == 200
        assert b"Networks" in resp.data

    def test_donor_intelligence_route(self, client):
        resp = client.get("/federal-finance/donor-intelligence")
        assert resp.status_code == 200
        assert b"Donor Intelligence" in resp.data

    def test_donor_intelligence_no_influence_section(self, client):
        """Influence scores were moved to their own page."""
        resp = client.get("/federal-finance/donor-intelligence")
        assert b"Influence Scores - Donors" not in resp.data

    def test_donor_intelligence_no_follow_the_money_section(self, client):
        """Follow the Money was moved to its own page."""
        resp = client.get("/federal-finance/donor-intelligence")
        assert b"follow-money-svg" not in resp.data


# ---------- Federal subnav ----------


class TestFederalSubnav:
    def test_subnav_has_all_links(self, client):
        resp = client.get("/federal-finance/")
        html = resp.data.decode()
        for page_name in [
            "Overview",
            "Candidates",
            "Networks",
            "Money Flow",
            "Donor Intelligence",
            "Influence",
            "Follow the Money",
            "Geography",
            "Matching",
        ]:
            assert page_name in html, f"Missing subnav link: {page_name}"


# ---------- Tabs on candidate detail ----------


class TestCandidateDetailTabs:
    def test_detail_page_loads_without_data(self, client):
        """Route returns 200 with 'no data' message or 404 when FEC tables missing."""
        resp = client.get("/federal-finance/candidate/H0IL01000?cycle=2026")
        assert resp.status_code in (200, 404)

    def test_detail_template_has_help_text(self, client):
        """Verify the template file itself contains help-text."""
        import os
        tpl = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "templates", "federal_finance", "detail.html",
        )
        with open(tpl) as f:
            content = f.read()
        assert "help-text" in content


# ---------- Live feed tabs ----------


class TestLiveFeedTabs:
    def test_live_feed_loads(self, client):
        resp = client.get("/live-feed")
        assert resp.status_code == 200

    def test_live_feed_has_help_text(self, client):
        resp = client.get("/live-feed")
        assert b"help-text" in resp.data


# ---------- Data explanations ----------


class TestDataExplanations:
    def test_index_has_help_text(self, client):
        resp = client.get("/")
        assert b"help-text" in resp.data

    def test_federal_overview_has_help_text(self, client):
        resp = client.get("/federal-finance/")
        assert b"help-text" in resp.data

    def test_lobbying_has_help_text(self, client):
        resp = client.get("/lobbying/")
        assert b"help-text" in resp.data

    def test_527_has_help_text(self, client):
        resp = client.get("/527/")
        assert b"help-text" in resp.data


# ---------- Network visualizations ----------


class TestNetworkVisualizations:
    def _read_template(self):
        import os
        tpl = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "templates", "analytics", "networks.html",
        )
        with open(tpl) as f:
            return f.read()

    def test_networks_route_loads(self, client):
        resp = client.get("/analytics/networks?load_mode=full")
        assert resp.status_code == 200

    def test_networks_template_has_tabs(self):
        content = self._read_template()
        for label in [
            "Force Graph",
            "Sankey Flow",
            "Heatmap",
            "Vendor Network",
            "Money Flow",
            "State-Federal",
            "Lobbying Bridge",
            "527 Dark Money",
        ]:
            assert label in content, f"Missing tab label: {label}"

    def test_networks_template_has_svg_elements(self):
        content = self._read_template()
        for svg_id in [
            "network-svg",
            "sankey-svg",
            "heatmap-svg",
            "vendor-svg",
            "combined-flow-svg",
            "overlap-svg",
            "lobbying-svg",
            "dark-money-svg",
        ]:
            assert svg_id in content, f"Missing SVG id: {svg_id}"

    def test_networks_template_has_data_blocks(self):
        content = self._read_template()
        for data_id in [
            "analytics-network-data",
            "vendor-network-data",
            "overlap-graph-data",
            "lobbying-graph-data",
            "ecosystem-527-data",
        ]:
            assert data_id in content, f"Missing data block: {data_id}"

    def test_networks_js_has_click_to_lock(self):
        import os
        js_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "static", "js", "analytics_networks.js",
        )
        with open(js_path) as f:
            content = f.read()
        assert "lockedNodeId" in content
        assert "applyHighlight" in content

    def test_networks_js_has_sankey_renderer(self):
        import os
        js_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "static", "js", "analytics_networks.js",
        )
        with open(js_path) as f:
            content = f.read()
        assert "renderSankey" in content
        assert "renderHeatmap" in content
        assert "renderVendor" in content
        assert "renderOverlap" in content
        assert "renderLobbying" in content
        assert "renderDarkMoney" in content
        assert "renderCombined" in content

    def test_vendor_network_db_function(self, app):
        from database.analytics import get_vendor_expenditure_network
        from database.connection import get_db
        conn = get_db(app.config["DATABASE_PATH"])
        result = get_vendor_expenditure_network(conn)
        assert "nodes" in result
        assert "edges" in result
        assert "centrality" in result
        assert "summary" in result
        conn.close()


class TestAdvancedGraphVisualizations:
    def _read_file(self, *parts):
        path = Path(__file__).resolve().parents[1].joinpath(*parts)
        return path.read_text()

    def test_relationships_template_has_arc_and_alluvial_controls(self):
        content = self._read_file("webapp", "templates", "analytics", "relationships.html")
        assert "Arc Diagram" in content
        assert "Alluvial" in content
        assert "data-graph-controls=\"donor-cogiving\"" in content
        assert "data-graph-controls=\"committee-similarity\"" in content
        assert "data-graph-controls=\"lobbying-influence\"" in content
        assert "data-graph-controls=\"ecosystem-527\"" in content

    def test_relationships_js_has_arc_and_alluvial_renderers(self):
        content = self._read_file("webapp", "static", "js", "relationship_graphs.js")
        assert "supportsArc" in content
        assert "supportsAlluvial" in content
        assert "drawArcGraph" in content
        assert "drawAlluvialGraph" in content

    def test_matching_template_has_overlap_bubble_view(self):
        content = self._read_file("webapp", "templates", "federal_finance", "matching.html")
        assert "match-overlap-bubble-svg" in content
        assert "match-overlap-bubble-color" in content
        assert "match-overlap-bubble-limit" in content
        assert "match-overlap-data" in content

    def test_donor_intelligence_template_has_community_treemap_sunburst(self):
        content = self._read_file("webapp", "templates", "federal_finance", "donor_intelligence.html")
        assert "Community Treemap / Sunburst" in content
        assert "cluster-community-svg" in content
        assert "cluster-community-mode" in content
        assert "cluster-community-metric" in content
        assert "cluster-community-data" in content

    def test_overlap_graph_db_function(self, app):
        from database.analytics import get_state_federal_overlap_graph
        from database.connection import get_db
        conn = get_db(app.config["DATABASE_PATH"])
        result = get_state_federal_overlap_graph(conn)
        assert "nodes" in result
        assert "edges" in result
        assert "centrality" in result
        assert "summary" in result
        conn.close()

    def test_overlap_graph_group_by_with_federal_data(self, app):
        """Overlap graph query must aggregate committee_name (GROUP BY fix)."""
        from database.analytics import get_state_federal_overlap_graph
        from database.connection import get_db
        conn = get_db(app.config["DATABASE_PATH"])

        # Seed fec_local_donor_matches
        conn.execute("DROP TABLE IF EXISTS fec_local_donor_matches")
        conn.execute(
            """
            CREATE TABLE fec_local_donor_matches (
                federal_donor_entity_key TEXT,
                local_donor_key TEXT,
                primary_local_donor_key TEXT,
                federal_donor_name TEXT,
                local_donor_name TEXT,
                federal_total_amount REAL,
                local_total_amount REAL,
                confidence_score REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fec_local_donor_matches VALUES
            ('FED1', 'LOC1', 'LOC1', 'Fed Donor 1', 'Local Donor 1', 5000.0, 3000.0, 0.9)
            """
        )

        # Seed fec_schedule_a_contributions with duplicate committee_id rows
        conn.execute("DROP TABLE IF EXISTS fec_schedule_a_contributions")
        conn.execute(
            """
            CREATE TABLE fec_schedule_a_contributions (
                contributor_id TEXT,
                committee_id TEXT,
                committee_name TEXT,
                contribution_receipt_amount REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO fec_schedule_a_contributions VALUES (?, ?, ?, ?)",
            [
                ("FED1", "C001", "Test FEC Committee", 1000.0),
                ("FED1", "C001", "Test FEC Committee", 2000.0),
            ],
        )
        conn.commit()

        result = get_state_federal_overlap_graph(conn)
        assert "nodes" in result
        assert "edges" in result
        # Verify federal committee edge was created
        fed_edges = [e for e in result["edges"] if e.get("edge_type") == "donor_federal"]
        assert len(fed_edges) >= 1
        assert fed_edges[0]["weight"] == 3000.0
        conn.close()
