"""Tests for Comptroller State Contracts scraper and ingestion.

TDD: Tests written before implementation.
Target: https://illinoiscomptroller.gov/financial-reports-data/find-a-report/state-contracts
"""
import sqlite3
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from scraper.comptroller_contracts import (
    extract_primary_identifier,
    parse_search_results_html,
    parse_contract_detail_html,
    ComptrollerContractsScraper,
)
from scraper.openbook_scraper import _hash_row, _parse_currency, _clean

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "comptroller"


# ---------------------------------------------------------------------------
# extract_primary_identifier tests
# ---------------------------------------------------------------------------


class TestExtractPrimaryIdentifier:
    """Test extraction of short search terms from full vendor names."""

    def test_simple_corp_suffix(self):
        """Remove INC/CORP/LLC suffixes and return primary name."""
        assert extract_primary_identifier("COMCAST OF ILLINOIS III INC") == "COMCAST"

    def test_llc_suffix(self):
        assert extract_primary_identifier("COMCAST BUSINESS COMMUNICATIONS LLC") == "COMCAST"

    def test_brand_with_geographic(self):
        """Remove geographic modifiers like 'OF ILLINOIS'."""
        assert extract_primary_identifier("AT&T OF ILLINOIS") == "AT&T"

    def test_short_name_passthrough(self):
        """Single-word names should pass through unchanged."""
        assert extract_primary_identifier("DELOITTE") == "DELOITTE"

    def test_two_word_brand(self):
        """Recognized multi-word brands should be kept together."""
        result = extract_primary_identifier("BLUE CROSS BLUE SHIELD OF ILLINOIS")
        # Should return at least "BLUE CROSS" - the primary brand
        assert "BLUE CROSS" in result

    def test_empty_input(self):
        assert extract_primary_identifier("") == ""

    def test_none_input(self):
        assert extract_primary_identifier(None) == ""

    def test_whitespace_handling(self):
        assert extract_primary_identifier("  COMCAST   CORP  ") == "COMCAST"

    def test_preserves_ampersand(self):
        """Vendor names with & should preserve them."""
        result = extract_primary_identifier("AT&T SERVICES INC")
        assert "AT&T" in result

    def test_removes_roman_numerals(self):
        """Remove trailing roman numerals like III, IV."""
        result = extract_primary_identifier("COMCAST OF ILLINOIS III INC")
        assert "III" not in result

    def test_numeric_suffix_preserved(self):
        """Numbers that are part of the brand should stay (e.g., 3M)."""
        result = extract_primary_identifier("3M COMPANY INC")
        assert "3M" in result


# ---------------------------------------------------------------------------
# parse_search_results_html tests
# ---------------------------------------------------------------------------


class TestParseSearchResults:
    @pytest.fixture
    def results_html(self):
        return (FIXTURES_DIR / "search_results.html").read_text()

    @pytest.fixture
    def empty_html(self):
        return (FIXTURES_DIR / "search_results_empty.html").read_text()

    @pytest.fixture
    def multipage_html(self):
        return (FIXTURES_DIR / "search_results_multipage.html").read_text()

    def test_parse_results_count(self, results_html):
        """Should parse all 4 contract rows."""
        contracts = parse_search_results_html(results_html)
        assert len(contracts) == 4

    def test_parse_results_fields(self, results_html):
        """First row should have correct fields."""
        contracts = parse_search_results_html(results_html)
        first = contracts[0]
        assert first["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert first["agency_contract_number"] == "60021431070"
        assert first["ctd_url"] is not None
        assert "contract-transparency" in first["ctd_url"]
        assert first["detail_url"] is not None
        assert "contract-detail" in first["detail_url"]
        assert first["payments_amount"] == 5310000.00
        assert first["row_hash"] is not None

    def test_parse_results_missing_ctd(self, results_html):
        """Second row has no CTD link - should be None."""
        contracts = parse_search_results_html(results_html)
        second = contracts[1]
        assert second["vendor_name"] == "COMCAST BUSINESS COMMUNICATIONS LLC"
        assert second["ctd_url"] is None

    def test_parse_results_zero_amount(self, results_html):
        """Fourth row has $0.00 payment."""
        contracts = parse_search_results_html(results_html)
        zero = [c for c in contracts if c["agency_contract_number"] == "420001Q5588"]
        assert len(zero) == 1
        assert zero[0]["payments_amount"] == 0.0

    def test_parse_results_unique_hashes(self, results_html):
        """All row hashes should be unique."""
        contracts = parse_search_results_html(results_html)
        hashes = [c["row_hash"] for c in contracts]
        assert len(hashes) == len(set(hashes))

    def test_parse_empty_results(self, empty_html):
        """Empty results page should return empty list."""
        contracts = parse_search_results_html(empty_html)
        assert contracts == []

    def test_parse_no_html(self):
        """None or empty string should return empty list."""
        assert parse_search_results_html("") == []
        assert parse_search_results_html(None) == []

    def test_parse_malformed_html(self):
        """Malformed HTML should not crash, returns empty list."""
        contracts = parse_search_results_html("<div>not a table</div>")
        assert contracts == []

    def test_parse_multipage_results(self, multipage_html):
        """Multi-page fixture should parse the visible page rows."""
        contracts = parse_search_results_html(multipage_html)
        assert len(contracts) == 2
        assert contracts[0]["vendor_name"] == "AT&T CORP"

    def test_html_entity_decoding(self, multipage_html):
        """HTML entities like &amp; should be decoded."""
        contracts = parse_search_results_html(multipage_html)
        # AT&T should be decoded from AT&amp;T
        assert contracts[0]["vendor_name"] == "AT&T CORP"


# ---------------------------------------------------------------------------
# parse_contract_detail_html tests
# ---------------------------------------------------------------------------


class TestParseContractDetail:
    @pytest.fixture
    def detail_html(self):
        return (FIXTURES_DIR / "contract_detail.html").read_text()

    @pytest.fixture
    def empty_detail_html(self):
        return (FIXTURES_DIR / "contract_detail_empty.html").read_text()

    @pytest.fixture
    def partial_detail_html(self):
        return (FIXTURES_DIR / "contract_detail_partial.html").read_text()

    def test_parse_detail_fields(self, detail_html):
        """Should extract all key-value pairs from detail table."""
        detail = parse_contract_detail_html(detail_html)
        assert detail["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert detail["agency"] == "COMMERCE AND ECONOMIC OPPORTUNITY"
        assert detail["contract_number"] == "60021431070"
        assert detail["description"] == "BROADBAND INTERNET SERVICES FOR STATE OFFICES"
        assert detail["start_date"] == "07/01/2024"
        assert detail["end_date"] == "06/30/2026"
        assert detail["current_contract_amount"] == 5310000.00
        assert detail["award_type"] == "Standard"

    def test_parse_detail_empty_page(self, empty_detail_html):
        """Empty detail page should return empty dict."""
        detail = parse_contract_detail_html(empty_detail_html)
        assert detail == {}

    def test_parse_detail_partial(self, partial_detail_html):
        """Partial detail should return only available fields."""
        detail = parse_contract_detail_html(partial_detail_html)
        assert detail["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert detail["contract_number"] == "420001Q5588"
        assert detail["current_contract_amount"] == 0.0
        # Missing fields should not be in dict
        assert "agency" not in detail
        assert "description" not in detail

    def test_parse_detail_none_input(self):
        """None input should return empty dict."""
        assert parse_contract_detail_html(None) == {}
        assert parse_contract_detail_html("") == {}


# ---------------------------------------------------------------------------
# Database persistence tests
# ---------------------------------------------------------------------------


class TestComptrollerDB:
    @pytest.fixture
    def db_conn(self, tmp_path):
        """Create a temp database with schema."""
        db_path = str(tmp_path / "test_comptroller.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_schema_tables_exist(self, db_conn):
        """Verify init-db creates the Comptroller tables."""
        tables = [
            "comptroller_state_contracts",
            "comptroller_contract_details",
            "comptroller_scrape_runs",
        ]
        for table in tables:
            row = db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            assert row is not None, f"Table {table} should exist"

    def test_save_contracts_insert(self, db_conn):
        """Test inserting contracts from parsed search results."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        contracts = [
            {
                "vendor_name": "COMCAST OF ILLINOIS III INC",
                "agency_contract_number": "60021431070",
                "ctd_url": "https://example.com/ctd",
                "detail_url": "https://example.com/detail",
                "payments_amount": 5310000.00,
                "row_hash": _hash_row(["COMCAST OF ILLINOIS III INC", "60021431070", "5310000.0"]),
            }
        ]
        ins, upd = scraper.save_contracts("COMCAST", contracts, "https://example.com")
        assert ins == 1
        assert upd == 0

        row = db_conn.execute(
            "SELECT * FROM comptroller_state_contracts WHERE agency_contract_number = '60021431070'"
        ).fetchone()
        assert row is not None
        assert row["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert row["payments_amount"] == 5310000.00

    def test_save_contracts_idempotent(self, db_conn):
        """Re-inserting same contract should not create duplicates."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        rh = _hash_row(["TEST CORP", "C001", "100000.0"])
        contracts = [
            {
                "vendor_name": "TEST CORP",
                "agency_contract_number": "C001",
                "ctd_url": None,
                "detail_url": "https://example.com/detail",
                "payments_amount": 100000.00,
                "row_hash": rh,
            }
        ]
        scraper.save_contracts("TEST", contracts, "https://example.com")
        scraper.save_contracts("TEST", contracts, "https://example.com")

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM comptroller_state_contracts WHERE row_hash = ?",
            (rh,),
        ).fetchone()["cnt"]
        assert count == 1

    def test_save_contracts_update_on_change(self, db_conn):
        """Changed data should update existing row."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        contracts_v1 = [
            {
                "vendor_name": "TEST CORP",
                "agency_contract_number": "C002",
                "ctd_url": None,
                "detail_url": "https://example.com/detail",
                "payments_amount": 50000.00,
                "row_hash": _hash_row(["TEST CORP", "C002", "50000.0"]),
            }
        ]
        ins1, upd1 = scraper.save_contracts("TEST", contracts_v1, "https://example.com")
        assert ins1 == 1 and upd1 == 0

        contracts_v2 = [
            {
                "vendor_name": "TEST CORP",
                "agency_contract_number": "C002",
                "ctd_url": "https://example.com/ctd",
                "detail_url": "https://example.com/detail",
                "payments_amount": 75000.00,
                "row_hash": _hash_row(["TEST CORP", "C002", "75000.0"]),
            }
        ]
        ins2, upd2 = scraper.save_contracts("TEST", contracts_v2, "https://example.com")
        assert ins2 == 0 and upd2 == 1

        row = db_conn.execute(
            "SELECT payments_amount FROM comptroller_state_contracts WHERE agency_contract_number = 'C002'"
        ).fetchone()
        assert row["payments_amount"] == 75000.00

    def test_save_contract_detail(self, db_conn):
        """Test saving contract detail data."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        # First insert a contract to reference
        contracts = [
            {
                "vendor_name": "COMCAST OF ILLINOIS III INC",
                "agency_contract_number": "60021431070",
                "ctd_url": None,
                "detail_url": "https://example.com/detail",
                "payments_amount": 5310000.00,
                "row_hash": _hash_row(["COMCAST OF ILLINOIS III INC", "60021431070", "5310000.0"]),
            }
        ]
        scraper.save_contracts("COMCAST", contracts, "https://example.com")

        detail = {
            "vendor_name": "COMCAST OF ILLINOIS III INC",
            "agency": "COMMERCE AND ECONOMIC OPPORTUNITY",
            "contract_number": "60021431070",
            "description": "BROADBAND INTERNET SERVICES",
            "start_date": "07/01/2024",
            "end_date": "06/30/2026",
            "current_contract_amount": 5310000.00,
            "award_type": "Standard",
        }
        scraper.save_contract_detail("60021431070", detail, "https://example.com/detail")

        row = db_conn.execute(
            "SELECT * FROM comptroller_contract_details WHERE agency_contract_number = '60021431070'"
        ).fetchone()
        assert row is not None
        assert row["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert row["description"] == "BROADBAND INTERNET SERVICES"
        assert row["current_contract_amount"] == 5310000.00

    def test_save_contract_detail_idempotent(self, db_conn):
        """Re-saving same detail should not create duplicates."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        detail = {
            "vendor_name": "TEST CORP",
            "contract_number": "C001",
            "current_contract_amount": 100000.00,
        }
        scraper.save_contract_detail("C001", detail, "https://example.com/detail")
        scraper.save_contract_detail("C001", detail, "https://example.com/detail")

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM comptroller_contract_details WHERE agency_contract_number = 'C001'"
        ).fetchone()["cnt"]
        assert count == 1

    def test_save_contract_detail_update(self, db_conn):
        """Changed detail should update existing row."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        detail_v1 = {
            "vendor_name": "TEST CORP",
            "contract_number": "C003",
            "current_contract_amount": 50000.00,
        }
        scraper.save_contract_detail("C003", detail_v1, "https://example.com/detail")

        detail_v2 = {
            "vendor_name": "TEST CORP",
            "contract_number": "C003",
            "description": "UPDATED DESCRIPTION",
            "current_contract_amount": 75000.00,
        }
        scraper.save_contract_detail("C003", detail_v2, "https://example.com/detail")

        row = db_conn.execute(
            "SELECT description, current_contract_amount FROM comptroller_contract_details WHERE agency_contract_number = 'C003'"
        ).fetchone()
        assert row["description"] == "UPDATED DESCRIPTION"
        assert row["current_contract_amount"] == 75000.00

    def test_scrape_run_lifecycle(self, db_conn):
        """Test creating and completing a scrape run."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        run_id = scraper.create_run("batch")
        assert run_id > 0

        scraper.complete_run(run_id, vendors_searched=5, contracts_found=20,
                             details_scraped=15, error_count=1, notes="test run")

        row = db_conn.execute(
            "SELECT * FROM comptroller_scrape_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row["completed_at"] is not None
        assert row["contracts_found"] == 20
        assert row["details_scraped"] == 15

    def test_full_parse_and_save_pipeline(self, db_conn):
        """End-to-end: parse fixture HTML and save to DB."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        html = (FIXTURES_DIR / "search_results.html").read_text()
        contracts = parse_search_results_html(html)

        ins, upd = scraper.save_contracts("COMCAST", contracts, "https://example.com")
        assert ins == 4
        assert upd == 0

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM comptroller_state_contracts"
        ).fetchone()["cnt"]
        assert count == 4

        # Re-run should not create duplicates
        ins2, upd2 = scraper.save_contracts("COMCAST", contracts, "https://example.com")
        assert ins2 == 0
        assert upd2 == 0

    def test_detail_parse_and_save_pipeline(self, db_conn):
        """End-to-end: parse detail fixture and save to DB."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        html = (FIXTURES_DIR / "contract_detail.html").read_text()
        detail = parse_contract_detail_html(html)

        scraper.save_contract_detail("60021431070", detail, "https://example.com/detail")

        row = db_conn.execute(
            "SELECT * FROM comptroller_contract_details WHERE agency_contract_number = '60021431070'"
        ).fetchone()
        assert row is not None
        assert row["vendor_name"] == "COMCAST OF ILLINOIS III INC"
        assert row["current_contract_amount"] == 5310000.00
        assert row["start_date"] == "07/01/2024"
        assert row["end_date"] == "06/30/2026"


# ---------------------------------------------------------------------------
# Scraper class instantiation tests
# ---------------------------------------------------------------------------


class TestComptrollerScraperInit:
    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_scraper_init.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_scraper_creation(self, db_conn):
        """Scraper can be instantiated with a DB connection."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        assert scraper.conn is db_conn
        assert scraper.headless is True

    def test_scraper_default_headless(self, db_conn):
        """Default headless should be True."""
        scraper = ComptrollerContractsScraper(db_conn)
        assert scraper.headless is True

    def test_scraper_urls(self, db_conn):
        """Scraper should have the correct target URL."""
        scraper = ComptrollerContractsScraper(db_conn, headless=True)
        assert "illinoiscomptroller.gov" in scraper.SEARCH_URL
        assert "state-contracts" in scraper.SEARCH_URL
