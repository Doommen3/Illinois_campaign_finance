"""Dated-widget registry completeness (plan profiles-races-dated-widgets, step W1 / S-W1).

``tests/scope_widgets_registry.py`` is the live list of widgets whose content
depends on the page's time scope. This test keeps it complete: every windowed
``scope_tag(`` call under ``webapp/templates`` must have a registry row. It is
one-way on purpose: a reverse "stale row" check would fire on every copy edit,
and the behaviour harness GETs every registered URL anyway.

A *windowed* site is ``scope_tag()`` or ``scope_tag(<anything but 'All time'>)``;
fixed-cycle texts such as ``'2026 Cycle'`` belong to the windowed family.
``scope_tag('All time')`` sites are not required in the registry.

A site is identified by ``(template, heading, ordinal)``:

* template -- path relative to ``webapp/templates``.
* heading  -- the text of the tag's own line as a reader sees it: the
  ``scope_tag`` call and Jinja statements removed, every other ``{{ expression }}``
  shown as ``N``, HTML tags removed, whitespace collapsed.
* ordinal  -- 1-based rank among ALL ``scope_tag`` calls in that template that
  share the heading (``'All time'`` ones included), in source order, so two
  widgets with the same title (``candidates.html`` has two "Candidates") stay
  distinct and every registry key is unique.

Step S-W2 (W2 + W3) adds the behaviour harness further down: one test per page, probing
every ``window`` widget of the registry with rows inside and outside an explicit window
(see "Behaviour harness" below), plus the accounting tests that keep the registry and the
harness in step and the recorded (partial / mismatch / unscoped) rows.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

import pytest

from database.connection import get_db
from tests.scope_widgets_registry import WIDGETS, by_template

TEMPLATES = Path(__file__).resolve().parents[1] / "webapp" / "templates"
MACROS = "_partials/macros.html"  # defines scope_tag; its own text is not a call site

_CALL = re.compile(r"\bscope_tag\(")
_ALL_TIME_CALL = re.compile(r"""scope_tag\(\s*(?:text\s*=\s*)?(['"])All time\1\s*\)""")
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}")
_JINJA_STMT = re.compile(r"\{%.*?%\}")
_HTML_TAG = re.compile(r"<[^>]*>")


class Site(NamedTuple):
    template: str
    line: int
    heading: str
    ordinal: int
    windowed: bool


def heading_text(line: str) -> str:
    """The text on a template line as a reader sees it (see the module docstring)."""
    line = _JINJA_EXPR.sub(lambda m: "" if "scope_tag(" in m.group(0) else "N", line)
    line = _JINJA_STMT.sub("", line)
    return " ".join(html.unescape(_HTML_TAG.sub("", line)).split())


def tag_sites() -> list[Site]:
    """Every ``scope_tag(`` call under webapp/templates, in template then source order."""
    sites: list[Site] = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        template = path.relative_to(TEMPLATES).as_posix()
        if template == MACROS:
            continue
        seen: Counter[str] = Counter()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for call in _CALL.finditer(line):
                heading = heading_text(line)
                seen[heading] += 1
                windowed = not _ALL_TIME_CALL.match(line, call.start())
                sites.append(Site(template, number, heading, seen[heading], windowed))
    return sites


def test_every_windowed_tag_site_is_registered():
    sites = tag_sites()
    assert sites, f"no scope_tag( call found under {TEMPLATES}; the template walk is broken"
    registry = by_template()
    missing = [
        f"{s.template}:{s.line}  heading={s.heading!r}  ordinal={s.ordinal}"
        + ("  <- no heading text on this line; register with heading='' and a note" if not s.heading else "")
        for s in sites
        if s.windowed and (s.template, s.heading, s.ordinal) not in registry
    ]
    assert not missing, (
        f"{len(missing)} windowed scope_tag site(s) missing from tests/scope_widgets_registry.py "
        "(add a Widget with the same template, heading and ordinal):\n  " + "\n  ".join(missing)
    )


# ===========================================================================
# Behaviour harness (plan step S-W2 = W2 + W3; decisions J-W2, J-W3)
#
# One test per page. Each test GETs the page twice -- once with an explicit window
# and once with a control scope that shows everything -- and, for every registered
# ``window`` widget on that page, slices the widget out of the HTML by its heading
# and asserts:
#
#   ins   markers that must appear inside the slice under the window;
#   outs  markers that must NOT appear under the window, and MUST appear under the
#         control scope (the control is what keeps an OUT marker from passing
#         vacuously when a copy edit stops the page rendering it);
#   card  (windowed text, all-time text) of a stat card: both must render, they
#         must differ, and the all-time text must not leak into the windowed card.
#
# Every fixture carries OUT rows on BOTH sides of its window: before it (the same cycle, and
# 2023) and after it (later in 2026, or 2027). So a window that snaps to whole cycles or years,
# that ignores the dates and falls back to the default scope, or that loses its upper bound
# fails. The profile, live-feed, federal and Viz Lab fixtures also put an IN row exactly on
# date_from and on date_to (the profile one with an OUT row on each adjacent day), and the
# OpenBook and lobbying fixtures one on date_from, so an exclusive bound fails there.
# Accepted holes: the other pages have no edge-day row (``scope_where`` and the resolver tests
# pin inclusivity), and the analytics pages are month-granular, so their window edges sit on
# month boundaries.
# ===========================================================================
# Imported now, at collection time, on purpose: webapp/routes/analytics.py binds ``get_db`` when it is first imported and
# create_app imports it lazily -- inside the first test that builds an app, where conftest has patched ``get_db`` with THAT
# test's schema. Every later test would then build the networks page's parallel graphs (network, vendor, lobbying, 527) on a
# dropped schema and silently get empty graphs ("relation ... does not exist" in the log).
import webapp.routes.analytics  # noqa: E402,F401
from tests.test_analytics_features import analytics_app  # noqa: E402,F401
from tests.test_profile_scope import (  # noqa: E402,F401
    _alice_key,
    _jane_entity_key,
    _seed_federal,
    donor_app,
    federal_app,
    irs527_app,
    openbook_app,
    profile_app,
)

WINDOW_ISBE = "date_from=2025-02-01&date_to=2026-12-31"      # ISBE / 527 / lobbying / OpenBook fixtures
WINDOW_FEDERAL = "date_from=2026-01-01&date_to=2026-06-30"   # FEC fixtures (first half of 2026)
WINDOW_ANALYTICS = "date_from=2026-02-01&date_to=2026-03-31"  # analytics fixture (rows Jan-Apr 2026)
ALL_TIME = "period=all"

_HEADING_TAG = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.S)
_SCOPE_TAG_SPAN = re.compile(r'<span class="scope-tag[^"]*">.*?</span>', re.S)
_ANY_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(<script\b[^>]*>.*?</script>)", re.S | re.I)
_EXPRESSION = re.compile(r"(?<![A-Za-z])N(?![A-Za-z])")


def _flat(fragment: str) -> str:
    """Reader's text of an HTML fragment: tags dropped, entities decoded, whitespace collapsed.

    ``<script>`` bodies are kept verbatim (the embedded chart JSON lives there and may contain ``<``).
    """
    pieces = []
    for part in _SCRIPT.split(fragment):
        if part.lower().startswith("<script"):
            pieces.append(re.sub(r"^<script[^>]*>|</script>$", " ", part, flags=re.I))
        else:
            pieces.append(_ANY_TAG.sub(" ", part))
    return " ".join(html.unescape(" ".join(pieces)).split())


def _widget_slice(doc: str, heading: str, level: str | None = None, ordinal: int = 1) -> str | None:
    """HTML of one widget: from its heading to the next heading of the same or a higher level.

    ``heading`` is the registry heading (every template expression shown as ``N``);
    the scope-tag label is ignored when matching. ``ordinal`` is the 1-based rank
    among the page's headings with that text, ``level`` restricts to ``h2``/``h3``.
    """
    pieces = [re.escape(piece) for piece in _EXPRESSION.split(heading)]
    wanted = re.compile(r"\S+".join(pieces))
    found = [
        (match.start(), match.group(1), _flat(_SCOPE_TAG_SPAN.sub("", match.group(2))))
        for match in _HEADING_TAG.finditer(doc)
    ]
    seen = 0
    for index, (start, tag, text) in enumerate(found):
        if (level and tag != level) or not wanted.fullmatch(text):
            continue
        seen += 1
        if seen == ordinal:
            stop = next((s for s, t, _ in found[index + 1:] if t <= tag), len(doc))
            return doc[start:stop]
    return None


def _has(text: str, marker: str) -> bool:
    """Whole-token containment, so ``$5,000.00`` is not found inside ``$15,000.00`` nor ``2`` inside ``2026``."""
    left = r"(?<![\w.,])" if marker[:1].isalnum() else ""
    right = r"(?![\w]|[.,]\d)" if marker[-1:].isalnum() else ""
    return re.search(left + re.escape(marker) + right, text) is not None


def _as_text(body: str) -> str:
    """Text to search: JSON responses are re-serialised (``": "`` separators) so markers do not depend on Flask's compact mode."""
    if body.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(json.loads(body), sort_keys=True)
        except ValueError:
            pass
    return _flat(body)


class Probe(NamedTuple):
    """One registered ``window`` widget on a page: where it is and what proves its window."""

    heading: str                 # registry heading (with the registry ordinal: the registry key)
    ins: tuple = ()              # markers inside the slice under the window
    outs: tuple = ()             # markers absent under the window, present under the control
    ordinal: int = 1             # registry ordinal (also the rank of the heading on the page)
    level: str | None = None     # "h2"/"h3" when two levels share the heading text
    at: str | None = None        # heading to find on the page when the registry heading is a label (untagged rows)
    card: tuple | None = None    # (windowed text, all-time text) of a stat card
    whole_page: bool = False     # no heading to slice by (untagged / <p> tag): the markers are searched page-wide
    block: str | None = None     # id of the <script type="application/json"> element holding the chart data (searched instead of the slice)
    via: str | None = None       # path+query of the response the widget draws from (JSON endpoint, other search type)
    gap: str | None = None       # finding id: the window does NOT hold today (strict: the probe fails once it is fixed)


class Page(NamedTuple):
    template: str                # registry template of the probed widgets
    url: str                     # path (+ fixed query) of the page
    probes: tuple
    window: str = WINDOW_ISBE
    control: str = ALL_TIME


def _with_query(url: str, query: str) -> str:
    return url + ("&" if "?" in url else "?") + query


def _headings_on(doc: str) -> list[str]:
    return [_flat(_SCOPE_TAG_SPAN.sub("", match.group(2))) for match in _HEADING_TAG.finditer(doc)]


def _json_block(doc: str, block_id: str) -> str | None:
    """The ``<script id=block_id type="application/json">`` payload, re-serialised with ``": "`` separators."""
    match = re.search(rf'<script id="{re.escape(block_id)}"[^>]*>(.*?)</script>', doc, re.S)
    return json.dumps(json.loads(match.group(1)), sort_keys=True) if match else None


def _widget_text(probe: Probe, doc: str, name: str, problems: list[str], label: str) -> str | None:
    """The text a probe's markers are searched in, or None (with a problem recorded) when the widget cannot be located."""
    if probe.block:
        text = _json_block(doc, probe.block)
        if text is None:
            problems.append(f"{label}: no <script id={probe.block!r}> in the {name} page")
        return text
    if probe.whole_page or probe.via:
        return _as_text(doc)
    part = _widget_slice(doc, probe.at or probe.heading, probe.level, probe.ordinal)
    if part is None:
        problems.append(f"{label}: no heading {probe.at or probe.heading!r} (ordinal {probe.ordinal}) in the {name} page; headings: {_headings_on(doc)}")
        return None
    return _flat(part)


def _probe_problems(probe: Probe, label: str, windowed: str, control: str) -> tuple[list[str], list[str]]:
    """``(structure, behaviour)`` problems of one widget. Structure = the check itself is broken (heading or JSON block not
    found, an OUT marker the control scope does not show, nothing asserted); behaviour = the window does not hold."""
    structure: list[str] = []
    behaviour: list[str] = []
    in_window = _widget_text(probe, windowed, "window", structure, label)
    in_control = _widget_text(probe, control, "control", structure, label)
    if in_window is None or in_control is None:
        return structure, behaviour
    for marker in probe.ins:
        if not _has(in_window, marker):
            behaviour.append(f"{label}: IN marker {marker!r} missing under the window")
    for marker in probe.outs:
        if _has(in_window, marker):
            behaviour.append(f"{label}: OUT marker {marker!r} leaked into the windowed widget")
        if not _has(in_control, marker):
            structure.append(f"{label}: OUT marker {marker!r} is not shown under the control scope (vacuous check)")
    if probe.card:
        windowed_text, all_time_text = probe.card
        if windowed_text == all_time_text:
            structure.append(f"{label}: card texts must differ, got {windowed_text!r} twice")
        if not _has(in_window, windowed_text):
            behaviour.append(f"{label}: card value {windowed_text!r} missing under the window")
        if _has(in_window, all_time_text):
            behaviour.append(f"{label}: all-time card value {all_time_text!r} leaked into the windowed card")
        if not _has(in_control, all_time_text):
            structure.append(f"{label}: card value {all_time_text!r} is not shown under the control scope")
    if not (probe.ins or probe.outs or probe.card):
        structure.append(f"{label}: probe asserts nothing")
    return structure, behaviour


def _check_page(app, page: Page, **names: str) -> None:
    """Run every probe of ``page``; ``names`` fill ``{placeholders}`` in the page and probe URLs (entity keys built by the fixture)."""
    registry = by_template()
    client = app.test_client()
    fetched: dict[tuple[str, str], str] = {}

    def fetch(url: str, scope: str) -> str:
        if (url, scope) not in fetched:
            response = client.get(_with_query(url, scope))
            assert response.status_code == 200, (url, scope, response.status_code)
            fetched[(url, scope)] = response.get_data(as_text=True)
        return fetched[(url, scope)]

    problems: list[str] = []
    for probe in page.probes:
        key = (page.template, probe.heading, probe.ordinal)
        label = f"{page.url} [{probe.heading}#{probe.ordinal}]"
        row = registry.get(key)
        if row is None:
            problems.append(f"{label}: no registry row {key}")
            continue
        if row.status != "window":
            problems.append(f"{label}: registry status is {row.status!r}; a probe asserts a held window, so move it to the recorded rows")
            continue
        where = (probe.via or page.url).format(**names)
        structure, behaviour = _probe_problems(probe, label, fetch(where, page.window), fetch(where, page.control))
        problems.extend(structure)  # a broken check is a problem even when the window is a known gap
        if probe.gap:
            if not behaviour and not structure:
                problems.append(f"{label}: gap {probe.gap!r} is closed -- drop gap= and flip the registry row to window")
        else:
            problems.extend(behaviour)
    assert not problems, "\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# Fixtures: the existing builders (profile_app, donor_app, ...) topped up IN PLACE
# with the rows a page lacks. Nothing below changes a value the other test files
# assert on: the rows are inserted after the base fixture is built, in this file.
# ---------------------------------------------------------------------------
def _topup(app, *statements):
    """Run ``(sql, params)`` statements against the app's database and commit."""
    conn = get_db(app.config["DATABASE_PATH"])
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    return app


_INSERT_EXPENDITURE = (
    "INSERT INTO isbe_expenditures (id, committee_id, filed_doc_id, last_name, expended_date, amount, purpose, d2_part, archived) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, '2A', FALSE)"
)
_INSERT_RECEIPT = (
    "INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name, received_date, amount, d2_part, "
    "city, state, zipcode, address1, archived) VALUES (?, ?, ?, ?, ?, ?, ?, '1A', 'Chicago', 'IL', '60601', ?, FALSE)"
)


@pytest.fixture
def w_profile_app(profile_app):
    """profile_app (committee 100: receipts Jan/Feb/May 2025 + 2023) plus payees and receipts on both sides of the window.

    Window 2025-02-01..2026-12-31. One IN row sits ON each edge and an OUT row on each adjacent day, so a `>`/`<` slip, a
    dropped bound or a window one day too wide fails. Committee 100 expenditures:
      OUT  20102 Stale Mailers      2023-08-01     450
      OUT  20001 Acme Consulting    2025-01-20   3,000   (same cycle, before the window; in the base seed)
      OUT  20104 Day Before Print   2025-01-31      60   (the day before the window)
      IN   20101 Window Printing    2025-02-01   1,100   (the first day of the window)
      IN   20103 Year End Mailers   2026-12-31     200   (the last day of the window)
      OUT  20105 New Year Printing  2027-01-01      90   (the day after the window)
    window: 2 transactions, 1,300; all time: 6 transactions, 4,900 = 450 + 3,000 + 60 + 1,100 + 200 + 90.
    Committee 100 receipts: IN Bob 2,500 (2025-02-10) + BigCorp 25,000 (2025-05-01); OUT Oldcycle 1,000 (2023-06-01), Alice 5,000
    (2025-01-15) and Future Donor 33 (2027-01-01): 2 rows in the window, 5 in all.
    Committee 200 gets an IN receipt (Other Neighbor 77 on 2025-03-03) and has the IN expenditure Print Shop Inc 1,500 on 2025-02-01
    (base seed), so a page that counted the window across committees instead of this one shows 3 rows and 2,800, not 2 and 1,300.
    """
    return _topup(
        profile_app,
        (_INSERT_RECEIPT, (10104, 200, 5002, "Neighbor", "Other", "2025-03-03", 77.0, "9 Elm St")),
        (_INSERT_RECEIPT, (10105, 100, 5001, "Donor", "Future", "2027-01-01", 33.0, "7 Elm St")),
        (_INSERT_EXPENDITURE, (20101, 100, 5001, "Window Printing", "2025-02-01", 1100.0, "Printing")),
        (_INSERT_EXPENDITURE, (20102, 100, 5001, "Stale Mailers", "2023-08-01", 450.0, "Mailers")),
        (_INSERT_EXPENDITURE, (20103, 100, 5001, "Year End Mailers", "2026-12-31", 200.0, "Mailers")),
        (_INSERT_EXPENDITURE, (20104, 100, 5001, "Day Before Print", "2025-01-31", 60.0, "Printing")),
        (_INSERT_EXPENDITURE, (20105, 100, 5001, "New Year Printing", "2027-01-01", 90.0, "Printing")),
    )


@pytest.fixture
def w_donor_app(donor_app):
    """donor_app (Legacy Committee 1 / Legacy Donor 1, ISBE committees 100 and 200) plus the rows its pages lack.

    Window 2025-02-01..2026-12-31; every widget gets OUT rows before it (same cycle and 2023) and after it (2027).
    Legacy donor 1 gave 1,200 on 2025-06-15 (IN) and 300 on 2023-04-01 (OUT, base seed); added 90 on 2025-01-10 and 40 on
    2027-02-01 (OUT): window 1,200 (1 contribution), all time 1,630 (4). Legacy reports: 1 filed 2025-07-01 pending (IN); added
    2 filed 2023-04-20 scraped, 3 filed 2025-01-15 error and 4 filed 2027-02-10 skipped (all OUT).
    ISBE receipts for committee 100 (to filed doc 5001) added so one search term ("Zed") hits every side:
      IN  Zed Window 2025-03-01 400    OUT Zed Early 2025-01-05 200    OUT Zed Stale 2023-05-05 300    OUT Zed Next 2027-03-01 500
    ``bulk_d2_receipts_recon`` (built by the bulk loader in production, absent here) gets one row per filed doc; the doc's own
    received_datetime decides the window: 5001 received 2025-04-15 (IN); 5013 received 2025-01-17, 5012 received 2022-04-05 and
    5020 (added, received 2027-02-15) are OUT.
    """
    from database.analytics import refresh_analytics_materialized

    conn = get_db(donor_app.config["DATABASE_PATH"])
    try:
        for day, amount in (("2025-01-10", 90.0), ("2027-02-01", 40.0)):
            conn.execute(
                "INSERT INTO contributions (report_id, donor_id, amount, transaction_date) VALUES (1, 1, ?, ?)", (amount, day)
            )
        for report_id, day, status in ((2, "2023-04-20", "scraped"), (3, "2025-01-15", "error"), (4, "2027-02-10", "skipped")):
            conn.execute(
                "INSERT INTO reports (id, committee_id, filed_date, scrape_status) VALUES (?, 1, ?, ?)", (report_id, day, status)
            )
        for receipt in (
            (10201, 100, 5001, "Window", "Zed", "2025-03-01", 400.0, "1 Zed St"),
            (10202, 100, 5001, "Early", "Zed", "2025-01-05", 200.0, "2 Zed St"),
            (10203, 100, 5001, "Stale", "Zed", "2023-05-05", 300.0, "3 Zed St"),
            (10205, 100, 5001, "Next", "Zed", "2027-03-01", 500.0, "5 Zed St"),
        ):
            conn.execute(_INSERT_RECEIPT, receipt)
        conn.execute(
            "INSERT INTO isbe_filed_docs (id, committee_id, doc_name, reporting_period_begin, reporting_period_end, "
            "received_datetime) VALUES (5020, 100, 'D-2 Q4 2026', '2026-10-01', '2026-12-31', '2027-02-15')"
        )
        conn.execute("DROP TABLE IF EXISTS bulk_d2_receipts_recon")
        conn.execute(
            """
            CREATE TABLE bulk_d2_receipts_recon (
                d2_totals_record_id INTEGER, committee_id_sbe INTEGER, committee_name TEXT, filed_doc_id INTEGER,
                d2_total_receipts REAL, d2_total_expenditures REAL, ending_funds_available REAL, is_archived INTEGER,
                receipt_row_count INTEGER, receipts_amount_sum REAL, first_receipt_date TEXT, last_receipt_date TEXT,
                receipts_minus_d2_total REAL
            )
            """
        )
        for record_id, doc_id, name in (
            (9101, 5001, "Zed Window Committee"), (9102, 5013, "Zed Early Committee"),
            (9103, 5012, "Zed Stale Committee"), (9104, 5020, "Zed Next Committee"),
        ):
            conn.execute(
                "INSERT INTO bulk_d2_receipts_recon (d2_totals_record_id, committee_id_sbe, committee_name, filed_doc_id, "
                "is_archived, receipt_row_count, receipts_amount_sum, receipts_minus_d2_total) VALUES (?, 100, ?, ?, 0, 1, 100.0, 50.0)",
                (record_id, name, doc_id),
            )
        conn.commit()
        refresh_analytics_materialized(conn)  # analytics_donor_summary = the unbounded (all-time) donor-key path
    finally:
        conn.close()
    return donor_app


_INSERT_FEC_A = (
    "INSERT INTO fec_schedule_a_contributions (sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name, "
    "contributor_name, contributor_city, contributor_state, contributor_zip, contributor_employer, contributor_occupation, "
    "is_individual, line_number, receipt_type, receipt_type_desc, contribution_receipt_amount, contribution_receipt_date, "
    "two_year_transaction_period, donor_key, load_date, image_number, api_source_identifier) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '11AI', 'IND', 'Individual contribution', ?, ?, ?, ?, '2026-03-02T01:02:03', ?, ?)"
)
_INSERT_FEC_B = (
    "INSERT INTO fec_schedule_b_disbursements (sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name, "
    "recipient_name, recipient_city, recipient_state, recipient_zip, disbursement_description, disbursement_amount, "
    "disbursement_date, two_year_transaction_period, load_date) "
    "VALUES (?, ?, 'H2IL01349', 'JACKSON, JONATHAN', 'C00011111', 'JONATHAN JACKSON FOR CONGRESS', ?, ?, 'IL', ?, ?, ?, ?, ?, '2026-03-02T01:02:03')"
)
_INSERT_FEC_E = (
    "INSERT INTO fec_schedule_e_independent_expenditures (sub_id, cycle, candidate_id, candidate_name, candidate_office, "
    "candidate_office_state, candidate_office_district, support_oppose_indicator, committee_id, committee_name, payee_name, "
    "payee_city, payee_state, payee_zip, expenditure_description, expenditure_amount, expenditure_date, most_recent, load_date) "
    "VALUES (?, ?, 'H2IL01349', 'JACKSON, JONATHAN', 'H', 'IL', '01', ?, ?, ?, ?, ?, 'IL', ?, ?, ?, ?, 1, '2026-03-02T01:02:03')"
)


def _seed_federal_extras(conn) -> None:
    """Rows on both sides of the federal window (2026-01-01..2026-06-30) on top of ``_seed_federal``.

    Every amount is >= 250 so the rows survive the networks' default minimum edge amount (250). All rows are cycle 2026 except
    Jane's 2023 gift, so even the pages pinned to one cycle see the OUT rows.
    Base seed: Jackson (H2IL01349, IL-01, committee C00011111); Jane Donor gave 250 on 2026-01-15 (IN) and 400 on 2023-06-01 (OUT).
    Schedule A added to Jackson: Early Donor 275 on 2025-03-05 (OUT, before the window); Fresh Mailers Inc 300 on 2026-02-10 (IN;
    an organisation); Old Billboards LLC 350 on 2025-09-09 (OUT; an organisation); Late Donor 260 on 2026-09-10 and Late Ads 280 on
    2026-09-15 (OUT, after the window; Late Ads is an organisation).
    A second candidate, Smith (H2IL09777, IL-09, committee C00077777), whose only money is OUT: Spring Donor 325 on 2025-04-10,
    Jane Donor 260 on 2025-05-05 (before the window) and Jane Donor 270 on 2026-09-20 (after it). So IL-09, Smith, Spring Donor and
    Springfield appear only when the window is wide, and Jane reaches Smith only then, from either side.
    Schedule B (Jackson's committee pays): IN Fresh Media LLC 320 on 2026-01-01 (the window's first day); OUT Stale Print Co 330 on
    2025-07-01 and Late Vendor 340 on 2026-09-11.
    Schedule E (for/against Jackson): IN WINDOW PAC -> Fresh Mailers Inc 700 on 2026-06-30 (its last day, support); OUT STALE PAC ->
    Old Billboards LLC 800 on 2025-08-01 (oppose) and LATE PAC -> Late Ads 900 on 2026-09-12 (support). Fresh Mailers Inc and Old
    Billboards LLC also gave in Schedule A, so each is a cross-role organisation, on its own side of the window.
    Hand-computed totals -- window: Schedule A 550 = 250 + 300 (2 donors, 2 contributions, all Jackson); B 320; E 700; combined 1,570.
    All time: Jane 1,180 = 250 + 400 + 260 + 270 in 4 contributions (the last two to Smith); Jackson A 2,115 = 250 + 400 + 275 + 300 +
    350 + 260 + 280 (7 contributions); Smith A 855 = 325 + 260 + 270 (3); A 2,970 (10 contributions, 7 donors); B 990 = 320 + 330 + 340;
    E 2,400 = 700 + 800 + 900; combined 6,360 = 2,970 + 990 + 2,400. Cross-role organisations: Fresh Mailers Inc (IN: gave 300,
    received 700), Old Billboards LLC (gave 350, received 800) and Late Ads (gave 280, received 900): 3 all time, 930 donated.
    """
    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES ('seed-key-9', '2026-02-07', 2026, 'U.S. House', 'H', 'IL-09', '09', 'Republican', 'REP',
                  'Primary', 'Jonathan Smith', 'JONATHAN SMITH', 0, 0, 'test_seed.csv', 9)
        """
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle,
            fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES ('seed-key-9', 'Jonathan Smith', 'U.S. House', 'H', 'IL-09', '09', 'Republican', 'REP',
                  'Primary', 2026, 'H2IL09777', 'SMITH, JONATHAN', 'H', 'IL', '09', 'REP', 'matched', 95.0, 'test')
        """
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_committees (
            candidate_id, committee_id, cycle, committee_name, committee_type, committee_designation,
            committee_designation_full, filing_frequency, committee_party, committee_city, committee_state,
            committee_zip, is_principal
        ) VALUES ('H2IL09777', 'C00077777', 2026, 'SMITH FOR CONGRESS', 'H', 'P', 'Principal campaign committee',
                  'Q', 'REP', 'SPRINGFIELD', 'IL', '62701', 1)
        """
    )
    jackson = ("H2IL01349", "JACKSON, JONATHAN", "C00011111", "JONATHAN JACKSON FOR CONGRESS")
    smith = ("H2IL09777", "SMITH, JONATHAN", "C00077777", "SMITH FOR CONGRESS")
    for sub_id, who, donor, city, zip5, employer, occupation, individual, amount, day, key in (
        ("sub-3", jackson, "Early Donor", "Evanston", "60201", "EARLY CO", "Teacher", 1, 275.0, "2025-03-05", "donor-key-3"),
        ("sub-4", smith, "Spring Donor", "Springfield", "62701", "SPRING CO", "Nurse", 1, 325.0, "2025-04-10", "donor-key-4"),
        ("sub-5", jackson, "Fresh Mailers Inc", "Chicago", "60601", "", "", 0, 300.0, "2026-02-10", "donor-key-5"),
        ("sub-6", jackson, "Old Billboards LLC", "Peoria", "61602", "", "", 0, 350.0, "2025-09-09", "donor-key-6"),
        ("sub-7", smith, "Jane Donor", "Chicago", "60601", "ACME", "Engineer", 1, 260.0, "2025-05-05", "donor-key-1"),
        ("sub-8", jackson, "Late Donor", "Aurora", "60505", "LATE CO", "Clerk", 1, 260.0, "2026-09-10", "donor-key-8"),
        ("sub-9", smith, "Jane Donor", "Chicago", "60601", "ACME", "Engineer", 1, 270.0, "2026-09-20", "donor-key-1"),
        ("sub-10", jackson, "Late Ads", "Aurora", "60505", "", "", 0, 280.0, "2026-09-15", "donor-key-10"),
    ):
        conn.execute(
            _INSERT_FEC_A,
            (sub_id, 2026, *who, donor, city, "IL", zip5, employer, occupation, individual, amount, day, 2026, key,
             f"img-{sub_id}", f"src-{sub_id}"),
        )
    conn.execute(_INSERT_FEC_B, ("b-in-1", 2026, "Fresh Media LLC", "Chicago", "60601", "Digital ads", 320.0, "2026-01-01", 2026))
    conn.execute(_INSERT_FEC_B, ("b-out-1", 2026, "Stale Print Co", "Peoria", "61602", "Yard signs", 330.0, "2025-07-01", 2026))
    conn.execute(_INSERT_FEC_B, ("b-late-1", 2026, "Late Vendor", "Aurora", "60505", "Radio", 340.0, "2026-09-11", 2026))
    conn.execute(
        _INSERT_FEC_E,
        ("e-in-1", 2026, "S", "C00099001", "WINDOW PAC", "Fresh Mailers Inc", "Chicago", "60601", "Mailers", 700.0, "2026-06-30"),
    )
    conn.execute(
        _INSERT_FEC_E,
        ("e-out-1", 2026, "O", "C00099002", "STALE PAC", "Old Billboards LLC", "Peoria", "61602", "Billboards", 800.0, "2025-08-01"),
    )
    conn.execute(
        _INSERT_FEC_E,
        ("e-late-1", 2026, "S", "C00099003", "LATE PAC", "Late Ads", "Aurora", "60505", "Ads", 900.0, "2026-09-12"),
    )


@pytest.fixture
def w_federal_app(federal_app, monkeypatch):
    """federal_app plus ``_seed_federal_extras`` (Schedule A, B and E rows on both sides of the 2026 window).

    The resolver's clock is pinned to 2026-10-26 (as tests/test_candidate_profile.py): under ``period=all`` the overview's Top
    Federal Races candidates are the current cycle's (J-S22), so the all-time control would otherwise age out on 2027-01-01."""
    from datetime import date

    from database.federal_fec import _ensure_missing_donor_identities

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 26)

    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)
    conn = get_db(federal_app.config["DATABASE_PATH"])
    try:
        _seed_federal_extras(conn)
        conn.commit()
        _ensure_missing_donor_identities(conn, cycle=None)
        conn.commit()
    finally:
        conn.close()
    return federal_app


@pytest.fixture
def w_live_app(w_donor_app):
    """w_donor_app (local rows) plus the federal seed and extras, so /live-feed has all four tables to window.

    /live-feed has one window for all four tables, so it uses the federal one (calendar 2026): the local table needs a 2026 row
    to have anything IN -- Zed Late 150 on 2026-01-01, the window's first day (committee 100); every other local receipt is from
    2025 or 2023 (OUT).
    """
    from database.federal_fec import _ensure_missing_donor_identities

    conn = get_db(w_donor_app.config["DATABASE_PATH"])
    try:
        conn.execute(_INSERT_RECEIPT, (10204, 100, 5001, "Late", "Zed", "2026-01-01", 150.0, "4 Zed St"))
        _seed_federal(conn)
        _seed_federal_extras(conn)
        conn.commit()
        _ensure_missing_donor_identities(conn, cycle=None)
        conn.commit()
    finally:
        conn.close()
    return w_donor_app


@pytest.fixture
def w_analytics_app(analytics_app):
    """analytics_app (contributions Jan-Apr 2026, expenditures Jan 2026 + 2024) plus lobbying and 527 graph inputs.

    Window 2026-02-01..2026-03-31 (month-aligned: the time series is monthly). The fixture's January contributions (1,000 + 500 +
    250 + 2,000, four rows) and one added here (Frank Donor, Peoria, 120 on 2026-01-20: a city only January has) are OUT before the
    window, 3,870 in 5 rows; its April contribution (Alice 10,000 on 2026-04-09) is OUT after it.
    Window: 4,200 in 5 contributions (Feb 3,000 + Mar 1,200); all time 18,070.
    Lobbying: Client One (entity 101, registered 2026) is matched to three payees of committee 1001:
      IN  Vendor Two (new expenditure 700 on 2026-03-10)
      OUT Vendor One (its only expenditures are on 2026-01-08) and Vendor Three (new expenditure 800 on 2026-04-25)
    527: Org Window (EIN-IN) spent on 2026-03-15 (IN); Org Early (EIN-OUT) on 2026-01-20 and Org Late (EIN-LATE) on 2026-04-20
    (OUT); each is matched to a committee.
    """
    expenditure = (
        "INSERT INTO bulk_expenditures_clean (committee_id_sbe, payee_last_or_business_name, candidate_name, d2_part_code, "
        "is_archived, amount, expended_date) VALUES (1001, ?, 'Candidate Alpha', '9A', 0, ?, ?)"
    )
    payee_match = (
        "INSERT INTO lobbying_expenditure_matches (source_type, source_id, source_name, payee_name, committee_id_sbe, score) "
        "VALUES ('client', 1, 'Client One', ?, 1001, ?)"
    )
    org = "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, state) VALUES (?, ?, 1, ?, 'IL')"
    spend = "INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, amount, date) VALUES (?, ?, ?, 'Vendor One', ?, ?)"
    committee = (
        "INSERT INTO irs527_committee_matches (ein, org_name, committee_id_sbe, committee_name, score, method) "
        "VALUES (?, ?, ?, ?, 0.9, 'name_state_zip')"
    )
    from database.models import Committee, Contribution, Donor

    conn = get_db(analytics_app.config["DATABASE_PATH"])
    try:
        committee_one = Committee.get_or_create(conn, "Committee One")
        january = conn.execute(
            "SELECT id FROM reports WHERE committee_id = ? AND reporting_period = 'Jan 2026'", (committee_one.id,)
        ).fetchone()["id"]
        frank = Donor.get_or_create(conn, "Frank Donor", "606 Cedar Rd, PEORIA, IL 61602", "frank donor", "606 cedar rd, peoria, il 61602")
        Contribution(
            report_id=january, donor_id=frank.id, amount=120.0, transaction_date="01/20/2026",
            description="January mailer", raw_contributed_by="raw", raw_address="raw address",
        ).save(conn)
        conn.commit()
    finally:
        conn.close()
    return _topup(
        analytics_app,
        (expenditure, ("Vendor Two", 700.0, "2026-03-10")),
        (expenditure, ("Vendor Three", 800.0, "2026-04-25")),
        ("INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'Client One')", ()),
        ("INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (101, 'Entity One', 2026)", ()),
        ("INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (101, 1, 2026)", ()),
        (payee_match, ("Vendor Two", 0.90)),
        (payee_match, ("Vendor One", 0.88)),
        (payee_match, ("Vendor Three", 0.86)),
        (org, ("EIN-IN", 1, "Org Window")),
        (org, ("EIN-OUT", 2, "Org Early")),
        (org, ("EIN-LATE", 3, "Org Late")),
        (spend, (1, "EIN-IN", "Org Window", 500, "2026-03-15")),
        (spend, (2, "EIN-OUT", "Org Early", 400, "2026-01-20")),
        (spend, (3, "EIN-LATE", "Org Late", 300, "2026-04-20")),
        (committee, ("EIN-IN", "Org Window", 10, "Committee Ten")),
        (committee, ("EIN-OUT", "Org Early", 11, "Committee Eleven")),
        (committee, ("EIN-LATE", "Org Late", 12, "Committee Twelve")),
    )


@pytest.fixture
def w_risk_app(w_analytics_app):
    """w_analytics_app plus Hank Donor's gifts to Committee Two, so one anomaly flag falls inside WINDOW_ANALYTICS.

    Committee Two (Dave 2,000 in January, 1,800 in February) gets 300 on 2025-12-15 and 4,200 on 2026-03-12: its March total is
    3.07x the Dec-Feb average of 1,366.67, a monthly_spike dated 2026-03 (IN). The flags dated outside the window are OUT:
    Committee One's April spike (10,000.0 on a 1,423.33 baseline, 2026-04), its large single contribution (Alice's 10,000 on
    2026-04-09) and its undated high_donor_concentration flag (HHI 7,963.69; an undated flag is outside any bounded window).
    """
    from database.models import Committee, Contribution, Donor

    conn = get_db(w_analytics_app.config["DATABASE_PATH"])
    try:
        committee_two = Committee.get_or_create(conn, "Committee Two")
        february = conn.execute(
            "SELECT id FROM reports WHERE committee_id = ? AND reporting_period = 'Feb 2026'", (committee_two.id,)
        ).fetchone()["id"]
        hank = Donor.get_or_create(conn, "Hank Donor", "808 Birch St, ST. LOUIS, MO 63102", "hank donor", "808 birch st, st. louis, mo 63102")
        for amount, day in ((300.0, "12/15/2025"), (4200.0, "03/12/2026")):
            Contribution(
                report_id=february, donor_id=hank.id, amount=amount, transaction_date=day,
                description="Gala tickets", raw_contributed_by="raw", raw_address="raw address",
            ).save(conn)
        conn.commit()
    finally:
        conn.close()
    return w_analytics_app


@pytest.fixture
def w_irs527_app(irs527_app):
    """irs527_app (Test 527 Org) plus expenditures and contributions on both sides of the window.

    Window 2025-02-01..2026-12-31. Base seed: expenditure Recipient Inc 5,000 on 2026-02-10 (IN); contributions Acme Donor
    4,321 on 2026-02-01 (IN) and Old Donor 1,000 on 2023-05-01 (OUT).
    Added OUT rows: expenditures Early Recipient Co 250 (2025-01-10), Stale Recipient LLC 700 (2023-06-15) and Late Recipient Co
    150 (2027-01-20), every recipient matched to a committee at score 0.9; contributions Early Donor 300 (2025-01-12) and
    Late Donor 80 (2027-01-22).
    Window: expenditures 5,000 (1 matched record), contributions 4,321 (1 record).
    All time: expenditures 6,100 = 5,000 + 250 + 700 + 150 (4 matched records); contributions 5,701 = 4,321 + 1,000 + 300 + 80 (4).
    """
    expenditure = (
        "INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, purpose, date) "
        "VALUES (100, '123456789', 'Test 527 Org', ?, 'Peoria', 'IL', ?, ?, ?)"
    )
    match = (
        "INSERT INTO irs527_expenditure_recipient_matches (ein, org_name, recipient_name, matched_type, matched_id, matched_name, score) "
        "VALUES ('123456789', 'Test 527 Org', ?, 'committee', ?, ?, 0.9)"
    )
    contribution = (
        "INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date) "
        "VALUES (100, '123456789', 'Test 527 Org', ?, 'Chicago', 'IL', ?, ?)"
    )
    return _topup(
        irs527_app,
        (expenditure, ("Early Recipient Co", 250, "Early flyers", "2025-01-10")),
        (expenditure, ("Stale Recipient LLC", 700, "Old canvass", "2023-06-15")),
        (expenditure, ("Late Recipient Co", 150, "New year mail", "2027-01-20")),
        (match, ("Recipient Inc", "10", "Committee Ten")),
        (match, ("Early Recipient Co", "11", "Committee Eleven")),
        (match, ("Stale Recipient LLC", "12", "Committee Twelve")),
        (match, ("Late Recipient Co", "13", "Committee Thirteen")),
        (contribution, ("Early Donor", 300, "2025-01-12")),
        (contribution, ("Late Donor", 80, "2027-01-22")),
    )


@pytest.fixture
def w_lobbying_app(w_donor_app):
    """w_donor_app plus a lobbying client whose matched donor (Alice Donor) gave on both sides of the window.

    Window 2025-02-01..2026-12-31. Alice's receipts (base seed): 5,000 to committee 100 on 2025-01-15 (OUT, same cycle), 10,000 to
    committee 200 on 2025-01-20 (OUT), 700 to committee 200 on 2023-03-01 (OUT). Added: 1,500 to committee 200 on 2025-02-01 (IN,
    the window's first day) and 60 to committee 200 on 2027-02-02 (OUT, after it).
    Window: 1,500, all to Friends of Jones. All time: 17,260 = 5,000 + 10,000 + 700 + 1,500 + 60 in 5 contributions, of which
    committee 100 (Citizens for Smith) took 5,000 and committee 200 took 12,260.
    """
    from database.analytics import refresh_analytics_materialized

    _topup(
        w_donor_app,
        (_INSERT_RECEIPT, (10301, 200, 5002, "Donor", "Alice", "2025-02-01", 1500.0, "100 Main St")),
        (_INSERT_RECEIPT, (10302, 200, 5002, "Donor", "Alice", "2027-02-02", 60.0, "100 Main St")),
    )
    conn = get_db(w_donor_app.config["DATABASE_PATH"])
    try:
        refresh_analytics_materialized(conn)
    finally:
        conn.close()
    key = _alice_key(w_donor_app)
    return _topup(
        w_donor_app,
        ("INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Test Entity', 2026)", ()),
        ("INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'Test Client')", ()),
        ("INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2026)", ()),
        ("INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method) "
         "VALUES (1, ?, 'Test Client', 'Alice Donor', 0.95, 'token_set')", (key,)),
    )


@pytest.fixture
def w_openbook_app(openbook_app):
    """openbook_app (CONTRIB VENDOR) plus contributions and warrants on both sides of the window.

    Window 2025-02-01..2026-12-31. Contributions (base): Big Donor 5,000 on 2026-03-15 (IN), Old Donor 100 on 2023-02-01 (OUT);
    added Early Donor 250 on 2025-01-15 and Late Donor 90 on 2027-01-05 (OUT): window 1 contribution, all time 4.
    Warrants added: W-IN-1 7,000 on 2025-02-01 (IN, the window's first day); W-EARLY 4,000 on 2025-01-10, W-OUT-1 9,000 on
    2023-03-20 and W-LATE 2,500 on 2027-01-06 (OUT): window 1 warrant, 7,000; all time 4, 22,500.
    """
    warrant = (
        "INSERT INTO openbook_contract_warrants (openbook_vendor_key, contract_number, fiscal_year, issue_date, payment_amount, "
        "source_url, row_hash) VALUES ('CONTRIB VENDOR', ?, ?, ?, ?, 'https://example.com', ?)"
    )
    return _topup(
        openbook_app,
        ("INSERT INTO openbook_contributions_raw (openbook_vendor_key, contributor_name, recipient_name, contribution_date, amount, "
         "source_url, row_hash) VALUES ('CONTRIB VENDOR', 'Early Donor', 'Gov. Brown', '2025-01-15', 250.0, 'https://example.com', 'hash-c-3')", ()),
        (warrant, ("W-IN-1", 2025, "2025-02-01", 7000.0, "hash-w-1")),
        (warrant, ("W-EARLY", 2025, "2025-01-10", 4000.0, "hash-w-2")),
        (warrant, ("W-OUT-1", 2023, "2023-03-20", 9000.0, "hash-w-3")),
        (warrant, ("W-LATE", 2027, "2027-01-06", 2500.0, "hash-w-4")),
        ("INSERT INTO openbook_contributions_raw (openbook_vendor_key, contributor_name, recipient_name, contribution_date, amount, "
         "source_url, row_hash) VALUES ('CONTRIB VENDOR', 'Late Donor', 'Gov. Green', '2027-01-05', 90.0, 'https://example.com', 'hash-c-4')", ()),
    )


@pytest.fixture
def w_viz_app(tmp_path, monkeypatch):
    """The Viz Lab fixture (committee 9001) with the lab enabled, plus rows for the prototypes that lacked one on a side.

    Window 2025-04-01..2025-06-30. Receipts: Donor Gamma 6,000 on 2025-04-01 (added; the window's first day) and Donor Beta 3,000
    on 2025-06-20 are IN; Donor Alpha 5,000 on 2025-03-10 (before) and Donor Delta 6,000 on 2025-09-05 (added; after) are OUT.
    (network_slice drops donor edges under 5,000, which is why Gamma and Delta are 6,000.) Expenditures: Spring Vendor 400 on
    2025-06-30 (added; the window's last day) is IN; Early Vendor 900 on 2025-03-20 (added) and Media Vendor 1,200 on 2025-07-01
    (base seed; the day after the window) are OUT.
    """
    from database.connection import init_db
    from tests.test_experimental_viz_lab import _seed_state_race_tables
    from webapp.app import create_app

    monkeypatch.setattr("database.analytics.current_election_cycle", lambda today=None: 2026)
    # Pin the time-scope resolver's clock too, so "this cycle" stays 2026 (as tests/test_candidate_profile.py).
    from datetime import date

    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 26)

    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)
    db_path = str(tmp_path / "w_viz.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        _seed_state_race_tables(conn)
        receipt = (
            "INSERT INTO bulk_receipts_clean (committee_id_sbe, first_name, last_or_business_name, address_line_1, address_line_2, "
            "city, state, postal_code, contributed_by, amount, received_date, received_datetime_raw, d2_part_code, filed_doc_id, "
            "is_archived) VALUES (9001, 'Donor', ?, '3 Pine Rd', NULL, 'Peoria', 'IL', '61602', ?, 6000.0, ?, ?, '1A', ?, 0)"
        )
        conn.execute(receipt, ("Gamma", "Donor Gamma", "2025-04-01", "2025-04-01T10:00:00", 7003))
        conn.execute(receipt, ("Delta", "Donor Delta", "2025-09-05", "2025-09-05T10:00:00", 7004))
        conn.executemany("INSERT INTO bulk_d2_totals_clean (filed_doc_id, is_archived) VALUES (?, 0)", [(7003,), (7004,)])
        expenditure = (
            "INSERT INTO bulk_expenditures_clean (committee_id_sbe, payee_last_or_business_name, candidate_name, d2_part_code, "
            "is_archived, amount, expended_date) VALUES (9001, ?, 'Candidate One', '9A', 0, ?, ?)"
        )
        conn.execute(expenditure, ("Early Vendor", 900.0, "2025-03-20"))
        conn.execute(expenditure, ("Spring Vendor", 400.0, "2025-06-30"))
        conn.commit()
    finally:
        conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path, "EXPERIMENTAL_VIZ_LAB_ENABLED": True,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


# ---------------------------------------------------------------------------
# Page tests, local (ISBE) data. Each Page holds the markers next to its test.
# ---------------------------------------------------------------------------
LIVE_FEED = Page(
    "live_feed.html", "/live-feed", window=WINDOW_FEDERAL, probes=(
        # Local table: only Zed Late (2026-01-01, the window's first day) is IN; the 9 other non-archived receipts are 2025, 2023
        # or 2027 (Zed Next).
        Probe("Local Candidate Donations (N)",
              ins=("Local Candidate Donations (1)", "Zed Late"),
              outs=("Local Candidate Donations (10)", "BigCorp", "2025-05-01", "2023-03-01", "Zed Next")),
        # Federal A: Jane 2026-01-15 + Fresh Mailers Inc 2026-02-10 IN; 8 more rows (Jane 2023, Early, Old Billboards, Late Donor,
        # Late Ads, Spring, Jane->Smith twice) OUT.
        Probe("Federal Candidate Donations (N)",
              ins=("Federal Candidate Donations (2)", "Fresh Mailers Inc", "$250.00"),
              outs=("Federal Candidate Donations (10)", "Early Donor", "Old Billboards LLC", "Spring Donor", "Late Donor", "$400.00")),
        Probe("Federal Committee Disbursements (Schedule B) (N)",
              ins=("Federal Committee Disbursements (Schedule B) (1)", "Fresh Media LLC"),
              outs=("Federal Committee Disbursements (Schedule B) (3)", "Stale Print Co", "Late Vendor")),
        Probe("Federal Independent Expenditures (Schedule E) (N)",
              ins=("Federal Independent Expenditures (Schedule E) (1)", "WINDOW PAC"),
              outs=("Federal Independent Expenditures (Schedule E) (3)", "STALE PAC", "LATE PAC")),
    ),
)


def test_live_feed_windows_all_four_tables(w_live_app):
    _check_page(w_live_app, LIVE_FEED)


SEARCH = Page(
    "search.html", "/search", probes=(
        # Filed docs matching "Zed": 5001 received 2025-04-15 (IN); 5013 (2025-01-17), 5012 (2022-04-05), 5020 (2027-02-15) OUT.
        Probe("Filed Doc IDs (N)", via="/search?type=filed_docs&q=Zed",
              ins=("Filed Doc IDs (1)", "Zed Window Committee"),
              outs=("Filed Doc IDs (4)", "Zed Early Committee", "Zed Stale Committee", "Zed Next Committee")),
        # Donor keys matching "Zed": Zed Window 2025-03-01 IN; Zed Early 2025-01-05, Zed Stale 2023-05-05, Zed Next 2027-03-01 OUT.
        Probe("Donor Keys (N)", via="/search?type=donor_keys&q=Zed",
              ins=("Donor Keys (1)", "Zed Window"),
              outs=("Donor Keys (4)", "Zed Early", "Zed Stale", "Zed Next")),
    ),
)


def test_search_windows_filed_docs_and_donor_keys(w_donor_app):
    _check_page(w_donor_app, SEARCH)


LEGACY_COMMITTEE = Page(
    "committees/detail.html", "/committees/1", probes=(
        # Legacy donor 1 -> Legacy Committee: 1,200 on 2025-06-15 IN; 300 (2023-04-01), 90 (2025-01-10), 40 (2027-02-01) OUT; all time 1,630.
        Probe("Total Contributions: N", whole_page=True,
              card=("Total Contributions: $1,200.00", "Total Contributions: $1,630.00")),
        Probe("Recent Contributions", ins=("2025-06-15",), outs=("2025-01-10", "2023-04-01", "2027-02-01")),
        # Legacy reports by filed date: report 1 filed 2025-07-01 IN; 2023-04-20, 2025-01-15 and 2027-02-10 OUT.
        Probe("Reports", ins=("2025-07-01",), outs=("2023-04-20", "2025-01-15", "2027-02-10")),
    ),
)


def test_legacy_committee_windows_total_and_recent_contributions(w_donor_app):
    _check_page(w_donor_app, LEGACY_COMMITTEE)


COMMITTEE_PROFILE = Page(
    "committees/detail_sbe.html", "/committees/sbe/100", probes=(
        # Committee 100 expenditures: Window Printing 1,100 (the window's first day) and Year End Mailers 200 (its last day) IN;
        # Stale Mailers 450 (2023), Acme 3,000 (2025-01-20), Day Before Print 60 (2025-01-31) and New Year Printing 90
        # (2027-01-01) OUT; all time 4,900.
        Probe("Expenditures Summary", ins=("Transactions: 2",), outs=("Transactions: 6",),
              card=("Total expenditures: $1,300.00", "Total expenditures: $4,900.00")),
        Probe("Top Payees", ins=("Window Printing", "Year End Mailers"),
              outs=("Acme Consulting", "Stale Mailers", "Day Before Print", "New Year Printing")),
    ),
)


def test_committee_profile_windows_expenditure_summary_and_top_payees(w_profile_app):
    _check_page(w_profile_app, COMMITTEE_PROFILE)


COMMITTEE_DONORS = Page(
    "committees/donors.html", "/committees/100/donors", probes=(
        # Committee 100 receipts: Bob Donor 2,500 (2025-02-10) and BigCorp 25,000 (2025-05-01) IN; Alice Donor 5,000
        # (2025-01-15), Donor Oldcycle 1,000 (2023-06-01) and Future Donor 33 (2027-01-01) OUT; all time 5 donors, 33,533.
        Probe("Donors", ins=("Bob Donor", "BigCorp"), outs=("Alice Donor", "Donor Oldcycle", "Future Donor"),
              card=("2 unique donors, $27,500.00", "5 unique donors, $33,533.00")),
    ),
)


def test_committee_donors_page_follows_window(w_profile_app):
    _check_page(w_profile_app, COMMITTEE_DONORS)


COMMITTEE_PAYEES = Page(
    "committees/payees.html", "/committees/100/payees", probes=(
        # Same expenditures as the profile's Top Payees: 2 payees and 1,300 in the window, 6 and 4,900 all time.
        Probe("Payees", ins=("Window Printing", "Year End Mailers"),
              outs=("Acme Consulting", "Stale Mailers", "Day Before Print", "New Year Printing"),
              card=("2 unique payees, $1,300.00", "6 unique payees, $4,900.00")),
    ),
)


def test_committee_payees_page_follows_window(w_profile_app):
    _check_page(w_profile_app, COMMITTEE_PAYEES)


LEGACY_DONOR = Page(
    "donors/detail.html", "/donors/1", probes=(
        Probe("Total Contributed: N", whole_page=True,
              card=("Total Contributed: $1,200.00", "Total Contributed: $1,630.00")),
        Probe("Number of Contributions: N", whole_page=True,
              ins=("Number of Contributions: 1",), outs=("Number of Contributions: 4",)),
    ),
)


def test_legacy_donor_summary_card_follows_window(w_donor_app):
    _check_page(w_donor_app, LEGACY_DONOR)


REPORTS = Page(
    "reports/list.html", "/reports/", probes=(
        # Report 1 filed 2025-07-01 (pending) IN; 2 (2023-04-20, scraped), 3 (2025-01-15, error), 4 (2027-02-10, skipped) OUT.
        Probe("N total reports", whole_page=True,
              ins=("1 total reports", "pending: 1"),
              outs=("4 total reports", "error: 1", "scraped: 1", "skipped: 1")),
    ),
)


def test_reports_status_badges_follow_window(w_donor_app):
    _check_page(w_donor_app, REPORTS)


ITEMIZED_RECEIPTS = Page(
    "candidate_finance/itemized.html", "/candidate-finance/1001/100/itemized", probes=(
        # Committee 100: Bob 2,500 (2025-02-10) and BigCorp 25,000 (2025-05-01) IN; Alice 5,000 (2025-01-15), Oldcycle 1,000
        # (2023) and Future Donor 33 (2027-01-01) OUT.
        Probe("Rows: N", whole_page=True,
              ins=("Rows: 2", "Bob Donor", "BigCorp"), outs=("Rows: 5", "Alice Donor", "Oldcycle", "Future Donor")),
    ),
)


def test_candidate_finance_itemized_receipts_follow_window(w_profile_app):
    _check_page(w_profile_app, ITEMIZED_RECEIPTS)


ITEMIZED_EXPENDITURES = Page(
    "candidate_finance/itemized_expenditures.html", "/candidate-finance/1001/100/itemized-expenditures", probes=(
        Probe("Rows: N", whole_page=True,
              ins=("Rows: 2", "Window Printing", "Year End Mailers"),
              outs=("Rows: 6", "Acme Consulting", "Stale Mailers", "Day Before Print", "New Year Printing")),
    ),
)


def test_candidate_finance_itemized_expenditures_follow_window(w_profile_app):
    _check_page(w_profile_app, ITEMIZED_EXPENDITURES)


# ---------------------------------------------------------------------------
# Page tests, 527 / lobbying / OpenBook / Viz Lab
# ---------------------------------------------------------------------------
HOME = Page(
    "index.html", "/", probes=(
        # 527 expenditures matched to a committee: Recipient Inc 5,000 (2026-02-10) IN; Early 250 (2025-01-10), Stale 700 (2023)
        # and Late 150 (2027-01-20) OUT.
        Probe("Matched Dark Money Expenditures", ins=("1 matched records",), outs=("4 matched records",),
              card=("Matched Amount $5,000.00", "Matched Amount $6,100.00")),
        Probe("Total 527 Expenditures", card=("$5,000.00", "$6,100.00")),
        # Contributions: Acme 4,321 IN; Old Donor 1,000 (2023), Early Donor 300 (2025-01-12) and Late Donor 80 (2027-01-22) OUT.
        Probe("Total 527 Contributions Received", ins=("1 contribution records",), outs=("4 contribution records",),
              card=("$4,321.00", "$5,701.00")),
    ),
)


def test_home_527_cards_follow_window(w_irs527_app):
    _check_page(w_irs527_app, HOME)


ORG_527 = Page(
    "irs527/detail.html", "/527/123456789", probes=(
        Probe("Contributions To This Organization (Top N)",
              ins=("Contributions To This Organization (Top 1)", "Acme Donor"),
              outs=("Contributions To This Organization (Top 4)", "Old Donor", "Early Donor", "Late Donor")),
        Probe("Top Expenditure Recipients (N records, N total)",
              ins=("Top Expenditure Recipients (1 records, $5,000.00 total)", "Recipient Inc"),
              outs=("Top Expenditure Recipients (4 records, $6,100.00 total)", "Stale Recipient LLC", "Early Recipient Co",
                    "Late Recipient Co")),
        Probe("Expenditures (Top N)",
              ins=("Expenditures (Top 1)", "Recipient Inc"),
              outs=("Expenditures (Top 4)", "Stale Recipient LLC", "Early Recipient Co", "Late Recipient Co")),
    ),
)


def test_527_org_tables_follow_window(w_irs527_app):
    _check_page(w_irs527_app, ORG_527)


ORG_527_CONTRIBUTORS = Page(
    "irs527/contributors.html", "/527/123456789/contributors", probes=(
        # Acme Donor 4,321 (2026-02-01) IN; Old Donor 1,000 (2023-05-01), Early Donor 300 (2025-01-12) and Late Donor 80
        # (2027-01-22) OUT; all time 4 contributors, 5,701.
        Probe("Contributors", ins=("Acme Donor",), outs=("Old Donor", "Early Donor", "Late Donor"),
              card=("1 unique contributor, $4,321.00", "4 unique contributors, $5,701.00")),
    ),
)


def test_527_contributors_page_follows_window(w_irs527_app):
    _check_page(w_irs527_app, ORG_527_CONTRIBUTORS)


ORG_527_RECIPIENTS = Page(
    "irs527/recipients.html", "/527/123456789/recipients", probes=(
        # Recipient Inc 5,000 (2026-02-10) IN; Early Recipient Co 250 (2025-01-10), Stale Recipient LLC 700 (2023-06-15) and
        # Late Recipient Co 150 (2027-01-20) OUT; all time 4 recipients, 6,100.
        Probe("Expenditure Recipients", ins=("Recipient Inc",),
              outs=("Early Recipient Co", "Stale Recipient LLC", "Late Recipient Co"),
              card=("1 unique recipient, $5,000.00", "4 unique recipients, $6,100.00")),
    ),
)


def test_527_recipients_page_follows_window(w_irs527_app):
    _check_page(w_irs527_app, ORG_527_RECIPIENTS)


DARK_MONEY = Page(
    "irs527/dark_money.html", "/527/dark-money", probes=(
        Probe("527 Funding Sources", ins=("Acme Donor", "Unique Contributors 1"),
              outs=("Old Donor", "Early Donor", "Late Donor", "Unique Contributors 4"),
              card=("Total Contributed $4,321.00", "Total Contributed $5,701.00")),
        # The match table is tagged on the subtitle above it; each match shows only when its expenditure falls in the window.
        Probe("527 organization expenditures matched to IL committees and candidates (N matches)", whole_page=True,
              ins=("Recipient Inc", "Committee Ten"),
              outs=("Early Recipient Co", "Stale Recipient LLC", "Late Recipient Co", "Committee Eleven", "Committee Twelve",
                    "Committee Thirteen")),
    ),
)


def test_527_dark_money_page_follows_window(w_irs527_app):
    _check_page(w_irs527_app, DARK_MONEY)


# Alice Donor gave 1,500 to committee 200 on 2025-02-01 (IN) and 5,000 / 10,000 / 700 / 60 outside the window (17,260 all time).
LOBBYING_MONEY = (
    Probe("Money Destinations",
          ins=("Friends of Jones", "Contributions: 1", "First donation: 2025-02-01"),
          outs=("Citizens for Smith", "Contributions: 5", "First donation: 2023-03-01", "Last donation: 2027-02-02"),
          card=("Matched donor dollars: $1,500.00", "Matched donor dollars: $17,260.00")),
)
LOBBYING_ENTITY = Page("lobbying/entity_detail.html", "/lobbying/1", LOBBYING_MONEY)
LOBBYING_CLIENT = Page("lobbying/client_detail.html", "/lobbying/client/1", LOBBYING_MONEY)


def test_lobbying_entity_money_destinations_follow_window(w_lobbying_app):
    _check_page(w_lobbying_app, LOBBYING_ENTITY)


def test_lobbying_client_money_destinations_follow_window(w_lobbying_app):
    _check_page(w_lobbying_app, LOBBYING_CLIENT)


LOBBYING_FLOWS = Page(
    "lobbying/flows.html", "/lobbying/flows", probes=(
        # The Sankey is drawn from /lobbying/flows/data (the page only carries the scope into that URL).
        Probe("Matched lobbying clients to Illinois committees via donor overlaps.", via="/lobbying/flows/data",
              ins=('"value": 1500.0', "Friends of Jones"), outs=('"value": 12260.0', "Citizens for Smith")),
    ),
)


def test_lobbying_flows_data_follows_window(w_lobbying_app):
    _check_page(w_lobbying_app, LOBBYING_FLOWS)


OPENBOOK_LIST = Page(
    "openbook/list.html", "/openbook/", probes=(
        # CONTRIB VENDOR contributions: Big Donor 2026-03-15 IN; Early Donor 2025-01-15, Old Donor 2023-02-01, Late Donor 2027-01-05 OUT.
        # The card heading carries the scope tag, so the label sits between the title and the value ("Custom range" / "All time").
        Probe("Contributions", level="h3", card=("Contributions Custom range 1", "Contributions All time 4")),
    ),
)


def test_openbook_list_contributions_card_follows_window(w_openbook_app):
    _check_page(w_openbook_app, OPENBOOK_LIST)


OPENBOOK_VENDOR = Page(
    "openbook/detail.html", "/openbook/CONTRIB%20VENDOR", probes=(
        Probe("Warrant Rows", card=("1 $7,000.00 total payments", "4 $22,500.00 total payments")),
        Probe("Contract Warrant Details", ins=("W-IN-1",), outs=("W-EARLY", "W-OUT-1", "W-LATE")),
    ),
)


def test_openbook_vendor_warrants_follow_window(w_openbook_app):
    _check_page(w_openbook_app, OPENBOOK_VENDOR)


VIZ_LAB = Page(
    "experimental/viz_lab.html", "/experimental/viz-lab", window="date_from=2025-04-01&date_to=2025-06-30", probes=(
        # All three draw from /experimental/viz-lab/data/<key>; the page only forwards the scope to it.
        Probe("Cumulative Inflow by Top Races (cumulative_inflow)", via="/experimental/viz-lab/data/cumulative_inflow",
              ins=('"month": "2025-04"', '"month": "2025-06"'), outs=('"month": "2025-03"', '"month": "2025-09"')),
        Probe("Payee Dominance Across Races (payee_dominance)", via="/experimental/viz-lab/data/payee_dominance",
              ins=("Spring Vendor",), outs=("Early Vendor", "Media Vendor")),
        Probe("Constrained Network Slice (network_slice)", via="/experimental/viz-lab/data/network_slice",
              ins=('"label": "Donor Gamma"',), outs=('"label": "Donor Alpha"', '"label": "Donor Delta"')),
    ),
)


def test_viz_lab_prototype_payloads_follow_window(w_viz_app):
    _check_page(w_viz_app, VIZ_LAB)


# ---------------------------------------------------------------------------
# Page tests, federal (FEC) data. Window = 2026-01-01..2026-06-30; the OUT rows are 2025 and 2023 (before it) and September 2026
# (after it), all but Jane's 2023 gift in cycle 2026 (see _seed_federal_extras for the hand-computed totals).
# ---------------------------------------------------------------------------
def _jane(app) -> str:
    return quote(_jane_entity_key(app), safe="")


FED_OVERVIEW = Page(
    "federal_finance/overview.html", "/federal-finance/", window=WINDOW_FEDERAL, probes=(
        # IL-01: contributions 550 and outside spending (Schedule E, dated by expenditure_date) 700 in the window; 2,115 and 2,400
        # all time. IL-09 (Smith) has 855 all time, none in the window.
        Probe("Top Federal Races", ins=("U.S. House - IL-01", "$550.00", "$700.00"), outs=("$2,115.00", "$2,400.00", "$855.00")),
        # F1 (closed in S-W6b): a race with no Schedule A / E money in the window is not listed, so Smith's IL-09 race (money only
        # in 2025 and late 2026) is out under the window and back (at $855.00) all time.
        Probe("Top Federal Races", outs=("IL-09",)),
        Probe("Top Donors (Network)", ins=("Fresh Mailers Inc", "Jane Donor"),
              outs=("Spring Donor", "Early Donor", "Old Billboards LLC", "Late Donor", "$1,180.00")),
        Probe("Top Candidate Nodes", ins=("JACKSON, JONATHAN", "$550.00"), outs=("SMITH, JONATHAN", "$2,115.00")),
        Probe("Geographic Snapshot - States", ins=("IL $550.00",), outs=("IL $2,970.00",)),
        Probe("Geographic Snapshot - Cities", ins=("Chicago IL $550.00",), outs=("Evanston", "Peoria", "Springfield", "Aurora")),
    ),
)


def test_federal_overview_tables_follow_window(w_federal_app):
    _check_page(w_federal_app, FED_OVERVIEW)


FED_NETWORKS = Page(
    "federal_finance/networks.html", "/federal-finance/networks", window=WINDOW_FEDERAL, probes=(
        # The graph is embedded as JS (const federalGraph = {...}); the flows table is built from the same payload in the browser.
        Probe("Federal Donor-Candidate Network", ins=('"label": "Fresh Mailers Inc"', '"weighted_degree": 550.0'),
              outs=('"label": "Early Donor"', '"label": "Late Donor"', '"label": "SMITH, JONATHAN"', '"weighted_degree": 2115.0')),
        Probe("Ranked Donor-Candidate Flows", whole_page=True, ins=('"label": "Jane Donor"',),
              outs=('"label": "Spring Donor"', '"label": "Old Billboards LLC"', '"label": "Late Donor"')),
    ),
)


def test_federal_networks_graph_and_flows_follow_window(w_federal_app):
    _check_page(w_federal_app, FED_NETWORKS)


FED_DONOR_INTELLIGENCE = Page(
    "federal_finance/donor_intelligence.html", "/federal-finance/donor-intelligence", window=WINDOW_FEDERAL, probes=(
        Probe("Donor Segmentation", ins=("Donors analyzed: 2",), outs=("Donors analyzed: 7", "$1,180.00")),
        # Window: one cluster of two donors, weight 550. All time Jane links Jackson's donors to Smith's, so it is ONE cluster of
        # 7 donors and 2 candidates, weight 2,970; just the September rows (an unbounded window) give two clusters, 840 and 270.
        Probe("Donor Network Clustering", ins=("Clusters found: 1", "Fresh Mailers Inc 2 1 2 $550.00"), outs=("$2,970.00",)),
        Probe("Community Treemap / Sunburst", ins=('"cluster_count": 1', '"total_weight": 550.0'),
              outs=('"donor_name": "Late Ads"', '"total_weight": 2970.0')),
    ),
)


def test_federal_donor_intelligence_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_DONOR_INTELLIGENCE)


FED_MONEY_FLOW = Page(
    "federal_finance/money_flow.html", "/federal-finance/money-flow", window=WINDOW_FEDERAL, probes=(
        # Schedule A 550 / B 320 / E 700 in the window; 2,970 / 990 / 2,400 all time.
        Probe("Layer Summary", ins=("A total: $550.00", "B total: $320.00", "E total: $700.00"),
              outs=("A total: $2,970.00", "B total: $990.00", "E total: $2,400.00"),
              card=("Combined: $1,570.00", "Combined: $6,360.00")),
        Probe("Top Multi-layer Flows", ins=("WINDOW PAC", "Fresh Media LLC"),
              outs=("STALE PAC", "Stale Print Co", "LATE PAC", "Late Vendor")),
        Probe("Cross-Role Organizations (Donor and Payee)", ins=("1 matched organizations", "Fresh Mailers Inc"),
              outs=("3 matched organizations", "Old Billboards LLC", "Late Ads")),
    ),
)


def test_federal_money_flow_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_MONEY_FLOW)


FED_INFLUENCE = Page(
    "federal_finance/influence.html", "/federal-finance/influence", window=WINDOW_FEDERAL, probes=(
        Probe("Top Donors by Influence", ins=("Fresh Mailers Inc", "Jane Donor"),
              outs=("Spring Donor", "Early Donor", "Old Billboards LLC", "Late Donor")),
        Probe("Top Candidates by Influence", ins=("JACKSON, JONATHAN",), outs=("SMITH, JONATHAN",)),
    ),
)


def test_federal_influence_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_INFLUENCE)


FED_FOLLOW_THE_MONEY = Page(
    "federal_finance/follow_the_money.html", "/federal-finance/follow-the-money?follow_donor_key={jane}", window=WINDOW_FEDERAL, probes=(
        # Picker amounts are the donor's window total: Jane 250 (+ 400 in 2023, 260 to Smith and 270 in September outside), 1,180 all time.
        Probe("", whole_page=True,  # the donor picker: its tag sits on the </select> line, which holds no text
              ins=("Jane Donor (IL) — $250", "Fresh Mailers Inc (IL) — $300"),
              outs=("Jane Donor (IL) — $1,180", "Spring Donor", "Early Donor", "Old Billboards LLC", "Late Donor")),
        Probe("Donors: N", whole_page=True,  # the graph summary line
              ins=("Full network: 4 nodes | 3 edges",), outs=("Full network: 11 nodes | 10 edges",)),
        # Jane reaches Smith only through the 2025-05-05 gift (OUT).
        Probe("Reachable Candidates", level="h3", ins=("JACKSON, JONATHAN",), outs=("SMITH, JONATHAN",)),
        Probe("Reachable Committees", level="h3", ins=("JONATHAN JACKSON FOR CONGRESS",), outs=("SMITH FOR CONGRESS",)),
    ),
)


def test_federal_follow_the_money_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_FOLLOW_THE_MONEY, jane=_jane(w_federal_app))


FED_GEOGRAPHY = Page(
    "federal_finance/geography.html", "/federal-finance/geography", window=WINDOW_FEDERAL, probes=(
        Probe("Top States", ins=("IL $550.00",), outs=("IL $2,970.00",)),
        Probe("Top Cities", ins=("Chicago IL $550.00",), outs=("Evanston", "Peoria", "Springfield", "Aurora")),
        Probe("Race Geographic Concentration", ins=("U.S. House - IL-01 $550.00",), outs=("IL-09",)),
    ),
)


def test_federal_geography_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_GEOGRAPHY)


FED_GEO_DRILLDOWN = Page(
    "federal_finance/geo_drilldown.html", "/federal-finance/geo-drilldown?geo_type=state&geo_value=IL", window=WINDOW_FEDERAL, probes=(
        Probe("| Detail rows: N", whole_page=True,
              ins=("Fresh Mailers Inc", "Jane Donor"), outs=("Spring Donor", "Early Donor", "Old Billboards LLC", "Late Donor"),
              card=("Total amount: $550.00", "Total amount: $2,970.00")),
    ),
)


def test_federal_geo_drilldown_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_GEO_DRILLDOWN)


FED_MATCHED_PROFILE = Page(
    "federal_finance/match_profile.html", "/federal-finance/matches/{jane}/no-local-key", window=WINDOW_FEDERAL, probes=(
        Probe("Federal Summary", ins=("Contributions 1", "Candidates 1"), outs=("Contributions 4", "Candidates 2"),
              card=("Total Amount $250.00", "Total Amount $1,180.00")),
        Probe("Federal Contributions", ins=("2026-01-15",), outs=("2025-05-05", "2023-06-01", "2026-09-20")),
    ),
)


def test_federal_matched_donor_profile_follows_window(w_federal_app):
    _check_page(w_federal_app, FED_MATCHED_PROFILE, jane=_jane(w_federal_app))


FED_DONOR = Page(
    "federal_finance/donor_detail.html", "/federal-finance/donors/{jane}", window=WINDOW_FEDERAL, probes=(
        Probe("Name Variants", ins=("Jane Donor 1 $250.00",), outs=("Jane Donor 4 $1,180.00",)),
    ),
)


def test_federal_donor_name_variants_follow_window(w_federal_app):
    _check_page(w_federal_app, FED_DONOR, jane=_jane(w_federal_app))


FED_COMMITTEE_RECEIPTS = Page(
    "federal_finance/committee_receipts.html", "/federal-finance/committees/C00011111/receipts", window=WINDOW_FEDERAL, probes=(
        Probe("Itemized Receipts (Schedule A)", ins=("Fresh Mailers Inc", "Jane Donor", "$250.00"),
              outs=("Early Donor", "Old Billboards LLC", "Late Donor", "2023-06-01", "$400.00")),
    ),
)


def test_federal_committee_receipts_follow_window(w_federal_app):
    _check_page(w_federal_app, FED_COMMITTEE_RECEIPTS)


FED_RACE_OUTSIDE_SPENDING = Page(
    "federal_finance/race_outside_spending.html", "/federal-finance/races/H/01/outside-spending", window=WINDOW_FEDERAL, probes=(
        # This page is always one cycle (2026); "all time" there still shows both 2026-cycle rows, so the control works.
        Probe("Total Schedule E Amount: N", whole_page=True,
              card=("Total Schedule E Amount: $700.00", "Total Schedule E Amount: $2,400.00")),
        Probe("Transaction Count: N", whole_page=True, ins=("Transaction Count: 1",), outs=("Transaction Count: 3",)),
        # WINDOW PAC's 2026-06-30 is the newest IN row; LATE PAC's 2026-09-12 is the newest of all.
        Probe("Latest Expenditure Date: N", whole_page=True,
              ins=("Latest Expenditure Date: 2026-06-30",), outs=("Latest Expenditure Date: 2026-09-12",)),
        Probe("Top IE Committees", ins=("WINDOW PAC",), outs=("STALE PAC", "LATE PAC")),
        Probe("Top Payees", ins=("Fresh Mailers Inc",), outs=("Old Billboards LLC", "Late Ads")),
        Probe("Independent Expenditures (Schedule E)", ins=("Showing 1 of 1 rows", "WINDOW PAC"),
              outs=("Showing 3 of 3 rows", "STALE PAC", "LATE PAC", "2025-08-01", "2026-09-12")),
    ),
)


def test_federal_race_outside_spending_follows_window(w_federal_app, monkeypatch):
    # The race page always queries one election cycle (the current one unless the scope is a cycle preset), so pin it.
    monkeypatch.setattr("webapp.routes.federal_finance.current_cycle", lambda today=None: 2026)
    _check_page(w_federal_app, FED_RACE_OUTSIDE_SPENDING)


FED_CANDIDATE = Page(
    "federal_finance/detail.html", "/federal-finance/H2IL01349", window=WINDOW_FEDERAL, probes=(
        Probe("Recent Disbursements (Schedule B)", ins=("Fresh Media LLC",), outs=("Stale Print Co", "Late Vendor")),
        Probe("Independent Expenditures (Schedule E)", ins=("WINDOW PAC",), outs=("STALE PAC", "LATE PAC")),
    ),
)


def test_federal_candidate_disbursements_and_ies_follow_window(w_federal_app):
    _check_page(w_federal_app, FED_CANDIDATE)


# ---------------------------------------------------------------------------
# Page tests, analytics. Window 2026-02-01..2026-03-31: the fixture's January 2026 contributions (3,870 in five rows with Frank
# Donor's) are OUT before it and its April one (Alice 10,000) after it. Window total 4,200 (IL 2,400 + MO 1,800); all time 18,070
# (IL 14,270 + MO 3,800). These pages show their heavy sections only with load_mode=full.
# ---------------------------------------------------------------------------
AN_OVERVIEW = Page(
    "analytics/overview.html", "/analytics/?load_mode=full&sync_full=1", window=WINDOW_ANALYTICS, probes=(
        Probe("Trend Visuals", block="analytics-overview-time-series",
              ins=('"month": "2026-02"', '"month": "2026-03"'), outs=('"month": "2026-01"', '"month": "2026-04"')),
        Probe("Time-Series Preview", ins=("2026-02 $3,000.00", "2026-03 $1,200.00"),
              outs=("2026-01", "2026-04", "$3,870.00", "$10,000.00")),
        # Anomaly flags: 3 over all time (outliers such as Alice's 10,000 in April), none among the five window contributions;
        # city rows 5 over all time (Peoria is January-only), 4 in the window. The other counts are the same in both scopes;
        # asserting them IN keeps "the window blanks every card" from passing.
        Probe("Full-Mode Preview",
              ins=("Network nodes: 6", "Concentration rows: 2", "State rows: 2", "City rows: 4"), outs=("City rows: 5",),
              card=("Anomaly flags: 0", "Anomaly flags: 3")),
    ),
)


def test_analytics_overview_trend_preview_and_card_follow_window(w_analytics_app):
    _check_page(w_analytics_app, AN_OVERVIEW)


AN_NETWORKS = Page(
    "analytics/networks.html", "/analytics/networks?load_mode=full", window=WINDOW_ANALYTICS, probes=(
        # Alice -> Committee One 1,700 = 800 + 900 (all time 12,700 with her January 1,000 and April 10,000); Dave -> Committee Two
        # 1,800 (3,800 with 2,000 in January).
        Probe("Network Graph Analytics", ins=('"weight": 1700.0', '"weight": 1800.0'), outs=('"weight": 12700.0', '"weight": 3800.0')),
        # Client One's payee edges: Vendor Two (spent 2026-03-10) IN; Vendor One (spent only 2026-01-08), Vendor Three (2026-04-25) OUT.
        Probe("Lobbying Bridge tab", block="lobbying-graph-data", ins=('"label": "Vendor Two"',),
              outs=('"label": "Vendor One"', '"label": "Vendor Three"')),
        # Org Window spent 2026-03-15 (IN); Org Early 2026-01-20 and Org Late 2026-04-20 (OUT).
        Probe("527 Dark Money tab", block="ecosystem-527-data", ins=('"label": "Org Window"',),
              outs=('"label": "Org Early"', '"label": "Org Late"')),
    ),
)


def test_analytics_networks_graphs_follow_window(w_analytics_app):
    _check_page(w_analytics_app, AN_NETWORKS)


AN_DONORS = Page(
    "analytics/donors.html", "/analytics/donors?load_mode=full", window=WINDOW_ANALYTICS, probes=(
        Probe("Donor Concentration Metrics", ins=("Committee One $2,400.00", "Committee Two $1,800.00"),
              outs=("Committee One $14,270.00", "Committee Two $3,800.00")),
    ),
)


def test_analytics_donor_concentration_follows_window(w_analytics_app):
    _check_page(w_analytics_app, AN_DONORS)


AN_RISK = Page(
    "analytics/risk.html", "/analytics/risk?load_mode=full", window=WINDOW_ANALYTICS, probes=(
        # One flag inside the window (Committee Two's 2026-03 spike, 4,200.0); three outside it (see w_risk_app).
        Probe("Risk Distribution Views", ins=('"event_date": "2026-03"', '"value": 4200.0'),
              outs=('"event_date": "2026-04"', '"event_date": "2026-04-09"', '"flag_type": "high_donor_concentration"')),
        Probe("Risk and Anomaly Flags", ins=("monthly_spike", "Committee Two", "4200.0"),
              outs=("2026-04", "10000.0", "7963.69", "large_single_contribution", "high_donor_concentration")),
    ),
)


def test_analytics_risk_flags_follow_window(w_risk_app):
    _check_page(w_risk_app, AN_RISK)


AN_GEOGRAPHY = Page(
    "analytics/geography.html", "/analytics/geography?load_mode=full", window=WINDOW_ANALYTICS, probes=(
        Probe("Interactive Geo Views", block="analytics-geo-summary", ins=('"total_amount": 2400.0',),
              outs=('"total_amount": 14270.0', '"city": "Peoria"')),
        Probe("Time-Series Intelligence", ins=("2026-02 $3,000.00", "2026-03 $1,200.00"),
              outs=("2026-01", "2026-04", "$3,870.00", "$10,000.00")),
        Probe("Geospatial Summary - States", ins=("IL $2,400.00", "MO $1,800.00"), outs=("IL $14,270.00", "MO $3,800.00")),
        Probe("Geospatial Summary - Cities", ins=("Chicago IL $1,700.00",),
              outs=("Chicago IL $12,950.00", "Springfield IL $800.00", "Peoria IL $120.00")),
    ),
)


def test_analytics_geography_widgets_follow_window(w_analytics_app):
    _check_page(w_analytics_app, AN_GEOGRAPHY)


AN_GEO_DRILLDOWN = Page(
    "analytics/geo_drilldown.html", "/analytics/geo-drilldown?geo_type=state&geo_value=IL&load_mode=full", window=WINDOW_ANALYTICS, probes=(
        # Eve Donor gave 250 on 2026-01-12 and Frank Donor 120 on 2026-01-20 (OUT); IL window subtotal 2,400 = Alice 1,700 + Carol
        # 400 + Bob 300, all time 14,270.
        Probe("| Detail rows: N", whole_page=True,
              ins=("Carol Donor", "Bob Donor"), outs=("Eve Donor", "Frank Donor"),
              card=("Page subtotal: $2,400.00", "Page subtotal: $14,270.00")),
    ),
)


def test_analytics_geo_drilldown_follows_window(w_analytics_app):
    _check_page(w_analytics_app, AN_GEO_DRILLDOWN)


AN_RELATIONSHIPS = Page(
    "analytics/relationships.html", "/analytics/relationships?load_mode=full", window=WINDOW_ANALYTICS, probes=(
        Probe("Lobbying Influence Graph", block="lobbying-influence-data", ins=('"label": "Vendor Two"',),
              outs=('"label": "Vendor One"', '"label": "Vendor Three"')),
        Probe("527 Ecosystem Graph", block="ecosystem-527-data", ins=('"label": "Org Window"',),
              outs=('"label": "Org Early"', '"label": "Org Late"')),
    ),
)


def test_analytics_relationship_graphs_follow_window(w_analytics_app):
    _check_page(w_analytics_app, AN_RELATIONSHIPS)


# ---------------------------------------------------------------------------
# Accounting: the harness and the registry must agree.
# ---------------------------------------------------------------------------
_ALL_PAGES = [value for value in list(globals().values()) if isinstance(value, Page)]
_PROBED = {(page.template, probe.heading, probe.ordinal) for page in _ALL_PAGES for probe in page.probes}

# A `window` row is covered by an existing page test when its covered_by cites one with no [iw]/[at]/[fl] marker (rows in and
# out of the window, or output compared across scopes -- the registry's own definition). These cite one next to a feeder test.
_COVERED_NEXT_TO_FEEDER_TEST = {
    ("federal_finance/detail.html", "Top Donors", 1),
    ("federal_finance/detail.html", "Recent Contributions", 1),
    ("federal_finance/donor_detail.html", "Candidates Donated To: N", 1),
    ("federal_finance/donor_detail.html", "Committees: N", 1),
    ("federal_finance/donor_detail.html", "Total Contributions: N", 1),
    ("federal_finance/donor_detail.html", "Total Amount: N", 1),
    ("federal_finance/donor_detail.html", "Date Range: N to N", 1),
}


def _covered_by_an_existing_page_test(widget) -> bool:
    if (widget.template, widget.heading, widget.ordinal) in _COVERED_NEXT_TO_FEEDER_TEST:
        return True
    return bool(widget.covered_by) and not any(marker in widget.covered_by for marker in ("[iw", "[at", "[fl"))


def _rule_pattern(rule: str) -> re.Pattern:
    """Flask URL rule -> regex (``<path:x>`` matches slashes, ``<x>`` does not)."""
    pieces = re.split(r"(<[^>]+>)", rule)
    return re.compile("".join(
        (".+" if piece.startswith("<path:") else "[^/]+") if piece.startswith("<") else re.escape(piece) for piece in pieces
    ))


def test_every_probe_targets_a_registered_window_widget_on_its_own_route():
    registry = by_template()
    problems = []
    for page in _ALL_PAGES:
        path = page.url.split("?")[0].format(jane="x")
        for probe in page.probes:
            row = registry.get((page.template, probe.heading, probe.ordinal))
            if row is None:
                problems.append(f"{page.url} [{probe.heading}#{probe.ordinal}]: no such registry row")
            elif row.status != "window":
                problems.append(f"{page.url} [{probe.heading}#{probe.ordinal}]: registry status is {row.status!r}")
            elif not _rule_pattern(row.url).fullmatch(path):
                problems.append(f"{page.url} [{probe.heading}#{probe.ordinal}]: registry route is {row.url!r}")
    assert not problems, "\n" + "\n".join(problems)


def test_every_window_widget_is_probed_or_covered_by_an_existing_page_test():
    """Process rule: a widget registered as `window` needs a probe here or a cited page test without [iw]/[at]/[fl]."""
    unaccounted = [
        f"{widget.template}  {widget.heading!r}  ordinal={widget.ordinal}"
        for widget in WIDGETS
        if widget.status == "window"
        and (widget.template, widget.heading, widget.ordinal) not in _PROBED
        and not _covered_by_an_existing_page_test(widget)
    ]
    assert not unaccounted, (
        f"{len(unaccounted)} `window` registry row(s) have no behaviour probe in tests/test_scope_widgets.py and no cited page test "
        "(add a Probe to a Page, or fill covered_by with a test that renders rows inside and outside the window):\n  "
        + "\n  ".join(unaccounted)
    )


# `partial` / `mismatch` rows are recorded, not asserted: each shows as an expected failure until its fix (S-W5 / S-W6) flips the
# registry row to `window` (which moves it out of this list and into the accounting test above); `unscoped` pages have no window.
def _recorded_rows():
    for widget in WIDGETS:
        if widget.status in ("partial", "mismatch"):
            mark = pytest.mark.xfail(strict=False, reason=f"registry status {widget.status} -- fixed by S-W5/S-W6")
        elif widget.status == "unscoped":
            mark = pytest.mark.skip(reason="outside the time-scope model (suggestion E8, decision J-S11): no bar, nothing to window")
        else:
            continue
        yield pytest.param(widget, marks=mark, id=f"{widget.status}:{widget.template}:{widget.heading[:48]}#{widget.ordinal}")


@pytest.mark.parametrize("widget", list(_recorded_rows()))
def test_recorded_widget_window_is_not_yet_held(widget):
    assert widget.status == "window", f"{widget.template} {widget.heading!r}: {widget.note or 'see docs/date_filtered_widgets.md'}"
