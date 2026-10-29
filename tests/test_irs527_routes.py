"""Tests for IRS 527 web routes."""
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from database.connection import get_db, init_db
from tests.test_committee_rollups import (
    _anchors, _fetch, _figures, _form_submission, _header_hrefs, _headings, _list_rows, _page, _page_links,
    _rows, _scope_key, _section,
)
from webapp.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    db_path = str(tmp_path / "test_irs527_routes.db")
    init_db(db_path)
    conn = get_db(db_path)

    # Seed some 527 data
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip)
        VALUES ('123456789', 100, 0, 'Test 527 Org', 'Chicago', 'IL', '60601')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state)
        VALUES (100, '123456789', 'Test 527 Org', 'Jane Director', 'President', 'Chicago', 'IL')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, purpose)
        VALUES (100, '123456789', 'Test 527 Org', 'Recipient Inc', 'Springfield', 'IL', 5000, 'Campaign support')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_reports (
            form_id, ein, period_start, period_end, org_name, total_contributions, total_expenditures
        ) VALUES (100, '123456789', '2026-01-01', '2026-03-31', 'Test 527 Org', 12345, 6789)
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contributions (
            form_id, ein, org_name, contributor_name, city, state, amount, date
        ) VALUES (100, '123456789', 'Test 527 Org', 'Acme Donor', 'Chicago', 'IL', 4321, '2026-02-01')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_contribution_rollup (ein, total_amount, contribution_count)
        VALUES ('123456789', 4321, 1)
        """
    )
    conn.commit()
    conn.close()

    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_527_list(client):
    response = client.get('/527/?period=all')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data
    assert b'$12,345.00' in response.data
    assert b'$6,789.00' in response.data
    assert b'$4,321.00' in response.data


def test_527_list_search(client):
    response = client.get('/527/?q=Test')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data


def test_527_list_dedupes_same_ein_rows_by_form_id_seq(client):
    conn = get_db(client.application.config["DATABASE_PATH"])
    conn.execute(
        """
        INSERT INTO irs527_organizations (
            ein, form_id, form_id_seq, org_name, city, state, zip, material_change_date, insert_datetime
        ) VALUES ('555555555', 8871, 100, 'Dup Org Old', 'Chicago', 'IL', '60601', '2025-01-01', '2025-01-02')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_organizations (
            ein, form_id, form_id_seq, org_name, city, state, zip, material_change_date, insert_datetime
        ) VALUES ('555555555', 8871, 101, 'Dup Org New', 'Springfield', 'IL', '62701', '2025-02-01', '2025-02-02')
        """
    )
    conn.commit()
    conn.close()

    response = client.get('/527/?q=Dup Org&period=all')
    assert response.status_code == 200
    assert b'Dup Org New' in response.data
    assert b'Dup Org Old' not in response.data
    assert response.data.count(b'/527/555555555') == 1


def test_527_detail(client):
    response = client.get('/527/123456789?period=all')
    assert response.status_code == 200
    assert b'Test 527 Org' in response.data
    assert b'Jane Director' in response.data
    assert b'Recipient Inc' in response.data
    assert b'Acme Donor' in response.data
    assert b'Contributions To This Organization' in response.data


def test_527_detail_period_spans_the_real_dates_and_skips_a_malformed_one(client):
    """FB-M4 (MT-017: prod /527/813554739 read "Period: 1000 to 995"). The TEXT period columns hold ISO (YYYY-MM-DD) or
    compact (YYYYMMDD) dates; a malformed value ("1000" / "995") won the lexicographic MIN / MAX. One malformed report and
    two real ones (compact 2019-01-01..2019-06-30, ISO 2020-01-01..2020-06-30): the Period is 2019-01-01 to 2020-06-30,
    shown as ISO dates. Kills the bare MIN/MAX over text, an ISO-only filter (the compact report is the earliest; every
    local value is compact), and a compact value shown raw."""
    conn = get_db(client.application.config["DATABASE_PATH"])
    conn.execute(
        """
        INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip)
        VALUES ('987654321', 300, 0, 'Prairie Ledger Fund', 'Peoria', 'IL', '61602')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_reports (form_id, ein, period_start, period_end, org_name, total_contributions, total_expenditures)
        VALUES (301, '987654321', '1000', '995', 'Prairie Ledger Fund', 10, 1),
               (302, '987654321', '20190101', '20190630', 'Prairie Ledger Fund', 20, 2),
               (303, '987654321', '2020-01-01', '2020-06-30', 'Prairie Ledger Fund', 30, 3)
        """
    )
    conn.commit()
    conn.close()

    text = client.get("/527/987654321?period=all").get_data(as_text=True)
    assert "<strong>Period:</strong> 2019-01-01 to 2020-06-30" in text
    assert "1000" not in text.split("<strong>Period:</strong>", 1)[1].split("</p>", 1)[0]


def test_527_detail_period_override_skips_malformed_transaction_dates(client):
    """FB-M4b (prod /527/813554739 still read "Period: 1000 to 995" after FB-M4): the page overrides the reports' period
    with the first / last transaction dates, and irs527_contributions.date mostly holds amounts (data dictionary A11).
    Org 987654322: reports 2019-01-01..2020-06-30, a contribution dated "1000" and an expenditure dated "995" -> the
    report dates. Org 987654323 (guard: the override is kept, and it REPLACES the reports' span with the transactions'
    own): the same reports plus one expenditure dated 20180315 (compact) -> 2018-03-15 to 2018-03-15. Kills a bare MIN/MAX
    over the transaction text and a compact date shown raw."""
    conn = get_db(client.application.config["DATABASE_PATH"])
    for ein, name, form in (("987654322", "Lakeshore Ledger Fund", 400), ("987654323", "Bluff Ledger Fund", 500)):
        conn.execute(
            "INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip) "
            "VALUES (?, ?, 0, ?, 'Peoria', 'IL', '61602')",
            (ein, form, name),
        )
        conn.execute(
            "INSERT INTO irs527_reports (form_id, ein, period_start, period_end, org_name, total_contributions, "
            "total_expenditures) VALUES (?, ?, '20190101', '20190630', ?, 20, 2), (?, ?, '2020-01-01', '2020-06-30', ?, 30, 3)",
            (form + 1, ein, name, form + 2, ein, name),
        )
    conn.execute(
        "INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date) "
        "VALUES (401, '987654322', 'Lakeshore Ledger Fund', 'Quill Donor', 'Peoria', 'IL', 1000, '1000')"
    )
    conn.execute(
        "INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, purpose, date) "
        "VALUES (401, '987654322', 'Lakeshore Ledger Fund', 'Vale Print', 'Peoria', 'IL', 995, 'Mailers', '995'), "
        "(501, '987654323', 'Bluff Ledger Fund', 'Vale Print', 'Peoria', 'IL', 250, 'Mailers', '20180315')"
    )
    conn.commit()
    conn.close()

    def period(ein):
        text = client.get(f"/527/{ein}?period=all").get_data(as_text=True)
        return text.split("<strong>Period:</strong>", 1)[1].split("</p>", 1)[0].strip()

    problems = [f"{ein}: {period(ein)!r} != {want!r}" for ein, want in (
        ("987654322", "2019-01-01 to 2020-06-30"), ("987654323", "2018-03-15 to 2018-03-15")) if period(ein) != want]
    assert not problems, problems


def test_527_detail_uses_highest_form_id_seq_row(client):
    conn = get_db(client.application.config["DATABASE_PATH"])
    conn.execute(
        """
        INSERT INTO irs527_organizations (
            ein, form_id, form_id_seq, org_name, address_1, city, state, zip, material_change_date, insert_datetime
        ) VALUES ('666666666', 8871, 200, 'Old Detail Org', '100 Old St', 'Chicago', 'IL', '60601', '2025-01-01', '2025-01-02')
        """
    )
    conn.execute(
        """
        INSERT INTO irs527_organizations (
            ein, form_id, form_id_seq, org_name, address_1, city, state, zip, material_change_date, insert_datetime
        ) VALUES ('666666666', 8871, 201, 'Newest Detail Org', '200 New Ave', 'Peoria', 'IL', '61602', '2025-02-01', '2025-02-02')
        """
    )
    conn.commit()
    conn.close()

    response = client.get('/527/666666666?period=all')
    assert response.status_code == 200
    assert b'Newest Detail Org' in response.data
    assert b'Old Detail Org' not in response.data
    assert b'Peoria' in response.data


def test_527_detail_not_found(client):
    response = client.get('/527/999999999')
    assert response.status_code == 404


def test_527_dark_money(client):
    response = client.get('/527/dark-money')
    assert response.status_code == 200


@pytest.fixture
def empty_app(tmp_path: Path):
    db_path = str(tmp_path / "test_empty.db")
    init_db(db_path)
    app = create_app({'TESTING': True, 'DATABASE_PATH': db_path})
    return app


@pytest.fixture
def empty_client(empty_app):
    return empty_app.test_client()


def test_527_list_empty_tables(empty_client):
    response = empty_client.get('/527/')
    assert response.status_code == 200


def test_527_dark_money_empty_tables(empty_client):
    response = empty_client.get('/527/dark-money')
    assert response.status_code == 200


# ===========================================================================
# S-PB (tests first), 527 side: J-P1 page order and C4 "see all" pages.
#
# Plan: .claude/plans/profiles-races-dated-widgets.md  6.2 (S-PB), design 2.1, journal J-P1 (accepted) and J-P6.
#
# Contract encoded below (anything not listed is deliberately NOT pinned):
#   * /527/<ein>/contributors and /527/<ein>/recipients (404 for an unknown EIN): every contributor / recipient of
#     the org aggregated per contributor_name / recipient_name (NULL and blank names left out, like the detail's
#     Top-10 tables), one row each with the cells name / total / records / first date / last date (left to right,
#     other columns free), default order total descending, 50 rows per page, ?sort=total|count|name|last,
#     ?dir=asc|desc, ?page=N, ?q=<case-insensitive substring of the name> applied before paging, a q form that
#     carries the scope, one scope bar, default scope ALL TIME (the detail's, so its Top-10 is the head of the
#     list), and a windowed <h2> (scope-tag text = the scope label).  Same query vocabulary as the committee
#     pages -- an assumption the report flags.
#   * /527/<ein> (J-P1): Financial Summary, Top Contributors, Top Expenditure Recipients come first among the
#     data sections (in that order), every other section follows, Filing History is last; where Organization
#     Details sits is NOT pinned.  The Top Contributors / Top Expenditure Recipients sections each hold one
#     "See all N ..." link (N = the unique count in the page scope) to /527/<ein>/contributors resp. /recipients,
#     opening in the same scope.  The Top-10 tables are the first ten rows of those lists.
#
# Fixture orgs (new fixture ledger_app; the existing `app` fixture is untouched):
#   500000001 four contributors / recipients whose total, name, count and last-date orders all differ
#   500000002 twelve of each (the detail stops at ten), several rows each, 2023 rows that a cycle-2026 window drops,
#             a NULL-name and a blank-name row, and every other detail section filled
#   500000003 55 'Match' + 5 'Other' of each at $10     500000004 exactly fifty of each at $10
#   500000005 150 of each in two tied levels (75 at $20.00 on 2026-04-04, 75 at $10.00 on 2026-04-03): PostgreSQL may
#             order tied rows differently for LIMIT 50 OFFSET 0 (a bounded heap, 150 > 2 x 50) than for OFFSET 50
# ===========================================================================

ORDER_EIN, LONG_EIN, PAGER_EIN, EXACT_EIN, TIE_EIN = "500000001", "500000002", "500000003", "500000004", "500000005"
FORM_ID = {ORDER_EIN: 501, LONG_EIN: 502, PAGER_EIN: 503, EXACT_EIN: 504, TIE_EIN: 505}
ORG_NAME = {ORDER_EIN: "Order 527 Org", LONG_EIN: "Long 527 Org", PAGER_EIN: "Pager 527 Org", EXACT_EIN: "Exact 527 Org",
            TIE_EIN: "Tie 527 Org"}
PAGES = ("contributors", "recipients")
PAGE_SIZE = 50


def _as_recipient(contributor: str) -> str:
    """The recipient that mirrors a contributor: 'Delta LLC' -> 'Delta Vendor'."""
    return contributor.split()[0] + " Vendor"


def _page_name(page: str, contributor: str) -> str:
    return contributor if page == "contributors" else _as_recipient(contributor)


# Org 500000001, positions A..D (the committee-side ordering table): total, records, first date, last date.
ORDER_GIVERS = [
    ("Zeta Fund", [(500.0, "2026-07-07")]),
    ("Alpha Fund", [(75.0, "2026-01-03"), (75.0, "2026-01-13"), (75.0, "2026-02-03"), (75.0, "2026-03-03")]),
    ("Mu Fund", [(100.0, "2025-12-05"), (50.0, "2026-04-05"), (50.0, "2026-05-05")]),
    ("Beta Fund", [(50.0, "2026-08-08"), (50.0, "2026-09-09")]),
]
ORDER_FIGURES = [
    ("$500.00", 1, date(2026, 7, 7), date(2026, 7, 7)),
    ("$300.00", 4, date(2026, 1, 3), date(2026, 3, 3)),
    ("$200.00", 3, date(2025, 12, 5), date(2026, 5, 5)),
    ("$100.00", 2, date(2026, 8, 8), date(2026, 9, 9)),
]
# (sort, dir) -> positions A=0 B=1 C=2 D=3 in the expected order; all eight orders are distinct.
EXPECTED_POSITIONS = {
    ("total", "desc"): [0, 1, 2, 3],
    ("total", "asc"): [3, 2, 1, 0],
    ("name", "asc"): [1, 3, 2, 0],     # Alpha, Beta, Mu, Zeta
    ("name", "desc"): [0, 2, 3, 1],
    ("count", "desc"): [1, 2, 3, 0],   # 4, 3, 2, 1
    ("count", "asc"): [0, 3, 2, 1],
    ("last", "desc"): [3, 0, 2, 1],    # 09-09, 07-07, 05-05, 03-03
    ("last", "asc"): [1, 2, 0, 3],
}

# Org 500000002: contributor -> its rows.  Recipients are the same rows under _as_recipient(name).
LONG_GIVERS = {
    "Alpha Fund": [(100.0, "2026-05-01"), (100.0, "2026-05-02"), (100.0, "2026-05-03")],
    "Bravo Group": [(400.0, "2026-02-02"), (200.0, "2025-06-06"), (100.0, "2023-03-03")],
    "Charlie Co": [(35.0, "2026-08-08"), (25.0, "2026-08-09")],
    "Delta LLC": [(500.0, "2026-03-03"), (400.0, "2026-03-04")],
    "Echo Union": [(100.0, "2025-12-12"), (50.0, "2025-12-13")],
    "Foxtrot PAC": [(250.0, "2023-01-15"), (250.0, "2023-01-16")],
    "Golf Trust": [(20.0, "2026-09-09"), (10.0, "2026-09-10"), (10.0, "2026-09-11")],
    "Hotel Fund": [(300.0, "2026-04-01"), (250.0, "2026-04-02"), (250.0, "2023-07-07")],
    "India Corp": [(60.0, "2026-06-06"), (40.0, "2026-06-07")],
    "Juliet Inc": [(210.0, "2026-07-07"), (200.0, "2026-07-08"), (200.0, "2026-07-09")],
    "Kilo LLC": [(120.0, "2025-09-09"), (80.0, "2025-09-10")],
    "Lima Assoc": [(150.0, "2026-10-10"), (150.0, "2026-10-11"), (100.0, "2026-10-12")],
}
# Never listed: a NULL name ($1,000.00, 2026-01-01) and a blank name ($77.00, 2026-01-02), contributors and recipients.
LONG_ALL = [   # all time, total descending: name, total, records, first date, last date
    ("Delta LLC", "$900.00", 2, date(2026, 3, 3), date(2026, 3, 4)),         # 500 + 400
    ("Hotel Fund", "$800.00", 3, date(2023, 7, 7), date(2026, 4, 2)),        # 300 + 250 + 250
    ("Bravo Group", "$700.00", 3, date(2023, 3, 3), date(2026, 2, 2)),       # 400 + 200 + 100
    ("Juliet Inc", "$610.00", 3, date(2026, 7, 7), date(2026, 7, 9)),        # 210 + 200 + 200
    ("Foxtrot PAC", "$500.00", 2, date(2023, 1, 15), date(2023, 1, 16)),     # 250 + 250
    ("Lima Assoc", "$400.00", 3, date(2026, 10, 10), date(2026, 10, 12)),    # 150 + 150 + 100
    ("Alpha Fund", "$300.00", 3, date(2026, 5, 1), date(2026, 5, 3)),        # 100 x 3
    ("Kilo LLC", "$200.00", 2, date(2025, 9, 9), date(2025, 9, 10)),         # 120 + 80
    ("Echo Union", "$150.00", 2, date(2025, 12, 12), date(2025, 12, 13)),    # 100 + 50
    ("India Corp", "$100.00", 2, date(2026, 6, 6), date(2026, 6, 7)),        # 60 + 40
    ("Charlie Co", "$60.00", 2, date(2026, 8, 8), date(2026, 8, 9)),         # 35 + 25   <- 11th: the detail stops at 10
    ("Golf Trust", "$40.00", 3, date(2026, 9, 9), date(2026, 9, 11)),        # 20 + 10 + 10   <- 12th
]
LONG_CYCLE = [  # cycle 2026 = 2025-01-01..2026-12-31: Foxtrot (2023 only) is gone; Bravo and Hotel lose their 2023 rows
    ("Delta LLC", "$900.00", 2, date(2026, 3, 3), date(2026, 3, 4)),
    ("Juliet Inc", "$610.00", 3, date(2026, 7, 7), date(2026, 7, 9)),
    ("Bravo Group", "$600.00", 2, date(2025, 6, 6), date(2026, 2, 2)),       # 400 + 200
    ("Hotel Fund", "$550.00", 2, date(2026, 4, 1), date(2026, 4, 2)),        # 300 + 250
    ("Lima Assoc", "$400.00", 3, date(2026, 10, 10), date(2026, 10, 12)),
    ("Alpha Fund", "$300.00", 3, date(2026, 5, 1), date(2026, 5, 3)),
    ("Kilo LLC", "$200.00", 2, date(2025, 9, 9), date(2025, 9, 10)),
    ("Echo Union", "$150.00", 2, date(2025, 12, 12), date(2025, 12, 13)),
    ("India Corp", "$100.00", 2, date(2026, 6, 6), date(2026, 6, 7)),
    ("Charlie Co", "$60.00", 2, date(2026, 8, 8), date(2026, 8, 9)),
    ("Golf Trust", "$40.00", 3, date(2026, 9, 9), date(2026, 9, 11)),        # 11th
]
LONG_SCOPES = [   # query, bar key, scope label, expected table
    ("", "all", "All time", LONG_ALL),
    ("?cycle=2026", "cycle:2026", "2026 Cycle", LONG_CYCLE),
]

# Orgs 500000003 / 500000004: display names per page ('Match Donor01' / 'Match Vendor 01' ...).
PAGER_NAMES = {
    "contributors": {"Match": "Match Donor{:02d}", "Other": "Other Donor{:02d}", "Exact": "Exact Donor{:02d}",
                     "Tie": "Tie Donor{:03d}"},
    "recipients": {"Match": "Match Vendor {:02d}", "Other": "Other Vendor {:02d}", "Exact": "Exact Vendor {:02d}",
                   "Tie": "Tie Vendor {:03d}"},
}


def _pager_names(page: str, family: str, first: int, last: int) -> list[str]:
    return [PAGER_NAMES[page][family].format(i) for i in range(first, last + 1)]


_CONTRIBUTION_SQL = """
    INSERT INTO irs527_contributions (form_id, ein, org_name, contributor_name, city, state, amount, date)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
_EXPENDITURE_SQL = """
    INSERT INTO irs527_expenditures (form_id, ein, org_name, recipient_name, city, state, amount, date, purpose)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'fixture')
"""
PLACES = [("Chicago", "IL"), ("Peoria", "WI")]
TIE_LEVELS = [(10.0, "2026-04-03"), (20.0, "2026-04-04")]   # (amount, day) for odd / even row numbers of org 500000005


def _add_rows(conn, ein, contributors, recipients):
    """contributors / recipients: [(name, amount, day)].  City and state alternate row by row, so a page that groups
    by more than the name splits every giver that has several rows."""
    conn.executemany(_CONTRIBUTION_SQL, [(FORM_ID[ein], ein, ORG_NAME[ein], n, *PLACES[i % 2], a, d)
                                         for i, (n, a, d) in enumerate(contributors)])
    conn.executemany(_EXPENDITURE_SQL, [(FORM_ID[ein], ein, ORG_NAME[ein], n, *PLACES[i % 2], a, d)
                                        for i, (n, a, d) in enumerate(recipients)])


@pytest.fixture
def ledger_app(tmp_path: Path):
    db_path = str(tmp_path / "test_irs527_ledgers.db")
    init_db(db_path)
    conn = get_db(db_path)
    for ein, form_id in FORM_ID.items():
        conn.execute(
            """
            INSERT INTO irs527_organizations (ein, form_id, form_id_seq, org_name, city, state, zip)
            VALUES (?, ?, 0, ?, 'Chicago', 'IL', '60601')
            """,
            (ein, form_id, ORG_NAME[ein]),
        )
    giving = [(name, a, d) for name, rows in ORDER_GIVERS for a, d in rows]
    _add_rows(conn, ORDER_EIN, giving, [(_as_recipient(n), a, d) for n, a, d in giving])
    giving = [(name, a, d) for name, rows in LONG_GIVERS.items() for a, d in rows]
    receiving = [(_as_recipient(n), a, d) for n, a, d in giving]
    junk = [(None, 1000.0, "2026-01-01"), ("   ", 77.0, "2026-01-02")]
    _add_rows(conn, LONG_EIN, giving + junk, receiving + junk)
    day = "2026-04-04"   # every Pager / Exact row: $10.00 on this day (ties on total, records and last date)
    pager = {page: _pager_names(page, "Match", 1, 55) + _pager_names(page, "Other", 1, 5) for page in PAGES}
    exact = {page: _pager_names(page, "Exact", 1, 50) for page in PAGES}
    tied = {page: _pager_names(page, "Tie", 1, 150) for page in PAGES}
    _add_rows(conn, PAGER_EIN, [(n, 10.0, day) for n in pager["contributors"]], [(n, 10.0, day) for n in pager["recipients"]])
    _add_rows(conn, EXACT_EIN, [(n, 10.0, day) for n in exact["contributors"]], [(n, 10.0, day) for n in exact["recipients"]])
    # Two levels only: even numbers $20.00 on 2026-04-04, odd numbers $10.00 on 2026-04-03 (75 rows each).
    _add_rows(conn, TIE_EIN, [(n, *TIE_LEVELS[i % 2]) for i, n in enumerate(tied["contributors"], start=1)],
              [(n, *TIE_LEVELS[i % 2]) for i, n in enumerate(tied["recipients"], start=1)])
    # Every other section of the Long org's detail page, so the order test has all ten headings to place.
    conn.execute(
        "INSERT INTO irs527_directors (form_id, ein, org_name, person_name, title, city, state) "
        "VALUES (502, ?, 'Long 527 Org', 'Dana Director', 'President', 'Chicago', 'IL')", (LONG_EIN,))
    conn.execute(
        "INSERT INTO irs527_related_orgs (form_id, ein, org_name, related_org_name, relationship_type, city, state) "
        "VALUES (502, ?, 'Long 527 Org', 'Related Fund', 'Affiliate', 'Chicago', 'IL')", (LONG_EIN,))
    conn.execute(
        "INSERT INTO irs527_committee_matches (ein, org_name, committee_id_sbe, committee_name, score, method) "
        "VALUES (?, 'Long 527 Org', 100, 'Citizens for Smith', 0.9, 'name')", (LONG_EIN,))
    conn.execute(
        "INSERT INTO irs527_reports (form_id, ein, period_start, period_end, org_name, total_contributions, "
        "total_expenditures) VALUES (502, ?, '2026-01-01', '2026-03-31', 'Long 527 Org', 4760, 4760)", (LONG_EIN,))
    conn.commit()
    conn.close()
    return create_app({'TESTING': True, 'DATABASE_PATH': db_path,
                       'DASHBOARD_PREWARM_ENABLED': False, 'ROUTE_PERF_CACHE_ENABLED': False})


def _ledger(html):
    """[(name, total, records, [first, last])] of the page's main table."""
    found = []
    for row in _list_rows(html):
        money, counts, days = _figures(row)
        found.append((row.name, money[0] if money else None, counts[0] if counts else None, days))
    return found


def _expected_ledger(page, table):
    return [(_page_name(page, name), total, records, [first, last]) for name, total, records, first, last in table]


class TestSeeAllPagesExist:
    def test_org_pages_are_200_and_an_unknown_ein_is_404(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            for ein, wanted in ((LONG_EIN, 200), ("999999999", 404)):
                status = client.get(f"/527/{ein}/{page}").status_code
                if status != wanted:
                    problems.append(f"/527/{ein}/{page}: {status}, wanted {wanted}")
        assert problems == []

    def test_heading_carries_the_windowed_scope_tag_and_the_page_has_one_scope_bar(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            for query, key, label, _ in LONG_SCOPES:
                url = f"/527/{LONG_EIN}/{page}{query}"
                status, html = _fetch(client, url)
                if status != 200:
                    problems.append(f"{url}: status {status}")
                    continue
                windowed = [h for h in _headings(html) if h.tag_class == "scope-tag" and h.tag_text == label]
                if not windowed:
                    problems.append(f"{url}: no <h2> with the windowed tag {label!r}; headings {_headings(html)}")
                bars = html.count('class="time-scope-bar')
                if bars != 1:
                    problems.append(f"{url}: {bars} scope bars, wanted 1")
                if _scope_key(html) != key:
                    problems.append(f"{url}: scope key {_scope_key(html)!r}, wanted {key!r}")
        assert problems == []


class TestSeeAllPageRows:
    def test_every_contributor_and_recipient_is_listed_beyond_the_top_ten_and_the_window_applies(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            for query, key, label, table in LONG_SCOPES:
                url = f"/527/{LONG_EIN}/{page}{query}"
                status, html = _fetch(client, url)
                if status != 200:
                    problems.append(f"{url}: status {status}")
                    continue
                found, wanted = _ledger(html), _expected_ledger(page, table)
                if found != wanted:
                    problems.append(f"{url}:\n    got    {found}\n    wanted {wanted}")
        assert problems == []

    def test_sort_and_dir_reorder_the_rows(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            names = [_page_name(page, name) for name, _ in ORDER_GIVERS]
            for (sort_by, sort_dir), positions in EXPECTED_POSITIONS.items():
                url = f"/527/{ORDER_EIN}/{page}?sort={sort_by}&dir={sort_dir}"
                status, html = _fetch(client, url)
                found = [row.name for row in _list_rows(html)] if status == 200 else f"status {status}"
                wanted = [names[position] for position in positions]
                if found != wanted:
                    problems.append(f"{url}: {found}, wanted {wanted}")
        assert problems == []

    def test_rows_show_total_records_first_and_last_date_in_that_order(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            rows = _list_rows(_page(client, f"/527/{ORDER_EIN}/{page}"))
            names = [_page_name(page, name) for name, _ in ORDER_GIVERS]
            if [row.name for row in rows] != names:
                problems.append(f"{page}: names {[row.name for row in rows]}, wanted {names}")
                continue
            for row, (total, count, first, last) in zip(rows, ORDER_FIGURES):
                money, counts, days = _figures(row)
                if (money, counts, days) != ([total], [count], [first, last]):
                    problems.append(f"{page} {row.name}: {(money, counts, days)}, wanted {([total], [count], [first, last])}")
        assert problems == []

    def test_header_sort_links_reorder_the_rows_and_keep_the_scope(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            names = [_page_name(page, name) for name, _ in ORDER_GIVERS]
            by_key: dict[str, list] = {}
            for href in _header_hrefs(_page(client, f"/527/{ORDER_EIN}/{page}?cycle=2026")):
                query = parse_qs(urlsplit(href).query)
                if "sort" in query:
                    by_key.setdefault(query["sort"][0], []).append((href, (query.get("dir") or [None])[0]))
            missing = {"total", "count", "name", "last"} - set(by_key)
            if missing:
                problems.append(f"{page}: no header sort link for {sorted(missing)}; found {sorted(by_key)}")
            for sort_by in ("total", "count", "name", "last"):
                for href, sort_dir in by_key.get(sort_by, []):
                    status, html = _fetch(client, href)
                    if status != 200:
                        problems.append(f"{page}: {href} answered {status}")
                        continue
                    allowed = [[names[p] for p in EXPECTED_POSITIONS[(sort_by, d)]]
                               for d in ((sort_dir,) if sort_dir else ("asc", "desc"))]
                    found = [row.name for row in _list_rows(html)]
                    if found not in allowed:
                        problems.append(f"{page}: {href} lists {found}, wanted one of {allowed}")
                    if _scope_key(html) != "cycle:2026":
                        problems.append(f"{page}: {href} lost the scope ({_scope_key(html)!r})")
        assert problems == []


class TestSeeAllPagePaging:
    def test_fifty_rows_per_page_and_the_pager_link_keeps_sort_and_scope(self, ledger_app):
        # Org 500000003: 55 'Match' + 5 'Other' at $10.  Name descending: Other 05..01, then Match 55..01.
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            descending = sorted(_pager_names(page, "Match", 1, 55) + _pager_names(page, "Other", 1, 5),
                                key=str.lower, reverse=True)       # Other 05..01, then Match 55..01
            first = _page(client, f"/527/{PAGER_EIN}/{page}?sort=name&dir=desc&cycle=2026")
            found = [row.name for row in _list_rows(first)]
            if found != descending[:PAGE_SIZE]:
                problems.append(f"{page} page 1: {len(found)} rows, {found[:1]}..{found[-1:]}; wanted 50 from {descending[0]}")
            if _page_links(first, 3):
                problems.append(f"{page} page 1 links to a page 3")
            forward = _page_links(first, 2)
            if not forward:
                problems.append(f"{page} page 1 has no link to page 2")
                continue
            status, second = _fetch(client, forward[0])
            if status != 200:
                problems.append(f"{page}: {forward[0]} answered {status}")
                continue
            found = [row.name for row in _list_rows(second)]
            if found != descending[PAGE_SIZE:]:
                problems.append(f"{page} page 2 (via {forward[0]}): {found}, wanted {descending[PAGE_SIZE:]}")
            if _scope_key(second) != "cycle:2026":
                problems.append(f"{page} page 2 lost the scope ({_scope_key(second)!r})")
            if _page_links(second, 3):
                problems.append(f"{page} page 2 links to a page 3")
        assert problems == []

    def test_default_order_pages_never_overlap_or_skip(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            everyone = set(_pager_names(page, "Match", 1, 55) + _pager_names(page, "Other", 1, 5))
            first = [row.name for row in _list_rows(_page(client, f"/527/{PAGER_EIN}/{page}"))]
            second = [row.name for row in _list_rows(_page(client, f"/527/{PAGER_EIN}/{page}?page=2"))]
            if (len(first), len(second)) != (PAGE_SIZE, 10):
                problems.append(f"{page}: page sizes {(len(first), len(second))}, wanted (50, 10)")
            if set(first) & set(second):
                problems.append(f"{page}: pages overlap on {sorted(set(first) & set(second))[:3]}")
            if set(first) | set(second) != everyone:
                problems.append(f"{page}: pages skip {sorted(everyone - set(first) - set(second))[:3]}")
        assert problems == []

    def test_pages_of_tied_rows_never_overlap_or_skip(self, ledger_app):
        # 150 rows in two tied levels.  Without a tie-break PostgreSQL may order the tied rows differently for
        # LIMIT 50 OFFSET 0 (a bounded heap, since 150 > 2 x 50) than for OFFSET 50, so pages repeat and drop rows.
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            everyone = set(_pager_names(page, "Tie", 1, 150))
            for sort_by in ("total", "last"):
                pages = [[row.name for row in _list_rows(_page(client, f"/527/{TIE_EIN}/{page}?sort={sort_by}&page={number}"))]
                         for number in (1, 2, 3)]
                seen = [name for names in pages for name in names]
                if [len(names) for names in pages] != [PAGE_SIZE] * 3:
                    problems.append(f"{page} sort={sort_by}: page sizes {[len(names) for names in pages]}, wanted 50 each")
                if len(seen) != len(set(seen)):
                    problems.append(f"{page} sort={sort_by}: {len(seen) - len(set(seen))} rows appear on two pages")
                if set(seen) != everyone:
                    problems.append(f"{page} sort={sort_by}: {len(everyone - set(seen))} rows appear on no page")
        assert problems == []

    def test_exactly_fifty_rows_have_no_second_page(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            html = _page(client, f"/527/{EXACT_EIN}/{page}")
            if len(_list_rows(html)) != PAGE_SIZE:
                problems.append(f"{page}: {len(_list_rows(html))} rows, wanted 50")
            if _page_links(html, 2):
                problems.append(f"{page}: links to a page 2 that would be empty")
        assert problems == []

    def test_q_filters_case_insensitively_on_the_name_before_paging(self, ledger_app):
        # In name order the first page is Match 01..50, so 'donor5' / 'vendor 5' (Match 50..55) only show up when
        # the filter runs before the paging.
        client = ledger_app.test_client()
        problems = []
        for page, needle in (("contributors", "donor5"), ("recipients", "vendor 5")):
            wanted = _pager_names(page, "Match", 50, 55)
            for typed in (needle, needle.upper()):
                url = f"/527/{PAGER_EIN}/{page}?q={quote(typed)}&sort=name&dir=asc"
                found = [row.name for row in _list_rows(_page(client, url))]
                if found != wanted:
                    problems.append(f"{url}: {found}, wanted {wanted}")
            url = f"/527/{PAGER_EIN}/{page}?q=zzz"
            if _list_rows(_page(client, url)):
                problems.append(f"{url}: rows for a filter nothing matches")
        assert problems == []

    def test_pager_link_keeps_the_filter(self, ledger_app):
        # q=match leaves 55 rows -> page 2 holds Match 51..55 and no 'Other'.
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            first = _page(client, f"/527/{PAGER_EIN}/{page}?q=match&sort=name&dir=asc")
            names = _pager_names(page, "Match", 1, 55)
            if [row.name for row in _list_rows(first)] != names[:PAGE_SIZE]:
                problems.append(f"{page} page 1 is not Match 01..50 in name order")
            forward = _page_links(first, 2)
            if not forward:
                problems.append(f"{page} page 1 has no link to page 2")
                continue
            found = [row.name for row in _list_rows(_page(client, forward[0]))]
            if found != names[PAGE_SIZE:]:
                problems.append(f"{page} page 2 via {forward[0]}: {found}, wanted {names[PAGE_SIZE:]}")
        assert problems == []

    def test_q_does_not_escape_the_scope_window(self, ledger_app):
        # Foxtrot's rows are all 2023: visible all time, invisible in cycle 2026.
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            name = _page_name(page, "Foxtrot PAC")
            everything = [row.name for row in _list_rows(_page(client, f"/527/{LONG_EIN}/{page}?q=foxtrot"))]
            windowed = [row.name for row in _list_rows(_page(client, f"/527/{LONG_EIN}/{page}?q=foxtrot&cycle=2026"))]
            if everything != [name]:
                problems.append(f"{page} all time: {everything}, wanted {[name]}")
            if windowed != []:
                problems.append(f"{page} cycle 2026: {windowed}, wanted no rows")
        assert problems == []

    def test_search_form_submits_the_scope_with_the_filter(self, ledger_app):
        # q=hotel: all time $800 x3; cycle 2026 $550 x2 (300 + 250); 2026-04-02..2026-12-31 only the $250 of 04-02.
        cases = [
            ("?cycle=2026", "cycle:2026", ("$550.00", 2)),
            ("?date_from=2026-04-02&date_to=2026-12-31", "custom", ("$250.00", 1)),
        ]
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            path = f"/527/{LONG_EIN}/{page}"
            for query, key, (total, records) in cases:
                submitted = _form_submission(_page(client, f"{path}{query}"), "hotel", path)
                status, html = _fetch(client, submitted)
                if status != 200:
                    problems.append(f"{submitted}: status {status}")
                    continue
                found = [(row.name, _figures(row)[0], _figures(row)[1]) for row in _list_rows(html)]
                wanted = [(_page_name(page, "Hotel Fund"), [total], [records])]
                if _scope_key(html) != key or found != wanted:
                    problems.append(f"{submitted}: scope {_scope_key(html)!r} rows {found}, wanted {key!r} {wanted}")
        assert problems == []

    def test_odd_page_numbers_filters_and_sorts_never_error(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            control = client.get(f"/527/{ORDER_EIN}/{page}").status_code   # a 404 would pass every check below
            if control != 200:
                problems.append(f"/527/{ORDER_EIN}/{page}: {control}, wanted 200")
            for query in ("?page=0", "?page=-3", "?page=abc", "?page=999999", "?q=%27", "?q=%5C%25_"):
                status = client.get(f"/527/{ORDER_EIN}/{page}{query}").status_code
                if status >= 500:
                    problems.append(f"/527/{ORDER_EIN}/{page}{query}: {status}")
            status, html = _fetch(client, f"/527/{ORDER_EIN}/{page}?sort=bogus_column&dir=sideways")
            if status == 400:    # a 400 is a fair answer too; a 5xx, or a partial list, is not
                continue
            found = sorted(row.name for row in _list_rows(html)) if status == 200 else f"status {status}"
            if found != sorted(_page_name(page, name) for name, _ in ORDER_GIVERS):
                problems.append(f"{page} with an unknown sort: {found}")
        assert problems == []

    def test_a_window_without_rows_renders_an_empty_list_not_a_404(self, ledger_app):
        # The Long org's earliest row is 2023, so cycle 2020 (2019-01-01..2020-12-31) is empty.
        client = ledger_app.test_client()
        problems = []
        for page in PAGES:
            url = f"/527/{LONG_EIN}/{page}?cycle=2020"
            status, html = _fetch(client, url)
            if status != 200:
                problems.append(f"{url}: status {status}")
                continue
            if _list_rows(html):
                problems.append(f"{url}: rows {[row.name for row in _list_rows(html)]}")
            if _scope_key(html) != "cycle:2020":
                problems.append(f"{url}: scope key {_scope_key(html)!r}")
        assert problems == []


class TestDetailPage:
    DETAIL_ORDER = [
        "Organization Details", "Financial Summary", "Filing History", "Matched IL Committees",
        "Directors / Officers", "Related Organizations", "Top Contributors", "Contributions To This Organization",
        "Top Expenditure Recipients", "Expenditures",
    ]

    def test_top_ten_tables_come_first_and_filing_history_last(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for query in ("", "?cycle=2026"):
            names = [h.name for h in _headings(_page(client, f"/527/{LONG_EIN}{query}"))]
            labels = []
            for name in names:
                matching = [label for label in self.DETAIL_ORDER if name.startswith(label)]
                labels.append(matching[0] if matching else name)
            where = {label: labels.index(label) for label in self.DETAIL_ORDER if label in labels}
            if sorted(labels) != sorted(self.DETAIL_ORDER):
                problems.append(f"{query or 'default'}: sections {labels}, wanted each of {self.DETAIL_ORDER} once")
                continue
            if not where["Financial Summary"] < where["Top Contributors"] < where["Top Expenditure Recipients"]:
                problems.append(f"{query or 'default'}: Financial Summary / Top Contributors / Top Expenditure Recipients "
                                f"are not in that order: {labels}")
            rest = [label for label in self.DETAIL_ORDER
                    if label not in ("Organization Details", "Financial Summary", "Top Contributors",
                                     "Top Expenditure Recipients", "Filing History")]
            late = [label for label in rest if where[label] < where["Top Expenditure Recipients"]]
            if late:
                problems.append(f"{query or 'default'}: {late} come before the Top-10 tables: {labels}")
            if labels[-1] != "Filing History":
                problems.append(f"{query or 'default'}: last section is {labels[-1]!r}, wanted Filing History")
        assert problems == []

    def test_see_all_links_count_the_unique_names_and_open_the_list_in_the_same_scope(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for query, key, label, table in LONG_SCOPES:
            html = _page(client, f"/527/{LONG_EIN}{query}")
            for page, heading in (("contributors", "Top Contributors"), ("recipients", "Top Expenditure Recipients")):
                where = f"/527/{LONG_EIN}{query} {heading}"
                section = _section(html, heading)
                links = [(href, text) for href, text in _anchors(section) if re.match(r"See all\s+[\d,]+", text, re.I)]
                if len(links) != 1:
                    problems.append(f"{where}: {len(links)} 'See all N ...' links, wanted 1")
                    continue
                href, text = links[0]
                shown = int(re.match(r"See all\s+([\d,]+)", text, flags=re.I).group(1).replace(",", ""))
                if shown != len(table):
                    problems.append(f"{where}: link says {shown}, wanted {len(table)}")
                if urlsplit(href).path != f"/527/{LONG_EIN}/{page}":
                    problems.append(f"{where}: link goes to {href}")
                    continue
                status, target = _fetch(client, href)
                if status != 200:
                    problems.append(f"{where}: {href} answered {status}")
                    continue
                if _scope_key(target) != key:
                    problems.append(f"{where}: {href} opens in scope {_scope_key(target)!r}, wanted {key!r}")
                if len(_list_rows(target)) != len(table):
                    problems.append(f"{where}: {href} lists {len(_list_rows(target))} rows, wanted {len(table)}")
        assert problems == []

    def test_top_ten_tables_are_the_head_of_the_full_lists(self, ledger_app):
        client = ledger_app.test_client()
        problems = []
        for query, key, label, table in LONG_SCOPES:
            detail = _page(client, f"/527/{LONG_EIN}{query}")
            for page, heading in (("contributors", "Top Contributors"), ("recipients", "Top Expenditure Recipients")):
                shown = [(row.name, _figures(row)[0], _figures(row)[1]) for row in _rows(_section(detail, heading))]
                full = [(row.name, _figures(row)[0], _figures(row)[1])
                        for row in _list_rows(_page(client, f"/527/{LONG_EIN}/{page}{query}"))]
                wanted = [(_page_name(page, name), [total], [records]) for name, total, records, _, _ in table[:10]]
                if shown != wanted:
                    problems.append(f"/527/{LONG_EIN}{query} {heading}: {shown}, wanted {wanted}")
                if full[:10] != shown:
                    problems.append(f"/527/{LONG_EIN}{query} {heading}: not the first ten rows of /{page}: {full[:10]}")
        assert problems == []
