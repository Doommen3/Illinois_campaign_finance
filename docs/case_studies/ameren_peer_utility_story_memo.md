# Story Memo: Ameren vs Utility Peers in Illinois Lobbying Data

**As of:** 2026-02-16  
**Data source:** Local PostgreSQL snapshot in `/Users/devin/Illinois_campaign_finance`  
**Reproducibility SQL:** `/Users/devin/Illinois_campaign_finance/docs/case_studies/sql/ameren_peer_utility_benchmark_queries.sql`  
**Benchmark outputs:** `/Users/devin/Illinois_campaign_finance/output/csv/ameren_peer_utility_*.csv` and `/Users/devin/Illinois_campaign_finance/output/csv/ameren_peer_openbook_*.csv`

## What Is Strongly Publishable Now

1. **Ameren Illinois is an outlier on lobbying breadth in this snapshot.**  
   It ranks `#1 of 3,312` clients by both distinct linked lobbying entities (`24`) and entity-client-year rows (`83`).

2. **Ameren Illinois is also high on lobbyist headcount, but not the single highest.**  
   It has `40` distinct registered lobbyists, ranking `#5 statewide`.

3. **Ameren’s overlap profile is mixed, not uniformly dominant.**  
   Ameren Illinois ranks `#2` on matched donor keys (`45`) but `#30` on matched payee edges (`90`), with an overall donor+payee overlap score of `135` (`#18 statewide`).

4. **Within a core utility peer set, Ameren leads some dimensions and trails others.**  
   In the selected 8-peer group, Ameren is `#1` for entity count, pair rows, lobbyist count, and donor matches, but `#4` for payee-edge count and `#4` for combined overlap score.

5. **Peer context matters: ComEd and related labels score higher on payee-edge pathways.**  
   `COMED` has `585` payee edges and overlap score `603` (`#1 within peers`), while `COMMONWEALTH EDISON CO` has overlap score `240` (`#2 within peers`).

## Story-Safe Core Framing

Use language like:

- “appears highly connected in disclosure records”
- “shows a large cross-dataset footprint”
- “name-matched overlap suggests pathways for further scrutiny”
- “does not, by itself, prove causality or improper influence”

Avoid language like:

- “proved influence”
- “controlled policy outcomes”
- “direct pay-to-play evidence” (without independent corroboration)

## Key Benchmark Table (Selected 8 Utility Peers)

| client_name | entities | pair_rows | lobbyists | donor_matches | payee_edges | overlap_score | statewide_overlap_rank | peer_overlap_rank |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| COMED | 16 | 69 | 34 | 18 | 585 | 603 | 3 | 1 |
| COMMONWEALTH EDISON CO | 2 | 8 | 6 | 30 | 210 | 240 | 10 | 2 |
| NICOR GAS | 13 | 39 | 28 | 26 | 179 | 205 | 12 | 3 |
| AMEREN ILLINOIS | 24 | 83 | 40 | 45 | 90 | 135 | 18 | 4 |
| THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS) | 5 | 17 | 17 | 38 | 32 | 70 | 42 | 5 |
| ILLINOIS AMERICAN WATER COMPANY | 7 | 24 | 20 | 5 | 14 | 19 | 171 | 6 |
| CONSTELLATION ENERGY GENERATION, LLC | 15 | 59 | 38 | 12 | 0 | 12 | 253 | 7 |
| AMEREN TRANSMISSION COMPANY OF ILLINOIS | 1 | 1 | 0 | 0 | 0 | 0 | 1253 | 8 |

Source: `/Users/devin/Illinois_campaign_finance/output/csv/ameren_peer_utility_story_table.csv`

## OpenBook Peer Scrape Results (Targeted, Bounded)

Run configuration used:

- `python3 run.py import-openbook-vendor --vendor-name "<NAME>" --max-contract-pages 10 --max-contribution-pages 5 --headless --pick-first`
- Primary peer names run first (8), then focused fallback names for no-match peers (5).
- Scrape-run totals in this local snapshot (`mode='vendor_poc'`): `15` runs, `1126` contract rows inserted, `0` contribution rows, `5` no-match errors.

### Match coverage

- Strict peer-name matches succeeded for 4 of 8 peers:
  - `AMEREN ILLINOIS` -> `AMEREN ILLINOIS`
  - `COMED` -> `COMMONWEALTH EDISON CO-COMED` (`pick_first`)
  - `COMMONWEALTH EDISON CO` -> `COMMONWEALTH EDISON CO`
  - `NICOR GAS` -> `NICOR GAS`
- Fallback-name recovery matched 3 additional peers:
  - `PEOPLES GAS` -> `PEOPLES GAS LIGHT & COKE CO`
  - `ILLINOIS AMERICAN WATER` -> `ILLINOIS AMERICAN WATER CO`
  - `CONSTELLATION ENERGY` -> `CONSTELLATION ENERGY CORP`
- `AMEREN TRANSMISSION COMPANY OF ILLINOIS` remained no-match in this run.

### Peer OpenBook footprint (from matched vendor keys in this run scope)

| openbook_vendor_key | contract_rows | raw_award_total |
|---|---:|---:|
| COMMONWEALTH EDISON CO | 658 | $571,130,190.44 |
| AMEREN ILLINOIS | 1012 | $110,911,744.06 |
| AMERICAN WATER | 143 | $66,773,263.89 |
| ILLINOIS AMERICAN WATER CO | 139 | $66,701,920.72 |
| NICOR GAS | 151 | $53,524,241.79 |
| CONSTELLATION ENERGY CORP | 8 | $46,762,100.00 |
| COMMONWEALTH EDISON CO-COMED | 10 | $1,743,097.00 |
| PEOPLES GAS LIGHT & COKE CO | 17 | $1,638,558.00 |

No warrant rows or contribution rows were added for these peer runs in this snapshot.

### Client-mapped rollups (strict vs fallback)

- Strict-only mapped totals across the 8 peers: `1831` rows, `$737,309,273.29`.
- Strict + fallback mapped totals: `1995` rows, `$852,411,852.01`.
- Strict + fallback + broad fallback totals: `2138` rows, `$919,185,115.90`.

Use strict + fallback as the main reporting view. The broad fallback view (for example `AMERICAN WATER`, `CONSTELLATION`) may over-capture adjacent entities.

## What This Suggests (Without Overclaiming)

- Ameren’s footprint is **abnormally high** for breadth and lobbying scale.
- Ameren’s campaign-finance/payee overlap is **high but not singular** among utility peers.
- The pattern supports a story about **network centrality and multi-domain overlap**, not one about demonstrated causal influence.
- In the targeted OpenBook peer scrape, Ameren remains substantial but is not the largest procurement footprint among peer-mapped vendor keys.

## Leads Worth Chasing Next (Reporting + Analysis)

1. Compare Ameren/ComEd/Nicor by **issue category and bill/docket context** (not just counts).
2. Tie lobbying-year spikes to **specific ICC dockets, major bills, and rate proceedings**.
3. Add **legal-entity normalization** (parent/subsidiary rollup) so ComEd/Commonwealth Edison and Ameren variants are consistently grouped.
4. Build a **person-level bridge test** from lobbyists to donors (name + employer/address disambiguation) with strict false-positive controls.
5. Expand OpenBook scraping to peers (`COMED`, `NICOR`, `PEOPLES GAS`, etc.) so procurement comparisons are apples-to-apples.

## Important Limits to State Explicitly

1. Matching is name-based; it is not final legal-entity identity proof.
2. Directional representation rows are not bilateral relationship proof.
3. Overlap metrics are not causality metrics.
4. OpenBook matching here is seed-name based (`manual` seed source with targeted terms), so legal-entity normalization is still required before definitive vendor attribution.
5. Some peers required fallback terms; broad fallbacks can include non-identical corporate variants.

## Suggested Headline + Dek (Safe Version)

**Headline option:**  
“Ameren’s Lobbying Footprint Ranks Among Illinois’ Largest, New Benchmark Shows”

**Dek option:**  
“In disclosure records, Ameren appears unusually broad by linked lobbying entities and high on cross-dataset overlap, though peer utilities show larger campaign payee-pathway counts in some categories.”
