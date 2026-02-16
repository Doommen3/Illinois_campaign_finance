#!/usr/bin/env python3
"""Check EXPLAIN for org->donor CTAS query."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://devin@localhost/ilcf")

from database.connection import get_db
from database.cross_matching import (
    _apply_postgres_session_tuning, _set_phase2_parallel_plan,
    _ensure_pg_trgm, _ensure_donor_address_mv, _DONOR_ADDRESS_MV,
)

conn = get_db(None)
_apply_postgres_session_tuning(conn)
_ensure_pg_trgm(conn)
_ensure_donor_address_mv(conn)

# Build the temp org addresses table (same as in _match_org_addresses_pg)
conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
conn.execute("""
    CREATE TEMP TABLE _tmp_527_org_addrs (
        ein TEXT, org_name TEXT, addr_type TEXT,
        city TEXT, state TEXT, zip TEXT,
        norm_state TEXT, norm_zip5 TEXT
    )
""")
for addr_type, city_col, state_col, zip_col in [
    ("org", "city", "state", "zip"),
    ("custodian", "custodian_city", "custodian_state", "custodian_zip"),
    ("contact", "contact_city", "contact_state", "contact_zip"),
    ("business", "business_city", "business_state", "business_zip"),
]:
    conn.execute(f"""
        INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, '{addr_type}', {city_col}, {state_col}, {zip_col},
               UPPER(TRIM({state_col})), SUBSTRING(TRIM({zip_col}) FROM 1 FOR 5)
        FROM irs527_organizations
        WHERE {state_col} IS NOT NULL
            AND {zip_col} IS NOT NULL AND TRIM({zip_col}) != ''
    """)
conn.execute("CREATE INDEX IF NOT EXISTS _idx_tmp_org_sz ON _tmp_527_org_addrs(norm_state, norm_zip5)")
conn.commit()

org_count = conn.execute("SELECT COUNT(*) AS c FROM _tmp_527_org_addrs").fetchone()
print(f"Org addresses: {org_count['c']}")

_set_phase2_parallel_plan(conn)

for p in ['enable_nestloop', 'max_parallel_workers_per_gather']:
    row = conn.execute(f'SHOW {p}').fetchone()
    print(f"{p} = {list(row.values())[0]}")

mv = _DONOR_ADDRESS_MV
threshold = 0.20

rows = conn.execute(f"""EXPLAIN (FORMAT TEXT)
    SELECT DISTINCT ON (o.ein, o.addr_type, m.donor_key)
        o.ein, o.org_name, o.addr_type,
        LOWER(TRIM(o.city)) AS org_city,
        m.donor_key, m.donor_name,
        m.norm_city, m.norm_state, m.donor_zip5,
        CASE WHEN m.donor_zip5 = o.norm_zip5 AND m.norm_city = LOWER(TRIM(o.city)) THEN 1.0
             WHEN m.donor_zip5 = o.norm_zip5 THEN 0.7 ELSE 0.5 END AS address_score
    FROM _tmp_527_org_addrs o
    JOIN {mv} m ON m.norm_state = o.norm_state AND m.donor_zip5 = o.norm_zip5
    WHERE m.donor_zip5 IS NOT NULL
        AND similarity(LOWER(o.org_name), LOWER(m.donor_name)) >= {threshold}
    ORDER BY o.ein, o.addr_type, m.donor_key
""").fetchall()

for row in rows:
    print(list(row.values())[0])

conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
conn.commit()
conn.close()
