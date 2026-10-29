# D4 — `isbe_most_recent_filings` picks a NULL-period filing as "most recent" (ETL fix)

**Owner:** Fable (orchestrator) · **Builder:** opus-builder-a · **Branch:** `d4-etl-nulls-last` · **Scope class:** Narrow (one ORDER BY in the ETL DDL) with a dollar blast radius, so the data-integrity specialist measures before acceptance · **Budget:** 90 min after the 07:21Z scope change (M1 20 · M2 15 · M3 35 · M4 10 · slack 10) · **Ordered by Devin 2026-10-29 ≈06:55Z.**

## Interpretation

`scripts/isbe_sunshine_etl.py` ≈461–493 builds `isbe_most_recent_filings` with two `DISTINCT ON` layers. The inner layer orders `received_datetime DESC NULLS LAST`; the **outer** layer orders `f.committee_id, f.reporting_period_end DESC` with no `NULLS LAST`. PostgreSQL sorts NULLs first under DESC, so any committee that has one periodic filing with a NULL `reporting_period_end` gets that filing as its "most recent" and `m.reporting_period_end` is NULL for the committee. Downstream (same file ≈496–540) `isbe_condensed_receipts` and `isbe_condensed_expenditures` take their first UNION branch as `r.received_date > COALESCE(m.reporting_period_end, '1900-01-01')`, i.e. **every raw row of that committee**, including the superseded versions of amended filings that the second branch exists to drop. Specialist estimate (8b review, 2026-10-29): 478 committees, ≈$32M of duplicated receipts. Fix: `ORDER BY f.committee_id, f.reporting_period_end DESC NULLS LAST`. Nothing else changes.

## Success criteria

1. A PostgreSQL test executes the real `isbe_most_recent_filings` DDL (extracted from the ETL source, not copied) against fixture tables and shows a committee with a dated quarterly and a NULL-period quarterly resolving to the dated one; red on the current source, green after the fix.
2. A second assertion executes the real `isbe_condensed_receipts` DDL on the same fixtures and shows a receipt attached to a superseded earlier version of the same period absent from the condensed view (present under the bug).
3. Local measurement (read-only, `psql ilcf`): the count of committees whose view row has a NULL `reporting_period_end` while a dated periodic filing exists, and the receipts dollars that leave the condensed set under the fixed selection, both before/after by CTE (do not rebuild the local matviews; the DB is shared).
4. Docs updated: ETL comment, `.claude/data-dictionary.md` quirk line for the view, `docs/data_integrity_checks.md` where D4 is described (grep `D4`), and the prod step written out for Devin.

## Steps

| # | Step | Files | Verify |
|---|---|---|---|
| M1 | Tests first (red): `tests/test_isbe_most_recent_filings_view.py` — helper that pulls each `CREATE MATERIALIZED VIEW <name> AS (...)` statement out of `scripts/isbe_sunshine_etl.py` by regex (up to the next `CREATE UNIQUE INDEX ON <name>`), fixtures `isbe_committees(id,name,type,active)`, `isbe_filed_docs(id,committee_id,doc_name,reporting_period_begin,reporting_period_end,received_datetime)`, `isbe_d2_reports(filed_doc_id,end_funds_available,total_investments,total_debts,inkind_itemized,inkind_non_itemized)`, `isbe_receipts(...)` with every column the condensed DDL's `r.*` needs for the assertions (id, committee_id, filed_doc_id, received_date, amount, archived, d2_part, first_name, last_name, address1, address2, city, state, zipcode). Fictional committee only. Then the measurement queries (criterion 3) with results pasted in the report. | the new test | `/opt/anaconda3/bin/python3 -m pytest -q -p no:cacheprovider tests/test_isbe_most_recent_filings_view.py` → red (quote the failure) |
| M2 | Fix + docs: `NULLS LAST` on the outer ORDER BY, one comment line naming the trap; dictionary + integrity-checks notes; report the lightest prod path to rebuild the three matviews (the definition changes, so `REFRESH` is not enough: find whether the ETL exposes a views-only entry point; otherwise `sunshine-import --bulk-dir Bulk_download` on the existing files with `ilcf-web.service` stopped for the compat swap, then `refresh-analytics --with-snapshot`; estimate runtime). Deliver the MT-028 text (manual test: a committee from the affected list — id only — whose donors-page total should now match its sworn D-2 total) in the report, NOT in `docs/manual_tests.md`. | `scripts/isbe_sunshine_etl.py`, `.claude/data-dictionary.md`, `docs/data_integrity_checks.md` | M1 green; `tests/test_isbe_sunshine_patterns.py` green; ruff `--select E9,F63,F7,F82` |

## Harness (binding)

- Own clone: `git clone -q /Users/devin/Illinois_campaign_finance /private/tmp/claude-501/-Users-devin-Illinois-campaign-finance/23a15782-b190-4aa5-bc94-93abd8adfe50/scratchpad/d4-clone && cd …/d4-clone && git remote set-url origin https://github.com/Doommen3/Illinois_campaign_finance.git && git fetch -q origin && git checkout -q -B d4-etl-nulls-last origin/main` (main is `48e5399`). Never edit the main tree. Never push. Never stage `.claude/plans/*` or `to_do/*`. Do not touch `cli/commands.py`, `CLAUDE.md`, `README.md`, `database/bulk_download_loader.py`, `docs/manual_tests.md`, `docs/runbooks/deployment.md` (doc lines for them go in the report).
- Interpreter `/opt/anaconda3/bin/python3`; pytest `-p no:cacheprovider`; the suite uses `ilcf_test` (conftest builds a schema per test). Red tests are committed first (`tests: … (red)`), the fix second. Quote heredoc delimiters (`<<'SQL'`). Local `psql ilcf` is read-only for you.
- Stop at the end of M1 and M2 with: commits (sha + line), the red and green command lines with their result lines, elapsed minutes, deviations. Fable re-runs red/green in a separate clone.

## Scope change 07:21Z (Fable, on the specialist's BLOCKING verdict)

The branch-2 picker of `isbe_condensed_receipts` / `isbe_condensed_expenditures` (`DISTINCT ON (begin, end, committee) ORDER BY …, received_datetime DESC NULLS LAST`) has no deterministic tiebreak and prefers a later empty A-1 / Letter / Statement of Organization or a dated archived original over a live sworn report. NULLS LAST alone exposes 453 committees to it (committee 32762 Q4 2025: a $25.5M live quarterly ties with an archived A-1 on NULL received_datetime; the pick is plan-dependent — the builder's $32.1M figure vs $6.66M under another plan). Decision: ship D4 with the picker rule `EXISTS (live D-2) DESC, received_datetime DESC NULLS LAST, id DESC` in both views (id order disagrees with filing time in 0.06 % of same-period pairs). Steps M3 (tests for the three defects, the ORDER BY change, all-committee read-only re-measure with live twins only) and M4 (docs + MT-028 refresh). Specialist second pass after M3. Prod path unchanged.

## Log
- 06:58Z plan written; builder dispatched.
- 07:13Z M1 `35f7f43` red confirmed by Fable (2 failed); local measure 453 committees, live rows leaving $34.5M / part-1 $32.1M.
- 07:16Z M2 `e91b9b6` green confirmed (11 passed, ruff clean); `create_materialized_views` / `create_compat_views` exist; MT-028 draft + prod commands in the scratchpad `mt-028-draft.md`.
- 07:19Z specialist `integrity-8b` BLOCKING (report in the session transcript): figure reproduced exactly but plan-dependent; 6,686 rows / $4.29M wrongly lost under a deterministic variant; four failure patterns; 25 stay-NULL committees clean.
- 07:21Z scope change above; M3 dispatched.
