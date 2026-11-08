# IRS 527: load 527s that pay Illinois recipients, and show conduits honestly

Devin, 2026-11-08 ("lets make a plan to do the alternative and how to display conduits"), after the 527 reload of
the same night (`docs/runbooks/irs527_data.md` "Status (2026-11-08)", `docs/runbooks/deployment.md` 2026-11-08 block).
Scope class: **Cross-Component** (loader + schema + seven display surfaces + one match job + prod reload).
Owner: a fresh session (this plan is the brief). Builder: one capable executor; the orchestrator re-runs every
verification command. Devin's gates are marked **DEVIN**.

## 0. Decision and definitions

**Today's rule** (`80ebc3b`): an org loads only if its registered address is in Illinois; all its records load.
1,959 EINs, 37,693 expenditure rows (2026-11-07 IRS build).

**New rule (this plan):** an org also loads when at least one of its Schedule B expenditures goes to an Illinois
recipient (`state = 'IL'` on the B record). For such a *payee-qualified* org (address outside Illinois) only its
Illinois-recipient expenditure rows load; its registration rows, reports, directors and related orgs load in full
(they are small). Illinois-addressed orgs keep loading everything. Contributions keep the existing wide rule (any
org with an Illinois contributor). Measured on the 2026-11-07 build: 2,486 EINs (+527), 655,946 expenditure rows
(+618,253), of which ActBlue Non-Federal (EIN 202517748) is 572,927 — purposes "Political contribution" 572,177,
"Refund" 746.

**Conduit:** a 527 whose Schedule B rows are earmarked contributions from individual donors passed through to the
recipient, not the organization's own spending. Conduit rows are **never** counted as 527 spending or dark money;
they get their own labelled surface. Registry seeded with ActBlue Non-Federal; step 2 finds any others.

**Display vocabulary** (use these exact words in templates and docs):
- "Illinois connection: registered in Illinois" / "Illinois connection: paid Illinois recipients".
- "Pass-through contributions" for conduit rows; never "expenditures", "spending" or "dark money" for them.
- Banner on a payee-qualified org page: "Listed because it paid Illinois recipients. Only its payments to Illinois
  recipients are loaded (N of M rows in the IRS file)."
- Banner on a conduit org page: "Conduit organization. Its Schedule B rows are earmarked contributions from
  individual donors routed to the recipient, not the organization's own spending; they are shown as pass-through
  contributions and excluded from 527 spending totals."

## 1. Steps

Each step: target files → verification command. Steps 1–3 and 5–8 are code; 4 is a measurement; 9–11 are the
rollout. Run the full suite (`pytest -q -m "not integration" -p no:cacheprovider`) at the end of step 8 and before
step 10, in a verify worktree (memory: never check out inside a builder's clone).

| # | Step | Target files | Verification |
|---|---|---|---|
| 1 | **Loader rule.** In `load_irs527_full_file`: `il_addressed = _collect_illinois_eins(f, include_contributions=False, include_expenditures=False)`; `il_eins = _collect_illinois_eins(f, include_contributions=False)` (addressed ∪ payee); `contribution_eins` unchanged. Orgs/reports/directors/related/election authorities filter on `il_eins`. Expenditures: `ein in il_eins and (ein in il_addressed or _is_illinois_expenditure(parsed))`. Count `expenditures_skipped_out_of_state` separately in `stats`. Update the `illinois_only` docstring and the `--illinois-only` help text (`cli/commands.py`, one line). Also record per-EIN totals from the file: see step 3. | `database/irs527_loader.py`, `cli/commands.py`, `tests/test_irs527_loader.py` | New tests: (a) CA org with an IL payee and a CA payee → org, directors load; 1 expenditure (IL) loads, 1 skipped; (b) IL org with a CA payee → both expenditures load; (c) CA org with only an IL contributor → still nothing but the contribution (existing test). `pytest -q tests/test_irs527_loader.py` |
| 2 | **Conduit registry.** `database/schema.sql`: `irs527_conduits (ein TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT, added_on DATE NOT NULL)`; **not** in the reload TRUNCATE list. `database/irs527_conduits.py`: `CONDUITS = [("202517748", "ActBlue Non-Federal", "earmarked individual contributions passed through to state candidates and committees", "2026-11-08")]` + `sync_irs527_conduits(conn)` (upsert, idempotent) called from `init-db` and at the end of `import-irs527`. **Discovery query** (run once, result to Devin): orgs with > 1,000 IL-payee rows where ≥ 90 % of rows have purpose ILIKE '%contribution%' and the org has no `irs527_contributions` rows of its own above the pass-through pattern — candidates for the registry. **DEVIN** confirms the list before step 9. | `database/schema.sql`, `database/irs527_conduits.py`, `cli/commands.py` (two call sites), `tests/test_irs527_conduits.py` | `python run.py init-db` on a fresh test DB creates the table and the seed row; `sync` twice → 1 row. `pytest -q tests/test_irs527_conduits.py` |
| 3 | **"N of M rows" for the banner.** The loader's first pass already reads every B row: count B rows per EIN in `_collect_illinois_eins` (return a dict alongside the set, or a second helper) and write `irs527_org_file_counts (ein PRIMARY KEY, expenditure_rows_in_file INT, il_expenditure_rows INT, file_build_date TEXT)` at the end of the import. Include this table in the reload TRUNCATE list. | `database/irs527_loader.py`, `database/schema.sql`, `tests/test_irs527_loader.py` | Loader test asserts the counts for the step-1 fixtures. |
| 4 | **Measure before changing display code.** Reload locally with the new rule (one-off script from the 2026-11-08 status block, `PYTHON=.venv/bin/python`); record: import minutes, row counts, `run-cross-matching --only 527-expenditures` runtime and match count on 655,946 rows (baseline 2026-11-08: 16.5 s / 11,291 matches on 37,693 rows), `/527/dark-money` and `/` cold+warm timings via `scripts/audit/route_sweep.py`. If the match job exceeds ~10 min or a route exceeds its current p95 by > 2×, stop and redesign (candidate: match only non-conduit rows, or pre-aggregate conduit rows per recipient). | none (measurement) | Numbers written into this plan's §3 before step 5 starts. |
| 5 | **One shared filter.** `database/analytics.py`: `IRS527_NON_CONDUIT_SQL = "NOT EXISTS (SELECT 1 FROM irs527_conduits c WHERE c.ein = e.ein)"` (alias `e` = `irs527_expenditures`) and a helper `irs527_conduit_eins(conn) -> set[str]`. Apply it to every 527 **spending** aggregate: home "Total 527 Expenditures" tile (`webapp/routes/main.py` ≈2943–2952), home insights dark-money matched total (`main.py` ≈332–352), `/527/` list expenditure totals (`webapp/routes/irs527.py` `list_orgs` ≈289), `/527/dark-money` matched list + stats (`irs527.py` `dark_money` ≈790–900), ecosystem/relationship graphs (`database/analytics.py` `get_irs527_ecosystem_graph` ≈6534–6600: active-EIN filter and edges), `/api/analytics/irs527-ecosystem`. The recipients ledger and the org page of a conduit keep showing its rows but under the pass-through label (step 7). | `database/analytics.py`, `webapp/routes/main.py`, `webapp/routes/irs527.py`, `webapp/routes/api.py`, tests | Route tests seed one conduit org + one normal org with IL-payee rows and assert each surface's total equals the normal org's amount only. `pytest -q tests/test_irs527_routes.py tests/test_webapp.py -k "527 or dark"` |
| 6 | **Pass-through surface.** `/527/dark-money`: new windowed section "Pass-through contributions to Illinois recipients (conduits)" — total, row count, top 25 recipients (grouped by matched committee/candidate when `irs527_expenditure_recipient_matches` has a match, else by recipient name), one line per conduit org with its total, and the definition sentence from §0. Register it in `tests/scope_widgets_registry.py` (`window` row + probe). Optional second widget on `/analytics/state-races/<race_key>` is **out of scope** (P-10). | `webapp/routes/irs527.py`, `webapp/templates/irs527/dark_money.html`, `tests/scope_widgets_registry.py`, `tests/test_scope_widgets.py`, `tests/test_irs527_routes.py` | `pytest -q tests/test_scope_widgets.py tests/test_irs527_routes.py`; `test_every_windowed_tag_site_is_registered` passes. |
| 7 | **Org list and org page.** `/527/` list: a column "Illinois connection" (registered / paid Illinois recipients / conduit) and a `?connection=` filter (all / registered / payee / conduit), default **all** (**DEVIN** may choose registered-only as the default). `/527/<ein>`: the two banners from §0 (payee-qualified uses `irs527_org_file_counts` for "N of M rows"); for a conduit, the Financial Summary line "Total Expenditures" becomes "Pass-through contributions to Illinois recipients" and "Top Expenditure Recipients" becomes "Top pass-through recipients"; the recipients ledger title likewise. Derive payee-qualified as `latest registration state <> 'IL'`. | `webapp/routes/irs527.py`, `webapp/templates/irs527/{list,detail,recipients}.html`, `tests/test_irs527_routes.py`, `tests/test_527_integration.py` | Route tests for each banner and label; list filter returns the right subsets. |
| 8 | **Match job and docs.** `match_527_expenditures_to_committees`: no semantic change (conduit rows still match recipients; display filters them), but add `conduit` to its log line counts. Docs in the same pass: `docs/runbooks/irs527_data.md` (new status block: the rule, the registry, how to add a conduit), `.claude/data-dictionary.md` (counts, new tables, the rule), CLAUDE.md "Data Sources → IRS 527" one line, README one line, `docs/manual_tests.md` MT-035 (conduit org page and dark-money pass-through section), MT-036 (payee-qualified org page banner and list filter). | as listed | `python scripts/audit/check_manual_tests.py`; full suite green in a verify worktree. |
| 9 | **Local reload + review.** Reload locally (one-off script), run the seven 527 match jobs, `refresh-analytics`, start a local server, walk MT-033/035/036 and the home page; compare the home "Total 527 Expenditures" before/after (must be unchanged apart from non-conduit payee rows). **DEVIN** reviews the dark-money page and the conduit list; this is the gate for prod. | none | Screenshots or the sweep output attached to the plan's §3. |
| 10 | **Prod rollout** (Devin's word; ~30 min, outside the 03:00–05:00 Chicago nightly window or with the refresh lock, as the one-off scripts already do): push, pull, `init-db` (new tables), rerun the one-off reload scripts adapted to the new file date (download the current Saturday build — the rule change needs the full file), part 2 (matching, analytics, restart, `record-manual`), public checks of `/527/`, `/527/dark-money`, `/527/202517748`, `/`, `/data-status`. Record in `docs/runbooks/deployment.md`. | prod | Public checks 200; totals reconcile to the local run; `data_manual_loads` row written. |
| 11 | **Follow-ups logged, not built:** P-5 (freshness spec for 527), P-6 (weekly wrapper now that the reload is scripted), P-9 (527 links on committee/candidate/donor pages — the natural home for "pass-through received via ActBlue"), P-10 (dark money in race analytics). | `docs/runbooks/irs527_data.md` §8 | — |

## 2. Risks and the calls already made

- **Conduit rows dominate everything they touch** (87 % of expenditure rows). Hence one shared filter (step 5)
  rather than per-page fixes, and a registry rather than a heuristic, so a new conduit is a one-line addition.
- **ActBlue is an identified conduit; others may hide.** Step 2's discovery query is the net; Devin confirms.
- **Performance.** The match job and the dark-money page scale with rows; step 4 measures before any display code
  is written (Performance-First policy). The ecosystem graph's active-EIN subquery must exclude conduits or ActBlue
  becomes the hub of every graph.
- **Reload is destructive on prod** (TRUNCATE + import, ~10 min with 527 pages partial). Same procedure as
  2026-11-08; acceptable at a quiet hour; staging tables are a later improvement, not this plan.
- **Caches.** Route caches key on scope + filters; new outputs need `refresh-analytics` (flushes Redis) and a
  service restart, both in step 10.
- **Not decided here:** whether "paid Illinois recipients" should require a minimum (e.g., ≥ $1,000 total to
  Illinois) to keep out one-off vendor payments. Default: no minimum; the list filter makes the set visible.
  **DEVIN** may set a floor after seeing the step-9 list.

## 3. Measurements (fill in during steps 4 and 9)

| Metric | Baseline (2026-11-08, address rule) | New rule |
|---|---|---|
| Import minutes (local M3 Pro / prod) | 21 / 9.8 | |
| Expenditure rows | 37,693 | (expected 655,946) |
| `527-expenditures` job: seconds / matches | 16.5 s / 11,291 | |
| `/527/dark-money` cold / warm ms | | |
| `/` cold / warm ms | | |
| Home "Total 527 Expenditures" (current cycle) | | must exclude conduit rows |
