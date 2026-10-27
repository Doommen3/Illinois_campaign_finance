"""Page-level "200-empty" classification in scripts/audit/route_sweep.py (plan S-S1a, item C7).

Today a 200 page is "200-empty" when ANY EMPTY_MARKERS substring appears ANYWHERE
in the body. The Live Feed page is therefore flagged although all four of its
sections hold rows: one status line renders "No rows synced"
(webapp/templates/live_feed.html:13-16). Pinned contract: a marker only makes a
page empty when the page has no data rows. A row whose only content is the
marker text is a placeholder (live_feed.html:166, candidate_finance/list.html:
151-153, admin/federal_receipt_audit.html:85-87), not a data row, so real
empty-state pages must stay flagged.

The script has no pure body -> category function (scan_markers() returns marker
lists and RouteResult.category() maps them; sweep() composes the two inline), so
every case drives the real sweep() with make_request replaced by a canned
response: no network, no database, no new API for the implementer to guess.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "route_sweep.py"
MODULE_NAME = "route_sweep_under_test"


@pytest.fixture(scope="module")
def route_sweep():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules, so register first.
    sys.modules[MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(MODULE_NAME, None)


def category_of(route_sweep, monkeypatch, body, content_type="text/html; charset=utf-8"):
    """Category the sweep assigns to a 200 response carrying this body."""
    payload = body.encode("utf-8")

    def canned_response(url):
        # make_request's contract: (status, elapsed_s, body, note, sha256, truncated, content_type)
        return 200, 0.01, payload, "", "0" * 64, False, content_type

    monkeypatch.setattr(route_sweep, "make_request", canned_response)
    results = route_sweep.sweep("http://sweep.test", [("/page", "page")], {}, ["all"], concurrency=1)

    assert len(results) == 1
    assert results[0].status == 200
    return results[0].category()


# --- Fixtures. Data rows are hand-counted in the comment above each literal. ---

# live_feed.html:13-16 when one latest-date value is missing. Table-free.
STATUS_LINES_ONE_NO_ROWS = """
<div class="status-section">
    <p><strong>Local feed latest date:</strong> 2026-10-20</p>
    <p><strong>Federal Schedule B latest date:</strong> No rows synced</p>
</div>
"""

# 2 data rows (the <thead> header row is not a data row).
LOCAL_TABLE_2_ROWS = """
<h2>Local Candidate Donations (2)</h2>
<table class="data-table">
    <thead>
        <tr><th>Date</th><th>Donor</th><th>Amount</th></tr>
    </thead>
    <tbody>
        <tr>
            <td>2026-10-20</td>
            <td>Jane Donor</td>
            <td>$100.00</td>
        </tr>
        <tr>
            <td>2026-10-19</td>
            <td>John Donor</td>
            <td>$250.00</td>
        </tr>
    </tbody>
</table>
"""

# 1 data row.
FEDERAL_TABLE_1_ROW = """
<h2>Federal Candidate Receipts (1)</h2>
<table class="data-table">
    <thead>
        <tr><th>Date</th><th>Contributor</th><th>Amount</th></tr>
    </thead>
    <tbody>
        <tr>
            <td>2026-10-18</td>
            <td>Alex Contributor</td>
            <td>$75.00</td>
        </tr>
    </tbody>
</table>
"""

# 0 data rows: the tbody holds only the empty-state placeholder <tr>.
PLACEHOLDER_SECTION_NO_ROWS_SYNCED = """
<h2>Federal Committee Disbursements (0)</h2>
<table class="data-table">
    <thead>
        <tr><th>Date</th><th>Recipient</th><th>Amount</th></tr>
    </thead>
    <tbody>
        <tr><td colspan="3">No rows synced</td></tr>
    </tbody>
</table>
"""

# 0 data rows; wording from admin/federal_receipt_audit.html:86 (marker "no rows").
PLACEHOLDER_TABLE_NO_ROWS_FOUND = """
<table class="data-table">
    <thead>
        <tr><th>Date</th><th>Donor</th><th>Amount</th></tr>
    </thead>
    <tbody>
        <tr>
            <td colspan="9">No rows found for this filter.</td>
        </tr>
    </tbody>
</table>
"""

# 0 data rows; wording from federal_finance/geo_drilldown.html:90 (marker "no matching").
PLACEHOLDER_TABLE_NO_MATCHING = """
<table class="data-table">
    <thead>
        <tr><th>Geography</th><th>Total</th></tr>
    </thead>
    <tbody>
        <tr><td colspan="7">No matching drilldown rows for this geography and filter set.</td></tr>
    </tbody>
</table>
"""

# 0 data rows: a header row but a whitespace-only tbody (a Jinja loop with no {% else %}).
HEADER_ROW_AND_BLANK_TBODY = """
<table class="data-table">
    <thead>
        <tr><th>Date</th><th>Donor</th><th>Amount</th></tr>
    </thead>
    <tbody>

    </tbody>
</table>
"""


def page(*parts):
    return "<!doctype html><html><body>" + "".join(parts) + "</body></html>"


@pytest.mark.parametrize(
    "body",
    [
        # 3 data rows across two sections; the only marker is a status line.
        pytest.param(
            page(STATUS_LINES_ONE_NO_ROWS, LOCAL_TABLE_2_ROWS, FEDERAL_TABLE_1_ROW),
            id="status_line_says_no_rows_beside_populated_tables",
        ),
        # 3 data rows; the empty section comes FIRST (kills "inspect only the first tbody").
        pytest.param(
            page(PLACEHOLDER_SECTION_NO_ROWS_SYNCED, LOCAL_TABLE_2_ROWS),
            id="placeholder_section_before_populated_section",
        ),
        # 2 data rows; the empty section comes LAST (kills "inspect only the last tbody").
        pytest.param(
            page(LOCAL_TABLE_2_ROWS, PLACEHOLDER_SECTION_NO_ROWS_SYNCED),
            id="placeholder_section_after_populated_section",
        ),
    ],
)
def test_marker_text_in_one_section_does_not_make_a_page_with_rows_empty(route_sweep, monkeypatch, body):
    assert category_of(route_sweep, monkeypatch, body) == "200-ok"


@pytest.mark.parametrize(
    "body",
    [
        # Marker in a table-free page; kills "ignore markers outside tables".
        pytest.param(page(STATUS_LINES_ONE_NO_ROWS), id="status_line_says_no_rows_and_no_tables"),
        # Other EMPTY_MARKERS wording (search.html:316); kills "only the phrase 'no rows'".
        pytest.param(page('<p class="no-results">No results found for "zzz"</p>'), id="no_results_text_and_no_tables"),
        # The literal structural marker from EMPTY_MARKERS, as pinned for the sweep.
        pytest.param(page("<table><tbody></tbody></table>"), id="bare_empty_tbody"),
        # Header <tr> must not count as a data row (kills "any <tr> anywhere means rows").
        pytest.param(page(HEADER_ROW_AND_BLANK_TBODY), id="header_row_and_whitespace_only_tbody"),
        # A placeholder <tr> is not a data row (kills "any <tr> inside a tbody means rows").
        pytest.param(page(PLACEHOLDER_TABLE_NO_ROWS_FOUND), id="only_a_no_rows_found_placeholder_row"),
        pytest.param(page(PLACEHOLDER_TABLE_NO_MATCHING), id="only_a_no_matching_placeholder_row"),
        # Several sections, every one empty (kills "more than one tbody means rows").
        pytest.param(
            page(STATUS_LINES_ONE_NO_ROWS, PLACEHOLDER_SECTION_NO_ROWS_SYNCED, PLACEHOLDER_TABLE_NO_ROWS_FOUND),
            id="every_section_is_a_placeholder",
        ),
    ],
)
def test_page_without_any_data_rows_is_empty(route_sweep, monkeypatch, body):
    assert category_of(route_sweep, monkeypatch, body) == "200-empty"


@pytest.mark.parametrize("body", ["[]", "{}", '{"data":[]}', '{"results":[]}'])
def test_bare_empty_json_payload_stays_empty(route_sweep, monkeypatch, body):
    # Existing behaviour (scan_markers "empty-json-payload"); a table-aware rewrite must keep it.
    assert category_of(route_sweep, monkeypatch, body, content_type="application/json") == "200-empty"
