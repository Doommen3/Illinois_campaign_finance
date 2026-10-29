# Page inventory

The shared page inventory for to-do items 10 (data integrity, `docs/data_integrity_checks.md`) and 2 (security
audit). It lists every rule in the Flask `url_map`, grouped into classes of pages that draw the same figures from
the same data path, so a check on a representative sample of one class is evidence for the whole class.
Structural only: no cell values, no private individuals, no addresses or donor keys.

**As of:** 2026-10-29, branch `worktree-ui-improvements` off `main` 2df4ae8.

## How to read this file

- **Members** are `url_map` rules written verbatim in backticks, one per line, each followed by its Flask
  endpoint. Every rule appears in backticks exactly once in this file (see Coverage). Methods are GET unless the
  line says otherwise.
- **Representative** URLs are real local URLs with public ids, written without backticks so the coverage check
  only sees rules (Michael W Frerichs = candidate 13505; Democratic Party of Illinois = committee 6239). Donor
  pages are reached by clicking through from a committee, never quoted.
- **Data path** names the tables, materialized views (matviews), compat views and caches behind the figures. The
  compat views `bulk_receipts_clean` / `bulk_expenditures_clean` read the matviews `isbe_condensed_receipts` /
  `isbe_condensed_expenditures` (`.claude/data-dictionary.md`). Caches are `RouteCache` entries
  (`docs/runbooks/route_perf_caching.md`); a check on a cached page uses `?refresh_cache=1` from a signed-in
  session, or a local server started after the last refresh.
- **Figures shown** cite the widget headings in `tests/scope_widgets_registry.py` where the page has rows there.
- **Sample n and why** follows the rule of three in `docs/data_integrity_checks.md` (0 failures in n gives a
  95 % upper bound of about 3/n on the class failure rate: n = 30 → < 10 %, n = 60 → < 5 %), stratified by size
  (top / middle / small members).
- **Batch** is the batch number in the batch plan of `docs/data_integrity_checks.md`.

## Classes

### C-01 — Donors (ISBE donor identity)

- **Members:**
  - `/donors/` — donors.list_donors
  - `/donors/<int:donor_id>` — donors.donor_detail
  - `/donors/key/<path:donor_key>` — donors.donor_detail_by_key
  - `/donors/entity/<path:entity_id>` — donors.donor_detail_by_entity
  - `/committees/<int:committee_id>/donors` — committees.committee_donors
- **Representative:** /donors/ (default window); /committees/6239/donors, then the top committee donor's
  profile reached by clicking its name.
- **Templates:** donors/list.html, donors/detail.html, donors/detail_by_key.html (key and entity pages),
  committees/donors.html.
- **Data path:** directory: `Donor.get_all_with_totals` over `analytics_donor_summary` (all time),
  `analytics_donor_cycle_summary` (a window of exactly one cycle) or a live GROUP BY on `bulk_receipts_clean`
  (other windows); key page: `Donor.get_summary_by_key` / `get_committee_breakdown_by_key` over
  `analytics_donor_committee_agg` (TEXT `committee_id`) and `bulk_receipts_clean`; entity page: the same over
  `donor_entity_local` / `donor_entity_local_member`; committee ledger: `_donor_rollup_source` (live GROUP BY on
  `bulk_receipts_clean`, contributions predicate `receipts_filter_sql`); the legacy donor page (int id) reads the legacy
  scraper tables `donors` + `contributions`. Donor identity = `donor_key` (`Donor._bulk_donor_key_sql`). Not
  route-cached.
- **Figures shown:** directory "N total donors" table; key/entity page "Total Contributed", "Number of
  Contributions", "Committees Supported", "Who They Donated To"; legacy page "Total Contributed", "Number of
  Contributions", "Contributions"; ledger "Donors" card, "Total Contributions N" table, "This cycle" subtotal.
- **Windowed?** Directory: yes, default current cycle (`cycle:2026`), axis `received_date`. Key/entity pages and
  ledger: default all time with a "This cycle" subtotal, axis `received_date`. Legacy page: transaction date.
- **Proposed integrity checks:** recompute a donor's total, contribution count and per-committee list from
  `isbe_receipts` (`archived = FALSE`) by donor key under the contributions predicate (DI-001); recompute a
  committee's donor count and total in the window (DI-002); recompute the directory's top rows for the default
  cycle (DI-003). Known semantics to confirm, not assume: in-kind (`5A`) rows are outside the contributions
  predicate; the analytics donor tables add an EXISTS on a non-archived D-2 and drop blank `d2_part`.
- **Sample n and why:** n = 60 donors (20 top by total, 20 middle, 20 small), plus every donor that is itself a
  committee among the top 20; most-read class and the subject of the known 8b defect, so it gets the < 5 %
  bound. Redacted donors (`redaction_requested = TRUE`) are excluded from the sample.
- **Batch:** 1 (in progress, `docs/audits/data_integrity_batch1_donors_2026-10-29.md`).

### C-02 — Candidate profiles

- **Members:**
  - `/candidates/<int:candidate_id>` — candidates.candidate_detail
  - `/candidates/<int:candidate_id>/outside-spending` — candidates.candidate_outside_spending
- **Representative:** /candidates/13505; /candidates/13505/outside-spending.
- **Templates:** candidates/detail.html, candidates/outside_spending.html (both extend base.html and include
  `_partials/time_scope_bar.html`).
- **Data path:** `isbe_candidates`, `isbe_candidacies`, `isbe_candidate_committees` (linked committees),
  `isbe_committees`, `isbe_condensed_receipts` (every part, amount > 0, not archived), `isbe_condensed_expenditures`
  (spent; Part 9 rows `d2_part LIKE '9%'` for outside spending), `isbe_most_recent_filings`, accepted
  `committee_family` groupings. Not route-cached.
- **Figures shown:** "Committees" card and table (raised / spent per committee, window and "This cycle"),
  "Candidacies", "Money raised" (per cycle), "Outside spending (D-2 Part 9)" card; outside-spending page "Line
  items" (supporting / opposing totals).
- **Windowed?** Default all time with a "This cycle" subtotal; axis `received_date` (receipts),
  `expended_date` (spending). "Money raised" is per cycle (odd years fold into the next even cycle).
- **Proposed integrity checks:** recompute raised per cycle over the linked committees from `isbe_receipts`
  (DI-004); recompute the Part 9 supporting / opposing totals from `isbe_expenditures` and explain every
  name-match difference by the rule in `docs/outside_spending.md` (DI-005).
- **Sample n and why:** n = 30 candidates (10 statewide or top-raised, 10 middle, 10 small or local); the page is
  a thin sum over committee figures that C-04 also checks, so the < 10 % bound is enough here.
- **Batch:** 2.

### C-03 — Entity analytics tabs (candidates and state races)

- **Members:**
  - `/candidates/<int:candidate_id>/<tab>` — entity_analytics.candidate_tab
  - `/candidates/<int:candidate_id>/network.json` — entity_analytics.candidate_network_json
  - `/analytics/state-races/<race_key>/<tab>` — entity_analytics.race_tab
  - `/analytics/state-races/<race_key>/network.json` — entity_analytics.race_network_json
- **Representative:** /candidates/13505/network; /candidates/13505/donors; /candidates/13505/network.json;
  /analytics/state-races/governor-statewide-illinois/risk.
- **Templates:** entity/network.html and the other `entity/` tab templates (`<tab>` is one of network, risk,
  donors, geography, relationships; anything else is a 404).
- **Data path:** `database/entity_analytics.py` (`get_entity_donors`, `get_entity_risk`, `get_entity_geography`,
  `get_entity_relationships`) and `database/entity_network.py` (`get_entity_network`) over `isbe_condensed_receipts`
  (receipt base `archived = FALSE AND d2_part LIKE '1%' AND amount > 0`; in-kind panel `d2_part = '5A'`),
  `isbe_condensed_expenditures`, `isbe_committees`, `isbe_candidates`, `isbe_candidate_committees`,
  `isbe_race_candidacies`, `committee_family_member`. Cached: `RouteCache` `entity_<tab>`, key
  `ENTITY_CACHE_REV` + kind + entity + tab (`network:<view>` for JSON views) + `scope_cache_key(scope)` + cycle +
  limits. Network node hrefs: committee → the committee profile, candidate → the candidate profile, donor →
  the donor key page (non-redacted only).
- **Figures shown:** registry rows for `entity_analytics.candidate_tab`: "Network edges", "Risk flags", "D-2
  reconciliation", "Concentration by group", "Donor classes", "Top donors", "Shared donors", "States", "Cities",
  "Monthly contributions", "Co-giving committees"; the Network tab's drill-down panels (money in, in-kind, money
  out) read the JSON.
- **Windowed?** Yes; candidate tabs default to all time, race tabs to the race's cycle; axis `received_date`.
- **Proposed integrity checks:** recompute the Donors tab top-10 and total over the linked committees (DI-006);
  recompute one network edge amount and its drill-down rows (DI-007). The tab predicate drops blank `d2_part`
  rows that the committee ledger keeps; the batch measures that gap and records it.
- **Sample n and why:** n = 30 entities (15 candidates, 15 races; top / middle / small by raised), each checked
  on the Donors tab and one network edge; tabs share one feeder per tab, so 30 entities bound the feeder at < 10 %.
- **Batch:** 2.

### C-04 — Committee profiles and payee ledger

- **Members:**
  - `/committees/<int:committee_id>` — committees.committee_detail
  - `/committees/sbe/<int:committee_id_sbe>` — committees.committee_detail_by_sbe
  - `/committees/<int:committee_id>/payees` — committees.committee_payees
- **Representative:** /committees/6239; /committees/sbe/6239; /committees/6239/payees.
- **Templates:** committees/detail_sbe.html (ISBE ids; the plain committee URL renders it whenever the id is in
  `isbe_committees`), committees/detail.html (legacy fallback), committees/payees.html.
- **Data path:** `_bulk_committee_profile` over `isbe_committees`, `bulk_receipts_clean` (Receipts Summary,
  every part: `receipts_filter_sql(parts="all")`), `bulk_expenditures_clean`, `bulk_committee_candidate_links`,
  `isbe_officers`, `isbe_filed_docs`, `analytics_donor_committee_agg` (Top Donors head); payee ledger:
  `_payee_rollup_source` (live GROUP BY on `bulk_expenditures_clean`, normalized payee name). Not route-cached.
- **Figures shown:** registry rows for `committees.committee_detail_by_sbe`: "Committee Information", "Receipts
  Summary", "Expenditures Summary", "Linked Candidates", "Current Officers", "Filing History", "Top Donors", "Top
  Payees"; payees page "Payees" card and table with "This cycle".
- **Windowed?** Default all time with a "This cycle" subtotal; receipts axis `received_date`, payees axis
  `expended_date`.
- **Proposed integrity checks:** recompute Receipts Summary (window and cycle) from `isbe_receipts`, all parts
  (DI-008); recompute Expenditures Summary and the payee ledger total (DI-009); Top Donors head equals the first
  rows of the C-01 ledger (covered by DI-002).
- **Sample n and why:** n = 60 committees (20 top by receipts, 20 middle, 20 small; include 5 party committees
  and 5 committees with ids 1–348, which collide with legacy ids); second most-read class, < 5 % bound.
- **Batch:** 3.

### C-05 — Filings and D-2 reconciliation

- **Members:**
  - `/committees/<int:committee_id>/filings` — committees.committee_filings
  - `/committees/filing/<int:filed_doc_id>` — committees.filing_detail
  - `/d2-reconciliation/` — d2_receipts_recon.list_d2_receipts_recon
  - `/d2-expenditures-reconciliation/` — d2_expenditures_recon.list_d2_expenditures_recon
- **Representative:** /committees/6239/filings; /committees/filing/1015462 (a Democratic Party of Illinois D-2);
  /d2-reconciliation/; /d2-expenditures-reconciliation/.
- **Templates:** committees/filings.html, committees/filing_detail.html, d2_receipts_recon/list.html,
  d2_expenditures_recon/list.html.
- **Data path:** `isbe_filed_docs`, `isbe_committees`, `isbe_condensed_receipts` / `isbe_condensed_expenditures`
  by `filed_doc_id` (with `bulk_*_clean` fallbacks); reconciliation lists read the compat tables
  `bulk_d2_receipts_recon` / `bulk_d2_expenditures_recon` (D-2 sworn totals vs itemized sums per filed doc).
- **Figures shown:** "N filing rows" (filings and both reconciliation lists), filing line items and D-2 summary.
- **Windowed?** Filings: yes, axis `received_at` of the filing; filing detail: one document, unscoped;
  reconciliation lists: yes (filing date).
- **Proposed integrity checks:** a filing's line-item count and sum and its D-2 totals recomputed from
  `isbe_receipts` / `isbe_d2_reports` by `filed_doc_id` (DI-010); a reconciliation row's D-2 total, itemized sum and
  difference recomputed (DI-011). Known open bug to keep in view: `isbe_d2_reports.transfer_in` /
  `loan_received` / `other_receipts` carry the non-itemized halves.
- **Sample n and why:** n = 60 filed documents (20 from top committees, 20 middle, 20 small; at least 10 amended
  D-2s); the D-2 is the sworn ground truth other classes lean on, < 5 % bound.
- **Batch:** 3.

### C-06 — Candidate-committee finance aggregates

- **Members:**
  - `/candidate-finance/` — candidate_finance.list_candidate_finance
  - `/candidate-finance/<int:candidate_id>/<int:committee_id>/itemized` — candidate_finance.candidate_committee_itemized
  - `/candidate-finance/<int:candidate_id>/<int:committee_id>/itemized-expenditures` — candidate_finance.candidate_committee_itemized_expenditures
- **Representative:** /candidate-finance/; /candidate-finance/13505/14111/itemized;
  /candidate-finance/13505/14111/itemized-expenditures.
- **Templates:** candidate_finance/list.html, candidate_finance/itemized.html,
  candidate_finance/itemized_expenditures.html.
- **Data path:** `bulk_candidate_committee_finance_agg` (matview built by `scripts/swap_bulk_to_isbe.py` from
  `isbe_d2_reports` + links; columns `sum_total_receipts`, `sum_total_expenditures`, `max_ending_funds_available`,
  `period_year`, `election_cycle`); itemized pages: `bulk_receipts_clean` / `bulk_expenditures_clean` joined to
  `bulk_committee_candidate_links`. List cached (keyed on every filter param).
- **Figures shown:** "N total candidate-committee aggregate rows" table; itemized "Rows: N" tables.
- **Windowed?** Yes, default current cycle; list on D-2 period dates, itemized pages on `received_date` /
  `expended_date`.
- **Proposed integrity checks:** a row's receipts / expenditures / ending funds recomputed from
  `isbe_d2_reports` + `isbe_filed_docs` (DI-012); itemized row count and sum recomputed from `isbe_receipts`
  (folded into DI-012).
- **Sample n and why:** n = 30 candidate × committee rows (top / middle / small by receipts); a single matview
  feeds all three pages, < 10 % bound.
- **Batch:** 4.

### C-07 — State races

- **Members:**
  - `/analytics/state-races/` — analytics.state_races
  - `/analytics/state-races/<race_key>` — analytics.state_race_detail
- **Representative:** /analytics/state-races/; /analytics/state-races/governor-statewide-illinois.
- **Templates:** analytics/state_races.html, analytics/state_race_detail.html.
- **Data path:** `get_state_race_analytics` / `get_state_race_detail` (`database/analytics.py`) over
  `isbe_race_candidacies` (view), `bulk_candidate_committee_finance_agg`, `bulk_receipts_clean`,
  `bulk_expenditures_clean`, `isbe_condensed_expenditures` (Part 9), `donor_entity_local_member`. Cached:
  `_state_races_cache_key(scope, election_cycle)` (one build per window and cycle, revision
  `_STATE_RACE_CACHE_REV`) and the per-race detail cache.
- **Figures shown:** registry rows for `analytics.state_races` and `analytics.state_race_detail` ("State Races",
  "Candidates", "Donors", "Contributions", "Total Amount", "Outside Spending (E)", "Outside Pressure Ratio",
  "Candidate Committee Rows", "Committee Contribution Rows", "Outside Spending Rows (D-2 Part 9)").
- **Windowed?** Yes, default current cycle; axis `received_date`; race identity `(office, district_type,
  district)` from the candidate's current office.
- **Proposed integrity checks:** per-candidate raised and race total recomputed from candidacies → linked
  committees → `isbe_receipts`, counting a slate committee once per race (DI-013).
- **Sample n and why:** n = 30 races (all statewide races, then 12 House and Senate districts by total, then small
  local races); < 10 % bound.
- **Batch:** 5.

### C-08 — Analytics dashboards

- **Members:**
  - `/analytics/` — analytics.dashboard
  - `/analytics/overview` — analytics.overview
  - `/analytics/donors` — analytics.donors
  - `/analytics/geography` — analytics.geography
  - `/analytics/geo-drilldown` — analytics.geo_drilldown
  - `/analytics/networks` — analytics.networks
  - `/analytics/relationships` — analytics.relationships
  - `/analytics/risk` — analytics.risk
- **Representative:** /analytics/; /analytics/risk.
- **Templates:** analytics/overview.html (both dashboard rules), analytics/donors.html,
  analytics/geography.html, analytics/geo_drilldown.html, analytics/networks.html, analytics/relationships.html,
  analytics/risk.html.
- **Data path:** `database/analytics.py` builders: `get_time_series` (`analytics_committee_monthly_totals`,
  `bulk_receipts_clean`), `get_geo_summary` / `get_geo_drilldown` / `get_donor_concentration`
  (`bulk_receipts_clean`, `analytics_donor_committee_agg`), `get_network_graph` and the other graph builders
  (`analytics_donor_committee_agg`, `analytics_donor_summary`, lobbying / 527 / FEC match tables),
  `get_anomaly_flags` (`analytics_large_contributions`, `analytics_committee_monthly_totals`),
  `get_reconciliation_outliers` (`bulk_d2_receipts_recon`), `get_nlp_spending_summary` (`bulk_expenditures_clean`),
  `get_dashboard_snapshot` (`analytics_snapshots`). Every route is `RouteCache`d, key = `scope_cache_key(scope)` +
  filters; prewarmed per worker.
- **Figures shown:** registry rows for `analytics.dashboard` ("Trend Months", "Network Nodes / Edges", "Top State
  Races", "Trend Visuals", "Time-Series Preview", "Reconciliation Preview", ...), `analytics.donors` ("Donor
  Concentration Metrics"), `analytics.geography` ("Geospatial Summary - States / Cities"), `analytics.networks`,
  `analytics.relationships`, `analytics.risk` ("Risk and Anomaly Flags", "D2 Reconciliation Signals").
- **Windowed?** Yes, default current cycle; axis `received_date`; NLP and reconciliation widgets are all time
  (registry status `fixed`).
- **Proposed integrity checks:** monthly time-series totals recomputed from `isbe_receipts` (DI-014); state and
  city totals for the geography page recomputed (DI-015); reconciliation rows covered by DI-011.
- **Sample n and why:** n = 30 figure cells per page sampled from its tables (top / middle / bottom rows), 8 pages;
  one shared builder module, < 10 % bound per page.
- **Batch:** 5.

### C-09 — Federal (FEC) dashboards

- **Members:**
  - `/federal-finance/` — federal_finance.list_federal_finance
  - `/federal-finance/overview` — federal_finance.federal_overview
  - `/federal-finance/candidates` — federal_finance.federal_candidates
  - `/federal-finance/networks` — federal_finance.federal_networks
  - `/federal-finance/donor-intelligence` — federal_finance.federal_donor_intelligence
  - `/federal-finance/money-flow` — federal_finance.federal_money_flow
  - `/federal-finance/influence` — federal_finance.federal_influence
  - `/federal-finance/follow-the-money` — federal_finance.federal_follow_the_money
  - `/federal-finance/geography` — federal_finance.federal_geography
  - `/federal-finance/geo-drilldown` — federal_finance.federal_geo_drilldown
  - `/federal-finance/matching` — federal_finance.federal_matching
- **Representative:** /federal-finance/overview; /federal-finance/matching.
- **Templates:** federal_finance/overview.html (both overview rules), candidates.html, networks.html,
  donor_intelligence.html, money_flow.html, influence.html, follow_the_money.html, geography.html,
  geo_drilldown.html, matching.html (all under federal_finance/).
- **Data path:** `database/federal_fec.py` over `fec_schedule_a_contributions`, `fec_schedule_b_disbursements`,
  `fec_schedule_e_independent_expenditures`, `fec_candidate_match`, `fec_candidate_cycle_totals`,
  `fec_candidate_committees`, `fec_local_donor_matches` (matching). Cached: federal view snapshots
  (`get_federal_view_snapshot`) and `federal_geo_drilldown` (180 s).
- **Figures shown:** registry rows for `federal_finance.list_federal_finance` ("Committees", "Contributions",
  "Donors", "Total Amount", "Date Range", "Schedule B / E Date Range", "Top Donors", ...), the money-flow cards
  ("Money In (A)", "Money Out (B)", "Outside Spending (E)", "Net Flow"), matching ("Federal Donors Matched",
  "Match Rate", "Matched Donor Rows").
- **Windowed?** Yes, by FEC two-year cycle (`two_year_transaction_period` / `cycle`), default current cycle.
- **Proposed integrity checks:** Schedule A / B / E counts and sums for the cycle recomputed (DI-016).
- **Sample n and why:** n = 30 figure cells across the 11 pages (every headline card, then top / middle table
  rows); one feeder module and three source tables, < 10 % bound.
- **Batch:** 6.

### C-10 — Federal profiles and race outside spending

- **Members:**
  - `/federal-finance/<candidate_id>` — federal_finance.federal_candidate_detail
  - `/federal-finance/committees/<committee_id>/receipts` — federal_finance.federal_committee_receipts
  - `/federal-finance/donors/<donor_entity_key>` — federal_finance.federal_donor_detail
  - `/federal-finance/matches/<federal_donor_entity_key>/<path:local_donor_key>` — federal_finance.federal_matched_donor_profile
  - `/federal-finance/races/<office_code>/<district_code>/outside-spending` — federal_finance.federal_race_outside_spending
- **Representative:** /federal-finance/S6IL00482 (Raja Krishnamoorthi, 2026 Senate);
  /federal-finance/committees/C00904466/receipts (Raja for Illinois); /federal-finance/races/H/07/outside-spending.
  Donor and match pages are reached by clicking through.
- **Templates:** federal_finance/detail.html, committee_receipts.html, donor_detail.html, match_profile.html,
  race_outside_spending.html.
- **Data path:** `fec_schedule_a_contributions` (by `candidate_id`, `committee_id`, `donor_entity_key`),
  `fec_schedule_b_disbursements`, `fec_schedule_e_independent_expenditures` (by office / state / district),
  `fec_candidate_cycle_totals` ("Total Source: FEC candidate totals endpoint"), `fec_candidate_committees`; match
  profile adds `fec_local_donor_matches` and the local side from `analytics_donor_committee_agg`.
- **Figures shown:** candidate detail cards ("Cycle", "Reported Receipts / Disbursements", "Committees",
  "Contributions", "Donors", "Total Amount", date ranges) and tables; committee receipts "Itemized Receipts
  (Schedule A)"; donor "Donations by Candidate"; match "Federal Summary" / "Local Summary"; race page "Total
  Schedule E Amount", "Transaction Count", "Top IE Committees".
- **Windowed?** Yes, by FEC cycle; donor detail all time with date range cards.
- **Proposed integrity checks:** candidate and committee Schedule A totals (DI-017); race Schedule E totals by
  support / oppose (DI-018).
- **Sample n and why:** n = 30 (10 candidates, 10 committees, 10 races; top / middle / small by amount); < 10 %.
- **Batch:** 6.

### C-11 — Lobbying

- **Members:**
  - `/lobbying/` — lobbying.list_entities
  - `/lobbying/<int:entity_id>` — lobbying.entity_detail
  - `/lobbying/client/<int:client_id>` — lobbying.client_detail
  - `/lobbying/flows` — lobbying.flows
  - `/lobbying/flows/data` — lobbying.flows_data (JSON for the flows page)
- **Representative:** /lobbying/; /lobbying/7036 (Taft Stettinius & Hollister LLP); /lobbying/client/3821
  (Ameren Illinois); /lobbying/flows.
- **Templates:** lobbying/list.html, entity_detail.html, client_detail.html, flows.html.
- **Data path:** `lobbying_entities`, `lobbying_clients`, `lobbying_entity_clients` (`reg_year`),
  `lobbying_donor_matches`, `lobbying_expenditure_matches`, `lobbying_527_matches`; money destinations and
  flows over `analytics_donor_committee_agg` / `bulk_receipts_clean` by matched `donor_key`.
- **Figures shown:** list "N lobbying entities registered with IL Secretary of State", "Clients"; entity "Clients
  (N)", "Matched Campaign Donors Across Clients", "Matched Expenditure Payees", "Money Destinations"; client
  "Represented By (N)", "Matched Campaign Donors", "527 Organization Connections"; flows chart.
- **Windowed?** Lists snap to registration years (`reg_year`); money figures on `received_date`.
- **Proposed integrity checks:** client counts per entity and year (DI-019); matched-donor money totals recomputed
  from `isbe_receipts` by the matched keys (DI-020).
- **Sample n and why:** n = 30 (15 entities, 15 clients; top / middle / small by client count); < 10 %.
- **Batch:** 7.

### C-12 — IRS 527 organizations

- **Members:**
  - `/527/` — irs527.list_orgs
  - `/527/<path:ein>` — irs527.org_detail
  - `/527/<path:ein>/contributors` — irs527.org_contributors
  - `/527/<path:ein>/recipients` — irs527.org_recipients
  - `/527/dark-money` — irs527.dark_money
- **Representative:** /527/; /527/813554739 (National Democratic Redistricting Committee); /527/dark-money.
- **Templates:** irs527/list.html, detail.html, contributors.html, recipients.html, dark_money.html.
- **Data path:** `irs527_organizations`, `irs527_reports`, `irs527_contributions` (text `date`),
  `irs527_expenditures` (text `date`), `irs527_contribution_rollup`, `irs527_contributor_rollup`,
  `irs527_committee_matches`, `irs527_expenditure_recipient_matches`, `irs527_directors`, director match tables.
  Windows on text dates go through `text_date_window_clause`. The dark-money summary stats are cached.
- **Figures shown:** registry rows for `irs527.org_detail` ("Financial Summary", "Filing History (N Reports)",
  "Top Contributors (N records, N total)", "Top Expenditure Recipients (N records, N total)", ...), list "Total
  Contributions" / "Total Expenditures", dark money "527 Funding Sources".
- **Windowed?** Yes, default current cycle on the text transaction date; org detail default all time.
- **Proposed integrity checks:** an org's contribution and expenditure counts and sums recomputed (DI-021); the
  dark-money totals recomputed (DI-022).
- **Sample n and why:** n = 30 orgs (10 top by contributions, 10 middle, 10 small); < 10 %.
- **Batch:** 7.

### C-13 — OpenBook vendors

- **Members:**
  - `/openbook/` — openbook.list_vendors
  - `/openbook/<path:vendor_key>` — openbook.vendor_detail
- **Representative:** /openbook/; /openbook/AMEREN%20ILLINOIS.
- **Templates:** openbook/list.html, openbook/detail.html.
- **Data path:** `openbook_vendor_seed`, `openbook_vendor_match`, `openbook_vendor_label`,
  `openbook_contracts_raw` (`award_amount`, `fiscal_year`), `openbook_contract_warrants`,
  `openbook_contributions_raw`; `lobbying_entities` for the cross-link.
- **Figures shown:** list stat cards and vendor table; detail "Contracts", "Warrants", "Contributions" sections
  (registry rows for `openbook.vendor_detail`).
- **Windowed?** Contracts by `fiscal_year`; contributions by `contribution_date`.
- **Proposed integrity checks:** a vendor's contract count, award total and contribution total recomputed
  (DI-023).
- **Sample n and why:** n = 30 vendors, or every vendor if fewer (top / middle / small by award total); < 10 %.
- **Batch:** 8.

### C-14 — Cross-source tools

- **Members:**
  - `/search` — main.search
  - `/person-intelligence` — main.person_intelligence
  - `/investigate` — main.investigate_workspace
  - `/live-feed` — main.live_feed
- **Representative:** /search?q=frerichs; /live-feed.
- **Templates:** search.html, person_intelligence.html, investigate.html, live_feed.html.
- **Data path:** restate figures owned by other classes: search over `analytics_donor_summary`,
  `bulk_candidate_committee_finance_agg`, `isbe_committees`, `isbe_candidates`, `isbe_filed_docs`,
  `fec_candidate_match` (cached `search_results`); person intelligence over ISBE candidates / committees, FEC
  Schedule A, `irs527_directors`, lobbying; investigate over `analytics_large_contributions` and match tables;
  live feed over the latest rows of `bulk_receipts_clean` and FEC Schedules A / B / E.
- **Figures shown:** search result totals per section; live feed "Local feed latest date", "Federal feed latest
  date" and the recent-row tables; investigate tables.
- **Windowed?** Search and live feed: yes, default current cycle; person intelligence and investigate: their own
  fixed windows.
- **Proposed integrity checks:** a figure shown by a tool equals the figure on the profile it links to, and that
  profile figure passes its class check (DI-024); live-feed latest dates equal the source maxima (DI-025).
- **Sample n and why:** n = 30 tool results (10 searches × top 3 results; the remaining from live feed and
  investigate rows); figures are restatements, so < 10 % suffices.
- **Batch:** 8.

### C-15 — Legacy scraper pages and the v1 JSON API

- **Members:**
  - `/legacy` — main.legacy
  - `/reports/` — reports.list_reports
  - `/reports/<int:report_id>` — reports.report_detail
  - `/committees/legacy/<int:committee_id>` — committees.legacy_committee_detail
  - `/api/stats` — api.stats
  - `/api/committees` — api.list_committees
  - `/api/committees/<int:committee_id>` — api.get_committee
  - `/api/donors` — api.list_donors
  - `/api/donors/<int:donor_id>` — api.get_donor
  - `/api/reports` — api.list_reports
  - `/api/reports/<int:report_id>` — api.get_report
  - `/api/scrape-status` — api.scrape_status
- **Representative:** /legacy; /reports/; /committees/legacy/1; /api/stats.
- **Templates:** legacy.html, reports/list.html, reports/detail.html, committees/detail.html; the API returns
  JSON.
- **Data path:** legacy scraper tables `committees` (ids 1–348), `reports`, `contributions`, `donors`,
  `scrape_status`; not refreshed. Exception: the donors list API uses `Donor.get_all_with_totals`, the C-01 directory
  feeder.
- **Figures shown:** "Legacy Pages" card, "Reports", "Total Contributions: N", "Recent Contributions"; API counts
  and totals.
- **Windowed?** Legacy committee page: transaction date, default all time; the rest unscoped.
- **Proposed integrity checks:** counts and totals equal `COUNT(*)` / `SUM` over the legacy tables (DI-026).
- **Sample n and why:** census, not a sample: the tables are small (344 committees, 650 reports, 766
  contributions) and frozen, so every API count and 30 legacy pages.
- **Batch:** 9.

### C-16 — Analytics JSON API

- **Members:**
  - `/api/analytics/anomalies` — api.analytics_anomalies
  - `/api/analytics/candidate-competition` — api.analytics_candidate_competition
  - `/api/analytics/committee-similarity` — api.analytics_committee_similarity
  - `/api/analytics/concentration` — api.analytics_concentration
  - `/api/analytics/donor-cogiving` — api.analytics_donor_cogiving
  - `/api/analytics/geo` — api.analytics_geo
  - `/api/analytics/irs527-ecosystem` — api.analytics_irs527_ecosystem
  - `/api/analytics/lobbying-influence` — api.analytics_lobbying_influence
  - `/api/analytics/network` — api.analytics_network
  - `/api/analytics/network/focus-sankey` — api.analytics_network_focus_sankey
  - `/api/analytics/nlp` — api.analytics_nlp
  - `/api/analytics/reconciliation` — api.analytics_reconciliation
  - `/api/analytics/time-series` — api.analytics_time_series
  - `/api/federal/network/focus-sankey` — api.federal_network_focus_sankey
- **Representative:** /api/analytics/reconciliation; /api/analytics/time-series.
- **Templates:** none (JSON).
- **Data path:** the same `database/analytics.py` builders as C-08 (windowed by `_resolved_date_window`); the
  focus-sankey endpoints run their own SQL over `bulk_receipts_clean`, `analytics_donor_committee_agg`,
  `bulk_expenditures_clean` (state) and FEC Schedules A / B (federal).
- **Figures shown:** JSON payloads behind the analytics and network charts.
- **Windowed?** Yes, same window parameters as the pages.
- **Proposed integrity checks:** a payload value equals the matching page widget and the C-08 recomputation
  (DI-027).
- **Sample n and why:** n = 30 payload values across the 14 endpoints (at least one each); the builders are
  checked in C-08, so this checks the serialization path only, < 10 %.
- **Batch:** 9.

### C-17 — Experimental Viz Lab (local only)

- **Members:**
  - `/experimental/viz-lab` — experimental.viz_lab
  - `/experimental/viz-lab/` — viz_lab.viz_lab_index (redirect to the combined Viz Lab)
  - `/experimental/viz-lab/<prototype_key>` — experimental.viz_lab
  - `/experimental/viz-lab/data/<prototype_key>` — experimental.viz_lab_data (JSON)
  - `/experimental/viz-lab/state-maps` — viz_lab.viz_lab_state_maps
  - `/experimental/viz-lab/triple-pipeline` — viz_lab.viz_lab_triple_pipeline (redirect)
  - `/experimental/viz-lab/data/triple-pipeline/graph` — viz_lab.triple_pipeline_graph (JSON artifact)
  - `/experimental/viz-lab/data/triple-pipeline/ego` — viz_lab.triple_pipeline_ego (JSON artifact)
  - `/experimental/viz-lab/data/triple-pipeline/entities` — viz_lab.triple_pipeline_entities (JSON artifact)
  - `/experimental/viz-lab/data/triple-pipeline/timeline` — viz_lab.triple_pipeline_timeline (JSON artifact)
  - `/experimental/viz-lab/data/geometry/il/status` — viz_lab.geometry_status (JSON)
  - `/experimental/viz-lab/data/geometry/il/<layer_key>` — viz_lab.geometry_layer (TopoJSON)
- **Representative:** /experimental/viz-lab (local server only).
- **Templates:** experimental/viz_lab.html, experimental/viz_lab_state_maps.html.
- **Data path:** `webapp/routes/experimental.py` prototype builders over the C-07 / C-08 sources (the prototype
  data endpoint is cached); triple-pipeline JSON artifacts on disk (not deployed to prod);
  `data/geometry/il/*.topo.json` (validated by `scripts/validate_geometry_keys.py`).
- **Figures shown:** registry rows for `experimental.viz_lab` (race money vs outside pressure, race funding
  concentration, cumulative inflow, payee dominance, entity resolution risk, network slice).
- **Windowed?** Yes, by its own window parameters.
- **Proposed integrity checks:** one race-money prototype value against the C-07 recomputation (DI-028); geometry
  keys by the existing validator.
- **Sample n and why:** n = 10 prototype values; local-only and not public, so a smoke-level bound (< 30 %) is
  deliberate. Revisit if a prototype ships.
- **Batch:** 9.

## Singletons

### S-01 — Home

- **Members:**
  - `/` — main.index
- **Representative:** /.
- **Templates:** index.html.
- **Data path:** `_get_candidate_stats_cached` (`bulk_candidate_committee_finance_agg`, `bulk_receipts_clean`,
  `bulk_d2_totals_clean`, FEC tables), `_get_top_donors_cached` (`analytics_donor_summary`, `bulk_receipts_clean`),
  527 and lobbying counts (`irs527_*`, `lobbying_entities`, `lobbying_clients`), dashboard insights. Cached
  (homepage candidate stats, top donors, dashboard insights).
- **Figures shown:** 22 registry rows for `main.index`: "Local Candidate Rows", "Local Candidates", "Local
  Committees", "Local Receipts", "Federal Candidates", "Federal Contributions", "Federal Disbursements (Schedule
  B)", "Federal Independent Expenditures (Schedule E)", "Matched Donor Pairs", "OpenBook Vendors Matched",
  "Archived Local Filings", "527 Organizations", "Total 527 Expenditures", "Total 527 Contributions Received",
  "Director-Donor Overlaps", "Lobbying Entities", "Top Donors", ...
- **Windowed?** Yes, default current cycle; mixed axes (registry gives each card's axis).
- **Proposed integrity checks:** every stat card recomputed (DI-029); Top Donors recomputed (DI-030).
- **Sample n and why:** census of the stat cards (22); Top Donors top 10.
- **Batch:** 4.

### S-02 — Committee directory

- **Members:**
  - `/committees/` — committees.list_committees
- **Representative:** /committees/.
- **Templates:** committees/list.html.
- **Data path:** `isbe_committees`, `isbe_committee_money`, a windowed receipts CTE over `bulk_receipts_clean`;
  `?by=family` groups accepted `committee_family` rows.
- **Figures shown:** "Committees" table with windowed receipts.
- **Windowed?** Yes, default current cycle, axis `received_date`.
- **Proposed integrity checks:** a row's windowed receipts recomputed (DI-031).
- **Sample n and why:** n = 30 rows (top / middle / small); the figure is the C-04 Receipts Summary over a window.
- **Batch:** 3.

### S-03 — Candidate directory

- **Members:**
  - `/candidates` — main.candidates
- **Representative:** /candidates.
- **Templates:** candidates.html.
- **Data path:** `_get_candidate_stats_cached` (same feeder as the home page's candidate cards).
- **Figures shown:** 7 registry rows for `main.candidates` ("Candidates", "Committees", "Total Receipts", "Total
  Expenditures", ...).
- **Windowed?** Yes, default current cycle.
- **Proposed integrity checks:** stat cards recomputed (DI-032).
- **Sample n and why:** census of the cards.
- **Batch:** 4.

### S-04 — Compare

- **Members:**
  - `/compare` — main.compare
- **Representative:** /compare.
- **Templates:** compare.html.
- **Data path:** `bulk_candidate_committee_finance_agg`, `bulk_candidates_clean`, `bulk_committee_candidate_links`,
  `bulk_receipts_clean`, `isbe_candidacies`.
- **Figures shown:** compare profiles: stat cards, Top Donors, Election History.
- **Windowed?** Yes, default current cycle.
- **Proposed integrity checks:** each stat card equals the profile figure for the same entity and window
  (DI-033).
- **Sample n and why:** n = 10 comparisons (pairs from C-02 and C-04 samples).
- **Batch:** 8.

### S-05 — About

- **Members:**
  - `/about` — main.about
- **Representative:** /about.
- **Templates:** about.html.
- **Data path:** none (static text).
- **Figures shown:** none.
- **Windowed?** No.
- **Proposed integrity checks:** none; no figures.
- **Sample n and why:** not applicable.
- **Batch:** none.

### S-06 — Privacy

- **Members:**
  - `/privacy` — main.privacy
- **Representative:** /privacy.
- **Templates:** privacy.html.
- **Data path:** none (static text). Redactions themselves are checked inside every donor sample (C-01 excludes
  and spot-checks redacted rows).
- **Figures shown:** none.
- **Windowed?** No.
- **Proposed integrity checks:** none; no figures.
- **Sample n and why:** not applicable.
- **Batch:** none.

## Non-page endpoints

| Rule | Endpoint | Serves | Figures needing a check? |
|---|---|---|---|
| `/robots.txt` | robots_txt | crawler rules | no |
| `/static/<path:filename>` | static | CSS, JS, images | no |
| `/auth/login` | auth.login (GET, POST) | sign-in form | no |
| `/auth/logout` | auth.logout | sign-out redirect | no |
| `/admin/` | admin.admin_hub | admin links | no |
| `/admin/committee-families` | admin.committee_family_queue | family review queue | no; member totals restate C-04 |
| `/admin/committee-families/<int:family_id>` | admin.committee_family_detail | one family's evidence | no; restates C-04 |
| `/admin/committee-families/<int:family_id>/decision` | admin.committee_family_decision (POST) | records a review decision | no |
| `/admin/donor-merges` | admin.donor_merge_queue | donor merge review queue | no; member totals restate C-01 |
| `/admin/donor-merges/<path:entity_id>` | admin.donor_merge_detail | one merge candidate | no; restates C-01 |
| `/admin/donor-merges/<path:entity_id>/decision` | admin.donor_merge_decision (POST) | records a merge decision | no |
| `/admin/federal-receipt-audit` | admin.federal_receipt_audit | internal FEC receipt audit | yes, internal: DI-034 |
| `/admin/federal-disbursement-audit` | admin.federal_disbursement_audit | internal FEC disbursement audit | yes, internal: DI-034 |
| `/manual-entry/` | manual_entry.queue | manual data-entry queue (signed in) | no; queue counts only |
| `/manual-entry/<int:queue_id>` | manual_entry.entry_form (GET, POST) | entry form, writes legacy contributions | no; write path, item 2's concern |
| `/527/suggest` | irs527.suggest | autocomplete JSON | no |
| `/committees/suggest` | committees.suggest_committees | autocomplete JSON | no |
| `/lobbying/suggest` | lobbying.suggest_entities | autocomplete JSON | no |
| `/lobbying/flows/suggest` | lobbying.suggest_flow_clients | autocomplete JSON | no |
| `/api/analytics/network/suggest` | api.analytics_network_suggest | autocomplete JSON | no |
| `/api/federal/network/suggest` | api.federal_network_suggest | autocomplete JSON | no |

Other JSON endpoints that carry figures are members of their class: the entity `network.json` rules (C-03), the
lobbying flows data (C-11), the analytics API (C-16), the v1 API (C-15) and the Viz Lab data (C-17).

## Coverage

`url_map` has 128 rules; 128 listed above (17 classes with 101 rules, 6 singletons with 6 rules, 21 non-page
endpoints), each in backticks exactly once. Recompute with the Flask app: collect `app.url_map.iter_rules()`
rule strings (as `scripts/audit/route_sweep.py` `enumerate_routes()` does, but without dropping `static` or
non-GET rules) and compare them with every backticked string starting with a slash in this file; the lists must
match with no duplicates. Re-run whenever a route is added or removed, and add the new rule to its class.
