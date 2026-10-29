"""tests/pg_worlds.py: what the entity suites rely on when they share one schema per module.

A shared world refuses ``commit()``, so a test cannot leave it dirty; closing a connection rolls back what the test did
to it (rows and DDL alike); connections a test leaves open are closed when it ends; and a private world is a schema
of its own that the shared one never sees.
"""
import pytest

from database.connection import get_db
from tests.pg_worlds import module_world, private_world


@pytest.fixture(scope="module")
def shared_world():
    with module_world() as shared:
        shared.init_schema()
        seed = shared.connect(guard=False)
        try:
            seed.execute("CREATE TABLE pg_worlds_probe (id INTEGER)")
            seed.execute("INSERT INTO pg_worlds_probe VALUES (1)")
            seed.commit()
        finally:
            seed.close()
        yield shared


@pytest.fixture(autouse=True)
def pg_test_schema(shared_world):
    with shared_world.per_test():
        yield shared_world.schema


def _probe(conn):
    return [row["id"] for row in conn.execute("SELECT id FROM pg_worlds_probe ORDER BY id").fetchall()]


def test_get_db_hands_out_connections_that_refuse_to_commit():
    conn = get_db("ignored.db")
    conn.execute("UPDATE pg_worlds_probe SET id = 2")
    with pytest.raises(AssertionError, match="shared by every test"):
        conn.commit()


def test_closing_a_connection_rolls_back_the_rows_and_the_ddl_a_test_changed(shared_world):
    conn = shared_world.connect()
    conn.execute("UPDATE pg_worlds_probe SET id = 99")
    conn.execute("DROP TABLE pg_worlds_probe")
    conn.close()

    fresh = shared_world.connect()
    try:
        assert _probe(fresh) == [1]
    finally:
        fresh.close()


def test_per_test_closes_the_connections_a_test_left_open(shared_world):
    with shared_world.per_test():
        leaked = shared_world.connect()
    assert leaked._pg_conn.closed


def test_a_private_world_may_commit_and_the_shared_world_never_sees_it(shared_world, monkeypatch):
    with private_world(monkeypatch) as private:
        private.init_schema()
        conn = get_db("ignored.db")  # the hooks point at the private schema now
        conn.execute("CREATE TABLE pg_worlds_private_only (id INTEGER)")
        conn.commit()
        conn.close()
    monkeypatch.undo()  # the shared world's hooks are back

    conn = get_db("ignored.db")
    try:
        assert conn.execute("SELECT to_regclass('pg_worlds_private_only') AS rel").fetchone()["rel"] is None
        assert _probe(conn) == [1]
    finally:
        conn.close()
