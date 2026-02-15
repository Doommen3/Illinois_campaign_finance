"""Tests for the guided investigation workspace UX and export flows."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_guided_workspace.db")
    init_db(db_path)
    conn = get_db(db_path)

    conn.execute(
        """
        INSERT INTO analytics_donor_summary (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, total_amount, contribution_count, committee_count
        ) VALUES (
            'bulk_receipts', 'donor:alpha', 'Alpha Industries', '', 'Chicago', 'IL',
            '', '', 125000.0, 12, 4
        )
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_committee_agg (
            source, donor_key, donor_name, donor_address, donor_city, donor_state,
            occupation, employer, committee_id, committee_name, total_amount, contribution_count
        ) VALUES (
            'bulk_receipts', 'donor:alpha', 'Alpha Industries', '', 'Chicago', 'IL',
            '', '', '9001', 'People for Better Transit', 90000.0, 6
        )
        """
    )
    conn.execute(
        """
        INSERT INTO lobbying_clients (client_id, client_name)
        VALUES (1, 'Alpha Industries')
        """
    )
    conn.execute(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method)
        VALUES (1, 'donor:alpha', 'Alpha Industries', 'Alpha Industries', 0.93, 'token_set')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name)
        VALUES ('111111111', 10, 1, 'Alpha Action Fund')
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_large_contributions (
            source, committee_name, donor_name, event_date, amount, large_threshold
        ) VALUES (
            'bulk_receipts', 'People for Better Transit', 'Alpha Industries', '2026-02-03', 45000.0, 10000.0
        )
        """
    )
    conn.commit()
    conn.close()

    return create_app({"TESTING": True, "DATABASE_PATH": db_path})


@pytest.fixture
def client(app):
    return app.test_client()


def test_guided_workspace_route_loads(client):
    response = client.get('/investigate')
    assert response.status_code == 200
    assert b'Guided Investigation Workspace' in response.data
    assert b'Investigate a person/organization' in response.data
    assert b'Trace money flow' in response.data
    assert b'Find unusual patterns this month' in response.data


def test_guided_workspace_has_interpretability_scaffolding(client):
    response = client.get('/investigate?tab=flow&q=Alpha')
    assert response.status_code == 200
    html = response.data.decode()
    assert 'What this means:' in html
    assert 'How calculated:' in html
    assert 'Caveats:' in html
    assert 'Advanced filters (optional)' in html


def test_guided_workspace_shows_confidence_badges(client):
    response = client.get('/investigate?tab=investigate&q=Alpha')
    assert response.status_code == 200
    html = response.data.decode()
    assert 'High confidence' in html or 'Likely match' in html or 'Possible match' in html


def test_guided_workspace_csv_export(client):
    response = client.get('/investigate?tab=flow&q=Alpha&format=csv')
    assert response.status_code == 200
    assert response.mimetype == 'text/csv'
    assert b'donor_name,committee_name,total_amount,contribution_count' in response.data


def test_guided_workspace_methodology_export(client):
    response = client.get('/investigate?tab=patterns&format=notes')
    assert response.status_code == 200
    assert response.mimetype == 'text/plain'
    text = response.data.decode()
    assert 'How calculated:' in text
    assert 'Caveats:' in text
