"""Tests for insight features: confidence labels, 527 contributions, donor dedup, homepage insights, Sankey."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_insights.db")
    init_db(db_path)
    conn = get_db(db_path)

    # --- analytics_donor_summary: 3 rows (2 Griffin variants + 1 unique) ---
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_city, donor_state,
            total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', 'donor:griffin1', 'GRIFFIN, KENNETH', 'Chicago', 'IL', 500000.0, 10, 3)
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_city, donor_state,
            total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', 'donor:griffin2', 'GRIFFIN, KENNETH C', 'Chicago', 'IL', 250000.0, 5, 2)
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_city, donor_state,
            total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', 'donor:pritzker1', 'PRITZKER, J B', 'Chicago', 'IL', 100000.0, 3, 1)
        """
    )

    # --- analytics_donor_committee_agg: 2 rows (90%/10% split for donor-dependent committee) ---
    conn.execute(
        """
        INSERT INTO analytics_donor_committee_agg (source, donor_key, donor_name, donor_city, donor_state,
            committee_id, committee_name, total_amount, contribution_count)
        VALUES ('bulk_receipts', 'donor:griffin1', 'GRIFFIN, KENNETH', 'Chicago', 'IL',
            'C001', 'Citizens for Good Gov', 450000.0, 8)
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_committee_agg (source, donor_key, donor_name, donor_city, donor_state,
            committee_id, committee_name, total_amount, contribution_count)
        VALUES ('bulk_receipts', 'donor:pritzker1', 'PRITZKER, J B', 'Chicago', 'IL',
            'C001', 'Citizens for Good Gov', 50000.0, 2)
        """
    )

    # --- Lobbying data ---
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Power Lobby LLC', 2026)"
    )
    conn.execute(
        "INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'ComEd')"
    )
    conn.execute(
        "INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2026)"
    )

    # --- lobbying_donor_matches ---
    conn.execute(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method)
        VALUES (1, 'donor:griffin1', 'ComEd', 'GRIFFIN, KENNETH', 0.85, 'token_set')
        """
    )

    # --- irs527_organizations + reports + directors + contributions ---
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state)
        VALUES ('123456789', 100, 1, 'IL Democratic Fund', 'Springfield', 'IL')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_reports (form_id, ein, period_start, period_end,
            total_contributions, total_expenditures)
        VALUES (100, '123456789', '2025-01-01', '2025-06-30', 200000.0, 150000.0)
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state)
        VALUES (100, '123456789', 'IL Democratic Fund', 'MADIGAN, MICHAEL J', 'Chair', 'Chicago', 'IL')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date)
        VALUES (100, '123456789', 'IL Democratic Fund', 'BIG CORP INC', 'Chicago', 'IL', 50000.0, '2025-03-15')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date)
        VALUES (100, '123456789', 'IL Democratic Fund', 'UNION LOCAL 150', 'Peoria', 'IL', 25000.0, '2025-04-01')
        """
    )

    # --- irs527_director_candidate_matches ---
    conn.execute(
        """
        INSERT INTO irs527_director_candidate_matches (ein, org_name, director_name,
            candidate_id, candidate_name, candidate_source, score)
        VALUES ('123456789', 'IL Democratic Fund', 'MADIGAN, MICHAEL J',
            'C100', 'MICHAEL J MADIGAN', 'state', 0.95)
        """
    )

    # --- irs527_expenditure_recipient_matches (2 rows: high + low score) ---
    conn.execute(
        """
        INSERT INTO irs527_expenditure_recipient_matches (ein, org_name, recipient_name,
            matched_type, matched_id, matched_name, score)
        VALUES ('123456789', 'IL Democratic Fund', 'Citizens Committee',
            'committee', 'C001', 'Citizens for Good Gov', 0.95)
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_expenditure_recipient_matches (ein, org_name, recipient_name,
            matched_type, matched_id, matched_name, score)
        VALUES ('123456789', 'IL Democratic Fund', 'Some PAC',
            'committee', 'C002', 'Some Other PAC', 0.45)
        """
    )

    # --- irs527_expenditures (for dark money totals insight) ---
    conn.execute(
        """
        INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, date, purpose)
        VALUES (100, '123456789', 'IL Democratic Fund', 'Citizens Committee', 'Springfield', 'IL', 75000.0, '2025-05-01', 'Campaign support')
        """
    )

    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def empty_app(tmp_path: Path):
    db_path = str(tmp_path / "test_insights_empty.db")
    init_db(db_path)
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path})
    return app


@pytest.fixture
def empty_client(empty_app):
    return empty_app.test_client()


# ====================== Feature 4: Confidence Labels ======================

class TestConfidenceLabels:
    def test_high_confidence(self, app):
        with app.app_context():
            label, css = app.jinja_env.filters['confidence_label'](0.95)
            assert label == "High confidence"
            assert css == "confidence-high"

    def test_likely_match(self, app):
        with app.app_context():
            label, css = app.jinja_env.filters['confidence_label'](0.75)
            assert label == "Likely match"
            assert css == "confidence-likely"

    def test_possible_match(self, app):
        with app.app_context():
            label, css = app.jinja_env.filters['confidence_label'](0.55)
            assert label == "Possible match"
            assert css == "confidence-possible"

    def test_review_needed(self, app):
        with app.app_context():
            label, css = app.jinja_env.filters['confidence_label'](0.40)
            assert label == "Review needed"
            assert css == "confidence-review"

    def test_none_score(self, app):
        with app.app_context():
            label, css = app.jinja_env.filters['confidence_label'](None)
            assert label == "Unknown"
            assert css == "confidence-unknown"

    def test_dark_money_shows_labels(self, client):
        resp = client.get('/527/dark-money')
        assert resp.status_code == 200
        assert b'confidence-high' in resp.data
        assert b'confidence-review' in resp.data


# ====================== Feature 5: 527 Contribution Enhancement ======================

class TestContributionImport:
    def test_dark_money_shows_contribution_stats(self, client):
        resp = client.get('/527/dark-money')
        assert resp.status_code == 200
        assert b'527 Funding Sources' in resp.data
        assert b'BIG CORP INC' in resp.data

    def test_org_detail_shows_contributions(self, client):
        resp = client.get('/527/123456789')
        assert resp.status_code == 200
        assert b'BIG CORP INC' in resp.data
        assert b'UNION LOCAL 150' in resp.data

    def test_empty_db_graceful(self, empty_client):
        resp = empty_client.get('/527/dark-money')
        assert resp.status_code == 200


# ====================== Feature 2: Donor Dedup ======================

class TestDonorDedup:
    def test_groups_donors_by_name(self, client):
        resp = client.get('/person-intelligence?q=Griffin')
        assert resp.status_code == 200
        # Should group the two Griffin variants
        assert b'GRIFFIN' in resp.data

    def test_shows_merged_totals(self, client):
        resp = client.get('/person-intelligence?q=Griffin')
        assert resp.status_code == 200
        # Merged total: 500000 + 250000 = 750000
        assert b'750,000' in resp.data

    def test_expandable_sub_rows(self, client):
        resp = client.get('/person-intelligence?q=Griffin')
        assert resp.status_code == 200
        # Should have details/summary for multi-key groups
        assert b'<details' in resp.data
        assert b'2 keys' in resp.data

    def test_single_donor_no_expand(self, client):
        resp = client.get('/person-intelligence?q=Pritzker')
        assert resp.status_code == 200
        assert b'PRITZKER' in resp.data
        # Single donor should NOT have expandable details
        assert b'<details' not in resp.data


# ====================== Feature 1: Homepage Insights ======================

class TestHomepageInsights:
    def test_key_findings_section_present(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Key Findings' in resp.data

    def test_donor_dependent_committees(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        # Griffin donates 450K/500K = 90% of Citizens for Good Gov
        assert b'Citizens for Good Gov' in resp.data

    def test_lobbying_donors(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'ComEd' in resp.data

    def test_director_candidates(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'MADIGAN' in resp.data

    def test_director_candidates_dedupe_visible_name_pairs(self, app, client):
        conn = get_db(app.config["DATABASE_PATH"])
        conn.execute(
            """
            INSERT INTO irs527_director_candidate_matches (
                ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("987654321", "Another Org", "MADIGAN, MICHAEL J", "C101", "MICHAEL J MADIGAN", "state", 0.94),
        )
        conn.execute(
            """
            INSERT INTO irs527_director_candidate_matches (
                ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("999999999", "Third Org", "MADIGAN, MICHAEL J", "C102", "MICHAEL J MADIGAN", "state", 0.93),
        )
        conn.commit()
        conn.close()

        resp = client.get('/')
        assert resp.status_code == 200
        assert resp.data.count(b'MICHAEL J MADIGAN') == 1

    def test_empty_db_graceful(self, empty_client):
        resp = empty_client.get('/')
        assert resp.status_code == 200
        assert b'Key Findings' in resp.data


# ====================== Feature 3: Sankey Diagram ======================

class TestSankeyDiagram:
    def test_page_loads_with_d3(self, client):
        resp = client.get('/lobbying/flows')
        assert resp.status_code == 200
        assert b'd3' in resp.data or b'D3' in resp.data

    def test_json_endpoint_returns_nodes_links(self, client):
        resp = client.get('/lobbying/flows/data')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'nodes' in data
        assert 'links' in data

    def test_nodes_have_types(self, client):
        resp = client.get('/lobbying/flows/data')
        data = resp.get_json()
        if data['nodes']:
            assert 'type' in data['nodes'][0]

    def test_filter_works(self, client):
        resp = client.get('/lobbying/flows/data?client=ComEd')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data['nodes'], list)

    def test_nonexistent_filter_returns_empty(self, client):
        resp = client.get('/lobbying/flows/data?client=NONEXISTENT_XYZ')
        data = resp.get_json()
        assert data['nodes'] == []
        assert data['links'] == []

    def test_empty_db_returns_empty(self, empty_client):
        resp = empty_client.get('/lobbying/flows/data')
        data = resp.get_json()
        assert data['nodes'] == []
        assert data['links'] == []
