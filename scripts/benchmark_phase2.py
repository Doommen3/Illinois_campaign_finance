#!/usr/bin/env python3
"""Benchmark Phase 2 cross-matching on local Postgres."""
import time
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# Ensure DATABASE_URL is set
os.environ.setdefault("DATABASE_URL", "postgresql://devin@localhost/ilcf")

from database.connection import get_db
from database.cross_matching import (
    match_527_directors_to_donors_by_address,
    match_527_org_addresses,
    _is_postgres_connection,
    _apply_postgres_session_tuning,
    _ORG_DONOR_NAME_PREFILTER_THRESHOLD,
)

db_path = None
conn = get_db(db_path)
print(f"Connection type: {conn.__class__.__name__}")
print(f"Is Postgres: {_is_postgres_connection(conn)}")
_apply_postgres_session_tuning(conn)

# Benchmark director -> donor address matching
print()
print("=== Director -> Donor Address Matching ===")
t0 = time.perf_counter()
result = match_527_directors_to_donors_by_address(conn)
t1 = time.perf_counter()
print(f"Result: {result}")
print(f"Elapsed: {t1 - t0:.2f}s")

# Benchmark org -> address matching
print()
print("=== Org -> Address Matching ===")
t0 = time.perf_counter()
result = match_527_org_addresses(
    conn, org_donor_name_threshold=_ORG_DONOR_NAME_PREFILTER_THRESHOLD
)
t1 = time.perf_counter()
print(f"Result: {result}")
print(f"Elapsed: {t1 - t0:.2f}s")

conn.close()
print()
print("Done!")
