"""Tests for search normalization and autocomplete suggest endpoints."""
from pathlib import Path

import pytest

from webapp.utils.search_normalize import normalize_search_query
from database.connection import get_db, init_db
from webapp.app import create_app


# ---- Unit tests for normalize_search_query ----

class TestNormalizeSearchQuery:

    def test_empty_input(self):
        assert normalize_search_query(None) == ""
        assert normalize_search_query("") == ""
        assert normalize_search_query("   ") == ""

    def test_lowercasing(self):
        assert normalize_search_query("AMEREN") == "ameren"
        assert normalize_search_query("Ameren") == "ameren"

    def test_whitespace_collapse(self):
        assert normalize_search_query("  foo   bar  ") == "foo bar"
        assert normalize_search_query("foo\t\nbar") == "foo bar"

    def test_abbreviation_dot_stripping(self):
        assert normalize_search_query("Ameren Inc.") == "ameren inc"
        assert normalize_search_query("ACME Corp.") == "acme corp"
        assert normalize_search_query("Test Ltd.") == "test ltd"
        assert normalize_search_query("Test Co.") == "test co"

    def test_sql_wildcard_stripping(self):
        assert normalize_search_query("test%query") == "testquery"
        assert normalize_search_query("test_query") == "test query"

    def test_case_insensitive_same_results(self):
        r1 = normalize_search_query("AMEREN")
        r2 = normalize_search_query("ameren")
        r3 = normalize_search_query("Ameren")
        assert r1 == r2 == r3

    def test_inc_variations_normalize(self):
        r1 = normalize_search_query("ACME Inc.")
        r2 = normalize_search_query("ACME Inc")
        r3 = normalize_search_query("acme inc")
        assert r1 == r2 == r3

    def test_preserves_meaningful_content(self):
        assert normalize_search_query("Commonwealth Edison") == "commonwealth edison"
        assert normalize_search_query("123-456") == "123-456"


# ---- Integration tests for suggest endpoints ----

@pytest.fixture
def app_527(tmp_path: Path):
    db_path = str(tmp_path / "test_suggest_527.db")
    init_db(db_path)
    conn = get_db(db_path)
    for i, name in enumerate(["Ameren Illinois PAC", "AMEREN CORP", "American Electric"]):
        conn.execute(
            "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip) "
            "VALUES (?, ?, 0, ?, 'Chicago', 'IL', '60601')",
            (f"00000000{i}", 100 + i, name),
        )
    conn.commit()
    conn.close()
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


@pytest.fixture
def app_lobbying(tmp_path: Path):
    db_path = str(tmp_path / "test_suggest_lobbying.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Springfield Group', 2026)"
    )
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (2, 'Springfield Advisors', 2025)"
    )
    conn.execute(
        "INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'ComEd')"
    )
    conn.execute(
        "INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method) "
        "VALUES (1, 'dk1', 'ComEd', 'Donor A', 0.90, 'jaccard')"
    )
    conn.commit()
    conn.close()
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


class TestSuggest527:

    def test_returns_json(self, app_527):
        client = app_527.test_client()
        resp = client.get("/527/suggest?q=amer")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 2

    def test_short_query_returns_empty(self, app_527):
        client = app_527.test_client()
        resp = client.get("/527/suggest?q=a")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_no_match(self, app_527):
        client = app_527.test_client()
        resp = client.get("/527/suggest?q=xyznotfound")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_case_insensitive(self, app_527):
        client = app_527.test_client()
        r1 = client.get("/527/suggest?q=ameren")
        r2 = client.get("/527/suggest?q=AMEREN")
        d1 = r1.get_json()
        d2 = r2.get_json()
        assert len(d1) == len(d2)

    def test_result_shape(self, app_527):
        client = app_527.test_client()
        resp = client.get("/527/suggest?q=ameren")
        data = resp.get_json()
        assert len(data) > 0
        assert "label" in data[0]
        assert "value" in data[0]


class TestSuggestLobbyingEntities:

    def test_returns_results(self, app_lobbying):
        client = app_lobbying.test_client()
        resp = client.get("/lobbying/suggest?q=spring")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_short_query_empty(self, app_lobbying):
        client = app_lobbying.test_client()
        resp = client.get("/lobbying/suggest?q=s")
        assert resp.get_json() == []


class TestSuggestFlowClients:

    def test_returns_clients(self, app_lobbying):
        client = app_lobbying.test_client()
        resp = client.get("/lobbying/flows/suggest?q=com")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["label"] == "ComEd"

    def test_short_query_empty(self, app_lobbying):
        client = app_lobbying.test_client()
        resp = client.get("/lobbying/flows/suggest?q=c")
        assert resp.get_json() == []


class TestNormalizedSearch:
    """Test that the main search routes apply normalization."""

    def test_527_search_case_insensitive(self, app_527):
        client = app_527.test_client()
        r1 = client.get("/527/?q=ameren&period=all")
        r2 = client.get("/527/?q=AMEREN&period=all")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert b"Ameren" in r1.data or b"AMEREN" in r1.data
        assert b"Ameren" in r2.data or b"AMEREN" in r2.data

    def test_lobbying_search_case_insensitive(self, app_lobbying):
        client = app_lobbying.test_client()
        r1 = client.get("/lobbying/?q=springfield")
        r2 = client.get("/lobbying/?q=SPRINGFIELD")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert b"Springfield" in r1.data
        assert b"Springfield" in r2.data
