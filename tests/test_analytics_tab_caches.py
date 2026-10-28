"""Tests-first suite for plan step P3 (load-times L5): /analytics/geography and /analytics/donors get a RouteCache.

Plan: .claude/plans/load-times.md -- ranked fix L5 and the "P3 contract (L5)" paragraph under "Unit designs".

Why: neither page has a cache.  Every hit re-runs get_geo_summary + get_time_series (geography) or get_donor_concentration +
get_nlp_spending_summary (donors); each loads the whole donor->committee row set of the window into Python (1.3 s / 1.1 s
local, 3.6 s / 3.0 s prod), and both pages are crawler-exposed: a window plus four limit arguments, so the key space cannot be
prewarmed -- only bounded by a key that carries exactly what the payload depends on.

Pinned contract (P3):

  * the payloads come from RouteCache("analytics_geography") / RouteCache("analytics_donors"), keyed on
    (scope_cache_key(scope), date_from, date_to, every limit / months argument the route reads): one entry per distinct
    request, shared by every gunicorn worker through Redis (a per-process dict is the anti-pattern L4 removes);
  * TTL = ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS / ANALYTICS_DONORS_CACHE_TTL_SECONDS: config.py reads them from the
    environment (default 21600 = ANALYTICS_CACHE_TTL_FALLBACK_SECONDS) and create_app copies them into app.config like the
    other ANALYTICS_*_CACHE_TTL_SECONDS keys -- otherwise the environment value is silently ignored (the L1 finding), and the
    routes read the right key (not the risk page's, which the new code is copied from);
  * ROUTE_PERF_CACHE_ENABLED and ?refresh_cache=1 behave as on the risk page;
  * warm_analytics_caches fills both entries for the default scope, under the key and with the arguments of a bare request.

How builds are observed: the four builders are replaced on webapp.routes.analytics (house pattern,
tests/test_app_performance.py) by spies that bind every call to the real signature (defaults applied, so an omitted argument
shows its default) and either
  * return a labelled fake whose text spells out the builder and the call serial ("get_geo_summary|call=2|"), so "this response
    embeds the SECOND build" is read from the response itself, not only from a counter (the `builders` fixture), or
  * pass through to the real builder and only count (the `real_builders` fixture), for the tests that need real figures.
The spies replace the attributes the routes call; the route must keep calling the builders through those names.  Sections A-D
pin quick-mode requests only: the geography and donors keys exclude load_mode, so the mode is not a dimension of those two
caches (see the accepted holes).  A full-mode GET no longer starts a background snapshot thread (plan LD9: it reads the stored
snapshot, or computes on the request thread without one), so a spy would see its builder calls; the overview, whose key does
carry the mode, is pinned in full mode in section E.

Caches: Redis is forced off in every test (house technique, tests/test_cache_backend.py) and every RouteCache in the process is
emptied around each test, found with gc so a cache added anywhere is cleared too.  Tests about the shared layer install a
FakeRedis (the slice of redis-py that RouteCache uses) and read keys, TTLs and values off it.  create_app forces
ROUTE_PERF_CACHE_ENABLED False under TESTING unless the config says otherwise, so the fixtures switch it on explicitly.

Fixtures are tests/test_analytics_features.py::analytics_app (anonymous ?refresh_cache=1 works: REFRESH_PARAMS_REQUIRE_LOGIN
False) and tests/test_scope_widgets.py::w_analytics_app (real figures), topped up here as w_history_app with one 2024
contribution so that cycle 2026, all time and cycle 2024 show three different sets of figures.  The resolver clock is pinned to
2026-10-26 by analytics_app: "this cycle" = cycle 2026 = 2025-01-01..2026-12-31 (24 months).

Red today (no cache anywhere): everything except test_nothing_is_cached_when_route_caches_are_off (a GUARD, both pages),
which pins a neighbour a lazy cache would break.

Section E (review fix R4, plan "Review fixes", findings C15 / C3) pins the OVERVIEW's cache key, which omits recon_limit,
recon_min_abs_diff and, in full mode, the mode itself and the two geo limits.  Unit level: _overview_cache_key on a hand-built
filters dict.  Route level: GET /analytics/ and its variants through the real _parse_filters, counting the seven builders the
overview reads (pass-through spies: the page renders real figures).  Prewarm: the key under which warm_analytics_caches files
its entry, against a bare request's key, through the FakeRedis (entries are filed under sha1(repr(key)), so a key that compares
equal but spells differently, 1000 against 1000.0, is a miss that the in-process dict hides).  Red today: the separation tests.
Green today (PIN, they guard the fix): the fields the key already carries, quick mode ignoring the geo limits and unrelated
parameters, a deterministic key, and the prewarm agreeing with the route.

Accepted holes (named, not silently dropped):
  * `?load_mode=full` and the stored-snapshot read are not exercised against the geography and donors caches (sections A-D are
    quick mode only): their keys exclude load_mode like the risk page's, so a quick-mode entry may serve a full-mode request --
    the page then renders from it exactly as the risk page does.  The overview's key does carry the mode; section E pins it.
  * The signed-in variant of ?refresh_cache=1 is not run (the fixture turns the login gate off; the gate itself belongs to
    tests/test_request_hygiene.py).
  * create_app's prewarm thread (gated off under TESTING) passing the two new TTLs to warm_analytics_caches is not run; the
    warm's own default TTL is pinned instead.
  * The L5 row's "share one _get_donor_committee_rows result between the two pages" is a speed-up the P3 contract does not
    pin and no HTTP-visible behaviour separates; not tested.
  * Whether get_nlp_spending_summary (it takes no window) is cached per window or once for all windows is left open: the
    donors tests never count NLP builds across windows.
  * Redis TTLs are accepted anywhere in [ttl, 2 * ttl]: plan P7 stores stale-while-revalidate entries at twice the TTL.
  * How many Redis entries a request writes is not counted: one combined entry per request (the contract's wording) and one
    entry per builder both pass.
  * Isolation between the two NEW prewarm blocks is not pinned (a prewarm that fetches the donor->committee rows once for both
    pages may fail them together); only that the already existing caches survive a failing geography build.
  * Not covered by P3 at all: the four /api/analytics/* JSON endpoints behind the pages' "JSON endpoint" links call the same
    builders uncached (plan L7 handles /api/ with robots.txt and keys), and concurrent misses still build twice (plan P7's
    single-flight).
"""
from __future__ import annotations

import gc
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import NamedTuple

import pytest

import webapp.cache_backend as cache_backend

# Imported at collection time on purpose (as tests/test_scope_widgets.py): webapp/routes/analytics.py binds get_db when it is
# first imported, which would otherwise happen inside the first test that builds an app, after conftest has patched get_db.
import webapp.routes.analytics as analytics_routes  # noqa: E402
from tests.test_analytics_features import analytics_app  # noqa: E402,F401
from tests.test_scope_widgets import w_analytics_app  # noqa: E402,F401
from webapp.cache_backend import RouteCache

REPO_ROOT = Path(__file__).resolve().parents[1]

GEO = "get_geo_summary"
SERIES = "get_time_series"
CONC = "get_donor_concentration"
NLP = "get_nlp_spending_summary"
ALL_BUILDERS = (GEO, SERIES, CONC, NLP)

# Windows.  The default one is cycle 2026 on the pinned clock; "all time" reaches the builders as '' / ''.
DEFAULT_FROM, DEFAULT_TO = "2025-01-01", "2026-12-31"
DEFAULT_MONTHS = 24          # (2026 - 2025) * 12 + 12 - 1 + 1
ALL_TIME_MONTHS = 120        # an unbounded window spans the 120-month ceiling
FEB_TO_MAR = "date_from=2026-02-01&date_to=2026-03-31"

FALLBACK_TTL = 21600         # ANALYTICS_CACHE_TTL_FALLBACK_SECONDS = config.py's 6 h


class Page(NamedTuple):
    name: str
    url: str
    cache: str                       # RouteCache name pinned by the contract
    ttl_key: str                     # app.config key
    builders: tuple[str, ...]        # everything the page renders from the cached payload
    windowed: tuple[str, ...]        # the builders that take date_from / date_to


GEOGRAPHY = Page(
    "geography", "/analytics/geography", "analytics_geography", "ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS",
    builders=(GEO, SERIES), windowed=(GEO, SERIES),
)
DONORS = Page(
    "donors", "/analytics/donors", "analytics_donors", "ANALYTICS_DONORS_CACHE_TTL_SECONDS",
    builders=(CONC, NLP), windowed=(CONC,),
)
PAGES = [pytest.param(GEOGRAPHY, id="geography"), pytest.param(DONORS, id="donors")]

# The arguments each builder is called with, as the route passes them today (limit_states <- ?geo_state_limit, ...).  The two
# builders that can take a pre-fetched row set instead of dates (get_geo_summary, get_donor_concentration) are projected without
# their dates: a prewarm may legitimately fetch the rows once and hand them over.  The window of what the prewarm stores is
# pinned by the figures of the history fixture instead.
PROJECTION = {
    GEO: ("limit_states", "limit_cities"),
    SERIES: ("months", "date_from", "date_to"),
    CONC: ("limit",),
    NLP: ("limit",),
}

# ---------------------------------------------------------------------------
# w_history_app figures, hand-computed.  Contributions (amounts from tests/test_analytics_features.py's seed plus the top-ups of
# w_analytics_app (Frank) and w_history_app (Old)):
#   Alice (Chicago IL)     2026: Jan 1,000   Feb 800   Mar 900   Apr 10,000     -> Committee One
#   Bob (Springfield IL)   2026: Jan 500               Mar 300                  -> Committee One
#   Carol (Naperville IL)  2026:           Feb 400                              -> Committee One
#   Eve (Chicago IL)       2026: Jan 250                                        -> Committee One
#   Frank (Peoria IL)      2026: Jan 120                                        -> Committee One
#   Dave (St. Louis MO)    2026: Jan 2,000 Feb 1,800                            -> Committee Two
#   Old (Rockford IL)      2024-06-15: 777                                      -> Committee One
# Cycle 2026 (2025-01-01..2026-12-31, the default window and what the prewarm must hold) has everything but Old:
#   states   IL 12,700 + 800 + 400 + 250 + 120 = 14,270 ; MO 2,000 + 1,800 = 3,800
#   cities   Chicago 12,700 + 250 = 12,950 ; St Louis 3,800 (the display form drops the period) ; Springfield 800 ;
#            Naperville 400 ; Peoria 120                                   (largest first)
#   months   Jan 1,000 + 500 + 250 + 2,000 + 120 = 3,870 ; Feb 800 + 400 + 1,800 = 3,000 ; Mar 900 + 300 = 1,200 ; Apr 10,000
#   concentration  Committee One 14,270 over 5 donors, Committee Two 3,800 over 1 donor; ordered by HHI, highest first, and a
#                  single donor has HHI 10,000 -> Committee Two first
# All time adds Old: IL 14,270 + 777 = 15,047 ; Rockford 777 (between Springfield 800 and Naperville 400) ; month 2024-06 first ;
#   Committee One 14,270 + 777 = 15,047 over 6 donors (still below Committee Two's HHI of 10,000).
# Cycle 2024 (2023-01-01..2024-12-31) holds only Old: IL 777 ; Rockford 777 ; month 2024-06 ; Committee One 777 over 1 donor.
# The window 2026-02-01..2026-03-31 (February + March):
#   states   IL 800 + 400 + 900 + 300 = 2,400 ; MO 1,800
#   cities   St Louis 1,800 ; Chicago 800 + 900 = 1,700 ; Naperville 400 ; Springfield 300
#   months   Feb 800 + 400 + 1,800 = 3,000 ; Mar 900 + 300 = 1,200
#   concentration  Committee One 2,400 over 3 donors (Alice 1,700, Carol 400, Bob 300), Committee Two 1,800 over 1 donor
# ---------------------------------------------------------------------------
CYCLE_2026_STATES = [("IL", 14270.0), ("MO", 3800.0)]
CYCLE_2026_CITIES = [
    ("Chicago", "IL", 12950.0), ("St Louis", "MO", 3800.0), ("Springfield", "IL", 800.0),
    ("Naperville", "IL", 400.0), ("Peoria", "IL", 120.0),
]
CYCLE_2026_SERIES = [("2026-01", 3870.0), ("2026-02", 3000.0), ("2026-03", 1200.0), ("2026-04", 10000.0)]
CYCLE_2026_CONCENTRATION = [("Committee Two", "$3,800.00", "1"), ("Committee One", "$14,270.00", "5")]
ALL_TIME_STATES = [("IL", 15047.0), ("MO", 3800.0)]
ALL_TIME_CITIES = [
    ("Chicago", "IL", 12950.0), ("St Louis", "MO", 3800.0), ("Springfield", "IL", 800.0),
    ("Rockford", "IL", 777.0), ("Naperville", "IL", 400.0), ("Peoria", "IL", 120.0),
]
ALL_TIME_SERIES = [("2024-06", 777.0)] + CYCLE_2026_SERIES
ALL_TIME_CONCENTRATION = [("Committee Two", "$3,800.00", "1"), ("Committee One", "$15,047.00", "6")]
CYCLE_2024_STATES = [("IL", 777.0)]
CYCLE_2024_CITIES = [("Rockford", "IL", 777.0)]
CYCLE_2024_SERIES = [("2024-06", 777.0)]
CYCLE_2024_CONCENTRATION = [("Committee One", "$777.00", "1")]
WINDOW_STATES = [("IL", 2400.0), ("MO", 1800.0)]
WINDOW_CITIES = [
    ("St Louis", "MO", 1800.0), ("Chicago", "IL", 1700.0), ("Naperville", "IL", 400.0), ("Springfield", "IL", 300.0),
]
WINDOW_SERIES = [("2026-02", 3000.0), ("2026-03", 1200.0)]
WINDOW_CONCENTRATION = [("Committee Two", "$1,800.00", "1"), ("Committee One", "$2,400.00", "3")]

# (query, geography figures, donor concentration rows, scope bar label) of the three windows the real-data tests use; every
# one shows different figures, and none of them is the default window.
REAL_WINDOWS = [
    (FEB_TO_MAR, (WINDOW_STATES, WINDOW_CITIES, WINDOW_SERIES), WINDOW_CONCENTRATION, "Custom range"),
    ("period=all", (ALL_TIME_STATES, ALL_TIME_CITIES, ALL_TIME_SERIES), ALL_TIME_CONCENTRATION, "All time"),
    ("period=cycle:2024", (CYCLE_2024_STATES, CYCLE_2024_CITIES, CYCLE_2024_SERIES), CYCLE_2024_CONCENTRATION, "2024 Cycle"),
]


# ---------------------------------------------------------------------------
# Caches and Redis
# ---------------------------------------------------------------------------
def _empty_route_caches() -> None:
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()


@pytest.fixture(autouse=True)
def _isolated_route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process RouteCache empty around each test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _empty_route_caches()
    yield
    _empty_route_caches()


class FakeRedis:
    """The slice of redis-py that RouteCache uses; remembers the TTL each key was stored with."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, *keys):
        removed = 0
        for key in keys:
            self.ttls.pop(key, None)
            if self.values.pop(key, None) is not None:
                removed += 1
        return removed

    def scan(self, cursor=0, match=None, count=None):
        prefix = (match or "*").rstrip("*")
        return 0, [key for key in self.values if key.startswith(prefix)]

    def keys_under(self, prefix: str) -> list[str]:
        return sorted(key for key in self.values if key.startswith(prefix))

    def wipe(self) -> None:
        self.values.clear()
        self.ttls.clear()


@pytest.fixture
def shared_cache(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    return fake


def _prefix(page: Page) -> str:
    return f"ilcf:cache:{page.cache}:"


# ---------------------------------------------------------------------------
# Builder spies
# ---------------------------------------------------------------------------
def _label(name: str, serial: int) -> str:
    return f"{name}|call={serial}|"


def _fake(name: str, serial: int):
    label = _label(name, serial)
    if name == GEO:
        return {
            "states": [{"state": label, "total_amount": 10.0, "contribution_count": 1, "donor_count": 1}],
            "cities": [{"city": label, "state": "IL", "total_amount": 10.0, "contribution_count": 1, "donor_count": 1}],
        }
    if name == SERIES:
        return [{"month": label, "total_amount": 10.0, "contribution_count": 1, "moving_avg_3": 10.0, "mom_change_pct": None}]
    if name == CONC:
        return [{
            "committee_id": 1, "committee_name": label, "total_amount": 10.0, "donor_count": 1, "top1_share": 1.0,
            "top5_share": 1.0, "top10_share": 1.0, "hhi": 10000.0, "gini": 0.0, "top_donor_amount": 10.0,
        }]
    return [{"category": label, "total_amount": 10.0, "entry_count": 1, "avg_amount": 10.0, "sample_text": "sample"}]


class Builders:
    """Recorded calls of the four builders, thread safe."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: dict[str, list[dict]] = {name: [] for name in ALL_BUILDERS}
        self.signature_errors: list[str] = []
        self.fail_next: set[str] = set()

    def reset(self) -> None:
        with self.lock:
            self.calls = {name: [] for name in ALL_BUILDERS}
            self.signature_errors = []

    def count(self, name: str) -> int:
        return len(self.calls[name])

    def counts(self, *names: str) -> dict:
        return {name: self.count(name) for name in names}

    def last(self, name: str) -> dict:
        return self.calls[name][-1]

    def projected(self, name: str, index: int) -> dict:
        call = self.calls[name][index]
        return {key: call.get(key) for key in PROJECTION[name]}


def _install_builders(monkeypatch, *, labelled: bool) -> Builders:
    spy = Builders()

    def make(name, real):
        signature = inspect.signature(real)
        connection = next(iter(signature.parameters))

        def wrapper(conn, *args, **kwargs):
            try:
                bound = signature.bind(conn, *args, **kwargs)
                bound.apply_defaults()
                recorded = {key: value for key, value in bound.arguments.items() if key != connection}
                error = None
            except TypeError as exc:
                recorded, error = dict(kwargs), f"{name}: {exc}"
            with spy.lock:
                spy.calls[name].append(recorded)
                serial = len(spy.calls[name])
                should_fail = name in spy.fail_next
                spy.fail_next.discard(name)
                if error:
                    spy.signature_errors.append(error)
            if should_fail:
                raise RuntimeError(f"{name}: simulated build failure")
            if labelled:
                return _fake(name, serial)
            return real(conn, *args, **kwargs)

        wrapper.__name__ = name
        return wrapper

    for name in ALL_BUILDERS:
        monkeypatch.setattr(analytics_routes, name, make(name, getattr(analytics_routes, name)))
    return spy


@pytest.fixture
def builders(monkeypatch):
    """Labelled fakes: each response embeds the serial of the build it was rendered from."""
    return _install_builders(monkeypatch, labelled=True)


@pytest.fixture
def real_builders(monkeypatch):
    """Pass-through counters: real figures, observable build counts."""
    return _install_builders(monkeypatch, labelled=False)


# ---------------------------------------------------------------------------
# Apps and page readers
# ---------------------------------------------------------------------------
@pytest.fixture
def cached_app(analytics_app):
    analytics_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    return analytics_app


@pytest.fixture
def client(cached_app):
    return cached_app.test_client()


@pytest.fixture
def w_history_app(w_analytics_app):
    """w_analytics_app plus Old Donor (Rockford IL, 777 on 2024-06-15, to Committee One): inside "all time" and cycle 2024, outside
    cycle 2026 -- so the default window, all time and cycle 2024 each show different figures (see the derivation at the top)."""
    from database.connection import get_db
    from database.models import Committee, Contribution, Donor

    conn = get_db(w_analytics_app.config["DATABASE_PATH"])
    try:
        committee_one = Committee.get_or_create(conn, "Committee One")
        january = conn.execute(
            "SELECT id FROM reports WHERE committee_id = ? AND reporting_period = 'Jan 2026'", (committee_one.id,)
        ).fetchone()["id"]
        old = Donor.get_or_create(conn, "Old Donor", "707 Elm St, ROCKFORD, IL 61101", "old donor", "707 elm st, rockford, il 61101")
        Contribution(
            report_id=january, donor_id=old.id, amount=777.0, transaction_date="06/15/2024",
            description="Old mailer", raw_contributed_by="raw", raw_address="raw address",
        ).save(conn)
        conn.commit()
    finally:
        conn.close()
    w_analytics_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    return w_analytics_app


def _get(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, f"GET {url} -> {response.status_code}"
    return response.get_data(as_text=True)


def _serials(html: str, name: str) -> list[int]:
    """Serials of the labelled fake builds of `name` that the page embeds (sorted, distinct)."""
    found = re.findall(rf"{re.escape(name)}\|call=(\d+)\|", html)
    return sorted({int(serial) for serial in found})


def _blob(html: str, element_id: str):
    match = re.search(rf'<script id="{re.escape(element_id)}" type="application/json">(.*?)</script>', html, re.S)
    assert match, f"no <script id={element_id!r}> in the page"
    return json.loads(match.group(1))


def _geo_figures(html: str) -> tuple[list, list, list]:
    """(states, cities, months) the geography page embeds: (state, total), (city, state, total), (month, total)."""
    summary = _blob(html, "analytics-geo-summary")
    series = _blob(html, "analytics-geo-timeseries")
    states = [(row["state"], row["total_amount"]) for row in summary["states"]]
    cities = [(row["city"], row["state"], row["total_amount"]) for row in summary["cities"]]
    months = [(row["month"], row["total_amount"]) for row in series]
    return states, cities, months


_ROW = re.compile(r"<tr>\s*(<td>.*?</td>)\s*</tr>", re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)
_HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')


def _table_rows(html: str) -> list[list[str]]:
    rows = []
    for row in _ROW.findall(html):
        rows.append([" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in _CELL.findall(row)])
    return rows


def _concentration_rows(html: str) -> list[tuple[str, str, str]]:
    """(committee, total, donors) of the Donor Concentration table (the only table with eight cells per row)."""
    return [(row[0], row[1], row[2]) for row in _table_rows(html) if len(row) == 8]


def _nlp_rows(html: str) -> list[list[str]]:
    """Rows of the NLP table (the only table with five cells per row)."""
    return [row for row in _table_rows(html) if len(row) == 5]


def _scope_inputs(html: str) -> dict:
    """The hidden scope fields of the page's form (period for a preset, date_from / date_to for a custom range)."""
    return dict(_HIDDEN.findall(html))


_SCOPE_LABEL = re.compile(r'<strong class="time-scope-label">(.*?)</strong>', re.S)


def _scope_label(html: str) -> str:
    """The scope bar's label: "2026 Cycle", "All time" or "Custom range"."""
    match = _SCOPE_LABEL.search(html)
    assert match, "no scope bar label in the page"
    return " ".join(match.group(1).split())


def _chrome(html: str) -> str:
    """Section nav + scope bar: everything between the nav and the page's own form.  It is rendered from the request, not from
    the cached payload, and carries the window label and every persisted link."""
    start = html.index('<nav class="analytics-subnav"')
    end = html.index('<form class="search-form analytics-form"')
    return " ".join(html[start:end].split())


# ===========================================================================
# A. config.py -> create_app -> app.config
# ===========================================================================
GEOGRAPHY_TTL_ENV = "ANALYTICS_GEOGRAPHY_CACHE_TTL_SECONDS"
DONORS_TTL_ENV = "ANALYTICS_DONORS_CACHE_TTL_SECONDS"


def _ttls_in_fresh_process(**environment) -> dict:
    """config.py reads the environment at import time, so look at it in a fresh interpreter (as tests/test_request_hygiene.py)."""
    env = {key: value for key, value in os.environ.items() if key not in (GEOGRAPHY_TTL_ENV, DONORS_TTL_ENV)}
    env.update(environment)
    script = textwrap.dedent(
        f"""
        import json
        import config
        from webapp.app import create_app

        app = create_app({{"TESTING": True, "DASHBOARD_PREWARM_ENABLED": False}})
        print(json.dumps({{
            "config_geography": getattr(config, "{GEOGRAPHY_TTL_ENV}", None),
            "config_donors": getattr(config, "{DONORS_TTL_ENV}", None),
            "app_geography": app.config.get("{GEOGRAPHY_TTL_ENV}"),
            "app_donors": app.config.get("{DONORS_TTL_ENV}"),
        }}))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_ttl_keys_default_to_the_six_hour_analytics_fallback():
    """Both keys exist in config.py AND in a fresh app's config, at 21600 s (the weekly import flushes Redis, so a long TTL
    costs no freshness).

    Kills: no key at all (the routes would fall back to a literal and the environment would never matter); a default copied
    from the geo drill-down (180); a key added to config.py that create_app never copies into app.config (the L1 finding:
    a configured value that nothing reads)."""
    values = _ttls_in_fresh_process()

    assert values["config_geography"] == FALLBACK_TTL, f"config.{GEOGRAPHY_TTL_ENV} is {values['config_geography']!r}"
    assert values["config_donors"] == FALLBACK_TTL, f"config.{DONORS_TTL_ENV} is {values['config_donors']!r}"
    assert values["app_geography"] == FALLBACK_TTL, f"app.config[{GEOGRAPHY_TTL_ENV!r}] is {values['app_geography']!r}"
    assert values["app_donors"] == FALLBACK_TTL, f"app.config[{DONORS_TTL_ENV!r}] is {values['app_donors']!r}"


def test_ttl_keys_are_read_from_their_own_environment_variables():
    """Two different values, one per variable, reach config.py and app.config unswapped and as integers.

    Kills: a hard-coded 21600 (the environment ignored); one variable feeding both keys; the two variables swapped;
    create_app copying the raw string (the other keys go through int())."""
    values = _ttls_in_fresh_process(**{GEOGRAPHY_TTL_ENV: "1111", DONORS_TTL_ENV: "2222"})

    assert values["config_geography"] == 1111, f"config.{GEOGRAPHY_TTL_ENV} is {values['config_geography']!r}"
    assert values["config_donors"] == 2222, f"config.{DONORS_TTL_ENV} is {values['config_donors']!r}"
    assert values["app_geography"] == 1111, f"app.config[{GEOGRAPHY_TTL_ENV!r}] is {values['app_geography']!r}"
    assert values["app_donors"] == 2222, f"app.config[{DONORS_TTL_ENV!r}] is {values['app_donors']!r}"


# ===========================================================================
# B. The route cache (labelled fakes, quick mode, in-process RouteCache)
# ===========================================================================
@pytest.mark.parametrize("page", PAGES)
def test_identical_requests_build_once(page, client, builders):
    """Two identical GETs build every builder of the page once, and the second response is rendered from that build with its
    data sections visible.

    Kills: no cache; caching only the heaviest builder (the page's other builder runs twice); a cache hit that forgets
    heavy_sections_loaded (the second response would say "Skipped in quick mode" instead of showing the rows); a hit that
    drops the payload; a builder called with arguments its real signature rejects."""
    first = _get(client, page.url)
    second = _get(client, page.url)

    for name in page.builders:
        assert builders.count(name) == 1, f"{name} ran {builders.count(name)} times for two identical requests"
        assert _serials(first, name) == [1], f"{name}: the first response does not embed its build"
        assert _serials(second, name) == [1], f"{name}: the second response is not rendered from the first build"
    assert "Skipped in quick mode" not in second, "the cache hit rendered the quick-mode placeholder instead of the rows"
    assert builders.signature_errors == []


# Four windows of seven months each (months = (2025 - 2025) * 12 + end month - start month + 1 = 7 for every one of them), chosen so
# that a key which carries the limits and `months` but lacks the window -- or lacks one of its two dates -- collides on at least
# one pair: [0] and [1] share their start, [0] and [2] share their end, [3] shares nothing with the others.
SEVEN_MONTH_WINDOWS = [
    ("2025-03-01", "2025-09-30"),
    ("2025-03-01", "2025-09-15"),
    ("2025-03-15", "2025-09-30"),
    ("2025-04-01", "2025-10-31"),
]


def _window_url(page: Page, query: str) -> str:
    return f"{page.url}?{query}" if query else page.url


@pytest.mark.parametrize("page", PAGES)
def test_each_window_has_its_own_entry(page, client, builders):
    """Six windows -- the default cycle, all time, and the four seven-month ranges above -- get six builds, each reaching the
    builders with its own dates and embedded by its own response; revisiting all six in reverse order is a pure hit that shows
    the ORIGINAL builds.

    Kills: a key without the window (the geography key's `months` is derived from the window, so only equal-length windows can
    expose this: the seven-month ranges collide); a key with only one of the two dates (the shared-start and shared-end pairs
    collide); a single-slot cache that keeps the last window (the reverse revisit would rebuild); last-writer-wins storage (a
    revisit would embed a later window's build); the months argument lost on the way (geography: 24 for the default cycle, 120
    for all time, 7 for the ranges)."""
    queries = ["", "period=all"] + [f"date_from={start}&date_to={end}" for start, end in SEVEN_MONTH_WINDOWS]
    windows = [(DEFAULT_FROM, DEFAULT_TO), ("", "")] + SEVEN_MONTH_WINDOWS
    responses = [_get(client, _window_url(page, query)) for query in queries]

    for name in page.windowed:
        calls = builders.calls[name]
        assert len(calls) == len(queries), f"{name} ran {len(calls)} times for {len(queries)} different windows"
        for index, window in enumerate(windows):
            assert (calls[index]["date_from"], calls[index]["date_to"]) == window, f"{name} call {index + 1} got another window"
            assert _serials(responses[index], name) == [index + 1], f"{name}: response {index + 1} is not its own build"
    if page is GEOGRAPHY:
        assert [call["months"] for call in builders.calls[SERIES]] == [DEFAULT_MONTHS, ALL_TIME_MONTHS, 7, 7, 7, 7]

    before = builders.counts(*ALL_BUILDERS)
    for index in reversed(range(len(queries))):
        again = _get(client, _window_url(page, queries[index]))
        for name in page.windowed:
            shown = _serials(again, name)
            assert shown == [index + 1], f"{name}: the revisit of window {index + 1} embeds build {shown}, not its original build"
    assert builders.counts(*ALL_BUILDERS) == before, "revisiting a cached window rebuilt something"


# (page, query, builder, parameter the builder must receive, value).  Defaults on the route: geo_state_limit 15,
# geo_city_limit 25, months derived from the window (24 here), concentration_limit 25, nlp_limit 20.
ARGUMENT_CASES = [
    pytest.param(GEOGRAPHY, "geo_state_limit=5", GEO, "limit_states", 5, id="geography-geo_state_limit"),
    pytest.param(GEOGRAPHY, "geo_city_limit=10", GEO, "limit_cities", 10, id="geography-geo_city_limit"),
    pytest.param(GEOGRAPHY, "months=6", SERIES, "months", 6, id="geography-months"),
    pytest.param(DONORS, "concentration_limit=10", CONC, "limit", 10, id="donors-concentration_limit"),
    pytest.param(DONORS, "nlp_limit=7", NLP, "limit", 7, id="donors-nlp_limit"),
]


@pytest.mark.parametrize("page, query, name, parameter, value", ARGUMENT_CASES)
def test_every_limit_and_months_argument_is_part_of_the_key(page, query, name, parameter, value, client, builders):
    """Changing one argument rebuilds the builder that takes it, with the new value, and the page shows that build; repeating
    the changed request is then a hit.  (The builder the argument does not reach is left unconstrained: a combined entry
    rebuilds both, split entries only one.)

    Kills: a key that forgets the argument (the limit=5 request would be served the 15-row payload: the builder would not run
    again); an argument parsed but not handed to the builder; a cache that stops caching once the query string is non-default
    (the repeat would rebuild)."""
    _get(client, page.url)
    changed = _get(client, f"{page.url}?{query}")

    assert builders.count(name) == 2, f"{name} did not rebuild for ?{query}"
    assert builders.last(name)[parameter] == value, f"{name} received {parameter}={builders.last(name)[parameter]!r}"
    assert _serials(changed, name) == [2], f"{name}: the response for ?{query} is not rendered from the new build"

    before = builders.counts(*ALL_BUILDERS)
    repeat = _get(client, f"{page.url}?{query}")

    assert builders.counts(*ALL_BUILDERS) == before, f"repeating ?{query} rebuilt something"
    assert _serials(repeat, name) == [2]
    assert builders.signature_errors == []


# Parameters _parse_filters reads on every analytics page that neither payload depends on.
UNRELATED = {
    "geography": ["concentration_limit=40", "nlp_limit=30", "min_edge_amount=2000", "network_limit=300", "anomaly_limit=10",
                  "recon_limit=5", "utm_source=crawler"],
    "donors": ["months=6", "geo_state_limit=5", "geo_city_limit=10", "min_edge_amount=2000", "network_limit=300",
               "anomaly_limit=10", "recon_limit=5", "utm_source=crawler"],
}


@pytest.mark.parametrize("page", PAGES)
def test_unrelated_query_parameters_do_not_create_entries(page, client, builders):
    """PERFORMANCE-KEY PIN (optional): delete this test if a key finer than the payload is acceptable.

    The crawler-exposed key space is the whole problem (plan L5/L7): a parameter the payload does not depend on must not buy
    its own entry.  After one default request, none of these builds anything: the default load_mode spelled out, every
    unrelated parameter of the page (including one the app has never heard of), and the same set in the opposite order.

    Kills: a key made from the raw query string / full path (any ?utm_source=<random> bypasses the cache and the page is built
    again, which is exactly the crawler problem); a key that reuses the overview's key (network_limit, anomaly_limit, ...);
    a key that is order sensitive."""
    _get(client, page.url)
    before = builders.counts(*page.builders)
    assert min(before.values()) == 1, "precondition: the first request built every builder of the page"

    queries = [
        "load_mode=quick",
        "&".join(UNRELATED[page.name]),
        "&".join(reversed(UNRELATED[page.name])),
    ]
    for query in queries:
        _get(client, f"{page.url}?{query}")
        assert builders.counts(*page.builders) == before, f"?{query} was not served from the existing entry"


@pytest.mark.parametrize("page", PAGES)
def test_one_window_spelled_two_ways_shares_a_build_and_keeps_its_own_links(page, client, builders):
    """The default cycle, ?period=cycle:2026 and the same dates as a custom range resolve to one window, so they share one
    build -- but each response carries ITS OWN scope fields (period for a preset, date_from / date_to for a custom range).

    Kills: a key built from the raw period string or the custom flag (the spelled-out request would build again); caching the
    rendered page or the whole template context (the custom response would carry the preset's hidden period field and no
    date fields: the links and the form would silently change the user's window)."""
    preset = _get(client, page.url)
    before = builders.counts(*page.builders)
    assert min(before.values()) == 1, "precondition: the first request built every builder of the page"

    explicit = _get(client, f"{page.url}?period=cycle:2026")
    custom = _get(client, f"{page.url}?date_from={DEFAULT_FROM}&date_to={DEFAULT_TO}")

    assert builders.counts(*page.builders) == before, "the same window under another spelling was built again"
    for name in page.windowed:
        assert _serials(explicit, name) == [1]
        assert _serials(custom, name) == [1]
    assert _scope_inputs(preset).get("period") == "cycle:2026"
    assert _scope_inputs(explicit).get("period") == "cycle:2026"
    custom_inputs = _scope_inputs(custom)
    assert custom_inputs.get("date_from") == DEFAULT_FROM
    assert custom_inputs.get("date_to") == DEFAULT_TO
    assert "period" not in custom_inputs, "the custom-range response carries the preset's period field"
    assert _scope_label(preset) == "2026 Cycle"
    assert _scope_label(explicit) == "2026 Cycle"
    assert _scope_label(custom) == "Custom range", "the custom-range response carries another request's scope bar"


@pytest.mark.parametrize("page", PAGES)
def test_refresh_cache_rebuilds_restocks_and_leaves_other_entries_alone(page, client, builders):
    """?refresh_cache=1 rebuilds every builder of the page for that request's key, stores the new build (the next plain request
    shows it, without building), and touches no other window's entry.  ?refresh_cache=0 is not a refresh.

    default (build 1) -> all time (build 2) -> default + refresh (build 3) -> default (hit: build 3) -> default + refresh_cache=0
    (hit: build 3) -> all time (hit: still build 2).

    Kills: ignoring the flag (the refreshed request would still embed build 1); a refresh that rebuilds but does not store (the
    next plain request builds again or serves build 1); a refresh that rebuilds only part of the payload; reading any
    refresh_cache key as true (?refresh_cache=0 would rebuild); a refresh that wipes the whole cache (the all-time entry would
    rebuild) -- an admin's refresh must not turn every other window cold."""
    _get(client, page.url)
    _get(client, f"{page.url}?period=all")
    before_refresh = builders.counts(*page.builders)

    refreshed = _get(client, f"{page.url}?refresh_cache=1")

    for name in page.builders:
        assert builders.count(name) > before_refresh[name], f"{name} was not rebuilt by ?refresh_cache=1"
    for name in page.windowed:
        assert _serials(refreshed, name) == [3], f"{name}: the refreshed response is not rendered from the new build"

    before = builders.counts(*ALL_BUILDERS)
    plain = _get(client, page.url)
    zero = _get(client, f"{page.url}?refresh_cache=0")
    other_window = _get(client, f"{page.url}?period=all")

    assert builders.counts(*ALL_BUILDERS) == before, "a request after the refresh rebuilt something"
    for name in page.windowed:
        assert _serials(plain, name) == [3], f"{name}: the refreshed build was not stored"
        assert _serials(zero, name) == [3], f"{name}: ?refresh_cache=0 did not read the entry"
        assert _serials(other_window, name) == [2], f"{name}: the refresh disturbed another window's entry"


@pytest.mark.parametrize("page", PAGES)
def test_nothing_is_cached_when_route_caches_are_off(page, cached_app, builders, shared_cache):
    """GUARD (green today): with ROUTE_PERF_CACHE_ENABLED false every request builds, nothing is written to the shared layer,
    and an entry stored while caching was on is not read (the startup prewarm writes entries whatever the flag says).

    Kills: a cache that ignores the flag on the write path (an entry appears in Redis although nothing may read it); one that
    ignores it on the read path (the request after the flag is switched off is served from the old entry)."""
    client = cached_app.test_client()
    cached_app.config["ROUTE_PERF_CACHE_ENABLED"] = False
    _get(client, page.url)
    _get(client, page.url)

    for name in page.builders:
        assert builders.count(name) == 2, f"{name} ran {builders.count(name)} times for two requests with the route caches off"
    assert shared_cache.keys_under(_prefix(page)) == [], "an entry was written although route caches are off"

    cached_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    _get(client, page.url)                                    # builds (nothing stored yet) and may store an entry
    cached_app.config["ROUTE_PERF_CACHE_ENABLED"] = False
    _get(client, page.url)
    _get(client, page.url)

    for name in page.builders:
        assert builders.count(name) == 5, f"{name}: a request with the route caches off was served from an entry"


@pytest.mark.parametrize(
    "page, failing",
    [pytest.param(GEOGRAPHY, GEO, id="geography"), pytest.param(DONORS, CONC, id="donors")],
)
def test_a_failed_build_is_not_cached(page, failing, client, builders):
    """The first build of `failing` raises; the next request must build it again (and embed that build), and only then is the
    page cached.  The first request may answer 500 or degrade: both are fine, caching the failure is not.

    Kills: a try/except that turns the failure into an empty payload and caches it for six hours (a public page showing
    "No state-level data" until the TTL ends); caching before the build succeeded."""
    builders.fail_next.add(failing)
    try:
        client.get(page.url)
    except Exception:
        pass  # TESTING propagates a failing view's exception to the client

    recovered = _get(client, page.url)
    assert builders.count(failing) == 2, f"{failing} ran {builders.count(failing)} times after one failure and one retry"
    assert _serials(recovered, failing) == [2]

    before = builders.counts(*ALL_BUILDERS)
    again = _get(client, page.url)
    assert builders.counts(*ALL_BUILDERS) == before, "the recovered page was not cached"
    assert _serials(again, failing) == [2]


# ===========================================================================
# C. The shared layer (FakeRedis)
# ===========================================================================
# The two new TTL settings, further apart than a factor of two so a swap cannot hide inside the accepted [ttl, 2 * ttl] range.
CONFIGURED_TTL = {"geography": 700, "donors": 3100}


@pytest.mark.parametrize("page", PAGES)
def test_entries_live_in_the_named_shared_namespace_with_the_configured_ttl(page, cached_app, client, builders, shared_cache):
    """One GET leaves its payload in Redis under ilcf:cache:<analytics_geography | analytics_donors>:, stored for the page's own
    TTL setting.  Every analytics TTL gets a different value, so reading the wrong key shows.

    Kills: a per-process dict / functools cache (nothing reaches Redis: the other gunicorn workers rebuild); the wrong cache
    name (a payload under another namespace is flushed and inspected under the wrong name); the risk or overview TTL key
    copied from the code this is modelled on; geography and donors reading each other's TTL; a hard-coded TTL or one read once
    at import; a TTL in milliseconds; a payload that never held the builders' output."""
    cached_app.config.update({
        "ANALYTICS_RISK_CACHE_TTL_SECONDS": 111,
        "ANALYTICS_OVERVIEW_CACHE_TTL_SECONDS": 222,
        "ANALYTICS_NETWORKS_CACHE_TTL_SECONDS": 333,
        "ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS": 444,
        "ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS": 555,
        GEOGRAPHY.ttl_key: CONFIGURED_TTL["geography"],
        DONORS.ttl_key: CONFIGURED_TTL["donors"],
    })
    ttl = CONFIGURED_TTL[page.name]

    _get(client, page.url)

    keys = shared_cache.keys_under(_prefix(page))
    assert keys, f"nothing was stored under {_prefix(page)}"
    stored = " ".join(shared_cache.values[key] for key in keys)
    for name in page.builders:
        assert _label(name, 1) in stored, f"the stored payload does not hold the output of {name}"
    for key in keys:
        assert ttl <= shared_cache.ttls[key] <= 2 * ttl, f"{key} was stored for {shared_cache.ttls[key]} s, not about {ttl} s"


def test_geography_figures_survive_the_shared_layer(w_history_app, real_builders, shared_cache):
    """Real builders, real figures, through Redis' JSON round trip: three windows with three different sets of figures (a
    February-March range, all time, cycle 2024) are built once each; revisiting them in reverse order is a pure hit that shows the
    same hand-computed figures, the same scope bar label and the same section nav / scope bar markup (derivation at the top).

    Kills: a key without the window (the all-time response would show the range's 2,400 / 1,800); a payload that does not
    survive json.dumps(default=str) / json.loads (the hit would show other values or rebuild every time); a hit that renders
    different rows than the miss; a cached template context (the request's TimeScope would come back from Redis as a string and
    the hit's scope bar and links would lose their window label -- invisible with the in-process cache, which keeps the object)."""
    client = w_history_app.test_client()

    misses = [_get(client, f"{GEOGRAPHY.url}?{query}") for query, _, _, _ in REAL_WINDOWS]

    for html, (query, figures, _, label) in zip(misses, REAL_WINDOWS):
        assert _geo_figures(html) == figures, f"?{query}: wrong figures on the first request"
        assert _scope_label(html) == label
    assert real_builders.counts(GEO, SERIES) == {GEO: 3, SERIES: 3}

    for index in reversed(range(len(REAL_WINDOWS))):
        query, figures, _, label = REAL_WINDOWS[index]
        again = _get(client, f"{GEOGRAPHY.url}?{query}")
        assert _geo_figures(again) == figures, f"?{query}: the revisit shows other figures"
        assert _scope_label(again) == label, f"?{query}: the revisit lost its scope bar label"
        assert _chrome(again) == _chrome(misses[index]), f"?{query}: the hit's section nav / scope bar differs from the miss's"
    assert real_builders.counts(GEO, SERIES) == {GEO: 3, SERIES: 3}, "a revisited window was built again"
    assert shared_cache.keys_under(_prefix(GEOGRAPHY)), "nothing reached the shared layer"


def test_donors_figures_survive_the_shared_layer(w_history_app, real_builders, shared_cache):
    """The donors twin: concentration rows of the same three windows, then the same three revisited from Redis in reverse order;
    the NLP table (it has no window) is the same rows in every response.

    Kills: as the geography twin, plus an NLP table that changes between the miss and the hit."""
    client = w_history_app.test_client()

    misses = [_get(client, f"{DONORS.url}?{query}") for query, _, _, _ in REAL_WINDOWS]

    nlp = _nlp_rows(misses[0])
    assert nlp, "precondition: the NLP table has rows"
    for html, (query, _, concentration, label) in zip(misses, REAL_WINDOWS):
        assert _concentration_rows(html) == concentration, f"?{query}: wrong concentration rows on the first request"
        assert _nlp_rows(html) == nlp
        assert _scope_label(html) == label
    assert real_builders.count(CONC) == 3

    for index in reversed(range(len(REAL_WINDOWS))):
        query, _, concentration, label = REAL_WINDOWS[index]
        again = _get(client, f"{DONORS.url}?{query}")
        assert _concentration_rows(again) == concentration, f"?{query}: the revisit shows other rows"
        assert _nlp_rows(again) == nlp, f"?{query}: the revisit shows another NLP table"
        assert _scope_label(again) == label, f"?{query}: the revisit lost its scope bar label"
        assert _chrome(again) == _chrome(misses[index]), f"?{query}: the hit's section nav / scope bar differs from the miss's"
    assert real_builders.count(CONC) == 3, "a revisited window was built again"
    assert shared_cache.keys_under(_prefix(DONORS)), "nothing reached the shared layer"


# ===========================================================================
# D. The prewarm
# ===========================================================================
def test_prewarm_makes_a_bare_request_a_pure_hit(cached_app, client, builders):
    """After warm_analytics_caches a bare GET of each page builds nothing and shows the rows (the first request after a
    restart or a flush is the one that used to pay 1-3.6 s).

    Kills: a prewarm that builds neither entry; one that builds only one page; one that files the entry under a key the route
    does not read (axis, months, a limit or a date that differs from a bare request's: the GET would build again)."""
    analytics_routes.warm_analytics_caches(cached_app.config["DATABASE_PATH"])
    assert builders.count(GEO) >= 1, "precondition: the prewarm never built the geography payload"
    built = builders.counts(*ALL_BUILDERS)

    geography = _get(client, GEOGRAPHY.url)
    donors = _get(client, DONORS.url)

    assert builders.counts(*ALL_BUILDERS) == built, "a bare request after the prewarm built something"
    assert "Skipped in quick mode" not in geography
    assert "Skipped in quick mode" not in donors
    assert _serials(geography, GEO), "the geography page does not show a build"
    assert _serials(donors, CONC), "the donors page does not show a build"


def test_prewarm_stores_what_a_cold_request_would_build(w_history_app, real_builders, shared_cache):
    """The prewarm calls the builders with a bare request's arguments, stores both entries for the full TTL, and what a bare
    request then shows is the hand-computed cycle-2026 figures, not all time's (see the derivation at the top).

    Kills: a prewarm that reuses the overview block's `geo_summary = _empty_geo_summary()` (the cached page would say "No
    state-level data available" for six hours); a prewarm that leaves the builders' own defaults in place (get_geo_summary(20,
    30) stored under the 15 / 25 key: invisible on a two-state fixture, a wrong page in production); a prewarm that builds for
    all time and files it under the default window's key (the history fixture's 2024 row shows: IL 15,047, not 14,270); an
    entry stored for a token TTL (a prewarm that evaporates in minutes)."""
    app = w_history_app
    client = app.test_client()

    # What a bare request passes to each builder, and the figures it renders (control: the expectations are right).
    cold_geography = _get(client, GEOGRAPHY.url)
    cold_donors = _get(client, DONORS.url)
    requested = {name: real_builders.projected(name, 0) for name in ALL_BUILDERS}
    assert requested[GEO] == {"limit_states": 15, "limit_cities": 25}
    assert _geo_figures(cold_geography) == (CYCLE_2026_STATES, CYCLE_2026_CITIES, CYCLE_2026_SERIES)
    assert _concentration_rows(cold_donors) == CYCLE_2026_CONCENTRATION

    _empty_route_caches()
    shared_cache.wipe()
    real_builders.reset()
    analytics_routes.warm_analytics_caches(app.config["DATABASE_PATH"])

    for page in (GEOGRAPHY, DONORS):
        keys = shared_cache.keys_under(_prefix(page))
        assert keys, f"the prewarm stored nothing under {_prefix(page)}"
        for key in keys:
            assert FALLBACK_TTL <= shared_cache.ttls[key] <= 2 * FALLBACK_TTL, f"{key}: stored for {shared_cache.ttls[key]} s"

    assert real_builders.count(GEO) >= 1, "precondition: the prewarm never built the geography payload"
    for index in range(real_builders.count(GEO)):
        assert real_builders.projected(GEO, index) == requested[GEO], f"prewarm get_geo_summary call {index} differs from a request's"
    for name in (SERIES, CONC, NLP):
        warmed = [real_builders.projected(name, index) for index in range(real_builders.count(name))]
        assert requested[name] in warmed, f"the prewarm never called {name} with a bare request's arguments {requested[name]}"

    built = real_builders.counts(*ALL_BUILDERS)
    warm_geography = _get(client, GEOGRAPHY.url)
    warm_donors = _get(client, DONORS.url)

    assert real_builders.counts(*ALL_BUILDERS) == built, "a bare request after the prewarm built something"
    assert _geo_figures(warm_geography) == (CYCLE_2026_STATES, CYCLE_2026_CITIES, CYCLE_2026_SERIES)
    assert _concentration_rows(warm_donors) == CYCLE_2026_CONCENTRATION
    assert _nlp_rows(warm_donors) == _nlp_rows(cold_donors)


def test_prewarm_survives_a_failing_geography_build(cached_app, builders, shared_cache):
    """A block of the prewarm that fails logs and moves on (every existing block is guarded): the caches the prewarm already
    built are still warmed, and nothing is stored for the failed build.  The prewarm runs in a background thread per worker whose
    wrapper swallows the exception, so an unguarded new block placed early would silently leave the 30 s risk / overview builds
    cold.  (Whether the donors entry survives a geography failure is left open: a prewarm that fetches the donor->committee rows
    once for both pages may fail them together.)

    Kills: a geography block outside its try / except; a block that stores an empty payload when its build fails."""
    builders.fail_next.add(GEO)
    try:
        analytics_routes.warm_analytics_caches(cached_app.config["DATABASE_PATH"])
    except Exception as exc:
        pytest.fail(f"a failing geography build escaped the prewarm: {exc!r}")

    assert builders.count(GEO) >= 1, "precondition: the prewarm never tried to build the geography payload"
    assert shared_cache.keys_under(_prefix(GEOGRAPHY)) == [], "the failed geography build was stored"
    for name in ("analytics_risk", "analytics_overview"):
        assert shared_cache.keys_under(f"ilcf:cache:{name}:"), f"the prewarm stopped before the {name} cache"


# ===========================================================================
# E. The overview's cache key (review fix R4)
# ===========================================================================
# dashboard() builds its payload from recon_limit, recon_min_abs_diff and -- with ?load_mode=full -- the mode itself and the two
# geo limits (geo_summary is the live get_geo_summary only in full mode), but _overview_cache_key leaves all of them out:
# ?recon_limit=3 and ?load_mode=full are served the bare request's payload on a warm key, and a cold key is poisoned by whichever
# variant is requested first.  The P3 rule holds in both directions: the key separates everything that changes the build and
# nothing that does not (the key space is crawler-exposed).
OVERVIEW_URL = "/analytics/"
RECON = "get_reconciliation_outliers"
# Every builder the overview route reads.  Pass-through counters: the page renders real figures, a miss shows as new calls.
OVERVIEW_BUILDERS = ("get_network_graph", "get_anomaly_flags", CONC, GEO, SERIES, NLP, RECON)


@pytest.fixture
def overview_calls(monkeypatch):
    """Builder name -> the arguments (defaults applied) of each call, for the overview's seven builders."""
    calls: dict[str, list[dict]] = {name: [] for name in OVERVIEW_BUILDERS}
    lock = threading.Lock()

    def spy(name, real):
        signature = inspect.signature(real)
        connection = next(iter(signature.parameters))

        def wrapper(conn, *args, **kwargs):
            bound = signature.bind(conn, *args, **kwargs)
            bound.apply_defaults()
            with lock:
                calls[name].append({key: value for key, value in bound.arguments.items() if key != connection})
            return real(conn, *args, **kwargs)

        wrapper.__name__ = name
        return wrapper

    for name in OVERVIEW_BUILDERS:
        monkeypatch.setattr(analytics_routes, name, spy(name, getattr(analytics_routes, name)))
    return calls


def _counts(calls: dict) -> dict:
    return {name: len(rows) for name, rows in calls.items()}


def _overview_filters(**changes) -> dict:
    """Hand-built stand-in for _parse_filters(): every field _overview_cache_key may read, at a bare request's values (cycle 2026
    on the pinned clock: 2025-01-01..2026-12-31, 24 months, default limits, quick mode).  `load_mode` follows
    `full_mode_requested`, so a key that reads either spelling of the mode works."""
    filters = {
        "time_period_key": "received:2025-01-01:2026-12-31",
        "date_from": DEFAULT_FROM,
        "date_to": DEFAULT_TO,
        "min_edge_amount": 1000.0,
        "network_limit": 200,
        "anomaly_limit": 25,
        "concentration_limit": 25,
        "months": DEFAULT_MONTHS,
        "nlp_limit": 20,
        "recon_limit": 20,
        "recon_min_abs_diff": 1000.0,
        "full_mode_requested": False,
        "geo_state_limit": 15,
        "geo_city_limit": 25,
    }
    filters.update(changes)
    filters["load_mode"] = "full" if filters["full_mode_requested"] else "quick"
    return filters


def _overview_key(cycle: int = 2026, sort_by: str = "total_amount", sort_dir: str = "desc", **changes) -> tuple:
    helper = getattr(analytics_routes, "_overview_cache_key", None)
    assert callable(helper), (
        "webapp.routes.analytics._overview_cache_key(filters, state_race_cycle, state_race_sort, state_race_dir) is not defined"
    )
    return helper(_overview_filters(**changes), cycle, sort_by, sort_dir)


# --- unit level: the key function on a hand-built filters dict -------------------------------------------------------------
def test_overview_key_is_deterministic():
    """PIN (green today), and the control of the separation tests below: a key that differed on every call would pass all of them.

    Kills: a timestamp, uuid or object address in the key (its entry is never found again); a key whose repr differs between two
    equal builds (Redis files entries under sha1(repr(key)): another worker, or the prewarm, would never find the entry)."""
    first, second = _overview_key(), _overview_key()

    assert isinstance(first, tuple)
    assert first == second
    assert hash(first) == hash(second)
    assert repr(first) == repr(second)


# One change at a time from a bare request: the fields and arguments the key carried before R4.
CARRIED_BEFORE_R4 = [
    pytest.param({"time_period_key": "received:2024-01-01:2024-12-31"}, id="time_period_key"),
    pytest.param({"date_from": "2025-02-01"}, id="date_from"),
    pytest.param({"date_to": "2026-11-30"}, id="date_to"),
    pytest.param({"min_edge_amount": 2000.0}, id="min_edge_amount"),
    pytest.param({"network_limit": 300}, id="network_limit"),
    pytest.param({"anomaly_limit": 10}, id="anomaly_limit"),
    pytest.param({"concentration_limit": 40}, id="concentration_limit"),
    pytest.param({"months": 6}, id="months"),
    pytest.param({"nlp_limit": 30}, id="nlp_limit"),
    pytest.param({"cycle": 2024}, id="state_race_cycle"),
    pytest.param({"sort_by": "outside_spending_total"}, id="state_race_sort"),
    pytest.param({"sort_dir": "asc"}, id="state_race_dir"),
]


@pytest.mark.parametrize("changes", CARRIED_BEFORE_R4)
def test_overview_key_still_separates_what_it_separated_before(changes):
    """PIN (green today): the edit that adds fields must keep every field the key already carries.

    Kills: a field dropped while the key is rewritten (the payload would then ignore a limit, the window, the cycle or the
    sort); the key replaced by a shorter or hashed form that loses one of them."""
    assert _overview_key(**changes) != _overview_key(), f"{changes} no longer changes the overview key"


# What the payload depends on and the key leaves out today.
ADDED_BY_R4 = [
    pytest.param({"recon_limit": 3}, id="recon_limit"),
    pytest.param({"recon_min_abs_diff": 2500.0}, id="recon_min_abs_diff"),
    pytest.param({"recon_min_abs_diff": 1000.5}, id="recon_min_abs_diff-half-a-dollar"),
    pytest.param({"full_mode_requested": True}, id="full_mode_requested"),
]


@pytest.mark.parametrize("changes", ADDED_BY_R4)
def test_overview_key_separates_the_fields_added_by_r4(changes):
    """RED today: none of them is in the key.

    Kills: ?recon_limit=3 / ?recon_min_abs_diff=2500 / ?load_mode=full served the bare request's payload (and a cold key poisoned by
    the first of them); one field added twice and another forgotten (one case per field); recon_min_abs_diff cut to an integer,
    which merges 1000.0 and 1000.5 although the reconciliation rows between them differ (the half-dollar case; the risk page's
    key rounds to cents, it does not truncate)."""
    assert _overview_key(**changes) != _overview_key(), f"{changes} does not change the overview key"


GEO_LIMITS = [pytest.param("geo_state_limit", 5, id="geo_state_limit"), pytest.param("geo_city_limit", 10, id="geo_city_limit")]


@pytest.mark.parametrize("field, other", GEO_LIMITS)
def test_overview_key_separates_the_geo_limits_in_full_mode(field, other):
    """RED today: in full mode the geo summary is computed from the limits, so they are part of the payload.

    Kills: no geo limits in the key (?load_mode=full&geo_state_limit=5 served the 15-state payload); only one of the two (one case
    per limit)."""
    full = {"full_mode_requested": True}

    assert _overview_key(**full, **{field: other}) != _overview_key(**full), f"{field} does not change the full-mode overview key"


@pytest.mark.parametrize("field, other", GEO_LIMITS)
def test_overview_key_ignores_the_geo_limits_in_quick_mode(field, other):
    """PIN (green today): in quick mode geo_summary is the empty placeholder whatever the limits, so a request that only changes
    them must find the bare request's entry.

    Kills: the geo limits always in the key (each ?geo_state_limit=N&geo_city_limit=M variant a crawler tries would buy its own
    build of the page); one of the two always in the key (one case per limit)."""
    assert _overview_key(**{field: other}) == _overview_key(), f"{field} changes the quick-mode overview key"


# --- route level: the real _parse_filters, the real cache, the real builders behind counters -------------------------------
def test_overview_recon_limit_gets_its_own_entry(client, overview_calls):
    """RED today: the second request is a hit on the first one's entry.

    A bare request; ?recon_limit=3 (built again, with limit=3 and the default min_abs_diff); ?recon_limit=3 once more (a hit: it
    has an entry of its own); the bare request again (a hit: the variant did not replace it).  No quick-mode request calls
    get_geo_summary.

    Kills: a key without recon_limit (the variant is served the bare payload; cold, the bare request would be served the
    variant's); a cache bypassed for every non-default request (the repeat rebuilds: every limit a crawler supplies pays the
    full cold build); a variant that overwrites the bare entry (the last request rebuilds); the limit parsed but not handed to
    the builder.  The fixture's reconciliation section may be empty: the pinned fact is which build ran, not what it returned."""
    recon = overview_calls[RECON]

    _get(client, OVERVIEW_URL)
    assert len(recon) == 1, "precondition: a bare request builds the reconciliation section once"
    assert recon[0]["limit"] == 20, "precondition: a bare request asks for 20 reconciliation rows"

    _get(client, f"{OVERVIEW_URL}?recon_limit=3")
    assert len(recon) == 2, f"?recon_limit=3 was served the bare request's entry: get_reconciliation_outliers ran {len(recon)} time(s)"
    assert recon[1]["limit"] == 3
    assert recon[1]["min_abs_diff"] == 1000.0
    assert overview_calls[GEO] == [], "a quick-mode request called get_geo_summary"
    built = _counts(overview_calls)

    _get(client, f"{OVERVIEW_URL}?recon_limit=3")
    assert _counts(overview_calls) == built, "repeating ?recon_limit=3 built something again: the variant is not cached"

    _get(client, OVERVIEW_URL)
    assert _counts(overview_calls) == built, "the bare request after the variant built something again"


def test_overview_full_mode_gets_its_own_entry(client, overview_calls):
    """RED today: ?load_mode=full is served the quick-mode entry, so the live geo summary never runs.

    A bare request (quick mode never calls get_geo_summary); ?load_mode=full (calls it once, live: plan LD9, no stored snapshot);
    ?load_mode=full once more (a hit); the bare request again (a hit).

    Kills: a key without the mode (full mode is served the quick payload and its empty geo section; cold, a quick request would be
    served the full one); a cache bypassed for full mode (the repeat rebuilds); a full-mode request that overwrites the quick
    entry (the last request rebuilds)."""
    geo = overview_calls[GEO]

    _get(client, OVERVIEW_URL)
    assert geo == [], "a quick-mode request called get_geo_summary"

    _get(client, f"{OVERVIEW_URL}?load_mode=full")
    assert len(geo) == 1, f"?load_mode=full was served the quick-mode entry: get_geo_summary ran {len(geo)} time(s)"
    built = _counts(overview_calls)

    _get(client, f"{OVERVIEW_URL}?load_mode=full")
    assert _counts(overview_calls) == built, "repeating ?load_mode=full built something again: the full-mode payload is not cached"

    _get(client, OVERVIEW_URL)
    assert _counts(overview_calls) == built, "the bare request after the full-mode request built something again"


# Parameters _parse_filters reads that the quick-mode overview payload does not depend on, plus one the app has never heard of.
UNRELATED_TO_THE_OVERVIEW = [
    "load_mode=quick",
    "geo_state_limit=5&geo_city_limit=10",
    "refresh_full=1&sync_full=1",
    "utm_source=crawler",
]


def test_unrelated_query_parameters_do_not_create_overview_entries(client, overview_calls):
    """PERFORMANCE-KEY PIN (green today; optional: delete it if a key finer than the payload is acceptable).

    After one bare request none of these builds anything: the default mode spelled out, the geo limits in quick mode, the snapshot
    flags (ignored in quick mode), a parameter the app has never heard of, and all of them together.

    Kills: a key made from the raw query string or from the whole filters dict (every ?utm_source=<random> buys a cold build of the
    heaviest page: the crawler problem of plan L5/L7); the geo limits in the key in quick mode, through the real parse."""
    _get(client, OVERVIEW_URL)
    before = _counts(overview_calls)
    assert before[RECON] == 1, "precondition: the bare request built the overview"

    for query in UNRELATED_TO_THE_OVERVIEW + ["&".join(UNRELATED_TO_THE_OVERVIEW)]:
        _get(client, f"{OVERVIEW_URL}?{query}")
        assert _counts(overview_calls) == before, f"?{query} was not served from the bare request's entry"


# --- the prewarm -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "warm_kwargs, query",
    [pytest.param({}, "", id="default-window"), pytest.param({"scope_key": "all"}, "?period=all", id="all-time")],
)
def test_prewarmed_overview_is_a_pure_hit_for_the_matching_request(
    warm_kwargs, query, cached_app, client, overview_calls, shared_cache
):
    """PIN (green today): the prewarm and the route build their overview key the same way, as Redis sees it.

    warm_analytics_caches fills the entry from a hand-built `window_filters` dict and every block of it is guarded, so a KeyError
    from the key function (a field the route's key reads and the prewarm's dict lacks) is logged and swallowed: the overview stays
    cold until the first visitor pays for it.  The FakeRedis files entries under sha1(repr(key)) like production, so a default that
    compares equal but spells differently (1000 for 1000.0, 0 for False) is a miss here, which the in-process dict would hide.

    Kills: a prewarm whose window_filters lacks a field of the new key; a prewarm default of another value or type than the route's
    (recon_limit, recon_min_abs_diff, the mode, the geo limits); a prewarm that files its entry under the full-mode key."""
    analytics_routes.warm_analytics_caches(cached_app.config["DATABASE_PATH"], **warm_kwargs)
    assert shared_cache.keys_under("ilcf:cache:analytics_overview:"), (
        "the prewarm stored no overview entry (its overview block logs and swallows an exception such as a KeyError)"
    )
    built = _counts(overview_calls)

    page = _get(client, f"{OVERVIEW_URL}{query}")

    assert _counts(overview_calls) == built, "a request after the prewarm built something: the prewarm's key differs from the route's"
    assert "Governor - Statewide At-Large" in page, "the hit does not render the prewarmed payload's state race rows"
