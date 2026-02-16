#!/usr/bin/env python3
"""Quick migration of missing tables from SQLite to local Postgres."""
import sqlite3
import psycopg
from psycopg.rows import dict_row

TABLES = [
    "irs527_organizations", "irs527_directors", "irs527_expenditures",
    "irs527_director_address_matches", "irs527_org_address_matches",
    "irs527_director_donor_matches", "irs527_director_candidate_matches",
    "irs527_committee_matches", "irs527_expenditure_recipient_matches",
    "lobbying_clients", "lobbying_entities", "lobbying_entity_clients",
    "lobbying_donor_matches", "lobbying_expenditure_matches",
    "lobbying_527_matches", "cross_matching_donor_address_index",
    "cross_matching_incremental_state",
    "federal_candidates",
]

TYPE_MAP = {
    "INTEGER": "BIGINT",
    "TEXT": "TEXT",
    "REAL": "DOUBLE PRECISION",
    "BLOB": "BYTEA",
}

sq = sqlite3.connect("data/campaign_finance.db")
sq.row_factory = sqlite3.Row

pg = psycopg.connect("postgresql://devin@localhost/ilcf", row_factory=dict_row, autocommit=False)

for table in TABLES:
    check = sq.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not check:
        print(f"SKIP {table} (not in SQLite)")
        continue

    count = sq.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    cols = sq.execute(f"PRAGMA table_info([{table}])").fetchall()
    col_names = [c["name"] for c in cols]

    pg_check = pg.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=%s", (table,)
    ).fetchone()
    if not pg_check:
        col_defs = []
        for c in cols:
            pg_type = TYPE_MAP.get((c["type"] or "TEXT").upper(), "TEXT")
            col_defs.append(f'"{c["name"]}" {pg_type}')
        create_sql = f'CREATE TABLE "{table}" ({", ".join(col_defs)})'
        pg.execute(create_sql)
        pg.commit()
        print(f"  Created table {table}")
    else:
        pg.execute(f'TRUNCATE TABLE "{table}"')
        pg.commit()

    if count == 0:
        print(f"{table}: 0 rows")
        continue

    placeholders = ", ".join(["%s"] * len(col_names))
    quoted_cols = ", ".join(f'"{c}"' for c in col_names)
    insert_sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'

    cursor = sq.execute(f"SELECT * FROM [{table}]")
    loaded = 0
    batch = []
    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        batch = [tuple(row) for row in rows]
        with pg.cursor() as cur:
            cur.executemany(insert_sql, batch)
        pg.commit()
        loaded += len(batch)
        if loaded % 50000 == 0:
            print(f"  {table}: {loaded}/{count}...")

    print(f"{table}: {loaded} rows migrated")

pg.close()
sq.close()
print("Done")
