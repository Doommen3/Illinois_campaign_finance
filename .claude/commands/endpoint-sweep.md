---
description: Curl-sweep the critical production routes and report HTTP status codes + timing.
---

Sweep these critical production routes via curl against `http://178.156.162.56` and report a markdown table of results.

Routes:

```
/
/search?q=Chicago
/candidates
/federal-finance/
/analytics/
/lobbying/
/527/
/federal-finance/geo-drilldown?geo_type=state&geo_value=IL&cycle=2026
/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all
/federal-finance/races/H/01/outside-spending?cycle=2026
/527/dark-money
```

Use a single curl loop with this format:

```bash
HOST=http://178.156.162.56
for path in \
  "/" \
  "/search?q=Chicago" \
  "/candidates" \
  "/federal-finance/" \
  "/analytics/" \
  "/lobbying/" \
  "/527/" \
  "/federal-finance/geo-drilldown?geo_type=state&geo_value=IL&cycle=2026" \
  "/analytics/geo-drilldown?geo_type=state&geo_value=IL&period=all" \
  "/federal-finance/races/H/01/outside-spending?cycle=2026" \
  "/527/dark-money"; do
  printf "%s\t%s\n" "$path" "$(curl -sS -o /dev/null -w '%{http_code} %{size_download}B %{time_total}s' --max-time 30 "$HOST$path" || echo 'TIMEOUT')"
done
```

Report results as a table: route | status | size | time. Flag anything non-200 (or timeout) at the top with a brief note (likely cause: cold cache for analytics routes; server down for all-fail; bad deploy for partial-fail). Do not investigate failures unless the user asks — just report.

If `$ARGUMENTS` is non-empty, treat it as a host override (e.g., `/endpoint-sweep http://127.0.0.1:5000` to sweep local instead).
