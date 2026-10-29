"""D4: the ETL's filed-doc pickers must keep a committee's current filing.

1. ``isbe_most_recent_filings`` must not pick a NULL-period filing as a committee's most recent filing.  The outer
   ``DISTINCT ON (committee_id)`` ordered ``reporting_period_end DESC``; PostgreSQL sorts NULL first under DESC, so one
   undated filing made the committee's ``reporting_period_end`` NULL, and ``isbe_condensed_receipts``' first UNION
   branch (``received_date > COALESCE(m.reporting_period_end, '1900-01-01')``) admitted every raw receipt.
2. The per-period picker of ``isbe_condensed_receipts`` / ``isbe_condensed_expenditures`` (``DISTINCT ON
   (reporting_period_begin, reporting_period_end, committee_id)``) ordered only ``received_datetime DESC NULLS LAST``:
   a later empty A-1 beat the live quarterly, a dated archived original beat a NULL-dated live amendment, and a tie
   on ``received_datetime`` resolved by plan.

The DDL is the real text of ``scripts/isbe_sunshine_etl.py`` (each ``CREATE MATERIALIZED VIEW <name> AS (...)``
statement, read from the file up to ``CREATE UNIQUE INDEX ON <name>``), executed against fixture tables in the
per-test schema.  Fictional committees only.
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


def _create_tables(conn) -> None:
    conn.execute("CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, active BOOLEAN)")
    conn.execute(
        "CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, committee_id INTEGER, doc_name TEXT,"
        " reporting_period_begin DATE, reporting_period_end DATE, received_datetime TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE isbe_d2_reports (filed_doc_id INTEGER, end_funds_available DOUBLE PRECISION,"
        " total_investments DOUBLE PRECISION, total_debts DOUBLE PRECISION, inkind_itemized DOUBLE PRECISION,"
        " inkind_non_itemized DOUBLE PRECISION, archived BOOLEAN DEFAULT FALSE)"
    )
    conn.execute(
        "CREATE TABLE isbe_receipts (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER,"
        " received_date DATE, amount DOUBLE PRECISION, archived BOOLEAN, d2_part TEXT, first_name TEXT,"
        " last_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT)"
    )


def _committee(conn, committee_id: int) -> None:
    conn.execute("INSERT INTO isbe_committees VALUES (?, 'Friends of Fictional Filer', 'Candidate', TRUE)",
                 (committee_id,))


def _doc(conn, doc_id, committee_id, doc_name, begin, end, received, d2=None) -> None:
    """One filed doc; ``d2`` None = no D-2 row, True = a live D-2, False = an archived D-2."""
    conn.execute("INSERT INTO isbe_filed_docs VALUES (?, ?, ?, ?, ?, ?)",
                 (doc_id, committee_id, doc_name, begin, end, received))
    if d2 is not None:
        conn.execute("INSERT INTO isbe_d2_reports (filed_doc_id, end_funds_available, total_investments, total_debts,"
                     " inkind_itemized, inkind_non_itemized, archived) VALUES (?, 100.0, 0.0, 0.0, 0.0, 0.0, ?)",
                     (doc_id, not d2))


def _receipt(conn, receipt_id, committee_id, doc_id, received_date, archived=False) -> None:
    conn.execute(
        "INSERT INTO isbe_receipts VALUES (?, ?, ?, ?, 500.0, ?, '1A', 'Pat', 'Fictional', '1 Test St', NULL,"
        " 'Springfield', 'IL', '62701')",
        (receipt_id, committee_id, doc_id, received_date, archived),
    )


def _build_views(conn) -> None:
    conn.execute(view_ddl("isbe_most_recent_filings"))
    conn.execute(view_ddl("isbe_condensed_receipts"))
    conn.commit()


def _condensed_ids(conn, committee_id: int) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM isbe_condensed_receipts WHERE committee_id = ? ORDER BY id", (committee_id,)).fetchall()]


@pytest.fixture
def db(tmp_path):
    conn = get_db(str(tmp_path / "d4.db"))
    _create_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn(db):
    """Committee 9101: Q1 as first filed (1001), Q1 amended (1002, received later) and an undated periodic filing
    (1003); the Q1 receipt is on the original (archived) and re-filed on the amendment (live)."""
    _committee(db, COMMITTEE)
    _doc(db, ORIGINAL, COMMITTEE, "Quarterly", "2025-01-01", "2025-03-31", "2025-04-10 09:00:00", d2=True)
    _doc(db, AMENDMENT, COMMITTEE, "Quarterly", "2025-01-01", "2025-03-31", "2025-05-01 14:30:00", d2=True)
    _doc(db, UNDATED, COMMITTEE, "Quarterly", None, None, "2025-06-01 08:00:00", d2=True)
    _receipt(db, ORIGINAL_RECEIPT, COMMITTEE, ORIGINAL, "2025-02-10", archived=True)
    _receipt(db, REFILED_RECEIPT, COMMITTEE, AMENDMENT, "2025-02-10")
    _build_views(db)
    return db


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
    assert _condensed_ids(conn, COMMITTEE) == [REFILED_RECEIPT]


# --- the per-period picker (specialist review of D4) ------------------------------------------------------------

def test_a_quarterly_with_a_live_d2_beats_a_later_empty_a1_for_the_same_period(db):
    """Committee 9201, Q3 2025: the quarterly (live D-2, one live receipt) and an A-1 received ten days later with the
    same period and no rows.  Kills: ordering by ``received_datetime`` alone (the empty A-1 wins and the quarterly's
    receipt vanishes from the condensed set)."""
    _committee(db, 9201)
    _doc(db, 2101, 9201, "Quarterly", "2025-07-01", "2025-09-30", "2025-10-10 09:00:00", d2=True)
    _doc(db, 2102, 9201, "A-1", "2025-07-01", "2025-09-30", "2025-10-20 09:00:00")
    _receipt(db, 60001, 9201, 2101, "2025-08-01")
    _build_views(db)

    assert _condensed_ids(db, 9201) == [60001]


def test_a_null_dated_live_amendment_beats_the_dated_archived_original(db):
    """Committee 9202, Q1 2025: the original (dated, D-2 archived, its receipt archived) and the amendment (no
    ``received_datetime``, live D-2, the re-filed receipt live).  Kills: ``received_datetime DESC NULLS LAST``
    preferring the dated archived original (only the archived receipt is kept)."""
    _committee(db, 9202)
    _doc(db, 2201, 9202, "Quarterly", "2025-01-01", "2025-03-31", "2025-04-10 09:00:00", d2=False)
    _doc(db, 2202, 9202, "Quarterly", "2025-01-01", "2025-03-31", None, d2=True)
    _receipt(db, 60101, 9202, 2201, "2025-02-10", archived=True)
    _receipt(db, 60102, 9202, 2202, "2025-02-10")
    _build_views(db)

    assert _condensed_ids(db, 9202) == [60102]


def test_docs_tied_on_received_datetime_resolve_to_the_higher_id(db):
    """Committee 9203: six quarters, each with two docs tied on everything the picker orders by (both live D-2, both
    without ``received_datetime``), the lower id inserted first; each carries one receipt.  The higher id wins every
    period.  Kills: a picker without a final ``id DESC`` (the tie resolves by plan; with the lower id inserted first it
    resolves to it here)."""
    _committee(db, 9203)
    expected = []
    quarters = [("2024-01-01", "2024-03-31"), ("2024-04-01", "2024-06-30"), ("2024-07-01", "2024-09-30"),
                ("2024-10-01", "2024-12-31"), ("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30")]
    for n, (begin, end) in enumerate(quarters):
        low, high = 2300 + 2 * n, 2301 + 2 * n
        _doc(db, low, 9203, "Quarterly", begin, end, None, d2=True)
        _doc(db, high, 9203, "Quarterly", begin, end, None, d2=True)
        _receipt(db, 61000 + 2 * n, 9203, low, begin)
        _receipt(db, 61001 + 2 * n, 9203, high, begin)
        expected.append(61001 + 2 * n)
    # A later dated filing so the first UNION branch (after the latest period end) admits none of these receipts.
    _doc(db, 2399, 9203, "Quarterly", "2025-07-01", "2025-09-30", "2025-10-10 09:00:00", d2=True)
    _build_views(db)

    assert _condensed_ids(db, 9203) == expected


def test_both_condensed_views_use_the_same_picker():
    """The receipts and expenditures views pick the same doc per period.  Kills: a fix applied to one view only."""
    def picker(name):
        found = re.search(r"JOIN \((\s*SELECT DISTINCT ON \(reporting_period_begin.*?)\) AS f USING\(filed_doc_id\)",
                          view_ddl(name), re.S)
        assert found, name
        return " ".join(found.group(1).split())

    assert picker("isbe_condensed_receipts") == picker("isbe_condensed_expenditures")
