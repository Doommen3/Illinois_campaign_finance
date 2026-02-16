#!/usr/bin/env python3
"""Newsroom data pipeline: runs SQL queries against campaign_finance.db and exports CSVs + JSON."""

import csv
import json
import os
import sqlite3
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'campaign_finance.db')
OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'output', 'newsroom')
CSV_DIR = os.path.join(OUT_DIR, 'supporting_tables')
JSON_PATH = os.path.join(OUT_DIR, 'query_results.json')

# ---------------------------------------------------------------------------
# SQL Queries — keyed by analysis slug
# ---------------------------------------------------------------------------
QUERIES = {

"meta_coverage": """
SELECT 'bulk_receipts_2025' as metric, COUNT(*) as cnt, printf('%.2f', SUM(amount)) as total
FROM bulk_receipts_clean WHERE substr(received_date,1,4)='2025'
UNION ALL SELECT 'bulk_receipts_2024', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_receipts_clean WHERE substr(received_date,1,4)='2024'
UNION ALL SELECT 'bulk_receipts_2023', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_receipts_clean WHERE substr(received_date,1,4)='2023'
UNION ALL SELECT 'bulk_receipts_2026', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_receipts_clean WHERE substr(received_date,1,4)='2026'
UNION ALL SELECT 'bulk_expenditures_2025', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_expenditures_clean WHERE substr(expended_date,1,4)='2025' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
UNION ALL SELECT 'bulk_expenditures_2024', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_expenditures_clean WHERE substr(expended_date,1,4)='2024' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
UNION ALL SELECT 'bulk_expenditures_2023', COUNT(*), printf('%.2f', SUM(amount))
FROM bulk_expenditures_clean WHERE substr(expended_date,1,4)='2023' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
UNION ALL SELECT 'fec_ie_total', COUNT(*), printf('%.2f', SUM(expenditure_amount))
FROM fec_schedule_e_independent_expenditures WHERE cycle=2026
""",

"war_chest": """
SELECT candidate_full_name, committee_name, committee_type, office_sought,
  printf('%.2f', sum_total_receipts) as receipts,
  printf('%.2f', sum_total_expenditures) as expenditures,
  printf('%.2f', max_ending_funds_available) as ending_funds,
  CASE WHEN sum_total_expenditures > 0
    THEN ROUND(sum_total_receipts / sum_total_expenditures, 2) ELSE 999999 END as ratio
FROM bulk_candidate_committee_finance_agg
WHERE period_year = 2025 AND sum_total_receipts > 500000
ORDER BY ratio DESC LIMIT 30
""",

"top_donors_2025": """
SELECT last_or_business_name, first_name,
  COUNT(*) as txns, printf('%.2f', SUM(amount)) as total
FROM bulk_receipts_clean
WHERE substr(received_date,1,4)='2025' AND amount > 0
GROUP BY last_or_business_name, first_name
ORDER BY SUM(amount) DESC LIMIT 30
""",

"top_donors_excl_actblue": """
SELECT last_or_business_name, first_name,
  COUNT(*) as txns, printf('%.2f', SUM(amount)) as total
FROM bulk_receipts_clean
WHERE substr(received_date,1,4)='2025' AND amount > 0
  AND last_or_business_name NOT LIKE '%ActBlue%'
GROUP BY last_or_business_name, first_name
ORDER BY SUM(amount) DESC LIMIT 30
""",

"single_donor_dominance": """
SELECT dca.committee_name, dca.donor_name,
  printf('%.2f', dca.total_amount) as donor_total,
  dca.contribution_count,
  printf('%.2f', cmttotal.total) as committee_total,
  ROUND(100.0 * dca.total_amount / cmttotal.total, 1) as pct_of_total
FROM analytics_donor_committee_agg dca
JOIN (
  SELECT committee_name, SUM(total_amount) as total
  FROM analytics_donor_committee_agg
  WHERE source='bulk_receipts'
  GROUP BY committee_name HAVING SUM(total_amount) > 500000
) cmttotal ON dca.committee_name = cmttotal.committee_name
WHERE dca.source='bulk_receipts'
  AND dca.total_amount / cmttotal.total > 0.75
  AND cmttotal.total > 500000
ORDER BY dca.total_amount DESC LIMIT 25
""",

"committee_transfers_2025": """
SELECT payee_last_or_business_name as recipient, COUNT(*) as txns,
  printf('%.2f', SUM(amount)) as total,
  COUNT(DISTINCT committee_id_sbe) as paying_committees
FROM bulk_expenditures_clean
WHERE substr(expended_date,1,4)='2025'
  AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
  AND (purpose LIKE '%Contribution%' OR purpose LIKE '%contribution%' OR purpose LIKE '%CONTRIBUTION%')
GROUP BY payee_last_or_business_name
ORDER BY SUM(amount) DESC LIMIT 30
""",

"top_vendors_2025": """
SELECT payee_last_or_business_name as vendor, COUNT(*) as txns,
  printf('%.2f', SUM(amount)) as total,
  COUNT(DISTINCT committee_id_sbe) as num_committees
FROM bulk_expenditures_clean
WHERE substr(expended_date,1,4)='2025'
  AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
  AND purpose NOT LIKE '%ontribution%'
GROUP BY payee_last_or_business_name
ORDER BY SUM(amount) DESC LIMIT 30
""",

"vendor_growth_yoy": """
WITH v2024 AS (
  SELECT payee_last_or_business_name as vendor, SUM(amount) as total_2024
  FROM bulk_expenditures_clean
  WHERE substr(expended_date,1,4)='2024' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
    AND purpose NOT LIKE '%ontribution%'
  GROUP BY payee_last_or_business_name HAVING SUM(amount) > 50000
),
v2025 AS (
  SELECT payee_last_or_business_name as vendor, SUM(amount) as total_2025
  FROM bulk_expenditures_clean
  WHERE substr(expended_date,1,4)='2025' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
    AND purpose NOT LIKE '%ontribution%'
  GROUP BY payee_last_or_business_name HAVING SUM(amount) > 50000
)
SELECT v2025.vendor,
  printf('%.2f', v2024.total_2024) as t2024,
  printf('%.2f', v2025.total_2025) as t2025,
  ROUND(v2025.total_2025 / v2024.total_2024, 2) as growth_ratio
FROM v2025 JOIN v2024 ON v2025.vendor = v2024.vendor
WHERE v2024.total_2024 > 0
ORDER BY growth_ratio DESC LIMIT 20
""",

"shared_vendors": """
SELECT payee_last_or_business_name as vendor,
  COUNT(DISTINCT committee_id_sbe) as num_committees,
  printf('%.2f', SUM(amount)) as total
FROM bulk_expenditures_clean
WHERE substr(expended_date,1,4)='2025' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
  AND purpose NOT LIKE '%ontribution%'
GROUP BY payee_last_or_business_name
HAVING COUNT(DISTINCT committee_id_sbe) >= 10
ORDER BY COUNT(DISTINCT committee_id_sbe) DESC LIMIT 25
""",

"fec_ie_by_candidate": """
SELECT candidate_name, candidate_id, candidate_office_district,
  printf('%.2f', SUM(CASE WHEN support_oppose_indicator='O' THEN expenditure_amount ELSE 0 END)) as oppose_total,
  printf('%.2f', SUM(CASE WHEN support_oppose_indicator='S' THEN expenditure_amount ELSE 0 END)) as support_total,
  printf('%.2f', SUM(expenditure_amount)) as total_ie,
  COUNT(*) as txns
FROM fec_schedule_e_independent_expenditures WHERE cycle=2026
GROUP BY candidate_id ORDER BY SUM(expenditure_amount) DESC LIMIT 20
""",

"fec_ie_by_spender": """
SELECT committee_name, committee_id,
  printf('%.2f', SUM(expenditure_amount)) as total,
  COUNT(DISTINCT candidate_id) as candidates_targeted,
  COUNT(*) as txns
FROM fec_schedule_e_independent_expenditures WHERE cycle=2026
GROUP BY committee_id ORDER BY SUM(expenditure_amount) DESC LIMIT 15
""",

"fec_ie_support_oppose": """
SELECT support_oppose_indicator, COUNT(*) as txns,
  printf('%.2f', SUM(expenditure_amount)) as total
FROM fec_schedule_e_independent_expenditures WHERE cycle=2026
GROUP BY support_oppose_indicator
""",

"fec_senate_race": """
SELECT cm.candidate_name, cm.fec_candidate_id, cm.party,
  printf('%.2f', ct.receipts) as receipts,
  printf('%.2f', ct.individual_contributions) as indiv_contributions,
  printf('%.2f', ct.disbursements) as disbursements,
  printf('%.2f', COALESCE(ct.last_cash_on_hand_end_period,0)) as cash_on_hand
FROM fec_candidate_match cm
JOIN fec_candidate_cycle_totals ct ON cm.fec_candidate_id = ct.candidate_id
WHERE ct.cycle=2026 AND cm.office_code='S' AND cm.match_status='matched'
ORDER BY ct.receipts DESC
""",

"fec_il09_primary": """
SELECT cm.candidate_name, cm.fec_candidate_id, cm.party,
  printf('%.2f', ct.receipts) as receipts,
  printf('%.2f', ct.individual_contributions) as indiv_contributions,
  printf('%.2f', ct.disbursements) as disbursements,
  printf('%.2f', COALESCE(ct.last_cash_on_hand_end_period,0)) as cash_on_hand
FROM fec_candidate_match cm
JOIN fec_candidate_cycle_totals ct ON cm.fec_candidate_id = ct.candidate_id
WHERE ct.cycle=2026 AND cm.district_code='09' AND cm.office_code='H' AND cm.match_status='matched'
ORDER BY ct.receipts DESC
""",

"lobbying_donor_overlap": """
SELECT ldm.client_name, ldm.donor_name, ldm.score,
  ads.total_amount as donor_total, ads.contribution_count
FROM lobbying_donor_matches ldm
LEFT JOIN analytics_donor_summary ads ON ldm.donor_key = ads.donor_key AND ads.source='bulk_receipts'
WHERE ads.total_amount > 100000
ORDER BY ads.total_amount DESC LIMIT 20
""",

"lobbying_527_triple": """
SELECT client_name, org_name, ein, score
FROM lobbying_527_matches ORDER BY score DESC
""",

"irs527_top_il_spenders": """
SELECT o.org_name, o.ein, o.city,
  COUNT(e.rowid_local) as exp_count,
  printf('%.2f', SUM(e.amount)) as total_spent
FROM irs527_organizations o
JOIN irs527_expenditures e ON o.ein = e.ein
WHERE o.state = 'IL' AND substr(e.date,1,4) IN ('2024','2025')
GROUP BY o.ein ORDER BY SUM(e.amount) DESC LIMIT 20
""",

"dual_system_donors": """
SELECT federal_donor_name, local_donor_name,
  printf('%.2f', federal_total_amount) as fed_total,
  printf('%.2f', local_total_amount) as local_total,
  printf('%.2f', federal_total_amount + local_total_amount) as combined,
  match_method, confidence_score
FROM fec_local_donor_matches
WHERE confidence_score >= 0.90
ORDER BY (federal_total_amount + local_total_amount) DESC LIMIT 25
""",

"small_dollar_trend": """
SELECT substr(received_date,1,4) as yr,
  SUM(CASE WHEN amount < 150 THEN 1 ELSE 0 END) as under_150_count,
  SUM(CASE WHEN amount >= 150 AND amount < 1000 THEN 1 ELSE 0 END) as mid_count,
  SUM(CASE WHEN amount >= 1000 AND amount < 50000 THEN 1 ELSE 0 END) as large_count,
  SUM(CASE WHEN amount >= 50000 THEN 1 ELSE 0 END) as mega_count,
  printf('%.2f', SUM(CASE WHEN amount >= 50000 THEN amount ELSE 0 END)) as mega_total
FROM bulk_receipts_clean
WHERE substr(received_date,1,4) IN ('2023','2024','2025') AND amount > 0
GROUP BY yr
""",

"governor_race_2025": """
SELECT candidate_full_name, committee_name,
  printf('%.2f', sum_total_receipts) as receipts,
  printf('%.2f', sum_total_expenditures) as expenditures,
  printf('%.2f', max_ending_funds_available) as ending_funds
FROM bulk_candidate_committee_finance_agg
WHERE office_sought = 'Governor' AND period_year = 2025
ORDER BY sum_total_receipts DESC
""",

"pure_stockpile_committees": """
SELECT c.committee_name, c.committee_type,
  printf('%.2f', SUM(r.amount)) as total_receipts
FROM bulk_receipts_clean r
JOIN bulk_committees_clean c ON r.committee_id_sbe = c.committee_id_sbe
WHERE substr(r.received_date,1,4)='2025'
  AND c.committee_id_sbe NOT IN (
    SELECT DISTINCT committee_id_sbe FROM bulk_expenditures_clean
    WHERE substr(expended_date,1,4)='2025' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
  )
GROUP BY c.committee_id_sbe
HAVING SUM(r.amount) > 100000
ORDER BY SUM(r.amount) DESC LIMIT 15
""",

"expenditure_purpose_breakdown": """
SELECT
  CASE
    WHEN purpose LIKE '%ontribution%' THEN 'Contribution/Transfer'
    WHEN purpose LIKE '%Advertis%' OR purpose LIKE '%Media%' OR purpose LIKE '%media%' OR purpose LIKE '%TV%' THEN 'Media/Advertising'
    WHEN purpose LIKE '%Consult%' OR purpose LIKE '%consult%' THEN 'Consulting'
    WHEN purpose LIKE '%Salary%' OR purpose LIKE '%Payroll%' OR purpose LIKE '%payroll%' THEN 'Payroll/Salary'
    WHEN purpose LIKE '%Fundrais%' OR purpose LIKE '%fundrais%' THEN 'Fundraising'
    WHEN purpose LIKE '%Rent%' OR purpose LIKE '%rent%' THEN 'Rent'
    WHEN purpose LIKE '%Legal%' OR purpose LIKE '%legal%' THEN 'Legal'
    WHEN purpose LIKE '%Travel%' OR purpose LIKE '%travel%' OR purpose LIKE '%Hotel%' THEN 'Travel'
    WHEN purpose LIKE '%Print%' OR purpose LIKE '%print%' OR purpose LIKE '%Mail%' OR purpose LIKE '%mail%' THEN 'Print/Mail'
    ELSE 'Other'
  END as category,
  COUNT(*) as txns,
  printf('%.2f', SUM(amount)) as total
FROM bulk_expenditures_clean
WHERE substr(expended_date,1,4)='2025' AND (is_amount_anomalous=0 OR is_amount_anomalous IS NULL)
GROUP BY category ORDER BY SUM(amount) DESC
""",

"competitive_house_primaries": """
SELECT cm.district_code, COUNT(*) as candidates,
  printf('%.2f', SUM(ct.receipts)) as combined_receipts
FROM fec_candidate_match cm
JOIN fec_candidate_cycle_totals ct ON cm.fec_candidate_id = ct.candidate_id
WHERE ct.cycle=2026 AND cm.office_code='H' AND cm.match_status='matched' AND ct.receipts > 100000
GROUP BY cm.district_code HAVING COUNT(*) >= 3
ORDER BY SUM(ct.receipts) DESC
""",

"kankakee_verification": """
SELECT committee_id_sbe, last_or_business_name, first_name,
  COUNT(*) as txns, printf('%.2f', SUM(amount)) as total
FROM bulk_receipts_clean
WHERE committee_id_sbe = 325 AND substr(received_date,1,4) = '2025'
GROUP BY last_or_business_name, first_name
ORDER BY SUM(amount) DESC LIMIT 10
""",

"director_donor_overlap": """
SELECT ddm.org_name, ddm.director_name, ddm.donor_name, ddm.score,
  ads.total_amount as donor_total
FROM irs527_director_donor_matches ddm
LEFT JOIN analytics_donor_summary ads ON ddm.donor_key = ads.donor_key AND ads.source='bulk_receipts'
WHERE ads.total_amount > 100000
ORDER BY ads.total_amount DESC LIMIT 15
""",

}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_queries(db_path=None):
    """Execute all queries, return {slug: {columns: [...], rows: [...]}}."""
    db = db_path or DB_PATH
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    results = {}
    for slug, sql in QUERIES.items():
        print(f'  Running: {slug} ...', end=' ', flush=True)
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        results[slug] = {'columns': cols, 'rows': rows}
        print(f'{len(rows)} rows')
    conn.close()
    return results


def export_csvs(results, csv_dir=None):
    """Write each result set to a CSV."""
    d = csv_dir or CSV_DIR
    os.makedirs(d, exist_ok=True)
    paths = {}
    for slug, data in results.items():
        path = os.path.join(d, f'{slug}.csv')
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(data['columns'])
            w.writerows(data['rows'])
        paths[slug] = path
    return paths


def export_json(results, json_path=None):
    """Write combined results to JSON for the renderer."""
    p = json_path or JSON_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    return p


if __name__ == '__main__':
    print('Running newsroom queries...')
    results = run_all_queries()
    csv_paths = export_csvs(results)
    json_path = export_json(results)
    print(f'\nExported {len(csv_paths)} CSVs to {CSV_DIR}/')
    print(f'JSON results: {json_path}')
