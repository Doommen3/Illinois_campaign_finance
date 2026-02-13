"""Cross-matching engine for lobbying, IRS 527, and campaign finance data."""
from __future__ import annotations

import math
import logging
import re
import sqlite3
import time
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

    right_items = [(target_meta, target_tokens) for target_meta, target_tokens in all_targets]
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


def match_527_directors_to_donors(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Match IRS 527 directors against analytics_donor_summary.

    Stores results in irs527_director_donor_matches table.
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
        ORDER BY total_amount DESC
        LIMIT 50000
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


def run_all_cross_matching(conn: sqlite3.Connection, threshold: float = 0.80) -> dict:
    """Run all cross-matching functions and return combined stats."""
    results = {}
    results["lobbying_donors"] = match_lobbying_to_donors(conn, threshold=threshold)
    results["lobbying_expenditures"] = match_lobbying_to_expenditure_payees(conn, threshold=threshold)
    results["527_committees"] = match_527_to_committees(conn, threshold=threshold)
    results["527_expenditures"] = match_527_expenditures_to_committees(conn, threshold=threshold)
    results["527_directors"] = match_527_directors_to_donors(conn, threshold=threshold)
    results["lobbying_527"] = match_lobbying_to_527(conn, threshold=threshold)
    return results
