# Data integrity, batch 1: the donors class (2026-10-29)

To-do 2026-10-28 item 10, batch 1. Its first finding is item 8b (the root cause). This is a read-only analysis of
the local `ilcf` database. Local ISBE data runs through 2026-09-28; prod is about a month fresher. Every query ran
with `PGOPTIONS='-c default_transaction_read_only=on'`. Nothing was fixed in this unit. Plan:
`.claude/plans/data-integrity-batches.md`. Framework documents: `docs/page_inventory.md` and
`docs/data_integrity_checks.md` (written by lane 10-A).

Public-repo rule: the report names committees, organizations and public officials only. A private person is
called "an individual donor". Donor keys are shown only for committees or organizations, and only as `md5(...)`.

## Scope

The donors class covers every page that shows who gave money to whom, keyed on a donor:

| Page / widget | What it shows | What it reads |
|---|---|---|
| `/donors/` | The donor directory: total, count and committees per donor in the window | All time: merged local donor entities (`donor_entity_local`, rows link to `/donors/entity/<id>`; measured in B-M2, see the sweep). Exactly one cycle: `analytics_donor_cycle_summary`, rows link to `/donors/key/<key>`. Any other window: live `bulk_receipts_clean` (`Donor.get_all_with_totals`) |
| `/donors/<id>` | Profile of a legacy local donor (`donors` table id) | `donors` plus the legacy contributions path (`Donor.get_by_id`) |
| `/donors/key/<key>` | "Total Contributed", "This cycle" and "Who They Donated To" for one donor key | **All time (the default):** `analytics_donor_summary` and `analytics_donor_committee_agg`. **Dated window and the "This cycle" subtotal:** live `bulk_receipts_clean` with `receipts_filter_sql` (contributions) (`Donor.get_summary_by_key` / `get_committee_breakdown_by_key`, `database/models.py:1688-1975`) |
| `/donors/entity/<id>` | The same page for a merged local donor entity | `Donor.get_summary_by_entity` / `get_committee_breakdown_by_entity` |
| `/committees/<id>/donors` (and committee Top Donors) | The committee's donors in the window | Live `bulk_receipts_clean` with `receipts_filter_sql(alias)`: part blank or `1%`, **no D-2 check** |
| Candidate / race Donors tab | The entity's donors (1A) and in-kind donors (5A) | `isbe_condensed_receipts`, `archived = FALSE AND d2_part like '1%'`; 5A for in-kind (`database/entity_analytics.py:53`, `:299`) |
| Network tab: donor nodes and the drill-down panel links | 1A donor nodes, 5A in-kind donor nodes. Each links to `/donors/key/<node key>` | `isbe_condensed_receipts`, archived = FALSE, **no D-2 check** (`database/entity_network.py:155-178`). Link: `_node_href` → `donors.donor_detail_by_key` (`webapp/routes/entity_analytics.py:168-177`) |
| Home "Top Donors" widget | The top 8 donors in the window (name, total, count) | Dated window (the default is the current cycle): live `bulk_receipts_clean`, grouped by name and key. All time: `analytics_donor_summary` (`webapp/routes/main.py:768-810`). `/analytics/` has no top-donor table: its networks page has "Top Donors" chart limits over `analytics_donor_committee_agg`, which belongs to the analytics class, not this one |

The key structural fact for 8b is that **the same donor key is counted by three different row rules**:

1. **The analytics donor tables** (`analytics_donor_*`, built by `_refresh_materialized_isbe`, `database/analytics.py:3398`)
   count only itemized monetary contributions. The filter is `_ISBE_DONOR_RECEIPT_FILTER_SQL`
   (`database/analytics.py:607`): `archived = FALSE AND d2_part LIKE '1%' AND EXISTS (a non-archived isbe_d2_reports
   row for the receipt's filed_doc_id)`.
2. **The live donor paths** (`receipts_filter_sql`, `database/models.py:65`) count part blank or `1%`, with no D-2
   check.
3. **The Network tab and the Donors tab** count `isbe_condensed_receipts` rows by part (1A, 5A, 2A), with no D-2
   check.

The `/donors/key` page switches between rules 1 and 2 depending on the time window, and the Network tab links into
it from rule 3.

## 8b root cause

### I1: the Frerichs in-kind node lands on a Democratic Party of Illinois page that never shows Frerichs

**Reproduced locally:** the mechanism yes, the exact figures no. On local data, `/candidates/13505` (Michael W
Frerichs; linked committee `14111` Friends of Frerichs) has a money-flow "In-kind contributions" node with 12
members totalling $358,212.97. Two of the members are "Democratic Party of Illinois", one per address-variant donor
key:

- 3 rows, $149,708.60 (2014). This key's donor page lists 10 other committees, total $789,310.00 all time.
- 5 rows, $56,060.14 (2022). This key's donor page lists 4 other committees (Citizens 4 Pripusich, Massac, Fulton
  and Kankakee County Democratic committees), total $2,794.00 all time.

Neither page lists Friends of Frerichs. Devin's prod observation was one member with 6 rows, ≈ $500k, landing on a
page that lists only Kendall County Democratic Party. That is the same failure on a different (prod-only) key
variant. The exact panel figures are **not reproduced locally**. The prod check is below.

**Mechanism.** The In-kind node is built from D-2 part **5A** rows: in-kind contributions, meaning goods or services
valued as filed (`entity_network.py:168-178`). Its link goes to `/donors/key/<key>` for the same donor key. On that
page's default All-time window, the page reads `analytics_donor_committee_agg`. That table is built only from
part-`1%` rows, so in-kind (5A) rows never reach it, and neither do transfers in (2A). The donor page is therefore
showing a different set of money from the set the reader clicked. For the Democratic Party of Illinois, its 1A
contributions are a small side channel: in-kind support and transfers are how the party funds candidates. The page
lists the few committees that got a 1A check from that exact address spelling, and nothing the reader clicked on.
When a key has **only** in-kind rows, the page returns 404 (`donors.py:153-154`, because the summary row is
missing). A second, smaller cause adds to this: each address spelling of the same organization is its own donor key
(the key embeds the street address). The panel shows several "Democratic Party of Illinois" members, each linking
to a sibling page with a different subset.

**Evidence.** This is the decisive query. It runs as given.

```sql
-- I1 decisive: the In-kind panel's Democratic Party of Illinois members for Friends of Frerichs (14111),
-- one per donor key, against what the all-time /donors/key page lists for that key.
WITH clicked AS (
  SELECT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,''))) AS donor_key,
         COUNT(*) AS inkind_rows, SUM(r.amount) AS inkind_amount
  FROM isbe_condensed_receipts r
  WHERE r.committee_id = 14111 AND r.d2_part = '5A' AND r.archived = FALSE AND r.amount > 0
    AND LOWER(TRIM(r.last_name)) = 'democratic party of illinois'
  GROUP BY 1)
SELECT md5(c.donor_key) AS key_md5, c.inkind_rows, c.inkind_amount,
       COUNT(a.committee_id) AS committees_on_donor_page,
       BOOL_OR(a.committee_id = '14111') AS page_lists_frerichs,
       STRING_AGG(a.committee_name, '; ' ORDER BY a.total_amount DESC) AS page_lists
FROM clicked c
LEFT JOIN analytics_donor_committee_agg a ON a.source = 'bulk_receipts' AND a.donor_key = c.donor_key
GROUP BY 1, 2, 3 ORDER BY 3 DESC;
```

```
 key_md5                          | inkind_rows | inkind_amount | committees_on_donor_page | page_lists_frerichs | page_lists
 ec72bf42319d6c7448307bb61414007d |           3 |      149708.6 |                       10 | f                   | Friends of Mica Carnahan Freeman; Macon County Democratic Central Cmte; Citizens for Pat Dowell; … (10)
 6e87fa762d2b6ac54249290a385473ad |           5 |      56060.14 |                        4 | f                   | Citizens 4 Pripusich; Massac County Democratic Committee; Fulton County Democratic Central Cmte; Kankakee County Democratic Central Cmte
```

The rendered page agrees. With the Flask test client, `GET /donors/key/<each key>` (All time) returned 200 for
both keys: "Total Contributed $789,310.00 / This cycle $400.00, Committees Supported 10" and "$2,794.00 / $225.00,
4". Friends of Frerichs is not in either "Who They Donated To" table.

Where the Democratic Party of Illinois's money sits, by part (all committees, non-archived, organization rows):

```sql
SELECT LEFT(COALESCE(r.d2_part,'(null)'),2) AS part, COUNT(*) AS n, ROUND(SUM(r.amount)::numeric,0) AS total,
       COUNT(DISTINCT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,'')))) AS donor_keys
FROM isbe_condensed_receipts r
WHERE r.archived = FALSE AND r.amount > 0 AND LOWER(TRIM(r.last_name)) = 'democratic party of illinois' AND COALESCE(r.first_name,'')=''
GROUP BY 1 ORDER BY 3 DESC;
```

```
 part |   n   |  total   | donor_keys
 5A   | 15966 | 72109889 |         80
 2A   |   736 | 23905379 |         89
 1A   |    57 |  1515256 |         24
 4A   |    45 |   317635 |         16
 3A   |     1 |   300000 |          1
```

Donor pages can show at most the 1A row: $1.5M of about $98M (≈ 1.5 %), split across up to 24 sibling keys.

**Prod check (for Devin; not run by this lane).** Run this on the server after `ssh`. It is read-only, and it shows
which key Devin clicked and that its page lists only Kendall County Democratic Party:
`PGOPTIONS='-c default_transaction_read_only=on' psql -h localhost ilcf -X -c "WITH clicked AS (SELECT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,''))) AS donor_key, COUNT(*) AS inkind_rows, SUM(r.amount) AS inkind_amount FROM isbe_condensed_receipts r WHERE r.committee_id = 14111 AND r.d2_part = '5A' AND r.archived = FALSE AND r.amount > 0 AND LOWER(TRIM(r.last_name)) = 'democratic party of illinois' GROUP BY 1) SELECT md5(c.donor_key) AS key_md5, c.inkind_rows, c.inkind_amount, COUNT(a.committee_id) AS committees_on_page, BOOL_OR(a.committee_id = '14111') AS lists_frerichs, STRING_AGG(a.committee_name, '; ') AS page_lists FROM clicked c LEFT JOIN analytics_donor_committee_agg a ON a.source = 'bulk_receipts' AND a.donor_key = c.donor_key GROUP BY 1,2,3 ORDER BY 3 DESC;"`
Expected: one row with `inkind_rows = 6` and ≈ $500k, `lists_frerichs = f` and `page_lists = Kendall County
Democratic Party…`. A different result reopens I1.

**Hypotheses for I1.**
- **H1 CONFIRMED.** The all-time donor page reads `analytics_donor_committee_agg`, which applies `d2_part LIKE '1%'`.
  The clicked 5A rows ($205,768.74 over two keys) are absent from both pages (decisive query: `page_lists_frerichs = f`
  on both keys).
- **H2 REFUTED for I1.** No Frerichs committee id appears under either key under any id. The agg's `committee_id` is
  `isbe_committees.id`, the same id space the network uses (`analytics.py:3442-3443`).
- **H3 CONFIRMED as a contributing cause.** The panel shows two "Democratic Party of Illinois" members, each with its
  own key and its own page. Across the site, the party's name carries 80 keys with 5A rows and 24 keys with 1A rows.
  Fixing H3 alone would not show Frerichs, because H1 still excludes 5A.
- **H4 REFUTED.** The agg was built at `2026-10-26 23:51` and the rows are from 2014 and 2022.

**Severity.** High for a reader. They click "$149,708.60 in-kind to Friends of Frerichs" and land on a page whose
headline total ($789,310) and committee list are about different money. Nothing on the page says in-kind is
excluded. For an in-kind-only donor the link is a 404.

### I2: `/committees/6239/donors` shows JB For Governor's $1.55M, but JB For Governor's donor page does not

**Reproduced locally: yes, exactly.** The `/donors/key/<JB For Governor key>` page (All time) shows "Total
Contributed $2,025,250.00 / This cycle $1,550,000.00, Committees Supported 9". The 9 listed committees do not
include the Democratic Party of Illinois. This was rendered with the Flask test client.

**Mechanism.** The receipt (id 6666776: $1,550,000, part 1A, 2026-09-25, to committee 6239) was filed on an
**A-1** (the 48-hour large-contribution report), not on a D-2 quarterly. Two of the page's figures read it
differently:

- The committee donors page and the donor page's "This cycle" subtotal are live (`receipts_filter_sql`, no D-2
  check), so they include the receipt.
- The donor page's All-time total and its "Who They Donated To" list read the analytics tables. Their filter adds
  `EXISTS (non-archived isbe_d2_reports row for the filed_doc_id)`, and an A-1 has no D-2 row, so the receipt is
  dropped.

This is why the page contradicts itself: "This cycle" ($1.55M) is close to "All time" ($2.03M), yet no listed
committee received the $1.55M. The gap is exactly $3,575,250 − $2,025,250 = $1,550,000. `isbe_condensed_receipts`
keeps an A-1 row only until the committee's next quarterly covers its date (`scripts/isbe_sunshine_etl.py:496-511`,
first branch). The defect is therefore **transient per receipt**: it lasts from the A-1 until the next D-2 is filed
and loaded. It is **permanent as a class**, because every large late contribution passes through this window, and the
window is the weeks before an election. Prod will probably have self-healed this particular row once the Q3 2026
quarterly (period ending 2026-09-30) was loaded. That has **not been verified**.

**Evidence.** This is the decisive query. It runs as given.

```sql
-- I2 decisive: the three paths for the donor key of receipt 6666776 (JB For Governor -> committee 6239).
WITH k AS (SELECT LOWER(TRIM(COALESCE(first_name,'')||'|'||COALESCE(last_name,'')||'|'||COALESCE(address1,'')||'|'||COALESCE(address2,'')||'|'||COALESCE(city,'')||'|'||COALESCE(state,'')||'|'||COALESCE(zipcode,''))) AS donor_key FROM isbe_condensed_receipts WHERE id = 6666776)
SELECT 'agg (all-time page list)' AS path, COUNT(*) AS committees, SUM(a.total_amount) AS total, BOOL_OR(a.committee_id='6239') AS lists_dpi, MAX(a.updated_at) AS built
FROM analytics_donor_committee_agg a, k WHERE a.source='bulk_receipts' AND a.donor_key=k.donor_key
UNION ALL
SELECT 'summary (all-time total)', MAX(s.committee_count), MAX(s.total_amount), NULL, MAX(s.updated_at) FROM analytics_donor_summary s, k WHERE s.source='bulk_receipts' AND s.donor_key=k.donor_key
UNION ALL
SELECT 'live bulk_receipts_clean, contributions predicate', COUNT(DISTINCT r.committee_id_sbe), SUM(r.amount), BOOL_OR(r.committee_id_sbe=6239), NULL
FROM bulk_receipts_clean r, k
WHERE COALESCE(r.amount,0)>0 AND NOT COALESCE(r.is_archived::boolean,FALSE) AND (COALESCE(r.d2_part_code,'')='' OR r.d2_part_code LIKE '1%')
  AND LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_or_business_name,'')||'|'||COALESCE(r.address_line_1,'')||'|'||COALESCE(r.address_line_2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.postal_code,'')))=k.donor_key;
```

```
 path                                              | committees |  total  | lists_dpi | built
 agg (all-time page list)                          |          9 | 2025250 | f         | 2026-10-26 23:51:22.291202
 summary (all-time total)                          |          9 | 2025250 |           | 2026-10-26 23:51:22.291202
 live bulk_receipts_clean, contributions predicate |         10 | 3575250 | t         |
```

The receipt itself, and why the analytics filter drops it:

```sql
SELECT r.id, r.filed_doc_id, r.d2_part, r.archived, r.received_date, r.amount, r.last_name,
       EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = r.filed_doc_id AND d.archived = FALSE) AS has_live_d2,
       (SELECT f.doc_name FROM isbe_filed_docs f WHERE f.id = r.filed_doc_id) AS doc
FROM isbe_receipts r WHERE r.id = 6666776;
```

```
   id    | filed_doc_id | d2_part | archived | received_date | amount  |    last_name    | has_live_d2 | doc
 6666776 |      1020820 | 1A      | f        | 2026-09-25    | 1550000 | JB For Governor | f           | A-1
```

**Hypotheses for I2.**
- **H1 CONFIRMED, in a variant.** The cause is not the part filter, since the row is 1A. It is the analytics filter's
  extra `EXISTS` on a D-2 report, which the live predicate does not have. `receipts_filter_sql`'s docstring
  documents this exception (`models.py:80-82`). The page mixes the two rules on one screen.
- **H2 REFUTED.** Committee 6239 is absent from the agg under any id. The $1.55M is in no agg row (the totals
  reconcile exactly).
- **H3 REFUTED for I2.** The live path and the agg use the same key (the live sum over the same key includes the
  row).
- **H4 REFUTED.** The agg was built 2026-10-26, a month after the 2026-09-25 receipt. The row is excluded by the
  filter, not by staleness.

**Severity.** High during election season. The largest late contributions (A-1s are, by definition, the large ones
in the final weeks before an election) are missing from donor pages' all-time totals and lists, while the same
page's cycle subtotal includes them.

### Is it system-wide?

**Yes. Both mechanisms are site-wide.** These counts are measured on local data (ISBE through 2026-09-28). Prod is
about a month fresher, so its A-1 count will differ.

**Network-tab donor links vs the donor page they open.** Over all (donor key, committee) pairs that a Network-tab
donor node can link from (`isbe_condensed_receipts`, archived = FALSE, amount > 0, parts 1A / 5A, redacted rows
excluded), the query checks whether the all-time `/donors/key` page for that key lists that committee
(`b1_systemwide.sql` below):

```
 d2_part |  pairs  |  dollars   | pairs_missing | dollars_missing | pairs_page_404 | pct_pairs_missing
 1A      | 1312542 | 3578789903 |          6003 |        17828156 |           4702 |              0.46
 5A      |   50152 |  378168879 |         35237 |       337863400 |          29660 |             70.26
```

- **In-kind (5A):** 70.3 % of in-kind links (35,237 of 50,152 pairs) land on a donor page that does not list the
  committee they were clicked from. That is $337.9M of $378.2M of in-kind dollars. 29,660 of those links (59 % of
  all in-kind pairs) land on a **404**, because the key has no 1A rows at all. The 30 % that do resolve are keys that
  also gave a D-2 1A contribution to the same committee.
- **1A:** 0.46 % (6,003 pairs, $17.8M), of which 4,702 are 404s. This is the I2 mechanism (1A on a filing with no
  live D-2).

**Sample: the 25 candidates with the most receipt rows** (37 linked committees, all time, same rule):

```
 d2_part | candidates | committees | links | links_missing_committee |  pct  | dollars_missing
 1A      |         25 |         37 | 87496 |                      15 |  0.02 |           33500
 5A      |         25 |         37 |  2610 |                    1807 | 69.23 |        15800062
```

This counts link pairs, not rendered nodes. The ego graph keeps only the top donors (`DEFAULT_CAPS`), so a rendered
page shows a subset of these pairs. B-M2 samples rendered pages.

**1A rows that the analytics donor tables drop (the I2 mechanism)**, by whether the row's filed doc has a live D-2:

```
 on_live_d2 | rows_1a |  dollars   | rows_since_2025 | dollars_since_2025
 f          |   15883 |  105201649 |            6323 |           99005093
 t          | 2822103 | 3484411935 |          142736 |          198697983
```

Since 2025-01-01, **$99.0M of $297.7M (33 %) of current-cycle itemized contribution dollars** in condensed receipts
sit on filings with no live D-2 (mostly A-1s). They are missing from every all-time donor page and from
`analytics_donor_*`, while the live pages (committee donors, cycle subtotals) count them. `/donors/` all-time ranks
and totals read the same tables, so they understate the same donors. B-M2 measures this per page.

### Proposed fixes (not implemented; route by footprint, COORDINATE with terminal 1)

Terminal 1 owns `database/analytics.py` (search Phase 2 M5 touches it). Every option below is a separate fix unit.

| # | Fix | Footprint | Trade-off |
|---|---|---|---|
| F1 | **Align the analytics donor filter with the live predicate.** Drop the `EXISTS` on `isbe_d2_reports` from `_ISBE_DONOR_RECEIPT_FILTER_SQL` / `_BULK_DONOR_RECEIPT_FILTER_SQL` and match `receipts_filter_sql` (contributions). This fixes I2 and the $99M gap | `database/analytics.py` + `refresh-analytics` on prod | Needs a check first that condensed receipts never hold an A-1 row *and* its D-2 re-listing at the same time (the first branch of `isbe_condensed_receipts` suggests they don't). Without that check, totals could double. Changes `/donors/` all-time ranks |
| F2 | **Make the network's in-kind link land where in-kind is shown.** Option (a): drop the href for 5A-only nodes, or point it to the entity's Donors tab, which already lists in-kind donors (`entity_analytics.py:299`). Option (b): give the donor page an in-kind section (5A by key), so the link is truthful | (a) `webapp/routes/entity_analytics.py::_node_href` plus the panel template. (b) `webapp/routes/donors.py`, `database/models.py`, `database/analytics.py` (a new 5A agg or a live query), `donors/detail_by_key.html` | (a) is small and honest, but removes a link. (b) answers Devin's question ("who they donated to") but is a new feature with a new aggregate |
| F3 | **Show committee donors' transfers.** For an organization key that matches a committee by name (as the network's 2A resolution does, `entity_network.py:180-195`), link to `/committees/<id>`, which shows its expenditures and transfers out, instead of a donor-key page | `database/entity_network.py` (node id rule) + `_node_href` | Name matching is approximate. Only unique exact-name matches resolve (the same rule the network already uses) |
| F4 | **Label what the page counts.** Say on the donor page "Itemized contributions (D-2 Part 1A). In-kind and transfers not included", and make "This cycle" and "All time" use one rule | `donors/detail_by_key.html` (+ F1 for the rule) | A wording-only change. Needed whatever else is chosen |
| F5 | **Merge address-variant keys (H3)** | Donor identity, site-wide | Large and out of scope here. It exists as committee families / entity merges. Listed for completeness, not recommended for 8b |
| F6 | *(added in B-M2, finding E)* **Refresh the merged donor entities with the analytics tables.** Run `rebuild-local-donor-entities` in the post-import chain, either after `refresh-analytics` or called from it, and document it in the refresh schedule. Alternatively, have the all-time `/donors/` list compute entity totals from `analytics_donor_summary` at read time | `database/analytics.py` or `cli/commands.py` (chain), README / CLAUDE.md refresh schedule, prod run | Adds rebuild time to every refresh (not measured here). The read-time option costs a join on every all-time directory page. Prod freshness is unknown: run the DI-B1-03 date query first |

Recommended minimum: **F1 + F2(a) + F4**, plus **F6** if prod shows the same staleness. F2(b) and F3 are product
decisions for Devin.

### How to find issues like this

1. **Link-target consistency.** Every link from an aggregate (a node, a panel row, a "top N" row) must land on a page
   that contains the aggregated rows, under the same window and the same row rule. Test it mechanically: for each
   link source, compute (key, target, amount) from the source's own row rule, then check that the target page's data
   path holds that (key, committee) pair with an amount ≥ the clicked amount. `b1_systemwide.sql` is that test for
   donor links.
2. **One row rule per figure, stated once.** List every predicate that defines "a contribution"
   (`receipts_filter_sql`, `_ISBE_DONOR_RECEIPT_FILTER_SQL`, `_RECEIPT_BASE_SQL`, the network's part filters). Where
   two figures on one page come from different rules (All time from the agg, "This cycle" from live), check that
   they reconcile: All time ≥ This cycle, and the listed committees sum to the total.
3. **Recompute from source, not from the page's query.** Check against `isbe_receipts` / `isbe_condensed_receipts`
   directly, stratified by part (1A / 2A / 5A), by filing type (D-2 vs A-1) and by recency (the current quarter is
   where A-1s live).
4. **Sample by strata, not at random.** Big organization donors (party committees, unions), in-kind-heavy
   recipients, recent large contributions, and address-variant names are where these classes of defect concentrate.
   A uniform sample of individual donors would have missed both I1 and I2.

## Sample sweep

Run 2026-10-29 on local `ilcf` (ISBE through 2026-09-28) with the Flask test client and no server. The script is
`b1_sweep.py` in the session scratchpad (see the appendix). Every page figure is compared with an **independent
recomputation from `isbe_condensed_receipts`**, written out in SQL. The "live" rule is the contributions predicate:
`archived = FALSE AND amount > 0 AND (d2_part blank OR LIKE '1%')`. No figure was recomputed with the page's own
query. **Tolerance:** $0.01 on dollars, exact on counts. Redacted rows are excluded by the network's link rule. No
sampled row was a redacted donor.

Each mismatch is classified by mechanism:
- **I1 (in-kind exclusion):** 5A rows are never on a donor page.
- **I2 (no live D-2):** 1A rows filed on an A-1 or another filing with no live D-2 report are dropped by the
  analytics donor tables. The check: (live − page) equals the dropped rows exactly.
- **E (stale entity table):** **new in B-M2**, see (a).
- **key variant.**
- **other.**

### (a) `/donors/` list rows vs their donor pages vs recomputation (n = 30)

Strata: page 1 of `/donors/?period=all` (top 10), page 1 of `/donors/` (2026 cycle, top 10), page 100 of all time
(5 rows, about $25.9k each) and page 2000 of all time (5 rows, $1,000 each). Figures: total, contribution count and
committee count.

**New finding: the all-time directory is not keyed on donor keys.** It lists **merged local donor entities** from
`donor_entity_local`, and its rows link to `/donors/entity/<id>`. That table is filled only by
`rebuild-local-donor-entities` (`cli/commands.py:1145`, `database/local_donor_entities.py:567`). The documented
refresh chain does not run it: neither `refresh-analytics` nor `sunshine-import` calls it, and it is not in
CLAUDE.md or the runbooks. Locally it was last built **2026-02-13**, eight months before the analytics tables
(2026-10-26).

| Stratum | n | List = its donor page | Page = recomputation | Failures by mechanism |
|---|---|---|---|---|
| Top 10 all time (entity rows) | 10 | 10 / 10 | 5 / 10 | 1 × I2 only (the top row: $432,393,700 shown vs $497,393,700 live; the $65,000,000 gap is one no-D-2 row). 4 × E: stored entity total ≠ today's analytics total (e.g. $54,363,602.51 shown vs $50,547,302.51 now, page *higher* than the data). In 3 of these 4, I2 also adds to the gap |
| Top 10, 2026 cycle (key rows) | 10 | 10 / 10 | 10 / 10 | none. The cycle path is live (or matches live): these rows *include* the A-1 rows that the all-time pages drop (6 of 10 carry them, e.g. $65M, $2.5M, $1.55M) |
| Middle, all time, page 100 | 5 | 5 / 5 | 3 / 5 | 1 × I2 ($1,000). 1 × E, inferred: the page is $250 / 1 row *above* live with no dropped rows, the same signature as the 4 verified E rows |
| Small, all time, page 2000 | 5 | 5 / 5 | 5 / 5 | none |

**E system-wide:** 22,609 of 246,900 entities (9.2 %) have a stored total that differs from the sum of their
members' current `analytics_donor_summary` totals. The absolute gap is $33.3M. 8,626 entities have a member key that
is no longer in the analytics tables. The query is in the appendix.

**Prod check (for Devin; read-only):**
`PGOPTIONS='-c default_transaction_read_only=on' psql -h localhost ilcf -X -c "SELECT MIN(updated_at), MAX(updated_at) FROM donor_entity_local WHERE source = 'bulk_receipts';"`

### (b) `/committees/<id>/donors` (n = 10)

Strata:
- **Largest by receipt rows (5):** IUOE Local 399 Political Education Fund (1157), REALTORS Political Action
  Committee (1126), Illinois Federation of Teachers COPE (185), Citizens for Lisa Madigan (13471) and Commonwealth
  Edison Company PAC (19752).
- **5 drawn reproducibly with ≥ 50 donors** (`ORDER BY md5(committee_id || 'b1')`): 15781, 31993, 18176, 24058 and
  32061.

The check compares the subtitle "N unique donors, $T | This cycle: $C" with the recomputation under the live rule,
and asks whether the first row's donor page lists the committee.

- **Subtitle:** 10 / 10 pass (count, total and cycle exact).
- **First row's `/donors/key` page lists the committee:** 10 / 10.

I2 is still possible here when a committee's top donor gave only on A-1s. That is the I2 instance itself:
committee 6239's first row, which this random draw did not hit.

### (c) Candidate Donors tab (n = 5)

Candidates: 13505 (Frerichs), plus the four candidates with the most receipt rows other than 13505 (16988, 12953,
16872 and 12831). Figures: the "Concentration by group" itemized **Total** and the **In-kind** total (the
"All committees" row, or the single group). Recomputation: `isbe_condensed_receipts` over the candidate's
`isbe_candidate_committees`, `d2_part LIKE '1%'` and `= '5A'`.

- **5 / 5 pass, exact.** For example, Frerichs shows itemized $1,853,270.12 and in-kind $451,676.23; the
  recomputation gives the same.
- The tab already shows in-kind separately and correctly. This is where F2(a) should point the in-kind node.

### (d) Network-tab donor links, rendered (25 candidates, 1,075 links)

The 25 candidate ids with the most receipt rows resolve to 19 distinct committee sets. For each one, the sweep
fetched `network.json?view=money_flow`, took every member of `class:individual`, `class:organization` and
`class:in_kind`, built its href with the app's own `_node_href`, and loaded the page.

| Member class | Links | 200, page lists the clicked committee | 200, committee missing | 404 |
|---|---|---|---|---|
| Individual donors (1A) | 385 | 385 | 0 | 0 |
| Organization donors (1A) | 532 | 532 | 0 | 0 |
| In-kind (5A) | 158 | 75 | 22 | 61 |

All 83 in-kind failures are **I1**. The rendered in-kind failure rate (52.5 %) is below the SQL rate over all pairs
(69–70 %, see 8b). The rendered graph keeps only the top donors, and large in-kind donors are more likely to have
also given 1A to the same committee.

### (e) Home "Top Donors" widget (n = 10)

The top 5 rows of the default window (2026 cycle) and the top 5 rows of `/?period=all`, each compared with the live
recomputation for its key.

- **Cycle:** 5 / 5 pass.
- **All time:** 3 / 5 pass. Both failures are **I2**: $432,050,000 shown vs $497,050,000 live (a $65,000,000
  no-D-2 row), and $66,094,642.00 vs $68,085,396.92 ($1,990,754.92 in two no-D-2 rows). The same donor shows
  **$90.5M "this cycle" and $432M "all time"** on two views of one page, yet the all-time figure omits $65M of the
  cycle's rows.

### Sample verdicts (rule of three: 0 failures in n means the 95 % upper bound is < 3/n)

| Stratum | n | Failures | Verdict |
|---|---|---|---|
| (a) `/donors/` 2026-cycle rows | 10 | 0 | < 30 % (weak: small n) |
| (a) `/donors/` all-time rows (entity) | 20 | 7 (5 E, one of them inferred; 2 I2 only; 3 of the E rows also carry I2) | **35 % measured.** E is system-wide at 9.2 % of entities, concentrated at the top |
| (a) list row = its own donor page | 30 | 0 | < 10 % |
| (b) committee donors subtitle | 10 | 0 | < 30 % |
| (b) first donor's page lists the committee | 10 | 0 | < 30 %. I2 is a known exception (committee 6239) |
| (c) Donors tab itemized + in-kind | 5 | 0 | < 60 % (weak: confirm with the manual check) |
| (d) 1A network links | 917 | 0 | **< 0.33 %** |
| (d) In-kind network links | 158 | 83 | **52.5 % measured (I1)** |
| (e) Home Top Donors, cycle | 5 | 0 | < 60 % |
| (e) Home Top Donors, all time | 5 | 2 | **40 % measured (I2)** |

**What the sample says about the class.**
- The live paths are clean: the committee donors pages, cycle windows and the Donors tab all recompute exactly.
- Every failure is on an **all-time path that reads a precomputed table**:
  - `analytics_donor_*` (I2 at the A-1 boundary);
  - `donor_entity_local` (E, never refreshed);
  - or a link from a part the target page does not count (I1).
- No key-variant failure was observed in the sample. H3 shows up as several sibling nodes, not as a wrong figure.

## Manual checks for this class

The ids are provisional. Fable renumbers them when folding them into `docs/data_integrity_checks.md`. Run every
query in `psql ilcf` (prod: `psql -h localhost ilcf`) after `export PGOPTIONS='-c default_transaction_read_only=on'`.
`<key>` is the text after `/donors/key/` in the page URL, URL-decoded.

### DI-B1-01 — Committee donors subtitle

- **Class:** donors (committee donors list)
- **Created:** 2026-10-29 (batch 1)
- **Status:** passing (10 / 10, batch 1)
- **Figure:** `/committees/<id>/donors` subtitle "N unique donors, $T | This cycle: $C" (All time).
- **Independent recomputation:**
  ```sql
  SELECT COUNT(DISTINCT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,'')))) AS unique_donors,
         ROUND(SUM(r.amount)::numeric, 2) AS total,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.received_date BETWEEN '2025-01-01' AND '2026-12-31'), 0)::numeric, 2) AS this_cycle
  FROM isbe_condensed_receipts r
  WHERE r.committee_id = <id> AND r.archived = FALSE AND r.amount > 0 AND (COALESCE(r.d2_part,'') = '' OR r.d2_part LIKE '1%');
  ```
- **Tolerance:** counts exact; dollars ± $0.01.
- **Sample rule:** 3 committees per run: 1 of the 5 largest by receipt rows, 1 party committee (e.g. 6239), and
  1 random committee with ≥ 50 donors.
- **Steps:** open the page; run the query with the id; compare the three numbers.
- **Pass:** all three match.
- **Fail:** any differs. Record the committee id and the three pairs of numbers.

### DI-B1-02 — Donor page All time agrees with its own This cycle (the I2 check)

- **Class:** donors (`/donors/key`)
- **Created:** 2026-10-29
- **Status:** failing (I2: e.g. JB For Governor's key, receipt 6666776). Fix F1 proposed
- **Figure:** "Total Contributed (All time)", "This cycle" and the "Who They Donated To" list on `/donors/key/<key>`.
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
- **Sample rule:** 5 keys per run, taken from the first rows of `/donors/` (2026 cycle) and from `/committees/6239/donors`.
- **Steps:** open the donor page (All time) and note the total, This cycle and the committee list; run the query;
  compare.
- **Pass:** All time = `live_all_time`; This cycle = `live_cycle`; the listed committees sum to All time.
- **Fail:** All time = `live_all_time − on_no_d2_filings` is I2. Any other gap is a new mechanism: record it.

### DI-B1-03 — The all-time donor directory is fresh (the E check)

- **Class:** donors (`/donors/?period=all`, `/donors/entity/<id>`)
- **Created:** 2026-10-29
- **Status:** failing locally (`donor_entity_local` built 2026-02-13). Prod unknown
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

### DI-B1-04 — Network in-kind links land on a page showing the in-kind money (the I1 check)

- **Class:** donors (Network tab donor links)
- **Created:** 2026-10-29
- **Status:** failing (52.5 % of rendered in-kind links, I1). Fix F2 proposed
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

### DI-B1-05 — Network 1A donor links land on a page listing the committee

- **Class:** donors (Network tab donor links)
- **Created:** 2026-10-29
- **Status:** passing (917 / 917)
- **Figure:** members of the "Individual donors" and "Organization donors" panels, and their links.
- **Independent recomputation:** the DI-B1-04 query with `r.d2_part = '1A'`. Then, on the landing page, the
  committee row's amount must be ≥ the panel amount.
- **Tolerance:** ± $0.01.
- **Sample rule:** the same 2 candidates as DI-B1-04, 3 members from each panel.
- **Steps:** as DI-B1-04.
- **Pass:** every link opens a 200 page that lists the committee.
- **Fail:** a 404 or a missing committee. Check the row's filing with the DI-B1-02 query (I2 if it is on a no-D-2
  filing).

### DI-B1-06 — Candidate Donors tab itemized and in-kind totals

- **Class:** donors (candidate / race Donors tab)
- **Created:** 2026-10-29
- **Status:** passing (5 / 5)
- **Figure:** `/candidates/<id>/donors`, "Concentration by group": **Total** and **In-kind** on the "All
  committees" row (or the single group row).
- **Independent recomputation:**
  ```sql
  SELECT ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.d2_part LIKE '1%'),0)::numeric,2) AS itemized,
         ROUND(COALESCE(SUM(r.amount) FILTER (WHERE r.d2_part = '5A'),0)::numeric,2) AS in_kind
  FROM isbe_condensed_receipts r
  WHERE r.archived = FALSE AND r.amount > 0
    AND r.committee_id IN (SELECT committee_id FROM isbe_candidate_committees WHERE candidate_id = <candidate_id>);
  ```
- **Tolerance:** ± $0.01.
- **Sample rule:** 2 candidates per run: 13505 and one random candidate with a committee.
- **Steps:** open the tab (All time); run the query; compare.
- **Pass:** both match.
- **Fail:** either differs. Note that an accepted committee family adds committees that are not in
  `isbe_candidate_committees`, so check `/admin/committee-families` before recording a failure.

### DI-B1-07 — Home Top Donors

- **Class:** donors (home widget)
- **Created:** 2026-10-29
- **Status:** cycle passing (5 / 5); all time failing (2 / 5, I2)
- **Figure:** the "Top Donors" total and count on `/` (default 2026 cycle) and on `/?period=all`.
- **Independent recomputation:** the DI-B1-02 query for each row's `<key>`. Compare with `live_cycle` for the
  default view and `live_all_time` for `period=all`. The count is `COUNT(*)` under the same WHERE clause.
- **Tolerance:** ± $0.01; counts exact.
- **Sample rule:** the top 3 rows of each view, every run.
- **Steps:** open both views; click each of the top 3 names to get `<key>` from the URL; run the query; compare.
- **Pass:** all match.
- **Fail:** an all-time gap equal to `on_no_d2_filings` is I2. Record any other gap as new.

## Appendix: scratch queries

`b1_systemwide.sql` (system-wide link check; read-only; about 50 s locally):

```sql
WITH pairs AS (
    SELECT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||
           COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,''))) AS donor_key,
           r.committee_id, r.d2_part, COUNT(*) AS n, SUM(r.amount) AS amount
    FROM isbe_condensed_receipts r
    WHERE r.archived = FALSE AND r.amount > 0 AND r.d2_part IN ('1A','5A')
      AND NOT COALESCE(r.redaction_requested, FALSE)
    GROUP BY 1, 2, 3
),
agg_keys AS (SELECT DISTINCT donor_key FROM analytics_donor_committee_agg WHERE source = 'bulk_receipts')
SELECT p.d2_part, COUNT(*) AS pairs, ROUND(SUM(p.amount)::numeric, 0) AS dollars,
       COUNT(*) FILTER (WHERE a.donor_key IS NULL) AS pairs_missing,
       ROUND(SUM(p.amount) FILTER (WHERE a.donor_key IS NULL)::numeric, 0) AS dollars_missing,
       COUNT(*) FILTER (WHERE k.donor_key IS NULL) AS pairs_page_404,
       ROUND(100.0 * COUNT(*) FILTER (WHERE a.donor_key IS NULL) / COUNT(*), 2) AS pct_pairs_missing
FROM pairs p
LEFT JOIN analytics_donor_committee_agg a
       ON a.source = 'bulk_receipts' AND a.donor_key = p.donor_key AND a.committee_id = CAST(p.committee_id AS TEXT)
LEFT JOIN agg_keys k ON k.donor_key = p.donor_key
GROUP BY p.d2_part ORDER BY 1;
```

1A rows by whether their filing has a live D-2 report (the I2 mechanism):

```sql
SELECT EXISTS (SELECT 1 FROM isbe_d2_reports d WHERE d.filed_doc_id = r.filed_doc_id AND d.archived = FALSE) AS on_live_d2,
       COUNT(*) AS rows_1a, ROUND(SUM(r.amount)::numeric, 0) AS dollars,
       COUNT(*) FILTER (WHERE r.received_date >= '2025-01-01') AS rows_since_2025,
       ROUND(SUM(r.amount) FILTER (WHERE r.received_date >= '2025-01-01')::numeric, 0) AS dollars_since_2025
FROM isbe_condensed_receipts r
WHERE r.archived = FALSE AND r.amount > 0 AND r.d2_part = '1A'
GROUP BY 1 ORDER BY 1;
```

Network link pairs for the 25 candidates with the most receipt rows (SQL complement of sweep (d)):

```sql
WITH top25 AS (
    SELECT cc.candidate_id, COUNT(*) AS n
    FROM isbe_candidate_committees cc JOIN isbe_condensed_receipts r ON r.committee_id = cc.committee_id
    WHERE r.archived = FALSE AND r.amount > 0
    GROUP BY cc.candidate_id ORDER BY n DESC LIMIT 25
),
cmtes AS (SELECT DISTINCT cc.committee_id FROM isbe_candidate_committees cc JOIN top25 USING (candidate_id)),
pairs AS (
    SELECT LOWER(TRIM(COALESCE(r.first_name,'')||'|'||COALESCE(r.last_name,'')||'|'||COALESCE(r.address1,'')||'|'||
           COALESCE(r.address2,'')||'|'||COALESCE(r.city,'')||'|'||COALESCE(r.state,'')||'|'||COALESCE(r.zipcode,''))) AS donor_key,
           r.committee_id, r.d2_part, SUM(r.amount) AS amount
    FROM isbe_condensed_receipts r JOIN cmtes USING (committee_id)
    WHERE r.archived = FALSE AND r.amount > 0 AND r.d2_part IN ('1A','5A') AND NOT COALESCE(r.redaction_requested, FALSE)
    GROUP BY 1, 2, 3
)
SELECT p.d2_part, (SELECT COUNT(*) FROM top25) AS candidates, (SELECT COUNT(*) FROM cmtes) AS committees,
       COUNT(*) AS links, COUNT(*) FILTER (WHERE a.donor_key IS NULL) AS links_missing_committee,
       ROUND(100.0 * COUNT(*) FILTER (WHERE a.donor_key IS NULL) / COUNT(*), 2) AS pct,
       ROUND(SUM(p.amount) FILTER (WHERE a.donor_key IS NULL)::numeric, 0) AS dollars_missing
FROM pairs p
LEFT JOIN analytics_donor_committee_agg a
       ON a.source = 'bulk_receipts' AND a.donor_key = p.donor_key AND a.committee_id = CAST(p.committee_id AS TEXT)
GROUP BY p.d2_part ORDER BY 1;
```

Entity-table staleness (E), system-wide:

```sql
WITH m AS (
  SELECT e.entity_id, e.total_amount AS entity_total, SUM(s.total_amount) AS agg_total, COUNT(*) AS members, COUNT(s.donor_key) AS members_in_agg
  FROM donor_entity_local e
  JOIN donor_entity_local_member mm ON mm.source = e.source AND mm.entity_id = e.entity_id
  LEFT JOIN analytics_donor_summary s ON s.source = mm.source AND s.donor_key = mm.donor_key
  WHERE e.source = 'bulk_receipts' GROUP BY 1, 2)
SELECT COUNT(*) AS entities, COUNT(*) FILTER (WHERE ABS(entity_total - COALESCE(agg_total,0)) > 0.01) AS entity_total_ne_agg,
       COUNT(*) FILTER (WHERE members_in_agg < members) AS entities_with_member_missing_from_agg,
       ROUND(SUM(ABS(entity_total - COALESCE(agg_total,0)))::numeric,0) AS abs_dollar_gap
FROM m;
-- local 2026-10-29: 246900 | 22609 | 8626 | 33301531
```

The rendered-page sweep is
`/private/tmp/claude-501/-Users-devin-Illinois-campaign-finance/1e47079a-70a0-41ff-a7e0-0bf1e63bc362/scratchpad/b1_sweep.py`
(argument: any of `abcde`). It runs on the Flask test client and needs `PGOPTIONS` read-only plus `DATABASE_URL`.
Its output only ever prints 10-character md5 prefixes of donor keys. It is a scratch tool, not a repo script.
Promoting it to `scripts/audit/` is a decision for Fable.
