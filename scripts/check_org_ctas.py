#!/usr/bin/env python3
"""Check if the org->donor CTAS in _match_org_addresses_pg uses parallel workers."""
import os, sys, time
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

# Build temp org addresses (same as _match_org_addresses_pg)
conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
conn.execute("""
    CREATE TEMP TABLE _tmp_527_org_addrs (
        ein TEXT, org_name TEXT, addr_type TEXT,
        city TEXT, state TEXT, zip TEXT,
        norm_state TEXT, norm_zip5 TEXT
    )
""")
for at, cc, sc, zc in [("org","city","state","zip"),
                        ("custodian","custodian_city","custodian_state","custodian_zip"),
                        ("contact","contact_city","contact_state","contact_zip"),
                        ("business","business_city","business_state","business_zip")]:
    conn.execute(f"""INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, '{at}', {cc}, {sc}, {zc},
               UPPER(TRIM({sc})), SUBSTRING(TRIM({zc}) FROM 1 FOR 5)
        FROM irs527_organizations WHERE {sc} IS NOT NULL AND {zc} IS NOT NULL AND TRIM({zc}) != ''""")
conn.execute("CREATE INDEX IF NOT EXISTS _idx_tmp_org_sz ON _tmp_527_org_addrs(norm_state, norm_zip5)")
conn.commit()

# Apply same settings as in the code
_set_phase2_parallel_plan(conn)

# Verify settings
for p in ['enable_nestloop', 'enable_indexscan', 'max_parallel_workers_per_gather']:
    row = conn.execute(f'SHOW {p}').fetchone()
    print(f"{p} = {list(row.values())[0]}")

mv = _DONOR_ADDRESS_MV

# Check EXPLAIN for the CTAS query
threshold = 0.20
rows = conn.execute(f"""EXPLAIN (FORMAT TEXT)
    SELECT DISTINCT ON (o.ein, o.addr_type, m.donor_key)
        o.ein, o.org_name, o.addr_type,
        m.donor_key, m.donor_name, m.donor_zip5, m.norm_state, m.norm_city,
        CASE WHEN m.donor_zip5 = o.norm_zip5 AND m.norm_city = LOWER(TRIM(o.city)) THEN 1.0
             WHEN m.donor_zip5 = o.norm_zip5 THEN 0.7 ELSE 0.5 END AS address_score
    FROM _tmp_527_org_addrs o
    JOIN {mv} m ON m.norm_state = o.norm_state AND m.donor_zip5 = o.norm_zip5
    WHERE m.donor_zip5 IS NOT NULL
        AND CASE
            WHEN {threshold} > 0 THEN similarity(LOWER(o.org_name), LOWER(m.donor_name)) >= {threshold}
            ELSE TRUE
        END
    ORDER BY o.ein, o.addr_type, m.donor_key
""").fetchall()
for row in rows:
    print(list(row.values())[0])

# Now time the actual CTAS
print("\n--- Timing CTAS ---")
conn.execute("DROP TABLE IF EXISTS _tmp_org_donor_matches")
t0 = time.perf_counter()
conn.execute(f"""
    CREATE TEMP TABLE _tmp_org_donor_matches AS
    SELECT DISTINCT ON (o.ein, o.addr_type, m.donor_key)
        o.ein, o.org_name, o.addr_type,
        LOWER(TRIM(o.city)) AS org_city,
        UPPER(TRIM(o.state)) AS org_state,
        SUBSTRING(TRIM(o.zip) FROM 1 FOR 5) AS org_zip5,
        'donor'::TEXT AS matched_entity_type,
        m.donor_key AS matched_entity_id,
        m.donor_name AS matched_entity_name,
        m.norm_city AS matched_city,
        m.norm_state AS matched_state,
        m.donor_zip5 AS matched_zip5,
        CASE
            WHEN m.donor_zip5 = o.norm_zip5 AND m.norm_city = LOWER(TRIM(o.city))
            THEN 1.0 WHEN m.donor_zip5 = o.norm_zip5 THEN 0.7 ELSE 0.5
        END AS address_score
    FROM _tmp_527_org_addrs o
    JOIN {mv} m ON m.norm_state = o.norm_state AND m.donor_zip5 = o.norm_zip5
    WHERE m.donor_zip5 IS NOT NULL
        AND similarity(LOWER(o.org_name), LOWER(m.donor_name)) >= {threshold}
    ORDER BY o.ein, o.addr_type, m.donor_key
""")
t1 = time.perf_counter()
cnt = conn.execute("SELECT COUNT(*) AS c FROM _tmp_org_donor_matches").fetchone()
print(f"CTAS time: {t1-t0:.1f}s, rows: {cnt['c']}")

conn.execute("DROP TABLE IF EXISTS _tmp_org_donor_matches")
conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
conn.commit()
conn.close()
