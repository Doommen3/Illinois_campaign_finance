"""Loader for IL Secretary of State lobbying entity/client CSV data."""
from __future__ import annotations

import csv
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HEADER_ALIASES = {
    "ENT_REG_YEAR": ("ENT_REG_YEAR",),
    "ENTITY_ID": ("ENTITY_ID", "ENT_ID"),
    "ENTITY_NAME": ("ENTITY_NAME", "ENT_NAME"),
    "ENTITY_ADDR1": ("ENTITY_ADDR1", "ENT_ADDR1"),
    "ENTITY_ADDR2": ("ENTITY_ADDR2", "ENT_ADDR2"),
    "ENTITY_CITY": ("ENTITY_CITY", "ENT_CITY"),
    "ENTITY_STATE": ("ENTITY_ST_ABBR", "ENTITY_STATE", "ENT_ST_ABBR"),
    "ENTITY_ZIP": ("ENTITY_ZIP", "ENT_ZIP"),
    "CLIENT_ID": ("CLIENT_ID",),
    "CLIENT_NAME": ("CLIENT_NAME",),
    "CLIENT_ADDR1": ("CLIENT_ADDR1",),
    "CLIENT_ADDR2": ("CLIENT_ADDR2",),
    "CLIENT_CITY": ("CLIENT_CITY",),
    "CLIENT_STATE": ("CLIENT_ST_ABBR", "CLIENT_STATE"),
    "CLIENT_ZIP": ("CLIENT_ZIP",),
    "CLIENT_STATUS": ("CLIENT_STATUS",),
}

_REQUIRED_HEADER_KEYS = (
    "ENT_REG_YEAR",
    "ENTITY_ID",
    "ENTITY_NAME",
    "CLIENT_ID",
    "CLIENT_NAME",
)


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


def _first_non_empty(row: dict[str, object], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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
    lobbyists: dict[int, tuple] = {}
    registrations: list[tuple] = []
    rows_skipped = 0

    with file_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        present_headers = set(reader.fieldnames or [])
        missing_headers = []
        for canonical in _REQUIRED_HEADER_KEYS:
            aliases = _HEADER_ALIASES[canonical]
            if not any(alias in present_headers for alias in aliases):
                missing_headers.append(canonical)
        if missing_headers:
            raise ValueError(f"Lobbying CSV missing required headers: {', '.join(missing_headers)}")

        for row_num, row in enumerate(reader, start=2):
            entity_id = _to_int(_first_non_empty(row, _HEADER_ALIASES["ENTITY_ID"]))
            client_id_raw = _to_int(_first_non_empty(row, _HEADER_ALIASES["CLIENT_ID"]))
            reg_year = _to_int(_first_non_empty(row, _HEADER_ALIASES["ENT_REG_YEAR"]))
            entity_name = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_NAME"]))
            entity_address_1 = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_ADDR1"]))
            entity_address_2 = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_ADDR2"]))
            entity_city = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_CITY"]))
            entity_state = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_STATE"]))
            entity_zip = _clean_text(_first_non_empty(row, _HEADER_ALIASES["ENTITY_ZIP"]))
            client_name = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_NAME"]))
            client_address_1 = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_ADDR1"]))
            client_address_2 = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_ADDR2"]))
            client_city = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_CITY"]))
            client_state = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_STATE"]))
            client_zip = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_ZIP"]))

            lobbyist_id = _to_int(row.get("LOBBYIST_ID"))
            lobbyist_first_name = _clean_text(row.get("LOBBYIST_FNAME"))
            lobbyist_middle_name = _clean_text(row.get("LOBBYIST_MNAME"))
            lobbyist_last_name = _clean_text(row.get("LOBBYIST_LNAME"))
            lobbyist_email = _clean_text(row.get("LOBBYIST_EMAIL"))
            lobbyist_phone = _clean_text(row.get("LOBBYIST_PHONE"))
            lobbyist_address_1 = _clean_text(row.get("LOBBYIST_ADDR1"))
            lobbyist_address_2 = _clean_text(row.get("LOBBYIST_ADDR2"))
            lobbyist_city = _clean_text(row.get("LOBBYIST_CITY"))
            lobbyist_state = _clean_text(row.get("LOBBYIST_ST_ABBR"))
            lobbyist_zip = _clean_text(row.get("LOBBYIST_ZIP"))
            lobbyist_status = _clean_text(row.get("LOBBYIST_STATUS"))
            client_status = _clean_text(_first_non_empty(row, _HEADER_ALIASES["CLIENT_STATUS"]))

            client_id = client_id_raw if client_id_raw and client_id_raw > 0 else None

            if entity_id is None:
                logger.warning("Row %d: missing entity_id, skipping", row_num)
                rows_skipped += 1
                continue

            if entity_id not in entities:
                entities[entity_id] = (
                    entity_id,
                    entity_name,
                    reg_year,
                    entity_address_1,
                    entity_address_2,
                    entity_city,
                    entity_state,
                    entity_zip,
                )

            if client_id is not None and client_name:
                if client_id not in clients:
                    clients[client_id] = (
                        client_id,
                        client_name,
                        client_address_1,
                        client_address_2,
                        client_city,
                        client_state,
                        client_zip,
                        client_status,
                    )

                pairs.append((entity_id, client_id, reg_year))

            if lobbyist_id is not None and lobbyist_id not in lobbyists:
                lobbyists[lobbyist_id] = (
                    lobbyist_id,
                    lobbyist_first_name,
                    lobbyist_middle_name,
                    lobbyist_last_name,
                    lobbyist_email,
                    lobbyist_phone,
                    lobbyist_address_1,
                    lobbyist_address_2,
                    lobbyist_city,
                    lobbyist_state,
                    lobbyist_zip,
                    lobbyist_status,
                )

            if lobbyist_id is not None:
                registrations.append(
                    (
                        lobbyist_id,
                        entity_id,
                        client_id,
                        reg_year,
                        lobbyist_status,
                        client_status,
                        file_path.name,
                    )
                )

    # Upsert entities
    for chunk in _chunked(list(entities.values())):
        conn.executemany(
            """
            INSERT INTO lobbying_entities (
                entity_id, entity_name, reg_year, address_1, address_2, city, state, postal_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                entity_name = COALESCE(excluded.entity_name, lobbying_entities.entity_name),
                reg_year = COALESCE(excluded.reg_year, lobbying_entities.reg_year),
                address_1 = COALESCE(excluded.address_1, lobbying_entities.address_1),
                address_2 = COALESCE(excluded.address_2, lobbying_entities.address_2),
                city = COALESCE(excluded.city, lobbying_entities.city),
                state = COALESCE(excluded.state, lobbying_entities.state),
                postal_code = COALESCE(excluded.postal_code, lobbying_entities.postal_code)
            """,
            chunk,
        )
    conn.commit()

    # Upsert clients
    for chunk in _chunked(list(clients.values())):
        conn.executemany(
            """
            INSERT INTO lobbying_clients (
                client_id, client_name, address_1, address_2, city, state, postal_code, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                client_name = COALESCE(excluded.client_name, lobbying_clients.client_name),
                address_1 = COALESCE(excluded.address_1, lobbying_clients.address_1),
                address_2 = COALESCE(excluded.address_2, lobbying_clients.address_2),
                city = COALESCE(excluded.city, lobbying_clients.city),
                state = COALESCE(excluded.state, lobbying_clients.state),
                postal_code = COALESCE(excluded.postal_code, lobbying_clients.postal_code),
                status = COALESCE(excluded.status, lobbying_clients.status)
            """,
            chunk,
        )
    conn.commit()

    # Upsert pairs
    for chunk in _chunked(pairs):
        conn.executemany(
            """
            INSERT INTO lobbying_entity_clients (entity_id, client_id, reg_year)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            chunk,
        )
    conn.commit()

    # Upsert lobbyists
    for chunk in _chunked(list(lobbyists.values())):
        conn.executemany(
            """
            INSERT INTO lobbying_lobbyists (
                lobbyist_id, first_name, middle_name, last_name, email, phone,
                address_1, address_2, city, state, postal_code, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lobbyist_id) DO UPDATE SET
                first_name = COALESCE(excluded.first_name, lobbying_lobbyists.first_name),
                middle_name = COALESCE(excluded.middle_name, lobbying_lobbyists.middle_name),
                last_name = COALESCE(excluded.last_name, lobbying_lobbyists.last_name),
                email = COALESCE(excluded.email, lobbying_lobbyists.email),
                phone = COALESCE(excluded.phone, lobbying_lobbyists.phone),
                address_1 = COALESCE(excluded.address_1, lobbying_lobbyists.address_1),
                address_2 = COALESCE(excluded.address_2, lobbying_lobbyists.address_2),
                city = COALESCE(excluded.city, lobbying_lobbyists.city),
                state = COALESCE(excluded.state, lobbying_lobbyists.state),
                postal_code = COALESCE(excluded.postal_code, lobbying_lobbyists.postal_code),
                status = COALESCE(excluded.status, lobbying_lobbyists.status)
            """,
            chunk,
        )
    conn.commit()

    # Upsert lobbyist registrations
    for chunk in _chunked(registrations):
        conn.executemany(
            """
            INSERT INTO lobbying_lobbyist_registrations (
                lobbyist_id, entity_id, client_id, reg_year,
                lobbyist_status, client_status, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            chunk,
        )
    conn.commit()

    stats = {
        "entities_loaded": len(entities),
        "clients_loaded": len(clients),
        "pairs_loaded": len(pairs),
        "lobbyists_loaded": len(lobbyists),
        "registrations_loaded": len(registrations),
        "rows_skipped": rows_skipped,
    }
    logger.info("Lobbying CSV loaded: %s", stats)
    return stats
