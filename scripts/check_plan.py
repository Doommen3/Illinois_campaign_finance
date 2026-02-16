#!/usr/bin/env python3
"""Check if parallel merge join is used for director->donor matching."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://devin@localhost/ilcf")

from database.connection import get_db
from database.cross_matching import (
    _apply_postgres_session_tuning,
    _set_phase2_parallel_plan,
    _ensure_pg_trgm,
    _ensure_donor_address_mv,
    _DONOR_ADDRESS_MV,
)

conn = get_db(None)
_apply_postgres_session_tuning(conn)
_ensure_pg_trgm(conn)
_ensure_donor_address_mv(conn)
_set_phase2_parallel_plan(conn)

# Verify settings
for p in ['enable_nestloop', 'max_parallel_workers_per_gather']:
    row = conn.execute(f'SHOW {p}').fetchone()
    print(f"{p} = {list(row.values())[0]}")

# Check plan
mv = _DONOR_ADDRESS_MV
sql = f"""EXPLAIN (ANALYZE, FORMAT TEXT)
    INSERT INTO irs527_director_address_matches
        (ein, org_name, director_name,
         director_city, director_state, director_zip5,
         donor_key, donor_name,
         donor_city, donor_state, donor_zip5,
         address_score, name_score)
    SELECT DISTINCT ON (d.ein, d.person_name, m.donor_key)
        d.ein,
        d.org_name,
        d.person_name,
        LOWER(TRIM(d.city)),
        UPPER(TRIM(d.state)),
        SUBSTRING(TRIM(d.zip) FROM 1 FOR 5),
        m.donor_key,
        m.donor_name,
        m.norm_city,
        m.norm_state,
        m.donor_zip5,
        CASE
            WHEN m.donor_zip5 = SUBSTRING(TRIM(d.zip) FROM 1 FOR 5)
                 AND m.norm_city = LOWER(TRIM(d.city))
            THEN 1.0
            WHEN m.donor_zip5 = SUBSTRING(TRIM(d.zip) FROM 1 FOR 5)
            THEN 0.7
            ELSE 0.2
        END,
        similarity(LOWER(d.person_name), LOWER(m.donor_name))
    FROM irs527_directors d
    JOIN {mv} m
        ON m.norm_state = UPPER(TRIM(d.state))
        AND m.donor_zip5 = SUBSTRING(TRIM(d.zip) FROM 1 FOR 5)
    WHERE d.person_name IS NOT NULL
        AND d.state IS NOT NULL
        AND d.zip IS NOT NULL AND TRIM(d.zip) != ''
        AND m.donor_zip5 IS NOT NULL
        AND similarity(LOWER(d.person_name), LOWER(m.donor_name)) >= 0.30
    ORDER BY d.ein, d.person_name, m.donor_key,
             similarity(LOWER(d.person_name), LOWER(m.donor_name)) DESC
"""
# Clear the table first
conn.execute("DELETE FROM irs527_director_address_matches")
conn.commit()
rows = conn.execute(sql).fetchall()
for row in rows:
    print(list(row.values())[0])

conn.close()
