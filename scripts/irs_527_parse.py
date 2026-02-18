#!/usr/bin/env python3
"""Parse IRS 527 Political Organizations bulk data into per-record CSV files.

Key design constraints:
- Record schemas are extracted programmatically from PolOrgsFileLayout.doc.
- Input is processed as a stream (line-by-line).
- Column-count mismatches are quarantined; no extra columns are silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sqlite3
import subprocess
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


SECTION_HEADINGS: list[tuple[str, str]] = [
    ("FILE HEADER RECORD FORMAT FOR ALL ENTRIES", "H"),
    ("8871 FORM RECORD FORMAT FOR ALL ENTRIES", "1"),
    ("DIRECTORS AND OFFICERS FORM RECORD FORMAT FOR ALL ENTRIES", "D"),
    ("RELATED ENTITIES FORM RECORD FORMAT FOR ALL ENTRIES", "R"),
    ("EAIN FORM RECORD FORMAT FOR ALL ENTRIES", "E"),
    ("8872 FORM RECORD FORMAT FOR ALL ENTRIES", "2"),
    ("SCHEDULE A RECORD FORMAT FOR ALL ENTRIES", "A"),
    ("SCHEDULE B RECORD FORMAT FOR ALL ENTRIES", "B"),
    ("FILE FOOTER RECORD FORMAT FOR ALL ENTRIES", "F"),
]

OUTPUT_FILES: dict[str, str] = {
    "1": "organizations_8871.csv",
    "D": "directors_officers.csv",
    "R": "related_entities.csv",
    "E": "eain.csv",
    "2": "reports_8872.csv",
    "A": "contributions_sched_a.csv",
    "B": "expenditures_sched_b.csv",
    "H": "file_header.csv",
    "F": "file_footer.csv",
}

QUARANTINE_FILE = "quarantine_bad_rows.csv"

DEFAULT_LAYOUT_DOCS = [
    Path("/mnt/data/PolOrgsFileLayout.doc"),
    Path("/Users/devin/Illinois_campaign_finance/Bulk_download/IRS_data/PolOrgsFileLayout.doc"),
]

SCHEDULE_A_REQUIRED = {
    "contributor_name",
    "contributor_address_1",
    "contributor_address_2",
    "contributor_address_city",
    "contributor_address_state",
    "contributor_address_zip_code",
    "contributor_address_zip_ext",
    "contributor_employer",
    "contribution_amount",
    "contributor_occupation",
    "agg_contribution_ytd",
    "contribution_date",
}

SCHEDULE_B_REQUIRED = {
    "recipient_name",
    "recipient_address_1",
    "recipient_address_2",
    "recipient_address_city",
    "recipient_address_state",
    "recipient_address_zip_code",
    "recipient_address_zip_ext",
    "recipient_employer",
    "expenditure_amount",
    "recipient_occupation",
    "expenditure_date",
    "expenditure_purpose",
}

EAIN_REQUIRED = {
    "eain_id",
    "election_authority_id_number",
    "state_issued",
}

LOGGER = logging.getLogger("irs_527_parse")


def resolve_layout_doc_path(explicit_path: str | None) -> Path:
    """Resolve the file layout doc path, preferring explicit argument."""
    if explicit_path:
        doc_path = Path(explicit_path).expanduser().resolve()
        if not doc_path.exists():
            raise FileNotFoundError(f"Layout doc not found: {doc_path}")
        return doc_path

    for candidate in DEFAULT_LAYOUT_DOCS:
        if candidate.exists():
            return candidate

    search_paths = ", ".join(str(p) for p in DEFAULT_LAYOUT_DOCS)
    raise FileNotFoundError(f"Could not find PolOrgsFileLayout.doc in: {search_paths}")


def extract_layout_text(layout_doc_path: Path) -> str:
    """Extract plain text from the legacy .doc using textutil."""
    cmd = ["textutil", "-convert", "txt", "-stdout", str(layout_doc_path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"textutil failed for {layout_doc_path}: {stderr or 'unknown error'}")
    return proc.stdout.replace("\x0c", "\n")


def _is_size_token(value: str) -> bool:
    token = value.strip()
    if not token:
        return False
    return bool(
        re.fullmatch(r"\(?\d+\)?", token)
        or re.fullmatch(r"(?i)X\(\d+\)", token)
        or re.fullmatch(r"(?i)Up to \d+ digits", token)
    )


def _is_noise_line(value: str) -> bool:
    token = value.strip()
    upper = token.upper()
    if not token:
        return True

    if upper in {
        "IRS FIELD NAME",
        "SIZE",
        "LENGTH",
        "VALID VALUES",
        "VALUE",
        "FORMAT",
        "DESCRIPTION",
        "ALPHANUMERIC",
        "NUMERIC",
        "CHARACTER",
        "DATETIME",
        "SYSDATE",
    }:
        return True

    if upper.startswith("PIPE"):
        return True

    if upper.startswith("PAGE"):
        return True

    # Table glyph row for delimiter.
    if re.fullmatch(r"[‘']?\|[’']?", token):
        return True

    # We only accept field labels that contain alpha characters.
    if not re.search(r"[A-Za-z]", token):
        return True

    return False


def _normalize_field_name(field_label: str) -> str:
    normalized = field_label.strip()
    normalized = normalized.replace("RECIEPIENT", "RECIPIENT")
    normalized = normalized.replace("E_MAIL", "EMAIL")
    normalized = normalized.replace("PRE or POST", "PRE_OR_POST")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized).strip("_").lower()
    normalized = re.sub(r"address_st$", "address_state", normalized)
    normalized = re.sub(r"address_st_", "address_state_", normalized)
    normalized = normalized.replace("zip_code_ext", "zip_ext")
    return normalized


def _slice_schema_sections(doc_text: str) -> dict[str, str]:
    text_upper = doc_text.upper()
    starts: list[tuple[int, str]] = []

    for heading, record_type in SECTION_HEADINGS:
        idx = text_upper.find(heading)
        if idx < 0:
            raise ValueError(f"Could not find section heading for record type {record_type!r}: {heading}")
        starts.append((idx, record_type))

    starts.sort(key=lambda item: item[0])

    sections: dict[str, str] = {}
    for pos, (start_idx, record_type) in enumerate(starts):
        end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(doc_text)
        sections[record_type] = doc_text[start_idx:end_idx]

    return sections


def _extract_field_labels(section_text: str) -> list[str]:
    lines = [line.strip() for line in section_text.splitlines()]
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.upper() == "IRS FIELD NAME")
    except StopIteration as exc:
        raise ValueError("Schema table start marker 'IRS Field Name' not found in section") from exc

    extracted: list[str] = []
    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx].strip()
        if _is_noise_line(line):
            continue

        next_non_empty = ""
        for probe in range(idx + 1, len(lines)):
            candidate = lines[probe].strip()
            if candidate:
                next_non_empty = candidate
                break

        if not next_non_empty:
            continue

        if _is_size_token(next_non_empty):
            extracted.append(line)

    # Stable de-duplication in case the doc repeats labels.
    deduped: list[str] = []
    seen: set[str] = set()
    for label in extracted:
        if label not in seen:
            deduped.append(label)
            seen.add(label)
    return deduped


def extract_schemas_from_doc(layout_doc_path: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (normalized_schema, raw_doc_labels) keyed by record type."""
    doc_text = extract_layout_text(layout_doc_path)
    section_texts = _slice_schema_sections(doc_text)

    raw_doc_labels: dict[str, list[str]] = {}
    normalized_schema: dict[str, list[str]] = {}

    for record_type in OUTPUT_FILES:
        labels = _extract_field_labels(section_texts[record_type])
        if not labels:
            raise ValueError(f"No fields extracted for record type {record_type}")
        raw_doc_labels[record_type] = labels
        normalized_schema[record_type] = [_normalize_field_name(label) for label in labels]

    validate_extracted_schemas(normalized_schema)
    return normalized_schema, raw_doc_labels


def validate_extracted_schemas(schema: dict[str, list[str]]) -> None:
    """Validate A/B/E required fields called out in the layout doc."""
    missing_record_types = sorted(set(OUTPUT_FILES) - set(schema))
    if missing_record_types:
        raise ValueError(f"Missing schema definitions for record types: {', '.join(missing_record_types)}")

    sched_a_fields = set(schema["A"])
    missing_a = sorted(SCHEDULE_A_REQUIRED - sched_a_fields)
    if missing_a:
        raise ValueError(f"Schedule A schema missing required doc fields: {', '.join(missing_a)}")

    sched_b_fields = set(schema["B"])
    missing_b = sorted(SCHEDULE_B_REQUIRED - sched_b_fields)
    if missing_b:
        raise ValueError(f"Schedule B schema missing required doc fields: {', '.join(missing_b)}")

    eain_fields = set(schema["E"])
    missing_e = sorted(EAIN_REQUIRED - eain_fields)
    if missing_e:
        raise ValueError(f"EAIN schema missing required doc fields: {', '.join(missing_e)}")


def parse_amount(value: str | None) -> Decimal | None:
    """Parse a currency string to Decimal.

    Handles: commas, dollar signs, parentheses negatives, leading/trailing spaces.
    Returns None for empty, whitespace-only, None, or truly invalid values.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    # Strip currency symbols
    text = text.replace("$", "").replace(",", "")
    # Handle parentheses negatives: (500.00) → -500.00
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    text = text.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def normalize_url(value: str | None) -> str | None:
    """Normalize and sanitize a URL.

    - Strips whitespace
    - Adds https:// if scheme is missing
    - Lowercases scheme and host
    - Strips UTM tracking parameters
    - Returns None for empty, None, or clearly invalid values
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    # Add scheme if missing (contains dot but no scheme)
    if "://" not in text:
        if "." in text and " " not in text:
            text = "https://" + text
        else:
            return None

    try:
        parsed = urlparse(text)
    except ValueError:
        return None

    if not parsed.hostname:
        return None

    # Lowercase scheme and host
    scheme = (parsed.scheme or "https").lower()
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""

    # Strip UTM tracking params
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in sorted(params.items()) if not k.startswith("utm_")}
        query = urlencode(filtered, doseq=True)
    else:
        query = ""

    result = urlunparse((scheme, host + port, parsed.path, parsed.params, query, ""))
    return result


def get_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    """Thread-safe SQLite connection factory.

    Each call creates a new connection (safe for per-thread use).
    Uses WAL journal mode and appropriate busy timeout.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def sql_join_staged_records(
    conn: sqlite3.Connection,
    left_table: str,
    right_table: str,
    join_column: str,
) -> list[sqlite3.Row]:
    """Join two staged record tables via SQL (not in-memory merge).

    This ensures cross-record-type lookups use SQL JOIN rather than
    loading both sides into memory.
    """
    left_ident = _sql_ident(left_table)
    right_ident = _sql_ident(right_table)
    col_ident = _sql_ident(join_column)
    query = (
        f"SELECT l.*, r.* FROM {left_ident} l "
        f"INNER JOIN {right_ident} r ON l.{col_ident} = r.{col_ident}"
    )
    return conn.execute(query).fetchall()


def _record_type_from_line(raw_line: str) -> str:
    for char in raw_line:
        if not char.isspace():
            return char
    return ""


def _make_writers(outdir: Path, schema: dict[str, list[str]]) -> tuple[dict[str, csv.writer], dict[str, object]]:
    outdir.mkdir(parents=True, exist_ok=True)

    writers: dict[str, csv.writer] = {}
    handles: dict[str, object] = {}

    for record_type, filename in OUTPUT_FILES.items():
        out_path = outdir / filename
        handle = out_path.open("w", encoding="utf-8", newline="")
        writer = csv.writer(handle)
        writer.writerow(schema[record_type])
        writers[record_type] = writer
        handles[record_type] = handle

    quarantine_path = outdir / QUARANTINE_FILE
    quarantine_handle = quarantine_path.open("w", encoding="utf-8", newline="")
    quarantine_writer = csv.writer(quarantine_handle)
    quarantine_writer.writerow(["record_type", "expected_len", "actual_len", "reason", "raw_line"])
    writers["__quarantine__"] = quarantine_writer
    handles["__quarantine__"] = quarantine_handle

    return writers, handles


def parse_irs_527_file(
    input_path: Path,
    outdir: Path,
    schema: dict[str, list[str]],
) -> dict[str, object]:
    """Stream-parse raw file into per-record CSVs and quarantine bad rows."""
    writers, handles = _make_writers(outdir, schema)
    record_counts: Counter[str] = Counter()
    quarantine_count = 0
    total_lines = 0
    warned_rows = 0
    max_warning_rows = 30

    try:
        with input_path.open("r", encoding="utf-8", errors="replace") as infile:
            for line_number, line in enumerate(infile, start=1):
                total_lines += 1
                raw_line = line.rstrip("\r\n")
                if raw_line.startswith("\ufeff"):
                    raw_line = raw_line.lstrip("\ufeff")

                record_type = _record_type_from_line(raw_line)
                parts = raw_line.split("|")
                actual_len = len(parts)

                def quarantine(reason: str, expected_len: int = 0) -> None:
                    nonlocal quarantine_count, warned_rows
                    quarantine_count += 1
                    writers["__quarantine__"].writerow(
                        [record_type, expected_len, actual_len, f"line={line_number}; {reason}", raw_line]
                    )
                    if warned_rows < max_warning_rows:
                        LOGGER.warning(
                            "Quarantine line %s (record_type=%r expected_len=%s actual_len=%s): %s",
                            line_number,
                            record_type,
                            expected_len,
                            actual_len,
                            reason,
                        )
                        warned_rows += 1

                if not raw_line:
                    quarantine("empty_line")
                    continue

                if record_type not in schema:
                    quarantine("unknown_record_type")
                    continue

                expected_fields = len(schema[record_type])
                has_terminal_delimiter = actual_len == (expected_fields + 1) and parts[-1] == ""
                exact_field_count = actual_len == expected_fields

                if not has_terminal_delimiter and not exact_field_count:
                    quarantine(
                        "column_count_mismatch (accepted: field_count or field_count+1 with trailing pipe)",
                        expected_fields,
                    )
                    continue

                if actual_len == (expected_fields + 1) and parts[-1] != "":
                    quarantine("extra_column_data_after_expected_fields", expected_fields)
                    continue

                row_values = parts[:-1] if has_terminal_delimiter else parts
                if len(row_values) != expected_fields:
                    quarantine("row_normalization_length_mismatch", expected_fields)
                    continue

                writers[record_type].writerow(row_values)
                record_counts[record_type] += 1
    finally:
        for handle in handles.values():
            handle.close()

    return {
        "input_path": str(input_path),
        "outdir": str(outdir),
        "total_lines": total_lines,
        "quarantine_rows": quarantine_count,
        "record_counts": dict(record_counts),
        "schema_field_counts": {record_type: len(columns) for record_type, columns in schema.items()},
    }


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_csvs_to_sqlite(outdir: Path, schema: dict[str, list[str]], db_path: Path) -> dict[str, int]:
    """Optional staging load hook for parsed outputs."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    loaded: dict[str, int] = {}

    try:
        for record_type, filename in OUTPUT_FILES.items():
            csv_path = outdir / filename
            table_name = f"irs527_stage_{csv_path.stem}"
            columns = schema[record_type]

            create_sql = (
                f"CREATE TABLE IF NOT EXISTS {_sql_ident(table_name)} ("
                + ", ".join(f"{_sql_ident(col)} TEXT" for col in columns)
                + ")"
            )
            conn.execute(create_sql)
            conn.execute(f"DELETE FROM {_sql_ident(table_name)}")

            insert_sql = (
                f"INSERT INTO {_sql_ident(table_name)} ("
                + ", ".join(_sql_ident(col) for col in columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")"
            )

            row_count = 0
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                batch: list[tuple[str, ...]] = []
                for row in reader:
                    row_count += 1
                    batch.append(tuple(row.get(col, "") for col in columns))
                    if len(batch) >= 5000:
                        conn.executemany(insert_sql, batch)
                        batch.clear()
                if batch:
                    conn.executemany(insert_sql, batch)
            loaded[table_name] = row_count

        # Quarantine staging table.
        quarantine_path = outdir / QUARANTINE_FILE
        quarantine_cols = ["record_type", "expected_len", "actual_len", "reason", "raw_line"]
        quarantine_table = "irs527_stage_quarantine_bad_rows"
        conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + _sql_ident(quarantine_table)
            + " ("
            + ", ".join(f"{_sql_ident(col)} TEXT" for col in quarantine_cols)
            + ")"
        )
        conn.execute(f"DELETE FROM {_sql_ident(quarantine_table)}")

        quarantine_insert_sql = (
            f"INSERT INTO {_sql_ident(quarantine_table)} ("
            + ", ".join(_sql_ident(col) for col in quarantine_cols)
            + ") VALUES ("
            + ", ".join("?" for _ in quarantine_cols)
            + ")"
        )
        quarantine_rows = 0
        with quarantine_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            batch: list[tuple[str, ...]] = []
            for row in reader:
                quarantine_rows += 1
                batch.append(tuple(row.get(col, "") for col in quarantine_cols))
                if len(batch) >= 5000:
                    conn.executemany(quarantine_insert_sql, batch)
                    batch.clear()
            if batch:
                conn.executemany(quarantine_insert_sql, batch)
        loaded[quarantine_table] = quarantine_rows

        conn.commit()
        return loaded
    finally:
        conn.close()


def _emit_schema_summary(schema: dict[str, list[str]], raw_labels: dict[str, list[str]]) -> None:
    LOGGER.info("Extracted record schemas from layout doc:")
    for record_type in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
        LOGGER.info(
            "  %s: %d fields (%s)",
            record_type,
            len(schema[record_type]),
            ", ".join(raw_labels[record_type][:3]) + ("..." if len(raw_labels[record_type]) > 3 else ""),
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse IRS 527 PolOrgs bulk file by record type.")
    parser.add_argument("--input", required=True, help="Path to raw IRS 527 pipe-delimited file.")
    parser.add_argument(
        "--outdir",
        default="data/processed/irs_527/",
        help="Directory for output CSV files.",
    )
    parser.add_argument(
        "--layout-doc",
        default=None,
        help="Path to PolOrgsFileLayout.doc. Defaults to known environment locations.",
    )
    parser.add_argument(
        "--load-db",
        action="store_true",
        help="Optional: load parsed CSV outputs into SQLite staging tables.",
    )
    parser.add_argument(
        "--db-path",
        default="data/campaign_finance.db",
        help="SQLite DB path for --load-db mode.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="%(levelname)s %(message)s",
    )

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    outdir = Path(args.outdir).expanduser().resolve()
    layout_doc_path = resolve_layout_doc_path(args.layout_doc)

    LOGGER.info("Layout doc: %s", layout_doc_path)
    schema, raw_labels = extract_schemas_from_doc(layout_doc_path)
    _emit_schema_summary(schema, raw_labels)

    summary = parse_irs_527_file(input_path=input_path, outdir=outdir, schema=schema)
    LOGGER.info("Parse complete: total_lines=%s quarantine_rows=%s", summary["total_lines"], summary["quarantine_rows"])
    for record_type in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
        LOGGER.info("  %s -> %s rows", record_type, summary["record_counts"].get(record_type, 0))

    if args.load_db:
        db_path = Path(args.db_path).expanduser().resolve()
        loaded = load_csvs_to_sqlite(outdir=outdir, schema=schema, db_path=db_path)
        LOGGER.info("Loaded staging tables into %s", db_path)
        for table_name, row_count in loaded.items():
            LOGGER.info("  %s: %s rows", table_name, row_count)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

