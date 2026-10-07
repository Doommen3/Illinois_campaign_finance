"""Tests for privacy redactions (database/privacy_redactions.py).

Plan: .claude/plans/privacy-redaction.md, Step 1 (tests first).

Every name, address and zip below is FICTIONAL. Real requesters' details never
go in the repo (plan design decision 3); they are inserted on prod via the CLI.

Matching contract: last name + zip5, exact after lower/trim on both sides; a
row's zip is the first five digits after stripping non-digits; the request's
first_name is optional (NULL matches any first name). `donors` and
`analytics_donor_summary` match the zip as a substring of the combined address
string, as the plan specifies.

`isbe_receipts` and the legacy bulk table are ETL-created (absent from the test
schema), so `_setup` creates minimal versions. `donors`,
`analytics_donor_summary` and `privacy_redactions` come from schema.sql.
"""
from datetime import date, datetime

import pytest
from click.testing import CliRunner

from cli.commands import cli
from database.connection import get_db, init_db
from database.privacy_redactions import (
    add_privacy_redaction,
    apply_privacy_redactions,
    refresh_dependent_views,
)

# ISBE's own literal. Asserted directly (not via the module constant) so a
# wrong constant cannot pass.
REDACTED = "Redaction Requested"

# Case and stray whitespace on the request side on purpose.
WALTER_REQUEST = {
    "last_name": " brinkerhoff",
    "zip5": "61820",
    "first_name": "WALTER ",
    "requested_by": "Fixture Requester LLC",
    "request_date": date(2026, 7, 16),
    "statute": "705 ILCS 90",
}
WALTER_ANY_FIRST = {**WALTER_REQUEST, "first_name": None}

ISBE_COLS = (
    "id", "committee_id", "first_name", "last_name", "address1", "address2",
    "city", "state", "zipcode", "received_date", "amount", "archived",
)
# Expected result:                                        WALTER_REQUEST | WALTER_ANY_FIRST
ISBE_ROWS = [
    (101, 7, "Walter", "Brinkerhoff", "100 Test Ave", "Unit 2", "Champaign", "IL",
     "61820-4455", date(2024, 3, 1), 500.0, False),     # match (zip+4)          | match
    (102, 7, "Walter", "Brinkerhoff", "100 Test Ave", "Unit 2", "Champaign", "IL",
     "61820-4455", date(2024, 3, 1), 500.0, True),      # match (archived copy)  | match
    (103, 9, " WALTER", "BRINKERHOFF ", "100 TEST AVE", None, "CHAMPAIGN", "IL",
     " 61820", date(2022, 10, 15), 250.0, False),       # match (case/space)     | match
    (104, 7, "Walter", "Brinkerhoff", "9 Elm St", None, "Urbana", "IL",
     "61821", date(2024, 5, 1), 100.0, False),          # NO: other zip          | NO
    (105, 7, "Walter", "Brinkerhoffer", "12 Oak Ave", None, "Champaign", "IL",
     "61820", date(2024, 5, 2), 100.0, False),          # NO: other last name    | NO
    (106, 7, "Edith", "Brinkerhoff", "100 Test Ave", "Unit 2", "Champaign", "IL",
     "61820-4455", date(2024, 5, 3), 100.0, False),     # NO: other first name   | match
    (107, 7, "Walter", "Brinkerhoff", "5 Lake Dr", None, "Chicago", "IL",
     "606182000", date(2024, 5, 4), 100.0, False),      # NO: digits contain 61820 at offset 2, first five are 60618 | NO
    (108, 11, "Lina", "Tessaro", "7 Mill Rd", None, "Springfield", "IL",
     "62704-1100", date(2024, 6, 1), 50.0, False),      # NO (second-request target) | NO
]
# WALTER_REQUEST -> 3 rows (101, 102, 103); WALTER_ANY_FIRST -> 4 (+106).
ISBE_SEED = {row[0]: dict(zip(ISBE_COLS, row)) for row in ISBE_ROWS}

LEGACY_COLS = (
    "bulk_row_id", "committee_id_sbe", "first_name", "last_or_business_name",
    "address_line_1", "address_line_2", "city", "state", "postal_code",
    "amount", "is_archived",
)
# Same people as ISBE_ROWS, bulk_row_id = id + 100:
# WALTER_REQUEST -> 3 rows (201, 202, 203); WALTER_ANY_FIRST -> 4 (+206).
LEGACY_ROWS = [(r[0] + 100, *r[1:9], r[10], int(r[11])) for r in ISBE_ROWS]
LEGACY_SEED = {row[0]: dict(zip(LEGACY_COLS, row)) for row in LEGACY_ROWS}

DONOR_COLS = (
    "id", "name", "address", "normalized_name", "normalized_address",
    "occupation", "employer",
)
# Expected result:                                        WALTER_REQUEST | WALTER_ANY_FIRST
DONOR_ROWS = [
    (301, "Walter Brinkerhoff", "100 Test Ave Unit 2, Champaign, IL 61820-4455",
     "walter brinkerhoff", "100 test ave unit 2 champaign il 61820-4455",
     "Engineer", "Acme Widgets"),                       # match                  | match
    (302, "WALTER BRINKERHOFF", "100 TEST AVE, CHAMPAIGN, IL 61820",
     "walter brinkerhoff", "100 test ave champaign il 61820", None, None),  # match (case) | match
    (303, "Walter Brinkerhoff", "9 Elm St, Urbana, IL 61821",
     "walter brinkerhoff", "9 elm st urbana il 61821", None, None),  # NO: other zip | NO
    (304, "Walter Brinkerhoffer", "12 Oak Ave, Champaign, IL 61820",
     "walter brinkerhoffer", "12 oak ave champaign il 61820", None, None),  # NO: last name | NO
    (305, "Edith Brinkerhoff", "100 Test Ave Unit 2, Champaign, IL 61820-4455",
     "edith brinkerhoff", "100 test ave unit 2 champaign il 61820-4455",
     None, None),                                       # NO: first name         | match
]
# WALTER_REQUEST -> 2 rows (301, 302); WALTER_ANY_FIRST -> 3 (+305).
DONOR_SEED = {row[0]: dict(zip(DONOR_COLS, row)) for row in DONOR_ROWS}

SUMMARY_COLS = (
    "source", "donor_key", "donor_name", "donor_address", "donor_city",
    "donor_state", "total_amount", "contribution_count", "committee_count",
)
KEY_WALTER_ZIP4 = "walter|brinkerhoff|100 test ave|unit 2|champaign|il|61820-4455"
KEY_WALTER_ZIP5 = "walter|brinkerhoff|100 test ave||champaign|il|61820"
KEY_WALTER_OTHER_ZIP = "walter|brinkerhoff|9 elm st||urbana|il|61821"
KEY_BRINKERHOFFER = "walter|brinkerhoffer|12 oak ave||champaign|il|61820"
KEY_EDITH = "edith|brinkerhoff|100 test ave|unit 2|champaign|il|61820-4455"
# Expected result:                                        WALTER_REQUEST | WALTER_ANY_FIRST
SUMMARY_ROWS = [
    ("bulk_receipts", KEY_WALTER_ZIP4, "Walter Brinkerhoff",
     "100 Test Ave, Unit 2, Champaign, IL, 61820-4455", "Champaign", "IL", 500.0, 1, 1),  # delete | delete
    ("bulk_receipts", KEY_WALTER_ZIP5, "WALTER BRINKERHOFF",
     "100 TEST AVE, CHAMPAIGN, IL, 61820", "CHAMPAIGN", "IL", 250.0, 1, 1),             # delete | delete
    ("contributions", "donor:301", "Walter Brinkerhoff",
     "100 Test Ave Unit 2, Champaign, IL 61820-4455", None, None, 75.0, 1, 1),          # delete | delete
    ("bulk_receipts", KEY_WALTER_OTHER_ZIP, "Walter Brinkerhoff",
     "9 Elm St, Urbana, IL, 61821", "Urbana", "IL", 100.0, 1, 1),                       # keep   | keep
    ("bulk_receipts", KEY_BRINKERHOFFER, "Walter Brinkerhoffer",
     "12 Oak Ave, Champaign, IL, 61820", "Champaign", "IL", 100.0, 1, 1),               # keep   | keep
    ("bulk_receipts", KEY_EDITH, "Edith Brinkerhoff",
     "100 Test Ave, Unit 2, Champaign, IL, 61820-4455", "Champaign", "IL", 100.0, 1, 1),  # keep | delete
]
# WALTER_REQUEST -> 3 deleted; WALTER_ANY_FIRST -> 4 deleted.

# Counts for WALTER_REQUEST against the default _setup tables.
WALTER_COUNTS = {
    "isbe_receipts": 3,
    "bulk_receipts_clean_legacy": 3,
    "donors": 2,
    "analytics_donor_summary": 3,
}


def _insert(conn, table, cols, rows):
    placeholders = ", ".join("?" * len(cols))
    conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)


def _setup(tmp_path, legacy_tables=("bulk_receipts_clean_legacy",)):
    """init_db, create the ETL-only tables, seed every table, commit."""
    db_path = str(tmp_path / "privacy.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute(
        "CREATE TABLE isbe_receipts (id BIGINT PRIMARY KEY, committee_id INT, "
        "first_name TEXT, last_name TEXT, address1 TEXT, address2 TEXT, city TEXT, "
        "state TEXT, zipcode TEXT, received_date DATE, amount DOUBLE PRECISION, "
        "archived BOOLEAN DEFAULT FALSE, redaction_requested BOOLEAN DEFAULT FALSE)"
    )
    _insert(conn, "isbe_receipts", ISBE_COLS, ISBE_ROWS)
    for table in legacy_tables:
        conn.execute(
            f"CREATE TABLE {table} (bulk_row_id INTEGER PRIMARY KEY, "
            "committee_id_sbe INTEGER, first_name TEXT, last_or_business_name TEXT, "
            "address_line_1 TEXT, address_line_2 TEXT, city TEXT, state TEXT, "
            "postal_code TEXT, amount REAL, is_archived INTEGER, "
            "redaction_requested INTEGER DEFAULT 0)"
        )
        _insert(conn, table, LEGACY_COLS, LEGACY_ROWS)
    _insert(conn, "donors", DONOR_COLS, DONOR_ROWS)
    _insert(conn, "analytics_donor_summary", SUMMARY_COLS, SUMMARY_ROWS)
    conn.commit()
    return conn


def _row(conn, table, key_col, key):
    return conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _create_condensed_matview(conn):
    # Mirrors the ETL matview: a snapshot of isbe_receipts with a unique id
    # index (required by REFRESH ... CONCURRENTLY).
    conn.execute("CREATE MATERIALIZED VIEW isbe_condensed_receipts AS SELECT * FROM isbe_receipts")
    conn.execute("CREATE UNIQUE INDEX idx_test_condensed_id ON isbe_condensed_receipts (id)")


# --- 1. matched row scrubbed to the ISBE format ------------------------------

def test_matched_isbe_rows_scrubbed_to_isbe_format(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result["isbe_receipts"] == 3  # 101, 102 (archived copy), 103 (case/space variant)
    for row_id in (101, 102, 103):
        row = _row(conn, "isbe_receipts", "id", row_id)
        assert row["address1"] == REDACTED, row_id
        assert row["address2"] is None, row_id
        assert row["city"] is None, row_id
        assert row["state"] is None, row_id
        assert row["zipcode"] is None, row_id
        assert row["redaction_requested"] is True, row_id
        for col in ("first_name", "last_name", "committee_id", "received_date", "amount", "archived"):
            assert row[col] == ISBE_SEED[row_id][col], (row_id, col)
    assert _count(conn, "isbe_receipts") == 8  # contribution rows are never deleted


# --- 2. negative controls ----------------------------------------------------

def test_negative_controls_untouched_when_request_first_name_set(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    apply_privacy_redactions(conn)

    for row_id in (104, 105, 106, 107, 108):
        row = _row(conn, "isbe_receipts", "id", row_id)
        for col in ISBE_COLS:
            assert row[col] == ISBE_SEED[row_id][col], (row_id, col)
        assert row["redaction_requested"] is False, row_id
    for row_id in (204, 205, 206, 207, 208):
        row = _row(conn, "bulk_receipts_clean_legacy", "bulk_row_id", row_id)
        for col in LEGACY_COLS:
            assert row[col] == LEGACY_SEED[row_id][col], (row_id, col)
        assert row["redaction_requested"] == 0, row_id
    for donor_id in (303, 304, 305):
        row = _row(conn, "donors", "id", donor_id)
        for col in DONOR_COLS:
            assert row[col] == DONOR_SEED[donor_id][col], (donor_id, col)


def test_null_request_first_name_matches_any_first_name(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_ANY_FIRST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result["isbe_receipts"] == 4  # 101, 102, 103 + 106 (Edith)
    assert result["bulk_receipts_clean_legacy"] == 4  # 201, 202, 203 + 206
    assert result["donors"] == 3  # 301, 302 + 305
    assert result["analytics_donor_summary"] == 4  # three Walter rows + Edith
    assert _row(conn, "isbe_receipts", "id", 106)["address1"] == REDACTED
    assert _row(conn, "bulk_receipts_clean_legacy", "bulk_row_id", 206)["address_line_1"] == REDACTED
    assert _row(conn, "donors", "id", 305)["address"] == REDACTED
    for row_id in (104, 105, 107, 108):
        assert _row(conn, "isbe_receipts", "id", row_id)["address1"] == ISBE_SEED[row_id]["address1"], row_id


# --- legacy-shaped bulk tables ----------------------------------------------

@pytest.mark.parametrize("table", ["bulk_receipts_clean_legacy", "bulk_receipts_clean"])
def test_legacy_shape_bulk_table_scrubbed(tmp_path, table):
    # bulk_receipts_clean is a real table when the compat swap was skipped.
    conn = _setup(tmp_path, legacy_tables=(table,))
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result[table] == 3  # 201, 202, 203
    for row_id in (201, 202, 203):
        row = _row(conn, table, "bulk_row_id", row_id)
        assert row["address_line_1"] == REDACTED, row_id
        assert row["address_line_2"] is None, row_id
        assert row["city"] is None, row_id
        assert row["state"] is None, row_id
        assert row["postal_code"] is None, row_id
        assert row["redaction_requested"] == 1, row_id
        for col in ("first_name", "last_or_business_name", "committee_id_sbe", "amount", "is_archived"):
            assert row[col] == LEGACY_SEED[row_id][col], (row_id, col)
    for row_id in (204, 205, 206, 207, 208):
        row = _row(conn, table, "bulk_row_id", row_id)
        assert row["address_line_1"] == LEGACY_SEED[row_id]["address_line_1"], row_id
        assert row["redaction_requested"] == 0, row_id


def test_bulk_receipts_clean_view_is_skipped(tmp_path):
    # Prod shape after the compat swap: bulk_receipts_clean is a VIEW over the
    # condensed matview. Any UPDATE on it raises in PostgreSQL.
    conn = _setup(tmp_path)
    _create_condensed_matview(conn)
    conn.execute(
        "CREATE VIEW bulk_receipts_clean AS SELECT r.id AS bulk_row_id, r.first_name, "
        "r.last_name AS last_or_business_name, r.address1 AS address_line_1, "
        "r.address2 AS address_line_2, r.city, r.state, r.zipcode AS postal_code, "
        "CASE WHEN r.redaction_requested THEN 1 ELSE 0 END AS redaction_requested "
        "FROM isbe_condensed_receipts r"
    )
    conn.commit()
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result.get("bulk_receipts_clean", 0) == 0
    assert result["isbe_receipts"] == 3
    assert result["bulk_receipts_clean_legacy"] == 3


# --- 3. idempotent -----------------------------------------------------------

def test_second_apply_changes_nothing(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    first = apply_privacy_redactions(conn)
    conn.commit()
    second = apply_privacy_redactions(conn)

    for table, expected in WALTER_COUNTS.items():
        assert first[table] == expected, table  # not a no-op the first time
    assert second == {table: 0 for table in first}
    assert _row(conn, "isbe_receipts", "id", 101)["address1"] == REDACTED
    assert _count(conn, "analytics_donor_summary") == 3  # rerun deletes nothing more


# --- 4. analytics_donor_summary ---------------------------------------------

def test_analytics_donor_summary_matching_rows_deleted(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result["analytics_donor_summary"] == 3
    rows = conn.execute("SELECT source, donor_key FROM analytics_donor_summary").fetchall()
    remaining = {(row["source"], row["donor_key"]) for row in rows}
    assert remaining == {
        ("bulk_receipts", KEY_WALTER_OTHER_ZIP),
        ("bulk_receipts", KEY_BRINKERHOFFER),
        ("bulk_receipts", KEY_EDITH),
    }


# --- 5. donors ---------------------------------------------------------------

def test_donors_rows_scrubbed(tmp_path):
    conn = _setup(tmp_path)
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result["donors"] == 2  # 301, 302
    for donor_id in (301, 302):
        row = _row(conn, "donors", "id", donor_id)
        assert row["address"] == REDACTED, donor_id
        assert row["normalized_address"] is None, donor_id
        for col in ("name", "normalized_name", "occupation", "employer"):
            assert row[col] == DONOR_SEED[donor_id][col], (donor_id, col)
    assert _count(conn, "donors") == 5


# --- 6. missing tables tolerated --------------------------------------------

@pytest.mark.parametrize("dropped", ["isbe_receipts", "analytics_donor_summary"])
def test_missing_target_table_tolerated(tmp_path, dropped):
    # bulk_receipts_clean is absent in every _setup, so that path runs too.
    conn = _setup(tmp_path)
    conn.execute(f"DROP TABLE {dropped}")
    conn.commit()
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result.get(dropped, 0) == 0
    assert result.get("bulk_receipts_clean", 0) == 0
    for table, expected in WALTER_COUNTS.items():
        if table != dropped:
            assert result[table] == expected, table
    # The other tables' changes really landed (not undone by a swallowed error).
    assert _row(conn, "bulk_receipts_clean_legacy", "bulk_row_id", 201)["address_line_1"] == REDACTED
    assert _row(conn, "donors", "id", 301)["address"] == REDACTED


def test_missing_privacy_redactions_table_returns_empty_dict(tmp_path):
    conn = _setup(tmp_path)
    conn.execute("DROP TABLE privacy_redactions")
    conn.commit()

    assert apply_privacy_redactions(conn) == {}
    assert _row(conn, "isbe_receipts", "id", 101)["address1"] == "100 Test Ave"


# --- every request row applied; last_applied_at ------------------------------

def test_every_request_row_applied_and_stamped(tmp_path):
    conn = _setup(tmp_path)
    request_ids = [
        add_privacy_redaction(conn, **WALTER_REQUEST),
        add_privacy_redaction(
            conn, last_name="Tessaro", zip5="62704",
            requested_by="Fixture Requester LLC", request_date=date(2026, 8, 1),
        ),
        # Matches nothing; still stamped.
        add_privacy_redaction(
            conn, last_name="Quillfeather", zip5="62999",
            requested_by="Fixture Requester LLC", request_date=date(2026, 8, 2),
        ),
    ]
    conn.commit()

    result = apply_privacy_redactions(conn)

    assert result["isbe_receipts"] == 4  # 101, 102, 103 (Walter) + 108 (Tessaro)
    assert _row(conn, "isbe_receipts", "id", 108)["address1"] == REDACTED
    for request_id in request_ids:
        row = _row(conn, "privacy_redactions", "id", request_id)
        assert isinstance(row["last_applied_at"], datetime), request_id
        assert row["last_applied_at"] >= row["created_at"], request_id


# --- 7. add_privacy_redaction ------------------------------------------------

def test_add_privacy_redaction_persists_row_and_returns_id(tmp_path):
    conn = _setup(tmp_path)
    walter_id = add_privacy_redaction(conn, **WALTER_REQUEST, notes="consent requested")
    tessaro_id = add_privacy_redaction(
        conn, last_name="Tessaro", zip5="62704",
        requested_by="Other Requester", request_date=date(2026, 8, 1),
    )
    conn.commit()

    assert isinstance(walter_id, int)
    assert tessaro_id != walter_id
    walter = _row(conn, "privacy_redactions", "id", walter_id)
    assert walter["last_name"].strip().lower() == "brinkerhoff"
    assert walter["first_name"].strip().lower() == "walter"
    assert walter["zip5"] == "61820"
    assert walter["requested_by"] == "Fixture Requester LLC"
    assert walter["request_date"] == date(2026, 7, 16)
    assert walter["statute"] == "705 ILCS 90"
    assert walter["notes"] == "consent requested"
    assert isinstance(walter["created_at"], datetime)
    assert walter["last_applied_at"] is None
    tessaro = _row(conn, "privacy_redactions", "id", tessaro_id)
    assert tessaro["zip5"] == "62704"
    assert tessaro["first_name"] is None  # NULL, not '' (NULL means "any first name")


@pytest.mark.parametrize("bad_zip", ["6002", "61820-4455", "abcde"])
def test_add_privacy_redaction_rejects_malformed_zip(tmp_path, bad_zip):
    conn = _setup(tmp_path)

    with pytest.raises(ValueError):
        add_privacy_redaction(conn, **{**WALTER_REQUEST, "zip5": bad_zip})

    assert _count(conn, "privacy_redactions") == 0


@pytest.mark.parametrize("bad_last", ["", "   ", None])
def test_add_privacy_redaction_rejects_empty_last_name(tmp_path, bad_last):
    # An empty last name would match every blank-name receipt in the zip
    # (over-redaction), so it is rejected before any insert.
    conn = _setup(tmp_path)

    with pytest.raises(ValueError):
        add_privacy_redaction(conn, **{**WALTER_REQUEST, "last_name": bad_last})

    assert _count(conn, "privacy_redactions") == 0


# --- 8. refresh_dependent_views ---------------------------------------------

def test_refresh_dependent_views_noop_without_matview(tmp_path):
    conn = _setup(tmp_path)
    # Uncommitted caller work must survive a no-op call (a swallowed error on
    # this connection would roll it back).
    conn.execute(
        "INSERT INTO donors (id, name, normalized_name) VALUES (?, ?, ?)",
        (399, "Pending Probe", "pending probe"),
    )

    assert refresh_dependent_views(conn) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM donors WHERE id = ?", (399,)).fetchone()["n"] == 1


def test_refresh_dependent_views_refreshes_existing_matview(tmp_path):
    conn = _setup(tmp_path)
    _create_condensed_matview(conn)
    conn.commit()
    add_privacy_redaction(conn, **WALTER_REQUEST)
    conn.commit()
    apply_privacy_redactions(conn)
    conn.commit()
    # Snapshot still shows the address, so the next assertions measure the refresh.
    assert _row(conn, "isbe_condensed_receipts", "id", 101)["address1"] == "100 Test Ave"
    conn.commit()

    assert refresh_dependent_views(conn) is None

    row = _row(conn, "isbe_condensed_receipts", "id", 101)
    assert row["address1"] == REDACTED
    assert row["zipcode"] is None
    assert row["redaction_requested"] is True
    # Transactional mode restored: work after the call can still be rolled back.
    conn.execute(
        "INSERT INTO donors (id, name, normalized_name) VALUES (?, ?, ?)",
        (399, "Rollback Probe", "rollback probe"),
    )
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) AS n FROM donors WHERE id = ?", (399,)).fetchone()["n"] == 0


# --- 9. CLI ------------------------------------------------------------------
# The autouse pg_test_schema fixture routes the CLI's get_db to this test's
# schema. Checks read through a fresh connection, so they only pass if the
# command committed.

WALTER_CLI_ARGS = [
    "add-privacy-redaction", "--first", "WALTER ", "--last", " brinkerhoff",
    "--zip", "61820", "--requested-by", "Fixture Requester LLC",
    "--request-date", "2026-07-16",
]


def test_cli_add_no_apply_then_apply_commits(tmp_path):
    _setup(tmp_path)
    runner = CliRunner()

    added = runner.invoke(cli, [*WALTER_CLI_ARGS, "--no-apply"])

    assert added.exit_code == 0, added.output
    check = get_db()
    requests = check.execute("SELECT * FROM privacy_redactions").fetchall()
    assert len(requests) == 1
    assert f"id {requests[0]['id']}" in added.output
    assert requests[0]["zip5"] == "61820"
    assert requests[0]["request_date"] == date(2026, 7, 16)
    assert requests[0]["statute"] == "705 ILCS 90"
    assert requests[0]["last_applied_at"] is None
    assert _row(check, "isbe_receipts", "id", 101)["address1"] == "100 Test Ave"  # --no-apply
    check.close()

    applied = runner.invoke(cli, ["apply-privacy-redactions", "--skip-matview-refresh", "--skip-cache-flush"])

    assert applied.exit_code == 0, applied.output
    for table, expected in WALTER_COUNTS.items():
        assert f"{table}: {expected}" in applied.output, table
    fresh = get_db()
    for row_id in (101, 102, 103):
        row = _row(fresh, "isbe_receipts", "id", row_id)
        assert row["address1"] == REDACTED, row_id
        assert row["zipcode"] is None, row_id
        assert row["redaction_requested"] is True, row_id
    for row_id in (104, 105, 106, 107, 108):
        assert _row(fresh, "isbe_receipts", "id", row_id)["address1"] == ISBE_SEED[row_id]["address1"], row_id
    assert fresh.execute("SELECT last_applied_at FROM privacy_redactions").fetchone()["last_applied_at"] is not None


def test_cli_add_rejects_malformed_zip(tmp_path):
    _setup(tmp_path)

    result = CliRunner().invoke(cli, [
        "add-privacy-redaction", "--last", "brinkerhoff", "--zip", "6002",
        "--requested-by", "Fixture Requester LLC",
    ])

    assert result.exit_code != 0
    assert "6002" in result.stderr
    check = get_db()
    assert _count(check, "privacy_redactions") == 0
    assert _row(check, "isbe_receipts", "id", 101)["address1"] == "100 Test Ave"


def test_cli_apply_default_run_without_matview(tmp_path, monkeypatch):
    import webapp.cache_backend as cache_backend

    # Never touch a developer's real Redis from the test suite.
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _setup(tmp_path)

    result = CliRunner().invoke(cli, ["apply-privacy-redactions"])

    assert result.exit_code == 0, result.output
    assert "isbe_receipts: 0" in result.output
    assert "Route cache keys flushed: 0" in result.output
    assert "Restart ilcf-web.service to clear the in-process search cache." in result.output
