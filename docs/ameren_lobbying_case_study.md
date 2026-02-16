# Ameren Lobbying Client Relationships (Reproducible Case Study)

## Dataset Context (As Observed 2026-02-16)
- Present lobbying tables in current local DB snapshot:
  - `lobbying_clients`
  - `lobbying_entities`
  - `lobbying_entity_clients`
  - `lobbying_donor_matches`
  - `lobbying_expenditure_matches`
  - `lobbying_527_matches`
- Not present in current local snapshot:
  - `lobbying_lobbyists`
  - `lobbying_lobbyist_registrations`

Because lobbyist-level tables are missing in this snapshot, the exact lobbyist-count query is provided below but cannot run here until those tables are loaded.

## Relationship Tables to Use
- Core client/entity registry: `lobbying_clients`, `lobbying_entities`, `lobbying_entity_clients`
- Lobbying -> campaign finance bridge: `lobbying_donor_matches`, `lobbying_expenditure_matches`
- Lobbying -> 527 bridge: `lobbying_527_matches`
- Lobbyist-level (when loaded): `lobbying_lobbyists`, `lobbying_lobbyist_registrations`

## Query 1: How Many Lobbyists Have Ameren as Client?

### Preflight Check
```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN ('lobbying_lobbyists', 'lobbying_lobbyist_registrations');
```

### Main Query (runs only when lobbyist tables exist)
```sql
SELECT
  c.client_id,
  c.client_name,
  COUNT(DISTINCT lr.lobbyist_id) AS lobbyist_count
FROM lobbying_clients c
JOIN lobbying_lobbyist_registrations lr
  ON lr.client_id = c.client_id
WHERE c.client_name ILIKE '%ameren%'
GROUP BY c.client_id, c.client_name
ORDER BY lobbyist_count DESC, c.client_name;
```

### Current Snapshot Proxy (entity-level, because lobbyist tables are missing)
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

Observed now:
- `AMEREN ILLINOIS` (`client_id=3821`): `22` entities (`80` pair rows across years)
- `AMEREN TRANSMISSION COMPANY OF ILLINOIS` (`client_id=6710`): `1` entity

## Query 2: Top Clients by Number of Lobbying Entities

```sql
SELECT
  c.client_id,
  c.client_name,
  COUNT(DISTINCT ec.entity_id) AS lobbying_entity_count
FROM lobbying_clients c
JOIN lobbying_entity_clients ec
  ON ec.client_id = c.client_id
GROUP BY c.client_id, c.client_name
ORDER BY lobbying_entity_count DESC, c.client_name
LIMIT 20;
```

Top of current output:
1. `AMEREN ILLINOIS` (22)
2. `ILLINOIS BROADBAND & CABLE ASSOCIATION` (20)
3. `THOMSON WEIR LLC` (20)
4. `HEALTH CARE COUNCIL OF ILLINOIS` (18)
5. `SPORTS BETTING ALLIANCE` (17)

## Query 3: Directional vs Implied Bi-Directional Relationships

### Directed Pair Count and Reverse-Pair Check
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

Observed now:
- `directed_pair_count = 4913`
- `has_reverse_pair_count = 100` (~2.0%)

Interpretation:
- The table is fundamentally **directional** (`entity -> client`).
- A minority of relationships have reverse counterparts because organizations can appear in both roles in separate rows.
- Do not assume every relation is bi-directional; treat reverse edges as separate observations.

## Ameren Resolution Issues and Normalization Plan

### Observed Issue
Name variants fragment totals across both lobbying and donor systems.

Example checks:
```sql
-- Lobbying client variants
SELECT client_id, client_name
FROM lobbying_clients
WHERE client_name ILIKE '%ameren%'
ORDER BY client_name;

-- Donor variants in state summary
SELECT COUNT(*) AS raw_variant_rows,
       COUNT(DISTINCT donor_key) AS raw_variant_keys,
       SUM(total_amount) AS raw_total_amount
FROM analytics_donor_summary
WHERE source='bulk_receipts'
  AND donor_name ILIKE '%ameren%';
```

Observed now in `analytics_donor_summary`:
- `264` donor keys with `donor_name ILIKE '%ameren%'`
- total amount across those keys: `$8,057,465.60`

### Proposed Normalization
1. Create a canonical alias table for organizations:
   - `entity_alias_canonical(alias_text, canonical_name, source_domain, confidence, updated_at)`
2. Normalize text for matching:
   - uppercase, punctuation stripping, whitespace collapse, suffix cleanup (`INC`, `LLC`, `PAC`, etc.)
3. Add deterministic rules first:
   - `AMEREN ILLINOIS`, `AMEREN ILLINOIS PAC`, `AMEREN TRANSMISSION COMPANY OF ILLINOIS`
4. Keep manual override workflow for high-dollar ambiguous rows.
5. Store both raw and canonical keys in downstream aggregates to preserve auditability.

## Optional Ameren Bridge Query (Current Snapshot)
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

Observed now:
- `AMEREN ILLINOIS`: 22 entities, 45 matched donors, 90 matched payee edges, 0 matched 527 orgs
- `AMEREN TRANSMISSION COMPANY OF ILLINOIS`: 1 entity, 0 donor matches
