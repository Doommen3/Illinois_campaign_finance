#!/usr/bin/env python3
"""Authoritative raw-file schema census for ingestion audit.

Scans committed raw files and produces per-feed schema/profile metadata:
- union/intersection of columns across files
- per-file column sets and order
- sampled null-rate estimates and example values
- schema-drift detection

For IRS FullDataFile.txt (headerless), schema is positional per record type.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep sample sizes bounded so the census runs quickly in local environments.
DEFAULT_SAMPLE_ROWS_PER_FILE = 50_000
DEFAULT_IRS_SAMPLE_ROWS_PER_RECORD = 50_000
DEFAULT_SAMPLE_ROWS_PER_JSON_FEED = 50_000


@dataclass(frozen=True)
class DelimitedFeedConfig:
    feed_name: str
    fmt: str
    delimiter: str
    globs: tuple[str, ...]
    notes: str = ""


DELIMITED_FEEDS: tuple[DelimitedFeedConfig, ...] = (
    DelimitedFeedConfig(
        feed_name="isbe_committees_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Committees.txt", "Bulk_download/committees_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_d2totals_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/D2Totals.txt", "Bulk_download/d2totals_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_candidates_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Candidates.txt", "Bulk_download/candidates_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_cmte_candidate_links_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/CmteCandidateLinks.txt", "Bulk_download/cmtecandidatelinks_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_receipts_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Receipts.txt", "Bulk_download/receipts_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_expenditures_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Expenditures.txt", "Bulk_download/expenditures_*.txt"),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_filed_docs_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/FiledDocs.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_canelections_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/CanElections.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_officers_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Officers.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_prev_officers_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/PrevOfficers.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_cmte_officer_links_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/CmteOfficerLinks.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="isbe_investments_tsv",
        fmt="tsv",
        delimiter="\t",
        globs=("Bulk_download/Investments.txt",),
    ),
    DelimitedFeedConfig(
        feed_name="ilsos_lobbying_active_clients_csv",
        fmt="csv",
        delimiter=",",
        globs=("Bulk_download/ILSOS_Lobbying_activeandclients/*.csv",),
    ),
    DelimitedFeedConfig(
        feed_name="ilsos_lobbying_daily_csv",
        fmt="csv",
        delimiter=",",
        globs=("Bulk_download/Lobbyist_Entity_Client_Data_Daily_*.csv",),
    ),
)


IRS_FULLDATA_PATH = Path("Bulk_download/IRS_data/var/IRS/data/scripts/pofd/download/FullDataFile.txt")
SQLITE_RAW_DB_PATH = Path("data/campaign_finance.db")

IRS_RECORD_TYPE_LABELS = {
    "H": "irs527_full_data_record_H_header",
    "1": "irs527_full_data_record_1_org_registration",
    "2": "irs527_full_data_record_2_periodic_report",
    "A": "irs527_full_data_record_A_contribution",
    "B": "irs527_full_data_record_B_expenditure",
    "D": "irs527_full_data_record_D_director",
    "R": "irs527_full_data_record_R_related_org",
    "E": "irs527_full_data_record_E_election_authority",
}
IRS_KNOWN_RECORD_TYPES = set(IRS_RECORD_TYPE_LABELS.keys())

FEC_RUNTIME_FEEDS: tuple[dict[str, str], ...] = (
    {
        "feed_name": "fec_api_schedule_a_json",
        "source_type_pattern": "fec_api:schedules_schedule_a",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /schedules/schedule_a/.",
    },
    {
        "feed_name": "fec_api_schedule_b_json",
        "source_type_pattern": "fec_api:schedules_schedule_b",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /schedules/schedule_b/.",
    },
    {
        "feed_name": "fec_api_schedule_e_json",
        "source_type_pattern": "fec_api:schedules_schedule_e",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /schedules/schedule_e/.",
    },
    {
        "feed_name": "fec_api_candidates_search_json",
        "source_type_pattern": "fec_api:candidates_search",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /candidates/search/.",
    },
    {
        "feed_name": "fec_api_candidate_committees_json",
        "source_type_pattern": "fec_api:candidate_%_committees",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /candidate/{id}/committees/.",
    },
    {
        "feed_name": "fec_api_candidate_totals_json",
        "source_type_pattern": "fec_api:candidate_%_totals",
        "array_key": "results",
        "notes": "Runtime JSON cached in raw_extractions from /candidate/{id}/totals/.",
    },
)

OPENBOOK_RUNTIME_FEEDS: tuple[dict[str, str], ...] = (
    {
        "feed_name": "openbook_contracts_search_json",
        "source_type_pattern": "openbook_contracts_search",
        "array_key": "contracts",
        "notes": "Parsed contracts payload snapshots persisted in raw_extractions (not full HTML).",
    },
    {
        "feed_name": "openbook_contributions_tab_json",
        "source_type_pattern": "openbook_contributions_tab",
        "array_key": "contributions",
        "notes": "Parsed contributions payload snapshots persisted in raw_extractions (not full HTML).",
    },
    {
        "feed_name": "openbook_employees_tab_json",
        "source_type_pattern": "openbook_employees_tab",
        "array_key": "contributions",
        "notes": "Parsed employees-tab payload snapshots persisted in raw_extractions (not full HTML).",
    },
    {
        "feed_name": "openbook_contract_detail_json",
        "source_type_pattern": "openbook_contract_detail",
        "array_key": "",
        "notes": "Contract detail payload snapshots persisted in raw_extractions (not full popup HTML).",
    },
)

CHICAGO_RUNTIME_FEEDS: tuple[dict[str, str], ...] = (
    {
        "feed_name": "chicago_contracts_socrata_json",
        "dataset_id": "rsxa-ify5",
        "notes": "Contracts dataset columns from Socrata metadata + sample row scan.",
    },
    {
        "feed_name": "chicago_payments_socrata_json",
        "dataset_id": "s4vu-giwb",
        "notes": "Payments dataset columns from Socrata metadata + sample row scan.",
    },
    {
        "feed_name": "chicago_lobbyist_contributions_socrata_json",
        "dataset_id": "p9p7-vfqc",
        "notes": "Lobbyist contributions dataset columns from Socrata metadata + sample row scan.",
    },
    {
        "feed_name": "chicago_lobbying_activity_socrata_json",
        "dataset_id": "pahz-egmi",
        "notes": "Lobbying activity dataset columns from Socrata metadata + sample row scan.",
    },
)


def _strip_bom(value: str) -> str:
    return value.lstrip("\ufeff")


def _norm_header(value: str) -> str:
    return _strip_bom(value).strip()


def _example_append(bucket: list[str], value: str, max_examples: int = 5) -> None:
    if not value:
        return
    if value in bucket:
        return
    if len(bucket) >= max_examples:
        return
    bucket.append(value)


def _find_files(globs: tuple[str, ...]) -> list[Path]:
    files: set[Path] = set()
    for pat in globs:
        files.update(REPO_ROOT.glob(pat))
    return sorted(files)


def _scan_delimited_feed(
    config: DelimitedFeedConfig,
    sample_rows_per_file: int,
) -> dict[str, Any]:
    files = _find_files(config.globs)
    if not files:
        return {
            "feed_name": config.feed_name,
            "format": config.fmt,
            "delimiter": config.delimiter,
            "raw_globs": list(config.globs),
            "notes": config.notes or "No matching raw files found in workspace.",
            "blocked": True,
            "blocked_reason": "No files matched configured glob(s).",
        }

    per_file_columns: dict[str, list[str]] = {}
    union_set: set[str] = set()
    intersection_set: set[str] | None = None
    presence_counter: Counter[str] = Counter()

    nonempty_counts: dict[str, int] = defaultdict(int)
    sample_rows_total = 0
    examples: dict[str, list[str]] = defaultdict(list)
    profile_files: list[dict[str, Any]] = []

    for file_path in files:
        rel = str(file_path.relative_to(REPO_ROOT))
        with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=config.delimiter)
            raw_header = next(reader, [])
            columns = [_norm_header(h) for h in raw_header if _norm_header(h)]

            per_file_columns[rel] = columns
            col_set = set(columns)
            union_set.update(col_set)
            for col in col_set:
                presence_counter[col] += 1
            if intersection_set is None:
                intersection_set = set(col_set)
            else:
                intersection_set &= col_set

        # Re-open with DictReader for profiling sample rows.
        sampled_rows = 0
        with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            dict_reader = csv.DictReader(f, delimiter=config.delimiter)
            # Normalize fieldnames to match header normalization.
            fieldnames = [_norm_header(h) for h in (dict_reader.fieldnames or [])]
            original_fieldnames = dict_reader.fieldnames or []

            for row in dict_reader:
                if sampled_rows >= sample_rows_per_file:
                    break
                sampled_rows += 1
                sample_rows_total += 1

                for idx, col in enumerate(fieldnames):
                    raw_key = original_fieldnames[idx] if idx < len(original_fieldnames) else col
                    value = row.get(raw_key)
                    text = (value or "").strip()
                    if text:
                        nonempty_counts[col] += 1
                        _example_append(examples[col], text)

        profile_files.append(
            {
                "path": rel,
                "column_order": columns,
                "sampled_rows": sampled_rows,
                "sampling_method": f"first_{sample_rows_per_file}_rows",
            }
        )

    intersection_sorted = sorted(intersection_set or set())
    union_sorted = sorted(union_set)
    file_count = len(files)

    column_stats = []
    for col in union_sorted:
        nonempty = nonempty_counts.get(col, 0)
        null_rate = None
        if sample_rows_total > 0:
            null_rate = round(1.0 - (nonempty / sample_rows_total), 6)
        column_stats.append(
            {
                "raw_column_name": col,
                "present_in_files_count": presence_counter[col],
                "present_in_all_files": presence_counter[col] == file_count,
                "example_values": examples.get(col, []),
                "null_rate_estimate": null_rate,
                "profiling_sample_rows_total": sample_rows_total,
                "notes": "",
            }
        )

    first_file_order = next(iter(per_file_columns.values())) if per_file_columns else []
    drift_files = []
    for rel, cols in per_file_columns.items():
        missing_vs_union = sorted(list(union_set - set(cols)))
        extra_vs_union = sorted(list(set(cols) - union_set))
        order_drift = cols != first_file_order
        if missing_vs_union or extra_vs_union or order_drift:
            drift_files.append(
                {
                    "path": rel,
                    "column_order": cols,
                    "missing_vs_union": missing_vs_union,
                    "extra_vs_union": extra_vs_union,
                    "order_drift_vs_first_file": order_drift,
                }
            )

    return {
        "feed_name": config.feed_name,
        "format": config.fmt,
        "delimiter": config.delimiter,
        "raw_globs": list(config.globs),
        "notes": config.notes,
        "blocked": False,
        "files": profile_files,
        "union_of_columns": union_sorted,
        "intersection_of_columns": intersection_sorted,
        "column_stats": column_stats,
        "schema_drift_files": drift_files,
        "profiling": {
            "sample_rows_per_file_cap": sample_rows_per_file,
            "sample_rows_total": sample_rows_total,
            "method": "header_scanned_all_files_profiled_first_n_rows_per_file",
        },
    }


def _scan_irs_file(sample_rows_per_record: int) -> list[dict[str, Any]]:
    path = REPO_ROOT / IRS_FULLDATA_PATH
    if not path.exists():
        return [
            {
                "feed_name": "irs527_full_data",
                "format": "pipe_delimited_headerless",
                "raw_globs": [str(IRS_FULLDATA_PATH)],
                "blocked": True,
                "blocked_reason": "FullDataFile.txt not present in workspace.",
            }
        ]

    # Per record type stats.
    row_counts: Counter[str] = Counter()
    max_positions: dict[str, int] = defaultdict(int)
    min_positions: dict[str, int] = {}
    nonempty_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    sample_counts: Counter[str] = Counter()
    examples: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))

    # Full-file pass for authoritative record-type discovery and positional max/min.
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if not line:
                continue
            fields = line.lstrip("\ufeff").split("|")
            if not fields:
                continue
            record_type_raw = (fields[0] or "").strip()
            record_type = record_type_raw if record_type_raw in IRS_KNOWN_RECORD_TYPES else "UNKNOWN"
            row_counts[record_type] += 1

            # Excluding record_type token itself.
            pos_count = max(len(fields) - 1, 0)
            max_positions[record_type] = max(max_positions[record_type], pos_count)
            if record_type not in min_positions:
                min_positions[record_type] = pos_count
            else:
                min_positions[record_type] = min(min_positions[record_type], pos_count)

            if sample_counts[record_type] >= sample_rows_per_record:
                continue

            sample_counts[record_type] += 1
            for pos in range(1, len(fields)):
                text = (fields[pos] or "").strip()
                if text:
                    nonempty_counts[record_type][pos] += 1
                    _example_append(examples[record_type][pos], text)

    rel_path = str(path.relative_to(REPO_ROOT))
    feeds: list[dict[str, Any]] = []
    for record_type in sorted(row_counts.keys()):
        feed_name = IRS_RECORD_TYPE_LABELS.get(record_type, f"irs527_full_data_record_{record_type}")
        max_pos = max_positions.get(record_type, 0)
        union_cols = [f"pos_{i}" for i in range(1, max_pos + 1)]
        min_pos = min_positions.get(record_type, 0)
        intersection_cols = [f"pos_{i}" for i in range(1, min_pos + 1)]
        sample_n = sample_counts.get(record_type, 0)

        column_stats = []
        for pos in range(1, max_pos + 1):
            nonempty = nonempty_counts[record_type].get(pos, 0)
            null_rate = None
            if sample_n > 0:
                null_rate = round(1.0 - (nonempty / sample_n), 6)
            column_stats.append(
                {
                    "raw_column_name": f"pos_{pos}",
                    "present_in_files_count": 1,
                    "present_in_all_files": True,
                    "example_values": examples[record_type].get(pos, []),
                    "null_rate_estimate": null_rate,
                    "profiling_sample_rows_total": sample_n,
                    "notes": "Headerless feed: positional column index.",
                }
            )

        feeds.append(
            {
                "feed_name": feed_name,
                "format": "pipe_delimited_headerless",
                "delimiter": "|",
                "raw_globs": [str(IRS_FULLDATA_PATH)],
                "blocked": False,
                "files": [
                    {
                        "path": rel_path,
                        "column_order": union_cols,
                        "sampled_rows": sample_n,
                        "sampling_method": f"first_{sample_rows_per_record}_rows_per_record_type_with_full_record_type_scan",
                    }
                ],
                "record_type": record_type,
                "rows_observed": row_counts[record_type],
                "union_of_columns": union_cols,
                "intersection_of_columns": intersection_cols,
                "column_stats": column_stats,
                "schema_drift_files": [],
                "profiling": {
                    "sample_rows_per_record_type_cap": sample_rows_per_record,
                    "sample_rows_record_type": sample_n,
                    "method": "full_file_record_type_scan_profiled_first_n_rows_per_record_type",
                },
            }
        )

    return feeds


def _scan_runtime_json_feed_from_raw_extractions(
    *,
    feed_name: str,
    source_type_pattern: str,
    array_key: str | None,
    notes: str,
    sample_rows_per_feed: int,
) -> dict[str, Any]:
    db_path = REPO_ROOT / SQLITE_RAW_DB_PATH
    if not db_path.exists():
        return {
            "feed_name": feed_name,
            "format": "json",
            "raw_globs": [f"sqlite:{SQLITE_RAW_DB_PATH} raw_extractions source_type LIKE {source_type_pattern}"],
            "notes": notes,
            "blocked": True,
            "blocked_reason": f"SQLite DB not found: {SQLITE_RAW_DB_PATH}",
        }

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT source_type, source_identifier, payload_json, updated_at
            FROM raw_extractions
            WHERE source_type LIKE ?
            ORDER BY updated_at DESC, source_identifier DESC
            """,
            (source_type_pattern,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "feed_name": feed_name,
            "format": "json",
            "raw_globs": [f"sqlite:{SQLITE_RAW_DB_PATH} raw_extractions source_type LIKE {source_type_pattern}"],
            "notes": notes,
            "blocked": True,
            "blocked_reason": "No matching raw_extractions rows for source_type pattern.",
        }

    per_file_columns: dict[str, list[str]] = {}
    union_set: set[str] = set()
    intersection_set: set[str] | None = None
    presence_counter: Counter[str] = Counter()

    nonempty_counts: dict[str, int] = defaultdict(int)
    sample_rows_total = 0
    examples: dict[str, list[str]] = defaultdict(list)
    profile_files: list[dict[str, Any]] = []
    rows_observed_total = 0

    for row in rows:
        source_type = str(row["source_type"])
        source_identifier = str(row["source_identifier"])
        payload_json = row["payload_json"] or ""
        file_id = f"raw_extractions:{source_type}:{source_identifier}"

        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            profile_files.append(
                {
                    "path": file_id,
                    "column_order": [],
                    "sampled_rows": 0,
                    "sampling_method": "invalid_json_payload",
                }
            )
            continue

        record_dicts: list[dict[str, Any]] = []
        if array_key is None:
            if isinstance(payload, dict):
                record_dicts = [payload]
        elif array_key == "":
            if isinstance(payload, dict):
                record_dicts = [payload]
        else:
            arr = payload.get(array_key) if isinstance(payload, dict) else None
            if isinstance(arr, list):
                record_dicts = [item for item in arr if isinstance(item, dict)]

        if record_dicts:
            column_order = list(record_dicts[0].keys())
        else:
            column_order = []
        col_set = set(column_order)
        # Include keys seen later in the record list to keep union authoritative.
        for item in record_dicts[1:]:
            col_set.update(item.keys())

        per_file_columns[file_id] = list(col_set) if not column_order else column_order
        union_set.update(col_set)
        for col in col_set:
            presence_counter[col] += 1
        if intersection_set is None:
            intersection_set = set(col_set)
        else:
            intersection_set &= col_set

        sampled_rows_this_file = 0
        for item in record_dicts:
            rows_observed_total += 1
            if sample_rows_total >= sample_rows_per_feed:
                continue
            sample_rows_total += 1
            sampled_rows_this_file += 1
            for key, value in item.items():
                text = str(value).strip() if value is not None else ""
                if text:
                    nonempty_counts[key] += 1
                    _example_append(examples[key], text)

        profile_files.append(
            {
                "path": file_id,
                "column_order": column_order,
                "sampled_rows": sampled_rows_this_file,
                "sampling_method": f"first_{sample_rows_per_feed}_rows_across_feed",
            }
        )

    file_count = len(profile_files)
    union_sorted = sorted(union_set)
    intersection_sorted = sorted(intersection_set or set())

    column_stats = []
    for col in union_sorted:
        nonempty = nonempty_counts.get(col, 0)
        null_rate = None
        if sample_rows_total > 0:
            null_rate = round(1.0 - (nonempty / sample_rows_total), 6)
        column_stats.append(
            {
                "raw_column_name": col,
                "present_in_files_count": presence_counter[col],
                "present_in_all_files": presence_counter[col] == file_count if file_count else False,
                "example_values": examples.get(col, []),
                "null_rate_estimate": null_rate,
                "profiling_sample_rows_total": sample_rows_total,
                "notes": "",
            }
        )

    first_file_order = profile_files[0]["column_order"] if profile_files else []
    drift_files = []
    for f in profile_files:
        cols = f["column_order"]
        missing_vs_union = sorted(list(union_set - set(cols)))
        extra_vs_union = sorted(list(set(cols) - union_set))
        order_drift = cols != first_file_order
        if missing_vs_union or extra_vs_union or order_drift:
            drift_files.append(
                {
                    "path": f["path"],
                    "column_order": cols,
                    "missing_vs_union": missing_vs_union,
                    "extra_vs_union": extra_vs_union,
                    "order_drift_vs_first_file": order_drift,
                }
            )

    if not union_sorted:
        return {
            "feed_name": feed_name,
            "format": "json",
            "raw_globs": [f"sqlite:{SQLITE_RAW_DB_PATH} raw_extractions source_type LIKE {source_type_pattern}"],
            "notes": notes,
            "blocked": True,
            "blocked_reason": "No object records found in payloads for configured extractor.",
            "files": profile_files,
        }

    return {
        "feed_name": feed_name,
        "format": "json",
        "raw_globs": [f"sqlite:{SQLITE_RAW_DB_PATH} raw_extractions source_type LIKE {source_type_pattern}"],
        "notes": notes,
        "blocked": False,
        "files": profile_files,
        "union_of_columns": union_sorted,
        "intersection_of_columns": intersection_sorted,
        "column_stats": column_stats,
        "rows_observed": rows_observed_total,
        "schema_drift_files": drift_files,
        "profiling": {
            "sample_rows_per_feed_cap": sample_rows_per_feed,
            "sample_rows_total": sample_rows_total,
            "method": "raw_extractions_payload_scan_union_all_payloads_profiled_first_n_rows_across_feed",
        },
    }


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "raw-schema-census/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8-sig"))


def _scan_chicago_socrata_feed(
    *,
    feed_name: str,
    dataset_id: str,
    notes: str,
    sample_rows_per_feed: int,
) -> dict[str, Any]:
    base = "https://data.cityofchicago.org"
    meta_url = f"{base}/api/views/{dataset_id}.json"
    rows_query = urllib.parse.urlencode(
        {
            "$limit": str(max(1, sample_rows_per_feed)),
            "$order": ":id",
        }
    )
    rows_url = f"{base}/resource/{dataset_id}.json?{rows_query}"

    try:
        metadata = _fetch_json(meta_url)
    except Exception as exc:
        return {
            "feed_name": feed_name,
            "format": "json_api",
            "raw_globs": [rows_url],
            "notes": notes,
            "blocked": True,
            "blocked_reason": f"Failed to fetch Socrata metadata: {exc}",
        }

    columns = []
    for col in metadata.get("columns", []) if isinstance(metadata, dict) else []:
        field = (col.get("fieldName") or "").strip()
        if field:
            columns.append(field)
    if not columns:
        return {
            "feed_name": feed_name,
            "format": "json_api",
            "raw_globs": [rows_url],
            "notes": notes,
            "blocked": True,
            "blocked_reason": "No metadata columns returned by Socrata view endpoint.",
        }

    try:
        sample_rows = _fetch_json(rows_url)
    except Exception:
        sample_rows = []

    nonempty_counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    sampled_rows = 0
    for row in sample_rows if isinstance(sample_rows, list) else []:
        if not isinstance(row, dict):
            continue
        sampled_rows += 1
        for col in columns:
            text = str(row.get(col)).strip() if row.get(col) is not None else ""
            if text:
                nonempty_counts[col] += 1
                _example_append(examples[col], text)

    column_stats = []
    for col in columns:
        null_rate = None
        if sampled_rows > 0:
            null_rate = round(1.0 - (nonempty_counts[col] / sampled_rows), 6)
        column_stats.append(
            {
                "raw_column_name": col,
                "present_in_files_count": 1,
                "present_in_all_files": True,
                "example_values": examples.get(col, []),
                "null_rate_estimate": null_rate,
                "profiling_sample_rows_total": sampled_rows,
                "notes": "",
            }
        )

    return {
        "feed_name": feed_name,
        "format": "json_api",
        "raw_globs": [rows_url],
        "notes": notes,
        "blocked": False,
        "files": [
            {
                "path": rows_url,
                "column_order": columns,
                "sampled_rows": sampled_rows,
                "sampling_method": f"socrata_metadata_columns_profiled_first_{sample_rows_per_feed}_rows",
            }
        ],
        "dataset_id": dataset_id,
        "union_of_columns": list(columns),
        "intersection_of_columns": list(columns),
        "column_stats": column_stats,
        "schema_drift_files": [],
        "profiling": {
            "sample_rows_per_feed_cap": sample_rows_per_feed,
            "sample_rows_total": sampled_rows,
            "method": "socrata_metadata_columns_plus_sample_rows",
        },
    }


def _scan_runtime_feeds(sample_rows_per_json_feed: int) -> list[dict[str, Any]]:
    feeds: list[dict[str, Any]] = []

    for cfg in FEC_RUNTIME_FEEDS:
        feeds.append(
            _scan_runtime_json_feed_from_raw_extractions(
                feed_name=cfg["feed_name"],
                source_type_pattern=cfg["source_type_pattern"],
                array_key=cfg["array_key"] if cfg["array_key"] != "" else None,
                notes=cfg["notes"],
                sample_rows_per_feed=sample_rows_per_json_feed,
            )
        )

    for cfg in OPENBOOK_RUNTIME_FEEDS:
        array_key = cfg["array_key"]
        feeds.append(
            _scan_runtime_json_feed_from_raw_extractions(
                feed_name=cfg["feed_name"],
                source_type_pattern=cfg["source_type_pattern"],
                array_key=array_key if array_key != "" else None,
                notes=cfg["notes"],
                sample_rows_per_feed=sample_rows_per_json_feed,
            )
        )

    for cfg in CHICAGO_RUNTIME_FEEDS:
        feeds.append(
            _scan_chicago_socrata_feed(
                feed_name=cfg["feed_name"],
                dataset_id=cfg["dataset_id"],
                notes=cfg["notes"],
                sample_rows_per_feed=min(5000, sample_rows_per_json_feed),
            )
        )

    return feeds


def run_census(
    sample_rows_per_file: int = DEFAULT_SAMPLE_ROWS_PER_FILE,
    sample_rows_per_irs_record: int = DEFAULT_IRS_SAMPLE_ROWS_PER_RECORD,
    sample_rows_per_json_feed: int = DEFAULT_SAMPLE_ROWS_PER_JSON_FEED,
) -> dict[str, Any]:
    feeds: list[dict[str, Any]] = []
    for config in DELIMITED_FEEDS:
        feeds.append(_scan_delimited_feed(config, sample_rows_per_file=sample_rows_per_file))
    feeds.extend(_scan_irs_file(sample_rows_per_record=sample_rows_per_irs_record))
    feeds.extend(_scan_runtime_feeds(sample_rows_per_json_feed=sample_rows_per_json_feed))

    blocked = [f for f in feeds if f.get("blocked")]

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "sampling": {
            "sample_rows_per_file": sample_rows_per_file,
            "sample_rows_per_irs_record_type": sample_rows_per_irs_record,
            "sample_rows_per_json_feed": sample_rows_per_json_feed,
        },
        "feeds": feeds,
        "blocked_feeds": blocked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run raw-file schema census across ingestion feeds.")
    parser.add_argument(
        "--output-json",
        default="output/raw_schema_census.json",
        help="Output JSON path (default: output/raw_schema_census.json)",
    )
    parser.add_argument(
        "--sample-rows-per-file",
        type=int,
        default=DEFAULT_SAMPLE_ROWS_PER_FILE,
        help=f"Max rows sampled per delimited file (default: {DEFAULT_SAMPLE_ROWS_PER_FILE})",
    )
    parser.add_argument(
        "--sample-rows-per-irs-record",
        type=int,
        default=DEFAULT_IRS_SAMPLE_ROWS_PER_RECORD,
        help=(
            "Max rows sampled per IRS record type for null/examples "
            f"(default: {DEFAULT_IRS_SAMPLE_ROWS_PER_RECORD})"
        ),
    )
    parser.add_argument(
        "--sample-rows-per-json-feed",
        type=int,
        default=DEFAULT_SAMPLE_ROWS_PER_JSON_FEED,
        help=(
            "Max rows sampled per runtime JSON feed for null/examples "
            f"(default: {DEFAULT_SAMPLE_ROWS_PER_JSON_FEED})"
        ),
    )
    args = parser.parse_args()

    payload = run_census(
        sample_rows_per_file=max(1, int(args.sample_rows_per_file)),
        sample_rows_per_irs_record=max(1, int(args.sample_rows_per_irs_record)),
        sample_rows_per_json_feed=max(1, int(args.sample_rows_per_json_feed)),
    )

    out_path = REPO_ROOT / args.output_json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    feed_count = len(payload["feeds"])
    blocked_count = len(payload["blocked_feeds"])
    print(f"Wrote census JSON: {out_path}")
    print(f"Feeds scanned: {feed_count} (blocked: {blocked_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
