#!/usr/bin/env python3
"""Validate required join keys and typing in IL district geometry assets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TWO_DIGIT_RE = re.compile(r"^\d{2}$")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GEOMETRY_DIR = ROOT / "data" / "geometry" / "il"


@dataclass(frozen=True)
class LayerValidationSpec:
    key: str
    path: Path
    required_fields: tuple[str, ...]
    raw_code_field: str
    district_min: int
    district_max: int
    district_key_required: bool


def _load_properties(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    doc_type = data.get("type")

    if doc_type == "Topology":
        objects = data.get("objects") or {}
        if not isinstance(objects, dict) or not objects:
            raise ValueError("TopoJSON contains no objects.")
        properties: list[dict] = []
        for obj in objects.values():
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "GeometryCollection":
                geometries = obj.get("geometries") or []
            else:
                geometries = [obj]
            for geom in geometries:
                if isinstance(geom, dict):
                    properties.append(dict(geom.get("properties") or {}))
        return properties

    if doc_type == "FeatureCollection":
        return [dict(feature.get("properties") or {}) for feature in data.get("features") or []]

    if doc_type == "Feature":
        return [dict(data.get("properties") or {})]

    raise ValueError(f"Unsupported geometry format type: {doc_type}")


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
    return None


def _validate_layer(spec: LayerValidationSpec) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not spec.path.exists():
        return False, [f"Missing file: {spec.path}"]

    try:
        props = _load_properties(spec.path)
    except Exception as exc:
        return False, [f"Failed to read {spec.path}: {exc}"]

    if not props:
        return False, [f"No features found in {spec.path}"]

    seen_districts: set[int] = set()
    seen_district_keys: set[str] = set()
    for idx, row in enumerate(props, start=1):
        for field in spec.required_fields:
            if field not in row:
                errors.append(f"feature[{idx}] missing required field '{field}'")

        geoid = row.get("geoid")
        if not isinstance(geoid, str) or not geoid.strip():
            errors.append(f"feature[{idx}] has invalid geoid (must be non-empty string)")

        geoid_raw = row.get("GEOID")
        if isinstance(geoid_raw, str) and isinstance(geoid, str):
            if geoid != geoid_raw:
                errors.append(f"feature[{idx}] geoid '{geoid}' does not match GEOID '{geoid_raw}'")

        district = _coerce_int(row.get("district"))
        if district is None:
            errors.append(f"feature[{idx}] has non-integer district value: {row.get('district')!r}")
            continue
        if district < spec.district_min or district > spec.district_max:
            errors.append(
                f"feature[{idx}] district {district} out of range "
                f"{spec.district_min}..{spec.district_max}"
            )
        if district in seen_districts:
            errors.append(f"feature[{idx}] duplicate district value {district}")
        seen_districts.add(district)

        raw_value = row.get(spec.raw_code_field)
        if not isinstance(raw_value, str) or not raw_value.strip():
            errors.append(f"feature[{idx}] has invalid {spec.raw_code_field}: {raw_value!r}")

        if spec.district_key_required:
            district_key = row.get("district_key")
            if not isinstance(district_key, str) or not TWO_DIGIT_RE.fullmatch(district_key):
                errors.append(
                    f"feature[{idx}] district_key must be a two-digit string, got {district_key!r}"
                )
            else:
                district_from_key = int(district_key)
                if district_from_key < spec.district_min or district_from_key > spec.district_max:
                    errors.append(
                        f"feature[{idx}] district_key {district_key} out of range "
                        f"{spec.district_min:02d}..{spec.district_max:02d}"
                    )
                if district_key in seen_district_keys:
                    errors.append(f"feature[{idx}] duplicate district_key {district_key}")
                seen_district_keys.add(district_key)
                if district != district_from_key:
                    errors.append(
                        f"feature[{idx}] district ({district}) does not match district_key ({district_key})"
                    )

    if errors:
        return False, errors

    return True, [f"{spec.key}: OK ({len(props)} features)"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate IL district geometry join keys.")
    parser.add_argument("--cd", default=str(DEFAULT_GEOMETRY_DIR / "il_cd119.topo.json"))
    parser.add_argument("--sldl", default=str(DEFAULT_GEOMETRY_DIR / "il_sldl.topo.json"))
    parser.add_argument("--sldu", default=str(DEFAULT_GEOMETRY_DIR / "il_sldu.topo.json"))
    args = parser.parse_args()

    specs = (
        LayerValidationSpec(
            key="Congressional (CD119)",
            path=Path(args.cd).expanduser().resolve(),
            required_fields=("GEOID", "geoid", "CD119FP", "district_key", "district"),
            raw_code_field="CD119FP",
            district_min=1,
            district_max=17,
            district_key_required=True,
        ),
        LayerValidationSpec(
            key="State House (SLDL)",
            path=Path(args.sldl).expanduser().resolve(),
            required_fields=("GEOID", "geoid", "SLDLST", "district"),
            raw_code_field="SLDLST",
            district_min=1,
            district_max=118,
            district_key_required=False,
        ),
        LayerValidationSpec(
            key="State Senate (SLDU)",
            path=Path(args.sldu).expanduser().resolve(),
            required_fields=("GEOID", "geoid", "SLDUST", "district"),
            raw_code_field="SLDUST",
            district_min=1,
            district_max=59,
            district_key_required=False,
        ),
    )

    any_failures = False
    for spec in specs:
        ok, messages = _validate_layer(spec)
        if ok:
            print(f"[OK] {messages[0]}")
        else:
            any_failures = True
            print(f"[FAIL] {spec.key}")
            for message in messages:
                print(f"  - {message}")

    if any_failures:
        return 1

    print("Geometry validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
