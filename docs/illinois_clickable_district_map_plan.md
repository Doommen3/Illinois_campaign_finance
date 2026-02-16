# Illinois Clickable District Map Plan (Local + Federal)

## Objective
Add a clickable district map that drives existing analytics filters for both:
- Local/state districts (`Representative`, `Senate`)
- Federal districts (`U.S. House`), plus statewide overlays for `U.S. Senate`

## Geometry Data Sources

| Layer | Suggested Source | Join Key Target | Notes |
|---|---|---|---|
| Illinois U.S. House districts | U.S. Census TIGER/Line Congressional Districts (`CD`) | federal `office_code='H'` + `district_code` | Keep district numbers as zero-padded 2-digit strings (`01`-`17`). |
| Illinois State House districts | U.S. Census TIGER/Line State Legislative Lower (`SLDL`) | local `district_type='Representative'` + district number | Illinois has 118 lower-chamber districts. |
| Illinois State Senate districts | U.S. Census TIGER/Line State Legislative Upper (`SLDU`) | local `district_type='Senate'` + district number | Illinois has 59 upper-chamber districts. |
| Statewide outline (for Senate/Governor overlays) | U.S. Census TIGER/Line state boundary (`STATE`) | office-level statewide views | Use as fallback where district is not applicable. |

## Join Keys and Normalization

### Federal
- Source fields:
  - `fec_candidate_match.office_code`
  - `fec_candidate_match.district_code`
  - fallback from `fec_schedule_a_contributions` + candidate race metadata
- Normalization rules:
  - office `H`: parse numeric district and zero-pad (`1` -> `01`)
  - office `S` and `P`: map to `STATEWIDE`

### Local/State
- Source fields:
  - `bulk_candidates_clean.district_type`
  - `bulk_candidates_clean.district`
  - or `bulk_candidate_committee_finance_agg` equivalents
- Normalization rules:
  - `district_type='Representative'`: integer `1..118`
  - `district_type='Senate'`: integer `1..59`
  - `district_type='Congressional'`: integer `1..17` (local source occasionally contains out-of-range values)

### Data Quality Notes from Current DB Snapshot
- Out-of-range/non-numeric district examples currently exist and should be excluded or flagged before map joins:
  - Congressional: `18`, `40`, `698`, `Central`, `Cook`
  - Representative: `Cook`, `Dupage`
  - Senate: `113`

## Proposed Data Contract
- Add a lightweight, cacheable district summary endpoint:
  - `/api/geo/district-summary?system=local|federal&chamber=house|senate&period=2026cycle`
- Payload shape:
  - `district_key` (for geometry join)
  - `label`
  - `total_amount`
  - `donor_count`
  - `candidate_count`
  - `outside_spending_total` (when available)
  - `lobbying_client_count` (optional overlay)

## Tech Options: Leaflet/Mapbox vs D3

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| Leaflet + TopoJSON/GeoJSON | Quick integration, simple click/hover behavior, good for choropleth interactions | Less flexible for custom network overlays and animated transitions | Best fast path for an `/experimental` district map |
| Mapbox GL (vector tiles) | Best performance at scale, smooth zoom, style control | More setup complexity and token/deployment overhead | Use only if map scope expands to many local geographies |
| D3/SVG | Full custom control and consistency with existing SVG graph stack | Harder pan/zoom and worse performance with large geometry payloads | Good for static district small multiples, not primary interactive map |

Recommended start: `Leaflet + pre-simplified TopoJSON`.

## Caching and Bundle-Size Plan
- Pre-build simplified TopoJSON assets per layer (`cd`, `sldu`, `sldl`) and serve as static versioned files.
- Keep two geometry tiers:
  - `low` simplification for overview
  - `high` simplification for zoomed detail
- Avoid shipping all geometry at once:
  - lazy-load by selected system/chamber
- Cache strategy:
  - static assets: long-lived immutable cache headers (`filename hash`)
  - metric API: server-side TTL cache keyed by `period + chamber + filters`
- Target payload sizes:
  - per layer compressed (brotli/gzip) ideally `< 300 KB` for overview tier.

## Incremental Delivery Plan (Doc-First)
1. Build district normalization SQL/view and QA report (no UI yet).
2. Add one experimental route: `/experimental/district-map` behind feature flag.
3. Ship local state chambers first (`Representative`, `Senate`).
4. Add federal House and statewide overlays once `fec_*` data is present locally.
5. Integrate map click -> existing analytics pages via query params (`analysis_office`, `analysis_district`, `period`).
