"""Tests-first suite for plan step S-S2, item B4: a per-cycle donor aggregate behind `/donors/`.

Plan: .claude/plans/profiles-races-dated-widgets.md section 6.2 (S-S2) and decision J-S16 in 6.3.

Why: with a time window `/donors/` runs a live GROUP BY over every receipt in the window (branch A of
`Donor.get_all_with_totals`), and the default scope is the current election cycle, so the most visited
donor list pays that cost on every page.  Pinned contract:

  TABLE   `analytics_donor_cycle_summary` in database/schema.sql, columns
              source, election_cycle, donor_key, donor_name, donor_address, donor_city, donor_state,
              occupation, employer, total_amount, contribution_count, committee_count, updated_at
          primary key (source, election_cycle, donor_key); election_cycle an integer; total_amount exact
          to the cent (double precision or numeric, NOT real: `analytics_donor_summary.total_amount` is
          real = float4 and loses cents above ~$16.7M, see test_totals_are_exact_to_the_cent...).
  REFRESH `refresh_analytics_materialized(conn)` rebuilds the rows of its own source in both ISBE branches
          (direct `isbe_condensed_receipts` and the legacy `bulk_*_clean` compat views): one row per donor
          per cycle, cycle = year + year % 2 of received_date, the /donors/ LIST predicate
          `receipts_filter_sql(parts="contributions")` (amount > 0, not archived, D-2 part blank or '1%', no
          D-2 report required), so a cycle page equals the live page for that window (plan J-S2 / J-S16).
  READ    `Donor.get_all_with_totals(...)` and `Donor.count(...)` read the table when the requested
          (from, to) is exactly `cycle_window(c)` AND the table holds rows for that source and cycle;
          every other window, and a cycle without rows, takes the live path.  A cycle-path row has the
          shape of a live row (id None, source 'bulk_receipts', donor_key link, float/int/int totals) and
          there is exactly one row per donor_key, so the list and `Donor.count` agree.

The data is read through TWO fixtures that differ only in where the receipts live (parametrized `env`):
  bulk  `_seed_isbe_tables` + `_create_compat_views`: raw `isbe_receipts` (TEXT dates) behind `bulk_*_clean`
        views            -> refresh takes `_refresh_materialized_bulk`
  isbe  `_seed_isbe_condensed_tables` + a `bulk_receipts_clean` view over `isbe_condensed_receipts` (DATE
        dates, as the production matview)  -> refresh takes `_refresh_materialized_isbe` (source_path
        'isbe_direct')
Both carry the SAME receipts, so every expectation below holds for both.  `amount` is widened to double
precision in both (the seeders' REAL would round the large donor).  The table-building tests run on both
branches (`env`); the read-path tests run on the production branch only (`prod_env`), because the read does not
depend on which refresh produced the rows.

How the read path is observed: a receipt inserted AFTER the refresh ("late data") changes the live answer
but not the aggregate, so "served from the aggregate" is read from the answer itself.  Tests that must be
red today carry late data; the live rows they are compared with are captured before it lands.

BASE FIXTURE (committed literals; cycle 2024 = 2023-01-01..2024-12-31, cycle 2026 = 2025-01-01..2026-12-31).
Committees 100 and 200, filed docs 5001 (committee 100) and 5002 (committee 200), both with an active D-2.

  donor                 receipts (committee, date, amount)                       cycle 2024        cycle 2026
  Alice Donor           100 2025-01-15 5000 | 200 2025-01-20 10000 | 200 2023-03-01 700
                                                                                 700 / 1 / 1       15000 / 2 / 2
  Bob Donor             100 2025-02-10 2500 | 100 2025-02-11 -500 (refund) | 100 2025-02-12 0
                                                                                 -                 2500 / 1 / 1
  Edgar Edge            100: 2023-01-01 1 | 2024-12-31 2 | 2025-01-01 4 | 2026-12-31 8   (the four boundary days)
                                                                                 3 / 2 / 1         12 / 2 / 1
  Willa Whale           100 2025-06-01 50123456.78                              -                 50123456.78 / 1 / 1
  Tie Alpha..Delta      one 1200 receipt each, 2025-08-01..04, committees alternating 100/200 (inserted Delta
                        first, so physical order is the reverse of the name order)   -        1200 / 1 / 1 each
                                                                       (cells are total / contributions / committees)
  EXCLUDED (never in the table): Ada Archived (archived receipt 999), Pat Transfer (part 2A, 888); Bob's refund
  and zero rows.  INCLUDED by the list predicate although analytics_donor_summary (D-2 EXISTS, part '1%') drops
  them: Una Blankpart (part NULL, 777), Nora NoDtwo (doc 5099, no D-2 at all, 666), Sam Superseded (doc 5004,
  only an archived D-2, 555) -- the A-1 receipt shape (J-S2 / J-S16).
  => cycle 2024: 2 rows (Alice, Edgar).  cycle 2026: 11 rows (Whale, Alice, Bob, Tie x4, Una, Nora, Sam, Edgar).

  Late data (inserted after the refresh): Lena Late 4444 on 2026-03-05; Alice +1000 on 2026-04-04 (live cycle
  2026 total 16000); Olga Old 333 on 2022-05-05 (cycle 2022 has no aggregate rows); Edgar +16 on 2024-06-06
  (live cycle 2024 total 19).
  Scenario additions (each test adds them itself, then refreshes): Carol Donor (three receipts whose occupation
  and employer vary, cycle 2026 600 / 3 / 2), BigCorp (no first name), Nina Nodate (receipt without a date).

Kill list: see each test's "Kills".  Accepted holes are listed in the hand-off report, not here.
"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from database.analytics import refresh_analytics_materialized
from database.connection import get_db, init_db
from database.models import Donor
from tests.test_isbe_rewire import (
    _create_compat_views,
    _seed_isbe_condensed_tables,
    _seed_isbe_tables,
)
from webapp.app import create_app
from webapp.utils.time_scope import cycle_window

SOURCE = "bulk_receipts"
TABLE = "analytics_donor_cycle_summary"
COLUMNS = {
    "source", "election_cycle", "donor_key", "donor_name", "donor_address", "donor_city", "donor_state",
    "occupation", "employer", "total_amount", "contribution_count", "committee_count", "updated_at",
}


def window(cycle: int) -> tuple[str, str]:
    start, end = cycle_window(cycle)
    return start.isoformat(), end.isoformat()


W2022, W2024, W2026 = window(2022), window(2024), window(2026)
WHOLE_PERIOD = ("2025-01-01", "2027-12-31")  # not a cycle window: always live; holds every 2026-cycle base receipt


# ---------------------------------------------------------------------------
# Donors and receipts (committed literals; see the module docstring for the arithmetic)
# ---------------------------------------------------------------------------

class Who(NamedTuple):
    first: str | None
    last: str
    street: str
    city: str
    state: str
    zip: str
    occupation: str | None
    employer: str | None

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}" if self.first else self.last

    @property
    def key(self) -> str:
        """The site donor key: lower(trim(first|last|address1|address2|city|state|zip))."""
        return f"{self.first or ''}|{self.last}|{self.street}||{self.city}|{self.state}|{self.zip}".strip().lower()

    @property
    def address_forms(self) -> set[str]:
        """The two address spellings in use today: summary builder 'IL, 60601', live branch A 'IL 60601'."""
        head = f"{self.street}, {self.city}, {self.state}"
        return {f"{head}, {self.zip}", f"{head} {self.zip}"}


ALICE = Who("Alice", "Donor", "100 Main St", "Chicago", "IL", "60601", "Lawyer", "BigLaw LLC")
BOB = Who("Bob", "Donor", "200 Elm St", "Evanston", "IL", "60201", "Teacher", "CPS")
EDGAR = Who("Edgar", "Edge", "7 Edge Rd", "Springfield", "IL", "62701", "Surveyor", "Edge Surveys")
WHALE = Who("Willa", "Whale", "1 Lake Shore Dr", "Chicago", "IL", "60611", None, None)
TIE_ALPHA = Who("Tie", "Alpha", "11 Tie St", "Chicago", "IL", "60601", None, None)
TIE_BRAVO = Who("Tie", "Bravo", "12 Tie St", "Chicago", "IL", "60601", None, None)
TIE_CHARLIE = Who("Tie", "Charlie", "13 Tie St", "Chicago", "IL", "60601", None, None)
TIE_DELTA = Who("Tie", "Delta", "14 Tie St", "Chicago", "IL", "60601", None, None)
ADA = Who("Ada", "Archived", "1 Gone Ave", "Chicago", "IL", "60601", None, None)
PAT = Who("Pat", "Transfer", "2 Gone Ave", "Chicago", "IL", "60601", None, None)
UNA = Who("Una", "Blankpart", "3 Gone Ave", "Chicago", "IL", "60601", None, None)
NORA = Who("Nora", "NoDtwo", "4 Gone Ave", "Chicago", "IL", "60601", None, None)
SAM = Who("Sam", "Superseded", "5 Gone Ave", "Chicago", "IL", "60601", None, None)
LENA = Who("Lena", "Late", "9 Late Ln", "Chicago", "IL", "60601", "Writer", "Self")
OLGA = Who("Olga", "Old", "3 Old Rd", "Chicago", "IL", "60601", "Retired", None)
CAROL = Who("Carol", "Donor", "303 Pine St", "Naperville", "IL", "60540", "Teacher", "District 203")
BIGCORP = Who(None, "BigCorp", "500 LaSalle", "Chicago", "IL", "60606", None, None)
NINA = Who("Nina", "Nodate", "9 Blank Ave", "Chicago", "IL", "60601", None, None)
NANCY = Who("Nancy", "Newbie", "2 New St", "Chicago", "IL", "60601", None, None)

DOC_OF = {100: 5001, 200: 5002}
_SAME = object()


def receipt(rid, who, committee, date, amount, *, doc=None, part="1A", archived=False,
            occupation=_SAME, employer=_SAME):
    """One receipt row, in the column order of RECEIPT_SQL."""
    return (
        rid, committee, doc or DOC_OF[committee], who.last, who.first, date, amount, part,
        who.occupation if occupation is _SAME else occupation,
        who.employer if employer is _SAME else employer,
        who.city, who.state, who.zip, who.street, archived,
    )


BASE_RECEIPTS = [
    receipt(30001, ALICE, 100, "2025-01-15", 5000.00),     # Alice cycle 2026: 5000 (c100) ...
    receipt(30002, ALICE, 200, "2025-01-20", 10000.00),    # ... + 10000 (c200) = 15000, 2 receipts, 2 committees
    receipt(30003, ALICE, 200, "2023-03-01", 700.00),      # cycle 2024: 700, 1 receipt, 1 committee (not 2: the
                                                           # all-time committee count is 2)
    receipt(30010, BOB, 100, "2025-02-10", 2500.00),       # Bob 2500 / 1 / 1 ...
    receipt(30011, BOB, 100, "2025-02-11", -500.00),       # ... a refund is not netted: amount <= 0 is excluded
    receipt(30012, BOB, 100, "2025-02-12", 0.00),          # ... and a zero is not counted
    receipt(30020, EDGAR, 100, "2023-01-01", 1.00),        # first day of cycle 2024
    receipt(30021, EDGAR, 100, "2024-12-31", 2.00),        # last day of cycle 2024  -> 2024: 1 + 2 = 3, 2 receipts
    receipt(30022, EDGAR, 100, "2025-01-01", 4.00),        # first day of cycle 2026
    receipt(30023, EDGAR, 100, "2026-12-31", 8.00),        # last day of cycle 2026  -> 2026: 4 + 8 = 12, 2 receipts
    receipt(30030, WHALE, 100, "2025-06-01", 50123456.78),  # float4 would store 50123456
    receipt(30040, TIE_DELTA, 200, "2025-08-04", 1200.00),
    receipt(30041, TIE_CHARLIE, 100, "2025-08-03", 1200.00),
    receipt(30042, TIE_BRAVO, 200, "2025-08-02", 1200.00),
    receipt(30043, TIE_ALPHA, 100, "2025-08-01", 1200.00),
    receipt(30050, ADA, 100, "2025-03-03", 999.00, archived=True),   # archived
    receipt(30051, PAT, 100, "2025-03-04", 888.00, part="2A"),       # transfer in, not an itemized contribution
    receipt(30052, UNA, 100, "2025-03-05", 777.00, part=None),       # blank D-2 part
    receipt(30053, NORA, 100, "2025-03-06", 666.00, doc=5099),       # filed doc without any D-2 report
    receipt(30054, SAM, 100, "2025-03-07", 555.00, doc=5004),        # filed doc whose only D-2 is archived
]

LATE_RECEIPTS = [
    receipt(30101, LENA, 100, "2026-03-05", 4444.00),
    receipt(30102, ALICE, 100, "2026-04-04", 1000.00),
    receipt(30103, OLGA, 100, "2022-05-05", 333.00),
    receipt(30104, EDGAR, 100, "2024-06-06", 16.00),
]

CAROL_RECEIPTS = [
    receipt(30201, CAROL, 100, "2025-03-01", 300.00),                                        # Teacher / District 203
    receipt(30202, CAROL, 200, "2025-04-01", 200.00, occupation="Educator"),                 # Educator / District 203
    receipt(30203, CAROL, 100, "2026-02-01", 100.00, employer="Naperville Schools"),         # Teacher / Naperville Schools
]

# Expected rows of the base fixture: (cycle, donor name) -> (total, contributions, committees)
BASE_ROWS = {
    (2024, "Alice Donor"): (700.0, 1, 1),
    (2024, "Edgar Edge"): (3.0, 2, 1),
    (2026, "Alice Donor"): (15000.0, 2, 2),
    (2026, "Bob Donor"): (2500.0, 1, 1),
    (2026, "Edgar Edge"): (12.0, 2, 1),
    (2026, "Tie Alpha"): (1200.0, 1, 1),
    (2026, "Tie Bravo"): (1200.0, 1, 1),
    (2026, "Tie Charlie"): (1200.0, 1, 1),
    (2026, "Tie Delta"): (1200.0, 1, 1),
    (2026, "Willa Whale"): (50123456.78, 1, 1),
    # Plan J-S2 / J-S16: the list predicate keeps blank-part and no-D-2 receipts (the summary's predicate does not).
    (2026, "Una Blankpart"): (777.0, 1, 1),
    (2026, "Nora NoDtwo"): (666.0, 1, 1),
    (2026, "Sam Superseded"): (555.0, 1, 1),
}
WHALE_ROW = (2026, "Willa Whale")

# cycle 2026, base fixture, in list order
BY_TOTAL_DESC = ["Willa Whale", "Alice Donor", "Bob Donor", "Tie Alpha", "Tie Bravo", "Tie Charlie", "Tie Delta",
                 "Una Blankpart", "Nora NoDtwo", "Sam Superseded", "Edgar Edge"]  # equal totals fall back on the name
BY_TOTAL_ASC = ["Edgar Edge", "Sam Superseded", "Nora NoDtwo", "Una Blankpart", "Tie Alpha", "Tie Bravo", "Tie Charlie",
                "Tie Delta", "Bob Donor", "Alice Donor", "Willa Whale"]
BY_NAME_ASC = ["Alice Donor", "Bob Donor", "Edgar Edge", "Nora NoDtwo", "Sam Superseded", "Tie Alpha", "Tie Bravo",
               "Tie Charlie", "Tie Delta", "Una Blankpart", "Willa Whale"]
BY_CONTRIBUTIONS_DESC = ["Alice Donor", "Edgar Edge", "Bob Donor", "Nora NoDtwo", "Sam Superseded", "Tie Alpha",
                         "Tie Bravo", "Tie Charlie", "Tie Delta", "Una Blankpart", "Willa Whale"]
                         # 2 receipts: Alice, Edgar; 1 receipt: everyone else (by name)
BY_COMMITTEES_DESC = ["Alice Donor", "Bob Donor", "Edgar Edge", "Nora NoDtwo", "Sam Superseded", "Tie Alpha",
                      "Tie Bravo", "Tie Charlie", "Tie Delta", "Una Blankpart", "Willa Whale"]
                      # 2 committees: Alice; 1 committee: everyone else (by name)


# ---------------------------------------------------------------------------
# Environment: two branches, same receipts
# ---------------------------------------------------------------------------

RECEIPT_SQL = (
    "INSERT INTO {table} (id, committee_id, filed_doc_id, last_name, first_name, received_date, amount, d2_part,"
    " occupation, employer, city, state, zipcode, address1, archived)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

ISBE_COMPAT_VIEW = """
    CREATE VIEW bulk_receipts_clean AS
    SELECT r.id AS bulk_row_id, r.id AS receipt_record_id, r.committee_id AS committee_id_sbe, r.filed_doc_id,
           r.last_name AS last_or_business_name, r.first_name, r.received_date::text AS received_date,
           r.amount, r.occupation, r.employer, r.address1 AS address_line_1, r.address2 AS address_line_2,
           r.city, r.state, r.zipcode AS postal_code, r.d2_part AS d2_part_code,
           CASE WHEN r.archived THEN 1 ELSE 0 END AS is_archived
    FROM isbe_condensed_receipts r
"""


class Env:
    def __init__(self, app, branch: str):
        self.app = app
        self.branch = branch

    @property
    def table(self) -> str:
        return "isbe_receipts" if self.branch == "bulk" else "isbe_condensed_receipts"

    def conn(self):
        return get_db(self.app.config["DATABASE_PATH"])

    def add(self, receipts) -> None:
        conn = self.conn()
        conn.executemany(RECEIPT_SQL.format(table=self.table), receipts)
        conn.commit()
        conn.close()

    def add_late(self) -> None:
        self.add(LATE_RECEIPTS)

    def refresh(self) -> dict:
        conn = self.conn()
        stats = refresh_analytics_materialized(conn)
        conn.close()
        return stats

    def get(self, url: str) -> str:
        response = self.app.test_client().get(url)
        assert response.status_code == 200, f"GET {url} -> {response.status_code}"
        return response.get_data(as_text=True)


def _build(tmp_path, branch: str) -> Env:
    db_path = str(tmp_path / "donor_cycle.db")
    init_db(db_path)
    conn = get_db(db_path)
    if branch == "bulk":
        _seed_isbe_tables(conn)
        receipts = "isbe_receipts"
    else:
        _seed_isbe_condensed_tables(conn)
        receipts = "isbe_condensed_receipts"
    conn.execute(f"DELETE FROM {receipts}")
    conn.execute(f"ALTER TABLE {receipts} ALTER COLUMN amount TYPE DOUBLE PRECISION")
    if branch == "isbe":
        conn.execute(f"ALTER TABLE {receipts} ALTER COLUMN received_date TYPE DATE USING received_date::date")
    conn.executemany(RECEIPT_SQL.format(table=receipts), BASE_RECEIPTS)
    # D-2 reports: 5001 and 5002 active, 5004 superseded (archived), 5099 has none.
    conn.execute("DELETE FROM isbe_d2_reports")
    conn.execute(
        "INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, archived)"
        " VALUES (9001, 100, 5001, FALSE), (9002, 200, 5002, FALSE), (9004, 100, 5004, TRUE)"
    )
    conn.commit()
    if branch == "bulk":
        _create_compat_views(conn)
    else:
        conn.execute(ISBE_COMPAT_VIEW)
        conn.commit()
    stats = refresh_analytics_materialized(conn)
    conn.close()
    if branch == "isbe":
        assert stats.get("source_path") == "isbe_direct", f"fixture error: the isbe fixture took {stats}"
    else:
        assert "source_path" not in stats, f"fixture error: the bulk fixture took {stats}"
    app = create_app({"TESTING": True, "DATABASE_PATH": db_path,
                      "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})
    return Env(app, branch)


@pytest.fixture(params=["bulk", "isbe"])
def env(request, tmp_path):
    """Both refresh branches (the SQL that fills the table differs per branch)."""
    return _build(tmp_path, request.param)


@pytest.fixture
def prod_env(tmp_path):
    """The production branch only: the read path does not depend on which refresh built the rows."""
    return _build(tmp_path, "isbe")


@pytest.fixture
def empty_conn(tmp_path):
    """Schema only (for the table-shape tests)."""
    db_path = str(tmp_path / "schema_only.db")
    init_db(db_path)
    return get_db(db_path)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def cycle_rows(conn, source: str = SOURCE) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM {TABLE} WHERE source = ? ORDER BY election_cycle, donor_name", (source,))
    return [dict(row) for row in rows.fetchall()]


def row_key(row: dict) -> tuple:
    return row["election_cycle"], row["donor_name"]


def row_values(row: dict) -> tuple:
    return round(float(row["total_amount"]), 2), int(row["contribution_count"]), int(row["committee_count"])


def values_by_row(rows: list[dict]) -> dict:
    assert len({row_key(r) for r in rows}) == len(rows), "two rows share a (cycle, donor name)"
    return {row_key(r): row_values(r) for r in rows}


def donors(conn, bounds, **kwargs) -> list:
    low, high = bounds
    kwargs.setdefault("limit", 500)
    return Donor.get_all_with_totals(conn, transaction_date_from=low, transaction_date_to=high, **kwargs)


def count(conn, bounds) -> int:
    return Donor.count(conn, transaction_date_from=bounds[0], transaction_date_to=bounds[1])


def names(rows) -> list[str]:
    return [row.name for row in rows]


def by_name(rows, name: str):
    matches = [row for row in rows if row.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} row, got {len(matches)}"
    return matches[0]


def insert_cycle_row(conn, source: str, cycle: int, key: str, *, name: str = "Sentinel Donor") -> None:
    """Every column non-null, so this does not depend on which columns the implementation made nullable."""
    conn.execute(
        f"INSERT INTO {TABLE} (source, election_cycle, donor_key, donor_name, donor_address, donor_city, donor_state,"
        " occupation, employer, total_amount, contribution_count, committee_count, updated_at)"
        " VALUES (?, ?, ?, ?, '1 Sentinel Way, Springfield, IL', 'Springfield', 'IL', 'Tester', 'Test Co',"
        " 1.0, 1, 1, CURRENT_TIMESTAMP)",
        (source, cycle, key, name),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def test_table_has_exactly_the_pinned_columns_and_money_precision(empty_conn):
    """Exact column set, an integer cycle, a timestamp, and a money column that keeps cents.

    Kills: a missing or extra column (e.g. a copied local_donor_id); election_cycle as text; total_amount copied
    as REAL from analytics_donor_summary (float4)."""
    rows = empty_conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = ?",
        (TABLE,),
    ).fetchall()
    types = {row["column_name"]: row["data_type"] for row in rows}

    assert set(types) == COLUMNS
    assert types["election_cycle"] in {"integer", "bigint", "smallint"}
    assert types["total_amount"] in {"double precision", "numeric"}
    assert types["updated_at"].startswith("timestamp")


def test_primary_key_is_source_cycle_and_donor_key(empty_conn):
    """The same donor may have a row per cycle and per source; one (source, cycle, donor_key) is unique.

    Kills: a key on (source, donor_key) (a donor could only have one cycle), on donor_key alone, or no key at all
    (a rebuild that appends would silently double every total)."""
    conn = empty_conn
    insert_cycle_row(conn, SOURCE, 2026, "k1")
    insert_cycle_row(conn, SOURCE, 2024, "k1")          # same donor, another cycle
    insert_cycle_row(conn, "contributions", 2026, "k1")  # same donor and cycle, another source

    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_cycle_row(conn, SOURCE, 2026, "k1")

    assert conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE}").fetchone()["n"] == 3


# ---------------------------------------------------------------------------
# The refresh
# ---------------------------------------------------------------------------

def test_refresh_builds_one_row_per_donor_per_cycle_with_exact_totals(env):
    """Hand-computed totals / contributions / committees for every (cycle, donor), and nothing else.

    The exact row set also proves the predicate: Ada (archived), Pat (part 2A) and Bob's refund and zero never produce
    a row or change Bob's numbers; Una (blank part), Nora (no D-2) and Sam (archived D-2) do (plan J-S2 / J-S16).

    Kills: cycle = year (receipts of 2025 would land in cycle 2025), year + 1, year - year % 2 (2025 -> 2024),
    integer-division rounding; windows off by a day (Edgar's 2023-01-01 / 2024-12-31 / 2025-01-01 / 2026-12-31
    receipts split 3 / 12 only when the boundaries are right); committee_count taken from all time (Alice's 2024
    row would say 2) or as the number of receipts; contribution_count of the whole donor; the summary builder's
    predicate instead of `receipts_filter_sql` (Una, Nora and Sam would vanish); netting refunds; counting zero
    amounts; extra rows (a NULL or foreign cycle, another source)."""
    rows = cycle_rows(env.conn())
    expected = {key: value for key, value in BASE_ROWS.items() if key != WHALE_ROW}
    actual = values_by_row([r for r in rows if row_key(r) != WHALE_ROW])

    assert actual == expected
    whale = [r for r in rows if row_key(r) == WHALE_ROW]
    assert len(whale) == 1, "the large donor must have exactly one 2026 row"
    assert (whale[0]["contribution_count"], whale[0]["committee_count"]) == (1, 1)
    assert len(rows) == len(BASE_ROWS) == 13


def test_totals_are_exact_to_the_cent_for_a_very_large_donor(env):
    """One $50,123,456.78 receipt must read back as 50123456.78.

    analytics_donor_summary.total_amount is REAL (float4) and today shows this donor as 50123456.0; copying that DDL
    into the new table would show every donor above ~$16.7M wrong by up to $4 on the cycle list while the custom
    window (double precision) shows the exact figure.  If you decide float4 is acceptable, delete this test.

    Kills: a REAL total_amount column; a CAST(... AS REAL) in the builder."""
    rows = [r for r in cycle_rows(env.conn()) if row_key(r) == WHALE_ROW]

    assert len(rows) == 1
    assert round(float(rows[0]["total_amount"]), 2) == 50123456.78


def test_cycle_rows_agree_with_the_all_time_summary_builder(env):
    """Same donor keys and descriptive columns as analytics_donor_summary where both hold the donor, the cycle totals
    add back up to it, and the only cycle-only donors are the three the summary's stricter predicate drops.

    Kills: a different donor_key expression (cycle rows no longer match /donors/key/<key> links and the all-time
    list); the summary builder's predicate (Una, Nora and Sam would vanish) or any other predicate drift (the
    per-donor sums would drift apart); descriptive columns taken from the wrong donor.  The address punctuation is deliberately not pinned (the summary builder
    writes 'IL, 60601', branch A writes 'IL 60601'): either is accepted, anything else fails.
    The large donor is skipped in the sum check: the summary stores it as float4 (see the previous test)."""
    conn = env.conn()
    rows = cycle_rows(conn)
    summary = {r["donor_key"]: dict(r) for r in conn.execute(
        "SELECT * FROM analytics_donor_summary WHERE source = ?", (SOURCE,)).fetchall()}
    people = {w.key: w for w in (ALICE, BOB, EDGAR, WHALE, TIE_ALPHA, TIE_BRAVO, TIE_CHARLIE, TIE_DELTA, UNA, NORA, SAM)}
    assert rows, "precondition: the cycle table is empty"
    # Plan J-S2 / J-S16: list predicate here, D-2 EXISTS in the all-time summary -- exactly these three differ.
    cycle_only = {row["donor_key"] for row in rows if row["donor_key"] not in summary}
    assert cycle_only == {UNA.key, NORA.key, SAM.key}

    cycle_totals: dict[str, float] = {}
    cycle_counts: dict[str, int] = {}
    for row in rows:
        key = row["donor_key"]
        assert key in people, f"unexpected donor key {key!r}"
        assert row["donor_address"] in people[key].address_forms, f"{key} address {row['donor_address']!r}"
        if key in cycle_only:
            continue
        for column in ("donor_name", "donor_city", "donor_state", "occupation", "employer"):
            assert row[column] == summary[key][column], f"{key} {column}"
        cycle_totals[key] = cycle_totals.get(key, 0.0) + float(row["total_amount"])
        cycle_counts[key] = cycle_counts.get(key, 0) + int(row["contribution_count"])

    for key, total in cycle_totals.items():
        assert cycle_counts[key] == summary[key]["contribution_count"], key
        if key != WHALE.key:
            assert round(total, 2) == round(float(summary[key]["total_amount"]), 2), key


def test_every_row_carries_a_real_recent_updated_at(env):
    """`updated_at` is a timestamp set by the refresh: a parsed datetime within the last hour on every row, not NULL and
    not a constant in the past.  (The window is checked in SQL so it holds for timestamp and timestamptz alike.)

    Kills: NULL (column default dropped), a hard-coded date, a text column holding 'now'."""
    conn = env.conn()
    stamps = [r["updated_at"] for r in cycle_rows(conn)]
    recent = conn.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE source = ?"
        " AND updated_at BETWEEN CURRENT_TIMESTAMP - INTERVAL '1 hour' AND CURRENT_TIMESTAMP + INTERVAL '5 seconds'",
        (SOURCE,),
    ).fetchone()["n"]

    assert len(stamps) == 13
    for stamp in stamps:
        assert isinstance(stamp, datetime)
    assert recent == 13


def test_a_second_refresh_rebuilds_the_rows_and_touches_only_its_own_source(env):
    """Change the data, refresh again: the rows follow the data, nothing is left stale or doubled, and rows of
    another source survive.

    Changes: Alice's $10,000 receipt is archived (her 2026 row drops to 5000 / 1 / 1), all of Edgar's receipts are
    deleted (both his rows must vanish), a new donor Nancy gives 90.  A 'contributions' sentinel row sits in cycle
    2026 throughout.  Expected after: cycle 2024 = Alice only; cycle 2026 = Whale, Alice 5000, Bob, Tie x4, Nancy.

    Kills: an append-only or ON CONFLICT DO NOTHING rebuild (Alice stays at 15000); an upsert without delete (Edgar's
    rows stay); a rebuild that crashes on the second run (primary key); DELETE without the source predicate
    (sentinel gone); rows not refreshed (updated_at older than the first build)."""
    conn = env.conn()
    insert_cycle_row(conn, "contributions", 2026, "donor:999", name="Sentinel Donor")
    first_build = max(r["updated_at"] for r in cycle_rows(conn))

    conn.execute(f"UPDATE {env.table} SET archived = TRUE WHERE id = 30002")
    conn.execute(f"DELETE FROM {env.table} WHERE last_name = 'Edge'")
    conn.commit()
    env.add([receipt(30501, NANCY, 200, "2025-07-07", 90.00)])
    env.refresh()

    rows = cycle_rows(conn)
    expected = {key: value for key, value in BASE_ROWS.items() if key[1] not in ("Edgar Edge", "Willa Whale")}
    expected[(2026, "Alice Donor")] = (5000.0, 1, 1)
    expected[(2026, "Nancy Newbie")] = (90.0, 1, 1)
    actual = values_by_row([r for r in rows if row_key(r) != WHALE_ROW])
    assert actual == expected
    assert len([r for r in rows if row_key(r) == WHALE_ROW]) == 1
    assert min(r["updated_at"] for r in rows) >= first_build

    sentinels = cycle_rows(conn, "contributions")
    assert [(r["election_cycle"], r["donor_key"], row_values(r)) for r in sentinels] == [(2026, "donor:999", (1.0, 1, 1))]


def test_a_donor_whose_receipts_vary_in_occupation_gets_one_row_per_cycle(env):
    """Carol's three 2026 receipts carry two occupations and two employers: one row, summed, not three.

    Which variant is shown is NOT pinned (the summary builder takes MAX(); any of the observed values is accepted).

    Kills: grouping by the display columns as branch A does (three rows, or a primary-key crash on refresh); keeping
    only the first group with ON CONFLICT DO NOTHING (total 300 instead of 600); a NULL occupation for mixed donors."""
    env.add(CAROL_RECEIPTS)
    env.refresh()

    rows = [r for r in cycle_rows(env.conn()) if r["donor_name"] == "Carol Donor"]

    assert len(rows) == 1
    assert (rows[0]["election_cycle"], row_values(rows[0])) == (2026, (600.0, 3, 2))
    assert rows[0]["donor_key"] == CAROL.key
    assert rows[0]["occupation"] in {"Teacher", "Educator"}
    assert rows[0]["employer"] in {"District 203", "Naperville Schools"}


def test_a_donor_without_a_first_name_keeps_the_site_key_and_name(env):
    """BigCorp has no first name (NULL): key '|bigcorp|500 lasalle||chicago|il|60606', name 'BigCorp'.

    Kills: concatenation without COALESCE (NULL key, primary-key crash), a name like 'None BigCorp', a key that
    differs from analytics_donor_summary's for business donors."""
    env.add([receipt(30301, BIGCORP, 100, "2025-05-01", 25000.00)])
    env.refresh()

    conn = env.conn()
    rows = [r for r in cycle_rows(conn) if r["donor_key"] == BIGCORP.key]
    summary = conn.execute(
        "SELECT donor_name FROM analytics_donor_summary WHERE source = ? AND donor_key = ?", (SOURCE, BIGCORP.key)
    ).fetchall()

    assert BIGCORP.key == "|bigcorp|500 lasalle||chicago|il|60606"
    assert len(rows) == 1
    assert (rows[0]["election_cycle"], rows[0]["donor_name"], row_values(rows[0])) == (2026, "BigCorp", (25000.0, 1, 1))
    assert [r["donor_name"] for r in summary] == ["BigCorp"]


def test_a_receipt_without_a_date_gets_no_cycle_row_and_does_not_break_the_refresh(env):
    """Nina's only receipt has no received_date: the all-time summary counts it (it passes the predicate), but no
    cycle can be derived, so the cycle table has no row for her and the refresh still completes.

    Kills: a NULL election_cycle (primary-key violation, refresh crashes); a coerced cycle such as 0 or the current
    year; dropping the receipt from the all-time summary as a side effect."""
    env.add([receipt(30401, NINA, 100, None, 50.00)])
    env.refresh()

    conn = env.conn()
    summary = conn.execute(
        "SELECT total_amount FROM analytics_donor_summary WHERE source = ? AND donor_key = ?", (SOURCE, NINA.key)
    ).fetchall()
    rows = cycle_rows(conn)

    assert [float(r["total_amount"]) for r in summary] == [50.0], "precondition: the receipt qualifies"
    assert [r for r in rows if r["donor_key"] == NINA.key] == []
    assert values_by_row([r for r in rows if row_key(r) != WHALE_ROW]) == {
        key: value for key, value in BASE_ROWS.items() if key != WHALE_ROW}


# ---------------------------------------------------------------------------
# Reads: Donor.get_all_with_totals / Donor.count
# ---------------------------------------------------------------------------

def test_exact_cycle_windows_are_served_from_the_aggregate(prod_env):
    """With late receipts in cycles 2026 and 2024, an exact cycle window still answers with the refreshed numbers:
    Lena (new donor) absent, Alice 15000 not 16000, Edgar 3 not 19, and `count` agrees with the list.

    Kills: ignoring the table (today's live path); reading it for the current cycle only; serving the list from the
    table but the count live (the count would say 12: Lena is live-only); cycle detection that
    only compares the start date."""
    prod_env.add_late()
    conn = prod_env.conn()

    rows_2026 = donors(conn, W2026)
    assert names(rows_2026) == BY_TOTAL_DESC
    assert by_name(rows_2026, "Alice Donor").total_amount == 15000.0
    assert count(conn, W2026) == 11

    rows_2024 = donors(conn, W2024)
    assert names(rows_2024) == ["Alice Donor", "Edgar Edge"]
    assert by_name(rows_2024, "Edgar Edge").total_amount == 3.0
    assert by_name(rows_2024, "Alice Donor").total_amount == 700.0
    assert count(conn, W2024) == 2


# (window, a donor the live path reports in it, with its live total).  Lena's late receipt is dated 2026-03-05;
# the off-cycle two-year window ends in 2025, so its witness is Edgar: 2 (2024-12-31) + 4 (2025-01-01) + the late 16
# (2024-06-06) = 22; his 2023-01-01 and 2026-12-31 receipts fall outside it.
NEAR_MISSES = [
    (("2025-01-01", "2026-06-30"), "Lena Late", 4444.0),   # ends mid-cycle (the brief's example)
    (("2025-01-02", "2026-12-31"), "Lena Late", 4444.0),   # starts a day late
    (("2025-01-01", "2026-12-30"), "Lena Late", 4444.0),   # ends a day early
    (("2024-12-31", "2026-12-31"), "Lena Late", 4444.0),   # starts a day early
    (("2025-01-01", "2027-01-01"), "Lena Late", 4444.0),   # ends a day late
    (("2023-01-01", "2026-12-31"), "Lena Late", 4444.0),   # two whole cycles
    (("2026-01-01", "2026-12-31"), "Lena Late", 4444.0),   # calendar 2026 only: half a cycle
    (("2025-01-01", None), "Lena Late", 4444.0),           # open ended
    ((None, "2026-12-31"), "Lena Late", 4444.0),           # open start
    (("2024-01-01", "2025-12-31"), "Edgar Edge", 22.0),    # two years, but not an election cycle (cycles end in even years)
]


def test_windows_that_are_not_exactly_one_cycle_use_the_live_receipts(prod_env):
    """Every near miss reports the late receipts (the live path), and its count matches its own list.

    The exact cycle window is checked first, in the same state, so the test cannot pass on a path that is live
    everywhere: it must exclude Lena, the near misses must include her.

    Kills: treating any window inside a cycle as the cycle (a start-year or end-year check), a window that merely
    starts on the cycle's first day, or ends on its last; reading the aggregate for multi-cycle sums; a count that
    still reads the table for windows the list reads live."""
    prod_env.add_late()
    conn = prod_env.conn()
    assert "Lena Late" not in names(donors(conn, W2026)), "precondition: the exact cycle window is served from the aggregate"

    for bounds, witness, live_total in NEAR_MISSES:
        rows = donors(conn, bounds)
        assert witness in names(rows), f"window {bounds} must read the live receipts"
        assert by_name(rows, witness).total_amount == live_total, f"window {bounds}: {witness}"
        assert count(conn, bounds) == len({row.donor_key for row in rows}), f"window {bounds}: count does not match its list"


def test_cycle_rows_have_the_shape_and_values_of_the_live_rows(prod_env):
    """For every donor of cycle 2026 the aggregate row equals the live row captured before the late receipts landed:
    same key, name, occupation, employer, city, state, totals, source, no local id, no entity id, numeric types.

    Kills: rows missing the donor_key (no /donors/key link), an entity-style row, a Decimal total, wrong source tag,
    a different city/state/employer, an address that is not one of the two accepted spellings."""
    conn = prod_env.conn()
    live = {row.donor_key: row for row in donors(conn, WHOLE_PERIOD)}   # live path, before the late data
    prod_env.add_late()

    rows = donors(conn, W2026)

    assert len(rows) == 11, "precondition: the cycle window must be served from the aggregate"
    people = {w.key: w for w in (ALICE, BOB, EDGAR, WHALE, TIE_ALPHA, TIE_BRAVO, TIE_CHARLIE, TIE_DELTA, UNA, NORA, SAM)}
    for row in rows:
        reference = live[row.donor_key]
        for field in ("name", "occupation", "employer", "donor_city", "donor_state", "contribution_count",
                      "committee_count", "source", "id", "entity_id"):
            assert getattr(row, field) == getattr(reference, field), f"{row.name}: {field}"
        assert round(row.total_amount, 2) == round(reference.total_amount, 2), row.name
        assert row.address in people[row.donor_key].address_forms, f"{row.name}: address {row.address!r}"
        assert row.source == SOURCE
        assert row.id is None
        assert row.entity_id is None
        assert isinstance(row.total_amount, float)
        assert isinstance(row.contribution_count, int)
        assert isinstance(row.committee_count, int)


def test_list_rows_and_count_agree_and_each_donor_key_appears_once(prod_env):
    """Carol's receipts vary in occupation and employer.  Today's windowed list shows her three times (it groups by
    seven columns) while the count groups by donor_key; on the cycle path both say one.

    Kills: copying branch A's GROUP BY into the table build or the read; a count that differs from the list;
    duplicate donor_key rows."""
    prod_env.add(CAROL_RECEIPTS)
    prod_env.refresh()
    conn = prod_env.conn()

    for bounds, expected in ((W2026, 12), (W2024, 2)):
        rows = donors(conn, bounds)
        assert len(rows) == count(conn, bounds) == expected, f"window {bounds}"
        assert len({row.donor_key for row in rows}) == len(rows), f"window {bounds}: repeated donor_key"

    carol = by_name(donors(conn, W2026), "Carol Donor")
    assert (carol.total_amount, carol.contribution_count, carol.committee_count) == (600.0, 3, 2)


def test_sorting_by_total_and_by_name_both_ways_with_the_name_breaking_ties(prod_env):
    """Four donors tie at 1200: the order among them is the name, ascending, in every total sort.

    Late data is present so the live path cannot satisfy this by accident.  The two count columns are included because
    the template links ten sortable columns and a sort map that only knows total and name would silently sort the rest
    by total (the text columns -- address, occupation, employer, city, state, source -- are an accepted hole).
    Kills: no secondary order (ties in physical order: Delta first, because the base receipts were inserted that
    way), tie-break descending with the primary, ignoring dir, ignoring sort_by, sorting on the wrong column."""
    prod_env.add_late()
    conn = prod_env.conn()

    assert names(donors(conn, W2026, sort_by="total_amount", sort_dir="desc")) == BY_TOTAL_DESC
    assert names(donors(conn, W2026, sort_by="total_amount", sort_dir="asc")) == BY_TOTAL_ASC
    assert names(donors(conn, W2026, sort_by="name", sort_dir="asc")) == BY_NAME_ASC
    assert names(donors(conn, W2026, sort_by="name", sort_dir="desc")) == BY_NAME_ASC[::-1]
    assert names(donors(conn, W2026, sort_by="contribution_count", sort_dir="desc")) == BY_CONTRIBUTIONS_DESC
    assert names(donors(conn, W2026, sort_by="committee_count", sort_dir="desc")) == BY_COMMITTEES_DESC


def test_limit_and_offset_page_through_the_cycle_list_without_gaps_or_repeats(prod_env):
    """Pages of three over the eleven-row list, in the sorted order.

    Kills: an offset that is ignored or applied twice, a limit that is not applied, pages that overlap or skip
    because the order is not total."""
    prod_env.add_late()
    conn = prod_env.conn()

    pages = [names(donors(conn, W2026, limit=3, offset=offset)) for offset in (0, 3, 6, 9, 12)]

    assert pages == [BY_TOTAL_DESC[0:3], BY_TOTAL_DESC[3:6], BY_TOTAL_DESC[6:9], BY_TOTAL_DESC[9:11], []]


def test_an_unknown_sort_key_is_not_interpolated_into_the_sql(prod_env):
    """sort_by and dir come straight from the query string (`?sort=`, `?dir=`).  An unknown value falls back to the default
    order and must never reach the SQL text.

    Kills: ORDER BY {sort_by} or {sort_dir} built from the raw value (a syntax error, or worse, an injectable order
    clause)."""
    prod_env.add_late()
    conn = prod_env.conn()

    evil = "; DROP TABLE analytics_donor_cycle_summary; --"
    assert names(donors(conn, W2026, sort_by="total_amount DESC" + evil)) == BY_TOTAL_DESC
    assert names(donors(conn, W2026, sort_dir="asc" + evil)) == BY_TOTAL_DESC


def test_a_cycle_without_aggregate_rows_reads_the_live_receipts(prod_env):
    """Cycle 2022 has no aggregate rows (nothing was received before the refresh); a receipt dated 2022 arrives
    later.  The list shows it (live path), it does not return an empty list.  Then the whole aggregate for the source
    is cleared, which is the state between a deploy and the first refresh: cycle 2026 must show live data too.

    Kills: 'cycle window => read the table' without checking the table has rows for that source and cycle (an
    empty page for 2022 and, right after a deploy, for every cycle); checking only that the table is non-empty."""
    prod_env.add_late()
    conn = prod_env.conn()
    assert conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE source = ? AND election_cycle = 2022",
                        (SOURCE,)).fetchone()["n"] == 0, "precondition"

    rows_2022 = donors(conn, W2022)
    assert names(rows_2022) == ["Olga Old"]
    assert rows_2022[0].total_amount == 333.0
    assert count(conn, W2022) == 1

    conn.execute(f"DELETE FROM {TABLE} WHERE source = ?", (SOURCE,))
    conn.commit()
    rows_2026 = donors(conn, W2026)
    assert "Lena Late" in names(rows_2026)
    assert by_name(rows_2026, "Alice Donor").total_amount == 16000.0
    assert count(conn, W2026) == len({row.donor_key for row in rows_2026})


def test_a_missing_table_falls_back_to_the_live_path_instead_of_failing(prod_env):
    """A database that has not run `init-db` since this change has no `analytics_donor_cycle_summary`; the donors
    list must keep working on the live path (CLAUDE.md: check `_table_exists()` before reading a derived table).  The
    exact cycle window is served from the aggregate first (precondition), then the table is dropped.

    Kills: a read that assumes the table exists (every cycle-scoped donors page would answer 500 until init-db ran)."""
    prod_env.add_late()
    conn = prod_env.conn()
    assert "Lena Late" not in names(donors(conn, W2026)), "precondition: the exact cycle window is served from the aggregate"

    conn.execute(f"DROP TABLE {TABLE}")
    conn.commit()
    rows = donors(conn, W2026)

    assert "Lena Late" in names(rows)
    assert count(conn, W2026) == len({row.donor_key for row in rows})
    assert prod_env.get("/donors/?cycle=2026").count("Lena Late") >= 1


def test_rows_of_another_source_are_ignored_by_every_read(prod_env):
    """Sentinel rows of source 'contributions' sit in cycles 2022, 2024 and 2026.  They must not appear in the lists,
    must not change the counts, and must not count as 'the cycle has aggregate rows' (cycle 2022 still falls back to
    the live path).

    Kills: a read or a rows-exist check without the source predicate."""
    prod_env.add_late()
    conn = prod_env.conn()
    for cycle in (2022, 2024, 2026):
        insert_cycle_row(conn, "contributions", cycle, f"donor:{cycle}", name=f"Sentinel {cycle}")

    assert names(donors(conn, W2026)) == BY_TOTAL_DESC
    assert count(conn, W2026) == 11
    assert names(donors(conn, W2024)) == ["Alice Donor", "Edgar Edge"]
    assert count(conn, W2024) == 2
    assert names(donors(conn, W2022)) == ["Olga Old"]
    assert count(conn, W2022) == 1


# ---------------------------------------------------------------------------
# The /donors/ page
# ---------------------------------------------------------------------------

class _DonorTable(HTMLParser):
    """Rows of the donors table: the ten data cells, the donor-key link, the first-cell text."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self._in_body = False
        self._row: dict | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self._in_body = True
        elif self._in_body and tag == "tr":
            self._row = {"cells": [], "href": None}
        elif self._row is not None and tag == "td":
            self._cell = []
        elif self._cell is not None and tag == "a" and self._row["href"] is None and len(self._row["cells"]) == 1:
            self._row["href"] = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "tbody":
            self._in_body = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def donor_rows(html: str) -> list[dict]:
    parser = _DonorTable()
    parser.feed(html)
    rows = []
    for raw in parser.rows:
        if raw["cells"][0].startswith("No donors found"):
            continue
        cells = raw["cells"]
        key = unquote(urlparse(raw["href"]).path.removeprefix("/donors/key/")) if raw["href"] else None
        rows.append({
            "source": cells[0], "name": cells[1], "address": cells[2], "occupation": cells[3], "employer": cells[4],
            "city": cells[5], "state": cells[6], "total": cells[7], "contributions": cells[8],
            "committees": cells[9], "key": key, "href": raw["href"],
        })
    return rows


def total_line(html: str) -> int:
    match = re.search(r"(\d+) total donors", html)
    assert match, "no 'N total donors' line"
    return int(match.group(1))


def row_named(rows: list[dict], name: str) -> dict:
    matches = [row for row in rows if row["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} row, got {len(matches)}"
    return matches[0]


def test_page_serves_a_cycle_scope_from_the_aggregate_and_a_custom_window_live(prod_env):
    """Same data, two scopes, two answers.  `?cycle=2026` ignores the late receipts (list, totals and the
    'N total donors' line); `?date_from=2025-01-01&date_to=2026-06-30` includes them.  Rows link to the donor-key
    page with the scope carried.

    Kills: a route that never reaches the cycle path (it passes other dates, or the model is only called with
    cycle=...), a page whose count line is live while the rows are not."""
    prod_env.add_late()

    cycle_page = prod_env.get("/donors/?cycle=2026")
    rows = donor_rows(cycle_page)
    assert [r["name"] for r in rows] == BY_TOTAL_DESC
    assert total_line(cycle_page) == 11
    alice = row_named(rows, "Alice Donor")
    assert alice["total"] == "$15,000.00"
    assert (alice["contributions"], alice["committees"]) == ("2", "2")
    assert alice["source"] == SOURCE
    assert alice["key"] == ALICE.key
    assert "source=bulk_receipts" in alice["href"]
    assert "period=cycle" in alice["href"]
    assert row_named(rows, "Willa Whale")["total"] == "$50,123,456.78"

    custom = donor_rows(prod_env.get("/donors/?date_from=2025-01-01&date_to=2026-06-30"))
    assert "Lena Late" in [r["name"] for r in custom]
    assert row_named(custom, "Alice Donor")["total"] == "$16,000.00"      # 5000 + 10000 + the late 1000
    assert row_named(custom, "Lena Late")["total"] == "$4,444.00"


def test_page_serves_another_cycle_and_the_period_spelling_from_the_aggregate(prod_env):
    """`?period=cycle:2024` (the spelling the scope bar emits) answers from the cycle-2024 aggregate: two donors, Edgar
    at $3.00 although a late receipt makes his live total $19.00.

    Kills: the current cycle being the only one served, a route that reads `period` differently from `cycle`."""
    prod_env.add_late()

    page = prod_env.get("/donors/?period=cycle:2024")
    rows = donor_rows(page)

    assert [r["name"] for r in rows] == ["Alice Donor", "Edgar Edge"]
    assert total_line(page) == 2
    assert row_named(rows, "Edgar Edge")["total"] == "$3.00"
    assert row_named(rows, "Alice Donor")["total"] == "$700.00"


def test_page_lists_the_live_donors_for_a_cycle_without_aggregate_rows(prod_env):
    """`?cycle=2022` has no aggregate rows; the late 2022 receipt must show, not 'No donors found.'.  The same state
    serves cycle 2026 from the aggregate (precondition), so the page cannot be live everywhere.

    Kills: an empty page for a cycle the refresh has not seen."""
    prod_env.add_late()
    assert "Lena Late" not in [r["name"] for r in donor_rows(prod_env.get("/donors/?cycle=2026"))], "precondition"

    page = prod_env.get("/donors/?cycle=2022")
    rows = donor_rows(page)

    assert [r["name"] for r in rows] == ["Olga Old"]
    assert rows[0]["total"] == "$333.00"
    assert total_line(page) == 1


def test_page_has_one_row_per_donor_key_and_its_total_line_counts_them(prod_env):
    """The visible symptom of the GROUP BY mismatch: Carol appears three times today while the line says N.

    Kills: duplicate rows per donor_key on the cycle path; a total line that differs from the number of rows."""
    prod_env.add(CAROL_RECEIPTS)
    prod_env.refresh()

    page = prod_env.get("/donors/?cycle=2026")
    rows = donor_rows(page)

    assert len(rows) == total_line(page) == 12
    assert len({r["key"] for r in rows}) == 12
    carol = row_named(rows, "Carol Donor")
    assert (carol["total"], carol["contributions"], carol["committees"]) == ("$600.00", "3", "2")


def test_page_sort_parameters_order_the_cycle_list(prod_env):
    """`?sort=name&dir=asc`, `?sort=total_amount&dir=asc` and the default order on the cycle path, with late data
    present so the live path cannot satisfy them.  Kills: the route's sort parameters not reaching the cycle path."""
    prod_env.add_late()

    by_name_asc = donor_rows(prod_env.get("/donors/?cycle=2026&sort=name&dir=asc"))
    by_total_asc = donor_rows(prod_env.get("/donors/?cycle=2026&sort=total_amount&dir=asc"))
    by_default = donor_rows(prod_env.get("/donors/?cycle=2026"))

    assert [r["name"] for r in by_name_asc] == BY_NAME_ASC
    assert [r["name"] for r in by_total_asc] == BY_TOTAL_ASC
    assert [r["name"] for r in by_default] == BY_TOTAL_DESC
