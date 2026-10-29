"""Outside spending (D-2 Part 9) drill-downs -- Lane A of .claude/plans/outside-spending-drilldown.md.

Binds to the plan's "Contract", "Acceptance criteria" 1 and decision rows D1-D7:

  * ``database/outside_spending.py`` -- the shared matcher: ``split_candidate_names``, ``candidate_name_key``,
    ``part9_cycle``, ``attribute_part9_rows``, ``summarize`` and ``get_candidate_outside_spending``.
  * ``/candidates/<id>`` -- the "Outside spending (D-2 Part 9)" section (summary line, by-spender table, link).
  * ``/candidates/<id>/outside-spending`` -- the paginated line-item page (filters, sort, window).
  * ``/analytics/state-races/<race key>`` -- Outside Spending Rows (Candidate / Supporting / Opposing / Total /
    Rows), each row linking to the candidate's line-item page with the race's window.

The matcher is imported INSIDE the tests, so collection succeeds and every test reports its own red.

Fixture world = _seed_isbe_tables + _seed_candidate_profile_rows + _create_compat_views + _seed_inbound_rows +
_seed_part9_rows (below). ``today`` is frozen to 2026-10-26 (current cycle 2026). Every Part-9 row is inserted into
BOTH isbe_expenditures (the raw table, and through it the bulk_expenditures_clean compat view) and
isbe_condensed_expenditures. The candidate pages AND the race builder read isbe_condensed_expenditures: the builder
takes the compat view only when that table is absent, a fixture shape this module never builds (the compat view and
its INTEGER 0/1 flag columns are pinned in tests/test_state_race_builder.py). Id 70020 is raw-only on purpose: a
page that read the raw table or the view would count it.

AMOUNTS ARE MULTIPLES OF 0.25: tests.test_isbe_rewire declares isbe_expenditures.amount as REAL (float4), so a
cent value such as .56 would sum differently in SQL (float4) and in Python (float8), and the two sides of a
reconciliation could disagree by a cent for a reason that has nothing to do with the code under test.

Candidates (name key = first and last token, punctuation stripped, upper-cased)
  900 Pat Example     key (PAT, EXAMPLE)   candidacies 2022 + 2026  => cycles {2022, 2026}
  930 Rae Rival       key (RAE, RIVAL)     candidacy 2026 (added here)  => cycles {2026}
  940 Pat Example     SAME NAME as 900, a different person: one candidacy in the odd year 2023 (a consolidated
                      election) => cycle 2024 (2023 + 2023 % 2). Rows dated 2023-2024 belong to HIM, never to 900.
  970 Dee M O'Brien   record = first_name 'Dee M', last_name "O'Brien"; candidacy 2026 (added here). The filers wrote
                      "Dee O'Brien" and "DEE OBRIEN": the key must come from the first and last TOKEN (DEE, OBRIEN),
                      and the SQL prefilter must strip the apostrophe the way the key does (the Chicago 1B case in
                      miniature: the record carries a middle initial the filings do not).
  910 Quinn Sortwell  no Part-9 rows at all, no candidacy.
  920 Pat Exemplar    no candidacy; one Part-9 row names him (T9). A candidate with no candidacy attaches nothing.
  (blank id) Nell Nullid -- in the race's candidacy feed with a NULL candidate id, so no profile page exists.
Spenders (isbe_committees, linked to no candidate): 701 Alpha Action Fund, 702 Beta Independent Committee,
  703 Gamma Issues PAC.

Part-9 rows (d2_part 9B, not archived unless noted; S = supporting, O = opposing; share = amount / names on the row)
  id     letter spender date        amount     name as filed            dir  attaches to / why
  70001  A      701     2025-01-01  8,000.00   Pat Example              S    900  (first day of the 2026-cycle window)
  70002  B      701     2025-11-03  1,000.00   PAT EXAMPLE              O    900  (upper-case spelling)
  70003  C      702     2026-03-01  2,000.00   Pat Example              S    900  (lower bound of the 03-01..06-30 window)
  70004  D      702     2026-05-15  4,000.00   Pat M. Example           S    900  (middle initial; person payee)
  70005  E      701     2026-06-30  16,000.00  Pat Example              O    900  (upper bound of the 03-01..06-30 window)
  70006  F      703     2026-09-09  32,000.50  Pat Example              S    900  (purpose NULL, office NULL)
  70007  SP     703     2026-07-04  9,000.50   Rae Rival, Pat Example   O    900 AND 930, share 4,500.25 each
  70008  G1     701     2021-11-05  640.00     Pat Example              S    900  (odd year 2021 => cycle 2022)
  70009  G2     702     2022-12-31  128.00     Pat Example              O    900  (last day of cycle 2022)
  70010  R1     703     2026-04-10  20,000.00  Rae Rival                O    930
  70011  R2     702     2026-08-20  10,000.00  Rae Rival                O    930
  70012  R3     701     2025-09-09  500.00     Rae Rival                S    930
  70013  N1     701     2026-02-02  1,500.50   Nell Nullid              S    nobody with a page (race row, no link)
  70021  O1     703     2026-05-05  3,000.00   Dee O'Brien              S    970
  70022  O2     701     2026-06-06  1,000.00   DEE OBRIEN               O    970
  TRAPS, none of which may count for candidate 900 / 930 (and T4 only for 940):
  70014  T4     702     2024-03-15  70,000.00  Pat Example              S    940 only: cycle 2024 is no cycle of 900
                                                                            (it lies BETWEEN 2022 and 2026, so a
                                                                            min..max range gate would let it in)
  70015  T5     703     2026-10-10  300,000.00 Pat Example              S    ARCHIVED
  70016  T6     701     2026-02-14  600,000.00 Pat Example              S    d2_part 1A, not Part 9
  70017  T7     702     2026-04-20  11,000.00  Pat Examples             S    near miss (substring of the last name)
  70018  T8     703     2026-08-30  22,000.00  Patience Example         O    same last name, other first name
  70019  T9     701     2026-03-20  770.00     Pat Exemplar             S    920 has no candidacy => nobody
  70020  T10    702     2022-03-03  5,000.00   Pat Example              S    RAW ONLY: not in isbe_condensed_expenditures
                                                                            (a superseded filing); inside cycle 2022 so
                                                                            it would show on 900's all-time and 2022
                                                                            figures if the page read the raw table, yet
                                                                            outside every race window.

Hand-computed figures, candidate 900 (A..G2 only; the traps are excluded for the reasons above)
  all time          S = 8,000 + 2,000 + 4,000 + 32,000.50 + 640           = 46,640.50     rows A B C D E F SP G1 G2 = 9
                    O = 1,000 + 16,000 + 4,500.25 + 128                   = 21,628.25
                    T = 46,640.50 + 21,628.25                             = 68,268.75     spenders 701 702 703
  cycle 2026        (2025-01-01..2026-12-31)  drops G1 G2
                    S = 8,000 + 2,000 + 4,000 + 32,000.50                 = 46,000.50     rows A B C D E F SP = 7
                    O = 1,000 + 16,000 + 4,500.25                         = 21,500.25
                    T                                                      = 67,500.75
  cycle 2022        (2021-01-01..2022-12-31)  S = 640   O = 128   T = 768    rows G1 G2 = 2
  cycle 2024        (2023-01-01..2024-12-31)  no row attaches: T4 is dated here but belongs to 940
  03-01..06-30      S = 2,000 + 4,000 = 6,000   O = 16,000   T = 22,000      rows C D E = 3 (bounds inclusive)
  from 2026-06-30   S = 32,000.50   O = 16,000 + 4,500.25 = 20,500.25   T = 52,500.75   rows E SP F = 3
  to 2022-12-31     S = 640   O = 128   T = 768    rows G1 G2 = 2 (T10 would make it 5,640 / 5,768)
  by spender, all time (support, oppose, total, rows):
    703  32,000.50   4,500.25  36,500.75  2   (F, SP)
    701   8,640.00  17,000.00  25,640.00  4   (A G1 | B E)
    702   6,000.00     128.00   6,128.00  3   (C D | G2)
  by spender, cycle 2026: 703 same as above | 701 8,000.00 17,000.00 25,000.00 3 | 702 6,000.00 0 6,000.00 2
Candidate 930: S = 500   O = 20,000 + 10,000 + 4,500.25 = 34,500.25   T = 35,000.25   rows R1 R2 R3 SP = 4
Candidate 940: S = 70,000 (T4 only)   rows 1   (none of 900's 2022 / 2026 rows: his only cycle is 2024)
Candidate 970: S = 3,000 (O1)   O = 1,000 (O2)   T = 4,000   rows 2; by spender 703 3,000 | 701 1,000
Race state-representative-representative-district-45, cycle 2026 (the candidacy feed holds 900 twice, once per
committee 901 / 902, plus 930 and the blank-id Nell Nullid):
  window 2025-01-01..2026-12-31   Pat Example  S 46,000.50  O 21,500.25  T 67,500.75  rows 7   (once, not twice)
                                  Rae Rival    S 500.00     O 34,500.25  T 35,000.25  rows 4
                                  Nell Nullid  S 1,500.50   O 0.00       T 1,500.50   rows 1
                                  card = 67,500.75 + 35,000.25 + 1,500.50 = 104,001.50
  window 03-01..06-30             Pat 6,000 / 16,000 / 22,000 / 3 | Rae 0 / 20,000 / 20,000 / 1 (R1) | card 42,000
  window 06-30..12-31 (a page window of 06-30..2027-06-30 clamped to the cycle end)
                                  Pat 32,000.50 / 20,500.25 / 52,500.75 / 3 (E SP F)
                                  Rae 0 / 14,500.25 / 14,500.25 / 2 (SP R2) | card 67,001.00

Noise from the shared fixtures stays in the database on purpose (candidates 1001-1003, committees 100/200/300,
the Part-1 style raw rows of the profile fixture): a query that forgets its candidate filter shows it.

Fixture ``suffix_app`` = outside_app plus SUFFIX_ROWS (below): two candidate records whose last_name carries a
generational suffix, the way ISBE stores them ("Barberis, Jr"), and the Part-9 rows that name them with and without
it. Only the tests that request it see them; every figure above is unchanged.
"""
import copy
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from database.connection import get_db, init_db
from tests.test_candidate_profile import (
    RACE_URL,
    _amount,
    _bar_scope,
    _committee_ids,
    _FrozenDate,
    _get,
    _header_text,
    _ISO,
    _MONEY,
    _seed_candidate_profile_rows,
    _seed_inbound_rows,
    _tables,
    _visible_text,
)
from tests.test_isbe_rewire import _create_compat_views, _seed_isbe_tables
from webapp.app import create_app


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    """'This cycle' is clock-relative; pin the resolver's clock to the 2026 cycle (as test_candidate_profile does)."""
    monkeypatch.setattr("webapp.utils.time_scope.date", _FrozenDate)


# ---------------------------------------------------------------------------
# Fixture rows
# ---------------------------------------------------------------------------

SPENDERS = {
    701: "Alpha Action Fund",
    702: "Beta Independent Committee",
    703: "Gamma Issues PAC",
}


def _p9(row_id, spender, day, amount, name, direction, *, office="State Representative", purpose="Mailers",
        payee="Mailhouse Direct", payee_first=None, part="9B", archived=False, condensed=True):
    """One expenditure row; its filing id is row id + 10000 so every row links to a different filing."""
    return {
        "id": row_id,
        "committee_id": spender,
        "filed_doc_id": row_id + 10000,
        "expended_date": day,
        "amount": amount,
        "candidate_name": name,
        "office": office,
        "supporting": direction == "S",
        "opposing": direction == "O",
        "purpose": purpose,
        "last_name": payee,
        "first_name": payee_first,
        "d2_part": part,
        "archived": archived,
        "condensed": condensed,
    }


# Inserted in this order, which is neither date order nor id order, so a missing ORDER BY shows.
PART9_ROWS = [
    _p9(70008, 701, "2021-11-05", 640.00, "Pat Example", "S", purpose="Yard signs", payee="Prairie Print"),              # G1
    _p9(70005, 701, "2026-06-30", 16000.00, "Pat Example", "O", purpose="Digital advertising", payee="TKO Buying LLC"),  # E
    _p9(70014, 702, "2024-03-15", 70000.00, "Pat Example", "S", office="State Senator", purpose="Television",
        payee="Prairie Media Group"),                                                                                    # T4
    _p9(70001, 701, "2025-01-01", 8000.00, "Pat Example", "S", purpose="Digital advertising", payee="TKO Buying LLC"),   # A
    _p9(70006, 703, "2026-09-09", 32000.50, "Pat Example", "S", office=None, purpose=None,
        payee="Lakeshore Strategies"),                                                                                   # F
    _p9(70017, 702, "2026-04-20", 11000.00, "Pat Examples", "S"),                                                         # T7
    _p9(70003, 702, "2026-03-01", 2000.00, "Pat Example", "S", purpose="Television", payee="Prairie Media Group"),       # C
    _p9(70007, 703, "2026-07-04", 9000.50, "Rae Rival, Pat Example", "O"),                                                # SP
    _p9(70012, 701, "2025-09-09", 500.00, "Rae Rival", "S", purpose="Digital advertising", payee="TKO Buying LLC"),      # R3
    _p9(70004, 702, "2026-05-15", 4000.00, "Pat M. Example", "S", office="State Representative 45th District",
        purpose="Canvassing", payee="Wexler", payee_first="Dana"),                                                       # D
    _p9(70016, 701, "2026-02-14", 600000.00, "Pat Example", "S", part="1A"),                                              # T6
    _p9(70002, 701, "2025-11-03", 1000.00, "PAT EXAMPLE", "O", office="State Rep. 45"),                                   # B
    _p9(70009, 702, "2022-12-31", 128.00, "Pat Example", "O"),                                                            # G2
    _p9(70010, 703, "2026-04-10", 20000.00, "Rae Rival", "O", purpose="Television", payee="Prairie Media Group"),         # R1
    _p9(70015, 703, "2026-10-10", 300000.00, "Pat Example", "S", archived=True),                                          # T5
    _p9(70013, 701, "2026-02-02", 1500.50, "Nell Nullid", "S"),                                                           # N1
    # T8 was "Patricia Example" until search Phase 2 M2 made PATRICIA an alias of PAT; "Patience" is in no alias set.
    _p9(70018, 703, "2026-08-30", 22000.00, "Patience Example", "O"),                                                     # T8
    _p9(70011, 702, "2026-08-20", 10000.00, "Rae Rival", "O", purpose="Television", payee="Prairie Media Group"),         # R2
    _p9(70019, 701, "2026-03-20", 770.00, "Pat Exemplar", "S"),                                                           # T9
    _p9(70020, 702, "2022-03-03", 5000.00, "Pat Example", "S", condensed=False),                                          # T10
    _p9(70022, 701, "2026-06-06", 1000.00, "DEE OBRIEN", "O", office="State Senator"),                                    # O2
    _p9(70021, 703, "2026-05-05", 3000.00, "Dee O'Brien", "S", office="State Senator"),                                   # O1
]


def _sql(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _seed_part9_rows(conn) -> None:
    conn.execute(
        """
        INSERT INTO isbe_committees (id, name, type, active, city, state, zipcode)
        VALUES
            (701, 'Alpha Action Fund', 'Political Action', TRUE, 'Chicago', 'IL', '60601'),
            (702, 'Beta Independent Committee', 'Political Action', TRUE, 'Chicago', 'IL', '60602'),
            (703, 'Gamma Issues PAC', 'Political Action', TRUE, 'Chicago', 'IL', '60603')
        """
    )
    # 940 is the namesake of 900 (same first and last name, a different person); 930, 940 and 970 get their
    # candidacies here.
    conn.execute(
        """
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party,
                                     city, state, zipcode, residence_county)
        VALUES
            (940, 'Example', 'Pat', 'State Senator', 'Senate District', '12', 'Independent',
             'Peoria', 'IL', '61602', 'Peoria'),
            (970, 'O''Brien', 'Dee M', 'State Senator', 'Senate District', '31', 'Republican',
             'Rockford', 'IL', '61101', 'Winnebago')
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_candidacies (candidate_id, election_type, election_year, race_type, outcome)
        VALUES
            (930, 'General Election', 2026, 'challenger', NULL),
            (940, 'Consolidated Election', 2023, 'challenger', 'lost'),
            (970, 'General Election', 2026, 'open seat', NULL)
        """
    )
    for row in PART9_ROWS:
        conn.execute(
            f"""
            INSERT INTO isbe_filed_docs (id, committee_id, doc_name, received_datetime)
            VALUES ({row['filed_doc_id']}, {row['committee_id']}, 'Quarterly', '{row['expended_date']} 12:00:00')
            """
        )
    columns = (
        "(id, committee_id, filed_doc_id, last_name, first_name, expended_date, amount, purpose, d2_part, "
        "candidate_name, office, supporting, opposing, archived)"
    )
    for row in PART9_ROWS:
        values = ", ".join(
            _sql(row[key])
            for key in ("id", "committee_id", "filed_doc_id", "last_name", "first_name", "expended_date", "amount",
                        "purpose", "d2_part", "candidate_name", "office", "supporting", "opposing", "archived")
        )
        conn.execute(f"INSERT INTO isbe_expenditures {columns} VALUES ({values})")
        if row["condensed"]:
            conn.execute(f"INSERT INTO isbe_condensed_expenditures {columns} VALUES ({values})")
    conn.commit()


def _build_app(tmp_path: Path):
    db_path = str(tmp_path / "outside_spending.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_isbe_tables(conn)
    _seed_candidate_profile_rows(conn)
    _create_compat_views(conn)
    _seed_inbound_rows(conn)
    _seed_part9_rows(conn)
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


@pytest.fixture
def outside_app(tmp_path: Path):
    return _build_app(tmp_path)


# Suffix world (fixture `suffix_app` only).  ISBE keeps a generational suffix inside last_name; the filers write the
# name without it, with it (with or without a comma), or as "Last, First".
#   980 Lee Example, Jr    record = first_name 'Lee' + last_name 'Example, Jr'; one candidacy, 2020 => cycle 2020, so
#                          only rows dated 2019-01-01..2020-12-31 can attach (no other Part-9 row is dated before
#                          2021-11-05).  Name key once the suffix is dropped: (LEE, EXAMPLE).
#   985 John Barberis, Jr  record = first_name 'John' + last_name 'Barberis, Jr'; candidacy 2026; in the feed of race
#                          state-representative-representative-district-45 as 'John Barberis, Jr' (what the
#                          isbe_race_candidacies view builds with TRIM(first_name || ' ' || last_name)), no committee.
#   id     spender date        amount    name as filed      dir  attaches to / why
#   71001  701     2019-03-01  1,000.00  Lee Example        S    980  (odd year 2019 => cycle 2020)
#   71002  701     2019-09-09  2,000.00  Lee Example Jr     S    980  (suffix, no comma)
#   71003  701     2020-03-03  4,000.00  Lee Example, Jr    O    980  (the ", Jr" is a suffix, not a second name)
#   71004  701     2020-08-08  8,000.00  Example, Lee       O    980  ("Last, First": one reversed name)
#   71005  702     2026-04-15  4,000.00  John Barberis      S    985
#   CONTROLS, same last name and another first name, in the cycle of the candidate they must not reach:
#   71006  702     2020-05-05 16,000.00  Mia Example        O    nobody (a last-name-only match would give it to 980)
#   71007  702     2026-05-05 32,000.00  Jane Barberis      O    nobody (... and this one to 985)
# Candidate 980: S = 1,000 + 2,000 = 3,000   O = 4,000 + 8,000 = 12,000   T = 15,000   rows 4.  The amounts are powers
#   of two, so a missed, doubled, halved or extra row changes the sum.
# Race ...-45, cycle 2026: before this row Pat 67,500.75 | Rae 35,000.25 | Nell 1,500.50 (card 104,001.50); now
#   Pat | Rae | John Barberis, Jr (S 4,000.00  O 0.00  T 4,000.00  rows 1) | Nell, card 108,001.50.
SUFFIX_ROWS = [
    _p9(71004, 701, "2020-08-08", 8000.00, "Example, Lee", "O"),
    _p9(71001, 701, "2019-03-01", 1000.00, "Lee Example", "S"),
    _p9(71005, 702, "2026-04-15", 4000.00, "John Barberis", "S"),
    _p9(71003, 701, "2020-03-03", 4000.00, "Lee Example, Jr", "O"),
    _p9(71007, 702, "2026-05-05", 32000.00, "Jane Barberis", "O"),                  # control for 985
    _p9(71002, 701, "2019-09-09", 2000.00, "Lee Example Jr", "S"),
    _p9(71006, 702, "2020-05-05", 16000.00, "Mia Example", "O"),                    # control for 980
]


def _seed_suffix_world(conn) -> None:
    conn.execute(
        """
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party,
                                     city, state, zipcode, residence_county)
        VALUES
            (980, 'Example, Jr', 'Lee', 'State Senator', 'Senate District', '20', 'Independent',
             'Peoria', 'IL', '61602', 'Peoria'),
            (985, 'Barberis, Jr', 'John', 'State Representative', 'Representative District', '45', 'Republican',
             'Springfield', 'IL', '62701', 'Sangamon')
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_candidacies (candidate_id, election_type, election_year, race_type, outcome)
        VALUES
            (980, 'General Election', 2020, 'challenger', 'lost'),
            (985, 'General Election', 2026, 'challenger', NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO isbe_race_candidacies VALUES
            (9105, 985, 2026, 2026, 'General Election', 'challenger', NULL, 'John Barberis, Jr',
             'State Representative', 'Representative District', '45', 'Republican', NULL, NULL)
        """
    )
    keys = ("id", "committee_id", "filed_doc_id", "last_name", "first_name", "expended_date", "amount", "purpose",
            "d2_part", "candidate_name", "office", "supporting", "opposing", "archived")
    for row in SUFFIX_ROWS:
        conn.execute(
            f"""
            INSERT INTO isbe_filed_docs (id, committee_id, doc_name, received_datetime)
            VALUES ({row['filed_doc_id']}, {row['committee_id']}, 'Quarterly', '{row['expended_date']} 12:00:00')
            """
        )
        values = ", ".join(_sql(row[key]) for key in keys)
        for table in ("isbe_expenditures", "isbe_condensed_expenditures"):
            conn.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({values})")
    conn.commit()


@pytest.fixture
def suffix_app(tmp_path: Path):
    app = _build_app(tmp_path)
    conn = get_db(app.config["DATABASE_PATH"])
    _seed_suffix_world(conn)
    conn.close()
    return app


# ---------------------------------------------------------------------------
# Expected values (hand-computed in the module docstring; never derived from PART9_ROWS)
# ---------------------------------------------------------------------------

# (support, oppose, total, rows, [(spender, support, oppose, total, rows), ...] by total desc)
WINDOW_ALL = (46640.50, 21628.25, 68268.75, 9, [
    (703, 32000.50, 4500.25, 36500.75, 2),
    (701, 8640.00, 17000.00, 25640.00, 4),
    (702, 6000.00, 128.00, 6128.00, 3),
])
WINDOW_2026 = (46000.50, 21500.25, 67500.75, 7, [
    (703, 32000.50, 4500.25, 36500.75, 2),
    (701, 8000.00, 17000.00, 25000.00, 3),
    (702, 6000.00, 0.00, 6000.00, 2),
])
WINDOW_2022 = (640.00, 128.00, 768.00, 2, [
    (701, 640.00, 0.00, 640.00, 1),
    (702, 0.00, 128.00, 128.00, 1),
])
WINDOW_MID = (6000.00, 16000.00, 22000.00, 3, [
    (701, 0.00, 16000.00, 16000.00, 1),
    (702, 6000.00, 0.00, 6000.00, 2),
])
WINDOW_FROM = (32000.50, 20500.25, 52500.75, 3, [
    (703, 32000.50, 4500.25, 36500.75, 2),
    (701, 0.00, 16000.00, 16000.00, 1),
])

# Pat Example's nine line items, newest first (see the docstring table for the letters).
ALL_DATES = ["2026-09-09", "2026-07-04", "2026-06-30", "2026-05-15", "2026-03-01",
             "2025-11-03", "2025-01-01", "2022-12-31", "2021-11-05"]

PAT_LINES = {
    # date: what the page must show for that row
    "2026-09-09": dict(spender=703, direction="S", amount=32000.50, share=32000.50, purpose=None,
                       payee=["Lakeshore Strategies"], office=None, name="Pat Example", filing=80006),      # F
    "2026-07-04": dict(spender=703, direction="O", amount=9000.50, share=4500.25, purpose="Mailers",
                       payee=["Mailhouse Direct"], office="State Representative", name=None, filing=80007),  # SP
    "2026-06-30": dict(spender=701, direction="O", amount=16000.00, share=16000.00, purpose="Digital advertising",
                       payee=["TKO Buying LLC"], office="State Representative", name="Pat Example",
                       filing=80005),                                                                          # E
    "2026-05-15": dict(spender=702, direction="S", amount=4000.00, share=4000.00, purpose="Canvassing",
                       payee=["Dana", "Wexler"], office="State Representative 45th District",
                       name="Pat M. Example", filing=80004),                                                   # D
    "2026-03-01": dict(spender=702, direction="S", amount=2000.00, share=2000.00, purpose="Television",
                       payee=["Prairie Media Group"], office="State Representative", name="Pat Example",
                       filing=80003),                                                                          # C
    "2025-11-03": dict(spender=701, direction="O", amount=1000.00, share=1000.00, purpose="Mailers",
                       payee=["Mailhouse Direct"], office="State Rep. 45", name="PAT EXAMPLE", filing=80002),  # B
    "2025-01-01": dict(spender=701, direction="S", amount=8000.00, share=8000.00, purpose="Digital advertising",
                       payee=["TKO Buying LLC"], office="State Representative", name="Pat Example",
                       filing=80001),                                                                          # A
    "2022-12-31": dict(spender=702, direction="O", amount=128.00, share=128.00, purpose="Mailers",
                       payee=["Mailhouse Direct"], office="State Representative", name="Pat Example",
                       filing=80009),                                                                          # G2
    "2021-11-05": dict(spender=701, direction="S", amount=640.00, share=640.00, purpose="Yard signs",
                       payee=["Prairie Print"], office="State Representative", name="Pat Example",
                       filing=80008),                                                                          # G1
}


# ---------------------------------------------------------------------------
# HTML helpers (stdlib only, in the style of tests.test_candidate_profile)
# ---------------------------------------------------------------------------

_AMOUNT = r"(\$[\d,]+(?:\.\d{2})?)"


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def _h2_html(page: str, label: str) -> str | None:
    """Inner HTML of the first <h2> whose text matches the regex ``label``."""
    for match in re.finditer(r"<h2[^>]*>(.*?)</h2>", page, re.S | re.I):
        if re.search(label, match.group(1), re.I):
            return match.group(1)
    return None


def _outside_section(page: str) -> str | None:
    """The candidate profile's 'Outside spending (D-2 Part 9)' block: its <h2> up to the next <h2>; None if absent."""
    start = re.search(r"<h2[^>]*>\s*Outside spending \(D-2 Part 9\)", page, re.I)
    if start is None:
        return None
    following = page.find("<h2", start.end())
    return page[start.start():following if following != -1 else len(page)]


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.anchors.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def _anchors(fragment: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(fragment)
    parser.close()
    return parser.anchors


def _all_items_link(section: str):
    """(N, href) of the 'All N line items' anchor, or None."""
    for href, text in _anchors(section):
        match = re.search(r"All\s+(\d[\d,]*)\s+line\s+items?", text, re.I)
        if match:
            return int(match.group(1).replace(",", "")), href
    return None


def _summary(text: str):
    """(supporting, opposing, total, rows) from 'Supporting: $ | Opposing: $ | Total: $ | N rows' prose, else None."""
    figures = {}
    for label in ("Supporting", "Opposing", "Total"):
        match = re.search(rf"\b{label}\b\s*:?\s*{_AMOUNT}", text)
        if match is None:
            return None
        figures[label] = _money(match.group(1))
    count = re.search(r"(\d[\d,]*)\s+(?:rows?|line\s+items?)\b", text)
    if count is None:
        return None
    return figures["Supporting"], figures["Opposing"], figures["Total"], int(count.group(1).replace(",", ""))


def _head_before_line_items(page: str) -> str:
    """Visible text above the 'Line items' heading of the line-item page (filters, summary line)."""
    heading = re.search(r"<h2[^>]*>\s*Line items", page, re.I)
    assert heading, "no 'Line items' heading on the page"
    return _visible_text(page[:heading.start()])


def _cell_date(cell: str) -> str:
    """ISO date out of a date cell: 2026-03-01 or 'Mar 1, 2026'; never 'non-empty'."""
    found = _ISO.findall(cell)
    if found:
        assert len(found) == 1, cell
        return found[0]
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cell.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    raise AssertionError(f"not a date: {cell!r}")


def _direction(cell: str) -> str:
    """'S' / 'O' out of a For / Against cell (For, Supporting, Support, S | Against, Opposing, Oppose, O)."""
    text = cell.strip().lower()
    if re.search(r"\b(for|support\w*)\b", text) or text == "s":
        return "S"
    if re.search(r"\b(against|oppos\w*)\b", text) or text == "o":
        return "O"
    return text


def _blank(cell: str) -> bool:
    """True for an empty cell or one holding only punctuation (a dash)."""
    return re.search(r"\w", cell) is None


def _data_rows(table):
    """Rows with one cell per header (a 'no rows' placeholder row spans the table and has a single cell)."""
    return [row for row in table.rows if len(row.cells) == len(table.headers)]


def _table_under(page: str, heading: str):
    for table in _tables(page):
        if re.search(heading, table.heading_html, re.I):
            return table
    return None


def _filing_ids(row) -> list[int]:
    ids = []
    for href in row.hrefs:
        match = re.fullmatch(r"/committees/filing/(\d+)", urlsplit(href).path)
        if match:
            ids.append(int(match.group(1)))
    return ids


_SORT_ARROWS = "\u25b2\u25bc\u25b4\u25be\u2191\u2193\u2195\u21c5\u2b06\u2b07^"


def _headers_are(table, expected: list[str]) -> bool:
    """Same header cells, ignoring order, case and sort arrows (exact count: an extra column fails)."""
    shown = sorted("".join(ch for ch in header if ch not in _SORT_ARROWS).strip().lower() for header in table.headers)
    return shown == sorted(name.lower() for name in expected)


def _spender_table(page: str):
    return _table_under(page, r"Outside spending \(D-2 Part 9\)")


def _spender_rows(table) -> list[tuple]:
    """[(committee id, support, oppose, total, rows)] of the profile's by-spender table, in page order."""
    rows = []
    for row in _data_rows(table):
        ids = _committee_ids(row)
        assert len(ids) == 1, ("each spender row needs exactly one /committees/<id> link", row.text, row.hrefs)
        rows.append((
            ids[0],
            _amount(table.cell(row, r"^supporting")),
            _amount(table.cell(row, r"^opposing")),
            _amount(table.cell(row, r"^total")),
            int(re.sub(r"\D", "", table.cell(row, r"^rows"))),
        ))
    return rows


LINE_HEADERS = ["Date", "Spender", "For / Against", "Amount", "Share", "Purpose", "Payee", "Office (as filed)",
                "Name (as filed)", "Filing"]


def _line_table(page: str):
    return _table_under(page, r"Line items")


def _line_items(page: str) -> list[dict]:
    """The line-item page's data rows as dicts (empty when the page has no table or no data rows)."""
    table = _line_table(page)
    if table is None:
        return []
    items = []
    for row in _data_rows(table):
        cells = {
            "date": table.cell(row, r"^date"),
            "spender": table.cell(row, r"^spender"),
            "direction": table.cell(row, r"^for"),
            "amount": table.cell(row, r"^amount"),
            "share": table.cell(row, r"^share"),
            "purpose": table.cell(row, r"^purpose"),
            "payee": table.cell(row, r"^payee"),
            "office": table.cell(row, r"^office"),
            "name": table.cell(row, r"^name"),
            "filing": table.cell(row, r"^filing"),
        }
        items.append({
            "date": _cell_date(cells["date"]),
            "direction": _direction(cells["direction"]),
            "spender_ids": _committee_ids(row),
            "filing_ids": _filing_ids(row),
            "cells": cells,
            "text": row.text,
        })
    return items


def _line_dates(page: str) -> list[str]:
    return [item["date"] for item in _line_items(page)]


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = {"action": attrs.get("action") or "", "method": (attrs.get("method") or "").lower(),
                          "controls": []}
            self.forms.append(self._form)
        elif self._form is not None and tag in ("input", "select", "textarea") and attrs.get("name"):
            self._form["controls"].append({"name": attrs["name"], "type": (attrs.get("type") or "").lower(),
                                           "value": attrs.get("value") or ""})

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def _forms(page: str) -> list[dict]:
    parser = _FormParser()
    parser.feed(page)
    parser.close()
    return parser.forms


def _race_table(page: str):
    table = _table_under(page, r"Outside Spending Rows")
    assert table is not None, "no 'Outside Spending Rows' table on the race page"
    return table


def _race_rows(page: str) -> list[dict]:
    table = _race_table(page)
    rows = []
    for row in _data_rows(table):
        rows.append({
            "name": table.cell(row, r"^candidate"),
            "supporting": _amount(table.cell(row, r"^supporting")),
            "opposing": _amount(table.cell(row, r"^opposing")),
            "total": _amount(table.cell(row, r"^total")),
            "rows": int(re.sub(r"\D", "", table.cell(row, r"^rows"))),
            "hrefs": row.hrefs,
        })
    return rows


def _outside_card(page: str) -> float:
    match = re.search(r"Outside Spending \(E\).*?<div class=\"stat-value\">\s*" + _AMOUNT + r"\s*</div>", page, re.S)
    assert match, "no 'Outside Spending (E)' card on the race page"
    return _money(match.group(1))


# ---------------------------------------------------------------------------
# The shared matcher (pure functions; no database)
# ---------------------------------------------------------------------------

PAT_CAND = {"key": ("PAT", "EXAMPLE"), "cycles": {2022, 2026}, "candidate_id": 900, "name": "Pat Example"}
RAE_CAND = {"key": ("RAE", "RIVAL"), "cycles": {2026}, "candidate_id": 930, "name": "Rae Rival"}
NELL_CAND = {"key": ("NELL", "NULLID"), "cycles": {2026}, "candidate_id": None, "name": "Nell Nullid"}


def _row(row_id, name, amount=1000.0, day=date(2026, 3, 1), *, supporting=True, committee_id=701):
    return {"id": row_id, "committee_id": committee_id, "candidate_name": name, "amount": amount,
            "expended_date": day, "supporting": supporting, "opposing": not supporting}


class TestMatcher:
    """Which wrong implementation still passes?  Killed here: cycle = the calendar year, year - year % 2, or
    always the next even year (part9_cycle); a key that keeps the middle name, keeps punctuation, ignores case,
    hands back a list, keeps a trailing generational suffix as the last name (two Larrys with a suffix share
    (LARRY, JR)), drops it only after a comma, only when written 'Jr', only Jr / Sr, or only one of a run, or
    turns 'Pat Jr' into None (candidate_name_key); a split that cuts 'Anderson' / 'Sandra' at their 'and', ignores 'AND', keeps
    empty parts or leaves the comma on a name, leaves 'Last, First[ Middle]' as two parts, reverses what is not
    one such pair (a two-token left side, a three-token right side, a bare suffix, an 'and' / ; / & list, an
    empty side) or returns the reversed name next to its parts (split_candidate_names); last-name-only or
    substring matching, a first-name-only match, a cycle gate that is a min..max range or treats an empty set
    as 'no gate', a share divided by the names that MATCHED instead of the names ON THE ROW, one attribution
    for a row that names two candidates, mutated inputs, `.first` on same-named candidates
    (attribute_part9_rows); totals built from the row amount instead of the share, spenders counted per row
    (summarize).
    Not pinned (accepted): the order of the returned attributions, a NULL candidate_name / amount (the race
    builder tests pin the NULL name), nicknames, rows with three or more single-token parts, a suffix after
    'Last, First' ('Scherer, Sue, Jr'), a multi-word surname before the comma ('Van Buren, Martin'), and the
    case and inner spacing of a reversed name beyond the contract examples (its key is what the padded variants
    pin)."""

    @pytest.mark.parametrize("day, cycle", [
        (date(2021, 11, 5), 2022),     # an odd year belongs to the next even year
        (date(2022, 1, 1), 2022),      # an even year is its own cycle, first day ...
        (date(2022, 12, 31), 2022),    # ... and last day
        (date(2023, 1, 1), 2024),
        (date(2024, 2, 29), 2024),
        (date(2025, 6, 15), 2026),
        (date(2026, 12, 31), 2026),
        (date(2027, 1, 1), 2028),
    ])
    def test_part9_cycle_rounds_odd_years_up_and_keeps_even_years(self, day, cycle):
        from database.outside_spending import part9_cycle
        assert part9_cycle(day) == cycle

    def test_candidate_name_key_pins_the_contract_examples(self):
        from database.outside_spending import candidate_name_key
        assert candidate_name_key("J.B. Pritzker") == ("JB", "PRITZKER")
        assert candidate_name_key("Michelle N. Pierre") == ("MICHELLE", "PIERRE")
        assert candidate_name_key("Michelle Pierre") == ("MICHELLE", "PIERRE")
        assert candidate_name_key("Rauner") is None
        # The Chicago 1B case: the candidate record reads 'Michelle N' + 'Pierre' (joined by the caller with a
        # space), the filers wrote 'Michelle Pierre'; both must meet on one key.
        assert candidate_name_key("Michelle N Pierre") == candidate_name_key("Michelle Pierre")

    @pytest.mark.parametrize("text, key", [
        ("  Pat    Example  ", ("PAT", "EXAMPLE")),            # extra spaces collapse
        ("pat example", ("PAT", "EXAMPLE")),                    # case folds
        ("Pat Example,", ("PAT", "EXAMPLE")),                   # trailing comma
        ("Pat Example.", ("PAT", "EXAMPLE")),                   # trailing period
        ("Pat\tExample", ("PAT", "EXAMPLE")),                   # any whitespace run is one separator
        ('Pat "Buzz" Example', ("PAT", "EXAMPLE")),             # quotes stripped; a nickname is a middle token
        ("Pat Marie Louise Example", ("PAT", "EXAMPLE")),       # two or more middle tokens
        ("Pat M. Example", ("PAT", "EXAMPLE")),
        ("Pat O'Brien", ("PAT", "OBRIEN")),                     # apostrophe stripped
        ("Dee M O'Brien", ("DEE", "OBRIEN")),                   # a candidate record: middle initial, apostrophe
        ("Pat Smith-Jones", ("PAT", "JONES")),                  # M1 (search Phase 2): a hyphen is a space; . , ' " are removed
    ])
    def test_candidate_name_key_normalises_spacing_case_and_punctuation(self, text, key):
        from database.outside_spending import candidate_name_key
        assert candidate_name_key(text) == key

    @pytest.mark.parametrize("text", ["Rauner", "Rauner.", "", "   ", ".", "'' ,", "J.B."])
    def test_candidate_name_key_is_none_below_two_tokens(self, text):
        from database.outside_spending import candidate_name_key
        assert candidate_name_key(text) is None

    @pytest.mark.parametrize("text, key", [
        # ISBE keeps a generational suffix inside last_name ("Barberis, Jr"); the filers write the name without it.
        # Every spelling of the candidate must meet the filed "John Barberis" on one key.
        ("John Barberis, Jr", ("JOHN", "BARBERIS")),
        ("John Barberis Jr", ("JOHN", "BARBERIS")),
        ("John Barberis", ("JOHN", "BARBERIS")),            # (pin) the filed spelling, the key all the others must reach
        ("Curtis Tarver, II", ("CURTIS", "TARVER")),
        ("Larry Walsh Jr", ("LARRY", "WALSH")),             # two Larrys with a suffix used to share (LARRY, JR) ...
        ("Larry R Rogers Jr", ("LARRY", "ROGERS")),         # ... and the middle initial stays out of the key
        # Every suffix of the contract, any case, with or without the period and the comma.
        ("John Barberis, Jr.", ("JOHN", "BARBERIS")),
        ("john barberis jr", ("JOHN", "BARBERIS")),
        ("John Barberis, Sr", ("JOHN", "BARBERIS")),
        ("John Barberis III", ("JOHN", "BARBERIS")),
        ("John Barberis, IV", ("JOHN", "BARBERIS")),
        ("John Barberis V", ("JOHN", "BARBERIS")),
        ("John Barberis Jr III", ("JOHN", "BARBERIS")),     # a run of suffixes goes one token at a time ("while")
    ])
    def test_candidate_name_key_drops_a_trailing_generational_suffix(self, text, key):
        from database.outside_spending import candidate_name_key
        assert candidate_name_key(text) == key, "a trailing JR / SR / II / III / IV / V is not the last name"

    @pytest.mark.parametrize("text, key", [
        ("Pat Jr", ("PAT", "JR")),
        ("Pat Jr.", ("PAT", "JR")),
        ("Pat II", ("PAT", "II")),
    ])
    def test_candidate_name_key_keeps_the_last_token_of_a_two_token_name_even_when_it_is_a_suffix(self, text, key):
        # (pin) passes today: a suffix is dropped only while MORE than two tokens remain, so a fix that drops it
        # unconditionally (and turns "Pat Jr" into None) fails here.
        from database.outside_spending import candidate_name_key
        assert candidate_name_key(text) == key

    @pytest.mark.parametrize("text", [
        "Pat Example, Rae Rival",
        "Pat Example; Rae Rival",
        "Pat Example / Rae Rival",
        "Pat Example & Rae Rival",
        "Pat Example and Rae Rival",
        "Pat Example AND Rae Rival",
        "Pat Example And Rae Rival",
        "Pat Example,Rae Rival",
        "Pat Example/Rae Rival",
        "Pat Example&Rae Rival",
        "Pat Example, and Rae Rival",
    ])
    def test_split_candidate_names_splits_on_every_separator(self, text):
        from database.outside_spending import split_candidate_names
        assert split_candidate_names(text) == ["Pat Example", "Rae Rival"]

    @pytest.mark.parametrize("text", ["Sandra Anderson", "Andy Anders", "Brandon Handy", "Alexander Bland",
                                      "Pat Randall"])
    def test_split_candidate_names_keeps_and_inside_words_whole(self, text):
        from database.outside_spending import split_candidate_names
        assert split_candidate_names(text) == [text]

    def test_split_candidate_names_splits_only_the_word_and(self):
        from database.outside_spending import split_candidate_names
        assert split_candidate_names("Sandra Anderson and Brandon Handy") == ["Sandra Anderson", "Brandon Handy"]
        assert split_candidate_names("Pat Example, Rae Rival and Nell Nullid") == [
            "Pat Example", "Rae Rival", "Nell Nullid"]
        assert split_candidate_names("A B, C D; E F / G H & I J and K L") == [
            "A B", "C D", "E F", "G H", "I J", "K L"]

    def test_split_candidate_names_trims_and_drops_empty_parts(self):
        from database.outside_spending import split_candidate_names
        assert split_candidate_names("  Pat Example  ,   Rae Rival  ") == ["Pat Example", "Rae Rival"]
        assert split_candidate_names("Pat Example,, Rae Rival; ") == ["Pat Example", "Rae Rival"]
        assert split_candidate_names("Pat Example, Rae Rival, and Nell Nullid") == [
            "Pat Example", "Rae Rival", "Nell Nullid"]

    def test_split_candidate_names_returns_one_name_for_a_single_name(self):
        from database.outside_spending import candidate_name_key, split_candidate_names
        assert split_candidate_names("Pat Example") == ["Pat Example"]
        trailing = split_candidate_names("Pat Example,")
        assert len(trailing) == 1                                  # whether the comma stays on the text is not pinned ...
        assert candidate_name_key(trailing[0]) == ("PAT", "EXAMPLE")   # ... the name it carries is

    @pytest.mark.parametrize("text", ["Pat Example, Jr", "Pat Example,Jr.", "Pat Example, III"])
    def test_a_bare_suffix_after_a_comma_is_not_a_second_name(self, text):
        # Real filings write "Carlos Rivas, Jr": the comma split must not turn the suffix into a second
        # name, or the one candidate named gets half the amount and the line item reads "1 of 2".
        from database.outside_spending import attribute_part9_rows, split_candidate_names
        assert split_candidate_names(text) == ["Pat Example"]
        attributions = attribute_part9_rows([_row(1, text, amount=1000.0)], [PAT_CAND])
        assert len(attributions) == 1
        assert attributions[0]["share"] == 1000.0
        assert attributions[0]["named_count"] == 1

    @pytest.mark.parametrize("text, names, key", [
        ("Scherer, Sue", ["Sue Scherer"], ("SUE", "SCHERER")),
        ("Hanson, Matt R", ["Matt R Hanson"], ("MATT", "HANSON")),          # the middle initial stays out of the key
        ("Anderson, Sandra", ["Sandra Anderson"], ("SANDRA", "ANDERSON")),  # 'and' inside words is not the word "and"
    ])
    def test_a_last_first_filing_is_one_name_written_first_name_first(self, text, names, key):
        # One comma, a one-token left side and a one- or two-token right side: ONE person filed "Last, First[ M]".
        # Today the comma splits it into two single tokens ("Scherer", "Sue") that match nobody.
        from database.outside_spending import candidate_name_key, split_candidate_names
        result = split_candidate_names(text)
        assert result == names, "a single-comma 'Last, First[ Middle]' filing names one person: 'First[ Middle] Last'"
        assert candidate_name_key(result[0]) == key

    @pytest.mark.parametrize("text, key", [
        ("Scherer,Sue", ("SUE", "SCHERER")),                    # no space after the comma
        ("  SCHERER ,  SUE  ", ("SUE", "SCHERER")),             # padding and capitals
        ("Hanson, Matt R.", ("MATT", "HANSON")),                # an initial with a period
    ])
    def test_a_last_first_filing_keys_the_same_whatever_its_spacing_case_or_initial_period(self, text, key):
        from database.outside_spending import candidate_name_key, split_candidate_names
        names = split_candidate_names(text)
        assert [candidate_name_key(name) for name in names] == [key], names

    def test_a_last_first_filing_attaches_as_one_name_with_the_whole_amount(self):
        from database.outside_spending import attribute_part9_rows
        sue = dict(PAT_CAND, key=("SUE", "SCHERER"), candidate_id=950, name="Sue Scherer")
        attributions = attribute_part9_rows([_row(1, "Scherer, Sue", amount=1000.0)], [sue])
        assert len(attributions) == 1, "'Scherer, Sue' must reach the candidate keyed (SUE, SCHERER)"
        assert attributions[0]["share"] == 1000.0       # the row names one person, not "Scherer" and "Sue" (or three)
        assert attributions[0]["named_count"] == 1

    @pytest.mark.parametrize("text, names", [
        ("Example, Jr", ["Example"]),                           # a bare suffix after a one-token left side is no first name
        ("Example,Jr.", ["Example"]),                           # ... with a period and no space
        ("Example, iii", ["Example"]),                          # ... in any case
        ("Pat Example, Rae Rival", ["Pat Example", "Rae Rival"]),   # two people: the left side has two tokens
        ("Pat Example, Rae", ["Pat Example", "Rae"]),               # ... also with a one-token right side
        ("Hanson, Matt R Q", ["Hanson", "Matt R Q"]),           # a three-token right side is not "First Middle"
        ("Smith, John and Jones, Mary", ["Smith", "John", "Jones", "Mary"]),    # two commas
        # One comma, a one-token left side and a right side of at most two tokens, yet a LIST: the separator, not the
        # size of the sides, is what keeps these from being reversed.
        ("Smith, and Jones", ["Smith", "Jones"]),               # the word "and" ...
        ("Smith, AND Jones", ["Smith", "Jones"]),               # ... in any case
        ("Smith, John; Mary", ["Smith", "John", "Mary"]),       # a semicolon
        ("Smith, John;Mary", ["Smith", "John", "Mary"]),        # ... without the space
        ("Smith, John/Mary", ["Smith", "John", "Mary"]),        # a slash
        ("Smith, John&Mary", ["Smith", "John", "Mary"]),        # an ampersand
        ("Example,", ["Example"]),                              # an empty right side
        (", Sue", ["Sue"]),                                     # an empty left side
    ])
    def test_everything_but_a_single_last_first_pair_splits_as_before(self, text, names):
        # (pin) passes today: it stops a reversal that is too eager, or one that crashes on an empty side.
        from database.outside_spending import split_candidate_names
        assert split_candidate_names(text) == names

    def test_a_name_repeated_on_one_row_is_one_name(self):
        from database.outside_spending import attribute_part9_rows, split_candidate_names
        assert split_candidate_names("Pat Example and Pat Example") == ["Pat Example"]
        attributions = attribute_part9_rows([_row(1, "Pat Example and Pat Example", amount=1000.0)], [PAT_CAND])
        assert len(attributions) == 1
        assert attributions[0]["share"] == 1000.0
        assert attributions[0]["named_count"] == 1

    def test_filing_spellings_attach_and_near_misses_do_not(self):
        from database.outside_spending import attribute_part9_rows
        attached = ["Pat Example", "Pat M. Example", "PAT EXAMPLE", "pat  example.", "Pat Marie Louise Example"]
        missed = ["Pat Examples", "Patience Example", "Pat Exemplar", "Pat Rival", "Example", "Pat", "", "Rae Rival"]
        rows = [_row(number, name) for number, name in enumerate(attached + missed, start=1)]
        attributions = attribute_part9_rows(rows, [PAT_CAND])
        assert sorted(a["id"] for a in attributions) == [1, 2, 3, 4, 5]
        for attribution in attributions:
            assert attribution["candidate"] == PAT_CAND, attribution["id"]
            assert attribution["share"] == 1000.0, attribution["id"]
            assert attribution["named_count"] == 1, attribution["id"]
        filed = {a["id"]: a["name_as_filed"] for a in attributions}
        assert filed[1] == "Pat Example"
        assert filed[2] == "Pat M. Example"
        assert filed[3] == "PAT EXAMPLE"

    @pytest.mark.parametrize("day, cycles, count", [
        (date(2021, 12, 31), {2022}, 1),            # an odd year counts toward the next even cycle
        (date(2022, 12, 31), {2022}, 1),
        (date(2023, 1, 1), {2022}, 0),              # 2023 is cycle 2024
        (date(2023, 5, 5), {2024}, 1),              # an odd-year row, an even-year candidacy cycle
        (date(2024, 6, 1), {2022, 2026}, 0),        # BETWEEN two candidacy cycles: membership, not a min..max range
        (date(2025, 1, 1), {2026}, 1),
        (date(2026, 12, 31), {2026}, 1),
        (date(2027, 1, 1), {2026}, 0),
        (date(2024, 6, 1), None, 1),                # None = no gate
        (date(2024, 6, 1), set(), 0),               # an empty set is a gate nothing passes (a candidate with no candidacies)
    ])
    def test_cycle_gate_is_membership_of_the_rows_cycle_in_the_candidates_cycles(self, day, cycles, count):
        from database.outside_spending import attribute_part9_rows
        candidate = dict(PAT_CAND, cycles=cycles)
        attributions = attribute_part9_rows([_row(1, "Pat Example", day=day)], [candidate])
        assert len(attributions) == count

    def test_multi_name_row_gives_each_named_candidate_amount_over_the_names_on_the_row(self):
        from database.outside_spending import attribute_part9_rows
        row = _row(1, "Rae Rival, Pat Example", amount=9000.50, supporting=False)
        attributions = attribute_part9_rows([row], [PAT_CAND, RAE_CAND])
        assert len(attributions) == 2
        by_candidate = {a["candidate"]["candidate_id"]: a for a in attributions}
        assert sorted(by_candidate) == [900, 930]
        assert by_candidate[900]["share"] == 4500.25
        assert by_candidate[930]["share"] == 4500.25
        assert by_candidate[900]["named_count"] == 2
        assert by_candidate[930]["named_count"] == 2
        assert by_candidate[900]["name_as_filed"] == "Pat Example"
        assert by_candidate[930]["name_as_filed"] == "Rae Rival"
        assert by_candidate[900]["amount"] == 9000.50           # the row's own amount is passed through unsplit
        assert by_candidate[900] is not by_candidate[930]       # one dict per (row, candidate), not one shared dict

    def test_share_divides_by_the_names_on_the_row_not_by_the_names_that_matched(self):
        from database.outside_spending import attribute_part9_rows
        attributions = attribute_part9_rows([_row(1, "Rae Rival, Pat Example", amount=1000.0)], [PAT_CAND])
        assert len(attributions) == 1
        assert attributions[0]["share"] == 500.0
        assert attributions[0]["named_count"] == 2

    def test_three_names_with_and_split_three_ways(self):
        from database.outside_spending import attribute_part9_rows
        row = _row(1, "Pat Example, Rae Rival and Nell Nullid", amount=3000.0)
        attributions = attribute_part9_rows([row], [PAT_CAND, RAE_CAND, NELL_CAND])
        assert len(attributions) == 3
        for attribution in attributions:
            assert attribution["share"] == 1000.0
            assert attribution["named_count"] == 3

    def test_same_name_candidates_are_told_apart_by_cycle_and_both_attach_within_a_shared_cycle(self):
        from database.outside_spending import attribute_part9_rows
        namesake = dict(PAT_CAND, candidate_id=940, cycles={2024})
        rows = [_row(1, "Pat Example", day=date(2024, 3, 15)), _row(2, "Pat Example", day=date(2026, 3, 1))]
        attributions = attribute_part9_rows(rows, [PAT_CAND, namesake])
        pairs = sorted((a["id"], a["candidate"]["candidate_id"]) for a in attributions)
        assert pairs == [(1, 940), (2, 900)]
        # Two same-named candidates alive in one cycle: the row counts for both (plan D4), not for the first.
        twin = dict(PAT_CAND, candidate_id=941, cycles={2026})
        shared = attribute_part9_rows([_row(3, "Pat Example", day=date(2026, 3, 1))], [PAT_CAND, twin])
        assert sorted(a["candidate"]["candidate_id"] for a in shared) == [900, 941]
        assert [a["share"] for a in shared] == [1000.0, 1000.0]

    def test_attribution_is_pure_and_passes_the_row_keys_through(self):
        from database.outside_spending import attribute_part9_rows
        row = dict(_row(7, "Pat Example", amount=250.0), purpose="Mailers", filed_doc_id=80007)
        rows = [row]
        candidates = [PAT_CAND]
        rows_before = copy.deepcopy(rows)
        candidates_before = copy.deepcopy(candidates)
        attributions = attribute_part9_rows(rows, candidates)
        assert rows == rows_before                    # no 'share' / 'candidate' keys written into the input rows
        assert candidates == candidates_before
        assert len(attributions) == 1
        for key, value in row.items():
            assert attributions[0][key] == value, key
        assert attributions[0]["candidate"] == PAT_CAND

    def test_attribution_of_nothing_is_nothing(self):
        from database.outside_spending import attribute_part9_rows
        assert attribute_part9_rows([], [PAT_CAND]) == []
        assert attribute_part9_rows([_row(1, "Pat Example")], []) == []

    def test_summarize_sums_shares_by_direction_and_counts_rows_and_distinct_spenders(self):
        from database.outside_spending import summarize
        attributions = [
            {"share": 1000.0, "amount": 2000.0, "supporting": True, "opposing": False, "committee_id": 701},
            {"share": 2500.5, "amount": 5001.0, "supporting": False, "opposing": True, "committee_id": 702},
            {"share": 4000.25, "amount": 4000.25, "supporting": True, "opposing": False, "committee_id": 701},
            {"share": 500.0, "amount": 500.0, "supporting": False, "opposing": True, "committee_id": 703},
        ]
        summary = summarize(attributions)
        assert summary["support_total"] == 5000.25      # 1,000 + 4,000.25 (the shares, not the 2,000 / 4,000.25 amounts)
        assert summary["oppose_total"] == 3000.5        # 2,500.5 + 500
        assert summary["total"] == 8000.75
        assert summary["row_count"] == 4
        assert summary["spender_count"] == 3            # 701 twice, 702, 703

    def test_summarize_of_nothing_is_all_zero(self):
        from database.outside_spending import summarize
        summary = summarize([])
        assert summary["total"] == 0
        assert summary["support_total"] == 0
        assert summary["oppose_total"] == 0
        assert summary["row_count"] == 0
        assert summary["spender_count"] == 0


def _attributes(record: str, filing: str) -> bool:
    """Whether a Part-9 row filed as ``filing`` attaches to the candidate whose record reads ``record`` (no cycle gate)."""
    from database.outside_spending import attribute_part9_rows, candidate_name_key
    candidate = {"key": candidate_name_key(record), "cycles": None, "candidate_id": 1, "name": record}
    return len(attribute_part9_rows([_row(1, filing)], [candidate])) == 1


class TestMatcherHyphensAndNicknames:
    """Search program Phase 2, decisions M1 + M2 (P2-3) with the direct-alias rule P2-17: a hyphen is a space (key =
    first + last token), and two first tokens match only when they are equal or one is a DIRECT alias of the other in the
    ONE nickname map of database/name_normalize.py (MIKE / MICHAEL, PAT / PATRICIA, TED / EDWARD) -- never through a
    shared third alias, so PATRICK and PATRICIA (both PAT) do not meet, nor TED and ED (both EDWARD), nor THEODORE and ED.
    A two-word alias ("JAY ROBERT" for JB) is read by its first word, the way the name key itself reads a name, so JB
    and JAY are direct aliases both ways. Periods and apostrophes are still removed, not split: "J.B."
    stays JB and "O'Brien" stays OBRIEN (TestMatcher pins both), so the key does not go through normalize_name as a whole.
    Kills: a hyphen removed ("SMITHJONES") or kept; a matcher-local nickname dict (the every-entry test binds to NICKNAMES);
    nicknames applied to one side only; aliases that chain through a shared alias; an alias applied to the last name."""

    @pytest.mark.parametrize("text, key", [
        ("Rae Smith-Jones", ("RAE", "JONES")),
        ("Rae-Lynn Smith-Jones", ("RAE", "JONES")),
    ])
    def test_a_hyphen_splits_a_name_into_tokens(self, text, key):
        from database.outside_spending import candidate_name_key
        assert candidate_name_key(text) == key

    def test_a_hyphenated_filing_and_a_spaced_record_attach_both_ways(self):
        assert _attributes("Rae Smith Jones", "Rae Smith-Jones")
        assert _attributes("Rae Smith-Jones", "Rae Smith Jones")
        assert _attributes("Rae Smith-Jones", "RAE SMITH - JONES")

    def test_a_nickname_filing_attaches_to_the_full_name_record_and_back(self):
        assert _attributes("Michael Farrow", "Mike Farrow")
        assert _attributes("Mike Farrow", "Michael Farrow")

    def test_ted_and_ed_each_meet_edward_but_not_each_other(self):
        # P2-17 (b): TED -> EDWARD and ED -> EDWARD are direct aliases; TED and ED only share EDWARD.
        for record, filing in [("Edward Example", "Ted Example"), ("Ted Example", "Edward Example"),
                               ("Edward Example", "Ed Example"), ("Ed Example", "Edward Example")]:
            assert _attributes(record, filing), (record, filing)
        assert not _attributes("Ed Example", "Ted Example")
        assert not _attributes("Ted Example", "Ed Example")

    def test_an_alias_never_reaches_through_a_shared_alias_or_the_last_name(self):
        assert not _attributes("Theodore Example", "Ed Example")
        assert not _attributes("Ed Example", "Theodore Example")
        # P2-17 (a): PATRICK and PATRICIA are both aliases of PAT, not of each other (no cycle gate here, so any cycle).
        assert not _attributes("Patricia Example", "Patrick Example")
        assert not _attributes("Patrick Example", "Patricia Example")
        assert not _attributes("Michael Farrow", "Brad Farrow")
        assert not _attributes("Michael Farrow", "Mike Farrowby")

    def test_a_nickname_filing_attaches_to_every_record_it_is_a_direct_alias_of(self):
        # P2-17 (c): "Pat" is a direct alias of both PATRICK and PATRICIA, so both records take the row, each with the
        # row's one-name share (the shared-key rule, D4).
        from database.outside_spending import attribute_part9_rows, candidate_name_key
        records = [
            {"key": candidate_name_key(name), "cycles": {2026}, "candidate_id": number, "name": name}
            for number, name in [(1, "Patrick Example"), (2, "Patricia Example")]
        ]
        attributions = attribute_part9_rows([_row(1, "Pat Example", amount=1000.0)], records)
        assert sorted(item["candidate"]["candidate_id"] for item in attributions) == [1, 2]
        assert [(item["share"], item["named_count"]) for item in attributions] == [(1000.0, 1), (1000.0, 1)]

    def test_the_suffix_rule_still_holds_with_a_nickname(self):
        assert _attributes("Michael Farrow, Jr", "Mike Farrow")
        assert _attributes("Mike Farrow Jr", "Michael Farrow")

    def test_a_last_first_filing_with_a_nickname_still_reads_first_name_first(self):
        assert _attributes("Michael Farrow", "Farrow, Mike")
        assert _attributes("Michael Farrow", "Farrow, Mike W")

    def test_jb_meets_jay_robert_and_its_dotted_spelling(self):
        assert _attributes("Jay Robert Farrow", "J.B. Farrow")
        assert _attributes("JB Farrow", "Jay Robert Farrow")
        assert _attributes("J.B. Farrow", "JB Farrow")

    def test_every_single_word_pair_of_the_shared_nickname_map_attaches_both_ways(self):
        from database.name_normalize import NICKNAMES
        pairs = [
            (name, alias)
            for name, aliases in NICKNAMES.items()
            for alias in aliases
            if " " not in name and " " not in alias
        ]
        assert pairs, "NICKNAMES has no single-word pair"
        missing = [pair for pair in pairs if not _attributes(f"{pair[0]} Farrow", f"{pair[1]} Farrow")]
        assert missing == [], f"{len(missing)} of {len(pairs)} NICKNAMES pairs do not attach: {missing[:8]}"


# ---------------------------------------------------------------------------
# get_candidate_outside_spending: the data function behind both candidate pages
# ---------------------------------------------------------------------------

def _scope(**args):
    """A TimeScope the way the line-item route resolves it (default all time, transaction axis), without a request."""
    from webapp.utils.time_scope import resolve_time_scope
    return resolve_time_scope(default="all", axis="transaction", args=args, today=date(2026, 10, 26))


@pytest.fixture
def outside_conn(outside_app):
    conn = get_db(outside_app.config["DATABASE_PATH"])
    yield conn
    conn.close()


LINE_ITEM_KEYS = {"expenditure_id", "expended_date", "committee_id", "committee_name", "amount", "share",
                  "named_count", "supporting", "opposing", "purpose", "payee", "office_as_filed",
                  "candidate_name_as_filed", "filed_doc_id"}


class TestCandidateDataFunction:
    """Which wrong implementation still passes?  Killed here: a window that also narrows `all_time`; summaries that
    count the traps (archived, Part 1A, the raw-only superseded row, near-miss names, the namesake's 2024 row);
    by-spender rows in id / name / insertion order or cut to the first spender; line items in the wrong order or
    carrying the amount where the share belongs.  Not pinned (accepted): extra keys, the type of expended_date,
    how a NULL purpose / office is represented (None or ''), and whether `candidate_name_as_filed` of a split row
    is the whole filed string or only the matched part."""

    def test_returns_all_time_and_window_summaries_by_spender_and_line_items(self, outside_conn):
        from database.outside_spending import get_candidate_outside_spending
        result = get_candidate_outside_spending(outside_conn, 900, scope=_scope(period="cycle:2026"))
        assert result is not None
        for key in ("all_time", "window", "by_spender", "rows"):
            assert key in result, key

        all_time = result["all_time"]       # the window does not narrow it
        assert all_time["support_total"] == pytest.approx(46640.50)
        assert all_time["oppose_total"] == pytest.approx(21628.25)
        assert all_time["total"] == pytest.approx(68268.75)
        assert all_time["row_count"] == 9
        assert all_time["spender_count"] == 3

        window = result["window"]           # cycle 2026: drops G1 and G2
        assert window["support_total"] == pytest.approx(46000.50)
        assert window["oppose_total"] == pytest.approx(21500.25)
        assert window["total"] == pytest.approx(67500.75)
        assert window["row_count"] == 7
        assert window["spender_count"] == 3

        by_spender = result["by_spender"]
        assert [item["committee_id"] for item in by_spender] == [703, 701, 702]     # total desc
        for item, expected in zip(by_spender, WINDOW_2026[4]):
            committee_id, support, oppose, total, rows = expected
            assert item["committee_name"] == SPENDERS[committee_id], committee_id
            assert item["support_total"] == pytest.approx(support), committee_id
            assert item["oppose_total"] == pytest.approx(oppose), committee_id
            assert item["total"] == pytest.approx(total), committee_id
            assert item["row_count"] == rows, committee_id

        rows = result["rows"]
        assert [row["expenditure_id"] for row in rows] == [70006, 70007, 70005, 70004, 70003, 70002, 70001]
        for row in rows:
            assert LINE_ITEM_KEYS <= set(row), (row["expenditure_id"], LINE_ITEM_KEYS - set(row))

    def test_line_item_values_for_a_plain_row_a_split_row_and_a_person_payee(self, outside_conn):
        from database.outside_spending import get_candidate_outside_spending
        result = get_candidate_outside_spending(outside_conn, 900, scope=_scope(period="cycle:2026"))
        rows = {row["expenditure_id"]: row for row in result["rows"]}

        plain = rows[70006]                                    # F: purpose NULL, office NULL, business payee
        assert str(plain["expended_date"])[:10] == "2026-09-09"
        assert plain["committee_id"] == 703
        assert plain["committee_name"] == "Gamma Issues PAC"
        assert plain["amount"] == pytest.approx(32000.50)
        assert plain["share"] == pytest.approx(32000.50)
        assert plain["named_count"] == 1
        assert plain["supporting"]
        assert not plain["opposing"]
        assert plain["purpose"] in (None, "")
        assert plain["office_as_filed"] in (None, "")
        assert "Lakeshore Strategies" in (plain["payee"] or "")
        assert plain["candidate_name_as_filed"] == "Pat Example"
        assert plain["filed_doc_id"] == 80006

        split = rows[70007]                                    # SP: 'Rae Rival, Pat Example', amount 9,000.50
        assert split["amount"] == pytest.approx(9000.50)
        assert split["share"] == pytest.approx(4500.25)
        assert split["named_count"] == 2
        assert not split["supporting"]
        assert split["opposing"]
        assert "Pat Example" in split["candidate_name_as_filed"]
        assert split["filed_doc_id"] == 80007

        person = rows[70004]                                   # D: payee Dana Wexler, filed as 'Pat M. Example'
        assert "Dana" in person["payee"]
        assert "Wexler" in person["payee"]
        assert person["candidate_name_as_filed"] == "Pat M. Example"
        assert person["office_as_filed"] == "State Representative 45th District"

    def test_all_time_scope_makes_window_equal_all_time_and_lists_every_row(self, outside_conn):
        from database.outside_spending import get_candidate_outside_spending
        result = get_candidate_outside_spending(outside_conn, 900, scope=_scope())
        assert result["window"] == result["all_time"]
        assert len(result["rows"]) == 9
        assert [row["expenditure_id"] for row in result["rows"]][-2:] == [70009, 70008]    # G2 2022, G1 2021: oldest last

    @pytest.mark.parametrize("candidate_id, rows, spenders", [
        pytest.param(930, 4, 3, id="rae-rival"),
        pytest.param(940, 1, 1, id="namesake-pat-example"),
        pytest.param(970, 2, 2, id="middle-initial-and-apostrophe"),
    ])
    def test_counts_are_the_candidates_own_not_the_tables(self, outside_conn, candidate_id, rows, spenders):
        # The database holds three spenders and many more Part-9 rows than any of these candidates has: a count taken
        # over the table (or over a bare name match) cannot give these figures.
        from database.outside_spending import get_candidate_outside_spending
        result = get_candidate_outside_spending(outside_conn, candidate_id, scope=_scope())
        assert result is not None
        assert result["all_time"]["row_count"] == rows
        assert result["all_time"]["spender_count"] == spenders
        assert len(result["by_spender"]) == spenders
        assert len(result["rows"]) == rows

    def test_returns_none_when_nothing_attaches_all_time(self, outside_conn):
        from database.outside_spending import get_candidate_outside_spending
        # Positive controls first: a function that returns None for everyone must not pass.
        assert get_candidate_outside_spending(outside_conn, 900, scope=_scope()) is not None
        namesake = get_candidate_outside_spending(outside_conn, 940, scope=_scope())
        assert namesake is not None
        assert namesake["all_time"]["total"] == pytest.approx(70000.00)      # T4 only, none of 900's rows
        assert namesake["all_time"]["row_count"] == 1
        # 910 has no Part-9 row at all; 920 is named by T9 but has no candidacy, so no cycle can ever match.
        assert get_candidate_outside_spending(outside_conn, 910, scope=_scope()) is None
        assert get_candidate_outside_spending(outside_conn, 920, scope=_scope()) is None


# ---------------------------------------------------------------------------
# Candidate profile section
# ---------------------------------------------------------------------------

PROFILE_WINDOWS = [
    pytest.param("", ("all", "", ""), "All time", WINDOW_ALL, id="all-time"),
    pytest.param("?period=cycle:2026", ("cycle:2026", "", ""), "2026 Cycle", WINDOW_2026, id="cycle-2026"),
    pytest.param("?period=cycle:2022", ("cycle:2022", "", ""), "2022 Cycle", WINDOW_2022, id="cycle-2022"),
    pytest.param("?date_from=2026-03-01&date_to=2026-06-30", ("custom", "2026-03-01", "2026-06-30"),
                 "Custom range", WINDOW_MID, id="custom-both-bounds"),
    pytest.param("?date_from=2026-06-30", ("custom", "2026-06-30", ""), "Custom range", WINDOW_FROM,
                 id="custom-lower-bound-only"),
    pytest.param("?date_to=2022-12-31", ("custom", "", "2022-12-31"), "Custom range", WINDOW_2022,
                 id="custom-upper-bound-only"),
]


class TestProfileSection:
    """Which wrong implementation still passes?  Killed here: an exact full-string match (misses 'Pat M. Example' and
    'PAT EXAMPLE'); counting the archived row, the Part 1A row, the raw-only superseded row, 'Pat Examples',
    'Patience Example' or the namesake's row; a cycle gate that is a min..max range (T4 lies between 900's cycles),
    a missing gate (920 and 940) or the raw year instead of year + year % 2 (940's 2023 candidacy); the full
    amount of a multi-name row instead of the share; support and oppose collapsed into one figure; a by-spender
    table cut to one spender or ordered by id / name; figures that ignore the window, or whose bounds are
    exclusive or need both ends; an empty window that still prints the all-time figures; a section rendered for
    a candidate with nothing attached; a link that drops the window; a candidate whose last name carries a
    suffix ('Example, Jr') and so misses every filing written without it, or one fix side only (filings with the
    suffix miss), or a 'Last, First' filing that attaches to nobody or is split in two (halved amount).
    Not pinned (accepted): the help text on the matching rule beyond its P2-18 phrases (pinned by
    test_the_matching_note_names_hyphens_and_nicknames_on_every_page), the position of the
    block on the page and the spender links' query string.
    Pinned on a reading of the contract (flagged in the report): 'All N line items' has N = the rows of the
    WINDOW, because the link carries the window and an all-time N would misstate what it opens."""

    @pytest.mark.parametrize("query, bar, tag, expected", PROFILE_WINDOWS)
    def test_section_totals_follow_the_window(self, outside_app, query, bar, tag, expected):
        support, oppose, total, row_count, spenders = expected
        page = _get(outside_app, f"/candidates/900{query}")
        assert _bar_scope(page) == bar
        section = _outside_section(page)
        assert section is not None, "no 'Outside spending (D-2 Part 9)' section on the profile"
        heading = _h2_html(page, r"Outside spending \(D-2 Part 9\)")
        assert f'<span class="scope-tag">{tag}</span>' in heading, heading
        text = _visible_text(section)
        assert _summary(text) == (support, oppose, total, row_count), text
        assert "No outside spending in this window." not in text

        table = _spender_table(page)
        assert table is not None, "no by-spender table"
        assert _headers_are(table, ["Spender", "Supporting", "Opposing", "Total", "Rows"]), table.headers
        assert _spender_rows(table) == spenders

        link = _all_items_link(section)
        assert link is not None, "no 'All N line items' link"
        count, href = link
        assert count == row_count
        assert urlsplit(href).path == "/candidates/900/outside-spending"
        followed = _get(outside_app, href)
        assert _bar_scope(followed) == bar              # the link carries the window it was clicked in
        assert len(_line_items(followed)) == row_count

    def test_all_time_section_names_each_spender_with_one_link_to_its_committee(self, outside_app):
        table = _spender_table(_get(outside_app, "/candidates/900"))
        assert table is not None
        rows = _data_rows(table)
        assert len(rows) == 3
        for row, committee_id in zip(rows, [703, 701, 702]):
            assert _committee_ids(row) == [committee_id], row.hrefs
            assert SPENDERS[committee_id] in table.cell(row, r"^spender")

    def test_window_without_rows_says_so_and_still_links_to_the_line_items(self, outside_app):
        # Window 2023-2024 holds one Part-9 row naming 'Pat Example' (T4, 2024-03-15). It is the namesake's, so
        # nothing attaches to candidate 900 there, yet 900 has rows all time: the section stays.
        page = _get(outside_app, "/candidates/900?period=cycle:2024")
        section = _outside_section(page)
        assert section is not None, "the section must stay when the candidate has rows in another window"
        text = _visible_text(section)
        assert "No outside spending in this window." in text
        leaked = [figure for figure in _MONEY.findall(text) if figure != "$0.00"]
        assert leaked == [], leaked                      # no all-time (or trap) figure under an empty window
        table = _spender_table(page)
        rows = _spender_rows(table) if table is not None else []
        assert rows == []
        line_links = [href for href, _text in _anchors(section)
                      if urlsplit(href).path == "/candidates/900/outside-spending"]
        assert line_links, "the link to the line items must remain"
        followed = _get(outside_app, line_links[0])
        assert _bar_scope(followed) == ("cycle:2024", "", "")
        assert _line_items(followed) == []

    def test_section_is_absent_when_nothing_attaches_all_time(self, outside_app):
        # Precondition first: a profile that never renders the section must not pass this test.
        assert _outside_section(_get(outside_app, "/candidates/900")) is not None
        for candidate_id in (910, 920):          # 910: no Part-9 row; 920: named by T9 but without a candidacy
            page = _get(outside_app, f"/candidates/{candidate_id}")
            assert _outside_section(page) is None, candidate_id
            assert "D-2 Part 9" not in _visible_text(page), candidate_id
            assert "outside-spending" not in page, candidate_id        # no anchor or link to the line-item page

    @pytest.mark.parametrize("candidate_id, summary, spenders", [
        pytest.param(930, (500.00, 34500.25, 35000.25, 4), [
            (703, 0.00, 24500.25, 24500.25, 2),      # R1 20,000 + the split row's 4,500.25
            (702, 0.00, 10000.00, 10000.00, 1),      # R2
            (701, 500.00, 0.00, 500.00, 1),          # R3
        ], id="rae-rival"),
        pytest.param(940, (70000.00, 0.00, 70000.00, 1), [
            (702, 70000.00, 0.00, 70000.00, 1),      # T4 only: his cycle is 2024, so none of 900's 2022 / 2026 rows
        ], id="namesake-pat-example"),
        pytest.param(970, (3000.00, 1000.00, 4000.00, 2), [
            (703, 3000.00, 0.00, 3000.00, 1),        # O1 "Dee O'Brien"
            (701, 0.00, 1000.00, 1000.00, 1),        # O2 "DEE OBRIEN"
        ], id="middle-initial-and-apostrophe"),
    ])
    def test_other_candidates_get_their_own_figures(self, outside_app, candidate_id, summary, spenders):
        page = _get(outside_app, f"/candidates/{candidate_id}")
        section = _outside_section(page)
        assert section is not None
        assert _summary(_visible_text(section)) == summary
        assert _spender_rows(_spender_table(page)) == spenders

    def test_a_suffix_in_the_last_name_does_not_hide_the_rows_filed_without_it(self, suffix_app):
        # Candidate 980 is the record 'Lee' + 'Example, Jr' (ISBE keeps the suffix inside last_name).  Four filings
        # name him four ways -- 'Lee Example', 'Lee Example Jr', 'Lee Example, Jr' and 'Example, Lee' -- and all
        # four are his (SUFFIX_ROWS has the hand-computed figures).  Today his key is (LEE, JR): the filings that
        # carry no 'Jr' miss him, and the ', Jr' one is cut down to 'Lee Example'.
        page = _get(suffix_app, "/candidates/980")
        section = _outside_section(page)
        assert section is not None, "no outside-spending section: none of the four filings attached to 'Example, Jr'"
        text = _visible_text(section)
        assert _summary(text) == (3000.00, 12000.00, 15000.00, 4), text


# ---------------------------------------------------------------------------
# Line-item page /candidates/<id>/outside-spending
# ---------------------------------------------------------------------------

LINE_URL = "/candidates/900/outside-spending"

# (query, scope bar, scope tag, expected dates newest first, summary line or None)
# Summary lines are asserted only without a support_oppose / committee_id filter: the contract does not say
# whether the summary follows those filters (flagged in the report).
LINE_CASES = [
    pytest.param("", ("all", "", ""), "All time", ALL_DATES, WINDOW_ALL[:4], id="all-time"),
    pytest.param("?period=cycle:2026", ("cycle:2026", "", ""), "2026 Cycle", ALL_DATES[:7], WINDOW_2026[:4],
                 id="cycle-2026"),
    pytest.param("?period=cycle:2022", ("cycle:2022", "", ""), "2022 Cycle", ["2022-12-31", "2021-11-05"],
                 WINDOW_2022[:4], id="cycle-2022"),
    pytest.param("?period=cycle:2024", ("cycle:2024", "", ""), "2024 Cycle", [], None,
                 id="cycle-2024-holds-only-the-namesakes-row"),
    pytest.param("?date_from=2026-03-01&date_to=2026-06-30", ("custom", "2026-03-01", "2026-06-30"), "Custom range",
                 ["2026-06-30", "2026-05-15", "2026-03-01"], WINDOW_MID[:4], id="custom-bounds-are-inclusive"),
    pytest.param("?date_from=2026-06-30", ("custom", "2026-06-30", ""), "Custom range",
                 ["2026-09-09", "2026-07-04", "2026-06-30"], WINDOW_FROM[:4], id="custom-lower-bound-only"),
    pytest.param("?date_to=2022-12-31", ("custom", "", "2022-12-31"), "Custom range",
                 ["2022-12-31", "2021-11-05"], WINDOW_2022[:4], id="custom-upper-bound-only"),
    pytest.param("?support_oppose=S", ("all", "", ""), "All time",
                 ["2026-09-09", "2026-05-15", "2026-03-01", "2025-01-01", "2021-11-05"], None, id="supporting"),
    pytest.param("?support_oppose=O", ("all", "", ""), "All time",
                 ["2026-07-04", "2026-06-30", "2025-11-03", "2022-12-31"], None, id="opposing"),
    pytest.param("?committee_id=701", ("all", "", ""), "All time",
                 ["2026-06-30", "2025-11-03", "2025-01-01", "2021-11-05"], None, id="spender-701"),
    pytest.param("?committee_id=703", ("all", "", ""), "All time",
                 ["2026-09-09", "2026-07-04"], None, id="spender-703"),
    pytest.param("?committee_id=704", ("all", "", ""), "All time", [], None, id="a-spender-with-no-rows"),
    pytest.param("?support_oppose=S&committee_id=701", ("all", "", ""), "All time",
                 ["2025-01-01", "2021-11-05"], None, id="supporting-and-spender-701"),
    pytest.param("?support_oppose=O&committee_id=702", ("all", "", ""), "All time",
                 ["2022-12-31"], None, id="opposing-and-spender-702"),
    pytest.param("?period=cycle:2026&support_oppose=O&committee_id=701", ("cycle:2026", "", ""), "2026 Cycle",
                 ["2026-06-30", "2025-11-03"], None, id="window-and-opposing-and-spender"),
    pytest.param("?date_from=2026-03-01&date_to=2026-06-30&support_oppose=S", ("custom", "2026-03-01", "2026-06-30"),
                 "Custom range", ["2026-05-15", "2026-03-01"], None, id="custom-window-and-supporting"),
]


class TestLineItemsPage:
    """Which wrong implementation still passes?  Killed here: a missing route (every test); a table that lists every
    Part-9 row or the wrong candidate's rows; the traps counted (archived, Part 1A, raw-only, near misses, the
    namesake's 2024 row); rows in insertion / id / ascending order; the share column holding the amount (or the
    amount column holding the share); a NULL purpose / office printed as 'None'; a payee that vanishes when the first
    name is NULL; spender or filing links pointing at the wrong id (every row has its own filing); filters that are
    ignored, applied to the wrong column or that drop the window; a filter form whose submit loses the window;
    pagination that ignores `page`; a 500 or 404 for a candidate with nothing attached.
    Not pinned (accepted): the 100-rows page size and the page links (no fixture candidate has more than nine
    rows), the summary line under a support_oppose / committee_id filter, a bogus sort / dir / page value, the
    copy of the 'For / Against' cell, and whether 'Name (as filed)' of a split row is the whole filed string."""

    def test_lists_every_attached_row_newest_first_with_the_contract_columns(self, outside_app):
        page = _get(outside_app, LINE_URL)
        head = _header_text(page)
        assert "Pat Example" in head
        assert "Outside spending (D-2 Part 9)" in head
        table = _line_table(page)
        assert table is not None, "no 'Line items' table"
        assert _headers_are(table, LINE_HEADERS), table.headers
        items = _line_items(page)
        assert [item["date"] for item in items] == ALL_DATES
        for item in items:
            day = item["date"]
            expected = PAT_LINES[day]
            cells = item["cells"]
            assert item["spender_ids"] == [expected["spender"]], day
            assert SPENDERS[expected["spender"]] in cells["spender"], day
            assert item["direction"] == expected["direction"], (day, cells["direction"])
            assert _amount(cells["amount"]) == expected["amount"], (day, cells["amount"])
            shares = [_money(figure) for figure in _MONEY.findall(cells["share"])]
            assert expected["share"] in shares, (day, cells["share"])
            assert item["filing_ids"] == [expected["filing"]], day
            for token in expected["payee"]:
                assert token in cells["payee"], (day, cells["payee"])

    def test_text_cells_show_what_was_filed_and_blank_means_blank_not_none(self, outside_app):
        items = {item["date"]: item for item in _line_items(_get(outside_app, LINE_URL))}
        assert len(items) == 9
        for day, expected in PAT_LINES.items():
            cells = items[day]["cells"]
            if expected["purpose"] is None:
                assert _blank(cells["purpose"]), (day, cells["purpose"])
            else:
                assert cells["purpose"] == expected["purpose"], day
            if expected["office"] is None:
                assert _blank(cells["office"]), (day, cells["office"])
            else:
                assert cells["office"] == expected["office"], day
            if expected["name"] is None:
                assert "Pat Example" in cells["name"], (day, cells["name"])      # the split row: see the class docstring
            else:
                assert cells["name"] == expected["name"], day
            for cell_name in ("purpose", "office", "payee", "name"):
                assert "none" not in cells[cell_name].lower(), (day, cell_name, cells[cell_name])
                assert "null" not in cells[cell_name].lower(), (day, cell_name, cells[cell_name])

    @pytest.mark.parametrize("candidate_id, own_name, single_day, single_amount", [
        pytest.param(900, "Pat Example", "2026-06-30", 16000.00, id="pat-example"),     # E
        pytest.param(930, "Rae Rival", "2026-08-20", 10000.00, id="rae-rival"),         # R2
    ])
    def test_split_row_shows_the_full_amount_its_share_and_one_of_n(
            self, outside_app, candidate_id, own_name, single_day, single_amount):
        items = {item["date"]: item
                 for item in _line_items(_get(outside_app, f"/candidates/{candidate_id}/outside-spending"))}
        split = items["2026-07-04"]                        # SP: 'Rae Rival, Pat Example', 9,000.50 over two names
        assert _amount(split["cells"]["amount"]) == 9000.50
        split_shares = [_money(figure) for figure in _MONEY.findall(split["cells"]["share"])]
        assert 4500.25 in split_shares, split["cells"]["share"]
        assert re.search(r"\b1\s+of\s+2\b", split["text"]), split["text"]
        assert split["direction"] == "O"
        assert own_name in split["cells"]["name"]
        # A one-name row of the same page carries no 'of 2' label and its share is its amount.
        single = items[single_day]
        assert _amount(single["cells"]["amount"]) == single_amount
        single_shares = [_money(figure) for figure in _MONEY.findall(single["cells"]["share"])]
        assert single_amount in single_shares, single["cells"]["share"]
        assert not re.search(r"\bof\s+2\b", single["text"]), single["text"]

    def test_unknown_candidate_is_404_while_a_known_one_is_200(self, outside_app):
        # The 200 comes first on purpose: with no route registered at all every id is a 404.
        assert _line_dates(_get(outside_app, LINE_URL)) == ALL_DATES
        missing = _get(outside_app, "/candidates/999999/outside-spending", status=404)
        assert "Pat Example" not in missing

    @pytest.mark.parametrize("candidate_id, name", [(910, "Quinn Sortwell"), (920, "Pat Exemplar")])
    def test_candidate_without_attached_rows_gets_an_empty_page_not_an_error(self, outside_app, candidate_id, name):
        assert _line_dates(_get(outside_app, LINE_URL)) == ALL_DATES        # the route works for a candidate with rows
        page = _get(outside_app, f"/candidates/{candidate_id}/outside-spending")
        assert name in _header_text(page)
        assert _line_items(page) == []
        leaked = [figure for figure in _MONEY.findall(_visible_text(page)) if figure != "$0.00"]
        assert leaked == [], leaked            # 920 is named by a $770.00 row (T9), 910 by nothing

    @pytest.mark.parametrize("query, bar, tag, dates, summary", LINE_CASES)
    def test_rows_follow_the_window_and_filters(self, outside_app, query, bar, tag, dates, summary):
        page = _get(outside_app, f"{LINE_URL}{query}")
        assert _bar_scope(page) == bar
        heading = _h2_html(page, r"Line items")
        assert heading is not None, "no 'Line items' heading"
        assert f'<span class="scope-tag">{tag}</span>' in heading, heading
        assert _line_dates(page) == dates
        if summary is not None:
            assert _summary(_head_before_line_items(page)) == summary

    @pytest.mark.parametrize("query, dates", [
        pytest.param("?committee_id=701", ["2026-06-30", "2025-11-03", "2025-01-01", "2021-11-05"],
                     id="default-is-date-newest-first"),
        pytest.param("?committee_id=701&sort=date&dir=asc", ["2021-11-05", "2025-01-01", "2025-11-03", "2026-06-30"],
                     id="date-ascending"),
        pytest.param("?committee_id=701&sort=amount", ["2026-06-30", "2025-01-01", "2025-11-03", "2021-11-05"],
                     id="amount-default-direction-is-descending"),
        pytest.param("?committee_id=701&sort=amount&dir=asc", ["2021-11-05", "2025-11-03", "2025-01-01", "2026-06-30"],
                     id="amount-ascending"),
    ])
    def test_sort_and_direction_order_the_rows(self, outside_app, query, dates):
        # Spender 701's rows by date: E 2026-06-30 (16,000), B 2025-11-03 (1,000), A 2025-01-01 (8,000), G1 2021-11-05
        # (640), so date order and amount order differ.  The split row is not among them (no share-vs-amount ambiguity).
        assert _line_dates(_get(outside_app, f"{LINE_URL}{query}")) == dates

    @pytest.mark.parametrize("scope_query, bar, dates", [
        pytest.param("period=cycle:2026", ("cycle:2026", "", ""),
                     ["2026-09-09", "2026-05-15", "2026-03-01", "2025-01-01"], id="preset-window"),
        pytest.param("date_from=2026-03-01&date_to=2026-06-30", ("custom", "2026-03-01", "2026-06-30"),
                     ["2026-05-15", "2026-03-01"], id="custom-window"),
    ])
    def test_filter_form_carries_the_window_so_a_submitted_filter_keeps_it(self, outside_app, scope_query, bar, dates):
        page = _get(outside_app, f"{LINE_URL}?{scope_query}")
        forms = [form for form in _forms(page) if any(c["name"] == "support_oppose" for c in form["controls"])]
        assert len(forms) == 1, "expected exactly one filter form with a support_oppose control"
        form = forms[0]
        assert "committee_id" in {c["name"] for c in form["controls"]}
        assert form["method"] in ("", "get")
        action = urlsplit(form["action"]).path or LINE_URL
        assert action == LINE_URL
        submission = {c["name"]: c["value"] for c in form["controls"] if c["type"] == "hidden"}
        submission["support_oppose"] = "S"
        result = _get(outside_app, f"{action}?{urlencode(submission)}")
        assert _bar_scope(result) == bar
        assert _line_dates(result) == dates          # supporting rows only, still inside the window

    def test_page_two_is_empty_but_still_200(self, outside_app):
        assert len(_line_items(_get(outside_app, LINE_URL))) == 9           # page 1 holds every row
        assert len(_line_items(_get(outside_app, f"{LINE_URL}?page=1"))) == 9
        assert _line_items(_get(outside_app, f"{LINE_URL}?page=2")) == []   # an ignored `page` would repeat the rows

    def test_page_links_back_to_the_profile(self, outside_app):
        paths = [urlsplit(href).path for href, _text in _anchors(_get(outside_app, LINE_URL))]
        assert "/candidates/900" in paths

    @pytest.mark.parametrize("candidate_id, dates, summary", [
        pytest.param(930, ["2026-08-20", "2026-07-04", "2026-04-10", "2025-09-09"], (500.00, 34500.25, 35000.25, 4),
                     id="rae-rival"),
        pytest.param(940, ["2024-03-15"], (70000.00, 0.00, 70000.00, 1), id="namesake-pat-example"),
        pytest.param(970, ["2026-06-06", "2026-05-05"], (3000.00, 1000.00, 4000.00, 2),
                     id="middle-initial-and-apostrophe"),
    ])
    def test_other_candidates_list_their_own_rows(self, outside_app, candidate_id, dates, summary):
        page = _get(outside_app, f"/candidates/{candidate_id}/outside-spending")
        assert _line_dates(page) == dates
        assert _summary(_head_before_line_items(page)) == summary


# ---------------------------------------------------------------------------
# State race detail: Outside Spending Rows reconcile with the candidates' line-item pages
# ---------------------------------------------------------------------------

# (candidate name, candidate id or None for a blank id, support, oppose, total, rows)
RACE_FULL = [
    ("Pat Example", 900, 46000.50, 21500.25, 67500.75, 7),
    ("Rae Rival", 930, 500.00, 34500.25, 35000.25, 4),
    ("Nell Nullid", None, 1500.50, 0.00, 1500.50, 1),
]
RACE_MID = [
    ("Pat Example", 900, 6000.00, 16000.00, 22000.00, 3),
    ("Rae Rival", 930, 0.00, 20000.00, 20000.00, 1),
]
RACE_TAIL = [
    ("Pat Example", 900, 32000.50, 20500.25, 52500.75, 3),
    ("Rae Rival", 930, 0.00, 14500.25, 14500.25, 2),
]

# (page query on top of election_cycle=2026, scope bar of the line-item page a row link opens, the link's query
#  string, expected rows, expected 'Outside Spending (E)' card)
RACE_SCENARIOS = [
    pytest.param("period=cycle:2026", ("cycle:2026", "", ""), {"period": ["cycle:2026"]}, RACE_FULL, 104001.50,
                 id="cycle-window"),
    # An all-time page still shows only the race cycle's outside spending, so its links must say cycle:2026 and
    # NOT period=all (all time would add Pat's 2022 rows on the line-item page).
    pytest.param("period=all", ("cycle:2026", "", ""), {"period": ["cycle:2026"]}, RACE_FULL, 104001.50,
                 id="all-time-page-is-clamped-to-the-cycle"),
    pytest.param("date_from=2026-03-01&date_to=2026-06-30", ("custom", "2026-03-01", "2026-06-30"),
                 {"date_from": ["2026-03-01"], "date_to": ["2026-06-30"]}, RACE_MID, 42000.00,
                 id="custom-window-inside-the-cycle"),
    # The page window runs past the cycle end: the links carry the INTERSECTION (to 2026-12-31), not 2027-06-30.
    pytest.param("date_from=2026-06-30&date_to=2027-06-30", ("custom", "2026-06-30", "2026-12-31"),
                 {"date_from": ["2026-06-30"], "date_to": ["2026-12-31"]}, RACE_TAIL, 67001.00,
                 id="custom-window-past-the-cycle-end"),
]


def _race_page(app, query: str) -> str:
    return _get(app, f"{RACE_URL}?election_cycle=2026&{query}")


class TestRaceReconciliation:
    """Which wrong implementation still passes?  Killed here: the old two-column table (Candidate / Total Amount);
    a candidate listed once per committee row of the candidacy feed (Pat is in the feed twice: 901 and 902) and so
    counted twice; support and opposition folded into one figure; the exact-string match (misses 'Pat M. Example'
    and 'PAT EXAMPLE') or substring match (takes 'Pat Examples', 'Patience Example'); the archived, Part 1A and
    out-of-cycle rows counted; a row total that is the all-time or the page-window figure instead of the cycle
    window's; a multi-name row counted in full for both candidates (the card would overshoot the sum of the
    shares); a card that is not the sum of the rows; a link built from the page scope (`period=all`, or the raw
    page bounds) instead of the window the row was computed over; a link for the blank-id candidate; a race
    candidate whose last name carries a suffix ('Barberis, Jr') who never takes the row filed 'John Barberis'.
    Not pinned (accepted): tie order between equal totals (no fixture ties), a candidate with no rows (the custom
    window omits Nell Nullid: this suite pins that a candidate without rows is NOT listed), the help text, and the
    compat-view fallback with its INTEGER 0/1 flag predicates (every fixture here has isbe_condensed_expenditures,
    which the builder reads first; tests/test_state_race_builder.py pins the fallback)."""

    @pytest.mark.parametrize("query, link_bar, link_query, expected, card", RACE_SCENARIOS)
    def test_outside_rows_have_the_contract_columns_figures_and_order(
            self, outside_app, query, link_bar, link_query, expected, card):
        page = _race_page(outside_app, query)
        table = _race_table(page)
        assert _headers_are(table, ["Candidate", "Supporting", "Opposing", "Total", "Rows"]), table.headers
        rows = _race_rows(page)
        assert [row["name"] for row in rows] == [spec[0] for spec in expected]      # canonical names, total desc, once each
        for row, spec in zip(rows, expected):
            name, _candidate_id, support, oppose, total, count = spec
            assert row["supporting"] == support, name
            assert row["opposing"] == oppose, name
            assert row["total"] == total, name
            assert row["rows"] == count, name

    @pytest.mark.parametrize("query, link_bar, link_query, expected, card", RACE_SCENARIOS)
    def test_outside_spending_card_is_the_sum_of_the_rows(
            self, outside_app, query, link_bar, link_query, expected, card):
        page = _race_page(outside_app, query)
        rows = _race_rows(page)
        assert rows, "no outside spending rows"
        assert round(sum(row["total"] for row in rows), 2) == card
        assert _outside_card(page) == card

    @pytest.mark.parametrize("query, link_bar, link_query, expected, card", RACE_SCENARIOS)
    def test_candidate_links_point_at_the_line_item_page_with_the_window_the_row_was_computed_over(
            self, outside_app, query, link_bar, link_query, expected, card):
        rows = _race_rows(_race_page(outside_app, query))
        assert len(rows) == len(expected)
        for row, spec in zip(rows, expected):
            name, candidate_id = spec[0], spec[1]
            profile_links = [urlsplit(href) for href in row["hrefs"] if urlsplit(href).path.startswith("/candidates")]
            if candidate_id is None:
                # Nell Nullid has a blank candidate id in the candidacy feed: plain text, no /candidates/None link.
                assert profile_links == [], (name, row["hrefs"])
                continue
            assert len(profile_links) == 1, (name, row["hrefs"])
            assert profile_links[0].path == f"/candidates/{candidate_id}/outside-spending", name
            assert parse_qs(profile_links[0].query) == link_query, (name, profile_links[0].query)

    @pytest.mark.parametrize("query, link_bar, link_query, expected, card", RACE_SCENARIOS)
    def test_following_each_link_reconciles_with_the_line_item_summary_to_the_cent(
            self, outside_app, query, link_bar, link_query, expected, card):
        rows = _race_rows(_race_page(outside_app, query))
        followed_count = 0
        for row, spec in zip(rows, expected):
            name, candidate_id = spec[0], spec[1]
            if candidate_id is None:
                continue
            href = next(href for href in row["hrefs"] if urlsplit(href).path.startswith("/candidates"))
            followed = _get(outside_app, href)
            assert _bar_scope(followed) == link_bar, name
            summary = _summary(_head_before_line_items(followed))
            assert summary == (row["supporting"], row["opposing"], row["total"], row["rows"]), name
            assert len(_line_items(followed)) == row["rows"], name
            followed_count += 1
        assert followed_count == len([spec for spec in expected if spec[1] is not None])

    def test_a_page_window_outside_the_race_cycle_shows_no_outside_rows(self, outside_app):
        # 2023-2024 holds T4 ('Pat Example', 70,000, 2024-03-15); the race is cycle 2026, whose window starts
        # 2025-01-01, so the window intersection is empty.
        page = _get(outside_app, f"{RACE_URL}?election_cycle=2026&date_from=2023-01-01&date_to=2024-12-31")
        table = _race_table(page)
        assert _headers_are(table, ["Candidate", "Supporting", "Opposing", "Total", "Rows"]), table.headers
        assert _race_rows(page) == []
        assert _outside_card(page) == 0.0

    def test_a_race_candidate_with_a_suffix_in_the_last_name_takes_the_row_filed_without_it(self, suffix_app):
        # John Barberis, Jr (candidate 985) is in the race's candidacy feed under that name; one Part-9 row, filed
        # 'John Barberis' (4,000, supporting), is his.  He ranks third by total, between Rae Rival (35,000.25) and
        # Nell Nullid (1,500.50), and the card grows by his 4,000 to 108,001.50.  Today the builder keys him
        # (JOHN, JR), which the filed 'John Barberis' never meets, so he is not listed at all.
        page = _race_page(suffix_app, "period=cycle:2026")
        rows = _race_rows(page)
        names = [row["name"] for row in rows]
        assert names == ["Pat Example", "Rae Rival", "John Barberis, Jr", "Nell Nullid"], names
        barberis = rows[2]
        assert barberis["supporting"] == 4000.00
        assert barberis["opposing"] == 0.00
        assert barberis["total"] == 4000.00
        assert barberis["rows"] == 1
        assert _outside_card(page) == 108001.50
        # His row links to the line-item page, whose summary reconciles to the cent (the profile side of the match).
        hrefs = [href for href in barberis["hrefs"] if urlsplit(href).path.startswith("/candidates")]
        assert len(hrefs) == 1, barberis["hrefs"]
        assert urlsplit(hrefs[0]).path == "/candidates/985/outside-spending"
        followed = _get(suffix_app, hrefs[0])
        assert _summary(_head_before_line_items(followed)) == (4000.00, 0.00, 4000.00, 1)


def test_the_matching_note_names_hyphens_and_nicknames_on_every_page(outside_app):
    """P2-18: the visible matching note on the profile section, the line-item page and the race page says what search
    Phase 2 changed (M1 hyphens, M2 nicknames)."""
    pages = {
        "profile": _get(outside_app, "/candidates/900"),
        "line items": _get(outside_app, LINE_URL),
        "race": _race_page(outside_app, "period=cycle:2026"),
    }
    for label, page in pages.items():
        text = _visible_text(page)
        assert "apostrophes, quotes and hyphens, middle names and trailing suffixes" in text, label
        assert "common nicknames such as Mike and Michael count as the same first name" in text, label
