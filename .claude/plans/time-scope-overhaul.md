# Plan: time scope overhaul — one visible, adjustable date range on every page

**Written:** 2026-10-22
**Why:** Devin: the nav "Period:" toggle (2026 Cycle / 2024 Cycle / Past Year / Past 2 Years / All Time) feels less useful than when it was designed (Feb 2026), separate date controls are scattered through the site, and on a candidate/entity page it is often unclear what dates the numbers cover or how to change them. Verified live on 2026-10-22 (section 1).
**Routing (task-router, 2026-10-22):** HYBRID. Gate 0 clear. Criteria machine-checkable (pytest modules named per step, grep gate, route-sweep bar/tag check, ruff). Blast radius: local edits, reversible; the working tree carries another session's uncommitted changes (privacy-redaction, CLAUDE.md, README, cli/commands.py, bulk_download_loader.py, two docs) — **nothing is committed or pushed without Devin's explicit ask, and those files are never swept into a commit**; prod deploy is a human checkpoint. Gate 3: quick wins + Phase 0 run loop-style; Phase 1's first visible pages are shown to Devin before the full rollout. Parallelization: quick wins serial in the main tree (tiny); Phase 0 units 0.1 / 0.2 / 0.4 are disjoint and may run as worktree lanes; Phase 2 propagation and cache steps stay serial with the orchestrator.
**Status:** Decisions D1–D4 **ratified by Devin 2026-10-22** (remove dropdown; scope follows drill-down only, top-nav resets; profiles default all time; presets Current / Previous / All time / Last 12 months / Custom). Q2 (case-sensitive search) retracted — see Phase Q. Live progress: `.claude/plans/time-scope-overhaul.STATE.md`.

---

## 1. What is there today, and why it fails

### 1.1 Mechanism (as built)

- `webapp/utils/time_filter.py` defines five preset keys. `get_active_period()` reads `?period=` and returns `{key, label, description, start_date, end_date}`. The current cycle is **hard-coded**: `DEFAULT_PERIOD = "2026cycle"`, and `get_period_dates()` only knows `2026cycle` and `2024cycle`.
- `base.html` renders a `<select>` in the nav. A script rewrites **every** same-origin `<a href>` and appends a hidden `period` input to **every** GET form, so the key follows the user everywhere.
- Each route then decides on its own: whether to filter at all, which date column (`received_date`, `filed_date`, `contribution_receipt_date`, `period_end_date`, `election_cycle`, `reg_year`), and which local overrides it accepts (`date_from`/`date_to`, `cycle`, `year`, `start_date`/`end_date`, `range`, `months`).
- Coverage (Appendix A): 59 HTML routes; 28 honour the period, 19 partially (some sections windowed, others all-time), 12 not at all.
- Display is inconsistent: an amber/teal chip (`_partials/active_filter_chip.html`) on candidate-finance, analytics, federal and live-feed pages; a small `period-badge` on two committee-detail headings and the homepage subtitle; **nothing** on donor, 527, lobbying, OpenBook, search, compare or person-intel pages.
- Overrides are AND-ed with the period, not substituted for it. On `/candidate-finance/` the global period both sets a date window on `period_end_date` **and** derives `cycle=2026`; a `year=` override is added on top. The chip then says "overriding global period", which is false.

### 1.2 Evidence from production (www.followthemoneyil.com, 2026-10-22)

| Page | What a visitor sees | What is actually happening |
|---|---|---|
| `/donors/key/jb|pritzker|…?period=2026cycle` | "Total Contributed: $90,500,000.00". No date label, no chip, no control. | Period-filtered. Same page with `period=all` says $497,050,000.00. Nothing on the page says which. |
| `/candidate-finance/?q=Pritzker&period=2026cycle` | Chip "2026 Cycle (Jan 2025 – present)", 2 rows. Year and Cycle dropdowns offering 1989–2026. | Date window on `period_end_date` AND `election_cycle = 2026`. |
| …`&year=2024` | Amber chip "Custom filter active · Year: 2024 · overriding global period 2026 Cycle". **0 rows.** | Year 2024 AND cycle 2026 AND Jan-2025+ window → empty. `period=all` shows 16 rows. The "override" did not override. |
| `/committees/15030?period=2026cycle` | "Receipts Summary [2026 Cycle]", "Expenditures Summary [2026 Cycle]". "Filing History", "Top Donors", "Top Payees" carry no label. | A reader cannot tell whether Top Donors is cycle or all-time without reading code. |
| `/` | Badge "2026 Cycle (Jan 2025 – present)". Freshness block: latest ISBE receipt 2026-10-21, latest FEC Schedule A receipt 2026-07-01. | "present" is not true for FEC data; the period label and the freshness facts never meet. |
| `/search?q=Pritzker` | "Committees (1)", "Donors (50)", "Candidates (2)". | Candidate hits are all-time (`_search_local_candidates` ignores the period); committee/donor hits are period-scoped. Headings do not say. |
| `/federal-finance/?period=2026cycle` → click the "Candidates" tab | URL becomes `/federal-finance/candidates?cycle=2026&date_from=2025-01-01&date_to=2026-10-22`. Chip turns amber: "Custom filter active · Date From: 2025-01-01 · Date To: 2026-10-22". | The subnav wrote the period's resolved dates into the link as if the visitor had typed them. From here the nav selector keeps those dates, so changing the period changes nothing. Same pattern on analytics tabs and "View full" links. |

### 1.3 Root causes

1. **The control is far from the numbers.** The only selector lives in the nav, labelled "Period:", visually unrelated to the content it changes. On an entity page the natural question ("since when?") has no answer next to the figure.
2. **One global key, many meanings.** The same "2026 Cycle" means `received_date ≥ 2025-01-01` on one page, `filed_date` on another, `election_cycle = 2026` on a third, `reg_year` nowhere (lobbying ignores it). A global control can only be intuitive if every page says what it did with it.
3. **Overrides compose instead of replace.** Users get empty pages and a chip that asserts the opposite.
4. **Wrong default for profiles.** Entity detail pages inherit the dashboard's cycle scope. A profile is a history; truncating "Total Contributed" silently is the single most misleading behaviour on the site.
5. **Rolling presets fight cycle data.** "Past Year"/"Past 2 Years" straddle cycles; the federal side (and `period_cycle()`) has to guess a cycle for them. Devin's instinct that these are less useful is right.
6. **"present" is a promise the data does not keep.** The window end should be the latest loaded date for that page's source, stated as such.
7. **Subnav links freeze derived dates.** Analytics and federal tab links persist the period's *resolved* `date_from`/`date_to` as though the user typed them. One tab click turns the chip amber ("Custom filter active") and silently disables the nav selector. This is the most likely source of "sometimes I can't change the date range". Appendix A.2 spot 1.
8. **Latent rollover bug.** On 2027-01-01 the site still defaults to "2026 Cycle" and has no "2028 Cycle" option. Seven files hard-code `2026cycle` (`webapp/routes/main.py` prewarm, `webapp/routes/analytics.py` prewarm, `scripts/audit/route_sweep.py`, `time_filter.py`, tests).

Prior work on this problem: Phase 1/2 audits (`docs/audits/phase1_findings.md`, `docs/audits/phase2_handoff.md`) added the chip, canonical date semantics (`filed_date` for report-driven, `transaction_date`/`received_date` for contribution-driven), federal date windows (P2-1 Option A), and subnav filter persistence (P2-4). P2-2 (lobbying) was never done. This plan supersedes the chip approach rather than extending it: the chip is a label bolted beside a control that lives elsewhere; the fix is to make the label and the control the same thing, on every page.

---

## 2. Design principles

1. **Every number has a visible time scope next to it.** No bare aggregate anywhere. Either the page scope bar covers it, or its heading carries an explicit tag ("All time", "Latest filing", "Registration year 2026").
2. **One control per page, where the data is.** A single scope bar directly under the page title (or under the section subnav where one exists). The nav carries no selector.
3. **Scope is stated as dates, plus what they apply to.** "Jan 1, 2025 – Oct 21, 2026 · by receipt date · latest ISBE data Oct 21, 2026". Preset names are shorthand, never the only description.
4. **Custom replaces preset.** A custom range, year or cycle is the scope. Nothing is AND-ed underneath it.
5. **Defaults fit the page.** Dashboards and lists default to the current cycle; entity profiles default to all time (with a current-cycle subtotal visible). The default is computed from today's date, never hard-coded.
6. **URL is the source of truth.** Scope lives in the query string so links are shareable and caches are keyed correctly. No cookies, no session state.
7. **Legacy URLs keep working.** `?period=2026cycle`, `?date_from=`, `?date_to=`, `?cycle=` continue to resolve to the equivalent scope.

---

## 3. Proposed architecture: `TimeScope`

### 3.1 Resolver (server)

New module `webapp/utils/time_scope.py` (replaces `time_filter.py` over Phase 2; `time_filter.py` becomes a thin shim then is deleted).

```python
@dataclass(frozen=True)
class TimeScope:
    key: str                 # "cycle:2026" | "cycle:2024" | "all" | "last12m" | "custom"
    start: date | None       # None = unbounded
    end: date | None         # None = open-ended ("through latest data")
    axis: str                # "received" | "filed" | "transaction" | "reg_year" | "cycle" | "none"
    granularity: str         # "day" | "year" | "cycle"
    data_through: date | None  # latest loaded date for this page's source
    is_default: bool
    is_custom: bool
    label: str               # "2026 Cycle" / "All time" / "Custom range"
    window_label: str        # "Jan 1, 2025 – Oct 21, 2026"
    axis_label: str          # "by receipt date"

def resolve_time_scope(*, default: str, axis: str, source: str = "isbe") -> TimeScope: ...
def current_cycle(today: date | None = None) -> int: ...      # even year → year; odd → year+1
def scope_params(scope: TimeScope) -> dict: ...                # for url_for / hidden inputs
def scope_cache_key(scope: TimeScope) -> str: ...              # f"{axis}:{start}:{end}"
def scope_where(scope, column, *, style="qmark") -> tuple[str, list]: ...
```

- Query-string contract (names kept, values generalised): `period=cycle:2026 | cycle:2024 | … | all | last12m`, `date_from=YYYY-MM-DD`, `date_to=YYYY-MM-DD`. Presence of `date_from`/`date_to` means `key="custom"` and the preset is ignored. Legacy values `2026cycle`, `2024cycle`, `1y`, `2y` are mapped (`2y` → custom window with the same dates so old links mean the same thing).
- `cycle=` and `year=` on pages that accept them become scope shorthands: `year=2024` ≡ `date_from=2024-01-01&date_to=2024-12-31`; `cycle=2024` ≡ `period=cycle:2024`. They are never applied in addition to a scope.
- Axis `cycle` or `reg_year` (FEC candidate totals, lobbying registrations): the resolver snaps the window outward to whole cycles/years and sets `granularity` so the bar can say "shown as registration years 2025–2026".
- `data_through` comes from the already-cached `global_data_status` (ISBE `MAX(received_date)`, FEC `MAX(contribution_receipt_date)` etc.) selected by `source`.
- Available cycle presets are generated: current cycle, previous cycle, and (for pages whose data goes back further) a "More cycles…" list, never a literal.

### 3.2 Scope bar (one partial, one place per page)

`webapp/templates/_partials/time_scope_bar.html`, rendered immediately under the `<h1>` (inside `_subnav.html` for analytics/federal so it sits under the tabs). Text-level mockup:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ SHOWING  2026 Cycle · Jan 1, 2025 – Oct 21, 2026 · by receipt date           │
│          latest ISBE data: Oct 21, 2026                                       │
│                                                                              │
│ [2026 Cycle] [2024 Cycle] [All time] [Last 12 months] [Custom ▾]   Reset     │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Preset buttons are links that keep the page's other filters (via the same allow-list mechanism as `subnav_persist_params`) and reset `page=`.
- "Custom ▾" discloses `date_from` / `date_to` inputs in a GET form with the page's other filters as hidden inputs. When custom is active the bar turns amber, shows the exact dates, and "Reset" returns to the page default.
- When the page's axis is coarser than a day the bar explains the snap: "Custom Mar 1 – Sep 30, 2025 → shown as registration year 2025".
- `role="status"`, keyboard operable; no JavaScript required for any of it (progressive enhancement only for the disclosure).

### 3.3 Section tags

Jinja macro `scope_tag(text=None)` in `_partials/macros.html`. Rule: **every `<h2>` above an aggregate carries a tag.** Default text is the page scope label; sections that ignore the scope say so:

- `All time` — committee info, officers, filing history, "first/last seen"
- `Latest filing` — cash on hand, ending funds available
- `2026 Cycle` (page scope) — receipts summary, top donors, top payees, trends
- `Registration years 2025–2026` — lobbying client lists

### 3.4 Propagation (replaces the JS link rewriter)

Server-side only. A Jinja global `scoped_url(endpoint, **kw)` appends `scope_params(time_scope)` when the target endpoint is scope-aware (registry in `time_scope.py`). Used for: drill-down links (list → detail, search result → entity), subnav tabs, pagination/sort/CSV links, and the search form. Links carry the scope **key** (`period=cycle:2026`), never the resolved dates; only a user-set custom range travels as `date_from`/`date_to`. That rule alone retires Appendix A.2 spot 1. Top-nav links carry **no** scope, so each section opens in its own default (see D2 for the alternative). The `ensurePeriodOnNavigation` script is deleted.

### 3.5 Defaults by page family

| Family | Default scope | Axis | Notes |
|---|---|---|---|
| Homepage `/`, Start Here | current cycle | received (ISBE) / transaction (FEC) | "what's happening now" |
| State Analytics `/analytics/*` | current cycle | per page: received (donors, networks, geography), filed (risk flags, reconciliation) | axis stated per tab |
| Federal Finance `/federal-finance/*` | current cycle | transaction, with cycle snap for pre-aggregated candidate totals | P2-1 already threads date windows |
| Lists: candidates, committees, donors, reports, 527 orgs, OpenBook vendors | current cycle | source-specific | "All time" one click away |
| Entity detail: committee, donor (both routes), federal candidate, federal donor, 527 org, lobbying entity/client, OpenBook vendor | **all time** | source-specific | headline shows all-time total **and** a current-cycle subtotal; see D3 |
| Lobbying list | current registration year(s) | reg_year | granularity "year"; closes P2-2 |
| Search | current cycle for counts; entity cards show both | mixed | each result heading tagged |
| Live feed | last 30 days | received | tag says so; presets still available |
| Compare, Person Intel | all time | mixed | trend charts tagged per series |

### 3.6 Caching

Cache keys switch from the preset key to `scope_cache_key()` (resolved window + axis). Rolling presets therefore roll daily — acceptable at 30-minute TTLs. Prewarm (`warm_analytics_caches`, homepage prewarm) computes its key from `resolve_time_scope(default=…)` with no request, so it stays aligned without literals. `?refresh_cache=1` unchanged.

### 3.7 What is deliberately not in this design

- No cookie/session persistence of the chosen scope (shareability and cache correctness win; revisit only if D2 picks "sticky").
- No per-section scope selectors (one control per page; sections are tagged, not individually adjustable).
- No change to data semantics already settled in Phase 1 (`filed_date` for report-driven, transaction/received for contribution-driven).
- No visual redesign beyond the bar, tags, and removing the nav select.

---

## 4. Decisions (D1–D4 ratified by Devin, 2026-10-22)

| id | Decision | Recommended | Alternative not taken | Why |
|---|---|---|---|---|
| D1 | Nav "Period:" select | **Remove it.** The scope bar is the only control. Optionally show passive text in the nav ("Scope: 2026 Cycle") that anchors to the bar. | Keep the select as a mirror of the bar. | Two controls for one state is exactly the confusion being fixed; a mirror also forces the nav to know each page's axis. |
| D2 | Propagation across top-nav jumps | **Scope follows the drill-down path** (list → detail, subnav tabs, search), **top-nav resets** to the section default. | Sticky site-wide (today's behaviour) via `scoped_url` on nav links too. | Predictable per page, profiles open in their natural scope, URLs stay shareable. Cost: a user who set "2024 Cycle" on Analytics must re-pick it on Donors. Reversible: a one-line change in the nav template. |
| D3 | Entity-profile default | **All time**, with a current-cycle subtotal beside the headline. | Inherit current cycle like every other page. | The $90.5M vs $497M case. A profile answers "who is this"; the cycle view is one click away and still visible as the subtotal. |
| D4 | Preset list | **Current cycle · Previous cycle · All time · Custom**, plus **Last 12 months**. Drop "Past 2 Years". | Keep all five today's presets. | Rolling 2-year windows add nothing a cycle does not, and they force cycle guessing on federal pages. "Last 12 months" is a question journalists actually ask. |

Decided here without asking (reversible, journal rows for the return check):

| id | decision | alternative not taken | reversible? | status |
|---|---|---|---|---|
| J1 | Keep query-param **names** (`period`, `date_from`, `date_to`, `cycle`, `year`); generalise **values** (`cycle:2026`) and map legacy values. | New names (`scope`, `from`, `to`). | yes | OPEN |
| J2 | `data_through` is per source (ISBE vs FEC vs 527 vs lobbying), read from the existing `global_data_status` cache, not queried per page. | Per-page `MAX(date)` query. | yes | OPEN |
| J3 | New module `time_scope.py` with `time_filter.py` as a shim during migration, deleted at the end of Phase 2. | Grow `time_filter.py` in place. | yes | OPEN |

---

## 5. Phased plan

Shared verification tooling (built in Phase 0 so every later step inherits it):

- `tests/test_time_scope.py` — resolver, legacy mapping, cycle computation under frozen dates, snap behaviour, cache key.
- `tests/test_scope_bar.py` — renders a page per family and asserts the bar text (resolved dates + axis) is present exactly once and matches the query.
- `scripts/audit/route_sweep.py` extended with two checks: (a) payload hash differs between `period=all` and `period=cycle:<current>` for scope-aware routes (exists today for the legacy keys); (b) every HTML response contains exactly one scope bar and every `<h2>` in an allow-list carries a tag.
- Fast gate per step: `pytest -q tests/test_time_scope.py tests/test_scope_bar.py tests/test_time_filter.py`. Phase gate: `pytest -q` + prod sweep.

### Phase Q — Quick wins (independent of the new architecture; ship first)

**Step Q1 — Stop freezing derived dates.** (Was 1.0.) Persist only *explicit* overrides: `webapp/routes/analytics.py` `_parse_filters` persist block, `webapp/routes/federal_finance.py` `_federal_subnav_persist_params`, `analytics/overview.html` "View full" links, `analytics/geo_drilldown.html` links; `applyTimePeriod` in `base.html` drops `date_from`/`date_to`/`cycle` when the preset changes. Verify: `tests/test_time_filter.py` new cases assert subnav/persist params carry no dates under a preset and do carry user-typed ones; `pytest -q tests/test_time_filter.py tests/test_federal_fec.py tests/test_webapp.py`.
**Step Q2 — Case-insensitive search. RETRACTED 2026-10-22.** Not a bug: `database/pg_compat.py:246-248` rewrites every `LIKE` to `ILIKE` before execution, so the raw `LIKE ?` text in `webapp/routes/main.py` is case-insensitive at runtime. Verified on prod: `/search?q=pritzker` finds "JB Pritzker". A regression test (`tests/test_webapp.py::test_search_is_case_insensitive`) now pins this.

### Phase 0 — Foundation (no visible change)

**Step 0.1 — Resolver, tests first.** Targets: `webapp/utils/time_scope.py`, `tests/test_time_scope.py`. Verify: `pytest -q tests/test_time_scope.py` (red → green).

Contract for 0.1 (the tests are written against this; the implementation must satisfy it exactly):

```python
# webapp/utils/time_scope.py — pure functions, no Flask import except `request` inside resolve_time_scope when args is None
from dataclasses import dataclass
from datetime import date

AXIS_LABELS = {"received": "by receipt date", "filed": "by filing date", "transaction": "by transaction date",
               "reg_year": "by registration year", "cycle": "by election cycle", "none": ""}

@dataclass(frozen=True)
class TimeScope:
    key: str                  # "cycle:2026" | "all" | "last12m" | "custom"
    start: date | None        # None = unbounded
    end: date | None          # None = open-ended
    axis: str                 # key of AXIS_LABELS
    granularity: str          # "day" | "year" | "cycle"
    data_through: date | None # latest loaded date for the page's source (caller supplies; may be None)
    is_default: bool
    is_custom: bool
    label: str                # "2026 Cycle" | "All time" | "Last 12 months" | "Custom range" | "Year 2024"
    window_label: str         # "Jan 1, 2025 – Oct 21, 2026" (see rules)
    axis_label: str           # AXIS_LABELS[axis]
    snap_note: str            # "" or e.g. "shown as registration years 2025–2026"

def current_cycle(today: date | None = None) -> int            # even year → year; odd → year + 1
def cycle_window(cycle: int) -> tuple[date, date]               # (Jan 1 of cycle-1, Dec 31 of cycle)
def preset_options(today: date | None = None) -> list[tuple[str, str]]
    # [("cycle:<cur>", "<cur> Cycle"), ("cycle:<cur-2>", "<cur-2> Cycle"), ("last12m", "Last 12 months"), ("all", "All time")]
def parse_scope_args(args, *, default: str = "cycle:current", today: date | None = None) -> dict
    # args: any Mapping with .get (request.args or a plain dict). Returns {key, start, end, label, is_custom, is_default}.
    # Precedence: date_from/date_to  >  year  >  cycle  >  period  >  default.
    # period values: "cycle:YYYY" (odd YYYY snaps to YYYY+1), "all", "last12m" (today-365d .. today), "custom" (treated as default unless dates given).
    # Legacy values mapped: "2026cycle"→"cycle:2026", "2024cycle"→"cycle:2024", "1y"→"last12m", "2y"→ custom window (today-730d .. today) label "Past 2 years";
    #   unknown/invalid → default. default "cycle:current" → f"cycle:{current_cycle(today)}".
    # date_from / date_to: ISO YYYY-MM-DD; invalid strings are ignored (treated as absent); one bound alone leaves the other None; start > end → swapped.
    #   When either bound is present: key="custom", is_custom=True, label="Custom range".
    # year=YYYY (and no dates): key="custom", start=Jan 1, end=Dec 31, label="Year YYYY", is_custom=True.
    # cycle=YYYY (and no dates/year): key=f"cycle:{snapped}", is_custom=False.
    # is_default: True iff not is_custom and key == resolved default key.
def resolve_time_scope(*, default: str = "cycle:current", axis: str = "received", today: date | None = None,
                       args=None, data_through: date | None = None) -> TimeScope
    # args None → flask.request.args. granularity: axis "reg_year"→"year", "cycle"→"cycle", else "day".
    # Snapping (granularity != day, bounded side only): year → start=Jan 1 of start.year, end=Dec 31 of end.year;
    #   cycle → start=Jan 1 of (cycle_of(start)-1), end=Dec 31 of cycle_of(end), where cycle_of(d)=current_cycle(d).
    #   snap_note = "shown as registration years A–B" (year) / "shown as election cycles A–B" (cycle), with A==B collapsed to one value; "" when nothing snapped or granularity day.
    # window_label: effective_end = min(end, data_through) if both else (end or data_through).
    #   both start and effective_end → "Jan 1, 2025 – Oct 21, 2026"; only effective_end → "Through Oct 21, 2026"; only start → "Since Jan 1, 2025"; neither → "All available data".
    #   Month names abbreviated (Jan, Feb, …, Sept→"Sep"), no leading zero on day, en dash with spaces.
def scope_params(scope: TimeScope) -> dict      # presets: {"period": key}; custom: {"date_from": iso, "date_to": iso} (only bounds that exist); never both forms
def scope_cache_key(scope: TimeScope) -> str    # f"{axis}:{start iso or ''}:{end iso or ''}"
def scope_where(scope: TimeScope, column: str, *, style: str = "qmark", prefix: str = "AND") -> tuple[str, list]
    # ("", []) when both bounds None; else f" {prefix} {column} >= ? AND {column} <= ?" with params as ISO strings (only present bounds); style "named" uses :ts_start / :ts_end and returns (sql, dict)
def scope_cycles(scope: TimeScope) -> tuple[int, ...] | None   # cycles overlapping [start, end]; None when start and end are both None; a one-sided bound uses today for the open side
```

Resolutions of the tester's ambiguities (2026-10-22), binding on the implementation:
- Cycle presets are bounded: `cycle_window` ends Dec 31 of the cycle (display clamps to `data_through`).
- `snap_note` with A==B uses the singular: "shown as registration year 2025" / "shown as election cycle 2026".
- `period=2y` → custom window, label "Past 2 years", `is_custom=True`.
- `scope_where(style="named")` for all-time returns `("", {})`; one-sided windows emit only the present clause/param.
- `scope_cycles`: start-only → cycles from cycle_of(start) through `current_cycle(today)`; end-only → `None` (unbounded below).
- `scope_params` always emits `period` for presets, even the default.
- Snapped presets keep their key and `is_custom=False`; `scope_cache_key` uses post-snap bounds.
- Invalid `year`/`cycle` values are ignored and precedence falls through to the next source.
- Extra axis `report_period` → label "by reporting period end", granularity day (used by the candidate-finance list, whose rows are D2 report periods).


**Step 0.2 — Bar partial + tag macro + CSS.** Targets: `webapp/templates/_partials/time_scope_bar.html`, `_partials/macros.html`, `webapp/static/css/style.css` (bump the `v=` cache-buster in `base.html`). Verify: `pytest -q tests/test_scope_bar.py`.
**Step 0.3 — Per-route inventory.** Folded into Appendix A of this plan (one row per HTML route, today's behaviour) plus the targets table in §3.5; no separate doc (J5). Each Phase 1/2 step marks its rows done in the STATE file.
**Step 0.4 — Sweep extension.** Target: `scripts/audit/route_sweep.py`. Verify: run against local `runserver`, report shows bar/tag checks.

### Phase 1 — Fix the traps (shippable on its own)

**Step 1.1 — Generic current cycle.** Replace every `2026cycle` literal (`webapp/routes/main.py`, `webapp/routes/analytics.py`, `scripts/audit/route_sweep.py`) with `current_cycle()`; presets generated. Verify: `tests/test_time_scope.py::test_rollover_2027` + `pytest -q tests/test_webapp.py`.
**Step 1.2 — Candidate finance: overrides replace scope.** Targets: `webapp/routes/candidate_finance.py`, `candidate_finance/list.html`, `itemized.html`, `itemized_expenditures.html`. `year`/`cycle` selects become scope shorthands; chip → bar. Verify: regression test `q=Pritzker&year=2024` returns the 2024 rows (today: 0) + `pytest -q tests/test_webapp.py tests/test_isbe_rewire.py -k candidate_finance` (the candidate-finance tests live there today).
**Step 1.3 — Entity profiles: bar + tags + D3 default.** Includes the federal candidate detail (Appendix A #47, no label today) and the SBE committee redirect that drops the scope (`webapp/routes/committees.py:687`). Targets: `committees/detail_sbe.html`, `committees/detail.html`, `donors/detail_by_key.html`, `donors/detail.html`, `federal_finance/detail.html`, `federal_finance/donor_detail.html`, `irs527/detail.html`, `lobbying/entity_detail.html`, `lobbying/client_detail.html`, `openbook/detail.html` and their routes. Add the current-cycle subtotal next to all-time headline totals. Verify: `tests/test_scope_bar.py` family cases + sweep check (b).
**Step 1.4 — Search results tagged; candidate search honours scope or says "All time".** Also remove or wire the Investigate page's cosmetic Cycle box (Appendix A.2 spot 9). Targets: `webapp/routes/main.py` (`search`, `_search_local_candidates`), `search.html`. Verify: `pytest -q tests/test_webapp.py -k search`.

### Phase 2 — One control everywhere

**Step 2.1 — Chip → bar on analytics, federal, live feed; section tags.** Targets: `analytics/_subnav.html`, `federal_finance/_subnav.html`, `live_feed.html`, the eight analytics and sixteen federal templates (tags only). Delete `_partials/active_filter_chip.html` and `build_filter_overrides`. Make the analytics `months` cap scope-aware so "All time" is not silently cut to 24 months (spot 10); drop the echo-only `range` param. Verify: `pytest -q tests/test_federal_fec.py tests/test_analytics_date_semantics.py tests/test_scope_bar.py`.
**Step 2.2 — Lists + lobbying (closes P2-2).** Targets: `committees/list.html`, `donors/list.html`, `reports/list.html`, `irs527/list.html`, `openbook/list.html`, `lobbying/list.html`, `lobbying/flows.html` and routes. Lobbying axis `reg_year` with year snap; the Sankey fetch forwards the scope (spot 5). 527 list stops COALESCE-ing to all-time report totals when a window is active (spot 2) so list, detail and home card agree. Committees list ISBE path honours the scope (today it never reads it). Verify: sweep check (a) for each route.
**Step 2.3 — Server-side propagation; remove nav select and JS rewriter (D1, D2).** Targets: `base.html`, `webapp/app.py` (context processor exposes `time_scope` and `scoped_url`), `webapp/utils/time_scope.py` (endpoint registry). Verify: `tests/test_scope_bar.py::test_drilldown_links_carry_scope`, `::test_topnav_links_do_not`, full `pytest -q`.
**Step 2.4 — Cache keys on resolved window; prewarm alignment.** Targets: `webapp/routes/main.py`, `webapp/routes/analytics.py`, `webapp/routes/federal_finance.py`, `webapp/routes/candidate_finance.py`, `webapp/routes/irs527.py`, `config.py` if TTL names change. Verify: `pytest -q tests/test_app_performance.py`; re-measure the four baselines in `docs/runbooks/route_perf_caching.md` (cold + 2 warm) and record before/after.
**Step 2.5 — Delete `time_filter.py` shim; grep gate.** Verify: `grep -rn "get_active_period\|period_where_clause\|time_filter" webapp database scripts tests` returns nothing; `pytest -q`.

### Phase 3 — Docs, sweep, deploy

**Step 3.1 — Docs.** `README.md` (global period → time scope), `CLAUDE.md` (Performance & Caching invariants: cache keys include the resolved scope; time-filter semantics paragraph), `docs/runbooks/route_perf_caching.md`, `docs/audits/time_scope_inventory.md` marked final. Verify: grep for "global period" finds only historical audit docs.
**Step 3.2 — Prod sweep + deploy.** `pytest -q`, push, pull, restart, `scripts/audit/route_sweep.py --host https://www.followthemoneyil.com`, spot-check the five pages in section 1.2. Push fires CI on GitHub-hosted runners — ask before pushing (cost gate).

Estimated size: Phase 0 ≈ 1 session; Phase 1 ≈ 1–2 sessions; Phase 2 ≈ 2 sessions (2.4 is the risky one); Phase 3 ≈ half a session.

---

## 6. Risks and side findings

- **Cache rollover:** cycle presets are bounded (`cycle:2026` = 2025-01-01..2026-12-31) so their keys are stable; only `last12m` and `2y`-style custom windows roll daily. Prewarm must compute keys the same way. Covered by step 2.4's test.
- **Pre-aggregated federal candidate totals** (`fec_candidate_cycle_totals`) cannot honour a day-level window; the bar's snap text handles this honestly rather than hiding it.
- **Old shared links** with `?period=1y` or `?period=2y` resolve to the same dates as before (mapped in the resolver), so bookmarks do not change meaning.
- ~~Side finding: candidate-name search uses case-sensitive `LIKE`~~ **Retracted.** The compat layer rewrites `LIKE`→`ILIKE` (`database/pg_compat.py:246-248`); search is case-insensitive on prod.
- Side finding (verify): the base-template "Data Status" strip (`base.html:96`) does not render on prod — only the homepage's own "Data Freshness" block does. Either `global_data_status` is `None` on prod (swallowed exception in the context processor) or `has_any_data` is false. Worth a look because the bar's `data_through` will read from the same cache.

---

## Appendix A — Per-route inventory (seed for step 0.3)

Compiled 2026-10-22 by read-only code inspection (routes + templates); not browser-verified except where section 1.2 says so. 59 HTML-rendering routes: 28 honour the period, 19 partially, 12 not at all (6 of those are static or have no data). The one candidate detail page in the app is federal `/federal-finance/<candidate_id>`; state candidates have no detail page and are reached only through the candidate-finance list and itemized pages.

Legend. Date columns: RD = `received_date` (ISBE/bulk receipts); TD = legacy `contributions.transaction_date`; FD = `filed_date` (legacy `reports`; ISBE `received_datetime` for filed docs); RPE = `isbe_filed_docs.reporting_period_end` (D2 coverage); EX = `expended_date`; IR = irs527 text `date`; OB = OpenBook `contribution_date` / `issue_date`; A / B / E = FEC Schedule A `contribution_receipt_date` / B `disbursement_date` / E `expenditure_date`; FC = FEC `cycle` (not a date). Chip = `_partials/active_filter_chip.html`, reached directly or through `analytics/_subnav.html` / `federal_finance/_subnav.html`. The nav Period select is global and is not counted as a page control.

| # | module | route | function (def line) | template | period honoured | local overrides | shows range? | range control? | notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | main | `/` | index (2709) | index.html | PARTIAL: RPE (D2 totals); RD top donors (only when period≠all); FC federal counts/totals (plural `period_cycles`); IR dark-money and 527 cards; everything else all-time (local counts, lobbying, openbook, 527 org count, freshness) | none | badge with label + description (index.html:8) | none | one badge over mixed date semantics |
| 2 | main | `/candidates` | candidates (2814) | candidates.html | PARTIAL: RPE (local receipts/expenditures); FC (federal totals, plural cycles); local counts all-time | none | none | none | silent |
| 3 | main | `/search` | search (2840) | search.html | PARTIAL: FD (reports), FD/RD (filed docs), RD (donor-key matches). Committee and donor primary results are all-time (`isbe_committees` main.py:1060; `analytics_donor_summary` main.py:1150); only legacy fallbacks window. Candidates all-time | q, type | none | none | donor totals in results ignore period |
| 4 | main | `/live-feed` | live_feed (3363) | live_feed.html | Y: RD (local), A, B, E | date_from, date_to, format, table, *_limit | chip (live_feed.html:9) | none (URL only) | only page where chip and filter agree; CSV uses same window |
| 5 | main | `/investigate` | investigate_workspace (2378) | investigate.html | N: period never read. `cycle` (default 2026, main.py:2386) is cosmetic: label "cycle 2026" (main.py:2445) but the federal SUM is unfiltered (2420–2436). `month` filters `analytics_large_contributions` | cycle, month, q, tab, advanced, format | none | cycle text (investigate.html:40), month select (:44) | visible Cycle box that filters nothing |
| 6 | main | `/person-intelligence` | person_intelligence (3045) | person_intelligence.html | N (all-time) | q | none | none | |
| 7 | main | `/compare` | compare (3505) | compare.html | N (all-time profiles) | mode, left, right | none | none | |
| 8 | main | `/legacy` | legacy (2825) | legacy.html | N (all-time counts) | none | none | none | |
| 9 | main | `/about` | about (2697) | about.html | N (static) | none | none | none | n/a |
| 10 | main | `/privacy` | privacy (2703) | privacy.html | N (static) | none | none | none | n/a |
| 11 | candidate_finance | `/candidate-finance/` | list_candidate_finance (59) | candidate_finance/list.html | Y with caveats: window on RPE (`period_end_date`); cycle from singular `period_cycle`, so "2y" drops 2024-cycle rows (candidate_finance.py:87) | start_date, end_date (no input), year, cycle | chip (list.html:27) | start_date (list.html:41), year select (:42), cycle select (:48) | dropdowns are data-driven, not period |
| 12 | candidate_finance | `/candidate-finance/<cid>/<cmte>/itemized` | candidate_committee_itemized (274) | candidate_finance/itemized.html | Y: RD for rows and count; no $ headline | date_from, date_to, q, d2_part, archived, min/max_amount, sort, dir, page, format | chip (itemized.html:25) | date inputs (:53–54) | cleanest detail page |
| 13 | candidate_finance | `/candidate-finance/<cid>/<cmte>/itemized-expenditures` | candidate_committee_itemized_expenditures (441) | candidate_finance/itemized_expenditures.html | Y: EX | date_from, date_to, q, d2_part, archived, anomalies_only, min/max_amount | chip (:26) | date inputs (:54–55) | same as 12 |
| 14 | committees | `/committees/` | list_committees (455) | committees/list.html | PARTIAL: ISBE primary path reads no period, shows all-time `isbe_committee_money` totals. Only the legacy fallback is TD-windowed (committees.py:519–535) | q, sort, dir, page | none | none | sort by Total Contributions is all-time |
| 15 | committees | `/committees/<id>` | committee_detail (593) | committees/detail.html (legacy row) or committees/detail_sbe.html | legacy: FD (reports), TD (contributions, total). detail_sbe: RD receipts and EX expenditure summaries plus top donors/payees windowed; filing history, officers, candidate links all-time | report_sort/dir, contrib_sort/dir, page | detail_sbe: badge on Receipts/Expenditures headings (detail_sbe.html:44, 54); "Date range" is min/max of filtered rows (:47–48, :57–58), not the window. legacy: none | none | |
| 16 | committees | `/committees/sbe/<id>` | committee_detail_by_sbe (674) | detail_sbe.html, or 302 | as 15 when rendered. When a legacy row matches, the 302 to `/committees/<id>` drops `?period=` (committees.py:687) | none | n/a | none | redirect lands on default cycle; linked from committees/list.html:40 |
| 17 | committees | `/committees/filing/<id>` | filing_detail (691) | committees/filing_detail.html | N (single filing) | none | none | none | n/a |
| 18 | donors | `/donors/` | list_donors (11) | donors/list.html | Y: RD when period≠all (recomputed); "all" uses the materialized all-time directory; TD for legacy source; count windowed | page, sort, dir | none | none | "N total donors" unlabeled |
| 19 | donors | `/donors/<donor_id>` | donor_detail (51) | donors/detail.html | Y: TD | page, sort, dir | none | none | "Total Contributed" unlabeled (detail.html:12) |
| 20 | donors | `/donors/key/<donor_key>` | donor_detail_by_key (109) | donors/detail_by_key.html | Y: RD (window branch; all-time analytics when no window) | source, page, sort, dir | none | none | unlabeled (verified live, section 1.2) |
| 21 | donors | `/donors/entity/<entity_id>` | donor_detail_by_entity (178) | donors/detail_by_key.html | Y: RD (as 20) | source, page, sort, dir | none | none | unlabeled |
| 22 | reports | `/reports/` | list_reports (11) | reports/list.html | Y: FD on legacy `reports`; status counts windowed | paper_filed, sort, dir, page | none | none | legacy table |
| 23 | reports | `/reports/<report_id>` | report_detail (70) | reports/detail.html | N | none | none | none | n/a |
| 24 | lobbying | `/lobbying/` | list_entities (658) | lobbying/list.html | N (period not read) | q, page | none | none | P2-2 never done |
| 25 | lobbying | `/lobbying/<entity_id>` | entity_detail (751) | lobbying/entity_detail.html | PARTIAL: money destinations, first/last donation RD-windowed via `_resolved_time_window`; donor matches, expenditure matches, clients all-time | date_from, date_to (not passed to template) | none; "First/Last donation" (entity_detail.html:23–24) are windowed min/max | none (URL only) | window invisible |
| 26 | lobbying | `/lobbying/client/<client_id>` | client_detail (833) | lobbying/client_detail.html | PARTIAL: same split as 25 | same | same | none | |
| 27 | lobbying | `/lobbying/flows` | flows (922) | lobbying/flows.html | N in practice: Sankey data fetched from `/lobbying/flows/data` with no period param (flows.html:37), so it always resolves to the default cycle (lobbying.py:933) | client | none | client filter only | nav Period ignored |
| 28 | irs527 | `/527/` | list_orgs (167) | irs527/list.html | PARTIAL: IR-windowed subqueries, but COALESCE prefers all-time `irs527_reports` totals (irs527.py:275–276) and sorts on them (:295). Any org with report rows ignores the window | date_from, date_to, q, page, sort, dir | none | none | disagrees with home card (windowed, main.py:2743–2771) and with detail |
| 29 | irs527 | `/527/<ein>` | org_detail (327) | irs527/detail.html | PARTIAL: "Financial Summary" IR-windowed (irs527.py:391–426); per-filing table, directors, matches all-time | date_from, date_to, contrib_q, page | none | none | summary ≠ list totals |
| 30 | irs527 | `/527/dark-money` | dark_money (633) | irs527/dark_money.html | Y: IR (cache keyed by period+dates, irs527.py:96) | date_from, date_to | none (subtitle only) | none | |
| 31 | openbook | `/openbook/` | list_vendors (41) | openbook/list.html | PARTIAL: contract_count and total_award_amount all-time (openbook.py:149); contribution_count windowed on OB `contribution_date` | date_from, date_to, q, page | none | none | |
| 32 | openbook | `/openbook/<vendor_key>` | vendor_detail (182) | openbook/detail.html | PARTIAL: contracts and award_total all-time (openbook.py:307); warrants windowed (issue_date); contributions windowed (contribution_date) | date_from, date_to | none | none | |
| 33 | federal_finance | `/federal-finance/`, `/overview` | federal_overview (337) | federal_finance/overview.html | PARTIAL: A (race analytics, geography, network) and E (outside spending) windowed; candidate_count cycle-only; cycle via singular `period_cycle` | cycle, analysis_office, analysis_district, date_from, date_to, refresh_cache | chip via `_subnav` | cycle text (overview.html:13); no date inputs | subnav freezes derived dates (spot 1). Under All Time the cycle box shows 2026 while data spans all cycles (federal_finance.py:60) |
| 34 | federal_finance | `/federal-finance/candidates` | federal_candidates (467) | federal_finance/candidates.html | N for dates: list and count are cycle-only (docstring, federal_finance.py:470–473) | cycle, office, party, page | chip, misleading (shows a window not applied) | cycle text (candidates.html:39) | |
| 35 | federal_finance | `/federal-finance/networks` | federal_networks (539) | federal_finance/networks.html | Y: A | cycle, date_from/to, network_* | chip | cycle (networks.html:13) | |
| 36 | federal_finance | `/federal-finance/donor-intelligence` | federal_donor_intelligence (620) | federal_finance/donor_intelligence.html | Y: A | cycle, dates, segmentation_*, cluster_limit | chip | cycle (:13) | |
| 37 | federal_finance | `/federal-finance/money-flow` | federal_money_flow (735) | federal_finance/money_flow.html | Y: A, B, E | cycle, dates | chip | cycle (:13) | |
| 38 | federal_finance | `/federal-finance/influence` | federal_influence (794) | federal_finance/influence.html | Y: A | cycle, dates | chip | cycle (:13) | |
| 39 | federal_finance | `/federal-finance/follow-the-money` | federal_follow_the_money (829) | federal_finance/follow_the_money.html | Y: A | cycle, dates, follow_* | chip | cycle (:13) | |
| 40 | federal_finance | `/federal-finance/geography` | federal_geography (879) | federal_finance/geography.html | Y: A | cycle, dates | chip | cycle (:13) | |
| 41 | federal_finance | `/federal-finance/geo-drilldown` | federal_geo_drilldown (908) | federal_finance/geo_drilldown.html | Y: A | cycle, dates, range (echo only, federal_finance.py:922), geo_*, source_page, page, per_page, sort, dir | chip (subnav) | none (links only) | `range` only partitions cache and links |
| 42 | federal_finance | `/federal-finance/matching` | federal_matching (1024) | federal_finance/matching.html | Y: A | cycle, dates, match_*, local_*, overlap_edge_limit | chip | cycle (matching.html:12) | |
| 43 | federal_finance | `/federal-finance/matches/<fed>/<local>` | federal_matched_donor_profile (1134) | federal_finance/match_profile.html | Y: A | cycle, dates | chip (subnav) | none | |
| 44 | federal_finance | `/federal-finance/donors/<donor_entity_key>` | federal_donor_detail (1267) | federal_finance/donor_detail.html | Y: A | cycle, dates | NONE (no subnav, no chip) | none | window invisible |
| 45 | federal_finance | `/federal-finance/committees/<id>/receipts` | federal_committee_receipts (1310) | federal_finance/committee_receipts.html | Y: A | cycle, dates, receipt_* | chip (subnav) | none | |
| 46 | federal_finance | `/federal-finance/races/<office>/<district>/outside-spending` | federal_race_outside_spending (1372) | federal_finance/race_outside_spending.html | Y: E | cycle, dates, support_oppose, schedule_e_* | chip (subnav) | cycle (:36) | |
| 47 | federal_finance | `/federal-finance/<candidate_id>` | federal_candidate_detail (1498) | federal_finance/detail.html | PARTIAL: headline money in/out, total, outside spending are cycle-level reported totals (database/federal_fec.py:4776–4786); Schedule A/B/E rows and "Showing X of Y" windowed | cycle, date_from, date_to, contribution_page, schedule_b_*, schedule_e_* | NONE (no chip, no label, no subnav) | none | the page where a visitor cannot tell what the numbers cover |
| 48 | analytics | `/analytics/`, `/overview` | dashboard (440) | analytics/overview.html | PARTIAL: RD for anomaly flags, donor concentration, monthly series, donor→committee edges, state race analytics (RD/EX). `months` (default 24) truncates after the window. All-time: NLP summary (analytics.py:529), reconciliation outliers (:530), committee→candidate edges | date_from, date_to, months, load_mode, election_cycle, refresh_cache, state_race_sort/dir | chip via `_subnav` | date_from/to (overview.html:22, 26), months (:30), load_mode | subnav and "View full" links freeze derived dates (overview.html:192, 222, 253, 271–277) |
| 49 | analytics | `/analytics/state-races/<race_key>` | state_race_detail (605) | analytics/state_race_detail.html | Y: RD/EX | date_from, date_to, election_cycle | chip (subnav) | none | |
| 50 | analytics | `/analytics/networks` | networks (661) | analytics/networks.html | PARTIAL: RD donor→committee edges; lobbying influence (RD/EX) and 527 ecosystem (IR) windowed; vendor network (analytics.py:713) and state-federal overlap (:715) all-time | date_from, date_to, min_edge_amount, network_limit, load_mode | chip | date_from/to (networks.html:22, 26) | |
| 51 | analytics | `/analytics/risk` | risk (768) | analytics/risk.html | PARTIAL: anomaly flags (RD) windowed; reconciliation outliers all-time (analytics.py:808) | date_from, date_to, anomaly_limit, recon_* | chip | date_from/to (risk.html:21, 25) | |
| 52 | analytics | `/analytics/donors` | donors (833) | analytics/donors.html | PARTIAL: donor concentration (RD) windowed; NLP summary all-time (analytics.py:850) | date_from, date_to, concentration_limit, nlp_limit | chip | dates (donors.html:21, 25) | |
| 53 | analytics | `/analytics/geography` | geography (862) | analytics/geography.html | Y: RD geo summary and monthly series; `months` cap applies | date_from, date_to, months, geo_*_limit | chip | dates (:21, 25), months (:29) | |
| 54 | analytics | `/analytics/geo-drilldown` | geo_drilldown (897) | analytics/geo_drilldown.html | Y: RD | date_from, date_to, range (echo only, analytics.py:906), geo_*, source_page, months | chip (subnav) and inline "Date window:" (geo_drilldown.html:52) | none (links only) | `range` cosmetic |
| 55 | analytics | `/analytics/relationships` | relationships (983) | analytics/relationships.html | PARTIAL: lobbying influence and 527 ecosystem windowed; donor co-giving, committee similarity, candidate competition all-time | date_from, date_to (hidden inputs), candidate_limit | chip (subnav) | none (hidden passthrough, relationships.html:13–14) | |
| 56 | d2_receipts_recon | `/d2-reconciliation/` | list_d2_receipts_recon (34) | d2_receipts_recon/list.html | Y: RPE selects D2 filings; per-filing sums unaffected | q, sort, dir, page, min_abs_diff, min_receipt_rows, format | none | none | no label |
| 57 | d2_expenditures_recon | `/d2-expenditures-reconciliation/` | list_d2_expenditures_recon (34) | d2_expenditures_recon/list.html | Y: RPE | q, sort, dir, page, min_abs_diff, min_expenditure_rows, anomalies_only, format | none | none | no label |
| 58 | experimental | `/experimental/viz-lab`, `/experimental/viz-lab/<prototype_key>` | viz_lab (1209) | experimental/viz_lab.html | Y via JSON data endpoints (JS forwards period and date_from, experimental_viz_lab.js:26–35) | date_from, date_to, period | YES: "Current period … Date window …" (viz_lab.html:16) | date_from/date_to inputs (:23, 27) | the most explicit page; local-only |
| 59 | viz_lab | `/experimental/viz-lab/state-maps` | viz_lab_state_maps (77) | experimental/viz_lab_state_maps.html | N: no queries; period only in "Back to Lab" link | none | none | none | |

Excluded: `api.py` (JSON only), JSON endpoints in `viz_lab.py` / `experimental.py`, `/suggest`, `/flows/suggest`, `/flows/data`, `auth.py`, `manual_entry.py`, `admin.py`.

### A.2 Spots to fix, ranked (file:line)

1. **Derived dates frozen into links** (verified live 2026-10-22). `webapp/routes/analytics.py:161-162` and `webapp/routes/federal_finance.py:128-129` (`_federal_subnav_persist_params`) persist the period's *resolved* `date_from`/`date_to`; `analytics/overview.html:192, 222, 253, 271-277` and `analytics/geo_drilldown.html:16-17` do the same in "View full" links. One tab click → chip says "Custom filter active" with dates the user never set. `base.html:178-184` (`applyTimePeriod`) keeps `date_from`/`date_to`, so the nav selector then changes nothing.
2. **527 list ignores the window for orgs with reports.** `webapp/routes/irs527.py:275-276` COALESCEs to all-time `irs527_reports` totals and sorts on them (`:295`). Detail (`:391-426`) and the home card (`webapp/routes/main.py:2743-2771`) are windowed, so three views disagree.
3. **Federal candidate detail mixes grains with no label.** `federal_finance/detail.html` has no chip and no subnav. Headline is cycle-level (`database/federal_fec.py:4776-4786`); rows are windowed (`webapp/routes/federal_finance.py:1498`).
4. **Singular vs plural cycle mapping.** `webapp/utils/time_filter.py:206` collapses "1y"/"2y" to one cycle; federal (`federal_finance.py:53`) and candidate-finance (`candidate_finance.py:87`) use it, home uses the plural (`main.py:471`). Federal "Past 2 Years" = cycle 2026 only; home "Past 2 Years" includes 2024. The plural value computed at `federal_finance.py:92` is never consumed. Resolved by D4 + the resolver's snap rule.
5. **Lobbying Sankey ignores the period.** `lobbying/flows.html:37` fetches with only `client`; `webapp/routes/lobbying.py:933` falls back to the default cycle.
6. **SBE committee redirect drops the period.** `webapp/routes/committees.py:687`, reached from `committees/list.html:40`.
7. **Home and candidates pages: one badge over mixed grains.** `index.html:8`; local counts all-time, D2 on RPE (`main.py:536-539`), federal on cycles (`main.py:470-475`), top donors switch to all-time (`main.py:814-827`). `candidates.html` has no period reference at all.
8. **Search results mix grains silently.** Committee primary `main.py:1060-1061` and donor primary `:1150` are all-time; only legacy fallbacks window. No label in `search.html`.
9. **Investigate's Cycle box filters nothing.** `main.py:2386` default 2026; SUM at `:2420-2436` unfiltered; label `:2445`; input `investigate.html:40`.
10. **Analytics `months` cap and echo-only `range`.** `webapp/routes/analytics.py:139` (default 24) and `database/analytics.py:2132` truncate even "All Time" to 24 months. `range` at `analytics.py:906` and `federal_finance.py:922` is cosmetic.

Caveats: code-path confidence high; prod-data claims medium. Spot 2 assumes `irs527_reports` is populated on prod (the loader writes it; counts not checked). Spot 6 applies only when a legacy `Committee` row matches the SBE id.
