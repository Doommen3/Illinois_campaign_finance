"""Public /data-status: every loaded dataset with its age, and the last nightly refresh runs.

Read-only and small (one MAX per dataset plus two short queries), so no RouteCache; the edge-cache
header comes from the app-wide after_request hook like every anonymous page. Data and semantics:
database/data_refresh.py; operations: docs/runbooks/scheduled_refresh.md.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, render_template

from database.data_refresh import dataset_status, recent_runs

data_status_bp = Blueprint("data_status", __name__)

_CHICAGO = ZoneInfo("America/Chicago")


def _chicago(value):
    return value.astimezone(_CHICAGO).strftime("%Y-%m-%d %H:%M") if value is not None else None


@data_status_bp.route("/data-status")
def index():
    conn = current_app.get_database()
    datasets = dataset_status(conn)
    for row in datasets:
        row["last_load_local"] = _chicago(row["last_load"])
    runs = recent_runs(conn, limit=14)
    for run in runs:
        run["started_local"] = _chicago(run["started_at"])
    return render_template("data_status.html", datasets=datasets, runs=runs)
