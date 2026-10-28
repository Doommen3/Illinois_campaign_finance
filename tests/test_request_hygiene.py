"""Load-times plan, unit P1 -- request hygiene: banner cost (L1), crawler and edge controls (L7), /candidates/ (L14).

Plan: .claude/plans/load-times.md ("Ranked fixes" rows L1, L7, L14 and the Execution decisions LD1, LD2, LD4).
The file starts red; every red test names the behaviour P1 has to add.

Contract pinned (header names and Cache-Control directives are compared case-insensitively):

  L1 (a) The Data Status banner (webapp/app.py ``_build_global_data_status``) and the /candidates freshness block
         (webapp/routes/main.py ``_get_candidate_stats``) read the latest local receipt date from the matview's native
         columns -- MAX(received_date) FROM isbe_condensed_receipts WHERE archived = FALSE -- and no longer run
         MAX(received_date) through the bulk_receipts_clean compat view (a 1.5 s parallel scan on prod).
     (b) ``config.GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS`` exists, defaults to 900, and ``create_app`` honours the
         environment variable of the same name. config.py reads the environment at import time, so this is checked
         in a fresh interpreter instead of by patching os.environ in-process.
     (c) Rendering the 404 page (unmatched URL, or abort(404) inside a route) or the 500 page does not build the banner.
  L7 (d) GET /robots.txt -> 200 text/plain; its ``User-agent: *`` group carries every LD1 Disallow line.
     (e) LD2: an anonymous GET answered 200 or 404 as text/html, with no session cookie, outside the
         auth/admin/manual_entry/api/experimental blueprints, carries exactly
         ``Cache-Control: public, max-age=0, s-maxage=<app.config["HTML_EDGE_CACHE_SECONDS"], default 300>`` and no
         ``Vary: Cookie``. 0 turns the header off.
     (f) LD4: an anonymous request carrying refresh_cache / refresh_full / sync_full is answered 302 to the same URL
         without them, before the route runs. A signed-in user is not redirected.
  L14 (g) GET /candidates/ is a 200 or a 30x to /candidates, never a 404.

Interpretation choices (each flagged in the tester report; relax by editing one test):
  * GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS = 0 bypasses the banner cache (today's ``ttl_seconds > 0`` rule). The fixtures
    rely on it so that a banner cached by an earlier test can never leak into a later one.
  * The refresh gate keys on a SIGNED-IN USER, not on the mere presence of a session cookie: any visitor who has loaded
    /auth/login holds a valid anonymous session cookie, and a client can send any cookie it likes.
  * The edge-cache header keys on the SESSION cookie by name: other cookies (Google Analytics, Cloudflare) must not
    switch it off.

Fixture facts (hand-computed). ``isbe_condensed_receipts`` rows (id, received_date, archived), inserted in this order so
that neither heap order nor MIN, first-row or an unfiltered MAX gives the answer:
    1  2024-06-01  FALSE
    2  2026-09-01  TRUE    ARCHIVED and the latest date of all: counts for nothing
    3  2026-03-15  FALSE   D1 = the latest NON-archived date: the banner must say 2026-03-15
    4  2025-01-10  FALSE
The "trap" variants also create a bulk_receipts_clean compat view with ONE row dated 2026-08-08 (later than D1, earlier
than the archived date): an implementation that still prefers, or also scans, the compat view shows 2026-08-08.
Without the trap the compat view is absent, as on a database that never ran the compat swap.
"""
import ast
import html as html_lib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from urllib.robotparser import RobotFileParser

import psycopg
import pytest

import webapp.app as app_module
import webapp.routes.analytics as analytics_routes
import webapp.routes.main as main_routes
from database.connection import get_db, init_db
from database.models import AppUser
from webapp.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]

ADMIN_USER = "hygiene_admin"
ADMIN_PASS = "secret123"

D1 = "2026-03-15"
ARCHIVED_LATER = "2026-09-01"
TRAP_DATE = "2026-08-08"

TTL_ENV = "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS"

# Public 200 HTML pages from four different blueprints (a per-blueprint decorator must not pass).
PUBLIC_PAGES = ["/about", "/committees/", "/analytics/", "/lobbying/"]

# LD1, split so a single decision can be relaxed by editing one list.
LD1_DISALLOWED_PATHS = [
    "/search",
    "/analytics/geo-drilldown",
    "/federal-finance/geo-drilldown",
    "/api/",
    "/experimental/",
    "/compare",
    "/investigate",
    "/person-intelligence",
    "/admin/",
    "/manual-entry/",
    "/auth/",
]
LD1_REFRESH_PATTERNS = [
    "/*?*refresh_cache=",
    "/*?*refresh_full=",
    "/*?*sync_full=",
]
LD1_SCOPE_PATTERNS = [
    "/*?*load_mode=",
    "/*?*period=",
    "/*?*cycle=",
    "/*?*year=",
    "/*?*date_from=",
]

# Landing pages and entity pages that must stay crawlable.
CRAWLABLE_PATHS = [
    "/",
    "/about",
    "/privacy",
    "/candidates",
    "/candidates/900",
    "/committees/",
    "/committees/1126",
    "/donors/",
    "/lobbying/",
    "/analytics/",
    "/federal-finance/",
    "/527/",
    "/openbook/",
]

BANNER_CONTROL_MESSAGE = (
    "control failed: a normal page did not build the banner "
    "(GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS=0 must bypass the cache)"
)

EMPTY_STATUS = {
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
    "has_any_data": False,
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _seed_matview(conn) -> None:
    conn.execute(
        """
        CREATE TABLE isbe_condensed_receipts (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            received_date DATE,
            amount DOUBLE PRECISION,
            archived BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO isbe_condensed_receipts (id, committee_id, received_date, amount, archived)
        VALUES
            (1, 100, '2024-06-01',  10.0, FALSE),
            (2, 100, '{ARCHIVED_LATER}', 9999.0, TRUE),
            (3, 100, '{D1}', 500.0, FALSE),
            (4, 100, '2025-01-10',  20.0, FALSE)
        """
    )
    conn.commit()


def _seed_matview_and_trap_view(conn) -> None:
    _seed_matview(conn)
    conn.execute(
        f"""
        CREATE VIEW bulk_receipts_clean AS
        SELECT 1 AS receipt_record_id, DATE '{TRAP_DATE}' AS received_date, FALSE AS is_archived
        """
    )
    conn.commit()


def _app_config(db_path: str, **overrides) -> dict:
    config = {
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": False,
        "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 0,
        "EXPERIMENTAL_VIZ_LAB_ENABLED": True,
    }
    config.update(overrides)
    return config


def _add_probe_routes(app) -> None:
    def boom():
        raise RuntimeError("request-hygiene probe: forced server error")

    app.add_url_rule("/_hygiene/boom", "hygiene_boom", boom)


@pytest.fixture
def make_app(tmp_path: Path):
    """Factory: build the app AFTER the test has installed its monkeypatches."""

    def factory(seed=None, **overrides):
        db_path = str(tmp_path / "request_hygiene.db")
        init_db(db_path)
        conn = get_db(db_path)
        AppUser.create_or_update_password(conn, ADMIN_USER, ADMIN_PASS)
        if seed is not None:
            seed(conn)
        conn.close()
        app = create_app(_app_config(db_path, **overrides))
        _add_probe_routes(app)
        return app

    return factory


def _login(client) -> None:
    page = client.get("/auth/login")
    match = re.search(rb'name="csrf_token"\s+value="([^"]+)"', page.data)
    assert match is not None, "the login page rendered no csrf_token"
    response = client.post(
        "/auth/login",
        data={
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
            "csrf_token": match.group(1).decode(),
            "next": "/about",
        },
    )
    assert response.status_code == 302


def _client_with_session(app, kind: str):
    client = app.test_client()
    if kind == "logged-in":
        _login(client)
    elif kind == "anonymous-session-with-csrf-token":
        # Anyone who loads the login page holds this cookie; it names no user.
        client.get("/auth/login")
    elif kind == "unsigned-garbage-cookie":
        client.set_cookie(app.config["SESSION_COOKIE_NAME"], "not-a-signed-session")
    else:
        raise AssertionError(kind)
    return client


def _header_tokens(response, header: str) -> set[str]:
    tokens = set()
    for line in response.headers.getlist(header):
        for part in line.split(","):
            part = part.strip().lower()
            if part:
                tokens.add(part)
    return tokens


def _cache_tokens(response) -> set[str]:
    return _header_tokens(response, "Cache-Control")


def _vary_tokens(response) -> set[str]:
    return _header_tokens(response, "Vary")


def _edge_tokens(seconds: int) -> set[str]:
    return {"public", "max-age=0", f"s-maxage={seconds}"}


def _assert_not_edge_cacheable(response) -> None:
    tokens = _cache_tokens(response)
    assert "public" not in tokens, tokens
    edge_ttls = [token for token in tokens if token.startswith("s-maxage")]
    assert edge_ttls == [], tokens


def _visible_text(page: str) -> str:
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _install_banner_counter(monkeypatch) -> dict:
    """Replace the banner builder with a counter. Call BEFORE the app is created."""
    calls = {"count": 0}

    def counting_builder(*args, **kwargs):
        calls["count"] += 1
        return dict(EMPTY_STATUS)

    monkeypatch.setattr(app_module, "_build_global_data_status", counting_builder)
    return calls


def _string_literals(func) -> list[str]:
    """Every string literal in ``func``'s source, whitespace-collapsed and lower-cased (f-string holes become {})."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            pieces = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    pieces.append(str(part.value))
                else:
                    pieces.append("{}")
            found.append(" ".join("".join(pieces).split()).lower())
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(" ".join(node.value.split()).lower())
    return found


MAX_RECEIVED_DATE = re.compile(r"max\(\s*(?:\w+\.)?received_date\s*\)")


def _max_received_date_statements(func) -> list[str]:
    return [text for text in _string_literals(func) if MAX_RECEIVED_DATE.search(text)]


# ---------------------------------------------------------------------------
# (a) L1 -- freshness is read from the matview's native columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_name, function_name",
    [("webapp.app", "_build_global_data_status"), ("webapp.routes.main", "_get_candidate_stats")],
    ids=["banner-builder", "candidates-freshness-block"],
)
def test_freshness_query_reads_the_matview_not_the_compat_view(module_name, function_name):
    func = getattr(importlib.import_module(module_name), function_name)
    statements = _max_received_date_statements(func)
    on_matview = [text for text in statements if "from isbe_condensed_receipts" in text]
    on_compat_view = [text for text in statements if "bulk_receipts_clean" in text]
    assert on_matview, ("no MAX(received_date) statement reads FROM isbe_condensed_receipts", statements)
    assert "archived = false" in on_matview[0], on_matview[0]
    assert on_compat_view == [], ("MAX(received_date) still runs through bulk_receipts_clean", on_compat_view)


@pytest.mark.parametrize("with_trap_view", [False, True], ids=["compat-view-absent", "compat-view-trap"])
def test_banner_shows_the_latest_non_archived_receipt_from_the_matview(make_app, with_trap_view):
    seed = _seed_matview_and_trap_view if with_trap_view else _seed_matview
    client = make_app(seed=seed).test_client()
    text = _visible_text(client.get("/about").get_data(as_text=True))
    match = re.search(r"Local receipts:\s*(\d{4}-\d{2}-\d{2})", text)
    assert match is not None, "the Data Status strip shows no local receipt date"
    assert match.group(1) == D1
    assert ARCHIVED_LATER not in text
    assert TRAP_DATE not in text


@pytest.mark.parametrize("with_trap_view", [False, True], ids=["compat-view-absent", "compat-view-trap"])
def test_candidates_landing_freshness_reads_the_matview(make_app, with_trap_view):
    seed = _seed_matview_and_trap_view if with_trap_view else _seed_matview
    client = make_app(seed=seed).test_client()
    response = client.get("/candidates")
    assert response.status_code == 200
    text = _visible_text(response.get_data(as_text=True))
    match = re.search(r"Latest receipt date:\s*(\d{4}-\d{2}-\d{2})", text)
    assert match is not None, "the landing page shows no latest receipt date"
    assert match.group(1) == D1
    assert ARCHIVED_LATER not in text
    assert TRAP_DATE not in text
    # The time-scope bar reads data_through from the same snapshot.
    assert "latest ISBE data: Mar 15, 2026" in text


def test_banner_builder_tolerates_a_database_without_the_matview(make_app):
    """Guard (green today): rewriting the query must keep the table-exists guard, or a fresh DB loses the strip."""
    app = make_app()
    conn = get_db(app.config["DATABASE_PATH"])
    status = app_module._build_global_data_status(conn, local_stale_days=45, federal_stale_days=14)
    conn.close()
    assert status["local_receipt_date"] is None


def test_candidates_landing_renders_without_the_matview(make_app):
    """Guard (green today): the freshness block raises on PostgreSQL if it queries a table that is not there."""
    response = make_app().test_client().get("/candidates")
    assert response.status_code == 200
    text = _visible_text(response.get_data(as_text=True))
    assert "Latest receipt date: Not loaded" in text


# ---------------------------------------------------------------------------
# (b) L1 -- GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS is wired from the environment
# ---------------------------------------------------------------------------

def _ttl_in_fresh_process(env_value):
    env = {key: value for key, value in os.environ.items() if key != TTL_ENV}
    if env_value is not None:
        env[TTL_ENV] = env_value
    script = textwrap.dedent(
        """
        import json
        import config
        from webapp.app import create_app

        app = create_app({"TESTING": True, "DASHBOARD_PREWARM_ENABLED": False})
        print(json.dumps({
            "config_module": getattr(config, "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS", None),
            "app_config": app.config.get("GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS"),
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_status_cache_ttl_defaults_to_900_seconds():
    values = _ttl_in_fresh_process(None)
    assert values["config_module"] == 900
    assert values["app_config"] == 900


def test_status_cache_ttl_honours_the_environment_variable():
    # Prod's .env sets 900 today and nothing reads it; the effective TTL is a hard-coded 45 s.
    values = _ttl_in_fresh_process("123")
    assert values["app_config"] == 123


def test_zero_ttl_rebuilds_the_banner_on_every_render(make_app, monkeypatch):
    """Guard (green today): the premise of the cache-sensitive tests in this file -- 0 means no banner cache."""
    calls = _install_banner_counter(monkeypatch)
    client = make_app().test_client()
    client.get("/about")
    after_first = calls["count"]
    client.get("/about")
    after_second = calls["count"]
    assert after_first >= 1, "the banner builder never ran"
    assert after_second > after_first, "a second render reused a cached banner although the TTL is 0"


# ---------------------------------------------------------------------------
# (c) L1 -- error pages do not build the banner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    ["/no-such-page-anywhere", "/candidates/999999"],
    ids=["unmatched-url", "abort-404-inside-a-route"],
)
def test_not_found_page_does_not_build_the_banner(make_app, monkeypatch, path):
    calls = _install_banner_counter(monkeypatch)
    client = make_app().test_client()
    assert client.get("/about").status_code == 200       # control: a normal page does build it
    builds_before = calls["count"]
    assert builds_before >= 1, BANNER_CONTROL_MESSAGE
    response = client.get(path)
    assert response.status_code == 404
    assert calls["count"] == builds_before


def test_server_error_page_does_not_build_the_banner(make_app, monkeypatch):
    calls = _install_banner_counter(monkeypatch)
    app = make_app()
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    assert client.get("/about").status_code == 200       # control
    builds_before = calls["count"]
    assert builds_before >= 1, BANNER_CONTROL_MESSAGE
    response = client.get("/_hygiene/boom")
    assert response.status_code == 500
    assert calls["count"] == builds_before


# ---------------------------------------------------------------------------
# (d) L7 / LD1 -- /robots.txt
# ---------------------------------------------------------------------------

def _robots_response(make_app):
    return make_app().test_client().get("/robots.txt")


def _star_group_disallows(body: str) -> list[str]:
    """Disallow values of every group whose User-agent list contains '*' (comments stripped)."""
    groups = []                    # (agents, disallows)
    agents: list[str] = []
    disallows: list[str] = []
    last_was_agent = False
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if not last_was_agent:
                if agents:
                    groups.append((agents, disallows))
                agents, disallows = [], []
            agents.append(value)
            last_was_agent = True
        else:
            last_was_agent = False
            if field == "disallow":
                disallows.append(value)
    if agents:
        groups.append((agents, disallows))
    star_disallows: list[str] = []
    for group_agents, group_disallows in groups:
        if "*" in group_agents:
            star_disallows.extend(group_disallows)
    return star_disallows


def test_robots_txt_is_served_as_plain_text_for_all_agents(make_app):
    response = _robots_response(make_app)
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert "User-agent: *" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "required",
    [LD1_DISALLOWED_PATHS, LD1_REFRESH_PATTERNS, LD1_SCOPE_PATTERNS],
    ids=["paths", "cache-busting-patterns", "scope-variant-patterns"],
)
def test_robots_txt_star_group_carries_every_ld1_rule(make_app, required):
    response = _robots_response(make_app)
    assert response.status_code == 200
    disallowed = _star_group_disallows(response.get_data(as_text=True))
    missing = [rule for rule in required if rule not in disallowed]
    assert missing == []


def test_robots_txt_keeps_the_public_pages_crawlable(make_app):
    response = _robots_response(make_app)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "/" not in _star_group_disallows(body)         # Disallow: / would hide the whole site
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    parser.modified()
    blocked = [path for path in CRAWLABLE_PATHS if not parser.can_fetch("*", path)]
    assert blocked == []


# ---------------------------------------------------------------------------
# (e) L7 / LD2 -- edge-cache header and Vary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_anonymous_public_page_carries_exactly_the_edge_cache_header(make_app, path):
    response = make_app().test_client().get(path)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert _cache_tokens(response) == _edge_tokens(300)


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_anonymous_public_page_does_not_vary_on_cookie(make_app, path):
    response = make_app().test_client().get(path)
    assert response.status_code == 200
    assert "cookie" not in _vary_tokens(response)


def test_edge_cache_seconds_follow_the_config(make_app):
    response = make_app(HTML_EDGE_CACHE_SECONDS=120).test_client().get("/about")
    assert response.status_code == 200
    assert _cache_tokens(response) == _edge_tokens(120)


def test_edge_cache_header_is_absent_when_seconds_is_zero(make_app):
    response = make_app(HTML_EDGE_CACHE_SECONDS=0).test_client().get("/about")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") is None


@pytest.mark.parametrize(
    "kind",
    ["logged-in", "anonymous-session-with-csrf-token", "unsigned-garbage-cookie"],
)
def test_requests_carrying_the_session_cookie_are_not_edge_cacheable(make_app, kind):
    app = make_app()
    client = _client_with_session(app, kind)
    response = client.get("/about")
    assert response.status_code == 200
    _assert_not_edge_cacheable(response)


def test_unrelated_cookies_do_not_switch_the_edge_cache_header_off(make_app):
    client = make_app().test_client()
    client.set_cookie("_ga", "GA1.2.1234567890.1700000000")
    client.set_cookie("cf_clearance", "challenge-token")
    response = client.get("/about")
    assert response.status_code == 200
    assert _cache_tokens(response) == _edge_tokens(300)
    assert "cookie" not in _vary_tokens(response)


@pytest.mark.parametrize(
    "path",
    ["/no-such-page-anywhere", "/candidates/999999"],
    ids=["unmatched-url", "abort-404-inside-a-route"],
)
def test_not_found_pages_carry_the_edge_cache_header(make_app, path):
    response = make_app().test_client().get(path)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert _cache_tokens(response) == _edge_tokens(300)


@pytest.mark.parametrize("kind", ["logged-in", "unsigned-garbage-cookie"])
def test_not_found_page_for_a_request_carrying_the_session_cookie_is_not_edge_cacheable(make_app, kind):
    app = make_app()
    client = _client_with_session(app, kind)
    response = client.get("/no-such-page-anywhere")
    assert response.status_code == 404
    _assert_not_edge_cacheable(response)


@pytest.mark.parametrize(
    "path, status, mimetype",
    [
        ("/_hygiene/boom", 500, "text/html"),
        ("/admin/", 302, "text/html"),
        ("/api/stats", 200, "application/json"),
        ("/527/suggest?q=ab", 200, "application/json"),
        ("/static/css/style.css", 200, "text/css"),
        ("/auth/login", 200, "text/html"),
        ("/experimental/viz-lab", 200, "text/html"),
    ],
    ids=[
        "server-error",
        "login-redirect",
        "api-json",
        "public-blueprint-json",
        "static-css",
        "login-page-sets-cookie",
        "experimental-page",
    ],
)
def test_responses_outside_the_ld2_rule_never_carry_the_edge_cache_header(make_app, path, status, mimetype):
    app = make_app()
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = app.test_client().get(path)
    assert response.status_code == status
    assert response.mimetype == mimetype
    _assert_not_edge_cacheable(response)


# ---------------------------------------------------------------------------
# (f) L7 / LD4 -- refresh parameters are for signed-in users only
# ---------------------------------------------------------------------------

# The raw spellings each route treats as "on": refresh_cache is int()-parsed (so 01 and a decoded " 1" count),
# refresh_full / sync_full go through _is_true_arg (1, true, yes, on, any case).
REFRESH_QUERIES = [
    "refresh_cache=1",
    "refresh_cache=01",
    "refresh_cache=+1",
    "refresh_full=1",
    "refresh_full=TRUE",
    "refresh_full=yes",
    "sync_full=1",
    "sync_full=On",
]


def _location_parts(response):
    location = urlsplit(response.headers["Location"])
    return location.path, sorted(parse_qsl(location.query, keep_blank_values=True))


@pytest.mark.parametrize("query", REFRESH_QUERIES)
def test_anonymous_refresh_request_is_redirected_to_the_same_url_without_it(make_app, query):
    client = make_app().test_client()
    response = client.get(f"/analytics/?{query}")
    assert response.status_code == 302
    path, pairs = _location_parts(response)
    assert path == "/analytics/"
    assert pairs == []
    _assert_not_edge_cacheable(response)


def test_redirect_keeps_every_other_parameter_and_drops_all_three_in_one_hop(make_app):
    client = make_app().test_client()
    url = (
        "/analytics/?refresh_cache=1&q=a%26b%20c&tag=x&floor=&period=all"
        "&tag=y&refresh_full=true&sync_full=1"
    )
    response = client.get(url)
    assert response.status_code == 302
    path, pairs = _location_parts(response)
    assert path == "/analytics/"
    assert pairs == [("floor", ""), ("period", "all"), ("q", "a&b c"), ("tag", "x"), ("tag", "y")]


@pytest.mark.parametrize("path", ["/federal-finance/", "/527/dark-money", "/analytics/risk"])
def test_the_gate_covers_other_blueprints_not_just_the_analytics_overview(make_app, path):
    client = make_app().test_client()
    assert client.get(path).status_code == 200            # control: the route renders in this fixture
    response = client.get(f"{path}?period=all&refresh_cache=1")
    assert response.status_code == 302
    location_path, pairs = _location_parts(response)
    assert location_path == path
    assert pairs == [("period", "all")]


@pytest.mark.parametrize("method", ["get", "head"])
def test_redirect_happens_before_the_route_runs(make_app, monkeypatch, method):
    """HEAD is dispatched to the GET view by Flask, so a gate that only looks at GET lets link checkers rebuild."""
    calls = {"count": 0}
    original = analytics_routes._parse_filters

    def spy(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analytics_routes, "_parse_filters", spy)
    app = make_app()
    anonymous = app.test_client()
    assert getattr(anonymous, method)("/analytics/?refresh_cache=1").status_code == 302
    assert calls["count"] == 0
    signed_in = app.test_client()
    _login(signed_in)
    assert getattr(signed_in, method)("/analytics/?refresh_cache=1").status_code == 200
    assert calls["count"] >= 1                             # control: the spy sees a request that does reach the route


@pytest.mark.parametrize("kind", ["anonymous-session-with-csrf-token", "unsigned-garbage-cookie"])
def test_a_session_cookie_without_a_signed_in_user_does_not_unlock_refresh(make_app, kind):
    app = make_app()
    client = _client_with_session(app, kind)
    response = client.get("/analytics/?refresh_cache=1")
    assert response.status_code == 302
    path, pairs = _location_parts(response)
    assert path == "/analytics/"
    assert pairs == []


@pytest.mark.parametrize("query", ["refresh_cache=1", "refresh_full=1", "sync_full=1"])
def test_signed_in_user_is_not_redirected(make_app, query):
    app = make_app()
    client = app.test_client()
    _login(client)
    response = client.get(f"/analytics/?{query}")
    assert response.status_code == 200
    assert response.headers.get("Location") is None


# ---------------------------------------------------------------------------
# (g) L14 -- /candidates/ is not a 404
# ---------------------------------------------------------------------------

def _first_h1(page: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.S | re.I)
    assert match is not None, "the page has no <h1>"
    return _visible_text(match.group(1))


def test_candidates_trailing_slash_serves_the_landing_page(make_app):
    client = make_app().test_client()
    reference = client.get("/candidates")
    assert reference.status_code == 200
    expected_heading = _first_h1(reference.get_data(as_text=True))
    response = client.get("/candidates/")
    assert response.status_code != 404
    if response.status_code == 200:
        landing = response
    else:
        assert response.status_code in (301, 302, 307, 308)
        assert urlsplit(response.headers["Location"]).path == "/candidates"
        landing = client.get(response.headers["Location"])
        assert landing.status_code == 200
    assert _first_h1(landing.get_data(as_text=True)) == expected_heading


# ---------------------------------------------------------------------------
# R1 -- review-fix round 2026-10-28 (plan: .claude/plans/load-times.md, "Review fixes", rows C4 and C6)
# ---------------------------------------------------------------------------
#
# C4  gate_refresh_params builds its 302 from request.path, the DECODED path, and nothing re-quotes it. The Location is
#     then parsed again by Werkzeug (which, like a browser's URL parser, drops tab / CR / LF) and by the visitor's
#     browser:
#         /%09/evil.example   decodes to '/<TAB>/evil.example'  -> Location '//evil.example' = another host
#         /%0a/x              decodes to '/<LF>/x'              -> Werkzeug refuses the header value -> 500
#         /a%3Fb  /a%23b  /a%25b   decode to '/a?b'  '/a#b'  '/a%b' -> the '?', '#', '%' are read as URL syntax
#     Contract: the Location path is the request's own path, percent-encoded (the plan builds it with
#     quote(request.path, safe="/")), so the redirect always points at the same URL minus the refresh keys.
#     Hand-computed Locations (quote, then the kept query):
#         GET /%09/evil.example?refresh_cache=1   -> /%09/evil.example      (TAB is 0x09)
#         GET /%0a/x?refresh_cache=1              -> /%0A/x                 (LF is 0x0A; quote writes upper-case hex)
#         GET /%0d%0a/x?refresh_cache=1           -> /%0D%0A/x              (CR is 0x0D)
#         GET /a%3Fb?refresh_cache=1&q=1          -> /a%3Fb?q=1             ('?' is 0x3F)
#         GET /a%23b?refresh_cache=1&q=1          -> /a%23b?q=1             ('#' is 0x23)
#         GET /a%25b?refresh_cache=1&q=1          -> /a%25b?q=1             ('%' is 0x25)
#         GET /a%20b?refresh_cache=1&q=1          -> /a%20b?q=1             (pin: passes today)
#         GET /caf%C3%A9?refresh_cache=1&q=1      -> /caf%C3%A9?q=1         (pin: UTF-8 C3 A9 = e acute; passes today)
#     Interpretation choices (flagged in the tester report; relax by editing one test):
#       * The Location PATH is pinned, not only "stays on this origin": LD4 promises "the same URL without the refresh
#         keys", and a fix that redirects hostile paths to '/' or collapses '/<TAB>/' into '/' would satisfy a pure
#         origin check.
#       * Hex digits are upper-case (what quote() writes).
#
# C6  A /search response whose sections timed out or failed (search_meta timed_out_sections / errored_sections: the app
#     already refuses to store such a page in its own RouteCache) is answered 200 with the PUBLIC edge header, so the
#     edge pins the degraded page for HTML_EDGE_CACHE_SECONDS. Contract: such a page carries exactly
#     "Cache-Control: no-store"; a healthy page (even one with no results at all) keeps the LD2 header.
#     The route-level search cache is off in these tests (ROUTE_PERF_CACHE_ENABLED False, as everywhere in this file),
#     so a stored healthy payload can never shadow the forced failure. SEARCH_QUERY_TIMEOUT_MS is raised for the same
#     reason test_search_perf.py raises it: only the section the test breaks may degrade.

def _assert_redirect_target(response, expected_path: str, expected_query: str = "") -> None:
    """The refresh redirect leaves for this site, at the request's own path. urlsplit drops tab / CR / LF the way a
    browser's URL parser does, so a Location that survives the checks below cannot be read as another host."""
    location = response.headers["Location"]
    parts = urlsplit(location)
    assert not location.startswith("//"), (
        f"Location {location!r} is protocol-relative: a browser follows it to another host (open redirect, "
        "no login needed). Percent-encode the decoded request.path before building the Location."
    )
    assert parts.scheme == "", f"Location {location!r} names a scheme"
    assert parts.netloc == "", f"Location {location!r} names a host ({parts.netloc!r})"
    assert parts.path == expected_path, (
        f"Location {location!r}: path {parts.path!r}, expected the request's own path {expected_path!r} "
        "(percent-encoded), so the redirect is the same URL minus the refresh keys"
    )
    assert parts.query == expected_query, f"Location {location!r}: query {parts.query!r}, expected {expected_query!r}"
    assert parts.fragment == "", f"Location {location!r}: fragment {parts.fragment!r}, expected none"


def test_a_tab_in_the_path_cannot_turn_the_refresh_redirect_into_an_open_redirect(make_app):
    """GET /%09/evil.example?refresh_cache=1: the decoded path is '/<TAB>/evil.example'. Werkzeug drops the tab when it
    normalises the Location, which then reads '//evil.example' -- a protocol-relative URL -- so ANY visitor (no login)
    can be bounced to another host through a link on this site.

    Kills: the redirect built from the decoded path (today); stripping control characters from the path (leaves
    '//evil.example'); collapsing the leading slashes, or redirecting hostile paths to '/' (a pure origin check would
    pass those; the path is pinned too)."""
    response = make_app().test_client().get("/%09/evil.example?refresh_cache=1")
    assert response.status_code == 302
    _assert_redirect_target(response, "/%09/evil.example")


@pytest.mark.parametrize(
    "raw_path, expected_path",
    [("/%0a/x", "/%0A/x"), ("/%0d%0a/x", "/%0D%0A/x")],
    ids=["lf", "crlf"],
)
def test_a_newline_in_the_path_gets_a_302_not_a_500(make_app, raw_path, expected_path):
    """The decoded path holds a raw LF (or CR LF). Put in the Location header it makes Werkzeug raise 'Header values
    must not contain newline characters' -> a 500 for any anonymous visitor (and the shape of a header injection).

    Kills: the unquoted path (today: 500); catching the ValueError and answering 400, or skipping the redirect so the
    request falls through to the 404 page (the contract is the 302); stripping the newline, which leaves '//x' (an
    open redirect) or a different URL."""
    app = make_app()
    app.config["PROPAGATE_EXCEPTIONS"] = False             # a crash becomes a 500 response, as in the LD2 test above
    response = app.test_client().get(f"{raw_path}?refresh_cache=1")
    assert response.status_code == 302, (
        f"status {response.status_code}, expected the LD4 redirect: the decoded path holds a raw newline, which "
        "Werkzeug refuses in a header value (ValueError -> 500). Percent-encode the path before building the Location."
    )
    _assert_redirect_target(response, expected_path)


@pytest.mark.parametrize("path", ["/a%3Fb", "/a%23b", "/a%25b"], ids=["question-mark", "hash", "percent"])
def test_reserved_characters_in_the_path_survive_the_refresh_redirect(make_app, path):
    """Today the decoded path is glued to the kept query unquoted, so the Location for /a%3Fb is '/a?b?q=1' (path /a,
    query 'b?q=1'), for /a%23b it is '/a#b?q=1' (path /a, fragment 'b?q=1') and for /a%25b it is '/a%b?q=1' (a stray
    '%'): the redirect lands on another URL and drops or mangles the kept parameters.

    Kills: a fix that escapes only '?' (or only one of the three); encoding the slashes too (quote without safe="/",
    '%2Fa%3Fb'); the unquoted path (today)."""
    response = make_app().test_client().get(f"{path}?refresh_cache=1&q=1")
    assert response.status_code == 302
    _assert_redirect_target(response, path, expected_query="q=1")


@pytest.mark.parametrize("path", ["/a%20b", "/caf%C3%A9"], ids=["space", "utf8"])
def test_ordinary_encodings_in_the_path_are_not_double_encoded_by_the_refresh_redirect(make_app, path):
    """(pin) Passes today. The redirect re-encodes the DECODED path once, never an already-encoded one, and writes a
    space as %20, not '+'. Kills: quote_plus ('/a+b'); quoting PATH_INFO instead of request.path ('/caf%C3%83%C2%A9');
    quoting the raw request target again ('/a%2520b')."""
    response = make_app().test_client().get(f"{path}?refresh_cache=1&q=1")
    assert response.status_code == 302
    _assert_redirect_target(response, path, expected_query="q=1")


# C6 -- the donors section is the one broken on purpose: GET /search runs it for type=all (and type=donors), and it is
# looked up as a module global of webapp.routes.main when the section runs, so a monkeypatch reaches it.
# search.html prints one notice per degraded kind; the notice text is used below as a CONTROL that the page really is
# in the state the fix has to treat as degraded (errored sections and timed-out sections are separate lists).
DEGRADED_SEARCH_MODES = [
    (RuntimeError, "Some sections failed and were skipped: donors"),
    (psycopg.errors.QueryCanceled, "Some sections timed out and were skipped: donors"),
]
DEGRADED_SEARCH_IDS = ["errored", "timed-out"]


@pytest.mark.parametrize("failure, notice", DEGRADED_SEARCH_MODES, ids=DEGRADED_SEARCH_IDS)
def test_a_degraded_search_page_is_not_edge_cacheable(make_app, monkeypatch, failure, notice):
    """A /search page with a failed or timed-out section is answered 200, and the app refuses to store it in its own
    cache -- but today add_edge_cache_header still stamps 'public, max-age=0, s-maxage=300' on it, so the edge keeps
    the partial page for five minutes. Timeouts (the 700 ms section budget) are the COMMON way a page degrades, so
    both kinds are pinned.

    Kills: no change (today); 'private' / 'no-cache' / a missing header instead of exactly 'no-store'; extra directives
    next to no-store; flagging only the errored list or only the timed-out list."""
    calls = {"count": 0}

    def failing_donors_section(*args, **kwargs):
        calls["count"] += 1
        raise failure("search-donors probe: forced failure")

    monkeypatch.setattr(main_routes, "_search_donors_source_aware", failing_donors_section)
    client = make_app(SEARCH_QUERY_TIMEOUT_MS=30000).test_client()
    response = client.get("/search?q=jones")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert calls["count"] == 1, "control: the broken donors section must have run exactly once"
    assert notice in page, f"control: the page must report the degraded section ({notice!r})"
    cache_control = response.headers.get("Cache-Control", "")
    assert "s-maxage" not in cache_control, (
        f"a degraded search page was answered Cache-Control {cache_control!r}: the edge cache would keep it. "
        "search() has to flag the request (g._no_edge_cache) and add_edge_cache_header has to emit 'no-store'."
    )
    assert "public" not in cache_control, f"Cache-Control {cache_control!r} still marks the degraded page public"
    assert _cache_tokens(response) == {"no-store"}, f"Cache-Control {cache_control!r}, expected exactly 'no-store'"


def test_a_healthy_search_page_is_still_edge_cacheable(make_app):
    """(pin) Passes today. The test schema is empty, so every section returns no rows: an EMPTY page is healthy and
    keeps the LD2 header. Kills: no-store on every /search response; treating 'no results' as degraded."""
    response = make_app(SEARCH_QUERY_TIMEOUT_MS=30000).test_client().get("/search?q=jones")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "Some sections" not in page, "control: no section may be reported as failed or timed out"
    assert _cache_tokens(response) == _edge_tokens(300)


def test_a_degraded_search_does_not_make_later_responses_uncacheable(make_app, monkeypatch):
    """(pin) Passes today. The degraded state belongs to ONE request. Kills: a flag kept in a module global or in
    app.config, which leaves every later response no-store until the worker restarts -- including a flag that only
    search() resets, which the /about request right after the degraded search exposes (a healthy search in between
    would hide it) -- and a decision remembered per query. The follow-up search uses type=committees, so it does not
    run the broken donors section."""
    calls = {"count": 0}

    def failing_donors_section(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("search-donors probe: forced failure")

    monkeypatch.setattr(main_routes, "_search_donors_source_aware", failing_donors_section)
    client = make_app(SEARCH_QUERY_TIMEOUT_MS=30000).test_client()
    degraded = client.get("/search?q=jones")
    assert degraded.status_code == 200
    assert calls["count"] == 1, "control: the first request must have run the broken donors section"
    assert "Some sections failed" in degraded.get_data(as_text=True), "control: the first page must be degraded"

    about = client.get("/about")
    assert about.status_code == 200
    assert _cache_tokens(about) == _edge_tokens(300)

    healthy_search = client.get("/search?q=jones&type=committees")
    assert healthy_search.status_code == 200
    assert calls["count"] == 1, "control: type=committees must not run the donors section"
    assert "Some sections" not in healthy_search.get_data(as_text=True), "control: the follow-up page must be healthy"
    assert _cache_tokens(healthy_search) == _edge_tokens(300)


# --- LD2, Flask-version independence (CI 2026-10-28: Flask 3.1.3 marks the session "accessed" on any
# touch of the proxy, so base.html's get_flashed_messages() gave every anonymous page "Vary: Cookie";
# Flask 3.0 only counted key reads). A cookieless request that neither reads a cookie nor needs to set
# one must not vary on Cookie whatever Flask does; a request that carries the session cookie, or a
# response that sets one, must.

def _session_probe_app(make_app):
    import flask

    app = make_app()

    @app.route("/_probe/session-read")
    def _probe_session_read():
        flask.session.get("nothing")  # marks the session accessed on every Flask version
        return flask.Response("<p>ok</p>", mimetype="text/html")

    @app.route("/_probe/session-flash")
    def _probe_session_flash():
        flask.flash("hello")
        return flask.Response("<p>ok</p>", mimetype="text/html")

    return app


def test_a_cookieless_request_that_reads_the_session_does_not_vary_on_cookie(make_app):
    response = _session_probe_app(make_app).test_client().get("/_probe/session-read")
    assert response.status_code == 200
    assert "cookie" not in _vary_tokens(response), "a request without a session cookie read nothing from a cookie"
    assert "Set-Cookie" not in response.headers


def test_a_request_carrying_the_session_cookie_still_varies_on_cookie(make_app):
    app = _session_probe_app(make_app)
    client = app.test_client()
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], "not-a-real-session")
    response = client.get("/_probe/session-read")
    assert response.status_code == 200
    assert "cookie" in _vary_tokens(response)


def test_a_flash_on_a_cookieless_request_still_sets_the_cookie_and_varies(make_app):
    app = _session_probe_app(make_app)
    response = app.test_client().get("/_probe/session-flash")
    assert response.status_code == 200
    assert app.config["SESSION_COOKIE_NAME"] in response.headers.get("Set-Cookie", "")
    assert "cookie" in _vary_tokens(response)
