"""Cross-matching engine for lobbying, IRS 527, and campaign finance data."""
from __future__ import annotations

import math
import logging
import re
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NAME_STOP_WORDS = frozenset({
    "the", "of", "for", "and", "in", "inc", "llc", "ltd", "co", "corp",
    "corporation", "company", "assoc", "association", "assn", "group",
    "committee", "fund", "pac", "political", "action", "a", "an",
})


def _normalize_name_tokens(value: str | None) -> list[str]:
    """Normalize a name into lowercase tokens, removing stop words and punctuation."""
    if not value:
        return []
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", value.lower()).strip()
    return [t for t in text.split() if t and t not in _NAME_STOP_WORDS]


def _jaccard(left: list[str], right: list[str]) -> float:
    """Jaccard similarity between two token lists."""
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


_JOB_OUTPUT_TABLES: dict[str, str] = {
    "lobbying_donors": "lobbying_donor_matches",
    "lobbying_expenditures": "lobbying_expenditure_matches",
    "527_committees": "irs527_committee_matches",
    "527_expenditures": "irs527_expenditure_recipient_matches",
    "527_directors": "irs527_director_donor_matches",
    "527_director_candidates": "irs527_director_candidate_matches",
    "527_director_addresses": "irs527_director_address_matches",
    "527_org_addresses": "irs527_org_address_matches",
    "lobbying_527": "lobbying_527_matches",
}

_JOB_INPUT_TABLES: dict[str, tuple[str, ...]] = {
    "lobbying_donors": ("lobbying_clients", "analytics_donor_summary"),
    "lobbying_expenditures": ("lobbying_clients", "bulk_expenditures_clean"),
    "527_committees": ("irs527_organizations", "bulk_committees_clean"),
    "527_expenditures": ("irs527_expenditures", "bulk_committees_clean", "bulk_candidates_clean"),
    "527_directors": ("irs527_directors", "analytics_donor_summary"),
    "527_director_candidates": ("irs527_directors", "bulk_candidates_clean", "federal_candidates"),
    "527_director_addresses": ("irs527_directors", "analytics_donor_summary"),
    "527_org_addresses": ("irs527_organizations", "bulk_committees_clean", "analytics_donor_summary"),
    "lobbying_527": ("lobbying_clients", "irs527_organizations"),
}


def _chunked(values: list[tuple], size: int = 5000):
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def _ensure_incremental_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cross_matching_job_fingerprints (
            job_name TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _table_fingerprint(conn: sqlite3.Connection, table_name: str) -> str:
    if not _table_exists(conn, table_name):
        return "missing"
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c, COALESCE(MAX(rowid), 0) AS m FROM {table_name}").fetchone()
    except sqlite3.OperationalError:
        row = conn.execute(f"SELECT COUNT(*) AS c, 0 AS m FROM {table_name}").fetchone()
    return f"{int(row['c'])}:{int(row['m'])}"


def _job_fingerprint(conn: sqlite3.Connection, job_name: str) -> str:
    parts = []
    for table_name in _JOB_INPUT_TABLES.get(job_name, ()):
        parts.append(f"{table_name}={_table_fingerprint(conn, table_name)}")
    return "|".join(parts)


def _get_saved_job_fingerprint(conn: sqlite3.Connection, job_name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT fingerprint FROM cross_matching_job_fingerprints WHERE job_name = ?",
        (job_name,),
    ).fetchone()
    return row["fingerprint"] if row else None


def _save_job_fingerprint(conn: sqlite3.Connection, job_name: str, fingerprint: str) -> None:
    conn.execute(
        """
        INSERT INTO cross_matching_job_fingerprints (job_name, fingerprint, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(job_name) DO UPDATE SET
            fingerprint = excluded.fingerprint,
            updated_at = CURRENT_TIMESTAMP
        """,
        (job_name, fingerprint),
    )
    conn.commit()


def _job_matches_count(conn: sqlite3.Connection, job_name: str) -> int:
    table_name = _JOB_OUTPUT_TABLES[job_name]
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
    return int(row["c"])


def _job_is_unchanged(conn: sqlite3.Connection, job_name: str) -> bool:
    current = _job_fingerprint(conn, job_name)
    saved = _get_saved_job_fingerprint(conn, job_name)
    return bool(saved and current == saved)


def _shadow_output_table_for_worker(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(f"DROP TABLE IF EXISTS temp.{table_name}")
    conn.execute(f"CREATE TEMP TABLE {table_name} AS SELECT * FROM main.{table_name} WHERE 0")
    conn.commit()


def _read_temp_output_rows(conn: sqlite3.Connection, table_name: str) -> tuple[list[str], list[tuple]]:
    columns = [row["name"] for row in conn.execute(f"PRAGMA temp.table_info({table_name})").fetchall()]
    rows = [tuple(row) for row in conn.execute(f"SELECT * FROM temp.{table_name}").fetchall()]
    return columns, rows


def _merge_rows_into_output_table(
    conn: sqlite3.Connection,
    table_name: str,
    columns: list[str],
    rows: list[tuple],
) -> None:
    conn.execute(f"DELETE FROM {table_name}")
    if not rows:
        conn.commit()
        return

    column_sql = ", ".join(columns)
    placeholder_sql = ", ".join(["?"] * len(columns))
    insert_sql = f"INSERT OR REPLACE INTO {table_name} ({column_sql}) VALUES ({placeholder_sql})"
    for chunk in _chunked(rows, size=5000):
        conn.executemany(insert_sql, chunk)
        conn.commit()


def _build_donor_address_indexes(
    conn: sqlite3.Connection,
    *,
    include_name_tokens: bool = False,
) -> dict[str, Any]:
    donor_rows = conn.execute(
        """
        SELECT donor_key, donor_name, donor_city, donor_state
        FROM analytics_donor_summary
        WHERE source = 'bulk_receipts'
            AND donor_key IS NOT NULL
            AND donor_name IS NOT NULL
            AND donor_state IS NOT NULL
        """
    ).fetchall()

    zip_index: dict[tuple[str, str], list] = defaultdict(list)
    city_index: dict[tuple[str, str], list] = defaultdict(list)
    for dr in donor_rows:
        norm_state = (dr["donor_state"] or "").strip().upper()
        if not norm_state:
            continue

        donor_zip5 = None
        parts = (dr["donor_key"] or "").split("|")
        zip5 = parts[-1].strip()[:5] if len(parts) >= 7 else ""
        if zip5 and zip5[0].isdigit():
            donor_zip5 = zip5

        payload = {
            "donor_key": dr["donor_key"],
            "donor_name": dr["donor_name"],
            "donor_city": dr["donor_city"],
            "donor_state": dr["donor_state"],
            "donor_zip5": donor_zip5,
        }
        if include_name_tokens:
            payload["_tokens"] = _normalize_name_tokens(dr["donor_name"])

        if donor_zip5:
            zip_index[(norm_state, donor_zip5)].append(payload)

        norm_city = _normalize_city(dr["donor_city"])
        if norm_city:
            city_index[(norm_state, norm_city)].append(payload)

    donor_count = sum(len(v) for v in zip_index.values()) or sum(len(v) for v in city_index.values())
    return {
        "zip_index": zip_index,
        "city_index": city_index,
        "donor_count": donor_count,
    }


def _format_duration(seconds: float) -> str:
    """Format seconds for concise log output."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {sec:04.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes:02d}m {sec:04.1f}s"


def _sparse_jaccard_matches(
    left_items: list[tuple[Any, list[str]]],
    right_items: list[tuple[Any, list[str]]],
    threshold: float,
    *,
    job_label: str = "sparse-jaccard",
    progress_label: str | None = None,
    progress_every: int | None = None,
) -> tuple[list[tuple[Any, Any, float]], dict[str, float | int]]:
    """Match tokenized left/right items using sparse inverted-index candidate generation.

    For threshold > 0, only pairs with at least one shared token are scored.
    For threshold <= 0, exhaustive scoring is used to preserve legacy behavior.
    """
    started = time.perf_counter()

    # Build right-side sparse index.
    token_to_id: dict[str, int] = {}
    right_payloads: list[Any] = []
    right_token_sets: list[frozenset[int]] = []
    right_token_counts: list[int] = []
    postings: dict[int, list[int]] = {}

    for payload, tokens in right_items:
        unique_tokens = set(tokens)
        if not unique_tokens:
            continue

        token_ids: set[int] = set()
        for token in unique_tokens:
            tok_id = token_to_id.get(token)
            if tok_id is None:
                tok_id = len(token_to_id)
                token_to_id[token] = tok_id
            token_ids.add(tok_id)

        right_idx = len(right_payloads)
        right_payloads.append(payload)
        right_token_sets.append(frozenset(token_ids))
        right_token_counts.append(len(token_ids))
        for tok_id in token_ids:
            postings.setdefault(tok_id, []).append(right_idx)

    total_left = len(left_items)
    total_right = len(right_payloads)
    full_pair_count = total_left * total_right
    if total_left == 0 or total_right == 0:
        logger.info(
            "%s pre-run estimate (threshold=%.2f): left_rows=%d, right_rows=%d, full_pairs=%d, "
            "est_scored_pairs=0, est_reduction=0.0x, mode=sparse",
            job_label,
            threshold,
            total_left,
            total_right,
            full_pair_count,
        )
        return [], {
            "left_rows": total_left,
            "right_rows": total_right,
            "full_pairs": full_pair_count,
            "scored_pairs": 0,
            "reduction": 0.0,
            "elapsed_seconds": time.perf_counter() - started,
            "estimated_scored_pairs": 0,
            "estimated_reduction": 0.0,
            "sample_size": 0,
        }

    exhaustive_mode = threshold <= 0.0
    sample_size = min(400, total_left)
    estimated_scored_pairs = full_pair_count if exhaustive_mode else 0
    if not exhaustive_mode and sample_size > 0:
        sampled = 0
        sampled_candidate_total = 0
        step = max(1, total_left // sample_size)
        for idx in range(0, total_left, step):
            if sampled >= sample_size:
                break
            left_tokens = left_items[idx][1]
            unique_left_tokens = set(left_tokens)
            left_token_ids = {token_to_id[token] for token in unique_left_tokens if token in token_to_id}
            if left_token_ids:
                candidate_ids: set[int] = set()
                for tok_id in left_token_ids:
                    candidate_ids.update(postings.get(tok_id, ()))
                sampled_candidate_total += len(candidate_ids)
            sampled += 1
        if sampled > 0:
            estimated_scored_pairs = int((sampled_candidate_total / sampled) * total_left)

    estimated_reduction = (full_pair_count / estimated_scored_pairs) if estimated_scored_pairs else 0.0
    logger.info(
        "%s pre-run estimate (threshold=%.2f): left_rows=%d, right_rows=%d, full_pairs=%d, "
        "est_scored_pairs=%d, est_reduction=%.1fx, mode=%s",
        job_label,
        threshold,
        total_left,
        total_right,
        full_pair_count,
        estimated_scored_pairs,
        estimated_reduction,
        "exhaustive" if exhaustive_mode else "sparse",
    )
    matches: list[tuple[Any, Any, float]] = []
    scored_pairs = 0

    for idx, (left_payload, left_tokens) in enumerate(left_items, start=1):
        unique_left_tokens = set(left_tokens)
        if not unique_left_tokens:
            continue
        left_token_count = len(unique_left_tokens)
        left_token_ids = {token_to_id[token] for token in unique_left_tokens if token in token_to_id}

        if exhaustive_mode:
            scored_pairs += total_right
            for right_idx, right_token_ids in enumerate(right_token_sets):
                intersection = len(left_token_ids & right_token_ids)
                union = left_token_count + right_token_counts[right_idx] - intersection
                if union <= 0:
                    continue
                score = intersection / union
                if score >= threshold:
                    matches.append((left_payload, right_payloads[right_idx], score))
        else:
            if not left_token_ids:
                continue
            candidate_intersections: dict[int, int] = {}
            for tok_id in left_token_ids:
                for right_idx in postings.get(tok_id, ()):
                    candidate_intersections[right_idx] = candidate_intersections.get(right_idx, 0) + 1

            scored_pairs += len(candidate_intersections)
            for right_idx, intersection in candidate_intersections.items():
                union = left_token_count + right_token_counts[right_idx] - intersection
                if union <= 0:
                    continue
                score = intersection / union
                if score >= threshold:
                    matches.append((left_payload, right_payloads[right_idx], score))

        if progress_label and progress_every and idx % progress_every == 0:
            elapsed = time.perf_counter() - started
            rate = idx / elapsed if elapsed > 0 else 0.0
            remaining = total_left - idx
            eta_seconds = remaining / rate if rate > 0 else math.inf
            eta_text = _format_duration(eta_seconds) if math.isfinite(eta_seconds) else "unknown"
            logger.info(
                "%s progress: %d/%d (%.1f%%), matches=%d, rate=%.1f rows/s, eta=%s",
                progress_label,
                idx,
                total_left,
                (idx / total_left) * 100,
                len(matches),
                rate,
                eta_text,
            )

    reduction = (full_pair_count / scored_pairs) if scored_pairs else 0.0
    return matches, {
        "left_rows": total_left,
        "right_rows": total_right,
        "full_pairs": full_pair_count,
        "scored_pairs": scored_pairs,
        "reduction": reduction,
        "elapsed_seconds": time.perf_counter() - started,
        "estimated_scored_pairs": estimated_scored_pairs,
        "estimated_reduction": estimated_reduction,
        "sample_size": sample_size,
    }


def match_lobbying_to_donors(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match lobbying clients against analytics_donor_summary donors.

    Stores results in lobbying_donor_matches table.
    """
    if not _table_exists(conn, "lobbying_clients") or not _table_exists(conn, "analytics_donor_summary"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM lobbying_donor_matches")
    conn.commit()

    clients = conn.execute("SELECT client_id, client_name FROM lobbying_clients").fetchall()
    donors = conn.execute(
        """
        SELECT donor_key, donor_name
        FROM analytics_donor_summary
        WHERE source = 'bulk_receipts'
        ORDER BY total_amount DESC
        LIMIT 50000
        """
    ).fetchall()

    left_items = []
    for c in clients:
        tokens = _normalize_name_tokens(c["client_name"])
        if tokens:
            left_items.append(((c["client_id"], c["client_name"]), tokens))

    right_items = []
    for d in donors:
        tokens = _normalize_name_tokens(d["donor_name"])
        if tokens:
            right_items.append(((d["donor_key"], d["donor_name"]), tokens))

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="lobbying-donors",
    )
    batch = [
        (client_id, donor_key, client_name, donor_name, score, "jaccard")
        for (client_id, client_name), (donor_key, donor_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT OR REPLACE INTO lobbying_donor_matches
                (client_id, donor_key, client_name, donor_name, score, method)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "Lobbying-to-donor matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def match_lobbying_to_expenditure_payees(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match lobbying clients/entities against expenditure payees.

    Stores results in lobbying_expenditure_matches table.
    """
    if not _table_exists(conn, "lobbying_clients") or not _table_exists(conn, "bulk_expenditures_clean"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM lobbying_expenditure_matches")
    conn.commit()

    # Gather distinct payees
    payees = conn.execute(
        """
        SELECT DISTINCT payee_last_or_business_name AS payee_name, committee_id_sbe
        FROM bulk_expenditures_clean
        WHERE payee_last_or_business_name IS NOT NULL
          AND TRIM(payee_last_or_business_name) != ''
        """
    ).fetchall()

    right_items = []
    for p in payees:
        tokens = _normalize_name_tokens(p["payee_name"])
        if tokens:
            right_items.append(((p["payee_name"], p["committee_id_sbe"]), tokens))

    # Clients
    clients = conn.execute("SELECT client_id, client_name FROM lobbying_clients").fetchall()
    entities = conn.execute("SELECT entity_id, entity_name FROM lobbying_entities").fetchall()

    left_items = []
    for c in clients:
        tokens = _normalize_name_tokens(c["client_name"])
        if tokens:
            left_items.append((("client", c["client_id"], c["client_name"]), tokens))
    for e in entities:
        tokens = _normalize_name_tokens(e["entity_name"])
        if tokens:
            left_items.append((("entity", e["entity_id"], e["entity_name"]), tokens))

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="lobbying-expenditures",
    )
    batch = [
        (source_type, source_id, source_name, payee_name, cmte_id, score)
        for (source_type, source_id, source_name), (payee_name, cmte_id), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT INTO lobbying_expenditure_matches
                (source_type, source_id, source_name, payee_name, committee_id_sbe, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "Lobbying-to-expenditure matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def match_527_to_committees(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match IRS 527 organizations against IL committees.

    Stores results in irs527_committee_matches table.
    """
    if not _table_exists(conn, "irs527_organizations") or not _table_exists(conn, "bulk_committees_clean"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM irs527_committee_matches")
    conn.commit()

    orgs = conn.execute(
        "SELECT DISTINCT ein, org_name FROM irs527_organizations WHERE org_name IS NOT NULL"
    ).fetchall()

    committees = conn.execute(
        "SELECT committee_id_sbe, committee_name FROM bulk_committees_clean WHERE committee_name IS NOT NULL"
    ).fetchall()

    right_items = []
    for c in committees:
        tokens = _normalize_name_tokens(c["committee_name"])
        if tokens:
            right_items.append(((c["committee_id_sbe"], c["committee_name"]), tokens))

    left_items = []
    for o in orgs:
        tokens = _normalize_name_tokens(o["org_name"])
        if tokens:
            left_items.append(((o["ein"], o["org_name"]), tokens))

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="527-committees",
    )
    batch = [
        (ein, org_name, cmte_id, cmte_name, score, "jaccard")
        for (ein, org_name), (cmte_id, cmte_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT OR REPLACE INTO irs527_committee_matches
                (ein, org_name, committee_id_sbe, committee_name, score, method)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "527-to-committee matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def match_527_expenditures_to_committees(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match IRS 527 expenditure recipients (IL) against committees and candidates.

    Stores results in irs527_expenditure_recipient_matches table.
    """
    started = time.perf_counter()
    if not _table_exists(conn, "irs527_expenditures"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM irs527_expenditure_recipient_matches")
    conn.commit()

    logger.info(
        "Starting 527-expenditure matching (threshold=%.2f): loading expenditures + targets",
        threshold,
    )
    load_started = time.perf_counter()
    expenditures = conn.execute(
        """
        SELECT ein, org_name, recipient_name
        FROM irs527_expenditures
        WHERE state = 'IL' AND recipient_name IS NOT NULL
        """
    ).fetchall()

    # Load committees
    cmte_tokens = []
    if _table_exists(conn, "bulk_committees_clean"):
        committees = conn.execute(
            "SELECT committee_id_sbe, committee_name FROM bulk_committees_clean WHERE committee_name IS NOT NULL"
        ).fetchall()
        for c in committees:
            tokens = _normalize_name_tokens(c["committee_name"])
            if tokens:
                cmte_tokens.append(("committee", str(c["committee_id_sbe"]), c["committee_name"], tokens))

    # Load candidates
    cand_tokens = []
    if _table_exists(conn, "bulk_candidates_clean"):
        candidates = conn.execute(
            "SELECT candidate_id, candidate_full_name FROM bulk_candidates_clean WHERE candidate_full_name IS NOT NULL"
        ).fetchall()
        for c in candidates:
            tokens = _normalize_name_tokens(c["candidate_full_name"])
            if tokens:
                cand_tokens.append(("candidate", str(c["candidate_id"]), c["candidate_full_name"], tokens))

    all_targets = cmte_tokens + cand_tokens
    logger.info(
        "Loaded %d IL expenditures and %d match targets in %s",
        len(expenditures),
        len(all_targets),
        _format_duration(time.perf_counter() - load_started),
    )

    if not expenditures or not all_targets:
        logger.info(
            "527-expenditure-to-committee/candidate matches: 0 (elapsed=%s)",
            _format_duration(time.perf_counter() - started),
        )
        return {"matches": 0}

    left_items = []
    for exp in expenditures:
        tokens = _normalize_name_tokens(exp["recipient_name"])
        if tokens:
            left_items.append(((exp["ein"], exp["org_name"], exp["recipient_name"]), tokens))

    right_items = [
        ((matched_type, matched_id, matched_name), target_tokens)
        for matched_type, matched_id, matched_name, target_tokens in all_targets
    ]
    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="527-expenditures",
        progress_label="527-expenditure",
        progress_every=5000,
    )
    batch = [
        (ein, org_name, recipient_name, matched_type, matched_id, matched_name, score)
        for (ein, org_name, recipient_name), (matched_type, matched_id, matched_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT INTO irs527_expenditure_recipient_matches
                (ein, org_name, recipient_name, matched_type, matched_id, matched_name, score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "527-expenditure-to-committee/candidate matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(time.perf_counter() - started),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def _normalize_zip5(value: str | None) -> str | None:
    """Normalize a zip code to 5-digit form."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # Take only digits and first 5
    digits = re.sub(r"[^0-9]", "", text.split("-")[0])
    if not digits:
        return None
    return digits.zfill(5)[:5]


def _normalize_city(value: str | None) -> str | None:
    """Normalize a city name to lowercase, stripped."""
    if not value:
        return None
    text = value.strip().lower()
    return text if text else None


def _address_score(
    city1: str | None, state1: str | None, zip1: str | None,
    city2: str | None, state2: str | None, zip2: str | None,
) -> float:
    """Score address similarity (0.0 to 1.0).

    Scoring:
    - Same state: 0.2
    - Same city+state: 0.5
    - Same zip5+city+state: 1.0
    - Same zip5+state (different city): 0.7
    """
    norm_city1 = _normalize_city(city1)
    norm_city2 = _normalize_city(city2)
    norm_state1 = (state1 or "").strip().upper()
    norm_state2 = (state2 or "").strip().upper()
    norm_zip1 = _normalize_zip5(zip1)
    norm_zip2 = _normalize_zip5(zip2)

    if not norm_state1 or not norm_state2:
        return 0.0

    if norm_state1 != norm_state2:
        return 0.0

    score = 0.2  # Same state

    city_match = norm_city1 and norm_city2 and norm_city1 == norm_city2
    zip_match = norm_zip1 and norm_zip2 and norm_zip1 == norm_zip2

    if zip_match and city_match:
        score = 1.0
    elif zip_match:
        score = 0.7
    elif city_match:
        score = 0.5

    return score


def match_527_directors_to_donors(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match IRS 527 directors against analytics_donor_summary (exhaustive).

    Stores results in irs527_director_donor_matches table.
    No LIMIT on donors — exhaustive matching using sparse inverted-index.
    """
    if not _table_exists(conn, "irs527_directors") or not _table_exists(conn, "analytics_donor_summary"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM irs527_director_donor_matches")
    conn.commit()

    directors = conn.execute(
        """
        SELECT rowid_local, ein, org_name, person_name
        FROM irs527_directors
        WHERE person_name IS NOT NULL
        """
    ).fetchall()

    donors = conn.execute(
        """
        SELECT donor_key, donor_name
        FROM analytics_donor_summary
        WHERE source = 'bulk_receipts'
        """
    ).fetchall()

    right_items = []
    for d in donors:
        tokens = _normalize_name_tokens(d["donor_name"])
        if tokens:
            right_items.append(((d["donor_key"], d["donor_name"]), tokens))

    left_items = []
    for director in directors:
        tokens = _normalize_name_tokens(director["person_name"])
        if tokens:
            left_items.append(((director["ein"], director["org_name"], director["person_name"]), tokens))

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="527-directors",
    )
    batch = [
        (ein, org_name, director_name, donor_key, donor_name, score)
        for (ein, org_name, director_name), (donor_key, donor_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT INTO irs527_director_donor_matches
                (ein, org_name, director_name, donor_key, donor_name, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "527-director-to-donor matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def match_527_directors_to_candidates(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match IRS 527 directors against state and federal candidates.

    Stores results in irs527_director_candidate_matches table.
    """
    if not _table_exists(conn, "irs527_directors"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM irs527_director_candidate_matches")
    conn.commit()

    directors = conn.execute(
        """
        SELECT rowid_local, ein, org_name, person_name
        FROM irs527_directors
        WHERE person_name IS NOT NULL
        """
    ).fetchall()

    if not directors:
        return {"matches": 0}

    left_items = []
    for director in directors:
        tokens = _normalize_name_tokens(director["person_name"])
        if tokens:
            left_items.append(((director["ein"], director["org_name"], director["person_name"]), tokens))

    right_items = []

    # State candidates
    if _table_exists(conn, "bulk_candidates_clean"):
        state_candidates = conn.execute(
            "SELECT candidate_id, candidate_full_name FROM bulk_candidates_clean WHERE candidate_full_name IS NOT NULL"
        ).fetchall()
        for c in state_candidates:
            tokens = _normalize_name_tokens(c["candidate_full_name"])
            if tokens:
                right_items.append((("state", str(c["candidate_id"]), c["candidate_full_name"]), tokens))

    # Federal candidates
    if _table_exists(conn, "fec_candidate_match"):
        federal_candidates = conn.execute(
            "SELECT fec_candidate_id, fec_name FROM fec_candidate_match WHERE match_status = 'matched' AND fec_name IS NOT NULL"
        ).fetchall()
        for c in federal_candidates:
            tokens = _normalize_name_tokens(c["fec_name"])
            if tokens:
                right_items.append((("federal", c["fec_candidate_id"], c["fec_name"]), tokens))

    if not right_items:
        return {"matches": 0}

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="527-director-candidates",
    )
    batch = [
        (ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score)
        for (ein, org_name, director_name), (candidate_source, candidate_id, candidate_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT INTO irs527_director_candidate_matches
                (ein, org_name, director_name, candidate_id, candidate_name, candidate_source, score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "527-director-to-candidate matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def match_527_directors_to_donors_by_address(
    conn: sqlite3.Connection,
    name_threshold: float = 0.30,
    donor_indexes: Optional[dict[str, Any]] = None,
) -> dict:
    """Match 527 directors to donors by zip5+state co-location AND name similarity.

    JOIN on zip5+state keeps candidate pairs manageable (~thousands per zip
    vs millions per city). Then filter by Jaccard name similarity >= name_threshold
    to find genuine person matches that pure name-matching might miss due to
    spelling variants (e.g., "J. Smith" vs "John Smith").

    Stores in irs527_director_address_matches.
    """
    if not _table_exists(conn, "irs527_directors") or not _table_exists(conn, "analytics_donor_summary"):
        return {"matches": 0, "skipped": "missing_tables"}

    started = time.perf_counter()
    conn.execute("DELETE FROM irs527_director_address_matches")
    conn.commit()

    _INSERT_SQL = """
        INSERT INTO irs527_director_address_matches
            (ein, org_name, director_name,
             director_city, director_state, director_zip5,
             donor_key, donor_name,
             donor_city, donor_state, donor_zip5,
             address_score, name_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    if donor_indexes is None:
        logger.info("527-director-address: building donor zip index from analytics_donor_summary")
        donor_indexes = _build_donor_address_indexes(conn, include_name_tokens=True)

    zip_index = donor_indexes["zip_index"]
    city_index = donor_indexes["city_index"]
    donor_count = int(donor_indexes["donor_count"])
    logger.info(
        "527-director-address: %d donors indexed (zip_buckets=%d, city_buckets=%d)",
        donor_count, len(zip_index), len(city_index)
    )
    for bucket in zip_index.values():
        for dr in bucket:
            if "_tokens" not in dr:
                dr["_tokens"] = _normalize_name_tokens(dr["donor_name"])
    for bucket in city_index.values():
        for dr in bucket:
            if "_tokens" not in dr:
                dr["_tokens"] = _normalize_name_tokens(dr["donor_name"])

    # Load directors with zip
    directors = conn.execute("""
        SELECT ein, org_name, person_name,
               city, state, zip,
               UPPER(TRIM(state)) AS norm_state,
               SUBSTR(TRIM(zip), 1, 5) AS norm_zip5
        FROM irs527_directors
        WHERE person_name IS NOT NULL
            AND state IS NOT NULL
            AND zip IS NOT NULL AND TRIM(zip) != ''
    """).fetchall()

    batch = []
    candidates_scored = 0
    matches_found = 0
    seen = set()  # deduplicate (ein, person_name, donor_key)

    def _flush():
        if batch:
            conn.executemany(_INSERT_SQL, batch)
            batch.clear()

    for d in directors:
        d_tokens = _normalize_name_tokens(d["person_name"])
        if not d_tokens:
            continue

        bucket_zip = zip_index.get((d["norm_state"], d["norm_zip5"])) or []
        norm_city = _normalize_city(d["city"])
        bucket_city = city_index.get((d["norm_state"], norm_city), []) if norm_city else []
        if not bucket_zip and not bucket_city:
            continue
        candidate_buckets = [bucket_zip, bucket_city] if bucket_city is not bucket_zip else [bucket_zip]
        seen_donor_keys = set()

        for bucket in candidate_buckets:
            for dr in bucket:
                if dr["donor_key"] in seen_donor_keys:
                    continue
                seen_donor_keys.add(dr["donor_key"])
                candidates_scored += 1
                donor_tokens = dr["_tokens"]
                if not donor_tokens:
                    continue

                name_score = _jaccard(d_tokens, donor_tokens)
                if name_score < name_threshold:
                    continue

                dedup_key = (d["ein"], d["person_name"], dr["donor_key"])
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                address_score = _address_score(
                    d["city"], d["state"], d["zip"],
                    dr["donor_city"], dr["donor_state"], dr["donor_zip5"],
                )

                batch.append((
                    d["ein"], d["org_name"], d["person_name"],
                    _normalize_city(d["city"]),
                    (d["state"] or "").strip().upper(),
                    _normalize_zip5(d["zip"]),
                    dr["donor_key"], dr["donor_name"],
                    _normalize_city(dr["donor_city"]),
                    (dr["donor_state"] or "").strip().upper(),
                    dr["donor_zip5"],
                    address_score, name_score,
                ))
                matches_found += 1
                if len(batch) >= 50000:
                    _flush()

    _flush()
    conn.commit()

    elapsed = time.perf_counter() - started
    logger.info(
        "527-director-to-donor address+name matches: %d "
        "(directors=%d, candidates_scored=%d, elapsed=%s)",
        matches_found, len(directors), candidates_scored,
        _format_duration(elapsed),
    )
    return {"matches": matches_found}


def match_527_org_addresses(
    conn: sqlite3.Connection,
    name_threshold: float = 0.30,
    donor_indexes: Optional[dict[str, Any]] = None,
) -> dict:
    """Match 527 org addresses against committees by zip5+state AND name similarity.

    JOIN on zip5+state keeps candidate pairs small, then filter by Jaccard
    name similarity (org_name vs committee_name) >= name_threshold.
    Checks org address, custodian address, contact address, business address.
    Stores results in irs527_org_address_matches.
    """
    if not _table_exists(conn, "irs527_organizations"):
        return {"matches": 0, "skipped": "missing_tables"}

    can_match_committees = _table_exists(conn, "bulk_committees_clean")
    can_match_donors = _table_exists(conn, "analytics_donor_summary")
    if not can_match_committees and not can_match_donors:
        return {"matches": 0, "skipped": "missing_tables"}

    started = time.perf_counter()
    conn.execute("DELETE FROM irs527_org_address_matches")
    conn.commit()

    # Flatten org addresses into a temp table
    conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
    conn.execute("""
        CREATE TEMP TABLE _tmp_527_org_addrs (
            ein TEXT, org_name TEXT, addr_type TEXT,
            city TEXT, state TEXT, zip TEXT,
            norm_state TEXT, norm_zip5 TEXT
        )
    """)

    conn.execute("""
        INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, 'org', city, state, zip,
               UPPER(TRIM(state)), SUBSTR(TRIM(zip), 1, 5)
        FROM irs527_organizations
        WHERE state IS NOT NULL
            AND zip IS NOT NULL AND TRIM(zip) != ''
    """)
    conn.execute("""
        INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, 'custodian', custodian_city, custodian_state, custodian_zip,
               UPPER(TRIM(custodian_state)), SUBSTR(TRIM(custodian_zip), 1, 5)
        FROM irs527_organizations
        WHERE custodian_state IS NOT NULL
            AND custodian_zip IS NOT NULL AND TRIM(custodian_zip) != ''
    """)
    conn.execute("""
        INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, 'contact', contact_city, contact_state, contact_zip,
               UPPER(TRIM(contact_state)), SUBSTR(TRIM(contact_zip), 1, 5)
        FROM irs527_organizations
        WHERE contact_state IS NOT NULL
            AND contact_zip IS NOT NULL AND TRIM(contact_zip) != ''
    """)
    conn.execute("""
        INSERT INTO _tmp_527_org_addrs
        SELECT ein, org_name, 'business', business_city, business_state, business_zip,
               UPPER(TRIM(business_state)), SUBSTR(TRIM(business_zip), 1, 5)
        FROM irs527_organizations
        WHERE business_state IS NOT NULL
            AND business_zip IS NOT NULL AND TRIM(business_zip) != ''
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS _idx_tmp_org_sz ON _tmp_527_org_addrs(norm_state, norm_zip5)")
    conn.commit()

    _INSERT_SQL = """
        INSERT INTO irs527_org_address_matches
            (ein, org_name, address_type,
             org_city, org_state, org_zip5,
             matched_entity_type, matched_entity_id, matched_entity_name,
             matched_city, matched_state, matched_zip5,
             address_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch = []
    total_matches = 0
    candidates_scored = 0

    def _flush():
        nonlocal total_matches
        if not batch:
            return
        conn.executemany(_INSERT_SQL, batch)
        total_matches += len(batch)
        batch.clear()

    seen = set()
    if can_match_committees:
        zip_col = None
        if _column_exists(conn, "bulk_committees_clean", "postal_code"):
            zip_col = "postal_code"
        elif _column_exists(conn, "bulk_committees_clean", "zip"):
            zip_col = "zip"

        if zip_col:
            # JOIN on zip5+state, then filter by org_name/committee_name Jaccard
            rows = conn.execute(
                f"""
                SELECT o.ein, o.org_name, o.addr_type,
                       o.city AS o_city, o.state AS o_state, o.zip AS o_zip,
                       c.committee_id_sbe, c.committee_name,
                       c.city AS c_city, c.state AS c_state, c.{zip_col} AS c_zip
                FROM _tmp_527_org_addrs o
                JOIN bulk_committees_clean c
                    ON o.norm_state = UPPER(TRIM(c.state))
                    AND o.norm_zip5 = SUBSTR(TRIM(c.{zip_col}), 1, 5)
                WHERE c.state IS NOT NULL AND c.{zip_col} IS NOT NULL
                """
            )

            for row in rows:
                candidates_scored += 1

                # Name similarity filter
                org_tokens = _normalize_name_tokens(row["org_name"])
                if not org_tokens:
                    continue
                cmte_tokens = _normalize_name_tokens(row["committee_name"])
                if not cmte_tokens:
                    continue
                name_score = _jaccard(org_tokens, cmte_tokens)
                if name_score < name_threshold:
                    continue

                # Deduplicate
                dedup_key = (row["ein"], row["addr_type"], "committee", row["committee_id_sbe"])
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                address_score = _address_score(
                    row["o_city"], row["o_state"], row["o_zip"],
                    row["c_city"], row["c_state"], row["c_zip"],
                )

                batch.append((
                    row["ein"], row["org_name"], row["addr_type"],
                    _normalize_city(row["o_city"]),
                    (row["o_state"] or "").strip().upper(),
                    _normalize_zip5(row["o_zip"]),
                    "committee", str(row["committee_id_sbe"]), row["committee_name"],
                    _normalize_city(row["c_city"]),
                    (row["c_state"] or "").strip().upper(),
                    _normalize_zip5(row["c_zip"]),
                    address_score,
                ))
                if len(batch) >= 50000:
                    _flush()

    if can_match_donors:
        if donor_indexes is None:
            donor_indexes = _build_donor_address_indexes(conn)

        donor_zip_index = donor_indexes["zip_index"]
        donor_city_index = donor_indexes["city_index"]

        org_rows = conn.execute(
            """
            SELECT ein, org_name, addr_type, city, state, zip, norm_state, norm_zip5
            FROM _tmp_527_org_addrs
            """
        ).fetchall()

        for o in org_rows:
            norm_state = o["norm_state"]
            norm_zip5 = o["norm_zip5"]
            norm_city = _normalize_city(o["city"])
            bucket_zip = donor_zip_index.get((norm_state, norm_zip5)) or []
            bucket_city = donor_city_index.get((norm_state, norm_city), []) if norm_city else []
            if not bucket_zip and not bucket_city:
                continue

            seen_donors = set()
            for bucket in (bucket_zip, bucket_city):
                for dr in bucket:
                    if dr["donor_key"] in seen_donors:
                        continue
                    seen_donors.add(dr["donor_key"])

                    address_score = _address_score(
                        o["city"], o["state"], o["zip"],
                        dr["donor_city"], dr["donor_state"], dr["donor_zip5"],
                    )
                    if address_score < 0.5:
                        continue

                    dedup_key = (o["ein"], o["addr_type"], "donor", dr["donor_key"])
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    batch.append((
                        o["ein"], o["org_name"], o["addr_type"],
                        _normalize_city(o["city"]),
                        (o["state"] or "").strip().upper(),
                        _normalize_zip5(o["zip"]),
                        "donor", str(dr["donor_key"]), dr["donor_name"],
                        _normalize_city(dr["donor_city"]),
                        (dr["donor_state"] or "").strip().upper(),
                        _normalize_zip5(dr["donor_zip5"]),
                        address_score,
                    ))
                    if len(batch) >= 50000:
                        _flush()

    _flush()
    conn.commit()

    conn.execute("DROP TABLE IF EXISTS _tmp_527_org_addrs")
    conn.commit()

    elapsed = time.perf_counter() - started
    logger.info(
        "527-org-address+name matches: %d (candidates_scored=%d, elapsed=%s)",
        total_matches, candidates_scored, _format_duration(elapsed),
    )
    return {"matches": total_matches}


def match_lobbying_to_527(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match lobbying clients against IRS 527 organizations.

    Stores results in lobbying_527_matches table.
    """
    if not _table_exists(conn, "lobbying_clients") or not _table_exists(conn, "irs527_organizations"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM lobbying_527_matches")
    conn.commit()

    clients = conn.execute("SELECT client_id, client_name FROM lobbying_clients").fetchall()
    orgs = conn.execute(
        "SELECT DISTINCT ein, org_name FROM irs527_organizations WHERE org_name IS NOT NULL"
    ).fetchall()

    right_items = []
    for o in orgs:
        tokens = _normalize_name_tokens(o["org_name"])
        if tokens:
            right_items.append(((o["ein"], o["org_name"]), tokens))

    left_items = []
    for c in clients:
        tokens = _normalize_name_tokens(c["client_name"])
        if tokens:
            left_items.append(((c["client_id"], c["client_name"]), tokens))

    pair_matches, stats = _sparse_jaccard_matches(
        left_items,
        right_items,
        threshold,
        job_label="lobbying-527",
    )
    batch = [
        (client_id, client_name, ein, org_name, score)
        for (client_id, client_name), (ein, org_name), score in pair_matches
    ]
    matches = len(batch)

    if batch:
        conn.executemany(
            """
            INSERT OR REPLACE INTO lobbying_527_matches
                (client_id, client_name, ein, org_name, score)
            VALUES (?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()

    logger.info(
        "Lobbying-to-527 matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(stats["elapsed_seconds"]),
        stats["scored_pairs"],
        stats["full_pairs"],
        stats["reduction"],
    )
    return {"matches": matches}


def run_all_cross_matching(
    conn: sqlite3.Connection,
    threshold: float = 0.80,
    incremental: bool = False,
) -> dict:
    """Run all cross-matching functions and return combined stats."""
    return _run_cross_matching_sequential(conn, threshold=threshold, incremental=incremental)


def _run_cross_matching_sequential(
    conn: sqlite3.Connection,
    threshold: float = 0.80,
    incremental: bool = False,
) -> dict:
    results: dict[str, Any] = {}
    if incremental:
        _ensure_incremental_state_table(conn)

    jobs: list[tuple[str, Any]] = [
        ("lobbying_donors", lambda c: match_lobbying_to_donors(c, threshold=threshold)),
        ("lobbying_expenditures", lambda c: match_lobbying_to_expenditure_payees(c, threshold=threshold)),
        ("527_committees", lambda c: match_527_to_committees(c, threshold=threshold)),
        ("527_expenditures", lambda c: match_527_expenditures_to_committees(c, threshold=threshold)),
        ("527_directors", lambda c: match_527_directors_to_donors(c, threshold=threshold)),
        ("527_director_candidates", lambda c: match_527_directors_to_candidates(c, threshold=threshold)),
        ("527_director_addresses", None),
        ("527_org_addresses", None),
        ("lobbying_527", lambda c: match_lobbying_to_527(c, threshold=threshold)),
    ]

    shared_donor_indexes: Optional[dict[str, Any]] = None
    for label, func in jobs:
        if incremental and _job_is_unchanged(conn, label):
            results[label] = {"matches": _job_matches_count(conn, label), "skipped": "unchanged"}
            continue

        if label == "527_director_addresses":
            if shared_donor_indexes is None and _table_exists(conn, "analytics_donor_summary"):
                shared_donor_indexes = _build_donor_address_indexes(conn, include_name_tokens=True)
            result = match_527_directors_to_donors_by_address(conn, donor_indexes=shared_donor_indexes)
        elif label == "527_org_addresses":
            result = match_527_org_addresses(conn, donor_indexes=shared_donor_indexes)
        else:
            result = func(conn)

        results[label] = result
        if incremental and "error" not in result:
            _save_job_fingerprint(conn, label, _job_fingerprint(conn, label))

    return results


def run_all_cross_matching_parallel(
    db_path: str,
    threshold: float = 0.80,
    max_workers: int = 4,
    job_timeout: int = 180,
    incremental: bool = False,
) -> dict:
    """Run cross-matching in two phases: fast jobs parallel, heavy jobs sequential.

    Phase 1 (parallel): Name-based Jaccard matching jobs run concurrently.
        These are read-heavy with brief writes (DELETE + INSERT small result sets).
    Phase 2 (sequential): Address-based SQL JOIN jobs run one at a time.
        These do large JOINs that can produce millions of rows and hold the
        write lock for extended periods.

    Per-job timeout: any job exceeding ``job_timeout`` seconds is killed and
    retried sequentially. If sequential retry also fails, the error is logged
    and the remaining jobs continue.

    Returns dict with same keys as ``run_all_cross_matching`` plus ``_errors``.
    """
    from database.connection import get_db

    # Phase 1: name-matching jobs (parallel read/score + isolated temp writes)
    parallel_jobs: list[tuple[str, str, Any]] = [
        ("lobbying_donors", _JOB_OUTPUT_TABLES["lobbying_donors"], lambda c: match_lobbying_to_donors(c, threshold=threshold)),
        ("lobbying_expenditures", _JOB_OUTPUT_TABLES["lobbying_expenditures"], lambda c: match_lobbying_to_expenditure_payees(c, threshold=threshold)),
        ("527_committees", _JOB_OUTPUT_TABLES["527_committees"], lambda c: match_527_to_committees(c, threshold=threshold)),
        ("527_expenditures", _JOB_OUTPUT_TABLES["527_expenditures"], lambda c: match_527_expenditures_to_committees(c, threshold=threshold)),
        ("527_directors", _JOB_OUTPUT_TABLES["527_directors"], lambda c: match_527_directors_to_donors(c, threshold=threshold)),
        ("527_director_candidates", _JOB_OUTPUT_TABLES["527_director_candidates"], lambda c: match_527_directors_to_candidates(c, threshold=threshold)),
        ("lobbying_527", _JOB_OUTPUT_TABLES["lobbying_527"], lambda c: match_lobbying_to_527(c, threshold=threshold)),
    ]

    # Phase 2: address-matching jobs (heavy SQL JOINs — run sequentially)
    sequential_jobs: list[tuple[str, str, Any]] = [
        ("527_director_addresses", _JOB_OUTPUT_TABLES["527_director_addresses"], None),
        ("527_org_addresses", _JOB_OUTPUT_TABLES["527_org_addresses"], None),
    ]

    results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    started = time.perf_counter()

    def _run_parallel_job(label: str, output_table: str, func):
        """Execute one job on its own connection; writes are isolated in temp output table."""
        conn = get_db(db_path)
        conn.execute("PRAGMA busy_timeout = 60000")
        try:
            _shadow_output_table_for_worker(conn, output_table)
            result = func(conn)
            columns, rows = _read_temp_output_rows(conn, output_table)
            return label, output_table, result, columns, rows
        finally:
            conn.close()

    if incremental:
        conn = get_db(db_path)
        try:
            _ensure_incremental_state_table(conn)
            for label, _, _ in parallel_jobs + sequential_jobs:
                if _job_is_unchanged(conn, label):
                    results[label] = {"matches": _job_matches_count(conn, label), "skipped": "unchanged"}
        finally:
            conn.close()

    # --- Phase 1: parallel name-matching ------------------------------------
    logger.info(
        "Phase 1: parallel name-matching (%d jobs, max_workers=%d, timeout=%ds)",
        len(parallel_jobs), max_workers, job_timeout,
    )

    failed_parallel: list[tuple[str, str, Any]] = []
    phase1_payloads: dict[str, tuple[str, list[str], list[tuple], dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_label = {
            executor.submit(_run_parallel_job, label, output_table, func): (label, output_table, func)
            for label, output_table, func in parallel_jobs
            if label not in results
        }
        for future in as_completed(future_to_label):
            label, output_table, func = future_to_label[future]
            try:
                _, payload_table, result, columns, rows = future.result(timeout=job_timeout)
                results[label] = result
                phase1_payloads[label] = (payload_table, columns, rows, result)
                logger.info("Phase 1 completed: %s -> %s", label, result)
            except TimeoutError:
                future.cancel()
                logger.warning(
                    "Phase 1 timeout (%ds): %s — will retry sequentially",
                    job_timeout, label,
                )
                failed_parallel.append((label, output_table, func))
            except Exception as exc:
                logger.error("Phase 1 failed: %s -> %s", label, exc)
                if "locked" in str(exc).lower():
                    logger.info("Will retry %s sequentially", label)
                    failed_parallel.append((label, output_table, func))
                else:
                    errors.append({"job": label, "error": str(exc), "phase": "parallel"})
                    results[label] = {"matches": 0, "error": str(exc)}

    if phase1_payloads:
        merge_conn = get_db(db_path)
        try:
            merge_conn.execute("PRAGMA busy_timeout = 120000")
            if incremental:
                _ensure_incremental_state_table(merge_conn)
            for label, (table_name, columns, rows, result) in phase1_payloads.items():
                _merge_rows_into_output_table(merge_conn, table_name, columns, rows)
                if incremental and "error" not in result:
                    _save_job_fingerprint(merge_conn, label, _job_fingerprint(merge_conn, label))
        finally:
            merge_conn.close()

    phase1_elapsed = time.perf_counter() - started
    phase1_skipped = sum(
        1
        for label, _, _ in parallel_jobs
        if label in results and results[label].get("skipped") == "unchanged"
    )
    logger.info(
        "Phase 1 finished in %s (%d succeeded, %d to retry)",
        _format_duration(phase1_elapsed),
        len(phase1_payloads) + phase1_skipped,
        len(failed_parallel),
    )

    # --- Phase 2: sequential (address jobs + any failed parallel jobs) ------
    all_sequential = failed_parallel + sequential_jobs
    if all_sequential:
        logger.info("Phase 2: sequential (%d jobs)", len(all_sequential))

    shared_donor_indexes: Optional[dict[str, Any]] = None
    for label, _output_table, func in all_sequential:
        if label in results and results[label].get("skipped") == "unchanged":
            continue
        job_started = time.perf_counter()
        try:
            conn = get_db(db_path)
            conn.execute("PRAGMA busy_timeout = 120000")
            try:
                if label == "527_director_addresses":
                    if shared_donor_indexes is None and _table_exists(conn, "analytics_donor_summary"):
                        shared_donor_indexes = _build_donor_address_indexes(conn, include_name_tokens=True)
                    result = match_527_directors_to_donors_by_address(
                        conn,
                        donor_indexes=shared_donor_indexes,
                    )
                elif label == "527_org_addresses":
                    result = match_527_org_addresses(
                        conn,
                        donor_indexes=shared_donor_indexes,
                    )
                else:
                    result = func(conn)
                results[label] = result
                if incremental and "error" not in result:
                    _save_job_fingerprint(conn, label, _job_fingerprint(conn, label))
                job_elapsed = time.perf_counter() - job_started
                logger.info(
                    "Phase 2 completed: %s -> %s (elapsed=%s)",
                    label, result, _format_duration(job_elapsed),
                )
            finally:
                conn.close()
        except Exception as exc:
            job_elapsed = time.perf_counter() - job_started
            logger.error(
                "Phase 2 failed: %s -> %s (elapsed=%s)",
                label, exc, _format_duration(job_elapsed),
            )
            errors.append({"job": label, "error": str(exc), "phase": "sequential"})
            results[label] = {"matches": 0, "error": str(exc)}

    elapsed = time.perf_counter() - started
    results["_errors"] = errors
    logger.info(
        "Cross-matching finished in %s (%d succeeded, %d failed)",
        _format_duration(elapsed),
        sum(1 for k, v in results.items() if k != "_errors" and "error" not in v),
        len(errors),
    )
    return results
