"""Module-scoped PostgreSQL schemas for suites that only READ one seeded world (test-speed program, unit T1).

``tests/conftest.py`` gives every test a schema of its own, and ``init_db`` runs all of ``database/schema.sql`` in it
(about 0.2 s, plus 0.05 s to drop it again). That is the right default, but a module of hundreds of cases that never
write to the rows they read pays it hundreds of times. Such a module builds its world once::

    @pytest.fixture(scope="module")
    def shared_world():
        with module_world() as shared:            # schema + database.connection hooks, dropped after the module
            shared.init_schema()                  # schema.sql, once
            seed = shared.connect(guard=False)
            try:
                seed_the_tables(seed)             # commits
            finally:
                seed.close()
            yield shared

    @pytest.fixture(autouse=True)
    def pg_test_schema(shared_world):             # shadows conftest.py's per-test fixture of the same name
        with shared_world.per_test():
            yield shared_world.schema

Rules of a shared world:

* Every connection handed out by ``get_db()`` or ``shared_world.connect()`` refuses ``commit()``. A test may UPDATE
  rows or DROP a table inside its own transaction -- closing the connection rolls it back -- but it cannot leave the
  world dirty for the next test. A test that has to commit (inserts rows, creates tables) takes a world of its own
  with ``private_world(monkeypatch)``: the hooks point there until the test ends, then back at the shared one.
* Connections a test leaves open are closed when it ends (``per_test``), as ``conftest.py`` does for its schema.
* With ``pytest-xdist`` use ``--dist loadfile`` so a module's tests share one worker, hence one world.
"""
from __future__ import annotations

import contextlib

import psycopg
import pytest

from database import connection as conn_mod
from database.pg_compat import PostgresCompatConnection
from tests.conftest import _TEST_DSN, _short_uuid, ddl_slot


def _forbid_commit() -> None:
    raise AssertionError(
        "this schema is shared by every test of the module: roll back (close the connection) instead of "
        "committing, or take private_world(monkeypatch) for a test that has to write"
    )


class SchemaWorld:
    """One schema of ``ilcf_test`` plus the ``database.connection`` hooks that route every ``get_db()`` to it.

    The hooks are installed through ``patch`` (a ``pytest.MonkeyPatch``) exactly as ``tests/conftest.py`` installs its
    own, so they are undone when the patch is.
    """

    def __init__(self, patch: pytest.MonkeyPatch, *, shared: bool) -> None:
        self.schema = f"t_{_short_uuid()}"
        self._shared = shared  # shared worlds hand out connections that cannot commit
        self._conns: list[PostgresCompatConnection] = []
        self._initialized = False
        admin = psycopg.connect(_TEST_DSN, autocommit=True)
        try:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        finally:
            admin.close()
        patch.setattr(conn_mod, "_get_db_override", self._get_db)
        patch.setattr(conn_mod, "_init_db_override", self._init_db)
        patch.setattr(conn_mod, "get_db", self._get_db)
        patch.setattr(conn_mod, "init_db", self._init_db)
        patch.setenv("DATABASE_URL", _TEST_DSN)
        patch.delenv("DATABASE_PATH", raising=False)

    def connect(self, *, guard: bool = True) -> PostgresCompatConnection:
        """A new connection on the schema; ``guard=False`` for the one that seeds it (commits are allowed)."""
        conn = PostgresCompatConnection(_TEST_DSN, schema=self.schema)
        if guard:
            conn.commit = _forbid_commit  # instance attribute: the class name must stay what ensure_schema checks
        self._conns.append(conn)
        return conn

    def init_schema(self) -> None:
        """Run schema.sql in the schema (once, like conftest.py's patched ``init_db``)."""
        if self._initialized:
            return
        conn = self.connect(guard=False)
        try:
            with ddl_slot():
                conn_mod.ensure_schema(conn)
            self._initialized = True
        finally:
            conn.close()

    def _get_db(self, db_path=None) -> PostgresCompatConnection:
        return self.connect(guard=self._shared)

    def _init_db(self, db_path=None) -> None:
        self.init_schema()

    @contextlib.contextmanager
    def per_test(self):
        """Close the connections a test leaves open."""
        mark = len(self._conns)
        try:
            yield
        finally:
            for conn in self._conns[mark:]:
                with contextlib.suppress(Exception):
                    conn.close()
            del self._conns[mark:]

    def close(self) -> None:
        """Close every connection and drop the schema."""
        for conn in self._conns:
            with contextlib.suppress(Exception):
                conn.close()
        self._conns.clear()
        admin = psycopg.connect(_TEST_DSN, autocommit=True)
        try:
            with ddl_slot():
                admin.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            admin.close()


@contextlib.contextmanager
def module_world():
    """A shared world for the whole module; use inside a ``scope="module"`` fixture."""
    with pytest.MonkeyPatch.context() as patch:
        world = SchemaWorld(patch, shared=True)
        try:
            yield world
        finally:
            world.close()


@contextlib.contextmanager
def private_world(patch: pytest.MonkeyPatch):
    """A world for ONE test (pass the function-scoped ``monkeypatch``): commits are allowed, the hooks point here
    until the test ends and then return to the module's world."""
    world = SchemaWorld(patch, shared=False)
    try:
        yield world
    finally:
        world.close()
