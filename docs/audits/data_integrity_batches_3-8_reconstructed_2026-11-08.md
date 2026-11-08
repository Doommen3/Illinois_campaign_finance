# Data integrity, batches 3–8: reconstructed from the session logs (2026-11-08)

To-do 2026-10-28 item 10, batches 3 to 8 of `docs/data_integrity_checks.md`. **This is a reconstruction, not a batch
report.** It replaces six reports that no longer exist.

## 0. What this document is

**What was lost.** On 2026-10-30 six fresh builders ran batches 3 to 8. Each wrote a report
(`docs/audits/data_integrity_batch<N>_<class>_2026-10-30.md`) and edited the master checklist. They committed on a
scratch clone under `/private/tmp`, as the chain `4af5aa4` → `f28fcea` (3) → `6c7feb1` (4) → `df1e85d` (5) →
`be54619` (6) → `e32d4df` (7) → `0618dc3` (8). Only batch 2 (`4af5aa4`) had been merged to `main`. macOS's
`/private/tmp` cleanup purged the clone. Fable found the loss on 2026-11-08 at 06:03Z. A bare repo rebuilt from every
surviving object holds `4af5aa4` but none of the six later commits, and no report or checklist blob from them. A
file search over `/Users/devin` and `/private/tmp` found no batch 3–9 report. Details:
`.claude/plans/data-integrity-batches.md` §RESUME 2026-11-08.

**What it was rebuilt from.** Devin chose reconstruction from the logs at 06:17Z (decision E35). Sources, all
written during the 2026-10-30 session and surviving in the repo or the main tree:

- `.claude/plans/data-integrity-batches.md`, the sections "## Batch 3" to "## Batch 8": each brief, decision journal,
  and log. The log holds the builders' M1 / M2 report lines and Fable's "verification → ACCEPTED" lines with the
  figures Fable re-ran. It also holds §PAUSE / RESUME POINT (the fix-id ranges and the decisions for Devin).
- `to_do/to_do_10_28_26.status.md` §10, the per-batch summaries written at the time.
- `.claude/data-dictionary.md` §"Facts from the data-integrity batches (2026-10-30, batches 4–8)".
- `docs/page_inventory.md`, the corrections dated 2026-10-30 (C-06 year rule, C-13 contract window, C-14 live feed,
  S-04 compare window).
- `docs/refresh_inventory.md` row RF-006.

**Rules of this reconstruction.**

- Every number below is quoted from those sources. Nothing was re-measured, and no database was opened.
- Where a source gives only a verdict, the verdict is recorded and the evidence is marked lost.
- Where an id appears without its original title or description, this says so.
- Every figure is **local** (`ilcf` on Devin's laptop on 2026-10-30). Local ISBE data ran to 2026-09-30. Local FEC data
  was older than prod: Schedule A to 2026-07-01, Schedule B and E upserted 2026-02-13. Local IRS 527 data ran to
  2025-12-31.
- The original builders used read-only database access and the Flask test client with route caches off (batch 4
  onward). Each batch's scratch scripts lived under the scratch clone's scratchpad and are lost with it.

**Lost for every batch.** The sample tables with every member's pass or fail, the drawing queries, the reproducible
SQL for each check and mismatch, the "manual checks" and "how to find issues like this" sections, the per-card and
per-figure rule tables, and the exact checklist text each batch wrote. The status lines, figures and fix ids below
are what survives.

Public-repo rule: committees, candidates, organizations and vendors appear by name. Individual donors never do.

## Summary

| Batch | Classes | Checks run (verdicts) | New checks | Fix ids | Builder time | Progress after |
|---|---|---|---|---|---|---|
| 3 | C-04, C-05, S-02 | DI-008, DI-009, DI-010, DI-011, DI-031 pass | DI-040, DI-041 fail | F-B3a–d | 23.1 of 75 min | 41 = 21 proposed / 13 pass / 7 fail |
| 4 | S-01, S-03, C-06 | DI-030 pass; DI-029, DI-032, DI-012 fail | DI-042 fail | F-B4a–g | 20.3 of 75 min | 42 = 18 / 14 / 10 |
| 5 | C-07, C-08 | DI-013, DI-014, DI-015 pass | DI-043, 044, 045, 048 pass; DI-046, 049 fail; DI-047 proposed | F-B5a–e | 31.5 of 90 min | 49 = 16 / 21 / 12 |
| 6 | C-09, C-10 | DI-016, DI-017, DI-018 fail | DI-051, 052 fail; DI-050 proposed | F-B6a–i | 34 of 90 min | 52 = 14 / 21 / 17 |
| 7 | C-11, C-12 | DI-019, DI-020, DI-021, DI-022 fail | DI-053, 054, 055 fail | F-B7a–h | 28.5 of 90 min | 55 = 10 / 21 / 24 |
| 8 | C-13, C-14, S-04 | DI-025, DI-033 pass; DI-023, DI-024 fail | DI-056, 057 fail | F-B8a–g | 20.2 of 75 min | 57 = 6 / 23 / 28 |

The logged Progress lines reconcile step by step from batch 2's 39 = 26 / 8 / 5. After batch 8 the six checks still
`proposed` are DI-026, DI-027, DI-028 and DI-034 (batch 9's scope), plus DI-047 and DI-050.

---

## 1. Batch 3: committee profiles, filings, D-2 reconciliation, committee directory

**Classes.** C-04 (`/committees/<id>`, `/committees/sbe/<id>`, `/committees/<id>/payees`), C-05
(`/committees/<id>/filings`, `/committees/filing/<filed_doc_id>`, `/d2-reconciliation/`,
`/d2-expenditures-reconciliation/`) and S-02 (`/committees/`).

**Builder and time.** `opus-builder-3`, dispatched 00:48Z. M1 took 7.9 min of 35 and M2 13.4 min of 40, for 23.1 of 75
min in total. Commit `f28fcea` added a 598-line report and changed the checklist by +218 / −53 lines. Fable accepted
M1 at 00:59Z and the batch at 01:16Z.

**Decisions.**

- **E18.** DI-008, DI-009 and DI-031 recompute over the picked receipts and picked expenditures fragment of the
  checklist's Conventions. The check texts were amended the way DI-004 was. The Receipts Summary counts every part.
- **E19.** DI-011 differences count as stale cache when the legacy recon table predates the filing's change (RF-006).
- **E20.** DI-008's D-2 bound was amended. Picked receipts on live-D-2 filings in the window must not exceed the sum of
  `total_receipts + total_inkind`. The tail after the last period end is split off, and the "other" tail must be
  $0.00. Pass means page = picked to the cent, the bound holds, and the "other" tail is $0.00.

**Samples (as logged).** C-04 frame: 13,947 committees with picked receipts above zero. Strata were ranks 1–20,
6,964–6,983 and the last 20. Nine party committees fell in naturally. Ids at or below 348: 292, 185 and 299 fell in
naturally, and 245 and 313 were forced in, replacing 40972 and 41103 ($200 each). S-02 frame: 2,898 committees with
picked receipts in cycle:2026. No exclusions were needed: 1,203 redacted receipt rows on 16 committees keep their
amounts on both sides.

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-008 Receipts Summary | pass | 60 / 60 to the cent against picked receipts (every part), all time, count and This cycle. Under the E20 bound 60 / 60 in both windows, "other" tail $0.00. The bound as first written failed 45 / 60 all time and 50 / 60 cycle, for two wording reasons. In-kind 5A is in the summary but in `total_inkind` on the D-2. A-1 receipts after the last period end (2026-06-30) sit on no sworn D-2 yet: $103,255,337.48 across 10 committees in the cycle window, of which committee 32762 holds one row of $65,000,000.00. |
| DI-009 Expenditures Summary and payees | pass | 60 / 60; payee count gap 0 everywhere. |
| DI-031 Directory windowed receipts | pass | 30 / 30, plus a census of all 2,898 rows against picked receipts: 0 differ, net $0.00. |
| DI-010 Filing detail | pass | 60 / 60 including 12 amended D-2s. 58 equal the raw document. Two Pre-election filings (343531, 415272) render empty by the condensed rule, and the page shows no D-2 summary. |
| DI-011 D-2 reconciliation row | pass | 30 / 30 per page against the current source. Window census: 258 receipt rows and 327 expenditure rows stale, 0 defects. The middle stratum was drawn from non-zero-difference rows. Fable ratified "pass" and moved the class-wide staleness to DI-041. |

**New checks.**

- **DI-040, fail.** The filing detail page renders 281,867 of 454,333 filings with line items as empty: archived
  262,693, Pre-election 18,954, live but not kept 220.
- **DI-041, fail.** The legacy recon tables were built from the 2026-02-06 legacy input. Their latest filing is
  2026-02-05 against 2026-09-30 in `isbe_filed_docs`. 10,437 D-2s are missing (9,819 live, 9,506 in cycle:2026), and
  763 archived flags have changed since. This is evidence for RF-006.

**Fixes proposed.** F-B3a, F-B3b, F-B3c and F-B3d (see §7).

**Other logged findings.** Legacy `committees` has 344 rows whose ids collide with ISBE ids and carry no
`committee_id_sbe`. All five sampled low ids render the ISBE committee on both URL forms. The legacy rows are
reachable only at `/committees/legacy/<id>`. Two source-data facts were recorded. `isbe_filed_docs` has a
`reporting_period_end` of 3023-03-30, a source typo. Archived filing 989644 carries $8.1B of sworn expenditures.

**Fable's re-verification (01:16Z).**

- DI-040 census: 172,466 shown, 262,693 archived, 220 live not kept, 18,954 Pre-election. Identical.
- DI-041: 2026-02-05 against 2026-09-30, with 10,437 missing and 9,819 live.
- Filing 343531: 15 raw rows, page empty. Filing 1015462: 100 rows, $1,314,306.65, equal to the page.
- Recon row for filed doc 989606: an archived D-2 with 0 live rows. Its live sibling 989909 has 3 rows, $2,723,968.46.
- At M1 (00:59Z):
  - Committee 245: page 1 / $1,000.00 / cycle $0.00, equal to picked receipts, rendered by `detail_sbe`.
  - Committee 6239: 11,409 / $151,547,085.37 / cycle $15,406,976.27, equal to picked receipts.
  - Committee 32762: after-period tail $65,000,000.00 in 1 row.
  - S-02 census: 2,898 rows, 0 differ, $0.00.

**Lost.** The 60-committee, 30-row, 60-filing and 30 + 30 recon-row member lists. The scripts `frame_c04.sql` and
`d2_bound_v2.py`. The SQL for DI-040 and DI-041. The amended check texts. The builder's M1 message, which was
truncated in transit, was completed in the M2 report, which is also lost.

---

## 2. Batch 4: home, candidate directory, candidate-committee finance

**Classes.** S-01 (`/`), S-03 (`/candidates`), C-06 (`/candidate-finance/` and its two itemized pages).

**Builder and time.** `opus-builder-4`, dispatched 01:16Z. M1 took 9.8 min of 40 and M2 8.2 min of 35, for 20.3 of 75
min in total. The test client ran with `ROUTE_PERF_CACHE_ENABLED=False` and no `REDIS_URL`. `/` took 16.3 s in the
cycle view and 14.4 s all time; `/candidates` took 2.1 s. Commit `6c7feb1` added a 424-line report and changed the
checklist by +215 / −50 lines. Fable accepted M1 at 01:30Z and the batch at 01:40Z.

**Decisions.**

- **E21.** Each card gets one independent source-table query, written from the builder's population definition and
  recorded in the check. The page's matviews are the diagnosis step only.
- **E22.** The picker is the standard for the D-2 total cards, as in E18. Those cards fail DI-029 and DI-032. The
  classification is a defect in the card builder with a source-data root cause. This created DI-042.

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-030 Home Top Donors | pass | 10 / 10 in both views. The all-time number one now includes its $65,000,000 no-live-D-2 row, which confirms fix F1 locally. Earlier: fail on 2026-10-29 (I2, all time 2 / 5). |
| DI-029 Home stat cards | fail | 17 / 22. The five failures are listed after this table. |
| DI-032 Candidate directory cards | fail | 5 / 7. Total Receipts and Total Expenditures carry the D-2 over-count. |
| DI-012 Candidate-committee finance row | fail | Frame: 5,252 cycle-2026 groups; the list header says 5,254 because 2 pairs have only archived D-2s. 20 / 30 rows to the cent and filing counts 30 / 30. The 10 top rows differ by up to $0.40, from the `REAL` (float4) cast of `sum_total_receipts` and the other sums. Census: 659 of 5,252 rows differ by more than 1¢, max $0.62. Itemized pages 30 / 30 for receipts and for expenditures: rows, CSV count and sum all equal picked receipts or expenditures, every part. |

The five DI-029 failures (cycle view unless stated):

- **Matched Dark Money Expenditures.** The join fans out over 11,189 match rows on 3,087 keys. Cycle: the card shows
  $12,860,903 / 1,484 against $700,843 / 122 distinct. All time: $536,688,948 / 128,019 against
  $32,515,961 / 9,955.
- **Federal Independent Expenditures (Schedule E).** The card windows by the backfill `cycle`. It shows 5,097 /
  $109,249,477.82; windowing by date gives 75 / $10,453,445.89. The all-time view passes.
- **Director-Donor Overlaps.** 13,186 match rows against 10,212 distinct triples, from 2,974 duplicates in 1,762
  groups.
- **Total 527 Contributions Received.** The cycle view fails on parser bug A11: 0 of 8,974,461 rows hold a valid date.
  The all-time view passes.
- **Local Receipts (D-2).** The card sums every live D-2: $380,446,672.19. One D-2 per period gives $380,253,064.35.

**Logged passes and known semantics (DI-029 and DI-032).**

- Donor-Dependent Committees, 527 directors-as-candidates, lobbying clients, and Local Candidate Rows 112,013.
- Local Candidates 31,826 and Committees 24,895. Both count candidate-linked entities only, so the labels are loose.
- Federal Candidates 125. Federal Contributions count 89,789. Its total is the cycle-totals sum $110,695,468.03, not
  Schedule A's $119,657,997.47, which is a known semantic. 415 transactions sit on 2 or more file numbers (DI-016).
- Schedule B 13,477 / $23,736,504.86. Matched Donor Pairs 1,788.
- OpenBook 555 / 8,930 / $30,118,894,097.55.
- Archived Local Filings 132,807. It counts archived D-2 reports, not documents, so the label is loose.
- 527 orgs 1,896. Total 527 Expenditures; 4,901 possible amended duplicates went to DI-021.
- Lobbying Entities 1,822. Legacy 344 / 650 / 561.
- The directory's Candidates, Committees, Federal Candidates, Contributions and Matched Donors cards pass.

**DI-012 year rule.** The matview's year is the year of the latest receipt on the filing, not the D-2 period end. The
cycle is `election_cycle = year + year % 2`. Fable corrected the inventory's §C-06 wording to match. Known semantic:
13,026 live D-2s with no receipt row get year NULL and appear in no cycle row. They hold $1,357,700.86 of receipts
and $12,111,952.69 of expenditures. Changing the rule (F-B4f) needs Devin's call. Slate committees repeat on each
candidate's row.

**New check.** **DI-042, fail.** Rule: one live D-2 per committee and period. Measured: 40 periods, 38 committees and
42 extra D-2s, adding $193,607.84 of receipts and $167,336.96 of expenditures. The originals were left live beside
their amendments. The directory card shows expenditures of $319,216,424.15 against $319,049,087.19 under the rule.

**Fixes proposed.** F-B4a to F-B4g (see §7). The batch also routed A11 to P-1 (`docs/runbooks/irs527_data.md`).

**Dictionary facts logged.**

- The director-donor match table has 2,974 duplicate triples.
- The recipient-match table is not unique on (ein, recipient_name), so join it through EXISTS.
- The finance agg's sums are REAL, and its year is the latest receipt's year.
- One receipt on filing 420112 (committee 1850) is filed under committee 4689.

**Fable's re-verification (01:40Z).**

- The agg column type is `real`. The 39266 × 32762 / 2025 row shows 2.5552252e+07 against the exact $25,552,251.95,
  with $5,691,207.51 and 3 filings.
- Home page under the test client with the cache off (7.5 s): Director-Donor Overlaps 13186, Local Committees 24895,
  Archived Local Filings 132807 (equal to archived `isbe_d2_reports`), Schedule E 5,097.
- At M1, Fable re-ran the four decisive queries with identical results, grouped as 40 periods and 42 extra D-2s.

**Lost.** The per-card population table (`M1_table.md`) and every card's query; the E21 queries were to be written into
DI-029 and DI-032 and are gone. The 30 C-06 rows by id. The SQL for DI-042.

---

## 3. Batch 5: state races and analytics dashboards

**Classes.** C-07 (`/analytics/state-races/` and `/<race_key>`) and C-08 (the eight `/analytics/*` dashboards).

**Builder and time.** `opus-builder-5`, dispatched 01:40Z. M1 took 11.0 min of 45 and M2 17.2 min of 45, for 31.5 of
90 min in total. The builder used a cache-off test client, a 100 s statement_timeout on probes, and CTE-only SQL,
because read-only mode rejects temp tables. The window bar read cycle:2026 as 2025-01-01..2026-09-30, the end capped
at the data date. Commit `df1e85d` added a 686-line report and changed the checklist by +338 / −63 lines. Fable
accepted M1 at 01:55Z and the batch at 02:14Z.

**Decisions.**

- **E23.** DI-013 recomputes under the builder's documented rule, over picked source-table figures, and records the
  rule per figure. The rule: once per race, a 1/n split, and a cycle gate.
- **E24.** Each uncovered C-08 page gets one headline check, which produced DI-043 to DI-049.

**DI-013 rule table (as logged).**

- Candidates come from `isbe_race_candidacies` at `election_cycle = 2026`.
- Donors are distinct site keys, with `donor_entity_local_member` merges.
- Contributions and the race total are picked receipts, every part, summed over distinct race × committee pairs.
- Candidate Committee Rows are the D-2 `sum_total_receipts` from the finance agg, split 1/n, under the agg's
  latest-receipt year rule.
- The top-candidate share uses the one-D-2-per-period set.
- Part 9 follows DI-005's rule and was not re-derived.

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-013 State race totals | pass | Race-list census: all 1,465 rows match (1,024 linked and 441 unlinked by label key). Total Amount is $324,105,554.18 on both sides. 810 redacted receipt rows in 256 races keep their amounts. 30 race pages: 180 / 180 cards and 60 / 60 committee rows. 79 candidate rows: 54 exact, 25 differ by DI-012's REAL cents, up to $0.22. The 30-race sample had 8 statewide races, the 12 largest House / Senate races and 10 small ones; all pages returned 200 (governor 1.5 s). Two statewide keys carry a city in the district field, a source spelling variant: `comptroller-statewide-benton` and `secretary-of-state-statewide-chicago`. |
| DI-014 Monthly time series | pass | 53 / 53 months under the contributions predicate: 21 default-window months, all 24 of cycle:2024, and the overview's last 8. Every month also equals `analytics_committee_monthly_totals`. |
| DI-015 Geography states and cities | pass | 20 / 20 states (ranks 1–10 and 23–32 of 55) and 20 / 20 cities (ranks 1–10 and 246–255 of 500), in the same order. The Donors column uses the page's looser identity: IL 74,503 against 74,645 site keys. |

**New checks (E24).**

| Id | Page | Verdict | Result as logged |
|---|---|---|---|
| DI-043 | `/analytics/overview` cards | pass | Trend Months 21; nodes / edges 547 / 400. |
| DI-044 | `/analytics/donors` concentration | pass | 25 / 25. Every shown row is a single-donor committee, and 361 committees sit at HHI 10,000 (F-B5d). |
| DI-045 | `/analytics/geo-drilldown` | pass | IL / Chicago: header 4 / 4, top-10 rows 10 / 10 each. |
| DI-046 | `/analytics/networks` | fail | 199 / 200 committee→candidate edges are off. The builder sums the REAL agg column in single precision. That half of the graph is all-time D-2 money under a "2026 Cycle" tag (F-B5a). |
| DI-047 | `/analytics/relationships` | proposed | Written with its query, not run. The query is lost. |
| DI-048 | `/analytics/risk` flags | pass | 25 / 25; ranks 15 and 16 are a severity tie. Only 22 of the window's 7,491 large rows can become flags today (F-B5c). |
| DI-049 | 527 ecosystem graph | fail | Empty in every window; 229 nodes all time. The parser expects YYYY-MM-DD and the stored dates are YYYYMMDD (F-B5b). The default window should hold 1,136 rows from 13 orgs. The log does not name the page that hosts the graph. Fable re-ran `/api/analytics/irs527-ecosystem`. |

**Known semantics logged.**

- The year rule excludes 4,803 D-2s / $1,090,342.77 on race committees (F-B4f).
- 226 committees in 638 race rows are counted fully in each race.
- Candidate Committee Rows show the cycle D-2 total under a windowed tag; the registry entry is `mismatch`.

**Fixes proposed.** F-B5a to F-B5e (see §7).

**Fable's re-verification.**

- At M1 (01:55Z): the race-list total over distinct (office, district_type, district, committee) pairs with picked
  receipts to 2026-09-30 was $324,105,554.18 / 1,023 races with money, identical. DI-014 months: 2026-01
  $9,174,021.89 / 8,936 rows and 2025-06 $9,235,099.74 / 7,120 rows, equal to the page's embedded series.
- At batch acceptance (02:14Z): `irs527_expenditures.date` is YYYYMMDD in 35,235 of 35,914 rows, 0 dashed.
  `/api/analytics/irs527-ecosystem` returns 0 nodes by default and 229 with `?period=all`.
- Network edge committee:32762 → candidate:39266: weight 378,380,000.00, equal to the SUM over the REAL column. The
  exact NUMERIC is 378,380,219.05. That $219.05 gap on the top all-time edge exceeds the report's "≤ $30.64" bound.
  Fable recorded that the report's magnitude line was understated for the top edge; the defect and fix stand.

**Lost.** The 30 race keys and 79 candidate rows, the month and state / city tables, the DI-043 to DI-049 queries
(DI-047's included) and the rewritten DI-013 / 014 / 015 texts.

---

## 4. Batch 6: federal dashboards, profiles and race outside spending

**Classes.** C-09 (the eleven `/federal-finance/*` dashboards) and C-10 (federal candidate and committee profiles,
race outside spending).

**Builder and time.** `opus-builder-6`, dispatched 02:14Z. M1 took 24 min of 45 and M2 7 min of 45, for 34 of 90 min
in total. Route cache and `FEDERAL_VIEW_CACHE_ENABLED` were off. Commit `be54619` added a 369-line report and changed
228 checklist lines. Fable accepted M1 at 02:41Z and the batch at 02:51Z.

**Deviation recorded at M1.** Fable's brief was wrong about local freshness; the 2026-10-29 prod sync was not local.
Local FEC data:

| Schedule | Rows | Dates | Last upsert |
|---|---|---|---|
| A | 89,789 | 2024-11-03..2026-07-01 | 2026-09-30 |
| B | 13,477 | to 2025-12-31 | 2026-02-13 |
| E | 5,097 | to 2026-02-10 | 2026-02-13 |

Only cycle 2026 exists locally.

**Decisions.**

- **E25.** Amendment, duplicate and memo semantics are measured before any comparison.
- **E26.** Schedule E is windowed by date (the MT-015 rule), never by the stored `cycle`.
- **E32.** Schedule A and B figures are judged under the FEC rule:
  - exclude `memo_code = 'X'`;
  - keep one row per (committee, transaction_id, report) on the highest `file_number`;
  - for Schedule E, use `most_recent` plus the date window.

  Pages that count memo or superseded rows fail DI-016 and DI-017 as defects. The root cause is that the loader
  stores no `memo_code` and keeps every upsert forever.

**E25 measurements.**

- The loader keys on `sub_id`, never deletes, and stores `memo_text` but not `memo_code`.
- Raw API payloads in `raw_extractions` join to every row. They recovered `memo_code`, `transaction_id`,
  `file_number`, `report_type/year` and Schedule E `most_recent`.
- 54,889 Schedule A rows have no stored `transaction_id`, and every Schedule E `most_recent` is null. Both predate
  commit `1016eb1` (2026-02-17).

Schedule A under the FEC rule:

| Part of Schedule A (cycle 2026, local) | Rows | Dollars |
|---|---|---|
| All rows | 89,789 | $119,657,997.47 |
| Memo X | 24,768 | $40,855,838.90 |
| … ActBlue earmark notes | 20,432 | $34.61M |
| … WinRed | 515 | $0.86M |
| … individuals behind joint-fundraising transfers | 3,175 | $3.78M |
| … others | 646 | $1.61M |
| Superseded same-report rows on a lower `file_number` | 397 | $1,054,362.57 |
| FEC rule (kept) | 64,649 | $77,770,896.00 |

290 of the 397 superseded rows are no longer returned by the API in their own payload. The kept total plus DI-052's
memo-and-superseded total (25,140 rows / $41,887,101.47) equals the all-rows line exactly. The memo and superseded
lines alone sum to 25,165 rows / $41,910,201.47. The 25-row / $23,100.00 difference is presumably rows in both
categories, but the log does not say.

Validation: raw sums exceed the FEC's reported receipts for 19 of 102 candidates. FEC-rule sums exceed it for none.
Junaid Ahmed: $14.05M raw, $1.65M reported, $0.93M under the rule. Schedule B has 2,839 memo rows / $2,551,133.80 and
no superseded rows.

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-016 Federal cycle totals | fail | Every Schedule A and B money figure on the dashboards counts memo and superseded rows. The arithmetic itself reproduces. Three page defects: the overview Schedule E card windows by the stored `cycle` (F-B6e); the Races card is capped at 12 (F-B6f); the Influence page shows $0.00 / 0 on every row (DI-051). |
| DI-017 Federal candidate and committee totals | fail | Under the rule, candidates 2 / 10 and committees 2 / 10; raw, 10 / 10. Worst: FRIENDS OF JUNAID (C00906891) shows $14,054,760.00 itemized against $927,180.00 under the rule and $1,650,112.48 reported. |
| DI-018 Federal race outside spending | fail | 9 / 10. The Senate race shows $6,099,849.42 / 10 rows against $4,733,531.42 / 8; two superseded 24-hour reports add $1,366,318.00. Only 9 races have Schedule E in the window, plus IL-13 at zero. Every race page applies the MT-015 date window. The query was rewritten to scope by the race's candidate ids, as the page does. The office / district columns disagree by one $5,000 row in two races. |

**New checks.**

| Id | Verdict | Result as logged |
|---|---|---|
| DI-050 matching cards | proposed | The arithmetic passes; the matched count was not recomputed. |
| DI-051 Influence page | fail | Weighted Degree $0.00 and Degree 0 on all 50 rows. The page reads degrees from graph nodes, but the builder stores them on centrality rows (F-B6g). |
| DI-052 memo and superseded detector | fail | 25,140 Schedule A rows / $41,887,101.47 counted, which is 35.0 % of the cycle's dollars. |

**Fixes proposed.** F-B6a to F-B6i (see §7). Censuses were used where the frames were small.

**Fable's re-verification (02:51Z).**

- C00906891: 817 raw rows / $14,054,760.00. Under the FEC rule from the stored payloads, 590 / $927,180.00. Identical.
  Reported: $1,650,112.48.
- `/federal-finance/races/S/00/outside-spending`: $6,099,849.42 / 10.
- `/federal-finance/influence`: 50 rows × $0.00.
- At M1: Schedule A totals, dates and upsert time identical; no `memo_code` column; 19 of 100 cycle-2026 candidates
  with raw above reported; Schedule B and E upsert times 2026-02-13.

**Lost.** The semantics table (figure → page filter → FEC rule → query), the 10 + 10 + 10 member lists, the payload
recovery SQL, and the rewritten DI-016 / 017 / 018 texts. The builder's M1 Schedule E details were truncated in
transit and were only in the lost report.

---

## 5. Batch 7: lobbying and IRS 527

**Classes.** C-11 (`/lobbying/`, entity and client pages, `/lobbying/flows`) and C-12 (`/527/`, org pages,
contributors, recipients, `/527/dark-money`).

**Builder and time.** `opus-builder-7`, dispatched 02:51Z. M1 took 15 min of 45 and M2 12 min of 45, for 28.5 of 90
min in total. Route cache was off. Local 527 data ends 2025-12-31; the newest `insert_datetime` is 2026-02-01. The
lobbying extract is 20260214, with `reg_year` 2022..2026. Commit `e32d4df` added a 542-line report and changed the
checklist by +273 / −70 lines. Fable accepted M1 at 03:09Z and the batch at 03:22Z.

**Decisions.**

- **E27.** Duplicates, unparseable dates and match fan-outs are measured first. Each source row counts once, and
  matches join through EXISTS.
- **E28.** Lobbying money is recomputed over picked receipts by the matched donor keys.
- **E33 (batch 7, 2026-10-30 03:09Z).** The amendment rule is the standard for 527: one live Form 8872 per
  (ein, period), chosen by the latest `insert_datetime` NULLS LAST, then the highest `form_id`. Pages that sum
  superseded forms fail as defects. The id E33 was later reused by §RESUME 2026-11-08 for an unrelated decision (the
  worktree location), so cite this one with its batch and time.

**E27 measurements.**

- No re-run doubling locally (loader D3): one load window per table. Expenditures loaded 2026-02-13 19:02–19:03 and
  contributions 2026-02-15 00:32–00:35.
- **Superseded 8872s.** `irs527_reports` holds 1,739 forms on 1,598 distinct (ein, period_start, period_end), so 141
  forms over 41 EINs are superseded. The loader drops the IRS amended indicator. The superseded forms carry
  Schedule A $54,995,372 and Schedule B $51,041,990. 4,104 of 4,200 expenditure rows and 10,786 of 11,096
  contribution rows reappear on the kept form.
- 2,998,806 contribution rows are identical within one form. They cannot be measured until P-1 loads the real date.
- **Dates.** A11: 0 contribution dates parse; the cycle text clause lets 318 rows / $224,180 through. Expenditure
  dates: 35,235 compact, 679 blank, 0 ISO; 20 amounts are non-positive.
- **Scope.** 567 of 656 contribution EINs have no `irs527_organizations` row. They hold 8,788,640 rows /
  $10,619,402,839, which is 97 % of contribution money. The cause is the `--illinois-only` include_contributions
  rule.
- **Match fan-out root cause.** `match_527_expenditures_to_committees` (`database/cross_matching.py` ≈ 1075) builds
  one left item per expenditure row. Copies therefore equal the IL expenditure rows per (ein, recipient_name), for all
  3,512 targets; one target has 140 copies. `created_at` is NULL on every row (RF-016).

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-019 Lobbying clients per entity and year | fail | Entity "Clients (N)" and client "Represented By (N)" count (member, reg_year) rows of `lobbying_entity_clients`, not members. 6 / 15 entities and 6 / 15 clients pass. Site-wide, 481 of 654 entities and 2,496 of 3,312 clients are over-counted. Taft (entity 7036) shows 321 for 105 clients, while the list shows 105. The list's Clients column passes a census, 2,231 / 2,231. The list header "N lobbying entities registered" counts `lobbying_entities.reg_year`, a loader first-seen year: 409 in cycle:2026, against 477 entities with client registrations and 1,346 with lobbyist registrations in 2025–26. All time, 1,822 passes. The entity "Matched donors" badge fans out on 285 of 356 entities; it joins matches to entity_clients without the year. |
| DI-020 Lobbying matched donors money | fail | 15 matched clients. Top Committees from the agg equal picked contributions by the matched keys, 70 / 70. "Matched donor dollars" and "Contributions" pass 2 / 15, and "This cycle" 8 / 15. `_get_receipt_activity` selects condensed receipts by donor NAME across all parts. Its key test compares a SHA-1 `_stable_entity_key` with the bulk donor key and can never pass. The name path reproduces the page 15 / 15 and reaches 1–11× the matched keys (median 4×). Ameren (client 3821): $7,515,121.12 against $4,548,836.60. Flows follow the keys: all time 304 / 304 against contributions; cycle 108 / 108 against all parts but 78 / 108 against contributions, a basis mismatch. Flows group by client_name, and 8 names span more than one client_id. |
| DI-021 527 org totals | fail | 30 orgs all time, from a frame of 89 EINs with a page and contributions. Raw: 30 / 30 on every figure. Under the amendment rule, 13 / 30 fail and 17 pass. The overstatement is $53,406,077 of contributions and $46,299,356 of expenditures; EIN 261725187 alone is +$32.9M / +$24.8M. "This cycle" is A11. Minor: "Top Expenditure Recipients (N records, $X)" counts rows with amount ≤ 0 but sums only those > 0, on 3 orgs. |
| DI-022 527 dark-money totals | fail | All-time funding sources reproduce ($10,938,950,394 / 8,973,118 / 1,052,915), but they include the superseded forms' $55.0M and the 567 page-less EINs. The cycle view is A11. "(N matches)" is a defect: 954 rendered in cycle:2026 against 113 distinct targets (109 distinct rows over 20 pages), and 11,189 against 3,512 all time. The page has no money sum, so rows repeat but dollars do not inflate; the home card still needs EXISTS. `/527/` list census: 1,896 orgs in both views. Cycle expenditures $13,783,754 equal batch 4's card. All-time report totals $317,870,040 / $278,641,598 include the superseded forms. The list and detail basis differ for 3–4 of 121 EINs. |

**New checks.**

| Id | Verdict | Result as logged |
|---|---|---|
| DI-053 one live 8872 per EIN × period | fail | 141 superseded forms over 41 EINs. |
| DI-054 one match row per target | fail | +7,677 (11,189 rows against 3,512 targets). |
| DI-055 funding-source scope / label | fail | A label defect. The "527 Funding Sources" card, labelled "Illinois-connected 527 organizations", sums 567 page-less EINs (8,788,640 rows / $10,619,402,839). The scope decision is Devin's (F-B7c). |

**Fixes proposed.** F-B7a to F-B7h (see §7).

**Self-review correction logged.** The home "Total 527 Expenditures" cycle card ($13,783,754) includes 30 superseded
rows / $48,501 (EIN 363954686). Row 18 all time is 97 % DI-055 scope.

**Dictionary facts logged.**

- 13,149 of 14,407 contribution forms have no report row.
- 310 expenditure rows are identical.
- `lobbying_donor_matches` has 3,100 rows / 560 clients / 3,046 keys at ≥ 0.80. `created_at` is NULL, and the keys
  come from the top 50,000 of `analytics_donor_summary`.
- `lobbying_entities.reg_year` is the first-seen year.
- `lobbying_entity_clients` has 15,116 rows on 5,345 pairs.

**Privacy note logged.** 41 hits are committees or organizations named after candidates, plus a law-office firm,
which is within the convention. Entities that are individual lobbyists appear by id only, and contributors as md5
prefixes.

**Fable's re-verification.**

- At batch acceptance (03:22Z): entity 7036 shows "Clients (321)" against 321 rows / 105 distinct clients in
  `lobbying_entity_clients`. Client 3821 shows "Matched donor dollars: $7,515,121.12" against $4,548,836.60 in
  `analytics_donor_committee_agg` by its 43 matched keys.
- At M1 (03:09Z): 1,739 / 1,598 / 141 / 41; 11,189 / 3,512; 567 of 656 EINs with 8,788,640 rows /
  $10,619,402,839; the dark-money page's "(954 matches)" and the card label. All identical.

**Lost.** The key-path table for lobbying money, the 15 + 15 entity and client ids and the 30 EINs with their
verdicts, the measurement SQL, and the rewritten DI-019 to 022 texts.

---

## 6. Batch 8: OpenBook, cross-source tools, compare

**Classes.** C-13 (`/openbook/`, `/openbook/<vendor_key>`), C-14 (`/search`, `/person-intelligence`, `/investigate`,
`/live-feed`) and S-04 (`/compare`).

**Builder and time.** `opus-builder-8`, dispatched 03:22Z. M1 took 7.5 min of 35 and M2 11.4 min of 40, for 20.2 of 75
min in total. The controller closed the batch at 21.4 min of 75, with 97 calls, 0 forbidden and 0 failures. Commit
`0618dc3` added a 486-line report and changed the checklist by +229 / −51 lines. Fable accepted M1 at 03:31Z and the
batch at 03:44Z.

**Decision.** **E29.** Restated figures are judged by identity of definition against the profile page already
verified, plus a direct source recomputation for OpenBook only.

**Checks.**

| Check | Verdict | Result as logged |
|---|---|---|
| DI-023 OpenBook vendor figures | fail | 555 listed vendors. List rows pass 555 / 555 in both windows; the seed fan-out does not double-count. Detail pages pass 62 / 68. Four findings, D1 to D4, follow this table. |
| DI-033 Compare stat cards | pass | 20 / 20 across 10 pairs from the batch 2 and 3 samples, including ids 299, 245 and 313. Every card equals its same-definition profile figure and the picked-receipts SQL. Pritzker (39266): $442,361,141.41 / 437 / 323. Democratic Party of Illinois (6239): $26,326,584.42 / 2,820. Known semantics, label fixes in F-B8e: compare is all time with no scope bar; the committee "Contributions" card counts part-1 rows (5,915) while the profile counts all parts (11,409); "Donor Counterparties" is Unique Donors; option order follows the REAL agg (DI-012, ordering only). |
| DI-024 Tool figures equal profile figures | fail | 29 results. Donor rows 15 / 15: search Total equals the donor page equals the DI-035 query, and I2 was not observed locally. Candidate rows 0 / 14: the row sums `bulk_candidate_committee_finance_agg.sum_total_receipts` (sworn D-2), re-cast and summed in REAL, under an "All time" tag, while the link carries `?period=cycle:2026`. Candidate 39266 shows $378,609,220.00 against $452,454,964.70 itemized on the profile, and −$199.05 against the numeric agg sum. Committee counts 14 / 14. Person-intelligence and investigate show no figure next to a link, so they have no members. Investigate's top state donors equal search's. Federal contributor rows sum raw Schedule A by name (DI-052 family). |
| DI-025 Live feed latest dates | pass | Latest dates: local 2026-09-30, Schedule A 2026-07-01, B 2025-12-31, E 2026-02-10. Coverage 2026-08-27. First 10 rows per table: 40 / 40. Known semantic F-B8g: the local feed lists one row per linked candidate. 4,765 committees are linked to more than one candidate, so 90,594 picked contributions become 177,768 rows; 14 of the 75 shown repeat. |

DI-023's four findings:

- **D1, defect.** `vendor_detail` sums the LIMIT-250 display rows, so 6 vendors show 250 contracts. COOK COUNTY shows
  250 / $10,628,707,662.50 against 2,834 / $26,307,070,682.03. AMEREN ILLINOIS shows 250 / $52.45M against 1,012 /
  $110.91M. The others are AMEREN ILLINOIS CO / COMPANY, ABBOTT and CROWE. Fix F-B8a.
- **D2, defect.** `issue_date` and `contribution_date` are M/D/YY text compared with ISO strings, so every windowed
  warrant figure is 0. Four vendors show 0 against 722 rows in cycle:2026 and 762 in cycle:2024. The list's `?period=`
  link lands readers on the zeros. Fix F-B8b.
- **D3, source data.** All 58 `openbook_contributions_raw` rows are scraped header rows ('DATE', NULL amount), which
  the current parser rejects. The result is "Contributions 1, $0.00" on 44 vendors. Fix F-B8c.
- **D4, presentation.**
  - `ORDER BY total_award_amount DESC` puts NULLs first: page 1 shows only $0 vendors, and the first contract-bearing
    row is at 497 of 555.
  - The list header counts 14 unlisted keys: 8,930 / $30,118,894,097.55 against 8,781 / $30,079,611,140.09 listed.
  - The Contracts card has no scope tag.

  Fix F-B8d.

**New checks.**

| Id | Verdict | Result as logged |
|---|---|---|
| DI-056 "detail cards cover all rows" | fail | D1: 6 vendors capped at 250 display rows. |
| DI-057 "date columns parse and window" | fail | D2: windowed warrant and contribution figures are 0 because of M/D/YY text compared as ISO. |

These two titles are quoted from Fable's M1 acceptance line. They are the only new-check titles that survive verbatim.

**Fixes proposed.** F-B8a to F-B8g (see §7).

**Findings for the records pass.** The inventory had drifted for the C-13 contract window, the S-04 "default current
cycle", and the C-14 live feed's join to candidate links. Fable corrected all three in `docs/page_inventory.md`. Two
further notes were logged. `/investigate` prints full donor keys in its Key column; this is not new and was noted for
the privacy policy. Two OpenBook vendor keys are individuals' names from payee seeds; they are not named here.

**Fable's re-verification.**

- At batch acceptance (03:44Z): agg 39266 is $378,609,419.05 numeric against $378,609,000.00 REAL-summed. The search
  result links to `/candidates/39266?period=cycle:2026`. Live-feed dates are identical.
- At M1 (03:31Z): COOK COUNTY 2,834 / $26,307,070,682.03 against the page's 250 / $10,628,707,662.50. Warrants with
  `?period=cycle:2026` show 0 / $0.00 against 686 rows by `to_date` for the vendor re-run; the log line follows the
  COOK COUNTY figure. contributions_raw has 58 rows, all 58 'DATE', all 58 NULL. Identical.

**Lost.** The definition table (tool figure → tool query → profile query → same definition?), the 30-vendor and
29-result member lists (search terms were recorded as ids or md5 prefixes), the 10 compare pairs, and the rewritten
DI-023 / 024 / 025 / 033 texts.

---

## 7. Fix queue (none implemented)

Every F-B id named in the plan logs or the status entry is listed. Descriptions are condensed from the log lines.
"Footprint" gives only what a log names; "not logged" means the lost report held it. For F-B6a–d and F-B7a–h the
logs give a list of fixes under a letter range. The letters below follow that list's order, a mapping the logs imply
but do not state one by one.

| Id | Batch | Finding / DI | Fix (one line) | Footprint (as logged) | Devin decision? |
|---|---|---|---|---|---|
| F-B3a | 3 | DI-040 | `filing_detail` reads raw rows by `filed_doc_id`, plus a superseded / not-counted note | `filing_detail` (`webapp/routes/committees.py`) | no |
| F-B3b | 3 | DI-041, RF-006 | Rebuild the D-2 recon tables from `isbe_*` inside the refresh chain | refresh chain; RF-006 names `scripts/swap_bulk_to_isbe.py` + its guard tests | no |
| F-B3c | 3 | DI-041 (prerequisite of F-B3b) | Load the D-2 Parts 6 / 7 itemized columns (`XferOutI` / `LoanMadeI`) into `isbe_d2_reports`; same family as the `transfer_in` bug | ISBE ETL (`isbe_d2_reports` load); file not logged | no |
| F-B3d | 3 | latent, DI-009 / DI-031 | Add the missing `amount > 0` to the Expenditures Summary and the directory CTE; changes no local figure | committee Expenditures Summary query + directory CTE; file not logged | no |
| F-B4a | 4 | DI-029 Matched Dark Money card | Join `irs527_expenditure_recipient_matches` through EXISTS, not a fan-out JOIN | `_build_dashboard_insights` (`webapp/routes/main.py`) | no |
| F-B4b | 4 | DI-029 Schedule E card | Window Schedule E by `expenditure_date`, not the backfill `cycle` | home card builder; file not logged | no |
| F-B4c | 4 | DI-029 Director-Donor Overlaps | COUNT(DISTINCT) and / or dedupe the matcher's 2,974 duplicate triples | card builder and / or the director-donor matcher | no |
| F-B4d | 4 | DI-029 / DI-032 / DI-042 | Sum the kept D-2 per (committee, period) in the condensed picker's order | `_get_candidate_stats` (`webapp/routes/main.py`) | no |
| F-B4e | 4 | DI-012 | Cast the finance agg's sums to DOUBLE PRECISION / NUMERIC | `CANDIDATE_FINANCE_AGG_VIEW_SQL` (`scripts/swap_bulk_to_isbe.py`) | no |
| F-B4f | 4 | DI-012 (13,026 live D-2s with year NULL) | Take the agg's year from `reporting_period_end`, not the latest receipt | the agg matview's year rule | **yes**: the finance-agg year rule |
| F-B4g | 4 | — | **Description not recoverable.** The logs name the id only inside "F-B4a..g" / "F-B4a–g". | not logged | unknown |
| F-B5a | 5 | DI-046 | Cast before summing, and restrict the D-2 half of the graph to the window's cycles or tag it All time | networks graph builder (`database/analytics.py`); line not logged | no |
| F-B5b | 5 | DI-049 | Parse the YYYYMMDD `irs527_expenditures.date` in the 527 ecosystem graph | 527 ecosystem builder; file not logged | no |
| F-B5c | 5 | DI-048 | Window `analytics_large_contributions` before the 200-row cap | risk-flag builder; file not logged | no |
| F-B5d | 5 | DI-044 | Floor / help line for single-donor committees at HHI 10,000 | donor concentration page; file not logged | no |
| F-B5e | 5 | DI-045 | Print the capped window end in the geo drill-down's date line | geo drill-down; file not logged | no |
| F-B6a | 6 | DI-016 / 017 / 052 | Store `memo_code` and backfill it from `raw_extractions` | `database/federal_fec.py` loader | no |
| F-B6b | 6 | DI-016 / 017 / 018 / 052 | Mark superseded rows and backfill the pre-`1016eb1` columns | FEC loader | no |
| F-B6c | 6 | DI-016 / 017 / 018 | One shared filter (no memo X, no superseded rows) at every named federal query site | every federal query site (list in the lost report) | no |
| F-B6d | 6 | DI-016 / 017 | Interim label on the federal money figures until F-B6a–c land | not logged | no |
| F-B6e | 6 | DI-016 overview Schedule E card | Window by date, not stored `cycle` (F-B4b family) | `_federal_schedule_b_e_metrics` | no |
| F-B6f | 6 | DI-016 Races card | Count the 18 active races, not the 12-row table | federal overview Races card | no |
| F-B6g | 6 | DI-051 | Read degrees where the builder stores them (centrality rows), not graph nodes | `/federal-finance/influence` | no |
| F-B6h | 6 | network totals | Remove H6IL02330's 4 seed rows from `fec_candidate_match` (+$91,200.00 in network totals) | `fec_candidate_match` | no |
| F-B6i | 6 | network edges | Add a tiebreak at the $7,000 edge cut | federal network builder | no |
| F-B7a | 7 | DI-021 / 022 / 053 | Loader keeps the IRS amended indicator; views pick the latest 8872 per (ein, period) | `database/irs527_loader.py` + 527 views / queries | no |
| F-B7b | 7 | DI-022 / 054, F-B4a | Matcher builds DISTINCT recipients; page counts distinct targets | `match_527_expenditures_to_committees` (`database/cross_matching.py` ≈ 1075) + `/527/dark-money` | no |
| F-B7c | 7 | DI-055 | Funding-source scope / label: restrict the card, or load pages for every contributing EIN | dark-money card; 527 loader scope | **yes**: 527 funding-source scope |
| F-B7d | 7 | DI-021 / 022 | One records / total basis, list against detail and count against sum | `/527/` list and org pages | no |
| F-B7e | 7 | DI-019 | Distinct member counts on entity and client pages; de-fan the "Matched donors" badge | lobbying entity and client pages | no |
| F-B7f | 7 | DI-019 | List header counts entities by registrations in the window's years, not `lobbying_entities.reg_year` | `/lobbying/` list | no |
| F-B7g | 7 | DI-020 | Money Destinations by matched key, contributions parts | `_get_receipt_activity` | no |
| F-B7h | 7 | DI-020 | Flows on one basis, grouped by client_id | `/lobbying/flows` | no |
| F-B8a | 8 | DI-023 / 056 | SQL COUNT / SUM over all rows, not the LIMIT-250 display rows | `vendor_detail` (`webapp/routes/openbook.py`) | no |
| F-B8b | 8 | DI-023 / 057 | `to_date` the M/D/YY text dates, or normalize at load | OpenBook routes or scraper load | no |
| F-B8c | 8 | DI-023 (source data) | Rescrape the 58 header rows in `openbook_contributions_raw` | OpenBook contribution scrape | no |
| F-B8d | 8 | DI-023 (presentation) | NULLS LAST ordering, list header counts listed vendors, scope tag on the Contracts card | `/openbook/` list and detail | no |
| F-B8e | 8 | DI-033 (known semantics) | Labels on compare: all time, part-1 "Contributions", "Donor Counterparties" | `/compare` template | no |
| F-B8f | 8 | DI-024 | Search candidate rows: label, numeric sum, link with `period=all` | search route | no |
| F-B8g | 8 | DI-025 (known semantic) | Live feed lists a receipt once, not once per linked candidate | `live_feed` (`webapp/routes/main.py`) | no |

Also routed, not an F-B id: A11 (527 contribution date) → P-1 in `docs/runbooks/irs527_data.md`. Since the batches,
`main` has commit `fa3158e`, "527 loader: read the contribution date from field 16…", which targets A11. Whether
local or prod data was reloaded with it was not checked here.

## 8. Decisions for Devin (carried from the logs)

1. **F-B4f, finance-agg year rule.** The year of `bulk_candidate_committee_finance_agg` is the latest receipt's year.
   13,026 live D-2s with no receipt row get year NULL and fall in no cycle: $1,357,700.86 of receipts and
   $12,111,952.69 of expenditures. On race committees this excludes 4,803 D-2s / $1,090,342.77. The option is to take
   the year from `reporting_period_end` instead.
2. **F-B7c, 527 funding-source scope.** The "527 Funding Sources" card, labelled "Illinois-connected", sums 567
   page-less EINs (97 % of contribution dollars). The options are to restrict the card or to load pages for every
   contributing EIN. Commit `80ebc3b` on `main` ("--illinois-only keeps IL-addressed orgs…; IL-contributor orgs keep
   only their contributions") changes that loader rule after the batches. Whether it settles the question was not
   checked here.
3. **DI-034, admin credentials.** Batch 9's scope. Decision E31 says DI-034 is recorded `blocked`, with manual steps,
   if no local admin credential is available. No credential is guessed and prod is never read.
4. **Prod re-checks of DI-030, DI-041 and DI-052.** All three were measured on local data only. DI-030 passed after
   F1 rebuilt the local analytics tables. DI-041 measured the local legacy recon tables. DI-052 measured local FEC
   data that is older than prod. Each needs a prod run, which needs Devin's explicit ask.

## 9. How to re-run a batch

The six briefs are intact in `.claude/plans/data-integrity-batches.md`, in the sections "## Batch 3" to "## Batch 8".
Each has its interpretation, scope, approach with its decisions, success criteria, verification, time budget and
milestones. A re-run follows `docs/data_integrity_checks.md` §How a batch runs:

- **Fresh builder.** One fresh agent per batch, started with no context from any other batch.
- **Location.** Work in an in-repo worktree under `.claude/worktrees/`, never a `/private/tmp` clone (decision E33 of
  §RESUME 2026-11-08).
- **Checklist edits.** One builder at a time edits the checklist (E36).
- **Report.** It writes the original report name, `docs/audits/data_integrity_batch<N>_<class>_2026-10-30.md` with
  the re-run date in place of 2026-10-30. Its checklist status lines then replace the "reconstructed" ones.

**Expect different figures.** Local data has changed since 2026-10-30:

- ISBE data is current to its last import.
- The local IRS 527 tables were being reloaded on 2026-11-08 (0 organizations at 06:05Z).
- Loader commits `fa3158e` and `80ebc3b` landed after the batches.

A re-run measures the site as it then stands. It does not reproduce the 2026-10-30 numbers.
