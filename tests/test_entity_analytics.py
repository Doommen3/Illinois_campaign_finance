"""Tests-first suite for plan step 3, part A: `database/entity_analytics.py`.

Plan: .claude/plans/race-sort-and-entity-networks.md -- section "Contract --
`database/entity_analytics.py`" and decisions N11-N13.  Every function takes the app's
psycopg compat connection and reads ISBE data from plain TABLES that stand in for the
production matviews (BOOLEAN TRUE/FALSE, dates as DATE), exactly the pattern of
tests/test_candidate_profile.py and tests/test_state_race_builder.py.

How to read the fixture (one seed helper, `_seed_world`; names are fictional)
-----------------------------------------------------------------------------
Race R = office "Member, Board of Education" / "Board of Education District" / "1B",
cycle 2026; the window of almost every test is calendar 2026 (WINDOW).

  candidate 20 "Alice Aldous"   committees 100, 101, 150 (links); 101 is also in the ACCEPTED
                                family F "Aldous Committees" with 102 (102 is NOT linked to her)
  candidate 10 "Bryn Bellamy"   committees 200, 150
  committee 150                 a slate linked to BOTH A and B
  candidate 60 "Fay Fontaine"   committee 800, candidacy election_year 2025 (cycle 2026), no receipts
  candidate 30 "Carlos Cortez"  committee 300, race S (another race), the site's big-money committee
  candidate 40 "Dana Dalton"    committee 400, race R in cycle 2024 only (spelling variant of the key)
  pending family P {100, 109} and rejected family X {150, 108} must change nothing
  ids are chosen so that sorting by label differs from sorting by candidate id or committee id.

Money traps that are planted on purpose (each is an excluded row unless a test says otherwise):
  archived rows, d2_part 2A/3A/4A (5A only counts in `in_kind_total`), a zero and a negative
  amount, rows one day outside either window bound and rows exactly ON them, redacted donors
  (flag only / address1 only / both), a NULL-state and a '' state donor, persons (first name filed) and
  organizations (first name NULL or '').

The site-wide large-contribution threshold (get_entity_risk) is engineered, not lucky:
  the receipt base (archived = FALSE, d2_part LIKE '1%', amount > 0, ALL committees, ALL time)
  has exactly N_BASE_ROWS = 122 rows.  Sorted descending the top six are
  20,000 (C), 13,521, 13,519, 9,000 (C), 6,761, 6,759; the 7th is 3,400 (C) and the 8th is
  3,000 (C, dated 2024, outside WINDOW).  percentile_cont(0.95) sits at position
  0.95 * 121 = 114.95 ascending = between the 8th (3,000) and 7th (3,400) largest:
      p95 = 3,000 + 0.95 * (3,400 - 3,000) = 3,380   ->   threshold = max(5,000, 2 * 3,380) = 6,760
  The row amounts around it leave a margin of 1.00 on each side (6,759 / 6,761, 13,519 / 13,521) so
  float noise cannot decide a test; exact `>=` boundaries are tested through the override.
  Thresholds the other readings would produce (measured with a throwaway reference implementation):
  the global /analytics/risk floor-rank rule -> 3,000 -> 6,000 (it would flag Yul's 6,759); percentile_disc
  -> 6,800; archived rows counted -> 13,521.2; every d2_part counted -> 11,034.9; the window applied to the
  p95 (drops the 2024 pivot) -> 13,518; the entity's own rows only -> 20,280; no 5,000 floor -> 4,800 once
  the two pivots are neutralised (test_threshold_floor_of_5000_binds...).
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

import pytest

from database.analytics import _gini, _state_race_slug, get_donor_concentration
from database.connection import get_db, init_db
from database.entity_analytics import (
    ENTITY_CACHE_REV,
    ENTITY_KINDS,
    entity_donor_rows,
    get_entity_donors,
    get_entity_geography,
    get_entity_relationships,
    get_entity_risk,
    resolve_entity,
)
from database.race_keys import race_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TODAY = date(2026, 10, 26)  # current cycle 2026 (never the wall clock)
WINDOW = {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)}

RACE_R = ("Member, Board of Education", "Board of Education District", "1B")
RACE_R_KEY = _state_race_slug(*RACE_R)
RACE_R_LABEL = "Member, Board of Education - Board of Education District 1B"
RACE_S = ("State Senator", "Senate District", "12")
RACE_S_KEY = _state_race_slug(*RACE_S)

# Candidate ids: A and B are deliberately in the opposite order of their labels.
CAND_A, CAND_B, CAND_C, CAND_D, CAND_F = 20, 10, 30, 40, 60

# Number of rows in the site-wide receipt base (archived = FALSE, d2_part LIKE '1%', amount > 0).
N_BASE_ROWS = 122
SITE_P95 = 3380.0  # see module docstring
SITE_THRESHOLD = 6760.0

COMMITTEE_NAMES = {
    100: "Friends of Alice Aldous",
    101: "Aldous 2026 Committee",
    102: "Aldous Victory Fund",
    108: "Rejected Family Fund",
    109: "Aldous Legacy Fund",
    150: "Citizens for Better Schools Slate",
    200: "Bryn Bellamy for Board",
    300: "Carlos Cortez for Senate",
    400: "Dana Dalton for Board",
    500: "Prairie Educators PAC",
    510: "Labor Alliance PAC",
    520: "Solo Giver PAC",
    530: "Big Overlap PAC",
    540: "Pair Fund",
    799: "Filler Committee",
    800: "Fay Fontaine for Board",
}
for _cid in range(600, 609):
    COMMITTEE_NAMES[_cid] = f"Spike Test {_cid}"
for _cid in range(610, 615):
    COMMITTEE_NAMES[_cid] = f"Concentration Test {_cid}"


# ---------------------------------------------------------------------------
# Donors (fictional) and the donor-key formula
# ---------------------------------------------------------------------------

def _donor(first, last, address1, city, state, zipcode, *, address2=None, occupation=None, employer=None,
           redaction_requested=False):
    return {
        "first_name": first, "last_name": last, "address1": address1, "address2": address2,
        "city": city, "state": state, "zipcode": zipcode, "occupation": occupation, "employer": employer,
        "redaction_requested": redaction_requested,
    }


def _donor_key(donor):
    """The `/donors/key/<key>` expression: LOWER(TRIM(first|last|address1|address2|city|state|zip)), NULL -> ''."""
    fields = ["first_name", "last_name", "address1", "address2", "city", "state", "zipcode"]
    return "|".join((donor[field] or "") for field in fields).strip().lower()


def _donor_name(donor):
    return " ".join(part for part in (donor["first_name"], donor["last_name"]) if part)


IVY = _donor("Ivy", "Ingram", "12 Oak St", "Chicago", "IL", "60601", occupation="Teacher", employer="Lakeview Elementary")
JON = _donor("Jon", "Jarvis", "34 Elm St", "Springfield", "IL", "62701", address2="Apt 2", occupation="Engineer",
             employer="Prairie Power")
KAY = _donor("Kay", "Keller", "56 Pine Rd", "Naperville", "il", "60540-1234", occupation="Nurse", employer="DuPage Clinic")
LEO = _donor("Leo", "Lund", "78 Birch Ln", "Madison", "WI", "53703", occupation="Chef", employer="Capitol Grill")
MIA = _donor("Mia", "Moreno", "90 Cedar Ct", "Chicago", "IL", "60601", occupation="Architect", employer="Moreno Design")
NED = _donor("Ned", "Novak", "21 Maple Ave", "Evanston", "IL", "60201", occupation="Professor", employer="Lakeshore College")
OPAL = _donor("Opal", "Ortiz", "43 Walnut Dr", None, None, None)  # no city / state / zip at all
EVA = _donor("Eva", "Eaton", "9 Quiet St", "", "", "")  # blank strings, not NULLs
PIA = _donor("Pia", "Petrov", "65 Spruce Way", "NAPERVILLE", "IL", "60540")  # same city as Kay, other spelling
SAL = _donor("Sal", "Sandoval", "87 Ash Blvd", "Springfield", "MO", "65802")  # Springfield, but Missouri
SLOAN = _donor("Sloan", "Sterling", "19 Slate Ln", "Rockford", "IL", "61101")
ZED = _donor("Zed", "Zimmer", "5 Zephyr Rd", "Rockford", "IL", "61101")
VIC = _donor("Vic", "Vance", "2 Victory Ln", "Chicago", "IL", "60601")
WES = _donor("Wes", "Whitlock", "4 Westgate Rd", "Peoria", "IL", "61602")
YUL = _donor("Yul", "Young", "8 Yarrow Ct", "Joliet", "IL", "60432")
ZOE = _donor("Zoe", "Zeller", "10 Zenith Ave", "Chicago", "IL", "60601")
# Redacted donors: ISBE format (both markers, address blanked) / flag only (address kept) / address marker only.
RAE = _donor("Rae", "Ramsey", "Redaction Requested", None, None, None, redaction_requested=True)
BO = _donor("Bo", "Barlow", "1 Hidden Way", "Chicago", "IL", "60601", redaction_requested=True)
ADA = _donor("Ada", "Archer", "Redaction Requested", "Chicago", "IL", "60601", redaction_requested=False)
# Organizations: first name NULL / first name ''.
ACME = _donor(None, "Acme Union Local 5", "1 Union Hall Rd", "Chicago", "IL", "60601")
BCO = _donor("", "Bright Co", "2 Commerce Pl", "Springfield", "IL", "62701")
PAC2 = _donor(None, "Prairie Teachers PAC", "3 Capitol Ave", "Springfield", "IL", "62701")
# Committee 300's money (site-wide threshold engineering) and the filler donor.
BIG1 = _donor("Gil", "Grant", "11 Granite Rd", "Chicago", "IL", "60601")
BIG2 = _donor("Hal", "Hart", "12 Heron Rd", "Chicago", "IL", "60601")
BIG3 = _donor("Ira", "Irwin", "13 Iris Rd", "Chicago", "IL", "60601")
BIG4 = _donor("Jax", "Jensen", "14 Juniper Rd", "Chicago", "IL", "60601")
BIG5 = _donor("Kit", "Knox", "15 Kestrel Rd", "Chicago", "IL", "60601")
BIG6 = _donor("Lyn", "Lowe", "16 Linden Rd", "Chicago", "IL", "60601")
FILL = _donor("Fil", "Filler", "17 Filler Rd", "Peoria", "IL", "61602")


def _gen(tag, number):
    """A one-off donor for the generated spike / concentration committees."""
    return _donor("Gen", f"{tag}{number:03d}", f"{number} Generated Rd", "Peoria", "IL", "61602")


# ---------------------------------------------------------------------------
# Receipts: (committee_id, donor, amount, received_date, d2_part, archived)
# ---------------------------------------------------------------------------

def _r(committee_id, donor, amount, received, part="1A", archived=False):
    return {
        "committee_id": committee_id, "donor": donor, "amount": float(amount), "received_date": received,
        "d2_part": part, "archived": archived,
    }


RECEIPT_ROWS = [
    # ---- committee 100 "Friends of Alice Aldous" (counted rows) ------------------------------------------
    _r(100, IVY, 1000.00, "2026-01-01"),   # exactly ON the lower window bound
    _r(100, IVY, 500.00, "2026-03-10"),
    _r(100, JON, 2000.00, "2026-02-15"),
    _r(100, KAY, 250.00, "2026-04-05"),
    _r(100, ACME, 1250.00, "2026-05-05"),
    _r(100, RAE, 100.00, "2026-06-06"),
    _r(100, BO, 100.00, "2026-06-07"),
    _r(100, ADA, 100.00, "2026-06-08"),
    _r(100, OPAL, 50.00, "2026-07-07"),
    _r(100, EVA, 25.00, "2026-07-08"),
    _r(100, NED, 400.00, "2026-08-01"),
    _r(100, LEO, 300.00, "2026-12-31"),    # exactly ON the upper window bound
    _r(100, WES, 6761.00, "2026-01-12"),   # large, just ABOVE the threshold 6,760
    _r(100, YUL, 6759.00, "2026-01-13"),   # large-looking, just BELOW the threshold
    # ---- committee 100: rows that must never count -----------------------------------------------------
    _r(100, KAY, 0.00, "2026-04-06"),                   # zero
    _r(100, KAY, -75.00, "2026-04-07"),                 # negative (refund)
    _r(100, IVY, 9999.00, "2026-03-11", archived=True),  # archived (superseded by an amendment)
    _r(100, ACME, 3000.00, "2026-02-02", part="2A"),    # transfer in
    _r(100, JON, 4000.00, "2026-02-03", part="3A"),     # loan
    _r(100, KAY, 600.00, "2026-02-04", part="4A"),      # other receipt
    _r(100, IVY, 800.00, "2025-12-31"),                 # one day BEFORE the window
    _r(100, IVY, 900.00, "2027-01-01"),                 # one day AFTER the window
    _r(100, ACME, 700.00, "2026-05-06", part="5A"),     # in-kind (in_kind_total only)
    _r(100, ACME, 333.00, "2025-05-06", part="5A"),     # in-kind outside the window
    # ---- committee 101 "Aldous 2026 Committee" ------------------------------------------------------------
    _r(101, VIC, 13521.00, "2026-01-15"),  # large: exactly 2 * 6,760 + 1  -> severity high
    _r(101, PIA, 80.00, "2026-02-02"),
    _r(101, SAL, 60.00, "2026-03-03"),
    _r(101, KAY, 40.00, "2026-03-04"),
    _r(101, NED, 400.00, "2026-08-02"),    # ties Ned's 400 at committee 100
    _r(101, IVY, 250.00, "2026-09-01"),
    _r(101, PAC2, 600.00, "2026-10-10"),
    # ---- committee 102 "Aldous Victory Fund" (family F member, not linked to Alice) ---------------------------
    _r(102, ZED, 150.00, "2026-05-05"),
    _r(102, IVY, 90.00, "2026-05-06"),
    _r(102, KAY, 10.00, "2026-05-07"),
    # ---- committee 150 slate (in both race groups) -------------------------------------------------------------
    _r(150, SLOAN, 100.00, "2026-06-15"),
    _r(150, SLOAN, 250.00, "2026-06-16"),
    _r(150, ACME, 120.00, "2026-04-04", part="5A"),  # in-kind on the slate: counted in BOTH groups' in_kind_total
    # ---- committee 200 "Bryn Bellamy for Board" ------------------------------------------------------------------
    _r(200, ZOE, 13519.00, "2026-02-20"),  # large: just BELOW 2 * 6,760 -> severity medium
    _r(200, IVY, 700.00, "2026-02-21"),
    _r(200, MIA, 1500.00, "2026-03-03"),
    _r(200, MIA, 1000.00, "2026-04-04"),
    _r(200, BCO, 200.00, "2026-05-05"),
    _r(200, KAY, 150.00, "2026-05-06"),
    # ---- committee 300 "Carlos Cortez for Senate": race S, and the site-wide threshold engineering -------------
    _r(300, JON, 400.00, "2026-03-03"),
    _r(300, IVY, 300.00, "2026-03-04"),
    _r(300, KAY, 200.00, "2026-03-05"),
    _r(300, BIG1, 20000.00, "2026-03-01"),                 # top-6 row, NOT in the entity
    _r(300, BIG2, 9000.00, "2026-04-01"),                  # top-6 row, NOT in the entity
    _r(300, BIG3, 3400.00, "2026-05-01"),                  # 7th largest
    _r(300, BIG4, 3000.00, "2024-05-01"),                  # 8th largest, OUTSIDE the 2026 window
    _r(300, BIG5, 50000.00, "2026-06-01", archived=True),  # archived: not in the p95
    _r(300, BIG6, 40000.00, "2026-06-02", part="2A"),      # transfer: not in the p95
    # ---- committees that co-give with the entity's donors (relationships) ------------------------------------------
    _r(500, IVY, 600.00, "2026-04-01"),
    _r(500, JON, 600.00, "2026-04-02"),
    _r(500, KAY, 100.00, "2025-06-01"),                    # outside the window: would be a 3rd shared donor
    _r(500, NED, 2000.00, "2026-04-03", archived=True),    # archived: would be a 4th shared donor
    _r(500, ACME, 800.00, "2026-04-04", part="2A"),        # not a contribution part
    _r(510, IVY, 1200.00, "2026-04-05"),
    _r(510, KAY, 800.00, "2026-04-06"),
    _r(520, NED, 1300.00, "2026-04-07"),                   # ONE shared donor, more money than committee 300's three
    _r(530, VIC, 700.00, "2026-04-10"),                    # seven shared donors, amounts in the entity's own rank order
    _r(530, ZOE, 650.00, "2026-04-10"),
    _r(530, WES, 600.00, "2026-04-10"),
    _r(530, YUL, 550.00, "2026-04-10"),
    _r(530, MIA, 500.00, "2026-04-10"),
    _r(530, IVY, 450.00, "2026-04-10"),
    _r(530, JON, 400.00, "2026-04-10"),
    _r(540, VIC, 100.00, "2026-04-11"),
    _r(540, WES, 100.00, "2026-04-12"),
    _r(109, KAY, 50.00, "2026-04-13"),                     # member of the PENDING family with 100
]

# ---- generated committees ----------------------------------------------------------------------------------------
# Spike committees 600-607: monthly sums per committee, one fresh donor per row so concentration stays tiny.
# (committee, month "2026-MM", amounts of the rows in that month)
SPIKE_MONTHS = [
    (600, "01", [600]), (600, "02", [600]), (600, "03", [600]), (600, "04", [1300, 1300]), (600, "05", [700]),
    (601, "01", [100]), (601, "02", [100]), (601, "03", [100]), (601, "04", [2099.99]),
    (602, "01", [1200]), (602, "02", [1200]), (602, "03", [1200]), (602, "04", [1799, 1800]),
    (603, "01", [1200]), (603, "02", [1200]), (603, "03", [1200]), (603, "04", [1800, 1800]),
    (604, "01", [1000]), (604, "04", [1500, 1500]),
    (605, "01", [1000]), (605, "02", [1500, 1500]),
    (606, "01", [2400, 2400, 2400]),
    (607, "03", [1500, 1500]), (607, "04", [100]),
]
# Committee 608 holds three small gifts in Oct-Dec 2025 (outside WINDOW) and 2,300 in January 2026: read over all time
# that January is a spike (2,300 >= 3 x 100 and 2,200 above it); inside the 2026 window it is the first month and never
# flagged.  (committee, received_date, amount)
SPIKE_OUT_OF_WINDOW = [(608, "2025-10-10", 100), (608, "2025-11-10", 100), (608, "2025-12-10", 100), (608, "2026-01-10", 2300)]
# Concentration committees 610-614: (committee, donor amounts) all dated 2026-02-10.
CONCENTRATION_COMMITTEES = [
    (610, [800, 100, 100]),    # 3 donors, HHI 6,600           -> flagged
    (611, [600, 400]),         # 2 donors, HHI 5,200           -> NOT flagged (needs >= 3 donors)
    (612, [580, 320, 100]),    # 3 donors, HHI 4,488           -> NOT flagged (just below 4,500)
    (613, [590, 310, 100]),    # 3 donors, HHI 4,542           -> flagged
    (614, [30, 70, 310, 590]),  # 4 donors, HHI exactly 4,500   -> flagged (>=)
]

# Filler to make the site-wide base exactly N_BASE_ROWS rows: one donor, committee 799, 2024, $5 each (see _all_rows).


def _generated_rows():
    rows = []
    serial = 0
    for committee_id, month, amounts in SPIKE_MONTHS:
        for index, amount in enumerate(amounts):
            serial += 1
            rows.append(_r(committee_id, _gen("sp", serial), amount, f"2026-{month}-{10 + index:02d}"))
    for committee_id, received, amount in SPIKE_OUT_OF_WINDOW:
        serial += 1
        rows.append(_r(committee_id, _gen("sp", serial), amount, received))
    for committee_id, amounts in CONCENTRATION_COMMITTEES:
        for index, amount in enumerate(amounts):
            serial += 1
            donor = _gen("cn", serial)
            rows.append(_r(committee_id, donor, amount, "2026-02-10"))
            if committee_id == 612 and index == 0:
                # H4488's biggest donor also gave 500 in 2025: over all time 1,080 / 320 / 100 is HHI 5,684 (flagged),
                # inside the 2026 window it is 4,488 (not).
                rows.append(_r(committee_id, donor, 500.0, "2025-06-01"))
    return rows


def _is_base_row(row):
    return (not row["archived"]) and row["d2_part"].startswith("1") and row["amount"] > 0


def _all_rows():
    rows = RECEIPT_ROWS + _generated_rows()
    base = sum(1 for row in rows if _is_base_row(row))
    filler = N_BASE_ROWS - base
    assert filler >= 0, f"fixture has {base} base rows, more than N_BASE_ROWS"
    return rows + [_r(799, FILL, 5.00, "2024-01-01") for _ in range(filler)]


# ---------------------------------------------------------------------------
# Bulk-D-2 reconciliation rows: (id, committee, doc, d2_total_receipts, receipts_amount_sum, first, last)
# receipts_minus_d2_total = receipts_amount_sum - d2_total_receipts (the table's own definition).
# ---------------------------------------------------------------------------

RECON_ROWS = [
    (1, 100, 9100, 10000.00, 11000.00, "2026-01-01", "2026-06-30"),  # diff +1,000.00  -> flagged (boundary, >=)
    (2, 100, 9101, 5000.00, 6500.50, "2019-01-01", "2019-12-31"),    # diff +1,500.50  -> flagged, all-time (2019)
    (3, 101, 9102, 8000.00, 8999.00, "2026-01-01", "2026-06-30"),    # diff   +999.00  -> NOT flagged
    (4, 200, 9103, 6000.00, 3500.00, "2026-01-01", "2026-06-30"),    # diff -2,500.00  -> flagged (ABS)
    (5, 200, 9104, 1000.00, 0.01, "2026-01-01", "2026-06-30"),       # diff   -999.99  -> NOT flagged
    (6, 300, 9105, 100.00, 90000.00, "2026-01-01", "2026-06-30"),    # diff +89,900    -> outside both entities
    (7, 102, 9106, 2000.00, 5000.00, "2026-01-01", "2026-06-30"),    # diff +3,000     -> only the candidate entity
]


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------

def _seed_world(conn, *, recon=True):
    """Create and fill every table the module reads; returns the ids the tests cannot hard-code."""
    conn.execute(
        """
        CREATE TABLE isbe_committees (
            id INTEGER PRIMARY KEY,
            name TEXT,
            type TEXT,
            active BOOLEAN DEFAULT TRUE,
            city TEXT,
            state TEXT,
            zipcode TEXT
        )
        """
    )
    # Inserted in descending id order so heap order is not the answer.
    for committee_id in sorted(COMMITTEE_NAMES, reverse=True):
        conn.execute(
            "INSERT INTO isbe_committees (id, name, type, active, city, state, zipcode) "
            "VALUES (?, ?, 'Candidate', TRUE, 'Springfield', 'IL', '62701')",
            (committee_id, COMMITTEE_NAMES[committee_id]),
        )

    conn.execute(
        """
        CREATE TABLE isbe_candidates (
            id INTEGER PRIMARY KEY,
            last_name TEXT, first_name TEXT,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT,
            office TEXT, district_type TEXT, district TEXT,
            residence_county TEXT, party TEXT,
            redaction_requested BOOLEAN DEFAULT FALSE
        )
        """
    )
    for candidate_id, first, last in (
        (CAND_F, "Fay", "Fontaine"), (CAND_D, "Dana", "Dalton"), (CAND_C, "Carlos", "Cortez"),
        (CAND_A, "Alice", "Aldous"), (CAND_B, "Bryn", "Bellamy"),
    ):
        conn.execute(
            "INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Independent')",
            (candidate_id, last, first, RACE_R[0], RACE_R[1], RACE_R[2]),
        )

    conn.execute(
        "CREATE TABLE isbe_candidate_committees (id INTEGER PRIMARY KEY, committee_id INTEGER, candidate_id INTEGER)"
    )
    links = [(150, CAND_B), (800, CAND_F), (200, CAND_B), (101, CAND_A), (400, CAND_D),
             (150, CAND_A), (300, CAND_C), (100, CAND_A)]  # an order that matches neither ids nor labels
    for index, (committee_id, candidate_id) in enumerate(links, start=1):
        conn.execute(
            "INSERT INTO isbe_candidate_committees (id, committee_id, candidate_id) VALUES (?, ?, ?)",
            (index, committee_id, candidate_id),
        )

    # The candidacy feed: a TABLE here, a VIEW in production (candidacy x candidate x linked committee).
    conn.execute(
        """
        CREATE TABLE isbe_race_candidacies (
            candidacy_id INTEGER, candidate_id INTEGER, election_year INTEGER, election_cycle INTEGER,
            election_type TEXT, race_type TEXT, outcome TEXT, candidate_full_name TEXT,
            office_sought TEXT, district_type TEXT, district TEXT, candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER, committee_name TEXT
        )
        """
    )
    r_office, r_type, r_district = RACE_R
    s_office, s_type, s_district = RACE_S
    # (candidate, name, election_year, race tuple, committee) -- candidate 10 first, so insertion order differs
    # from label order; Fay's candidacy is election_year 2025 (cycle 2026); the 2024 rows spell the race
    # differently (case / spacing) but share the slug.
    candidacy_rows = [
        (CAND_B, "Bryn Bellamy", 2026, (r_office, r_type, r_district), 200),
        (CAND_B, "Bryn Bellamy", 2026, (r_office, r_type, r_district), 150),
        (CAND_F, "Fay Fontaine", 2025, (r_office, r_type, r_district), 800),
        (CAND_C, "Carlos Cortez", 2026, (s_office, s_type, s_district), 300),
        (CAND_A, "Alice Aldous", 2026, (r_office, r_type, r_district), 150),
        (CAND_A, "Alice Aldous", 2026, (r_office, r_type, r_district), 101),
        (CAND_A, "Alice Aldous", 2026, (r_office, r_type, r_district), 100),
        (CAND_A, "Alice Aldous", 2024, (r_office, r_type, r_district), 100),
        (CAND_A, "Alice Aldous", 2024, (r_office, r_type, r_district), 101),
        (CAND_A, "Alice Aldous", 2024, (r_office, r_type, r_district), 150),
        (CAND_D, "Dana Dalton", 2024, ("MEMBER,  BOARD OF EDUCATION", "board of education district", "1b"), 400),
    ]
    for candidacy_id, (candidate_id, name, year, (office, district_type, district), committee_id) in enumerate(
        candidacy_rows, start=1
    ):
        conn.execute(
            """
            INSERT INTO isbe_race_candidacies (
                candidacy_id, candidate_id, election_year, election_cycle, election_type, race_type, outcome,
                candidate_full_name, office_sought, district_type, district, candidate_party_affiliation,
                committee_id_sbe, committee_name
            ) VALUES (?, ?, ?, ?, 'General Election', 'open seat', NULL, ?, ?, ?, ?, 'Independent', ?, ?)
            """,
            (
                candidacy_id, candidate_id, year, year + year % 2, name, office, district_type, district,
                committee_id, COMMITTEE_NAMES[committee_id],
            ),
        )

    # Committee families: pending (100, 109) first, ACCEPTED (101, 102) second, rejected (150, 108) last.
    for label, status, members in (
        ("Aldous Pending Family", "pending", [100, 109]),
        ("Aldous Committees", "accepted", [101, 102]),
        ("Rejected Family", "rejected", [150, 108]),
    ):
        member_hash = hashlib.sha1(",".join(str(i) for i in sorted(members)).encode()).hexdigest()
        reviewed_at = None if status == "pending" else "2026-01-15 10:00:00"
        family = conn.execute(
            "INSERT INTO committee_family (label, status, evidence, member_hash, reviewed_at) "
            "VALUES (?, ?, CAST(? AS JSONB), ?, ?) RETURNING id",
            (label, status, json.dumps({}), member_hash, reviewed_at),
        ).fetchone()
        for committee_id in members:
            conn.execute(
                "INSERT INTO committee_family_member (family_id, committee_id) VALUES (?, ?)",
                (family["id"], committee_id),
            )

    # The receipts stand-in for the matview: a plain TABLE with the production column types.
    conn.execute(
        """
        CREATE TABLE isbe_condensed_receipts (
            id INTEGER PRIMARY KEY,
            committee_id INTEGER,
            filed_doc_id INTEGER,
            last_name TEXT,
            first_name TEXT,
            received_date DATE,
            amount DOUBLE PRECISION,
            occupation TEXT,
            employer TEXT,
            address1 TEXT,
            address2 TEXT,
            city TEXT,
            state TEXT,
            zipcode TEXT,
            d2_part TEXT,
            archived BOOLEAN DEFAULT FALSE,
            redaction_requested BOOLEAN DEFAULT FALSE
        )
        """
    )
    numbered = [dict(row, id=1000 + index) for index, row in enumerate(_all_rows())]
    # Insert smallest amounts first (ties: later ids first) so heap order is the opposite of "amount desc".
    for row in sorted(numbered, key=lambda item: (item["amount"], -item["id"])):
        donor = row["donor"]
        conn.execute(
            """
            INSERT INTO isbe_condensed_receipts (
                id, committee_id, filed_doc_id, last_name, first_name, received_date, amount, occupation, employer,
                address1, address2, city, state, zipcode, d2_part, archived, redaction_requested
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["committee_id"], 9000 + row["committee_id"], donor["last_name"], donor["first_name"],
                date.fromisoformat(row["received_date"]), row["amount"], donor["occupation"], donor["employer"],
                donor["address1"], donor["address2"], donor["city"], donor["state"], donor["zipcode"],
                row["d2_part"], row["archived"], donor["redaction_requested"],
            ),
        )

    if recon:
        conn.execute(
            """
            CREATE TABLE bulk_d2_receipts_recon (
                d2_totals_record_id BIGINT,
                committee_id_sbe INTEGER,
                committee_name TEXT,
                filed_doc_id INTEGER,
                d2_total_receipts DOUBLE PRECISION,
                d2_total_expenditures DOUBLE PRECISION,
                ending_funds_available DOUBLE PRECISION,
                is_archived INTEGER,
                receipt_row_count BIGINT,
                receipts_amount_sum DOUBLE PRECISION,
                first_receipt_date TEXT,
                last_receipt_date TEXT,
                receipts_minus_d2_total DOUBLE PRECISION
            )
            """
        )
        for record_id, committee_id, doc, d2_total, receipts_sum, first, last in RECON_ROWS:
            conn.execute(
                """
                INSERT INTO bulk_d2_receipts_recon (
                    d2_totals_record_id, committee_id_sbe, committee_name, filed_doc_id, d2_total_receipts,
                    d2_total_expenditures, ending_funds_available, is_archived, receipt_row_count,
                    receipts_amount_sum, first_receipt_date, last_receipt_date, receipts_minus_d2_total
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 3, ?, ?, ?, ?)
                """,
                (
                    record_id, committee_id, COMMITTEE_NAMES[committee_id], doc, d2_total, receipts_sum, first, last,
                    receipts_sum - d2_total,
                ),
            )
    conn.commit()


def _family_id(conn):
    return conn.execute("SELECT id FROM committee_family WHERE label = 'Aldous Committees'").fetchone()["id"]


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "entity_analytics.db")
    init_db(db_path)
    connection = get_db(db_path)
    try:
        _seed_world(connection, recon=True)
        yield connection
    finally:
        connection.close()


@pytest.fixture
def conn_no_recon(tmp_path):
    db_path = str(tmp_path / "entity_analytics_no_recon.db")
    init_db(db_path)
    connection = get_db(db_path)
    try:
        _seed_world(connection, recon=False)
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Hand-built entities (contract shape) so the aggregate tests do not depend on resolve_entity
# ---------------------------------------------------------------------------

def _group(label, committee_ids, candidate_id=None, family_id=None):
    return {"label": label, "committee_ids": list(committee_ids), "candidate_id": candidate_id, "family_id": family_id}


def _entity(kind, key, label, groups, *, cycle=None, candidate_ids=()):
    committee_ids = sorted({committee_id for group in groups for committee_id in group["committee_ids"]})
    return {
        "kind": kind, "key": key, "label": label, "cycle": cycle, "committee_ids": committee_ids,
        "candidate_ids": list(candidate_ids), "groups": groups,
    }


GROUP_ALICE = _group("Alice Aldous", [100, 101, 150], CAND_A)
GROUP_BRYN = _group("Bryn Bellamy", [150, 200], CAND_B)
GROUP_FAY = _group("Fay Fontaine", [800], CAND_F)
RACE_ENTITY = _entity(
    "race", RACE_R_KEY, RACE_R_LABEL, [GROUP_ALICE, GROUP_BRYN, GROUP_FAY], cycle=2026,
    candidate_ids=[CAND_B, CAND_A, CAND_F],
)
CORTEZ_ENTITY = _entity(
    "candidate", str(CAND_C), "Carlos Cortez", [_group("Carlos Cortez for Senate", [300], CAND_C)],
    candidate_ids=[CAND_C],
)
SPIKE_ENTITY = _entity("race", "spike-test", "Spike test", [_group("Spike group", list(range(600, 609)))])
CONCENTRATION_ENTITY = _entity(
    "race", "concentration-test", "Concentration test",
    [
        _group("H3", [610]), _group("H2", [611]), _group("H4488", [612]), _group("H4542", [613]),
        _group("H4500", [614]),
    ],
)


def _candidate_a_entity(conn):
    """Alice as the resolver should return her: the accepted family as ONE group, the other committees alone."""
    return _entity(
        "candidate", str(CAND_A), "Alice Aldous",
        [
            _group("Aldous Committees", [101, 102], CAND_A, _family_id(conn)),
            _group("Citizens for Better Schools Slate", [150], CAND_A),
            _group("Friends of Alice Aldous", [100], CAND_A),
        ],
        candidate_ids=[CAND_A],
    )


def _assert_keys(item, expected):
    actual = set(item.keys())
    wanted = set(expected)
    assert actual - wanted == set(), f"unexpected keys: {sorted(actual - wanted)}"
    assert wanted - actual == set(), f"missing keys: {sorted(wanted - actual)}"


def _near(value, tolerance=0.005):
    return pytest.approx(value, abs=tolerance)


# ===========================================================================
# Module constants
# ===========================================================================

def test_entity_kinds_and_cache_revision_constants():
    """Kills: a kind added or renamed (the routes whitelist on this tuple) and an empty cache revision.
    (Passes against the stub by design -- the constants are part of the compile-only surface.)"""
    assert ENTITY_KINDS == ("candidate", "race")
    assert isinstance(ENTITY_CACHE_REV, str)
    assert ENTITY_CACHE_REV.strip() != ""


# ===========================================================================
# resolve_entity -- candidates
# ===========================================================================

RESOLVED_ENTITY_KEYS = {"kind", "key", "label", "cycle", "committee_ids", "candidate_ids", "groups"}
RESOLVED_GROUP_KEYS = {"label", "committee_ids", "candidate_id", "family_id"}


@pytest.mark.parametrize("key", ["20", 20], ids=["str-key", "int-key"])
def test_candidate_entity_for_alice_includes_the_accepted_family_member_and_groups_it(conn, key):
    """Candidate 20 is linked to 100, 101, 150.  101 is in the ACCEPTED family {101, 102}: 102 joins and the
    two form ONE group.  Pending family {100, 109} and rejected family {150, 108} add nothing.
    Group order: the family first, then the single committees by label:
      'Aldous Committees' < 'Citizens for Better Schools Slate' (150) < 'Friends of Alice Aldous' (100)
    -- the label order differs from the committee-id order (100, 150), so an id sort fails.
    Kills: no family expansion; pending / rejected families expanded; int-vs-str key crash; groups per
    committee without the family merge; unsorted groups; extra keys."""
    entity = resolve_entity(conn, "candidate", key)
    assert entity is not None
    _assert_keys(entity, RESOLVED_ENTITY_KEYS)
    assert entity["kind"] == "candidate"
    assert entity["key"] == "20"
    assert entity["label"] == "Alice Aldous"
    assert entity["cycle"] is None
    assert entity["committee_ids"] == [100, 101, 102, 150]
    assert entity["candidate_ids"] == [CAND_A]

    groups = entity["groups"]
    assert [group["label"] for group in groups] == [
        "Aldous Committees", "Citizens for Better Schools Slate", "Friends of Alice Aldous",
    ]
    for group in groups:
        _assert_keys(group, RESOLVED_GROUP_KEYS)
    assert sorted(groups[0]["committee_ids"]) == [101, 102]
    assert groups[0]["family_id"] == _family_id(conn)
    assert sorted(groups[1]["committee_ids"]) == [150]
    assert groups[1]["family_id"] is None
    assert sorted(groups[2]["committee_ids"]) == [100]
    assert groups[2]["family_id"] is None


def test_candidate_entity_never_includes_pending_or_rejected_family_members(conn):
    """109 shares a PENDING family with 100, 108 a REJECTED one with 150 (and 102 an accepted one with 101).
    Kills: any status filter missing from the family lookup."""
    entity = resolve_entity(conn, "candidate", CAND_A)
    assert 109 not in entity["committee_ids"]
    assert 108 not in entity["committee_ids"]
    assert 102 in entity["committee_ids"]


@pytest.mark.parametrize(
    "candidate_id, label, committee_ids, group_labels",
    [
        # Bryn: links 200 and the slate 150, no family.  'Bryn Bellamy for Board' < 'Citizens ... Slate'.
        (CAND_B, "Bryn Bellamy", [150, 200], ["Bryn Bellamy for Board", "Citizens for Better Schools Slate"]),
        # Carlos: one committee, one group; no other candidate's committee leaks in through the slate.
        (CAND_C, "Carlos Cortez", [300], ["Carlos Cortez for Senate"]),
    ],
    ids=["bryn-no-family", "carlos-single"],
)
def test_candidate_entity_without_families_has_one_group_per_committee(conn, candidate_id, label, committee_ids,
                                                                       group_labels):
    """Kills: a candidate filter missing (all links returned), family_id set on a plain committee group,
    committees grouped together when no family exists, groups left in link order."""
    entity = resolve_entity(conn, "candidate", str(candidate_id))
    assert entity["label"] == label
    assert entity["committee_ids"] == committee_ids
    assert entity["candidate_ids"] == [candidate_id]
    assert [group["label"] for group in entity["groups"]] == group_labels
    for group in entity["groups"]:
        assert len(group["committee_ids"]) == 1
        assert group["family_id"] is None
    flattened = sorted(cid for group in entity["groups"] for cid in group["committee_ids"])
    assert flattened == committee_ids


@pytest.mark.parametrize(
    "kind, key",
    [
        ("candidate", "999"),            # no such candidate
        ("candidate", 999),
        ("candidate", "not-a-number"),   # must be None, not a ValueError from int()
        ("candidate", RACE_R_KEY),       # a race slug is not a candidate id
        ("candidate", ""),
        ("race", "20"),                  # a candidate id is not a race slug
        ("committee", "100"),            # not an entity kind
        ("", "20"),
    ],
)
def test_unknown_entity_returns_none(conn, kind, key):
    """Kills: an exception instead of None, a lookup that ignores the kind, a candidate id accepted as a race."""
    assert resolve_entity(conn, kind, key, today=TODAY) is None


# ===========================================================================
# resolve_entity -- races
# ===========================================================================

@pytest.mark.parametrize("today", [date(2026, 10, 26), date(2025, 3, 1)], ids=["even-year", "odd-year-folds-forward"])
def test_race_entity_default_cycle_comes_from_today(conn, today):
    """today 2026-10-26 is cycle 2026; 2025-03-01 folds forward to 2026 (the race pages' rule).
    2026 rows: Alice (100, 101, 150), Bryn (200, 150), Fay (800, election_year 2025 = cycle 2026).
    Kills: filtering on election_year (Fay vanishes), no cycle filter (the 2024 row 400 leaks in),
    a cycle taken from the wall clock, an odd year not folded."""
    entity = resolve_entity(conn, "race", RACE_R_KEY, today=today)
    assert entity is not None
    _assert_keys(entity, RESOLVED_ENTITY_KEYS)
    assert entity["kind"] == "race"
    assert entity["key"] == RACE_R_KEY
    assert entity["label"] == RACE_R_LABEL
    assert entity["cycle"] == 2026
    assert entity["committee_ids"] == [100, 101, 150, 200, 800]
    assert sorted(entity["candidate_ids"]) == [CAND_B, CAND_A, CAND_F]
    assert len(entity["candidate_ids"]) == 3


def test_race_entity_groups_are_one_per_candidate_sorted_by_label(conn):
    """Labels 'Alice Aldous' (id 20) < 'Bryn Bellamy' (id 10) < 'Fay Fontaine' (id 60): label order differs from
    candidate-id order, from committee order and from insertion order.  Family expansion does NOT apply to a
    race: 102 (accepted family of 101) is not a committee of the race.
    Kills: groups sorted by candidate id, one group per committee, family members added, extra keys."""
    entity = resolve_entity(conn, "race", RACE_R_KEY, today=TODAY)
    groups = entity["groups"]
    assert [group["label"] for group in groups] == ["Alice Aldous", "Bryn Bellamy", "Fay Fontaine"]
    for group in groups:
        _assert_keys(group, RESOLVED_GROUP_KEYS)
        assert group["family_id"] is None
    assert [group["candidate_id"] for group in groups] == [CAND_A, CAND_B, CAND_F]
    assert sorted(groups[0]["committee_ids"]) == [100, 101, 150]
    assert sorted(groups[1]["committee_ids"]) == [150, 200]
    assert sorted(groups[2]["committee_ids"]) == [800]
    assert 102 not in entity["committee_ids"]
    assert 109 not in entity["committee_ids"]
    assert 108 not in entity["committee_ids"]


def test_race_entity_slate_committee_is_in_both_groups_and_once_in_the_committee_list(conn):
    """150 is linked to Alice AND Bryn: in both groups, exactly once in committee_ids.
    Kills: de-duplicating the slate away from one group, a committee list with a duplicate."""
    entity = resolve_entity(conn, "race", RACE_R_KEY, today=TODAY)
    by_label = {group["label"]: group for group in entity["groups"]}
    assert 150 in by_label["Alice Aldous"]["committee_ids"]
    assert 150 in by_label["Bryn Bellamy"]["committee_ids"]
    assert entity["committee_ids"].count(150) == 1
    assert len(entity["committee_ids"]) == len(set(entity["committee_ids"]))


def test_race_entity_for_an_explicit_cycle_ignores_today(conn):
    """Cycle 2024 has Alice (100, 101, 150) and Dana (400); Dana's candidacy spells the race 'MEMBER,  BOARD OF
    EDUCATION' / 'board of education district' / '1b' -- the same slug, so she is in the same race.
    Kills: raw-string comparison of office / district (Dana vanishes), the explicit cycle ignored in favour of
    today (the 2026 rows come back), 200 / 800 leaking in from 2026."""
    entity = resolve_entity(conn, "race", RACE_R_KEY, election_cycle=2024, today=TODAY)
    assert entity is not None
    assert entity["cycle"] == 2024
    assert entity["committee_ids"] == [100, 101, 150, 400]
    assert [group["label"] for group in entity["groups"]] == ["Alice Aldous", "Dana Dalton"]
    assert sorted(entity["candidate_ids"]) == [CAND_A, CAND_D]
    by_label = {group["label"]: group for group in entity["groups"]}
    assert sorted(by_label["Dana Dalton"]["committee_ids"]) == [400]
    # whatever spelling names the race, its tokens are the race key
    assert race_key(entity["label"]) == RACE_R_KEY


def test_race_entity_default_cycle_follows_a_2024_today(conn):
    """today in 2024 selects cycle 2024 without an explicit argument.  Kills: a hard-coded 2026 default."""
    entity = resolve_entity(conn, "race", RACE_R_KEY, today=date(2024, 6, 1))
    assert entity["cycle"] == 2024
    assert entity["committee_ids"] == [100, 101, 150, 400]


def test_other_race_is_resolved_on_its_own_slug(conn):
    """Race S (State Senator, Senate District 12) = candidate 30 / committee 300 only.
    Kills: a slug filter that is ignored (every 2026 candidacy returned)."""
    entity = resolve_entity(conn, "race", RACE_S_KEY, today=TODAY)
    assert entity["committee_ids"] == [300]
    assert [group["label"] for group in entity["groups"]] == ["Carlos Cortez"]
    assert entity["candidate_ids"] == [CAND_C]
    assert entity["cycle"] == 2026


@pytest.mark.parametrize(
    "key, election_cycle, today",
    [
        (RACE_R_KEY, 2022, TODAY),                     # the race exists, but not in 2022
        (RACE_R_KEY, None, date(2022, 6, 1)),          # default cycle 2022 by `today`
        (RACE_S_KEY, 2024, TODAY),                     # race S has no 2024 candidacy
        ("no-such-office-no-such-district-9", None, TODAY),
        ("", None, TODAY),
    ],
    ids=["empty-cycle", "empty-default-cycle", "other-race-empty-cycle", "unknown-slug", "empty-slug"],
)
def test_race_without_rows_returns_none(conn, key, election_cycle, today):
    """Kills: an entity with empty committee lists instead of None (the routes 404 on None)."""
    assert resolve_entity(conn, "race", key, election_cycle=election_cycle, today=today) is None


# ===========================================================================
# entity_donor_rows
# ===========================================================================

DONOR_ROW_KEYS = {
    "donor_key", "donor_name", "donor_class", "committee_id", "committee_name", "total_amount",
    "contribution_count", "city", "state", "zip5", "occupation", "employer", "redacted", "first_date", "last_date",
}

# Committee 100, window 2026, in the contract's order (total desc, donor_name asc, committee_id asc):
# (donor, class, total, count, first_date, last_date).  Excluded and therefore absent: Kay's 0.00 and -75.00 rows,
# her 4A 600; Ivy's archived 9,999 and her 2025-12-31 / 2027-01-01 rows; Acme's 2A 3,000 and 5A 700 / 333;
# Jon's 3A 4,000.  Three rows tie at 100.00 (Ada < Bo < Rae by name).
COMMITTEE_100_ROWS_2026 = [
    (WES, "individual", 6761.00, 1, "2026-01-12", "2026-01-12"),
    (YUL, "individual", 6759.00, 1, "2026-01-13", "2026-01-13"),
    (JON, "individual", 2000.00, 1, "2026-02-15", "2026-02-15"),
    (IVY, "individual", 1500.00, 2, "2026-01-01", "2026-03-10"),   # 1,000 (on the lower bound) + 500
    (ACME, "organization", 1250.00, 1, "2026-05-05", "2026-05-05"),
    (NED, "individual", 400.00, 1, "2026-08-01", "2026-08-01"),
    (LEO, "individual", 300.00, 1, "2026-12-31", "2026-12-31"),    # exactly on the upper bound
    (KAY, "individual", 250.00, 1, "2026-04-05", "2026-04-05"),
    (ADA, "individual", 100.00, 1, "2026-06-08", "2026-06-08"),
    (BO, "individual", 100.00, 1, "2026-06-07", "2026-06-07"),
    (RAE, "individual", 100.00, 1, "2026-06-06", "2026-06-06"),
    (OPAL, "individual", 50.00, 1, "2026-07-07", "2026-07-07"),
    (EVA, "individual", 25.00, 1, "2026-07-08", "2026-07-08"),
]  # 13 rows, total 19,595.00 (14 contributions)


def test_donor_rows_for_committee_100_match_the_hand_computed_table(conn):
    """The whole committee-100 table, value by value and in order.
    Kills: archived / zero / negative / 2A / 3A / 4A / 5A rows counted; the window ignored or exclusive;
    rows not grouped per donor (Ivy would be two rows); a count of rows instead of contributions; wrong order."""
    rows = entity_donor_rows(conn, [100], **WINDOW)
    assert len(rows) == len(COMMITTEE_100_ROWS_2026)
    for row, (donor, donor_class, total, count, first, last) in zip(rows, COMMITTEE_100_ROWS_2026):
        assert row["donor_name"] == _donor_name(donor)
        assert row["donor_class"] == donor_class
        assert row["committee_id"] == 100
        assert row["committee_name"] == "Friends of Alice Aldous"
        assert row["total_amount"] == _near(total)
        assert row["contribution_count"] == count
        assert row["first_date"] == date.fromisoformat(first)
        assert row["last_date"] == date.fromisoformat(last)
    assert sum(row["total_amount"] for row in rows) == _near(19595.00)
    assert sum(row["contribution_count"] for row in rows) == 14


def test_donor_row_has_exactly_the_contract_keys_and_types(conn):
    """Kills: extra or missing columns; dates returned as strings or datetimes; counts as floats."""
    rows = entity_donor_rows(conn, [100], **WINDOW)
    for row in rows:
        _assert_keys(row, DONOR_ROW_KEYS)
    first = rows[0]
    assert isinstance(first["total_amount"], float)
    assert type(first["contribution_count"]) is int
    assert type(first["committee_id"]) is int
    assert type(first["first_date"]) is date
    assert type(first["last_date"]) is date
    assert first["redacted"] is False


def test_donor_key_equals_the_site_donor_key_formula(conn):
    """donor_key = LOWER(TRIM(first|last|address1|address2|city|state|zip)) with NULL as '' -- the key the
    /donors/key/<key> page uses.  The expected key is built in Python from the fixture's field values (Jon has
    an address2, Kay a lower-case state and a ZIP+4, the organizations an empty first name, Rae a redacted address).
    Kills: a hash or id instead of the key, a different delimiter, a key without address2 or zip, no lower-casing."""
    rows = entity_donor_rows(conn, [100], **WINDOW)
    by_name = {row["donor_name"]: row["donor_key"] for row in rows}
    for donor in (IVY, JON, KAY, LEO, ACME, RAE, EVA, OPAL):
        assert by_name[_donor_name(donor)] == _donor_key(donor)
    assert by_name["Jon Jarvis"] == "jon|jarvis|34 elm st|apt 2|springfield|il|62701"
    assert by_name["Acme Union Local 5"] == "|acme union local 5|1 union hall rd||chicago|il|60601"
    assert by_name["Kay Keller"] == "kay|keller|56 pine rd||naperville|il|60540-1234"


@pytest.mark.parametrize(
    "donor, committee_id, donor_class, state, zip5",
    [
        (IVY, 100, "individual", "IL", "60601"),
        (KAY, 100, "individual", "IL", "60540"),    # stored 'il' and '60540-1234' -> 'IL', first five digits
        (LEO, 100, "individual", "WI", "53703"),
        (OPAL, 100, "individual", None, None),      # NULL state / zip
        (EVA, 100, "individual", None, None),       # '' state / zip -> None, not ''
        (ACME, 100, "organization", "IL", "60601"),  # NULL first name
        (BCO, 200, "organization", "IL", "62701"),  # '' first name
        (PAC2, 101, "organization", "IL", "62701"),
        (PIA, 101, "individual", "IL", "60540"),
        (SAL, 101, "individual", "MO", "65802"),
    ],
    ids=["ivy", "kay-lowercase-state-zip4", "leo-wi", "opal-null", "eva-blank", "acme-null-first", "bco-empty-first",
         "pac2", "pia", "sal-missouri"],
)
def test_donor_class_state_and_zip5_derivations(conn, donor, committee_id, donor_class, state, zip5):
    """individual <=> a first name is filed (NULL and '' both mean organization); state upper-cased 2 letters or
    None; zip5 = first five digits or None.
    Kills: class guessed from the name text, `first_name IS NULL` only (BCO has ''), `first_name <> ''` only
    (ACME has NULL), a state/zip passed through raw, '' returned instead of None."""
    rows = entity_donor_rows(conn, [100, 101, 200], **WINDOW)
    matching = [r for r in rows if r["donor_key"] == _donor_key(donor) and r["committee_id"] == committee_id]
    assert len(matching) == 1
    row = matching[0]
    assert row["donor_class"] == donor_class
    assert row["state"] == state
    assert row["zip5"] == zip5
    assert row["redacted"] is False


@pytest.mark.parametrize(
    "donor",
    [RAE, BO, ADA],
    ids=["rae-flag-and-address-marker", "bo-flag-only-address-kept", "ada-address-marker-only"],
)
def test_redacted_donor_row_keeps_amounts_but_hides_the_address(conn, donor):
    """redacted <=> redaction_requested = TRUE OR address1 = 'Redaction Requested'.  Bo's row still carries a real
    address and Ada's flag is FALSE with a real-looking city / state / zip; both must come back blank anyway.
    Kills: testing only the flag, testing only the address marker, trusting NULL columns, dropping the row
    (its $100 still counts), losing the donor name."""
    rows = entity_donor_rows(conn, [100], **WINDOW)
    matching = [r for r in rows if r["donor_key"] == _donor_key(donor)]
    assert len(matching) == 1
    row = matching[0]
    assert row["redacted"] is True
    assert row["city"] is None
    assert row["state"] is None
    assert row["zip5"] is None
    assert row["total_amount"] == _near(100.00)
    assert row["contribution_count"] == 1
    assert row["donor_name"] == _donor_name(donor)
    assert row["donor_class"] == "individual"


def test_donor_rows_carry_city_occupation_and_employer(conn):
    """Kills: person attributes dropped or taken from another committee's row."""
    rows = entity_donor_rows(conn, [100], **WINDOW)
    ivy = [r for r in rows if r["donor_key"] == _donor_key(IVY)][0]
    assert ivy["city"] == "Chicago"
    assert ivy["occupation"] == "Teacher"
    assert ivy["employer"] == "Lakeview Elementary"
    jon = [r for r in rows if r["donor_key"] == _donor_key(JON)][0]
    assert jon["city"] == "Springfield"
    assert jon["occupation"] == "Engineer"
    acme = [r for r in rows if r["donor_key"] == _donor_key(ACME)][0]
    assert acme["occupation"] is None
    assert acme["employer"] is None


def test_one_row_per_donor_and_committee_with_deterministic_tie_order(conn):
    """Committees [100, 101]: Ned gave 400 to each -> TWO rows (never merged across committees), tied on total and
    name, so committee 100 comes first; Ivy 1,500 at 100 and 250 at 101 stay separate; 20 rows in all.
    Kills: grouping by donor only, a missing committee tie-break, committee_id restriction ignored."""
    rows = entity_donor_rows(conn, [100, 101], **WINDOW)
    expected = [
        ("Vic Vance", 101, 13521.0), ("Wes Whitlock", 100, 6761.0), ("Yul Young", 100, 6759.0),
        ("Jon Jarvis", 100, 2000.0), ("Ivy Ingram", 100, 1500.0), ("Acme Union Local 5", 100, 1250.0),
        ("Prairie Teachers PAC", 101, 600.0), ("Ned Novak", 100, 400.0), ("Ned Novak", 101, 400.0),
        ("Leo Lund", 100, 300.0), ("Ivy Ingram", 101, 250.0), ("Kay Keller", 100, 250.0),
        ("Ada Archer", 100, 100.0), ("Bo Barlow", 100, 100.0), ("Rae Ramsey", 100, 100.0),
        ("Pia Petrov", 101, 80.0), ("Sal Sandoval", 101, 60.0), ("Opal Ortiz", 100, 50.0),
        ("Kay Keller", 101, 40.0), ("Eva Eaton", 100, 25.0),
    ]
    assert len(rows) == len(expected)
    actual = [(row["donor_name"], row["committee_id"], row["total_amount"]) for row in rows]
    assert [(name, committee) for name, committee, _ in actual] == [(name, committee) for name, committee, _ in expected]
    for (_, _, got), (_, _, want) in zip(actual, expected):
        assert got == _near(want)


@pytest.mark.parametrize(
    "committee_ids, expected_donors",
    [
        ([200], ["Zoe Zeller", "Mia Moreno", "Ivy Ingram", "Bright Co", "Kay Keller"]),  # 13,519 / 2,500 / 700 / 200 / 150
        ([150], ["Sloan Sterling"]),    # the slate: 100 + 250 -> one row of 350 (the 5A in-kind row is not a contribution)
        ([102], ["Zed Zimmer", "Ivy Ingram", "Kay Keller"]),  # 150 / 90 / 10
        ([800], []),                    # an entity committee with no receipts
        ([999999], []),                 # a committee that does not exist
        ([], []),                       # nothing asked, nothing returned (and no SQL error on an empty ANY list)
    ],
    ids=["200", "slate-150", "family-member-102", "no-receipts", "unknown", "empty-list"],
)
def test_donor_rows_are_restricted_to_the_requested_committees(conn, committee_ids, expected_donors):
    """Kills: a committee filter that is ignored (all 135 receipts come back), an exception on []."""
    rows = entity_donor_rows(conn, committee_ids, **WINDOW)
    assert [row["donor_name"] for row in rows] == expected_donors
    for row in rows:
        assert row["committee_id"] in committee_ids


# Ivy at committee 100 has base rows on 2025-12-31 (800), 2026-01-01 (1,000), 2026-03-10 (500), 2027-01-01 (900);
# the 9,999 row on 2026-03-11 is archived.
@pytest.mark.parametrize(
    "date_from, date_to, total, count, first, last",
    [
        (None, None, 3200.00, 4, "2025-12-31", "2027-01-01"),                                   # unbounded both ways
        (date(2026, 1, 1), date(2026, 12, 31), 1500.00, 2, "2026-01-01", "2026-03-10"),        # lower bound ON a row
        (date(2025, 12, 31), date(2026, 1, 1), 1800.00, 2, "2025-12-31", "2026-01-01"),        # both bounds ON rows
        (date(2026, 1, 2), None, 1400.00, 2, "2026-03-10", "2027-01-01"),                      # open upper bound
        (None, date(2026, 3, 9), 1800.00, 2, "2025-12-31", "2026-01-01"),                      # open lower bound
        (date(2026, 3, 10), date(2026, 3, 10), 500.00, 1, "2026-03-10", "2026-03-10"),         # one day
    ],
    ids=["unbounded", "year-2026", "both-bounds-on-rows", "open-upper", "open-lower", "single-day"],
)
def test_donor_row_window_is_inclusive_and_open_ended_when_none(conn, date_from, date_to, total, count, first, last):
    """Kills: exclusive bounds, None treated as 'no rows' or as the current cycle, the window applied to the
    wrong column, an off-by-one-day bound, the archived 9,999 counted."""
    rows = entity_donor_rows(conn, [100], date_from=date_from, date_to=date_to)
    ivy = [r for r in rows if r["donor_key"] == _donor_key(IVY)]
    assert len(ivy) == 1
    assert ivy[0]["total_amount"] == _near(total)
    assert ivy[0]["contribution_count"] == count
    assert ivy[0]["first_date"] == date.fromisoformat(first)
    assert ivy[0]["last_date"] == date.fromisoformat(last)


def test_donor_rows_window_without_rows_returns_an_empty_list(conn):
    """Kills: a window that falls back to all time when it matches nothing."""
    assert entity_donor_rows(conn, [100, 101, 200], date_from=date(2030, 1, 1), date_to=date(2030, 12, 31)) == []


# ===========================================================================
# get_entity_donors
# ===========================================================================

METRIC_KEYS = {
    "total_amount", "donor_count", "contribution_count", "top1_share", "top5_share", "top10_share", "hhi", "gini",
    "individual_amount", "organization_amount", "in_kind_total",
}
GROUP_METRIC_KEYS = METRIC_KEYS | {"label", "committee_ids", "candidate_id", "family_id"}
TOP_DONOR_KEYS = {
    "donor_key", "donor_name", "donor_class", "total_amount", "contribution_count", "committee_count", "city", "state",
    "redacted", "groups",
}
SHARED_DONOR_KEYS = {"donor_key", "donor_name", "total_amount", "group_count", "groups"}

# Group "Alice Aldous" = committees 100 + 101 + 150, window 2026, donors de-duplicated across the three committees.
# Per-donor totals (18 donors): Vic 13,521 | Wes 6,761 | Yul 6,759 | Jon 2,000 | Ivy 1,750 (1,500 at 100 + 250 at 101) |
# Acme 1,250 | Ned 800 (400 + 400) | Prairie Teachers PAC 600 | Sloan 350 | Leo 300 | Kay 290 (250 + 40) |
# Rae, Bo, Ada 100 each | Pia 80 | Sal 60 | Opal 50 | Eva 25.  Sum = 34,896.
#   top1  = 13,521 / 34,896                              = 0.3875
#   top5  = (13,521 + 6,761 + 6,759 + 2,000 + 1,750) / 34,896 = 30,791 / 34,896 = 0.8824
#   top10 = top5 + (1,250 + 800 + 600 + 350 + 300) = 34,091 / 34,896       = 0.9769
#   individual = 34,896 - (Acme 1,250 + Prairie Teachers PAC 600) = 33,046; organization = 1,850
#   contributions: 14 at 100 + 7 at 101 + 2 at 150 = 23; in-kind: 700 at 100 + 120 at 150 = 820 (333 is 2025)
#   HHI / Gini: the get_donor_concentration formulas over those 18 totals (cross-checked below).
ALICE_DONOR_TOTALS = [13521, 6761, 6759, 2000, 1750, 1250, 800, 600, 350, 300, 290, 100, 100, 100, 80, 60, 50, 25]
ALICE_METRICS = {
    "total_amount": 34896.0, "donor_count": 18, "contribution_count": 23,
    "top1_share": 0.3875, "top5_share": 0.8824, "top10_share": 0.9769, "hhi": 2333.66, "gini": 0.7414,
    "individual_amount": 33046.0, "organization_amount": 1850.0, "in_kind_total": 820.0,
}
# Group "Bryn Bellamy" = committees 150 + 200: Zoe 13,519 | Mia 2,500 (1,500 + 1,000) | Ivy 700 | Sloan 350 (slate) |
# Bright Co 200 | Kay 150 = 17,419.   top1 = 13,519 / 17,419 = 0.7761;  top5 = 17,269 / 17,419 = 0.9914.
# contributions: 2 at 150 + 6 at 200 = 8; in-kind: 120 (the slate's) -- the same slate row Alice's group also counts.
BRYN_DONOR_TOTALS = [13519, 2500, 700, 350, 200, 150]
BRYN_METRICS = {
    "total_amount": 17419.0, "donor_count": 6, "contribution_count": 8,
    "top1_share": 0.7761, "top5_share": 0.9914, "top10_share": 1.0, "hhi": 6251.64, "gini": 0.7089,
    "individual_amount": 17219.0, "organization_amount": 200.0, "in_kind_total": 120.0,
}
# Whole race entity: 21 donors (Sloan, Ivy and Kay are in both groups but counted once), 29 contributions
# (23 + 8 - the slate's 2), total 51,965 = 34,896 + 17,419 - 350 (the slate's Sloan money counted once).
RACE_TOTALS = {
    "total_amount": 51965.0, "donor_count": 21, "contribution_count": 29,
    "top1_share": 0.2602, "top5_share": 0.8286, "top10_share": 0.9653, "hhi": 1763.76, "gini": 0.7234,
    "individual_amount": 49915.0, "organization_amount": 2050.0, "in_kind_total": 820.0,
}


def _assert_metrics(actual, expected):
    assert actual["total_amount"] == _near(expected["total_amount"])
    assert actual["donor_count"] == expected["donor_count"]
    assert actual["contribution_count"] == expected["contribution_count"]
    assert actual["top1_share"] == _near(expected["top1_share"], 0.0001)
    assert actual["top5_share"] == _near(expected["top5_share"], 0.0001)
    assert actual["top10_share"] == _near(expected["top10_share"], 0.0001)
    assert actual["hhi"] == _near(expected["hhi"], 0.011)
    assert actual["gini"] == _near(expected["gini"], 0.0001)
    assert actual["individual_amount"] == _near(expected["individual_amount"])
    assert actual["organization_amount"] == _near(expected["organization_amount"])
    assert actual["in_kind_total"] == _near(expected["in_kind_total"])


def _donors(conn, entity=RACE_ENTITY, **kwargs):
    options = dict(WINDOW)
    options.update(kwargs)
    return get_entity_donors(conn, entity, **options)


def test_donor_payload_has_exactly_the_contract_keys(conn):
    """Kills: extra or missing top-level / group / donor keys."""
    result = _donors(conn)
    _assert_keys(result, {"groups", "totals", "class_breakdown", "top_donors", "shared_donors"})
    for group in result["groups"]:
        _assert_keys(group, GROUP_METRIC_KEYS)
    assert METRIC_KEYS <= set(result["totals"].keys())
    for donor in result["top_donors"]:
        _assert_keys(donor, TOP_DONOR_KEYS)
    for donor in result["shared_donors"]:
        _assert_keys(donor, SHARED_DONOR_KEYS)


def test_groups_come_back_in_entity_order_with_their_identity_fields(conn):
    """Kills: groups re-sorted by amount, labels or committee lists lost, one entry per committee."""
    groups = _donors(conn)["groups"]
    assert [group["label"] for group in groups] == ["Alice Aldous", "Bryn Bellamy", "Fay Fontaine"]
    assert [sorted(group["committee_ids"]) for group in groups] == [[100, 101, 150], [150, 200], [800]]
    assert [group["candidate_id"] for group in groups] == [CAND_A, CAND_B, CAND_F]
    assert [group["family_id"] for group in groups] == [None, None, None]


def test_group_metrics_for_alice_are_computed_over_her_committees_with_donors_deduplicated(conn):
    """Ivy, Kay and Ned each gave to two of Alice's committees: one donor each (18 donors, not 21).
    Kills: counting rows instead of donors, summing per committee, using the entity's committees for every
    group, archived / zero / negative / 2A-4A rows in the totals, the window ignored, 5A counted as money."""
    alice = _donors(conn)["groups"][0]
    _assert_metrics(alice, ALICE_METRICS)


def test_group_metrics_for_bryn_are_computed_over_her_committees(conn):
    """Kills: metrics computed once for the whole entity and copied to every group."""
    bryn = _donors(conn)["groups"][1]
    _assert_metrics(bryn, BRYN_METRICS)


@pytest.mark.parametrize(
    "group_index, donor_totals",
    [(0, ALICE_DONOR_TOTALS), (1, BRYN_DONOR_TOTALS)],
    ids=["alice", "bryn"],
)
def test_group_metrics_equal_the_global_donor_concentration_formulas(conn, group_index, donor_totals):
    """Feed the same per-donor totals to the site's get_donor_concentration and demand the same shares / HHI / Gini
    (rounded the same way).  Kills: HHI on a 0-1 scale, a Gini variant, shares over the wrong denominator,
    top-k shares over rows instead of donors."""
    reference_rows = [
        {"committee_id": 1, "committee_name": "reference", "donor_key": f"donor-{index}", "total_amount": float(amount)}
        for index, amount in enumerate(donor_totals)
    ]
    reference = get_donor_concentration(conn, donor_committee_rows=reference_rows)[0]
    group = _donors(conn)["groups"][group_index]
    assert group["total_amount"] == _near(reference["total_amount"])
    assert group["donor_count"] == reference["donor_count"]
    assert group["top1_share"] == _near(reference["top1_share"], 0.0001)
    assert group["top5_share"] == _near(reference["top5_share"], 0.0001)
    assert group["top10_share"] == _near(reference["top10_share"], 0.0001)
    assert group["hhi"] == _near(reference["hhi"], 0.011)
    assert group["gini"] == _near(reference["gini"], 0.0001)
    assert group["gini"] == _near(_gini([float(amount) for amount in donor_totals]), 0.0001)


def test_group_without_receipts_reports_zeros_and_does_not_divide_by_zero(conn):
    """Fay's committee 800 has no receipts.  Kills: ZeroDivisionError / NaN / the group silently dropped."""
    groups = _donors(conn)["groups"]
    fay = [group for group in groups if group["label"] == "Fay Fontaine"]
    assert len(fay) == 1
    assert fay[0]["total_amount"] == 0.0
    assert fay[0]["donor_count"] == 0
    assert fay[0]["contribution_count"] == 0
    assert fay[0]["in_kind_total"] == 0.0


def test_entity_totals_deduplicate_donors_across_committees_and_groups(conn):
    """Totals over the whole race: 21 donors although the groups list 18 + 6 + 0; Sloan's slate money once.
    Kills: summing the group figures, counting a donor once per committee, double-counting the slate."""
    totals = _donors(conn)["totals"]
    _assert_metrics(totals, RACE_TOTALS)


def test_slate_committee_is_counted_in_both_groups_but_only_once_in_the_totals(conn):
    """The slate 150 (Sloan 350 over 2 contributions, in-kind 120) sits in Alice's AND Bryn's group.
    group sums minus the totals must equal exactly the slate's figures.
    Kills: de-duplicating the slate out of one group, summing both groups into the totals."""
    result = _donors(conn)
    alice, bryn = result["groups"][0], result["groups"][1]
    totals = result["totals"]
    assert alice["total_amount"] + bryn["total_amount"] - totals["total_amount"] == _near(350.0)
    assert alice["contribution_count"] + bryn["contribution_count"] - totals["contribution_count"] == 2
    assert alice["in_kind_total"] + bryn["in_kind_total"] - totals["in_kind_total"] == _near(120.0)


def test_in_kind_total_is_5a_only_inside_the_window_and_never_money(conn):
    """5A rows: 100 -> 700 (2026-05-06) and 333 (2025-05-06, outside), 150 -> 120.  Alice 820, Bryn 120, Fay 0.
    Kills: in-kind rows counted as donor money, the 2025 in-kind row counted, 2A/3A/4A mistaken for in-kind."""
    result = _donors(conn)
    assert [group["in_kind_total"] for group in result["groups"]] == [_near(820.0), _near(120.0), 0.0]
    assert result["totals"]["in_kind_total"] == _near(820.0)
    # Acme gave 1,250 as a contribution; the 700 in-kind row is not part of it.
    acme = [d for d in result["top_donors"] if d["donor_name"] == "Acme Union Local 5"][0]
    assert acme["total_amount"] == _near(1250.0)


def test_class_breakdown_splits_individuals_from_organizations_without_double_counting(conn):
    """Organizations: Acme 1,250 + Prairie Teachers PAC 600 + Bright Co 200 = 2,050 (3 donors);
    individuals 49,915 (18 donors).  Kills: class decided per row, a missing class, donors counted per committee."""
    breakdown = _donors(conn)["class_breakdown"]
    by_class = {entry["donor_class"]: entry for entry in breakdown}
    assert len(breakdown) == 2
    assert set(by_class) == {"individual", "organization"}
    assert by_class["individual"]["total_amount"] == _near(49915.0)
    assert by_class["individual"]["donor_count"] == 18
    assert by_class["organization"]["total_amount"] == _near(2050.0)
    assert by_class["organization"]["donor_count"] == 3
    for entry in breakdown:
        _assert_keys(entry, {"donor_class", "total_amount", "donor_count"})


RACE_TOP_DONORS = [
    # (name, total, contributions, committees, groups)
    ("Vic Vance", 13521.0, 1, 1, ["Alice Aldous"]),
    ("Zoe Zeller", 13519.0, 1, 1, ["Bryn Bellamy"]),
    ("Wes Whitlock", 6761.0, 1, 1, ["Alice Aldous"]),
    ("Yul Young", 6759.0, 1, 1, ["Alice Aldous"]),
    ("Mia Moreno", 2500.0, 2, 1, ["Bryn Bellamy"]),
    ("Ivy Ingram", 2450.0, 4, 3, ["Alice Aldous", "Bryn Bellamy"]),   # 100: 1,500 | 101: 250 | 200: 700
    ("Jon Jarvis", 2000.0, 1, 1, ["Alice Aldous"]),
    ("Acme Union Local 5", 1250.0, 1, 1, ["Alice Aldous"]),
    ("Ned Novak", 800.0, 2, 2, ["Alice Aldous"]),
    ("Prairie Teachers PAC", 600.0, 1, 1, ["Alice Aldous"]),
    ("Kay Keller", 440.0, 3, 3, ["Alice Aldous", "Bryn Bellamy"]),    # 100: 250 | 101: 40 | 200: 150
    ("Sloan Sterling", 350.0, 2, 1, ["Alice Aldous", "Bryn Bellamy"]),  # the slate: both groups, ONE committee
    ("Leo Lund", 300.0, 1, 1, ["Alice Aldous"]),
    ("Bright Co", 200.0, 1, 1, ["Bryn Bellamy"]),
    ("Ada Archer", 100.0, 1, 1, ["Alice Aldous"]),                    # three-way tie at 100: name order
    ("Bo Barlow", 100.0, 1, 1, ["Alice Aldous"]),
    ("Rae Ramsey", 100.0, 1, 1, ["Alice Aldous"]),
    ("Pia Petrov", 80.0, 1, 1, ["Alice Aldous"]),
    ("Sal Sandoval", 60.0, 1, 1, ["Alice Aldous"]),
    ("Opal Ortiz", 50.0, 1, 1, ["Alice Aldous"]),
    ("Eva Eaton", 25.0, 1, 1, ["Alice Aldous"]),
]


def test_top_donors_are_the_whole_entity_ranked_by_amount_then_name(conn):
    """All 21 donors fit under the default cap of 25.  Ranked by entity-wide total (Ivy's three committees add up
    to 2,450 and rank above Jon's single 2,000), ties by name (Ada < Bo < Rae).
    Kills: ranking per committee row, per group, or by contribution count; a missing tie-break."""
    top = _donors(conn)["top_donors"]
    assert len(top) == len(RACE_TOP_DONORS)
    for donor, (name, total, contributions, committees, groups) in zip(top, RACE_TOP_DONORS):
        assert donor["donor_name"] == name
        assert donor["total_amount"] == _near(total)
        assert donor["contribution_count"] == contributions
        assert donor["committee_count"] == committees
        assert sorted(donor["groups"]) == groups
        assert len(donor["groups"]) == len(groups)


def test_top_donor_fields_for_persons_organizations_and_redacted_donors(conn):
    """Kills: class / redaction / location lost on the way up, a redacted donor shown with an address,
    the lower-case state not normalized, an organization labelled as a person."""
    top = {donor["donor_name"]: donor for donor in _donors(conn)["top_donors"]}
    vic = top["Vic Vance"]
    assert vic["donor_key"] == _donor_key(VIC)
    assert vic["donor_class"] == "individual"
    assert vic["city"] == "Chicago"
    assert vic["state"] == "IL"
    assert vic["redacted"] is False
    kay = top["Kay Keller"]
    assert kay["city"] == "Naperville"
    assert kay["state"] == "IL"
    assert top["Leo Lund"]["state"] == "WI"
    acme = top["Acme Union Local 5"]
    assert acme["donor_class"] == "organization"
    assert acme["donor_key"] == _donor_key(ACME)
    for name in ("Rae Ramsey", "Bo Barlow", "Ada Archer"):
        assert top[name]["redacted"] is True
        assert top[name]["city"] is None
        assert top[name]["state"] is None
        assert top[name]["total_amount"] == _near(100.0)
    assert top["Opal Ortiz"]["state"] is None
    assert top["Opal Ortiz"]["redacted"] is False


def test_top_donors_cap_follows_top_n(conn):
    """Kills: a hard-coded 25, top_n applied after the shared-donor / class work only."""
    top = _donors(conn, top_n=3)["top_donors"]
    assert [donor["donor_name"] for donor in top] == ["Vic Vance", "Zoe Zeller", "Wes Whitlock"]


def test_shared_donors_are_those_in_at_least_two_groups_with_per_group_amounts(conn):
    """Race groups Alice / Bryn / Fay.  Shared: Ivy (Alice 1,750 | Bryn 700), Kay (290 | 150) and Sloan, whose only
    committee is the slate that sits in BOTH groups (350 | 350).  total_amount is the donor's entity total
    (Sloan 350, not 700; Ivy 2,450).  Donors in one group only (Vic, Zoe, Mia, ...) are not shared.
    Kills: counting committees instead of groups, summing the per-group amounts into total_amount, listing
    every donor, a missing amount desc order, group amounts that are entity-wide."""
    shared = _donors(conn)["shared_donors"]
    assert [donor["donor_name"] for donor in shared] == ["Ivy Ingram", "Kay Keller", "Sloan Sterling"]
    expected = {
        "Ivy Ingram": (2450.0, {"Alice Aldous": 1750.0, "Bryn Bellamy": 700.0}),
        "Kay Keller": (440.0, {"Alice Aldous": 290.0, "Bryn Bellamy": 150.0}),
        "Sloan Sterling": (350.0, {"Alice Aldous": 350.0, "Bryn Bellamy": 350.0}),
    }
    for donor in shared:
        total, per_group = expected[donor["donor_name"]]
        assert donor["total_amount"] == _near(total)
        assert donor["group_count"] == 2
        assert len(donor["groups"]) == 2
        for entry in donor["groups"]:
            _assert_keys(entry, {"label", "amount"})
        amounts = {entry["label"]: entry["amount"] for entry in donor["groups"]}
        assert set(amounts) == set(per_group)
        for label, amount in per_group.items():
            assert amounts[label] == _near(amount)
    assert shared[0]["donor_key"] == _donor_key(IVY)


def test_shared_donors_are_empty_for_a_single_group_entity(conn):
    """Carlos has one committee, one group: nobody is 'shared'.  Kills: the single group's donors all listed."""
    result = _donors(conn, entity=CORTEZ_ENTITY)
    assert result["shared_donors"] == []
    assert len(result["groups"]) == 1
    # a single group's metrics are the entity's metrics
    assert result["groups"][0]["total_amount"] == _near(result["totals"]["total_amount"])
    assert result["groups"][0]["donor_count"] == result["totals"]["donor_count"]
    assert result["totals"]["total_amount"] == _near(33300.0)   # 20,000 + 9,000 + 3,400 + 400 + 300 + 200
    assert result["totals"]["donor_count"] == 6


def test_shared_donors_cap_follows_top_n(conn):
    """Kills: shared donors left uncapped."""
    shared = _donors(conn, top_n=1)["shared_donors"]
    assert [donor["donor_name"] for donor in shared] == ["Ivy Ingram"]


def test_candidate_entity_with_a_family_group_has_three_groups_and_shares_across_them(conn):
    """Alice as a candidate entity: groups 'Aldous Committees' (101 + 102), 'Citizens ... Slate' (150) and
    'Friends of Alice Aldous' (100).  The family group's metrics span two committees:
      Vic 13,521 | Prairie Teachers PAC 600 | Ned 400 | Ivy 340 (250 + 90) | Zed 150 | Pia 80 | Sal 60 | Kay 50 (40 + 10)
      = 15,201 over 8 donors, 7 + 3 = 10 contributions; top1 = 13,521 / 15,201 = 0.8895.
    Entity totals: 35,146 over 19 donors (Ivy, Kay and Ned are in two groups) and 26 contributions.
    Shared across groups: Ivy (100: 1,500 | family 340), Ned (400 | 400), Kay (250 | 50) -- not Sloan (slate only).
    Kills: the family treated as two groups, donors double-counted in the totals, family_id lost."""
    entity = _candidate_a_entity(conn)
    result = _donors(conn, entity=entity)
    labels = [group["label"] for group in result["groups"]]
    assert labels == ["Aldous Committees", "Citizens for Better Schools Slate", "Friends of Alice Aldous"]
    family, slate, own = result["groups"]
    assert family["family_id"] == entity["groups"][0]["family_id"]
    assert family["family_id"] is not None
    assert sorted(family["committee_ids"]) == [101, 102]
    _assert_metrics(family, {
        "total_amount": 15201.0, "donor_count": 8, "contribution_count": 10, "top1_share": 0.8895,
        "top5_share": 0.9875, "top10_share": 1.0, "hhi": 7940.79, "gini": 0.8071, "individual_amount": 14601.0,
        "organization_amount": 600.0, "in_kind_total": 0.0,
    })
    _assert_metrics(slate, {
        "total_amount": 350.0, "donor_count": 1, "contribution_count": 2, "top1_share": 1.0, "top5_share": 1.0,
        "top10_share": 1.0, "hhi": 10000.0, "gini": 0.0, "individual_amount": 350.0, "organization_amount": 0.0,
        "in_kind_total": 120.0,
    })
    _assert_metrics(own, {
        "total_amount": 19595.0, "donor_count": 13, "contribution_count": 14, "top1_share": 0.345,
        "top5_share": 0.9324, "top10_share": 0.9911, "hhi": 2592.78, "gini": 0.6926, "individual_amount": 18345.0,
        "organization_amount": 1250.0, "in_kind_total": 700.0,
    })
    _assert_metrics(result["totals"], {
        "total_amount": 35146.0, "donor_count": 19, "contribution_count": 26, "top1_share": 0.3847,
        "top5_share": 0.8786, "top10_share": 0.9725, "hhi": 2303.43, "gini": 0.7485, "individual_amount": 33296.0,
        "organization_amount": 1850.0, "in_kind_total": 820.0,
    })
    shared = result["shared_donors"]
    assert [donor["donor_name"] for donor in shared] == ["Ivy Ingram", "Ned Novak", "Kay Keller"]
    expected = {
        "Ivy Ingram": (1840.0, {"Aldous Committees": 340.0, "Friends of Alice Aldous": 1500.0}),
        "Ned Novak": (800.0, {"Aldous Committees": 400.0, "Friends of Alice Aldous": 400.0}),
        "Kay Keller": (300.0, {"Aldous Committees": 50.0, "Friends of Alice Aldous": 250.0}),
    }
    for donor in shared:
        total, per_group = expected[donor["donor_name"]]
        assert donor["total_amount"] == _near(total)
        assert donor["group_count"] == 2
        amounts = {entry["label"]: entry["amount"] for entry in donor["groups"]}
        assert set(amounts) == set(per_group)
        for label, amount in per_group.items():
            assert amounts[label] == _near(amount)


def test_a_narrower_window_changes_every_group_metric(conn):
    """June 2026: Alice's group has Rae / Bo / Ada (100 each, 06-06..08) and Sloan's slate gifts (100 + 250):
    650 over 4 donors and 5 contributions, top1 = 350 / 650 = 0.5385; Bryn's group is Sloan alone (350, 1 donor,
    2 contributions, HHI 10,000).  Kills: the window ignored by the aggregates (any of them)."""
    result = _donors(conn, date_from=date(2026, 6, 1), date_to=date(2026, 6, 30))
    alice, bryn, fay = result["groups"]
    _assert_metrics(alice, {
        "total_amount": 650.0, "donor_count": 4, "contribution_count": 5, "top1_share": 0.5385, "top5_share": 1.0,
        "top10_share": 1.0, "hhi": 3609.47, "gini": 0.2885, "individual_amount": 650.0, "organization_amount": 0.0,
        "in_kind_total": 0.0,
    })
    _assert_metrics(bryn, {
        "total_amount": 350.0, "donor_count": 1, "contribution_count": 2, "top1_share": 1.0, "top5_share": 1.0,
        "top10_share": 1.0, "hhi": 10000.0, "gini": 0.0, "individual_amount": 350.0, "organization_amount": 0.0,
        "in_kind_total": 0.0,
    })
    assert fay["total_amount"] == 0.0
    assert [donor["donor_name"] for donor in result["shared_donors"]] == ["Sloan Sterling"]


def test_a_window_without_receipts_gives_empty_lists_and_zero_totals(conn):
    """Kills: division by zero on an empty window, leftovers from outside the window."""
    result = _donors(conn, date_from=date(2030, 1, 1), date_to=date(2030, 12, 31))
    assert result["top_donors"] == []
    assert result["shared_donors"] == []
    assert result["totals"]["total_amount"] == 0.0
    assert result["totals"]["donor_count"] == 0
    assert result["totals"]["contribution_count"] == 0
    for group in result["groups"]:
        assert group["total_amount"] == 0.0
        assert group["donor_count"] == 0


# ===========================================================================
# get_entity_geography
# ===========================================================================

GEO_STATE_KEYS = {"state", "total_amount", "donor_count", "contribution_count", "share"}
GEO_CITY_KEYS = {"city", "state", "total_amount", "donor_count", "contribution_count", "share"}
GEO_MONTH_KEYS = {"month", "total_amount", "contribution_count", "donor_count"}

# Race entity, window 2026: 51,965 over 21 donors and 29 contributions.
#   unknown location (no state): Opal 50 + Eva 25 ('' state) + the three redacted donors 3 x 100 = 375
#   IL 51,230 (14 donors, 22 contributions) | WI 300 (Leo) | MO 60 (Sal)      51,230 + 300 + 60 + 375 = 51,965
GEO_TOTAL = 51965.0
GEO_STATES_2026 = [("IL", 51230.0, 14, 22), ("WI", 300.0, 1, 1), ("MO", 60.0, 1, 1)]
# Chicago, IL: Ivy 2,450 (4 contributions) + Mia 2,500 (2) + Acme 1,250 + Vic 13,521 + Zoe 13,519 = 33,240, 5 donors, 9
# contributions -- Bo's and Ada's rows say Chicago / IL but are redacted and stay out.
# Springfield, IL: Jon 2,000 + Bright Co 200 + Prairie Teachers PAC 600 = 2,800 (3 donors); Springfield, MO: Sal 60.
# Naperville, IL: Kay 440 (3 contributions, stored 'il') + Pia 80 ('NAPERVILLE') = 520, 2 donors, 4 contributions.
GEO_CITIES_2026 = [
    ("Chicago", "IL", 33240.0, 5, 9),
    ("Peoria", "IL", 6761.0, 1, 1),
    ("Joliet", "IL", 6759.0, 1, 1),
    ("Springfield", "IL", 2800.0, 3, 3),
    ("Evanston", "IL", 800.0, 1, 2),
    ("Naperville", "IL", 520.0, 2, 4),
    ("Rockford", "IL", 350.0, 1, 2),
    ("Madison", "WI", 300.0, 1, 1),
    ("Springfield", "MO", 60.0, 1, 1),
]
# month: (amount, contributions, donors).  November has no receipt at all.
GEO_MONTHS_2026 = [
    ("2026-01", 28041.0, 4, 4),   # Ivy 1,000 + Wes 6,761 + Yul 6,759 + Vic 13,521
    ("2026-02", 16299.0, 4, 4),   # Jon 2,000 + Pia 80 + Zoe 13,519 + Ivy 700
    ("2026-03", 2100.0, 4, 4),    # Ivy 500 + Sal 60 + Kay 40 + Mia 1,500
    ("2026-04", 1250.0, 2, 2),    # Kay 250 + Mia 1,000
    ("2026-05", 1600.0, 3, 3),    # Acme 1,250 + Bright Co 200 + Kay 150
    ("2026-06", 650.0, 5, 4),     # Rae, Bo, Ada 100 each + Sloan 100 + 250 (five contributions, four donors)
    ("2026-07", 75.0, 2, 2),      # Opal 50 + Eva 25
    ("2026-08", 800.0, 2, 1),     # Ned 400 at 100 and 400 at 101 (one donor)
    ("2026-09", 250.0, 1, 1),     # Ivy
    ("2026-10", 600.0, 1, 1),     # Prairie Teachers PAC
    ("2026-11", 0.0, 0, 0),       # zero-filled
    ("2026-12", 300.0, 1, 1),     # Leo, on the upper bound
]


def _geo(conn, entity=RACE_ENTITY, **kwargs):
    options = dict(WINDOW)
    options.update(kwargs)
    return get_entity_geography(conn, entity, **options)


def test_geography_payload_has_exactly_the_contract_keys(conn):
    """Kills: extra or missing keys at any level."""
    result = _geo(conn)
    _assert_keys(result, {"states", "cities", "time_series", "in_state_share", "unknown_amount", "totals"})
    _assert_keys(result["totals"], {"total_amount", "donor_count", "contribution_count"})
    for row in result["states"]:
        _assert_keys(row, GEO_STATE_KEYS)
    for row in result["cities"]:
        _assert_keys(row, GEO_CITY_KEYS)
    for row in result["time_series"]:
        _assert_keys(row, GEO_MONTH_KEYS)


def test_geography_totals_deduplicate_donors_and_slate_money(conn):
    """Kills: counting a donor per committee (Ivy gave to three), the slate counted per group, the window ignored."""
    totals = _geo(conn)["totals"]
    assert totals["total_amount"] == _near(GEO_TOTAL)
    assert totals["donor_count"] == 21
    assert totals["contribution_count"] == 29


def test_geography_states_table(conn):
    """Kay's stored 'il' counts as IL; Sal's Springfield is Missouri; blank / NULL / redacted rows are in no state.
    Order: amount desc.  Kills: raw (case-sensitive) state grouping, the redacted donors in IL, a state row for
    the blanks, unsorted output."""
    states = _geo(conn)["states"]
    assert [row["state"] for row in states] == [state for state, _, _, _ in GEO_STATES_2026]
    for row, (state, amount, donors, contributions) in zip(states, GEO_STATES_2026):
        assert row["total_amount"] == _near(amount)
        assert row["donor_count"] == donors
        assert row["contribution_count"] == contributions
        assert row["share"] == _near(amount / GEO_TOTAL, 0.0001)


def test_geography_cities_table_merges_spellings_and_keeps_same_named_cities_in_different_states_apart(conn):
    """'NAPERVILLE' (Pia) and 'Naperville' (Kay, state 'il') are one city; Springfield IL and Springfield MO are two;
    redacted donors with a city on file (Bo, Ada: Chicago) are not in Chicago.  Order: amount desc.
    Kills: grouping on the raw city text, grouping on city without state, redacted rows in the city table."""
    cities = _geo(conn)["cities"]
    assert [(row["city"], row["state"]) for row in cities] == [(c, s) for c, s, _, _, _ in GEO_CITIES_2026]
    for row, (city, state, amount, donors, contributions) in zip(cities, GEO_CITIES_2026):
        assert row["total_amount"] == _near(amount)
        assert row["donor_count"] == donors
        assert row["contribution_count"] == contributions
        assert row["share"] == _near(amount / GEO_TOTAL, 0.0001)


def test_geography_shares_are_fractions_of_the_entity_total_and_leave_room_for_unknown(conn):
    """State shares sum to (51,230 + 300 + 60) / 51,965 = 0.9928 < 1 because 375 has no location.
    Kills: shares over the located amount only (sum would be exactly 1), shares in percent."""
    result = _geo(conn)
    share_sum = sum(row["share"] for row in result["states"])
    assert share_sum <= 1.0
    assert share_sum == _near(51590.0 / GEO_TOTAL, 0.0005)
    city_amounts = sum(row["total_amount"] for row in result["cities"])
    state_amounts = sum(row["total_amount"] for row in result["states"])
    assert city_amounts == _near(state_amounts)
    assert state_amounts + result["unknown_amount"] == _near(GEO_TOTAL)


def test_geography_unknown_amount_and_in_state_share(conn):
    """unknown_amount = Opal (NULL state) 50 + Eva ('' state) 25 + Rae / Bo / Ada (redacted) 300 = 375;
    in_state_share = IL 51,230 / 51,965 = 0.9859.
    Kills: redacted money assigned to a state, '' treated as a state, in_state_share over the located amount."""
    result = _geo(conn)
    assert result["unknown_amount"] == _near(375.0)
    assert result["in_state_share"] == _near(51230.0 / GEO_TOTAL, 0.0001)


def test_geography_time_series_is_zero_filled_between_first_and_last_contribution(conn):
    """Twelve months for 2026; November has no receipt and is present with zeros.
    Kills: sparse months only, donor_count summed instead of distinct, contributions as rows of the donor table."""
    series = _geo(conn)["time_series"]
    assert [row["month"] for row in series] == [month for month, _, _, _ in GEO_MONTHS_2026]
    for row, (month, amount, contributions, donors) in zip(series, GEO_MONTHS_2026):
        assert row["total_amount"] == _near(amount)
        assert row["contribution_count"] == contributions
        assert row["donor_count"] == donors


def test_geography_time_series_spans_the_contributions_not_the_window_bounds(conn):
    """Window 2025-01-01..2027-12-31 contains a 2025-12-31 gift (800) and a 2027-01-01 gift (900) of Ivy's at 100:
    the series runs 2025-12 .. 2027-01 (14 months), zero-filled in between, and NOT from 2025-01 to 2027-12.
    Kills: filling the whole window, starting the series at the first month of the window."""
    result = _geo(conn, date_from=date(2025, 1, 1), date_to=date(2027, 12, 31))
    series = result["time_series"]
    assert len(series) == 14
    assert series[0]["month"] == "2025-12"
    assert series[0]["total_amount"] == _near(800.0)
    assert series[-1]["month"] == "2027-01"
    assert series[-1]["total_amount"] == _near(900.0)
    november = [row for row in series if row["month"] == "2026-11"][0]
    assert november["total_amount"] == 0.0
    assert november["contribution_count"] == 0
    assert result["totals"]["total_amount"] == _near(GEO_TOTAL + 800.0 + 900.0)
    assert result["totals"]["donor_count"] == 21
    assert result["totals"]["contribution_count"] == 31


def test_geography_follows_a_narrow_window(conn):
    """June..July 2026: months 06 and 07 only (650 and 75); states / cities are recomputed for the window.
    Kills: states and cities computed over all time while the series is windowed."""
    result = _geo(conn, date_from=date(2026, 6, 1), date_to=date(2026, 7, 31))
    assert [row["month"] for row in result["time_series"]] == ["2026-06", "2026-07"]
    assert result["totals"]["total_amount"] == _near(725.0)
    # In June/July only Sloan (Rockford, 350) has a location; Rae, Bo, Ada, Opal and Eva (375) do not.
    assert [row["state"] for row in result["states"]] == ["IL"]
    assert result["states"][0]["total_amount"] == _near(350.0)
    assert [(row["city"], row["state"]) for row in result["cities"]] == [("Rockford", "IL")]
    assert result["unknown_amount"] == _near(375.0)


def test_geography_limits_cap_states_and_cities_but_not_the_totals(conn):
    """limit_states=2 keeps IL and WI (Missouri's 60 is the smallest); limit_cities=3 keeps Chicago, Peoria, Joliet.
    Kills: limits ignored, the smallest rows kept, totals recomputed from the capped lists."""
    result = _geo(conn, limit_states=2, limit_cities=3)
    assert [row["state"] for row in result["states"]] == ["IL", "WI"]
    assert [row["city"] for row in result["cities"]] == ["Chicago", "Peoria", "Joliet"]
    assert result["totals"]["total_amount"] == _near(GEO_TOTAL)
    assert result["totals"]["donor_count"] == 21


def test_geography_for_a_window_without_receipts_is_empty_and_safe(conn):
    """Kills: division by zero on a zero total, leftover rows from outside the window."""
    result = _geo(conn, date_from=date(2030, 1, 1), date_to=date(2030, 12, 31))
    assert result["states"] == []
    assert result["cities"] == []
    assert result["time_series"] == []
    assert result["totals"]["total_amount"] == 0.0
    assert result["totals"]["donor_count"] == 0
    assert result["totals"]["contribution_count"] == 0
    assert result["unknown_amount"] == 0.0
    assert result["in_state_share"] == 0.0


# ===========================================================================
# get_entity_risk
# ===========================================================================

RISK_KEYS = {"flags", "threshold", "reconciliation", "reconciliation_available", "counts"}
FLAG_KEYS = {
    "flag_type", "severity", "committee_id", "committee_name", "group_label", "donor_key", "donor_name",
    "event_date", "month", "value", "baseline", "threshold", "details",
}
FLAG_TYPES = {"large_single_contribution", "monthly_spike", "high_donor_concentration"}

# The race entity's contributions at or above the site-wide threshold 6,760 (window 2026), amount desc:
# (donor, committee, committee name, group label, date, amount, severity).  2 x 6,760 = 13,520, so 13,521 is 'high'
# and 13,519 is 'medium'.  Yul's 6,759 is one dollar short.  Committee 300's 20,000 and 9,000 are not the entity's.
RACE_LARGE_FLAGS = [
    (VIC, 101, "Aldous 2026 Committee", "Alice Aldous", date(2026, 1, 15), 13521.0, "high"),
    (ZOE, 200, "Bryn Bellamy for Board", "Bryn Bellamy", date(2026, 2, 20), 13519.0, "medium"),
    (WES, 100, "Friends of Alice Aldous", "Alice Aldous", date(2026, 1, 12), 6761.0, "medium"),
]


def _risk(conn, entity=RACE_ENTITY, **kwargs):
    options = dict(WINDOW)
    options.update(kwargs)
    return get_entity_risk(conn, entity, **options)


def _flags(result, flag_type):
    return [flag for flag in result["flags"] if flag["flag_type"] == flag_type]


def _assert_large_flags(flags, expected):
    assert len(flags) == len(expected)
    for flag, (donor, committee_id, committee_name, group_label, event_date, amount, severity) in zip(flags, expected):
        assert flag["donor_name"] == _donor_name(donor)
        assert flag["donor_key"] == _donor_key(donor)
        assert flag["committee_id"] == committee_id
        assert flag["committee_name"] == committee_name
        assert flag["group_label"] == group_label
        assert flag["event_date"] == event_date
        assert flag["value"] == _near(amount)
        assert flag["severity"] == severity


def test_risk_payload_has_exactly_the_contract_keys(conn):
    """Kills: extra or missing keys on the payload, the flags, the threshold block or the counts."""
    result = _risk(conn)
    _assert_keys(result, RISK_KEYS)
    _assert_keys(result["threshold"], {"large_single", "source"})
    _assert_keys(result["counts"], {"large_single_contribution", "monthly_spike", "high_donor_concentration",
                                    "reconciliation"})
    assert len(result["flags"]) > 0
    for flag in result["flags"]:
        _assert_keys(flag, FLAG_KEYS)
        assert flag["severity"] in ("high", "medium")
        assert flag["flag_type"] in FLAG_TYPES


def test_site_wide_threshold_is_twice_the_p95_of_every_committee_over_all_time(conn):
    """The fixture's receipt base (archived = FALSE, d2_part LIKE '1%', amount > 0; ALL committees, ALL dates) has
    122 rows; percentile_cont(0.95) = 3,000 + 0.95 x (3,400 - 3,000) = 3,380, so the threshold is 2 x 3,380 = 6,760.
    The independent SQL guard below proves the fixture still says that before the product is asked.
    Kills: a p95 over the entity's own rows only (about 9,2xx), over the window only (the 2024 pivot drops: 4,2xx),
    over archived / 2A / 3A rows (the 50,000 / 40,000 / 4,000 rows shift it), a nearest-rank percentile
    (3,000 -> 6,000, which would flag Yul's 6,759), a hard-coded 5,000."""
    guard = conn.execute(
        "SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY amount) AS p95, COUNT(*) AS n "
        "FROM isbe_condensed_receipts WHERE archived = FALSE AND d2_part LIKE '1%' AND amount > 0"
    ).fetchone()
    assert guard["n"] == N_BASE_ROWS
    assert guard["p95"] == pytest.approx(SITE_P95, abs=0.001)

    result = _risk(conn)
    assert result["threshold"]["large_single"] == _near(SITE_THRESHOLD, 0.01)
    assert result["threshold"]["source"] == "site_wide"


def test_large_flags_are_the_entitys_own_rows_ranked_by_amount_with_severity_at_twice_the_threshold(conn):
    """Vic 13,521 >= 2 x 6,760 -> high; Zoe 13,519 -> medium; Wes 6,761 >= 6,760 -> medium; Yul 6,759 -> no flag.
    Committee 300's 20,000 and 9,000 are above the threshold but not the entity's: absent.
    Kills: flags sliced from a global list, severity from the wrong multiple, `>` instead of `>=` around the
    threshold (covered by the override tests), the archived 9,999 / 2A 3,000 rows flagged."""
    large = _flags(_risk(conn), "large_single_contribution")
    _assert_large_flags(large, RACE_LARGE_FLAGS)
    flagged_names = {flag["donor_name"] for flag in large}
    assert "Gil Grant" not in flagged_names   # committee 300's 20,000
    assert "Hal Hart" not in flagged_names    # committee 300's 9,000


def test_large_flags_use_the_group_labels_of_the_entity_in_use(conn):
    """The same rows seen through the candidate entity: Vic's committee 101 belongs to the family group
    'Aldous Committees', Wes's 100 to 'Friends of Alice Aldous'; Zoe's 200 is not Alice's.
    Kills: a group label taken from the committee name only, or fixed to the race's candidates."""
    entity = _candidate_a_entity(conn)
    large = _flags(_risk(conn, entity=entity), "large_single_contribution")
    expected = [
        (VIC, 101, "Aldous 2026 Committee", "Aldous Committees", date(2026, 1, 15), 13521.0, "high"),
        (WES, 100, "Friends of Alice Aldous", "Friends of Alice Aldous", date(2026, 1, 12), 6761.0, "medium"),
    ]
    _assert_large_flags(large, expected)


def test_threshold_floor_of_5000_binds_when_twice_the_site_p95_is_smaller(conn):
    """Turn committee 300's two pivot rows (3,400 and 3,000) into 100s: the 7th and 8th largest base rows are then
    both 2,400 (committee 606), p95 = 2,400 and 2 x p95 = 4,800 < 5,000.  The threshold is the floor 5,000 and
    'site_wide'; severity now flips at 10,000, so Zoe's 13,519 is high and Yul's 6,759 is flagged.
    Kills: a missing floor (4,800), a floor applied to the override, a hard-coded 6,760."""
    conn.execute("UPDATE isbe_condensed_receipts SET amount = 100 WHERE committee_id = 300 AND amount IN (3400, 3000)")
    guard = conn.execute(
        "SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY amount) AS p95 FROM isbe_condensed_receipts "
        "WHERE archived = FALSE AND d2_part LIKE '1%' AND amount > 0"
    ).fetchone()
    assert guard["p95"] == pytest.approx(2400.0, abs=0.001)

    result = _risk(conn)
    assert result["threshold"]["large_single"] == _near(5000.0)
    assert result["threshold"]["source"] == "site_wide"
    expected = [
        (VIC, 101, "Aldous 2026 Committee", "Alice Aldous", date(2026, 1, 15), 13521.0, "high"),
        (ZOE, 200, "Bryn Bellamy for Board", "Bryn Bellamy", date(2026, 2, 20), 13519.0, "high"),
        (WES, 100, "Friends of Alice Aldous", "Alice Aldous", date(2026, 1, 12), 6761.0, "medium"),
        (YUL, 100, "Friends of Alice Aldous", "Alice Aldous", date(2026, 1, 13), 6759.0, "medium"),
    ]
    _assert_large_flags(_flags(result, "large_single_contribution"), expected)


@pytest.mark.parametrize(
    "override, expected_rows",
    [
        # 2 x 6,760.5 = 13,521.0 exactly: Vic sits ON the severity boundary (high); Wes 6,761 clears 6,760.5.
        (6760.5, [("Vic Vance", "high"), ("Zoe Zeller", "medium"), ("Wes Whitlock", "medium")]),
        # Zoe's 13,519 is exactly the override (inclusive); 2 x 13,519 = 27,038 -> nothing is high.
        (13519.0, [("Vic Vance", "medium"), ("Zoe Zeller", "medium")]),
    ],
    ids=["severity-boundary-exactly-twice", "threshold-boundary-inclusive"],
)
def test_large_threshold_override_replaces_the_site_wide_rule(conn, override, expected_rows):
    """Kills: the override ignored, `>` instead of `>=`, severity computed against the site-wide threshold,
    the source label left as 'site_wide'."""
    result = _risk(conn, large_threshold=override)
    assert result["threshold"]["large_single"] == override
    assert result["threshold"]["source"] == "override"
    large = _flags(result, "large_single_contribution")
    assert [(flag["donor_name"], flag["severity"]) for flag in large] == expected_rows
    assert result["counts"]["large_single_contribution"] == len(expected_rows)


def test_site_threshold_kwarg_is_used_verbatim_as_the_site_wide_rule_without_the_percentile_statement(conn, monkeypatch):
    """Plan N15: the route layer computes the site-wide threshold once per process and passes it in; the builder uses
    it verbatim with source 'site_wide' and does not run its own percentile statement.  Kills: a kwarg that is ignored
    (the disabled helper raises), one relabelled 'override', or one that beats an explicit ``large_threshold``."""
    import database.entity_analytics as module

    expected = _risk(conn)  # the module's own path, before its statement is disabled

    def no_statement(_conn):
        raise AssertionError("_site_large_threshold must not run when site_threshold is given")

    monkeypatch.setattr(module, "_site_large_threshold", no_statement)
    result = _risk(conn, site_threshold=SITE_THRESHOLD)
    assert result == expected
    assert result["threshold"] == {"large_single": SITE_THRESHOLD, "source": "site_wide"}

    higher = _risk(conn, site_threshold=10000.0)
    large = _flags(higher, "large_single_contribution")
    assert [flag["donor_name"] for flag in large] == [_donor_name(VIC), _donor_name(ZOE)]
    assert {flag["severity"] for flag in large} == {"medium"}
    assert _risk(conn, site_threshold=10000.0, large_threshold=6000.0)["threshold"] == {
        "large_single": 6000.0, "source": "override",
    }


def test_large_flags_follow_the_window_but_the_threshold_does_not(conn):
    """Window ending 2026-01-31 keeps Vic (01-15) and Wes (01-12), drops Zoe (02-20); the threshold is still the
    all-time site-wide 6,760.  Kills: flags over all time, a p95 recomputed over the window."""
    result = _risk(conn, date_to=date(2026, 1, 31))
    expected = [
        (VIC, 101, "Aldous 2026 Committee", "Alice Aldous", date(2026, 1, 15), 13521.0, "high"),
        (WES, 100, "Friends of Alice Aldous", "Alice Aldous", date(2026, 1, 12), 6761.0, "medium"),
    ]
    _assert_large_flags(_flags(result, "large_single_contribution"), expected)
    assert result["threshold"]["large_single"] == _near(SITE_THRESHOLD, 0.01)


def test_limit_caps_the_large_flags_and_the_reconciliation_rows_keeping_the_biggest(conn):
    """limit=1: the single largest contribution (Vic) and the single largest |diff| (committee 200, -2,500).
    Kills: limit ignored, the smallest rows kept, limit applied before sorting."""
    result = _risk(conn, limit=1)
    large = _flags(result, "large_single_contribution")
    assert [flag["donor_name"] for flag in large] == ["Vic Vance"]
    assert len(result["reconciliation"]) == 1
    assert result["reconciliation"][0]["committee_id"] == 200


# Spike committees (window 2026; amounts per month; a fresh donor per row):
#   600  600, 600, 600, then April 2,600  -> 2,600 >= 3 x 600 and exceeds it by exactly 2,000   FLAG (delta boundary)
#   601  100, 100, 100, then April 2,099.99 -> ratio 21 but exceeds the average by 1,999.99      no flag
#   602  1,200 x 3, then April 3,599       -> exceeds by 2,399 but ratio 2.999                  no flag
#   603  1,200 x 3, then April 3,600       -> ratio exactly 3.0, exceeds by 2,400               FLAG (ratio boundary)
#   604  January 1,000, February and March EMPTY (zero-filled), April 3,000
#                                          -> trailing average (1,000 + 0 + 0) / 3 = 333.33     FLAG, baseline 333.33
#   605  January 1,000, February 3,000     -> only ONE prior month: its average is 1,000        FLAG, baseline 1,000
#   606  January 7,200 alone               -> the first month is never flagged                  no flag
#   607  first receipts in March (3,000), April 100 -> March is its first month                no flag
#   608  Oct-Dec 2025 (100 each, outside the window) then 2,300 in Jan 2026 -> inside the window January is the
#        committee's first month                                                              no flag
SPIKE_FLAGS = {
    600: ("2026-04", 2600.0, 600.0),
    603: ("2026-04", 3600.0, 1200.0),
    604: ("2026-04", 3000.0, 333.33),
    605: ("2026-02", 3000.0, 1000.0),
}


def test_monthly_spike_rule_boundaries_zero_fill_short_history_and_first_month(conn):
    """Exactly the four committees above are flagged, each once, with value = month sum, baseline = trailing
    average and month = 'YYYY-MM'.
    Kills: delta-only or ratio-only rules; `>` instead of `>=` on either leg; a baseline that skips empty months
    (604 would show 1,000) or always divides by 3 (605 would show 333.33); requiring four months of history; a
    first month that can be flagged (606, 607); a window that is ignored (608); spikes sliced from a global list."""
    result = _risk(conn, entity=SPIKE_ENTITY)
    spikes = _flags(result, "monthly_spike")
    assert sorted(flag["committee_id"] for flag in spikes) == sorted(SPIKE_FLAGS)
    for flag in spikes:
        month, value, baseline = SPIKE_FLAGS[flag["committee_id"]]
        assert flag["month"] == month
        assert flag["value"] == _near(value)
        assert flag["baseline"] == _near(baseline, 0.01)
        assert flag["committee_name"] == f"Spike Test {flag['committee_id']}"
        assert flag["group_label"] == "Spike group"
    assert result["counts"]["monthly_spike"] == 4


def test_spike_entity_has_no_other_kinds_of_flag(conn):
    """The spike committees hold dozens of small donors and nothing near 6,760: no large flag, no concentration flag.
    Kills: a concentration rule that fires on many-donor groups, a threshold that is too low."""
    result = _risk(conn, entity=SPIKE_ENTITY)
    assert _flags(result, "large_single_contribution") == []
    assert _flags(result, "high_donor_concentration") == []
    assert result["counts"]["large_single_contribution"] == 0
    assert result["counts"]["high_donor_concentration"] == 0
    assert result["counts"]["reconciliation"] == 0


# Concentration committees (one group each, 2026-02-10): HHI = sum of squared percentage shares.
#   H3     800 / 100 / 100         -> 6,600, 3 donors     FLAG
#   H2     600 / 400               -> 5,200, 2 donors     no flag (needs >= 3 donors)
#   H4488  580 / 320 / 100         -> 4,488, 3 donors     no flag (just under 4,500; the 580 donor also gave 500
#                                                         in 2025: over all time 1,080 / 320 / 100 = 5,684 would flag)
#   H4542  590 / 310 / 100         -> 4,542, 3 donors     FLAG
#   H4500  30 / 70 / 310 / 590     -> 4,500.00, 4 donors  FLAG (>= is inclusive; 9 + 49 + 961 + 3,481 = 4,500)
CONCENTRATION_FLAGS = {"H3": (6600.0, 3), "H4542": (4542.0, 3), "H4500": (4500.0, 4)}


def test_concentration_flags_need_hhi_of_4500_and_three_donors_per_group(conn):
    """Kills: no donor-count guard (H2), `>` instead of `>=` (H4500), a threshold of 4,000 or 5,000 (H4542 / H4500),
    one HHI for the whole entity instead of one per group, a window that is ignored (H4488).
    value = hhi, baseline = donor count."""
    result = _risk(conn, entity=CONCENTRATION_ENTITY)
    flags = _flags(result, "high_donor_concentration")
    assert sorted(flag["group_label"] for flag in flags) == sorted(CONCENTRATION_FLAGS)
    for flag in flags:
        value, donors = CONCENTRATION_FLAGS[flag["group_label"]]
        assert flag["value"] == _near(value, 0.011)
        assert flag["baseline"] == _near(float(donors))
    assert result["counts"]["high_donor_concentration"] == 3
    assert result["counts"]["monthly_spike"] == 0
    assert result["counts"]["large_single_contribution"] == 0


def test_race_entity_flags_and_counts_are_per_group_and_consistent(conn):
    """Race: only Bryn's group is concentrated (6,251.64 over 6 donors; Alice's is 2,333.66, Fay's has no donors);
    no month spikes (every committee's biggest month is its first); reconciliation has 3 rows.
    Kills: a concentration flag for the entity as a whole or for every group, spurious spikes (the slate, the family
    committee 102), counts that disagree with the lists."""
    result = _risk(conn)
    concentration = _flags(result, "high_donor_concentration")
    assert len(concentration) == 1
    assert concentration[0]["group_label"] == "Bryn Bellamy"
    assert concentration[0]["value"] == _near(6251.64, 0.011)
    assert concentration[0]["baseline"] == _near(6.0)
    assert _flags(result, "monthly_spike") == []
    counts = result["counts"]
    assert counts["large_single_contribution"] == 3
    assert len(_flags(result, "large_single_contribution")) == 3
    assert counts["monthly_spike"] == 0
    assert counts["high_donor_concentration"] == 1
    assert counts["reconciliation"] == 3
    assert len(result["reconciliation"]) == 3


def test_candidate_entity_concentration_is_judged_on_its_family_group(conn):
    """Alice as a candidate: the family group 'Aldous Committees' is dominated by Vic (7,940.79 over 8 donors) ->
    flagged; 'Friends of Alice Aldous' (2,592.78) is not; the slate group has HHI 10,000 but one donor -> not.
    Kills: the donor-count guard missing, groups taken from the race instead of the entity."""
    result = _risk(conn, entity=_candidate_a_entity(conn))
    concentration = _flags(result, "high_donor_concentration")
    assert [flag["group_label"] for flag in concentration] == ["Aldous Committees"]
    assert concentration[0]["value"] == _near(7940.79, 0.011)
    assert concentration[0]["baseline"] == _near(8.0)


def _assert_reconciliation(rows, expected):
    assert len(rows) == len(expected)
    for row, (committee_id, name, label, doc, d2_total, receipts_sum, signed_diff) in zip(rows, expected):
        assert {"committee_id", "committee_name", "group_label", "diff", "receipts_minus_d2_total", "filed_doc_id",
                "d2_total_receipts", "receipts_amount_sum"} <= set(row.keys())
        assert row["committee_id"] == committee_id
        assert row["committee_name"] == name
        assert row["group_label"] == label
        assert row["filed_doc_id"] == doc
        assert row["d2_total_receipts"] == _near(d2_total)
        assert row["receipts_amount_sum"] == _near(receipts_sum)
        assert row["receipts_minus_d2_total"] == _near(signed_diff)
        assert abs(row["diff"]) == _near(abs(signed_diff))


def test_reconciliation_rows_of_the_race_entity_are_abs_diff_of_at_least_1000_over_all_time(conn):
    """Committee 100: +1,000.00 (boundary, inclusive) and +1,500.50 (a 2019 filing: the table is all-time, the
    window must not cut it); committee 200: -2,500.00 (negative counts) while -999.99 and committee 101's +999.00
    stay out; committee 300 (+89,900) and 102 (+3,000) are not in the race.  Order: |diff| desc.
    Kills: signed comparison, `>` instead of `>=`, the window applied, committee_id_sbe filter ignored."""
    result = _risk(conn)
    assert result["reconciliation_available"] is True
    expected = [
        (200, "Bryn Bellamy for Board", "Bryn Bellamy", 9103, 6000.0, 3500.0, -2500.0),
        (100, "Friends of Alice Aldous", "Alice Aldous", 9101, 5000.0, 6500.5, 1500.5),
        (100, "Friends of Alice Aldous", "Alice Aldous", 9100, 10000.0, 11000.0, 1000.0),
    ]
    _assert_reconciliation(result["reconciliation"], expected)


def test_reconciliation_rows_use_the_candidate_entitys_committees_and_group_labels(conn):
    """The accepted-family member 102 (+3,000) is part of Alice-the-candidate and is labelled with its family.
    Kills: reconciliation read for the linked committees only, group labels fixed to the race."""
    result = _risk(conn, entity=_candidate_a_entity(conn))
    expected = [
        (102, "Aldous Victory Fund", "Aldous Committees", 9106, 2000.0, 5000.0, 3000.0),
        (100, "Friends of Alice Aldous", "Friends of Alice Aldous", 9101, 5000.0, 6500.5, 1500.5),
        (100, "Friends of Alice Aldous", "Friends of Alice Aldous", 9100, 10000.0, 11000.0, 1000.0),
    ]
    _assert_reconciliation(result["reconciliation"], expected)


def test_reconciliation_is_unavailable_and_empty_when_the_table_is_missing(conn_no_recon):
    """A fresh database has no bulk_d2_receipts_recon: no exception, an explicit False, an empty list, a zero count,
    and the other flags are still computed.  Kills: a crash on the missing table, True hard-coded."""
    result = _risk(conn_no_recon)
    assert result["reconciliation_available"] is False
    assert result["reconciliation"] == []
    assert result["counts"]["reconciliation"] == 0
    assert result["counts"]["large_single_contribution"] == 3
    _assert_large_flags(_flags(result, "large_single_contribution"), RACE_LARGE_FLAGS)


# ===========================================================================
# get_entity_relationships
# ===========================================================================

REL_KEYS = {"cogiving", "shared_donors", "summary"}
COGIVING_KEYS = {
    "committee_id", "committee_name", "shared_donor_count", "shared_amount_to_entity", "shared_amount_to_committee",
    "top_shared_donors",
}
TOP_SHARED_KEYS = {"donor_key", "donor_name", "amount_to_entity", "amount_to_committee"}

# Race entity (window 2026, 21 donors).  Committees OUTSIDE the race that received contributions from its donors:
#   (committee, name, shared donors, $ to that committee from them, $ the same donors gave the entity)
#   530 Big Overlap PAC      7 donors: Vic 700, Zoe 650, Wes 600, Yul 550, Mia 500, Ivy 450, Jon 400 = 3,850
#                            their entity money 13,521 + 13,519 + 6,761 + 6,759 + 2,500 + 2,450 + 2,000 = 47,510
#   300 Carlos Cortez        3 donors: Jon 400, Ivy 300, Kay 200 = 900     entity money 2,000 + 2,450 + 440 = 4,890
#   510 Labor Alliance PAC   2 donors: Ivy 1,200, Kay 800 = 2,000          entity money 2,450 + 440 = 2,890
#   500 Prairie Educators    2 donors: Ivy 600, Jon 600 = 1,200            entity money 2,450 + 2,000 = 4,450
#                            (NOT Kay's 2025 gift, NOT Ned's archived 2,000, NOT Acme's 2A 800)
#   540 Pair Fund            2 donors: Vic 100, Wes 100 = 200              entity money 13,521 + 6,761 = 20,282
#   102 Aldous Victory Fund  2 donors: Ivy 90, Kay 10 = 100                entity money 2,450 + 440 = 2,890
#                            (a family member of 101, but the RACE entity does not include family members)
#   with min_shared_donors=1 also: 520 Solo Giver PAC (Ned 1,300) and 109 Aldous Legacy Fund (Kay 50).
# Rank: shared_donor_count desc, then $ to the committee desc -- so 510 (2,000) > 500 (1,200) > 540 (200) > 102 (100).
RACE_COGIVING = [
    (530, "Big Overlap PAC", 7, 3850.0, 47510.0),
    (300, "Carlos Cortez for Senate", 3, 900.0, 4890.0),
    (510, "Labor Alliance PAC", 2, 2000.0, 2890.0),
    (500, "Prairie Educators PAC", 2, 1200.0, 4450.0),
    (540, "Pair Fund", 2, 200.0, 20282.0),
    (102, "Aldous Victory Fund", 2, 100.0, 2890.0),
]


def _rel(conn, entity=RACE_ENTITY, **kwargs):
    options = dict(WINDOW)
    options.update(kwargs)
    return get_entity_relationships(conn, entity, **options)


def _assert_cogiving(rows, expected):
    assert [row["committee_id"] for row in rows] == [item[0] for item in expected]
    for row, (committee_id, name, shared, to_committee, to_entity) in zip(rows, expected):
        assert row["committee_name"] == name
        assert row["shared_donor_count"] == shared
        assert row["shared_amount_to_committee"] == _near(to_committee)
        assert row["shared_amount_to_entity"] == _near(to_entity)


def test_relationships_payload_has_exactly_the_contract_keys(conn):
    """Kills: extra or missing keys on the payload, the committee rows, the shared-donor rows or the summary."""
    result = _rel(conn)
    _assert_keys(result, REL_KEYS)
    _assert_keys(result["summary"], {"entity_donor_count", "donor_pool_size", "cogiving_committee_count"})
    assert len(result["cogiving"]) > 0
    for row in result["cogiving"]:
        _assert_keys(row, COGIVING_KEYS)
        for donor in row["top_shared_donors"]:
            _assert_keys(donor, TOP_SHARED_KEYS)


def test_cogiving_committees_are_ranked_by_shared_donors_then_money_and_exclude_the_entity(conn):
    """The hand-computed table above.  The entity's own committees (100, 101, 150, 200, 800) never appear although
    their donors obviously 'share' with themselves.
    Kills: own committees included, ranking by money first (520 and 510 would outrank 300), the count of gifts
    instead of distinct donors, archived / non-contribution / out-of-window gifts counted (500 would have 3-5)."""
    result = _rel(conn)
    _assert_cogiving(result["cogiving"], RACE_COGIVING)
    returned = {row["committee_id"] for row in result["cogiving"]}
    assert returned.isdisjoint({100, 101, 150, 200, 800})


def test_cogiving_for_a_candidate_entity_excludes_its_family_member_but_not_the_race_rival(conn):
    """Alice as a candidate owns 100, 101, 102, 150: 102 is excluded and Bryn's 200 (Ivy 700 + Kay 150 = 850 from two
    of Alice's donors) is an outsider.  The candidate's donors differ (Ivy is 1,840 here, 2,450 in the race).
    Kills: the exclusion list taken from the linked committees only, or from a fixed set instead of the entity."""
    result = _rel(conn, entity=_candidate_a_entity(conn))
    expected = [
        (530, "Big Overlap PAC", 5, 2700.0, 30881.0),     # Vic 700, Wes 600, Yul 550, Jon 400, Ivy 450
        (300, "Carlos Cortez for Senate", 3, 900.0, 4140.0),
        (510, "Labor Alliance PAC", 2, 2000.0, 2140.0),
        (500, "Prairie Educators PAC", 2, 1200.0, 3840.0),
        (200, "Bryn Bellamy for Board", 2, 850.0, 2140.0),
        (540, "Pair Fund", 2, 200.0, 20282.0),
    ]
    _assert_cogiving(result["cogiving"], expected)
    assert result["summary"]["entity_donor_count"] == 19


@pytest.mark.parametrize(
    "min_shared, expected_ids",
    [
        (1, [530, 300, 510, 500, 540, 102, 520, 109]),   # one-donor committees follow, 520 (1,300) before 109 (50)
        (2, [530, 300, 510, 500, 540, 102]),
        (3, [530, 300]),
        (7, [530]),
        (8, []),
    ],
    ids=["min1", "min2-default", "min3", "min7-boundary", "min8-none"],
)
def test_min_shared_donors_is_an_inclusive_floor(conn, min_shared, expected_ids):
    """Kills: the floor ignored, `>` instead of `>=` (min7 would be empty), ranking by money among the one-donor
    committees wrongly placed above the multi-donor ones."""
    result = _rel(conn, min_shared_donors=min_shared)
    assert [row["committee_id"] for row in result["cogiving"]] == expected_ids


def test_top_shared_donors_are_capped_at_five_and_carry_both_amounts(conn):
    """Committee 530 shares seven donors; the five named are the biggest by BOTH measures (their entity money
    and their gift to 530 rank identically): Vic, Zoe, Wes, Yul, Mia.  Committee 300's three donors are all named
    (as a set: Ivy outranks Jon on entity money, Jon outranks Ivy on the gift to 300).
    Kills: no cap, a cap of 3 or 10, a missing amount, amounts swapped, a donor listed that did not give."""
    cogiving = {row["committee_id"]: row for row in _rel(conn)["cogiving"]}
    top = cogiving[530]["top_shared_donors"]
    assert len(top) == 5
    expected = [
        (VIC, 13521.0, 700.0), (ZOE, 13519.0, 650.0), (WES, 6761.0, 600.0), (YUL, 6759.0, 550.0), (MIA, 2500.0, 500.0),
    ]
    for entry, (donor, to_entity, to_committee) in zip(top, expected):
        assert entry["donor_key"] == _donor_key(donor)
        assert entry["donor_name"] == _donor_name(donor)
        assert entry["amount_to_entity"] == _near(to_entity)
        assert entry["amount_to_committee"] == _near(to_committee)
    small = {entry["donor_name"]: entry for entry in cogiving[300]["top_shared_donors"]}
    assert set(small) == {"Ivy Ingram", "Jon Jarvis", "Kay Keller"}
    assert small["Ivy Ingram"]["amount_to_entity"] == _near(2450.0)
    assert small["Ivy Ingram"]["amount_to_committee"] == _near(300.0)
    assert small["Jon Jarvis"]["amount_to_entity"] == _near(2000.0)
    assert small["Jon Jarvis"]["amount_to_committee"] == _near(400.0)
    assert small["Kay Keller"]["amount_to_entity"] == _near(440.0)
    assert small["Kay Keller"]["amount_to_committee"] == _near(200.0)


@pytest.mark.parametrize(
    "pool, expected, pool_size",
    [
        # pool 2 = {Vic 13,521, Zoe 13,519}: only 530 received money from both
        (2, [(530, "Big Overlap PAC", 2, 1350.0, 27040.0)], 2),
        # pool 3 adds Wes 6,761: 530 now has three of them, and 540 (Vic 100 + Wes 100) reaches the floor of two
        (3, [(530, "Big Overlap PAC", 3, 1950.0, 33801.0), (540, "Pair Fund", 2, 200.0, 20282.0)], 3),
    ],
    ids=["pool-2", "pool-3"],
)
def test_donor_pool_limits_which_of_the_entitys_donors_are_matched(conn, pool, expected, pool_size):
    """The pool is the entity's top donors BY AMOUNT (not the first rows, not alphabetical).
    Kills: donor_pool ignored, the pool taken in name or id order, shared amounts computed over all 21 donors."""
    result = _rel(conn, donor_pool=pool)
    _assert_cogiving(result["cogiving"], expected)
    assert result["summary"]["entity_donor_count"] == 21
    assert result["summary"]["donor_pool_size"] == pool_size
    assert result["summary"]["cogiving_committee_count"] == len(expected)


def test_relationship_summary_for_the_default_pool(conn):
    """21 donors, all inside the default pool of 2,000; six co-giving committees.
    Kills: a pool size that echoes the parameter instead of the donors actually matched, a count of the capped list."""
    summary = _rel(conn)["summary"]
    assert summary["entity_donor_count"] == 21
    assert summary["donor_pool_size"] == 21
    assert summary["cogiving_committee_count"] == 6


def test_limit_keeps_the_best_ranked_cogiving_committees(conn):
    """Kills: limit ignored, limit applied before ranking (an arbitrary committee survives)."""
    result = _rel(conn, limit=2)
    assert [row["committee_id"] for row in result["cogiving"]] == [530, 300]


def test_cogiving_follows_the_window_for_both_the_entitys_donors_and_the_gifts(conn):
    """April 2026 only: the race's donors that month are Mia (1,000 at 200) and Kay (250 at 100); outside committees
    that received April gifts from them: 510 (Kay 800), 530 (Mia 500), 109 (Kay 50).  Kay's 2025 gift to 500 and every
    other month's gift are out.  With min_shared_donors=1.
    Kills: the window applied to the entity's donors only (or to the gifts only)."""
    result = _rel(conn, date_from=date(2026, 4, 1), date_to=date(2026, 4, 30), min_shared_donors=1)
    expected = [
        (510, "Labor Alliance PAC", 1, 800.0, 250.0),
        (530, "Big Overlap PAC", 1, 500.0, 1000.0),
        (109, "Aldous Legacy Fund", 1, 50.0, 250.0),
    ]
    _assert_cogiving(result["cogiving"], expected)
    assert result["summary"]["entity_donor_count"] == 2


def test_relationship_shared_donors_are_exactly_the_donor_builders_list(conn):
    """Both views must tell the same story about the race's candidate overlap (Ivy, Kay, Sloan).
    Kills: a different list, a different cap, a different order, an empty list on the relationships side."""
    from_donors = _donors(conn)["shared_donors"]
    from_relationships = _rel(conn)["shared_donors"]
    assert len(from_donors) == 3
    assert from_relationships == from_donors


def test_relationship_shared_donors_for_a_single_group_entity_are_empty(conn):
    """Kills: shared donors invented for a one-group entity."""
    assert _rel(conn, entity=CORTEZ_ENTITY)["shared_donors"] == []


def test_resolved_entities_feed_every_builder_the_same_numbers_as_the_literal_entities(conn):
    """End to end: resolve_entity -> get_entity_donors / entity_donor_rows / get_entity_risk.  The resolved race and the
    literal RACE_ENTITY give identical donor totals; the rows for the resolved committee list contain no receipt of
    a committee outside it; the resolved candidate (family expansion) sees committee 102's reconciliation row.
    Kills: a resolver whose output the builders cannot consume (missing keys, wrong group shapes)."""
    race = resolve_entity(conn, "race", RACE_R_KEY, today=TODAY)
    donors = get_entity_donors(conn, race, **WINDOW)
    assert donors["totals"]["total_amount"] == _near(RACE_TOTALS["total_amount"])
    assert donors["totals"]["donor_count"] == RACE_TOTALS["donor_count"]
    assert [group["label"] for group in donors["groups"]] == ["Alice Aldous", "Bryn Bellamy", "Fay Fontaine"]
    rows = entity_donor_rows(conn, race["committee_ids"], **WINDOW)
    assert {row["committee_id"] for row in rows} <= set(race["committee_ids"])
    assert sum(row["total_amount"] for row in rows) == _near(RACE_TOTALS["total_amount"])

    candidate = resolve_entity(conn, "candidate", CAND_A)
    risk = get_entity_risk(conn, candidate, **WINDOW)
    assert [row["committee_id"] for row in risk["reconciliation"]] == [102, 100, 100]


# ===========================================================================
# Cross-cutting: open windows, input purity
# ===========================================================================

def test_donor_and_geography_builders_read_all_time_when_the_window_is_open(conn):
    """No date arguments = all time (not 'this cycle', not 'nothing').  Ivy's 2025-12-31 (800) and 2027-01-01 (900)
    gifts at committee 100 join the 2026 money: 51,965 + 1,700 = 53,665 over 31 contributions; in-kind 700 + 333 + 120
    = 1,153.  Kills: a default window, None treated as 'no rows'."""
    donors = get_entity_donors(conn, RACE_ENTITY)
    assert donors["totals"]["total_amount"] == _near(53665.0)
    assert donors["totals"]["donor_count"] == 21
    assert donors["totals"]["contribution_count"] == 31
    assert donors["totals"]["in_kind_total"] == _near(1153.0)
    geography = get_entity_geography(conn, RACE_ENTITY)
    assert geography["totals"]["total_amount"] == _near(53665.0)
    assert geography["time_series"][0]["month"] == "2025-12"
    assert geography["time_series"][-1]["month"] == "2027-01"


def test_relationship_and_risk_builders_read_all_time_when_the_window_is_open(conn):
    """All time, Kay's 2025 gift (100) to committee 500 is a third shared donor there (Ivy 600, Jon 600, Kay 100 =
    1,300 to the committee; their entity money 4,150 + 2,000 + 440 = 6,590, Ivy's total now including 800 + 900), so 500
    outranks 300 (three donors, 900).  The large flags do not change (no large gift sits outside 2026).
    Kills: a default window on the relationship or risk builders."""
    relationships = get_entity_relationships(conn, RACE_ENTITY)
    expected = [
        (530, "Big Overlap PAC", 7, 3850.0, 49210.0),
        (500, "Prairie Educators PAC", 3, 1300.0, 6590.0),
        (300, "Carlos Cortez for Senate", 3, 900.0, 6590.0),
        (510, "Labor Alliance PAC", 2, 2000.0, 4590.0),
        (540, "Pair Fund", 2, 200.0, 20282.0),
        (102, "Aldous Victory Fund", 2, 100.0, 4590.0),
    ]
    _assert_cogiving(relationships["cogiving"], expected)
    risk = get_entity_risk(conn, RACE_ENTITY)
    _assert_large_flags(_flags(risk, "large_single_contribution"), RACE_LARGE_FLAGS)


def test_builders_do_not_modify_the_entity_they_are_given(conn):
    """A resolved entity is cached by the routes and shared between tabs: a builder that sorts its groups in place,
    adds keys to them or empties a list would corrupt the next tab.  Kills: in-place mutation of the input."""
    entity = copy.deepcopy(RACE_ENTITY)
    snapshot = copy.deepcopy(entity)
    get_entity_donors(conn, entity, **WINDOW)
    get_entity_geography(conn, entity, **WINDOW)
    get_entity_risk(conn, entity, **WINDOW)
    get_entity_relationships(conn, entity, **WINDOW)
    assert entity == snapshot
