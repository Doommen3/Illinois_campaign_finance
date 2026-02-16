"""Tests for OpenBook Illinois Comptroller scraper and ingestion."""
import os
import sqlite3
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from scraper.openbook_scraper import (
    OpenBookScraper,
    _HttpSession,
    _is_person_name,
    _pick_best_match,
    _score_all_matches,
    _score_one,
    _clean_html_cell,
    generate_search_terms,
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


# ---------------------------------------------------------------------------
# generate_search_terms tests
# ---------------------------------------------------------------------------


class TestGenerateSearchTerms:
    """Tests for smart search term generation from vendor names."""

    def test_person_name_last_first(self):
        """SMITH, JOHN -> ['SMITH, JOHN'] (full name only at high confidence)"""
        result = generate_search_terms("SMITH, JOHN")
        assert "SMITH, JOHN" in result
        assert "JOHN" not in result

    def test_person_name_last_first_middle(self):
        """SMITH, JOHN A -> ['SMITH, JOHN A'] (full name only at high confidence)"""
        result = generate_search_terms("SMITH, JOHN A")
        assert "SMITH, JOHN A" in result
        assert len(result) >= 1

    def test_person_vs_company_comma_before_suffix(self):
        """CATERPILLAR, INC -> treated as company, not person."""
        result = generate_search_terms("CATERPILLAR, INC")
        assert "CATERPILLAR" in result
        # Should NOT treat "INC" as a first name

    def test_company_suffix_stripping(self):
        """COMCAST OF ILLINOIS III INC -> primary is COMCAST."""
        result = generate_search_terms("COMCAST OF ILLINOIS III INC")
        assert result[0] == "COMCAST"

    def test_llc_suffix_stripping(self):
        """ACME CONSULTING LLC -> primary is ACME CONSULTING (2 tokens kept)."""
        result = generate_search_terms("ACME CONSULTING LLC")
        assert result[0] == "ACME CONSULTING"

    def test_corp_suffix_stripping(self):
        """WIDGET MANUFACTURING CORP -> primary is WIDGET MANUFACTURING (2 tokens kept)."""
        result = generate_search_terms("WIDGET MANUFACTURING CORP")
        assert result[0] == "WIDGET MANUFACTURING"

    def test_geographic_removal(self):
        """AT&T OF ILLINOIS -> AT&T."""
        result = generate_search_terms("AT&T OF ILLINOIS")
        assert "AT&T" in result

    def test_short_passthrough(self):
        """DELOITTE -> ['DELOITTE'] (no stripping needed)."""
        result = generate_search_terms("DELOITTE")
        assert result == ["DELOITTE"]

    def test_two_word_passthrough(self):
        """BLUE CROSS -> kept as is."""
        result = generate_search_terms("BLUE CROSS")
        assert "BLUE CROSS" in result

    def test_long_name_uses_two_token_primary(self):
        """BLUE CROSS & BLUE SHIELD -> primary should be BLUE CROSS (not BLUE)."""
        result = generate_search_terms("BLUE CROSS & BLUE SHIELD")
        assert result[0] == "BLUE CROSS"

    def test_chicago_authority_uses_two_token_primary(self):
        """CHICAGO TRANSIT AUTHORITY -> primary should keep two tokens for precision."""
        result = generate_search_terms("CHICAGO TRANSIT AUTHORITY")
        assert result[0] == "CHICAGO TRANSIT"

    def test_abbreviation_expansion_comed(self):
        """COMED -> includes COMMONWEALTH EDISON."""
        result = generate_search_terms("COMED")
        assert "COMED" in result
        assert "COMMONWEALTH EDISON" in result

    def test_abbreviation_expansion_bcbs(self):
        """BCBS -> includes BLUE CROSS BLUE SHIELD."""
        result = generate_search_terms("BCBS")
        assert "BCBS" in result
        assert "BLUE CROSS BLUE SHIELD" in result

    def test_empty_string(self):
        result = generate_search_terms("")
        assert result == []

    def test_whitespace_only(self):
        result = generate_search_terms("   ")
        assert result == []

    def test_all_suffix_fallback(self):
        """If everything is a suffix, fall back to first token."""
        result = generate_search_terms("INC LLC CORP")
        assert len(result) >= 1
        assert result[0] == "INC"

    def test_no_duplicates(self):
        """Output list should have no duplicates."""
        result = generate_search_terms("DELOITTE")
        assert len(result) == len(set(result))

    def test_ampersand_preserved(self):
        """AT&T SERVICES INC -> AT&T in result."""
        result = generate_search_terms("AT&T SERVICES INC")
        assert "AT&T" in result

    def test_min_length_filter(self):
        """Terms shorter than 3 chars should be filtered out."""
        # "AB" as a seed should return empty or only valid terms
        result = generate_search_terms("AB")
        assert all(len(t) >= 3 for t in result)

    def test_original_included_as_fallback(self):
        """Original name (cleaned) should be included if different from primary."""
        result = generate_search_terms("COMCAST OF ILLINOIS III INC")
        # Primary is "COMCAST", original cleaned should also be present
        assert "COMCAST" in result
        assert any("COMCAST" in t and t != "COMCAST" for t in result)


# ---------------------------------------------------------------------------
# _score_all_matches tests
# ---------------------------------------------------------------------------


class TestScoreAllMatches:
    """Tests for _score_all_matches() that returns all results above threshold."""

    def test_exact_match_scores_one(self):
        suggestions = [
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
        ]
        results = _score_all_matches("COMCAST CORPORATION", suggestions, "COMCAST")
        assert len(results) >= 1
        exact = [r for r in results if r["confidence"] == 1.0]
        assert len(exact) == 1

    def test_multiple_suggestions_all_scored(self):
        suggestions = [
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
            {"id": "COMCAST OF ILLINOIS III INC", "value": "Comcast of Illinois III Inc"},
            {"id": "COMCAST BUSINESS COMM", "value": "Comcast Business Comm"},
        ]
        # With dual scoring (seed + search_term), all COMCAST variants match search_term "COMCAST"
        results = _score_all_matches("COMCAST CORPORATION", suggestions, "COMCAST")
        assert len(results) == 3  # All three prefix-match against "COMCAST"

    def test_below_threshold_excluded(self):
        suggestions = [
            {"id": "TOTALLY DIFFERENT COMPANY", "value": "Totally Different Company"},
        ]
        results = _score_all_matches("COMCAST", suggestions, "COMCAST", min_confidence=0.3)
        assert len(results) == 0

    def test_sorted_by_confidence_descending(self):
        suggestions = [
            {"id": "COMCAST BUSINESS COMM LLC", "value": "Comcast Business Comm LLC"},
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
            {"id": "COMCAST OF ILLINOIS", "value": "Comcast of Illinois"},
        ]
        results = _score_all_matches("COMCAST CORPORATION", suggestions, "COMCAST")
        confidences = [r["confidence"] for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_search_term_recorded(self):
        suggestions = [
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
        ]
        results = _score_all_matches("COMCAST CORPORATION", suggestions, "COMCAST")
        assert all(r["search_term"] == "COMCAST" for r in results)

    def test_empty_suggestions(self):
        results = _score_all_matches("ANYTHING", [], "ANYTHING")
        assert results == []

    def test_none_suggestions(self):
        results = _score_all_matches("ANYTHING", None, "ANYTHING")
        assert results == []


# ---------------------------------------------------------------------------
# resolve_all_matches_http tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestResolveAllMatchesHTTP:
    """Tests for resolve_all_matches_http() with mocked HTTP responses."""

    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_resolve_all.db")
        init_db(db_path)
        conn = get_db(db_path)
        yield conn
        conn.close()

    def test_broad_search_returns_multiple(self, db_conn, monkeypatch):
        """Searching 'COMCAST' should return multiple vendor matches."""
        import json as _json
        from scraper import openbook_scraper

        fake_suggestions = _json.dumps([
            {"id": "COMCAST CORPORATION", "value": "Comcast Corporation"},
            {"id": "COMCAST OF ILLINOIS III INC", "value": "Comcast of Illinois III Inc"},
            {"id": "COMCAST BUSINESS COMM", "value": "Comcast Business Comm"},
        ])

        class FakeSession:
            timeout = 30
            def get(self, url):
                return fake_suggestions

        scraper = OpenBookScraper(db_conn, headless=True)
        # Disable rate limiter waits for tests
        monkeypatch.setattr(scraper.rate_limiter, "wait", lambda: None)
        monkeypatch.setattr(scraper.rate_limiter, "record_success", lambda: None)

        results = scraper.resolve_all_matches_http(
            "COMCAST OF ILLINOIS III INC", FakeSession()
        )
        assert len(results) >= 2
        vendor_keys = [r["vendor_key"] for r in results]
        assert "COMCAST CORPORATION" in vendor_keys

    def test_abbreviation_expansion(self, db_conn, monkeypatch):
        """COMED -> no results, but COMMONWEALTH EDISON -> results."""
        import json as _json

        call_log = []

        def fake_get(url):
            call_log.append(url)
            if "COMMONWEALTH" in url or "EDISON" in url:
                return _json.dumps([
                    {"id": "COMMONWEALTH EDISON CO", "value": "Commonwealth Edison Co"},
                ])
            return _json.dumps([])

        class FakeSession:
            timeout = 30
            def get(self, url):
                return fake_get(url)

        scraper = OpenBookScraper(db_conn, headless=True)
        monkeypatch.setattr(scraper.rate_limiter, "wait", lambda: None)
        monkeypatch.setattr(scraper.rate_limiter, "record_success", lambda: None)

        results = scraper.resolve_all_matches_http("COMED", FakeSession())
        assert len(results) >= 1
        assert any("COMMONWEALTH EDISON" in r["vendor_key"] for r in results)

    def test_person_name_resolution(self, db_conn, monkeypatch):
        """SMITH, JOHN -> searches 'SMITH' (last name only)."""
        import json as _json

        searched_terms = []

        def fake_get(url):
            # Extract the search term from the URL
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "term" in qs:
                searched_terms.append(qs["term"][0])
            return _json.dumps([
                {"id": "SMITH ENTERPRISES", "value": "Smith Enterprises"},
            ])

        class FakeSession:
            timeout = 30
            def get(self, url):
                return fake_get(url)

        scraper = OpenBookScraper(db_conn, headless=True)
        monkeypatch.setattr(scraper.rate_limiter, "wait", lambda: None)
        monkeypatch.setattr(scraper.rate_limiter, "record_success", lambda: None)

        results = scraper.resolve_all_matches_http("SMITH, JOHN", FakeSession())
        # Should search full person name "SMITH, JOHN"
        assert any("SMITH, JOHN" == t for t in searched_terms)


# ---------------------------------------------------------------------------
# Contribution parsing with HTML entities tests
# ---------------------------------------------------------------------------


class TestParseContributionsEntities:
    """Tests for contribution parsing with HTML entities and junk row filtering."""

    @pytest.fixture
    def entity_html(self):
        return (FIXTURES_DIR / "contributions_with_entities.html").read_text()

    def test_entities_skips_junk_rows(self, entity_html):
        """Header/nbsp rows should be filtered out."""
        contribs = parse_contributions_html(entity_html, "TEST_VENDOR")
        # Only 2 valid data rows (header-in-td row with &nbsp; is filtered)
        names = [c["contributor_name"] for c in contribs]
        assert "&nbsp;" not in " ".join(names)
        assert "Received By" not in names

    def test_entity_ampersand_unescaped(self, entity_html):
        """&amp; in contributor name should become &."""
        contribs = parse_contributions_html(entity_html, "TEST_VENDOR")
        amp_rows = [c for c in contribs if "&" in c["contributor_name"]]
        assert len(amp_rows) >= 1
        assert amp_rows[0]["contributor_name"] == "SMITH & ASSOCIATES"

    def test_date_validation_filters_non_dates(self, entity_html):
        """Only rows with valid date strings pass through."""
        contribs = parse_contributions_html(entity_html, "TEST_VENDOR")
        for c in contribs:
            # All dates should match MM/DD/YYYY pattern
            import re
            assert re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", c["contribution_date"])

    def test_valid_row_count(self, entity_html):
        """Should parse exactly 2 valid data rows."""
        contribs = parse_contributions_html(entity_html, "TEST_VENDOR")
        assert len(contribs) == 2


# ---------------------------------------------------------------------------
# Short prefix scoring penalty tests
# ---------------------------------------------------------------------------


class TestShortPrefixPenalty:
    """Tests for short single-token prefix scoring penalty."""

    def test_short_single_token_prefix_penalized(self):
        """'BUSH' vs 'BUSHMASTER FIREARMS' -> 0.5 (penalized)."""
        method, confidence = _score_one("BUSH", "BUSHMASTER FIREARMS")
        assert method == "prefix"
        assert confidence == 0.5

    def test_long_single_token_prefix_normal(self):
        """'COMCAST' vs 'COMCAST CORP' -> 0.9 (normal)."""
        method, confidence = _score_one("COMCAST", "COMCAST CORP")
        assert method == "prefix"
        assert confidence == 0.9

    def test_multi_token_prefix_normal(self):
        """'ACME CORP' vs 'ACME CORPORATION INC' -> 0.9 (multi-token, not penalized)."""
        method, confidence = _score_one("ACME CORP", "ACME CORPORATION INC")
        # This is fuzzy since "ACME CORP" doesn't prefix-match "ACME CORPORATION INC"
        # but let's test a true multi-token prefix
        method2, confidence2 = _score_one("ACME CORPORATION", "ACME CORPORATION INC")
        assert method2 == "prefix"
        assert confidence2 == 0.9

    def test_five_char_single_token_penalized(self):
        """'TERRY' (5 chars) vs 'TERRY CONSTRUCTION' -> 0.5."""
        method, confidence = _score_one("TERRY", "TERRY CONSTRUCTION")
        assert method == "prefix"
        assert confidence == 0.5

    def test_six_char_single_token_normal(self):
        """'DELOITTE' (8 chars) vs 'DELOITTE CONSULTING' -> 0.9."""
        method, confidence = _score_one("DELOITTE", "DELOITTE CONSULTING")
        assert method == "prefix"
        assert confidence == 0.9


# ---------------------------------------------------------------------------
# Contract detail with tr attributes tests
# ---------------------------------------------------------------------------


class TestParseContractDetailAttributes:
    """Tests for contract detail parsing with class attributes on tr elements."""

    @pytest.fixture
    def detail_attrs_html(self):
        return (FIXTURES_DIR / "contract_detail_with_attributes.html").read_text()

    def test_parse_detail_with_tr_attributes(self, detail_attrs_html):
        """Rows with class='odd'/'even' attributes should still be parsed."""
        rows = parse_contract_detail_html(
            detail_attrs_html,
            vendor_key="TEST VENDOR",
            contract_number="C-999",
            fiscal_year=2026,
        )
        assert len(rows) == 3

    def test_parse_detail_amounts(self, detail_attrs_html):
        rows = parse_contract_detail_html(
            detail_attrs_html,
            vendor_key="TEST VENDOR",
            contract_number="C-999",
            fiscal_year=2026,
        )
        amounts = [r["payment_amount"] for r in rows]
        assert 15000.00 in amounts
        assert 25500.75 in amounts
        assert 8200.00 in amounts

    def test_parse_detail_dates(self, detail_attrs_html):
        rows = parse_contract_detail_html(
            detail_attrs_html,
            vendor_key="TEST VENDOR",
            contract_number="C-999",
            fiscal_year=2026,
        )
        dates = [r["issue_date"] for r in rows]
        assert "2/15/26" in dates
        assert "1/10/26" in dates
        assert "12/5/25" in dates


# ---------------------------------------------------------------------------
# _is_person_name helper tests
# ---------------------------------------------------------------------------


class TestIsPersonName:
    """Tests for _is_person_name() helper."""

    def test_person_last_first(self):
        assert _is_person_name("SMITH, JOHN") is True

    def test_person_last_first_middle(self):
        assert _is_person_name("SMITH, JOHN A") is True

    def test_company_with_suffix(self):
        """CATERPILLAR, INC -> not a person."""
        assert _is_person_name("CATERPILLAR, INC") is False

    def test_company_with_llc(self):
        assert _is_person_name("ACME, LLC") is False

    def test_no_comma(self):
        assert _is_person_name("COMCAST CORPORATION") is False

    def test_empty_string(self):
        assert _is_person_name("") is False

    def test_none(self):
        assert _is_person_name(None) is False

    def test_comma_but_empty_after(self):
        assert _is_person_name("SOMETHING,") is False

    def test_company_inc_corp(self):
        """COMPANY, INC CORP -> all suffixes."""
        assert _is_person_name("COMPANY, INC CORP") is False


# ---------------------------------------------------------------------------
# ISBE seed generation tests
# ---------------------------------------------------------------------------


class TestSeedGenerationISBE:
    """Tests for ISBE seed generation from bulk_expenditures_clean."""

    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test_isbe_seeds.db")
        init_db(db_path)
        conn = get_db(db_path)
        # Create bulk_expenditures_clean table (minimal schema)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bulk_expenditures_clean (
                id INTEGER PRIMARY KEY,
                payee_last_or_business_name TEXT,
                amount REAL,
                is_amount_anomalous INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        yield conn
        conn.close()

    def test_seeds_from_isbe(self, db_conn):
        """Only business names (not persons) should be seeded."""
        db_conn.execute(
            "INSERT INTO bulk_expenditures_clean (payee_last_or_business_name, amount) "
            "VALUES ('ACME CORPORATION', 50000.0)"
        )
        db_conn.execute(
            "INSERT INTO bulk_expenditures_clean (payee_last_or_business_name, amount) "
            "VALUES ('SMITH, JOHN', 50000.0)"
        )
        db_conn.execute(
            "INSERT INTO bulk_expenditures_clean (payee_last_or_business_name, amount) "
            "VALUES ('TINY VENDOR', 500.0)"
        )
        db_conn.commit()

        stats = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="isbe",
        )
        assert stats["isbe_seeds"] == 1  # Only ACME CORPORATION (person skipped, tiny below min)
        assert stats["total_new_seeds"] == 1

        row = db_conn.execute(
            "SELECT * FROM openbook_vendor_seed WHERE seed_text = 'ACME CORPORATION'"
        ).fetchone()
        assert row is not None
        assert row["seed_source"] == "isbe"

    def test_isbe_skips_anomalous(self, db_conn):
        """Rows with is_amount_anomalous=1 should be excluded."""
        db_conn.execute(
            "INSERT INTO bulk_expenditures_clean (payee_last_or_business_name, amount, is_amount_anomalous) "
            "VALUES ('ANOMALOUS VENDOR', 100000.0, 1)"
        )
        db_conn.commit()

        stats = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="isbe",
        )
        assert stats["isbe_seeds"] == 0

    def test_isbe_seeds_idempotent(self, db_conn):
        """Re-running ISBE seed generation shouldn't create duplicates."""
        db_conn.execute(
            "INSERT INTO bulk_expenditures_clean (payee_last_or_business_name, amount) "
            "VALUES ('REPEAT CORP', 50000.0)"
        )
        db_conn.commit()

        stats1 = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="isbe",
        )
        assert stats1["isbe_seeds"] == 1

        stats2 = OpenBookScraper.generate_seeds(
            db_conn, min_amount=10000.0, limit_per_source=100, sources="isbe",
        )
        assert stats2["isbe_seeds"] == 0

        count = db_conn.execute(
            "SELECT COUNT(*) as cnt FROM openbook_vendor_seed WHERE seed_text = 'REPEAT CORP'"
        ).fetchone()["cnt"]
        assert count == 1


# ---------------------------------------------------------------------------
# CLI sources option test
# ---------------------------------------------------------------------------


class TestSourcesOption:
    """Tests for comma-separated sources parsing."""

    def test_sources_comma_separated(self):
        """Verify comma-separated string splits correctly."""
        sources = "fec,isbe"
        result = [s.strip() for s in sources.split(",")]
        assert result == ["fec", "isbe"]

    def test_sources_with_spaces(self):
        sources = "fec, isbe, lobbying"
        result = [s.strip() for s in sources.split(",")]
        assert result == ["fec", "isbe", "lobbying"]


# ---------------------------------------------------------------------------
# _clean_html_cell helper tests
# ---------------------------------------------------------------------------


class TestCleanHtmlCell:
    """Tests for _clean_html_cell() HTML entity + tag stripping."""

    def test_unescape_amp(self):
        assert _clean_html_cell("SMITH &amp; JONES") == "SMITH & JONES"

    def test_unescape_nbsp(self):
        assert _clean_html_cell("&nbsp;") == ""

    def test_strip_tags(self):
        assert _clean_html_cell("<b>BOLD</b>") == "BOLD"

    def test_collapse_whitespace(self):
        assert _clean_html_cell("  lots   of    space  ") == "lots of space"

    def test_empty(self):
        assert _clean_html_cell("") == ""

    def test_none(self):
        assert _clean_html_cell(None) == ""
