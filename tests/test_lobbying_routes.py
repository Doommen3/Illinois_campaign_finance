"""Tests for lobbying web routes."""
from pathlib import Path

import pytest

from database.connection import get_db, init_db
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_lobbying_routes.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Seed some lobbying data
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (1, 'Test Entity', 2026)"
    )
    conn.execute(
        "INSERT INTO lobbying_clients (client_id, client_name) VALUES (1, 'Test Client')"
    )
    conn.execute(
        "INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year) VALUES (1, 1, 2026)"
    )
    conn.commit()
    conn.close()

    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_lobbying_list(client):
    response = client.get('/lobbying/')
    assert response.status_code == 200
    assert b'Test Entity' in response.data


def test_lobbying_list_search(client):
    response = client.get('/lobbying/?q=Test')
    assert response.status_code == 200
    assert b'Test Entity' in response.data


def test_lobbying_entity_detail(client):
    response = client.get('/lobbying/1')
    assert response.status_code == 200
    assert b'Test Entity' in response.data
    assert b'Test Client' in response.data


def test_lobbying_entity_not_found(client):
    response = client.get('/lobbying/9999')
    assert response.status_code == 404


def test_lobbying_client_detail(client):
    response = client.get('/lobbying/client/1')
    assert response.status_code == 200
    assert b'Test Client' in response.data
    assert b'Test Entity' in response.data


def test_lobbying_client_not_found(client):
    response = client.get('/lobbying/client/9999')
    assert response.status_code == 404


@pytest.fixture
def empty_app(tmp_path: Path):
    db_path = str(tmp_path / "test_empty.db")
    init_db(db_path)
    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def empty_client(empty_app):
    return empty_app.test_client()


def test_lobbying_list_empty_tables(empty_client):
    response = empty_client.get('/lobbying/')
    assert response.status_code == 200
