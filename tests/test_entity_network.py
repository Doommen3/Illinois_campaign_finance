"""Plan step 3, part B (tests first): the entity ego network, ``database/entity_network.py``.

Plan: .claude/plans/race-sort-and-entity-networks.md, decision N4 and the section "Contract --
database/entity_analytics.py and database/entity_network.py" (the "Shared conventions" paragraph and the
``entity_network`` half). ``docs/outside_spending.md`` is the Part-9 attribution rule (name key, split rows,
cycle gate, archived rows never count).

The API under test (a compile-only stub exists; step 4 implements it)::

    get_entity_network(conn, entity, *, date_from=None, date_to=None, caps=None) -> dict
    DEFAULT_CAPS == {"donors": 40, "vendors": 25, "transfers": 15, "ie_spenders": 15}

``entity`` is built by hand here, in the shape ``resolve_entity`` returns; nothing is imported from
``database/entity_analytics.py``. Tables are the ETL's own isbe_* tables; the two matviews are plain tables.

Readings of the contract where it is silent or loose (each is flagged in the tester's report; each is
isolated in its own test where it can be, so a different decision is a one-line amendment):

  R1  a node's ``total_amount`` is the sum of its incident edge weights; pinned only for leaf nodes (donor,
      vendor, unresolved transfer source) that carry one edge type. Entity committee / candidate / spender
      totals are not pinned.
  R2  top-N per class ranks NEIGHBOUR NODES by the node's total weight (not by its largest single edge); the
      resolved ``committee:<id>`` transfer payer counts against the ``transfers`` cap like any payer; the
      dropped count in ``summary.truncated`` counts neighbour nodes. Ties: amount desc, then label or id asc
      (the contract says "name/id"; the tie tests accept either order).
  R3  one edge per (edge_type, source, target) -- and, for independent_expenditure, per support_oppose flag:
      one spender both supporting and opposing a candidate gives two edges. ``count`` = underlying rows
      (for committee_candidate the count is not pinned).
  R4  an unresolved 2A payer node (``transfer:<sha1-16>``) and a resolved one (``committee:<id>``) are both
      ``node_type "committee"``, ``role "transfer_source"``; ``node_type`` has no "transfer" value.
  R5  ``vendor:`` ids hash ``LOWER(TRIM(first_name last_name))``; first_name NULL or '' both mean "no first
      name"; a payee with a first name is "first last". Deeper normalisation (punctuation, inner spaces) is
      unpinned.
  R6  Part-9 attribution gate = the candidate's own candidacy cycles (election_year + election_year % 2), NOT
      the race entity's ``cycle``: the window (date_from/date_to) is what narrows a race page to its cycle.
  R7  ``summary.window`` holds ISO date STRINGS (the payload is served as JSON), None when unbounded.
  R8  "every list sorted amount desc": edges by weight, nodes by total_amount, centrality by weighted degree.
  R9  ``source_table`` names the table the edge was read from.
  R10 a redacted donor (``redaction_requested`` OR ``address1 = 'Redaction Requested'``) keeps label and
      amounts; the node carries no city/state/zip and ``redacted is True``. The node id still embeds the
      donor key (the contract's formula), so an id is not address-free when the row still holds an address.

Fixture world (committed literals; the expected values are hand-summed in the tables below):

  candidates   1 "Ada Q. Example" (ISBE record, middle initial; filers write "Ada Example"), cycles {2026};
               2 "Bo Rival", cycles {2026, 2024}: its second candidacy is election_year 2023, an odd year
               that belongs to cycle 2024; 3 "Cal Outsider", cycles {2026}, in no entity.
  committees   100 Friends of Ada Example, 101 Ada Example Victory Fund (both Ada), 102 Citizens for Ada Example
               (a family member, linked to nobody in isbe_candidate_committees), 200 Bo Rival for Illinois;
               300 Prairie Outside Fund (outside every entity, gets money from the same donors and vendors);
               400 Prairie State Leadership Fund (a 2A payer matches its name); 500 and 501 both named
               "Twin Name Committee" (a payer matching two committees stays unresolved); IE spenders 900
               Statewide Action PAC, 905 Minor Spender PAC, 901 Quiet Spender PAC (only a negative and a zero row).
  entities     race: committees [100, 101, 200], candidates [1, 2], groups "Ada Example" [100, 101] and
               "Bo Rival" [200]. candidate: committees [100, 101, 102], candidate [1], groups "Friends of Ada
               Example" [100] and the family group "Victory Fund family" [101, 102].
  window       WIN = 2026-01-01 .. 2026-06-30 inclusive. Rows sit exactly on both edges and one day outside each.
  receipts     donors D1 (also a same-name namesake D1b at another address), D2 (two rows whose last names
               differ only in case), D3/D4/D5 (redacted three ways), organizations O1 (first_name NULL), O2
               (first_name ''), O3 (gives to two committees); 5A in-kind, 2A transfers, and the rows that must
               count for nothing: archived, zero, negative, 3A loan, 4A other receipt, outside committee 300.
  expenditures vendor "Prairie Print Co" paid by two committees under case/whitespace/NULL-vs-'' variants;
               a vendor only an archived / negative / zero row mentions; Part-9 rows of spender 900 (S, O, a
               two-name row, a three-name row naming an outsider, archived, out of window, outside the
               candidacy cycles) and the Part-9 payments that must not become vendors.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date

import pytest

from database.connection import get_db, init_db
from database.entity_network import DEFAULT_CAPS, get_entity_network

DC = "donor_committee"
IK = "in_kind_donor_committee"
TI = "transfer_in"
CV = "committee_vendor"
IE = "independent_expenditure"
CC = "committee_candidate"
EDGE_TYPES = (DC, IK, TI, CV, IE, CC)

WIN = (date(2026, 1, 1), date(2026, 6, 30))

TOP_LEVEL_KEYS = {"entity", "nodes", "edges", "centrality", "summary"}
SUMMARY_KEYS = {"node_count", "edge_count", "edges_by_type", "truncated", "edge_type_definitions", "window"}
NODE_REQUIRED = {"id", "label", "node_type", "role", "total_amount"}
NODE_ALLOWED = NODE_REQUIRED | {"group_label", "redacted"}
EDGE_REQUIRED = {
    "source", "target", "edge_type", "weight", "count",
    "edge_label", "edge_description", "weight_unit", "metric_label", "source_table",
}
CENTRALITY_KEYS = {"node_id", "label", "node_type", "weighted_degree", "degree"}
SOURCE_TABLE = {
    DC: "isbe_condensed_receipts", IK: "isbe_condensed_receipts", TI: "isbe_condensed_receipts",
    CC: "isbe_condensed_receipts", CV: "isbe_condensed_expenditures", IE: "isbe_condensed_expenditures",
}

# ---------------------------------------------------------------------------
# Identity helpers: the contract's formulas, computed in Python from fixture literals
# ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    return name.strip(" ").lower()


def _h16(name: str) -> str:
    return hashlib.sha1(_norm(name).encode("utf-8")).hexdigest()[:16]


def vendor_id(name: str) -> str:
    return "vendor:" + _h16(name)


def transfer_id(name: str) -> str:
    return "transfer:" + _h16(name)


def donor_key(donor: dict) -> str:
    """LOWER(TRIM(first|last|address1|address2|city|state|zip)) -- TRIM only strips the ends of the whole string."""
    parts = (donor["first"], donor["last"], donor["a1"], donor["a2"], donor["city"], donor["state"], donor["zipcode"])
    return "|".join(part or "" for part in parts).strip(" ").lower()


def donor_label(donor: dict) -> str:
    return " ".join(part for part in (donor["first"], donor["last"]) if part).strip(" ")


def _donor(first, last, a1=None, a2=None, city=None, state=None, zipcode=None, flag=False):
    return {"first": first, "last": last, "a1": a1, "a2": a2, "city": city, "state": state,
            "zipcode": zipcode, "flag": flag}


DONORS = {
    # individuals
    "D1": _donor("Dana", "Donor", "12 Oak St", None, "Springfield", "IL", "62701"),
    "D1b": _donor("Dana", "Donor", "9 Elm Ave", None, "Chicago", "IL", "60601"),   # same name, other address
    # D2a / D2b: last names differ only in case, address2 ends in a space that the whole-string TRIM keeps
    "D2a": _donor("Eli", "EGAN", "5 Pine Rd", "Apt 2 ", "Peoria", "IL", "61602"),
    "D2b": _donor("Eli", "Egan", "5 Pine Rd", "Apt 2 ", "Peoria", "IL", "61602"),
    "D3": _donor("Rhea", "Redactwell", flag=True),                                     # ISBE format: all NULL
    "D4": _donor("Rory", "Reviewed", "77 Cedar Ln", None, "Peoria", "IL", "61602", flag=True),   # flag, address kept
    "D5": _donor("Ray", "Rescrubbed", "Redaction Requested", None, "Quincy", "IL", "62301"),     # text marker only
    # organizations: the business name is in last_name, first_name NULL or ''
    "O1": _donor(None, "Acme Union Local 9", "1 Union Way", None, "Chicago", "IL", "60604"),
    "O2": _donor("", "Prairie Builders LLC", "40 Main St", None, "Peoria", "IL", "61602"),
    "O3": _donor(None, "Heartland Realty", "300 Lake St", None, "Rockford", "IL", "61101"),
    # window-edge donors, penny donor, in-kind donor, family-only donor
    "B1": _donor("Bea", "Border", "1 Edge Rd", None, "Joliet", "IL", "60432"),
    "B2": _donor("Cy", "Border", "2 Edge Rd", None, "Joliet", "IL", "60432"),
    "Early": _donor("Dee", "Early", "3 Edge Rd", None, "Joliet", "IL", "60432"),
    "Late": _donor("Eve", "Late", "4 Edge Rd", None, "Joliet", "IL", "60432"),
    "Penny": _donor("Penny", "Pincher", "8 Cent Ct", None, "Aurora", "IL", "60505"),
    "Ina": _donor("Ina", "Inkind", "6 Gift Way", None, "Elgin", "IL", "60120"),
    "Fay": _donor("Fay", "Family", "14 Kin St", None, "Decatur", "IL", "62521"),
    # rows that must leave no node at all
    "Zed": _donor("Zed", "Zero", "21 Null Rd", None, "Alton", "IL", "62002"),
    "Nate": _donor("Nate", "Negative", "22 Null Rd", None, "Alton", "IL", "62002"),
    "Archie": _donor("Archie", "Archived", "23 Null Rd", None, "Alton", "IL", "62002"),
    "Arlo": _donor("Arlo", "Archivedgift", "24 Null Rd", None, "Alton", "IL", "62002"),
    "Lou": _donor("Lou", "Lender", "25 Null Rd", None, "Alton", "IL", "62002"),
    "Ivy": _donor("Ivy", "Interest", "26 Null Rd", None, "Alton", "IL", "62002"),
    "Otto": _donor("Otto", "Offsite", "27 Null Rd", None, "Alton", "IL", "62002"),
}
D = {name: "donor:" + donor_key(donor) for name, donor in DONORS.items()}
REDACTED = {"D3", "D4", "D5"}

C100, C101, C102, C200 = "committee:100", "committee:101", "committee:102", "committee:200"
C300, C400, C500, C501 = "committee:300", "committee:400", "committee:500", "committee:501"
C900, C901, C905 = "committee:900", "committee:901", "committee:905"
CAND1, CAND2, CAND3 = "candidate:1", "candidate:2", "candidate:3"

V_A = vendor_id("Prairie Print Co")
V_B = vendor_id("Lakeshore Media Group")
V_P = vendor_id("Wren Wexler")
T_INC = transfer_id("Prairie State Leadership Fund Inc")
T_TWIN = transfer_id("Twin Name Committee")

RACE_ENTITY = {
    "kind": "race",
    "key": "state-representative-representative-district-45",
    "label": "State Representative - Representative District 45",
    "cycle": 2026,
    "committee_ids": [100, 101, 200],
    "candidate_ids": [1, 2],
    "groups": [
        {"label": "Ada Example", "committee_ids": [100, 101], "candidate_id": 1, "family_id": None},
        {"label": "Bo Rival", "committee_ids": [200], "candidate_id": 2, "family_id": None},
    ],
}
CANDIDATE_ENTITY = {
    "kind": "candidate",
    "key": "1",
    "label": "Ada Q. Example",
    "cycle": None,
    "committee_ids": [100, 101, 102],
    "candidate_ids": [1],
    "groups": [
        {"label": "Friends of Ada Example", "committee_ids": [100], "candidate_id": 1, "family_id": None},
        {"label": "Victory Fund family", "committee_ids": [101, 102], "candidate_id": 1, "family_id": 7},
    ],
}
EMPTY_ENTITY = {
    "kind": "race", "key": "empty-race", "label": "Empty Race", "cycle": 2026,
    "committee_ids": [], "candidate_ids": [1, 2], "groups": [],
}
BULK_ENTITY = {
    "kind": "candidate", "key": "1", "label": "Ada Q. Example", "cycle": None,
    "committee_ids": [100], "candidate_ids": [1],
    "groups": [{"label": "Friends of Ada Example", "committee_ids": [100], "candidate_id": 1, "family_id": None}],
}

# ---------------------------------------------------------------------------
# Expected edges. Key = (edge_type, source, target, support_oppose); value = (weight, count);
# count None = not pinned. Every sum is hand-computed from the seed tables further down.
# ---------------------------------------------------------------------------

# Race entity, window WIN.
#   committee 100, 1A rows in WIN: D1 3000 + 2000 (the archived 3000 twin and the -500 refund do not count)
#     = 5000; D1b 2500; D2 800 + 200 = 1000; D3 1200; O1 4000; O3 1500; Penny 3 x 0.10 = 0.30
#     -> 15200.30 (the 5A 750, the 2A 2000 + 500, the 3A loan, the 4A interest, the zero and the negative: none)
#   committee 101: D1 1000 + D4 900 + D5 400 = 2300.   committee 200: O2 2500 + O3 1500 + B1 100 + B2 200 = 4300.
#   vendors: 100 -> Prairie Print Co 600 (the archived 9999, the -80, the 2025-12-31 1000: out); 101 -> same vendor
#     500 (the 2026-07-01 2000 is out); 101 -> Wren Wexler 300; 200 -> Lakeshore Media Group 900.
#   spender 900 (candidate 1 = "Ada Q. Example", key ADA/EXAMPLE; candidate 2 = "Bo Rival"):
#     Ada S: 120 (2026-01-01) + 5000 + 1000/2 + 1500 = 7120 in 4 rows;  Ada O: 900/3 + 75 = 375 in 2 rows
#     Bo O: 2000 + 900/3 + 130 (2026-06-30) = 2430 in 3 rows;           Bo S: 1000/2 = 500 in 1 row
#     (the archived 8000, the "Cal Outsider" rows, the 2025-12-31 700 and the 2026-07-01 650 are out)
RACE_WIN = {
    (DC, D["D1"], C100, None): (5000.0, 2),
    (DC, D["D1"], C101, None): (1000.0, 1),
    (DC, D["D1b"], C100, None): (2500.0, 1),
    (DC, D["D2a"], C100, None): (1000.0, 2),
    (DC, D["D3"], C100, None): (1200.0, 1),
    (DC, D["D4"], C101, None): (900.0, 1),
    (DC, D["D5"], C101, None): (400.0, 1),
    (DC, D["O1"], C100, None): (4000.0, 1),
    (DC, D["O2"], C200, None): (2500.0, 1),
    (DC, D["O3"], C100, None): (1500.0, 1),
    (DC, D["O3"], C200, None): (1500.0, 1),
    (DC, D["B1"], C200, None): (100.0, 1),
    (DC, D["B2"], C200, None): (200.0, 1),
    (DC, D["Penny"], C100, None): (0.3, 3),
    (IK, D["O1"], C100, None): (750.0, 1),
    (IK, D["Ina"], C200, None): (300.0, 1),
    (TI, C400, C100, None): (2000.0, 1),
    (TI, C400, C200, None): (700.0, 1),
    (TI, T_INC, C101, None): (800.0, 1),
    (TI, T_TWIN, C100, None): (500.0, 1),
    (CV, C100, V_A, None): (600.0, 1),
    (CV, C101, V_A, None): (500.0, 1),
    (CV, C101, V_P, None): (300.0, 1),
    (CV, C200, V_B, None): (900.0, 1),
    (IE, C900, CAND1, "S"): (7120.0, 4),
    (IE, C900, CAND1, "O"): (375.0, 2),
    (IE, C900, CAND2, "O"): (2430.0, 3),
    (IE, C900, CAND2, "S"): (500.0, 1),
    (IE, C905, CAND2, "S"): (50.0, 1),
    (CC, C100, CAND1, None): (15200.3, None),
    (CC, C101, CAND1, None): (2300.0, None),
    (CC, C200, CAND2, None): (4300.0, None),
}
RACE_WIN_NODE_IDS = {
    C100, C101, C200, CAND1, CAND2,
    D["D1"], D["D1b"], D["D2a"], D["D3"], D["D4"], D["D5"], D["O1"], D["O2"], D["O3"],
    D["B1"], D["B2"], D["Penny"], D["Ina"],
    V_A, V_B, V_P, C400, T_INC, T_TWIN, C900, C905,
}

# Race entity, no window. Only the rows the window cut change (the archived, zero, negative and non-1A rows
# stay out): D1 -> 100 adds 9000 (2025-12-31) + 4000 (2026-07-01); D1 -> 101 adds 600 (2026-07-10); Early 300 and
# Late 400 -> 200; O1 in-kind adds 250 (2026-07-02); 400 -> 100 adds 1300 (2025-12-31); 100 -> vendor adds 1000,
# 101 -> vendor adds 2000; Ada S adds 700 (2025-12-31) = 7820 in 5 rows (the 2023-05-05 700 is outside Ada's only
# cycle, 2026, and stays out); Bo O adds 650 (2026-07-01) + 400 (2023-05-05, cycle 2024 = Bo's 2023 candidacy)
# = 3480 in 5 rows; committee receipts: 100 = 15200.30 + 9000 + 4000 = 28200.30, 101 = 2300 + 600 = 2900,
# 200 = 4300 + 300 + 400 = 5000.
RACE_ALL = {
    **RACE_WIN,
    (DC, D["D1"], C100, None): (18000.0, 4),
    (DC, D["D1"], C101, None): (1600.0, 2),
    (DC, D["Early"], C200, None): (300.0, 1),
    (DC, D["Late"], C200, None): (400.0, 1),
    (IK, D["O1"], C100, None): (1000.0, 2),
    (TI, C400, C100, None): (3300.0, 2),
    (CV, C100, V_A, None): (1600.0, 2),
    (CV, C101, V_A, None): (2500.0, 2),
    (IE, C900, CAND1, "S"): (7820.0, 5),
    (IE, C900, CAND2, "O"): (3480.0, 5),
    (CC, C100, CAND1, None): (28200.3, None),
    (CC, C101, CAND1, None): (2900.0, None),
    (CC, C200, CAND2, None): (5000.0, None),
}

# Candidate entity (committees 100, 101, 102; candidate 1), window WIN. Committee 102 adds D1 700 + Fay 1100 = 1800
# of receipts and pays Prairie Print Co 250; nothing of committee 200 / candidate 2 / spender 905 is in it.
CAND_WIN = {
    (DC, D["D1"], C100, None): (5000.0, 2),
    (DC, D["D1"], C101, None): (1000.0, 1),
    (DC, D["D1"], C102, None): (700.0, 1),
    (DC, D["D1b"], C100, None): (2500.0, 1),
    (DC, D["D2a"], C100, None): (1000.0, 2),
    (DC, D["D3"], C100, None): (1200.0, 1),
    (DC, D["D4"], C101, None): (900.0, 1),
    (DC, D["D5"], C101, None): (400.0, 1),
    (DC, D["O1"], C100, None): (4000.0, 1),
    (DC, D["O3"], C100, None): (1500.0, 1),
    (DC, D["Penny"], C100, None): (0.3, 3),
    (DC, D["Fay"], C102, None): (1100.0, 1),
    (IK, D["O1"], C100, None): (750.0, 1),
    (TI, C400, C100, None): (2000.0, 1),
    (TI, T_INC, C101, None): (800.0, 1),
    (TI, T_TWIN, C100, None): (500.0, 1),
    (CV, C100, V_A, None): (600.0, 1),
    (CV, C101, V_A, None): (500.0, 1),
    (CV, C101, V_P, None): (300.0, 1),
    (CV, C102, V_A, None): (250.0, 1),
    (IE, C900, CAND1, "S"): (7120.0, 4),
    (IE, C900, CAND1, "O"): (375.0, 2),
    (CC, C100, CAND1, None): (15200.3, None),
    (CC, C101, CAND1, None): (2300.0, None),
    (CC, C102, CAND1, None): (1800.0, None),
}
CAND_WIN_NODE_IDS = {
    C100, C101, C102, CAND1,
    D["D1"], D["D1b"], D["D2a"], D["D3"], D["D4"], D["D5"], D["O1"], D["O3"], D["Penny"], D["Fay"],
    V_A, V_P, C400, T_INC, T_TWIN, C900,
}

# Race entity, date_to = 2025-12-31 only (date_from unbounded): D1 9000 -> 100, Early 300 -> 200, 400 -> 100 1300,
# 100 -> Prairie Print Co 1000, Ada S 700 (2025-12-31), Bo O 400 (2023-05-05, cycle 2024); committee receipts
# 100 = 9000 and 200 = 300.
RACE_UP_TO_2025 = {
    (DC, D["D1"], C100, None): (9000.0, 1),
    (DC, D["Early"], C200, None): (300.0, 1),
    (TI, C400, C100, None): (1300.0, 1),
    (CV, C100, V_A, None): (1000.0, 1),
    (IE, C900, CAND1, "S"): (700.0, 1),
    (IE, C900, CAND2, "O"): (400.0, 1),
    (CC, C100, CAND1, None): (9000.0, None),
    (CC, C200, CAND2, None): (300.0, None),
}
# Race entity, date_from = 2026-07-01 only (date_to unbounded): D1 4000 -> 100 (2026-07-01), D1 600 -> 101, Late 400
# -> 200, O1 in-kind 250, 101 -> vendor 2000 (2026-07-01), Bo O 650 (2026-07-01); committee receipts 100 = 4000,
# 101 = 600, 200 = 400.
RACE_FROM_JULY = {
    (DC, D["D1"], C100, None): (4000.0, 1),
    (DC, D["D1"], C101, None): (600.0, 1),
    (DC, D["Late"], C200, None): (400.0, 1),
    (IK, D["O1"], C100, None): (250.0, 1),
    (CV, C101, V_A, None): (2000.0, 1),
    (IE, C900, CAND2, "O"): (650.0, 1),
    (CC, C100, CAND1, None): (4000.0, None),
    (CC, C101, CAND1, None): (600.0, None),
    (CC, C200, CAND2, None): (400.0, None),
}

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

_DDL = [
    """CREATE TABLE isbe_committees (
        id INTEGER PRIMARY KEY, type TEXT, state_committee BOOLEAN, local_committee BOOLEAN, refer_name TEXT,
        name TEXT, address1 TEXT, address2 TEXT, address3 TEXT, city TEXT, state TEXT, zipcode TEXT,
        active BOOLEAN DEFAULT TRUE, status_date DATE, creation_date DATE, creation_amount DOUBLE PRECISION,
        disp_funds_return TEXT, disp_funds_political_committee TEXT, disp_funds_charity TEXT, disp_funds_95 TEXT,
        candidate_position TEXT, policy_position TEXT, party TEXT, purpose TEXT)""",
    """CREATE TABLE isbe_candidates (
        id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT, address1 TEXT, address2 TEXT, city TEXT,
        state TEXT, zipcode TEXT, office TEXT, district_type TEXT, district TEXT, residence_county TEXT,
        party TEXT, redaction_requested BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE isbe_candidacies (
        id INTEGER PRIMARY KEY, candidate_id INTEGER, election_type TEXT, election_year INTEGER, race_type TEXT,
        outcome TEXT, fair_campaign BOOLEAN DEFAULT FALSE, limits_off BOOLEAN DEFAULT FALSE,
        limits_off_reason TEXT)""",
    """CREATE TABLE isbe_candidate_committees (
        id INTEGER PRIMARY KEY, committee_id INTEGER, candidate_id INTEGER)""",
    """CREATE TABLE isbe_condensed_receipts (
        id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, etrans_id TEXT, last_name TEXT,
        first_name TEXT, received_date DATE, amount DOUBLE PRECISION, aggregate_amount DOUBLE PRECISION,
        loan_amount DOUBLE PRECISION, occupation TEXT, employer TEXT, address1 TEXT, address2 TEXT, city TEXT,
        state TEXT, zipcode TEXT, d2_part TEXT, description TEXT, vendor_last_name TEXT, vendor_first_name TEXT,
        vendor_address1 TEXT, vendor_address2 TEXT, vendor_city TEXT, vendor_state TEXT, vendor_zipcode TEXT,
        archived BOOLEAN DEFAULT FALSE, country TEXT, redaction_requested BOOLEAN DEFAULT FALSE)""",
    """CREATE TABLE isbe_condensed_expenditures (
        id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, etrans_id TEXT, last_name TEXT,
        first_name TEXT, expended_date DATE, amount DOUBLE PRECISION, aggregate_amount DOUBLE PRECISION,
        address1 TEXT, address2 TEXT, city TEXT, state TEXT, zipcode TEXT, d2_part TEXT, purpose TEXT,
        candidate_name TEXT, office TEXT, supporting BOOLEAN DEFAULT FALSE, opposing BOOLEAN DEFAULT FALSE,
        archived BOOLEAN DEFAULT FALSE, country TEXT, redaction_requested BOOLEAN DEFAULT FALSE)""",
]


def _sql(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _insert(conn, table: str, rows: list[dict], first_id: int | None = None) -> None:
    """One multi-row INSERT; ``first_id`` numbers the rows (in the order given) when the rows carry no id."""
    if first_id is not None:
        rows = [{"id": first_id + offset, **row} for offset, row in enumerate(rows)]
    columns = list(rows[0])
    values = ", ".join("(" + ", ".join(_sql(row[column]) for column in columns) + ")" for row in rows)
    conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values}")


def _committee_row(committee_id: int, name: str, kind: str = "Political Action") -> dict:
    return {"id": committee_id, "name": name, "type": kind, "active": True, "city": "Springfield",
            "state": "IL", "zipcode": "62701"}


def _receipt(committee, donor, day, amount, *, part="1A", archived=False) -> dict:
    d = DONORS[donor] if isinstance(donor, str) else donor
    return {
        "committee_id": committee, "last_name": d["last"], "first_name": d["first"], "received_date": day,
        "amount": amount, "address1": d["a1"], "address2": d["a2"], "city": d["city"], "state": d["state"],
        "zipcode": d["zipcode"], "d2_part": part, "archived": archived, "redaction_requested": d["flag"],
    }


def _payer(committee, last, first, day, amount, *, archived=False) -> dict:
    return _receipt(committee, _donor(first, last), day, amount, part="2A", archived=archived)


def _spend(committee, payee_last, day, amount, *, payee_first=None, part="2A", archived=False,
           candidate_name=None, direction=None) -> dict:
    return {
        "committee_id": committee, "last_name": payee_last, "first_name": payee_first, "expended_date": day,
        "amount": amount, "d2_part": part, "purpose": "Services", "candidate_name": candidate_name,
        "office": "State Representative" if candidate_name else None,
        "supporting": direction == "S", "opposing": direction == "O", "archived": archived,
    }


def _ie(spender, day, amount, name, direction, *, part="9B", archived=False) -> dict:
    return _spend(spender, "Mailhouse Direct", day, amount, part=part, archived=archived,
                  candidate_name=name, direction=direction)


def _seed_reference(conn) -> None:
    committees = [
        (100, "Friends of Ada Example", "Candidate"), (101, "Ada Example Victory Fund", "Candidate"),
        (102, "Citizens for Ada Example", "Candidate"), (200, "Bo Rival for Illinois", "Candidate"),
        (300, "Prairie Outside Fund", "Political Action"), (400, "Prairie State Leadership Fund", "Political Action"),
        (500, "Twin Name Committee", "Political Action"), (501, "Twin Name Committee", "Political Action"),
        (900, "Statewide Action PAC", "Political Action"), (901, "Quiet Spender PAC", "Political Action"),
        (905, "Minor Spender PAC", "Political Action"),
    ]
    _insert(conn, "isbe_committees", [_committee_row(*committee) for committee in committees])
    candidates = [
        # (id, last_name, first_name): candidate 1 carries a middle initial, filers write "Ada Example"
        (1, "Example", "Ada Q."), (2, "Rival", "Bo"), (3, "Outsider", "Cal"),
    ]
    _insert(conn, "isbe_candidates", [
        {"id": cid, "last_name": last, "first_name": first, "office": "State Representative",
         "district_type": "Representative District", "district": "45", "party": "Independent",
         "city": "Springfield", "state": "IL", "zipcode": "62701", "residence_county": "Sangamon",
         "redaction_requested": False}
        for cid, last, first in candidates
    ])
    candidacies = [
        # (candidate, election_type, election_year): 2023 is an odd year, so it belongs to cycle 2024
        (1, "General Election", 2026), (2, "General Election", 2026),
        (2, "Consolidated Election", 2023), (3, "General Election", 2026),
    ]
    _insert(conn, "isbe_candidacies", [
        {"candidate_id": cid, "election_type": kind, "election_year": year, "race_type": "challenger", "outcome": None}
        for cid, kind, year in candidacies
    ], first_id=1)
    _insert(conn, "isbe_candidate_committees", [
        {"committee_id": 100, "candidate_id": 1}, {"committee_id": 101, "candidate_id": 1},
        {"committee_id": 200, "candidate_id": 2},
    ], first_id=1)   # 102 is a family member only: no link row, as for a re-registered committee


def _receipt_rows() -> list[dict]:
    return [
        # --- D1 -> 100: 3000 + 2000 count; the archived twin, the refund, and the two rows outside WIN do not
        _receipt(100, "D1", "2026-02-10", 3000.0),
        _receipt(100, "D1", "2026-02-10", 3000.0, archived=True),
        _receipt(100, "D1", "2026-03-05", 2000.0),
        _receipt(100, "D1", "2026-04-04", -500.0),
        _receipt(100, "D1", "2025-12-31", 9000.0),
        _receipt(100, "D1", "2026-07-01", 4000.0),
        # --- D1 -> 101 (1000 in WIN, 600 after), 102 (candidate entity only), 300 (outside every entity)
        _receipt(101, "D1", "2026-04-01", 1000.0),
        _receipt(101, "D1", "2026-07-10", 600.0),
        _receipt(102, "D1", "2026-02-02", 700.0),
        _receipt(300, "D1", "2026-02-02", 50000.0),
        _receipt(100, "D1b", "2026-02-20", 2500.0),
        _receipt(100, "D2a", "2026-03-03", 800.0),
        _receipt(100, "D2b", "2026-03-04", 200.0),
        # --- redacted three ways
        _receipt(100, "D3", "2026-04-15", 1200.0),
        _receipt(101, "D4", "2026-05-05", 900.0),
        _receipt(101, "D5", "2026-05-06", 400.0),
        # --- organizations; O1 also gives in kind (750 in WIN, 250 after, an archived 5000) and to outsider 300
        _receipt(100, "O1", "2026-01-15", 4000.0),
        _receipt(100, "O1", "2026-02-15", 750.0, part="5A"),
        _receipt(100, "O1", "2026-07-02", 250.0, part="5A"),
        _receipt(300, "O1", "2026-02-01", 1000.0),
        _receipt(200, "O2", "2026-05-20", 2500.0),
        _receipt(100, "O3", "2026-03-20", 1500.0),
        _receipt(200, "O3", "2026-03-21", 1500.0),
        # --- window edges (inclusive) and one day outside each
        _receipt(200, "B1", "2026-01-01", 100.0),
        _receipt(200, "B2", "2026-06-30", 200.0),
        _receipt(200, "Early", "2025-12-31", 300.0),
        _receipt(200, "Late", "2026-07-01", 400.0),
        # --- 3 x 0.10 sums to 0.30000000000000004 in binary floating point
        _receipt(100, "Penny", "2026-02-01", 0.1),
        _receipt(100, "Penny", "2026-02-02", 0.1),
        _receipt(100, "Penny", "2026-02-03", 0.1),
        _receipt(200, "Ina", "2026-03-15", 300.0, part="5A"),
        _receipt(102, "Fay", "2026-03-03", 1100.0),
        # --- counts for nothing
        _receipt(100, "Zed", "2026-03-01", 0.0),
        _receipt(100, "Nate", "2026-03-01", -250.0),
        _receipt(100, "Archie", "2026-03-02", 6000.0, archived=True),
        _receipt(100, "Arlo", "2026-03-02", 5000.0, part="5A", archived=True),
        _receipt(100, "Lou", "2026-03-02", 10000.0, part="3A"),
        _receipt(100, "Ivy", "2026-03-02", 50.0, part="4A"),
        _receipt(300, "Otto", "2026-03-02", 7000.0),
        # --- 2A transfers in: payer name in last_name, first_name '' or NULL
        _payer(100, "PRAIRIE STATE LEADERSHIP FUND ", "", "2026-03-12", 2000.0),      # = committee 400 after LOWER/TRIM
        _payer(200, "Prairie State Leadership Fund", None, "2026-04-02", 700.0),
        _payer(100, "Prairie State Leadership Fund", None, "2025-12-31", 1300.0),
        _payer(101, "Prairie State Leadership Fund Inc", None, "2026-05-18", 800.0),   # near match: stays unresolved
        _payer(100, "Twin Name Committee", None, "2026-02-02", 500.0),                 # matches 500 AND 501
        _payer(100, "Ghost Transfer Fund", None, "2026-03-03", 3000.0, archived=True),
        _payer(100, "Refunded Transfer Fund", None, "2026-03-04", -100.0),
    ]


def _expenditure_rows() -> list[dict]:
    return [
        # --- vendor "Prairie Print Co": NULL first_name / lower-case, '' first_name / upper-case with a trailing space
        _spend(100, "Prairie Print Co", "2026-02-12", 600.0),
        _spend(101, "PRAIRIE PRINT CO ", "2026-03-08", 500.0, payee_first=""),
        _spend(100, "Prairie Print Co", "2026-02-13", 9999.0, archived=True),
        _spend(100, "Prairie Print Co", "2026-04-02", -80.0),
        _spend(100, "Prairie Print Co", "2025-12-31", 1000.0),
        _spend(101, "Prairie Print Co", "2026-07-01", 2000.0),
        _spend(300, "Prairie Print Co", "2026-03-03", 7000.0),
        _spend(102, "Prairie Print Co", "2026-02-14", 250.0),
        _spend(200, "Lakeshore Media Group", "2026-04-20", 900.0),
        _spend(101, "Wexler", "2026-05-30", 300.0, payee_first="Wren"),
        # --- vendors nobody paid in a counting row
        _spend(100, "Ghost Printing", "2026-03-10", 400.0, archived=True),
        _spend(100, "Refund Vendor", "2026-03-11", -75.0),
        _spend(100, "Zero Vendor", "2026-03-12", 0.0),
        # --- Part 9 paid BY an entity committee, naming the outsider candidate: no vendor, no IE edge
        _spend(200, "Lakeshore Mailers", "2026-05-01", 400.0, part="9B", candidate_name="Cal Outsider", direction="S"),
        # --- Part 9 of spender 900 (payee 'Mailhouse Direct' must never become a vendor)
        _ie(900, "2026-01-01", 120.0, "Ada Example", "S"),
        _ie(900, "2026-02-05", 5000.0, "Ada Example", "S"),
        _ie(900, "2026-03-03", 8000.0, "Ada Example", "S", archived=True),
        _ie(900, "2026-03-10", 2000.0, "Bo Rival", "O"),
        _ie(900, "2026-03-30", 75.0, "ADA EXAMPLE", "O"),
        _ie(900, "2026-04-04", 1000.0, "Ada Example and Bo Rival", "S"),
        _ie(900, "2026-04-14", 900.0, "Ada Example; Bo Rival; Cal Outsider", "O"),
        _ie(900, "2026-04-24", 2500.0, "Cal Outsider", "S"),
        _ie(900, "2026-05-05", 1500.0, "Ada Example", "S", part="9A"),
        _ie(900, "2026-06-30", 130.0, "Bo Rival", "O"),
        _ie(900, "2026-07-01", 650.0, "Bo Rival", "O"),
        _ie(900, "2025-12-31", 700.0, "Ada Example", "S"),
        _ie(900, "2023-05-05", 700.0, "Ada Example", "S"),     # cycle 2024: Ada has no 2024 candidacy
        _ie(900, "2023-05-05", 400.0, "Bo Rival", "O"),        # cycle 2024: Bo's 2023 candidacy
        _ie(905, "2026-03-01", 50.0, "Bo Rival", "S"),
        _ie(901, "2026-02-02", -300.0, "Ada Example", "S"),
        _ie(901, "2026-02-03", 0.0, "Bo Rival", "O"),
    ]


def _seed_activity(conn) -> None:
    _insert(conn, "isbe_condensed_receipts", _receipt_rows(), first_id=1)
    _insert(conn, "isbe_condensed_expenditures", _expenditure_rows(), first_id=1)


@pytest.fixture()
def reference(tmp_path):
    """Tables plus candidates / candidacies / committees, no money rows."""
    db_path = str(tmp_path / "entity_network.db")
    init_db(db_path)
    conn = get_db(db_path)
    for statement in _DDL:
        conn.execute(statement)
    _seed_reference(conn)
    conn.commit()
    return conn


@pytest.fixture()
def world(reference):
    _seed_activity(reference)
    reference.commit()
    return reference


def _call(conn, entity, **kwargs):
    return get_entity_network(conn, copy.deepcopy(entity), **kwargs)


def _edge_map(payload) -> dict:
    mapping = {}
    for edge in payload["edges"]:
        key = (edge["edge_type"], edge["source"], edge["target"], edge.get("support_oppose"))
        assert key not in mapping, f"duplicate edge {key}"
        mapping[key] = (edge["weight"], edge["count"])
    return mapping


def _edge_problems(actual: dict, expected: dict) -> list[str]:
    problems = []
    for key in sorted(set(expected) - set(actual), key=str):
        problems.append(f"missing {key}")
    for key in sorted(set(actual) - set(expected), key=str):
        problems.append(f"unexpected {key} -> {actual[key]}")
    for key in sorted(set(actual) & set(expected), key=str):
        weight, count = actual[key]
        want_weight, want_count = expected[key]
        if weight != pytest.approx(want_weight, abs=0.005):
            problems.append(f"{key}: weight {weight} != {want_weight}")
        if want_count is not None and count != want_count:
            problems.append(f"{key}: count {count} != {want_count}")
    return problems


def _nodes(payload) -> dict:
    return {node["id"]: node for node in payload["nodes"]}


def _only(expected: dict, edge_type: str) -> dict:
    return {key: value for key, value in expected.items() if key[0] == edge_type}


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------

def test_default_caps_constant():
    assert DEFAULT_CAPS == {"donors": 40, "vendors": 25, "transfers": 15, "ie_spenders": 15}


@pytest.mark.parametrize("entity", [RACE_ENTITY, CANDIDATE_ENTITY], ids=["race", "candidate"])
def test_payload_shape_entity_echo_and_json(world, entity):
    """Exactly the five top-level and six summary keys; the entity comes back equal and is not mutated."""
    pristine = copy.deepcopy(entity)
    given = copy.deepcopy(entity)
    payload = get_entity_network(world, given, date_from=WIN[0], date_to=WIN[1])
    assert set(payload) == TOP_LEVEL_KEYS
    assert set(payload["summary"]) == SUMMARY_KEYS
    assert payload["entity"] == pristine
    assert given == pristine, "get_entity_network mutated the entity it was given"
    assert DEFAULT_CAPS == {"donors": 40, "vendors": 25, "transfers": 15, "ie_spenders": 15}, "caps=None edited DEFAULT_CAPS"
    json.dumps(payload)   # the Network tab serves this as JSON: no Decimal, date, tuple-key


@pytest.mark.parametrize("window", [(None, None), WIN], ids=["unbounded", "window"])
def test_empty_committee_list_yields_empty_graph(world, window):
    """candidate_ids is non-empty and spender 900 has Part-9 rows for both: still no committee, no graph."""
    payload = _call(world, EMPTY_ENTITY, date_from=window[0], date_to=window[1])
    assert payload["entity"] == EMPTY_ENTITY
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["centrality"] == []
    assert payload["summary"]["node_count"] == 0
    assert payload["summary"]["edge_count"] == 0
    assert payload["summary"]["edges_by_type"] == {}
    assert all(dropped == 0 for dropped in payload["summary"]["truncated"].values())


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def test_race_nodes_are_exactly_the_one_hop_neighbourhood(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    ids = [node["id"] for node in payload["nodes"]]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    assert set(ids) == RACE_WIN_NODE_IDS
    assert len(ids) == 26


def test_candidate_nodes_include_the_family_member_and_only_this_candidate(world):
    payload = _call(world, CANDIDATE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    ids = {node["id"] for node in payload["nodes"]}
    assert ids == CAND_WIN_NODE_IDS
    assert len(ids) == 20
    for missing in (C200, CAND2, C905, D["O2"], D["Ina"], D["B1"], V_B):
        assert missing not in ids, f"{missing} belongs to the other candidate of the race"


def test_node_vocabulary(world):
    """node_type and role per node class; node keys limited to the contract's (id, label, node_type, role,
    group_label, total_amount, redacted): no city / state / zip for anyone."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    expected = {C100: ("committee", "entity"), C101: ("committee", "entity"), C200: ("committee", "entity"),
                CAND1: ("candidate", "candidate"), CAND2: ("candidate", "candidate"),
                C900: ("committee", "ie_spender"), C905: ("committee", "ie_spender"),
                C400: ("committee", "transfer_source")}
    for name in ("D1", "D1b", "D2a", "D3", "D4", "D5", "O1", "O2", "O3", "B1", "B2", "Penny", "Ina"):
        expected[D[name]] = ("donor", "donor")
    for vendor in (V_A, V_B, V_P):
        expected[vendor] = ("vendor", "vendor")
    nodes = _nodes(payload)
    problems = []
    for node_id, (node_type, role) in expected.items():
        node = nodes[node_id]
        if (node["node_type"], node["role"]) != (node_type, role):
            problems.append(f"{node_id}: {(node['node_type'], node['role'])} != {(node_type, role)}")
    assert not problems
    for node in payload["nodes"]:
        assert NODE_REQUIRED <= set(node), f"{node['id']} lacks {NODE_REQUIRED - set(node)}"
        assert set(node) <= NODE_ALLOWED, f"{node['id']} has undeclared keys {set(node) - NODE_ALLOWED}"
        assert node["node_type"] in ("donor", "committee", "candidate", "vendor")
        assert node["role"] in ("entity", "candidate", "donor", "vendor", "transfer_source", "ie_spender")


def test_unresolved_transfer_payers_are_committee_transfer_sources(world):
    """R4: the contract's node_type vocabulary has no 'transfer'; an unresolved payer is a committee."""
    nodes = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    for node_id, name in ((T_INC, "Prairie State Leadership Fund Inc"), (T_TWIN, "Twin Name Committee")):
        assert nodes[node_id]["node_type"] == "committee"
        assert nodes[node_id]["role"] == "transfer_source"
        assert nodes[node_id]["label"].strip().lower() == name.lower()


def test_entity_committee_nodes_carry_their_group_label(world):
    race = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert race[C100]["group_label"] == "Ada Example"
    assert race[C101]["group_label"] == "Ada Example"
    assert race[C200]["group_label"] == "Bo Rival"
    candidate = _nodes(_call(world, CANDIDATE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert candidate[C100]["group_label"] == "Friends of Ada Example"
    assert candidate[C101]["group_label"] == "Victory Fund family"
    assert candidate[C102]["group_label"] == "Victory Fund family"


def test_node_labels(world):
    nodes = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert nodes[C100]["label"] == "Friends of Ada Example"
    assert nodes[C200]["label"] == "Bo Rival for Illinois"
    assert nodes[C900]["label"] == "Statewide Action PAC"
    assert nodes[C400]["label"] == "Prairie State Leadership Fund"
    assert nodes[CAND1]["label"] in ("Ada Q. Example", "Ada Example")   # ISBE record or the group label
    assert nodes[CAND2]["label"] == "Bo Rival"
    for name in ("D1", "D1b", "O1", "O2", "O3", "B1", "D3", "Penny"):    # TRIM(first || ' ' || last), NULL/'' first name
        assert nodes[D[name]]["label"] == donor_label(DONORS[name]), name
    assert nodes[D["D2a"]]["label"].lower() == "eli egan"
    assert nodes[V_A]["label"].strip().lower() == "prairie print co"
    assert nodes[V_B]["label"].strip().lower() == "lakeshore media group"


def test_donor_identity_is_the_donor_key(world):
    """Same name at two addresses = two donors; two rows whose names differ only in case = one donor, id lower-cased,
    the whole-string TRIM keeps the space inside 'apt 2 |'."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    assert D["D2a"] == D["D2b"] == "donor:eli|egan|5 pine rd|apt 2 |peoria|il|61602"
    assert D["D1"] != D["D1b"]
    nodes = _nodes(payload)
    assert D["D1"] in nodes
    assert D["D1b"] in nodes
    eli_nodes = [node_id for node_id in nodes if node_id.startswith("donor:eli|")]
    assert eli_nodes == [D["D2a"]]
    assert D["O1"].startswith("donor:|acme union local 9|")   # NULL first_name: empty first field, key keeps the pipe
    assert D["O2"].startswith("donor:|prairie builders llc|")   # '' first_name keys the same way as NULL


@pytest.mark.parametrize("donor", sorted(REDACTED))
def test_redacted_donor_keeps_name_and_amount_but_hides_the_address(world, donor):
    """R10. D3: ISBE format (flag, NULL address). D4: flag TRUE but the row still holds an address. D5: only the
    'Redaction Requested' marker in address1, flag FALSE, city/zip present. The lazy node copies city/state/zip."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    node = _nodes(payload)[D[donor]]
    assert node["redacted"] is True
    assert node["label"] == donor_label(DONORS[donor])
    for field in ("city", "state", "zip", "zip5", "zipcode", "address", "address1", "address2"):
        assert node.get(field) in (None, ""), f"{field} leaked on the redacted node"
    secrets = [token for token in (DONORS[donor]["a1"], DONORS[donor]["city"], DONORS[donor]["zipcode"])
               if token and token != "Redaction Requested"]
    others = {key: value for key, value in node.items() if key != "id"}
    text = json.dumps(others).lower()
    for token in secrets:
        assert token.lower() not in text, f"{token!r} found on the redacted node"
    committee = {"D3": C100, "D4": C101, "D5": C101}[donor]     # the committee each of them gave to
    weight = RACE_WIN[(DC, D[donor], committee, None)][0]          # 1200, 900, 400
    assert node["total_amount"] == pytest.approx(weight, abs=0.005)


def test_other_donors_are_not_marked_redacted(world):
    nodes = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    for name in ("D1", "D1b", "D2a", "O1", "O2", "O3", "B1", "Penny", "Ina"):
        assert not nodes[D[name]].get("redacted"), name


@pytest.mark.parametrize("node_id,expected_total", [
    pytest.param(D["D1"], 6000.0, id="D1: 5000 to 100 + 1000 to 101"),
    pytest.param(D["D1b"], 2500.0, id="D1b"),
    pytest.param(D["D2a"], 1000.0, id="D2: 800 + 200"),
    pytest.param(D["D3"], 1200.0, id="D3 redacted keeps its amount"),
    pytest.param(D["O3"], 3000.0, id="O3: 1500 to each of 100 and 200"),
    pytest.param(D["Penny"], 0.3, id="Penny"),
    pytest.param(D["Ina"], 300.0, id="Ina in-kind only"),
    pytest.param(V_A, 1100.0, id="vendor paid by two committees: 600 + 500"),
    pytest.param(V_B, 900.0, id="vendor B"),
    pytest.param(V_P, 300.0, id="vendor P"),
    pytest.param(T_INC, 800.0, id="unresolved transfer source"),
    pytest.param(T_TWIN, 500.0, id="unresolved twin-name transfer source"),
])
def test_leaf_node_total_amount_is_the_sum_of_its_edges(world, node_id, expected_total):
    """R1. A global total (the donor's 50000 to the outside committee 300, the vendor's 7000 from 300) must not show."""
    nodes = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert nodes[node_id]["total_amount"] == pytest.approx(expected_total, abs=0.005)


def test_rows_that_must_count_for_nothing_leave_no_node(world):
    absent = {
        "zero 1A": D["Zed"], "negative 1A only": D["Nate"], "archived 1A": D["Archie"], "archived 5A": D["Arlo"],
        "3A loan": D["Lou"], "4A other receipt": D["Ivy"], "gift to outside committee": D["Otto"],
        "outside committee 300": C300, "family-only donor (102 is not in the race)": D["Fay"],
        "family member 102": C102, "candidate outside the entity": CAND3,
        "archived 2A": transfer_id("Ghost Transfer Fund"), "negative 2A": transfer_id("Refunded Transfer Fund"),
        "twin-name committee 500": C500, "twin-name committee 501": C501,
        "archived vendor row": vendor_id("Ghost Printing"), "negative vendor row": vendor_id("Refund Vendor"),
        "zero vendor row": vendor_id("Zero Vendor"),
        "Part-9 payment made BY an entity committee": vendor_id("Lakeshore Mailers"),
        "payee of the outside spender's Part-9 rows": vendor_id("Mailhouse Direct"),
        "spender with only a negative and a zero Part-9 row": C901,
        "donor dated the day before date_from": D["Early"], "donor dated the day after date_to": D["Late"],
    }
    ids = {node["id"] for node in _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])["nodes"]}
    leaked = {reason: node_id for reason, node_id in absent.items() if node_id in ids}
    assert not leaked


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("edge_type", EDGE_TYPES)
def test_race_edges_in_window_by_type(world, edge_type):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    actual = {key: value for key, value in _edge_map(payload).items() if key[0] == edge_type}
    assert not _edge_problems(actual, _only(RACE_WIN, edge_type))


def test_race_edges_have_no_other_type_and_exact_total(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    assert Counter(edge["edge_type"] for edge in payload["edges"]) == Counter(key[0] for key in RACE_WIN)
    assert len(payload["edges"]) == 32


def test_candidate_entity_edges_in_window(world):
    payload = _call(world, CANDIDATE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    assert not _edge_problems(_edge_map(payload), CAND_WIN)
    assert len(payload["edges"]) == 25


def test_race_edges_without_a_window(world):
    payload = _call(world, RACE_ENTITY)
    assert not _edge_problems(_edge_map(payload), RACE_ALL)
    assert len(payload["edges"]) == 34


def test_edge_directions_scope_and_referential_integrity(world):
    """Every edge ends at nodes of the payload; donor/transfer edges end at entity committees; the entity's own
    committee/candidate sets bound committee_vendor, committee_candidate and independent_expenditure targets."""
    for entity, window in ((RACE_ENTITY, WIN), (RACE_ENTITY, (None, None)), (CANDIDATE_ENTITY, WIN)):
        payload = _call(world, entity, date_from=window[0], date_to=window[1])
        node_ids = {node["id"] for node in payload["nodes"]}
        committees = {f"committee:{cid}" for cid in entity["committee_ids"]}
        candidates = {f"candidate:{cid}" for cid in entity["candidate_ids"]}
        for edge in payload["edges"]:
            where = (edge["edge_type"], edge["source"], edge["target"])
            assert edge["source"] in node_ids, where
            assert edge["target"] in node_ids, where
            if edge["edge_type"] in (DC, IK):
                assert edge["source"].startswith("donor:"), where
                assert edge["target"] in committees, where
            elif edge["edge_type"] == TI:
                assert edge["source"].startswith(("committee:", "transfer:")), where
                assert edge["target"] in committees, where
            elif edge["edge_type"] == CV:
                assert edge["source"] in committees, where
                assert edge["target"].startswith("vendor:"), where
            elif edge["edge_type"] == IE:
                assert edge["source"].startswith("committee:"), where
                assert edge["target"] in candidates, where
            elif edge["edge_type"] == CC:
                assert edge["source"] in committees, where
                assert edge["target"] in candidates, where
            else:
                pytest.fail(f"unknown edge type {where}")


def test_transfer_payer_resolves_only_on_an_exact_unique_committee_name(world):
    """'PRAIRIE STATE LEADERSHIP FUND ' (upper-case, padded) = committee 400; '... Fund Inc' is not (exact only);
    'Twin Name Committee' names committees 500 AND 501, so it resolves to neither (no LIMIT 1)."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    transfers = {key: value for key, value in _edge_map(payload).items() if key[0] == TI}
    sources = {key[1] for key in transfers}
    assert sources == {C400, T_INC, T_TWIN}
    assert (TI, C400, C100, None) in transfers
    assert (TI, C400, C200, None) in transfers      # a second entity committee, spelled differently
    assert (TI, T_INC, C101, None) in transfers
    assert (TI, T_TWIN, C100, None) in transfers
    nodes = _nodes(payload)
    assert nodes[C400]["role"] == "transfer_source"
    assert C500 not in nodes and C501 not in nodes


def test_vendor_node_merges_case_whitespace_and_null_versus_empty_first_name(world):
    """Two committees, 'Prairie Print Co' (NULL first) and 'PRAIRIE PRINT CO ' ('' first): ONE vendor node."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    vendors = [node for node in payload["nodes"] if node["node_type"] == "vendor"]
    printers = [node for node in vendors if node["label"].strip().lower() == "prairie print co"]
    assert [node["id"] for node in printers] == [V_A]
    into_vendor = {edge["source"]: edge["weight"] for edge in payload["edges"]
                   if edge["edge_type"] == CV and edge["target"] == V_A}
    assert into_vendor == {C100: pytest.approx(600.0), C101: pytest.approx(500.0)}


def test_individual_payee_is_first_name_then_last_name(world):
    """R5: a payee with a first name is 'first last' (the donor display-name rule), hashed lower-cased."""
    nodes = _nodes(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert V_P == "vendor:" + hashlib.sha1(b"wren wexler").hexdigest()[:16]
    assert nodes[V_P]["label"].strip().lower() == "wren wexler"


def test_independent_expenditure_edges_are_signed_and_split(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    expected_signed = {
        (C900, CAND1, "S"): 7120.0, (C900, CAND1, "O"): -375.0, (C900, CAND2, "O"): -2430.0,
        (C900, CAND2, "S"): 500.0, (C905, CAND2, "S"): 50.0,
    }
    seen = {}
    for edge in payload["edges"]:
        if edge["edge_type"] != IE:
            assert edge.get("support_oppose") is None, f"{edge['edge_type']} carries support_oppose"
            continue
        assert edge["support_oppose"] in ("S", "O")
        assert edge["weight"] > 0
        sign = 1.0 if edge["support_oppose"] == "S" else -1.0
        assert edge["signed_weight"] == pytest.approx(sign * edge["weight"], abs=0.005)
        seen[(edge["source"], edge["target"], edge["support_oppose"])] = edge["signed_weight"]
    assert set(seen) == set(expected_signed)
    for key, signed in expected_signed.items():
        assert seen[key] == pytest.approx(signed, abs=0.005), key


@pytest.mark.parametrize("day,expected", [
    pytest.param("2026-02-05", {(IE, C900, CAND1, "S"): (5000.0, 1)},
                 id="'Ada Example' meets ISBE 'Ada Q. Example' (middle initial)"),
    pytest.param("2026-03-30", {(IE, C900, CAND1, "O"): (75.0, 1)}, id="'ADA EXAMPLE' (case) opposing"),
    pytest.param("2026-03-10", {(IE, C900, CAND2, "O"): (2000.0, 1)}, id="plain opposing"),
    pytest.param("2026-04-04", {(IE, C900, CAND1, "S"): (500.0, 1), (IE, C900, CAND2, "S"): (500.0, 1)},
                 id="two names on a row: 1000 / 2 each"),
    pytest.param("2026-04-14", {(IE, C900, CAND1, "O"): (300.0, 1), (IE, C900, CAND2, "O"): (300.0, 1)},
                 id="three names, one outside the entity: still 900 / 3 each, never 900 / 2"),
    pytest.param("2026-05-05", {(IE, C900, CAND1, "S"): (1500.0, 1)}, id="Part 9A counts (LIKE '9%')"),
    pytest.param("2026-03-01", {(IE, C905, CAND2, "S"): (50.0, 1)}, id="second spender"),
    pytest.param("2026-03-03", {}, id="archived row"),
    pytest.param("2026-04-24", {}, id="row naming only the outsider candidate"),
    pytest.param("2026-05-01", {}, id="Part 9 paid by an entity committee naming the outsider"),
    pytest.param("2026-02-02", {}, id="negative Part-9 amount"),
    pytest.param("2026-02-03", {}, id="zero Part-9 amount"),
])
def test_part9_attribution_one_day_at_a_time(world, day, expected):
    """A one-day window isolates the Part-9 row(s) of that day (every Part-9 date in the seed is unique)."""
    when = date.fromisoformat(day)
    payload = _call(world, RACE_ENTITY, date_from=when, date_to=when)
    actual = {key: value for key, value in _edge_map(payload).items() if key[0] == IE}
    assert not _edge_problems(actual, expected)


def test_part9_cycle_gate_uses_candidacy_cycles(world):
    """2023-05-05 is cycle 2024. Ada has only cycle 2026: her row stays out in every window. Bo's candidacy is
    election_year 2023 (odd year -> cycle 2024): his row attaches, even though the race entity says cycle 2026 (R6)."""
    window = date(2023, 5, 1), date(2023, 5, 31)
    race = _call(world, RACE_ENTITY, date_from=window[0], date_to=window[1])
    race_ie = {key: value for key, value in _edge_map(race).items() if key[0] == IE}
    assert not _edge_problems(race_ie, {(IE, C900, CAND2, "O"): (400.0, 1)})
    candidate = _call(world, CANDIDATE_ENTITY, date_from=window[0], date_to=window[1])
    assert not [edge for edge in candidate["edges"] if edge["edge_type"] == IE]


def test_committee_vendor_excludes_part9_even_for_an_entity_committee(world):
    """Committee 200 paid 'Lakeshore Mailers' 400 on a Part-9 row (naming an outsider): no vendor, no IE edge."""
    when = date(2026, 5, 1)
    payload = _call(world, RACE_ENTITY, date_from=when, date_to=when)
    assert not [edge for edge in payload["edges"] if edge["edge_type"] in (CV, IE)]
    assert vendor_id("Lakeshore Mailers") not in {node["id"] for node in payload["nodes"]}


def test_every_edge_carries_its_semantics(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    per_type = {}
    for edge in payload["edges"]:
        assert EDGE_REQUIRED <= set(edge), f"{edge['edge_type']} lacks {EDGE_REQUIRED - set(edge)}"
        for field in ("edge_label", "edge_description", "metric_label", "source_table"):
            assert isinstance(edge[field], str) and edge[field].strip(), (edge["edge_type"], field)
        assert edge["weight_unit"] == "usd"
        assert edge["source_table"] == SOURCE_TABLE[edge["edge_type"]], edge["edge_type"]
        fields = {field: edge[field] for field in ("edge_label", "edge_description", "weight_unit", "metric_label")}
        assert per_type.setdefault(edge["edge_type"], fields) == fields, f"{edge['edge_type']} edges disagree"
    assert set(per_type) == set(EDGE_TYPES)
    assert len({fields["edge_label"] for fields in per_type.values()}) == len(EDGE_TYPES), "labels not distinct"
    definitions = payload["summary"]["edge_type_definitions"]
    assert set(EDGE_TYPES) <= set(definitions)
    for edge_type, fields in per_type.items():
        assert definitions[edge_type] == fields, edge_type


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def test_half_open_window_up_to_a_date(world):
    payload = _call(world, RACE_ENTITY, date_to=date(2025, 12, 31))
    assert not _edge_problems(_edge_map(payload), RACE_UP_TO_2025)


def test_half_open_window_from_a_date(world):
    payload = _call(world, RACE_ENTITY, date_from=date(2026, 7, 1))
    assert not _edge_problems(_edge_map(payload), RACE_FROM_JULY)


@pytest.mark.parametrize("day,expected", [
    pytest.param("2026-01-01", {(DC, D["B1"], C200, None): (100.0, 1), (IE, C900, CAND1, "S"): (120.0, 1),
                                (CC, C200, CAND2, None): (100.0, None)}, id="date_from day"),
    pytest.param("2026-06-30", {(DC, D["B2"], C200, None): (200.0, 1), (IE, C900, CAND2, "O"): (130.0, 1),
                                (CC, C200, CAND2, None): (200.0, None)}, id="date_to day"),
])
def test_window_is_inclusive_on_both_ends_for_every_source(world, day, expected):
    when = date.fromisoformat(day)
    payload = _call(world, RACE_ENTITY, date_from=when, date_to=when)
    assert not _edge_problems(_edge_map(payload), expected)


@pytest.mark.parametrize("date_from,date_to", [
    pytest.param(None, None, id="unbounded"),
    pytest.param(date(2026, 1, 1), None, id="from only"),
    pytest.param(None, date(2026, 6, 30), id="to only"),
    pytest.param(date(2026, 1, 1), date(2026, 6, 30), id="both"),
])
def test_summary_window_echoes_iso_dates(world, date_from, date_to):
    window = _call(world, RACE_ENTITY, date_from=date_from, date_to=date_to)["summary"]["window"]
    assert set(window) == {"date_from", "date_to"}
    for key, given in (("date_from", date_from), ("date_to", date_to)):
        if given is None:
            assert window[key] is None
        else:
            assert isinstance(window[key], str)
            assert date.fromisoformat(window[key]) == given


# ---------------------------------------------------------------------------
# Summary, numbers, ordering, centrality
# ---------------------------------------------------------------------------

def test_summary_counts_match_the_lists(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    summary = payload["summary"]
    assert type(summary["node_count"]) is int
    assert type(summary["edge_count"]) is int
    assert summary["node_count"] == len(payload["nodes"]) == 26
    assert summary["edge_count"] == len(payload["edges"]) == 32
    assert summary["edges_by_type"] == {DC: 14, IK: 2, TI: 4, CV: 4, IE: 5, CC: 3}
    for cls in ("donors", "vendors", "transfers", "ie_spenders"):
        assert summary["truncated"].get(cls, 0) == 0, f"{cls} truncated under the default caps"


def test_numbers_are_floats_rounded_to_cents_and_counts_are_ints(world):
    """3 x 0.10 sums to 0.30000000000000004 in floating point: the contract says money is rounded to cents."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    penny = [edge for edge in payload["edges"] if edge["source"] == D["Penny"]]
    assert len(penny) == 1
    assert penny[0]["weight"] == 0.3
    assert penny[0]["count"] == 3
    for node in payload["nodes"]:
        assert type(node["total_amount"]) is float, node["id"]
    for edge in payload["edges"]:
        assert type(edge["weight"]) is float, edge
        assert type(edge["count"]) is int, edge
        assert edge["weight"] == round(edge["weight"], 2), edge
        if "signed_weight" in edge:
            assert type(edge["signed_weight"]) is float, edge
    for row in payload["centrality"]:
        assert type(row["weighted_degree"]) is float, row
        assert type(row["degree"]) is int, row


def test_lists_are_sorted_amount_desc_and_repeatable(world):
    """R8 (the 'Shared conventions' sentence). Same call twice gives the identical payload."""
    first = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    second = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    assert first == second
    weights = [edge["weight"] for edge in first["edges"]]
    assert weights == sorted(weights, reverse=True)
    totals = [node["total_amount"] for node in first["nodes"]]
    assert totals == sorted(totals, reverse=True)
    degrees = [row["weighted_degree"] for row in first["centrality"]]
    assert degrees == sorted(degrees, reverse=True)


def test_centrality_is_derived_from_the_payload_edges(world):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    weighted = defaultdict(float)
    degree = Counter()
    for edge in payload["edges"]:
        for end in (edge["source"], edge["target"]):
            weighted[end] += edge["weight"]
            degree[end] += 1
    rows = payload["centrality"]
    assert len(rows) == len(payload["nodes"]) == 26     # <= 100, so every node is listed
    assert {row["node_id"] for row in rows} == {node["id"] for node in payload["nodes"]}
    nodes = _nodes(payload)
    for row in rows:
        assert CENTRALITY_KEYS <= set(row) <= CENTRALITY_KEYS | {"system"}
        assert row["label"] == nodes[row["node_id"]]["label"]
        assert row["node_type"] == nodes[row["node_id"]]["node_type"]
        assert row["weighted_degree"] == pytest.approx(weighted[row["node_id"]], abs=0.011), row["node_id"]
        assert row["degree"] == degree[row["node_id"]], row["node_id"]
    top = rows[0]
    # committee 100: 15200.30 of 1A + 750 in kind + 2000 + 500 transfers + 600 vendor + the committee_candidate
    # edge 15200.30 = 34250.60 over 7 + 1 + 2 + 1 + 1 = 12 edges
    assert top["node_id"] == C100
    assert top["weighted_degree"] == pytest.approx(34250.6, abs=0.011)
    assert top["degree"] == 12


def test_centrality_is_limited_to_100_and_caps_can_exceed_the_defaults(reference):
    """130 donors > the default 40: caps={'donors': 200, ...} keeps all 130 nodes; centrality still lists 100."""
    rows = [_receipt(100, _donor("Many", f"Donor{i:03d}"), "2026-03-01", 1000.0 + i) for i in range(130)]
    _insert(reference, "isbe_condensed_receipts", rows, first_id=1)
    reference.commit()
    caps = {"donors": 200, "vendors": 25, "transfers": 15, "ie_spenders": 15}
    payload = _call(reference, BULK_ENTITY, caps=caps)
    donors = [node for node in payload["nodes"] if node["role"] == "donor"]
    assert len(donors) == 130
    assert len(payload["nodes"]) == 132         # + committee 100 + candidate 1
    assert len(payload["centrality"]) == 100
    listed = [row["node_id"] for row in payload["centrality"]]
    top_donors = {"donor:" + donor_key(_donor("Many", f"Donor{i:03d}")) for i in range(32, 130)}   # amounts 1032..1129
    assert set(listed) == {C100, CAND1} | top_donors
    assert payload["summary"]["node_count"] == 132
    assert payload["summary"]["truncated"].get("donors", 0) == 0


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

def test_explicit_caps_keep_the_top_neighbours_by_total_weight(world):
    """donors=2: D1 (6000) and O1 (4000 + 750 in kind) beat O3 (3000). vendors=1: Prairie Print Co (600 + 500 = 1100
    over two committees) beats Lakeshore Media Group (900), the largest SINGLE edge. transfers=1: committee 400
    (2000 + 700) -- the resolved payer counts. ie_spenders=1: 900 beats 905. Entity committees, candidates and
    committee_candidate edges are never capped."""
    caps = {"donors": 2, "vendors": 1, "transfers": 1, "ie_spenders": 1}
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1], caps=caps)
    assert caps == {"donors": 2, "vendors": 1, "transfers": 1, "ie_spenders": 1}, "the caller's caps dict was edited"
    survivors = {D["D1"], D["O1"], V_A, C400, C900}
    kept_ids = {C100, C101, C200, CAND1, CAND2} | survivors
    assert {node["id"] for node in payload["nodes"]} == kept_ids
    expected = {key: value for key, value in RACE_WIN.items() if key[1] in kept_ids and key[2] in kept_ids}
    assert len(expected) == 15
    assert not _edge_problems(_edge_map(payload), expected)
    truncated = payload["summary"]["truncated"]
    assert truncated.get("donors", 0) == 11        # 13 donor nodes - 2
    assert truncated.get("vendors", 0) == 2
    assert truncated.get("transfers", 0) == 2
    assert truncated.get("ie_spenders", 0) == 1
    assert payload["summary"]["node_count"] == len(payload["nodes"]) == 10
    assert payload["summary"]["edge_count"] == len(payload["edges"]) == 15
    node_ids = {node["id"] for node in payload["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in payload["edges"])


_BULK_CASES = [
    pytest.param("donors", 40, "donor", id="donors"),
    pytest.param("vendors", 25, "vendor", id="vendors"),
    pytest.param("transfers", 15, "transfer_source", id="transfers"),
    pytest.param("ie_spenders", 15, "ie_spender", id="ie_spenders"),
]


def _bulk_amounts(count: int) -> list[float]:
    return [9000.0, 8000.0] + [100.0] * (count - 2)    # two clear leaders, then one big tie


def _bulk_sources(cls: str, count: int) -> list[tuple[float, str, str]]:
    """(weight, label, node id) per source, index order."""
    amounts = _bulk_amounts(count)
    sources = []
    for i in range(count):
        if cls == "donors":
            donor = _donor("Bulk", f"Donor{i:02d}")
            sources.append((amounts[i], donor_label(donor), "donor:" + donor_key(donor)))
        elif cls == "vendors":
            name = f"Bulk Vendor {i:02d}"
            sources.append((amounts[i], name, vendor_id(name)))
        elif cls == "transfers":
            name = f"Bulk Payer {i:02d}"
            sources.append((amounts[i], name, transfer_id(name)))
        else:
            sources.append((amounts[i], f"Bulk Spender {i:02d}", f"committee:{3000 + i}"))
    return sources


def _seed_bulk(conn, cls: str, count: int) -> None:
    """``count`` neighbours of one class around committee 100, inserted in REVERSE index order."""
    amounts = _bulk_amounts(count)
    order = list(range(count))[::-1]
    if cls == "donors":
        rows = [_receipt(100, _donor("Bulk", f"Donor{i:02d}"), "2026-03-01", amounts[i]) for i in order]
        _insert(conn, "isbe_condensed_receipts", rows, first_id=1)
    elif cls == "vendors":
        rows = [_spend(100, f"Bulk Vendor {i:02d}", "2026-03-01", amounts[i]) for i in order]
        _insert(conn, "isbe_condensed_expenditures", rows, first_id=1)
    elif cls == "transfers":
        rows = [_payer(100, f"Bulk Payer {i:02d}", None, "2026-03-01", amounts[i]) for i in order]
        _insert(conn, "isbe_condensed_receipts", rows, first_id=1)
    else:
        _insert(conn, "isbe_committees", [_committee_row(3000 + i, f"Bulk Spender {i:02d}") for i in order])
        rows = [_ie(3000 + i, "2026-03-01", amounts[i], "Ada Example", "S") for i in order]
        _insert(conn, "isbe_condensed_expenditures", rows, first_id=1)
    conn.commit()


@pytest.mark.parametrize("cls,default,role", _BULK_CASES)
def test_caps_none_applies_the_default_cap_with_a_deterministic_tie_break(reference, cls, default, role):
    """default + 3 neighbours: two leaders, the rest tied at 100 and inserted in reverse order. caps=None keeps exactly
    `default` of them (a missing default, a wrong number, or an arbitrary tie-break fails), drops 3 and reports 3."""
    count = default + 3
    _seed_bulk(reference, cls, count)
    payload = _call(reference, BULK_ENTITY)
    sources = _bulk_sources(cls, count)
    by_label = {item[2] for item in sorted(sources, key=lambda item: (-item[0], item[1]))[:default]}
    by_id = {item[2] for item in sorted(sources, key=lambda item: (-item[0], item[2]))[:default]}
    kept = {node["id"] for node in payload["nodes"] if node["role"] == role}
    assert len(kept) == default
    assert kept in (by_label, by_id), "survivors are not the top by weight with a label-or-id ascending tie-break"
    assert payload["summary"]["truncated"].get(cls, 0) == 3
    for other in ("donors", "vendors", "transfers", "ie_spenders"):
        if other != cls:
            assert payload["summary"]["truncated"].get(other, 0) == 0
    dropped = {item[2] for item in sources} - kept
    assert len(dropped) == 3
    for edge in payload["edges"]:
        assert edge["source"] not in dropped and edge["target"] not in dropped, "an edge of a dropped neighbour"
    assert payload["summary"]["node_count"] == len(payload["nodes"])
    assert payload["summary"]["edge_count"] == len(payload["edges"])


# ---------------------------------------------------------------------------
# Phase A views (plan step 8): money_flow / vendor_network / ego_force are pure projections of the ego payload.
# Expected sums are the RACE_WIN edges regrouped by hand: individuals (D1, D1b, D2a, D3, D4, D5, B1, B2, Penny)
# give 9,700.30 to 100 (5000 + 2500 + 1000 + 1200 + 0.30), 2,300 to 101 (1000 + 900 + 400) and 300 to 200
# (100 + 200); organizations (O1, O2, O3: no filed first name) give 5,500 to 100 (4000 + 1500) and 4,000 to 200
# (2500 + 1500); in-kind 750 to 100 (O1) and 300 to 200 (Ina); transfer sources 400 (2000 + 700 = 2700), the
# unresolved Inc payer 800 and the twin payer 500; vendors Prairie Print Co 1,100 (600 + 500), Lakeshore 900,
# Wren Wexler 300; spenders 900 (7120 S + 375 O on Ada, 2430 O + 500 S on Bo = 10,425) and 905 (50 S on Bo).
# ---------------------------------------------------------------------------

from database.entity_network import donor_class, ego_force, money_flow, vendor_network  # noqa: E402

CLASS_INDIVIDUAL, CLASS_ORGANIZATION, CLASS_IN_KIND = "class:individual", "class:organization", "class:in_kind"

RACE_MONEY_FLOW_LINKS = {
    (CLASS_INDIVIDUAL, C100, DC, None): (9700.3, 9),
    (CLASS_INDIVIDUAL, C101, DC, None): (2300.0, 3),
    (CLASS_INDIVIDUAL, C200, DC, None): (300.0, 2),
    (CLASS_ORGANIZATION, C100, DC, None): (5500.0, 2),
    (CLASS_ORGANIZATION, C200, DC, None): (4000.0, 2),
    (CLASS_IN_KIND, C100, IK, None): (750.0, 1),
    (CLASS_IN_KIND, C200, IK, None): (300.0, 1),
    (C400, C100, TI, None): (2000.0, 1),
    (C400, C200, TI, None): (700.0, 1),
    (T_INC, C101, TI, None): (800.0, 1),
    (T_TWIN, C100, TI, None): (500.0, 1),
    (C100, V_A, CV, None): (600.0, 1),
    (C101, V_A, CV, None): (500.0, 1),
    (C101, V_P, CV, None): (300.0, 1),
    (C200, V_B, CV, None): (900.0, 1),
    (C900, CAND1, IE, "S"): (7120.0, 4),
    (C900, CAND1, IE, "O"): (375.0, 2),
    (C900, CAND2, IE, "O"): (2430.0, 3),
    (C900, CAND2, IE, "S"): (500.0, 1),
    (C905, CAND2, IE, "S"): (50.0, 1),
}


def _link_map(view) -> dict:
    mapping = {}
    for link in view["links"]:
        key = (link["source"], link["target"], link["edge_type"], link.get("support_oppose"))
        assert key not in mapping, f"duplicate link {key}"
        mapping[key] = (link["weight"], link["count"])
    return mapping


def _sum_by_type(edges) -> dict:
    totals = defaultdict(float)
    for edge in edges:
        totals[edge["edge_type"]] += edge["weight"]
    return {edge_type: round(total, 2) for edge_type, total in totals.items()}


@pytest.mark.parametrize("node_id, expected", [
    (D["D1"], "individual"), (D["Penny"], "individual"), (D["D3"], "individual"),
    (D["O1"], "organization"), (D["O2"], "organization"), (D["O3"], "organization"),
])
def test_donor_class_is_read_from_the_filed_first_name_in_the_node_id(node_id, expected):
    """The Donors tab's rule (a first name filed -> individual; NULL or '' -> organization) applied to the key."""
    assert donor_class(node_id) == expected


def test_money_flow_links_regroup_the_race_edges_by_class_and_keep_every_dollar(world):
    """Kills: a class assigned by name heuristics; in-kind merged into contributions; a transfer source or spender
    dropped; committee_candidate edges counted as flows (they would double the 1A money); a link per donor."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    view = money_flow(payload)
    problems = _edge_problems(_link_map(view), RACE_MONEY_FLOW_LINKS)
    assert not problems, "\n".join(problems)
    assert view["view"] == "money_flow"
    assert set(view) == {"view", "nodes", "links", "totals", "folded", "truncated"}


def test_money_flow_totals_reconcile_to_the_ego_edges_for_every_entity_and_window(world):
    """Per edge type the links sum to the ego's edges (committee_candidate excluded), support/oppose to the signed
    IE edges; every node's amount is the sum of its links."""
    for entity, window in ((RACE_ENTITY, WIN), (CANDIDATE_ENTITY, WIN), (RACE_ENTITY, (None, None))):
        payload = _call(world, entity, date_from=window[0], date_to=window[1])
        view = money_flow(payload)
        ego = _sum_by_type(edge for edge in payload["edges"] if edge["edge_type"] != CC)
        assert {k: v for k, v in view["totals"].items() if k not in ("support", "oppose")} == pytest.approx(ego, abs=0.005)
        assert CC not in view["totals"]
        assert view["totals"]["support"] == pytest.approx(
            sum(e["weight"] for e in payload["edges"] if e.get("support_oppose") == "S"), abs=0.005)
        assert view["totals"]["oppose"] == pytest.approx(
            sum(e["weight"] for e in payload["edges"] if e.get("support_oppose") == "O"), abs=0.005)
        incident = defaultdict(float)
        for link in view["links"]:
            incident[link["source"]] += link["weight"]
            incident[link["target"]] += link["weight"]
        for node in view["nodes"]:
            assert node["amount"] == pytest.approx(incident[node["id"]], abs=0.005), node


def test_money_flow_nodes_sit_in_three_columns_with_labels_and_kinds(world):
    """Sources left, the entity's committees and the candidates it spends on in the middle, vendors right; class
    nodes are labelled for readers; entity committees keep their group label; nothing else appears."""
    view = money_flow(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    by_id = {node["id"]: node for node in view["nodes"]}
    assert set(by_id) == {
        CLASS_INDIVIDUAL, CLASS_ORGANIZATION, CLASS_IN_KIND, C400, T_INC, T_TWIN, C900, C905,
        C100, C101, C200, CAND1, CAND2, V_A, V_B, V_P,
    }
    assert {n: by_id[n]["column"] for n in (CLASS_INDIVIDUAL, C400, T_INC, C900)} == {CLASS_INDIVIDUAL: 0, C400: 0, T_INC: 0, C900: 0}
    assert {n: by_id[n]["column"] for n in (C100, C101, C200, CAND1, CAND2)} == dict.fromkeys((C100, C101, C200, CAND1, CAND2), 1)
    assert {n: by_id[n]["column"] for n in (V_A, V_B, V_P)} == dict.fromkeys((V_A, V_B, V_P), 2)
    assert by_id[CLASS_INDIVIDUAL]["label"] == "Individual donors"
    assert by_id[CLASS_IN_KIND]["kind"] == "in_kind"
    assert by_id[C400]["kind"] == "transfer_source" and by_id[C900]["kind"] == "ie_spender"
    assert by_id[C100]["kind"] == "committee" and by_id[C100]["group_label"] == "Ada Example"
    assert by_id[CAND1]["kind"] == "candidate" and by_id[V_A]["kind"] == "vendor"
    assert by_id[V_A]["label"] == "Prairie Print Co" and by_id[V_A]["amount"] == pytest.approx(1100.0)
    assert view["folded"] == {"vendors": 0, "transfers": 0, "spenders": 0, "internal": {"count": 0, "amount": 0.0}}
    columns = [node["column"] for node in view["nodes"]]
    assert columns == sorted(columns), "nodes are listed by column"


def test_money_flow_caps_fold_the_smaller_vendors_transfers_and_spenders_into_one_other_node_each(world):
    """Kills: a cap that drops money instead of folding it; a fold that merges support and oppose; a cap applied to
    the entity's own committees or to donor classes."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    view = money_flow(payload, max_vendors=1, max_transfers=1, max_spenders=1)
    links = _link_map(view)
    assert view["folded"] == {"vendors": 2, "transfers": 2, "spenders": 1, "internal": {"count": 0, "amount": 0.0}}
    assert links[(C100, V_A, CV, None)] == (600.0, 1)
    assert links[(C101, V_A, CV, None)] == (500.0, 1)
    assert links[(C101, "other:vendors", CV, None)] == (300.0, 1)
    assert links[(C200, "other:vendors", CV, None)] == (900.0, 1)
    assert links[(C400, C100, TI, None)] == (2000.0, 1)
    assert links[("other:transfers", C101, TI, None)] == (800.0, 1)
    assert links[("other:transfers", C100, TI, None)] == (500.0, 1)
    assert links[("other:spenders", CAND2, IE, "S")] == (50.0, 1)
    assert links[(C900, CAND1, IE, "S")] == (7120.0, 4)
    assert not any(key[0] in (T_INC, T_TWIN, C905) or key[1] in (V_B, V_P) for key in links)
    assert view["totals"] == money_flow(payload)["totals"], "folding moves money, it never loses it"
    by_id = {node["id"]: node for node in view["nodes"]}
    assert by_id["other:vendors"] == {"id": "other:vendors", "label": "Other vendors", "kind": "vendor", "column": 2,
                                      "amount": 1200.0}
    assert {C100, C101, C200, CLASS_INDIVIDUAL, CLASS_ORGANIZATION, CLASS_IN_KIND} <= set(by_id)


def test_vendor_network_flags_vendors_shared_by_two_committees(world):
    """Prairie Print Co is paid by 100 and 101 (shared); Lakeshore by 200 and Wren Wexler by 101 only. Spenders and
    the candidates they name are in, donors and transfer sources are not; edges are the vendor and IE edges only."""
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    view = vendor_network(payload)
    assert set(view) == {"view", "nodes", "edges", "shared_vendors"}
    assert view["view"] == "vendor_network"
    by_id = {node["id"]: node for node in view["nodes"]}
    assert set(by_id) == {C100, C101, C200, C900, C905, V_A, V_B, V_P, CAND1, CAND2}
    assert by_id[V_A]["shared"] is True and by_id[V_A]["committee_ids"] == [C100, C101] and by_id[V_A]["committee_count"] == 2
    assert by_id[V_B]["shared"] is False and by_id[V_B]["committee_ids"] == [C200]
    assert by_id[V_P]["shared"] is False
    assert view["shared_vendors"] == [V_A]
    assert {by_id[n]["side"] for n in (C100, C101, C200, C900, C905)} == {"payer"}
    assert {by_id[n]["side"] for n in (V_A, V_B, V_P, CAND1, CAND2)} == {"payee"}
    assert {edge["edge_type"] for edge in view["edges"]} == {CV, IE}
    expected = {key: value for key, value in RACE_WIN.items() if key[0] in (CV, IE)}
    assert not _edge_problems(_edge_map(view), expected)
    assert by_id[C100]["label"] == "Friends of Ada Example" and by_id[C100]["role"] == "entity"


def test_vendor_network_counts_every_entity_committee_paying_a_vendor(world):
    """The candidate entity pays Prairie Print Co from 100, 101 and 102; a committee without vendor edges stays a node."""
    view = vendor_network(_call(world, CANDIDATE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    by_id = {node["id"]: node for node in view["nodes"]}
    assert by_id[V_A]["committee_ids"] == [C100, C101, C102] and by_id[V_A]["committee_count"] == 3
    assert view["shared_vendors"] == [V_A]
    empty = vendor_network(_call(world, RACE_ENTITY, date_from=date(2030, 1, 1), date_to=date(2030, 12, 31)))
    assert {node["id"] for node in empty["nodes"]} == {C100, C101, C200}
    assert empty["edges"] == [] and empty["shared_vendors"] == []


def test_ego_force_is_the_payload_nodes_and_edges(world):
    view = ego_force(_call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    assert set(view) == {"view", "nodes", "edges"}
    assert view["view"] == "ego"
    assert view["nodes"] == payload["nodes"] and view["edges"] == payload["edges"]


@pytest.mark.parametrize("projection", [money_flow, vendor_network, ego_force])
def test_projections_leave_the_payload_untouched_and_serialise_to_json(world, projection):
    payload = _call(world, RACE_ENTITY, date_from=WIN[0], date_to=WIN[1])
    pristine = copy.deepcopy(payload)
    view = projection(payload)
    assert payload == pristine
    json.dumps(view)
    for item in view["nodes"] + view.get("edges", view.get("links", [])):
        for value in item.values():
            assert not isinstance(value, float) or value == round(value, 2)
    view["nodes"][0]["label"] = "changed"
    assert payload == pristine, "the projection must not alias the payload's dicts"


@pytest.mark.parametrize("projection", [money_flow, vendor_network, ego_force])
def test_projections_of_an_empty_graph_are_empty(world, projection):
    view = projection(_call(world, EMPTY_ENTITY, date_from=WIN[0], date_to=WIN[1]))
    assert view["nodes"] == []
    assert view.get("edges", view.get("links")) == []
    if projection is money_flow:
        assert view["totals"] == {"support": 0.0, "oppose": 0.0}
        assert view["folded"] == {"vendors": 0, "transfers": 0, "spenders": 0, "internal": {"count": 0, "amount": 0.0}}


# ---------------------------------------------------------------------------
# Review fixes F1 / F2 on money_flow: hand-built ego payloads (no database), so a capped graph and an internal
# transfer can be set up exactly.
# ---------------------------------------------------------------------------

NO_TRUNCATION = {"donors": 0, "vendors": 0, "transfers": 0, "ie_spenders": 0}


def _ego_node(node_id, node_type, role, label=None):
    return {"id": node_id, "label": label or node_id, "node_type": node_type, "role": role, "total_amount": 0.0}


def _ego_edge(edge_type, source, target, weight, count, support_oppose=None):
    edge = {"source": source, "target": target, "edge_type": edge_type, "weight": weight, "count": count,
            "edge_label": edge_type}
    if support_oppose:
        edge["support_oppose"] = support_oppose
    return edge


def _ego(nodes, edges, truncated=NO_TRUNCATION):
    return {"nodes": nodes, "edges": edges, "centrality": [], "summary": {"truncated": dict(truncated)}}


def test_money_flow_labels_the_donor_classes_as_top_n_and_passes_truncation_through_when_donors_were_capped():
    """F1: the ego payload keeps only the top-N donors, so the donor-class source nodes hold only their dollars and
    must say so. N = the donors of that class actually in the payload; the legend needs ``truncated`` to say how many
    were left out. An uncapped payload keeps the plain labels (nothing is missing). Kills: class nodes that read as
    complete totals; a truncation count that is dropped or recomputed; a label counting donors of the other class."""
    nodes = [_ego_node("committee:901", "committee", "entity")]
    edges = []
    for key in ("ada|giver|1 elm st||springfield|il|62701", "bo|giver|2 elm st||springfield|il|62701",
                "cy|giver|3 elm st||springfield|il|62701"):
        nodes.append(_ego_node(f"donor:{key}", "donor", "donor"))
        edges.append(_ego_edge(DC, f"donor:{key}", "committee:901", 100.0, 1))
    for key in ("|acme corp|9 main st||peoria|il|61602", "|beta llc|8 main st||peoria|il|61602"):
        nodes.append(_ego_node(f"donor:{key}", "donor", "donor"))
        edges.append(_ego_edge(DC, f"donor:{key}", "committee:901", 50.0, 1))

    capped = money_flow(_ego(nodes, edges, {**NO_TRUNCATION, "donors": 3}))
    labels = {node["id"]: node["label"] for node in capped["nodes"]}
    assert labels[CLASS_INDIVIDUAL] == "Top 3 individual donors"
    assert labels[CLASS_ORGANIZATION] == "Top 2 organization donors"
    assert capped["truncated"] == {**NO_TRUNCATION, "donors": 3}

    complete = money_flow(_ego(nodes, edges))
    labels = {node["id"]: node["label"] for node in complete["nodes"]}
    assert labels[CLASS_INDIVIDUAL] == "Individual donors" and labels[CLASS_ORGANIZATION] == "Organization donors"
    assert complete["truncated"] == NO_TRUNCATION


def _internal_transfer_ego(with_independent_expenditure):
    """Two entity committees; 902 receives 1,000 from one donor, 200 from an outside committee and 300 from 901
    (an internal transfer); optionally 901 also spends 500 (3 rows) supporting candidate 1, next to the outside
    spender 900's 700."""
    donor = "donor:ada|giver|1 elm st||springfield|il|62701"
    nodes = [_ego_node("committee:901", "committee", "entity"), _ego_node("committee:902", "committee", "entity"),
             _ego_node("committee:400", "committee", "transfer_source"), _ego_node("candidate:1", "candidate", "candidate"),
             _ego_node("committee:900", "committee", "ie_spender"), _ego_node(donor, "donor", "donor")]
    edges = [_ego_edge(DC, donor, "committee:902", 1000.0, 4),
             _ego_edge(TI, "committee:400", "committee:902", 200.0, 1),
             _ego_edge(TI, "committee:901", "committee:902", 300.0, 1),
             _ego_edge(IE, "committee:900", "candidate:1", 700.0, 2, "S")]
    if with_independent_expenditure:
        edges.append(_ego_edge(IE, "committee:901", "candidate:1", 500.0, 3, "S"))
    return _ego(nodes, edges)


@pytest.mark.parametrize("with_independent_expenditure, internal", [
    pytest.param(False, {"count": 1, "amount": 300.0}, id="transfer-only"),
    pytest.param(True, {"count": 4, "amount": 800.0}, id="transfer-and-independent-expenditure"),
])
def test_money_flow_leaves_money_between_the_entitys_own_committees_out_of_the_sankey(with_independent_expenditure,
                                                                                        internal):
    """F2: a transfer_in (or an independent_expenditure) whose source is one of the entity's own committees would be
    a column-1 -> column-1 link and an inflow that is only a move between the entity's own accounts. It is no link, no
    node amount and no total; ``folded.internal`` counts it (rows, dollars). Kills: a backwards link; the transfer
    counted as an inflow; the internal money silently lost instead of reported."""
    view = money_flow(_internal_transfer_ego(with_independent_expenditure))
    column = {node["id"]: node["column"] for node in view["nodes"]}
    assert all(column[link["source"]] < column[link["target"]] for link in view["links"]), view["links"]
    links = _link_map(view)
    assert ("committee:901", "committee:902", TI, None) not in links
    assert links[("committee:400", "committee:902", TI, None)] == (200.0, 1)
    assert links[("committee:900", "candidate:1", IE, "S")] == (700.0, 2)
    assert ("committee:901", "candidate:1", IE, "S") not in links
    assert view["totals"][TI] == 200.0
    assert view["totals"]["support"] == 700.0
    assert view["folded"]["internal"] == internal
    assert {key: view["folded"][key] for key in ("vendors", "transfers", "spenders")} == {"vendors": 0, "transfers": 0,
                                                                                         "spenders": 0}
    incident = defaultdict(float)
    for link in view["links"]:
        incident[link["source"]] += link["weight"]
        incident[link["target"]] += link["weight"]
    for node in view["nodes"]:
        assert node["amount"] == pytest.approx(incident[node["id"]], abs=0.005), node
