"""Production route sweep.

Enumerates Flask routes from app.url_map, fires GET requests at a configured
host (default: production), and reports status codes, response times, payload
sizes, plus heuristic empty/error markers.

Usage:
    DATABASE_URL=postgresql://devin@localhost/ilcf \\
        .venv/bin/python3 scripts/audit/route_sweep.py \\
        --host https://followthemoneyil.com \\
        --output docs/audits/route_sweep_$(date +%Y-%m-%d).md

The script is read-only (HTTP GET only). It refuses to send anything but GET.
For parameterized routes, seed IDs are pulled from the local DB if available;
otherwise the route is skipped with a note.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

EMPTY_MARKERS = (
    "no data",
    "no results",
    "no matching",
    "nothing to show",
    "0 results",
    "no records",
    "no rows",
    "<tbody></tbody>",
    "<tbody>\n</tbody>",
)

ERROR_MARKERS = (
    "internal server error",
    "traceback (most recent call last)",
    "500 internal server error",
    "<title>500",
    "<title>error",
)


@dataclass
class RouteResult:
    rule: str
    endpoint: str
    url: str
    status: int | None
    elapsed_ms: float
    payload_bytes: int
    period: str
    empty_markers: list[str] = field(default_factory=list)
    error_markers: list[str] = field(default_factory=list)
    note: str = ""

    def category(self) -> str:
        if self.note == "skipped":
            return "skipped"
        if self.status is None:
            return "network-error"
        if self.status >= 500:
            return "5xx"
        if self.status >= 400:
            # 401s on /api/* are expected — the sweep doesn't authenticate.
            # Bucket them separately so the 4xx list shows genuine breakage only.
            if self.status == 401 and self.rule.startswith("/api/"):
                return "auth-gated"
            return "4xx"
        if self.status >= 300:
            return "3xx"
        if self.error_markers:
            return "200-with-error"
        if self.empty_markers:
            return "200-empty"
        return "200-ok"


def enumerate_routes() -> list[tuple[str, str]]:
    """Return [(rule, endpoint), ...] for all GET routes registered on the app."""
    os.environ.setdefault("DATABASE_URL", "postgresql://devin@localhost/ilcf")
    from webapp.app import create_app  # type: ignore

    app = create_app()
    rules = []
    for rule in app.url_map.iter_rules():
        methods = rule.methods or set()
        if "GET" not in methods:
            continue
        if rule.endpoint == "static":
            continue
        rules.append((rule.rule, rule.endpoint))
    return sorted(rules)


def fetch_seed_ids() -> dict[str, list[str]]:
    """Pull a small handful of real IDs from local DB to seed parameterized routes.

    Returns dict keyed by canonical param-name with list of representative values.
    Silently returns empty lists if the DB / table is unavailable; the sweep then
    skips the parameterized routes.

    Schema notes (post-ISBE migration):
    - Legacy `committees`/`donors`/`reports` tables are empty; canonical IDs come
      from `isbe_committees` / `analytics_donor_committee_agg` / `isbe_filed_docs`.
      The detail routes fall back to the ISBE id when the legacy lookup misses.
    - Donor `donor_key` lives on `analytics_donor_committee_agg`, not a
      `donors_canonical` table.
    - Lobbying tables key on `entity_id` / `client_id`, not `id`.
    - Federal candidate/committee IDs live in `fec_candidate_committees`.
    - Federal donor entity keys + match pairs live in `fec_local_donor_matches`.
    - Federal race office/district is on `fec_schedule_e_independent_expenditures.
      candidate_office` / `candidate_office_district`.
    - State-race detail slugs are hashed; we build one from the local DB via
      `database.analytics._state_race_slug` so the sweep gets a hit instead of a
      404. Falls back to "no seed" if `_state_race_slug` isn't importable.
    """
    seeds: dict[str, list[str]] = {
        "committee_id": [],
        "committee_id_sbe": [],
        "filed_doc_id": [],
        "donor_id": [],
        "donor_key": [],
        "entity_id": [],
        "report_id": [],
        "candidate_id": [],
        "candidate_committee": [],  # tuple (candidate_id, committee_id_sbe)
        "lobbying_entity_id": [],
        "lobbying_client_id": [],
        "irs527_ein": [],
        "openbook_vendor_key": [],
        "federal_candidate_id": [],
        "federal_committee_id": [],
        "federal_donor_entity_key": [],
        "federal_match_pair": [],  # (federal_donor_entity_key, local_donor_key)
        "federal_race": [],  # (office_code, district_code)
        "state_race_key": [],
    }
    try:
        from database.connection import get_db  # type: ignore
    except Exception:
        return seeds

    try:
        conn = get_db(os.environ.get("DATABASE_URL", "postgresql://devin@localhost/ilcf"))
    except Exception as exc:
        print(f"[seed] DB unreachable; parameterized routes will be skipped ({exc})", file=sys.stderr)
        return seeds

    def _try(sql: str) -> list[Any]:
        try:
            return list(conn.execute(sql).fetchall())
        except Exception:
            return []

    # committee_id: route falls back to isbe_committees.id when the legacy row
    # is missing, so the isbe id is a valid seed for both legacy + ISBE paths.
    for row in _try("SELECT id FROM isbe_committees WHERE id IS NOT NULL ORDER BY id LIMIT 3"):
        seeds["committee_id"].append(str(row[0]))
        seeds["committee_id_sbe"].append(str(row[0]))

    for row in _try("SELECT id FROM isbe_filed_docs WHERE id IS NOT NULL ORDER BY id LIMIT 2"):
        seeds["filed_doc_id"].append(str(row[0]))

    # donor_id: legacy `donors` table is empty post-migration; the donor_id
    # route 404s for everything. Skip rather than fabricate a bogus seed.
    # donor_key: pull from analytics rollup (the canonical donor identity).
    for row in _try(
        "SELECT donor_key FROM analytics_donor_committee_agg "
        "WHERE donor_key IS NOT NULL AND donor_key <> '' "
        "ORDER BY total_amount DESC NULLS LAST LIMIT 2"
    ):
        seeds["donor_key"].append(str(row[0]))

    # entity_id (merged-donor entities): donor_entity_local is empty locally
    # post-migration. Leave unseeded.

    for row in _try("SELECT id FROM reports WHERE id IS NOT NULL ORDER BY id LIMIT 2"):
        seeds["report_id"].append(str(row[0]))

    for row in _try(
        "SELECT candidate_id FROM bulk_candidate_committee_finance_agg "
        "WHERE candidate_id IS NOT NULL ORDER BY sum_total_receipts DESC NULLS LAST LIMIT 3"
    ):
        seeds["candidate_id"].append(str(row[0]))
    for row in _try(
        "SELECT candidate_id, committee_id_sbe FROM bulk_candidate_committee_finance_agg "
        "WHERE candidate_id IS NOT NULL AND committee_id_sbe IS NOT NULL "
        "ORDER BY sum_total_receipts DESC NULLS LAST LIMIT 3"
    ):
        seeds["candidate_committee"].append(f"{row[0]}/{row[1]}")

    for row in _try("SELECT entity_id FROM lobbying_entities WHERE entity_id IS NOT NULL ORDER BY entity_id LIMIT 2"):
        seeds["lobbying_entity_id"].append(str(row[0]))
    for row in _try("SELECT client_id FROM lobbying_clients WHERE client_id IS NOT NULL ORDER BY client_id LIMIT 2"):
        seeds["lobbying_client_id"].append(str(row[0]))

    for row in _try("SELECT ein FROM irs527_organizations WHERE ein IS NOT NULL LIMIT 2"):
        seeds["irs527_ein"].append(str(row[0]))
    for row in _try("SELECT openbook_vendor_key FROM openbook_vendor_match WHERE openbook_vendor_key IS NOT NULL LIMIT 2"):
        seeds["openbook_vendor_key"].append(str(row[0]))

    for row in _try("SELECT DISTINCT candidate_id FROM fec_candidate_committees WHERE candidate_id IS NOT NULL LIMIT 2"):
        seeds["federal_candidate_id"].append(str(row[0]))
    for row in _try("SELECT DISTINCT committee_id FROM fec_candidate_committees WHERE committee_id IS NOT NULL LIMIT 2"):
        seeds["federal_committee_id"].append(str(row[0]))
    for row in _try(
        "SELECT DISTINCT federal_donor_entity_key FROM fec_local_donor_matches "
        "WHERE federal_donor_entity_key IS NOT NULL LIMIT 2"
    ):
        seeds["federal_donor_entity_key"].append(str(row[0]))
    for row in _try(
        "SELECT federal_donor_entity_key, local_donor_key FROM fec_local_donor_matches "
        "WHERE federal_donor_entity_key IS NOT NULL AND local_donor_key IS NOT NULL LIMIT 2"
    ):
        seeds["federal_match_pair"].append(f"{row[0]}/{row[1]}")
    for row in _try(
        "SELECT DISTINCT candidate_office, candidate_office_district "
        "FROM fec_schedule_e_independent_expenditures "
        "WHERE candidate_office IS NOT NULL AND candidate_office_district IS NOT NULL LIMIT 2"
    ):
        seeds["federal_race"].append(f"{row[0]}/{row[1]}")

    # state_race_key: slugs are hashed (`<office>-<district_type>-<digest>`), so
    # plain strings like "governor" 404. Build one from the local DB.
    try:
        from database.analytics import _state_race_slug  # type: ignore

        candidate_rows = _try(
            "SELECT office_sought, district_type, district FROM bulk_candidate_committee_finance_agg "
            "WHERE office_sought IS NOT NULL ORDER BY sum_total_receipts DESC NULLS LAST LIMIT 2"
        )
        for row in candidate_rows:
            office_sought = (row[0] or "").strip() or "Unknown Office"
            district_type = (row[1] or "").strip() or "Unknown District Type"
            district = (row[2] or "").strip()
            seeds["state_race_key"].append(_state_race_slug(office_sought, district_type, district))
    except Exception:
        pass

    return seeds


def expand_route(rule: str, seeds: dict[str, list[str]]) -> list[str]:
    """Return concrete URL paths for a rule, filling in placeholders from seeds.

    Skips a rule (returns []) if a placeholder can't be filled.
    """
    if "<" not in rule:
        return [rule]

    # Two-placeholder candidate-finance routes
    if "<int:candidate_id>/<int:committee_id>" in rule:
        out = []
        for combo in seeds["candidate_committee"][:1]:
            out.append(rule.replace("<int:candidate_id>/<int:committee_id>", combo))
        return out

    if "<federal_donor_entity_key>/<path:local_donor_key>" in rule:
        out = []
        for combo in seeds["federal_match_pair"][:1]:
            out.append(rule.replace("<federal_donor_entity_key>/<path:local_donor_key>", combo))
        return out

    if "<office_code>/<district_code>/outside-spending" in rule:
        out = []
        for combo in seeds["federal_race"][:1]:
            out.append(rule.replace("<office_code>/<district_code>", combo))
        return out

    mapping = {
        "<int:committee_id>": seeds["committee_id"],
        "<int:committee_id_sbe>": seeds["committee_id_sbe"],
        "<int:filed_doc_id>": seeds["filed_doc_id"],
        "<int:donor_id>": seeds["donor_id"],
        "<path:donor_key>": seeds["donor_key"],
        "<path:entity_id>": seeds["entity_id"],
        "<int:report_id>": seeds["report_id"],
        "<int:candidate_id>": seeds["candidate_id"],
        "<int:entity_id>": seeds["lobbying_entity_id"],
        "<int:client_id>": seeds["lobbying_client_id"],
        "<path:ein>": seeds["irs527_ein"],
        "<path:vendor_key>": seeds["openbook_vendor_key"],
        "<candidate_id>": seeds["federal_candidate_id"],
        "<committee_id>": seeds["federal_committee_id"],
        "<donor_entity_key>": seeds["federal_donor_entity_key"],
        "<race_key>": seeds["state_race_key"],
        "<prototype_key>": ["triple-pipeline", "state-maps"],
        "<layer_key>": ["cd119", "sldl", "sldu"],
        "<int:queue_id>": [],  # manual entry requires login; skip
    }

    for placeholder, values in mapping.items():
        if placeholder in rule:
            if not values:
                return []
            substituted = rule.replace(placeholder, values[0])
            if "<" in substituted:
                return []  # nested placeholder we can't fill
            return [substituted]

    return []


def make_request(url: str) -> tuple[int | None, float, bytes, str]:
    """GET the URL. Returns (status_code, elapsed_seconds, body_bytes, note)."""
    ctx = ssl.create_default_context()
    req = urlrequest.Request(url, method="GET", headers={
        "User-Agent": "ilcf-route-sweep/1.0",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
    })
    start = time.monotonic()
    try:
        with urlrequest.urlopen(req, timeout=30, context=ctx) as resp:
            body = resp.read(200_000)  # cap at 200KB
            elapsed = time.monotonic() - start
            return resp.status, elapsed, body, ""
    except urlerror.HTTPError as e:
        elapsed = time.monotonic() - start
        try:
            body = e.read(50_000)
        except Exception:
            body = b""
        return e.code, elapsed, body, ""
    except (urlerror.URLError, TimeoutError, OSError) as e:
        elapsed = time.monotonic() - start
        return None, elapsed, b"", f"net-error: {e}"


def scan_markers(body: bytes) -> tuple[list[str], list[str]]:
    text = body.decode("utf-8", errors="ignore").lower()
    # Strip whitespace-only differences for tbody check
    text_compact = re.sub(r"\s+", "", text)
    empty = []
    for marker in EMPTY_MARKERS:
        if marker in text:
            empty.append(marker)
        elif marker.replace("\n", "").replace(" ", "") in text_compact:
            empty.append(marker)
    # Also detect bare empty JSON
    stripped = text.strip()
    if stripped in ("[]", "{}", '{"data":[]}', '{"results":[]}'):
        empty.append("empty-json-payload")
    error = [m for m in ERROR_MARKERS if m in text]
    return empty, error


def sweep(host: str, routes: list[tuple[str, str]], seeds: dict[str, list[str]],
          periods: list[str], concurrency: int = 6) -> list[RouteResult]:
    expanded: list[tuple[str, str, str]] = []  # (rule, endpoint, concrete_url_path)
    skipped: list[RouteResult] = []
    for rule, endpoint in routes:
        urls = expand_route(rule, seeds)
        if not urls:
            for period in periods:
                skipped.append(RouteResult(
                    rule=rule, endpoint=endpoint, url=rule, status=None,
                    elapsed_ms=0.0, payload_bytes=0, period=period, note="skipped",
                ))
            continue
        for u in urls:
            expanded.append((rule, endpoint, u))

    tasks = []
    for rule, endpoint, path in expanded:
        for period in periods:
            sep = "&" if "?" in path else "?"
            # urlencode the path segment (but preserve / and the query separator)
            safe_path = urlparse.quote(path, safe="/?&=")
            full = f"{host.rstrip('/')}{safe_path}{sep}period={period}"
            tasks.append((rule, endpoint, full, period))

    results: list[RouteResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        future_map = {
            ex.submit(make_request, full): (rule, endpoint, full, period)
            for (rule, endpoint, full, period) in tasks
        }
        for fut in as_completed(future_map):
            rule, endpoint, full, period = future_map[fut]
            status, elapsed, body, note = fut.result()
            empty, errors = scan_markers(body) if body else ([], [])
            results.append(RouteResult(
                rule=rule, endpoint=endpoint, url=full, status=status,
                elapsed_ms=round(elapsed * 1000.0, 1),
                payload_bytes=len(body), period=period,
                empty_markers=empty, error_markers=errors, note=note,
            ))

    results.extend(skipped)
    return results


def render_markdown(results: list[RouteResult], host: str, periods: list[str]) -> str:
    by_cat: dict[str, list[RouteResult]] = {}
    for r in results:
        by_cat.setdefault(r.category(), []).append(r)

    cat_order = ["5xx", "200-with-error", "4xx", "3xx", "network-error", "200-empty", "200-ok", "auth-gated", "skipped"]

    lines: list[str] = []
    lines.append(f"# Production Route Sweep — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(f"- **Host:** `{host}`")
    lines.append(f"- **Periods tested:** {', '.join(periods)}")
    lines.append(f"- **Routes hit:** {len([r for r in results if r.note != 'skipped'])}")
    lines.append(f"- **Routes skipped (missing seeds):** {len([r for r in results if r.note == 'skipped'])}")
    lines.append("")
    lines.append("## Summary by category")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|---|---|")
    for cat in cat_order:
        if cat in by_cat:
            lines.append(f"| {cat} | {len(by_cat[cat])} |")
    lines.append("")

    for cat in cat_order:
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        lines.append(f"## {cat} ({len(rows)})")
        lines.append("")
        if cat == "auth-gated":
            lines.append(
                "_401s on `/api/*` are expected — the sweep does not authenticate. "
                "These are listed for completeness, not as breakage. See commit "
                "`c94ad2f` for the API auth gate._"
            )
            lines.append("")
        if cat == "skipped":
            lines.append("| Rule | Endpoint | Reason |")
            lines.append("|---|---|---|")
            seen = set()
            for r in sorted(rows, key=lambda x: x.rule):
                if r.rule in seen:
                    continue
                seen.add(r.rule)
                lines.append(f"| `{r.rule}` | {r.endpoint} | no seed available |")
            lines.append("")
            continue

        lines.append("| Status | ms | Bytes | Period | URL | Markers |")
        lines.append("|---:|---:|---:|---|---|---|")
        for r in sorted(rows, key=lambda x: (x.status or 0, -x.elapsed_ms)):
            markers = []
            if r.error_markers:
                markers.append("⚠ " + ",".join(r.error_markers[:2]))
            if r.empty_markers:
                markers.append("∅ " + ",".join(r.empty_markers[:2]))
            if r.note and r.note != "skipped":
                markers.append(r.note)
            lines.append(
                f"| {r.status or '-'} | {r.elapsed_ms:.0f} | {r.payload_bytes} | "
                f"{r.period} | `{r.url.replace(host, '')}` | {'; '.join(markers)} |"
            )
        lines.append("")

    # Detect "claims to filter but doesn't" — routes where 2026cycle and all return same byte size
    if len(periods) >= 2:
        lines.append("## Period-filter no-op detector")
        lines.append("")
        lines.append("Routes where the payload size is identical across `?period=2026cycle` and `?period=all`")
        lines.append("(suggests the route does not actually filter by period):")
        lines.append("")
        by_url_no_period: dict[str, dict[str, RouteResult]] = {}
        for r in results:
            if r.note == "skipped" or r.status != 200:
                continue
            base = re.sub(r"[?&]period=[^&]*", "", r.url)
            by_url_no_period.setdefault(base, {})[r.period] = r
        no_op = []
        for base, p_map in by_url_no_period.items():
            if "2026cycle" in p_map and "all" in p_map:
                a, b = p_map["2026cycle"], p_map["all"]
                if a.payload_bytes == b.payload_bytes and a.payload_bytes > 0:
                    no_op.append((base, a.payload_bytes))
        if not no_op:
            lines.append("_None detected._")
        else:
            lines.append("| URL | Bytes |")
            lines.append("|---|---:|")
            for base, size in sorted(no_op):
                lines.append(f"| `{base.replace(host, '')}` | {size} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="https://followthemoneyil.com",
                        help="Base URL of the deployment to sweep (HTTPS scheme).")
    parser.add_argument("--output", default=None, help="Markdown output path.")
    parser.add_argument("--json", default=None, help="Optional raw-JSON output path.")
    parser.add_argument("--periods", default="2026cycle,all",
                        help="Comma-separated list of period keys to test per route.")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit total HTTP requests (for smoke tests). 0 = unlimited.")
    args = parser.parse_args()

    if not args.host.startswith("http"):
        parser.error("--host must include http:// or https://")

    print(f"[sweep] Enumerating routes from app.url_map ...", file=sys.stderr)
    routes = enumerate_routes()
    print(f"[sweep] {len(routes)} GET routes registered", file=sys.stderr)

    print(f"[sweep] Loading seed IDs from local DB ...", file=sys.stderr)
    seeds = fetch_seed_ids()
    for k, v in seeds.items():
        if v:
            print(f"  - {k}: {len(v)} seed(s)", file=sys.stderr)

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]

    if args.limit > 0:
        routes = routes[:args.limit]

    print(f"[sweep] Sweeping {args.host} with periods={periods} ...", file=sys.stderr)
    t0 = time.monotonic()
    results = sweep(args.host, routes, seeds, periods, concurrency=args.concurrency)
    dt = time.monotonic() - t0
    print(f"[sweep] Done in {dt:.1f}s. {len(results)} probes.", file=sys.stderr)

    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"[sweep] Wrote JSON to {args.json}", file=sys.stderr)

    md = render_markdown(results, args.host, periods)
    if args.output:
        with open(args.output, "w") as f:
            f.write(md)
        print(f"[sweep] Wrote markdown to {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
