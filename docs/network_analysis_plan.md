# Network Analysis Plan

## Goal
Extend the existing relationship graphs into a metrics-driven network lab without heavy first-pass compute.

## Graph Model

### Node Types
- `donor` from `analytics_donor_summary` / `analytics_donor_committee_agg` (`donor_key`)
- `committee` from `bulk_committees_clean` and `analytics_donor_committee_agg` (`committee_id`)
- `candidate_state` from `bulk_candidates_clean` (`candidate_id`)
- `candidate_federal` from `fec_candidate_match` / `fec_schedule_a_contributions` (`candidate_id`) when available
- `lobbying_client` from `lobbying_clients` (`client_id`)
- `lobbying_entity` from `lobbying_entities` (`entity_id`)
- `payee` from `bulk_expenditures_clean` / `lobbying_expenditure_matches` (stable normalized payee key)
- `irs527_org` from `irs527_organizations` (`ein`)
- `director` from `irs527_director_donor_matches` (stable normalized director key)

### Edge Types and Weights
- `donor -> committee` (`analytics_donor_committee_agg.total_amount`, unit USD)
- `committee -> candidate_state` (receipt allocation from committee totals, unit USD estimate)
- `client -> entity` (`lobbying_entity_clients` count of registration years)
- `client -> donor` (`lobbying_donor_matches.score`, unit match-score)
- `entity/client -> payee` (`lobbying_expenditure_matches.score`, with optional matched spend USD)
- `donor -> committee (federal)` from `fec_schedule_a_contributions` (USD), when available
- `org527 -> committee` (`irs527_committee_matches.score`)
- `org527 -> recipient` (`irs527_expenditure_recipient_matches` score, upgraded to USD when matched spend exists)
- `director -> donor` (`irs527_director_donor_matches.score`)

## Metrics (Minimum Set)
- `Degree` and `Weighted Degree`:
  - Already present and should remain the fast default metric.
- `Approximate Betweenness Centrality`:
  - Compute on sampled source nodes only (`k` pivots) to avoid all-pairs shortest paths.
- `Community Detection`:
  - Louvain/Leiden on undirected projection per selected edge types.
- `Bridge Ratio` (recommended extra):
  - Share of a node's weighted edges that cross system boundaries (state/federal/lobbying/527 layers).
- `Edge Persistence` (recommended extra):
  - Fraction of periods where an edge remains active in rolling windows.

## Small-Sample-First Compute Plan

### Phase 1: Fast Prototype (Recommended)
- Build induced subgraph from top-weight edges only:
  - Example cap: `<= 800` nodes, `<= 3,000` edges.
- Compute:
  - weighted degree (exact)
  - approximate betweenness (`k=64` sampled pivots)
  - Louvain communities (single run)
- Runtime target: under 10 seconds on local machine for one graph slice.

### Phase 2: Controlled Expansion
- Raise cap gradually to `<= 2,000` nodes, `<= 10,000` edges.
- Add bootstrap stability checks for community labels (3-5 seeds).
- Cache metric outputs by:
  - edge-type set
  - date window
  - threshold bundle

### Phase 3: Optional Heavy Mode (Ask First)
- Full-graph betweenness or high-resolution temporal slices.
- Only run with explicit user approval due cost/runtime.

## UI Exploration Plan
- Add an experimental network workbench at `/experimental/network-lab` (feature flag).
- Controls:
  - edge-type toggles
  - min weight / min match score
  - date range (global filter compatible)
  - top-N node/edge caps
- Views:
  - force graph
  - arc diagram
  - matrix heatmap
  - community summary table
- Interaction:
  - ego mode (1-hop/2-hop)
  - shortest path between selected nodes
  - pin + compare two nodes (shared neighbors, shared money, bridge score)
- Explainability panel:
  - display edge provenance (`source_table`, metric unit, confidence semantics)

## Data Contract Additions (API)
- Add metrics payload per graph response:
  - `node_metrics`: degree, weighted degree, betweenness_approx, community_id, bridge_ratio
  - `graph_meta`: node/edge caps, sample parameters, compute_ms, cache_hit
- Keep response bounded:
  - default `max_edges <= 1500`
  - default `max_nodes <= 1000`

## Risks and Mitigations
- Mixed units (USD vs match score) in one graph can mislead rankings.
  - Mitigation: metric-by-edge-type and explicit unit labels.
- Community output instability across sparse graphs.
  - Mitigation: minimum edge threshold and stability scoring.
- Over-dense layouts reduce usability.
  - Mitigation: strongest-edge mode and community-collapse mode by default.
