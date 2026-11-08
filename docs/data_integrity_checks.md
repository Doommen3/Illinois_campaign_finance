# Data integrity checks

## What this is

The master list of every figure on the site that needs a data-integrity check, and the manual steps to run each
one: recompute the figure independently from the source tables, compare it with what the page shows, and record
pass or fail. It is the sibling of `docs/manual_tests.md`, which covers behaviour (pages load, links work, windows
read correctly); this file covers numbers. The pages are grouped into classes in `docs/page_inventory.md`; every
class and every singleton with figures maps to at least one check below, so nothing the site shows is outside
this list.

**Status legend.** `proposed` (written, not yet run) → `in progress` (a batch is running it) → `pass` / `fail` /
`blocked` (blocked = cannot be run as written, for example a missing table or a query that needs a decision; say
why). A batch flips the **Status** of each check it runs, in its section and in the index, and writes the date:
`pass (2026-11-02)`. A failed check stays `fail` until the fix is deployed and the check passes again; it is never
deleted.

**Progress:** Checks: 60 total — 2 proposed, 0 in progress, 24 pass, 33 fail, 1 blocked (2026-11-08, after batch 9;
batches 3–8 statuses reconstructed from the session logs — reports lost, see
docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md). Each batch updates this line.

Program closed 2026-11-08: all 17 classes and the singletons have run checks; the fix queue is in the batch reports
and the reconstruction document §7; DI-047 and DI-050 remain proposed (written in batches 5–6, not run); DI-034
blocked (admin credential).

## How a batch runs

1. **One fresh agent per batch.** A batch covers the 2–4 classes named in the batch plan and nothing else, so the
   agent's context holds only those classes' routes, tables and checks. Start it with this file, the classes'
   sections in `docs/page_inventory.md` and `.claude/data-dictionary.md`.
2. **Read-only database.** Local `ilcf` only, never prod: `export PGOPTIONS='-c default_transaction_read_only=on'`
   in every shell before `psql ilcf` or any Python that opens the database. Scratch scripts live outside the repo.
3. **Independent recomputation.** Recompute each figure with SQL over the source tables (`isbe_receipts`,
   `isbe_expenditures`, `isbe_d2_reports`, `isbe_filed_docs`, the ISBE link tables, the FEC / lobbying / 527 /
   OpenBook base tables), never with the page's own query or its matview. When a figure disagrees, run the same
   query on the matview the page reads (`isbe_condensed_receipts`, `analytics_donor_*`, ...) to locate the step
   that changes the number.
4. **Stratified sample.** Draw the sample by the rule below, read each figure from the page (Flask test client or a
   local server), and compare. Exclude redacted donors and parser-dirty rows from the sample and say how many were
   excluded; never average them in.
5. **Report.** Write `docs/audits/data_integrity_batch<N>_<class>_<date>.md`: scope, sample (n, strata, how drawn),
   pass rate, every mismatch classified (defect / known semantic / stale cache / source data), proposed fixes with
   their file footprint (not implemented in the batch).
6. **Update this file.** Flip the status of each check run, add any new checks the batch wrote (next free DI id,
   status `proposed` or the result), update the Progress line, the batch plan row and the batch log.

## Sampling rule

Each sampled member is a pass or a fail. With 0 failures in a sample of n, the 95 % upper bound on the class's
failure rate is about 3/n (the rule of three):

| n | 95 % upper bound with 0 failures |
|---|---|
| 10 | < 30 % |
| 30 | < 10 % |
| 60 | < 5 % |
| 100 | < 3 % |
| 300 | < 1 % |

**Strata.** Sort the class's members by the size of their main figure and draw equal thirds from the top, the
middle and the small end, so the heavy tail (the largest committees and donors, where readers look first) is
always in the sample. Every class has an n and a reason in `docs/page_inventory.md`; most-read classes take
n = 60, the rest n = 30. One failure ends the "no defect" claim for the class: the batch finds the cause, reports
how many members it touches (a measured count, not an estimate), and the check stays `fail`.

## Batch plan

| Batch | Classes (C-NN) | Why this order | Status | Report |
|---|---|---|---|---|
| 1 | C-01 | Most-read entity pages; the known 8b defect (donor page omits a committee) lives here | done (2026-10-29): live paths pass; all-time precomputed paths fail: I1, I2, E — fixes F1/F2a/F4/F6 proposed to terminal 1 | `docs/audits/data_integrity_batch1_donors_2026-10-29.md` |
| 2 | C-02, C-03 | Candidate profiles and their tabs are the next most-read entity pages | done (2026-10-30): DI-004 (amended), DI-005, DI-006, DI-007 pass 30 / 30 each; new DI-039 pass (residual 0); no page defect | `docs/audits/data_integrity_batch2_candidates_2026-10-30.md` |
| 3 | C-04, C-05, S-02 | Committee profiles, filings and the committee directory; the D-2 is the sworn ground truth later batches lean on | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-008 (amended, E20 bound), DI-009, DI-010, DI-011, DI-031 pass; new DI-040, DI-041 fail; fixes F-B3a–d | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §1 (original `data_integrity_batch3_committees_2026-10-30.md` lost) |
| 4 | S-01, S-03, C-06 | Home and candidate directory cards restate classes 1–3; candidate-finance shares their D-2 source | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-030 pass 10 / 10; DI-029 fail 17 / 22, DI-032 fail 5 / 7, DI-012 fail (REAL sums); new DI-042 fail; fixes F-B4a–g | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §2 (original `data_integrity_batch4_home_directory_finance_2026-10-30.md` lost) |
| 5 | C-07, C-08 | State races and analytics dashboards (cached builders over the same receipts) | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-013, DI-014, DI-015 pass; new DI-043, 044, 045, 048 pass, DI-046, 049 fail, DI-047 proposed; fixes F-B5a–e | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §3 (original `data_integrity_batch5_races_analytics_2026-10-30.md` lost) |
| 6 | C-09, C-10 | Federal (FEC) pages, one source family | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-016, 017, 018 fail under the FEC rule (memo + superseded rows, 35 % of Schedule A dollars locally); new DI-051, 052 fail, DI-050 proposed; fixes F-B6a–i | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §4 (original `data_integrity_batch6_federal_2026-10-30.md` lost) |
| 7 | C-11, C-12 | Lobbying and IRS 527, the match-table classes | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-019, 020, 021, 022 fail; new DI-053, 054, 055 fail; fixes F-B7a–h | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §5 (original `data_integrity_batch7_lobbying_527_2026-10-30.md` lost) |
| 8 | C-13, C-14, S-04 | OpenBook, cross-source tools and compare (tools restate figures checked earlier) | done (2026-10-30; reconstructed 2026-11-08 — report lost): DI-025, DI-033 pass; DI-023, DI-024 fail; new DI-056, 057 fail; fixes F-B8a–g | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §6 (original `data_integrity_batch8_openbook_tools_compare_2026-10-30.md` lost) |
| 9 | C-16, C-15, C-17 + admin audits | JSON endpoints, legacy pages, the local-only Viz Lab and internal audit pages last | done (2026-11-08): DI-028 pass 10 / 10; DI-026 fail (legacy Donors figure); DI-027 fail 25 / 30; DI-034 blocked (no local credential); new DI-058, DI-059, DI-060 fail — fixes F-B9a–g proposed | `docs/audits/data_integrity_batch9_json_legacy_vizlab_admin_2026-11-08.md` |

S-05 (`/about`) and S-06 (`/privacy`) show no figures and need no check; the non-page endpoints in
`docs/page_inventory.md` carry no figures except the two admin audit pages (DI-034).

## Index

| ID | Class | Figure | Status |
|---|---|---|---|
| DI-001 | C-01 | Donor profile total, count and "Who They Donated To" | fail (2026-10-29, I2 + I1; batch 1 report) |
| DI-002 | C-01 | Committee donor ledger: donor count and total in the window | pass (2026-10-29, 10 / 10; batch 1 report) |
| DI-003 | C-01 | Donor directory top rows for the default cycle | pass (2026-10-29, 10 / 10; batch 1 report) |
| DI-004 | C-02 | Candidate "Money raised" per cycle | pass (2026-10-30, 30 / 30, amended recomputation; batch 2 report) |
| DI-005 | C-02 | Candidate outside spending (D-2 Part 9) supporting / opposing | pass (2026-10-30, 30 / 30; batch 2 report) |
| DI-006 | C-03 | Entity Donors tab: totals (itemized, in-kind) and top donors | pass (2026-10-30, 30 / 30: 15 candidates + 15 races; batch 2 report) |
| DI-007 | C-03 | Network tab edge amount and its drill-down rows | pass (2026-10-30, 30 / 30; batch 2 report) |
| DI-008 | C-04 | Committee Receipts Summary (window and This cycle) | pass (2026-10-30, 60 / 60, amended recomputation + E20 bound; batch 3, reconstructed — report lost) |
| DI-009 | C-04 | Committee Expenditures Summary and payee ledger total | pass (2026-10-30, 60 / 60, payee gap 0; batch 3, reconstructed — report lost) |
| DI-010 | C-05 | Filing detail line items and D-2 summary | pass (2026-10-30, 60 / 60; batch 3, reconstructed — report lost) |
| DI-011 | C-05 | D-2 reconciliation row | pass (2026-10-30, 30 / 30 per page vs current source; staleness → DI-041; batch 3, reconstructed — report lost) |
| DI-012 | C-06 | Candidate-committee finance row and its itemized pages | fail (2026-10-30, REAL sums: 659 of 5,252 rows > 1¢, max $0.62; itemized 30 / 30; batch 4, reconstructed — report lost) |
| DI-013 | C-07 | State race per-candidate raised and race total | pass (2026-10-30, census 1,465 / 1,465 + 30 races; batch 5, reconstructed — report lost) |
| DI-014 | C-08 | Analytics monthly time series | pass (2026-10-30, 53 / 53 months, contributions predicate; batch 5, reconstructed — report lost) |
| DI-015 | C-08 | Analytics geography state and city totals | pass (2026-10-30, 20 / 20 states + 20 / 20 cities; batch 5, reconstructed — report lost) |
| DI-016 | C-09 | Federal Schedule A / B / E cycle totals | fail (2026-10-30, memo + superseded rows counted, FEC rule E32; batch 6, reconstructed — report lost) |
| DI-017 | C-10 | Federal candidate and committee Schedule A totals | fail (2026-10-30, 2 / 10 + 2 / 10 under the FEC rule; batch 6, reconstructed — report lost) |
| DI-018 | C-10 | Federal race outside spending (Schedule E) | fail (2026-10-30, 9 / 10; batch 6, reconstructed — report lost) |
| DI-019 | C-11 | Lobbying clients per entity per registration year | fail (2026-10-30, (member, reg_year) rows counted; 6 / 15 + 6 / 15; batch 7, reconstructed — report lost) |
| DI-020 | C-11 | Lobbying matched campaign donors money | fail (2026-10-30, 2 / 15 (cycle 8 / 15), money selected by donor name; batch 7, reconstructed — report lost) |
| DI-021 | C-12 | 527 organization contributions and expenditures | fail (2026-10-30, 17 / 30 under the amendment rule, raw 30 / 30; batch 7, reconstructed — report lost) |
| DI-022 | C-12 | 527 dark-money totals | fail (2026-10-30, superseded forms + page-less EINs + "(N matches)" fan-out; batch 7, reconstructed — report lost) |
| DI-023 | C-13 | OpenBook vendor contracts and contributions | fail (2026-10-30, detail 62 / 68: 250-row cap, text-date windows, junk contribution rows; batch 8, reconstructed — report lost) |
| DI-024 | C-14 | Tool figures equal the profile figures they link to | fail (2026-10-30, candidate rows 0 / 14, donor 15 / 15, committee 14 / 14; batch 8, reconstructed — report lost) |
| DI-025 | C-14 | Live feed latest dates | pass (2026-10-30, 4 dates + 40 / 40 rows; batch 8, reconstructed — report lost) |
| DI-026 | C-15 | Legacy pages and v1 API counts | fail (2026-11-08, legacy Donors figure reads the ISBE directory; batch 9 report) |
| DI-027 | C-16 | Analytics JSON payload values | fail (2026-11-08, 25 / 30; payload = page 14 / 14; batch 9 report) |
| DI-028 | C-17 | Viz Lab race-money prototype value | pass (2026-11-08, 10 / 10; batch 9 report) |
| DI-029 | S-01 | Home stat cards | fail (2026-10-30, 17 / 22; batch 4, reconstructed — report lost) |
| DI-030 | S-01 | Home Top Donors | pass (2026-10-30, 10 / 10 both views after F1; batch 4, reconstructed — report lost; earlier fail 2026-10-29, I2) |
| DI-031 | S-02 | Committee directory windowed receipts | pass (2026-10-30, 30 / 30 + census 2,898 / 0 differ; batch 3, reconstructed — report lost) |
| DI-032 | S-03 | Candidate directory stat cards | fail (2026-10-30, 5 / 7, D-2 over-count; batch 4, reconstructed — report lost) |
| DI-033 | S-04 | Compare stat cards | pass (2026-10-30, 20 / 20 over 10 pairs; batch 8, reconstructed — report lost) |
| DI-034 | admin | Federal receipt / disbursement audit pages | blocked (2026-11-08, no local credential; batch 9 report) |
| DI-035 | C-01 | Donor page All time agrees with its own This cycle (I2 detector) | fail (2026-10-29, I2; batch 1 report) |
| DI-036 | C-01 | All-time donor directory freshness (`donor_entity_local`) | fail (2026-10-29, E, local; prod unknown; batch 1 report) |
| DI-037 | C-01 | Network in-kind links land on a page showing the in-kind money | fail (2026-10-29, I1, 52.5 % of rendered links; batch 1 report) |
| DI-038 | C-01 | Network 1A donor links land on a page listing the committee | pass (2026-10-29, 917 / 917; batch 1 report) |
| DI-039 | C-02 / C-04 | Live receipts outside the condensed view are explained (the D4 detector) | pass (2026-10-30, residual 0 of 34,188 rows; batch 2 report) |
| DI-040 | C-05 | Filing detail renders the line items of every filing (working title) | fail (2026-10-30, 281,867 of 454,333 filings with line items render empty; batch 3, reconstructed — report lost) |
| DI-041 | C-05 | D-2 reconciliation tables are current (working title; RF-006) | fail (2026-10-30, built from the 2026-02-06 input, 10,437 D-2s missing; batch 3, reconstructed — report lost) |
| DI-042 | S-01 / S-03 | One live D-2 per committee × period (working title) | fail (2026-10-30, 42 extra live D-2s in 40 periods / 38 committees; batch 4, reconstructed — report lost) |
| DI-043 | C-08 | Analytics overview headline cards (working title) | pass (2026-10-30; batch 5, reconstructed — report lost) |
| DI-044 | C-08 | Donor concentration metrics (working title) | pass (2026-10-30, 25 / 25; batch 5, reconstructed — report lost) |
| DI-045 | C-08 | Geo drill-down header and top rows (working title) | pass (2026-10-30, IL / Chicago 4 / 4 + 10 / 10; batch 5, reconstructed — report lost) |
| DI-046 | C-08 | Analytics network graph node / edge figures (working title) | fail (2026-10-30, 199 / 200 committee→candidate edges off; batch 5, reconstructed — report lost) |
| DI-047 | C-08 | Analytics relationships top rows (working title) | proposed (written 2026-10-30, batch 5, not run; its query lost with the report) |
| DI-048 | C-08 | Risk and anomaly flags (working title) | pass (2026-10-30, 25 / 25; batch 5, reconstructed — report lost) |
| DI-049 | C-08 | 527 ecosystem graph (working title) | fail (2026-10-30, empty in every window; batch 5, reconstructed — report lost) |
| DI-050 | C-09 | Federal matching cards (working title) | proposed (written 2026-10-30, batch 6; arithmetic passes, matched count not recomputed) |
| DI-051 | C-09 | Federal Influence page degrees (working title) | fail (2026-10-30, $0.00 / 0 on all 50 rows; batch 6, reconstructed — report lost) |
| DI-052 | C-09 / C-10 | FEC memo + superseded rows detector (working title) | fail (2026-10-30, 25,140 rows / $41,887,101.47 = 35.0 %; batch 6, reconstructed — report lost) |
| DI-053 | C-12 | One live Form 8872 per EIN × period (working title) | fail (2026-10-30, 141 superseded forms / 41 EINs; batch 7, reconstructed — report lost) |
| DI-054 | C-12 | One 527 recipient-match row per target (working title) | fail (2026-10-30, +7,677 rows; batch 7, reconstructed — report lost) |
| DI-055 | C-12 | 527 funding-source scope and label (working title) | fail (2026-10-30, 567 page-less EINs, 97 % of dollars; label defect, scope for Devin; batch 7, reconstructed — report lost) |
| DI-056 | C-13 | Detail cards cover all rows | fail (2026-10-30, 6 vendors capped at 250 rows; batch 8, reconstructed — report lost) |
| DI-057 | C-13 | Date columns parse and window | fail (2026-10-30, text M/D/YY dates compared as ISO → windowed figures 0; batch 8, reconstructed — report lost) |
| DI-058 | C-08 / C-16 | NLP spending categories read the legacy contributions table | fail (2026-11-08, 766 legacy rows; batch 9 report) |
| DI-059 | C-16 | State focus-sankey donor nodes group by last / business name only | fail (2026-11-08, committee 25185 all time 9 / 20 nodes; batch 9 report) |
| DI-060 | C-03 / C-16 | Candidate-competition donor pool cut inside a tie | fail (2026-11-08, 185 tied at the cut, 82 inside; batch 9 report) |

## Conventions

- Dates are ISO (`YYYY-MM-DD`). Site paths are in backticks relative to the site root; placeholders go in angle
  brackets (`/committees/<id>`). Examples name public officials and committees only (Michael W Frerichs =
  candidate 13505; Democratic Party of Illinois = committee 6239); donor pages are reached by clicking through,
  and no private name, address or donor key is ever written here or in a batch report.
- `<start>` / `<end>` are the dates the page's window bar shows. For an all-time window drop the date clause.
- Shared SQL pieces used below (ISBE receipts, alias `r`):
  - **Donor key** (the site's `donor_key`, `Donor._bulk_donor_key_sql` on the compat view's column names):
    `LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,'')))`
  - **Contributions** (`receipts_filter_sql`, parts `contributions`):
    `r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')`
  - **All receipts** (parts `all`): `r.archived = FALSE AND r.amount > 0`
- **Picked receipts** (the documented amendment picker of `MATVIEW_DDL` in `scripts/isbe_sunshine_etl.py`, applied
  to the source tables so a picker defect in the matview cannot pass unseen; added 2026-10-30, batch 2). A receipt
  counts when it is dated after the committee's last periodic `reporting_period_end` (periodic = `doc_name` not
  A-1, Statement of Organization, Letter/Correspondence, B-1 or Nonparticipation; none → 1900-01-01) **or** it is
  on the one filing kept per (committee, period): `doc_name <> 'Pre-election'`, a live D-2 first, then the latest
  `received_datetime` (NULLS LAST), then the highest id. `<ids>` is the committee id array:
  ```sql
  WITH cm AS (SELECT unnest(ARRAY[<ids>]::int[]) AS committee_id),
  last_end AS (
    SELECT c.committee_id, COALESCE((SELECT MAX(f.reporting_period_end) FROM isbe_filed_docs f
           WHERE f.committee_id = c.committee_id
             AND f.doc_name NOT IN ('A-1', 'Statement of Organization', 'Letter/Correspondence', 'B-1', 'Nonparticipation')),
           DATE '1900-01-01') AS pe
    FROM cm c),
  kept AS (
    SELECT DISTINCT ON (f.committee_id, f.reporting_period_begin, f.reporting_period_end) f.id
    FROM isbe_filed_docs f
    WHERE f.committee_id IN (SELECT committee_id FROM cm) AND f.doc_name <> 'Pre-election'
    ORDER BY f.committee_id, f.reporting_period_begin, f.reporting_period_end,
             EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = f.id AND d.archived = FALSE) DESC,
             f.received_datetime DESC NULLS LAST, f.id DESC)
  SELECT ... FROM isbe_receipts r JOIN last_end le ON le.committee_id = r.committee_id
  WHERE r.archived = FALSE AND r.amount > 0
    AND (r.received_date > le.pe OR r.filed_doc_id IN (SELECT id FROM kept)) ...;
  ```
  Picked expenditures: the same over `isbe_expenditures e` with `e.expended_date`.
- A donor key copied from a `/donors/key/<donor_key>` URL is URL-encoded: decode `%7C` to `|` and `%20` to a space
  before pasting it into a query.

## Checks

### DI-001 — Donor profile total, count and committee list

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** fail (2026-10-29, mechanisms I2 and I1; see batch 1 report). I2: 1A rows filed on an A-1 or on any
  filing with no live D-2 are missing from the all-time page (for example the top committee donor on
  `/committees/6239/donors`, whose $1.55M gift is not on its own page; detector DI-035). I1: in-kind (5A) giving
  never appears, so in-kind network links land on a page without the committee (DI-037). Fixes F1, F2a and F4
  proposed to terminal 1.
- **Figure:** `/donors/key/<donor_key>`: "Total Contributed", "Number of Contributions", "Committees Supported"
  and the "Who They Donated To" table (committee, amount).
- **Independent recomputation:**
  ```sql
  SELECT r.committee_id, c.name, COUNT(*) AS n, SUM(r.amount) AS total
  FROM isbe_receipts r JOIN isbe_committees c ON c.id = r.committee_id
  WHERE r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
    AND LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,''))) = '<donor_key>'
  GROUP BY 1, 2 ORDER BY total DESC;
  ```
  Then the same with `d2_part = '5A'` in place of the contributions clause, to see in-kind giving the page leaves
  out.
- **Tolerance:** totals to the cent; count exact; the committee set identical.
- **Sample rule:** n = 60 donors, 20 top / 20 middle / 20 small by all-time total, plus every committee-donor in the
  top 20; redacted donors excluded.
- **Steps:**
  1. Open `/committees/6239/donors` (or a sampled committee's donors page) and click a sampled donor.
  2. Note the four figures and the committee rows; copy the key from the URL and decode it.
  3. Run the query in `psql ilcf`; sum `n` and `total`, count the rows.
- **Pass:** the sums equal "Number of Contributions" and "Total Contributed", the row count equals "Committees
  Supported", and every committee and amount in the table appears in the query result.
- **Fail:** any total off by more than a cent, a committee present in one list and not the other (for example the
  committee whose donors page you started from is missing), or a count mismatch. A gap equal to the donor's rows on
  filings with no live D-2 (`on_no_d2_filings` in DI-035) is I2; a missing committee that only shows up in the 5A
  query is I1. Any other gap is a new mechanism: record it.

### DI-002 — Committee donor ledger count and total

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** pass (2026-10-29, 10 / 10 committees, count, total and cycle exact; see batch 1 report). Batch 1's
  first-row check also passed 10 / 10 (the top donor's page lists the committee); committee 6239's top row is the
  known I2 exception (DI-001, DI-035).
- **Figure:** `/committees/<id>/donors` subtitle "N unique donors, $T | This cycle: $C" (All time).
- **Independent recomputation** (batch 1's query, adopted):
  ```sql
  SELECT COUNT(DISTINCT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,'')))) AS unique_donors,
         ROUND(SUM(r.amount)::numeric, 2) AS total,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.received_date BETWEEN '2025-01-01' AND '2026-12-31'), 0)::numeric, 2) AS this_cycle
  FROM isbe_condensed_receipts r
  WHERE r.committee_id = <id> AND r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%');
  ```
  If a figure differs, rerun with `FROM isbe_receipts r` (raw rows, not archived) to tell a condensed-picker or
  amendment difference from a page defect. D4 (fixed 2026-10-29 in the ETL; prod after its matview rebuild): the
  condensed views' per-period picker preferred a later empty A-1 / Letter / B-1, or a dated archived original, over
  the filing with a live D-2 (ties resolved by plan), and `isbe_most_recent_filings` could pick an undated Raffle
  Report / Final / Notification of Self Funding (453 committees with a NULL cutoff). Measured locally on live part-1
  receipts, all committees: 42,088 rows / $38.8M enter across 518 committees and 760 rows / $148K leave across 20,
  net +$38.7M. A committee's total far below its sworn D-2 total before the rebuild is most likely D4, not a page
  defect.
- **Tolerance:** counts exact; dollars ± $0.01.
- **Sample rule:** manual run: 3 committees, 1 of the 5 largest by receipt rows, 1 party committee (for example
  6239) and 1 random committee with ≥ 50 donors. Batch: n = 60 committees, 20 top / 20 middle / 20 small by
  all-time receipts (batch 1 measured n = 10).
- **Steps:**
  1. Open `/committees/<id>/donors` for a sampled committee (start with `/committees/6239/donors`).
  2. Read the three numbers in the subtitle.
  3. Run the query with the id.
- **Pass:** all three match.
- **Fail:** any differs. Record the committee id and the three pairs of numbers.

### DI-003 — Donor directory top rows

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** pass (2026-10-29, default 2026-cycle window, 10 / 10 rows equal their donor page and the
  recomputation; see batch 1 report). The all-time directory (`/donors/?period=all`) is a different data path: it
  lists merged entities from `donor_entity_local` and failed there (mechanism E, plus I2); that path is checked by
  DI-036.
- **Figure:** `/donors/` in its default window (2026 cycle): the top 10 rows' totals, contribution counts and
  committee counts.
- **Independent recomputation:**
  ```sql
  SELECT LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,''))) AS donor_key,
         SUM(r.amount) AS total, COUNT(*) AS n, COUNT(DISTINCT r.committee_id) AS committees
  FROM isbe_receipts r
  WHERE r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
    AND r.received_date BETWEEN '2025-01-01' AND '2026-12-31'
  GROUP BY 1 ORDER BY total DESC LIMIT 10;
  ```
- **Tolerance:** to the cent. A difference that equals the donor's blank-`d2_part` rows, or rows whose filed doc has
  no non-archived D-2 report, is a fail: since fix F1 (commit 64c3b62, batch-1 finding I2 fixed) the analytics donor
  filter (`_BULK_DONOR_RECEIPT_FILTER_SQL` in `database/analytics.py`) is the live contributions predicate with no D-2
  check (on a site where `refresh-analytics` has not run since that deploy, record it as "stale aggregates").
- **Sample rule:** the top 10, then 10 from the middle and 10 from the bottom of the directory's sorted list.
- **Steps:**
  1. Open `/donors/`; confirm the window reads "2026 Cycle".
  2. Note the top 10 rows' figures (compare by matching the query's row to the page row by rank and name; do not
     copy keys into notes).
  3. Run the query.
- **Pass:** same order and same figures for every sampled row.
- **Fail:** a different order or any figure off by more than a cent with no named cause.

### DI-004 — Candidate money raised per cycle

- **Class:** C-02
- **Created:** 2026-10-29; recomputation amended 2026-10-30 (batch 2)
- **Status:** pass (2026-10-30, 30 / 30 candidates, every cycle to the cent, local; see batch 2 report). As first
  written, the query summed every live `isbe_receipts` row and matched only 23 / 30. All 7 differences were
  superseded rows ISBE left live, and the page was right in each case (worked examples under DI-039).
- **Figure:** `/candidates/<id>`: the "Money raised" table (cycle, raised).
- **Independent recomputation:** the **picked receipts** fragment (Conventions) with
  `<ids>` = `SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>`, then:
  ```sql
  SELECT EXTRACT(YEAR FROM r.received_date)::int + EXTRACT(YEAR FROM r.received_date)::int % 2 AS cycle,
         ROUND(SUM(r.amount)::numeric, 2) AS raised
  FROM isbe_receipts r JOIN last_end le ON le.committee_id = r.committee_id
  WHERE r.archived = FALSE AND r.amount > 0 AND r.received_date IS NOT NULL
    AND (r.received_date > le.pe OR r.filed_doc_id IN (SELECT id FROM kept))
  GROUP BY 1 ORDER BY 1 DESC;
  ```
  Never read `isbe_condensed_receipts` here: comparing the page with its own matview cannot see a picker defect.
- **Tolerance:** to the cent per cycle.
- **Sample rule:** n = 30 candidates, 10 statewide or top-raised / 10 middle / 10 small.
- **Steps:**
  1. Open `/candidates/13505` (then each sampled candidate).
  2. Read every row of "Money raised".
  3. Run the query.
- **Pass:** every cycle's amount matches.
- **Fail:** any cycle off by more than a cent. Then run the same query on `isbe_condensed_receipts`. If the page
  equals the matview but not this query, the picker (the matview DDL) is at fault; if it equals neither, the page
  is. A plain sum of all live raw rows is higher by the DI-039 categories and is not a failure.

### DI-005 — Candidate outside spending

- **Class:** C-02
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, 30 / 30 candidates, local; see batch 2 report). Supporting, Opposing, row count
  and the profile card's "All N line items" equal an independent attribution written from
  `docs/outside_spending.md`. The 94 surname-matching rows the page leaves out are all explained by the rule:
  65 another first name, 14 single-token, 7 substring surname, 8 cycle gate.
- **Figure:** `/candidates/<id>/outside-spending`: supporting and opposing totals and the line-item count; the
  "Outside spending (D-2 Part 9)" card on `/candidates/<id>`.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*) AS n,
         SUM(e.amount) FILTER (WHERE e.supporting) AS supporting,
         SUM(e.amount) FILTER (WHERE e.opposing) AS opposing
  FROM isbe_expenditures e
  WHERE e.archived = FALSE AND e.d2_part LIKE '9%'
    AND e.candidate_name ILIKE '%<candidate last name>%'
    AND e.expended_date BETWEEN '<start>' AND '<end>';
  ```
- **Tolerance:** to the cent after removing rows the name rule in `docs/outside_spending.md` excludes (another
  candidate with the same surname, multi-candidate strings); every removed row is listed by count with its reason.
- **Sample rule:** n = 30 candidates with any Part 9 rows, 10 top / 10 middle / 10 small by Part 9 total.
- **Steps:**
  1. Open `/candidates/13505/outside-spending`.
  2. Read the supporting / opposing totals and the row count.
  3. Run the query; look through rows the page does not show.
- **Pass:** totals match once every excluded row is explained by the name rule.
- **Fail:** an unexplained row, or a total off by more than a cent.

### DI-006 — Entity Donors tab

- **Class:** C-03
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, 30 / 30 entities = 15 candidates + 15 cycle-2026 races, local: Total, In-kind and
  every Top donors row exact; see batch 2 report). Earlier: pass (2026-10-29, 5 / 5 candidates; batch 1 report).
  Blank-`d2_part` gap: 0 of 30 sampled entities; 3 condensed rows / $11,400 site-wide.
- **Figure:** `/candidates/<id>/donors` (and `/analytics/state-races/<race_key>/donors`): "Concentration by group",
  **Total** and **In-kind** on the "All committees" row (or the single group row); the "Top donors" table (top 10
  amounts).
- **Independent recomputation** (batch 2 ran both queries over the **picked receipts** fragment of the Conventions,
  `<ids>` = the entity's committees, not over `isbe_condensed_receipts`; the statements below are batch 1's form):
  totals (batch 1's query, adopted):
  ```sql
  SELECT ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.d2_part LIKE '1%'),0)::numeric,2) AS itemized,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.d2_part = '5A'),0)::numeric,2) AS in_kind
  FROM isbe_condensed_receipts r
  WHERE r.archived = FALSE AND r.amount > 0
    AND r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>);
  ```
  Top donors rows (kept: it checks the per-donor rows the totals query does not):
  ```sql
  SELECT LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,''))) AS donor_key,
         SUM(r.amount) AS total
  FROM isbe_receipts r
  WHERE r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>)
    AND r.archived = FALSE AND r.amount > 0 AND r.d2_part LIKE '1%'
    AND r.received_date BETWEEN '<start>' AND '<end>'
  GROUP BY 1 ORDER BY total DESC LIMIT 10;
  ```
  The tab uses `d2_part LIKE '1%'` (blank excluded); rerun with the contributions clause to measure the gap with
  the committee ledger and record it.
- **Tolerance:** ± $0.01 per figure and per row; same order. Compare each shown donor with its own key's total.
  Donors tied at the 10th amount may be a different key of equal amount, which is not a failure.
- **Sample rule:** manual run: 2 candidates, 13505 and one random candidate with a committee. Batch: n = 30
  entities, 15 candidates and 15 races, top / middle / small by raised (batch 1 measured 5 candidates).
- **Steps:**
  1. Open `/candidates/13505/donors` (All time).
  2. Read Total and In-kind on the "All committees" row, and the top 10 donor rows.
  3. Run both queries (for a race, take the linked committees of every candidacy in the race).
- **Pass:** Total, In-kind and every sampled row match.
- **Fail:** any differs with no named cause. An accepted committee family adds committees that are not in
  `isbe_candidate_committees`, so check `/admin/committee-families` before recording a failure.

### DI-007 — Network edge and drill-down rows

- **Class:** C-03
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, 30 / 30 edges, one per DI-006 entity, local: 14 money-in, 6 in-kind, 10 money-out;
  edge weight = `network/rows.json` total = recomputation, and the counts are equal; every money-in donor link opens a
  200 page that lists the committee; in-kind-only donors have no link by design (F2a); see batch 2 report).
- **Figure:** `/candidates/<id>/network.json`: one donor → committee edge amount, and the rows of the drill-down
  panel that opens when you click it on `/candidates/<id>/network`.
- **Independent recomputation:** for an edge `donor:<donor_key>` → `committee:<committee_id>` in the money-in
  panel:
  ```sql
  SELECT COUNT(*) AS n, SUM(r.amount) AS total
  FROM isbe_receipts r
  WHERE r.committee_id = <committee_id>
    AND r.archived = FALSE AND r.amount > 0 AND r.d2_part LIKE '1%'
    AND LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,''))) = '<donor_key>'
    AND r.received_date BETWEEN '<start>' AND '<end>';
  ```
  For an in-kind panel edge use `r.d2_part = '5A'`. Batch 2 ran it over the **picked receipts** fragment
  (Conventions), not the raw table. For a money-out edge (`committee_vendor`, target `vendor:<sha1[:16] of the lowered
  payee name>`), use picked expenditures with `(e.d2_part IS NULL OR NOT (e.d2_part like '9%'))` and
  `LOWER(TRIM(COALESCE(e.first_name,'') || ' ' || COALESCE(e.last_name,''))) = '<payee name, lower case>'`. The
  drill-down rows are `/candidates/<id>/network/rows.json?source=<source>&target=<target>&edge_type=<type>`
  (`total_amount`, `total_count`).
- **Tolerance:** to the cent; the drill-down row count equals `n`.
- **Sample rule:** one edge per sampled entity of DI-006 (n = 30), mixing money-in, in-kind and money-out panels.
- **Steps:**
  1. Open `/candidates/13505/network`, click an edge, note its amount and rows.
  2. Find the same edge in `/candidates/13505/network.json` (its `source`, `target`, amount).
  3. Run the query.
- **Pass:** edge amount, panel total and query total agree; row counts agree; the donor node's link opens a donor
  page whose committee list contains this committee (cross-check with DI-001). "Who They Donated To" lists committee
  names without links: find the committee by name, and its amount must be at least the edge amount. An in-kind-only
  donor has no link (fix F2a).
- **Fail:** any disagreement, or a donor link that lands on a page without this committee.

### DI-008 — Committee Receipts Summary

- **Class:** C-04
- **Created:** 2026-10-29; recomputation and D-2 bound amended 2026-10-30 (batch 3, E18 / E20)
- **Status:** pass (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 60 / 60 committees, all time, count
  and This cycle to the cent against picked receipts, every part; amended D-2 bound 60 / 60 in both windows, "other"
  tail $0.00. As first written the bound failed 45 / 60 all time and 50 / 60 cycle: in-kind 5A sits in `total_inkind`,
  and A-1 receipts after the last period end (2026-06-30) are on no sworn D-2 yet — $103,255,337.48 on 10 committees
  in the cycle window, one row of $65,000,000.00 on committee 32762.)
- **Amended 2026-10-30 (batch 3, E18 / E20; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Supersedes the query and the D-2 bound below. Recompute with the **picked receipts** fragment
  (Conventions), `<ids>` = the committee's id, every part (no `d2_part` filter), for the page window and for
  `'2025-01-01'`..`'2026-12-31'`; never a plain raw sum (it over-counts superseded rows ISBE left live, the DI-039
  categories) and never the matviews or `bulk_*_clean` views the page reads. Bound: picked receipts on filings with a
  live D-2 in the window ≤ Σ (`total_receipts` + `total_inkind`) of those D-2s; picked receipts outside such filings
  split into the tail after the committee's last periodic period end (not yet on a sworn D-2) and 'other', which must
  be $0.00. Per-filing agreement is DI-011's job. Worked examples (Fable's re-run): committee 245 (id ≤ 348, renders
  `detail_sbe.html`) 1 / $1,000.00 / cycle $0.00 = picked; committee 6239 11,409 / $151,547,085.37 / cycle
  $15,406,976.27 = picked.
- **Figure:** `/committees/<id>`: "Receipts Summary" total for the window and its "This cycle" subtotal.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*) AS n, SUM(r.amount) AS total
  FROM isbe_receipts r
  WHERE r.committee_id = <id> AND r.archived = FALSE AND r.amount > 0
    AND r.received_date BETWEEN '<start>' AND '<end>';
  ```
  Run for the page window and for `'2025-01-01'`..`'2026-12-31'`. Sanity bound from the sworn D-2s:
  `SELECT SUM(d.total_receipts) FROM isbe_d2_reports d JOIN isbe_filed_docs f ON f.id = d.filed_doc_id WHERE d.committee_id = <id> AND d.archived = FALSE AND f.reporting_period_begin >= '<start>' AND f.reporting_period_end <= '<end>';`
  (includes non-itemized money, so it is at least the itemized total; it is a bound, not an equality).
- **Tolerance:** to the cent.
- **Sample rule:** n = 60 committees, 20 top / 20 middle / 20 small; include 5 party committees and 5 committees
  with ids 1–348.
- **Steps:**
  1. Open `/committees/6239` (then each sampled committee).
  2. Read the Receipts Summary total and "This cycle".
  3. Run both queries and the D-2 bound.
- **Pass:** page = picked receipts to the cent, the amended bound holds and the 'other' tail is $0.00.
- **Fail:** a mismatch, a bound violation, or a non-zero 'other' tail.

### DI-009 — Committee Expenditures Summary and payee ledger

- **Class:** C-04
- **Created:** 2026-10-29; recomputation amended 2026-10-30 (batch 3, E18)
- **Status:** pass (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 60 / 60 committees, Expenditures
  Summary and payees total / This cycle to the cent, payee count gap 0 everywhere)
- **Amended 2026-10-30 (batch 3, E18; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Supersedes the query's raw sum: recompute over **picked expenditures** (Conventions,
  `e.expended_date`); the payee-name expression stays. Latent F-B3d: the page's Expenditures Summary lacks `amount >
  0` (changes no local figure).
- **Figure:** `/committees/<id>` "Expenditures Summary"; `/committees/<id>/payees` "Payees" count, total and "This
  cycle".
- **Independent recomputation:**
  ```sql
  SELECT COUNT(DISTINCT TRIM(REGEXP_REPLACE(LOWER(TRIM(COALESCE(e.first_name,'') || ' ' || COALESCE(e.last_name,''))), '[^[:alnum:]]+', ' ', 'g'))) AS payees,
         SUM(e.amount) AS total
  FROM isbe_expenditures e
  WHERE e.committee_id = <id> AND e.archived = FALSE AND e.amount > 0
    AND e.expended_date BETWEEN '<start>' AND '<end>';
  ```
- **Tolerance:** total to the cent; payee count ±1 % (the page joins first and last name slightly differently
  when one is blank) and every larger gap explained.
- **Sample rule:** the 60 committees of DI-008.
- **Steps:**
  1. Open `/committees/6239/payees`; read the count, total and "This cycle".
  2. Read "Expenditures Summary" on `/committees/6239`.
  3. Run the query for both windows.
- **Pass:** totals match to the cent, and the payee count is within tolerance.
- **Fail:** a total mismatch or an unexplained count gap.

### DI-010 — Filing detail

- **Class:** C-05
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 60 / 60 filed documents incl. 12
  amended D-2s; 58 equal the raw document, 2 Pre-election filings (343531, 415272) render empty by the condensed rule
  and the page shows no D-2 summary — the class-wide form of that is DI-040)
- **Figure:** `/committees/filing/<filed_doc_id>`: the line items (count, sum) and the D-2 summary figures.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*) AS n, SUM(amount) AS total FROM isbe_receipts WHERE filed_doc_id = <filed_doc_id>;
  SELECT COUNT(*) AS n, SUM(amount) AS total FROM isbe_expenditures WHERE filed_doc_id = <filed_doc_id>;
  SELECT beginning_funds_avail, individual_itemized, individual_non_itemized, total_receipts,
         total_expenditures, end_funds_available, archived
  FROM isbe_d2_reports WHERE filed_doc_id = <filed_doc_id>;
  ```
- **Tolerance:** counts exact, sums to the cent. If the page shows fewer lines than the query, check whether it
  reads `isbe_condensed_receipts` and the filing is not the one the picker kept for its period (name it).
- **Sample rule:** n = 60 filed documents, 20 from top committees / 20 middle / 20 small, at least 10 amended.
- **Steps:**
  1. Open `/committees/6239/filings`, open a filing (for example `/committees/filing/1015462`).
  2. Note the line-item count and sum and the D-2 figures.
  3. Run the three queries.
- **Pass:** every figure matches.
- **Fail:** any unexplained difference.

### DI-011 — D-2 reconciliation row

- **Class:** C-05
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 30 / 30 rows per page against the
  current source; window census 258 receipts + 327 expenditures rows stale, 0 defects; middle stratum drawn from
  non-zero-difference rows. The pages read the legacy `bulk_d2_*_recon` tables — their staleness is DI-041 / RF-006)
- **Amended 2026-10-30 (batch 3, E19; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Classify a difference as **stale cache** when the filing (or its D-2) changed after the recon table's
  last build, and as a defect only when the row's own figures disagree with the source rows as they stood. The pages
  read `bulk_d2_receipts_recon` / `bulk_d2_expenditures_recon`, which only `import-bulk-download` rebuilds (RF-006;
  their staleness is DI-041).
- **Figure:** a row on `/d2-reconciliation/` (D-2 total receipts, itemized sum, difference) and on
  `/d2-expenditures-reconciliation/`.
- **Independent recomputation:**
  ```sql
  SELECT d.total_receipts, d.individual_itemized,
         (SELECT SUM(r.amount) FROM isbe_receipts r WHERE r.filed_doc_id = d.filed_doc_id AND r.archived = FALSE) AS itemized_sum
  FROM isbe_d2_reports d WHERE d.filed_doc_id = <filed_doc_id>;
  ```
  The expenditures page: the same with `total_expenditures` and `isbe_expenditures`.
- **Tolerance:** to the cent. Keep the open ETL bug in view: `transfer_in`, `loan_received` and `other_receipts`
  in `isbe_d2_reports` hold the non-itemized halves, so do not compare against them.
- **Sample rule:** 30 rows per page, top / middle / small by absolute difference.
- **Steps:**
  1. Open `/d2-reconciliation/`; pick sampled rows and note their filed doc ids and figures.
  2. Run the query per row.
- **Pass:** D-2 total, itemized sum and difference match.
- **Fail:** any mismatch.

### DI-012 — Candidate-committee finance row

- **Class:** C-06
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 4; reconstructed 2026-11-08 — report lost; frame 5,252 cycle-2026 groups (list
  header 5,254: 2 pairs with only archived D-2s); 20 / 30 rows to the cent, filing counts 30 / 30; the 10 top rows off
  by ≤ $0.40 because `sum_total_receipts` etc. are `REAL` (float4) — census 659 of 5,252 rows > 1¢, max $0.62 (fix
  F-B4e); itemized pages 30 / 30 × 2 (rows, CSV count and sum = picked receipts / expenditures, all parts). Fable's
  re-run: 39266 × 32762 / 2025 shows 2.5552252e+07 vs exact $25,552,251.95.)
- **Amended 2026-10-30 (batch 4, year rule; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** The matview's year (`period_year`) is the year of the **latest receipt on the filing**, not the D-2
  period end; `election_cycle = year + year % 2`. Live D-2s with no receipt row get year NULL and appear in no cycle
  (13,026 locally; $1,357,700.86 receipts / $12,111,952.69 expenditures; known semantic, rule change F-B4f is Devin's
  call). The query below (`EXTRACT(YEAR FROM f.reporting_period_end)`) does not follow that rule — rewrite it at
  re-run. Itemized pages: picked receipts / expenditures, all parts. Slate committees repeat on each candidate's row
  (known semantic).
- **Figure:** `/candidate-finance/`: a row's total receipts, total expenditures and ending funds; the row count on
  `/candidate-finance/<candidate_id>/<committee_id>/itemized`.
- **Independent recomputation:**
  ```sql
  SELECT SUM(d.total_receipts) AS receipts, SUM(d.total_expenditures) AS expenditures,
         MAX(d.end_funds_available) AS max_ending_funds, COUNT(*) AS filings
  FROM isbe_d2_reports d JOIN isbe_filed_docs f ON f.id = d.filed_doc_id
  WHERE d.committee_id = <committee_id> AND d.archived = FALSE
    AND EXTRACT(YEAR FROM f.reporting_period_end) = <period_year>;
  ```
  Before the batch, read the year rule of `bulk_candidate_committee_finance_agg` in `scripts/swap_bulk_to_isbe.py`
  and use the same year column; itemized: `SELECT COUNT(*), SUM(amount) FROM isbe_receipts WHERE committee_id = <committee_id> AND archived = FALSE AND received_date BETWEEN '<start>' AND '<end>';`
- **Tolerance:** to the cent; filing count exact.
- **Sample rule:** n = 30 rows, top / middle / small by receipts.
- **Steps:**
  1. Open `/candidate-finance/`, filter to a sampled candidate (start with candidate 13505, committee 14111).
  2. Note the row's figures; open its itemized page and note the count.
  3. Run the queries.
- **Pass:** every figure matches.
- **Fail:** any mismatch.

### DI-013 — State race totals

- **Class:** C-07
- **Created:** 2026-10-29; recomputation replaced 2026-10-30 (batch 5, E23)
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; race-list census 1,465 / 1,465 rows
  (1,024 linked + 441 unlinked by label key), Total Amount $324,105,554.18 both sides; 30 race pages (8 statewide, 12
  largest House / Senate, 10 small): 180 / 180 cards, 60 / 60 committee rows; 79 candidate rows: 54 exact, 25 off by
  DI-012's REAL cents ≤ $0.22)
- **Amended 2026-10-30 (batch 5, E23; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Supersedes the raw-sum query below. Follow the builder's rule: candidates by `isbe_race_candidacies`
  at `election_cycle = 2026`; race identity `_state_race_slug(office, district_type, district)`; contributions and
  race total = **picked receipts**, all parts, summed over distinct race × committee pairs; donors = distinct site key
  with `donor_entity_local_member` merges; Candidate Committee Rows = D-2 `sum_total_receipts` from the finance agg
  split 1/n (DI-012's year rule); top-candidate share from the one-D-2-per-period set (DI-042); Part 9 by DI-005's
  rule. Known semantics: a committee feeding two races counts fully in each (226 committees in 638 race rows); the
  year rule excludes 4,803 D-2s / $1,090,342.77 on race committees (F-B4f); Candidate Committee Rows show the cycle
  D-2 total under a windowed tag. Two statewide keys carry a city in the district field
  (`comptroller-statewide-benton`, `secretary-of-state-statewide-chicago`), a source spelling variant.
- **Figure:** `/analytics/state-races/<race_key>`: per-candidate raised and the race total; the race's row on
  `/analytics/state-races/`.
- **Independent recomputation:**
  ```sql
  WITH race_committees AS (
    SELECT DISTINCT cc.committee_id
    FROM isbe_candidates ca
    JOIN isbe_candidacies cy ON cy.candidate_id = ca.id
    JOIN isbe_candidate_committees cc ON cc.candidate_id = ca.id
    WHERE ca.office = '<office>' AND COALESCE(ca.district_type,'') = '<district_type>' AND COALESCE(ca.district,'') = '<district>'
      AND cy.election_year + cy.election_year % 2 = 2026
  )
  SELECT SUM(r.amount) AS race_total
  FROM isbe_receipts r
  WHERE r.committee_id IN (SELECT committee_id FROM race_committees)
    AND r.archived = FALSE AND r.amount > 0
    AND r.received_date BETWEEN '2025-01-01' AND '2026-12-31';
  ```
  Per candidate: the same with `ca.id = <candidate_id>`. A slate committee counts once per race (DISTINCT above).
- **Tolerance:** to the cent; if the page uses the contributions predicate instead of all receipts, rerun with it
  and record which one the page uses.
- **Sample rule:** n = 30 races: all statewide races, 12 House / Senate districts by total, the rest small.
- **Steps:**
  1. Open `/analytics/state-races/governor-statewide-illinois`.
  2. Note each candidate's raised and the total.
  3. Run the queries with the race's office, district type and district.
- **Pass:** race total and every candidate's figure match.
- **Fail:** any mismatch with no named cause.

### DI-014 — Analytics monthly time series

- **Class:** C-08
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; 53 / 53 months under the
  **contributions** predicate: 21 default-window months, all 24 of cycle:2024, the overview's last 8; every month also
  = `analytics_committee_monthly_totals`. Fable's re-run: 2026-01 $9,174,021.89 / 8,936 rows, 2025-06 $9,235,099.74 /
  7,120 rows.)
- **Figure:** `/analytics/` "Time-Series Preview" (month, amount); the same series on `/analytics/geography`
  "Time-Series Intelligence".
- **Independent recomputation:**
  ```sql
  SELECT DATE_TRUNC('month', r.received_date)::date AS month, SUM(r.amount) AS total, COUNT(*) AS n
  FROM isbe_receipts r
  WHERE r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
    AND r.received_date BETWEEN '<start>' AND '<end>'
  GROUP BY 1 ORDER BY 1;
  ```
  If it does not match, rerun with the all-receipts clause; the check passes only if one of the two matches every
  month, and the report names which.
- **Tolerance:** to the cent per month.
- **Sample rule:** every month in the default window (census, about 22 months), plus 8 months from a 2024 window.
- **Steps:**
  1. Open `/analytics/` (use `?refresh_cache=1` when signed in, or a freshly started local server).
  2. Note each month's amount.
  3. Run the query.
- **Pass:** every month matches under one predicate.
- **Fail:** any month off under both.

### DI-015 — Analytics geography totals

- **Class:** C-08
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; 20 / 20 states (ranks 1–10, 23–32 of
  55) and 20 / 20 cities (ranks 1–10, 246–255 of 500), same order)
- **Amended 2026-10-30 (batch 5, tolerance note; the amended text was lost with the report; rule as logged,
  reconstructed 2026-11-08):** The Donors column uses the page's looser donor identity (IL 74,503 vs 74,645 site
  keys): known semantic. The page's normalization was named in the lost report.
- **Figure:** `/analytics/geography`: "Geospatial Summary - States" and "- Cities" (amount, count).
- **Independent recomputation:**
  ```sql
  SELECT UPPER(TRIM(r.state)) AS state, SUM(r.amount) AS total, COUNT(*) AS n
  FROM isbe_receipts r
  WHERE r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
    AND r.received_date BETWEEN '<start>' AND '<end>'
  GROUP BY 1 ORDER BY total DESC LIMIT 10;
  ```
  Cities: group by `UPPER(TRIM(r.city))` and state.
- **Tolerance:** to the cent; same top-10 order. Name any normalization difference (case, blank states).
- **Sample rule:** top 10 states and top 10 cities, plus 10 from the middle of each list.
- **Steps:**
  1. Open `/analytics/geography`.
  2. Note the states and cities tables.
  3. Run both queries.
- **Pass:** every sampled row matches.
- **Fail:** any unexplained difference.

### DI-016 — Federal cycle totals

- **Class:** C-09
- **Created:** 2026-10-29; rule amended 2026-10-30 (batch 6, E25 / E26 / E32)
- **Status:** fail (2026-10-30, batch 6; reconstructed 2026-11-08 — report lost; every Schedule A / B money figure on
  the dashboards counts FEC memo entries and superseded amendment rows; the arithmetic reproduces. Local Schedule A
  cycle 2026: 89,789 / $119,657,997.47 raw vs 64,649 / $77,770,896.00 under the FEC rule; memo X 24,768 /
  $40,855,838.90; superseded 397 / $1,054,362.57. Schedule B: 2,839 memo rows / $2,551,133.80, no superseded rows.
  Also: the overview Schedule E card windows by stored `cycle` (F-B6e), the Races card caps at 12 (F-B6f). Local FEC
  data: Schedule A to 2026-07-01, B to 2025-12-31, E to 2026-02-10 — older than prod. Fixes F-B6a–f.)
- **Amended 2026-10-30 (batch 6, E25 / E26 / E32; the amended text was lost with the report; rule as logged,
  reconstructed 2026-11-08):** Judge Schedule A / B under the FEC rule: exclude `memo_code = 'X'`; keep one row per
  (committee, `transaction_id`, report) on the highest `file_number`. `memo_code` is not a stored column: recover it
  (and `transaction_id`, `file_number`, report type / year) from the `raw_extractions` payloads, which join to every
  row. Schedule E: `most_recent` and the bounded-cycle date window (`_schedule_e_date_bounds`, MT-015), never the
  stored `cycle` (E26). 'Total Amount' on the list page is Σ `fec_candidate_cycle_totals.receipts` (the FEC's own
  totals): compare with that table.
- **Figure:** `/federal-finance/overview`: "Contributions", "Total Amount", Schedule B / E counts and amounts, date
  ranges; `/federal-finance/money-flow` "Money In (A)", "Money Out (B)", "Outside Spending (E)".
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), SUM(contribution_receipt_amount), MIN(contribution_receipt_date), MAX(contribution_receipt_date)
  FROM fec_schedule_a_contributions WHERE two_year_transaction_period = <cycle>;
  SELECT COUNT(*), SUM(disbursement_amount), MIN(disbursement_date), MAX(disbursement_date)
  FROM fec_schedule_b_disbursements WHERE two_year_transaction_period = <cycle>;
  SELECT COUNT(*), SUM(expenditure_amount), MIN(expenditure_date), MAX(expenditure_date)
  FROM fec_schedule_e_independent_expenditures WHERE cycle = <cycle>;
  ```
- **Tolerance:** counts exact, sums to the cent; if the page drops memo rows or superseded amendments, rerun with
  the matching filter (`memo_text`, `amendment_indicator`, `most_recent`) and name it.
- **Sample rule:** every headline card on the 11 pages (census), then 10 table rows top / middle / bottom.
- **Steps:**
  1. Open `/federal-finance/overview`; note the cards and the cycle.
  2. Run the three queries.
- **Pass:** every card matches.
- **Fail:** any unexplained difference.

### DI-017 — Federal candidate and committee totals

- **Class:** C-10
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 6; reconstructed 2026-11-08 — report lost; under the FEC rule candidates 2 / 10,
  committees 2 / 10 (raw 10 / 10); worst FRIENDS OF JUNAID (C00906891) $14,054,760.00 itemized (817 rows) vs
  $927,180.00 / 590 rows under the rule vs $1,650,112.48 reported; raw sums exceed the FEC-reported receipts for 19 of
  102 candidates, the rule's for none)
- **Amended 2026-10-30 (batch 6, E32; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Recompute under DI-016's FEC rule. Never print a `donor_entity_key` (federal donors are individuals).
- **Figure:** `/federal-finance/<candidate_id>` "Contributions", "Donors", "Total Amount";
  `/federal-finance/committees/<committee_id>/receipts` "Itemized Receipts (Schedule A)" count and total.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*) AS n, COUNT(DISTINCT donor_entity_key) AS donors, SUM(contribution_receipt_amount) AS total
  FROM fec_schedule_a_contributions WHERE candidate_id = '<candidate_id>' AND two_year_transaction_period = <cycle>;
  SELECT COUNT(*) AS n, SUM(contribution_receipt_amount) AS total
  FROM fec_schedule_a_contributions WHERE committee_id = '<committee_id>' AND two_year_transaction_period = <cycle>;
  ```
  "Reported Receipts" comes from `fec_candidate_cycle_totals.receipts` for the candidate and cycle (the FEC's own
  total, not the itemized sum).
- **Tolerance:** to the cent; donor count exact.
- **Sample rule:** n = 30: 10 candidates, 10 committees, 10 races (DI-018), top / middle / small.
- **Steps:**
  1. Open `/federal-finance/S6IL00482` and `/federal-finance/committees/C00904466/receipts`.
  2. Note the figures.
  3. Run the queries.
- **Pass:** every figure matches.
- **Fail:** any unexplained difference.

### DI-018 — Federal race outside spending

- **Class:** C-10
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 6; reconstructed 2026-11-08 — report lost; 9 / 10 races: the Senate race
  (`/federal-finance/races/S/00/outside-spending`) shows $6,099,849.42 / 10 rows vs $4,733,531.42 / 8 — two superseded
  24-hour reports add $1,366,318.00; only 9 races have Schedule E in the window, plus IL-13 at zero; every race page
  applies the MT-015 date window)
- **Amended 2026-10-30 (batch 6, E26; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Scope by the race's candidate ids, as the page does (the office / district columns disagree by one
  $5,000 row in two races); window by `expenditure_date` (bounded cycle), not `cycle`; keep `most_recent` rows only.
- **Figure:** `/federal-finance/races/<office_code>/<district_code>/outside-spending`: "Total Schedule E Amount",
  "Transaction Count", "Latest Expenditure Date", support / oppose split.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), SUM(expenditure_amount), MAX(expenditure_date),
         SUM(expenditure_amount) FILTER (WHERE support_oppose_indicator = 'S') AS support,
         SUM(expenditure_amount) FILTER (WHERE support_oppose_indicator = 'O') AS oppose
  FROM fec_schedule_e_independent_expenditures
  WHERE candidate_office = '<office_code>' AND candidate_office_state = 'IL'
    AND COALESCE(candidate_office_district,'') = '<district_code>' AND cycle = <cycle>;
  ```
- **Tolerance:** count exact, sums to the cent; name any memo / amendment filter the page applies.
- **Sample rule:** 10 races (part of DI-017's 30), top / middle / small by Schedule E total.
- **Steps:**
  1. Open `/federal-finance/races/H/07/outside-spending`.
  2. Note the cards.
  3. Run the query.
- **Pass:** every card matches.
- **Fail:** any unexplained difference.

### DI-019 — Lobbying clients per entity and year

- **Class:** C-11
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; entity "Clients (N)" and client
  "Represented By (N)" count (member, `reg_year`) rows of `lobbying_entity_clients`, not members: 6 / 15 entities + 6
  / 15 clients pass; site-wide 481 / 654 entities and 2,496 / 3,312 clients over-counted (Taft, entity 7036: 321 shown
  for 105 clients); the list's Clients column passes a census 2,231 / 2,231; the list header "N lobbying entities
  registered" counts `lobbying_entities.reg_year`, a loader first-seen year (409 in cycle:2026 vs 477 entities with
  client registrations / 1,346 with lobbyist registrations in 2025–26; all time 1,822 passes); the entity "Matched
  donors" badge fans out on 285 / 356 entities. Lobbying extract 20260214, `reg_year` 2022..2026. Fixes F-B7e /
  F-B7f.)
- **Amended 2026-10-30 (batch 7, logged; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** The queries below count distinct members; the page's per-(member, `reg_year`) row count is the
  defect. The list header's population is `lobbying_entities.reg_year` (a loader first-seen year), not a registration
  year.
- **Figure:** `/lobbying/` entity rows (client count) and "N lobbying entities registered"; `/lobbying/<entity_id>`
  "Clients (N)"; `/lobbying/client/<client_id>` "Represented By (N)".
- **Independent recomputation:**
  ```sql
  SELECT COUNT(DISTINCT entity_id) FROM lobbying_entity_clients WHERE reg_year = <year>;
  SELECT COUNT(DISTINCT client_id) FROM lobbying_entity_clients WHERE entity_id = <entity_id> AND reg_year = <year>;
  SELECT COUNT(DISTINCT entity_id) FROM lobbying_entity_clients WHERE client_id = <client_id> AND reg_year = <year>;
  ```
- **Tolerance:** exact.
- **Sample rule:** n = 30, 15 entities and 15 clients, top / middle / small by client count.
- **Steps:**
  1. Open `/lobbying/7036` and `/lobbying/client/3821`; note the counts and the year the window shows.
  2. Run the queries for that year.
- **Pass:** all counts match.
- **Fail:** any mismatch.

### DI-020 — Lobbying matched campaign donors

- **Class:** C-11
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; 15 matched clients: Top Committees
  (agg) = picked contributions by the matched keys 70 / 70, but "Matched donor dollars" / "Contributions" pass 2 / 15
  and "This cycle" 8 / 15 — `_get_receipt_activity` selects condensed receipts by donor NAME across all parts (its key
  test compares a SHA-1 `_stable_entity_key` with the bulk donor key and never passes); the name path reproduces the
  page 15 / 15 at 1–11× the matched keys (median 4×); Ameren (client 3821) $7,515,121.12 vs $4,548,836.60 by its 43
  keys; flows all time 304 / 304, cycle 108 / 108 vs all parts but 78 / 108 vs contributions; flows group by
  `client_name` (8 names span > 1 `client_id`). Fixes F-B7g / F-B7h.)
- **Amended 2026-10-30 (batch 7, E28; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Recompute over **picked receipts** by the matched `donor_key`s and the page's window;
  `analytics_donor_committee_agg` is the diagnosis step only.
- **Figure:** `/lobbying/client/<client_id>` "Matched Campaign Donors" (donor rows with amounts) and "Money
  Destinations".
- **Independent recomputation:**
  ```sql
  SELECT m.donor_key, SUM(r.amount) AS total, COUNT(DISTINCT r.committee_id) AS committees
  FROM lobbying_donor_matches m
  JOIN isbe_receipts r
    ON LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,''))) = m.donor_key
  WHERE m.client_id = <client_id>
    AND r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
  GROUP BY 1 ORDER BY total DESC;
  ```
  Slow (no index on the expression); run it for one client at a time.
- **Tolerance:** to the cent per matched donor; the match set itself (which donors are matched, score ≥ 0.80) is a
  matching-quality question for a separate review, not this check.
- **Sample rule:** the 15 clients of DI-019.
- **Steps:**
  1. Open `/lobbying/client/3821`; note the matched donors' amounts (by rank, not by name).
  2. Run the query.
- **Pass:** amounts match per row.
- **Fail:** any unexplained difference.

### DI-021 — 527 organization totals

- **Class:** C-12
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; 30 orgs all time (frame 89 EINs with
  a page and contributions): raw 30 / 30 on every figure, under the amendment rule 17 / 30 (13 fail; overstatement
  $53,406,077 contributions / $46,299,356 expenditures, e.g. EIN 261725187 +$32.9M / +$24.8M); "This cycle" unusable
  (A11 → P-1); minor: "Top Expenditure Recipients (N records, $X)" counts amount ≤ 0 rows but sums only > 0 (3 orgs).
  Local 527 data ends 2025-12-31. Fixes F-B7a, F-B7d.)
- **Amended 2026-10-30 (batch 7, E27 / E33 of batch 7; the amended text was lost with the report; rule as logged,
  reconstructed 2026-11-08):** Count one live Form 8872 per (ein, period): the latest `insert_datetime` NULLS LAST,
  then the highest `form_id` (141 superseded forms over 41 EINs locally; the loader drops the IRS amended indicator).
  Count each source row once; measure identical rows (2,998,806 contribution rows identical within a form,
  unmeasurable until P-1 loads the real date; 310 identical expenditure rows).
- **Figure:** `/527/<ein>` "Financial Summary", "Top Contributors (N records, N total)", "Top Expenditure Recipients
  (N records, N total)"; `/527/<ein>/contributors` and `/527/<ein>/recipients` totals.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), SUM(amount) FROM irs527_contributions WHERE ein = '<ein>';
  SELECT COUNT(*), SUM(amount) FROM irs527_expenditures WHERE ein = '<ein>';
  ```
  All-time window; for a windowed figure, filter `date` with the same text-date rule as
  `webapp/utils/text_dates.py` `text_date_window_clause`.
- **Tolerance:** to the cent; if the page de-duplicates rows repeated across amended reports (`form_id`), name the
  rule and rerun with it.
- **Sample rule:** n = 30 orgs, 10 top / 10 middle / 10 small by contributions.
- **Steps:**
  1. Open `/527/813554739`; note the summary and the two "N records, N total" headings.
  2. Run the queries.
- **Pass:** counts and totals match.
- **Fail:** any unexplained difference.

### DI-022 — 527 dark-money totals

- **Class:** C-12
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; all-time funding sources reproduce
  ($10,938,950,394 / 8,973,118 / 1,052,915) but include the superseded forms' $55.0M and 567 page-less EINs (DI-055);
  cycle view = A11; "(N matches)" counts match rows: 954 rendered in cycle:2026 vs 113 distinct targets, 11,189 vs
  3,512 all time (DI-054); the page has no money sum; `/527/` list census 1,896 orgs both views, cycle expenditures
  $13,783,754 (includes 30 superseded rows / $48,501, EIN 363954686), all-time report totals $317,870,040 /
  $278,641,598 include superseded forms. Fixes F-B7a–d.)
- **Amended 2026-10-30 (batch 7, E27; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Join `irs527_expenditure_recipient_matches` through EXISTS (one row per IL expenditure row, not per
  recipient; `created_at` NULL, RF-016); apply DI-021's amendment rule.
- **Figure:** `/527/dark-money` "Total Contributions", "Total Expenditures", "527 Funding Sources"; `/527/` list
  totals.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), SUM(amount) FROM irs527_contributions;
  SELECT COUNT(*), SUM(amount) FROM irs527_expenditures;
  ```
  with the page's window applied to the text `date` column as in DI-021, and the matched-expenditure total from
  `irs527_expenditure_recipient_matches` joined to `irs527_expenditures` (read the join columns from
  `database/schema.sql` first).
- **Tolerance:** to the cent.
- **Sample rule:** census of the summary figures; 10 matched-expenditure rows top / middle / small.
- **Steps:**
  1. Open `/527/dark-money` and note the window and figures.
  2. Run the queries.
- **Pass:** every figure matches.
- **Fail:** any unexplained difference.

### DI-023 — OpenBook vendor figures

- **Class:** C-13
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost; 555 listed vendors: list rows 555 /
  555 in both windows (seed fan-out does not double-count); detail pages 62 / 68. D1 defect (DI-056): `vendor_detail`
  sums the LIMIT-250 display rows — COOK COUNTY 250 / $10,628,707,662.50 vs 2,834 / $26,307,070,682.03; AMEREN
  ILLINOIS 250 / $52.45M vs 1,012 / $110.91M; also AMEREN ILLINOIS CO / COMPANY, ABBOTT, CROWE. D2 defect (DI-057):
  text M/D/YY dates compared as ISO → every windowed warrant figure 0 (4 vendors: 0 vs 722 rows in cycle:2026, 762 in
  cycle:2024). D3 source data: all 58 `openbook_contributions_raw` rows are header rows ('DATE', NULL amount) →
  "Contributions 1, $0.00" on 44 vendors. D4 presentation: NULL award totals sort first (first contract-bearing row
  497 / 555); list header counts 14 unlisted keys (8,930 / $30,118,894,097.55 vs 8,781 / $30,079,611,140.09 listed);
  Contracts card has no scope tag. Fixes F-B8a–d.)
- **Amended 2026-10-30 (batch 8, E29; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Recompute through `openbook_vendor_seed → openbook_vendor_match` (a seed with several matches must
  not double-count); contracts are not windowed; warrants / contributions window on `to_date(<text>, 'MM/DD/YY')`.
- **Figure:** `/openbook/<vendor_key>` contract count and award total, contribution count and total; the vendor's
  row on `/openbook/`.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), COUNT(DISTINCT contract_number), SUM(award_amount) FROM openbook_contracts_raw WHERE openbook_vendor_key = '<vendor_key>';
  SELECT COUNT(*), SUM(amount) FROM openbook_contributions_raw WHERE openbook_vendor_key = '<vendor_key>';
  ```
- **Tolerance:** to the cent; if the page counts distinct contracts, compare with the distinct count.
- **Sample rule:** n = 30 vendors, or all if fewer, top / middle / small by award total.
- **Steps:**
  1. Open `/openbook/AMEREN%20ILLINOIS`; note the figures.
  2. Run the queries with the decoded key.
- **Pass:** every figure matches.
- **Fail:** any unexplained difference.

### DI-024 — Tool figures equal profile figures

- **Class:** C-14
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost; 29 results: donor rows 15 / 15
  (search Total = donor page = DI-035 query; I2 not observed locally); candidate rows 0 / 14 — the row sums
  `bulk_candidate_committee_finance_agg.sum_total_receipts` (sworn D-2), re-cast and summed in REAL, under an "All
  time" tag while the link carries `?period=cycle:2026` (candidate 39266 $378,609,220.00 vs profile itemized
  $452,454,964.70; −$199.05 vs the numeric agg sum); committee counts 14 / 14; person-intelligence and investigate
  show no figure next to a link (no members); federal contributor rows sum raw Schedule A by name (DI-052 family). Fix
  F-B8f.)
- **Amended 2026-10-30 (batch 8, E29; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** For each tool figure, state whether the tool's query and the profile's query share the definition
  (window, predicate, picker); a difference under the same definition is a defect, under a different definition a
  known semantic with a label / link fix. Search terms naming individuals are recorded as ids / md5 prefixes only.
- **Figure:** any total shown by `/search`, `/person-intelligence` or `/investigate` next to a link to a profile.
- **Independent recomputation:** follow the link and run the profile's own check (DI-001 for donors, DI-008 for
  committees, DI-004 for candidates, DI-017 for federal candidates) for the same window.
- **Tolerance:** to the cent, same window.
- **Sample rule:** n = 30: 10 searches (5 candidate or committee names, 5 common surnames) × the top 3 results.
- **Steps:**
  1. Open `/search?q=frerichs`; note each result's figure and window.
  2. Open the result's profile in the same window; compare; run the profile's check if not already passed.
- **Pass:** tool and profile agree and the profile check passes.
- **Fail:** tool and profile disagree, or the profile check fails.

### DI-025 — Live feed latest dates

- **Class:** C-14
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost; local 2026-09-30, Schedule A
  2026-07-01, B 2025-12-31, E 2026-02-10; coverage 2026-08-27; first 10 rows per table 40 / 40. Known semantic F-B8g:
  the local feed lists one row per linked candidate — 4,765 committees linked to > 1 candidate, 90,594 picked
  contributions → 177,768 rows; 14 of the 75 shown repeat.)
- **Figure:** `/live-feed` "Local feed latest date" and "Federal feed latest date", and the first rows of each
  feed table.
- **Independent recomputation:**
  ```sql
  SELECT MAX(received_date) FROM isbe_receipts WHERE archived = FALSE AND amount > 0;
  SELECT MAX(contribution_receipt_date) FROM fec_schedule_a_contributions;
  SELECT MAX(disbursement_date) FROM fec_schedule_b_disbursements;
  SELECT MAX(expenditure_date) FROM fec_schedule_e_independent_expenditures;
  ```
- **Tolerance:** exact date; a later local date on the page than in `isbe_receipts` is a fail (future-dated rows
  must be named).
- **Sample rule:** census (four dates), plus the first 10 rows of each table checked by date and amount.
- **Steps:**
  1. Open `/live-feed`; note the dates.
  2. Run the queries.
- **Pass:** all dates match.
- **Fail:** any mismatch.

### DI-026 — Legacy pages and v1 API

- **Class:** C-15
- **Created:** 2026-10-29
- **Status:** fail (2026-11-08, batch 9 report). Census of the frozen legacy tables: every count and total on
  `/legacy`, `/api/stats`, `/reports/`, `/api/committees`, `/api/reports` matches, and 30 / 30
  `/committees/legacy/<id>` + `/api/committees/<id>`, 10 / 10 `/reports/<id>` + `/api/reports/<id>`, 5 / 5
  `/api/donors/<id>` match — except the donors figure: the `/legacy` Donors card, `/api/stats` `donors` and the
  `/api/donors` list total read the ISBE donor directory (251,347 rows, `id` null in the list) instead of the legacy
  `donors` table (561). Fixes F-B9e (card and stats) and F-B9f (list) proposed.
- **Figure:** `/legacy` counts, `/api/stats` counts and totals, `/reports/` count, `/committees/legacy/<id>` "Total
  Contributions", `/api/committees/<id>` `total_contributions` / `report_count`, `/api/donors[/<id>]`,
  `/api/reports[/<id>]`.
- **Independent recomputation:**
  ```sql
  SELECT (SELECT COUNT(*) FROM committees) AS committees, (SELECT COUNT(*) FROM reports) AS reports,
         (SELECT COUNT(*) FROM contributions) AS contributions, (SELECT COUNT(*) FROM donors) AS donors,
         (SELECT SUM(amount) FROM contributions) AS total,
         (SELECT COUNT(*) FROM reports WHERE is_paper_filed) AS paper_filed;
  SELECT scrape_status, COUNT(*) FROM reports GROUP BY 1;
  -- per committee (page and /api/committees/<id>)
  SELECT COALESCE(SUM(c.amount), 0), (SELECT COUNT(*) FROM reports WHERE committee_id = <id>)
  FROM contributions c JOIN reports p ON p.id = c.report_id WHERE p.committee_id = <id>;
  -- per report / per donor
  SELECT COUNT(*), SUM(amount) FROM contributions WHERE report_id = <report_id>;
  SELECT COUNT(*), SUM(amount) FROM contributions WHERE donor_id = <donor_id>;
  ```
  `reports.filed_date` is text (`M/D/YYYY h:mm:ss PM` plus a note); all 650 fall in 2026-01..02, so `/reports/` shows
  650 under the default cycle and all time.
- **Tolerance:** exact (the API's float total `5173230.590000001` counts as equal).
- **Sample rule:** census of the counts; 30 legacy committee pages drawn 10 / 10 / 10 from the top, middle and small
  end of the committees with contributions; 10 reports the same way; 5 legacy donors.
- **Steps:**
  1. Open `/legacy` and `/api/stats`; note the counts. Donors must read 561.
  2. Open `/committees/legacy/1` and `/api/committees/1`; note the totals.
  3. Run the queries.
- **Pass:** every figure matches.
- **Fail:** any mismatch.

### DI-027 — Analytics JSON payloads

- **Class:** C-16
- **Created:** 2026-10-29
- **Status:** fail (2026-11-08, 25 / 30 values; batch 9 report). Payload = page widget (template context) on 14 / 14
  endpoints, with two notes: the reconciliation rows have no sort tiebreak (two tied pairs came back swapped,
  F-B9c), and the bare `committee-similarity` (3 / $10,000) and `candidate-competition` ($2,500) defaults differ
  from the relationships page (2 / $5,000 and $5,000; F-B9d) — called with the page's parameters they are equal.
  Failing values: NLP "uncategorized" and NLP total (DI-058, F-B9a); network committee 32762 → candidate 39266
  $378,380,000.00 vs $378,380,010.09 (DI-046, F-B5a); candidate-competition state node 39266 $440,055,500.00 vs
  $440,155,500.00 (DI-060, F-B9b); 527 ecosystem default window empty (DI-049, F-B5b). Passing: time series ×3,
  geo ×3, anomalies ×2, concentration ×2, reconciliation ×2, network donor edge, co-giving ×2, similarity ×2,
  federal competition node, lobbying ×2, 527 all-time edge, state focus-sankey ×2, federal focus-sankey ×2.
- **Figure:** values in the 14 `/api/analytics/*` and `/api/federal/network/focus-sankey` payloads.
- **Independent recomputation:** first the page widget: request the page and read the same object from its template
  context (endpoint → widget: `time-series`, `geo` → `/analytics/geography`; `anomalies`, `reconciliation` →
  `/analytics/risk`; `concentration`, `nlp` → `/analytics/donors`; `network` → `/analytics/networks`;
  `donor-cogiving`, `committee-similarity`, `candidate-competition`, `lobbying-influence`, `irs527-ecosystem` →
  `/analytics/relationships`; the focus-sankeys render their JSON as-is). Then the value from source with the
  Conventions, for example the window's monthly contributions:
  ```sql
  -- <picked receipts CTE from the Conventions, all committees>
  SELECT to_char(pr.received_date, 'YYYY-MM'), SUM(pr.amount), COUNT(*) FROM pr
  WHERE (COALESCE(pr.d2_part,'') = '' OR pr.d2_part LIKE '1%')
    AND pr.received_date BETWEEN '<start>' AND '<end>' GROUP BY 1 ORDER BY 1;
  ```
  Geo cities: the page's city key lowercases and strips punctuation (`Chicago,` = `Chicago`). Graph node totals
  (co-giving, similarity) are all time over the site donor key; competition nodes are restricted to the top-2000
  donor pool (see DI-060); federal values exclude nothing but must be checked for `memo_code = 'X'` in
  `raw_extractions` (DI-052).
- **Tolerance:** to the cent; float noise ≤ $0.000001 ignored.
- **Sample rule:** n = 30 values across the 14 endpoints, at least one each; the batch 9 report lists them (V01–V30).
- **Steps:**
  1. Open `/api/analytics/time-series` and `/analytics/geography` with the same window; compare one month.
  2. Run the query above for that month.
- **Pass:** payload, widget and recomputation agree.
- **Fail:** any disagreement with no named cause.

### DI-028 — Viz Lab race-money value

- **Class:** C-17
- **Created:** 2026-10-29
- **Status:** pass (2026-11-08, 10 / 10 races; batch 9 report). The prototype's 80 rows equal their
  `/analytics/state-races/` rows (total, outside spending, donors, candidates). Ten races (ranks 1–4, 40–42, 78–80)
  match the source recomputation to the cent; outside spending matches after DI-005's name rule (Representative 7:
  4 rows name another first name; Representative 29: 3 rows do). Triple-pipeline JSON is a dated artifact
  (2026-02-16, gitignored, main tree only), not compared; geometry keys pass `scripts/validate_geometry_keys.py`.
- **Figure:** `/experimental/viz-lab/race_money_pressure` "Race Money vs Outside Pressure" (data endpoint
  `/experimental/viz-lab/data/race_money_pressure`): a race's total and outside spending.
- **Independent recomputation:** race committees from the cycle's candidacies, race identity by the site's
  `_state_race_slug(office, district_type, district)`:
  ```sql
  SELECT DISTINCT ca.office, ca.district_type, ca.district, ca.id, ca.last_name, cc.committee_id
  FROM isbe_candidacies cy JOIN isbe_candidates ca ON ca.id = cy.candidate_id
  LEFT JOIN isbe_candidate_committees cc ON cc.candidate_id = ca.id
  WHERE cy.election_year + cy.election_year % 2 = 2026;
  -- then, over the race's distinct committees, with the Conventions' picked receipts (all parts):
  SELECT SUM(pr.amount) FROM pr WHERE pr.committee_id = ANY(<race committees>)
    AND pr.received_date BETWEEN '2025-01-01' AND '2026-12-31';
  ```
  Outside spending: DI-005's query per candidate surname over the window, then DI-005's name rule.
- **Tolerance:** to the cent.
- **Sample rule:** n = 10 races, top 4 / middle 3 / bottom 3 of the prototype's 80 (local only; smoke-level).
- **Steps:**
  1. Start a local server with `EXPERIMENTAL_VIZ_LAB_ENABLED=1` (port 5068), open
     `/experimental/viz-lab/race_money_pressure`; stop the server afterwards.
  2. Note a race's values; run the queries.
- **Pass:** values match.
- **Fail:** any mismatch.

### DI-029 — Home stat cards

- **Class:** S-01
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 4; reconstructed 2026-11-08 — report lost; 17 / 22 cards. Fail: Matched Dark
  Money Expenditures (match-table JOIN fan-out — cycle $12,860,903 / 1,484 vs $700,843 / 122 distinct; all time
  $536,688,948 / 128,019 vs $32,515,961 / 9,955; F-B4a); Federal Independent Expenditures (cycle windowed by backfill
  `cycle`: 5,097 / $109,249,477.82 vs 75 / $10,453,445.89 by date; F-B4b); Director-Donor Overlaps (13,186 vs 10,212
  distinct; F-B4c); Total 527 Contributions Received cycle (A11 → P-1); Local Receipts (every live D-2 summed:
  $380,446,672.19 vs $380,253,064.35 one D-2 per period; DI-042, F-B4d). Loose labels (known semantic): Local
  Candidates 31,826 / Committees 24,895 count candidate-linked only; Archived Local Filings 132,807 counts archived
  D-2 reports; Federal Contributions total is Σ cycle totals $110,695,468.03, not Schedule A $119,657,997.47. Test
  client with route cache off.)
- **Amended 2026-10-30 (batch 4, E21; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Each card's query is written from the builder's population definition over source tables (picked
  receipts / one D-2 per period for ISBE; `fec_*`; 527 / lobbying base tables), matviews only for diagnosis. The 22
  per-card queries written on 2026-10-30 are lost; rewrite them at re-run (cards and axes:
  `tests/scope_widgets_registry.py`).
- **Figure:** `/` stat cards: "Local Candidates", "Local Committees", "Local Receipts", "Federal Contributions",
  "527 Organizations", "Total 527 Contributions Received", "Lobbying Entities", "Archived Local Filings" and the
  rest of the 22 registry rows for `main.index`.
- **Independent recomputation:** one query per card, for example
  ```sql
  SELECT COUNT(*), SUM(amount) FROM isbe_receipts WHERE archived = FALSE AND amount > 0 AND received_date BETWEEN '2025-01-01' AND '2026-12-31';
  SELECT COUNT(*) FROM isbe_filed_docs WHERE archived = TRUE;
  SELECT COUNT(DISTINCT ein) FROM irs527_organizations;
  SELECT COUNT(*) FROM lobbying_entities;
  ```
  Before the batch, read each card's builder in `webapp/routes/main.py` to fix the population it counts, then
  write that card's query into this check.
- **Tolerance:** counts exact, sums to the cent.
- **Sample rule:** census of the cards.
- **Steps:**
  1. Open `/`; note every card and the window.
  2. Run each card's query.
- **Pass:** every card matches.
- **Fail:** any card off with no named cause.

### DI-030 — Home Top Donors

- **Class:** S-01
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 4; reconstructed 2026-11-08 — report lost; top 5 of each view 10 / 10; the
  all-time number one now includes its $65,000,000 no-live-D-2 row — fix F1 confirmed locally; prod not re-checked)
- **Earlier:** fail (2026-10-29, mechanism I2; see batch 1 report). Default 2026 cycle passes 5 / 5; all time
  (`/?period=all`) fails 2 / 5, each gap equal to the donor's rows on filings with no live D-2 (one $65,000,000 row;
  two rows totalling $1,990,754.92). Fix F1 proposed to terminal 1.
- **Figure:** the "Top Donors" total and count on `/` (default 2026 cycle) and on `/?period=all`.
- **Independent recomputation:** the DI-035 query for each row's `<key>`. Compare with `live_cycle` for the default
  view and `live_all_time` for `period=all`. The count is `COUNT(*)` under the same WHERE clause.
- **Tolerance:** ± $0.01; counts exact.
- **Sample rule:** manual run: the top 3 rows of each view. Batch: the top 5 of each view (batch 1 measured 5 + 5).
- **Steps:**
  1. Open both views; click each of the top 3 names to get `<key>` from the URL.
  2. Run the DI-035 query per key; compare.
- **Pass:** all match.
- **Fail:** an all-time gap equal to `on_no_d2_filings` is I2. Record any other gap as new.

### DI-031 — Committee directory windowed receipts

- **Class:** S-02
- **Created:** 2026-10-29; recomputation amended 2026-10-30 (batch 3, E18)
- **Status:** pass (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 30 / 30 rows, plus a census of all
  2,898 committees with picked receipts in cycle:2026: 0 differ, net $0.00)
- **Amended 2026-10-30 (batch 3, E18; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** DI-008's amended recomputation (picked receipts, every part) for that committee and window. Latent
  F-B3d: the directory CTE lacks `amount > 0` (changes no local figure).
- **Figure:** `/committees/` a committee row's receipts in the default window.
- **Independent recomputation:** DI-008's query for that committee and window.
- **Tolerance:** to the cent.
- **Sample rule:** n = 30 rows, top / middle / small.
- **Steps:**
  1. Open `/committees/`; note sampled rows.
  2. Run DI-008's query per row.
- **Pass:** every row matches.
- **Fail:** any mismatch.

### DI-032 — Candidate directory stat cards

- **Class:** S-03
- **Created:** 2026-10-29
- **Status:** fail (2026-10-30, batch 4; reconstructed 2026-11-08 — report lost; 5 / 7: Total Receipts $380,446,672.19
  vs $380,253,064.35 and Total Expenditures $319,216,424.15 vs $319,049,087.19 under one live D-2 per (committee,
  period) — DI-042, F-B4d; Candidates, Committees, Federal Candidates, Contributions, Matched Donors pass)
- **Amended 2026-10-30 (batch 4, E21 / E22; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** D-2 total cards are judged with the picker (one live D-2 per committee × period, the condensed
  picker's order). The per-card queries of 2026-10-30 are lost; rewrite them at re-run.
- **Figure:** `/candidates` "Candidates", "Committees", "Total Receipts", "Total Expenditures" (and the other
  registry rows for `main.candidates`).
- **Independent recomputation:** read the card builder (`_get_candidate_stats_cached`, `webapp/routes/main.py`),
  then recompute each card from `isbe_candidacies`, `isbe_candidate_committees`, `isbe_d2_reports` /
  `isbe_receipts` for the window; write the queries into this check.
- **Tolerance:** counts exact, sums to the cent.
- **Sample rule:** census of the cards.
- **Steps:**
  1. Open `/candidates`; note the cards.
  2. Run the queries.
- **Pass:** every card matches.
- **Fail:** any mismatch.

### DI-033 — Compare stat cards

- **Class:** S-04
- **Created:** 2026-10-29
- **Status:** pass (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost; 20 / 20 cards over 10 pairs from the
  batch 2 / 3 samples incl. ids 299, 245, 313; each card = its same-definition profile figure and the picked-receipts
  SQL — Pritzker 39266 $442,361,141.41 / 437 / 323; Democratic Party of Illinois 6239 $26,326,584.42 / 2,820. Known
  semantics (label fixes F-B8e): compare is all time with no scope bar; the committee "Contributions" card is part-1
  rows (5,915) vs the profile's all parts (11,409); "Donor Counterparties" = Unique Donors; option order by the REAL
  agg (DI-012, ordering only).)
- **Amended 2026-10-30 (batch 8, logged; the amended text was lost with the report; rule as logged, reconstructed
  2026-11-08):** Window: all time (`/compare` has no scope bar); read "the same window" below as all time.
- **Figure:** `/compare` profile stat cards for two candidates or committees.
- **Independent recomputation:** DI-004 / DI-008 for each entity in the same window.
- **Tolerance:** to the cent.
- **Sample rule:** n = 10 comparisons drawn from the C-02 and C-04 samples.
- **Steps:**
  1. Open `/compare`, choose two sampled entities.
  2. Compare each card with the entity's profile and its check.
- **Pass:** every card matches.
- **Fail:** any mismatch.

### DI-034 — Admin federal audit pages

- **Class:** admin (non-page endpoints)
- **Created:** 2026-10-29
- **Status:** blocked (2026-11-08, batch 9 report). Both pages are behind `login_required` (`webapp/auth.py`:
  `session["manual_entry_user_id"]` plus an active `app_users` row; `/auth/login` checks the password with
  `AppUser.verify_credentials`). The local `app_users` table has one active user and no credential for it was
  available to the batch; per decision E31 no password was guessed, no session was forged and prod was not read.
  Needs Devin to run the steps below.
- **Figure:** `/admin/federal-receipt-audit` and `/admin/federal-disbursement-audit` (default cycle 2026, status
  `flagged`, `min_abs_diff` 1000, `tolerance` 0.01): status counts and per-candidate reported vs schedule totals.
- **Independent recomputation** (the pages' rule in `database/federal_fec.py`, per candidate and cycle):
  ```sql
  SELECT t.candidate_id, t.receipts AS reported,
         (SELECT COALESCE(SUM(a.contribution_receipt_amount), 0) FROM fec_schedule_a_contributions a
          WHERE a.candidate_id = t.candidate_id AND a.cycle = t.cycle) AS schedule_total
  FROM fec_candidate_cycle_totals t WHERE t.cycle = 2026 AND t.candidate_id = '<candidate_id>';
  SELECT t.candidate_id, t.disbursements AS reported,
         (SELECT COALESCE(SUM(b.disbursement_amount), 0) FROM fec_schedule_b_disbursements b
          WHERE b.candidate_id = t.candidate_id AND b.cycle = t.cycle) AS schedule_total
  FROM fec_candidate_cycle_totals t WHERE t.cycle = 2026 AND t.candidate_id = '<candidate_id>';
  ```
  The schedule sums include memo and superseded rows (DI-052); record how many of the flagged rows that explains.
- **Tolerance:** to the cent.
- **Sample rule:** census of the status counts; 10 rows each.
- **Steps (local only):**
  1. `python run.py create-user` (or use the existing local account), then `python run.py runserver --port 5068`.
  2. Sign in at `http://127.0.0.1:5068/auth/login`.
  3. Open `/admin/federal-receipt-audit?cycle=2026&status=all` and `/admin/federal-disbursement-audit?cycle=2026&status=all`;
     note the status counts and the first 10 rows' reported and schedule totals.
  4. Run the queries for those candidates; stop the server.
- **Pass:** every figure matches.
- **Fail:** any mismatch.

### DI-035 — Donor page All time agrees with its own This cycle (the I2 check)

- **Class:** C-01
- **Created:** 2026-10-29 (batch 1, provisional id DI-B1-02)
- **Status:** fail (2026-10-29, mechanism I2; see batch 1 report). Example: the top committee donor on
  `/committees/6239/donors` (a $1.55M receipt filed on an A-1). Since 2025-01-01, $99.0M of $297.7M (33 %) of
  itemized contribution dollars sit on filings with no live D-2 and are missing from every all-time donor page.
  Fix F1 proposed to terminal 1.
- **Figure:** "Total Contributed (All time)", "This cycle" and the "Who They Donated To" list on
  `/donors/key/<donor_key>`.
- **Independent recomputation:**
  ```sql
  SELECT ROUND(SUM(r.amount)::numeric,2) AS live_all_time,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.received_date BETWEEN '2025-01-01' AND '2026-12-31'),0)::numeric,2) AS live_cycle,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE NOT EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = r.filed_doc_id AND d.archived = FALSE)),0)::numeric,2) AS on_no_d2_filings
  FROM isbe_condensed_receipts r
  WHERE md5(LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,'')))) = md5('<key>')
    AND r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%');
  ```
- **Tolerance:** ± $0.01.
- **Sample rule:** 5 keys per run, taken from the first rows of `/donors/` (2026 cycle) and from
  `/committees/6239/donors`.
- **Steps:** open the donor page (All time) and note the total, This cycle and the committee list; run the query;
  compare.
- **Pass:** All time = `live_all_time`; This cycle = `live_cycle`; the listed committees sum to All time.
- **Fail:** All time = `live_all_time − on_no_d2_filings` is I2. Any other gap is a new mechanism: record it.

### DI-036 — The all-time donor directory is fresh (the E check)

- **Class:** C-01
- **Created:** 2026-10-29 (batch 1, provisional id DI-B1-03)
- **Status:** fail (2026-10-29, local only, mechanism E; see batch 1 report). Locally `donor_entity_local` was
  built 2026-02-13, eight months before the analytics tables (2026-10-26); 22,609 of 246,900 entities (9.2 %) store
  a total that differs from their members' current totals ($33.3M absolute gap). Prod unknown: run the first two
  queries on prod. Fix F6 proposed to terminal 1.
- **Figure:** the total on the first 10 rows of `/donors/?period=all`, and the age of the table they come from.
- **Independent recomputation:**
  ```sql
  SELECT MAX(updated_at) AS entities_built FROM donor_entity_local WHERE source = 'bulk_receipts';
  SELECT MAX(updated_at) AS analytics_built FROM analytics_donor_summary WHERE source = 'bulk_receipts';
  SELECT e.entity_id, e.total_amount AS shown,
         (SELECT SUM(s.total_amount) FROM donor_entity_local_member m JOIN analytics_donor_summary s
            ON s.source = m.source AND s.donor_key = m.donor_key WHERE m.source = e.source AND m.entity_id = e.entity_id) AS current
  FROM donor_entity_local e WHERE e.source = 'bulk_receipts' ORDER BY e.total_amount DESC LIMIT 10;
  ```
- **Tolerance:** build dates within 1 day of each other; `shown` = `current` ± $0.01.
- **Sample rule:** the top 10 every run, because staleness concentrates in the largest, most active donors.
- **Steps:** run the three queries; open `/donors/?period=all` and check that the first rows' totals equal `shown`.
- **Pass:** the dates are within a day, and all 10 have `shown = current`.
- **Fail:** the entity table is older than the analytics tables, or any row differs. Record the dates and the
  number of differing rows.

### DI-037 — Network in-kind links land on a page showing the in-kind money (the I1 check)

- **Class:** C-01
- **Created:** 2026-10-29 (batch 1, provisional id DI-B1-04)
- **Status:** fail (2026-10-29, mechanism I1; see batch 1 report). 83 of 158 rendered in-kind links (52.5 %) fail:
  61 land on a 404 and 22 on a page without the clicked committee. Over all link pairs, 70.3 % of in-kind pairs
  (35,237 of 50,152; $337.9M) fail. Fixes F2a and F4 proposed to terminal 1.
- **Figure:** on `/candidates/<id>/network`, money-flow view, the "In-kind contributions" panel: each member's
  amount, and the page its link opens.
- **Independent recomputation:**
  ```sql
  SELECT md5(LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,'')))) AS key_md5,
         MIN(TRIM(COALESCE(r.first_name,'')||' '||COALESCE(r.last_name,''))) AS donor, COUNT(*) AS rows, ROUND(SUM(r.amount)::numeric,2) AS in_kind
  FROM isbe_condensed_receipts r
  WHERE r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>)
    AND r.archived = FALSE AND r.amount > 0 AND r.d2_part = '5A'
  GROUP BY 1 ORDER BY 4 DESC LIMIT 10;
  ```
- **Tolerance:** amounts ± $0.01. The landing page must list the candidate's committee.
- **Sample rule:** 2 candidates per run: 13505 (Frerichs) and one of the 25 with the most receipt rows. Check the
  3 largest in-kind members of each.
- **Steps:** open the Network tab; click the In-kind node; compare the members' amounts with the query; click each
  of the 3 largest members.
- **Pass:** the amounts match, and every link opens a page that lists the candidate's committee with the in-kind
  amount.
- **Fail:** the link 404s, or the page does not list the committee (I1). Record the candidate, the member and the
  outcome.

### DI-038 — Network 1A donor links land on a page listing the committee

- **Class:** C-01
- **Created:** 2026-10-29 (batch 1, provisional id DI-B1-05)
- **Status:** pass (2026-10-29, 917 / 917 rendered links: 385 individual, 532 organization; see batch 1 report).
  Over all link pairs 0.46 % of 1A pairs fail by I2, none of them in the rendered sample.
- **Figure:** members of the "Individual donors" and "Organization donors" panels, and their links.
- **Independent recomputation:** the DI-037 query with `r.d2_part = '1A'`. Then, on the landing page, the
  committee row's amount must be ≥ the panel amount.
- **Tolerance:** ± $0.01.
- **Sample rule:** the same 2 candidates as DI-037, 3 members from each panel.
- **Steps:** as DI-037.
- **Pass:** every link opens a 200 page that lists the committee.
- **Fail:** a 404 or a missing committee. Check the row's filing with the DI-035 query (I2 if it is on a no-D-2
  filing).

### DI-039 — Live receipts outside the condensed view are explained (the D4 detector)

- **Class:** C-02 / C-04 (shared: every page that reads `isbe_condensed_receipts`)
- **Created:** 2026-10-30 (batch 2)
- **Status:** pass (2026-10-30, local: 34,188 live raw rows / $34,906,534.41 absent from the view, 0.53 % of live raw
  dollars, all diagnosed; residual 0 rows; see batch 2 report)
- **Figure:** none on a page. This is the detector for the D4 class of defect (a picker that drops live receipts:
  the D4 bug dropped $38.7M and every "page = matview" comparison passed it).
- **Independent recomputation:** every live raw row the view lacks, diagnosed by the documented picker (the
  **picked receipts** rule of the Conventions, over all committees); first matching category wins:
  ```sql
  WITH last_end AS (
    SELECT committee_id, MAX(reporting_period_end) AS period_end FROM isbe_filed_docs
    WHERE doc_name NOT IN ('A-1', 'Statement of Organization', 'Letter/Correspondence', 'B-1', 'Nonparticipation')
    GROUP BY committee_id
  ), kept AS (
    SELECT DISTINCT ON (f.committee_id, f.reporting_period_begin, f.reporting_period_end) f.id
    FROM isbe_filed_docs f WHERE f.doc_name <> 'Pre-election'
    ORDER BY f.committee_id, f.reporting_period_begin, f.reporting_period_end,
             EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = f.id AND d.archived = FALSE) DESC,
             f.received_datetime DESC NULLS LAST, f.id DESC
  ), gap AS (
    SELECT r.committee_id, r.amount, f.doc_name, f.archived AS filing_archived,
           (r.filed_doc_id IN (SELECT id FROM kept)) AS filing_kept
    FROM isbe_receipts r JOIN isbe_filed_docs f ON f.id = r.filed_doc_id
    WHERE r.archived = FALSE AND r.amount > 0 AND r.received_date IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM isbe_condensed_receipts c WHERE c.id = r.id)
  )
  SELECT CASE WHEN doc_name = 'Pre-election' THEN 'c. Pre-election report (excluded by design)'
              WHEN doc_name IS NULL THEN 'c2. filing with NULL doc_name'
              WHEN filing_archived THEN 'a. filing archived'
              WHEN NOT filing_kept THEN 'b. superseded: picker kept another filing for the period'
              ELSE 'residual: undiagnosed' END AS category,
         COUNT(*) AS rows, COUNT(DISTINCT committee_id) AS committees, ROUND(SUM(amount)::numeric, 2) AS dollars
  FROM gap GROUP BY 1 ORDER BY 1;
  ```
  (runs in about 5 s locally). Measured 2026-10-30: (a) 23,556 rows / $25,074,895.00 / 104 committees; (b) 7,768 /
  $7,309,905.11 / 140; (c) 2,864 / $2,521,734.30 / 132; residual none.
- **Tolerance:** 0 residual rows.
- **Sample rule:** all committees (the query is the census).
- **Steps:** run the query after every ISBE import or matview DDL change. For a residual row, look at its filing and
  the filings of the same committee and period (the batch 2 report's `di004_docs.sql`).
- **Pass:** no `residual` row in the output.
- **Fail:** any residual: record its row count and dollars, and what it looks like (doc types, committees, date
  range). A residual means the matview keeps something other than what its DDL comment documents, or drops rows
  no documented category covers.
- **Worked examples** (source data, from batch 2's DI-004 sample): committee 665 (an archived A-1 whose rows are
  live; the same contributions are on the kept Semiannual), committee 16283 (two live amendments of 2010-01..06,
  both with live D-2s), committee 10552 (a 1996 Semiannual carrying 1998 receipts, superseded by an empty
  amendment).

### DI-040 — Filing detail renders the line items of every filing

- **Class:** C-05
- **Created:** 2026-10-30 (batch 3). Title: working title from the log's descriptor; the original heading is lost.
- **Status:** fail (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; 281,867 of 454,333 filings with line
  items render as empty: archived 262,693, Pre-election 18,954, live-not-kept 220; Fable's re-run identical (172,466
  shown). Fix F-B3a.)
- **Figure:** `/committees/filing/<filed_doc_id>`: the line items of a filing whose rows the condensed view does not
  keep.
- **Independent recomputation:** a census of filings with raw line items in `isbe_receipts` / `isbe_expenditures` by
  `filed_doc_id`, split by why the page shows none (filing archived / Pre-election / live but not the kept filing for
  its period). The 2026-10-30 SQL is lost — to be written at re-run.
- **Tolerance:** 0 filings with raw line items rendered empty without a note.
- **Sample rule:** census.
- **Steps:** open `/committees/filing/343531` (15 raw rows; the page was empty on 2026-10-30) and
  `/committees/filing/1015462` (100 rows / $1,314,306.65, shown); run the census.
- **Pass:** every filing with raw line items shows them, or says why they are not counted.
- **Fail:** any filing rendered empty with no note.

### DI-041 — D-2 reconciliation tables are current

- **Class:** C-05 (staleness evidence for RF-006)
- **Created:** 2026-10-30 (batch 3). Title: working title; the original heading is lost.
- **Status:** fail (2026-10-30, batch 3; reconstructed 2026-11-08 — report lost; local, `bulk_d2_receipts_recon` /
  `bulk_d2_expenditures_recon` built from the 2026-02-06 legacy input: latest filing 2026-02-05 vs 2026-09-30 in
  `isbe_filed_docs`; 10,437 D-2s without a row (9,819 live, 9,506 in cycle:2026); 763 archived flags changed since.
  Fixes F-B3b (rebuild from `isbe_*` in the chain), needs F-B3c. Prod not checked — decision for Devin.)
- **Figure:** none on a page; the freshness of the tables behind `/d2-reconciliation/` and
  `/d2-expenditures-reconciliation/`.
- **Independent recomputation:** latest filing date in the recon tables vs in `isbe_filed_docs`; count of live D-2s in
  `isbe_d2_reports` with no recon row; count of D-2s whose `archived` flag differs. The 2026-10-30 SQL is lost — to be
  written at re-run.
- **Tolerance:** the tables' latest filing within one import of `isbe_filed_docs`; 0 missing live D-2s.
- **Sample rule:** census.
- **Steps:** run the three measures; record dates and counts.
- **Pass:** no missing live D-2s and no stale flags.
- **Fail:** any missing live D-2 or a latest-filing gap beyond one import.

### DI-042 — One live D-2 per committee × period

- **Class:** S-01 / S-03 (the D-2 total cards; also used by DI-013's top-candidate share)
- **Created:** 2026-10-30 (batch 4, E22). Title: working title from the log ("one live D-2 per committee × period");
  the original heading is lost.
- **Status:** fail (2026-10-30, batch 4; reconstructed 2026-11-08 — report lost; cycle:2026: 42 extra live D-2s in 40
  (committee, period) groups on 38 committees — originals left live beside amendments; +$193,607.84 receipts /
  +$167,336.96 expenditures on the cards (≈ 0.05 %). Fix F-B4d in `_get_candidate_stats`.)
- **Figure:** none directly; the detector behind "Local Receipts" (home) and "Total Receipts" / "Total Expenditures"
  (`/candidates`).
- **Independent recomputation:** live `isbe_d2_reports` grouped by (committee, reporting period) with more than one
  row; dollars of the extras. SQL lost — to be written at re-run.
- **Tolerance:** 0 groups.
- **Sample rule:** census.
- **Steps:** run the grouping for the window.
- **Pass:** no (committee, period) has two live D-2s, or the card builder keeps one.
- **Fail:** a card sums more than one live D-2 for a period.

### DI-043 — Analytics overview headline cards

- **Class:** C-08 (`/analytics/overview`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title; the original heading is lost.
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; Trend Months 21; nodes / edges 547 /
  400)
- **Figure:** the overview's headline cards.
- **Independent recomputation:** written on 2026-10-30, lost with the report — to be written at re-run.
- **Tolerance / Sample rule:** census of the cards (as logged: card values above).
- **Pass / Fail:** every card equals its recomputation / any card off.

### DI-044 — Donor concentration metrics

- **Class:** C-08 (`/analytics/donors`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title (the brief named "Donor Concentration Metrics"); the
  original heading is lost.
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; 25 / 25 rows; every shown row is a
  single-donor committee — 361 committees sit at HHI 10,000; presentation fix F-B5d (floor / help line))
- **Figure:** "Donor Concentration Metrics" rows.
- **Independent recomputation:** lost with the report — to be written at re-run.
- **Sample rule:** the 25 shown rows.
- **Pass / Fail:** every row equals its recomputation / any row off.

### DI-045 — Geo drill-down header and top rows

- **Class:** C-08 (`/analytics/geo-drilldown`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title; the original heading is lost.
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; IL / Chicago: header 4 / 4, top-10
  rows 10 / 10 each; presentation fix F-B5e: the date line should print the capped window end)
- **Figure:** the drill-down header figures and top-10 rows.
- **Independent recomputation:** lost with the report — to be written at re-run.
- **Sample rule:** one state and one city (IL / Chicago), header + top 10.
- **Pass / Fail:** header and rows match / any mismatch.

### DI-046 — Analytics network graph node / edge figures

- **Class:** C-08 (`/analytics/networks`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title; the original heading is lost.
- **Status:** fail (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; 199 / 200 committee→candidate edges
  off: the builder sums the REAL agg column in single precision, and that half of the graph is all-time D-2 money
  under a "2026 Cycle" tag. Fable's re-run: edge committee:32762 → candidate:39266 weight 378,380,000.00 vs exact
  NUMERIC 378,380,219.05 ($219.05 gap — the report's "≤ $30.64" bound was understated for the top edge). Fix F-B5a.)
- **Figure:** node / edge counts and committee→candidate edge weights.
- **Independent recomputation:** edge weight = exact NUMERIC sum of the D-2 money for the window's cycles. SQL lost —
  to be written at re-run.
- **Tolerance:** to the cent, window-consistent tag.
- **Sample rule:** as logged, 200 committee→candidate edges.
- **Pass / Fail:** every edge to the cent under the shown window / any edge off or windowed differently from its tag.

### DI-047 — Analytics relationships top rows

- **Class:** C-08 (`/analytics/relationships`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title (the brief named "top relationship rows"); the original
  heading is lost.
- **Status:** proposed (written 2026-10-30 with its query, not run; the query was in the lost report — to be rewritten
  before the first run)
- **Figure:** top relationship rows.
- **Independent recomputation:** lost — to be written at re-run.
- **Pass / Fail:** to be written with the query.

### DI-048 — Risk and anomaly flags

- **Class:** C-08 (`/analytics/risk`)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title (the brief named "Risk and Anomaly Flags count by
  type"); the original heading is lost.
- **Status:** pass (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; 25 / 25 (ranks 15 / 16 a severity
  tie). Latent: only 22 of the window's 7,491 large rows can become flags because `analytics_large_contributions` is
  capped at 200 rows before windowing — fix F-B5c.)
- **Figure:** risk flags by type.
- **Independent recomputation:** lost with the report — to be written at re-run.
- **Sample rule:** the 25 shown flags.
- **Pass / Fail:** every flag reproduces / any flag off.

### DI-049 — 527 ecosystem graph

- **Class:** C-08 (the graph served by `/api/analytics/irs527-ecosystem`; the hosting page is not named in the log)
- **Created:** 2026-10-30 (batch 5, E24). Title: working title; the original heading is lost.
- **Status:** fail (2026-10-30, batch 5; reconstructed 2026-11-08 — report lost; empty in every window, 229 nodes all
  time: the parser expects YYYY-MM-DD, the stored `irs527_expenditures.date` is YYYYMMDD (35,235 of 35,914 rows, 0
  dashed); the default window should hold 1,136 rows / 13 orgs. Fable's re-run: 0 nodes default vs 229 with
  `?period=all`. Fix F-B5b.)
- **Figure:** node / edge counts of the 527 ecosystem graph in the default window.
- **Independent recomputation:** `irs527_expenditures` rows in the window with the YYYYMMDD date parsed. SQL lost — to
  be written at re-run.
- **Tolerance:** exact counts.
- **Sample rule:** default window and all time.
- **Pass / Fail:** graph rows = the windowed rows / an empty or mismatched graph.

### DI-050 — Federal matching cards

- **Class:** C-09 (`/federal-finance/matching`)
- **Created:** 2026-10-30 (batch 6). Title: working title; the original heading is lost.
- **Status:** proposed (written 2026-10-30, batch 6; the cards' arithmetic passes, the matched count was not
  recomputed; the match pages read `fec_local_donor_matches` + `analytics_donor_committee_agg`)
- **Figure:** the matching page's cards.
- **Independent recomputation:** lost with the report — to be written at re-run (recompute the matched count).
- **Pass / Fail:** to be written with the query.

### DI-051 — Federal Influence page degrees

- **Class:** C-09 (`/federal-finance/influence`)
- **Created:** 2026-10-30 (batch 6). Title: working title; the original heading is lost.
- **Status:** fail (2026-10-30, batch 6; reconstructed 2026-11-08 — report lost; Weighted Degree $0.00 / Degree 0 on
  all 50 rows — the page reads degrees from graph nodes, the builder stores them on centrality rows; Fable's re-run
  identical. Fix F-B6g.)
- **Figure:** Weighted Degree and Degree per row.
- **Independent recomputation:** the builder's centrality rows for the same graph. SQL lost — to be written at re-run.
- **Pass:** each row shows its centrality values. **Fail:** zeros where the builder has values.

### DI-052 — FEC memo and superseded rows detector

- **Class:** C-09 / C-10 (every Schedule A money figure)
- **Created:** 2026-10-30 (batch 6, E32). Title: working title (the log calls it the "memo + superseded detector");
  the original heading is lost.
- **Status:** fail (2026-10-30, batch 6; reconstructed 2026-11-08 — report lost; local cycle 2026: 25,140 Schedule A
  rows / $41,887,101.47 counted that the FEC's totals exclude = 35.0 % of the cycle's dollars (memo X 24,768 /
  $40,855,838.90; superseded 397 / $1,054,362.57; the two overlap — the components sum 25 rows / $23,100.00 above the
  union, overlap not described in the log). Local FEC data older than prod — prod re-check is a decision for Devin.
  Fixes F-B6a–d.)
- **Figure:** none on a page; the count and dollars of memo + superseded rows any Schedule A figure would include.
- **Independent recomputation:** Schedule A rows whose payload in `raw_extractions` has `memo_code = 'X'`, or that are
  not the highest `file_number` for their (committee, `transaction_id`, report). SQL lost — to be written at re-run.
- **Tolerance:** 0 rows counted by a page (after F-B6).
- **Sample rule:** census.
- **Pass:** no page money figure includes memo or superseded rows.
- **Fail:** any does.

### DI-053 — One live Form 8872 per EIN × period

- **Class:** C-12
- **Created:** 2026-10-30 (batch 7, E33 of batch 7). Title: working title from the log; the original heading is lost.
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; `irs527_reports` 1,739 forms / 1,598
  distinct (ein, period_start, period_end) → 141 superseded forms over 41 EINs; Schedule A $54,995,372 / Schedule B
  $51,041,990 on them. Fix F-B7a.)
- **Figure:** none directly; every 527 org / list / dark-money total built from reports.
- **Independent recomputation:** forms per (ein, period_start, period_end) beyond one; keep = latest `insert_datetime`
  NULLS LAST, then highest `form_id`. SQL lost — to be written at re-run.
- **Tolerance:** 0 superseded forms counted by a page.
- **Sample rule:** census.
- **Pass / Fail:** pages count one form per period / any page sums superseded forms.

### DI-054 — One 527 recipient-match row per target

- **Class:** C-12 (also feeds the S-01 Matched Dark Money card)
- **Created:** 2026-10-30 (batch 7). Title: working title from the log ("one match row per target"); the original
  heading is lost.
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost;
  `irs527_expenditure_recipient_matches` 11,189 rows over 3,512 targets = +7,677:
  `match_527_expenditures_to_committees` (`database/cross_matching.py` ≈ 1075) builds one left item per expenditure
  row; one target has 140 copies; `created_at` NULL (RF-016). Fix F-B7b.)
- **Figure:** "(N matches)" on `/527/dark-money` (954 rendered in cycle:2026 vs 113 distinct targets).
- **Independent recomputation:** match rows vs distinct targets. SQL lost — to be written at re-run.
- **Tolerance:** one row per target.
- **Sample rule:** census.
- **Pass / Fail:** rows = targets / any duplicate.

### DI-055 — 527 funding-source scope and label

- **Class:** C-12 (`/527/dark-money` "527 Funding Sources")
- **Created:** 2026-10-30 (batch 7). Title: working title from the log ("funding-source scope / label"); the original
  heading is lost.
- **Status:** fail (2026-10-30, batch 7; reconstructed 2026-11-08 — report lost; a label defect: the card, labelled
  "Illinois-connected 527 organizations", sums 567 of 656 contributing EINs that have no `irs527_organizations` row —
  8,788,640 rows / $10,619,402,839 = 97 % of contribution dollars — loaded by the `--illinois-only`
  include_contributions rule. Fix F-B7c; scope is a decision for Devin. Commit `80ebc3b` (after the batch) changed
  that loader rule; effect not measured.)
- **Figure:** "527 Funding Sources" and its label.
- **Independent recomputation:** contribution rows / dollars by whether the EIN has an `irs527_organizations` row. SQL
  lost — to be written at re-run.
- **Pass:** the card's population matches its label. **Fail:** it does not.

### DI-056 — Detail cards cover all rows

- **Class:** C-13 (`/openbook/<vendor_key>`)
- **Created:** 2026-10-30 (batch 8). Title verbatim from Fable's batch-8 M1 acceptance.
- **Status:** fail (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost; `vendor_detail` sums the LIMIT-250
  display rows, so 6 vendors show 250 contracts: COOK COUNTY 250 / $10,628,707,662.50 vs 2,834 / $26,307,070,682.03
  (Fable's re-run identical); AMEREN ILLINOIS 250 / $52.45M vs 1,012 / $110.91M; AMEREN ILLINOIS CO / COMPANY, ABBOTT,
  CROWE. Fix F-B8a.)
- **Figure:** the detail page's contract count and award total.
- **Independent recomputation:** COUNT / SUM of the vendor's contracts over all rows. SQL lost — to be written at
  re-run.
- **Tolerance:** exact count, sum to the cent.
- **Sample rule:** every vendor with more than 250 contract rows.
- **Pass / Fail:** cards = all-row totals / any capped figure.

### DI-057 — Date columns parse and window

- **Class:** C-13
- **Created:** 2026-10-30 (batch 8). Title verbatim from Fable's batch-8 M1 acceptance.
- **Status:** fail (2026-10-30, batch 8; reconstructed 2026-11-08 — report lost;
  `openbook_contract_warrants.issue_date` and `openbook_contributions_raw.contribution_date` are M/D/YY text compared
  with ISO strings, so every windowed warrant figure is 0 — 4 vendors: 0 vs 722 rows in cycle:2026, 762 in cycle:2024;
  Fable's re-run: `?period=cycle:2026` 0 / $0.00 vs 686 by `to_date` for the vendor re-run. Fix F-B8b.)
- **Figure:** windowed warrant and contribution figures on OpenBook pages (`?period=` links from the list).
- **Independent recomputation:** the same rows windowed on `to_date(<text>, 'MM/DD/YY')`. SQL lost — to be written at
  re-run.
- **Tolerance:** exact.
- **Sample rule:** vendors with warrants in the window.
- **Pass / Fail:** windowed figures = the parsed-date recomputation / zeros or mismatches.

### DI-058 — NLP spending categories read the legacy contributions table

- **Class:** C-08 / C-16
- **Created:** 2026-11-08 (batch 9)
- **Status:** fail (2026-11-08, batch 9 report). `get_nlp_spending_summary` (`database/analytics.py:4431`) takes the
  first table that exists, and the frozen legacy `contributions` table (766 rows, $5,173,230.59) comes first; the
  ISBE expenditure branches never run and `d2_itemized_entries` is empty. Every category on `/analytics/donors`,
  `/analytics/` and `/api/analytics/nlp` describes legacy contributions, not spending. Fix F-B9a proposed.
- **Figure:** "Spending categories" (`nlp_summary`) on `/analytics/donors` and `/analytics/`; `/api/analytics/nlp`.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(*), SUM(amount) FROM contributions WHERE amount > 0;                         -- what the widget sums today
  SELECT COUNT(*), SUM(amount) FROM isbe_expenditures WHERE archived = FALSE AND amount > 0;  -- the spending it should describe
  ```
- **Tolerance:** the widget's total must equal the expenditure source's total (all time) to the cent.
- **Sample rule:** census.
- **Steps:**
  1. Open `/api/analytics/nlp`; add the category totals.
  2. Run both queries.
- **Pass:** the categories sum to the expenditure total.
- **Fail:** they sum to the legacy contributions total.

### DI-059 — State focus-sankey donor nodes group by last / business name only

- **Class:** C-16
- **Created:** 2026-11-08 (batch 9)
- **Status:** fail (2026-11-08, batch 9 report). The committee, candidate and vendor focus branches of
  `/api/analytics/network/focus-sankey` group receipts by `last_or_business_name` (all parts, transfers in
  included) and hash that name into the node id. Committee 25185 all time: 9 / 20 donor nodes merge more than one
  first name; committee 6239: 0 / 20 (cycle and all time), but its all-time view shows one node with two parallel
  edges from two spellings of one name. Every edge equals its own name group, so the defect is identity, not
  arithmetic. Fix F-B9g proposed.
- **Figure:** donor nodes and their edges in the Custom Sankey on `/analytics/networks` (committee focus).
- **Independent recomputation:**
  ```sql
  SELECT r.last_name, COUNT(DISTINCT COALESCE(r.first_name, '')) AS first_names,
         COUNT(DISTINCT LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,'')))) AS site_keys,
         SUM(r.amount), COUNT(*)
  FROM isbe_condensed_receipts r
  WHERE r.committee_id = <id> AND r.archived = FALSE AND r.amount > 0
  GROUP BY r.last_name ORDER BY 4 DESC LIMIT 20;
  ```
- **Tolerance:** a node must be one donor key.
- **Sample rule:** the top 20 donor nodes of 3 focus views (one large candidate committee all time, one party
  committee cycle and all time).
- **Steps:**
  1. Request `/api/analytics/network/focus-sankey?focus_node_id=committee:25185&period=all`.
  2. Run the query for 25185; count rows with `site_keys > 1`.
- **Pass:** every donor node is one site donor key.
- **Fail:** any node merges keys, or two nodes share an id.

### DI-060 — Candidate-competition donor pool cut inside a tie

- **Class:** C-03 / C-16
- **Created:** 2026-11-08 (batch 9)
- **Status:** fail (2026-11-08, batch 9 report). `get_candidate_competition_networks` keeps the top 2,000 state
  donors by `analytics_donor_summary.total_amount` with no tiebreak. The cut sits at $100,000.00, where 185 donors
  tie and 82 get in, so candidate 39266's node read $440,055,500.00 / 16 donors on the page and $440,155,500.00 / 17
  in the recomputation (three of its donors are in the tie). The federal pool has the same shape (13 donors tied at
  its $6,250 cut). Fix F-B9b proposed.
- **Figure:** candidate node totals and edges on `/analytics/relationships` "Candidate competition";
  `/api/analytics/candidate-competition`.
- **Independent recomputation:**
  ```sql
  WITH p AS (SELECT donor_key, total_amount, ROW_NUMBER() OVER (ORDER BY total_amount DESC) rn
             FROM analytics_donor_summary WHERE source = 'bulk_receipts')
  SELECT (SELECT total_amount FROM p WHERE rn = 2000) AS cut,
         (SELECT COUNT(*) FROM p WHERE total_amount = (SELECT total_amount FROM p WHERE rn = 2000)) AS tied,
         (SELECT COUNT(*) FROM p WHERE rn <= 2000 AND total_amount = (SELECT total_amount FROM p WHERE rn = 2000)) AS tied_inside;
  ```
- **Tolerance:** `tied = tied_inside` (or a documented deterministic tiebreak).
- **Sample rule:** census of both pools' cuts.
- **Steps:**
  1. Run the query (and its federal twin over `fec_schedule_a_contributions` by donor entity key).
- **Pass:** no donor tied at the cut is left out, or the order is deterministic.
- **Fail:** the cut splits a tie with no tiebreak.

## Batch log

| Date | Batch | Agent | Classes | Sample | Result | Report |
|---|---|---|---|---|---|---|
| 2026-10-29 | 1 | opus-engineer 10-B | C-01 | n = 30 directory rows, 10 committee ledgers, 5 Donors tabs, 1,075 rendered network links, 10 home rows | live paths pass; all-time precomputed paths fail: I1, I2, E — fixes F1/F2a/F4/F6 proposed to terminal 1 | `docs/audits/data_integrity_batch1_donors_2026-10-29.md` |
| 2026-10-30 | 2 | opus-builder (opus-engineer) | C-02, C-03 | DI-004 / DI-005: 30 candidates each; DI-006 / DI-007: 30 entities (15 candidates, 15 cycle-2026 races) and 30 edges; DI-039: census | all pass, no page defect; DI-004's recomputation amended to the source-table picker; new DI-039 (D4 detector) pass, residual 0 | `docs/audits/data_integrity_batch2_candidates_2026-10-30.md` |
| 2026-10-30 | 3 | opus-builder-3 (opus-engineer) | C-04, C-05, S-02 | DI-008 / DI-009: 60 committees (20 / 20 / 20, incl. party committees and 5 ids ≤ 348); DI-031: 30 rows + census 2,898; DI-010: 60 filings (12 amended D-2s); DI-011: 30 + 30 rows | DI-008 (amended, E20 bound), 009, 010, 011, 031 pass; new DI-040, DI-041 fail; fixes F-B3a–d. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §1 |
| 2026-10-30 | 4 | opus-builder-4 (opus-engineer) | S-01, S-03, C-06 | 22 home cards + 7 directory cards (census); DI-030: top 5 × 2 views; DI-012: 30 rows + census 5,252 | DI-030 pass; DI-029 fail 17 / 22, DI-032 fail 5 / 7, DI-012 fail; new DI-042 fail; fixes F-B4a–g. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §2 |
| 2026-10-30 | 5 | opus-builder-5 (opus-engineer) | C-07, C-08 | DI-013: census 1,465 races + 30 race pages; DI-014: 53 months; DI-015: 20 states + 20 cities; DI-043..049 per page | DI-013, 014, 015 pass; new DI-043, 044, 045, 048 pass, DI-046, 049 fail, DI-047 proposed; fixes F-B5a–e. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §3 |
| 2026-10-30 | 6 | opus-builder-6 (opus-engineer) | C-09, C-10 | federal cards (census where frames were small); DI-017: 10 candidates + 10 committees; DI-018: 10 races | DI-016, 017, 018 fail under the FEC rule (E32); new DI-051, 052 fail, DI-050 proposed; fixes F-B6a–i. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §4 |
| 2026-10-30 | 7 | opus-builder-7 (opus-engineer) | C-11, C-12 | DI-019: 15 entities + 15 clients; DI-020: 15 clients; DI-021: 30 orgs; DI-022: census + list census 1,896 | DI-019, 020, 021, 022 fail; new DI-053, 054, 055 fail; fixes F-B7a–h. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §5 |
| 2026-10-30 | 8 | opus-builder-8 (opus-engineer) | C-13, C-14, S-04 | DI-023: 555 list rows + 68 detail pages; DI-024: 29 results; DI-025: 4 dates + 40 rows; DI-033: 10 pairs / 20 cards | DI-025, DI-033 pass; DI-023, DI-024 fail; new DI-056, 057 fail; fixes F-B8a–g. Reconstructed 2026-11-08 — report lost | `docs/audits/data_integrity_batches_3-8_reconstructed_2026-11-08.md` §6 |
| 2026-11-08 | 9 | opus-builder-9b (opus-engineer) | C-16, C-15, C-17 + admin | DI-027: 30 values over 14 endpoints; DI-026: census + 30 legacy committees, 10 reports, 5 donors; DI-028: 10 races; DI-034: not run | DI-028 pass 10 / 10; DI-026 fail (legacy Donors figure); DI-027 fail 25 / 30; DI-034 blocked (no local credential); new DI-058, DI-059, DI-060 fail; fixes F-B9a–g proposed | `docs/audits/data_integrity_batch9_json_legacy_vizlab_admin_2026-11-08.md` |
