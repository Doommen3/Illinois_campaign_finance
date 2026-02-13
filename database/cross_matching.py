"""Cross-matching engine for lobbying, IRS 527, and campaign finance data."""
from __future__ import annotations

import logging
import re
import sqlite3
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
    if not _table_exists(conn, "irs527_expenditures"):
        return {"matches": 0, "skipped": "missing_tables"}

    conn.execute("DELETE FROM irs527_expenditure_recipient_matches")
    conn.commit()

    expenditures = conn.execute(
        """
        SELECT rowid_local, ein, org_name, recipient_name
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

    matches = 0
    batch = []
    for exp in expenditures:
        exp_tokens = _normalize_name_tokens(exp["recipient_name"])
        if not exp_tokens:
            continue
        for matched_type, matched_id, matched_name, t_tokens in all_targets:
            score = _jaccard(exp_tokens, t_tokens)
            if score >= threshold:
                batch.append((
                    exp["ein"], exp["org_name"], exp["recipient_name"],
                    matched_type, matched_id, matched_name, score,
                ))
                matches += 1

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

    logger.info("527-expenditure-to-committee/candidate matches: %d", matches)
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
