"""Tests-first suite for plan step 5: the per-entity analytics tabs (blueprint ``entity_analytics``).

Plan: .claude/plans/race-sort-and-entity-networks.md -- "Route contract -- blueprint `entity_analytics`",
decisions N10 (tabs mirror the global subnav) and N14 (caching + cost), and the builder payload shapes of the
"Contract" section.  Step 6 implements ``webapp/routes/entity_analytics.py`` (today a compile-only stub: the
blueprint has no routes and is not registered), the five templates under ``webapp/templates/entity/`` and the
subnav on the two parent pages.

How the suite works.  Routing is isolated from the builders: ``resolve_entity`` and the five ``get_entity_*``
builders are replaced ON THE ROUTE MODULE (the contract imports them by name) with recording fakes that return
small fixed payloads in the contract's shapes.  Every call's kwargs are captured.  Pages are asserted through a
stdlib HTML parser (rows with cells and links, anchors with class tokens, h1/h2 text, tags), never string-matched
on whole markup.  The two parent pages (candidate detail, race detail) are real pages over the seeds of
tests/test_candidate_profile.py and tests/test_state_races_index.py.

Fake entities (all names fictional; committed literals):

    candidate 1   "Dana Fictionalson"   committees 901, 902   cycle None          groups: two (one committee each)
    candidate 2   "Robin Notreal"       committee 911          cycle None          (second entity for the cache tests)
    race  member-board-of-education-1b  "Member Board of Education - District 1B"   cycle = ?election_cycle, else the
                                                                                    current cycle by the frozen clock
    race  state-senator-senate-district-9  (second entity for the cache tests)
    anything else -> None

The clock is frozen at 2026-10-26 (house technique of tests/test_state_races_index.py): the current cycle is 2026,
cycle 2024 is the previous one, the race default window is cycle_window(2026) = 2025-01-01..2026-12-31 and
cycle_window(2024) = 2023-01-01..2024-12-31.  ``clock.today`` moves it for the rollover test.

Pinned contract (each test names the wrong implementation it kills):

  * ENTITY_TABS, the blueprint name, exactly four routes, no prefix (GET /candidates/<int>/<tab>, the race twin,
    and the two network.json routes); ``_entity_cache_key`` signature and tuple (revision first, key as str,
    scope_cache_key of the scope, election_cycle verbatim, params last).
  * 404: unknown candidate / race (``resolve_entity`` -> None), unknown tab, unknown ``view``.  No builder runs and
    nothing is cached on any 404.  ``resolve_entity`` gets the kind, the candidate id as an int (the URL converter)
    or the race slug, and for races ``election_cycle`` = None when absent, the int when ``?election_cycle=2024``.
  * 200 for every tab on both kinds: ``<h1>`` = "<label> -- <Tab>", one scope bar after the h1 whose
    ``data-scope-key`` is the resolved scope, the subnav (Overview + five tabs, the active one marked, every link
    carrying the scope params and, for races, ``election_cycle``), windowed ``<h2>``s tagged with the scope label.
  * The window reaches the one right builder as ``date_from`` / ``date_to`` keywords: candidate default unbounded
    (None, None), race default the cycle window, a cycle preset, ``period=all``, custom bounds, half-open bounds,
    ``election_cycle`` alone drives the default window of a race.
  * network.json: the five builder keys + ``view: "ego"``, unknown ``view`` 404; the HTML Network tab carries
    ``<div id="entity-network-graph" data-src="<entity>/network.json?view=ego&<scope>">`` and an edge table.
  * Caching (N14): one RouteCache per tab ``entity_<tab>`` through ``get_or_compute`` (single-flight), TTL
    ``ENTITY_ANALYTICS_CACHE_TTL_SECONDS`` (config.py, default 21600), key = ``_entity_cache_key``; the same
    (kind, key, tab, window) built once; a different window / election_cycle / tab / entity / network.json-vs-HTML
    builds separately; ``?refresh_cache=1`` is 302 for an anonymous caller (no build) and a forced, stored rebuild for
    a signed-in one; a race cache entry never outlives a cycle rollover (the repo's J-R4 lesson: keys carry the
    RESOLVED cycle).
  * Anonymous tab pages carry exactly the site's edge-cache header (tests/test_request_hygiene.py LD2): tokens
    {public, max-age=0, s-maxage=300}, no Vary: Cookie, no Set-Cookie.

Page-content pins are presence checks on the fake payload's values: threshold line + "All time" reconciliation
section (risk), linked top donors with a redacted donor unlinked (donors), states / cities / monthly tables
(geography), co-giving committees linked to /committees/<id> (relationships), edge rows with currency weights
(network).

Interpretations (ambiguities in the contract, each flagged in the tester report; relax by editing one test):
  I1  the cache key's ``election_cycle`` slot for a race is the RESOLVED cycle (entity["cycle"]), never None
      (test_race_entry_does_not_outlive_a_cycle_rollover); a candidate's is None.
  I2  ``resolve_entity`` gets ``election_cycle`` only from the request arg (None when absent), as the task text
      says -- so ``?period=cycle:2024`` WITHOUT ``election_cycle`` shows the CURRENT-cycle candidates in a 2024
      window, unlike /analytics/state-races/<key>, which derives the cycle from the window.  Not pinned either way.
  I3  every subnav link carries the scope EXPLICITLY (``**scope_query`` = scope_params: ``period=<key>`` or the
      custom bounds), so a link resolved against any other page default still lands on the page's window.
  I4  the active tab is marked by class token ``active`` or ``aria-current`` on the subnav anchor (house convention).
  I5  the Network tab's edge table links committee nodes to /committees/<id> and donor nodes to /donors/key/<key>
      ("linked" in the contract); one test, relaxable.
  I6  the scope axis (contract text: "transaction"; the race page it mirrors uses "received") is NOT pinned.

Accepted holes (named, not silently dropped):
  * The builders' own correctness is tests/test_entity_analytics.py / test_entity_network.py (step 3); here they are fakes.
  * ``?load_mode`` and other analytics query args are not part of the contract and are not exercised.
  * The wall clock leaks through a route that calls ``datetime.date.today()`` via ``import datetime`` (only the
    modules that ``from datetime import date`` are frozen); today that is the same cycle, so it would pass.
  * A cold app that needs more than ~1 s to reach the cache can make the single-flight test pass for a naive
    implementation (false pass, never a false fail).
  * Environment propagation of ENTITY_ANALYTICS_CACHE_TTL_SECONDS is covered by the config attribute + app.config
    copy, not by a fresh-interpreter run.
  * Share / HHI / Gini number formatting, the monthly-spike and concentration flag rows, and the unknown_amount /
    in_state_share lines are not pinned (formatting choices; a changed format would not change a decision).
"""
from __future__ import annotations

import copy
import gc
import inspect
import re
import threading
import time
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

import config as app_config
import webapp.cache_backend as cache_backend
import webapp.routes.analytics  # noqa: F401  (imported at collection time, as tests/test_analytics_tab_caches.py does)
import webapp.routes.entity_analytics as entity_routes
from database.connection import get_db, init_db
from database.models import AppUser
from tests.test_candidate_profile import candidate_app  # noqa: F401  (fixture: candidate 900 and its committees)
from tests.test_request_hygiene import ADMIN_PASS, ADMIN_USER, _login
from tests.test_state_race_builder import _create_race_tables
from tests.test_state_races_index import _seed_races
from webapp.app import create_app
from webapp.cache_backend import RouteCache
from webapp.utils.time_scope import resolve_time_scope, scope_cache_key

TODAY = date(2026, 10, 26)

TABS = ("network", "risk", "donors", "geography", "relationships")
TARGETS = TABS + ("network.json",)
KINDS = ("candidate", "race")

BUILDER_OF = {
    "network": "get_entity_network",
    "risk": "get_entity_risk",
    "donors": "get_entity_donors",
    "geography": "get_entity_geography",
    "relationships": "get_entity_relationships",
    "network.json": "get_entity_network",
}
BUILDERS = tuple(sorted(set(BUILDER_OF.values())))
CACHE_NAME_OF = {tab: f"entity_{tab}" for tab in TABS}

RACE_KEY = "member-board-of-education-1b"
OTHER_RACE_KEY = "state-senator-senate-district-9"
CANDIDATE_LABEL = "Dana Fictionalson"
RACE_LABEL = "Member Board of Education - District 1B"

GROUP_A = "Friends of Dana Fictionalson"
GROUP_B = "Dana Fictionalson Victory Fund"
PAT_KEY = "pat|largegiver|100 elm st||springfield|il|62701"
RAE_KEY = "rae|hidden|redaction requested|||il|"
SAM_KEY = "sam|sharedgiver|7 oak ave||peoria|il|61602"
VENDOR_ID = "vendor:0123456789abcdef"

# Edge-cache rule of tests/test_request_hygiene.py (LD2): the header on anonymous public HTML.
EDGE_TOKENS = {"public", "max-age=0", "s-maxage=300"}


# ---------------------------------------------------------------------------
# Clock, caches
# ---------------------------------------------------------------------------

class _Clock:
    today = TODAY


CLOCK = _Clock()


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return CLOCK.today


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Freeze ``date.today()`` in every module that may resolve a cycle from the clock."""
    CLOCK.today = TODAY
    for module in (
        "webapp.utils.time_scope",
        "database.analytics",
        "webapp.routes.analytics",
        "webapp.routes.candidates",
        "webapp.routes.entity_analytics",
        "database.entity_analytics",
    ):
        monkeypatch.setattr(f"{module}.date", _FrozenDate, raising=False)
    yield CLOCK
    CLOCK.today = TODAY


def _empty_route_caches() -> None:
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()


@pytest.fixture(autouse=True)
def route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process route cache empty around each test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _empty_route_caches()
    yield
    _empty_route_caches()


# ---------------------------------------------------------------------------
# Fake entities and payloads (contract shapes; committed literals)
# ---------------------------------------------------------------------------

def _group(label, committee_ids, candidate_id):
    return {"label": label, "committee_ids": committee_ids, "candidate_id": candidate_id, "family_id": None}


CANDIDATES = {
    1: {"kind": "candidate", "key": "1", "label": CANDIDATE_LABEL, "cycle": None, "committee_ids": [901, 902],
        "candidate_ids": [1], "groups": [_group(GROUP_A, [901], 1), _group(GROUP_B, [902], 1)]},
    2: {"kind": "candidate", "key": "2", "label": "Robin Notreal", "cycle": None, "committee_ids": [911],
        "candidate_ids": [2], "groups": [_group("Notreal for Illinois", [911], 2)]},
}
RACES = {
    RACE_KEY: {"kind": "race", "key": RACE_KEY, "label": RACE_LABEL, "cycle": 2026, "committee_ids": [901, 911],
               "candidate_ids": [1, 2], "groups": [_group(CANDIDATES[1]["label"], [901], 1),
                                                    _group("Robin Notreal", [911], 2)]},
    OTHER_RACE_KEY: {"kind": "race", "key": OTHER_RACE_KEY, "label": "State Senator - Senate District 9",
                     "cycle": 2026, "committee_ids": [921], "candidate_ids": [3],
                     "groups": [_group("Sample Senator", [921], 3)]},
}


def _zero_metrics(label=None):
    metrics = {"top1_share": 0.0, "top5_share": 0.0, "top10_share": 0.0, "hhi": 0.0, "gini": 0.0,
               "total_amount": 0.0, "donor_count": 0, "contribution_count": 0, "individual_amount": 0.0,
               "organization_amount": 0.0, "in_kind_total": 0.0}
    if label is not None:
        metrics.update({"label": label, "committee_ids": [], "candidate_id": None, "family_id": None})
    return metrics


def _group_metrics(label, committee_ids, total, donors, contributions, individual, organization):
    return {"label": label, "committee_ids": committee_ids, "candidate_id": 1, "family_id": None,
            "total_amount": total, "donor_count": donors, "contribution_count": contributions,
            "top1_share": 0.4, "top5_share": 0.8, "top10_share": 0.95, "hhi": 1800.5, "gini": 0.61,
            "individual_amount": individual, "organization_amount": organization, "in_kind_total": 0.0}


def donors_payload(entity, n):
    return {
        "groups": [
            _group_metrics(GROUP_A, [901], 40000.0, 7, 11, 31000.0, 9000.0),
            _group_metrics(GROUP_B, [902], 20000.0, 5, 6, 14000.0, 6000.0),
        ],
        "totals": _group_metrics("All", [901, 902], 60000.0, 12, 17, 45000.0, 15000.0),
        "class_breakdown": [
            {"donor_class": "individual", "total_amount": 45000.0, "donor_count": 9},
            {"donor_class": "organization", "total_amount": 15000.0, "donor_count": 3},
        ],
        "top_donors": [
            {"donor_key": PAT_KEY, "donor_name": f"Pat Largegiver #{n}", "donor_class": "individual",
             "total_amount": 25000.0, "contribution_count": 2, "committee_count": 1, "city": "Springfield",
             "state": "IL", "redacted": False, "groups": [GROUP_A]},
            {"donor_key": RAE_KEY, "donor_name": "Rae Hidden", "donor_class": "individual",
             "total_amount": 18000.0, "contribution_count": 3, "committee_count": 1, "city": None,
             "state": None, "redacted": True, "groups": [GROUP_B]},
        ],
        "shared_donors": [
            {"donor_key": SAM_KEY, "donor_name": "Sam Sharedgiver", "total_amount": 9000.0, "group_count": 2,
             "groups": [{"label": GROUP_A, "amount": 5000.0}, {"label": GROUP_B, "amount": 4000.0}]},
        ],
    }


def geography_payload(entity, n):
    def row(city, state, amount, donors, contributions, share):
        return {"city": city, "state": state, "total_amount": amount, "donor_count": donors,
                "contribution_count": contributions, "share": share}

    return {
        "states": [
            {"state": "IL", "total_amount": 30000.0, "donor_count": 8, "contribution_count": 12, "share": 0.75},
            {"state": "MO", "total_amount": 10000.0, "donor_count": 3, "contribution_count": 4, "share": 0.25},
        ],
        "cities": [
            row(f"Springfield #{n}", "IL", 18000.0, 5, 7, 0.45),
            row("Chicago", "IL", 12000.0, 3, 5, 0.3),
            row("St Louis", "MO", 7000.0, 2, 3, 0.175),
        ],
        "time_series": [
            {"month": "2026-01", "total_amount": 4000.0, "contribution_count": 3, "donor_count": 2},
            {"month": "2026-02", "total_amount": 0.0, "contribution_count": 0, "donor_count": 0},
            {"month": "2026-03", "total_amount": 6000.0, "contribution_count": 5, "donor_count": 4},
        ],
        "in_state_share": 0.75,
        "unknown_amount": 0.0,
        "totals": {"total_amount": 40000.0, "donor_count": 11, "contribution_count": 16},
    }


def risk_payload(entity, n):
    return {
        "flags": [
            {"flag_type": "large_single_contribution", "severity": "high", "committee_id": 901,
             "committee_name": GROUP_A, "group_label": GROUP_A, "donor_key": PAT_KEY,
             "donor_name": f"Pat Largegiver #{n}", "event_date": "2026-03-01", "month": "2026-03",
             "value": 25000.0, "baseline": None, "threshold": 12345.0, "details": "single receipt"},
            {"flag_type": "monthly_spike", "severity": "medium", "committee_id": 901, "committee_name": GROUP_A,
             "group_label": GROUP_A, "donor_key": None, "donor_name": None, "event_date": None,
             "month": "2026-02", "value": 8000.0, "baseline": 1000.0, "threshold": None, "details": "spike"},
            {"flag_type": "high_donor_concentration", "severity": "high", "committee_id": None,
             "committee_name": None, "group_label": GROUP_B, "donor_key": None, "donor_name": None,
             "event_date": None, "month": None, "value": 5200.5, "baseline": 4, "threshold": 4500,
             "details": "hhi"},
        ],
        "threshold": {"large_single": 12345.0, "source": "site_wide"},
        "reconciliation": [
            {"committee_id": 902, "committee_name": GROUP_B, "group_label": GROUP_B,
             "receipts_minus_d2_total": 4321.5, "diff": 4321.5},
        ],
        "reconciliation_available": True,
        "counts": {"large_single_contribution": 1, "monthly_spike": 1, "high_donor_concentration": 1,
                   "reconciliation": 1},
    }


def relationships_payload(entity, n):
    return {
        "cogiving": [
            {"committee_id": 777, "committee_name": f"Fictional Victory Fund #{n}", "shared_donor_count": 4,
             "shared_amount_to_entity": 12000.0, "shared_amount_to_committee": 8000.0,
             "top_shared_donors": [{"donor_key": PAT_KEY, "donor_name": "Pat Largegiver",
                                    "amount_to_entity": 7000.0, "amount_to_committee": 3000.0}]},
            {"committee_id": 778, "committee_name": "Imaginary Leadership PAC", "shared_donor_count": 2,
             "shared_amount_to_entity": 5500.0, "shared_amount_to_committee": 2200.0, "top_shared_donors": []},
        ],
        "shared_donors": [
            {"donor_key": SAM_KEY, "donor_name": "Sam Sharedgiver", "total_amount": 9000.0, "group_count": 2,
             "groups": [{"label": GROUP_A, "amount": 5000.0}, {"label": GROUP_B, "amount": 4000.0}]},
        ],
        "summary": {"entity_donor_count": 12, "donor_pool_size": 12, "cogiving_committee_count": 2},
    }


EDGE_DEFINITIONS = {
    "donor_committee": {"edge_label": "Contributions from donors", "edge_description": "Itemized receipts",
                        "weight_unit": "usd", "metric_label": "Amount contributed"},
    "committee_vendor": {"edge_label": "Spending with vendors", "edge_description": "Itemized expenditures",
                         "weight_unit": "usd", "metric_label": "Amount paid"},
    "committee_candidate": {"edge_label": "Committee supports candidate", "edge_description": "Committee link",
                            "weight_unit": "usd", "metric_label": "Amount raised"},
}


def _edge(edge_type, source, target, weight, count):
    return {"source": source, "target": target, "edge_type": edge_type, "weight": weight, "count": count,
            "weight_unit": "usd", "source_table": "isbe_condensed_receipts", **{
                key: EDGE_DEFINITIONS[edge_type][key] for key in ("edge_label", "edge_description", "metric_label")}}


def network_payload(entity, n):
    return {
        "entity": entity,
        "nodes": [
            {"id": "committee:901", "label": GROUP_A, "node_type": "committee", "role": "entity",
             "group_label": GROUP_A, "total_amount": 77345.67},
            {"id": "candidate:1", "label": CANDIDATE_LABEL, "node_type": "candidate", "role": "candidate",
             "group_label": GROUP_A, "total_amount": 40000.0},
            {"id": f"donor:{PAT_KEY}", "label": f"Pat Largegiver #{n}", "node_type": "donor", "role": "donor",
             "group_label": GROUP_A, "total_amount": 25000.0},
            {"id": VENDOR_ID, "label": f"Acme Print Fictional #{n}", "node_type": "vendor", "role": "vendor",
             "group_label": GROUP_A, "total_amount": 12345.67},
        ],
        "edges": [
            _edge("donor_committee", f"donor:{PAT_KEY}", "committee:901", 25000.0, 2),
            _edge("committee_vendor", "committee:901", VENDOR_ID, 12345.67, 3),
            _edge("committee_candidate", "committee:901", "candidate:1", 40000.0, 20),
        ],
        "centrality": [{"node_id": "committee:901", "label": GROUP_A, "node_type": "committee",
                        "weighted_degree": 77345.67, "degree": 3}],
        "summary": {"node_count": 4, "edge_count": 3,
                    "edges_by_type": {"donor_committee": 1, "committee_vendor": 1, "committee_candidate": 1},
                    "truncated": {"donors": 0, "vendors": 0, "transfers": 0, "ie_spenders": 0},
                    "edge_type_definitions": EDGE_DEFINITIONS,
                    "window": {"date_from": None, "date_to": None}},
    }


FULL = {
    "get_entity_donors": donors_payload,
    "get_entity_geography": geography_payload,
    "get_entity_risk": risk_payload,
    "get_entity_relationships": relationships_payload,
    "get_entity_network": network_payload,
}


def _empty_donors(entity, n):
    return {"groups": [], "totals": _zero_metrics("All"), "class_breakdown": [], "top_donors": [],
            "shared_donors": []}


def _empty_geography(entity, n):
    return {"states": [], "cities": [], "time_series": [], "in_state_share": 0.0, "unknown_amount": 0.0,
            "totals": {"total_amount": 0.0, "donor_count": 0, "contribution_count": 0}}


def _empty_risk(entity, n):
    return {"flags": [], "threshold": {"large_single": 5000.0, "source": "site_wide"}, "reconciliation": [],
            "reconciliation_available": False,
            "counts": {"large_single_contribution": 0, "monthly_spike": 0, "high_donor_concentration": 0,
                       "reconciliation": 0}}


def _empty_relationships(entity, n):
    return {"cogiving": [], "shared_donors": [],
            "summary": {"entity_donor_count": 0, "donor_pool_size": 0, "cogiving_committee_count": 0}}


def _empty_network(entity, n):
    return {"entity": entity, "nodes": [], "edges": [], "centrality": [],
            "summary": {"node_count": 0, "edge_count": 0, "edges_by_type": {},
                        "truncated": {"donors": 0, "vendors": 0, "transfers": 0, "ie_spenders": 0},
                        "edge_type_definitions": {}, "window": {"date_from": None, "date_to": None}}}


EMPTY = {
    "get_entity_donors": _empty_donors,
    "get_entity_geography": _empty_geography,
    "get_entity_risk": _empty_risk,
    "get_entity_relationships": _empty_relationships,
    "get_entity_network": _empty_network,
}


class Fakes:
    """Recording replacements for resolve_entity and the five builders."""

    def __init__(self) -> None:
        self.calls = {name: [] for name in BUILDERS}
        self.resolve_calls: list[dict] = []
        self.payloads = FULL
        self.hook = None
        self._lock = threading.Lock()

    def total_builds(self) -> int:
        return sum(len(calls) for calls in self.calls.values())

    def resolve(self, conn, kind, key, *, election_cycle=None, today=None):
        with self._lock:
            self.resolve_calls.append({"kind": kind, "key": key, "election_cycle": election_cycle})
        if kind == "candidate" and key in (1, 2, "1", "2"):
            return copy.deepcopy(CANDIDATES[int(key)])
        if kind == "race" and key in RACES:
            entity = copy.deepcopy(RACES[key])
            current = CLOCK.today.year + CLOCK.today.year % 2
            entity["cycle"] = election_cycle if election_cycle is not None else current
            return entity
        return None

    def builder(self, name):
        def fake(conn, entity, **kwargs):
            with self._lock:
                self.calls[name].append({"conn": conn, "entity": entity, **kwargs})
                serial = len(self.calls[name])
            if self.hook is not None:
                self.hook(name)
            return self.payloads[name](entity, serial)

        return fake


@pytest.fixture
def fakes(monkeypatch):
    recorder = Fakes()
    monkeypatch.setattr(entity_routes, "resolve_entity", recorder.resolve)
    for name in BUILDERS:
        monkeypatch.setattr(entity_routes, name, recorder.builder(name))
    return recorder


@pytest.fixture
def cache_calls(monkeypatch):
    """Every RouteCache.get_or_compute call of the five ``entity_<tab>`` caches: its name, key and TTL."""
    calls: list[dict] = []
    real = RouteCache.get_or_compute

    def spy(self, cache_key, builder, ttl_seconds, **kwargs):
        if self.name in CACHE_NAME_OF.values():
            calls.append({"cache": self.name, "key": cache_key, "ttl": ttl_seconds})
        return real(self, cache_key, builder, ttl_seconds, **kwargs)

    monkeypatch.setattr(RouteCache, "get_or_compute", spy)
    return calls


# ---------------------------------------------------------------------------
# Apps and clients
# ---------------------------------------------------------------------------

@pytest.fixture
def make_app(tmp_path):
    def factory(**overrides):
        db_path = str(tmp_path / "entity_routes.db")
        init_db(db_path)
        conn = get_db(db_path)
        AppUser.create_or_update_password(conn, ADMIN_USER, ADMIN_PASS)
        conn.close()
        config = {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": False,
            "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 0,
        }
        config.update(overrides)
        return create_app(config)

    return factory


@pytest.fixture
def client(make_app, fakes):
    """Route caches off (the TESTING default); the fakes are installed."""
    return make_app().test_client()


@pytest.fixture
def cached_app(make_app, fakes, cache_calls):
    """Route caches on, the way production runs."""
    return make_app(ROUTE_PERF_CACHE_ENABLED=True)


@pytest.fixture
def cached_client(cached_app):
    return cached_app.test_client()


# ---------------------------------------------------------------------------
# URLs and expected windows
# ---------------------------------------------------------------------------

def parent_path(kind: str) -> str:
    return "/candidates/1" if kind == "candidate" else f"/analytics/state-races/{RACE_KEY}"


def url_of(kind: str, target: str, query: str = "", *, key=None) -> str:
    base = parent_path(kind) if key is None else key
    url = f"{base}/{target}"
    return f"{url}?{query}" if query else url


def label_of(kind: str) -> str:
    return CANDIDATE_LABEL if kind == "candidate" else RACE_LABEL


def page_default(kind: str, args: dict) -> str:
    """The default scope key of a tab page: candidates all time, races the cycle of ``election_cycle`` / today."""
    if kind == "candidate":
        return "all"
    return f"cycle:{args.get('election_cycle') or 2026}"


def scope_of(kind: str, query: str):
    args = dict(parse_qsl(query))
    return resolve_time_scope(default=page_default(kind, args), axis="transaction", today=TODAY, args=args)


def window_of(args: dict, default: str = "cycle:1990"):
    """(key, start, end) a link's query resolves to; the default is a sentinel no page uses (see I3)."""
    scope = resolve_time_scope(default=default, axis="transaction", today=TODAY, args=args)
    return scope.key, scope.start, scope.end


# (query, builder date_from, builder date_to) -- hand-computed from cycle_window(c) = (c-1-01-01, c-12-31)
CANDIDATE_WINDOWS = [
    pytest.param("", None, None, id="default-all-time-is-unbounded"),
    pytest.param("period=cycle:2024", date(2023, 1, 1), date(2024, 12, 31), id="cycle-2024"),
    pytest.param("period=all", None, None, id="period-all"),
    pytest.param("date_from=2025-02-01&date_to=2025-09-30", date(2025, 2, 1), date(2025, 9, 30), id="custom"),
    pytest.param("date_from=2025-02-01", date(2025, 2, 1), None, id="from-only"),
    pytest.param("date_to=2025-09-30", None, date(2025, 9, 30), id="to-only"),
]
RACE_WINDOWS = [
    pytest.param("", date(2025, 1, 1), date(2026, 12, 31), id="default-current-cycle"),
    pytest.param("period=cycle:2024", date(2023, 1, 1), date(2024, 12, 31), id="cycle-2024"),
    pytest.param("period=all", None, None, id="period-all"),
    pytest.param("election_cycle=2024", date(2023, 1, 1), date(2024, 12, 31), id="election-cycle-drives-the-default"),
    pytest.param("election_cycle=2024&period=all", None, None, id="explicit-period-beats-the-cycle-default"),
    pytest.param("date_from=2025-02-01&date_to=2025-09-30", date(2025, 2, 1), date(2025, 9, 30), id="custom"),
]

def _window_params():
    cases = []
    for kind, windows in (("candidate", CANDIDATE_WINDOWS), ("race", RACE_WINDOWS)):
        for target in TARGETS:
            for window in windows:
                query, start, end = window.values
                cases.append(pytest.param(kind, target, query, start, end, id=f"{kind}-{target}-{window.id}"))
    return cases


# ---------------------------------------------------------------------------
# Page parsing (stdlib HTMLParser)
# ---------------------------------------------------------------------------

class Page(HTMLParser):
    """h1 / h2 text (with the tag spans inside h2), anchors, tables with rows (cells, links) and ids."""

    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self.markup = markup
        self.h1: list[str] = []
        self.h2: list[dict] = []
        self.anchors: list[dict] = []
        self.tables: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self._text: list[str] = []
        self._skip = 0
        self._h1: list[str] | None = None
        self._h2: dict | None = None
        self._span: dict | None = None
        self._anchor: dict | None = None
        self._tables: list[dict] = []
        self._row: dict | None = None
        self._cell: list[str] | None = None
        self.feed(markup)
        self.close()

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text).split())

    def handle_starttag(self, tag, attrs):
        attributes = {name: (value or "") for name, value in attrs}
        classes = set(attributes.get("class", "").split())
        if "id" in attributes:
            self.by_id[attributes["id"]] = attributes
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "h1":
            self._h1 = []
        elif tag == "h2":
            self._h2 = {"text": [], "spans": []}
        elif tag == "span" and self._h2 is not None:
            self._span = {"classes": classes, "text": []}
        elif tag == "table":
            table = {"classes": classes, "attrs": attributes, "rows": []}
            self._tables.append(table)
            self.tables.append(table)
        elif tag == "tr" and self._tables:
            self._row = {"cells": [], "links": [], "header": False}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._row["header"] = self._row["header"] or tag == "th"
        elif tag == "a":
            self._anchor = {"href": attributes.get("href", ""), "text": [], "classes": classes,
                            "attrs": attributes, "row": self._row}

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "h1" and self._h1 is not None:
            self.h1.append(" ".join("".join(self._h1).split()))
            self._h1 = None
        elif tag == "span" and self._span is not None and self._h2 is not None:
            self._span["text"] = " ".join("".join(self._span["text"]).split())
            self._h2["spans"].append(self._span)
            self._span = None
        elif tag == "h2" and self._h2 is not None:
            self._h2["text"] = " ".join("".join(self._h2["text"]).split())
            self.h2.append(self._h2)
            self._h2 = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "a" and self._anchor is not None:
            anchor = self._anchor
            anchor["text"] = " ".join("".join(anchor["text"]).split())
            self.anchors.append(anchor)
            if anchor["row"] is not None:
                anchor["row"]["links"].append((anchor["href"], anchor["text"]))
            self._anchor = None
        elif tag == "tr" and self._row is not None and self._tables:
            self._row["text"] = " ".join(self._row["cells"])
            self._tables[-1]["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._tables:
            self._tables.pop()

    def handle_data(self, data):
        if self._skip:
            return
        self._text.append(data)
        for target in (self._h1, self._cell, self._h2 and self._h2["text"], self._span and self._span["text"],
                       self._anchor and self._anchor["text"]):
            if target is not None:
                target.append(data)

    # -- queries -----------------------------------------------------------------------------------------------

    def rows_with(self, *needles: str) -> list[tuple[dict, dict]]:
        """(table, row) of every data row whose text holds every needle."""
        found = []
        for table in self.tables:
            for row in table["rows"]:
                if not row["header"] and all(needle in row["text"] for needle in needles):
                    found.append((table, row))
        return found

    def data_rows(self, table: dict) -> list[dict]:
        return [row for row in table["rows"] if not row["header"]]

    def anchor_path(self, anchor: dict) -> str:
        return unquote(urlsplit(anchor["href"]).path)


def get(client, url: str, status: int = 200) -> Page:
    response = client.get(url)
    assert response.status_code == status, f"GET {url} -> {response.status_code}, expected {status}"
    return Page(response.get_data(as_text=True))


def link_args(anchor: dict) -> dict:
    return dict(parse_qsl(urlsplit(anchor["href"]).query))


def marked(anchor: dict) -> bool:
    return "active" in anchor["classes"] or anchor["attrs"].get("aria-current", "") in ("page", "true")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_entity_tabs_and_blueprint_name_are_pinned():
    """Kills: a reordered, renamed or extra tab (the template links and the cache names follow this tuple); a
    blueprint named differently from the endpoint prefix the templates' url_for uses."""
    assert entity_routes.ENTITY_TABS == ("network", "risk", "donors", "geography", "relationships")
    assert entity_routes.bp.name == "entity_analytics"


def test_the_blueprint_is_registered_with_exactly_the_four_contract_routes(make_app):
    """Kills: forgetting to register the blueprint (step 6); a URL prefix; a fifth route (a JSON twin of a tab, a
    catch-all); a tab rule that is not `<tab>` or a candidate id that is not an int converter (``/candidates/abc/risk``
    must not reach the view)."""
    app = make_app()
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("entity_analytics.")]
    shapes = {re.sub(r"<(\w+):\w+>", r"<\1>", re.sub(r"<\w+>", "<str>", rule.rule)) for rule in rules}

    assert len(rules) == 4, sorted(rule.rule for rule in rules)
    assert shapes == {
        "/candidates/<int>/<str>",
        "/candidates/<int>/network.json",
        "/analytics/state-races/<str>/<str>",
        "/analytics/state-races/<str>/network.json",
    }
    for rule in rules:
        assert rule.methods - {"GET", "HEAD", "OPTIONS"} == set(), f"{rule.rule} accepts {rule.methods}"


def test_entity_cache_key_signature_is_pinned():
    """Kills: a helper with another argument order or names (the route and every test call it positionally)."""
    parameters = list(inspect.signature(entity_routes._entity_cache_key).parameters)
    assert parameters == ["kind", "key", "tab", "scope", "election_cycle", "params"]


SCOPE_ALL = resolve_time_scope(default="all", axis="transaction", today=TODAY, args={})
SCOPE_2024 = resolve_time_scope(default="all", axis="transaction", today=TODAY, args={"period": "cycle:2024"})
SCOPE_CUSTOM = resolve_time_scope(default="all", axis="transaction", today=TODAY,
                                  args={"date_from": "2025-02-01", "date_to": "2025-09-30"})


def test_entity_cache_key_tuple_is_the_contract_tuple():
    """Kills: the revision anywhere but first; a bare int key (the URL gives an int, the slug a str: one spelling);
    the scope object or its dates instead of ``scope_cache_key``; the cycle dropped or coerced; params left out."""
    key = entity_routes._entity_cache_key("candidate", 1, "risk", SCOPE_ALL, None, (50,))

    assert key == (entity_routes.ENTITY_CACHE_REV, "candidate", "1", "risk", scope_cache_key(SCOPE_ALL), None, (50,))
    assert type(key[2]) is str
    assert entity_routes._entity_cache_key("candidate", "1", "risk", SCOPE_ALL, None, (50,)) == key
    assert hash(key) == hash(entity_routes._entity_cache_key("candidate", 1, "risk", SCOPE_ALL, None, (50,)))

    race = entity_routes._entity_cache_key("race", RACE_KEY, "network:ego", SCOPE_2024, 2024, (40, 25, 15, 15))
    assert race == (entity_routes.ENTITY_CACHE_REV, "race", RACE_KEY, "network:ego", scope_cache_key(SCOPE_2024),
                    2024, (40, 25, 15, 15))


def test_entity_cache_key_reads_the_revision_when_the_key_is_built(monkeypatch):
    """Kills: a revision captured at import (a default argument, a module-level tuple): bumping ENTITY_CACHE_REV must
    bump every key so a release that changes a payload never reads the previous release's entries."""
    monkeypatch.setattr(entity_routes, "ENTITY_CACHE_REV", "ea-next")
    key = entity_routes._entity_cache_key("candidate", 1, "risk", SCOPE_ALL, None, (50,))
    assert key[0] == "ea-next"


KEY_VARIANTS = [
    pytest.param(dict(kind="race"), id="kind"),
    pytest.param(dict(key=2), id="entity"),
    pytest.param(dict(tab="donors"), id="tab"),
    pytest.param(dict(scope=SCOPE_2024), id="scope-preset"),
    pytest.param(dict(scope=SCOPE_CUSTOM), id="scope-custom"),
    pytest.param(dict(election_cycle=2024), id="cycle"),
    pytest.param(dict(params=(25,)), id="params"),
]


@pytest.mark.parametrize("change", KEY_VARIANTS)
def test_entity_cache_key_separates_everything_that_changes_the_build(change):
    """Kills: a key that ignores the kind (candidate 1 vs race "1"), the entity, the tab, the window, the cycle or the
    limits: two tabs, windows, cycles, entities or cap sets would share one entry."""
    base_args = dict(kind="candidate", key=1, tab="risk", scope=SCOPE_ALL, election_cycle=None, params=(50,))
    base = entity_routes._entity_cache_key(**base_args)
    changed = entity_routes._entity_cache_key(**{**base_args, **change})
    assert changed != base


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------

def _assert_404_without_side_effects(client, fakes, cache_calls, bad_url: str, control_url: str) -> None:
    """The control (a known entity / tab answers 200 through the same machinery, so a missing route cannot pass),
    then the 404 request, which must leave builders and caches exactly where the control left them."""
    assert client.get(control_url).status_code == 200, f"control {control_url} did not render"
    builds, cached = fakes.total_builds(), len(cache_calls)

    assert client.get(bad_url).status_code == 404
    assert fakes.total_builds() == builds, "a 404 must not run a builder"
    assert len(cache_calls) == cached, "a 404 must not touch the entity caches"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("bad, control", [
    pytest.param("/candidates/999/{target}", "/candidates/1/{target}", id="candidate"),
    pytest.param("/analytics/state-races/no-such-race/{target}",
                 f"/analytics/state-races/{RACE_KEY}/{{target}}", id="race"),
])
def test_unknown_entity_is_404_and_runs_no_builder(cached_client, fakes, cache_calls, bad, control, target):
    """Kills: a builder that runs before the entity check (a 404 that still costs a build); a cached 404; a 200 page
    for an entity ``resolve_entity`` rejected."""
    _assert_404_without_side_effects(cached_client, fakes, cache_calls, bad.format(target=target),
                                     control.format(target=target))


BAD_TABS = ["bogus", "Risk", "RISK", "risk.json", "networks", "overview"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", BAD_TABS)
def test_unknown_tab_is_404_even_for_a_known_entity(cached_client, fakes, cache_calls, kind, tab):
    """Kills: a lax tab match (case-insensitive, prefix, ``in``) or a fall-through that renders a blank page; a build
    for a tab that does not exist."""
    _assert_404_without_side_effects(cached_client, fakes, cache_calls, url_of(kind, tab), url_of(kind, "risk"))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("view", ["bogus", "money_flow", "vendor_network"])
def test_network_json_with_an_unknown_view_is_404(cached_client, fakes, cache_calls, kind, view):
    """Phase A knows only ``ego`` (step 8 adds the others): every other value is a 404, not a silent fall back to ego.
    Kills: ignoring ``view``; accepting the step-8 views early."""
    _assert_404_without_side_effects(cached_client, fakes, cache_calls, url_of(kind, "network.json", f"view={view}"),
                                     url_of(kind, "network.json", "view=ego"))


def test_resolve_entity_gets_the_kind_the_int_candidate_id_and_the_race_slug(client, fakes):
    """Kills: a candidate id passed as a str (the URL converter gives an int); a race slug rewritten; the wrong kind."""
    assert client.get(url_of("candidate", "risk")).status_code == 200
    assert client.get(url_of("race", "risk")).status_code == 200

    candidate_call, race_call = fakes.resolve_calls[0], fakes.resolve_calls[-1]
    assert candidate_call["kind"] == "candidate"
    assert candidate_call["key"] == 1
    assert type(candidate_call["key"]) is int
    assert race_call["kind"] == "race"
    assert race_call["key"] == RACE_KEY


@pytest.mark.parametrize("target", TARGETS)
def test_race_election_cycle_reaches_resolve_entity(client, fakes, target):
    """None when the request has no ``election_cycle``, the int when it has.  Kills: dropping it (the 2024 candidates
    are unreachable); a str ('2024'); the resolved window's cycle or the current cycle sent when the arg is absent
    (reading I2: the request arg only)."""
    assert client.get(url_of("race", target)).status_code == 200
    assert client.get(url_of("race", target, "election_cycle=2024")).status_code == 200

    absent = [c for c in fakes.resolve_calls if c["kind"] == "race"][0]
    given = [c for c in fakes.resolve_calls if c["kind"] == "race"][-1]
    assert absent["election_cycle"] is None
    assert given["election_cycle"] == 2024
    assert type(given["election_cycle"]) is int


# ---------------------------------------------------------------------------
# 200: window, builder, chrome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind, target, query, start, end", _window_params())
def test_the_one_right_builder_gets_the_resolved_window(client, fakes, kind, target, query, start, end):
    """Kills: every builder called (or the wrong one: tab-to-builder swapped); no window passed; a window passed as ISO
    strings or positionally; the page default applied where the request gave a window (``period=all`` and half-open
    ranges stay unbounded on the open side); the race default taken from today instead of ``election_cycle``;
    the candidate default bounded (the page default is all time, plan N10)."""
    response = client.get(url_of(kind, target, query))
    assert response.status_code == 200, response.status_code

    wanted = BUILDER_OF[target]
    assert len(fakes.calls[wanted]) == 1, f"{wanted} called {len(fakes.calls[wanted])} times"
    for name in BUILDERS:
        if name != wanted:
            assert fakes.calls[name] == [], f"{name} must not run for the {target} tab"

    call = fakes.calls[wanted][0]
    assert call["date_from"] == start
    assert call["date_to"] == end
    assert call["conn"] is not None
    assert call["entity"]["kind"] == kind
    assert call["entity"]["key"] == ("1" if kind == "candidate" else RACE_KEY)
    if kind == "race" and "election_cycle=2024" in query:
        assert call["entity"]["cycle"] == 2024


def _chrome_cases():
    cases = []
    queries = {
        "candidate": ["", "period=cycle:2024", "date_from=2025-02-01&date_to=2025-09-30"],
        "race": ["", "period=cycle:2024&election_cycle=2024", "election_cycle=2024",
                 "date_from=2025-02-01&date_to=2025-09-30"],
    }
    for kind in KINDS:
        for tab in TABS:
            for query in queries[kind]:
                cases.append(pytest.param(kind, tab, query, id=f"{kind}-{tab}-{query or 'default'}"))
    return cases


@pytest.mark.parametrize("kind, tab, query", _chrome_cases())
def test_tab_page_chrome(client, fakes, kind, tab, query):
    """The page frame every tab shares.  Kills: an h1 without the entity label or tab name (or a second h1); a
    missing scope bar or two of them (the subnav partial must not include the bar); the bar before the h1; a bar that
    shows another scope than the resolved one."""
    page = get(client, url_of(kind, tab, query))
    label = label_of(kind)
    scope = scope_of(kind, query)
    name = {"network": "Networks?", "risk": "Risk", "donors": "Donors", "geography": "Geography",
            "relationships": "Relationships"}[tab]

    assert len(page.h1) == 1, page.h1
    assert re.fullmatch(rf"{re.escape(label)}\s+—\s+{name}", page.h1[0]), page.h1[0]

    assert page.markup.count('class="time-scope-bar') == 1
    assert page.markup.index("<h1") < page.markup.index('class="time-scope-bar')
    assert re.findall(r'data-scope-key="([^"]*)"', page.markup) == [scope.key]


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("kind, query, reset_period", [
    pytest.param("candidate", "", None, id="candidate-default"),
    pytest.param("candidate", "period=cycle:2024", "all", id="candidate-not-default"),
    pytest.param("race", "", None, id="race-default"),
    pytest.param("race", "period=cycle:2024", "cycle:2026", id="race-not-default"),
    pytest.param("race", "election_cycle=2024", None, id="race-cycle-default-is-the-election-cycle"),
    pytest.param("race", "election_cycle=2024&period=cycle:2026", "cycle:2024", id="race-cycle-2024-not-default"),
])
def test_scope_bar_default_key_and_reset_link(client, fakes, kind, tab, query, reset_period):
    """The route passes ``scope_default_key`` and resolves the scope against the same default.  Kills: a reset link
    that points at the wrong default (a candidate page resetting to the current cycle, a race page ignoring its
    ``election_cycle``); a reset link on a page already at its default; a scope resolved with another default than
    the one the bar advertises."""
    page = get(client, url_of(kind, tab, query))
    resets = [a for a in page.anchors if "time-scope-reset" in a["classes"]]
    if reset_period is None:
        assert resets == [], "a page at its default scope must not offer Reset"
        return
    assert len(resets) == 1
    assert link_args(resets[0]).get("period") == reset_period


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("query, label", [
    pytest.param("", None, id="default"),
    pytest.param("period=cycle:2024", "2024 Cycle", id="cycle-2024"),
    pytest.param("date_from=2025-02-01&date_to=2025-09-30", "Custom range", id="custom"),
])
def test_windowed_headings_carry_the_scope_tag(client, fakes, kind, tab, query, label):
    """Windowed ``<h2>``s are tagged with the page's scope label, not a fixed one.  Kills: no tag at all; every tag
    hard-coded ('All time' / '2026 Cycle'); a windowed section tagged as fixed (it would claim the window is ignored)."""
    page = get(client, url_of(kind, tab, query))
    expected = label or scope_of(kind, query).label
    windowed = [
        span["text"] for h2 in page.h2 for span in h2["spans"]
        if "scope-tag" in span["classes"] and "scope-tag-fixed" not in span["classes"]
    ]
    assert expected in windowed, f"no windowed <h2> tagged {expected!r}; tags: {windowed}"


def _subnav(page: Page, base: str) -> dict:
    """The subnav anchors by name.  The scope bar's own links (class ``time-scope-*``) point at the page itself and are
    not part of it; Overview must read 'Overview'; a tab link must read like its tab (``network`` -> 'Networks')."""
    found = {}
    for anchor in page.anchors:
        if any(token.startswith("time-scope") for token in anchor["classes"]):
            continue
        path = page.anchor_path(anchor)
        text = anchor["text"].strip().lower()
        if path == base and text == "overview":
            found.setdefault("overview", []).append(anchor)
        for tab in TABS:
            if path == f"{base}/{tab}" and tab.rstrip("s") in text:
                found.setdefault(tab, []).append(anchor)
    return found


def _assert_subnav(page: Page, kind: str, base: str, active: str, request_query: str, expected: tuple):
    """Overview + five tab links; the active one marked, the others not; every link carries the scope params."""
    links = _subnav(page, base)
    assert set(links) == {"overview", *TABS}, f"subnav links present: {sorted(links)}"

    for name, anchors in links.items():
        if name == active:
            assert any(marked(a) for a in anchors), f"the active item {name!r} is not marked"
        else:
            assert not any(marked(a) for a in anchors), f"{name!r} is marked but {active!r} is the page"

    request_args = dict(parse_qsl(request_query))
    for name, anchors in links.items():
        for anchor in anchors:
            args = link_args(anchor)
            assert window_of(args) == expected, f"{name} link {anchor['href']!r} lands on {window_of(args)}, not {expected}"
            if kind == "race" and "election_cycle" in request_args:
                assert args.get("election_cycle") == request_args["election_cycle"], anchor["href"]
            if kind == "race" and "election_cycle" not in request_args:
                assert args.get("election_cycle") in (None, "2026"), anchor["href"]
            if kind == "candidate":
                assert "election_cycle" not in args, anchor["href"]


@pytest.mark.parametrize("kind, tab, query", _chrome_cases())
def test_subnav_on_every_tab_marks_the_active_tab_and_carries_the_scope(client, fakes, kind, tab, query):
    """Kills: a subnav missing Overview or a tab; no active marker, or the marker on every item; links that drop the
    scope (the reader loses the window on every click) or that resolve to another window than the page's; a race
    link that drops ``election_cycle`` (the 2024 candidates vanish); a candidate link that grows one."""
    page = get(client, url_of(kind, tab, query))
    scope = scope_of(kind, query)
    _assert_subnav(page, kind, parent_path(kind), tab, query, (scope.key, scope.start, scope.end))


def test_subnav_link_with_a_custom_range_keeps_both_bounds(client, fakes):
    """Kills: a custom window collapsed to its preset key, or only one bound carried (hand-pinned, not through the
    resolver)."""
    page = get(client, url_of("candidate", "risk", "date_from=2025-02-01&date_to=2025-09-30"))
    for anchors in _subnav(page, "/candidates/1").values():
        args = link_args(anchors[0])
        assert args.get("date_from") == "2025-02-01", anchors[0]["href"]
        assert args.get("date_to") == "2025-09-30", anchors[0]["href"]
        assert "period" not in args, anchors[0]["href"]


# ---------------------------------------------------------------------------
# 200: what each tab shows of its payload
# ---------------------------------------------------------------------------

def check_risk(page: Page, n: int) -> None:
    text = page.text
    assert re.search(r"threshold.{0,160}\$12,345\.00|\$12,345\.00.{0,160}threshold", text, re.I), (
        "the threshold line (large single contribution threshold $12,345.00) is missing"
    )

    big = page.rows_with(f"Pat Largegiver #{n}", "25,000")
    assert len(big) == 1, f"large single contribution flag rows: {len(big)}"
    table, row = big[0]
    assert re.search(r"high", row["text"], re.I), f"severity missing from {row['text']!r}"
    assert len(page.data_rows(table)) == 3, "the flag table must list all three flags"

    reconciliation = page.rows_with(GROUP_B, "$4,321.50")
    assert reconciliation, "the reconciliation row (committee + difference) is missing"
    assert reconciliation[0][0] is not table, "reconciliation rows belong in their own table"

    sections = [h2 for h2 in page.h2 if "reconcil" in h2["text"].lower()]
    assert len(sections) == 1, [h2["text"] for h2 in page.h2]
    fixed = [s for s in sections[0]["spans"] if "scope-tag-fixed" in s["classes"]]
    assert [s["text"] for s in fixed] == ["All time"], "the reconciliation table is all-time and must say so"


def check_donors(page: Page, n: int) -> None:
    for label, amount in ((GROUP_A, "$40,000.00"), (GROUP_B, "$20,000.00")):
        assert page.rows_with(label, amount), f"group concentration row for {label} ({amount}) is missing"
    for donor_class, amount in (("individual", "$45,000.00"), ("organization", "$15,000.00")):
        rows = [r for _t, r in page.rows_with(amount) if donor_class in r["text"].lower()]
        assert rows, f"class breakdown row {donor_class} {amount} is missing"

    pat = page.rows_with(f"Pat Largegiver #{n}", "$25,000.00")
    assert pat, "top donor row is missing"
    hrefs = [unquote(urlsplit(h).path) for h, _t in pat[0][1]["links"]]
    assert f"/donors/key/{PAT_KEY}" in hrefs, f"top donor must link to the donor page; links: {hrefs}"

    rae = page.rows_with("Rae Hidden", "$18,000.00")
    assert rae, "a redacted donor still counts and is listed"
    for anchor in page.anchors:
        assert RAE_KEY not in unquote(anchor["href"]), f"redacted donor linked: {anchor['href']}"
    assert not any("Rae Hidden" in a["text"] for a in page.anchors), "the redacted donor's name must not be a link"

    assert page.rows_with("Sam Sharedgiver", "$9,000.00"), "shared donor row is missing"


def check_geography(page: Page, n: int) -> None:
    states = page.rows_with("$30,000.00")
    il = [(t, r) for t, r in states if "IL" in r["cells"]]
    mo = [(t, r) for t, r in page.rows_with("$10,000.00") if "MO" in r["cells"]]
    assert il and mo, "state rows (IL $30,000.00, MO $10,000.00) are missing"
    assert il[0][0] is mo[0][0], "both states belong in the same table"

    cities = page.rows_with(f"Springfield #{n}", "$18,000.00")
    assert cities, "city row is missing"
    assert cities[0][0] is not il[0][0], "cities and states are separate tables"
    assert page.rows_with("Chicago", "$12,000.00")
    assert page.rows_with("St Louis", "$7,000.00")

    for number, name, amount in ((1, "Jan", "$4,000.00"), (2, "Feb", "$0.00"), (3, "Mar", "$6,000.00")):
        pattern = re.compile(rf"2026-0{number}|{name}[a-z]*\.? 2026")
        rows = [r for _t, r in page.rows_with(amount) if pattern.search(r["text"])]
        assert rows, f"monthly series row {number} ({amount}) is missing (zero months stay in the series)"


def check_relationships(page: Page, n: int) -> None:
    name = f"Fictional Victory Fund #{n}"
    rows = page.rows_with(name)
    assert len(rows) == 1, "co-giving row of the first committee is missing"
    row = rows[0][1]
    assert "$12,000.00" in row["text"] or "$8,000.00" in row["text"], f"shared amount missing from {row['text']!r}"
    assert "4" in row["cells"], f"shared donor count missing from {row['cells']}"
    paths = [unquote(urlsplit(h).path) for h, t in row["links"] if t == name]
    assert paths == ["/committees/777"], f"co-giving committee must link to its page; links: {row['links']}"

    other = page.rows_with("Imaginary Leadership PAC", "$2,200.00")
    assert other, "the second co-giving committee is missing"
    assert [unquote(urlsplit(h).path) for h, _t in other[0][1]["links"]][0] == "/committees/778"
    assert "Pat Largegiver" in page.text, "the top shared donors of a co-giving committee are shown"
    assert page.rows_with("Sam Sharedgiver", "$9,000.00"), "shared donors across groups are missing"


def check_network(page: Page, n: int) -> None:
    vendor = page.rows_with(f"Acme Print Fictional #{n}", "$12,345.67", GROUP_A)
    assert len(vendor) == 1, "committee_vendor edge row (source, target, currency weight) is missing"
    table = vendor[0][0]

    donor = page.rows_with(f"Pat Largegiver #{n}", "$25,000.00", GROUP_A)
    assert donor and donor[0][0] is table, "all edges belong in one table"
    assert len(page.data_rows(table)) == 3, "the edge table must list every edge, no more"

    assert "data-table" in table["classes"], "the edge table must be a sortable data-table"
    assert "data-server-sort" not in table["attrs"], "client-side sort (table_sort.js), not a server sort"

    for definition in EDGE_DEFINITIONS.values():
        assert definition["edge_label"] in page.text, f"edge type heading {definition['edge_label']!r} missing"
    assert "entity-network-graph" in page.by_id, "the graph placeholder for the step-7 renderer is missing"


CONTENT = {
    "risk": check_risk,
    "donors": check_donors,
    "geography": check_geography,
    "relationships": check_relationships,
    "network": check_network,
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
def test_every_tab_renders_for_both_kinds(client, fakes, kind, tab):
    """The control of the 404 tests and the content pins of the five tabs (presence of the fake payload's values; see
    the module docstring for what is deliberately not pinned).  Kills: a template that renders another tab's
    payload, drops a section, truncates a list, or prints raw floats instead of currency."""
    page = get(client, url_of(kind, tab))
    CONTENT[tab](page, 1)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
def test_every_tab_renders_with_empty_payloads(client, fakes, kind, tab):
    """An entity with no money in the window (empty lists, zero totals, reconciliation unavailable) is a 200 with the
    frame and no traceback.  Kills: ``payload.top_donors[0]`` / division by a zero total / a missing-key crash in any
    of the five templates; an empty page without the subnav (the reader could not leave)."""
    fakes.payloads = EMPTY
    page = get(client, url_of(kind, tab))
    assert len(page.h1) == 1
    assert len(_subnav(page, parent_path(kind))) == 6


def test_network_tab_links_committee_and_donor_nodes(client, fakes):
    """Reading I5 of "a linked, sortable edge table": committee nodes link to /committees/<id>, donor nodes to
    /donors/key/<key>.  Kills: an edge table of bare text."""
    page = get(client, url_of("candidate", "network"))
    vendor_row = page.rows_with("Acme Print Fictional #1", "$12,345.67")[0][1]
    donor_row = page.rows_with("Pat Largegiver #1", "$25,000.00")[0][1]
    vendor_paths = [unquote(urlsplit(h).path) for h, _t in vendor_row["links"]]
    donor_paths = [unquote(urlsplit(h).path) for h, _t in donor_row["links"]]
    assert "/committees/901" in vendor_paths, vendor_row["links"]
    assert f"/donors/key/{PAT_KEY}" in donor_paths, donor_row["links"]
    assert "/committees/901" in donor_paths, donor_row["links"]


# ---------------------------------------------------------------------------
# network.json and the graph placeholder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("query", ["", "view=ego"])
def test_network_json_is_the_builder_payload_plus_the_view(client, fakes, kind, query):
    """Kills: HTML or a wrapped body; a missing key; a payload that is not the builder's (nodes / edges recomputed or
    emptied); no ``view`` echo, or a default other than ego; extra keys (the exact set is the contract)."""
    response = client.get(url_of(kind, "network.json", query))
    assert response.status_code == 200
    assert response.mimetype == "application/json"

    body = response.get_json()
    expected = network_payload(fakes.resolve(None, kind, 1 if kind == "candidate" else RACE_KEY), 1)
    assert set(body) == {"entity", "nodes", "edges", "centrality", "summary", "view"}
    assert body["view"] == "ego"
    assert body["nodes"] == expected["nodes"]
    assert body["edges"] == expected["edges"]
    assert body["centrality"] == expected["centrality"]
    assert body["summary"] == expected["summary"]
    assert body["entity"]["key"] == expected["entity"]["key"]
    assert body["entity"]["kind"] == kind


@pytest.mark.parametrize("kind, query", [
    pytest.param("candidate", "", id="candidate-default"),
    pytest.param("candidate", "period=cycle:2024", id="candidate-cycle"),
    pytest.param("race", "", id="race-default"),
    pytest.param("race", "period=cycle:2024&election_cycle=2024", id="race-cycle"),
])
def test_network_tab_graph_placeholder_points_at_the_json_with_the_scope(client, fakes, kind, query):
    """Kills: a placeholder without ``data-src``; a src that drops the view, the scope or (races) the election cycle;
    a src that points at another entity or the HTML tab; two placeholders."""
    page = get(client, url_of(kind, "network", query))
    assert "entity-network-graph" in page.by_id
    assert page.markup.count('id="entity-network-graph"') == 1

    src = page.by_id["entity-network-graph"].get("data-src", "")
    parts = urlsplit(src)
    assert unquote(parts.path) == f"{parent_path(kind)}/network.json"
    args = dict(parse_qsl(parts.query))
    assert args.get("view") == "ego"
    request_args = dict(parse_qsl(query))
    expected = window_of(request_args, default=page_default(kind, request_args))
    assert window_of(args) == expected
    if "election_cycle" in request_args:
        assert args.get("election_cycle") == request_args["election_cycle"]


# ---------------------------------------------------------------------------
# Caching (N14)
# ---------------------------------------------------------------------------

def _marker(tab: str, n: int) -> str:
    return {
        "network": f"Acme Print Fictional #{n}",
        "risk": f"Pat Largegiver #{n}",
        "donors": f"Pat Largegiver #{n}",
        "geography": f"Springfield #{n}",
        "relationships": f"Fictional Victory Fund #{n}",
    }[tab]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
def test_the_same_request_twice_builds_once_and_serves_the_stored_payload(cached_client, fakes, cache_calls, kind, tab):
    """The second response must carry the FIRST build's marker (#1), not a fresh one.  Kills: no cache at all; a cache
    that is read but never written; a cache that is written but rebuilt anyway (a no-op 'cache' passes a call count
    only if the builder output is identical: the serial in the marker makes it observable); caching through
    something other than ``RouteCache.get_or_compute`` (the single-flight path), under a name other than
    ``entity_<tab>``, or with a key that is not the contract tuple."""
    url = url_of(kind, tab)
    first = get(cached_client, url)
    second = get(cached_client, url)

    assert len(fakes.calls[BUILDER_OF[tab]]) == 1
    assert _marker(tab, 1) in first.text
    assert _marker(tab, 1) in second.text
    assert _marker(tab, 2) not in second.text

    assert len(cache_calls) == 2
    first_call = cache_calls[0]
    assert first_call["cache"] == CACHE_NAME_OF[tab]
    key = first_call["key"]
    scope = scope_of(kind, "")
    assert key[0] == entity_routes.ENTITY_CACHE_REV
    assert key[1] == kind
    assert key[2] == ("1" if kind == "candidate" else RACE_KEY)
    assert key[3] == tab
    assert key[5] == (None if kind == "candidate" else 2026), "reading I1: the resolved cycle, never the raw argument"
    assert key[4].split(":", 1)[1] == f"{scope.start.isoformat() if scope.start else ''}:{scope.end.isoformat() if scope.end else ''}"
    assert isinstance(key[-1], tuple), "params (the tab's limits) close the key"
    assert cache_calls[1]["key"] == key, "the same request must map to the same key"


@pytest.mark.parametrize("kind", KINDS)
def test_network_json_and_the_html_tab_are_separate_entries(cached_client, fakes, cache_calls, kind):
    """The JSON view is cached under tab ``network:<view>`` in the network cache.  Kills: the JSON served the HTML tab's
    entry (or the reverse); the JSON cached under the bare ``network`` tab; a JSON route that bypasses the cache."""
    assert cached_client.get(url_of(kind, "network")).status_code == 200
    assert cached_client.get(url_of(kind, "network.json")).status_code == 200
    assert len(fakes.calls["get_entity_network"]) == 2, "html tab and json each build once"

    assert cached_client.get(url_of(kind, "network")).status_code == 200
    assert cached_client.get(url_of(kind, "network.json", "view=ego")).status_code == 200
    assert len(fakes.calls["get_entity_network"]) == 2, "both are served from their own entries afterwards"

    assert [(c["cache"], c["key"][3]) for c in cache_calls] == [("entity_network", "network"),
                                                                ("entity_network", "network:ego")] * 2


SEPARATE = [
    pytest.param("candidate", "donors", "", "period=cycle:2024", id="candidate-other-window"),
    pytest.param("candidate", "donors", "", "date_from=2025-02-01&date_to=2025-09-30", id="candidate-custom-window"),
    pytest.param("candidate", "donors", "date_from=2025-02-01", "date_to=2025-02-01", id="candidate-bounds-not-merged"),
    pytest.param("race", "donors", "", "period=cycle:2024", id="race-other-window"),
    pytest.param("race", "donors", "period=all", "period=all&election_cycle=2024", id="race-other-cycle-same-window"),
    pytest.param("race", "risk", "period=all", "period=all&election_cycle=2024", id="race-other-cycle-risk"),
]


@pytest.mark.parametrize("kind, tab, first_query, second_query", SEPARATE)
def test_a_different_window_or_cycle_builds_separately(cached_client, fakes, kind, tab, first_query, second_query):
    """Kills: a key without the window (the 2024 page served the all-time numbers); a key without the cycle (the 2024
    candidates served the current ones: the window is identical in the cycle case, only ``election_cycle`` differs); a
    key that merges the two bounds."""
    assert cached_client.get(url_of(kind, tab, first_query)).status_code == 200
    assert cached_client.get(url_of(kind, tab, second_query)).status_code == 200
    builder = BUILDER_OF[tab]
    assert len(fakes.calls[builder]) == 2
    assert cached_client.get(url_of(kind, tab, first_query)).status_code == 200
    assert cached_client.get(url_of(kind, tab, second_query)).status_code == 200
    assert len(fakes.calls[builder]) == 2, "each variant is then served from its own entry"


def test_a_different_tab_builds_its_own_payload(cached_client, fakes):
    """Kills: one shared cache keyed without the tab: the donors request would be served the risk payload."""
    risk = get(cached_client, url_of("candidate", "risk"))
    donors = get(cached_client, url_of("candidate", "donors"))
    assert len(fakes.calls["get_entity_risk"]) == 1
    assert len(fakes.calls["get_entity_donors"]) == 1
    assert "Rae Hidden" in donors.text
    assert "Rae Hidden" not in risk.text


@pytest.mark.parametrize("first, second", [
    pytest.param("/candidates/1/donors", "/candidates/2/donors", id="candidate"),
    pytest.param(f"/analytics/state-races/{RACE_KEY}/donors", f"/analytics/state-races/{OTHER_RACE_KEY}/donors",
                 id="race"),
])
def test_a_different_entity_builds_separately(cached_client, fakes, first, second):
    """Kills: a key without the entity id / slug (entity 2 served entity 1's numbers)."""
    assert cached_client.get(first).status_code == 200
    assert cached_client.get(second).status_code == 200
    calls = fakes.calls["get_entity_donors"]
    assert len(calls) == 2
    assert calls[0]["entity"]["key"] != calls[1]["entity"]["key"]


def test_candidate_and_race_with_the_same_key_text_do_not_share_an_entry(cached_client, fakes, cache_calls,
                                                                          monkeypatch):
    """The key carries the kind.  Kills: a key that is only (key, tab, window): candidate "1" and a race slug "1" collide."""
    monkeypatch.setitem(RACES, "1", {**RACES[OTHER_RACE_KEY], "key": "1", "label": "A race named one"})
    assert cached_client.get("/candidates/1/donors").status_code == 200
    assert cached_client.get("/analytics/state-races/1/donors").status_code == 200
    assert len(fakes.calls["get_entity_donors"]) == 2
    assert [c["key"][1:3] for c in cache_calls] == [("candidate", "1"), ("race", "1")]


def test_anonymous_refresh_is_redirected_before_any_build(cached_client, fakes, cache_calls):
    """LD4 for the new routes: 302 to the same path with the refresh keys dropped and every other parameter kept; no
    build, nothing cached.  The control (the same URL without the refresh key answers 200) follows the redirect check.
    Kills: a route that rebuilds on an anonymous ``?refresh_cache=1`` (a crawler could force the heaviest queries);
    a route that is not covered by the gate (an absolute or hand-built redirect)."""
    url = url_of("race", "donors", "period=cycle:2024&election_cycle=2024&refresh_cache=1")
    response = cached_client.get(url)
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == f"/analytics/state-races/{RACE_KEY}/donors"
    assert sorted(parse_qsl(location.query)) == [("election_cycle", "2024"), ("period", "cycle:2024")]
    assert fakes.total_builds() == 0
    assert cache_calls == []

    control = cached_client.get(url_of("race", "donors", "period=cycle:2024&election_cycle=2024"))
    assert control.status_code == 200
    assert fakes.total_builds() == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
def test_signed_in_refresh_forces_a_rebuild_and_stores_it(cached_app, fakes, kind, tab):
    """Kills: a refresh that is ignored (still the cached #1); a refresh that rebuilds but does not store (the next
    plain request would build again, or show #1); a refresh that never reads the cache afterwards; a rebuild on the
    plain requests around it."""
    client = cached_app.test_client()
    _login(client)
    url = url_of(kind, tab)
    builder = BUILDER_OF[tab]

    assert _marker(tab, 1) in get(client, url).text
    assert _marker(tab, 1) in get(client, url).text
    assert len(fakes.calls[builder]) == 1

    refreshed = get(client, url_of(kind, tab, "refresh_cache=1"))
    assert len(fakes.calls[builder]) == 2
    assert _marker(tab, 2) in refreshed.text

    after = get(client, url)
    assert len(fakes.calls[builder]) == 2, "the refreshed entry must serve the next plain request"
    assert _marker(tab, 2) in after.text


def test_the_cache_ttl_comes_from_the_entity_analytics_setting(make_app, fakes, cache_calls):
    """``ENTITY_ANALYTICS_CACHE_TTL_SECONDS`` lives in config.py (default the 21600 s analytics fallback) and create_app
    copies it into app.config; the route passes that value as the TTL.  Kills: a hard-coded TTL; a setting that
    config.py never defines or create_app never copies (the environment value would be silently ignored); a TTL
    taken from another page's setting."""
    assert app_config.ENTITY_ANALYTICS_CACHE_TTL_SECONDS == 21600

    default_app = make_app(ROUTE_PERF_CACHE_ENABLED=True)
    assert default_app.config["ENTITY_ANALYTICS_CACHE_TTL_SECONDS"] == 21600
    assert default_app.test_client().get(url_of("candidate", "risk")).status_code == 200
    assert cache_calls[-1]["ttl"] == 21600

    tuned = make_app(ROUTE_PERF_CACHE_ENABLED=True, ENTITY_ANALYTICS_CACHE_TTL_SECONDS=777)
    assert tuned.test_client().get(url_of("candidate", "donors")).status_code == 200
    assert cache_calls[-1]["cache"] == "entity_donors"
    assert cache_calls[-1]["ttl"] == 777


def test_caching_is_off_when_route_caches_are_off(client, fakes, cache_calls):
    """The TESTING default.  Kills: a cache that ignores ``ROUTE_PERF_CACHE_ENABLED`` (every other cached page in the
    suite assumes builds are not memoized under TESTING)."""
    assert client.get(url_of("candidate", "risk")).status_code == 200
    assert client.get(url_of("candidate", "risk")).status_code == 200
    assert len(fakes.calls["get_entity_risk"]) == 2
    assert cache_calls == []


def test_race_entry_does_not_outlive_a_cycle_rollover(cached_client, fakes, clock):
    """The repo's J-R4 lesson at route level: ``period=all`` has the same window all year, so only the resolved cycle
    keeps an entry built before a rollover from being served after it.  Reading I1: the key carries the entity's
    resolved cycle.  Kills: a key that holds the raw ``election_cycle`` argument (None) for races."""
    url = url_of("race", "donors", "period=all")
    assert cached_client.get(url).status_code == 200
    assert cached_client.get(url).status_code == 200
    assert len(fakes.calls["get_entity_donors"]) == 1

    clock.today = date(2027, 3, 1)  # the current cycle is now 2028
    assert cached_client.get(url).status_code == 200

    cycles = [call["entity"]["cycle"] for call in fakes.calls["get_entity_donors"]]
    assert cycles == [2026, 2028]


def test_concurrent_misses_on_one_key_build_once(cached_app, fakes, cache_calls):
    """Single-flight (N14): two requests that miss the same key while the first build runs wait for it.  The first
    build blocks until the second request has had time to reach the cache.  Kills: a get-then-set cache (both build);
    a lock that is per request.  (A very slow second request can only make this pass for a naive cache, never fail.)"""
    url = url_of("race", "risk")
    entered, release = threading.Event(), threading.Event()

    def hold(name):
        entered.set()
        release.wait(10)

    fakes.hook = hold
    responses: dict[str, object] = {}

    def run(name):
        responses[name] = cached_app.test_client().get(url)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    while not entered.wait(0.05) and first.is_alive():
        pass
    assert entered.is_set(), "the first request never reached the builder"
    second.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and len(fakes.calls["get_entity_risk"]) < 2:
        time.sleep(0.02)
    release.set()
    first.join(15)
    second.join(15)

    assert len(fakes.calls["get_entity_risk"]) == 1
    assert responses["first"].status_code == 200
    assert responses["second"].status_code == 200
    for name in ("first", "second"):
        assert _marker("risk", 1) in responses[name].get_data(as_text=True)


# ---------------------------------------------------------------------------
# Anonymous HTML hygiene (tests/test_request_hygiene.py LD2)
# ---------------------------------------------------------------------------

def _tokens(response, header: str) -> set[str]:
    tokens = set()
    for line in response.headers.getlist(header):
        for part in line.split(","):
            part = part.strip().lower()
            if part:
                tokens.add(part)
    return tokens


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("tab", TABS)
def test_anonymous_tab_page_carries_the_site_edge_cache_header_and_no_cookie(client, fakes, kind, tab):
    """The after-request hook decides; nothing per route.  Kills: a per-route Cache-Control override (private, no-store,
    a different max-age); a route that touches the session (a Set-Cookie or Vary: Cookie makes the page uncacheable at
    the edge and leaks a cookie to every crawler)."""
    response = client.get(url_of(kind, tab))
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert _tokens(response, "Cache-Control") == EDGE_TOKENS
    assert "cookie" not in _tokens(response, "Vary")
    assert "Set-Cookie" not in response.headers


@pytest.mark.parametrize("kind", KINDS)
def test_network_json_sets_no_cookie(client, fakes, kind):
    """Kills: a JSON route that opens a session."""
    response = client.get(url_of(kind, "network.json"))
    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers


# ---------------------------------------------------------------------------
# The two parent pages carry the same subnav, Overview active (real pages, real seeds)
# ---------------------------------------------------------------------------

@pytest.fixture
def race_parent_client(make_app):
    """The race detail page over the seeds of tests/test_state_races_index.py (race office-1-district-1, cycle 2026;
    race 0, state-senator-senate-district-7, cycle 2024)."""
    app = make_app()
    conn = get_db(app.config["DATABASE_PATH"])
    try:
        _create_race_tables(conn)
        _seed_races(conn)
        conn.commit()
    finally:
        conn.close()
    return app.test_client()


CANDIDATE_PARENT_QUERIES = ["", "period=cycle:2024", "date_from=2025-02-01&date_to=2025-09-30"]


@pytest.mark.parametrize("query", CANDIDATE_PARENT_QUERIES)
def test_candidate_detail_page_carries_the_subnav_with_overview_active(candidate_app, query):
    """/candidates/<id> renders the same Overview + five tab links as the tab pages, Overview marked, every link
    carrying the page's scope, and still exactly one scope bar.  Kills: a subnav only on the tab pages (no way back
    or across); the partial including a second scope bar; Overview not marked; links that drop the scope."""
    page = get(candidate_app.test_client(), f"/candidates/900{'?' + query if query else ''}")
    assert page.markup.count('class="time-scope-bar') == 1

    links = _subnav(page, "/candidates/900")
    assert set(links) == {"overview", *TABS}, f"subnav links present: {sorted(links)}"
    assert any(marked(a) for a in links["overview"])
    for tab in TABS:
        assert not any(marked(a) for a in links[tab])

    expected = window_of(dict(parse_qsl(query)), default="all")
    for name, anchors in links.items():
        assert window_of(link_args(anchors[0])) == expected, anchors[0]["href"]
        assert "election_cycle" not in link_args(anchors[0])


RACE_PARENT_CASES = [
    pytest.param("office-1-district-1", "", "cycle:2026", id="current-cycle-default"),
    pytest.param("office-1-district-1", "period=all", "cycle:2026", id="all-time"),
    pytest.param("state-senator-senate-district-7", "election_cycle=2024&period=cycle:2024", "cycle:2026",
                 id="previous-cycle"),
]


@pytest.mark.parametrize("race_key, query, page_default_key", RACE_PARENT_CASES)
def test_race_detail_page_carries_the_subnav_with_overview_active(race_parent_client, race_key, query,
                                                                  page_default_key):
    """/analytics/state-races/<key> (which already includes the analytics subnav and its scope bar) gains the entity
    subnav under its heading: Overview active, five tab links, the scope and ``election_cycle`` on every link, and
    still one scope bar.  Kills: the entity subnav missing on the race page; a duplicate bar; a link that drops
    ``election_cycle`` (the previous-cycle race would open its tabs on the current cycle)."""
    base = f"/analytics/state-races/{race_key}"
    page = get(race_parent_client, f"{base}{'?' + query if query else ''}")
    assert page.markup.count('class="time-scope-bar') == 1

    links = _subnav(page, base)
    assert set(links) == {"overview", *TABS}, f"subnav links present: {sorted(links)}"
    assert any(marked(a) for a in links["overview"])
    for tab in TABS:
        assert not any(marked(a) for a in links[tab])

    args = dict(parse_qsl(query))
    expected = window_of(args, default=page_default_key)
    for name, anchors in links.items():
        link = link_args(anchors[0])
        assert window_of(link) == expected, anchors[0]["href"]
        if "election_cycle" in args:
            assert link.get("election_cycle") == args["election_cycle"], anchors[0]["href"]
