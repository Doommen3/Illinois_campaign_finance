"""Tests for cross-matching engine."""
from pathlib import Path

from database.connection import get_db, init_db
from database.cross_matching import (
    _normalize_name_tokens,
    _jaccard,
    _build_insert_sql,
    _build_donor_address_indexes,
    _apply_postgres_session_tuning,
    _shadow_output_table_for_worker,
    _merge_rows_into_output_table,
    match_lobbying_to_donors,
    match_lobbying_to_527,
    match_527_expenditures_to_committees,
    match_527_org_addresses,
    match_527_to_committees,
    run_all_cross_matching,
    run_all_cross_matching_parallel,
)


def test_normalize_name_tokens():
    # "corp" and "inc" are in stop words
    assert _normalize_name_tokens("ACME CORP INC") == ["acme"]
    assert _normalize_name_tokens("The Association of Farmers") == ["farmers"]
    assert _normalize_name_tokens("") == []
    assert _normalize_name_tokens(None) == []
    # Multi-word names retain non-stop words
    assert _normalize_name_tokens("Northwestern University") == ["northwestern", "university"]


def test_normalize_name_tokens_handles_bom_and_malformed_text():
    assert _normalize_name_tokens("\ufeffAcme, LLC!!!") == ["acme"]
    assert _normalize_name_tokens("\ufeff   ") == []
    assert _normalize_name_tokens("@@@###") == []


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


def _insert_donor_summary(conn, donor_key, donor_name, amount=1000, donor_city=None, donor_state="IL"):
    conn.execute(
        """
        INSERT OR REPLACE INTO analytics_donor_summary
            (source, donor_key, donor_name, donor_city, donor_state, total_amount, contribution_count, committee_count)
        VALUES ('bulk_receipts', ?, ?, ?, ?, ?, 1, 1)
        """,
        (donor_key, donor_name, donor_city, donor_state, amount),
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


def _insert_527_org_with_address(conn, ein, org_name, city, state, zip_code):
    conn.execute(
        """
        INSERT OR REPLACE INTO irs527_organizations
            (ein, form_id, form_id_seq, org_name, city, state, zip)
        VALUES (?, 1, 0, ?, ?, ?, ?)
        """,
        (ein, org_name, city, state, zip_code),
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


def test_run_all_cross_matching_parallel(tmp_path: Path):
    """Verify parallel orchestrator returns all 9 result keys with no errors."""
    db_path = str(tmp_path / "test_parallel.db")
    init_db(db_path)
    conn = get_db(db_path)
    _insert_lobbying_client(conn, 1, "Northwestern University")
    _insert_donor_summary(conn, "northwestern|university", "Northwestern University")
    _insert_527_org(conn, "123456789", "Citizens for Springfield")
    conn.close()

    results = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=4)

    expected_keys = {
        "lobbying_donors", "lobbying_expenditures",
        "527_committees", "527_expenditures",
        "527_directors", "527_director_candidates",
        "527_director_addresses", "527_org_addresses",
        "lobbying_527",
    }
    assert expected_keys.issubset(results.keys())
    assert results["_errors"] == []


def test_run_all_cross_matching_parallel_matches_sequential(tmp_path: Path):
    """Verify parallel and sequential produce the same match counts."""
    db_path = str(tmp_path / "test_compare.db")
    init_db(db_path)
    conn = get_db(db_path)
    _insert_lobbying_client(conn, 1, "Northwestern University")
    _insert_donor_summary(conn, "northwestern|university", "Northwestern University")
    _insert_527_org(conn, "123456789", "Citizens for Springfield")

    sequential_results = run_all_cross_matching(conn, threshold=0.80)
    conn.close()

    parallel_results = run_all_cross_matching_parallel(db_path, threshold=0.80, max_workers=4)
    parallel_results.pop("_errors", None)

    for key in sequential_results:
        seq_matches = sequential_results[key].get("matches", 0)
        par_matches = parallel_results[key].get("matches", 0)
        assert seq_matches == par_matches, f"{key}: sequential={seq_matches} != parallel={par_matches}"


def test_build_insert_sql_for_postgres_replace_uses_on_conflict():
    class PostgresCompatConnection:
        pass

    sql = _build_insert_sql(
        PostgresCompatConnection(),
        "lobbying_donor_matches",
        ["client_id", "donor_key", "client_name", "donor_name", "score", "method"],
        replace=True,
    )
    assert "INSERT OR REPLACE" not in sql
    assert "ON CONFLICT (client_id, donor_key)" in sql
    assert "DO UPDATE SET" in sql


def test_shadow_output_table_for_postgres_uses_public_source():
    class PostgresCompatConnection:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(sql.strip())
            return self

        def commit(self):
            return None

    conn = PostgresCompatConnection()
    _shadow_output_table_for_worker(conn, "lobbying_donor_matches")
    assert any("DROP TABLE IF EXISTS pg_temp.lobbying_donor_matches" in q for q in conn.sql)
    assert any("CREATE TEMP TABLE lobbying_donor_matches AS SELECT * FROM public.lobbying_donor_matches WHERE 0" in q for q in conn.sql)


def test_merge_rows_into_output_table_postgres_uses_swap_pattern():
    class PostgresCompatConnection:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []

        def execute(self, sql, params=None):
            self.execute_calls.append(sql)
            return self

        def executemany(self, sql, rows):
            self.executemany_calls.append((sql, list(rows)))
            return self

        def commit(self):
            return None

    conn = PostgresCompatConnection()
    _merge_rows_into_output_table(
        conn,
        "lobbying_donor_matches",
        ["client_id", "donor_key", "client_name", "donor_name", "score", "method"],
        [(1, "k1", "Client", "Donor", 0.95, "jaccard")],
    )

    assert any("CREATE TABLE _swap_lobbying_donor_matches_" in q for q in conn.execute_calls)
    assert any("LOCK TABLE lobbying_donor_matches IN ACCESS EXCLUSIVE MODE" in q for q in conn.execute_calls)
    assert any("ALTER TABLE lobbying_donor_matches RENAME TO _old_lobbying_donor_matches_" in q for q in conn.execute_calls)
    assert any("ALTER TABLE _swap_lobbying_donor_matches_" in q and "RENAME TO lobbying_donor_matches" in q for q in conn.execute_calls)
    assert conn.executemany_calls
    sql, rows = conn.executemany_calls[0]
    assert "INSERT INTO _swap_lobbying_donor_matches_" in sql
    assert "INSERT OR REPLACE" not in sql
    assert len(rows) == 1


def test_build_donor_address_indexes_persists_cache(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_donor_summary(
        conn,
        "r|x|x|x|x|x|62704",
        "Acme Donor",
        donor_city="Springfield",
        donor_state="IL",
    )

    indexes = _build_donor_address_indexes(conn, include_name_tokens=True)
    assert indexes["donor_count"] == 1

    row = conn.execute(
        "SELECT donor_zip5, norm_city, donor_tokens FROM cross_matching_donor_address_index WHERE donor_key = ?",
        ("r|x|x|x|x|x|62704",),
    ).fetchone()
    assert row is not None
    assert row["donor_zip5"] == "62704"
    assert row["norm_city"] == "springfield"
    assert "acme" in (row["donor_tokens"] or "")
    conn.close()


def test_match_527_org_addresses_uses_org_donor_name_prefilter(tmp_path: Path):
    conn = _setup_db(tmp_path)
    _insert_527_org_with_address(conn, "123456789", "Acme Future Action", "Springfield", "IL", "62704")

    _insert_donor_summary(
        conn,
        "d|1|1|1|1|1|62704",
        "Acme Future Holdings",
        donor_city="Springfield",
        donor_state="IL",
    )
    _insert_donor_summary(
        conn,
        "d|2|2|2|2|2|62704",
        "Unrelated Name",
        donor_city="Springfield",
        donor_state="IL",
    )

    result = match_527_org_addresses(conn, org_donor_name_threshold=0.20)
    assert result["matches"] >= 1

    rows = conn.execute(
        """
        SELECT matched_entity_type, matched_entity_id, matched_entity_name
        FROM irs527_org_address_matches
        WHERE ein = '123456789' AND matched_entity_type = 'donor'
        """
    ).fetchall()
    donor_ids = {r["matched_entity_id"] for r in rows}
    assert "d|1|1|1|1|1|62704" in donor_ids
    assert "d|2|2|2|2|2|62704" not in donor_ids
    conn.close()


def test_apply_postgres_session_tuning_executes_safe_settings():
    class PostgresCompatConnection:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(sql)
            return self

    conn = PostgresCompatConnection()
    _apply_postgres_session_tuning(conn)
    assert "SET synchronous_commit TO OFF" in conn.sql
    assert any("SET work_mem" in sql for sql in conn.sql)
    assert any("SET temp_buffers" in sql for sql in conn.sql)
