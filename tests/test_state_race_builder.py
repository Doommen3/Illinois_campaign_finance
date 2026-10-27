"""Tests-first suite for plan step S-RA: the state race builder.

Plan: .claude/plans/profiles-races-dated-widgets.md section 6.2 (S-RA) and the
decisions J-S3..J-S7 in section 6.3.

Pinned contract (see each test for the hand-computed numbers):

* 0.2   archived receipts never count toward race totals / contributions / donors;
        every other receipt part still counts (J-S2: transfers are race money).
* A4    (J-S3) a committee counts ONCE per race for the race's D-2 denominator and
        is split 1/n across the n candidates of that race it is linked to; a
        committee linked into two different races counts fully in each.
* A5    (J-S4) Part-9 outside spending is constrained to the resolved cycle's
        window (2025-01-01..2026-12-31 for cycle 2026) intersected with the page
        window; matched by candidate name only (the free-text office is ignored).
* A9    (J-S7) race donor identity = the site donor_key (Donor._bulk_donor_key_sql)
        with donor_entity_local_member merges applied.
* C1    (J-S5) race keys come from database.race_keys.race_key: normalized tokens,
        no digest; legacy "-<10 hex>" keys redirect to the canonical key.
* C2    (J-S6) the candidate set comes from the isbe_race_candidacies view; D-2
        money still comes from bulk_candidate_committee_finance_agg joined per
        (committee_id_sbe, election_cycle); the default cycle is the current
        cycle by date, passed to the builder as `today=`.

Fixture shape: tables below mirror the production compat views column-for-column
for the columns the builder can read (see COMPAT_VIEWS_DDL in
scripts/isbe_sunshine_etl.py and CANDIDATE_FINANCE_AGG_VIEW_SQL in
scripts/swap_bulk_to_isbe.py).  `is_archived` is INTEGER 0/1 there, so it is here.
The agg grain is (candidate, committee, period_year): one committee-candidate link
spans several rows per cycle, and a committee linked to n candidates repeats the
same committee-level D-2 total on every link.  The fixtures keep both properties.

Every test passes `election_cycle=` explicitly unless it is about the default
cycle, so nothing here depends on the wall clock.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from database.analytics import (
    _build_state_race_analytics_rows,
    _state_race_slug,
    get_state_race_analytics,
    get_state_race_detail,
)
from database.connection import get_db, init_db
from database.federal_fec import get_federal_race_analytics
from database.models import Donor
from webapp.app import create_app

CYCLE = 2026

# Race R: the race most tests use.
RACE_R = ("State Representative", "Representative District", "45")
RACE_R_LABEL = "State Representative - Representative District 45"
RACE_R_KEY = "state-representative-representative-district-45"

# Race S: a second race (multi-race committee, zero-money candidates).
RACE_S = ("State Senator", "Senate District", "12")
RACE_S_LABEL = "State Senator - Senate District 12"
RACE_S_KEY = "state-senator-senate-district-12"

DIGEST_SUFFIX = re.compile(r"-[0-9a-f]{10}$")
SLUG_SHAPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _create_race_tables(conn) -> None:
    """Create the four relations the builder reads, shaped like the views."""
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT,
            committee_type TEXT,
            committee_party_affiliation TEXT,
            period_year INTEGER,
            election_cycle INTEGER,
            filing_count INTEGER,
            sum_total_receipts DOUBLE PRECISION,
            sum_total_expenditures DOUBLE PRECISION,
            max_ending_funds_available DOUBLE PRECISION,
            archived_filing_count INTEGER,
            period_start_date TEXT,
            period_end_date TEXT
        )
        """
    )
    # No contributed_by column: the production compat view does not have one.
    conn.execute(
        """
        CREATE TABLE bulk_receipts_clean (
            bulk_row_id BIGINT,
            committee_id_sbe INTEGER,
            filed_doc_id INTEGER,
            last_or_business_name TEXT,
            first_name TEXT,
            received_date TEXT,
            amount DOUBLE PRECISION,
            occupation TEXT,
            employer TEXT,
            address_line_1 TEXT,
            address_line_2 TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            d2_part_code TEXT,
            is_archived INTEGER,
            received_datetime_raw TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE bulk_expenditures_clean (
            bulk_row_id BIGINT,
            committee_id_sbe INTEGER,
            filed_doc_id INTEGER,
            payee_last_or_business_name TEXT,
            expended_date TEXT,
            amount DOUBLE PRECISION,
            d2_part_code TEXT,
            candidate_name TEXT,
            office TEXT,
            is_archived INTEGER
        )
        """
    )
    # The candidacy feed: a TABLE here, a VIEW in production (J-S6).
    conn.execute(
        """
        CREATE TABLE isbe_race_candidacies (
            candidacy_id INTEGER,
            candidate_id INTEGER,
            election_year INTEGER,
            election_cycle INTEGER,
            election_type TEXT,
            race_type TEXT,
            outcome TEXT,
            candidate_full_name TEXT,
            office_sought TEXT,
            district_type TEXT,
            district TEXT,
            candidate_party_affiliation TEXT,
            committee_id_sbe INTEGER,
            committee_name TEXT
        )
        """
    )


def _add_agg(conn, candidate_id, name, race, committee_id, receipts, *, cycle=CYCLE, period_year=None) -> None:
    office, district_type, district = race
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg (
            candidate_id, candidate_full_name, office_sought, district_type, district,
            candidate_party_affiliation, committee_id_sbe, committee_name, committee_type,
            committee_party_affiliation, period_year, election_cycle, filing_count,
            sum_total_receipts, sum_total_expenditures, max_ending_funds_available,
            archived_filing_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id, name, office, district_type, district,
            "Independent", committee_id, f"Committee {committee_id}", "Candidate",
            "Independent", period_year or cycle, cycle, 1,
            receipts, 0.0, 0.0, 0,
        ),
    )


def _add_candidacy(
    conn, candidacy_id, candidate_id, name, race, committee_id=None, *,
    year=CYCLE, election_type="General Election",
) -> None:
    office, district_type, district = race
    committee_name = None if committee_id is None else f"Committee {committee_id}"
    conn.execute(
        """
        INSERT INTO isbe_race_candidacies (
            candidacy_id, candidate_id, election_year, election_cycle, election_type,
            race_type, outcome, candidate_full_name, office_sought, district_type,
            district, candidate_party_affiliation, committee_id_sbe, committee_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidacy_id, candidate_id, year, year + year % 2, election_type,
            "open seat", None, name, office, district_type,
            district, "Independent", committee_id, committee_name,
        ),
    )


def _add_receipt(
    conn, committee_id, first, last, amount, *, date="2026-03-01",
    address1="1 Main St", address2=None, city="Springfield", state="IL", zip5="62701",
    archived=0, part="1A",
) -> None:
    next_id = conn.execute(
        "SELECT COALESCE(MAX(bulk_row_id), 0) + 1 AS next_id FROM bulk_receipts_clean"
    ).fetchone()["next_id"]
    conn.execute(
        """
        INSERT INTO bulk_receipts_clean (
            bulk_row_id, committee_id_sbe, filed_doc_id, last_or_business_name, first_name,
            received_date, amount, occupation, employer, address_line_1, address_line_2,
            city, state, postal_code, d2_part_code, is_archived, received_datetime_raw
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            next_id, committee_id, 50000 + next_id, last, first,
            date, amount, None, None, address1, address2,
            city, state, zip5, part, archived, date,
        ),
    )


def _add_part9(
    conn, candidate_name, amount, expended_date, *, spender=301, part="9A", archived=0,
    office="House of Representatives 45",
) -> None:
    """One Part-9 (independent expenditure) row.  `office` is ISBE free text and
    deliberately does NOT match the candidate-office vocabulary (J-S4)."""
    next_id = conn.execute(
        "SELECT COALESCE(MAX(bulk_row_id), 0) + 1 AS next_id FROM bulk_expenditures_clean"
    ).fetchone()["next_id"]
    conn.execute(
        """
        INSERT INTO bulk_expenditures_clean (
            bulk_row_id, committee_id_sbe, filed_doc_id, payee_last_or_business_name,
            expended_date, amount, d2_part_code, candidate_name, office, is_archived
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            next_id, spender, 60000 + next_id, "Media Vendor",
            expended_date, amount, part, candidate_name, office, archived,
        ),
    )


def _seed_race_r_structure(conn) -> None:
    """Race R: candidates A=101 (Alice Alpha) and B=102 (Bob Beta).

    Committee X=201 is linked to BOTH A and B; committee Y=202 only to A.
    Each agg link spans two period rows, as in production (grain: candidate,
    committee, period_year); the committee-level D-2 total repeats on every link:
        X: 90 + 10 = 100 on the (A,X) link and again on the (B,X) link.
        Y: 50 + 50 = 100 on the (A,Y) link.
    Committee-level D-2 totals: X = 100, Y = 100.  The equal 50/50 split of Y is
    on purpose: a DISTINCT-on-value or MAX-per-row dedupe returns 50 for Y.
    Alice also has a second 2026 candidacy (General Primary) for X, as real
    candidates do ((candidate, election_type, year) is not unique upstream): X is
    shared by TWO candidates, not by three candidacy rows.
    """
    _add_candidacy(conn, 9101, 101, "Alice Alpha", RACE_R, 201)
    _add_candidacy(conn, 9104, 101, "Alice Alpha", RACE_R, 201, election_type="General Primary")
    _add_candidacy(conn, 9102, 101, "Alice Alpha", RACE_R, 202)
    _add_candidacy(conn, 9103, 102, "Bob Beta", RACE_R, 201)
    _add_agg(conn, 101, "Alice Alpha", RACE_R, 201, 90.0, period_year=2025)
    _add_agg(conn, 101, "Alice Alpha", RACE_R, 201, 10.0, period_year=2026)
    _add_agg(conn, 102, "Bob Beta", RACE_R, 201, 90.0, period_year=2025)
    _add_agg(conn, 102, "Bob Beta", RACE_R, 201, 10.0, period_year=2026)
    _add_agg(conn, 101, "Alice Alpha", RACE_R, 202, 50.0, period_year=2025)
    _add_agg(conn, 101, "Alice Alpha", RACE_R, 202, 50.0, period_year=2026)


def _seed_race_r_receipts(conn) -> None:
    """Receipts for race R (all 2026, itemized part 1A, not archived).

    X: Pat 300 + Quinn 100 -> 400.0, 2 contributions, 2 donors.
    Y: Rae 200             -> 200.0, 1 contribution, 1 donor.
    Race R: 600.0, 3 contributions, 3 donors.
    """
    _add_receipt(conn, 201, "Pat", "Payer", 300.0, date="2026-03-01")
    _add_receipt(conn, 201, "Quinn", "Quill", 100.0, date="2026-03-02")
    _add_receipt(conn, 202, "Rae", "Rowan", 200.0, date="2026-03-03")


def _seed_alice_part9(conn) -> None:
    """Part-9 rows naming Alice Alpha: 700 inside the 2026 cycle window and 300 before
    it, plus two in-window distractors that must never count: an ARCHIVED Part-9 row
    (5000) and a non-Part-9 row carrying her name (999, part 1A)."""
    _add_part9(conn, "ALICE ALPHA", 700.0, "2025-06-01")
    _add_part9(conn, "ALICE ALPHA", 300.0, "2021-05-01")
    _add_part9(conn, "ALICE ALPHA", 5000.0, "2025-07-01", archived=1)
    _add_part9(conn, "ALICE ALPHA", 999.0, "2025-08-01", part="1A")


def _seed_single_candidate_race(conn) -> None:
    """Race R with one candidate (A=101) and one committee (X=201); no receipts yet."""
    _add_candidacy(conn, 9101, 101, "Alice Alpha", RACE_R, 201)
    _add_agg(conn, 101, "Alice Alpha", RACE_R, 201, 100.0)


def _by_label(rows: list[dict]) -> dict[str, dict]:
    labels = [row["race_label"] for row in rows]
    assert len(labels) == len(set(labels)), f"duplicate race labels: {labels}"
    return {row["race_label"]: row for row in rows}


def _build(conn, **kwargs) -> list[dict]:
    kwargs.setdefault("election_cycle", CYCLE)
    return _build_state_race_analytics_rows(conn, **kwargs)


def _race_key_function():
    # Imported lazily: database/race_keys.py is created by the implementer, and a
    # module-level import would turn every test below into a collection error.
    from database.race_keys import race_key

    return race_key


@pytest.fixture
def race_db_path(tmp_path: Path) -> str:
    db_path = str(tmp_path / "state_race_builder.db")
    init_db(db_path)
    conn = get_db(db_path)
    try:
        _create_race_tables(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def race_conn(race_db_path):
    conn = get_db(race_db_path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 0.2  archived receipts
# ---------------------------------------------------------------------------

def test_archived_receipts_are_excluded_from_race_totals(race_conn):
    """Kills: no archived filter on the receipts scan (today), or a filter on only
    one of the two scans (the donor scan and the aggregate scan are separate)."""
    _seed_single_candidate_race(race_conn)
    # Both inside the window.  The archived row comes from a DIFFERENT donor so
    # that donor_count (second scan) also separates the implementations.
    _add_receipt(race_conn, 201, "Pat", "Payer", 1000.0, date="2026-03-01", archived=0)
    _add_receipt(race_conn, 201, "Quinn", "Quitter", 5000.0, date="2026-03-02", archived=1)

    rows = get_state_race_analytics(
        race_conn, limit=5000, date_from="2026-01-01", date_to="2026-12-31", election_cycle=CYCLE
    )

    race = _by_label(rows)[RACE_R_LABEL]
    assert race["total_amount"] == 1000.0
    assert race["contribution_count"] == 1
    assert race["donor_count"] == 1


def test_null_is_archived_counts_as_current_not_archived(race_conn):
    """House rule (CLAUDE.md, ISBE Archived flag): NULL means current.
    Kills `is_archived = 0` style filters, which silently drop NULL rows."""
    _seed_single_candidate_race(race_conn)
    _add_receipt(race_conn, 201, "Nia", "Nullflag", 300.0, archived=None)
    _add_receipt(race_conn, 201, "Quinn", "Quitter", 700.0, archived=1)

    race = _by_label(_build(race_conn))[RACE_R_LABEL]

    assert race["total_amount"] == 300.0
    assert race["contribution_count"] == 1
    assert race["donor_count"] == 1


def test_race_totals_keep_every_receipt_part_except_archived_rows(race_conn):
    """J-S2 (OPEN): race totals use "all" parts, because party transfers are race
    money.  Itemized contributions (1A), transfers in (2A), other receipts (4A) and
    in-kind (5A) all count; only archived rows drop out.
        1A 100 + 2A 250 + 4A 40 + 5A 10 = 400.0 over 4 contributions and 4 donors
        archived 2A 9000 is excluded (today: 9400 / 5 / 5)
    Kills reusing the donor-list predicate _BULK_DONOR_RECEIPT_FILTER_SQL (CLAUDE.md
    points at it for archived filtering): it keeps only '1%' parts and needs an
    active bulk_d2_totals_clean row, which this fixture does not even have."""
    _seed_single_candidate_race(race_conn)
    _add_receipt(race_conn, 201, "Pat", "Payer", 100.0, part="1A")
    _add_receipt(race_conn, 201, "", "Illinois Party Committee", 250.0, part="2A")
    _add_receipt(race_conn, 201, "Olive", "Other", 40.0, part="4A")
    _add_receipt(race_conn, 201, "Ian", "Inkind", 10.0, part="5A")
    _add_receipt(race_conn, 201, "", "Superseded Transfer Committee", 9000.0, part="2A", archived=1)

    race = _by_label(_build(race_conn))[RACE_R_LABEL]

    assert race["total_amount"] == 400.0
    assert race["contribution_count"] == 4
    assert race["donor_count"] == 4


# ---------------------------------------------------------------------------
# A4 / J-S3  once-per-race attribution
# ---------------------------------------------------------------------------

def test_shared_committee_counts_once_per_race_and_fully_in_each_race(race_conn):
    """Kills: today's per-link double count; SUM of the repeated D-2 total across
    links; MAX-per-row / DISTINCT-on-value dedupe (both lose a period row);
    no-split attribution; splitting across candidates of OTHER races; splitting a
    committee across the races it appears in.

    Race R (see _seed_race_r_structure): committee-level D-2 X=100 shared by A and
    B, Y=100 for A only.
        A = X/2 + Y = 50 + 100 = 150 ; B = X/2 = 50 ; denominator = X + Y = 200
        top_candidate_share = 150 / 200 = 0.75           (today: 200 / 300)
    Race S: committee X is ALSO linked to candidate D there; E has committee Z.
        X agg links repeat its 100 again on (D,X); Z = 100.
        D = X (only candidate of S linked to X) = 100 ; E = 100 ; denominator = 200
        top_candidate_share = 100 / 200 = 0.5
    Receipts are page-window only and count fully in each race X belongs to:
        R: X 400 + Y 200 = 600.0, 3 contributions, 3 donors (Pat, Quinn, Rae)
        S: X 400 + Z 150 = 550.0, 3 contributions, 3 donors (Pat, Quinn, Zoe)
    """
    _seed_race_r_structure(race_conn)
    _seed_race_r_receipts(race_conn)
    _add_candidacy(race_conn, 9201, 103, "Dana Delta", RACE_S, 201)
    _add_candidacy(race_conn, 9202, 104, "Eli Echo", RACE_S, 203)
    _add_agg(race_conn, 103, "Dana Delta", RACE_S, 201, 90.0, period_year=2025)
    _add_agg(race_conn, 103, "Dana Delta", RACE_S, 201, 10.0, period_year=2026)
    _add_agg(race_conn, 104, "Eli Echo", RACE_S, 203, 100.0)
    _add_receipt(race_conn, 203, "Zoe", "Zane", 150.0, date="2026-03-04")

    by_label = _by_label(_build(race_conn))

    assert set(by_label) == {RACE_R_LABEL, RACE_S_LABEL}
    race_r = by_label[RACE_R_LABEL]
    race_s = by_label[RACE_S_LABEL]

    assert race_r["top_candidate_share"] == 0.75
    assert race_r["candidate_count"] == 2
    assert race_r["total_amount"] == 600.0
    assert race_r["contribution_count"] == 3
    assert race_r["donor_count"] == 3

    assert race_s["top_candidate_share"] == 0.5
    assert race_s["candidate_count"] == 2
    assert race_s["total_amount"] == 550.0
    assert race_s["contribution_count"] == 3
    assert race_s["donor_count"] == 3


# ---------------------------------------------------------------------------
# A5 / J-S4  outside spending constrained to the cycle window
# ---------------------------------------------------------------------------

def _outside_by_label(conn, date_from, date_to) -> dict[str, float]:
    rows = _build(conn, date_from=date_from, date_to=date_to)
    return {label: row["outside_spending_total"] for label, row in _by_label(rows).items()}


def test_outside_spending_is_constrained_to_the_election_cycle_window(race_conn):
    """Part-9 rows naming Alice: 700 on 2025-06-01 (inside the cycle-2026 window
    2025-01-01..2026-12-31) and 300 on 2021-05-01 (before it).  Their `office` text
    ("House of Representatives 45") is not the candidate office vocabulary; the
    match is by name only.

    Kills: no cycle constraint (today: 1000); cycle window applied only when there
    is no page window; page window replacing the cycle window (2021 window -> 300);
    requiring the free-text office to match; a global (unscoped) outside total
    leaking into the other race.
    """
    _seed_race_r_structure(race_conn)
    # A second race whose candidate is not named in any Part-9 row.
    _add_candidacy(race_conn, 9301, 105, "Dana Delta", RACE_S, 203)
    _add_agg(race_conn, 105, "Dana Delta", RACE_S, 203, 100.0)
    _seed_alice_part9(race_conn)

    no_page_window = _outside_by_label(race_conn, None, None)
    in_cycle_page_window = _outside_by_label(race_conn, "2025-01-01", "2025-12-31")
    out_of_cycle_page_window = _outside_by_label(race_conn, "2021-01-01", "2021-12-31")

    assert no_page_window[RACE_R_LABEL] == 700.0
    assert in_cycle_page_window[RACE_R_LABEL] == 700.0
    assert out_of_cycle_page_window[RACE_R_LABEL] == 0.0
    assert no_page_window[RACE_S_LABEL] == 0.0
    assert in_cycle_page_window[RACE_S_LABEL] == 0.0
    assert out_of_cycle_page_window[RACE_S_LABEL] == 0.0


def test_outside_spending_cycle_window_is_inclusive_and_intersects_the_page_window(race_conn):
    """Cycle-2026 window = 2025-01-01..2026-12-31, both ends inclusive.
    Rows: 33 on 2024-12-31 (out), 11 on 2025-01-01 (in), 700 on 2025-06-01 (in),
    22 on 2026-12-31 (in), 44 on 2027-01-01 (out).
    In-cycle total = 11 + 700 + 22 = 733 ; out-of-cycle = 33 + 44 = 77 (today: 810).
    Page windows intersect with the cycle window:
        none, wide (2000..2030), exact cycle  -> 733
        2024-06-01..2025-01-01                -> 11   (33 is before the cycle)
        2026-12-31..2027-06-30                -> 22   (44 is after the cycle)
    Kills off-by-one windows, strict < / > comparisons, year-only comparisons and a
    page window that replaces (rather than narrows) the cycle window.
    """
    _seed_race_r_structure(race_conn)
    for amount, day in [
        (33.0, "2024-12-31"),
        (11.0, "2025-01-01"),
        (700.0, "2025-06-01"),
        (22.0, "2026-12-31"),
        (44.0, "2027-01-01"),
    ]:
        _add_part9(race_conn, "ALICE ALPHA", amount, day)

    windows_and_expected = [
        ((None, None), 733.0),
        (("2000-01-01", "2030-12-31"), 733.0),
        (("2025-01-01", "2026-12-31"), 733.0),
        (("2024-06-01", "2025-01-01"), 11.0),
        (("2026-12-31", "2027-06-30"), 22.0),
    ]
    for (date_from, date_to), expected in windows_and_expected:
        observed = _outside_by_label(race_conn, date_from, date_to)[RACE_R_LABEL]
        assert observed == expected, f"window {date_from}..{date_to}"


# ---------------------------------------------------------------------------
# A9 / J-S7  donor identity
# ---------------------------------------------------------------------------

def _donor_counts_for_race_r(conn) -> tuple[int, dict[str, int]]:
    """(race-level donor_count, {committee_id: committee-level donor_count}).

    The overview builds without supporting rows and the detail page with them; the
    race-level donor identity must not depend on the mode."""
    summary_rows = _build(conn, include_supporting_rows=False)
    detail_rows = _build(conn, include_supporting_rows=True)
    assert len(summary_rows) == 1
    assert len(detail_rows) == 1
    race = detail_rows[0]
    assert summary_rows[0]["donor_count"] == race["donor_count"], "summary and detail modes disagree"
    per_committee = {
        str(row["committee_id_sbe"]): row["donor_count"] for row in race["committee_receipt_rows"]
    }
    return race["donor_count"], per_committee


def _site_donor_keys(conn) -> set[str]:
    """Distinct donor_keys of the receipts, computed with the site's own formula."""
    key_sql = Donor._bulk_donor_key_sql("r")
    rows = conn.execute(f"SELECT DISTINCT {key_sql} AS donor_key FROM bulk_receipts_clean r").fetchall()
    return {row["donor_key"] for row in rows}


def _merge_donor_keys(conn, entity_id: str, canonical_name: str, members: list[tuple[str, str, str, str]]) -> None:
    """Insert one donor entity and its members in the shape the donor pages read:
    source 'bulk_receipts', merge_action 'auto_merge', review_status 'not_needed'.
    members: [(donor_key, donor_name, CITY, zip5)]."""
    conn.execute(
        """
        INSERT INTO donor_entity_local (
            entity_id, source, canonical_name, display_name, member_count, total_amount,
            confidence_score, peak_confidence_score, confidence_tier, merge_action, method_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entity_id, "bulk_receipts", canonical_name, members[0][1], len(members), 0.0, 0.99, 0.99,
         "high", "auto_merge", "test:v1"),
    )
    for donor_key, donor_name, city, zip5 in members:
        conn.execute(
            """
            INSERT INTO donor_entity_local_member (
                source, donor_key, entity_id, canonical_name, donor_name, donor_city, donor_state,
                donor_zip5, confidence_score, confidence_tier, merge_action, total_amount,
                contribution_count, committee_count, review_status, reasons_json, method_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bulk_receipts", donor_key, entity_id, canonical_name, donor_name, city, "IL",
                zip5, 0.99, "high", "auto_merge", 0.0, 1, 1, "not_needed", "{}", "test:v1",
            ),
        )


def test_race_donor_count_distinguishes_namesakes_then_applies_a_merge(race_conn):
    """Site donor_key = LOWER(TRIM(first|last|addr1|addr2|city|state|zip)).
    Jane Doe at 1 Main St (twice) is one donor; adding a Jane Doe at 9 Oak St makes
    two; merging those two keys into one entity makes one again.  Kills the
    name-only key (today: step 2 sees 1), at BOTH the race level and the
    per-committee level, and a lookup that never consults the member table.
        jane1 = jane|doe|1 main st||springfield|il|62701
        jane2 = jane|doe|9 oak st||chicago|il|60601
    """
    _seed_single_candidate_race(race_conn)
    _add_receipt(race_conn, 201, "Jane", "Doe", 100.0, date="2026-03-01")
    _add_receipt(race_conn, 201, "Jane", "Doe", 50.0, date="2026-03-02")

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)
    assert race_donors == 1
    assert per_committee == {"201": 1}

    _add_receipt(
        race_conn, 201, "Jane", "Doe", 25.0, date="2026-03-03",
        address1="9 Oak St", city="Chicago", zip5="60601",
    )
    jane1 = "jane|doe|1 main st||springfield|il|62701"
    jane2 = "jane|doe|9 oak st||chicago|il|60601"
    assert _site_donor_keys(race_conn) == {jane1, jane2}, "site donor_key formula drifted; update the literals"

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)
    assert race_donors == 2
    assert per_committee == {"201": 2}

    _merge_donor_keys(
        race_conn, "entity:test:jane-doe-namesakes", "jane doe",
        [(jane1, "Jane Doe", "SPRINGFIELD", "62701"), (jane2, "Jane Doe", "CHICAGO", "60601")],
    )

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)
    assert race_donors == 1
    assert per_committee == {"201": 1}


def test_race_donor_key_is_case_insensitive_like_the_site_donor_key(race_conn):
    """LOWER() is part of the site formula, so "JANE DOE, 1 MAIN ST ..." and
    "Jane Doe, 1 Main St ..." are the same donor (and the same /donors/key/ URL).
    Kills a key built without LOWER (today's name key is case-sensitive: 2)."""
    _seed_single_candidate_race(race_conn)
    _add_receipt(race_conn, 201, "Jane", "Doe", 100.0)
    _add_receipt(
        race_conn, 201, "JANE", "DOE", 50.0,
        address1="1 MAIN ST", city="SPRINGFIELD", state="IL", zip5="62701",
    )

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)

    assert race_donors == 1
    assert per_committee == {"201": 1}


def test_race_donor_count_applies_local_entity_merges_across_committees(race_conn):
    """Merged donor_keys count as ONE donor, at the race level and per committee.
    The merged members here are spelled differently ("Jane Doe" / "Jane Q Doe") so
    that today's name-based key also sees two donors and this merge step is RED for
    its own reason.

    Receipts (race R: X=201 and Y=202 are both committees of the race):
        X: Jane Doe @1 Main St 100 ; Jane Q Doe @9 Oak St 50 ; John Roe @5 Elm St 25
        Y: John Roe @5 Elm St 10   ; Jane Doe @1 Main St 5
    Site donor_keys (literals, checked against Donor._bulk_donor_key_sql below):
        jane1 = jane|doe|1 main st||springfield|il|62701
        janeq = jane q|doe|9 oak st||chicago|il|60601
        john  = john|roe|5 elm st||peoria|il|61602
    Unmerged: race {jane1, janeq, john} = 3 ; X = 3 ; Y = {john, jane1} = 2.
    After merging jane1 + janeq into one entity E:
        race {E, john} = 2 ; X {E, john} = 2 ; Y {john, E} = 2.
    Money is untouched by merging: race total = X 175 + Y 15 = 190.0 over 5
    contributions, before and after.
    Kills: ignoring merges (today); merging only at the race level; summing the
    per-committee counts for the race (2 + 2 = 4); counting only entity members
    (drops `john`, race = 1); collapsing everything once any member row exists; a
    join on entity_id that fans each receipt out once per member (totals double).
    """
    _seed_race_r_structure(race_conn)
    _add_receipt(race_conn, 201, "Jane", "Doe", 100.0)
    _add_receipt(
        race_conn, 201, "Jane Q", "Doe", 50.0,
        address1="9 Oak St", city="Chicago", zip5="60601",
    )
    _add_receipt(
        race_conn, 201, "John", "Roe", 25.0,
        address1="5 Elm St", city="Peoria", zip5="61602",
    )
    _add_receipt(
        race_conn, 202, "John", "Roe", 10.0,
        address1="5 Elm St", city="Peoria", zip5="61602",
    )
    _add_receipt(race_conn, 202, "Jane", "Doe", 5.0)

    jane1 = "jane|doe|1 main st||springfield|il|62701"
    janeq = "jane q|doe|9 oak st||chicago|il|60601"
    john = "john|roe|5 elm st||peoria|il|61602"
    assert _site_donor_keys(race_conn) == {jane1, janeq, john}, "site donor_key formula drifted; update the literals"

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)
    assert race_donors == 3
    assert per_committee == {"201": 3, "202": 2}
    unmerged = _build(race_conn)[0]
    assert unmerged["total_amount"] == 190.0
    assert unmerged["contribution_count"] == 5

    _merge_donor_keys(
        race_conn, "entity:test:jane-doe", "jane doe",
        [(jane1, "Jane Doe", "SPRINGFIELD", "62701"), (janeq, "Jane Q Doe", "CHICAGO", "60601")],
    )

    race_donors, per_committee = _donor_counts_for_race_r(race_conn)
    assert race_donors == 2
    assert per_committee == {"201": 2, "202": 2}
    merged = _build(race_conn)[0]
    assert merged["total_amount"] == 190.0
    assert merged["contribution_count"] == 5


# ---------------------------------------------------------------------------
# C1 / J-S5  race keys
# ---------------------------------------------------------------------------

def test_race_key_builds_the_pinned_slug_from_normalized_tokens():
    race_key = _race_key_function()

    assert race_key(*RACE_R) == RACE_R_KEY
    assert race_key(*RACE_S) == RACE_S_KEY
    assert race_key("Governor", "Statewide", "At-Large") == "governor-statewide-at-large"
    # Case, runs of whitespace and padding do not change the key.
    assert race_key("STATE  REPRESENTATIVE", "representative district", "45") == RACE_R_KEY
    assert race_key("  State\tRepresentative ", "Representative   District", " 45 ") == RACE_R_KEY


def test_race_key_skips_empty_parts_and_never_emits_stray_hyphens():
    race_key = _race_key_function()

    assert race_key("Governor", "Statewide", None) == "governor-statewide"
    assert race_key("Governor", "Statewide", "") == "governor-statewide"
    assert race_key("Governor", None, "At-Large") == "governor-at-large"
    assert race_key(None, "Statewide", "At-Large") == "statewide-at-large"
    assert race_key("Governor", "   ", "At-Large") == "governor-at-large"

    awkward_inputs = [
        ("Judge - Circuit Court (Cook)", "Subcircuit", "07"),
        ("Governor / Lieutenant Governor", "Statewide", ""),
        ("State's Attorney", "County", "Cook"),
        ("  Comptroller  ", "Statewide", "At-Large "),
    ]
    for parts in awkward_inputs:
        key = race_key(*parts)
        assert SLUG_SHAPE.fullmatch(key), f"malformed slug {key!r} for {parts}"


def test_race_key_never_ends_in_a_digest_suffix():
    race_key = _race_key_function()

    tuples = [RACE_R, RACE_S, ("Governor", "Statewide", "At-Large"), ("Comptroller", "Statewide", "")]
    for parts in tuples:
        key = race_key(*parts)
        assert not DIGEST_SUFFIX.search(key), f"digest-like suffix on {key!r}"


def test_state_race_slug_is_exactly_the_shared_race_key():
    """Today the slug carries a sha1[:10] digest and a 'na' placeholder."""
    assert _state_race_slug(*RACE_R) == RACE_R_KEY
    assert _state_race_slug("Governor", "Statewide", "") == "governor-statewide"
    assert _state_race_slug(*RACE_R) == _race_key_function()(*RACE_R)


def test_state_race_rows_carry_unique_digest_free_race_keys(race_conn):
    _seed_race_r_structure(race_conn)
    _add_candidacy(race_conn, 9301, 105, "Dana Delta", RACE_S, 203)
    _add_agg(race_conn, 105, "Dana Delta", RACE_S, 203, 100.0)

    rows = _build(race_conn)

    keys = [row["race_key"] for row in rows]
    assert sorted(keys) == sorted([RACE_R_KEY, RACE_S_KEY])
    for key in keys:
        assert not DIGEST_SUFFIX.search(key)


def test_case_variants_of_one_race_merge_into_a_single_row_and_a_single_key(race_conn):
    """EXTENSION (suggestion C1: "case variants collide"): the key IS the race
    identity.  Two spellings of the same office/district normalize to one key, so
    the builder must produce one race row for them, and the detail lookup by that
    key must see the candidates of both spellings.  Kills the lazy change that swaps
    the slug body for race_key() but keeps grouping by the raw strings (two rows,
    one key; detail resolves to whichever spelling comes first)."""
    spellings = [
        (601, "Pat Primary", ("State Representative", "Representative District", "45"), 8601),
        (602, "Quin Quote", ("STATE  REPRESENTATIVE", "representative district", "45"), 8602),
    ]
    for candidate_id, name, race, committee_id in spellings:
        _add_candidacy(race_conn, 9000 + candidate_id, candidate_id, name, race, committee_id)
        _add_agg(race_conn, candidate_id, name, race, committee_id, 100.0)

    rows = _build(race_conn)

    assert [row["race_key"] for row in rows] == [RACE_R_KEY]
    assert rows[0]["candidate_count"] == 2
    detail = get_state_race_detail(race_conn, race_key=RACE_R_KEY, election_cycle=CYCLE)
    assert detail is not None
    names = {row["candidate_name"] for row in detail["candidate_rows"]}
    assert names == {"Pat Primary", "Quin Quote"}


def _seed_federal_races(conn) -> None:
    conn.executemany(
        """
        INSERT INTO fec_il_candidate_seed (
            candidate_key, as_of_date, cycle, office, office_code, district, district_code,
            party, party_code, election_stage, candidate_name, normalized_candidate_name,
            write_in, already_listed_general, source_file, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("seed-h07", "2026-02-07", 2026, "U.S. House", "H", "IL-07", "07",
             "Democratic", "DEM", "Primary", "Hannah House", "hannah house", 0, 0, "test.csv", 1),
            ("seed-sen", "2026-02-07", 2026, "U.S. Senate", "S", "Statewide", "",
             "Republican", "REP", "Primary", "Sam Senate", "sam senate", 0, 0, "test.csv", 2),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fec_candidate_match (
            seed_candidate_key, candidate_name, office, office_code, district, district_code,
            party, party_code, election_stage, cycle, fec_candidate_id, fec_name,
            fec_office, fec_state, fec_district, fec_party,
            match_status, match_score, match_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("seed-h07", "Hannah House", "U.S. House", "H", "IL-07", "07",
             "Democratic", "DEM", "Primary", 2026, "H2IL07001", "HOUSE, HANNAH",
             "H", "IL", "07", "DEM", "matched", 98.0, "test"),
            ("seed-sen", "Sam Senate", "U.S. Senate", "S", "Statewide", "",
             "Republican", "REP", "Primary", 2026, "S2IL00001", "SENATE, SAM",
             "S", "IL", "00", "REP", "matched", 98.0, "test"),
        ],
    )
    conn.commit()


def test_federal_race_rows_use_the_shared_race_key_helper(race_conn):
    """The federal builder is database.federal_fec.get_federal_race_analytics
    (database/analytics.py:3027, named in the plan, is the STATE slug resolver).
    Federal rows have `race_id` ("H:07"), `race_office` ("U.S. House"),
    `race_district` ("IL-07"), `office_code`, `district_code` and NO `race_key`.

    OPEN CONTRACT POINT: which parts feed the key is not pinned by the plan.  This
    test accepts either the display labels or the codes and pins everything else:
    the field exists, equals the shared helper's output for one of the two part
    sets, has no digest, and is unique per race.  Tighten the accepted set to one
    choice once it is decided."""
    _seed_federal_races(race_conn)

    rows = get_federal_race_analytics(race_conn, cycle=2026)

    assert len(rows) == 2
    for row in rows:
        assert "race_key" in row, f"federal race row has no race_key: {sorted(row)}"

    race_key = _race_key_function()
    for row in rows:
        by_labels = race_key(row["race_office"], row["race_district"])
        by_codes = race_key(row["office_code"], row["district_code"])
        assert row["race_key"] in {by_labels, by_codes}
        assert not DIGEST_SUFFIX.search(row["race_key"])
    assert len({row["race_key"] for row in rows}) == 2


# ---------------------------------------------------------------------------
# C1 / J-S5  route: legacy digest keys redirect, canonical keys render
# ---------------------------------------------------------------------------

def _race_client(race_conn, race_db_path):
    _seed_race_r_structure(race_conn)
    _seed_race_r_receipts(race_conn)
    race_conn.commit()
    app = create_app(
        {"TESTING": True, "DATABASE_PATH": race_db_path, "DASHBOARD_PREWARM_ENABLED": False}
    )
    return app.test_client()


def _location_parts(response) -> tuple[str, dict[str, list[str]]]:
    parsed = urlparse(response.headers["Location"])
    return parsed.path, parse_qs(parsed.query)


def test_legacy_digest_race_key_redirects_to_the_canonical_key_keeping_the_query(race_conn, race_db_path):
    """Kills: no redirect (today: 404); dropping the query string; keeping only a
    whitelist of parameters; redirecting to a recomputed/wrong slug."""
    client = _race_client(race_conn, race_db_path)
    legacy = f"/analytics/state-races/{RACE_R_KEY}-0123456789"

    plain = client.get(f"{legacy}?period=all")
    assert plain.status_code in (301, 302)
    path, query = _location_parts(plain)
    assert path == f"/analytics/state-races/{RACE_R_KEY}"
    assert query == {"period": ["all"]}

    two_params = client.get(f"{legacy}?period=all&election_cycle=2026")
    assert two_params.status_code in (301, 302)
    path, query = _location_parts(two_params)
    assert path == f"/analytics/state-races/{RACE_R_KEY}"
    assert query == {"period": ["all"], "election_cycle": ["2026"]}


def test_canonical_race_key_route_renders_and_a_legacy_key_lands_there(race_conn, race_db_path):
    """Kills: redirecting canonical keys too (loop / 3xx instead of 200) and a
    redirect target that does not resolve."""
    client = _race_client(race_conn, race_db_path)
    canonical = f"/analytics/state-races/{RACE_R_KEY}?period=all&election_cycle=2026"

    direct = client.get(canonical)
    assert direct.status_code == 200
    assert RACE_R_LABEL.encode() in direct.data

    followed = client.get(
        f"/analytics/state-races/{RACE_R_KEY}-0123456789?period=all&election_cycle=2026",
        follow_redirects=True,
    )
    assert followed.status_code == 200
    assert RACE_R_LABEL.encode() in followed.data


# ---------------------------------------------------------------------------
# C2 / J-S6  the candidacy feed
# ---------------------------------------------------------------------------

def test_candidacy_without_receipts_or_agg_row_appears_with_zero_money(race_conn):
    """Race S has two 2026 candidacies and no money anywhere: Quinn's committee
    (701) has no receipts and no agg row; Nora has no committee at all
    (committee_id_sbe NULL).  Kills: building the candidate set from the agg view
    (today: no race), an INNER JOIN to committees (drops Nora), division by zero on
    an all-zero race, and a detail lookup that still resolves keys from the agg view."""
    _add_candidacy(race_conn, 9401, 301, "Quinn Quiet", RACE_S, 701)
    _add_candidacy(race_conn, 9402, 302, "Nora Nocommittee", RACE_S, None)

    rows = _build(race_conn, include_supporting_rows=True)

    assert [row["race_label"] for row in rows] == [RACE_S_LABEL]
    race = rows[0]
    assert race["candidate_count"] == 2
    assert race["total_amount"] == 0.0
    assert race["contribution_count"] == 0
    assert race["donor_count"] == 0
    assert race["outside_spending_total"] == 0.0
    assert race["top_candidate_share"] == 0.0

    detail = get_state_race_detail(race_conn, race_key=RACE_S_KEY, election_cycle=CYCLE)
    assert detail is not None
    # Whether a committee-less candidate also gets a (blank-committee) detail row is
    # not pinned; Quinn must be listed and nobody else may appear.
    names = {row["candidate_name"] for row in detail["candidate_rows"]}
    assert "Quinn Quiet" in names
    assert names <= {"Quinn Quiet", "Nora Nocommittee"}


def test_agg_row_without_a_candidacy_in_the_cycle_makes_no_race(race_conn):
    """Ghost Gina has a cycle-2026 agg row (and receipts) but her only candidacy is
    from 2024: no 2026 race.  Control Carl has both and must appear.
    Kills: any builder that still derives races from the agg view or from receipts."""
    control_race = ("State Treasurer", "Statewide", "At-Large")
    ghost_race = ("Comptroller", "Statewide", "At-Large")
    _add_candidacy(race_conn, 9501, 401, "Control Carl", control_race, 701)
    _add_agg(race_conn, 401, "Control Carl", control_race, 701, 200.0)
    _add_candidacy(race_conn, 9502, 402, "Ghost Gina", ghost_race, 702, year=2024)
    _add_agg(race_conn, 402, "Ghost Gina", ghost_race, 702, 500.0)
    _add_receipt(race_conn, 702, "Gil", "Giver", 900.0)

    rows = _build(race_conn)

    assert {row["race_label"] for row in rows} == {"State Treasurer - Statewide At-Large"}
    assert len(rows) == 1


def test_d2_money_joins_agg_per_committee_and_cycle_without_candidacy_fan_out(race_conn):
    """Race R, cycle 2026.  Dana Dee (501, committee 801) has TWO 2026 candidacies
    (General Primary + General Election; (candidate, election_type, year) is not
    unique upstream) and a 2024 one.  Rex Roe (502, committee 802).  Zed Zee (503,
    committee 803) has a 2026 candidacy but his only agg row is for cycle 2024.
    agg: (Dana,801) 2026 = 300 and 2024 = 9000 ; (Rex,802) 2026 = 100 ;
         (Zed,803) 2024 = 7000.
    Cycle 2026: Dana 300 (once, not 600), Rex 100, Zed 0 -> share 300/400 = 0.75,
    3 candidates, exactly 3 candidate rows.  Cycle 2024: Dana only, 9000.
    Kills: a join per candidacy row (Dana doubled), a join by committee only
    (9300 / 7000 leak across cycles), duplicate candidate rows, and dropping
    candidacy-only candidates (today: 2 candidates)."""
    _add_candidacy(race_conn, 9600, 501, "Dana Dee", RACE_R, 801, year=2024)
    _add_candidacy(race_conn, 9601, 501, "Dana Dee", RACE_R, 801, election_type="General Primary")
    _add_candidacy(race_conn, 9602, 501, "Dana Dee", RACE_R, 801)
    _add_candidacy(race_conn, 9603, 502, "Rex Roe", RACE_R, 802)
    _add_candidacy(race_conn, 9604, 503, "Zed Zee", RACE_R, 803)
    _add_agg(race_conn, 501, "Dana Dee", RACE_R, 801, 300.0, cycle=2026)
    _add_agg(race_conn, 501, "Dana Dee", RACE_R, 801, 9000.0, cycle=2024)
    _add_agg(race_conn, 502, "Rex Roe", RACE_R, 802, 100.0, cycle=2026)
    _add_agg(race_conn, 503, "Zed Zee", RACE_R, 803, 7000.0, cycle=2024)

    cycle_2026 = _by_label(_build(race_conn, include_supporting_rows=True, election_cycle=2026))[RACE_R_LABEL]

    assert cycle_2026["candidate_count"] == 3
    candidate_rows = cycle_2026["candidate_rows"]
    assert len(candidate_rows) == 3
    receipts_by_candidate = {row["candidate_name"]: row["candidate_receipts_total"] for row in candidate_rows}
    assert receipts_by_candidate == {"Dana Dee": 300.0, "Rex Roe": 100.0, "Zed Zee": 0.0}
    assert cycle_2026["top_candidate_share"] == 0.75

    cycle_2024 = _by_label(_build(race_conn, include_supporting_rows=True, election_cycle=2024))[RACE_R_LABEL]

    assert cycle_2024["candidate_count"] == 1
    assert len(cycle_2024["candidate_rows"]) == 1
    assert cycle_2024["candidate_rows"][0]["candidate_receipts_total"] == 9000.0
    assert cycle_2024["top_candidate_share"] == 1.0


LABEL_2025 = "Auditor - Statewide Odd-Year"
LABEL_2026 = "State Senator - Senate District 3"
LABEL_2027 = "Treasurer - Statewide Odd-Year"
LABEL_2028 = "State Senator - Senate District 5"


def _seed_two_cycles(conn) -> None:
    """Cycle 2026 = election years 2025 and 2026 ; cycle 2028 = years 2027 and 2028.
    Every candidate has a committee with no money (money is not the subject)."""
    _add_candidacy(conn, 9701, 601, "Odd Twentyfive", ("Auditor", "Statewide", "Odd-Year"), 951, year=2025)
    _add_candidacy(conn, 9702, 602, "Even Twentysix", ("State Senator", "Senate District", "3"), 952, year=2026)
    _add_candidacy(conn, 9703, 603, "Odd Twentyseven", ("Treasurer", "Statewide", "Odd-Year"), 953, year=2027)
    _add_candidacy(conn, 9704, 604, "Even Twentyeight", ("State Senator", "Senate District", "5"), 954, year=2028)


CYCLE_2026_LABELS = {LABEL_2025, LABEL_2026}
CYCLE_2028_LABELS = {LABEL_2027, LABEL_2028}


@pytest.mark.parametrize(
    "today, expected_labels",
    [
        pytest.param(dt.date(2025, 6, 1), CYCLE_2026_LABELS, id="2025-06-01-odd-year-folds-forward"),
        pytest.param(dt.date(2026, 10, 26), CYCLE_2026_LABELS, id="2026-10-26"),
        pytest.param(dt.date(2026, 12, 31), CYCLE_2026_LABELS, id="2026-12-31-last-day"),
        pytest.param(dt.date(2027, 1, 1), CYCLE_2028_LABELS, id="2027-01-01-first-day-of-next"),
        pytest.param(dt.date(2027, 3, 1), CYCLE_2028_LABELS, id="2027-03-01"),
    ],
)
def test_default_cycle_is_the_current_cycle_by_date_not_the_max_cycle(race_conn, today, expected_labels):
    """cycle = year + year % 2 of `today`.  Candidacies exist in BOTH cycles, so a
    MAX(election_cycle) default always answers 2028, a wall-clock default ignores
    `today=`, and an election_year (not election_cycle) filter loses the odd-year
    candidacies (2025 and 2027)."""
    _seed_two_cycles(race_conn)

    rows = _build_state_race_analytics_rows(race_conn, election_cycle=None, today=today)

    assert {row["race_label"] for row in rows} == expected_labels
    assert len(rows) == 2


@pytest.mark.parametrize(
    "explicit_cycle, today, expected_labels",
    [
        pytest.param(2028, dt.date(2026, 10, 26), CYCLE_2028_LABELS, id="explicit-2028-beats-today-2026"),
        pytest.param(2026, dt.date(2027, 3, 1), CYCLE_2026_LABELS, id="explicit-2026-beats-today-2027"),
    ],
)
def test_explicit_election_cycle_beats_today(race_conn, explicit_cycle, today, expected_labels):
    _seed_two_cycles(race_conn)

    rows = _build_state_race_analytics_rows(race_conn, election_cycle=explicit_cycle, today=today)

    assert {row["race_label"] for row in rows} == expected_labels
    assert len(rows) == 2


def test_public_functions_default_to_the_current_cycle_by_the_real_clock(race_conn):
    """The routes call get_state_race_analytics / get_state_race_detail with no
    election_cycle and no `today`.  The seed is derived from the wall clock at run
    time (current cycle and the one after it, each with candidacy AND agg money),
    so this test is date-independent.  Kills MAX(election_cycle) defaults (today),
    over either relation, and a detail lookup whose default differs from the list's."""
    today = dt.date.today()
    current = today.year + today.year % 2
    now_race = ("State Senator", "Senate District", "3")
    later_race = ("State Senator", "Senate District", "5")
    _add_candidacy(race_conn, 9801, 701, "Now Nancy", now_race, 961, year=current)
    _add_candidacy(race_conn, 9802, 702, "Later Larry", later_race, 962, year=current + 2)
    _add_agg(race_conn, 701, "Now Nancy", now_race, 961, 100.0, cycle=current)
    _add_agg(race_conn, 702, "Later Larry", later_race, 962, 100.0, cycle=current + 2)

    rows = get_state_race_analytics(race_conn, limit=5000)

    assert [row["race_label"] for row in rows] == ["State Senator - Senate District 3"]
    assert get_state_race_detail(race_conn, race_key="state-senator-senate-district-3") is not None
    assert get_state_race_detail(race_conn, race_key="state-senator-senate-district-5") is None


def test_builder_returns_nothing_when_the_candidacy_view_is_missing(race_conn):
    """Same contract as a missing agg/receipts/expenditures relation: []."""
    _add_agg(race_conn, 101, "Alice Alpha", RACE_R, 201, 100.0)
    _add_receipt(race_conn, 201, "Pat", "Payer", 100.0)
    race_conn.execute("DROP TABLE isbe_race_candidacies")

    assert _build(race_conn) == []
    assert get_state_race_analytics(race_conn, limit=5000, election_cycle=CYCLE) == []


# ---------------------------------------------------------------------------
# C2 / J-S6  the view DDL (RACE_CANDIDACIES_VIEW_SQL in scripts/swap_bulk_to_isbe.py)
# ---------------------------------------------------------------------------

def test_race_candidacies_view_ddl_has_the_pinned_columns_cycles_and_row_shape(race_conn):
    """Executes the production DDL string against minimal ISBE source tables.

    Contract: scripts/swap_bulk_to_isbe.py exposes the statement as
    RACE_CANDIDACIES_VIEW_SQL, ONE statement ("CREATE VIEW isbe_race_candidacies
    AS ...", like CANDIDATE_FINANCE_AGG_VIEW_SQL) that main() executes.  Enum
    columns are TEXT in these source tables, so enum typing is not exercised.

    Source rows: candidate 301 has two committees (701, 702) and three candidacies
    (9001 General Primary 2026, 9002 General Election 2026, 9003 Consolidated 2025);
    candidate 302 has a 2027 candidacy (9004) and NO committee link.
    Expected view rows = candidacy x committee link, 3*2 + 1 = 7, with
    election_cycle = year + year % 2 (2025 -> 2026, 2027 -> 2028) and the committee
    columns NULL for the unlinked candidacy.  Kills: an INNER JOIN (drops 9004),
    a wrong cycle formula, a candidate-level (not candidacy-level) grain, extra or
    missing columns."""
    from scripts.swap_bulk_to_isbe import RACE_CANDIDACIES_VIEW_SQL

    race_conn.execute("DROP TABLE isbe_race_candidacies")
    race_conn.execute(
        """
        CREATE TABLE isbe_committees (
            id INTEGER, type TEXT, state_committee BOOLEAN, local_committee BOOLEAN, refer_name TEXT,
            name TEXT, address1 TEXT, address2 TEXT, address3 TEXT, city TEXT, state TEXT, zipcode TEXT,
            active BOOLEAN, status_date DATE, creation_date DATE, creation_amount DOUBLE PRECISION,
            disp_funds_return TEXT, disp_funds_political_committee TEXT, disp_funds_charity TEXT,
            disp_funds_95 TEXT, candidate_position TEXT, policy_position TEXT, party TEXT, purpose TEXT
        )
        """
    )
    race_conn.execute(
        """
        CREATE TABLE isbe_candidates (
            id INTEGER, last_name TEXT, first_name TEXT, address1 TEXT, address2 TEXT, city TEXT,
            state TEXT, zipcode TEXT, office TEXT, district_type TEXT, district TEXT,
            residence_county TEXT, party TEXT, redaction_requested BOOLEAN
        )
        """
    )
    race_conn.execute(
        "CREATE TABLE isbe_candidate_committees (id INTEGER, committee_id INTEGER, candidate_id INTEGER)"
    )
    race_conn.execute(
        """
        CREATE TABLE isbe_candidacies (
            id INTEGER, candidate_id INTEGER, election_type TEXT, election_year INTEGER,
            race_type TEXT, outcome TEXT, fair_campaign BOOLEAN, limits_off BOOLEAN,
            limits_off_reason TEXT
        )
        """
    )
    race_conn.execute(
        "INSERT INTO isbe_committees (id, name, type, party) VALUES (701, 'Quinn for Senate', 'Candidate', 'D'), "
        "(702, 'Slate Committee', 'Candidate', 'D')"
    )
    race_conn.execute(
        """
        INSERT INTO isbe_candidates (id, last_name, first_name, office, district_type, district, party)
        VALUES (301, 'Quiet', 'Quinn', 'State Senator', 'Senate District', '12', 'Democratic'),
               (302, 'Nocommittee', 'Nora', 'State Senator', 'Senate District', '12', 'Republican')
        """
    )
    race_conn.execute(
        "INSERT INTO isbe_candidate_committees (id, committee_id, candidate_id) VALUES (1, 701, 301), (2, 702, 301)"
    )
    race_conn.execute(
        """
        INSERT INTO isbe_candidacies (id, candidate_id, election_type, election_year, race_type, outcome)
        VALUES (9001, 301, 'General Primary', 2026, 'challenger', 'won'),
               (9002, 301, 'General Election', 2026, 'challenger', NULL),
               (9003, 301, 'Consolidated Election', 2025, 'incumbent', 'won'),
               (9004, 302, 'General Election', 2027, 'open seat', NULL)
        """
    )

    race_conn.execute(RACE_CANDIDACIES_VIEW_SQL)

    expected_columns = {
        "candidacy_id", "candidate_id", "election_year", "election_cycle", "election_type",
        "race_type", "outcome", "candidate_full_name", "office_sought", "district_type",
        "district", "candidate_party_affiliation", "committee_id_sbe", "committee_name",
    }
    first_row = race_conn.execute("SELECT * FROM isbe_race_candidacies LIMIT 1").fetchone()
    assert set(first_row.keys()) == expected_columns
    assert len(first_row.keys()) == 14

    shape = [
        (row["candidacy_id"], row["committee_id_sbe"], row["election_cycle"])
        for row in race_conn.execute(
            "SELECT candidacy_id, committee_id_sbe, election_cycle FROM isbe_race_candidacies "
            "ORDER BY candidacy_id, committee_id_sbe"
        ).fetchall()
    ]
    assert shape == [
        (9001, 701, 2026), (9001, 702, 2026),
        (9002, 701, 2026), (9002, 702, 2026),
        (9003, 701, 2026), (9003, 702, 2026),
        (9004, None, 2028),
    ]

    full = race_conn.execute(
        "SELECT * FROM isbe_race_candidacies WHERE candidacy_id = 9001 AND committee_id_sbe = 701"
    ).fetchone()
    assert full["candidate_id"] == 301
    assert full["election_year"] == 2026
    assert full["election_type"] == "General Primary"
    assert full["race_type"] == "challenger"
    assert full["outcome"] == "won"
    assert full["candidate_full_name"] == "Quinn Quiet"
    assert full["office_sought"] == "State Senator"
    assert full["district_type"] == "Senate District"
    assert full["district"] == "12"
    assert full["candidate_party_affiliation"] == "Democratic"
    assert full["committee_name"] == "Quinn for Senate"

    unlinked = race_conn.execute("SELECT * FROM isbe_race_candidacies WHERE candidacy_id = 9004").fetchone()
    assert unlinked["committee_id_sbe"] is None
    assert unlinked["committee_name"] is None
    assert unlinked["candidate_full_name"] == "Nora Nocommittee"


# ---------------------------------------------------------------------------
# Detail payload
# ---------------------------------------------------------------------------

def test_state_race_detail_populates_candidate_committee_and_outside_spending_rows(race_conn):
    """Full world for race R under the new digest-free key, detail (race_filter) path.

    receipts: X Pat 300 + Quinn 100 (+ an ARCHIVED 999 from Ghost, excluded), Y Rae 200
    Part-9: ALICE ALPHA 700 in the cycle window, 300 before it (excluded)
    Expected: 600.0 / 3 contributions / 3 donors ; outside 700.0 ; share 0.75.
    """
    _seed_race_r_structure(race_conn)
    _seed_race_r_receipts(race_conn)
    _add_receipt(race_conn, 201, "Ghost", "Giver", 999.0, date="2026-03-03", archived=1)
    _seed_alice_part9(race_conn)

    detail = get_state_race_detail(race_conn, race_key=RACE_R_KEY, election_cycle=CYCLE)

    assert detail is not None
    assert detail["race_key"] == RACE_R_KEY
    assert detail["race_label"] == RACE_R_LABEL
    assert detail["candidate_count"] == 2
    assert detail["total_amount"] == 600.0
    assert detail["contribution_count"] == 3
    assert detail["donor_count"] == 3
    assert detail["outside_spending_total"] == 700.0
    assert detail["top_candidate_share"] == 0.75

    candidate_pairs = {(row["candidate_name"], str(row["committee_id_sbe"])) for row in detail["candidate_rows"]}
    assert candidate_pairs == {("Alice Alpha", "201"), ("Alice Alpha", "202"), ("Bob Beta", "201")}
    assert len(detail["candidate_rows"]) == 3

    committee_rows = detail["committee_receipt_rows"]
    assert [str(row["committee_id_sbe"]) for row in committee_rows] == ["201", "202"]
    assert [row["total_amount"] for row in committee_rows] == [400.0, 200.0]
    assert [row["contribution_count"] for row in committee_rows] == [2, 1]
    assert [row["donor_count"] for row in committee_rows] == [2, 1]

    outside_rows = detail["outside_spending_rows"]
    assert len(outside_rows) == 1
    assert outside_rows[0]["candidate_name"].upper() == "ALICE ALPHA"
    assert outside_rows[0]["total_amount"] == 700.0
