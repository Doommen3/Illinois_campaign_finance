"""S-C8 (tests first) -- committee families: schema, seeder, /admin review flow, public display.

Plan: .claude/plans/profiles-races-dated-widgets.md 6.2 (S-C8) and decision J-S9 (6.3).
Design: docs/committee_families_design.md (schema, evidence rule, admin flow, display rules).
Principle: group, never merge. Committee ids, pages and per-committee totals never change; a family
is a reviewable grouping and only ACCEPTED families show up on public pages.

Everything here is RED until S-C8 lands. Contract pinned (PROPOSED where the design is silent):

* Schema: ``committee_family`` / ``committee_family_member`` exactly as in the design's DDL.
* ``database/committee_families.py`` (new; imported lazily so every test fails on its own):
    seed_families(conn) -> dict   keys pairs / families_created / families_existing (extra keys allowed;
                                  pairs = strong pairs found, the same number on every run)
    weak_pairs(conn) -> list[dict]  keys c1, c2 (committee ids, c1 < c2) and sim (float); read-only
  ``conn`` is what ``database.connection.get_db()`` returns. Like database/privacy_redactions.py the
  functions do not commit; the caller does (these tests commit, then read through a fresh connection).
  The seeder may read ONLY isbe_committees, isbe_candidate_committees and isbe_officers: the fixtures
  deliberately have no isbe_committee_money / isbe_condensed_* / isbe_most_recent_filings.
* Evidence rule: a pair is linked when it shares a candidate AND (identical normalized name OR trigram
  similarity >= 0.8 OR the same normalized first+last officer name OR the same non-blank normalized
  street address in the same zip). Linked pairs are unioned (connected components).
  NOTE vs scripts/committee_families.py: its address formula appends the zip BEFORE testing blankness,
  so two committees with no street address in one zip count as "same address". D7 below pins that as a
  false positive (a zip alone is not an address).
* Family row: label = as-filed name of the most recently active member; evidence flags same_name /
  similar_name (number) / shared_officer / same_address plus shared_candidates (all shared candidate ids).
* Admin: /admin/committee-families[?status=pending|accepted|rejected] (default pending), /<id>,
  POST /<id>/decision with decision accepted | rejected | remove_member (+ committee_id), all behind
  login_required, flash + redirect like donor merges. Evidence badge text = the evidence key with
  underscores as spaces ("same address"); the active flag reads "Active"/"Inactive".
* Display (accepted families only): committee profile banner "<label> ... N committees, M active" with
  links to sibling /committees/<id>; ``/committees/?by=family`` collapses accepted members into one row
  (label, "N committees|members", summed WINDOWED total); candidate profile groups them under an
  h3/h4/h5 sub-heading carrying the label.

pg_trgm: the tests that need it are marked ``integration`` (CI skips them). Every seeder world still tries
``CREATE EXTENSION IF NOT EXISTS pg_trgm`` (like database.cross_matching._ensure_pg_trgm); where the database
refuses, the non-integration seeder tests only pass if ``seed_families`` degrades gracefully (skips the trigram
clause) instead of calling similarity() unconditionally. That no-extension path cannot be proven locally.

Fixtures are committed literals; every expectation is derived in a comment beside the row that causes it.
Accepted holes are listed in the module tail comment.
"""
import hashlib
import html as html_lib
import importlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import psycopg
import pytest

from database.connection import get_db, init_db
from database.models import AppUser
from tests.test_isbe_rewire import (
    _create_candidate_finance_agg_view,
    _create_compat_views,
    _seed_isbe_tables,
)
from webapp.app import create_app

ADMIN_USER = "manual_admin"
ADMIN_PASS = "secret123"
LIST_URL = "/admin/committee-families"
HEX40 = re.compile(r"[0-9a-f]{40}")


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _seeder():
    """The not-yet-written seeder module; a missing module fails the calling test, not collection."""
    try:
        return importlib.import_module("database.committee_families")
    except ModuleNotFoundError as exc:
        if exc.name != "database.committee_families":
            raise
        pytest.fail("S-C8 not implemented: database/committee_families.py does not exist")


def _columns(conn, table):
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table,),
    ).fetchall()
    return {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows}


def _require_family_tables(conn):
    for table in ("committee_family", "committee_family_member"):
        if not _columns(conn, table):
            pytest.fail(f"S-C8 not implemented: table {table} is missing from database/schema.sql")


def _read_families(db_path):
    """{family_id: row dict + 'members': frozenset(committee ids)} read through a FRESH connection."""
    conn = get_db(db_path)
    try:
        families = conn.execute(
            "SELECT id, label, status, evidence, member_hash, notes, created_at, reviewed_at "
            "FROM committee_family ORDER BY id"
        ).fetchall()
        members = conn.execute(
            "SELECT family_id, committee_id FROM committee_family_member ORDER BY family_id, committee_id"
        ).fetchall()
    finally:
        conn.close()
    result = {row["id"]: dict(row) for row in families}
    for record in result.values():
        record["members"] = frozenset()
    grouped = {}
    for member in members:
        grouped.setdefault(member["family_id"], set()).add(member["committee_id"])
    for family_id, ids in grouped.items():
        result[family_id]["members"] = frozenset(ids)
    return result


def _stable(record):
    """The fields a review decision / re-seed must not disturb (label and evidence may be refreshed)."""
    return (
        record["status"], record["notes"], record["reviewed_at"], record["created_at"],
        record["member_hash"], record["members"],
    )


def _seconds_from_db_now(db_path, family_id, column):
    """|DB clock - timestamp column| in seconds, computed server-side (no Python/DB timezone guessing)."""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            f"SELECT abs(extract(epoch FROM (localtimestamp - {column}))) AS secs "
            "FROM committee_family WHERE id = ?",
            (family_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["secs"] is not None, f"{column} is NULL for family {family_id}"
    return float(row["secs"])


def _norm(text):
    """Lower-case with runs of space/underscore/hyphen collapsed: 'same_address' -> 'same address'."""
    return re.sub(r"[\s_\-]+", " ", text.lower()).strip()


def _visible_text(page):
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", stripped)).split())


def _main_text(page):
    main = re.search(r"<main\b.*?</main>", page, re.S | re.I)
    return _visible_text(main.group(0) if main else page)


def _hrefs(page):
    return [html_lib.unescape(h) for h in re.findall(r'href="([^"]*)"', page)]


def _linked_committee_ids(page):
    ids = set()
    for href in _hrefs(page):
        match = re.fullmatch(r"/committees/(\d+)", re.split(r"[?#]", href)[0])
        if match:
            ids.add(int(match.group(1)))
    return ids


def _csrf_token(page):
    text = page if isinstance(page, str) else page.decode("utf-8")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    assert match is not None, "no csrf_token hidden input on the page"
    return match.group(1)


class _Row:
    def __init__(self):
        self.cells = []
        self.hrefs = []
        self.has_td = False

    @property
    def text(self):
        return " ".join(self.cells)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = _Row()
            self._stack[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._row.has_td = self._row.has_td or tag == "td"
        elif tag == "a" and attrs.get("href") and self._row is not None:
            self._row.hrefs.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.cells.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _body_rows(page):
    """Every <tr> that has a <td>, across all tables, in document order."""
    parser = _TableParser()
    parser.feed(page)
    parser.close()
    return [row for table in parser.tables for row in table if row.has_td]


def _rows_with(page, needle):
    return [row for row in _body_rows(page) if needle in row.text]


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = {"action": attrs.get("action", ""), "controls": []}
            self.forms.append(self._form)
        elif tag in ("input", "button") and self._form is not None and attrs.get("name"):
            self._form["controls"].append((attrs["name"], attrs.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def _forms(page):
    parser = _FormParser()
    parser.feed(page)
    parser.close()
    return parser.forms


# ---------------------------------------------------------------------------
# 1. Schema (database/schema.sql, applied by init_db)
# ---------------------------------------------------------------------------

FAMILY_COLUMNS = {
    "id": ("bigint", "NO"),
    "label": ("text", "NO"),
    "status": ("text", "NO"),
    "evidence": ("jsonb", "NO"),
    "member_hash": ("text", "NO"),
    "notes": ("text", "YES"),
    "created_at": ("timestamp without time zone", "NO"),
    "reviewed_at": ("timestamp without time zone", "YES"),
}
MEMBER_COLUMNS = {
    "family_id": ("bigint", "NO"),
    "committee_id": ("integer", "NO"),
    "evidence": ("jsonb", "NO"),
}


@pytest.fixture
def schema_conn(tmp_path: Path):
    db_path = str(tmp_path / "families_schema.db")
    init_db(db_path)
    conn = get_db(db_path)
    yield conn
    conn.close()


class TestSchema:
    def test_tables_have_exactly_the_designed_columns(self, schema_conn):
        family = _columns(schema_conn, "committee_family")
        member = _columns(schema_conn, "committee_family_member")
        assert family, "committee_family table does not exist"
        assert member, "committee_family_member table does not exist"
        assert family == FAMILY_COLUMNS          # exact set: an extra or missing column fails
        assert member == MEMBER_COLUMNS

    def test_row_defaults(self, schema_conn):
        _require_family_tables(schema_conn)
        schema_conn.execute(
            "INSERT INTO committee_family (label, member_hash) VALUES ('Default Family', 'hash-default')"
        )
        schema_conn.execute(
            "INSERT INTO committee_family_member (family_id, committee_id) "
            "SELECT id, 4242 FROM committee_family WHERE member_hash = 'hash-default'"
        )
        schema_conn.commit()
        family = schema_conn.execute(
            "SELECT status, evidence, notes, reviewed_at, "
            "abs(extract(epoch FROM (localtimestamp - created_at))) AS age FROM committee_family"
        ).fetchone()
        member = schema_conn.execute("SELECT evidence FROM committee_family_member").fetchone()
        assert family["status"] == "pending"
        assert family["evidence"] == {}
        assert family["notes"] is None
        assert family["reviewed_at"] is None
        assert float(family["age"]) < 120          # created_at defaulted to the DB clock, parsed not just non-NULL
        assert member["evidence"] == {}

    def test_status_check_allows_exactly_pending_accepted_rejected(self, schema_conn):
        _require_family_tables(schema_conn)
        for index, status in enumerate(("pending", "accepted", "rejected")):
            schema_conn.execute(
                "INSERT INTO committee_family (label, status, member_hash) VALUES (?, ?, ?)",
                (f"ok {status}", status, f"hash-ok-{index}"),
            )
            schema_conn.commit()
        for bad in ("archived", "Accepted", "approved", ""):
            with pytest.raises(psycopg.errors.CheckViolation):
                schema_conn.execute(
                    "INSERT INTO committee_family (label, status, member_hash) VALUES (?, ?, ?)",
                    (f"bad {bad}", bad, f"hash-bad-{bad}"),
                )
        remaining = schema_conn.execute("SELECT COUNT(*) AS n FROM committee_family").fetchone()["n"]
        assert remaining == 3                      # the rejected inserts left nothing behind

    def test_member_hash_unique_member_pk_fk_and_cascade(self, schema_conn):
        _require_family_tables(schema_conn)
        assert _columns(schema_conn, "isbe_committees") == {}, "premise: no isbe_committees in a bare schema"
        schema_conn.execute("INSERT INTO committee_family (label, member_hash) VALUES ('One', 'hash-one')")
        family_id = schema_conn.execute("SELECT id FROM committee_family").fetchone()["id"]
        # committee_id has NO foreign key: the ETL drops and reloads isbe_*, and none exists here.
        schema_conn.execute(
            "INSERT INTO committee_family_member (family_id, committee_id) VALUES (?, 5001), (?, 5002)",
            (family_id, family_id),
        )
        schema_conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            schema_conn.execute("INSERT INTO committee_family (label, member_hash) VALUES ('Two', 'hash-one')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            schema_conn.execute(
                "INSERT INTO committee_family_member (family_id, committee_id) VALUES (?, 5001)", (family_id,)
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            schema_conn.execute(
                "INSERT INTO committee_family_member (family_id, committee_id) VALUES (999999, 5003)"
            )
        assert schema_conn.execute("SELECT COUNT(*) AS n FROM committee_family").fetchone()["n"] == 1
        assert schema_conn.execute("SELECT COUNT(*) AS n FROM committee_family_member").fetchone()["n"] == 2
        schema_conn.execute("DELETE FROM committee_family WHERE id = ?", (family_id,))
        schema_conn.commit()
        assert schema_conn.execute("SELECT COUNT(*) AS n FROM committee_family_member").fetchone()["n"] == 0

    def test_member_table_is_indexed_on_committee_id(self, schema_conn):
        _require_family_tables(schema_conn)
        rows = schema_conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = 'committee_family_member'"
        ).fetchall()
        definitions = [r["indexdef"] for r in rows]
        # The PK index is on (family_id, committee_id); a lookup by committee needs its own index.
        assert any(re.search(r"\(committee_id\)\s*$", d) for d in definitions), definitions


# ---------------------------------------------------------------------------
# 2. Seeder fixture: committed literals, expectations derived in the comments
# ---------------------------------------------------------------------------
# Normalization used below: lower-case, every run of non-alphanumerics -> one space, trimmed.
#   "Friends Of Pat." -> "friends of pat" ; "100 Main St." + "60651" -> "100 main st 60651".
# Noise from tests.test_isbe_rewire._seed_isbe_tables stays in the database on purpose (committees 100/200/300,
# candidates 1001-1003, officers of 100/200): none of it may end up in a family.
#
# (id, name, active, creation_date, street, zip)
BASE_COMMITTEES = [
    # F1 -- same normalized name ("friends of pat"), shared candidate 10, same zip but different streets.
    (1, "Friends of Pat", False, "2012-03-01", "10 Elm St", "62711"),
    (2, "Friends Of Pat.", True, "2018-05-01", "99 Oak Ave", "62711"),
    # D1 -- shared candidate 12 and nothing else: trigram 0.0, different street and zip, no officers.
    (7, "Blue Ribbon Fund", False, "2010-01-01", "20 Pine Rd", "62720"),
    (8, "Oak Street Neighbors", True, "2019-01-01", "21 Maple Rd", "62721"),
    # F3 -- shared candidate 13 + officer "Sam Treasurer" (case differs); names, streets, zips unrelated.
    (9, "Aldridge Victory Fund", True, "2020-01-01", "1 Prairie Rd", "62730"),
    (10, "Zimmer Leadership Committee", False, "2016-01-01", "2 River Rd", "62731"),
    # F4 -- shared candidates 14 AND 44, same street after normalization, same zip; unrelated names.
    (11, "Brightwater Initiative", False, "2014-01-01", "100 Main St.", "60651"),
    (12, "Cedar Hollow Committee", True, "2019-01-01", "100 MAIN ST", "60651"),
    # D2 -- identical normalized name, same zip, but DIFFERENT candidates (20 / 21): no family.
    (21, "Citizens for Quality Schools", False, "2005-01-01", "1 School Ln", "62707"),
    (22, "Citizens for Quality Schools.", True, "2007-01-01", "2 School Ln", "62707"),
    # D2b -- identical normalized name, NO candidate link rows at all, different zips: no family.
    (23, "Neighbors for Better Roads", False, "2008-01-01", "3 Road St", "62708"),
    (24, "NEIGHBORS FOR BETTER ROADS", True, "2009-01-01", "4 Road St", "62709"),
    # D3 -- same street address + zip, different candidates (22 / 23): no family.
    (25, "Hollis Park Committee", False, "2011-01-01", "300 Shared Plaza", "60301"),
    (26, "Quarry Road Alliance", True, "2012-01-01", "300 Shared Plaza", "60301"),
    # D4 -- same officer "Alex Shared", different candidates (24 / 25): no family.
    (27, "Ingram Fund", False, "2011-01-01", "5 Ingram Rd", "62740"),
    (28, "Jasper Fund", True, "2013-01-01", "6 Jasper Rd", "62741"),
    # CHAIN -- 31~32 same address (candidate 15), 32~33 same officer (candidate 16), 31 and 33 share nothing.
    # One family {31,32,33} only through union across two candidates and two kinds of evidence.
    (31, "Lakeview Citizens Group", False, "2011-01-01", "5 Lake Dr", "60611"),
    (32, "Mapleton Voters Fund", True, "2022-01-01", "5 Lake Dr", "60611"),
    (33, "Vandalia Friends Committee", False, "2015-01-01", "77 Other Blvd", "60699"),
    # D5 -- shared candidate 26; officers "Tim Treasurer" vs {"Tom Treasurer", "Tim Other"}: same LAST name
    # and same FIRST name appear, but no full-name match: no family.
    (41, "Kestrel Committee", False, "2011-01-01", "7 Kestrel Rd", "62750"),
    (42, "Lumen Committee", True, "2013-01-01", "8 Lumen Rd", "62751"),
    # D6 -- shared candidate 27; both committees' only officer row has a blank name (NULL/NULL vs ''/'').
    (45, "Maple Hill Fund", False, "2011-01-01", "9 Maple Rd", "62760"),
    (46, "Nectar Lane Fund", True, "2013-01-01", "10 Nectar Rd", "62761"),
    # D7 -- shared candidate 28; NO street address, same zip: a zip alone is not an address.
    (47, "Orchard Fund", False, "2011-01-01", None, "60999"),
    (48, "Pine Fund", True, "2013-01-01", "", "60999"),
    # D8 -- shared candidate 29; same street, DIFFERENT zip: no family.
    (49, "Quill Fund", False, "2011-01-01", "1 Same St", "60001"),
    (50, "Rook Fund", True, "2013-01-01", "1 Same St", "62001"),
    # D9 -- shared candidate 30; different street, SAME zip: no family.
    (51, "Sable Fund", False, "2011-01-01", "1 Alpha St", "60501"),
    (52, "Tern Fund", True, "2013-01-01", "2 Beta St", "60501"),
    # D10 -- identical normalized name, link rows exist but candidate_id is NULL: NULL is not a shared candidate.
    (53, "Quartz Hollow Committee", False, "2011-01-01", "11 Quartz Rd", "62770"),
    (54, "QUARTZ HOLLOW COMMITTEE", True, "2013-01-01", "12 Quartz Rd", "62771"),
]

# (committee_id, candidate_id)
BASE_LINKS = [
    (1, 10), (2, 10), (2, 10),                     # duplicate link row on purpose: pair counted once
    (7, 12), (8, 12),
    (9, 13), (10, 13),
    (11, 14), (11, 44), (12, 14), (12, 44),
    (21, 20), (22, 21),
    (25, 22), (26, 23),
    (27, 24), (28, 25),
    (31, 15), (32, 15), (32, 16), (33, 16),
    (41, 26), (42, 26),
    (45, 27), (46, 27),
    (47, 28), (48, 28),
    (49, 29), (50, 29),
    (51, 30), (52, 30),
    (53, None), (54, None),
]

# (committee_id, first_name, last_name, title)
BASE_OFFICERS = [
    (9, "Sam", "Treasurer", "Treasurer"), (10, "SAM", "TREASURER", "Treasurer"),
    (27, "Alex", "Shared", "Treasurer"), (28, "Alex", "Shared", "Treasurer"),
    (32, "Pat", "Chair", "Chairman"), (33, "PAT", "CHAIR", "Chairman"),
    (41, "Tim", "Treasurer", "Treasurer"),
    (42, "Tom", "Treasurer", "Treasurer"), (42, "Tim", "Other", "Secretary"),
    (45, None, None, "Treasurer"), (46, "", "", "Treasurer"),
]

# Needs pg_trgm (integration). Trigram similarities measured on PostgreSQL 16 / pg_trgm and re-checked by the
# fixture builder: 3~4 = 0.8000 exactly (boundary: rejects any threshold above 0.8), 13~14 = 0.7727,
# 61~62 = 0.9643, 63~64 = 0.7692, 65~66 = 0.8788.
TRGM_COMMITTEES = [
    # P2 -- shared candidate 11, similarity 0.8 (not identical): a family on similar_name alone.
    (3, "Friends of Sam Ortega", False, "2013-04-01", "5 Birch Ln", "62712"),
    (4, "Friends of Samuel Ortega", True, "2021-06-01", "8 Cedar Ln", "62712"),
    # N2 -- shared candidate 17, similarity 0.7727 < 0.8: NOT a family.
    (13, "Citizens for Pat", False, "2015-01-01", "3 Oak Ct", "62713"),
    (14, "Citizens for Pat 2024", True, "2024-01-01", "4 Elm Ct", "62714"),
    # W1 -- no candidates, same zip, similarity 0.9643: a WEAK pair (review queue), never a family.
    (61, "Illinois Prairie Action PAC", True, "2016-01-01", "9 Action Way", "62703"),
    (62, "Illinois Prairie Action IE PAC", True, "2017-01-01", "9 Action Way Ste 2", "62703"),
    # W2 -- same zip but similarity 0.7692 < 0.8: not weak.
    (63, "Friends of Pat Quinn", True, "2016-01-01", "11 Quinn St", "62704"),
    (64, "Friends of Patrick Quinn", True, "2017-01-01", "12 Quinn St", "62704"),
    # W3 -- similarity 0.8788 but different zips: not weak.
    (65, "Riverbend Taxpayers Alliance", True, "2016-01-01", "13 River Rd", "62705"),
    (66, "Riverbend Taxpayers Alliance Inc", True, "2017-01-01", "14 River Rd", "62706"),
    # W4 -- identical names, blank zips (NULL / ''): not weak.
    (67, "Harbor Light Committee", True, "2016-01-01", None, None),
    (68, "Harbor Light Committee", True, "2017-01-01", "", ""),
]
TRGM_LINKS = [(3, 11), (4, 11), (13, 17), (14, 17)]

FAMILY_F1 = frozenset({1, 2})
FAMILY_F3 = frozenset({9, 10})
FAMILY_F4 = frozenset({11, 12})
FAMILY_CHAIN = frozenset({31, 32, 33})
FAMILY_P2 = frozenset({3, 4})

BASE_FAMILIES = {FAMILY_F1, FAMILY_F3, FAMILY_F4, FAMILY_CHAIN}
TRGM_FAMILIES = BASE_FAMILIES | {FAMILY_P2}
# Strong pairs: base = (1,2) (9,10) (11,12) (31,32) (32,33) = 5; with trigram also (3,4) = 6.
BASE_PAIRS = 5
TRGM_PAIRS = 6

DECOY_NAMES = {
    frozenset({7, 8}): "D1 shared candidate only",
    frozenset({21, 22}): "D2 same name, different candidates",
    frozenset({23, 24}): "D2b same name, no candidate rows",
    frozenset({25, 26}): "D3 same address, no shared candidate",
    frozenset({27, 28}): "D4 same officer, no shared candidate",
    frozenset({41, 42}): "D5 officer first-only/last-only match",
    frozenset({45, 46}): "D6 blank officer names",
    frozenset({47, 48}): "D7 blank street, same zip",
    frozenset({49, 50}): "D8 same street, different zip",
    frozenset({51, 52}): "D9 different street, same zip",
    frozenset({53, 54}): "D10 NULL candidate rows",
    frozenset({13, 14}): "N2 similarity 0.7727 below threshold",
}

# Label = as-filed name of the member that is both active and has the latest creation_date.
EXPECTED_LABELS = {
    FAMILY_F1: "Friends Of Pat.",              # id 2: higher id, as-filed case and trailing period kept
    FAMILY_F3: "Aldridge Victory Fund",        # id 9: lower id, alphabetically first
    FAMILY_F4: "Cedar Hollow Committee",       # id 12
    FAMILY_CHAIN: "Mapleton Voters Fund",      # id 32: middle id AND middle name (L < M < V): kills min/max id, first/last alpha
    FAMILY_P2: "Friends of Samuel Ortega",     # id 4
}

# (same_name, shared_officer, same_address, shared candidates) per family.
EXPECTED_EVIDENCE = {
    FAMILY_F1: (True, False, False, [10]),
    FAMILY_F3: (False, True, False, [13]),
    FAMILY_F4: (False, False, True, [14, 44]),
    FAMILY_CHAIN: (False, True, True, [15, 16]),
}


def _insert_committees(conn, rows):
    conn.executemany(
        "INSERT INTO isbe_committees (id, name, type, address1, zipcode, active, creation_date) "
        "VALUES (?, ?, 'Candidate', ?, ?, ?, ?)",
        [(cid, name, street, zipcode, active, created) for cid, name, active, created, street, zipcode in rows],
    )


def _insert_links(conn, links):
    conn.executemany("INSERT INTO isbe_candidate_committees (committee_id, candidate_id) VALUES (?, ?)", links)


def _insert_officers(conn, officers):
    conn.executemany(
        "INSERT INTO isbe_officers (committee_id, first_name, last_name, title) VALUES (?, ?, ?, ?)", officers
    )


def _try_pg_trgm(conn):
    """Best effort, like database.cross_matching._ensure_pg_trgm: True when similarity() is usable."""
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")
        conn.commit()
        conn.execute("SELECT similarity('a', 'a') AS s").fetchone()
        return True
    except Exception:                                               # pragma: no cover - environment dependent
        conn.rollback()
        return False


def _require_trgm(conn):
    if not _try_pg_trgm(conn):
        pytest.skip("pg_trgm unavailable in the test database")
    premises = {
        ("friends of sam ortega", "friends of samuel ortega"): (0.8, 0.8),
        ("citizens for pat", "citizens for pat 2024"): (0.77, 0.7728),
        ("illinois prairie action pac", "illinois prairie action ie pac"): (0.96, 0.97),
        ("friends of pat quinn", "friends of patrick quinn"): (0.76, 0.77),
        ("riverbend taxpayers alliance", "riverbend taxpayers alliance inc"): (0.87, 0.88),
    }
    for (a, b), (low, high) in premises.items():
        value = round(float(conn.execute("SELECT similarity(?, ?) AS s", (a, b)).fetchone()["s"]), 4)
        assert low <= value <= high, f"fixture premise drifted: similarity({a!r}, {b!r}) = {value}"


def _make_seed_world(tmp_path, *, trigram):
    db_path = str(tmp_path / "families_seed.db")
    init_db(db_path)
    conn = get_db(db_path)
    _try_pg_trgm(conn)       # the base tests also run where the extension is missing and must not need it
    _seed_isbe_tables(conn)
    _insert_committees(conn, BASE_COMMITTEES)
    _insert_links(conn, BASE_LINKS)
    _insert_officers(conn, BASE_OFFICERS)
    if trigram:
        _require_trgm(conn)
        _insert_committees(conn, TRGM_COMMITTEES)
        _insert_links(conn, TRGM_LINKS)
    conn.commit()
    return SimpleNamespace(db_path=db_path, conn=conn)


@pytest.fixture
def seed_world(tmp_path: Path):
    world = _make_seed_world(tmp_path, trigram=False)
    yield world
    world.conn.close()


@pytest.fixture
def seed_world_trgm(tmp_path: Path):
    world = _make_seed_world(tmp_path, trigram=True)
    yield world
    world.conn.close()


def _member_sets(db_path):
    return sorted(sorted(rec["members"]) for rec in _read_families(db_path).values())


def _assert_exact_families(db_path, expected):
    actual = [frozenset(ids) for ids in _member_sets(db_path)]
    unexpected = [s for s in actual if s not in expected]
    problems = [f"{sorted(s)}: {DECOY_NAMES.get(s, 'not an expected family')}" for s in unexpected]
    assert not problems, f"families that must not exist: {problems}"
    missing = [sorted(s) for s in expected if s not in actual]
    assert not missing, f"expected families not created: {missing}"
    assert len(actual) == len(expected), f"duplicate or extra families: {sorted(map(sorted, actual))}"


class TestSeeder:
    def test_seed_returns_the_three_counts(self, seed_world):
        result = _seeder().seed_families(seed_world.conn)
        seed_world.conn.commit()
        assert result["pairs"] == BASE_PAIRS             # 5 strong pairs; the duplicate link row is not a 6th
        assert result["families_created"] == 4
        assert result["families_existing"] == 0

    def test_seed_creates_exactly_the_expected_families_and_no_decoys(self, seed_world):
        _seeder().seed_families(seed_world.conn)
        seed_world.conn.commit()
        _assert_exact_families(seed_world.db_path, BASE_FAMILIES)

    def test_new_families_are_pending_and_unreviewed(self, seed_world):
        _seeder().seed_families(seed_world.conn)
        seed_world.conn.commit()
        records = _read_families(seed_world.db_path)
        assert len(records) == 4
        hashes = [rec["member_hash"] for rec in records.values()]
        for family_id, rec in records.items():
            assert rec["status"] == "pending"
            assert rec["reviewed_at"] is None
            assert rec["notes"] is None
            assert HEX40.fullmatch(rec["member_hash"]), rec["member_hash"]       # sha1 hexdigest
            assert _seconds_from_db_now(seed_world.db_path, family_id, "created_at") < 120
        assert len(set(hashes)) == 4                      # one hash per distinct member set

    def test_label_is_the_as_filed_name_of_the_most_recently_active_member(self, seed_world):
        _seeder().seed_families(seed_world.conn)
        seed_world.conn.commit()
        labels = {rec["members"]: rec["label"] for rec in _read_families(seed_world.db_path).values()}
        for members in BASE_FAMILIES:
            assert labels[members] == EXPECTED_LABELS[members], sorted(members)

    def test_evidence_records_only_what_actually_holds(self, seed_world):
        _seeder().seed_families(seed_world.conn)
        seed_world.conn.commit()
        by_members = {rec["members"]: rec["evidence"] for rec in _read_families(seed_world.db_path).values()}
        for members, (same_name, officer, address, candidates) in EXPECTED_EVIDENCE.items():
            evidence = by_members[members]
            assert bool(evidence.get("same_name")) is same_name, (sorted(members), evidence)
            assert bool(evidence.get("shared_officer")) is officer, (sorted(members), evidence)
            assert bool(evidence.get("same_address")) is address, (sorted(members), evidence)
            assert sorted(int(c) for c in evidence["shared_candidates"]) == candidates, (sorted(members), evidence)
            similar = float(evidence.get("similar_name") or 0.0)
            if not same_name:
                assert similar < 0.8, (sorted(members), evidence)    # unrelated names must not claim similarity

    def test_reseed_is_idempotent_keeps_reviews_and_adds_only_new_member_sets(self, seed_world):
        seeder = _seeder()
        conn = seed_world.conn
        first = seeder.seed_families(conn)
        conn.commit()
        assert first["families_created"] == 4            # a no-op seeder cannot pass the next assertions
        by_members = {rec["members"]: fid for fid, rec in _read_families(seed_world.db_path).items()}
        conn.execute(
            "UPDATE committee_family SET status = 'accepted', reviewed_at = '2026-01-15 10:00:00', notes = 'keep me' "
            "WHERE id = ?", (by_members[FAMILY_F1],))
        conn.execute(
            "UPDATE committee_family SET status = 'rejected', reviewed_at = '2026-01-16 11:00:00', "
            "notes = 'different actors' WHERE id = ?", (by_members[FAMILY_F3],))
        conn.commit()
        before = _read_families(seed_world.db_path)

        second = seeder.seed_families(conn)
        conn.commit()
        assert second["families_created"] == 0
        assert second["families_existing"] == 4
        assert second["pairs"] == BASE_PAIRS
        after = _read_families(seed_world.db_path)
        assert set(after) == set(before)                  # same ids: nothing deleted and re-inserted
        for family_id, rec in before.items():
            assert _stable(after[family_id]) == _stable(rec), family_id
        assert after[by_members[FAMILY_F1]]["status"] == "accepted"
        assert after[by_members[FAMILY_F3]]["status"] == "rejected"

        # New evidence between runs: exactly one new pending family, everything else untouched.
        _insert_committees(conn, [
            (81, "Friends of Casey Vale", False, "2014-01-01", "1 Vale St", "62780"),
            (82, "Friends Of Casey Vale.", True, "2018-01-01", "2 Vale St", "62781"),
        ])
        _insert_links(conn, [(81, 40), (82, 40)])
        conn.commit()
        third = seeder.seed_families(conn)
        conn.commit()
        assert third["pairs"] == BASE_PAIRS + 1
        assert third["families_created"] == 1
        assert third["families_existing"] == 4
        final = _read_families(seed_world.db_path)
        new_ids = set(final) - set(before)
        assert len(new_ids) == 1
        new_record = final[new_ids.pop()]
        assert new_record["members"] == frozenset({81, 82})
        assert new_record["status"] == "pending"
        for family_id, rec in before.items():
            assert _stable(final[family_id]) == _stable(rec), family_id

    @pytest.mark.integration
    def test_similar_names_group_at_the_threshold_and_not_below(self, seed_world_trgm):
        conn = seed_world_trgm.conn
        result = _seeder().seed_families(conn)
        conn.commit()
        assert result["pairs"] == TRGM_PAIRS
        assert result["families_created"] == 5
        _assert_exact_families(seed_world_trgm.db_path, TRGM_FAMILIES)   # {13,14} at 0.7727 must be absent
        records = {rec["members"]: rec for rec in _read_families(seed_world_trgm.db_path).values()}
        p2 = records[FAMILY_P2]
        assert p2["label"] == EXPECTED_LABELS[FAMILY_P2]
        assert not p2["evidence"].get("same_name")
        assert 0.8 - 1e-6 <= float(p2["evidence"]["similar_name"]) < 1.0
        assert sorted(int(c) for c in p2["evidence"]["shared_candidates"]) == [11]

    @pytest.mark.integration
    def test_weak_pairs_are_same_zip_similar_names_without_a_shared_candidate(self, seed_world_trgm):
        seeder = _seeder()
        conn = seed_world_trgm.conn
        rows = seeder.weak_pairs(conn)
        pairs = sorted((int(r["c1"]), int(r["c2"])) for r in rows)
        # (21,22): identical names, same zip, two DIFFERENT candidates; (61,62): 0.9643, no candidates at all.
        # Absent on purpose: (1,2) (3,4) shared candidates; (13,14) (63,64) below 0.8; (65,66) other zip; (67,68) blank zip.
        assert pairs == [(21, 22), (61, 62)]
        by_pair = {(int(r["c1"]), int(r["c2"])): float(r["sim"]) for r in rows}
        assert by_pair[(21, 22)] == pytest.approx(1.0, abs=0.01)
        assert by_pair[(61, 62)] == pytest.approx(0.9643, abs=0.01)
        _require_family_tables(conn)
        assert conn.execute("SELECT COUNT(*) AS n FROM committee_family").fetchone()["n"] == 0   # read-only

        seeder.seed_families(conn)
        conn.commit()
        members = {m for rec in _read_families(seed_world_trgm.db_path).values() for m in rec["members"]}
        assert members.isdisjoint({21, 22, 61, 62})       # weak pairs are never seeded as families
        assert sorted((int(r["c1"]), int(r["c2"])) for r in seeder.weak_pairs(conn)) == pairs


# ---------------------------------------------------------------------------
# 3. Admin review flow  (/admin/committee-families, modelled on /admin/donor-merges)
# ---------------------------------------------------------------------------
# Auth, copied from tests/test_webapp.py: AppUser "manual_admin" / "secret123" signs in through
# /auth/login with the page's csrf_token; anonymous GETs of admin pages answer 302 -> /auth/login.
# Every POST needs the session csrf_token (before_request enforce_csrf answers 400 otherwise).

@pytest.fixture
def families_app(tmp_path: Path):
    """ISBE-shaped app (committees 100/200/300 as noise) plus an admin user; tests add their own families."""
    db_path = str(tmp_path / "families_web.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    AppUser.create_or_update_password(conn, ADMIN_USER, ADMIN_PASS)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _db_path(app):
    return app.config["DATABASE_PATH"]


def _add_committee(conn, committee_id, name, *, active=True):
    conn.execute(
        "INSERT INTO isbe_committees (id, name, type, active, city, state, zipcode) "
        "VALUES (?, ?, 'Candidate', ?, 'Springfield', 'IL', '62701')",
        (committee_id, name, active),
    )


def _add_family(conn, label, status, members, *, evidence=None, notes=None):
    """Insert one family + its member rows straight into the tables; returns the family id."""
    _require_family_tables(conn)
    member_hash = hashlib.sha1(",".join(str(i) for i in sorted(members)).encode()).hexdigest()
    reviewed_at = None if status == "pending" else "2026-01-15 10:00:00"
    row = conn.execute(
        "INSERT INTO committee_family (label, status, evidence, member_hash, notes, reviewed_at) "
        "VALUES (?, ?, CAST(? AS JSONB), ?, ?, ?) RETURNING id",
        (label, status, json.dumps(evidence or {}), member_hash, notes, reviewed_at),
    ).fetchone()
    for committee_id in members:
        conn.execute(
            "INSERT INTO committee_family_member (family_id, committee_id) VALUES (?, ?)", (row["id"], committee_id)
        )
    return row["id"]


def _admin_client(app):
    client = app.test_client()
    login_page = client.get(f"/auth/login?next={quote(LIST_URL, safe='/')}")
    response = client.post(
        "/auth/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASS, "next": LIST_URL,
              "csrf_token": _csrf_token(login_page.data)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return client


def _token(client, family_id):
    page = client.get(f"{LIST_URL}/{family_id}")
    assert page.status_code == 200, f"GET {LIST_URL}/{family_id} -> {page.status_code}"
    return _csrf_token(page.data)


def _post(client, family_id, token, **fields):
    return client.post(
        f"{LIST_URL}/{family_id}/decision", data={"csrf_token": token, **fields}, follow_redirects=False
    )


# Review-queue world. Insert order fixes the family ids 1..4 and keeps each family's SIZE different from its
# id and its active count, so the size cell cannot be confused with an id or an active-count cell.
#   key  status    size  active  evidence
#   A    pending     3     1     same_name
#   X    accepted    4     2     similar_name
#   B    pending     2     0     shared_officer + same_address
#   R    rejected    2     1     same_address
ADMIN_COMMITTEES = [
    (801, "Alder Friends", True), (802, "Alder Citizens", False), (803, "Alder Victory", False),
    (811, "Birch One", True), (812, "Birch Two", True), (813, "Birch Three", False), (814, "Birch Four", False),
    (821, "Cedar One", False), (822, "Cedar Two", False),
    (831, "Dogwood One", True), (832, "Dogwood Two", False),
]
LABEL_A, LABEL_X, LABEL_B, LABEL_R = (
    "Alder Slate Family", "Birch Accepted Family", "Cedar Pending Family", "Dogwood Rejected Family",
)
ALL_ADMIN_LABELS = (LABEL_A, LABEL_X, LABEL_B, LABEL_R)


def _admin_world(app):
    conn = get_db(_db_path(app))
    for committee_id, name, active in ADMIN_COMMITTEES:
        _add_committee(conn, committee_id, name, active=active)
    ids = {
        "A": _add_family(conn, LABEL_A, "pending", [801, 802, 803],
                         evidence={"same_name": True, "shared_candidates": [80]}),
        "X": _add_family(conn, LABEL_X, "accepted", [811, 812, 813, 814],
                         evidence={"similar_name": 0.91, "shared_candidates": [81]}),
        "B": _add_family(conn, LABEL_B, "pending", [821, 822],
                         evidence={"shared_officer": True, "same_address": True, "shared_candidates": [82]}),
        "R": _add_family(conn, LABEL_R, "rejected", [831, 832],
                         evidence={"same_address": True, "shared_candidates": [83]}),
    }
    conn.commit()
    conn.close()
    return ids


def _single_row(page, needle):
    rows = _rows_with(page, needle)
    assert len(rows) == 1, f"expected exactly one table row containing {needle!r}, got {len(rows)}"
    return rows[0]


class TestAdminAccess:
    def test_anonymous_visitors_are_redirected_to_login(self, families_app):
        client = families_app.test_client()
        for url in (LIST_URL, f"{LIST_URL}?status=accepted", f"{LIST_URL}/1"):
            response = client.get(url)
            assert response.status_code == 302, f"GET {url} -> {response.status_code}, expected a redirect to login"
            assert "/auth/login" in response.headers.get("Location", ""), url

    def test_anonymous_decision_is_redirected_to_login_and_changes_nothing(self, families_app):
        ids = _admin_world(families_app)
        client = families_app.test_client()
        anonymous_token = _csrf_token(client.get("/auth/login").data)      # valid CSRF, but not signed in
        before = _read_families(_db_path(families_app))
        response = client.post(
            f"{LIST_URL}/{ids['A']}/decision",
            data={"csrf_token": anonymous_token, "decision": "accepted"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/auth/login" in response.headers.get("Location", "")       # not the handler's own redirect
        assert _read_families(_db_path(families_app)) == before

    def test_hub_links_to_the_families_page(self, families_app):
        client = _admin_client(families_app)
        page = client.get("/admin/").get_data(as_text=True)
        links = [href for href in _hrefs(page) if re.fullmatch(r"/admin/committee-families(\?.*)?", href)]
        assert links, "admin hub has no link to /admin/committee-families"


class TestAdminList:
    def test_default_view_is_pending_with_label_size_and_evidence(self, families_app):
        _admin_world(families_app)
        client = _admin_client(families_app)
        response = client.get(LIST_URL)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        shown = {label for label in ALL_ADMIN_LABELS if label in page}
        assert shown == {LABEL_A, LABEL_B}          # accepted and rejected families are not in the pending view

        row_a = _single_row(page, LABEL_A)
        assert re.search(r"\b3\b", row_a.text.replace(LABEL_A, " ")), row_a.text      # 3 members
        text_a = _norm(row_a.text)
        assert "same name" in text_a
        for absent in ("shared officer", "same address", "similar name"):
            assert absent not in text_a, (absent, row_a.text)

        row_b = _single_row(page, LABEL_B)
        assert re.search(r"\b2\b", row_b.text.replace(LABEL_B, " ")), row_b.text      # 2 members
        text_b = _norm(row_b.text)
        assert "shared officer" in text_b
        assert "same address" in text_b
        assert "same name" not in text_b

    def test_status_filter_shows_only_that_status(self, families_app):
        _admin_world(families_app)
        client = _admin_client(families_app)
        expected = {"pending": {LABEL_A, LABEL_B}, "accepted": {LABEL_X}, "rejected": {LABEL_R}}
        for status, labels in expected.items():
            page = client.get(f"{LIST_URL}?status={status}").get_data(as_text=True)
            shown = {label for label in ALL_ADMIN_LABELS if label in page}
            assert shown == labels, status
        accepted_page = client.get(f"{LIST_URL}?status=accepted").get_data(as_text=True)
        row_x = _single_row(accepted_page, LABEL_X)
        assert re.search(r"\b4\b", row_x.text.replace(LABEL_X, " ")), row_x.text      # 4 members
        assert "similar name" in _norm(row_x.text)


class TestAdminDetail:
    def test_detail_lists_every_member_with_id_name_and_active_flag(self, families_app):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        response = client.get(f"{LIST_URL}/{ids['A']}")
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert LABEL_A in page
        members = {801: ("Alder Friends", True), 802: ("Alder Citizens", False), 803: ("Alder Victory", False)}
        for committee_id, (name, active) in members.items():
            row = _single_row(page, name)
            text = row.text.lower()
            assert re.search(rf"\b{committee_id}\b", text), (name, row.text)
            has_active = re.search(r"\bactive\b", text) is not None
            has_inactive = re.search(r"\binactive\b", text) is not None
            if active:
                assert has_active, (name, row.text)
                assert not has_inactive, (name, row.text)
            else:
                assert has_inactive, (name, row.text)
                assert not has_active, (name, row.text)
        # members of the other families do not leak in
        for other in ("Birch One", "Cedar One", "Dogwood One"):
            assert other not in page
        text = _norm(_main_text(page))
        assert "same name" in text
        assert "shared officer" not in text

    def test_detail_forms_post_the_decisions_the_handler_understands(self, families_app):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        page = client.get(f"{LIST_URL}/{ids['A']}").get_data(as_text=True)
        decision_url = f"{LIST_URL}/{ids['A']}/decision"
        forms = [form for form in _forms(page) if form["action"] == decision_url]
        assert forms, f"no form posts to {decision_url}"
        for form in forms:
            assert any(name == "csrf_token" for name, _ in form["controls"]), form      # else the POST is a 400
        controls = [control for form in forms for control in form["controls"]]
        assert ("decision", "accepted") in controls
        assert ("decision", "rejected") in controls
        assert ("decision", "remove_member") in controls
        removable = {value for name, value in controls if name == "committee_id"}
        assert removable == {"801", "802", "803"}          # one remove control per member, none for foreign ids

    def test_unknown_family_is_404(self, families_app):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        assert client.get(f"{LIST_URL}/{ids['A']}").status_code == 200       # the route exists for a real family
        assert client.get(f"{LIST_URL}/999999").status_code == 404


class TestAdminDecisions:
    @pytest.mark.parametrize("key,decision,expected", [
        ("A", "accepted", "accepted"),       # pending  -> accepted
        ("A", "rejected", "rejected"),       # pending  -> rejected
        ("R", "accepted", "accepted"),       # rejected -> accepted: reviewed_at is refreshed, not kept from January
        ("X", "rejected", "rejected"),       # accepted -> rejected
    ])
    def test_decision_sets_status_and_reviewed_at_on_that_family_only(self, families_app, key, decision, expected):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        before = _read_families(_db_path(families_app))
        target_id = ids[key]
        response = _post(client, target_id, _token(client, target_id), decision=decision)
        assert response.status_code == 302
        after = _read_families(_db_path(families_app))
        target = after[target_id]
        assert target["status"] == expected
        assert _seconds_from_db_now(_db_path(families_app), target_id, "reviewed_at") < 60
        assert target["members"] == before[target_id]["members"]
        assert target["member_hash"] == before[target_id]["member_hash"]
        assert target["label"] == before[target_id]["label"]
        for family_id, record in before.items():
            if family_id != target_id:
                assert after[family_id] == record, f"family {family_id} was touched"

    def test_missing_or_unknown_decision_changes_nothing(self, families_app):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        token = _token(client, ids["A"])
        before = _read_families(_db_path(families_app))
        for fields in ({"decision": "bogus"}, {"decision": "approve"}, {"decision": ""}, {}):
            response = _post(client, ids["A"], token, **fields)
            assert response.status_code in (302, 400), fields
            assert _read_families(_db_path(families_app)) == before, fields

    def test_remove_member_deletes_only_that_membership_and_rewrites_the_hash(self, families_app):
        conn = get_db(_db_path(families_app))
        for committee_id, name, active in [(861, "Gum One", True), (862, "Gum Two", False),
                                           (863, "Gum Three", False), (864, "Gum Four", True)]:
            _add_committee(conn, committee_id, name, active=active)
        target = _add_family(conn, "Gum Target Family", "pending", [861, 862, 863])
        other = _add_family(conn, "Gum Other Family", "pending", [862, 864])      # shares committee 862
        conn.commit()
        conn.close()
        client = _admin_client(families_app)
        before = _read_families(_db_path(families_app))
        response = _post(client, target, _token(client, target), decision="remove_member", committee_id="862")
        assert response.status_code == 302
        after = _read_families(_db_path(families_app))
        assert after[target]["members"] == frozenset({861, 863})
        assert after[target]["member_hash"] != before[target]["member_hash"]
        assert HEX40.fullmatch(after[target]["member_hash"]), after[target]["member_hash"]
        assert after[target]["status"] == "pending"
        assert after[target]["label"] == "Gum Target Family"
        assert after[other] == before[other]                  # 862 is still in the OTHER family: delete is scoped

    def test_remove_member_without_a_member_of_that_family_changes_nothing(self, families_app):
        conn = get_db(_db_path(families_app))
        for committee_id, name in [(861, "Gum One"), (862, "Gum Two"), (864, "Gum Four")]:
            _add_committee(conn, committee_id, name)
        target = _add_family(conn, "Gum Target Family", "pending", [861, 862])
        _add_family(conn, "Gum Other Family", "pending", [862, 864])
        conn.commit()
        conn.close()
        client = _admin_client(families_app)
        token = _token(client, target)
        before = _read_families(_db_path(families_app))
        cases = (
            {"decision": "remove_member"},                                   # no committee_id
            {"decision": "remove_member", "committee_id": "864"},           # belongs to another family only
            {"decision": "remove_member", "committee_id": "abc"},           # not a number
            {"decision": "remove_member", "committee_id": "999999"},        # unknown committee
        )
        for fields in cases:
            response = _post(client, target, token, **fields)
            assert response.status_code in (302, 400), fields
            assert _read_families(_db_path(families_app)) == before, fields

    def test_accept_is_refused_when_a_member_is_already_in_another_accepted_family(self, families_app):
        conn = get_db(_db_path(families_app))
        for committee_id, name in [(841, "Elm One"), (842, "Elm Shared"), (843, "Elm Three")]:
            _add_committee(conn, committee_id, name)
        accepted = _add_family(conn, "Elm Accepted Family", "accepted", [841, 842])
        pending = _add_family(conn, "Elm Pending Family", "pending", [842, 843])
        conn.commit()
        conn.close()
        client = _admin_client(families_app)
        token = _token(client, pending)
        before = _read_families(_db_path(families_app))
        response = client.post(
            f"{LIST_URL}/{pending}/decision",
            data={"csrf_token": token, "decision": "accepted"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "flash-error" in page                          # the reviewer is told why
        assert "flash-success" not in page
        after = _read_families(_db_path(families_app))
        assert after[pending]["status"] == "pending"
        assert after[pending]["reviewed_at"] is None
        assert after == before                                # and nothing else moved
        assert after[accepted]["status"] == "accepted"

    def test_accept_is_not_blocked_by_rejected_families_or_by_the_family_itself(self, families_app):
        conn = get_db(_db_path(families_app))
        for committee_id, name in [(841, "Elm One"), (842, "Elm Two"), (851, "Fir One"), (852, "Fir Two"),
                                   (853, "Fir Three")]:
            _add_committee(conn, committee_id, name)
        already = _add_family(conn, "Elm Accepted Family", "accepted", [841, 842])
        clear = _add_family(conn, "Fir Pending Family", "pending", [851, 852])
        _add_family(conn, "Fir Old Rejected Family", "rejected", [851, 853])       # shares 851, but rejected
        conn.commit()
        conn.close()
        client = _admin_client(families_app)

        response = _post(client, clear, _token(client, clear), decision="accepted")
        assert response.status_code == 302
        assert _read_families(_db_path(families_app))[clear]["status"] == "accepted"

        # Accepting an already accepted family: its own members are not "another accepted family".
        response = client.post(
            f"{LIST_URL}/{already}/decision",
            data={"csrf_token": _token(client, already), "decision": "accepted"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "flash-error" not in response.get_data(as_text=True)
        assert _read_families(_db_path(families_app))[already]["status"] == "accepted"

    def test_decision_on_an_unknown_family_changes_nothing(self, families_app):
        ids = _admin_world(families_app)
        client = _admin_client(families_app)
        token = _token(client, ids["A"])
        before = _read_families(_db_path(families_app))
        for fields in ({"decision": "accepted"}, {"decision": "remove_member", "committee_id": "801"}):
            response = _post(client, 999999, token, **fields)
            assert response.status_code in (302, 404), fields
            assert _read_families(_db_path(families_app)) == before, fields
        # Control: the same session and token do work on a real family (so "nothing changed" is not a missing route).
        control = _post(client, ids["A"], token, decision="rejected")
        assert control.status_code == 302
        assert _read_families(_db_path(families_app))[ids["A"]]["status"] == "rejected"


# ---------------------------------------------------------------------------
# 4. Public display: only ACCEPTED families show up
# ---------------------------------------------------------------------------
# Labels are deliberately different from every member name, so "label on the page" can only come from the
# family banner / rollup row and "label absent" really proves the family is hidden.
#   family  status    members          active  expected banner text
#   D3      accepted  701 702 703        1     "3 committees" ... "1 active"
#   D4      accepted  761 762 763 764    2     "4 committees" ... "2 active"
#   MA      accepted  771 773            1     "2 committees" ... "1 active"
#   MP      pending   771 772                  inserted FIRST (lower family id) and contains 771 too: a lookup that
#                                              takes "the first family of this committee" lands on the pending one
#   DP      pending   711 712
#   DR      rejected  721 722
LABEL_D3 = "Dana Example Committee Family"
LABEL_D4 = "Harbor Quartet Family"
LABEL_MA = "Late Accepted Family"
LABEL_MP = "Early Pending Family"
LABEL_DP = "Pending Pal Family"
LABEL_DR = "Rejected Roster Family"
ALL_DISPLAY_LABELS = (LABEL_D3, LABEL_D4, LABEL_MA, LABEL_MP, LABEL_DP, LABEL_DR)

D3_MEMBERS = {701: "Friends of Dana Example", 702: "Dana Example for Senate", 703: "Citizens for Dana Example"}
D4_MEMBERS = {761: "Harbor Alpha Committee", 762: "Harbor Beta Committee",
              763: "Harbor Gamma Committee", 764: "Harbor Delta Committee"}
MIXED_MEMBERS = {771: "Mixed Pat One", 772: "Mixed Pat Two", 773: "Mixed Pat Three"}
DISPLAY_COMMITTEES = [
    (701, D3_MEMBERS[701], True), (702, D3_MEMBERS[702], False), (703, D3_MEMBERS[703], False),
    (761, D4_MEMBERS[761], True), (762, D4_MEMBERS[762], True),
    (763, D4_MEMBERS[763], False), (764, D4_MEMBERS[764], False),
    (771, MIXED_MEMBERS[771], True), (772, MIXED_MEMBERS[772], False), (773, MIXED_MEMBERS[773], False),
    (711, "Pending Pal One", True), (712, "Pending Pal Two", False),
    (721, "Rejected Rey One", True), (722, "Rejected Rey Two", False),
]
ALL_DISPLAY_IDS = {cid for cid, _, _ in DISPLAY_COMMITTEES}

# (receipt id, committee, received_date, amount, archived). Window used by the list test: 2025-01-01..2025-12-31.
#   701 $100 (the $7 archived row counts for nothing) | 702 $200 | 703 $0 (its $1,000 is from 2023, outside)
#   => D3 windowed total = 100 + 200 + 0 = $300.00
#   761 $11 | 762 $22 | 763 $0 | 764 $0 (its $5 is archived)  => D4 windowed total = $33.00
#   771 / 773 have no receipts => MA windowed total = $0.00
#   711 $40, 712 $60 (pending family: never summed)
DISPLAY_RECEIPTS = [
    (70101, 701, "2025-03-01", 100.0, False),
    (70102, 701, "2025-03-02", 7.0, True),
    (70201, 702, "2025-06-01", 200.0, False),
    (70301, 703, "2023-01-01", 1000.0, False),
    (76101, 761, "2025-02-01", 11.0, False),
    (76201, 762, "2025-02-02", 22.0, False),
    (76401, 764, "2025-02-03", 5.0, True),
    (71101, 711, "2025-02-02", 40.0, False),
    (71201, 712, "2025-02-03", 60.0, False),
]
NOISE_ROWS = 3             # committees 100 / 200 / 300 from _seed_isbe_tables
WINDOW = "date_from=2025-01-01&date_to=2025-12-31"


def _display_world(app):
    conn = get_db(_db_path(app))
    for committee_id, name, active in DISPLAY_COMMITTEES:
        _add_committee(conn, committee_id, name, active=active)
    conn.executemany(
        "INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name, received_date, "
        "amount, d2_part, archived) VALUES (?, ?, NULL, 'Donor', 'Dee', ?, ?, '1A', ?)",
        DISPLAY_RECEIPTS,
    )
    _add_family(conn, LABEL_MP, "pending", [771, 772])        # first on purpose: the lowest family id
    _add_family(conn, LABEL_MA, "accepted", [771, 773])
    _add_family(conn, LABEL_D3, "accepted", sorted(D3_MEMBERS))
    _add_family(conn, LABEL_D4, "accepted", sorted(D4_MEMBERS))
    _add_family(conn, LABEL_DP, "pending", [711, 712])
    _add_family(conn, LABEL_DR, "rejected", [721, 722])
    conn.commit()
    conn.close()


def _label_windows(page, label, width=240):
    """Visible text that follows each occurrence of ``label`` inside <main> (the banner's sentence)."""
    text = _main_text(page)
    return [text[m.end(): m.end() + width] for m in re.finditer(re.escape(label), text)]


def _has_count(row_text, label, count):
    """The member count shows up as a number of its own in the row (any wording), not inside a money amount."""
    stripped = re.sub(r"\$[\d,]+(\.\d+)?", " ", row_text.replace(label, " "))
    return re.search(rf"(?<![\w.]){count}(?![\w.])", stripped) is not None


def _committee_list_rows(page):
    """Body rows of the committees table (the table whose header row has a 'Name' column)."""
    parser = _TableParser()
    parser.feed(page)
    parser.close()
    for table in parser.tables:
        headers = [row for row in table if not row.has_td]
        if headers and any(cell.lower().startswith("name") for cell in headers[0].cells):
            return [row for row in table if row.has_td]
    raise AssertionError("no committees table (a table whose header has a Name column)")


class TestDisplay:
    @pytest.mark.parametrize("path", ["/committees/{id}", "/committees/sbe/{id}"])
    def test_member_of_an_accepted_family_sees_the_banner_with_counts_and_sibling_links(self, families_app, path):
        _display_world(families_app)
        client = families_app.test_client()
        cases = [
            (701, LABEL_D3, 3, 1, {702, 703}),
            (762, LABEL_D4, 4, 2, {761, 763, 764}),
            (771, LABEL_MA, 2, 1, {773}),         # also in the older PENDING family MP: only MA may show
        ]
        for committee_id, label, size, active, siblings in cases:
            response = client.get(path.format(id=committee_id))
            assert response.status_code == 200, (path, committee_id)
            page = response.get_data(as_text=True)
            windows = _label_windows(page, label)
            assert windows, f"family label {label!r} not shown on committee {committee_id}"
            sized = [w for w in windows if re.search(rf"\b{size}\s+committees\b", w)]
            assert sized, f"no '{size} committees' next to the label: {windows}"
            assert any(re.search(rf"\b{active}\s+active\b", w) for w in sized), f"no '{active} active': {sized}"
            linked = _linked_committee_ids(page)
            assert siblings <= linked, f"missing sibling links: {sorted(siblings - linked)}"
            strangers = (linked & ALL_DISPLAY_IDS) - siblings - {committee_id}
            assert not strangers, f"links to committees outside the family: {sorted(strangers)}"
            other_labels = [other for other in ALL_DISPLAY_LABELS if other != label and other in page]
            assert not other_labels, f"committee {committee_id} page also shows {other_labels}"

    def test_only_accepted_families_are_shown(self, families_app):
        _display_world(families_app)
        client = families_app.test_client()
        member_page = client.get("/committees/701").get_data(as_text=True)
        assert LABEL_D3 in member_page                   # positive companion: the feature is on for accepted ones
        pages = {}
        for committee_id in (711, 712, 721, 722, 772, 100):
            response = client.get(f"/committees/{committee_id}")
            assert response.status_code == 200, committee_id
            pages[committee_id] = response.get_data(as_text=True)
        for committee_id, page in pages.items():
            for label in ALL_DISPLAY_LABELS:
                assert label not in page, f"committee {committee_id} page shows {label!r}"
        assert 712 not in _linked_committee_ids(pages[711])      # pending siblings are not advertised
        assert 722 not in _linked_committee_ids(pages[721])      # nor are rejected ones
        assert 771 not in _linked_committee_ids(pages[772])      # 772 is only in the pending MP

    def test_by_family_collapses_accepted_members_and_default_stays_per_committee(self, families_app):
        _display_world(families_app)
        client = families_app.test_client()
        all_committees = NOISE_ROWS + len(DISPLAY_COMMITTEES)

        plain_response = client.get(f"/committees/?{WINDOW}")
        assert plain_response.status_code == 200
        plain = _committee_list_rows(plain_response.get_data(as_text=True))
        assert len(plain) == all_committees                                     # 3 + 14 = 17: one row per committee
        expected_totals = {701: "$100.00", 702: "$200.00", 703: "$0.00",
                           761: "$11.00", 762: "$22.00", 763: "$0.00", 764: "$0.00",
                           711: "$40.00", 712: "$60.00"}
        for committee_id, name, _ in DISPLAY_COMMITTEES:
            matching = [row for row in plain if name in row.text]
            assert len(matching) == 1, name
            if committee_id in expected_totals:
                assert expected_totals[committee_id] in matching[0].text, (name, matching[0].text)
        plain_text = " ".join(row.text for row in plain)
        for label in ALL_DISPLAY_LABELS:
            assert label not in plain_text
        assert "$300.00" not in plain_text
        assert "$33.00" not in plain_text                  # no rollup without ?by=family

        grouped_response = client.get(f"/committees/?{WINDOW}&by=family")
        assert grouped_response.status_code == 200
        grouped = _committee_list_rows(grouped_response.get_data(as_text=True))
        # D3 3 -> 1, D4 4 -> 1, MA {771,773} 2 -> 1; 772, the pending and the rejected families stay split.
        assert len(grouped) == all_committees - 2 - 3 - 1                       # 17 - 6 = 11
        family_rows = [
            (LABEL_D3, list(D3_MEMBERS.values()), 3, "$300.00"),
            (LABEL_D4, list(D4_MEMBERS.values()), 4, "$33.00"),
            (LABEL_MA, [MIXED_MEMBERS[771], MIXED_MEMBERS[773]], 2, "$0.00"),
        ]
        for label, names, size, total in family_rows:
            mentioning = [row for row in grouped if label in row.text or any(name in row.text for name in names)]
            assert len(mentioning) == 1, f"{label}: members must collapse into one row, got {len(mentioning)}"
            row = mentioning[0]
            assert label in row.text
            assert _has_count(row.text, label, size), f"member count {size} missing in {row.text!r}"
            assert total in row.text, f"windowed total {total} missing in {row.text!r}"
        for name in ("Pending Pal One", "Pending Pal Two", "Rejected Rey One", "Rejected Rey Two", MIXED_MEMBERS[772]):
            assert len([row for row in grouped if name in row.text]) == 1, name
        grouped_text = " ".join(row.text for row in grouped)
        for label in (LABEL_DP, LABEL_DR, LABEL_MP):
            assert label not in grouped_text


# ---------------------------------------------------------------------------
# 5. CROSS-LANE: candidate profile grouping.
#    Red until BOTH lane C (route /candidates/<id>, plan step C1) and S-C8 (grouping) have landed.
#    Today /candidates/<id> is a 404; after lane C alone it renders but has no group sub-heading.
#    The fixture reuses lane C's own row builder so the page has every table it reads.
# ---------------------------------------------------------------------------

LABEL_C_ACCEPTED = "Morgan Taylor Campaign Family"
LABEL_C_EARLY_PENDING = "Early Pending Candidate Family"      # {552, 553}, inserted BEFORE the accepted family
LABEL_C_PENDING = "Casey Reed Campaign Family"


def _candidate_family_app(tmp_path):
    try:
        from tests.test_candidate_profile import _seed_candidate_profile_rows
    except ImportError as exc:
        pytest.fail(f"cross-lane fixture unavailable (tests/test_candidate_profile.py): {exc}")
    db_path = str(tmp_path / "families_candidate.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _seed_candidate_profile_rows(conn)
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.execute(
        "INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party, "
        "city, state, zipcode, residence_county) VALUES "
        "(55, 'Taylor', 'Morgan', 'State Representative', 'Representative District', '12', 'Democratic', "
        "'Peoria', 'IL', '61602', 'Peoria'), "
        "(56, 'Reed', 'Casey', 'State Senator', 'Senate District', '9', 'Republican', 'Quincy', 'IL', '62301', 'Adams')"
    )
    committees = [
        (551, "Friends of Morgan Taylor", True), (552, "Morgan Taylor Victory Fund", False),
        (553, "Taylor Leadership PAC", True),
        (561, "Reed for Illinois", True), (562, "Casey Reed Committee", False),
    ]
    for committee_id, name, active in committees:
        _add_committee(conn, committee_id, name, active=active)
        conn.execute(
            "INSERT INTO isbe_most_recent_filings (committee_name, committee_id, committee_type, committee_active) "
            "VALUES (?, ?, 'Candidate', ?)",
            (name, committee_id, active),
        )
    _insert_links(conn, [(551, 55), (552, 55), (553, 55), (561, 56), (562, 56)])
    # Last activity orders the page, newest first: 551 (2026-03-01) > 553 (2025-06-01) > 552 (2024-01-01).
    # Ungrouped, the non-member 553 sits BETWEEN the two family members; grouped, they stay together.
    # (isbe_condensed_receipts is lane C's stand-in for the matview the page reads.)
    conn.executemany(
        "INSERT INTO isbe_condensed_receipts (id, committee_id, last_name, first_name, received_date, amount, "
        "d2_part, archived) VALUES (?, ?, 'Donor', 'Dee', ?, 10.0, '1A', FALSE)",
        [(55101, 551, "2026-03-01"), (55301, 553, "2025-06-01"), (55201, 552, "2024-01-01")],
    )
    _add_family(conn, LABEL_C_EARLY_PENDING, "pending", [552, 553])    # lower family id than the accepted one
    _add_family(conn, LABEL_C_ACCEPTED, "accepted", [551, 552])
    _add_family(conn, LABEL_C_PENDING, "pending", [561, 562])
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _headings(page, levels="2345"):
    """[(position, visible text)] of h2..h5 elements in document order."""
    found = []
    for match in re.finditer(rf"<h([{levels}])\b[^>]*>(.*?)</h\1>", page, re.S | re.I):
        found.append((match.start(), _visible_text(match.group(2))))
    return found


def _committee_link_positions(page):
    positions = {}
    for match in re.finditer(r'href="/committees/(\d+)(?:[?#][^"]*)?"', page):
        positions.setdefault(int(match.group(1)), []).append(match.start())
    return positions


class TestCandidateProfileGrouping:
    def test_accepted_family_members_render_under_one_labelled_subheading_pending_ones_do_not(self, tmp_path):
        client = _candidate_family_app(tmp_path).test_client()

        accepted = client.get("/candidates/55")
        assert accepted.status_code == 200, f"/candidates/55 -> {accepted.status_code} (lane C route not built yet?)"
        page = accepted.get_data(as_text=True)
        sub_headings = [(pos, text) for pos, text in _headings(page, "345") if LABEL_C_ACCEPTED in text]
        assert len(sub_headings) == 1, f"expected one h3-h5 sub-heading with the family label, got {sub_headings}"
        heading_pos = sub_headings[0][0]
        links = _committee_link_positions(page)
        for committee_id in (551, 552, 553):
            assert len(links.get(committee_id, [])) == 1, f"committee {committee_id} must be listed exactly once"
        first_member = min(links[551][0], links[552][0])
        last_member = max(links[551][0], links[552][0])
        assert heading_pos < first_member                                 # members come after their heading
        in_between = [pos for pos, _ in _headings(page) if heading_pos < pos < first_member]
        assert not in_between                                             # ... with no other heading between
        assert not (heading_pos < links[553][0] < last_member)            # the non-member is not swallowed by the group
        assert all(LABEL_C_EARLY_PENDING not in text for _, text in _headings(page))

        # Same page shape for a PENDING family: committees listed, but no group sub-heading and no label.
        pending = client.get("/candidates/56")
        assert pending.status_code == 200, f"/candidates/56 -> {pending.status_code}"
        pending_page = pending.get_data(as_text=True)
        pending_links = _committee_link_positions(pending_page)
        for committee_id in (561, 562):
            assert len(pending_links.get(committee_id, [])) == 1, f"committee {committee_id} must be listed once"
        assert all(LABEL_C_PENDING not in text for _, text in _headings(pending_page))
        assert LABEL_C_PENDING not in _main_text(pending_page)


# ---------------------------------------------------------------------------
# Accepted holes (named, not silently decided)
# ---------------------------------------------------------------------------
# * member_hash is checked to be a 40-char hex that CHANGES on remove_member; its input format ("sha1 of the
#   sorted member ids") is not pinned, so a random new hash passes and the removal is not checked to equal what
#   the seeder would compute for the reduced set.
# * Label precedence: the active flag and the latest creation_date agree in every fixture family, so an
#   implementation that ignores `active` (or ignores creation_date) passes. The task says "active OR later
#   creation_date"; scripts/committee_families.py orders active first.
# * A committee that sits in a pending AND an accepted family is covered on the banner, the committees list and
#   the candidate page; the admin pages and the seeder are not asked about it.
# * Banner position ("under the bar"), the candidate link inside the banner, and list sort order/links under
#   ?by=family are not pinned (layout, not an invariant).
# * Not pinned because the task did not list them: admin list `q` search, pagination, sort and status counts,
#   the weak-pair tab with its "create family" action, race-detail ?by=family rows, the `seed` subcommand of
#   scripts/committee_families.py, and what happens to a family left with 0 or 1 member after remove_member.
# * Re-seeding after a removal or after a family grows creates a NEW pending family for the new member set
#   (hash-based design); the tests neither pin nor forbid that.
