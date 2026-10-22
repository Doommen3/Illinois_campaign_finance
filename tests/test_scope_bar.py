"""Step 0.2 — the time-scope bar partial and the scope_tag macro render the
resolved window, keep non-time filters in preset links, and reset pagination."""
from datetime import date
from pathlib import Path

import pytest
from flask import render_template_string

from database.connection import init_db
from webapp.app import create_app
from webapp.utils.time_scope import TimeScope


@pytest.fixture
def bar_app(tmp_path: Path):
    db_path = str(tmp_path / "scope_bar.db")
    init_db(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": db_path,
            "DASHBOARD_PREWARM_ENABLED": False,
            "ROUTE_PERF_CACHE_ENABLED": False,
        }
    )

    @app.route("/_scope_bar_probe")
    def _probe():
        return render_template_string(
            '{% from "_partials/macros.html" import scope_tag with context %}'
            '{% include "_partials/time_scope_bar.html" %}'
            "<h2>Receipts {{ scope_tag() }}</h2><h2>Officers {{ scope_tag('All time') }}</h2>",
            time_scope=app.config["_PROBE_SCOPE"],
            scope_default_key="cycle:2026",
            time_scope_source_label="ISBE",
        )

    return app


def _preset_scope(**over) -> TimeScope:
    base = dict(
        key="cycle:2026", start=date(2025, 1, 1), end=None, axis="received", granularity="day",
        data_through=date(2026, 10, 21), is_default=True, is_custom=False, label="2026 Cycle",
        window_label="Jan 1, 2025 – Oct 21, 2026", axis_label="by receipt date", snap_note="",
    )
    base.update(over)
    return TimeScope(**base)


def test_bar_shows_resolved_window_axis_and_data_through(bar_app):
    bar_app.config["_PROBE_SCOPE"] = _preset_scope()
    html = bar_app.test_client().get("/_scope_bar_probe?q=smith&sort=amount&page=3").get_data(as_text=True)
    assert html.count('class="time-scope-bar') == 1
    assert "2026 Cycle" in html
    assert "Jan 1, 2025 – Oct 21, 2026" in html
    assert "by receipt date" in html
    assert "latest ISBE data: Oct 21, 2026" in html
    # Default scope: no reset link; the matching preset is marked active.
    assert "time-scope-reset" not in html
    assert 'class="time-scope-preset active" href="/_scope_bar_probe?q=smith&amp;sort=amount&amp;period=cycle%3A2026"' in html


def test_preset_links_keep_filters_and_drop_time_params_and_page(bar_app):
    bar_app.config["_PROBE_SCOPE"] = _preset_scope()
    html = bar_app.test_client().get(
        "/_scope_bar_probe?q=smith&page=3&date_from=2025-02-01&cycle=2024&year=2024"
    ).get_data(as_text=True)
    assert 'href="/_scope_bar_probe?q=smith&amp;period=all"' in html
    assert "page=3" not in html
    assert "cycle=2024" not in html
    assert "year=2024" not in html
    # The custom form keeps q as a hidden input and never carries page/time params.
    assert '<input type="hidden" name="q" value="smith">' in html
    assert 'name="page"' not in html
    assert 'name="period"' not in html


def test_custom_scope_is_amber_prefilled_and_resettable(bar_app):
    bar_app.config["_PROBE_SCOPE"] = _preset_scope(
        key="custom", start=date(2025, 3, 1), end=date(2025, 9, 30), is_default=False, is_custom=True,
        label="Custom range", window_label="Mar 1, 2025 – Sep 30, 2025",
    )
    html = bar_app.test_client().get("/_scope_bar_probe?date_from=2025-03-01&date_to=2025-09-30").get_data(as_text=True)
    assert "time-scope-bar-custom" in html
    assert 'name="date_from" value="2025-03-01"' in html
    assert 'name="date_to" value="2025-09-30"' in html
    assert "<details class=\"time-scope-custom\" open>" in html
    assert 'class="time-scope-reset" href="/_scope_bar_probe?period=cycle%3A2026"' in html
    assert "time-scope-preset active" not in html


def test_snap_note_and_scope_tags(bar_app):
    bar_app.config["_PROBE_SCOPE"] = _preset_scope(
        axis="reg_year", granularity="year", axis_label="by registration year",
        snap_note="shown as registration years 2025–2026",
    )
    html = bar_app.test_client().get("/_scope_bar_probe").get_data(as_text=True)
    assert "shown as registration years 2025–2026" in html
    assert '<h2>Receipts <span class="scope-tag">2026 Cycle</span></h2>' in html
    assert '<h2>Officers <span class="scope-tag scope-tag-fixed">All time</span></h2>' in html


# ---------------------------------------------------------------------------
# Step 2.3 — the nav "Period:" dropdown and the JS link rewriter are gone (D1);
# top-nav clicks reset the scope per section, only drill-down links carry it (D2).
# ---------------------------------------------------------------------------
import re  # noqa: E402


def _site_nav(html: str) -> str:
    for tag in ("nav", "header"):
        start = html.find(f"<{tag}")
        if start != -1:
            return html[start:html.find(f"</{tag}>", start)]
    raise AssertionError("no site nav found")


def test_topnav_links_do_not_carry_scope(bar_app):
    html = bar_app.test_client().get("/about?period=all").get_data(as_text=True)
    hrefs = re.findall(r'href="([^"]+)"', _site_nav(html))
    assert hrefs
    assert all("period=" not in h and "date_from" not in h for h in hrefs), hrefs


def test_nav_has_no_period_select_or_rewriter(bar_app):
    html = bar_app.test_client().get("/about").get_data(as_text=True)
    assert "time-period-select" not in html
    assert "applyTimePeriod" not in html and "ensurePeriodOnNavigation" not in html
    assert '<input type="hidden" name="period"' not in html      # nav search box no longer pins a period
