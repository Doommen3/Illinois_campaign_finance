"""Step 1.3 — entity profile pages: default to ALL TIME, show a current-cycle
subtotal beside the headline, carry exactly one time-scope bar, and tag every
aggregate heading with its scope (page scope or a fixed "All time").

Starts with the ISBE committee profile (1.3a); later lanes add donors, federal
candidate/donor, 527, lobbying and OpenBook profiles to this file.
"""
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest

from database.connection import get_db, init_db
from tests.test_isbe_rewire import (
    _create_candidate_finance_agg_view,
    _create_compat_views,
    _seed_isbe_tables,
)
from webapp.app import create_app


@pytest.fixture
def profile_app(tmp_path: Path):
    db_path = str(tmp_path / "profile_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    # One receipt for committee 100 outside the current (2026) cycle so that
    # all-time and this-cycle totals differ: 32,500 this cycle, 33,500 all time.
    conn.execute(
        """
        INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name,
            received_date, amount, d2_part, archived)
        VALUES (10099, 100, 5001, 'Oldcycle', 'Donor', '2023-06-01', 1000.0, '1A', FALSE)
        """
    )
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestCommitteeProfileScope:
    def test_defaults_to_all_time_with_cycle_subtotal(self, profile_app):
        html = profile_app.test_client().get("/committees/sbe/100").get_data(as_text=True)
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$33,500.00" in html                      # all-time total receipts
        assert "This cycle" in html and "$32,500.00" in html   # current-cycle subtotal beside it
        assert "period-badge" not in html               # old badge gone
        # Section tags: scoped sections carry the page scope, fixed ones say so.
        assert '<h2>Receipts Summary <span class="scope-tag">All time</span></h2>' in html
        assert '<h2>Top Donors <span class="scope-tag">All time</span></h2>' in html
        assert '<h2>Filing History <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html
        assert '<h2>Committee Information <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html
        assert '<h2>Current Officers <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html

    def test_cycle_preset_narrows_and_keeps_subtotal(self, profile_app):
        html = profile_app.test_client().get("/committees/sbe/100?period=cycle:2026").get_data(as_text=True)
        assert 'data-scope-key="cycle:2026"' in html
        assert "$33,500.00" not in html
        assert "$32,500.00" in html
        assert '<h2>Receipts Summary <span class="scope-tag">2026 Cycle</span></h2>' in html
        # Reset link offered because this is not the page default.
        assert "time-scope-reset" in html

    def test_legacy_period_key_still_works(self, profile_app):
        html = profile_app.test_client().get("/committees/sbe/100?period=2024cycle").get_data(as_text=True)
        assert 'data-scope-key="cycle:2024"' in html
        assert "$1,000.00" in html

    def test_canonical_committee_route_uses_same_profile(self, profile_app):
        html = profile_app.test_client().get("/committees/100?period=cycle:2026").get_data(as_text=True)
        assert html.count('class="time-scope-bar') == 1
        assert "$32,500.00" in html


def _hrefs_containing(html: str, needle: str) -> list[str]:
    return [h for h in re.findall(r'href="([^"]+)"', html) if needle in h]


# ---------------------------------------------------------------------------
# 1.3b — donor profiles: bulk donor key page and legacy manual-entry donor page
# ---------------------------------------------------------------------------

@pytest.fixture
def donor_app(tmp_path: Path):
    from database.analytics import refresh_analytics_materialized

    db_path = str(tmp_path / "donor_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    # Alice gave 5,000 + 10,000 inside the current (2026) cycle. Add 700 in 2023
    # so all-time (15,700) and this-cycle (15,000) differ.
    conn.execute(
        """
        INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name,
            received_date, amount, d2_part, occupation, employer, city, state, zipcode, address1, archived)
        VALUES (10098, 200, 5002, 'Donor', 'Alice', '2023-03-01', 700.0, '1A',
            'Lawyer', 'BigLaw LLC', 'Chicago', 'IL', '60601', '100 Main St', FALSE)
        """
    )
    # Legacy manual-entry donor: 300 in 2023, 1,200 in 2025 -> 1,500 all time.
    conn.execute("INSERT INTO committees (id, name) VALUES (1, 'Legacy Committee')")
    conn.execute("INSERT INTO reports (id, committee_id, filed_date) VALUES (1, 1, '2025-07-01')")
    conn.execute("INSERT INTO donors (id, name, normalized_name) VALUES (1, 'Legacy Donor', 'legacy donor')")
    conn.execute(
        """
        INSERT INTO contributions (report_id, donor_id, amount, transaction_date)
        VALUES (1, 1, 300.0, '2023-04-01'), (1, 1, 1200.0, '2025-06-15')
        """
    )
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    refresh_analytics_materialized(conn)   # populates analytics_donor_summary (the all-time path)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _alice_key(app) -> str:
    conn = get_db(app.config["DATABASE_PATH"])
    row = conn.execute(
        """
        SELECT donor_key FROM analytics_donor_summary
        WHERE source = 'bulk_receipts' AND donor_name LIKE '%Alice%'
        ORDER BY total_amount DESC LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert row, "fixture: Alice missing from analytics_donor_summary"
    return row["donor_key"]


class TestDonorProfileScope:
    def test_bulk_key_defaults_to_all_time_with_cycle_subtotal(self, donor_app):
        url = f"/donors/key/{quote(_alice_key(donor_app), safe='')}?source=bulk_receipts"
        html = donor_app.test_client().get(url).get_data(as_text=True)
        assert "Alice" in html                                  # page renders today
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$15,700.00" in html                             # all-time total
        assert "This cycle" in html and "$15,000.00" in html    # current-cycle subtotal
        assert '<h2>Who They Donated To <span class="scope-tag">All time</span></h2>' in html

    def test_bulk_key_cycle_preset_narrows_and_links_keep_scope(self, donor_app):
        url = f"/donors/key/{quote(_alice_key(donor_app), safe='')}?source=bulk_receipts&period=cycle:2026"
        html = donor_app.test_client().get(url).get_data(as_text=True)
        assert 'data-scope-key="cycle:2026"' in html
        assert "$15,700.00" not in html
        assert "$15,000.00" in html
        assert '<h2>Who They Donated To <span class="scope-tag">2026 Cycle</span></h2>' in html
        assert "time-scope-reset" in html
        sort_links = _hrefs_containing(html, "sort=contribution_count")
        assert sort_links and all("period=cycle" in h for h in sort_links)

    def test_bulk_key_empty_scope_renders_zero_not_404(self, donor_app):
        """A preset with no receipts for this donor must still render (with Reset), not 404."""
        url = f"/donors/key/{quote(_alice_key(donor_app), safe='')}?source=bulk_receipts&period=cycle:2022"
        resp = donor_app.test_client().get(url)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Alice" in html
        assert 'data-scope-key="cycle:2022"' in html
        assert "Total Contributed:</strong> $0.00" in html
        assert "time-scope-reset" in html

    def test_legacy_donor_page_defaults_to_all_time_with_cycle_subtotal(self, donor_app):
        html = donor_app.test_client().get("/donors/1").get_data(as_text=True)
        assert "Legacy Donor" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$1,500.00" in html
        assert "This cycle" in html and "$1,200.00" in html
        assert '<h2>Contributions <span class="scope-tag">All time</span></h2>' in html


# ---------------------------------------------------------------------------
# 1.3c — federal candidate detail and federal donor detail
# ---------------------------------------------------------------------------

def _seed_federal(conn) -> None:
    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ('seed-key-1', '2026-02-07', 2026, 'U.S. House', 'H', 'IL-01', '01', 'Democratic', 'DEM',
         'Primary', 'Jonathan Jackson', 'JONATHAN JACKSON', 0, 0, 'test_seed.csv', 2),
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
        ('seed-key-1', 'Jonathan Jackson', 'U.S. House', 'H', 'IL-01', '01', 'Democratic', 'DEM',
         'Primary', 2026, 'H2IL01349', 'JACKSON, JONATHAN', 'H', 'IL', '01', 'DEM', 'matched', 95.0, 'test'),
    )
    # Real data carries one match row per cycle a candidate ran; add the 2024 one.
    conn.execute(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ('seed-key-1-2024', '2024-02-07', 2024, 'U.S. House', 'H', 'IL-01', '01', 'Democratic', 'DEM',
         'Primary', 'Jonathan Jackson', 'JONATHAN JACKSON', 0, 0, 'test_seed.csv', 2),
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
        ('seed-key-1-2024', 'Jonathan Jackson', 'U.S. House', 'H', 'IL-01', '01', 'Democratic', 'DEM',
         'Primary', 2024, 'H2IL01349', 'JACKSON, JONATHAN', 'H', 'IL', '01', 'DEM', 'matched', 95.0, 'test'),
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_committees (
            candidate_id, committee_id, cycle, committee_name, committee_type,
            committee_designation, committee_designation_full, filing_frequency,
            committee_party, committee_city, committee_state, committee_zip, is_principal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ('H2IL01349', 'C00011111', 2026, 'JONATHAN JACKSON FOR CONGRESS', 'H', 'P',
         'Principal campaign committee', 'Q', 'DEM', 'WASHINGTON', 'DC', '20005', 1),
    )
    # Same donor in two cycles: 250 in the 2026 cycle, 400 in the 2024 cycle.
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
            ('sub-1', 2026, 'H2IL01349', 'JACKSON, JONATHAN', 'C00011111', 'JONATHAN JACKSON FOR CONGRESS',
             'Jane Donor', 'Chicago', 'IL', '60601', 'ACME', 'Engineer', None, 1, '11AI', 'IND',
             'Individual contribution', '', 250.0, '2026-01-15', 2026, 'donor-key-1',
             '2026-01-20T01:02:03', '202601209000000001', 'source-1'),
            ('sub-2', 2024, 'H2IL01349', 'JACKSON, JONATHAN', 'C00011111', 'JONATHAN JACKSON FOR CONGRESS',
             'Jane Donor', 'Chicago', 'IL', '60601', 'ACME', 'Engineer', None, 1, '11AI', 'IND',
             'Individual contribution', '', 400.0, '2023-06-01', 2024, 'donor-key-1',
             '2023-06-05T01:02:03', '202306059000000001', 'source-2'),
        ],
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_cycle_totals (
            candidate_id, cycle, receipts, disbursements, contributions, individual_contributions,
            coverage_start_date, coverage_end_date, transaction_coverage_date, last_report_year,
            last_report_type_full, last_cash_on_hand_end_period, source_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ('H2IL01349', 2026, 1200.0, 900.0, 1200.0, 1100.0, '2026-01-01', '2026-03-31',
         '2026-03-31', 2026, 'Q1', 5000.0, '{"source":"test"}'),
    )


@pytest.fixture
def federal_app(tmp_path: Path, monkeypatch):
    from database.federal_fec import _ensure_missing_donor_identities

    # Freeze the scope clock (2026-10-26) so period=all / current-cycle expectations
    # in this file, test_scope_widgets.py and test_scope_windows_part_b.py do not
    # age out on 2027-01-01 (same idiom as tests/test_candidate_profile.py).
    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 26)

    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)
    monkeypatch.setattr("database.analytics.current_election_cycle", lambda today=None: 2026)

    db_path = str(tmp_path / "federal_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_federal(conn)
    conn.commit()
    _ensure_missing_donor_identities(conn, cycle=None)
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _jane_entity_key(app) -> str:
    conn = get_db(app.config["DATABASE_PATH"])
    rows = conn.execute(
        "SELECT DISTINCT donor_entity_key FROM fec_schedule_a_contributions WHERE donor_key = 'donor-key-1'"
    ).fetchall()
    conn.close()
    keys = {r["donor_entity_key"] for r in rows if r["donor_entity_key"]}
    assert len(keys) == 1, f"fixture: expected one donor identity for Jane, got {keys}"
    return keys.pop()


class TestFederalProfileScope:
    # J8: the federal candidate page keeps a current-cycle default because its
    # headline totals come from FEC's per-cycle pre-aggregated tables.
    def test_candidate_defaults_to_current_cycle(self, federal_app):
        html = federal_app.test_client().get("/federal-finance/H2IL01349").get_data(as_text=True)
        assert "Jane Donor" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="cycle:2026"' in html
        assert '<h2>Top Donors <span class="scope-tag">2026 Cycle</span></h2>' in html
        assert "$250.00" in html and "$650.00" not in html

    def test_candidate_all_time_spans_cycles_and_tags_reported_totals(self, federal_app):
        html = federal_app.test_client().get("/federal-finance/H2IL01349?period=all").get_data(as_text=True)
        assert 'data-scope-key="all"' in html
        assert "$650.00" in html                                # Jane across both cycles
        assert '<h2>Top Donors <span class="scope-tag">All time</span></h2>' in html
        # Pre-aggregated FEC totals stay per cycle and must say which one.
        assert '<span class="scope-tag scope-tag-fixed">2026 Cycle</span>' in html
        assert "time-scope-reset" in html

    def test_candidate_explicit_cycle_param_still_scopes(self, federal_app):
        html = federal_app.test_client().get("/federal-finance/H2IL01349?cycle=2024").get_data(as_text=True)
        assert 'data-scope-key="cycle:2024"' in html
        assert "$400.00" in html and "$250.00" not in html

    def test_donor_defaults_to_all_time_with_cycle_subtotal(self, federal_app):
        url = f"/federal-finance/donors/{quote(_jane_entity_key(federal_app), safe='')}"
        html = federal_app.test_client().get(url).get_data(as_text=True)
        assert "Federal Donor Detail" in html and "Jane Donor" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$650.00" in html
        assert "This cycle" in html and "$250.00" in html
        assert '<h2>Donations by Candidate <span class="scope-tag">All time</span></h2>' in html

    def test_donor_cycle_preset_narrows(self, federal_app):
        url = f"/federal-finance/donors/{quote(_jane_entity_key(federal_app), safe='')}?period=cycle:2026"
        html = federal_app.test_client().get(url).get_data(as_text=True)
        assert 'data-scope-key="cycle:2026"' in html
        assert "$650.00" not in html
        assert "$250.00" in html
        assert '<h2>Donations by Candidate <span class="scope-tag">2026 Cycle</span></h2>' in html


# ---------------------------------------------------------------------------
# 1.3d — 527 organisation, lobbying entity/client, OpenBook vendor
# ---------------------------------------------------------------------------

@pytest.fixture
def irs527_app(tmp_path: Path):
    db_path = str(tmp_path / "irs527_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip)
        VALUES ('123456789', 100, 0, 'Test 527 Org', 'Chicago', 'IL', '60601')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state)
        VALUES (100, '123456789', 'Test 527 Org', 'Jane Director', 'President', 'Chicago', 'IL')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, purpose, date)
        VALUES (100, '123456789', 'Test 527 Org', 'Recipient Inc', 'Springfield', 'IL', 5000, 'Campaign support', '2026-02-10')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_reports (form_id, ein, period_start, period_end, org_name, total_contributions, total_expenditures)
        VALUES (100, '123456789', '2026-01-01', '2026-03-31', 'Test 527 Org', 12345, 6789)
        """
    )
    # 4,321 inside the current cycle + 1,000 in 2023 -> 5,321 all time.
    conn.executemany(
        """
        INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (100, '123456789', 'Test 527 Org', 'Acme Donor', 'Chicago', 'IL', 4321, '2026-02-01'),
            (100, '123456789', 'Test 527 Org', 'Old Donor', 'Chicago', 'IL', 1000, '2023-05-01'),
        ],
    )
    conn.execute(
        "INSERT INTO irs527_contribution_rollup (ein, total_amount, contribution_count) VALUES ('123456789', 5321, 2)"
    )
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestIrs527ProfileScope:
    def test_defaults_to_all_time_with_cycle_subtotal(self, irs527_app):
        html = irs527_app.test_client().get("/527/123456789").get_data(as_text=True)
        assert "Test 527 Org" in html and "Old Donor" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$5,321.00" in html
        assert "This cycle" in html and "$4,321.00" in html
        assert '<h2>Financial Summary <span class="scope-tag">All time</span></h2>' in html
        assert '<h2>Organization Details <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html

    def test_cycle_preset_narrows(self, irs527_app):
        html = irs527_app.test_client().get("/527/123456789?period=cycle:2026").get_data(as_text=True)
        assert 'data-scope-key="cycle:2026"' in html
        assert "$5,321.00" not in html
        assert "$4,321.00" in html
        assert '<h2>Financial Summary <span class="scope-tag">2026 Cycle</span></h2>' in html
        assert "time-scope-reset" in html


@pytest.fixture
def lobbying_app(tmp_path: Path):
    db_path = str(tmp_path / "lobbying_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute("INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Test Entity', 2026)")
    conn.execute("INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'Test Client')")
    conn.execute("INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2026)")
    conn.execute(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method)
        VALUES (1, 'donor:test', 'Test Client', 'Matched Donor', 0.95, 'token_set')
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_committee_agg (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, committee_id, committee_name, total_amount, contribution_count
        ) VALUES ('bulk_receipts', 'donor:test', 'Matched Donor', '', 'Chicago', 'IL',
                  '', '', '24680', 'Fallback Committee', 1200.0, 3)
        """
    )
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestLobbyingProfileScope:
    def test_entity_defaults_to_all_time_with_cycle_subtotal(self, lobbying_app):
        html = lobbying_app.test_client().get("/lobbying/1").get_data(as_text=True)
        assert "Test Entity" in html and "Test Client" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$1,200.00" in html                              # matched donor dollars, all time
        assert "This cycle" in html
        assert '<h2>Money Destinations <span class="scope-tag">All time</span></h2>' in html
        assert '<h2>Clients (1) <span class="scope-tag">All time</span></h2>' in html

    def test_client_cycle_preset_narrows(self, lobbying_app):
        html = lobbying_app.test_client().get("/lobbying/client/1?period=cycle:2026").get_data(as_text=True)
        assert "Test Client" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="cycle:2026"' in html
        assert '<h2>Money Destinations <span class="scope-tag">2026 Cycle</span></h2>' in html
        assert '<h2>Represented By (1) <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html
        assert "time-scope-reset" in html


@pytest.fixture
def openbook_app(tmp_path: Path):
    db_path = str(tmp_path / "openbook_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO openbook_vendor_seed (seed_text, seed_source) VALUES (?, ?)", ("CONTRIB VENDOR", "expenditures")
    )
    seed_id = conn.execute(
        "SELECT seed_id FROM openbook_vendor_seed WHERE seed_text = ?", ("CONTRIB VENDOR",)
    ).fetchone()["seed_id"]
    conn.execute(
        """
        INSERT INTO openbook_vendor_match (seed_id, openbook_vendor_key, openbook_vendor_label,
                                           match_method, confidence, search_term_used)
        VALUES (?, ?, ?, 'exact', 0.95, ?)
        """,
        (seed_id, "CONTRIB VENDOR", "Contrib Vendor", "CONTRIB VENDOR"),
    )
    # 5,000 inside the current cycle + 100 in 2023 -> 5,100 all time.
    conn.executemany(
        """
        INSERT INTO openbook_contributions_raw (openbook_vendor_key, contributor_name, recipient_name,
                                                contribution_date, amount, source_url, row_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("CONTRIB VENDOR", "Big Donor", "Sen. Smith", "2026-03-15", 5000.0, "https://example.com", "hash-c-1"),
            ("CONTRIB VENDOR", "Old Donor", "Rep. Jones", "2023-02-01", 100.0, "https://example.com", "hash-c-2"),
        ],
    )
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestOpenBookProfileScope:
    def test_defaults_to_all_time_with_cycle_subtotal(self, openbook_app):
        html = openbook_app.test_client().get("/openbook/CONTRIB%20VENDOR").get_data(as_text=True)
        assert "Contrib Vendor" in html and "Old Donor" in html
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "$5,100.00" in html
        assert "This cycle" in html and "$5,000.00" in html
        assert '<h2>Contributions <span class="scope-tag">All time</span></h2>' in html
        assert '<h2>Contracts <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html

    def test_cycle_preset_narrows(self, openbook_app):
        html = openbook_app.test_client().get("/openbook/CONTRIB%20VENDOR?period=cycle:2026").get_data(as_text=True)
        assert 'data-scope-key="cycle:2026"' in html
        assert "$5,100.00" not in html
        assert "$5,000.00" in html
        assert "Old Donor" not in html
        assert "time-scope-reset" in html
