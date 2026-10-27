"""Step S-W6a -- axis labels, feeder windows, partial-card tags, scope carry and JSON-endpoint scope.

Plan .claude/plans/profiles-races-dated-widgets.md section 6.2 row S-W6 (the part that does not touch
search, the 527 pages or the committee templates), decisions J-S14 and J-S11; suggestions E2-E6 and A6 in
docs/architecture_suggestions_2026-10-26.md. Written BEFORE the implementation: every behaviour test below
fails today, except the ones named "guard" (they pin a neighbour the fix must not break).

What is pinned (header texts are matched on reader text; markers are whole tokens):

  E3  axis labels. The scope bar prints ``<span class="time-scope-axis">``. /analytics/risk must stop saying
      "by filing date" (its SQL windows the contribution date); the two D-2 reconciliation pages must say
      "by reporting period end" (their SQL windows isbe_filed_docs.reporting_period_end); the legacy committee
      page keeps its "by transaction date" bar and its Reports section says "by filing date".
  E2  feeders. Networks Vendor tab payload is windowed; the State-Federal tab panel carries a FIXED "All time"
      tag; federal candidate detail Transfer Chains and Cross-Role Organizations follow a custom window.
  E4  partial cards. FEC cards on / and /candidates follow the window (the candidates card is a FIXED
      "<cycle> Cycle" tag); the D-2 totals fallback windows isbe_filed_docs.reporting_period_end; the matching
      page's local side and the OpenBook list's Contracts / Total Award cards carry FIXED "All time" tags;
      lobbying client counts window lobbying_entity_clients.reg_year; "(cycle)" marks the cycle-based
      Top State Races columns.
  E5  JSON endpoints. Analytics "JSON endpoint" anchors resolve (through resolve_time_scope) to the page's
      window and never carry an empty date_from= / date_to=; both focus-sankey endpoints accept period=;
      analytics_networks.js forwards the page window.
  E6  scope carry on the lobbying entity / client pages.
  Also: the three fixtures named in the S-RB follow-up freeze the webapp.utils.time_scope clock, and the
  prewarm keeps feeding the entries a default request reads (the two traps a lazy E3 / E2 fix falls into).

Fixtures are the S-W2 harness's (``w_*`` in tests/test_scope_widgets.py), topped up in this file; every
hand-computed value sits in a comment beside the rows that produce it.
"""
from __future__ import annotations

import dataclasses
import gc
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from database.connection import get_db
from webapp import cache_backend
from webapp.utils.time_scope import resolve_time_scope

# Imported at collection time on purpose (see tests/test_scope_widgets.py): webapp/routes/analytics.py binds
# get_db at first import, which would otherwise happen inside the first test that builds an app.
import webapp.routes.analytics as analytics_routes  # noqa: E402
from tests.test_analytics_features import analytics_app  # noqa: E402,F401
from tests.test_experimental_viz_lab import app as viz_lab_app  # noqa: E402,F401
from tests.test_profile_scope import (  # noqa: E402,F401  (donor_app / federal_app / openbook_app: the w_* fixtures' bases)
    _alice_key,
    donor_app,
    federal_app,
    openbook_app,
    profile_app,
)
from tests.test_scope_widgets import (  # noqa: E402,F401
    ALL_TIME,
    WINDOW_ANALYTICS,
    WINDOW_FEDERAL,
    WINDOW_ISBE,
    _INSERT_FEC_A,
    _INSERT_FEC_B,
    _INSERT_FEC_E,
    _flat,
    _has,
    _topup,
    w_analytics_app,
    w_donor_app,
    w_federal_app,
    w_lobbying_app,
    w_openbook_app,
    w_viz_app,
)

ROOT = Path(__file__).resolve().parents[1]
WINDOW_D2 = "date_from=2025-04-01&date_to=2025-12-31"   # ISBE reporting-period tests (D-2 recon pages and the D-2 totals fallback)

# ---------------------------------------------------------------------------
# Reader-text helpers (small, local: the harness's slicer matches a heading exactly, and these tests must
# not care whether an implementer puts a note inside the heading or a tag next to it)
# ---------------------------------------------------------------------------
_HEADING = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.S)
_TAG_SPAN = re.compile(r'<span class="(scope-tag[^"]*)">(.*?)</span>', re.S)
_AXIS_SPAN = re.compile(r'<span class="time-scope-axis">(.*?)</span>', re.S)
_STAT_VALUE = re.compile(r'class="stat-value">(.*?)</', re.S)
_STAT_DETAIL = re.compile(r'class="stat-detail">(.*?)</', re.S)
_HREF = re.compile(r'<a\b[^>]*?\bhref="([^"]*)"', re.S)


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


def _section(doc: str, title: str, *, level: str | None = None, ordinal: int = 1) -> str | None:
    """HTML from the ``ordinal``-th heading whose reader text (scope tags removed) is ``title`` or starts with
    ``title`` + a space, to the next heading of the same or a higher level (or the end of ``<main>``); None when there
    is no such heading."""
    found = [
        (m.start(), m.group(1), _flat(_TAG_SPAN.sub("", m.group(2))))
        for m in _HEADING.finditer(doc)
    ]
    seen = 0
    for index, (start, tag, text) in enumerate(found):
        if (level and tag != level) or not (text == title or text.startswith(title + " ")):
            continue
        seen += 1
        if seen == ordinal:
            stop = next((s for s, t, _ in found[index + 1:] if t <= tag), len(doc))
            end_of_content = doc.find("</main>", start)
            return doc[start:min(stop, end_of_content) if end_of_content >= 0 else stop]
    return None


def _need(doc: str, title: str, **kwargs) -> str:
    part = _section(doc, title, **kwargs)
    assert part is not None, f"no heading {title!r} {kwargs} on the page"
    return part


def _stat(section: str) -> tuple[str, str]:
    """``(stat-value text, stat-detail text)`` of the first stat card in ``section`` ("" when absent)."""
    value, detail = _STAT_VALUE.search(section), _STAT_DETAIL.search(section)
    return (_flat(value.group(1)) if value else "", _flat(detail.group(1)) if detail else "")


def _args(href: str) -> dict[str, str]:
    """Query string of an href as a plain ``{name: first value}`` mapping (what resolve_time_scope reads)."""
    return {name: values[0] for name, values in parse_qs(urlsplit(href).query, keep_blank_values=True).items()}


def _window_of(args: dict[str, str], *, default: str = "cycle:current"):
    """``(start, end)`` the production resolver gives a request carrying ``args`` (day axis, fixed clock)."""
    scope = resolve_time_scope(default=default, axis="received", args=args, today=date(2026, 10, 26))
    return scope.start, scope.end


def _hrefs(doc: str) -> list[str]:
    """Every anchor href inside ``<main>`` (the nav bar and the footer are outside it and carry no scope by design),
    with HTML entities decoded."""
    main = re.search(r"<main\b.*?</main>", doc, re.S)
    assert main, "no <main> element"
    return [_flat(href) for href in _HREF.findall(main.group(0))]


# ===========================================================================
# E3 -- the bar's axis label says what the SQL windows
# ===========================================================================
RISK_WORDING = ("by receipt date", "by transaction date")


def test_risk_bar_names_the_contribution_date_not_the_filing_date(analytics_app):
    """get_anomaly_flags windows the contribution ``event_date`` (``r.received_date`` on the ISBE path,
    ``transaction_date`` on the legacy one), never a filing date. Either of the axis words the resolver already has
    for that date is accepted; "by filing date" is the lie.

    Kills: leaving axis="filed"; blanking the label (axis="none" prints no span); an invented new axis word."""
    labels = _axis_labels(_get(analytics_app, "/analytics/risk?period=all"))
    assert len(labels) == 1, f"expected one axis label in the bar, found {labels}"
    assert labels[0] in RISK_WORDING, f"risk bar says {labels[0]!r}; its SQL windows the contribution date"


@pytest.mark.parametrize("url", ["/d2-reconciliation/", "/d2-expenditures-reconciliation/"])
def test_d2_reconciliation_bars_name_the_reporting_period_end(profile_app, url):
    """Both pages window ``isbe_filed_docs.reporting_period_end`` (database/models.py D2*Recon._build_filter_sql) but
    print "by filing date". The label is the resolver's existing wording for that date.

    Kills: leaving axis="filed"; relabelling to "by receipt date" / "by transaction date"; dropping the label."""
    labels = _axis_labels(_get(profile_app, url + "?period=all"))
    assert labels == ["by reporting period end"], f"{url}: bar axis labels are {labels}"


def test_legacy_committee_reports_say_filing_date_while_the_page_stays_transaction_dated(w_donor_app):
    """/committees/legacy/<id>: Total Contributions and Recent Contributions window the contribution
    ``transaction_date`` (the bar says so); the Reports table windows ``reports.filed_date``. The Reports section
    must say "by filing date" (the resolver's word for ``filed``); the contributions section must not, and the
    page-level bar stays "by transaction date".

    Kills: switching the whole page to the filed axis (contributions would be mislabelled); a note placed outside
    the Reports section; a note repeated on every section."""
    doc = _get(w_donor_app, "/committees/legacy/1?period=all")
    assert _axis_labels(doc) == ["by transaction date"]
    reports = _flat(_need(doc, "Reports", level="h2"))
    contributions = _flat(_need(doc, "Recent Contributions", level="h2"))
    assert "by filing date" in reports, f"Reports section text: {reports[:200]!r}"
    assert "by filing date" not in contributions, "Recent Contributions is dated by transaction date, not filing date"


# --- guard: the pages really do window the reporting period end, so the new label tells the truth -------------------------
_RECON_DOCS = (
    # doc id, reporting_period_end, received_datetime, committee name shown on the page, in WINDOW_D2 by period end?
    (6001, "2025-12-31", "2026-02-10", "Late Filer Committee", True),       # last day by period; FILED after the window
    (6002, "2025-03-31", "2025-04-15", "Early Period Committee", False),    # day before by period; FILED inside the window
    (6003, "2025-06-30", "2025-07-15", "Middle Committee", True),
    (6004, "2025-04-01", "2025-04-02", "Edge Start Committee", True),       # first day
    (6005, "2026-01-01", "2026-01-02", "Edge After Committee", False),      # day after
    (6006, None, "2025-06-01", "Undated Committee", False),                 # no period end: unknown, excluded under a bound
)


def _create_recon_table(conn, table: str, model) -> None:
    columns = []
    for field in dataclasses.fields(model):
        name = field.name
        if name == "committee_name" or name.endswith("_date"):
            kind = "TEXT"
        elif name.endswith("_id") or name.endswith("_count") or name in ("committee_id_sbe", "is_archived"):
            kind = "INTEGER"
        else:
            kind = "REAL"
        columns.append(f"{name} {kind}")
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE TABLE {table} ({', '.join(columns)})")


@pytest.fixture
def recon_app(profile_app):
    """profile_app plus six filed docs of committee 100 and one row per doc in both reconciliation tables
    (``bulk_d2_receipts_recon`` / ``bulk_d2_expenditures_recon``, built by the bulk loader in production). Window
    2025-04-01..2025-12-31 (WINDOW_D2): IN by ``reporting_period_end`` = Late Filer (filed after the window), Middle, Edge Start;
    OUT = Early Period (filed INSIDE the window), Edge After, Undated."""
    from database.models import D2ExpendituresRecon, D2ReceiptsRecon

    conn = get_db(profile_app.config["DATABASE_PATH"])
    try:
        _create_recon_table(conn, "bulk_d2_receipts_recon", D2ReceiptsRecon)
        _create_recon_table(conn, "bulk_d2_expenditures_recon", D2ExpendituresRecon)
        for doc_id, period_end, received, name, _inside in _RECON_DOCS:
            conn.execute(
                "INSERT INTO isbe_filed_docs (id, committee_id, doc_name, reporting_period_end, received_datetime) "
                "VALUES (?, 100, ?, ?, ?)",
                (doc_id, name, period_end, received),
            )
            for table in ("bulk_d2_receipts_recon", "bulk_d2_expenditures_recon"):
                conn.execute(
                    f"INSERT INTO {table} (d2_totals_record_id, committee_id_sbe, committee_name, filed_doc_id, is_archived) "
                    "VALUES (?, 100, ?, ?, 0)",
                    (doc_id + 100, name, doc_id),
                )
        conn.commit()
    finally:
        conn.close()
    return profile_app


@pytest.mark.parametrize("url", ["/d2-reconciliation/", "/d2-expenditures-reconciliation/"])
def test_d2_reconciliation_windows_the_period_end_not_the_filing_date(recon_app, url):
    """GUARD (passes today): the label "by reporting period end" is only true while the SQL windows
    ``isbe_filed_docs.reporting_period_end`` (D2*Recon._build_filter_sql). The rows are chosen so that period end and received
    date disagree: Late Filer is in by period end but filed after the window; Early Period is out by period end but filed inside it.

    Kills: resolving the label/axis mismatch the other way round (re-pointing the SQL at the received date), which keeps the page
    consistent but changes what the D-2 reconciliation shows."""
    text = _flat(_get(recon_app, f"{url}?{WINDOW_D2}"))
    everything = _flat(_get(recon_app, f"{url}?{ALL_TIME}"))
    for _doc, _end, _received, name, inside in _RECON_DOCS:
        assert _has(everything, name), f"control: {name} should be listed under period=all"
        assert _has(text, name) is inside, f"{name}: expected {'listed' if inside else 'absent'} for the window {WINDOW_D2}"


# ===========================================================================
# E2 -- feeders that must follow the window (or say they do not)
# ===========================================================================
def _json_script(doc: str, script_id: str) -> dict:
    match = re.search(rf'<script id="{re.escape(script_id)}"[^>]*>(.*?)</script>', doc, re.S)
    assert match, f"no <script id={script_id!r}> on the page"
    return json.loads(match.group(1))


_VENDOR_ROW = (
    "INSERT INTO bulk_expenditures_clean (committee_id_sbe, payee_last_or_business_name, candidate_name, d2_part_code, "
    "is_archived, amount, expended_date) VALUES (1001, ?, 'Candidate Alpha', '9A', 0, ?, ?)"
)


@pytest.fixture
def vendor_app(w_analytics_app):
    """w_analytics_app plus committee-1001 payees with per-edge totals >= 1,000 (the vendor graph's minimum edge).

    Window 2026-02-01..2026-03-31 (WINDOW_ANALYTICS). Base seed: Vendor One 3,387 (1,500 + 999 + 888, all on 2026-01-08,
    OUT) and Legacy Vendor 600 (never an edge).
      IN   Vendor Mid Co         1,500  2026-03-10
      IN   Vendor Edge Start Co  1,100  2026-02-01   (the window's first day)
      IN   Vendor Edge End Co    1,200  2026-03-31   (its last day)
      IN   Vendor Split Co       1,000  2026-03-05 + OUT 700 on 2026-01-15: window edge 1,000, all time 1,700
      OUT  Vendor Day Before Co  1,400  2026-01-31
      OUT  Vendor Day After Co   1,300  2026-04-01
      OUT  Vendor Stale Co       1,600  2024-05-05
      OUT  Vendor Thin Co        window total 600 (2026-03-02) < 1,000, all time 1,500 (+ 900 on 2024-05-06)
    """
    return _topup(
        w_analytics_app,
        (_VENDOR_ROW, ("Vendor Mid Co", 1500.0, "2026-03-10")),
        (_VENDOR_ROW, ("Vendor Edge Start Co", 1100.0, "2026-02-01")),
        (_VENDOR_ROW, ("Vendor Edge End Co", 1200.0, "2026-03-31")),
        (_VENDOR_ROW, ("Vendor Split Co", 1000.0, "2026-03-05")),
        (_VENDOR_ROW, ("Vendor Split Co", 700.0, "2026-01-15")),
        (_VENDOR_ROW, ("Vendor Day Before Co", 1400.0, "2026-01-31")),
        (_VENDOR_ROW, ("Vendor Day After Co", 1300.0, "2026-04-01")),
        (_VENDOR_ROW, ("Vendor Stale Co", 1600.0, "2024-05-05")),
        (_VENDOR_ROW, ("Vendor Thin Co", 600.0, "2026-03-02")),
        (_VENDOR_ROW, ("Vendor Thin Co", 900.0, "2024-05-06")),
    )


def _vendor_edges(doc: str) -> dict[str, float]:
    """``{vendor label: summed edge weight}`` of the Vendor tab's embedded graph."""
    graph = _json_script(doc, "vendor-network-data")
    labels = {node["id"]: node["label"] for node in graph["nodes"] if node.get("node_type") == "vendor"}
    weights: dict[str, float] = {}
    for edge in graph["edges"]:
        if edge["target"] in labels:
            label = labels[edge["target"]]
            weights[label] = weights.get(label, 0.0) + float(edge["weight"])
    return weights


VENDORS_IN_WINDOW = {
    "Vendor Mid Co": 1500.0,
    "Vendor Edge Start Co": 1100.0,
    "Vendor Edge End Co": 1200.0,
    "Vendor Split Co": 1000.0,
}
VENDORS_OUT_OF_WINDOW = ("Vendor Day Before Co", "Vendor Day After Co", "Vendor Stale Co", "Vendor Thin Co", "Vendor One")


def test_networks_vendor_graph_follows_the_window(vendor_app):
    """E2 / #77: ``get_vendor_expenditure_network`` takes no dates, so the Vendor tab shows every payee ever. Under an
    explicit window the embedded graph holds exactly the in-window edges, each summed over the window only.

    Kills: no window at all (today); filtering payees by "has any in-window row" but summing every row (Split would
    weigh 1,700, Thin would stay); windowing after the all-time 1,000 minimum (Thin 600 would survive); an exclusive
    bound (the two edge-day payees); a one-day-wide window (Day Before / Day After). Control: period=all shows all of
    them, so the OUT names are not absent for an unrelated reason."""
    windowed = _vendor_edges(_get(vendor_app, f"/analytics/networks?load_mode=full&{WINDOW_ANALYTICS}"))
    everything = _vendor_edges(_get(vendor_app, f"/analytics/networks?load_mode=full&{ALL_TIME}"))

    for name in (*VENDORS_IN_WINDOW, *VENDORS_OUT_OF_WINDOW):
        assert name in everything, f"control: {name} should be in the all-time vendor graph, got {sorted(everything)}"
    assert everything["Vendor Split Co"] == 1700.0

    for name, weight in VENDORS_IN_WINDOW.items():
        assert windowed.get(name) == weight, f"{name}: expected edge weight {weight} in the window, got {windowed.get(name)}"
    leaked = sorted(set(windowed) - set(VENDORS_IN_WINDOW))
    assert leaked == [], f"vendors in the windowed Vendor tab graph that the window does not contain: {leaked}"


def test_networks_state_federal_tab_is_tagged_all_time(w_analytics_app):
    """E2 / #79 / J-S14: the State-Federal overlap graph has no date arguments (its local side is
    ``fec_local_donor_matches`` totals), so its panel carries a FIXED "All time" tag instead of inheriting the page's
    window tag. Pinned on the panel (``panel-overlap``), not on a heading text.

    Kills: tagging the panel with the page window (``scope_tag()`` prints "Custom range"); a fixed tag with other
    text; the tag placed on another tab's panel or only in the page-level heading."""
    doc = _get(w_analytics_app, f"/analytics/networks?load_mode=full&{WINDOW_ANALYTICS}")
    start = doc.index('class="tab-panel panel-overlap"')
    stop = doc.index('class="tab-panel panel-lobbying"')
    assert start < stop, "tab panel order changed: panel-overlap must precede panel-lobbying"
    tags = _tags(doc[start:stop])
    assert tags, "the State-Federal panel carries no scope tag"
    assert all(tag == (True, "All time") for tag in tags), f"State-Federal panel tags: {tags}"


# Federal candidate detail (/federal-finance/<id>): a custom window is not a cycle preset, so the route passes
# ``date_from`` / ``date_to`` (not ``cycle``) to get_federal_candidate_detail, which hands neither to the two feeders below.
_CHAIN_ROW = (
    "INSERT INTO fec_schedule_b_disbursements (sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name, "
    "recipient_name, recipient_city, recipient_state, recipient_zip, recipient_committee_id, recipient_candidate_id, "
    "recipient_candidate_name, disbursement_description, disbursement_amount, disbursement_date, "
    "two_year_transaction_period, load_date) "
    "VALUES (?, ?, 'H2IL01349', 'JACKSON, JONATHAN', 'C00011111', 'JONATHAN JACKSON FOR CONGRESS', ?, 'Chicago', 'IL', "
    "'60601', ?, ?, ?, 'Transfer', ?, ?, ?, '2026-03-02T01:02:03')"
)


@pytest.fixture
def chain_app(w_federal_app):
    """w_federal_app plus Schedule B rows WITH recipient ids, all paid by Jackson's committee C00011111.

    Window 2026-01-01..2026-06-30 (WINDOW_FEDERAL). Recipient committee -> rows:
      IN   C00055001  610  2026-03-03
      IN   C00055002  620  2026-06-30   (the window's last day)
      IN   C00055003  630  2026-01-01   (its first day)
      IN   C00055007  100  2026-02-02   + OUT 200 on 2025-11-11: window 1 transfer / $100.00, all time 2 / $300.00
      OUT  C00055004  640  2025-12-31   (the day before)
      OUT  C00055005  650  2026-07-01   (the day after)
      OUT  C00055006  660  2023-05-05   (cycle 2024)
    The base seed's Schedule B rows carry no recipient ids, so they are not chains.
    """
    rows = (
        # sub_id, cycle, recipient name, recipient committee, recipient candidate, candidate name, amount, date
        ("b-tc-in-1", 2026, "Chain Committee In", "C00055001", "H2IL05001", "CHAIN IN, ALEX", 610.0, "2026-03-03"),
        ("b-tc-in-2", 2026, "Chain Committee End", "C00055002", "H2IL05002", "CHAIN END, BLAIR", 620.0, "2026-06-30"),
        ("b-tc-in-3", 2026, "Chain Committee Start", "C00055003", "H2IL05003", "CHAIN START, CASEY", 630.0, "2026-01-01"),
        ("b-tc-in-4", 2026, "Chain Committee Split", "C00055007", "H2IL05007", "CHAIN SPLIT, GALE", 100.0, "2026-02-02"),
        ("b-tc-out-0", 2026, "Chain Committee Split", "C00055007", "H2IL05007", "CHAIN SPLIT, GALE", 200.0, "2025-11-11"),
        ("b-tc-out-1", 2026, "Chain Committee Early", "C00055004", "H2IL05004", "CHAIN EARLY, DANA", 640.0, "2025-12-31"),
        ("b-tc-out-2", 2026, "Chain Committee Late", "C00055005", "H2IL05005", "CHAIN LATE, ELLIS", 650.0, "2026-07-01"),
        ("b-tc-out-3", 2024, "Chain Committee Stale", "C00055006", "H2IL05006", "CHAIN STALE, FRANK", 660.0, "2023-05-05"),
    )
    return _topup(w_federal_app, *[(_CHAIN_ROW, (*row, row[1])) for row in rows])  # last parameter: two_year_transaction_period


def test_federal_candidate_transfer_chains_follow_the_window(chain_app):
    """E2 / #148: ``_get_candidate_schedule_b_transfer_chains`` is called without dates, so a custom window shows every
    chain of the candidate. Under the window only in-window Schedule B rows count, summed over the window.

    Kills: no window (today); windowing by cycle only (the 2023 row has cycle 2024, the 2025-12-31 row cycle 2026);
    an exclusive bound (C00055002 / C00055003 sit on the edge days); filtering recipients by "has an in-window row" but
    summing all (C00055007 would read $300.00 and 2 transfers); a one-day-wide window (C00055004 / C00055005). The control
    (period=all) shows all seven recipient committees."""
    section = _need(_get(chain_app, f"/federal-finance/H2IL01349?{WINDOW_FEDERAL}"), "Explicit Transfer Chains", level="h2")
    everything = _flat(_need(_get(chain_app, f"/federal-finance/H2IL01349?{ALL_TIME}"), "Explicit Transfer Chains", level="h2"))
    text = _flat(section)

    for committee in ("C00055001", "C00055002", "C00055003", "C00055004", "C00055005", "C00055006", "C00055007"):
        assert _has(everything, committee), f"control: {committee} should be listed under period=all"
    listed = sorted(set(re.findall(r"C000550\d\d", text)))
    assert listed == ["C00055001", "C00055002", "C00055003", "C00055007"], f"recipient committees listed under the window: {listed}"
    assert _has(text, "$100.00"), "the split recipient should total $100.00 inside the window"
    assert not _has(text, "$300.00"), "the split recipient's all-time $300.00 leaked into the windowed chains"


def test_federal_candidate_cross_role_organizations_follow_the_window(w_federal_app):
    """E2 / #150: ``get_federal_cross_role_organizations`` accepts ``date_from`` / ``date_to`` but its caller passes none.
    Base seed, Jackson: Fresh Mailers Inc gave 300 (A, 2026-02-10) and received 700 (E, 2026-06-30) -> IN; Old Billboards
    LLC gave 350 (2025-09-09) / received 800 (2025-08-01) and Late Ads gave 280 (2026-09-15) / received 900 (2026-09-12)
    -> OUT. Window: 1 organisation, $300.00 donated, $700.00 received; all time: 3 organisations.

    Kills: no window (today: 3 matched); windowing only the Schedule A side (Old Billboards would keep its $800.00 via
    E) or only the E side; an exclusive upper bound (Fresh Mailers' E row is on 2026-06-30)."""
    text = _flat(_need(_get(w_federal_app, f"/federal-finance/H2IL01349?{WINDOW_FEDERAL}"), "Cross-Role Organizations", level="h2"))
    everything = _flat(_need(_get(w_federal_app, f"/federal-finance/H2IL01349?{ALL_TIME}"), "Cross-Role Organizations", level="h2"))

    assert _has(everything, "3 matched organizations"), "control: period=all should match three organisations"
    assert _has(text, "1 matched organizations"), f"summary line under the window: {text[:400]!r}"
    assert _has(text, "Fresh Mailers Inc"), "Fresh Mailers Inc (A and E inside the window) is missing"
    assert _has(text, "$300.00 donated"), f"donated total under the window: {text[:400]!r}"
    assert _has(text, "$700.00 received"), f"received total under the window: {text[:400]!r}"
    leaked = [name for name in ("Old Billboards LLC", "Late Ads") if _has(text, name)]
    assert leaked == [], f"out-of-window organisations still listed: {leaked}"


# ===========================================================================
# E4 / A6 -- partially windowed cards: window them, or say they are fixed
# ===========================================================================
# --- FEC cards on / and /candidates (webapp/routes/main.py _get_candidate_stats) ---------------------------------------
_EDGE_DONOR = ("H2IL01349", "JACKSON, JONATHAN", "C00011111", "JONATHAN JACKSON FOR CONGRESS")


@pytest.fixture
def fec_cards_app(w_federal_app):
    """w_federal_app plus rows around the windows' edge days and three cycle-2024 rows that are not in the 2023 window.

    Window 1 = 2026-01-01..2026-06-30 (WINDOW_FEDERAL).
    Schedule A inside it: Jane 250 (2026-01-15), Fresh Mailers Inc 300 (2026-02-10), Edge Start 110 (2026-01-01) and Edge
    End 120 (2026-06-30) -> 4 contributions, $780.00. Outside it: the base seed's other eight rows (2,420), Edge Before 130
    (2025-12-31), Edge After 140 (2026-07-01) and Cycle Mate 150 (2024-08-08, cycle 2024).
    Schedule B inside: Fresh Media LLC 320 (2026-01-01) -> 1 / $320.00. Schedule E inside: WINDOW PAC 700 (2026-06-30) -> 1 / $700.00.
    All time: A 2,970 (base, 10 rows) + 110 + 120 + 130 + 140 + 150 = 3,620 in 15 contributions; B 990 + Cycle Mate Vendor 210
    (2024-09-09) = $1,200.00 in 4; E 2,400 + CYCLE MATE PAC 220 (2024-10-10) = $2,620.00 in 4.
    Window 2 = 2023-05-01..2023-12-31 (inside cycle 2024 = 2023-01-01..2024-12-31): only Jane's 2023-06-01 gift (cycle 2024)
    is inside -> 1 contribution / $400.00, 0 B, 0 E. The three cycle-2024 rows above share that cycle but not the window.
    fec_candidate_match: Jackson (H2IL01349) in cycles 2026 and 2024, Smith (H2IL09777) in 2026 -> 2 distinct candidates in
    cycle 2026, 1 in cycle 2024. fec_candidate_cycle_totals holds a REPORTED 2026 receipts figure of 1,200.00 for Jackson,
    which is not the sum of any rows above (today's 2026-cycle card prints $1,200.00 under every window).
    """
    conn = get_db(w_federal_app.config["DATABASE_PATH"])
    try:
        for sub_id, donor, amount, day in (
            ("sub-e1", "Edge Start Donor", 110.0, "2026-01-01"),
            ("sub-e2", "Edge End Donor", 120.0, "2026-06-30"),
            ("sub-e3", "Edge Before Donor", 130.0, "2025-12-31"),
            ("sub-e4", "Edge After Donor", 140.0, "2026-07-01"),
        ):
            conn.execute(
                _INSERT_FEC_A,
                (sub_id, 2026, *_EDGE_DONOR, donor, "Chicago", "IL", "60601", "", "", 1, amount, day, 2026,
                 f"donor-key-{sub_id}", f"img-{sub_id}", f"src-{sub_id}"),
            )
        conn.execute(
            _INSERT_FEC_A,
            ("sub-e5", 2024, *_EDGE_DONOR, "Cycle Mate Donor", "Chicago", "IL", "60601", "", "", 1, 150.0, "2024-08-08", 2024,
             "donor-key-sub-e5", "img-sub-e5", "src-sub-e5"),
        )
        conn.execute(_INSERT_FEC_B, ("b-24-1", 2024, "Cycle Mate Vendor", "Chicago", "60601", "Mailers", 210.0, "2024-09-09", 2024))
        conn.execute(
            _INSERT_FEC_E,
            ("e-24-1", 2024, "S", "C00099004", "CYCLE MATE PAC", "Cycle Mate Vendor", "Chicago", "60601", "Mailers", 220.0, "2024-10-10"),
        )
        conn.commit()
    finally:
        conn.close()
    return w_federal_app


FEC_WINDOW_2026 = WINDOW_FEDERAL                                  # inside cycle 2026
FEC_WINDOW_2024 = "date_from=2023-05-01&date_to=2023-12-31"        # inside cycle 2024 (2023-01-01..2024-12-31)

# card key -> (h3 title, ordinal of that title on the page)
HOME_CARDS = {
    "candidates": ("Federal Candidates", 1),
    "contributions": ("Federal Contributions", 1),
    "disbursements": ("Federal Disbursements (Schedule B)", 1),
    "independent": ("Federal Independent Expenditures (Schedule E)", 1),
}
CANDIDATES_PAGE_CARDS = {"candidates": ("Candidates", 2), "contributions": ("Contributions", 1)}  # 1st "Candidates" is ISBE


def _cards(doc: str, spec: dict) -> dict[str, tuple[str, str, list]]:
    """``{key: (stat-value, stat-detail, scope tags)}`` for the cards of ``spec`` (a missing card fails the test)."""
    cards = {}
    for key, (title, ordinal) in spec.items():
        part = _need(doc, title, level="h3", ordinal=ordinal)
        value, detail = _stat(part)
        cards[key] = (value, detail, _tags(part))
    return cards


def _assert_card(cards: dict, key: str, value: str, detail: str | None = None) -> None:
    got_value, got_detail, tags = cards[key]
    assert got_value == value, f"{key} card value: expected {value!r}, got {got_value!r}"
    if detail is not None:
        assert got_detail == detail, f"{key} card detail: expected {detail!r}, got {got_detail!r}"
    assert tags, f"{key} card carries no scope tag"
    assert not any(fixed for fixed, _ in tags), f"{key} card follows the window but its tag is fixed: {tags}"


def _assert_candidates_card(cards: dict, value: str, cycle_tag: str) -> None:
    got_value, _detail, tags = cards["candidates"]
    assert got_value == value, f"candidates card value: expected {value!r}, got {got_value!r}"
    assert tags == [(True, cycle_tag)], f"candidates card tag: expected a fixed {cycle_tag!r}, got {tags}"


def test_home_fec_cards_follow_the_window(fec_cards_app):
    """E4 / A6 / J-S14 (#10-#12): ``_get_candidate_stats`` filters every FEC query by ``cycle IN (...)`` of the window,
    so a custom range shows the whole cycle (and the reported cycle total) instead of the range. Under the window the
    Contributions / Schedule B / Schedule E cards count and sum the rows dated inside it
    (``contribution_receipt_date`` / ``disbursement_date`` / ``expenditure_date``); the Candidates card
    (fec_candidate_match has a cycle, no date) stays cycle-granular and says so with a FIXED "<cycle> Cycle" tag.

    Kills: today's cycle filter (13 / 3 / 3 rows); windowing the counts but keeping the reported cycle total ($1,200.00);
    an exclusive bound (Edge Start / Edge End, the B row on 2026-01-01 and the E row on 2026-06-30); windowing by `cycle`
    only (Edge Before / Edge After are cycle 2026); a candidates card tagged with the window label or a hard-coded
    "2026 Cycle" (see the 2024 window test); fixed tags on the three windowed cards."""
    windowed = _cards(_get(fec_cards_app, f"/?{FEC_WINDOW_2026}"), HOME_CARDS)
    _assert_card(windowed, "contributions", "4", "$780.00 total")
    _assert_card(windowed, "disbursements", "1", "$320.00 total")
    _assert_card(windowed, "independent", "1", "$700.00 total")
    _assert_candidates_card(windowed, "2", "2026 Cycle")

    control = _cards(_get(fec_cards_app, f"/?{ALL_TIME}"), HOME_CARDS)  # the OUT rows exist and are counted when unbounded
    assert control["contributions"][0] == "15"
    assert control["disbursements"][:2] == ("4", "$1,200.00 total")
    assert control["independent"][:2] == ("4", "$2,620.00 total")


def test_home_fec_cards_in_an_earlier_cycle_name_that_cycle(fec_cards_app):
    """The same cards for a window inside cycle 2024 (2023-05-01..2023-12-31): only Jane's 2023-06-01 gift is inside, no
    Schedule B / E row, and the candidates card is the 2024 cycle's one candidate under a fixed "2024 Cycle" tag.

    Kills: a tag hard-coded to the current cycle; a cycle filter instead of the window (Cycle Mate A / B / E rows are cycle
    2024 but dated after the window: today's cards would read 2 / $550.00, 1, 1); empty cards that print the 2026 figures."""
    cards = _cards(_get(fec_cards_app, f"/?{FEC_WINDOW_2024}"), HOME_CARDS)
    _assert_card(cards, "contributions", "1", "$400.00 total")
    _assert_card(cards, "disbursements", "0", "$0.00 total")
    _assert_card(cards, "independent", "0", "$0.00 total")
    _assert_candidates_card(cards, "1", "2024 Cycle")


def test_candidates_page_fec_cards_follow_the_window(fec_cards_app):
    """E4 / A6 / J-S14 (#27, #28): the same feeder drives the FEC column of /candidates (first "Candidates" card is the
    ISBE one). Contributions follow the window; Candidates carries the fixed cycle tag. A second window inside cycle 2024
    pins the tag to the window's cycle.

    Kills: fixing only the home page; a tag hard-coded to 2026."""
    in_2026 = _cards(_get(fec_cards_app, f"/candidates?{FEC_WINDOW_2026}"), CANDIDATES_PAGE_CARDS)
    _assert_card(in_2026, "contributions", "4", "$780.00 total")
    _assert_candidates_card(in_2026, "2", "2026 Cycle")

    in_2024 = _cards(_get(fec_cards_app, f"/candidates?{FEC_WINDOW_2024}"), CANDIDATES_PAGE_CARDS)
    _assert_card(in_2024, "contributions", "1", "$400.00 total")
    _assert_candidates_card(in_2024, "1", "2024 Cycle")


# --- D-2 totals fallback on / and /candidates (main.py reads isbe_d2_reports when bulk_d2_totals_clean is absent) -------
_FILED_DOC = "INSERT INTO isbe_filed_docs (id, committee_id, doc_name, reporting_period_end) VALUES (?, 100, ?, ?)"
_D2_REPORT = (
    "INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, total_receipts, total_expenditures, archived) "
    "VALUES (?, 100, ?, ?, ?, ?)"
)


@pytest.fixture
def d2_fallback_app(profile_app):
    """profile_app with its ``bulk_d2_totals_clean`` compat view DROPPED (the ``--skip-compat-swap`` shape: main.py then reads
    ``isbe_d2_reports`` directly, the fallback branch) plus edge docs.

    Window 2025-04-01..2025-12-31 (WINDOW_D2), dated by the filed doc's ``reporting_period_end``. Base seed:
      OUT  doc 5001 (ends 2025-03-31, the day before)  50,000 / 20,000     OUT doc 5002 (ends 2025-03-31)  25,000 / 10,000
      IN   doc 5003 (ends 2025-06-30)                  30,000 / 15,000
    Added:
      IN   doc 5101 ends 2025-04-01 (first day)  1,000 / 400        IN  doc 5102 ends 2025-12-31 (last day)  2,000 / 800
      OUT  doc 5103 ends 2026-01-01 (day after)  8,000 / 3,200
      OUT  doc 5104 ends 2025-07-01 but its D-2 row is ARCHIVED  7,777 / 3,333  (excluded everywhere)
      OUT  doc 5105 has no reporting_period_end  5,555 / 2,222   (unknown period: excluded under a bound, counted all time)
    Window: receipts 30,000 + 1,000 + 2,000 = $33,000.00; expenditures 15,000 + 400 + 800 = $16,200.00.
    All time (archived excluded): receipts 105,000 + 1,000 + 2,000 + 8,000 + 5,555 = $121,555.00; expenditures
    45,000 + 400 + 800 + 3,200 + 2,222 = $51,622.00.
    """
    return _topup(
        profile_app,
        ("DROP VIEW IF EXISTS bulk_d2_totals_clean", ()),
        (_FILED_DOC, (5101, "D-2 edge start", "2025-04-01")),
        (_FILED_DOC, (5102, "D-2 edge end", "2025-12-31")),
        (_FILED_DOC, (5103, "D-2 day after", "2026-01-01")),
        (_FILED_DOC, (5104, "D-2 archived", "2025-07-01")),
        (_FILED_DOC, (5105, "D-2 undated", None)),
        (_D2_REPORT, (9101, 5101, 1000.0, 400.0, False)),
        (_D2_REPORT, (9102, 5102, 2000.0, 800.0, False)),
        (_D2_REPORT, (9103, 5103, 8000.0, 3200.0, False)),
        (_D2_REPORT, (9104, 5104, 7777.0, 3333.0, True)),
        (_D2_REPORT, (9105, 5105, 5555.0, 2222.0, False)),
    )


def _money_card(doc: str, title: str, *, level: str = "h3") -> str:
    return _stat(_need(doc, title, level=level))[0]


def test_home_local_receipts_card_windows_the_d2_fallback(d2_fallback_app):
    """E4 / J-S14 (#8): without ``bulk_d2_totals_clean`` the card sums ``isbe_d2_reports`` for every filing ever. Under a
    window it sums the D-2 rows whose filed doc's ``reporting_period_end`` (via ``filed_doc_id``) falls inside it.

    Kills: the unwindowed fallback (today: $121,555.00 under the window); windowing by the doc's received date; an
    exclusive bound (docs 5101 / 5102 sit on the edge days; 5001 / 5002 end the day before); dropping the archived
    filter while rewriting the query (9104); counting undated docs under a bound (9105); a left join that keeps D-2 rows
    whose doc is missing. Control: period=all keeps the unwindowed total."""
    assert _money_card(_get(d2_fallback_app, f"/?{WINDOW_D2}"), "Local Receipts") == "$33,000.00"
    assert _money_card(_get(d2_fallback_app, f"/?{ALL_TIME}"), "Local Receipts") == "$121,555.00"


def test_candidates_page_state_totals_window_the_d2_fallback(d2_fallback_app):
    """The same fallback feeds the ISBE column of /candidates: Total Receipts and Total Expenditures.

    Kills: windowing receipts but not expenditures (or the reverse)."""
    windowed = _get(d2_fallback_app, f"/candidates?{WINDOW_D2}")
    assert _money_card(windowed, "Total Receipts") == "$33,000.00"
    assert _money_card(windowed, "Total Expenditures") == "$16,200.00"
    everything = _get(d2_fallback_app, f"/candidates?{ALL_TIME}")
    assert _money_card(everything, "Total Receipts") == "$121,555.00"
    assert _money_card(everything, "Total Expenditures") == "$51,622.00"


# --- Federal/Local matching page: the local side is all-time --------------------------------------------------------------
MATCHING_LOCAL_SIDE = (
    ("Local Donors Considered", "h3"),
    ("Match Method Tiers", "h2"),
    ("Local vs Federal Overlap Bubble View", "h2"),
    ("Matched Donor Rows", "h2"),
)


@pytest.mark.parametrize(("title", "level"), MATCHING_LOCAL_SIDE)
def test_matching_page_local_side_is_tagged_all_time(w_federal_app, title, level):
    """E4 / J-S14 (#124-#127): ``get_federal_local_donor_matches`` windows the federal side only; the local donor pool and
    local totals are all-time. Every widget that shows the local side carries a FIXED "All time" tag inside its section
    (its own heading, or a sub-heading of it), under a custom window.

    Kills: today (windowed tags only, none fixed); a fixed tag placed on a different widget or in the form; a bare
    "Local" label without the tag text. Accepted: the federal side's own tag is not pinned here (S-W5 tags the untagged
    cards)."""
    section = _need(_get(w_federal_app, f"/federal-finance/matching?{WINDOW_FEDERAL}"), title, level=level)
    fixed = [text for is_fixed, text in _tags(section) if is_fixed]
    assert "All time" in fixed, f"{title!r}: tags are {_tags(section)}"


# --- OpenBook list: Contracts / Total Award cards are fiscal-year totals, not windowed -------------------------------------
_CONTRACT = (
    "INSERT INTO openbook_contracts_raw (openbook_vendor_key, vendor_label, fiscal_year, contract_number, award_amount, "
    "source_url, row_hash) VALUES ('CONTRIB VENDOR', 'Contrib Vendor', ?, ?, ?, 'https://example.com', ?)"
)


@pytest.fixture
def openbook_contracts_app(w_openbook_app):
    """w_openbook_app plus two contracts outside every window below: FY2023 12,000 and FY2027 3,000 -> 2 contracts,
    $15,000.00 (the cards' all-time values; the window 2025-02-01..2026-12-31 contains neither fiscal year)."""
    return _topup(
        w_openbook_app,
        (_CONTRACT, (2023, "C-2023-1", 12000.0, "hash-k-1")),
        (_CONTRACT, (2027, "C-2027-1", 3000.0, "hash-k-2")),
    )


@pytest.mark.parametrize(("title", "value"), [("Contracts", "2"), ("Total Award Amount", "$15,000.00")])
def test_openbook_list_contract_cards_are_tagged_all_time(openbook_contracts_app, title, value):
    """E4 / J-S14 (#178): ``openbook_contracts_raw`` has a fiscal year, no activity date, so these two cards ignore the
    window. Each carries a FIXED "All time" tag and keeps its all-time value under a window that contains neither contract.

    Kills: today (no tag); a windowed tag on a number that is not windowed; "fixing" the number to 0 by windowing on
    fiscal_year (the tag would then be a lie about a different bug); a tag on the wrong card."""
    section = _need(_get(openbook_contracts_app, f"/openbook/?{WINDOW_ISBE}"), title, level="h3")
    assert _stat(section)[0] == value
    assert _tags(section) == [(True, "All time")]


# --- Lobbying client counts: windowed by lobbying_entity_clients.reg_year ------------------------------------------------
@pytest.fixture
def lobbying_counts_app(tmp_path):
    """Two entities whose clients registered in different years (``lobbying_entity_clients.reg_year``).

    Window 2025-02-01..2026-12-31 (WINDOW_ISBE): the reg_year axis snaps it to whole years 2025-2026.
      Entity 1 'Carry Entity' (reg_year 2025):  client 11 reg 2025 (IN, the first year), 12 reg 2026 (IN, the last year),
        13 reg 2024 (OUT), 14 reg 2027 (OUT), 15 reg 2023 AND 2026 (IN once: the 2023 registration is not a second client)
        -> window 3 clients (11, 12, 15), all time 5 distinct clients.
      Entity 2 'Second Entity' (reg_year 2026): client 21 reg 2026 (IN), 22 reg 2024 (OUT) -> window 1, all time 2.
    """
    from database.connection import init_db
    from webapp.app import create_app

    db_path = str(tmp_path / "lobbying_counts.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        conn.execute("INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Carry Entity', 2025)")
        conn.execute("INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (2, 'Second Entity', 2026)")
        for client_id, name in (
            (11, "Client In 2025"), (12, "Client In 2026"), (13, "Client Out 2024"), (14, "Client Out 2027"),
            (15, "Client Mixed"), (21, "Client Second In"), (22, "Client Second Out"),
        ):
            conn.execute("INSERT INTO lobbying_clients (client_id, client_name) VALUES (?, ?)", (client_id, name))
        for entity_id, client_id, year in (
            (1, 11, 2025), (1, 12, 2026), (1, 13, 2024), (1, 14, 2027), (1, 15, 2023), (1, 15, 2026), (2, 21, 2026), (2, 22, 2024),
        ):
            conn.execute(
                "INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (?, ?, ?)", (entity_id, client_id, year)
            )
        conn.commit()
    finally:
        conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path, "DASHBOARD_PREWARM_ENABLED": False,
                       "ROUTE_PERF_CACHE_ENABLED": False})


def _client_counts(doc: str) -> dict[str, str]:
    """``{entity name: Clients cell}`` from the /lobbying/ table (columns: id, name, reg year, clients)."""
    counts = {}
    for row in re.findall(r"<tr>(.*?)</tr>", doc, re.S):
        cells = [_flat(cell) for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) == 4:
            counts[cells[1]] = cells[3]
    return counts


def test_lobbying_list_client_counts_window_by_registration_year(lobbying_counts_app):
    """E4 / J-S14: ``client_count`` is ``COUNT(DISTINCT ec.client_id)`` over ALL of an entity's registrations. Under a
    window it counts the clients registered in the scope's years, each once.

    Kills: today (5 / 2); counting registrations instead of clients (Client Mixed twice); filtering by the entity's own
    reg_year only; an exclusive year bound (clients 11 / 12 sit in the window's first / last year); one global window count
    for every row (entity 2 must read 1, not 3). Control: period=all keeps 5 / 2."""
    windowed = _client_counts(_get(lobbying_counts_app, f"/lobbying/?{WINDOW_ISBE}"))
    assert windowed == {"Carry Entity": "3", "Second Entity": "1"}, windowed
    everything = _client_counts(_get(lobbying_counts_app, f"/lobbying/?{ALL_TIME}"))
    assert everything == {"Carry Entity": "5", "Second Entity": "2"}, everything


def test_lobbying_entity_detail_clients_follow_the_registration_years(lobbying_counts_app):
    """E4 / J-S14 (entity detail Clients section): the heading count, the rows and the tag follow the window. An
    out-of-year client is not listed or counted, a client registered in two years shows only its in-window year, and the
    tag is the page's window tag (the section was a FIXED "All time" tag over an unwindowed query).

    Kills: today (5 distinct clients incl. both OUT ones); windowing the rows but printing the all-time count; a window
    tag left as fixed "All time"; listing Client Mixed's 2023 registration. Control: period=all lists the OUT clients.
    CONFLICT with a green test (amend it with this change; not touched here): tests/test_profile_scope.py ~L464,
    TestLobbyingProfileScope.test_entity_defaults_to_all_time_with_cycle_subtotal, asserts
    ``<h2>Clients (1) <span class="scope-tag scope-tag-fixed">All time</span></h2>`` -- once Clients is windowed the default
    (all-time) scope prints the non-fixed ``<span class="scope-tag">All time</span>``."""
    section = _need(_get(lobbying_counts_app, f"/lobbying/1?{WINDOW_ISBE}"), "Clients", level="h2")
    text = _flat(section)
    assert _has(text, "Clients (3)"), f"heading count: {text[:80]!r}"
    for name in ("Client In 2025", "Client In 2026", "Client Mixed"):
        assert _has(text, name), f"{name} (registered in the window) is missing"
    assert [n for n in ("Client Out 2024", "Client Out 2027") if _has(text, n)] == []
    assert not _has(text, "2023"), "Client Mixed's 2023 registration is outside the window"
    tags = _tags(section)
    assert tags and not any(fixed for fixed, _ in tags), f"Clients is windowed now, but its tag is {tags}"

    everything = _flat(_need(_get(lobbying_counts_app, f"/lobbying/1?{ALL_TIME}"), "Clients", level="h2"))
    assert _has(everything, "Client Out 2024") and _has(everything, "Client Out 2027"), "control: OUT clients under period=all"


# --- Top State Races: cycle-based columns are marked ------------------------------------------------------------------
STATE_RACE_PAGES = (("/analytics/?period=all", "Top State Races"), ("/analytics/state-races/?period=all", "State Races"))


@pytest.mark.parametrize(("url", "heading"), STATE_RACE_PAGES)
def test_cycle_based_state_race_columns_are_marked(analytics_app, url, heading):
    """E4 / A6: the candidate count and Top Candidate Share come from the cycle's candidates and D-2 totals, not from the
    window ("Candidates: 2026 cycle; the money columns follow the selected window"). Their column headers say "(cycle)";
    no other header does.

    Kills: today (no marker); marking every header; marking the wrong two (Donors / Contributions are windowed); a marker
    only in the help text above the table."""
    section = _need(_get(analytics_app, url), heading, level="h2")
    headers = [_flat(th) for th in re.findall(r"<th\b[^>]*>(.*?)</th>", section, re.S)]
    assert headers, f"no table headers under {heading!r}"
    marked = [header for header in headers if "(cycle)" in header]
    assert len(marked) == 2, f"expected exactly two '(cycle)' headers, got {marked} of {headers}"
    assert [header for header in marked if header.startswith("Candidates")] != [], f"Candidates column unmarked: {headers}"
    assert [header for header in marked if header.startswith("Top Candidate Share")] != [], f"Share column unmarked: {headers}"


# ===========================================================================
# E6 -- lobbying entity / client pages carry the scope on their outgoing links
# ===========================================================================
CYCLE_2024_WINDOW = (date(2023, 1, 1), date(2024, 12, 31))   # what ?period=cycle:2024 means


@pytest.fixture
def lobbying_links_app(w_lobbying_app):
    """w_lobbying_app (entity 1 'Test Entity', client 1 'Test Client', Alice matched to client 1) so that every link kind
    the two pages can print exists under ``?period=cycle:2024`` (2023-01-01..2024-12-31):
      * Top Committees: Alice's 700 to committee 200 on 2023-03-01 is the only gift in the window -> /committees/sbe/200
      * Clients / Represented By: client 1 is also registered in 2024 (the base row is 2026), so the table still lists it
        if an implementer windows it by reg_year
      * federal links: a fec_local_donor_matches row for Alice's donor key
      * 527 link: a lobbying_527_matches row for client 1
    """
    key = _alice_key(w_lobbying_app)
    return _topup(
        w_lobbying_app,
        ("INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2024)", ()),
        (
            "INSERT INTO fec_local_donor_matches (federal_donor_entity_key, local_donor_key, federal_donor_name, local_donor_name, "
            "match_method, confidence_score) VALUES ('fed-entity-alice', ?, 'Alice Donor', 'Alice Donor', 'test', 0.9)",
            (key,),
        ),
        (
            "INSERT INTO lobbying_527_matches (client_id, client_name, ein, org_name, score) "
            "VALUES (1, 'Test Client', '123456789', 'Test 527 Org', 0.9)",
            (),
        ),
    )


_COMMITTEE_LINK = re.compile(r"^/committees/sbe/\d+")
_CLIENT_LINK = re.compile(r"^/lobbying/client/\d+")
_ENTITY_LINK = re.compile(r"^/lobbying/\d+(?:\?|$)")
_DONOR_LINK = re.compile(r"^/donors/key/")
_FEDERAL_DONOR_LINK = re.compile(r"^/federal-finance/donors/")
_FEDERAL_MATCH_LINK = re.compile(r"^/federal-finance/matches/")
_ORG_527_LINK = re.compile(r"^/527/[^/?]+")

# Template lines (2026-10-26): entity_detail.html :61 committee, :114 and :148 client, :158 donor, :164 federal donor, :173 matched;
# client_detail.html :60 committee, :112 entity, :149 donor, :155 federal donor, :164 matched, :213 527 organisation.
ENTITY_PAGE_LINKS = {
    "committee": _COMMITTEE_LINK,
    "client": _CLIENT_LINK,                     # printed twice: the Clients table and the donor-match rows
    "local donor": _DONOR_LINK,
    "federal donor": _FEDERAL_DONOR_LINK,
    "federal matched profile": _FEDERAL_MATCH_LINK,
}
CLIENT_PAGE_LINKS = {
    "committee": _COMMITTEE_LINK,
    "entity": _ENTITY_LINK,
    "local donor": _DONOR_LINK,
    "federal donor": _FEDERAL_DONOR_LINK,
    "federal matched profile": _FEDERAL_MATCH_LINK,
    "527 organisation": _ORG_527_LINK,          # not in the brief's line list; the same defect, dropped by one parametrize id
}


def _assert_links_keep_the_window(app, url: str, pattern: re.Pattern, minimum: int = 1) -> None:
    links = [href for href in _hrefs(_get(app, url)) if pattern.match(href)]
    assert len(links) >= minimum, f"{url}: expected >= {minimum} link(s) matching {pattern.pattern}, found {links} (fixture lost a row?)"
    # Resolve each href with the production resolver: a hard-coded cycle=2026 beside period=cycle:2024 would WIN there.
    lost = [href for href in links if _window_of(_args(href), default="all") != CYCLE_2024_WINDOW]
    assert lost == [], f"{url}: links that reset the window (they should resolve to cycle 2024): {lost}"


@pytest.mark.parametrize("kind", sorted(ENTITY_PAGE_LINKS))
def test_lobbying_entity_links_keep_the_scope(lobbying_links_app, kind):
    """E6: /lobbying/<entity_id>?period=cycle:2024 -- each outgoing link resolves (through resolve_time_scope) to the same
    cycle-2024 window. The federal links also print a hard-coded ``cycle=2026`` today, which would out-rank ``period=``.

    Kills: today (no scope on any link); appending ``**scope_query`` while keeping ``cycle=2026`` on the two federal links;
    fixing only the first of two links of a kind (the client link prints twice); carrying the scope on the entity page but
    not the client page (separate test below)."""
    minimum = 2 if kind == "client" else 1
    _assert_links_keep_the_window(lobbying_links_app, "/lobbying/1?period=cycle:2024", ENTITY_PAGE_LINKS[kind], minimum)


@pytest.mark.parametrize("kind", sorted(CLIENT_PAGE_LINKS))
def test_lobbying_client_links_keep_the_scope(lobbying_links_app, kind):
    """E6: /lobbying/client/<client_id>?period=cycle:2024 -- same contract for the client page's links."""
    _assert_links_keep_the_window(lobbying_links_app, "/lobbying/client/1?period=cycle:2024", CLIENT_PAGE_LINKS[kind])


# ===========================================================================
# E5 -- analytics JSON endpoints answer for the window the page shows
# ===========================================================================
JSON_ANCHORS = (
    ("/analytics/networks", "/api/analytics/network"),          # networks.html:336
    ("/analytics/risk", "/api/analytics/anomalies"),            # risk.html:129
    ("/analytics/donors", "/api/analytics/concentration"),      # donors.html:76
    ("/analytics/geography", "/api/analytics/time-series"),     # geography.html:88
    ("/analytics/geography", "/api/analytics/geo"),             # geography.html:185
)
PAGE_SCOPES = (
    "period=all",
    "period=cycle:2024",
    "date_from=2025-02-01&date_to=2026-03-31",
)


@pytest.mark.parametrize(("page", "endpoint"), JSON_ANCHORS)
def test_json_endpoint_anchor_resolves_to_the_pages_window(analytics_app, page, endpoint):
    """E5: the page and the endpoint behind its "JSON endpoint" anchor must describe the same window. The anchor's query
    is run through the production resolver and compared with the page's own query; it never prints an empty
    ``date_from=`` / ``date_to=`` (the API resolves an empty pair to the CURRENT CYCLE, not to all time).

    Kills: today (networks / donors anchors carry no scope; risk / geography anchors print ``date_from=&date_to=`` under
    All time); carrying only dates (loses ``period=all``); carrying only ``period=`` and dropping a custom range; fixing one
    of geography's two anchors; hard-coding the current cycle."""
    problems = []
    for scope in PAGE_SCOPES:
        doc = _get(analytics_app, f"{page}?{scope}")
        anchors = [href for href in _hrefs(doc) if urlsplit(href).path == endpoint]
        if not anchors:
            problems.append(f"{scope}: no anchor to {endpoint} on {page}")
            continue
        wanted = _window_of({name: values[0] for name, values in parse_qs(scope).items()})
        for href in anchors:
            args = _args(href)
            empty = [name for name in ("date_from", "date_to") if args.get(name) == ""]
            if empty:
                problems.append(f"{scope}: {href} carries empty {empty}")
            got = _window_of(args)
            if got != wanted:
                problems.append(f"{scope}: {href} resolves to {got}, the page shows {wanted}")
    assert not problems, "\n" + "\n".join(problems)


def _focus_labels(payload: dict, node_type: str) -> list[str]:
    return sorted(node["label"] for node in payload["nodes"] if node["node_type"] == node_type)


def _focus_edges(payload: dict, edge_type: str, *, key: str = "target") -> dict[str, float]:
    """``{label of the edge's source or target (``key``): weight}`` for the edges of ``edge_type``."""
    labels = {node["id"]: node["label"] for node in payload["nodes"]}
    return {labels[edge[key]]: edge["weight"] for edge in payload["edges"] if edge["edge_type"] == edge_type}


def test_analytics_focus_sankey_resolves_the_scope_period(analytics_app):
    """E5: /api/analytics/network/focus-sankey reads raw ``date_from`` / ``date_to`` and ignores ``period``. Committee 1001's
    receipts: Donor One 5,000 (2026-01-05), Donor Two 2,000 (2026-01-06), Legacy Donor 4,000 (2024-06-10); payees: Vendor One
    (2026-01-08) and Legacy Vendor 600 (2024-07-15). ``period=cycle:2024`` (2023-01-01..2024-12-31) keeps only the Legacy pair;
    ``period=all`` keeps all five; a one-day custom range (2026-01-06) keeps Donor Two alone (guard: the raw dates work today).
    The committee->candidate edge comes from a cycle aggregate and is not part of the window contract.

    Kills: today (period ignored: all five under cycle:2024); resolving ``period`` but dropping raw dates; an exclusive
    bound (the one-day window); turning ``period=all`` into the current cycle."""
    client = analytics_app.test_client()
    base = "/api/analytics/network/focus-sankey?focus_node_id=committee:1001&focus_type=committee"
    payloads = {scope: client.get(f"{base}&{scope}").get_json() for scope in
                ("period=all", "period=cycle:2024", "date_from=2026-01-06&date_to=2026-01-06")}

    everything = payloads["period=all"]
    assert _focus_edges(everything, "donor_committee", key="source") == {
        "Donor One": 5000.0, "Donor Two": 2000.0, "Legacy Donor": 4000.0,
    }, "control: period=all"
    assert _focus_labels(everything, "vendor") == ["Legacy Vendor", "Vendor One"], "control: period=all"

    day = payloads["date_from=2026-01-06&date_to=2026-01-06"]
    assert _focus_edges(day, "donor_committee", key="source") == {"Donor Two": 2000.0}
    assert _focus_labels(day, "vendor") == []

    cycle = payloads["period=cycle:2024"]
    assert _focus_edges(cycle, "donor_committee", key="source") == {"Legacy Donor": 4000.0}
    assert _focus_edges(cycle, "committee_vendor") == {"Legacy Vendor": 600.0}


def test_federal_focus_sankey_resolves_the_scope_period(w_federal_app):
    """E5: /api/federal/network/focus-sankey reads only ``cycle``. Jane Donor gave Jackson 250 (2026-01-15, cycle 2026) and
    400 (2023-06-01, cycle 2024) and Smith 260 (2025-05-05) and 270 (2026-09-20). ``period=cycle:2024`` keeps only the
    400 gift to Jackson (no Smith, no Schedule B vendors: Jackson's B rows are all cycle 2026); ``period=all`` keeps
    Jackson 650 and Smith 530. Guard: the legacy ``cycle=2024`` still narrows the same way.

    Kills: today (period ignored: 650 / 530 under cycle:2024); resolving the period but not applying it to the Schedule B
    vendor edges; defaulting ``period=all`` to the current cycle."""
    client = w_federal_app.test_client()
    base = "/api/federal/network/focus-sankey?focus_label=Jane+Donor&focus_type=donor"
    everything = client.get(f"{base}&period=all").get_json()
    assert _focus_edges(everything, "donor_candidate") == {"JACKSON, JONATHAN": 650.0, "SMITH, JONATHAN": 530.0}, "control"
    assert _focus_labels(everything, "fed_vendor") == ["Fresh Media LLC", "Late Vendor", "Stale Print Co"], "control"

    for scope in ("cycle=2024", "period=cycle:2024"):
        narrowed = client.get(f"{base}&{scope}").get_json()
        assert _focus_edges(narrowed, "donor_candidate") == {"JACKSON, JONATHAN": 400.0}, scope
        assert _focus_labels(narrowed, "fed_vendor") == [], scope


def test_federal_focus_sankey_follows_a_custom_window(w_federal_app):
    """E5 (extension of the brief's ``period=cycle:2024`` pin, flagged in the report): a custom range is also resolved.
    Window 2026-01-01..2026-06-30: Jane's only in-window gift is Jackson 250 (2026-01-15); Smith's rows are 2025-05-05 and
    2026-09-20; Jackson's committee paid Fresh Media LLC 320 on 2026-01-01 (the first day) and Stale Print / Late Vendor
    outside it.

    Kills: mapping every non-cycle scope to 'no filter' (a cycle-only implementation); an exclusive lower bound."""
    narrowed = w_federal_app.test_client().get(
        f"/api/federal/network/focus-sankey?focus_label=Jane+Donor&focus_type=donor&{WINDOW_FEDERAL}"
    ).get_json()
    assert _focus_edges(narrowed, "donor_candidate") == {"JACKSON, JONATHAN": 250.0}
    assert _focus_labels(narrowed, "fed_vendor") == ["Fresh Media LLC"]


_SCOPE_KEY = re.compile(r"""['"](?:period|date_from|date_to)['"]|\b(?:period|date_from|date_to)\s*:|location\.(?:search|href)""")


def _strip_js_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", source, flags=re.S)


def test_custom_sankey_script_forwards_the_page_window():
    """E5 (source-pattern test: no browser here): ``fetchAndRenderCustomSankey`` builds its query from four form fields
    and never mentions the window, so the endpoint can only answer for its default. The function must name the scope
    (``period`` / ``date_from`` / ``date_to``) or read the page's own query string, directly or through a helper defined in
    the same file whose first 25 lines do.

    Kills: today (no scope token anywhere in the file); a comment that merely mentions the words (comments are stripped);
    a helper that is defined but never called from the function. Accepted hole: a token inside an unrelated string literal in
    the function passes; a scope handed over by a template data attribute fails this pattern and needs the pattern extended."""
    source = _strip_js_comments((ROOT / "webapp/static/js/analytics_networks.js").read_text(encoding="utf-8"))
    start = source.index("const fetchAndRenderCustomSankey")
    end = source.index("\n    };", start)
    body = source[start:end]
    assert "focus-sankey" in body, "precondition: the focus-sankey fetch moved out of fetchAndRenderCustomSankey"
    if _SCOPE_KEY.search(body):
        return
    lines = source.splitlines()
    called = set(re.findall(r"\b([A-Za-z_$][\w$]*)\(", body)) - {"fetch", "URLSearchParams", "String", "drawEmpty", "renderFocusCentricSankey"}
    for name in sorted(called):
        for number, line in enumerate(lines):
            if re.search(rf"(?:const|let|function)\s+{re.escape(name)}\b", line):
                if _SCOPE_KEY.search("\n".join(lines[number:number + 25])):
                    return
    pytest.fail("fetchAndRenderCustomSankey does not forward the page window (no period / date_from / date_to / location.search)")


# ===========================================================================
# Fixture clock (S-RB follow-up): three fixtures must not age out on 2027-01-01
# ===========================================================================
class _Clock2027(date):
    """The machine clock after the fixtures' data stops being 'this cycle'."""

    @classmethod
    def today(cls):
        return date(2027, 6, 1)


@pytest.mark.parametrize("fixture_name", ["analytics_app", "w_viz_app", "viz_lab_app"])
def test_fixture_freezes_the_time_scope_clock(fixture_name, request, monkeypatch):
    """tests/test_candidate_profile.py pins the resolver's clock with ``monkeypatch.setattr("webapp.utils.time_scope.date", ...)``.
    ``analytics_app`` (test_analytics_features.py), ``w_viz_app`` (test_scope_widgets.py) and ``app`` (test_experimental_viz_lab.py,
    imported here as ``viz_lab_app``) pin only ``database.analytics.current_election_cycle``, so every ``resolve_time_scope``
    default ("this cycle") moves on 2027-01-01 and their 2026 rows fall out of it.
    Behavioural, not a source pattern: this test sets the module's clock to 2027-06-01 BEFORE the fixture runs (the same
    ``monkeypatch`` instance, so the fixture's own patch, if any, lands after it), then asks the resolver for the current cycle.

    Kills: today (no fixture touches the clock: cycle 2028); a comment or unused string naming the hook (a pattern test would
    pass); patching a different module's ``date``. Accepted hole: a patch that merely re-reads the real clock passes today and
    fails only after 2027-01-01."""
    from webapp.utils import time_scope

    monkeypatch.setattr("webapp.utils.time_scope.date", _Clock2027)
    request.getfixturevalue(fixture_name)
    assert time_scope.current_cycle() == 2026, (
        f"{fixture_name} leaves webapp.utils.time_scope on the real clock: the current cycle reads "
        f"{time_scope.current_cycle()}, not the 2026 its rows belong to"
    )


# ===========================================================================
# Prewarm coherence: the two traps a lazy E3 / E2 fix falls into
# ===========================================================================
class _Clock20261026(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 26)


@pytest.fixture
def empty_route_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process RouteCache empty around the test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)

    def clear():
        for obj in gc.get_objects():
            if isinstance(obj, cache_backend.RouteCache):
                obj.invalidate()

    clear()
    yield
    clear()


def test_risk_prewarm_fills_the_entry_a_default_request_reads(analytics_app, monkeypatch, empty_route_caches):
    """``warm_analytics_caches`` builds the risk entry under its own copy of the page's axis (``risk_period_key``,
    webapp/routes/analytics.py ~1268, still ``axis="filed"``) because the scope cache key embeds the axis. Moving the page to
    another axis without moving that copy leaves the prewarm filling an entry nobody reads. Guard: passes today; it goes red
    the moment the route's axis changes alone.

    Kills: changing ``_parse_filters(axis=...)`` in ``risk()`` and forgetting the duplicate in the prewarm."""
    monkeypatch.setattr("webapp.utils.time_scope.date", _Clock20261026)
    analytics_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    calls: list[int] = []
    real = analytics_routes.get_anomaly_flags

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics_routes, "get_anomaly_flags", counting)
    analytics_routes.warm_analytics_caches(analytics_app.config["DATABASE_PATH"])
    warmed = len(calls)
    assert warmed >= 1, "precondition: the prewarm never built the risk entry"
    assert analytics_app.test_client().get("/analytics/risk").status_code == 200
    assert len(calls) == warmed, "a default /analytics/risk request recomputed the flags: the prewarm filled a different key"


def test_networks_prewarm_holds_the_windowed_vendor_graph(vendor_app, monkeypatch, empty_route_caches):
    """``warm_analytics_caches`` builds the default networks entry (cycle 2026 on the pinned clock = 2025-01-01..2026-12-31)
    with its own call of ``get_vendor_expenditure_network`` (webapp/routes/analytics.py ~1330) that passes no dates. Once the
    route windows that graph, a prewarm that does not would serve the ALL-TIME vendor graph to the default page from cache.
    Vendor Stale Co (2024-05-05) and Vendor Thin Co (600 inside the window, 1,500 all time) must be absent from the
    prewarmed default page.

    Kills: windowing the route but not the prewarm's vendor job. Red today for the same reason as the explicit-window test
    (nothing is windowed yet); after a route-only fix it stays red, which is the point."""
    monkeypatch.setattr("webapp.utils.time_scope.date", _Clock20261026)
    vendor_app.config["ROUTE_PERF_CACHE_ENABLED"] = True
    analytics_routes.warm_analytics_caches(vendor_app.config["DATABASE_PATH"])
    edges = _vendor_edges(_get(vendor_app, "/analytics/networks?load_mode=full"))
    assert edges.get("Vendor Mid Co") == 1500.0, f"precondition: the default vendor graph lost its in-window edges: {sorted(edges)}"
    stale = [name for name in ("Vendor Stale Co", "Vendor Thin Co") if name in edges]
    assert stale == [], f"the default page's cached vendor graph holds out-of-window vendors: {stale}"
