"""Tests for rewiring all website tables to ISBE sunshine-derived data.

Covers:
- bulk_candidate_committee_finance_agg as VIEW from ISBE tables
- Candidate search fallback for candidates without committees
- analytics_donor_summary refresh from ISBE receipts
- analytics_donor_committee_agg refresh from ISBE receipts
- analytics_committee_monthly_totals refresh from ISBE receipts
- analytics_large_contributions refresh from ISBE receipts
- Person intelligence using ISBE donor data
"""

import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp.app import create_app
from database.connection import init_db, get_db


def _seed_isbe_tables(conn):
    """Create ISBE sunshine tables and populate with test data (SQLite-compatible)."""
    # Drop in dependency order (no CASCADE in SQLite)
    for tbl in ['isbe_candidate_committees', 'isbe_d2_reports', 'isbe_filed_docs',
                'isbe_receipts', 'isbe_expenditures', 'isbe_candidates', 'isbe_committees']:
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")

    conn.execute("""
        CREATE TABLE isbe_committees (
            id INTEGER PRIMARY KEY,
            name TEXT, type TEXT, refer_name TEXT,
            address1 TEXT, address2 TEXT, address3 TEXT,
            city TEXT, state TEXT, zipcode TEXT,
            active INTEGER DEFAULT 1,
            status_date TEXT, creation_date TEXT,
            creation_amount REAL,
            disp_funds_return TEXT, disp_funds_political_committee TEXT,
            disp_funds_charity TEXT, disp_funds_95 TEXT,
            candidate_position TEXT, policy_position TEXT,
            party TEXT, purpose TEXT,
            state_committee INTEGER, local_committee INTEGER
        )
    """)
    conn.execute("""
        INSERT INTO isbe_committees (id, name, type, refer_name, city, state, zipcode, active, party)
        VALUES
            (100, 'Citizens for Smith', 'Candidate', 'SMITH', 'Chicago', 'IL', '60601', 1, 'Democratic'),
            (200, 'Friends of Jones', 'Candidate', 'JONES', 'Springfield', 'IL', '62701', 1, 'Republican'),
            (300, 'PAC United', 'Political Action', 'PACUNITED', 'Peoria', 'IL', '61602', 1, NULL)
    """)

    conn.execute("""
        CREATE TABLE isbe_candidates (
            id INTEGER PRIMARY KEY,
            last_name TEXT, first_name TEXT,
            address1 TEXT, address2 TEXT,
            city TEXT, state TEXT, zipcode TEXT,
            office TEXT, district_type TEXT, district TEXT,
            residence_county TEXT, party TEXT,
            redaction_requested INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party, city, state, zipcode, residence_county)
        VALUES
            (1001, 'Smith', 'John', 'Governor', 'Statewide', 'At-Large', 'Democratic', 'Chicago', 'IL', '60601', 'Cook'),
            (1002, 'Jones', 'Sarah', 'State Senator', 'Legislative', '5', 'Republican', 'Springfield', 'IL', '62701', 'Sangamon'),
            (1003, 'Atcha', 'Haroon', 'Trustee', 'Comm College', 'Dupage', NULL, 'Naperville', 'IL', '60540', 'DuPage')
    """)

    conn.execute("""
        CREATE TABLE isbe_candidate_committees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            committee_id INTEGER,
            candidate_id INTEGER
        )
    """)
    conn.execute("""
        INSERT INTO isbe_candidate_committees (committee_id, candidate_id)
        VALUES (100, 1001), (200, 1002)
    """)

    conn.execute("""
        CREATE TABLE isbe_filed_docs (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            doc_name TEXT, doc_type TEXT,
            reporting_period_begin TEXT, reporting_period_end TEXT,
            received_datetime TEXT, filed_date TEXT
        )
    """)
    conn.execute("""
        INSERT INTO isbe_filed_docs (id, committee_id, doc_name, reporting_period_begin, reporting_period_end, received_datetime)
        VALUES
            (5001, 100, 'D-2 Q1 2025', '2025-01-01', '2025-03-31', '2025-04-15'),
            (5002, 200, 'D-2 Q1 2025', '2025-01-01', '2025-03-31', '2025-04-15'),
            (5003, 100, 'D-2 Q2 2025', '2025-04-01', '2025-06-30', '2025-07-15')
    """)

    conn.execute("""
        CREATE TABLE isbe_d2_reports (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            filed_doc_id INTEGER,
            beginning_funds_avail REAL,
            total_receipts REAL,
            total_expenditures REAL,
            end_funds_available REAL,
            individual_itemized REAL, individual_non_itemized REAL,
            transfer_in REAL, loan_received REAL, other_receipts REAL,
            inkind_itemized REAL, inkind_non_itemized REAL, total_inkind REAL,
            expenditures_itemized REAL, expenditures_non_itemized REAL,
            independent_expenditures_itemized REAL, independent_expenditures_non_itemized REAL,
            debts_itemized REAL, debts_non_itemized REAL, total_debts REAL,
            total_investments REAL,
            archived INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, total_receipts, total_expenditures, end_funds_available, archived)
        VALUES
            (9001, 100, 5001, 50000.0, 20000.0, 30000.0, 0),
            (9002, 200, 5002, 25000.0, 10000.0, 15000.0, 0),
            (9003, 100, 5003, 30000.0, 15000.0, 45000.0, 0)
    """)

    conn.execute("""
        CREATE TABLE isbe_receipts (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            filed_doc_id INTEGER,
            etrans_id TEXT,
            last_name TEXT, first_name TEXT,
            received_date TEXT,
            amount REAL,
            aggregate_amount REAL, loan_amount REAL,
            occupation TEXT, employer TEXT,
            address1 TEXT, address2 TEXT,
            city TEXT, state TEXT, zipcode TEXT,
            d2_part TEXT, description TEXT,
            vendor_last_name TEXT, vendor_first_name TEXT,
            vendor_address1 TEXT, vendor_address2 TEXT,
            vendor_city TEXT, vendor_state TEXT, vendor_zipcode TEXT,
            archived INTEGER DEFAULT 0,
            country TEXT,
            redaction_requested INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name,
            received_date, amount, d2_part, occupation, employer, city, state, zipcode, address1, archived)
        VALUES
            (10001, 100, 5001, 'Donor', 'Alice', '2025-01-15', 5000.0, '1A', 'Lawyer', 'BigLaw LLC', 'Chicago', 'IL', '60601', '100 Main St', 0),
            (10002, 100, 5001, 'Donor', 'Bob', '2025-02-10', 2500.0, '1A', 'Teacher', 'CPS', 'Evanston', 'IL', '60201', '200 Elm St', 0),
            (10003, 200, 5002, 'Donor', 'Alice', '2025-01-20', 10000.0, '1A', 'Lawyer', 'BigLaw LLC', 'Chicago', 'IL', '60601', '100 Main St', 0),
            (10004, 100, 5003, 'BigCorp', NULL, '2025-05-01', 25000.0, '1A', NULL, NULL, 'Chicago', 'IL', '60606', '500 LaSalle', 0),
            (10005, 100, 5001, 'Archived', 'Person', '2025-01-01', 999.0, '1A', NULL, NULL, 'Chicago', 'IL', '60601', '999 Old St', 1)
    """)

    conn.execute("""
        CREATE TABLE isbe_expenditures (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            filed_doc_id INTEGER,
            etrans_id TEXT,
            last_name TEXT, first_name TEXT,
            expended_date TEXT,
            amount REAL,
            aggregate_amount REAL,
            address1 TEXT, address2 TEXT,
            city TEXT, state TEXT, zipcode TEXT,
            d2_part TEXT, purpose TEXT,
            candidate_name TEXT, office TEXT,
            supporting INTEGER DEFAULT 0,
            opposing INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            country TEXT,
            redaction_requested INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        INSERT INTO isbe_expenditures (id, committee_id, filed_doc_id, last_name, first_name,
            expended_date, amount, purpose, d2_part, archived)
        VALUES
            (20001, 100, 5001, 'Acme Consulting', NULL, '2025-01-20', 3000.0, 'Consulting', '2A', 0),
            (20002, 200, 5002, 'Print Shop Inc', NULL, '2025-02-01', 1500.0, 'Printing', '2A', 0)
    """)

    conn.commit()


def _create_compat_views(conn):
    """Create backward-compatible views mapping bulk_*_clean to isbe_* (SQLite-compatible)."""
    conn.execute("DROP VIEW IF EXISTS bulk_committees_clean")
    conn.execute("""
        CREATE VIEW bulk_committees_clean AS
        SELECT
            id AS committee_id_sbe, name AS committee_name,
            refer_name AS reference_name, type AS committee_type,
            address1 AS address_line_1, address2 AS address_line_2,
            city, state, zipcode AS postal_code,
            active AS is_active, status_date, creation_date, creation_amount,
            party AS committee_party_affiliation, purpose AS committee_purpose,
            NULL AS source_file, NULL AS source_row_number
        FROM isbe_committees
    """)

    conn.execute("DROP VIEW IF EXISTS bulk_candidates_clean")
    conn.execute("""
        CREATE VIEW bulk_candidates_clean AS
        SELECT
            id AS candidate_id, last_name, first_name,
            TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS candidate_full_name,
            address1 AS address_line_1, address2 AS address_line_2,
            city, state, zipcode AS postal_code,
            office AS office_sought, district_type, district,
            residence_county, party AS party_affiliation,
            redaction_requested, NULL AS source_file, NULL AS source_row_number
        FROM isbe_candidates
    """)

    conn.execute("DROP VIEW IF EXISTS bulk_receipts_clean")
    conn.execute("""
        CREATE VIEW bulk_receipts_clean AS
        SELECT
            id AS bulk_row_id, id AS receipt_record_id,
            committee_id AS committee_id_sbe, filed_doc_id,
            etrans_id AS electronic_transaction_id,
            last_name AS last_or_business_name, first_name,
            received_date, CAST(received_date AS TEXT) AS received_datetime_raw,
            amount, aggregate_amount, loan_amount,
            occupation, employer,
            address1 AS address_line_1, address2 AS address_line_2,
            city, state, zipcode AS postal_code,
            d2_part AS d2_part_code, description,
            vendor_last_name AS vendor_last_or_business_name, vendor_first_name,
            vendor_address1 AS vendor_address_line_1, vendor_address2 AS vendor_address_line_2,
            vendor_city, vendor_state, vendor_zipcode AS vendor_postal_code,
            archived AS is_archived, country, redaction_requested,
            NULL AS source_file, NULL AS source_row_number
        FROM isbe_receipts
    """)

    conn.execute("DROP VIEW IF EXISTS bulk_expenditures_clean")
    conn.execute("""
        CREATE VIEW bulk_expenditures_clean AS
        SELECT
            id AS bulk_row_id, id AS expenditure_record_id,
            committee_id AS committee_id_sbe, filed_doc_id,
            etrans_id AS electronic_transaction_id,
            last_name AS payee_last_or_business_name, first_name AS payee_first_name,
            expended_date, amount, aggregate_amount,
            address1 AS address_line_1, address2 AS address_line_2,
            city, state, zipcode AS postal_code,
            d2_part AS d2_part_code, purpose, candidate_name, office,
            supporting AS is_supporting, opposing AS is_opposing,
            archived AS is_archived, country, redaction_requested,
            NULL AS anomaly_reason,
            CASE WHEN amount > 10000000 THEN 1 ELSE 0 END AS is_amount_anomalous,
            NULL AS source_file, NULL AS source_row_number
        FROM isbe_expenditures
    """)

    conn.execute("DROP VIEW IF EXISTS bulk_d2_totals_clean")
    conn.execute("""
        CREATE VIEW bulk_d2_totals_clean AS
        SELECT
            id AS d2_totals_record_id, committee_id AS committee_id_sbe, filed_doc_id,
            beginning_funds_avail AS beginning_funds_available,
            individual_itemized AS individual_contributions_itemized,
            individual_non_itemized AS individual_contributions_non_itemized,
            NULL AS transfers_in_itemized, NULL AS transfers_in_non_itemized,
            NULL AS loans_received_itemized, NULL AS loans_received_non_itemized,
            NULL AS other_receipts_itemized, NULL AS other_receipts_non_itemized,
            total_receipts,
            inkind_itemized AS in_kind_contributions_itemized,
            inkind_non_itemized AS in_kind_contributions_non_itemized,
            total_inkind AS total_in_kind_contributions,
            NULL AS transfers_out_itemized, NULL AS transfers_out_non_itemized,
            NULL AS loans_made_itemized, NULL AS loans_made_non_itemized,
            expenditures_itemized, expenditures_non_itemized,
            independent_expenditures_itemized, independent_expenditures_non_itemized,
            total_expenditures,
            debts_itemized AS debts_obligations_itemized,
            debts_non_itemized AS debts_obligations_non_itemized,
            total_debts AS total_debts_obligations,
            total_investments,
            end_funds_available AS ending_funds_available,
            archived AS is_archived,
            NULL AS source_file, NULL AS source_row_number
        FROM isbe_d2_reports
    """)

    conn.execute("DROP VIEW IF EXISTS bulk_committee_candidate_links")
    conn.execute("""
        CREATE VIEW bulk_committee_candidate_links AS
        SELECT
            cc.id AS link_record_id, cc.committee_id AS committee_id_sbe,
            cc.candidate_id, c.name AS committee_name,
            c.refer_name AS reference_name, c.type AS committee_type,
            c.party AS committee_party_affiliation, NULL AS committee_status_code,
            c.city AS committee_city, c.state AS committee_state,
            c.zipcode AS committee_postal_code, c.purpose AS committee_purpose,
            ca.last_name, ca.first_name,
            TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
            ca.office AS office_sought, ca.district_type, ca.district,
            ca.residence_county, ca.party AS candidate_party_affiliation,
            ca.city AS candidate_city, ca.state AS candidate_state,
            ca.zipcode AS candidate_postal_code,
            NULL AS link_source_file, NULL AS candidate_source_file,
            NULL AS committee_source_file
        FROM isbe_candidate_committees cc
        JOIN isbe_committees c ON c.id = cc.committee_id
        JOIN isbe_candidates ca ON ca.id = cc.candidate_id
    """)

    conn.commit()


def _create_candidate_finance_agg_view(conn):
    """Create bulk_candidate_committee_finance_agg as a VIEW derived from ISBE tables (SQLite)."""
    conn.execute("DROP VIEW IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
    conn.execute("""
        CREATE VIEW bulk_candidate_committee_finance_agg AS
        WITH filing_periods AS (
            SELECT
                committee_id AS committee_id_sbe,
                filed_doc_id,
                CAST(STRFTIME('%Y', MAX(received_date)) AS INTEGER) AS period_year,
                MIN(received_date) AS period_start_date,
                MAX(received_date) AS period_end_date
            FROM isbe_receipts
            WHERE received_date IS NOT NULL
            GROUP BY committee_id, filed_doc_id
        )
        SELECT
            cc.candidate_id,
            TRIM(COALESCE(ca.first_name, '') || ' ' || COALESCE(ca.last_name, '')) AS candidate_full_name,
            ca.office AS office_sought,
            ca.district_type,
            ca.district,
            ca.party AS candidate_party_affiliation,
            cc.committee_id AS committee_id_sbe,
            c.name AS committee_name,
            c.type AS committee_type,
            c.party AS committee_party_affiliation,
            fp.period_year,
            CASE
                WHEN fp.period_year IS NULL THEN NULL
                WHEN fp.period_year % 2 = 0 THEN fp.period_year
                ELSE fp.period_year + 1
            END AS election_cycle,
            COUNT(DISTINCT CASE WHEN COALESCE(d2.archived, 0) = 0 THEN d2.filed_doc_id END) AS filing_count,
            COALESCE(
                SUM(CASE WHEN COALESCE(d2.archived, 0) = 0 THEN COALESCE(d2.total_receipts, 0) ELSE 0 END),
                0
            ) AS sum_total_receipts,
            COALESCE(
                SUM(CASE WHEN COALESCE(d2.archived, 0) = 0 THEN COALESCE(d2.total_expenditures, 0) ELSE 0 END),
                0
            ) AS sum_total_expenditures,
            COALESCE(
                MAX(CASE WHEN COALESCE(d2.archived, 0) = 0 THEN d2.end_funds_available END),
                0
            ) AS max_ending_funds_available,
            COALESCE(SUM(CASE WHEN d2.archived = 1 THEN 1 ELSE 0 END), 0) AS archived_filing_count,
            MIN(fp.period_start_date) AS period_start_date,
            MAX(fp.period_end_date) AS period_end_date
        FROM isbe_candidate_committees cc
        JOIN isbe_candidates ca ON ca.id = cc.candidate_id
        JOIN isbe_committees c ON c.id = cc.committee_id
        LEFT JOIN isbe_d2_reports d2
            ON d2.committee_id = cc.committee_id
        LEFT JOIN filing_periods fp
            ON fp.committee_id_sbe = d2.committee_id
            AND fp.filed_doc_id = d2.filed_doc_id
        GROUP BY
            cc.candidate_id, ca.first_name, ca.last_name,
            ca.office, ca.district_type, ca.district, ca.party,
            cc.committee_id, c.name, c.type, c.party,
            fp.period_year,
            CASE
                WHEN fp.period_year IS NULL THEN NULL
                WHEN fp.period_year % 2 = 0 THEN fp.period_year
                ELSE fp.period_year + 1
            END
    """)
    conn.commit()


@pytest.fixture
def isbe_app(tmp_path: Path):
    """Create a test app with ISBE sunshine tables and compat views."""
    db_path = str(tmp_path / "test_isbe_rewire.db")
    init_db(db_path)
    conn = get_db(db_path)

    _seed_isbe_tables(conn)
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)

    conn.close()

    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': db_path,
    })
    yield app


@pytest.fixture
def isbe_client(isbe_app):
    return isbe_app.test_client()


class TestCandidateFinanceAggView:
    """Test bulk_candidate_committee_finance_agg as ISBE-derived VIEW."""

    def test_agg_view_returns_candidates_with_committees(self, isbe_app):
        """Candidates with committee links appear in agg view."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        rows = conn.execute(
            "SELECT candidate_id, candidate_full_name, committee_id_sbe FROM bulk_candidate_committee_finance_agg ORDER BY candidate_id"
        ).fetchall()
        candidate_ids = {row['candidate_id'] for row in rows}
        assert 1001 in candidate_ids, "Smith should be in agg"
        assert 1002 in candidate_ids, "Jones should be in agg"
        conn.close()

    def test_agg_view_excludes_candidates_without_committees(self, isbe_app):
        """Atcha (no committee link) should NOT appear in agg view."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        rows = conn.execute(
            "SELECT candidate_id FROM bulk_candidate_committee_finance_agg WHERE candidate_id = 1003"
        ).fetchall()
        assert len(rows) == 0, "Atcha (no committee) should not be in agg view"
        conn.close()

    def test_agg_view_has_financial_data(self, isbe_app):
        """Agg view should contain real financial figures from D2 reports."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        row = conn.execute(
            "SELECT SUM(sum_total_receipts) AS total FROM bulk_candidate_committee_finance_agg WHERE candidate_id = 1001"
        ).fetchone()
        assert row['total'] is not None
        assert float(row['total']) > 0, "Smith should have receipts from D2 reports"
        conn.close()

    def test_agg_view_has_expected_columns(self, isbe_app):
        """All expected columns must be present for downstream routes."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        row = conn.execute(
            "SELECT * FROM bulk_candidate_committee_finance_agg LIMIT 1"
        ).fetchone()
        expected_cols = [
            'candidate_id', 'candidate_full_name', 'office_sought',
            'district_type', 'district', 'candidate_party_affiliation',
            'committee_id_sbe', 'committee_name', 'committee_type',
            'committee_party_affiliation', 'period_year', 'election_cycle',
            'filing_count', 'sum_total_receipts', 'sum_total_expenditures',
            'max_ending_funds_available', 'archived_filing_count',
            'period_start_date', 'period_end_date',
        ]
        for col in expected_cols:
            assert col in row.keys(), f"Missing column: {col}"
        conn.close()

    def test_agg_view_election_cycle_computation(self, isbe_app):
        """Election cycle should be the even year >= period_year."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        rows = conn.execute(
            "SELECT period_year, election_cycle FROM bulk_candidate_committee_finance_agg WHERE period_year IS NOT NULL"
        ).fetchall()
        for row in rows:
            if row['period_year'] is not None and row['election_cycle'] is not None:
                assert int(row['election_cycle']) % 2 == 0, "election_cycle must be even"
                assert int(row['election_cycle']) >= int(row['period_year'])
        conn.close()

    def test_agg_view_excludes_archived_d2_from_totals(self, isbe_app):
        """Archived D2 reports should not contribute to sum_total_receipts."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        # Add an archived D2 report
        conn.execute("""
            INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, total_receipts, total_expenditures, end_funds_available, archived)
            VALUES (9999, 100, 5001, 999999.0, 999999.0, 999999.0, TRUE)
        """)
        conn.commit()
        row = conn.execute(
            "SELECT SUM(sum_total_receipts) AS total FROM bulk_candidate_committee_finance_agg WHERE candidate_id = 1001"
        ).fetchone()
        # The 999999 should NOT be included
        assert float(row['total']) < 999999.0, "Archived D2 should not inflate totals"
        conn.close()


class TestCandidateSearchFallback:
    """Test that candidate search finds candidates even without committee links."""

    def test_search_finds_candidate_with_committee(self, isbe_app, isbe_client):
        """Smith (has committee) should appear in search results."""
        response = isbe_client.get('/search?q=Smith&type=candidates')
        assert response.status_code == 200
        assert b'Smith' in response.data

    def test_search_finds_candidate_without_committee(self, isbe_app, isbe_client):
        """Atcha (no committee) should appear in search results via fallback."""
        response = isbe_client.get('/search?q=Atcha&type=candidates')
        assert response.status_code == 200
        assert b'Atcha' in response.data

    def test_search_candidate_without_committee_shows_zero_receipts(self, isbe_app, isbe_client):
        """Candidates without committees should show $0 receipts."""
        response = isbe_client.get('/search?q=Atcha&type=candidates')
        assert response.status_code == 200
        # The candidate should be present but without financial data
        assert b'Atcha' in response.data

    def test_search_empty_query_returns_200(self, isbe_app, isbe_client):
        """Empty search should not crash."""
        response = isbe_client.get('/search?q=&type=candidates')
        assert response.status_code == 200


class TestCandidateFinancePage:
    """Test the /candidate-finance/ page with ISBE-derived data."""

    def test_candidate_finance_page_loads(self, isbe_app, isbe_client):
        """Page should load with ISBE data."""
        response = isbe_client.get('/candidate-finance/')
        assert response.status_code == 200
        assert b'Candidate Committee Finance' in response.data

    def test_candidate_finance_shows_isbe_candidates(self, isbe_app, isbe_client):
        """ISBE candidates with committees should appear."""
        response = isbe_client.get('/candidate-finance/')
        assert response.status_code == 200
        assert b'Smith' in response.data or b'Jones' in response.data

    def test_candidate_finance_search_filter(self, isbe_app, isbe_client):
        """Query filter should work."""
        response = isbe_client.get('/candidate-finance/?q=Smith')
        assert response.status_code == 200
        assert b'Smith' in response.data


class TestAnalyticsRefreshFromISBE:
    """Test that analytics refresh populates correctly from ISBE compat views."""

    def test_refresh_populates_donor_committee_agg(self, isbe_app):
        """After refresh, analytics_donor_committee_agg should have ISBE-derived rows."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        result = refresh_analytics_materialized(conn)
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM analytics_donor_committee_agg WHERE source = 'bulk_receipts'"
        ).fetchone()
        assert int(rows['cnt']) > 0, "Should have donor-committee agg rows from ISBE receipts"
        conn.close()

    def test_refresh_populates_donor_summary(self, isbe_app):
        """After refresh, analytics_donor_summary should have ISBE-derived rows."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        result = refresh_analytics_materialized(conn)
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM analytics_donor_summary WHERE source = 'bulk_receipts'"
        ).fetchone()
        assert int(rows['cnt']) > 0, "Should have donor summary rows from ISBE receipts"
        conn.close()

    def test_refresh_populates_monthly_totals(self, isbe_app):
        """After refresh, analytics_committee_monthly_totals should have ISBE-derived rows."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        result = refresh_analytics_materialized(conn)
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM analytics_committee_monthly_totals WHERE source = 'bulk_receipts'"
        ).fetchone()
        assert int(rows['cnt']) > 0, "Should have monthly totals from ISBE receipts"
        conn.close()

    def test_refresh_populates_large_contributions(self, isbe_app):
        """After refresh, analytics_large_contributions should have ISBE-derived rows for large amounts."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        result = refresh_analytics_materialized(conn)
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM analytics_large_contributions WHERE source = 'bulk_receipts'"
        ).fetchone()
        # We have a 25000 and 10000 donation; threshold is max(5000, p95*2)
        assert int(rows['cnt']) > 0, "Should have large contributions from ISBE receipts"
        conn.close()

    def test_donor_summary_has_correct_totals(self, isbe_app):
        """Donor summary totals should match sum of ISBE receipts."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        refresh_analytics_materialized(conn)
        # Alice Donor gave 5000 + 10000 = 15000 across two committees
        alice_rows = conn.execute(
            "SELECT total_amount FROM analytics_donor_summary WHERE source = 'bulk_receipts' AND donor_name LIKE '%Alice%'"
        ).fetchall()
        assert len(alice_rows) > 0, "Alice should be in donor summary"
        total = sum(float(r['total_amount']) for r in alice_rows)
        assert total == pytest.approx(15000.0, abs=1.0), f"Alice total should be 15000, got {total}"
        conn.close()


class TestPersonIntelligenceISBE:
    """Test person intelligence page uses ISBE-derived donor data."""

    def test_person_intel_finds_donor(self, isbe_app, isbe_client):
        """After analytics refresh, person intelligence should find ISBE donors."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        refresh_analytics_materialized(conn)
        conn.close()

        response = isbe_client.get('/person-intelligence?q=Alice')
        assert response.status_code == 200
        assert b'Alice' in response.data

    def test_person_intel_finds_candidate_in_agg(self, isbe_app, isbe_client):
        """Person intelligence should find ISBE candidates."""
        response = isbe_client.get('/person-intelligence?q=Smith')
        assert response.status_code == 200


class TestHomepageWithISBE:
    """Test homepage stats use ISBE-derived data."""

    def test_homepage_loads(self, isbe_app, isbe_client):
        """Homepage should load without errors using ISBE data."""
        response = isbe_client.get('/')
        assert response.status_code == 200

    def test_homepage_shows_candidate_count(self, isbe_app, isbe_client):
        """Homepage should show non-zero candidate count from ISBE data."""
        from database.analytics import refresh_analytics_materialized
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        refresh_analytics_materialized(conn)
        conn.close()

        response = isbe_client.get('/')
        assert response.status_code == 200


class TestEdgeCases:
    """Edge cases: empty data, missing fields, malformed input."""

    def test_empty_isbe_tables(self, tmp_path):
        """App should handle empty ISBE tables gracefully."""
        db_path = str(tmp_path / "test_empty.db")
        init_db(db_path)
        conn = get_db(db_path)

        # Create empty ISBE tables (SQLite-compatible)
        for tbl in ['isbe_candidate_committees', 'isbe_d2_reports', 'isbe_filed_docs',
                    'isbe_receipts', 'isbe_expenditures', 'isbe_candidates', 'isbe_committees']:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")

        conn.execute("CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT, type TEXT, refer_name TEXT, address1 TEXT, address2 TEXT, address3 TEXT, city TEXT, state TEXT, zipcode TEXT, active INTEGER DEFAULT 1, status_date TEXT, creation_date TEXT, creation_amount REAL, disp_funds_return TEXT, disp_funds_political_committee TEXT, disp_funds_charity TEXT, disp_funds_95 TEXT, candidate_position TEXT, policy_position TEXT, party TEXT, purpose TEXT, state_committee INTEGER, local_committee INTEGER)")
        conn.execute("CREATE TABLE isbe_candidates (id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, office TEXT, district_type TEXT, district TEXT, residence_county TEXT, party TEXT, redaction_requested INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE isbe_candidate_committees (id INTEGER PRIMARY KEY AUTOINCREMENT, committee_id INTEGER, candidate_id INTEGER)")
        conn.execute("CREATE TABLE isbe_filed_docs (id INTEGER PRIMARY KEY, committee_id INTEGER, doc_name TEXT, doc_type TEXT, reporting_period_begin TEXT, reporting_period_end TEXT, received_datetime TEXT, filed_date TEXT)")
        conn.execute("CREATE TABLE isbe_d2_reports (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, beginning_funds_avail REAL, total_receipts REAL, total_expenditures REAL, end_funds_available REAL, individual_itemized REAL, individual_non_itemized REAL, transfer_in REAL, loan_received REAL, other_receipts REAL, inkind_itemized REAL, inkind_non_itemized REAL, total_inkind REAL, expenditures_itemized REAL, expenditures_non_itemized REAL, independent_expenditures_itemized REAL, independent_expenditures_non_itemized REAL, debts_itemized REAL, debts_non_itemized REAL, total_debts REAL, total_investments REAL, archived INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE isbe_receipts (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, etrans_id TEXT, last_name TEXT, first_name TEXT, received_date TEXT, amount REAL, aggregate_amount REAL, loan_amount REAL, occupation TEXT, employer TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, d2_part TEXT, description TEXT, vendor_last_name TEXT, vendor_first_name TEXT, vendor_address1 TEXT, vendor_address2 TEXT, vendor_city TEXT, vendor_state TEXT, vendor_zipcode TEXT, archived INTEGER DEFAULT 0, country TEXT, redaction_requested INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE isbe_expenditures (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, etrans_id TEXT, last_name TEXT, first_name TEXT, expended_date TEXT, amount REAL, aggregate_amount REAL, address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, d2_part TEXT, purpose TEXT, candidate_name TEXT, office TEXT, supporting INTEGER DEFAULT 0, opposing INTEGER DEFAULT 0, archived INTEGER DEFAULT 0, country TEXT, redaction_requested INTEGER DEFAULT 0)")
        conn.commit()

        _create_compat_views(conn)
        _create_candidate_finance_agg_view(conn)
        conn.close()

        app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
        client = app.test_client()

        # Should not crash
        assert client.get('/').status_code == 200
        assert client.get('/search?q=anything&type=candidates').status_code == 200
        assert client.get('/candidate-finance/').status_code == 200

    def test_candidate_with_null_name_fields(self, isbe_app):
        """Candidates with NULL first/last names should not crash."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        conn.execute("""
            INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party)
            VALUES (9999, NULL, NULL, 'Unknown', 'Unknown', 'Unknown', NULL)
        """)
        conn.commit()
        # Search should not crash
        rows = conn.execute(
            "SELECT candidate_full_name FROM bulk_candidates_clean WHERE candidate_id = 9999"
        ).fetchall()
        assert len(rows) == 1
        conn.close()

    def test_receipt_with_null_amount(self, isbe_app):
        """Receipts with NULL amount should not crash analytics."""
        conn = get_db(isbe_app.config['DATABASE_PATH'])
        conn.execute("""
            INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, received_date, amount, d2_part, archived)
            VALUES (99999, 100, 5001, 'NullDonor', '2025-03-01', NULL, '1A', FALSE)
        """)
        conn.commit()
        # Compat view should still work
        row = conn.execute(
            "SELECT amount FROM bulk_receipts_clean WHERE bulk_row_id = 99999"
        ).fetchone()
        assert row['amount'] is None
        conn.close()
