# Committee families — design (suggestions item C8; plan step S-C8)

**Date:** 2026-10-26 · **Status:** built in plan step S-C8 (approved by Devin, "Go on all the suggestions"): schema, seeder, admin review, committee banner, candidate-profile grouping and the committees-list rollup exist. Local `ilcf` holds 753 pending families and none is accepted yet, so no public page shows a family. Sections below describe what shipped; pieces that were designed but not built are marked **not built**.

## Problem
ISBE issues a new committee id every time an actor re-registers, and filers vary the name
("Committee to Elect Dawn Reglin" / "Committee to Re Elect Dawn Reglin", "Friends of Judy Martini" /
"Friends Of Judy Lynne Martini"). Readers see the same campaign as several unrelated committees,
and per-committee totals split a career across rows. At the same time, name similarity alone groups
genuinely distinct committees ("… Action PAC" / "… Action IE PAC", same-named referendum committees in
different districts), so nothing may be merged automatically.

## Decision: group, never merge
- Committee ids, pages, URLs and per-committee totals stay exactly as they are.
- A **family** is a reviewable grouping with evidence. Public pages show the grouping (banner,
  grouped rows, optional rollup) only for families an admin has **accepted**.
- Seeding is an evidence rule run by a script; acceptance is a human decision in `/admin`.

## Evidence rule (seed)
A pair of committees is linked when **they share a linked candidate** (`isbe_candidate_committees`)
**and** at least one corroboration holds:
1. identical normalized name (`lower`, non-alphanumerics collapsed to single spaces), or
2. `pg_trgm` `similarity(norm_name_a, norm_name_b) >= 0.8`, or
3. a shared officer name (`isbe_officers`, normalized first + last), or
4. the same non-blank normalized street address + zip (`isbe_committees.address1`, `zipcode`).

Linked pairs are unioned into families (connected components). Pairs that are >= 0.8 similar and
share a zip but **no candidate** are kept as a separate **weak** queue: never seeded as families,
listed for review so a missing candidate link can be added upstream or the pair dismissed.

The rule and its SQL live in `database/committee_families.py` (`propose_families`), so both commands apply
the same code; `scripts/committee_families.py` is the CLI (`report` = read-only dry run; `seed` writes
pending families, idempotent on `member_hash`). The seeder reads only `isbe_committees`,
`isbe_candidate_committees` and `isbe_officers`; the similarity clause is skipped where `pg_trgm` is not
installed (CI).

As built the strong-pair query is one SELECT: names, streets and zips are normalized in a
`cm AS NOT MATERIALIZED` CTE (the hint is load-bearing; keep it) and officer names are aggregated once per
committee in an `officer_names` CTE — a CTE, not the temp table first planned, and not a per-pair `EXISTS`
(the first version took 174.5 s on local `ilcf`; this one 0.34 s). Stored evidence holds only what is true:
`same_name`, `similar_name` (best near-miss score), `shared_officer`, `same_address` and
`shared_candidates`. A family's label is the as-filed name of its most recently registered active member
(any member when none is active; ties: higher committee id).

## Dry run (local `ilcf`, 2026-10-26, `python3 scripts/committee_families.py report --limit 30`, 2 min 48 s with the first query version)
- Strong pairs: **915** (same name 205, similar-but-not-identical 141, shared officer 238, same address 453).
- Proposed families: **753 covering 1,624 committees**; sizes 2: 659 · 3: 76 · 4: 13 · 5: 4 · 6: 1.
- Weak pairs (review only): **263**.
- `seed` on local `ilcf` (the shipped single-SELECT query) reproduced the proposal: **753 pending families /
  1,624 committees**; a second run created none (idempotent on `member_hash`).
- Largest proposals are slates and serial re-registrations: "Friends of Wisniewski" (6 ids 2001–2010:
  "Committee to Re-Elect Dralle & Riley" → "Friends of Riley, Dralle & Wisniewski" → "Friends of
  Wisniewski" ×2 plus the two individual funds); "Friends of Judy Martini" (5 ids 1992–2015); township
  party committees sharing an address and candidates with a candidate's personal fund ("Citizens for
  Viverito" + three "Township … Party" committees). The last pattern is correct by the rule but is
  exactly what review is for: a reviewer may split the slate from the personal fund.
- Full sample: `scratchpad/families_dry_run.md` (session scratchpad; regenerate with the command above).

## Schema (`database/schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS committee_family (
    id            BIGSERIAL PRIMARY KEY,
    label         TEXT NOT NULL,                 -- seed: name of the most recently active member
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    evidence      JSONB NOT NULL DEFAULT '{}',   -- {"same_name":true,"similar_name":0.83,"shared_officer":true,"same_address":true,"shared_candidates":[...]}
    member_hash   TEXT NOT NULL UNIQUE,          -- sha1 of the sorted member ids → idempotent re-seeding
    notes         TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at   TIMESTAMP
);
CREATE TABLE IF NOT EXISTS committee_family_member (
    family_id     BIGINT NOT NULL REFERENCES committee_family(id) ON DELETE CASCADE,
    committee_id  INTEGER NOT NULL,              -- isbe_committees.id (no FK: the ETL drops/reloads isbe_*)
    evidence      JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (family_id, committee_id)
);
CREATE INDEX IF NOT EXISTS idx_committee_family_member_committee ON committee_family_member (committee_id);
```
Re-seeding: a family whose member set already exists (`member_hash`) is left alone (its status and
notes survive); new member sets are inserted as `pending`; families whose members no longer exist in
`isbe_committees` are reported, not deleted. A committee may belong to at most one accepted family
(enforced in the decision handler, not the schema). Because identity is the member set, a later `seed`
after a member removal (or after a family grows) proposes the new member set as a **new pending family**
next to the reviewed one; it has no public effect until someone accepts it, and rejecting it keeps it from
coming back (a rejected row keeps its hash).

## Admin review (`webapp/routes/admin.py`, modelled on `/admin/donor-merges`)
Built: the three routes below (`login_required`; templates `admin/committee_families_list.html` and
`admin/committee_family_detail.html`; linked from the admin hub).
- `/admin/committee-families?status=pending|accepted|rejected&page=` — list: label, committees, active now,
  evidence badges, review status, reviewed date; fixed order (exact-name families first, then largest, then
  id); 50 per page; status counts. **Not built:** the `q` filter, sort controls and a last-activity column.
- `/admin/committee-families/<id>` — label, status, evidence badges, shared candidates (linked to their
  profiles) and a member table (id, name linking to the committee profile, type, active, created, linked
  candidates, per-member evidence badges); actions **accept**, **reject** and **remove member** (POST
  `/decision`, `flash` + redirect, CSRF token). **Not built:** last activity with its kind, and officers.
- Decision rules (J-S21): accepting is refused while a member already sits in a different accepted family;
  removing a member is refused when it would leave fewer than two members or give the family the member set
  of another family; a removal rewrites `member_hash`.
- **Not built:** the "Weak (no shared candidate)" tab and its "create family" action. The 263 weak pairs are
  only printed by `scripts/committee_families.py report`; add the tab if the review queue needs it.

## Display (accepted families only)
- **Committee profile**: a banner under the scope bar — "Part of the *Friends of X* family: 3 committees,
  1 active ·" followed by links to the sibling committee profiles (inactive ones marked), carrying the page's
  scope. **Not built:** a link to the candidate profile when all members share one candidate.
- **Candidate profile** (plan C1): committees that share one accepted family (two or more of the
  candidate's own, J-S21) render under a family heading with the member count; every other committee stays
  its own row. A block sits where its newest member sits, so the order stays by last activity.
- **Opt-in rollup** `?by=family` on the **committees list only** (`/committees/?by=family`, toggled by a
  "Group by family" / "One row per committee" link): the members of an accepted family collapse to one row
  carrying the family label, the member count and the summed totals (windowed like the default list); the
  row links to the family's active (else newest) member and `q` matches the label or any member name. The
  default remains one row per committee. The race detail's committee rows keep one row per committee: that
  rollup is **deferred (J-S18)** until families have been reviewed in prod, because a rollup over unreviewed
  proposals would show wrong groupings and the accepted set is empty until the review happens.
- "Last activity" for families is **not built** (the admin pages show the `active` flag only). If it is
  added, reuse the candidate-profile definition (greatest of last non-archived receipt, last non-archived
  expenditure, last filing received) — `isbe_committee_money.last_receipt_date` is NULL for most older
  committees and must not be used for this.

## Out of scope / later
- Merging money across ids, or changing any per-committee number.
- Automatic acceptance of exact-name groups (a reviewer still clicks; the list sorts them first).
- Officer-name matching across committees that share no candidate (too noisy: common names).
- Performance: the first dry run's per-pair officer `EXISTS` was slow (≈ 2.5 min); the shipped query
  aggregates officer names per committee once in a CTE (not a temp table; 174.5 s → 0.34 s) — still a
  script, not a request path.
- Judgment calls to ratify (journal J-S21, reversible): a family needs at least two members, a lone member is
  never grouped; label tie-break as above; the admin list ships without `q`, sort or the weak-pairs tab.
  J-S18 (above) defers the race-detail rollup.
