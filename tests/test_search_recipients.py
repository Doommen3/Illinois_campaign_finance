"""Recipients in /search -- the tests-first suite (plan .claude/plans/recipient-search.md, milestone M1).

A recipient is the payee of an itemized ISBE committee expenditure (decision R1). The directory is two materialized views built
from isbe_condensed_expenditures by database.recipient_directory.build_recipient_directory (R2): isbe_recipient_summary (one
row per recipient key) and isbe_recipient_committees (one row per recipient key x committee). Identity, predicate and display
spelling are the committee payee page's (R4); the section is all-time (R5), lists the top 5 paying committees with links to
/committees/<id>/payees?q=<spelling> and no scope parameters (R6), sits after Donors, type filter `recipients`, cache revision
sn4 (R7), and holds no address column (R8).

The file starts red. A test whose docstring says "(guard)" already passes on HEAD and pins behaviour the change must not break.
Product code is reached through `_rd()` inside each test, so a missing module fails each test on its own instead of breaking
collection.

The seed world (all names fictional; isbe_condensed_expenditures is a TABLE here, a matview in production; bulk_expenditures_clean
is the ETL compat view over it, as scripts/isbe_sunshine_etl.py builds it):

  committees  701 Friends of Quorvane | 702 Second Fixture Fund | 703 Third Fixture Fund | 704 Fourth Fixture Fund |
              705 Fifth Fixture Fund | 706 Sixth Fixture Fund | 707 Seventh Fixture Fund | 708 Eighth Fixture Fund |
              799 has NO isbe_committees row
  recipient                 paid by (committee: total x payments)                                   total   pays  cmtes
  Zorbleck Printing Co      701: 250+250+100 = 600 x3 (+ a 0, a -30 and an archived 999: dropped)
                            702: 500 x1 spelled "zorbleck printing co"
                            703: 200+150 "Zorbleck Printing Co" + 50 "ZORBLECK PRINTING, CO." = 400 x3
                            704: 300 x1 (part 9B) | 705: 200 x1 (6B) | 706: 100 x1 (7B)              2100      10     6
                            first 2021-10-10, last 2026-03-15; most common spelling "Zorbleck Printing Co" (8 of 10 rows)
  Plimsoll Grafting LLC     707: one live $5,000 + three archived copies of it                       5000       1     1
  Robert Fenwhistle         701: 300 + 20 (the 20 filed as "  Fenwhistle " / " Robert  ")              320       2     1
  Quibbix.com               799: 150 (first name all blanks) | 702: 25                                175       2     2
  Quorvane Strategies       702: 75                                                                     75       1     1
  Nonzorbleck Supply        703: 50 (substring decoy)                                                   50       1     1
  Tamsin                    704: 40 (first name only, last NULL)                                        40       1     1
  Glimmer Vendor 01..32     708: i * 10 each                                                          5280      32    32 pairs
  blank keys                701: "***" 99 and NULL/NULL 11 -- no letter or digit, dropped
  => 39 recipients, 45 (recipient, committee) pairs, grand total 13,040.00
  donors      Quorvane Holdings 10 (analytics_donor_summary; for the section-order test)

Readings of the contract chosen here (each one line; the orchestrator can overturn them by editing the named test):

  T1  The Recipients section renders as a bare <section> with <h2>Recipients (N) ...</h2> and a <table> with <th> headers
      Recipient / Total received / Payments / Committees / Last paid / Paid by (copied from Donors), so the names suite's
      _sections parser reads it.
  T2  The recipient name is not a link (no recipient page exists, R6); the "Paid by" cell's anchors are the committee links.
  T3  "+ N more" is matched as the text "+ 1 more" in the Paid by cell.
  T4  "Last paid" may render as YYYY-MM-DD or any format _parse_day of tests/test_committee_rollups.py reads.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import webapp.cache_backend as cache_backend
import webapp.routes.main as main_routes
from database.connection import get_db, init_db
from database.pg_compat import PostgresCompatCursor
from tests.scope_widgets_registry import WIDGETS
from tests.test_committee_rollups import _list_rows, _parse_day
from tests.test_search_names import SEARCH_KEY_PREFIX, FakeRedis, _app_config, _each, _money, _sections
from tests.test_search_names import _page as _search_html
from webapp.app import create_app
from webapp.routes.committees import _PAYEE_NAME_SQL

REPO_ROOT = Path(__file__).resolve().parents[1]
ETL_SCRIPT = REPO_ROOT / "scripts" / "isbe_sunshine_etl.py"
ONE_OFF_SCRIPT = REPO_ROOT / "scripts" / "build_recipient_directory.py"
NAME_INDEX = "idx_isbe_recipient_summary_name_tsv"


def _rd():
    """The product module; a RED failure (not a collection error) while it does not exist."""
    try:
        import database.recipient_directory as module
    except ImportError as exc:
        pytest.fail(f"not implemented yet: database.recipient_directory ({exc})", pytrace=False)
    return module


# ---------------------------------------------------------------------------
# The seed world
# ---------------------------------------------------------------------------

COMMITTEES = {
    701: "Friends of Quorvane", 702: "Second Fixture Fund", 703: "Third Fixture Fund", 704: "Fourth Fixture Fund",
    705: "Fifth Fixture Fund", 706: "Sixth Fixture Fund", 707: "Seventh Fixture Fund", 708: "Eighth Fixture Fund",
}
ZORBLECK = "Zorbleck Printing Co"

# (committee, last_name, first_name, expended_date, amount, d2_part, archived)
EXPENDITURES = [
    (701, ZORBLECK, None, "2024-01-10", 250.0, "8B", False),
    (701, ZORBLECK, None, "2025-06-01", 250.0, "8B", False),
    (701, ZORBLECK, None, "2026-03-15", 100.0, "8B", False),
    (701, ZORBLECK, None, "2026-04-01", 0.0, "8B", False),       # amount 0: dropped
    (701, ZORBLECK, None, "2026-04-02", -30.0, "8B", False),     # negative: dropped
    (701, ZORBLECK, None, "2026-04-03", 999.0, "8B", True),      # archived: dropped (would move last paid too)
    (702, "zorbleck printing co", None, "2025-02-02", 500.0, "8B", False),
    (703, ZORBLECK, None, "2023-05-05", 200.0, "8B", False),
    (703, ZORBLECK, None, "2023-06-06", 150.0, "8B", False),
    (703, "ZORBLECK PRINTING, CO.", None, "2023-07-07", 50.0, "8B", False),
    (704, ZORBLECK, None, "2022-08-08", 300.0, "9B", False),     # independent expenditure
    (705, ZORBLECK, None, "2022-09-09", 200.0, "6B", False),     # transfer out
    (706, ZORBLECK, None, "2021-10-10", 100.0, "7B", False),     # loan made
    (707, "Plimsoll Grafting LLC", None, "2025-11-11", 5000.0, "8B", False),
    (707, "Plimsoll Grafting LLC", None, "2025-11-11", 5000.0, "8B", True),
    (707, "Plimsoll Grafting LLC", None, "2025-11-11", 5000.0, "8B", True),
    (707, "Plimsoll Grafting LLC", None, "2025-11-11", 5000.0, "8B", True),
    (701, "Fenwhistle", "Robert", "2025-03-03", 300.0, "8B", False),
    (701, "  Fenwhistle ", " Robert  ", "2025-04-04", 20.0, "8B", False),
    (799, "Quibbix.com", "   ", "2024-12-12", 150.0, "8B", False),
    (702, "Quibbix.com", None, "2024-11-11", 25.0, "8B", False),
    (702, "Quorvane Strategies", None, "2025-05-05", 75.0, "8B", False),
    (703, "Nonzorbleck Supply", None, "2024-02-02", 50.0, "8B", False),
    (704, None, "Tamsin", "2024-03-03", 40.0, "8B", False),
    (701, "***", None, "2025-01-01", 99.0, "8B", False),         # blank key: dropped
    (701, None, None, "2025-01-02", 11.0, "8B", False),          # blank key: dropped
] + [(708, f"Glimmer Vendor {i:02d}", None, "2026-02-02", i * 10.0, "8B", False) for i in range(1, 33)]

RECIPIENT_COUNT = 39
PAIR_COUNT = 45
GRAND_TOTAL = 13040.0

SUMMARY_COLUMNS = ["recipient_key", "recipient_name", "total_amount", "payment_count", "committee_count", "first_date", "last_date"]
PAIR_COLUMNS = ["recipient_key", "committee_id", "recipient_name", "total_amount", "payment_count", "first_date", "last_date"]


def _seed_world(conn) -> None:
    conn.execute(
        """
        CREATE TABLE isbe_committees (
            id INTEGER PRIMARY KEY, type TEXT, state_committee BOOLEAN, local_committee BOOLEAN, refer_name TEXT,
            name TEXT, address1 TEXT, address2 TEXT, address3 TEXT, city TEXT, state TEXT, zipcode TEXT,
            active BOOLEAN DEFAULT TRUE, status_date DATE, creation_date DATE, creation_amount DOUBLE PRECISION,
            party TEXT, purpose TEXT
        )
        """
    )
    for committee_id, name in COMMITTEES.items():
        conn.execute(
            "INSERT INTO isbe_committees (id, type, name, city, state) VALUES (?, 'Political Action', ?, 'Peoria', 'IL')",
            (committee_id, name),
        )
    conn.execute(
        """
        CREATE TABLE isbe_condensed_expenditures (
            id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, etrans_id TEXT, last_name TEXT,
            first_name TEXT, expended_date DATE, amount DOUBLE PRECISION, aggregate_amount DOUBLE PRECISION,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, d2_part TEXT, purpose TEXT,
            candidate_name TEXT, office TEXT, supporting BOOLEAN DEFAULT FALSE, opposing BOOLEAN DEFAULT FALSE,
            archived BOOLEAN DEFAULT FALSE, country TEXT, redaction_requested BOOLEAN DEFAULT FALSE
        )
        """
    )
    for row_id, (committee, last, first, day, amount, part, archived) in enumerate(EXPENDITURES, start=80001):
        conn.execute(
            "INSERT INTO isbe_condensed_expenditures (id, committee_id, last_name, first_name, expended_date, amount, d2_part, "
            "archived, address1, city, state, zipcode, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '1 Fixture Way', 'Peoria', 'IL', "
            "'61602', 'fixture')",
            (row_id, committee, last, first, day, amount, part, archived),
        )
    # The compat view the committee payee page reads (scripts/isbe_sunshine_etl.py, isbe_bulk_expenditures_clean_compat).
    conn.execute(
        """
        CREATE VIEW bulk_expenditures_clean AS
        SELECT e.id AS bulk_row_id, e.id AS expenditure_record_id, e.committee_id AS committee_id_sbe, e.filed_doc_id,
               e.etrans_id AS electronic_transaction_id, e.last_name AS payee_last_or_business_name,
               e.first_name AS payee_first_name, e.expended_date::text AS expended_date, e.amount, e.aggregate_amount,
               e.address1 AS address_line_1, e.address2 AS address_line_2, e.city, e.state, e.zipcode AS postal_code,
               e.d2_part AS d2_part_code, e.purpose, e.candidate_name, e.office,
               CASE WHEN e.supporting THEN 1 ELSE 0 END AS is_supporting,
               CASE WHEN e.opposing THEN 1 ELSE 0 END AS is_opposing,
               CASE WHEN e.archived THEN 1 ELSE 0 END AS is_archived, e.country,
               CASE WHEN e.redaction_requested THEN 1 ELSE 0 END AS redaction_requested,
               0::integer AS is_amount_anomalous, NULL::text AS anomaly_reason, NULL::text AS source_file,
               NULL::bigint AS source_row_number
        FROM isbe_condensed_expenditures e
        """
    )
    conn.execute(
        "INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_address, donor_city, donor_state, "
        "total_amount, contribution_count, committee_count) VALUES ('bulk_receipts', 'quorvane holdings||1 oak st|peoria|il|61602', "
        "'Quorvane Holdings', '1 Oak St, Peoria, IL 61602', 'Peoria', 'IL', 10.0, 1, 1)"
    )
    conn.commit()


def _build_world(tmp_path: Path, directory: bool = True) -> str:
    """A fresh test schema with the seed world; the directory built through the compat connection unless directory=False."""
    db_path = str(tmp_path / "search_recipients.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_world(conn)
    if directory:
        _rd().build_recipient_directory(conn)
        conn.commit()
    conn.close()
    return db_path


def _client(tmp_path: Path, directory: bool = True, **config):
    return create_app(_app_config(_build_world(tmp_path, directory), **config)).test_client()


@pytest.fixture(autouse=True)
def _isolated_search_cache(monkeypatch):
    """Never touch a developer's Redis; start and end every test with an empty search cache."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    main_routes._search_results_cache.invalidate()
    yield
    main_routes._search_results_cache.invalidate()


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------

def _recipients(client, query: str, **params) -> list:
    """Rows of the Recipients section ([] when the section is absent); the heading count is checked by _sections."""
    return _sections(_search_html(client, query, **params)).get("Recipients", [])


def _names(rows) -> list[str]:
    return [row.cell("Recipient") for row in rows]


def _totals(rows) -> list[float]:
    return [_money(row.cell("Total received")) for row in rows]


def _committee_links(row) -> list[tuple[int, str, dict]]:
    """(committee id, link text, parsed query) of every visible /committees/<id>/payees anchor of the row, in order."""
    found = []
    for href, text, inside_details in row.anchors:
        match = re.fullmatch(r"/committees/(\d+)/payees", urlsplit(href).path)
        if match and not inside_details:
            found.append((int(match.group(1)), text, parse_qs(urlsplit(href).query)))
    return found


# ---------------------------------------------------------------------------
# (1) Directory invariants
# ---------------------------------------------------------------------------

def test_build_returns_the_two_counts_and_can_run_again(tmp_path):
    """build_recipient_directory drops and recreates both views: a second run on the same database neither fails ("already
    exists") nor doubles anything, and both runs return {"recipients": 39, "recipient_committees": 45}. Kills: CREATE without the
    DROP; counts of the source rows instead of the directory rows; a summary keyed by (name, committee)."""
    rd = _rd()
    db_path = str(tmp_path / "rebuild.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_world(conn)
    first = rd.build_recipient_directory(conn)
    conn.commit()
    second = rd.build_recipient_directory(conn)
    conn.commit()
    expected = {"recipients": RECIPIENT_COUNT, "recipient_committees": PAIR_COUNT}
    assert first == expected
    assert second == expected
    assert conn.execute("SELECT COUNT(*) AS n FROM isbe_recipient_summary").fetchone()["n"] == RECIPIENT_COUNT
    conn.close()


def test_summary_and_pair_totals_reconcile_with_the_source_predicate(tmp_path):
    """Grand totals: summary = pairs = the source predicate (not archived, amount > 0, a name with a letter or digit, every D-2
    part) = 13,040.00 by hand. Per recipient: total, payments and first/last date of the summary row equal the sums / extremes of
    its pair rows, and committee_count equals the number of pair rows. Kills: a part-8B-only filter (drops 900 of Zorbleck's);
    a missing archived / amount filter; blank-key groups kept; a summary that counts committees from another grouping."""
    rd = _rd()
    db_path = _build_world(tmp_path)
    conn = get_db(db_path)
    source = conn.execute(
        "SELECT SUM(amount) AS total FROM isbe_condensed_expenditures WHERE archived = FALSE AND COALESCE(amount, 0) > 0 "
        "AND (COALESCE(first_name, '') || COALESCE(last_name, '')) ~ '[[:alnum:]]'"
    ).fetchone()["total"]
    summary = conn.execute(f"SELECT SUM(total_amount) AS total FROM {rd.RECIPIENT_SUMMARY}").fetchone()["total"]
    pairs = conn.execute(f"SELECT SUM(total_amount) AS total FROM {rd.RECIPIENT_COMMITTEES}").fetchone()["total"]
    assert round(float(source), 2) == GRAND_TOTAL
    assert round(float(summary), 2) == GRAND_TOTAL
    assert round(float(pairs), 2) == GRAND_TOTAL

    mismatches = conn.execute(
        f"""
        SELECT s.recipient_key FROM {rd.RECIPIENT_SUMMARY} s
        JOIN (SELECT recipient_key, SUM(total_amount) AS total, SUM(payment_count) AS payments, COUNT(*) AS committees,
                     MIN(first_date) AS first_date, MAX(last_date) AS last_date
              FROM {rd.RECIPIENT_COMMITTEES} GROUP BY recipient_key) p USING (recipient_key)
        WHERE ROUND(s.total_amount::numeric, 2) <> ROUND(p.total::numeric, 2) OR s.payment_count <> p.payments
           OR s.committee_count <> p.committees OR s.first_date <> p.first_date OR s.last_date <> p.last_date
        """
    ).fetchall()
    assert [row["recipient_key"] for row in mismatches] == []
    conn.close()


def test_an_archived_twin_is_not_counted(tmp_path):
    """CLAUDE.md's Archived rule on its LIUNA example shape: three archived copies and one live row of one $5,000 payment are
    $5,000 and ONE payment, in the summary and in the pair. Kills a directory that ignores `archived` ($20,000, 4 payments)."""
    rd = _rd()
    conn = get_db(_build_world(tmp_path))
    summary = conn.execute(
        f"SELECT total_amount, payment_count, committee_count FROM {rd.RECIPIENT_SUMMARY} WHERE recipient_key = 'plimsoll grafting llc'"
    ).fetchone()
    pair = conn.execute(
        f"SELECT committee_id, total_amount, payment_count FROM {rd.RECIPIENT_COMMITTEES} "
        "WHERE recipient_key = 'plimsoll grafting llc'"
    ).fetchall()
    conn.close()
    assert (float(summary["total_amount"]), summary["payment_count"], summary["committee_count"]) == (5000.0, 1, 1)
    assert [(row["committee_id"], float(row["total_amount"]), row["payment_count"]) for row in pair] == [(707, 5000.0, 1)]


def test_spellings_that_differ_in_case_or_punctuation_are_one_recipient_shown_by_its_most_common_spelling(tmp_path):
    """"Zorbleck Printing Co" (8 rows), "zorbleck printing co" (1) and "ZORBLECK PRINTING, CO." (1) share the key
    'zorbleck printing co': ONE summary row named "Zorbleck Printing Co" with 2,100.00 over 10 payments and 6 committees, first
    2021-10-10, last 2026-03-15. Inside a pair the pair's own most common spelling is kept (702 -> the lower-case one, the name
    committee 702's payee page shows). Kills: grouping on the raw name; MIN/MAX instead of MODE for the display name; a pair name
    taken from the summary."""
    rd = _rd()
    conn = get_db(_build_world(tmp_path))
    rows = conn.execute(
        f"SELECT recipient_key, recipient_name, total_amount, payment_count, committee_count, first_date, last_date "
        f"FROM {rd.RECIPIENT_SUMMARY} WHERE recipient_key LIKE '%zorbleck%' ORDER BY recipient_key"
    ).fetchall()
    pairs = conn.execute(
        f"SELECT committee_id, recipient_name, total_amount, payment_count FROM {rd.RECIPIENT_COMMITTEES} "
        "WHERE recipient_key = 'zorbleck printing co' ORDER BY committee_id"
    ).fetchall()
    conn.close()
    assert [row["recipient_key"] for row in rows] == ["nonzorbleck supply", "zorbleck printing co"]
    row = rows[1]
    assert row["recipient_name"] == ZORBLECK
    assert (float(row["total_amount"]), row["payment_count"], row["committee_count"]) == (2100.0, 10, 6)
    assert (str(row["first_date"])[:10], str(row["last_date"])[:10]) == ("2021-10-10", "2026-03-15")
    assert [(p["committee_id"], p["recipient_name"], float(p["total_amount"]), p["payment_count"]) for p in pairs] == [
        (701, ZORBLECK, 600.0, 3), (702, "zorbleck printing co", 500.0, 1), (703, ZORBLECK, 400.0, 3),
        (704, ZORBLECK, 300.0, 1), (705, ZORBLECK, 200.0, 1), (706, ZORBLECK, 100.0, 1),
    ]


def test_the_two_relations_are_materialized_views_with_the_pinned_columns_and_no_address(tmp_path):
    """The contract's column lists, in order, and relkind 'm'. R8: no address, city, state or zip column, so a privacy removal
    request (which scrubs addresses) needs no new scrub step. Kills: plain tables (not rebuilt by a refresh); extra columns
    carrying where a recipient lives."""
    rd = _rd()
    conn = get_db(_build_world(tmp_path))
    found = {}
    for relation in (rd.RECIPIENT_SUMMARY, rd.RECIPIENT_COMMITTEES):
        kind = conn.execute("SELECT relkind FROM pg_class WHERE oid = to_regclass(?)", (relation,)).fetchone()
        columns = conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = to_regclass(?) AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
            (relation,),
        ).fetchall()
        found[relation] = (kind["relkind"] if kind else None, [row["attname"] for row in columns])
    conn.close()
    assert (rd.RECIPIENT_SUMMARY, rd.RECIPIENT_COMMITTEES) == ("isbe_recipient_summary", "isbe_recipient_committees")
    assert found["isbe_recipient_summary"] == ("m", SUMMARY_COLUMNS)
    assert found["isbe_recipient_committees"] == ("m", PAIR_COLUMNS)
    for _kind, columns in found.values():
        assert not [c for c in columns if re.search(r"address|city|state|zip", c)], columns


# ---------------------------------------------------------------------------
# (2) recipient_name_sql / recipient_key_sql are the committee page's name and key
# ---------------------------------------------------------------------------

def test_name_and_key_sql_equal_the_committee_payee_pages_on_the_same_rows(tmp_path):
    """On every seed row: recipient_name_sql('e') over isbe_condensed_expenditures equals committees._PAYEE_NAME_SQL over the
    compat view, and recipient_key_sql(name) equals the payee page's payee_key formula (webapp/routes/committees.py
    _payee_rollup_source). Spot values: whitespace-only first name -> "Quibbix.com" / 'quibbix com'; first name only ->
    "Tamsin"; padded halves -> "Robert Fenwhistle"; "ZORBLECK PRINTING, CO." -> 'zorbleck printing co'. Kills: a plain
    first || ' ' || last (NULL-propagating, double spaces); a key that keeps punctuation or case."""
    rd = _rd()
    db_path = str(tmp_path / "name_key.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_world(conn)
    name_sql = rd.recipient_name_sql("e")
    ours = {
        row["id"]: (row["name"], row["key"])
        for row in conn.execute(
            f"SELECT e.id, {name_sql} AS name, {rd.recipient_key_sql(name_sql)} AS key FROM isbe_condensed_expenditures e"
        ).fetchall()
    }
    page_key = f"TRIM(REGEXP_REPLACE(LOWER({_PAYEE_NAME_SQL}), '[^[:alnum:]]+', ' ', 'g'))"
    theirs = {
        row["id"]: (row["name"], row["key"])
        for row in conn.execute(
            f"SELECT e.bulk_row_id AS id, {_PAYEE_NAME_SQL} AS name, {page_key} AS key FROM bulk_expenditures_clean e"
        ).fetchall()
    }
    conn.close()
    assert len(ours) == len(EXPENDITURES)
    assert ours == theirs
    values = set(ours.values())
    for expected in (("Quibbix.com", "quibbix com"), ("Tamsin", "tamsin"), ("Robert Fenwhistle", "robert fenwhistle"),
                     ("ZORBLECK PRINTING, CO.", "zorbleck printing co"), ("***", "")):
        assert expected in values, expected


# ---------------------------------------------------------------------------
# (3) /search matching, order, limit
# ---------------------------------------------------------------------------

def test_tokens_match_in_any_order_by_prefix_across_punctuation_and_by_nickname(tmp_path):
    """The names suite's token rules on the directory: words in any order, a word prefix, "quibbix com" for "Quibbix.com"
    (punctuation is a separator) and Bob -> Robert. Each answers with exactly the one recipient. Kills: a raw ILIKE on the whole
    query; a tsvector without the contract normalization ("Quibbix.com" one token); no nickname expansion."""
    client = _client(tmp_path)

    def check(case):
        query, expected = case
        assert _names(_recipients(client, query, type="recipients")) == expected

    _each(
        [
            ("Zorbleck Printing", [ZORBLECK]),
            ("printing zorbleck", [ZORBLECK]),
            ("Zorbl", [ZORBLECK]),
            ("quibbix com", ["Quibbix.com"]),
            ("Quibbix.com", ["Quibbix.com"]),
            ("Bob Fenwhistle", ["Robert Fenwhistle"]),
            ("Fenwhistle, Robert", ["Robert Fenwhistle"]),
        ],
        check,
    )


def test_the_substring_fallback_runs_only_when_the_tokens_find_nothing_and_the_query_has_four_characters(tmp_path):
    """Reading R2 of the names suite. "Zorbleck" has a token hit, so the decoy "Nonzorbleck Supply" is NOT listed; "orblec"
    starts no token, so ONE substring fallback lists both, by total (2,100 then 50); "orb" is under the 4-character floor and
    lists no Recipients section. Kills: token OR substring in one predicate; no fallback; a fallback without the floor."""
    client = _client(tmp_path)
    assert _names(_recipients(client, "Zorbleck", type="recipients")) == [ZORBLECK]
    fallback = _recipients(client, "orblec", type="recipients")
    assert _names(fallback) == [ZORBLECK, "Nonzorbleck Supply"]
    assert _totals(fallback) == [2100.0, 50.0]
    assert _recipients(client, "orb", type="recipients") == []


def test_rows_are_ordered_by_total_received_and_limited_to_30_or_20_for_a_short_query(tmp_path):
    """32 "Glimmer Vendor NN" recipients receive NN x $10. "glimmer" lists 30 rows, $320 down to $30 (vendors 32..03);
    "gli" (under 4 characters) lists 20, $320 down to $130. Kills: name order; no limit; one limit for both query lengths."""
    client = _client(tmp_path)
    long_rows = _recipients(client, "glimmer", type="recipients")
    assert _totals(long_rows) == [i * 10.0 for i in range(32, 2, -1)]
    assert _names(long_rows) == [f"Glimmer Vendor {i:02d}" for i in range(32, 2, -1)]
    short_rows = _recipients(client, "gli", type="recipients")
    assert _totals(short_rows) == [i * 10.0 for i in range(32, 12, -1)]


# ---------------------------------------------------------------------------
# (4) The type filter and the dropdown
# ---------------------------------------------------------------------------

def test_type_recipients_shows_only_that_section_and_type_all_puts_it_after_donors(tmp_path):
    """"Quorvane" matches committee 701, the donor "Quorvane Holdings" and the recipient "Quorvane Strategies". type=recipients
    lists only Recipients; type=all lists all three, Recipients right after Donors (R7). Kills: an unknown type falling back to
    'all' (today); the section rendered for every type; the section placed elsewhere."""
    client = _client(tmp_path)
    only = _sections(_search_html(client, "Quorvane", type="recipients"))
    assert list(only) == ["Recipients"]
    assert _names(only["Recipients"]) == ["Quorvane Strategies"]

    everything = list(_sections(_search_html(client, "Quorvane")))
    assert {"Committees", "Donors", "Recipients"} <= set(everything), everything
    assert everything.index("Recipients") == everything.index("Donors") + 1, everything


def test_the_type_dropdown_offers_recipients_after_donors_and_keeps_it_selected(tmp_path):
    """The search form's <select name="type"> has <option value="recipients">Recipients</option> right after Donors, selected
    when the page was asked for type=recipients. Kills: a route that accepts the type with no way to pick it."""
    client = _client(tmp_path)
    html = _search_html(client, "Zorbleck", type="recipients")
    select = re.search(r'<select name="type">(.*?)</select>', html, re.S)
    assert select, "no type dropdown"
    options = re.findall(r'<option value="([^"]+)"([^>]*)>\s*([^<]*?)\s*</option>', select.group(1))
    values = [value for value, _attrs, _label in options]
    assert ("recipients", "Recipients") in [(value, label) for value, _attrs, label in options], options
    assert values.index("recipients") == values.index("donors") + 1, values
    assert [value for value, attrs, _label in options if "selected" in attrs] == ["recipients"]


def test_a_query_that_matches_only_a_recipient_does_not_say_no_results(tmp_path):
    """"Plimsoll" matches only the recipient Plimsoll Grafting LLC: the page lists the Recipients section and does NOT also say
    "No results found". Kills a no-results condition that ignores the recipients section."""
    client = _client(tmp_path)
    html = _search_html(client, "Plimsoll")
    assert list(_sections(html)) == ["Recipients"]
    assert "No results found" not in html


def test_a_query_that_matches_nothing_still_says_no_results(tmp_path):
    """(guard) Passes on HEAD. "Xylquonder" matches nothing anywhere: no section and the "No results found" message. Kills a fix
    that drops the message altogether."""
    client = _client(tmp_path)
    html = _search_html(client, "Xylquonder")
    assert _sections(html) == {}
    assert "No results found" in html


# ---------------------------------------------------------------------------
# (5) A row: figures, its committees and the link to the payee page
# ---------------------------------------------------------------------------

def test_a_row_shows_its_figures_and_its_top_five_committees_then_n_more(tmp_path):
    """Zorbleck Printing Co: total $2,100.00, 10 payments, 6 committees, last paid 2026-03-15; Paid by lists 701..705 by amount
    (600, 500, 400, 300, 200) with the committee names, then "+ 1 more" (706). Each link is /committees/<id>/payees with q = that
    committee's own spelling (702: the lower-case one) and NO scope parameter (R6). Kills: committees in id or name order; more
    or fewer than five; a q from the summary spelling; links that carry the page window (the payee page would show a windowed
    total)."""
    client = _client(tmp_path)
    rows = _recipients(client, "Zorbleck", type="recipients")
    assert _names(rows) == [ZORBLECK]
    row = rows[0]
    assert _money(row.cell("Total received")) == 2100.0
    assert row.cell("Payments") == "10"
    assert row.cell("Committees") == "6"
    assert str(_parse_day(row.cell("Last paid")) or row.cell("Last paid")) == "2026-03-15"

    links = _committee_links(row)
    assert [(cid, text) for cid, text, _q in links] == [(cid, COMMITTEES[cid]) for cid in (701, 702, 703, 704, 705)]
    assert [q for _cid, _text, q in links] == [
        {"q": [ZORBLECK]}, {"q": ["zorbleck printing co"]}, {"q": [ZORBLECK]}, {"q": [ZORBLECK]}, {"q": [ZORBLECK]},
    ]
    paid_by = row.cell("Paid by")
    amounts = [float(value.replace(",", "")) for value in re.findall(r"\$([\d,]+\.\d{2})", paid_by)]
    assert amounts == [600.0, 500.0, 400.0, 300.0, 200.0], paid_by
    assert "+ 1 more" in paid_by, paid_by
    assert COMMITTEES[706] not in row.visible_text


def test_a_committee_without_an_isbe_committees_row_is_labelled_by_its_id(tmp_path):
    """Quibbix.com was paid $150 by committee 799, which has no isbe_committees row, and $25 by 702: both are listed, 799 first
    as "Committee 799", and no "more" text (2 committees, 2 listed). Kills: an inner join that drops the payer; an empty label."""
    client = _client(tmp_path)
    rows = _recipients(client, "Quibbix", type="recipients")
    assert _names(rows) == ["Quibbix.com"]
    assert [(cid, text) for cid, text, _q in _committee_links(rows[0])] == [(799, "Committee 799"), (702, COMMITTEES[702])]
    assert "more" not in rows[0].cell("Paid by")


def test_each_committee_link_opens_the_payee_page_listing_the_recipient_with_the_same_total(tmp_path):
    """Follow every Paid-by link of Zorbleck Printing Co: the committee's all-time payee page (no scope parameters) answers 200
    and lists a row with that exact name and the amount the search row showed for it (600 / 500 / 400 / 300 / 200). This is the
    reconciliation R6 promises. Kills: a pair total that disagrees with the payee page (another predicate, another key, a
    windowed link); a q the page cannot find."""
    client = _client(tmp_path)
    row = _recipients(client, "Zorbleck", type="recipients")[0]
    links = [href for href, _text, inside in row.anchors if not inside and urlsplit(href).path.endswith("/payees")]
    amounts = [float(value.replace(",", "")) for value in re.findall(r"\$([\d,]+\.\d{2})", row.cell("Paid by"))]
    assert len(links) == 5 and len(amounts) == 5, (links, amounts)

    def check(case):
        href, amount = case
        response = client.get(href)
        assert response.status_code == 200, (href, response.status_code)
        q = parse_qs(urlsplit(href).query)["q"][0]
        listed = [r for r in _list_rows(response.get_data(as_text=True)) if r.name == q]
        assert len(listed) == 1, (href, [r.name for r in _list_rows(response.get_data(as_text=True))])
        assert f"${amount:,.2f}" in listed[0].cells, (href, listed[0].cells)

    _each(list(zip(links, amounts)), check)


# ---------------------------------------------------------------------------
# (6) The fixed "All time" tag and the registry row
# ---------------------------------------------------------------------------

def test_the_heading_carries_only_the_fixed_all_time_tag_under_a_non_default_scope(tmp_path):
    """R5: under year=2024 and cycle=2022 the heading's only scope tag is the FIXED "All time" one and the totals are still
    all-time ($2,100.00 for Zorbleck, whose 2024 payments are $250). Kills: a windowed tag; a windowed total."""
    client = _client(tmp_path)

    def check(scope):
        html = _search_html(client, "Zorbleck", type="recipients", **scope)
        heading = re.search(r"<h2>\s*Recipients \(\d+\)(.*?)</h2>", html, re.S)
        assert heading, "no Recipients heading"
        tags = re.findall(r'<span class="(scope-tag[^"]*)">(.*?)</span>', heading.group(1), re.S)
        assert [(cls, text.strip()) for cls, text in tags] == [("scope-tag scope-tag-fixed", "All time")], tags
        assert _totals(_sections(html)["Recipients"]) == [2100.0]

    _each([{"year": "2024"}, {"cycle": "2022"}], check)


def test_the_scope_widget_registry_has_the_recipients_row():
    """tests/scope_widgets_registry.py lists the section as main.search / "Recipients (N)" / table / fixed / none, and its
    covered_by names a test of this file that exists. Kills: an unregistered section; a covered_by pointing nowhere."""
    rows = [w for w in WIDGETS if w.endpoint == "main.search" and w.heading == "Recipients (N)"]
    assert len(rows) == 1, "no registry row for the Recipients section"
    widget = rows[0]
    assert (widget.url, widget.template, widget.kind, widget.status, widget.axis) == (
        "/search", "search.html", "table", "fixed", "none",
    )
    named = re.findall(r"test_\w+", widget.covered_by or "")
    assert named, widget.covered_by
    assert all(callable(globals().get(name)) for name in named), named


# ---------------------------------------------------------------------------
# (7) The search_results cache
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: fake)
    return fake


def test_a_second_identical_request_is_a_cache_hit_that_still_lists_the_recipients(tmp_path, fake_redis, monkeypatch):
    """Cache on: the first request stores ONE entry whose route key starts with the revision sn4 and whose payload carries the
    recipients. Then Zorbleck gets another $1,000 and the directory is rebuilt: the second identical request still shows
    $2,100.00 (served from the cache, Recipients included), and after the cache is emptied the page shows $3,100.00 (control:
    the rebuild really changed the answer). Kills: the section left out of the cached payload or of the cache-hit copy loop
    (the second page would have no Recipients); the old revision (entries computed without the section would be served)."""
    client = _client(tmp_path, ROUTE_PERF_CACHE_ENABLED=True)
    # RouteCache stores under a sha1 of the route's key, so the route's key is read where it is handed to the cache.
    cache = main_routes._search_results_cache
    route_keys: list = []
    original_set = cache.set
    monkeypatch.setattr(cache, "set", lambda key, *args, **kwargs: (route_keys.append(key), original_set(key, *args, **kwargs))[1])
    assert _totals(_recipients(client, "Zorbleck")) == [2100.0]
    stored = [key for key in fake_redis.values if key.startswith(SEARCH_KEY_PREFIX)]
    assert len(stored) == 1, stored
    assert len(route_keys) == 1 and str(route_keys[0]).startswith("sn4:"), route_keys
    assert stored[0] == cache._redis_key(route_keys[0])
    payload = fake_redis.values[stored[0]]
    payload = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
    assert '"recipients"' in payload and ZORBLECK in payload, payload

    conn = get_db()
    conn.execute(
        "INSERT INTO isbe_condensed_expenditures (id, committee_id, last_name, expended_date, amount, d2_part, archived) "
        "VALUES (89999, 701, ?, '2026-05-05', 1000.0, '8B', FALSE)",
        (ZORBLECK,),
    )
    _rd().build_recipient_directory(conn)
    conn.commit()
    conn.close()

    assert _totals(_recipients(client, "Zorbleck")) == [2100.0], "recomputed or section dropped: the cache did not serve it"
    main_routes._search_results_cache.invalidate()
    assert _totals(_recipients(client, "Zorbleck")) == [3100.0]


def test_the_search_cache_key_revision_is_sn4():
    """R7: cached payloads gain a section, so every sn3 entry must become unreachable. Kills a forgotten bump."""
    assert main_routes.SEARCH_CACHE_REVISION == "sn4"
    assert main_routes._search_results_cache_key("cycle:2026", "Zorbleck", "recipients").startswith("sn4:")


# ---------------------------------------------------------------------------
# (8) Directory absent
# ---------------------------------------------------------------------------

def test_without_the_directory_the_page_renders_with_no_recipients_and_no_errored_section(tmp_path):
    """A database where the views were never built (fresh DB, prod before the first build): /search?q=Zorbleck and
    type=recipients answer 200, list no Recipients section and show no "Some sections failed" banner (_search_html asserts it);
    type=recipients still honours the filter and lists NO other section even though "Quorvane" matches a committee. Kills: a
    section that queries a missing relation (errored banner); type=recipients treated as unknown (falls back to all)."""
    client = _client(tmp_path, directory=False)
    assert "Recipients" not in _sections(_search_html(client, "Zorbleck"))
    assert _sections(_search_html(client, "Zorbleck", type="recipients")) == {}
    assert _sections(_search_html(client, "Quorvane", type="recipients")) == {}


# ---------------------------------------------------------------------------
# (9) ETL hook and the index the route's SQL uses
# ---------------------------------------------------------------------------

def test_the_etl_builds_the_directory_after_the_condensed_views_and_the_one_off_script_calls_it():
    """Pattern test on scripts/isbe_sunshine_etl.py: create_materialized_views calls build_recipient_directory( after it runs
    MATVIEW_DDL (which creates isbe_condensed_expenditures), in code, not in a comment; scripts/build_recipient_directory.py
    exists and calls it too. Kills: a directory that goes stale after every nightly import; a build before its source exists."""
    tree = ast.parse(ETL_SCRIPT.read_text(encoding="utf-8"))
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "create_materialized_views"), None
    )
    assert function is not None, "create_materialized_views is gone"
    calls = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "build_recipient_directory"
    ]
    ddl_uses = [node for node in ast.walk(function) if isinstance(node, ast.Name) and node.id == "MATVIEW_DDL"]
    assert calls, "create_materialized_views does not call build_recipient_directory"
    assert ddl_uses and min(n.lineno for n in ddl_uses) < min(c.lineno for c in calls), "the build runs before MATVIEW_DDL"

    assert ONE_OFF_SCRIPT.exists(), "scripts/build_recipient_directory.py is missing"
    script = ast.parse(ONE_OFF_SCRIPT.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "build_recipient_directory"
        for node in ast.walk(script)
    ), "the one-off script does not call build_recipient_directory"


@pytest.fixture
def sql_log(monkeypatch):
    """(sql, params) of every statement the compat layer runs while the test runs."""
    log: list[tuple] = []
    original = PostgresCompatCursor.execute

    def recording(self, sql, params=None):
        log.append((sql, params))
        return original(self, sql, params)

    monkeypatch.setattr(PostgresCompatCursor, "execute", recording)
    return log


def test_the_routes_token_statement_is_served_by_the_name_index(tmp_path, sql_log):
    """The plan test of the names suite, on the directory: /search?type=recipients is requested, every recorded statement that
    applies `@@` to isbe_recipient_summary is EXPLAINed with sequential scans off, and at least one plan names
    idx_isbe_recipient_summary_name_tsv. Kills: a predicate spelled differently from the indexed expression (the planner ignores
    the index: a seq scan of 365 K rows per search); the fallback OR-ed into the token predicate; the index missing."""
    client = _client(tmp_path)
    sql_log.clear()
    _search_html(client, "Zorbleck Printing", type="recipients")
    statements = [(sql, params) for sql, params in list(sql_log) if "@@" in sql and "isbe_recipient_summary" in sql]
    assert statements, "/search ran no statement that applies @@ to isbe_recipient_summary"

    conn = get_db()
    conn.execute("SET enable_seqscan = off")
    plans = []
    for sql, params in statements:
        rows = conn.execute("EXPLAIN " + sql, params if params is not None else ()).fetchall()
        plans.append(" ".join(str(value) for row in rows for value in row.values()))
    conn.rollback()
    conn.close()
    assert any(NAME_INDEX in plan for plan in plans), f"no plan uses {NAME_INDEX}:\n" + "\n---\n".join(plans)
