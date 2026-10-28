"""Registry of the widgets whose content depends on the page's time scope.

Plan step W1 / S-W1 (.claude/plans/profiles-races-dated-widgets.md). Data only: nothing
at runtime reads this module and it imports nothing from webapp.

docs/date_filtered_widgets.md section A is the dated, hand-built snapshot this list was
built from (``inv #N`` after a row is that snapshot's row number); this module is the
live list. tests/test_scope_widgets.py::test_every_windowed_tag_site_is_registered fails
when a windowed ``scope_tag(`` call under webapp/templates has no row here, so register a
new windowed widget in the change that adds its tag.

Widget fields
  endpoint    Flask endpoint of the page ("blueprint.function"), for building URLs with url_for.
  url         Flask URL rule of the page, placeholders kept (not a concrete URL).
  template    Path under webapp/templates.
  heading     A row with a scope_tag site: the text the completeness test parses from the tag's
              line (every {{ expression }} on it is shown as N). A row without one (untagged,
              inheriting another widget's tag, or unscoped): a descriptive label, not literal
              template text.
  kind        card (stat cards / stat lines), chart, list, map or table; "+"-joined, in
              alphabetical order, when one block mixes them.
  status      window | fixed | partial | mismatch | unscoped (below).
  axis        The inventory's axis cell: the axis handed to resolve_time_scope (what the bar
              says), plus "(eff: X)" when the SQL windows on a different date; "none" = no scope.
  ordinal     Rank among ALL scope_tag calls of the template that share the heading ("All time"
              ones included), in source order.
  covered_by  Existing test(s) that assert windowed content for the widget, in the notation
              below; None = no such test.
  note        Why a row is not "window", that it is untagged, where its JSON data lives, ...

status, taken from the inventory's "window applied in query?" cell (yes / no / partial / NO)
  window    yes: the query applies the page window on the axis the bar names.
  fixed     no: not windowed by design (tagged "All time", or untagged all-time content).
  partial   partial: the window reaches only some figures, or only whole cycles.
  mismatch  NO (inventory C1): tagged windowed but the query ignores the window; or inventory
            C3: the query windows on a different date than the bar's axis names.
  unscoped  A page outside the scope model, no bar (suggestion E8, plan decision J-S11).

covered_by notation: SR tests/test_scope_rollout.py, PS tests/test_profile_scope.py, WA
tests/test_webapp.py::TestWebApp, AF tests/test_analytics_features.py, AD
tests/test_analytics_date_semantics.py, FF tests/test_federal_fec.py, EV
tests/test_experimental_viz_lab.py, LR tests/test_lobbying_routes.py, CP
tests/test_candidate_profile.py, AW tests/test_scope_axis_and_windows.py, WB
tests/test_scope_windows_part_b.py, OS tests/test_outside_spending.py. Markers: [iw] asserts
with only in-window fixture rows, so it cannot catch a dropped window; [at] asserts the
all-time branch only; [fl] calls the feeder, not the page; no marker = rows inside and
outside the window, or output compared across scopes.
"""
from __future__ import annotations

from dataclasses import dataclass

STATUSES = ("window", "fixed", "partial", "mismatch", "unscoped")
KIND_PARTS = ("card", "chart", "list", "map", "table")


@dataclass(frozen=True)
class Widget:
    endpoint: str
    url: str
    template: str
    heading: str
    kind: str
    status: str
    axis: str
    ordinal: int = 1
    covered_by: str | None = None
    note: str = ""

    def __post_init__(self):
        where = f"{self.template} {self.heading!r}"
        if self.status not in STATUSES:
            raise ValueError(f"{where}: status {self.status!r} is not one of {STATUSES}")
        if not set(self.kind.split("+")) <= set(KIND_PARTS):
            raise ValueError(f"{where}: kind {self.kind!r} is not '+'-joined from {KIND_PARTS}")


WIDGETS: tuple[Widget, ...] = (
    # /  [main.index]
    Widget(
        "main.index", "/", "index.html",
        "Donor-Dependent Committees", "table", "fixed", "none",
    ),  # inv #1
    Widget(
        "main.index", "/", "index.html",
        "527 Directors Who Are Also Candidates", "table", "fixed", "none",
    ),  # inv #2
    Widget(
        "main.index", "/", "index.html",
        "Lobbying Clients With Donor Matches", "table", "fixed", "none",
    ),  # inv #3
    Widget(
        "main.index", "/", "index.html",
        "Matched Dark Money Expenditures", "card", "window", "received (eff: 527 expenditure date)",
    ),  # inv #4
    Widget(
        "main.index", "/", "index.html",
        "Local Candidate Rows", "card", "fixed", "none",
    ),  # inv #5
    Widget(
        "main.index", "/", "index.html",
        "Local Candidates", "card", "fixed", "none",
    ),  # inv #6
    Widget(
        "main.index", "/", "index.html",
        "Local Committees", "card", "fixed", "none",
    ),  # inv #7
    Widget(
        "main.index", "/", "index.html",
        "Local Receipts", "card", "mismatch", "received (eff: D-2 reporting_period_end)",
        covered_by="AW::test_home_local_receipts_card_windows_the_d2_fallback",
        note=(
            "bar axis label differs from the SQL date (inventory C3, still open: the page bar says 'by receipt date'); "
            "both D-2 paths window reporting_period_end (the isbe_d2_reports fallback since S-W6a)"
        ),
    ),  # inv #8
    Widget(
        "main.index", "/", "index.html",
        "Federal Candidates", "card", "fixed", "received (eff: cycle)",
        covered_by="AW::test_home_fec_cards_follow_the_window; AW::test_home_fec_cards_in_an_earlier_cycle_name_that_cycle",
        note="fixed '<cycle> Cycle' tag naming the window's cycle(s) (S-W6a, J-S14): fec_candidate_match has a cycle, no date",
    ),  # inv #9
    Widget(
        "main.index", "/", "index.html",
        "Federal Contributions", "card", "window", "received (eff: contribution_receipt_date; whole cycles: cycle)",
        covered_by="AW::test_home_fec_cards_follow_the_window; AW::test_home_fec_cards_in_an_earlier_cycle_name_that_cycle",
        note="a whole-cycle window (a cycle preset) keeps the cycle filter and the reported cycle total; any other bounded window counts the rows dated inside it (S-W6a, J-S14)",
    ),  # inv #10
    Widget(
        "main.index", "/", "index.html",
        "Federal Disbursements (Schedule B)", "card", "window", "received (eff: disbursement_date; whole cycles: cycle)",
        covered_by="AW::test_home_fec_cards_follow_the_window; AW::test_home_fec_cards_in_an_earlier_cycle_name_that_cycle",
        note="a whole-cycle window (a cycle preset) keeps the cycle filter; any other bounded window counts the rows dated inside it (S-W6a, J-S14); also WA::test_index_shows_schedule_b_e_metric_cards [iw: rows dated 2026-01, default cycle]",
    ),  # inv #11
    Widget(
        "main.index", "/", "index.html",
        "Federal Independent Expenditures (Schedule E)", "card", "window", "received (eff: expenditure_date; whole cycles: cycle)",
        covered_by="AW::test_home_fec_cards_follow_the_window; AW::test_home_fec_cards_in_an_earlier_cycle_name_that_cycle",
        note="a whole-cycle window (a cycle preset) keeps the cycle filter; any other bounded window counts the rows dated inside it (S-W6a, J-S14); also WA::test_index_shows_schedule_b_e_metric_cards [iw]",
    ),  # inv #12
    Widget(
        "main.index", "/", "index.html",
        "Matched Donor Pairs", "card", "fixed", "none",
    ),  # inv #13
    Widget(
        "main.index", "/", "index.html",
        "OpenBook Vendors Matched", "card", "fixed", "none",
    ),  # inv #14
    Widget(
        "main.index", "/", "index.html",
        "Archived Local Filings", "card", "fixed", "none",
    ),  # inv #15
    Widget(
        "main.index", "/", "index.html",
        "527 Organizations", "card", "fixed", "none",
    ),  # inv #16
    Widget(
        "main.index", "/", "index.html",
        "Total 527 Expenditures", "card", "window", "received (eff: 527 expenditure date)",
    ),  # inv #17
    Widget(
        "main.index", "/", "index.html",
        "Total 527 Contributions Received", "card", "window", "received (eff: 527 contribution date)",
    ),  # inv #18
    Widget(
        "main.index", "/", "index.html",
        "Director-Donor Overlaps", "card", "fixed", "none",
    ),  # inv #19
    Widget(
        "main.index", "/", "index.html",
        "Lobbying Entities", "card", "fixed", "none",
    ),  # inv #20
    Widget(
        "main.index", "/", "index.html",
        "Top Donors", "table", "window", "received",
        covered_by="SR::TestHomeScope::test_home_top_donors_follow_scope",
    ),  # inv #21
    Widget(
        "main.index", "/", "index.html",
        "Legacy Pages", "card", "fixed", "none",
    ),  # inv #22

    # /candidates  [main.candidates]
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Candidates", "card", "fixed", "none",
        note="inventory label: Candidates (State)",
    ),  # inv #23
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Committees", "card", "fixed", "none",
        note="inventory label: Committees (State)",
    ),  # inv #24
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Total Receipts", "card", "mismatch", "received (eff: D-2 reporting_period_end)",
        covered_by="AW::test_candidates_page_state_totals_window_the_d2_fallback",
        note=(
            "bar axis label differs from the SQL date (inventory C3, still open: the page bar says 'by receipt date'); "
            "both D-2 paths window reporting_period_end (the isbe_d2_reports fallback since S-W6a)"
        ),
    ),  # inv #25
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Total Expenditures", "card", "mismatch", "received (eff: D-2 reporting_period_end)",
        covered_by="AW::test_candidates_page_state_totals_window_the_d2_fallback",
        note=(
            "bar axis label differs from the SQL date (inventory C3, still open: the page bar says 'by receipt date'); "
            "both D-2 paths window reporting_period_end (the isbe_d2_reports fallback since S-W6a)"
        ),
    ),  # inv #26
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Candidates", "card", "fixed", "received (eff: cycle)",
        ordinal=2,
        covered_by="AW::test_candidates_page_fec_cards_follow_the_window",
        note=(
            "fixed '<cycle> Cycle' tag naming the window's cycle(s) (S-W6a, J-S14): fec_candidate_match has a cycle, "
            "no date; inventory label: Candidates (Federal)"
        ),
    ),  # inv #27
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Contributions", "card", "window", "received (eff: contribution_receipt_date; whole cycles: cycle)",
        covered_by="AW::test_candidates_page_fec_cards_follow_the_window",
        note="a whole-cycle window (a cycle preset) keeps the cycle filter and the reported cycle total; any other bounded window counts the rows dated inside it (S-W6a, J-S14); inventory label: Contributions (Federal)",
    ),  # inv #28
    Widget(
        "main.candidates", "/candidates", "candidates.html",
        "Matched Donors", "card", "fixed", "none",
    ),  # inv #29

    # /candidates/<int:candidate_id>  [candidates.candidate_detail]  (S-C1; not in the inventory)
    Widget(
        "candidates.candidate_detail", "/candidates/<int:candidate_id>", "candidates/detail.html",
        "Committees", "card+table", "partial", "received (eff: expended_date for Spent)",
        covered_by=(
            "CP::TestScope::{test_default_is_all_time_with_this_cycle_subtotal_and_isbe_data_through, "
            "test_raised_and_spent_follow_the_window_but_rows_order_and_last_activity_do_not}"
        ),
        note=(
            "rows, order and Last activity are all-time by design (J-C3); only Raised/Spent and the "
            "headline totals follow the window; 'This cycle' is the current cycle whatever the scope"
        ),
    ),
    Widget(
        "candidates.candidate_detail", "/candidates/<int:candidate_id>", "candidates/detail.html",
        "Candidacies", "table", "fixed", "none",
        covered_by="CP::TestScope::test_fixed_tables_ignore_the_window",
    ),
    Widget(
        "candidates.candidate_detail", "/candidates/<int:candidate_id>", "candidates/detail.html",
        "Money raised", "table", "fixed", "none",
        covered_by="CP::TestScope::test_fixed_tables_ignore_the_window",
        note="receipts per election cycle across all the candidate's committees; the tag text is the grain",
    ),
    Widget(
        "candidates.candidate_detail", "/candidates/<int:candidate_id>", "candidates/detail.html",
        "Outside spending (D-2 Part 9)", "card+table", "window", "received (eff: expended_date)",
        covered_by="OS::TestProfileSection::test_section_totals_follow_the_window",
        note=(
            "plan outside-spending-drilldown D1-D5: Part-9 rows attributed by first+last name token within the "
            "candidate's candidacy cycles; Supporting / Opposing / Total line + by-spender table follow the window; "
            "the section renders whenever the candidate has rows all-time"
        ),
    ),

    # /candidates/<int:candidate_id>/outside-spending  [candidates.candidate_outside_spending]
    Widget(
        "candidates.candidate_outside_spending", "/candidates/<int:candidate_id>/outside-spending",
        "candidates/outside_spending.html",
        "Line items", "table", "window", "transaction (eff: expended_date)",
        covered_by="OS::TestLineItemsPage::test_rows_follow_the_window_and_filters",
        note="one row per attributed Part-9 expenditure (share = amount / names on the row); S/O + spender filters",
    ),

    # /live-feed  [main.live_feed]
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Local Candidate Donations (N)", "table", "window", "received",
    ),  # inv #30
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal Candidate Donations (N)", "table", "window", "received (eff: transaction)",
    ),  # inv #31
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal Committee Disbursements (Schedule B) (N)", "table", "window", "received (eff: transaction)",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw: 'MEDIA BUY VENDOR' in live-feed HTML/CSV, rows in 2026]",
    ),  # inv #32
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal Independent Expenditures (Schedule E) (N)", "table", "window", "received (eff: transaction)",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw]",
    ),  # inv #33
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Local feed latest date: N", "list", "partial", "received",
        note="tag at the end of the line (S-W5): the newest date among the window's rows of the Local tab; the 'Federal latest report coverage' line is tagged All time; inventory label: Latest-date status block",
    ),  # inv #34
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal feed latest date: N", "list", "partial", "received",
        note="tag at the end of the line (S-W5): the newest date among the window's Schedule A rows; same status block as 'Local feed latest date: N'",
    ),  # inv #34
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal Schedule B latest date: N", "list", "partial", "received",
        note="tag at the end of the line (S-W5): the newest date among the window's Schedule B rows; same status block as 'Local feed latest date: N'",
    ),  # inv #34
    Widget(
        "main.live_feed", "/live-feed", "live_feed.html",
        "Federal Schedule E latest date: N", "list", "partial", "received",
        note="tag at the end of the line (S-W5): the newest date among the window's Schedule E rows; same status block as 'Local feed latest date: N'",
    ),  # inv #34

    # /search  [main.search]
    Widget(
        "main.search", "/search", "search.html",
        "Committees (N)", "table", "fixed", "none",
        covered_by="WB::test_search_committees_and_donors_carry_only_fixed_all_time_tags",
        note=(
            "fixed 'All time' tag (S-W6b, J-S14/J-S23): isbe_committees by name, legacy rows beside it all-time too; "
            "only a database without isbe_committees serves the section from the legacy fallback, windowed by "
            "contribution date, and the same tag call then shows the window"
        ),
    ),  # inv #35
    Widget(
        "main.search", "/search", "search.html",
        "Donors (N)", "table", "fixed", "none",
        covered_by="WB::test_search_committees_and_donors_carry_only_fixed_all_time_tags",
        note=(
            "fixed 'All time' tag (S-W6b, J-S14/J-S23): analytics_donor_summary by name, legacy rows beside it all-time "
            "too; windowed tag + windowed legacy fallback only when analytics_donor_summary is missing"
        ),
    ),  # inv #36
    Widget(
        "main.search", "/search", "search.html",
        "Candidates (N)", "table", "fixed", "none",
    ),  # inv #37
    Widget(
        "main.search", "/search", "search.html",
        "Reports (N)", "table", "mismatch", "received (eff: filed)",
        note=(
            "bar axis label differs from the SQL date (inventory C3); since S-W6b the section itself says 'by filing "
            "date' (WB::test_search_reports_section_says_by_filing_date_under_any_scope)"
        ),
    ),  # inv #38
    Widget(
        "main.search", "/search", "search.html",
        "Filed Doc IDs (N)", "table", "window", "received (eff: doc received_datetime)",
    ),  # inv #39
    Widget(
        "main.search", "/search", "search.html",
        "Donor Keys (N)", "table", "window", "received",
    ),  # inv #40

    # /committees/  [committees.list_committees]
    Widget(
        "committees.list_committees", "/committees/", "committees/list.html",
        "Total Contributions N", "table", "partial", "received",
        covered_by="SR::TestListScope::test_committees_list_isbe_totals_follow_scope",
        note="tag on the Total Contributions column header (S-W5; the N is its sort arrow): only that column is windowed, the committee rows are not; inventory label: Committees table (Total Contributions column)",
    ),  # inv #41

    # /committees/<int:committee_id>  [committees.committee_detail]
    Widget(
        "committees.committee_detail", "/committees/<int:committee_id>", "committees/detail.html",
        "Total Contributions: N", "card", "window", "transaction",
        note="inventory label: Total Contributions",
    ),  # inv #42
    Widget(
        "committees.committee_detail", "/committees/<int:committee_id>", "committees/detail.html",
        "Reports", "table", "window", "transaction (eff: filed; the section says 'by filing date')",
        note=(
            "windows reports.filed_date; the section names its own axis since S-W6a (the page bar stays 'by transaction "
            "date'; label pinned by AW::test_legacy_committee_reports_say_filing_date_while_the_page_stays_transaction_dated)"
        ),
    ),  # inv #43
    Widget(
        "committees.committee_detail", "/committees/<int:committee_id>", "committees/detail.html",
        "Recent Contributions", "table", "window", "transaction",
    ),  # inv #44

    # /committees/sbe/<int:committee_id_sbe>  [committees.committee_detail_by_sbe]
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Committee Information", "list", "fixed", "none",
        note=(
            "also served at /committees/<int:committee_id> (no legacy row); no windowed-content test "
            "(tag text pinned by "
            "PS::TestCommitteeProfileScope::test_defaults_to_all_time_with_cycle_subtotal)"
        ),
    ),  # inv #45
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Receipts Summary", "card", "window", "received",
        covered_by=(
            "PS::TestCommitteeProfileScope::{test_defaults_to_all_time_with_cycle_subtotal, "
            "test_cycle_preset_narrows_and_keeps_subtotal, test_legacy_period_key_still_works, "
            "test_canonical_committee_route_uses_same_profile}"
        ),
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #46
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Expenditures Summary", "card", "window", "received (eff: expended_date)",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #47
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Linked Candidates", "table", "fixed", "none",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #48
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Current Officers", "table", "fixed", "none",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #49
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Filing History", "table", "fixed", "none",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #50
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Top Donors", "table", "window", "received",
        covered_by="PS::TestCommitteeProfileScope::test_legacy_period_key_still_works ($1,000 Oldcycle donor under 2024cycle)",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #51
    Widget(
        "committees.committee_detail_by_sbe", "/committees/sbe/<int:committee_id_sbe>", "committees/detail_sbe.html",
        "Top Payees", "table", "window", "received (eff: expended_date)",
        note="also served at /committees/<int:committee_id> (no legacy row)",
    ),  # inv #52

    # /committees/<int:committee_id>/donors and /payees  [committees.committee_donors / committee_payees]
    # (plan S-PB / P3; added after the inventory snapshot)
    Widget(
        "committees.committee_donors", "/committees/<int:committee_id>/donors", "committees/donors.html",
        "Donors", "card+table", "window", "received",
        covered_by=(
            "tests/test_committee_rollups.py::TestFullListPages::{test_rows_follow_the_scope_window, "
            "test_q_does_not_escape_the_scope_window, test_search_form_submits_the_scope_with_the_filter}"
        ),
        note=(
            "every donor_key of the committee, 50 per page; the profile's Top Donors are its head; summary line "
            "'N unique donors, $X | This cycle: $Y' ('This cycle' is the current cycle whatever the scope)"
        ),
    ),
    Widget(
        "committees.committee_payees", "/committees/<int:committee_id>/payees", "committees/payees.html",
        "Payees", "card+table", "window", "transaction",
        covered_by=(
            "tests/test_committee_rollups.py::TestFullListPages::{test_rows_follow_the_scope_window, "
            "test_q_does_not_escape_the_scope_window, test_search_form_submits_the_scope_with_the_filter}"
        ),
        note=(
            "every normalized payee name of the committee, 50 per page; the profile's Top Payees are its head; "
            "windowed on expended_date, which the bar names (axis transaction)"
        ),
    ),

    # /donors/  [donors.list_donors]
    Widget(
        "donors.list_donors", "/donors/", "donors/list.html",
        "N total donors", "table", "window", "received",
        covered_by="SR::TestListScope::test_donor_list_totals_and_sort_links_follow_scope",
        note="tag on the count line above the table (S-W5); inventory label: Donors table + 'N total donors'",
    ),  # inv #53

    # /donors/<int:donor_id>  [donors.donor_detail]
    Widget(
        "donors.donor_detail", "/donors/<int:donor_id>", "donors/detail.html",
        "Total Contributed: N", "card", "window", "transaction",
        covered_by="PS::TestDonorProfileScope::test_legacy_donor_page_defaults_to_all_time_with_cycle_subtotal [at]",
        note="tag at the end of the line (S-W5); the 'This cycle' line is self-labelled and stays untagged; inventory label: Donor summary block (Total Contributed, This cycle, Number of Contributions)",
    ),  # inv #54
    Widget(
        "donors.donor_detail", "/donors/<int:donor_id>", "donors/detail.html",
        "Number of Contributions: N", "card", "window", "transaction",
        covered_by="PS::TestDonorProfileScope::test_legacy_donor_page_defaults_to_all_time_with_cycle_subtotal [at]",
        note="tag at the end of the line (S-W5); same summary block as 'Total Contributed: N'",
    ),  # inv #54
    Widget(
        "donors.donor_detail", "/donors/<int:donor_id>", "donors/detail.html",
        "Contributions", "table", "window", "transaction",
        covered_by="WA::test_donor_detail_respects_transaction_date_period_filter",
    ),  # inv #55

    # /donors/key/<path:donor_key>  [donors.donor_detail_by_key]
    Widget(
        "donors.donor_detail_by_key", "/donors/key/<path:donor_key>", "donors/detail_by_key.html",
        "Total Contributed: N", "card", "partial", "received",
        covered_by="PS::TestDonorProfileScope::{test_bulk_key_defaults_to_all_time_with_cycle_subtotal, test_bulk_key_cycle_preset_narrows_and_links_keep_scope, test_bulk_key_empty_scope_renders_zero_not_404} (key route only; entity route NONE)",
        note="tag at the end of the line (S-W5); the 'This cycle' line is self-labelled and stays untagged; bulk_receipts source only; also served at /donors/entity/<path:entity_id>; inventory label: Donor summary block (Total Contributed, This cycle, Contributions, Committees)",
    ),  # inv #56
    Widget(
        "donors.donor_detail_by_key", "/donors/key/<path:donor_key>", "donors/detail_by_key.html",
        "Number of Contributions: N", "card", "partial", "received",
        covered_by="PS::TestDonorProfileScope::{test_bulk_key_defaults_to_all_time_with_cycle_subtotal, test_bulk_key_cycle_preset_narrows_and_links_keep_scope, test_bulk_key_empty_scope_renders_zero_not_404} (key route only; entity route NONE)",
        note="tag at the end of the line (S-W5); same summary block as 'Total Contributed: N'",
    ),  # inv #56
    Widget(
        "donors.donor_detail_by_key", "/donors/key/<path:donor_key>", "donors/detail_by_key.html",
        "Committees Supported: N", "card", "partial", "received",
        covered_by="PS::TestDonorProfileScope::{test_bulk_key_defaults_to_all_time_with_cycle_subtotal, test_bulk_key_cycle_preset_narrows_and_links_keep_scope, test_bulk_key_empty_scope_renders_zero_not_404} (key route only; entity route NONE)",
        note="tag at the end of the line (S-W5); same summary block as 'Total Contributed: N'",
    ),  # inv #56
    Widget(
        "donors.donor_detail_by_key", "/donors/key/<path:donor_key>", "donors/detail_by_key.html",
        "Who They Donated To", "table", "window", "received",
        covered_by="PS::TestDonorProfileScope::{test_bulk_key_defaults..., test_bulk_key_cycle_preset_narrows...} (key route only)",
        note="also served at /donors/entity/<path:entity_id>",
    ),  # inv #57

    # /reports/  [reports.list_reports]
    Widget(
        "reports.list_reports", "/reports/", "reports/list.html",
        "N total reports", "list", "window", "filed",
        note="tag on the count line, which also heads the status badges below it (S-W5); inventory label: Report status badges (+ 'N total reports')",
    ),  # inv #58
    Widget(
        "reports.list_reports", "/reports/", "reports/list.html",
        "Filed N", "table", "window", "filed",
        covered_by="SR::TestListScope::test_reports_list_filters_by_filed_date_and_links_keep_scope; WA::test_reports_page_respects_filed_date_period_filter",
        note="tag on the Filed column header, the date the rows are filtered on (S-W5; the N is its sort arrow); inventory label: Reports table",
    ),  # inv #59

    # /candidate-finance/  [candidate_finance.list_candidate_finance]
    Widget(
        "candidate_finance.list_candidate_finance", "/candidate-finance/", "candidate_finance/list.html",
        "N total candidate-committee aggregate rows", "table", "partial", "other (report_period)",
        covered_by="WA::test_candidate_finance_scope_overrides_replace_period",
        note="tag on the count line above the table (S-W5); cycle or period-end; fallback source skips; inventory label: Candidate-committee finance table (+ row count)",
    ),  # inv #60

    # /candidate-finance/<int:candidate_id>/<int:committee_id>/itemized  [candidate_finance.candidate_committee_itemized]
    Widget(
        "candidate_finance.candidate_committee_itemized", "/candidate-finance/<int:candidate_id>/<int:committee_id>/itemized", "candidate_finance/itemized.html",
        "Rows: N", "table", "window", "received",
        note="tag on the Rows line above the table (S-W5); inventory label: Itemized receipts table (+ 'Rows' count)",
    ),  # inv #61

    # /candidate-finance/<int:candidate_id>/<int:committee_id>/itemized-expenditures  [candidate_finance.candidate_committee_itemized_expenditures]
    Widget(
        "candidate_finance.candidate_committee_itemized_expenditures", "/candidate-finance/<int:candidate_id>/<int:committee_id>/itemized-expenditures", "candidate_finance/itemized_expenditures.html",
        "Rows: N", "table", "window", "transaction",
        note="tag on the Rows line above the table (S-W5); inventory label: Itemized expenditures table (+ 'Rows' count)",
    ),  # inv #62

    # /d2-reconciliation/  [d2_receipts_recon.list_d2_receipts_recon]
    Widget(
        "d2_receipts_recon.list_d2_receipts_recon", "/d2-reconciliation/", "d2_receipts_recon/list.html",
        "N filing rows", "table", "window", "report_period",
        covered_by="AW::test_d2_reconciliation_windows_the_period_end_not_the_filing_date[/d2-reconciliation/]",
        note=(
            "tag on the subtitle only; the table is untagged; windows isbe_filed_docs.reporting_period_end and the bar "
            "says 'by reporting period end' since S-W6a; inventory label: D2 receipts reconciliation ('N filing rows' + table)"
        ),
    ),  # inv #63

    # /d2-expenditures-reconciliation/  [d2_expenditures_recon.list_d2_expenditures_recon]
    Widget(
        "d2_expenditures_recon.list_d2_expenditures_recon", "/d2-expenditures-reconciliation/", "d2_expenditures_recon/list.html",
        "N filing rows", "table", "window", "report_period",
        covered_by="AW::test_d2_reconciliation_windows_the_period_end_not_the_filing_date[/d2-expenditures-reconciliation/]",
        note=(
            "tag on the subtitle only; the table is untagged; windows isbe_filed_docs.reporting_period_end and the bar "
            "says 'by reporting period end' since S-W6a; inventory label: D2 expenditures reconciliation ('N filing rows' + table)"
        ),
    ),  # inv #64

    # /analytics/  [analytics.dashboard]
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Trend Months", "card", "partial", "received",
        note="tag on the card heading (S-W5): two of the four stat cards are windowed (this one and Network Nodes / Edges), NLP Categories and Reconciliation Outliers are tagged All time; also served at /analytics/overview; inventory label: Stat cards: Trend Months, NLP Categories, Reconciliation Outliers, Network Nodes / Edges",
    ),  # inv #65
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "NLP Categories", "card", "fixed", "none",
        note="tagged All time (S-W5): the NLP summary is not windowed; same stat-card block as 'Trend Months'",
    ),  # inv #65
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Reconciliation Outliers", "card", "fixed", "none",
        note="tagged All time (S-W5): the reconciliation rows are not windowed; same stat-card block as 'Trend Months'",
    ),  # inv #65
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Network Nodes / Edges", "card", "partial", "received",
        note="tag on the card heading (S-W5): counts the windowed network graph (full mode only); same stat-card block as 'Trend Months'",
    ),  # inv #65
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Top State Races", "table", "partial", "received",
        covered_by=(
            "AF::test_analytics_dashboard_route_loads [iw: default scope, "
            "$10,000/$1,500/15.0%/62.5%]; AF::test_top_state_races_can_sort_by_outside_spending "
            "[iw]; "
            "AF::test_top_state_races_shows_helper_when_nonzero_outside_is_below_default_top_rows "
            "[iw]"
        ),
        note=(
            "race set/candidate share cycle-based by design; their columns say '(cycle)' since S-W6a "
            "(AW::test_cycle_based_state_race_columns_are_marked); also served at /analytics/overview"
        ),
    ),  # inv #66
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Trend Visuals", "chart", "window", "received",
        covered_by="AF::test_analytics_date_range_filters [fl]",
        note=(
            "also served at /analytics/overview; inventory label: Trend Visuals (time-series line + "
            "MoM bars, 2 svg via #analytics-overview-time-series -> static/js/analytics_visuals.js)"
        ),
    ),  # inv #67
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Time-Series Preview", "table", "window", "received",
        covered_by="AF::test_analytics_date_range_filters [fl]",
        note="also served at /analytics/overview",
    ),  # inv #68
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "NLP Preview", "table", "fixed", "none",
        note="also served at /analytics/overview",
    ),  # inv #69
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Reconciliation Preview", "table", "fixed", "none",
        note="also served at /analytics/overview",
    ),  # inv #70
    Widget(
        "analytics.dashboard", "/analytics/", "analytics/overview.html",
        "Full-Mode Preview", "card", "window", "received",
        note=(
            "also served at /analytics/overview; inventory label: Full-Mode Preview (counts of "
            "network nodes, anomaly flags, concentration rows, geo states/cities)"
        ),
    ),  # inv #71

    # /analytics/state-races/<race_key>  [analytics.state_race_detail]
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Candidates", "card", "partial", "received (eff: cycle)",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="fixed '<cycle> Cycle' tag on the card heading (S-W5): the candidate set comes from isbe_race_candidacies by election_cycle, not dates; the other five cards follow the window; inventory label: Stat cards: Candidates, Donors, Contributions, Total Amount, Outside Spending (E), Outside Pressure Ratio",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Donors", "card", "partial", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="tag on the card heading (S-W5); same stat-card block as 'Candidates'",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Contributions", "card", "partial", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="tag on the card heading (S-W5); same stat-card block as 'Candidates'",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Total Amount", "card", "partial", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="tag on the card heading (S-W5); same stat-card block as 'Candidates'",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Outside Spending (E)", "card", "partial", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="tag on the card heading (S-W5); same stat-card block as 'Candidates'",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Outside Pressure Ratio", "card", "partial", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output ($10,000/$1,500 cycle vs $14,000/$2,100 all)",
        note="tag on the card heading (S-W5); same stat-card block as 'Candidates'",
    ),  # inv #72
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Candidate Committee Rows", "table", "mismatch", "received",
        note="election_cycle filter only",
    ),  # inv #73
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Committee Contribution Rows", "table", "window", "received",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output",
    ),  # inv #74
    Widget(
        "analytics.state_race_detail", "/analytics/state-races/<race_key>", "analytics/state_race_detail.html",
        "Outside Spending Rows (D-2 Part 9)", "table", "window", "received (eff: expended_date)",
        covered_by="AF::test_state_race_detail_period_propagation_changes_output",
    ),  # inv #75

    # /analytics/state-races/  [analytics.state_races]  (plan S-RB; added after the inventory snapshot)
    Widget(
        "analytics.state_races", "/analytics/state-races/", "analytics/state_races.html",
        "State Races", "card+table", "partial", "received",
        covered_by=(
            "tests/test_state_races_index.py::test_index_links_exactly_the_races_with_windowed_activity; "
            "tests/test_state_races_index.py::test_cached_index_does_not_leak_across_scope_cycle_and_sort"
        ),
        note=(
            "every race of the resolved candidate cycle (J-R4) + an 'N of M races have activity' line; receipts, "
            "donors, contributions and outside spending windowed, Top Candidate Share from cycle D-2 totals "
            "(same status as the overview's Top State Races; the Candidates and Top Candidate Share columns say "
            "'(cycle)' since S-W6a)"
        ),
    ),

    # /analytics/networks  [analytics.networks]
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "Network Graph Analytics", "chart+table", "window", "received",
        covered_by=(
            "AF::test_analytics_dashboard_full_mode_loads_heavy_sections [at]; "
            "AD::test_donor_committee_rows_uses_transaction_date_window [fl, underlying rows]"
        ),
        note="inventory label: Network Graph Analytics (Force Graph / Sankey Flow / Heatmap tabs + centrality table)",
    ),  # inv #76
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "Vendor Network tab", "chart", "window", "received (eff: expended_date)",
        covered_by="AW::test_networks_vendor_graph_follows_the_window; AW::test_networks_prewarm_holds_the_windowed_vendor_graph",
        note="no scope_tag of its own; inherits the 'Network Graph Analytics' tag; vendor graph windowed since S-W6a (#vendor-network-data)",
    ),  # inv #77
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "Money Flow tab", "chart", "window", "received",
        covered_by="AW::test_networks_vendor_graph_follows_the_window (vendor edges; donor edges: the 'Network Graph Analytics' probe)",
        note="no scope_tag of its own; inherits the 'Network Graph Analytics' tag; drawn from the network and vendor graphs, both windowed",
    ),  # inv #78
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "State-Federal tab", "chart", "fixed", "none",
        covered_by="AW::test_networks_state_federal_tab_is_tagged_all_time",
        note="overlap graph has no date args; the panel carries a fixed 'All time' tag since S-W6a (J-S14)",
    ),  # inv #79
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "Lobbying Bridge tab", "chart", "window", "received",
        covered_by="AF::test_lobbying_influence_graph_date_window_filters_edges [fl]",
        note="no scope_tag of its own; inherits the 'Network Graph Analytics' tag",
    ),  # inv #80
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "527 Dark Money tab", "chart", "window", "received",
        covered_by="AF::test_irs527_ecosystem_graph_date_window_filters_edges [fl]",
        note="no scope_tag of its own; inherits the 'Network Graph Analytics' tag",
    ),  # inv #81
    Widget(
        "analytics.networks", "/analytics/networks", "analytics/networks.html",
        "Custom Sankey tab", "chart", "window", "received",
        covered_by="AW::test_analytics_focus_sankey_resolves_the_scope_period; AW::test_custom_sankey_script_forwards_the_page_window",
        note=(
            "no scope_tag of its own; inherits the 'Network Graph Analytics' tag; the JS forwards the page's scope "
            "keys and the endpoint resolves them like the page (S-W6a)"
        ),
    ),  # inv #82

    # /analytics/risk  [analytics.risk]
    Widget(
        "analytics.risk", "/analytics/risk", "analytics/risk.html",
        "Risk Distribution Views", "chart", "window", "received",
        covered_by="AF::test_analytics_date_range_filters [fl]",
        note="windows the contribution date; the bar says 'by receipt date' since S-W6a",
    ),  # inv #83
    Widget(
        "analytics.risk", "/analytics/risk", "analytics/risk.html",
        "Risk and Anomaly Flags", "table", "window", "received",
        covered_by="AF::test_analytics_date_range_filters [fl]; AF::test_analytics_api_endpoints (/api/analytics/anomalies April window)",
        note="windows the contribution date; the bar says 'by receipt date' since S-W6a",
    ),  # inv #84
    Widget(
        "analytics.risk", "/analytics/risk", "analytics/risk.html",
        "D2 Reconciliation Signals", "table", "fixed", "none",
    ),  # inv #85

    # /analytics/donors  [analytics.donors]
    Widget(
        "analytics.donors", "/analytics/donors", "analytics/donors.html",
        "Donor Concentration Metrics", "table", "window", "received",
        covered_by="AD::test_donor_committee_rows_uses_transaction_date_window [fl, underlying rows]",
    ),  # inv #86
    Widget(
        "analytics.donors", "/analytics/donors", "analytics/donors.html",
        "NLP Spending Categories", "table", "fixed", "none",
    ),  # inv #87

    # /analytics/geography  [analytics.geography]
    Widget(
        "analytics.geography", "/analytics/geography", "analytics/geography.html",
        "Interactive Geo Views", "chart", "window", "received",
        covered_by="AD::test_donor_committee_rows_uses_transaction_date_window [fl, underlying rows]",
        note="inventory label: Interactive Geo Views (State Choropleth Grid, Top City Concentration)",
    ),  # inv #88
    Widget(
        "analytics.geography", "/analytics/geography", "analytics/geography.html",
        "Time-Series Intelligence", "table", "window", "received",
        covered_by=(
            "AF::test_analytics_date_range_filters [fl]; AF::test_analytics_api_endpoints "
            "(/api/analytics/time-series Feb-Mar window)"
        ),
    ),  # inv #89
    Widget(
        "analytics.geography", "/analytics/geography", "analytics/geography.html",
        "Geospatial Summary - States", "table", "window", "received",
    ),  # inv #90
    Widget(
        "analytics.geography", "/analytics/geography", "analytics/geography.html",
        "Geospatial Summary - Cities", "table", "window", "received",
    ),  # inv #91

    # /analytics/geo-drilldown  [analytics.geo_drilldown]
    Widget(
        "analytics.geo_drilldown", "/analytics/geo-drilldown", "analytics/geo_drilldown.html",
        "| Detail rows: N", "card+table", "window", "received",
        note="tag at the end of the aggregate line above the date-window help text (S-W5); related [at]: AF::test_analytics_geo_drilldown_route; inventory label: Geo drilldown summary + donor-committee rows",
    ),  # inv #92

    # /analytics/relationships  [analytics.relationships]
    Widget(
        "analytics.relationships", "/analytics/relationships", "analytics/relationships.html",
        "Donor Co-Giving Network", "chart+table", "fixed", "none",
    ),  # inv #93
    Widget(
        "analytics.relationships", "/analytics/relationships", "analytics/relationships.html",
        "Committee Similarity Network", "chart+table", "fixed", "none",
    ),  # inv #94
    Widget(
        "analytics.relationships", "/analytics/relationships", "analytics/relationships.html",
        "Candidate Competition Networks", "chart+table", "fixed", "none",
    ),  # inv #95
    Widget(
        "analytics.relationships", "/analytics/relationships", "analytics/relationships.html",
        "Lobbying Influence Graph", "chart+table", "window", "received",
        covered_by="AF::test_lobbying_influence_graph_date_window_filters_edges [fl]",
    ),  # inv #96
    Widget(
        "analytics.relationships", "/analytics/relationships", "analytics/relationships.html",
        "527 Ecosystem Graph", "chart+table", "window", "received",
        covered_by="AF::test_irs527_ecosystem_graph_date_window_filters_edges [fl]",
    ),  # inv #97

    # /federal-finance/  [federal_finance.list_federal_finance]
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Candidates", "card", "partial", "transaction (eff: cycle)",
        note="cycle tag on the card heading (S-W5; 'N Cycle' under a cycle preset, 'All cycles' otherwise): seed rows by cycle, no dates; the Schedule B Rows and Schedule E Rows cards are cycle-level too (Races: see its row), the two network cards follow the window; also served at /federal-finance/overview; inventory label: Stat cards: Candidates, Races, Network Donors, Network Total, Schedule B Rows, Schedule E Rows",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Races", "card", "partial", "transaction",
        note="counts the Top Federal Races rows (S-W6b): races of the window's cycle (else the current cycle) with Schedule A or E rows in the window, at most 12 (so not a race count); window tag + the fixed '<cycle> Cycle' tag (ordinal 2) unless the window is that cycle; same stat-card block as 'Candidates'",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Races", "card", "fixed", "transaction (eff: cycle)",
        ordinal=2,
        note="fixed '<cycle> Cycle' tag (S-W6b, J-S22): the races' candidates come from fec_candidate_match by that cycle; hidden when the window tag already names it",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Network Donors", "card", "partial", "transaction",
        note="tag on the card heading (S-W5): donors of the windowed donor-candidate edges (>= $100); same stat-card block as 'Candidates'",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Network Total", "card", "partial", "transaction",
        note="tag on the card heading (S-W5): total of the windowed donor-candidate edges (>= $100); same stat-card block as 'Candidates'",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Schedule B Rows", "card", "partial", "transaction (eff: cycle)",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw: ?cycle=2026 '$625.00 total disbursed', '$640.75 total independent expenditures']",
        note="cycle tag on the card heading (S-W5): _federal_schedule_b_e_metrics filters by cycle only, no dates; same stat-card block as 'Candidates'",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Schedule E Rows", "card", "partial", "transaction (eff: cycle)",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw: ?cycle=2026 '$625.00 total disbursed', '$640.75 total independent expenditures']",
        note="cycle tag on the card heading (S-W5): _federal_schedule_b_e_metrics filters by cycle only, no dates; same stat-card block as 'Candidates'",
    ),  # inv #98
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Top Federal Races", "table", "window", "transaction",
        covered_by=(
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]; "
            "FF::test_get_federal_race_analytics_respects_date_window [fl]; "
            "WB::{test_top_federal_races_table_lists_only_races_with_money_in_the_window, "
            "test_top_federal_races_candidates_column_counts_the_marked_cycle_only}"
        ),
        note=(
            "since S-W6b (J-S22, F1 closed): races of the window's own cycle (else the current cycle, as J-R4) with "
            "Schedule A or E rows in the window; the money columns follow the window, the candidate set and the "
            "Candidates column are that cycle's (fixed tag, ordinal 2); also served at /federal-finance/overview"
        ),
    ),  # inv #99
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Top Federal Races", "table", "fixed", "transaction (eff: cycle)",
        ordinal=2,
        covered_by="WB::test_top_federal_races_marks_the_cycle_granular_candidate_set",
        note="fixed '<cycle> Cycle' tag (S-W6b, J-S22): the candidate set (race membership, Candidates column) comes from fec_candidate_match by that cycle; hidden when the window tag already names it",
    ),  # inv #99
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Top Donors (Network)", "table", "window", "transaction",
        covered_by="FF::test_get_federal_network_graph_respects_date_window [fl]",
        note="also served at /federal-finance/overview",
    ),  # inv #100
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Top Candidate Nodes", "table", "window", "transaction",
        covered_by="FF::test_get_federal_network_graph_respects_date_window [fl]",
        note="also served at /federal-finance/overview",
    ),  # inv #101
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Geographic Snapshot - States", "table", "window", "transaction",
        covered_by="FF::test_get_federal_geographic_concentration_respects_date_window [fl]",
        note="also served at /federal-finance/overview",
    ),  # inv #102
    Widget(
        "federal_finance.list_federal_finance", "/federal-finance/", "federal_finance/overview.html",
        "Geographic Snapshot - Cities", "table", "window", "transaction",
        covered_by="FF::test_get_federal_geographic_concentration_respects_date_window [fl]",
        note="also served at /federal-finance/overview",
    ),  # inv #103

    # /federal-finance/candidates  [federal_finance.federal_candidates]
    Widget(
        "federal_finance.federal_candidates", "/federal-finance/candidates", "federal_finance/candidates.html",
        "N candidate seed rows", "table", "partial", "transaction (eff: cycle)",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw: ?cycle=2026 '$1,200.00']",
        note="cycle-granular only; dates ignored; inventory label: 'N candidate seed rows' + candidate table",
    ),  # inv #104

    # /federal-finance/networks  [federal_finance.federal_networks]
    Widget(
        "federal_finance.federal_networks", "/federal-finance/networks", "federal_finance/networks.html",
        "Federal Donor-Candidate Network", "chart+table", "window", "transaction",
        covered_by="FF::test_get_federal_network_graph_respects_date_window [fl]",
    ),  # inv #105
    Widget(
        "federal_finance.federal_networks", "/federal-finance/networks", "federal_finance/networks.html",
        "Ranked Donor-Candidate Flows", "table", "window", "transaction",
        covered_by="FF::test_get_federal_network_graph_respects_date_window [fl]",
        note="tag on the h3 above the flows table (S-W5); the table is built client-side from the windowed graph payload",
    ),  # inv #106
    Widget(
        "federal_finance.federal_networks", "/federal-finance/networks", "federal_finance/networks.html",
        "Custom Sankey", "chart", "partial", "transaction (eff: cycle)",
        note=(
            "the endpoint resolves period / cycle / date_from / date_to since S-W6a "
            "(AW::test_federal_focus_sankey_follows_a_custom_window), but this page's JS still sends only `cycle`"
        ),
    ),  # inv #107

    # /federal-finance/donor-intelligence  [federal_finance.federal_donor_intelligence]
    Widget(
        "federal_finance.federal_donor_intelligence", "/federal-finance/donor-intelligence", "federal_finance/donor_intelligence.html",
        "Donor Segmentation", "table", "window", "transaction",
        covered_by="FF::test_get_federal_donor_segmentation_respects_date_window [fl]",
    ),  # inv #108
    Widget(
        "federal_finance.federal_donor_intelligence", "/federal-finance/donor-intelligence", "federal_finance/donor_intelligence.html",
        "Donor Network Clustering", "table", "window", "transaction",
        covered_by="FF::test_get_federal_donor_network_clusters_respects_date_window [fl]",
    ),  # inv #109
    Widget(
        "federal_finance.federal_donor_intelligence", "/federal-finance/donor-intelligence", "federal_finance/donor_intelligence.html",
        "Community Treemap / Sunburst", "chart", "window", "transaction",
        covered_by="FF::test_get_federal_donor_network_clusters_respects_date_window [fl]",
    ),  # inv #110

    # /federal-finance/money-flow  [federal_finance.federal_money_flow]
    Widget(
        "federal_finance.federal_money_flow", "/federal-finance/money-flow", "federal_finance/money_flow.html",
        "Layer Summary", "card+table", "window", "transaction",
        covered_by="FF::test_get_federal_multilayer_network_graph_respects_date_window [fl]",
        note="inventory label: Layer Summary (stat lines + layer table)",
    ),  # inv #111
    Widget(
        "federal_finance.federal_money_flow", "/federal-finance/money-flow", "federal_finance/money_flow.html",
        "Top Multi-layer Flows", "table", "window", "transaction",
        covered_by="FF::test_get_federal_multilayer_network_graph_respects_date_window [fl]",
    ),  # inv #112
    Widget(
        "federal_finance.federal_money_flow", "/federal-finance/money-flow", "federal_finance/money_flow.html",
        "Cross-Role Organizations (Donor and Payee)", "table", "window", "transaction",
        covered_by="FF::test_get_federal_cross_role_organizations_respects_date_window [fl]",
    ),  # inv #113

    # /federal-finance/influence  [federal_finance.federal_influence]
    Widget(
        "federal_finance.federal_influence", "/federal-finance/influence", "federal_finance/influence.html",
        "Top Donors by Influence", "table", "window", "transaction",
        covered_by="FF::test_get_federal_influence_scores_respects_date_window [fl]",
    ),  # inv #114
    Widget(
        "federal_finance.federal_influence", "/federal-finance/influence", "federal_finance/influence.html",
        "Top Candidates by Influence", "table", "window", "transaction",
        covered_by="FF::test_get_federal_influence_scores_respects_date_window [fl]",
    ),  # inv #115

    # /federal-finance/follow-the-money  [federal_finance.federal_follow_the_money]
    Widget(
        "federal_finance.federal_follow_the_money", "/federal-finance/follow-the-money", "federal_finance/follow_the_money.html",
        "", "list", "window", "transaction",
        covered_by="FF::test_get_top_donor_entities_respects_date_window [fl]",
        note="tag right after the donor <select> (S-W5); its line holds no text, hence the empty heading; the options list the window's top 200 donors with their window totals; inventory label: Donor picker (top 200 donors)",
    ),  # inv #116
    Widget(
        "federal_finance.federal_follow_the_money", "/federal-finance/follow-the-money", "federal_finance/follow_the_money.html",
        "Donors: N", "chart", "window", "transaction",
        covered_by="FF::test_get_federal_follow_the_money_respects_date_window [fl]",
        note="tag at the end of the graph summary line 'Full network: N nodes | N edges | ... | Donors: N' (S-W5); inventory label: Follow-the-money network graph",
    ),  # inv #117
    Widget(
        "federal_finance.federal_follow_the_money", "/federal-finance/follow-the-money", "federal_finance/follow_the_money.html",
        "Reachable Candidates", "table", "window", "transaction",
        covered_by="FF::test_get_federal_follow_the_money_respects_date_window [fl]",
    ),  # inv #118
    Widget(
        "federal_finance.federal_follow_the_money", "/federal-finance/follow-the-money", "federal_finance/follow_the_money.html",
        "Reachable Committees", "table", "window", "transaction",
        covered_by="FF::test_get_federal_follow_the_money_respects_date_window [fl]",
    ),  # inv #119

    # /federal-finance/geography  [federal_finance.federal_geography]
    Widget(
        "federal_finance.federal_geography", "/federal-finance/geography", "federal_finance/geography.html",
        "Top States", "table", "window", "transaction",
        covered_by="FF::test_get_federal_geographic_concentration_respects_date_window [fl]",
    ),  # inv #120
    Widget(
        "federal_finance.federal_geography", "/federal-finance/geography", "federal_finance/geography.html",
        "Top Cities", "table", "window", "transaction",
        covered_by="FF::test_get_federal_geographic_concentration_respects_date_window [fl]",
    ),  # inv #121
    Widget(
        "federal_finance.federal_geography", "/federal-finance/geography", "federal_finance/geography.html",
        "Race Geographic Concentration", "table", "window", "transaction",
        covered_by="FF::test_get_federal_geographic_concentration_respects_date_window [fl]",
    ),  # inv #122

    # /federal-finance/geo-drilldown  [federal_finance.federal_geo_drilldown]
    Widget(
        "federal_finance.federal_geo_drilldown", "/federal-finance/geo-drilldown", "federal_finance/geo_drilldown.html",
        "| Detail rows: N", "card+table", "window", "transaction",
        covered_by="FF::test_get_federal_geo_drilldown_respects_date_window [fl]; WA::test_federal_finance_page_loads_with_synced_rows [iw]",
        note="tag at the end of the aggregate line above the table (S-W5); inventory label: Federal geo drilldown summary + donor-candidate rows",
    ),  # inv #123

    # /federal-finance/matching  [federal_finance.federal_matching]
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Federal Donors Considered", "card", "partial", "transaction",
        covered_by="FF::test_get_federal_local_donor_matches_respects_date_window [fl] (asserts federal_donors_considered only)",
        note="tag on the card heading (S-W5): the federal side follows the window, the local side is all-time by design (Local Donors Considered carries a fixed 'All time' tag since S-W6a, J-S14); inventory label: Stat cards: Federal Donors Considered, Local Donors Considered, Federal Donors Matched, Match Rate",
    ),  # inv #124
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Local Donors Considered", "card", "fixed", "none",
        note="fixed 'All time' tag since S-W6a (J-S14): the local donor pool is not windowed; same stat-card block as 'Federal Donors Considered'",
    ),  # inv #124
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Federal Donors Matched", "card", "partial", "transaction",
        note="tag on the card heading (S-W5): federal donors in the window matched against the all-time local pool; same stat-card block as 'Federal Donors Considered'",
    ),  # inv #124
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Match Rate", "card", "partial", "transaction",
        note="tag on the card heading (S-W5): windowed federal donors matched / considered against the all-time local pool; same stat-card block as 'Federal Donors Considered'",
    ),  # inv #124
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Match Method Tiers", "card", "partial", "transaction",
        covered_by="FF::test_get_federal_local_donor_matches_respects_date_window [fl] (federal pool count only)",
        note=(
            "federal side windowed, local side all-time by design and tagged fixed 'All time' since S-W6a "
            "(AW::test_matching_page_local_side_is_tagged_all_time); inventory label: Match Method Tiers (tier counts + "
            "overlap summary)"
        ),
    ),  # inv #125
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Local vs Federal Overlap Bubble View", "chart", "partial", "transaction",
        note="local axis all-time by design, tagged fixed 'All time' since S-W6a (AW::test_matching_page_local_side_is_tagged_all_time)",
    ),  # inv #126
    Widget(
        "federal_finance.federal_matching", "/federal-finance/matching", "federal_finance/matching.html",
        "Matched Donor Rows", "table", "partial", "transaction",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw]",
        note="local total all-time by design, tagged fixed 'All time' since S-W6a (AW::test_matching_page_local_side_is_tagged_all_time)",
    ),  # inv #127

    # /federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>  [federal_finance.federal_matched_donor_profile]
    Widget(
        "federal_finance.federal_matched_donor_profile",
        "/federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>", "federal_finance/match_profile.html",
        "Federal Summary", "card", "window", "transaction",
        covered_by="FF::test_get_federal_donor_detail_respects_date_window [fl]",
    ),  # inv #128
    Widget(
        "federal_finance.federal_matched_donor_profile",
        "/federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>", "federal_finance/match_profile.html",
        "Local Summary", "card", "fixed", "none",
    ),  # inv #129
    Widget(
        "federal_finance.federal_matched_donor_profile",
        "/federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>", "federal_finance/match_profile.html",
        "Federal Contributions", "table", "window", "transaction",
        covered_by="FF::test_get_federal_donor_detail_respects_date_window [fl]",
    ),  # inv #130
    Widget(
        "federal_finance.federal_matched_donor_profile",
        "/federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>", "federal_finance/match_profile.html",
        "Local Donations (Committee Breakdown)", "table", "fixed", "none",
        covered_by="WA::test_federal_finance_page_loads_with_synced_rows [iw: 'Friends of Springfield']",
    ),  # inv #131

    # /federal-finance/donors/<donor_entity_key>  [federal_finance.federal_donor_detail]
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Candidates Donated To: N", "card", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}; FF::test_get_federal_donor_detail_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); the 'This cycle' line is self-labelled and stays untagged; inventory label: Donor summary (Candidates, Committees, Total Contributions, Total Amount, This cycle, Date Range)",
    ),  # inv #132
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Committees: N", "card", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}; FF::test_get_federal_donor_detail_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates Donated To: N'",
    ),  # inv #132
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Total Contributions: N", "card", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}; FF::test_get_federal_donor_detail_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates Donated To: N'",
    ),  # inv #132
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Total Amount: N", "card", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}; FF::test_get_federal_donor_detail_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates Donated To: N'",
    ),  # inv #132
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Date Range: N to N", "card", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}; FF::test_get_federal_donor_detail_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates Donated To: N'",
    ),  # inv #132
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Donations by Candidate", "table", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::{test_donor_defaults_to_all_time_with_cycle_subtotal, test_donor_cycle_preset_narrows}",
    ),  # inv #133
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Name Variants", "table", "window", "transaction",
        covered_by="FF::test_get_federal_donor_detail_respects_date_window [fl]",
    ),  # inv #134
    Widget(
        "federal_finance.federal_donor_detail", "/federal-finance/donors/<donor_entity_key>", "federal_finance/donor_detail.html",
        "Recent Contributions", "table", "window", "transaction",
        covered_by="PS::TestFederalProfileScope::test_donor_cycle_preset_narrows ($250 vs $650, page-wide)",
    ),  # inv #135

    # /federal-finance/committees/<committee_id>/receipts  [federal_finance.federal_committee_receipts]
    Widget(
        "federal_finance.federal_committee_receipts", "/federal-finance/committees/<committee_id>/receipts", "federal_finance/committee_receipts.html",
        "Date range: N to N", "card", "partial", "transaction",
        covered_by="FF::test_get_federal_committee_receipts_respects_date_window [fl]",
        note="tag at the end of the summary line 'Receipts | Unique donors | Total receipts | Date range' (S-W5); inventory label: Committee receipt summary (receipts, unique donors, total, date range; transfer-source stats)",
    ),  # inv #136
    Widget(
        "federal_finance.federal_committee_receipts", "/federal-finance/committees/<committee_id>/receipts", "federal_finance/committee_receipts.html",
        "Recipient committees: N.", "card", "partial", "transaction (eff: cycle)",
        note="fixed '<cycle> Cycle' tag (S-W5) at the end of the transfer-source stats paragraph: fec_transfer_source_committees has one row per cycle (the scope's cycle, else the latest), no dates; same summary block as 'Date range: N to N'",
    ),  # inv #136
    Widget(
        "federal_finance.federal_committee_receipts",
        "/federal-finance/committees/<committee_id>/receipts", "federal_finance/committee_receipts.html",
        "Itemized Receipts (Schedule A)", "table", "window", "transaction",
        covered_by=(
            "FF::test_get_federal_committee_receipts_respects_date_window [fl]; "
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]"
        ),
    ),  # inv #137

    # /federal-finance/races/<office_code>/<district_code>/outside-spending  [federal_finance.federal_race_outside_spending]
    Widget(
        "federal_finance.federal_race_outside_spending", "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Candidates: N", "card", "fixed", "transaction (eff: cycle)",
        note="fixed '<cycle> Cycle' tag (S-W5): the race's candidate set is the cycle's (fec_candidate_match by cycle), not dates; the Schedule E figures below follow the window; inventory label: Race summary (Candidates, Total Schedule E Amount, Transaction Count, Latest Expenditure Date)",
    ),  # inv #138
    Widget(
        "federal_finance.federal_race_outside_spending", "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Total Schedule E Amount: N", "card", "window", "transaction",
        covered_by="FF::test_get_federal_race_outside_spending_respects_date_window [fl]",
        note="tag at the end of the line (S-W5): the page's cycle label when the scope is all time (a race page is always one cycle), else the window label; same summary block as 'Candidates: N'",
    ),  # inv #138
    Widget(
        "federal_finance.federal_race_outside_spending", "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Transaction Count: N", "card", "window", "transaction",
        covered_by="FF::test_get_federal_race_outside_spending_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates: N'",
    ),  # inv #138
    Widget(
        "federal_finance.federal_race_outside_spending", "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Latest Expenditure Date: N", "card", "window", "transaction",
        covered_by="FF::test_get_federal_race_outside_spending_respects_date_window [fl]",
        note="tag at the end of the line (S-W5); same summary block as 'Candidates: N'",
    ),  # inv #138
    Widget(
        "federal_finance.federal_race_outside_spending",
        "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Top IE Committees", "table", "window", "transaction",
        covered_by=(
            "FF::test_get_federal_race_outside_spending_respects_date_window [fl]; "
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]"
        ),
    ),  # inv #139
    Widget(
        "federal_finance.federal_race_outside_spending",
        "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Top Payees", "table", "window", "transaction",
        covered_by="FF::test_get_federal_race_outside_spending_respects_date_window [fl]",
    ),  # inv #140
    Widget(
        "federal_finance.federal_race_outside_spending",
        "/federal-finance/races/<office_code>/<district_code>/outside-spending", "federal_finance/race_outside_spending.html",
        "Independent Expenditures (Schedule E)", "table", "window", "transaction",
        covered_by=(
            "FF::test_get_federal_race_outside_spending_respects_date_window [fl]; "
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]"
        ),
    ),  # inv #141

    # /federal-finance/<candidate_id>  [federal_finance.federal_candidate_detail]
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Cycle: N", "card", "mismatch", "transaction (eff: cycle)",
        covered_by="PS::TestFederalProfileScope::test_candidate_all_time_spans_cycles_and_tags_reported_totals (asserts fixed '2026 Cycle' tag)",
        note="inline fixed-cycle tag; every aggregate line of the summary block carries the same fixed tag since S-W5 (Committees, Contributions, Donors, Total Amount, the three date ranges); cycle-level summary, no dates; inventory label: Candidate summary block (Cycle, Total Source, Reported Disbursements, Contributions, Donors, Total Amount, date ranges)",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Total Source: FEC candidate totals endpoint (reported receipts)", "card", "mismatch", "transaction (eff: cycle)",
        covered_by="PS::TestFederalProfileScope::test_candidate_all_time_spans_cycles_and_tags_reported_totals (asserts fixed '2026 Cycle' tag)",
        note="inline fixed-cycle tag in the same summary block as 'Cycle: N'; cycle-level summary, no dates",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Reported Disbursements: N", "card", "mismatch", "transaction (eff: cycle)",
        covered_by="PS::TestFederalProfileScope::test_candidate_all_time_spans_cycles_and_tags_reported_totals (asserts fixed '2026 Cycle' tag)",
        note="inline fixed-cycle tag in the same summary block as 'Cycle: N'; cycle-level summary, no dates",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Committees: N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Contributions: N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Donors: N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Total Amount: N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Date Range: N to N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Schedule B Date Range: N to N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Schedule E Date Range: N to N", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag at the end of the line (S-W5); cycle-level summary, no dates; same summary block as 'Cycle: N'",
    ),  # inv #142
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Money In (A)", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag on the card heading (S-W5); derived from the cycle summary; inventory label: Stat cards: Money In (A), Money Out (B), Outside Spending (E), Net Flow",
    ),  # inv #143
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Money Out (B)", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag on the card heading (S-W5); derived from the cycle summary; same stat-card block as 'Money In (A)'",
    ),  # inv #143
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Outside Spending (E)", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag on the card heading (S-W5); derived from the cycle summary; same stat-card block as 'Money In (A)'",
    ),  # inv #143
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Net Flow", "card", "mismatch", "transaction (eff: cycle)",
        note="fixed-cycle tag on the card heading (S-W5); derived from the cycle summary; same stat-card block as 'Money In (A)'",
    ),  # inv #143
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Committees", "table", "partial", "transaction",
        note="cycle-only; dates ignored",
    ),  # inv #144
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Top Donors", "table", "window", "transaction",
        covered_by=(
            "PS::TestFederalProfileScope::{test_candidate_defaults_to_current_cycle, "
            "test_candidate_all_time_spans_cycles_and_tags_reported_totals, "
            "test_candidate_explicit_cycle_param_still_scopes}; "
            "FF::test_get_federal_candidate_detail_respects_date_window [fl]"
        ),
    ),  # inv #145
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Recent Contributions", "table", "window", "transaction",
        covered_by=(
            "PS::TestFederalProfileScope::test_candidate_explicit_cycle_param_still_scopes ($400 vs "
            "$250, page-wide); FF::test_get_federal_candidate_detail_respects_date_window [fl]"
        ),
    ),  # inv #146
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Recent Disbursements (Schedule B)", "table", "window", "transaction",
        covered_by=(
            "FF::test_get_federal_candidate_detail_respects_date_window [fl]; "
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]"
        ),
    ),  # inv #147
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Explicit Transfer Chains (Schedule B IDs)", "table", "window", "transaction",
        covered_by="AW::test_federal_candidate_transfer_chains_follow_the_window",
        note="disbursement_date window under a non-cycle scope since S-W6a",
    ),  # inv #148
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Independent Expenditures (Schedule E)", "table", "window", "transaction",
        covered_by=(
            "FF::test_get_federal_candidate_detail_respects_date_window [fl]; "
            "WA::test_federal_finance_page_loads_with_synced_rows [iw]"
        ),
    ),  # inv #149
    Widget(
        "federal_finance.federal_candidate_detail", "/federal-finance/<candidate_id>", "federal_finance/detail.html",
        "Cross-Role Organizations (A and B/E)", "table", "window", "transaction",
        covered_by="AW::test_federal_candidate_cross_role_organizations_follow_the_window",
        note="the detail's date_from / date_to reach the feeder since S-W6a",
    ),  # inv #150

    # /527/  [irs527.list_orgs]
    Widget(
        "irs527.list_orgs", "/527/", "irs527/list.html",
        "Total Contributions", "table", "partial", "transaction",
        covered_by="SR::TestListScope::test_527_list_windowed_totals_do_not_fall_back_to_report_totals",
        note="tag on the column header (S-W5): only the three money columns are windowed, the organization rows are not; inventory label: 527 organizations table (Total Contributions, Total Expenditures, Contributions Received (A) columns)",
    ),  # inv #151
    Widget(
        "irs527.list_orgs", "/527/", "irs527/list.html",
        "Total Expenditures", "table", "partial", "transaction",
        covered_by="SR::TestListScope::test_527_list_windowed_totals_do_not_fall_back_to_report_totals",
        note="tag on the column header (S-W5); same table as 'Total Contributions'",
    ),  # inv #151
    Widget(
        "irs527.list_orgs", "/527/", "irs527/list.html",
        "Contributions Received (A)", "table", "partial", "transaction",
        covered_by="SR::TestListScope::test_527_list_windowed_totals_do_not_fall_back_to_report_totals",
        note="tag on the column header (S-W5); same table as 'Total Contributions'",
    ),  # inv #151

    # /527/<path:ein>  [irs527.org_detail]
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Organization Details", "list", "fixed", "none",
        note="no windowed-content test (tag pinned by PS::TestIrs527ProfileScope::test_defaults_to_all_time_with_cycle_subtotal)",
    ),  # inv #152
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Financial Summary", "card", "window", "transaction (eff: Reports Filed by period_end)",
        covered_by=(
            "PS::TestIrs527ProfileScope::{test_defaults_to_all_time_with_cycle_subtotal, test_cycle_preset_narrows}; "
            "WB::{test_527_reports_filed_follows_the_window, test_527_reports_filed_compares_compact_dates_as_dates}"
        ),
        note=(
            "'Reports Filed' counts the EIN's reports whose period_end (ISO or compact text) is in the window since "
            "S-W6b (undated ones only when unbounded); 'This cycle' is the labelled current-cycle subtotal; inventory "
            "label: Financial Summary (Reports Filed, Total Contributions, This cycle, Total Expenditures, Period, "
            "Contributions Received)"
        ),
    ),  # inv #153
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Filing History (N Reports)", "table", "fixed", "none",
    ),  # inv #154
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Matched IL Committees", "table", "fixed", "none",
    ),  # inv #155
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Directors / Officers (N)", "table", "fixed", "none",
    ),  # inv #156
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Related Organizations (N)", "table", "fixed", "none",
    ),  # inv #157
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Top Contributors (N records, N total)", "table", "window", "transaction",
        covered_by="PS::TestIrs527ProfileScope::test_cycle_preset_narrows ($5,321 absent / $4,321 present, page-wide)",
        note="inventory label: Top Contributors (N records, $ total)",
    ),  # inv #158
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Contributions To This Organization (Top N)", "table", "window", "transaction",
        covered_by="PS::TestIrs527ProfileScope::test_defaults_to_all_time_with_cycle_subtotal [at: 'Old Donor' row]",
    ),  # inv #159
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Top Expenditure Recipients (N records, N total)", "table", "window", "transaction",
        note="inventory label: Top Expenditure Recipients (N records, $ total)",
    ),  # inv #160
    Widget(
        "irs527.org_detail", "/527/<path:ein>", "irs527/detail.html",
        "Expenditures (Top N)", "table", "window", "transaction",
    ),  # inv #161

    # /527/<path:ein>/contributors and /recipients  [irs527.org_contributors / org_recipients]
    # (plan S-PB / C4; added after the inventory snapshot)
    Widget(
        "irs527.org_contributors", "/527/<path:ein>/contributors", "irs527/contributors.html",
        "Contributors", "card+table", "window", "transaction",
        covered_by=(
            "tests/test_irs527_routes.py::TestSeeAllPageRows::"
            "test_every_contributor_and_recipient_is_listed_beyond_the_top_ten_and_the_window_applies; "
            "tests/test_irs527_routes.py::TestSeeAllPagePaging::test_q_does_not_escape_the_scope_window"
        ),
        note="one row per exact contributor_name, 50 per page; the detail's Top Contributors are its head",
    ),
    Widget(
        "irs527.org_recipients", "/527/<path:ein>/recipients", "irs527/recipients.html",
        "Expenditure Recipients", "card+table", "window", "transaction",
        covered_by=(
            "tests/test_irs527_routes.py::TestSeeAllPageRows::"
            "test_every_contributor_and_recipient_is_listed_beyond_the_top_ten_and_the_window_applies; "
            "tests/test_irs527_routes.py::TestSeeAllPagePaging::test_q_does_not_escape_the_scope_window"
        ),
        note="one row per exact recipient_name, 50 per page; the detail's Top Expenditure Recipients are its head",
    ),

    # /527/dark-money  [irs527.dark_money]
    Widget(
        "irs527.dark_money", "/527/dark-money", "irs527/dark_money.html",
        "527 Funding Sources", "card+table", "window", "transaction",
    ),  # inv #162
    Widget(
        "irs527.dark_money", "/527/dark-money", "irs527/dark_money.html",
        "527 organization expenditures matched to IL committees and candidates (N matches)", "table", "window", "transaction",
        note="tag at the end of the subtitle above the table (S-W5); inventory label: Expenditure-recipient matches table ('N matches')",
    ),  # inv #163

    # /lobbying/  [lobbying.list_entities]
    Widget(
        "lobbying.list_entities", "/lobbying/", "lobbying/list.html",
        "N lobbying entities registered with IL Secretary of State", "table", "window", "reg_year",
        covered_by="SR::TestListScope::test_lobbying_list_filters_by_registration_year",
        note="tag on the count line above the table (S-W5): the entity rows and this total are filtered by reg_year (scopes snap to whole years); the Clients column carries its own tag below",
    ),  # inv #164
    Widget(
        "lobbying.list_entities", "/lobbying/", "lobbying/list.html",
        "Clients", "table", "window", "reg_year",
        covered_by="SR::TestListScope::test_lobbying_list_filters_by_registration_year; AW::test_lobbying_list_client_counts_window_by_registration_year",
        note="tag on the Clients column header; rows filtered by the entity's reg_year, Clients counted once each over lobbying_entity_clients.reg_year in the scope's years (S-W6a); inventory label: Lobbying entities table",
    ),  # inv #164

    # /lobbying/<int:entity_id>  [lobbying.entity_detail]
    Widget(
        "lobbying.entity_detail", "/lobbying/<int:entity_id>", "lobbying/entity_detail.html",
        "Matched donors / confidence badges", "list", "fixed", "none",
        note="untagged",
    ),  # inv #165
    Widget(
        "lobbying.entity_detail", "/lobbying/<int:entity_id>", "lobbying/entity_detail.html",
        "Money Destinations", "card+chart+table", "window", "received",
        covered_by=(
            "PS::TestLobbyingProfileScope::test_entity_defaults_to_all_time_with_cycle_subtotal [at: "
            "$1,200 default; no bounded-window content assertion]"
        ),
    ),  # inv #166
    Widget(
        "lobbying.entity_detail", "/lobbying/<int:entity_id>", "lobbying/entity_detail.html",
        "Clients (N)", "table", "window", "received (eff: lobbying_entity_clients.reg_year, whole years)",
        covered_by="AW::test_lobbying_entity_detail_clients_follow_the_registration_years",
        note="registrations in the window's years since S-W6a; the help text says 'by registration year'",
    ),  # inv #167
    Widget(
        "lobbying.entity_detail", "/lobbying/<int:entity_id>", "lobbying/entity_detail.html",
        "Matched Campaign Donors Across Clients", "table", "fixed", "none",
    ),  # inv #168
    Widget(
        "lobbying.entity_detail", "/lobbying/<int:entity_id>", "lobbying/entity_detail.html",
        "Matched Expenditure Payees", "table", "fixed", "none",
    ),  # inv #169

    # /lobbying/client/<int:client_id>  [lobbying.client_detail]
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "Matched donors / confidence badges", "list", "fixed", "none",
        note="untagged",
    ),  # inv #170
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "Money Destinations", "card+chart+table", "window", "received",
        note=(
            "no windowed-content test (tags pinned by "
            "PS::TestLobbyingProfileScope::test_client_cycle_preset_narrows; no amounts asserted)"
        ),
    ),  # inv #171
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "Represented By (N)", "table", "fixed", "none",
        note="no windowed-content test (tag pinned by PS::TestLobbyingProfileScope::test_client_cycle_preset_narrows)",
    ),  # inv #172
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "Matched Campaign Donors", "table", "fixed", "none",
    ),  # inv #173
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "Matched Expenditure Payees", "table", "fixed", "none",
    ),  # inv #174
    Widget(
        "lobbying.client_detail", "/lobbying/client/<int:client_id>", "lobbying/client_detail.html",
        "527 Organization Connections", "table", "fixed", "none",
    ),  # inv #175

    # /lobbying/flows  [lobbying.flows]
    Widget(
        "lobbying.flows", "/lobbying/flows", "lobbying/flows.html",
        "Matched lobbying clients to Illinois committees via donor overlaps.", "chart", "window", "received",
        covered_by="LR::test_lobbying_flows_data_returns_summary_metadata [at: unbounded branch]; scope-in-fetch-URL pinned by SR::TestListScope::test_flows_page_forwards_scope_to_its_data_fetch (no bounded content test)",
        note="tag at the end of the subtitle above the Sankey (S-W5); JSON data at /lobbying/flows/data; inventory label: Lobbying money-flow Sankey (client -> committee)",
    ),  # inv #176

    # /openbook/  [openbook.list_vendors]
    Widget(
        "openbook.list_vendors", "/openbook/", "openbook/list.html",
        "Contributions", "card", "window", "transaction",
        note="tag on the card heading (S-W5); SR::TestListScope::test_openbook_list_carries_scope_and_narrows asserts '$5,000.00' absent but the page renders counts only, so it cannot detect narrowing (the harness probe can); inventory label: Stat card: Contributions",
    ),  # inv #177
    Widget(
        "openbook.list_vendors", "/openbook/", "openbook/list.html",
        "Stat cards: Matched Vendors, Contracts, Total Award Amount", "card", "fixed", "none",
        covered_by="AW::test_openbook_list_contract_cards_are_tagged_all_time",
        note="Contracts and Total Award Amount carry fixed 'All time' tags since S-W6a (J-S14; fiscal_year only); Matched Vendors untagged",
    ),  # inv #178
    Widget(
        "openbook.list_vendors", "/openbook/", "openbook/list.html",
        "Contributions", "table", "partial", "transaction",
        ordinal=2,
        covered_by="SR::TestListScope::test_openbook_list_carries_scope_and_narrows [at: vendor presence at period=all]",
        note="tag on the Contributions column header (S-W5), the second 'Contributions' tag of the template after the stat card (#177): only that column is windowed; inventory label: Vendor table (Contracts, Total Awards, Contributions columns)",
    ),  # inv #179

    # /openbook/<path:vendor_key>  [openbook.vendor_detail]
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Stat cards: Seed Links, Best Confidence, Contracts", "card", "fixed", "none",
        note="untagged",
    ),  # inv #180
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Warrant Rows", "card", "window", "transaction",
    ),  # inv #181
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Contributions", "card", "window", "transaction",
        covered_by="PS::TestOpenBookProfileScope::{test_defaults_to_all_time_with_cycle_subtotal, test_cycle_preset_narrows}",
        note="inventory label: Contributions (stat card)",
    ),  # inv #182
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Seed Provenance", "table", "fixed", "none",
        note="no windowed-content test (tag pinned by PS::TestOpenBookProfileScope::test_defaults_to_all_time_with_cycle_subtotal)",
    ),  # inv #183
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Contracts", "table", "fixed", "none",
        note="no windowed-content test (tag pinned by PS::TestOpenBookProfileScope::test_defaults_to_all_time_with_cycle_subtotal)",
    ),  # inv #184
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Contract Warrant Details", "table", "window", "transaction",
    ),  # inv #185
    Widget(
        "openbook.vendor_detail", "/openbook/<path:vendor_key>", "openbook/detail.html",
        "Contributions", "table", "window", "transaction",
        ordinal=2,
        covered_by="PS::TestOpenBookProfileScope::test_cycle_preset_narrows ('Old Donor' row absent under cycle:2026)",
        note="inventory label: Contributions (table)",
    ),  # inv #186

    # /experimental/viz-lab  [experimental.viz_lab]
    # one scope_tag site serves all six prototype cards below (inv #187-#192), so it has a row of its own
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "N Experimental", "chart+table", "partial", "received",
        note="the shared card heading (S-W5): the tag reads the page window on every prototype except entity_resolution, which gets 'All time'; the per-prototype rows below carry the verdicts",
    ),
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Race Money vs Outside Pressure (race_money_pressure)", "chart+table", "partial", "received",
        covered_by="EV::test_viz_lab_data_race_money_pressure [iw: 2025 custom window]",
        note="tagged by the shared card heading; cycle-based race set; JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #187
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Race Funding Concentration Quadrant (race_concentration)", "chart+table", "partial", "received",
        note="tagged by the shared card heading; cycle-based race set; JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #188
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Cumulative Inflow by Top Races (cumulative_inflow)", "chart+table", "window", "received",
        note="tagged by the shared card heading; JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #189
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Payee Dominance Across Races (payee_dominance)", "chart+table", "window", "received (eff: expended_date)",
        note="tagged by the shared card heading; JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #190
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Entity Resolution Risk Panel (entity_resolution)", "chart+table", "fixed", "none",
        note="tagged 'All time' by the shared card heading (S-W5); JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #191
    Widget(
        "experimental.viz_lab", "/experimental/viz-lab", "experimental/viz_lab.html",
        "Constrained Network Slice (network_slice)", "chart+table", "window", "received",
        covered_by="EV::test_viz_lab_network_slice_advanced_metrics_payload [at]",
        note="tagged by the shared card heading; JSON data at /experimental/viz-lab/data/<prototype_key>",
    ),  # inv #192

    # /experimental/viz-lab/state-maps  [viz_lab.viz_lab_state_maps]
    Widget(
        "viz_lab.viz_lab_state_maps", "/experimental/viz-lab/state-maps", "experimental/viz_lab_state_maps.html",
        "Illinois State Maps (CD/SLDL/SLDU boundaries)", "map", "fixed", "none",
        note="untagged; no windowed-content test (tests/test_viz_lab_routes.py: structure only)",
    ),  # inv #193

    # /compare  [main.compare]
    Widget(
        "main.compare", "/compare", "compare.html",
        "Compare profiles: stat cards, Top Donors, Election History, Trend Comparison (svg#compare-trend-svg), Donor Overlap",
        "card+chart+table", "unscoped", "none",
        note="no scope bar or resolver; outside the scope model (suggestion E8)",
    ),  # inv #194

    # /investigate  [main.investigate_workspace]
    Widget(
        "main.investigate_workspace", "/investigate", "investigate.html",
        "Investigate / Money Flow / Unusual Patterns tables", "table", "unscoped", "none",
        note=(
            "no scope bar or resolver; outside the scope model (suggestion E8); has its own ?month= "
            "filter, which is not the time scope; no windowed-content test "
            "(tests/test_guided_workspace.py: structure)"
        ),
    ),  # inv #195

    # /person-intelligence  [main.person_intelligence]
    Widget(
        "main.person_intelligence", "/person-intelligence", "person_intelligence.html",
        "Person Intelligence result tables (donors, candidates, 527 directors, lobbying, federal contributors)",
        "table", "unscoped", "none",
        note="no scope bar or resolver; outside the scope model (suggestion E8)",
    ),  # inv #196
)


def by_template() -> dict[tuple[str, str, int], Widget]:
    """The registry keyed (template, heading, ordinal), the identity of a scope_tag site."""
    keyed: dict[tuple[str, str, int], Widget] = {}
    for widget in WIDGETS:
        key = (widget.template, widget.heading, widget.ordinal)
        if key in keyed:
            raise ValueError(f"duplicate registry key {key}")
        keyed[key] = widget
    return keyed
