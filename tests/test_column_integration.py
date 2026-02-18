"""Tests for the column integration plan (Bundles A-D + IRS 527 features).

Covers:
- Schema contract: new columns exist in tables after init-db
- Ingestion mapping: new fields are extracted from API payloads and inserted
- API/serializer: new fields appear in candidate detail response
- IRS 527: reports timeline and contribution aggregates work
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path: Path):
    """Create a fresh DB with schema applied."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    schema_path = Path(__file__).parent.parent / "database" / "schema.sql"
    conn.executescript(schema_path.read_text())
    conn.commit()
    yield conn
    conn.close()


# ===================================================================
# SCHEMA CONTRACT TESTS
# ===================================================================

class TestSchemaContract:
    """Verify new columns exist after schema init."""

    def _column_names(self, conn: sqlite3.Connection, table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in rows}

    def test_fec_candidate_committees_has_bundle_d_columns(self, db):
        cols = self._column_names(db, "fec_candidate_committees")
        assert "last_file_date" in cols
        assert "first_file_date" in cols
        assert "party_full" in cols

    def test_fec_schedule_a_has_bundle_a_columns(self, db):
        cols = self._column_names(db, "fec_schedule_a_contributions")
        assert "amendment_indicator" in cols
        assert "file_number" in cols
        assert "transaction_id" in cols

    def test_fec_schedule_b_has_bundle_b_columns(self, db):
        cols = self._column_names(db, "fec_schedule_b_disbursements")
        assert "amendment_indicator" in cols
        assert "disbursement_purpose_category" in cols
        assert "file_number" in cols
        assert "transaction_id" in cols

    def test_fec_schedule_e_has_bundle_c_columns(self, db):
        cols = self._column_names(db, "fec_schedule_e_independent_expenditures")
        assert "is_notice" in cols
        assert "most_recent" in cols
        assert "file_number" in cols
        assert "previous_file_number" in cols
        assert "amendment_indicator" in cols
        assert "transaction_id" in cols


# ===================================================================
# INGESTION TESTS
# ===================================================================

class TestIngestion:
    """Verify new fields are mapped from API payloads to DB rows."""

    def test_schedule_a_carries_amendment_indicator(self, db):
        """amendment_indicator from FEC API payload should be stored."""
        from database.federal_fec import _upsert_schedule_rows

        rows = [{
            "sub_id": "TEST_SA_001",
            "contribution_receipt_amount": 100.0,
            "contribution_receipt_date": "2025-01-01",
            "contributor_name": "TEST DONOR",
            "amendment_indicator": "A",
            "file_number": "1920467",
            "transaction_id": "SA11AI.4181",
        }]
        _upsert_schedule_rows(
            db, rows=rows, candidate_id="H0IL01001",
            candidate_name="TEST", cycle=2026,
            committee_id="C00000001",
            default_committee_name="TEST COMMITTEE",
            api_source_identifier="test",
        )
        db.commit()

        row = db.execute(
            "SELECT amendment_indicator, file_number, transaction_id "
            "FROM fec_schedule_a_contributions WHERE sub_id = 'TEST_SA_001'"
        ).fetchone()
        assert row is not None
        assert row["amendment_indicator"] == "A"
        assert row["file_number"] == "1920467"
        assert row["transaction_id"] == "SA11AI.4181"

    def test_schedule_b_carries_purpose_category(self, db):
        """disbursement_purpose_category should be stored."""
        from database.federal_fec import _upsert_schedule_b_rows

        rows = [{
            "sub_id": "TEST_SB_001",
            "disbursement_amount": 500.0,
            "disbursement_date": "2025-06-15",
            "recipient_name": "VENDOR INC",
            "amendment_indicator": "N",
            "disbursement_purpose_category": "ADVERTISING",
            "file_number": "1919764",
            "transaction_id": "SB17.4284",
        }]
        _upsert_schedule_b_rows(
            db, rows=rows, candidate_id="H0IL01001",
            candidate_name="TEST", cycle=2026,
            committee_id="C00000001",
            default_committee_name="TEST COMMITTEE",
            api_source_identifier="test",
        )
        db.commit()

        row = db.execute(
            "SELECT amendment_indicator, disbursement_purpose_category, file_number, transaction_id "
            "FROM fec_schedule_b_disbursements WHERE sub_id = 'TEST_SB_001'"
        ).fetchone()
        assert row is not None
        assert row["amendment_indicator"] == "N"
        assert row["disbursement_purpose_category"] == "ADVERTISING"
        assert row["file_number"] == "1919764"
        assert row["transaction_id"] == "SB17.4284"

    def test_schedule_e_carries_notice_and_lineage(self, db):
        """is_notice, most_recent, file_number, etc. should be stored."""
        from database.federal_fec import _upsert_schedule_e_rows

        rows = [{
            "sub_id": "TEST_SE_001",
            "expenditure_amount": 10000.0,
            "expenditure_date": "2025-10-01",
            "payee_name": "AD FIRM LLC",
            "support_oppose_indicator": "S",
            "is_notice": True,
            "most_recent": True,
            "file_number": "1560820",
            "previous_file_number": "1560800",
            "amendment_indicator": "N",
            "transaction_id": "F57.000001",
        }]
        _upsert_schedule_e_rows(
            db, rows=rows, candidate_id="H0IL01001",
            candidate_name="TEST", cycle=2026,
            api_source_identifier="test",
        )
        db.commit()

        row = db.execute(
            "SELECT is_notice, most_recent, file_number, previous_file_number, "
            "amendment_indicator, transaction_id "
            "FROM fec_schedule_e_independent_expenditures WHERE sub_id = 'TEST_SE_001'"
        ).fetchone()
        assert row is not None
        assert row["is_notice"] == 1
        assert row["most_recent"] == 1
        assert row["file_number"] == "1560820"
        assert row["previous_file_number"] == "1560800"
        assert row["amendment_indicator"] == "N"
        assert row["transaction_id"] == "F57.000001"

    def test_candidate_committees_carries_metadata(self, db):
        """last_file_date, first_file_date, party_full should be stored."""
        from database.federal_fec import _upsert_candidate_committees

        committees = [{
            "committee_id": "C00123456",
            "committee_name": "TEST COMMITTEE",
            "committee_type": "H",
            "committee_designation": "P",
            "committee_designation_full": "Principal campaign committee",
            "filing_frequency": "Q",
            "committee_party": "DEM",
            "committee_city": "CHICAGO",
            "committee_state": "IL",
            "committee_zip": "60601",
            "is_principal": 1,
            "source_payload_json": "{}",
            "last_file_date": "2026-01-31",
            "first_file_date": "2025-05-15",
            "party_full": "DEMOCRATIC PARTY",
        }]
        _upsert_candidate_committees(db, "H0IL01001", 2026, committees)
        db.commit()

        row = db.execute(
            "SELECT last_file_date, first_file_date, party_full "
            "FROM fec_candidate_committees WHERE committee_id = 'C00123456'"
        ).fetchone()
        assert row is not None
        assert row["last_file_date"] == "2026-01-31"
        assert row["first_file_date"] == "2025-05-15"
        assert row["party_full"] == "DEMOCRATIC PARTY"


# ===================================================================
# IRS 527 FEATURE TESTS
# ===================================================================

class TestIRS527Features:
    """Test IRS 527 reports timeline and contribution aggregates."""

    def _setup_527_data(self, db):
        """Insert minimal 527 test data."""
        db.execute("""
            CREATE TABLE IF NOT EXISTS irs527_organizations (
                ein TEXT, form_id INTEGER, form_id_seq INTEGER DEFAULT 0,
                org_name TEXT, address_1 TEXT, address_2 TEXT,
                city TEXT, state TEXT, zip TEXT, zip_ext TEXT,
                email TEXT, purpose TEXT, formation_date TEXT,
                custodian_name TEXT, contact_name TEXT,
                custodian_address_1 TEXT, custodian_address_2 TEXT,
                custodian_city TEXT, custodian_state TEXT,
                custodian_zip TEXT, custodian_zip_ext TEXT,
                contact_address_1 TEXT, contact_address_2 TEXT,
                contact_city TEXT, contact_state TEXT,
                contact_zip TEXT, contact_zip_ext TEXT,
                business_address_1 TEXT, business_address_2 TEXT,
                business_city TEXT, business_state TEXT,
                business_zip TEXT, business_zip_ext TEXT,
                material_change_date TEXT, insert_datetime TEXT,
                related_entity_bypass INTEGER, eain_bypass INTEGER,
                PRIMARY KEY(ein, form_id_seq)
            )
        """)
        db.execute("""
            INSERT OR REPLACE INTO irs527_organizations
            (ein, form_id, form_id_seq, org_name, city, state, zip, purpose)
            VALUES ('123456789', 100, 0, 'TEST 527 ORG', 'CHICAGO', 'IL', '60601',
                    'Political advocacy')
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS irs527_reports (
                form_id INTEGER, ein TEXT,
                period_start TEXT, period_end TEXT,
                org_name TEXT, org_ein TEXT,
                org_address_1 TEXT, org_address_2 TEXT,
                org_city TEXT, org_state TEXT, org_zip TEXT, org_zip_ext TEXT,
                email TEXT, formation_date TEXT,
                custodian_name TEXT,
                custodian_address_1 TEXT, custodian_address_2 TEXT,
                custodian_city TEXT, custodian_state TEXT,
                custodian_zip TEXT, custodian_zip_ext TEXT,
                contact_name TEXT, contact_address_1 TEXT, contact_address_2 TEXT,
                contact_city TEXT, contact_state TEXT,
                contact_zip TEXT, contact_zip_ext TEXT,
                business_address_1 TEXT, business_address_2 TEXT,
                business_city TEXT, business_state TEXT,
                business_zip TEXT, business_zip_ext TEXT,
                qtr_indicator INTEGER,
                monthly_amount_1 REAL, monthly_amount_2 REAL, monthly_amount_3 REAL,
                total_contributions REAL, total_expenditures REAL,
                insert_datetime TEXT,
                PRIMARY KEY(form_id, ein)
            )
        """)
        db.execute("""
            INSERT OR REPLACE INTO irs527_reports
            (form_id, ein, period_start, period_end, total_contributions,
             total_expenditures, qtr_indicator, insert_datetime)
            VALUES (200, '123456789', '2025-01-01', '2025-03-31', 50000.0,
                    30000.0, 1, '2025-04-15 10:00:00')
        """)
        db.execute("""
            INSERT OR REPLACE INTO irs527_reports
            (form_id, ein, period_start, period_end, total_contributions,
             total_expenditures, qtr_indicator, insert_datetime)
            VALUES (201, '123456789', '2025-04-01', '2025-06-30', 75000.0,
                    45000.0, 2, '2025-07-15 10:00:00')
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS irs527_contributions (
                rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id INTEGER, ein TEXT, org_name TEXT,
                contributor_name TEXT, contributor_address TEXT,
                contributor_address_2 TEXT, city TEXT, state TEXT,
                zip TEXT, zip_ext TEXT, contributor_employer TEXT,
                amount REAL, contributor_occupation TEXT, date TEXT
            )
        """)
        db.execute("""
            INSERT INTO irs527_contributions
            (form_id, ein, org_name, contributor_name, city, state,
             contributor_employer, amount, contributor_occupation, date)
            VALUES (200, '123456789', 'TEST 527 ORG', 'BIG DONOR LLC',
                    'SPRINGFIELD', 'IL', 'ACME CORP', 25000.0, 'CEO', '2025-02-15')
        """)
        db.execute("""
            INSERT INTO irs527_contributions
            (form_id, ein, org_name, contributor_name, city, state,
             contributor_employer, amount, contributor_occupation, date)
            VALUES (200, '123456789', 'TEST 527 ORG', 'BIG DONOR LLC',
                    'SPRINGFIELD', 'IL', 'ACME CORP', 15000.0, 'CEO', '2025-03-01')
        """)
        db.execute("""
            INSERT INTO irs527_contributions
            (form_id, ein, org_name, contributor_name, city, state,
             contributor_employer, amount, contributor_occupation, date)
            VALUES (201, '123456789', 'TEST 527 ORG', 'SMALL DONOR',
                    'CHICAGO', 'IL', 'WIDGETS INC', 5000.0, 'ENGINEER', '2025-05-01')
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS irs527_expenditures (
                rowid_local INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id INTEGER, ein TEXT, org_name TEXT,
                recipient_name TEXT, recipient_address TEXT,
                recipient_address_2 TEXT, city TEXT, state TEXT,
                zip TEXT, zip_ext TEXT, recipient_employer TEXT,
                amount REAL, recipient_occupation TEXT, date TEXT, purpose TEXT
            )
        """)
        db.execute("""
            INSERT INTO irs527_expenditures
            (form_id, ein, org_name, recipient_name, city, state,
             amount, date, purpose)
            VALUES (200, '123456789', 'TEST 527 ORG', 'AD FIRM LLC',
                    'CHICAGO', 'IL', 20000.0, '2025-03-15', 'ADVERTISING')
        """)
        db.commit()

    def test_reports_query_returns_timeline(self, db):
        """Reports timeline query should return individual reports with period dates."""
        self._setup_527_data(db)

        reports = db.execute("""
            SELECT form_id, period_start, period_end,
                   COALESCE(total_contributions, 0) AS total_contributions,
                   COALESCE(total_expenditures, 0) AS total_expenditures,
                   qtr_indicator, insert_datetime
            FROM irs527_reports
            WHERE ein = '123456789'
            ORDER BY period_end DESC
        """).fetchall()

        assert len(reports) == 2
        assert reports[0]["period_end"] == "2025-06-30"
        assert reports[0]["total_contributions"] == 75000.0
        assert reports[1]["period_start"] == "2025-01-01"
        assert reports[1]["qtr_indicator"] == 1

    def test_top_contributors_aggregate(self, db):
        """Top contributors query should aggregate by contributor name."""
        self._setup_527_data(db)

        top = db.execute("""
            SELECT contributor_name,
                   COALESCE(SUM(amount), 0) AS total_amount,
                   COUNT(*) AS contribution_count
            FROM irs527_contributions
            WHERE ein = '123456789'
              AND contributor_name IS NOT NULL AND TRIM(contributor_name) != ''
            GROUP BY contributor_name
            ORDER BY total_amount DESC
            LIMIT 10
        """).fetchall()

        assert len(top) == 2
        assert top[0]["contributor_name"] == "BIG DONOR LLC"
        assert top[0]["total_amount"] == 40000.0
        assert top[0]["contribution_count"] == 2
        assert top[1]["contributor_name"] == "SMALL DONOR"
        assert top[1]["total_amount"] == 5000.0

    def test_top_recipients_aggregate(self, db):
        """Top recipients query should aggregate expenditure recipients."""
        self._setup_527_data(db)

        top = db.execute("""
            SELECT recipient_name,
                   COALESCE(SUM(amount), 0) AS total_amount,
                   COUNT(*) AS expenditure_count
            FROM irs527_expenditures
            WHERE ein = '123456789'
              AND recipient_name IS NOT NULL AND TRIM(recipient_name) != ''
            GROUP BY recipient_name
            ORDER BY total_amount DESC
            LIMIT 10
        """).fetchall()

        assert len(top) == 1
        assert top[0]["recipient_name"] == "AD FIRM LLC"
        assert top[0]["total_amount"] == 20000.0

    def test_contribution_search_filters(self, db):
        """Contribution search by contributor name should filter results."""
        self._setup_527_data(db)

        results = db.execute("""
            SELECT contributor_name, amount
            FROM irs527_contributions
            WHERE ein = '123456789'
              AND contributor_name LIKE '%BIG%'
            ORDER BY amount DESC
        """).fetchall()

        assert len(results) == 2
        assert all("BIG" in r["contributor_name"] for r in results)


# ===================================================================
# FIELD POPULATION TESTS (would fail if ingestion stops populating)
# ===================================================================

class TestFieldPopulation:
    """Tests that would fail if a newly integrated field stops being populated."""

    def test_amendment_indicator_not_null_for_valid_payload(self, db):
        """If FEC API provides amendment_indicator, it must not be silently dropped."""
        from database.federal_fec import _upsert_schedule_rows

        _upsert_schedule_rows(
            db,
            rows=[{
                "sub_id": "AMEND_TEST_001",
                "contribution_receipt_amount": 50.0,
                "contribution_receipt_date": "2025-06-01",
                "contributor_name": "DONOR",
                "amendment_indicator": "N",
            }],
            candidate_id="H0IL05001",
            candidate_name="TEST",
            cycle=2026,
            committee_id="C00000001",
            default_committee_name="TEST COMMITTEE",
            api_source_identifier="test",
        )
        db.commit()

        val = db.execute(
            "SELECT amendment_indicator FROM fec_schedule_a_contributions WHERE sub_id = 'AMEND_TEST_001'"
        ).fetchone()["amendment_indicator"]
        assert val == "N", "amendment_indicator must be populated when provided in payload"

    def test_disbursement_purpose_category_not_null_for_valid_payload(self, db):
        """disbursement_purpose_category must be populated when present."""
        from database.federal_fec import _upsert_schedule_b_rows

        _upsert_schedule_b_rows(
            db,
            rows=[{
                "sub_id": "PURPOSE_TEST_001",
                "disbursement_amount": 200.0,
                "disbursement_date": "2025-06-01",
                "recipient_name": "VENDOR",
                "disbursement_purpose_category": "FUNDRAISING",
            }],
            candidate_id="H0IL05001",
            candidate_name="TEST",
            cycle=2026,
            committee_id="C00000001",
            default_committee_name="TEST COMMITTEE",
            api_source_identifier="test",
        )
        db.commit()

        val = db.execute(
            "SELECT disbursement_purpose_category FROM fec_schedule_b_disbursements "
            "WHERE sub_id = 'PURPOSE_TEST_001'"
        ).fetchone()["disbursement_purpose_category"]
        assert val == "FUNDRAISING", "disbursement_purpose_category must be populated when provided"

    def test_is_notice_boolean_preserved(self, db):
        """is_notice boolean from FEC API must be stored as integer 0/1."""
        from database.federal_fec import _upsert_schedule_e_rows

        _upsert_schedule_e_rows(
            db,
            rows=[{
                "sub_id": "NOTICE_TEST_001",
                "expenditure_amount": 1000.0,
                "expenditure_date": "2025-10-15",
                "payee_name": "MEDIA CO",
                "support_oppose_indicator": "O",
                "is_notice": False,
                "most_recent": True,
            }],
            candidate_id="H0IL05001",
            candidate_name="TEST",
            cycle=2026,
            api_source_identifier="test",
        )
        db.commit()

        row = db.execute(
            "SELECT is_notice, most_recent FROM fec_schedule_e_independent_expenditures "
            "WHERE sub_id = 'NOTICE_TEST_001'"
        ).fetchone()
        assert row["is_notice"] == 0, "is_notice=False should be stored as 0"
        assert row["most_recent"] == 1, "most_recent=True should be stored as 1"

    def test_party_full_preserved(self, db):
        """party_full must be stored when provided."""
        from database.federal_fec import _upsert_candidate_committees

        _upsert_candidate_committees(db, "H0IL05001", 2026, [{
            "committee_id": "C00999888",
            "committee_name": "PARTY TEST",
            "is_principal": 0,
            "party_full": "REPUBLICAN PARTY",
        }])
        db.commit()

        val = db.execute(
            "SELECT party_full FROM fec_candidate_committees WHERE committee_id = 'C00999888'"
        ).fetchone()["party_full"]
        assert val == "REPUBLICAN PARTY"
