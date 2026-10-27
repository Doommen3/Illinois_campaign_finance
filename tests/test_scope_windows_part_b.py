"""Step S-W6b -- search labels, 527 "Reports Filed" window and Top Federal Races honesty.

Plan .claude/plans/profiles-races-dated-widgets.md section 6.2 row S-W6 (the half that touches /search, the 527 organisation
page and the federal overview), decision J-S14; suggestions E2 (#35, #36), E3 (#38) and E4 (#153, #99) in
docs/architecture_suggestions_2026-10-26.md. Written BEFORE the implementation: every behaviour test below fails today, except
the ones named "guard" (they pin a neighbour the fix must not break). Tests marked FLAGGED pin a consequence the brief does not
spell out; each is a separate test so it can be dropped alone.

What is pinned (headings are matched on reader text; scope tags are ``<span class="scope-tag[ scope-tag-fixed]">``):

  #35 / #36  /search?q=...  The Committees and Donors sections carry only FIXED "All time" tags under every scope. Their ISBE rows
             (isbe_committees, analytics_donor_summary) are identical under every scope (guard), so the tag is true. FLAGGED: the
             legacy fallback rows (committees / donors, windowed by transaction_date today) are all-time too, or the tag lies.
  #38        /search Reports section says "by filing date" under any scope while the page bar keeps "by receipt date"; its heading
             tag stays windowed; guard: the SQL really windows reports.filed_date, so the words are true.
  #153       /527/<ein> Financial Summary "Reports Filed" counts the EIN's irs527_reports whose period_end (TEXT: ISO or compact
             YYYYMMDD) is inside the window; FLAGGED: compact dates are compared as dates (the obvious reuse of
             _text_date_window_clause is wrong for them); its tag stays windowed (guard); Filing History keeps listing every
             report (guard).
  #99        /federal-finance/ Top Federal Races: the first table lists only races with money in the window; a FIXED "<cycle> Cycle"
             tag marks the cycle-granular candidate set; FLAGGED: its Candidates column counts that cycle's candidates only.

Fixtures are topped up in place on the harness's / profile-scope bases; every hand-computed value sits in a comment beside the rows
that produce it. There is no .claude/review-rubric.md in this repo: the style is the dominant one of tests/test_scope_axis_and_windows.py.
"""
from __future__ import annotations

import re

import pytest

from database.connection import get_db

# Importing the harness also imports webapp.routes.analytics at collection time (see the note in tests/test_scope_widgets.py), so
# create_app never imports it lazily inside the first test of this file.
from tests.test_profile_scope import (  # noqa: E402,F401  (donor_app / federal_app / irs527_app: the bases of the fixtures below)
    donor_app,
    federal_app,
    irs527_app,
)
from tests.test_scope_widgets import (  # noqa: E402,F401
    ALL_TIME,
    WINDOW_FEDERAL,
    WINDOW_ISBE,
    _INSERT_RECEIPT,
    _flat,
    _has,
    _topup,
    w_federal_app,
    w_irs527_app,
)

WINDOW_2024 = "date_from=2023-05-01&date_to=2023-12-31"   # inside cycle 2024 (2023-01-01..2024-12-31): only Jane's 2023-06-01 gift
WINDOW_EMPTY = "date_from=2030-01-01&date_to=2030-12-31"  # no fixture row anywhere near it

# ---------------------------------------------------------------------------
# Reader-text helpers (small and local on purpose: they must not care whether an implementer puts a note inside a heading or a
# tag next to it, and this file must not depend on the other S-W6 test file)
# ---------------------------------------------------------------------------
_HEADING = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.S)
_TAG_SPAN = re.compile(r'<span class="(scope-tag[^"]*)">(.*?)</span>', re.S)
_AXIS_SPAN = re.compile(r'<span class="time-scope-axis">(.*?)</span>', re.S)
_TABLE = re.compile(r"<table\b.*?</table>", re.S)


def _get(app, url: str) -> str:
    response = app.test_client().get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response.get_data(as_text=True)


def _axis_labels(doc: str) -> list[str]:
    """Texts of the scope bar's axis label span(s)."""
    return [_flat(text) for text in _AXIS_SPAN.findall(doc)]


def _tags(fragment: str) -> list[tuple[bool, str]]:
    """``(is_fixed, text)`` of every scope tag in ``fragment``, in source order."""
    return [("scope-tag-fixed" in css.split(), _flat(text)) for css, text in _TAG_SPAN.findall(fragment)]


def _section(doc: str, title: str, *, level: str | None = None) -> str | None:
    """HTML from the first heading whose reader text (scope tags removed) is ``title`` or starts with ``title`` + a space, to the next
    heading of the same or a higher level (or the end of ``<main>``); None when there is no such heading."""
    found = [(m.start(), m.group(1), _flat(_TAG_SPAN.sub("", m.group(2)))) for m in _HEADING.finditer(doc)]
    for index, (start, tag, text) in enumerate(found):
        if (level and tag != level) or not (text == title or text.startswith(title + " ")):
            continue
        stop = next((s for s, t, _ in found[index + 1:] if t <= tag), len(doc))
        end_of_content = doc.find("</main>", start)
        return doc[start:min(stop, end_of_content) if end_of_content >= 0 else stop]
    return None


def _need(doc: str, title: str, **kwargs) -> str:
    part = _section(doc, title, **kwargs)
    assert part is not None, f"no heading {title!r} {kwargs} on the page"
    return part


def _first_table(section: str) -> tuple[list[str], list[list[str]]]:
    """``(header texts, raw cell HTML per row)`` of the section's first table, FIRST ``<tbody>`` only. A row whose cell count differs
    from the header count (a colspan "no rows" notice) is skipped."""
    table = _TABLE.search(section)
    assert table, "no <table> in the section"
    headers = [_flat(th) for th in re.findall(r"<th\b[^>]*>(.*?)</th>", table.group(0), re.S)]
    body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", table.group(0), re.S)
    rows: list[list[str]] = []
    if body:
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body.group(1), re.S):
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
            if len(cells) == len(headers):
                rows.append(cells)
    return headers, rows


def _link_text(cell: str) -> str:
    link = re.search(r"<a\b[^>]*>(.*?)</a>", cell, re.S)
    return _flat(link.group(1)) if link else _flat(cell)


def _search_rows(section: str) -> list[tuple[str, ...]]:
    """One tuple per result row of a /search section: ``(link text of the first cell, reader text of every other cell)``."""
    _headers, rows = _first_table(section)
    return [(_link_text(cells[0]), *[_flat(cell) for cell in cells[1:]]) for cells in rows]


# ===========================================================================
# Search (#35, #36, #38): fixture
# ===========================================================================
@pytest.fixture
def search_app(donor_app):
    """donor_app plus two families of rows, each found by its own search term (ids 31, 400+ and 10401+ are free in the base seed).

    "Quillfeather" -- sources with NO activity date (isbe_committees; analytics_donor_summary, rebuilt after the inserts):
      committees 400 Quillfeather Committee (Candidate, Democratic, Chicago IL) and 401 Quillfeather Action Fund (Political Action,
      Peoria IL); receipts (filed doc 5001, Part 1A): Quillfeather Donor 1,234 on 2025-03-03 to 400; Quillfeather Earlier 300 on
      2023-09-09 to 401 and 200 on 2025-03-03 to 400.
      Donor totals: Quillfeather Donor $1,234.00 (nothing inside the cycle-2024 window 2023-01-01..2024-12-31), Quillfeather Earlier
      $500.00 all time ($300.00 if windowed to cycle 2024). Committee 400's only receipts are 2025, committee 401's is 2023, so an
      activity-windowed variant would hide 400 and Quillfeather Donor under cycle 2024 and shrink Earlier to $300.00.
    "Pennyroyal" -- the legacy tables (the fallbacks, which window by contribution transaction_date / report filed_date today):
      committee 31 Pennyroyal Legacy Committee with reports 31 'Quarterly Alpha' (filed 2025-07-01, updated_at 2023-01-01), 32
      'Quarterly Beta' (filed 2023-04-20, updated_at 2025-07-02) and 33 'Quarterly Gamma' (filed 2027-02-10, updated_at 2027-02-11),
      and donor 31 Pennyroyal Legacy Donor with ONE contribution, 1,000 on 2025-06-15 (report 31): nothing inside cycle 2024.
      Each report's updated_at sits on the opposite side of the 2025-06-01..2025-08-31 window from its filed_date.
    """
    from database.analytics import refresh_analytics_materialized

    _topup(
        donor_app,
        ("INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active, party) VALUES "
         "(400, 'Quillfeather Committee', 'Candidate', 'Chicago', 'IL', '60601', TRUE, 'Democratic')", ()),
        ("INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active, party) VALUES "
         "(401, 'Quillfeather Action Fund', 'Political Action', 'Peoria', 'IL', '61602', TRUE, NULL)", ()),
        # _INSERT_RECEIPT parameters: id, committee, filed doc, LAST name, FIRST name, date, amount, address1
        (_INSERT_RECEIPT, (10401, 400, 5001, "Donor", "Quillfeather", "2025-03-03", 1234.0, "4 Quill Ln")),
        (_INSERT_RECEIPT, (10402, 401, 5001, "Earlier", "Quillfeather", "2023-09-09", 300.0, "5 Quill Ln")),
        (_INSERT_RECEIPT, (10403, 400, 5001, "Earlier", "Quillfeather", "2025-03-03", 200.0, "5 Quill Ln")),
    )
    conn = get_db(donor_app.config["DATABASE_PATH"])
    try:
        refresh_analytics_materialized(conn)  # analytics_donor_summary = the unbounded (all-time) donor path
    finally:
        conn.close()
    report = (
        "INSERT INTO reports (id, committee_id, report_type, reporting_period, filed_date, updated_at) "
        "VALUES (?, 31, ?, ?, ?, ?)"
    )
    return _topup(
        donor_app,
        ("INSERT INTO committees (id, name) VALUES (31, 'Pennyroyal Legacy Committee')", ()),
        (report, (31, "Quarterly Alpha", "Q2 2025", "2025-07-01", "2023-01-01")),
        (report, (32, "Quarterly Beta", "Q1 2023", "2023-04-20", "2025-07-02")),
        (report, (33, "Quarterly Gamma", "Q4 2026", "2027-02-10", "2027-02-11")),
        ("INSERT INTO donors (id, name, normalized_name) VALUES (31, 'Pennyroyal Legacy Donor', 'pennyroyal legacy donor')", ()),
        ("INSERT INTO contributions (report_id, donor_id, amount, transaction_date) VALUES (31, 31, 1000.0, '2025-06-15')", ()),
    )


SEARCH_SCOPES = (
    "period=all",
    "period=cycle:2024",                          # 2023-01-01..2024-12-31
    "period=cycle:2026",                          # 2025-01-01..2026-12-31
    "date_from=2025-02-01&date_to=2026-12-31",
)


# ===========================================================================
# #35 / #36 -- Committees and Donors are all-time sources and say so
# ===========================================================================
def test_search_committees_and_donors_carry_only_fixed_all_time_tags(search_app):
    """J-S14: ``_search_committees_source_aware`` reads isbe_committees by name and ``_search_donors_source_aware`` reads
    analytics_donor_summary by name; neither has a date to window, so both headings are tagged windowed today (``scope_tag()``) and
    claim a window they ignore. Every scope tag in either section must be the FIXED "All time" one, under every scope below
    (under ``period=all`` the windowed tag reads "All time" too, so the class decides).

    Kills: today (windowed tags); retagging one of the two sections; a fixed tag only when the scope is not all-time; a fixed tag
    with other text ("ISBE", "Directory", "Fixed"); removing the tag; keeping the windowed tag beside the fixed one."""
    problems = []
    for scope in SEARCH_SCOPES:
        doc = _get(search_app, f"/search?q=Quillfeather&{scope}")
        for title in ("Committees", "Donors"):
            section = _section(doc, title, level="h2")
            if section is None:
                problems.append(f"{scope}: no {title!r} section (fixture lost a row?)")
                continue
            tags = _tags(section)
            if not tags or any(tag != (True, "All time") for tag in tags):
                problems.append(f"{scope}: {title} tags are {tags}, expected only (True, 'All time')")
    assert not problems, "\n" + "\n".join(problems)


QUILLFEATHER_COMMITTEES = [   # (name, type, location); ORDER BY name
    ("Quillfeather Action Fund", "Political Action", "Peoria, IL"),
    ("Quillfeather Committee", "Candidate (Democratic)", "Chicago, IL"),
]
QUILLFEATHER_DONORS = [       # (name, all-time total): 1,234 = one gift; 500 = 300 (2023-09-09) + 200 (2025-03-03)
    ("Quillfeather Donor", "$1,234.00"),
    ("Quillfeather Earlier", "$500.00"),
]


def test_search_isbe_committee_and_donor_rows_do_not_depend_on_the_window(search_app):
    """GUARD (passes today): the rows behind the fixed "All time" tag are the same under every scope, including cycle 2024, where
    committee 400 and Quillfeather Donor have no activity at all and Quillfeather Earlier shows its all-time $500.00, not the
    windowed $300.00. A future change that windows these sections shows up here and has to change the tag with it.

    Kills (once the fix lands): windowing the committee list by receipts, hiding donors with no in-window receipts, showing windowed
    totals, or going empty under a narrow scope while the tag still says "All time"."""
    problems = []
    for scope in SEARCH_SCOPES:
        doc = _get(search_app, f"/search?q=Quillfeather&{scope}")
        committees = sorted(_search_rows(_need(doc, "Committees", level="h2")))
        donors = sorted((row[0], row[2]) for row in _search_rows(_need(doc, "Donors", level="h2")))
        if committees != sorted(QUILLFEATHER_COMMITTEES):
            problems.append(f"{scope}: committee rows {committees}")
        if donors != sorted(QUILLFEATHER_DONORS):
            problems.append(f"{scope}: donor rows {donors}")
    assert not problems, "\n" + "\n".join(problems)


def test_search_legacy_fallback_rows_ignore_the_window_too(search_app):
    """FLAGGED (consequence of the fixed tag, not spelled out in the brief): ``Committee.search`` / ``Donor.search`` receive
    ``transaction_date_from/to`` and keep only legacy committees / donors with a contribution inside the window. A section tagged
    "All time" that silently drops its legacy rows under a window tells the reader the committee does not exist. Pennyroyal Legacy
    Committee and Donor have activity only on 2025-06-15, so under cycle 2024 they are gone today; under any scope both must list
    exactly as under ``period=all`` (donor total = the all-time $1,000.00).

    Kills: retagging without un-windowing the legacy fallbacks (today: no Committees / Donors section under cycle 2024); windowing
    the legacy donor total. Drop this test if the legacy fallbacks are meant to stay windowed (then the tag must say so)."""
    problems = []
    for scope in SEARCH_SCOPES:
        doc = _get(search_app, f"/search?q=Pennyroyal&{scope}")
        committees = _section(doc, "Committees", level="h2")
        donors = _section(doc, "Donors", level="h2")
        if committees is None:
            problems.append(f"{scope}: no Committees section; the legacy committee vanished under the window")
        else:
            names = [row[0] for row in _search_rows(committees)]
            if names != ["Pennyroyal Legacy Committee"]:
                problems.append(f"{scope}: legacy committee rows {names}")
        if donors is None:
            problems.append(f"{scope}: no Donors section; the legacy donor vanished under the window")
        else:
            rows = [(row[0], row[2]) for row in _search_rows(donors)]
            if rows != [("Pennyroyal Legacy Donor", "$1,000.00")]:
                problems.append(f"{scope}: legacy donor rows {rows}")
    assert not problems, "\n" + "\n".join(problems)


# ===========================================================================
# #38 -- the Reports section is dated by filing date, the page bar by receipt date
# ===========================================================================
REPORT_SCOPES = (
    "period=all",
    "period=cycle:2026",                          # 2025-01-01..2026-12-31
    "date_from=2025-06-01&date_to=2025-08-31",
)


def test_search_reports_section_says_by_filing_date_under_any_scope(search_app):
    """E3 / #38: the Reports feeder windows ``reports.filed_date`` while the page bar (axis ``received``) says "by receipt date" --
    right for Donor Keys, wrong for Reports. The page-level axis stays (it still describes the other windowed sections); the Reports
    section itself carries the words "by filing date" (heading or a line inside the section, reader text, any case), its heading tag
    stays windowed, and the two all-time sections beside it do not say it. Q = Pennyroyal: Reports (report 31 is filed inside every
    scope below), Committees and Donors (their legacy rows are inside every scope below) are all present.

    Kills: today (no words); switching the whole page to the filed axis (the bar would say "by filing date"); a note on the page
    subtitle only (outside the section); the words repeated on every section; retagging Reports as fixed "All time" (its rows are
    windowed); words hidden in an attribute or comment (reader text only)."""
    problems = []
    for scope in REPORT_SCOPES:
        doc = _get(search_app, f"/search?q=Pennyroyal&{scope}")
        if _axis_labels(doc) != ["by receipt date"]:
            problems.append(f"{scope}: the page bar axis is {_axis_labels(doc)}, expected ['by receipt date']")
        reports = _section(doc, "Reports", level="h2")
        if reports is None:
            problems.append(f"{scope}: no Reports section (fixture lost a row?)")
            continue
        if "by filing date" not in _flat(reports).lower():
            problems.append(f"{scope}: the Reports section does not say 'by filing date': {_flat(reports)[:160]!r}")
        tags = _tags(reports)
        if not tags or any(fixed for fixed, _ in tags):
            problems.append(f"{scope}: the Reports tags {tags} must stay windowed (its rows are windowed by filed_date)")
        for title in ("Committees", "Donors"):
            beside = _section(doc, title, level="h2")
            if beside is None:
                problems.append(f"{scope}: no {title!r} section (control for the 'not repeated everywhere' check)")
            elif "by filing date" in _flat(beside).lower():
                problems.append(f"{scope}: the {title} section also says 'by filing date'")
    assert not problems, "\n" + "\n".join(problems)


def test_search_reports_are_windowed_by_filed_date_so_the_words_are_true(search_app):
    """GUARD (passes today): "by filing date" is only true while the feeder windows ``reports.filed_date``. Window
    2025-06-01..2025-08-31: Quarterly Alpha (filed 2025-07-01) is in, Beta (filed 2023-04-20) and Gamma (filed 2027-02-10) are out;
    their ``updated_at`` values sit on the opposite sides, so re-pointing the SQL at ``updated_at`` (which orders the list) flips the
    result. Control: period=all lists all three.

    Kills: resolving the axis mismatch by changing the SQL date instead of the label."""
    windowed = _search_rows(_need(_get(search_app, "/search?q=Pennyroyal&date_from=2025-06-01&date_to=2025-08-31"), "Reports", level="h2"))
    everything = _search_rows(_need(_get(search_app, f"/search?q=Pennyroyal&{ALL_TIME}"), "Reports", level="h2"))
    assert sorted(row[0] for row in everything) == ["Quarterly Alpha", "Quarterly Beta", "Quarterly Gamma"], "control: period=all"
    assert [row[0] for row in windowed] == ["Quarterly Alpha"], f"reports listed under the window: {[row[0] for row in windowed]}"


# ===========================================================================
# #153 -- 527 "Reports Filed" follows the window (irs527_reports.period_end)
# ===========================================================================
REPORTS_ORG = "555000111"     # the page under test
NEIGHBOR_ORG = "555000222"    # reports only, no page: two of them end inside the window
COMPACT_ORG = "555000333"     # three compact-date reports for the FLAGGED test
_ORG_ROW = (
    "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip) "
    "VALUES (?, ?, 0, 'Reports Count Org', 'Chicago', 'IL', '60601')"
)
_REPORT_ROW = (
    "INSERT INTO irs527_reports (form_id, ein, period_start, period_end, org_name, total_contributions, total_expenditures, "
    "insert_datetime) VALUES (?, ?, ?, ?, 'Reports Count Org', 0, 0, ?)"
)
# form id, period_start, period_end, insert_datetime (the "Filed" column). The columns are TEXT: production holds compact YYYYMMDD
# (1,739 of 1,739 local rows), the test seeds hold ISO, so both appear. Window = WINDOW_ISBE = 2025-02-01..2026-12-31.
REPORTS_ORG_ROWS = (
    (501, "20250101", "20250201", "2027-03-15 09:00:00"),   # IN  compact; ends ON the window's first day; filed after the window
    (502, "20260401", "20261231", "2027-04-01 09:00:00"),   # IN  compact; ends ON the window's last day; filed after the window
    (503, "2025-01-15", "2025-02-28", "2024-12-01 09:00:00"),   # IN  ISO; starts BEFORE the window, ends inside; filed before it
    (504, "2025-04-01", "2025-06-30", "2025-07-15 09:00:00"),   # IN  ISO; ordinary
    (509, "20260701", "20261015", "2027-01-20 09:00:00"),   # IN  compact, late in the last year; filed after the window
    (505, "2024-10-01", "2025-01-31", "2025-06-15 09:00:00"),   # OUT ISO; ends the day BEFORE the window; filed INSIDE it
    (506, "20261201", "20270101", "2026-05-05 09:00:00"),   # OUT compact; STARTS inside the window, ends the day AFTER it; filed inside
    (507, "2023-01-01", "2023-03-31", "2023-04-20 09:00:00"),   # OUT ISO; years before
    (508, None, None, None),                                 # OUT undated: unknown period, excluded under a bound, counted all time
)
COMPACT_ORG_ROWS = (
    (701, "20250101", "20250131", "2025-03-01 09:00:00"),   # OUT compact; ends the day BEFORE the window 2025-02-01..2026-12-31
    (702, "20250601", "20250615", "2025-06-20 09:00:00"),   # IN  compact; mid 2025
    (703, "20241201", "20241231", "2025-01-10 09:00:00"),   # OUT compact; the year before
)


@pytest.fixture
def reports_org_app(irs527_app):
    """irs527_app (org 123456789 with one report ending 2026-03-31) plus three more EINs that have reports.

    Org 555000111 (the page under test) has the nine reports of REPORTS_ORG_ROWS. In the window the five marked IN end inside it
    ("by period_end" -> 5), the four marked OUT do not, so all time = 9. Each plausible wrong reading gives another number:
    windowing ``insert_datetime`` (the "Filed" column) counts 504, 505, 506 -> 3; ``period_start`` counts 502, 504, 509, 506 -> 4;
    any overlap with the window counts 501, 502, 503, 504, 509, 506 -> 6; "contained in the window" counts 502, 504, 509 -> 3;
    an ISO-only string compare of ``period_end`` counts 501, 503, 504 -> 3 (it loses the compact 20261231 and 20261015).
    One-day windows: 2025-02-01 holds only 501 (1), 2026-12-31 only 502 (1); 2030 holds none (0).
    Neighbours a counter without ``ein = ?`` would add: org 555000222 has two reports ending 2025-04-30 and 2026-03-31, org 555000333
    one inside the window (702), the base org one ending 2026-03-31 -> 5 + 2 + 1 + 1 = 9 under the window, 9 + 2 + 3 + 1 = 15 all time.
    Org 555000333 (see COMPACT_ORG_ROWS) feeds the FLAGGED compact-date test: 3 reports, 1 inside the window.
    """
    _topup(
        irs527_app,
        (_ORG_ROW, (REPORTS_ORG, 500)),
        (_ORG_ROW, (COMPACT_ORG, 700)),
        *[(_REPORT_ROW, (form_id, REPORTS_ORG, start, end, filed)) for form_id, start, end, filed in REPORTS_ORG_ROWS],
        *[(_REPORT_ROW, (form_id, COMPACT_ORG, start, end, filed)) for form_id, start, end, filed in COMPACT_ORG_ROWS],
        (_REPORT_ROW, (601, NEIGHBOR_ORG, "2025-04-01", "2025-04-30", "2025-05-15 09:00:00")),
        (_REPORT_ROW, (602, NEIGHBOR_ORG, "20260101", "20260331", "2026-04-15 09:00:00")),
    )
    return irs527_app


def _reports_filed(doc: str) -> int | None:
    match = re.search(r"Reports Filed:?\s*(\d+)", _flat(_need(doc, "Financial Summary", level="h2")))
    return int(match.group(1)) if match else None


def test_527_reports_filed_follows_the_window(reports_org_app):
    """E4 / #153 / J-S14: ``org_detail`` counts ``irs527_reports WHERE ein = ?`` with no date clause, so the card reads the same
    under every scope while its heading tag claims the window. Under a window it counts this EIN's reports whose ``period_end``
    is inside it, bounds inclusive; the default and ``period=all`` keep every report, undated included.

    Kills: today (9 under every window); windowing another date column or reading another semantic (see the fixture: filed 3,
    period_start 4, overlap 6, contained 3, ISO-only string compare 3); an exclusive bound (the one-day windows on 501 / 502);
    a count without ``ein = ?`` (9 / 15 with the neighbours); falling back to the all-time count when the window holds nothing
    (2030 -> 0); breaking the unbounded path. Compact dates near the window start: see the FLAGGED test below."""
    url = f"/527/{REPORTS_ORG}"
    wanted = (
        (f"{url}?{WINDOW_ISBE}", 5),
        (f"{url}?date_from=2025-02-01&date_to=2025-02-01", 1),
        (f"{url}?date_from=2026-12-31&date_to=2026-12-31", 1),
        (f"{url}?{WINDOW_EMPTY}", 0),
        (f"{url}?{ALL_TIME}", 9),
        (url, 9),
    )
    problems = []
    for page, expected in wanted:
        got = _reports_filed(_get(reports_org_app, page))
        if got != expected:
            problems.append(f"{page}: Reports Filed {got}, expected {expected}")
    assert not problems, "\n" + "\n".join(problems)


def test_527_reports_filed_compares_compact_dates_as_dates(reports_org_app):
    """FLAGGED (found while hand-computing the fixture; the brief points at ``_text_date_window_clause`` for TEXT dates): production
    stores ``irs527_reports.period_end`` compact (YYYYMMDD). The helper ORs an ISO-bounds branch with a compact-bounds branch over
    the same column, and a lexical compare of a compact value with ISO bounds only decides on the YEAR: for a window that spans
    years, every compact date in the window's first year passes the ISO branch. Reusing the helper as is -- the obvious
    implementation -- therefore counts reports that ended before the window starts. Org 555000333: 701 ends 20250131 (the day before
    2025-02-01), 702 ends 20250615, 703 ends 20241231. Window 2025-02-01..2026-12-31 -> only 702 (the helper says 2); a window
    2025-10-26..2026-10-26 (what "last 12 months" looks like on 2026-10-26) -> none (the helper says 2: all of 2025 passes).
    All time -> 3.

    Kills: the unmodified helper (also leaks in the other 527 date filters on compact data: contributions / expenditures totals and
    ledgers -- an incidental finding, fixing the helper would repair those too); any comparison that does not normalise both sides to
    one format. Drop this test if the helper's behaviour on compact dates is out of scope for S-W6b (then make row 701 ISO)."""
    url = f"/527/{COMPACT_ORG}"
    wanted = (
        (f"{url}?{WINDOW_ISBE}", 1),
        (f"{url}?date_from=2025-10-26&date_to=2026-10-26", 0),
        (f"{url}?{ALL_TIME}", 3),
    )
    problems = []
    for page, expected in wanted:
        got = _reports_filed(_get(reports_org_app, page))
        if got != expected:
            problems.append(f"{page}: Reports Filed {got}, expected {expected}")
    assert not problems, "\n" + "\n".join(problems)


def test_527_financial_summary_tag_stays_windowed(reports_org_app):
    """GUARD (passes today): once Reports Filed follows the window, the Financial Summary tag must stay the page's window tag --
    the alternative fix (split the figure out under a fixed "All time" tag) is not the decision. Custom window: a non-fixed tag
    that is not "All time"; ``period=all``: the non-fixed "All time" tag (the class, not the text, tells windowed from fixed).

    Kills: tagging the section or a split-out Reports Filed line as fixed "All time"; dropping the tag."""
    windowed = _tags(_need(_get(reports_org_app, f"/527/{REPORTS_ORG}?{WINDOW_ISBE}"), "Financial Summary", level="h2"))
    assert windowed, "Financial Summary carries no scope tag"
    assert not any(fixed for fixed, _ in windowed), f"Financial Summary is windowed but its tags are {windowed}"
    assert all(text != "All time" for _, text in windowed), f"a custom window labelled 'All time': {windowed}"
    everything = _tags(_need(_get(reports_org_app, f"/527/{REPORTS_ORG}?{ALL_TIME}"), "Financial Summary", level="h2"))
    assert everything == [(False, "All time")], f"period=all Financial Summary tags: {everything}"


def test_527_filing_history_keeps_every_report_under_a_window(reports_org_app):
    """GUARD (passes today): Filing History is tagged fixed "All time" and lists every report of the EIN. Windowing the Reports
    Filed figure must not shrink it (a count computed by filtering the shared ``reports`` list would). Under the window: heading
    "(9 Reports)" and all nine form ids, the four OUT ones included.

    Kills: filtering the shared list instead of counting separately."""
    section = _need(_get(reports_org_app, f"/527/{REPORTS_ORG}?{WINDOW_ISBE}"), "Filing History", level="h2")
    text = _flat(section)
    assert _has(text, "(9 Reports)"), f"Filing History heading: {text[:80]!r}"
    missing = [form_id for form_id, *_ in REPORTS_ORG_ROWS if not _has(text, str(form_id))]
    assert missing == [], f"form ids missing from Filing History under the window: {missing}"
    assert _tags(section) == [(True, "All time")], f"Filing History tags: {_tags(section)}"


# ===========================================================================
# #99 -- Top Federal Races: money in the window, honest cycle marker
# ===========================================================================
@pytest.fixture
def top_races_app(w_federal_app):
    """w_federal_app (Jackson, IL-01, matched in cycles 2026 and 2024; Smith, IL-09, matched in 2026 only; see
    ``_seed_federal_extras`` for the hand-computed money) plus one rival in Jackson's race who has NO money anywhere:
    Old Rival (H2IL01888), matched to IL-01 in cycle 2024 only.

    Top Federal Races, race IL-01 / IL-09 ("Total Amount" column):
      WINDOW_FEDERAL 2026-01-01..2026-06-30: IL-01 $550.00 (outside spending $700.00); IL-09 has nothing (Smith's money is 2025-04-10,
        2025-05-05 and 2026-09-20) -> today a $0.00 row.
      WINDOW_2024 2023-05-01..2023-12-31 (inside cycle 2024): IL-01 $400.00 (Jane's 2023-06-01 gift); IL-09 nothing.
      WINDOW_EMPTY (2030): nothing. All time: IL-01 $2,115.00, IL-09 $855.00.
    Candidates of IL-01: the 2026 cycle has one (Jackson); all cycles have two (Jackson and Old Rival). Under a custom window the page
    passes cycle=None to the feeder, so today the column counts every cycle's candidates ("2" under WINDOW_FEDERAL).
    """
    seed = (
        "INSERT INTO fec_il_candidate_seed (candidate_key, as_of_date, cycle, office, office_code, district, district_code, party, "
        "party_code, election_stage, candidate_name, normalized_candidate_name, write_in, already_listed_general, source_file, "
        "source_row_number) VALUES ('seed-key-rival-2024', '2024-02-07', 2024, 'U.S. House', 'H', 'IL-01', '01', 'Republican', 'REP', "
        "'Primary', 'Old Rival', 'OLD RIVAL', 0, 0, 'test_seed.csv', 11)"
    )
    match = (
        "INSERT INTO fec_candidate_match (seed_candidate_key, candidate_name, office, office_code, district, district_code, party, "
        "party_code, election_stage, cycle, fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party, "
        "match_status, match_score, match_method) VALUES ('seed-key-rival-2024', 'Old Rival', 'U.S. House', 'H', 'IL-01', '01', "
        "'Republican', 'REP', 'Primary', 2024, 'H2IL01888', 'RIVAL, OLD', 'H', 'IL', '01', 'REP', 'matched', 95.0, 'test')"
    )
    return _topup(w_federal_app, (seed, ()), (match, ()))


RACE_IL01 = "U.S. House - IL-01"
RACE_IL09 = "U.S. House - IL-09"


def _race_rows(doc: str) -> list[dict[str, str]]:
    """The first table of Top Federal Races (its first ``<tbody>``) as ``{header text: cell text}`` per race row."""
    headers, rows = _first_table(_need(doc, "Top Federal Races", level="h2"))
    return [dict(zip(headers, (_flat(cell) for cell in cells))) for cells in rows]


def _cell(row: dict[str, str], header: str) -> str:
    """The cell under the first column whose header starts with ``header`` (a header decorated with a tag still matches)."""
    for name, value in row.items():
        if name.startswith(header):
            return value
    raise AssertionError(f"no column {header!r} in {list(row)}")


def test_top_federal_races_table_lists_only_races_with_money_in_the_window(top_races_app):
    """E4 / #99 / harness finding F1: the race set comes from fec_candidate_match, the money from Schedule A / E in the window, so
    Smith's IL-09 (money only outside the window) is a $0.00 row today -- even for a window in the 2023-2024 cycle, because a custom
    window leaves ``cycle_filter`` None and lists every cycle's candidates. The honest presentation (the brief allows a separate "no
    activity in this window" state): the FIRST table's body has no race with money in neither Schedule A nor E. Pinned per scope:
    window -> only IL-01 (with its real figure); empty window -> no race rows; all time -> both, IL-09 at its $855.00 (control: the
    race is not hidden everywhere).

    Kills: today (IL-09 at $0.00 under the three windows); emptying the table under every window (IL-01 stays, with its figure);
    hiding IL-09 everywhere (all-time control); a "show at least one row" fallback on the empty window; hiding zero rows by renaming
    them (the all-zero check is by values, not by label). Accepted hole: a race with only in-window Schedule E money is neither
    required nor forbidden (the brief leaves it open; no fixture race has that shape)."""
    expected = {
        WINDOW_FEDERAL: [(RACE_IL01, "$550.00")],
        WINDOW_2024: [(RACE_IL01, "$400.00")],
        WINDOW_EMPTY: [],
        ALL_TIME: [(RACE_IL01, "$2,115.00"), (RACE_IL09, "$855.00")],
    }
    problems = []
    for scope, wanted in expected.items():
        rows = _race_rows(_get(top_races_app, f"/federal-finance/?{scope}"))
        got = [(_cell(row, "Race"), _cell(row, "Total Amount")) for row in rows]
        if got != wanted:
            problems.append(f"{scope}: main table rows {got}, expected {wanted}")
        all_zero = [
            _cell(row, "Race")
            for row in rows
            if _cell(row, "Contributions") == "0"
            and _cell(row, "Total Amount") == "$0.00"
            and _cell(row, "Outside Spending") == "$0.00"
        ]
        if all_zero:
            problems.append(f"{scope}: races listed with no money at all: {all_zero}")
    assert not problems, "\n" + "\n".join(problems)


def test_top_federal_races_marks_the_cycle_granular_candidate_set(top_races_app):
    """E4 / #99 / J-S14: the candidate set (Candidates column, race membership) comes from the cycle, not from the dates, while the
    amounts follow the window. The Top Federal Races block carries a FIXED ``<window's cycle> Cycle`` scope tag (on the heading or on
    the candidate-count column; anywhere inside the block) and keeps the window tag for the amounts. WINDOW_FEDERAL sits inside
    cycle 2026, WINDOW_2024 inside cycle 2024.

    Kills: today (no fixed tag); a tag hard-coded to the current cycle (WINDOW_2024 must say 2024; the template's ``cycle`` variable is
    ``current_cycle()`` for custom windows); a plain-text note instead of a scope tag; both cycles shown; replacing the window tag by
    the fixed one (the amounts are windowed). Accepted hole: a window spanning two cycles has no single cycle and is not pinned;
    ``period=all`` and cycle presets need no extra marker and are not pinned."""
    problems = []
    for scope, marker in ((WINDOW_FEDERAL, "2026 Cycle"), (WINDOW_2024, "2024 Cycle")):
        tags = _tags(_need(_get(top_races_app, f"/federal-finance/?{scope}"), "Top Federal Races", level="h2"))
        fixed = {text for is_fixed, text in tags if is_fixed}
        windowed = [text for is_fixed, text in tags if not is_fixed]
        if fixed != {marker}:
            problems.append(f"{scope}: fixed tags {sorted(fixed)}, expected {{{marker!r}}} (all tags: {tags})")
        if "Custom range" not in windowed:
            problems.append(f"{scope}: the window tag for the amounts is gone: {tags}")
    assert not problems, "\n" + "\n".join(problems)


def test_top_federal_races_candidates_column_counts_the_marked_cycle_only(top_races_app):
    """FLAGGED (what makes the marker honest; not spelled out in the brief): under WINDOW_FEDERAL the page passes ``cycle=None``, so the
    Candidates column of IL-01 counts every cycle's candidates -- Jackson and the 2024-only Old Rival, "2" -- and a "2026 Cycle" tag
    over that number would be false. IL-01 must read 1 (Jackson, the only 2026-cycle candidate; the "only candidates with money in the
    window" reading also gives 1). Not pinned: the 2024 window and the all-time count (the two readings disagree there).

    Kills: tag-only fixes that leave the all-cycle candidate set (today: 2); a global candidate count (IL-09's Smith would make it 2).
    Drop this test if the intended fix is the tag alone."""
    rows = _race_rows(_get(top_races_app, f"/federal-finance/?{WINDOW_FEDERAL}"))
    il01 = [row for row in rows if _cell(row, "Race") == RACE_IL01]
    assert len(il01) == 1, f"expected exactly one IL-01 row, got {[_cell(row, 'Race') for row in rows]}"
    assert _cell(il01[0], "Candidates") == "1", f"IL-01 Candidates under WINDOW_FEDERAL: {_cell(il01[0], 'Candidates')!r}"


# ===========================================================================
# Follow-up: the home page's 527 cards window TEXT dates with the org page's helper (one definition)
# ===========================================================================
def test_home_527_card_ignores_an_amount_like_date_like_the_org_page(w_irs527_app):
    """The 527 loader leaves amounts in ``irs527_contributions.date`` for many rows. Compared lexically with ISO bounds, '20250'
    passes a window that starts in 2025 ('20250' > '2025-02-01'), so the unguarded helper counted such a row; the org page's
    guarded helper (S-W6b) does not, and the home page's "Total 527 Contributions Received" card must window the same way.
    w_irs527_app under WINDOW_ISBE: Acme Donor 4,321 is the only contribution inside; the added row (999, date '20250') is no date.

    Kills: a second, unguarded copy of the helper behind the home page (it shows $5,320.00 and 2 contribution records)."""
    _topup(w_irs527_app, (
        "INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date) "
        "VALUES (100, '123456789', 'Test 527 Org', 'Garbled Date Donor', 'Chicago', 'IL', 999, '20250')", ()))
    org = _flat(_need(_get(w_irs527_app, f"/527/123456789?{WINDOW_ISBE}"), "Financial Summary", level="h2"))
    home = _flat(_need(_get(w_irs527_app, f"/?{WINDOW_ISBE}"), "Total 527 Contributions Received"))
    assert _has(org, "Total Contributions: $4,321.00") and _has(home, "$4,321.00") and _has(home, "1 contribution records"), (
        f"org page: {org!r}; home card: {home!r}")
