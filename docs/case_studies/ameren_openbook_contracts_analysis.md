# Ameren OpenBook Contract Analysis (Production-Sourced)

**As of:** 2026-02-16  
**Data source:** Targeted OpenBook scraping on production DB, exported to local CSV for analysis.

## Scope and Matching

- Seed source: `ameren_case_study`
- Seeds loaded: `5`
- Match rows recorded: `25` (including one `no_match`)
- `no_match` rows: `1`
- Unique matched vendor keys: `6`

Match method distribution (rows):

- `exact`: `1`
- `no_match`: `1`
- `prefix`: `23`

### Matched Vendor Keys (best observed confidence)

| vendor_key | vendor_label | match_method | confidence | search_term_used |
| --- | --- | --- | --- | --- |
| AMEREN ILLINOIS | AMEREN ILLINOIS | exact | 1 | AMEREN |
| AMEREN ILLINOIS COMPANY | AMEREN ILLINOIS COMPANY | prefix | 0.9 | AMEREN |
| AMEREN ILLINOIS CO | AMEREN ILLINOIS CO | prefix | 0.9 | AMEREN |
| AMEREN ENERGY RESOURCES | AMEREN ENERGY RESOURCES | prefix | 0.9 | AMEREN |
| AMEREN ENERGY GENERATING CO | AMEREN ENERGY GENERATING CO | prefix | 0.9 | AMEREN |
| AMEREN CIPS | AMEREN CIPS | prefix | 0.9 | AMEREN |

## Contract Footprint

Raw row totals from matched vendor keys:
- Contract rows: `3003`
- Total raw `award_amount`: `$332,379,837.35`

Deduped contract footprint (collapsed across likely alias duplicates by `contract_number + fiscal_year + agency_name + award_amount`):
- Deduped contract records: `1021`
- Deduped total `award_amount`: `$116,057,036.29`

### Vendor-Key Totals (Raw, Before Deduping)

| vendor_key | contract_rows | total_award_amount |
| --- | --- | --- |
| AMEREN ILLINOIS | 1012 | $110,911,808.23 |
| AMEREN ILLINOIS CO | 994 | $108,359,201.27 |
| AMEREN ILLINOIS COMPANY | 988 | $107,963,599.79 |
| AMEREN ENERGY RESOURCES | 2 | $4,000,000.00 |
| AMEREN ENERGY GENERATING CO | 4 | $965,871.15 |
| AMEREN CIPS | 3 | $179,356.91 |

### Yearly Trend by Fiscal Year (Deduped)

| fiscal_year | contract_rows | total_award_amount |
| --- | --- | --- |
| 2005 | 3 | $179,356.91 |
| 2008 | 1 | $4,000,000.00 |
| 2009 | 1 | $0.00 |
| 2011 | 45 | $9,406,574.48 |
| 2012 | 120 | $15,609,453.22 |
| 2013 | 120 | $8,126,256.58 |
| 2014 | 129 | $7,314,810.38 |
| 2015 | 131 | $9,823,000.59 |
| 2016 | 111 | $5,219,379.45 |
| 2017 | 79 | $3,892,799.42 |
| 2018 | 42 | $4,003,916.74 |
| 2019 | 26 | $3,592,035.31 |
| 2020 | 28 | $3,297,270.23 |
| 2021 | 27 | $3,919,874.54 |
| 2022 | 33 | $7,114,975.77 |
| 2023 | 31 | $9,506,568.95 |
| 2024 | 32 | $6,845,161.16 |
| 2025 | 30 | $6,231,726.47 |
| 2026 | 32 | $7,973,876.09 |

### Top Agencies by Award Amount (Deduped)

| agency_name | contract_rows | total_award_amount |
| --- | --- | --- |
| TRANSPORTATION | 1007 | $107,424,792.13 |
| COMMERCE AND ECONOMIC OPPORTUN | 9 | $5,613,468.91 |
| CAPITAL DEVELOPMENT BOARD | 5 | $3,018,775.25 |

### Top Contracts by Award Amount (Deduped)

| fiscal_year | contract_number | agency_name | award_amount | alias_vendor_keys_seen |
| --- | --- | --- | --- | --- |
| 2008 | 80008483002 | COMMERCE AND ECONOMIC OPPORTUN | $4,000,000.00 | AMEREN ENERGY RESOURCES |
| 2023 | 30000007362 | CAPITAL DEVELOPMENT BOARD | $2,860,163.21 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2012 | 200UT810074 | TRANSPORTATION | $2,700,000.00 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2015 | 500UT914006 | TRANSPORTATION | $2,015,114.72 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2019 | 900UT917002 | TRANSPORTATION | $2,009,991.85 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2023 | 300UT423001 | TRANSPORTATION | $1,960,083.97 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2024 | 400UT423001 | TRANSPORTATION | $1,960,083.97 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2025 | 500UT423001 | TRANSPORTATION | $1,960,083.97 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2026 | 600UT423001 | TRANSPORTATION | $1,960,083.97 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2018 | 800UT917002 | TRANSPORTATION | $1,679,414.89 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2011 | 100UT808024 | TRANSPORTATION | $1,577,341.92 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2011 | 100UT809006 | TRANSPORTATION | $1,500,000.00 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2026 | 600UT925009 | TRANSPORTATION | $1,402,494.49 | AMEREN ILLINOIS |
| 2022 | 200UT422002 | TRANSPORTATION | $1,396,890.93 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |
| 2023 | 300UT422002 | TRANSPORTATION | $1,396,890.93 | AMEREN ILLINOIS, AMEREN ILLINOIS CO, AMEREN ILLINOIS COMPANY |

## Warrant Detail Footprint

- Warrant rows (raw): `27`
- Distinct warrant rows (date/amount key): `27`
- Total warrant payment amount: `$6,498,534.54`

### Warrant Trend by Fiscal Year

| fiscal_year | warrant_rows | total_payment_amount |
| --- | --- | --- |
| 2023 | 7 | $3,815,704.23 |
| 2024 | 7 | $1,244,561.29 |
| 2025 | 6 | $852,002.78 |
| 2026 | 7 | $586,266.24 |

## Interpretation and Caveats

- These results are name-matched OpenBook pathways from Ameren-seeded terms, not legal-entity-verified identity resolution.
- Several Ameren-like vendor keys appear to represent alias variants of the same underlying vendor; deduped estimates are the more conservative view.
- `award_amount` values reflect contract listing amounts in OpenBook and do not, by themselves, imply influence, causality, or policy outcomes.
- Warrant detail coverage is partial relative to total contracts and should be treated as supplemental payment evidence.

## Files Produced

- `/Users/devin/Illinois_campaign_finance/output/csv/ameren_openbook_matches.csv`
- `/Users/devin/Illinois_campaign_finance/output/csv/ameren_openbook_contracts.csv`
- `/Users/devin/Illinois_campaign_finance/output/csv/ameren_openbook_warrants.csv`

## SQL Appendix (Reproducible)

### 1) Ameren seed build (idempotent)

```sql
WITH base_names AS (
  SELECT DISTINCT TRIM(client_name) AS seed_text
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
  UNION
  SELECT DISTINCT TRIM(entity_name) AS seed_text
  FROM lobbying_entities
  WHERE entity_name ILIKE '%ameren%'
),
curated(seed_text) AS (
  VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN CORPORATION'),
    ('AMEREN SERVICES'),
    ('AMEREN SERVICES COMPANY'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS')
),
all_seeds AS (
  SELECT seed_text FROM base_names
  UNION
  SELECT seed_text FROM curated
)
INSERT INTO openbook_vendor_seed (seed_text, seed_source)
SELECT seed_text, 'ameren_case_study'
FROM all_seeds
WHERE seed_text IS NOT NULL
  AND seed_text <> ''
ON CONFLICT (seed_text, seed_source) DO NOTHING;
```

### 2) Preflight counts (targeted scope)

```sql
WITH unresolved AS (
  SELECT COUNT(*) AS c
  FROM openbook_vendor_seed s
  LEFT JOIN openbook_vendor_match m ON m.seed_id = s.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_id IS NULL
),
unscraped AS (
  SELECT COUNT(DISTINCT m.openbook_vendor_key) AS c
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  LEFT JOIN openbook_contracts_raw c
    ON c.openbook_vendor_key = m.openbook_vendor_key
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
    AND c.id IS NULL
)
SELECT
  (SELECT c FROM unresolved) AS unresolved_seed_count,
  (SELECT c FROM unscraped) AS unscraped_vendor_key_count;
```

### 3) Match quality and vendor keys

```sql
SELECT
  m.match_method,
  COUNT(*) AS match_rows
FROM openbook_vendor_match m
JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
WHERE s.seed_source = 'ameren_case_study'
GROUP BY m.match_method
ORDER BY m.match_method;
```

```sql
WITH ranked AS (
  SELECT
    m.openbook_vendor_key,
    m.openbook_vendor_label,
    m.match_method,
    m.confidence,
    m.search_term_used,
    ROW_NUMBER() OVER (
      PARTITION BY m.openbook_vendor_key
      ORDER BY m.confidence DESC, m.match_id ASC
    ) AS rn
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
)
SELECT
  openbook_vendor_key,
  openbook_vendor_label,
  match_method,
  confidence,
  search_term_used
FROM ranked
WHERE rn = 1
ORDER BY confidence DESC, openbook_vendor_key;
```

### 4) Contract and warrant rollups

```sql
WITH vendor_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
)
SELECT
  COUNT(*) AS contract_rows,
  ROUND(COALESCE(SUM(c.award_amount), 0)::numeric, 2) AS total_award_amount
FROM openbook_contracts_raw c
JOIN vendor_keys vk ON vk.openbook_vendor_key = c.openbook_vendor_key;
```

```sql
WITH vendor_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
),
dedup AS (
  SELECT DISTINCT ON (
      COALESCE(c.contract_number, ''),
      COALESCE(c.fiscal_year, -1),
      COALESCE(c.agency_name, ''),
      COALESCE(c.award_amount, 0)
  )
    c.fiscal_year,
    c.agency_name,
    c.contract_number,
    c.award_amount
  FROM openbook_contracts_raw c
  JOIN vendor_keys vk ON vk.openbook_vendor_key = c.openbook_vendor_key
  ORDER BY
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0),
    c.id
)
SELECT
  COUNT(*) AS deduped_contract_rows,
  ROUND(COALESCE(SUM(award_amount), 0)::numeric, 2) AS deduped_total_award_amount
FROM dedup;
```

```sql
WITH vendor_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
),
dedup AS (
  SELECT DISTINCT ON (
      COALESCE(c.contract_number, ''),
      COALESCE(c.fiscal_year, -1),
      COALESCE(c.agency_name, ''),
      COALESCE(c.award_amount, 0)
  )
    c.fiscal_year,
    c.agency_name,
    c.contract_number,
    c.award_amount
  FROM openbook_contracts_raw c
  JOIN vendor_keys vk ON vk.openbook_vendor_key = c.openbook_vendor_key
  ORDER BY
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0),
    c.id
)
SELECT
  fiscal_year,
  COUNT(*) AS contract_rows,
  ROUND(COALESCE(SUM(award_amount), 0)::numeric, 2) AS total_award_amount
FROM dedup
GROUP BY fiscal_year
ORDER BY fiscal_year;
```

```sql
WITH vendor_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
),
dedup AS (
  SELECT DISTINCT ON (
      COALESCE(c.contract_number, ''),
      COALESCE(c.fiscal_year, -1),
      COALESCE(c.agency_name, ''),
      COALESCE(c.award_amount, 0)
  )
    c.fiscal_year,
    c.agency_name,
    c.contract_number,
    c.award_amount
  FROM openbook_contracts_raw c
  JOIN vendor_keys vk ON vk.openbook_vendor_key = c.openbook_vendor_key
  ORDER BY
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0),
    c.id
)
SELECT
  agency_name,
  COUNT(*) AS contract_rows,
  ROUND(COALESCE(SUM(award_amount), 0)::numeric, 2) AS total_award_amount
FROM dedup
GROUP BY agency_name
ORDER BY total_award_amount DESC NULLS LAST
LIMIT 10;
```

```sql
WITH vendor_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
)
SELECT
  COUNT(*) AS warrant_rows,
  ROUND(COALESCE(SUM(w.payment_amount), 0)::numeric, 2) AS total_payment_amount
FROM openbook_contract_warrants w
JOIN vendor_keys vk ON vk.openbook_vendor_key = w.openbook_vendor_key;
```
