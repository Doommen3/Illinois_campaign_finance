"""Tests for cross-matching engine."""
from pathlib import Path

from database.connection import get_db, init_db
from database.cross_matching import (
    _normalize_name_tokens,
    _jaccard,
    match_lobbying_to_donors,
    match_lobbying_to_527,
    match_527_expenditures_to_committees,
    match_527_to_committees,
    run_all_cross_matching,
)


def test_normalize_name_tokens():
    # "corp" and "inc" are in stop words
    assert _normalize_name_tokens("ACME CORP INC") == ["acme"]
    assert _normalize_name_tokens("The Association of Farmers") == ["farmers"]
    assert _normalize_name_tokens("") == []
    assert _normalize_name_tokens(None) == []
    # Multi-word names retain non-stop words
    assert _normalize_name_tokens("Northwestern University") == ["northwestern", "university"]


def test_jaccard_identical():
    assert _jaccard(["acme", "corp"], ["acme", "corp"]) == 1.0


def test_jaccard_overlap():
    score = _jaccard(["acme", "corp", "holdings"], ["acme", "corp"])
    assert 0.6 <= score <= 0.7  # 2/3


def test_jaccard_no_overlap():
    assert _jaccard(["alpha"], ["beta"]) == 0.0


def test_jaccard_empty():
    assert _jaccard([], ["beta"]) == 0.0
    assert _jaccard([], []) == 0.0


def _setup_db(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_db(db_path)
    return conn


def _insert_lobbying_client(conn, client_id, client_name):
    conn.execute(
        "INSERT OR IGNORE INTO lobbying_clients (client_id, client_name) VALUES (?, ?)",
        (client_id, client_name),
    )
    conn.commit()


def _insert_donor_summary(conn, donor_key, donor_name, amount=1000):
    conn.execute(
        """
        INSERT OR REPLACE INTO analytics_donor_summary
            (source, donor_key, donor_name, total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', ?, ?, ?, 1, 1)
        """,
        (donor_key, donor_name, amount),
    )
    conn.commit()


def _insert_527_org(conn, ein, org_name, state="IL"):
    conn.execute(
        """
        INSERT OR REPLACE INTO irs527_organizations
            (ein, form_id, form_id_seq, org_name, state)
        VALUES (?, 1, 0, ?, ?)
        """,
        (ein, org_name, state),
    )
    conn.commit()


def _insert_committee(conn, committee_id_sbe, committee_name):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_committees_clean (
            committee_id_sbe INTEGER PRIMARY KEY,
            committee_name TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO bulk_committees_clean
            (committee_id_sbe, committee_name)
        VALUES (?, ?)
        """,
        (committee_id_sbe, committee_name),
    )
    conn.commit()


def _insert_527_expenditure(conn, ein, org_name, recipient_name, state="IL"):
    conn.execute(
        """
        INSERT INTO irs527_expenditures (
            form_id, ein, org_name, recipient_name, state
        ) VALUES (1, ?, ?, ?, ?)
        """,
        (ein, org_name, recipient_name, state),
    )
    conn.commit()


def test_match_lobbying_to_donors_finds_match(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_lobbying_client(conn, 1, "Northwestern University")
    _insert_donor_summary(conn, "northwestern|university", "Northwestern University")

    stats = match_lobbying_to_donors(conn, threshold=0.80)
    assert stats["matches"] >= 1

    match = conn.execute(
        "SELECT * FROM lobbying_donor_matches WHERE client_id = 1"
    ).fetchone()
    assert match is not None
    assert match["score"] >= 0.80

    conn.close()


def test_match_lobbying_to_donors_no_match(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_lobbying_client(conn, 1, "Alpha Beta Gamma")
    _insert_donor_summary(conn, "xyz|corp", "XYZ Corporation Holdings")

    stats = match_lobbying_to_donors(conn, threshold=0.80)
    assert stats["matches"] == 0

    conn.close()


def test_match_lobbying_to_donors_threshold_filter(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_lobbying_client(conn, 1, "Acme Corp Holdings")
    _insert_donor_summary(conn, "acme|corp", "Acme Corp")

    # High threshold should reject partial matches
    stats = match_lobbying_to_donors(conn, threshold=0.99)
    assert stats["matches"] == 0

    conn.close()


def test_match_527_to_committees(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_527_org(conn, "123456789", "Citizens for Springfield")
    _insert_committee(conn, 101, "Citizens for Springfield")

    stats = match_527_to_committees(conn, threshold=0.80)
    assert stats["matches"] >= 1

    conn.close()


def test_match_527_expenditures_to_committees(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_committee(conn, 101, "Citizens for Springfield")
    _insert_527_expenditure(conn, "123456789", "Citizens Org", "Citizens for Springfield", state="IL")

    stats = match_527_expenditures_to_committees(conn, threshold=0.80)
    assert stats["matches"] >= 1

    match = conn.execute(
        "SELECT * FROM irs527_expenditure_recipient_matches WHERE ein = ?",
        ("123456789",),
    ).fetchone()
    assert match is not None
    assert match["matched_type"] == "committee"
    assert match["score"] >= 0.80

    conn.close()


def test_match_lobbying_to_527(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_lobbying_client(conn, 1, "Environmental Law Center")
    _insert_527_org(conn, "111111111", "Environmental Law Center")

    stats = match_lobbying_to_527(conn, threshold=0.80)
    assert stats["matches"] >= 1

    match = conn.execute(
        "SELECT * FROM lobbying_527_matches WHERE client_id = 1"
    ).fetchone()
    assert match is not None
    assert match["score"] >= 0.80

    conn.close()


def test_match_missing_tables(tmp_path: Path):
    """Test graceful handling when required tables are missing."""
    db_path = str(tmp_path / "empty.db")
    init_db(db_path)
    conn = get_db(db_path)

    # These should not raise
    stats = match_lobbying_to_donors(conn, threshold=0.80)
    assert stats.get("skipped") == "missing_tables" or stats["matches"] == 0

    conn.close()


def test_run_all_cross_matching(tmp_path: Path):
    conn = _setup_db(tmp_path)
    results = run_all_cross_matching(conn, threshold=0.80)
    assert "lobbying_donors" in results
    assert "lobbying_expenditures" in results
    assert "527_committees" in results
    assert "527_expenditures" in results
    assert "527_directors" in results
    assert "lobbying_527" in results
    conn.close()
