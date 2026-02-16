"""Tests for the Flask web application."""
import pytest
import sys
import os
import re
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp.app import create_app
import webapp.routes.main as main_routes
from database.connection import init_db, get_db
from database.models import Committee, Report, Donor, Contribution, AppUser, D2ReceiptsRecon


@pytest.fixture
def app(tmp_path: Path):
    """Create a test application."""
    db_path = str(tmp_path / "test_webapp.db")
    init_db(db_path)

    # Seed a minimum dataset used by detail routes.
    conn = get_db(db_path)
    committee = Committee.get_or_create(conn, "Test Committee")
    report = Report(
        committee_id=committee.id,
        report_type="A-1 ($1000+ Year Round)",
        reporting_period="Q1 2026",
        filed_date="02/01/2026",
        pages=3,
        detail_url="https://www.elections.il.gov/CampaignDisclosure/A1List.aspx?FiledDocID=abc",
        scrape_status="scraped",
    ).save(conn)
    donor = Donor.get_or_create(conn, "Jane Donor", "123 Main St", "jane donor", "123 main st")
    Contribution(
        report_id=report.id,
        donor_id=donor.id,
        amount=250.0,
        received_by="Test Committee",
        description="Test contribution",
        raw_contributed_by="Jane Donor",
        raw_address="123 Main St",
    ).save(conn)
    AppUser.create_or_update_password(conn, "manual_admin", "secret123")
    conn.close()

    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': db_path
    })
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


def _extract_csrf_token(html: bytes) -> str:
    match = re.search(rb'name="csrf_token"\s+value="([^"]+)"', html)
    assert match is not None
    return match.group(1).decode("utf-8")


def _login_manual_user(client, *, next_url="/manual-entry/"):
    login_page = client.get(f"/auth/login?next={quote(next_url, safe='/')}")
    csrf_token = _extract_csrf_token(login_page.data)
    return client.post(
        "/auth/login",
        data={
            "username": "manual_admin",
            "password": "secret123",
            "next": next_url,
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )


class _CaptureSqlResult:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _CaptureSqlConn:
    def __init__(self):
        self.sql_history: list[str] = []

    def execute(self, sql, params=()):
        self.sql_history.append(sql)
        return _CaptureSqlResult()


class TestWebApp:
    """Tests for the web application."""

    def test_index_loads(self, client):
        """Test that the index page loads."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Dashboard' in response.data

    def test_index_reads_persisted_matched_donor_pairs(self, app, client):
        """Dashboard should show persisted federal/local donor pair count."""
        conn = get_db(app.config['DATABASE_PATH'])
        rows = [
            (f"fed-{idx}", f"local-{idx}", "name_state_zip")
            for idx in range(17)
        ]
        conn.executemany(
            """
            INSERT INTO fec_local_donor_matches (
                federal_donor_entity_key,
                local_donor_key,
                match_method
            ) VALUES (?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()

        response = client.get('/')
        assert response.status_code == 200
        assert b'Matched Donor Pairs' in response.data
        assert b'>17<' in response.data

    def test_index_shows_schedule_b_e_metric_cards(self, app, client):
        """Dashboard should show Schedule B and Schedule E metric cards with totals."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.executemany(
            """
            INSERT INTO fec_schedule_b_disbursements (
                sub_id, cycle, candidate_id, committee_id, recipient_name,
                disbursement_amount, disbursement_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("idx-sb-1", 2026, "H2IL00001", "C00000001", "Vendor A", 321.11, "2026-01-05"),
                ("idx-sb-2", 2026, "H2IL00001", "C00000001", "Vendor B", 123.45, "2026-01-07"),
            ],
        )
        conn.execute(
            """
            INSERT INTO fec_schedule_e_independent_expenditures (
                sub_id, cycle, candidate_id, committee_id, payee_name,
                support_oppose_indicator, expenditure_amount, expenditure_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("idx-se-1", 2026, "H2IL00001", "C000IE001", "Payee A", "S", 987.65, "2026-01-10"),
        )
        conn.commit()
        conn.close()

        response = client.get('/')
        assert response.status_code == 200
        assert b'Federal Disbursements (Schedule B)' in response.data
        assert b'Federal Independent Expenditures (Schedule E)' in response.data
        assert b'$444.56 total' in response.data
        assert b'$987.65 total' in response.data

    def test_committees_page_loads(self, client):
        """Test that the committees page loads."""
        response = client.get('/committees/')
        assert response.status_code == 200

    def test_donors_page_loads(self, client):
        """Test that the donors page loads."""
        response = client.get('/donors/')
        assert response.status_code == 200

    def test_donor_detail_respects_transaction_date_period_filter(self, app, client):
        """Donor detail should filter contributions by contribution transaction_date."""
        conn = get_db(app.config['DATABASE_PATH'])
        committee = Committee.get_or_create(conn, "Period Filter Committee")
        report = Report(
            committee_id=committee.id,
            report_type="A-1 ($1000+ Year Round)",
            reporting_period="Q2 2026",
            filed_date="06/01/2026",
            pages=2,
            detail_url="https://example.com/period-filter-report",
            scrape_status="scraped",
        ).save(conn)
        donor = Donor.get_or_create(
            conn,
            "Period Filter Donor",
            "777 Date Ln",
            "period filter donor",
            "777 date ln",
        )
        Contribution(
            report_id=report.id,
            donor_id=donor.id,
            amount=100.0,
            transaction_date="2024-05-10",
            description="Older Window Contribution",
            raw_contributed_by="Period Filter Donor",
            raw_address="777 Date Ln",
        ).save(conn)
        Contribution(
            report_id=report.id,
            donor_id=donor.id,
            amount=150.0,
            transaction_date="2025-07-22",
            description="Current Window Contribution",
            raw_contributed_by="Period Filter Donor",
            raw_address="777 Date Ln",
        ).save(conn)
        conn.close()

        cycle_response = client.get(f"/donors/{donor.id}?period=2026cycle")
        assert cycle_response.status_code == 200
        assert b'Current Window Contribution' in cycle_response.data
        assert b'Older Window Contribution' not in cycle_response.data

        all_time_response = client.get(f"/donors/{donor.id}?period=all")
        assert all_time_response.status_code == 200
        assert b'Current Window Contribution' in all_time_response.data
        assert b'Older Window Contribution' in all_time_response.data

    def test_openbook_list_page_loads(self, app, client):
        """OpenBook list route should render matched vendor aggregates."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            ("TEST VENDOR LLC", "expenditures"),
        )
        seed_id = conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
            ("TEST VENDOR LLC",),
        ).fetchone()["seed_id"]
        conn.execute(
            """
            INSERT INTO openbook_vendor_match (
                seed_id, openbook_vendor_key, openbook_vendor_label, match_method, confidence, search_term_used
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (seed_id, "TEST VENDOR", "Test Vendor", "exact", 1.0, "TEST VENDOR"),
        )
        conn.execute(
            """
            INSERT INTO openbook_contracts_raw (
                openbook_vendor_key, vendor_label, fiscal_year, agency_name, contract_number, award_amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TEST VENDOR", "Test Vendor", 2026, "Agency A", "CN-1", 12345.67, "https://example.com", "hash-contract-1"),
        )
        conn.execute(
            """
            INSERT INTO openbook_contributions_raw (
                openbook_vendor_key, contributor_name, recipient_name, contribution_date, amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("TEST VENDOR", "Donor A", "Recipient A", "2026-01-01", 250.0, "https://example.com", "hash-contrib-1"),
        )
        conn.commit()
        conn.close()

        response = client.get('/openbook/')
        assert response.status_code == 200
        assert b'OpenBook Vendor Matches' in response.data
        assert b'Test Vendor' in response.data

    def test_openbook_detail_page_loads(self, app, client):
        """OpenBook detail route should render contracts, provenance, and contributions."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            ("ANOTHER VENDOR", "lobbying"),
        )
        seed_id = conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
            ("ANOTHER VENDOR",),
        ).fetchone()["seed_id"]
        conn.execute(
            """
            INSERT INTO openbook_vendor_match (
                seed_id, openbook_vendor_key, openbook_vendor_label, match_method, confidence, search_term_used
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (seed_id, "ANOTHER VENDOR", "Another Vendor", "prefix", 0.9, "ANOTHER VENDOR"),
        )
        conn.execute(
            """
            INSERT INTO openbook_contracts_raw (
                openbook_vendor_key, vendor_label, fiscal_year, agency_name, contract_number, award_amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ANOTHER VENDOR", "Another Vendor", 2025, "Agency B", "CN-2", 5000.0, "https://example.com", "hash-contract-2"),
        )
        conn.execute(
            """
            INSERT INTO openbook_contributions_raw (
                openbook_vendor_key, contributor_name, recipient_name, contribution_date, amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("ANOTHER VENDOR", "Donor B", "Recipient B", "2025-04-10", 100.0, "https://example.com", "hash-contrib-2"),
        )
        conn.commit()
        conn.close()

        response = client.get('/openbook/ANOTHER%20VENDOR')
        assert response.status_code == 200
        assert b'Another Vendor' in response.data
        assert b'Seed Provenance' in response.data
        assert b'CN-2' in response.data

    def test_openbook_list_empty_tables(self, app, client):
        """OpenBook list renders gracefully when tables exist but have no matched data."""
        response = client.get('/openbook/')
        assert response.status_code == 200
        assert b'No OpenBook vendors found.' in response.data

    def test_openbook_detail_nonexistent_vendor(self, app, client):
        """OpenBook detail returns 404 for a vendor key not in the database."""
        response = client.get('/openbook/NONEXISTENT%20VENDOR%20XYZ')
        assert response.status_code == 404

    def test_openbook_list_search_filter(self, app, client):
        """OpenBook list ?q= filter returns only matching vendors."""
        conn = get_db(app.config['DATABASE_PATH'])
        for seed_text, vendor_key, label in [
            ("ALPHA CORP", "ALPHA CORP", "Alpha Corp"),
            ("BETA LLC", "BETA LLC", "Beta LLC"),
        ]:
            conn.execute(
                "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
                (seed_text, "expenditures"),
            )
            seed_id = conn.execute(
                "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
                (seed_text,),
            ).fetchone()["seed_id"]
            conn.execute(
                """INSERT INTO openbook_vendor_match (
                    seed_id, openbook_vendor_key, openbook_vendor_label,
                    match_method, confidence, search_term_used
                ) VALUES (?, ?, ?, 'exact', 1.0, ?)""",
                (seed_id, vendor_key, label, vendor_key),
            )
        conn.commit()
        conn.close()

        response = client.get('/openbook/?q=ALPHA')
        assert response.status_code == 200
        assert b'Alpha Corp' in response.data
        assert b'Beta LLC' not in response.data

    def test_openbook_detail_empty_contracts(self, app, client):
        """OpenBook detail renders zero-state tables when vendor has match but no contracts."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            ("EMPTY VENDOR", "lobbying"),
        )
        seed_id = conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
            ("EMPTY VENDOR",),
        ).fetchone()["seed_id"]
        conn.execute(
            """INSERT INTO openbook_vendor_match (
                seed_id, openbook_vendor_key, openbook_vendor_label,
                match_method, confidence, search_term_used
            ) VALUES (?, ?, ?, 'prefix', 0.85, ?)""",
            (seed_id, "EMPTY VENDOR", "Empty Vendor", "EMPTY VENDOR"),
        )
        conn.commit()
        conn.close()

        response = client.get('/openbook/EMPTY%20VENDOR')
        assert response.status_code == 200
        assert b'Empty Vendor' in response.data
        assert b'No contracts scraped for this vendor yet.' in response.data
        assert b'No contribution rows scraped for this vendor yet.' in response.data
        assert b'No warrant-level payment rows scraped yet.' in response.data

    def test_openbook_dashboard_stats_card(self, app, client):
        """Homepage dashboard should render OpenBook stats card with correct counts."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            ("DASH VENDOR", "expenditures"),
        )
        seed_id = conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
            ("DASH VENDOR",),
        ).fetchone()["seed_id"]
        conn.execute(
            """INSERT INTO openbook_vendor_match (
                seed_id, openbook_vendor_key, openbook_vendor_label,
                match_method, confidence, search_term_used
            ) VALUES (?, ?, ?, 'exact', 1.0, ?)""",
            (seed_id, "DASH VENDOR", "Dash Vendor", "DASH VENDOR"),
        )
        conn.execute(
            """INSERT INTO openbook_contracts_raw (
                openbook_vendor_key, vendor_label, fiscal_year, agency_name,
                contract_number, award_amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("DASH VENDOR", "Dash Vendor", 2026, "Agency D", "CN-D1", 99000.0, "https://example.com", "hash-dash-1"),
        )
        conn.execute(
            """INSERT INTO openbook_contracts_raw (
                openbook_vendor_key, vendor_label, fiscal_year, agency_name,
                contract_number, award_amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("DASH VENDOR", "Dash Vendor", 2025, "Agency D", "CN-D2", 1000.0, "https://example.com", "hash-dash-2"),
        )
        conn.commit()
        conn.close()

        response = client.get('/')
        assert response.status_code == 200
        assert b'OpenBook Vendors Matched' in response.data
        assert b'2 contracts' in response.data

    def test_openbook_detail_contribution_amounts(self, app, client):
        """OpenBook detail page renders contribution amounts correctly."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)",
            ("CONTRIB VENDOR", "expenditures"),
        )
        seed_id = conn.execute(
            "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?",
            ("CONTRIB VENDOR",),
        ).fetchone()["seed_id"]
        conn.execute(
            """INSERT INTO openbook_vendor_match (
                seed_id, openbook_vendor_key, openbook_vendor_label,
                match_method, confidence, search_term_used
            ) VALUES (?, ?, ?, 'exact', 0.95, ?)""",
            (seed_id, "CONTRIB VENDOR", "Contrib Vendor", "CONTRIB VENDOR"),
        )
        conn.execute(
            """INSERT INTO openbook_contributions_raw (
                openbook_vendor_key, contributor_name, recipient_name,
                contribution_date, amount, source_url, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("CONTRIB VENDOR", "Big Donor", "Sen. Smith", "2026-03-15", 5000.0, "https://example.com", "hash-c-1"),
        )
        conn.commit()
        conn.close()

        response = client.get('/openbook/CONTRIB%20VENDOR?period=all')
        assert response.status_code == 200
        assert b'Big Donor' in response.data
        assert b'Sen. Smith' in response.data
        assert b'2026-03-15' in response.data

    def test_reports_page_loads(self, client):
        """Test that the reports page loads."""
        response = client.get('/reports/')
        assert response.status_code == 200

    def test_reports_page_respects_filed_date_period_filter(self, app, client):
        """Reports list should filter report rows by report filed_date."""
        conn = get_db(app.config['DATABASE_PATH'])
        committee = Committee.get_or_create(conn, "Old Report Committee")
        Report(
            committee_id=committee.id,
            report_type="Older Cycle Report",
            reporting_period="Q2 2024",
            filed_date="03/15/2024",
            pages=1,
            detail_url="https://example.com/older-cycle-report",
            scrape_status="scraped",
        ).save(conn)
        conn.close()

        cycle_response = client.get('/reports/?period=2026cycle')
        assert cycle_response.status_code == 200
        assert b'Older Cycle Report' not in cycle_response.data

        all_time_response = client.get('/reports/?period=all')
        assert all_time_response.status_code == 200
        assert b'Older Cycle Report' in all_time_response.data

    def test_search_short_non_numeric_query_is_guarded(self, client):
        """Global search should guard very short non-numeric wildcard queries."""
        response = client.get('/search?q=a&type=all')
        assert response.status_code == 200
        assert b'Enter at least 2 characters' in response.data
        assert b'Search runtime' in response.data

    def test_live_feed_page_loads(self, client):
        """Live feed page should render both local and federal sections."""
        response = client.get('/live-feed')
        assert response.status_code == 200
        assert b'Live Donation Feed' in response.data
        assert b'Local Candidate Donations' in response.data
        assert b'Federal Candidate Donations' in response.data
        assert b'Federal Committee Disbursements (Schedule B)' in response.data
        assert b'Federal Independent Expenditures (Schedule E)' in response.data

    def test_analytics_networks_tab_css_hooks_present(self, client):
        """Analytics networks page should retain baseline stylesheet/scaffold markup."""
        response = client.get('/analytics/networks')
        assert response.status_code == 200
        assert b'/static/css/style.css' in response.data
        assert b'Analytics: Networks' in response.data
        assert b'class="search-form analytics-form"' in response.data

    def test_candidate_finance_page_loads_without_bulk_table(self, client):
        """Test candidate finance page renders guidance when bulk table is missing."""
        response = client.get('/candidate-finance/')
        assert response.status_code == 200
        assert b'bulk candidate finance table is not available yet' in response.data.lower()

    def test_candidate_finance_page_loads_with_bulk_table(self, app, client):
        """Test candidate finance page renders imported aggregate data and supports query params."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
        conn.execute(
            """
            CREATE TABLE bulk_candidate_committee_finance_agg (
                candidate_id INTEGER,
                candidate_full_name TEXT,
                office_sought TEXT,
                district_type TEXT,
                district TEXT,
                candidate_party_affiliation TEXT,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                committee_type TEXT,
                committee_party_affiliation TEXT,
                period_year INTEGER,
                election_cycle INTEGER,
                filing_count INTEGER,
                sum_total_receipts REAL,
                sum_total_expenditures REAL,
                max_ending_funds_available REAL,
                archived_filing_count INTEGER,
                period_start_date TEXT,
                period_end_date TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_candidate_committee_finance_agg (
                candidate_id, candidate_full_name, office_sought, district_type, district,
                candidate_party_affiliation, committee_id_sbe, committee_name, committee_type,
                committee_party_affiliation, period_year, election_cycle, filing_count, sum_total_receipts,
                sum_total_expenditures, max_ending_funds_available, archived_filing_count, period_start_date, period_end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (201, "Jordan Smith", "Governor", "Statewide", "At-Large", "Democratic",
                 101, "Committee A", "Political Action", "Democratic", 2025, 2026, 1, 3000.0, 1200.0, 500.0, 0, "2025-01-01", "2025-01-31"),
                (202, "Casey Jones", "Mayor", "Municipal", "7", "Independent",
                 102, "Committee B", "Political Party", "Republican", 2026, 2026, 2, 5000.0, 900.0, 700.0, 1, "2026-01-01", "2026-02-28"),
            ]
        )
        conn.commit()
        conn.close()

        response = client.get('/candidate-finance/?sort=sum_total_receipts&dir=desc&period=all')
        assert response.status_code == 200
        assert b'Candidate Committee Finance' in response.data
        assert response.data.find(b'Casey Jones') < response.data.find(b'Jordan Smith')
        assert b'/candidate-finance/202/102/itemized' in response.data

        filtered = client.get('/candidate-finance/?q=Jordan&period=all')
        assert filtered.status_code == 200
        assert b'Jordan Smith' in filtered.data
        assert b'Casey Jones' not in filtered.data

        filtered_by_party = client.get('/candidate-finance/?candidate_party=Democratic&min_receipts=2500&period=all')
        assert filtered_by_party.status_code == 200
        assert b'Jordan Smith' in filtered_by_party.data
        assert b'Casey Jones' not in filtered_by_party.data

        filtered_by_year = client.get('/candidate-finance/?year=2025&period=all')
        assert filtered_by_year.status_code == 200
        assert b'Jordan Smith' in filtered_by_year.data
        assert b'Casey Jones' not in filtered_by_year.data

        filtered_by_cycle = client.get('/candidate-finance/?cycle=2026&period=all')
        assert filtered_by_cycle.status_code == 200
        assert b'Jordan Smith' in filtered_by_cycle.data
        assert b'Casey Jones' in filtered_by_cycle.data

        exported = client.get('/candidate-finance/?format=csv&committee_party=Republican&period=all')
        assert exported.status_code == 200
        assert exported.mimetype == 'text/csv'
        assert 'attachment; filename=candidate_committee_finance.csv' in exported.headers.get('Content-Disposition', '')
        assert b'candidate_id,candidate_full_name,office_sought' in exported.data
        assert b'period_year,election_cycle' in exported.data
        assert b'Casey Jones' in exported.data
        assert b'Jordan Smith' not in exported.data

    def test_federal_finance_page_loads_without_synced_rows(self, client):
        """Test federal finance page loads and shows empty-data guidance."""
        response = client.get('/federal-finance/')
        assert response.status_code == 200
        assert b'Federal Finance (FEC)' in response.data
        assert b'Federal finance section navigation' in response.data
        assert b'No synced FEC candidate rows found' in response.data

    def test_federal_finance_page_loads_with_synced_rows(self, app, client):
        """Test federal finance page and detail route render synced candidate and contribution data."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key, as_of_date, cycle, office, office_code, district, district_code,
                party, party_code, election_stage, candidate_name, normalized_candidate_name,
                write_in, already_listed_general, source_file, source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-key-1',
                '2026-02-07',
                2026,
                'U.S. House',
                'H',
                'IL-01',
                '01',
                'Democratic',
                'DEM',
                'Primary',
                'Jonathan Jackson',
                'JONATHAN JACKSON',
                0,
                0,
                'test_seed.csv',
                2,
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_match (
                seed_candidate_key, candidate_name, office, office_code, district, district_code,
                party, party_code, election_stage, cycle,
                fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
                match_status, match_score, match_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-key-1',
                'Jonathan Jackson',
                'U.S. House',
                'H',
                'IL-01',
                '01',
                'Democratic',
                'DEM',
                'Primary',
                2026,
                'H2IL01349',
                'JACKSON, JONATHAN',
                'H',
                'IL',
                '01',
                'DEM',
                'matched',
                95.0,
                'test',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key, as_of_date, cycle, office, office_code, district, district_code,
                party, party_code, election_stage, candidate_name, normalized_candidate_name,
                write_in, already_listed_general, source_file, source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-key-2',
                '2026-02-07',
                2026,
                'U.S. House',
                'H',
                'IL-09',
                '09',
                'Republican',
                'REP',
                'Primary',
                'Jonathan Smith',
                'JONATHAN SMITH',
                0,
                0,
                'test_seed.csv',
                3,
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_match (
                seed_candidate_key, candidate_name, office, office_code, district, district_code,
                party, party_code, election_stage, cycle,
                fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
                match_status, match_score, match_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-key-2',
                'Jonathan Smith',
                'U.S. House',
                'H',
                'IL-09',
                '09',
                'Republican',
                'REP',
                'Primary',
                2026,
                'H2IL09999',
                'SMITH, JONATHAN',
                'H',
                'IL',
                '09',
                'REP',
                'matched',
                91.0,
                'test',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_committees (
                candidate_id, committee_id, cycle, committee_name, committee_type,
                committee_designation, committee_designation_full, filing_frequency,
                committee_party, committee_city, committee_state, committee_zip, is_principal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'H2IL01349',
                'C00011111',
                2026,
                'JONATHAN JACKSON FOR CONGRESS',
                'H',
                'P',
                'Principal campaign committee',
                'Q',
                'DEM',
                'WASHINGTON',
                'DC',
                '20005',
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_committees (
                candidate_id, committee_id, cycle, committee_name, committee_type,
                committee_designation, committee_designation_full, filing_frequency,
                committee_party, committee_city, committee_state, committee_zip, is_principal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'H2IL09999',
                'C00022222',
                2026,
                'SMITH FOR CONGRESS',
                'H',
                'P',
                'Principal campaign committee',
                'Q',
                'REP',
                'CHICAGO',
                'IL',
                '60607',
                1,
            ),
        )
        conn.executemany(
            """
            INSERT INTO fec_schedule_a_contributions (
                sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
                contributor_name, contributor_city, contributor_state, contributor_zip,
                contributor_employer, contributor_occupation, contributor_id,
                is_individual, line_number, receipt_type, receipt_type_desc, memo_text,
                contribution_receipt_amount, contribution_receipt_date, two_year_transaction_period,
                donor_key, load_date, image_number, api_source_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'sub-1',
                    2026,
                    'H2IL01349',
                    'JACKSON, JONATHAN',
                    'C00011111',
                    'JONATHAN JACKSON FOR CONGRESS',
                    'Jane Donor',
                    'Chicago',
                    'IL',
                    '60601',
                    'ACME',
                    'Engineer',
                    None,
                    1,
                    '11AI',
                    'IND',
                    'Individual contribution',
                    '',
                    250.0,
                    '2026-01-15',
                    2026,
                    'donor-key-1',
                    '2026-01-20T01:02:03',
                    '202601209000000001',
                    'source-1',
                ),
                (
                    'sub-2',
                    2026,
                    'H2IL01349',
                    'JACKSON, JONATHAN',
                    'C00011111',
                    'JONATHAN JACKSON FOR CONGRESS',
                    'Jane Donor',
                    'Chicago',
                    'IL',
                    '60601',
                    'ACME',
                    'Engineer',
                    None,
                    1,
                    '11AI',
                    'IND',
                    'Individual contribution',
                    '',
                    100.0,
                    '2026-01-10',
                    2026,
                    'donor-key-1',
                    '2026-01-18T01:02:03',
                    '202601189000000001',
                    'source-2',
                ),
                (
                    'sub-3',
                    2026,
                    'H2IL09999',
                    'SMITH, JONATHAN',
                    'C00022222',
                    'SMITH FOR CONGRESS',
                    'JANE DONOR',
                    'CHICAGO',
                    'IL',
                    '60601',
                    'ACME',
                    'Engineer',
                    None,
                    1,
                    '11AI',
                    'IND',
                    'Individual contribution',
                    '',
                    75.0,
                    '2026-01-20',
                    2026,
                    'donor-key-3',
                    '2026-01-20T01:02:03',
                    '202601209000000003',
                    'source-3',
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO fec_candidate_cycle_totals (
                candidate_id, cycle, receipts, disbursements, contributions, individual_contributions,
                coverage_start_date, coverage_end_date, transaction_coverage_date, last_report_year,
                last_report_type_full, last_cash_on_hand_end_period, source_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'H2IL01349',
                    2026,
                    1200.0,
                    900.0,
                    1200.0,
                    1100.0,
                    '2026-01-01',
                    '2026-03-31',
                    '2026-03-31',
                    2026,
                    'Q1',
                    5000.0,
                    '{"source":"test"}',
                ),
                (
                    'H2IL09999',
                    2026,
                    900.0,
                    450.0,
                    900.0,
                    900.0,
                    '2026-01-01',
                    '2026-03-31',
                    '2026-03-31',
                    2026,
                    'Q1',
                    3500.0,
                    '{"source":"test"}',
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO fec_schedule_b_disbursements (
                sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
                recipient_name, recipient_city, recipient_state, recipient_zip,
                disbursement_type_desc, category_code_full, disbursement_amount, disbursement_date,
                api_source_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'sb-1',
                    2026,
                    'H2IL01349',
                    'JACKSON, JONATHAN',
                    'C00011111',
                    'JONATHAN JACKSON FOR CONGRESS',
                    'MEDIA BUY VENDOR',
                    'Chicago',
                    'IL',
                    '60601',
                    'Operating Expenditure',
                    'Advertising Expenses',
                    350.0,
                    '2026-01-20',
                    'src-sb-1',
                ),
                (
                    'sb-2',
                    2026,
                    'H2IL01349',
                    'JACKSON, JONATHAN',
                    'C00011111',
                    'JONATHAN JACKSON FOR CONGRESS',
                    'PRINT SHOP',
                    'Chicago',
                    'IL',
                    '60607',
                    'Operating Expenditure',
                    'Printing',
                    275.0,
                    '2026-01-18',
                    'src-sb-2',
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO fec_schedule_e_independent_expenditures (
                sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
                payee_name, payee_city, payee_state, payee_zip,
                support_oppose_indicator, category_code_full, expenditure_amount, expenditure_date,
                report_type, line_number, api_source_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'se-1',
                    2026,
                    'H2IL01349',
                    'JACKSON, JONATHAN',
                    'C000IE111',
                    'INDEPENDENT EXPENDITURE PAC',
                    'AD CREATIVE STUDIO',
                    'Chicago',
                    'IL',
                    '60602',
                    'S',
                    'Communications',
                    640.75,
                    '2026-01-22',
                    '48H3',
                    '24A',
                    'src-se-1',
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address, donor_city, donor_state,
                occupation, employer, total_amount, contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'bulk_receipts',
                'local-key-1',
                701,
                'Jane Donor',
                '123 MAIN ST, CHICAGO, IL 60601',
                'Chicago',
                'IL',
                'Engineer',
                'ACME',
                610.0,
                5,
                2,
            ),
        )
        conn.executemany(
            """
            INSERT INTO analytics_donor_committee_agg (
                source, donor_key, donor_name, donor_address, donor_city, donor_state,
                committee_id, committee_name, total_amount, contribution_count, occupation, employer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'bulk_receipts',
                    'local-key-1',
                    'Jane Donor',
                    '123 MAIN ST, CHICAGO, IL 60601',
                    'Chicago',
                    'IL',
                    'L-COM-1',
                    'Friends of Springfield',
                    350.0,
                    3,
                    'Engineer',
                    'ACME',
                ),
                (
                    'bulk_receipts',
                    'local-key-1',
                    'Jane Donor',
                    '123 MAIN ST, CHICAGO, IL 60601',
                    'Chicago',
                    'IL',
                    'L-COM-2',
                    'Citizens for Transit',
                    260.0,
                    2,
                    'Engineer',
                    'ACME',
                ),
            ],
        )
        conn.commit()
        conn.close()

        overview = client.get('/federal-finance/?cycle=2026')
        assert overview.status_code == 200
        assert b'Federal Finance (FEC)' in overview.data
        assert b'Top Federal Races' in overview.data
        assert b'Explore More' in overview.data
        assert b'Schedule B Rows' in overview.data
        assert b'Schedule E Rows' in overview.data
        assert b'Outside Spending (E)' in overview.data
        assert b'Outside Pressure Ratio' in overview.data
        assert b'/federal-finance/races/H/01/outside-spending?cycle=2026' in overview.data
        assert b'$625.00 total disbursed' in overview.data
        assert b'$640.75 total independent expenditures' in overview.data
        assert b'$425.00' in overview.data
        conn = get_db(app.config['DATABASE_PATH'])
        snapshot_row = conn.execute(
            """
            SELECT cache_key, status
            FROM analytics_snapshots
            WHERE snapshot_type = 'federal_overview'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        assert snapshot_row is not None
        assert snapshot_row['status'] == 'completed'

        candidates = client.get('/federal-finance/candidates?cycle=2026')
        assert candidates.status_code == 200
        assert b'Federal Candidates' in candidates.data
        assert b'Jonathan Jackson' in candidates.data
        assert b'H2IL01349' in candidates.data
        assert b'$1,200.00' in candidates.data

        networks = client.get('/federal-finance/networks?cycle=2026&network_min_edge_amount=0')
        assert networks.status_code == 200
        assert b'Federal Donor-Candidate Network' in networks.data
        assert b'id="federal-network-svg"' in networks.data
        assert b'id="federal-graph-mode"' in networks.data
        assert b'Trend Matrix' in networks.data
        assert b'Interactive Force' not in networks.data
        assert b'id="federal-highlight-race"' in networks.data
        assert b'id="federal-highlight-party"' in networks.data
        assert b'Ranked Donor-Candidate Flows' in networks.data
        assert b'id="federal-flow-table"' in networks.data
        assert b'id="federal-flow-min"' in networks.data
        assert b'id="federal-flow-limit"' in networks.data
        assert b'id="federal-flow-search"' in networks.data
        # Multilayer, cross-role, and overlap moved to Money Flow / Matching pages
        assert b'Multi-layer Money Flow Network (A/B/E)' not in networks.data
        assert b'Cross-Role Organizations (Donor and Payee)' not in networks.data

        money_flow = client.get('/federal-finance/money-flow?cycle=2026&network_min_edge_amount=0')
        assert money_flow.status_code == 200
        assert b'Money Flow' in money_flow.data

        influence_page = client.get('/federal-finance/influence?cycle=2026')
        assert influence_page.status_code == 200
        assert b'Influence' in influence_page.data

        follow_page = client.get('/federal-finance/follow-the-money?cycle=2026')
        assert follow_page.status_code == 200
        assert b'Follow the Money' in follow_page.data

        geography_page = client.get('/federal-finance/geography?cycle=2026')
        assert geography_page.status_code == 200
        assert b'Geography' in geography_page.data or b'Geographic' in geography_page.data

        intelligence = client.get('/federal-finance/donor-intelligence?cycle=2026')
        assert intelligence.status_code == 200
        assert b'Federal Donor Intelligence' in intelligence.data
        assert b'Donor Segmentation' in intelligence.data
        assert b'Donor Network Clustering' in intelligence.data
        assert b'Community Treemap / Sunburst' in intelligence.data
        assert b'id="cluster-community-svg"' in intelligence.data
        # Influence and Follow the Money moved to their own pages
        assert b'Influence Scores - Donors' not in intelligence.data
        assert b'Follow the Money (Multi-Hop)' not in intelligence.data

        matching = client.get('/federal-finance/matching?cycle=2026')
        assert matching.status_code == 200
        assert b'Federal/Local Donor Matching' in matching.data
        assert b'Match Method Tiers' in matching.data
        assert b'Local vs Federal Overlap Bubble View' in matching.data
        assert b'id="match-overlap-bubble-svg"' in matching.data
        assert b'View Combined' in matching.data

        detail = client.get('/federal-finance/H2IL01349?cycle=2026')
        assert detail.status_code == 200
        assert b'Top Donors' in detail.data
        assert b'Jane Donor' in detail.data
        assert b'Recent Contributions' in detail.data
        assert b'Recent Disbursements (Schedule B)' in detail.data
        assert b'Independent Expenditures (Schedule E)' in detail.data
        assert b'Money In (A)' in detail.data
        assert b'Money Out (B)' in detail.data
        assert b'Outside Spending (E)' in detail.data
        assert b'Explicit Transfer Chains (Schedule B IDs)' in detail.data
        assert b'Cross-Role Organizations (A and B/E)' in detail.data
        assert b'Reported Disbursements:' in detail.data
        assert b'/federal-finance/donors/' in detail.data
        assert b'Total Source:' in detail.data
        assert b'FEC candidate totals endpoint' in detail.data
        assert b'Export CSV' in detail.data
        assert b'/federal-finance/committees/C00011111/receipts?cycle=2026' in detail.data

        committee_receipts = client.get('/federal-finance/committees/C00011111/receipts?cycle=2026')
        assert committee_receipts.status_code == 200
        assert b'Federal Committee Receipts' in committee_receipts.data
        assert b'Committee ID: C00011111' in committee_receipts.data
        assert b'JONATHAN JACKSON FOR CONGRESS' in committee_receipts.data
        assert b'Jane Donor' in committee_receipts.data

        detail_sorted = client.get(
            '/federal-finance/H2IL01349?cycle=2026&schedule_b_sort=amount&schedule_b_dir=asc&schedule_e_sort=amount&schedule_e_dir=asc'
        )
        assert detail_sorted.status_code == 200
        assert b'schedule_b_sort=amount' in detail_sorted.data
        assert b'schedule_e_sort=amount' in detail_sorted.data

        race_outside = client.get('/federal-finance/races/H/01/outside-spending?cycle=2026')
        assert race_outside.status_code == 200
        assert b'Independent Expenditures (Schedule E)' in race_outside.data
        assert b'INDEPENDENT EXPENDITURE PAC' in race_outside.data
        assert b'AD CREATIVE STUDIO' in race_outside.data

        race_outside_csv = client.get('/federal-finance/races/H/01/outside-spending?cycle=2026&format=csv')
        assert race_outside_csv.status_code == 200
        assert race_outside_csv.mimetype == 'text/csv'
        assert b'sub_id,cycle,candidate_id,candidate_name,expenditure_date' in race_outside_csv.data
        assert b'se-1' in race_outside_csv.data

        live_feed = client.get('/live-feed?local_limit=20&federal_limit=20&schedule_b_limit=20&schedule_e_limit=20')
        assert live_feed.status_code == 200
        assert b'Federal Committee Disbursements (Schedule B)' in live_feed.data
        assert b'Federal Independent Expenditures (Schedule E)' in live_feed.data
        assert b'MEDIA BUY VENDOR' in live_feed.data
        assert b'AD CREATIVE STUDIO' in live_feed.data
        assert b'Export Schedule B CSV' in live_feed.data
        assert b'Export Schedule E CSV' in live_feed.data

        schedule_b_csv = client.get('/federal-finance/H2IL01349?cycle=2026&format=csv&table=schedule_b')
        assert schedule_b_csv.status_code == 200
        assert schedule_b_csv.mimetype == 'text/csv'
        assert 'federal_candidate_H2IL01349_schedule_b.csv' in schedule_b_csv.headers.get('Content-Disposition', '')
        assert b'sub_id,cycle,candidate_id' in schedule_b_csv.data
        assert b'MEDIA BUY VENDOR' in schedule_b_csv.data

        schedule_e_csv = client.get('/federal-finance/H2IL01349?cycle=2026&format=csv&table=schedule_e')
        assert schedule_e_csv.status_code == 200
        assert schedule_e_csv.mimetype == 'text/csv'
        assert 'federal_candidate_H2IL01349_schedule_e.csv' in schedule_e_csv.headers.get('Content-Disposition', '')
        assert b'sub_id,cycle,candidate_id,expenditure_date' in schedule_e_csv.data
        assert b'AD CREATIVE STUDIO' in schedule_e_csv.data

        live_feed_schedule_b_csv = client.get('/live-feed?format=csv&table=schedule_b')
        assert live_feed_schedule_b_csv.status_code == 200
        assert live_feed_schedule_b_csv.mimetype == 'text/csv'
        assert 'live_feed_schedule_b.csv' in live_feed_schedule_b_csv.headers.get('Content-Disposition', '')
        assert b'disbursement_date,cycle,candidate_id' in live_feed_schedule_b_csv.data
        assert b'MEDIA BUY VENDOR' in live_feed_schedule_b_csv.data

        live_feed_schedule_e_csv = client.get('/live-feed?format=csv&table=schedule_e')
        assert live_feed_schedule_e_csv.status_code == 200
        assert live_feed_schedule_e_csv.mimetype == 'text/csv'
        assert 'live_feed_schedule_e.csv' in live_feed_schedule_e_csv.headers.get('Content-Disposition', '')
        assert b'expenditure_date,cycle,candidate_id' in live_feed_schedule_e_csv.data
        assert b'AD CREATIVE STUDIO' in live_feed_schedule_e_csv.data

        donor_key = None
        conn = get_db(app.config['DATABASE_PATH'])
        row = conn.execute(
            """
            SELECT donor_entity_key
            FROM fec_schedule_a_contributions
            WHERE candidate_id = 'H2IL01349'
            ORDER BY contribution_receipt_amount DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            donor_key = row["donor_entity_key"]
        conn.close()

        assert donor_key is not None
        donor_detail = client.get(f'/federal-finance/donors/{quote(donor_key)}?cycle=2026')
        assert donor_detail.status_code == 200
        assert b'Federal Donor Detail' in donor_detail.data
        assert b'Donations by Candidate' in donor_detail.data
        assert b'H2IL01349' in donor_detail.data
        assert b'H2IL09999' in donor_detail.data

        match_profile = client.get(f'/federal-finance/matches/{quote(donor_key)}/local-key-1?cycle=2026&local_source=bulk_receipts')
        assert match_profile.status_code == 200
        assert b'Matched Donor Profile (Federal + Local)' in match_profile.data
        assert b'Federal Contributions' in match_profile.data
        assert b'Local Donations (Committee Breakdown)' in match_profile.data
        assert b'Friends of Springfield' in match_profile.data

        follow = client.get(f'/federal-finance/follow-the-money?cycle=2026&follow_donor_key={quote(donor_key)}&follow_min_edge_amount=0')
        assert follow.status_code == 200
        assert b'Follow the Money' in follow.data

    def test_candidate_committee_itemized_page_loads_with_bulk_tables(self, app, client):
        """Test candidate/committee drill-down page renders itemized lines and supports filters/CSV."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_candidates_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_committees_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_committee_candidate_links")
        conn.execute("DROP TABLE IF EXISTS bulk_receipts_clean")

        conn.execute(
            """
            CREATE TABLE bulk_candidates_clean (
                candidate_id INTEGER,
                candidate_full_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_committees_clean (
                committee_id_sbe INTEGER,
                committee_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_committee_candidate_links (
                candidate_id INTEGER,
                committee_id_sbe INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_receipts_clean (
                receipt_record_id INTEGER,
                committee_id_sbe INTEGER,
                filed_doc_id INTEGER,
                received_date TEXT,
                d2_part_code TEXT,
                last_or_business_name TEXT,
                first_name TEXT,
                occupation TEXT,
                employer TEXT,
                address_line_1 TEXT,
                address_line_2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                amount REAL,
                aggregate_amount REAL,
                loan_amount REAL,
                description TEXT,
                vendor_last_or_business_name TEXT,
                vendor_first_name TEXT,
                vendor_address_line_1 TEXT,
                vendor_address_line_2 TEXT,
                vendor_city TEXT,
                vendor_state TEXT,
                vendor_postal_code TEXT,
                is_archived INTEGER,
                country TEXT,
                redaction_requested INTEGER
            )
            """
        )

        conn.execute("INSERT INTO bulk_candidates_clean (candidate_id, candidate_full_name) VALUES (201, 'Jordan Smith')")
        conn.execute("INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name) VALUES (101, 'Committee A')")
        conn.execute("INSERT INTO bulk_committee_candidate_links (candidate_id, committee_id_sbe) VALUES (201, 101)")
        conn.executemany(
            """
            INSERT INTO bulk_receipts_clean (
                receipt_record_id, committee_id_sbe, filed_doc_id, received_date, d2_part_code,
                last_or_business_name, first_name, occupation, employer,
                address_line_1, address_line_2, city, state, postal_code,
                amount, aggregate_amount, loan_amount, description,
                vendor_last_or_business_name, vendor_first_name,
                vendor_address_line_1, vendor_address_line_2, vendor_city, vendor_state, vendor_postal_code,
                is_archived, country, redaction_requested
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (3001, 101, 5001, "2025-01-10", "1A", "Bishop", "Elizabeth", "Politics", "City", "1 Main", "", "LaSalle", "IL", "61301", 100.0, 0.0, 0.0, "Contribution", "", "", "", "", "", "", "", 0, "", 0),
                (3002, 101, 5001, "2025-01-11", "5A", "Vendor", "Office", "", "", "2 Main", "", "Chicago", "IL", "60601", 50.0, 0.0, 0.0, "Printing", "Ink", "Co", "2 Main", "", "Chicago", "IL", "60601", 0, "", 0),
                (3003, 101, 5002, "2025-01-12", "1A", "Archived", "Donor", "", "", "3 Main", "", "Chicago", "IL", "60601", 200.0, 0.0, 0.0, "Old row", "", "", "", "", "", "", "", 1, "", 0),
            ],
        )
        conn.commit()
        conn.close()

        response = client.get('/candidate-finance/201/101/itemized?sort=amount&dir=desc')
        assert response.status_code == 200
        assert b'Candidate/Committee Itemized Receipts' in response.data
        assert b'Donor Archived' not in response.data
        assert b'Elizabeth Bishop' in response.data

        response_with_archived = client.get('/candidate-finance/201/101/itemized?sort=amount&dir=desc&archived=all')
        assert response_with_archived.status_code == 200
        assert response_with_archived.data.find(b'Donor Archived') < response_with_archived.data.find(b'Elizabeth Bishop')

        filtered = client.get('/candidate-finance/201/101/itemized?d2_part=1&min_amount=90&archived=no')
        assert filtered.status_code == 200
        assert b'Elizabeth Bishop' in filtered.data
        assert b'Donor Archived' not in filtered.data
        assert b'Office Vendor' not in filtered.data

        exported = client.get('/candidate-finance/201/101/itemized?format=csv&archived=no')
        assert exported.status_code == 200
        assert exported.mimetype == 'text/csv'
        assert 'candidate_201_committee_101_itemized.csv' in exported.headers.get('Content-Disposition', '')
        assert b'receipt_record_id,committee_id_sbe,candidate_id' in exported.data
        assert b'Elizabeth Bishop' in exported.data
        assert b'Donor Archived' not in exported.data

        missing_link = client.get('/candidate-finance/999/101/itemized')
        assert missing_link.status_code == 200
        assert b'No candidate/committee link was found' in missing_link.data

    def test_candidate_committee_itemized_expenditures_page_loads_with_bulk_tables(self, app, client):
        """Test candidate/committee expenditure drill-down page renders itemized lines and supports filters/CSV."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_candidates_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_committees_clean")
        conn.execute("DROP TABLE IF EXISTS bulk_committee_candidate_links")
        conn.execute("DROP TABLE IF EXISTS bulk_expenditures_clean")

        conn.execute(
            """
            CREATE TABLE bulk_candidates_clean (
                candidate_id INTEGER,
                candidate_full_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_committees_clean (
                committee_id_sbe INTEGER,
                committee_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_committee_candidate_links (
                candidate_id INTEGER,
                committee_id_sbe INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bulk_expenditures_clean (
                expenditure_record_id INTEGER,
                committee_id_sbe INTEGER,
                filed_doc_id INTEGER,
                expended_date TEXT,
                d2_part_code TEXT,
                payee_last_or_business_name TEXT,
                payee_first_name TEXT,
                address_line_1 TEXT,
                address_line_2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                amount REAL,
                aggregate_amount REAL,
                purpose TEXT,
                candidate_name TEXT,
                office TEXT,
                is_supporting INTEGER,
                is_opposing INTEGER,
                is_archived INTEGER,
                country TEXT,
                redaction_requested INTEGER,
                is_amount_anomalous INTEGER,
                anomaly_reason TEXT
            )
            """
        )

        conn.execute("INSERT INTO bulk_candidates_clean (candidate_id, candidate_full_name) VALUES (201, 'Jordan Smith')")
        conn.execute("INSERT INTO bulk_committees_clean (committee_id_sbe, committee_name) VALUES (101, 'Committee A')")
        conn.execute("INSERT INTO bulk_committee_candidate_links (candidate_id, committee_id_sbe) VALUES (201, 101)")
        conn.executemany(
            """
            INSERT INTO bulk_expenditures_clean (
                expenditure_record_id, committee_id_sbe, filed_doc_id, expended_date, d2_part_code,
                payee_last_or_business_name, payee_first_name,
                address_line_1, address_line_2, city, state, postal_code,
                amount, aggregate_amount, purpose, candidate_name, office,
                is_supporting, is_opposing, is_archived, country, redaction_requested,
                is_amount_anomalous, anomaly_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (7001, 101, 5001, "2025-01-10", "8B", "Media", "Vendor", "1 Main", "", "Chicago", "IL", "60601", 20000.0, 0.0, "Media buy", "", "", 0, 0, 0, "US", 0, 0, None),
                (7002, 101, 5001, "2025-01-11", "6A", "Transfer", "Target", "2 Main", "", "Chicago", "IL", "60601", 1500.0, 0.0, "Transfer out", "", "", 0, 0, 0, "US", 0, 1, "abs(amount)>=10000000"),
                (7003, 101, 5002, "2025-01-12", "8A", "Archived", "Vendor", "3 Main", "", "Chicago", "IL", "60601", 250.0, 0.0, "Old row", "", "", 0, 0, 1, "US", 0, 0, None),
            ],
        )
        conn.commit()
        conn.close()

        response = client.get('/candidate-finance/201/101/itemized-expenditures?sort=amount&dir=desc')
        assert response.status_code == 200
        assert b'Candidate/Committee Itemized Expenditures' in response.data
        assert b'Vendor Archived' not in response.data
        assert b'Vendor Media' in response.data

        filtered = client.get('/candidate-finance/201/101/itemized-expenditures?d2_part=6&anomalies_only=yes')
        assert filtered.status_code == 200
        assert b'Target Transfer' in filtered.data
        assert b'Vendor Media' not in filtered.data

        exported = client.get('/candidate-finance/201/101/itemized-expenditures?format=csv&archived=no')
        assert exported.status_code == 200
        assert exported.mimetype == 'text/csv'
        assert 'candidate_201_committee_101_itemized_expenditures.csv' in exported.headers.get('Content-Disposition', '')
        assert b'expenditure_record_id,committee_id_sbe,candidate_id' in exported.data
        assert b'Vendor Media' in exported.data
        assert b'Vendor Archived' not in exported.data

        missing_link = client.get('/candidate-finance/999/101/itemized-expenditures')
        assert missing_link.status_code == 200
        assert b'No candidate/committee link was found' in missing_link.data

    def test_d2_reconciliation_page_loads_without_bulk_table(self, client):
        """Test D2 reconciliation page renders guidance when table is missing."""
        response = client.get('/d2-reconciliation/')
        assert response.status_code == 200
        assert b'd2 reconciliation table is not available yet' in response.data.lower()

    def test_d2_reconciliation_page_loads_with_bulk_table(self, app, client):
        """Test D2 reconciliation page renders imported data and supports query params."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_d2_receipts_recon")
        conn.execute(
            """
            CREATE TABLE bulk_d2_receipts_recon (
                d2_totals_record_id INTEGER,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                filed_doc_id INTEGER,
                d2_total_receipts REAL,
                d2_total_expenditures REAL,
                ending_funds_available REAL,
                is_archived INTEGER,
                receipt_row_count INTEGER,
                receipts_amount_sum REAL,
                first_receipt_date TEXT,
                last_receipt_date TEXT,
                receipts_minus_d2_total REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_d2_receipts_recon (
                d2_totals_record_id, committee_id_sbe, committee_name, filed_doc_id,
                d2_total_receipts, d2_total_expenditures, ending_funds_available, is_archived,
                receipt_row_count, receipts_amount_sum, first_receipt_date, last_receipt_date,
                receipts_minus_d2_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 101, "Committee A", 5001, 1000.0, 600.0, 400.0, 0, 10, 950.0, "2025-01-01", "2025-01-31", -50.0),
                (2, 102, "Committee B", 5002, 500.0, 450.0, 75.0, 1, 5, 650.0, "2025-02-01", "2025-02-28", 150.0),
            ]
        )
        conn.commit()
        conn.close()

        response = client.get('/d2-reconciliation/?sort=abs_diff&dir=desc')
        assert response.status_code == 200
        assert b'D2 Receipts Reconciliation' in response.data
        assert response.data.find(b'Committee B') < response.data.find(b'Committee A')

        filtered = client.get('/d2-reconciliation/?q=Committee%20A&min_abs_diff=40')
        assert filtered.status_code == 200
        assert b'Committee A' in filtered.data
        assert b'Committee B' not in filtered.data

        exported = client.get('/d2-reconciliation/?format=csv&min_receipt_rows=6')
        assert exported.status_code == 200
        assert exported.mimetype == 'text/csv'
        assert 'attachment; filename=d2_receipts_reconciliation.csv' in exported.headers.get('Content-Disposition', '')
        assert b'd2_totals_record_id,committee_id_sbe,committee_name' in exported.data
        assert b'Committee A' in exported.data
        assert b'Committee B' not in exported.data

    def test_d2_expenditures_reconciliation_page_loads_without_bulk_table(self, client):
        """Test D2 expenditures reconciliation page renders guidance when table is missing."""
        response = client.get('/d2-expenditures-reconciliation/')
        assert response.status_code == 200
        assert b'd2 expenditures reconciliation table is not available yet' in response.data.lower()

    def test_d2_expenditures_reconciliation_page_loads_with_bulk_table(self, app, client):
        """Test D2 expenditures reconciliation page renders imported data and supports query params."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_d2_expenditures_recon")
        conn.execute(
            """
            CREATE TABLE bulk_d2_expenditures_recon (
                d2_totals_record_id INTEGER,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                filed_doc_id INTEGER,
                d2_transfers_out_itemized REAL,
                d2_loans_made_itemized REAL,
                d2_expenditures_itemized REAL,
                d2_independent_expenditures_itemized REAL,
                d2_itemized_expenditures_total REAL,
                d2_total_expenditures REAL,
                ending_funds_available REAL,
                is_archived INTEGER,
                expenditure_row_count INTEGER,
                expenditures_amount_sum REAL,
                sum_part_6_transfers_out REAL,
                sum_part_7_loans_made REAL,
                sum_part_8_expenditures REAL,
                sum_part_9_independent_expenditures REAL,
                anomaly_row_count INTEGER,
                first_expenditure_date TEXT,
                last_expenditure_date TEXT,
                expenditures_minus_d2_itemized_total REAL,
                part_6_minus_d2_transfers_out_itemized REAL,
                part_7_minus_d2_loans_made_itemized REAL,
                part_8_minus_d2_expenditures_itemized REAL,
                part_9_minus_d2_independent_expenditures_itemized REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_d2_expenditures_recon (
                d2_totals_record_id, committee_id_sbe, committee_name, filed_doc_id,
                d2_transfers_out_itemized, d2_loans_made_itemized, d2_expenditures_itemized,
                d2_independent_expenditures_itemized, d2_itemized_expenditures_total,
                d2_total_expenditures, ending_funds_available, is_archived,
                expenditure_row_count, expenditures_amount_sum,
                sum_part_6_transfers_out, sum_part_7_loans_made, sum_part_8_expenditures, sum_part_9_independent_expenditures,
                anomaly_row_count, first_expenditure_date, last_expenditure_date,
                expenditures_minus_d2_itemized_total,
                part_6_minus_d2_transfers_out_itemized, part_7_minus_d2_loans_made_itemized,
                part_8_minus_d2_expenditures_itemized, part_9_minus_d2_independent_expenditures_itemized
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 101, "Committee A", 5001, 100.0, 0.0, 900.0, 0.0, 1000.0, 1200.0, 400.0, 0, 12, 980.0, 90.0, 0.0, 890.0, 0.0, 0, "2025-01-01", "2025-01-31", -20.0, -10.0, 0.0, -10.0, 0.0),
                (2, 102, "Committee B", 5002, 50.0, 0.0, 250.0, 0.0, 300.0, 500.0, 100.0, 0, 9, 460.0, 70.0, 0.0, 390.0, 0.0, 2, "2025-02-01", "2025-02-28", 160.0, 20.0, 0.0, 140.0, 0.0),
            ],
        )
        conn.commit()
        conn.close()

        response = client.get('/d2-expenditures-reconciliation/?sort=abs_diff&dir=desc')
        assert response.status_code == 200
        assert b'D2 Expenditures Reconciliation' in response.data
        assert response.data.find(b'Committee B') < response.data.find(b'Committee A')

        filtered = client.get('/d2-expenditures-reconciliation/?q=Committee%20A&min_abs_diff=10')
        assert filtered.status_code == 200
        assert b'Committee A' in filtered.data
        assert b'Committee B' not in filtered.data

        anomalies = client.get('/d2-expenditures-reconciliation/?anomalies_only=yes')
        assert anomalies.status_code == 200
        assert b'Committee B' in anomalies.data
        assert b'Committee A' not in anomalies.data

        exported = client.get('/d2-expenditures-reconciliation/?format=csv&min_expenditure_rows=10')
        assert exported.status_code == 200
        assert exported.mimetype == 'text/csv'
        assert 'attachment; filename=d2_expenditures_reconciliation.csv' in exported.headers.get('Content-Disposition', '')
        assert b'd2_totals_record_id,committee_id_sbe,committee_name' in exported.data
        assert b'Committee A' in exported.data
        assert b'Committee B' not in exported.data

    def test_reports_sort_query_loads(self, client):
        """Test reports page supports sort params."""
        response = client.get('/reports/?sort=report_type&dir=asc')
        assert response.status_code == 200

    def test_search_page_loads(self, client):
        """Test that the search page loads."""
        response = client.get('/search')
        assert response.status_code == 200

    def test_search_local_candidates_uses_safe_numeric_and_text_casts(self, monkeypatch):
        conn = _CaptureSqlConn()
        monkeypatch.setattr(main_routes, "_table_exists", lambda _conn, _table: True)
        monkeypatch.setattr(main_routes, "_column_exists", lambda _conn, _table, _col: True)

        main_routes._search_local_candidates(conn, "smith", limit=10)

        rendered_sql = "\n".join(conn.sql_history)
        assert "COALESCE(SUM(CAST(NULLIF(TRIM(CAST(sum_total_receipts AS TEXT)), '') AS REAL)), 0)" in rendered_sql
        assert "COALESCE(CAST(candidate_id AS TEXT), '') LIKE ?" in rendered_sql

    def test_search_filed_docs_uses_safe_abs_diff_cast(self, monkeypatch):
        conn = _CaptureSqlConn()
        monkeypatch.setattr(main_routes, "_table_exists", lambda _conn, _table: _table == "bulk_d2_receipts_recon")

        main_routes._search_filed_docs(conn, "9001", limit=10)

        rendered_sql = "\n".join(conn.sql_history)
        assert "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0)) AS abs_diff" in rendered_sql
        assert "COALESCE(CAST(filed_doc_id AS TEXT), '') LIKE ?" in rendered_sql

    def test_d2_receipts_recon_filters_use_postgres_safe_casts(self):
        where_sql, _params = D2ReceiptsRecon._build_filter_sql(search="9001", min_abs_diff=10, min_receipt_rows=2)
        assert "COALESCE(CAST(committee_id_sbe AS TEXT), '') LIKE ?" in where_sql
        assert "COALESCE(CAST(filed_doc_id AS TEXT), '') LIKE ?" in where_sql
        assert "ABS(COALESCE(CAST(NULLIF(TRIM(CAST(receipts_minus_d2_total AS TEXT)), '') AS REAL), 0)) >= ?" in where_sql
        assert "COALESCE(CAST(NULLIF(TRIM(CAST(receipt_row_count AS TEXT)), '') AS INTEGER), 0) >= ?" in where_sql

    def test_search_extended_categories(self, app, client):
        """Test global search includes candidates, reports, filed docs, and donor keys."""
        conn = get_db(app.config['DATABASE_PATH'])

        conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
        conn.execute(
            """
            CREATE TABLE bulk_candidate_committee_finance_agg (
                candidate_id INTEGER,
                candidate_full_name TEXT,
                office_sought TEXT,
                committee_id_sbe INTEGER,
                sum_total_receipts REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bulk_candidate_committee_finance_agg (
                candidate_id, candidate_full_name, office_sought, committee_id_sbe, sum_total_receipts
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (301, "Local Search Candidate", "Mayor", 991, 25000.0),
        )

        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key, as_of_date, cycle, office, office_code, district, district_code,
                party, party_code, election_stage, candidate_name, normalized_candidate_name,
                write_in, already_listed_general, source_file, source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "search-seed-1",
                "2026-02-07",
                2026,
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                "Federal Search Candidate",
                "FEDERAL SEARCH CANDIDATE",
                0,
                0,
                "seed.csv",
                1,
            ),
        )

        conn.execute(
            """
            INSERT INTO fec_candidate_match (
                seed_candidate_key, candidate_name, office, office_code, district, district_code,
                party, party_code, election_stage, cycle, fec_candidate_id, fec_name, match_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "search-seed-1",
                "Federal Search Candidate",
                "U.S. House",
                "H",
                "IL-01",
                "01",
                "Democratic",
                "DEM",
                "Primary",
                2026,
                "H2IL01001",
                "SEARCH, FEDERAL",
                "matched",
            ),
        )

        conn.execute("DROP TABLE IF EXISTS bulk_d2_receipts_recon")
        conn.execute(
            """
            CREATE TABLE bulk_d2_receipts_recon (
                filed_doc_id INTEGER,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                receipts_minus_d2_total REAL,
                first_receipt_date TEXT,
                last_receipt_date TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bulk_d2_receipts_recon (
                filed_doc_id, committee_id_sbe, committee_name, receipts_minus_d2_total, first_receipt_date, last_receipt_date
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (9001, 991, "Doc Search Committee", 125.0, "2026-01-01", "2026-01-31"),
        )

        conn.execute("DELETE FROM analytics_donor_summary")
        conn.execute(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bulk_receipts",
                "bulk-search-key-1",
                None,
                "Bulk Search Donor",
                "1 Main St, Chicago, IL 60601",
                "Chicago",
                "IL",
                "Engineer",
                "Search Corp",
                5555.0,
                4,
                2,
            ),
        )
        conn.commit()
        conn.close()

        candidates = client.get("/search?q=Search%20Candidate&type=candidates")
        assert candidates.status_code == 200
        assert b"Local Search Candidate" in candidates.data
        assert b"Federal Search Candidate" in candidates.data
        assert b"Provenance" in candidates.data

        reports = client.get("/search?q=Test%20Committee&type=reports")
        assert reports.status_code == 200
        assert b"Reports" in reports.data
        assert b"Test Committee" in reports.data

        filed_docs = client.get("/search?q=9001&type=filed_docs")
        assert filed_docs.status_code == 200
        assert b"Filed Doc IDs" in filed_docs.data
        assert b"9001" in filed_docs.data

        donor_keys = client.get("/search?q=bulk-search-key&type=donor_keys")
        assert donor_keys.status_code == 200
        assert b"Donor Keys" in donor_keys.data
        assert b"bulk-search-key-1" in donor_keys.data

    def test_d2_reconciliation_handles_text_numeric_columns(self, app, client):
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DROP TABLE IF EXISTS bulk_d2_receipts_recon")
        conn.execute(
            """
            CREATE TABLE bulk_d2_receipts_recon (
                d2_totals_record_id INTEGER,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                filed_doc_id INTEGER,
                d2_total_receipts TEXT,
                d2_total_expenditures TEXT,
                ending_funds_available TEXT,
                is_archived INTEGER,
                receipt_row_count TEXT,
                receipts_amount_sum TEXT,
                first_receipt_date TEXT,
                last_receipt_date TEXT,
                receipts_minus_d2_total TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bulk_d2_receipts_recon (
                d2_totals_record_id,
                committee_id_sbe,
                committee_name,
                filed_doc_id,
                d2_total_receipts,
                d2_total_expenditures,
                ending_funds_available,
                is_archived,
                receipt_row_count,
                receipts_amount_sum,
                first_receipt_date,
                last_receipt_date,
                receipts_minus_d2_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 101, "Committee A", 9001, "1000.50", "500.25", "250.10", 0, "3", "900.00", "2026-01-01", "2026-01-31", "-100.50"),
        )
        conn.commit()
        conn.close()

        response = client.get('/d2-reconciliation/')
        assert response.status_code == 200
        assert b'Committee A' in response.data

    def test_compare_page_candidate_mode(self, app, client):
        """Test compare route renders side-by-side candidate overlap when bulk tables exist."""
        conn = get_db(app.config['DATABASE_PATH'])

        conn.execute("DROP TABLE IF EXISTS bulk_candidates_clean")
        conn.execute(
            """
            CREATE TABLE bulk_candidates_clean (
                candidate_id INTEGER,
                candidate_full_name TEXT,
                office_sought TEXT,
                district_type TEXT,
                district TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_candidates_clean (
                candidate_id, candidate_full_name, office_sought, district_type, district
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (201, "Candidate Left", "Mayor", "Municipal", "1"),
                (202, "Candidate Right", "Mayor", "Municipal", "2"),
            ],
        )

        conn.execute("DROP TABLE IF EXISTS bulk_candidate_committee_finance_agg")
        conn.execute(
            """
            CREATE TABLE bulk_candidate_committee_finance_agg (
                candidate_id INTEGER,
                candidate_full_name TEXT,
                office_sought TEXT,
                committee_id_sbe INTEGER,
                sum_total_receipts REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_candidate_committee_finance_agg (
                candidate_id, candidate_full_name, office_sought, committee_id_sbe, sum_total_receipts
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (201, "Candidate Left", "Mayor", 101, 12000.0),
                (202, "Candidate Right", "Mayor", 102, 15000.0),
            ],
        )

        conn.execute("DROP TABLE IF EXISTS bulk_committee_candidate_links")
        conn.execute(
            """
            CREATE TABLE bulk_committee_candidate_links (
                candidate_id INTEGER,
                committee_id_sbe INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_committee_candidate_links (candidate_id, committee_id_sbe)
            VALUES (?, ?)
            """,
            [(201, 101), (202, 102)],
        )

        conn.execute("DROP TABLE IF EXISTS bulk_receipts_clean")
        conn.execute(
            """
            CREATE TABLE bulk_receipts_clean (
                committee_id_sbe INTEGER,
                amount REAL,
                received_date TEXT,
                first_name TEXT,
                last_or_business_name TEXT,
                address_line_1 TEXT,
                address_line_2 TEXT,
                city TEXT,
                state TEXT,
                postal_code TEXT,
                d2_part_code TEXT,
                is_archived INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO bulk_receipts_clean (
                committee_id_sbe, amount, received_date, first_name, last_or_business_name,
                address_line_1, address_line_2, city, state, postal_code, d2_part_code, is_archived
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (101, 3000.0, "2026-01-05", "Alex", "Overlap", "1 Main", "", "Chicago", "IL", "60601", "1", 0),
                (102, 4500.0, "2026-01-12", "Alex", "Overlap", "1 Main", "", "Chicago", "IL", "60601", "1", 0),
                (101, 900.0, "2026-02-01", "Pat", "Solo", "2 Main", "", "Chicago", "IL", "60602", "1", 0),
                (102, 1200.0, "2026-02-07", "Jamie", "Solo", "3 Main", "", "Chicago", "IL", "60603", "1", 0),
            ],
        )
        conn.commit()
        conn.close()

        response = client.get("/compare?mode=candidate&left=201&right=202")
        assert response.status_code == 200
        assert b"Trend Comparison" in response.data
        assert b"Donor Overlap" in response.data
        assert b"Alex Overlap" in response.data

    def test_committees_sort_query_loads(self, client):
        """Test committees page supports sort params."""
        response = client.get('/committees/?sort=total_contributions&dir=desc')
        assert response.status_code == 200

    def test_donors_sort_query_loads(self, client):
        """Test donors page supports sort params."""
        response = client.get('/donors/?sort=contribution_count&dir=asc')
        assert response.status_code == 200

    def test_donors_sort_occupation_query_loads(self, client):
        """Test donors page supports occupation sorting."""
        response = client.get('/donors/?sort=occupation&dir=asc')
        assert response.status_code == 200

    def test_donors_page_uses_materialized_summary_when_available(self, app, client):
        """Test donors page loads bulk materialized donor summary rows and fields."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.execute(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bulk_receipts",
                "bulk:test:1",
                None,
                "Bulk Donor Example",
                "1 Main St, Chicago, IL 60601",
                "Chicago",
                "IL",
                "Engineer",
                "Example Corp",
                12345.0,
                4,
                2,
            ),
        )
        conn.execute(
            """
            INSERT INTO analytics_materialized_meta (
                source, donor_row_count, monthly_row_count, large_row_count, large_threshold,
                materialization_version, materialization_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                donor_row_count = excluded.donor_row_count,
                monthly_row_count = excluded.monthly_row_count,
                large_row_count = excluded.large_row_count,
                large_threshold = excluded.large_threshold,
                materialization_version = excluded.materialization_version,
                materialization_notes = excluded.materialization_notes,
                refreshed_at = CURRENT_TIMESTAMP
            """,
            ("bulk_receipts", 1, 0, 0, 5000.0, 2, "test-fixture"),
        )
        conn.commit()
        conn.close()

        response = client.get('/donors/?sort=state&dir=asc')
        assert response.status_code == 200
        assert b'Data source: bulk_receipts' in response.data
        assert b'Bulk Donor Example' in response.data
        assert b'Engineer' in response.data
        assert b'Example Corp' in response.data
        assert b'Chicago' in response.data
        assert b'IL' in response.data
        assert b'/donors/key/' in response.data
        assert b'source=bulk_receipts' in response.data

    def test_index_loads_with_materialized_donor_without_local_id(self, app, client):
        """Test dashboard top donors renders rows that do not have local donor ids."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.execute(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bulk_receipts",
                "bulk:index:1",
                None,
                "Bulk Index Donor",
                "1 Main St, Chicago, IL 60601",
                "Chicago",
                "IL",
                "Engineer",
                "Example Corp",
                9999.0,
                2,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO analytics_materialized_meta (
                source, donor_row_count, monthly_row_count, large_row_count, large_threshold,
                materialization_version, materialization_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                donor_row_count = excluded.donor_row_count,
                monthly_row_count = excluded.monthly_row_count,
                large_row_count = excluded.large_row_count,
                large_threshold = excluded.large_threshold,
                materialization_version = excluded.materialization_version,
                materialization_notes = excluded.materialization_notes,
                refreshed_at = CURRENT_TIMESTAMP
            """,
            ("bulk_receipts", 1, 0, 0, 5000.0, 2, "test-fixture"),
        )
        conn.commit()
        conn.close()

        response = client.get('/')
        assert response.status_code == 200
        assert b'Dashboard' in response.data
        assert b'Bulk Index Donor' in response.data
        assert b'donor_id=None' not in response.data
        assert b'/donors/key/' in response.data
        assert b'source=bulk_receipts' in response.data

    def test_donors_page_prefers_local_entity_rows_when_available(self, app, client):
        """Test donors page uses merged local donor entities when materialized entities exist."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM donor_entity_local_member")
        conn.execute("DELETE FROM donor_entity_local")
        conn.execute("DELETE FROM analytics_donor_committee_agg")
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.executemany(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "kenneth|griffin|131 s dearborn st||chicago|il|60603-5517", None,
                    "Kenneth Griffin", "131 S Dearborn St, Chicago, IL, 60603-5517",
                    "Chicago", "IL", "Founder and CEO", "Citadel LLC", 55016400.0, 8, 3,
                ),
                (
                    "bulk_receipts", "kenneth|griffin|131 s. dearborn st||chicago|il|60603", None,
                    "Kenneth Griffin", "131 S. Dearborn St, Chicago, IL, 60603",
                    "Chicago", "IL", "CEO", "The Citadel, LLC", 53817500.0, 10, 6,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO analytics_donor_committee_agg (
                source, donor_key, donor_name, donor_address, donor_city, donor_state,
                occupation, employer, committee_id, committee_name, total_amount, contribution_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "kenneth|griffin|131 s dearborn st||chicago|il|60603-5517",
                    "Kenneth Griffin", "131 S Dearborn St, Chicago, IL, 60603-5517",
                    "Chicago", "IL", "Founder and CEO", "Citadel LLC",
                    "100", "Committee A", 1000.0, 1,
                ),
                (
                    "bulk_receipts", "kenneth|griffin|131 s. dearborn st||chicago|il|60603",
                    "Kenneth Griffin", "131 S. Dearborn St, Chicago, IL, 60603",
                    "Chicago", "IL", "CEO", "The Citadel, LLC",
                    "200", "Committee B", 2000.0, 2,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO donor_entity_local (
                entity_id, source, canonical_name, display_name, member_count, total_amount,
                confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:kenneth:1",
                "bulk_receipts",
                "kenneth griffin",
                "Kenneth Griffin",
                2,
                108833900.0,
                0.95,
                0.95,
                "high",
                "review",
                "test:v1",
            ),
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state, donor_zip5,
                confidence_score, confidence_tier, merge_action, total_amount, contribution_count, committee_count,
                review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "kenneth|griffin|131 s dearborn st||chicago|il|60603-5517", "entity:kenneth:1",
                    "kenneth griffin", "Kenneth Griffin", "CHICAGO", "IL", "60603",
                    0.95, "high", "review", 55016400.0, 8, 3, "pending", "{}", "test:v1",
                ),
                (
                    "bulk_receipts", "kenneth|griffin|131 s. dearborn st||chicago|il|60603", "entity:kenneth:1",
                    "kenneth griffin", "Kenneth Griffin", "CHICAGO", "IL", "60603",
                    0.95, "high", "review", 53817500.0, 10, 6, "pending", "{}", "test:v1",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO analytics_materialized_meta (
                source, donor_row_count, monthly_row_count, large_row_count, large_threshold,
                materialization_version, materialization_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                donor_row_count = excluded.donor_row_count,
                monthly_row_count = excluded.monthly_row_count,
                large_row_count = excluded.large_row_count,
                large_threshold = excluded.large_threshold,
                materialization_version = excluded.materialization_version,
                materialization_notes = excluded.materialization_notes,
                refreshed_at = CURRENT_TIMESTAMP
            """,
            ("bulk_receipts", 2, 0, 0, 5000.0, 2, "test-fixture"),
        )
        conn.commit()
        conn.close()

        response = client.get('/donors/?sort=total_amount&dir=desc')
        assert response.status_code == 200
        assert b'Kenneth Griffin' in response.data
        assert response.data.count(b'Kenneth Griffin') >= 1
        assert b'/donors/entity/entity' in response.data

    def test_donors_page_entity_sorts_by_contribution_count_desc(self, app, client):
        """Entity-backed donor list should support sorting by contribution_count descending."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM donor_entity_local_member")
        conn.execute("DELETE FROM donor_entity_local")
        conn.execute("DELETE FROM analytics_donor_committee_agg")
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.executemany(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:sort:key_low", None,
                    "Low Contrib High Amount", "1 Main St, Chicago, IL, 60601",
                    "Chicago", "IL", "Business Owner", "Example One", 250000.0, 46, 9,
                ),
                (
                    "bulk_receipts", "entity:sort:key_high", None,
                    "High Contrib Lower Amount", "2 Main St, Chicago, IL, 60602",
                    "Chicago", "IL", "Executive", "Example Two", 10000.0, 220, 19,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local (
                entity_id, source, canonical_name, display_name, member_count, total_amount,
                confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "entity:sort:low",
                    "bulk_receipts",
                    "low contrib high amount",
                    "Low Contrib High Amount",
                    1,
                    250000.0,
                    0.99,
                    0.99,
                    "high",
                    "singleton",
                    "test:v1",
                ),
                (
                    "entity:sort:high",
                    "bulk_receipts",
                    "high contrib lower amount",
                    "High Contrib Lower Amount",
                    1,
                    10000.0,
                    0.99,
                    0.99,
                    "high",
                    "singleton",
                    "test:v1",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state, donor_zip5,
                confidence_score, confidence_tier, merge_action, total_amount, contribution_count, committee_count,
                review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:sort:key_low", "entity:sort:low",
                    "low contrib high amount", "Low Contrib High Amount", "CHICAGO", "IL", "60601",
                    0.99, "high", "singleton", 250000.0, 46, 9, "not_needed", "{}", "test:v1",
                ),
                (
                    "bulk_receipts", "entity:sort:key_high", "entity:sort:high",
                    "high contrib lower amount", "High Contrib Lower Amount", "CHICAGO", "IL", "60602",
                    0.99, "high", "singleton", 10000.0, 220, 19, "not_needed", "{}", "test:v1",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO analytics_materialized_meta (
                source, donor_row_count, monthly_row_count, large_row_count, large_threshold,
                materialization_version, materialization_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                donor_row_count = excluded.donor_row_count,
                monthly_row_count = excluded.monthly_row_count,
                large_row_count = excluded.large_row_count,
                large_threshold = excluded.large_threshold,
                materialization_version = excluded.materialization_version,
                materialization_notes = excluded.materialization_notes,
                refreshed_at = CURRENT_TIMESTAMP
            """,
            ("bulk_receipts", 2, 0, 0, 5000.0, 2, "test-fixture"),
        )
        conn.commit()
        conn.close()

        response = client.get('/donors/?sort=contribution_count&dir=desc')
        assert response.status_code == 200
        high_pos = response.data.find(b'High Contrib Lower Amount')
        low_pos = response.data.find(b'Low Contrib High Amount')
        assert high_pos != -1
        assert low_pos != -1
        assert high_pos < low_pos

    def test_bulk_donor_detail_by_entity_loads_and_aggregates(self, app, client):
        """Test donor detail route by entity aggregates committee totals across member donor keys."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM donor_entity_local_member")
        conn.execute("DELETE FROM donor_entity_local")
        conn.execute("DELETE FROM analytics_donor_committee_agg")
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.executemany(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:detail:key1", None, "Entity Detail Donor",
                    "10 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", 700.0, 3, 2,
                ),
                (
                    "bulk_receipts", "entity:detail:key2", None, "Entity Detail Donor",
                    "20 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", 300.0, 1, 1,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO analytics_donor_committee_agg (
                source, donor_key, donor_name, donor_address, donor_city, donor_state,
                occupation, employer, committee_id, committee_name, total_amount, contribution_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:detail:key1", "Entity Detail Donor",
                    "10 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", "100", "Committee A", 500.0, 2,
                ),
                (
                    "bulk_receipts", "entity:detail:key1", "Entity Detail Donor",
                    "10 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", "200", "Committee Z", 200.0, 1,
                ),
                (
                    "bulk_receipts", "entity:detail:key2", "Entity Detail Donor",
                    "20 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", "100", "Committee A", 300.0, 1,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO donor_entity_local (
                entity_id, source, canonical_name, display_name, member_count, total_amount,
                confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:detail:1",
                "bulk_receipts",
                "entity detail donor",
                "Entity Detail Donor",
                2,
                1000.0,
                0.9,
                0.95,
                "high",
                "review",
                "test:v1",
            ),
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state, donor_zip5,
                confidence_score, confidence_tier, merge_action, total_amount, contribution_count, committee_count,
                review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:detail:key1", "entity:detail:1", "entity detail donor",
                    "Entity Detail Donor", "SPRINGFIELD", "IL", "62701",
                    0.9, "high", "review", 700.0, 3, 2, "pending", "{}", "test:v1",
                ),
                (
                    "bulk_receipts", "entity:detail:key2", "entity:detail:1", "entity detail donor",
                    "Entity Detail Donor", "SPRINGFIELD", "IL", "62701",
                    0.82, "high", "review", 300.0, 1, 1, "pending", "{}", "test:v1",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO analytics_materialized_meta (
                source, donor_row_count, monthly_row_count, large_row_count, large_threshold,
                materialization_version, materialization_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                donor_row_count = excluded.donor_row_count,
                monthly_row_count = excluded.monthly_row_count,
                large_row_count = excluded.large_row_count,
                large_threshold = excluded.large_threshold,
                materialization_version = excluded.materialization_version,
                materialization_notes = excluded.materialization_notes,
                refreshed_at = CURRENT_TIMESTAMP
            """,
            ("bulk_receipts", 2, 0, 0, 5000.0, 2, "test-fixture"),
        )
        conn.commit()
        conn.close()

        entity_id_url = quote("entity:detail:1", safe="")
        response = client.get(f'/donors/entity/{entity_id_url}?source=bulk_receipts&sort=amount&dir=desc')
        assert response.status_code == 200
        assert b'Entity Detail Donor' in response.data
        assert b'Who They Donated To' in response.data
        assert b'Committees Supported:</strong> 2' in response.data
        assert b'Entity ID:</strong> entity:detail:1' in response.data
        assert response.data.find(b'Committee A') < response.data.find(b'Committee Z')

    def test_bulk_donor_detail_by_key_loads_and_sorts(self, app, client):
        """Test donor detail route by key shows committee breakdown for bulk donors."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute("DELETE FROM analytics_donor_summary")
        conn.execute("DELETE FROM analytics_donor_committee_agg")
        conn.execute(
            """
            INSERT INTO analytics_donor_summary (
                source, donor_key, local_donor_id, donor_name, donor_address,
                donor_city, donor_state, occupation, employer, total_amount,
                contribution_count, committee_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bulk_receipts",
                "bulk:detail:1",
                None,
                "Bulk Detail Donor",
                "10 Main St, Springfield, IL 62701",
                "Springfield",
                "IL",
                "Teacher",
                "School District",
                700.0,
                3,
                2,
            ),
        )
        conn.executemany(
            """
            INSERT INTO analytics_donor_committee_agg (
                source, donor_key, donor_name, donor_address, donor_city, donor_state,
                occupation, employer, committee_id, committee_name, total_amount, contribution_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "bulk:detail:1", "Bulk Detail Donor",
                    "10 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", "200", "Committee Z", 200.0, 1
                ),
                (
                    "bulk_receipts", "bulk:detail:1", "Bulk Detail Donor",
                    "10 Main St, Springfield, IL 62701", "Springfield", "IL",
                    "Teacher", "School District", "100", "Committee A", 500.0, 2
                ),
            ],
        )
        conn.commit()
        conn.close()

        donor_key_url = quote("bulk:detail:1", safe="")
        response = client.get(f'/donors/key/{donor_key_url}?source=bulk_receipts&sort=amount&dir=desc')
        assert response.status_code == 200
        assert b'Bulk Detail Donor' in response.data
        assert b'Who They Donated To' in response.data
        assert b'Committees Supported:</strong> 2' in response.data
        assert response.data.find(b'Committee A') < response.data.find(b'Committee Z')

    def test_report_detail_loads_with_sort(self, client):
        """Test report detail supports contribution sort params."""
        response = client.get('/reports/1?sort=donor&dir=asc')
        assert response.status_code == 200

    def test_manual_entry_page_loads(self, client):
        """Test that the manual entry page requires login."""
        response = client.get('/manual-entry/')
        assert response.status_code == 302
        assert '/auth/login' in response.headers.get('Location', '')

    def test_manual_entry_login_and_access(self, client):
        """Test logging in allows manual entry access."""
        login = _login_manual_user(client, next_url="/manual-entry/")
        assert login.status_code == 302
        assert '/manual-entry/' in login.headers.get('Location', '')

        response = client.get('/manual-entry/')
        assert response.status_code == 200
        assert b'Manual Entry Queue' in response.data

    def test_login_rejects_missing_csrf_token(self, client):
        """Login POST should fail without a CSRF token."""
        response = client.post(
            '/auth/login',
            data={
                'username': 'manual_admin',
                'password': 'secret123',
                'next': '/manual-entry/',
            },
            follow_redirects=False,
        )
        assert response.status_code == 400

    def test_manual_entry_delete_is_scoped_to_report(self, app, client):
        """Delete action must only remove contributions for the current queue report."""
        login = _login_manual_user(client, next_url="/manual-entry/")
        assert login.status_code == 302

        conn = get_db(app.config['DATABASE_PATH'])

        committee = Committee.get_or_create(conn, "Scoped Delete Committee")
        report = Report(
            committee_id=committee.id,
            report_type="A-1 ($1000+ Year Round)",
            reporting_period="Q2 2026",
            filed_date="03/15/2026",
            pages=2,
            detail_url="https://example.com/scoped-delete-report",
            scrape_status="pending",
        ).save(conn)
        conn.execute(
            """
            INSERT INTO manual_entry_queue (report_id, status)
            VALUES (?, 'pending')
            """,
            (report.id,),
        )
        queue_id = conn.execute(
            "SELECT id FROM manual_entry_queue WHERE report_id = ?",
            (report.id,),
        ).fetchone()["id"]

        donor = Donor.get_or_create(conn, "Scoped Donor", "1 Scoped St", "scoped donor", "1 scoped st")
        target_contribution = Contribution(
            report_id=report.id,
            donor_id=donor.id,
            amount=55.0,
            raw_contributed_by="Scoped Donor",
            raw_address="1 Scoped St",
        ).save(conn)
        other_report_contribution = Contribution(
            report_id=1,
            donor_id=donor.id,
            amount=65.0,
            raw_contributed_by="Scoped Donor",
            raw_address="1 Scoped St",
        ).save(conn)
        conn.close()

        page = client.get(f"/manual-entry/{queue_id}")
        assert page.status_code == 200
        csrf_token = _extract_csrf_token(page.data)

        invalid_delete = client.post(
            f"/manual-entry/{queue_id}",
            data={
                "action": "delete_contribution",
                "contribution_id": str(other_report_contribution.id),
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert invalid_delete.status_code == 302

        conn = get_db(app.config['DATABASE_PATH'])
        still_exists = conn.execute(
            "SELECT id FROM contributions WHERE id = ?",
            (other_report_contribution.id,),
        ).fetchone()
        assert still_exists is not None

        valid_delete = client.post(
            f"/manual-entry/{queue_id}",
            data={
                "action": "delete_contribution",
                "contribution_id": str(target_contribution.id),
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert valid_delete.status_code == 302

        deleted = conn.execute(
            "SELECT id FROM contributions WHERE id = ?",
            (target_contribution.id,),
        ).fetchone()
        assert deleted is None
        conn.close()

    def test_admin_donor_merges_requires_login(self, client):
        """Admin donor merge review routes should require login."""
        response = client.get('/admin/donor-merges')
        assert response.status_code == 302
        assert '/auth/login' in response.headers.get('Location', '')

    def test_admin_federal_receipt_audit_requires_login(self, client):
        """Federal receipt audit route should require login."""
        response = client.get('/admin/federal-receipt-audit')
        assert response.status_code == 302
        assert '/auth/login' in response.headers.get('Location', '')

    def test_admin_federal_disbursement_audit_requires_login(self, client):
        """Federal disbursement audit route should require login."""
        response = client.get('/admin/federal-disbursement-audit')
        assert response.status_code == 302
        assert '/auth/login' in response.headers.get('Location', '')

    def test_admin_donor_merge_queue_loads(self, app, client):
        """Admin donor merge queue should render review entities."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            """
            INSERT INTO donor_entity_local (
                entity_id, source, canonical_name, display_name, member_count, total_amount,
                confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:admin:1",
                "bulk_receipts",
                "kenneth griffin",
                "Kenneth Griffin",
                2,
                108833900.0,
                0.95,
                0.95,
                "high",
                "review",
                "test:v1",
            ),
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state, donor_zip5,
                confidence_score, confidence_tier, merge_action, total_amount, contribution_count, committee_count,
                review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:admin:key1", "entity:admin:1", "kenneth griffin",
                    "Kenneth Griffin", "CHICAGO", "IL", "60603",
                    0.95, "high", "review", 55016400.0, 8, 3, "pending", "{}", "test:v1",
                ),
                (
                    "bulk_receipts", "entity:admin:key2", "entity:admin:1", "kenneth griffin",
                    "Kenneth Griffin", "CHICAGO", "IL", "60611",
                    0.92, "high", "review", 53817500.0, 10, 6, "approved", "{}", "test:v1",
                ),
            ],
        )
        conn.commit()
        conn.close()

        login = _login_manual_user(client, next_url="/admin/donor-merges")
        assert login.status_code == 302

        response = client.get('/admin/donor-merges?source=bulk_receipts&status=pending')
        assert response.status_code == 200
        assert b'Donor Merge Review' in response.data
        assert b'Kenneth Griffin' in response.data
        assert b'entity:admin:1' in response.data
        assert b'Pending (1)' in response.data

    def test_admin_donor_merge_decision_updates_status(self, app, client):
        """Admin donor merge decisions should persist to local member review_status rows."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            """
            INSERT INTO donor_entity_local (
                entity_id, source, canonical_name, display_name, member_count, total_amount,
                confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:admin:decision",
                "bulk_receipts",
                "kenneth griffin",
                "Kenneth Griffin",
                2,
                108833900.0,
                0.95,
                0.95,
                "high",
                "review",
                "test:v1",
            ),
        )
        conn.executemany(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state, donor_zip5,
                confidence_score, confidence_tier, merge_action, total_amount, contribution_count, committee_count,
                review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "bulk_receipts", "entity:admin:decision:key1", "entity:admin:decision", "kenneth griffin",
                    "Kenneth Griffin", "CHICAGO", "IL", "60603",
                    0.95, "high", "review", 55016400.0, 8, 3, "pending", "{}", "test:v1",
                ),
                (
                    "bulk_receipts", "entity:admin:decision:key2", "entity:admin:decision", "kenneth griffin",
                    "Kenneth Griffin", "CHICAGO", "IL", "60611",
                    0.92, "high", "review", 53817500.0, 10, 6, "pending", "{}", "test:v1",
                ),
            ],
        )
        conn.commit()
        conn.close()

        login = _login_manual_user(client, next_url="/admin/donor-merges")
        assert login.status_code == 302

        entity_id_url = quote("entity:admin:decision", safe="")
        detail_page = client.get(f"/admin/donor-merges/{entity_id_url}?source=bulk_receipts")
        assert detail_page.status_code == 200
        csrf_token = _extract_csrf_token(detail_page.data)

        first_update = client.post(
            f'/admin/donor-merges/{entity_id_url}/decision',
            data={
                'source': 'bulk_receipts',
                'decision': 'approved',
                'donor_key': 'entity:admin:decision:key1',
                'csrf_token': csrf_token,
            },
            follow_redirects=False,
        )
        assert first_update.status_code == 302

        conn = get_db(app.config['DATABASE_PATH'])
        key1_status = conn.execute(
            """
            SELECT review_status
            FROM donor_entity_local_member
            WHERE source = 'bulk_receipts' AND donor_key = 'entity:admin:decision:key1'
            """
        ).fetchone()["review_status"]
        key2_status = conn.execute(
            """
            SELECT review_status
            FROM donor_entity_local_member
            WHERE source = 'bulk_receipts' AND donor_key = 'entity:admin:decision:key2'
            """
        ).fetchone()["review_status"]
        assert key1_status == "approved"
        assert key2_status == "pending"
        conn.close()

        second_update = client.post(
            f'/admin/donor-merges/{entity_id_url}/decision',
            data={
                'source': 'bulk_receipts',
                'decision': 'rejected',
                'csrf_token': csrf_token,
            },
            follow_redirects=False,
        )
        assert second_update.status_code == 302

        conn = get_db(app.config['DATABASE_PATH'])
        statuses = conn.execute(
            """
            SELECT donor_key, review_status
            FROM donor_entity_local_member
            WHERE source = 'bulk_receipts'
              AND entity_id = 'entity:admin:decision'
            ORDER BY donor_key
            """
        ).fetchall()
        conn.close()
        assert [row["review_status"] for row in statuses] == ["rejected", "rejected"]

    def test_admin_federal_receipt_audit_loads(self, app, client):
        """Federal receipt audit page should render internal mismatch flags."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key, as_of_date, cycle, office, office_code, district, district_code,
                party, party_code, election_stage, candidate_name, normalized_candidate_name,
                write_in, already_listed_general, source_file, source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-admin-audit',
                '2026-02-07',
                2026,
                'U.S. House',
                'H',
                'IL-03',
                '03',
                'Democratic',
                'DEM',
                'Primary',
                'Candidate Admin Audit',
                'CANDIDATE ADMIN AUDIT',
                0,
                0,
                'seed.csv',
                2,
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_match (
                seed_candidate_key, candidate_name, office, office_code, district, district_code,
                party, party_code, election_stage, cycle,
                fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
                match_status, match_score, match_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-admin-audit',
                'Candidate Admin Audit',
                'U.S. House',
                'H',
                'IL-03',
                '03',
                'Democratic',
                'DEM',
                'Primary',
                2026,
                'H2IL03333',
                'AUDIT, CANDIDATE',
                'H',
                'IL',
                '03',
                'DEM',
                'matched',
                93.0,
                'test',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_cycle_totals (
                candidate_id, cycle, receipts, contributions, individual_contributions,
                coverage_start_date, coverage_end_date, transaction_coverage_date,
                last_report_year, last_report_type_full, last_cash_on_hand_end_period, source_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'H2IL03333',
                2026,
                5000.0,
                5000.0,
                4500.0,
                '2026-01-01',
                '2026-03-31',
                '2026-03-31',
                2026,
                'Q1',
                10000.0,
                '{"source":"test"}',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_schedule_a_contributions (
                sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
                contributor_name, contributor_city, contributor_state, contributor_zip,
                contributor_employer, contributor_occupation, contributor_id, is_individual,
                line_number, receipt_type, receipt_type_desc, memo_text,
                contribution_receipt_amount, contribution_receipt_date, two_year_transaction_period,
                donor_key, donor_entity_key, donor_entity_method, load_date, image_number, api_source_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'sub-admin-audit',
                2026,
                'H2IL03333',
                'AUDIT, CANDIDATE',
                'C00333333',
                'AUDIT COMMITTEE',
                'Test Donor',
                'Chicago',
                'IL',
                '60603',
                'ACME',
                'Engineer',
                None,
                1,
                '11AI',
                'IND',
                'Individual contribution',
                '',
                2500.0,
                '2026-01-10',
                2026,
                'donor-audit-key',
                'donor_audit_il_60603',
                'name_state_zip',
                '2026-01-11',
                'img-admin-audit',
                'src-admin-audit',
            ),
        )
        conn.commit()
        conn.close()

        login = _login_manual_user(client, next_url="/admin/federal-receipt-audit")
        assert login.status_code == 302

        response = client.get('/admin/federal-receipt-audit?cycle=2026&status=flagged&min_abs_diff=1')
        assert response.status_code == 200
        assert b'Federal Receipt Audit' in response.data
        assert b'AUDIT, CANDIDATE' in response.data
        assert b'missing_schedule_rows' in response.data

    def test_admin_federal_disbursement_audit_loads(self, app, client):
        """Federal disbursement audit page should render internal mismatch flags."""
        conn = get_db(app.config['DATABASE_PATH'])
        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key, as_of_date, cycle, office, office_code, district, district_code,
                party, party_code, election_stage, candidate_name, normalized_candidate_name,
                write_in, already_listed_general, source_file, source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-admin-disb-audit',
                '2026-02-07',
                2026,
                'U.S. House',
                'H',
                'IL-10',
                '10',
                'Democratic',
                'DEM',
                'Primary',
                'Candidate Admin Disbursement',
                'CANDIDATE ADMIN DISBURSEMENT',
                0,
                0,
                'seed.csv',
                2,
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_match (
                seed_candidate_key, candidate_name, office, office_code, district, district_code,
                party, party_code, election_stage, cycle,
                fec_candidate_id, fec_name, fec_office, fec_state, fec_district, fec_party,
                match_status, match_score, match_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'seed-admin-disb-audit',
                'Candidate Admin Disbursement',
                'U.S. House',
                'H',
                'IL-10',
                '10',
                'Democratic',
                'DEM',
                'Primary',
                2026,
                'H2IL10000',
                'DISB AUDIT, CANDIDATE',
                'H',
                'IL',
                '10',
                'DEM',
                'matched',
                93.0,
                'test',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_candidate_cycle_totals (
                candidate_id, cycle, receipts, disbursements, contributions, individual_contributions,
                coverage_start_date, coverage_end_date, transaction_coverage_date,
                last_report_year, last_report_type_full, last_cash_on_hand_end_period, source_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'H2IL10000',
                2026,
                5000.0,
                4200.0,
                5000.0,
                4500.0,
                '2026-01-01',
                '2026-03-31',
                '2026-03-31',
                2026,
                'Q1',
                9000.0,
                '{"source":"test"}',
            ),
        )
        conn.execute(
            """
            INSERT INTO fec_schedule_b_disbursements (
                sub_id, cycle, candidate_id, candidate_name, committee_id, committee_name,
                recipient_name, disbursement_amount, disbursement_date, api_source_identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'sub-admin-disb-audit',
                2026,
                'H2IL10000',
                'DISB AUDIT, CANDIDATE',
                'C01000000',
                'DISB AUDIT COMMITTEE',
                'Vendor One',
                1000.0,
                '2026-01-09',
                'src-admin-disb-audit',
            ),
        )
        conn.commit()
        conn.close()

        login = _login_manual_user(client, next_url="/admin/federal-disbursement-audit")
        assert login.status_code == 302

        response = client.get('/admin/federal-disbursement-audit?cycle=2026&status=flagged&min_abs_diff=1')
        assert response.status_code == 200
        assert b'Federal Disbursement Audit' in response.data
        assert b'DISB AUDIT, CANDIDATE' in response.data
        assert b'missing_schedule_b_rows' in response.data

    def test_api_stats(self, client):
        """Test that the API stats endpoint works."""
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert 'committees' in data
        assert 'reports' in data
        assert 'donors' in data

    def test_api_key_required_when_configured(self, app):
        """API endpoints should require a valid key when key auth is enabled."""
        secured_app = create_app({
            'TESTING': True,
            'DATABASE_PATH': app.config['DATABASE_PATH'],
            'API_KEYS': ['test-api-key'],
            'API_REQUIRE_KEY': True,
        })
        secured_client = secured_app.test_client()

        missing_key = secured_client.get('/api/stats')
        assert missing_key.status_code == 401

        wrong_key = secured_client.get('/api/stats', headers={'X-API-Key': 'wrong'})
        assert wrong_key.status_code == 401

        ok = secured_client.get('/api/stats', headers={'X-API-Key': 'test-api-key'})
        assert ok.status_code == 200

    def test_api_rate_limit_enforced(self, app):
        """API limiter should return 429 after the configured request budget."""
        limited_app = create_app({
            'TESTING': True,
            'DATABASE_PATH': app.config['DATABASE_PATH'],
            'API_KEYS': ['rate-limit-key'],
            'API_REQUIRE_KEY': True,
            'API_RATE_LIMIT_PER_MINUTE': 1,
        })
        limited_client = limited_app.test_client()
        headers = {'X-API-Key': 'rate-limit-key'}

        first = limited_client.get('/api/stats', headers=headers)
        assert first.status_code == 200

        second = limited_client.get('/api/stats', headers=headers)
        assert second.status_code == 429
        assert second.get_json()['error'] == 'rate_limit_exceeded'

    def test_custom_404_template(self, client):
        """Unknown routes should render the custom 404 page."""
        response = client.get('/this-route-does-not-exist')
        assert response.status_code == 404
        assert b'Page Not Found' in response.data
        assert b'Try one of these pages' in response.data

    def test_production_secret_key_policy(self, app):
        """Production-like configs should reject insecure default secret keys."""
        with pytest.raises(RuntimeError):
            create_app({
                'DATABASE_PATH': app.config['DATABASE_PATH'],
                'APP_ENV': 'production',
                'SECRET_KEY': 'dev-secret-key-change-in-production',
                'TESTING': False,
            })

    def test_multiple_requests_thread_safe(self, client):
        """Test that multiple requests don't cause threading issues."""
        # Make multiple requests in sequence
        for _ in range(10):
            response = client.get('/')
            assert response.status_code == 200

    def test_static_css_loads(self, client):
        """Test that the CSS file loads."""
        response = client.get('/static/css/style.css')
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
