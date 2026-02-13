#!/usr/bin/env python3
"""Capture mobile screenshots and detect horizontal overflow across key routes."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_ROUTES = [
    "/",
    "/candidates",
    "/candidate-finance",
    "/federal-finance",
    "/analytics",
    "/analytics/networks",
    "/analytics/risk",
    "/analytics/donors",
    "/analytics/geography",
    "/d2-reconciliation",
]


OVERFLOW_JS = """
() => {
  const viewportWidth = window.innerWidth;
  const doc = document.documentElement;
  const body = document.body;
  const scrollWidth = Math.max(
    doc ? doc.scrollWidth : 0,
    body ? body.scrollWidth : 0
  );

  function selectorFor(el) {
    const tag = (el.tagName || "").toLowerCase();
    if (!tag) return "unknown";
    if (el.id) return `${tag}#${el.id}`;
    const classes = Array.from(el.classList || []).slice(0, 3).join(".");
    return classes ? `${tag}.${classes}` : tag;
  }

  const offenders = [];
  for (const el of document.querySelectorAll("body *")) {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const leftOverflow = Math.max(0, -rect.left);
    const rightOverflow = Math.max(0, rect.right - viewportWidth);
    const overflow = Math.max(leftOverflow, rightOverflow);
    if (overflow <= 2) continue;
    offenders.push({
      selector: selectorFor(el),
      overflow_px: Math.round(overflow * 10) / 10,
      left: Math.round(rect.left * 10) / 10,
      right: Math.round(rect.right * 10) / 10,
      width: Math.round(rect.width * 10) / 10
    });
  }
  offenders.sort((a, b) => b.overflow_px - a.overflow_px);
  return {
    viewport_width: viewportWidth,
    scroll_width: scrollWidth,
    page_overflow_px: Math.max(0, Math.round((scrollWidth - viewportWidth) * 10) / 10),
    offenders: offenders.slice(0, 15)
  };
}
"""


def _slug_for_route(route: str) -> str:
    cleaned = route.strip().strip("/")
    if not cleaned:
        return "home"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned).strip("-").lower()
    return slug or "route"


def _parse_routes(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_ROUTES)
    routes = [part.strip() for part in raw.split(",") if part.strip()]
    normalized = []
    for route in routes:
        normalized.append(route if route.startswith("/") else f"/{route}")
    return normalized


def _write_markdown_report(
    output_path: Path,
    *,
    base_url: str,
    width: int,
    height: int,
    timestamp_utc: str,
    results: list[dict[str, Any]],
) -> None:
    lines = [
        "# Mobile Smoke Check",
        "",
        f"- Base URL: `{base_url}`",
        f"- Viewport: `{width}x{height}`",
        f"- Timestamp (UTC): `{timestamp_utc}`",
        "",
        "| Route | Status | Overflow (px) | Screenshot | Top offender |",
        "|---|---:|---:|---|---|",
    ]
    for row in results:
        route = row["route"]
        status = row["status"]
        screenshot = row.get("screenshot", "")
        overflow = row.get("page_overflow_px", "")
        offenders = row.get("offenders") or []
        top = offenders[0]["selector"] if offenders else "-"
        screenshot_ref = f"`{screenshot}`" if screenshot else "-"
        if status != "ok":
            overflow = "-"
            top = row.get("error", "error")
        lines.append(f"| `{route}` | `{status}` | `{overflow}` | {screenshot_ref} | `{top}` |")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="Base URL for the web app")
    parser.add_argument(
        "--routes",
        default="",
        help="Comma-separated route paths to test (default: built-in key routes)",
    )
    parser.add_argument("--width", type=int, default=390, help="Mobile viewport width")
    parser.add_argument("--height", type=int, default=844, help="Mobile viewport height")
    parser.add_argument(
        "--output-dir",
        default="output/mobile_smoke",
        help="Directory for screenshots and reports",
    )
    parser.add_argument(
        "--max-overflow-px",
        type=float,
        default=2.0,
        help="Fail threshold for page horizontal overflow",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=25,
        help="Navigation timeout per route",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/"
    routes = _parse_routes(args.routes)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, Any]] = []
    failures = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = context.new_page()
        page.set_default_timeout(args.timeout_seconds * 1000)

        for route in routes:
            url = urljoin(base_url, route.lstrip("/"))
            slug = _slug_for_route(route)
            screenshot_file = output_dir / f"{slug}.png"
            row: dict[str, Any] = {"route": route, "url": url}

            try:
                response = page.goto(url, wait_until="domcontentloaded")
                if response is None:
                    row.update({"status": "error", "error": "no_response"})
                else:
                    status_code = response.status
                    row["http_status"] = status_code
                    page.wait_for_timeout(500)
                    overflow = page.evaluate(OVERFLOW_JS)
                    page.screenshot(path=str(screenshot_file), full_page=True)
                    row.update(
                        {
                            "status": "ok" if status_code < 400 else "http_error",
                            "screenshot": str(screenshot_file),
                            "page_overflow_px": overflow.get("page_overflow_px", 0),
                            "viewport_width": overflow.get("viewport_width"),
                            "scroll_width": overflow.get("scroll_width"),
                            "offenders": overflow.get("offenders", []),
                        }
                    )
                    if row["status"] != "ok" or row["page_overflow_px"] > args.max_overflow_px:
                        failures += 1
            except PlaywrightTimeoutError:
                row.update({"status": "error", "error": "timeout"})
                failures += 1
            except Exception as exc:  # pragma: no cover - best effort diagnostics
                row.update({"status": "error", "error": str(exc)})
                failures += 1

            results.append(row)

        context.close()
        browser.close()

    json_report = output_dir / "report.json"
    md_report = output_dir / "report.md"
    payload = {
        "base_url": base_url,
        "viewport": {"width": args.width, "height": args.height},
        "timestamp_utc": timestamp,
        "max_overflow_px": args.max_overflow_px,
        "results": results,
    }
    json_report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown_report(
        md_report,
        base_url=base_url,
        width=args.width,
        height=args.height,
        timestamp_utc=timestamp,
        results=results,
    )

    print(f"Wrote mobile smoke report: {json_report}")
    print(f"Wrote summary: {md_report}")
    if failures:
        print(f"Mobile smoke check found {failures} failing route(s).")
        return 1
    print("Mobile smoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
