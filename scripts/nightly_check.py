#!/usr/bin/env python3
"""The 05:00 check of the nightly data refresh (systemd ilcf-nightly-check.timer).

Finds the nightly run (data_refresh_runs, kind 'nightly') that started on the given America/Chicago
date, stores its diff if the run never finished, and writes <report-dir>/<YYYY-MM-DD>.md with the
run, the per-step minutes from the chain log's STEP<N>_START / STEP<N>_END lines, what changed per
table, the freshness gate and the status of every dataset (manual ones included).

The check is ok only when the run finished ok AND the sources the nightly chain refreshes (ISBE
filings, FEC Schedule A) pass their data_freshness thresholds: a chain that exits 0 without
advancing data (the FEC silent no-op) fails here. A run still going is check_status 'running', not
failed; scripts/nightly_refresh.sh runs this check again when the run finishes, overwriting the
report and check_status. Earlier runs that never finished (a killed wrapper) are noted in the
report. Exits 1 unless the check is ok, so `systemctl --failed` shows it.

Usage:
  python3 scripts/nightly_check.py [--report-dir DIR] [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from database.connection import get_db  # noqa: E402
from database.data_freshness import check_freshness, format_report  # noqa: E402
from database.data_refresh import (  # noqa: E402
    DATASETS,
    dataset_status,
    diff_snapshots,
    record_check,
    snapshot_counts,
)

CHICAGO = ZoneInfo("America/Chicago")
DEFAULT_REPORT_DIR = "/srv/illinois_campaign_finance/shared/refresh_runs"
_GATE_TABLES = {d.table for d in DATASETS if d.nightly}

_LOG_LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.*)$")
_STEP_RE = re.compile(r"^STEP(\d+[A-Z]?)_(START|END|SKIPPED|WARN)\b\s*(\S*)")
_STEP_FAILED_RE = re.compile(r"^STEP_FAILED (\S+)")


def parse_chain_log(text: str) -> list[dict]:
    """Steps in log order: step, name, status (ok / warn / failed / skipped / open), minutes."""
    steps: dict[str, dict] = {}
    for line in text.splitlines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        message = m.group(2)
        s = _STEP_RE.match(message)
        if s:
            step, event, name = s.groups()
            entry = steps.setdefault(step, {"step": step, "name": name, "status": "open", "start": None, "end": None})
            if event == "START":
                entry.update(name=name, start=ts)
            elif event == "END":
                entry["end"] = ts
                if entry["status"] == "open":
                    entry["status"] = "ok"
            elif event == "WARN":
                entry["status"] = "warn"
            else:
                entry.update(name=name, status="skipped")
            continue
        f = _STEP_FAILED_RE.match(message)
        if f:
            open_steps = [e for e in steps.values() if e["status"] == "open"]
            if open_steps:
                open_steps[-1].update(status="failed", end=ts)
            else:
                steps[f.group(1)] = {"step": "?", "name": f.group(1), "status": "failed", "start": None, "end": ts}
    result = []
    for entry in steps.values():
        minutes = None
        if entry["start"] and entry["end"]:
            minutes = round((entry["end"] - entry["start"]).total_seconds() / 60, 1)
        result.append({"step": entry["step"], "name": entry["name"], "status": entry["status"], "minutes": minutes})
    return result


def find_run(conn, day: date):
    return conn.execute(
        "SELECT * FROM data_refresh_runs WHERE kind = 'nightly' "
        "AND CAST(started_at AT TIME ZONE 'America/Chicago' AS DATE) = ? ORDER BY started_at DESC LIMIT 1",
        (day,),
    ).fetchone()


def _stuck_runs(conn, day: date):
    """Nightly runs from before ``day`` still marked running: their wrapper died."""
    return conn.execute(
        "SELECT id, started_at FROM data_refresh_runs WHERE status = 'running' "
        "AND CAST(started_at AT TIME ZONE 'America/Chicago' AS DATE) < ? ORDER BY started_at",
        (day,),
    ).fetchall()


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        return value.astimezone(CHICAGO).strftime("%Y-%m-%d %H:%M") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render_report(day, check, problems, run, steps, diff, freshness, statuses, notes=()) -> str:
    lines = [f"# Nightly refresh check, {day.isoformat()} (America/Chicago)", "", f"**Check: {check.upper()}**", ""]
    if problems:
        lines += [f"- {p}" for p in problems] + [""]
    if notes:
        lines += ["Notes:", ""] + [f"- {n}" for n in notes] + [""]
    if run is not None:
        minutes = None
        if run["finished_at"]:
            minutes = round((run["finished_at"] - run["started_at"]).total_seconds() / 60, 1)
        lines += [
            "## Run", "",
            "| Run | Kind | Status | Exit code | Started | Finished | Minutes | Log |",
            "|---|---|---|---|---|---|---|---|",
            f"| {run['id']} | {run['kind']} | {run['status']} | {_fmt(run['exit_code'])} | {_fmt(run['started_at'])} "
            f"| {_fmt(run['finished_at'])} | {_fmt(minutes)} | {_fmt(run['log_path'])} |", "",
        ]
    if steps:
        lines += ["## Steps (chain log)", "", "| Step | Name | Status | Minutes |", "|---|---|---|---|"]
        lines += [f"| {s['step']} | {s['name']} | {s['status']} | {_fmt(s['minutes'])} |" for s in steps] + [""]
    if diff:
        lines += [
            "## What changed", "",
            "| Table | Rows before | Rows after | Rows added | Latest before | Latest after |",
            "|---|---|---|---|---|---|",
        ]
        order = [t for d in DATASETS for t, _ in d.tables]  # JSONB returns keys in its own order
        for table in sorted(diff, key=lambda t: order.index(t) if t in order else len(order)):
            d = diff[table]
            added = d["rows_added"]
            lines.append(
                f"| {table} | {_fmt(d['rows_before'])} | {_fmt(d['rows_after'])} | {added:+,} "
                f"| {_fmt(d['max_date_before'])} | {_fmt(d['max_date_after'])}{' (new)' if d['new_max_date'] else ''} |"
            )
        lines.append("")
    lines += ["## Freshness", "", "```text", format_report(freshness, as_of=day), "```", ""]
    lines += [
        "## Datasets", "",
        "| Dataset | Automation | Cadence | Latest data | Last load | Age (days) | Stale |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in statuses:
        lines.append(
            f"| {r['label']} | {r['automation']} | {r['cadence']} | {_fmt(r['last_data_date'])} "
            f"| {_fmt(r['last_load'])} | {_fmt(r['age_days'])} | {_fmt(r['stale'])} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--date", help="America/Chicago date of the run to check (default: today there)")
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date) if args.date else datetime.now(CHICAGO).date()

    conn = get_db()
    try:
        run = find_run(conn, day)
        problems: list[str] = []
        diff = None
        steps: list[dict] = []
        if run is None:
            check = "missing"
            problems.append(f"no nightly run started on {day.isoformat()} (America/Chicago)")
        else:
            diff = run["diff"]
            if diff is None:  # killed or still running: measure what it changed so far
                diff = diff_snapshots(run["snapshot_before"] or {}, snapshot_counts(conn))
                conn.execute("UPDATE data_refresh_runs SET diff = CAST(? AS JSONB) WHERE id = ?", (json.dumps(diff), run["id"]))
                conn.commit()
            if run["log_path"] and Path(run["log_path"]).is_file():
                steps = parse_chain_log(Path(run["log_path"]).read_text(errors="replace"))
                problems += [f"step {s['step']} {s['name']} failed" for s in steps if s["status"] == "failed"]
            if run["status"] == "running":
                open_steps = [s for s in steps if s["status"] == "open"]
                at_step = f", step {open_steps[-1]['step']} {open_steps[-1]['name']}" if open_steps else ""
                problems.insert(0, f"still running at {datetime.now(CHICAGO).strftime('%H:%M')} "
                                   f"(started {_fmt(run['started_at'])}{at_step}); the final report follows when it ends")
            elif run["status"] != "ok":
                problems.append(f"the run failed (exit code {run['exit_code']})")
        freshness = check_freshness(conn, as_of=day)
        problems += [
            f"{r.spec.label} is {r.status.upper()} (latest {_fmt(r.max_date)}, limit {r.spec.max_age_days} days)"
            for r in freshness if r.spec.table in _GATE_TABLES and not r.ok
        ]
        notes = [f"run {r['id']} started {_fmt(r['started_at'])} never finished: abandoned (its wrapper died; set its status by hand)"
                 for r in _stuck_runs(conn, day)]
        if run is not None:
            check = "running" if run["status"] == "running" else ("failed" if problems else "ok")
            record_check(conn, run["id"], check, notes="; ".join(problems) or None)
            run = find_run(conn, day)
        statuses = dataset_status(conn, as_of=day)
    finally:
        conn.close()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{day.isoformat()}.md"
    report_path.write_text(render_report(day, check, problems, run, steps, diff, freshness, statuses, notes))
    print(f"nightly check {day.isoformat()}: {check.upper()} ({'; '.join(problems) or 'no problems'}) report={report_path}")
    return 0 if check == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
