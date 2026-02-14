"""Loader for IL Secretary of State lobbying entity/client CSV data."""
from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REQUIRED_HEADERS = {
    "ENT_REG_YEAR",
    "ENTITY_ID",
    "ENTITY_NAME",
    "CLIENT_ID",
    "CLIENT_NAME",
}


def _clean_text(value: str | None) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _to_int(value: str | None) -> Optional[int]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (ValueError, OverflowError):
        return None


def _chunked(rows, size: int = 5000):
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def load_lobbying_csv(conn: sqlite3.Connection, file_path: str | Path) -> dict:
    """Parse IL SOS lobbying CSV and upsert into lobbying tables.

    Returns stats: entities_loaded, clients_loaded, pairs_loaded.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Lobbying CSV not found: {file_path}")

    entities: dict[int, tuple] = {}
    clients: dict[int, tuple] = {}
    pairs: list[tuple] = []
    rows_skipped = 0

    with file_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        present_headers = set(reader.fieldnames or [])
        missing_headers = sorted(_REQUIRED_HEADERS - present_headers)
        if missing_headers:
            raise ValueError(f"Lobbying CSV missing required headers: {', '.join(missing_headers)}")

        for row_num, row in enumerate(reader, start=2):
            entity_id = _to_int(row.get("ENTITY_ID"))
            client_id = _to_int(row.get("CLIENT_ID"))
            reg_year = _to_int(row.get("ENT_REG_YEAR"))
            entity_name = _clean_text(row.get("ENTITY_NAME"))
            client_name = _clean_text(row.get("CLIENT_NAME"))

            if entity_id is None or client_id is None:
                logger.warning("Row %d: missing entity_id or client_id, skipping", row_num)
                rows_skipped += 1
                continue

            if entity_id not in entities:
                entities[entity_id] = (entity_id, entity_name, reg_year)

            if client_id not in clients:
                clients[client_id] = (client_id, client_name)

            pairs.append((entity_id, client_id, reg_year))

    # Upsert entities
    for chunk in _chunked(list(entities.values())):
        conn.executemany(
            """
            INSERT INTO lobbying_entities (entity_id, entity_name, reg_year)
            VALUES (?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                entity_name = COALESCE(excluded.entity_name, lobbying_entities.entity_name),
                reg_year = COALESCE(excluded.reg_year, lobbying_entities.reg_year)
            """,
            chunk,
        )
    conn.commit()

    # Upsert clients
    for chunk in _chunked(list(clients.values())):
        conn.executemany(
            """
            INSERT INTO lobbying_clients (client_id, client_name)
            VALUES (?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                client_name = COALESCE(excluded.client_name, lobbying_clients.client_name)
            """,
            chunk,
        )
    conn.commit()

    # Upsert pairs
    for chunk in _chunked(pairs):
        conn.executemany(
            """
            INSERT OR IGNORE INTO lobbying_entity_clients (entity_id, client_id, reg_year)
            VALUES (?, ?, ?)
            """,
            chunk,
        )
    conn.commit()

    stats = {
        "entities_loaded": len(entities),
        "clients_loaded": len(clients),
        "pairs_loaded": len(pairs),
        "rows_skipped": rows_skipped,
    }
    logger.info("Lobbying CSV loaded: %s", stats)
    return stats
