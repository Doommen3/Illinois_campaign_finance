# Graphs Roadmap (Current Data)

## Scope and Constraints
- This roadmap is doc-first and assumes lightweight prototypes only (no heavy compute jobs).
- Global period filter semantics should stay consistent with the app:
  - Contribution-driven metrics: filter by transaction date (`received_date`, `expended_date`, `contribution_receipt_date`, `disbursement_date`, `expenditure_date`).
  - Report-driven metrics: filter by `filed_date` when report tables are used.
- Local dataset scale observed in the current DB snapshot (2026-02-16):
  - `bulk_receipts_clean`: 3,614,883 rows
  - `bulk_expenditures_clean`: 3,119,087 rows
  - `analytics_donor_summary`: 1,015,579 rows
  - `analytics_donor_committee_agg`: 1,177,443 rows
  - `lobbying_entity_clients`: 14,195 rows
- Federal `fec_*` tables are not present in the current local DB snapshot; federal ideas below are still valid but gated on ingest.

## 12 Concrete Chart Ideas

| # | Question Answered | Chart Type | Required Joins | Fields Needed | Filters (Global Range) | Performance Notes |
|---|---|---|---|---|---|---|
| 1 | Which state races are most money-heavy and outside-spending-heavy at the same time? | Bubble scatter (`x=total receipts`, `y=outside pressure`, `size=donor count`) | `bulk_candidate_committee_finance_agg` -> `bulk_receipts_clean` on `committee_id_sbe`; optional `bulk_expenditures_clean` by `candidate_name` (part 9 rows) | `office_sought`, `district_type`, `district`, `sum_total_receipts`, `amount`, `d2_part_code`, `candidate_name` | `received_date` and `expended_date` between `date_from/date_to` | Pre-aggregate by committee and race in SQL CTEs; do not pull raw rows to Python. |
| 2 | Which districts are broad-based vs concentrated by donor base? | Quadrant chart (`donor_count` vs `top_donor_share`) | `analytics_donor_committee_agg` -> `bulk_cmte_candidate_links_clean` on `committee_id`; optional `bulk_candidates_clean` on `candidate_id` | `donor_key`, `total_amount`, `committee_id`, `candidate_id`, district fields | `analytics` rows constrained by period-specific rebuild or direct receipts date filter | Use top-N candidate/race window first (for example top 200 races by amount). |
| 3 | How quickly is money entering each competitive district over time? | Small-multiple cumulative line charts by race | `bulk_receipts_clean` -> `bulk_cmte_candidate_links_clean` -> `bulk_candidates_clean` | `received_date`, `amount`, `committee_id_sbe`, `candidate_id`, district fields | `received_date` in global range | Bucket to week/month in SQL; render max 12-20 districts per view. |
| 4 | Are top districts funded by many small gifts or fewer large gifts? | Box/violin + percentile bands by district | `bulk_receipts_clean` -> `bulk_cmte_candidate_links_clean` -> `bulk_candidates_clean` | `amount`, `received_date`, district fields | `received_date` in range | Use sampled quantiles (`percentile_cont`) per district; avoid client-side raw point plotting. |
| 5 | Which lobbying clients have the widest district footprint? | District-by-client heatmap | `lobbying_entity_clients` -> `lobbying_donor_matches` on `client_id` -> `analytics_donor_committee_agg` on `donor_key` -> candidate links for district | `client_id`, `client_name`, `donor_key`, `committee_id`, district fields | `reg_year` between `YEAR(date_from)`/`YEAR(date_to)` plus receipts window | Materialize intermediate `client -> committee` totals keyed by period; cap to top 100 clients. |
| 6 | Which payees dominate spending across districts? | Treemap or packed bubbles by payee with district facet | `bulk_expenditures_clean` -> `bulk_cmte_candidate_links_clean` -> `bulk_candidates_clean`; optional `lobbying_expenditure_matches` | `payee_last_or_business_name`, `amount`, `committee_id_sbe`, district fields | `expended_date` in range | Normalize payee names first (trim/case/punctuation) in SQL CTE to reduce duplicates. |
| 7 | Which districts show unusual donor turnover cycle to cycle? | Cohort retention heatmap (donor survival) | `analytics_donor_committee_agg` -> candidate links/district | `donor_key`, `committee_id`, district fields, inferred cycle/year | compare current cycle window vs prior cycle window | Start with top 50 districts by receipts; cohort math on sampled districts first. |
| 8 | Where are committees acting as funding hubs across many candidates? | Bipartite projection + hub ranking bars | `analytics_donor_committee_agg` + `bulk_cmte_candidate_links_clean` + `bulk_candidates_clean` | `committee_id`, `candidate_id`, donor totals, district fields | period window on receipts | Use precomputed committee totals and degree counts; avoid all-pairs joins without caps. |
| 9 | How much money flows from matched lobbying clients into each race path? | Layered Sankey (`client -> donor -> committee -> race`) | `lobbying_donor_matches` -> `analytics_donor_committee_agg` -> candidate links | `client_name`, `donor_key`, `committee_id`, `total_amount`, race labels | receipts range + lobbying year range | Use confidence threshold (`score >= 0.9`) and top-K edges per layer to keep graph readable. |
| 10 | Which races are most exposed to a single economic sector (utilities, labor, gaming, etc.)? | Stacked bars by race and sector | `analytics_donor_summary` + donor normalization/classification table -> committee/race links | `donor_name`, `donor_key`, `total_amount`, sector label, race | receipts range | Sector classification should be cached in a small lookup table; recompute only changed donors. |
| 11 | How does state-vs-federal overlap differ by district? | Dual-axis district map + slope chart | `fec_local_donor_matches` -> `analytics_donor_committee_agg` (state) and `fec_schedule_a_contributions` (federal) -> race mapping | `local_donor_key`, `federal_donor_entity_key`, district/race keys, amounts | state: receipts range; federal: cycle/date range | Gate behind `fec_*` availability; pre-aggregate to district-level totals before join. |
| 12 | Where are entity-resolution errors likely distorting insights (example: Ameren variants)? | Data quality dashboard (variant count + money at risk) | `analytics_donor_summary` self-aggregation on normalized names; optional `lobbying_clients` alias mapping | `donor_name`, `donor_key`, `total_amount`, normalized canonical name | same as source table window or latest snapshot | Lightweight SQL only; this should run fast and guide manual alias curation. |

## Recommended Delivery Order (Low Risk)
1. Ideas 1, 2, and 3 (state-race metrics using existing `bulk_*`/`analytics_*` tables).
2. Ideas 5 and 9 (lobbying bridge extensions, confidence-thresholded).
3. Idea 12 (entity-resolution quality panel to improve all downstream charts).
4. Federal-gated ideas (11 and federal variants of others) after `fec_*` data is present locally.
