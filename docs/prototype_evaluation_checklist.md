# Prototype Evaluation Checklist (Viz Lab)

## What To Look For

1. **Date-window correctness**
   - Change period and/or `date_from`/`date_to`, then confirm chart values and row counts move as expected.
   - Prototypes expected to be date-aware: `race_money_pressure`, `race_concentration`, `cumulative_inflow`, `payee_dominance`, `network_slice`.
   - Prototype intentionally not date-aware yet: `entity_resolution` (snapshot aggregate table).

2. **Aggregate consistency**
   - Compare `race_money_pressure` totals with `/analytics` race totals for the same window.
   - Spot-check 1-2 `payee_dominance` rows against raw expenditure records.
   - For `cumulative_inflow`, verify latest cumulative values align with total receipts trends.

3. **Readability / story payoff**
   - Confirm outliers are obvious without extra filtering.
   - Confirm axis labels and tooltips make units explicit (USD vs %).
   - Confirm top rows in each table support the chart’s main message.

4. **Local performance**
   - Check runtime text under each prototype (`query_ms`).
   - Target is under ~2 seconds for typical local ranges.
   - Confirm second load is typically faster (`cache hit`).

## Production-Ready Candidates (Current Prototype Pass)

1. **Strong candidates after validation**
   - `race_money_pressure`
   - `cumulative_inflow`
   - `payee_dominance`

2. **Needs additional semantics review**
   - `race_concentration` (uses top-candidate share as concentration proxy)

3. **Needs additional data-quality workflow**
   - `entity_resolution` (good operational panel, but date-window support still pending)

4. **Experimental only (Phase C)**
   - `network_slice` (bounded and safe; advanced metrics now explicit opt-in via "Compute Advanced Metrics")

## Performance Risks And Mitigations

1. **Risk: wide-window scans on receipts/expenditures**
   - Mitigation: bounded race/payee limits, set-based SQL CTEs, endpoint cache keyed by period/date/filter.

2. **Risk: large network payloads / expensive graph metrics**
   - Mitigation: edge/node caps plus threshold controls (`edge_threshold`, `edge_limit`, `node_cap`), explicit advanced compute action, and bounded `k` pivots by mode (`fast`/`safe`).

3. **Risk: table render overload**
   - Mitigation: per-prototype pagination (`page`, `per_page`), top-N chart caps.

4. **Risk: stale interpretation for snapshot-only panels**
   - Mitigation: explicitly label `entity_resolution` as snapshot-based and non-date-window for now.
