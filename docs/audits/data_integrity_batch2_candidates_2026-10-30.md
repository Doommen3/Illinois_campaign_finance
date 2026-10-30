# Data integrity, batch 2: candidate profiles and entity analytics tabs (2026-10-30)

To-do 2026-10-28 item 10, batch 2: classes **C-02** (candidate profiles) and **C-03** (entity analytics tabs),
checks DI-004, DI-005, DI-006 and DI-007, plus a new check, DI-039. This is a read-only analysis of the local `ilcf`
database. Local ISBE data runs through 2026-09-30; prod is about a month fresher, so every figure here is **local**.
Every query ran with `PGOPTIONS='-c default_transaction_read_only=on'`. Nothing was fixed in this unit. Plan:
`.claude/plans/data-integrity-batches.md` §Batch 2. Before the sweep, the five ISBE matviews were rebuilt under
main's DDL (D4 picker), and `refresh-analytics --with-snapshot` finished at 00:27:18Z. No figure checked here reads
an `analytics_*` table.

Public-repo rule: the report names candidates, committees, organizations and races only. Individual donors appear
only as md5 prefixes of their donor key, never by name, address or key.

## Result

| Check | Figure | n | Result |
|---|---|---|---|
| DI-004 | Candidate "Money raised" per cycle | 30 candidates | **pass**, 30 / 30 (amended recomputation) |
| DI-005 | Candidate outside spending (D-2 Part 9) | 30 candidates | **pass**, 30 / 30 |
| DI-006 | Donors tab Total, In-kind and Top donors | 30 entities (15 candidates, 15 races) | **pass**, 30 / 30 |
| DI-007 | Network edge, its rows and the donor link | 30 edges (one per DI-006 entity) | **pass**, 30 / 30 |
| DI-039 (new) | Live receipts outside the condensed view are explained | all committees | **pass**, residual 0 rows |

With 0 failures in n = 30, the 95 % upper bound on each class feeder's failure rate is below 10 % (rule of three).
No page defect was found in either class.

## Scope

| Member (`url_map` rule) | Class | What was checked |
|---|---|---|
| `/candidates/<int:candidate_id>` | C-02 | "Money raised" per cycle (DI-004); the "Outside spending (D-2 Part 9)" card and its "All N line items" count (DI-005) |
| `/candidates/<int:candidate_id>/outside-spending` | C-02 | Supporting, Opposing and row count of the line-item summary (DI-005) |
| `/candidates/<int:candidate_id>/<tab>` | C-03 | `donors`: Total and In-kind on the "All committees" row (or the single group row), and the Top donors rows (DI-006); `network`: the donor node link map (DI-007) |
| `/candidates/<int:candidate_id>/network.json` | C-03 | One edge's weight and row count (DI-007) |
| `/candidates/<int:candidate_id>/network/rows.json` | C-03 | That edge's drill-down rows: total and count (DI-007) |
| `/analytics/state-races/<race_key>/<tab>` | C-03 | As the candidate tab, for 15 races |
| `/analytics/state-races/<race_key>/network.json` (and `/network/rows.json`) | C-03 | As the candidate JSON, for 15 races |

The risk, geography and relationships tabs read the same receipt base (`database/entity_analytics.py:53`) through
the same entity resolution. They were not sampled separately; the inventory gives them no separate check.

## Method

**Independent recomputation.** Each figure is recomputed with SQL over the source tables: `isbe_filed_docs`,
`isbe_d2_reports`, `isbe_receipts` and `isbe_expenditures`. The condensed matviews the pages read are never used.
The amendment picker is applied to the source tables exactly as documented in `MATVIEW_DDL`
(`scripts/isbe_sunshine_etl.py`, the `isbe_most_recent_filings` and `isbe_condensed_receipts` definitions). A
committee's receipts count when they pass one of two branches:

1. **Dated after the last periodic filing.** The receipt is dated after the committee's latest
   `reporting_period_end` among filings whose `doc_name` is not A-1, Statement of Organization,
   Letter/Correspondence, B-1 or Nonparticipation. A committee with no such filing uses 1900-01-01.
2. **On the kept filing.** The receipt is on the one filing kept for its `(committee, reporting_period_begin,
   reporting_period_end)`, chosen among filings whose `doc_name <> 'Pre-election'`. The kept filing is the one
   with a live D-2 first, then the latest `received_datetime` (NULLS LAST), then the highest id.

Either way the row also needs `archived = FALSE AND amount > 0`. This is the "picked receipts" fragment below; the
same fragment over `isbe_expenditures` / `expended_date` gives picked expenditures.

```sql
-- picked receipts for the committees in :ids (an int array); append the figure's own filters
WITH cm AS (SELECT unnest(:ids::int[]) AS committee_id),
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
  AND (r.received_date > le.pe OR r.filed_doc_id IN (SELECT id FROM kept))
```

**Pages** were read with the Flask test client over the clone's code (main `669364a`) against the local database.
The route caches were in-process only (no `REDIS_URL`), so every sweep run built its payloads fresh.

**Scripts.** The scripts are kept outside the repo, in the session scratchpad `batch2-work/`. The drawing query is
`draw_c02.sql`. The sweeps are `b2_di004v2.py`, `b2_di005.py` and `b2_c03.py`. The diagnosis queries are
`di004_diag.sql`, `di004_docs.sql` and `di039.sql`. Every query that decides a result is quoted in this report.

**Exclusions.** 765 redacted candidates (`isbe_candidates.redaction_requested`) were removed from every frame. No
redacted donor row and no parser-dirty row appeared in any sampled figure. Every Top-donors row shown had a donor
link, so all of them were compared by key.

## C-02 — candidate profiles

### DI-004: "Money raised" per cycle (n = 30)

**Sample.** The frame is 14,969 candidates with raised > 0 over their linked committees (`isbe_candidate_committees`),
all time. Strata: the top 10 by rank, 10 from the 45th–55th percentile, and 10 from the bottom third, each ordered
by `md5(candidate_id)`. Drawing query:

```sql
WITH cm AS (SELECT committee_id, SUM(amount) AS raised FROM isbe_receipts
            WHERE archived = FALSE AND amount > 0 AND received_date IS NOT NULL GROUP BY committee_id),
cand AS (SELECT cc.candidate_id, SUM(cm.raised) AS raised FROM isbe_candidate_committees cc
         JOIN cm ON cm.committee_id = cc.committee_id
         JOIN isbe_candidates c ON c.id = cc.candidate_id AND NOT COALESCE(c.redaction_requested, FALSE)
         GROUP BY cc.candidate_id HAVING SUM(cm.raised) > 0),
ranked AS (SELECT candidate_id, raised, PERCENT_RANK() OVER (ORDER BY raised DESC) AS pr,
                  ROW_NUMBER() OVER (ORDER BY raised DESC, candidate_id) AS rk, COUNT(*) OVER () AS n_total FROM cand),
s AS ((SELECT 'top' AS stratum, * FROM ranked WHERE rk <= 10)
      UNION ALL (SELECT 'middle', * FROM ranked WHERE pr BETWEEN 0.45 AND 0.55 ORDER BY md5(candidate_id::text) LIMIT 10)
      UNION ALL (SELECT 'small', * FROM ranked WHERE pr >= 0.6667 ORDER BY md5(candidate_id::text) LIMIT 10))
SELECT stratum, candidate_id, ROUND(raised::numeric, 2) AS raised, rk, n_total FROM s ORDER BY rk;
```

**Recomputation.** The picked receipts of the candidate's linked committees, with `received_date IS NOT NULL`,
grouped by `year + year % 2`. The candidate's committees come from
`(SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <id>)`.

**Result: 30 / 30 pass.** Every cycle row on the page matches to the cent. The result is the same when the picker
also drops archived filings (`AND f.archived = FALSE` in `kept`), so that choice does not affect any sampled
candidate. Every sampled candidate passed:

| Stratum | Candidate ids (each passes) |
|---|---|
| top | 39266, 27663, 109, 4675, 10463, 17058, 16939, 16940, 24169, 16872 |
| middle | 28423, 43659, 25576, 26948, 20515, 14927, 8017, 20074, 43290, 21234 |
| small | 22552, 45311, 26063, 23982, 28056, 6095, 53639, 8816, 21691, 54037 |

**The check's first query was wrong, and was amended.** As first written, DI-004 summed every live
`isbe_receipts` row. That way only 23 of the 30 matched. All 7 differences sit on 3 committees, and each time the raw
sum counts a contribution that a later filing replaced. The page is right in every case. These cases are the
worked examples for DI-039 below, not DI-004 failures. The amended DI-004 applies the picker above instead.

### DI-005: outside spending (n = 30)

**Method.** I wrote an independent attribution from the rule text in `docs/outside_spending.md`, not from
`database/outside_spending.py`. It covers the (first, last) name key with punctuation stripped and hyphens read as
spaces, trailing suffixes, the split on `, ; / &` and "and" with an amount / n share, the single-comma
"Last, First" reading, direct nickname aliases and the candidacy-cycle gate. Only the data map `NICKNAMES`
(`database/name_normalize.py`) is shared with the site. The rule runs over raw `isbe_expenditures` with
`archived = FALSE AND d2_part LIKE '9%'`; 19,649 rows locally.

**Sample.** The frame is 1,086 non-redacted candidates with at least one attributed row. The strata are the same
as DI-004, by attributed total.

| Stratum | Candidate ids (each passes) |
|---|---|
| top | 39266, 27663, 18501, 45774, 45575, 15105, 34309, 45618, 28606, 42966 |
| middle | 47003, 45964, 25929, 40847, 42927, 39312, 33233, 34410, 33151, 45776 |
| small | 33186, 25576, 17457, 46621, 28578, 43422, 40677, 34501, 26642, 25396 |

**Result: 30 / 30 pass.** Four figures equal the recomputation to the cent for every candidate. They are the
Supporting and Opposing totals and the row count on `/candidates/<id>/outside-spending`, the same three on the
profile card, and the card's "All N line items" count. This holds over raw rows and over condensed rows: no
attributed row is missing from `isbe_condensed_expenditures`. The top stratum includes 39266 and 27663; the
Part 9 totals run from $29.15M down to $50.

**Exclusions.** Some rows have the candidate's surname somewhere in `candidate_name` (`ILIKE '%<last name>%'`)
but are not attributed. Each one is explained by the rule:

| Reason (rule in `docs/outside_spending.md`) | Rows | Dollars | Candidates |
|---|---|---|---|
| Another candidate: last token equal, first name neither equal nor a direct alias | 65 | $248,244.38 | 10 |
| Single-token name ("Rauner"), which never matches | 14 | $411,258.14 | 2 |
| Surname only a substring of another name, or not the last token | 7 | $53,982.95 | 2 |
| Cycle gate: the row's cycle is not one of the candidate's candidacy cycles | 8 | $54,872.48 | 4 |

No excluded row is unexplained.

## C-03 — entity analytics tabs

### Sample (n = 30 entities)

**Candidates (15).** The DI-004 ranking is used, five per stratum. The top stratum is 5 drawn by `md5(id)` from
the top 30, so committees shared by a slate are not drawn over and over. Middle and small are 5 each, ordered by
`md5('c03' || id)`. Window: all time, the candidate tab default.

**Races (15).** The frame is the cycle-2026 races of `isbe_race_candidacies`, keyed by the race builder's own slug
(`_state_race_slug`). Of 1,465 races, 966 have picked part-1 receipts in 2025-01-01..2026-12-31; they are ranked by
that total. Strata: the top 5, 5 from the 45th–55th percentile and 5 from the bottom third, each ordered by
`md5('c03' || race_key)`. Window: `cycle:2026` (2025-01-01..2026-12-31), the race tab default, read back from
`network.json` `summary.window`.

### DI-006: Donors tab (n = 30)

**Recomputation.** The picked receipts of the entity's committees in the window, with `entity.committee_ids` read
back from `network.json`. For a race, each committee counts once. Total is `SUM(amount) FILTER (WHERE d2_part
LIKE '1%')`. In-kind is `FILTER (WHERE d2_part = '5A')`. The Top donors are the donor key's `SUM(amount)` over
`d2_part LIKE '1%'`, top 10 by amount. Each linked row on the page is then compared with its own key's recomputed
total.

**Result: 30 / 30 pass.** Total and In-kind match to the cent. The top-10 amount sequence matches the
recomputation in every entity. Every donor key shown carries exactly its recomputed amount. In 14 entities, several
donors tie at the 10th amount, so the page and an arbitrary `LIMIT 10` pick different keys of equal amount. Those
ties are not mismatches.

### DI-007: network edge, rows and donor link (n = 30)

The edge type rotates between money-in (`donor_committee`, 1A), in-kind (`in_kind_donor_committee`, 5A) and
money-out (`committee_vendor`). The heaviest edge of that type is taken, and the next type is used when an entity
has none. Mix: 14 money-in, 6 in-kind, 10 money-out.

**Recomputation.**
- **Donor edges:** picked receipts at the edge's committee with `md5(<donor key expression>) = md5(<node key>)`,
  `d2_part = '1A'` (money-in) or `'5A'` (in-kind), in the window.
- **Vendor edges:** picked expenditures at the committee whose `LOWER(TRIM(first_name || ' ' || last_name))`
  hashes to the node id (`vendor:` + `sha1[:16]`), parts other than `9%`, in the window.

**Pass rule.** The edge weight, the `rows.json` `total_amount` and the recomputed sum agree to the cent, and the
edge count, the `rows.json` `total_count` and the recomputed count are equal. A money-in donor must also have a link
(`entity-network-meta` `hrefs`). The link must open a 200 page whose "Who They Donated To" table has a row named
like the committee, with an amount at least the edge weight. That page lists committees by name only, without
links, so the row is found by name.

**Result: 30 / 30 pass.**

| Entity | Stratum | Edge | Committee | Edge weight = rows = recomputed | Rows | Donor link |
|---|---|---|---|---|---|---|
| candidate 25139 | top | money-in | 22989 | $630,400.00 | 3 | 200, committee listed |
| candidate 12953 | top | in-kind | 13471 | $1,243,549.37 | 1,532 | none (in-kind only, fix F2a) |
| candidate 16988 | top | money-out | 13471 | $4,508,768.67 | 23 | — |
| candidate 14580 | top | money-in | 1212 | $1,005,300.00 | 5 | 200, committee listed |
| candidate 579 | top | in-kind | 1212 | $516,454.88 | 97 | none (in-kind only) |
| candidate 22952 | middle | money-out | 21605 | $7,089.02 | 7 | — |
| candidate 35543 | middle | money-in | 32108 | $2,000.00 | 1 | 200, committee listed |
| candidate 54041 | middle | in-kind | 41125 | $1,276.59 | 1 | none (in-kind only) |
| candidate 28221 | middle | money-out | 25721 | $17,688.17 | 4 | — |
| candidate 53490 | middle | money-in | 40597 | $3,000.00 | 3 | 200, committee listed |
| candidate 12370 | small | money-in | 13138 | $160.00 | 1 | 200, committee listed |
| candidate 25411 | small | money-out | 4284 | $5,907.54 | 4 | — |
| candidate 42488 | small | money-in | 35105 | $1,000.00 | 1 | 200, committee listed |
| candidate 39297 | small | in-kind | 32786 | $1,377.10 | 2 | none (in-kind only) |
| candidate 47246 | small | money-out | 39520 | $890.00 | 2 | — |
| governor-statewide-illinois | top | money-in | 32762 | $90,500,000.00 | 3 | 200, committee listed |
| secretary-of-state-statewide | top | money-in | 19323 | $507,300.00 | 2 | 200, committee listed |
| president-county-board-cook | top | money-out | 19263 | $3,439,138.00 | 23 | — |
| committeeman-township-oak-park | top | money-in | 16283 | $255,000.00 | 2 | 200, committee listed |
| state-senator-senate-39 | top | in-kind | 16283 | $50,683.36 | 4 | none (in-kind only) |
| president-village-cary | middle | money-out | 25249 | $9,532.02 | 1 | — |
| trustee-township-york | middle | money-in | 36362 | $10,000.00 | 1 | 200, committee listed |
| clerk-village-manteno | middle | in-kind | 40138 | $766.00 | 1 | none (in-kind only) |
| clerk-city-calumet-city | middle | money-out | 40164 | $11,827.36 | 2 | — |
| treasurer-county-mchenry | middle | money-in | 37456 | $2,485.16 | 1 | 200, committee listed |
| assessor-township-oswego | small | money-in | 36837 | $1,000.00 | 1 | 200, committee listed |
| trustee-township-bremen | small | money-out | 40054 | $1,000.00 | 1 | — |
| trustee-township-south-moline | small | money-in | 40376 | $1,000.00 | 1 | 200, committee listed |
| trustee-village-carpentersville | small | money-in | 35009 | $1,000.00 | 1 | 200, committee listed |
| superintendent-regional-office-of-education-sangamon | small | money-out | 37048 | $1,999.00 | 4 | — |

Every DI-006 entity in the table above also passed DI-006. The six in-kind donors in the sample gave no 1A money to
the edge's committee. As fix F2a intends (`webapp/routes/entity_analytics.py` `_node_href`), they carry no link, so
the batch 1 I1 symptom (DI-037) cannot occur on them.

### The blank-`d2_part` gap (known semantic, measured)

The entity tabs keep `d2_part LIKE '1%'`, while the committee ledger's contributions rule also keeps blank parts.
**Sample: 0 of 30 entities have a blank-part row in their window ($0).** Site-wide, live rows with a blank part
are very few:

| Table | Rows | Dollars | Committees | Dates |
|---|---|---|---|---|
| `isbe_receipts` (live, amount > 0) | 7 | $23,800.00 | 2 | 2015-06-12 .. 2024-06-21 |
| `isbe_condensed_receipts` (live, amount > 0) | 3 | $11,400.00 | 2 | 2015-06-12 .. 2024-06-21 |

All of them are on candidate-linked committees. The Donors tab's own help text says "Itemized contributions (D-2
part 1A)", so leaving them out matches the page's wording. Classification: known semantic, not a defect.

```sql
SELECT COUNT(*), ROUND(SUM(amount)::numeric,2), COUNT(DISTINCT committee_id), MIN(received_date), MAX(received_date)
FROM isbe_condensed_receipts WHERE archived = FALSE AND amount > 0 AND COALESCE(TRIM(d2_part),'') = '';
```

## DI-039 (new): live receipts outside the condensed view are explained

This check detects the D4 class of defect, a picker that silently drops live receipts. It counts every live
`isbe_receipts` row (`archived = FALSE AND amount > 0 AND received_date IS NOT NULL`) that
`isbe_condensed_receipts` does not carry. Each row is diagnosed by the documented picker over the source tables,
and the first matching category wins (`di039.sql`, the "picked receipts" fragment's `last_end` and `kept` over all
committees):

| Category | Rows | Committees | Dollars | Receipt dates |
|---|---|---|---|---|
| (a) on an archived filing | 23,556 | 104 | $25,074,895.00 | 1994-07-01 .. 2025-11-05 |
| (b) superseded: the picker kept another filing for the period | 7,768 | 140 | $7,309,905.11 | 1994-08-18 .. 2024-09-30 |
| (c) Pre-election report (the DDL excludes it by design) | 2,864 | 132 | $2,521,734.30 | 1999-01-04 .. 2010-10-03 |
| residual: undiagnosed | **0** | — | $0 | — |
| **Total** | 34,188 | — | $34,906,534.41 | of $6,609,546,590.06 live raw dollars (0.53 %) |

No gap row is dated after its committee's last periodic period end, so the "after the last filing" branch drops
nothing. The M1 diagnosis had left 362 rows ($48K) undiagnosed. They were rows on a filing that had another filing
for the period, which the M1 query only counted when that other filing was not archived. Under the picker's own
order, all of them are category (b), and 102 Pre-election rows move to (c).

**Result: pass (residual 0 rows).**

**Worked examples** (classification: source data; ISBE left superseded rows live):

1. **Committee 665** (linked to candidates 109, 4675, 10463 and 17058), cycle 2010, $1,000. A-1 filing 379371
   is `archived = TRUE`, but its two $500 receipt rows have `archived = FALSE`. The same two contributions appear
   again as live rows on Semiannual 390612, which the view keeps. Category (a).
2. **Committee 16283** (candidates 16939 and 16940), cycle 2010, $40,850.32. Period 2010-01-01..2010-06-30 has two
   non-archived amendments with live D-2s: 413229 ($40,850.32, received 2010-10-18) and 439472 ($43,850.32,
   received 2011-07-15). The picker keeps 439472, so a raw sum counts the period twice. Category (b).
3. **Committee 10552** (candidate 8816), cycle 1998, $850. Semiannual 134553 covers 1996-09-16..1996-12-31 but its
   receipts are dated 1998. A later live amendment of the same period, 148794, has no receipts, and the picker
   keeps it. Category (b). The filing's period disagrees with its receipt dates, a source-data anomaly.

Queries to reproduce an example (psql variables):

```sql
-- di004_diag.sql: live raw rows of a candidate's committees in one cycle that the condensed view lacks, by filing
SELECT r.committee_id, r.filed_doc_id, f.doc_name, f.reporting_period_begin, f.reporting_period_end,
       f.received_datetime, f.amended, f.archived AS doc_archived,
       EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = r.filed_doc_id AND d.archived = FALSE) AS live_d2,
       COUNT(*) AS n, SUM(r.amount) AS amount
FROM isbe_receipts r JOIN isbe_filed_docs f ON f.id = r.filed_doc_id
WHERE r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = :cid)
  AND r.archived = FALSE AND r.amount > 0 AND r.received_date IS NOT NULL
  AND EXTRACT(YEAR FROM r.received_date)::int + EXTRACT(YEAR FROM r.received_date)::int % 2 = :cyc
  AND NOT EXISTS (SELECT 1 FROM isbe_condensed_receipts c WHERE c.id = r.id)
GROUP BY 1,2,3,4,5,6,7,8,9 ORDER BY 1,2;
-- di004_docs.sql: every filing of one committee for one period, with its live raw and condensed totals
SELECT f.id, f.doc_name, f.received_datetime, f.amended, f.archived,
       EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = f.id AND d.archived = FALSE) AS live_d2,
       (SELECT SUM(amount) FROM isbe_receipts r WHERE r.filed_doc_id = f.id AND NOT r.archived AND r.amount > 0) AS raw_live,
       (SELECT SUM(amount) FROM isbe_condensed_receipts r WHERE r.filed_doc_id = f.id AND NOT r.archived AND r.amount > 0) AS condensed_live
FROM isbe_filed_docs f
WHERE f.committee_id = :cmte AND f.reporting_period_begin IS NOT DISTINCT FROM :pb::date
  AND f.reporting_period_end IS NOT DISTINCT FROM :pe::date
ORDER BY f.received_datetime;
```

The DI-039 query itself is in `docs/data_integrity_checks.md` under DI-039.

## Mismatch classification

| Finding | Where | Classification | Count |
|---|---|---|---|
| A raw `isbe_receipts` sum is higher than the page for 7 of 30 candidates (DI-004 as first written) | 3 committees | source data (superseded rows left live); the check's query was amended | 7 candidates, $1,000 / $40,850.32 / $850 |
| Live raw receipts absent from the condensed view, site-wide | 34,188 rows | source data (a), (b) and by-design exclusion (c); residual 0 | $34.9M, 0.53 % |
| Top-10 donors at a tie choose a different key of equal amount than `LIMIT 10` | 14 entities | not a mismatch (equal amounts) | — |
| Blank `d2_part` rows not on the Donors tab | 2 committees site-wide | known semantic (the tab says "part 1A") | 3 condensed rows, $11,400 |

There is no defect, so no fix is proposed for the pages.

**Proposed check-text fixes**, applied in `docs/data_integrity_checks.md` in this batch:
- **DI-004.** The recomputation applies the documented picker over the source tables; it no longer sums every live
  raw row.
- **DI-006 and DI-007.** The recomputations use the same source-table picker, not `isbe_condensed_receipts`, so a
  picker defect cannot pass them unseen. The Top-donors comparison is per shown key, so ties at the 10th row do not
  fail.
- **DI-007.** The donor-link step reads the landing page's committee by name, because "Who They Donated To" has no
  committee links. An in-kind-only donor has no link by design (F2a).

## Findings for the orchestrator

- **Data dictionary drift (Part 9).** `.claude/data-dictionary.md` (§isbe_expenditures and `docs/outside_spending.md`)
  says 19,095 non-archived Part 9 rows / $130.3M. Locally, after the D4 rebuild, `isbe_condensed_expenditures`
  holds **19,649 rows / $168,777,576.53** (`archived = FALSE AND d2_part LIKE '9%'`). Raw `isbe_expenditures` holds
  the same 19,649 live rows: no raw row is missing from the view. The outside-spending coverage percentages in
  `docs/outside_spending.md` were measured on the old 19,095 rows.
- **One committee in two races.** Committee 16283 feeds both `committeeman-township-oak-park` and
  `state-senator-senate-39` in cycle 2026, so the two races show identical Donors-tab totals ($4,524,251.00). It is
  linked to candidates 16939 and 16940. This is the documented candidate-office quirk
  (`isbe_candidates.office` is the current office; plan J-S3), not a defect. Readers comparing races should know it.

## Manual checks for these classes

They are the DI-004, DI-005, DI-006, DI-007 and DI-039 entries in `docs/data_integrity_checks.md`, amended as
above. Short form for Devin:

1. **C-02.** Open `/candidates/13505`.
   - Read "Money raised" and run DI-004's query for 13505; each cycle must match to the cent.
   - Open "All N line items" and check the Supporting / Opposing / rows line equals the profile card.
   - Run DI-005's ILIKE query and confirm every row the page does not show is another first name, a one-word name,
     a substring surname or outside the candidate's cycles.
2. **C-03.** Open `/candidates/13505/donors`.
   - Check Total and In-kind against DI-006's query.
   - Then open `/candidates/13505/network`, click a donor edge, and compare the panel total with DI-007's query.
   - Follow the donor's link and find the committee by name in "Who They Donated To".
   - Repeat once for a race, `/analytics/state-races/governor-statewide-illinois/donors`, with the window 2025-01-01
     to 2026-12-31.
3. **After every ISBE import.** Run DI-039's query; the residual row must be 0.

## How to find issues like this

- **Keep the recomputation independent of the matview the page reads.** A comparison against the page's own
  matview only proves the page sums correctly. It cannot see a picker that drops or duplicates filings, and the D4
  bug ($38.7M of live receipts) would have passed it. Apply the documented rule to the source tables instead.
- **When raw and page disagree, diagnose before classifying.** For each row in the gap, look at its filing: is it
  archived, is there another filing for the period, which one did the picker keep, and is the report type excluded
  by design? An empty residual after that is the evidence that the picker does what its comment says.
- **Read the window from the payload.** Read the window the page actually used from `network.json`
  `summary.window` or the scope bar, never from the default you expect. Race tabs default to the race's cycle and
  candidate tabs to all time.
- **Compare top-N lists per key, not by position.** Ties at the cut are common below the top stratum.
- **Parse links after HTML-unescaping.** Donor keys contain `&` and apostrophes, which arrive as `&amp;` and
  `&#39;`.
