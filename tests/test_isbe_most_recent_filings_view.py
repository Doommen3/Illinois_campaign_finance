"""D4: ``isbe_most_recent_filings`` must not pick a NULL-period filing as a committee's most recent filing.

The ETL's outer ``DISTINCT ON (committee_id)`` orders ``reporting_period_end DESC``; PostgreSQL sorts NULL first under
DESC, so one periodic filing without a period end made the committee's ``reporting_period_end`` NULL.
``isbe_condensed_receipts``' first UNION branch (``received_date > COALESCE(m.reporting_period_end, '1900-01-01')``)
then admitted every raw receipt of the committee, including the superseded versions of amended filings.

The DDL is the real text of ``scripts/isbe_sunshine_etl.py`` (each ``CREATE MATERIALIZED VIEW <name> AS (...)``
statement, read from the file up to ``CREATE UNIQUE INDEX ON <name>``), executed against fixture tables in the
per-test schema.  Fictional committee only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from database.connection import get_db

ETL_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "isbe_sunshine_etl.py"

COMMITTEE = 9101
ORIGINAL, AMENDMENT, UNDATED = 1001, 1002, 1003   # filed doc ids
ORIGINAL_RECEIPT, REFILED_RECEIPT = 50001, 50002


def view_ddl(name: str) -> str:
    """The ETL's ``CREATE MATERIALIZED VIEW <name> AS (...);`` statement, verbatim."""
    text = ETL_SOURCE.read_text()
    found = re.search(
        rf"(CREATE MATERIALIZED VIEW {name} AS \(.*?\);)\s*CREATE UNIQUE INDEX ON {name}\b", text, re.S
    )
    assert found, f"no CREATE MATERIALIZED VIEW {name} statement in {ETL_SOURCE}"
    return found.group(1)


@pytest.fixture
def conn(tmp_path):
    conn = get_db(str(tmp_path / "d4.db"))
    conn.execute("CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, active BOOLEAN)")
    conn.execute(
        "CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, committee_id INTEGER, doc_name TEXT,"
        " reporting_period_begin DATE, reporting_period_end DATE, received_datetime TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE isbe_d2_reports (filed_doc_id INTEGER, end_funds_available DOUBLE PRECISION,"
        " total_investments DOUBLE PRECISION, total_debts DOUBLE PRECISION, inkind_itemized DOUBLE PRECISION,"
        " inkind_non_itemized DOUBLE PRECISION)"
    )
    conn.execute(
        "CREATE TABLE isbe_receipts (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER,"
        " received_date DATE, amount DOUBLE PRECISION, archived BOOLEAN, d2_part TEXT, first_name TEXT,"
        " last_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT)"
    )
    conn.execute("INSERT INTO isbe_committees VALUES (?, 'Friends of Fictional Filer', 'Candidate', TRUE)",
                 (COMMITTEE,))
    conn.executemany(
        "INSERT INTO isbe_filed_docs VALUES (?, ?, 'Quarterly', ?, ?, ?)",
        [
            (ORIGINAL, COMMITTEE, "2025-01-01", "2025-03-31", "2025-04-10 09:00:00"),   # Q1 as first filed
            (AMENDMENT, COMMITTEE, "2025-01-01", "2025-03-31", "2025-05-01 14:30:00"),  # Q1 amended
            (UNDATED, COMMITTEE, None, None, "2025-06-01 08:00:00"),                    # periodic, no period end
        ],
    )
    conn.executemany(
        "INSERT INTO isbe_d2_reports VALUES (?, 100.0, 0.0, 0.0, 0.0, 0.0)", [(ORIGINAL,), (AMENDMENT,), (UNDATED,)]
    )
    receipt = (" VALUES (?, ?, ?, '2025-02-10', 500.0, ?, '1A', 'Pat', 'Fictional', '1 Test St', NULL,"
               " 'Springfield', 'IL', '62701')")
    conn.execute("INSERT INTO isbe_receipts" + receipt, (ORIGINAL_RECEIPT, COMMITTEE, ORIGINAL, True))
    conn.execute("INSERT INTO isbe_receipts" + receipt, (REFILED_RECEIPT, COMMITTEE, AMENDMENT, False))
    conn.execute(view_ddl("isbe_most_recent_filings"))
    conn.execute(view_ddl("isbe_condensed_receipts"))
    conn.commit()
    yield conn
    conn.close()


def test_a_null_period_filing_is_not_the_most_recent_filing(conn):
    """Kills: ``reporting_period_end DESC`` without ``NULLS LAST`` in the outer ``DISTINCT ON`` (the undated filing
    1003 wins and the committee's period end is NULL)."""
    rows = conn.execute(
        "SELECT filed_doc_id, reporting_period_end FROM isbe_most_recent_filings WHERE committee_id = ?", (COMMITTEE,)
    ).fetchall()

    assert [(r["filed_doc_id"], str(r["reporting_period_end"])) for r in rows] == [(AMENDMENT, "2025-03-31")]


def test_the_superseded_receipt_stays_out_of_the_condensed_receipts(conn):
    """The Q1 receipt filed on the original (1001) and re-filed on the amendment (1002) is condensed to the
    amendment's row once.  Kills: a NULL period end letting the first UNION branch admit every raw receipt."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM isbe_condensed_receipts WHERE committee_id = ? ORDER BY id", (COMMITTEE,)).fetchall()]

    assert ids == [REFILED_RECEIPT]
