"""Search program, Phase 2 -- every name search on the site matches like /search: the tests-first suite (plan P2-M1).

Plan: .claude/plans/outside-spending-drilldown.md, "### Phase 2 brief" (decisions P2-0 .. P2-6). Phase 1's suite
(tests/test_search_names.py) already proves the normalizer; this file proves the CALLERS use it: one shape and one decoy per
caller (P2-5), plus pins on the three shared helpers. A test whose docstring says "(guard)" passes before the change: it pins
behaviour the change must not break.

Contract pinned (database/name_normalize.py; names chosen by the builder, P2-1):

  name_match_sql(col, q)          -> (NAME_TSVECTOR_SQL(col) + " @@ to_tsquery('simple', ?)", [search_tsquery(q)]) | None
  substring_fallback_sql(col, q)  -> (col + " LIKE ?", ["%" + normalize_name(q) + "%"]) for a normalized query of 4+ chars | None
  normalized_substring_sql(col, q)-> ("UPPER(" + col + ") LIKE ?", ["%" + the normalized tokens joined by "%" + "%"]) | None

  Entity tables take the token predicate and, only when it finds nothing, the one substring fallback (as /search). Transactional
  tables (FEC Schedule A contributor / Schedule B recipient, IRS 527 contributor, the expenditure payee) take the normalized
  substring. Reading chosen here (the brief's text is "'%' + normalize_name(q) + '%'"): the tokens are joined by '%', not by a
  space, because the stored side of a transactional table is NOT normalized -- with a space, "Farrow, Michael" would stop
  matching the stored "FARROW, MICHAEL" (FEC's own format) that the old LIKE '%farrow, michael%' found, and "O'Brien" would
  stop matching "O'BRIEN". With '%' both keep working and "Farrow Michael" (no comma) starts to.

The seed world (fictional names; every table carries the same three kinds of row, each table with its own middle initial so a
page that shows several sections tells them apart):

  P   the positive: tokens MICHAEL + FARROW with punctuation and a middle initial ("Michael A. Farrow", "FARROW, MICHAEL E")
  D1  the token decoy: "Brad Farrow ..." -- found by "Farrow", never by "Farrow Mike"
  D2  the substring decoy: "Nonfarrow ..." -- never listed for "Farrow" while a token match exists (fallback only on zero rows),
      listed by the fragment "arrow" through the fallback. Transactional tables carry "BARROW ..." instead, so a substring
      section on a multi-section page cannot put "nonfarrow" on it.

  per table (P | D1 | D2):
    isbe_committees              Friends of Michael H. Farrow | Friends of Brad Farrow | Nonfarrow Holdings PAC
                                 (+ Prairie Leadership Fund, ACCEPTED family "Prairie Leadership Family" with P)
    candidates (isbe, bulk_candidates_clean, the finance aggregate)  Michael J Farrow | Brad Farrow | Lee Nonfarrow
    analytics_donor_summary      Michael A. Farrow | Brad Farrow | Nonfarrow Group
    analytics_donor_committee_agg  Michael K. Farrow | Brad Farrow | Nonfarrow Group
    lobbying_entities            Michael C. Farrow Strategies | Brad Farrow Associates | Nonfarrow Partners
                                 (+ the hyphen shape Smith-Jones Advocacy Group and its decoy Smithson Advocacy)
    lobbying_clients             Michael B. Farrow Holdings | Brad Farrow Holdings | Nonfarrow Industries
    lobbying_donor_matches       Michael L. Farrow Group / Michael M. Farrow | Brad Farrow Group | Nonfarrow Industries
    irs527_organizations         Michael D. Farrow Victory Fund | Brad Farrow Victory Fund | Nonfarrow Action Fund
    irs527_directors             FARROW, MICHAEL E | FARROW, BRAD | NONFARROW, LEE
    irs527_contributions         FARROW, MICHAEL N | FARROW, BRAD | NONFARROW, LEE        (all to org 527000001)
    fec_candidate_match          FARROW, MICHAEL G | FARROW, BRAD | NONFARROW, LEE
    fec_candidate_committees     MICHAEL G FARROW FOR CONGRESS | BRAD FARROW FOR CONGRESS | NONFARROW FOR CONGRESS
    fec_schedule_a_contributions FARROW, MICHAEL F | FARROW, BRAD | BARROW, LEE
    fec_schedule_b_disbursements FARROW MEDIA, LLC | -- | BARROW MEDIA, LLC
    bulk_expenditures_clean      FARROW PRINTING, INC. | -- | BARROW PRINTING, INC.

  The legacy world (the suggest API's fallbacks: analytics_donor_summary empty, no isbe_committees / bulk_candidates_clean):
    donors Michael P. Farrow | Brad Farrow;  committees Friends of Michael Q. Farrow | Friends of Brad Farrow;
    bulk_committees_clean Rae Smith-Jones for Senate | Smithson for Senate;  finance aggregate Michael R Farrow | Brad Farrow.

Decisions recorded here (each one test the orchestrator can overturn):
  - /person-intelligence's candidate section is rerouted too (the brief's column list for that route names donor, client, org,
    person and entity names; the route's purpose is the "Mike Frerichs" lookup, so its candidate section takes the token rule).
  - Lists keep their count and their rows on ONE predicate: the fallback is chosen when the token COUNT is zero.

Accepted holes (named, not pinned): the legacy suggest sections are not in the two aggregate fallback tests (they use the
same helper); result order and limits; accent folding.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote_plus

import pytest

from database import name_normalize as nn
from database.connection import get_db, init_db
from database.pg_compat import PostgresCompatCursor
from webapp.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]

# The contract's server-side normalization, spelled out (not taken from the product) so the pins measure the product.
CONTRACT_TSVECTOR = "to_tsvector('simple', regexp_replace(upper(coalesce({expr}, '')), '[^A-Z0-9 ]', ' ', 'g'))"


def _each(cases, check) -> None:
    """Run check(case) for every case and fail once, listing every failing case."""
    cases = list(cases)
    failures = []
    for case in cases:
        try:
            check(case)
        except AssertionError as exc:
            failures.append(f"{case!r}: {exc}")
    assert not failures, f"{len(failures)} of {len(cases)} cases fail:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# (1) The shared helpers (database/name_normalize.py)
# ---------------------------------------------------------------------------

def test_name_match_sql_is_the_index_expression_with_the_tsquery_bound():
    """Kills: a predicate spelled differently from the GIN index expression (lower(), no coalesce, another regex), the tsquery
    inlined into the SQL, a helper that returns SQL for a query with no token (it must say None, so the caller lists nothing)."""
    sql, params = nn.name_match_sql("c.name", "Farrow, Mike W.")
    assert sql == CONTRACT_TSVECTOR.format(expr="c.name") + " @@ to_tsquery('simple', ?)"
    assert list(params) == ["farrow:* & (mike:* | michael:*)"] == [nn.search_tsquery("Farrow, Mike W.")]
    assert nn.name_match_sql("c.name", " -- ") is None
    assert nn.name_match_sql("c.name", None) is None


def test_substring_fallback_sql_is_the_phase_1_rule():
    """The one fallback /search runs: the raw column LIKE the normalized query, for 4+ normalized characters only (a short
    fragment never scans a table). Kills: no length floor, the raw query in the pattern, UPPER() around the column (the compat
    layer already runs LIKE as ILIKE, and UPPER() would keep the trigram index on donor_name from serving it)."""
    sql, params = nn.substring_fallback_sql("donor_name", "arrow")
    assert sql == "donor_name LIKE ?"
    assert list(params) == ["%ARROW%"]
    sql, params = nn.substring_fallback_sql("donor_name", "o'Neil-Sm")
    assert list(params) == ["%O NEIL SM%"]
    assert nn.substring_fallback_sql("donor_name", "arr") is None
    assert nn.substring_fallback_sql("donor_name", "") is None


def test_normalized_substring_sql_joins_the_query_tokens_with_wildcards():
    """The transactional rule (P2-2): no tsvector over millions of rows, a substring on UPPER(col); the tokens are joined by '%'
    (module docstring) so "Farrow, Michael", "Farrow Michael" and "farrow-michael" all reach a stored "FARROW, MICHAEL F"."""
    sql, params = nn.normalized_substring_sql("contributor_name", "Farrow, Michael")
    assert sql == "UPPER(contributor_name) LIKE ?"
    assert list(params) == ["%FARROW%MICHAEL%"]
    assert list(nn.normalized_substring_sql("contributor_name", "  o'brien ")[1]) == ["%O%BRIEN%"]
    assert nn.normalized_substring_sql("contributor_name", "%_") is None


HOSTILE_QUERIES = [
    "Farrow'); DROP TABLE isbe_committees; --",
    "farrow:* | !mike & (x",
    "50%_off \\ Farrow",
    'Robert "); SELECT 1; --',
]


def test_no_helper_puts_the_query_text_into_the_sql():
    """User text reaches SQL only as a bound parameter, and the parameter holds only what the normalizer lets through. Kills an
    f-string predicate, a tsquery built from the raw text (operators survive), a LIKE pattern with the user's % and _."""
    helpers = [
        (nn.name_match_sql, re.compile(r"[a-z0-9:*&|() ]+")),
        (nn.substring_fallback_sql, re.compile(r"%[A-Z0-9 ]+%")),
        (nn.normalized_substring_sql, re.compile(r"%[A-Z0-9%]+%")),
    ]

    def check(query):
        for helper, allowed in helpers:
            result = helper("col", query)
            if result is None:
                continue
            sql, params = result
            assert sql == helper("col", "farrow mike")[0], f"{helper.__name__} SQL depends on the query: {sql}"
            for param in params:
                assert allowed.fullmatch(param), f"{helper.__name__} parameter {param!r}"

    _each(HOSTILE_QUERIES, check)


def test_the_old_search_normalizer_is_gone_and_nothing_imports_it():
    """P2-1: webapp/utils/search_normalize.py is absorbed by database/name_normalize.py, not shimmed."""
    assert not (REPO_ROOT / "webapp" / "utils" / "search_normalize.py").exists()
    assert not (REPO_ROOT / "tests" / "test_search_normalize.py").exists()
    this_file = Path(__file__).resolve()
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for folder in ("webapp", "database", "scripts", "cli", "tests")
        for path in (REPO_ROOT / folder).rglob("*.py")
        if path.resolve() != this_file and "search_normalize" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# (2) The seed worlds
# ---------------------------------------------------------------------------

def _seed_isbe_and_bulk(conn) -> None:
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
    conn.execute(
        """
        INSERT INTO isbe_committees (id, type, name, party, city, state) VALUES
            (501, 'Candidate', 'Friends of Michael H. Farrow', 'Democratic', 'Chicago', 'IL'),
            (502, 'Candidate', 'Friends of Brad Farrow', 'Republican', 'Peoria', 'IL'),
            (503, 'Political Action', 'Nonfarrow Holdings PAC', NULL, 'Joliet', 'IL'),
            (505, 'Candidate', 'Prairie Leadership Fund', 'Democratic', 'Chicago', 'IL')
        """
    )
    conn.execute(
        """
        CREATE TABLE isbe_candidates (
            id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT, address1 TEXT, address2 TEXT, city TEXT, state TEXT,
            zipcode TEXT, office TEXT, district_type TEXT, district TEXT, residence_county TEXT, party TEXT,
            redaction_requested BOOLEAN DEFAULT FALSE
        )
        """
    )
    conn.execute(
        "INSERT INTO isbe_candidates (id, first_name, last_name, office) VALUES "
        "(601, 'Michael J', 'Farrow', 'Comptroller'), (602, 'Brad', 'Farrow', 'County Clerk'), "
        "(603, 'Lee', 'Nonfarrow', 'Trustee')"
    )
    conn.execute("CREATE TABLE isbe_candidate_committees (id INTEGER PRIMARY KEY, committee_id INTEGER, candidate_id INTEGER)")
    conn.execute("INSERT INTO isbe_candidate_committees VALUES (1, 501, 601), (2, 502, 602), (3, 503, 603)")
    conn.execute("CREATE TABLE bulk_candidates_clean (candidate_id INTEGER, candidate_full_name TEXT)")
    conn.execute(
        "INSERT INTO bulk_candidates_clean VALUES (601, 'Michael J Farrow'), (602, 'Brad Farrow'), (603, 'Lee Nonfarrow')"
    )
    conn.execute(
        """
        CREATE TABLE bulk_candidate_committee_finance_agg (
            candidate_id INTEGER, candidate_full_name TEXT, office_sought TEXT, district_type TEXT, district TEXT,
            candidate_party_affiliation TEXT, committee_id_sbe INTEGER, committee_name TEXT, committee_type TEXT,
            committee_party_affiliation TEXT, period_year INTEGER, election_cycle INTEGER, filing_count INTEGER,
            sum_total_receipts DOUBLE PRECISION, sum_total_expenditures DOUBLE PRECISION,
            max_ending_funds_available DOUBLE PRECISION, archived_filing_count INTEGER, period_start_date TEXT,
            period_end_date TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO bulk_candidate_committee_finance_agg VALUES
            (601, 'Michael J Farrow', 'Comptroller', 'Statewide', NULL, 'Democratic', 501, 'Friends of Michael H. Farrow',
             'Candidate', 'Democratic', 2026, 2026, 1, 1000.0, 400.0, 600.0, 0, '2026-01-05', '2026-03-15'),
            (602, 'Brad Farrow', 'County Clerk', 'County', 'Cook', 'Republican', 502, 'Friends of Brad Farrow',
             'Candidate', 'Republican', 2026, 2026, 1, 900.0, 300.0, 600.0, 0, '2026-01-05', '2026-03-15'),
            (603, 'Lee Nonfarrow', 'Trustee', 'Community College', '3', NULL, 503, 'Nonfarrow Holdings PAC',
             'Political Action', NULL, 2026, 2026, 1, 800.0, 200.0, 600.0, 0, '2026-01-05', '2026-03-15')
        """
    )
    conn.execute("CREATE TABLE bulk_expenditures_clean (payee_last_or_business_name TEXT, amount DOUBLE PRECISION)")
    conn.execute("INSERT INTO bulk_expenditures_clean VALUES ('FARROW PRINTING, INC.', 1200.0), ('BARROW PRINTING, INC.', 900.0)")
    conn.execute(
        "INSERT INTO committee_family (id, label, status, member_hash) VALUES (1, 'Prairie Leadership Family', 'accepted', 'h-1')"
    )
    conn.execute("INSERT INTO committee_family_member (family_id, committee_id) VALUES (1, 501), (1, 505)")


def _seed_schema_tables(conn) -> None:
    conn.execute(
        """
        INSERT INTO analytics_donor_summary (source, donor_key, donor_name, donor_city, donor_state, total_amount,
                                             contribution_count, committee_count) VALUES
            ('bulk_receipts', 'michael|farrow|1 oak st', 'Michael A. Farrow', 'Chicago', 'IL', 900.0, 3, 1),
            ('bulk_receipts', 'brad|farrow|2 elm st', 'Brad Farrow', 'Peoria', 'IL', 800.0, 2, 1),
            ('bulk_receipts', 'nonfarrow group||3 pine st', 'Nonfarrow Group', 'Joliet', 'IL', 700.0, 1, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO analytics_donor_committee_agg (source, donor_key, donor_name, committee_id, committee_name, total_amount,
                                                   contribution_count) VALUES
            ('bulk_receipts', 'michael|farrow|1 oak st', 'Michael K. Farrow', '501', 'Friends of Michael H. Farrow', 1500.0, 3),
            ('bulk_receipts', 'brad|farrow|2 elm st', 'Brad Farrow', '502', 'Friends of Brad Farrow', 1200.0, 2),
            ('bulk_receipts', 'nonfarrow group||3 pine st', 'Nonfarrow Group', '503', 'Nonfarrow Holdings PAC', 1100.0, 1)
        """
    )
    conn.execute(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES "
        "(701, 'Michael C. Farrow Strategies', 2026), (702, 'Brad Farrow Associates', 2026), "
        "(703, 'Nonfarrow Partners', 2026), (704, 'Smith-Jones Advocacy Group', 2026), (705, 'Smithson Advocacy', 2026)"
    )
    conn.execute(
        "INSERT INTO lobbying_clients (client_id, client_name) VALUES "
        "(801, 'Michael B. Farrow Holdings'), (802, 'Brad Farrow Holdings'), (803, 'Nonfarrow Industries')"
    )
    conn.execute(
        """
        INSERT INTO lobbying_donor_matches (client_id, donor_key, client_name, donor_name, score, method) VALUES
            (801, 'michael|farrow|1 oak st', 'Michael L. Farrow Group', 'Michael M. Farrow', 0.9, 'jaccard'),
            (802, 'brad|farrow|2 elm st', 'Brad Farrow Group', 'Brad Farrow', 0.9, 'jaccard'),
            (803, 'nonfarrow group||3 pine st', 'Nonfarrow Industries', 'Nonfarrow Group', 0.9, 'jaccard')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip) VALUES
            ('527000001', 1, 0, 'Michael D. Farrow Victory Fund', 'Chicago', 'IL', '60601'),
            ('527000002', 2, 0, 'Brad Farrow Victory Fund', 'Peoria', 'IL', '61602'),
            ('527000003', 3, 0, 'Nonfarrow Action Fund', 'Joliet', 'IL', '60432')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state) VALUES
            (1, '527000001', 'Michael D. Farrow Victory Fund', 'FARROW, MICHAEL E', 'Treasurer', 'Chicago', 'IL'),
            (2, '527000002', 'Brad Farrow Victory Fund', 'FARROW, BRAD', 'Chair', 'Peoria', 'IL'),
            (3, '527000003', 'Nonfarrow Action Fund', 'NONFARROW, LEE', 'Chair', 'Joliet', 'IL')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date) VALUES
            (1, '527000001', 'Michael D. Farrow Victory Fund', 'FARROW, MICHAEL N', 'Chicago', 'IL', 500.0, '2026-01-15'),
            (1, '527000001', 'Michael D. Farrow Victory Fund', 'FARROW, BRAD', 'Peoria', 'IL', 400.0, '2026-02-15'),
            (1, '527000001', 'Michael D. Farrow Victory Fund', 'NONFARROW, LEE', 'Joliet', 'IL', 300.0, '2026-03-15')
        """
    )
    conn.execute(
        "INSERT INTO fec_il_candidate_seed (candidate_key, candidate_name, normalized_candidate_name) VALUES "
        "('ck-farrow-michael', 'FARROW, MICHAEL G', 'FARROW MICHAEL G'), ('ck-farrow-brad', 'FARROW, BRAD', 'FARROW BRAD'), "
        "('ck-nonfarrow-lee', 'NONFARROW, LEE', 'NONFARROW LEE')"
    )
    conn.execute(
        """
        INSERT INTO fec_candidate_match (seed_candidate_key, candidate_name, fec_name, fec_candidate_id, office, district,
                                         cycle, match_status, match_score) VALUES
            ('ck-farrow-michael', 'FARROW, MICHAEL G', 'FARROW, MICHAEL G', 'H4IL00001', 'U.S. House', 'IL-01', 2026,
             'matched', 0.95),
            ('ck-farrow-brad', 'FARROW, BRAD', 'FARROW, BRAD', 'H4IL00002', 'U.S. House', 'IL-02', 2026, 'matched', 0.95),
            ('ck-nonfarrow-lee', 'NONFARROW, LEE', 'NONFARROW, LEE', 'H4IL00003', 'U.S. House', 'IL-03', 2026, 'matched', 0.95)
        """
    )
    conn.execute(
        "INSERT INTO fec_candidate_committees (candidate_id, committee_id, cycle, committee_name) VALUES "
        "('H4IL00001', 'C00000001', 2026, 'MICHAEL G FARROW FOR CONGRESS'), "
        "('H4IL00002', 'C00000002', 2026, 'BRAD FARROW FOR CONGRESS'), "
        "('H4IL00003', 'C00000003', 2026, 'NONFARROW FOR CONGRESS')"
    )
    conn.execute(
        """
        INSERT INTO fec_schedule_a_contributions (sub_id, cycle, contributor_name, contributor_city, contributor_state,
                                                  contribution_receipt_amount, donor_entity_key) VALUES
            ('sa-1', 2026, 'FARROW, MICHAEL F', 'CHICAGO', 'IL', 250.0, 'fe-farrow-michael'),
            ('sa-2', 2026, 'FARROW, BRAD', 'PEORIA', 'IL', 200.0, 'fe-farrow-brad'),
            ('sa-3', 2026, 'BARROW, LEE', 'JOLIET', 'IL', 150.0, 'fe-barrow-lee')
        """
    )
    conn.execute(
        "INSERT INTO fec_schedule_b_disbursements (sub_id, cycle, recipient_name, disbursement_amount) VALUES "
        "('sb-1', 2026, 'FARROW MEDIA, LLC', 1000.0), ('sb-2', 2026, 'BARROW MEDIA, LLC', 900.0)"
    )


def _seed_legacy(conn) -> None:
    conn.execute(
        "INSERT INTO donors (name, normalized_name) VALUES ('Michael P. Farrow', 'michael p farrow'), "
        "('Brad Farrow', 'brad farrow')"
    )
    conn.execute("INSERT INTO committees (name) VALUES ('Friends of Michael Q. Farrow'), ('Friends of Brad Farrow')")
    conn.execute("CREATE TABLE bulk_committees_clean (committee_id_sbe INTEGER, committee_name TEXT)")
    conn.execute("INSERT INTO bulk_committees_clean VALUES (9001, 'Rae Smith-Jones for Senate'), (9002, 'Smithson for Senate')")
    conn.execute("CREATE TABLE bulk_candidate_committee_finance_agg (candidate_id INTEGER, candidate_full_name TEXT)")
    conn.execute("INSERT INTO bulk_candidate_committee_finance_agg VALUES (701, 'Michael R Farrow'), (702, 'Brad Farrow')")


def _app_config(db_path: str, **overrides) -> dict:
    config = {
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "DASHBOARD_PREWARM_ENABLED": False,
        "ROUTE_PERF_CACHE_ENABLED": False,
        "GLOBAL_DATA_STATUS_CACHE_TTL_SECONDS": 0,
        "SEARCH_QUERY_TIMEOUT_MS": 30000,
    }
    config.update(overrides)
    return config


def _client(tmp_path: Path, world: str):
    db_path = str(tmp_path / f"callers_{world}.db")
    init_db(db_path)
    conn = get_db(db_path)
    if world == "shared":
        _seed_isbe_and_bulk(conn)
        _seed_schema_tables(conn)
    else:
        _seed_legacy(conn)
    conn.commit()
    conn.close()
    return create_app(_app_config(db_path)).test_client()


@pytest.fixture
def shared_client(tmp_path):
    return _client(tmp_path, "shared")


def _text(client, url_template: str, query: str, section: str | None = None) -> str:
    """The lower-cased response body for the query; with ``section``, only the HTML from that heading to its </section>."""
    response = client.get(url_template.format(q=quote_plus(query)))
    assert response.status_code == 200, f"{url_template} -> {response.status_code}"
    text = response.get_data(as_text=True).lower()
    if section:
        start = text.find(section)
        assert start != -1, f"no section {section!r} on the page"
        end = text.find("</section>", start)
        text = text[start:end if end != -1 else None]
    return text


# ---------------------------------------------------------------------------
# (3) One shape and one decoy per caller
# ---------------------------------------------------------------------------

class Caller:
    def __init__(self, caller_id, url, query, marker, decoy, *, world="shared", farrow_marker=None, transactional=False,
                 section=None):
        self.id, self.url, self.query, self.marker, self.decoy = caller_id, url, query, marker, decoy
        self.world, self.transactional, self.section = world, transactional, section
        # The P row this section shows for "Farrow" / "arrow" (the aggregate fallback tests); entity sections only.
        self.farrow_marker = None if transactional else (farrow_marker or marker)

    def __repr__(self):
        return self.id


NETWORK = "/api/analytics/network/suggest?entity_type={kind}&q={{q}}"
FEDERAL = "/api/federal/network/suggest?entity_type={kind}&q={{q}}"
CONTRIBUTIONS = "contributions to this organization"

CALLERS = [
    # committees.py
    Caller("committees-list", "/committees/?period=all&q={q}", "farrow mike", "friends of michael h. farrow", "brad"),
    Caller("committees-by-family", "/committees/?by=family&period=all&q={q}", "farrow mike", "prairie leadership family",
           "brad"),
    Caller("committees-suggest", "/committees/suggest?q={q}", "farrow mike", "friends of michael h. farrow", "brad"),
    # lobbying.py (the hyphen shape on the entity list and suggest)
    Caller("lobbying-list", "/lobbying/?period=all&q={q}", "jones smith", "smith-jones advocacy group", "smithson",
           farrow_marker="michael c. farrow strategies"),
    Caller("lobbying-suggest", "/lobbying/suggest?q={q}", "jones smith", "smith-jones advocacy group", "smithson",
           farrow_marker="michael c. farrow strategies"),
    Caller("lobbying-flows-suggest", "/lobbying/flows/suggest?q={q}", "farrow mike", "michael l. farrow group", "brad"),
    Caller("lobbying-flows-data", "/lobbying/flows/data?period=all&client={q}", "farrow mike", "michael l. farrow group",
           "brad"),
    # irs527.py
    Caller("527-list", "/527/?period=all&q={q}", "farrow mike", "michael d. farrow victory fund", "brad"),
    Caller("527-suggest", "/527/suggest?q={q}", "farrow mike", "michael d. farrow victory fund", "brad"),
    Caller("527-org-contributions", "/527/527000001?contrib_q={q}", "Farrow Michael", "farrow, michael n", "brad",
           transactional=True, section=CONTRIBUTIONS),
    # api.py -- the network suggest API
    Caller("network-suggest-donors", NETWORK.format(kind="donor"), "farrow mike", "michael a. farrow", "brad"),
    Caller("network-suggest-committees", NETWORK.format(kind="committee"), "farrow mike", "friends of michael h. farrow",
           "brad"),
    Caller("network-suggest-candidates", NETWORK.format(kind="candidate"), "farrow mike", "michael j farrow", "brad"),
    Caller("network-suggest-vendors", NETWORK.format(kind="vendor"), "Farrow Printing Inc", "farrow printing, inc.",
           "barrow", transactional=True),
    Caller("network-suggest-legacy-donors", NETWORK.format(kind="donor"), "farrow mike", "michael p. farrow", "brad",
           world="legacy"),
    Caller("network-suggest-legacy-bulk-committees", NETWORK.format(kind="committee"), "jones smith",
           "rae smith-jones for senate", "smithson", world="legacy"),
    Caller("network-suggest-legacy-committees", NETWORK.format(kind="committee"), "farrow mike",
           "friends of michael q. farrow", "brad", world="legacy"),
    Caller("network-suggest-aggregate-candidates", NETWORK.format(kind="candidate"), "farrow mike", "michael r farrow",
           "brad", world="legacy"),
    # api.py -- the federal suggest API
    Caller("federal-suggest-candidates", FEDERAL.format(kind="candidate"), "mike farrow", "farrow, michael g", "brad"),
    Caller("federal-suggest-committees", FEDERAL.format(kind="committee"), "farrow mike", "michael g farrow for congress",
           "brad"),
    Caller("federal-suggest-donors", FEDERAL.format(kind="donor"), "Farrow Michael", "farrow, michael f", "brad",
           transactional=True),
    Caller("federal-suggest-vendors", FEDERAL.format(kind="vendor"), "Farrow Media LLC", "farrow media, llc", "barrow",
           transactional=True),
    # models.py -- the candidate-finance filter
    Caller("candidate-finance", "/candidate-finance/?period=all&q={q}", "farrow mike", "michael j farrow", "brad"),
    # main.py -- /investigate (one entry per section; the donor-committee and second match sections are on the flow tab)
    Caller("investigate-donors", "/investigate?q={q}", "farrow mike", "michael a. farrow", "brad"),
    Caller("investigate-lobbying-clients", "/investigate?q={q}", "farrow mike", "michael b. farrow holdings", "brad"),
    Caller("investigate-527-orgs", "/investigate?q={q}", "farrow mike", "michael d. farrow victory fund", "brad"),
    Caller("investigate-lobbying-matches", "/investigate?q={q}", "farrow mike", "michael l. farrow group", "brad"),
    Caller("investigate-flow-donor-committees", "/investigate?tab=flow&q={q}", "farrow mike", "michael k. farrow", "brad"),
    Caller("investigate-flow-lobbying-matches", "/investigate?tab=flow&q={q}", "farrow mike", "michael l. farrow group",
           "brad"),
    Caller("investigate-fec-donors", "/investigate?q={q}", "Farrow Michael", "farrow, michael f", "brad", transactional=True),
    # main.py -- /person-intelligence
    Caller("person-intelligence-donors", "/person-intelligence?q={q}", "farrow mike", "michael a. farrow", "brad"),
    Caller("person-intelligence-candidates", "/person-intelligence?q={q}", "farrow mike", "michael j farrow", "brad"),
    Caller("person-intelligence-527-directors", "/person-intelligence?q={q}", "farrow mike", "farrow, michael e", "brad"),
    Caller("person-intelligence-lobbying-entities", "/person-intelligence?q={q}", "farrow mike",
           "michael c. farrow strategies", "brad"),
    Caller("person-intelligence-lobbying-clients", "/person-intelligence?q={q}", "farrow mike",
           "michael b. farrow holdings", "brad"),
    Caller("person-intelligence-fec-donors", "/person-intelligence?q={q}", "Farrow Michael", "farrow, michael f", "brad",
           transactional=True),
]


@pytest.mark.parametrize("caller", CALLERS, ids=[caller.id for caller in CALLERS])
def test_the_caller_finds_the_shape_and_not_the_decoy(tmp_path, caller):
    """Entity callers: a nickname in reversed order ("farrow mike" -> "Michael X. Farrow") or a hyphenated name typed as two
    words in reversed order ("jones smith" -> "Smith-Jones ..."), never the namesake with another first name. Transactional
    callers: "Farrow Michael" reaches "FARROW, MICHAEL X" across the comma, never "FARROW, BRAD". Kills a caller still on the
    whole-string LIKE '%q%', and a caller that matches any token instead of every token."""
    text = _text(_client(tmp_path, caller.world), caller.url, caller.query, caller.section)
    assert caller.marker in text, f"{caller.query!r} does not reach {caller.marker!r}"
    assert caller.decoy not in text, f"{caller.query!r} lists the decoy {caller.decoy!r}"


ENTITY_CALLERS = [caller for caller in CALLERS if caller.world == "shared" and caller.farrow_marker]


def test_no_entity_caller_falls_back_to_substring_while_a_token_matches(shared_client):
    """R2 of Phase 1 on every caller: "Farrow" matches tokens, so "Nonfarrow ..." (a substring hit only) is not listed. Kills a
    caller that ORs the fallback into the token predicate, or runs it whenever the query is long enough."""

    def check(caller):
        text = _text(shared_client, caller.url, "Farrow", caller.section)
        assert caller.farrow_marker in text, f"'Farrow' does not reach {caller.farrow_marker!r}"
        assert "nonfarrow" not in text, "a substring-only row is listed although tokens matched"

    _each(ENTITY_CALLERS, check)


def test_every_entity_caller_falls_back_to_substring_when_no_token_matches(shared_client):
    """(guard) "arrow" starts no token, so the one substring fallback lists the rows that contain it. Kills a caller that
    dropped the fallback (mid-word fragments, which the old LIKE served, would stop working)."""

    def check(caller):
        text = _text(shared_client, caller.url, "arrow", caller.section)
        assert caller.farrow_marker in text, f"'arrow' does not reach {caller.farrow_marker!r} through the fallback"

    _each(ENTITY_CALLERS, check)


@pytest.fixture
def sql_log(monkeypatch):
    log: list[str] = []
    original = PostgresCompatCursor.execute

    def recording(self, sql, params=None):
        log.append(sql)
        return original(self, sql, params)

    monkeypatch.setattr(PostgresCompatCursor, "execute", recording)
    return log


def test_every_caller_binds_the_query_as_a_parameter(shared_client, sql_log):
    """(guard) No statement any caller runs carries the typed text. Kills an f-string predicate on any rerouted route."""
    urls = sorted({caller.url for caller in CALLERS if caller.world == "shared"})

    def check(url):
        sql_log.clear()
        _text(shared_client, url, "Zqxjv Farrow")
        leaked = [sql for sql in sql_log if "zqxjv" in sql.lower()]
        assert not leaked, leaked[:1]

    _each(urls, check)


def test_candidate_finance_matches_committee_names_by_token_and_keeps_office_as_substring(shared_client):
    """The filter ORs three columns: candidate and committee names by token, office_sought by substring (brief: leave the
    office as it is). "farrow friends michael" is in no candidate name but every token is in P's committee name."""
    url = "/candidate-finance/?period=all&q={q}"
    text = _text(shared_client, url, "farrow friends michael")
    assert "michael j farrow" in text and "brad" not in text
    text = _text(shared_client, url, "Comptroll")
    assert "michael j farrow" in text and "brad" not in text


# ---------------------------------------------------------------------------
# (4) The Viz Lab label search (Python-side, P2-4)
# ---------------------------------------------------------------------------

def test_the_viz_lab_label_search_matches_tokens_then_falls_back_to_substring(tmp_path, monkeypatch):
    """Every query token (nickname-expanded) prefixes a token of the normalized label; only when no label matches does the
    normalized substring apply. Kills the old `search_text in label.lower()` and a per-label "token OR substring"."""
    graph = {
        "nodes": [
            {"id": "donor:p", "label": "FARROW, MICHAEL W", "node_type": "donor"},
            {"id": "donor:b", "label": "BRAD FARROW", "node_type": "donor"},
            {"id": "donor:n", "label": "NONFARROW GROUP", "node_type": "donor"},
            {"id": "committee:1", "label": "Prairie Leadership Fund", "node_type": "committee"},
        ],
        "edges": [
            {"source": donor, "target": "committee:1", "edge_type": "donation", "weight": 6000.0}
            for donor in ("donor:p", "donor:b", "donor:n")
        ],
    }
    monkeypatch.setattr("webapp.routes.experimental.get_network_graph", lambda conn, **kwargs: graph)
    db_path = str(tmp_path / "callers_viz.db")
    init_db(db_path)
    client = create_app(_app_config(db_path, EXPERIMENTAL_VIZ_LAB_ENABLED=True)).test_client()

    def labels(search):
        payload = client.get(f"/experimental/viz-lab/data/network_slice?period=all&search={quote_plus(search)}").get_json()
        return sorted(row["label"] for row in payload.get("node_metrics") or [])

    assert labels("Mike Farrow") == ["FARROW, MICHAEL W"]
    assert labels("Farrow") == ["BRAD FARROW", "FARROW, MICHAEL W"]
    assert labels("arrow") == ["BRAD FARROW", "FARROW, MICHAEL W", "NONFARROW GROUP"]
