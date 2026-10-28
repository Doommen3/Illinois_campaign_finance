"""Tests-first suite for plan step S-S2, item B3: the window-independent graph builders are built once.

Plan: .claude/plans/profiles-races-dated-widgets.md section 6.2 (S-S2) and decision J-S15 in 6.3.

Why: `/analytics/relationships` and `/analytics/networks` cache ONE payload per time window, so the
all-time view (`?period=all`) pays the full cold cost of five graph builders even though the heavy ones
do not depend on the window at all.  Pinned contract:

  * Window-independent builders (they take no dates):
        relationships: get_donor_cogiving_network, get_committee_similarity_network,
                       get_candidate_competition_networks
        networks:      get_state_federal_overlap_graph, and get_vendor_expenditure_network while it takes no dates
    are cached under keys WITHOUT the window.  Any window (default cycle, ?period=all, a custom range,
    another cycle) reuses them; the key still carries every parameter the builder itself takes.
  * Windowed builders (lobbying influence, 527 ecosystem, and on networks get_network_graph) still run
    once per window, receive that window's dates, and are still cached per window.
  * `?refresh_cache=1` rebuilds the window-independent builders (and re-stocks the cache).
  * The startup prewarm (`warm_analytics_caches`) leaves the window-independent builders warm for EVERY window
    (J-S15: "?period=all reuses the warmed heavy parts") and a bare request stays a pure cache hit.
  * With ROUTE_PERF_CACHE_ENABLED false nothing is cached (every request rebuilds).

CONFLICT WITH S-W6a (flagged in the report): decision J-S14 and tests/test_scope_axis_and_windows.py
(`test_networks_vendor_graph_follows_the_window`, written before this step) make the networks Vendor graph WINDOWED, i.e.
`get_vendor_expenditure_network` will gain date_from/date_to.  A windowed builder cannot be cached across windows, so
this file reads the classification from the builder's signature at import time: while it takes no dates it is
window-independent (cached once, like the overlap graph); once it takes `date_from` it is a per-window builder like
lobbying.  Either order of S-S2 and S-W6 therefore leaves both suites satisfiable; S-W6a is what proves the dates are
honoured, so adding an unused date parameter does not get past the pair.

How the builders are observed: each of the eight graph builders is replaced, on BOTH
`webapp.routes.analytics` and `database.analytics`, by a spy that records the keyword arguments of every
call, checks them against the real builder's signature (so a window passed to a builder that takes no
dates is caught even though the spy accepts anything), and returns a payload whose single node label
spells out the builder, the call serial and the keyword arguments.  The label is what the pages embed as
JSON, so "the second response reuses the first build" is read from the response itself, not only from
a counter.  The route must keep calling the builders through those module attributes (house pattern,
tests/test_app_performance.py).

Caches: Redis is forced off (house technique, tests/test_cache_backend.py) and EVERY `RouteCache`
instance in the process is emptied around each test, found with `gc` rather than by module so a heavy
cache the implementer adds anywhere is cleared too.  A cache that is not a `RouteCache` (a bare dict)
would leak between these tests; if you add one, give it an `invalidate()` the sweep can reach or use
RouteCache.
"""
from __future__ import annotations

import gc
import inspect
import json
import re
import threading

import pytest

import database.analytics as analytics_db
import webapp.cache_backend as cache_backend
import webapp.routes.analytics as analytics_routes
from database.connection import init_db
from webapp.app import create_app
from webapp.cache_backend import RouteCache
from webapp.utils.time_scope import current_cycle, cycle_window

RELATIONSHIPS = "/analytics/relationships"
NETWORKS = "/analytics/networks"

# Window-independent builders (heavy) and per-window builders, by page.
COGIVING = "get_donor_cogiving_network"
SIMILARITY = "get_committee_similarity_network"
COMPETITION = "get_candidate_competition_networks"
VENDOR = "get_vendor_expenditure_network"
OVERLAP = "get_state_federal_overlap_graph"
LOBBYING = "get_lobbying_influence_graph"
ECOSYSTEM = "get_irs527_ecosystem_graph"
NETWORK = "get_network_graph"

# S-W6a windows the Vendor graph (see the module docstring), so its class is read from the signature.
VENDOR_TAKES_A_WINDOW = "date_from" in inspect.signature(analytics_db.get_vendor_expenditure_network).parameters

REL_HEAVY = (COGIVING, SIMILARITY, COMPETITION)
NET_HEAVY = (OVERLAP,) if VENDOR_TAKES_A_WINDOW else (VENDOR, OVERLAP)
WINDOWED = (LOBBYING, ECOSYSTEM)           # on both pages
NET_WINDOWED = (NETWORK, *WINDOWED, *((VENDOR,) if VENDOR_TAKES_A_WINDOW else ()))
ALL_BUILDERS = (*REL_HEAVY, VENDOR, OVERLAP, *WINDOWED, NETWORK)

# <script id="..." type="application/json"> ids the templates embed, per builder.
REL_SCRIPT_IDS = {
    COGIVING: ["donor-cogiving-data"],
    SIMILARITY: ["committee-similarity-data"],
    COMPETITION: ["candidate-state-data", "candidate-federal-data", "candidate-combined-data"],
    LOBBYING: ["lobbying-influence-data"],
    ECOSYSTEM: ["ecosystem-527-data"],
}
NET_SCRIPT_IDS = {
    VENDOR: ["vendor-network-data"],
    OVERLAP: ["overlap-graph-data"],
    NETWORK: ["analytics-network-data"],
    LOBBYING: ["lobbying-graph-data"],
    ECOSYSTEM: ["ecosystem-527-data"],
}

# The window a page resolves with no query string: the current cycle by date, read at run time so this file does
# not rot on 2027-01-01.
DEFAULT_START, DEFAULT_END = (day.isoformat() for day in cycle_window(current_cycle()))
CUSTOM = "date_from=2025-01-01&date_to=2025-06-30"


# ---------------------------------------------------------------------------
# Caches and Redis
# ---------------------------------------------------------------------------

def _empty_route_caches() -> None:
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()


@pytest.fixture(autouse=True)
def route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process RouteCache empty around each test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _empty_route_caches()
    yield
    _empty_route_caches()


# ---------------------------------------------------------------------------
# Builder spies
# ---------------------------------------------------------------------------

def _graph(label: str) -> dict:
    return {
        "nodes": [{"id": "n1", "label": label, "type": "fake"}],
        "edges": [],
        "centrality": [],
        "summary": {"node_count": 1, "edge_count": 0},
    }


class Builders:
    """Recorded calls of the eight graph builders (thread safe: the networks page runs them in a pool)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = {name: [] for name in ALL_BUILDERS}
        self.signature_errors: list[str] = []
        self.fail_next: set[str] = set()

    def count(self, name: str) -> int:
        return len(self.calls[name])

    def last(self, name: str) -> dict:
        return self.calls[name][-1]

    def counts(self, *names: str) -> dict:
        return {name: self.count(name) for name in names}


def _label(name: str, serial: int, kwargs: dict) -> str:
    params = "|".join(f"{key}={kwargs[key]}" for key in sorted(kwargs))
    return f"{name}|call={serial}|{params}"


def _payload(name: str, serial: int, kwargs: dict) -> dict:
    label = _label(name, serial, kwargs)
    if name == COMPETITION:  # four graphs, one per scope; the page embeds three of them
        return {scope: _graph(f"{label}|scope={scope}") for scope in ("state", "federal", "cross_scope", "combined")}
    return _graph(label)


@pytest.fixture
def builders(monkeypatch):
    spy = Builders()

    def make(name, real):
        signature = inspect.signature(real)
        connection_parameter = next(iter(signature.parameters))

        def wrapper(conn, *args, **kwargs):
            # Bind to the real signature: records positional and keyword calls alike, and flags arguments the
            # real builder would reject.
            try:
                bound = signature.bind(conn, *args, **kwargs)
                recorded = {k: v for k, v in bound.arguments.items() if k != connection_parameter}
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
            return _payload(name, serial, recorded)

        wrapper.__name__ = name
        return wrapper

    for name in ALL_BUILDERS:
        wrapper = make(name, getattr(analytics_db, name))
        monkeypatch.setattr(analytics_db, name, wrapper)
        monkeypatch.setattr(analytics_routes, name, wrapper)
    return spy


# ---------------------------------------------------------------------------
# App factories and page readers
# ---------------------------------------------------------------------------

def _make_app(tmp_path, *, route_cache: bool):
    db_path = str(tmp_path / "alltime_cache.db")
    init_db(db_path)
    return create_app({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": route_cache,
        "REFRESH_PARAMS_REQUIRE_LOGIN": False,  # anonymous ?refresh_cache=1 in these tests (LD4)
        "ANALYTICS_RELATIONSHIPS_CACHE_TTL_SECONDS": 600,
        "ANALYTICS_NETWORKS_CACHE_TTL_SECONDS": 600,
    })


@pytest.fixture
def client(tmp_path, builders):
    return _make_app(tmp_path, route_cache=True).test_client()


@pytest.fixture
def uncached_client(tmp_path, builders):
    return _make_app(tmp_path, route_cache=False).test_client()


def _get(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, f"GET {url} -> {response.status_code}"
    return response.get_data(as_text=True)


def _blob(html: str, element_id: str) -> dict:
    match = re.search(rf'<script id="{re.escape(element_id)}" type="application/json">(.*?)</script>', html, re.S)
    assert match, f"no <script id={element_id!r}> in the page"
    return json.loads(match.group(1))


def _node_label(blob: dict) -> str:
    assert len(blob["nodes"]) == 1, f"fake payload should carry exactly one node, got {blob['nodes']!r}"
    return blob["nodes"][0]["label"]


def _labels(html: str, script_ids: dict, name: str) -> list[str]:
    return [_node_label(_blob(html, element_id)) for element_id in script_ids[name]]


def _window_in(label: str) -> tuple[str, str]:
    """(date_from, date_to) a windowed fake was called with; '' = unbounded (labels list kwargs alphabetically)."""
    match = re.search(r"date_from=([^|]*)\|date_to=([^|]*)", label)
    assert match, f"no window in label {label!r}"
    return match.group(1), match.group(2)


def _assert_no_signature_drift(builders: Builders) -> None:
    assert builders.signature_errors == [], (
        "a builder was called with arguments its real signature does not accept "
        f"(e.g. a window passed to a builder that takes no dates): {builders.signature_errors}"
    )


# ---------------------------------------------------------------------------
# /analytics/relationships
# ---------------------------------------------------------------------------

def test_relationships_heavy_builders_run_once_across_windows(client, builders):
    """Default cycle, all time and a custom range share ONE build of the three heavy builders.

    Windowed builders: default (1), all time (2), custom range (3); the fourth request repeats the default
    window and is a window-cache hit (still 3).  Heavy builders stay at 1 throughout.

    Kills: the per-window key kept as is (heavy builders run 3 times); caching the heavy parts for
    ?period=all only; a heavy key that still carries date_from/date_to; windowed builders cached under the
    window-free key (they would run once, serving the first window's graph to every later window); windowed
    builders never cached (the repeat of the default window would run them a 4th time); the window handed to
    a heavy builder (signature drift)."""
    _get(client, RELATIONSHIPS)
    _get(client, f"{RELATIONSHIPS}?period=all")
    _get(client, f"{RELATIONSHIPS}?{CUSTOM}")
    _get(client, RELATIONSHIPS)

    assert builders.counts(*REL_HEAVY) == {COGIVING: 1, SIMILARITY: 1, COMPETITION: 1}
    assert builders.counts(*WINDOWED) == {LOBBYING: 3, ECOSYSTEM: 3}
    assert builders.calls[LOBBYING][0]["date_from"] == DEFAULT_START
    assert builders.calls[LOBBYING][0]["date_to"] == DEFAULT_END
    assert builders.calls[LOBBYING][1]["date_from"] == ""      # all time: the pages hand the builders ''
    assert builders.calls[LOBBYING][1]["date_to"] == ""
    assert builders.calls[LOBBYING][2]["date_from"] == "2025-01-01"
    assert builders.calls[LOBBYING][2]["date_to"] == "2025-06-30"
    assert builders.calls[ECOSYSTEM][1]["date_from"] == ""
    assert builders.calls[ECOSYSTEM][2]["date_to"] == "2025-06-30"
    _assert_no_signature_drift(builders)


def test_relationships_window_independent_json_is_identical_across_windows(client, builders):
    """The heavy payloads embedded for the default cycle and for all time are the same build's JSON (call serial 1
    in both responses), while each windowed payload is built for its own window.

    Kills: a rebuild that a counter alone could miss (the second response would read call=2); serving the windowed
    graphs from the first window's build (the all-time response would carry the default window's dates)."""
    first = _get(client, RELATIONSHIPS)
    second = _get(client, f"{RELATIONSHIPS}?period=all")

    for name in REL_HEAVY:
        for element_id in REL_SCRIPT_IDS[name]:
            blob_first, blob_second = _blob(first, element_id), _blob(second, element_id)
            assert blob_first == blob_second, f"{element_id} differs between the default and the all-time response"
            assert f"{name}|call=1|" in _node_label(blob_first), f"{element_id}: not the first (only) build of {name}"

    for name in WINDOWED:
        label_first = _labels(first, REL_SCRIPT_IDS, name)[0]
        label_second = _labels(second, REL_SCRIPT_IDS, name)[0]
        assert f"{name}|call=1|" in label_first, f"{name}: the default response is not the first build"
        assert f"{name}|call=2|" in label_second, f"{name}: the all-time response is not its own (second) build"
        assert _window_in(label_first) == (DEFAULT_START, DEFAULT_END)
        assert _window_in(label_second) == ("", "")


# (query parameter, {builder: keyword argument the builder must receive}).  Defaults on the route:
# donor_limit 200, committee_limit 120, candidate_limit 80, edge_limit 200, min_shared_amount 5000.0,
# min_shared_donors 2, min_shared_targets 2.
HEAVY_PARAMETER_CASES = [
    pytest.param("donor_limit=300", {COGIVING: ("donor_limit", 300)}, id="donor_limit"),
    pytest.param("min_shared_targets=3", {COGIVING: ("min_shared_targets", 3)}, id="min_shared_targets"),
    pytest.param("committee_limit=150", {SIMILARITY: ("committee_limit", 150)}, id="committee_limit"),
    pytest.param("candidate_limit=90", {COMPETITION: ("candidate_limit", 90)}, id="candidate_limit"),
    pytest.param("min_shared_donors=3",
                 {SIMILARITY: ("min_shared_donors", 3), COMPETITION: ("min_shared_donors", 3)},
                 id="min_shared_donors"),
    pytest.param("min_shared_amount=7000",
                 {COGIVING: ("min_shared_amount", 7000.0), SIMILARITY: ("min_shared_amount", 7000.0),
                  COMPETITION: ("min_shared_amount", 7000.0)},
                 id="min_shared_amount"),
    pytest.param("edge_limit=250",
                 {COGIVING: ("edge_limit", 250), SIMILARITY: ("edge_limit", 250), COMPETITION: ("edge_limit", 250)},
                 id="edge_limit"),
]


@pytest.mark.parametrize("query, affected", HEAVY_PARAMETER_CASES)
def test_relationships_heavy_cache_key_carries_the_builders_own_parameters(client, builders, query, affected):
    """Dropping the window from the key must not drop the parameters the builder itself takes.

    1. ?period=all builds each heavy builder once.
    2. ?period=cycle:2024 with one parameter changed: the builders that take that parameter run again,
       receive the new value, and the page embeds that build.
    3. ?period=cycle:2022 with the SAME changed parameter: a new window but the same heavy parameters, so
       nothing heavy runs again (RED today: every new window rebuilds).

    Kills: a heavy key made of nothing (or of the window-free page key only), which would serve the
    donor_limit=200 graph to a donor_limit=300 request; a key that forgets one of the seven parameters
    (the parametrization covers each); a key so fine that it still rebuilds per window."""
    _get(client, f"{RELATIONSHIPS}?period=all")
    assert builders.counts(*REL_HEAVY) == {COGIVING: 1, SIMILARITY: 1, COMPETITION: 1}, "precondition"

    changed = _get(client, f"{RELATIONSHIPS}?period=cycle:2024&{query}")
    for name, (keyword, value) in affected.items():
        assert builders.count(name) == 2, f"{name} did not rebuild for {query}"
        assert builders.last(name)[keyword] == value, f"{name} received {keyword}={builders.last(name)[keyword]!r}"
        for label in _labels(changed, REL_SCRIPT_IDS, name):
            assert f"{keyword}={value}" in label, f"the page embeds a stale {name} payload: {label}"
            assert f"{name}|call=2|" in label

    after_changed = builders.counts(*REL_HEAVY)
    _get(client, f"{RELATIONSHIPS}?period=cycle:2022&{query}")
    assert builders.counts(*REL_HEAVY) == after_changed, "same heavy parameters, new window: must reuse the build"
    _assert_no_signature_drift(builders)


def test_relationships_parameters_that_only_feed_windowed_builders_do_not_rebuild_heavy_ones(client, builders):
    """client_limit and org_limit reach only the lobbying and 527 builders, so changing them must not rerun
    the three heavy builders, while the windowed builders do run again with the new limits.

    PERFORMANCE-KEY PIN (optional): delete this test if a key that is coarser than necessary is acceptable.
    Kills: a heavy key built from the whole query string / the page's full parameter tuple minus the window."""
    _get(client, f"{RELATIONSHIPS}?period=all")
    _get(client, f"{RELATIONSHIPS}?period=all&client_limit=100&org_limit=150")

    assert builders.counts(*REL_HEAVY) == {COGIVING: 1, SIMILARITY: 1, COMPETITION: 1}
    assert builders.counts(*WINDOWED) == {LOBBYING: 2, ECOSYSTEM: 2}
    assert builders.last(LOBBYING)["client_limit"] == 100
    assert builders.last(ECOSYSTEM)["org_limit"] == 150


def test_relationships_refresh_cache_rebuilds_the_heavy_builders_and_restocks_them(client, builders):
    """?refresh_cache=1 rebuilds the window-independent builders, whatever the window, and the rebuilt payload is
    what the next request (any window) reuses.

    default (H1 W1) -> default + refresh (H2 W2) -> all time (H2 reused, W3; embeds the refreshed call=2)
    -> all time + refresh (H3 W4).

    Kills: a refresh that only rebuilds the windowed parts; a refresh that bypasses the cache but never
    stores the new payload (the all-time request would rebuild, H3); a refresh that does not apply to
    the heavy cache because it is keyed apart from the page key; ignoring refresh_cache on the all-time view."""
    _get(client, RELATIONSHIPS)
    assert builders.counts(*REL_HEAVY) == {COGIVING: 1, SIMILARITY: 1, COMPETITION: 1}, "precondition"

    _get(client, f"{RELATIONSHIPS}?refresh_cache=1")
    assert builders.counts(*REL_HEAVY) == {COGIVING: 2, SIMILARITY: 2, COMPETITION: 2}, "refresh did not rebuild"
    assert builders.counts(*WINDOWED) == {LOBBYING: 2, ECOSYSTEM: 2}

    after_refresh = _get(client, f"{RELATIONSHIPS}?period=all")
    assert builders.counts(*REL_HEAVY) == {COGIVING: 2, SIMILARITY: 2, COMPETITION: 2}, "refreshed build not reused"
    assert builders.counts(*WINDOWED) == {LOBBYING: 3, ECOSYSTEM: 3}
    assert f"{COGIVING}|call=2|" in _labels(after_refresh, REL_SCRIPT_IDS, COGIVING)[0]

    _get(client, f"{RELATIONSHIPS}?period=all&refresh_cache=1")
    assert builders.counts(*REL_HEAVY) == {COGIVING: 3, SIMILARITY: 3, COMPETITION: 3}
    assert builders.counts(*WINDOWED) == {LOBBYING: 4, ECOSYSTEM: 4}


def test_relationships_nothing_is_cached_when_route_caches_are_off(uncached_client, builders):
    """GUARD (green today): with ROUTE_PERF_CACHE_ENABLED false every request rebuilds everything.

    Kills: a heavy cache that ignores the flag (a module-level dict), which would also leak payloads between
    tests and make every other analytics test order dependent."""
    _get(uncached_client, RELATIONSHIPS)
    _get(uncached_client, RELATIONSHIPS)
    _get(uncached_client, f"{RELATIONSHIPS}?period=all")

    assert builders.counts(*REL_HEAVY) == {COGIVING: 3, SIMILARITY: 3, COMPETITION: 3}
    assert builders.counts(*WINDOWED) == {LOBBYING: 3, ECOSYSTEM: 3}


# ---------------------------------------------------------------------------
# /analytics/networks
# ---------------------------------------------------------------------------

def test_networks_heavy_builders_run_once_across_windows(client, builders):
    """The overlap graph (and the vendor graph while it takes no dates) is built once for any window; get_network_graph,
    lobbying and 527 (and the vendor graph once it takes dates) once per window.

    default (1), all time (2), custom (3), default again = window-cache hit (still 3).

    Kills: as the relationships test, plus: get_network_graph treated as window independent (it takes
    date_from/date_to and must run per window with that window's dates)."""
    _get(client, NETWORKS)
    _get(client, f"{NETWORKS}?period=all")
    _get(client, f"{NETWORKS}?{CUSTOM}")
    _get(client, NETWORKS)

    assert builders.counts(*NET_HEAVY) == {name: 1 for name in NET_HEAVY}
    assert builders.counts(*NET_WINDOWED) == {name: 3 for name in NET_WINDOWED}
    for name in (NETWORK, *((VENDOR,) if VENDOR_TAKES_A_WINDOW else ())):
        assert builders.calls[name][0]["date_from"] == DEFAULT_START, name
        assert builders.calls[name][0]["date_to"] == DEFAULT_END, name
        assert builders.calls[name][1]["date_from"] == "", name
        assert builders.calls[name][1]["date_to"] == "", name
        assert builders.calls[name][2]["date_from"] == "2025-01-01", name
        assert builders.calls[name][2]["date_to"] == "2025-06-30", name
    assert builders.calls[LOBBYING][2]["date_to"] == "2025-06-30"
    _assert_no_signature_drift(builders)


def test_networks_window_independent_json_is_identical_across_windows(client, builders):
    """The overlap graph (and the vendor graph while it takes no dates) embeds the same build (call=1) in both
    responses; every windowed graph embeds its own window.  Kills: see the relationships twin."""
    first = _get(client, NETWORKS)
    second = _get(client, f"{NETWORKS}?period=all")

    for name in NET_HEAVY:
        element_id = NET_SCRIPT_IDS[name][0]
        blob_first, blob_second = _blob(first, element_id), _blob(second, element_id)
        assert blob_first == blob_second, f"{element_id} differs between the default and the all-time response"
        assert f"{name}|call=1|" in _node_label(blob_first)

    for name in NET_WINDOWED:
        label_first = _labels(first, NET_SCRIPT_IDS, name)[0]
        label_second = _labels(second, NET_SCRIPT_IDS, name)[0]
        assert f"{name}|call=1|" in label_first, f"{name}: the default response is not the first build"
        assert f"{name}|call=2|" in label_second, f"{name}: the all-time response is not its own (second) build"
        assert _window_in(label_first) == (DEFAULT_START, DEFAULT_END)
        assert _window_in(label_second) == ("", "")


def test_networks_form_parameters_rebuild_only_the_graph_that_takes_them(client, builders):
    """min_edge_amount and network_limit reach get_network_graph only: it reruns with the new values, the
    window-independent graphs (fixed arguments on this page) do not.

    PERFORMANCE-KEY PIN (optional): delete this test if a heavy key that includes the page's form fields is
    acceptable.  Kills: a heavy key made from the page's whole cache key minus the window."""
    _get(client, f"{NETWORKS}?period=all")
    _get(client, f"{NETWORKS}?period=all&min_edge_amount=5000&network_limit=300")

    assert builders.counts(*NET_HEAVY) == {name: 1 for name in NET_HEAVY}
    assert builders.count(NETWORK) == 2
    assert builders.last(NETWORK)["min_edge_amount"] == 5000.0
    assert builders.last(NETWORK)["limit"] == 300


def test_networks_refresh_cache_rebuilds_the_heavy_builders_and_restocks_them(client, builders):
    """Same sequence and kill list as the relationships twin, on the window-independent networks graphs:
    default (H1) -> default + refresh (H2) -> all time (H2 reused) -> all time + refresh (H3)."""
    heavy = NET_HEAVY

    def heavy_counts(n: int) -> dict:
        return {name: n for name in heavy}

    _get(client, NETWORKS)
    assert builders.counts(*heavy) == heavy_counts(1), "precondition"

    _get(client, f"{NETWORKS}?refresh_cache=1")
    assert builders.counts(*heavy) == heavy_counts(2), "refresh did not rebuild"
    assert builders.count(NETWORK) == 2

    after_refresh = _get(client, f"{NETWORKS}?period=all")
    assert builders.counts(*heavy) == heavy_counts(2), "refreshed build not reused"
    assert builders.count(NETWORK) == 3
    assert f"{OVERLAP}|call=2|" in _labels(after_refresh, NET_SCRIPT_IDS, OVERLAP)[0]

    _get(client, f"{NETWORKS}?period=all&refresh_cache=1")
    assert builders.counts(*heavy) == heavy_counts(3)
    assert builders.count(NETWORK) == 4


def test_networks_a_failed_heavy_build_is_not_served_to_other_windows(client, builders):
    """GUARD (green today, added beyond the brief): the networks page degrades a failing builder to an empty graph
    flagged `summary.error`.  Once heavy builds are shared by every window, caching that empty graph would show it
    for every window until the TTL.  The default window's overlap build fails once; the all-time request must build
    it again and embed the real graph.

    Kills: caching whatever the heavy builder returned, error graph included, under the window-free key."""
    builders.fail_next.add(OVERLAP)
    broken = _get(client, NETWORKS)
    assert _blob(broken, "overlap-graph-data")["summary"].get("error"), "precondition: the page shows the failed build"

    recovered = _get(client, f"{NETWORKS}?period=all")
    assert builders.count(OVERLAP) == 2, "the failed overlap build was cached and reused for another window"
    blob = _blob(recovered, "overlap-graph-data")
    assert not blob["summary"].get("error")
    assert f"{OVERLAP}|call=2|" in _node_label(blob)


def test_networks_nothing_is_cached_when_route_caches_are_off(uncached_client, builders):
    """GUARD (green today): see the relationships twin."""
    _get(uncached_client, NETWORKS)
    _get(uncached_client, NETWORKS)
    _get(uncached_client, f"{NETWORKS}?period=all")

    assert builders.counts(*NET_HEAVY) == {name: 3 for name in NET_HEAVY}
    assert builders.counts(*NET_WINDOWED) == {name: 3 for name in NET_WINDOWED}


# ---------------------------------------------------------------------------
# Prewarm
# ---------------------------------------------------------------------------

def test_prewarm_warms_the_heavy_builders_so_every_window_reuses_them(tmp_path, builders):
    """`warm_analytics_caches` is what makes the first request fast after a restart; it writes the route caches
    itself, so it must follow the route's new layout.

    1. After the prewarm every window-independent builder has run (precondition).
    2. A bare GET of each page is a pure cache hit: no builder runs (the prewarm still matches the default request).
    3. ?period=all runs only the windowed builders (get_network_graph on networks; lobbying and 527 on both pages; the
       vendor graph on networks once it takes dates) and none of the window-independent ones.

    Kills: a prewarm that keeps writing the old whole-payload entries (the first all-time request rebuilds the heavy
    parts, the 30 s+ the step exists to remove); a prewarm whose keys no longer match the default request after the
    route's keys changed (a cold landing page)."""
    app = _make_app(tmp_path, route_cache=True)
    analytics_routes.warm_analytics_caches(app.config["DATABASE_PATH"])
    heavy = (*REL_HEAVY, *NET_HEAVY)
    warmed = builders.counts(*heavy)
    assert all(n >= 1 for n in warmed.values()), f"precondition: the prewarm did not build {warmed}"

    client = app.test_client()
    before = builders.counts(*ALL_BUILDERS)
    _get(client, RELATIONSHIPS)
    _get(client, NETWORKS)
    assert builders.counts(*ALL_BUILDERS) == before, "a bare request after the prewarm must be a pure cache hit"

    _get(client, f"{RELATIONSHIPS}?period=all")
    _get(client, f"{NETWORKS}?period=all")
    assert builders.counts(*heavy) == warmed, "an all-time request rebuilt window-independent builders"
    assert builders.count(LOBBYING) == before[LOBBYING] + 2
    assert builders.count(ECOSYSTEM) == before[ECOSYSTEM] + 2
    assert builders.count(NETWORK) == before[NETWORK] + 1
    if VENDOR_TAKES_A_WINDOW:
        assert builders.count(VENDOR) == before[VENDOR] + 1
    _assert_no_signature_drift(builders)

