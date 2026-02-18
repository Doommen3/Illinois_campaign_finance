-- Ameren Socratic analysis query pack (local PostgreSQL snapshot)
-- As of: 2026-02-16
-- Scope: reproducible SQL used to generate /output/csv/ameren_socratic_*.csv

-- Q00. Required table availability
SELECT req.table_name,
       CASE WHEN t.table_name IS NOT NULL THEN 1 ELSE 0 END AS is_present
FROM (
    VALUES
    ('lobbying_clients'),
    ('lobbying_entities'),
    ('lobbying_entity_clients'),
    ('lobbying_lobbyists'),
    ('lobbying_lobbyist_registrations'),
    ('analytics_donor_summary'),
    ('lobbying_donor_matches'),
    ('lobbying_expenditure_matches'),
    ('lobbying_527_matches'),
    ('openbook_vendor_seed'),
    ('openbook_vendor_match'),
    ('openbook_contracts_raw'),
    ('openbook_contract_warrants'),
    ('openbook_scrape_runs')
) AS req(table_name)
LEFT JOIN information_schema.tables t
  ON t.table_schema = 'public'
 AND t.table_name = req.table_name
ORDER BY req.table_name;

-- Q01. Ameren scope: client footprint + lobbyist counts
WITH ameren_clients AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
),
entity_counts AS (
  SELECT client_id,
         COUNT(DISTINCT entity_id) AS lobbying_entity_count,
         COUNT(*) AS pair_rows
  FROM lobbying_entity_clients
  GROUP BY client_id
),
lobbyist_counts AS (
  SELECT client_id,
         COUNT(DISTINCT lobbyist_id) AS lobbyist_count
  FROM lobbying_lobbyist_registrations
  GROUP BY client_id
)
SELECT
  ac.client_id,
  ac.client_name,
  COALESCE(ec.lobbying_entity_count, 0) AS lobbying_entity_count,
  COALESCE(ec.pair_rows, 0) AS pair_rows,
  COALESCE(lc.lobbyist_count, 0) AS lobbyist_count
FROM ameren_clients ac
LEFT JOIN entity_counts ec ON ec.client_id = ac.client_id
LEFT JOIN lobbyist_counts lc ON lc.client_id = ac.client_id
ORDER BY lobbying_entity_count DESC, ac.client_name;

-- Q02. Ameren scope: entity list by client
SELECT DISTINCT
  ec.entity_id,
  e.entity_name,
  c.client_id,
  c.client_name
FROM lobbying_clients c
JOIN lobbying_entity_clients ec ON ec.client_id = c.client_id
LEFT JOIN lobbying_entities e ON e.entity_id = ec.entity_id
WHERE c.client_name ILIKE '%ameren%'
ORDER BY c.client_name, e.entity_name NULLS LAST, ec.entity_id;

-- Q03. Descriptive rollup across domains
WITH ameren_clients AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
),
ameren_entities AS (
  SELECT DISTINCT ec.entity_id
  FROM lobbying_entity_clients ec
  JOIN ameren_clients ac ON ac.client_id = ec.client_id
),
openbook_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
)
SELECT
  (SELECT COUNT(*) FROM ameren_clients) AS ameren_client_count,
  (SELECT COUNT(*) FROM ameren_entities) AS ameren_distinct_entity_count,
  (SELECT COUNT(*) FROM lobbying_entity_clients ec JOIN ameren_clients ac ON ac.client_id = ec.client_id) AS ameren_pair_rows,
  (SELECT COUNT(DISTINCT lr.lobbyist_id) FROM lobbying_lobbyist_registrations lr JOIN ameren_clients ac ON ac.client_id = lr.client_id) AS ameren_distinct_lobbyists,
  (SELECT COUNT(DISTINCT donor_key) FROM analytics_donor_summary WHERE source='bulk_receipts' AND donor_name ILIKE '%ameren%') AS donor_variant_keys,
  (SELECT ROUND(COALESCE(SUM(total_amount), 0)::numeric, 2) FROM analytics_donor_summary WHERE source='bulk_receipts' AND donor_name ILIKE '%ameren%') AS donor_variant_total_amount,
  (SELECT COUNT(*) FROM openbook_vendor_seed WHERE seed_source='ameren_case_study') AS openbook_seed_count,
  (SELECT COUNT(*) FROM openbook_vendor_match m JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id WHERE s.seed_source='ameren_case_study') AS openbook_match_rows,
  (SELECT COUNT(*) FROM openbook_keys) AS openbook_vendor_key_count,
  (SELECT COUNT(*) FROM openbook_contracts_raw c JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key) AS openbook_contract_rows,
  (SELECT ROUND(COALESCE(SUM(c.award_amount::numeric), 0), 2) FROM openbook_contracts_raw c JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key) AS openbook_contract_award_total,
  (SELECT COUNT(*) FROM openbook_contract_warrants w JOIN openbook_keys k ON k.openbook_vendor_key = w.openbook_vendor_key) AS openbook_warrant_rows,
  (SELECT ROUND(COALESCE(SUM(w.payment_amount::numeric), 0), 2) FROM openbook_contract_warrants w JOIN openbook_keys k ON k.openbook_vendor_key = w.openbook_vendor_key) AS openbook_warrant_total;

-- Q04. Lobbying time trend (Ameren clients)
WITH ameren_clients AS (
  SELECT client_id
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
)
SELECT
  ec.reg_year,
  COUNT(*) AS pair_rows,
  COUNT(DISTINCT ec.entity_id) AS distinct_entities,
  COUNT(DISTINCT lr.lobbyist_id) AS distinct_lobbyists
FROM lobbying_entity_clients ec
JOIN ameren_clients ac ON ac.client_id = ec.client_id
LEFT JOIN lobbying_lobbyist_registrations lr
  ON lr.client_id = ec.client_id
 AND lr.entity_id = ec.entity_id
 AND lr.reg_year = ec.reg_year
GROUP BY ec.reg_year
ORDER BY ec.reg_year;

-- Q05. OpenBook contract trend (raw + dedup)
WITH openbook_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
),
raw AS (
  SELECT
    c.fiscal_year,
    COUNT(*) AS raw_contract_rows,
    ROUND(COALESCE(SUM(c.award_amount::numeric), 0), 2) AS raw_award_amount
  FROM openbook_contracts_raw c
  JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key
  GROUP BY c.fiscal_year
),
dedup AS (
  SELECT DISTINCT ON (
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0)
  )
    c.fiscal_year,
    c.award_amount::numeric AS award_amount
  FROM openbook_contracts_raw c
  JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key
  ORDER BY
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0),
    c.id
),
dedup_rollup AS (
  SELECT
    fiscal_year,
    COUNT(*) AS dedup_contract_rows,
    ROUND(COALESCE(SUM(award_amount), 0), 2) AS dedup_award_amount
  FROM dedup
  GROUP BY fiscal_year
)
SELECT
  COALESCE(r.fiscal_year, d.fiscal_year) AS fiscal_year,
  COALESCE(r.raw_contract_rows, 0) AS raw_contract_rows,
  COALESCE(r.raw_award_amount, 0) AS raw_award_amount,
  COALESCE(d.dedup_contract_rows, 0) AS dedup_contract_rows,
  COALESCE(d.dedup_award_amount, 0) AS dedup_award_amount
FROM raw r
FULL OUTER JOIN dedup_rollup d ON d.fiscal_year = r.fiscal_year
ORDER BY fiscal_year;

-- Q06. OpenBook warrant trend
WITH openbook_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_match m
  JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
  WHERE s.seed_source = 'ameren_case_study'
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
)
SELECT
  w.fiscal_year,
  COUNT(*) AS warrant_rows,
  ROUND(COALESCE(SUM(w.payment_amount::numeric), 0), 2) AS total_payment_amount
FROM openbook_contract_warrants w
JOIN openbook_keys k ON k.openbook_vendor_key = w.openbook_vendor_key
GROUP BY w.fiscal_year
ORDER BY w.fiscal_year;

-- Q07. Relationship directionality (statewide + Ameren subset)
WITH statewide_pairs AS (
  SELECT DISTINCT entity_id, client_id
  FROM lobbying_entity_clients
  WHERE client_id IS NOT NULL
),
ameren_clients AS (
  SELECT client_id
  FROM lobbying_clients
  WHERE client_name ILIKE '%ameren%'
),
ameren_pairs AS (
  SELECT DISTINCT ec.entity_id, ec.client_id
  FROM lobbying_entity_clients ec
  JOIN ameren_clients ac ON ac.client_id = ec.client_id
)
SELECT
  (SELECT COUNT(*) FROM statewide_pairs) AS directed_pair_count,
  (SELECT COUNT(*) FROM statewide_pairs p
     WHERE EXISTS (
       SELECT 1 FROM statewide_pairs r
       WHERE r.entity_id = p.client_id
         AND r.client_id = p.entity_id
     )) AS has_reverse_pair_count,
  ROUND(
    100.0 *
    (SELECT COUNT(*) FROM statewide_pairs p
       WHERE EXISTS (
         SELECT 1 FROM statewide_pairs r
         WHERE r.entity_id = p.client_id
           AND r.client_id = p.entity_id
       ))
    / NULLIF((SELECT COUNT(*) FROM statewide_pairs), 0),
    2
  ) AS reverse_pair_pct,
  (SELECT COUNT(*) FROM ameren_pairs) AS ameren_directed_pair_count,
  (SELECT COUNT(*) FROM ameren_pairs p
     WHERE EXISTS (
       SELECT 1 FROM statewide_pairs r
       WHERE r.entity_id = p.client_id
         AND r.client_id = p.entity_id
     )) AS ameren_has_reverse_pair_count;

-- Q08. Lobbyist -> entity -> client network structure
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
LEFT JOIN lobbying_lobbyist_registrations lr ON lr.client_id = ac.client_id
GROUP BY ac.client_id, ac.client_name
ORDER BY unique_lobbyists DESC, ac.client_name;

-- Q09. Cross-domain bridge overlap (donor/payee/527)
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
  COUNT(DISTINCT lem.match_id) AS matched_payee_edge_count,
  COUNT(DISTINCT l5.ein) AS matched_527_count
FROM ameren_clients ac
LEFT JOIN lobbying_entity_clients ec ON ec.client_id = ac.client_id
LEFT JOIN lobbying_donor_matches ldm ON ldm.client_id = ac.client_id
LEFT JOIN lobbying_expenditure_matches lem
  ON lem.source_type='client' AND lem.source_id = ac.client_id
LEFT JOIN lobbying_527_matches l5 ON l5.client_id = ac.client_id
GROUP BY ac.client_id, ac.client_name
ORDER BY lobbying_entity_count DESC, ac.client_name;

-- Q10. OpenBook match-method distribution
SELECT
  m.match_method,
  COUNT(*) AS match_rows
FROM openbook_vendor_match m
JOIN openbook_vendor_seed s ON s.seed_id = m.seed_id
WHERE s.seed_source = 'ameren_case_study'
GROUP BY m.match_method
ORDER BY m.match_method;

-- Q11. OpenBook best row per matched vendor key
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

-- Q12. OpenBook top agencies (deduped contracts)
WITH openbook_keys AS (
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
    c.agency_name,
    c.award_amount::numeric AS award_amount
  FROM openbook_contracts_raw c
  JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key
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
  ROUND(COALESCE(SUM(award_amount), 0), 2) AS total_award_amount
FROM dedup
GROUP BY agency_name
ORDER BY total_award_amount DESC NULLS LAST
LIMIT 15;

-- Q13. OpenBook top contracts (deduped with alias list)
WITH openbook_keys AS (
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
    c.contract_number,
    c.agency_name,
    c.award_amount::numeric AS award_amount
  FROM openbook_contracts_raw c
  JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key
  ORDER BY
    COALESCE(c.contract_number, ''),
    COALESCE(c.fiscal_year, -1),
    COALESCE(c.agency_name, ''),
    COALESCE(c.award_amount, 0),
    c.id
),
alias_map AS (
  SELECT
    c.fiscal_year,
    c.contract_number,
    c.agency_name,
    c.award_amount::numeric AS award_amount,
    string_agg(DISTINCT c.openbook_vendor_key, ', ' ORDER BY c.openbook_vendor_key) AS alias_vendor_keys_seen
  FROM openbook_contracts_raw c
  JOIN openbook_keys k ON k.openbook_vendor_key = c.openbook_vendor_key
  GROUP BY c.fiscal_year, c.contract_number, c.agency_name, c.award_amount
)
SELECT
  d.fiscal_year,
  d.contract_number,
  d.agency_name,
  ROUND(d.award_amount, 2) AS award_amount,
  a.alias_vendor_keys_seen
FROM dedup d
LEFT JOIN alias_map a
  ON a.fiscal_year IS NOT DISTINCT FROM d.fiscal_year
 AND a.contract_number IS NOT DISTINCT FROM d.contract_number
 AND a.agency_name IS NOT DISTINCT FROM d.agency_name
 AND a.award_amount IS NOT DISTINCT FROM d.award_amount
ORDER BY d.award_amount DESC NULLS LAST
LIMIT 20;

-- Q14. Local scrape-run metadata snapshot
SELECT
  run_id,
  started_at,
  completed_at,
  mode,
  seed_count,
  match_count,
  contract_rows,
  contribution_rows,
  error_count,
  notes
FROM openbook_scrape_runs
ORDER BY run_id DESC
LIMIT 15;
