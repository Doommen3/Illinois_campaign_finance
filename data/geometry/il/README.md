# Illinois District Geometry Assets

This folder stores production-ready Illinois district geometry assets used by maps in this app.

## Source

Source data comes from U.S. Census Bureau TIGER/Line "current" shapefiles (2025 vintage, Illinois):

- `tl_2025_17_cd119` (Congressional districts for 119th Congress)
- `tl_2025_17_sldl` (State House / lower chamber)
- `tl_2025_17_sldu` (State Senate / upper chamber)

## Output Files

- `il_cd119.topo.json`
- `il_sldl.topo.json`
- `il_sldu.topo.json`

## Join Keys

- Congressional (`il_cd119.topo.json`):
  - `district_key` (string): zero-padded `"01"`..`"17"` for joins keyed as strings.
  - `district` (integer): `1`..`17`.
  - `geoid` (string): stable identifier from TIGER `GEOID`.
  - Raw field retained: `CD119FP`.
- State House (`il_sldl.topo.json`):
  - `district` (integer): `1`..`118`.
  - `geoid` (string): stable identifier from TIGER `GEOID`.
  - Raw field retained: `SLDLST`.
- State Senate (`il_sldu.topo.json`):
  - `district` (integer): `1`..`59`.
  - `geoid` (string): stable identifier from TIGER `GEOID`.
  - Raw field retained: `SLDUST`.

`GEOID` is also retained in outputs for traceability.

## Cleaning Rules

Placeholder records are dropped during build:

- Congressional: `CD119FP == "ZZ"`
- State House: `SLDLST == "ZZZ"`
- State Senate: `SLDUST == "ZZZ"`

## Rebuild

From repo root:

```bash
python3 scripts/build_geometry.py --maps-root /Users/devin/Illinois_campaign_finance/Maps
```

Optional:

```bash
python3 scripts/build_geometry.py \
  --maps-root /Users/devin/Illinois_campaign_finance/Maps \
  --output-dir data/geometry/il \
  --simplify-pct 8
```

Validation only:

```bash
python3 scripts/validate_geometry_keys.py
```

## Assumptions

- TIGER sources are read from either unzipped folders or same-name `.zip` files in `--maps-root`.
- Geometry is reprojected to WGS84 (`-proj wgs84`) during conversion.
- Simplification uses mapshaper weighted topology-preserving simplification at `8%` with `keep-shapes` for web performance while retaining district integrity.
