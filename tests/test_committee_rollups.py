"""S-PA (tests first): shared receipts predicate, committee donor/payee rollups,
ISBE-first committee route, OpenBook empty-list fix.

Plan: .claude/plans/profiles-races-dated-widgets.md  section 6.2 (S-PA), decisions J-S2 / J-S8
(6.3), design 2.1, journal rows J-P3..J-P5.

Contract encoded here (proposed signatures; the implementer may add keyword arguments):

* ``database.models.receipts_filter_sql(alias, parts="contributions") -> str``  (module level, no
  connection).  Columns referenced are the ``bulk_receipts_clean`` ones the legacy predicate uses:
  ``amount``, ``is_archived``, ``d2_part_code``.  ``contributions`` = amount > 0, not archived (NULL
  counts as current), d2 part blank/NULL or ``1%``; ``all`` = amount > 0, not archived;
  anything else raises ValueError.  ``Donor._bulk_receipts_base_filter_sql(conn, alias)`` must
  return the identical text (it is the shared predicate, not a second copy).
* ``webapp.routes.committees``:
    committee_donor_rollup(conn, committee_id_sbe, scope, *, limit=50, offset=0, sort_by="total",
                           sort_dir="desc", q=None) -> list of row mappings
        row keys: donor_key, donor_name, city, state, total_amount, contribution_count,
                  first_date, last_date
    committee_donor_rollup_count(conn, committee_id_sbe, scope, *, q=None) -> int
    committee_payee_rollup(... same arguments ...)
        row keys: payee_name, total_amount, transaction_count, first_date, last_date
    committee_payee_rollup_count(conn, committee_id_sbe, scope, *, q=None) -> int
  ``sort_by`` in {total, count, name, last}; ``sort_dir`` in {asc, desc}.  These row-key names are
  the ones ``committees/detail_sbe.html`` already iterates (donor_name, total_amount, ...), so the
  profile's Top-20 can be switched to the helpers without template changes.
* ``/committees/<id>`` resolves ISBE first (J-S8), legacy page only for ids absent from ISBE.
* ``/openbook/`` renders (200, zeroed summary) when ``openbook_vendor_match`` is absent.
* ``q`` = case-insensitive substring of the DISPLAYED name (first + last), bound as a parameter.
  Whether it also matches street/city is deliberately NOT pinned (brief: ``q="oak"`` -> 1 row; plan
  2.1: "name filter"; the Jane Doe fixture cannot satisfy both).
* The helpers return ``[]`` / ``0`` without raising when the receipts / expenditures relation is
  missing, like ``_bulk_committee_profile`` does today (CLAUDE.md: fresh DBs may lack tables).
* ``_bulk_committee_profile``'s ``top_donors`` / ``top_payees`` are the head (limit=20) of the rollups.

Every expected number is hand-computed in the comment next to the fixture row that produces it.
Fixture committees (all in the ISBE compat views, same approach as tests/test_profile_scope.py):
  500 Jane Doe fixture   501 other committee (leak check)   510 ordering   520 window edges
  530 name/key quirks    550 25 donors/payees (Top-20 cap)   560 ties        900 predicate rows
"""
from __future__ import annotations

import itertools
import re
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import pytest

import database.models as models
from database.connection import get_db, init_db
from database.models import Donor
from tests.test_isbe_rewire import (
    _create_candidate_finance_agg_view,
    _create_compat_views,
    _seed_isbe_tables,
)
from webapp.app import create_app
from webapp.routes import committees as committees_routes
from webapp.utils.time_scope import current_cycle, resolve_time_scope

MAIN, OTHER, ORDER, EDGE, NAMES, BIG, TIES, PRED = 500, 501, 510, 520, 530, 550, 560, 900

# (first, last, street, street2, city, state, zip).  The expected donor_key is the site formula
# LOWER(TRIM(first|last|street|street2|city|state|zip)) with NULL -> '' (database/models.py 787-795);
# the fixture builder asserts these literals against the SQL formula so they cannot drift silently.
JANE_MAIN = ("Jane", "Doe", "1 Main St", None, "Springfield", "IL", "62701")
JANE_OAK = ("Jane", "Doe", "9 Oak St", None, "Chicago", "IL", "60601")
OLD_TIMER = ("Old", "Timer", "5 Elm St", None, "Decatur", "IL", "62521")
FRIENDS = (None, "Friends of Someone", "10 Party Way", None, "Springfield", "IL", "62701")
OTHER_RECEIPTS = (None, "Other Receipts Inc", "11 Other Rd", None, "Peoria", "IL", "61602")

KEY_JANE_MAIN = "jane|doe|1 main st||springfield|il|62701"
KEY_JANE_OAK = "jane|doe|9 oak st||chicago|il|60601"
KEY_OLD_TIMER = "old|timer|5 elm st||decatur|il|62521"

# Committee 530: quirks the site formula treats literally.
JANE_ELM = ("Jane", "Doe", "2 Elm St", None, "Peoria", "IL", "61602")
JANE_ELM_UPPER = ("JANE", "DOE", "2 ELM ST", None, "PEORIA", "IL", "61602")      # case variant: same key
JANE_ELM_TRAILING = ("Jane ", "Doe", "2 Elm St", None, "Peoria", "IL", "61602")  # inner space: NOT same key
BIGCORP = (None, "BigCorp", "500 LaSalle", None, "Chicago", "IL", "60606")         # NULL first name
ACME_HOLDINGS = ("", "Acme Holdings", "7 Wacker Dr", "Suite 5", "Chicago", "IL", "60601")  # '' first, street2
KEY_JANE_ELM = "jane|doe|2 elm st||peoria|il|61602"
KEY_JANE_ELM_TRAILING = "jane |doe|2 elm st||peoria|il|61602"
KEY_BIGCORP = "|bigcorp|500 lasalle||chicago|il|60606"
KEY_ACME_HOLDINGS = "|acme holdings|7 wacker dr|suite 5|chicago|il|60601"

# Committee 520: window edges for cycle 2026 = 2025-01-01 .. 2026-12-31.
EDGE_FIRST = ("Edge", "First", "2 E St", None, "Peoria", "IL", "61602")
EDGE_LAST = ("Edge", "Last", "3 E St", None, "Peoria", "IL", "61602")
KEY_EDGE_FIRST = "edge|first|2 e st||peoria|il|61602"
KEY_EDGE_LAST = "edge|last|3 e st||peoria|il|61602"

ALL_HAND_COMPUTED_KEYS = (
    KEY_JANE_MAIN, KEY_JANE_OAK, KEY_OLD_TIMER, KEY_JANE_ELM, KEY_JANE_ELM_TRAILING,
    KEY_BIGCORP, KEY_ACME_HOLDINGS, KEY_EDGE_FIRST, KEY_EDGE_LAST,
)

# Committee 510: four donors/payees whose total/name/count/last-date orders all differ.
#   position  donor       payee      total  count  last gift/payment
#   A         Zed Alpha   Zeta Co     $500    1    2026-07-07
#   B         Amy Beta    Alpha Co    $300    4    2026-03-03   (4 x $75)
#   C         Mia Gamma   Mu Co       $200    3    2026-05-05   ($100 + $50 + $50; first 2025-12-05)
#   D         Bob Delta   Beta Co     $100    2    2026-09-09   (2 x $50)
ORDER_NAMES = {
    "donors": ["Zed Alpha", "Amy Beta", "Mia Gamma", "Bob Delta"],
    "payees": ["Zeta Co", "Alpha Co", "Mu Co", "Beta Co"],
}
# (sort_by, sort_dir) -> positions A=0 B=1 C=2 D=3 in expected order (all eight are distinct):
EXPECTED_POSITIONS = {
    ("total", "desc"): [0, 1, 2, 3],   # 500, 300, 200, 100
    ("total", "asc"): [3, 2, 1, 0],
    ("name", "asc"): [1, 3, 2, 0],     # Amy/Alpha, Bob/Beta, Mia/Mu, Zed/Zeta
    ("name", "desc"): [0, 2, 3, 1],
    ("count", "desc"): [1, 2, 3, 0],   # 4, 3, 2, 1
    ("count", "asc"): [0, 3, 2, 1],
    ("last", "desc"): [3, 0, 2, 1],    # 09-09, 07-07, 05-05, 03-03
    ("last", "asc"): [1, 2, 0, 3],
}

_KINDS = {
    "donors": {"rollup": "committee_donor_rollup", "count": "committee_donor_rollup_count",
               "name": "donor_name", "n": "contribution_count"},
    "payees": {"rollup": "committee_payee_rollup", "count": "committee_payee_rollup_count",
               "name": "payee_name", "n": "transaction_count"},
}

_next_id = itertools.count(70001)

_RECEIPT_SQL = """
    INSERT INTO isbe_receipts (id, committee_id, filed_doc_id, last_name, first_name, received_date,
        amount, d2_part, address1, address2, city, state, zipcode, archived)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_EXPENDITURE_SQL = """
    INSERT INTO isbe_expenditures (id, committee_id, filed_doc_id, last_name, first_name, expended_date,
        amount, purpose, d2_part, archived)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _need(module, name):
    """Fetch a not-yet-written product function; fail RED (not a collection error) when absent."""
    found = getattr(module, name, None)
    if found is None:
        pytest.fail(f"not implemented yet: {module.__name__}.{name}", pytrace=False)
    return found


def _api(kind: str):
    spec = _KINDS[kind]
    return _need(committees_routes, spec["rollup"]), _need(committees_routes, spec["count"]), spec


def _cycle_2026():
    return resolve_time_scope(default="all", axis="received", args={"cycle": "2026"})


def _all_time():
    return resolve_time_scope(default="all", axis="received", args={})


def _money(value) -> float:
    return round(float(value), 2)


def _day(value) -> date:
    assert value is not None, "date column came back NULL"
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _receipt(conn, committee, received, who, amount, part, archived=False, rid=None):
    first, last, street, street2, city, state, zipcode = who
    conn.execute(_RECEIPT_SQL, (rid or next(_next_id), committee, None, last, first, received,
                                amount, part, street, street2, city, state, zipcode, archived))


def _expenditure(conn, committee, expended, last, first, amount, part, archived=False):
    conn.execute(_EXPENDITURE_SQL, (next(_next_id), committee, None, last, first, expended,
                                    amount, "fixture", part, archived))


# ---------------------------------------------------------------------------
# fixture data
# ---------------------------------------------------------------------------

def _seed_main(conn):
    """Committee 500 receipts + expenditures (cycle 2026 = 2025-01-01..2026-12-31)."""
    # Kept, inside cycle 2026:
    _receipt(conn, MAIN, "2026-02-01", JANE_MAIN, 100.0, "1A")      # Jane/1 Main
    _receipt(conn, MAIN, "2026-03-01", JANE_MAIN, 50.0, None)       # Jane/1 Main, NULL part == blank
    _receipt(conn, MAIN, "2026-04-01", JANE_OAK, 75.0, "1A")        # Jane/9 Oak
    # Kept under "all" only (outside the window):
    _receipt(conn, MAIN, "2023-05-01", JANE_MAIN, 40.0, "")         # Jane/1 Main, blank part
    _receipt(conn, MAIN, "2022-01-01", OLD_TIMER, 10.0, "1B")       # Old Timer, part 1B matches '1%'
    # Never a donor contribution (each would distort a total, count or first/last date if kept):
    _receipt(conn, MAIN, "2026-03-15", JANE_MAIN, 999.0, "1A", archived=True)   # archived
    _receipt(conn, MAIN, "2026-01-15", JANE_MAIN, 0.0, "1A")                    # amount 0
    _receipt(conn, MAIN, "2026-03-20", JANE_MAIN, -20.0, "1A")                  # negative (refund)
    _receipt(conn, MAIN, "2026-05-01", FRIENDS, 5000.0, "2A")                   # transfer in
    _receipt(conn, MAIN, "2026-05-02", OTHER_RECEIPTS, 700.0, "5A")             # other receipts
    # Another committee: Jane/9 Oak gave it $1,000 in the window (committee 500 must not see it).
    _receipt(conn, OTHER, "2026-05-05", JANE_OAK, 1000.0, "1A")
    # Expected committee 500 donors, cycle 2026:
    #   Jane/1 Main  100 + 50 = 150, count 2, first 2026-02-01, last 2026-03-01
    #   Jane/9 Oak   75, count 1, 2026-04-01
    # Expected, all time: Jane/1 Main 100+50+40 = 190 x3 (first 2023-05-01, last 2026-03-01),
    #   Jane/9 Oak 75 x1, Old Timer 10 x1 (2022-01-01).

    # Payees (committee 500).  Part codes vary on purpose: payees have NO d2-part clause.
    _expenditure(conn, MAIN, "2026-02-10", "ACME Printing", None, 100.0, "7A")
    _expenditure(conn, MAIN, "2026-03-10", "ACME Printing", None, 200.0, "9A")
    _expenditure(conn, MAIN, "2026-04-10", "Acme  printing", None, 50.0, None)  # two spaces, other case
    _expenditure(conn, MAIN, "2023-05-05", "ACME Printing", None, 75.0, "7A")   # outside the window
    _expenditure(conn, MAIN, "2026-05-05", "ACME Printing", None, 999.0, "7A", archived=True)
    _expenditure(conn, MAIN, "2026-05-06", "ACME Printing", None, 0.0, "7A")    # amount 0
    _expenditure(conn, MAIN, "2026-05-07", "ACME Printing", None, -30.0, "7A")  # negative
    _expenditure(conn, MAIN, "2026-03-15", "Beta Mail", None, 500.0, "7A")
    _expenditure(conn, MAIN, "2026-06-06", "Quinn", "Pat", 20.0, "7A")          # first + last name
    _expenditure(conn, MAIN, "2022-02-02", "Old Vendor", None, 15.0, "7A")      # all time only
    _expenditure(conn, OTHER, "2026-05-05", "ACME Printing", None, 1000.0, "7A")  # other committee
    # Expected committee 500 payees, cycle 2026: Beta Mail 500 x1 (2026-03-15); ACME 100+200+50
    #   = 350 x3 (first 2026-02-10, last 2026-04-10); Pat Quinn 20 x1 (2026-06-06).
    # Expected, all time: Beta Mail 500; ACME 350+75 = 425 x4 (first 2023-05-05, last 2026-04-10);
    #   Pat Quinn 20; Old Vendor 15 (2022-02-02).


def _seed_order(conn):
    """Committee 510: orders differ per sort key (see ORDER_NAMES table above)."""
    gifts = {
        ("Zed", "Alpha", "1 A St"): [(500.0, "2026-07-07")],
        ("Amy", "Beta", "2 B St"): [(75.0, "2026-01-03"), (75.0, "2026-01-13"),
                                    (75.0, "2026-02-03"), (75.0, "2026-03-03")],
        ("Mia", "Gamma", "3 C St"): [(100.0, "2025-12-05"), (50.0, "2026-04-05"), (50.0, "2026-05-05")],
        ("Bob", "Delta", "4 D St"): [(50.0, "2026-08-08"), (50.0, "2026-09-09")],
    }
    for (first, last, street), rows in gifts.items():
        for amount, received in rows:
            _receipt(conn, ORDER, received, (first, last, street, None, "Peoria", "IL", "61602"), amount, "1A")
    payments = {
        "Zeta Co": [(500.0, "2026-07-07")],
        "Alpha Co": [(75.0, "2026-01-03"), (75.0, "2026-01-13"), (75.0, "2026-02-03"), (75.0, "2026-03-03")],
        "Mu Co": [(100.0, "2025-12-05"), (50.0, "2026-04-05"), (50.0, "2026-05-05")],
        "Beta Co": [(50.0, "2026-08-08"), (50.0, "2026-09-09")],
    }
    for payee, rows in payments.items():
        for amount, expended in rows:
            _expenditure(conn, ORDER, expended, payee, None, amount, "7A")


def _seed_edges(conn):
    """Committee 520: one gift/payment just outside each end of cycle 2026, one just inside."""
    early = ("Edge", "Early", "1 E St", None, "Peoria", "IL", "61602")
    late = ("Edge", "Late", "4 E St", None, "Peoria", "IL", "61602")
    _receipt(conn, EDGE, "2024-12-31", early, 11.0, "1A")               # out: day before the window
    _receipt(conn, EDGE, "2025-01-01", EDGE_FIRST, 22.0, "1A")          # in: first day
    _receipt(conn, EDGE, "2026-12-31 15:30:00", EDGE_LAST, 33.0, "1A")  # in: last day, time of day present
    _receipt(conn, EDGE, "2027-01-01", late, 44.0, "1A")                # out: day after the window
    _expenditure(conn, EDGE, "2024-12-31", "Edge Early", None, 11.0, "7A")
    _expenditure(conn, EDGE, "2025-01-01", "Edge First", None, 22.0, "7A")
    _expenditure(conn, EDGE, "2026-12-31 15:30:00", "Edge Last", None, 33.0, "7A")
    _expenditure(conn, EDGE, "2027-01-01", "Edge Late", None, 44.0, "7A")
    # Cycle 2026 expected: Edge Last 33, Edge First 22 (in that total-desc order).  All time: 4 rows.


def _seed_names(conn):
    """Committee 530, all gifts 2026-01..02, part 1A."""
    _receipt(conn, NAMES, "2026-01-10", JANE_ELM, 10.0, "1A")
    _receipt(conn, NAMES, "2026-01-11", JANE_ELM_UPPER, 20.0, "1A")      # same key as JANE_ELM -> one row 30 x2
    _receipt(conn, NAMES, "2026-02-02", BIGCORP, 300.0, "1A")
    _receipt(conn, NAMES, "2026-02-03", ACME_HOLDINGS, 400.0, "1A")
    _receipt(conn, NAMES, "2026-02-04", JANE_ELM_TRAILING, 5.0, "1A")     # distinct key (site formula is literal)
    # Expected rows (total desc): Acme Holdings 400 x1, BigCorp 300 x1, Jane Doe 30 x2, 'Jane  Doe' 5 x1.


def _seed_big(conn):
    """Committee 550: Donor01..Donor25 give i * $10; Payee01..Payee25 receive i * $10 (Top-20 cap)."""
    for i in range(1, 26):
        who = ("Dee", f"Donor{i:02d}", f"{i} Big St", None, "Peoria", "IL", "61602")
        _receipt(conn, BIG, "2026-03-03", who, i * 10.0, "1A")
        _expenditure(conn, BIG, "2026-03-03", f"Payee{i:02d}", None, i * 10.0, "7A")
    # Top 20 by total = i 25 down to 6 = 250, 240, ..., 60.


TIE_ROWS, TIE_PAGE = 60, 10


def _seed_ties(conn):
    """Committee 560: 60 donors and 60 payees that tie on total, count and last date."""
    for i in range(1, TIE_ROWS + 1):
        who = ("Tie", f"Number{i:02d}", f"{i} Tie St", None, "Peoria", "IL", "61602")
        _receipt(conn, TIES, "2026-04-04", who, 10.0, "1A")
        _expenditure(conn, TIES, "2026-04-04", f"Tie Vendor {i:02d}", None, 10.0, "7A")


def _seed_predicate_rows(conn):
    """Committee 900: rows with explicit ids, the table the predicate tests run against."""
    who = ("Pred", "Tester", "900 Pred St", None, "Peoria", "IL", "61602")
    rows = [
        # id, amount, d2 part, archived                          contributions / all
        (9001, 10.0, "1A", False),    # keep / keep
        (9002, 20.0, None, False),    # NULL part = blank: keep / keep
        (9003, 30.0, "", False),      # blank part: keep / keep
        (9004, 40.0, "1A", None),     # NULL archived counts as current: keep / keep
        (9005, 50.0, "1B", False),    # matches '1%': keep / keep
        (9011, 100.0, "1A", True),    # archived: drop / drop
        (9012, 0.0, "1A", False),     # zero: drop / drop
        (9013, -50.0, "1A", False),   # negative: drop / drop
        (9014, 200.0, "2A", False),   # transfer in: drop / keep
        (9015, 300.0, "5A", False),   # other receipts: drop / keep
    ]
    for rid, amount, part, archived in rows:
        _receipt(conn, PRED, "2026-02-02", who, amount, part, archived=archived, rid=rid)


CONTRIBUTION_IDS = [9001, 9002, 9003, 9004, 9005]
ALL_PARTS_IDS = [9001, 9002, 9003, 9004, 9005, 9014, 9015]


def _assert_literal_keys_match_site_formula(conn):
    rows = conn.execute(
        f"SELECT DISTINCT {Donor._bulk_donor_key_sql('r')} AS donor_key FROM bulk_receipts_clean r"
    ).fetchall()
    site_keys = {row["donor_key"] for row in rows}
    for literal in ALL_HAND_COMPUTED_KEYS:
        assert literal in site_keys, f"hand-computed key drifted from the site formula: {literal!r}"


@pytest.fixture
def rollup_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "committee_rollups.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active)
        VALUES (500, 'Rollup Fixture PAC', 'Political Action', 'Springfield', 'IL', '62701', TRUE),
               (550, 'Big Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE)
        """
    )
    for seed in (_seed_main, _seed_order, _seed_edges, _seed_names, _seed_big, _seed_ties,
                 _seed_predicate_rows):
        seed(conn)
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    _assert_literal_keys_match_site_formula(conn)
    conn.close()
    return db_path


@pytest.fixture
def rollup_conn(rollup_db):
    conn = get_db(rollup_db)
    yield conn
    conn.close()


@pytest.fixture
def rollup_app(rollup_db):
    return create_app({"TESTING": True, "DATABASE_PATH": rollup_db,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


# ---------------------------------------------------------------------------
# A. shared receipts predicate (A3 / J-S2)
# ---------------------------------------------------------------------------

def _predicate_ids(conn, predicate: str) -> list[int]:
    # alias "rcp" on purpose: an implementation that hard-codes "r." cannot run.
    rows = conn.execute(
        "SELECT rcp.bulk_row_id AS rid FROM bulk_receipts_clean rcp "
        f"WHERE rcp.committee_id_sbe = ? AND ({predicate}) ORDER BY rcp.bulk_row_id",
        (PRED,),
    ).fetchall()
    return [row["rid"] for row in rows]


class TestReceiptsFilterSql:
    def test_contributions_keeps_only_real_contributions(self, rollup_conn):
        receipts_filter_sql = _need(models, "receipts_filter_sql")
        kept = _predicate_ids(rollup_conn, receipts_filter_sql("rcp"))
        assert kept == CONTRIBUTION_IDS

    def test_default_parts_is_contributions(self):
        receipts_filter_sql = _need(models, "receipts_filter_sql")
        assert receipts_filter_sql("rcp") == receipts_filter_sql("rcp", parts="contributions")

    def test_all_parts_drops_only_archived_and_non_positive(self, rollup_conn):
        receipts_filter_sql = _need(models, "receipts_filter_sql")
        kept = _predicate_ids(rollup_conn, receipts_filter_sql("rcp", parts="all"))
        assert kept == ALL_PARTS_IDS

    def test_unknown_parts_value_raises_value_error(self):
        receipts_filter_sql = _need(models, "receipts_filter_sql")
        with pytest.raises(ValueError):
            receipts_filter_sql("r", parts="bogus")

    def test_legacy_donor_predicate_is_the_same_text_and_selects_the_same_rows(self, rollup_conn):
        receipts_filter_sql = _need(models, "receipts_filter_sql")
        legacy = Donor._bulk_receipts_base_filter_sql(rollup_conn, "rcp")
        assert legacy == receipts_filter_sql("rcp")
        assert _predicate_ids(rollup_conn, legacy) == CONTRIBUTION_IDS


# ---------------------------------------------------------------------------
# B. donor rollup, committee 500 (the Jane Doe fixture)
# ---------------------------------------------------------------------------

class TestDonorRollupJane:
    def test_cycle_scope_has_exactly_the_two_namesake_rows_keyed_by_site_formula(self, rollup_conn):
        rollup, _, _ = _api("donors")
        rows = rollup(rollup_conn, MAIN, _cycle_2026())
        assert [row["donor_key"] for row in rows] == [KEY_JANE_MAIN, KEY_JANE_OAK]

    def test_cycle_scope_totals_counts_and_dates(self, rollup_conn):
        rollup, _, _ = _api("donors")
        by_key = {row["donor_key"]: row for row in rollup(rollup_conn, MAIN, _cycle_2026())}
        assert KEY_JANE_MAIN in by_key
        assert KEY_JANE_OAK in by_key
        main, oak = by_key[KEY_JANE_MAIN], by_key[KEY_JANE_OAK]
        assert _money(main["total_amount"]) == 150.0
        assert int(main["contribution_count"]) == 2
        assert _day(main["first_date"]) == date(2026, 2, 1)
        assert _day(main["last_date"]) == date(2026, 3, 1)
        assert _money(oak["total_amount"]) == 75.0
        assert int(oak["contribution_count"]) == 1
        assert _day(oak["first_date"]) == date(2026, 4, 1)
        assert _day(oak["last_date"]) == date(2026, 4, 1)

    def test_display_name_and_location_are_as_filed(self, rollup_conn):
        rollup, _, _ = _api("donors")
        main, oak = rollup(rollup_conn, MAIN, _cycle_2026())
        assert main["donor_name"] == "Jane Doe"
        assert main["city"] == "Springfield"
        assert main["state"] == "IL"
        assert oak["donor_name"] == "Jane Doe"
        assert oak["city"] == "Chicago"
        assert oak["state"] == "IL"

    def test_all_time_scope_adds_the_out_of_window_rows(self, rollup_conn):
        rollup, _, _ = _api("donors")
        rows = rollup(rollup_conn, MAIN, _all_time())
        assert [row["donor_key"] for row in rows] == [KEY_JANE_MAIN, KEY_JANE_OAK, KEY_OLD_TIMER]
        main, oak, old = rows
        assert _money(main["total_amount"]) == 190.0
        assert int(main["contribution_count"]) == 3
        assert _day(main["first_date"]) == date(2023, 5, 1)
        assert _day(main["last_date"]) == date(2026, 3, 1)
        assert _money(oak["total_amount"]) == 75.0
        assert int(oak["contribution_count"]) == 1
        assert _money(old["total_amount"]) == 10.0
        assert int(old["contribution_count"]) == 1
        assert _day(old["first_date"]) == date(2022, 1, 1)

    def test_other_committees_are_a_separate_rollup(self, rollup_conn):
        rollup, _, _ = _api("donors")
        rows = rollup(rollup_conn, OTHER, _cycle_2026())
        assert [row["donor_key"] for row in rows] == [KEY_JANE_OAK]
        assert _money(rows[0]["total_amount"]) == 1000.0
        assert rollup(rollup_conn, 987654, _all_time()) == []


# ---------------------------------------------------------------------------
# B. payee rollup, committee 500
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    return " ".join(name.lower().split())


class TestPayeeRollupAcme:
    def test_cycle_scope_name_variants_collapse_to_one_payee(self, rollup_conn):
        rollup, _, _ = _api("payees")
        rows = rollup(rollup_conn, MAIN, _cycle_2026())
        assert [_norm(row["payee_name"]) for row in rows] == ["beta mail", "acme printing", "pat quinn"]

    def test_cycle_scope_totals_counts_and_dates(self, rollup_conn):
        rollup, _, _ = _api("payees")
        beta, acme, quinn = rollup(rollup_conn, MAIN, _cycle_2026())
        assert _money(beta["total_amount"]) == 500.0
        assert int(beta["transaction_count"]) == 1
        assert _money(acme["total_amount"]) == 350.0
        assert int(acme["transaction_count"]) == 3
        assert _day(acme["first_date"]) == date(2026, 2, 10)
        assert _day(acme["last_date"]) == date(2026, 4, 10)
        assert _money(quinn["total_amount"]) == 20.0
        assert int(quinn["transaction_count"]) == 1

    def test_display_name_is_a_filed_spelling_and_composes_first_and_last(self, rollup_conn):
        rollup, _, _ = _api("payees")
        beta, acme, quinn = rollup(rollup_conn, MAIN, _cycle_2026())
        assert beta["payee_name"] == "Beta Mail"
        assert acme["payee_name"] in ("ACME Printing", "Acme  printing")
        assert quinn["payee_name"] == "Pat Quinn"

    def test_all_time_scope_adds_the_out_of_window_rows(self, rollup_conn):
        rollup, _, _ = _api("payees")
        rows = rollup(rollup_conn, MAIN, _all_time())
        assert [_norm(row["payee_name"]) for row in rows] == [
            "beta mail", "acme printing", "pat quinn", "old vendor",
        ]
        acme = rows[1]
        assert _money(acme["total_amount"]) == 425.0
        assert int(acme["transaction_count"]) == 4
        assert _day(acme["first_date"]) == date(2023, 5, 5)
        assert _day(acme["last_date"]) == date(2026, 4, 10)
        assert _money(rows[3]["total_amount"]) == 15.0

    def test_other_committees_are_a_separate_rollup(self, rollup_conn):
        rollup, _, _ = _api("payees")
        rows = rollup(rollup_conn, OTHER, _cycle_2026())
        assert [_norm(row["payee_name"]) for row in rows] == ["acme printing"]
        assert _money(rows[0]["total_amount"]) == 1000.0


# ---------------------------------------------------------------------------
# B. shared behaviour of both rollups: count twin, sort, pagination, q, edges, ties, hostile args
# ---------------------------------------------------------------------------

class TestRollupCounts:
    @pytest.mark.parametrize("kind, committee, scope_name, expected", [
        ("donors", MAIN, "cycle", 2),     # Jane/1 Main, Jane/9 Oak
        ("donors", MAIN, "all", 3),       # + Old Timer
        ("donors", NAMES, "all", 4),      # 5 receipts, 4 donor keys (case variants merge)
        ("donors", ORDER, "all", 4),
        ("donors", 987654, "all", 0),
        ("payees", MAIN, "cycle", 3),     # Beta Mail, ACME (3 spellings), Pat Quinn
        ("payees", MAIN, "all", 4),       # + Old Vendor
        ("payees", ORDER, "all", 4),
    ])
    def test_count_twin_counts_unique_rows_in_scope(self, rollup_conn, kind, committee, scope_name, expected):
        _, count, _ = _api(kind)
        scope = _cycle_2026() if scope_name == "cycle" else _all_time()
        assert count(rollup_conn, committee, scope) == expected

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_count_twin_equals_row_count_when_everything_fits(self, rollup_conn, kind):
        rollup, count, _ = _api(kind)
        rows = rollup(rollup_conn, BIG, _all_time(), limit=1000)
        assert len(rows) == 25
        assert count(rollup_conn, BIG, _all_time()) == 25
        assert len(rollup(rollup_conn, BIG, _all_time())) == 25      # the default page holds all 25


@pytest.fixture
def bare_conn(tmp_path: Path):
    """A fresh database: init_db only, no ISBE tables and no bulk_*_clean views."""
    db_path = str(tmp_path / "bare.db")
    init_db(db_path)
    conn = get_db(db_path)
    for relation in ("bulk_receipts_clean", "bulk_expenditures_clean",
                     "isbe_condensed_receipts", "isbe_condensed_expenditures"):
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (relation,)
        ).fetchone()
        assert present is None, f"fixture: {relation} should not exist on a fresh database"
    yield conn
    conn.close()


class TestRollupOnDatabaseWithoutReceiptTables:
    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_missing_source_relations_give_an_empty_rollup_not_an_error(self, bare_conn, kind):
        rollup, count, _ = _api(kind)
        assert rollup(bare_conn, MAIN, _all_time()) == []
        assert count(bare_conn, MAIN, _all_time()) == 0


class TestRollupOrdering:
    @pytest.mark.parametrize("kind", ["donors", "payees"])
    @pytest.mark.parametrize("sort_by, sort_dir", list(EXPECTED_POSITIONS))
    def test_each_sort_key_and_direction_has_its_own_order(self, rollup_conn, kind, sort_by, sort_dir):
        rollup, _, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time(), sort_by=sort_by, sort_dir=sort_dir)
        names = ORDER_NAMES[kind]
        expected = [names[position] for position in EXPECTED_POSITIONS[(sort_by, sort_dir)]]
        assert [row[spec["name"]] for row in rows] == expected

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_default_order_is_total_descending(self, rollup_conn, kind):
        rollup, _, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time())
        assert [row[spec["name"]] for row in rows] == ORDER_NAMES[kind]
        assert [_money(row["total_amount"]) for row in rows] == [500.0, 300.0, 200.0, 100.0]
        assert [int(row[spec["n"]]) for row in rows] == [1, 4, 3, 2]

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_last_date_column_is_the_most_recent_in_window(self, rollup_conn, kind):
        rollup, _, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time(), sort_by="last", sort_dir="desc")
        assert [_day(row["last_date"]) for row in rows] == [
            date(2026, 9, 9), date(2026, 7, 7), date(2026, 5, 5), date(2026, 3, 3),
        ]
        # first gifts in last-desc order D, A, C, B: sorting by first date would give D, A, B, C
        assert [_day(row["first_date"]) for row in rows] == [
            date(2026, 8, 8), date(2026, 7, 7), date(2025, 12, 5), date(2026, 1, 3),
        ]


class TestRollupPagination:
    @pytest.mark.parametrize("kind", ["donors", "payees"])
    @pytest.mark.parametrize("limit, offset, positions", [
        (2, 0, [0, 1]),
        (2, 2, [2, 3]),
        (2, 4, []),
        (3, 1, [1, 2, 3]),
        (1, 3, [3]),
    ])
    def test_limit_and_offset_slice_the_sorted_list(self, rollup_conn, kind, limit, offset, positions):
        rollup, _, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time(), limit=limit, offset=offset)
        names = ORDER_NAMES[kind]
        assert [row[spec["name"]] for row in rows] == [names[p] for p in positions]

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_pagination_follows_the_requested_sort(self, rollup_conn, kind):
        rollup, _, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time(), limit=2, offset=2, sort_by="name", sort_dir="asc")
        names = ORDER_NAMES[kind]
        assert [row[spec["name"]] for row in rows] == [names[2], names[0]]   # 3rd and 4th by name

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    @pytest.mark.parametrize("sort_by", ["total", "count", "last"])
    def test_pages_of_tied_rows_never_overlap_or_skip(self, rollup_conn, kind, sort_by):
        rollup, _, spec = _api(kind)
        pages = [
            rollup(rollup_conn, TIES, _all_time(), limit=TIE_PAGE, offset=offset, sort_by=sort_by, sort_dir="desc")
            for offset in range(0, TIE_ROWS, TIE_PAGE)
        ]
        assert [len(page) for page in pages] == [TIE_PAGE] * (TIE_ROWS // TIE_PAGE)
        seen = [row[spec["name"]] for page in pages for row in page]
        assert len(set(seen)) == TIE_ROWS


class TestRollupNameFilter:
    @pytest.mark.parametrize("kind, q, positions", [
        ("donors", "m", [1, 2]),            # Amy Beta, Mia Gamma (substring, not prefix)
        ("donors", "MY BE", [1]),           # case-insensitive, spans first and last name
        ("donors", "zzz", []),
        ("donors", "o'brien", []),          # a quote must be bound, not interpolated
        ("payees", "ETA", [0, 3]),          # Zeta Co, Beta Co
        ("payees", "zzz", []),
        ("payees", "o'brien", []),
    ])
    def test_q_filters_on_the_displayed_name_and_the_count_twin_agrees(self, rollup_conn, kind, q, positions):
        rollup, count, spec = _api(kind)
        rows = rollup(rollup_conn, ORDER, _all_time(), q=q)
        names = ORDER_NAMES[kind]
        assert [row[spec["name"]] for row in rows] == [names[p] for p in positions]
        assert count(rollup_conn, ORDER, _all_time(), q=q) == len(positions)

    def test_q_applies_before_pagination(self, rollup_conn):
        rollup, _, _ = _api("donors")
        rows = rollup(rollup_conn, ORDER, _all_time(), q="m", limit=1, offset=1)
        assert [row["donor_name"] for row in rows] == ["Mia Gamma"]   # 2nd of [Amy Beta 300, Mia Gamma 200]

    def test_q_respects_the_scope_window(self, rollup_conn):
        rollup, count, _ = _api("donors")
        # Old Timer gave only in 2022: visible all-time, invisible in cycle 2026.
        assert [row["donor_key"] for row in rollup(rollup_conn, MAIN, _all_time(), q="timer")] == [KEY_OLD_TIMER]
        assert rollup(rollup_conn, MAIN, _cycle_2026(), q="timer") == []
        assert count(rollup_conn, MAIN, _cycle_2026(), q="timer") == 0


class TestRollupWindowEdges:
    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_cycle_window_is_inclusive_and_ignores_time_of_day(self, rollup_conn, kind):
        rollup, count, spec = _api(kind)
        rows = rollup(rollup_conn, EDGE, _cycle_2026())
        assert [row[spec["name"]] for row in rows] == ["Edge Last", "Edge First"]
        assert [_money(row["total_amount"]) for row in rows] == [33.0, 22.0]
        assert _day(rows[0]["last_date"]) == date(2026, 12, 31)
        assert count(rollup_conn, EDGE, _cycle_2026()) == 2

    @pytest.mark.parametrize("kind", ["donors", "payees"])
    def test_all_time_scope_has_no_window(self, rollup_conn, kind):
        rollup, count, spec = _api(kind)
        rows = rollup(rollup_conn, EDGE, _all_time())
        assert [row[spec["name"]] for row in rows] == ["Edge Late", "Edge Last", "Edge First", "Edge Early"]
        assert count(rollup_conn, EDGE, _all_time()) == 4


class TestRollupHostileArguments:
    @pytest.mark.parametrize("kind", ["donors", "payees"])
    @pytest.mark.parametrize("bad", [
        {"sort_by": "total; DROP TABLE isbe_receipts; --"},
        {"sort_by": "bogus_column"},
        {"sort_dir": "desc; DROP TABLE isbe_receipts; --"},
        {"sort_dir": "sideways"},
    ])
    def test_sort_arguments_never_reach_sql(self, rollup_conn, kind, bad):
        rollup, _, spec = _api(kind)
        before = rollup_conn.execute("SELECT COUNT(*) AS n FROM isbe_receipts").fetchone()["n"]
        try:
            rows = rollup(rollup_conn, ORDER, _all_time(), **bad)
        except ValueError:
            rows = None          # an explicit whitelist rejection is fine
        if rows is not None:     # so is falling back to a default order, but never a partial answer
            assert sorted(row[spec["name"]] for row in rows) == sorted(ORDER_NAMES[kind])
        after = rollup_conn.execute("SELECT COUNT(*) AS n FROM isbe_receipts").fetchone()["n"]
        assert after == before


class TestDonorKeyIsTheSiteFormulaVerbatim:
    """Committee 530: what the formula does with case, NULLs, blanks and inner spaces."""

    def test_keys_and_merge_behaviour(self, rollup_conn):
        rollup, _, _ = _api("donors")
        rows = rollup(rollup_conn, NAMES, _all_time())
        assert [row["donor_key"] for row in rows] == [
            KEY_ACME_HOLDINGS, KEY_BIGCORP, KEY_JANE_ELM, KEY_JANE_ELM_TRAILING,
        ]
        assert [_money(row["total_amount"]) for row in rows] == [400.0, 300.0, 30.0, 5.0]
        assert [int(row["contribution_count"]) for row in rows] == [1, 1, 2, 1]

    def test_business_donors_show_their_name_without_a_phantom_first_name(self, rollup_conn):
        rollup, _, _ = _api("donors")
        by_key = {row["donor_key"]: row for row in rollup(rollup_conn, NAMES, _all_time())}
        assert by_key[KEY_BIGCORP]["donor_name"] == "BigCorp"              # first name NULL
        assert by_key[KEY_ACME_HOLDINGS]["donor_name"] == "Acme Holdings"  # first name ''

    def test_case_variants_of_one_donor_are_one_row(self, rollup_conn):
        rollup, _, _ = _api("donors")
        by_key = {row["donor_key"]: row for row in rollup(rollup_conn, NAMES, _all_time())}
        merged = by_key[KEY_JANE_ELM]
        assert merged["donor_name"].lower() == "jane doe"
        assert merged["city"].lower() == "peoria"
        assert _day(merged["first_date"]) == date(2026, 1, 10)
        assert _day(merged["last_date"]) == date(2026, 1, 11)


# ---------------------------------------------------------------------------
# C. reconciliation with the donor profile (the point of A3)
# ---------------------------------------------------------------------------

def _donor_summary(conn, key, scope):
    return Donor.get_summary_by_key(
        conn, key, source="bulk_receipts",
        date_from=scope.start.isoformat(), date_to=scope.end.isoformat(),
    )


class TestRollupReconcilesWithDonorProfile:
    @pytest.mark.parametrize("committee, key, expected_total", [
        (MAIN, KEY_JANE_MAIN, 150.0),     # gave only to committee 500 in the window
        (EDGE, KEY_EDGE_FIRST, 22.0),     # first day of the window
        (EDGE, KEY_EDGE_LAST, 33.0),      # last day, with a time of day
    ])
    def test_rollup_total_equals_the_donor_page_windowed_total(self, rollup_conn, committee, key, expected_total):
        rollup, _, _ = _api("donors")
        scope = _cycle_2026()
        rolled = {row["donor_key"]: row for row in rollup(rollup_conn, committee, scope)}
        assert key in rolled
        summary = _donor_summary(rollup_conn, key, scope)
        assert summary is not None
        assert _money(summary.total_amount) == expected_total
        assert _money(rolled[key]["total_amount"]) == _money(summary.total_amount)
        assert int(rolled[key]["contribution_count"]) == int(summary.contribution_count)

    def test_rollup_row_equals_the_donor_page_committee_breakdown_row(self, rollup_conn):
        rollup, _, _ = _api("donors")
        scope = _cycle_2026()
        breakdown = Donor.get_committee_breakdown_by_key(
            rollup_conn, KEY_JANE_OAK, "bulk_receipts",
            date_from=scope.start.isoformat(), date_to=scope.end.isoformat(),
        )
        donor_page = {str(row["committee_id"]): (_money(row["total_amount"]), int(row["contribution_count"]))
                      for row in breakdown}
        assert donor_page == {"500": (75.0, 1), "501": (1000.0, 1)}
        for committee in (MAIN, OTHER):
            rows = {row["donor_key"]: row for row in rollup(rollup_conn, committee, scope)}
            row = rows[KEY_JANE_OAK]
            assert (_money(row["total_amount"]), int(row["contribution_count"])) == donor_page[str(committee)]

    def test_rollup_key_opens_the_donor_page_showing_the_same_total(self, rollup_conn, rollup_app):
        rollup, _, _ = _api("donors")
        first = rollup(rollup_conn, MAIN, _cycle_2026())[0]
        url = f"/donors/key/{quote(first['donor_key'], safe='')}?source=bulk_receipts&period=cycle:2026"
        resp = rollup_app.test_client().get(url)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Jane Doe" in html
        assert "Total Contributed:</strong> $150.00" in html


# ---------------------------------------------------------------------------
# P1. the profile's Top-20 is the head of the full rollup
# ---------------------------------------------------------------------------

class TestProfileTopNIsTheRollupHead:
    def test_top_donors_are_the_rollup_rows_not_name_strings(self, rollup_conn):
        profile = committees_routes._bulk_committee_profile(rollup_conn, MAIN, _cycle_2026())
        top = [(row["donor_key"], _money(row["total_amount"]), int(row["contribution_count"]))
               for row in profile["top_donors"]]
        assert top == [(KEY_JANE_MAIN, 150.0, 2), (KEY_JANE_OAK, 75.0, 1)]
        rollup, _, _ = _api("donors")
        head = rollup(rollup_conn, MAIN, _cycle_2026(), limit=20)
        assert top == [(row["donor_key"], _money(row["total_amount"]), int(row["contribution_count"]))
                       for row in head]

    def test_top_payees_are_the_rollup_rows_with_variants_merged(self, rollup_conn):
        profile = committees_routes._bulk_committee_profile(rollup_conn, MAIN, _cycle_2026())
        top = [(_norm(row["payee_name"]), _money(row["total_amount"]), int(row["transaction_count"]))
               for row in profile["top_payees"]]
        assert top == [("beta mail", 500.0, 1), ("acme printing", 350.0, 3), ("pat quinn", 20.0, 1)]
        rollup, _, _ = _api("payees")
        head = rollup(rollup_conn, MAIN, _cycle_2026(), limit=20)
        assert top == [(_norm(row["payee_name"]), _money(row["total_amount"]), int(row["transaction_count"]))
                       for row in head]

    def test_top_donors_stop_at_twenty(self, rollup_conn):
        profile = committees_routes._bulk_committee_profile(rollup_conn, BIG, _all_time())
        totals = [_money(row["total_amount"]) for row in profile["top_donors"]]
        assert totals == [float(i * 10) for i in range(25, 5, -1)]     # 250.0 .. 60.0
        rollup, _, _ = _api("donors")
        head = rollup(rollup_conn, BIG, _all_time(), limit=20)
        assert [row["donor_key"] for row in profile["top_donors"]] == [row["donor_key"] for row in head]

    def test_top_payees_stop_at_twenty(self, rollup_conn):
        profile = committees_routes._bulk_committee_profile(rollup_conn, BIG, _all_time())
        totals = [_money(row["total_amount"]) for row in profile["top_payees"]]
        assert totals == [float(i * 10) for i in range(25, 5, -1)]
        rollup, _, _ = _api("payees")
        head = rollup(rollup_conn, BIG, _all_time(), limit=20)
        assert [row["payee_name"] for row in profile["top_payees"]] == [row["payee_name"] for row in head]


# ---------------------------------------------------------------------------
# D. A7 / J-S8: /committees/<id> resolves ISBE first, legacy only when ISBE lacks the id
# ---------------------------------------------------------------------------

@pytest.fixture
def route_app(tmp_path: Path):
    db_path = str(tmp_path / "committee_route.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active)
        VALUES (7, 'ISBE Seven', 'Candidate', 'Chicago', 'IL', '60601', TRUE)
        """
    )
    conn.execute("INSERT INTO committees (id, name) VALUES (7, 'Legacy Seven'), (8, 'Legacy Eight')")
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestCommitteeRouteResolvesIsbeFirst:
    def test_isbe_wins_when_both_tables_have_the_id_and_legacy_still_serves_the_rest(self, route_app):
        client = route_app.test_client()
        problems = []
        # (id, expected status, text that must appear, text that must not)
        for committee_id, status, present, absent in [
            (7, 200, "ISBE Seven", "Legacy Seven"),    # both tables have 7 -> ISBE profile
            (8, 200, "Legacy Eight", None),            # ISBE has no 8 -> legacy page still served
            (9999, 404, None, None),                   # neither table -> 404
        ]:
            resp = client.get(f"/committees/{committee_id}")
            html = resp.get_data(as_text=True)
            if resp.status_code != status:
                problems.append(f"/committees/{committee_id}: status {resp.status_code}, wanted {status}")
            if present and present not in html:
                problems.append(f"/committees/{committee_id}: missing {present!r}")
            if absent and absent in html:
                problems.append(f"/committees/{committee_id}: unexpectedly shows {absent!r}")
        assert problems == []


# ---------------------------------------------------------------------------
# E. A8: /openbook/ without openbook_vendor_match
# ---------------------------------------------------------------------------

@pytest.fixture
def openbook_without_match_table(tmp_path: Path):
    db_path = str(tmp_path / "openbook_absent.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.execute("DROP TABLE IF EXISTS openbook_vendor_match")
    conn.commit()
    still_there = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        ("openbook_vendor_match",),
    ).fetchone()
    assert still_there is None, "fixture: openbook_vendor_match should be absent"
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _stat_values(html: str) -> list[str]:
    return [value.strip() for value in re.findall(r'class="stat-value">(.*?)</p>', html, flags=re.S)]


class TestOpenBookListWithoutMatchTable:
    def test_list_renders_with_a_zeroed_summary(self, openbook_without_match_table):
        resp = openbook_without_match_table.test_client().get("/openbook/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "OpenBook Vendor Matches" in html
        assert "No OpenBook vendors found." in html
        # Matched Vendors, Contracts, Total Award Amount, Contributions
        assert _stat_values(html) == ["0", "0", "$0.00", "0"]


# ===========================================================================
# F. S-PB (tests first): P2 profile reorder + See-all links, P3 full-list pages, J-S19 search links
#
# Plan: .claude/plans/profiles-races-dated-widgets.md  6.2 (S-PB), design 2.1, journal J-P1/J-P2/J-P6, J-S19/J-S20.
#
# Contract encoded below (anything not listed is deliberately NOT pinned):
#   * /committees/<isbe id>: <h2> order = Receipts Summary, Expenditures Summary, Top Donors, Top Payees,
#     Committee Information, Linked Candidates, Current Officers, Filing History (last), and nothing else.
#   * Top Donors rows link to /donors/key/<donor_key>?source=bulk_receipts and the link carries the page scope.
#     The Top Donors section holds one "See all N donors" link to /committees/<id>/donors and Top Payees one
#     "See all N payees" link to /committees/<id>/payees; N = the unique count (rollup count twin) in the page
#     scope, and following either link lands in the same scope.  Assumed UNCONDITIONAL (shown even when N <= 20).
#   * /committees/<id>/donors and /payees (ISBE ids; 404 for unknown ids): <h2>Donors|Payees <windowed tag></h2>,
#     one scope bar, default scope all time plus a "This cycle: $X" subtotal, summary line "N unique donors, $X"
#     ("... payees ..."; X = sum of the rollup totals), one row per rollup row with the cells name / total / count /
#     first date / last date (left to right; the cell order of the other columns is free), donor rows linked like
#     the profile's, payee rows unlinked, 50 rows per page, ?sort=total|count|name|last, ?dir=asc|desc, ?page=N,
#     ?q=<case-insensitive substring of the name> applied before paging, and a q form that carries the scope.
#   * The page names the ISBE committee (a legacy committee with the same id must not shadow it).
#   * Search (J-S19): a result row from the legacy committees table links to /committees/legacy/<id>; an ISBE row
#     links to /committees/<id>.
#
# Fixture committees (hand-computed numbers sit beside the rows that produce them).  From the S-PA fixture above:
#   500 Jane Doe fixture: donors 3 all time / 2 in cycle 2026, payees 4 / 3     510 four rows, all orders differ
#   550 25 donors and 25 payees (the Top-20 cap)     560 sixty rows that tie on everything
#   100 (from _seed_isbe_tables) is the one committee that has every profile section
# Added by pages_db for the full-list pages:
#   570 special characters   580 exactly fifty donors and payees   590 55 'Match' + 5 'Other' donors and payees
#   plus ISBE rows for 510 / 560 / 570 / 580 / 590 and a LEGACY committee that shares id 500
# ===========================================================================

KINDS = ("donors", "payees")
LIST_HEADING = {"donors": "Donors", "payees": "Payees"}
SPECIAL, FIFTY, MATCHES = 570, 580, 590
PAGE_SIZE = 50

PROFILE_ORDER = [
    "Receipts Summary", "Expenditures Summary", "Top Donors", "Top Payees",
    "Committee Information", "Linked Candidates", "Current Officers", "Filing History",
]

# Committee 510, by position A..D (see ORDER_NAMES): total, count, first date, last date.
ORDER_FIGURES = [
    ("$500.00", 1, date(2026, 7, 7), date(2026, 7, 7)),
    ("$300.00", 4, date(2026, 1, 3), date(2026, 3, 3)),
    ("$200.00", 3, date(2025, 12, 5), date(2026, 5, 5)),
    ("$100.00", 2, date(2026, 8, 8), date(2026, 9, 9)),
]
ORDER_DONOR_KEYS = [   # LOWER(first|last|street||city|state|zip) of the _seed_order donors, A..D
    "zed|alpha|1 a st||peoria|il|61602",
    "amy|beta|2 b st||peoria|il|61602",
    "mia|gamma|3 c st||peoria|il|61602",
    "bob|delta|4 d st||peoria|il|61602",
]

# Committee 570: names that break naive URL or HTML building.
HASH_DONOR = ("Hash", "Tag", "55 Hash St #7", None, "Peoria", "IL", "61602")       # '#' in the street
AMP_DONOR = (None, "Smith & Sons", "8 Amp St", None, "Peoria", "IL", "61602")        # '&' in a business name
PCT_DONOR = (None, "100% Pure", "9 Pct St", None, "Peoria", "IL", "61602")           # '%' in a business name
KEY_HASH = "hash|tag|55 hash st #7||peoria|il|61602"
KEY_AMP = "|smith & sons|8 amp st||peoria|il|61602"
KEY_PCT = "|100% pure|9 pct st||peoria|il|61602"
SCRIPT_PAYEE = "<script>alert(1)</script> Payee"

# Display names of the seeded families: donors "Tie Number07", payees "Tie Vendor 07".
FAMILY_NAMES = {
    "donors": {"Tie": "Tie Number{:02d}", "Fifty": "Fifty Donor{:02d}", "Match": "Match Donor{:02d}",
               "Other": "Other Donor{:02d}"},
    "payees": {"Tie": "Tie Vendor {:02d}", "Fifty": "Fifty Vendor {:02d}", "Match": "Match Vendor {:02d}",
               "Other": "Other Vendor {:02d}"},
}


def _family(kind: str, family: str, first: int, last: int) -> list[str]:
    return [FAMILY_NAMES[kind][family].format(i) for i in range(first, last + 1)]


def _seed_specials(conn):
    """Committee 570: three donors ($30, $20, $10 = $60) and two payees ($30 + $20 = $50), all 2026-05."""
    _receipt(conn, SPECIAL, "2026-05-01", HASH_DONOR, 30.0, "1A")
    _receipt(conn, SPECIAL, "2026-05-02", AMP_DONOR, 20.0, "1A")
    _receipt(conn, SPECIAL, "2026-05-03", PCT_DONOR, 10.0, "1A")
    _expenditure(conn, SPECIAL, "2026-05-04", "Smith & Sons", None, 30.0, "7A")
    _expenditure(conn, SPECIAL, "2026-05-05", SCRIPT_PAYEE, None, 20.0, "7A")


def _seed_page_sizes(conn):
    """580: fifty donors and fifty payees at $10 (= $500).  590: 55 'Match' + 5 'Other' at $10 each (= $600)."""
    for i in range(1, 51):
        who = ("Fifty", f"Donor{i:02d}", f"{i} Fifty St", None, "Peoria", "IL", "61602")
        _receipt(conn, FIFTY, "2026-04-04", who, 10.0, "1A")
        _expenditure(conn, FIFTY, "2026-04-04", f"Fifty Vendor {i:02d}", None, 10.0, "7A")
    for i in range(1, 56):
        who = ("Match", f"Donor{i:02d}", f"{i} Match St", None, "Peoria", "IL", "61602")
        _receipt(conn, MATCHES, "2026-04-04", who, 10.0, "1A")
        _expenditure(conn, MATCHES, "2026-04-04", f"Match Vendor {i:02d}", None, 10.0, "7A")
    for i in range(1, 6):
        who = ("Other", f"Donor{i:02d}", f"{i} Other St", None, "Peoria", "IL", "61602")
        _receipt(conn, MATCHES, "2026-04-04", who, 10.0, "1A")
        _expenditure(conn, MATCHES, "2026-04-04", f"Other Vendor {i:02d}", None, 10.0, "7A")


@pytest.fixture
def pages_db(rollup_db: str) -> str:
    """rollup_db plus what the full-list page tests need: ISBE committee rows for the page committees, a LEGACY
    committee that shares id 500 (the ISBE committee must win on the new pages too, J-S8), and committees
    570 / 580 / 590."""
    conn = get_db(rollup_db)
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active)
        VALUES (510, 'Order Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE),
               (560, 'Ties Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE),
               (570, 'Specials Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE),
               (580, 'Fifty Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE),
               (590, 'Matches Fixture PAC', 'Political Action', 'Peoria', 'IL', '61602', TRUE)
        """
    )
    conn.execute("INSERT INTO committees (id, name) VALUES (500, 'Legacy Shadow Five Hundred')")
    _seed_specials(conn)
    _seed_page_sizes(conn)
    conn.commit()
    site_keys = {
        row["donor_key"] for row in conn.execute(
            f"SELECT DISTINCT {Donor._bulk_donor_key_sql('r')} AS donor_key FROM bulk_receipts_clean r"
        ).fetchall()
    }
    for literal in (KEY_HASH, KEY_AMP, KEY_PCT, *ORDER_DONOR_KEYS):
        assert literal in site_keys, f"hand-computed key drifted from the site formula: {literal!r}"
    conn.close()
    return rollup_db


@pytest.fixture
def pages_app(pages_db):
    return create_app({"TESTING": True, "DATABASE_PATH": pages_db,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


# ---------------------------------------------------------------------------
# reading rendered pages (house style: <h2> + scope_tag, <table class="data-table"> with a <tbody>)
# ---------------------------------------------------------------------------

_SCOPE_TAG = re.compile(r'<span class="(scope-tag[^"]*)">(.*?)</span>', re.S)
_HTML_TAG = re.compile(r"<[^>]*>")


class Heading(NamedTuple):
    name: str        # the heading text without its scope tag
    tag_class: str   # "scope-tag" (windowed) | "scope-tag scope-tag-fixed" | "" (no tag)
    tag_text: str    # "All time" | "2026 Cycle" | ""


class Row(NamedTuple):
    name: str          # first cell; the text of its link when it has one
    cells: list        # every cell's visible text
    hrefs: list        # every href in the row, entity-decoded


def _text(fragment: str) -> str:
    """Visible text: tags removed, entities decoded, whitespace collapsed."""
    return " ".join(unescape(_HTML_TAG.sub(" ", fragment)).split())


def _content(html: str) -> str:
    """The page after its <h1> (skips the nav and the global Data Status strip)."""
    _, found, body = html.partition("</h1>")
    assert found, "page has no <h1>"
    return body


def _h1(html: str) -> str:
    found = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, flags=re.S)
    return _text(found.group(1)) if found else ""


def _headings(html: str) -> list[Heading]:
    found = []
    for inner in re.findall(r"<h2\b[^>]*>(.*?)</h2>", _content(html), flags=re.S):
        tag = _SCOPE_TAG.search(inner)
        found.append(Heading(
            _text(_SCOPE_TAG.sub("", inner)),
            tag.group(1) if tag else "",
            _text(tag.group(2)) if tag else "",
        ))
    return found


def _section(html: str, name_prefix: str) -> str:
    """Markup from the <h2> whose name starts with ``name_prefix`` up to the next <h2> (or the end of the page)."""
    content = _content(html)
    opens = list(re.finditer(r"<h2\b[^>]*>(.*?)</h2>", content, flags=re.S))
    for i, found in enumerate(opens):
        if _text(_SCOPE_TAG.sub("", found.group(1))).startswith(name_prefix):
            end = opens[i + 1].start() if i + 1 < len(opens) else len(content)
            return content[found.start():end]
    pytest.fail(f"no <h2> starts with {name_prefix!r}; headings: {[h.name for h in _headings(html)]}", pytrace=False)


def _anchors(fragment: str) -> list[tuple[str, str]]:
    """(href, link text) for every link in the fragment; hrefs are entity-decoded."""
    return [(unescape(href), _text(inner))
            for href, inner in re.findall(r'<a\b[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', fragment, flags=re.S)]


def _rows(fragment: str) -> list[Row]:
    """Data rows of the first <tbody> in the fragment; rows with fewer than three cells are 'No rows found' stubs."""
    body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", fragment, flags=re.S)
    if not body:
        return []
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body.group(1), flags=re.S):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.S)
        if len(cells) < 3:
            continue
        first_links = _anchors(cells[0])
        rows.append(Row(first_links[0][1] if first_links else _text(cells[0]),
                        [_text(cell) for cell in cells], [href for href, _ in _anchors(tr)]))
    return rows


def _list_rows(html: str) -> list[Row]:
    """Rows of the page's main table (the first one after the <h1>)."""
    return _rows(_content(html))


def _header_hrefs(html: str) -> list[str]:
    head = re.search(r"<thead\b[^>]*>(.*?)</thead>", _content(html), flags=re.S)
    return [href for href, _ in _anchors(head.group(1))] if head else []


def _parse_day(text: str):
    for pattern in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _figures(row: Row):
    """(money cells, integer cells, date cells) of a row, in cell order; the first cell (the name) is skipped."""
    rest = row.cells[1:]
    money = [cell for cell in rest if re.fullmatch(r"\$[\d,]+\.\d{2}", cell)]
    counts = [int(cell) for cell in rest if re.fullmatch(r"\d+", cell)]
    days = [day for day in (_parse_day(cell) for cell in rest) if day is not None]
    return money, counts, days


def _fetch(client, url: str) -> tuple[int, str]:
    response = client.get(url)
    return response.status_code, response.get_data(as_text=True)


def _page(client, url: str) -> str:
    status, html = _fetch(client, url)
    assert status == 200, f"GET {url} answered {status}, wanted 200"
    return html


def _scope_key(html: str):
    found = re.search(r'data-scope-key="([^"]*)"', html)
    return found.group(1) if found else None


def _page_links(html: str, number: int) -> list[str]:
    """Hrefs in the page body whose query asks for ?page=<number>."""
    return [href for href, _ in _anchors(_content(html)) if parse_qs(urlsplit(href).query).get("page") == [str(number)]]


def _attr(tag: str, name: str):
    found = re.search(rf'(?<![\w-]){name}\s*=\s*"([^"]*)"', tag)
    return found.group(1) if found else None


def _form_submission(html: str, q: str, default_path: str) -> str:
    """The URL a browser requests when ``q`` is typed into the page's search form and it is submitted:
    the visible q input takes ``q``, hidden inputs are sent as rendered."""
    forms = []
    for found in re.finditer(r"(<form\b[^>]*>)(.*?)</form>", _content(html), flags=re.S):
        inputs = re.findall(r"<input\b[^>]*>", found.group(2))
        typed = [tag for tag in inputs if _attr(tag, "name") == "q" and (_attr(tag, "type") or "text") != "hidden"]
        if typed:
            forms.append((found.group(1), inputs))
    assert len(forms) == 1, f"wanted exactly one search form with a q input, found {len(forms)}"
    form_tag, inputs = forms[0]
    assert (_attr(form_tag, "method") or "get").lower() == "get", "search form must submit with GET"
    fields = []
    for tag in inputs:
        name = _attr(tag, "name")
        input_type = (_attr(tag, "type") or "text").lower()
        if not name or input_type in ("submit", "button", "image", "reset"):
            continue
        typed_here = name == "q" and input_type != "hidden"
        fields.append((name, q if typed_here else unescape(_attr(tag, "value") or "")))
    action = unescape(_attr(form_tag, "action") or "") or default_path
    return f"{action}?{urlencode(fields)}"


# ---------------------------------------------------------------------------
# F1. P2: profile order, linked Top Donors, See-all links
# ---------------------------------------------------------------------------

class TestProfileOrder:
    def test_h2_order_is_summaries_then_top_n_then_the_rest_with_filing_history_last(self, rollup_app):
        # Committee 100 has every section (information, linked candidate, officers, filings, donors, payees).
        client = rollup_app.test_client()
        problems = []
        for query in ("", "?cycle=2026"):
            found = [heading.name for heading in _headings(_page(client, f"/committees/100{query}"))]
            if found != PROFILE_ORDER:
                problems.append(f"/committees/100{query}: {found}")
        assert problems == [], f"wanted exactly {PROFILE_ORDER}"


class TestProfileTopDonorLinks:
    def test_rows_link_to_the_donor_page_and_the_link_carries_the_scope(self, rollup_app):
        client = rollup_app.test_client()
        rows = _rows(_section(_page(client, f"/committees/{MAIN}?cycle=2026"), "Top Donors"))
        assert [row.name for row in rows] == ["Jane Doe", "Jane Doe"]
        # Row 1 is Jane/1 Main ($150.00, gave only here); row 2 is Jane/9 Oak ($75.00 here + $1,000.00 to 501).
        for row, key, donor_page_total in zip(rows, (KEY_JANE_MAIN, KEY_JANE_OAK), ("$150.00", "$1,075.00")):
            links = [href for href in row.hrefs if "/donors/key/" in href]
            assert len(links) == 1, f"{key}: donor links {row.hrefs}"
            parts = urlsplit(links[0])
            assert unquote(parts.path) == "/donors/key/" + key
            assert parse_qs(parts.query).get("source") == ["bulk_receipts"]
            donor_page = _page(client, links[0])
            assert _scope_key(donor_page) == "cycle:2026"
            assert f"Total Contributed:</strong> {donor_page_total}" in donor_page


class TestProfileSeeAllLinks:
    # committee, query, bar key, donors N, payees N.  Hand-computed in _seed_main / _seed_big:
    #   500 all time: donors Jane/1 Main, Jane/9 Oak, Old Timer = 3; payees Beta Mail, ACME, Pat Quinn, Old Vendor = 4
    #   500 cycle 2026: Old Timer and Old Vendor (2022) drop out = 2 and 3
    #   550: 25 donors and 25 payees although only 20 of each are shown
    CASES = [
        (MAIN, "", "all", 3, 4),
        (MAIN, "?cycle=2026", "cycle:2026", 2, 3),
        (BIG, "", "all", 25, 25),
    ]

    def test_links_show_the_unique_count_and_open_the_full_list_in_the_same_scope(self, rollup_app):
        client = rollup_app.test_client()
        problems = []
        for committee, query, bar_key, donors_n, payees_n in self.CASES:
            html = _page(client, f"/committees/{committee}{query}")
            for kind, heading, wanted in (("donors", "Top Donors", donors_n), ("payees", "Top Payees", payees_n)):
                label = f"/committees/{committee}{query} {heading}"
                section = _section(html, heading)
                links = [(href, text) for href, text in _anchors(section)
                         if re.match(rf"See all\s+[\d,]+\s+{kind[:-1]}", text, flags=re.I)]
                if len(links) != 1:
                    problems.append(f"{label}: {len(links)} 'See all N {kind}' links, wanted 1")
                    continue
                href, text = links[0]
                shown = int(re.match(r"See all\s+([\d,]+)", text, flags=re.I).group(1).replace(",", ""))
                if shown != wanted:
                    problems.append(f"{label}: link says {shown}, wanted {wanted}")
                if committee == BIG and len(_rows(section)) != 20:
                    problems.append(f"{label}: {len(_rows(section))} table rows, wanted the Top-20 cap")
                if urlsplit(href).path != f"/committees/{committee}/{kind}":
                    problems.append(f"{label}: link goes to {href}")
                    continue
                status, target = _fetch(client, href)
                if status != 200:
                    problems.append(f"{label}: {href} answered {status}")
                    continue
                if _scope_key(target) != bar_key:
                    problems.append(f"{label}: {href} opens in scope {_scope_key(target)!r}, wanted {bar_key!r}")
                if f"{wanted} unique {kind}," not in _text(_content(target)):
                    problems.append(f"{label}: {href} does not say '{wanted} unique {kind},'")
        assert problems == []


# ---------------------------------------------------------------------------
# F2. P3: /committees/<id>/donors and /committees/<id>/payees
# ---------------------------------------------------------------------------

class TestFullListPages:
    # query, bar key, scope tag text, donors summary, payees summary.  Committee 500, hand-computed:
    #   all time: donors 190 + 75 + 10 = 275 (3 donors); payees 500 + 425 + 20 + 15 = 960 (4 payees)
    #   cycle 2026: donors 150 + 75 = 225 (2); payees 500 + 350 + 20 = 870 (3).  Never in a donor total: the
    #   archived $999, the $0 and -$20 rows, the $5,000 transfer (2A) and the $700 other receipt (5A).
    SCOPES = [
        ("", "all", "All time", "3 unique donors, $275.00", "4 unique payees, $960.00"),
        ("?cycle=2026", "cycle:2026", "2026 Cycle", "2 unique donors, $225.00", "3 unique payees, $870.00"),
    ]

    def test_isbe_committee_is_200_and_an_unknown_id_is_404(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            for committee, wanted in ((MAIN, 200), (987654, 404)):
                status = client.get(f"/committees/{committee}/{kind}").status_code
                if status != wanted:
                    problems.append(f"/committees/{committee}/{kind}: {status}, wanted {wanted}")
        assert problems == []

    def test_heading_carries_the_windowed_scope_tag_with_the_summary_line_and_one_scope_bar(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            for query, bar_key, tag, donors_summary, payees_summary in self.SCOPES:
                url = f"/committees/{MAIN}/{kind}{query}"
                status, html = _fetch(client, url)
                if status != 200:
                    problems.append(f"{url}: status {status}")
                    continue
                heading = f'<h2>{LIST_HEADING[kind]} <span class="scope-tag">{tag}</span></h2>'
                if heading not in html:
                    problems.append(f"{url}: missing {heading}; headings {_headings(html)}")
                bars = html.count('class="time-scope-bar')
                if bars != 1:
                    problems.append(f"{url}: {bars} scope bars, wanted 1")
                if _scope_key(html) != bar_key:
                    problems.append(f"{url}: scope key {_scope_key(html)!r}, wanted {bar_key!r}")
                summary = donors_summary if kind == "donors" else payees_summary
                if summary not in _text(_content(html)):
                    problems.append(f"{url}: missing summary line {summary!r}")
        assert problems == []

    def test_default_scope_shows_a_this_cycle_subtotal(self, pages_app, pages_db):
        # The subtotal is for today's cycle (2026 until 2027-01-01), so the expectation is computed with the
        # rollup helpers for current_cycle() instead of a literal: $225.00 donors / $870.00 payees in 2026.
        conn = get_db(pages_db)
        this_cycle = resolve_time_scope(default="all", axis="received", args={"cycle": str(current_cycle())})
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            rollup, _, _ = _api(kind)
            subtotal = sum(row["total_amount"] for row in rollup(conn, MAIN, this_cycle, limit=1000))
            everything = sum(row["total_amount"] for row in rollup(conn, MAIN, _all_time(), limit=1000))
            money = "${:,.2f}".format(subtotal)
            if "${:,.2f}".format(everything) == money:
                problems.append(f"{kind}: fixture cannot tell this cycle from all time ({money})")
            text = _text(_content(_page(client, f"/committees/{MAIN}/{kind}")))
            if not re.search(rf"This cycle[^$]{{0,30}}{re.escape(money)}", text, flags=re.I):
                problems.append(f"{kind}: no 'This cycle ... {money}' in the page text")
        conn.close()
        assert problems == []

    def test_rows_follow_the_scope_window(self, pages_app):
        # (kind, query) -> (normalized name, total, count) of every row, default order (total descending).
        wanted = {
            ("donors", ""): [("jane doe", "$190.00", 3), ("jane doe", "$75.00", 1), ("old timer", "$10.00", 1)],
            ("donors", "?cycle=2026"): [("jane doe", "$150.00", 2), ("jane doe", "$75.00", 1)],
            ("payees", ""): [("beta mail", "$500.00", 1), ("acme printing", "$425.00", 4),
                             ("pat quinn", "$20.00", 1), ("old vendor", "$15.00", 1)],
            ("payees", "?cycle=2026"): [("beta mail", "$500.00", 1), ("acme printing", "$350.00", 3),
                                        ("pat quinn", "$20.00", 1)],
        }
        client = pages_app.test_client()
        problems = []
        for (kind, query), expected in wanted.items():
            url = f"/committees/{MAIN}/{kind}{query}"
            status, html = _fetch(client, url)
            if status != 200:
                problems.append(f"{url}: status {status}")
                continue
            found = []
            for row in _list_rows(html):
                money, counts, _ = _figures(row)
                found.append((_norm(row.name), money[0] if money else None, counts[0] if counts else None))
            if found != expected:
                problems.append(f"{url}: {found}, wanted {expected}")
        assert problems == []

    def test_rows_show_name_total_count_first_and_last_date(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            rows = _list_rows(_page(client, f"/committees/{ORDER}/{kind}"))
            if [row.name for row in rows] != ORDER_NAMES[kind]:
                problems.append(f"{kind}: names {[row.name for row in rows]}, wanted {ORDER_NAMES[kind]}")
                continue
            for row, (total, count, first, last) in zip(rows, ORDER_FIGURES):
                money, counts, days = _figures(row)
                if money != [total]:
                    problems.append(f"{kind} {row.name}: money cells {money}, wanted {[total]}")
                if counts != [count]:
                    problems.append(f"{kind} {row.name}: integer cells {counts}, wanted {[count]}")
                if days != [first, last]:
                    problems.append(f"{kind} {row.name}: date cells {days}, wanted first then last {[first, last]}")
        assert problems == []

    def test_donor_rows_link_to_the_donor_page_and_payee_rows_do_not(self, pages_app):
        client = pages_app.test_client()
        problems = []
        rows = _list_rows(_page(client, f"/committees/{ORDER}/donors"))
        for row, key in zip(rows, ORDER_DONOR_KEYS):
            links = [href for href in row.hrefs if "/donors/key/" in href]
            if len(links) != 1:
                problems.append(f"donor {row.name}: donor links {row.hrefs}")
                continue
            parts = urlsplit(links[0])
            if unquote(parts.path) != "/donors/key/" + key:
                problems.append(f"donor {row.name}: path {unquote(parts.path)!r}, wanted key {key!r}")
            if parse_qs(parts.query).get("source") != ["bulk_receipts"]:
                problems.append(f"donor {row.name}: query {parts.query!r}, wanted source=bulk_receipts")
        # The link carries the page scope: Jane/1 Main's donor page shows the cycle total, not the all-time one.
        jane_rows = _list_rows(_page(client, f"/committees/{MAIN}/donors?cycle=2026"))
        for row, key, donor_page_total in zip(jane_rows, (KEY_JANE_MAIN, KEY_JANE_OAK), ("$150.00", "$1,075.00")):
            links = [href for href in row.hrefs if "/donors/key/" in href]
            if len(links) != 1:
                problems.append(f"cycle donor {key}: donor links {row.hrefs}")
                continue
            if unquote(urlsplit(links[0]).path) != "/donors/key/" + key:
                problems.append(f"cycle donor {key}: link {links[0]}")
            status, donor_page = _fetch(client, links[0])
            if status != 200 or _scope_key(donor_page) != "cycle:2026" or \
                    f"Total Contributed:</strong> {donor_page_total}" not in donor_page:
                problems.append(f"cycle donor {key}: {links[0]} -> {status}, scope {_scope_key(donor_page)!r}")
        # Payees have no identity key (J-P4): their rows carry no links at all.
        for committee in (ORDER, MAIN):
            for row in _list_rows(_page(client, f"/committees/{committee}/payees")):
                if row.hrefs:
                    problems.append(f"payee {row.name}: unexpected links {row.hrefs}")
        assert problems == []

    def test_sort_and_dir_reorder_the_rows(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            for (sort_by, sort_dir), positions in EXPECTED_POSITIONS.items():
                url = f"/committees/{ORDER}/{kind}?sort={sort_by}&dir={sort_dir}"
                status, html = _fetch(client, url)
                found = [row.name for row in _list_rows(html)] if status == 200 else f"status {status}"
                wanted = [ORDER_NAMES[kind][position] for position in positions]
                if found != wanted:
                    problems.append(f"{url}: {found}, wanted {wanted}")
        assert problems == []

    def test_header_sort_links_reorder_the_rows_and_keep_the_scope(self, pages_app):
        # Follow the links as rendered (not hand-built URLs): each of total / count / name / last has one, it keeps
        # the page scope, and it yields that key's order in the direction it names (either, when it names none).
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            start = f"/committees/{ORDER}/{kind}?cycle=2026"
            by_key: dict[str, list] = {}
            for href in _header_hrefs(_page(client, start)):
                query = parse_qs(urlsplit(href).query)
                if "sort" in query:
                    by_key.setdefault(query["sort"][0], []).append((href, (query.get("dir") or [None])[0]))
            missing = {"total", "count", "name", "last"} - set(by_key)
            if missing:
                problems.append(f"{kind}: no header sort link for {sorted(missing)}; found {sorted(by_key)}")
            for sort_by in ("total", "count", "name", "last"):
                for href, sort_dir in by_key.get(sort_by, []):
                    status, html = _fetch(client, href)
                    if status != 200:
                        problems.append(f"{kind}: {href} answered {status}")
                        continue
                    allowed = [[ORDER_NAMES[kind][p] for p in EXPECTED_POSITIONS[(sort_by, d)]]
                               for d in ((sort_dir,) if sort_dir else ("asc", "desc"))]
                    found = [row.name for row in _list_rows(html)]
                    if found not in allowed:
                        problems.append(f"{kind}: {href} lists {found}, wanted one of {allowed}")
                    if _scope_key(html) != "cycle:2026":
                        problems.append(f"{kind}: {href} lost the scope ({_scope_key(html)!r})")
        assert problems == []

    def test_fifty_rows_per_page_and_the_pager_link_keeps_sort_and_scope(self, pages_app):
        # Committee 560: sixty rows at $10 (= $600), all in 2026-04.  Name descending = Number60 .. Number01.
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            descending = _family(kind, "Tie", 1, 60)[::-1]
            first = _page(client, f"/committees/{TIES}/{kind}?sort=name&dir=desc&cycle=2026")
            found = [row.name for row in _list_rows(first)]
            if found != descending[:PAGE_SIZE]:
                problems.append(f"{kind} page 1: {len(found)} rows {found[:2]}..{found[-1:]}, wanted 50 from {descending[0]}")
            if f"60 unique {kind}, $600.00" not in _text(_content(first)):
                problems.append(f"{kind} page 1: summary is not for all 60 rows")
            if _page_links(first, 3):
                problems.append(f"{kind} page 1 links to a page 3")
            forward = _page_links(first, 2)
            if not forward:
                problems.append(f"{kind} page 1 has no link to page 2")
                continue
            status, second = _fetch(client, forward[0])
            if status != 200:
                problems.append(f"{kind}: {forward[0]} answered {status}")
                continue
            found = [row.name for row in _list_rows(second)]
            if found != descending[PAGE_SIZE:]:
                problems.append(f"{kind} page 2 (via {forward[0]}): {found}, wanted {descending[PAGE_SIZE:]}")
            if _scope_key(second) != "cycle:2026":
                problems.append(f"{kind} page 2 lost the scope ({_scope_key(second)!r})")
            if f"60 unique {kind}, $600.00" not in _text(_content(second)):
                problems.append(f"{kind} page 2: summary is not for all 60 rows")
            if _page_links(second, 3):
                problems.append(f"{kind} page 2 links to a page 3")
        assert problems == []

    def test_default_order_pages_never_overlap_or_skip(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            everyone = set(_family(kind, "Tie", 1, 60))
            first = [row.name for row in _list_rows(_page(client, f"/committees/{TIES}/{kind}"))]
            second = [row.name for row in _list_rows(_page(client, f"/committees/{TIES}/{kind}?page=2"))]
            if (len(first), len(second)) != (PAGE_SIZE, 10):
                problems.append(f"{kind}: page sizes {(len(first), len(second))}, wanted (50, 10)")
            if set(first) & set(second):
                problems.append(f"{kind}: pages overlap on {sorted(set(first) & set(second))[:3]}")
            if set(first) | set(second) != everyone:
                problems.append(f"{kind}: pages skip {sorted(everyone - set(first) - set(second))[:3]}")
        assert problems == []

    def test_exactly_fifty_rows_have_no_second_page(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            html = _page(client, f"/committees/{FIFTY}/{kind}")
            if len(_list_rows(html)) != PAGE_SIZE:
                problems.append(f"{kind}: {len(_list_rows(html))} rows, wanted 50")
            if _page_links(html, 2):
                problems.append(f"{kind}: links to a page 2 that would be empty")
            if f"50 unique {kind}, $500.00" not in _text(_content(html)):
                problems.append(f"{kind}: summary line")
        assert problems == []

    def test_q_filters_case_insensitively_on_the_name_before_paging(self, pages_app):
        # 'Number5' / 'Vendor 5' leave rows 50..59 only; rows 01..49 (the whole first page) must not hide them.
        client = pages_app.test_client()
        problems = []
        for kind, needle in (("donors", "Number5"), ("payees", "Vendor 5")):
            for typed in (needle, needle.upper(), needle.lower()):
                url = f"/committees/{TIES}/{kind}?q={quote(typed)}"
                found = sorted(row.name for row in _list_rows(_page(client, url)))
                wanted = _family(kind, "Tie", 50, 59)
                if found != wanted:
                    problems.append(f"{url}: {found}, wanted {wanted}")
            url = f"/committees/{TIES}/{kind}?q=zzz"
            if _list_rows(_page(client, url)):
                problems.append(f"{url}: rows for a filter nothing matches")
        assert problems == []

    def test_pager_link_keeps_the_filter(self, pages_app):
        # Committee 590: 55 'Match' + 5 'Other'.  q=match leaves 55 rows -> page 2 holds Match 51..55 and no 'Other'.
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            first = _page(client, f"/committees/{MATCHES}/{kind}?q=match&sort=name&dir=asc")
            names = _family(kind, "Match", 1, 55)
            if [row.name for row in _list_rows(first)] != names[:PAGE_SIZE]:
                problems.append(f"{kind} page 1 is not Match 01..50 in name order")
            forward = _page_links(first, 2)
            if not forward:
                problems.append(f"{kind} page 1 has no link to page 2")
                continue
            second = _page(client, forward[0])
            found = [row.name for row in _list_rows(second)]
            if found != names[PAGE_SIZE:]:
                problems.append(f"{kind} page 2 via {forward[0]}: {found}, wanted {names[PAGE_SIZE:]}")
        assert problems == []

    def test_q_does_not_escape_the_scope_window(self, pages_app):
        # Old Timer (donor) and Old Vendor (payee) are 2022 rows: visible all time, invisible in cycle 2026.
        client = pages_app.test_client()
        problems = []
        for kind, needle, old_name in (("donors", "timer", "old timer"), ("payees", "vendor", "old vendor")):
            everything = [_norm(row.name) for row in _list_rows(_page(client, f"/committees/{MAIN}/{kind}?q={needle}"))]
            windowed = [_norm(row.name) for row in
                        _list_rows(_page(client, f"/committees/{MAIN}/{kind}?q={needle}&cycle=2026"))]
            if everything != [old_name]:
                problems.append(f"{kind} all time q={needle}: {everything}, wanted {[old_name]}")
            if windowed != []:
                problems.append(f"{kind} cycle 2026 q={needle}: {windowed}, wanted no rows")
        assert problems == []

    def test_search_form_submits_the_scope_with_the_filter(self, pages_app):
        # (kind, scope query, bar key, q) -> rows a browser would get after typing q into the page's form.
        #   cycle 2026: Jane/1 Main 100 + 50, Jane/9 Oak 75; ACME 100 + 200 + 50
        #   2026-03-01..2026-12-31: Jane/1 Main only the $50 of 2026-03-01 (the day is inclusive), Jane/9 Oak $75;
        #                           ACME 200 + 50 = 250 (2 rows)
        cases = [
            ("donors", "?cycle=2026", "cycle:2026", "jane", [("jane doe", "$150.00", 2), ("jane doe", "$75.00", 1)]),
            ("donors", "?date_from=2026-03-01&date_to=2026-12-31", "custom", "jane",
             [("jane doe", "$75.00", 1), ("jane doe", "$50.00", 1)]),
            ("payees", "?cycle=2026", "cycle:2026", "acme", [("acme printing", "$350.00", 3)]),
            ("payees", "?date_from=2026-03-01&date_to=2026-12-31", "custom", "acme", [("acme printing", "$250.00", 2)]),
        ]
        client = pages_app.test_client()
        problems = []
        for kind, query, bar_key, q, wanted in cases:
            path = f"/committees/{MAIN}/{kind}"
            submitted = _form_submission(_page(client, f"{path}{query}"), q, path)
            status, html = _fetch(client, submitted)
            if status != 200:
                problems.append(f"{submitted}: status {status}")
                continue
            found = []
            for row in _list_rows(html):
                money, counts, _ = _figures(row)
                found.append((_norm(row.name), money[0] if money else None, counts[0] if counts else None))
            if _scope_key(html) != bar_key or found != wanted:
                problems.append(f"{submitted}: scope {_scope_key(html)!r} rows {found}, wanted {bar_key!r} {wanted}")
        assert problems == []

    def test_special_characters_in_names_survive_links_escaping_and_the_filter(self, pages_app):
        client = pages_app.test_client()
        problems = []
        wanted = [("Hash Tag", KEY_HASH, "$30.00"), ("Smith & Sons", KEY_AMP, "$20.00"), ("100% Pure", KEY_PCT, "$10.00")]
        # cycle 2026 on purpose: the donor page computes a windowed total live, while its all-time total reads the
        # analytics_donor_summary table that this fixture does not materialize.
        rows = _list_rows(_page(client, f"/committees/{SPECIAL}/donors?cycle=2026"))
        if [row.name for row in rows] != [name for name, _, _ in wanted]:
            problems.append(f"donor rows {[row.name for row in rows]}")
        else:
            for row, (name, key, total) in zip(rows, wanted):
                links = [href for href in row.hrefs if "/donors/key/" in href]
                if len(links) != 1:
                    problems.append(f"{name}: donor links {row.hrefs}")
                    continue
                status, donor_page = _fetch(client, links[0])
                text = _text(_content(donor_page))
                if status != 200 or _h1(donor_page) != name or f"Total Contributed: {total}" not in text:
                    problems.append(f"{name}: {links[0]} -> {status}; page does not show the donor and {total}")
        # q is a literal substring: '%' matches only the donor whose name contains one.
        literal = [row.name for row in _list_rows(_page(client, f"/committees/{SPECIAL}/donors?q=%25&cycle=2026"))]
        if literal != ["100% Pure"]:
            problems.append(f"q=%25 listed {literal}, wanted ['100% Pure']")
        # Payee names from filings are text, never markup; the typed filter is echoed escaped.
        status, html = _fetch(client, f"/committees/{SPECIAL}/payees")
        if [row.name for row in _list_rows(html)] != ["Smith & Sons", SCRIPT_PAYEE]:
            problems.append(f"payee rows {[row.name for row in _list_rows(html)]}")
        if "<script>alert(1)</script>" in html:
            problems.append("payee name rendered as raw markup")
        if "&lt;script&gt;alert(1)&lt;/script&gt; Payee" not in html:
            problems.append("escaped payee name not found")
        _, echoed = _fetch(client, f"/committees/{SPECIAL}/payees?q={quote('<script>alert(1)</script>')}")
        if "<script>alert(1)</script>" in echoed:
            problems.append("typed filter echoed as raw markup")
        assert problems == []

    def test_isbe_committee_wins_over_a_legacy_row_with_the_same_id(self, pages_app):
        # pages_db adds a LEGACY committee 'Legacy Shadow Five Hundred' with id 500; ISBE 500 is 'Rollup Fixture PAC'.
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            html = _page(client, f"/committees/{MAIN}/{kind}")
            if "Rollup Fixture PAC" not in html:
                problems.append(f"{kind}: the ISBE committee name is not on the page")
            if "Legacy Shadow Five Hundred" in html:
                problems.append(f"{kind}: the legacy committee's name leaked onto the page")
            if not _list_rows(html):
                problems.append(f"{kind}: no rows for the ISBE committee")
        assert problems == []

    def test_odd_page_numbers_and_filters_never_error(self, pages_app):
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            control = client.get(f"/committees/{ORDER}/{kind}").status_code   # a 404 would pass every check below
            if control != 200:
                problems.append(f"/committees/{ORDER}/{kind}: {control}, wanted 200")
            for query in ("?page=0", "?page=-3", "?page=abc", "?page=999999", "?q=%27", "?q=%5C%25_"):
                status = client.get(f"/committees/{ORDER}/{kind}{query}").status_code
                if status >= 500:
                    problems.append(f"/committees/{ORDER}/{kind}{query}: {status}")
        assert problems == []

    def test_unknown_sort_and_dir_fall_back_without_losing_rows(self, pages_app):
        # A 400 would be a fair answer too; a 5xx, or a partial list, is not.
        client = pages_app.test_client()
        control = client.get(f"/committees/{ORDER}/donors").status_code   # a 404 would pass every check below
        assert control == 200, f"/committees/{ORDER}/donors answered {control}, wanted 200"
        problems = []
        for kind in KINDS:
            url = f"/committees/{ORDER}/{kind}?sort=bogus_column&dir=sideways"
            status, html = _fetch(client, url)
            if status == 400:
                continue
            found = sorted(row.name for row in _list_rows(html)) if status == 200 else f"status {status}"
            if found != sorted(ORDER_NAMES[kind]):
                problems.append(f"{url}: {found}")
        assert problems == []

    def test_a_window_without_rows_renders_an_empty_list_not_a_404(self, pages_app):
        # Committee 500 has nothing before 2022, so cycle 2020 (2019-01-01..2020-12-31) is empty.  The page must still
        # render (with the bar's Reset link), as the donor page does for a donor with no gifts in the window.
        client = pages_app.test_client()
        problems = []
        for kind in KINDS:
            url = f"/committees/{MAIN}/{kind}?cycle=2020"
            status, html = _fetch(client, url)
            if status != 200:
                problems.append(f"{url}: status {status}")
                continue
            if _list_rows(html):
                problems.append(f"{url}: rows {[row.name for row in _list_rows(html)]}")
            if _scope_key(html) != "cycle:2020":
                problems.append(f"{url}: scope key {_scope_key(html)!r}")
            if f"0 unique {kind}, $0.00" not in _text(_content(html)):
                problems.append(f"{url}: no '0 unique {kind}, $0.00' summary line")
        assert problems == []


# ---------------------------------------------------------------------------
# F3. J-S19: search rows from the legacy committees table point at /committees/legacy/<id>
# ---------------------------------------------------------------------------

@pytest.fixture
def search_app(tmp_path: Path):
    db_path = str(tmp_path / "committee_search.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, city, state, zipcode, active)
        VALUES (7, 'Zyxwv Seven ISBE Committee', 'Candidate', 'Chicago', 'IL', '60601', TRUE),
               (9, 'Zyxwv Nine ISBE Committee', 'Candidate', 'Chicago', 'IL', '60601', TRUE)
        """
    )
    # Legacy id 7 is ALSO an ISBE id (a different committee); legacy id 8 has no ISBE twin.  The names differ
    # because the search drops a legacy row whose name an ISBE row already shows.
    conn.execute(
        "INSERT INTO committees (id, name) VALUES (7, 'Zyxwv Seven Legacy Committee'), (8, 'Zyxwv Eight Legacy Committee')"
    )
    conn.commit()
    _create_compat_views(conn)
    _create_candidate_finance_agg_view(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


class TestSearchLegacyCommitteeLinks:
    def test_legacy_rows_link_to_the_legacy_page_and_isbe_rows_to_the_profile(self, search_app):
        client = search_app.test_client()
        html = _page(client, "/search?q=Zyxwv&type=committees&period=all")
        found = {row.name: row.hrefs[0] for row in _rows(_section(html, "Committees")) if row.hrefs}
        wanted = {
            "Zyxwv Nine ISBE Committee": "/committees/9",
            "Zyxwv Seven ISBE Committee": "/committees/7",
            "Zyxwv Seven Legacy Committee": "/committees/legacy/7",     # same id as the ISBE row above
            "Zyxwv Eight Legacy Committee": "/committees/legacy/8",
        }
        assert {name: urlsplit(href).path for name, href in found.items()} == wanted
        # and the links open the committee the row names
        legacy = _page(client, found["Zyxwv Seven Legacy Committee"])
        assert "Zyxwv Seven Legacy Committee" in legacy
        assert "Zyxwv Seven ISBE Committee" not in legacy
        isbe = _page(client, found["Zyxwv Seven ISBE Committee"])
        assert "Zyxwv Seven ISBE Committee" in isbe
        assert "Zyxwv Seven Legacy Committee" not in isbe
