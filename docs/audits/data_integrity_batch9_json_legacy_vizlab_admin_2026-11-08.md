# Data integrity, batch 9: analytics JSON API, legacy pages and v1 API, Viz Lab, admin audits (2026-11-08)

To-do 2026-10-28 item 10, batch 9, the last batch of the master list: classes **C-16** (analytics JSON API),
**C-15** (legacy scraper pages and the v1 JSON API), **C-17** (the local-only Viz Lab) and the two admin federal audit
pages, checks DI-026, DI-027, DI-028 and DI-034, plus three new checks, DI-058, DI-059 and DI-060. This is a
read-only analysis of the local `ilcf` database. Every query ran with
`PGOPTIONS='-c default_transaction_read_only=on'`. Nothing was fixed in this unit. Plan:
`.claude/plans/data-integrity-batches.md` §Batch 9 and §RESUME 2026-11-08. The batch was started on 2026-10-30,
paused after M1, and re-measured from scratch on 2026-11-08 (the earlier partial scripts were lost with the scratch
clone); every figure below was measured on 2026-11-08 between 06:16Z and 06:40Z.

Public-repo rule: the report names candidates, committees, organizations, vendors and races only. Individual
donors appear only as md5 prefixes of their donor key (or as the payload's own hashed node id), never by name,
address or key.

## Result

| Check | Figure | n | Result |
|---|---|---|---|
| DI-026 | Legacy pages and v1 API counts | census + 30 legacy committees + 10 reports + 5 donors | **fail**: the `/legacy` Donors card, `/api/stats` `donors` and the `/api/donors` list read the ISBE donor directory (251,347), not the legacy `donors` table (561). Everything else exact. |
| DI-027 | Analytics JSON payload values | 14 endpoints, 30 values | **fail**, 25 / 30. Payload = page widget on 14 / 14 endpoints. Fails: NLP ×2 (new DI-058), the network committee→candidate edge (known DI-046), a candidate-competition node at a tie (new DI-060), the 527 graph's empty default window (known DI-049). |
| DI-028 | Viz Lab race money and outside spending | 10 races | **pass**, 10 / 10 (prototype = race list on all 80 rows) |
| DI-034 | Admin federal audit pages | — | **blocked**: no local credential available to the test client (decision E31); manual steps below |
| DI-058 (new) | NLP spending categories read the legacy contributions table | census | **fail** |
| DI-059 (new) | State focus-sankey donor nodes group by last / business name only | 3 focus views, 60 edges | **fail** (committee 25185 all time: 9 / 20 nodes merge more than one first name) |
| DI-060 (new) | Candidate-competition donor pool cut inside a tie | census of the cut | **fail** (185 donors tied at $100,000.00 at rank 2,000; 82 inside) |

## Scope

| Member (`url_map` rule) | Class | What was checked |
|---|---|---|
| `/api/analytics/time-series`, `/geo`, `/anomalies`, `/concentration`, `/nlp`, `/reconciliation`, `/network`, `/donor-cogiving`, `/committee-similarity`, `/candidate-competition`, `/lobbying-influence`, `/irs527-ecosystem`, `/network/focus-sankey` | C-16 | payload vs page context; 28 values recomputed from source |
| `/api/federal/network/focus-sankey` | C-16 | 2 values recomputed from FEC Schedules A / B and `raw_extractions` |
| `/legacy`, `/reports/`, `/reports/<id>`, `/committees/legacy/<id>`, `/api/stats`, `/api/committees[/<id>]`, `/api/donors[/<id>]`, `/api/reports[/<id>]`, `/api/scrape-status` | C-15 | census against the legacy tables |
| `/experimental/viz-lab[/<key>]`, `/experimental/viz-lab/data/race_money_pressure`, `/state-maps`, the two redirects, the triple-pipeline and geometry JSON | C-17 | 10 races recomputed; routes smoke-tested; artifacts dated; geometry keys validated |
| `/admin/federal-receipt-audit`, `/admin/federal-disbursement-audit` | admin | sign-in path read; blocked |

## Data state (local `ilcf`, 2026-11-08)

| Source | State |
|---|---|
| ISBE | receipts to 2026-09-30; latest filing received 2026-09-30 08:02. Matviews not rebuilt by this batch. |
| FEC | Schedule A 89,789 rows (to 2026-07-01), B 13,477, E 5,097 |
| IRS 527 | reloaded by another session, finished 06:24Z: organizations 2,417 (1,959 EINs), reports 1,802, contributions 9,757,402, expenditures 37,693, directors 5,792. This batch read 0 / 30,000 / 7,875,000 (orgs / expenditures / contributions) at 06:20:18Z and 2,417 / 37,693 / 9,757,402 at 06:30:12Z, 06:31:15Z and 06:32:57Z; the 527 check ran after the last stable pair. `irs527_expenditure_recipient_matches` was 11,286 rows at 06:32Z and again at 06:35:54Z. |
| Legacy scraper tables | frozen: `committees` 344, `reports` 650 (63 paper; 526 scraped, 124 skipped; filed 2026-01..02), `contributions` 766 / $5,173,230.59, `donors` 561; `scrape_state` last run 2026-02-06 |
| ISBE donor directory | `donor_entity_local` 251,347 rows |

## Method

- **Test client.** `webapp.app.create_app({"TESTING": True, "ROUTE_PERF_CACHE_ENABLED": False,
  "DASHBOARD_PREWARM_ENABLED": False, "API_REQUIRE_KEY": False})`, with `EXPERIMENTAL_VIZ_LAB_ENABLED = True` for
  C-17. Page figures were read from the template context (Flask's `template_rendered` signal), the exact objects the
  widgets render; a spot check confirmed the rendered HTML carries the same numbers. No server was started.
- **Window.** Pages and API resolve the same scope: `cycle:2026` = 2025-01-01..2026-12-31, data through 2026-09-30.
  `period=all` was used where named.
- **Recomputation.** The Conventions' picked receipts (and picked expenditures) over `isbe_receipts` /
  `isbe_expenditures` / `isbe_filed_docs` / `isbe_d2_reports`, the contributions predicate where the builder uses it,
  and the site donor key. FEC values from `fec_schedule_a_contributions` / `fec_schedule_b_disbursements`, with
  `memo_code` read from `raw_extractions`. 527 values with the dictionary's kept-form rule (latest
  `insert_datetime`, then `form_id`, per EIN and period). E30: each value is compared with the page widget first; batch
  5's report is lost, so every value was recomputed from source.
- Scripts (outside the repo): `b9_app.py` (harness), `b9_dump.py`, `b9_c16_compare.py`, `b9_c16_indep*.py`,
  `b9_sankey_*.py`, `b9_527.py`, `b9_c15*.py`, `b9_viz*.py` in the session scratchpad `batch9-work/`.

## C-16: analytics JSON API (DI-027)

### Endpoint → widget → rule

| Endpoint | Page widget (template context) | Builder rule | Payload = widget |
|---|---|---|---|
| `time-series` | `/analytics/geography` `time_series` (also `/analytics/`) | `get_time_series`: monthly contributions, window | yes |
| `geo` | `/analytics/geography` `geo_summary` | `get_geo_summary`: condensed contributions, city key strips punctuation | yes |
| `anomalies` | `/analytics/risk` `anomalies` | `get_anomaly_flags` | yes |
| `concentration` | `/analytics/donors` `concentration` | `get_donor_concentration` over the windowed donor-committee rows | yes |
| `nlp` | `/analytics/donors` `nlp_summary` | `get_nlp_spending_summary` (all time) | yes |
| `reconciliation` | `/analytics/risk` `reconciliation` | `get_reconciliation_outliers` over `bulk_d2_receipts_recon` (all time) | yes, except two tied pairs in swapped order (rows 8/9: filed docs 697465 / 743880; rows 18/19: 906305 / 887550; equal `abs_diff`) |
| `network` | `/analytics/networks` `network` | `get_network_graph` | yes |
| `donor-cogiving` | `/analytics/relationships` `donor_cogiving` | all-time agg tables | yes |
| `committee-similarity` | `/analytics/relationships` `committee_similarity` | all-time agg tables | yes with the page's parameters (`min_shared_donors=2&min_shared_amount=5000`); the bare API defaults to 3 / $10,000 and returns another graph (43 vs 51 nodes) |
| `candidate-competition` | `/analytics/relationships` `candidate_competition` | top-2000 donor pools | yes with `min_shared_amount=5000`; the bare API defaults to $2,500 (summary fields differ, graph identical) |
| `lobbying-influence` | `/analytics/relationships` `lobbying_influence` | windowed | yes |
| `irs527-ecosystem` | `/analytics/relationships` `ecosystem_527` | windowed | yes in both windows (0 nodes default; 247 nodes / 200 edges all time) |
| `network/focus-sankey` | `/analytics/networks` Custom Sankey (renders the JSON) | own SQL over `bulk_receipts_clean` / `bulk_expenditures_clean` / finance agg | n/a (no server widget) |
| `federal/network/focus-sankey` | federal networks Custom Sankey (renders the JSON) | own SQL over Schedules A / B | n/a |

### Values (n = 30, at least one per endpoint)

| # | Endpoint | Value (payload) | Recomputed | Result |
|---|---|---|---|---|
| V01 | time-series | 2025-01: $7,287,697.97 / 6,136 | same | pass |
| V02 | time-series | 2025-09: $18,023,659.92 / 11,236 | same | pass |
| V03 | time-series | 2026-09: $14,070,121.77 / 2,520 | same | pass |
| V04 | geo | IL: $279,502,585.44 / 134,772 | same | pass |
| V05 | geo | PA: $1,202,728.64 / 901 | same | pass |
| V06 | geo | Chicago, IL: $160,461,188.07 / 36,686 | $160,451,738.07 / 36,674 with `UPPER(TRIM(city)) = 'CHICAGO'` plus $9,450.00 / 12 rows whose city carries punctuation; the page's city key strips it | pass |
| V07 | anomalies | large single contribution $65,000,000.00, 2026-08-10, committee 32762 (JB for Governor) | one picked live row; the window's largest | pass |
| V08 | anomalies | $25,000,000.00, 2025-11-13, committee 32762 | one picked live row; the window's second | pass |
| V09 | concentration | committee 23199: $7,113,031.16, 1 donor | same | pass |
| V10 | concentration | committee 36876: $268,145.25, 1 donor | same | pass |
| V11 | nlp | "uncategorized" $5,060,587.50 / 742 entries | the rows are the legacy `contributions` table, not spending (DI-058) | **fail** |
| V12 | nlp | all categories $5,173,230.59 / 766 | = `SUM(amount)` of legacy `contributions`; ISBE live expenditures all time are $6,158,907,151.51 / 3,375,478 rows | **fail** |
| V13 | reconciliation | filed doc 642754 (committee 25185): D-2 total receipts $50,019,878.85; receipts $0.00 | `isbe_d2_reports.total_receipts` = $50,019,878.85 (an archived D-2); the filing's 17 receipt rows are all archived, live sum $0.00 | pass |
| V14 | reconciliation | filed doc 697465 (committee 32762): $34,313,156.53; $0.00 | same (archived D-2; 35 receipt rows, all archived) | pass |
| V15 | network | donor→committee 32762, node hash `fab87e51`: $90,500,000.00 / 3 | same (raw-field donor group, window) | pass |
| V16 | network | committee 32762 → candidate 39266: $378,380,000.00 | $378,380,010.09 = 38 D-2s, one live per period; the finance agg sums to $378,380,219.05 in NUMERIC over its 11 rows, and the payload is its single-precision REAL sum | **fail** (known DI-046, F-B5a) |
| V17 | donor-cogiving | donor node md5 `9300a69f`: $9,112,000.00, 5 committees | same (6 picked rows, all time) | pass |
| V18 | donor-cogiving | donor node md5 `6da4054d`: $14,821,100.00, 16 committees | same (26 rows) | pass |
| V19 | committee-similarity | committee 25185: $167,392,472.67 / 8,510 donors | same | pass |
| V20 | committee-similarity | committee 16283: $17,682,976.60 / 3,017 donors | same | pass |
| V21 | candidate-competition | federal S6IL00482: $25,999,663.42 / 759 pool donors | same (pool cut at $6,250, where 13 donors tie; the recomputation's pool gives the same total) | pass |
| V22 | candidate-competition | state 39266: $440,055,500.00 / 16 pool donors | $440,155,500.00 / 17 | **fail** (DI-060: the cut sits inside a $100,000 tie) |
| V23 | lobbying-influence | donor_committee_flow, Chicagoland Chamber of Commerce → committee 580 (Chicagoland Chamber of Commerce PAC): $81,590.00 | same (4 receipts; `row_count` 1 counts aggregated rows) | pass |
| V24 | lobbying-influence | client_entity, client 4016 → entity 4683: 2 | 2 registration years (2025, 2026) | pass |
| V25 | irs527-ecosystem | default window: 0 nodes / 0 edges | the window holds 2,915 expenditure rows from 15 EINs and 1,479,592 contribution rows from 243 EINs; every stored date is YYYYMMDD | **fail** (known DI-049, F-B5b) |
| V26 | irs527-ecosystem | all time, org 912064912 → committee 7684: $52,950.00 | $52,950.00 / 29 rows, all on kept forms (18 forms) | pass |
| V27 | network/focus-sankey | committee 6239 (Democratic Party of Illinois), cycle, top donor node hash `d20ef9fd`: $5,210,000.00 / 7 | same (one site key) | pass |
| V28 | network/focus-sankey | committee 6239 → vendor "Democratic Party of Illinois - Federal": $3,128,337.68 / 61 | same (picked expenditures; 0 archived rows) | pass |
| V29 | federal/network/focus-sankey | committee C00904466 (Raja for Illinois), cycle 2026, top donor (FEC donor entity key prefix `nsz_9910`): $19,327,171.21 / 15 | same; all 15 rows have an empty `memo_code` | pass |
| V30 | federal/network/focus-sankey | C00904466 → vendor Adelstein & Associates LLC: $4,575,845.00 / 13 | same | pass |

**25 / 30 pass.** Two of the five fails are known (DI-046, DI-049); three are new (two values of DI-058, one of
DI-060). The serialization path itself is clean: every payload equals its page widget.

### Mismatch SQL (reproducible)

**DI-058, NLP categories (V11, V12).** The builder reads the first table that exists, and the legacy contributions
table exists (`database/analytics.py:4435`); `d2_itemized_entries` is empty (0 rows), so nothing is added:
```sql
SELECT COUNT(*), SUM(amount) FROM contributions WHERE amount > 0;                      -- 766, 5,173,230.59 = payload
SELECT COUNT(*) FROM d2_itemized_entries;                                               -- 0
SELECT COUNT(*), SUM(amount) FROM isbe_expenditures WHERE archived = FALSE AND amount > 0; -- 3,375,478, 6,158,907,151.51
```
The widget is titled as spending, but it categorizes the descriptions of 766 frozen legacy contributions.

**DI-060, competition pool tie (V22).**
```sql
WITH p AS (SELECT donor_key, total_amount, ROW_NUMBER() OVER (ORDER BY total_amount DESC) rn
           FROM analytics_donor_summary WHERE source = 'bulk_receipts')
SELECT (SELECT total_amount FROM p WHERE rn = 2000)                                  AS cut,          -- 100,000.00
       (SELECT COUNT(*) FROM p WHERE total_amount = 100000)                           AS tied,         -- 185
       (SELECT COUNT(*) FROM p WHERE rn <= 2000 AND total_amount = 100000)            AS tied_inside;  -- 82
```
Three of candidate 39266's donors have exactly $100,000.00 all time (rank range 1,919..2,103); which of them enter
the pool depends on the scan order, so the node's total moves in $100,000 steps between builds. The same
`ORDER BY ... LIMIT` without a tiebreak sits in the federal pool (13 donors tied at the $6,250 cut).

**DI-046 and DI-049** reproduce with the batch 5 queries (reconstruction document §3): V16's REAL sum is
`SELECT SUM(sum_total_receipts) FROM bulk_candidate_committee_finance_agg WHERE committee_id_sbe = 32762` (378,380,000)
vs `SUM(sum_total_receipts::numeric)` (378,380,219.05); V25's empty window is `SELECT COUNT(*) FROM
irs527_expenditures WHERE date ~ '^\d{4}-'` = 0 against the builder's dashed-date parser.

### Other findings in C-16

- **DI-059, state focus-sankey donor grouping.** The committee, candidate and vendor focus branches of
  `/api/analytics/network/focus-sankey` group a committee's inbound receipts by `last_or_business_name` only, over
  all receipt parts (transfers in included), and hash that name into the node id. Measured against picked receipts:

  | Focus | Donor edges | Amount + count = picked last-name group | Nodes merging > 1 first name |
  |---|---|---|---|
  | committee 25185, all time | 20 | 20 | **9** |
  | committee 6239, all time | 20 | 20 | 0 |
  | committee 6239, cycle 2026 | 20 | 20 | 0 |

  In committee 6239's all-time view, two raw spellings of one name (md5 `86b14060` and `19aaed4e` of the raw last
  name) hash to one node id, so the node carries two parallel edges, $20,410,000.00 / 23 rows and $3,170,000.00 / 4
  rows, each exact for its spelling.
  ```sql
  SELECT r.last_name, COUNT(DISTINCT COALESCE(r.first_name,'')) AS first_names, SUM(r.amount), COUNT(*)
  FROM isbe_condensed_receipts r
  WHERE r.committee_id = 25185 AND r.archived = FALSE AND r.amount > 0
  GROUP BY r.last_name ORDER BY 3 DESC LIMIT 20;   -- 9 of the 20 have first_names > 1
  ```
- **Federal focus-sankey memo rows.** Beside V29, the second donor edge of C00904466 is ActBlue, $1,322,782.56 /
  2,077 rows, and every one of those rows has `memo_code = 'X'` in `raw_extractions` (earmark memo entries that
  repeat contributions already itemized under the individual donors). This is the known FEC memo class (DI-052,
  fixes F-B6a / F-B6c); the federal sankey is one more query site for F-B6c's shared filter.
- **Reconciliation tie order.** `get_reconciliation_outliers` sorts by the absolute difference only, so equal rows
  come back in any order (two pairs swapped between page and API). The rows themselves are equal.
- **Reconciliation rows are archived D-2s with no Archived column.** 19 of the 20 shown rows are archived D-2s
  (all but the third; `is_archived = 1`); the page shows no Archived column, so a reader sees a
  $50M "gap" on a superseded filing. The table is the stale legacy recon table (DI-041, F-B3b).
- **527 recipient match table duplicates.** `irs527_expenditure_recipient_matches` holds 11,286 rows for 3,520
  distinct (EIN, recipient, matched id) triples; the pair in V26 appears 31 times. The edge stays correct because
  the builder looks amounts up by key, but duplicates consume the `edge_limit * 2` row budget. Covered by F-B7b
  (distinct recipients in the matcher).
- **Bare-API defaults.** `committee-similarity` (3 shared donors / $10,000) and `candidate-competition` ($2,500)
  default to other thresholds than the relationships page (2 / $5,000 and $5,000), so the API without parameters
  serves another graph than the page shows.

## C-15: legacy pages and v1 API (DI-026)

| Figure | Page / API | Legacy table | Result |
|---|---|---|---|
| Committees | `/legacy` 344; `/api/stats` 344; `/api/committees` total 344 | 344 | pass |
| Reports | `/legacy` 650; `/api/stats` 650; `/reports/` 650 (cycle:2026 and all time); `/api/reports` total 650 | 650 (all filed 2026-01..02) | pass |
| Paper-filed / status | `/api/stats` 63; scraped 526, skipped 124 | 63; 526 / 124 | pass |
| Contributions | `/legacy` 766; `/api/stats` 766, total 5173230.590000001 | 766 / $5,173,230.59 | pass (float noise only) |
| **Donors** | **`/legacy` card 251347; `/api/stats` 251,347; `/api/donors` total 251,347 with `id` null** | **561** | **fail** |
| `/committees/legacy/<id>` Total Contributions | 30 / 30 | per committee | pass |
| `/api/committees/<id>` `total_contributions`, `report_count` | 30 / 30 | per committee | pass |
| `/reports/<id>` total + rows, `/api/reports/<id>` contributions | 10 / 10 | per report | pass |
| `/api/donors/<id>` `total_contributions`, `contribution_count` | 5 / 5 | per donor | pass |
| `/api/scrape-status` | `details` / `main_list` latest runs | `scrape_state` latest rows (2026-02-06) | pass |

**Samples.** Legacy committees, 10 / 10 / 10 from the top, middle and small end of the 254 committees with
contributions: 152, 67, 19, 1, 300, 207, 154, 28, 36, 43; 111, 147, 290, 231, 191, 34, 193, 200, 266, 103; 114, 238,
329, 273, 159, 296, 56, 255, 251, 254. Four committees without contributions (3, 7, 8, 9) render $0.00. Reports: the
top 4, middle 3 and bottom 3 by total among reports with contributions. Legacy donors 216, 76, 282, 58, 60.

**Cause of the fail.** `Donor.count` (`database/models.py:1539`) and `Donor.get_all_with_totals` read the donor
directory source (`get_directory_source`), which is the ISBE directory, while every other legacy figure and
`/api/donors/<id>` read the legacy tables:
```sql
SELECT (SELECT COUNT(*) FROM donors) AS legacy_donors,                -- 561
       (SELECT COUNT(*) FROM donor_entity_local) AS directory_rows;   -- 251,347 = card, /api/stats, /api/donors total
```
The list endpoint's rows carry `id = null`, so a client cannot follow a list row to `/api/donors/<id>`.

## C-17: Viz Lab (DI-028)

**Routes (test client, Viz Lab flag on).** `/experimental/viz-lab` 200, `/experimental/viz-lab/race_money_pressure`
200, `/experimental/viz-lab/state-maps` 200, `/experimental/viz-lab/` and `/triple-pipeline` 302 to
`/experimental/viz-lab`, `/data/race_money_pressure` 200 (80 races, $258,751,542.47 receipts, $5,677,420.77 outside
spending), `/data/geometry/il/status` 200.

**Race money vs outside pressure.** The prototype calls `get_state_race_analytics(limit=80)`, the builder of the
state-races list that batch 5 verified (DI-013). All 80 prototype rows equal their `/analytics/state-races/` row
(total, outside spending, donors, candidates). Ten races were recomputed from source: candidacies at
`election_year + election_year % 2 = 2026`, race identity by the site's `_state_race_slug` over
`isbe_candidates.office / district_type / district`, picked receipts (all parts) over the race's distinct committees in
the window; outside spending by DI-005's Part 9 query per candidate surname, then DI-005's name rule.

| Rank | Race key | Total (prototype = recomputed) | Outside spending | Part 9 rows explained |
|---|---|---|---|---|
| 1 | `governor-statewide-illinois` | $98,317,203.48 (9 committees) | $44,303.10 = raw | — |
| 2 | `secretary-of-state-statewide` | $20,859,701.76 | $510,000.00 = raw | — |
| 3 | `state-representative-representative-7` | $17,414,778.62 | $0.00 | 4 rows / $1,385.30 name another first name (a mayoral candidate) |
| 4 | `state-senator-senate-39` | $15,129,681.72 | $0.00 | none |
| 40 | `state-senator-senate-9` | $760,245.91 | $0.00 | none |
| 41 | `state-representative-representative-73` | $738,621.01 | $0.00 | none |
| 42 | `state-representative-representative-28` | $712,736.63 | $0.00 | none |
| 78 | `state-senator-senate-23` | $456,344.52 | $0.00 | none |
| 79 | `state-representative-representative-29` | $444,420.08 | $9,900.00 = the candidate's 2 rows | 3 rows / $29,348.85 name another first name |
| 80 | `state-representative-representative-92` | $438,032.56 | $0.00 | none |

**10 / 10 pass.** Rank 4's total equals `committeeman-township-oak-park`'s: committee 16283 feeds both races (the
known candidate-office quirk, batch 2).

**Artifacts.** The triple-pipeline JSON (`graph.json`, `ego/`, `timeline_index.json`) is gitignored. It exists only
in the main tree's `artifacts/triple_pipeline/`, dated 2026-02-16, so in this worktree `graph`, `entities` and `ego`
return 404 and `timeline` returns an empty series. Classified as **artifact, dated 2026-02-16**, not compared with live
data. The geometry TopoJSON is gitignored too (absent in the worktree, so `status.all_present` is false here); the main
tree's files (2026-02-23) pass `scripts/validate_geometry_keys.py`: CD119 17, SLDL 118, SLDU 59 features, "Geometry
validation passed".

## Admin audit pages (DI-034): blocked

Both pages are behind `login_required` (`webapp/auth.py`), which reads `session["manual_entry_user_id"]` and an active
`app_users` row; `/auth/login` checks a username and password with `AppUser.verify_credentials`. The local
`app_users` table has 1 active user and no credential for it is available to this batch. Per decision E31 no password
was guessed, no session was forged and prod was not read. Manual steps for Devin are in DI-034 and under "Manual
checks" below. What the pages compute (`database/federal_fec.py`): per candidate and cycle, the FEC reported
`receipts` / `disbursements` in `fec_candidate_cycle_totals` against `SUM(contribution_receipt_amount)` /
`SUM(disbursement_amount)` of the synced schedule rows, flagged when the difference exceeds `min_abs_diff`
(default $1,000) beyond `tolerance`. The schedule sums include memo and superseded rows (DI-052), which can raise
`schedule_exceeds_reported` flags.

## Mismatch classification

| Finding | Where | Classification | Count |
|---|---|---|---|
| NLP categories built from legacy contributions | `/analytics/donors`, `/analytics/`, `/api/analytics/nlp` | defect (source branch order) | 766 rows, all categories |
| Competition pool cut inside a tie | `/analytics/relationships`, `/api/analytics/candidate-competition` | defect (no tiebreak; nondeterministic) | 185 tied, 82 inside |
| Network committee→candidate edge (REAL sum) | `/analytics/networks`, `/api/analytics/network` | known defect DI-046 | 1 of 1 sampled |
| 527 ecosystem default window empty | `/analytics/relationships`, `/analytics/networks`, API | known defect DI-049 | every window |
| Focus-sankey donor nodes by last name | Custom Sankey, `/api/analytics/network/focus-sankey` | defect (identity) | 9 / 20 nodes for committee 25185 |
| Federal sankey ActBlue memo edge | federal Custom Sankey | known defect class DI-052 | 2,077 rows / $1,322,782.56 for C00904466 |
| `/legacy` Donors, `/api/stats` donors, `/api/donors` list | C-15 | defect (wrong source) | 251,347 vs 561 |
| Reconciliation tie order | `/analytics/risk` vs API | not a mismatch (equal rows) | 2 pairs |
| Bare-API thresholds differ from the page | API | known semantic (documentation) | 2 endpoints |
| Chicago city key strips punctuation | geo | not a mismatch (page normalization) | 12 rows / $9,450.00 |
| 527 match-table duplicates | `irs527_expenditure_recipient_matches` | known defect class (F-B7b) | 11,286 rows / 3,520 distinct |

## Proposed fixes (not implemented)

| Fix | Check | Change | Footprint |
|---|---|---|---|
| F-B9a | DI-058 | Read ISBE expenditures first (condensed, `archived = FALSE`, `purpose`); drop the legacy `contributions` branch from a spending widget | `get_nlp_spending_summary`, `database/analytics.py:4431` + one test |
| F-B9b | DI-060 | Add a deterministic tiebreak (`ORDER BY total_amount DESC, donor_key`) to both donor pools, or cut at a threshold that keeps every tied donor | `get_candidate_competition_networks`, `database/analytics.py` ≈ 5732–5752 (state) and ≈ 5810–5820 (federal) |
| F-B9c | DI-027 | Tiebreak the reconciliation sort (`..., filed_doc_id`) and show an Archived column on the risk page | `get_reconciliation_outliers` (`database/analytics.py:2674`), `analytics/risk.html` |
| F-B9d | DI-027 | Align the bare-API defaults with the relationships page (similarity 2 / $5,000, competition $5,000), or document them | `webapp/routes/api.py` `analytics_committee_similarity`, `analytics_candidate_competition` |
| F-B9e | DI-026 | Count legacy donors from the legacy `donors` table on `/legacy` and in `/api/stats` | `webapp/routes/main.py` `legacy`, `webapp/routes/api.py` `stats`, a legacy count in `database/models.py` |
| F-B9f | DI-026 | Make the `/api/donors` list read the legacy table its detail endpoint reads (or label it as the ISBE directory and drop `id`) | `webapp/routes/api.py` `list_donors` |
| F-B9g | DI-059 | Group focus-sankey donors by the site donor key (the function already builds `_bulk_receipts_donor_key_sql`) under the contributions predicate | `webapp/routes/api.py` focus-sankey committee / candidate / vendor branches (≈ 985–1000 and the two `GROUP BY committee_id_sbe, {receipts_donor_col}` sites) |

Existing fixes this batch leans on: F-B5a (DI-046), F-B5b (DI-049), F-B6a / F-B6c (memo rows, now also the federal
sankey), F-B3b (stale recon table under the reconciliation widget), F-B7b (527 match duplicates).

## Findings for the orchestrator

- **Data dictionary drift (not edited; terminal 1 owns the dictionary).** `donor_entity_local` is 251,347 rows
  locally (dictionary: 246,900). After the 2026-11-08 527 reload, `irs527_contributions.date` holds YYYYMMDD dates
  (9,754,741 rows; 2,661 blank), where the dictionary says the column holds amounts with 0 parseable dates;
  `irs527_reports` is 1,802 forms (dictionary: 1,739); `irs527_expenditures` 37,693 rows (dictionary: 35,914).
- **DI-049 got wider with the reload.** With contribution dates now parseable as YYYYMMDD, the 527 graph's default
  window should hold 243 contributing EINs, not only the 15 spending ones; F-B5b's parser fix should cover both tables.
- **The worktree has no Viz Lab artifacts or geometry.** Both are gitignored, so any worktree or fresh clone shows
  the triple-pipeline as 404 and the state maps without layers. Worth one line in the Viz Lab runbook.
- **Batch 9's DI-027 differs from the 2026-10-30 partial run.** The paused builder counted 26 / 30; this run is 25 /
  30 because its sample includes a candidate-competition node at the pool tie (V22). Both runs agree on every value
  they share.

## Manual checks for these classes

They are the DI-026, DI-027, DI-028, DI-034, DI-058, DI-059 and DI-060 entries in `docs/data_integrity_checks.md`.
Short form for Devin:

1. **C-16.** Open `/api/analytics/time-series` and `/analytics/geography`; one month must match. Open
   `/api/analytics/nlp` and check whether the categories still come from 766 legacy rows (DI-058).
2. **C-15.** Open `/legacy` and `/api/stats`; Donors must read 561 (it reads 251,347 today).
3. **C-17.** Start a local server with `EXPERIMENTAL_VIZ_LAB_ENABLED=1` on port 5068, open
   `/experimental/viz-lab/race_money_pressure`, compare the Governor row with
   `/analytics/state-races/governor-statewide-illinois`, then stop the server.
4. **Admin (DI-034).** Locally: `python run.py create-user` (or use the existing account), start the server, sign in
   at `/auth/login`, open `/admin/federal-receipt-audit?cycle=2026&status=all` and
   `/admin/federal-disbursement-audit?cycle=2026&status=all`, note the status counts and the first 10 rows, and run
   DI-034's queries for those candidates.

## How to find issues like this

- **Compare the payload with the page's template context, not with its HTML.** The context is what the widget
  renders, so equality there proves the serialization path; numbers parsed out of HTML hide rounding and tie order.
- **Call the API with the page's parameters before calling a mismatch.** Two endpoints default to other thresholds
  than the page; the bare call returns another graph and looks like a defect.
- **Read which table a builder actually reads.** `get_nlp_spending_summary` checks for tables in order and stops at
  the first that exists; a frozen legacy table shadowed the live source for every reader.
- **Check the boundary of every top-N pool.** A `LIMIT` without a tiebreak inside a large tie makes a figure
  nondeterministic; count the tied rows at the cut.
- **Group by identity, not by display name.** A last-name group and a hashed display name both merge different
  people and split one person across spellings.
- **Keep the scratch label files out of the repo.** Payloads carry full donor keys; reports cite md5 prefixes only.

## Closing section

**Progress line (as `docs/data_integrity_checks.md` holds it after this commit):** Checks: 42 total — 22 proposed, 0
in progress, 9 pass, 10 fail, 1 blocked (2026-11-08, after batch 9; batches 3–8 statuses pending the reconstruction
commit). The statuses that batches 3–8 measured (DI-008..033 and DI-040..057) are applied from
`docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` in the commit right after this one, so the
program's final Progress line comes from that commit, not from this one.

**Every `fail` in the file after this commit, with its fix:**

| Check | Fix |
|---|---|
| DI-001 | F1, F2a, F4 (batch 1) |
| DI-026 | F-B9e, F-B9f |
| DI-027 | F-B9a (NLP), F-B9b (competition), F-B5a (DI-046), F-B5b (DI-049); hygiene F-B9c, F-B9d |
| DI-030 | F1 |
| DI-035 | F1 |
| DI-036 | F6 |
| DI-037 | F2a, F4 |
| DI-058 | F-B9a |
| DI-059 | F-B9g |
| DI-060 | F-B9b |

**Every `blocked`:** DI-034, no local admin credential available to the test client (E31); manual steps in the
check.

**Still `proposed` in the file after this commit (22):** DI-008 to DI-025, DI-029, DI-031, DI-032 and DI-033. All
22 were run in batches 3–8; their statuses are lost with those reports and are re-applied from the reconstruction in
the next commit. The ids DI-040 to DI-057 stay reserved for that commit. After it, per the reconstruction, DI-047 and
DI-050 remain `proposed` (written but not run in batches 5 and 6).
