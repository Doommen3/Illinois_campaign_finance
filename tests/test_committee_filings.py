"""P8 / L11 (tests first): committee filing history -- the profile keeps the 25 newest filings, a new page lists them all.

Plan: .claude/plans/load-times.md, paragraph "P8/L11 contract (committee filing history ...)".
The file starts red: the profile still renders EVERY filing (and puts a filing without a received time FIRST), and
GET /committees/<id>/filings is a 404 because the route does not exist yet.

Contract pinned (texts, URLs and column names exactly as the plan and today's template write them):

  * /committees/<id> and its alias /committees/sbe/<id>: the "Filing History" section lists the 25 newest filings of
    the committee, newest first -- ORDER BY received_datetime DESC NULLS LAST, id DESC LIMIT 25.
  * The section carries ONE line, "Showing 25 of N filings" when N > 25 and "N filings" (no "Showing") when N <= 25, and
    only when N > 25 ONE link whose text is "All N filings" to /committees/<id>/filings (never the /sbe/ alias).
  * GET /committees/<id>/filings: 100 filings per page (?page=, default 1), same order, the profile table's columns
    (Document, Filed, Reporting Period) and its per-row link /committees/filing/<filed_doc_id>; a heading containing
    "Filings" tagged <span class="scope-tag scope-tag-fixed">All time</span> (a page window never filters it); a link
    back to /committees/<id>; 404 for an unknown committee.

Pinned beyond the literal paragraph (each is flagged in the tester report; relax by editing one test):
  * Founding date: the profile derives "Founded:" / "Re-activated:" from the OLDEST Statement of Organization. That
    document is usually the OLDEST filing, i.e. beyond the 25th. test_founding_date_... is a GUARD (passes today).
  * Fetch volume: the profile reads at most 25 isbe_filed_docs rows from the database, a filings page at most 100 (the
    plan's SQL says LIMIT 25; a fetch-everything-then-slice implementation returns the same HTML).
  * A page beyond the last is 200 and empty (the brief says so for page 2 of a 30-filing committee); it is NOT clamped
    to the last page.
  * Odd ?page= values (0, -5, abc, empty) never answer 5xx. A page that has a next page links to it (?page=N+1).

Fixture facts (hand-computed beside the rows that produce them). All committees live in one database, next to the
filings of committees 100 and 200 that tests.test_isbe_rewire._seed_isbe_tables makes, so a missing committee filter or
a global count shows up:
  700 Dover Street PAC   30 filings, FILINGS_700 below (newest first; the ids, the insertion order and the reporting
                         periods all disagree with the received order; rank 30 has NO received time)
  710 Elm Court PAC      3 filings: newest 7102, then 7103, then 7101 which has no received time
  720 Fair Oaks PAC      exactly 25 filings, ids 7201..7225, one per day: the profile shows all 25 and says "25 filings"
  726 Grand Ave PAC      26 filings, ids 7261..7286: the profile shows 7286..7262; 7261 is on the filings page only
  730 Harbor Point PAC   250 filings, ids 73001..73250, groups of tied received times (see _tied_hours)
  740 Iris Lane PAC      no filings at all
  987654                 not a committee

isbe_filed_docs is made with the production column types (received_datetime TIMESTAMP, reporting periods DATE;
scripts/isbe_sunshine_etl.py), where _seed_isbe_tables makes TEXT columns, so the pages are exercised on datetime/date
values as on prod. Committee names never contain the word "Filings" (the heading check looks for it).
"""
from __future__ import annotations

import contextlib
import html as html_lib
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urljoin, urlsplit

import psycopg
import pytest

from database.connection import get_db, init_db
from tests.test_isbe_rewire import (
    _create_candidate_finance_agg_view,
    _create_compat_views,
    _seed_isbe_tables,
)
from webapp.app import create_app

THIRTY, THREE, TWENTY_FIVE, TWENTY_SIX, MANY, NONE_AT_ALL, UNKNOWN = 700, 710, 720, 726, 730, 740, 987654
PROFILE_LIMIT = 25
PAGE_SIZE = 100


class Filing(NamedTuple):
    doc_id: int
    received: str | None   # 'YYYY-MM-DD HH:MM:SS'; None = the filing has no received time
    name: str
    begin: str | None      # reporting period, 'YYYY-MM-DD'; None = no period (Statement of Organization)
    end: str | None


# ---------------------------------------------------------------------------
# Committee 700: 30 filings, newest first.  Rank r (1 = newest) has id 7000 + (7 * r) mod 31 -- a permutation of
# 7001..7030, so id order is neither date order nor its reverse.  Rows are INSERTED in ascending (11 * r) mod 31
# order, which matches neither.  Every received time is distinct.
#   * Ranks 1-8 are in the 2026 cycle window (2025-01-01..2026-12-31), ranks 9-29 are older: a window would show
#     fewer than 30.  Committees 100 and 200 have filings dated 2019-03-01..2025-07-15, so a missing committee
#     filter pulls their filings into the top 25.
#   * LATE filers (ranks 3, 9, 14, 20, and rank 30) report very old periods (2014..2016): ordering by
#     reporting_period_end instead of received_datetime drops them from the top 25.
#   * Ranks 25 and 26 were received on the SAME DAY: rank 25 at 17:30 with the LOWER id 7020, rank 26 at 09:00 with
#     the higher id 7027.  Ordering by the day (not the timestamp) puts 7027 into the profile and pushes 7020 out.
#   * Rank 30 (id 7024) has no received time: it must be LAST (NULLS LAST), where Postgres' DESC default puts it first.
#   * Rank 4 (re-registration) and rank 29 (the founding one) are Statements of Organization with no period.  Rank 29
#     is beyond the 25th, so "Founded: 2015-03-02" can only come from a query over ALL filings.
#   * Document names carry the id ("Report 7007"), so a name never sorts like the dates and a leak is greppable.
# Expected cells (Document | Filed | Reporting Period): the name, received[:10] or "" when there is no received time,
# "begin to end" or an em dash when there is no begin.
# ---------------------------------------------------------------------------
FILINGS_700 = [
    Filing(7007, "2026-07-15 14:05:00", "Report 7007", "2026-04-01", "2026-06-30"),            # 1
    Filing(7014, "2026-04-15 09:40:00", "Report 7014", "2026-01-01", "2026-03-31"),            # 2
    Filing(7021, "2026-02-20 11:25:00", "Report 7021", "2016-07-01", "2016-09-30"),            # 3  late filer
    Filing(7028, "2025-12-11 10:15:00", "Statement of Organization", None, None),              # 4  re-registration
    Filing(7004, "2025-10-15 16:50:00", "Report 7004", "2025-07-01", "2025-09-30"),            # 5
    Filing(7011, "2025-07-15 08:30:00", "Report 7011", "2025-04-01", "2025-06-30"),            # 6
    Filing(7018, "2025-04-15 13:10:00", "Report 7018", "2025-01-01", "2025-03-31"),            # 7
    Filing(7025, "2025-01-15 15:45:00", "Report 7025", "2024-10-01", "2024-12-31"),            # 8
    Filing(7001, "2024-10-15 09:05:00", "Report 7001", "2016-04-01", "2016-06-30"),            # 9  late filer
    Filing(7008, "2024-07-15 12:00:00", "Report 7008", "2024-04-01", "2024-06-30"),            # 10
    Filing(7015, "2024-04-15 10:35:00", "Report 7015", "2024-01-01", "2024-03-31"),            # 11
    Filing(7022, "2024-01-16 14:20:00", "Report 7022", "2023-10-01", "2023-12-31"),            # 12
    Filing(7029, "2023-10-16 09:55:00", "Report 7029", "2023-07-01", "2023-09-30"),            # 13
    Filing(7005, "2023-07-17 11:45:00", "Report 7005", "2016-01-01", "2016-03-31"),            # 14 late filer
    Filing(7012, "2023-04-17 15:15:00", "Report 7012", "2023-01-01", "2023-03-31"),            # 15
    Filing(7019, "2023-01-17 08:10:00", "Report 7019", "2022-10-01", "2022-12-31"),            # 16
    Filing(7026, "2022-10-17 13:30:00", "Report 7026", "2022-07-01", "2022-09-30"),            # 17
    Filing(7002, "2022-07-15 10:20:00", "Report 7002", "2022-04-01", "2022-06-30"),            # 18
    Filing(7009, "2022-04-15 16:00:00", "Report 7009", "2022-01-01", "2022-03-31"),            # 19
    Filing(7016, "2022-01-18 09:15:00", "Report 7016", "2015-10-01", "2015-12-31"),            # 20 late filer
    Filing(7023, "2021-10-15 12:40:00", "Report 7023", "2021-07-01", "2021-09-30"),            # 21
    Filing(7030, "2021-07-15 14:55:00", "Report 7030", "2021-04-01", "2021-06-30"),            # 22
    Filing(7006, "2021-04-15 10:05:00", "Report 7006", "2021-01-01", "2021-03-31"),            # 23
    Filing(7013, "2021-01-15 11:30:00", "Report 7013", "2020-10-01", "2020-12-31"),            # 24
    Filing(7020, "2019-12-20 17:30:00", "Report 7020", "2019-07-01", "2019-09-30"),            # 25 same day as 26, LATER
    Filing(7027, "2019-12-20 09:00:00", "Report 7027", "2019-04-01", "2019-06-30"),            # 26 first one off the profile
    Filing(7003, "2019-04-15 13:20:00", "Report 7003", "2019-01-01", "2019-03-31"),            # 27
    Filing(7010, "2017-06-30 11:20:00", "Report 7010", "2017-01-01", "2017-03-31"),            # 28
    Filing(7017, "2015-03-02 09:00:00", "Statement of Organization", None, None),              # 29 the founding D-1
    Filing(7024, None, "Report 7024", "2014-01-01", "2014-03-31"),                             # 30 NO received time
]
INSERT_ORDER_700 = sorted(
    FILINGS_700, key=lambda filing: (11 * (FILINGS_700.index(filing) + 1)) % 31
)

# Committee 710: newest first.  Inserted as 7103, 7101, 7102: neither id order (7101, 7102, 7103) nor its reverse
# (7103, 7102, 7101) nor insertion order is the expected [7102, 7103, 7101]; Postgres' DESC default would give
# [7101, 7102, 7103].
FILINGS_710 = [
    Filing(7102, "2025-05-20 10:00:00", "Report 7102", "2025-01-01", "2025-03-31"),
    Filing(7103, "2024-11-02 16:45:00", "Pre-election 7103", None, None),
    Filing(7101, None, "Final 7101", "2023-07-01", "2023-09-30"),
]
INSERT_ORDER_710 = [FILINGS_710[1], FILINGS_710[2], FILINGS_710[0]]


def _daily(first_id: int, count: int) -> list[Filing]:
    """count filings with ids first_id.. ascending, one per day at noon from 2023-01-02, no period: id order = date order."""
    filings = []
    for offset in range(count):
        day = date(2023, 1, 2) + timedelta(days=offset)
        filings.append(Filing(first_id + offset, f"{day.isoformat()} 12:00:00", f"Report {first_id + offset}", None, None))
    return filings


def _tied_hours(first_id: int, count: int) -> list[Filing]:
    """count filings in INSERTION order (ascending id).  Rank r (1 = newest = highest id first_id + count - 1) was
    received 2024-03-10 12:00 minus (r + 1) // 4 whole hours, so ranks {1,2}, {3..6}, {7..10}, ... {4k-1..4k+2} tie.
    For 250: the profile cut (ranks 25|26), the page-1|2 cut (100|101) and the page-2|3 cut (200|201) each fall INSIDE a
    group of four ({23..26}, {99..102}, {199..202}); within a group the higher id is the newer filing (the plan's
    'id DESC' tie-break).  Inserted oldest-first, heap order within a group is the OPPOSITE of the expected order."""
    top = datetime(2024, 3, 10, 12, 0, 0)
    filings = []
    for rank in range(count, 0, -1):
        doc_id = first_id + count - rank
        received = (top - timedelta(hours=(rank + 1) // 4)).isoformat(sep=" ")
        filings.append(Filing(doc_id, received, f"Report {doc_id}", None, None))
    return filings


DAILY_720 = _daily(7201, 25)
DAILY_726 = _daily(7261, 26)
TIED_730 = _tied_hours(73001, 250)

NEWEST_700 = [filing.doc_id for filing in FILINGS_700]
NEWEST_710 = [filing.doc_id for filing in FILINGS_710]
NEWEST_726 = [filing.doc_id for filing in reversed(DAILY_726)]   # 7286 .. 7261
NEWEST_730 = [filing.doc_id for filing in reversed(TIED_730)]    # 73250 .. 73001

COMMITTEES = (
    # id, name, creation_date.  700 was re-activated on 2025-12-11 (the date of its rank-4 Statement of Organization).
    (THIRTY, "Dover Street PAC", "2025-12-11"),
    (THREE, "Elm Court PAC", None),
    (TWENTY_FIVE, "Fair Oaks PAC", None),
    (TWENTY_SIX, "Grand Ave PAC", None),
    (MANY, "Harbor Point PAC", None),
    (NONE_AT_ALL, "Iris Lane PAC", None),
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _timestamp(text: str | None):
    return datetime.fromisoformat(text) if text else None


def _day(text: str | None):
    return date.fromisoformat(text) if text else None


def _insert_filings(conn, committee_id: int, filings: list[Filing]) -> None:
    rows = [
        (f.doc_id, committee_id, f.name, _timestamp(f.received), _day(f.begin), _day(f.end))
        for f in filings
    ]
    conn.executemany(
        "INSERT INTO isbe_filed_docs (id, committee_id, doc_name, received_datetime, "
        "reporting_period_begin, reporting_period_end) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def _use_production_column_types(conn) -> None:
    """scripts/isbe_sunshine_etl.py: received_datetime TIMESTAMP, reporting_period_begin/end DATE (the shared seed has TEXT)."""
    conn.execute(
        "ALTER TABLE isbe_filed_docs ALTER COLUMN received_datetime TYPE TIMESTAMP USING received_datetime::timestamp"
    )
    conn.execute(
        "ALTER TABLE isbe_filed_docs ALTER COLUMN reporting_period_begin TYPE DATE USING reporting_period_begin::date"
    )
    conn.execute(
        "ALTER TABLE isbe_filed_docs ALTER COLUMN reporting_period_end TYPE DATE USING reporting_period_end::date"
    )


@pytest.fixture
def filings_app(tmp_path: Path):
    db_path = str(tmp_path / "committee_filings.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _use_production_column_types(conn)
    conn.executemany(
        "INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active, creation_date) "
        "VALUES (?, ?, 'Political Action', 'Peoria', 'IL', '61602', TRUE, ?)",
        COMMITTEES,
    )
    _insert_filings(conn, THIRTY, INSERT_ORDER_700)
    _insert_filings(conn, THREE, INSERT_ORDER_710)
    _insert_filings(conn, TWENTY_FIVE, DAILY_720)
    _insert_filings(conn, TWENTY_SIX, DAILY_726)
    _insert_filings(conn, MANY, TIED_730)
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


# ---------------------------------------------------------------------------
# Reading rendered pages (stdlib only: bs4 is not in requirements.txt)
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]*>")
_SCOPE_TAG = re.compile(r'<span class="scope-tag[^"]*">.*?</span>', re.S)
_ANCHOR = re.compile(r'<a\b[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
_FILING_PATH = re.compile(r"^/committees/filing/(\d+)$")
_SHOWING = re.compile(r"Showing\s+\d+\s+of\s+[\d,]+\s+filings")
FIXED_ALL_TIME = '<span class="scope-tag scope-tag-fixed">All time</span>'


class _Row(NamedTuple):
    cells: list
    hrefs: list


class _Table(NamedTuple):
    headers: list
    rows: list


class _TableParser(HTMLParser):
    """Header cells and body rows (cell texts + link targets) of ONE <table>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headers: list = []
        self.rows: list = []
        self._cells = None
        self._hrefs: list = []
        self._buffer = None
        self._has_td = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells, self._hrefs, self._has_td = [], [], False
        elif tag in ("td", "th") and self._cells is not None:
            self._buffer = []
            self._has_td = self._has_td or tag == "td"
        elif tag == "a" and self._cells is not None:
            href = dict(attrs).get("href")
            if href:
                self._hrefs.append(href)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._buffer is not None and self._cells is not None:
            self._cells.append(" ".join("".join(self._buffer).split()))
            self._buffer = None
        elif tag == "tr" and self._cells is not None:
            if self._has_td:
                self.rows.append(_Row(self._cells, self._hrefs))
            elif not self.headers:
                self.headers = self._cells
            self._cells = None

    def handle_data(self, data):
        if self._buffer is not None:
            self._buffer.append(data)


def _text(fragment: str) -> str:
    """Visible text: tags removed, entities decoded, whitespace collapsed."""
    return " ".join(html_lib.unescape(_TAG.sub(" ", fragment)).split())


def _anchors(fragment: str) -> list:
    """(href, link text) of every link in the fragment; hrefs are entity-decoded."""
    return [(html_lib.unescape(href), _text(inner)) for href, inner in _ANCHOR.findall(fragment)]


def _link_label(label: str) -> str:
    """Link text without a trailing arrow (the house style ends 'See all N donors' links with &rarr;)."""
    return re.sub(r"[\s→»›>]+$", "", label)


def _section(page: str, name_prefix: str) -> str:
    """Markup from the <h2> whose name (scope tag removed) starts with name_prefix to the next <h2> or the end."""
    opens = list(re.finditer(r"<h2\b[^>]*>(.*?)</h2>", page, re.S | re.I))
    for index, found in enumerate(opens):
        if _text(_SCOPE_TAG.sub("", found.group(1))).startswith(name_prefix):
            end = opens[index + 1].start() if index + 1 < len(opens) else len(page)
            return page[found.start():end]
    names = [_text(_SCOPE_TAG.sub("", found.group(1))) for found in opens]
    pytest.fail(f"no <h2> starts with {name_prefix!r}; headings: {names}", pytrace=False)


def _filing_table(page: str) -> _Table:
    """The first table whose rows link to /committees/filing/<id>."""
    for markup in re.findall(r"<table\b.*?</table>", page, re.S | re.I):
        parser = _TableParser()
        parser.feed(markup)
        parser.close()
        for row in parser.rows:
            if any(_FILING_PATH.match(urlsplit(href).path) for href in row.hrefs):
                return _Table(parser.headers, parser.rows)
    pytest.fail("no table whose rows link to /committees/filing/<id>", pytrace=False)


def _filing_ids(page: str) -> list:
    """Filing ids of EVERY /committees/filing/<id> link on the whole page, in page order (hidden rows count)."""
    ids = []
    for href, _label in _anchors(page):
        found = _FILING_PATH.match(urlsplit(href).path)
        if found:
            ids.append(int(found.group(1)))
    return ids


def _numbers_on(page: str) -> set:
    """Every run of digits anywhere in the page source (links, text, attributes, inline JSON)."""
    return set(re.findall(r"\d+", page))


def _linked_pages(page: str, path: str) -> set:
    """Page numbers N of every link on the page to <path>?page=N (relative hrefs resolved against path)."""
    found = set()
    for href, _label in _anchors(page):
        target = urlsplit(urljoin(path, href))
        values = parse_qs(target.query).get("page")
        if target.path == path and values and values[0].isdigit():
            found.add(int(values[0]))
    return found


def _get(client, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status, f"GET {url} answered {response.status_code}, wanted {status}"
    return response.get_data(as_text=True)


def _expected_cells(filing: Filing) -> list:
    filed = ""
    if filing.received:
        filed = filing.received[:10]
    period = "—"
    if filing.begin:
        period = f"{filing.begin} to {filing.end}"
    return [filing.name, filed, period]


@contextlib.contextmanager
def _filed_doc_reads():
    """(statement start, rows returned) of every statement naming isbe_filed_docs while the block runs.

    A class-level psycopg hook like tests/test_db_guards.py: it sees the compat layer and any side connection.
    """
    reads: list = []
    original = psycopg.Cursor.execute

    def recording_execute(self, query, params=None, **kwargs):
        result = original(self, query, params, **kwargs)
        text = query.decode("utf-8", "replace") if isinstance(query, bytes) else str(query)
        if "isbe_filed_docs" in text.lower():
            reads.append((" ".join(text.split())[:110], self.rowcount))
        return result

    psycopg.Cursor.execute = recording_execute
    try:
        yield reads
    finally:
        psycopg.Cursor.execute = original


# ---------------------------------------------------------------------------
# The fixture itself must keep discriminating (pure python, no database)
# ---------------------------------------------------------------------------

class TestFixtureShape:
    def test_committee_700_is_30_distinct_filings_with_the_only_missing_time_last(self):
        assert len(FILINGS_700) == 30
        assert len({f.doc_id for f in FILINGS_700}) == 30
        assert [f.doc_id for f in FILINGS_700] == [7000 + (7 * rank) % 31 for rank in range(1, 31)]
        dated = [f.received for f in FILINGS_700 if f.received]
        assert len(dated) == 29
        assert FILINGS_700[-1].received is None
        assert dated == sorted(set(dated), reverse=True)

    def test_every_lazy_ordering_of_committee_700_picks_a_different_top_25(self):
        expected = NEWEST_700[:PROFILE_LIMIT]
        dated = [f for f in FILINGS_700 if f.received]
        undated = [f for f in FILINGS_700 if not f.received]
        with_period = [f for f in FILINGS_700 if f.end]
        without_period = [f for f in FILINGS_700 if not f.end]
        lazy = {
            "id desc": sorted(FILINGS_700, key=lambda f: f.doc_id, reverse=True),
            "id asc": sorted(FILINGS_700, key=lambda f: f.doc_id),
            "received desc, NULL first (the Postgres default)": undated + sorted(dated, key=lambda f: f.received, reverse=True),
            "received asc": sorted(dated, key=lambda f: f.received) + undated,
            "day only, then id desc": sorted(dated, key=lambda f: (f.received[:10], f.doc_id), reverse=True) + undated,
            "period end desc, NULL last": sorted(with_period, key=lambda f: f.end, reverse=True) + without_period,
            "period end desc, NULL first": without_period + sorted(with_period, key=lambda f: f.end, reverse=True),
            "document name desc": sorted(FILINGS_700, key=lambda f: f.name, reverse=True),
            "insertion order": INSERT_ORDER_700,
        }
        same = [name for name, order in lazy.items() if [f.doc_id for f in order][:PROFILE_LIMIT] == expected]
        assert same == [], f"these orderings would pass for the wrong reason: {same}"

    def test_every_lazy_ordering_of_committee_710_differs_too(self):
        dated = [f for f in FILINGS_710 if f.received]
        undated = [f for f in FILINGS_710 if not f.received]
        lazy = {
            "id desc": sorted(FILINGS_710, key=lambda f: f.doc_id, reverse=True),
            "id asc": sorted(FILINGS_710, key=lambda f: f.doc_id),
            "received desc, NULL first (the Postgres default)": undated + sorted(dated, key=lambda f: f.received, reverse=True),
            "insertion order": INSERT_ORDER_710,
        }
        same = [name for name, order in lazy.items() if [f.doc_id for f in order] == NEWEST_710]
        assert same == [], f"these orderings would pass for the wrong reason: {same}"

    def test_the_founding_statement_of_organization_is_beyond_the_profile(self):
        names = [f.name for f in FILINGS_700]
        first_25 = names[:PROFILE_LIMIT]
        assert first_25.count("Statement of Organization") == 1
        assert names.index("Statement of Organization", PROFILE_LIMIT) == 28

    def test_committee_730_ties_straddle_the_profile_cut_and_both_page_cuts(self):
        by_time = sorted(TIED_730, key=lambda f: f.received, reverse=True)   # stable: ties stay in insertion order
        lazy = [f.doc_id for f in by_time]
        for cut in (PROFILE_LIMIT, PAGE_SIZE, 2 * PAGE_SIZE):
            assert lazy[:cut] != NEWEST_730[:cut], f"no tie group straddles rank {cut}|{cut + 1}"
            assert TIED_730[-cut].received == TIED_730[-cut - 1].received
        assert NEWEST_730[0] == 73250
        assert NEWEST_730[-1] == 73001
        assert NEWEST_730[:PROFILE_LIMIT][-1] == 73226


# ---------------------------------------------------------------------------
# The profile: /committees/<id> and /committees/sbe/<id>
# ---------------------------------------------------------------------------

PROFILE_ROWS = [
    # url, filings the table must list (newest first), filings that must be nowhere in the page source
    pytest.param("/committees/700", NEWEST_700[:25], NEWEST_700[25:], id="30-filings"),
    pytest.param("/committees/sbe/700", NEWEST_700[:25], NEWEST_700[25:], id="30-filings-sbe-alias"),
    pytest.param("/committees/700?cycle=2026", NEWEST_700[:25], NEWEST_700[25:], id="30-filings-cycle-window"),
    pytest.param("/committees/710", NEWEST_710, [], id="3-filings-the-undated-one-last"),
    pytest.param("/committees/730", NEWEST_730[:25], NEWEST_730[25:], id="250-filings-ties-at-the-cut"),
]

OVER_25 = [
    # url, committee id, N
    pytest.param("/committees/700", THIRTY, 30, id="30-filings"),
    pytest.param("/committees/sbe/700", THIRTY, 30, id="30-filings-sbe-alias"),
    pytest.param("/committees/700?cycle=2026", THIRTY, 30, id="30-filings-cycle-window"),
    pytest.param("/committees/726", TWENTY_SIX, 26, id="26-filings-boundary"),
    pytest.param("/committees/730", MANY, 250, id="250-filings"),
]

UP_TO_25 = [
    pytest.param("/committees/710", THREE, 3, id="3-filings"),
    pytest.param("/committees/720", TWENTY_FIVE, 25, id="25-filings-boundary"),
]


class TestProfile:
    @pytest.mark.parametrize("url, shown, absent", PROFILE_ROWS)
    def test_lists_the_newest_filings_up_to_25_newest_first_with_nothing_older_anywhere(self, filings_app, url, shown, absent):
        page = _get(filings_app.test_client(), url)
        ids = _filing_ids(page)                   # every filing link on the page: a hidden row, a second table, a stranger's filing
        assert len(ids) == len(shown), f"{len(ids)} filings linked on the page, wanted {len(shown)}"
        assert ids == shown
        assert len(_filing_table(page).rows) == len(shown)      # no padding rows either
        numbers = _numbers_on(page)
        leaked = sorted(str(doc_id) for doc_id in absent if str(doc_id) in numbers)
        assert leaked == [], f"older filings still present in the page source: {leaked[:10]}"

    @pytest.mark.parametrize("url, committee_id, total", OVER_25)
    def test_over_25_filings_say_showing_25_of_n_and_link_to_all_n(self, filings_app, url, committee_id, total):
        page = _get(filings_app.test_client(), url)
        section = _section(page, "Filing History")
        lines = _SHOWING.findall(_text(section))
        assert lines == [f"Showing 25 of {total} filings"]
        label = f"All {total} filings"
        links = [href for href, text in _anchors(section) if _link_label(text) == label]
        assert len(links) == 1, f"links labelled {label!r}: {links}"
        target = urlsplit(urljoin(urlsplit(url).path, links[0]))
        assert target.path == f"/committees/{committee_id}/filings"
        assert parse_qs(target.query).get("page", ["1"]) == ["1"]       # opens at the first page, not page 2

    @pytest.mark.parametrize("url, committee_id, total", UP_TO_25)
    def test_25_or_fewer_filings_say_n_filings_and_have_no_link(self, filings_app, url, committee_id, total):
        page = _get(filings_app.test_client(), url)
        text = _text(_section(page, "Filing History"))
        assert _SHOWING.findall(text) == []
        assert re.search(rf"(?<![\d,]){total} filings\b", text), f"no '{total} filings' in {text[:160]!r}"
        filings_page = f"/committees/{committee_id}/filings"
        to_filings_page = [
            href for href, _label in _anchors(page) if urlsplit(urljoin(urlsplit(url).path, href)).path == filings_page
        ]
        assert to_filings_page == []
        all_n_links = [label for _href, label in _anchors(page) if re.match(r"All \d+ filings", _link_label(label))]
        assert all_n_links == []

    def test_founding_date_still_comes_from_the_oldest_statement_of_organization(self, filings_app):
        """GUARD (passes today): 'Founded:' is the earliest Statement of Organization of ALL the committee's filings.
        Deriving it from the 25 rows the profile now shows would print 2025-12-11 (rank 4) and no 'Re-activated:'."""
        page = _get(filings_app.test_client(), "/committees/700")
        text = _text(_section(page, "Committee Information"))
        assert "Founded: 2015-03-02" in text
        assert "Re-activated: 2025-12-11" in text


# ---------------------------------------------------------------------------
# /committees/<id>/filings
# ---------------------------------------------------------------------------

FILINGS_PAGE_1 = [
    pytest.param("/committees/700/filings", NEWEST_700, id="30-filings"),
    pytest.param("/committees/710/filings", NEWEST_710, id="3-filings-the-undated-one-last"),
    pytest.param("/committees/726/filings", NEWEST_726, id="26-filings-the-profile-cuts-the-oldest"),
]


class TestFilingsPage:
    @pytest.mark.parametrize("url, expected", FILINGS_PAGE_1)
    def test_page_1_lists_every_filing_newest_first_when_they_fit_on_one_page(self, filings_app, url, expected):
        page = _get(filings_app.test_client(), url)
        ids = _filing_ids(page)
        assert len(ids) == len(expected), f"{len(ids)} filings linked on the page, wanted {len(expected)}"
        assert ids == expected
        assert len(_filing_table(page).rows) == len(expected)

    @pytest.mark.parametrize("committee_id, filings", [
        pytest.param(THIRTY, FILINGS_700, id="30-filings"),
        pytest.param(THREE, FILINGS_710, id="3-filings"),
    ])
    def test_columns_cells_and_row_links_match_the_profile_table(self, filings_app, committee_id, filings):
        table = _filing_table(_get(filings_app.test_client(), f"/committees/{committee_id}/filings"))
        assert table.headers == ["Document", "Filed", "Reporting Period"]
        assert [row.cells for row in table.rows] == [_expected_cells(f) for f in filings]
        assert [row.hrefs for row in table.rows] == [[f"/committees/filing/{f.doc_id}"] for f in filings]

    @pytest.mark.parametrize("committee_id, shared", [
        pytest.param(THIRTY, 25, id="30-filings"),
        pytest.param(THREE, 3, id="3-filings"),
    ])
    def test_rows_are_cell_for_cell_and_link_for_link_the_profile_rows(self, filings_app, committee_id, shared):
        client = filings_app.test_client()
        profile = _filing_table(_get(client, f"/committees/{committee_id}"))
        everything = _filing_table(_get(client, f"/committees/{committee_id}/filings"))
        assert everything.headers == profile.headers
        assert len(profile.rows) == shared
        assert everything.rows[:shared] == profile.rows

    def test_the_26th_newest_is_on_the_filings_page_but_not_on_the_profile(self, filings_app):
        client = filings_app.test_client()
        profile_ids = _filing_ids(_get(client, "/committees/700"))
        filings_ids = _filing_ids(_get(client, "/committees/700/filings"))
        assert NEWEST_700[25] == 7027
        assert 7027 in filings_ids
        assert 7027 not in profile_ids
        assert filings_ids[-1] == 7024            # no received time: last, not first
        assert 7024 not in profile_ids

    def test_pages_beyond_the_last_are_200_and_empty_not_clamped(self, filings_app):
        client = filings_app.test_client()
        problems = []
        for committee_id, page_number in ((THIRTY, 2), (THIRTY, 3), (THIRTY, 999999), (MANY, 4)):
            url = f"/committees/{committee_id}/filings?page={page_number}"
            response = client.get(url)
            if response.status_code != 200:
                problems.append(f"{url}: status {response.status_code}")
                continue
            ids = _filing_ids(response.get_data(as_text=True))
            if ids != []:
                problems.append(f"{url}: {len(ids)} filings listed (first {ids[:3]}), wanted none")
        assert problems == []

    def test_heading_is_filings_with_the_fixed_all_time_tag_whatever_the_window(self, filings_app):
        client = filings_app.test_client()
        problems = []
        for query in ("", "?cycle=2026", "?date_from=2020-01-01&date_to=2020-12-31", "?period=cycle:2024"):
            url = f"/committees/700/filings{query}"
            response = client.get(url)
            if response.status_code != 200:
                problems.append(f"{url}: status {response.status_code}")
                continue
            page = response.get_data(as_text=True)
            headings = [
                inner for _level, inner in re.findall(r"<h([1-3])\b[^>]*>(.*?)</h\1>", page, re.S | re.I)
                if "Filings" in _text(_SCOPE_TAG.sub("", inner))
            ]
            fixed = [inner for inner in headings if FIXED_ALL_TIME in inner]
            if not fixed:
                problems.append(f"{url}: no heading with 'Filings' and {FIXED_ALL_TIME}; found {[_text(h) for h in headings]}")
            ids = _filing_ids(page)
            if ids != NEWEST_700:
                problems.append(f"{url}: the window filtered the list: {len(ids)} filings, wanted all 30")
        assert problems == []

    def test_links_back_to_the_profile(self, filings_app):
        path = "/committees/700/filings"
        page = _get(filings_app.test_client(), path)
        back = [href for href, _label in _anchors(page) if urlsplit(urljoin(path, href)).path == "/committees/700"]
        assert back != [], "no link to /committees/700"

    def test_unknown_committee_is_404_while_a_known_one_is_200(self, filings_app):
        client = filings_app.test_client()
        assert client.get("/committees/700/filings").status_code == 200
        assert client.get(f"/committees/{UNKNOWN}/filings").status_code == 404

    def test_a_committee_without_filings_is_200_with_no_rows_and_its_profile_has_no_link(self, filings_app):
        client = filings_app.test_client()
        filings_ids = _filing_ids(_get(client, f"/committees/{NONE_AT_ALL}/filings"))
        assert filings_ids == []
        profile = _get(client, f"/committees/{NONE_AT_ALL}")
        filings_page = f"/committees/{NONE_AT_ALL}/filings"
        base = f"/committees/{NONE_AT_ALL}"
        links = [href for href, _label in _anchors(profile) if urlsplit(urljoin(base, href)).path == filings_page]
        assert links == []

    def test_odd_page_values_never_answer_5xx(self, filings_app):
        client = filings_app.test_client()
        assert client.get("/committees/700/filings").status_code == 200      # the page exists (404 would pass < 500)
        problems = []
        for value in ("0", "-5", "abc", ""):
            url = f"/committees/700/filings?page={value}"
            status = client.get(url).status_code
            if status >= 500:
                problems.append(f"{url}: status {status}")
        assert problems == []

    def test_a_page_with_a_next_page_links_to_it_and_the_last_page_links_nowhere_beyond(self, filings_app):
        client = filings_app.test_client()
        problems = []
        # committee, page, the page it must link to (None = no duty), the last page that exists
        for committee_id, page_number, forward, last_page in (
            (MANY, 1, 2, 3), (MANY, 2, 3, 3), (MANY, 3, None, 3), (THIRTY, 1, None, 1),
        ):
            path = f"/committees/{committee_id}/filings"
            page = client.get(f"{path}?page={page_number}")
            if page.status_code != 200:
                problems.append(f"{path}?page={page_number}: status {page.status_code}")
                continue
            linked = _linked_pages(page.get_data(as_text=True), path)
            if forward and forward not in linked:
                problems.append(f"{path}?page={page_number}: no link to page {forward}; links to pages {sorted(linked)}")
            beyond = sorted(number for number in linked if number > last_page)
            if beyond:
                problems.append(f"{path}?page={page_number}: links to pages that do not exist: {beyond}")
        assert problems == []


class TestHundredPerPage:
    @pytest.mark.parametrize("page_number, expected", [
        pytest.param(1, NEWEST_730[:100], id="page-1-ids-73250-to-73151"),
        pytest.param(2, NEWEST_730[100:200], id="page-2-ids-73150-to-73051"),
        pytest.param(3, NEWEST_730[200:], id="page-3-ids-73050-to-73001"),
    ])
    def test_250_filings_fill_pages_of_100_100_50_newest_first_without_overlap_or_gap(self, filings_app, page_number, expected):
        # Ties at the page cuts: ranks 100|101 and 200|201 each share a received time, so only the id tie-break
        # keeps the pages from overlapping or skipping.
        page = _get(filings_app.test_client(), f"/committees/{MANY}/filings?page={page_number}")
        ids = _filing_ids(page)
        assert len(ids) == len(expected)
        assert ids == expected
        assert len(_filing_table(page).rows) == len(expected)


# ---------------------------------------------------------------------------
# Only what is shown is fetched (plan SQL: ... LIMIT 25; 100 per page)
# ---------------------------------------------------------------------------

class TestOnlyWhatIsShownIsFetched:
    def test_the_profile_of_a_250_filing_committee_reads_at_most_25_filings(self, filings_app):
        client = filings_app.test_client()
        with _filed_doc_reads() as reads:
            _get(client, f"/committees/{MANY}")
        assert reads != [], "no statement named isbe_filed_docs: the recorder saw nothing"
        offenders = [(sql, rows) for sql, rows in reads if rows > PROFILE_LIMIT]
        assert offenders == [], f"statements returning more than {PROFILE_LIMIT} rows: {offenders}"

    def test_a_filings_page_of_a_250_filing_committee_reads_at_most_100_filings(self, filings_app):
        client = filings_app.test_client()
        problems = []
        for page_number in (1, 3):
            with _filed_doc_reads() as reads:
                _get(client, f"/committees/{MANY}/filings?page={page_number}")
            if not reads:
                problems.append(f"page {page_number}: no statement named isbe_filed_docs")
            for sql, rows in reads:
                if rows > PAGE_SIZE:
                    problems.append(f"page {page_number}: {rows} rows from {sql!r}")
        assert problems == []
