"""Step C1 -- candidate profile page ``GET /candidates/<isbe candidate id>``.

Plan: .claude/plans/profiles-races-dated-widgets.md section 2.4 and journal rows
J-C1..J-C3. The page lists every committee linked to one ISBE candidate,
most recently active first, so a reader can tell which are still live.

Contract these tests pin (header names are matched case-insensitively):
  * Committees table = the first table whose rows link to ``/committees/<id>``
    (path equality; a query string is allowed). Column headers looked up by
    regex: "status", "last activity", "filing", "raised|receipts",
    "spent|expenditures". "Last activity" cell = ONE ISO date (YYYY-MM-DD) plus
    the kind word receipt | expenditure | filing.
  * Last activity = latest of: non-archived receipt, non-archived expenditure,
    filing received date -- read from ``isbe_condensed_receipts``,
    ``isbe_condensed_expenditures`` and ``isbe_most_recent_filings`` (the
    fixture plants raw-only TRAP rows so reading the raw tables fails).
  * "Recently active" badge text in the row = last activity within 12 months of
    ``data_through`` (the latest non-archived ISBE receipt, which the bar prints
    as "latest ISBE data: ..."). Muted row = any class token containing
    "muted" on/in the row = last activity 4+ years before ``data_through``
    (same anchor; wall-clock anchors are not accepted).
  * Candidacies table = the table with an "outcome" header; heading tagged
    ``scope_tag('All time')``. Money-by-cycle table = the table with a "cycle"
    header that is neither of the above; heading carries a fixed scope tag.
  * Committees raised/spent follow the page window; last activity, ordering,
    candidacies and money-by-cycle do not (J-C3).

Fixture facts (hand-computed; ``today`` is frozen to 2026-10-26 so the
current cycle is 2026 and this file does not rot on 2027-01-01):

  data_through = 2026-03-15 (latest NON-archived receipt anywhere; the
  ARCHIVED 2026-09-01 receipt is excluded, so "latest ISBE data" must read Mar 15, not Sep 1).

  Candidate 900 "Pat Example" -- committees 901 / 902 / 903
    901 Friends of Pat Example  ACTIVE    receipts   2026-03-15 $500 | ARCHIVED 2026-09-01 $9,999 (ignored)
                                          expenditure 2025-11-02 $120
                                          filing      2026-01-20 "Pre-election"
        last activity 2026-03-15 receipt; raised 500 / spent 120 (all time AND cycle 2026)
    902 Pat Example for Senate  INACTIVE  receipts   2021-05-05 $300 | raw-only TRAP 2023-09-09 $44
                                          expenditure 2022-06-30 $80 | ARCHIVED 2024-02-02 $55 (ignored)
                                          filing      none
        last activity 2022-06-30 expenditure; raised 300 / spent 80 all time, 0 / 0 in cycle 2026
    903 Citizens for Example    INACTIVE  no receipts; expenditure raw-only TRAP 2024-08-08 $15
                                          filing      2019-04-10 "Quarterly" | raw-only TRAP
                                                      "Statement of Organization" 2024-11-11
        last activity 2019-04-10 filing; raised 0 / spent 0
    badge: 901 only (threshold 2025-03-15).  muted: 903 only (902 is 3.7 years idle,
    903 is 6.9; threshold 2022-03-15).
    candidacies: 2026 General Primary incumbent won; 2022 General Election challenger lost.
    money by cycle (receipts, non-archived): 2026 -> $500, 2022 (2021-05-05) -> $300.
    all-time raised across committees = $800; current-cycle (2025-2026) = $500.

  Candidate 910 "Quinn Sortwell" -- ordering fixture; ids, names, link order and
  money are all shuffled against the expected order so id / name / insertion /
  receipts-only / raised orderings all differ from it:
    913 Beta Committee for Quinn   receipts 2024-05-05 $20 (inserted first), 2026-02-10 $50   last 2026-02-10 receipt
    911 Gamma Committee for Quinn  receipt 2020-01-01 $9,000; expenditures 2023-03-03 $5 (inserted first),
                                   2025-04-01 $10                              last 2025-04-01 expenditure
    912 Alpha Committee for Quinn  receipt 2025-02-20 $400                    last 2025-02-20 receipt
    914 Delta Committee for Quinn  nothing at all                              last: none (sorts LAST)
    expected order 913, 911, 912, 914; badge (threshold 2025-03-15) on 913 and 911 only.

  Noise from tests.test_isbe_rewire._seed_isbe_tables stays in the database on
  purpose (candidates 1001-1003, committees 100/200/300, their candidacies):
  a query that forgets ``WHERE candidate_id = ?`` shows it.
"""
import html as html_lib
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from database.connection import get_db, init_db
from tests.test_isbe_rewire import (
    _create_candidate_finance_agg_view,
    _create_compat_views,
    _seed_isbe_tables,
    isbe_only_app,  # noqa: F401  (a fixture, requested by name in TestInboundLinks)
)
from webapp.app import create_app


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

class _FrozenDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 26)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    """'This cycle' is clock-relative; pin the resolver's clock to the 2026 cycle."""
    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)


def _seed_candidate_profile_rows(conn) -> None:
    # isbe_candidacies with the ETL's real enum types (scripts/isbe_sunshine_etl.py, ~L180 and ~L376),
    # so a prod-only enum/COALESCE mistake shows up here. _seed_isbe_tables made it with TEXT columns.
    conn.execute("DROP TABLE IF EXISTS isbe_candidacies")
    conn.execute("CREATE TYPE isbe_candidacy_race_type AS ENUM ('incumbent', 'challenger', 'open seat', 'retired')")
    conn.execute("CREATE TYPE isbe_candidacy_outcome AS ENUM ('won', 'lost')")
    conn.execute(
        """
        CREATE TABLE isbe_candidacies (
            id BIGSERIAL PRIMARY KEY,
            candidate_id INTEGER,
            election_type TEXT,
            election_year INTEGER,
            race_type isbe_candidacy_race_type,
            outcome isbe_candidacy_outcome,
            fair_campaign BOOLEAN DEFAULT FALSE,
            limits_off BOOLEAN DEFAULT FALSE,
            limits_off_reason TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_candidacies (candidate_id, election_type, election_year, race_type, outcome)
        VALUES
            -- noise (same rows _seed_isbe_tables makes, with valid enum labels); candidate 1001 has a 2026
            -- outcome that is still pending (NULL), the modal case until the 2026-11-03 election
            (1001, 'General Primary', 2022, 'challenger', 'lost'),
            (1001, 'General Election', 2026, 'incumbent', NULL),
            (1002, 'General Primary', 2024, 'open seat', 'won'),
            (1002, 'General Election', 2024, 'open seat', 'won'),
            -- candidate 900, OLDEST first so a missing ORDER BY shows
            (900, 'General Election', 2022, 'challenger', 'lost'),
            (900, 'General Primary', 2026, 'incumbent', 'won')
        """
    )

    conn.execute(
        """
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party,
                                     city, state, zipcode, residence_county)
        VALUES
            (900, 'Example', 'Pat', 'State Representative', 'Representative District', '45', 'Democratic',
             'Springfield', 'IL', '62701', 'Cook'),
            (910, 'Sortwell', 'Quinn', 'State Senator', 'Senate District', '12', 'Republican',
             'Naperville', 'IL', '60540', 'DuPage')
        """
    )
    # Committee rows inserted in reverse id order (901-903) / shuffled (911-914) so heap order is not the answer.
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, active, city, state, zipcode)
        VALUES
            (903, 'Citizens for Example', 'Candidate', FALSE, 'Springfield', 'IL', '62701'),
            (902, 'Pat Example for Senate', 'Candidate', FALSE, 'Springfield', 'IL', '62701'),
            (901, 'Friends of Pat Example', 'Candidate', TRUE, 'Springfield', 'IL', '62701'),
            (912, 'Alpha Committee for Quinn', 'Candidate', FALSE, 'Naperville', 'IL', '60540'),
            (914, 'Delta Committee for Quinn', 'Candidate', TRUE, 'Naperville', 'IL', '60540'),
            (911, 'Gamma Committee for Quinn', 'Candidate', TRUE, 'Naperville', 'IL', '60540'),
            (913, 'Beta Committee for Quinn', 'Candidate', TRUE, 'Naperville', 'IL', '60540')
        """
    )
    # Link rows in an order that matches neither the expected order nor id order.
    conn.execute(
        """
        INSERT INTO isbe_candidate_committees (committee_id, candidate_id)
        VALUES (902, 900), (903, 900), (901, 900),
               (911, 910), (914, 910), (913, 910), (912, 910)
        """
    )

    # --- raw tables (they feed data_through via the bulk_receipts_clean compat view) ---
    conn.execute(
        """
        INSERT INTO isbe_receipts (id, committee_id, last_name, first_name, received_date, amount, d2_part, archived)
        VALUES
            (90101, 901, 'Donor', 'Dana',   '2026-03-15',  500.0, '1A', FALSE),
            (90102, 901, 'Donor', 'Archie', '2026-09-01', 9999.0, '1A', TRUE),   -- ARCHIVED: counts for nothing
            (90201, 902, 'Donor', 'Dale',   '2021-05-05',  300.0, '1A', FALSE),   -- odd year 2021 -> cycle 2022
            (90202, 902, 'Donor', 'Dora',   '2023-09-09',   44.0, '1A', FALSE),   -- TRAP: raw only (superseded filing)
            (91101, 911, 'Donor', 'Dex',    '2020-01-01', 9000.0, '1A', FALSE),
            (91201, 912, 'Donor', 'Dot',    '2025-02-20',  400.0, '1A', FALSE),
            -- 913: the OLDER receipt is inserted first, so "first matching row" / LIMIT 1 without ORDER BY is wrong
            (91300, 913, 'Donor', 'Dan',    '2024-05-05',   20.0, '1A', FALSE),
            (91301, 913, 'Donor', 'Dee',    '2026-02-10',   50.0, '1A', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_expenditures (id, committee_id, last_name, expended_date, amount, purpose, d2_part, archived)
        VALUES
            (91001, 901, 'Acme Print',       '2025-11-02', 120.0, 'Printing', '2A', FALSE),
            (91002, 902, 'Mailers Inc',      '2022-06-30',  80.0, 'Mailers',  '2A', FALSE),
            (91003, 902, 'Archived Vendor',  '2024-02-02',  55.0, 'Mailers',  '2A', TRUE),    -- ARCHIVED
            (91004, 903, 'Superseded Vendor', '2024-08-08', 15.0, 'Signs',    '2A', FALSE),   -- TRAP: raw only
            -- 911: the OLDER expenditure is inserted first (same LIMIT 1 trap as 913's receipts)
            (91100, 911, 'Old Print Shop',   '2023-03-03',   5.0, 'Printing', '2A', FALSE),
            (91102, 911, 'Print Shop',       '2025-04-01',  10.0, 'Printing', '2A', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_filed_docs (id, committee_id, doc_name, reporting_period_begin, reporting_period_end,
                                     received_datetime)
        VALUES
            (9501, 901, 'Pre-election', '2025-10-01', '2025-12-31', '2026-01-20 10:00:00'),
            (9503, 903, 'Quarterly',    '2019-01-01', '2019-03-31', '2019-04-10 09:30:00'),
            -- TRAP: admin paperwork that isbe_most_recent_filings excludes (and that is not "activity")
            (9513, 903, 'Statement of Organization', NULL, NULL, '2024-11-11 10:00:00')
        """
    )

    # --- stand-ins for the matviews the plan names, with the production column types ---
    # isbe_condensed_* = raw rows minus the TRAP rows (superseded filings); archived rows stay, as in prod.
    conn.execute("CREATE TABLE isbe_condensed_receipts AS SELECT * FROM isbe_receipts WHERE id <> 90202")
    conn.execute(
        "ALTER TABLE isbe_condensed_receipts ALTER COLUMN received_date TYPE DATE USING received_date::date"
    )
    conn.execute("CREATE TABLE isbe_condensed_expenditures AS SELECT * FROM isbe_expenditures WHERE id <> 91004")
    conn.execute(
        "ALTER TABLE isbe_condensed_expenditures ALTER COLUMN expended_date TYPE DATE USING expended_date::date"
    )
    # One row per committee, NULL filing columns when it has no qualifying filing (LEFT JOIN in the real matview).
    conn.execute(
        """
        CREATE TABLE isbe_most_recent_filings (
            end_funds_available DOUBLE PRECISION DEFAULT 0,
            total_investments DOUBLE PRECISION DEFAULT 0,
            total_debts DOUBLE PRECISION DEFAULT 0,
            total_inkind DOUBLE PRECISION DEFAULT 0,
            committee_name TEXT,
            committee_id INTEGER,
            committee_type TEXT,
            committee_active BOOLEAN,
            filed_doc_id INTEGER,
            doc_name TEXT,
            reporting_period_end DATE,
            reporting_period_begin DATE,
            received_datetime TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_most_recent_filings (committee_name, committee_id, committee_type, committee_active,
                                              filed_doc_id, doc_name, reporting_period_end,
                                              reporting_period_begin, received_datetime)
        VALUES
            ('Friends of Pat Example',    901, 'Candidate', TRUE,  9501, 'Pre-election', '2025-12-31', '2025-10-01', '2026-01-20 10:00:00'),
            ('Pat Example for Senate',    902, 'Candidate', FALSE, NULL, NULL,           NULL,         NULL,         NULL),
            ('Citizens for Example',      903, 'Candidate', FALSE, 9503, 'Quarterly',    '2019-03-31', '2019-01-01', '2019-04-10 09:30:00'),
            ('Gamma Committee for Quinn', 911, 'Candidate', TRUE,  NULL, NULL,           NULL,         NULL,         NULL),
            ('Alpha Committee for Quinn', 912, 'Candidate', FALSE, NULL, NULL,           NULL,         NULL,         NULL),
            ('Beta Committee for Quinn',  913, 'Candidate', TRUE,  NULL, NULL,           NULL,         NULL,         NULL),
            ('Delta Committee for Quinn', 914, 'Candidate', TRUE,  NULL, NULL,           NULL,         NULL,         NULL),
            ('Citizens for Smith',        100, 'Candidate', TRUE,  5003, 'D-2 Q2 2025',  '2025-06-30', '2025-04-01', '2025-07-15 00:00:00'),
            ('Friends of Jones',          200, 'Candidate', TRUE,  5002, 'D-2 Q1 2025',  '2025-03-31', '2025-01-01', '2025-04-15 00:00:00'),
            ('PAC United',                300, 'Political Action', TRUE, NULL, NULL,     NULL,         NULL,         NULL)
        """
    )
    conn.commit()


@pytest.fixture
def candidate_app(tmp_path: Path):
    db_path = str(tmp_path / "candidate_profile.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _seed_candidate_profile_rows(conn)
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


# ---------------------------------------------------------------------------
# HTML helpers (stdlib only: bs4 is not in requirements.txt)
# ---------------------------------------------------------------------------

_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONEY = re.compile(r"-?\$[\d,]+(?:\.\d{2})?")
_KIND = re.compile(r"receipt|expenditure|filing", re.I)


class _Row:
    def __init__(self, classes):
        self.cells: list[str] = []
        self.hrefs: list[str] = []
        self.classes: set[str] = set(classes)   # tokens on the <tr> and on everything inside it
        self.has_td = False

    @property
    def text(self) -> str:
        return " ".join(self.cells)


class _Table:
    def __init__(self, heading_html: str, headers: list[str], rows: list[_Row]):
        self.heading_html = heading_html        # inner HTML of the nearest preceding <h2>
        self.headers = headers
        self.rows = rows

    def cell(self, row: _Row, header_pattern: str) -> str:
        assert len(row.cells) == len(self.headers), (self.headers, row.cells)
        for index, header in enumerate(self.headers):
            if re.search(header_pattern, header, re.I):
                return row.cells[index]
        raise AssertionError(f"no column /{header_pattern}/ in headers {self.headers}")


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "tr":
            self._row = _Row(classes)
            self.rows.append(self._row)
        elif self._row is not None:
            self._row.classes.update(classes)
            if tag in ("td", "th"):
                self._cell = []
                self._row.has_td = self._row.has_td or tag == "td"
            elif tag == "a" and attrs.get("href"):
                self._row.hrefs.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.cells.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _tables(page: str) -> list[_Table]:
    headings = [(m.start(), m.group(1)) for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", page, re.S | re.I)]
    found = []
    for match in re.finditer(r"<table\b.*?</table>", page, re.S | re.I):
        parser = _TableParser()
        parser.feed(match.group(0))
        parser.close()
        heading = next((h for pos, h in reversed(headings) if pos < match.start()), "")
        header_rows = [r for r in parser.rows if not r.has_td]
        body_rows = [r for r in parser.rows if r.has_td]
        found.append(_Table(heading, header_rows[0].cells if header_rows else [], body_rows))
    return found


def _committee_ids(row: _Row) -> list[int]:
    ids = []
    for href in row.hrefs:
        match = re.fullmatch(r"/committees/(\d+)", re.split(r"[?#]", href)[0])
        if match:
            ids.append(int(match.group(1)))
    return ids


def _committees_table(page: str) -> _Table:
    for table in _tables(page):
        if any(_committee_ids(row) for row in table.rows):
            return table
    raise AssertionError("no table whose rows link to /committees/<id>")


def _candidacies_table(page: str) -> _Table:
    for table in _tables(page):
        if any(re.search(r"outcome", header, re.I) for header in table.headers):
            return table
    raise AssertionError("no table with an 'outcome' column (candidacies)")


def _cycle_table(page: str) -> _Table:
    for table in _tables(page):
        has_committee_links = any(_committee_ids(row) for row in table.rows)
        has_outcome = any(re.search(r"outcome", header, re.I) for header in table.headers)
        has_cycle = any(re.search(r"cycle", header, re.I) for header in table.headers)
        if has_cycle and not has_committee_links and not has_outcome:
            return table
    raise AssertionError("no money-by-cycle table (a table with a 'cycle' column)")


def _rows_by_committee(table: _Table) -> dict[int, _Row]:
    mapping: dict[int, _Row] = {}
    for row in table.rows:
        ids = _committee_ids(row)
        assert len(ids) == 1, ("each row needs exactly one /committees/<id> link", row.text, row.hrefs)
        assert ids[0] not in mapping, ("committee listed twice", ids[0])
        mapping[ids[0]] = row
    return mapping


def _amount(cell: str) -> float:
    """One dollar figure -> float; a cell with no figure (a dash) counts as zero."""
    figures = _MONEY.findall(cell)
    assert len(figures) <= 1, ("expected at most one dollar figure in the cell", cell)
    if not figures:
        return 0.0
    return float(figures[0].replace("$", "").replace(",", ""))


def _visible_text(page: str) -> str:
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _header_text(page: str) -> str:
    start = page.find("<h1")
    assert start != -1, "no <h1> on the page"
    end = page.find("<h2", start)
    return _visible_text(page[start:end] if end != -1 else page[start:])


def _get(app, url: str, status: int = 200) -> str:
    resp = app.test_client().get(url)
    assert resp.status_code == status, (url, resp.status_code)
    return resp.get_data(as_text=True)


# What the "Last activity" cell must show, per committee of candidate 900: (ISO dates in the cell, kind words).
LAST_ACTIVITY_900 = {
    901: (["2026-03-15"], {"receipt"}),       # beats the 2026-01-20 filing, the 2025-11-02 expenditure, the ARCHIVED 2026-09-01 receipt
    902: (["2022-06-30"], {"expenditure"}),   # beats the 2021-05-05 receipt; ignores ARCHIVED 2024-02-02 and raw-only 2023-09-09
    903: (["2019-04-10"], {"filing"}),        # no money; ignores raw-only 2024-08-08 expenditure and the 2024-11-11 admin doc
}


def _last_activity_900(table: _Table) -> dict:
    result = {}
    for committee_id, row in _rows_by_committee(table).items():
        cell = table.cell(row, r"last\s+activity")
        result[committee_id] = (_ISO.findall(cell), {w.lower() for w in _KIND.findall(cell)})
    return result


# ---------------------------------------------------------------------------
# Candidate 900: header, committees table
# ---------------------------------------------------------------------------

class TestCandidateHeader:
    def test_header_shows_name_office_district_party_county_of_this_candidate_only(self, candidate_app):
        head = _header_text(_get(candidate_app, "/candidates/900"))
        for needle in ("Pat Example", "State Representative", "Representative District", "Democratic", "Cook"):
            assert needle in head, (needle, head)
        assert re.search(r"\b45\b", head), head          # district number
        # Other candidates (seeded noise and the ordering candidate) must not bleed in.
        for stranger in ("John Smith", "Governor", "Sangamon", "Quinn", "Sortwell"):
            assert stranger not in head, (stranger, head)


class TestCommitteesTable:
    def test_lists_exactly_this_candidates_committees_newest_activity_first_with_profile_links(self, candidate_app):
        table = _committees_table(_get(candidate_app, "/candidates/900"))
        links_per_row = [_committee_ids(row) for row in table.rows]
        # One profile link per row, exactly 901, 902, 903 in that order: no 100/200/300 (other candidates),
        # none of 911-914, no duplicates, no filing links mistaken for committee links.
        assert links_per_row == [[901], [902], [903]]
        names = {901: "Friends of Pat Example", 902: "Pat Example for Senate", 903: "Citizens for Example"}
        for committee_id, row in _rows_by_committee(table).items():
            assert names[committee_id] in row.text, (committee_id, row.text)

    def test_last_activity_is_latest_non_archived_receipt_expenditure_or_filing(self, candidate_app):
        table = _committees_table(_get(candidate_app, "/candidates/900"))
        assert _last_activity_900(table) == LAST_ACTIVITY_900

    def test_isbe_status_and_own_last_filing_name_per_committee(self, candidate_app):
        table = _committees_table(_get(candidate_app, "/candidates/900"))
        status = {}
        last_filing = {}
        for committee_id, row in _rows_by_committee(table).items():
            words = re.findall(r"\b(active|inactive)\b", table.cell(row, r"status"), re.I)
            status[committee_id] = words[0].lower() if words else None
            filing_cell = table.cell(row, r"filing")
            shown = [name for name in ("Pre-election", "Quarterly") if name in filing_cell]
            assert len(shown) <= 1, ("another committee's filing name leaked in", committee_id, filing_cell)
            last_filing[committee_id] = shown[0] if shown else None
        assert status == {901: "active", 902: "inactive", 903: "inactive"}
        assert last_filing == {901: "Pre-election", 902: None, 903: "Quarterly"}

    def test_recently_active_badge_within_12_months_and_muted_after_4_idle_years(self, candidate_app):
        # data_through = 2026-03-15: badge cutoff 2025-03-15, muted cutoff 2022-03-15.
        # 902 (last 2022-06-30, 3.7 years idle) is neither; for any anchor between 2026-03-15 and
        # 2026-06-29 that stays true, a wall-clock anchor (4.3 years on 2026-10-26) would mute it.
        table = _committees_table(_get(candidate_app, "/candidates/900"))
        badge = {}
        muted = {}
        for committee_id, row in _rows_by_committee(table).items():
            badge[committee_id] = "recently active" in row.text.lower()
            muted[committee_id] = any("muted" in token.lower() for token in row.classes)
        assert badge == {901: True, 902: False, 903: False}
        assert muted == {901: False, 902: False, 903: True}


# ---------------------------------------------------------------------------
# Candidate 900: candidacies and money by cycle (both fixed, all-time)
# ---------------------------------------------------------------------------

class TestFixedTables:
    def test_candidacies_newest_first_and_tagged_all_time(self, candidate_app):
        table = _candidacies_table(_get(candidate_app, "/candidates/900"))
        rows = [[cell.lower() for cell in row.cells] for row in table.rows]
        assert len(rows) == 2          # not 4+: candidates 1001/1002 have candidacies too
        for needle in ("2026", "general primary", "incumbent", "won"):
            assert needle in rows[0], (needle, rows[0])
        for needle in ("2022", "general election", "challenger", "lost"):
            assert needle in rows[1], (needle, rows[1])
        assert '<span class="scope-tag scope-tag-fixed">All time</span>' in table.heading_html

    def test_money_by_cycle_counts_non_archived_condensed_receipts_per_election_cycle(self, candidate_app):
        table = _cycle_table(_get(candidate_app, "/candidates/900"))
        assert re.search(r'<span class="scope-tag scope-tag-fixed">[^<]+</span>', table.heading_html), table.heading_html
        by_cycle = {}
        for row in table.rows:
            years = re.findall(r"20\d\d", row.cells[0])
            assert years, ("first cell of a cycle row must name the cycle", row.cells)
            by_cycle[int(years[-1])] = _amount(" ".join(row.cells[1:]))
        # 2026: the $500 live receipt only (not the ARCHIVED $9,999, not noise committees' 2025 money, no
        # expenditures). 2022: the 2021-05-05 $300 (odd year -> next cycle), no $80 expenditure.
        # The raw-only $44 (2023, cycle 2024) must not appear. Zero-filled gap rows are fine.
        assert {cycle: amount for cycle, amount in by_cycle.items() if amount} == {2026: 500.0, 2022: 300.0}


# ---------------------------------------------------------------------------
# Time scope
# ---------------------------------------------------------------------------

class TestScope:
    def test_default_is_all_time_with_this_cycle_subtotal_and_isbe_data_through(self, candidate_app):
        page = _get(candidate_app, "/candidates/900")
        assert page.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in page
        assert "period-badge" not in page
        assert "by receipt date" in page
        # data_through excludes the ARCHIVED 2026-09-01 receipt: the bar must say Mar 15, not Sep 1.
        assert "latest ISBE data: Mar 15, 2026" in page
        text = _visible_text(page)
        assert "$800.00" in text                                       # all-time raised across the three committees
        assert re.search(r"This cycle\b[^$]{0,20}\$500\.00", text), text   # current-cycle (2025-2026) subtotal beside it

    @pytest.mark.parametrize("query, expected", [
        ("", {901: (500.0, 120.0), 902: (300.0, 80.0), 903: (0.0, 0.0)}),
        ("?cycle=2026", {901: (500.0, 120.0), 902: (0.0, 0.0), 903: (0.0, 0.0)}),
        ("?period=cycle:2026", {901: (500.0, 120.0), 902: (0.0, 0.0), 903: (0.0, 0.0)}),
    ], ids=["all-time", "cycle-2026-param", "cycle-2026-period"])
    def test_raised_and_spent_follow_the_window_but_rows_order_and_last_activity_do_not(
            self, candidate_app, query, expected):
        page = _get(candidate_app, f"/candidates/900{query}")
        table = _committees_table(page)
        rows = _rows_by_committee(table)
        assert list(rows) == [901, 902, 903]          # still all three, still ordered by last activity
        money = {}
        for committee_id, row in rows.items():
            money[committee_id] = (_amount(table.cell(row, r"raised|receipts")),
                                   _amount(table.cell(row, r"spent|expenditures")))
        assert money == expected                        # 901 raised excludes the ARCHIVED $9,999 in every window
        assert _last_activity_900(table) == LAST_ACTIVITY_900
        if query:
            assert 'data-scope-key="cycle:2026"' in page
            assert "time-scope-reset" in page
            assert '<span class="scope-tag">2026 Cycle</span>' in table.heading_html
        else:
            assert '<span class="scope-tag">All time</span>' in table.heading_html

    @pytest.mark.parametrize("query", ["?cycle=2026", "?period=cycle:2026"])
    def test_fixed_tables_ignore_the_window(self, candidate_app, query):
        page = _get(candidate_app, f"/candidates/900{query}")
        assert len(_candidacies_table(page).rows) == 2
        by_cycle = {}
        for row in _cycle_table(page).rows:
            by_cycle[int(re.findall(r"20\d\d", row.cells[0])[-1])] = _amount(" ".join(row.cells[1:]))
        assert {cycle: amount for cycle, amount in by_cycle.items() if amount} == {2026: 500.0, 2022: 300.0}

    @pytest.mark.parametrize("query", ["?cycle=2026", "?period=cycle:2026"])
    def test_committee_links_keep_a_non_default_scope(self, candidate_app, query):
        table = _committees_table(_get(candidate_app, f"/candidates/900{query}"))
        for committee_id, row in _rows_by_committee(table).items():
            profile_links = [h for h in row.hrefs if re.fullmatch(r"/committees/\d+", re.split(r"[?#]", h)[0])]
            assert len(profile_links) == 1, (committee_id, row.hrefs)
            assert re.search(r"period=cycle(?::|%3A)2026", profile_links[0]), profile_links[0]


# ---------------------------------------------------------------------------
# Candidate 910: ordering and badge boundaries (id, name, link and money order all differ from the answer)
# ---------------------------------------------------------------------------

class TestOrderingFixture:
    def test_order_is_last_activity_desc_nulls_last_not_id_name_link_receipts_or_raised_order(self, candidate_app):
        table = _committees_table(_get(candidate_app, "/candidates/910"))
        rows = _rows_by_committee(table)
        # id asc 911,912,913,914 | name asc 912,913,914,911 | link order 911,914,913,912 | receipts-only
        # 913,912,911,914 | raised desc 911,912,913,914 | NULLs-first desc 914,913,911,912 | date asc 912,911,913,914.
        assert list(rows) == [913, 911, 912, 914]
        dates = {}
        kinds = {}
        for committee_id, row in rows.items():
            cell = table.cell(row, r"last\s+activity")
            dates[committee_id] = _ISO.findall(cell)
            kinds[committee_id] = {w.lower() for w in _KIND.findall(cell)}
        assert dates == {913: ["2026-02-10"], 911: ["2025-04-01"], 912: ["2025-02-20"], 914: []}
        for committee_id, expected_kind in ((913, "receipt"), (911, "expenditure"), (912, "receipt")):
            assert kinds[committee_id] == {expected_kind}, (committee_id, kinds[committee_id])

    def test_badge_is_12_months_of_data_through_across_all_activity_kinds(self, candidate_app):
        # cutoff 2025-03-15: 913 (2026-02-10) yes; 911 yes via its 2025-04-01 EXPENDITURE (its last receipt is
        # 2020); 912 (2025-02-20, 12.8 months) no; 914 (never active) no. Kills receipts-only badges and
        # windows shorter than 11.5 or longer than 12.8 months.
        table = _committees_table(_get(candidate_app, "/candidates/910"))
        badge = {cid: "recently active" in row.text.lower() for cid, row in _rows_by_committee(table).items()}
        assert badge == {913: True, 911: True, 912: False, 914: False}


# ---------------------------------------------------------------------------
# Edge candidates and routing
# ---------------------------------------------------------------------------

class TestEdgesAndRouting:
    def test_candidate_with_no_committees_and_no_party_renders(self, candidate_app):
        page = _get(candidate_app, "/candidates/1003")      # Haroon Atcha: Trustee, NULL party, no committee link
        head = _header_text(page)
        for needle in ("Haroon Atcha", "Trustee", "DuPage"):
            assert needle in head, (needle, head)
        assert "None" not in head
        committee_links = [_committee_ids(row) for table in _tables(page) for row in table.rows]
        assert [ids for ids in committee_links if ids] == []

    def test_pending_outcome_renders_blank_not_none(self, candidate_app):
        page = _get(candidate_app, "/candidates/1001")      # 2026 General Election incumbent, outcome still NULL
        table = _candidacies_table(page)
        assert len(table.rows) == 2
        newest = table.rows[0]
        assert "2026" in newest.cells
        outcome = table.cell(newest, r"outcome").lower()
        assert "none" not in outcome
        assert "null" not in outcome

    def test_unknown_candidate_is_404_while_a_known_one_is_200(self, candidate_app):
        # The 200 comes first on purpose: with no route registered at all every id is a 404.
        assert "Pat Example" in _get(candidate_app, "/candidates/900")
        missing = _get(candidate_app, "/candidates/999999", status=404)
        assert "Pat Example" not in missing

    def test_landing_page_and_profile_route_coexist(self, candidate_app):
        landing = _get(candidate_app, "/candidates")
        assert "Pat Example" not in landing                  # still the landing page, not a profile
        assert "Pat Example" in _get(candidate_app, "/candidates/900")


# ---------------------------------------------------------------------------
# Step C2 -- inbound links to the candidate profile
# ---------------------------------------------------------------------------
# Plan: .claude/plans/profiles-races-dated-widgets.md section 2.4 "Links in" and journal row J-C4.
#
# Contract TestInboundLinks pins (rows are matched by their text, never by column position):
#   * Committee profile, at /committees/<id> AND /committees/sbe/<id> (one template, two routes): every
#     "Linked Candidates" row links the candidate NAME to /candidates/<candidate_id>.
#   * Search: a local (ISBE) row of the Candidates section links to /candidates/<candidate_id>, whether the
#     finance aggregate or the candidates-only fallback found it. A federal (FEC) row never does.
#   * State race detail: every "Candidate Committee Rows" row links to /candidates/<candidate_id>.
#   * Candidate-finance list: every row links to /candidates/<candidate_id> -- also when the itemized
#     drilldown tables are absent -- and the committee-name link to .../itemized survives.
#   * A row with no ISBE candidate id (NULL or blank) is plain text: no anchor of ANY /candidates shape, and
#     the page still renders. (The production views inner-join isbe_candidates, so such a row is a defensive
#     case; the fixture plants it by hand.)
#   * A link is the path /candidates/<digits> exactly, and at least one per row sits outside a collapsed
#     <details> (provenance) panel. The scope travels with it: following the link must land on a profile whose
#     time-scope bar reads the SAME scope as the page the link was on (preset, all time or a custom range). The
#     query encoding (period=, cycle=, date_from/date_to) is the implementer's choice. The profile defaults to
#     all time while search / candidate-finance / race detail default to the current cycle, so a link that
#     carries nothing is wrong on those three even at their own default.
#
# Fixture facts, hand-computed. World = _seed_isbe_tables + _seed_candidate_profile_rows + the compat views +
# _seed_inbound_rows (candidate 910 and committees 903 / 911-914 stay in the database but nothing here reads them):
#   candidates   900 Pat Example   State Representative D45, committees 901 902 903 950
#                920 Pat Exemplar  no committee, no finance-aggregate rows
#                930 Rae Rival     State Representative D45, committees 931 950
#   committee 950 "Joint Fund for Pat and Rae" is linked to 900 AND 930 -> two Linked Candidates rows,
#       Pat Example -> /candidates/900 and Rae Rival -> /candidates/930.
#   finance aggregate (a stand-in TABLE; [period_end_date]):
#       A1  900 Pat Example   / 901 Friends of Pat Example      2026 [2026-03-15]
#       A2  900 Pat Example   / 902 Pat Example for Senate      2022 [2022-06-30]
#       A3  930 Rae Rival     / 931 Rival for Representative    2026 [2026-02-28]
#       A4  NULL Pat Exorphan / 960 Orphan Fund                 2026 [2026-04-01]   <- no ISBE candidate id
#     rows shown:  cycle 2026 -> A1 A3 A4 | all time -> A1 A2 A3 A4 | custom 2026-01-01..2026-06-30 -> A1 A3 A4
#   search "Pat Ex": the Candidates section has exactly 5 rows
#       local    Pat Example   finance aggregate (A1 + A2)  -> /candidates/900
#       local    Pat Exemplar  candidates-only fallback     -> /candidates/920
#       local    Pat Exorphan  finance aggregate (A4)       -> plain text
#       federal  Pat Exley     FEC H0IL99999, matched       -> /federal-finance/H0IL99999
#       federal  Pat Exton     FEC unmatched, NULL FEC id   -> no /candidates link
#   race state-representative-representative-district-45, cycle 2026: the candidacy feed holds
#       900 Pat Example / 901, 900 Pat Example / 902, 930 Rae Rival / 931, NULL Nell Nullid / 941
#       -> "Candidate Committee Rows" has 4 rows, the last with a blank candidate id.

def _seed_inbound_rows(conn) -> None:
    conn.execute(
        """
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party,
                                     city, state, zipcode, residence_county)
        VALUES
            (920, 'Exemplar', 'Pat', 'State Representative', 'Representative District', '45', 'Independent',
             'Springfield', 'IL', '62701', 'Sangamon'),
            (930, 'Rival', 'Rae', 'State Representative', 'Representative District', '45', 'Republican',
             'Springfield', 'IL', '62701', 'Sangamon')
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, active, city, state, zipcode)
        VALUES
            (931, 'Rival for Representative', 'Candidate', TRUE, 'Springfield', 'IL', '62701'),
            (950, 'Joint Fund for Pat and Rae', 'Candidate', TRUE, 'Springfield', 'IL', '62701')
        """
    )
    conn.execute(
        "INSERT INTO isbe_candidate_committees (committee_id, candidate_id) VALUES (931, 930), (950, 900), (950, 930)"
    )

    # The finance aggregate is a TABLE here (a VIEW in production) so one row can carry a NULL candidate_id.
    # Committee 950 has no rows: nothing below reads them.
    conn.execute("DROP VIEW IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT,
            committee_type TEXT,
            committee_party_affiliation TEXT,
            period_year INTEGER,
            election_cycle INTEGER,
            filing_count INTEGER,
            sum_total_receipts DOUBLE PRECISION,
            sum_total_expenditures DOUBLE PRECISION,
            max_ending_funds_available DOUBLE PRECISION,
            archived_filing_count INTEGER,
            period_start_date TEXT,
            period_end_date TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg VALUES
            (900, 'Pat Example', 'State Representative', 'Representative District', '45', 'Democratic',
             901, 'Friends of Pat Example', 'Candidate', 'Democratic',
             2026, 2026, 1, 500.0, 120.0, 380.0, 0, '2026-01-20', '2026-03-15'),
            (900, 'Pat Example', 'State Representative', 'Representative District', '45', 'Democratic',
             902, 'Pat Example for Senate', 'Candidate', 'Democratic',
             2022, 2022, 1, 300.0, 80.0, 220.0, 0, '2021-05-05', '2022-06-30'),
            (930, 'Rae Rival', 'State Representative', 'Representative District', '45', 'Republican',
             931, 'Rival for Representative', 'Candidate', 'Republican',
             2026, 2026, 1, 700.0, 90.0, 610.0, 0, '2026-01-10', '2026-02-28'),
            (NULL, 'Pat Exorphan', 'State Representative', 'Representative District', '45', NULL,
             960, 'Orphan Fund', 'Candidate', NULL,
             2026, 2026, 1, 50.0, 5.0, 45.0, 0, '2026-02-01', '2026-04-01')
        """
    )

    # The candidacy feed the race builder reads (a TABLE here, a VIEW in production), columns as in
    # tests/test_state_race_builder.py. Nell Nullid has a blank candidate id and a committee.
    conn.execute(
        """
        CREATE TABLE isbe_race_candidacies (
            candidacy_id INTEGER,
            candidate_id INTEGER,
            election_year INTEGER,
            election_cycle INTEGER,
            election_type TEXT,
            race_type TEXT,
            outcome TEXT,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_race_candidacies VALUES
            (9101, 900, 2026, 2026, 'General Election', 'incumbent', NULL, 'Pat Example',
             'State Representative', 'Representative District', '45', 'Democratic', 901, 'Friends of Pat Example'),
            (9102, 900, 2026, 2026, 'General Election', 'incumbent', NULL, 'Pat Example',
             'State Representative', 'Representative District', '45', 'Democratic', 902, 'Pat Example for Senate'),
            (9103, 930, 2026, 2026, 'General Election', 'challenger', NULL, 'Rae Rival',
             'State Representative', 'Representative District', '45', 'Republican', 931, 'Rival for Representative'),
            (9104, NULL, 2026, 2026, 'General Election', 'challenger', NULL, 'Nell Nullid',
             'State Representative', 'Representative District', '45', NULL, 941, 'Nullid Fund')
        """
    )

    # Two federal candidates for the search page: one matched to an FEC id, one unmatched (NULL FEC id).
    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (candidate_key, candidate_name, normalized_candidate_name)
        VALUES ('ck-exley', 'Pat Exley', 'pat exley'), ('ck-exton', 'Pat Exton', 'pat exton')
        """
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (seed_candidate_key, candidate_name, fec_candidate_id, match_status,
                                         match_score)
        VALUES ('ck-exley', 'Pat Exley', 'H0IL99999', 'matched', 0.95),
               ('ck-exton', 'Pat Exton', NULL, 'unmatched', NULL)
        """
    )
    conn.commit()


@pytest.fixture
def inbound_app(tmp_path: Path):
    db_path = str(tmp_path / "candidate_inbound.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _seed_candidate_profile_rows(conn)
    _create_compat_views(conn)
    _seed_inbound_rows(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class _RowParser(HTMLParser):
    """Cell texts and (href, text) anchors of one <tr> fragment; ``visible_anchors`` skips <details> panels."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self.visible_anchors: list[tuple[str, str]] = []
        self._cell = None
        self._href = None
        self._anchor_text: list[str] = []
        self._details_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._cell = []
        elif tag == "details":
            self._details_depth += 1
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.cells.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "details":
            self._details_depth -= 1
        elif tag == "a" and self._href is not None:
            anchor = (self._href, " ".join(" ".join(self._anchor_text).split()))
            self.anchors.append(anchor)
            if self._details_depth == 0:
                self.visible_anchors.append(anchor)
            self._href = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._href is not None:
            self._anchor_text.append(data)


class _LinkRow:
    def __init__(self, cells: list[str], anchors: list[tuple[str, str]], visible_anchors: list[tuple[str, str]]):
        self.cells = cells
        self.anchors = anchors
        self.visible_anchors = visible_anchors          # the anchors not inside a collapsed <details> panel

    @property
    def text(self) -> str:
        return " ".join(self.cells)


def _link_rows(page: str, after: str) -> list[_LinkRow]:
    """Body rows of the first table that follows the first match of the regex ``after``."""
    start = re.search(after, page, re.S | re.I)
    assert start, f"no /{after}/ on the page"
    table = re.search(r"<table\b.*?</table>", page[start.end():], re.S | re.I)
    assert table, f"no table after /{after}/"
    rows = []
    for fragment in re.findall(r"<tr\b.*?</tr>", table.group(0), re.S | re.I):
        if "<td" not in fragment.lower():
            continue                                  # header row
        parser = _RowParser()
        parser.feed(fragment)
        parser.close()
        rows.append(_LinkRow(parser.cells, parser.anchors, parser.visible_anchors))
    return rows


def _row_with(rows: list[_LinkRow], needle: str) -> _LinkRow:
    hits = [row for row in rows if needle in row.text]
    assert len(hits) == 1, (needle, len(hits), [row.text for row in rows])
    return hits[0]


def _profile_id(href: str):
    """The candidate id when the link's path is exactly /candidates/<digits>; None for any other path."""
    match = re.fullmatch(r"/candidates/(\d+)", urlsplit(href).path)
    return int(match.group(1)) if match else None


def _candidate_paths(row: _LinkRow) -> list[str]:
    """Every href in the row aimed at the candidates blueprint, valid or not (/candidates/None, /candidates?id=9...)."""
    hrefs = []
    for href, _text in row.anchors:
        path = urlsplit(href).path
        if path == "/candidates" or path.startswith("/candidates/"):
            hrefs.append(href)
    return hrefs


def _bar_scope(page: str) -> tuple[str, str, str]:
    """(scope key, custom date_from, custom date_to) printed by the page's one time-scope bar."""
    assert page.count('class="time-scope-bar') == 1
    bar = re.search(r'<section class="time-scope-bar.*?</section>', page, re.S)
    assert bar, "no time-scope bar"
    key = re.search(r'data-scope-key="([^"]*)"', bar.group(0))
    start = re.search(r'name="date_from" value="([^"]*)"', bar.group(0))
    end = re.search(r'name="date_to" value="([^"]*)"', bar.group(0))
    assert key, "the bar has no data-scope-key"
    return key.group(1), start.group(1) if start else "", end.group(1) if end else ""


def _assert_profile_links(rows: list[_LinkRow], expected: dict[str, int | None]) -> None:
    """Exactly the expected rows; each links only to its own profile (visibly), or (id None) to no /candidates path."""
    assert len(rows) == len(expected), ([row.text for row in rows], sorted(expected))
    for needle, candidate_id in expected.items():
        row = _row_with(rows, needle)
        paths = _candidate_paths(row)
        if candidate_id is None:
            assert paths == [], (needle, "has no ISBE candidate id, so it must be plain text", paths)
        else:
            assert {_profile_id(href) for href in paths} == {candidate_id}, (needle, candidate_id, paths)
            visible = [href for href, _text in row.visible_anchors if _profile_id(href) == candidate_id]
            assert visible, (needle, "the profile link must not live only inside a collapsed <details> panel", paths)


def _assert_links_carry_scope(app, rows: list[_LinkRow], expected_bar: tuple[str, str, str]) -> None:
    """Follow every profile link once: the profile page must show the scope the source page showed."""
    hrefs = sorted({href for row in rows for href, _text in row.anchors if _profile_id(href) is not None})
    assert hrefs, "no candidate-profile link to follow"
    for href in hrefs:
        assert _bar_scope(_get(app, href)) == expected_bar, (href, expected_bar)


# Scope query -> the bar the SOURCE page must show for it (checked, so a page that ignores the query cannot
# make the carry-over check vacuous). Each page is a default for one of them: the profile and committee
# profile default to all time, search / candidate-finance / race detail to the 2026 cycle.
INBOUND_SCOPES = [
    pytest.param("cycle=2026", ("cycle:2026", "", ""), id="cycle-2026"),
    pytest.param("period=all", ("all", "", ""), id="all-time"),
    pytest.param("date_from=2026-01-01&date_to=2026-06-30", ("custom", "2026-01-01", "2026-06-30"),
                 id="custom-range"),
]

RACE_URL = "/analytics/state-races/state-representative-representative-district-45"

# Candidate-finance rows are keyed by committee name; its drilldown is /candidate-finance/<candidate>/<committee>/itemized.
FINANCE_ITEMIZED = {
    "Friends of Pat Example": (900, 901),
    "Pat Example for Senate": (900, 902),
    "Rival for Representative": (930, 931),
}


class TestInboundLinks:
    """Which wrong implementation still passes?  Killed here: no link at all (every test, incl. the NULL and
    federal rows, which are asserted TOGETHER with the linked rows so a link-nothing change cannot satisfy
    them); one candidate's id on every row (two candidates per page); a committee id or the id cell where
    the candidate id belongs (ids 900/920/930 never equal a committee id); a link on only one of search's two
    code paths (agg row 900 and fallback row 920); a link on the first of two rows of one candidate (race
    900/901 and 900/902); a link for the blank / NULL / federal-id rows (BuildError, /candidates/None,
    /candidates/H0IL99999); a guard that tests `is not none` where the race row carries '' ; the scope
    dropped, hard-coded to one cycle, carried only when non-default, or sent as period=custom for a custom
    range; the committee profile fixed on one of its two routes; the profile link nested inside the
    itemized-available branch of the candidate-finance list, or replacing the committee links there; a link
    tucked into the collapsed Provenance panel instead of the row.
    Not pinned (accepted): the link text on search / race / finance rows, pagination, link classes.
    """

    @pytest.mark.parametrize("scope_query, expected_bar", INBOUND_SCOPES)
    def test_committee_profile_linked_candidate_names_link_to_their_own_profiles(
            self, inbound_app, scope_query, expected_bar):
        for path in ("/committees/950", "/committees/sbe/950"):
            page = _get(inbound_app, f"{path}?{scope_query}")
            assert _bar_scope(page) == expected_bar, path
            rows = _link_rows(page, r"<h2>\s*Linked Candidates")
            _assert_profile_links(rows, {"Pat Example": 900, "Rae Rival": 930})
            for name, candidate_id in (("Pat Example", 900), ("Rae Rival", 930)):
                anchor_texts = [text for href, text in _row_with(rows, name).anchors
                                if _profile_id(href) == candidate_id]
                assert name in anchor_texts, (path, "the candidate NAME is the link text", name, anchor_texts)
            _assert_links_carry_scope(inbound_app, rows, expected_bar)

    @pytest.mark.parametrize("scope_query, expected_bar", INBOUND_SCOPES)
    def test_search_candidates_section_links_isbe_rows_only_to_their_own_profiles(
            self, inbound_app, scope_query, expected_bar):
        page = _get(inbound_app, f"/search?q=Pat+Ex&{scope_query}")
        assert _bar_scope(page) == expected_bar
        rows = _link_rows(page, r"<h2>\s*Candidates\s*\(")
        _assert_profile_links(rows, {
            "Pat Example": 900,        # found through the finance aggregate
            "Pat Exemplar": 920,       # found through the candidates-only fallback
            "Pat Exorphan": None,      # aggregate row without a candidate id
            "Pat Exley": None,         # federal, matched
            "Pat Exton": None,         # federal, unmatched: NULL fec_candidate_id
        })
        federal_paths = [urlsplit(href).path for href, _text in _row_with(rows, "Pat Exley").anchors]
        assert "/federal-finance/H0IL99999" in federal_paths           # the matched federal link survives
        _assert_links_carry_scope(inbound_app, rows, expected_bar)

    @pytest.mark.parametrize("scope_query, expected_bar", INBOUND_SCOPES)
    def test_state_race_detail_candidate_rows_link_to_their_own_profiles(
            self, inbound_app, scope_query, expected_bar):
        page = _get(inbound_app, f"{RACE_URL}?election_cycle=2026&{scope_query}")
        assert _bar_scope(page) == expected_bar
        rows = _link_rows(page, r"<h2>\s*Candidate Committee Rows")
        _assert_profile_links(rows, {
            "Friends of Pat Example": 900,
            "Pat Example for Senate": 900,      # the same candidate's second row links too
            "Rival for Representative": 930,
            "Nullid Fund": None,                # blank candidate id ('' in the payload, not None)
        })
        _assert_links_carry_scope(inbound_app, rows, expected_bar)

    @pytest.mark.parametrize("scope_query, expected_bar", INBOUND_SCOPES)
    def test_candidate_finance_list_rows_link_to_profiles_and_keep_their_committee_links(
            self, inbound_app, scope_query, expected_bar):
        page = _get(inbound_app, f"/candidate-finance/?{scope_query}")
        assert _bar_scope(page) == expected_bar
        rows = _link_rows(page, r"<h1>Candidate Committee Finance</h1>")
        expected = {"Friends of Pat Example": 900, "Rival for Representative": 930, "Orphan Fund": None}
        if expected_bar[0] == "all":
            expected["Pat Example for Senate"] = 900          # A2 is a 2022 row: only all time shows it
        _assert_profile_links(rows, expected)
        for committee_name, (candidate_id, committee_id) in FINANCE_ITEMIZED.items():
            if committee_name not in expected:
                continue
            itemized = f"/candidate-finance/{candidate_id}/{committee_id}/itemized"
            committee_link_paths = [urlsplit(href).path for href, text in _row_with(rows, committee_name).anchors
                                    if text == committee_name]
            assert itemized in committee_link_paths, (committee_name, committee_link_paths)
        _assert_links_carry_scope(inbound_app, rows, expected_bar)

    def test_candidate_finance_list_profile_links_do_not_need_the_itemized_tables(self, request):
        # ISBE fallback mode (the isbe_only_app world: no compat views, no aggregate): the rows carry no anchor at
        # all today, so the profile link has to live outside the itemized-available branch.
        fallback_app = request.getfixturevalue("isbe_only_app")
        page = _get(fallback_app, "/candidate-finance/")
        rows = _link_rows(page, r"<h1>Candidate Committee Finance</h1>")
        _assert_profile_links(rows, {"Citizens for Smith": 1001, "Friends of Jones": 1002})
