"""Phase 2 — the time-scope bar replaces the active-filter chip on analytics,
federal finance and the live feed (step 2.1); later steps add lists and the
nav/JS removal to this file. Pages render on an empty database, so these
pin structure (one bar, no chip, scope key, tags, links) not amounts."""
import re
from pathlib import Path

import pytest

from database.connection import init_db
from webapp.app import create_app
from webapp.utils.time_scope import current_cycle


@pytest.fixture
def rollout_app(tmp_path: Path):
    db_path = str(tmp_path / "scope_rollout.db")
    init_db(db_path)
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _get(app, url: str) -> str:
    resp = app.test_client().get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp.get_data(as_text=True)


def _subnav(html: str, css_class: str) -> str:
    start = html.find(f'class="{css_class}"')
    assert start != -1, css_class
    return html[start:html.find("</nav>", start)]


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', html)


class TestAnalyticsScope:
    def test_overview_has_one_bar_no_chip_default_current_cycle(self, rollout_app):
        html = _get(rollout_app, "/analytics/")
        assert html.count('class="time-scope-bar') == 1
        assert "active-filter-chip" not in html
        assert f'data-scope-key="cycle:{current_cycle()}"' in html

    def test_months_follow_the_scope_unless_set_explicitly(self, rollout_app):
        # All time is no longer silently cut to 24 months (Appendix A.2 spot 10).
        html = _get(rollout_app, "/analytics/?period=all")
        assert 'data-scope-key="all"' in html
        assert 'name="months" min="1" max="120" value="" placeholder="120"' in html
        # A bounded preset shows the months it spans: Jan 2023 – Dec 2024.
        html = _get(rollout_app, "/analytics/?period=cycle:2024")
        assert 'name="months" min="1" max="120" value="" placeholder="24"' in html
        # An explicit value still wins and is the only months value the subnav carries.
        html = _get(rollout_app, "/analytics/?period=all&months=6")
        assert 'name="months" min="1" max="120" value="6" placeholder="6"' in html
        assert "months=6" in _subnav(html, "analytics-subnav")

    def test_subnav_links_carry_scope_key_never_resolved_dates(self, rollout_app):
        html = _get(rollout_app, "/analytics/?period=cycle:2024")
        subnav = _subnav(html, "analytics-subnav")
        links = [h for h in _hrefs(subnav) if "/analytics/networks" in h]
        assert links and all(("period=cycle%3A2024" in h or "period=cycle:2024" in h) for h in links)
        assert "date_from" not in subnav and "months=" not in subnav
        # A section heading on every analytics page carries the scope tag.
        for path in ("/analytics/", "/analytics/networks", "/analytics/risk", "/analytics/donors",
                     "/analytics/geography", "/analytics/relationships"):
            page = _get(rollout_app, path + "?period=cycle:2024")
            assert page.count('class="time-scope-bar') == 1, path
            assert '<span class="scope-tag">2024 Cycle</span>' in page, path


class TestFederalScope:
    def test_overview_has_one_bar_no_chip_default_current_cycle(self, rollout_app):
        html = _get(rollout_app, "/federal-finance/overview")
        assert html.count('class="time-scope-bar') == 1
        assert "active-filter-chip" not in html
        assert f'data-scope-key="cycle:{current_cycle()}"' in html

    def test_all_time_renders_and_subnav_carries_scope_key(self, rollout_app):
        html = _get(rollout_app, "/federal-finance/candidates?period=all&analysis_office=H")
        assert 'data-scope-key="all"' in html
        subnav = _subnav(html, "federal-subnav")
        links = [h for h in _hrefs(subnav) if "/federal-finance/networks" in h]
        assert links and all("period=all" in h and "analysis_office=H" in h for h in links)
        assert "cycle=" not in subnav and "date_from" not in subnav

    def test_explicit_cycle_param_scopes_every_federal_page(self, rollout_app):
        for path in ("/federal-finance/overview", "/federal-finance/candidates", "/federal-finance/networks",
                     "/federal-finance/money-flow", "/federal-finance/influence",
                     "/federal-finance/follow-the-money", "/federal-finance/geography",
                     "/federal-finance/matching", "/federal-finance/donor-intelligence"):
            page = _get(rollout_app, path + "?cycle=2024")
            assert page.count('class="time-scope-bar') == 1, path
            assert 'data-scope-key="cycle:2024"' in page, path
            assert "active-filter-chip" not in page, path


class TestLiveFeedScope:
    def test_has_one_bar_no_chip_and_tagged_sections(self, rollout_app):
        html = _get(rollout_app, "/live-feed")
        assert html.count('class="time-scope-bar') == 1
        assert "active-filter-chip" not in html
        assert f'data-scope-key="cycle:{current_cycle()}"' in html
        assert f'<h2>Local Candidate Donations (0) <span class="scope-tag">{current_cycle()} Cycle</span></h2>' in html
        html = _get(rollout_app, "/live-feed?period=all")
        assert '<h2>Federal Candidate Donations (0) <span class="scope-tag">All time</span></h2>' in html


def test_legacy_chip_is_gone():
    """Step 2.1 closes with the chip partial and its helper deleted."""
    assert not Path("webapp/templates/_partials/active_filter_chip.html").exists()
    assert "active_filter_chip" not in "".join(
        p.read_text() for p in Path("webapp/templates").rglob("*.html"))
    assert "build_filter_overrides" not in "".join(
        p.read_text() for p in Path("webapp/routes").glob("*.py"))


# ---------------------------------------------------------------------------
# Step 2.2 — list pages + lobbying flows. Seeded fixtures come from the Phase-1
# profile tests so list and detail pages are checked against the same rows.
# ---------------------------------------------------------------------------
from database.connection import get_db  # noqa: E402
from tests.test_profile_scope import donor_app, irs527_app, openbook_app  # noqa: E402,F401

LIST_PAGES = ("/donors/", "/reports/", "/committees/", "/527/", "/527/dark-money",
              "/openbook/", "/lobbying/", "/lobbying/flows")


@pytest.fixture
def lobbying_list_app(tmp_path: Path):
    db_path = str(tmp_path / "lobbying_list_scope.db")
    init_db(db_path)
    conn = get_db(db_path)
    conn.executemany(
        "INSERT INTO lobbying_entities (entity_id, entity_name, reg_year) VALUES (?, ?, ?)",
        [(1, "Current Entity", current_cycle()), (2, "Lapsed Entity", 2022)],
    )
    conn.commit()
    conn.close()
    return create_app({"TESTING": True, "DATABASE_PATH": db_path,
                       "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})


def _hidden_scope_input(html: str, name: str, value: str) -> bool:
    return f'<input type="hidden" name="{name}" value="{value}">' in html


def _page_form(html: str, action: str) -> str:
    """The page's own search form (not the nav search box in base.html)."""
    start = html.find(f'<form action="{action}"')
    assert start != -1, action
    return html[start:html.find("</form>", start)]


class TestListScope:
    def test_every_list_page_has_one_bar_and_defaults_to_current_cycle(self, rollout_app):
        # Also on the empty database: the early-return branches must carry the scope.
        for path in LIST_PAGES:
            html = _get(rollout_app, path)
            assert html.count('class="time-scope-bar') == 1, path
            assert "active-filter-chip" not in html, path
            assert f'data-scope-key="cycle:{current_cycle()}"' in html, path
            assert "time_period" not in html, path

    def test_search_forms_carry_the_scope(self, rollout_app):
        for path in ("/committees/", "/527/", "/openbook/", "/lobbying/"):
            form = _page_form(_get(rollout_app, f"{path}?period=all&q=zz"), path)
            assert _hidden_scope_input(form, "period", "all"), path
            assert 'name="date_from"' not in form, path

    def test_donor_list_totals_and_sort_links_follow_scope(self, donor_app):
        html = _get(donor_app, "/donors/?period=all")
        assert "$15,700.00" in html                       # Alice all time (Phase-1 detail page agrees)
        sort_links = [h for h in _hrefs(html) if "sort=" in h]
        assert sort_links and all("period=all" in h for h in sort_links)
        html = _get(donor_app, "/donors/?period=cycle:2024")
        assert 'data-scope-key="cycle:2024"' in html
        assert "$700.00" in html and "$15,700.00" not in html

    def test_reports_list_filters_by_filed_date_and_links_keep_scope(self, donor_app):
        html = _get(donor_app, "/reports/")                  # filed 2025-07-01 is in the current cycle
        assert 'href="/reports/1"' in html
        html = _get(donor_app, "/reports/?period=cycle:2024")
        assert 'href="/reports/1"' not in html and "No reports found." in html
        sort_links = [h for h in _hrefs(html) if "sort=" in h]
        assert sort_links and all("period=cycle" in h for h in sort_links)
        assert all("date_from" not in h for h in sort_links)

    def test_committees_list_isbe_totals_follow_scope(self, donor_app):
        # Committee 200: $10,000 on 2025-01-20 (current cycle) + $700 on 2023-03-01.
        html = _get(donor_app, "/committees/?period=cycle:2024")
        assert 'data-scope-key="cycle:2024"' in html
        row = html[html.find("/committees/sbe/200"):]
        assert "$700.00" in row[:1500] and "$10,000.00" not in row[:1500]
        html = _get(donor_app, "/committees/?period=cycle:2026&sort=total_contributions&dir=desc")
        row = html[html.find("/committees/sbe/200"):]
        assert "$10,000.00" in row[:1500]
        # Committee 100 excludes its archived $999 row: 5,000 + 2,500 + 25,000.
        row = html[html.find("/committees/sbe/100"):]
        assert "$32,500.00" in row[:1500]

    def test_527_list_windowed_totals_do_not_fall_back_to_report_totals(self, irs527_app):
        # Report totals (12,345 / 6,789) are all-time; a window must use the dated rows.
        html = _get(irs527_app, "/527/?period=cycle:2026")
        assert 'data-scope-key="cycle:2026"' in html
        assert "$4,321.00" in html and "$5,000.00" in html
        assert "$12,345.00" not in html and "$6,789.00" not in html
        html = _get(irs527_app, "/527/?period=all")
        assert "$12,345.00" in html                       # all time keeps the report totals

    def test_openbook_list_carries_scope_and_narrows(self, openbook_app):
        # E7 (plan profiles-races-dated-widgets): the list renders contribution COUNTS only -- the "Contributions" stat card and
        # the vendor row's last cell -- never amounts, so the old `"$5,000.00" not in html` could not fail. The fixture holds
        # one contribution in the 2026 cycle (2026-03-15, $5,000) and one in the 2024 cycle (2023-02-01, $100).
        def contribution_counts(query):
            html = _get(openbook_app, f"/openbook/?{query}")
            card = re.search(r'<h3>Contributions(?:\s*<span class="scope-tag[^"]*">[^<]*</span>)?</h3>\s*<p class="stat-value">(\d+)</p>', html)
            cells = re.findall(r"<td>(\d+)</td>", html[html.index("<tbody>"):])  # seed links, contracts, contributions
            assert card and cells, query
            return html, card.group(1), cells[-1]

        html, card, cell = contribution_counts("period=all")
        assert "CONTRIB VENDOR" in html
        assert _hidden_scope_input(html, "period", "all")
        assert (card, cell) == ("2", "2")      # both contributions
        html, card, cell = contribution_counts("period=cycle:2024")
        assert 'data-scope-key="cycle:2024"' in html
        assert (card, cell) == ("1", "1")      # only the 2023 one; an ignored window shows "2"
        html, card, cell = contribution_counts("period=cycle:2026")
        assert (card, cell) == ("1", "1")      # only the 2026 one
        html, card, cell = contribution_counts("period=cycle:2022")
        assert "CONTRIB VENDOR" in html        # the vendor still lists ...
        assert (card, cell) == ("0", "0")      # ... with none: a page stuck on any one cycle would still show "1" or "2"

    def test_lobbying_list_filters_by_registration_year(self, lobbying_list_app):
        html = _get(lobbying_list_app, "/lobbying/")
        assert "Current Entity" in html and "Lapsed Entity" not in html
        assert "registration year" in html                # the bar's snap note
        html = _get(lobbying_list_app, "/lobbying/?period=all")
        assert "Current Entity" in html and "Lapsed Entity" in html
        html = _get(lobbying_list_app, "/lobbying/?period=cycle:2022")
        assert "Lapsed Entity" in html and "Current Entity" not in html

    def test_flows_page_forwards_scope_to_its_data_fetch(self, rollout_app):
        html = _get(rollout_app, "/lobbying/flows?period=cycle:2024")
        assert 'data-scope-key="cycle:2024"' in html
        assert re.search(r"/lobbying/flows/data\?period=cycle(?::|%3A)2024", html), "data URL must carry the scope"
        assert _get(rollout_app, "/lobbying/flows/data?period=cycle:2024").startswith("{")


# ---------------------------------------------------------------------------
# Step 2.3/2.4 — homepage, candidates landing, viz lab, D2 reconciliation and
# the JSON API leave the legacy global period (the last `time_period` readers,
# so the nav dropdown + JS rewriter can go).
# ---------------------------------------------------------------------------
class TestHomeScope:
    def test_home_and_candidates_have_one_bar_default_current_cycle(self, rollout_app):
        for path in ("/", "/candidates"):
            html = _get(rollout_app, path)
            assert html.count('class="time-scope-bar') == 1, path
            assert "period-badge" not in html, path
            assert f'data-scope-key="cycle:{current_cycle()}"' in html, path
        assert 'data-scope-key="all"' in _get(rollout_app, "/?period=all")

    def test_home_top_donors_follow_scope(self, donor_app):
        html = _get(donor_app, "/?period=cycle:2024")
        assert 'data-scope-key="cycle:2024"' in html
        assert "$700.00" in html and "$15,700.00" not in html
        assert "$15,700.00" in _get(donor_app, "/?period=all")


class TestSecondaryPagesScope:
    @pytest.fixture
    def viz_app(self, tmp_path: Path):
        db_path = str(tmp_path / "viz_scope.db")
        init_db(db_path)
        return create_app({"TESTING": True, "DATABASE_PATH": db_path, "EXPERIMENTAL_VIZ_LAB_ENABLED": True,
                           "DASHBOARD_PREWARM_ENABLED": False, "ROUTE_PERF_CACHE_ENABLED": False})

    def test_viz_lab_has_bar_and_no_legacy_period_line(self, viz_app):
        html = _get(viz_app, "/experimental/viz-lab?period=all")
        assert html.count('class="time-scope-bar') == 1
        assert 'data-scope-key="all"' in html
        assert "Current period:" not in html

    def test_d2_reconciliation_pages_have_bar(self, rollout_app):
        for path in ("/d2-reconciliation/", "/d2-expenditures-reconciliation/"):
            html = _get(rollout_app, path)
            assert html.count('class="time-scope-bar') == 1, path
            assert f'data-scope-key="cycle:{current_cycle()}"' in html, path

    def test_no_legacy_period_readers_remain_in_these_routes(self):
        for name in ("main", "api", "experimental", "d2_receipts_recon", "d2_expenditures_recon"):
            src = Path(f"webapp/routes/{name}.py").read_text()
            assert "time_filter" not in src, name
