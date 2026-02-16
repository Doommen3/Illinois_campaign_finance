"""Tests for Postgres-oriented init-db behavior."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.connection as db_connection


class _DummyConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_init_db_accepts_postgres_dsn(monkeypatch):
    conn = _DummyConn()
    called = {"ensure": 0, "close": 0}

    monkeypatch.setattr(db_connection, "get_db", lambda _target: conn)

    def _fake_ensure_schema(_conn):
        called["ensure"] += 1

    def _fake_close_db(_conn):
        called["close"] += 1
        _conn.close()

    monkeypatch.setattr(db_connection, "ensure_schema", _fake_ensure_schema)
    monkeypatch.setattr(db_connection, "close_db", _fake_close_db)

    db_connection.init_db("postgresql://example/ilcf")

    assert called["ensure"] == 1
    assert called["close"] == 1
    assert conn.closed is True


def test_adapt_schema_for_postgres_autoincrement_translation():
    schema = """
    CREATE TABLE foo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT
    );
    CREATE TABLE bar (
        rowid_local INTEGER PRIMARY KEY AUTOINCREMENT
    );
    """
    adapted = db_connection._adapt_schema_for_postgres(schema)
    assert "AUTOINCREMENT" not in adapted
    assert "id BIGSERIAL PRIMARY KEY" in adapted
    assert "rowid_local BIGSERIAL PRIMARY KEY" in adapted
