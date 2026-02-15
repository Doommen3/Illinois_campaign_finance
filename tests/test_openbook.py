"""Tests for OpenBook Illinois Comptroller scraper and ingestion."""
import os
import sqlite3
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from scraper.openbook_scraper import (
    OpenBookScraper,
    _HttpSession,
    _pick_best_match,
    parse_contracts_html,
    parse_contract_detail_html,
    parse_contributions_html,
    _hash_row,
    _parse_currency,
    _clean,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "openbook"


# ---------------------------------------------------------------------------
# Unit tests for parsing helpers
# ---------------------------------------------------------------------------


class TestParseHelpers:
    def test_parse_currency_normal(self):
        assert _parse_currency("$5,310,000.00") == 5310000.00

    def test_parse_currency_zero(self):
        assert _parse_currency("$0.00") == 0.0

    def test_parse_currency_none(self):
        assert _parse_currency("") is None
        assert _parse_currency(None) is None

    def test_parse_currency_negative(self):
        assert _parse_currency("-$1,234.56") == -1234.56

    def test_clean_whitespace(self):
        assert _clean("  COMCAST CORPORATION             ") == "COMCAST CORPORATION"
        assert _clean("  hello   world  ") == "hello world"
        assert _clean("") == ""

    def test_hash_row_deterministic(self):
        h1 = _hash_row(["a", "b", "c"])
        h2 = _hash_row(["a", "b", "c"])
        assert h1 == h2
        assert len(h1) == 40  # SHA-1 hex

    def test_hash_row_different_input(self):
        h1 = _hash_row(["a", "b", "c"])
        h2 = _hash_row(["a", "b", "d"])
        assert h1 != h2


# ---------------------------------------------------------------------------
# Contract parsing tests
# ---------------------------------------------------------------------------


class TestParseContracts:
    @pytest.fixture
    def contracts_html(self):
        return (FIXTURES_DIR / "contracts_comcast.html").read_text()

    def test_parse_contracts_count(self, contracts_html):
        contracts = parse_contracts_html(contracts_html, "COMCAST")
        assert len(contracts) == 5

    def test_parse_contracts_fields(self, contracts_html):
        contracts = parse_contracts_html(contracts_html, "COMCAST")
        first = contracts[0]
        assert first["vendor_label"] == "COMCAST OF ILLINOIS III INC"
        assert first["fiscal_year"] == 2026
        assert first["agency_code"] == "420"
        assert first["contract_number"] == "60021431070"
        assert first["award_amount"] == 5310000.00
        assert first["agency_name"] == "COMMERCE AND ECONOMIC OPPORTUN"
        assert first["detail_url"] is not None
        assert "Vendor_Warrant_Listing" in first["detail_url"]
        assert first["row_hash"] is not None

    def test_parse_contracts_zero_amount(self, contracts_html):
        contracts = parse_contracts_html(contracts_html, "COMCAST")
        # 4th contract has $0.00
        zero_contract = [c for c in contracts if c["contract_number"] == "400004T0279"]
        assert len(zero_contract) == 1
        assert zero_contract[0]["award_amount"] == 0.0

    def test_parse_contracts_no_detail_link(self, contracts_html):
        contracts = parse_contracts_html(contracts_html, "COMCAST")
        # 4th contract has no onclick detail link
        no_link = [c for c in contracts if c["contract_number"] == "400004T0279"]
        assert len(no_link) == 1
        assert no_link[0]["detail_url"] is None

    def test_parse_contracts_dedupe_hashes_unique(self, contracts_html):
        contracts = parse_contracts_html(contracts_html, "COMCAST")
        hashes = [c["row_hash"] for c in contracts]
        assert len(hashes) == len(set(hashes)), "All row hashes should be unique"

    def test_parse_contracts_empty_html(self):
        contracts = parse_contracts_html("<html><body></body></html>", "TEST")
        assert contracts == []


# ---------------------------------------------------------------------------
# Contribution parsing tests
# ---------------------------------------------------------------------------


class TestParseContributions:
    @pytest.fixture
    def contributions_html(self):
        return (FIXTURES_DIR / "contributions_with_data.html").read_text()

    @pytest.fixture
    def empty_contributions_html(self):
        return (FIXTURES_DIR / "contributions_empty.html").read_text()

    def test_parse_contributions_count(self, contributions_html):
        contribs = parse_contributions_html(contributions_html, "ACME")
        assert len(contribs) == 3

    def test_parse_contributions_fields(self, contributions_html):
        contribs = parse_contributions_html(contributions_html, "ACME")
        first = contribs[0]
        assert first["contributor_name"] == "SMITH, JOHN"
        assert first["recipient_name"] == "FRIENDS OF GOVERNOR"
        assert first["employer"] == "ACME CORP"
        assert first["contribution_date"] == "01/15/2025"
        assert first["amount"] == 5000.00
        assert first["row_hash"] is not None

    def test_parse_contributions_empty(self, empty_contributions_html):
        contribs = parse_contributions_html(empty_contributions_html, "TEST")
        assert contribs == []

    def test_parse_contributions_dedupe_hashes(self, contributions_html):
        contribs = parse_contributions_html(contributions_html, "ACME")
        hashes = [c["row_hash"] for c in contribs]
        assert len(hashes) == len(set(hashes))


# ---------------------------------------------------------------------------
# Contract detail popup parsing tests
# ---------------------------------------------------------------------------


class TestParseContractDetails:
    @pytest.fixture
    def detail_with_data_html(self):
        return (FIXTURES_DIR / "contract_detail_with_data.html").read_text()

    @pytest.fixture
    def detail_empty_html(self):
        return (FIXTURES_DIR / "contract_detail_empty.html").read_text()

    def test_parse_contract_detail_with_rows(self, detail_with_data_html):
        rows = parse_contract_detail_html(
            detail_with_data_html,
            vendor_key="ALIVIO MEDICAL CENTER",
            contract_number="C-123",
            fiscal_year=2026,
        )
        assert len(rows) == 3
        assert rows[0]["issue_date"] == "1/22/26"
        assert rows[0]["payment_amount"] == 20833.26

    def test_parse_contract_detail_empty(self, detail_empty_html):
        rows = parse_contract_detail_html(
            detail_empty_html,
            vendor_key="ALIVIO MEDICAL CENTER",
            contract_number="C-123",
            fiscal_year=2026,
        )
        assert rows == []


# ---------------------------------------------------------------------------
# Database persistence tests
# ---------------------------------------------------------------------------


class TestOpenBookDB:
    @pytest.fixture
    def db_conn(self, tmp_path):
        """Create a temp database with schema."""
        db_path = str(tmp_path / "test_openbook.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_init_db_creates_openbook_tables(self, db_conn):
        """Verify init-db creates the new OpenBook tables."""
        tables = [
            "openbook_vendor_seed",
            "openbook_vendor_match",
            "openbook_contracts_raw",
            "openbook_contract_warrants",
            "openbook_contract_detail_status",
            "openbook_contributions_raw",
            "openbook_scrape_runs",
        ]
        for table in tables:
            row = db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"Table {table} should exist"

    def test_save_contracts_insert(self, db_conn):
        """Test inserting contracts with idempotent upserts."""
        scraper = OpenBookScraper(db_conn, headless=True)
        contracts = [
            {
                "vendor_label": "TEST CORP",
                "fiscal_year": 2026,
                "agency_code": "420",
                "agency_name": "COMMERCE",
                "contract_number": "C001",
                "award_amount": 100000.00,
                "detail_url": "https://example.com/detail",
                "row_hash": _hash_row(["TEST", "TEST CORP", "2026", "420", "C001", "100000.0"]),
            }
        ]
        ins, upd = scraper.save_contracts("TEST", contracts, "https://example.com")
        assert ins == 1
        assert upd == 0

        # Verify in DB
        row = db_conn.execute(
            "SELECT * FROM openbook_contracts_raw WHERE contract_number = 'C001'"
        ).fetchone()
        assert row is not None
        assert row["award_amount"] == 100000.00

    def test_save_contracts_idempotent(self, db_conn):
        """Test that re-inserting the same contract doesn't create duplicates."""
        scraper = OpenBookScraper(db_conn, headless=True)
        contracts = [
            {
                "vendor_label": "TEST CORP",
                "fiscal_year": 2026,
                "agency_code": "420",
                "agency_name": "COMMERCE",
                "contract_number": "C001",
                "award_amount": 100000.00,
                "detail_url": None,
                "row_hash": _hash_row(["TEST", "TEST CORP", "2026", "420", "C001", "100000.0"]),
            }
        ]
        scraper.save_contracts("TEST", contracts, "https://example.com")
        scraper.save_contracts("TEST", contracts, "https://example.com")

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_contracts_raw WHERE contract_number = 'C001'"
        ).fetchone()["cnt"]
        assert count == 1

    def test_save_contracts_update_on_change(self, db_conn):
        """Test that changed data triggers an update."""
        scraper = OpenBookScraper(db_conn, headless=True)
        contracts_v1 = [
            {
                "vendor_label": "TEST CORP",
                "fiscal_year": 2026,
                "agency_code": "420",
                "agency_name": "COMMERCE",
                "contract_number": "C002",
                "award_amount": 50000.00,
                "detail_url": None,
                "row_hash": _hash_row(["TEST", "TEST CORP", "2026", "420", "C002", "50000.0"]),
            }
        ]
        ins1, upd1 = scraper.save_contracts("TEST", contracts_v1, "https://example.com")
        assert ins1 == 1 and upd1 == 0

        # Update amount
        contracts_v2 = [
            {
                "vendor_label": "TEST CORP",
                "fiscal_year": 2026,
                "agency_code": "420",
                "agency_name": "COMMERCE",
                "contract_number": "C002",
                "award_amount": 75000.00,
                "detail_url": None,
                "row_hash": _hash_row(["TEST", "TEST CORP", "2026", "420", "C002", "75000.0"]),
            }
        ]
        ins2, upd2 = scraper.save_contracts("TEST", contracts_v2, "https://example.com")
        assert ins2 == 0 and upd2 == 1

        row = db_conn.execute(
            "SELECT award_amount FROM openbook_contracts_raw WHERE contract_number = 'C002'"
        ).fetchone()
        assert row["award_amount"] == 75000.00

    def test_save_contributions_insert(self, db_conn):
        """Test inserting contributions."""
        scraper = OpenBookScraper(db_conn, headless=True)
        contributions = [
            {
                "contributor_name": "SMITH, JOHN",
                "contributor_first_name": None,
                "recipient_name": "FRIENDS OF GOV",
                "employer": "ACME",
                "contribution_date": "01/15/2025",
                "amount": 5000.00,
                "row_hash": _hash_row(["VENDOR", "SMITH, JOHN", "FRIENDS OF GOV", "01/15/2025", "5000.0"]),
            }
        ]
        ins, upd = scraper.save_contributions("VENDOR", contributions, "https://example.com")
        assert ins == 1
        assert upd == 0

    def test_save_contributions_idempotent(self, db_conn):
        """Test that re-inserting the same contribution doesn't create duplicates."""
        scraper = OpenBookScraper(db_conn, headless=True)
        rh = _hash_row(["VENDOR", "DOE, JANE", "COMMITTEE", "03/01/2025", "1000.0"])
        contributions = [
            {
                "contributor_name": "DOE, JANE",
                "contributor_first_name": None,
                "recipient_name": "COMMITTEE",
                "employer": "ACME",
                "contribution_date": "03/01/2025",
                "amount": 1000.00,
                "row_hash": rh,
            }
        ]
        scraper.save_contributions("VENDOR", contributions, "https://example.com")
        scraper.save_contributions("VENDOR", contributions, "https://example.com")

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_contributions_raw WHERE row_hash = ?",
            (rh,),
        ).fetchone()["cnt"]
        assert count == 1

    def test_seed_and_match_persistence(self, db_conn):
        """Test seed and match record creation."""
        scraper = OpenBookScraper(db_conn, headless=True)
        seed_id = scraper._ensure_seed("COMCAST", "manual")
        assert seed_id > 0

        # Idempotent
        seed_id2 = scraper._ensure_seed("COMCAST", "manual")
        assert seed_id2 == seed_id

        match_id = scraper._ensure_match(seed_id, {
            "vendor_key": "COMCAST",
            "vendor_label": "COMCAST",
            "match_method": "exact",
            "confidence": 1.0,
        })
        assert match_id > 0

        # Idempotent
        match_id2 = scraper._ensure_match(seed_id, {
            "vendor_key": "COMCAST",
            "vendor_label": "COMCAST",
            "match_method": "exact",
            "confidence": 1.0,
        })
        assert match_id2 == match_id

    def test_scrape_run_lifecycle(self, db_conn):
        """Test creating and completing a scrape run."""
        scraper = OpenBookScraper(db_conn, headless=True)
        run_id = scraper.create_run("vendor_poc")
        assert run_id > 0

        scraper.complete_run(run_id, 1, 1, 10, 0, 0, "test run")

        row = db_conn.execute(
            "SELECT * FROM openbook_scrape_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row["completed_at"] is not None
        assert row["contract_rows"] == 10

    def test_save_raw_extraction(self, db_conn):
        """Test raw extraction persistence."""
        scraper = OpenBookScraper(db_conn, headless=True)
        scraper.save_raw_extraction(
            "openbook_contracts_search",
            "TEST:contracts:1",
            {"test": "data"},
            "https://example.com",
        )

        row = db_conn.execute(
            "SELECT * FROM raw_extractions WHERE source_type = 'openbook_contracts_search'"
        ).fetchone()
        assert row is not None
        assert '"test"' in row["payload_json"]

    def test_save_contract_warrants_and_status(self, db_conn):
        scraper = OpenBookScraper(db_conn, headless=True)
        warrants = [
            {
                "issue_date": "1/22/26",
                "payment_amount": 20833.26,
                "row_hash": _hash_row(["ALIVIO", "C-1", "2026", "1/22/26", "20833.26"]),
            }
        ]

        ins, upd = scraper.save_contract_warrants(
            vendor_key="ALIVIO",
            contract_number="C-1",
            fiscal_year=2026,
            detail_url="https://office.illinoiscomptroller.gov/detail",
            warrants=warrants,
            source_url="https://office.illinoiscomptroller.gov/detail",
        )
        assert ins == 1
        assert upd == 0

        scraper.upsert_contract_detail_status(
            vendor_key="ALIVIO",
            contract_number="C-1",
            fiscal_year=2026,
            detail_url="https://office.illinoiscomptroller.gov/detail",
            status="has_data",
            warrant_row_count=1,
        )

        state = scraper._detail_fetch_state(
            vendor_key="ALIVIO",
            contract_number="C-1",
            fiscal_year=2026,
            max_error_retries=2,
        )
        assert state == "skip_has_data"

    def test_detail_state_skips_no_data(self, db_conn):
        scraper = OpenBookScraper(db_conn, headless=True)
        scraper.upsert_contract_detail_status(
            vendor_key="ALIVIO",
            contract_number="C-2",
            fiscal_year=2026,
            detail_url="https://office.illinoiscomptroller.gov/detail2",
            status="no_data",
            warrant_row_count=0,
        )
        state = scraper._detail_fetch_state(
            vendor_key="ALIVIO",
            contract_number="C-2",
            fiscal_year=2026,
            max_error_retries=2,
        )
        assert state == "skip_no_data"

    def test_full_parse_and_save_pipeline(self, db_conn):
        """End-to-end test: parse fixture HTML and save to DB."""
        scraper = OpenBookScraper(db_conn, headless=True)
        html = (FIXTURES_DIR / "contracts_comcast.html").read_text()
        contracts = parse_contracts_html(html, "COMCAST")

        ins, upd = scraper.save_contracts("COMCAST", contracts, "https://openbook.illinoiscomptroller.gov/search.cfm")
        assert ins == 5
        assert upd == 0

        # Verify all rows
        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_contracts_raw WHERE openbook_vendor_key = 'COMCAST'"
        ).fetchone()["cnt"]
        assert count == 5

        # Re-run should not create duplicates
        ins2, upd2 = scraper.save_contracts("COMCAST", contracts, "https://openbook.illinoiscomptroller.gov/search.cfm")
        assert ins2 == 0
        assert upd2 == 0

        count2 = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_contracts_raw WHERE openbook_vendor_key = 'COMCAST'"
        ).fetchone()["cnt"]
        assert count2 == 5


# ---------------------------------------------------------------------------
# _pick_best_match tests
# ---------------------------------------------------------------------------


class TestPickBestMatch:
    def test_exact_match(self):
        suggestions = [
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
            {"id": "COMCAST OF ILLINOIS", "value": "Comcast of Illinois"},
        ]
        result = _pick_best_match("COMCAST CORPORATION", suggestions)
        assert result is not None
        assert result["match_method"] == "exact"
        assert result["confidence"] == 1.0
        assert result["vendor_key"] == "COMCAST CORPORATION"

    def test_prefix_match(self):
        suggestions = [
            {"id": "ACME CORPORATION INC", "value": "Acme Corporation Inc"},
        ]
        result = _pick_best_match("ACME CORPORATION", suggestions)
        assert result is not None
        assert result["match_method"] == "prefix"
        assert result["confidence"] == 0.9

    def test_fuzzy_match(self):
        suggestions = [
            {"id": "XYZ INDUSTRIES GROUP LLC", "value": "XYZ Industries Group LLC"},
        ]
        # "XYZ INDUSTRIES GROUP" vs "XYZ INDUSTRIES GROUP LLC" => prefix match
        # Use tokens that overlap but aren't a prefix to test fuzzy:
        # "INDUSTRIES XYZ" vs "XYZ INDUSTRIES GROUP LLC" => Jaccard 2/4 = 0.5
        suggestions2 = [
            {"id": "XYZ INDUSTRIES GROUP LLC", "value": "XYZ Industries Group LLC"},
        ]
        result = _pick_best_match("INDUSTRIES XYZ", suggestions2)
        assert result is not None
        assert result["match_method"] == "fuzzy"
        assert result["confidence"] >= 0.5

    def test_no_match_below_threshold(self):
        suggestions = [
            {"id": "TOTALLY DIFFERENT NAME", "value": "Totally Different Name"},
        ]
        result = _pick_best_match("ABC CORPORATION", suggestions)
        assert result is None

    def test_pick_first_fallback(self):
        suggestions = [
            {"id": "UNRELATED VENDOR", "value": "Unrelated Vendor"},
        ]
        result = _pick_best_match("ABC CORPORATION", suggestions, pick_first=True)
        assert result is not None
        assert result["match_method"] == "pick_first"
        assert result["confidence"] == 0.5
        assert result["vendor_key"] == "UNRELATED VENDOR"

    def test_empty_suggestions(self):
        result = _pick_best_match("ANYTHING", [])
        assert result is None

    def test_none_suggestions(self):
        result = _pick_best_match("ANYTHING", None)
        assert result is None


# ---------------------------------------------------------------------------
# No-match sentinel tests
# ---------------------------------------------------------------------------


class TestNoMatchSentinel:
    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_nomatch.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_record_no_match(self, db_conn):
        scraper = OpenBookScraper(db_conn, headless=True)
        seed_id = scraper._ensure_seed("NO MATCH VENDOR", "manual")
        scraper._record_no_match(seed_id)

        row = db_conn.execute(
            "SELECT * FROM openbook_vendor_match WHERE seed_id = ?", (seed_id,)
        ).fetchone()
        assert row is not None
        assert row["match_method"] == "no_match"
        assert row["openbook_vendor_key"] == ""
        assert row["confidence"] == 0.0

    def test_record_no_match_idempotent(self, db_conn):
        scraper = OpenBookScraper(db_conn, headless=True)
        seed_id = scraper._ensure_seed("NO MATCH VENDOR 2", "manual")
        scraper._record_no_match(seed_id)
        scraper._record_no_match(seed_id)  # second call should not fail

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_vendor_match WHERE seed_id = ? AND match_method = 'no_match'",
            (seed_id,),
        ).fetchone()["cnt"]
        assert count == 1


# ---------------------------------------------------------------------------
# Seed generation tests
# ---------------------------------------------------------------------------


class TestSeedGeneration:
    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_seeds.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_seeds_from_expenditures(self, db_conn):
        """Test seed generation from d2_itemized_entries."""
        # Insert committee first (FK for d2_reports)
        db_conn.execute(
            "INSERT INTO committees (id, name) VALUES (1, 'Test Committee')"
        )
        db_conn.execute(
            """INSERT INTO d2_reports (committee_id, report_type, reporting_period)
               VALUES (1, 'test', 'Q1')"""
        )
        db_conn.execute(
            """INSERT INTO d2_itemized_links (d2_report_id, itemized_type, url, source_identifier)
               VALUES (1, 'expenditure', 'http://test', 'test_link_1')"""
        )
        db_conn.execute(
            """INSERT INTO d2_itemized_entries
               (d2_report_id, itemized_link_id, source_page, source_row, row_hash,
                entry_type, vendor_name, amount)
               VALUES (1, 1, 1, 1, 'hash1', 'expenditure', 'BIG VENDOR CORP', 50000.0)"""
        )
        db_conn.execute(
            """INSERT INTO d2_itemized_entries
               (d2_report_id, itemized_link_id, source_page, source_row, row_hash,
                entry_type, vendor_name, amount)
               VALUES (1, 1, 1, 2, 'hash2', 'expenditure', 'SMALL VENDOR', 500.0)"""
        )
        db_conn.commit()

        stats = OpenBookScraper.generate_seeds(
            db_conn,
            min_amount=10000.0,
            limit_per_source=100,
            sources="expenditures",
        )
        assert stats["expenditures_seeds"] == 1  # Only BIG VENDOR CORP
        assert stats["total_new_seeds"] == 1

        # Verify the seed
        row = db_conn.execute(
            "SELECT * FROM openbook_vendor_seed WHERE seed_text = 'BIG VENDOR CORP'"
        ).fetchone()
        assert row is not None
        assert row["seed_source"] == "expenditures"

    def test_seeds_idempotent(self, db_conn):
        """Re-running seed generation doesn't create duplicates."""
        db_conn.execute(
            "INSERT INTO committees (id, name) VALUES (1, 'Test Committee')"
        )
        db_conn.execute(
            """INSERT INTO d2_reports (committee_id, report_type, reporting_period)
               VALUES (1, 'test', 'Q1')"""
        )
        db_conn.execute(
            """INSERT INTO d2_itemized_links (d2_report_id, itemized_type, url, source_identifier)
               VALUES (1, 'expenditure', 'http://test', 'test_link_1')"""
        )
        db_conn.execute(
            """INSERT INTO d2_itemized_entries
               (d2_report_id, itemized_link_id, source_page, source_row, row_hash,
                entry_type, vendor_name, amount)
               VALUES (1, 1, 1, 1, 'hash1', 'expenditure', 'REPEAT VENDOR', 20000.0)"""
        )
        db_conn.commit()

        stats1 = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="expenditures",
        )
        assert stats1["expenditures_seeds"] == 1

        stats2 = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="expenditures",
        )
        assert stats2["expenditures_seeds"] == 0  # No new seeds
        assert stats2["total_new_seeds"] == 0

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_vendor_seed WHERE seed_text = 'REPEAT VENDOR'"
        ).fetchone()["cnt"]
        assert count == 1

    def test_seeds_missing_tables(self, db_conn):
        """Gracefully handles missing source tables."""
        # Drop tables that might not exist in minimal schema
        # lobbying_entities might exist from init_db, but fec might not have data
        stats = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="all",
        )
        # Should not raise, all counts should be 0 (no data rows)
        assert stats["total_new_seeds"] == 0

    def test_seeds_from_lobbying(self, db_conn):
        """Test seed generation from lobbying_entities."""
        db_conn.execute(
            "INSERT INTO lobbying_entities (entity_id, entity_name) VALUES (1, 'AT&T ILLINOIS')"
        )
        db_conn.execute(
            "INSERT INTO lobbying_entities (entity_id, entity_name) VALUES (2, 'COMCAST CORP')"
        )
        db_conn.commit()

        stats = OpenBookScraper.generate_seeds(
            db_conn, min_amount=0, limit_per_source=100, sources="lobbying",
        )
        assert stats["lobbying_seeds"] == 2
        assert stats["total_new_seeds"] == 2


# ---------------------------------------------------------------------------
# _HttpSession tests
# ---------------------------------------------------------------------------


class TestHttpSession:
    def test_session_creation(self):
        """Test that _HttpSession can be created without errors."""
        session = _HttpSession(timeout=10)
        assert session.timeout == 10
        assert session._cj is not None
        assert session._opener is not None

    def test_session_default_timeout(self):
        session = _HttpSession()
        assert session.timeout == 30


# ---------------------------------------------------------------------------
# save_raw_extraction Postgres-compat test
# ---------------------------------------------------------------------------


class TestRawExtractionPostgresCompat:
    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_rawext.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_save_raw_extraction_upsert(self, db_conn):
        """save_raw_extraction now uses check-then-insert (no INSERT OR REPLACE)."""
        scraper = OpenBookScraper(db_conn, headless=True)

        # First insert
        scraper.save_raw_extraction(
            "openbook_test", "key1", {"version": 1}, "http://test"
        )
        row = db_conn.execute(
            "SELECT payload_json FROM raw_extractions WHERE source_identifier = 'key1'"
        ).fetchone()
        assert '"version": 1' in row["payload_json"]

        # Update with same key
        scraper.save_raw_extraction(
            "openbook_test", "key1", {"version": 2}, "http://test"
        )
        row = db_conn.execute(
            "SELECT payload_json FROM raw_extractions WHERE source_identifier = 'key1'"
        ).fetchone()
        assert '"version": 2' in row["payload_json"]

        # Verify only one row
        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM raw_extractions WHERE source_identifier = 'key1'"
        ).fetchone()["cnt"]
        assert count == 1
