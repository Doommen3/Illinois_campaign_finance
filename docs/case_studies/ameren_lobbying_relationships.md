# Ameren Lobbying Relationship Footprint in Illinois

**As of:** 2026-02-16  
**Scope note:** This version reflects the current local PostgreSQL snapshot after loading the daily IL SOS lobbying extract that includes lobbyist-level registrations. All figures below are from reproducible SQL in this repository.

## Executive Summary

- `AMEREN ILLINOIS` (`client_id=3821`) is linked to `24` distinct lobbying entities and `83` entity-client-year rows in the current snapshot.
- `AMEREN TRANSMISSION COMPANY OF ILLINOIS` (`client_id=6710`) appears with `1` linked entity and `1` entity-client-year row.
- With lobbyist-level tables now loaded, Ameren headcounts are computable: `AMEREN ILLINOIS` has `40` distinct registered lobbyists; the transmission client has `0` in this snapshot.
- Ameren Illinois lobbyist counts by registration year are: `2022: 26`, `2023: 21`, `2024: 22`, `2025: 28`, `2026: 23`.
- Relationship directionality remains asymmetric statewide: `directed_pair_count = 5345`; `has_reverse_pair_count = 111` (`2.08%`).
- Cross-domain overlap remains visible: `45` matched donor keys and `90` matched payee edges for `AMEREN ILLINOIS`; `0` matched 527 organizations.
- Donor-name variants matching `%ameren%` in `analytics_donor_summary` remain `264` keys totaling `$8,057,465.60`.
- Committee spending tied to Ameren-matched payee pathways is now computable (with caveats): `198,776` expenditure rows and `$442,594,201.57` across `1999-2026` for `87` matched committees.

## Key Findings

**Observed in current snapshot:** Ameren Illinois remains one of the most connected clients in the entity-client registry, with `24` linked entities. The `83` pair rows indicate repeated registrations across years, not 83 unique organizations.

**Observed in current snapshot:** Lobbyist-level records are now present (`lobbying_lobbyists`, `lobbying_lobbyist_registrations`), enabling direct headcount analysis instead of entity-only proxies.

**Observed in current snapshot:** Ameren Illinois has `40` unique lobbyists across `2022-2026`. The annual count peaks at `28` in `2025` and is `23` in `2026` so far.

**Observed in current snapshot:** The network remains directional by design. Only `2.08%` of directed entity-client pairs have a reverse counterpart, so reverse links should be treated as separate observations.

**Inference (bounded):** Ameren-related names span multiple data domains (lobbying clients, donor matches, expenditure-payee matches). This supports overlap analysis, not causal claims about policy outcomes.

## How Relationships Are Modeled

The core row in `lobbying_entity_clients` is directional: an entity is recorded as representing a client in a given year. It is not a symmetric "organization-to-organization" tie by default.

That nuance is measurable statewide: `5345` directed pairs vs `111` reverse counterparts (`2.08%`). In practice, that means you should not infer two-way equivalence unless both directions are explicitly present as separate rows.

## Ameren Case Study Narrative

### What "24 lobbying entities tied to Ameren Illinois" means operationally

It means `24` distinct `entity_id` values are linked to `client_id=3821` in `lobbying_entity_clients`. This is a registry-level count of represented entities, not a lobbyist headcount.

### What the `83` pair rows imply

The `83` rows represent entity-client-year observations. The same entity can appear repeatedly in different years, so row volume captures persistence over time as well as breadth.

### Lobbyist-level relationship picture (now computed)

For Ameren Illinois, `40` distinct lobbyists appear in registrations linked to that client. Yearly distinct-lobbyist counts are:

- `2022`: `26`
- `2023`: `21`
- `2024`: `22`
- `2025`: `28`
- `2026`: `23`

Network-structure summary for Ameren Illinois registrations:

- `40` unique lobbyists
- `23` unique entities represented in those lobbyist registrations
- `121` unique lobbyist-entity-client-year registration rows

Why `23` here vs `24` in the entity-client table: one Ameren-linked entity appears in the entity-client registry but has no lobbyist registration row in this snapshot.

### What `45` matched donors and `90` matched payee edges suggest

These bridge counts indicate that names tied to Ameren Illinois in lobbying data also appear in campaign-finance donor and payee matching tables. That provides auditable overlap paths for investigation, but it does not establish motive, coordination, or causality by itself.

## Data Quality and Entity Resolution

Name fragmentation remains a core quality issue. The same real-world organization can appear under multiple text variants across lobbying and campaign-finance datasets.

Observed indicator in this snapshot:

- `264` donor keys with names matching `%ameren%`
- Total amount across those keys: `$8,057,465.60`

Normalization plan (unchanged, still recommended):

1. Maintain a canonical alias table (`entity_alias_canonical`) with source and confidence metadata.
2. Apply deterministic normalization (case, punctuation, whitespace, suffix cleanup) first.
3. Seed explicit Ameren rules (`AMEREN ILLINOIS`, `AMEREN ILLINOIS PAC`, `AMEREN TRANSMISSION COMPANY OF ILLINOIS`).
4. Keep manual override for ambiguous/high-dollar rows.
5. Persist both raw and canonical identifiers in outputs for full audit traceability.

## What Was Previously Missing Is Now Computed

The previously missing lobbyist-level relationship tables are now present and populated:

- `lobbying_lobbyists`
- `lobbying_lobbyist_registrations`

Because those tables are loaded, the following are now computed in this snapshot:

- Ameren lobbyist counts overall and by year.
- Lobbyist-entity-client structure metrics for Ameren-linked registrations.
- Yearly campaign-spending trend for committees connected through Ameren payee-match pathways.

### Spending trend caveat (important)

The spending trend query aggregates all expenditures from committees that match Ameren-linked payee pathways. This is **not** a measure of "Ameren spending"; it is a committee-level pathway proxy.

Observed pathway aggregate:

- `87` matched committees
- `198,776` expenditure rows (`1999-2026`)
- `$442,594,201.57` total (anomalous expenditure rows excluded)

Top years by pathway amount:

- `2018`: `$113,411,772.30`
- `2016`: `$76,176,787.16`
- `2014`: `$75,661,601.33`

Recent years in current snapshot:

- `2022`: `$16,111,798.74`
- `2023`: `$3,125,329.29`
- `2024`: `$4,094,507.04`
- `2025`: `$3,150,863.86`
- `2026`: `$4,316.50`

## OpenBook Contract Evidence (Production Targeted Run)

To expand beyond lobbying/campaign-finance overlap, a targeted OpenBook run was executed on production using Ameren-seeded vendor terms (`seed_source='ameren_case_study'`) and exported to local analysis files.

Observed OpenBook matching and contract footprint:

- `5` Ameren seeds loaded, producing `6` matched OpenBook vendor keys (`1` exact, `23` prefix, `1` no-match row).
- Raw matched contract rows: `3003`, totaling `$332,379,837.35`.
- Conservative deduped view (collapsing likely alias duplicates by contract number + year + agency + amount): `1021` records totaling `$116,057,036.29`.
- Deduped top agency totals: `TRANSPORTATION` (`$107,424,792.13`), `COMMERCE AND ECONOMIC OPPORTUN` (`$5,613,468.91`), `CAPITAL DEVELOPMENT BOARD` (`$3,018,775.25`).
- Warrant-detail rows: `27` with `$6,498,534.54` in total payment amount.

Interpretation limit: this is name-based vendor matching and contract overlap evidence, not proof of causality or policy influence. Full OpenBook methodology and tables are documented in `/Users/devin/Illinois_campaign_finance/docs/case_studies/ameren_openbook_contracts_analysis.md`.

## Socratic Findings (Simple -> Complex)

A question-led follow-up analysis is now documented in `/Users/devin/Illinois_campaign_finance/docs/case_studies/ameren_socratic_analysis.md`, with reproducible SQL and CSV outputs.

Key takeaways from that Socratic pass:

- [Observed] Ameren scope remains concentrated in two lobbying clients, with `24` distinct entities and `84` total entity-client-year rows (`83` for `AMEREN ILLINOIS`, `1` for transmission company).
- [Observed] Lobbyist-level analysis remains directly computable in this snapshot: `40` distinct lobbyists for `AMEREN ILLINOIS`, with yearly counts from `2022-2026` visible in trend tables.
- [Observed] Relationship structure is still strongly directional statewide (`5345` directed pairs, `111` reverse; `2.08%`), and Ameren-directed pairs have no reverse counterpart in this snapshot.
- [Observed] Cross-domain overlap persists for `AMEREN ILLINOIS` (`45` donor keys, `90` payee edges, `0` matched 527 orgs), and Ameren-seeded OpenBook scope contains `3003` contract rows and `27` warrant rows.
- [Inference] These results strengthen the overlap map and audit trail across datasets, but still do not establish causal influence.

## Methods Appendix (SQL)

### A. Ameren entity counts + pair rows

```sql
SELECT
  c.client_id,
  c.client_name,
  COUNT(DISTINCT ec.entity_id) AS lobbying_entity_count,
  COUNT(*) AS pair_rows
FROM lobbying_clients c
LEFT JOIN lobbying_entity_clients ec
  ON ec.client_id = c.client_id
WHERE c.client_name ILIKE '%ameren%'
GROUP BY c.client_id, c.client_name
ORDER BY lobbying_entity_count DESC, c.client_name;
```

### B. Directed vs reverse-pair relationship rate

```sql
WITH pairs AS (
  SELECT DISTINCT entity_id, client_id
  FROM lobbying_entity_clients
  WHERE client_id IS NOT NULL
)
SELECT
  COUNT(*) AS directed_pair_count,
  COUNT(*) FILTER (
    WHERE EXISTS (
      SELECT 1
      FROM pairs r
      WHERE r.entity_id = pairs.client_id
        AND r.client_id = pairs.entity_id
    )
  ) AS has_reverse_pair_count
FROM pairs;
```

### C. Donor variants for `%ameren%`

```sql
SELECT COUNT(*) AS raw_variant_rows,
       COUNT(DISTINCT donor_key) AS raw_variant_keys,
       SUM(total_amount) AS raw_total_amount
FROM analytics_donor_summary
WHERE source='bulk_receipts'
  AND donor_name ILIKE '%ameren%';
```

### D. Ameren bridge (lobbying -> donor/payee/527)

```sql
WITH ameren_clients AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
)
SELECT
  ac.client_id,
  ac.client_name,
  COUNT(DISTINCT ec.entity_id) AS lobbying_entity_count,
  COUNT(DISTINCT ldm.donor_key) AS matched_donor_count,
  COUNT(DISTINCT l5.ein) AS matched_527_count,
  COUNT(DISTINCT lem.match_id) AS matched_payee_edge_count
FROM ameren_clients ac
LEFT JOIN lobbying_entity_clients ec ON ec.client_id = ac.client_id
LEFT JOIN lobbying_donor_matches ldm ON ldm.client_id = ac.client_id
LEFT JOIN lobbying_527_matches l5 ON l5.client_id = ac.client_id
LEFT JOIN lobbying_expenditure_matches lem
  ON lem.source_type='client' AND lem.source_id = ac.client_id
GROUP BY ac.client_id, ac.client_name
ORDER BY lobbying_entity_count DESC, ac.client_name;
```

### E. Ameren lobbyist counts (overall)

```sql
SELECT
  c.client_id,
  c.client_name,
  COUNT(DISTINCT lr.lobbyist_id) AS lobbyist_count
FROM lobbying_clients c
LEFT JOIN lobbying_lobbyist_registrations lr
  ON lr.client_id = c.client_id
WHERE c.client_name ILIKE '%ameren%'
GROUP BY c.client_id, c.client_name
ORDER BY lobbyist_count DESC, c.client_name;
```

### F. Ameren lobbyist counts by year

```sql
SELECT
  c.client_id,
  c.client_name,
  lr.reg_year,
  COUNT(DISTINCT lr.lobbyist_id) AS lobbyist_count
FROM lobbying_clients c
JOIN lobbying_lobbyist_registrations lr
  ON lr.client_id = c.client_id
WHERE c.client_name ILIKE '%ameren%'
GROUP BY c.client_id, c.client_name, lr.reg_year
ORDER BY c.client_name, lr.reg_year;
```

### G. Lobbyist-entity-client network structure summary

```sql
WITH ameren_clients AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
)
SELECT
  ac.client_id,
  ac.client_name,
  COUNT(DISTINCT lr.lobbyist_id) AS unique_lobbyists,
  COUNT(DISTINCT lr.entity_id) AS unique_entities,
  COUNT(DISTINCT (
    lr.lobbyist_id::text || '-' || lr.entity_id::text || '-' ||
    COALESCE(lr.client_id::text,'NULL') || '-' || COALESCE(lr.reg_year::text,'NULL')
  )) AS unique_lobbyist_entity_client_year_rows
FROM ameren_clients ac
LEFT JOIN lobbying_lobbyist_registrations lr
  ON lr.client_id = ac.client_id
GROUP BY ac.client_id, ac.client_name
ORDER BY unique_lobbyists DESC, ac.client_name;
```

### H. Top Ameren-registered lobbyists by entity breadth

```sql
WITH ameren_clients AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
)
SELECT
  ac.client_name,
  lr.lobbyist_id,
  l.last_name,
  l.first_name,
  COUNT(DISTINCT lr.entity_id) AS entity_count,
  COUNT(DISTINCT lr.reg_year) AS active_years,
  MIN(lr.reg_year) AS first_year,
  MAX(lr.reg_year) AS last_year
FROM ameren_clients ac
JOIN lobbying_lobbyist_registrations lr ON lr.client_id = ac.client_id
JOIN lobbying_lobbyists l ON l.lobbyist_id = lr.lobbyist_id
GROUP BY ac.client_name, lr.lobbyist_id, l.last_name, l.first_name
ORDER BY entity_count DESC, active_years DESC, ac.client_name, l.last_name, l.first_name
LIMIT 15;
```

### I. Yearly spending trend for Ameren-pathway matched committees

```sql
WITH ameren_committees AS (
  SELECT DISTINCT lem.committee_id_sbe
  FROM lobbying_expenditure_matches lem
  JOIN lobbying_clients c
    ON lem.source_type = 'client'
   AND lem.source_id = c.client_id
  WHERE c.client_name ILIKE '%ameren%'
    AND lem.committee_id_sbe IS NOT NULL
)
SELECT
  substr(be.expended_date, 1, 4) AS expended_year,
  COUNT(*) AS expenditure_rows,
  SUM(be.amount) AS total_amount
FROM bulk_expenditures_clean be
JOIN ameren_committees ac
  ON ac.committee_id_sbe = be.committee_id_sbe
WHERE (be.is_amount_anomalous = 0 OR be.is_amount_anomalous IS NULL)
GROUP BY substr(be.expended_date, 1, 4)
ORDER BY expended_year;
```
