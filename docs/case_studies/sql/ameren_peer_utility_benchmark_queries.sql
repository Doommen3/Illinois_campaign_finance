-- Ameren peer-utility benchmark pack (local PostgreSQL snapshot)
-- As of: 2026-02-16
-- Purpose: compare Ameren against a bounded utility peer set with statewide ranks.

-- Peer set used in this benchmark (name-based, not legal-entity-normalized):
WITH peer_names AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC')
  ) AS p(client_name)
)
SELECT c.client_id, c.client_name
FROM lobbying_clients c
JOIN peer_names p ON p.client_name = c.client_name
ORDER BY c.client_name;


-- Core metric model (all clients), then filtered/ranked for peers.
WITH peer_names AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC')
  ) AS p(client_name)
),
base_clients AS (
  SELECT c.client_id, c.client_name
  FROM lobbying_clients c
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
),
donor_counts AS (
  SELECT client_id,
         COUNT(DISTINCT donor_key) AS matched_donor_count
  FROM lobbying_donor_matches
  GROUP BY client_id
),
payee_counts AS (
  SELECT source_id AS client_id,
         COUNT(DISTINCT match_id) AS matched_payee_edge_count
  FROM lobbying_expenditure_matches
  WHERE source_type = 'client'
  GROUP BY source_id
),
match527_counts AS (
  SELECT client_id,
         COUNT(DISTINCT ein) AS matched_527_count
  FROM lobbying_527_matches
  GROUP BY client_id
),
metrics AS (
  SELECT
    bc.client_id,
    bc.client_name,
    COALESCE(ec.lobbying_entity_count, 0) AS lobbying_entity_count,
    COALESCE(ec.pair_rows, 0) AS pair_rows,
    COALESCE(lc.lobbyist_count, 0) AS lobbyist_count,
    COALESCE(dc.matched_donor_count, 0) AS matched_donor_count,
    COALESCE(pc.matched_payee_edge_count, 0) AS matched_payee_edge_count,
    COALESCE(m5.matched_527_count, 0) AS matched_527_count
  FROM base_clients bc
  LEFT JOIN entity_counts ec ON ec.client_id = bc.client_id
  LEFT JOIN lobbyist_counts lc ON lc.client_id = bc.client_id
  LEFT JOIN donor_counts dc ON dc.client_id = bc.client_id
  LEFT JOIN payee_counts pc ON pc.client_id = bc.client_id
  LEFT JOIN match527_counts m5 ON m5.client_id = bc.client_id
),
ranked AS (
  SELECT
    m.*,
    (m.matched_donor_count + m.matched_payee_edge_count) AS overlap_score,
    ROW_NUMBER() OVER (ORDER BY m.lobbying_entity_count DESC, m.pair_rows DESC, m.client_name) AS rank_entity_count_statewide,
    ROW_NUMBER() OVER (ORDER BY m.pair_rows DESC, m.client_name) AS rank_pair_rows_statewide,
    ROW_NUMBER() OVER (ORDER BY m.lobbyist_count DESC, m.client_name) AS rank_lobbyist_count_statewide,
    ROW_NUMBER() OVER (ORDER BY m.matched_donor_count DESC, m.client_name) AS rank_donor_match_statewide,
    ROW_NUMBER() OVER (ORDER BY m.matched_payee_edge_count DESC, m.client_name) AS rank_payee_match_statewide,
    ROW_NUMBER() OVER (ORDER BY (m.matched_donor_count + m.matched_payee_edge_count) DESC, m.client_name) AS rank_overlap_score_statewide,
    COUNT(*) OVER () AS statewide_client_count,
    ROUND((100.0 * CUME_DIST() OVER (ORDER BY m.lobbying_entity_count))::numeric, 2) AS entity_count_percentile,
    ROUND((100.0 * CUME_DIST() OVER (ORDER BY m.lobbyist_count))::numeric, 2) AS lobbyist_count_percentile,
    ROUND((100.0 * CUME_DIST() OVER (ORDER BY m.matched_donor_count))::numeric, 2) AS donor_match_percentile,
    ROUND((100.0 * CUME_DIST() OVER (ORDER BY m.matched_payee_edge_count))::numeric, 2) AS payee_match_percentile,
    ROUND((100.0 * CUME_DIST() OVER (ORDER BY (m.matched_donor_count + m.matched_payee_edge_count)))::numeric, 2) AS overlap_score_percentile
  FROM metrics m
),
peers AS (
  SELECT r.*
  FROM ranked r
  JOIN peer_names p ON p.client_name = r.client_name
)
SELECT
  p.*,
  ROW_NUMBER() OVER (ORDER BY p.lobbying_entity_count DESC, p.pair_rows DESC, p.client_name) AS rank_entity_count_within_peers,
  ROW_NUMBER() OVER (ORDER BY p.pair_rows DESC, p.client_name) AS rank_pair_rows_within_peers,
  ROW_NUMBER() OVER (ORDER BY p.lobbyist_count DESC, p.client_name) AS rank_lobbyist_count_within_peers,
  ROW_NUMBER() OVER (ORDER BY p.matched_donor_count DESC, p.client_name) AS rank_donor_match_within_peers,
  ROW_NUMBER() OVER (ORDER BY p.matched_payee_edge_count DESC, p.client_name) AS rank_payee_match_within_peers,
  ROW_NUMBER() OVER (ORDER BY p.overlap_score DESC, p.client_name) AS rank_overlap_score_within_peers,
  COUNT(*) OVER () AS peer_count
FROM peers p
ORDER BY p.client_name;


-- Year trend (2022-2026) for peers.
WITH peer_names AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC')
  ) AS p(client_name)
),
peers AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name IN (SELECT client_name FROM peer_names)
),
ec AS (
  SELECT client_id, reg_year,
         COUNT(*) AS pair_rows,
         COUNT(DISTINCT entity_id) AS distinct_entities
  FROM lobbying_entity_clients
  GROUP BY client_id, reg_year
),
lr AS (
  SELECT client_id, reg_year,
         COUNT(DISTINCT lobbyist_id) AS distinct_lobbyists
  FROM lobbying_lobbyist_registrations
  GROUP BY client_id, reg_year
),
years AS (
  SELECT reg_year FROM ec
  UNION
  SELECT reg_year FROM lr
)
SELECT
  p.client_name,
  y.reg_year,
  COALESCE(ec.pair_rows, 0) AS pair_rows,
  COALESCE(ec.distinct_entities, 0) AS distinct_entities,
  COALESCE(lr.distinct_lobbyists, 0) AS distinct_lobbyists
FROM peers p
JOIN years y ON TRUE
LEFT JOIN ec ON ec.client_id = p.client_id AND ec.reg_year = y.reg_year
LEFT JOIN lr ON lr.client_id = p.client_id AND lr.reg_year = y.reg_year
WHERE y.reg_year BETWEEN 2022 AND 2026
ORDER BY p.client_name, y.reg_year;


-- 2022 -> 2026 deltas for peers.
WITH peer_names AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC')
  ) AS p(client_name)
),
peers AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name IN (SELECT client_name FROM peer_names)
),
ec AS (
  SELECT client_id, reg_year,
         COUNT(*) AS pair_rows
  FROM lobbying_entity_clients
  GROUP BY client_id, reg_year
),
lr AS (
  SELECT client_id, reg_year,
         COUNT(DISTINCT lobbyist_id) AS distinct_lobbyists
  FROM lobbying_lobbyist_registrations
  GROUP BY client_id, reg_year
),
wide AS (
  SELECT
    p.client_id,
    p.client_name,
    COALESCE(MAX(CASE WHEN ec.reg_year=2022 THEN ec.pair_rows END),0) AS pair_rows_2022,
    COALESCE(MAX(CASE WHEN ec.reg_year=2026 THEN ec.pair_rows END),0) AS pair_rows_2026,
    COALESCE(MAX(CASE WHEN lr.reg_year=2022 THEN lr.distinct_lobbyists END),0) AS lobbyists_2022,
    COALESCE(MAX(CASE WHEN lr.reg_year=2026 THEN lr.distinct_lobbyists END),0) AS lobbyists_2026
  FROM peers p
  LEFT JOIN ec ON ec.client_id=p.client_id
  LEFT JOIN lr ON lr.client_id=p.client_id
  GROUP BY p.client_id, p.client_name
)
SELECT
  client_id,
  client_name,
  pair_rows_2022,
  pair_rows_2026,
  (pair_rows_2026 - pair_rows_2022) AS pair_rows_delta,
  lobbyists_2022,
  lobbyists_2026,
  (lobbyists_2026 - lobbyists_2022) AS lobbyists_delta
FROM wide
ORDER BY client_name;


-- OpenBook comparability note table for this peer set.
-- Important: current local openbook tables are Ameren-seed scoped.
WITH peer_names AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC')
  ) AS p(client_name)
),
peers AS (
  SELECT client_id, client_name
  FROM lobbying_clients
  WHERE client_name IN (SELECT client_name FROM peer_names)
)
SELECT
  p.client_name,
  COUNT(DISTINCT s.seed_id) FILTER (WHERE s.seed_source='ameren_case_study') AS ameren_seed_rows,
  COUNT(DISTINCT m.openbook_vendor_key) FILTER (
    WHERE s.seed_source='ameren_case_study'
      AND m.match_method <> 'no_match'
      AND COALESCE(m.openbook_vendor_key,'') <> ''
  ) AS matched_vendor_keys_in_scope,
  COUNT(c.id) FILTER (
    WHERE s.seed_source='ameren_case_study'
      AND m.match_method <> 'no_match'
      AND COALESCE(m.openbook_vendor_key,'') <> ''
  ) AS matched_contract_rows_in_scope
FROM peers p
LEFT JOIN openbook_vendor_seed s ON s.seed_text = p.client_name
LEFT JOIN openbook_vendor_match m ON m.seed_id = s.seed_id
LEFT JOIN openbook_contracts_raw c ON c.openbook_vendor_key = m.openbook_vendor_key
GROUP BY p.client_name
ORDER BY p.client_name;


-- OpenBook peer scrape result queries (manual seed-source runs on 2026-02-16)
-- Seed terms run:
--   Primary: AMEREN ILLINOIS, AMEREN TRANSMISSION COMPANY OF ILLINOIS,
--            COMED, COMMONWEALTH EDISON CO, NICOR GAS,
--            THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS),
--            ILLINOIS AMERICAN WATER COMPANY,
--            CONSTELLATION ENERGY GENERATION, LLC
--   Fallback: PEOPLES GAS, ILLINOIS AMERICAN WATER, AMERICAN WATER,
--             CONSTELLATION ENERGY, CONSTELLATION

WITH seed_terms AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC'),
    ('PEOPLES GAS'),
    ('ILLINOIS AMERICAN WATER'),
    ('AMERICAN WATER'),
    ('CONSTELLATION ENERGY'),
    ('CONSTELLATION')
  ) AS t(seed_text)
)
SELECT
  s.seed_text,
  s.seed_source,
  m.openbook_vendor_key,
  m.openbook_vendor_label,
  m.match_method,
  m.confidence,
  m.search_term_used,
  m.created_at
FROM openbook_vendor_seed s
LEFT JOIN openbook_vendor_match m ON m.seed_id = s.seed_id
WHERE s.seed_source = 'manual'
  AND s.seed_text IN (SELECT seed_text FROM seed_terms)
ORDER BY s.seed_text, m.created_at;


WITH seed_terms AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS'),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS'),
    ('COMED'),
    ('COMMONWEALTH EDISON CO'),
    ('NICOR GAS'),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)'),
    ('ILLINOIS AMERICAN WATER COMPANY'),
    ('CONSTELLATION ENERGY GENERATION, LLC'),
    ('PEOPLES GAS'),
    ('ILLINOIS AMERICAN WATER'),
    ('AMERICAN WATER'),
    ('CONSTELLATION ENERGY'),
    ('CONSTELLATION')
  ) AS t(seed_text)
),
matched_keys AS (
  SELECT DISTINCT m.openbook_vendor_key
  FROM openbook_vendor_seed s
  JOIN openbook_vendor_match m ON m.seed_id = s.seed_id
  WHERE s.seed_source = 'manual'
    AND s.seed_text IN (SELECT seed_text FROM seed_terms)
    AND m.match_method <> 'no_match'
    AND COALESCE(m.openbook_vendor_key, '') <> ''
),
contract_rollup AS (
  SELECT
    c.openbook_vendor_key,
    COUNT(*) AS contract_rows,
    COUNT(DISTINCT COALESCE(c.contract_number,'') || '|' || COALESCE(c.fiscal_year::text,'') || '|' ||
                     COALESCE(c.agency_name,'') || '|' || COALESCE(c.award_amount::text,'')) AS dedup_contract_rows,
    ROUND(COALESCE(SUM(c.award_amount::numeric), 0), 2) AS raw_award_total
  FROM openbook_contracts_raw c
  JOIN matched_keys mk ON mk.openbook_vendor_key = c.openbook_vendor_key
  GROUP BY c.openbook_vendor_key
),
warrant_rollup AS (
  SELECT
    w.openbook_vendor_key,
    COUNT(*) AS warrant_rows,
    ROUND(COALESCE(SUM(w.payment_amount::numeric), 0), 2) AS warrant_total
  FROM openbook_contract_warrants w
  JOIN matched_keys mk ON mk.openbook_vendor_key = w.openbook_vendor_key
  GROUP BY w.openbook_vendor_key
)
SELECT
  mk.openbook_vendor_key,
  COALESCE(cr.contract_rows, 0) AS contract_rows,
  COALESCE(cr.dedup_contract_rows, 0) AS dedup_contract_rows,
  COALESCE(cr.raw_award_total, 0) AS raw_award_total,
  COALESCE(wr.warrant_rows, 0) AS warrant_rows,
  COALESCE(wr.warrant_total, 0) AS warrant_total
FROM matched_keys mk
LEFT JOIN contract_rollup cr ON cr.openbook_vendor_key = mk.openbook_vendor_key
LEFT JOIN warrant_rollup wr ON wr.openbook_vendor_key = mk.openbook_vendor_key
ORDER BY raw_award_total DESC, mk.openbook_vendor_key;


WITH client_seed_map AS (
  SELECT * FROM (VALUES
    ('AMEREN ILLINOIS','AMEREN ILLINOIS','strict',1),
    ('AMEREN TRANSMISSION COMPANY OF ILLINOIS','AMEREN TRANSMISSION COMPANY OF ILLINOIS','strict',1),
    ('COMED','COMED','strict',1),
    ('COMMONWEALTH EDISON CO','COMMONWEALTH EDISON CO','strict',1),
    ('NICOR GAS','NICOR GAS','strict',1),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)','THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)','strict',1),
    ('THE PEOPLES GAS LIGHT & COKE COMPANY (PEOPLES GAS)','PEOPLES GAS','fallback',2),
    ('ILLINOIS AMERICAN WATER COMPANY','ILLINOIS AMERICAN WATER COMPANY','strict',1),
    ('ILLINOIS AMERICAN WATER COMPANY','ILLINOIS AMERICAN WATER','fallback',2),
    ('ILLINOIS AMERICAN WATER COMPANY','AMERICAN WATER','fallback_broad',3),
    ('CONSTELLATION ENERGY GENERATION, LLC','CONSTELLATION ENERGY GENERATION, LLC','strict',1),
    ('CONSTELLATION ENERGY GENERATION, LLC','CONSTELLATION ENERGY','fallback',2),
    ('CONSTELLATION ENERGY GENERATION, LLC','CONSTELLATION','fallback_broad',3)
  ) AS t(client_name, seed_text, mapping_type, mapping_level)
),
seed_matches AS (
  SELECT
    csm.client_name,
    csm.seed_text,
    csm.mapping_type,
    csm.mapping_level,
    m.openbook_vendor_key,
    m.openbook_vendor_label,
    m.match_method,
    m.confidence
  FROM client_seed_map csm
  LEFT JOIN openbook_vendor_seed s
    ON s.seed_source='manual' AND s.seed_text = csm.seed_text
  LEFT JOIN openbook_vendor_match m
    ON m.seed_id = s.seed_id
),
usable AS (
  SELECT *
  FROM seed_matches
  WHERE COALESCE(openbook_vendor_key, '') <> ''
    AND COALESCE(match_method, '') <> 'no_match'
),
dedup_keys AS (
  SELECT
    client_name,
    openbook_vendor_key,
    MIN(mapping_level) AS best_mapping_level
  FROM usable
  GROUP BY client_name, openbook_vendor_key
),
key_metrics AS (
  SELECT
    dk.client_name,
    dk.openbook_vendor_key,
    dk.best_mapping_level,
    COUNT(c.id) AS contract_rows,
    ROUND(COALESCE(SUM(c.award_amount::numeric), 0), 2) AS raw_award_total,
    COUNT(w.id) AS warrant_rows,
    ROUND(COALESCE(SUM(w.payment_amount::numeric), 0), 2) AS warrant_total
  FROM dedup_keys dk
  LEFT JOIN openbook_contracts_raw c ON c.openbook_vendor_key = dk.openbook_vendor_key
  LEFT JOIN openbook_contract_warrants w ON w.openbook_vendor_key = dk.openbook_vendor_key
  GROUP BY dk.client_name, dk.openbook_vendor_key, dk.best_mapping_level
),
strict_rollup AS (
  SELECT
    client_name,
    COUNT(*) AS vendor_keys,
    SUM(contract_rows) AS contract_rows,
    ROUND(COALESCE(SUM(raw_award_total), 0), 2) AS award_total
  FROM key_metrics
  WHERE best_mapping_level <= 1
  GROUP BY client_name
),
fallback_rollup AS (
  SELECT
    client_name,
    COUNT(*) AS vendor_keys,
    SUM(contract_rows) AS contract_rows,
    ROUND(COALESCE(SUM(raw_award_total), 0), 2) AS award_total
  FROM key_metrics
  WHERE best_mapping_level <= 2
  GROUP BY client_name
),
full_rollup AS (
  SELECT
    client_name,
    COUNT(*) AS vendor_keys,
    SUM(contract_rows) AS contract_rows,
    ROUND(COALESCE(SUM(raw_award_total), 0), 2) AS award_total
  FROM key_metrics
  WHERE best_mapping_level <= 3
  GROUP BY client_name
),
peer_set AS (
  SELECT DISTINCT client_name FROM client_seed_map
)
SELECT
  p.client_name,
  COALESCE(s.vendor_keys, 0) AS strict_vendor_keys,
  COALESCE(s.contract_rows, 0) AS strict_contract_rows,
  COALESCE(s.award_total, 0) AS strict_award_total,
  COALESCE(f.vendor_keys, 0) AS strict_plus_fallback_vendor_keys,
  COALESCE(f.contract_rows, 0) AS strict_plus_fallback_contract_rows,
  COALESCE(f.award_total, 0) AS strict_plus_fallback_award_total,
  COALESCE(a.vendor_keys, 0) AS with_broad_vendor_keys,
  COALESCE(a.contract_rows, 0) AS with_broad_contract_rows,
  COALESCE(a.award_total, 0) AS with_broad_award_total
FROM peer_set p
LEFT JOIN strict_rollup s ON s.client_name = p.client_name
LEFT JOIN fallback_rollup f ON f.client_name = p.client_name
LEFT JOIN full_rollup a ON a.client_name = p.client_name
ORDER BY p.client_name;
