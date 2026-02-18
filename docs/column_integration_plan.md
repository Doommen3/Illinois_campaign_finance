# Column Integration Plan

Date: 2026-02-16

This document ranks unused columns, proves value with observed data, and specifies the minimum code/schema changes required to integrate high-value columns in FollowTheMoneyIL.

No code was implemented in this pass.

## Inputs Located

- Unused-columns inventory: `docs/column_load_drop_unused.csv`
- Raw-column census: `docs/raw_column_audit.csv`
- Candidate profiling artifact (39 columns): `tmp/column_candidate_profiles_v2.json`
- Human-readable profile appendix: `tmp/column_profile_appendix.md`
- Ranked output CSV: `docs/columns_to_integrate.csv`

Raw sources inspected:

- ISBE bulk files: `Bulk_download/` (including `Bulk_download/FiledDocs.txt`, `Bulk_download/Investments.txt`)
- IRS 527 full feed: `Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt`
- FEC raw payload storage: `raw_extractions` rows written by `database/federal_fec.py`

Current product code and schema inspected:

- `database/schema.sql`
- `database/connection.py`
- `database/federal_fec.py`
- `database/bulk_download_loader.py`
- `database/irs527_loader.py`
- `webapp/routes/federal_finance.py`
- `webapp/routes/committees.py`
- `webapp/routes/irs527.py`
- `webapp/templates/federal_finance/detail.html`
- `webapp/templates/committees/detail_sbe.html`
- `webapp/templates/irs527/detail.html`

## Step A - Candidate Selection

Selection rules applied from unused inventory + observed values:

1. Null-rate screen: prefer columns with null rate `< 0.40`.
2. Distinctness screen: include high-cardinality keys/IDs and medium-cardinality filter candidates.
3. Joinability screen: prioritize committee IDs, filed document IDs, transaction IDs, filing identifiers.
4. Entity-resolution screen: include columns that improve identity/matching context.
5. Governance/auditability screen: include amended/final/notice/period lineage fields.

Result:

- 39 candidate columns profiled across 7 feeds/tables.
- Action split from `docs/columns_to_integrate.csv`:
  - `integrate_now`: 22
  - `integrate_later`: 11
  - `investigate_semantics`: 3
  - `do_not_surface_pii`: 2
  - `exclude_low_signal`: 1

Explicit exclusions/flags with evidence:

- `fec_candidate_committees.email` and `fec_candidate_committees.treasurer_phone`: high-fill contact PII; action = `do_not_surface_pii`.
- `fec_candidate_committees.form_type`: constant `F1` (no information gain); action = `exclude_low_signal`.
- `irs527_organizations.pos_42`, `irs527_organizations.pos_43`: binary values but positional semantics unclear; action = `investigate_semantics`.

## Step B - Data Profiling (Evidence)

Computed for every candidate column:

- `% null`, `% empty`, `unique_count`, `unique_rate`
- `top_20` values
- value-length distribution (`min/median/p95/max`)
- numeric stats (`min/median/p95/max`) where numeric-like
- date validity and range where date-like
- key checks: uniqueness profile + join success rate where relevant

Raw/staging/DB profile status:

- Raw value profiles exist for all 39 candidates (`tmp/column_candidate_profiles_v2.json`).
- `stage_column_exists = false` for all 39 candidates, confirming they are not persisted in current modeled/staging tables.
- Join-rate checks from observed data:
  - `isbe_filed_docs_tsv.CommitteeID -> bulk_committees_clean.committee_id_sbe`: `0.999971`
  - `isbe_investments_tsv.CommitteeID -> bulk_committees_clean.committee_id_sbe`: `1.000000`
  - `isbe_investments_tsv.FiledDocID -> bulk_d2_totals_clean.filed_doc_id`: `0.999747`

Full per-column metrics are in Appendix A.

## Step C - Value Scoring and Ranked Backlog

Scoring rubric per column (0-3 each):

- Product value (`P`)
- Analytical value (`A`)
- Governance value (`G`)
- Engineering cost (`C`)
- Risk (`R`)

Priority score formula used in CSV:

- `priority_score = P + A + G - C - R`

Ranked backlog output:

- `docs/columns_to_integrate.csv`
- Columns included exactly as requested:
  - `dataset, table, column, null_rate, unique_rate, join_rate, value_scores, recommended_action`
- Additional helper field included: `priority_score`

Top-ranked integrate-now columns by score and evidence are listed in the next section.

## Top 10 Quick Wins

1. **`isbe_filed_docs.CommitteeID`**
   This key is non-null (`0.0` null rate) with excellent join performance (`0.999971` to committee table), and it unlocks reliable committee-level filing history without probabilistic matching. Current behavior skips `FiledDocs.txt` in the default bulk pipeline, so committees can miss filing history entirely unless sunshine ETL is run.

2. **`fec_schedule_b.disbursement_purpose_category`**
   It is fully populated (`0.0` null rate) and already normalized into a finite set (`OTHER`, `ADMINISTRATIVE`, `MATERIALS`, etc.), enabling immediate UI filters and analytic rollups of spending purpose. Today it is present in raw FEC payloads but dropped in transform/insert.

3. **`fec_schedule_e.is_notice`**
   Non-null boolean with meaningful split (`False` 2759, `True` 2338) and direct governance value: users can distinguish notices from regular filings. This supports transparency and correction-aware interpretation of independent expenditure data.

4. **`fec_schedule_e.most_recent`**
   Non-null boolean (`True` 4842, `False` 255) that directly supports de-duplication and amended-chain UI logic. It enables “latest filing only” analytics without guessing from dates.

5. **`isbe_filed_docs.RcvdDateTime`**
   Non-null and high distinctness (`unique_rate 0.917555`) with valid dates through `2026-02-15`; this is strong provenance for filing timeliness and recency filters. Current committee page can display it only when `isbe_filed_docs` exists, but bulk import currently never builds that table.

6. **`isbe_filed_docs.Amend`**
   Non-null boolean (`False` 836,680; `True` 99,334) that directly flags corrected filings and prevents misleading trend reads. The current committee filing history UI does not expose amendment state.

7. **`fec_schedule_a.amendment_indicator`**
   Non-null with known FEC amendment codes (`A`, `N`, `C`), which is high governance value for receipt rows and audit views. This is low-cost to carry because it is a scalar field already present in each raw row.

8. **`fec_schedule_b.file_number`**
   Non-null filing identifier with moderate cardinality and strong provenance value for linking row-level disbursements back to filing artifacts. It is currently omitted from schedule B insert/select/export paths.

9. **`fec_schedule_e.previous_file_number`**
   Very high distinctness (`unique_rate 0.211265`) and explicit lineage context for amendment chains in independent expenditure records. It enables “what changed from prior filing” drill-down once surfaced.

10. **`fec_candidate_committees.last_file_date`**
    Non-null with current recency (latest observed `2026-02-11`), enabling committee freshness labels on candidate pages. This improves user trust by showing how current each committee’s filings are.

## Integrate-Now Columns: Precise Change Plan

### Bundle A: FEC Schedule A Filing Provenance

Columns:

- `fec_schedule_a_contributions.amendment_indicator`
- `fec_schedule_a_contributions.file_number`
- `fec_schedule_a_contributions.transaction_id`

Minimum code/schema changes:

- `database/schema.sql`
  - Add columns to `fec_schedule_a_contributions`.
  - Add index on `file_number` (optional but recommended for provenance lookups).
- `database/connection.py`
  - Add `_ensure_column(...)` guards for the new columns so existing DBs migrate in-place.
- `database/federal_fec.py`
  - Add fields to `_upsert_schedule_rows` payload tuple and `INSERT ... ON CONFLICT` list.
  - Add fields to candidate detail contribution `SELECT` and serialized response payload.
- `webapp/templates/federal_finance/detail.html`
  - Add columns/badges in “Recent Contributions” table.
- Tests:
  - `tests/test_federal_fec.py`
  - `tests/test_webapp.py`

Migration needed: **Yes**

Backfill needed: **Yes** (rerun `sync-fec-il-federal`; cached `raw_extractions` can be reused)

API + UI change needed: **Yes**

### Bundle B: FEC Schedule B Provenance + Purpose

Columns:

- `fec_schedule_b_disbursements.amendment_indicator`
- `fec_schedule_b_disbursements.disbursement_purpose_category`
- `fec_schedule_b_disbursements.file_number`
- `fec_schedule_b_disbursements.transaction_id`

Minimum code/schema changes:

- `database/schema.sql`
  - Add four columns to `fec_schedule_b_disbursements`.
  - Add indexes on `file_number`, `disbursement_purpose_category` (if filtering/sorting in UI).
- `database/connection.py`
  - `_ensure_column(...)` for all four columns.
- `database/federal_fec.py`
  - Extend `_upsert_schedule_b_rows` payload and insert/update list.
  - Extend candidate detail schedule B `SELECT` and serialized payload.
- `webapp/routes/federal_finance.py`
  - Extend schedule B CSV export rows/header.
- `webapp/templates/federal_finance/detail.html`
  - Surface purpose category and filing provenance columns.
- Tests:
  - `tests/test_federal_fec.py`
  - `tests/test_webapp.py`

Migration needed: **Yes**

Backfill needed: **Yes**

API + UI change needed: **Yes**

### Bundle C: FEC Schedule E Notice + Filing Lineage

Columns:

- `fec_schedule_e_independent_expenditures.is_notice`
- `fec_schedule_e_independent_expenditures.most_recent`
- `fec_schedule_e_independent_expenditures.file_number`
- `fec_schedule_e_independent_expenditures.previous_file_number`
- `fec_schedule_e_independent_expenditures.amendment_indicator`
- `fec_schedule_e_independent_expenditures.transaction_id`

Minimum code/schema changes:

- `database/schema.sql`
  - Add six columns to `fec_schedule_e_independent_expenditures`.
  - Add indexes on `file_number`, `previous_file_number`, and `most_recent` if exposing filters.
- `database/connection.py`
  - `_ensure_column(...)` additions for six columns.
- `database/federal_fec.py`
  - Extend `_upsert_schedule_e_rows` payload and insert/update list.
  - Extend candidate detail schedule E `SELECT` and serialized payload.
- `webapp/routes/federal_finance.py`
  - Extend schedule E CSV export rows/header.
- `webapp/templates/federal_finance/detail.html`
  - Add notice/recent/amendment/provenance columns or badges.
- Tests:
  - `tests/test_federal_fec.py`
  - `tests/test_webapp.py`

Migration needed: **Yes**

Backfill needed: **Yes**

API + UI change needed: **Yes**

### Bundle D: FEC Candidate Committee Metadata

Columns:

- `fec_candidate_committees.last_file_date`
- `fec_candidate_committees.first_file_date`
- `fec_candidate_committees.party_full`

Minimum code/schema changes:

- `database/schema.sql`
  - Add three columns to `fec_candidate_committees`.
- `database/connection.py`
  - `_ensure_column(...)` for these fields.
- `database/federal_fec.py`
  - Extend `_extract_committees_from_candidate` and fallback committee mapping to include fields.
  - Extend `_upsert_candidate_committees` payload and insert/update list.
  - Extend committee `SELECT` in candidate detail and serializer output.
- `webapp/templates/federal_finance/detail.html`
  - Show committee freshness/party labels in committees table.
- Tests:
  - `tests/test_federal_fec.py`
  - `tests/test_webapp.py`

Migration needed: **Yes**

Backfill needed: **Yes**

API + UI change needed: **Yes**

### Bundle E: ISBE FiledDocs Integration in Default Bulk Pipeline

Columns:

- `isbe_filed_docs.CommitteeID`
- `isbe_filed_docs.RptPdEndDate`
- `isbe_filed_docs.RptPdBegDate`
- `isbe_filed_docs.Amend`
- `isbe_filed_docs.DocName`
- `isbe_filed_docs.RcvdDateTime`

Minimum code/schema changes:

- `database/schema.sql`
  - Add `isbe_filed_docs` table (SQLite path) with typed columns and committee/reporting indexes.
- `database/connection.py`
  - If table already exists in older DBs, add `_ensure_column(...)` for `amended`/period/date compatibility.
- `database/bulk_download_loader.py`
  - Add `FILEDDOCS` prefix handling in `import_bulk_download`.
  - Create typed loader for `FiledDocs.txt` and load into `isbe_filed_docs`.
  - Include ingest stats in return payload.
- `webapp/routes/committees.py`
  - Include `amended` in filing-history query/serialization.
- `webapp/templates/committees/detail_sbe.html`
  - Render amended indicator in filing history rows.
- Tests:
  - `tests/test_bulk_download_loader.py`
  - `tests/test_isbe_rewire.py` (filing-history assertions)
  - `tests/test_webapp.py` (committee detail rendering)

Migration needed: **Yes** (new table in default schema path)

Backfill needed: **Yes** (rerun `import-bulk-download` with `FiledDocs.txt` present)

API + UI change needed:

- `CommitteeID`, `RptPdBegDate`, `RptPdEndDate`, `DocName`, `RcvdDateTime`: **No new API/UI contract needed once ingest exists** (already consumed by existing committee route/template).
- `Amend`: **Yes** (currently ignored in route/template).

### Per-Column Integrate-Now Plan Matrix

| Dataset | Table | Column | Bundle | Migration | Backfill | API+UI change |
|---|---|---|---|---|---|---|
| fec_api_schedule_a_json | fec_schedule_a_contributions | amendment_indicator | A | yes | yes | yes |
| fec_api_schedule_a_json | fec_schedule_a_contributions | file_number | A | yes | yes | yes |
| fec_api_schedule_a_json | fec_schedule_a_contributions | transaction_id | A | yes | yes | yes |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | amendment_indicator | B | yes | yes | yes |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | disbursement_purpose_category | B | yes | yes | yes |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | file_number | B | yes | yes | yes |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | transaction_id | B | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | is_notice | C | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | most_recent | C | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | file_number | C | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | previous_file_number | C | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | amendment_indicator | C | yes | yes | yes |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | transaction_id | C | yes | yes | yes |
| fec_api_candidate_committees_json | fec_candidate_committees | last_file_date | D | yes | yes | yes |
| fec_api_candidate_committees_json | fec_candidate_committees | first_file_date | D | yes | yes | yes |
| fec_api_candidate_committees_json | fec_candidate_committees | party_full | D | yes | yes | yes |
| isbe_filed_docs_tsv | isbe_filed_docs | CommitteeID | E | yes | yes | no |
| isbe_filed_docs_tsv | isbe_filed_docs | RptPdEndDate | E | yes | yes | no |
| isbe_filed_docs_tsv | isbe_filed_docs | RptPdBegDate | E | yes | yes | no |
| isbe_filed_docs_tsv | isbe_filed_docs | Amend | E | yes | yes | yes |
| isbe_filed_docs_tsv | isbe_filed_docs | DocName | E | yes | yes | no |
| isbe_filed_docs_tsv | isbe_filed_docs | RcvdDateTime | E | yes | yes | no |

## Step D - Exact Drop/Ignore Touchpoints (Non-Negotiable)

### Touchpoint Catalog

- `TP-A-RAW`: `database/federal_fec.py:2237` (`/schedules/schedule_a/` payload fetched and cached; no projection at raw layer)
- `TP-A-XFORM`: `database/federal_fec.py:1247` (Schedule A tuple omits candidate columns)
- `TP-A-SQL`: `database/federal_fec.py:1281` (Schedule A `INSERT` column list omits them)
- `TP-A-SCHEMA`: `database/schema.sql:457` (Schedule A table lacks these columns)
- `TP-A-API`: `database/federal_fec.py:4891` and `database/federal_fec.py:5168` (detail select/serializer omit)
- `TP-A-UI`: `webapp/templates/federal_finance/detail.html:245` (table headers/rows omit)

- `TP-B-RAW`: `database/federal_fec.py:4141` (`/schedules/schedule_b/` payload fetched and cached)
- `TP-B-XFORM`: `database/federal_fec.py:1404`
- `TP-B-SQL`: `database/federal_fec.py:1441`
- `TP-B-SCHEMA`: `database/schema.sql:495`
- `TP-B-API`: `database/federal_fec.py:4951` and `database/federal_fec.py:5191`
- `TP-B-CSV`: `webapp/routes/federal_finance.py:1360`
- `TP-B-UI`: `webapp/templates/federal_finance/detail.html:322`

- `TP-C-RAW`: `database/federal_fec.py:4371` (`/schedules/schedule_e/` payload fetched and cached)
- `TP-C-XFORM`: `database/federal_fec.py:1558`
- `TP-C-SQL`: `database/federal_fec.py:1593`
- `TP-C-SCHEMA`: `database/schema.sql:539`
- `TP-C-API`: `database/federal_fec.py:5010` and `database/federal_fec.py:5212`
- `TP-C-CSV`: `webapp/routes/federal_finance.py:1425`
- `TP-C-UI`: `webapp/templates/federal_finance/detail.html:432`

- `TP-D-RAW`: `database/federal_fec.py:1789` and `database/federal_fec.py:1866` (candidate and committee payloads cached raw)
- `TP-D-XFORM`: `database/federal_fec.py:946` and `database/federal_fec.py:2138` (committee extraction omits fields)
- `TP-D-SQL`: `database/federal_fec.py:997` (committee upsert column list omits fields)
- `TP-D-SCHEMA`: `database/schema.sql:435`
- `TP-D-API`: `database/federal_fec.py:5049` and `database/federal_fec.py:5234`
- `TP-D-UI`: `webapp/templates/federal_finance/detail.html:155`

- `TP-E-RAW`: `database/bulk_download_loader.py:15` and `database/bulk_download_loader.py:1362` (no FiledDocs prefix/file read in default bulk import)
- `TP-E-XFORM`: `database/bulk_download_loader.py:1371` (only committees/d2/candidates/links/receipts/expenditures loaders invoked)
- `TP-E-SCHEMA`: `database/schema.sql` (no `isbe_filed_docs` table in default schema)
- `TP-E-SQL-AMEND`: `webapp/routes/committees.py:305` (query omits amendment state)
- `TP-E-API-AMEND`: `webapp/routes/committees.py:320` (serialized filing history omits amendment state)
- `TP-E-UI-AMEND`: `webapp/templates/committees/detail_sbe.html:107` (filing history UI has no amended indicator)

### Per-Column Touchpoint Matrix

| Dataset.Table.Column | Raw reader / projection | Transform subset | SQL select/insert omission | Schema/model gap | API serializer omission | Frontend omission |
|---|---|---|---|---|---|---|
| fec_schedule_a_contributions.amendment_indicator | TP-A-RAW | TP-A-XFORM | TP-A-SQL | TP-A-SCHEMA | TP-A-API | TP-A-UI |
| fec_schedule_a_contributions.file_number | TP-A-RAW | TP-A-XFORM | TP-A-SQL | TP-A-SCHEMA | TP-A-API | TP-A-UI |
| fec_schedule_a_contributions.transaction_id | TP-A-RAW | TP-A-XFORM | TP-A-SQL | TP-A-SCHEMA | TP-A-API | TP-A-UI |
| fec_schedule_b_disbursements.amendment_indicator | TP-B-RAW | TP-B-XFORM | TP-B-SQL | TP-B-SCHEMA | TP-B-API/TP-B-CSV | TP-B-UI |
| fec_schedule_b_disbursements.disbursement_purpose_category | TP-B-RAW | TP-B-XFORM | TP-B-SQL | TP-B-SCHEMA | TP-B-API/TP-B-CSV | TP-B-UI |
| fec_schedule_b_disbursements.file_number | TP-B-RAW | TP-B-XFORM | TP-B-SQL | TP-B-SCHEMA | TP-B-API/TP-B-CSV | TP-B-UI |
| fec_schedule_b_disbursements.transaction_id | TP-B-RAW | TP-B-XFORM | TP-B-SQL | TP-B-SCHEMA | TP-B-API/TP-B-CSV | TP-B-UI |
| fec_schedule_e_independent_expenditures.is_notice | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_schedule_e_independent_expenditures.most_recent | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_schedule_e_independent_expenditures.file_number | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_schedule_e_independent_expenditures.previous_file_number | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_schedule_e_independent_expenditures.amendment_indicator | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_schedule_e_independent_expenditures.transaction_id | TP-C-RAW | TP-C-XFORM | TP-C-SQL | TP-C-SCHEMA | TP-C-API/TP-C-CSV | TP-C-UI |
| fec_candidate_committees.last_file_date | TP-D-RAW | TP-D-XFORM | TP-D-SQL | TP-D-SCHEMA | TP-D-API | TP-D-UI |
| fec_candidate_committees.first_file_date | TP-D-RAW | TP-D-XFORM | TP-D-SQL | TP-D-SCHEMA | TP-D-API | TP-D-UI |
| fec_candidate_committees.party_full | TP-D-RAW | TP-D-XFORM | TP-D-SQL | TP-D-SCHEMA | TP-D-API | TP-D-UI |
| isbe_filed_docs.CommitteeID | TP-E-RAW | TP-E-XFORM | n/a (already used in `WHERE` when table exists) | TP-E-SCHEMA | n/a | n/a |
| isbe_filed_docs.RptPdEndDate | TP-E-RAW | TP-E-XFORM | n/a (already selected as `reporting_period_end`) | TP-E-SCHEMA | n/a | n/a |
| isbe_filed_docs.RptPdBegDate | TP-E-RAW | TP-E-XFORM | n/a (already selected as `reporting_period_begin`) | TP-E-SCHEMA | n/a | n/a |
| isbe_filed_docs.Amend | TP-E-RAW | TP-E-XFORM | TP-E-SQL-AMEND | TP-E-SCHEMA | TP-E-API-AMEND | TP-E-UI-AMEND |
| isbe_filed_docs.DocName | TP-E-RAW | TP-E-XFORM | n/a (already selected as `doc_name`) | TP-E-SCHEMA | n/a | n/a |
| isbe_filed_docs.RcvdDateTime | TP-E-RAW | TP-E-XFORM | n/a (already selected as `received_datetime`) | TP-E-SCHEMA | n/a | n/a |

## Appendix A - Full Per-Column Profiles

The following appendix contains the full profiling block for every candidate column (null/empty/unique/top20/length/numeric/date/join metrics).

### fec_api_schedule_a_json.amendment_indicator
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 96551 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000031 | `join_rate`: n/a
- `value_length_dist`: min=1, median=1, p95=1.0, max=1
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: A (75999), N (18516), C (2036)

### fec_api_schedule_a_json.file_number
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 96551 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.001719 | `join_rate`: n/a
- `value_length_dist`: min=7, median=7, p95=7.0, max=7
- `numeric_stats`: min=1884750.0, median=1920121.0, p95=1930968.0, max=1939592.0
- `date_stats`: valid_rate=0.8589035846340276, earliest=1884-07-05, latest=1939-05-09
- `top_20_values`: 1920467 (5369), 1923772 (3621), 1920398 (3309), 1920429 (3133), 1931552 (2950), 1919764 (2820), 1902575 (2748), 1920733 (2203), 1920283 (2130), 1919408 (2112), 1920143 (1985), 1920385 (1949), 1901486 (1858), 1901918 (1819), 1919813 (1777), 1919707 (1702), 1902395 (1618), 1910558 (1584), 1902734 (1574), 1921249 (1564)

### fec_api_schedule_b_json.amendment_indicator
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 13477 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000223 | `join_rate`: n/a
- `value_length_dist`: min=1, median=1, p95=1.0, max=1
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: A (10713), N (2649), C (115)

### fec_api_schedule_b_json.disbursement_purpose_category
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P2/A3/G2/C1/R0
- `row_count`: 13477 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000816 | `join_rate`: n/a
- `value_length_dist`: min=5, median=5, p95=14.0, max=15
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: OTHER (9756), ADMINISTRATIVE (1816), MATERIALS (700), FUNDRAISING (353), ADVERTISING (348), TRAVEL (295), REFUNDS (123), CONTRIBUTIONS (73), TRANSFERS (7), EVENTS (5), LOAN-REPAYMENTS (1)

### fec_api_schedule_b_json.file_number
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 13477 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.012317 | `join_rate`: n/a
- `value_length_dist`: min=7, median=7, p95=7.0, max=7
- `numeric_stats`: min=1884750.0, median=1919919.0, p95=1931552.0, max=1946129.0
- `date_stats`: valid_rate=0.8826148252578467, earliest=1884-07-05, latest=1946-01-02
- `top_20_values`: 1920467 (859), 1919764 (448), 1920733 (411), 1901486 (377), 1920429 (367), 1921134 (348), 1919813 (340), 1887569 (329), 1920720 (318), 1920398 (312), 1923772 (305), 1910558 (295), 1901601 (264), 1887503 (254), 1919849 (235), 1931552 (225), 1930968 (214), 1894630 (210), 1927176 (207), 1924183 (206)

### fec_api_schedule_e_json.is_notice
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 5097 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000392 | `join_rate`: n/a
- `value_length_dist`: min=4, median=5, p95=5.0, max=5
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: False (2759), True (2338)

### fec_api_schedule_e_json.most_recent
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 5097 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000392 | `join_rate`: n/a
- `value_length_dist`: min=4, median=4, p95=4.199999999999818, max=5
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: True (4842), False (255)

### isbe_filed_docs_tsv.CommitteeID
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A2/G3/C2/R0
- `row_count`: 936014 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.032727 | `join_rate`: 0.999971
- `value_length_dist`: min=1, median=5.0, p95=5.0, max=5
- `numeric_stats`: min=1.0, median=16362.0, p95=36860.0, max=41090.0
- `date_stats`: n/a
- `top_20_values`: 1126 (1981), 497 (1746), 538 (1537), 675 (1175), 13732 (1150), 6239 (1083), 292 (994), 1132 (973), 17218 (952), 17589 (917), 665 (892), 627 (884), 1334 (867), 1028 (816), 13309 (809), 16283 (792), 1025 (751), 19328 (718), 175 (717), 1330 (712)

### fec_api_schedule_e_json.file_number
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 5097 | `null_rate`: 0.00157 | `empty_rate`: 0 | `unique_rate`: 0.217921 | `join_rate`: n/a
- `value_length_dist`: min=6, median=7, p95=7.0, max=7
- `numeric_stats`: min=308483.0, median=1555986.0, p95=1802050.9999999981, max=1946229.0
- `date_stats`: valid_rate=0.8257024955786991, earliest=1000-07-06, latest=9980-05-07
- `top_20_values`: 1560820 (153), 1561440 (145), 1291159 (86), 1630212 (84), 1603039 (84), 1329815 (54), 834697 (53), 834674 (50), 1599892 (48), 1630211 (45), 1308365 (40), 1305705 (39), 1599890 (36), 1878663 (31), 1611193 (30), 1630213 (30), 1603041 (30), 1539581 (30), 1602193 (29), 1677190 (26)

### fec_api_schedule_e_json.previous_file_number
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 5097 | `null_rate`: 0.007259 | `empty_rate`: 0 | `unique_rate`: 0.211265 | `join_rate`: n/a
- `value_length_dist`: min=5, median=7.0, p95=7.0, max=8
- `numeric_stats`: min=-9459476.0, median=1557784.0, p95=1804462.4000000013, max=1946229.0
- `date_stats`: valid_rate=0.808498023715415, earliest=1008-02-04, latest=9896-07-05
- `top_20_values`: 1560820 (153), 1561440 (145), 1273979 (86), 1603023 (84), 1581120 (84), 830512 (72), 1297779 (54), 834697 (53), 1582080 (48), 1561453 (45), 1299631 (40), 1282314 (39), 1568571 (36), 1260710 (36), 1849401 (31), 1611193 (30), 1603025 (30), 1594551 (30), 1539581 (30), 366486 (30)

### fec_api_schedule_e_json.amendment_indicator
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A1/G3/C1/R0
- `row_count`: 5097 | `null_rate`: 0.008044 | `empty_rate`: 0 | `unique_rate`: 0.000593 | `join_rate`: n/a
- `value_length_dist`: min=1, median=1.0, p95=1.0, max=1
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: N (3306), A (1744), T (6)

### isbe_filed_docs_tsv.RptPdEndDate
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A2/G3/C2/R0
- `row_count`: 936014 | `null_rate`: 0.132433 | `empty_rate`: 0.132433 | `unique_rate`: 0.008971 | `join_rate`: n/a
- `value_length_dist`: min=19, median=19, p95=19.0, max=19
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=1903-06-30, latest=3023-03-30
- `top_20_values`: 2018-12-31 00:00:00 (10768), 2022-12-31 00:00:00 (10722), 2022-06-30 00:00:00 (9913), 2022-09-30 00:00:00 (9823), 2018-09-30 00:00:00 (9788), 2018-03-31 00:00:00 (9710), 2023-03-31 00:00:00 (9568), 2012-12-31 00:00:00 (9274), 2024-12-31 00:00:00 (9172), 2019-03-31 00:00:00 (8961), 2016-12-31 00:00:00 (8959), 2014-12-31 00:00:00 (8818), 2024-09-30 00:00:00 (8798), 2020-12-31 00:00:00 (8794), 2012-03-31 00:00:00 (8775), 2012-09-30 00:00:00 (8585), 2016-09-30 00:00:00 (8563), 2020-03-31 00:00:00 (8535), 2025-03-31 00:00:00 (8471), 2020-09-30 00:00:00 (8441)

### isbe_filed_docs_tsv.RptPdBegDate
- `recommended_action`: integrate_now | `priority_score`: 6 | `value_scores`: P3/A2/G3/C2/R0
- `row_count`: 936014 | `null_rate`: 0.133137 | `empty_rate`: 0.133137 | `unique_rate`: 0.009742 | `join_rate`: n/a
- `value_length_dist`: min=19, median=19.0, p95=19.0, max=19
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=1903-01-01, latest=9859-01-01
- `top_20_values`: 2018-10-01 00:00:00 (10621), 2022-10-01 00:00:00 (10543), 1998-07-01 00:00:00 (10113), 1998-01-01 00:00:00 (9610), 2022-04-01 00:00:00 (9558), 2018-07-01 00:00:00 (9532), 2022-07-01 00:00:00 (9417), 1996-07-01 00:00:00 (9369), 2018-01-01 00:00:00 (9304), 2023-01-01 00:00:00 (9179), 2012-10-01 00:00:00 (9135), 2024-10-01 00:00:00 (9017), 1994-07-01 00:00:00 (8917), 2016-10-01 00:00:00 (8817), 2010-07-01 00:00:00 (8767), 1992-07-01 00:00:00 (8682), 2019-01-01 00:00:00 (8648), 2020-10-01 00:00:00 (8643), 2014-10-01 00:00:00 (8615), 2024-07-01 00:00:00 (8572)

### fec_api_candidate_committees_json.last_file_date
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P3/A1/G2/C1/R0
- `row_count`: 120 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.341667 | `join_rate`: n/a
- `value_length_dist`: min=10, median=10.0, p95=10.0, max=10
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=2023-10-11, latest=2026-02-11
- `top_20_values`: 2026-01-31 (44), 2026-01-30 (20), 2026-01-29 (7), 2026-02-01 (5), 2025-11-25 (4), 2026-01-28 (3), 2026-01-19 (2), 2026-01-14 (2), 2026-02-02 (1), 2025-08-15 (1), 2025-04-15 (1), 2023-10-11 (1), 2025-06-27 (1), 2026-02-04 (1), 2026-02-05 (1), 2026-02-11 (1), 2025-11-23 (1), 2026-01-16 (1), 2025-08-13 (1), 2025-11-24 (1)

### fec_api_schedule_a_json.transaction_id
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P2/A2/G2/C1/R0
- `row_count`: 96551 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.570341 | `join_rate`: n/a
- `value_length_dist`: min=3, median=8, p95=20.0, max=20
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: SA11AI.4181 (19), SA11AI.4168 (17), SA11AI.4166 (17), SA11AI.4106 (14), SA11AI.4179 (13), SA11AI.4170 (13), SA11AI.4518 (12), SA11AI.4188 (12), SA11AI.4163 (12), SA11AI.4206 (12), SA11AI.4318 (12), SA11AI.4175 (12), SA11AI.4222 (12), SA11AI.4172 (12), SA11AI.4421 (12), SA11AI.4135 (12), SA11AI.4173 (11), SA11AI.4178 (11), SA11AI.4109 (11), SA11AI.4108 (11)

### fec_api_schedule_b_json.transaction_id
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P2/A2/G2/C1/R0
- `row_count`: 13477 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.968391 | `join_rate`: n/a
- `value_length_dist`: min=3, median=9, p95=20.0, max=20
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: SB17.4284 (6), SB17.4279 (6), SB17.4160 (5), SB17.4150 (5), SB17.4114 (5), SB17.4292 (5), SB17.4293 (5), SB17.4274 (5), SB17.4278 (5), SB17.4286 (5), SB17.4273 (5), SB17.4266 (5), SB17.4188 (4), SB17.4158 (4), SB17.4139 (4), SB17.4143 (4), SB17.4136 (4), SB17.4168 (4), SB17.4155 (4), SB17.4146 (4)

### isbe_filed_docs_tsv.Amend
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P3/A1/G3/C2/R0
- `row_count`: 936014 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000002 | `join_rate`: n/a
- `value_length_dist`: min=4, median=5.0, p95=5.0, max=5
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: False (836680), True (99334)

### isbe_filed_docs_tsv.DocName
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P3/A2/G2/C2/R0
- `row_count`: 936014 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000014 | `join_rate`: n/a
- `value_length_dist`: min=3, median=9.0, p95=25.0, max=28
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: Quarterly (275443), A-1 (256413), Semiannual (155492), Pre-election (81834), Statement of Organization (58639), Nonparticipation (55313), Final (29765), Letter/Correspondence (16746), Annual (3456), B-1 (1847), Raffle Report (629), Post-election (409), Notification of Self Funding (28)

### isbe_filed_docs_tsv.RcvdDateTime
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P3/A1/G3/C2/R0
- `row_count`: 936014 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.917555 | `join_rate`: n/a
- `value_length_dist`: min=19, median=19.0, p95=29.0, max=29
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=1988-12-20, latest=2026-02-15
- `top_20_values`: 2011-01-03 16:00:00 (55), 2011-01-05 16:10:00 (51), 2010-12-06 13:40:00 (33), 2010-10-18 16:25:00 (31), 2024-10-15 00:00:00 (25), 2024-10-07 00:00:00 (24), 2025-10-03 00:00:00 (24), 2010-12-27 15:47:00 (23), 2012-10-09 16:10:00 (23), 2010-12-29 14:00:00 (22), 2011-01-03 13:00:00 (22), 2024-12-13 00:00:00 (22), 1991-01-18 15:05:00 (19), 2026-01-05 00:00:00 (19), 1998-10-15 09:34:00 (17), 1999-02-03 16:00:00 (17), 2015-01-05 16:19:00 (17), 1991-01-17 10:15:00 (16), 1998-03-02 10:27:00 (16), 1998-10-19 12:58:00 (16)

### fec_api_schedule_e_json.transaction_id
- `recommended_action`: integrate_now | `priority_score`: 5 | `value_scores`: P2/A2/G2/C1/R0
- `row_count`: 5097 | `null_rate`: 0.009221 | `empty_rate`: 0 | `unique_rate`: 0.611485 | `join_rate`: n/a
- `value_length_dist`: min=3, median=10.0, p95=20.0, max=20
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: F57.000001 (12), F57.000002 (10), F57.4335 (7), F57.4672 (6), F57.5359 (6), F57.5355 (6), F57.4550 (6), F57.4755 (6), F57.4549 (6), F57.4754 (6), F57.4548 (6), F57.4547 (6), F57.4499 (6), F57.5357 (6), F57.5356 (6), F57.5358 (6), F57.4453 (6), F57.000004 (5), WFT2020924184-1 (5), WFT20209241758-1 (5)

### fec_api_candidate_committees_json.first_file_date
- `recommended_action`: integrate_now | `priority_score`: 4 | `value_scores`: P2/A1/G2/C1/R0
- `row_count`: 120 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.858333 | `join_rate`: n/a
- `value_length_dist`: min=10, median=10.0, p95=10.0, max=10
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=1995-08-24, latest=2025-11-18
- `top_20_values`: 2025-05-15 (4), 2025-05-13 (3), 2025-08-26 (3), 2025-06-02 (2), 2025-07-10 (2), 2025-05-05 (2), 2025-07-16 (2), 2025-10-01 (2), 2025-09-19 (2), 2025-04-30 (2), 2025-05-06 (2), 2025-07-08 (2), 2025-05-07 (2), 2024-06-24 (1), 2008-11-24 (1), 2023-07-07 (1), 2021-05-31 (1), 2019-10-21 (1), 2022-01-27 (1), 2021-12-07 (1)

### fec_api_candidate_committees_json.party_full
- `recommended_action`: integrate_now | `priority_score`: 3 | `value_scores`: P2/A1/G1/C1/R0
- `row_count`: 120 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.025 | `join_rate`: n/a
- `value_length_dist`: min=11, median=16.0, p95=16.0, max=16
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: DEMOCRATIC PARTY (75), REPUBLICAN PARTY (40), INDEPENDENT (5)

### fec_api_schedule_a_json.entity_type_desc
- `recommended_action`: integrate_later | `priority_score`: 4 | `value_scores`: P2/A2/G1/C1/R0
- `row_count`: 96551 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000073 | `join_rate`: n/a
- `value_length_dist`: min=9, median=10, p95=26.0, max=26
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: INDIVIDUAL (61628), POLITICAL ACTION COMMITTEE (24543), ORGANIZATION (8760), CANDIDATE (982), OTHER COMMITTEE (398), CAMPAIGN COMMITTEE (208), POLITICAL PARTY COMMITTEE (32)

### fec_api_schedule_a_json.pdf_url
- `recommended_action`: integrate_later | `priority_score`: 4 | `value_scores`: P2/A1/G2/C1/R0
- `row_count`: 96551 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.195814 | `join_rate`: n/a
- `value_length_dist`: min=59, median=59, p95=59.0, max=59
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378752 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378710 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791012729 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789242 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789241 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789240 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789228 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789226 (12), https://docquery.fec.gov/cgi-bin/fecimg/?202510159790807896 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202510149790789892 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202512019793354502 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378750 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378741 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378719 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202512059793378703 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791168432 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791563125 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202601299794401096 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202601299794401061 (11), https://docquery.fec.gov/cgi-bin/fecimg/?202601299794401041 (11)

### fec_api_schedule_b_json.pdf_url
- `recommended_action`: integrate_later | `priority_score`: 4 | `value_scores`: P2/A1/G2/C1/R0
- `row_count`: 13477 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.341025 | `join_rate`: n/a
- `value_length_dist`: min=59, median=59, p95=59.0, max=59
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473527 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473517 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473494 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473500 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473489 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473515 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473514 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473513 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473510 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473492 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473498 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473519 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473504 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473511 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473525 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473505 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473502 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473499 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473493 (3), https://docquery.fec.gov/cgi-bin/fecimg/?202510159791473524 (3)

### isbe_investments_tsv.CurrentValue
- `recommended_action`: integrate_later | `priority_score`: 2 | `value_scores`: P2/A3/G1/C3/R1
- `row_count`: 23697 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.289362 | `join_rate`: n/a
- `value_length_dist`: min=1, median=6, p95=9.0, max=10
- `numeric_stats`: min=-25000.0, median=11739.78, p95=250000.0, max=4828760.78
- `date_stats`: n/a
- `top_20_values`: 0 (2957), 100000 (1044), 50000 (445), 25000 (427), 20000 (314), 10000 (312), 30000 (257), 5000 (244), 7500 (196), 250 (175), 200000 (159), 150000 (153), 15000 (151), 1500 (133), 1000 (128), 50 (119), 6000 (104), 5480.23 (102), 40000 (91), 24.61 (85)

### isbe_investments_tsv.PurchasePrice
- `recommended_action`: integrate_later | `priority_score`: 2 | `value_scores`: P2/A3/G1/C3/R1
- `row_count`: 23697 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.050682 | `join_rate`: n/a
- `value_length_dist`: min=1, median=5, p95=8.0, max=10
- `numeric_stats`: min=0.0, median=20000.0, p95=250000.0, max=21916316.0
- `date_stats`: n/a
- `top_20_values`: 100000 (2004), 50000 (1179), 10000 (1154), 25000 (952), 20000 (827), 30000 (658), 5000 (491), 40000 (414), 15000 (293), 75000 (281), 150000 (239), 200000 (237), 35000 (232), 7500 (207), 11 (196), 60000 (184), 250 (181), 34857.24 (148), 500 (147), 26806.49 (142)

### isbe_filed_docs_tsv.FiledDocType
- `recommended_action`: integrate_later | `priority_score`: 2 | `value_scores`: P2/A2/G1/C2/R1
- `row_count`: 936014 | `null_rate`: 0.572099 | `empty_rate`: 0.572099 | `unique_rate`: 0.000022 | `join_rate`: n/a
- `value_length_dist`: min=2, median=2, p95=2, max=2
- `numeric_stats`: min=5.0, median=35.0, p95=45.0, max=45.0
- `date_stats`: n/a
- `top_20_values`: 45 (146450), 10 (78465), 25 (58111), 35 (53790), 05 (32176), 30 (18624), 40 (9040), 20 (3456), 15 (409)

### isbe_filed_docs_tsv.Archived
- `recommended_action`: integrate_later | `priority_score`: 1 | `value_scores`: P1/A1/G2/C2/R1
- `row_count`: 936014 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.000002 | `join_rate`: n/a
- `value_length_dist`: min=4, median=5.0, p95=5.0, max=5
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: False (488201), True (447813)

### fec_api_candidate_committees_json.website
- `recommended_action`: integrate_later | `priority_score`: 1 | `value_scores`: P1/A1/G1/C1/R1
- `row_count`: 120 | `null_rate`: 0.233333 | `empty_rate`: 0 | `unique_rate`: 0.98913 | `join_rate`: n/a
- `value_length_dist`: min=2, median=22.0, p95=34.0, max=42
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: WWW.KOPPIEFORCONGRESS.COM (2), HTTPS://WWW.CHANGE.ORG/NATHANEBILLIPSUSREP (1), HTTP://WWW.QUIGLEYFORCONGRESS.COM (1), KINACOLLINS.COM (1), WWW.KINACOLLINS.COM (1), HTTPS://MARYMILLERFORCONGRESS.COM (1), HTTPS://WWW.DELIAFORCONGRESS.COM/ (1), HTTP://WWW.SCHNEIDERFORCONGRESS.COM (1), HTTPS://NIKKIFORCONGRESS.COM/ (1), VOTEPIERCE4CONGRESS.COM (1), HTTP://WWW.ERICFORILLINOIS.COM/ (1), MELISSAFORCONGRESS.ORG (1), RICE4CONGRESS.COM (1), HTTP://BOSTFORCONGRESS.COM (1), LOYDFORCONGRESS.COM (1), HTTPS://CHRISTIANMAXWELLFORCONGRESS.COM/ (1), WWW.JESSEJACKSONJR.ORG (1), JESSEJACKSONJRFORCONGRESS.COM (1), REGISFORCONGRESS.COM (1), FRANCEFORCONGRESS.COM (1)

### isbe_investments_tsv.CommitteeID
- `recommended_action`: integrate_later | `priority_score`: 0 | `value_scores`: P1/A2/G1/C3/R1
- `row_count`: 23697 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.021902 | `join_rate`: 1
- `value_length_dist`: min=1, median=4, p95=5.0, max=5
- `numeric_stats`: min=4.0, median=7041.0, p95=32198.0, max=40472.0
- `date_stats`: n/a
- `top_20_values`: 4815 (1002), 8081 (761), 14968 (743), 4384 (606), 10097 (583), 5777 (537), 13255 (455), 1330 (433), 124 (376), 4410 (327), 183 (307), 4758 (303), 14566 (296), 14895 (292), 38916 (285), 724 (265), 12444 (251), 126 (246), 7985 (237), 15478 (234)

### isbe_investments_tsv.Description
- `recommended_action`: integrate_later | `priority_score`: 0 | `value_scores`: P1/A2/G1/C3/R1
- `row_count`: 23697 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.046082 | `join_rate`: n/a
- `value_length_dist`: min=2, median=19, p95=47.0, max=88
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: Investment purchase (2332), Certificate of Deposit (1657), Loan made (1282), certificate of deposit (606), CD (604), CD Purchase (583), Purchase CD (531), cd purchase (291), interest (289), Purchase of CD (262), Investment (259), Purchase of Certificate of Deposit (259), CD purchase (250), transfer investment from Bank of America 4/1/05 (231), Mutual Funds (226), money market (192), investment (184), Mutual Fund (181), CD PURCHASE (168), Donation (156)

### isbe_investments_tsv.FiledDocID
- `recommended_action`: integrate_later | `priority_score`: 0 | `value_scores`: P1/A2/G1/C3/R1
- `row_count`: 23697 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.421024 | `join_rate`: 0.999747
- `value_length_dist`: min=6, median=6, p95=6.0, max=6
- `numeric_stats`: min=173016.0, median=497500.0, p95=975629.0, max=999966.0
- `date_stats`: valid_rate=0.8173186479301178, earliest=1730-01-06, latest=9999-06-06
- `top_20_values`: 232770 (72), 221639 (68), 247484 (64), 216971 (63), 200847 (41), 263161 (37), 266411 (37), 276905 (37), 295685 (37), 376852 (37), 996603 (36), 260668 (35), 996795 (35), 203115 (31), 248830 (29), 190749 (28), 355996 (24), 360099 (24), 360322 (24), 179976 (23)

### irs527_full_data_record_1_org_registration.pos_41
- `recommended_action`: investigate_semantics | `priority_score`: 1 | `value_scores`: P1/A1/G2/C2/R1
- `row_count`: 75273 | `null_rate`: 0.015424 | `empty_rate`: 0 | `unique_rate`: 0.999555 | `join_rate`: n/a
- `value_length_dist`: min=19, median=19.0, p95=19.0, max=19
- `numeric_stats`: n/a
- `date_stats`: valid_rate=1.0, earliest=2001-05-13, latest=2026-02-07
- `top_20_values`: 2007-03-05 00:00:00 (9), 2007-03-02 00:00:00 (5), 2007-03-06 00:00:00 (3), 2002-07-09 00:08:14 (2), 2002-07-15 11:31:54 (2), 2002-07-15 13:46:16 (2), 2002-07-15 14:12:58 (2), 2002-07-15 17:19:03 (2), 2002-07-15 17:26:31 (2), 2002-07-15 17:52:30 (2), 2002-07-15 18:33:45 (2), 2004-04-15 22:27:25 (2), 2011-04-15 14:49:18 (2), 2014-02-03 14:30:29 (2), 2016-01-19 11:18:06 (2), 2019-03-01 14:21:40 (2), 2020-07-15 14:23:48 (2), 2021-11-05 14:52:09 (2), 2022-07-25 17:05:49 (2), 2023-09-01 14:13:34 (2)

### irs527_full_data_record_1_org_registration.pos_42
- `recommended_action`: investigate_semantics | `priority_score`: -1 | `value_scores`: P0/A1/G2/C2/R2
- `row_count`: 75273 | `null_rate`: 0.015464 | `empty_rate`: 0.00004 | `unique_rate`: 0.000027 | `join_rate`: n/a
- `value_length_dist`: min=1, median=1, p95=1.0, max=1
- `numeric_stats`: min=0.0, median=1.0, p95=1.0, max=1.0
- `date_stats`: n/a
- `top_20_values`: 1 (59978), 0 (14131)

### irs527_full_data_record_1_org_registration.pos_43
- `recommended_action`: investigate_semantics | `priority_score`: -1 | `value_scores`: P0/A1/G2/C2/R2
- `row_count`: 75273 | `null_rate`: 0.015464 | `empty_rate`: 0.00004 | `unique_rate`: 0.000027 | `join_rate`: n/a
- `value_length_dist`: min=1, median=1, p95=1.0, max=1
- `numeric_stats`: min=0.0, median=1.0, p95=1.0, max=1.0
- `date_stats`: n/a
- `top_20_values`: 1 (57829), 0 (16280)

### fec_api_candidate_committees_json.email
- `recommended_action`: do_not_surface_pii | `priority_score`: -2 | `value_scores`: P0/A1/G1/C1/R3
- `row_count`: 120 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.916667 | `join_rate`: n/a
- `value_length_dist`: min=15, median=29.0, p95=57.0, max=62
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: FEC@CAPCOMPLIANCE.COM (5), FEC@CFOCONSULTS.COM (4), PHILACOLLINS@YAHOO.COM (2), COMPLIANCE@HOWDYHEARTLAND.COM (2), COMPLIANCE@KATZCOMPLIANCE.COM (2), NATHANEBILLIPSFORUSREP2026@GMAIL.COM;NATHANBILLIPSJR@GMAIL.COM (1), INFO@KINACOLLINS.COM;JOSH@BLUERAVENCAMPAIGNS.COM (1), KINACOLLINS3@GMAIL.COM;DEVERIA@TAXABS.COM (1), JASON@TABULARIUS.PRO;BENDEMARZO@GMAIL.COM (1), JACKSON@ACUITYPOLITICS.COM (1), DELIAFORCONGRESS@GMAIL.COM (1), TMOOSE@HDAFEC.COM;LLISKER@HDAFEC.COM (1), VICTORIA@SPRUCESTREETCOMP.COM;ASHLEE@SPRUCESTREETCOMP.COM (1), JANICA@PCMSLLC.COM;BSS820@GMAIL.COM (1), PIERCE4CONGRESS@GMAIL.COM;SEAN@WESTPALMACCOUNTING.COM (1), LUPE.C361@GMAIL.COM (1), MARK@RICE4CONGRESS.COM;SMARTIN314@SBCGLOBAL.NET (1), CHARLIEINCONGRESS@GMAIL.COM;THECGCO@AOL.COM (1), MIKEBOST@PDSCOMPLIANCE.COM;ADMIN@PDSCOMPLIANCE.COM (1), INFO@LOYDFORCONGRESS.COM;LIZ@LIZCURTISASSOCIATES.COM (1)

### fec_api_candidate_committees_json.treasurer_phone
- `recommended_action`: do_not_surface_pii | `priority_score`: -2 | `value_scores`: P0/A1/G1/C1/R3
- `row_count`: 120 | `null_rate`: 0.058333 | `empty_rate`: 0 | `unique_rate`: 0.867257 | `join_rate`: n/a
- `value_length_dist`: min=10, median=10, p95=10.0, max=10
- `numeric_stats`: min=2022208411.0, median=6188768071.0, p95=8478392907.2, max=9546218806.0
- `date_stats`: valid_rate=0.8141592920353983, earliest=2024-09-08, latest=9546-02-01
- `top_20_values`: 2025446960 (6), 2022407451 (3), 2025480880 (3), 7734498579 (2), 7035497705 (2), 8474313882 (2), 2026281580 (2), 5085431720 (2), 4014540990 (2), 4142155025 (1), 2022208411 (1), 7736782564 (1), 8472931100 (1), 8474415911 (1), 5618994412 (1), 4026893553 (1), 7738953815 (1), 2066827328 (1), 5634990667 (1), 8883160002 (1)

### fec_api_candidate_committees_json.form_type
- `recommended_action`: exclude_low_signal | `priority_score`: 0 | `value_scores`: P0/A0/G0/C0/R0
- `row_count`: 120 | `null_rate`: 0 | `empty_rate`: 0 | `unique_rate`: 0.008333 | `join_rate`: n/a
- `value_length_dist`: min=2, median=2.0, p95=2.0, max=2
- `numeric_stats`: n/a
- `date_stats`: n/a
- `top_20_values`: F1 (120)

## Appendix B - Staging/DB Presence By Candidate Column

| Dataset | Table | Column | stage_column_exists | join_rate |
|---|---|---|---|---:|
| fec_api_candidate_committees_json | fec_candidate_committees | email | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | first_file_date | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | form_type | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | last_file_date | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | party_full | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | treasurer_phone | false | n/a |
| fec_api_candidate_committees_json | fec_candidate_committees | website | false | n/a |
| fec_api_schedule_a_json | fec_schedule_a_contributions | amendment_indicator | false | n/a |
| fec_api_schedule_a_json | fec_schedule_a_contributions | entity_type_desc | false | n/a |
| fec_api_schedule_a_json | fec_schedule_a_contributions | file_number | false | n/a |
| fec_api_schedule_a_json | fec_schedule_a_contributions | pdf_url | false | n/a |
| fec_api_schedule_a_json | fec_schedule_a_contributions | transaction_id | false | n/a |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | amendment_indicator | false | n/a |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | disbursement_purpose_category | false | n/a |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | file_number | false | n/a |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | pdf_url | false | n/a |
| fec_api_schedule_b_json | fec_schedule_b_disbursements | transaction_id | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | amendment_indicator | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | file_number | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | is_notice | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | most_recent | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | previous_file_number | false | n/a |
| fec_api_schedule_e_json | fec_schedule_e_independent_expenditures | transaction_id | false | n/a |
| irs527_full_data_record_1_org_registration | irs527_organizations | pos_41 | false | n/a |
| irs527_full_data_record_1_org_registration | irs527_organizations | pos_42 | false | n/a |
| irs527_full_data_record_1_org_registration | irs527_organizations | pos_43 | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | Amend | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | Archived | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | CommitteeID | false | 0.999971 |
| isbe_filed_docs_tsv | isbe_filed_docs | DocName | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | FiledDocType | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | RcvdDateTime | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | RptPdBegDate | false | n/a |
| isbe_filed_docs_tsv | isbe_filed_docs | RptPdEndDate | false | n/a |
| isbe_investments_tsv | isbe_investments | CommitteeID | false | 1.000000 |
| isbe_investments_tsv | isbe_investments | CurrentValue | false | n/a |
| isbe_investments_tsv | isbe_investments | Description | false | n/a |
| isbe_investments_tsv | isbe_investments | FiledDocID | false | 0.999747 |
| isbe_investments_tsv | isbe_investments | PurchasePrice | false | n/a |
