# STATE — race-sort-and-entity-networks (resume file; plan in `race-sort-and-entity-networks.md`)

**Resume here (2026-10-28 ~16:50Z, PAUSED on Devin's ask at a clean boundary — nothing running):** ask 1 (Top State Races whole-list sort) is SHIPPED — `e196649` deployed 15:00Z, records `a240836`, CI green for both. Asks 2–5 (per-race / per-candidate Network, Risk, Donors, Geography, Relationships tabs + the readable force graph) are in progress, tests-first, all UNCOMMITTED: step 3 ✅, step 4 ✅ both lanes (`database/entity_network.py` 76 passed; `database/entity_analytics.py` 125 passed; timings recorded, N14 met except the two findings → N15 + the prod `ANALYZE` check), step 5 ✅ (route suite 336 cases, red observed); steps 6–11 ⬜. Also UNCOMMITTED and green: the filings/deadlines note (`webapp/templates/base.html`, `webapp/templates/about.html`, two anchor tests in `tests/test_webapp.py`). **Next unit on resume = step 6**: dispatch one implementer for the blueprint + templates + subnav + registry rows + `config.py` TTL per the plan's "Route contract" and readings I1–I6, with N15 (`site_threshold` kwarg + `entity_risk_threshold` cache entry, one anchor test) and N16 (cycle derivation; the orchestrator amends the `None`-when-absent case of `test_race_election_cycle_reaches_resolve_entity`); verification `pytest -q -p no:cacheprovider tests/test_entity_analytics_routes.py` (→ 336 passed) + `tests/test_scope_widgets.py tests/test_candidate_profile.py tests/test_state_races_index.py`; then the Phase A checkpoint for Devin (PushNotification) and his ratification of N10–N16. Prod `pg_stats` check for `idx_isbe_condensed_receipts_donor_key_md5` returned **0** (Devin ran it 2026-10-28 ~16:55Z): the index is ignored on prod too (affects today's `/donors/key/<key>` lookups, A16). Fix handed to Devin: `ANALYZE isbe_condensed_receipts;` on prod (seconds, share-update lock only); re-check should print 1. Record the outcome here when he reports it. Commit/push/deploy only on Devin's explicit ask; CI is free (public repo) but a push still needs the ask. Context note: the pausing session was at ≈300K tokens; resume in a fresh session from this file + the plan.

## Standing directives (carry forward)
- Never commit/push/deploy without Devin's explicit ask in the session (per-session gate; CI free on this public repo). Prod SSH/psql only on his ask; public HTTPS reads of www.followthemoneyil.com are fine.
- Another session shares this working tree: never `git stash/checkout/revert/reset`; never touch or commit its files (`.claude/plans/privacy-redaction.md`, `CLAUDE.md`, `README.md`, `cli/commands.py`, `database/bulk_download_loader.py`, `docs/audits/phase2_handoff.md`, `docs/runbooks/privacy_takedown_response.md`, untracked `MelissaForDuPage/`, `docs/ARCHIVED_FILINGS_AUDIT_2026-05-27.md`, `docs/runbooks/dupage_donor_dossier.md`, `logs/`, the stray SQLite file `host=localhost dbname=ilcf user=devin`). No git worktrees in this repo: parallel lanes = disjoint files in the main tree.
- Tests first: red observed by the orchestrator before an implementer runs; the orchestrator re-runs every verification itself; `tester` agents for adversarial suites, `implementer` for one unit at a time.
- Interpreter `/opt/anaconda3/bin/python3`; pytest with `-p no:cacheprovider`; run the entity suites sequentially (each case builds its own schema; parallel runs contend on `ilcf_test`). Stop any temporary server. Performance-first: measure before/after and record the numbers in the plan.
- Never commit privacy request rows or real names in fixtures. New windowed `scope_tag(` sites need a `Widget` row in `tests/scope_widgets_registry.py` (+ probe).
- `README.md` / `CLAUDE.md` doc sync is owed to the other session that holds those files; this task's docs go to `docs/runbooks/route_perf_caching.md`, `docs/runbooks/deployment.md`, a new `docs/entity_networks.md` (N9), and the dated row in `docs/architecture_suggestions_2026-10-26.md`.

## Step table (plan step numbers; statuses at the pause)
| # | step | status | verification evidence |
|---|---|---|---|
| 1–2 | ask 1: whole-list race sort, 16 orders from one cached build, header links, revision os3 | ✅ SHIPPED `e196649` 2026-10-28 | full suite 2283 passed; prod checks in the runbook "Deployed 2026-10-28 15:00Z" block; CI runs 40711410947 + 40714952291 green |
| 3 | contract tests (tester agents) | ✅ 2026-10-28 | `tests/test_entity_network.py` 76 cases, orchestrator red: 75 failed / 1 passed; `tests/test_entity_analytics.py` 125 cases, orchestrator red: 124 failed / 1 passed; both ruff clean, no real names; authors' readings R1–R10 / A1–A9 recorded in the plan |
| 4B | `database/entity_network.py` | ✅ 2026-10-28 | orchestrator re-ran: 76 passed (23 s), ruff clean; timings in the plan ("step 4, lane B"): Pritzker cycle window 155 ms cold / 15 ms warm, unbounded 325–414 / 147 ms; typical candidate 37 / 28 ms; heaviest committee 1157 638 / 491 ms unbounded — N14 targets met |
| 4A | `database/entity_analytics.py` (resolve_entity, donor base, donors, geography, risk, relationships) | ✅ 2026-10-28 | orchestrator re-ran: 125 passed (46 s), ruff clean; timings in the plan ("step 4, lane A"); two findings → N15 (cache the site-wide risk threshold in the routes, step 6) and the md5-index statistics gap (local `ANALYZE isbe_condensed_receipts` run by the orchestrator; prod needs the `pg_stats` check — Devin) |
| 5 | route tests `tests/test_entity_analytics_routes.py` + stub blueprint `webapp/routes/entity_analytics.py` (tester) | ✅ 2026-10-28 | 336 cases; orchestrator red: 334 failed / 2 passed (124 s), ruff clean, no real names; readings I1–I6 in the plan; N16 (cycle derivation) journaled with the one test amendment the orchestrator makes in step 6 |
| 6 | routes + `webapp/templates/entity/*` + subnav on both detail pages + registry rows + `config.py` TTL | ⬜ | per the plan's "Route contract"; **Phase A checkpoint for Devin after this step** |
| 7 | N7 renderer (`webapp/static/js/network_graph.js`, vendored d3-force/zoom) + N8 Playwright readability probe | ⬜ | — |
| 8 | Phase A network views (money flow, vendor network, ego force) | ⬜ | — |
| 9 | global networks/relationships pages on the new renderer | ⬜ | — |
| 10 | Phase B (after Devin's reaction) | ⬜ | — |
| 11 | phase gate: full suite, route sweep with a local server, docs (N9), then the commit/push/deploy ask | ⬜ | — |
| — | filings/deadlines note (footer + About) | ✅ green, UNCOMMITTED | `tests/test_webapp.py` two anchor tests red → green; 177 page tests passed |

## Decision journal
In the plan file: N1–N9 ACCEPTED (Devin, "go on the decisions"); N10–N14 OPEN (decide-and-journal; Devin ratifies at the Phase A checkpoint); the authors' readings R1–R10 and A1–A9 and the orchestrator's p95 decision (A1) are below the contract.

## Files of this task (all uncommitted at the pause unless marked)
- Committed: everything in `e196649` + `a240836` (plan file as of the contract; runbook deploy block).
- New, uncommitted: `database/entity_network.py` (green), `database/entity_analytics.py` (green), `tests/test_entity_network.py`, `tests/test_entity_analytics.py`, `tests/test_entity_analytics_routes.py` (red by design until step 6), the stub `webapp/routes/entity_analytics.py` (not registered).
- Modified, uncommitted: `.claude/plans/race-sort-and-entity-networks.md` (readings, route contract, lane B verification), this STATE file, `webapp/templates/base.html`, `webapp/templates/about.html`, `tests/test_webapp.py`.
- Memory: `~/.claude/projects/-Users-devin-Illinois-campaign-finance/memory/active_programs_2026-10-28.md` (+ index line).

## Log (newest first)
- 2026-10-28 ~16:50Z — step 5 red observed (334/336), readings + N16 recorded; PAUSED, state closed, Devin pinged.
- 2026-10-28 ~16:35Z — lane A verified green (125 passed); local ANALYZE of `isbe_condensed_receipts` (md5 index stats 0 → 1); N15 journaled.
- 2026-10-28 ~16:20Z — Devin: pause at the next opportunity with state + handoff. Lane A implementer and step-5 test author running; this file written; results appended below when they land.
- 2026-10-28 ~16:05Z — step 5 test author dispatched (route contract written into the plan); lane A implementer dispatched after the 124-red observation.
- 2026-10-28 ~15:58Z — lane B verified green (76 passed) with timings; recorded in the plan.
- 2026-10-28 ~15:30Z — step 3 authors dispatched; filings/deadlines note implemented tests-first (Devin's ask) while they ran.
- 2026-10-28 15:00Z — `e196649` deployed (pull + restart 3 s, warm 96 s), public checks green; `a240836` records pushed; CI green ×2.
