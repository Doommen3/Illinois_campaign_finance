"""Performance-focused tests for app request-path caching behavior."""
from pathlib import Path

from database.connection import get_db, init_db
from webapp.app import create_app
import webapp.app as app_module
import webapp.routes.main as main_routes
import webapp.routes.analytics as analytics_routes


def test_global_data_status_cached_across_requests(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "perf_cache.db")
    init_db(db_path)

    call_count = {"value": 0}

    def fake_status(conn, *, local_stale_days, federal_stale_days):
        call_count["value"] += 1
        return {
            "local_receipt_date": None,
            "federal_receipt_date": None,
            "federal_disbursement_date": None,
            "federal_independent_expenditure_date": None,
            "federal_sync_updated_at": None,
            "federal_latest_coverage_date": None,
            "local_age_days": None,
            "federal_age_days": None,
            "federal_receipt_age_days": None,
            "federal_disbursement_age_days": None,
            "federal_independent_expenditure_age_days": None,
            "local_is_stale": False,
            "federal_is_stale": False,
            "is_any_stale": False,
            "has_any_data": True,
        }

    monkeypatch.setattr(app_module, "_build_global_data_status", fake_status)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 60,
        }
    )

    client = app.test_client()
    first = client.get("/")
    second = client.get("/search?q=test")

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count["value"] == 1


def test_dashboard_insights_cached_across_requests(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "dashboard_cache.db")
    init_db(db_path)

    call_count = {"value": 0}

    def fake_build(conn):
        call_count["value"] += 1
        return {
            "donor_dependent_committees": [],
            "lobbying_donor_overlap": [],
            "director_candidates": [],
            "dark_money_totals": {"total_amount": 0.0, "match_count": 0},
        }

    monkeypatch.setattr(main_routes, "_build_dashboard_insights", fake_build)
    main_routes._dashboard_insights_cache["value"] = None
    main_routes._dashboard_insights_cache["expires_at"] = 0.0

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "DASHBOARD_INSIGHTS_CACHE_TTL_SECONDS": 60,
        }
    )

    conn = get_db(db_path)
    with app.app_context():
        first = main_routes._get_dashboard_insights(conn)
        second = main_routes._get_dashboard_insights(conn)
    conn.close()

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert call_count["value"] == 1


def test_relationships_network_cached_across_requests(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "relationships_cache.db")
    init_db(db_path)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS": 60,
        }
    )
    analytics_routes._relationships_cache["payload"] = None
    analytics_routes._relationships_cache["expires_at"] = 0.0
    analytics_routes._relationships_cache["key"] = None

    call_count = {"value": 0}

    def fake_network(*args, **kwargs):
        call_count["value"] += 1
        return {"nodes": [], "edges": [], "summary": {}}

    graph_date_args = {"lobbying": None, "ecosystem": None}

    def fake_lobbying(*args, **kwargs):
        graph_date_args["lobbying"] = {
            "date_from": kwargs.get("date_from"),
            "date_to": kwargs.get("date_to"),
        }
        return fake_network(*args, **kwargs)

    def fake_ecosystem(*args, **kwargs):
        graph_date_args["ecosystem"] = {
            "date_from": kwargs.get("date_from"),
            "date_to": kwargs.get("date_to"),
        }
        return fake_network(*args, **kwargs)

    def fake_candidate_competition(*args, **kwargs):
        call_count["value"] += 1
        return {
            "state": {"nodes": [], "edges": [], "summary": {"edge_count": 0}},
            "federal": {"nodes": [], "edges": [], "summary": {"edge_count": 0}},
            "cross_scope": {"nodes": [], "edges": [], "summary": {"edge_count": 0}},
            "combined": {"nodes": [], "edges": [], "summary": {"edge_count": 0}},
        }

    monkeypatch.setattr(analytics_routes, "get_donor_cogiving_network", fake_network)
    monkeypatch.setattr(analytics_routes, "get_committee_similarity_network", fake_network)
    monkeypatch.setattr(analytics_routes, "get_candidate_competition_networks", fake_candidate_competition)
    monkeypatch.setattr(analytics_routes, "get_lobbying_influence_graph", fake_lobbying)
    monkeypatch.setattr(analytics_routes, "get_irs527_ecosystem_graph", fake_ecosystem)

    client = app.test_client()
    first = client.get("/analytics/relationships?date_from=2025-01-01&date_to=2025-12-31&load_mode=full")
    second = client.get("/analytics/relationships?date_from=2025-01-01&date_to=2025-12-31&load_mode=full")

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count["value"] == 5
    assert graph_date_args["lobbying"] == {"date_from": "2025-01-01", "date_to": "2025-12-31"}
    assert graph_date_args["ecosystem"] == {"date_from": "2025-01-01", "date_to": "2025-12-31"}


def test_search_results_cached_across_requests(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "search_cache.db")
    init_db(db_path)

    main_routes._search_results_cache.clear()
    call_count = {"value": 0}

    def fake_search_reports(*args, **kwargs):
        call_count["value"] += 1
        return []

    monkeypatch.setattr(main_routes, "_search_reports", fake_search_reports)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": True,
            "SEARCH_RESULTS_CACHE_TTL_SECONDS": 60,
            "SEARCH_RESULTS_CACHE_MAX_ENTRIES": 64,
        }
    )

    client = app.test_client()
    first = client.get("/search?q=sample&type=reports")
    second = client.get("/search?q=sample&type=reports")

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count["value"] == 1


def test_search_all_skips_heavy_sections_for_non_key_queries(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "search_sections.db")
    init_db(db_path)

    call_count = {"filed_docs": 0, "donor_keys": 0}

    def fake_filed_docs(*args, **kwargs):
        call_count["filed_docs"] += 1
        return []

    def fake_donor_keys(*args, **kwargs):
        call_count["donor_keys"] += 1
        return []

    monkeypatch.setattr(main_routes, "_search_filed_docs", fake_filed_docs)
    monkeypatch.setattr(main_routes, "_search_donor_keys", fake_donor_keys)

    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "ROUTE_PERF_CACHE_ENABLED": False,
        }
    )

    client = app.test_client()
    response = client.get("/search?q=illinois&type=all")

    assert response.status_code == 200
    assert call_count["filed_docs"] == 0
    assert call_count["donor_keys"] == 0
