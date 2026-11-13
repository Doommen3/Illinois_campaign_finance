"""Recipient directory: who committees paid, for the Recipients section of /search.

Two materialized views over isbe_condensed_expenditures (plan .claude/plans/recipient-search.md, decisions R2/R4/R8):

  isbe_recipient_summary     one row per recipient key: most common filed spelling, total, payments, committees, first/last date
  isbe_recipient_committees  one row per recipient key x committee: the pair's most common spelling (the name that committee's
                             all-time payee page shows), total, payments, first/last date

Identity and predicate are the committee payee pages' (webapp/routes/committees.py _payee_rollup_source): the name-only key,
not archived, amount > 0, every D-2 part. No address column is carried, so privacy removal requests need no scrub step here.
Built by the ISBE ETL after the condensed views and by scripts/build_recipient_directory.py. The caller commits.
"""
from __future__ import annotations

RECIPIENT_SUMMARY = "isbe_recipient_summary"
RECIPIENT_COMMITTEES = "isbe_recipient_committees"


def recipient_name_sql(alias: str = "e") -> str:
    """The filed name: first and last trimmed, joined by one space only when both are non-blank (twin of _PAYEE_NAME_SQL)."""
    first = f"COALESCE(NULLIF(TRIM({alias}.first_name), ''), '')"
    last = f"COALESCE(NULLIF(TRIM({alias}.last_name), ''), '')"
    return f"TRIM({first} || CASE WHEN {first} != '' AND {last} != '' THEN ' ' ELSE '' END || {last})"


def recipient_key_sql(name_sql: str) -> str:
    """The payee_key of the committee payee pages: lower case, runs of non-alphanumerics collapsed to one space, trimmed."""
    return f"TRIM(REGEXP_REPLACE(LOWER({name_sql}), '[^[:alnum:]]+', ' ', 'g'))"


_NAME = recipient_name_sql("e")
_ROWS = f"""
    SELECT {recipient_key_sql(_NAME)} COLLATE "C" AS recipient_key, e.committee_id, {_NAME} AS name, e.amount, e.expended_date
    FROM isbe_condensed_expenditures e
    WHERE e.archived = FALSE AND COALESCE(e.amount, 0) > 0
"""

RECIPIENT_DIRECTORY_STATEMENTS: tuple[str, ...] = (
    f"DROP MATERIALIZED VIEW IF EXISTS {RECIPIENT_SUMMARY}",
    f"DROP MATERIALIZED VIEW IF EXISTS {RECIPIENT_COMMITTEES}",
    f"""
    CREATE MATERIALIZED VIEW {RECIPIENT_COMMITTEES} AS
    SELECT recipient_key,
           committee_id,
           MODE() WITHIN GROUP (ORDER BY name) AS recipient_name,
           SUM(amount)::double precision AS total_amount,
           COUNT(*) AS payment_count,
           MIN(expended_date)::date AS first_date,
           MAX(expended_date)::date AS last_date
    FROM ({_ROWS}) src
    WHERE recipient_key <> ''
    GROUP BY recipient_key, committee_id
    """,
    f"""
    CREATE MATERIALIZED VIEW {RECIPIENT_SUMMARY} AS
    SELECT recipient_key,
           MODE() WITHIN GROUP (ORDER BY name) AS recipient_name,
           SUM(amount)::double precision AS total_amount,
           COUNT(*) AS payment_count,
           COUNT(DISTINCT committee_id) AS committee_count,
           MIN(expended_date)::date AS first_date,
           MAX(expended_date)::date AS last_date
    FROM ({_ROWS}) src
    WHERE recipient_key <> ''
    GROUP BY recipient_key
    """,
    f"CREATE UNIQUE INDEX idx_isbe_recipient_committees_key_committee ON {RECIPIENT_COMMITTEES} (recipient_key, committee_id)",
    f"CREATE INDEX idx_isbe_recipient_committees_key_total ON {RECIPIENT_COMMITTEES} (recipient_key, total_amount DESC)",
    f"CREATE UNIQUE INDEX idx_isbe_recipient_summary_key ON {RECIPIENT_SUMMARY} (recipient_key)",
    # Spelled out literally: the route's predicate NAME_TSVECTOR_SQL('recipient_name') must be the same text for the planner.
    f"CREATE INDEX idx_isbe_recipient_summary_name_tsv ON {RECIPIENT_SUMMARY} USING gin "
    "(to_tsvector('simple', regexp_replace(upper(coalesce(recipient_name, '')), '[^A-Z0-9 ]', ' ', 'g')))",
)


def build_recipient_directory(conn) -> dict:
    """(Re)build both views and ANALYZE them. Works with a raw psycopg connection and the compat connection; does not commit."""
    for statement in RECIPIENT_DIRECTORY_STATEMENTS:
        conn.execute(statement)
    conn.execute(f"ANALYZE {RECIPIENT_SUMMARY}, {RECIPIENT_COMMITTEES}")
    recipients = conn.execute(f"SELECT COUNT(*) AS n FROM {RECIPIENT_SUMMARY}").fetchone()[0]
    pairs = conn.execute(f"SELECT COUNT(*) AS n FROM {RECIPIENT_COMMITTEES}").fetchone()[0]
    return {"recipients": int(recipients), "recipient_committees": int(pairs)}
