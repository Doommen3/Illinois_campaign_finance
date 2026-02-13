"""Tests for lobbying data loader."""
from pathlib import Path

from database.connection import get_db, init_db
from database.lobbying_loader import load_lobbying_csv


def _make_csv(tmp_path: Path, filename: str = "lobbying.csv", content: str = "") -> Path:
    csv_path = tmp_path / filename
    csv_path.write_text(content, encoding="utf-8")
    return csv_path


def test_load_lobbying_csv_basic(tmp_path: Path):
    csv_content = (
        '"ENT_REG_YEAR","ENTITY_ID","ENTITY_NAME","CLIENT_ID","CLIENT_NAME"\n'
        '"2026","100","Alpha Lobby Group","200","Acme Corp"\n'
        '"2026","100","Alpha Lobby Group","201","Beta Inc"\n'
        '"2026","101","Bravo Consulting","200","Acme Corp"\n'
    )
    csv_path = _make_csv(tmp_path, content=csv_content)

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    stats = load_lobbying_csv(conn, csv_path)

    assert stats["entities_loaded"] == 2
    assert stats["clients_loaded"] == 2
    assert stats["pairs_loaded"] == 3

    # Verify entity data
    entity = conn.execute(
        "SELECT entity_name FROM lobbying_entities WHERE entity_id = 100"
    ).fetchone()
    assert entity["entity_name"] == "Alpha Lobby Group"

    # Verify client data
    client = conn.execute(
        "SELECT client_name FROM lobbying_clients WHERE client_id = 200"
    ).fetchone()
    assert client["client_name"] == "Acme Corp"

    # Verify junction rows
    count = conn.execute("SELECT COUNT(*) AS c FROM lobbying_entity_clients").fetchone()["c"]
    assert count == 3

    conn.close()


def test_load_lobbying_csv_dedup_on_reload(tmp_path: Path):
    """Reloading the same CSV should not create duplicate entities/clients."""
    csv_content = (
        '"ENT_REG_YEAR","ENTITY_ID","ENTITY_NAME","CLIENT_ID","CLIENT_NAME"\n'
        '"2026","100","Alpha Lobby Group","200","Acme Corp"\n'
    )
    csv_path = _make_csv(tmp_path, content=csv_content)

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    load_lobbying_csv(conn, csv_path)
    stats = load_lobbying_csv(conn, csv_path)

    assert stats["entities_loaded"] == 1
    assert stats["clients_loaded"] == 1

    entity_count = conn.execute("SELECT COUNT(*) AS c FROM lobbying_entities").fetchone()["c"]
    assert entity_count == 1

    client_count = conn.execute("SELECT COUNT(*) AS c FROM lobbying_clients").fetchone()["c"]
    assert client_count == 1

    conn.close()


def test_load_lobbying_csv_stats_correct(tmp_path: Path):
    csv_content = (
        '"ENT_REG_YEAR","ENTITY_ID","ENTITY_NAME","CLIENT_ID","CLIENT_NAME"\n'
        '"2026","100","Entity A","200","Client X"\n'
        '"2026","100","Entity A","201","Client Y"\n'
        '"2025","101","Entity B","200","Client X"\n'
        '"2025","102","Entity C","202","Client Z"\n'
    )
    csv_path = _make_csv(tmp_path, content=csv_content)

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    stats = load_lobbying_csv(conn, csv_path)

    assert stats["entities_loaded"] == 3
    assert stats["clients_loaded"] == 3
    assert stats["pairs_loaded"] == 4

    conn.close()


def test_load_lobbying_csv_missing_file(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)

    try:
        load_lobbying_csv(conn, tmp_path / "nonexistent.csv")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass
    finally:
        conn.close()
