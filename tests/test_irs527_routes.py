"""Tests for IRS 527 web routes."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_irs527_routes.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Seed some 527 data
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
        INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, purpose)
        VALUES (100, '123456789', 'Test 527 Org', 'Recipient Inc', 'Springfield', 'IL', 5000, 'Campaign support')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_reports (
            form_id, ein, period_start, period_end, org_name, total_contributions, total_expenditures
        ) VALUES (100, '123456789', '2026-01-01', '2026-03-31', 'Test 527 Org', 12345, 6789)
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contributions (
            form_id, ein, org_name, contributor_name, city, state, amount, date
        ) VALUES (100, '123456789', 'Test 527 Org', 'Acme Donor', 'Chicago', 'IL', 4321, '2026-02-01')
        """
    )
    conn.commit()
    conn.close()

    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_527_list(client):
    response = client.get('/527/')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data
    assert b'$12,345.00' in response.data
    assert b'$6,789.00' in response.data
    assert b'$4,321.00' in response.data


def test_527_list_search(client):
    response = client.get('/527/?q=Test')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data


def test_527_detail(client):
    response = client.get('/527/123456789')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data
    assert b'Jane Director' in response.data
    assert b'Recipient Inc' in response.data
    assert b'Acme Donor' in response.data
    assert b'Contributions To This Organization' in response.data


def test_527_detail_not_found(client):
    response = client.get('/527/999999999')
    assert response.status_code == 404


def test_527_dark_money(client):
    response = client.get('/527/dark-money')
    assert response.status_code == 200


@pytest.fixture
def empty_app(tmp_path: Path):
    db_path = str(tmp_path / "test_empty.db")
    init_db(db_path)
    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def empty_client(empty_app):
    return empty_app.test_client()


def test_527_list_empty_tables(empty_client):
    response = empty_client.get('/527/')
    assert response.status_code == 200


def test_527_dark_money_empty_tables(empty_client):
    response = empty_client.get('/527/dark-money')
    assert response.status_code == 200
