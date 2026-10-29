# Plan — manual test register `docs/manual_tests.md` (to-do 2026-10-28 item 9)

**Status:** DEPLOYED 2026-10-29 04:37Z — terminal 1 merged `d171949` + `633cfb8` into main `9fed404` (with search Phase 2), pushed 04:36:56Z, prod pulled + restarted 04:37:44Z; terminal 1 appends MT-025 (search Phase 2) in its records commit — re-fetch main before any further register work. CLAUDE.md/README pointers still deferred per D6. Was: DONE 2026-10-29 04:20Z (M1 `d171949`, M2 committed after it on `worktree-ui-improvements`; merge pending terminal 1's review). Was: IN PROGRESS 2026-10-29 03:53Z — terminal 2 (session `ui-improvements-fb`, Fable orchestrating one `opus-engineer`); worktree `/Users/devin/Illinois_campaign_finance/.claude/worktrees/ui-improvements`, branch `worktree-ui-improvements` off `main` `2df4ae8`. To-do status line: `to_do/to_do_10_28_26.status.md` "STATUS" §9 (terminal 2 wrote it 03:47Z in the task file; moved verbatim to the sidecar by T1 at 03:55Z when Devin split the file — sessions write only the sidecar, exact-string edits only; the task file is Devin's). Terminal 1 (`illinois-campaign-finance-49`) informed 03:46Z; answered 03:52Z: all defaults accepted, riders in D9.

## Devin's ask (verbatim, to-do item 9)
> We should have a document where we are recording manual tests that I should run with every feature update, bug fix etc even though we are running automated tests, I want to have a document that shows tests I should run and verify, and it should be updated as we go. I'll be able to run the manual tests on my own time sporadically, but I think this is something we need to do to ensure we are not missing the mark anywhere. The document should be formatted in a way where it tells me what I'm testing, what date the test was made, what steps I should follow to do the manual tests on the website, what constitues a pass and what is a fail

Conflict map v2 verdict: SAFE-ALWAYS — terminal 2 creates the file and owns its format; terminal 1 appends entries for its own deploys afterwards; the data-integrity checklist (item 10) lives in a sibling file `docs/data_integrity_checks.md`, cross-linked, never in this one.

## Brief (fable-architect §1)
- **Interpretation.** A durable, append-only register of manual checks Devin runs on the live site (`https://www.followthemoneyil.com`, or a local server) in addition to the automated suite. Each entry says what is tested, when the test was created, the exact steps on the website, what counts as a pass and what counts as a fail. Delivered now: the file with its format and maintenance rule, plus a baseline set of tests covering the site's existing public surfaces so it is usable on day one; from now on every feature update or bug fix adds entries. Also a small checker so anyone appending can prove the document is well-formed and its URLs resolve.
- **Scope.** Narrow: two new files, no application code. `docs/manual_tests.md` (the builder writes it) and `scripts/audit/check_manual_tests.py` (orchestrator wrote it 03:49Z; observed red = FileNotFoundError with the document absent). No edits to `CLAUDE.md` / `README.md` (no-go: another local session holds dirty edits there) — their one-line pointers are deferred and handed to terminal 1 in the completion report.
- **Approach.** One per-test section per check (steps are multi-line, so a single wide table would be unreadable) + a summary index table + a run log. Steps carry site-relative paths in backticks so the checker can fetch them. Pass/fail criteria are observable on the page (numbers that must reconcile, elements that must be present, a default window that must read a given way), never "looks right". Example entities are public officials and committees only (Michael W Frerichs = candidate 13505, Democratic Party of Illinois = committee 6239, as in the to-do); donor-level tests are reached by clicking from a committee page, never by a hard-coded private individual.
- **Success criteria.** (1) `docs/manual_tests.md` exists with the skeleton in Scaffold A, 20–30 baseline tests, each with every required field, unique sequential ids `MT-001…`, every id in the index, a core subset marked. (2) `python3 scripts/audit/check_manual_tests.py --host http://127.0.0.1:5057` prints `OK`, exit 0, against a local server on this worktree (every quoted path answers 200). (3) Three tests picked at random by Fable are followable step by step on the local server and their pass/fail criteria are decidable from the page. (4) Nothing asserts a feature that is not deployed (documentation honesty; `git log` + `docs/` are the record). (5) `git status` shows only the two new files + this plan.
- **Verification (Fable re-runs).** The checker twice (structure only; with `--host`), `ruff check --select E9,F63,F7,F82 scripts/audit/check_manual_tests.py`, the three-test walk-through, `git status --short`. No specialist: no code path, no data path, no security surface.
- **Time budget.** 40 min builder time (range 25–55); thresholds 20 / 40 / 60 min. Milestones: **M1** skeleton + 5 tests (home, candidate detail, committee detail, time scope bar, privacy) with the checker green against the server (≤ 15 min; Fable vets the shape before the bulk) → **M2** the rest of the baseline, checker green, report (+ 25).
- **Risks.** Steps drift as pages change (mitigated by the checker's URL sweep and the "Revised" rule); a test could encode a current defect as expected behaviour (the "unusual patterns this month" widget shows 2026-09 — the test must say the month shown equals the latest month of data in the freshness indicator, so it fails while the defect exists); over-long baseline nobody runs (cap 30, core subset ≈ 15 min).

## Decision journal (ratify per row: accept | revert | edit)
| id | decision | alternative not taken | reversible? | status |
|---|---|---|---|---|
| D1 | **Shape**: `### MT-NNN — title` sections with bold field labels, a summary index table (`| ID | Area | What is tested | Created | Core |`) and a run log table at the end. | one wide table with a steps column | yes | T1 ACCEPTED 03:52Z; Devin OPEN |
| D2 | **Ids** sequential, never reused. Changing a test adds `**Revised:** <date> — <what>`; retiring adds `**Retired:** <date> — <why>` and keeps the section. | edit in place | yes | T1 ACCEPTED; Devin OPEN |
| D3 | **Baseline** = smoke + reconciliation tests for every public surface (home, search, candidates list/detail tabs, committees + donors/payees pages, donors list, analytics dashboard / state races / geo drill-down / networks / relationships, federal finance + geo + races outside spending, lobbying, 527 + dark money + org detail, OpenBook, compare / investigate / person intelligence, about / privacy, time-scope semantics, Network tab drill-down, freshness indicators). 20–30 tests, ⭐ core subset ≈ 15 min. | only the item-1 entry | yes | OPEN |
| D4 | **Entities** named in steps: public officials / committees only (13505, 6239, state-wide races); donor pages reached by clicking through. No private individual's name, address or donor key in the document. | hard-coded donor keys | yes | OPEN |
| D5 | **Item 1 entry seeded by terminal 2**, steps written against the deployed behaviour (merge `6ed586c`, prod `6674379` since 2026-10-29 03:19Z), `**Source commit:** 6674379`; terminal 1 appends the Phase 2 search entry at its deploy. | leave to T1 | yes | T1 ACCEPTED 03:52Z |
| D6 | **Pointers deferred**: no `CLAUDE.md` / `README.md` edit; exact lines handed to T1 in the completion report. | edit them here | yes | T1 ACCEPTED 03:52Z |
| D7 | **Checker** `scripts/audit/check_manual_tests.py` (stdlib; structure + optional GET sweep of every backticked `/path`, placeholders with `<>` skipped) written by the orchestrator, red observed 03:49Z. | shell one-liner in the plan only | yes | OPEN |
| D8 | **Builder does not commit**; Fable commits at each milestone after its own re-run (evaluator split). | builder commits | yes | OPEN |
| D10 | MT-002 checks Donors total ≤ Overview total (the Donors tab counts itemized 1A only; the Overview counts all receipts — measured locally $1.85M vs $12.69M all time). | strict equality (false by design) | yes | ACCEPTED 04:04Z |
| D11 | MT-003 uses the same ≤ rule for the donors page; payees page checked for strict equality (locally equal to the cent). | strict equality on both | yes | ACCEPTED 04:04Z |
| D12 | MT-022 pass = heading month equals the "Local receipts:" month in the Data Status box. | the home Data Freshness section (same date) | yes | ACCEPTED 04:19Z |
| D13 | Geo drill-down pass = "Sum check delta" under $1.00 (IL all-time shows $0.03 of rounding). | exactly $0.00 | yes | ACCEPTED 04:19Z |
| D14 | MT-007 reaches the candidate list through `/candidate-finance/` (`/candidates` is a hub with no list). | drop the hub step | yes | ACCEPTED 04:19Z |
| D15 | MT-015 and MT-017 carry dated known-failing notes (orchestrator's edit, 04:19Z) so a sporadic run is not a surprise; MT-009 has a "caches warm" precondition and allows one reload (first `/analytics/` load after a restart 500'd locally: statement timeout in `get_anomaly_flags → get_donor_concentration → _get_donor_committee_rows` during prewarm). | no notes until triaged | yes | DECIDED |
| D9 | **T1 riders adopted**: every test carries `**Source commit:**` (sha, or `baseline` for the day-one set) and a one-line `**Precondition:**` (data/state needed; `none` when there is none) so a deploy-time append is mechanical. Both are in the checker's REQUIRED tuple (03:53Z). | optional fields | yes | T1 requested; adopted |

## Scaffold (binding shapes)
**A. `docs/manual_tests.md` skeleton** (headings and field labels exactly as written — the checker reads `### MT-\d{3}`, `**Area:**`, `**Created:**`, `**Source commit:**`, `**Precondition:**`, `**Steps:**`, `**Pass:**`, `**Fail:**` and index rows starting `| MT-NNN |`):
```
# Manual test register
<What this is: 2–3 sentences — manual checks on the live site run in addition to pytest; automated tests prove the code
does what the tests say, these prove the site does what a reader expects.>
## How to run a session
<numbered: open https://www.followthemoneyil.com or a local server; pick the ⭐ core subset (~15 min), everything, or the
Areas just changed; follow steps exactly; decide Pass/Fail; add a run-log row.>
## Maintenance rule
<every feature update or bug fix that lands adds ≥ 1 test in the same commit (or the deploy-record commit): new section +
index row; ids sequential, never reused; Revised / Retired lines per D2; run
`python3 scripts/audit/check_manual_tests.py [--host http://127.0.0.1:5057]` before committing; the data-integrity
checklist lives in docs/data_integrity_checks.md (to-do item 10), not here.>
## Conventions
<ISO dates; site-relative paths in backticks so the checker can fetch them; placeholders in angle brackets; public
officials and committees only as examples; "window" = the time scope bar under the page title.>
## Index
| ID | Area | What is tested | Created | Core |
|---|---|---|---|---|
## Tests
### MT-001 — <title>
- **Area:** <one of: Home · Search · Candidates · Committees · Donors · Analytics · Federal · Lobbying · 527 · OpenBook ·
  Tools · Time scope · Network tab · Privacy · Freshness>
- **Created:** 2026-10-29
- **Source commit:** <sha of the change this test guards, or `baseline`>
- **Precondition:** <one line: data/state needed, or `none`>
- **Why:** <one sentence: what regression or reader expectation this guards>
- **Steps:**
  1. Open `/path`.
  2. <click / read / compare — one action per step>
- **Pass:** <observable: numbers that reconcile, elements present, default window text, link targets>
- **Fail:** <observable: mismatch, "N/A"/blank/None, missing element, error page, wrong window>
## Run log
| Date | Site | Tests run | Result | Notes |
|---|---|---|---|---|
```
**B. Sources for accurate steps** (read, do not edit): `webapp/templates/base.html` (nav), `webapp/routes/main.py` + `webapp/templates/index.html` (home widgets incl. "unusual patterns this month" and the freshness strip), `webapp/routes/candidates.py` + `webapp/templates/candidates/` (profile tabs), `webapp/routes/entity_analytics.py` + `docs/entity_networks.md` (Network tab, drill-down panel, cap note, "See all donors"), `webapp/routes/committees.py` + `webapp/templates/committees/` (detail, `/committees/<id>/donors`, `/payees`, `?by=family`), `webapp/routes/donors.py`, `webapp/routes/analytics.py` + `webapp/templates/analytics/` (dashboard, state races, geo drill-down, networks, relationships), `webapp/routes/federal_finance.py`, `webapp/routes/lobbying.py`, `webapp/routes/irs527.py`, `webapp/routes/openbook.py`, `webapp/utils/time_scope.py` + `webapp/templates/_partials/time_scope_bar.html` (defaults: lists/dashboards/search = current cycle 2025-01-01..2026-12-31; entity profiles = all time with a "This cycle" subtotal; lobbying = registration years; `date_from`/`date_to` > `year` > `cycle` > `period`), `docs/runbooks/deployment.md` §"Endpoint sweep after deploy" (the critical route list), `docs/architecture_suggestions_2026-10-26.md` §G and `git log --oneline -40` (what is actually deployed). Confirm each page on the local server with `curl -s http://127.0.0.1:5057/<path> | grep -c '<marker>'` before writing steps that name an element; use `psql ilcf` (read-only) to pick example ids.
**C. Bar for pass/fail** (copy the style): *Candidate detail totals* — Pass: the Overview "Total raised" equals the Donors tab's total for the same window, dollars formatted, "This cycle" subtotal ≤ all-time; Fail: mismatch, "N/A", blank, 500. *Network tab drill-down* — Pass: clicking "Top N individual donors" opens a panel under the graph whose member amounts sum to the node's amount, members link to donor pages, cap note names how many are not shown and "See all donors" lands on the Donors tab; second click clears; Fail: no panel, sum differs, dead link. *Unusual patterns this month* — Pass: the month in the heading equals the latest month of data shown in the freshness indicator; Fail: an older month (this is the known defect in to-do item 3 — the test is meant to fail until it is fixed, say so in **Why**).
**D. Commands.** Server: `DATABASE_URL=postgresql://devin@localhost/ilcf nohup /opt/anaconda3/bin/python3 run.py runserver --port 5057 > <scratchpad>/server5057.log 2>&1 &` (port 5000 is held by macOS; the server answers in ≈ 8 s); stop: `pkill -f "runserver --port 5057"`. Checker: `/opt/anaconda3/bin/python3 scripts/audit/check_manual_tests.py` and `… --host http://127.0.0.1:5057`. Lint: `/opt/anaconda3/bin/python3 -m ruff check --select E9,F63,F7,F82 scripts/audit/check_manual_tests.py`.

## Environment rules for the builder
Work only in the worktree above; never touch `/Users/devin/Illinois_campaign_finance` or any other worktree; never `git -C`, never `git stash`, never commit or push (Fable commits); edit only `docs/manual_tests.md` (report a checker bug, do not fix it); never edit `CLAUDE.md`, `README.md`, `to_do/*`, `.claude/plans/*`; local DB read-only, never prod; stop any server you start; measure time with `date -u +%FT%TZ`.

## Log
- 2026-10-29 03:45Z interpreter + server + DB verified (flask on `/opt/anaconda3/bin/python3`, psycopg 3.3.2; `/about`, `/candidates/13505`, `/committees/6239`, `/privacy`, `/candidates/13505/network` all 200 on :5057). 03:46Z T1 messaged. 03:47Z to-do §9 → IN PROGRESS. 03:49Z checker written, ruff clean, red observed. 03:52Z T1 answers (D5/D6/D1 accepted; riders → D9). 03:53Z checker REQUIRED extended; plan written; builder dispatched for M1 (watchdog shows its first call 03:52:10Z). 03:55Z to-do split: status line now in `to_do/to_do_10_28_26.status.md`. 03:56Z controller baseline ON TRACK (0 edits outside targets; its psycopg2 note is moot — the project uses psycopg 3 and the server ran on `/opt/anaconda3/bin/python3` at 03:47Z; no venv exists in this worktree). Thresholds 04:14 / 04:34 / 04:54Z.
