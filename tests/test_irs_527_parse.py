from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.irs_527_parse import (
    OUTPUT_FILES,
    QUARANTINE_FILE,
    extract_schemas_from_doc,
    load_csvs_to_sqlite,
    parse_irs_527_file,
    resolve_layout_doc_path,
)


def _local_layout_doc() -> Path:
    return Path("Bulk_download/IRS_data/PolOrgsFileLayout.doc")


def _build_valid_line(record_type: str, field_count: int) -> str:
    values = [record_type] + [f"{record_type}_{idx}" for idx in range(1, field_count)]
    return "|".join(values) + "|"


def test_extract_schemas_from_doc_has_required_record_types():
    doc_path = _local_layout_doc()
    if not doc_path.exists():
        pytest.skip("PolOrgsFileLayout.doc not available in this environment")

    schema, raw_labels = extract_schemas_from_doc(doc_path)

    assert set(OUTPUT_FILES.keys()) == set(schema.keys())
    assert set(OUTPUT_FILES.keys()) == set(raw_labels.keys())

    # Required field evidence checks (from doc descriptions).
    assert "contributor_name" in schema["A"]
    assert "contributor_address_1" in schema["A"]
    assert "contributor_employer" in schema["A"]
    assert "agg_contribution_ytd" in schema["A"]
    assert "contribution_date" in schema["A"]

    assert "recipient_name" in schema["B"]
    assert "recipient_address_1" in schema["B"]
    assert "recipient_employer" in schema["B"]
    assert "recipient_occupation" in schema["B"]
    assert "expenditure_purpose" in schema["B"]

    assert "eain_id" in schema["E"]
    assert "election_authority_id_number" in schema["E"]
    assert "state_issued" in schema["E"]

    # Sanity check a few raw labels to ensure parser is reading the doc tables.
    assert raw_labels["H"][0] == "Record Type Code"
    assert raw_labels["A"][0] == "Record Type"
    assert raw_labels["F"][-1] == "Record Count"


def test_parse_routes_records_to_correct_output_and_writes_headers(tmp_path: Path):
    doc_path = _local_layout_doc()
    if not doc_path.exists():
        pytest.skip("PolOrgsFileLayout.doc not available in this environment")

    schema, _raw_labels = extract_schemas_from_doc(doc_path)

    input_lines: list[str] = []
    for record_type in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
        input_lines.append(_build_valid_line(record_type, len(schema[record_type])))

    # One mismatch (extra column) + one unknown type to validate quarantine.
    bad_a_values = ["A"] + [f"A_bad_{idx}" for idx in range(1, len(schema["A"]))] + ["EXTRA_COLUMN"]
    input_lines.append("|".join(bad_a_values) + "|")
    input_lines.append("Z|not|a|known|record|")

    input_path = tmp_path / "irs_527_sample.txt"
    input_path.write_text("\n".join(input_lines) + "\n", encoding="utf-8")

    outdir = tmp_path / "parsed"
    summary = parse_irs_527_file(input_path=input_path, outdir=outdir, schema=schema)

    assert summary["quarantine_rows"] == 2
    for record_type in ("H", "1", "D", "R", "E", "2", "A", "B", "F"):
        assert summary["record_counts"].get(record_type, 0) == 1

    for record_type, filename in OUTPUT_FILES.items():
        csv_path = outdir / filename
        assert csv_path.exists(), f"missing output file {csv_path}"
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows, f"expected header row in {csv_path}"
        assert rows[0] == schema[record_type]
        assert len(rows) == 2  # header + one valid data row
        assert len(rows[1]) == len(schema[record_type])

    quarantine_path = outdir / QUARANTINE_FILE
    with quarantine_path.open("r", encoding="utf-8", newline="") as handle:
        quarantine_rows = list(csv.DictReader(handle))

    assert len(quarantine_rows) == 2
    reasons = " | ".join(row["reason"] for row in quarantine_rows)
    assert "column_count_mismatch" in reasons
    assert "unknown_record_type" in reasons


def test_optional_sqlite_staging_load(tmp_path: Path):
    doc_path = _local_layout_doc()
    if not doc_path.exists():
        pytest.skip("PolOrgsFileLayout.doc not available in this environment")

    schema, _raw_labels = extract_schemas_from_doc(doc_path)

    input_lines = [
        _build_valid_line("H", len(schema["H"])),
        _build_valid_line("1", len(schema["1"])),
        _build_valid_line("D", len(schema["D"])),
        _build_valid_line("R", len(schema["R"])),
        _build_valid_line("E", len(schema["E"])),
        _build_valid_line("2", len(schema["2"])),
        _build_valid_line("A", len(schema["A"])),
        _build_valid_line("B", len(schema["B"])),
        _build_valid_line("F", len(schema["F"])),
    ]
    input_path = tmp_path / "irs_527_db_sample.txt"
    input_path.write_text("\n".join(input_lines) + "\n", encoding="utf-8")

    outdir = tmp_path / "parsed"
    parse_irs_527_file(input_path=input_path, outdir=outdir, schema=schema)

    db_path = tmp_path / "stage.db"
    loaded = load_csvs_to_sqlite(outdir=outdir, schema=schema, db_path=db_path)

    assert loaded["irs527_stage_organizations_8871"] == 1
    assert loaded["irs527_stage_reports_8872"] == 1
    assert loaded["irs527_stage_contributions_sched_a"] == 1
    assert loaded["irs527_stage_expenditures_sched_b"] == 1
    assert loaded["irs527_stage_quarantine_bad_rows"] == 0


def test_resolve_layout_doc_path_prefers_explicit(tmp_path: Path):
    explicit = tmp_path / "fake_layout.doc"
    explicit.write_text("placeholder", encoding="utf-8")
    resolved = resolve_layout_doc_path(str(explicit))
    assert resolved == explicit.resolve()

