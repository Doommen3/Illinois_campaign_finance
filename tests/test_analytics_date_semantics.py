"""Regression tests for the C5 fix in database/analytics.py.

Before the fix, legacy `contributions` rows without `transaction_date` were
attributed to their report's `filed_date` via a COALESCE fallback. This
misbucketed transactions into the report-filing month rather than the actual
transaction month. The fix uses `transaction_date` only; rows without it are
excluded from date-bounded queries.
"""

from __future__ import annotations

from database.analytics import _get_donor_committee_rows
from database.connection import get_db


def test_donor_committee_rows_excludes_missing_transaction_date():
    """A contributions row whose transaction_date is NULL must not be
    attributed to its report's filed_date when a date window is applied.

    Setup:
      - report.filed_date = 2024-01-15 (would have matched the window)
      - contribution.transaction_date = NULL (the actual transaction date is
        unknown; the row should NOT show up under a Jan 2024 window pre-fix
        nor post-fix, but for a Dec 2023 window the COALESCE behavior would
        have INCLUDED it via filed_date — the fix excludes it).
    """
    conn = get_db()
    # Use the schema_translator's plain-name tables (the conftest already
    # routes us into a fresh test schema).
    conn.executescript(
        """
        CREATE TABLE committees (
            id INTEGER PRIMARY KEY,
            name TEXT,
            scrape_status TEXT
        );

        CREATE TABLE donors (
            id INTEGER PRIMARY KEY,
            name TEXT,
            address TEXT,
            normalized_name TEXT,
            normalized_address TEXT
        );

        CREATE TABLE reports (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            filed_date TEXT,
            paper_filed INTEGER DEFAULT 0
        );

        CREATE TABLE contributions (
            id INTEGER PRIMARY KEY,
            report_id INTEGER,
            donor_id INTEGER,
            amount REAL,
            transaction_date TEXT
        );
        """
    )

    conn.execute("INSERT INTO committees (id, name) VALUES (1, 'Citizens for X')")
    conn.execute(
        "INSERT INTO donors (id, name, address, normalized_name, normalized_address)"
        " VALUES (1, 'Acme Corp', '123 Main St', 'acme corp', '123 main st')"
    )
    conn.execute(
        "INSERT INTO reports (id, committee_id, filed_date) VALUES (1, 1, '2024-01-15')"
    )
    # Row A: real transaction_date in October 2023 (the actual contribution
    # date). Filed in Jan 2024 — but we should bucket by Oct 2023.
    conn.execute(
        "INSERT INTO contributions (id, report_id, donor_id, amount, transaction_date)"
        " VALUES (1, 1, 1, 5000, '2023-10-20')"
    )
    # Row B: transaction_date missing. Pre-fix, COALESCE would have
    # attributed this to filed_date=2024-01-15. Post-fix, it is excluded.
    conn.execute(
        "INSERT INTO contributions (id, report_id, donor_id, amount, transaction_date)"
        " VALUES (2, 1, 1, 7500, NULL)"
    )
    conn.commit()

    # Apply a date window that the FILED date (2024-01-15) would match but
    # the actual transaction_date (2023-10-20) would not.
    rows, _src = _get_donor_committee_rows(
        conn,
        min_edge_amount=0,
        date_from="2024-01-01",
        date_to="2024-12-31",
    )

    # Pre-fix: row B (NULL transaction_date) would have leaked into Jan 2024 via filed_date.
    # Post-fix: row B is excluded; row A's transaction is Oct 2023, also outside the window.
    # Therefore: no donor-committee rows should appear under this window.
    assert rows == [], (
        f"Expected no rows for the Jan-Dec 2024 window (transaction was in 2023 and "
        f"the NULL-date row must not fall back to filed_date), got: {rows}"
    )


def test_donor_committee_rows_uses_transaction_date_window():
    """A row with a transaction_date inside the window IS returned."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE committees (id INTEGER PRIMARY KEY, name TEXT, scrape_status TEXT);
        CREATE TABLE donors (
            id INTEGER PRIMARY KEY, name TEXT, address TEXT,
            normalized_name TEXT, normalized_address TEXT
        );
        CREATE TABLE reports (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_date TEXT, paper_filed INTEGER DEFAULT 0);
        CREATE TABLE contributions (
            id INTEGER PRIMARY KEY, report_id INTEGER, donor_id INTEGER,
            amount REAL, transaction_date TEXT
        );
        """
    )
    conn.execute("INSERT INTO committees (id, name) VALUES (1, 'Citizens for X')")
    conn.execute(
        "INSERT INTO donors (id, name, address, normalized_name, normalized_address)"
        " VALUES (1, 'Acme', '123 Main', 'acme', '123 main')"
    )
    conn.execute(
        "INSERT INTO reports (id, committee_id, filed_date) VALUES (1, 1, '2024-05-01')"
    )
    conn.execute(
        "INSERT INTO contributions (id, report_id, donor_id, amount, transaction_date)"
        " VALUES (1, 1, 1, 5000, '2024-03-15')"
    )
    conn.commit()

    rows, _src = _get_donor_committee_rows(
        conn,
        min_edge_amount=0,
        date_from="2024-01-01",
        date_to="2024-12-31",
    )
    assert len(rows) == 1
    assert rows[0]["total_amount"] == 5000.0
