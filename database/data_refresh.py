"""Scheduled data refresh: the dataset inventory, the run record and the data status.

The nightly timer (``scripts/nightly_refresh.sh``) records each run in ``data_refresh_runs`` with a
snapshot of every source table before and after (row count + latest natural date) and their diff;
the 05:00 check (``scripts/nightly_check.py``) marks the run checked. Datasets without an automated
download are loaded by hand (``scripts/manual_load.sh``), recorded in ``data_manual_loads``.
``dataset_status`` joins both into the per-dataset age shown on ``/data-status``.

Snapshot row counts are raw table counts (archived ISBE rows included): they measure what a load
changed, never money. ISBE tables are dropped and reloaded by ``sunshine-import``, so a table's
``rows_added`` can be negative.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from database.data_freshness import DEFAULT_SPECS, _coerce_date, _table_exists

# A text date column filtered to well-formed ISO or compact values and normalized to compact
# YYYYMMDD, so MAX compares like with like (IRS 527 text columns hold malformed values on prod).
_TEXT_DATE_RE = "^(19|20)[0-9]{2}-?(0[1-9]|1[0-2])-?(0[1-9]|[12][0-9]|3[01])$"


def _clean_text_date(column: str) -> str:
    return f"CASE WHEN {column} ~ '{_TEXT_DATE_RE}' THEN REPLACE({column}, '-', '') END"


_MAX_AGE_BY_TABLE = {spec.table: spec.max_age_days for spec in DEFAULT_SPECS}


@dataclass(frozen=True)
class Dataset:
    """One loaded source. ``tables`` are (table, natural-date SQL expression); the first is the
    one whose latest date is the dataset's age. ``automation`` is auto (a timer or the nightly
    chain loads it) or manual (Devin downloads a file or starts the load)."""

    key: str
    label: str
    automation: str
    cadence: str
    loader: str
    tables: tuple[tuple[str, str], ...]
    folder: str | None = None  # local drop folder for hand-downloaded files (mirrored to shared/manual/<key>/)
    nightly: bool = False  # refreshed by the nightly chain: an ok run counts as its last load
    notes: str | None = None

    @property
    def table(self) -> str:
        return self.tables[0][0]

    @property
    def max_age_days(self) -> int | None:
        """Staleness threshold from ``database/data_freshness.py`` where one exists."""
        return _MAX_AGE_BY_TABLE.get(self.table)


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "isbe", "ISBE state campaign finance", "auto", "nightly 03:00 Central",
        "sunshine-import --download (scripts/refresh_prod_chain.sh)",
        (("isbe_filed_docs", "received_datetime"), ("isbe_receipts", "received_date"),
         ("isbe_expenditures", "expended_date")),
        nightly=True,
    ),
    Dataset(
        "fec_a", "FEC Schedule A (contributions)", "auto", "nightly 03:00 Central + Sundays 03:00 UTC",
        "sync-fec-il-federal --refresh-cache",
        (("fec_schedule_a_contributions", "contribution_receipt_date"),),
        nightly=True,
    ),
    Dataset(
        "fec_b", "FEC Schedule B (disbursements)", "auto", "weekly, Wednesday nightly run",
        "scripts/fec-schedule-b-catchup.sh (backfill-fec-schedule-b --refresh-cache)",
        (("fec_schedule_b_disbursements", "disbursement_date"),),
    ),
    Dataset(
        "fec_e", "FEC Schedule E (independent expenditures)", "auto", "hourly",
        "backfill-fec-schedule-e (scripts/fec-schedule-e-catchup.sh)",
        (("fec_schedule_e_independent_expenditures", "expenditure_date"),),
    ),
    Dataset(
        "chicago", "City of Chicago contracts, payments and lobbying", "auto",
        "nightly 03:00 Central (needs SOCRATA_APP_TOKEN)",
        'import-chicago-phase1 --app-token "$SOCRATA_APP_TOKEN" --upsert',
        (("chicago_payments_raw", "check_date"), ("chicago_contracts_raw", "approval_date"),
         ("chicago_lobbyist_contributions_raw", "contribution_date"),
         ("chicago_lobbying_activity_raw", "period_end")),
        nightly=True,
    ),
    Dataset(
        "lobbying", "IL Secretary of State lobbying", "manual", "weekly",
        "import-lobbying --file <extract.csv>",
        (("lobbying_lobbyist_registrations", "created_at"),),
        folder="data/manual/lobbying",
        notes="The extract has no per-row date: the date is when the newest registration first loaded.",
    ),
    Dataset(
        "irs527", "IRS 527 political organizations", "manual", "monthly (the IRS rebuilds the file every Saturday)",
        "import-irs527 --file <FullDataFile.txt> --illinois-only",
        (("irs527_reports", _clean_text_date("period_end")), ("irs527_expenditures", _clean_text_date("date"))),
        folder="data/manual/irs527",
        notes=("Download scriptable weekly; import blocked on loader idempotency "
               "(docs/runbooks/irs527_data.md P-3: a re-run doubles five tables)."),
    ),
    Dataset(
        "openbook", "IL Comptroller OpenBook contracts", "manual", "as needed",
        "import-openbook-batch --generate-seeds",
        (("openbook_scrape_runs", "completed_at"), ("openbook_contracts_raw", "last_seen_at")),
        folder="data/manual/openbook",
        notes="A scrape, not a file: the date is the last completed scrape run.",
    ),
)

_DATASET_KEYS = {d.key for d in DATASETS}


def _to_date(value) -> date | None:
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value.strip()):
        try:
            return datetime.strptime(value.strip(), "%Y%m%d").date()
        except ValueError:
            return None
    return _coerce_date(value)


def _max_date(conn, table: str, expr: str) -> date | None:
    # Table names and expressions come from DATASETS above, not user input.
    row = conn.execute(f"SELECT MAX({expr}) FROM {table}").fetchone()
    return _to_date(row[0])


def snapshot_counts(conn) -> dict[str, dict]:
    """``{table: {"rows": n, "max_date": "YYYY-MM-DD" | None}}`` for every DATASETS table; a
    missing table has ``rows`` None."""
    snap: dict[str, dict] = {}
    for dataset in DATASETS:
        for table, expr in dataset.tables:
            if not _table_exists(conn, table):
                snap[table] = {"rows": None, "max_date": None}
                continue
            row = conn.execute(f"SELECT COUNT(*), MAX({expr}) FROM {table}").fetchone()
            latest = _to_date(row[1])
            snap[table] = {"rows": int(row[0] or 0), "max_date": latest.isoformat() if latest else None}
    return snap


def diff_snapshots(before: dict, after: dict) -> dict[str, dict]:
    """Per table: rows before/after, rows added (negative when a reload shrank it), the latest
    date before/after and whether it advanced."""
    diff: dict[str, dict] = {}
    for table in sorted(set(before) | set(after)):
        b = before.get(table) or {}
        a = after.get(table) or {}
        rows_before, rows_after = b.get("rows"), a.get("rows")
        max_before, max_after = b.get("max_date"), a.get("max_date")
        diff[table] = {
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_added": (rows_after or 0) - (rows_before or 0),
            "max_date_before": max_before,
            "max_date_after": max_after,
            "new_max_date": bool(max_after) and (max_before is None or max_after > max_before),
        }
    return diff


def start_run(conn, kind: str = "nightly", log_path: str | None = None) -> int:
    """Open a run (status running) with the before snapshot; returns its id."""
    row = conn.execute(
        "INSERT INTO data_refresh_runs (kind, log_path, snapshot_before) VALUES (?, ?, CAST(? AS JSONB)) RETURNING id",
        (kind, log_path, json.dumps(snapshot_counts(conn))),
    ).fetchone()
    conn.commit()
    return int(row[0])


def finish_run(conn, run_id: int, exit_code: int) -> None:
    """Close a run: ok on exit 0, failed otherwise, with the after snapshot and the diff."""
    row = conn.execute("SELECT snapshot_before FROM data_refresh_runs WHERE id = ?", (run_id,)).fetchone()
    before = (row[0] if row else None) or {}
    after = snapshot_counts(conn)
    conn.execute(
        "UPDATE data_refresh_runs SET finished_at = now(), status = ?, exit_code = ?, "
        "snapshot_after = CAST(? AS JSONB), diff = CAST(? AS JSONB) WHERE id = ?",
        ("ok" if exit_code == 0 else "failed", exit_code, json.dumps(after),
         json.dumps(diff_snapshots(before, after)), run_id),
    )
    conn.commit()


def record_check(conn, run_id: int, check_status: str, notes: str | None = None) -> None:
    conn.execute(
        "UPDATE data_refresh_runs SET check_status = ?, checked_at = now(), notes = COALESCE(?, notes) WHERE id = ?",
        (check_status, notes, run_id),
    )
    conn.commit()


def record_manual_load(
    conn,
    dataset_key: str,
    source_file: str,
    file_date: date | None = None,
    rows_loaded: int | None = None,
    notes: str | None = None,
) -> int:
    if dataset_key not in _DATASET_KEYS:
        raise ValueError(f"unknown dataset {dataset_key!r}; expected one of {sorted(_DATASET_KEYS)}")
    row = conn.execute(
        "INSERT INTO data_manual_loads (dataset_key, source_file, file_date, rows_loaded, notes) "
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (dataset_key, source_file, file_date, rows_loaded, notes),
    ).fetchone()
    conn.commit()
    return int(row[0])


def dataset_status(conn, as_of: date | None = None) -> list[dict]:
    """One row per DATASETS entry: latest data date, last load, age in days and a stale flag
    (None where ``data_freshness`` has no threshold). Reads MAX of the first table only, never a
    full count, so it is cheap enough for a page. Missing tables (fresh DB) read as absent."""
    as_of = as_of or date.today()

    manual_loads: dict[str, datetime] = {}
    if _table_exists(conn, "data_manual_loads"):
        for row in conn.execute("SELECT dataset_key, MAX(loaded_at) FROM data_manual_loads GROUP BY dataset_key").fetchall():
            manual_loads[row[0]] = row[1]
    last_ok_run = None
    if _table_exists(conn, "data_refresh_runs"):
        last_ok_run = conn.execute("SELECT MAX(finished_at) FROM data_refresh_runs WHERE status = 'ok'").fetchone()[0]

    rows = []
    for dataset in DATASETS:
        table, expr = dataset.tables[0]
        present = _table_exists(conn, table)
        last_data_date = _max_date(conn, table, expr) if present else None
        loads = [t for t in (manual_loads.get(dataset.key), last_ok_run if dataset.nightly else None) if t]
        last_load = max(loads) if loads else None
        basis = last_data_date or (last_load.date() if last_load else None)
        age_days = (as_of - basis).days if basis else None
        max_age = dataset.max_age_days
        if max_age is None:
            stale = None
        else:
            stale = last_data_date is None or age_days > max_age
        rows.append({
            "key": dataset.key,
            "label": dataset.label,
            "automation": dataset.automation,
            "cadence": dataset.cadence,
            "loader": dataset.loader,
            "folder": dataset.folder,
            "notes": dataset.notes,
            "table": table,
            "present": present,
            "last_data_date": last_data_date,
            "last_load": last_load,
            "age_days": age_days,
            "max_age_days": max_age,
            "stale": stale,
        })
    return rows


def summarize_diff(diff: dict) -> list[str]:
    """One line per dataset a run changed: rows added over its tables, and the new latest date of
    its main table when that advanced (e.g. "FEC Schedule A (contributions) +1,204, latest 2026-10-27")."""
    lines = []
    for dataset in DATASETS:
        entries = [diff[table] for table, _ in dataset.tables if table in diff]
        if not entries:
            continue
        added = sum(e.get("rows_added") or 0 for e in entries)
        main = diff.get(dataset.table) or {}
        new_date = main.get("max_date_after") if main.get("new_max_date") else None
        if added == 0 and not new_date:
            continue
        lines.append(f"{dataset.label} {added:+,}" + (f", latest {new_date}" if new_date else ""))
    return lines


def recent_runs(conn, limit: int = 14) -> list[dict]:
    """The latest runs, newest first, with minutes and the diff summary; [] before init-db made the table."""
    if not _table_exists(conn, "data_refresh_runs"):
        return []
    rows = conn.execute(
        "SELECT id, kind, started_at, finished_at, status, exit_code, check_status, notes, diff "
        "FROM data_refresh_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    runs = []
    for r in rows:
        minutes = None
        if r["finished_at"] is not None:
            minutes = round((r["finished_at"] - r["started_at"]).total_seconds() / 60, 1)
        runs.append({
            "id": r["id"], "kind": r["kind"], "started_at": r["started_at"], "finished_at": r["finished_at"],
            "minutes": minutes, "status": r["status"], "exit_code": r["exit_code"],
            "check_status": r["check_status"], "notes": r["notes"], "changes": summarize_diff(r["diff"] or {}),
        })
    return runs
