"""Tests-first suite for plan step P5 (item L4): the geo drill-down is pushed down into SQL and cached in Redis.

Plan: .claude/plans/load-times.md -- ranked fix L4 and "Unit designs" P5.

Why: /analytics/geo-drilldown loads every donor->committee pair of analytics_donor_committee_agg (1.3M rows) into Python on
each request, filters one state or city there, sorts and slices there, and keeps the page in a per-worker dict for 180 s.
Pinned contract (anything not listed keeps today's payload, key for key):

  * Payload parity.  For a state cell and a city cell, on the all-time path (agg table) and on a custom-window path (live ISBE
    GROUP BY), get_geo_drilldown returns exactly what it returns today: rows, summary (sum_check_delta == 0), pagination, sort,
    source, date_from/date_to.  Same normalisation (state: trimmed, upper case; city: _city_key, i.e. case, punctuation and runs
    of spaces ignored), same fallback (_extract_city_state on donor_address, only for a row with NEITHER city NOR state), same
    four sort-key tuples, same page clamp.
  * All-time path: no statement returns more than per_page rows (no Python-side filter, sort or aggregation of the cell), the
    rows come from the agg table (not a fresh GROUP BY over the receipts), and the cell predicate is indexable.
  * analytics_donor_committee_agg gains donor_state_key / donor_city_key (TEXT), filled for every row by
    refresh_analytics_materialized, and idx_analytics_donor_committee_agg_geo (source, donor_state_key, donor_city_key,
    total_amount DESC) in database/schema.sql; init-db adds both to an existing table.
  * /analytics/geo-drilldown caches in RouteCache("analytics_geo_drilldown") (Redis-shared), TTL from
    ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS (default 21600), key unchanged, ROUTE_PERF_CACHE_ENABLED honoured; the federal
    drill-down leaves its per-worker dict for a shared RouteCache too.

Red today (the step's work; every other test is a green guard of today's behaviour that the rewrite must keep)
  test_all_time_statements_never_return_more_than_per_page_rows, test_refresh_fills_the_geo_key_columns_for_every_row,
  test_schema_file_declares_the_geo_index, test_a_fresh_schema_has_the_key_columns_and_the_index,
  test_init_db_adds_the_columns_and_the_index_to_an_existing_agg_table, test_the_city_cell_page_statement_uses_the_geo_index,
  test_the_route_stores_the_drilldown_in_the_shared_route_cache, test_the_route_cache_key_separates_every_request_dimension
  (its last assertion), test_a_refresh_drops_the_cached_pages, test_the_default_ttl_is_the_analytics_default,
  test_the_federal_route_stores_its_drilldown_in_the_shared_cache.

Not pinned on purpose (a failure would never change a decision)
  the type of committee_id (an int on the agg path, the text of the id on the live path, today); the live path's hashed
  donor_key; how many rows a windowed statement returns (address-only rows need Python parsing); the stored form of an empty key
  (NULL or ''); tab / no-break-space padding of a state or city; the federal cache's name and default TTL; ?refresh_cache=1 (this
  route ignores it today).

How things are observed
  * Statements: PostgresCompatCursor.execute/executemany/fetchone/fetchall are wrapped for the length of a block
    (statements_of); a statement's rows are the rows actually fetched from the server (answers the compat layer fakes for
    sqlite_master / PRAGMA probes are not counted).  A statement run behind the cursor's back (conn._pg_conn) is invisible --
    accepted hole.
  * Caches: Redis is forced off in every test (house technique, tests/test_cache_backend.py) and every in-process RouteCache
    plus the two old per-worker dicts are emptied around each test.  The route tests swap in FakeRedis so what lands in the
    shared store is visible, and call the builder through webapp.routes.analytics.get_geo_drilldown / webapp.routes.
    federal_finance.get_federal_geo_drilldown (house pattern: the route keeps calling the builder through that module name).
  * Fixtures: the SQL tests seed a small ISBE-direct dataset (isbe_condensed_receipts / isbe_committees / isbe_d2_reports, the
    production path) and run the real refresh_analytics_materialized; the route tests reuse analytics_app and federal_app.

Fixture facts (every number is derivable from the DONORS table below; the headers are asserted as literals)
  Committees 101 "Alpha Committee", 102 "Beta Committee", 103 "gamma pac" (lower-case on purpose).  49 donor/committee pairs
  (= 49 agg rows), 3 filtered-out receipts that no path may ever count, 2 pairs that sit in no cell.
  IL (24 rows): Chicago 14 rows (11 by city text -- "Chicago", "CHICAGO", "  chicago ", "Chicago.", "chicago" with a state of
  "il " / " IL" -- and 3 by address only: city/state NULL, '' and NULL), Springfield 3, Naperville 2, Des Plaines 2 ("DES  PLAINES"),
  Peoria 1 (address only), Oak Park 1, plus ONE row that has a state and no city ("55 Grand Ave, Elgin, IL 60120", city NULL,
  state "IL"): it belongs to the IL cell only, shows an empty city, and is in no city cell (the fallback fires only when BOTH
  are empty).  MO (11 rows): "St. Louis" in four spellings plus one address-only row (one St Louis cell), Springfield MO next to
  Springfield IL, Kansas City, "O'Fallon"/"OFallon" (one cell), a state-only row.  IN (9 rows) is the name-order cell:
  "al smith"/"Al Smith", "bo lee"/"Bo Lee", "pat poe"/"PAT POE"/"Pat Poe", and "Ana Muñoz" next to "Ana Muzzy".  WI (3 rows)
  carries cents (33.33 + 33.33 + 33.34).  The two unreachable pairs: a city with no state ("Peoria", 8,000) and an address that
  parses to nothing (7,000).
  Window 2026-02-01..2026-03-31 (inclusive): receipts dated on both boundary days and the days just outside, so the live path's
  >= / <= comparisons are pinned; some donors are partly inside (only their in-window receipts count).

Kill list (what each family of checks repels; accepted holes are listed in the report)
  state kept as the raw column or trimmed only (misses "il ", " IL"); city key without punctuation or space collapsing
  ("St. Louis", "Chicago.", "DES  PLAINES", "O'Fallon"); no fallback, or fallback per field (the Elgin row would land in a bogus
  city cell); city cell without the state (Springfield IL + MO, the city-only decoy); no source predicate; COUNT(*) as the donor
  count; header computed over the page; ORDER BY total_amount alone (ties are planted in every sort and every sort is run
  ascending AND descending, so an arbitrary-but-stable tie order cannot match both); donor_name ordered without LOWER or by the
  database collation instead of code points ("al smith"/"Al Smith" kill the case-sensitive orders, "Ana Muñoz" vs "Ana Muzzy"
  kills the locale order); page clamp lost; empty cell page not echoed; Decimal / non-JSON values in the payload; windowed path
  that drops the archived / part-1 / positive-amount predicates or an inclusive bound; fallback rows missing from the
  windowed path; keys filled only for rows with a city; keys not added to an existing table, or DDL that init-db cannot run twice;
  an agg built before the columns existed answering with an empty page, or backfilling inside the GET; the index added but unused;
  a cache keyed too coarsely, per worker, outside the namespace refresh-analytics flushes (a 6 h TTL would then serve pre-refresh
  numbers all day), with the wrong TTL, or ignoring ROUTE_PERF_CACHE_ENABLED.
"""
from __future__ import annotations

import contextlib
import fnmatch
import gc
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode

import pytest

import database.analytics as analytics_db
import database.connection as connection_db
import database.federal_fec as federal_db
import webapp.cache_backend as cache_backend
import webapp.routes.analytics as analytics_routes
import webapp.routes.federal_finance as federal_routes
from database.analytics import get_geo_drilldown, get_geo_summary, refresh_analytics_materialized
from database.connection import ensure_schema, get_db, init_db
from database.pg_compat import PostgresCompatCursor
from tests.test_analytics_features import analytics_app, analytics_conn  # noqa: F401  (fixtures)
from tests.test_profile_scope import federal_app  # noqa: F401  (fixture)
from webapp.cache_backend import RouteCache

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT / "database" / "schema.sql"

WINDOW = {"date_from": "2026-02-01", "date_to": "2026-03-31"}  # inclusive on both days
GEO_INDEX = "idx_analytics_donor_committee_agg_geo"
AGG = "analytics_donor_committee_agg"

ALPHA, BETA, GAMMA = 101, 102, 103
COMMITTEES = {ALPHA: "Alpha Committee", BETA: "Beta Committee", GAMMA: "gamma pac"}
D2_FILED_DOC = {ALPHA: 5001, BETA: 5002, GAMMA: 5003}


# ---------------------------------------------------------------------------
# The dataset.  One Donor = one (donor, committee) pair = one agg row.
# ---------------------------------------------------------------------------

class Donor(NamedTuple):
    label: str
    first: str
    last: str
    committee: int
    address1: str
    city: str | None          # as filed (raw, padding and case included)
    state: str | None         # as filed
    zipcode: str | None
    receipts: tuple          # (received_date, amount) as filed; amount <= 0 is never counted
    cell: tuple              # (city as the payload shows it, state) -- ("", "IL") = state cell only, ("", "") = no cell
    total: float             # hand-computed: sum of the counted (amount > 0) receipts
    count: int               # hand-computed: number of counted receipts
    window: tuple | None     # (total, count) of the receipts dated inside WINDOW; None = no receipt inside


DONORS = [
    # ---- Chicago, IL: city text variants and address-only rows ------------------------------------------------------
    # 600 + 300 = 900 in 2 receipts (the -40 refund is never counted); only the 02-10 gift is inside the window.
    Donor("C01", "Ann", "Adams", ALPHA, "1 Lake St", "Chicago", "IL", "60601",
          (("2026-02-10", 600.0), ("2026-04-01", 300.0), ("2026-02-20", -40.0)), ("Chicago", "IL"), 900.0, 2, (600.0, 1)),
    # 460 + 440 = 900 in 2; 02-01 (first window day) is inside, 01-31 is not -> window 460 in 1.  Lower-case name, upper-case
    # city, state "il ".
    Donor("C02", "ben", "baker", BETA, "2 Oak St", "CHICAGO", "il ", "60601",
          (("2026-02-01", 460.0), ("2026-01-31", 440.0)), ("Chicago", "IL"), 900.0, 2, (460.0, 1)),
    # 900 in 1; padded lower-case city.
    Donor("C03", "Cara", "Cole", GAMMA, "3 Pine St", "  chicago ", "IL", "60601",
          (("2026-03-15", 900.0),), ("Chicago", "IL"), 900.0, 1, (900.0, 1)),
    # 250 + 250.25 + 250.25 = 750.5 in 3; the 01-10 gift is outside the window -> 500.5 in 2.  City with a trailing period.
    Donor("C04", "Dan", "Dunn", ALPHA, "4 Elm St", "Chicago.", "IL", "60602",
          (("2026-01-10", 250.0), ("2026-02-12", 250.25), ("2026-03-12", 250.25)), ("Chicago", "IL"), 750.5, 3, (500.5, 2)),
    # 250 + 250.25 + 250.25 = 750.5 in 3, all inside the window; state with a leading space.
    Donor("C05", "Eli", "Ewing", BETA, "5 Main St", "chicago", " IL", "60602",
          (("2026-02-12", 250.0), ("2026-02-13", 250.25), ("2026-02-14", 250.25)), ("Chicago", "IL"), 750.5, 3, (750.5, 3)),
    # 6 x 100 = 600 in 6; three gifts are in 2025 -> window 300 in 3.
    Donor("C06", "Fay", "Finn", ALPHA, "6 Bay St", "Chicago", "IL", "60603",
          (("2026-02-05", 100.0), ("2026-02-06", 100.0), ("2026-02-07", 100.0),
           ("2025-06-01", 100.0), ("2025-06-02", 100.0), ("2025-06-03", 100.0)), ("Chicago", "IL"), 600.0, 6, (300.0, 3)),
    Donor("C07", "Gus", "Gray", GAMMA, "7 Gold St", "Chicago", "IL", "60603",
          (("2026-02-05", 500.0),), ("Chicago", "IL"), 500.0, 1, (500.0, 1)),
    Donor("C08", "Hal", "Hill", BETA, "8 Hill St", "Chicago", "IL", "60604",
          (("2026-03-05", 500.0),), ("Chicago", "IL"), 500.0, 1, (500.0, 1)),
    # Address only, NULL city/state: 4 x 100 = 400 in 4; the two April gifts are outside -> window 200 in 2.  (regex 1: ", City, ST zip")
    Donor("C09", "Ivy", "Ives", ALPHA, "10 Wacker Dr, Chicago, IL 60606", None, None, None,
          (("2026-02-15", 100.0), ("2026-02-16", 100.0), ("2026-04-15", 100.0), ("2026-04-16", 100.0)),
          ("Chicago", "IL"), 400.0, 4, (200.0, 2)),
    # Address only, EMPTY-STRING city/state/zip: 350 in 1.  (regex 2: ", ST zip" with no city between the commas)
    Donor("C10", "Jon", "Judd", BETA, "Chicago, IL 60607", "", "", "",
          (("2026-02-28", 350.0),), ("Chicago", "IL"), 350.0, 1, (350.0, 1)),
    # Address only, NULL city/state: 3 x 100 = 300 in 3, all in 2025 -> absent from the window.  (regex 3: ", City, ST" no zip)
    Donor("C11", "Kay", "Kerr", GAMMA, "500 N State St, Chicago, IL", None, None, None,
          (("2025-12-01", 100.0), ("2025-12-02", 100.0), ("2025-12-03", 100.0)), ("Chicago", "IL"), 300.0, 3, None),
    # 100.25 + 150.25 = 250.5 in 2, both inside the window.
    Donor("C12", "Lee", "Long", ALPHA, "12 Long Rd", "CHICAGO", "IL", "60608",
          (("2026-02-18", 100.25), ("2026-03-18", 150.25)), ("Chicago", "IL"), 250.5, 2, (250.5, 2)),
    # The same donor as C01 (identical filing) giving to a second committee: 120 in 1.  13 donors, 14 rows in Chicago.
    Donor("C13", "Ann", "Adams", BETA, "1 Lake St", "Chicago", "IL", "60601",
          (("2026-03-20", 120.0),), ("Chicago", "IL"), 120.0, 1, (120.0, 1)),
    Donor("C14", "Max", "Moss", GAMMA, "14 Moss Ave", "Chicago", "IL", "60609",
          (("2026-02-02", 75.0),), ("Chicago", "IL"), 75.0, 1, (75.0, 1)),
    # ---- the rest of IL ---------------------------------------------------------------------------------------------
    # 500 + 300 = 800 in 2; the 06-01 gift is outside -> 500 in 1.
    Donor("S01", "Ned", "Nash", ALPHA, "30 Nash Rd", "Springfield", "IL", "62701",
          (("2026-02-15", 500.0), ("2026-06-01", 300.0)), ("Springfield", "IL"), 800.0, 2, (500.0, 1)),
    Donor("S02", "Opal", "Orr", BETA, "31 Orr St", "SPRINGFIELD", "IL", "62702",
          (("2026-03-10", 450.0),), ("Springfield", "IL"), 450.0, 1, (450.0, 1)),
    # 5 x 30 = 150 in 5; two gifts are in 2025 -> window 90 in 3.
    Donor("S03", "Pete", "Pugh", GAMMA, "32 Pugh Ave", "springfield ", "IL", "62703",
          (("2026-02-05", 30.0), ("2026-02-06", 30.0), ("2026-02-07", 30.0), ("2025-11-01", 30.0), ("2025-11-02", 30.0)),
          ("Springfield", "IL"), 150.0, 5, (90.0, 3)),
    # 4 x 160 = 640 in 4; the two April gifts are outside -> window 320 in 2.
    Donor("N01", "Quin", "Quill", ALPHA, "33 Quill Ln", "Naperville", "IL", "60540",
          (("2026-02-20", 160.0), ("2026-02-21", 160.0), ("2026-04-10", 160.0), ("2026-04-11", 160.0)),
          ("Naperville", "IL"), 640.0, 4, (320.0, 2)),
    Donor("N02", "Rae", "Rowe", BETA, "34 Rowe Dr", "naperville", "IL", "60541",
          (("2026-01-02", 200.0),), ("Naperville", "IL"), 200.0, 1, None),
    # 110 + 110 = 220 in 2.
    Donor("D01", "Sid", "Shaw", GAMMA, "35 Shaw Blvd", "Des Plaines", "IL", "60016",
          (("2026-02-25", 110.0), ("2026-03-25", 110.0)), ("Des Plaines", "IL"), 220.0, 2, (220.0, 2)),
    # 90 in 1 on 03-31 (last window day); double space inside the city.
    Donor("D02", "Tia", "Tate", ALPHA, "36 Tate Ct", "DES  PLAINES", "IL", "60018",
          (("2026-03-31", 90.0),), ("Des Plaines", "IL"), 90.0, 1, (90.0, 1)),
    # State "IL", NO city: stays an IL-cell row with an empty city; the Elgin text inside address1 must not be used.
    Donor("E01", "Fay", "Fox", BETA, "55 Grand Ave, Elgin, IL 60120", None, "IL", None,
          (("2026-02-01", 175.0),), ("", "IL"), 175.0, 1, (175.0, 1)),
    # Address only (regex 1): 2 x 30 = 60 in 2.
    Donor("P01", "Uma", "Underwood", ALPHA, "7 Elm St, Peoria, IL 61602", None, None, None,
          (("2026-02-28", 30.0), ("2026-03-01", 30.0)), ("Peoria", "IL"), 60.0, 2, (60.0, 2)),
    Donor("O01", "Vic", "Vance", GAMMA, "37 Vance Pl", "Oak Park", "IL", "60301",
          (("2026-04-30", 45.0),), ("Oak Park", "IL"), 45.0, 1, None),
    # ---- MO -------------------------------------------------------------------------------------------------------
    # 400 + 300 = 700 in 2; the 01-15 gift is outside -> 400 in 1.
    Donor("M01", "Wes", "West", ALPHA, "40 West St", "St. Louis", "MO", "63101",
          (("2026-02-14", 400.0), ("2026-01-15", 300.0)), ("St Louis", "MO"), 700.0, 2, (400.0, 1)),
    Donor("M02", "Xan", "Xiong", BETA, "41 Xiong St", "ST LOUIS", "MO", "63102",
          (("2026-03-31", 300.0),), ("St Louis", "MO"), 300.0, 1, (300.0, 1)),
    # 04-01 is the day after the window.
    Donor("M03", "Yul", "Young", GAMMA, "42 Young St", "st louis ", "MO", "63103",
          (("2026-04-01", 120.0),), ("St Louis", "MO"), 120.0, 1, None),
    # 40 + 40 = 80 in 2; the first one is on the first window day.  Double space inside the city.
    Donor("M04", "Zoe", "Zane", ALPHA, "43 Zane St", "St  Louis", "MO", "63104",
          (("2026-02-01", 40.0), ("2026-03-01", 40.0)), ("St Louis", "MO"), 80.0, 2, (80.0, 2)),
    # Address only (regex 1) in St. Louis: 60 in 1.
    Donor("M05", "Abe", "Ames", BETA, "3 Pine St, St. Louis, MO 63101", None, None, None,
          (("2026-02-10", 60.0),), ("St Louis", "MO"), 60.0, 1, (60.0, 1)),
    Donor("M06", "Bea", "Bell", ALPHA, "44 Bell St", "Springfield", "MO", "65801",
          (("2025-12-31", 500.0),), ("Springfield", "MO"), 500.0, 1, None),
    Donor("M07", "Cal", "Cobb", GAMMA, "45 Cobb St", "Springfield", "MO", "65802",
          (("2026-03-02", 40.0),), ("Springfield", "MO"), 40.0, 1, (40.0, 1)),
    # 130 + 130 = 260 in 2; the 05-01 gift is outside -> 130 in 1.
    Donor("M08", "Don", "Dale", BETA, "46 Dale St", "Kansas City", "MO", "64101",
          (("2026-02-20", 130.0), ("2026-05-01", 130.0)), ("Kansas City", "MO"), 260.0, 2, (130.0, 1)),
    # State "MO", no city: MO cell only.
    Donor("M09", "Eve", "East", ALPHA, "47 East St", None, "MO", None,
          (("2026-02-22", 55.0),), ("", "MO"), 55.0, 1, (55.0, 1)),
    # "O'Fallon" and "OFallon" are one city (punctuation is dropped without inserting a space): 35 + 25 = 60, 2 donors.
    Donor("M10", "Flo", "Fitch", BETA, "48 Fitch St", "O'Fallon", "MO", "63366",
          (("2026-01-31", 35.0),), ("Ofallon", "MO"), 35.0, 1, None),
    Donor("M11", "Gil", "Grant", GAMMA, "49 Grant St", "OFallon", "MO", "63368",
          (("2026-02-03", 25.0),), ("Ofallon", "MO"), 25.0, 1, (25.0, 1)),
    # ---- IN: the name-order cell (all inside the window) ----------------------------------------------------------
    Donor("I01", "Ana", "Muñoz", ALPHA, "50 Calumet Ave", "Hammond", "IN", "46320",       # ñ = n with tilde
          (("2026-02-15", 410.0),), ("Hammond", "IN"), 410.0, 1, (410.0, 1)),
    Donor("I02", "Ana", "Muzzy", BETA, "51 Calumet Ave", "Hammond", "IN", "46320",
          (("2026-02-15", 220.0),), ("Hammond", "IN"), 220.0, 1, (220.0, 1)),
    # 3 x 300 = 900 in 3.
    Donor("I03", "al", "smith", ALPHA, "52 Calumet Ave", "Hammond", "IN", "46320",
          (("2026-02-15", 300.0), ("2026-02-16", 300.0), ("2026-02-17", 300.0)), ("Hammond", "IN"), 900.0, 3, (900.0, 3)),
    Donor("I04", "Al", "Smith", BETA, "53 Calumet Ave", "Hammond", "IN", "46320",
          (("2026-02-15", 400.0),), ("Hammond", "IN"), 400.0, 1, (400.0, 1)),
    Donor("I05", "bo", "lee", ALPHA, "54 Broadway", "Gary", "IN", "46402",
          (("2026-02-15", 300.0),), ("Gary", "IN"), 300.0, 1, (300.0, 1)),
    # 2 x 350 = 700 in 2.
    Donor("I06", "Bo", "Lee", BETA, "55 Broadway", "Gary", "IN", "46402",
          (("2026-02-15", 350.0), ("2026-02-16", 350.0)), ("Gary", "IN"), 700.0, 2, (700.0, 2)),
    Donor("I07", "pat", "poe", ALPHA, "56 Broadway", "Gary", "IN", "46402",
          (("2026-02-15", 100.0),), ("Gary", "IN"), 100.0, 1, (100.0, 1)),
    Donor("I08", "PAT", "POE", BETA, "57 Broadway", "Gary", "IN", "46402",
          (("2026-02-15", 200.0),), ("Gary", "IN"), 200.0, 1, (200.0, 1)),
    Donor("I09", "Pat", "Poe", GAMMA, "58 Broadway", "Gary", "IN", "46402",
          (("2026-02-15", 300.0),), ("Gary", "IN"), 300.0, 1, (300.0, 1)),
    # ---- WI: cents (33.33 + 33.33 + 33.34 = 100.00) -------------------------------------------------------------
    Donor("W01", "Gil", "Gage", ALPHA, "60 Gage St", "Madison", "WI", "53703",
          (("2026-02-15", 33.33),), ("Madison", "WI"), 33.33, 1, (33.33, 1)),
    Donor("W02", "Hal", "Hess", BETA, "61 Hess St", "Madison", "WI", "53703",
          (("2026-02-15", 33.33),), ("Madison", "WI"), 33.33, 1, (33.33, 1)),
    Donor("W03", "Ira", "Ivey", GAMMA, "62 Ivey St", "Madison", "WI", "53703",
          (("2026-02-15", 33.34),), ("Madison", "WI"), 33.34, 1, (33.34, 1)),
    # ---- in no cell: a city with no state (8,000) and an address that parses to nothing (7,000) --------------------
    Donor("X01", "Hope", "Hunt", BETA, "9 Mill Rd", "Peoria", None, None,
          (("2026-02-15", 8000.0),), ("", ""), 8000.0, 1, (8000.0, 1)),
    Donor("X02", "Ian", "Ito", GAMMA, "PO Box 12", None, None, None,
          (("2026-02-15", 7000.0),), ("", ""), 7000.0, 1, (7000.0, 1)),
]
SPEC = {donor.label: donor for donor in DONORS}

# Receipts the agg and the live path must never count.  (first, last, committee, filed_doc_id, d2_part, archived, amount)
FILTERED_OUT = [
    ("Zed", "Zimmer", ALPHA, 5001, "1A", True, 9999.0),    # archived receipt
    ("Yan", "Yost", BETA, 5002, "5A", False, 8888.0),      # D-2 part 5, not a contribution part
    ("Xia", "Xu", GAMMA, 5099, "1A", True, 7777.0),        # superseded filing: report 5099 and its receipt archived
]
FILTERED_OUT_NAMES = {"Zed Zimmer", "Yan Yost", "Xia Xu"}

# Hand-computed cell headers: (total, contributions, donors, detail rows).  All time:
#   IL  = Chicago 7,296.5 + Springfield 1,400 + Naperville 840 + Des Plaines 310 + Peoria 60 + Oak Park 45 + Elgin (state only) 175
#         = 10,126.5; contributions 31+8+5+3+2+1+1 = 51; rows 14+3+2+2+1+1+1 = 24; donors 24 - 1 (Ann Adams gave to two committees)
#   MO  = St Louis 1,260 + Springfield 540 + Kansas City 260 + Ofallon 60 + state only 55 = 2,175; 7+2+2+2+1 = 14; rows 5+2+1+2+1 = 11
#   IN  = Hammond 1,930 + Gary 1,600 = 3,530; 6+6 = 12; 9 rows.   WI = 33.33 + 33.33 + 33.34 = 100; 3 rows
#   Chicago = 900*3 + 750.5*2 + 600 + 500*2 + 400 + 350 + 300 + 250.5 + 120 + 75 = 7,296.5; 31 contributions; 14 rows, 13 donors
#   Springfield IL 800+450+150 (2+1+5); Naperville 640+200 (4+1); Des Plaines 220+90 (2+1); St Louis 700+300+120+80+60 (2+1+1+2+1);
#   Springfield MO 500+40; Ofallon 35+25; Hammond 410+220+900+400 (1+1+3+1); Gary 300+700+100+200+300 (1+2+1+1+1)
ALL_TIME_HEADERS = {
    ("state", "IL", None): (10126.5, 51, 23, 24),
    ("state", "MO", None): (2175.0, 14, 11, 11),
    ("state", "IN", None): (3530.0, 12, 9, 9),
    ("state", "WI", None): (100.0, 3, 3, 3),
    ("city", "Chicago", "IL"): (7296.5, 31, 13, 14),
    ("city", "Springfield", "IL"): (1400.0, 8, 3, 3),
    ("city", "Naperville", "IL"): (840.0, 5, 2, 2),
    ("city", "Des Plaines", "IL"): (310.0, 3, 2, 2),
    ("city", "Peoria", "IL"): (60.0, 2, 1, 1),
    ("city", "Oak Park", "IL"): (45.0, 1, 1, 1),
    ("city", "St Louis", "MO"): (1260.0, 7, 5, 5),
    ("city", "Springfield", "MO"): (540.0, 2, 2, 2),
    ("city", "Kansas City", "MO"): (260.0, 2, 1, 1),
    ("city", "Ofallon", "MO"): (60.0, 2, 2, 2),
    ("city", "Hammond", "IN"): (1930.0, 6, 4, 4),
    ("city", "Gary", "IN"): (1600.0, 6, 5, 5),
    ("city", "Madison", "WI"): (100.0, 3, 3, 3),
}
# Inside 2026-02-01..2026-03-31:
#   Chicago = 600+120+460+900+500.5+750.5+300+500+500+200+350+250.5+75 = 5,506.5; contributions 1+1+1+1+2+3+3+1+1+2+1+2+1 = 20;
#             13 rows (Kay Kerr is wholly outside), 12 donors
#   IL = Chicago 5,506.5 + Springfield 1,040 + Naperville 320 + Des Plaines 310 + Peoria 60 + Elgin 175 = 7,411.5; 20+5+2+3+2+1 = 33;
#        rows 13+3+1+2+1+1 = 21; donors 12+3+1+2+1+1 = 20 (Oak Park's only gift is 04-30)
#   MO = St Louis 840 + Springfield 40 + Kansas City 130 + Ofallon 25 + state only 55 = 1,090; 5+1+1+1+1 = 9; rows 4+1+1+1+1 = 8
#   St Louis = 400 + 300 + 80 + 60 (1+1+2+1); Springfield IL 500+450+90 (1+1+3); Des Plaines 220+90 (2+1); IN, WI unchanged
WINDOW_HEADERS = {
    ("state", "IL", None): (7411.5, 33, 20, 21),
    ("state", "MO", None): (1090.0, 9, 8, 8),
    ("state", "IN", None): (3530.0, 12, 9, 9),
    ("state", "WI", None): (100.0, 3, 3, 3),
    ("city", "Chicago", "IL"): (5506.5, 20, 12, 13),
    ("city", "Springfield", "IL"): (1040.0, 5, 3, 3),
    ("city", "Naperville", "IL"): (320.0, 2, 1, 1),
    ("city", "Des Plaines", "IL"): (310.0, 3, 2, 2),
    ("city", "Peoria", "IL"): (60.0, 2, 1, 1),
    ("city", "Oak Park", "IL"): (0.0, 0, 0, 0),
    ("city", "St Louis", "MO"): (840.0, 5, 4, 4),
    ("city", "Springfield", "MO"): (40.0, 1, 1, 1),
    ("city", "Kansas City", "MO"): (130.0, 1, 1, 1),
    ("city", "Ofallon", "MO"): (25.0, 1, 1, 1),
    ("city", "Hammond", "IN"): (1930.0, 6, 4, 4),
    ("city", "Gary", "IN"): (1600.0, 6, 5, 5),
    ("city", "Madison", "WI"): (100.0, 3, 3, 3),
}

# Hand-ordered cells (labels, best first for "desc", first for "asc"); the derivation is in the comment above each list.
# Default sort = (total, count, lower(name)) descending.  IL, all time: 900/2 ben baker, 900/2 ann adams, 900/1 cara cole, 800,
# 750.5/3 eli ewing, 750.5/3 dan dunn, 640, 600, 500/1 hal hill, 500/1 gus gray | 450, 400, 350, 300, 250.5, 220, 200, 175,
# 150, 120 | 90, 75, 60, 45.
IL_DEFAULT = ["C02", "C01", "C03", "S01", "C05", "C04", "N01", "C06", "C08", "C07",
              "S02", "C09", "C10", "C11", "C12", "D01", "N02", "E01", "S03", "C13",
              "D02", "C14", "P01", "O01"]
IL_PAGES = [  # (page, labels, page_total_amount, page_contribution_count)
    (1, IL_DEFAULT[0:10], 7241.0, 25),    # 900*3 + 800 + 750.5*2 + 640 + 600 + 500*2 = 7241; 2+2+1+2+3+3+4+6+1+1 = 25
    (2, IL_DEFAULT[10:20], 2615.5, 21),   # 450+400+350+300+250.5+220+200+175+150+120 = 2615.5; 1+4+1+3+2+2+1+1+5+1 = 21
    (3, IL_DEFAULT[20:24], 270.0, 5),     # 90+75+60+45 = 270; 1+1+2+1 = 5
]
# Chicago, all time: the IL order without the 10 non-Chicago rows.
CHICAGO_DEFAULT = ["C02", "C01", "C03", "C05", "C04", "C06", "C08", "C07", "C09", "C10", "C11", "C12", "C13", "C14"]
CHICAGO_PAGES = [
    (1, CHICAGO_DEFAULT[0:10], 6551.0, 24),  # 900*3 + 750.5*2 + 600 + 500*2 + 400 + 350 = 6551; 2+2+1+3+3+6+1+1+4+1 = 24
    (2, CHICAGO_DEFAULT[10:14], 745.5, 7),   # 300 + 250.5 + 120 + 75 = 745.5; 3+2+1+1 = 7
]
# contribution_count sort = (count, total, lower(name)): 6 | 5 | 4: 640, 400 | 3: 750.5 eli ewing, 750.5 dan dunn, 300 | 2: 900 ben
# baker, 900 ann adams, 800 ...
IL_COUNT_DESC_PAGE_1 = ["C06", "S03", "N01", "C09", "C05", "C04", "C11", "C02", "C01", "S01"]
# ascending: the count-1 rows by total 45, 75, 90, 120, 175, 200, 350, 450, 500 gus gray, 500 hal hill ...
IL_COUNT_ASC_PAGE_1 = ["O01", "C14", "D02", "C13", "E01", "N02", "C10", "S02", "C07", "C08"]
# committee_name sort = (lower(committee), total): "alpha committee" < "beta committee" < "gamma pac"; within a committee by total.
CHICAGO_COMMITTEE_ASC = ["C12", "C09", "C06", "C04", "C01",     # Alpha: 250.5, 400, 600, 750.5, 900
                         "C13", "C10", "C08", "C05", "C02",     # Beta: 120, 350, 500, 750.5, 900
                         "C14", "C11", "C07", "C03"]            # gamma: 75, 300, 500, 900
CHICAGO_COMMITTEE_DESC = list(reversed(CHICAGO_COMMITTEE_ASC))
# donor_name sort = (lower(name), total): the two Ann Adams rows tie on the name and are split by total (120 before 900).
CHICAGO_NAME_ASC = ["C13", "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C14"]
CHICAGO_NAME_DESC = list(reversed(CHICAGO_NAME_ASC))
# IN by name, Python code-point order of the lower-cased name, ties by total: "al smith" 400 (Al Smith) < 900 (al smith) |
# "ana muzzy" < "ana muñoz" (z is U+007A, n-tilde is U+00F1) | "bo lee" 300 < 700 | "pat poe" 100 < 200 < 300.
IN_NAME_ASC = ["I04", "I03", "I02", "I01", "I05", "I06", "I07", "I08", "I09"]
IN_NAME_DESC = list(reversed(IN_NAME_ASC))
# Window, default sort: Chicago 900 cara cole, 750.5, 600, 500.5, 500 hal hill, 500 gus gray, 460, 350, 300, 250.5, 200, 120, 75.
WINDOW_CHICAGO_DEFAULT = ["C03", "C05", "C01", "C04", "C08", "C07", "C02", "C10", "C06", "C12", "C09", "C13", "C14"]
# IL window: 900, 750.5, 600, 500.5 | 500/1 ned nash, hal hill, gus gray | 460 | 450 | 350 | 320 | 300 | 250.5 | 220 | 200 | 175 | 120 |
# 90/3 pete pugh, 90/1 tia tate | 75 | 60.
WINDOW_IL_DEFAULT = ["C03", "C05", "C01", "C04", "S01", "C08", "C07", "C02", "S02", "C10",
                     "N01", "C06", "C12", "D01", "C09", "E01", "C13", "S03", "D02", "C14",
                     "P01"]
WINDOW_MO_DEFAULT = ["M01", "M02", "M08", "M04", "M05", "M09", "M07", "M11"]      # 400, 300, 130, 80, 60, 55, 40, 25
WINDOW_ST_LOUIS_DEFAULT = ["M01", "M02", "M04", "M05"]                              # 400, 300, 80, 60


def _agg_donor_key(donor: Donor) -> str:
    """The agg's donor_key: LOWER(TRIM(first|last|address1|address2|city|state|zip)), NULLs as ''."""
    parts = [donor.first, donor.last, donor.address1, "", donor.city or "", donor.state or "", donor.zipcode or ""]
    return "|".join(parts).strip(" ").lower()


def _expected_row(label: str, *, window: bool = False) -> dict:
    donor = SPEC[label]
    total, count = donor.window if window else (donor.total, donor.count)
    row = {
        "donor_key": _agg_donor_key(donor),
        "donor_name": f"{donor.first} {donor.last}",
        "donor_city": donor.cell[0],
        "donor_state": donor.cell[1],
        "committee_id": donor.committee,
        "committee_name": COMMITTEES[donor.committee],
        "total_amount": total,
        "contribution_count": count,
    }
    if window:
        row.pop("donor_key")  # the live path hashes the key (_stable_entity_key); only its presence is pinned (_comparable)
    return row


LABEL_BY_ROW = {(f"{d.first} {d.last}", d.committee): d.label for d in DONORS}


def _labels(payload: dict) -> list[str]:
    return [LABEL_BY_ROW[(row["donor_name"], int(row["committee_id"]))] for row in payload["rows"]]


def _comparable(rows: list[dict], *, window: bool = False) -> list[dict]:
    """Rows as compared with _expected_row.

    committee_id is compared as a number: today the agg path hands out an int and the live path the text of the id, and nothing
    reads it (the template does not), so its type is not pinned.  On the live path donor_key is a hash (_stable_entity_key): it
    must be a non-empty string and is then dropped.
    """
    out = []
    for row in rows:
        row = dict(row, committee_id=int(row["committee_id"]))
        if window:
            assert isinstance(row["donor_key"], str), row
            assert row["donor_key"], row
            row.pop("donor_key")
        out.append(row)
    return out


def _cell_labels(geo_type: str, value: str, state: str | None, *, window: bool = False) -> list[str]:
    out = []
    for donor in DONORS:
        city, donor_state = donor.cell
        if geo_type == "state":
            inside = donor_state == value
        else:
            inside = city == value and donor_state == state
        if inside and (donor.window is not None or not window):
            out.append(donor.label)
    return out


def _sort_key(label: str, sort_by: str, *, window: bool) -> tuple:
    donor = SPEC[label]
    total, count = donor.window if window else (donor.total, donor.count)
    name = f"{donor.first} {donor.last}".lower()
    if sort_by == "total_amount":
        return (total, count, name)
    if sort_by == "contribution_count":
        return (count, total, name)
    if sort_by == "donor_name":
        return (name, total)
    return (COMMITTEES[donor.committee].lower(), total)


def _sorted_labels(labels: list[str], sort_by: str, sort_dir: str, *, window: bool = False) -> list[str]:
    """The documented contract: Python tuple keys, all components in the one direction, ties broken by the later components."""
    keys = {label: _sort_key(label, sort_by, window=window) for label in labels}
    assert len(set(keys.values())) == len(keys), f"fixture has a full sort tie under {sort_by}: the order would be arbitrary"
    return sorted(labels, key=keys.get, reverse=(sort_dir == "desc"))


def _summary(geo_type, value, state, header, *, label, page_total, page_count) -> dict:
    total, contributions, donors, detail_rows = header
    return {
        "geo_type": geo_type,
        "geo_value": value,
        "geo_state": value if geo_type == "state" else state,
        "geo_label": label,
        "total_amount": total,
        "contribution_count": contributions,
        "donor_count": donors,
        "detail_row_count": detail_rows,
        "page_total_amount": page_total,
        "page_contribution_count": page_count,
        "sum_check_delta": 0.0,
    }


def drill(conn, geo_type: str, value: str, state: str | None = None, **kwargs) -> dict:
    return get_geo_drilldown(conn, geo_type=geo_type, geo_value=value, geo_state=state, **kwargs)


def _assert_strict_json(payload: dict) -> None:
    """No Decimal / date / set may leak out of SQL: the payload survives a strict JSON round trip unchanged (RouteCache stores it)."""
    assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# Fixture: ISBE-direct dataset + the real refresh
# ---------------------------------------------------------------------------

def _seed_geo_dataset(conn) -> None:
    conn.execute("CREATE TABLE isbe_committees (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO isbe_committees (id, name) VALUES (?, ?)", sorted(COMMITTEES.items()))
    conn.execute(
        "CREATE TABLE isbe_d2_reports (id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER, "
        "archived BOOLEAN DEFAULT FALSE)"
    )
    conn.executemany(
        "INSERT INTO isbe_d2_reports (id, committee_id, filed_doc_id, archived) VALUES (?, ?, ?, ?)",
        [(9001, ALPHA, 5001, False), (9002, BETA, 5002, False), (9003, GAMMA, 5003, False), (9099, ALPHA, 5099, True)],
    )
    conn.execute(
        """
        CREATE TABLE isbe_condensed_receipts (
            id INTEGER PRIMARY KEY, committee_id INTEGER, filed_doc_id INTEGER,
            last_name TEXT, first_name TEXT, received_date DATE, amount REAL,
            occupation TEXT, employer TEXT, address1 TEXT, address2 TEXT,
            city TEXT, state TEXT, zipcode TEXT, d2_part TEXT, archived BOOLEAN DEFAULT FALSE
        )
        """
    )
    rows = []
    for donor in DONORS:
        for received, amount in donor.receipts:
            rows.append((D2_FILED_DOC[donor.committee], donor.committee, donor.last, donor.first, received, amount,
                         donor.address1, donor.city, donor.state, donor.zipcode, "1A", False))
    for first, last, committee, filed_doc, part, archived, amount in FILTERED_OUT:
        rows.append((filed_doc, committee, last, first, "2026-02-15", amount, f"20 {last} St", "Chicago", "IL", "60610",
                     part, archived))
    conn.executemany(
        "INSERT INTO isbe_condensed_receipts (id, committee_id, filed_doc_id, last_name, first_name, received_date, amount, "
        "address1, city, state, zipcode, d2_part, archived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(index,) + (row[1], row[0]) + row[2:] for index, row in enumerate(rows, start=1000)],
    )
    conn.commit()


@pytest.fixture(autouse=True)
def _no_redis_and_empty_caches(monkeypatch):
    """Redis off (never touch a developer's real one) and every in-process route cache empty around each test."""
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: None)
    _empty_caches()
    yield
    _empty_caches()


def _empty_caches() -> None:
    for obj in gc.get_objects():
        if isinstance(obj, RouteCache):
            obj.invalidate()
    for module, name in ((analytics_routes, "_geo_drilldown_cache"), (federal_routes, "_federal_geo_drilldown_cache")):
        store = getattr(module, name, None)
        if isinstance(store, dict):  # the per-worker dicts this step retires
            store.clear()


@pytest.fixture
def geo_conn(tmp_path):
    db_path = str(tmp_path / "geo_sql.db")
    init_db(db_path)
    conn = get_db(db_path)
    _seed_geo_dataset(conn)
    stats = refresh_analytics_materialized(conn)
    assert stats["source"] == "bulk_receipts"
    assert stats["source_path"] == "isbe_direct"
    assert stats["donor_rows"] == len(DONORS), "fixture drift: one agg row per (donor, committee) pair"
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Statement recording
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def statements_of():
    """Yield the list that records every statement run through the compat cursor while the block runs.

    Entry: {"sql", "params", "rows" (fetched from the server), "columns" (result column names)}.
    """
    log: list[dict] = []
    original_execute = PostgresCompatCursor.execute
    original_executemany = PostgresCompatCursor.executemany
    original_fetchone = PostgresCompatCursor.fetchone
    original_fetchall = PostgresCompatCursor.fetchall

    def executemany(self, sql, seq_of_params):
        log.append({"sql": sql, "params": (), "rows": 0, "columns": ()})
        return original_executemany(self, sql, seq_of_params)

    def execute(self, sql, params=None):
        entry = {"sql": sql, "params": tuple(params or ()), "rows": 0, "columns": ()}
        log.append(entry)
        self._geo_test_entry = entry
        result = original_execute(self, sql, params)
        if self._fake_rows is None and self._cursor.description:
            entry["columns"] = tuple(column.name for column in self._cursor.description)
        return result

    def fetchone(self):
        row = original_fetchone(self)
        entry = getattr(self, "_geo_test_entry", None)
        if row is not None and entry is not None and self._fake_rows is None:
            entry["rows"] += 1
        return row

    def fetchall(self):
        rows = original_fetchall(self)
        entry = getattr(self, "_geo_test_entry", None)
        if entry is not None and self._fake_rows is None:
            entry["rows"] += len(rows)
        return rows

    PostgresCompatCursor.execute = execute
    PostgresCompatCursor.executemany = executemany
    PostgresCompatCursor.fetchone = fetchone
    PostgresCompatCursor.fetchall = fetchall
    try:
        yield log
    finally:
        PostgresCompatCursor.execute = original_execute
        PostgresCompatCursor.executemany = original_executemany
        PostgresCompatCursor.fetchone = original_fetchone
        PostgresCompatCursor.fetchall = original_fetchall


def _short(sql: str) -> str:
    return " ".join(sql.split())[:160]


def _writes(log: list[dict]) -> list[str]:
    """Statements that change anything: a GET must never backfill, create or refresh as a side effect."""
    verbs = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE", "REFRESH", "VACUUM", "ANALYZE")
    return [_short(entry["sql"]) for entry in log if entry["sql"].lstrip().upper().startswith(verbs)]


def _require_key_columns(conn) -> None:
    names = {row["name"] for row in conn.execute(f"PRAGMA table_info({AGG})").fetchall()}
    missing = {"donor_state_key", "donor_city_key"} - names
    assert not missing, f"{AGG} has no column(s) {sorted(missing)}"


# ---------------------------------------------------------------------------
# Payload parity (green today, must stay green)
# ---------------------------------------------------------------------------

def test_state_cell_pages_match_the_hand_computed_literals(geo_conn):
    """IL, all time, 10 per page: three pages of the default sort, every payload part pinned value by value.

    Kills: header computed over the page (page 1 total 7,241 vs header 10,126.5); COUNT(*) for donors (24 rows, 23 donors:
    Ann Adams gave to two committees); a state key that is the raw column or only trimmed (the "il " and " IL" Chicago rows
    and all address-only rows would drop out of the 24); fallback applied per field (the Elgin row, city NULL + state IL,
    must show an empty city); ties broken by physical order (three 900s, two 750.5s, two 500s); a lost offset (page 2 and 3);
    non-JSON values (Decimal from ROUND(...::numeric)); extra or missing payload keys."""
    for page, labels, page_total, page_count in IL_PAGES:
        payload = drill(geo_conn, "state", "IL", page=page, per_page=10)

        assert set(payload) == {"source", "rows", "summary", "pagination", "sort", "date_from", "date_to"}
        assert payload["source"] == "bulk_receipts"
        assert _labels(payload) == labels, f"page {page}"
        assert _comparable(payload["rows"]) == [_expected_row(label) for label in labels], f"page {page}"
        assert payload["summary"] == _summary(
            "state", "IL", None, ALL_TIME_HEADERS[("state", "IL", None)], label="IL",
            page_total=page_total, page_count=page_count,
        ), f"page {page}"
        assert payload["pagination"] == {"page": page, "per_page": 10, "total_rows": 24, "total_pages": 3}
        assert payload["sort"] == {"sort_by": "total_amount", "sort_dir": "desc"}
        assert payload["date_from"] is None
        assert payload["date_to"] is None
        _assert_strict_json(payload)


def test_city_cell_merges_every_spelling_and_the_address_only_rows(geo_conn):
    """Chicago, IL, 10 per page: 14 rows from "Chicago", "CHICAGO", "  chicago ", "Chicago.", "chicago" and three address-only
    shapes (NULL, '' and NULL city/state), under any spelling of the requested city.

    Kills: LOWER(TRIM(city)) as the key (misses "Chicago." and the padding); no fallback (C09, C10, C11 missing: 11 rows);
    the fallback skipped for '' (C10); a city cell that includes the state-only Elgin row (15 rows); case-sensitive matching of
    the REQUESTED city."""
    for page, labels, page_total, page_count in CHICAGO_PAGES:
        payload = drill(geo_conn, "city", "Chicago", "IL", page=page, per_page=10)
        assert _labels(payload) == labels, f"page {page}"
        assert _comparable(payload["rows"]) == [_expected_row(label) for label in labels], f"page {page}"
        assert payload["summary"] == _summary(
            "city", "Chicago", "IL", ALL_TIME_HEADERS[("city", "Chicago", "IL")], label="Chicago, IL",
            page_total=page_total, page_count=page_count,
        ), f"page {page}"
        assert payload["pagination"] == {"page": page, "per_page": 10, "total_rows": 14, "total_pages": 2}
        _assert_strict_json(payload)

    for spelling in ("CHICAGO", " chicago.", "Chi-cago", "chicago"):    # "Chi-cago": punctuation dropped, no space inserted
        payload = drill(geo_conn, "city", spelling, "il", per_page=10)
        assert payload["summary"]["detail_row_count"] == 14, spelling
        assert payload["summary"]["geo_state"] == "IL"
        assert payload["summary"]["geo_value"] == spelling.strip()
        assert payload["summary"]["geo_label"] == "Chicago, IL"
        assert _comparable(payload["rows"]) == [_expected_row(label) for label in CHICAGO_DEFAULT[:10]], spelling


def test_a_city_name_in_two_states_is_two_cells_and_the_state_only_rows_have_no_city_cell(geo_conn):
    """Springfield IL (3 rows) vs Springfield MO (2); "St. Louis" in four spellings plus an address-only row is one cell of 5;
    "O'Fallon"/"OFallon" one cell of 2; Peoria IL is the one address-only row (the 8,000 "Peoria" with no state stays out);
    Elgin (the state-only row's address text) is nothing.

    Kills: a city cell that ignores the state; punctuation turned into a space ("O'Fallon" -> "o fallon"); runs of spaces kept
    ("St  Louis", "DES  PLAINES"); the fallback invented for rows that have a state (an Elgin cell)."""
    cells = [
        ("Springfield", "IL", ["S01", "S02", "S03"]),
        ("Springfield", "MO", ["M06", "M07"]),
        ("St. Louis", "MO", ["M01", "M02", "M03", "M04", "M05"]),     # 700, 300, 120, 80, 60
        ("ST  LOUIS", "MO", ["M01", "M02", "M03", "M04", "M05"]),
        ("OFallon", "MO", ["M10", "M11"]),                              # 35, 25
        ("o'fallon", "MO", ["M10", "M11"]),
        ("Peoria", "IL", ["P01"]),
        ("DES PLAINES", "IL", ["D01", "D02"]),
        ("Naperville", "IL", ["N01", "N02"]),
        ("Elgin", "IL", []),
    ]
    for value, state, labels in cells:
        payload = drill(geo_conn, "city", value, state)
        assert _labels(payload) == labels, f"{value}, {state}"
        assert _comparable(payload["rows"]) == [_expected_row(label) for label in labels], f"{value}, {state}"
        assert payload["summary"]["detail_row_count"] == len(labels), f"{value}, {state}"


def test_cell_headers_are_the_hand_computed_literals_and_agree_with_the_geography_summary(geo_conn):
    """Every state and city cell, all time: the header literals, a single page that adds up to the header, and the number
    the geography page links with (get_geo_summary, unchanged by this step) is the number the drill-down shows.

    Kills: any normalisation drift between the two pages; a header that is not the whole cell; sum_check_delta not 0."""
    summary = get_geo_summary(geo_conn, limit_states=500, limit_cities=500)
    summary_states = {row["state"]: row for row in summary["states"]}
    summary_cities = {(row["city"], row["state"]): row for row in summary["cities"]}
    assert set(summary_states) == {"IL", "MO", "IN", "WI"}
    assert len(summary_cities) == 13

    for (geo_type, value, state), (total, contributions, donors, detail_rows) in ALL_TIME_HEADERS.items():
        payload = drill(geo_conn, geo_type, value, state, per_page=50)
        header = payload["summary"]
        assert header["total_amount"] == total, (geo_type, value, state)
        assert header["contribution_count"] == contributions, (geo_type, value, state)
        assert header["donor_count"] == donors, (geo_type, value, state)
        assert header["detail_row_count"] == detail_rows, (geo_type, value, state)
        assert header["page_total_amount"] == total, (geo_type, value, state)
        assert header["page_contribution_count"] == contributions, (geo_type, value, state)
        assert header["sum_check_delta"] == 0.0, (geo_type, value, state)
        assert payload["pagination"] == {"page": 1, "per_page": 50, "total_rows": detail_rows, "total_pages": 1}

        link = summary_states[value] if geo_type == "state" else summary_cities[(value, state)]
        assert link["total_amount"] == total, (geo_type, value, state)
        assert link["contribution_count"] == contributions, (geo_type, value, state)
        assert link["donor_count"] == donors, (geo_type, value, state)


def test_sort_tie_breaks_follow_the_documented_tuples(geo_conn):
    """The orders that only a full tie-break chain gets right, in both directions on the same data.

    Chicago by committee: Alpha, Beta, gamma pac (lower-cased), amount inside a committee; by name: the two Ann Adams rows
    split by amount; IL by count: 640 before 400 at four contributions, 750.5 eli ewing before 750.5 dan dunn at three.
    Ascending is the exact reverse of descending, so a tie order that is arbitrary-but-stable cannot satisfy both.

    Kills: ORDER BY committee_name / donor_name / total_amount alone; a sort key that is not lower-cased; a secondary key in
    the other direction."""
    cases = [
        ("city", "Chicago", "IL", "committee_name", "asc", CHICAGO_COMMITTEE_ASC),
        ("city", "Chicago", "IL", "committee_name", "desc", CHICAGO_COMMITTEE_DESC),
        ("city", "Chicago", "IL", "donor_name", "asc", CHICAGO_NAME_ASC),
        ("city", "Chicago", "IL", "donor_name", "desc", CHICAGO_NAME_DESC),
    ]
    for geo_type, value, state, sort_by, sort_dir, expected in cases:
        first = drill(geo_conn, geo_type, value, state, per_page=10, page=1, sort_by=sort_by, sort_dir=sort_dir)
        second = drill(geo_conn, geo_type, value, state, per_page=10, page=2, sort_by=sort_by, sort_dir=sort_dir)
        assert _labels(first) + _labels(second) == expected, (sort_by, sort_dir)
        assert first["sort"] == {"sort_by": sort_by, "sort_dir": sort_dir}

    count_desc = drill(geo_conn, "state", "IL", per_page=10, sort_by="contribution_count", sort_dir="desc")
    count_asc = drill(geo_conn, "state", "IL", per_page=10, sort_by="contribution_count", sort_dir="asc")
    assert _labels(count_desc) == IL_COUNT_DESC_PAGE_1
    assert _labels(count_asc) == IL_COUNT_ASC_PAGE_1


def test_name_sorts_ignore_case_split_ties_by_amount_and_follow_code_point_order(geo_conn):
    """IN is the name-order cell.  Python orders the lower-cased names by code point, so "ana muzzy" (z = U+007A) comes before
    "ana muñoz" (n-tilde = U+00F1) and the pairs that differ only by case ("al smith"/"Al Smith", "bo lee"/"Bo Lee", the
    "pat poe" triple) are ordered by amount, in both directions.

    Kills: ORDER BY donor_name without LOWER (lower-case-first or upper-case-first collations split the pairs by case, not by
    amount: one of "al smith"/"Al Smith" and "bo lee"/"Bo Lee" is wrong under either); ORDER BY LOWER(donor_name) under a
    locale collation (n-tilde sorts with n, before z); a missing amount tie-break."""
    asc = drill(geo_conn, "state", "IN", per_page=50, sort_by="donor_name", sort_dir="asc")
    desc = drill(geo_conn, "state", "IN", per_page=50, sort_by="donor_name", sort_dir="desc")
    assert _labels(asc) == IN_NAME_ASC
    assert _labels(desc) == IN_NAME_DESC
    assert [row["donor_name"] for row in asc["rows"]] == [
        "Al Smith", "al smith", "Ana Muzzy", "Ana Muñoz", "bo lee", "Bo Lee", "pat poe", "PAT POE", "Pat Poe",
    ]


def test_every_sort_direction_and_page_follows_the_documented_tuples(geo_conn):
    """Sweep: four sorts x two directions x every page of five cells, all time, against the documented key tuples.

    Kills: any sort that is applied after the page is cut, a LIMIT/OFFSET that does not follow the sort, a sort key that is
    wrong for one of the four columns, a direction applied to the first key only."""
    cells = [("state", "IL", None), ("city", "Chicago", "IL"), ("state", "MO", None), ("state", "IN", None),
             ("city", "St Louis", "MO")]
    per_page = 10
    for geo_type, value, state in cells:
        members = _cell_labels(geo_type, value, state)
        for sort_by in ("total_amount", "contribution_count", "donor_name", "committee_name"):
            for sort_dir in ("desc", "asc"):
                ordered = _sorted_labels(members, sort_by, sort_dir)
                pages = (len(ordered) + per_page - 1) // per_page
                for page in range(1, pages + 1):
                    payload = drill(geo_conn, geo_type, value, state, page=page, per_page=per_page,
                                    sort_by=sort_by, sort_dir=sort_dir)
                    where = (geo_type, value, state, sort_by, sort_dir, page)
                    assert _labels(payload) == ordered[(page - 1) * per_page: page * per_page], where
                    assert payload["pagination"] == {
                        "page": page, "per_page": per_page, "total_rows": len(ordered), "total_pages": pages,
                    }, where


def test_arguments_are_normalised_like_today(geo_conn):
    """The arguments the route passes through unchecked.

    Kills: a rewrite that drops a normalisation because the SQL "does not need it": geo_type case, the "City, ST" form, a
    geo_state next to a state-type request, per_page clamped to 10..250 (0 and None mean 50), page below 1, sort/dir trimmed
    and lower-cased with unknown values falling back to total_amount / desc, an unknown or empty state answering with the
    empty payload."""
    explicit = drill(geo_conn, "city", "Chicago", "IL", per_page=10)

    upper = drill(geo_conn, "CITY", "Chicago", "il", per_page=10)
    assert upper["rows"] == explicit["rows"]
    assert upper["summary"] == explicit["summary"]

    comma = drill(geo_conn, "city", "Chicago, IL", None, per_page=10)
    assert comma["rows"] == explicit["rows"]
    assert comma["summary"] == explicit["summary"]

    padded_comma = drill(geo_conn, "city", " St. Louis ,mo ", "", per_page=10)
    assert padded_comma["summary"]["geo_value"] == "St. Louis"
    assert padded_comma["summary"]["geo_state"] == "MO"
    assert padded_comma["summary"]["geo_label"] == "St Louis, MO"
    assert padded_comma["summary"]["detail_row_count"] == 5

    state_type = drill(geo_conn, "state", " il ", "MO", per_page=10)   # geo_state is ignored for a state cell
    assert state_type["summary"]["geo_value"] == "il"
    assert state_type["summary"]["geo_state"] == "IL"
    assert state_type["summary"]["geo_label"] == "IL"
    assert state_type["summary"]["detail_row_count"] == 24

    unknown_type = drill(geo_conn, "county", "IL", None, per_page=10)  # anything but "city" is a state request
    assert unknown_type["summary"]["geo_type"] == "state"
    assert unknown_type["summary"]["detail_row_count"] == 24

    for raw, expected in ((3, 10), (1000, 250), (0, 50), (None, 50), (75, 75)):
        payload = drill(geo_conn, "state", "IL", per_page=raw)
        assert payload["pagination"]["per_page"] == expected, raw
        assert len(payload["rows"]) == min(24, expected), raw

    for raw in (0, -3, None):
        payload = drill(geo_conn, "state", "IL", page=raw, per_page=10)
        assert payload["pagination"]["page"] == 1, raw
        assert _labels(payload) == IL_DEFAULT[:10], raw

    messy = drill(geo_conn, "state", "IL", per_page=10, sort_by=" Contribution_Count ", sort_dir=" ASC ")
    assert messy["sort"] == {"sort_by": "contribution_count", "sort_dir": "asc"}
    assert _labels(messy) == IL_COUNT_ASC_PAGE_1

    fallback = drill(geo_conn, "state", "IL", per_page=10, sort_by="nonsense", sort_dir="sideways")
    assert fallback["sort"] == {"sort_by": "total_amount", "sort_dir": "desc"}
    assert _labels(fallback) == IL_DEFAULT[:10]

    for geo_type, value, state in (("state", "", None), ("state", "   ", None), ("city", "Chicago", None), ("city", "Chicago", "")):
        payload = drill(geo_conn, geo_type, value, state, page=4, per_page=10, date_from="", date_to=None)
        assert payload["rows"] == [], (geo_type, value, state)
        assert payload["summary"]["detail_row_count"] == 0, (geo_type, value, state)
        assert payload["summary"]["total_amount"] == 0.0, (geo_type, value, state)
        assert payload["summary"]["geo_state"] == "", (geo_type, value, state)
        assert payload["pagination"] == {"page": 4, "per_page": 10, "total_rows": 0, "total_pages": 0}, (geo_type, value, state)
        assert payload["date_from"] is None
        assert payload["date_to"] is None
        assert payload["source"] == "bulk_receipts"
        _assert_strict_json(payload)


def test_a_page_past_the_end_is_clamped_and_an_empty_cell_keeps_the_requested_page(geo_conn):
    """Page 99 of a 3-page cell answers page 3 (rows and echoed page); an empty cell answers page 5 with no pages.

    Kills: OFFSET computed before the row count is known (empty rows for page 99), the echoed page left at 99, an empty cell
    whose page is forced to 1."""
    clamped = drill(geo_conn, "state", "IL", page=99, per_page=10)
    assert clamped["pagination"] == {"page": 3, "per_page": 10, "total_rows": 24, "total_pages": 3}
    assert _labels(clamped) == IL_DEFAULT[20:24]
    assert clamped["summary"]["page_total_amount"] == 270.0
    assert clamped["summary"]["page_contribution_count"] == 5

    empty = drill(geo_conn, "city", "Atlantis", "IL", page=5, per_page=10)
    assert empty["rows"] == []
    assert empty["pagination"] == {"page": 5, "per_page": 10, "total_rows": 0, "total_pages": 0}
    assert empty["summary"] == _summary("city", "Atlantis", "IL", (0.0, 0, 0, 0), label="Atlantis, IL", page_total=0.0, page_count=0)


def test_filtered_out_receipts_are_never_counted_on_either_path(geo_conn):
    """An archived receipt (9,999), a D-2 part-5 receipt (8,888) and an archived receipt of a superseded D-2 report
    (7,777: the receipt row itself is archived, as the condensed view encodes an amendment), all dated
    inside the window and all from Chicago, appear nowhere; neither does the -40 refund (C01 keeps 900 / 600).

    Kills: a windowed rewrite that drops _ISBE_DONOR_RECEIPT_FILTER_SQL or `amount > 0`; an agg rebuilt without them."""
    for kwargs in ({}, WINDOW):
        seen = set()
        for page in (1, 2, 3):
            seen |= {row["donor_name"] for row in drill(geo_conn, "state", "IL", page=page, per_page=10, **kwargs)["rows"]}
        assert seen.isdisjoint(FILTERED_OUT_NAMES), kwargs
        assert drill(geo_conn, "state", "IL", **kwargs)["summary"]["total_amount"] == (7411.5 if kwargs else 10126.5)


def test_foreign_source_agg_rows_in_the_same_cell_are_ignored(geo_conn):
    """A row of another source (the legacy 'contributions' source) in IL / Chicago with 99,999 changes nothing.

    Kills: a missing `source = ?` predicate.  (The foreign row gets its key columns when the table has them, so a missing
    predicate is the only reason it could show up.)"""
    columns = {row["name"] for row in geo_conn.execute(f"PRAGMA table_info({AGG})").fetchall()}
    values = {
        "source": "contributions", "donor_key": "donor:99999", "donor_name": "Foreign Source Donor",
        "donor_address": "1 Foreign St, Chicago, IL 60601", "donor_city": "Chicago", "donor_state": "IL",
        "committee_id": "101", "committee_name": "Alpha Committee", "total_amount": 99999.0, "contribution_count": 9,
        "donor_state_key": "IL", "donor_city_key": "chicago",
    }
    present = {name: value for name, value in values.items() if name in columns}
    geo_conn.execute(
        f"INSERT INTO {AGG} ({', '.join(present)}) VALUES ({', '.join('?' for _ in present)})", tuple(present.values())
    )
    geo_conn.commit()

    state = drill(geo_conn, "state", "IL", per_page=10)
    city = drill(geo_conn, "city", "Chicago", "IL", per_page=10)
    assert state["summary"]["total_amount"] == 10126.5
    assert state["summary"]["detail_row_count"] == 24
    assert city["summary"]["total_amount"] == 7296.5
    assert city["summary"]["detail_row_count"] == 14
    assert "Foreign Source Donor" not in {row["donor_name"] for row in state["rows"] + city["rows"]}


# ---------------------------------------------------------------------------
# Custom-window path (live GROUP BY): payload parity
# ---------------------------------------------------------------------------

def test_windowed_cells_match_the_hand_computed_literals(geo_conn):
    """2026-02-01..2026-03-31 inclusive: only the receipts inside count (counts and totals shrink for partly inside donors).

    Chicago 13 rows / 5,506.5 / 20 contributions / 12 donors (C11 is wholly outside, C02's 01-31 gift is out and its 02-01 gift
    in); MO 8 rows / 1,090 (M03 on 04-01 and M06 on 2025-12-31 are out; M02 on 03-31 is in); IL 21 rows / 7,411.5 in three
    pages, the state-only Elgin row (02-01) and the address-only Peoria row both inside.

    Kills: a pushed-down WHERE that drops the address-only / state-only rows (fallback only in the all-time path); an
    exclusive bound (02-01 and 03-31 rows lost); the window ignored (all-time numbers); donor_key empty."""
    chicago = drill(geo_conn, "city", "Chicago", "IL", per_page=50, **WINDOW)
    assert _labels(chicago) == WINDOW_CHICAGO_DEFAULT
    assert _comparable(chicago["rows"], window=True) == [_expected_row(l, window=True) for l in WINDOW_CHICAGO_DEFAULT]
    assert chicago["summary"] == _summary("city", "Chicago", "IL", WINDOW_HEADERS[("city", "Chicago", "IL")],
                                           label="Chicago, IL", page_total=5506.5, page_count=20)
    assert chicago["pagination"] == {"page": 1, "per_page": 50, "total_rows": 13, "total_pages": 1}
    assert chicago["date_from"] == "2026-02-01"
    assert chicago["date_to"] == "2026-03-31"
    assert chicago["source"] == "bulk_receipts"
    _assert_strict_json(chicago)

    missouri = drill(geo_conn, "state", "MO", per_page=50, **WINDOW)
    assert _labels(missouri) == WINDOW_MO_DEFAULT
    assert _comparable(missouri["rows"], window=True) == [_expected_row(l, window=True) for l in WINDOW_MO_DEFAULT]
    assert missouri["summary"] == _summary("state", "MO", None, WINDOW_HEADERS[("state", "MO", None)], label="MO",
                                            page_total=1090.0, page_count=9)

    st_louis = drill(geo_conn, "city", "St. Louis", "MO", per_page=50, **WINDOW)
    assert _labels(st_louis) == WINDOW_ST_LOUIS_DEFAULT
    assert st_louis["summary"]["total_amount"] == 840.0
    assert st_louis["summary"]["donor_count"] == 4

    page_totals = [(1, 5511.0, 13), (2, 1840.5, 18), (3, 60.0, 2)]   # 21 rows: 10 + 10 + 1 (sums in the comment below)
    for page, page_total, page_count in page_totals:
        payload = drill(geo_conn, "state", "IL", page=page, per_page=10, **WINDOW)
        labels = WINDOW_IL_DEFAULT[(page - 1) * 10: page * 10]
        assert _labels(payload) == labels, page
        assert _comparable(payload["rows"], window=True) == [_expected_row(l, window=True) for l in labels], page
        assert payload["summary"] == _summary("state", "IL", None, WINDOW_HEADERS[("state", "IL", None)], label="IL",
                                               page_total=page_total, page_count=page_count), page
        assert payload["pagination"] == {"page": page, "per_page": 10, "total_rows": 21, "total_pages": 3}, page
    # page 1: 900+750.5+600+500.5+500+500+500+460+450+350 = 5511 (1+3+1+2+1+1+1+1+1+1 = 13); page 2: 320+300+250.5+220+200+175+
    # 120+90+90+75 = 1840.5 (2+3+2+2+2+1+1+3+1+1 = 18); page 3: 60 (2).


def test_windowed_headers_agree_with_the_geography_summary_for_every_cell(geo_conn):
    """Same cross-check as the all-time one, inside the window, for all 17 cells (Oak Park is empty in the window).

    Kills: normalisation or fallback drift between the windowed drill-down and the page that links to it."""
    summary = get_geo_summary(geo_conn, limit_states=500, limit_cities=500, **WINDOW)
    summary_states = {row["state"]: row for row in summary["states"]}
    summary_cities = {(row["city"], row["state"]): row for row in summary["cities"]}

    for (geo_type, value, state), (total, contributions, donors, detail_rows) in WINDOW_HEADERS.items():
        payload = drill(geo_conn, geo_type, value, state, per_page=50, **WINDOW)
        header = payload["summary"]
        assert header["total_amount"] == total, (geo_type, value, state)
        assert header["contribution_count"] == contributions, (geo_type, value, state)
        assert header["donor_count"] == donors, (geo_type, value, state)
        assert header["detail_row_count"] == detail_rows, (geo_type, value, state)
        assert header["sum_check_delta"] == 0.0, (geo_type, value, state)

        link = summary_states.get(value) if geo_type == "state" else summary_cities.get((value, state))
        if detail_rows == 0:
            assert link is None, (geo_type, value, state)
            continue
        assert link["total_amount"] == total, (geo_type, value, state)
        assert link["contribution_count"] == contributions, (geo_type, value, state)
        assert link["donor_count"] == donors, (geo_type, value, state)


def test_every_sort_direction_and_page_follows_the_documented_tuples_in_the_window(geo_conn):
    """The sweep again on the windowed path, where the sort/limit is a second implementation.

    Kills: a windowed ORDER BY that differs from the all-time one (different tie-breaks, different key per column)."""
    cells = [("state", "IL", None), ("city", "Chicago", "IL"), ("state", "MO", None)]
    per_page = 10
    for geo_type, value, state in cells:
        members = _cell_labels(geo_type, value, state, window=True)
        for sort_by in ("total_amount", "contribution_count", "donor_name", "committee_name"):
            for sort_dir in ("desc", "asc"):
                ordered = _sorted_labels(members, sort_by, sort_dir, window=True)
                pages = (len(ordered) + per_page - 1) // per_page
                for page in range(1, pages + 1):
                    payload = drill(geo_conn, geo_type, value, state, page=page, per_page=per_page,
                                    sort_by=sort_by, sort_dir=sort_dir, **WINDOW)
                    where = (geo_type, value, state, sort_by, sort_dir, page)
                    assert _labels(payload) == ordered[(page - 1) * per_page: page * per_page], where
                    assert payload["pagination"]["total_rows"] == len(ordered), where


def test_open_ended_windows_use_only_the_bound_given(geo_conn):
    """date_from alone and date_to alone (the route passes '' for a missing bound).

    From 2026-04-01 Chicago has Ann Adams's 300 (04-01, first day) and Ivy Ives's two April gifts (200): 500 in 3 contributions,
    2 donors.  Up to 2026-01-31 it has ben baker's 440 (01-31, last day), Dan Dunn's 250, Fay Finn's three 2025 gifts (300) and
    Kay Kerr's three (300): 1,290 in 8, 4 donors.

    Kills: a rewrite that needs both bounds (BETWEEN) or treats '' as a date."""
    after = drill(geo_conn, "city", "Chicago", "IL", per_page=50, date_from="2026-04-01", date_to="")
    assert _labels(after) == ["C01", "C09"]
    assert [(row["total_amount"], row["contribution_count"]) for row in after["rows"]] == [(300.0, 1), (200.0, 2)]
    assert after["summary"]["total_amount"] == 500.0
    assert after["summary"]["contribution_count"] == 3
    assert after["summary"]["donor_count"] == 2
    assert after["date_from"] == "2026-04-01"
    assert after["date_to"] is None

    before = drill(geo_conn, "city", "Chicago", "IL", per_page=50, date_from="", date_to="2026-01-31")
    assert _labels(before) == ["C02", "C11", "C06", "C04"]          # 440 | 300/3 kay kerr | 300/3 fay finn | 250
    assert [(row["total_amount"], row["contribution_count"]) for row in before["rows"]] == [
        (440.0, 1), (300.0, 3), (300.0, 3), (250.0, 1)]
    assert before["summary"]["total_amount"] == 1290.0
    assert before["summary"]["contribution_count"] == 8
    assert before["summary"]["donor_count"] == 4
    assert before["date_from"] is None
    assert before["date_to"] == "2026-01-31"


# ---------------------------------------------------------------------------
# All-time path: no Python-side scan (red today)
# ---------------------------------------------------------------------------

def test_all_time_statements_never_return_more_than_per_page_rows(geo_conn):
    """Every statement of an all-time call returns at most per_page rows -- both cells, every sort and direction, first page,
    second page and a page past the end, with the date arguments as the route passes them ('' and None).

    Today one statement returns every one of the 49 agg rows, which is the whole cost (1.3M rows on prod).  The cells hold 24
    / 14 / 11 rows against a per_page of 10, so a statement that fetches the cell (to count it, to sort it, to list its
    donors) is caught as well as one that fetches the table.

    Kills: filter-in-SQL but sort/slice/count in Python; SELECT DISTINCT donor_key for the donor count; GROUP BY to
    count; a statement per page run in a loop is not covered (accepted hole)."""
    violations = []
    calls = 0
    cells = [("state", "IL", None), ("city", "Chicago", "IL"), ("state", "MO", None)]
    for date_args in ({"date_from": "", "date_to": ""}, {"date_from": None, "date_to": None}):
        for geo_type, value, state in cells:
            for sort_by in ("total_amount", "contribution_count", "donor_name", "committee_name"):
                for sort_dir in ("desc", "asc"):
                    for page in (1, 2, 99):
                        with statements_of() as log:
                            drill(geo_conn, geo_type, value, state, page=page, per_page=10, sort_by=sort_by,
                                  sort_dir=sort_dir, **date_args)
                        calls += 1
                        for entry in log:
                            if entry["rows"] > 10:
                                violations.append((entry["rows"], geo_type, value, sort_by, sort_dir, page, _short(entry["sql"])))
    assert calls == 144
    assert not violations, f"{len(violations)} of {calls} calls ran a statement returning more than 10 rows; first: {violations[:2]}"


def test_all_time_path_reads_the_agg_and_does_not_regroup_the_receipts(geo_conn):
    """With a current agg the all-time call touches analytics_donor_committee_agg and never runs a GROUP BY over the receipts
    (the live path stays for custom windows and for an agg that is missing or stale).

    Kills: an 'always live' rewrite that pushes the filter into the receipts GROUP BY: it would pass the row bound and still
    cost seconds on 6.5M receipts."""
    with statements_of() as log:
        drill(geo_conn, "state", "IL", per_page=10)
        drill(geo_conn, "city", "Chicago", "IL", per_page=10)
    assert _writes(log) == []
    regrouped = [
        _short(entry["sql"]) for entry in log
        if "group by" in entry["sql"].lower()
        and any(table in entry["sql"].lower() for table in ("isbe_condensed_receipts", "bulk_receipts_clean", "isbe_receipts"))
    ]
    assert not regrouped, regrouped
    assert any(AGG in entry["sql"] for entry in log), "no statement reads the agg table"


# ---------------------------------------------------------------------------
# The key columns and the index (red today)
# ---------------------------------------------------------------------------

# (state key, city key) per donor: state upper-cased and trimmed, city = _city_key of the city the payload shows.  A row with a
# state and no city has an empty (or NULL) city key; a row in no cell has an empty (or NULL) state key and its city key is not
# pinned.  Hand-derived from the DONORS table: "il " / " IL" -> IL; "Chicago." / "  chicago " / "CHICAGO" -> chicago;
# "DES  PLAINES" -> des plaines; "O'Fallon" / "OFallon" -> ofallon; address-only rows -> the parsed (city, state).
def _expected_keys(donor: Donor) -> tuple[str, str | None]:
    city, state = donor.cell
    return state, (city.lower() if state else None)


def test_refresh_fills_the_geo_key_columns_for_every_row(geo_conn):
    """After refresh_analytics_materialized every agg row carries the normalised keys, address-only rows included.

    Kills: keys left NULL (the whole cell disappears); keys filled only where a city or state column is present; the
    fallback keyed per field (E01 must have an empty city key, not 'il 60120'); a state key that keeps case or padding
    ('il ', ' IL'); a city key that keeps punctuation or double spaces."""
    _require_key_columns(geo_conn)
    rows = geo_conn.execute(
        f"SELECT donor_name, committee_id, donor_state_key, donor_city_key FROM {AGG} WHERE source = 'bulk_receipts'"
    ).fetchall()
    assert len(rows) == len(DONORS)
    by_label = {LABEL_BY_ROW[(row["donor_name"], int(row["committee_id"]))]: row for row in rows}
    assert set(by_label) == set(SPEC)

    for label, donor in SPEC.items():
        row = by_label[label]
        expected_state, expected_city = _expected_keys(donor)
        assert (row["donor_state_key"] or "") == expected_state, label
        if expected_city is not None:
            assert (row["donor_city_key"] or "") == expected_city, label


def test_schema_file_declares_the_geo_index():
    """database/schema.sql carries the index statement (comments do not count): name, table, column order, DESC, IF NOT EXISTS.

    Kills: a different column order (the state/city equality probe would not be a prefix), no DESC (the page needs no sort),
    no IF NOT EXISTS (init-db runs the file every time), a commented-out statement."""
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    pattern = (
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+idx_analytics_donor_committee_agg_geo\s+ON\s+analytics_donor_committee_agg\s*"
        r"\(\s*source\s*,\s*donor_state_key\s*,\s*donor_city_key\s*,\s*total_amount\s+DESC\s*\)\s*;"
    )
    assert re.search(pattern, text, flags=re.IGNORECASE), "no CREATE INDEX IF NOT EXISTS idx_analytics_donor_committee_agg_geo"


def _index_columns(conn, name: str) -> list[str] | None:
    row = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() AND indexname = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    match = re.search(r"USING btree \((?P<columns>.*)\)\s*$", row["indexdef"])
    assert match, row["indexdef"]
    return [part.strip() for part in match.group("columns").split(",")]


def _column_types(conn) -> dict[str, str]:
    return {row["name"]: row["type"] for row in conn.execute(f"PRAGMA table_info({AGG})").fetchall()}


def test_a_fresh_schema_has_the_key_columns_and_the_index(geo_conn):
    """init-db on an empty database creates both TEXT columns and the index with the pinned column list."""
    types = _column_types(geo_conn)
    assert types.get("donor_state_key") == "text", f"{AGG}.donor_state_key is {types.get('donor_state_key')!r}, not a TEXT column"
    assert types.get("donor_city_key") == "text", f"{AGG}.donor_city_key is {types.get('donor_city_key')!r}, not a TEXT column"
    assert _index_columns(geo_conn, GEO_INDEX) == ["source", "donor_state_key", "donor_city_key", "total_amount DESC"]


def _simulate_the_previous_release(conn) -> None:
    """Put the database back where the last release left it: no key columns, no geo index, agg materialized at version 3."""
    conn.execute(f"ALTER TABLE {AGG} DROP COLUMN IF EXISTS donor_state_key")
    conn.execute(f"ALTER TABLE {AGG} DROP COLUMN IF EXISTS donor_city_key")
    conn.execute("UPDATE analytics_materialized_meta SET materialization_version = 3 WHERE source = 'bulk_receipts'")
    conn.commit()
    assert "donor_state_key" not in _column_types(conn)
    assert _index_columns(conn, GEO_INDEX) is None


def test_init_db_adds_the_columns_and_the_index_to_an_existing_agg_table(geo_conn):
    """An existing agg table (prod's) has neither column; ensure_schema -- what init-db runs -- must add both and then create the
    index on them without failing.

    Kills: the columns added to CREATE TABLE only (the table exists, so they never appear, and CREATE INDEX on them fails: the
    classic order trap in ensure_schema, which runs _ensure_column BEFORE schema.sql); the index declared without the columns;
    DDL that is not idempotent (init-db runs on every deploy)."""
    _simulate_the_previous_release(geo_conn)
    try:
        ensure_schema(geo_conn)
    except Exception as exc:  # noqa: BLE001 - reported as the test's failure, with the cause
        pytest.fail(f"init-db crashed on an agg table without the key columns: {type(exc).__name__}: {exc}")
    types = _column_types(geo_conn)
    assert types.get("donor_state_key") == "text", f"init-db left {AGG}.donor_state_key as {types.get('donor_state_key')!r}"
    assert types.get("donor_city_key") == "text", f"init-db left {AGG}.donor_city_key as {types.get('donor_city_key')!r}"
    assert _index_columns(geo_conn, GEO_INDEX) == ["source", "donor_state_key", "donor_city_key", "total_amount DESC"]
    try:
        ensure_schema(geo_conn)       # init-db runs on every deploy: the second run finds everything in place
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"a second init-db run crashed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Review fix R6 (.claude/plans/load-times.md, "Review fixes"): init-db must not ALTER the agg table on every deploy.
# schema.sql's ALTER TABLE ... ADD COLUMN IF NOT EXISTS takes ACCESS EXCLUSIVE on the table before it looks for the column, so
# each init-db queues every reader of the 1.3M-row agg behind the longest in-flight request.  The columns an existing table
# lacks are added by ensure_schema through _ensure_column (ALTER only when the column is missing); schema.sql keeps the CREATE
# TABLE columns and the CREATE INDEX that uses them (both pinned above and unchanged).
# ---------------------------------------------------------------------------

def test_schema_file_runs_no_alter_table_statement():
    """database/schema.sql contains no ALTER TABLE statement (comments do not count): init-db runs the whole file on every deploy.

    The file's only two were the ADD COLUMN IF NOT EXISTS pair on analytics_donor_committee_agg; nothing may take their place.

    Kills: the pair kept next to the new _ensure_column calls; the pair rewritten as a DO block or put on a line with another
    statement (the search is not anchored to a line start); any other table altered the same way.  A commented-out ALTER is
    not a statement and is allowed."""
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    statements = [" ".join(found.split()) for found in re.findall(r"\bALTER\s+TABLE\b[^;]*", text, flags=re.IGNORECASE)]
    assert statements == [], (
        f"database/schema.sql still runs {len(statements)} ALTER TABLE statement(s) on every init-db: {statements}; delete them "
        "and add the columns in database.connection.ensure_schema with _ensure_column"
    )


def test_ensure_schema_adds_the_geo_key_columns_through_ensure_column(geo_conn, monkeypatch):
    """ensure_schema adds donor_state_key and donor_city_key to an existing agg table by asking _ensure_column for each (it
    ALTERs only when the column is missing); afterwards both columns and the geo index are in place.

    Same setup as the init-db test above (the previous release's table: no key columns, no geo index).  _ensure_column is
    wrapped, not replaced -- the real helper still does the work -- and every (table, column) it is asked for is recorded.

    Kills: the columns left to schema.sql's ALTER (nothing is recorded); one column of the pair; the right column on the wrong
    table; an ALTER written inline in ensure_schema instead of through the helper; the calls placed after the schema script
    (its CREATE INDEX on a missing column crashes); a helper call whose effect is lost (type and index are checked at the end)."""
    calls: list[tuple[str, str]] = []
    real_ensure_column = connection_db._ensure_column

    def recording_ensure_column(conn, table_name, column_name, column_sql):
        calls.append((table_name, column_name))
        return real_ensure_column(conn, table_name, column_name, column_sql)

    monkeypatch.setattr(connection_db, "_ensure_column", recording_ensure_column)
    _simulate_the_previous_release(geo_conn)
    try:
        ensure_schema(geo_conn)
    except Exception as exc:  # noqa: BLE001 - reported as the test's failure, with the cause
        pytest.fail(f"init-db crashed on an agg table without the key columns: {type(exc).__name__}: {exc}")

    agg_columns_asked_for = [column for table, column in calls if table == AGG]
    assert (AGG, "donor_state_key") in calls, (
        f"ensure_schema never called _ensure_column for {AGG}.donor_state_key (it asked for {agg_columns_asked_for} on {AGG}); "
        "add the call before conn.executescript(schema)"
    )
    assert (AGG, "donor_city_key") in calls, (
        f"ensure_schema never called _ensure_column for {AGG}.donor_city_key (it asked for {agg_columns_asked_for} on {AGG}); "
        "add the call before conn.executescript(schema)"
    )
    types = _column_types(geo_conn)
    assert types.get("donor_state_key") == "text", f"init-db left {AGG}.donor_state_key as {types.get('donor_state_key')!r}"
    assert types.get("donor_city_key") == "text", f"init-db left {AGG}.donor_city_key as {types.get('donor_city_key')!r}"
    assert _index_columns(geo_conn, GEO_INDEX) == ["source", "donor_state_key", "donor_city_key", "total_amount DESC"]


def test_an_agg_built_before_the_columns_existed_still_answers_correctly(geo_conn):
    """The deploy window: init-db has added the (empty) columns but refresh-analytics has not run yet.  The all-time drill-down
    must still return the right rows -- by reading the live data, by backfilling in init-db, or by treating the agg as stale --
    never an empty page for every state, and without writing anything while serving a GET.  (The live path's donor_key is a
    hash, so the rows are compared without it.)

    Kills: reading the new keys unconditionally (every cell empty until the next weekly refresh); a backfill run inside the
    request (an UPDATE over 1.3M rows under a web worker's statement timeout)."""
    _simulate_the_previous_release(geo_conn)
    ensure_schema(geo_conn)

    with statements_of() as log:
        state = drill(geo_conn, "state", "IL", per_page=10)
    assert _writes(log) == []
    assert _labels(state) == IL_DEFAULT[:10]
    assert _comparable(state["rows"], window=True) == [
        _expected_row(label, window=True) | {"total_amount": SPEC[label].total, "contribution_count": SPEC[label].count}
        for label in IL_DEFAULT[:10]
    ]
    assert state["summary"]["total_amount"] == 10126.5
    assert state["summary"]["detail_row_count"] == 24
    assert state["summary"]["donor_count"] == 23

    city = drill(geo_conn, "city", "Chicago", "IL", per_page=10)
    assert _labels(city) == CHICAGO_DEFAULT[:10]
    assert city["summary"]["total_amount"] == 7296.5
    assert city["summary"]["detail_row_count"] == 14


def test_the_city_cell_page_statement_uses_the_geo_index(geo_conn):
    """The statement that fetches a city cell's page can use idx_analytics_donor_committee_agg_geo: with sequential scans
    switched off the planner chooses that index.  (The state cell is not asserted: its default order is not the index's.)

    Kills: the index added but the predicate written so that it cannot be used (UPPER(TRIM(donor_state)) = ? on the raw
    columns, a function over the key columns, a LIKE).  A cell query with no source predicate still uses the index (a full
    index scan); the foreign-source test is what catches that."""
    assert _index_columns(geo_conn, GEO_INDEX) is not None, f"{GEO_INDEX} does not exist"
    with statements_of() as log:
        drill(geo_conn, "city", "Chicago", "IL", per_page=10)
    page_statements = [e for e in log if AGG in e["sql"] and e["rows"] >= 2]     # the header and the probes return one row
    assert page_statements, "no statement over the agg table returned the cell's rows"

    other = get_db(None)
    try:
        other.execute("SET enable_seqscan = off")
        for entry in page_statements:
            plan = "\n".join(str(next(iter(row.values()))) for row in other.execute("EXPLAIN " + entry["sql"], entry["params"]).fetchall())
            assert GEO_INDEX in plan, f"plan does not use {GEO_INDEX}:\n{plan}\nfor: {_short(entry['sql'])}"
    finally:
        other.close()


def test_a_refresh_from_the_contributions_source_fills_the_keys_too(analytics_conn):
    """The legacy source has no city/state columns, so every key comes from the address fallback.  Before and after
    refresh_analytics_materialized the all-time drill-down is the same (live path vs agg path).

    Kills: keys filled in the ISBE refresh only; a fallback that works for rows with a NULL city/state column but not for rows
    that never had the column."""
    def payloads():
        out = {}
        for cell in [("state", "IL", None), ("state", "MO", None), ("city", "Chicago", "IL"), ("city", "St. Louis", "MO")]:
            payload = drill(analytics_conn, cell[0], cell[1], cell[2], per_page=10)
            payload["rows"] = _comparable(payload["rows"])      # committee_id as a number: its type is not pinned
            out[cell] = payload
        return out

    before = payloads()
    assert before[("state", "IL", None)]["summary"]["detail_row_count"] == 4          # Alice, Bob, Carol, Eve
    assert before[("city", "Chicago", "IL")]["summary"]["detail_row_count"] == 2       # "Chicago" and "CHICAGO"

    stats = refresh_analytics_materialized(analytics_conn)
    assert stats["source"] == "contributions"
    assert payloads() == before


# ---------------------------------------------------------------------------
# Routes: RouteCache("analytics_geo_drilldown")
# ---------------------------------------------------------------------------

class FakeRedis:
    """The few commands RouteCache uses, over a dict: what a second gunicorn worker would see."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = int(ttl)
        return True

    def set(self, key, value, nx=False, px=None, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, *keys):
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    def scan(self, cursor=0, match=None, count=None):
        return 0, [key for key in list(self.store) if fnmatch.fnmatchcase(key, match or "*")]

    def ping(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(cache_backend, "_get_redis_client", lambda: client)
    return client


@pytest.fixture
def geo_builds(monkeypatch):
    """Record every call the route makes to the builder (through the module name, house pattern) and pass it through."""
    calls: list[dict] = []
    real = analytics_db.get_geo_drilldown

    def spy(conn, *args, **kwargs):
        calls.append(kwargs)
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(analytics_db, "get_geo_drilldown", spy)           # a route that reaches it as analytics_db.<name>
    if hasattr(analytics_routes, "get_geo_drilldown"):                      # ... or by the name it imports (today's way)
        monkeypatch.setattr(analytics_routes, "get_geo_drilldown", spy)
    return calls


GEO_ROUTE = "/analytics/geo-drilldown"
GEO_PREFIX = "ilcf:cache:analytics_geo_drilldown:"
BASE_QUERY = {"geo_type": "state", "geo_value": "IL", "period": "all", "per_page": 17}


def _get(client, query: dict):
    response = client.get(f"{GEO_ROUTE}?{urlencode({k: v for k, v in query.items() if v is not None})}")
    assert response.status_code == 200, (query, response.status_code)
    return response


def _geo_keys(redis_client: FakeRedis) -> list[str]:
    return sorted(key for key in redis_client.store if key.startswith(GEO_PREFIX))


def _find(obj, key):
    """First value stored under `key` anywhere in a parsed JSON document (the entry may be wrapped by the cache layer)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def test_the_route_stores_the_drilldown_in_the_shared_route_cache(analytics_app, fake_redis, geo_builds):
    """Two identical GETs build once, and the entry sits in the shared store under RouteCache("analytics_geo_drilldown")'s
    namespace with the configured TTL (or twice it, the stale-while-revalidate form planned for P7) -- not in a per-worker dict.

    Kills: the per-worker dict kept (nothing reaches Redis: a second gunicorn worker would rebuild the 8-15 s page); another
    cache name; a TTL hard-coded instead of read from ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS; two builds for one request."""
    analytics_app.config.update(ROUTE_PERF_CACHE_ENABLED=True, ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS=1234)
    client = analytics_app.test_client()

    first = _get(client, BASE_QUERY)
    second = _get(client, BASE_QUERY)

    assert len(geo_builds) == 1
    keys = _geo_keys(fake_redis)
    assert len(keys) == 1, f"keys in the shared store: {sorted(fake_redis.store)}"
    assert re.fullmatch(re.escape(GEO_PREFIX) + r"[0-9a-f]{40}", keys[0])
    assert fake_redis.ttls[keys[0]] in (1234, 2468)
    stored = json.loads(fake_redis.store[keys[0]])
    summary = _find(stored, "summary")
    assert summary is not None, stored
    assert summary["geo_label"] == "IL"
    assert "Alice Donor" in fake_redis.store[keys[0]]
    assert b"Alice Donor" in first.get_data()
    assert b"Alice Donor" in second.get_data()


def test_the_route_cache_key_separates_every_request_dimension(analytics_app, fake_redis, geo_builds):
    """Each dimension of the request is its own entry (so one request can never be answered with another's page) and the
    presentation-only parameters share the base entry (so a crawler varying them does not rebuild).

    Kills: a key that drops page, per_page, sort, dir, geo_type, geo_value, geo_state or the time window; a key that takes in
    source_page / load_mode / months / the geo limits and misses on every variation."""
    analytics_app.config.update(ROUTE_PERF_CACHE_ENABLED=True, ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS=600)
    client = analytics_app.test_client()
    variants = [
        {},
        {"page": 2},
        {"per_page": 18},
        {"sort": "donor_name"},
        {"dir": "asc"},
        {"geo_value": "MO"},
        {"geo_type": "city"},                                     # differs from the base by the type alone
        {"geo_type": "city", "geo_value": "Chicago", "geo_state": "IL"},
        {"geo_type": "city", "geo_value": "Chicago", "geo_state": "MO"},
        {"period": None, "date_from": "2026-02-01", "date_to": "2026-03-31"},
        {"period": "cycle:2024"},
    ]
    queries = [{**BASE_QUERY, **variant} for variant in variants]

    for query in queries:
        _get(client, query)
    assert len(geo_builds) == len(queries), "every distinct request must build"

    for query in queries:
        _get(client, query)
    assert len(geo_builds) == len(queries), "a repeated request must be served from the cache"

    for presentation in ({"source_page": "overview"}, {"load_mode": "full"}, {"months": 6},
                         {"geo_state_limit": 5}, {"geo_city_limit": 30}):
        _get(client, {**BASE_QUERY, **presentation})
    assert len(geo_builds) == len(queries), "presentation-only parameters must hit the base entry"

    assert len(_geo_keys(fake_redis)) == len(queries), "one shared-store entry per distinct request"


def test_the_route_cache_honours_the_perf_cache_flag(analytics_app, fake_redis, geo_builds):
    """ROUTE_PERF_CACHE_ENABLED false: every request builds and nothing is written to the shared store.

    Kills: RouteCache used unconditionally."""
    analytics_app.config.update(ROUTE_PERF_CACHE_ENABLED=False)
    client = analytics_app.test_client()

    _get(client, BASE_QUERY)
    _get(client, BASE_QUERY)

    assert len(geo_builds) == 2
    assert _geo_keys(fake_redis) == []


def test_a_refresh_drops_the_cached_pages(analytics_app, fake_redis, geo_builds):
    """With a 6 h TTL a page cached before refresh-analytics would be served stale all day: refresh_analytics_materialized
    flushes the shared route caches, so the next identical GET builds again.

    Kills: a cache outside RouteCache's `ilcf:cache:` namespace (the flush never reaches it); a per-worker dict (a refresh in
    the CLI process cannot clear a web worker's dict at all)."""
    analytics_app.config.update(ROUTE_PERF_CACHE_ENABLED=True, ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS=21600)
    client = analytics_app.test_client()
    _get(client, BASE_QUERY)
    _get(client, BASE_QUERY)
    assert len(geo_builds) == 1

    conn = get_db(analytics_app.config["DATABASE_PATH"])
    try:
        refresh_analytics_materialized(conn)
    finally:
        conn.close()
    assert _geo_keys(fake_redis) == []

    _get(client, BASE_QUERY)
    assert len(geo_builds) == 2, "the page cached before the refresh was served again"


def test_the_default_ttl_is_the_analytics_default():
    """config.ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS defaults to 21600 (read in a clean process with the variable unset).

    Kills: the 180 s default kept (a fresh page every three minutes under crawler load)."""
    env = {key: value for key, value in os.environ.items() if key != "ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS"}
    result = subprocess.run(
        [sys.executable, "-c", "import config; print(config.ANALYTICS_GEO_DRILLDOWN_CACHE_TTL_SECONDS)"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "21600"


def test_the_federal_route_stores_its_drilldown_in_the_shared_cache(federal_app, fake_redis, monkeypatch):
    """Same move for /federal-finance/geo-drilldown: two identical GETs build once and the entry reaches the shared store with
    the configured TTL (or twice it).  The cache's name is not pinned beyond containing "geo" (the plan names only the
    analytics one).

    Kills: the federal per-worker dict kept."""
    calls: list[dict] = []
    real = federal_db.get_federal_geo_drilldown

    def spy(conn, *args, **kwargs):
        calls.append(kwargs)
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(federal_db, "get_federal_geo_drilldown", spy)
    if hasattr(federal_routes, "get_federal_geo_drilldown"):
        monkeypatch.setattr(federal_routes, "get_federal_geo_drilldown", spy)
    federal_app.config.update(ROUTE_PERF_CACHE_ENABLED=True, FEDERAL_GEO_DRILLDOWN_CACHE_TTL_SECONDS=1234)
    client = federal_app.test_client()
    url = "/federal-finance/geo-drilldown?cycle=2026&geo_type=state&geo_value=IL"

    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200

    assert len(calls) == 1
    keys = [key for key in fake_redis.store if key.startswith("ilcf:cache:") and "geo" in key]
    assert len(keys) == 1, f"keys in the shared store: {sorted(fake_redis.store)}"
    assert fake_redis.ttls[keys[0]] in (1234, 2468)
