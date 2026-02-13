"""Cross-matching engine for lobbying, IRS 527, and campaign finance data."""
from __future__ import annotations

import math
import logging
import re
import sqlite3
import time
from typing import Optional

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

    # Pre-tokenize donors
    donor_tokens = []
    for d in donors:
        tokens = _normalize_name_tokens(d["donor_name"])
        if tokens:
            donor_tokens.append((d["donor_key"], d["donor_name"], tokens))

    matches = 0
    batch = []
    for c in clients:
        c_tokens = _normalize_name_tokens(c["client_name"])
        if not c_tokens:
            continue
        for d_key, d_name, d_tokens in donor_tokens:
            score = _jaccard(c_tokens, d_tokens)
            if score >= threshold:
                batch.append((c["client_id"], d_key, c["client_name"], d_name, score, "jaccard"))
                matches += 1

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

    logger.info("Lobbying-to-donor matches: %d", matches)
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

    payee_tokens = []
    for p in payees:
        tokens = _normalize_name_tokens(p["payee_name"])
        if tokens:
            payee_tokens.append((p["payee_name"], p["committee_id_sbe"], tokens))

    # Clients
    clients = conn.execute("SELECT client_id, client_name FROM lobbying_clients").fetchall()
    entities = conn.execute("SELECT entity_id, entity_name FROM lobbying_entities").fetchall()

    matches = 0
    batch = []

    for c in clients:
        c_tokens = _normalize_name_tokens(c["client_name"])
        if not c_tokens:
            continue
        for p_name, cmte_id, p_tokens in payee_tokens:
            score = _jaccard(c_tokens, p_tokens)
            if score >= threshold:
                batch.append(("client", c["client_id"], c["client_name"], p_name, cmte_id, score))
                matches += 1

    for e in entities:
        e_tokens = _normalize_name_tokens(e["entity_name"])
        if not e_tokens:
            continue
        for p_name, cmte_id, p_tokens in payee_tokens:
            score = _jaccard(e_tokens, p_tokens)
            if score >= threshold:
                batch.append(("entity", e["entity_id"], e["entity_name"], p_name, cmte_id, score))
                matches += 1

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

    logger.info("Lobbying-to-expenditure matches: %d", matches)
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

    cmte_tokens = []
    for c in committees:
        tokens = _normalize_name_tokens(c["committee_name"])
        if tokens:
            cmte_tokens.append((c["committee_id_sbe"], c["committee_name"], tokens))

    matches = 0
    batch = []
    for o in orgs:
        o_tokens = _normalize_name_tokens(o["org_name"])
        if not o_tokens:
            continue
        for cmte_id, cmte_name, c_tokens in cmte_tokens:
            score = _jaccard(o_tokens, c_tokens)
            if score >= threshold:
                batch.append((o["ein"], o["org_name"], cmte_id, cmte_name, score, "jaccard"))
                matches += 1

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

    logger.info("527-to-committee matches: %d", matches)
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

    # Sparse-index approach: tokenize targets once, then only score candidates that share >=1 token.
    # This preserves exact Jaccard scores for threshold > 0 while avoiding full cartesian scans.
    index_started = time.perf_counter()
    token_to_id: dict[str, int] = {}
    target_meta: list[tuple[str, str, str]] = []
    target_token_sets: list[frozenset[int]] = []
    target_token_counts: list[int] = []
    postings: dict[int, list[int]] = {}

    def _token_id(token: str) -> int:
        existing = token_to_id.get(token)
        if existing is not None:
            return existing
        existing = len(token_to_id)
        token_to_id[token] = existing
        return existing

    for matched_type, matched_id, matched_name, target_tokens in all_targets:
        token_ids = frozenset(_token_id(tok) for tok in target_tokens)
        if not token_ids:
            continue
        target_idx = len(target_meta)
        target_meta.append((matched_type, matched_id, matched_name))
        target_token_sets.append(token_ids)
        target_token_counts.append(len(token_ids))
        for tok_id in token_ids:
            postings.setdefault(tok_id, []).append(target_idx)

    logger.info(
        "Built sparse target index in %s (targets=%d, unique_tokens=%d)",
        _format_duration(time.perf_counter() - index_started),
        len(target_meta),
        len(token_to_id),
    )

    if not target_meta:
        logger.info(
            "527-expenditure-to-committee/candidate matches: 0 (elapsed=%s)",
            _format_duration(time.perf_counter() - started),
        )
        return {"matches": 0}

    matches = 0
    batch = []
    scored_pairs = 0
    loop_started = time.perf_counter()
    total_expenditures = len(expenditures)
    progress_every = 5000
    exhaustive_mode = threshold <= 0.0
    if exhaustive_mode:
        logger.warning(
            "Threshold %.3f requires exhaustive scoring; sparse candidate pruning is disabled.",
            threshold,
        )

    for idx, exp in enumerate(expenditures, start=1):
        exp_tokens = _normalize_name_tokens(exp["recipient_name"])
        if not exp_tokens:
            continue

        exp_token_ids = frozenset(_token_id(tok) for tok in exp_tokens)
        if not exp_token_ids:
            continue
        exp_token_count = len(exp_token_ids)

        if exhaustive_mode:
            candidate_intersections = {
                target_idx: len(exp_token_ids & target_token_sets[target_idx])
                for target_idx in range(len(target_meta))
            }
        else:
            candidate_intersections: dict[int, int] = {}
            for tok_id in exp_token_ids:
                for target_idx in postings.get(tok_id, ()):
                    candidate_intersections[target_idx] = candidate_intersections.get(target_idx, 0) + 1

        scored_pairs += len(candidate_intersections)
        for target_idx, intersection in candidate_intersections.items():
            union = exp_token_count + target_token_counts[target_idx] - intersection
            if union <= 0:
                continue
            score = intersection / union
            if score >= threshold:
                matched_type, matched_id, matched_name = target_meta[target_idx]
                batch.append((
                    exp["ein"], exp["org_name"], exp["recipient_name"],
                    matched_type, matched_id, matched_name, score,
                ))
                matches += 1

        if idx % progress_every == 0:
            elapsed = time.perf_counter() - loop_started
            rate = idx / elapsed if elapsed > 0 else 0.0
            remaining = total_expenditures - idx
            eta_seconds = remaining / rate if rate > 0 else math.inf
            eta_text = _format_duration(eta_seconds) if math.isfinite(eta_seconds) else "unknown"
            logger.info(
                "527-expenditure progress: %d/%d (%.1f%%), matches=%d, rate=%.1f rows/s, eta=%s",
                idx,
                total_expenditures,
                (idx / total_expenditures) * 100,
                matches,
                rate,
                eta_text,
            )

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

    full_pair_count = total_expenditures * len(target_meta)
    reduction = (full_pair_count / scored_pairs) if scored_pairs else 0.0
    logger.info(
        "527-expenditure-to-committee/candidate matches: %d (elapsed=%s, scored_pairs=%d/%d, reduction=%.1fx)",
        matches,
        _format_duration(time.perf_counter() - started),
        scored_pairs,
        full_pair_count,
        reduction,
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

    donor_tokens = []
    for d in donors:
        tokens = _normalize_name_tokens(d["donor_name"])
        if tokens:
            donor_tokens.append((d["donor_key"], d["donor_name"], tokens))

    matches = 0
    batch = []
    for director in directors:
        d_tokens = _normalize_name_tokens(director["person_name"])
        if not d_tokens:
            continue
        for donor_key, donor_name, dt_tokens in donor_tokens:
            score = _jaccard(d_tokens, dt_tokens)
            if score >= threshold:
                batch.append((
                    director["ein"], director["org_name"], director["person_name"],
                    donor_key, donor_name, score,
                ))
                matches += 1

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

    logger.info("527-director-to-donor matches: %d", matches)
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

    org_tokens = []
    for o in orgs:
        tokens = _normalize_name_tokens(o["org_name"])
        if tokens:
            org_tokens.append((o["ein"], o["org_name"], tokens))

    matches = 0
    batch = []
    for c in clients:
        c_tokens = _normalize_name_tokens(c["client_name"])
        if not c_tokens:
            continue
        for ein, org_name, o_tokens in org_tokens:
            score = _jaccard(c_tokens, o_tokens)
            if score >= threshold:
                batch.append((c["client_id"], c["client_name"], ein, org_name, score))
                matches += 1

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

    logger.info("Lobbying-to-527 matches: %d", matches)
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
