#!/usr/bin/env python3
"""Build Illinois district geometry TopoJSON assets from Census TIGER/Line files."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPS_ROOT = Path("/Users/devin/Illinois_campaign_finance/Maps")
DEFAULT_OUTPUT_DIR = ROOT / "data" / "geometry" / "il"


@dataclass(frozen=True)
class LayerSpec:
    name: str
    source_base: str
    output_name: str
    raw_code_field: str
    placeholder_code: str
    each_expr: str
    keep_fields: tuple[str, ...]


LAYERS = (
    LayerSpec(
        name="Congressional (CD119)",
        source_base="tl_2025_17_cd119",
        output_name="il_cd119.topo.json",
        raw_code_field="CD119FP",
        placeholder_code="ZZ",
        each_expr=(
            "geoid=String(GEOID),"
            "district=parseInt(CD119FP,10),"
            "district_key=('0'+String(parseInt(CD119FP,10))).slice(-2)"
        ),
        keep_fields=("GEOID", "geoid", "CD119FP", "district_key", "district", "NAMELSAD"),
    ),
    LayerSpec(
        name="State House (SLDL)",
        source_base="tl_2025_17_sldl",
        output_name="il_sldl.topo.json",
        raw_code_field="SLDLST",
        placeholder_code="ZZZ",
        each_expr="geoid=String(GEOID),district=parseInt(SLDLST,10)",
        keep_fields=("GEOID", "geoid", "SLDLST", "district", "NAMELSAD"),
    ),
    LayerSpec(
        name="State Senate (SLDU)",
        source_base="tl_2025_17_sldu",
        output_name="il_sldu.topo.json",
        raw_code_field="SLDUST",
        placeholder_code="ZZZ",
        each_expr="geoid=String(GEOID),district=parseInt(SLDUST,10)",
        keep_fields=("GEOID", "geoid", "SLDUST", "district", "NAMELSAD"),
    ),
)


def _run(cmd: list[str]) -> None:
    print(f"+ {' '.join(shlex.quote(part) for part in cmd)}")
    subprocess.run(cmd, check=True)


def _resolve_mapshaper_prefix() -> list[str]:
    if shutil.which("mapshaper"):
        return ["mapshaper"]
    if shutil.which("npx"):
        return ["npx", "--yes", "mapshaper"]
    raise RuntimeError(
        "mapshaper was not found. Install Node.js/npm and run `npm install -g mapshaper` "
        "or use npx in PATH."
    )


def _extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(destination)


def _find_source_shp(maps_root: Path, layer: LayerSpec, unzip_root: Path) -> Path:
    zip_path = maps_root / f"{layer.source_base}.zip"
    direct_shp = maps_root / layer.source_base / f"{layer.source_base}.shp"
    if zip_path.exists():
        destination = unzip_root / layer.source_base
        _extract_zip(zip_path, destination)
        expected = destination / f"{layer.source_base}.shp"
        if expected.exists():
            return expected
        matches = sorted(destination.rglob("*.shp"))
        if matches:
            return matches[0]
        raise FileNotFoundError(f"Zip found but no .shp inside: {zip_path}")
    if direct_shp.exists():
        return direct_shp

    matches = sorted(maps_root.rglob(f"{layer.source_base}.shp"))
    if matches:
        return matches[0]

    raise FileNotFoundError(
        f"Missing source for {layer.name}. Expected one of: "
        f"{zip_path} or {direct_shp}. You can override search root with --maps-root."
    )


def _build_layer(
    mapshaper_prefix: list[str],
    layer: LayerSpec,
    shp_path: Path,
    output_path: Path,
    simplify_pct: float,
) -> None:
    filter_expr = f"String({layer.raw_code_field}) != '{layer.placeholder_code}'"
    cmd = [
        *mapshaper_prefix,
        "-i",
        str(shp_path),
        "encoding=utf8",
        "-proj",
        "wgs84",
        "-filter",
        filter_expr,
        "-each",
        layer.each_expr,
        "-filter-fields",
        ",".join(layer.keep_fields),
        "-simplify",
        "weighted",
        f"{simplify_pct}%",
        "keep-shapes",
        "-o",
        str(output_path),
        "format=topojson",
    ]
    _run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build IL district topology assets from TIGER/Line shapefiles."
    )
    parser.add_argument(
        "--maps-root",
        default=str(DEFAULT_MAPS_ROOT),
        help="Directory containing TIGER layer folders or zips.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where TopoJSON outputs will be written.",
    )
    parser.add_argument(
        "--simplify-pct",
        type=float,
        default=8.0,
        help="Topology-preserving simplification percentage retained (default: 8).",
    )
    parser.add_argument(
        "--work-dir",
        default="",
        help="Optional work directory for zip extraction.",
    )
    parser.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Do not delete temporary extraction directory.",
    )
    args = parser.parse_args()

    maps_root = Path(args.maps_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    simplify_pct = float(args.simplify_pct)
    if simplify_pct <= 0 or simplify_pct > 100:
        raise ValueError("--simplify-pct must be in (0, 100].")
    if not maps_root.exists():
        raise FileNotFoundError(f"Maps root does not exist: {maps_root}")

    mapshaper_prefix = _resolve_mapshaper_prefix()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup_work_dir = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="il-geometry-build-"))
        cleanup_work_dir = not args.keep_work_dir

    unzip_root = work_dir / "unzipped"
    unzip_root.mkdir(parents=True, exist_ok=True)

    built_paths: dict[str, Path] = {}
    try:
        for layer in LAYERS:
            source_shp = _find_source_shp(maps_root, layer, unzip_root)
            output_path = output_dir / layer.output_name
            print(f"Building {layer.name}: {source_shp} -> {output_path}")
            _build_layer(mapshaper_prefix, layer, source_shp, output_path, simplify_pct=simplify_pct)
            built_paths[layer.source_base] = output_path

        validator = ROOT / "scripts" / "validate_geometry_keys.py"
        _run(
            [
                sys.executable,
                str(validator),
                "--cd",
                str(built_paths["tl_2025_17_cd119"]),
                "--sldl",
                str(built_paths["tl_2025_17_sldl"]),
                "--sldu",
                str(built_paths["tl_2025_17_sldu"]),
            ]
        )
    finally:
        if cleanup_work_dir and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)

    print("Geometry build completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
