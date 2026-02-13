# Analytics Feature Checklist

## Requested Feature Set

- [x] 1. Network graph analytics
  - [x] Donor -> Committee weighted edges from contribution totals.
  - [x] Committee -> Candidate weighted edges when bulk candidate tables exist.
  - [x] Weighted-degree centrality output.
  - [x] Interactive network visualization with `Power Players` and `Regional View` modes.
  - [x] Chord diagram visualization with `Power Players` and `Regions` modes.
  - [x] Web section on `/analytics/` + JSON API endpoint `/api/analytics/network`.

- [x] 2. Risk and anomaly flags
  - [x] Large single contribution flags using dynamic thresholding.
  - [x] Monthly spike detection vs trailing baseline.
  - [x] High donor concentration risk flag (HHI-based).
  - [x] Web section + JSON API endpoint `/api/analytics/anomalies`.

- [x] 3. Donor concentration metrics
  - [x] Top-1, Top-5, Top-10 donor share by committee.
  - [x] HHI score by committee.
  - [x] Gini coefficient by committee.
  - [x] Web section + JSON API endpoint `/api/analytics/concentration`.

- [x] 4. Time-series intelligence
  - [x] Monthly totals and contribution counts.
  - [x] 3-month moving average.
  - [x] Month-over-month percent change.
  - [x] Web section + JSON API endpoint `/api/analytics/time-series`.

- [x] 5. Geospatial summary
  - [x] State-level aggregation (parsed from donor addresses).
  - [x] City-level aggregation (parsed from donor addresses).
  - [x] Web section + JSON API endpoint `/api/analytics/geo`.

- [x] 9. NLP spending categorization
  - [x] Keyword-based category tagging for spending text.
  - [x] Uses contributions and D-2 itemized entries when present.
  - [x] Web section + JSON API endpoint `/api/analytics/nlp`.

## Route Additions

- `/analytics/` (web dashboard)
- `/api/analytics/network`
- `/api/analytics/anomalies`
- `/api/analytics/concentration`
- `/api/analytics/time-series`
- `/api/analytics/geo`
- `/api/analytics/nlp`

## UX + Explainability Enhancements

- [x] Expanded global search beyond committees/donors:
  - [x] Candidates (state + federal)
  - [x] Reports
  - [x] Filed doc IDs
  - [x] Donor keys
- [x] Added compare mode at `/compare`:
  - [x] Candidate vs candidate
  - [x] Committee vs committee
  - [x] Donor overlap summaries and monthly trend overlay
- [x] Added row-level provenance panels in key table views.
- [x] Added anomaly explainability fields (threshold, baseline, percentile, rule context).
- [x] Added visual summary charts in analytics pages:
  - [x] Overview trend + MoM visual
  - [x] Geography state/city visual summaries
  - [x] Risk histogram + severity profile
