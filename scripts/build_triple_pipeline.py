#!/usr/bin/env python3
"""Build entity-resolution layer for the triple-channel influence pipeline."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.entity_resolution import (
    EntityProfile,
    EntityResolver,
    ResolverConfig,
    compute_match_score,
    blocking_keys,
)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_donor_key(donor_key: Optional[str]) -> Tuple[str, str, str, str, str, str, str]:
    parts = (donor_key or "").split("|")
    while len(parts) < 7:
        parts.append("")
    return (
        parts[0],
        parts[1],
        parts[2],
        parts[3],
        parts[4],
        parts[5],
        parts[6],
    )


def _join_address(*parts: Optional[str]) -> Optional[str]:
    tokens = [p.strip() for p in parts if p and p.strip()]
    if not tokens:
        return None
    return " ".join(tokens)


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # YYYY-MM-DD or YYYY-MM-DD HH:MM:SS
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    # YYYYMMDD
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    # YYYYMM
    if len(text) == 6 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m").date()
        except ValueError:
            return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _date_to_iso(value: Optional[date]) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d")


def _min_date(values: Iterable[Optional[str]]) -> Optional[date]:
    parsed = [d for d in (_parse_date(v) for v in values) if d]
    return min(parsed) if parsed else None


def _max_date(values: Iterable[Optional[str]]) -> Optional[date]:
    parsed = [d for d in (_parse_date(v) for v in values) if d]
    return max(parsed) if parsed else None


def _month_key(value: Optional[str]) -> Optional[str]:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return parsed.strftime("%Y-%m")


def _months_between(start_iso: Optional[str], end_iso: Optional[str]) -> int:
    start = _parse_date(start_iso)
    end = _parse_date(end_iso)
    if not start or not end:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(0, months)


def _safe_float(value: Optional[float]) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_org_type(name: str) -> str:
    lowered = name.lower()
    org_markers = [
        "committee",
        "pac",
        "fund",
        "union",
        "association",
        "assn",
        "inc",
        "llc",
        "corp",
        "party",
    ]
    if any(marker in lowered for marker in org_markers):
        return "organization"
    if "," in name and any(token.strip() for token in name.split(",")):
        return "person"
    return "organization"


def load_lobby_profiles(conn: sqlite3.Connection) -> Tuple[Dict[str, EntityProfile], Dict[str, dict]]:
    profiles: Dict[str, EntityProfile] = {}
    meta: Dict[str, dict] = {}

    for row in conn.execute("SELECT client_id, client_name FROM lobbying_clients"):
        record_id = f"lobby_client:{row['client_id']}"
        profile = EntityProfile(
            record_id=record_id,
            source="lobby_client",
            name=row["client_name"] or "",
            identifiers={"lobby_client_id": str(row["client_id"])},
        )
        profiles[record_id] = profile
        meta[record_id] = {
            "entity_type": "lobby_client",
            "source_table": "lobbying_clients",
            "source_id": row["client_id"],
        }

    for row in conn.execute("SELECT entity_id, entity_name FROM lobbying_entities"):
        record_id = f"lobby_entity:{row['entity_id']}"
        profile = EntityProfile(
            record_id=record_id,
            source="lobby_entity",
            name=row["entity_name"] or "",
            identifiers={"lobby_entity_id": str(row["entity_id"])},
        )
        profiles[record_id] = profile
        meta[record_id] = {
            "entity_type": "lobby_entity",
            "source_table": "lobbying_entities",
            "source_id": row["entity_id"],
        }

    return profiles, meta


def _latest_irs527_org_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    try:
        return conn.execute(
            """
            SELECT * FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY ein
                        ORDER BY COALESCE(form_id_seq, 0) DESC, COALESCE(insert_datetime, '') DESC
                    ) AS rn
                FROM irs527_organizations
                WHERE ein IS NOT NULL
            ) WHERE rn = 1
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            """
            SELECT * FROM irs527_organizations
            WHERE ein IS NOT NULL
            ORDER BY COALESCE(form_id_seq, 0) DESC, COALESCE(insert_datetime, '') DESC
            """
        ).fetchall()
        seen = set()
        latest = []
        for row in rows:
            if row["ein"] in seen:
                continue
            seen.add(row["ein"])
            latest.append(row)
        return latest


def load_irs527_profiles(conn: sqlite3.Connection) -> Tuple[Dict[str, EntityProfile], Dict[str, dict]]:
    profiles: Dict[str, EntityProfile] = {}
    meta: Dict[str, dict] = {}
    directors_by_ein: Dict[str, List[str]] = defaultdict(list)

    for row in conn.execute(
        "SELECT ein, person_name FROM irs527_directors WHERE person_name IS NOT NULL AND TRIM(person_name) != ''"
    ):
        directors_by_ein[row["ein"]].append(row["person_name"])

    for row in _latest_irs527_org_rows(conn):
        ein = row["ein"]
        record_id = f"irs527:{ein}"
        address = _join_address(row["address_1"], row["address_2"])
        profile = EntityProfile(
            record_id=record_id,
            source="irs527",
            name=row["org_name"] or "",
            aliases=[name for name in {row["org_name"]} if name],
            address=address,
            city=row["city"],
            state=row["state"],
            postal_code=row["zip"],
            officers=directors_by_ein.get(ein, []),
            identifiers={"ein": ein},
        )
        profiles[record_id] = profile
        meta[record_id] = {
            "entity_type": "irs527_org",
            "source_table": "irs527_organizations",
            "source_id": ein,
        }

    return profiles, meta


def load_committee_profiles(conn: sqlite3.Connection) -> Tuple[Dict[str, EntityProfile], Dict[str, dict]]:
    profiles: Dict[str, EntityProfile] = {}
    meta: Dict[str, dict] = {}

    rows = conn.execute(
        """
        SELECT committee_id_sbe, committee_name, address_line_1, address_line_2, city, state, postal_code
        FROM bulk_committees_clean
        WHERE committee_id_sbe IS NOT NULL
        """
    )
    for row in rows:
        record_id = f"committee:{row['committee_id_sbe']}"
        address = _join_address(row["address_line_1"], row["address_line_2"])
        profile = EntityProfile(
            record_id=record_id,
            source="committee",
            name=row["committee_name"] or "",
            address=address,
            city=row["city"],
            state=row["state"],
            postal_code=row["postal_code"],
            identifiers={"committee_id_sbe": str(row["committee_id_sbe"])},
        )
        profiles[record_id] = profile
        meta[record_id] = {
            "entity_type": "committee",
            "source_table": "bulk_committees_clean",
            "source_id": row["committee_id_sbe"],
        }
    return profiles, meta


def load_donor_profiles(
    conn: sqlite3.Connection, alias_limit: int = 15
) -> Tuple[Dict[str, EntityProfile], Dict[str, dict], Dict[str, str]]:
    profiles: Dict[str, EntityProfile] = {}
    meta: Dict[str, dict] = {}
    donor_key_to_entity: Dict[str, str] = {}

    rows = conn.execute(
        """
        SELECT entity_id, canonical_name, display_name
        FROM donor_entity_local
        WHERE source = 'bulk_receipts'
        """
    )
    for row in rows:
        entity_id = row["entity_id"]
        record_id = f"donor_entity:{entity_id}"
        name = row["display_name"] or row["canonical_name"] or ""
        profile = EntityProfile(
            record_id=record_id,
            source="donor_entity",
            name=name,
            identifiers={"donor_entity_id": entity_id},
        )
        profiles[record_id] = profile
        meta[record_id] = {
            "entity_type": "donor_entity",
            "source_table": "donor_entity_local",
            "source_id": entity_id,
        }

    member_rows = conn.execute(
        """
        SELECT entity_id, donor_key, donor_name, donor_city, donor_state, donor_zip5
        FROM donor_entity_local_member
        WHERE source = 'bulk_receipts'
        """
    )
    alias_counts: Dict[str, int] = Counter()
    for row in member_rows:
        entity_id = row["entity_id"]
        record_id = f"donor_entity:{entity_id}"
        profile = profiles.get(record_id)
        if not profile:
            continue

        donor_key = row["donor_key"]
        if donor_key:
            donor_key_to_entity[donor_key] = entity_id
            _, _, address1, address2, city, state, zip_code = _parse_donor_key(donor_key)
            if not profile.address and (address1 or address2):
                profile.address = _join_address(address1, address2)
            if not profile.city and city:
                profile.city = city
            if not profile.state and state:
                profile.state = state
            if not profile.postal_code and zip_code:
                profile.postal_code = zip_code

        donor_name = row["donor_name"]
        if donor_name and alias_counts[record_id] < alias_limit:
            profile.aliases.append(donor_name)
            alias_counts[record_id] += 1

    return profiles, meta, donor_key_to_entity


def collect_lobbying_stats(conn: sqlite3.Connection) -> tuple[dict[int, dict], dict[int, dict]]:
    client_stats: dict[int, dict] = {}
    entity_stats: dict[int, dict] = {}

    for row in conn.execute(
        """
        SELECT client_id, MIN(reg_year) AS min_year, MAX(reg_year) AS max_year, COUNT(*) AS cnt
        FROM lobbying_entity_clients
        GROUP BY client_id
        """
    ):
        client_stats[row["client_id"]] = {
            "count": int(row["cnt"] or 0),
            "min_year": row["min_year"],
            "max_year": row["max_year"],
        }

    for row in conn.execute(
        """
        SELECT entity_id, MIN(reg_year) AS min_year, MAX(reg_year) AS max_year, COUNT(*) AS cnt
        FROM lobbying_entity_clients
        GROUP BY entity_id
        """
    ):
        entity_stats[row["entity_id"]] = {
            "count": int(row["cnt"] or 0),
            "min_year": row["min_year"],
            "max_year": row["max_year"],
        }

    return client_stats, entity_stats


def collect_irs527_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    stats: dict[str, dict] = {}

    for row in conn.execute(
        """
        SELECT ein, MIN(period_start) AS min_start, MAX(period_end) AS max_end
        FROM irs527_reports
        WHERE ein IS NOT NULL
        GROUP BY ein
        """
    ):
        stats[row["ein"]] = {
            "min_start": row["min_start"],
            "max_end": row["max_end"],
            "min_date": row["min_start"],
            "max_date": row["max_end"],
            "total_expenditures": 0.0,
            "total_contributions": 0.0,
        }

    for row in conn.execute(
        """
        SELECT ein, MIN(date) AS min_date, MAX(date) AS max_date, SUM(amount) AS total_amount
        FROM irs527_expenditures
        WHERE ein IS NOT NULL
        GROUP BY ein
        """
    ):
        entry = stats.setdefault(
            row["ein"],
            {
                "min_start": None,
                "max_end": None,
                "min_date": None,
                "max_date": None,
                "total_expenditures": 0.0,
                "total_contributions": 0.0,
            },
        )
        entry["total_expenditures"] = _safe_float(row["total_amount"])
        entry["min_date"] = _min_date([entry.get("min_date"), row["min_date"]])
        entry["max_date"] = _max_date([entry.get("max_date"), row["max_date"]])

    for row in conn.execute(
        """
        SELECT ein, SUM(amount) AS total_amount
        FROM irs527_contributions
        WHERE ein IS NOT NULL AND amount IS NOT NULL
        GROUP BY ein
        """
    ):
        entry = stats.setdefault(
            row["ein"],
            {
                "min_start": None,
                "max_end": None,
                "min_date": None,
                "max_date": None,
                "total_expenditures": 0.0,
                "total_contributions": 0.0,
            },
        )
        entry["total_contributions"] = _safe_float(row["total_amount"])

    return stats


def collect_committee_stats(conn: sqlite3.Connection) -> tuple[dict[int, dict], dict[int, dict], dict[int, dict]]:
    committee_info: dict[int, dict] = {}
    committee_receipts: dict[int, dict] = {}
    committee_expenditures: dict[int, dict] = {}

    for row in conn.execute(
        """
        SELECT committee_id_sbe, committee_name, committee_type, address_line_1, address_line_2,
               city, state, postal_code
        FROM bulk_committees_clean
        WHERE committee_id_sbe IS NOT NULL
        """
    ):
        committee_info[int(row["committee_id_sbe"])] = {
            "name": row["committee_name"] or "",
            "type": row["committee_type"] or "",
            "address": _join_address(row["address_line_1"], row["address_line_2"]),
            "city": row["city"],
            "state": row["state"],
            "postal_code": row["postal_code"],
        }

    for row in conn.execute(
        """
        SELECT committee_id_sbe, MIN(received_date) AS min_date, MAX(received_date) AS max_date,
               SUM(amount) AS total_amount
        FROM bulk_receipts_clean
        WHERE amount > 0
        GROUP BY committee_id_sbe
        """
    ):
        committee_receipts[int(row["committee_id_sbe"])] = {
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "total_amount": _safe_float(row["total_amount"]),
        }

    for row in conn.execute(
        """
        SELECT committee_id_sbe, MIN(expended_date) AS min_date, MAX(expended_date) AS max_date,
               SUM(amount) AS total_amount
        FROM bulk_expenditures_clean
        WHERE amount > 0 AND (is_amount_anomalous = 0 OR is_amount_anomalous IS NULL)
        GROUP BY committee_id_sbe
        """
    ):
        committee_expenditures[int(row["committee_id_sbe"])] = {
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "total_amount": _safe_float(row["total_amount"]),
        }

    return committee_info, committee_receipts, committee_expenditures


def collect_committee_transfers(conn: sqlite3.Connection) -> dict[int, dict]:
    transfers: dict[int, dict] = {}
    for row in conn.execute(
        """
        SELECT committee_id_sbe,
               SUM(COALESCE(transfers_in_itemized,0) + COALESCE(transfers_in_non_itemized,0)) AS transfers_in,
               SUM(COALESCE(transfers_out_itemized,0) + COALESCE(transfers_out_non_itemized,0)) AS transfers_out
        FROM bulk_d2_totals_clean
        GROUP BY committee_id_sbe
        """
    ):
        transfers[int(row["committee_id_sbe"])] = {
            "transfers_in": _safe_float(row["transfers_in"]),
            "transfers_out": _safe_float(row["transfers_out"]),
        }
    return transfers


def collect_donor_entity_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    stats: dict[str, dict] = {}

    for row in conn.execute(
        """
        SELECT entity_id, canonical_name, display_name, total_amount
        FROM donor_entity_local
        WHERE source = 'bulk_receipts'
        """
    ):
        stats[row["entity_id"]] = {
            "canonical_name": row["canonical_name"],
            "display_name": row["display_name"],
            "total_amount": _safe_float(row["total_amount"]),
            "min_date": None,
            "max_date": None,
        }

    donor_key_expr = (
        "LOWER(TRIM("
        "COALESCE(first_name, '') || '|' || COALESCE(last_or_business_name, '') || '|' || "
        "COALESCE(address_line_1, '') || '|' || COALESCE(address_line_2, '') || '|' || "
        "COALESCE(city, '') || '|' || COALESCE(state, '') || '|' || COALESCE(postal_code, '')"
        "))"
    )

    query = f"""
        WITH donor_receipts AS (
            SELECT
                {donor_key_expr} AS donor_key,
                MIN(received_date) AS min_date,
                MAX(received_date) AS max_date
            FROM bulk_receipts_clean
            WHERE amount > 0 AND received_date IS NOT NULL
            GROUP BY donor_key
        )
        SELECT m.entity_id, MIN(dr.min_date) AS min_date, MAX(dr.max_date) AS max_date
        FROM donor_entity_local_member m
        JOIN donor_receipts dr ON dr.donor_key = m.donor_key
        WHERE m.source = 'bulk_receipts'
        GROUP BY m.entity_id
    """

    for row in conn.execute(query):
        entry = stats.setdefault(
            row["entity_id"],
            {
                "canonical_name": None,
                "display_name": None,
                "total_amount": 0.0,
                "min_date": None,
                "max_date": None,
            },
        )
        entry["min_date"] = row["min_date"]
        entry["max_date"] = row["max_date"]

    return stats


def _candidate_pairs_for_sources(
    profiles: Dict[str, EntityProfile],
    allowed_sources: Iterable[str],
) -> List[Tuple[str, str]]:
    allowed = set(allowed_sources)
    blocks: Dict[str, List[str]] = defaultdict(list)
    for record_id, profile in profiles.items():
        if profile.source not in allowed:
            continue
        for key in blocking_keys(profile):
            blocks[key].append(record_id)

    pairs = set()
    for ids in blocks.values():
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                left_id, right_id = ids[i], ids[j]
                if left_id == right_id:
                    continue
                pairs.add((left_id, right_id))
    return list(pairs)


def _ensure_profile(
    profiles: Dict[str, EntityProfile],
    meta: Dict[str, dict],
    record_id: str,
    source: str,
    name: str,
    identifiers: Dict[str, str],
) -> None:
    if record_id in profiles:
        return
    profiles[record_id] = EntityProfile(
        record_id=record_id,
        source=source,
        name=name,
        identifiers=identifiers,
    )
    meta[record_id] = {
        "entity_type": source,
        "source_table": "derived",
        "source_id": record_id,
    }


def load_match_evidence(
    conn: sqlite3.Connection,
    profiles: Dict[str, EntityProfile],
    meta: Dict[str, dict],
    donor_key_to_entity: Dict[str, str],
    resolver: EntityResolver,
) -> Dict[str, int]:
    counts: Dict[str, int] = Counter()

    rows = conn.execute("SELECT client_id, client_name, ein, org_name, score FROM lobbying_527_matches")
    for row in rows:
        left_id = f"lobby_client:{row['client_id']}"
        right_id = f"irs527:{row['ein']}"
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["score"] or 0.0),
                "lobbying_527_matches",
                {"client_name": row["client_name"], "org_name": row["org_name"]},
            )
            counts["lobbying_527_matches"] += 1

    rows = conn.execute(
        "SELECT ein, org_name, committee_id_sbe, committee_name, score FROM irs527_committee_matches"
    )
    for row in rows:
        left_id = f"irs527:{row['ein']}"
        right_id = f"committee:{row['committee_id_sbe']}"
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["score"] or 0.0),
                "irs527_committee_matches",
                {"org_name": row["org_name"], "committee_name": row["committee_name"]},
            )
            counts["irs527_committee_matches"] += 1

    rows = conn.execute(
        "SELECT client_id, client_name, donor_key, donor_name, score FROM lobbying_donor_matches"
    )
    for row in rows:
        entity_id = donor_key_to_entity.get(row["donor_key"])
        if entity_id:
            right_id = f"donor_entity:{entity_id}"
        else:
            donor_key = row["donor_key"]
            right_id = f"donor_key:{donor_key}"
            _ensure_profile(
                profiles,
                meta,
                right_id,
                "donor_key",
                row["donor_name"] or donor_key or "",
                {"donor_key": donor_key or ""},
            )
        left_id = f"lobby_client:{row['client_id']}"
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["score"] or 0.0),
                "lobbying_donor_matches",
                {"client_name": row["client_name"], "donor_name": row["donor_name"], "donor_key": row["donor_key"]},
            )
            counts["lobbying_donor_matches"] += 1

    rows = conn.execute(
        "SELECT ein, org_name, director_name, donor_key, donor_name, score FROM irs527_director_donor_matches"
    )
    for row in rows:
        left_id = f"irs527:{row['ein']}"
        entity_id = donor_key_to_entity.get(row["donor_key"])
        if entity_id:
            right_id = f"donor_entity:{entity_id}"
        else:
            donor_key = row["donor_key"]
            right_id = f"donor_key:{donor_key}"
            _ensure_profile(
                profiles,
                meta,
                right_id,
                "donor_key",
                row["donor_name"] or donor_key or "",
                {"donor_key": donor_key or ""},
            )
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["score"] or 0.0),
                "irs527_director_donor_matches",
                {
                    "org_name": row["org_name"],
                    "director_name": row["director_name"],
                    "donor_name": row["donor_name"],
                    "donor_key": row["donor_key"],
                },
            )
            counts["irs527_director_donor_matches"] += 1

    rows = conn.execute(
        """
        SELECT ein, org_name, matched_entity_type, matched_entity_id, matched_entity_name, address_score
        FROM irs527_org_address_matches
        """
    )
    for row in rows:
        left_id = f"irs527:{row['ein']}"
        if row["matched_entity_type"] == "committee":
            right_id = f"committee:{row['matched_entity_id']}"
        elif row["matched_entity_type"] == "donor":
            right_id = f"donor_entity:{row['matched_entity_id']}"
        else:
            continue
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["address_score"] or 0.0),
                "irs527_org_address_matches",
                {
                    "org_name": row["org_name"],
                    "matched_name": row["matched_entity_name"],
                    "matched_entity_type": row["matched_entity_type"],
                },
            )
            counts["irs527_org_address_matches"] += 1

    rows = conn.execute(
        """
        SELECT ein, org_name, recipient_name, matched_type, matched_id, matched_name, score
        FROM irs527_expenditure_recipient_matches
        """
    )
    for row in rows:
        left_id = f"irs527:{row['ein']}"
        if row["matched_type"] != "committee":
            continue
        right_id = f"committee:{row['matched_id']}"
        if left_id in profiles and right_id in profiles:
            resolver.add_match(
                left_id,
                right_id,
                float(row["score"] or 0.0),
                "irs527_expenditure_recipient_matches",
                {"org_name": row["org_name"], "matched_name": row["matched_name"]},
            )
            counts["irs527_expenditure_recipient_matches"] += 1

    return counts


def add_fuzzy_matches(
    resolver: EntityResolver,
    profiles: Dict[str, EntityProfile],
    config: ResolverConfig,
) -> int:
    source_pairs = {
        ("lobby_client", "irs527"),
        ("lobby_client", "committee"),
        ("lobby_client", "donor_entity"),
        ("lobby_entity", "irs527"),
        ("lobby_entity", "committee"),
        ("irs527", "committee"),
        ("irs527", "donor_entity"),
    }
    allowed_sources = {s for pair in source_pairs for s in pair}
    pairs = _candidate_pairs_for_sources(profiles, allowed_sources)
    count = 0

    for left_id, right_id in pairs:
        left = profiles[left_id]
        right = profiles[right_id]
        if left.source == right.source:
            continue
        if (left.source, right.source) not in source_pairs and (right.source, left.source) not in source_pairs:
            continue
        score, parts = compute_match_score(left, right, config)
        if score >= config.review_threshold:
            resolver.add_match(
                left_id,
                right_id,
                score,
                "fuzzy_name_address",
                {
                    "name_score": f"{parts['name_score']:.3f}",
                    "address_score": f"{parts['address_score']:.3f}",
                    "officer_score": f"{parts['officer_score']:.3f}",
                },
            )
            count += 1
    return count


def build_resolver(
    conn: sqlite3.Connection,
    config: ResolverConfig,
    enable_fuzzy: bool = False,
) -> Tuple[EntityResolver, Dict[str, EntityProfile], Dict[str, dict], Dict[str, int]]:
    profiles: Dict[str, EntityProfile] = {}
    meta: Dict[str, dict] = {}

    lobby_profiles, lobby_meta = load_lobby_profiles(conn)
    profiles.update(lobby_profiles)
    meta.update(lobby_meta)

    irs_profiles, irs_meta = load_irs527_profiles(conn)
    profiles.update(irs_profiles)
    meta.update(irs_meta)

    committee_profiles, committee_meta = load_committee_profiles(conn)
    profiles.update(committee_profiles)
    meta.update(committee_meta)

    donor_profiles, donor_meta, donor_key_to_entity = load_donor_profiles(conn)
    profiles.update(donor_profiles)
    meta.update(donor_meta)

    resolver = EntityResolver(config=config)
    for profile in profiles.values():
        resolver.add_profile(profile)

    evidence_counts = load_match_evidence(conn, profiles, meta, donor_key_to_entity, resolver)

    if enable_fuzzy:
        evidence_counts["fuzzy_matches"] = add_fuzzy_matches(resolver, profiles, config)

    resolver.resolve()
    return resolver, profiles, meta, evidence_counts


def _cluster_map(resolver: EntityResolver) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record_id in resolver.profiles:
        mapping[record_id] = resolver.uf.find(record_id)
    return mapping


def _format_address(address: Optional[str], city: Optional[str], state: Optional[str], postal_code: Optional[str]) -> Optional[str]:
    parts = [p for p in [address, city, state, postal_code] if p and str(p).strip()]
    return ", ".join(str(p).strip() for p in parts) if parts else None


def _maybe_append(target_set: set, value: Optional[str]) -> None:
    if value:
        cleaned = str(value).strip()
        if cleaned:
            target_set.add(cleaned)


def export_entities_csv(
    out_path: str,
    resolver: EntityResolver,
    cluster_map: dict[str, str],
    committee_info: dict[int, dict],
    committee_receipts: dict[int, dict],
    committee_expenditures: dict[int, dict],
    committee_transfers: dict[int, dict],
    donor_stats: dict[str, dict],
    irs_stats: dict[str, dict],
    lobby_client_stats: dict[int, dict],
    lobby_entity_stats: dict[int, dict],
) -> tuple[list[dict], dict[str, dict]]:
    clusters = resolver.clusters()
    entity_rows: list[dict] = []
    entity_lookup: dict[str, dict] = {}

    for cluster_id, profiles in clusters.items():
        lobby_names: set = set()
        committee_names: set = set()
        donor_names: set = set()
        pac_names: set = set()
        org_527_names: set = set()
        addresses: set = set()
        officers: set = set()
        eins: set = set()

        channel_lobby = 0
        channel_527 = 0
        channel_campaign = 0
        evidence_lobby = 0
        evidence_527 = 0
        evidence_campaign = 0

        date_candidates: list[str] = []

        for profile in profiles:
            if profile.source in {"lobby_client", "lobby_entity"}:
                channel_lobby = 1
                evidence_lobby += 1
                _maybe_append(lobby_names, profile.name)
                if profile.source == "lobby_client":
                    client_id = int(profile.identifiers.get("lobby_client_id", "0") or 0)
                    stats = lobby_client_stats.get(client_id)
                    if stats:
                        if stats.get("min_year"):
                            date_candidates.append(f"{stats['min_year']}-01-01")
                        if stats.get("max_year"):
                            date_candidates.append(f"{stats['max_year']}-12-31")
                if profile.source == "lobby_entity":
                    entity_id = int(profile.identifiers.get("lobby_entity_id", "0") or 0)
                    stats = lobby_entity_stats.get(entity_id)
                    if stats:
                        if stats.get("min_year"):
                            date_candidates.append(f"{stats['min_year']}-01-01")
                        if stats.get("max_year"):
                            date_candidates.append(f"{stats['max_year']}-12-31")

            elif profile.source == "irs527":
                channel_527 = 1
                evidence_527 += 1
                _maybe_append(org_527_names, profile.name)
                eins.add(profile.identifiers.get("ein", ""))
                for officer in profile.officers:
                    _maybe_append(officers, officer)
                addr = _format_address(profile.address, profile.city, profile.state, profile.postal_code)
                _maybe_append(addresses, addr)
                ein = profile.identifiers.get("ein")
                if ein and ein in irs_stats:
                    stats = irs_stats[ein]
                    date_candidates.append(stats.get("min_date"))
                    date_candidates.append(stats.get("max_date"))

            elif profile.source == "committee":
                channel_campaign = 1
                evidence_campaign += 1
                committee_id = int(profile.identifiers.get("committee_id_sbe", "0") or 0)
                info = committee_info.get(committee_id, {})
                committee_name = info.get("name") or profile.name
                _maybe_append(committee_names, committee_name)
                if info.get("type") and "PAC" in str(info.get("type")).upper():
                    _maybe_append(pac_names, committee_name)
                elif " PAC" in committee_name.upper() or committee_name.upper().endswith("PAC"):
                    _maybe_append(pac_names, committee_name)
                addr = _format_address(info.get("address"), info.get("city"), info.get("state"), info.get("postal_code"))
                _maybe_append(addresses, addr)
                receipts = committee_receipts.get(committee_id)
                expenditures = committee_expenditures.get(committee_id)
                if receipts:
                    date_candidates.append(receipts.get("min_date"))
                    date_candidates.append(receipts.get("max_date"))
                if expenditures:
                    date_candidates.append(expenditures.get("min_date"))
                    date_candidates.append(expenditures.get("max_date"))

            elif profile.source in {"donor_entity", "donor_key"}:
                channel_campaign = 1
                evidence_campaign += 1
                _maybe_append(donor_names, profile.name)
                addr = _format_address(profile.address, profile.city, profile.state, profile.postal_code)
                _maybe_append(addresses, addr)
                if profile.source == "donor_entity":
                    donor_id = profile.identifiers.get("donor_entity_id")
                    if donor_id and donor_id in donor_stats:
                        date_candidates.append(donor_stats[donor_id].get("min_date"))
                        date_candidates.append(donor_stats[donor_id].get("max_date"))

        canonical_name = resolver.canonical_name(profiles)
        entity_type = "multi"
        sources = {profile.source for profile in profiles}
        if sources <= {"donor_entity", "donor_key"}:
            entity_type = _normalize_org_type(canonical_name)
        elif sources & {"committee"} and len(sources) == 1:
            entity_type = "committee"
        elif sources & {"irs527"} and len(sources) == 1:
            entity_type = "irs527"
        elif sources & {"lobby_client", "lobby_entity"} and len(sources) == 1:
            entity_type = "lobbying"
        elif len(sources) > 1:
            entity_type = "multi"

        first_seen = _date_to_iso(_min_date(date_candidates))
        last_seen = _date_to_iso(_max_date(date_candidates))

        row = {
            "entity_id": cluster_id,
            "canonical_name": canonical_name,
            "entity_type": entity_type,
            "confidence_score": f"{resolver.cluster_confidence([p.record_id for p in profiles]):.4f}",
            "lobby_names[]": json.dumps(sorted(lobby_names), ensure_ascii=True),
            "pac_names[]": json.dumps(sorted(pac_names), ensure_ascii=True),
            "committee_names[]": json.dumps(sorted(committee_names), ensure_ascii=True),
            "donor_names[]": json.dumps(sorted(donor_names), ensure_ascii=True),
            "527_names[]": json.dumps(sorted(org_527_names), ensure_ascii=True),
            "addresses[]": json.dumps(sorted(addresses), ensure_ascii=True),
            "officers[]": json.dumps(sorted(officers), ensure_ascii=True),
            "ein": ";".join(sorted(e for e in eins if e)),
            "first_seen_date": first_seen or "",
            "last_seen_date": last_seen or "",
            "channel_lobby": channel_lobby,
            "channel_527": channel_527,
            "channel_campaign": channel_campaign,
            "evidence_count_lobby": evidence_lobby,
            "evidence_count_527": evidence_527,
            "evidence_count_campaign": evidence_campaign,
        }

        entity_rows.append(row)
        entity_lookup[cluster_id] = row

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = [
        "entity_id",
        "canonical_name",
        "entity_type",
        "confidence_score",
        "lobby_names[]",
        "pac_names[]",
        "committee_names[]",
        "donor_names[]",
        "527_names[]",
        "addresses[]",
        "officers[]",
        "ein",
        "first_seen_date",
        "last_seen_date",
        "channel_lobby",
        "channel_527",
        "channel_campaign",
        "evidence_count_lobby",
        "evidence_count_527",
        "evidence_count_campaign",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in entity_rows:
            writer.writerow(row)

    return entity_rows, entity_lookup


def export_top_entities_csv(
    out_path: str,
    entity_rows: list[dict],
    committee_receipts: dict[int, dict],
    committee_expenditures: dict[int, dict],
    committee_transfers: dict[int, dict],
    donor_stats: dict[str, dict],
    irs_stats: dict[str, dict],
    cluster_profiles: dict[str, list[EntityProfile]],
) -> None:
    rows: list[dict] = []

    for row in entity_rows:
        cluster_id = row["entity_id"]
        profiles = cluster_profiles.get(cluster_id, [])
        committee_ids = []
        donor_ids = []
        eins = []
        lobby_counts = 0

        for profile in profiles:
            if profile.source == "committee":
                committee_ids.append(int(profile.identifiers.get("committee_id_sbe", "0") or 0))
            elif profile.source == "donor_entity":
                donor_ids.append(profile.identifiers.get("donor_entity_id"))
            elif profile.source == "irs527":
                eins.append(profile.identifiers.get("ein"))
            elif profile.source in {"lobby_client", "lobby_entity"}:
                lobby_counts += 1

        total_lobby_reports = lobby_counts
        total_527_amount = sum(irs_stats.get(ein, {}).get("total_expenditures", 0.0) for ein in eins if ein)
        total_campaign_amount = 0.0
        total_transfers_in = 0.0
        total_transfers_out = 0.0

        for committee_id in committee_ids:
            receipts = committee_receipts.get(committee_id, {})
            expenditures = committee_expenditures.get(committee_id, {})
            total_campaign_amount += _safe_float(receipts.get("total_amount"))
            total_campaign_amount += _safe_float(expenditures.get("total_amount"))
            transfers = committee_transfers.get(committee_id, {})
            total_transfers_in += _safe_float(transfers.get("transfers_in"))
            total_transfers_out += _safe_float(transfers.get("transfers_out"))

        for donor_id in donor_ids:
            if donor_id and donor_id in donor_stats:
                total_campaign_amount += _safe_float(donor_stats[donor_id].get("total_amount"))

        channels_count = int(row["channel_lobby"]) + int(row["channel_527"]) + int(row["channel_campaign"])
        persistence_months = _months_between(row.get("first_seen_date"), row.get("last_seen_date"))
        total_money = total_campaign_amount + total_527_amount

        rows.append(
            {
                "canonical_name": row["canonical_name"],
                "channels_count": channels_count,
                "confidence_score": row["confidence_score"],
                "total_lobby_reports": int(total_lobby_reports),
                "total_527_amount": round(total_527_amount, 2),
                "total_campaign_amount": round(total_campaign_amount, 2),
                "total_transfers_in": round(total_transfers_in, 2),
                "total_transfers_out": round(total_transfers_out, 2),
                "persistence_months": persistence_months,
                "tri_channel_premium_metrics": json.dumps({"pending": True}, ensure_ascii=True),
                "_sort_money": total_money,
            }
        )

    rows.sort(
        key=lambda r: (
            -r["channels_count"],
            -r["_sort_money"],
            -r["persistence_months"],
        )
    )

    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
        row.pop("_sort_money", None)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = [
        "rank",
        "canonical_name",
        "channels_count",
        "confidence_score",
        "total_lobby_reports",
        "total_527_amount",
        "total_campaign_amount",
        "total_transfers_in",
        "total_transfers_out",
        "persistence_months",
        "tri_channel_premium_metrics",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_edges_csv(
    out_path: str,
    conn: sqlite3.Connection,
    cluster_map: dict[str, str],
    entity_lookup: dict[str, dict],
    tri_clusters: set[str],
) -> None:
    edge_rows: list[dict] = []
    edge_accumulator: dict[tuple, dict] = {}

    def add_edge(source_id: str, target_id: str, edge_type: str, weight: float, unit: str, table: str, ref: str):
        if source_id == target_id:
            return
        if tri_clusters and source_id not in tri_clusters and target_id not in tri_clusters:
            return
        key = (source_id, target_id, edge_type, unit, table)
        entry = edge_accumulator.get(key)
        if not entry:
            entry = {
                "source_entity_id": source_id,
                "source_name": entity_lookup.get(source_id, {}).get("canonical_name", ""),
                "target_entity_id": target_id,
                "target_name": entity_lookup.get(target_id, {}).get("canonical_name", ""),
                "edge_type": edge_type,
                "weight": 0.0,
                "weight_unit": unit,
                "evidence_table": table,
                "evidence_ref": ref,
            }
            edge_accumulator[key] = entry
        entry["weight"] += weight

    for row in conn.execute("SELECT entity_id, client_id, reg_year FROM lobbying_entity_clients"):
        source_record = f"lobby_entity:{row['entity_id']}"
        target_record = f"lobby_client:{row['client_id']}"
        source_cluster = cluster_map.get(source_record)
        target_cluster = cluster_map.get(target_record)
        if not source_cluster or not target_cluster:
            continue
        ref = f"{row['entity_id']}:{row['client_id']}:{row['reg_year']}" if row["reg_year"] else f"{row['entity_id']}:{row['client_id']}"
        add_edge(source_cluster, target_cluster, "lobby_entity_client", 1.0, "count", "lobbying_entity_clients", ref)

    for row in conn.execute(
        "SELECT client_id, ein, score FROM lobbying_527_matches"
    ):
        source_record = f"lobby_client:{row['client_id']}"
        target_record = f"irs527:{row['ein']}"
        source_cluster = cluster_map.get(source_record)
        target_cluster = cluster_map.get(target_record)
        if not source_cluster or not target_cluster:
            continue
        ref = f"{row['client_id']}:{row['ein']}"
        add_edge(source_cluster, target_cluster, "lobby_client_527_match", _safe_float(row["score"]), "score", "lobbying_527_matches", ref)

    for row in conn.execute(
        "SELECT ein, committee_id_sbe, score FROM irs527_committee_matches"
    ):
        source_record = f"irs527:{row['ein']}"
        target_record = f"committee:{row['committee_id_sbe']}"
        source_cluster = cluster_map.get(source_record)
        target_cluster = cluster_map.get(target_record)
        if not source_cluster or not target_cluster:
            continue
        ref = f"{row['ein']}:{row['committee_id_sbe']}"
        add_edge(source_cluster, target_cluster, "irs527_committee_match", _safe_float(row["score"]), "score", "irs527_committee_matches", ref)

    for row in conn.execute(
        """
        SELECT m.ein, m.matched_id AS committee_id, SUM(e.amount) AS total_amount
        FROM irs527_expenditure_recipient_matches m
        JOIN irs527_expenditures e ON e.ein = m.ein AND e.recipient_name = m.recipient_name
        WHERE m.matched_type = 'committee'
        GROUP BY m.ein, m.matched_id
        """
    ):
        source_record = f"irs527:{row['ein']}"
        target_record = f"committee:{row['committee_id']}"
        source_cluster = cluster_map.get(source_record)
        target_cluster = cluster_map.get(target_record)
        if not source_cluster or not target_cluster:
            continue
        ref = f"{row['ein']}:{row['committee_id']}"
        add_edge(
            source_cluster,
            target_cluster,
            "irs527_expenditure_committee",
            _safe_float(row["total_amount"]),
            "usd",
            "irs527_expenditure_recipient_matches",
            ref,
        )

    for row in conn.execute(
        """
        SELECT m.entity_id AS donor_entity_id, a.committee_id, SUM(a.total_amount) AS total_amount
        FROM analytics_donor_committee_agg a
        JOIN donor_entity_local_member m ON m.donor_key = a.donor_key
        WHERE a.source = 'bulk_receipts' AND m.source = 'bulk_receipts'
        GROUP BY m.entity_id, a.committee_id
        """
    ):
        donor_id = row["donor_entity_id"]
        source_record = f"donor_entity:{donor_id}"
        target_record = f"committee:{row['committee_id']}"
        source_cluster = cluster_map.get(source_record)
        target_cluster = cluster_map.get(target_record)
        if not source_cluster or not target_cluster:
            continue
        ref = f"{donor_id}:{row['committee_id']}"
        add_edge(
            source_cluster,
            target_cluster,
            "donor_committee",
            _safe_float(row["total_amount"]),
            "usd",
            "analytics_donor_committee_agg",
            ref,
        )

    edge_rows = list(edge_accumulator.values())
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = [
        "source_entity_id",
        "source_name",
        "target_entity_id",
        "target_name",
        "edge_type",
        "weight",
        "weight_unit",
        "evidence_table",
        "evidence_ref",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in edge_rows:
            writer.writerow(row)


def export_timeline_csv(
    out_path: str,
    conn: sqlite3.Connection,
    cluster_map: dict[str, str],
    entity_lookup: dict[str, dict],
    tri_clusters: set[str],
) -> None:
    timeline: dict[tuple[str, str], dict] = {}

    def add_metric(cluster_id: str, month_key: str, field: str, value: float):
        if not cluster_id or not month_key:
            return
        if tri_clusters and cluster_id not in tri_clusters:
            return
        key = (cluster_id, month_key)
        entry = timeline.get(key)
        if not entry:
            entry = {
                "entity_id": cluster_id,
                "canonical_name": entity_lookup.get(cluster_id, {}).get("canonical_name", ""),
                "month_key": month_key,
                "lobbying_reports": 0,
                "campaign_receipts": 0.0,
                "campaign_expenditures": 0.0,
                "transfers_in": 0.0,
                "transfers_out": 0.0,
                "irs527_expenditures": 0.0,
                "irs527_contributions": 0.0,
            }
            timeline[key] = entry
        entry[field] += value

    for row in conn.execute(
        """
        SELECT client_id, reg_year, COUNT(*) AS cnt
        FROM lobbying_entity_clients
        GROUP BY client_id, reg_year
        """
    ):
        if not row["reg_year"]:
            continue
        source_record = f"lobby_client:{row['client_id']}"
        cluster_id = cluster_map.get(source_record)
        month_key = f"{row['reg_year']}-01"
        add_metric(cluster_id, month_key, "lobbying_reports", float(row["cnt"] or 0))

    for row in conn.execute(
        """
        SELECT entity_id, reg_year, COUNT(*) AS cnt
        FROM lobbying_entity_clients
        GROUP BY entity_id, reg_year
        """
    ):
        if not row["reg_year"]:
            continue
        source_record = f"lobby_entity:{row['entity_id']}"
        cluster_id = cluster_map.get(source_record)
        month_key = f"{row['reg_year']}-01"
        add_metric(cluster_id, month_key, "lobbying_reports", float(row["cnt"] or 0))

    for row in conn.execute(
        """
        SELECT committee_id_sbe, SUBSTR(received_date, 1, 7) AS month_key, SUM(amount) AS total
        FROM bulk_receipts_clean
        WHERE amount > 0 AND received_date IS NOT NULL AND LENGTH(received_date) >= 7
        GROUP BY committee_id_sbe, month_key
        """
    ):
        source_record = f"committee:{row['committee_id_sbe']}"
        cluster_id = cluster_map.get(source_record)
        add_metric(cluster_id, row["month_key"], "campaign_receipts", _safe_float(row["total"]))

    for row in conn.execute(
        """
        SELECT committee_id_sbe, SUBSTR(expended_date, 1, 7) AS month_key, SUM(amount) AS total
        FROM bulk_expenditures_clean
        WHERE amount > 0 AND (is_amount_anomalous = 0 OR is_amount_anomalous IS NULL)
          AND expended_date IS NOT NULL AND LENGTH(expended_date) >= 7
        GROUP BY committee_id_sbe, month_key
        """
    ):
        source_record = f"committee:{row['committee_id_sbe']}"
        cluster_id = cluster_map.get(source_record)
        add_metric(cluster_id, row["month_key"], "campaign_expenditures", _safe_float(row["total"]))

    for row in conn.execute(
        """
        SELECT committee_id_sbe, SUBSTR(expended_date, 1, 7) AS month_key, SUM(amount) AS total
        FROM bulk_expenditures_clean
        WHERE amount > 0 AND expended_date IS NOT NULL AND LENGTH(expended_date) >= 7
          AND (purpose LIKE '%Contribution%' OR purpose LIKE '%contribution%' OR purpose LIKE '%CONTRIBUTION%')
        GROUP BY committee_id_sbe, month_key
        """
    ):
        source_record = f"committee:{row['committee_id_sbe']}"
        cluster_id = cluster_map.get(source_record)
        add_metric(cluster_id, row["month_key"], "transfers_out", _safe_float(row["total"]))

    for row in conn.execute(
        """
        SELECT committee_id_sbe, SUBSTR(received_date, 1, 7) AS month_key, SUM(amount) AS total
        FROM bulk_receipts_clean
        WHERE amount > 0 AND received_date IS NOT NULL AND LENGTH(received_date) >= 7
          AND (description LIKE '%Transfer%' OR description LIKE '%TRANSFER%')
        GROUP BY committee_id_sbe, month_key
        """
    ):
        source_record = f"committee:{row['committee_id_sbe']}"
        cluster_id = cluster_map.get(source_record)
        add_metric(cluster_id, row["month_key"], "transfers_in", _safe_float(row["total"]))

    for row in conn.execute(
        """
        SELECT ein, SUBSTR(date, 1, 6) AS month_key, SUM(amount) AS total
        FROM irs527_expenditures
        WHERE date IS NOT NULL
          AND LENGTH(date) >= 8
          AND date GLOB '[0-9][0-9][0-9][0-9]*'
          AND CAST(SUBSTR(date, 1, 4) AS INTEGER) BETWEEN 1900 AND 2100
        GROUP BY ein, month_key
        """
    ):
        source_record = f"irs527:{row['ein']}"
        cluster_id = cluster_map.get(source_record)
        month_key = _month_key(row["month_key"])
        add_metric(cluster_id, month_key, "irs527_expenditures", _safe_float(row["total"]))

    for row in conn.execute(
        """
        SELECT ein, SUBSTR(date, 1, 6) AS month_key, SUM(amount) AS total
        FROM irs527_contributions
        WHERE date IS NOT NULL
          AND LENGTH(date) >= 8
          AND date GLOB '[0-9][0-9][0-9][0-9]*'
          AND CAST(SUBSTR(date, 1, 4) AS INTEGER) BETWEEN 1900 AND 2100
        GROUP BY ein, month_key
        """
    ):
        source_record = f"irs527:{row['ein']}"
        cluster_id = cluster_map.get(source_record)
        month_key = _month_key(row["month_key"])
        add_metric(cluster_id, month_key, "irs527_contributions", _safe_float(row["total"]))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = [
        "entity_id",
        "canonical_name",
        "month_key",
        "lobbying_reports",
        "campaign_receipts",
        "campaign_expenditures",
        "transfers_in",
        "transfers_out",
        "irs527_expenditures",
        "irs527_contributions",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(timeline.values(), key=lambda r: (r["entity_id"], r["month_key"])):
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build entity resolution layer for triple-channel pipeline.")
    parser.add_argument("--db", default=os.path.join("data", "campaign_finance.db"))
    parser.add_argument("--enable-fuzzy", action="store_true", help="Enable fuzzy cross-channel matching.")
    parser.add_argument("--auto-merge-threshold", type=float, default=0.92)
    parser.add_argument("--review-threshold", type=float, default=0.85)
    parser.add_argument("--summary", action="store_true", help="Print summary counts.")
    parser.add_argument("--export", action="store_true", help="Write CSV exports for Step 3 outputs.")
    parser.add_argument("--output-dir", default=os.path.join("data", "exports"))
    args = parser.parse_args()

    config = ResolverConfig(
        auto_merge_threshold=args.auto_merge_threshold,
        review_threshold=args.review_threshold,
    )

    conn = _connect(args.db)
    resolver, profiles, _, evidence_counts = build_resolver(conn, config, enable_fuzzy=args.enable_fuzzy)

    if args.summary:
        source_counts = Counter(profile.source for profile in profiles.values())
        print("Profile counts by source:")
        for source, count in sorted(source_counts.items()):
            print(f"  {source}: {count}")
        print("\nEvidence counts:")
        for table, count in sorted(evidence_counts.items()):
            print(f"  {table}: {count}")
        print(f"\nResolved clusters: {len(resolver.clusters())}")

    if args.export:
        cluster_map = _cluster_map(resolver)
        committee_info, committee_receipts, committee_expenditures = collect_committee_stats(conn)
        committee_transfers = collect_committee_transfers(conn)
        donor_stats = collect_donor_entity_stats(conn)
        irs_stats = collect_irs527_stats(conn)
        lobby_client_stats, lobby_entity_stats = collect_lobbying_stats(conn)

        entity_csv = os.path.join(args.output_dir, "triple_pipeline_entities.csv")
        top_csv = os.path.join(args.output_dir, "triple_pipeline_top_entities.csv")
        edges_csv = os.path.join(args.output_dir, "triple_pipeline_edges.csv")
        timeline_csv = os.path.join(args.output_dir, "triple_pipeline_timeline.csv")

        entity_rows, entity_lookup = export_entities_csv(
            entity_csv,
            resolver,
            cluster_map,
            committee_info,
            committee_receipts,
            committee_expenditures,
            committee_transfers,
            donor_stats,
            irs_stats,
            lobby_client_stats,
            lobby_entity_stats,
        )
        export_top_entities_csv(
            top_csv,
            entity_rows,
            committee_receipts,
            committee_expenditures,
            committee_transfers,
            donor_stats,
            irs_stats,
            resolver.clusters(),
        )

        tri_clusters = {
            row["entity_id"]
            for row in entity_rows
            if int(row["channel_lobby"]) == 1 and int(row["channel_527"]) == 1 and int(row["channel_campaign"]) == 1
        }
        export_edges_csv(edges_csv, conn, cluster_map, entity_lookup, tri_clusters)
        export_timeline_csv(timeline_csv, conn, cluster_map, entity_lookup, tri_clusters)

    conn.close()


if __name__ == "__main__":
    main()
