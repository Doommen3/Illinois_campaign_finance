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

**Progress:** Checks: 34 total — 31 proposed, 3 in progress, 0 pass, 0 fail, 0 blocked (2026-10-29). Each batch
updates this line.

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
| 1 | C-01 | Most-read entity pages; the known 8b defect (donor page omits a committee) lives here | in progress | `docs/audits/data_integrity_batch1_donors_2026-10-29.md` |
| 2 | C-02, C-03 | Candidate profiles and their tabs are the next most-read entity pages | proposed | — |
| 3 | C-04, C-05, S-02 | Committee profiles, filings and the committee directory; the D-2 is the sworn ground truth later batches lean on | proposed | — |
| 4 | S-01, S-03, C-06 | Home and candidate directory cards restate classes 1–3; candidate-finance shares their D-2 source | proposed | — |
| 5 | C-07, C-08 | State races and analytics dashboards (cached builders over the same receipts) | proposed | — |
| 6 | C-09, C-10 | Federal (FEC) pages, one source family | proposed | — |
| 7 | C-11, C-12 | Lobbying and IRS 527, the match-table classes | proposed | — |
| 8 | C-13, C-14, S-04 | OpenBook, cross-source tools and compare (tools restate figures checked earlier) | proposed | — |
| 9 | C-16, C-15, C-17 + admin audits | JSON endpoints, legacy pages, the local-only Viz Lab and internal audit pages last | proposed | — |

S-05 (`/about`) and S-06 (`/privacy`) show no figures and need no check; the non-page endpoints in
`docs/page_inventory.md` carry no figures except the two admin audit pages (DI-034).

## Index

| ID | Class | Figure | Status |
|---|---|---|---|
| DI-001 | C-01 | Donor profile total, count and "Who They Donated To" | in progress |
| DI-002 | C-01 | Committee donor ledger: donor count and total in the window | in progress |
| DI-003 | C-01 | Donor directory top rows for the default cycle | in progress |
| DI-004 | C-02 | Candidate "Money raised" per cycle | proposed |
| DI-005 | C-02 | Candidate outside spending (D-2 Part 9) supporting / opposing | proposed |
| DI-006 | C-03 | Entity Donors tab: top donors and total | proposed |
| DI-007 | C-03 | Network tab edge amount and its drill-down rows | proposed |
| DI-008 | C-04 | Committee Receipts Summary (window and This cycle) | proposed |
| DI-009 | C-04 | Committee Expenditures Summary and payee ledger total | proposed |
| DI-010 | C-05 | Filing detail line items and D-2 summary | proposed |
| DI-011 | C-05 | D-2 reconciliation row | proposed |
| DI-012 | C-06 | Candidate-committee finance row and its itemized pages | proposed |
| DI-013 | C-07 | State race per-candidate raised and race total | proposed |
| DI-014 | C-08 | Analytics monthly time series | proposed |
| DI-015 | C-08 | Analytics geography state and city totals | proposed |
| DI-016 | C-09 | Federal Schedule A / B / E cycle totals | proposed |
| DI-017 | C-10 | Federal candidate and committee Schedule A totals | proposed |
| DI-018 | C-10 | Federal race outside spending (Schedule E) | proposed |
| DI-019 | C-11 | Lobbying clients per entity per registration year | proposed |
| DI-020 | C-11 | Lobbying matched campaign donors money | proposed |
| DI-021 | C-12 | 527 organization contributions and expenditures | proposed |
| DI-022 | C-12 | 527 dark-money totals | proposed |
| DI-023 | C-13 | OpenBook vendor contracts and contributions | proposed |
| DI-024 | C-14 | Tool figures equal the profile figures they link to | proposed |
| DI-025 | C-14 | Live feed latest dates | proposed |
| DI-026 | C-15 | Legacy pages and v1 API counts | proposed |
| DI-027 | C-16 | Analytics JSON payload values | proposed |
| DI-028 | C-17 | Viz Lab race-money prototype value | proposed |
| DI-029 | S-01 | Home stat cards | proposed |
| DI-030 | S-01 | Home Top Donors | proposed |
| DI-031 | S-02 | Committee directory windowed receipts | proposed |
| DI-032 | S-03 | Candidate directory stat cards | proposed |
| DI-033 | S-04 | Compare stat cards | proposed |
| DI-034 | admin | Federal receipt / disbursement audit pages | proposed |

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
- A donor key copied from a `/donors/key/<donor_key>` URL is URL-encoded: decode `%7C` to `|` and `%20` to a space
  before pasting it into a query.

## Checks

### DI-001 — Donor profile total, count and committee list

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** in progress (batch 1)
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
  committee whose donors page you started from is missing), or a count mismatch.

### DI-002 — Committee donor ledger count and total

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** in progress (batch 1)
- **Figure:** `/committees/<id>/donors`: "N total donors", "Total Contributions" and the "This cycle" subtotal.
- **Independent recomputation:**
  ```sql
  SELECT COUNT(DISTINCT LOWER(TRIM(COALESCE(r.first_name,'') || '|' || COALESCE(r.last_name,'') || '|' || COALESCE(r.address1,'') || '|' || COALESCE(r.address2,'') || '|' || COALESCE(r.city,'') || '|' || COALESCE(r.state,'') || '|' || COALESCE(r.zipcode,'')))) AS donors,
         SUM(r.amount) AS total
  FROM isbe_receipts r
  WHERE r.committee_id = <id>
    AND r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%')
    AND r.received_date BETWEEN '<start>' AND '<end>';
  ```
  Run once for the page window and once for `'2025-01-01'`..`'2026-12-31'` (This cycle).
- **Tolerance:** donor count exact; totals to the cent.
- **Sample rule:** n = 60 committees, 20 top / 20 middle / 20 small by all-time receipts.
- **Steps:**
  1. Open `/committees/<id>/donors` for a sampled committee (start with `/committees/6239/donors`).
  2. Read the donor count, the total and "This cycle".
  3. Run the query for both windows.
- **Pass:** all three figures match.
- **Fail:** any mismatch; record whether `isbe_condensed_receipts` with the same clauses matches the page (picker
  or amendment difference) or not (page defect).

### DI-003 — Donor directory top rows

- **Class:** C-01
- **Created:** 2026-10-29
- **Status:** in progress (batch 1)
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
  no non-archived D-2 report, is the analytics donor filter (`_BULK_DONOR_RECEIPT_FILTER_SQL` in
  `database/analytics.py`): record it as "known semantic", not as a pass.
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
- **Created:** 2026-10-29
- **Status:** proposed
- **Figure:** `/candidates/<id>`: the "Money raised" table (cycle, raised).
- **Independent recomputation:**
  ```sql
  SELECT EXTRACT(YEAR FROM r.received_date)::int + EXTRACT(YEAR FROM r.received_date)::int % 2 AS cycle,
         SUM(r.amount) AS raised
  FROM isbe_receipts r
  WHERE r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>)
    AND r.archived = FALSE AND r.amount > 0 AND r.received_date IS NOT NULL
  GROUP BY 1 ORDER BY 1 DESC;
  ```
- **Tolerance:** to the cent per cycle.
- **Sample rule:** n = 30 candidates, 10 statewide or top-raised / 10 middle / 10 small.
- **Steps:**
  1. Open `/candidates/13505` (then each sampled candidate).
  2. Read every row of "Money raised".
  3. Run the query.
- **Pass:** every cycle's amount matches.
- **Fail:** any cycle off by more than a cent; check the same query on `isbe_condensed_receipts` to tell a picker
  difference from a page defect.

### DI-005 — Candidate outside spending

- **Class:** C-02
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
- **Figure:** `/candidates/<id>/donors` (and `/analytics/state-races/<race_key>/donors`): the "Top donors" table
  (top 10 amounts) and the tab total.
- **Independent recomputation:**
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
- **Tolerance:** to the cent per row; same order.
- **Sample rule:** n = 30 entities, 15 candidates and 15 races, top / middle / small by raised.
- **Steps:**
  1. Open `/candidates/13505/donors`.
  2. Read the top 10 rows and the total.
  3. Run the query (for a race, take the linked committees of every candidacy in the race).
- **Pass:** rows and total match.
- **Fail:** any row or total mismatch with no named cause.

### DI-007 — Network edge and drill-down rows

- **Class:** C-03
- **Created:** 2026-10-29
- **Status:** proposed
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
  For an in-kind panel edge use `r.d2_part = '5A'`.
- **Tolerance:** to the cent; the drill-down row count equals `n`.
- **Sample rule:** one edge per sampled entity of DI-006 (n = 30), mixing money-in, in-kind and money-out panels.
- **Steps:**
  1. Open `/candidates/13505/network`, click an edge, note its amount and rows.
  2. Find the same edge in `/candidates/13505/network.json` (its `source`, `target`, amount).
  3. Run the query.
- **Pass:** edge amount, panel total and query total agree; row counts agree; the donor node's link opens a donor
  page whose committee list contains this committee (cross-check with DI-001).
- **Fail:** any disagreement, or a donor link that lands on a page without this committee.

### DI-008 — Committee Receipts Summary

- **Class:** C-04
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Pass:** both figures match to the cent and neither exceeds the D-2 bound.
- **Fail:** a mismatch, or an itemized total above the sworn D-2 total.

### DI-009 — Committee Expenditures Summary and payee ledger

- **Class:** C-04
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
- **Figure:** `/legacy` counts, `/api/stats` counts and totals, `/reports/` count, `/committees/legacy/<id>` "Total
  Contributions".
- **Independent recomputation:**
  ```sql
  SELECT (SELECT COUNT(*) FROM committees) AS committees, (SELECT COUNT(*) FROM reports) AS reports,
         (SELECT COUNT(*) FROM contributions) AS contributions, (SELECT COUNT(*) FROM donors) AS donors,
         (SELECT SUM(amount) FROM contributions) AS total;
  SELECT COUNT(*), SUM(c.amount) FROM contributions c JOIN reports p ON p.id = c.report_id WHERE p.committee_id = <id>;
  ```
- **Tolerance:** exact.
- **Sample rule:** census of the counts; 30 legacy committee pages.
- **Steps:**
  1. Open `/legacy` and `/api/stats`; note the counts.
  2. Open `/committees/legacy/1`; note the total.
  3. Run the queries.
- **Pass:** every figure matches.
- **Fail:** any mismatch.

### DI-027 — Analytics JSON payloads

- **Class:** C-16
- **Created:** 2026-10-29
- **Status:** proposed
- **Figure:** values in `/api/analytics/time-series`, `/api/analytics/reconciliation`, `/api/analytics/geo` and
  the other analytics endpoints.
- **Independent recomputation:** the C-08 / C-05 checks (DI-014, DI-015, DI-011) for the same window; the JSON
  value must equal both the page widget and the recomputation.
- **Tolerance:** to the cent.
- **Sample rule:** n = 30 values across the 14 endpoints, at least one each.
- **Steps:**
  1. Open `/api/analytics/time-series` with the same window parameters as the page.
  2. Compare its values with the page and with DI-014's query.
- **Pass:** all three agree.
- **Fail:** any disagreement.

### DI-028 — Viz Lab race-money value

- **Class:** C-17
- **Created:** 2026-10-29
- **Status:** proposed
- **Figure:** `/experimental/viz-lab` "Race Money vs Outside Pressure": one race's money and outside spending.
- **Independent recomputation:** DI-013's query for the same race and window; outside spending as in DI-005 over
  the race's candidates.
- **Tolerance:** to the cent.
- **Sample rule:** n = 10 races (local server only; the Viz Lab is not public).
- **Steps:**
  1. Start a local server, open `/experimental/viz-lab`.
  2. Note a race's values; run DI-013's query.
- **Pass:** values match.
- **Fail:** any mismatch.

### DI-029 — Home stat cards

- **Class:** S-01
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
- **Figure:** `/` "Top Donors" table (top 10 amounts).
- **Independent recomputation:** DI-003's query for the home page's window.
- **Tolerance:** as DI-003.
- **Sample rule:** the 10 rows.
- **Steps:**
  1. Open `/`; note the Top Donors rows (by rank).
  2. Run DI-003's query.
- **Pass:** rows match, or differ only by DI-003's known semantic.
- **Fail:** any other difference.

### DI-031 — Committee directory windowed receipts

- **Class:** S-02
- **Created:** 2026-10-29
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
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
- **Status:** proposed
- **Figure:** `/admin/federal-receipt-audit` and `/admin/federal-disbursement-audit` totals (signed in).
- **Independent recomputation:** DI-016's Schedule A and B queries for the same cycle and filters.
- **Tolerance:** to the cent.
- **Sample rule:** census of the summary figures; 10 rows each.
- **Steps:**
  1. Sign in, open both audit pages.
  2. Run DI-016's queries.
- **Pass:** every figure matches.
- **Fail:** any mismatch.

## Batch log

| Date | Batch | Agent | Classes | Sample | Result | Report |
|---|---|---|---|---|---|---|
| 2026-10-29 | 1 | opus-engineer (lane 10-B) | C-01 | n = 60 planned | in progress | `docs/audits/data_integrity_batch1_donors_2026-10-29.md` |
