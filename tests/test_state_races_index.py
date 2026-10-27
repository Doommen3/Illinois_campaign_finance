"""Tests-first suite for plan step S-RB: the all-state-races index and the shared race build.

Plan: .claude/plans/profiles-races-dated-widgets.md section 2.2 (design), journal rows
J-R1..J-R4 (section 4) and J-S15 / J-S17 (section 6.3).  S-RA landed first: the builder
reads ``isbe_race_candidacies``, race keys carry no digest, and
``database.analytics.current_election_cycle(today)`` exists.

Pinned contract (each test names the wrong implementation it kills):

* R1 / J-R4 / C5  the candidate cycle follows the page scope on the overview, the index
        and the detail page: a cycle preset drives it, an explicit ``election_cycle`` beats
        the preset, every other scope keeps the current cycle by date and the page labels it
        ("Candidates: 2026 cycle").  Cache keys carry the RESOLVED integer cycle, never None,
        so a payload built before a cycle rollover is not served after it.
* R2 / J-R3       overview and index share ONE cached full-list build.
* R3 / J-R1 / J-R2  ``GET /analytics/state-races/`` lists every race of the resolved cycle;
        a race links to its detail page iff it has windowed receipts or windowed Part-9
        outside spending (D-2 totals and unwindowed money do not count); the overview links
        to the index ("See all N state races"), the detail page links back.
* B5              the prewarm fills the top-12 race details.
* B6 / J-S17      ``refresh_analytics_materialized`` flushes the route caches exactly once,
        after its commit; the ETL ``main()`` flushes after ``create_compat_views``.
* C3 / J-S17      one analytics TTL fallback constant (21600) replaces the 300 / 1800 literals.

Clock: frozen at 2026-10-26 (house technique of tests/test_candidate_profile.py, extended
to the two other modules that call ``date.today()``), so cycle 2026 is "current" and cycle
2024 is "previous" regardless of the wall clock.  ``clock.today`` moves it for the rollover test.

Fixture (hand-computed, committed literals).  One database, two cycles; the race builder
rows are ``(candidate, committee)`` pairs from ``isbe_race_candidacies``:

    race 0   "State Senator - Senate District 7"   cycle 2024   receipt 700 dated 2024-03-01
    races 1..15 "Office n - District n"            cycle 2026   window 2025-01-01..2026-12-31

      n      windowed receipts   windowed Part-9   other money                    class under cycle:2026
      1..9   15000 - 1000n       -                 D-2 total = receipt            active  (14000 .. 6000)
      10     5000                2500              D-2 total 5000                 active  (both)
      11     4000                -                 D-2 total 4000                 active
      12     -                   3000              none                           active  (outside spending only)
      13     -                   -                 D-2 total 9999, no receipts    INACTIVE (D-2 alone is not activity)
      14     -                   -                 4500 dated 2024-06-01          INACTIVE (receipt before the window)
      15     -                   -                 nothing at all                 INACTIVE

    cycle:2026  active = races 1..12 -> "12 of 15";  period=all  active = 1..12 + 14 -> "13 of 15"
    cycle:2024  race 0 only, active -> "1 of 1";  cycle:2024 explicitly under the 2026 window -> "0 of 1"

Orders (``database.analytics._state_race_sort_key``, descending unless stated; races with
equal keys fall back on the label, descending):

    cycle:2026 default (total_amount)  1..11, 12 (zero total but 3000 outside), then 15, 14, 13 (label desc)
    period=all default                 1..10, 14 (4500), 11 (4000), 12, 15, 13
    outside_spending_total asc         13, 14, 15, 11, 9, 8, 7, 6, 5, 4, 3, 2, 1, 10 (2500), 12 (3000)
    outside_spending_total desc        the exact reverse
    outside_pressure_ratio desc        10 (ratio 0.5), 12, 1..9, 11, 15, 14, 13
    total_amount asc                   the exact reverse of the default

The suite needs no product stub: symbols that do not exist yet are looked up with
``getattr`` so a missing symbol is a red assertion naming it, never a collection error.
"""
from __future__ import annotations

import ast
import gc
import inspect
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import database.analytics as analytics_db
import webapp.cache_backend as cache_backend
import webapp.routes.analytics as analytics_routes
from database.analytics import refresh_analytics_materialized
from database.connection import get_db, init_db
from tests.test_analytics_features import analytics_conn  # noqa: F401  (fixture)
from tests.test_isbe_rewire import isbe_app, isbe_direct_app  # noqa: F401  (fixtures)
from tests.test_state_race_builder import (
    _add_agg,
    _add_candidacy,
    _add_part9,
    _add_receipt,
    _create_race_tables,
)
from webapp.app import create_app
from webapp.cache_backend import RouteCache
from webapp.utils.time_scope import resolve_time_scope

REPO = Path(__file__).resolve().parents[1]
INDEX = "/analytics/state-races/"

START = date(2026, 10, 26)
CURRENT_CYCLE = 2026
PRIOR_CYCLE = 2024


# ---------------------------------------------------------------------------
# Clock, caches, builder counter
# ---------------------------------------------------------------------------

class _Clock:
    today = START


CLOCK = _Clock()


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return CLOCK.today


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Freeze ``date.today()`` in the three modules that resolve a cycle from the clock."""
    CLOCK.today = START
    for module in ("webapp.utils.time_scope", "database.analytics", "webapp.routes.analytics"):
        monkeypatch.setattr(f"{module}.date", _FrozenDate, raising=False)
    yield CLOCK
    CLOCK.today = START


def _empty_route_caches() -> None:
    # gc, not a module walk: the shared full-list cache may live in any module.
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()


@pytest.fixture(autouse=True)
def route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process route cache empty
    around each test, so a cached test cannot leak into the next one."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _empty_route_caches()
    yield
    _empty_route_caches()


@pytest.fixture
def builder_calls(monkeypatch):
    """Every call of the race builder, as its ``race_filter`` (None = the full list)."""
    calls: list = []
    real = analytics_db._build_state_race_analytics_rows

    def counting(*args, **kwargs):
        calls.append(kwargs.get("race_filter"))
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics_db, "_build_state_race_analytics_rows", counting)
    return calls


# ---------------------------------------------------------------------------
# Fixture: two cycles, 16 races (see the module docstring for the table)
# ---------------------------------------------------------------------------

PRIOR_RACE = ("State Senator", "Senate District", "7")
LABELS = {0: "State Senator - Senate District 7"}
KEYS = {0: "state-senator-senate-district-7"}
for _n in range(1, 16):
    LABELS[_n] = f"Office {_n} - District {_n}"
    KEYS[_n] = f"office-{_n}-district-{_n}"

CURRENT_RACES = list(range(1, 16))
ACTIVE_UNDER_CYCLE_2026 = list(range(1, 13))
ACTIVE_UNDER_ALL_TIME = list(range(1, 13)) + [14]

CYCLE_2026_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 14, 13]
ALL_TIME_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 11, 12, 15, 13]
TOTAL_ASC = [13, 14, 15, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
OUTSIDE_ASC = [13, 14, 15, 11, 9, 8, 7, 6, 5, 4, 3, 2, 1, 10, 12]
OUTSIDE_DESC = [12, 10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 15, 14, 13]
RATIO_DESC = [10, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 15, 14, 13]

# What the default (cycle:2026) overview shows: its twelve highest rows.
OVERVIEW_TOP_TWELVE = list(range(1, 13))
SCOPE_ALL_OVERVIEW_ROWS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 11]


def _race(number: int) -> tuple[str, str, str]:
    return (f"Office {number}", "District", str(number))


def _seed_races(conn) -> None:
    _add_candidacy(conn, 9000, 100, "Ann Prior", PRIOR_RACE, 400, year=PRIOR_CYCLE)
    _add_agg(conn, 100, "Ann Prior", PRIOR_RACE, 400, 700.0, cycle=PRIOR_CYCLE)
    _add_receipt(conn, 400, "Pat", "Prior", 700.0, date="2024-03-01")

    for number in CURRENT_RACES:
        _add_candidacy(conn, 9000 + number, 200 + number, f"Candidate {number}", _race(number),
                       500 + number, year=CURRENT_CYCLE)

    for number in range(1, 12):  # receipts 14000 .. 4000, dated inside the 2026 window
        amount = 15000.0 - 1000.0 * number
        _add_agg(conn, 200 + number, f"Candidate {number}", _race(number), 500 + number, amount,
                 cycle=CURRENT_CYCLE)
        _add_receipt(conn, 500 + number, "Donor", f"Number{number}", amount, date="2026-03-01")

    _add_part9(conn, "CANDIDATE 10", 2500.0, "2026-04-01")  # race 10: receipts AND outside spending
    _add_part9(conn, "CANDIDATE 12", 3000.0, "2026-05-01")  # race 12: outside spending only
    _add_agg(conn, 213, "Candidate 13", _race(13), 513, 9999.0, cycle=CURRENT_CYCLE)  # D-2 total only
    _add_agg(conn, 214, "Candidate 14", _race(14), 514, 4500.0, cycle=CURRENT_CYCLE)
    _add_receipt(conn, 514, "Donor", "Number14", 4500.0, date="2024-06-01")  # before the window


@pytest.fixture
def race_db_path(tmp_path: Path) -> str:
    db_path = str(tmp_path / "state_races_index.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        _create_race_tables(conn)
        _seed_races(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def make_client(race_db_path):
    def make(**config):
        settings = {"TESTING": True, "DATABASE_PATH": race_db_path, "DASHBOARD_PREWARM_ENABLED": False}
        settings.update(config)
        return create_app(settings).test_client()

    return make


@pytest.fixture
def client(make_client):
    """Route caches off (the TESTING default)."""
    return make_client()


@pytest.fixture
def cached_client(make_client):
    """Route caches on, the way production runs."""
    return make_client(ROUTE_PERF_CACHE_ENABLED=True)


# ---------------------------------------------------------------------------
# Page parsing (stdlib HTMLParser: rows with cells / links / class tokens, anchors, h2 classes)
# ---------------------------------------------------------------------------

class _Page(HTMLParser):
    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self.markup = markup
        self.rows: list[dict] = []
        self.anchors: list[tuple[str, str]] = []
        self.h2_classes: list[set[str]] = []
        self._text: list[str] = []
        self._row: dict | None = None
        self._cell: list[str] | None = None
        self._anchor: dict | None = None
        self._h2: set[str] | None = None
        self._skipping = 0
        self.feed(markup)
        self.close()

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text).split())

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        tokens = set((attributes.get("class") or "").split())
        if tag in ("script", "style"):
            self._skipping += 1
        if tag == "tr":
            self._row = {"cells": [], "links": [], "classes": set(tokens)}
        elif self._row is not None:
            self._row["classes"] |= tokens
        if tag in ("td", "th") and self._row is not None:
            self._cell = []
        if tag == "a":
            self._anchor = {"href": attributes.get("href") or "", "text": []}
        if tag == "h2":
            self._h2 = set(tokens)
        elif self._h2 is not None:
            self._h2 |= tokens

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skipping = max(0, self._skipping - 1)
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        if tag == "a" and self._anchor is not None:
            text = " ".join("".join(self._anchor["text"]).split())
            self.anchors.append((self._anchor["href"], text))
            if self._row is not None:
                self._row["links"].append((self._anchor["href"], text))
            self._anchor = None
        if tag == "tr" and self._row is not None:
            self._row["text"] = " ".join(self._row["cells"])
            self.rows.append(self._row)
            self._row = None
        if tag == "h2" and self._h2 is not None:
            self.h2_classes.append(self._h2)
            self._h2 = None

    def handle_data(self, data):
        if self._skipping:
            return
        self._text.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._anchor is not None:
            self._anchor["text"].append(data)


def _get(client, url: str, status: int = 200) -> _Page:
    response = client.get(url)
    assert response.status_code == status, f"GET {url} -> {response.status_code}, expected {status}"
    return _Page(response.get_data(as_text=True))


_LABEL_PATTERNS = {
    number: re.compile(rf"(?<![\w-]){re.escape(label)}(?![\w-])") for number, label in LABELS.items()
}


def _race_rows(page: _Page) -> list[tuple[int, dict]]:
    """(race number, table row) for every row that names one of the fixture's races, in page order."""
    found = []
    for row in page.rows:
        hits = [number for number, pattern in _LABEL_PATTERNS.items() if pattern.search(row["text"])]
        assert len(hits) <= 1, f"one table row names several races: {row['text']!r}"
        if hits:
            found.append((hits[0], row))
    return found


def _shown(page: _Page) -> list[int]:
    return [number for number, _row in _race_rows(page)]


def _detail_hrefs(row: dict) -> list[str]:
    hrefs = []
    for href, _text in row["links"]:
        path = urlparse(href).path
        if path.startswith("/analytics/state-races/") and path != INDEX:
            hrefs.append(href)
    return hrefs


def _money(row: dict) -> list[str]:
    return re.findall(r"\$[\d,]+\.\d{2}", row["text"])


def _candidate_cycle_claims(page: _Page) -> set[int]:
    return {int(year) for year in re.findall(r"Candidates:\s*(\d{4})\s+cycle", page.text, flags=re.IGNORECASE)}


def _summary(page: _Page) -> tuple[int, int] | None:
    found = re.search(r"\b(\d+) of (\d+) races have activity\b", page.text)
    return (int(found.group(1)), int(found.group(2))) if found else None


def _total_amount_card(page: _Page) -> str | None:
    """The detail page's 'Total Amount' stat card (the windowed figure; the committee tables carry D-2 totals)."""
    found = re.search(r"<h3>\s*Total Amount(?:\s*<span class=\"scope-tag[^\"]*\">[^<]*</span>)?\s*</h3>\s*<div class=\"stat-value\">\s*([^<]*?)\s*</div>", page.markup)
    return found.group(1) if found else None


def _inactive_marker(row: dict) -> bool:
    return any("inactive" in token.lower() or "muted" in token.lower() for token in row["classes"])


# ---------------------------------------------------------------------------
# R1 / J-R4  the candidate cycle follows the scope  (overview + index)
# ---------------------------------------------------------------------------

OVERVIEW = "/analytics/"

COHERENCE_CASES = [
    # (page, query, races shown, cycle the page resolves, must the page say so)
    pytest.param(OVERVIEW, "period=cycle:2024", {0}, 2024, False, id="overview-preset-2024-lists-the-2024-race"),
    pytest.param(OVERVIEW, "period=cycle:2026", set(OVERVIEW_TOP_TWELVE), 2026, False, id="overview-preset-2026"),
    pytest.param(OVERVIEW, "period=all", set(SCOPE_ALL_OVERVIEW_ROWS), 2026, True, id="overview-all-time-labels-2026"),
    pytest.param(OVERVIEW, "period=all&election_cycle=2024", {0}, 2024, True, id="overview-all-time-explicit-2024"),
    pytest.param(OVERVIEW, "period=cycle:2026&election_cycle=2024", {0}, 2024, False,
                 id="overview-explicit-cycle-beats-the-preset"),
    pytest.param(OVERVIEW, "date_from=2023-01-01&date_to=2026-12-31", set(SCOPE_ALL_OVERVIEW_ROWS), 2026, True,
                 id="overview-two-cycle-custom-range-labels-2026"),
    pytest.param(INDEX, "period=cycle:2024", {0}, 2024, False, id="index-preset-2024"),
    pytest.param(INDEX, "period=all", set(CURRENT_RACES), 2026, True, id="index-all-time-labels-2026"),
    pytest.param(INDEX, "period=all&election_cycle=2024", {0}, 2024, True, id="index-all-time-explicit-2024"),
]


@pytest.mark.parametrize("path, query, expected, resolved_cycle, label_required", COHERENCE_CASES)
def test_page_resolves_one_candidate_cycle_from_the_scope(client, path, query, expected, resolved_cycle,
                                                          label_required):
    """A cycle preset drives the candidate set; an explicit ``election_cycle`` beats the preset;
    every non-preset scope keeps the current cycle by date and says which cycle it shows.

    Kills: leaving the MAX/current cycle for every scope (preset 2024 lists the 2026 races);
    letting the scope override an explicit ``election_cycle``; picking the first cycle of a
    two-cycle window (``scope_cycles(...)[0]``); a label that always names the current cycle;
    a label under a non-cycle scope that is missing."""
    page = _get(client, f"{path}?{query}")

    shown = _shown(page)
    assert len(shown) == len(set(shown)), f"a race is listed twice: {shown}"
    assert set(shown) == expected, f"races shown {sorted(shown)}, expected {sorted(expected)}"

    claims = _candidate_cycle_claims(page)
    assert claims <= {resolved_cycle}, f"the page names candidate cycle(s) {sorted(claims)}, resolved {resolved_cycle}"
    if label_required:
        assert claims == {resolved_cycle}, f"no 'Candidates: {resolved_cycle} cycle' label under a non-cycle scope"


DETAIL_CASES = [
    # (race, query, status, the 'Total Amount' stat card)
    pytest.param(0, "period=cycle:2024", 200, "$700.00", id="2024-race-resolves-under-preset-2024"),
    pytest.param(1, "period=cycle:2024", 404, None, id="2026-race-is-not-in-cycle-2024"),
    pytest.param(0, "period=all", 404, None, id="2024-race-is-not-in-the-all-time-current-cycle"),
    pytest.param(1, "period=all", 200, "$14,000.00", id="2026-race-resolves-all-time"),
    pytest.param(0, "period=all&election_cycle=2024", 200, "$700.00", id="explicit-cycle-2024-all-time"),
    pytest.param(0, "period=cycle:2026&election_cycle=2024", 200, "$0.00", id="explicit-cycle-beats-the-preset"),
]


@pytest.mark.parametrize("race, query, status, total_card", DETAIL_CASES)
def test_detail_page_resolves_the_same_cycle_as_the_overview(client, race, query, status, total_card):
    """The detail page uses the overview's cycle rule, so every link on the overview resolves, and
    its windowed 'Total Amount' follows the page window.

    Kills: fixing the overview and not the detail (preset 2024 on the 2024 race stays 404 and
    the 2026 race stays 200); a scope that overrides an explicit ``election_cycle``."""
    url = f"/analytics/state-races/{KEYS[race]}?{query}"
    if status == 404:
        _get(client, url, status=404)
        return
    page = _get(client, url)
    assert LABELS[race] in page.text
    assert _total_amount_card(page) == total_card


# ---------------------------------------------------------------------------
# C5  cache keys carry the RESOLVED integer cycle
# ---------------------------------------------------------------------------

def _cache_key_function():
    helper = getattr(analytics_routes, "_state_races_cache_key", None)
    assert callable(helper), (
        "webapp.routes.analytics._state_races_cache_key(scope, election_cycle, sort_by, sort_dir, today=None) "
        "-> tuple is not defined (plan C5)"
    )
    return helper


def _scope(period: str, today: date = START):
    return resolve_time_scope(default="cycle:current", axis="received", today=today, args={"period": period})


CACHE_KEY_CASES = [
    # (period, election_cycle, today, resolved cycle, cycles that must NOT appear)
    pytest.param("all", None, date(2026, 10, 26), 2026, (2024, 2028), id="all-time-resolves-the-current-cycle"),
    pytest.param("cycle:2024", None, date(2026, 10, 26), 2024, (2026, 2028), id="preset-2024"),
    pytest.param("cycle:2026", None, date(2026, 10, 26), 2026, (2024, 2028), id="preset-2026"),
    pytest.param("all", None, date(2027, 3, 1), 2028, (2024, 2026), id="all-time-moves-with-today"),
    pytest.param("cycle:2024", None, date(2027, 3, 1), 2024, (2026, 2028), id="preset-beats-today"),
    pytest.param("all", 2024, date(2026, 10, 26), 2024, (2026, 2028), id="explicit-cycle"),
    pytest.param("cycle:2026", 2024, date(2026, 10, 26), 2024, (2026, 2028), id="explicit-cycle-beats-the-preset"),
]


@pytest.mark.parametrize("period, election_cycle, today, resolved, absent", CACHE_KEY_CASES)
def test_state_races_cache_key_carries_the_resolved_cycle(period, election_cycle, today, resolved, absent):
    """Kills: the raw ``election_cycle`` (None) in the key, which is what lets a payload built
    before a cycle rollover outlive it; a hard-coded cycle; a key that ignores ``today``."""
    key = _cache_key_function()(_scope(period, today), election_cycle, "total_amount", "desc", today=today)

    assert isinstance(key, tuple)
    assert None not in key
    assert resolved in key, f"resolved cycle {resolved} missing from {key!r}"
    for other in absent:
        assert other not in key, f"cycle {other} must not appear in {key!r}"


def test_state_races_cache_key_separates_everything_that_changes_the_build():
    """Kills: a key that drops the scope, the cycle, the sort field or the direction (a cache
    that serves one sort order, one window or one cycle for another); an unhashable key."""
    helper = _cache_key_function()
    base = helper(_scope("all"), None, "total_amount", "desc", today=START)

    assert hash(base) == hash(helper(_scope("all"), None, "total_amount", "desc", today=START))
    assert base == helper(_scope("all"), None, "total_amount", "desc", today=START)

    assert helper(_scope("cycle:2026"), None, "total_amount", "desc", today=START) != base, "scope"
    assert helper(_scope("all"), 2024, "total_amount", "desc", today=START) != base, "cycle"
    assert helper(_scope("all"), None, "outside_spending_total", "desc", today=START) != base, "sort field"
    assert helper(_scope("all"), None, "total_amount", "asc", today=START) != base, "direction"
    assert helper(_scope("all", date(2027, 3, 1)), None, "total_amount", "desc", today=date(2027, 3, 1)) != base, "today"


ROLLOVER_PAGES = {
    "overview": "/analytics/?period=all",
    "index": "/analytics/state-races/?period=all",
    "detail": f"/analytics/state-races/{KEYS[1]}?period=all",
}


@pytest.mark.parametrize("page_name", sorted(ROLLOVER_PAGES))
def test_cached_payloads_do_not_survive_a_cycle_rollover(page_name, cached_client, clock):
    """``period=all`` has the same window all year, so only the resolved cycle tells a payload
    cached in 2026 from the one the page must build after 2026-12-31 (cycle 2028: no candidacies
    in this fixture).

    Kills: cache keys that hold ``election_cycle=None`` ("MAX cycle"); a detail cache keyed on
    the raw request instead of the resolved cycle; an overview payload that keeps its own
    12-row copy under the old key."""
    url = ROLLOVER_PAGES[page_name]
    before = cached_client.get(url)
    assert before.status_code == 200, f"GET {url} -> {before.status_code}"
    assert LABELS[1] in before.get_data(as_text=True)

    clock.today = date(2027, 3, 1)
    after = cached_client.get(url)

    if page_name == "detail":
        assert after.status_code == 404, "the detail page served a payload cached before the rollover"
    else:
        assert after.status_code == 200
        stale = _shown(_Page(after.get_data(as_text=True)))
        assert stale == [], f"races of cycle 2026 served after the rollover: {stale}"


# ---------------------------------------------------------------------------
# R2 / J-R3  one shared build
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "first, second",
    [pytest.param(OVERVIEW, INDEX, id="overview-then-index"), pytest.param(INDEX, OVERVIEW, id="index-then-overview")],
)
def test_overview_and_index_share_one_full_list_build(first, second, cached_client, builder_calls):
    """Kills: the index building its own list (second cold miss); the overview caching only its
    12-row slice so the index cannot reuse it; either page keying the shared list differently
    (default sort, default scope) so the second page misses."""
    _get(cached_client, first)
    _get(cached_client, second)

    assert len(builder_calls) == 1, f"builder calls (race_filter per call): {builder_calls}"
    assert builder_calls[0] is None, "the single build must be the full list, not a one-race lookup"


@pytest.mark.parametrize(
    "overview_query, index_query",
    [
        pytest.param("period=cycle:2026", "period=cycle:2026", id="default-sort"),
        pytest.param(
            "period=all&state_race_sort=outside_spending_total&state_race_dir=asc",
            "period=all&sort=outside_spending_total&dir=asc",
            id="outside-asc-all-time",
        ),
    ],
)
def test_index_head_is_the_overview_in_order(client, overview_query, index_query):
    """The overview's twelve rows are the first twelve rows of the index: same races, same
    order, same dollar figures.

    Kills: an index that re-sorts differently, uses another window or cycle, or formats other
    numbers; an index that sorts only the 12-row slice."""
    overview = _get(client, f"{OVERVIEW}?{overview_query}")
    index = _get(client, f"{INDEX}?{index_query}")

    overview_rows = _race_rows(overview)
    index_rows = _race_rows(index)
    assert len(overview_rows) == 12
    assert len(index_rows) == 15

    assert [number for number, _row in index_rows[:12]] == [number for number, _row in overview_rows]
    for (number, overview_row), (_same, index_row) in zip(overview_rows, index_rows[:12]):
        assert _money(index_row) == _money(overview_row), f"race {number}: dollar figures differ"


def test_index_is_cached_and_refresh_cache_forces_a_rebuild(cached_client, builder_calls):
    """CLAUDE.md: ``?refresh_cache=1`` forces a miss on every cached analytics page.

    Kills: an index with no cache (second GET rebuilds); an index that ignores
    ``refresh_cache`` (stale for the whole six-hour TTL)."""
    _get(cached_client, INDEX)
    assert len(builder_calls) == 1, "first index request must build once"
    _get(cached_client, INDEX)
    assert len(builder_calls) == 1, "second index request must be served from the cache"
    _get(cached_client, f"{INDEX}?refresh_cache=1")
    assert len(builder_calls) == 2, "refresh_cache=1 must rebuild"


def test_cached_index_does_not_leak_across_scope_cycle_and_sort(cached_client):
    """One process, one cache: seven requests that differ in one cache dimension each must each
    get their own answer.

    Kills: a shared-list key that drops the scope (step 3 repeats step 1's 12 active), the
    cycle (step 2 repeats step 1), the sort field (step 5 repeats step 1) or the direction
    (step 6 repeats step 5)."""
    steps = [
        ("period=cycle:2026", CYCLE_2026_ORDER, (12, 15)),
        ("period=cycle:2024", [0], (1, 1)),
        ("period=all", ALL_TIME_ORDER, (13, 15)),
        ("period=cycle:2026&election_cycle=2024", [0], (0, 1)),
        ("period=cycle:2026&sort=outside_spending_total&dir=asc", OUTSIDE_ASC, (12, 15)),
        ("period=cycle:2026&sort=outside_spending_total&dir=desc", OUTSIDE_DESC, (12, 15)),
        ("period=cycle:2026", CYCLE_2026_ORDER, (12, 15)),
    ]
    wrong = {}
    for query, order, summary in steps:
        page = _get(cached_client, f"{INDEX}?{query}")
        got = (_shown(page), _summary(page))
        if got != (order, summary):
            wrong[query] = {"got": got, "expected": (order, summary)}
    assert wrong == {}


def test_cached_detail_does_not_leak_across_window_and_cycle(cached_client):
    """Race 14's receipt predates the 2026 window; race 0 is a 2024 race asked for under the
    2026 window.  Same race, same process, only the window (or the explicit cycle) changes.

    Kills: a detail cache key that drops the window (the all-time $4,500.00 served to the
    windowed request, or the reverse) or the explicit cycle."""
    steps = [
        (14, "period=cycle:2026", "$0.00"),
        (14, "period=all", "$4,500.00"),
        (14, "period=cycle:2026", "$0.00"),
        (0, "period=all&election_cycle=2024", "$700.00"),
        (0, "period=cycle:2026&election_cycle=2024", "$0.00"),
        (0, "period=all&election_cycle=2024", "$700.00"),
    ]
    wrong = []
    for race, query, expected in steps:
        page = _get(cached_client, f"/analytics/state-races/{KEYS[race]}?{query}")
        if _total_amount_card(page) != expected:
            wrong.append((race, query, _total_amount_card(page), expected))
    assert wrong == [], "(race, query, 'Total Amount' shown, expected)"


# ---------------------------------------------------------------------------
# R3 / J-R1 / J-R2  the index page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected",
    [
        pytest.param("", CURRENT_RACES, id="default-scope-lists-all-15-not-12"),
        pytest.param("period=cycle:2026", CURRENT_RACES, id="preset-2026"),
        pytest.param("period=cycle:2024", [0], id="preset-2024"),
        pytest.param("period=all", CURRENT_RACES, id="all-time-keeps-the-current-cycle"),
    ],
)
def test_index_lists_every_race_of_the_resolved_cycle(client, query, expected):
    """Kills: the overview's 12-row cap; listing only races with activity; listing races of
    another cycle; the same race twice."""
    page = _get(client, f"{INDEX}?{query}")

    assert sorted(_shown(page)) == sorted(expected)


def test_index_is_not_capped_at_a_page_size(race_db_path, make_client):
    """The production 2026 cycle has about 1,465 races ('scroll', no pagination): 110 more
    candidacy-only races push this cycle to 125.

    Kills: a hidden row cap (50 / 100 per page) that a 15-race fixture cannot reach."""
    conn = get_db(race_db_path)
    try:
        for i in range(1, 111):
            _add_candidacy(conn, 9500 + i, 800 + i, f"Filler Person {i}", (f"Filler {i:03d}", "District", str(i)),
                           None, year=CURRENT_CYCLE)
        conn.commit()
    finally:
        conn.close()

    page = _get(make_client(), INDEX)

    fillers = [row for row in page.rows if re.search(r"Filler \d{3} - District", row["text"])]
    assert len(fillers) == 110
    assert sorted(_shown(page)) == sorted(CURRENT_RACES)
    assert _summary(page) == (12, 125)


@pytest.mark.parametrize(
    "query, linked, summary",
    [
        pytest.param("period=cycle:2026", ACTIVE_UNDER_CYCLE_2026, (12, 15), id="cycle-2026"),
        pytest.param("period=all", ACTIVE_UNDER_ALL_TIME, (13, 15), id="all-time"),
        pytest.param("period=cycle:2024", [0], (1, 1), id="cycle-2024"),
    ],
)
def test_index_links_exactly_the_races_with_windowed_activity(client, query, linked, summary):
    """Activity = windowed receipts or windowed Part-9 outside spending.  Race 12 has only
    outside spending (links); race 13 has only a D-2 total (does not); race 14's receipt predates
    the cycle window (links only all-time); race 15 has nothing.

    Kills: activity = total_amount > 0 (race 12 unlinked); activity from D-2 / top-candidate
    share / 'has a committee' (race 13 linked); activity from all-time money under a windowed
    scope (race 14 linked); every row linked; no row linked; a muted class on linked rows."""
    page = _get(client, f"{INDEX}?{query}")
    rows = _race_rows(page)
    everything = [number for number, _row in rows]

    linked_seen = sorted(number for number, row in rows if _detail_hrefs(row))
    unlinked_seen = sorted(number for number, row in rows if not _detail_hrefs(row))
    assert linked_seen == sorted(linked)
    assert unlinked_seen == sorted(set(everything) - set(linked))

    unmarked_inactive = sorted(number for number, row in rows if not _detail_hrefs(row) and not _inactive_marker(row))
    marked_active = sorted(number for number, row in rows if _detail_hrefs(row) and _inactive_marker(row))
    assert unmarked_inactive == [], "unlinked rows need a class token containing 'inactive' or 'muted'"
    assert marked_active == [], "linked rows must not carry an 'inactive' / 'muted' class token"

    assert _summary(page) == summary, "summary line 'N of M races have activity'"


@pytest.mark.parametrize(
    "query, params",
    [
        pytest.param("period=cycle:2026", {"period": ["cycle:2026"]}, id="preset"),
        pytest.param("period=all", {"period": ["all"]}, id="all-time"),
        pytest.param("date_from=2026-01-01&date_to=2026-06-30",
                     {"date_from": ["2026-01-01"], "date_to": ["2026-06-30"]}, id="custom-range"),
        pytest.param("period=all&election_cycle=2024", {"period": ["all"], "election_cycle": ["2024"]},
                     id="explicit-cycle"),
    ],
)
def test_index_links_carry_the_page_scope_and_resolve(client, query, params):
    """Every linked row points at ``/analytics/state-races/<key>`` with the page's scope (and
    an explicit ``election_cycle``), and the link resolves.

    Kills: links without the scope query; dropping ``election_cycle`` (the 2024 race would
    404 from a 2024 index); a preset link that also carries bounds, or a custom link that also
    carries ``period``."""
    page = _get(client, f"{INDEX}?{query}")
    linked = [(number, href) for number, row in _race_rows(page) for href in _detail_hrefs(row)]
    assert linked, "precondition: the index links at least one race"

    for number, href in linked:
        parsed = urlparse(href)
        found = parse_qs(parsed.query)
        assert parsed.path == f"/analytics/state-races/{KEYS[number]}"
        for name, value in params.items():
            assert found.get(name) == value, f"race {number}: {name} in {href}"
        if "period" in params:
            assert "date_from" not in found and "date_to" not in found, f"race {number}: bounds beside period"
        else:
            assert "period" not in found, f"race {number}: period beside bounds"
        detail = _get(client, href)
        assert LABELS[number] in detail.text


SORT_CASES = [
    pytest.param("", CYCLE_2026_ORDER, id="default"),
    pytest.param("sort=total_amount&dir=asc", TOTAL_ASC, id="total-asc"),
    pytest.param("sort=outside_spending_total&dir=asc", OUTSIDE_ASC, id="outside-asc"),
    pytest.param("sort=outside_spending_total&dir=desc", OUTSIDE_DESC, id="outside-desc"),
    pytest.param("sort=outside_spending_total", OUTSIDE_DESC, id="outside-direction-defaults-to-desc"),
    pytest.param("sort=outside_pressure_ratio&dir=desc", RATIO_DESC, id="ratio-desc"),
    pytest.param("sort=bogus&dir=sideways", CYCLE_2026_ORDER, id="invalid-values-fall-back-to-the-default"),
]


@pytest.mark.parametrize("query, expected", SORT_CASES)
def test_index_sort_controls_reorder_every_race(client, query, expected):
    """Same sort fields and fallbacks as the overview (``_STATE_RACE_SORT_FIELDS``; direction
    is ``asc`` only when asked).

    Kills: ignoring ``sort`` or ``dir``; sorting by a different field; sorting only the first
    twelve rows (outside-asc moves 13, 14, 15 to the front); a 500 on a bad value."""
    page = _get(client, f"{INDEX}?{query}")

    assert _shown(page) == expected


OVERVIEW_LINK_CASES = [
    pytest.param(
        "period=all&election_cycle=2026&state_race_sort=outside_spending_total&state_race_dir=asc",
        {"period": ["all"], "election_cycle": ["2026"], "sort": ["outside_spending_total"], "dir": ["asc"]},
        id="scope-cycle-and-sort",
    ),
    pytest.param("period=cycle:2026", {"period": ["cycle:2026"]}, id="preset"),
]


@pytest.mark.parametrize("query, params", OVERVIEW_LINK_CASES)
def test_overview_links_to_the_index_with_the_count_scope_and_sort(client, query, params):
    """"See all 15 state races": the count is the cycle's race total (not the 12 shown, not
    the 12 active), the href is the index with the page scope, ``election_cycle`` and sort
    (``sort`` / ``dir``) when set, and following it reproduces the overview's rows.

    Kills: no link; a count of 12 (cap or active races); a link without the scope or sort."""
    overview = _get(client, f"{OVERVIEW}?{query}")

    hrefs = [href for href, text in overview.anchors if re.search(r"See all 15 state races\b", text)]
    assert hrefs, "no 'See all 15 state races' link on the overview"
    parsed = urlparse(hrefs[0])
    found = parse_qs(parsed.query)
    assert parsed.path == INDEX
    for name, value in params.items():
        assert found.get(name) == value, f"{name} in {hrefs[0]}"

    index = _get(client, hrefs[0])
    assert _shown(index)[:12] == _shown(overview)


@pytest.mark.parametrize(
    "race, query, params",
    [
        pytest.param(1, "period=cycle:2026", {"period": ["cycle:2026"]}, id="preset"),
        pytest.param(0, "period=all&election_cycle=2024", {"period": ["all"], "election_cycle": ["2024"]},
                     id="explicit-cycle"),
    ],
)
def test_detail_page_links_back_to_the_index(client, race, query, params):
    """Kills: no link; a link without the scope; dropping ``election_cycle`` (a 2024 race
    would send the reader to a 2026 index)."""
    detail = _get(client, f"/analytics/state-races/{KEYS[race]}?{query}")

    hrefs = [href for href, text in detail.anchors if re.search(r"All state races", text, flags=re.IGNORECASE)]
    assert hrefs, "no 'All state races' link on the detail page"
    parsed = urlparse(hrefs[0])
    found = parse_qs(parsed.query)
    assert parsed.path == INDEX
    for name, value in params.items():
        assert found.get(name) == value, f"{name} in {hrefs[0]}"

    index = _get(client, hrefs[0])
    assert race in _shown(index)


def test_index_renders_one_scope_bar_and_tags_its_table_windowed(client):
    """Kills: a page without the bar (no subnav include); the bar twice (subnav + a direct
    include); an untagged windowed table (inventory defect E1).  The registry row for the new
    template is the implementer's: tests/test_scope_widgets.py::test_every_windowed_tag_site_is_registered."""
    response = client.get(INDEX)
    assert response.status_code == 200, f"GET {INDEX} -> {response.status_code}"
    markup = response.get_data(as_text=True)

    assert markup.count('class="time-scope-bar') == 1
    windowed_headings = [c for c in _Page(markup).h2_classes if "scope-tag" in c and "scope-tag-fixed" not in c]
    assert windowed_headings, "no <h2> carries the windowed scope_tag()"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

UNAVAILABLE_TEXT = "State race bulk tables are not available yet."


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param("isbe_race_candidacies", id="candidacy-feed"),
        pytest.param("bulk_candidate_committee_finance_agg", id="agg-view"),
        pytest.param("bulk_receipts_clean", id="receipts-view"),
        pytest.param("bulk_expenditures_clean", id="expenditures-view"),
    ],
)
def test_overview_says_unavailable_when_a_race_relation_is_missing(race_db_path, make_client, missing):
    """The builder returns [] when any of its four relations is missing; the section must say
    'not available', not render an empty table.

    Kills: an availability check that does not test ``isbe_race_candidacies`` (the production
    view is created by swap_bulk_to_isbe.py, a deploy step); a check that tests only that."""
    conn = get_db(race_db_path)
    try:
        conn.execute(f"DROP TABLE {missing}")
        conn.commit()
    finally:
        conn.close()

    markup = make_client().get(OVERVIEW).get_data(as_text=True)

    section = re.search(r"<h2>\s*Top State Races.*?(?=<h2>|\Z)", markup, flags=re.DOTALL)
    assert section, "the Top State Races section is gone"
    text = section.group(0)
    assert UNAVAILABLE_TEXT in text
    assert "<table" not in text, "an empty table was rendered instead of the message"
    assert "No state race analytics rows found" not in text


# ---------------------------------------------------------------------------
# B5  the prewarm fills the top-12 race details
# ---------------------------------------------------------------------------

def test_prewarm_makes_the_top_race_details_free(cached_client, builder_calls, race_db_path):
    """Hook: ``warm_analytics_caches(db_target)`` -- the one function webapp/app.py's prewarm
    thread calls, so anything it warms is warmed in production.  After it, each of the overview's
    twelve rows opens without a builder call.

    Kills: no detail prewarm (the first GET builds); warming only the first race; warming
    under a key a default request does not produce (window, cycle, election_cycle=None)."""
    analytics_routes.warm_analytics_caches(race_db_path)
    assert builder_calls, "precondition: the prewarm built nothing, so its caches cannot be judged"
    builder_calls.clear()

    leaks = {}
    for number in OVERVIEW_TOP_TWELVE:
        response = cached_client.get(f"/analytics/state-races/{KEYS[number]}")
        assert response.status_code == 200, f"race {number}: GET -> {response.status_code}"
        assert LABELS[number] in response.get_data(as_text=True)
        if builder_calls:
            leaks[number] = list(builder_calls)
            builder_calls.clear()
    assert leaks == {}, "details that still built after the prewarm: race -> race_filter per builder call"


# ---------------------------------------------------------------------------
# B6 / J-S17  route caches are flushed when the data changes
# ---------------------------------------------------------------------------

@pytest.fixture
def contributions_conn(analytics_conn):  # noqa: F811
    """Legacy source: contributions only (no bulk_committees_clean, so the bulk path is not taken)."""
    return analytics_conn


@pytest.fixture
def bulk_conn(isbe_app):  # noqa: F811
    """bulk_*_clean compat views over the isbe_* tables, no isbe_condensed_receipts."""
    conn = get_db(isbe_app.config["DATABASE_PATH"])
    yield conn
    conn.close()


@pytest.fixture
def isbe_conn(isbe_direct_app):  # noqa: F811
    """isbe_condensed_receipts present: the production path after sunshine-import."""
    conn = get_db(isbe_direct_app.config["DATABASE_PATH"])
    yield conn
    conn.close()


# refresh_analytics_materialized has three branches; (donor-flow source, ISBE direct path) selects each.
REFRESH_BRANCHES = {
    "bulk": ("bulk_receipts", False),
    "isbe": ("bulk_receipts", True),  # the production path after sunshine-import
    "contributions": ("contributions", False),
}


@pytest.mark.parametrize("branch", sorted(REFRESH_BRANCHES))
def test_refresh_flushes_route_caches_exactly_once_after_the_commit(branch, request, monkeypatch, tmp_path):
    """``flush_all_route_caches`` is patched on ``webapp.cache_backend``, so the production
    import must be lazy (inside the function).  A second connection reads the analytics tables
    at the moment of the flush: it sees committed rows only.

    Kills: no flush; a flush only in one of the three refresh branches; a flush per branch plus
    one at the end (twice); a flush before ``commit()`` (a worker could re-cache stale data
    before the refreshed rows are visible); a module-level import of the function; a return
    value that is no longer the refresh statistics."""
    conn = request.getfixturevalue(f"{branch}_conn")
    selected = (analytics_db._donor_flow_source(conn), analytics_db._use_isbe_direct_path(conn))
    assert selected == REFRESH_BRANCHES[branch], f"precondition: the {branch} fixture takes the {selected} branch"
    watcher = get_db(str(tmp_path / "watcher.db"))

    def committed_rows() -> int:
        row = watcher.execute(
            "SELECT (SELECT COUNT(*) FROM analytics_donor_committee_agg)"
            " + (SELECT COUNT(*) FROM analytics_donor_summary) AS n"
        ).fetchone()
        watcher.rollback()  # end the read transaction: no lock held while the refresh runs
        return int(row["n"])

    seen_at_flush: list[int] = []

    def spy() -> int:
        seen_at_flush.append(committed_rows())
        return 0

    rows_before = committed_rows()
    monkeypatch.setattr(cache_backend, "flush_all_route_caches", spy)
    stats = refresh_analytics_materialized(conn)
    rows_after = committed_rows()
    watcher.close()

    assert isinstance(stats, dict), f"refresh_analytics_materialized returned {stats!r}, not its statistics dict"
    assert len(seen_at_flush) == 1, f"flush_all_route_caches called {len(seen_at_flush)} times"
    assert rows_before == 0, "precondition: nothing materialized yet, so the commit order can be observed"
    assert rows_after > 0, "precondition: the refresh materialized rows"
    assert seen_at_flush == [rows_after], "the flush ran before the refreshed rows were committed"


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_lines(tree: ast.AST, name: str) -> list[int]:
    return sorted(node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and _callee(node) == name)


def test_etl_main_flushes_route_caches_after_the_compat_views():
    """Pattern test (main() needs a real ISBE import to run).  Kills: no flush; a flush
    before the matviews / compat views are rebuilt (a request in between re-caches stale
    numbers); a flush in a helper main() never calls.  Accepted hole: a flush on a branch that
    never runs (``if False``) passes."""
    source = (REPO / "scripts" / "isbe_sunshine_etl.py").read_text(encoding="utf-8")
    main = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "main")

    compat = _call_lines(main, "create_compat_views")
    flush = _call_lines(main, "flush_all_route_caches")

    assert compat, "precondition: main() no longer calls create_compat_views"
    assert flush, "main() never calls flush_all_route_caches"
    assert min(flush) > max(compat), f"flush at line(s) {flush} is not after create_compat_views at {compat}"


# ---------------------------------------------------------------------------
# C3 / J-S17  one TTL fallback constant
# ---------------------------------------------------------------------------

ROUTES_SOURCE = (REPO / "webapp" / "routes" / "analytics.py").read_text(encoding="utf-8")
TTL_KEYS = r"ANALYTICS_(?:OVERVIEW|NETWORKS|RELATIONSHIPS|RISK)_CACHE_TTL_SECONDS"


def test_analytics_routes_define_the_ttl_fallback_constant():
    """config.py ships 21600 for these four caches; the code fallbacks were 300 and 1800."""
    value = getattr(analytics_routes, "ANALYTICS_CACHE_TTL_FALLBACK_SECONDS", None)

    assert value == 21600, "ANALYTICS_CACHE_TTL_FALLBACK_SECONDS = 21600 is not defined in webapp/routes/analytics.py"
    assert type(value) is int


def test_analytics_routes_have_no_literal_ttl_fallbacks():
    """Kills: replacing only some of the five 300s; swapping one literal for another (600)
    instead of the constant; leaving the 1800 defaults of ``warm_analytics_caches``."""
    stray = re.findall(r'_CACHE_TTL_SECONDS"\s*,\s*(?:300|1800)\s*\)', ROUTES_SOURCE)
    assert stray == [], f"literal 300 / 1800 TTL fallbacks remain: {len(stray)}"

    lookups = re.findall(rf'\.get\(\s*"({TTL_KEYS})"\s*,\s*([^)]+?)\s*\)', ROUTES_SOURCE)
    not_constant = [key for key, fallback in lookups
                    if fallback.strip().rstrip(",").strip() != "ANALYTICS_CACHE_TTL_FALLBACK_SECONDS"]
    assert not_constant == [], f"TTL lookups that do not fall back to the constant: {not_constant}"

    parameters = inspect.signature(analytics_routes.warm_analytics_caches).parameters
    defaults = {name: parameters[name].default for name in ("networks_ttl", "relationships_ttl", "risk_ttl", "overview_ttl")}
    assert defaults == {name: 21600 for name in defaults}, "warm_analytics_caches defaults are not the constant's value"


def test_app_prewarm_ttl_lookup_uses_the_constant():
    """webapp/app.py's analytics prewarm fell back to 1800 for all four caches (config.py ships
    21600).  Whole-file checks: 1800 appears nowhere else in app.py, so a helper that moves the
    lookups elsewhere still passes.

    Kills: leaving any of the four 1800 fallbacks; replacing them with another literal."""
    source = (REPO / "webapp" / "app.py").read_text(encoding="utf-8")

    calls = _call_lines(ast.parse(source), "warm_analytics_caches")
    assert calls, "precondition: webapp/app.py no longer calls warm_analytics_caches"
    assert not re.search(r"\b1800\b", source), "a 1800 literal remains in webapp/app.py"
    assert "ANALYTICS_CACHE_TTL_FALLBACK_SECONDS" in source or re.search(r"\b21600\b", source), (
        "webapp/app.py names neither ANALYTICS_CACHE_TTL_FALLBACK_SECONDS nor 21600"
    )
