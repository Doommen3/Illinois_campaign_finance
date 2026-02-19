"""Shared pytest fixtures: redirect all test DB access to PostgreSQL ilcf_test.

Each test gets a fresh schema (``t_<short_uuid>``) inside the ``ilcf_test``
database.  Uses module-level override hooks (``_get_db_override`` /
``_init_db_override``) in ``database.connection`` so that **all** callers —
including those that did ``from database.connection import get_db`` at module
load time — are transparently redirected to PostgreSQL.

Teardown drops the schema with CASCADE.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

_TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://devin@localhost/ilcf_test",
)


def _short_uuid() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def pg_test_schema(monkeypatch, tmp_path):
    """Per-test PostgreSQL schema isolation."""
    schema_name = f"t_{_short_uuid()}"

    # Create schema using a raw admin connection
    admin_conn = psycopg.connect(_TEST_DSN)
    admin_conn.autocommit = True
    admin_conn.execute(f"CREATE SCHEMA {schema_name}")
    admin_conn.close()

    from database.pg_compat import PostgresCompatConnection
    from database import connection as conn_mod

    _conns: list[PostgresCompatConnection] = []
    _schema_initialized = False

    def _patched_get_db(db_path=None):
        c = PostgresCompatConnection(_TEST_DSN, schema=schema_name)
        _conns.append(c)
        return c

    def _patched_init_db(db_path=None):
        nonlocal _schema_initialized
        if _schema_initialized:
            return
        c = conn_mod.get_db(db_path)
        try:
            conn_mod.ensure_schema(c)
            _schema_initialized = True
        finally:
            conn_mod.close_db(c)

    # Set the module-level override hooks — these are checked inside the
    # function body of get_db/init_db, so even stale local-import references
    # (``from database.connection import get_db``) route through the override.
    monkeypatch.setattr(conn_mod, "_get_db_override", _patched_get_db)
    monkeypatch.setattr(conn_mod, "_init_db_override", _patched_init_db)
    # Also patch the module attributes for callers that do conn_mod.get_db()
    monkeypatch.setattr(conn_mod, "get_db", _patched_get_db)
    monkeypatch.setattr(conn_mod, "init_db", _patched_init_db)

    monkeypatch.setenv("DATABASE_URL", _TEST_DSN)
    monkeypatch.delenv("DATABASE_PATH", raising=False)

    yield schema_name

    # Teardown: close any open connections, then drop schema
    for c in _conns:
        try:
            c.close()
        except Exception:
            pass

    admin_conn = psycopg.connect(_TEST_DSN)
    admin_conn.autocommit = True
    try:
        admin_conn.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
    finally:
        admin_conn.close()
