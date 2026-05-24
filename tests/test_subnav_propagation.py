"""P2-4 — subnav filter-state propagation.

Asserts that allow-listed filters (date_from, date_to, min_edge_amount,
network_limit, etc.) survive a subnav tab change, and non-allow-listed
filters (state_race_sort, follow_donor_key, etc.) do NOT propagate.
"""
from pathlib import Path

from database.connection import init_db
from webapp.app import create_app


def _make_client(db_path: str):
    init_db(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": False,
        }
    )
    return app.test_client()


def test_analytics_subnav_propagates_allow_listed_filters(tmp_path: Path):
    """Non-default analytics filters appear in every subnav anchor href."""
    client = _make_client(str(tmp_path / "analytics_subnav.db"))

    resp = client.get(
        "/analytics/networks"
        "?date_from=2026-01-01"
        "&date_to=2026-06-30"
        "&min_edge_amount=750"
        "&network_limit=300"
        "&anomaly_limit=40"
        "&concentration_limit=35"
        "&months=18"
        "&geo_state_limit=12"
        "&geo_city_limit=20"
        "&nlp_limit=18"
        "&load_mode=full"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # All six tabs should carry every allow-listed param. The Overview tab
    # uses the `dashboard` endpoint which routes to `/analytics/` (no
    # /overview suffix).
    for tab_path in (
        "/analytics/?",
        "/analytics/networks?",
        "/analytics/risk?",
        "/analytics/donors?",
        "/analytics/geography?",
        "/analytics/relationships?",
    ):
        anchor_marker = f'href="{tab_path}'
        assert anchor_marker in body, f"missing subnav anchor for {tab_path}"

    # The allow-listed filter values should be present in subnav anchors.
    assert "date_from=2026-01-01" in body
    assert "date_to=2026-06-30" in body
    assert "min_edge_amount=750" in body
    assert "network_limit=300" in body
    assert "anomaly_limit=40" in body
    assert "concentration_limit=35" in body
    assert "months=18" in body
    assert "geo_state_limit=12" in body
    assert "geo_city_limit=20" in body
    assert "nlp_limit=18" in body
    assert "load_mode=full" in body


def test_analytics_subnav_does_not_propagate_excluded_params(tmp_path: Path):
    """Page-specific UI controls must NOT appear in subnav anchors."""
    client = _make_client(str(tmp_path / "analytics_subnav_excl.db"))

    resp = client.get(
        "/analytics/?state_race_sort=outside_spending_total"
        "&state_race_dir=asc"
        "&refresh_cache=1"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Extract subnav block specifically so we don't false-positive on these
    # params appearing in page-body form controls or links elsewhere.
    start = body.find('class="analytics-subnav"')
    assert start != -1
    end = body.find("</nav>", start)
    assert end != -1
    subnav_html = body[start:end]

    assert "state_race_sort=" not in subnav_html
    assert "state_race_dir=" not in subnav_html
    assert "refresh_cache=" not in subnav_html


def test_federal_subnav_propagates_allow_listed_filters(tmp_path: Path):
    """Federal cycle + analysis scope + window appear in every federal subnav anchor."""
    client = _make_client(str(tmp_path / "federal_subnav.db"))

    resp = client.get(
        "/federal-finance/networks"
        "?cycle=2026"
        "&analysis_office=H"
        "&analysis_district=01"
        "&date_from=2026-01-01"
        "&date_to=2026-06-30"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    start = body.find('class="federal-subnav"')
    assert start != -1
    end = body.find("</nav>", start)
    assert end != -1
    subnav_html = body[start:end]

    # Allow-listed params should appear inside every federal subnav anchor.
    assert "cycle=2026" in subnav_html
    assert "analysis_office=H" in subnav_html
    assert "analysis_district=01" in subnav_html
    assert "date_from=2026-01-01" in subnav_html
    assert "date_to=2026-06-30" in subnav_html


def test_federal_subnav_does_not_propagate_excluded_params(tmp_path: Path):
    """Per-page knobs (network_min_edge_amount, follow_donor_key, etc.) must NOT
    propagate across federal subnav tabs."""
    client = _make_client(str(tmp_path / "federal_subnav_excl.db"))

    resp = client.get(
        "/federal-finance/follow-the-money"
        "?cycle=2026"
        "&follow_donor_key=test-donor-key"
        "&follow_max_hops=5"
        "&follow_min_edge_amount=999"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    start = body.find('class="federal-subnav"')
    assert start != -1
    end = body.find("</nav>", start)
    assert end != -1
    subnav_html = body[start:end]

    assert "follow_donor_key=" not in subnav_html
    assert "follow_max_hops=" not in subnav_html
    assert "follow_min_edge_amount=" not in subnav_html
    assert "network_min_edge_amount=" not in subnav_html
