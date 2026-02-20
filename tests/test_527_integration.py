"""Tests for 527 enhanced integration: contributions, director-candidate matching,
address matching, person intelligence, and dashboard 527 summary."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from database.cross_matching import (
    _normalize_name_tokens,
    _jaccard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_db(tmp_path: Path):
    db_path = str(tmp_path / "test_527_int.db")
    init_db(db_path)
    conn = get_db(db_path)
    return conn


def _insert_527_org(conn, ein, org_name, state="IL", city="Chicago", zip_code="60601",
                    address_1="123 Main St"):
    conn.execute(
        """
        INSERT OR REPLACE INTO irs527_organizations
            (ein, form_id, form_id_seq, org_name, state, city, zip, address_1)
        VALUES (?, 1, 0, ?, ?, ?, ?, ?)
        """,
        (ein, org_name, state, city, zip_code, address_1),
    )
    conn.commit()


def _insert_527_director(conn, ein, org_name, person_name, city="Chicago",
                         state="IL", zip_code="60601", address_1="123 Main St"):
    conn.execute(
        """
        INSERT INTO irs527_directors
            (form_id, ein, org_name, person_name, city, state, zip, address_1)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ein, org_name, person_name, city, state, zip_code, address_1),
    )
    conn.commit()


def _insert_donor_summary(conn, donor_key, donor_name, amount=1000,
                          city=None, state=None, address=None):
    conn.execute(
        """
        INSERT OR REPLACE INTO analytics_donor_summary
            (source, donor_key, donor_name, donor_city, donor_state, donor_address,
             total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', ?, ?, ?, ?, ?, ?, 1, 1)
        """,
        (donor_key, donor_name, city, state, address, amount),
    )
    conn.commit()


def _insert_state_candidate(conn, candidate_id, candidate_full_name):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_candidates_clean (
            candidate_id INTEGER PRIMARY KEY,
            candidate_full_name TEXT
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO bulk_candidates_clean (candidate_id, candidate_full_name) VALUES (?, ?)",
        (candidate_id, candidate_full_name),
    )
    conn.commit()


def _insert_federal_candidate(conn, candidate_key, candidate_name, fec_candidate_id):
    # Must insert seed row first to satisfy FK constraint
    conn.execute(
        """
        INSERT OR REPLACE INTO fec_il_candidate_seed
            (candidate_key, candidate_name, normalized_candidate_name)
        VALUES (?, ?, ?)
        """,
        (candidate_key, candidate_name, candidate_name.lower()),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO fec_candidate_match
            (seed_candidate_key, candidate_name, match_status, fec_candidate_id, fec_name)
        VALUES (?, ?, 'matched', ?, ?)
        """,
        (candidate_key, candidate_name, fec_candidate_id, candidate_name),
    )
    conn.commit()


def _insert_committee(conn, committee_id_sbe, committee_name, city=None, state=None,
                      zip_code=None):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_name TEXT,
            city TEXT,
            state TEXT,
            zip TEXT
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO bulk_committees_clean (committee_id_sbe, committee_name, city, state, zip) VALUES (?, ?, ?, ?, ?)",
        (committee_id_sbe, committee_name, city, state, zip_code),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. 527 Contribution Parsing Tests (Type A Records)
# ---------------------------------------------------------------------------

class TestIrs527ContributionParsing:

    def test_parse_contribution_valid(self):
        from database.irs527_loader import _parse_contribution
        fields = ["A", "9555268", "38295", "Test Org", "521073928",
                  "Donor Person", "456 Oak Ave", "", "Springfield", "IL", "62701", "",
                  "Employer Inc", "1500.00", "Attorney", "20030430"]
        result = _parse_contribution(fields)
        assert result is not None
        assert result[0] == 9555268   # form_id
        assert result[1] == "521073928"  # ein
        assert result[3] == "Donor Person"  # contributor_name
        assert result[11] == 1500.0   # amount

    def test_parse_contribution_malformed(self):
        from database.irs527_loader import _parse_contribution
        fields = ["A", "bad"]  # Too few fields
        result = _parse_contribution(fields)
        assert result is None

    def test_load_527_with_contributions(self, tmp_path: Path):
        """Contributions (type A records) should be loaded into irs527_contributions."""
        from database.irs527_loader import load_irs527_full_file
        content = (
            "H|20260208|0641|F|\n"
            "1|8871|8|0|0|0|364367949|Test Org|123 Main||Chicago|IL|60601||email@test.com||"
            "John|123||Chicago|IL|60601||Jane|123||Chicago|IL|60601||"
            "123||Chicago|IL|60601|||||Purpose||2001-01-01|0|1\n"
            "A|9555268|38295|Test Org|364367949|Donor Person|456 Oak||Springfield|IL|62701||"
            "Employer|1500|Attorney|20030430\n"
        )
        data_path = tmp_path / "test_data.txt"
        data_path.write_text(content, encoding="utf-8")

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = get_db(db_path)

        stats = load_irs527_full_file(conn, data_path, illinois_only=False)
        assert stats["contributions"] >= 1

        row = conn.execute(
            "SELECT contributor_name, amount FROM irs527_contributions WHERE ein = '364367949'"
        ).fetchone()
        assert row is not None
        assert row["contributor_name"] == "Donor Person"
        assert row["amount"] == 1500.0
        conn.close()

    def test_load_527_contributions_illinois_filter(self, tmp_path: Path):
        """Illinois-only filter should apply to contributions via EIN set."""
        from database.irs527_loader import load_irs527_full_file
        content = (
            "H|20260208|0641|F|\n"
            "1|8871|8|0|0|0|111111111|IL ORG|123 Main||Chicago|IL|60601||email@test.com||"
            "John|123||Chicago|IL|60601||Jane|123||Chicago|IL|60601||"
            "123||Chicago|IL|60601|||||Purpose||2001-01-01|0|1\n"
            "1|8871|9|0|0|0|222222222|CA ORG|456 Oak||LA|CA|90001||email@test.com||"
            "John|456||LA|CA|90001||Jane|456||LA|CA|90001||"
            "456||LA|CA|90001|||||Purpose||2001-01-01|0|1\n"
            "A|100|1|IL ORG|111111111|IL Donor|123||Chicago|IL|60601||Emp|500|Occ|20030101\n"
            "A|101|2|CA ORG|222222222|CA Donor|456||LA|CA|90001||Emp|300|Occ|20030101\n"
        )
        data_path = tmp_path / "test_data.txt"
        data_path.write_text(content, encoding="utf-8")

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = get_db(db_path)

        stats = load_irs527_full_file(conn, data_path, illinois_only=True)
        assert stats["contributions"] == 1  # Only IL org's contribution

        rows = conn.execute("SELECT * FROM irs527_contributions").fetchall()
        assert len(rows) == 1
        assert rows[0]["ein"] == "111111111"
        conn.close()

    def test_contribution_empty_data(self, tmp_path: Path):
        """Empty file should not crash contribution loading."""
        from database.irs527_loader import load_irs527_full_file
        content = "H|20260208|0641|F|\n"
        data_path = tmp_path / "test_data.txt"
        data_path.write_text(content, encoding="utf-8")

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = get_db(db_path)

        stats = load_irs527_full_file(conn, data_path, illinois_only=False)
        assert stats["contributions"] == 0
        conn.close()

    def test_contribution_bom_encoding(self, tmp_path: Path):
        """File with BOM should load correctly."""
        from database.irs527_loader import load_irs527_full_file
        content = (
            "\ufeffH|20260208|0641|F|\n"
            "1|8871|8|0|0|0|364367949|Test Org|123||City|IL|60601||e@t.com||"
            "J|1||C|IL|6||J|1||C|IL|6||1||C|IL|6|||||P||2001-01-01|0|1\n"
            "A|100|1|Test Org|364367949|BOM Donor|123||City|IL|60601||E|100|O|20030101\n"
        )
        data_path = tmp_path / "test_bom.txt"
        data_path.write_bytes(content.encode("utf-8-sig"))

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        conn = get_db(db_path)

        stats = load_irs527_full_file(conn, data_path, illinois_only=False)
        assert stats["contributions"] >= 1
        conn.close()


# ---------------------------------------------------------------------------
# 2. Director-to-Candidate Matching Tests
# ---------------------------------------------------------------------------

class TestDirectorCandidateMatching:

    def test_match_directors_to_state_candidates(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_candidates
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "Robert Steffen")
        _insert_state_candidate(conn, 1001, "Robert Steffen")

        stats = match_527_directors_to_candidates(conn, threshold=0.80)
        assert stats["matches"] >= 1

        match = conn.execute(
            "SELECT * FROM irs527_director_candidate_matches WHERE ein = '123456789'"
        ).fetchone()
        assert match is not None
        assert match["candidate_source"] == "state"
        assert match["score"] >= 0.80
        conn.close()

    def test_match_directors_to_federal_candidates(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_candidates
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "Lauren Underwood")
        _insert_federal_candidate(conn, "il14-2026-underwood", "Lauren Underwood", "H8IL14150")

        stats = match_527_directors_to_candidates(conn, threshold=0.80)
        assert stats["matches"] >= 1

        match = conn.execute(
            "SELECT * FROM irs527_director_candidate_matches WHERE candidate_source = 'federal'"
        ).fetchone()
        assert match is not None
        assert match["score"] >= 0.80
        conn.close()

    def test_match_directors_no_candidates(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_candidates
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "Unique Name Nobody")

        stats = match_527_directors_to_candidates(conn, threshold=0.80)
        assert stats["matches"] == 0
        conn.close()

    def test_match_directors_missing_tables(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_candidates
        db_path = str(tmp_path / "empty.db")
        init_db(db_path)
        conn = get_db(db_path)

        stats = match_527_directors_to_candidates(conn, threshold=0.80)
        assert stats.get("skipped") == "missing_tables" or stats["matches"] == 0
        conn.close()


# ---------------------------------------------------------------------------
# 3. Address Normalization Tests
# ---------------------------------------------------------------------------

class TestAddressNormalization:

    def test_normalize_zip5(self):
        from database.cross_matching import _normalize_zip5
        assert _normalize_zip5("60601") == "60601"
        assert _normalize_zip5("60601-1234") == "60601"
        assert _normalize_zip5("0601") == "00601"
        assert _normalize_zip5("") is None
        assert _normalize_zip5(None) is None

    def test_normalize_city(self):
        from database.cross_matching import _normalize_city
        assert _normalize_city("CHICAGO") == "chicago"
        assert _normalize_city("  Chicago  ") == "chicago"
        assert _normalize_city("") is None
        assert _normalize_city(None) is None

    def test_address_score_exact_match(self):
        from database.cross_matching import _address_score
        score = _address_score("Chicago", "IL", "60601", "Chicago", "IL", "60601")
        assert score == 1.0

    def test_address_score_different_zip_same_city_state(self):
        from database.cross_matching import _address_score
        score = _address_score("Chicago", "IL", "60601", "Chicago", "IL", "60602")
        assert 0.4 <= score <= 0.7

    def test_address_score_different_city(self):
        from database.cross_matching import _address_score
        score = _address_score("Chicago", "IL", "60601", "Springfield", "IL", "62701")
        assert score < 0.5

    def test_address_score_different_state(self):
        from database.cross_matching import _address_score
        score = _address_score("Chicago", "IL", "60601", "Chicago", "IN", "60601")
        assert score < 0.5

    def test_address_score_missing_data(self):
        from database.cross_matching import _address_score
        score = _address_score(None, None, None, "Chicago", "IL", "60601")
        assert score == 0.0


# ---------------------------------------------------------------------------
# 4. Director-to-Donor Address Matching Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDirectorAddressMatching:

    def test_match_by_address_exact(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_donors_by_address
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "John Smith",
                             city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "john|smith|chicago", "John Smith",
                              city="Chicago", state="IL")

        stats = match_527_directors_to_donors_by_address(conn)
        assert stats["matches"] >= 1

        match = conn.execute(
            "SELECT * FROM irs527_director_address_matches WHERE ein = '123456789'"
        ).fetchone()
        assert match is not None
        assert match["address_score"] > 0
        conn.close()

    def test_match_by_address_different_zip(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_donors_by_address
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "John Smith",
                             city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "john|smith|chicago2", "John Smith",
                              city="Chicago", state="IL")

        stats = match_527_directors_to_donors_by_address(conn)
        # Should still match on city+state even without zip match
        assert stats["matches"] >= 1
        conn.close()

    def test_no_address_match_different_state(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_donors_by_address
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "John Smith",
                             city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "john|smith|ny", "John Smith",
                              city="New York", state="NY")

        stats = match_527_directors_to_donors_by_address(conn)
        assert stats["matches"] == 0
        conn.close()

    def test_director_address_missing_city_bucket_does_not_error(self, tmp_path: Path):
        from database.cross_matching import match_527_directors_to_donors_by_address
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "John Smith",
                             city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "john|smith|123 main||springfield|IL|60601", "John Smith",
                              city="Springfield", state="IL")

        stats = match_527_directors_to_donors_by_address(conn)
        assert "matches" in stats
        assert stats["matches"] >= 1
        conn.close()


# ---------------------------------------------------------------------------
# 5. Organization Address Matching Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestOrgAddressMatching:

    def test_match_org_address_to_committee(self, tmp_path: Path):
        from database.cross_matching import match_527_org_addresses
        conn = _setup_db(tmp_path)
        _insert_527_org(conn, "123456789", "Citizens PAC",
                        city="Springfield", state="IL", zip_code="62701")
        _insert_committee(conn, 101, "Citizens Committee",
                          city="Springfield", state="IL", zip_code="62701")

        stats = match_527_org_addresses(conn)
        assert stats["matches"] >= 1

        match = conn.execute(
            "SELECT * FROM irs527_org_address_matches WHERE ein = '123456789'"
        ).fetchone()
        assert match is not None
        assert match["matched_entity_type"] == "committee"
        conn.close()

    def test_match_org_address_to_donor(self, tmp_path: Path):
        from database.cross_matching import match_527_org_addresses
        conn = _setup_db(tmp_path)
        _insert_527_org(conn, "123456789", "Citizens PAC",
                        city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "donor|key|1", "Some Donor",
                              city="Chicago", state="IL")

        stats = match_527_org_addresses(conn)
        assert stats["matches"] >= 1
        conn.close()

    def test_no_org_address_match(self, tmp_path: Path):
        from database.cross_matching import match_527_org_addresses
        conn = _setup_db(tmp_path)
        _insert_527_org(conn, "123456789", "Citizens PAC",
                        city="Chicago", state="IL", zip_code="60601")
        _insert_committee(conn, 101, "Faraway Committee",
                          city="Los Angeles", state="CA", zip_code="90001")

        stats = match_527_org_addresses(conn)
        assert stats["matches"] == 0
        conn.close()

    def test_org_address_missing_city_bucket_does_not_error(self, tmp_path: Path):
        from database.cross_matching import match_527_org_addresses
        conn = _setup_db(tmp_path)
        _insert_527_org(conn, "123456789", "Citizens PAC",
                        city="Chicago", state="IL", zip_code="60601")
        _insert_donor_summary(conn, "first|last|123 main||springfield|IL|60601", "Some Donor",
                              city="Springfield", state="IL")

        stats = match_527_org_addresses(conn)
        assert "matches" in stats
        assert stats["matches"] >= 1
        conn.close()


# ---------------------------------------------------------------------------
# 6. Exhaustive Director-to-Donor Matching (no LIMIT 50000)
# ---------------------------------------------------------------------------

class TestExhaustiveDirectorDonorMatching:

    def test_exhaustive_match_finds_low_dollar_donor(self, tmp_path: Path):
        """Exhaustive matching should find donors even with tiny amounts."""
        from database.cross_matching import match_527_directors_to_donors
        conn = _setup_db(tmp_path)
        _insert_527_director(conn, "123456789", "Test Org", "Tiny Donor Person")
        # Very small amount donor - would be missed by LIMIT 50000 ORDER BY amount DESC
        _insert_donor_summary(conn, "tiny|donor|person", "Tiny Donor Person", amount=1)

        stats = match_527_directors_to_donors(conn, threshold=0.80)
        assert stats["matches"] >= 1
        conn.close()


# ---------------------------------------------------------------------------
# 7. Web Route Tests - 527 Detail Page Enhancement
# ---------------------------------------------------------------------------

class TestIrs527DetailEnhanced:

    @pytest.fixture
    def app(self, tmp_path: Path):
        db_path = str(tmp_path / "test_527_detail.db")
        init_db(db_path)
        conn = get_db(db_path)

        # Seed org, director, donor match, candidate match
        conn.execute(
            "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state) "
            "VALUES ('123456789', 100, 0, 'Test 527 Org', 'Chicago', 'IL')"
        )
        conn.execute(
            "INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state) "
            "VALUES (100, '123456789', 'Test 527 Org', 'Jane Director', 'President', 'Chicago', 'IL')"
        )
        conn.execute(
            "INSERT INTO irs527_director_donor_matches (ein, org_name, director_name, donor_key, donor_name, score) "
            "VALUES ('123456789', 'Test 527 Org', 'Jane Director', 'jane|director', 'Jane Director', 0.95)"
        )
        conn.execute(
            "INSERT INTO irs527_director_candidate_matches (ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score) "
            "VALUES ('123456789', 'Test 527 Org', 'Jane Director', '1001', 'Jane Director', 'state', 0.92)"
        )
        conn.execute(
            "INSERT INTO irs527_reports (form_id, ein, total_contributions, total_expenditures) "
            "VALUES (100, '123456789', 10000, 5000)"
        )
        conn.commit()
        conn.close()

        from webapp.app import create_app
        app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_detail_shows_director_donor_matches(self, client):
        response = client.get('/527/123456789')
        assert response.status_code == 200
        assert b'Jane Director' in response.data
        # Should show matched donor info
        assert b'jane|director' in response.data or b'Matched Donor' in response.data

    def test_detail_shows_director_candidate_matches(self, client):
        response = client.get('/527/123456789')
        assert response.status_code == 200
        # Should show candidate match info
        assert b'Matched Candidate' in response.data or b'candidate' in response.data.lower()


# ---------------------------------------------------------------------------
# 8. Person Intelligence Route Tests
# ---------------------------------------------------------------------------

class TestPersonIntelligence:

    @pytest.fixture
    def app(self, tmp_path: Path):
        db_path = str(tmp_path / "test_person_intel.db")
        init_db(db_path)
        conn = get_db(db_path)

        # Seed some data for "Jane Smith"
        conn.execute(
            "INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state) "
            "VALUES (100, '123456789', 'Test Org', 'Jane Smith', 'Director', 'Chicago', 'IL')"
        )
        conn.execute(
            """INSERT OR REPLACE INTO analytics_donor_summary
               (source, donor_key, donor_name, total_amount, contribution_count, committee_count)
               VALUES ('bulk_receipts', 'jane|smith', 'Jane Smith', 5000, 3, 2)"""
        )
        conn.commit()
        conn.close()

        from webapp.app import create_app
        app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_person_intelligence_page_loads(self, client):
        response = client.get('/person-intelligence')
        assert response.status_code == 200

    def test_person_intelligence_search(self, client):
        response = client.get('/person-intelligence?q=Jane+Smith')
        assert response.status_code == 200
        assert b'Jane Smith' in response.data

    def test_person_intelligence_empty_query(self, client):
        response = client.get('/person-intelligence?q=')
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 9. Dashboard 527 Summary Card Tests
# ---------------------------------------------------------------------------

class TestDashboard527Card:

    @pytest.fixture
    def app(self, tmp_path: Path):
        db_path = str(tmp_path / "test_dashboard.db")
        init_db(db_path)
        conn = get_db(db_path)

        conn.execute(
            "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state) "
            "VALUES ('123456789', 100, 0, 'Test Org', 'Chicago', 'IL')"
        )
        conn.execute(
            "INSERT INTO irs527_reports (form_id, ein, total_contributions, total_expenditures) "
            "VALUES (100, '123456789', 10000, 5000)"
        )
        conn.execute(
            "INSERT INTO irs527_director_donor_matches (ein, org_name, director_name, donor_key, donor_name, score) "
            "VALUES ('123456789', 'Test Org', 'Director A', 'dir|a', 'Director A', 0.90)"
        )
        conn.commit()
        conn.close()

        from webapp.app import create_app
        app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_dashboard_has_527_summary(self, client):
        response = client.get('/')
        assert response.status_code == 200
        # Dashboard should contain 527 summary data
        assert b'527' in response.data
        assert b'Dark Money' in response.data or b'dark-money' in response.data.lower()


# ---------------------------------------------------------------------------
# 10. Integration: run_all_cross_matching includes new functions
# ---------------------------------------------------------------------------

class TestRunAllCrossMatchingExpanded:

    def test_run_all_includes_new_matching(self, tmp_path: Path):
        from database.cross_matching import run_all_cross_matching
        conn = _setup_db(tmp_path)

        results = run_all_cross_matching(conn, threshold=0.80)
        # Should include new matching types
        assert "527_director_candidates" in results
        assert "527_director_addresses" in results
        assert "527_org_addresses" in results
        conn.close()
