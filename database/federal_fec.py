"""FEC federal candidate ingestion and query helpers.

This module focuses on:
- Loading Illinois federal candidate seeds from a CSV file.
- Resolving FEC candidate IDs using /v1/candidates/search/.
- Pulling Schedule A contribution details for matched candidate committees.
- Persisting raw endpoint payloads in raw_extractions for reproducibility.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
import sqlite3
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PARSER_VERSION = "fec_sync_v1"


def _is_postgres_connection(conn: sqlite3.Connection) -> bool:
    return conn.__class__.__name__ == "PostgresCompatConnection"


def _distinct_concat_aggregate_sql(conn: sqlite3.Connection, expression_sql: str) -> str:
    if _is_postgres_connection(conn):
        return f"STRING_AGG(DISTINCT {expression_sql}, ',')"
    return f"GROUP_CONCAT(DISTINCT {expression_sql})"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _federal_view_cache_key(snapshot_type: str, params: dict) -> str:
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"{snapshot_type}:{digest}"


def get_federal_view_snapshot(
    conn: sqlite3.Connection,
    snapshot_type: str,
    params: dict,
    ttl_seconds: int = 600,
) -> dict:
    """Fetch cached payload metadata for heavy federal views."""
    cache_key = _federal_view_cache_key(snapshot_type, params)
    if not _table_exists(conn, "analytics_snapshots"):
        return {
            "cache_key": cache_key,
            "status": "empty",
            "payload": None,
            "error_message": None,
            "completed_at": None,
            "updated_at": None,
            "age_seconds": None,
            "is_stale": True,
            "is_fresh": False,
        }

    row = conn.execute(
        """
        SELECT cache_key, status, payload_json, error_message, completed_at, updated_at
        FROM analytics_snapshots
        WHERE cache_key = ? AND snapshot_type = ?
        LIMIT 1
        """,
        (cache_key, snapshot_type),
    ).fetchone()
    if not row:
        return {
            "cache_key": cache_key,
            "status": "empty",
            "payload": None,
            "error_message": None,
            "completed_at": None,
            "updated_at": None,
            "age_seconds": None,
            "is_stale": True,
            "is_fresh": False,
        }

    payload = json.loads(row["payload_json"]) if row["payload_json"] else None
    completed_at = row["completed_at"]
    age_seconds = None
    is_fresh = False
    if completed_at:
        try:
            if isinstance(completed_at, datetime):
                completed_dt = (
                    completed_at.replace(tzinfo=timezone.utc)
                    if completed_at.tzinfo is None
                    else completed_at.astimezone(timezone.utc)
                )
            else:
                completed_dt = datetime.strptime(str(completed_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (datetime.now(timezone.utc) - completed_dt).total_seconds())
            is_fresh = row["status"] == "completed" and age_seconds <= float(max(1, ttl_seconds))
        except (ValueError, TypeError):
            age_seconds = None

    return {
        "cache_key": row["cache_key"],
        "status": row["status"],
        "payload": payload,
        "error_message": row["error_message"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
        "age_seconds": age_seconds,
        "is_stale": not is_fresh,
        "is_fresh": is_fresh,
    }


def save_federal_view_snapshot(
    conn: sqlite3.Connection,
    snapshot_type: str,
    params: dict,
    status: str,
    payload: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> dict:
    """Persist cached payload metadata for heavy federal views."""
    cache_key = _federal_view_cache_key(snapshot_type, params)
    payload_json = json.dumps(payload) if payload is not None else None
    params_json = json.dumps(params, sort_keys=True)
    conn.execute(
        """
        INSERT INTO analytics_snapshots (
            cache_key, snapshot_type, params_json, payload_json, status, error_message,
            created_at, updated_at, completed_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
            CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
        )
        ON CONFLICT(cache_key) DO UPDATE SET
            snapshot_type = excluded.snapshot_type,
            params_json = excluded.params_json,
            payload_json = CASE
                WHEN excluded.payload_json IS NOT NULL THEN excluded.payload_json
                ELSE analytics_snapshots.payload_json
            END,
            status = excluded.status,
            error_message = excluded.error_message,
            updated_at = CURRENT_TIMESTAMP,
            completed_at = CASE
                WHEN excluded.status = 'completed' THEN CURRENT_TIMESTAMP
                ELSE analytics_snapshots.completed_at
            END
        """,
        (cache_key, snapshot_type, params_json, payload_json, status, error_message, status),
    )
    conn.commit()
    return get_federal_view_snapshot(conn, snapshot_type=snapshot_type, params=params, ttl_seconds=1)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _normalize_name(value: str | None) -> str:
    text = _clean_text(value).upper()
    if not text:
        return ""

    # Normalize "LAST, FIRST" to "FIRST LAST" when possible.
    if "," in text:
        left, right = [part.strip() for part in text.split(",", 1)]
        if left and right:
            text = f"{right} {left}"

    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _name_tokens(value: str | None) -> list[str]:
    stop_words = {
        "JR",
        "SR",
        "II",
        "III",
        "IV",
        "V",
        "DR",
        "MR",
        "MRS",
        "MS",
    }
    return [token for token in _normalize_name(value).split() if token and token not in stop_words]


def _jaccard(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    l_set = set(left)
    r_set = set(right)
    overlap = len(l_set & r_set)
    union = len(l_set | r_set)
    if union == 0:
        return 0.0
    return overlap / union


def _parse_bool(value: str | None) -> int | None:
    lowered = _clean_text(value).lower()
    if not lowered:
        return None
    if lowered in {"1", "true", "t", "yes", "y"}:
        return 1
    if lowered in {"0", "false", "f", "no", "n"}:
        return 0
    return None


def _office_code(value: str | None) -> str | None:
    lowered = _clean_text(value).lower()
    if not lowered:
        return None
    if "house" in lowered:
        return "H"
    if "senate" in lowered:
        return "S"
    if "president" in lowered:
        return "P"
    return None


def _district_code(value: str | None, office_code: str | None) -> str | None:
    if office_code != "H":
        return None

    text = _clean_text(value).upper()
    if not text:
        return None

    m = re.search(r"(\d{1,2})$", text)
    if not m:
        return None
    return m.group(1).zfill(2)


_PARTY_CODE_MAP = {
    "DEMOCRAT": "DEM",
    "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "INDEPENDENT": "IND",
    "OTHER": "OTH",
    "GREEN": "GRE",
    "LIBERTARIAN": "LIB",
}

_PARTY_DISPLAY_MAP = {
    "DEM": "Democratic",
    "REP": "Republican",
    "IND": "Independent",
    "LIB": "Libertarian",
    "GRE": "Green",
    "OTH": "Other",
}


def _party_code(value: str | None) -> str | None:
    text = _clean_text(value).upper()
    if not text:
        return None
    if len(text) == 3:
        return text
    return _PARTY_CODE_MAP.get(text)


def _canonical_party_code(*values: str | None) -> str:
    for value in values:
        text = _clean_text(value).upper()
        if not text:
            continue
        if text in _PARTY_DISPLAY_MAP:
            return text
        mapped = _PARTY_CODE_MAP.get(text)
        if mapped:
            return mapped
        if len(text) == 3:
            return text
    return ""


def _party_display_label(party_code: str, fallback: str | None = None) -> str:
    canonical = _canonical_party_code(party_code, fallback)
    if canonical in _PARTY_DISPLAY_MAP:
        return _PARTY_DISPLAY_MAP[canonical]
    text = _clean_text(fallback)
    if text:
        return text.title()
    return "Unknown Party"


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = _clean_text(str(value))
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _normalize_zip5(value: str | None) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    m = re.search(r"(\d{5})", text)
    return m.group(1) if m else ""


def _build_donor_entity_identity(
    contributor_name: str | None,
    contributor_state: str | None,
    contributor_zip: str | None,
    contributor_id: str | None,
    donor_key: str | None,
    sub_id: str | None = None,
) -> tuple[str, str]:
    """Build a stable donor entity key for cross-candidate drill-down.

    Identity strategy uses the strongest available fields from current data:
    1) normalized name + state + ZIP5
    2) normalized name + ZIP5
    3) normalized name + state
    4) contributor_id
    5) legacy donor_key
    6) normalized name only
    7) sub_id fallback (last resort to avoid blank keys)
    """
    name_key = _normalize_name(contributor_name)
    state_key = _clean_text(contributor_state).upper()
    zip5 = _normalize_zip5(contributor_zip)
    contributor_id_key = _clean_text(contributor_id).upper()
    donor_key_clean = _clean_text(donor_key).lower()
    sub_key = _clean_text(sub_id)

    if name_key and state_key and zip5:
        digest = hashlib.sha1(f"{name_key}|{state_key}|{zip5}".encode("utf-8")).hexdigest()[:20]
        return f"nsz_{digest}", "name_state_zip"
    if name_key and zip5:
        digest = hashlib.sha1(f"{name_key}|{zip5}".encode("utf-8")).hexdigest()[:20]
        return f"nz_{digest}", "name_zip"
    if name_key and state_key:
        digest = hashlib.sha1(f"{name_key}|{state_key}".encode("utf-8")).hexdigest()[:20]
        return f"ns_{digest}", "name_state"
    if contributor_id_key:
        digest = hashlib.sha1(contributor_id_key.encode("utf-8")).hexdigest()[:20]
        return f"id_{digest}", "contributor_id"
    if donor_key_clean:
        return f"dk_{donor_key_clean}", "legacy_donor_key"
    if name_key:
        digest = hashlib.sha1(name_key.encode("utf-8")).hexdigest()[:20]
        return f"n_{digest}", "name_only"
    if sub_key:
        digest = hashlib.sha1(sub_key.encode("utf-8")).hexdigest()[:20]
        return f"sub_{digest}", "sub_id_fallback"
    return "", "unknown"


def donor_identity_method_label(method: str | None) -> str:
    labels = {
        "name_state_zip": "matched by normalized name + state + ZIP5",
        "name_zip": "matched by normalized name + ZIP5",
        "name_state": "matched by normalized name + state",
        "contributor_id": "matched by contributor ID",
        "legacy_donor_key": "matched by existing donor key",
        "name_only": "matched by normalized name only",
        "sub_id_fallback": "fallback using contribution record only",
        "unknown": "identity method unavailable",
    }
    key = _clean_text(method).lower()
    return labels.get(key, labels["unknown"])


def _canonical_office_code(*values: str | None) -> str:
    for value in values:
        text = _clean_text(value).upper()
        if not text:
            continue
        if text in {"H", "S", "P"}:
            return text
        if "HOUSE" in text:
            return "H"
        if "SENATE" in text:
            return "S"
        if "PRESIDENT" in text:
            return "P"
    return ""


def _canonical_district_code(office_code: str, *values: str | None) -> str:
    if office_code in {"S", "P"}:
        return "STATEWIDE"
    if office_code != "H":
        return ""

    for value in values:
        text = _clean_text(value).upper()
        if not text:
            continue
        m = re.search(r"(\d{1,2})", text)
        if m:
            return m.group(1).zfill(2)
    return ""


def _office_display_label(office_code: str, fallback: str | None = None) -> str:
    if office_code == "H":
        return "U.S. House"
    if office_code == "S":
        return "U.S. Senate"
    if office_code == "P":
        return "President"
    return _clean_text(fallback) or "Unknown Office"


def _district_display_label(office_code: str, district_code: str, fallback: str | None = None) -> str:
    if office_code in {"S", "P"}:
        return "Statewide"
    if office_code == "H":
        if district_code and district_code != "STATEWIDE":
            return f"IL-{district_code}"
        return _clean_text(fallback) or "Unknown District"
    return _clean_text(fallback) or "-"


def _normalize_district_filter(office_code: str, district_code: str | None) -> str:
    text = _clean_text(district_code).upper()
    if not text:
        return ""
    if office_code in {"S", "P"}:
        return "STATEWIDE"
    if office_code == "H":
        m = re.search(r"(\d{1,2})", text)
        if m:
            return m.group(1).zfill(2)
    return text


def _hash_identifier(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _candidate_key(
    cycle: int,
    office: str,
    district: str,
    party: str,
    election_stage: str,
    candidate_name: str,
    row_number: int,
) -> str:
    payload = "|".join(
        [
            str(cycle),
            _clean_text(office).upper(),
            _clean_text(district).upper(),
            _clean_text(party).upper(),
            _clean_text(election_stage).upper(),
            _normalize_name(candidate_name),
            str(row_number),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _build_cache_source_type(endpoint: str) -> str:
    normalized = endpoint.strip("/").replace("/", "_")
    return f"fec_api:{normalized}"


def _serialize_params(params: dict[str, Any]) -> str:
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            normalized[key] = [str(v) for v in value]
        else:
            normalized[key] = str(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _build_redacted_source_url(base_url: str, endpoint: str, params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if k != "api_key" and v is not None}
    query = urlencode(filtered, doseq=True)
    prefix = base_url.rstrip("/")
    path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    if query:
        return f"{prefix}{path}?{query}"
    return f"{prefix}{path}"


def _get_cached_payload(conn: sqlite3.Connection, source_type: str, source_identifier: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT payload_json
        FROM raw_extractions
        WHERE source_type = ? AND source_identifier = ?
        """,
        (source_type, source_identifier),
    ).fetchone()
    if not row or not row["payload_json"]:
        return None
    try:
        return json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None


def _save_raw_payload(
    conn: sqlite3.Connection,
    source_type: str,
    source_identifier: str,
    source_url: str,
    payload: dict,
) -> None:
    payload_json = json.dumps(payload, ensure_ascii=True)
    conn.execute(
        """
        INSERT INTO raw_extractions (
            source_type,
            source_identifier,
            source_url,
            parser_version,
            payload_json
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_type, source_identifier) DO UPDATE SET
            source_url = excluded.source_url,
            parser_version = excluded.parser_version,
            payload_json = excluded.payload_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            source_type,
            source_identifier,
            source_url,
            PARSER_VERSION,
            payload_json,
        ),
    )


@dataclass
class FecApiClient:
    """Minimal FEC API client with urllib + curl fallback and retries."""

    api_key: str
    base_url: str = "https://api.open.fec.gov/v1"
    timeout_seconds: int = 30
    max_retries: int = 4
    retry_backoff_seconds: float = 1.5
    user_agent: str = "IllinoisCampaignFinanceFecSync/1.0"

    def request(self, endpoint: str, params: dict[str, Any]) -> dict:
        if not self.api_key:
            raise ValueError("FEC API key is required")

        clean_params = {k: v for k, v in params.items() if v is not None}
        clean_params["api_key"] = self.api_key
        url = self._build_url(endpoint, clean_params)

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                req = Request(url, headers={"User-Agent": self.user_agent})
                with urlopen(req, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                return json.loads(raw)
            except HTTPError as exc:
                last_error = exc
                # Retries on throttle and transient upstream errors.
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                break
            except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                break

        # Fallback to curl for environments where urllib DNS/network is restricted.
        for attempt in range(1, self.max_retries + 1):
            proc = subprocess.run(
                [
                    "curl",
                    "--max-time",
                    str(self.timeout_seconds),
                    "-sS",
                    url,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                try:
                    return json.loads(proc.stdout)
                except json.JSONDecodeError as exc:
                    last_error = exc
            else:
                if proc.stderr:
                    last_error = RuntimeError(proc.stderr.strip())

            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * attempt)

        raise RuntimeError(f"FEC request failed for {endpoint}: {last_error}")

    def _build_url(self, endpoint: str, params: dict[str, Any]) -> str:
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        query = urlencode(params, doseq=True)
        return f"{self.base_url.rstrip('/')}{path}?{query}"


def _request_with_cache(
    conn: sqlite3.Connection,
    client: FecApiClient,
    endpoint: str,
    params: dict[str, Any],
    use_cache: bool,
) -> tuple[dict, str, bool]:
    serialized_params = _serialize_params(params)
    source_type = _build_cache_source_type(endpoint)
    source_identifier = _hash_identifier(endpoint, serialized_params)

    if use_cache:
        cached = _get_cached_payload(conn, source_type=source_type, source_identifier=source_identifier)
        if cached is not None:
            return cached, source_identifier, True

    payload = client.request(endpoint, params=params)
    source_url = _build_redacted_source_url(client.base_url, endpoint, params)
    _save_raw_payload(
        conn,
        source_type=source_type,
        source_identifier=source_identifier,
        source_url=source_url,
        payload=payload,
    )
    return payload, source_identifier, False


def _seed_rows_from_csv(csv_path: Path, default_cycle: int | None) -> list[dict]:
    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row_number, row in enumerate(reader, start=2):
            candidate_name = _clean_text(row.get("candidate_name"))
            if not candidate_name:
                continue
            as_of_date = _clean_text(row.get("as_of_date"))
            cycle = default_cycle
            if cycle is None and as_of_date and len(as_of_date) >= 4 and as_of_date[:4].isdigit():
                cycle = int(as_of_date[:4])
            if cycle is None:
                cycle = 2026

            office = _clean_text(row.get("office"))
            office_code = _office_code(office)
            district = _clean_text(row.get("district"))
            district_code = _district_code(district, office_code)
            party = _clean_text(row.get("party"))
            party_code = _party_code(party)
            election_stage = _clean_text(row.get("election_stage"))

            candidate_key = _candidate_key(
                cycle=cycle,
                office=office,
                district=district,
                party=party,
                election_stage=election_stage,
                candidate_name=candidate_name,
                row_number=row_number,
            )

            rows.append(
                {
                    "candidate_key": candidate_key,
                    "as_of_date": as_of_date or None,
                    "cycle": cycle,
                    "office": office or None,
                    "office_code": office_code,
                    "district": district or None,
                    "district_code": district_code,
                    "party": party or None,
                    "party_code": party_code,
                    "election_stage": election_stage or None,
                    "candidate_name": candidate_name,
                    "normalized_candidate_name": _normalize_name(candidate_name),
                    "write_in": _parse_bool(row.get("write_in")),
                    "already_listed_general": _parse_bool(row.get("already_listed_general")),
                    "source_row_number": row_number,
                }
            )

    return rows


def _upsert_seed_rows(conn: sqlite3.Connection, seed_rows: list[dict], source_file: str) -> None:
    for row in seed_rows:
        conn.execute(
            """
            INSERT INTO fec_il_candidate_seed (
                candidate_key,
                as_of_date,
                cycle,
                office,
                office_code,
                district,
                district_code,
                party,
                party_code,
                election_stage,
                candidate_name,
                normalized_candidate_name,
                write_in,
                already_listed_general,
                source_file,
                source_row_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_key) DO UPDATE SET
                as_of_date = excluded.as_of_date,
                cycle = excluded.cycle,
                office = excluded.office,
                office_code = excluded.office_code,
                district = excluded.district,
                district_code = excluded.district_code,
                party = excluded.party,
                party_code = excluded.party_code,
                election_stage = excluded.election_stage,
                candidate_name = excluded.candidate_name,
                normalized_candidate_name = excluded.normalized_candidate_name,
                write_in = excluded.write_in,
                already_listed_general = excluded.already_listed_general,
                source_file = excluded.source_file,
                source_row_number = excluded.source_row_number,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                row["candidate_key"],
                row["as_of_date"],
                row["cycle"],
                row["office"],
                row["office_code"],
                row["district"],
                row["district_code"],
                row["party"],
                row["party_code"],
                row["election_stage"],
                row["candidate_name"],
                row["normalized_candidate_name"],
                row["write_in"],
                row["already_listed_general"],
                source_file,
                row["source_row_number"],
            ),
        )


def _score_candidate_match(seed: dict, candidate: dict) -> float:
    seed_tokens = _name_tokens(seed.get("candidate_name"))
    candidate_tokens = _name_tokens(candidate.get("name"))
    score = _jaccard(seed_tokens, candidate_tokens) * 65.0

    if _normalize_name(seed.get("candidate_name")) == _normalize_name(candidate.get("name")):
        score += 25.0

    office_code = seed.get("office_code")
    if office_code and candidate.get("office") == office_code:
        score += 18.0
    elif office_code:
        score -= 18.0

    district_code = seed.get("district_code")
    if office_code == "H" and district_code:
        cand_district = _clean_text(candidate.get("district") or "")
        if cand_district and cand_district.zfill(2) == district_code:
            score += 20.0
        else:
            score -= 14.0

    party_code = seed.get("party_code")
    if party_code and candidate.get("party") == party_code:
        score += 10.0

    if _clean_text(candidate.get("state")) == "IL":
        score += 4.0

    candidate_status = _clean_text(candidate.get("candidate_status"))
    if candidate_status in {"C", "F", "N"}:
        score += 2.0

    return score


def _select_best_candidate(seed: dict, candidates: list[dict]) -> tuple[dict | None, str, float]:
    if not candidates:
        return None, "unmatched", 0.0

    scored = sorted(
        ((candidate, _score_candidate_match(seed, candidate)) for candidate in candidates),
        key=lambda pair: pair[1],
        reverse=True,
    )
    top_candidate, top_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else -9999

    if top_score < 40:
        return None, "unmatched", top_score

    if top_score - second_score <= 2 and top_score < 85:
        return top_candidate, "ambiguous", top_score

    return top_candidate, "matched", top_score


def _upsert_candidate_match(
    conn: sqlite3.Connection,
    seed: dict,
    matched: dict | None,
    match_status: str,
    match_score: float,
    match_method: str,
) -> None:
    conn.execute(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key,
            candidate_name,
            office,
            office_code,
            district,
            district_code,
            party,
            party_code,
            election_stage,
            cycle,
            fec_candidate_id,
            fec_name,
            fec_office,
            fec_state,
            fec_district,
            fec_party,
            match_status,
            match_score,
            match_method,
            candidate_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(seed_candidate_key) DO UPDATE SET
            candidate_name = excluded.candidate_name,
            office = excluded.office,
            office_code = excluded.office_code,
            district = excluded.district,
            district_code = excluded.district_code,
            party = excluded.party,
            party_code = excluded.party_code,
            election_stage = excluded.election_stage,
            cycle = excluded.cycle,
            fec_candidate_id = excluded.fec_candidate_id,
            fec_name = excluded.fec_name,
            fec_office = excluded.fec_office,
            fec_state = excluded.fec_state,
            fec_district = excluded.fec_district,
            fec_party = excluded.fec_party,
            match_status = excluded.match_status,
            match_score = excluded.match_score,
            match_method = excluded.match_method,
            candidate_payload_json = excluded.candidate_payload_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            seed["candidate_key"],
            seed["candidate_name"],
            seed.get("office"),
            seed.get("office_code"),
            seed.get("district"),
            seed.get("district_code"),
            seed.get("party"),
            seed.get("party_code"),
            seed.get("election_stage"),
            seed.get("cycle"),
            matched.get("candidate_id") if matched else None,
            matched.get("name") if matched else None,
            matched.get("office") if matched else None,
            matched.get("state") if matched else None,
            matched.get("district") if matched else None,
            matched.get("party") if matched else None,
            match_status,
            round(float(match_score), 3),
            match_method,
            json.dumps(matched, ensure_ascii=True) if matched else None,
        ),
    )


def _extract_committees_from_candidate(candidate_payload: dict) -> list[dict]:
    committees = candidate_payload.get("principal_committees") or []
    output: list[dict] = []
    for committee in committees:
        committee_id = _clean_text(committee.get("committee_id"))
        if not committee_id:
            continue
        output.append(
            {
                "committee_id": committee_id,
                "committee_name": committee.get("name"),
                "committee_type": committee.get("committee_type"),
                "committee_designation": committee.get("designation"),
                "committee_designation_full": committee.get("designation_full"),
                "filing_frequency": committee.get("filing_frequency"),
                "committee_party": committee.get("party"),
                "committee_city": committee.get("city"),
                "committee_state": committee.get("state"),
                "committee_zip": committee.get("zip"),
                "is_principal": 1 if _clean_text(committee.get("designation")) == "P" else 0,
                "source_payload_json": json.dumps(committee, ensure_ascii=True),
            }
        )
    return output


def _upsert_candidate_committees(
    conn: sqlite3.Connection,
    candidate_id: str,
    cycle: int,
    committees: list[dict],
) -> int:
    count = 0
    for committee in committees:
        committee_id = _clean_text(committee.get("committee_id"))
        if not committee_id:
            continue

        conn.execute(
            """
            INSERT INTO fec_candidate_committees (
                candidate_id,
                committee_id,
                cycle,
                committee_name,
                committee_type,
                committee_designation,
                committee_designation_full,
                filing_frequency,
                committee_party,
                committee_city,
                committee_state,
                committee_zip,
                is_principal,
                source_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, committee_id, cycle) DO UPDATE SET
                committee_name = excluded.committee_name,
                committee_type = excluded.committee_type,
                committee_designation = excluded.committee_designation,
                committee_designation_full = excluded.committee_designation_full,
                filing_frequency = excluded.filing_frequency,
                committee_party = excluded.committee_party,
                committee_city = excluded.committee_city,
                committee_state = excluded.committee_state,
                committee_zip = excluded.committee_zip,
                is_principal = excluded.is_principal,
                source_payload_json = excluded.source_payload_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                committee_id,
                cycle,
                committee.get("committee_name"),
                committee.get("committee_type"),
                committee.get("committee_designation"),
                committee.get("committee_designation_full"),
                committee.get("filing_frequency"),
                committee.get("committee_party"),
                committee.get("committee_city"),
                committee.get("committee_state"),
                committee.get("committee_zip"),
                committee.get("is_principal") or 0,
                committee.get("source_payload_json"),
            ),
        )
        count += 1
    return count


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _upsert_candidate_cycle_total(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    cycle: int,
    payload: dict,
) -> int:
    if not _table_exists(conn, "fec_candidate_cycle_totals"):
        return 0

    receipts = _safe_float(payload.get("receipts"))
    contributions = _safe_float(payload.get("contributions"))
    individual_contributions = _safe_float(payload.get("individual_contributions"))
    disbursements = _safe_float(payload.get("disbursements"))
    coverage_start_date = _clean_text(payload.get("coverage_start_date")) or None
    coverage_end_date = _clean_text(payload.get("coverage_end_date")) or None
    transaction_coverage_date = _clean_text(payload.get("transaction_coverage_date")) or None
    last_report_year = _safe_int(payload.get("last_report_year"))
    last_report_type_full = _clean_text(payload.get("last_report_type_full")) or None
    last_cash_on_hand_end_period = _safe_float(payload.get("last_cash_on_hand_end_period"))

    conn.execute(
        """
        INSERT INTO fec_candidate_cycle_totals (
            candidate_id,
            cycle,
            receipts,
            contributions,
            individual_contributions,
            disbursements,
            coverage_start_date,
            coverage_end_date,
            transaction_coverage_date,
            last_report_year,
            last_report_type_full,
            last_cash_on_hand_end_period,
            source_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id, cycle) DO UPDATE SET
            receipts = excluded.receipts,
            contributions = excluded.contributions,
            individual_contributions = excluded.individual_contributions,
            disbursements = excluded.disbursements,
            coverage_start_date = excluded.coverage_start_date,
            coverage_end_date = excluded.coverage_end_date,
            transaction_coverage_date = excluded.transaction_coverage_date,
            last_report_year = excluded.last_report_year,
            last_report_type_full = excluded.last_report_type_full,
            last_cash_on_hand_end_period = excluded.last_cash_on_hand_end_period,
            source_payload_json = excluded.source_payload_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            candidate_id,
            cycle,
            receipts,
            contributions,
            individual_contributions,
            disbursements,
            coverage_start_date,
            coverage_end_date,
            transaction_coverage_date,
            last_report_year,
            last_report_type_full,
            last_cash_on_hand_end_period,
            json.dumps(payload, ensure_ascii=True),
        ),
    )
    return 1


def _build_donor_key(
    contributor_name: str | None,
    contributor_state: str | None,
    contributor_zip: str | None,
    contributor_id: str | None,
) -> str:
    payload = "|".join(
        [
            _normalize_name(contributor_name),
            _clean_text(contributor_state).upper(),
            _clean_text(contributor_zip).upper(),
            _clean_text(contributor_id).upper(),
        ]
    )
    if not payload.strip("|"):
        return ""
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _normalize_org_identity(
    value: str | None,
    *,
    state: str | None = None,
    zip_code: str | None = None,
) -> str:
    """Build a lightweight organization identity key for A/B/E cross-role matching."""
    name_key = _normalize_name(value)
    if not name_key:
        return ""
    state_key = _clean_text(state).upper()
    zip_key = _clean_text(zip_code)[:5]
    return "|".join([name_key, state_key, zip_key])


def _extract_contributor_name(row: dict) -> str:
    direct = _clean_text(row.get("contributor_name"))
    if direct:
        return direct

    contributor = row.get("contributor") if isinstance(row.get("contributor"), dict) else {}
    if contributor:
        nested = _clean_text(contributor.get("name"))
        if nested:
            return nested

    first = _clean_text(row.get("contributor_first_name"))
    last = _clean_text(row.get("contributor_last_name"))
    return " ".join(part for part in [first, last] if part).strip()


def _upsert_schedule_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    cycle: int,
    candidate_id: str | None,
    candidate_name: str | None,
    committee_id: str,
    default_committee_name: str | None,
    api_source_identifier: str,
) -> int:
    payload_rows: list[tuple] = []
    default_candidate_id = _clean_text(candidate_id)
    default_candidate_name = _clean_text(candidate_name)

    for row in rows:
        sub_id = _clean_text(row.get("sub_id"))
        if not sub_id:
            fallback_payload = "|".join(
                [
                    committee_id,
                    str(row.get("contribution_receipt_date") or ""),
                    str(row.get("contribution_receipt_amount") or ""),
                    _extract_contributor_name(row),
                    str(row.get("image_number") or ""),
                ]
            )
            sub_id = hashlib.sha1(fallback_payload.encode("utf-8")).hexdigest()

        contributor = row.get("contributor") if isinstance(row.get("contributor"), dict) else {}
        committee_payload = row.get("committee") if isinstance(row.get("committee"), dict) else {}

        contributor_name = _extract_contributor_name(row)
        contributor_city = _clean_text(row.get("contributor_city")) or _clean_text(contributor.get("city"))
        contributor_state = _clean_text(row.get("contributor_state")) or _clean_text(contributor.get("state"))
        contributor_zip = _clean_text(row.get("contributor_zip")) or _clean_text(contributor.get("zip"))
        contributor_employer = _clean_text(row.get("contributor_employer"))
        contributor_occupation = _clean_text(row.get("contributor_occupation"))
        contributor_id = _clean_text(row.get("contributor_id")) or _clean_text(contributor.get("committee_id"))

        is_individual_raw = row.get("is_individual")
        if isinstance(is_individual_raw, bool):
            is_individual = 1 if is_individual_raw else 0
        elif is_individual_raw is None:
            is_individual = None
        else:
            is_individual = _parse_bool(str(is_individual_raw))

        contributor_amount = row.get("contribution_receipt_amount")
        try:
            contribution_receipt_amount = float(contributor_amount) if contributor_amount is not None else None
        except (TypeError, ValueError):
            contribution_receipt_amount = None

        committee_name = (
            _clean_text(row.get("committee_name"))
            or _clean_text(committee_payload.get("name"))
            or default_committee_name
        )

        donor_key = _build_donor_key(
            contributor_name=contributor_name,
            contributor_state=contributor_state,
            contributor_zip=contributor_zip,
            contributor_id=contributor_id,
        )
        donor_entity_key, donor_entity_method = _build_donor_entity_identity(
            contributor_name=contributor_name,
            contributor_state=contributor_state,
            contributor_zip=contributor_zip,
            contributor_id=contributor_id,
            donor_key=donor_key,
            sub_id=sub_id,
        )

        payload_rows.append(
            (
                sub_id,
                cycle,
                default_candidate_id or _clean_text(row.get("candidate_id")) or None,
                default_candidate_name or _clean_text(row.get("candidate_name")) or None,
                committee_id,
                committee_name,
                contributor_name or None,
                contributor_city or None,
                contributor_state or None,
                contributor_zip or None,
                contributor_employer or None,
                contributor_occupation or None,
                contributor_id or None,
                is_individual,
                _clean_text(row.get("line_number")) or None,
                _clean_text(row.get("receipt_type")) or None,
                _clean_text(row.get("receipt_type_desc")) or _clean_text(row.get("receipt_type_full")) or None,
                _clean_text(row.get("memo_text")) or None,
                contribution_receipt_amount,
                _clean_text(row.get("contribution_receipt_date")) or None,
                _coerce_int(row.get("two_year_transaction_period")),
                donor_key or None,
                donor_entity_key or None,
                donor_entity_method or None,
                _clean_text(row.get("load_date")) or None,
                _clean_text(row.get("image_number")) or None,
                api_source_identifier,
            )
        )

    conn.executemany(
        """
        INSERT INTO fec_schedule_a_contributions (
            sub_id,
            cycle,
            candidate_id,
            candidate_name,
            committee_id,
            committee_name,
            contributor_name,
            contributor_city,
            contributor_state,
            contributor_zip,
            contributor_employer,
            contributor_occupation,
            contributor_id,
            is_individual,
            line_number,
            receipt_type,
            receipt_type_desc,
            memo_text,
            contribution_receipt_amount,
            contribution_receipt_date,
            two_year_transaction_period,
            donor_key,
            donor_entity_key,
            donor_entity_method,
            load_date,
            image_number,
            api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sub_id) DO UPDATE SET
            cycle = excluded.cycle,
            candidate_id = excluded.candidate_id,
            candidate_name = excluded.candidate_name,
            committee_id = excluded.committee_id,
            committee_name = excluded.committee_name,
            contributor_name = excluded.contributor_name,
            contributor_city = excluded.contributor_city,
            contributor_state = excluded.contributor_state,
            contributor_zip = excluded.contributor_zip,
            contributor_employer = excluded.contributor_employer,
            contributor_occupation = excluded.contributor_occupation,
            contributor_id = excluded.contributor_id,
            is_individual = excluded.is_individual,
            line_number = excluded.line_number,
            receipt_type = excluded.receipt_type,
            receipt_type_desc = excluded.receipt_type_desc,
            memo_text = excluded.memo_text,
            contribution_receipt_amount = excluded.contribution_receipt_amount,
            contribution_receipt_date = excluded.contribution_receipt_date,
            two_year_transaction_period = excluded.two_year_transaction_period,
            donor_key = excluded.donor_key,
            donor_entity_key = excluded.donor_entity_key,
            donor_entity_method = excluded.donor_entity_method,
            load_date = excluded.load_date,
            image_number = excluded.image_number,
            api_source_identifier = excluded.api_source_identifier,
            updated_at = CURRENT_TIMESTAMP
        """,
        payload_rows,
    )
    return len(payload_rows)


def _extract_schedule_b_recipient_name(row: dict) -> str:
    direct = _clean_text(row.get("recipient_name"))
    if direct:
        return direct

    payee_name = _clean_text(row.get("payee_name"))
    if payee_name:
        return payee_name

    payee_first = _clean_text(row.get("payee_first_name"))
    payee_last = _clean_text(row.get("payee_last_name"))
    return " ".join(part for part in [payee_first, payee_last] if part).strip()


def _upsert_schedule_b_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    cycle: int,
    candidate_id: str,
    candidate_name: str,
    committee_id: str,
    default_committee_name: str | None,
    api_source_identifier: str,
) -> int:
    payload_rows: list[tuple] = []

    for row in rows:
        sub_id = _clean_text(row.get("sub_id"))
        if not sub_id:
            fallback_payload = "|".join(
                [
                    committee_id,
                    str(row.get("disbursement_date") or ""),
                    str(row.get("disbursement_amount") or ""),
                    _extract_schedule_b_recipient_name(row),
                    str(row.get("image_number") or ""),
                ]
            )
            sub_id = hashlib.sha1(fallback_payload.encode("utf-8")).hexdigest()

        committee_payload = row.get("committee") if isinstance(row.get("committee"), dict) else {}
        committee_name = (
            _clean_text(row.get("committee_name"))
            or _clean_text(committee_payload.get("name"))
            or default_committee_name
        )

        recipient_name = _extract_schedule_b_recipient_name(row)
        recipient_city = _clean_text(row.get("recipient_city")) or _clean_text(row.get("payee_city"))
        recipient_state = _clean_text(row.get("recipient_state")) or _clean_text(row.get("payee_state"))
        recipient_zip = _clean_text(row.get("recipient_zip")) or _clean_text(row.get("payee_zip"))

        raw_disbursement_amount = row.get("disbursement_amount")
        try:
            disbursement_amount = (
                float(raw_disbursement_amount) if raw_disbursement_amount is not None else None
            )
        except (TypeError, ValueError):
            disbursement_amount = None

        payload_rows.append(
            (
                sub_id,
                cycle,
                candidate_id,
                candidate_name,
                committee_id,
                committee_name,
                recipient_name or None,
                recipient_city or None,
                recipient_state or None,
                recipient_zip or None,
                _clean_text(row.get("recipient_committee_id")) or None,
                _clean_text(row.get("candidate_id")) or None,
                _clean_text(row.get("candidate_name")) or None,
                _clean_text(row.get("payee_employer")) or None,
                _clean_text(row.get("payee_occupation")) or None,
                _clean_text(row.get("line_number")) or None,
                _clean_text(row.get("disbursement_type")) or None,
                _clean_text(row.get("disbursement_type_description")) or None,
                _clean_text(row.get("category_code")) or None,
                _clean_text(row.get("category_code_full")) or None,
                _clean_text(row.get("election_type")) or None,
                _clean_text(row.get("election_type_full")) or None,
                _clean_text(row.get("disbursement_description")) or None,
                _clean_text(row.get("memo_text")) or None,
                disbursement_amount,
                _clean_text(row.get("disbursement_date")) or None,
                _coerce_int(row.get("two_year_transaction_period")),
                _clean_text(row.get("load_date")) or None,
                _clean_text(row.get("image_number")) or None,
                api_source_identifier,
            )
        )

    conn.executemany(
        """
        INSERT INTO fec_schedule_b_disbursements (
            sub_id,
            cycle,
            candidate_id,
            candidate_name,
            committee_id,
            committee_name,
            recipient_name,
            recipient_city,
            recipient_state,
            recipient_zip,
            recipient_committee_id,
            recipient_candidate_id,
            recipient_candidate_name,
            payee_employer,
            payee_occupation,
            line_number,
            disbursement_type,
            disbursement_type_desc,
            category_code,
            category_code_full,
            election_type,
            election_type_full,
            disbursement_description,
            memo_text,
            disbursement_amount,
            disbursement_date,
            two_year_transaction_period,
            load_date,
            image_number,
            api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sub_id) DO UPDATE SET
            cycle = excluded.cycle,
            candidate_id = excluded.candidate_id,
            candidate_name = excluded.candidate_name,
            committee_id = excluded.committee_id,
            committee_name = excluded.committee_name,
            recipient_name = excluded.recipient_name,
            recipient_city = excluded.recipient_city,
            recipient_state = excluded.recipient_state,
            recipient_zip = excluded.recipient_zip,
            recipient_committee_id = excluded.recipient_committee_id,
            recipient_candidate_id = excluded.recipient_candidate_id,
            recipient_candidate_name = excluded.recipient_candidate_name,
            payee_employer = excluded.payee_employer,
            payee_occupation = excluded.payee_occupation,
            line_number = excluded.line_number,
            disbursement_type = excluded.disbursement_type,
            disbursement_type_desc = excluded.disbursement_type_desc,
            category_code = excluded.category_code,
            category_code_full = excluded.category_code_full,
            election_type = excluded.election_type,
            election_type_full = excluded.election_type_full,
            disbursement_description = excluded.disbursement_description,
            memo_text = excluded.memo_text,
            disbursement_amount = excluded.disbursement_amount,
            disbursement_date = excluded.disbursement_date,
            two_year_transaction_period = excluded.two_year_transaction_period,
            load_date = excluded.load_date,
            image_number = excluded.image_number,
            api_source_identifier = excluded.api_source_identifier,
            updated_at = CURRENT_TIMESTAMP
        """,
        payload_rows,
    )
    return len(payload_rows)


def _extract_schedule_e_payee_name(row: dict) -> str:
    direct = _clean_text(row.get("payee_name"))
    if direct:
        return direct

    first = _clean_text(row.get("payee_first_name"))
    last = _clean_text(row.get("payee_last_name"))
    combined = " ".join(part for part in [first, last] if part).strip()
    if combined:
        return combined
    return _clean_text(row.get("committee_name"))


def _upsert_schedule_e_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    cycle: int,
    candidate_id: str,
    candidate_name: str,
    api_source_identifier: str,
) -> int:
    payload_rows: list[tuple] = []

    for row in rows:
        sub_id = _clean_text(row.get("sub_id"))
        if not sub_id:
            fallback_payload = "|".join(
                [
                    candidate_id,
                    str(row.get("expenditure_date") or ""),
                    str(row.get("expenditure_amount") or ""),
                    _extract_schedule_e_payee_name(row),
                    str(row.get("image_number") or ""),
                ]
            )
            sub_id = hashlib.sha1(fallback_payload.encode("utf-8")).hexdigest()

        committee_payload = row.get("committee") if isinstance(row.get("committee"), dict) else {}
        committee_name = _clean_text(row.get("committee_name")) or _clean_text(committee_payload.get("name"))
        candidate_row_id = _clean_text(row.get("candidate_id")) or candidate_id
        candidate_row_name = _clean_text(row.get("candidate_name")) or candidate_name

        raw_amount = row.get("expenditure_amount")
        try:
            expenditure_amount = float(raw_amount) if raw_amount is not None else None
        except (TypeError, ValueError):
            expenditure_amount = None

        payload_rows.append(
            (
                sub_id,
                cycle,
                candidate_row_id or None,
                candidate_row_name or None,
                _clean_text(row.get("candidate_office")) or None,
                _clean_text(row.get("candidate_office_state")) or None,
                _clean_text(row.get("candidate_office_district")) or None,
                _clean_text(row.get("support_oppose_indicator")) or None,
                _clean_text(row.get("committee_id")) or None,
                committee_name or None,
                _extract_schedule_e_payee_name(row) or None,
                _clean_text(row.get("payee_city")) or None,
                _clean_text(row.get("payee_state")) or None,
                _clean_text(row.get("payee_zip")) or None,
                _clean_text(row.get("category_code")) or None,
                _clean_text(row.get("category_code_full")) or None,
                _clean_text(row.get("election_type")) or None,
                _clean_text(row.get("election_type_full")) or None,
                _clean_text(row.get("expenditure_description")) or None,
                _clean_text(row.get("memo_text")) or None,
                expenditure_amount,
                _clean_text(row.get("expenditure_date")) or None,
                _clean_text(row.get("filing_date")) or None,
                _clean_text(row.get("report_type")) or None,
                _clean_text(row.get("line_number")) or _clean_text(row.get("form_line_number")) or None,
                _clean_text(row.get("image_number")) or None,
                _clean_text(row.get("load_date")) or None,
                api_source_identifier,
            )
        )

    conn.executemany(
        """
        INSERT INTO fec_schedule_e_independent_expenditures (
            sub_id,
            cycle,
            candidate_id,
            candidate_name,
            candidate_office,
            candidate_office_state,
            candidate_office_district,
            support_oppose_indicator,
            committee_id,
            committee_name,
            payee_name,
            payee_city,
            payee_state,
            payee_zip,
            category_code,
            category_code_full,
            election_type,
            election_type_full,
            expenditure_description,
            memo_text,
            expenditure_amount,
            expenditure_date,
            filing_date,
            report_type,
            line_number,
            image_number,
            load_date,
            api_source_identifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sub_id) DO UPDATE SET
            cycle = excluded.cycle,
            candidate_id = excluded.candidate_id,
            candidate_name = excluded.candidate_name,
            candidate_office = excluded.candidate_office,
            candidate_office_state = excluded.candidate_office_state,
            candidate_office_district = excluded.candidate_office_district,
            support_oppose_indicator = excluded.support_oppose_indicator,
            committee_id = excluded.committee_id,
            committee_name = excluded.committee_name,
            payee_name = excluded.payee_name,
            payee_city = excluded.payee_city,
            payee_state = excluded.payee_state,
            payee_zip = excluded.payee_zip,
            category_code = excluded.category_code,
            category_code_full = excluded.category_code_full,
            election_type = excluded.election_type,
            election_type_full = excluded.election_type_full,
            expenditure_description = excluded.expenditure_description,
            memo_text = excluded.memo_text,
            expenditure_amount = excluded.expenditure_amount,
            expenditure_date = excluded.expenditure_date,
            filing_date = excluded.filing_date,
            report_type = excluded.report_type,
            line_number = excluded.line_number,
            image_number = excluded.image_number,
            load_date = excluded.load_date,
            api_source_identifier = excluded.api_source_identifier,
            updated_at = CURRENT_TIMESTAMP
        """,
        payload_rows,
    )
    return len(payload_rows)


def rebuild_fec_donor_identities(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    only_missing: bool = False,
    batch_size: int = 5000,
) -> dict:
    """Recompute donor entity keys for cross-candidate donor drill-down."""
    if not _table_exists(conn, "fec_schedule_a_contributions"):
        return {"rows_scanned": 0, "rows_updated": 0, "cycle": cycle, "only_missing": bool(only_missing)}

    if not _column_exists(conn, "fec_schedule_a_contributions", "donor_entity_key"):
        return {"rows_scanned": 0, "rows_updated": 0, "cycle": cycle, "only_missing": bool(only_missing)}

    where_clauses: list[str] = ["rowid > ?"]
    params: list[Any] = [0]
    if cycle is not None:
        where_clauses.append("cycle = ?")
        params.append(int(cycle))
    if only_missing:
        where_clauses.append("(donor_entity_key IS NULL OR TRIM(donor_entity_key) = '')")

    last_rowid = 0
    rows_scanned = 0
    rows_updated = 0

    while True:
        rows = conn.execute(
            f"""
            SELECT
                rowid,
                sub_id,
                contributor_name,
                contributor_state,
                contributor_zip,
                contributor_id,
                donor_key,
                donor_entity_key,
                donor_entity_method
            FROM fec_schedule_a_contributions
            WHERE {" AND ".join(where_clauses)}
            ORDER BY rowid ASC
            LIMIT ?
            """,
            [last_rowid, *params[1:], max(1, int(batch_size))],
        ).fetchall()
        if not rows:
            break

        updates: list[tuple[str, str, int]] = []
        for row in rows:
            rows_scanned += 1
            entity_key, entity_method = _build_donor_entity_identity(
                contributor_name=row["contributor_name"],
                contributor_state=row["contributor_state"],
                contributor_zip=row["contributor_zip"],
                contributor_id=row["contributor_id"],
                donor_key=row["donor_key"],
                sub_id=row["sub_id"],
            )

            existing_key = _clean_text(row["donor_entity_key"])
            existing_method = _clean_text(row["donor_entity_method"]).lower()
            if existing_key == entity_key and existing_method == entity_method:
                continue

            updates.append((entity_key or None, entity_method or None, int(row["rowid"])))

        if updates:
            conn.executemany(
                """
                UPDATE fec_schedule_a_contributions
                SET donor_entity_key = ?, donor_entity_method = ?, updated_at = CURRENT_TIMESTAMP
                WHERE rowid = ?
                """,
                updates,
            )
            rows_updated += len(updates)

        last_rowid = int(rows[-1]["rowid"])

    if rows_updated:
        conn.commit()

    return {
        "rows_scanned": rows_scanned,
        "rows_updated": rows_updated,
        "cycle": cycle,
        "only_missing": bool(only_missing),
    }


def _ensure_missing_donor_identities(conn: sqlite3.Connection, cycle: int | None = None) -> None:
    if not _table_exists(conn, "fec_schedule_a_contributions"):
        return
    if not _column_exists(conn, "fec_schedule_a_contributions", "donor_entity_key"):
        return
    missing = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM fec_schedule_a_contributions
        WHERE (? IS NULL OR cycle = ?)
          AND (donor_entity_key IS NULL OR TRIM(donor_entity_key) = '')
        """,
        (cycle, cycle),
    ).fetchone()
    if not missing or int(missing["count"] or 0) == 0:
        return
    rebuild_fec_donor_identities(conn, cycle=cycle, only_missing=True)


def _fetch_candidate_universe(
    conn: sqlite3.Connection,
    client: FecApiClient,
    cycle: int,
    per_page: int,
    use_cache: bool,
    max_calls: int,
    api_calls_made: int,
) -> tuple[list[dict], int, int]:
    rows: list[dict] = []
    page = 1
    pages = 1

    while page <= pages:
        params = {
            "state": "IL",
            "cycle": cycle,
            "per_page": per_page,
            "page": page,
        }
        payload, _source_id, from_cache = _request_with_cache(
            conn,
            client,
            endpoint="/candidates/search/",
            params=params,
            use_cache=use_cache,
        )
        if not from_cache:
            api_calls_made += 1
            if api_calls_made > max_calls:
                break

        pagination = payload.get("pagination") or {}
        pages = int(pagination.get("pages") or 1)
        rows.extend(payload.get("results") or [])
        page += 1

    return rows, api_calls_made, page - 1


def _fetch_targeted_candidates(
    conn: sqlite3.Connection,
    client: FecApiClient,
    seed: dict,
    per_page: int,
    use_cache: bool,
    max_calls: int,
    api_calls_made: int,
) -> tuple[list[dict], int]:
    params: dict[str, Any] = {
        "q": seed["candidate_name"],
        "state": "IL",
        "cycle": seed["cycle"],
        "per_page": min(per_page, 50),
        "page": 1,
    }
    if seed.get("office_code"):
        params["office"] = seed["office_code"]
    if seed.get("district_code"):
        params["district"] = seed["district_code"]

    payload, _source_id, from_cache = _request_with_cache(
        conn,
        client,
        endpoint="/candidates/search/",
        params=params,
        use_cache=use_cache,
    )
    if not from_cache:
        api_calls_made += 1

    if api_calls_made > max_calls:
        return [], api_calls_made

    return payload.get("results") or [], api_calls_made


def _fetch_all_committees_for_candidate(
    conn: sqlite3.Connection,
    client: FecApiClient,
    candidate_id: str,
    cycle: int,
    per_page: int,
    use_cache: bool,
    max_calls: int,
    api_calls_made: int,
) -> tuple[list[dict], int]:
    page = 1
    pages = 1
    output: list[dict] = []

    while page <= pages and api_calls_made <= max_calls:
        params = {
            "candidate_id": candidate_id,
            "cycle": cycle,
            "per_page": per_page,
            "page": page,
        }
        payload, _source_id, from_cache = _request_with_cache(
            conn,
            client,
            endpoint=f"/candidate/{candidate_id}/committees/",
            params=params,
            use_cache=use_cache,
        )
        if not from_cache:
            api_calls_made += 1

        pagination = payload.get("pagination") or {}
        pages = int(pagination.get("pages") or 1)
        output.extend(payload.get("results") or [])
        page += 1

    return output, api_calls_made


def _fetch_candidate_cycle_totals(
    conn: sqlite3.Connection,
    client: FecApiClient,
    candidate_id: str,
    cycle: int,
    use_cache: bool,
    max_calls: int,
    api_calls_made: int,
) -> tuple[list[dict], int]:
    if api_calls_made > max_calls:
        return [], api_calls_made

    payload, _source_id, from_cache = _request_with_cache(
        conn,
        client,
        endpoint=f"/candidate/{candidate_id}/totals/",
        params={
            "cycle": cycle,
            "per_page": 100,
        },
        use_cache=use_cache,
    )
    if not from_cache:
        api_calls_made += 1

    if api_calls_made > max_calls:
        return [], api_calls_made

    return payload.get("results") or [], api_calls_made


def sync_il_federal_fec(
    conn: sqlite3.Connection,
    candidates_csv: Path,
    api_key: str,
    cycle: int | None = None,
    contributor_state: str | None = "IL",
    per_page: int = 100,
    max_calls: int = 900,
    max_pages_per_committee: int = 250,
    include_all_committees: bool = False,
    refresh_cache: bool = False,
    skip_donations: bool = False,
    max_committees: int | None = None,
    refresh_local_matches: bool = True,
    client: FecApiClient | None = None,
) -> dict:
    """Sync IL federal candidate IDs and donation rows from FEC APIs.

    The sync stores raw endpoint payloads in raw_extractions so repeated runs can
    reuse responses and avoid unnecessary API calls.
    """
    if not candidates_csv.exists():
        raise FileNotFoundError(f"Candidate CSV not found: {candidates_csv}")

    if not _table_exists(conn, "raw_extractions"):
        raise RuntimeError("raw_extractions table is required for FEC sync")

    effective_cycle = cycle
    seed_rows = _seed_rows_from_csv(candidates_csv, default_cycle=effective_cycle)
    if not seed_rows:
        return {
            "seed_rows_loaded": 0,
            "candidate_universe_rows": 0,
            "matched_rows": 0,
            "ambiguous_rows": 0,
            "unmatched_rows": 0,
            "committees_upserted": 0,
            "schedule_pages_processed": 0,
            "contributions_upserted": 0,
            "api_calls_made": 0,
            "candidate_universe_pages": 0,
            "call_budget_reached": False,
            "federal_local_pairs_written": 0,
            "federal_local_matches_written": 0,
        }

    effective_cycle = cycle or int(seed_rows[0]["cycle"])

    _upsert_seed_rows(conn, seed_rows=seed_rows, source_file=candidates_csv.name)
    conn.commit()

    api_client = client or FecApiClient(api_key=api_key)
    use_cache = not refresh_cache

    api_calls_made = 0
    candidate_universe_rows, api_calls_made, candidate_universe_pages = _fetch_candidate_universe(
        conn,
        api_client,
        cycle=effective_cycle,
        per_page=min(max(1, per_page), 100),
        use_cache=use_cache,
        max_calls=max_calls,
        api_calls_made=api_calls_made,
    )

    matched_rows = 0
    ambiguous_rows = 0
    unmatched_rows = 0
    committees_upserted = 0
    candidate_totals_upserted = 0
    contributions_upserted = 0
    schedule_pages_processed = 0
    call_budget_reached = False

    candidate_by_id: dict[str, dict] = {
        _clean_text(row.get("candidate_id")): row
        for row in candidate_universe_rows
        if _clean_text(row.get("candidate_id"))
    }

    for seed in seed_rows:
        if api_calls_made > max_calls:
            call_budget_reached = True
            break

        match_candidate, match_status, match_score = _select_best_candidate(seed, candidate_universe_rows)
        match_method = "state_cycle_search"

        if match_candidate is None:
            targeted_rows, api_calls_made = _fetch_targeted_candidates(
                conn,
                api_client,
                seed=seed,
                per_page=min(max(1, per_page), 100),
                use_cache=use_cache,
                max_calls=max_calls,
                api_calls_made=api_calls_made,
            )
            if api_calls_made > max_calls:
                call_budget_reached = True
                break

            match_candidate, match_status, match_score = _select_best_candidate(seed, targeted_rows)
            match_method = "targeted_search"

            if match_candidate:
                candidate_id = _clean_text(match_candidate.get("candidate_id"))
                if candidate_id:
                    candidate_by_id[candidate_id] = match_candidate

        _upsert_candidate_match(
            conn,
            seed=seed,
            matched=match_candidate,
            match_status=match_status,
            match_score=match_score,
            match_method=match_method,
        )

        if match_status == "matched":
            matched_rows += 1
        elif match_status == "ambiguous":
            ambiguous_rows += 1
        else:
            unmatched_rows += 1

    conn.commit()

    matched_candidates = conn.execute(
        """
        SELECT DISTINCT fec_candidate_id
        FROM fec_candidate_match
        WHERE cycle = ?
          AND fec_candidate_id IS NOT NULL
        """,
        (effective_cycle,),
    ).fetchall()

    for row in matched_candidates:
        if api_calls_made > max_calls:
            call_budget_reached = True
            break

        candidate_id = _clean_text(row["fec_candidate_id"])
        if not candidate_id:
            continue

        totals_rows, api_calls_made = _fetch_candidate_cycle_totals(
            conn,
            api_client,
            candidate_id=candidate_id,
            cycle=effective_cycle,
            use_cache=use_cache,
            max_calls=max_calls,
            api_calls_made=api_calls_made,
        )
        if api_calls_made > max_calls:
            call_budget_reached = True
            break

        selected_total = None
        for totals_row in totals_rows:
            if int(totals_row.get("cycle") or 0) == int(effective_cycle):
                selected_total = totals_row
                break
        if selected_total is None and totals_rows:
            selected_total = totals_rows[0]

        if selected_total:
            candidate_totals_upserted += _upsert_candidate_cycle_total(
                conn,
                candidate_id=candidate_id,
                cycle=effective_cycle,
                payload=selected_total,
            )

    conn.commit()

    if call_budget_reached:
        local_match_stats: dict[str, Any] = {
            "rows_written": 0,
            "materialized_matches": 0,
        }
        if refresh_local_matches:
            local_match_stats = refresh_fec_local_donor_matches(conn, cycle=effective_cycle)

        return {
            "seed_rows_loaded": len(seed_rows),
            "candidate_universe_rows": len(candidate_universe_rows),
            "candidate_universe_pages": candidate_universe_pages,
            "matched_rows": matched_rows,
            "ambiguous_rows": ambiguous_rows,
            "unmatched_rows": unmatched_rows,
            "candidate_totals_upserted": candidate_totals_upserted,
            "committees_upserted": committees_upserted,
            "schedule_pages_processed": schedule_pages_processed,
            "contributions_upserted": contributions_upserted,
            "api_calls_made": api_calls_made,
            "call_budget_reached": call_budget_reached,
            "federal_local_pairs_written": int(local_match_stats.get("rows_written") or 0),
            "federal_local_matches_written": int(local_match_stats.get("materialized_matches") or 0),
        }

    for row in matched_candidates:
        candidate_id = _clean_text(row["fec_candidate_id"])
        if not candidate_id:
            continue

        payload = candidate_by_id.get(candidate_id)
        committees = _extract_committees_from_candidate(payload or {}) if payload else []

        if include_all_committees or not committees:
            fetched_committees, api_calls_made = _fetch_all_committees_for_candidate(
                conn,
                api_client,
                candidate_id=candidate_id,
                cycle=effective_cycle,
                per_page=min(max(1, per_page), 100),
                use_cache=use_cache,
                max_calls=max_calls,
                api_calls_made=api_calls_made,
            )
            committees = [
                {
                    "committee_id": committee.get("committee_id"),
                    "committee_name": committee.get("name"),
                    "committee_type": committee.get("committee_type"),
                    "committee_designation": committee.get("designation"),
                    "committee_designation_full": committee.get("designation_full"),
                    "filing_frequency": committee.get("filing_frequency"),
                    "committee_party": committee.get("party"),
                    "committee_city": committee.get("city"),
                    "committee_state": committee.get("state"),
                    "committee_zip": committee.get("zip"),
                    "is_principal": 1 if _clean_text(committee.get("designation")) == "P" else 0,
                    "source_payload_json": json.dumps(committee, ensure_ascii=True),
                }
                for committee in fetched_committees
            ]

        committees_upserted += _upsert_candidate_committees(
            conn,
            candidate_id=candidate_id,
            cycle=effective_cycle,
            committees=committees,
        )

        if api_calls_made > max_calls:
            call_budget_reached = True
            break

    conn.commit()

    if not skip_donations and not call_budget_reached:
        committee_rows = conn.execute(
            """
            SELECT
                c.candidate_id,
                c.committee_id,
                c.committee_name,
                c.is_principal,
                COALESCE(m.fec_name, m.candidate_name) AS candidate_name
            FROM fec_candidate_committees c
            JOIN (
                SELECT DISTINCT fec_candidate_id, candidate_name, fec_name
                FROM fec_candidate_match
                WHERE cycle = ? AND fec_candidate_id IS NOT NULL
            ) m ON m.fec_candidate_id = c.candidate_id
            WHERE c.cycle = ?
              AND (? = 1 OR c.is_principal = 1)
            ORDER BY c.candidate_id, c.committee_id
            """,
            (effective_cycle, effective_cycle, 1 if include_all_committees else 0),
        ).fetchall()

        if max_committees is not None:
            committee_rows = committee_rows[: max(0, int(max_committees))]

        for committee in committee_rows:
            if api_calls_made > max_calls:
                call_budget_reached = True
                break

            candidate_id = _clean_text(committee["candidate_id"])
            committee_id = _clean_text(committee["committee_id"])
            candidate_name = _clean_text(committee["candidate_name"]) or candidate_id
            committee_name = _clean_text(committee["committee_name"]) or committee_id

            if not candidate_id or not committee_id:
                continue

            max_date_row = conn.execute(
                """
                SELECT MAX(contribution_receipt_date) AS max_date
                FROM fec_schedule_a_contributions
                WHERE committee_id = ? AND cycle = ?
                """,
                (committee_id, effective_cycle),
            ).fetchone()
            min_date = _clean_text(max_date_row["max_date"]) if max_date_row else ""

            last_index = None
            last_receipt_date = None
            page_counter = 0
            seen_page_tokens: set[str] = set()

            while page_counter < max_pages_per_committee:
                params: dict[str, Any] = {
                    "committee_id": committee_id,
                    "two_year_transaction_period": effective_cycle,
                    "per_page": min(max(1, per_page), 100),
                    "sort": "-contribution_receipt_date",
                }
                if contributor_state:
                    params["contributor_state"] = contributor_state
                if min_date:
                    params["min_date"] = min_date
                if last_index:
                    params["last_index"] = last_index
                if last_receipt_date:
                    params["last_contribution_receipt_date"] = last_receipt_date

                payload, source_identifier, from_cache = _request_with_cache(
                    conn,
                    api_client,
                    endpoint="/schedules/schedule_a/",
                    params=params,
                    use_cache=use_cache,
                )
                if not from_cache:
                    api_calls_made += 1
                    if api_calls_made > max_calls:
                        call_budget_reached = True
                        break

                schedule_pages_processed += 1
                page_counter += 1

                results = payload.get("results") or []
                if not results:
                    break

                contributions_upserted += _upsert_schedule_rows(
                    conn,
                    rows=results,
                    cycle=effective_cycle,
                    candidate_id=candidate_id,
                    candidate_name=candidate_name,
                    committee_id=committee_id,
                    default_committee_name=committee_name,
                    api_source_identifier=source_identifier,
                )
                conn.commit()

                pagination = payload.get("pagination") or {}
                last_indexes = pagination.get("last_indexes") or {}
                next_last_index = _clean_text(last_indexes.get("last_index"))
                next_last_receipt_date = _clean_text(last_indexes.get("last_contribution_receipt_date"))

                if not next_last_index:
                    break

                page_token = f"{next_last_index}|{next_last_receipt_date}"
                if page_token in seen_page_tokens:
                    break
                seen_page_tokens.add(page_token)

                last_index = next_last_index
                last_receipt_date = next_last_receipt_date or None

            if call_budget_reached:
                break

    local_match_stats: dict[str, Any] = {
        "rows_written": 0,
        "materialized_matches": 0,
    }
    if refresh_local_matches:
        local_match_stats = refresh_fec_local_donor_matches(conn, cycle=effective_cycle)

    return {
        "seed_rows_loaded": len(seed_rows),
        "candidate_universe_rows": len(candidate_universe_rows),
        "candidate_universe_pages": candidate_universe_pages,
        "matched_rows": matched_rows,
        "ambiguous_rows": ambiguous_rows,
        "unmatched_rows": unmatched_rows,
        "candidate_totals_upserted": candidate_totals_upserted,
        "committees_upserted": committees_upserted,
        "schedule_pages_processed": schedule_pages_processed,
        "contributions_upserted": contributions_upserted,
        "api_calls_made": api_calls_made,
        "call_budget_reached": call_budget_reached,
        "federal_local_pairs_written": int(local_match_stats.get("rows_written") or 0),
        "federal_local_matches_written": int(local_match_stats.get("materialized_matches") or 0),
    }


def _ensure_fec_schedule_a_backfill_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fec_schedule_a_backfill_state (
            committee_id TEXT NOT NULL,
            cycle INTEGER NOT NULL,
            candidate_id TEXT,
            candidate_name TEXT,
            next_last_index TEXT,
            next_last_receipt_date TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            pages_processed_total INTEGER NOT NULL DEFAULT 0,
            contributions_upserted_total INTEGER NOT NULL DEFAULT 0,
            api_calls_total INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (committee_id, cycle)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_backfill_cycle_completed
        ON fec_schedule_a_backfill_state(cycle, completed, updated_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_backfill_candidate
        ON fec_schedule_a_backfill_state(candidate_id, cycle)
        """
    )


def _ensure_fec_schedule_b_backfill_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fec_schedule_b_backfill_state (
            committee_id TEXT NOT NULL,
            cycle INTEGER NOT NULL,
            candidate_id TEXT,
            candidate_name TEXT,
            next_last_index TEXT,
            next_last_disbursement_date TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            pages_processed_total INTEGER NOT NULL DEFAULT 0,
            disbursements_upserted_total INTEGER NOT NULL DEFAULT 0,
            api_calls_total INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (committee_id, cycle)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_backfill_cycle_completed
        ON fec_schedule_b_backfill_state(cycle, completed, updated_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_b_backfill_candidate
        ON fec_schedule_b_backfill_state(candidate_id, cycle)
        """
    )


def _ensure_fec_schedule_e_backfill_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fec_schedule_e_backfill_state (
            candidate_id TEXT NOT NULL,
            cycle INTEGER NOT NULL,
            candidate_name TEXT,
            next_last_index TEXT,
            next_last_expenditure_date TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            pages_processed_total INTEGER NOT NULL DEFAULT 0,
            expenditures_upserted_total INTEGER NOT NULL DEFAULT 0,
            api_calls_total INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (candidate_id, cycle)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_backfill_cycle_completed
        ON fec_schedule_e_backfill_state(cycle, completed, updated_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_schedule_e_backfill_candidate
        ON fec_schedule_e_backfill_state(candidate_id, cycle)
        """
    )


def _ensure_fec_transfer_source_committees_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fec_transfer_source_committees (
            committee_id TEXT NOT NULL,
            cycle INTEGER NOT NULL,
            committee_name TEXT,
            transfer_count INTEGER NOT NULL DEFAULT 0,
            transfer_total_amount REAL NOT NULL DEFAULT 0,
            source_candidate_count INTEGER NOT NULL DEFAULT 0,
            recipient_candidate_count INTEGER NOT NULL DEFAULT 0,
            recipient_committee_count INTEGER NOT NULL DEFAULT 0,
            latest_transfer_date TEXT,
            receipts_synced INTEGER NOT NULL DEFAULT 0,
            receipts_row_count INTEGER NOT NULL DEFAULT 0,
            receipts_total_amount REAL NOT NULL DEFAULT 0,
            receipts_coverage_start TEXT,
            receipts_coverage_end TEXT,
            next_last_index TEXT,
            next_last_receipt_date TEXT,
            receipts_pages_processed_total INTEGER NOT NULL DEFAULT 0,
            receipts_api_calls_total INTEGER NOT NULL DEFAULT 0,
            last_receipts_sync_at TIMESTAMP,
            refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (committee_id, cycle)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_transfer_source_cycle_synced
        ON fec_transfer_source_committees(cycle, receipts_synced, transfer_total_amount DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fec_transfer_source_amount
        ON fec_transfer_source_committees(transfer_total_amount DESC, transfer_count DESC)
        """
    )

    # Backward compatibility for existing installs if columns were added later.
    for column_name, column_sql in [
        ("committee_name", "committee_name TEXT"),
        ("transfer_count", "transfer_count INTEGER NOT NULL DEFAULT 0"),
        ("transfer_total_amount", "transfer_total_amount REAL NOT NULL DEFAULT 0"),
        ("source_candidate_count", "source_candidate_count INTEGER NOT NULL DEFAULT 0"),
        ("recipient_candidate_count", "recipient_candidate_count INTEGER NOT NULL DEFAULT 0"),
        ("recipient_committee_count", "recipient_committee_count INTEGER NOT NULL DEFAULT 0"),
        ("latest_transfer_date", "latest_transfer_date TEXT"),
        ("receipts_synced", "receipts_synced INTEGER NOT NULL DEFAULT 0"),
        ("receipts_row_count", "receipts_row_count INTEGER NOT NULL DEFAULT 0"),
        ("receipts_total_amount", "receipts_total_amount REAL NOT NULL DEFAULT 0"),
        ("receipts_coverage_start", "receipts_coverage_start TEXT"),
        ("receipts_coverage_end", "receipts_coverage_end TEXT"),
        ("next_last_index", "next_last_index TEXT"),
        ("next_last_receipt_date", "next_last_receipt_date TEXT"),
        ("receipts_pages_processed_total", "receipts_pages_processed_total INTEGER NOT NULL DEFAULT 0"),
        ("receipts_api_calls_total", "receipts_api_calls_total INTEGER NOT NULL DEFAULT 0"),
        ("last_receipts_sync_at", "last_receipts_sync_at TIMESTAMP"),
        ("refreshed_at", "refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        if not _column_exists(conn, "fec_transfer_source_committees", column_name):
            conn.execute(
                f"ALTER TABLE fec_transfer_source_committees ADD COLUMN {column_sql}"
            )


def refresh_fec_transfer_source_committees(
    conn: sqlite3.Connection,
    *,
    cycle: int | None = None,
    min_transfer_amount: float = 0.0,
) -> dict:
    """Materialize committees that disburse to candidates/committees (Schedule B)."""
    _ensure_fec_transfer_source_committees_table(conn)

    if not _table_exists(conn, "fec_schedule_b_disbursements"):
        if cycle is None:
            conn.execute("DELETE FROM fec_transfer_source_committees")
        else:
            conn.execute(
                "DELETE FROM fec_transfer_source_committees WHERE cycle = ?",
                (int(cycle),),
            )
        conn.commit()
        return {
            "cycle": cycle,
            "rows_written": 0,
            "transfer_total_amount": 0.0,
            "skipped_reason": "missing_fec_schedule_b_disbursements",
        }

    effective_cycle = int(cycle) if cycle is not None else None
    min_transfer_amount_value = float(max(0.0, min_transfer_amount))

    where_clauses = [
        "committee_id IS NOT NULL",
        "NULLIF(committee_id, '') IS NOT NULL",
        "("
        "NULLIF(recipient_candidate_id, '') IS NOT NULL "
        "OR NULLIF(recipient_committee_id, '') IS NOT NULL"
        ")",
    ]
    params: list[Any] = []
    if effective_cycle is not None:
        where_clauses.append("cycle = ?")
        params.append(effective_cycle)

    having_sql = ""
    if min_transfer_amount_value > 0:
        having_sql = "HAVING COALESCE(SUM(disbursement_amount), 0.0) >= ?"
        params.append(min_transfer_amount_value)

    rows = conn.execute(
        f"""
        SELECT
            committee_id,
            cycle,
            COALESCE(MAX(committee_name), committee_id) AS committee_name,
            COUNT(*) AS transfer_count,
            COALESCE(SUM(disbursement_amount), 0.0) AS transfer_total_amount,
            COUNT(DISTINCT NULLIF(candidate_id, '')) AS source_candidate_count,
            COUNT(DISTINCT NULLIF(recipient_candidate_id, '')) AS recipient_candidate_count,
            COUNT(DISTINCT NULLIF(recipient_committee_id, '')) AS recipient_committee_count,
            MAX(disbursement_date) AS latest_transfer_date
        FROM fec_schedule_b_disbursements
        WHERE {" AND ".join(where_clauses)}
        GROUP BY committee_id, cycle
        {having_sql}
        ORDER BY transfer_total_amount DESC, transfer_count DESC, committee_id ASC
        """,
        params,
    ).fetchall()

    preserved_state: dict[str, dict[str, Any]] = {}
    if effective_cycle is not None:
        existing_rows = conn.execute(
            """
            SELECT
                committee_id,
                receipts_synced,
                receipts_row_count,
                receipts_total_amount,
                receipts_coverage_start,
                receipts_coverage_end,
                next_last_index,
                next_last_receipt_date,
                receipts_pages_processed_total,
                receipts_api_calls_total,
                last_receipts_sync_at
            FROM fec_transfer_source_committees
            WHERE cycle = ?
            """,
            (effective_cycle,),
        ).fetchall()
        preserved_state = {
            _clean_text(row["committee_id"]): {
                "receipts_synced": int(row["receipts_synced"] or 0),
                "receipts_row_count": int(row["receipts_row_count"] or 0),
                "receipts_total_amount": float(row["receipts_total_amount"] or 0.0),
                "receipts_coverage_start": row["receipts_coverage_start"],
                "receipts_coverage_end": row["receipts_coverage_end"],
                "next_last_index": row["next_last_index"],
                "next_last_receipt_date": row["next_last_receipt_date"],
                "receipts_pages_processed_total": int(row["receipts_pages_processed_total"] or 0),
                "receipts_api_calls_total": int(row["receipts_api_calls_total"] or 0),
                "last_receipts_sync_at": row["last_receipts_sync_at"],
            }
            for row in existing_rows
            if _clean_text(row["committee_id"])
        }
        conn.execute(
            "DELETE FROM fec_transfer_source_committees WHERE cycle = ?",
            (effective_cycle,),
        )
    else:
        conn.execute("DELETE FROM fec_transfer_source_committees")

    insert_rows: list[tuple[Any, ...]] = []
    transfer_total_amount = 0.0
    for row in rows:
        committee_id = _clean_text(row["committee_id"])
        if not committee_id:
            continue
        cycle_value = int(row["cycle"] or 0)
        state = preserved_state.get(committee_id, {})
        transfer_amount = float(row["transfer_total_amount"] or 0.0)
        transfer_total_amount += transfer_amount
        insert_rows.append(
            (
                committee_id,
                cycle_value,
                _clean_text(row["committee_name"]) or committee_id,
                int(row["transfer_count"] or 0),
                transfer_amount,
                int(row["source_candidate_count"] or 0),
                int(row["recipient_candidate_count"] or 0),
                int(row["recipient_committee_count"] or 0),
                row["latest_transfer_date"],
                int(state.get("receipts_synced") or 0),
                int(state.get("receipts_row_count") or 0),
                float(state.get("receipts_total_amount") or 0.0),
                state.get("receipts_coverage_start"),
                state.get("receipts_coverage_end"),
                state.get("next_last_index"),
                state.get("next_last_receipt_date"),
                int(state.get("receipts_pages_processed_total") or 0),
                int(state.get("receipts_api_calls_total") or 0),
                state.get("last_receipts_sync_at"),
            )
        )

    if insert_rows:
        conn.executemany(
            """
            INSERT INTO fec_transfer_source_committees (
                committee_id,
                cycle,
                committee_name,
                transfer_count,
                transfer_total_amount,
                source_candidate_count,
                recipient_candidate_count,
                recipient_committee_count,
                latest_transfer_date,
                receipts_synced,
                receipts_row_count,
                receipts_total_amount,
                receipts_coverage_start,
                receipts_coverage_end,
                next_last_index,
                next_last_receipt_date,
                receipts_pages_processed_total,
                receipts_api_calls_total,
                last_receipts_sync_at,
                refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            insert_rows,
        )

    conn.commit()
    return {
        "cycle": effective_cycle,
        "rows_written": len(insert_rows),
        "transfer_total_amount": round(transfer_total_amount, 2),
        "min_transfer_amount": min_transfer_amount_value,
    }


def sync_fec_transfer_committee_receipts(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    cycle: int,
    max_calls: int = 1000,
    per_page: int = 100,
    max_pages_per_committee: int = 25,
    include_completed: bool = False,
    refresh_cache: bool = False,
    refresh_registry: bool = True,
    max_committees: int | None = None,
    client: FecApiClient | None = None,
) -> dict:
    """Backfill Schedule A receipts for committees flagged as transfer sources."""
    if not api_key:
        raise ValueError("FEC API key is required")
    if not _table_exists(conn, "raw_extractions"):
        raise RuntimeError("raw_extractions table is required for transfer-committee sync")

    _ensure_fec_transfer_source_committees_table(conn)
    conn.commit()

    effective_cycle = int(cycle)
    registry_stats: dict[str, Any] = {"rows_written": 0}
    if refresh_registry:
        registry_stats = refresh_fec_transfer_source_committees(
            conn,
            cycle=effective_cycle,
        )

    include_completed_value = 1 if include_completed else 0
    queue = conn.execute(
        """
        SELECT
            committee_id,
            cycle,
            committee_name,
            transfer_count,
            transfer_total_amount,
            receipts_synced,
            next_last_index,
            next_last_receipt_date
        FROM fec_transfer_source_committees
        WHERE cycle = ?
          AND (
              ? = 1
              OR COALESCE(receipts_synced, 0) = 0
              OR NULLIF(next_last_index, '') IS NOT NULL
          )
        ORDER BY transfer_total_amount DESC, transfer_count DESC, committee_id ASC
        """,
        (effective_cycle, include_completed_value),
    ).fetchall()

    queue_rows = list(queue)
    if max_committees is not None:
        queue_rows = queue_rows[: max(0, int(max_committees))]

    call_budget = max(1, int(max_calls))
    per_page_value = min(max(1, int(per_page)), 100)
    page_cap = max(1, int(max_pages_per_committee))
    api_calls_made = 0
    pages_processed = 0
    contributions_upserted = 0
    committees_processed = 0
    committees_completed = 0
    call_budget_reached = False

    api_client = client or FecApiClient(api_key=api_key)
    use_cache = not bool(refresh_cache)

    for committee in queue_rows:
        committee_id = _clean_text(committee["committee_id"])
        committee_name = _clean_text(committee["committee_name"]) or committee_id
        if not committee_id:
            continue

        committees_processed += 1
        committee_pages = 0
        committee_contributions = 0
        committee_api_calls = 0
        committee_completed = False
        last_error = None
        seen_tokens: set[str] = set()
        next_last_index = _clean_text(committee["next_last_index"])
        next_last_receipt_date = _clean_text(committee["next_last_receipt_date"])

        owner = conn.execute(
            """
            SELECT
                c.candidate_id,
                COALESCE(MAX(m.fec_name), MAX(m.candidate_name), MAX(c.candidate_id)) AS candidate_name
            FROM fec_candidate_committees c
            LEFT JOIN fec_candidate_match m
              ON m.fec_candidate_id = c.candidate_id
             AND m.cycle = c.cycle
            WHERE c.committee_id = ?
              AND c.cycle = ?
            GROUP BY c.candidate_id
            ORDER BY MAX(c.is_principal) DESC, c.candidate_id ASC
            LIMIT 1
            """,
            (committee_id, effective_cycle),
        ).fetchone()
        owner_candidate_id = _clean_text(owner["candidate_id"]) if owner else ""
        owner_candidate_name = _clean_text(owner["candidate_name"]) if owner else ""

        max_date_row = conn.execute(
            """
            SELECT MAX(contribution_receipt_date) AS max_date
            FROM fec_schedule_a_contributions
            WHERE committee_id = ? AND cycle = ?
            """,
            (committee_id, effective_cycle),
        ).fetchone()
        min_date = _clean_text(max_date_row["max_date"]) if max_date_row else ""

        while committee_pages < page_cap and api_calls_made < call_budget:
            params: dict[str, Any] = {
                "committee_id": committee_id,
                "two_year_transaction_period": effective_cycle,
                "per_page": per_page_value,
                "sort": "-contribution_receipt_date",
            }
            if next_last_index:
                params["last_index"] = next_last_index
            if next_last_receipt_date:
                params["last_contribution_receipt_date"] = next_last_receipt_date
            if min_date and not next_last_index:
                params["min_date"] = min_date

            try:
                payload, source_identifier, from_cache = _request_with_cache(
                    conn,
                    api_client,
                    endpoint="/schedules/schedule_a/",
                    params=params,
                    use_cache=use_cache,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                break

            if not from_cache:
                api_calls_made += 1
                committee_api_calls += 1

            pages_processed += 1
            committee_pages += 1

            results = payload.get("results") or []
            if not results:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break

            upserted_rows = _upsert_schedule_rows(
                conn,
                rows=results,
                cycle=effective_cycle,
                candidate_id=owner_candidate_id or None,
                candidate_name=owner_candidate_name or None,
                committee_id=committee_id,
                default_committee_name=committee_name,
                api_source_identifier=source_identifier,
            )
            committee_contributions += upserted_rows
            contributions_upserted += upserted_rows
            conn.commit()

            pagination = payload.get("pagination") or {}
            last_indexes = pagination.get("last_indexes") or {}
            fetched_last_index = _clean_text(last_indexes.get("last_index"))
            fetched_last_receipt_date = _clean_text(last_indexes.get("last_contribution_receipt_date"))

            if not fetched_last_index:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break

            token = f"{fetched_last_index}|{fetched_last_receipt_date}"
            if token in seen_tokens:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break
            seen_tokens.add(token)

            next_last_index = fetched_last_index
            next_last_receipt_date = fetched_last_receipt_date

            if api_calls_made >= call_budget:
                call_budget_reached = True
                break

        receipts_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COALESCE(SUM(contribution_receipt_amount), 0.0) AS total_amount,
                MIN(contribution_receipt_date) AS coverage_start,
                MAX(contribution_receipt_date) AS coverage_end
            FROM fec_schedule_a_contributions
            WHERE committee_id = ? AND cycle = ?
            """,
            (committee_id, effective_cycle),
        ).fetchone()
        receipts_row_count = int(receipts_summary["row_count"] or 0) if receipts_summary else 0
        receipts_total_amount = float(receipts_summary["total_amount"] or 0.0) if receipts_summary else 0.0
        receipts_coverage_start = receipts_summary["coverage_start"] if receipts_summary else None
        receipts_coverage_end = receipts_summary["coverage_end"] if receipts_summary else None

        conn.execute(
            """
            UPDATE fec_transfer_source_committees
            SET
                committee_name = COALESCE(NULLIF(?, ''), committee_name),
                receipts_synced = ?,
                receipts_row_count = ?,
                receipts_total_amount = ?,
                receipts_coverage_start = ?,
                receipts_coverage_end = ?,
                next_last_index = ?,
                next_last_receipt_date = ?,
                receipts_pages_processed_total = COALESCE(receipts_pages_processed_total, 0) + ?,
                receipts_api_calls_total = COALESCE(receipts_api_calls_total, 0) + ?,
                last_receipts_sync_at = CURRENT_TIMESTAMP,
                refreshed_at = CURRENT_TIMESTAMP
            WHERE committee_id = ? AND cycle = ?
            """,
            (
                committee_name,
                1 if committee_completed else 0,
                receipts_row_count,
                round(receipts_total_amount, 2),
                receipts_coverage_start,
                receipts_coverage_end,
                next_last_index if not committee_completed else None,
                next_last_receipt_date if not committee_completed else None,
                committee_pages,
                committee_api_calls,
                committee_id,
                effective_cycle,
            ),
        )
        conn.commit()

        if committee_completed:
            committees_completed += 1

        if last_error:
            # Keep the cursor state so the next run can retry this committee.
            conn.execute(
                """
                UPDATE fec_transfer_source_committees
                SET refreshed_at = CURRENT_TIMESTAMP
                WHERE committee_id = ? AND cycle = ?
                """,
                (committee_id, effective_cycle),
            )
            conn.commit()

        if api_calls_made >= call_budget:
            call_budget_reached = True
            break

    return {
        "cycle": effective_cycle,
        "transfer_committees_selected": len(queue_rows),
        "transfer_committees_processed": committees_processed,
        "transfer_committees_completed": committees_completed,
        "pages_processed": pages_processed,
        "contributions_upserted": contributions_upserted,
        "api_calls_made": api_calls_made,
        "call_budget_reached": bool(call_budget_reached),
        "max_calls": call_budget,
        "refresh_registry": bool(refresh_registry),
        "registry_rows_written": int(registry_stats.get("rows_written") or 0),
    }


def get_federal_receipt_mismatch_flags(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    status: str = "flagged",
    min_abs_diff: float = 1.0,
    tolerance: float = 0.01,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Return candidate-level FEC total vs synced Schedule A subtotal mismatch flags."""
    if not _table_exists(conn, "fec_candidate_match"):
        return {
            "rows": [],
            "status_counts": {},
            "total_rows": 0,
            "status": "empty",
            "cycle": cycle,
            "min_abs_diff": float(min_abs_diff),
            "tolerance": float(tolerance),
        }

    _ensure_fec_schedule_a_backfill_state_table(conn)

    status_value = _clean_text(status).lower() or "flagged"
    valid_statuses = {
        "flagged",
        "all",
        "matched",
        "missing_schedule_rows",
        "schedule_exceeds_reported",
        "missing_reported_total",
    }
    if status_value not in valid_statuses:
        status_value = "flagged"

    cycle_value = int(cycle) if cycle is not None else None
    tolerance_value = max(0.0, float(tolerance))
    min_abs_diff_value = max(0.0, float(min_abs_diff))

    schedule_source_sql = (
        """
        SELECT
            candidate_id,
            cycle,
            COUNT(DISTINCT sub_id) AS contribution_count,
            COUNT(
                DISTINCT COALESCE(
                    NULLIF(donor_entity_key, ''),
                    NULLIF(donor_key, ''),
                    sub_id
                )
            ) AS donor_count,
            COUNT(DISTINCT committee_id) AS committee_count,
            COALESCE(SUM(contribution_receipt_amount), 0) AS schedule_total_amount,
            MIN(contribution_receipt_date) AS earliest_contribution_date,
            MAX(contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions
        GROUP BY candidate_id, cycle
        """
        if _table_exists(conn, "fec_schedule_a_contributions")
        else """
        SELECT
            NULL AS candidate_id,
            NULL AS cycle,
            0 AS contribution_count,
            0 AS donor_count,
            0 AS committee_count,
            0.0 AS schedule_total_amount,
            NULL AS earliest_contribution_date,
            NULL AS latest_contribution_date
        WHERE 1 = 0
        """
    )
    totals_source_sql = (
        """
        SELECT
            candidate_id,
            cycle,
            receipts,
            coverage_end_date,
            transaction_coverage_date,
            updated_at
        FROM fec_candidate_cycle_totals
        """
        if _table_exists(conn, "fec_candidate_cycle_totals")
        else """
        SELECT
            NULL AS candidate_id,
            NULL AS cycle,
            NULL AS receipts,
            NULL AS coverage_end_date,
            NULL AS transaction_coverage_date,
            NULL AS updated_at
        WHERE 1 = 0
        """
    )

    cte_sql = f"""
        WITH candidate_meta AS (
            SELECT
                fec_candidate_id AS candidate_id,
                cycle,
                MAX(COALESCE(NULLIF(fec_name, ''), NULLIF(candidate_name, ''), fec_candidate_id)) AS candidate_name,
                MAX(COALESCE(NULLIF(candidate_name, ''), NULLIF(fec_name, ''), fec_candidate_id)) AS seed_candidate_name,
                MAX(office) AS office,
                MAX(district) AS district,
                MAX(party) AS party,
                MAX(match_status) AS match_status
            FROM fec_candidate_match
            WHERE fec_candidate_id IS NOT NULL
            GROUP BY fec_candidate_id, cycle
        ),
        schedule_totals AS (
            {schedule_source_sql}
        ),
        reported_totals AS (
            {totals_source_sql}
        ),
        backfill_progress AS (
            SELECT
                candidate_id,
                cycle,
                COUNT(*) AS tracked_committees,
                SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_committees,
                MAX(updated_at) AS backfill_updated_at
            FROM fec_schedule_a_backfill_state
            GROUP BY candidate_id, cycle
        ),
        rollup AS (
            SELECT
                cm.candidate_id,
                cm.cycle,
                cm.candidate_name,
                cm.seed_candidate_name,
                cm.office,
                cm.district,
                cm.party,
                cm.match_status,
                rt.receipts AS reported_total_receipts,
                COALESCE(st.schedule_total_amount, 0.0) AS schedule_total_amount,
                CASE
                    WHEN rt.receipts IS NULL THEN NULL
                    ELSE rt.receipts - COALESCE(st.schedule_total_amount, 0.0)
                END AS mismatch_amount,
                CASE
                    WHEN rt.receipts IS NULL OR ABS(rt.receipts) < 0.0000001 THEN NULL
                    ELSE (rt.receipts - COALESCE(st.schedule_total_amount, 0.0)) / rt.receipts
                END AS mismatch_ratio,
                CASE
                    WHEN rt.receipts IS NULL THEN 'missing_reported_total'
                    WHEN ABS(rt.receipts - COALESCE(st.schedule_total_amount, 0.0)) <= ? THEN 'matched'
                    WHEN rt.receipts > COALESCE(st.schedule_total_amount, 0.0) THEN 'missing_schedule_rows'
                    ELSE 'schedule_exceeds_reported'
                END AS flag_status,
                COALESCE(st.committee_count, 0) AS schedule_committee_count,
                COALESCE(st.contribution_count, 0) AS schedule_contribution_count,
                COALESCE(st.donor_count, 0) AS schedule_donor_count,
                st.earliest_contribution_date,
                st.latest_contribution_date,
                rt.coverage_end_date,
                rt.transaction_coverage_date,
                rt.updated_at AS totals_updated_at,
                COALESCE(bp.tracked_committees, 0) AS tracked_committees,
                COALESCE(bp.completed_committees, 0) AS completed_committees,
                bp.backfill_updated_at
            FROM candidate_meta cm
            LEFT JOIN reported_totals rt
              ON rt.candidate_id = cm.candidate_id
             AND rt.cycle = cm.cycle
            LEFT JOIN schedule_totals st
              ON st.candidate_id = cm.candidate_id
             AND st.cycle = cm.cycle
            LEFT JOIN backfill_progress bp
              ON bp.candidate_id = cm.candidate_id
             AND bp.cycle = cm.cycle
        )
    """

    filter_clauses = ["(? IS NULL OR cycle = ?)"]
    filter_params: list[Any] = [cycle_value, cycle_value]

    if status_value == "flagged":
        filter_clauses.append(
            "(flag_status != 'matched' AND (flag_status = 'missing_reported_total' OR ABS(COALESCE(mismatch_amount, 0.0)) >= ?))"
        )
        filter_params.append(min_abs_diff_value)
    elif status_value == "all":
        if min_abs_diff_value > 0:
            filter_clauses.append("(flag_status = 'missing_reported_total' OR ABS(COALESCE(mismatch_amount, 0.0)) >= ?)")
            filter_params.append(min_abs_diff_value)
    elif status_value == "matched":
        filter_clauses.append("flag_status = 'matched'")
    else:
        filter_clauses.append("flag_status = ?")
        filter_params.append(status_value)
        if status_value in {"missing_schedule_rows", "schedule_exceeds_reported"} and min_abs_diff_value > 0:
            filter_clauses.append("ABS(COALESCE(mismatch_amount, 0.0)) >= ?")
            filter_params.append(min_abs_diff_value)

    where_sql = f"WHERE {' AND '.join(filter_clauses)}"

    rows = conn.execute(
        f"""
        {cte_sql}
        SELECT
            candidate_id,
            cycle,
            candidate_name,
            seed_candidate_name,
            office,
            district,
            party,
            match_status,
            reported_total_receipts,
            schedule_total_amount,
            mismatch_amount,
            mismatch_ratio,
            flag_status,
            schedule_committee_count,
            schedule_contribution_count,
            schedule_donor_count,
            earliest_contribution_date,
            latest_contribution_date,
            coverage_end_date,
            transaction_coverage_date,
            totals_updated_at,
            tracked_committees,
            completed_committees,
            backfill_updated_at
        FROM rollup
        {where_sql}
        ORDER BY
            CASE WHEN mismatch_amount IS NULL THEN -1 ELSE ABS(mismatch_amount) END DESC,
            candidate_name ASC
        LIMIT ? OFFSET ?
        """,
        [tolerance_value, *filter_params, max(1, int(limit)), max(0, int(offset))],
    ).fetchall()

    total_row = conn.execute(
        f"""
        {cte_sql}
        SELECT COUNT(*) AS count
        FROM rollup
        {where_sql}
        """,
        [tolerance_value, *filter_params],
    ).fetchone()
    total_rows = int(total_row["count"] or 0) if total_row else 0

    status_counts_rows = conn.execute(
        f"""
        {cte_sql}
        SELECT
            flag_status,
            COUNT(*) AS count
        FROM rollup
        WHERE (? IS NULL OR cycle = ?)
        GROUP BY flag_status
        ORDER BY count DESC, flag_status ASC
        """,
        [tolerance_value, cycle_value, cycle_value],
    ).fetchall()

    status_counts = {row["flag_status"]: int(row["count"] or 0) for row in status_counts_rows}

    output_rows: list[dict] = []
    for row in rows:
        output_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "cycle": int(row["cycle"] or 0),
                "candidate_name": row["candidate_name"] or row["seed_candidate_name"] or row["candidate_id"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "match_status": row["match_status"],
                "reported_total_receipts": (
                    float(row["reported_total_receipts"]) if row["reported_total_receipts"] is not None else None
                ),
                "schedule_total_amount": float(row["schedule_total_amount"] or 0.0),
                "mismatch_amount": float(row["mismatch_amount"]) if row["mismatch_amount"] is not None else None,
                "mismatch_ratio": float(row["mismatch_ratio"]) if row["mismatch_ratio"] is not None else None,
                "flag_status": row["flag_status"],
                "schedule_committee_count": int(row["schedule_committee_count"] or 0),
                "schedule_contribution_count": int(row["schedule_contribution_count"] or 0),
                "schedule_donor_count": int(row["schedule_donor_count"] or 0),
                "earliest_contribution_date": row["earliest_contribution_date"],
                "latest_contribution_date": row["latest_contribution_date"],
                "coverage_end_date": row["coverage_end_date"],
                "transaction_coverage_date": row["transaction_coverage_date"],
                "totals_updated_at": row["totals_updated_at"],
                "tracked_committees": int(row["tracked_committees"] or 0),
                "completed_committees": int(row["completed_committees"] or 0),
                "backfill_updated_at": row["backfill_updated_at"],
            }
        )

    return {
        "rows": output_rows,
        "status_counts": status_counts,
        "total_rows": total_rows,
        "status": status_value,
        "cycle": cycle_value,
        "min_abs_diff": min_abs_diff_value,
        "tolerance": tolerance_value,
    }


def get_federal_disbursement_mismatch_flags(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    status: str = "flagged",
    min_abs_diff: float = 1.0,
    tolerance: float = 0.01,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Return candidate-level reported disbursements vs synced Schedule B mismatch flags."""
    if not _table_exists(conn, "fec_candidate_match"):
        return {
            "rows": [],
            "status_counts": {},
            "total_rows": 0,
            "status": "empty",
            "cycle": cycle,
            "min_abs_diff": float(min_abs_diff),
            "tolerance": float(tolerance),
        }

    _ensure_fec_schedule_b_backfill_state_table(conn)

    status_value = _clean_text(status).lower() or "flagged"
    valid_statuses = {
        "flagged",
        "all",
        "matched",
        "missing_schedule_b_rows",
        "schedule_b_exceeds_reported",
        "missing_reported_total",
    }
    if status_value not in valid_statuses:
        status_value = "flagged"

    cycle_value = int(cycle) if cycle is not None else None
    tolerance_value = max(0.0, float(tolerance))
    min_abs_diff_value = max(0.0, float(min_abs_diff))

    schedule_source_sql = (
        """
        SELECT
            candidate_id,
            cycle,
            COUNT(DISTINCT sub_id) AS disbursement_count,
            COUNT(DISTINCT committee_id) AS committee_count,
            COALESCE(SUM(disbursement_amount), 0) AS schedule_total_amount,
            MIN(disbursement_date) AS earliest_disbursement_date,
            MAX(disbursement_date) AS latest_disbursement_date
        FROM fec_schedule_b_disbursements
        GROUP BY candidate_id, cycle
        """
        if _table_exists(conn, "fec_schedule_b_disbursements")
        else """
        SELECT
            NULL AS candidate_id,
            NULL AS cycle,
            0 AS disbursement_count,
            0 AS committee_count,
            0.0 AS schedule_total_amount,
            NULL AS earliest_disbursement_date,
            NULL AS latest_disbursement_date
        WHERE 1 = 0
        """
    )
    totals_source_sql = (
        """
        SELECT
            candidate_id,
            cycle,
            disbursements,
            coverage_end_date,
            transaction_coverage_date,
            updated_at
        FROM fec_candidate_cycle_totals
        """
        if _table_exists(conn, "fec_candidate_cycle_totals")
        else """
        SELECT
            NULL AS candidate_id,
            NULL AS cycle,
            NULL AS disbursements,
            NULL AS coverage_end_date,
            NULL AS transaction_coverage_date,
            NULL AS updated_at
        WHERE 1 = 0
        """
    )

    cte_sql = f"""
        WITH candidate_meta AS (
            SELECT
                fec_candidate_id AS candidate_id,
                cycle,
                MAX(COALESCE(NULLIF(fec_name, ''), NULLIF(candidate_name, ''), fec_candidate_id)) AS candidate_name,
                MAX(COALESCE(NULLIF(candidate_name, ''), NULLIF(fec_name, ''), fec_candidate_id)) AS seed_candidate_name,
                MAX(office) AS office,
                MAX(district) AS district,
                MAX(party) AS party,
                MAX(match_status) AS match_status
            FROM fec_candidate_match
            WHERE fec_candidate_id IS NOT NULL
            GROUP BY fec_candidate_id, cycle
        ),
        schedule_totals AS (
            {schedule_source_sql}
        ),
        reported_totals AS (
            {totals_source_sql}
        ),
        backfill_progress AS (
            SELECT
                candidate_id,
                cycle,
                COUNT(*) AS tracked_committees,
                SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_committees,
                MAX(updated_at) AS backfill_updated_at
            FROM fec_schedule_b_backfill_state
            GROUP BY candidate_id, cycle
        ),
        rollup AS (
            SELECT
                cm.candidate_id,
                cm.cycle,
                cm.candidate_name,
                cm.seed_candidate_name,
                cm.office,
                cm.district,
                cm.party,
                cm.match_status,
                rt.disbursements AS reported_total_disbursements,
                COALESCE(st.schedule_total_amount, 0.0) AS schedule_total_amount,
                CASE
                    WHEN rt.disbursements IS NULL THEN NULL
                    ELSE rt.disbursements - COALESCE(st.schedule_total_amount, 0.0)
                END AS mismatch_amount,
                CASE
                    WHEN rt.disbursements IS NULL OR ABS(rt.disbursements) < 0.0000001 THEN NULL
                    ELSE (rt.disbursements - COALESCE(st.schedule_total_amount, 0.0)) / rt.disbursements
                END AS mismatch_ratio,
                CASE
                    WHEN rt.disbursements IS NULL THEN 'missing_reported_total'
                    WHEN ABS(rt.disbursements - COALESCE(st.schedule_total_amount, 0.0)) <= ? THEN 'matched'
                    WHEN rt.disbursements > COALESCE(st.schedule_total_amount, 0.0) THEN 'missing_schedule_b_rows'
                    ELSE 'schedule_b_exceeds_reported'
                END AS flag_status,
                COALESCE(st.committee_count, 0) AS schedule_committee_count,
                COALESCE(st.disbursement_count, 0) AS schedule_disbursement_count,
                st.earliest_disbursement_date,
                st.latest_disbursement_date,
                rt.coverage_end_date,
                rt.transaction_coverage_date,
                rt.updated_at AS totals_updated_at,
                COALESCE(bp.tracked_committees, 0) AS tracked_committees,
                COALESCE(bp.completed_committees, 0) AS completed_committees,
                bp.backfill_updated_at
            FROM candidate_meta cm
            LEFT JOIN reported_totals rt
              ON rt.candidate_id = cm.candidate_id
             AND rt.cycle = cm.cycle
            LEFT JOIN schedule_totals st
              ON st.candidate_id = cm.candidate_id
             AND st.cycle = cm.cycle
            LEFT JOIN backfill_progress bp
              ON bp.candidate_id = cm.candidate_id
             AND bp.cycle = cm.cycle
        )
    """

    filter_clauses = ["(? IS NULL OR cycle = ?)"]
    filter_params: list[Any] = [cycle_value, cycle_value]

    if status_value == "flagged":
        filter_clauses.append(
            "(flag_status != 'matched' AND (flag_status = 'missing_reported_total' OR ABS(COALESCE(mismatch_amount, 0.0)) >= ?))"
        )
        filter_params.append(min_abs_diff_value)
    elif status_value == "all":
        if min_abs_diff_value > 0:
            filter_clauses.append("(flag_status = 'missing_reported_total' OR ABS(COALESCE(mismatch_amount, 0.0)) >= ?)")
            filter_params.append(min_abs_diff_value)
    elif status_value == "matched":
        filter_clauses.append("flag_status = 'matched'")
    else:
        filter_clauses.append("flag_status = ?")
        filter_params.append(status_value)
        if status_value in {"missing_schedule_b_rows", "schedule_b_exceeds_reported"} and min_abs_diff_value > 0:
            filter_clauses.append("ABS(COALESCE(mismatch_amount, 0.0)) >= ?")
            filter_params.append(min_abs_diff_value)

    where_sql = f"WHERE {' AND '.join(filter_clauses)}"

    rows = conn.execute(
        f"""
        {cte_sql}
        SELECT
            candidate_id,
            cycle,
            candidate_name,
            seed_candidate_name,
            office,
            district,
            party,
            match_status,
            reported_total_disbursements,
            schedule_total_amount,
            mismatch_amount,
            mismatch_ratio,
            flag_status,
            schedule_committee_count,
            schedule_disbursement_count,
            earliest_disbursement_date,
            latest_disbursement_date,
            coverage_end_date,
            transaction_coverage_date,
            totals_updated_at,
            tracked_committees,
            completed_committees,
            backfill_updated_at
        FROM rollup
        {where_sql}
        ORDER BY
            CASE WHEN mismatch_amount IS NULL THEN -1 ELSE ABS(mismatch_amount) END DESC,
            candidate_name ASC
        LIMIT ? OFFSET ?
        """,
        [tolerance_value, *filter_params, max(1, int(limit)), max(0, int(offset))],
    ).fetchall()

    total_row = conn.execute(
        f"""
        {cte_sql}
        SELECT COUNT(*) AS count
        FROM rollup
        {where_sql}
        """,
        [tolerance_value, *filter_params],
    ).fetchone()
    total_rows = int(total_row["count"] or 0) if total_row else 0

    status_counts_rows = conn.execute(
        f"""
        {cte_sql}
        SELECT
            flag_status,
            COUNT(*) AS count
        FROM rollup
        WHERE (? IS NULL OR cycle = ?)
        GROUP BY flag_status
        ORDER BY count DESC, flag_status ASC
        """,
        [tolerance_value, cycle_value, cycle_value],
    ).fetchall()
    status_counts = {row["flag_status"]: int(row["count"] or 0) for row in status_counts_rows}

    output_rows: list[dict] = []
    for row in rows:
        output_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "cycle": int(row["cycle"] or 0),
                "candidate_name": row["candidate_name"] or row["seed_candidate_name"] or row["candidate_id"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "match_status": row["match_status"],
                "reported_total_disbursements": (
                    float(row["reported_total_disbursements"])
                    if row["reported_total_disbursements"] is not None
                    else None
                ),
                "schedule_total_amount": float(row["schedule_total_amount"] or 0.0),
                "mismatch_amount": float(row["mismatch_amount"]) if row["mismatch_amount"] is not None else None,
                "mismatch_ratio": float(row["mismatch_ratio"]) if row["mismatch_ratio"] is not None else None,
                "flag_status": row["flag_status"],
                "schedule_committee_count": int(row["schedule_committee_count"] or 0),
                "schedule_disbursement_count": int(row["schedule_disbursement_count"] or 0),
                "earliest_disbursement_date": row["earliest_disbursement_date"],
                "latest_disbursement_date": row["latest_disbursement_date"],
                "coverage_end_date": row["coverage_end_date"],
                "transaction_coverage_date": row["transaction_coverage_date"],
                "totals_updated_at": row["totals_updated_at"],
                "tracked_committees": int(row["tracked_committees"] or 0),
                "completed_committees": int(row["completed_committees"] or 0),
                "backfill_updated_at": row["backfill_updated_at"],
            }
        )

    return {
        "rows": output_rows,
        "status_counts": status_counts,
        "total_rows": total_rows,
        "status": status_value,
        "cycle": cycle_value,
        "min_abs_diff": min_abs_diff_value,
        "tolerance": tolerance_value,
    }


def _upsert_schedule_a_backfill_state(
    conn: sqlite3.Connection,
    *,
    committee_id: str,
    cycle: int,
    candidate_id: str | None,
    candidate_name: str | None,
    next_last_index: str | None,
    next_last_receipt_date: str | None,
    completed: bool,
    pages_processed_delta: int,
    contributions_upserted_delta: int,
    api_calls_delta: int,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fec_schedule_a_backfill_state (
            committee_id,
            cycle,
            candidate_id,
            candidate_name,
            next_last_index,
            next_last_receipt_date,
            completed,
            pages_processed_total,
            contributions_upserted_total,
            api_calls_total,
            last_error,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(committee_id, cycle) DO UPDATE SET
            candidate_id = excluded.candidate_id,
            candidate_name = excluded.candidate_name,
            next_last_index = excluded.next_last_index,
            next_last_receipt_date = excluded.next_last_receipt_date,
            completed = excluded.completed,
            pages_processed_total = fec_schedule_a_backfill_state.pages_processed_total + excluded.pages_processed_total,
            contributions_upserted_total = (
                fec_schedule_a_backfill_state.contributions_upserted_total + excluded.contributions_upserted_total
            ),
            api_calls_total = fec_schedule_a_backfill_state.api_calls_total + excluded.api_calls_total,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            committee_id,
            int(cycle),
            candidate_id,
            candidate_name,
            _clean_text(next_last_index) or None,
            _clean_text(next_last_receipt_date) or None,
            1 if completed else 0,
            max(0, int(pages_processed_delta)),
            max(0, int(contributions_upserted_delta)),
            max(0, int(api_calls_delta)),
            _clean_text(last_error)[:500] if last_error else None,
        ),
    )


def _upsert_schedule_b_backfill_state(
    conn: sqlite3.Connection,
    *,
    committee_id: str,
    cycle: int,
    candidate_id: str | None,
    candidate_name: str | None,
    next_last_index: str | None,
    next_last_disbursement_date: str | None,
    completed: bool,
    pages_processed_delta: int,
    disbursements_upserted_delta: int,
    api_calls_delta: int,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fec_schedule_b_backfill_state (
            committee_id,
            cycle,
            candidate_id,
            candidate_name,
            next_last_index,
            next_last_disbursement_date,
            completed,
            pages_processed_total,
            disbursements_upserted_total,
            api_calls_total,
            last_error,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(committee_id, cycle) DO UPDATE SET
            candidate_id = excluded.candidate_id,
            candidate_name = excluded.candidate_name,
            next_last_index = excluded.next_last_index,
            next_last_disbursement_date = excluded.next_last_disbursement_date,
            completed = excluded.completed,
            pages_processed_total = fec_schedule_b_backfill_state.pages_processed_total + excluded.pages_processed_total,
            disbursements_upserted_total = (
                fec_schedule_b_backfill_state.disbursements_upserted_total + excluded.disbursements_upserted_total
            ),
            api_calls_total = fec_schedule_b_backfill_state.api_calls_total + excluded.api_calls_total,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            committee_id,
            int(cycle),
            candidate_id,
            candidate_name,
            _clean_text(next_last_index) or None,
            _clean_text(next_last_disbursement_date) or None,
            1 if completed else 0,
            max(0, int(pages_processed_delta)),
            max(0, int(disbursements_upserted_delta)),
            max(0, int(api_calls_delta)),
            _clean_text(last_error)[:500] if last_error else None,
        ),
    )


def _upsert_schedule_e_backfill_state(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    cycle: int,
    candidate_name: str | None,
    next_last_index: str | None,
    next_last_expenditure_date: str | None,
    completed: bool,
    pages_processed_delta: int,
    expenditures_upserted_delta: int,
    api_calls_delta: int,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fec_schedule_e_backfill_state (
            candidate_id,
            cycle,
            candidate_name,
            next_last_index,
            next_last_expenditure_date,
            completed,
            pages_processed_total,
            expenditures_upserted_total,
            api_calls_total,
            last_error,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(candidate_id, cycle) DO UPDATE SET
            candidate_name = excluded.candidate_name,
            next_last_index = excluded.next_last_index,
            next_last_expenditure_date = excluded.next_last_expenditure_date,
            completed = excluded.completed,
            pages_processed_total = fec_schedule_e_backfill_state.pages_processed_total + excluded.pages_processed_total,
            expenditures_upserted_total = (
                fec_schedule_e_backfill_state.expenditures_upserted_total + excluded.expenditures_upserted_total
            ),
            api_calls_total = fec_schedule_e_backfill_state.api_calls_total + excluded.api_calls_total,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            candidate_id,
            int(cycle),
            candidate_name,
            _clean_text(next_last_index) or None,
            _clean_text(next_last_expenditure_date) or None,
            1 if completed else 0,
            max(0, int(pages_processed_delta)),
            max(0, int(expenditures_upserted_delta)),
            max(0, int(api_calls_delta)),
            _clean_text(last_error)[:500] if last_error else None,
        ),
    )


def backfill_fec_missing_schedule_a(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    cycle: int,
    max_calls: int = 1000,
    per_page: int = 100,
    max_pages_per_committee: int = 25,
    min_abs_gap: float = 500.0,
    tolerance: float = 0.01,
    include_completed: bool = False,
    refresh_cache: bool = False,
    max_committees: int | None = None,
    client: FecApiClient | None = None,
) -> dict:
    """Backfill Schedule A rows for candidates with reported-vs-synced receipt gaps.

    Designed for hourly catch-up runs under API call budgets (for example, 1000 calls/hour).
    """
    if not api_key:
        raise ValueError("FEC API key is required")
    if not _table_exists(conn, "raw_extractions"):
        raise RuntimeError("raw_extractions table is required for FEC backfill")
    if not _table_exists(conn, "fec_candidate_committees"):
        raise RuntimeError("fec_candidate_committees table is required for FEC backfill")

    _ensure_fec_schedule_a_backfill_state_table(conn)
    conn.commit()

    effective_cycle = int(cycle)
    call_budget = max(1, int(max_calls))
    per_page_value = min(max(1, int(per_page)), 100)
    pages_per_committee_cap = max(1, int(max_pages_per_committee))
    min_gap_value = max(0.0, float(min_abs_gap))
    tolerance_value = max(0.0, float(tolerance))

    mismatch = get_federal_receipt_mismatch_flags(
        conn,
        cycle=effective_cycle,
        status="missing_schedule_rows",
        min_abs_diff=max(min_gap_value, tolerance_value),
        tolerance=tolerance_value,
        limit=10000,
        offset=0,
    )
    candidate_rows = mismatch.get("rows") or []
    candidate_ids = sorted({row["candidate_id"] for row in candidate_rows if _clean_text(row.get("candidate_id"))})

    if not candidate_ids:
        return {
            "cycle": effective_cycle,
            "candidate_gap_count": 0,
            "committees_selected": 0,
            "committees_processed": 0,
            "committees_completed": 0,
            "committees_remaining": 0,
            "pages_processed": 0,
            "contributions_upserted": 0,
            "api_calls_made": 0,
            "call_budget_reached": False,
            "max_calls": call_budget,
            "min_abs_gap": min_gap_value,
            "tolerance": tolerance_value,
            "include_completed": bool(include_completed),
            "refresh_cache": bool(refresh_cache),
        }

    placeholders = ",".join(["?"] * len(candidate_ids))
    committee_rows = conn.execute(
        f"""
        SELECT
            c.candidate_id,
            c.cycle,
            c.committee_id,
            c.committee_name,
            COALESCE(m.fec_name, m.candidate_name, c.candidate_id) AS candidate_name,
            COALESCE(bs.next_last_index, '') AS next_last_index,
            COALESCE(bs.next_last_receipt_date, '') AS next_last_receipt_date,
            COALESCE(bs.completed, 0) AS completed,
            bs.updated_at AS backfill_updated_at
        FROM fec_candidate_committees c
        LEFT JOIN (
            SELECT
                fec_candidate_id AS candidate_id,
                cycle,
                MAX(fec_name) AS fec_name,
                MAX(candidate_name) AS candidate_name
            FROM fec_candidate_match
            WHERE fec_candidate_id IS NOT NULL
            GROUP BY fec_candidate_id, cycle
        ) m
          ON m.candidate_id = c.candidate_id
         AND m.cycle = c.cycle
        LEFT JOIN fec_schedule_a_backfill_state bs
          ON bs.committee_id = c.committee_id
         AND bs.cycle = c.cycle
        WHERE c.cycle = ?
          AND c.candidate_id IN ({placeholders})
          AND c.committee_id IS NOT NULL
        ORDER BY
            COALESCE(bs.completed, 0) ASC,
            CASE WHEN bs.updated_at IS NULL THEN 0 ELSE 1 END ASC,
            bs.updated_at ASC,
            c.candidate_id ASC,
            c.committee_id ASC
        """,
        [effective_cycle, *candidate_ids],
    ).fetchall()

    queue = list(committee_rows)
    if max_committees is not None:
        queue = queue[: max(0, int(max_committees))]

    api_client = client or FecApiClient(api_key=api_key)
    use_cache = not bool(refresh_cache)

    api_calls_made = 0
    pages_processed = 0
    contributions_upserted = 0
    committees_processed = 0
    committees_completed = 0
    call_budget_reached = False

    for committee in queue:
        committee_id = _clean_text(committee["committee_id"])
        candidate_id = _clean_text(committee["candidate_id"])
        candidate_name = _clean_text(committee["candidate_name"]) or candidate_id
        committee_name = _clean_text(committee["committee_name"]) or committee_id
        if not committee_id or not candidate_id:
            continue

        already_completed = int(committee["completed"] or 0) == 1
        if already_completed and not include_completed:
            continue

        committees_processed += 1
        committee_pages = 0
        committee_contributions = 0
        committee_api_calls = 0
        next_last_index = _clean_text(committee["next_last_index"])
        next_last_receipt_date = _clean_text(committee["next_last_receipt_date"])
        committee_completed = False
        last_error = None
        seen_tokens: set[str] = set()

        while committee_pages < pages_per_committee_cap and api_calls_made < call_budget:
            params: dict[str, Any] = {
                "committee_id": committee_id,
                "two_year_transaction_period": effective_cycle,
                "per_page": per_page_value,
                "sort": "-contribution_receipt_date",
            }
            if next_last_index:
                params["last_index"] = next_last_index
            if next_last_receipt_date:
                params["last_contribution_receipt_date"] = next_last_receipt_date

            try:
                payload, source_identifier, from_cache = _request_with_cache(
                    conn,
                    api_client,
                    endpoint="/schedules/schedule_a/",
                    params=params,
                    use_cache=use_cache,
                )
            except Exception as exc:  # noqa: BLE001 - retain per-committee errors and continue queue
                last_error = str(exc)
                break

            if not from_cache:
                api_calls_made += 1
                committee_api_calls += 1

            pages_processed += 1
            committee_pages += 1

            results = payload.get("results") or []
            if not results:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break

            upserted_rows = _upsert_schedule_rows(
                conn,
                rows=results,
                cycle=effective_cycle,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                committee_id=committee_id,
                default_committee_name=committee_name,
                api_source_identifier=source_identifier,
            )
            committee_contributions += upserted_rows
            contributions_upserted += upserted_rows
            conn.commit()

            pagination = payload.get("pagination") or {}
            last_indexes = pagination.get("last_indexes") or {}
            fetched_last_index = _clean_text(last_indexes.get("last_index"))
            fetched_last_receipt = _clean_text(last_indexes.get("last_contribution_receipt_date"))

            if not fetched_last_index:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break

            token = f"{fetched_last_index}|{fetched_last_receipt}"
            if token in seen_tokens:
                committee_completed = True
                next_last_index = ""
                next_last_receipt_date = ""
                break
            seen_tokens.add(token)

            next_last_index = fetched_last_index
            next_last_receipt_date = fetched_last_receipt

            if api_calls_made >= call_budget:
                call_budget_reached = True
                break

        _upsert_schedule_a_backfill_state(
            conn,
            committee_id=committee_id,
            cycle=effective_cycle,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            next_last_index=next_last_index if not committee_completed else None,
            next_last_receipt_date=next_last_receipt_date if not committee_completed else None,
            completed=committee_completed,
            pages_processed_delta=committee_pages,
            contributions_upserted_delta=committee_contributions,
            api_calls_delta=committee_api_calls,
            last_error=last_error,
        )
        conn.commit()

        if committee_completed:
            committees_completed += 1

        if api_calls_made >= call_budget:
            call_budget_reached = True
            break

    selected_committee_ids = [row["committee_id"] for row in queue if _clean_text(row["committee_id"])]
    selected_unique = list(dict.fromkeys(selected_committee_ids))
    completed_in_state = 0
    if selected_unique:
        selected_placeholders = ",".join(["?"] * len(selected_unique))
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM fec_schedule_a_backfill_state
            WHERE cycle = ?
              AND committee_id IN ({selected_placeholders})
              AND completed = 1
            """,
            [effective_cycle, *selected_unique],
        ).fetchone()
        completed_in_state = int(row["count"] or 0) if row else 0

    committees_selected = len(selected_unique)
    committees_remaining = max(0, committees_selected - completed_in_state)

    return {
        "cycle": effective_cycle,
        "candidate_gap_count": len(candidate_ids),
        "committees_selected": committees_selected,
        "committees_processed": committees_processed,
        "committees_completed": committees_completed,
        "committees_remaining": committees_remaining,
        "pages_processed": pages_processed,
        "contributions_upserted": contributions_upserted,
        "api_calls_made": api_calls_made,
        "call_budget_reached": bool(call_budget_reached),
        "max_calls": call_budget,
        "min_abs_gap": min_gap_value,
        "tolerance": tolerance_value,
        "include_completed": bool(include_completed),
        "refresh_cache": bool(refresh_cache),
    }


def backfill_fec_schedule_b(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    cycle: int,
    max_calls: int = 1000,
    per_page: int = 100,
    max_pages_per_committee: int = 25,
    include_completed: bool = False,
    refresh_cache: bool = False,
    include_all_committees: bool = True,
    max_committees: int | None = None,
    client: FecApiClient | None = None,
) -> dict:
    """Backfill FEC Schedule B disbursements for candidate committees."""
    if not api_key:
        raise ValueError("FEC API key is required")
    if not _table_exists(conn, "raw_extractions"):
        raise RuntimeError("raw_extractions table is required for FEC Schedule B backfill")
    if not _table_exists(conn, "fec_candidate_committees"):
        raise RuntimeError("fec_candidate_committees table is required for FEC Schedule B backfill")

    _ensure_fec_schedule_b_backfill_state_table(conn)
    conn.commit()

    effective_cycle = int(cycle)
    call_budget = max(1, int(max_calls))
    per_page_value = min(max(1, int(per_page)), 100)
    pages_per_committee_cap = max(1, int(max_pages_per_committee))

    committee_rows = conn.execute(
        """
        SELECT
            c.candidate_id,
            c.cycle,
            c.committee_id,
            c.committee_name,
            COALESCE(m.fec_name, m.candidate_name, c.candidate_id) AS candidate_name,
            COALESCE(bs.next_last_index, '') AS next_last_index,
            COALESCE(bs.next_last_disbursement_date, '') AS next_last_disbursement_date,
            COALESCE(bs.completed, 0) AS completed,
            bs.updated_at AS backfill_updated_at
        FROM fec_candidate_committees c
        LEFT JOIN (
            SELECT
                fec_candidate_id AS candidate_id,
                cycle,
                MAX(fec_name) AS fec_name,
                MAX(candidate_name) AS candidate_name
            FROM fec_candidate_match
            WHERE fec_candidate_id IS NOT NULL
            GROUP BY fec_candidate_id, cycle
        ) m
          ON m.candidate_id = c.candidate_id
         AND m.cycle = c.cycle
        LEFT JOIN fec_schedule_b_backfill_state bs
          ON bs.committee_id = c.committee_id
         AND bs.cycle = c.cycle
        WHERE c.cycle = ?
          AND c.committee_id IS NOT NULL
          AND (? = 1 OR c.is_principal = 1)
        ORDER BY
            COALESCE(bs.completed, 0) ASC,
            CASE WHEN bs.updated_at IS NULL THEN 0 ELSE 1 END ASC,
            bs.updated_at ASC,
            c.candidate_id ASC,
            c.committee_id ASC
        """,
        (effective_cycle, 1 if include_all_committees else 0),
    ).fetchall()

    queue = list(committee_rows)
    if max_committees is not None:
        queue = queue[: max(0, int(max_committees))]

    api_client = client or FecApiClient(api_key=api_key)
    use_cache = not bool(refresh_cache)

    api_calls_made = 0
    pages_processed = 0
    disbursements_upserted = 0
    committees_processed = 0
    committees_completed = 0
    call_budget_reached = False

    for committee in queue:
        committee_id = _clean_text(committee["committee_id"])
        candidate_id = _clean_text(committee["candidate_id"])
        candidate_name = _clean_text(committee["candidate_name"]) or candidate_id
        committee_name = _clean_text(committee["committee_name"]) or committee_id
        if not committee_id or not candidate_id:
            continue

        already_completed = int(committee["completed"] or 0) == 1
        if already_completed and not include_completed:
            continue

        committees_processed += 1
        committee_pages = 0
        committee_disbursements = 0
        committee_api_calls = 0
        next_last_index = _clean_text(committee["next_last_index"])
        next_last_disbursement_date = _clean_text(committee["next_last_disbursement_date"])
        committee_completed = False
        last_error = None
        seen_tokens: set[str] = set()

        while committee_pages < pages_per_committee_cap and api_calls_made < call_budget:
            params: dict[str, Any] = {
                "committee_id": committee_id,
                "two_year_transaction_period": effective_cycle,
                "per_page": per_page_value,
                "sort": "-disbursement_date",
            }
            if next_last_index:
                params["last_index"] = next_last_index
            if next_last_disbursement_date:
                params["last_disbursement_date"] = next_last_disbursement_date

            try:
                payload, source_identifier, from_cache = _request_with_cache(
                    conn,
                    api_client,
                    endpoint="/schedules/schedule_b/",
                    params=params,
                    use_cache=use_cache,
                )
            except Exception as exc:  # noqa: BLE001 - retain per-committee error and continue
                last_error = str(exc)
                break

            if not from_cache:
                api_calls_made += 1
                committee_api_calls += 1

            pages_processed += 1
            committee_pages += 1

            results = payload.get("results") or []
            if not results:
                committee_completed = True
                next_last_index = ""
                next_last_disbursement_date = ""
                break

            upserted_rows = _upsert_schedule_b_rows(
                conn,
                rows=results,
                cycle=effective_cycle,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                committee_id=committee_id,
                default_committee_name=committee_name,
                api_source_identifier=source_identifier,
            )
            committee_disbursements += upserted_rows
            disbursements_upserted += upserted_rows
            conn.commit()

            pagination = payload.get("pagination") or {}
            last_indexes = pagination.get("last_indexes") or {}
            fetched_last_index = _clean_text(last_indexes.get("last_index"))
            fetched_last_disbursement_date = _clean_text(
                last_indexes.get("last_disbursement_date") or last_indexes.get("last_disbursement_dt")
            )

            if not fetched_last_index:
                committee_completed = True
                next_last_index = ""
                next_last_disbursement_date = ""
                break

            token = f"{fetched_last_index}|{fetched_last_disbursement_date}"
            if token in seen_tokens:
                committee_completed = True
                next_last_index = ""
                next_last_disbursement_date = ""
                break
            seen_tokens.add(token)

            next_last_index = fetched_last_index
            next_last_disbursement_date = fetched_last_disbursement_date

            if api_calls_made >= call_budget:
                call_budget_reached = True
                break

        _upsert_schedule_b_backfill_state(
            conn,
            committee_id=committee_id,
            cycle=effective_cycle,
            candidate_id=candidate_id,
            candidate_name=candidate_name,
            next_last_index=next_last_index if not committee_completed else None,
            next_last_disbursement_date=(
                next_last_disbursement_date if not committee_completed else None
            ),
            completed=committee_completed,
            pages_processed_delta=committee_pages,
            disbursements_upserted_delta=committee_disbursements,
            api_calls_delta=committee_api_calls,
            last_error=last_error,
        )
        conn.commit()

        if committee_completed:
            committees_completed += 1

        if api_calls_made >= call_budget:
            call_budget_reached = True
            break

    selected_committee_ids = [row["committee_id"] for row in queue if _clean_text(row["committee_id"])]
    selected_unique = list(dict.fromkeys(selected_committee_ids))
    completed_in_state = 0
    if selected_unique:
        selected_placeholders = ",".join(["?"] * len(selected_unique))
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM fec_schedule_b_backfill_state
            WHERE cycle = ?
              AND committee_id IN ({selected_placeholders})
              AND completed = 1
            """,
            [effective_cycle, *selected_unique],
        ).fetchone()
        completed_in_state = int(row["count"] or 0) if row else 0

    committees_selected = len(selected_unique)
    committees_remaining = max(0, committees_selected - completed_in_state)

    return {
        "cycle": effective_cycle,
        "committees_selected": committees_selected,
        "committees_processed": committees_processed,
        "committees_completed": committees_completed,
        "committees_remaining": committees_remaining,
        "pages_processed": pages_processed,
        "disbursements_upserted": disbursements_upserted,
        "api_calls_made": api_calls_made,
        "call_budget_reached": bool(call_budget_reached),
        "max_calls": call_budget,
        "include_completed": bool(include_completed),
        "include_all_committees": bool(include_all_committees),
        "refresh_cache": bool(refresh_cache),
    }


def backfill_fec_schedule_e(
    conn: sqlite3.Connection,
    *,
    api_key: str,
    cycle: int,
    max_calls: int = 1000,
    per_page: int = 100,
    max_pages_per_candidate: int = 25,
    include_completed: bool = False,
    refresh_cache: bool = False,
    max_candidates: int | None = None,
    client: FecApiClient | None = None,
) -> dict:
    """Backfill FEC Schedule E independent expenditure rows for matched IL candidates."""
    if not api_key:
        raise ValueError("FEC API key is required")
    if not _table_exists(conn, "raw_extractions"):
        raise RuntimeError("raw_extractions table is required for FEC Schedule E backfill")
    if not _table_exists(conn, "fec_candidate_match"):
        raise RuntimeError("fec_candidate_match table is required for FEC Schedule E backfill")

    _ensure_fec_schedule_e_backfill_state_table(conn)
    conn.commit()

    effective_cycle = int(cycle)
    call_budget = max(1, int(max_calls))
    per_page_value = min(max(1, int(per_page)), 100)
    pages_per_candidate_cap = max(1, int(max_pages_per_candidate))

    candidate_rows = conn.execute(
        """
        SELECT
            m.fec_candidate_id AS candidate_id,
            COALESCE(m.fec_name, m.candidate_name, m.fec_candidate_id) AS candidate_name,
            COALESCE(bs.next_last_index, '') AS next_last_index,
            COALESCE(bs.next_last_expenditure_date, '') AS next_last_expenditure_date,
            COALESCE(bs.completed, 0) AS completed,
            bs.updated_at AS backfill_updated_at
        FROM fec_candidate_match m
        LEFT JOIN fec_schedule_e_backfill_state bs
          ON bs.candidate_id = m.fec_candidate_id
         AND bs.cycle = m.cycle
        WHERE m.cycle = ?
          AND m.fec_candidate_id IS NOT NULL
        GROUP BY m.fec_candidate_id, m.cycle
        ORDER BY
            COALESCE(bs.completed, 0) ASC,
            CASE WHEN bs.updated_at IS NULL THEN 0 ELSE 1 END ASC,
            bs.updated_at ASC,
            candidate_name ASC
        """,
        (effective_cycle,),
    ).fetchall()

    queue = list(candidate_rows)
    if max_candidates is not None:
        queue = queue[: max(0, int(max_candidates))]

    api_client = client or FecApiClient(api_key=api_key)
    use_cache = not bool(refresh_cache)

    api_calls_made = 0
    pages_processed = 0
    expenditures_upserted = 0
    candidates_processed = 0
    candidates_completed = 0
    call_budget_reached = False

    for candidate in queue:
        candidate_id = _clean_text(candidate["candidate_id"])
        candidate_name = _clean_text(candidate["candidate_name"]) or candidate_id
        if not candidate_id:
            continue

        already_completed = int(candidate["completed"] or 0) == 1
        if already_completed and not include_completed:
            continue

        candidates_processed += 1
        candidate_pages = 0
        candidate_expenditures = 0
        candidate_api_calls = 0
        next_last_index = _clean_text(candidate["next_last_index"])
        next_last_expenditure_date = _clean_text(candidate["next_last_expenditure_date"])
        candidate_completed = False
        last_error = None
        seen_tokens: set[str] = set()

        while candidate_pages < pages_per_candidate_cap and api_calls_made < call_budget:
            params: dict[str, Any] = {
                "candidate_id": candidate_id,
                "two_year_transaction_period": effective_cycle,
                "per_page": per_page_value,
                "sort": "-expenditure_date",
            }
            if next_last_index:
                params["last_index"] = next_last_index
            if next_last_expenditure_date:
                params["last_expenditure_date"] = next_last_expenditure_date

            try:
                payload, source_identifier, from_cache = _request_with_cache(
                    conn,
                    api_client,
                    endpoint="/schedules/schedule_e/",
                    params=params,
                    use_cache=use_cache,
                )
            except Exception as exc:  # noqa: BLE001 - retain per-candidate error and continue
                last_error = str(exc)
                break

            if not from_cache:
                api_calls_made += 1
                candidate_api_calls += 1

            pages_processed += 1
            candidate_pages += 1

            results = payload.get("results") or []
            if not results:
                candidate_completed = True
                next_last_index = ""
                next_last_expenditure_date = ""
                break

            upserted_rows = _upsert_schedule_e_rows(
                conn,
                rows=results,
                cycle=effective_cycle,
                candidate_id=candidate_id,
                candidate_name=candidate_name,
                api_source_identifier=source_identifier,
            )
            candidate_expenditures += upserted_rows
            expenditures_upserted += upserted_rows
            conn.commit()

            pagination = payload.get("pagination") or {}
            last_indexes = pagination.get("last_indexes") or {}
            fetched_last_index = _clean_text(last_indexes.get("last_index"))
            fetched_last_expenditure_date = _clean_text(
                last_indexes.get("last_expenditure_date") or last_indexes.get("last_expenditure_dt")
            )

            if not fetched_last_index:
                candidate_completed = True
                next_last_index = ""
                next_last_expenditure_date = ""
                break

            token = f"{fetched_last_index}|{fetched_last_expenditure_date}"
            if token in seen_tokens:
                candidate_completed = True
                next_last_index = ""
                next_last_expenditure_date = ""
                break
            seen_tokens.add(token)

            next_last_index = fetched_last_index
            next_last_expenditure_date = fetched_last_expenditure_date

            if api_calls_made >= call_budget:
                call_budget_reached = True
                break

        _upsert_schedule_e_backfill_state(
            conn,
            candidate_id=candidate_id,
            cycle=effective_cycle,
            candidate_name=candidate_name,
            next_last_index=next_last_index if not candidate_completed else None,
            next_last_expenditure_date=(
                next_last_expenditure_date if not candidate_completed else None
            ),
            completed=candidate_completed,
            pages_processed_delta=candidate_pages,
            expenditures_upserted_delta=candidate_expenditures,
            api_calls_delta=candidate_api_calls,
            last_error=last_error,
        )
        conn.commit()

        if candidate_completed:
            candidates_completed += 1

        if api_calls_made >= call_budget:
            call_budget_reached = True
            break

    selected_candidate_ids = [row["candidate_id"] for row in queue if _clean_text(row["candidate_id"])]
    selected_unique = list(dict.fromkeys(selected_candidate_ids))
    completed_in_state = 0
    if selected_unique:
        selected_placeholders = ",".join(["?"] * len(selected_unique))
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM fec_schedule_e_backfill_state
            WHERE cycle = ?
              AND candidate_id IN ({selected_placeholders})
              AND completed = 1
            """,
            [effective_cycle, *selected_unique],
        ).fetchone()
        completed_in_state = int(row["count"] or 0) if row else 0

    candidates_selected = len(selected_unique)
    candidates_remaining = max(0, candidates_selected - completed_in_state)

    return {
        "cycle": effective_cycle,
        "candidates_selected": candidates_selected,
        "candidates_processed": candidates_processed,
        "candidates_completed": candidates_completed,
        "candidates_remaining": candidates_remaining,
        "pages_processed": pages_processed,
        "expenditures_upserted": expenditures_upserted,
        "api_calls_made": api_calls_made,
        "call_budget_reached": bool(call_budget_reached),
        "max_calls": call_budget,
        "include_completed": bool(include_completed),
        "refresh_cache": bool(refresh_cache),
    }


def federal_data_available(conn: sqlite3.Connection) -> bool:
    required = [
        "fec_il_candidate_seed",
        "fec_candidate_match",
        "fec_candidate_committees",
        "fec_schedule_a_contributions",
    ]
    return all(_table_exists(conn, table_name) for table_name in required)


def count_federal_candidates(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    search: str | None = None,
    office: str | None = None,
    party: str | None = None,
    match_status: str | None = None,
) -> int:
    where_clauses: list[str] = []
    params: list[Any] = []

    if cycle is not None:
        where_clauses.append("m.cycle = ?")
        params.append(int(cycle))

    search_text = _clean_text(search)
    if search_text:
        where_clauses.append(
            "(COALESCE(m.candidate_name, '') LIKE ? OR COALESCE(m.fec_name, '') LIKE ? OR COALESCE(m.fec_candidate_id, '') LIKE ?)"
        )
        like = f"%{search_text}%"
        params.extend([like, like, like])

    office_text = _clean_text(office)
    if office_text:
        where_clauses.append("COALESCE(m.office, '') LIKE ?")
        params.append(f"%{office_text}%")

    party_text = _clean_text(party)
    if party_text:
        where_clauses.append("COALESCE(m.party, '') LIKE ?")
        params.append(f"%{party_text}%")

    status_text = _clean_text(match_status)
    if status_text:
        where_clauses.append("COALESCE(m.match_status, '') = ?")
        params.append(status_text)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM fec_candidate_match m
        {where_sql}
        """,
        params,
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def list_federal_candidates(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    cycle: int | None = None,
    search: str | None = None,
    office: str | None = None,
    party: str | None = None,
    match_status: str | None = None,
    sort_by: str = "total_amount",
    sort_dir: str = "desc",
) -> list[dict]:
    _ensure_missing_donor_identities(conn, cycle=cycle)

    where_clauses: list[str] = []
    params: list[Any] = []

    if cycle is not None:
        where_clauses.append("m.cycle = ?")
        params.append(int(cycle))

    search_text = _clean_text(search)
    if search_text:
        where_clauses.append(
            "(COALESCE(m.candidate_name, '') LIKE ? OR COALESCE(m.fec_name, '') LIKE ? OR COALESCE(m.fec_candidate_id, '') LIKE ?)"
        )
        like = f"%{search_text}%"
        params.extend([like, like, like])

    office_text = _clean_text(office)
    if office_text:
        where_clauses.append("COALESCE(m.office, '') LIKE ?")
        params.append(f"%{office_text}%")

    party_text = _clean_text(party)
    if party_text:
        where_clauses.append("COALESCE(m.party, '') LIKE ?")
        params.append(f"%{party_text}%")

    status_text = _clean_text(match_status)
    if status_text:
        where_clauses.append("COALESCE(m.match_status, '') = ?")
        params.append(status_text)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sort_map = {
        "candidate_name": "m.candidate_name",
        "office": "m.office",
        "district": "m.district",
        "party": "m.party",
        "match_status": "m.match_status",
        "fec_candidate_id": "m.fec_candidate_id",
        "committee_count": "committee_count",
        "contribution_count": "contribution_count",
        "donor_count": "donor_count",
        "total_amount": "total_amount",
        "latest_contribution_date": "latest_contribution_date",
    }
    order_by = sort_map.get(sort_by, "total_amount")
    direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"

    query = f"""
        SELECT
            m.seed_candidate_key,
            m.candidate_name,
            m.office,
            m.district,
            m.party,
            m.election_stage,
            m.cycle,
            m.fec_candidate_id,
            m.fec_name,
            m.fec_party,
            m.match_status,
            m.match_score,
            COALESCE(cc.committee_count, 0) AS committee_count,
            COALESCE(sa.contribution_count, 0) AS contribution_count,
            COALESCE(sa.donor_count, 0) AS donor_count,
            COALESCE(ft.receipts, COALESCE(sa.schedule_total_amount, 0)) AS total_amount,
            COALESCE(sa.latest_contribution_date, ft.transaction_coverage_date, ft.coverage_end_date)
                AS latest_contribution_date,
            ft.updated_at AS totals_updated_at
        FROM fec_candidate_match m
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT committee_id) AS committee_count
            FROM fec_candidate_committees
            GROUP BY candidate_id, cycle
        ) cc
          ON cc.candidate_id = m.fec_candidate_id
         AND cc.cycle = m.cycle
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT sub_id) AS contribution_count,
                COUNT(
                    DISTINCT COALESCE(
                        NULLIF(donor_entity_key, ''),
                        NULLIF(donor_key, ''),
                        sub_id
                    )
                ) AS donor_count,
                COALESCE(SUM(contribution_receipt_amount), 0) AS schedule_total_amount,
                MAX(contribution_receipt_date) AS latest_contribution_date
            FROM fec_schedule_a_contributions
            GROUP BY candidate_id, cycle
        ) sa
          ON sa.candidate_id = m.fec_candidate_id
         AND sa.cycle = m.cycle
        LEFT JOIN fec_candidate_cycle_totals ft
          ON ft.candidate_id = m.fec_candidate_id
         AND ft.cycle = m.cycle
        {where_sql}
        ORDER BY {order_by} {direction}, m.candidate_name ASC
        LIMIT ? OFFSET ?
    """

    params.extend([max(1, int(limit)), max(0, int(offset))])
    rows = conn.execute(query, params).fetchall()
    output: list[dict] = []
    for row in rows:
        output.append(
            {
                "seed_candidate_key": row["seed_candidate_key"],
                "candidate_name": row["candidate_name"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "election_stage": row["election_stage"],
                "cycle": row["cycle"],
                "fec_candidate_id": row["fec_candidate_id"],
                "fec_name": row["fec_name"],
                "fec_party": row["fec_party"],
                "match_status": row["match_status"],
                "match_score": row["match_score"],
                "committee_count": int(row["committee_count"] or 0),
                "contribution_count": int(row["contribution_count"] or 0),
                "donor_count": int(row["donor_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "latest_contribution_date": row["latest_contribution_date"],
                "totals_updated_at": row["totals_updated_at"],
            }
        )
    return output


def get_federal_candidate_detail(
    conn: sqlite3.Connection,
    candidate_id: str,
    cycle: int | None = None,
    top_donor_limit: int = 25,
    contribution_limit: int = 200,
    contribution_offset: int = 0,
    schedule_b_limit: int = 200,
    schedule_b_offset: int = 0,
    schedule_e_limit: int = 200,
    schedule_e_offset: int = 0,
    schedule_b_sort: str = "date",
    schedule_b_dir: str = "desc",
    schedule_e_sort: str = "date",
    schedule_e_dir: str = "desc",
) -> dict | None:
    _ensure_missing_donor_identities(conn, cycle=cycle)
    has_schedule_b = _table_exists(conn, "fec_schedule_b_disbursements")
    has_schedule_e = _table_exists(conn, "fec_schedule_e_independent_expenditures")

    where_cycle = ""
    params: list[Any] = [candidate_id]
    if cycle is not None:
        where_cycle = " AND m.cycle = ?"
        params.append(int(cycle))

    schedule_b_join_sql = (
        """
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT sub_id) AS disbursement_count,
                COALESCE(SUM(disbursement_amount), 0) AS schedule_total_amount,
                MAX(disbursement_date) AS latest_disbursement_date,
                MIN(disbursement_date) AS earliest_disbursement_date
            FROM fec_schedule_b_disbursements
            GROUP BY candidate_id, cycle
        ) sb
          ON sb.candidate_id = m.fec_candidate_id
         AND sb.cycle = m.cycle
        """
        if has_schedule_b
        else ""
    )
    schedule_b_count_expr = "COALESCE(sb.disbursement_count, 0)" if has_schedule_b else "0"
    schedule_b_total_expr = "COALESCE(sb.schedule_total_amount, 0)" if has_schedule_b else "0.0"
    schedule_b_latest_expr = "sb.latest_disbursement_date" if has_schedule_b else "NULL"
    schedule_b_earliest_expr = "sb.earliest_disbursement_date" if has_schedule_b else "NULL"

    schedule_e_join_sql = (
        """
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT sub_id) AS expenditure_count,
                COALESCE(SUM(expenditure_amount), 0) AS schedule_total_amount,
                MAX(expenditure_date) AS latest_expenditure_date,
                MIN(expenditure_date) AS earliest_expenditure_date
            FROM fec_schedule_e_independent_expenditures
            GROUP BY candidate_id, cycle
        ) se
          ON se.candidate_id = m.fec_candidate_id
         AND se.cycle = m.cycle
        """
        if has_schedule_e
        else ""
    )
    schedule_e_count_expr = "COALESCE(se.expenditure_count, 0)" if has_schedule_e else "0"
    schedule_e_total_expr = "COALESCE(se.schedule_total_amount, 0)" if has_schedule_e else "0.0"
    schedule_e_latest_expr = "se.latest_expenditure_date" if has_schedule_e else "NULL"
    schedule_e_earliest_expr = "se.earliest_expenditure_date" if has_schedule_e else "NULL"

    summary = conn.execute(
        f"""
        SELECT
            m.fec_candidate_id,
            m.fec_name,
            m.candidate_name,
            m.office,
            m.district,
            m.party,
            m.election_stage,
            m.match_status,
            m.match_score,
            m.cycle,
            COALESCE(cc.committee_count, 0) AS committee_count,
            COALESCE(sa.contribution_count, 0) AS contribution_count,
            COALESCE(sa.donor_count, 0) AS donor_count,
            COALESCE(ft.receipts, COALESCE(sa.schedule_total_amount, 0)) AS total_amount,
            ft.receipts AS reported_total_receipts,
            ft.disbursements AS reported_total_disbursements,
            COALESCE(sa.schedule_total_amount, 0) AS schedule_total_amount,
            {schedule_b_total_expr} AS schedule_b_total_amount,
            {schedule_b_count_expr} AS schedule_b_disbursement_count,
            {schedule_b_earliest_expr} AS earliest_disbursement_date,
            {schedule_b_latest_expr} AS latest_disbursement_date,
            {schedule_e_total_expr} AS schedule_e_total_amount,
            {schedule_e_count_expr} AS schedule_e_expenditure_count,
            {schedule_e_earliest_expr} AS earliest_expenditure_date,
            {schedule_e_latest_expr} AS latest_expenditure_date,
            sa.latest_contribution_date AS latest_contribution_date,
            sa.earliest_contribution_date AS earliest_contribution_date,
            ft.coverage_end_date AS coverage_end_date,
            ft.transaction_coverage_date AS transaction_coverage_date,
            ft.updated_at AS totals_updated_at
        FROM fec_candidate_match m
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT committee_id) AS committee_count
            FROM fec_candidate_committees
            GROUP BY candidate_id, cycle
        ) cc
          ON cc.candidate_id = m.fec_candidate_id
         AND cc.cycle = m.cycle
        LEFT JOIN (
            SELECT
                candidate_id,
                cycle,
                COUNT(DISTINCT sub_id) AS contribution_count,
                COUNT(
                    DISTINCT COALESCE(
                        NULLIF(donor_entity_key, ''),
                        NULLIF(donor_key, ''),
                        sub_id
                    )
                ) AS donor_count,
                COALESCE(SUM(contribution_receipt_amount), 0) AS schedule_total_amount,
                MAX(contribution_receipt_date) AS latest_contribution_date,
                MIN(contribution_receipt_date) AS earliest_contribution_date
            FROM fec_schedule_a_contributions
            GROUP BY candidate_id, cycle
        ) sa
          ON sa.candidate_id = m.fec_candidate_id
         AND sa.cycle = m.cycle
        LEFT JOIN fec_candidate_cycle_totals ft
          ON ft.candidate_id = m.fec_candidate_id
         AND ft.cycle = m.cycle
        {schedule_b_join_sql}
        {schedule_e_join_sql}
        WHERE m.fec_candidate_id = ?
        {where_cycle}
        LIMIT 1
        """,
        params,
    ).fetchone()

    if not summary:
        return None

    contribution_where = "WHERE candidate_id = ?"
    contribution_params: list[Any] = [candidate_id]
    if cycle is not None:
        contribution_where += " AND cycle = ?"
        contribution_params.append(int(cycle))

    top_donors = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(donor_entity_key, ''), NULLIF(donor_key, ''), sub_id) AS donor_entity_key,
            COALESCE(MAX(donor_entity_method), 'unknown') AS donor_entity_method,
            COALESCE(MAX(donor_key), '') AS donor_key,
            COALESCE(MAX(contributor_name), 'Unknown Donor') AS donor_name,
            COALESCE(MAX(contributor_city), '') AS contributor_city,
            COALESCE(MAX(contributor_state), '') AS contributor_state,
            COALESCE(MAX(contributor_zip), '') AS contributor_zip,
            COALESCE(MAX(contributor_employer), '') AS contributor_employer,
            COALESCE(MAX(contributor_occupation), '') AS contributor_occupation,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(contribution_receipt_amount), 0) AS total_amount,
            MAX(contribution_receipt_date) AS latest_date,
            MIN(contribution_receipt_date) AS earliest_date
        FROM fec_schedule_a_contributions
        {contribution_where}
        GROUP BY COALESCE(NULLIF(donor_entity_key, ''), NULLIF(donor_key, ''), sub_id)
        ORDER BY total_amount DESC, contribution_count DESC
        LIMIT ?
        """,
        contribution_params + [max(1, int(top_donor_limit))],
    ).fetchall()

    contributions = conn.execute(
        f"""
        SELECT
            sub_id,
            contribution_receipt_date,
            contribution_receipt_amount,
            contributor_name,
            contributor_city,
            contributor_state,
            contributor_zip,
            contributor_employer,
            contributor_occupation,
            committee_id,
            committee_name,
            line_number,
            receipt_type,
            receipt_type_desc,
            memo_text,
            donor_key,
            donor_entity_key,
            donor_entity_method
        FROM fec_schedule_a_contributions
        {contribution_where}
        ORDER BY contribution_receipt_date DESC, contribution_receipt_amount DESC, sub_id DESC
        LIMIT ? OFFSET ?
        """,
        contribution_params + [max(1, int(contribution_limit)), max(0, int(contribution_offset))],
    ).fetchall()

    total_contributions_row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM fec_schedule_a_contributions
        {contribution_where}
        """,
        contribution_params,
    ).fetchone()

    schedule_b_rows: list[dict] = []
    total_schedule_b_disbursements = 0
    if has_schedule_b:
        schedule_b_sort_field = _clean_text(schedule_b_sort).lower() or "date"
        schedule_b_sort_dir = "ASC" if _clean_text(schedule_b_dir).lower() == "asc" else "DESC"
        schedule_b_sort_columns = {
            "date": "disbursement_date",
            "amount": "disbursement_amount",
            "recipient": "recipient_name",
            "committee": "committee_name",
            "type": "disbursement_type_desc",
            "category": "category_code_full",
        }
        schedule_b_order_column = schedule_b_sort_columns.get(schedule_b_sort_field, "disbursement_date")
        schedule_b_order_sql = f"{schedule_b_order_column} {schedule_b_sort_dir}, sub_id DESC"

        schedule_b_where = "WHERE candidate_id = ?"
        schedule_b_params: list[Any] = [candidate_id]
        if cycle is not None:
            schedule_b_where += " AND cycle = ?"
            schedule_b_params.append(int(cycle))

        schedule_b_rows = conn.execute(
            f"""
            SELECT
                sub_id,
                disbursement_date,
                disbursement_amount,
                recipient_name,
                recipient_city,
                recipient_state,
                recipient_zip,
                recipient_candidate_id,
                recipient_candidate_name,
                committee_id,
                committee_name,
                disbursement_type,
                disbursement_type_desc,
                category_code,
                category_code_full,
                memo_text
            FROM fec_schedule_b_disbursements
            {schedule_b_where}
            ORDER BY {schedule_b_order_sql}
            LIMIT ? OFFSET ?
            """,
            schedule_b_params + [max(1, int(schedule_b_limit)), max(0, int(schedule_b_offset))],
        ).fetchall()

        schedule_b_count_row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM fec_schedule_b_disbursements
            {schedule_b_where}
            """,
            schedule_b_params,
        ).fetchone()
        total_schedule_b_disbursements = int(schedule_b_count_row["count"] or 0) if schedule_b_count_row else 0

    schedule_e_rows: list[dict] = []
    total_schedule_e_expenditures = 0
    if has_schedule_e:
        schedule_e_sort_field = _clean_text(schedule_e_sort).lower() or "date"
        schedule_e_sort_dir = "ASC" if _clean_text(schedule_e_dir).lower() == "asc" else "DESC"
        schedule_e_sort_columns = {
            "date": "expenditure_date",
            "amount": "expenditure_amount",
            "support_oppose": "support_oppose_indicator",
            "committee": "committee_name",
            "payee": "payee_name",
            "category": "category_code_full",
        }
        schedule_e_order_column = schedule_e_sort_columns.get(schedule_e_sort_field, "expenditure_date")
        schedule_e_order_sql = f"{schedule_e_order_column} {schedule_e_sort_dir}, sub_id DESC"

        schedule_e_where = "WHERE candidate_id = ?"
        schedule_e_params: list[Any] = [candidate_id]
        if cycle is not None:
            schedule_e_where += " AND cycle = ?"
            schedule_e_params.append(int(cycle))

        schedule_e_rows = conn.execute(
            f"""
            SELECT
                sub_id,
                expenditure_date,
                expenditure_amount,
                support_oppose_indicator,
                committee_id,
                committee_name,
                payee_name,
                payee_city,
                payee_state,
                payee_zip,
                category_code,
                category_code_full,
                report_type,
                line_number,
                memo_text,
                expenditure_description
            FROM fec_schedule_e_independent_expenditures
            {schedule_e_where}
            ORDER BY {schedule_e_order_sql}
            LIMIT ? OFFSET ?
            """,
            schedule_e_params + [max(1, int(schedule_e_limit)), max(0, int(schedule_e_offset))],
        ).fetchall()

        schedule_e_count_row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM fec_schedule_e_independent_expenditures
            {schedule_e_where}
            """,
            schedule_e_params,
        ).fetchone()
        total_schedule_e_expenditures = int(schedule_e_count_row["count"] or 0) if schedule_e_count_row else 0

    committee_rows = conn.execute(
        """
        SELECT committee_id, committee_name, committee_designation, committee_designation_full, committee_type
        FROM fec_candidate_committees
        WHERE candidate_id = ?
          AND (? IS NULL OR cycle = ?)
        ORDER BY committee_designation DESC, committee_name ASC
        """,
        (candidate_id, cycle, cycle),
    ).fetchall()

    reported_total_disbursements = (
        float(summary["reported_total_disbursements"])
        if summary["reported_total_disbursements"] is not None
        else None
    )
    schedule_b_total_amount = float(summary["schedule_b_total_amount"] or 0.0)
    disbursement_gap_amount = (
        (reported_total_disbursements - schedule_b_total_amount)
        if reported_total_disbursements is not None
        else None
    )
    disbursement_gap_ratio = (
        (disbursement_gap_amount / reported_total_disbursements)
        if reported_total_disbursements not in (None, 0)
        else None
    )
    schedule_total_amount = float(summary["schedule_total_amount"] or 0.0)
    schedule_e_total_amount = float(summary["schedule_e_total_amount"] or 0.0)
    reported_total_receipts = (
        float(summary["reported_total_receipts"] or 0.0)
        if summary["reported_total_receipts"] is not None
        else None
    )
    money_in_total = reported_total_receipts if reported_total_receipts is not None else schedule_total_amount
    money_in_source = "reported_receipts" if reported_total_receipts is not None else "schedule_a_synced"
    money_out_total = (
        reported_total_disbursements if reported_total_disbursements is not None else schedule_b_total_amount
    )
    money_out_source = "reported_disbursements" if reported_total_disbursements is not None else "schedule_b_synced"
    outside_spending_total = schedule_e_total_amount
    net_money_flow = money_in_total - money_out_total
    outside_pressure_ratio = (outside_spending_total / money_in_total) if money_in_total > 0 else None

    cross_role = get_federal_cross_role_organizations(
        conn,
        cycle=cycle,
        candidate_id=candidate_id,
        limit=50,
        min_total_amount=0.0,
    )
    transfer_chains = _get_candidate_schedule_b_transfer_chains(
        conn,
        candidate_id=candidate_id,
        cycle=cycle,
        limit=50,
    )

    return {
        "summary": {
            "fec_candidate_id": summary["fec_candidate_id"],
            "fec_name": summary["fec_name"],
            "candidate_name": summary["candidate_name"],
            "office": summary["office"],
            "district": summary["district"],
            "party": summary["party"],
            "election_stage": summary["election_stage"],
            "match_status": summary["match_status"],
            "match_score": summary["match_score"],
            "cycle": summary["cycle"],
            "committee_count": int(summary["committee_count"] or 0),
            "contribution_count": int(summary["contribution_count"] or 0),
            "donor_count": int(summary["donor_count"] or 0),
            "total_amount": float(summary["total_amount"] or 0.0),
            "reported_total_receipts": reported_total_receipts,
            "reported_total_disbursements": reported_total_disbursements,
            "schedule_total_amount": schedule_total_amount,
            "schedule_b_total_amount": schedule_b_total_amount,
            "schedule_b_disbursement_count": int(summary["schedule_b_disbursement_count"] or 0),
            "earliest_disbursement_date": summary["earliest_disbursement_date"],
            "latest_disbursement_date": summary["latest_disbursement_date"],
            "schedule_e_total_amount": schedule_e_total_amount,
            "schedule_e_expenditure_count": int(summary["schedule_e_expenditure_count"] or 0),
            "earliest_expenditure_date": summary["earliest_expenditure_date"],
            "latest_expenditure_date": summary["latest_expenditure_date"],
            "disbursement_gap_amount": disbursement_gap_amount,
            "disbursement_gap_ratio": disbursement_gap_ratio,
            "money_in_total": round(money_in_total, 2),
            "money_in_source": money_in_source,
            "money_out_total": round(money_out_total, 2),
            "money_out_source": money_out_source,
            "outside_spending_total": round(outside_spending_total, 2),
            "outside_pressure_ratio": round(outside_pressure_ratio, 4) if outside_pressure_ratio is not None else None,
            "net_money_flow": round(net_money_flow, 2),
            "uses_reported_total_receipts": summary["reported_total_receipts"] is not None,
            "uses_reported_total_disbursements": summary["reported_total_disbursements"] is not None,
            "latest_contribution_date": summary["latest_contribution_date"],
            "earliest_contribution_date": summary["earliest_contribution_date"],
            "coverage_end_date": summary["coverage_end_date"],
            "transaction_coverage_date": summary["transaction_coverage_date"],
            "totals_updated_at": summary["totals_updated_at"],
        },
        "top_donors": [
            {
                "donor_entity_key": row["donor_entity_key"],
                "donor_entity_method": row["donor_entity_method"],
                "donor_entity_method_label": donor_identity_method_label(row["donor_entity_method"]),
                "donor_key": row["donor_key"],
                "donor_name": row["donor_name"],
                "contributor_city": row["contributor_city"],
                "contributor_state": row["contributor_state"],
                "contributor_zip": row["contributor_zip"],
                "contributor_employer": row["contributor_employer"],
                "contributor_occupation": row["contributor_occupation"],
                "contribution_count": int(row["contribution_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "latest_date": row["latest_date"],
                "earliest_date": row["earliest_date"],
            }
            for row in top_donors
        ],
        "contributions": [
            {
                "sub_id": row["sub_id"],
                "contribution_receipt_date": row["contribution_receipt_date"],
                "contribution_receipt_amount": float(row["contribution_receipt_amount"] or 0.0),
                "contributor_name": row["contributor_name"],
                "contributor_city": row["contributor_city"],
                "contributor_state": row["contributor_state"],
                "contributor_zip": row["contributor_zip"],
                "contributor_employer": row["contributor_employer"],
                "contributor_occupation": row["contributor_occupation"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "line_number": row["line_number"],
                "receipt_type": row["receipt_type"],
                "receipt_type_desc": row["receipt_type_desc"],
                "memo_text": row["memo_text"],
                "donor_key": row["donor_key"],
                "donor_entity_key": row["donor_entity_key"],
                "donor_entity_method": row["donor_entity_method"],
            }
            for row in contributions
        ],
        "schedule_b_disbursements": [
            {
                "sub_id": row["sub_id"],
                "disbursement_date": row["disbursement_date"],
                "disbursement_amount": float(row["disbursement_amount"] or 0.0),
                "recipient_name": row["recipient_name"],
                "recipient_city": row["recipient_city"],
                "recipient_state": row["recipient_state"],
                "recipient_zip": row["recipient_zip"],
                "recipient_candidate_id": row["recipient_candidate_id"],
                "recipient_candidate_name": row["recipient_candidate_name"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "disbursement_type": row["disbursement_type"],
                "disbursement_type_desc": row["disbursement_type_desc"],
                "category_code": row["category_code"],
                "category_code_full": row["category_code_full"],
                "memo_text": row["memo_text"],
            }
            for row in schedule_b_rows
        ],
        "schedule_e_independent_expenditures": [
            {
                "sub_id": row["sub_id"],
                "expenditure_date": row["expenditure_date"],
                "expenditure_amount": float(row["expenditure_amount"] or 0.0),
                "support_oppose_indicator": row["support_oppose_indicator"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "payee_name": row["payee_name"],
                "payee_city": row["payee_city"],
                "payee_state": row["payee_state"],
                "payee_zip": row["payee_zip"],
                "category_code": row["category_code"],
                "category_code_full": row["category_code_full"],
                "report_type": row["report_type"],
                "line_number": row["line_number"],
                "memo_text": row["memo_text"],
                "expenditure_description": row["expenditure_description"],
            }
            for row in schedule_e_rows
        ],
        "committees": [
            {
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "committee_designation": row["committee_designation"],
                "committee_designation_full": row["committee_designation_full"],
                "committee_type": row["committee_type"],
            }
            for row in committee_rows
        ],
        "total_contributions": int(total_contributions_row["count"] or 0) if total_contributions_row else 0,
        "total_schedule_b_disbursements": total_schedule_b_disbursements,
        "total_schedule_e_expenditures": total_schedule_e_expenditures,
        "cross_role_organizations": cross_role.get("rows", []),
        "cross_role_summary": cross_role.get("summary", {}),
        "schedule_b_transfer_chains": transfer_chains,
    }


def get_federal_committee_receipts(
    conn: sqlite3.Connection,
    *,
    committee_id: str,
    cycle: int | None = None,
    receipt_limit: int = 100,
    receipt_offset: int = 0,
    receipt_sort: str = "date",
    receipt_dir: str = "desc",
) -> dict | None:
    """Return one committee's Schedule A receipts with transfer-source context."""
    if not _table_exists(conn, "fec_schedule_a_contributions"):
        return None

    committee_key = _clean_text(committee_id)
    if not committee_key:
        return None

    sort_field = _clean_text(receipt_sort).lower() or "date"
    sort_dir_sql = "ASC" if _clean_text(receipt_dir).lower() == "asc" else "DESC"
    sort_columns = {
        "date": "contribution_receipt_date",
        "amount": "contribution_receipt_amount",
        "donor": "contributor_name",
        "state": "contributor_state",
        "type": "receipt_type_desc",
    }
    order_column = sort_columns.get(sort_field, "contribution_receipt_date")
    order_sql = f"{order_column} {sort_dir_sql}, sub_id DESC"

    where_sql = "WHERE committee_id = ?"
    params: list[Any] = [committee_key]
    if cycle is not None:
        where_sql += " AND cycle = ?"
        params.append(int(cycle))

    summary = conn.execute(
        f"""
        SELECT
            COALESCE(MAX(committee_name), committee_id) AS committee_name,
            COUNT(*) AS receipt_count,
            COALESCE(SUM(contribution_receipt_amount), 0.0) AS total_amount,
            COUNT(DISTINCT COALESCE(NULLIF(donor_entity_key, ''), NULLIF(donor_key, ''), NULLIF(contributor_name, ''))) AS donor_count,
            MIN(contribution_receipt_date) AS coverage_start,
            MAX(contribution_receipt_date) AS coverage_end
        FROM fec_schedule_a_contributions
        {where_sql}
        """,
        params,
    ).fetchone()
    if not summary or int(summary["receipt_count"] or 0) == 0:
        return None

    rows = conn.execute(
        f"""
        SELECT
            sub_id,
            cycle,
            candidate_id,
            candidate_name,
            committee_id,
            COALESCE(NULLIF(committee_name, ''), committee_id) AS committee_name,
            contributor_name,
            contributor_city,
            contributor_state,
            contributor_zip,
            contributor_employer,
            contributor_occupation,
            receipt_type,
            receipt_type_desc,
            memo_text,
            contribution_receipt_amount,
            contribution_receipt_date,
            donor_key,
            donor_entity_key,
            donor_entity_method
        FROM fec_schedule_a_contributions
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        params + [max(1, int(receipt_limit)), max(0, int(receipt_offset))],
    ).fetchall()

    total_row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM fec_schedule_a_contributions
        {where_sql}
        """,
        params,
    ).fetchone()
    total_receipts = int(total_row["count"] or 0) if total_row else 0

    owner_rows = []
    if _table_exists(conn, "fec_candidate_committees"):
        owner_rows = conn.execute(
            """
            SELECT
                c.candidate_id,
                COALESCE(MAX(m.fec_name), MAX(m.candidate_name), MAX(c.candidate_id)) AS candidate_name,
                MAX(c.is_principal) AS is_principal
            FROM fec_candidate_committees c
            LEFT JOIN fec_candidate_match m
              ON m.fec_candidate_id = c.candidate_id
             AND m.cycle = c.cycle
            WHERE c.committee_id = ?
              AND (? IS NULL OR c.cycle = ?)
            GROUP BY c.candidate_id
            ORDER BY is_principal DESC, candidate_name ASC
            """,
            (committee_key, cycle, cycle),
        ).fetchall()

    transfer_meta = None
    if _table_exists(conn, "fec_transfer_source_committees"):
        transfer_meta_row = conn.execute(
            """
            SELECT
                committee_id,
                cycle,
                committee_name,
                transfer_count,
                transfer_total_amount,
                source_candidate_count,
                recipient_candidate_count,
                recipient_committee_count,
                latest_transfer_date,
                receipts_synced,
                receipts_row_count,
                receipts_total_amount,
                receipts_coverage_start,
                receipts_coverage_end,
                last_receipts_sync_at
            FROM fec_transfer_source_committees
            WHERE committee_id = ?
              AND (? IS NULL OR cycle = ?)
            ORDER BY cycle DESC
            LIMIT 1
            """,
            (committee_key, cycle, cycle),
        ).fetchone()
        if transfer_meta_row:
            transfer_meta = {
                "committee_id": transfer_meta_row["committee_id"],
                "cycle": int(transfer_meta_row["cycle"] or 0),
                "committee_name": transfer_meta_row["committee_name"] or committee_key,
                "transfer_count": int(transfer_meta_row["transfer_count"] or 0),
                "transfer_total_amount": float(transfer_meta_row["transfer_total_amount"] or 0.0),
                "source_candidate_count": int(transfer_meta_row["source_candidate_count"] or 0),
                "recipient_candidate_count": int(transfer_meta_row["recipient_candidate_count"] or 0),
                "recipient_committee_count": int(transfer_meta_row["recipient_committee_count"] or 0),
                "latest_transfer_date": transfer_meta_row["latest_transfer_date"],
                "receipts_synced": bool(int(transfer_meta_row["receipts_synced"] or 0)),
                "receipts_row_count": int(transfer_meta_row["receipts_row_count"] or 0),
                "receipts_total_amount": float(transfer_meta_row["receipts_total_amount"] or 0.0),
                "receipts_coverage_start": transfer_meta_row["receipts_coverage_start"],
                "receipts_coverage_end": transfer_meta_row["receipts_coverage_end"],
                "last_receipts_sync_at": transfer_meta_row["last_receipts_sync_at"],
            }

    return {
        "summary": {
            "committee_id": committee_key,
            "committee_name": summary["committee_name"] or committee_key,
            "receipt_count": int(summary["receipt_count"] or 0),
            "total_amount": float(summary["total_amount"] or 0.0),
            "donor_count": int(summary["donor_count"] or 0),
            "coverage_start": summary["coverage_start"],
            "coverage_end": summary["coverage_end"],
        },
        "receipts": [
            {
                "sub_id": row["sub_id"],
                "cycle": int(row["cycle"] or 0),
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "contributor_name": row["contributor_name"],
                "contributor_city": row["contributor_city"],
                "contributor_state": row["contributor_state"],
                "contributor_zip": row["contributor_zip"],
                "contributor_employer": row["contributor_employer"],
                "contributor_occupation": row["contributor_occupation"],
                "receipt_type": row["receipt_type"],
                "receipt_type_desc": row["receipt_type_desc"],
                "memo_text": row["memo_text"],
                "contribution_receipt_amount": float(row["contribution_receipt_amount"] or 0.0),
                "contribution_receipt_date": row["contribution_receipt_date"],
                "donor_key": row["donor_key"],
                "donor_entity_key": row["donor_entity_key"],
                "donor_entity_method": row["donor_entity_method"],
            }
            for row in rows
        ],
        "committee_owners": [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or row["candidate_id"],
                "is_principal": bool(int(row["is_principal"] or 0)),
            }
            for row in owner_rows
        ],
        "transfer_meta": transfer_meta,
        "total_receipts": total_receipts,
    }


def get_federal_race_analytics(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Aggregate race-level federal analytics for Illinois candidates."""
    _ensure_missing_donor_identities(conn, cycle=cycle)

    office_filter = _canonical_office_code(office_code)
    district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""

    candidate_rows = conn.execute(
        """
        SELECT DISTINCT
            m.fec_candidate_id AS candidate_id,
            COALESCE(m.fec_name, m.candidate_name, 'Unknown Candidate') AS candidate_name,
            m.office_code,
            m.fec_office,
            m.office,
            m.district_code,
            m.fec_district,
            m.district
        FROM fec_candidate_match m
        WHERE m.fec_candidate_id IS NOT NULL
          AND (? IS NULL OR m.cycle = ?)
        """,
        (cycle, cycle),
    ).fetchall()

    races: dict[str, dict[str, Any]] = {}
    candidate_to_race: dict[str, str] = {}

    for row in candidate_rows:
        candidate_id = _clean_text(row["candidate_id"])
        if not candidate_id:
            continue

        row_office_code = _canonical_office_code(row["office_code"], row["fec_office"], row["office"])
        row_district_code = _canonical_district_code(row_office_code, row["district_code"], row["fec_district"], row["district"])

        if office_filter and row_office_code != office_filter:
            continue
        if district_filter and row_district_code != district_filter:
            continue

        race_id = f"{row_office_code or 'UNK'}:{row_district_code or 'NA'}"
        race = races.setdefault(
            race_id,
            {
                "race_id": race_id,
                "office_code": row_office_code,
                "district_code": row_district_code,
                "race_office": _office_display_label(row_office_code, row["office"]),
                "race_district": _district_display_label(row_office_code, row_district_code, row["district"]),
                "candidate_ids": set(),
                "candidate_names": {},
                "candidate_totals": defaultdict(float),
                "donor_keys": set(),
                "contribution_count": 0,
                "total_amount": 0.0,
                "outside_expenditure_count": 0,
                "outside_spending_total": 0.0,
                "earliest_contribution_date": None,
                "latest_contribution_date": None,
            },
        )
        race["candidate_ids"].add(candidate_id)
        race["candidate_names"][candidate_id] = _clean_text(row["candidate_name"]) or candidate_id
        candidate_to_race[candidate_id] = race_id

    if not races:
        return []

    edge_rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_entity_key,
            sa.candidate_id AS candidate_id,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions sa
        WHERE sa.candidate_id IS NOT NULL
          AND (? IS NULL OR sa.cycle = ?)
                GROUP BY 1, sa.candidate_id
        """,
        (cycle, cycle),
    ).fetchall()

    for row in edge_rows:
        candidate_id = _clean_text(row["candidate_id"])
        race_id = candidate_to_race.get(candidate_id)
        if not race_id:
            continue

        race = races[race_id]
        donor_key = _clean_text(row["donor_entity_key"])
        if donor_key:
            race["donor_keys"].add(donor_key)

        contribution_count = int(row["contribution_count"] or 0)
        total_amount = float(row["total_amount"] or 0.0)
        race["contribution_count"] += contribution_count
        race["total_amount"] += total_amount
        race["candidate_totals"][candidate_id] += total_amount

        earliest_date = row["earliest_contribution_date"]
        latest_date = row["latest_contribution_date"]

        if earliest_date and (
            race["earliest_contribution_date"] is None
            or earliest_date < race["earliest_contribution_date"]
        ):
            race["earliest_contribution_date"] = earliest_date
        if latest_date and (
            race["latest_contribution_date"] is None
            or latest_date > race["latest_contribution_date"]
        ):
            race["latest_contribution_date"] = latest_date

    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        outside_rows = conn.execute(
            """
            SELECT
                candidate_id,
                COUNT(*) AS expenditure_count,
                COALESCE(SUM(expenditure_amount), 0.0) AS total_amount
            FROM fec_schedule_e_independent_expenditures
            WHERE candidate_id IS NOT NULL
              AND (? IS NULL OR cycle = ?)
            GROUP BY candidate_id
            """,
            (cycle, cycle),
        ).fetchall()
        for row in outside_rows:
            candidate_id = _clean_text(row["candidate_id"])
            race_id = candidate_to_race.get(candidate_id)
            if not race_id:
                continue
            race = races[race_id]
            race["outside_expenditure_count"] += int(row["expenditure_count"] or 0)
            race["outside_spending_total"] += float(row["total_amount"] or 0.0)

    output: list[dict] = []
    for race in races.values():
        candidate_totals = [
            (
                candidate_id,
                race["candidate_names"].get(candidate_id, candidate_id),
                float(race["candidate_totals"].get(candidate_id, 0.0)),
            )
            for candidate_id in race["candidate_ids"]
        ]
        candidate_totals.sort(key=lambda item: item[2], reverse=True)

        top_candidate_id = candidate_totals[0][0] if candidate_totals else None
        top_candidate_name = candidate_totals[0][1] if candidate_totals else None
        top_candidate_amount = candidate_totals[0][2] if candidate_totals else 0.0
        total_amount = float(race["total_amount"] or 0.0)
        outside_spending_total = float(race["outside_spending_total"] or 0.0)
        outside_expenditure_count = int(race["outside_expenditure_count"] or 0)
        outside_pressure_ratio = (outside_spending_total / total_amount) if total_amount > 0 else 0.0
        top_candidate_share = (top_candidate_amount / total_amount) if total_amount > 0 else 0.0
        contribution_count = int(race["contribution_count"] or 0)

        output.append(
            {
                "race_id": race["race_id"],
                "office_code": race["office_code"],
                "district_code": race["district_code"],
                "race_office": race["race_office"],
                "race_district": race["race_district"],
                "race_label": f"{race['race_office']} - {race['race_district']}",
                "candidate_count": len(race["candidate_ids"]),
                "donor_count": len(race["donor_keys"]),
                "contribution_count": contribution_count,
                "total_amount": round(total_amount, 2),
                "avg_contribution_amount": round(total_amount / contribution_count, 2) if contribution_count > 0 else 0.0,
                "outside_spending_total": round(outside_spending_total, 2),
                "outside_expenditure_count": outside_expenditure_count,
                "outside_pressure_ratio": round(outside_pressure_ratio, 4),
                "top_candidate_id": top_candidate_id,
                "top_candidate_name": top_candidate_name,
                "top_candidate_amount": round(top_candidate_amount, 2),
                "top_candidate_share": round(top_candidate_share, 4),
                "earliest_contribution_date": race["earliest_contribution_date"],
                "latest_contribution_date": race["latest_contribution_date"],
            }
        )

    output.sort(
        key=lambda row: (
            float(row["total_amount"] or 0.0),
            int(row["contribution_count"] or 0),
            row["race_label"],
        ),
        reverse=True,
    )
    return output[: max(1, int(limit))]


def get_federal_race_outside_spending(
    conn: sqlite3.Connection,
    *,
    cycle: int,
    office_code: str,
    district_code: str,
    limit: int = 100,
    offset: int = 0,
    sort: str = "date",
    dir: str = "desc",
    support_oppose: str = "",
    search: str = "",
    aggregate_limit: int = 15,
) -> dict:
    """Return race-level Schedule E drilldown data with filters and pagination."""
    office_filter = _canonical_office_code(office_code)
    if not office_filter:
        return {
            "race": {
                "office_code": "",
                "district_code": "",
                "race_label": "Unknown Race",
                "candidate_count": 0,
                "transaction_count": 0,
                "total_amount": 0.0,
                "latest_expenditure_date": None,
            },
            "top_committees": [],
            "top_payees": [],
            "rows": [],
            "total_rows": 0,
        }

    district_raw = _clean_text(district_code).upper()
    if office_filter == "H":
        district_filter = _normalize_district_filter(office_filter, district_raw)
        if district_raw == "NA" and not district_filter:
            district_filter = "NA"
    else:
        district_filter = "NA"

    district_for_label = district_filter if district_filter and district_filter != "NA" else "STATEWIDE"
    race_label = f"{_office_display_label(office_filter)} - {_district_display_label(office_filter, district_for_label)}"

    if not _table_exists(conn, "fec_candidate_match"):
        return {
            "race": {
                "office_code": office_filter,
                "district_code": district_filter or "NA",
                "race_label": race_label,
                "candidate_count": 0,
                "transaction_count": 0,
                "total_amount": 0.0,
                "latest_expenditure_date": None,
            },
            "top_committees": [],
            "top_payees": [],
            "rows": [],
            "total_rows": 0,
        }

    candidate_rows = conn.execute(
        """
        SELECT DISTINCT
            m.fec_candidate_id AS candidate_id,
            COALESCE(m.fec_name, m.candidate_name, m.fec_candidate_id) AS candidate_name,
            m.office_code,
            m.fec_office,
            m.office,
            m.district_code,
            m.fec_district,
            m.district
        FROM fec_candidate_match m
        WHERE m.fec_candidate_id IS NOT NULL
          AND m.cycle = ?
        """,
        (int(cycle),),
    ).fetchall()

    candidate_names: dict[str, str] = {}
    scoped_candidate_ids: list[str] = []
    for row in candidate_rows:
        candidate_id = _clean_text(row["candidate_id"])
        if not candidate_id or candidate_id in candidate_names:
            continue
        row_office_code = _canonical_office_code(row["office_code"], row["fec_office"], row["office"])
        row_district_code = _canonical_district_code(row_office_code, row["district_code"], row["fec_district"], row["district"])
        row_race_district = row_district_code if row_office_code == "H" and row_district_code else "NA"

        if row_office_code != office_filter:
            continue
        if district_filter and row_race_district != district_filter:
            continue

        candidate_names[candidate_id] = _clean_text(row["candidate_name"]) or candidate_id
        scoped_candidate_ids.append(candidate_id)

    if not scoped_candidate_ids or not _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        return {
            "race": {
                "office_code": office_filter,
                "district_code": district_filter or "NA",
                "race_label": race_label,
                "candidate_count": len(scoped_candidate_ids),
                "transaction_count": 0,
                "total_amount": 0.0,
                "latest_expenditure_date": None,
            },
            "top_committees": [],
            "top_payees": [],
            "rows": [],
            "total_rows": 0,
        }

    candidate_placeholders = ",".join("?" for _ in scoped_candidate_ids)
    candidate_join_sql = (
        """
        LEFT JOIN (
            SELECT
                fec_candidate_id AS candidate_id,
                MAX(COALESCE(NULLIF(fec_name, ''), NULLIF(candidate_name, ''), fec_candidate_id)) AS candidate_name
            FROM fec_candidate_match
            WHERE cycle = ?
            GROUP BY fec_candidate_id
        ) cm
          ON cm.candidate_id = se.candidate_id
        """
    )

    where_parts = [
        "se.cycle = ?",
        f"se.candidate_id IN ({candidate_placeholders})",
    ]
    where_params: list[Any] = [int(cycle), *scoped_candidate_ids]

    support_oppose_filter = _clean_text(support_oppose).upper()
    if support_oppose_filter in {"S", "O"}:
        where_parts.append("COALESCE(se.support_oppose_indicator, '') = ?")
        where_params.append(support_oppose_filter)

    search_text = _clean_text(search)
    if search_text:
        search_like = f"%{search_text.lower()}%"
        where_parts.append(
            "(" 
            "LOWER(COALESCE(se.committee_name, '')) LIKE ? OR "
            "LOWER(COALESCE(se.payee_name, '')) LIKE ? OR "
            "LOWER(COALESCE(NULLIF(se.candidate_name, ''), cm.candidate_name, se.candidate_id, '')) LIKE ? OR "
            "LOWER(COALESCE(se.category_code_full, '')) LIKE ?"
            ")"
        )
        where_params.extend([search_like, search_like, search_like, search_like])

    where_sql = " AND ".join(where_parts)

    sort_field = _clean_text(sort).lower() or "date"
    sort_dir = "ASC" if _clean_text(dir).lower() == "asc" else "DESC"
    sort_columns = {
        "date": "se.expenditure_date",
        "amount": "se.expenditure_amount",
        "support_oppose": "se.support_oppose_indicator",
        "committee": "se.committee_name",
        "payee": "se.payee_name",
        "candidate": "COALESCE(NULLIF(se.candidate_name, ''), cm.candidate_name, se.candidate_id)",
        "category": "se.category_code_full",
    }
    order_column = sort_columns.get(sort_field, "se.expenditure_date")
    order_sql = f"{order_column} {sort_dir}, se.sub_id DESC"

    metric_row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(SUM(se.expenditure_amount), 0.0) AS total_amount,
            MAX(se.expenditure_date) AS latest_expenditure_date
        FROM fec_schedule_e_independent_expenditures se
        {candidate_join_sql}
        WHERE {where_sql}
        """,
        [int(cycle), *where_params],
    ).fetchone()

    committee_candidate_names_agg = _distinct_concat_aggregate_sql(
        conn,
        "COALESCE(NULLIF(se.candidate_name, ''), cm.candidate_name, se.candidate_id)",
    )
    payee_committee_names_agg = _distinct_concat_aggregate_sql(
        conn,
        "COALESCE(NULLIF(se.committee_name, ''), se.committee_id, 'Unknown Committee')",
    )
    payee_candidate_names_agg = _distinct_concat_aggregate_sql(
        conn,
        "COALESCE(NULLIF(se.candidate_name, ''), cm.candidate_name, se.candidate_id)",
    )

    top_committees_rows = conn.execute(
        f"""
        SELECT
            se.committee_id,
            COALESCE(NULLIF(se.committee_name, ''), se.committee_id, 'Unknown Committee') AS committee_name,
            COUNT(*) AS transaction_count,
            COALESCE(SUM(se.expenditure_amount), 0.0) AS total_amount,
            {committee_candidate_names_agg} AS candidate_names
        FROM fec_schedule_e_independent_expenditures se
        {candidate_join_sql}
        WHERE {where_sql}
        GROUP BY se.committee_id, committee_name
        ORDER BY total_amount DESC, transaction_count DESC, committee_name ASC
        LIMIT ?
        """,
        [int(cycle), *where_params, max(1, int(aggregate_limit))],
    ).fetchall()

    top_payees_rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(se.payee_name, ''), 'Unknown Payee') AS payee_name,
            COALESCE(NULLIF(se.payee_state, ''), '') AS payee_state,
            COUNT(*) AS transaction_count,
            COALESCE(SUM(se.expenditure_amount), 0.0) AS total_amount,
            {payee_committee_names_agg} AS committee_names,
            {payee_candidate_names_agg} AS candidate_names
        FROM fec_schedule_e_independent_expenditures se
        {candidate_join_sql}
        WHERE {where_sql}
        GROUP BY payee_name, payee_state
        ORDER BY total_amount DESC, transaction_count DESC, payee_name ASC
        LIMIT ?
        """,
        [int(cycle), *where_params, max(1, int(aggregate_limit))],
    ).fetchall()

    rows = conn.execute(
        f"""
        SELECT
            se.sub_id,
            se.cycle,
            se.candidate_id,
            COALESCE(NULLIF(se.candidate_name, ''), cm.candidate_name, se.candidate_id) AS candidate_name,
            se.expenditure_date,
            se.support_oppose_indicator,
            se.committee_id,
            COALESCE(NULLIF(se.committee_name, ''), se.committee_id, 'Unknown Committee') AS committee_name,
            COALESCE(NULLIF(se.payee_name, ''), 'Unknown Payee') AS payee_name,
            se.payee_city,
            se.payee_state,
            se.payee_zip,
            se.category_code,
            se.category_code_full,
            se.report_type,
            se.line_number,
            se.expenditure_amount,
            se.memo_text,
            se.expenditure_description
        FROM fec_schedule_e_independent_expenditures se
        {candidate_join_sql}
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        [int(cycle), *where_params, max(1, int(limit)), max(0, int(offset))],
    ).fetchall()

    total_rows_row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM fec_schedule_e_independent_expenditures se
        {candidate_join_sql}
        WHERE {where_sql}
        """,
        [int(cycle), *where_params],
    ).fetchone()

    return {
        "race": {
            "office_code": office_filter,
            "district_code": district_filter or "NA",
            "race_label": race_label,
            "candidate_count": len(scoped_candidate_ids),
            "transaction_count": int(metric_row["transaction_count"] or 0) if metric_row else 0,
            "total_amount": float(metric_row["total_amount"] or 0.0) if metric_row else 0.0,
            "latest_expenditure_date": metric_row["latest_expenditure_date"] if metric_row else None,
        },
        "top_committees": [
            {
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "transaction_count": int(row["transaction_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "candidate_names": row["candidate_names"] or "",
            }
            for row in top_committees_rows
        ],
        "top_payees": [
            {
                "payee_name": row["payee_name"],
                "payee_state": row["payee_state"],
                "transaction_count": int(row["transaction_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "committee_names": row["committee_names"] or "",
                "candidate_names": row["candidate_names"] or "",
            }
            for row in top_payees_rows
        ],
        "rows": [
            {
                "sub_id": row["sub_id"],
                "cycle": int(row["cycle"] or 0),
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"] or candidate_names.get(row["candidate_id"], row["candidate_id"]),
                "expenditure_date": row["expenditure_date"],
                "support_oppose_indicator": row["support_oppose_indicator"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "payee_name": row["payee_name"],
                "payee_city": row["payee_city"],
                "payee_state": row["payee_state"],
                "payee_zip": row["payee_zip"],
                "category_code": row["category_code"],
                "category_code_full": row["category_code_full"],
                "report_type": row["report_type"],
                "line_number": row["line_number"],
                "expenditure_amount": float(row["expenditure_amount"] or 0.0),
                "memo_text": row["memo_text"],
                "expenditure_description": row["expenditure_description"],
            }
            for row in rows
        ],
        "total_rows": int(total_rows_row["count"] or 0) if total_rows_row else 0,
    }


def get_federal_network_graph(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    min_edge_amount: float = 1000.0,
    limit: int = 1000,
) -> dict:
    """Build donor->candidate weighted network for federal contributions."""
    _ensure_missing_donor_identities(conn, cycle=cycle)

    office_filter = _canonical_office_code(office_code)
    district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""
    edge_limit = max(1, int(limit))
    query_limit = max(edge_limit * 4, edge_limit)

    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_entity_key,
            COALESCE(MAX(sa.contributor_name), 'Unknown Donor') AS donor_name,
            COALESCE(MAX(sa.contributor_city), '') AS donor_city,
            COALESCE(MAX(sa.contributor_state), '') AS donor_state,
            sa.candidate_id AS candidate_id,
            COALESCE(MAX(cm.fec_name), MAX(cm.candidate_name), MAX(sa.candidate_name), 'Unknown Candidate') AS candidate_name,
            COALESCE(MAX(NULLIF(cm.office_code, '')), MAX(NULLIF(cm.fec_office, '')), '') AS office_code,
            COALESCE(MAX(cm.office), '') AS office,
            COALESCE(MAX(NULLIF(cm.district_code, '')), MAX(NULLIF(cm.fec_district, '')), '') AS district_code,
            COALESCE(MAX(cm.district), '') AS district,
            COALESCE(MAX(NULLIF(cm.party_code, '')), MAX(NULLIF(cm.fec_party, '')), MAX(NULLIF(cm.party, '')), '') AS party_code,
            COALESCE(MAX(cm.party), '') AS party,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions sa
        LEFT JOIN fec_candidate_match cm
          ON cm.fec_candidate_id = sa.candidate_id
         AND cm.cycle = sa.cycle
        WHERE sa.candidate_id IS NOT NULL
          AND (? IS NULL OR sa.cycle = ?)
                GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.candidate_id
        HAVING COALESCE(SUM(sa.contribution_receipt_amount), 0) >= ?
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        (cycle, cycle, float(max(0.0, min_edge_amount)), query_limit),
    ).fetchall()

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict] = []
    weighted_degree: dict[str, float] = defaultdict(float)
    edge_degree: dict[str, int] = defaultdict(int)
    total_amount = 0.0
    earliest_contribution_date = None
    latest_contribution_date = None

    for row in rows:
        row_office_code = _canonical_office_code(row["office_code"], row["office"])
        row_district_code = _canonical_district_code(row_office_code, row["district_code"], row["district"])

        if office_filter and row_office_code != office_filter:
            continue
        if district_filter and row_district_code != district_filter:
            continue
        if len(edges) >= edge_limit:
            break

        donor_entity_key = _clean_text(row["donor_entity_key"])
        candidate_id = _clean_text(row["candidate_id"])
        if not donor_entity_key or not candidate_id:
            continue

        donor_node_id = f"donor:{donor_entity_key}"
        candidate_node_id = f"candidate:{candidate_id}"
        office_label = _office_display_label(row_office_code, row["office"])
        district_label = _district_display_label(row_office_code, row_district_code, row["district"])
        race_label = f"{office_label} - {district_label}"
        row_party_code = _canonical_party_code(row["party_code"], row["party"])
        row_party_label = _party_display_label(row_party_code, row["party"])
        weight = float(row["total_amount"] or 0.0)
        contribution_count = int(row["contribution_count"] or 0)

        nodes.setdefault(
            donor_node_id,
            {
                "id": donor_node_id,
                "label": _clean_text(row["donor_name"]) or "Unknown Donor",
                "node_type": "donor",
                "entity_key": donor_entity_key,
                "city": _clean_text(row["donor_city"]) or None,
                "state": _clean_text(row["donor_state"]) or None,
            },
        )
        nodes.setdefault(
            candidate_node_id,
            {
                "id": candidate_node_id,
                "label": _clean_text(row["candidate_name"]) or candidate_id,
                "node_type": "candidate",
                "candidate_id": candidate_id,
                "office_code": row_office_code,
                "district_code": row_district_code,
                "office_display": office_label,
                "district_display": district_label,
                "race_label": race_label,
                "party_code": row_party_code or None,
                "party_display": row_party_label,
            },
        )

        edges.append(
            {
                "source": donor_node_id,
                "target": candidate_node_id,
                "edge_type": "donor_candidate",
                "weight": round(weight, 2),
                "contribution_count": contribution_count,
                "donor_entity_key": donor_entity_key,
                "candidate_id": candidate_id,
                "donor_label": _clean_text(row["donor_name"]) or "Unknown Donor",
                "candidate_label": _clean_text(row["candidate_name"]) or candidate_id,
                "office_display": office_label,
                "district_display": district_label,
                "race_label": race_label,
                "party_code": row_party_code or None,
                "party_display": row_party_label,
                "earliest_contribution_date": row["earliest_contribution_date"],
                "latest_contribution_date": row["latest_contribution_date"],
            }
        )

        weighted_degree[donor_node_id] += weight
        weighted_degree[candidate_node_id] += weight
        edge_degree[donor_node_id] += 1
        edge_degree[candidate_node_id] += 1

        total_amount += weight
        edge_earliest = row["earliest_contribution_date"]
        edge_latest = row["latest_contribution_date"]
        if edge_earliest and (earliest_contribution_date is None or edge_earliest < earliest_contribution_date):
            earliest_contribution_date = edge_earliest
        if edge_latest and (latest_contribution_date is None or edge_latest > latest_contribution_date):
            latest_contribution_date = edge_latest

    centrality = [
        {
            "node_id": node_id,
            "label": node["label"],
            "node_type": node["node_type"],
            "weighted_degree": round(weighted_degree.get(node_id, 0.0), 2),
            "degree": edge_degree.get(node_id, 0),
            "candidate_id": node.get("candidate_id"),
            "entity_key": node.get("entity_key"),
            "office_display": node.get("office_display"),
            "district_display": node.get("district_display"),
            "race_label": node.get("race_label"),
            "party_code": node.get("party_code"),
            "party_display": node.get("party_display"),
            "city": node.get("city"),
            "state": node.get("state"),
        }
        for node_id, node in nodes.items()
    ]
    centrality.sort(key=lambda row: (row["weighted_degree"], row["degree"]), reverse=True)

    donor_count = sum(1 for node in nodes.values() if node["node_type"] == "donor")
    candidate_count = sum(1 for node in nodes.values() if node["node_type"] == "candidate")

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "donor_count": donor_count,
            "candidate_count": candidate_count,
            "total_amount": round(total_amount, 2),
            "min_edge_amount": float(max(0.0, min_edge_amount)),
            "cycle": cycle,
            "office_filter": office_filter or None,
            "district_filter": district_filter or None,
            "earliest_contribution_date": earliest_contribution_date,
            "latest_contribution_date": latest_contribution_date,
        },
    }


def _candidate_in_scope(
    candidate_id: str | None,
    *,
    candidate_filter: str,
    candidate_meta: dict[str, dict[str, str]],
    office_filter: str,
    district_filter: str,
) -> bool:
    candidate_key = _clean_text(candidate_id)
    if not candidate_key:
        return False
    if candidate_filter:
        return candidate_key == candidate_filter
    if office_filter or district_filter:
        return _candidate_passes_filter(candidate_meta.get(candidate_key), office_filter, district_filter)
    return True


def get_federal_cross_role_organizations(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    candidate_id: str | None = None,
    limit: int = 200,
    min_total_amount: float = 0.0,
) -> dict:
    """Find organizations that appear as both Schedule A donors and Schedule B/E payees."""
    _ensure_missing_donor_identities(conn, cycle=cycle)

    limit_value = max(1, int(limit))
    min_total_amount_value = max(0.0, float(min_total_amount))
    candidate_filter = _clean_text(candidate_id)
    office_filter = ""
    district_filter = ""
    candidate_meta: dict[str, dict[str, str]] = {}
    if not candidate_filter:
        office_filter = _canonical_office_code(office_code)
        district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""
        if office_filter or district_filter:
            candidate_meta = _candidate_filter_metadata(conn, cycle=cycle)

    entities: dict[str, dict[str, Any]] = {}

    def _ensure_entity(org_key: str, name: str, state: str, zip5: str) -> dict[str, Any]:
        entity = entities.setdefault(
            org_key,
            {
                "organization_key": org_key,
                "organization_name": "",
                "state": "",
                "zip5": "",
                "donor_amount": 0.0,
                "donor_count": 0,
                "schedule_b_amount": 0.0,
                "schedule_b_count": 0,
                "schedule_e_amount": 0.0,
                "schedule_e_count": 0,
                "candidate_ids": set(),
            },
        )
        if not entity["organization_name"] and name:
            entity["organization_name"] = name
        if not entity["state"] and state:
            entity["state"] = state
        if not entity["zip5"] and zip5:
            entity["zip5"] = zip5
        return entity

    if _table_exists(conn, "fec_schedule_a_contributions"):
        donor_rows = conn.execute(
            """
            SELECT
                candidate_id,
                contributor_name AS organization_name,
                contributor_state AS state,
                contributor_zip AS zip_code,
                COUNT(*) AS row_count,
                COALESCE(SUM(contribution_receipt_amount), 0.0) AS total_amount
            FROM fec_schedule_a_contributions
            WHERE candidate_id IS NOT NULL
              AND (? IS NULL OR cycle = ?)
            GROUP BY candidate_id, contributor_name, contributor_state, contributor_zip
            """,
            (cycle, cycle),
        ).fetchall()
        for row in donor_rows:
            candidate_key = _clean_text(row["candidate_id"])
            if not _candidate_in_scope(
                candidate_key,
                candidate_filter=candidate_filter,
                candidate_meta=candidate_meta,
                office_filter=office_filter,
                district_filter=district_filter,
            ):
                continue

            name = _clean_text(row["organization_name"])
            state = _clean_text(row["state"]).upper()
            zip5 = _normalize_zip5(row["zip_code"])
            org_key = _normalize_org_identity(name, state=state, zip_code=zip5)
            if not org_key:
                continue

            entity = _ensure_entity(org_key, name, state, zip5)
            entity["candidate_ids"].add(candidate_key)
            entity["donor_amount"] += float(row["total_amount"] or 0.0)
            entity["donor_count"] += int(row["row_count"] or 0)

    if _table_exists(conn, "fec_schedule_b_disbursements"):
        schedule_b_rows = conn.execute(
            """
            SELECT
                candidate_id,
                recipient_name AS organization_name,
                recipient_state AS state,
                recipient_zip AS zip_code,
                COUNT(*) AS row_count,
                COALESCE(SUM(disbursement_amount), 0.0) AS total_amount
            FROM fec_schedule_b_disbursements
            WHERE candidate_id IS NOT NULL
              AND (? IS NULL OR cycle = ?)
            GROUP BY candidate_id, recipient_name, recipient_state, recipient_zip
            """,
            (cycle, cycle),
        ).fetchall()
        for row in schedule_b_rows:
            candidate_key = _clean_text(row["candidate_id"])
            if not _candidate_in_scope(
                candidate_key,
                candidate_filter=candidate_filter,
                candidate_meta=candidate_meta,
                office_filter=office_filter,
                district_filter=district_filter,
            ):
                continue

            name = _clean_text(row["organization_name"])
            state = _clean_text(row["state"]).upper()
            zip5 = _normalize_zip5(row["zip_code"])
            org_key = _normalize_org_identity(name, state=state, zip_code=zip5)
            if not org_key:
                continue

            entity = _ensure_entity(org_key, name, state, zip5)
            entity["candidate_ids"].add(candidate_key)
            entity["schedule_b_amount"] += float(row["total_amount"] or 0.0)
            entity["schedule_b_count"] += int(row["row_count"] or 0)

    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        schedule_e_rows = conn.execute(
            """
            SELECT
                candidate_id,
                payee_name AS organization_name,
                payee_state AS state,
                payee_zip AS zip_code,
                COUNT(*) AS row_count,
                COALESCE(SUM(expenditure_amount), 0.0) AS total_amount
            FROM fec_schedule_e_independent_expenditures
            WHERE candidate_id IS NOT NULL
              AND (? IS NULL OR cycle = ?)
            GROUP BY candidate_id, payee_name, payee_state, payee_zip
            """,
            (cycle, cycle),
        ).fetchall()
        for row in schedule_e_rows:
            candidate_key = _clean_text(row["candidate_id"])
            if not _candidate_in_scope(
                candidate_key,
                candidate_filter=candidate_filter,
                candidate_meta=candidate_meta,
                office_filter=office_filter,
                district_filter=district_filter,
            ):
                continue

            name = _clean_text(row["organization_name"])
            state = _clean_text(row["state"]).upper()
            zip5 = _normalize_zip5(row["zip_code"])
            org_key = _normalize_org_identity(name, state=state, zip_code=zip5)
            if not org_key:
                continue

            entity = _ensure_entity(org_key, name, state, zip5)
            entity["candidate_ids"].add(candidate_key)
            entity["schedule_e_amount"] += float(row["total_amount"] or 0.0)
            entity["schedule_e_count"] += int(row["row_count"] or 0)

    donor_org_count = 0
    payee_org_count = 0
    matched_rows: list[dict] = []
    total_donor_amount = 0.0
    total_out_amount = 0.0
    for entity in entities.values():
        donor_amount = float(entity["donor_amount"] or 0.0)
        donor_count = int(entity["donor_count"] or 0)
        schedule_b_amount = float(entity["schedule_b_amount"] or 0.0)
        schedule_e_amount = float(entity["schedule_e_amount"] or 0.0)
        schedule_b_count = int(entity["schedule_b_count"] or 0)
        schedule_e_count = int(entity["schedule_e_count"] or 0)
        out_amount = schedule_b_amount + schedule_e_amount
        out_count = schedule_b_count + schedule_e_count

        if donor_count > 0:
            donor_org_count += 1
        if out_count > 0:
            payee_org_count += 1
        if donor_count <= 0 or out_count <= 0:
            continue

        activity_total = donor_amount + out_amount
        if activity_total < min_total_amount_value:
            continue

        net_in_minus_out = donor_amount - out_amount
        matched_rows.append(
            {
                "organization_key": entity["organization_key"],
                "organization_name": entity["organization_name"] or entity["organization_key"],
                "state": entity["state"] or None,
                "zip5": entity["zip5"] or None,
                "candidate_count": len(entity["candidate_ids"]),
                "donor_amount": round(donor_amount, 2),
                "donor_count": donor_count,
                "schedule_b_amount": round(schedule_b_amount, 2),
                "schedule_b_count": schedule_b_count,
                "schedule_e_amount": round(schedule_e_amount, 2),
                "schedule_e_count": schedule_e_count,
                "total_out_amount": round(out_amount, 2),
                "total_out_count": out_count,
                "activity_total": round(activity_total, 2),
                "net_in_minus_out": round(net_in_minus_out, 2),
            }
        )
        total_donor_amount += donor_amount
        total_out_amount += out_amount

    matched_rows.sort(
        key=lambda row: (
            float(row["activity_total"] or 0.0),
            float(row["donor_amount"] or 0.0),
            float(row["total_out_amount"] or 0.0),
        ),
        reverse=True,
    )
    total_matched_rows = len(matched_rows)

    return {
        "rows": matched_rows[:limit_value],
        "summary": {
            "cycle": cycle,
            "office_filter": office_filter or None,
            "district_filter": district_filter or None,
            "candidate_filter": candidate_filter or None,
            "donor_organization_count": donor_org_count,
            "payee_organization_count": payee_org_count,
            "matched_organization_count": total_matched_rows,
            "total_donor_amount": round(total_donor_amount, 2),
            "total_out_amount": round(total_out_amount, 2),
            "min_total_amount": min_total_amount_value,
        },
    }


def _get_candidate_schedule_b_transfer_chains(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    cycle: int | None = None,
    limit: int = 50,
) -> list[dict]:
    if not _table_exists(conn, "fec_schedule_b_disbursements"):
        return []

    where_sql = "WHERE candidate_id = ?"
    params: list[Any] = [candidate_id]
    if cycle is not None:
        where_sql += " AND cycle = ?"
        params.append(int(cycle))

    candidate_lookup_rows = conn.execute(
        """
        SELECT DISTINCT
            fec_candidate_id,
            COALESCE(fec_name, candidate_name, fec_candidate_id) AS candidate_name
        FROM fec_candidate_match
        WHERE fec_candidate_id IS NOT NULL
          AND (? IS NULL OR cycle = ?)
        """,
        (cycle, cycle),
    ).fetchall()
    candidate_lookup = {
        _clean_text(row["fec_candidate_id"]): _clean_text(row["candidate_name"]) or _clean_text(row["fec_candidate_id"])
        for row in candidate_lookup_rows
        if _clean_text(row["fec_candidate_id"])
    }

    committee_lookup_rows = conn.execute(
        """
        SELECT
            cc.committee_id,
            COALESCE(MAX(cc.committee_name), cc.committee_id) AS committee_name,
            COALESCE(MAX(cm.fec_candidate_id), MAX(cc.candidate_id)) AS owner_candidate_id,
            COALESCE(MAX(cm.fec_name), MAX(cm.candidate_name), MAX(cc.candidate_id)) AS owner_candidate_name
        FROM fec_candidate_committees cc
        LEFT JOIN fec_candidate_match cm
          ON cm.fec_candidate_id = cc.candidate_id
         AND cm.cycle = cc.cycle
        WHERE cc.committee_id IS NOT NULL
          AND (? IS NULL OR cc.cycle = ?)
        GROUP BY cc.committee_id
        """,
        (cycle, cycle),
    ).fetchall()
    committee_lookup = {
        _clean_text(row["committee_id"]): {
            "committee_name": _clean_text(row["committee_name"]) or _clean_text(row["committee_id"]),
            "owner_candidate_id": _clean_text(row["owner_candidate_id"]),
            "owner_candidate_name": _clean_text(row["owner_candidate_name"]),
        }
        for row in committee_lookup_rows
        if _clean_text(row["committee_id"])
    }

    transfer_rows = conn.execute(
        f"""
        SELECT
            recipient_candidate_id,
            recipient_candidate_name,
            recipient_committee_id,
            recipient_name,
            COUNT(*) AS transfer_count,
            COALESCE(SUM(disbursement_amount), 0.0) AS total_amount,
            MIN(disbursement_date) AS earliest_disbursement_date,
            MAX(disbursement_date) AS latest_disbursement_date
        FROM fec_schedule_b_disbursements
        {where_sql}
          AND (
              NULLIF(recipient_candidate_id, '') IS NOT NULL
              OR NULLIF(recipient_committee_id, '') IS NOT NULL
          )
        GROUP BY recipient_candidate_id, recipient_candidate_name, recipient_committee_id, recipient_name
        ORDER BY total_amount DESC, transfer_count DESC, latest_disbursement_date DESC
        LIMIT ?
        """,
        params + [max(1, int(limit))],
    ).fetchall()

    output: list[dict] = []
    for row in transfer_rows:
        recipient_candidate_id = _clean_text(row["recipient_candidate_id"])
        recipient_committee_id = _clean_text(row["recipient_committee_id"])
        committee_meta = committee_lookup.get(recipient_committee_id, {})

        resolved_candidate_id = recipient_candidate_id or _clean_text(committee_meta.get("owner_candidate_id"))
        resolved_candidate_name = (
            candidate_lookup.get(resolved_candidate_id)
            or _clean_text(row["recipient_candidate_name"])
            or _clean_text(committee_meta.get("owner_candidate_name"))
            or resolved_candidate_id
            or None
        )
        resolved_committee_name = (
            _clean_text(committee_meta.get("committee_name"))
            or _clean_text(row["recipient_name"])
            or recipient_committee_id
            or None
        )

        if recipient_candidate_id and recipient_committee_id:
            match_type = "candidate_id + committee_id"
        elif recipient_candidate_id:
            match_type = "candidate_id"
        elif recipient_committee_id and _clean_text(committee_meta.get("owner_candidate_id")):
            match_type = "committee_id -> candidate"
        elif recipient_committee_id:
            match_type = "committee_id"
        else:
            match_type = "unresolved"

        output.append(
            {
                "recipient_candidate_id": resolved_candidate_id or None,
                "recipient_candidate_name": resolved_candidate_name,
                "recipient_committee_id": recipient_committee_id or None,
                "recipient_committee_name": resolved_committee_name,
                "match_type": match_type,
                "transfer_count": int(row["transfer_count"] or 0),
                "total_amount": round(float(row["total_amount"] or 0.0), 2),
                "earliest_disbursement_date": row["earliest_disbursement_date"],
                "latest_disbursement_date": row["latest_disbursement_date"],
            }
        )
    return output


def get_federal_multilayer_network_graph(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    min_edge_amount: float = 1000.0,
    limit: int = 1500,
) -> dict:
    """Build a multi-layer flow network across Schedule A/B/E."""
    _ensure_missing_donor_identities(conn, cycle=cycle)

    office_filter = _canonical_office_code(office_code)
    district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""
    edge_limit = max(1, int(limit))
    query_limit = max(edge_limit * 4, edge_limit)
    min_edge = float(max(0.0, min_edge_amount))
    candidate_meta = _candidate_filter_metadata(conn, cycle=cycle)

    staged_edges: list[dict] = []

    schedule_a_edges, _candidate_meta, _office_scope, _district_scope = _filtered_edge_rows(
        conn,
        cycle=cycle,
        office_code=office_filter or None,
        district_code=district_filter or None,
    )
    for row in schedule_a_edges:
        donor_key = _clean_text(row.get("donor_entity_key"))
        candidate_id_value = _clean_text(row.get("candidate_id"))
        if not donor_key or not candidate_id_value:
            continue

        weight = float(row.get("total_amount") or 0.0)
        if weight < min_edge:
            continue

        staged_edges.append(
            {
                "_source_id": f"donor:{donor_key}",
                "_target_id": f"candidate:{candidate_id_value}",
                "_source_node": {
                    "id": f"donor:{donor_key}",
                    "label": _clean_text(row.get("donor_name")) or "Unknown Donor",
                    "node_type": "donor",
                    "entity_key": donor_key,
                    "city": _clean_text(row.get("donor_city")) or None,
                    "state": _clean_text(row.get("donor_state")) or None,
                },
                "_target_node": {
                    "id": f"candidate:{candidate_id_value}",
                    "label": _clean_text(row.get("candidate_name")) or candidate_id_value,
                    "node_type": "candidate",
                    "candidate_id": candidate_id_value,
                    "office_display": row.get("office_display"),
                    "district_display": row.get("district_display"),
                    "race_label": row.get("race_label"),
                    "party_code": row.get("party_code"),
                    "party_display": row.get("party_display"),
                },
                "source": f"donor:{donor_key}",
                "target": f"candidate:{candidate_id_value}",
                "edge_type": "donor_candidate",
                "layer_label": "Schedule A: Donor -> Candidate",
                "weight": round(weight, 2),
                "row_count": int(row.get("contribution_count") or 0),
                "candidate_id": candidate_id_value,
                "candidate_name": _clean_text(row.get("candidate_name")) or candidate_id_value,
                "race_label": row.get("race_label"),
                "party_display": row.get("party_display"),
                "earliest_date": row.get("earliest_contribution_date"),
                "latest_date": row.get("latest_contribution_date"),
            }
        )

    if _table_exists(conn, "fec_schedule_b_disbursements"):
        schedule_b_rows = conn.execute(
            """
            SELECT
                sb.candidate_id,
                sb.committee_id,
                COALESCE(MAX(sb.committee_name), sb.committee_id, 'Unknown Committee') AS committee_name,
                COALESCE(NULLIF(sb.recipient_name, ''), NULLIF(sb.recipient_committee_id, ''), sb.sub_id) AS recipient_name,
                COALESCE(MAX(sb.recipient_city), '') AS recipient_city,
                COALESCE(MAX(sb.recipient_state), '') AS recipient_state,
                COALESCE(MAX(sb.recipient_zip), '') AS recipient_zip,
                COALESCE(MAX(sb.recipient_committee_id), '') AS recipient_committee_id,
                COALESCE(MAX(sb.recipient_candidate_id), '') AS recipient_candidate_id,
                COUNT(*) AS row_count,
                COALESCE(SUM(sb.disbursement_amount), 0.0) AS total_amount,
                MIN(sb.disbursement_date) AS earliest_date,
                MAX(sb.disbursement_date) AS latest_date
            FROM fec_schedule_b_disbursements sb
            WHERE sb.candidate_id IS NOT NULL
              AND sb.committee_id IS NOT NULL
              AND (? IS NULL OR sb.cycle = ?)
            GROUP BY
                sb.candidate_id,
                sb.committee_id,
                COALESCE(NULLIF(sb.recipient_name, ''), NULLIF(sb.recipient_committee_id, ''), sb.sub_id),
                COALESCE(sb.recipient_state, ''),
                COALESCE(sb.recipient_zip, '')
            HAVING COALESCE(SUM(sb.disbursement_amount), 0.0) >= ?
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            (cycle, cycle, min_edge, query_limit),
        ).fetchall()
        for row in schedule_b_rows:
            candidate_id_value = _clean_text(row["candidate_id"])
            if not _candidate_in_scope(
                candidate_id_value,
                candidate_filter="",
                candidate_meta=candidate_meta,
                office_filter=office_filter,
                district_filter=district_filter,
            ):
                continue

            committee_id = _clean_text(row["committee_id"])
            if not committee_id:
                continue

            recipient_name = _clean_text(row["recipient_name"])
            recipient_state = _clean_text(row["recipient_state"]).upper()
            recipient_zip5 = _normalize_zip5(row["recipient_zip"])
            recipient_identity = _normalize_org_identity(
                recipient_name,
                state=recipient_state,
                zip_code=recipient_zip5,
            )
            if not recipient_identity:
                recipient_identity = _build_donor_key(recipient_name, recipient_state, recipient_zip5, None)
            if not recipient_identity:
                continue

            weight = float(row["total_amount"] or 0.0)
            if weight < min_edge:
                continue

            candidate_row_meta = candidate_meta.get(candidate_id_value, {})
            candidate_name = _clean_text(candidate_row_meta.get("candidate_name")) or candidate_id_value

            staged_edges.append(
                {
                    "_source_id": f"candidate_committee:{committee_id}",
                    "_target_id": f"vendor:{recipient_identity}",
                    "_source_node": {
                        "id": f"candidate_committee:{committee_id}",
                        "label": _clean_text(row["committee_name"]) or committee_id,
                        "node_type": "candidate_committee",
                        "committee_id": committee_id,
                        "candidate_id": candidate_id_value,
                        "candidate_name": candidate_name,
                        "race_label": candidate_row_meta.get("race_label"),
                        "party_display": candidate_row_meta.get("party_display"),
                    },
                    "_target_node": {
                        "id": f"vendor:{recipient_identity}",
                        "label": recipient_name or recipient_identity,
                        "node_type": "vendor",
                        "entity_key": recipient_identity,
                        "city": _clean_text(row["recipient_city"]) or None,
                        "state": recipient_state or None,
                        "zip5": recipient_zip5 or None,
                    },
                    "source": f"candidate_committee:{committee_id}",
                    "target": f"vendor:{recipient_identity}",
                    "edge_type": "committee_vendor",
                    "layer_label": "Schedule B: Candidate Committee -> Vendor",
                    "weight": round(weight, 2),
                    "row_count": int(row["row_count"] or 0),
                    "candidate_id": candidate_id_value,
                    "candidate_name": candidate_name,
                    "committee_id": committee_id,
                    "committee_name": _clean_text(row["committee_name"]) or committee_id,
                    "counterparty_name": recipient_name or recipient_identity,
                    "counterparty_state": recipient_state or None,
                    "counterparty_zip5": recipient_zip5 or None,
                    "recipient_candidate_id": _clean_text(row["recipient_candidate_id"]) or None,
                    "recipient_committee_id": _clean_text(row["recipient_committee_id"]) or None,
                    "race_label": candidate_row_meta.get("race_label"),
                    "party_display": candidate_row_meta.get("party_display"),
                    "earliest_date": row["earliest_date"],
                    "latest_date": row["latest_date"],
                }
            )

    if _table_exists(conn, "fec_schedule_e_independent_expenditures"):
        support_oppose_values_agg = _distinct_concat_aggregate_sql(
            conn,
            "COALESCE(se.support_oppose_indicator, '')",
        )
        schedule_e_rows = conn.execute(
            f"""
            SELECT
                se.candidate_id,
                se.committee_id,
                COALESCE(MAX(se.committee_name), se.committee_id, 'Unknown IE Committee') AS committee_name,
                {support_oppose_values_agg} AS support_oppose_values,
                COUNT(*) AS row_count,
                COALESCE(SUM(se.expenditure_amount), 0.0) AS total_amount,
                MIN(se.expenditure_date) AS earliest_date,
                MAX(se.expenditure_date) AS latest_date
            FROM fec_schedule_e_independent_expenditures se
            WHERE se.candidate_id IS NOT NULL
              AND se.committee_id IS NOT NULL
              AND (? IS NULL OR se.cycle = ?)
            GROUP BY se.candidate_id, se.committee_id
            HAVING COALESCE(SUM(se.expenditure_amount), 0.0) >= ?
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            (cycle, cycle, min_edge, query_limit),
        ).fetchall()
        for row in schedule_e_rows:
            candidate_id_value = _clean_text(row["candidate_id"])
            if not _candidate_in_scope(
                candidate_id_value,
                candidate_filter="",
                candidate_meta=candidate_meta,
                office_filter=office_filter,
                district_filter=district_filter,
            ):
                continue

            committee_id = _clean_text(row["committee_id"])
            if not committee_id:
                continue

            weight = float(row["total_amount"] or 0.0)
            if weight < min_edge:
                continue

            candidate_row_meta = candidate_meta.get(candidate_id_value, {})
            candidate_name = _clean_text(candidate_row_meta.get("candidate_name")) or candidate_id_value

            staged_edges.append(
                {
                    "_source_id": f"ie_committee:{committee_id}",
                    "_target_id": f"candidate:{candidate_id_value}",
                    "_source_node": {
                        "id": f"ie_committee:{committee_id}",
                        "label": _clean_text(row["committee_name"]) or committee_id,
                        "node_type": "ie_committee",
                        "committee_id": committee_id,
                    },
                    "_target_node": {
                        "id": f"candidate:{candidate_id_value}",
                        "label": candidate_name,
                        "node_type": "candidate",
                        "candidate_id": candidate_id_value,
                        "office_display": candidate_row_meta.get("office_display"),
                        "district_display": candidate_row_meta.get("district_display"),
                        "race_label": candidate_row_meta.get("race_label"),
                        "party_code": candidate_row_meta.get("party_code"),
                        "party_display": candidate_row_meta.get("party_display"),
                    },
                    "source": f"ie_committee:{committee_id}",
                    "target": f"candidate:{candidate_id_value}",
                    "edge_type": "ie_committee_candidate",
                    "layer_label": "Schedule E: IE Committee -> Candidate",
                    "weight": round(weight, 2),
                    "row_count": int(row["row_count"] or 0),
                    "committee_id": committee_id,
                    "committee_name": _clean_text(row["committee_name"]) or committee_id,
                    "candidate_id": candidate_id_value,
                    "candidate_name": candidate_name,
                    "support_oppose_values": _clean_text(row["support_oppose_values"]) or None,
                    "race_label": candidate_row_meta.get("race_label"),
                    "party_display": candidate_row_meta.get("party_display"),
                    "earliest_date": row["earliest_date"],
                    "latest_date": row["latest_date"],
                }
            )

    staged_edges.sort(
        key=lambda row: (
            float(row.get("weight") or 0.0),
            int(row.get("row_count") or 0),
        ),
        reverse=True,
    )
    staged_edges = staged_edges[:edge_limit]

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict] = []
    weighted_degree: dict[str, float] = defaultdict(float)
    edge_degree: dict[str, int] = defaultdict(int)
    total_amount = 0.0
    earliest_date = None
    latest_date = None
    layer_stats = {
        "donor_candidate": {"edge_count": 0, "total_amount": 0.0, "label": "Schedule A"},
        "committee_vendor": {"edge_count": 0, "total_amount": 0.0, "label": "Schedule B"},
        "ie_committee_candidate": {"edge_count": 0, "total_amount": 0.0, "label": "Schedule E"},
    }

    for row in staged_edges:
        source_id = row["_source_id"]
        target_id = row["_target_id"]
        source_node = dict(row["_source_node"])
        target_node = dict(row["_target_node"])
        edge = {key: value for key, value in row.items() if not key.startswith("_")}
        edge["source_label"] = source_node.get("label")
        edge["target_label"] = target_node.get("label")
        edge["source_node_type"] = source_node.get("node_type")
        edge["target_node_type"] = target_node.get("node_type")

        nodes.setdefault(source_id, source_node)
        nodes.setdefault(target_id, target_node)
        edges.append(edge)

        weight = float(edge.get("weight") or 0.0)
        weighted_degree[source_id] += weight
        weighted_degree[target_id] += weight
        edge_degree[source_id] += 1
        edge_degree[target_id] += 1
        total_amount += weight

        edge_type = _clean_text(edge.get("edge_type"))
        if edge_type in layer_stats:
            layer_stats[edge_type]["edge_count"] += 1
            layer_stats[edge_type]["total_amount"] += weight

        edge_earliest = edge.get("earliest_date")
        edge_latest = edge.get("latest_date")
        if edge_earliest and (earliest_date is None or edge_earliest < earliest_date):
            earliest_date = edge_earliest
        if edge_latest and (latest_date is None or edge_latest > latest_date):
            latest_date = edge_latest

    centrality = [
        {
            "node_id": node_id,
            "label": node.get("label"),
            "node_type": node.get("node_type"),
            "weighted_degree": round(weighted_degree.get(node_id, 0.0), 2),
            "degree": edge_degree.get(node_id, 0),
            "candidate_id": node.get("candidate_id"),
            "committee_id": node.get("committee_id"),
            "entity_key": node.get("entity_key"),
            "race_label": node.get("race_label"),
            "party_display": node.get("party_display"),
            "state": node.get("state"),
            "city": node.get("city"),
            "zip5": node.get("zip5"),
        }
        for node_id, node in nodes.items()
    ]
    centrality.sort(key=lambda row: (row["weighted_degree"], row["degree"]), reverse=True)

    donor_count = sum(1 for node in nodes.values() if node.get("node_type") == "donor")
    candidate_count = sum(1 for node in nodes.values() if node.get("node_type") == "candidate")
    candidate_committee_count = sum(1 for node in nodes.values() if node.get("node_type") == "candidate_committee")
    vendor_count = sum(1 for node in nodes.values() if node.get("node_type") == "vendor")
    ie_committee_count = sum(1 for node in nodes.values() if node.get("node_type") == "ie_committee")

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "donor_count": donor_count,
            "candidate_count": candidate_count,
            "candidate_committee_count": candidate_committee_count,
            "vendor_count": vendor_count,
            "ie_committee_count": ie_committee_count,
            "total_amount": round(total_amount, 2),
            "donor_candidate_total_amount": round(layer_stats["donor_candidate"]["total_amount"], 2),
            "committee_vendor_total_amount": round(layer_stats["committee_vendor"]["total_amount"], 2),
            "ie_committee_candidate_total_amount": round(layer_stats["ie_committee_candidate"]["total_amount"], 2),
            "min_edge_amount": min_edge,
            "cycle": cycle,
            "office_filter": office_filter or None,
            "district_filter": district_filter or None,
            "earliest_date": earliest_date,
            "latest_date": latest_date,
        },
        "layer_summary": [
            {
                "edge_type": edge_type,
                "label": payload["label"],
                "edge_count": int(payload["edge_count"] or 0),
                "total_amount": round(float(payload["total_amount"] or 0.0), 2),
            }
            for edge_type, payload in layer_stats.items()
        ],
    }


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator)


def _euclidean_distance(left: list[float], right: list[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5


def _standardize_matrix(rows: list[list[float]]) -> list[list[float]]:
    if not rows:
        return []
    cols = len(rows[0])
    means = []
    stds = []
    for col in range(cols):
        values = [row[col] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
        std = variance**0.5
        means.append(mean)
        stds.append(std if std > 1e-9 else 1.0)
    return [[(row[col] - means[col]) / stds[col] for col in range(cols)] for row in rows]


def _kmeans_labels(
    rows: list[list[float]],
    k: int,
    iterations: int = 20,
) -> list[int]:
    if not rows:
        return []
    n = len(rows)
    k = max(1, min(int(k), n))
    if k == 1:
        return [0 for _ in rows]

    # Deterministic centroid initialization by evenly spaced sorted points.
    scored = sorted(enumerate(rows), key=lambda item: sum(item[1]))
    centroids = []
    for i in range(k):
        idx = int(i * (n - 1) / max(1, k - 1))
        centroids.append(list(scored[idx][1]))

    labels = [0 for _ in rows]
    for _ in range(max(1, int(iterations))):
        changed = False
        for i, row in enumerate(rows):
            best_label = 0
            best_distance = None
            for label, centroid in enumerate(centroids):
                distance = _euclidean_distance(row, centroid)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_label = label
            if labels[i] != best_label:
                labels[i] = best_label
                changed = True

        clusters = [[] for _ in range(k)]
        for label, row in zip(labels, rows):
            clusters[label].append(row)
        for label in range(k):
            if not clusters[label]:
                continue
            cols = len(clusters[label][0])
            centroids[label] = [
                sum(point[col] for point in clusters[label]) / len(clusters[label])
                for col in range(cols)
            ]
        if not changed:
            break
    return labels


def _dbscan_labels(
    rows: list[list[float]],
    eps: float = 0.9,
    min_samples: int = 6,
) -> list[int]:
    if not rows:
        return []
    n = len(rows)
    eps = max(0.01, float(eps))
    min_samples = max(2, int(min_samples))

    labels = [-99 for _ in range(n)]  # -99 unvisited, -1 noise, >=0 cluster id
    cluster_id = 0

    def region_query(idx: int) -> list[int]:
        center = rows[idx]
        neighbors: list[int] = []
        for j, row in enumerate(rows):
            if _euclidean_distance(center, row) <= eps:
                neighbors.append(j)
        return neighbors

    for i in range(n):
        if labels[i] != -99:
            continue
        neighbors = region_query(i)
        if len(neighbors) < min_samples:
            labels[i] = -1
            continue

        labels[i] = cluster_id
        seed_set = [neighbor for neighbor in neighbors if neighbor != i]
        p = 0
        while p < len(seed_set):
            point = seed_set[p]
            if labels[point] == -1:
                labels[point] = cluster_id
            if labels[point] != -99:
                p += 1
                continue

            labels[point] = cluster_id
            point_neighbors = region_query(point)
            if len(point_neighbors) >= min_samples:
                existing = set(seed_set)
                for neighbor in point_neighbors:
                    if neighbor not in existing:
                        seed_set.append(neighbor)
                        existing.add(neighbor)
            p += 1

        cluster_id += 1

    return labels


def _weighted_pagerank(
    adjacency: dict[str, list[tuple[str, float]]],
    damping: float = 0.85,
    iterations: int = 25,
) -> dict[str, float]:
    if not adjacency:
        return {}
    nodes = list(adjacency.keys())
    n = len(nodes)
    base = (1.0 - damping) / n
    ranks = {node: 1.0 / n for node in nodes}

    for _ in range(max(1, int(iterations))):
        next_ranks = {node: base for node in nodes}
        for node in nodes:
            neighbors = adjacency.get(node, [])
            weight_sum = sum(max(0.0, weight) for _neighbor, weight in neighbors)
            if weight_sum <= 0:
                spill = damping * ranks[node] / n
                for target in nodes:
                    next_ranks[target] += spill
                continue
            for neighbor, weight in neighbors:
                if neighbor not in next_ranks:
                    continue
                next_ranks[neighbor] += damping * ranks[node] * (max(0.0, weight) / weight_sum)
        ranks = next_ranks

    max_rank = max(ranks.values()) if ranks else 1.0
    if max_rank <= 0:
        return ranks
    return {node: value / max_rank for node, value in ranks.items()}


def _extract_zip5_from_address(value: str | None) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    m = re.search(r"(\d{5})(?:-\d{4})?$", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{5})", text)
    return m.group(1) if m else ""


def _chunked(values: list[str], size: int = 900) -> list[list[str]]:
    if size <= 0:
        size = 900
    return [values[i : i + size] for i in range(0, len(values), size)]


def _ensure_fec_local_donor_matches_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fec_local_donor_matches (
            federal_donor_entity_key TEXT NOT NULL,
            local_donor_key TEXT NOT NULL,
            primary_local_donor_key TEXT,
            federal_donor_name TEXT,
            local_donor_name TEXT,
            federal_donor_state TEXT,
            local_donor_state TEXT,
            federal_donor_zip TEXT,
            local_donor_zip TEXT,
            federal_total_amount REAL NOT NULL DEFAULT 0,
            local_total_amount REAL NOT NULL DEFAULT 0,
            federal_contribution_count INTEGER NOT NULL DEFAULT 0,
            local_contribution_count INTEGER NOT NULL DEFAULT 0,
            local_committee_count INTEGER NOT NULL DEFAULT 0,
            match_method TEXT NOT NULL,
            confidence_score REAL NOT NULL DEFAULT 0,
            local_donor_keys_json TEXT,
            refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (federal_donor_entity_key, local_donor_key)
        );

        CREATE INDEX IF NOT EXISTS idx_fec_local_matches_local_key
            ON fec_local_donor_matches(local_donor_key);
        CREATE INDEX IF NOT EXISTS idx_fec_local_matches_confidence
            ON fec_local_donor_matches(confidence_score DESC, match_method);
        """
    )


def _candidate_filter_metadata(
    conn: sqlite3.Connection,
    cycle: int | None = None,
) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT DISTINCT
            fec_candidate_id,
            office_code,
            fec_office,
            office,
            district_code,
            fec_district,
            district,
            party_code,
            fec_party,
            party,
            COALESCE(fec_name, candidate_name, 'Unknown Candidate') AS candidate_name
        FROM fec_candidate_match
        WHERE fec_candidate_id IS NOT NULL
          AND (? IS NULL OR cycle = ?)
        """,
        (cycle, cycle),
    ).fetchall()

    output: dict[str, dict[str, str]] = {}
    for row in rows:
        candidate_id = _clean_text(row["fec_candidate_id"])
        if not candidate_id:
            continue
        office_code = _canonical_office_code(row["office_code"], row["fec_office"], row["office"])
        district_code = _canonical_district_code(office_code, row["district_code"], row["fec_district"], row["district"])
        office_display = _office_display_label(office_code, row["office"])
        district_display = _district_display_label(office_code, district_code, row["district"])
        party_code = _canonical_party_code(row["party_code"], row["fec_party"], row["party"])
        output[candidate_id] = {
            "candidate_name": _clean_text(row["candidate_name"]) or candidate_id,
            "office_code": office_code,
            "district_code": district_code,
            "office_display": office_display,
            "district_display": district_display,
            "race_label": f"{office_display} - {district_display}",
            "party_code": party_code,
            "party_display": _party_display_label(party_code, row["party"]),
        }
    return output


def _candidate_passes_filter(
    candidate_meta: dict[str, str] | None,
    office_filter: str,
    district_filter: str,
) -> bool:
    if not candidate_meta:
        return False
    if office_filter and candidate_meta.get("office_code", "") != office_filter:
        return False
    if district_filter and candidate_meta.get("district_code", "") != district_filter:
        return False
    return True


def _federal_edge_rows(
    conn: sqlite3.Connection,
    cycle: int | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_entity_key,
            COALESCE(MAX(sa.contributor_name), 'Unknown Donor') AS donor_name,
            COALESCE(MAX(sa.contributor_city), '') AS donor_city,
            COALESCE(MAX(sa.contributor_state), '') AS donor_state,
            COALESCE(MAX(sa.contributor_zip), '') AS donor_zip,
            COALESCE(MAX(sa.contributor_employer), '') AS donor_employer,
            COALESCE(MAX(sa.contributor_occupation), '') AS donor_occupation,
            sa.candidate_id AS candidate_id,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions sa
        WHERE sa.candidate_id IS NOT NULL
          AND (? IS NULL OR sa.cycle = ?)
                GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.candidate_id
        """,
        (cycle, cycle),
    ).fetchall()
    return [dict(row) for row in rows]


def _federal_donor_committee_edges(
    conn: sqlite3.Connection,
    cycle: int | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS donor_entity_key,
            COALESCE(MAX(sa.contributor_name), 'Unknown Donor') AS donor_name,
            sa.committee_id AS committee_id,
            COALESCE(MAX(sa.committee_name), sa.committee_id, 'Unknown Committee') AS committee_name,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions sa
        WHERE sa.committee_id IS NOT NULL
          AND (? IS NULL OR sa.cycle = ?)
                GROUP BY COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id), sa.committee_id
        """,
        (cycle, cycle),
    ).fetchall()
    return [dict(row) for row in rows]


def _federal_committee_candidate_edges(
    conn: sqlite3.Connection,
    cycle: int | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            sa.committee_id AS committee_id,
            COALESCE(MAX(sa.committee_name), sa.committee_id, 'Unknown Committee') AS committee_name,
            sa.candidate_id AS candidate_id,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date
        FROM fec_schedule_a_contributions sa
        WHERE sa.committee_id IS NOT NULL
          AND sa.candidate_id IS NOT NULL
          AND (? IS NULL OR sa.cycle = ?)
        GROUP BY sa.committee_id, sa.candidate_id
        """,
        (cycle, cycle),
    ).fetchall()
    return [dict(row) for row in rows]


def _filtered_edge_rows(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
) -> tuple[list[dict], dict[str, dict[str, str]], str, str]:
    office_filter = _canonical_office_code(office_code)
    district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""
    candidate_meta = _candidate_filter_metadata(conn, cycle=cycle)
    base_edges = _federal_edge_rows(conn, cycle=cycle)

    filtered: list[dict] = []
    for row in base_edges:
        candidate_id = _clean_text(row.get("candidate_id"))
        meta = candidate_meta.get(candidate_id)
        if office_filter or district_filter:
            if not _candidate_passes_filter(meta, office_filter, district_filter):
                continue
        output_row = dict(row)
        if meta:
            output_row["candidate_name"] = meta["candidate_name"]
            output_row["office_code"] = meta["office_code"]
            output_row["district_code"] = meta["district_code"]
            output_row["office_display"] = meta["office_display"]
            output_row["district_display"] = meta["district_display"]
            output_row["race_label"] = meta["race_label"]
            output_row["party_code"] = meta["party_code"]
            output_row["party_display"] = meta["party_display"]
        else:
            output_row["candidate_name"] = candidate_id or "Unknown Candidate"
            output_row["office_code"] = ""
            output_row["district_code"] = ""
            output_row["office_display"] = "Unknown Office"
            output_row["district_display"] = "-"
            output_row["race_label"] = f"{output_row['office_display']} - {output_row['district_display']}"
            output_row["party_code"] = ""
            output_row["party_display"] = "Unknown Party"
        filtered.append(output_row)

    return filtered, candidate_meta, office_filter, district_filter


def get_federal_donor_segmentation(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    method: str = "kmeans",
    donor_limit: int = 2000,
    kmeans_k: int = 5,
    dbscan_eps: float = 1.0,
    dbscan_min_samples: int = 8,
) -> dict:
    """Segment federal donors by amount/frequency/breadth profiles."""
    rows, _candidate_meta, office_filter, district_filter = _filtered_edge_rows(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
    )

    donors: dict[str, dict[str, Any]] = {}
    for row in rows:
        donor_key = _clean_text(row.get("donor_entity_key"))
        if not donor_key:
            continue
        donor = donors.setdefault(
            donor_key,
            {
                "donor_entity_key": donor_key,
                "donor_name": _clean_text(row.get("donor_name")) or "Unknown Donor",
                "donor_city": _clean_text(row.get("donor_city")) or None,
                "donor_state": _clean_text(row.get("donor_state")) or None,
                "donor_zip": _normalize_zip5(row.get("donor_zip")) or None,
                "donor_employer": _clean_text(row.get("donor_employer")) or None,
                "donor_occupation": _clean_text(row.get("donor_occupation")) or None,
                "total_amount": 0.0,
                "contribution_count": 0,
                "candidate_ids": set(),
            },
        )
        donor["total_amount"] += float(row.get("total_amount") or 0.0)
        donor["contribution_count"] += int(row.get("contribution_count") or 0)
        candidate_id = _clean_text(row.get("candidate_id"))
        if candidate_id:
            donor["candidate_ids"].add(candidate_id)

    donor_rows = list(donors.values())
    donor_rows.sort(key=lambda row: (row["total_amount"], row["contribution_count"]), reverse=True)
    donor_rows = donor_rows[: max(50, min(10000, int(donor_limit)))]

    feature_rows: list[list[float]] = []
    for donor in donor_rows:
        total_amount = float(donor["total_amount"] or 0.0)
        contribution_count = int(donor["contribution_count"] or 0)
        candidate_count = len(donor["candidate_ids"])
        avg_amount = _safe_div(total_amount, contribution_count)
        is_in_state = 1.0 if _clean_text(donor.get("donor_state")).upper() == "IL" else 0.0
        feature_rows.append(
            [
                (total_amount + 1.0) ** 0.5,
                (contribution_count + 1.0) ** 0.5,
                float(candidate_count),
                (avg_amount + 1.0) ** 0.5,
                is_in_state,
            ]
        )

    standardized = _standardize_matrix(feature_rows)
    method_key = _clean_text(method).lower()
    if method_key == "dbscan":
        labels = _dbscan_labels(standardized, eps=dbscan_eps, min_samples=dbscan_min_samples)
    else:
        method_key = "kmeans"
        labels = _kmeans_labels(standardized, k=kmeans_k, iterations=20)

    clusters: dict[str, dict[str, Any]] = {}
    for donor, label in zip(donor_rows, labels):
        if label < 0:
            cluster_id = "noise"
            cluster_name = "Noise / Unclustered"
        else:
            cluster_id = f"cluster_{label}"
            cluster_name = f"Cluster {label + 1}"

        candidate_count = len(donor["candidate_ids"])
        donor["candidate_count"] = candidate_count
        donor["avg_amount"] = round(_safe_div(donor["total_amount"], donor["contribution_count"]), 2)
        donor["cluster_id"] = cluster_id
        donor["cluster_name"] = cluster_name
        donor.pop("candidate_ids", None)

        cluster = clusters.setdefault(
            cluster_id,
            {
                "cluster_id": cluster_id,
                "cluster_name": cluster_name,
                "donor_count": 0,
                "total_amount": 0.0,
                "contribution_count": 0,
                "candidate_count_sum": 0,
                "in_state_count": 0,
                "top_donors": [],
            },
        )
        cluster["donor_count"] += 1
        cluster["total_amount"] += float(donor["total_amount"] or 0.0)
        cluster["contribution_count"] += int(donor["contribution_count"] or 0)
        cluster["candidate_count_sum"] += int(candidate_count)
        if _clean_text(donor.get("donor_state")).upper() == "IL":
            cluster["in_state_count"] += 1
        cluster["top_donors"].append(
            {
                "donor_entity_key": donor["donor_entity_key"],
                "donor_name": donor["donor_name"],
                "total_amount": round(float(donor["total_amount"] or 0.0), 2),
                "contribution_count": int(donor["contribution_count"] or 0),
                "candidate_count": int(candidate_count),
            }
        )

    cluster_rows = []
    for cluster in clusters.values():
        donor_count = int(cluster["donor_count"] or 0)
        contribution_count = int(cluster["contribution_count"] or 0)
        total_amount = float(cluster["total_amount"] or 0.0)
        top_donors = sorted(cluster["top_donors"], key=lambda row: row["total_amount"], reverse=True)[:5]
        cluster_rows.append(
            {
                "cluster_id": cluster["cluster_id"],
                "cluster_name": cluster["cluster_name"],
                "donor_count": donor_count,
                "total_amount": round(total_amount, 2),
                "contribution_count": contribution_count,
                "avg_total_amount": round(_safe_div(total_amount, donor_count), 2),
                "avg_contribution_count": round(_safe_div(contribution_count, donor_count), 2),
                "avg_candidate_count": round(_safe_div(cluster["candidate_count_sum"], donor_count), 2),
                "in_state_share": round(_safe_div(cluster["in_state_count"], donor_count), 4),
                "top_donors": top_donors,
            }
        )
    cluster_rows.sort(key=lambda row: row["total_amount"], reverse=True)

    return {
        "method": method_key,
        "cycle": cycle,
        "office_filter": office_filter or None,
        "district_filter": district_filter or None,
        "donor_count": len(donor_rows),
        "cluster_count": len(cluster_rows),
        "clusters": cluster_rows,
        "sample_donors": donor_rows[:100],
    }


def get_federal_donor_network_clusters(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    min_edge_amount: float = 1000.0,
    limit: int = 1200,
) -> dict:
    """Find donor communities using weighted label propagation on donor-candidate graph."""
    network = get_federal_network_graph(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
        min_edge_amount=min_edge_amount,
        limit=limit,
    )

    nodes = {node["id"]: dict(node) for node in network["nodes"]}
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in network["edges"]:
        source = edge["source"]
        target = edge["target"]
        weight = float(edge.get("weight") or 0.0)
        adjacency[source].append((target, weight))
        adjacency[target].append((source, weight))

    labels = {node_id: node_id for node_id in nodes.keys()}
    order = sorted(
        nodes.keys(),
        key=lambda node_id: sum(weight for _neighbor, weight in adjacency.get(node_id, [])),
        reverse=True,
    )
    for _ in range(20):
        changed = False
        for node_id in order:
            scores: dict[str, float] = defaultdict(float)
            for neighbor, weight in adjacency.get(node_id, []):
                scores[labels.get(neighbor, neighbor)] += max(0.0, weight)
            if not scores:
                continue
            best_label = max(scores.items(), key=lambda item: item[1])[0]
            if labels.get(node_id) != best_label:
                labels[node_id] = best_label
                changed = True
        if not changed:
            break

    clusters: dict[str, dict[str, Any]] = {}
    for node_id, node in nodes.items():
        label = labels.get(node_id, node_id)
        cluster = clusters.setdefault(
            label,
            {
                "cluster_id": label,
                "donors": [],
                "candidates": [],
                "total_weight": 0.0,
                "edge_count": 0,
            },
        )
        if node.get("node_type") == "donor":
            cluster["donors"].append(node)
        elif node.get("node_type") == "candidate":
            cluster["candidates"].append(node)

    for edge in network["edges"]:
        source_label = labels.get(edge["source"])
        target_label = labels.get(edge["target"])
        if source_label != target_label:
            continue
        cluster = clusters.get(source_label)
        if not cluster:
            continue
        cluster["edge_count"] += 1
        cluster["total_weight"] += float(edge.get("weight") or 0.0)

    cluster_rows = []
    for cluster in clusters.values():
        donor_count = len(cluster["donors"])
        candidate_count = len(cluster["candidates"])
        if donor_count == 0 or candidate_count == 0:
            continue
        top_donors = sorted(
            [
                {
                    "donor_entity_key": donor.get("entity_key"),
                    "donor_name": donor.get("label"),
                    "weighted_degree": round(float(donor.get("weighted_degree") or 0.0), 2),
                    "degree": int(donor.get("degree") or 0),
                }
                for donor in cluster["donors"]
            ],
            key=lambda row: row["weighted_degree"],
            reverse=True,
        )[:5]
        top_candidates = sorted(
            [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "candidate_name": candidate.get("label"),
                    "weighted_degree": round(float(candidate.get("weighted_degree") or 0.0), 2),
                    "degree": int(candidate.get("degree") or 0),
                    "office_display": candidate.get("office_display"),
                    "district_display": candidate.get("district_display"),
                }
                for candidate in cluster["candidates"]
            ],
            key=lambda row: row["weighted_degree"],
            reverse=True,
        )[:5]

        cluster_rows.append(
            {
                "cluster_id": cluster["cluster_id"],
                "donor_count": donor_count,
                "candidate_count": candidate_count,
                "edge_count": int(cluster["edge_count"] or 0),
                "total_weight": round(float(cluster["total_weight"] or 0.0), 2),
                "top_donors": top_donors,
                "top_candidates": top_candidates,
            }
        )

    cluster_rows.sort(key=lambda row: (row["total_weight"], row["edge_count"]), reverse=True)
    for idx, row in enumerate(cluster_rows, start=1):
        anchor_candidate = row["top_candidates"][0] if row.get("top_candidates") else {}
        anchor_donor = row["top_donors"][0] if row.get("top_donors") else {}
        anchor_candidate_name = _clean_text(anchor_candidate.get("candidate_name"))
        anchor_donor_name = _clean_text(anchor_donor.get("donor_name"))
        office_display = _clean_text(anchor_candidate.get("office_display"))
        district_display = _clean_text(anchor_candidate.get("district_display"))
        race_display = ""
        if office_display or district_display:
            race_display = f"{office_display or 'Office?'}-{district_display or '?'}"

        if anchor_candidate_name:
            if race_display:
                cluster_label = f"Cluster {idx}: {anchor_candidate_name} ({race_display})"
            else:
                cluster_label = f"Cluster {idx}: {anchor_candidate_name}"
        elif anchor_donor_name:
            cluster_label = f"Cluster {idx}: {anchor_donor_name}"
        else:
            cluster_label = f"Cluster {idx}"

        row["cluster_rank"] = idx
        row["cluster_label"] = cluster_label
        row["cluster_id_raw"] = row.get("cluster_id")
        row["cluster_anchor_candidate"] = anchor_candidate_name or None
        row["cluster_anchor_candidate_id"] = _clean_text(anchor_candidate.get("candidate_id")) or None
        row["cluster_anchor_donor"] = anchor_donor_name or None
        row["cluster_anchor_donor_entity_key"] = _clean_text(anchor_donor.get("donor_entity_key")) or None

    return {
        "cycle": cycle,
        "office_filter": network["summary"].get("office_filter"),
        "district_filter": network["summary"].get("district_filter"),
        "cluster_count": len(cluster_rows),
        "clusters": cluster_rows[:25],
        "graph_summary": network["summary"],
    }


def get_federal_influence_scores(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    min_edge_amount: float = 500.0,
    limit: int = 1000,
) -> dict:
    """Compute influence scores for donors and candidates on federal network."""
    network = get_federal_network_graph(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
        min_edge_amount=min_edge_amount,
        limit=limit,
    )
    nodes = {node["id"]: dict(node) for node in network["nodes"]}
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in network["edges"]:
        weight = float(edge.get("weight") or 0.0)
        adjacency[edge["source"]].append((edge["target"], weight))
        adjacency[edge["target"]].append((edge["source"], weight))

    pagerank = _weighted_pagerank(adjacency)

    donor_rows = []
    candidate_rows = []
    donor_max_weight = max(
        [float(node.get("weighted_degree") or 0.0) for node in nodes.values() if node.get("node_type") == "donor"] or [1.0]
    )
    donor_max_degree = max(
        [int(node.get("degree") or 0) for node in nodes.values() if node.get("node_type") == "donor"] or [1]
    )
    candidate_max_weight = max(
        [float(node.get("weighted_degree") or 0.0) for node in nodes.values() if node.get("node_type") == "candidate"] or [1.0]
    )
    candidate_max_degree = max(
        [int(node.get("degree") or 0) for node in nodes.values() if node.get("node_type") == "candidate"] or [1]
    )

    for node_id, node in nodes.items():
        weight_norm = _safe_div(float(node.get("weighted_degree") or 0.0), donor_max_weight if node.get("node_type") == "donor" else candidate_max_weight)
        degree_norm = _safe_div(float(node.get("degree") or 0), donor_max_degree if node.get("node_type") == "donor" else candidate_max_degree)
        pagerank_norm = float(pagerank.get(node_id, 0.0))
        influence = round((0.55 * weight_norm) + (0.25 * degree_norm) + (0.20 * pagerank_norm), 4)

        row = {
            "node_id": node_id,
            "label": node.get("label"),
            "node_type": node.get("node_type"),
            "weighted_degree": round(float(node.get("weighted_degree") or 0.0), 2),
            "degree": int(node.get("degree") or 0),
            "pagerank_score": round(pagerank_norm, 4),
            "influence_score": influence,
        }
        if node.get("node_type") == "donor":
            row["donor_entity_key"] = node.get("entity_key")
            row["state"] = node.get("state")
            donor_rows.append(row)
        elif node.get("node_type") == "candidate":
            row["candidate_id"] = node.get("candidate_id")
            row["office_display"] = node.get("office_display")
            row["district_display"] = node.get("district_display")
            candidate_rows.append(row)

    donor_rows.sort(key=lambda row: row["influence_score"], reverse=True)
    candidate_rows.sort(key=lambda row: row["influence_score"], reverse=True)

    return {
        "cycle": cycle,
        "office_filter": network["summary"].get("office_filter"),
        "district_filter": network["summary"].get("district_filter"),
        "donors": donor_rows[:50],
        "candidates": candidate_rows[:50],
    }


def get_top_donor_entities(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return top donor entities by total contributions for use in dropdowns."""
    if not _table_exists(conn, "fec_schedule_a_contributions"):
        return []
    cycle_filter = ""
    params: list[Any] = []
    if cycle is not None:
        cycle_filter = "AND sa.cycle = ?"
        params.append(int(cycle))
    params.append(min(max(int(limit), 50), 1000))
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) AS entity_key,
            MAX(COALESCE(sa.contributor_name, '')) AS donor_name,
            MAX(COALESCE(sa.contributor_state, '')) AS donor_state,
            SUM(COALESCE(sa.contribution_receipt_amount, 0)) AS total_amount,
            COUNT(*) AS contribution_count
        FROM fec_schedule_a_contributions sa
        WHERE sa.candidate_id IS NOT NULL
          AND COALESCE(NULLIF(sa.donor_entity_key, ''), NULLIF(sa.donor_key, ''), sa.sub_id) IS NOT NULL
          {cycle_filter}
        GROUP BY entity_key
        HAVING total_amount > 0
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "entity_key": row["entity_key"],
            "donor_name": row["donor_name"] or "Unknown",
            "donor_state": row["donor_state"] or "",
            "total_amount": round(float(row["total_amount"] or 0), 2),
            "contribution_count": int(row["contribution_count"] or 0),
        }
        for row in rows
    ]


def get_federal_follow_the_money(
    conn: sqlite3.Connection,
    donor_entity_key: str,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    max_hops: int = 3,
    min_edge_amount: float = 0.0,
) -> dict:
    """Trace a donor through donor->committee->candidate multi-hop network."""
    key = _clean_text(donor_entity_key)
    if not key:
        return {
            "requested_donor_entity_key": donor_entity_key,
            "start_node_found": False,
            "nodes": [],
            "edges": [],
            "summary": {"node_count": 0, "edge_count": 0, "max_hops": int(max_hops)},
            "reachable_candidates": [],
            "reachable_committees": [],
            "connected_donors": [],
        }

    office_filter = _canonical_office_code(office_code)
    district_filter = _normalize_district_filter(office_filter, district_code) if office_filter else ""
    candidate_meta = _candidate_filter_metadata(conn, cycle=cycle)
    donor_committee_edges = _federal_donor_committee_edges(conn, cycle=cycle)
    committee_candidate_edges = _federal_committee_candidate_edges(conn, cycle=cycle)

    graph_adj: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    nodes: dict[str, dict[str, Any]] = {}

    def add_edge(source: str, target: str, payload: dict) -> None:
        graph_adj[source].append((target, payload))
        graph_adj[target].append((source, payload))

    min_amount = float(max(0.0, min_edge_amount))

    for row in donor_committee_edges:
        amount = float(row.get("total_amount") or 0.0)
        if amount < min_amount:
            continue
        donor_key = _clean_text(row.get("donor_entity_key"))
        committee_id = _clean_text(row.get("committee_id"))
        if not donor_key or not committee_id:
            continue

        donor_node = f"donor:{donor_key}"
        committee_node = f"committee:{committee_id}"
        nodes.setdefault(
            donor_node,
            {
                "id": donor_node,
                "node_type": "donor",
                "label": _clean_text(row.get("donor_name")) or "Unknown Donor",
                "entity_key": donor_key,
            },
        )
        nodes.setdefault(
            committee_node,
            {
                "id": committee_node,
                "node_type": "committee",
                "label": _clean_text(row.get("committee_name")) or committee_id,
                "committee_id": committee_id,
            },
        )
        add_edge(
            donor_node,
            committee_node,
            {
                "edge_type": "donor_committee",
                "weight": round(amount, 2),
                "contribution_count": int(row.get("contribution_count") or 0),
                "earliest_contribution_date": row.get("earliest_contribution_date"),
                "latest_contribution_date": row.get("latest_contribution_date"),
            },
        )

    for row in committee_candidate_edges:
        amount = float(row.get("total_amount") or 0.0)
        if amount < min_amount:
            continue
        committee_id = _clean_text(row.get("committee_id"))
        candidate_id = _clean_text(row.get("candidate_id"))
        if not committee_id or not candidate_id:
            continue

        meta = candidate_meta.get(candidate_id)
        if office_filter or district_filter:
            if not _candidate_passes_filter(meta, office_filter, district_filter):
                continue

        committee_node = f"committee:{committee_id}"
        candidate_node = f"candidate:{candidate_id}"
        nodes.setdefault(
            committee_node,
            {
                "id": committee_node,
                "node_type": "committee",
                "label": _clean_text(row.get("committee_name")) or committee_id,
                "committee_id": committee_id,
            },
        )
        if meta:
            candidate_label = meta["candidate_name"]
            office_display = meta["office_display"]
            district_display = meta["district_display"]
            race_label = meta["race_label"]
            party_code = meta["party_code"]
            party_display = meta["party_display"]
        else:
            candidate_label = candidate_id
            office_display = "Unknown Office"
            district_display = "-"
            race_label = f"{office_display} - {district_display}"
            party_code = ""
            party_display = "Unknown Party"
        nodes.setdefault(
            candidate_node,
            {
                "id": candidate_node,
                "node_type": "candidate",
                "label": candidate_label,
                "candidate_id": candidate_id,
                "office_display": office_display,
                "district_display": district_display,
                "race_label": race_label,
                "party_code": party_code or None,
                "party_display": party_display,
            },
        )
        add_edge(
            committee_node,
            candidate_node,
            {
                "edge_type": "committee_candidate",
                "weight": round(amount, 2),
                "contribution_count": int(row.get("contribution_count") or 0),
                "earliest_contribution_date": row.get("earliest_contribution_date"),
                "latest_contribution_date": row.get("latest_contribution_date"),
            },
        )

    start_node = f"donor:{key}"
    if start_node not in nodes:
        return {
            "requested_donor_entity_key": key,
            "start_node_found": False,
            "nodes": [],
            "edges": [],
            "summary": {"node_count": 0, "edge_count": 0, "max_hops": int(max_hops)},
            "reachable_candidates": [],
            "reachable_committees": [],
            "connected_donors": [],
        }

    max_hops = max(1, min(8, int(max_hops)))
    queue: list[tuple[str, int]] = [(start_node, 0)]
    visited_hops = {start_node: 0}
    seen_edges: set[tuple[str, str, str]] = set()
    output_edges: list[dict] = []
    while queue:
        node_id, hop = queue.pop(0)
        if hop >= max_hops:
            continue
        for neighbor, edge_payload in graph_adj.get(node_id, []):
            next_hop = hop + 1
            if neighbor not in visited_hops or next_hop < visited_hops[neighbor]:
                visited_hops[neighbor] = next_hop
                queue.append((neighbor, next_hop))

            edge_type = edge_payload.get("edge_type", "edge")
            edge_key = tuple(sorted([node_id, neighbor]) + [edge_type])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            output_edges.append(
                {
                    "source": node_id,
                    "target": neighbor,
                    "edge_type": edge_type,
                    "weight": float(edge_payload.get("weight") or 0.0),
                    "contribution_count": int(edge_payload.get("contribution_count") or 0),
                    "earliest_contribution_date": edge_payload.get("earliest_contribution_date"),
                    "latest_contribution_date": edge_payload.get("latest_contribution_date"),
                }
            )

    output_nodes = []
    for node_id, hop in visited_hops.items():
        node = dict(nodes[node_id])
        node["hop_distance"] = int(hop)
        output_nodes.append(node)

    reachable_candidates = sorted(
        [
            {
                "candidate_id": node.get("candidate_id"),
                "candidate_name": node.get("label"),
                "office_display": node.get("office_display"),
                "district_display": node.get("district_display"),
                "race_label": node.get("race_label"),
                "party_code": node.get("party_code"),
                "party_display": node.get("party_display"),
                "hop_distance": int(node.get("hop_distance") or 0),
            }
            for node in output_nodes
            if node.get("node_type") == "candidate"
        ],
        key=lambda row: (row["hop_distance"], row["candidate_name"] or ""),
    )
    reachable_committees = sorted(
        [
            {
                "committee_id": node.get("committee_id"),
                "committee_name": node.get("label"),
                "hop_distance": int(node.get("hop_distance") or 0),
            }
            for node in output_nodes
            if node.get("node_type") == "committee"
        ],
        key=lambda row: (row["hop_distance"], row["committee_name"] or ""),
    )
    connected_donors = sorted(
        [
            {
                "donor_entity_key": node.get("entity_key"),
                "donor_name": node.get("label"),
                "hop_distance": int(node.get("hop_distance") or 0),
            }
            for node in output_nodes
            if node.get("node_type") == "donor" and node.get("entity_key") != key
        ],
        key=lambda row: (row["hop_distance"], row["donor_name"] or ""),
    )

    return {
        "requested_donor_entity_key": key,
        "start_node_found": True,
        "nodes": output_nodes,
        "edges": output_edges,
        "summary": {
            "node_count": len(output_nodes),
            "edge_count": len(output_edges),
            "max_hops": max_hops,
            "candidate_count": len(reachable_candidates),
            "committee_count": len(reachable_committees),
            "connected_donor_count": len(connected_donors),
        },
        "reachable_candidates": reachable_candidates[:200],
        "reachable_committees": reachable_committees[:200],
        "connected_donors": connected_donors[:200],
    }


def get_federal_geographic_concentration(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    limit_states: int = 20,
    limit_cities: int = 30,
    limit_races: int = 25,
) -> dict:
    """Compute federal donor geographic concentration at state/city and race level."""
    rows, _candidate_meta, office_filter, district_filter = _filtered_edge_rows(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
    )

    states: dict[str, dict[str, Any]] = {}
    cities: dict[tuple[str, str], dict[str, Any]] = {}
    race_states: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    race_totals: dict[str, float] = defaultdict(float)

    for row in rows:
        state = _clean_text(row.get("donor_state")).upper()
        city = _clean_text(row.get("donor_city"))
        amount = float(row.get("total_amount") or 0.0)
        contribution_count = int(row.get("contribution_count") or 0)
        donor_key = _clean_text(row.get("donor_entity_key"))
        race_key = f"{row.get('office_display')} - {row.get('district_display')}"

        if state:
            bucket = states.setdefault(
                state,
                {"state": state, "total_amount": 0.0, "contribution_count": 0, "donor_keys": set()},
            )
            bucket["total_amount"] += amount
            bucket["contribution_count"] += contribution_count
            if donor_key:
                bucket["donor_keys"].add(donor_key)

            race_states[race_key][state] += amount
            race_totals[race_key] += amount

        if city and state:
            city_key = (city.title(), state)
            bucket = cities.setdefault(
                city_key,
                {
                    "city": city.title(),
                    "state": state,
                    "total_amount": 0.0,
                    "contribution_count": 0,
                    "donor_keys": set(),
                },
            )
            bucket["total_amount"] += amount
            bucket["contribution_count"] += contribution_count
            if donor_key:
                bucket["donor_keys"].add(donor_key)

    state_rows = [
        {
            "state": state,
            "total_amount": round(bucket["total_amount"], 2),
            "contribution_count": int(bucket["contribution_count"] or 0),
            "donor_count": len(bucket["donor_keys"]),
        }
        for state, bucket in states.items()
    ]
    state_rows.sort(key=lambda row: row["total_amount"], reverse=True)

    city_rows = [
        {
            "city": bucket["city"],
            "state": bucket["state"],
            "total_amount": round(bucket["total_amount"], 2),
            "contribution_count": int(bucket["contribution_count"] or 0),
            "donor_count": len(bucket["donor_keys"]),
        }
        for bucket in cities.values()
    ]
    city_rows.sort(key=lambda row: row["total_amount"], reverse=True)

    race_rows = []
    for race, state_map in race_states.items():
        total_amount = float(race_totals.get(race, 0.0))
        if total_amount <= 0:
            continue
        shares = [amount / total_amount for amount in state_map.values() if amount > 0]
        hhi = sum(share**2 for share in shares) * 10000
        top_state = max(state_map.items(), key=lambda item: item[1])
        race_rows.append(
            {
                "race_label": race,
                "total_amount": round(total_amount, 2),
                "state_count": len(state_map),
                "top_state": top_state[0],
                "top_state_share": round(_safe_div(top_state[1], total_amount), 4),
                "hhi": round(hhi, 2),
            }
        )
    race_rows.sort(key=lambda row: row["hhi"], reverse=True)

    return {
        "cycle": cycle,
        "office_filter": office_filter or None,
        "district_filter": district_filter or None,
        "states": state_rows[: max(1, int(limit_states))],
        "cities": city_rows[: max(1, int(limit_cities))],
        "race_concentration": race_rows[: max(1, int(limit_races))],
    }


def get_federal_local_donor_matches(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    federal_donor_limit: int = 5000,
    local_donor_limit: int = 100000,
    match_limit: int = 5000,
) -> dict:
    """Match federal donors to local donors using deterministic name/address signatures."""
    rows, _candidate_meta, office_filter, district_filter = _filtered_edge_rows(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
    )

    federal_donors: dict[str, dict[str, Any]] = {}
    for row in rows:
        donor_key = _clean_text(row.get("donor_entity_key"))
        if not donor_key:
            continue
        donor = federal_donors.setdefault(
            donor_key,
            {
                "federal_donor_entity_key": donor_key,
                "federal_donor_name": _clean_text(row.get("donor_name")) or "Unknown Donor",
                "federal_donor_state": _clean_text(row.get("donor_state")).upper() or "",
                "federal_donor_zip": _normalize_zip5(row.get("donor_zip")),
                "federal_total_amount": 0.0,
                "federal_contribution_count": 0,
                "federal_candidate_ids": set(),
            },
        )
        donor["federal_total_amount"] += float(row.get("total_amount") or 0.0)
        donor["federal_contribution_count"] += int(row.get("contribution_count") or 0)
        candidate_id = _clean_text(row.get("candidate_id"))
        if candidate_id:
            donor["federal_candidate_ids"].add(candidate_id)

    federal_rows = list(federal_donors.values())
    federal_rows.sort(key=lambda row: row["federal_total_amount"], reverse=True)
    federal_rows = federal_rows[: max(100, min(50000, int(federal_donor_limit)))]

    local_rows = conn.execute(
        """
        SELECT
            donor_key,
            donor_name,
            donor_address,
            donor_city,
            donor_state,
            total_amount,
            contribution_count,
            committee_count
        FROM analytics_donor_summary
        WHERE source = 'bulk_receipts'
        ORDER BY total_amount DESC
        LIMIT ?
        """,
        (max(1000, min(500000, int(local_donor_limit))),),
    ).fetchall()

    local_by_name_state_zip: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    local_by_name_state: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    local_by_name_zip: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    local_processed: list[dict[str, Any]] = []
    for row in local_rows:
        local_name = _normalize_name(row["donor_name"])
        if not local_name:
            continue
        local_state = _clean_text(row["donor_state"]).upper()
        local_zip = _extract_zip5_from_address(row["donor_address"])
        processed = {
            "local_donor_key": row["donor_key"],
            "local_donor_name": row["donor_name"],
            "local_donor_state": local_state,
            "local_donor_zip": local_zip,
            "local_donor_city": row["donor_city"],
            "local_total_amount": float(row["total_amount"] or 0.0),
            "local_contribution_count": int(row["contribution_count"] or 0),
            "local_committee_count": int(row["committee_count"] or 0),
        }
        local_processed.append(processed)
        if local_state and local_zip:
            local_by_name_state_zip[(local_name, local_state, local_zip)].append(processed)
        if local_state:
            local_by_name_state[(local_name, local_state)].append(processed)
        if local_zip:
            local_by_name_zip[(local_name, local_zip)].append(processed)

    raw_matches: list[dict[str, Any]] = []
    for federal in federal_rows:
        fed_name = _normalize_name(federal["federal_donor_name"])
        fed_state = _clean_text(federal["federal_donor_state"]).upper()
        fed_zip = _normalize_zip5(federal["federal_donor_zip"])
        candidates: list[tuple[str, float, list[dict[str, Any]]]] = []

        if fed_name and fed_state and fed_zip:
            rows_match = local_by_name_state_zip.get((fed_name, fed_state, fed_zip), [])
            if rows_match:
                candidates.append(("name_state_zip", 0.95, rows_match))
        if not candidates and fed_name and fed_state:
            rows_match = local_by_name_state.get((fed_name, fed_state), [])
            if rows_match:
                candidates.append(("name_state", 0.80, rows_match))
        if not candidates and fed_name and fed_zip:
            rows_match = local_by_name_zip.get((fed_name, fed_zip), [])
            if rows_match:
                candidates.append(("name_zip", 0.70, rows_match))

        if not candidates:
            continue

        method, score, matched_rows = candidates[0]
        matched_rows = sorted(matched_rows, key=lambda row: row["local_total_amount"], reverse=True)
        for local in matched_rows[:3]:
            raw_matches.append(
                {
                    "federal_donor_entity_key": federal["federal_donor_entity_key"],
                    "federal_donor_name": federal["federal_donor_name"],
                    "federal_donor_state": federal["federal_donor_state"],
                    "federal_donor_zip": federal["federal_donor_zip"],
                    "federal_total_amount": round(float(federal["federal_total_amount"] or 0.0), 2),
                    "federal_contribution_count": int(federal["federal_contribution_count"] or 0),
                    "federal_candidate_count": len(federal["federal_candidate_ids"]),
                    "local_donor_key": local["local_donor_key"],
                    "local_donor_name": local["local_donor_name"],
                    "local_donor_state": local["local_donor_state"],
                    "local_donor_zip": local["local_donor_zip"],
                    "local_total_amount": round(float(local["local_total_amount"] or 0.0), 2),
                    "local_contribution_count": int(local["local_contribution_count"] or 0),
                    "local_committee_count": int(local["local_committee_count"] or 0),
                    "match_method": method,
                    "confidence_score": score,
                }
            )

    # Merge obvious local-identity variants so the matching table is readable.
    # Identity key is built from normalized local name/state/zip and match method.
    merged_matches: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in raw_matches:
        merge_key = (
            _clean_text(row.get("federal_donor_entity_key")),
            _normalize_name(row.get("local_donor_name")),
            _clean_text(row.get("local_donor_state")).upper(),
            _normalize_zip5(row.get("local_donor_zip")),
            _clean_text(row.get("match_method")).lower(),
        )
        local_key = _clean_text(row.get("local_donor_key"))
        if merge_key not in merged_matches:
            merged = dict(row)
            merged["local_donor_keys"] = [local_key] if local_key else []
            merged["matched_local_variants"] = 1
            merged["_best_local_amount"] = float(row.get("local_total_amount") or 0.0)
            merged_matches[merge_key] = merged
            continue

        merged = merged_matches[merge_key]
        merged["local_total_amount"] = round(
            float(merged.get("local_total_amount") or 0.0) + float(row.get("local_total_amount") or 0.0),
            2,
        )
        merged["local_contribution_count"] = int(merged.get("local_contribution_count") or 0) + int(
            row.get("local_contribution_count") or 0
        )
        merged["local_committee_count"] = int(merged.get("local_committee_count") or 0) + int(
            row.get("local_committee_count") or 0
        )
        merged["confidence_score"] = max(
            float(merged.get("confidence_score") or 0.0),
            float(row.get("confidence_score") or 0.0),
        )
        merged["matched_local_variants"] = int(merged.get("matched_local_variants") or 0) + 1
        if local_key and local_key not in merged["local_donor_keys"]:
            merged["local_donor_keys"].append(local_key)

        current_amount = float(row.get("local_total_amount") or 0.0)
        if current_amount > float(merged.get("_best_local_amount") or 0.0):
            merged["_best_local_amount"] = current_amount
            merged["local_donor_key"] = row.get("local_donor_key")
            merged["local_donor_name"] = row.get("local_donor_name")
            merged["local_donor_state"] = row.get("local_donor_state")
            merged["local_donor_zip"] = row.get("local_donor_zip")
            merged["local_donor_city"] = row.get("local_donor_city")

    matches = list(merged_matches.values())
    for row in matches:
        row.pop("_best_local_amount", None)
        row["local_donor_keys"] = sorted([key for key in row.get("local_donor_keys", []) if key])

    matches.sort(key=lambda row: (row["confidence_score"], row["federal_total_amount"], row["local_total_amount"]), reverse=True)
    matches = matches[: max(100, min(20000, int(match_limit)))]

    tier_counts: dict[str, int] = defaultdict(int)
    for row in matches:
        tier_counts[row["match_method"]] += 1

    federal_donor_matched = len({row["federal_donor_entity_key"] for row in matches})
    local_donor_key_set: set[str] = set()
    for row in matches:
        keys = row.get("local_donor_keys") or []
        if keys:
            local_donor_key_set.update(_clean_text(key) for key in keys if _clean_text(key))
        elif _clean_text(row.get("local_donor_key")):
            local_donor_key_set.add(_clean_text(row.get("local_donor_key")))
    local_donor_matched = len(local_donor_key_set)

    return {
        "cycle": cycle,
        "office_filter": office_filter or None,
        "district_filter": district_filter or None,
        "federal_donors_considered": len(federal_rows),
        "local_donors_considered": len(local_processed),
        "federal_donors_matched": federal_donor_matched,
        "local_donors_matched": local_donor_matched,
        "match_rate": round(_safe_div(federal_donor_matched, len(federal_rows)), 4) if federal_rows else 0.0,
        "tier_counts": dict(sorted(tier_counts.items(), key=lambda item: item[0])),
        "raw_match_row_count": len(raw_matches),
        "merged_match_row_count": len(matches),
        "matches": matches,
    }


def refresh_fec_local_donor_matches(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    federal_donor_limit: int = 5000,
    local_donor_limit: int = 100000,
    match_limit: int = 5000,
) -> dict:
    """Persist federal/local donor matches for dashboard metrics and quick lookups."""
    _ensure_fec_local_donor_matches_table(conn)

    if not _table_exists(conn, "fec_schedule_a_contributions"):
        conn.execute("DELETE FROM fec_local_donor_matches")
        conn.commit()
        return {
            "rows_written": 0,
            "materialized_pairs": 0,
            "materialized_matches": 0,
            "skipped_reason": "missing_fec_schedule_a_contributions",
        }

    if not _table_exists(conn, "analytics_donor_summary"):
        conn.execute("DELETE FROM fec_local_donor_matches")
        conn.commit()
        return {
            "rows_written": 0,
            "materialized_pairs": 0,
            "materialized_matches": 0,
            "skipped_reason": "missing_analytics_donor_summary",
        }

    payload = get_federal_local_donor_matches(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
        federal_donor_limit=federal_donor_limit,
        local_donor_limit=local_donor_limit,
        match_limit=match_limit,
    )

    rows_to_write: list[tuple[Any, ...]] = []
    for row in payload.get("matches", []):
        local_keys = row.get("local_donor_keys") or []
        cleaned_keys = sorted(
            {
                _clean_text(key)
                for key in local_keys
                if _clean_text(key)
            }
        )
        fallback_key = _clean_text(row.get("local_donor_key"))
        if fallback_key and fallback_key not in cleaned_keys:
            cleaned_keys.append(fallback_key)
        if not cleaned_keys:
            continue

        local_keys_json = json.dumps(cleaned_keys, ensure_ascii=True)
        primary_local_key = fallback_key or cleaned_keys[0]
        for local_key in cleaned_keys:
            rows_to_write.append(
                (
                    _clean_text(row.get("federal_donor_entity_key")),
                    local_key,
                    primary_local_key,
                    _clean_text(row.get("federal_donor_name")) or None,
                    _clean_text(row.get("local_donor_name")) or None,
                    _clean_text(row.get("federal_donor_state")).upper() or None,
                    _clean_text(row.get("local_donor_state")).upper() or None,
                    _normalize_zip5(row.get("federal_donor_zip")) or None,
                    _normalize_zip5(row.get("local_donor_zip")) or None,
                    float(row.get("federal_total_amount") or 0.0),
                    float(row.get("local_total_amount") or 0.0),
                    int(row.get("federal_contribution_count") or 0),
                    int(row.get("local_contribution_count") or 0),
                    int(row.get("local_committee_count") or 0),
                    _clean_text(row.get("match_method")).lower() or "unknown",
                    float(row.get("confidence_score") or 0.0),
                    local_keys_json,
                )
            )

    conn.execute("DELETE FROM fec_local_donor_matches")
    if rows_to_write:
        conn.executemany(
            """
            INSERT INTO fec_local_donor_matches (
                federal_donor_entity_key,
                local_donor_key,
                primary_local_donor_key,
                federal_donor_name,
                local_donor_name,
                federal_donor_state,
                local_donor_state,
                federal_donor_zip,
                local_donor_zip,
                federal_total_amount,
                local_total_amount,
                federal_contribution_count,
                local_contribution_count,
                local_committee_count,
                match_method,
                confidence_score,
                local_donor_keys_json,
                refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            rows_to_write,
        )
    conn.commit()

    return {
        "rows_written": len(rows_to_write),
        "materialized_pairs": len(rows_to_write),
        "materialized_matches": len(payload.get("matches", [])),
        "cycle": payload.get("cycle"),
        "office_filter": payload.get("office_filter"),
        "district_filter": payload.get("district_filter"),
        "federal_donors_considered": payload.get("federal_donors_considered", 0),
        "local_donors_considered": payload.get("local_donors_considered", 0),
        "federal_donors_matched": payload.get("federal_donors_matched", 0),
        "local_donors_matched": payload.get("local_donors_matched", 0),
        "match_rate": payload.get("match_rate", 0.0),
        "tier_counts": payload.get("tier_counts", {}),
        "raw_match_row_count": payload.get("raw_match_row_count", 0),
        "merged_match_row_count": payload.get("merged_match_row_count", 0),
    }


def get_federal_local_overlap_network(
    conn: sqlite3.Connection,
    cycle: int | None = None,
    office_code: str | None = None,
    district_code: str | None = None,
    min_edge_amount: float = 500.0,
    edge_limit: int = 600,
    federal_donor_limit: int = 5000,
    local_donor_limit: int = 100000,
) -> dict:
    """Build overlap network for donors matched across federal and local datasets."""
    matches_payload = get_federal_local_donor_matches(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
        federal_donor_limit=federal_donor_limit,
        local_donor_limit=local_donor_limit,
        match_limit=20000,
    )
    matches = matches_payload["matches"]
    if not matches:
        return {
            "nodes": [],
            "edges": [],
            "centrality": [],
            "summary": {
                "node_count": 0,
                "edge_count": 0,
                "matched_donors": 0,
                "federal_candidate_count": 0,
                "local_committee_count": 0,
                "min_edge_amount": float(max(0.0, min_edge_amount)),
            },
            "match_summary": matches_payload,
        }

    match_by_federal: dict[str, dict[str, Any]] = {}
    local_keys: set[str] = set()
    for row in matches:
        fed_key = _clean_text(row.get("federal_donor_entity_key"))
        if not fed_key:
            continue
        row_local_keys = row.get("local_donor_keys") or []
        cleaned_local_keys = [_clean_text(key) for key in row_local_keys if _clean_text(key)]
        if not cleaned_local_keys:
            fallback_local_key = _clean_text(row.get("local_donor_key"))
            if fallback_local_key:
                cleaned_local_keys = [fallback_local_key]

        if fed_key not in match_by_federal:
            merged = dict(row)
            merged["_all_local_keys"] = set(cleaned_local_keys)
            match_by_federal[fed_key] = merged
        else:
            existing = match_by_federal[fed_key]
            existing["_all_local_keys"].update(cleaned_local_keys)
            existing["local_total_amount"] = round(
                float(existing.get("local_total_amount") or 0.0) + float(row.get("local_total_amount") or 0.0),
                2,
            )
            existing["local_contribution_count"] = int(existing.get("local_contribution_count") or 0) + int(
                row.get("local_contribution_count") or 0
            )
            existing["local_committee_count"] = int(existing.get("local_committee_count") or 0) + int(
                row.get("local_committee_count") or 0
            )
            existing["matched_local_variants"] = int(existing.get("matched_local_variants") or 1) + int(
                row.get("matched_local_variants") or 1
            )
            existing["confidence_score"] = max(
                float(existing.get("confidence_score") or 0.0),
                float(row.get("confidence_score") or 0.0),
            )

        local_keys.update(cleaned_local_keys)

    rows, candidate_meta, office_filter, district_filter = _filtered_edge_rows(
        conn,
        cycle=cycle,
        office_code=office_code,
        district_code=district_code,
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict] = []
    weighted_degree: dict[str, float] = defaultdict(float)
    edge_degree: dict[str, int] = defaultdict(int)
    min_amount = float(max(0.0, min_edge_amount))

    for row in rows:
        fed_key = _clean_text(row.get("donor_entity_key"))
        if fed_key not in match_by_federal:
            continue
        amount = float(row.get("total_amount") or 0.0)
        if amount < min_amount:
            continue
        candidate_id = _clean_text(row.get("candidate_id"))
        if not candidate_id:
            continue
        meta = candidate_meta.get(candidate_id)
        if office_filter or district_filter:
            if not _candidate_passes_filter(meta, office_filter, district_filter):
                continue

        donor_node = f"match_donor:{fed_key}"
        candidate_node = f"federal_candidate:{candidate_id}"
        match = match_by_federal[fed_key]
        nodes.setdefault(
            donor_node,
            {
                "id": donor_node,
                "node_type": "matched_donor",
                "label": match["federal_donor_name"],
                "federal_donor_entity_key": fed_key,
                "local_donor_key": match["local_donor_key"],
                "local_donor_keys": sorted(match.get("_all_local_keys", [])),
                "matched_local_variants": int(match.get("matched_local_variants") or 1),
                "match_method": match["match_method"],
                "confidence_score": match["confidence_score"],
            },
        )
        if meta:
            candidate_label = meta["candidate_name"]
            office_display = meta["office_display"]
            district_display = meta["district_display"]
            race_label = meta["race_label"]
            party_code = meta["party_code"]
            party_display = meta["party_display"]
        else:
            candidate_label = candidate_id
            office_display = "Unknown Office"
            district_display = "-"
            race_label = f"{office_display} - {district_display}"
            party_code = ""
            party_display = "Unknown Party"
        nodes.setdefault(
            candidate_node,
            {
                "id": candidate_node,
                "node_type": "federal_candidate",
                "label": candidate_label,
                "candidate_id": candidate_id,
                "office_display": office_display,
                "district_display": district_display,
                "race_label": race_label,
                "party_code": party_code or None,
                "party_display": party_display,
            },
        )
        edge = {
            "source": donor_node,
            "target": candidate_node,
            "edge_type": "donor_federal_candidate",
            "weight": round(amount, 2),
            "contribution_count": int(row.get("contribution_count") or 0),
            "race_label": race_label,
            "party_code": party_code or None,
            "party_display": party_display,
        }
        edges.append(edge)
        weighted_degree[donor_node] += amount
        weighted_degree[candidate_node] += amount
        edge_degree[donor_node] += 1
        edge_degree[candidate_node] += 1

    local_edges: list[dict] = []
    local_key_list = sorted(local_keys)
    if local_key_list:
        for chunk in _chunked(local_key_list, size=900):
            placeholders = ",".join(["?"] * len(chunk))
            query_rows = conn.execute(
                f"""
                SELECT
                    donor_key,
                    committee_id,
                    committee_name,
                    COALESCE(SUM(total_amount), 0) AS total_amount,
                    COALESCE(SUM(contribution_count), 0) AS contribution_count
                FROM analytics_donor_committee_agg
                WHERE source = 'bulk_receipts'
                  AND donor_key IN ({placeholders})
                GROUP BY donor_key, committee_id, committee_name
                """,
                chunk,
            ).fetchall()
            local_edges.extend([dict(row) for row in query_rows])

    local_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in local_edges:
        local_by_key[_clean_text(row.get("donor_key"))].append(row)

    for fed_key, match in match_by_federal.items():
        donor_node = f"match_donor:{fed_key}"
        committee_rollup: dict[str, dict[str, Any]] = {}
        all_local_keys = sorted(match.get("_all_local_keys", []))
        for local_key in all_local_keys:
            for row in local_by_key.get(local_key, []):
                committee_id = _clean_text(row.get("committee_id"))
                if not committee_id:
                    continue
                committee = committee_rollup.setdefault(
                    committee_id,
                    {
                        "committee_id": committee_id,
                        "committee_name": _clean_text(row.get("committee_name")) or committee_id,
                        "total_amount": 0.0,
                        "contribution_count": 0,
                    },
                )
                committee["total_amount"] += float(row.get("total_amount") or 0.0)
                committee["contribution_count"] += int(row.get("contribution_count") or 0)

        for row in committee_rollup.values():
            amount = float(row.get("total_amount") or 0.0)
            if amount < min_amount:
                continue
            committee_id = _clean_text(row.get("committee_id"))
            if not committee_id:
                continue
            committee_node = f"local_committee:{committee_id}"
            nodes.setdefault(
                committee_node,
                {
                    "id": committee_node,
                    "node_type": "local_committee",
                    "label": _clean_text(row.get("committee_name")) or committee_id,
                    "committee_id": committee_id,
                },
            )
            edge = {
                "source": donor_node,
                "target": committee_node,
                "edge_type": "donor_local_committee",
                "weight": round(amount, 2),
                "contribution_count": int(row.get("contribution_count") or 0),
            }
            edges.append(edge)
            weighted_degree[donor_node] += amount
            weighted_degree[committee_node] += amount
            edge_degree[donor_node] += 1
            edge_degree[committee_node] += 1

    edges.sort(key=lambda edge: edge["weight"], reverse=True)
    edges = edges[: max(100, min(5000, int(edge_limit)))]

    used_nodes = set()
    for edge in edges:
        used_nodes.add(edge["source"])
        used_nodes.add(edge["target"])
    nodes = {node_id: node for node_id, node in nodes.items() if node_id in used_nodes}

    centrality = [
        {
            "node_id": node_id,
            "label": node.get("label"),
            "node_type": node.get("node_type"),
            "weighted_degree": round(float(weighted_degree.get(node_id, 0.0)), 2),
            "degree": int(edge_degree.get(node_id, 0)),
            "candidate_id": node.get("candidate_id"),
            "committee_id": node.get("committee_id"),
            "federal_donor_entity_key": node.get("federal_donor_entity_key"),
            "confidence_score": node.get("confidence_score"),
            "match_method": node.get("match_method"),
            "office_display": node.get("office_display"),
            "district_display": node.get("district_display"),
            "race_label": node.get("race_label"),
            "party_code": node.get("party_code"),
            "party_display": node.get("party_display"),
        }
        for node_id, node in nodes.items()
    ]
    centrality.sort(key=lambda row: (row["weighted_degree"], row["degree"]), reverse=True)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "centrality": centrality,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "matched_donors": sum(1 for node in nodes.values() if node.get("node_type") == "matched_donor"),
            "federal_candidate_count": sum(1 for node in nodes.values() if node.get("node_type") == "federal_candidate"),
            "local_committee_count": sum(1 for node in nodes.values() if node.get("node_type") == "local_committee"),
            "min_edge_amount": min_amount,
        },
        "match_summary": matches_payload,
    }


def get_federal_donor_detail(
    conn: sqlite3.Connection,
    donor_entity_key: str,
    cycle: int | None = None,
    contribution_limit: int = 200,
    contribution_offset: int = 0,
) -> dict | None:
    donor_entity_key = _clean_text(donor_entity_key)
    if not donor_entity_key:
        return None

    _ensure_missing_donor_identities(conn, cycle=cycle)

    params: list[Any] = [donor_entity_key]
    where_cycle = ""
    if cycle is not None:
        where_cycle = " AND sa.cycle = ?"
        params.append(int(cycle))

    summary = conn.execute(
        f"""
        SELECT
            COALESCE(MAX(sa.contributor_name), 'Unknown Donor') AS donor_name,
            COALESCE(MAX(sa.contributor_city), '') AS contributor_city,
            COALESCE(MAX(sa.contributor_state), '') AS contributor_state,
            COALESCE(MAX(sa.contributor_zip), '') AS contributor_zip,
            COALESCE(MAX(sa.contributor_employer), '') AS contributor_employer,
            COALESCE(MAX(sa.contributor_occupation), '') AS contributor_occupation,
            COALESCE(MAX(sa.donor_entity_method), 'unknown') AS donor_entity_method,
            COUNT(*) AS contribution_count,
            COUNT(DISTINCT sa.candidate_id) AS candidate_count,
            COUNT(DISTINCT sa.committee_id) AS committee_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date
        FROM fec_schedule_a_contributions sa
        WHERE sa.donor_entity_key = ?
        {where_cycle}
        """,
        params,
    ).fetchone()

    if not summary or int(summary["contribution_count"] or 0) == 0:
        return None

    by_candidate = conn.execute(
        f"""
        SELECT
            sa.candidate_id,
            COALESCE(
                MAX(cm.fec_name),
                MAX(cm.candidate_name),
                MAX(sa.candidate_name),
                'Unknown Candidate'
            ) AS candidate_name,
            COALESCE(MAX(cm.office), '') AS office,
            COALESCE(MAX(cm.district), '') AS district,
            COALESCE(MAX(cm.party), '') AS party,
            COUNT(*) AS contribution_count,
            COUNT(DISTINCT sa.committee_id) AS committee_count,
            COALESCE(SUM(sa.contribution_receipt_amount), 0) AS total_amount,
            MAX(sa.contribution_receipt_date) AS latest_contribution_date,
            MIN(sa.contribution_receipt_date) AS earliest_contribution_date
        FROM fec_schedule_a_contributions sa
        LEFT JOIN fec_candidate_match cm
          ON cm.fec_candidate_id = sa.candidate_id
         AND cm.cycle = sa.cycle
        WHERE sa.donor_entity_key = ?
        {where_cycle}
        GROUP BY sa.candidate_id
        ORDER BY total_amount DESC, contribution_count DESC, candidate_name ASC
        """,
        params,
    ).fetchall()

    contributions = conn.execute(
        f"""
        SELECT
            sa.sub_id,
            sa.contribution_receipt_date,
            sa.contribution_receipt_amount,
            sa.contributor_name,
            sa.contributor_city,
            sa.contributor_state,
            sa.contributor_zip,
            sa.contributor_employer,
            sa.contributor_occupation,
            sa.committee_id,
            sa.committee_name,
            sa.candidate_id,
            COALESCE(cm.fec_name, cm.candidate_name, sa.candidate_name, 'Unknown Candidate') AS candidate_name,
            COALESCE(cm.office, '') AS office,
            COALESCE(cm.district, '') AS district,
            COALESCE(cm.party, '') AS party,
            sa.receipt_type,
            sa.receipt_type_desc,
            sa.memo_text
        FROM fec_schedule_a_contributions sa
        LEFT JOIN fec_candidate_match cm
          ON cm.fec_candidate_id = sa.candidate_id
         AND cm.cycle = sa.cycle
        WHERE sa.donor_entity_key = ?
        {where_cycle}
        ORDER BY sa.contribution_receipt_date DESC, sa.contribution_receipt_amount DESC, sa.sub_id DESC
        LIMIT ? OFFSET ?
        """,
        params + [max(1, int(contribution_limit)), max(0, int(contribution_offset))],
    ).fetchall()

    contribution_total_row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM fec_schedule_a_contributions sa
        WHERE sa.donor_entity_key = ?
        {where_cycle}
        """,
        params,
    ).fetchone()

    name_variants = conn.execute(
        f"""
        SELECT
            COALESCE(contributor_name, 'Unknown Donor') AS contributor_name,
            COUNT(*) AS contribution_count,
            COALESCE(SUM(contribution_receipt_amount), 0) AS total_amount
        FROM fec_schedule_a_contributions sa
        WHERE sa.donor_entity_key = ?
        {where_cycle}
        GROUP BY contributor_name
        ORDER BY total_amount DESC, contribution_count DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    return {
        "summary": {
            "donor_entity_key": donor_entity_key,
            "donor_name": summary["donor_name"],
            "contributor_city": summary["contributor_city"],
            "contributor_state": summary["contributor_state"],
            "contributor_zip": summary["contributor_zip"],
            "contributor_employer": summary["contributor_employer"],
            "contributor_occupation": summary["contributor_occupation"],
            "donor_entity_method": summary["donor_entity_method"],
            "donor_entity_method_label": donor_identity_method_label(summary["donor_entity_method"]),
            "contribution_count": int(summary["contribution_count"] or 0),
            "candidate_count": int(summary["candidate_count"] or 0),
            "committee_count": int(summary["committee_count"] or 0),
            "total_amount": float(summary["total_amount"] or 0.0),
            "latest_contribution_date": summary["latest_contribution_date"],
            "earliest_contribution_date": summary["earliest_contribution_date"],
        },
        "by_candidate": [
            {
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "contribution_count": int(row["contribution_count"] or 0),
                "committee_count": int(row["committee_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
                "latest_contribution_date": row["latest_contribution_date"],
                "earliest_contribution_date": row["earliest_contribution_date"],
            }
            for row in by_candidate
        ],
        "contributions": [
            {
                "sub_id": row["sub_id"],
                "contribution_receipt_date": row["contribution_receipt_date"],
                "contribution_receipt_amount": float(row["contribution_receipt_amount"] or 0.0),
                "contributor_name": row["contributor_name"],
                "contributor_city": row["contributor_city"],
                "contributor_state": row["contributor_state"],
                "contributor_zip": row["contributor_zip"],
                "contributor_employer": row["contributor_employer"],
                "contributor_occupation": row["contributor_occupation"],
                "committee_id": row["committee_id"],
                "committee_name": row["committee_name"],
                "candidate_id": row["candidate_id"],
                "candidate_name": row["candidate_name"],
                "office": row["office"],
                "district": row["district"],
                "party": row["party"],
                "receipt_type": row["receipt_type"],
                "receipt_type_desc": row["receipt_type_desc"],
                "memo_text": row["memo_text"],
            }
            for row in contributions
        ],
        "name_variants": [
            {
                "contributor_name": row["contributor_name"],
                "contribution_count": int(row["contribution_count"] or 0),
                "total_amount": float(row["total_amount"] or 0.0),
            }
            for row in name_variants
        ],
        "total_contributions": int(contribution_total_row["count"] or 0) if contribution_total_row else 0,
    }
