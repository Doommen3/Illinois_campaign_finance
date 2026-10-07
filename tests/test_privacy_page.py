"""Tests for the /privacy removal-request page and its footer link."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp.app import create_app
from database.connection import init_db


@pytest.fixture
def client(tmp_path: Path):
    db_path = str(tmp_path / "test_privacy_page.db")
    init_db(db_path)
    app = create_app({
        'TESTING': True,
        'DATABASE_PATH': db_path
    })
    return app.test_client()


def test_privacy_page_renders_redaction_policy(client):
    response = client.get('/privacy')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Redaction Requested' in body
    assert '72 hours' in body
    assert 'RedactionRequest.aspx' in body


def test_footer_links_to_privacy_page(client):
    response = client.get('/about')
    assert response.status_code == 200
    assert 'href="/privacy"' in response.get_data(as_text=True)
