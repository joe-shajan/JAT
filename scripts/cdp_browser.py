#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_research.browser_attach import attach_page, check_api_response
ACCESS_CHECK_MARKERS = (
    "one more step before you proceed",
    "verify it's you",
    "verify it’s you",
    "access is temporarily restricted",
    "captcha-delivery",
    "geo.captcha",
)


class CdpPage:
    def __init__(self) -> None:
        self.ws = attach_page("wellfound.com")
        self.next_id = 0
        self.send("Runtime.enable")
        self.send("Page.enable")

    def close(self) -> None:
        self.ws.close()

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(json.dumps(message["error"]))
            return message.get("result", {})

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
        )
        value = result.get("result", {})
        if "value" in value:
            return value["value"]
        return value

    def navigate(self, url: str, wait_seconds: float) -> None:
        self.send("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

    def slow_scroll(self, steps: int, delay: float, pixels: int) -> None:
        for _ in range(steps):
            self.evaluate(
                f"""
(() => {{
  window.dispatchEvent(new WheelEvent('wheel', {{deltaY: {pixels}, bubbles: true, cancelable: true}}));
  document.documentElement.dispatchEvent(new WheelEvent('wheel', {{deltaY: {pixels}, bubbles: true, cancelable: true}}));
  document.documentElement.scrollTop += {pixels};
  document.body.scrollTop += {pixels};
}})()
                """
            )
            time.sleep(delay)


def get_page() -> CdpPage:
    return CdpPage()


def page_snapshot(page: CdpPage) -> dict[str, Any]:
    return page.evaluate(
        """
(() => {
  const anchors = [...document.querySelectorAll('a[href]')].map(a => ({
    text: (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' '),
    href: a.href
  })).filter(a => a.href);
  return {
    title: document.title,
    url: location.href,
    scrollY: window.scrollY,
    scrollHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
    text: document.body.innerText,
    anchors
  };
})()
        """
    )


def save_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def has_access_check(snapshot: dict[str, Any]) -> bool:
    text = str(snapshot.get("text", "")).lower()
    url = str(snapshot.get("url", "")).lower()
    return any(marker in text or marker in url for marker in ACCESS_CHECK_MARKERS)


def save_scroll_series(
    page: CdpPage,
    out_dir: Path,
    prefix: str,
    start: int,
    snapshots: int,
    scrolls_per_snapshot: int,
    delay: float,
    pixels: int,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    blocked = False
    last_snapshot: dict[str, Any] | None = None

    for index in range(start, start + snapshots):
        page.slow_scroll(scrolls_per_snapshot, delay, pixels)
        snapshot = page_snapshot(page)
        last_snapshot = snapshot
        save_snapshot(out_dir / f"{prefix}-{index:03d}.json", snapshot)
        saved += 1
        if has_access_check(snapshot):
            blocked = True
            break

    result: dict[str, Any] = {
        "saved": saved,
        "blocked": blocked,
        "out_dir": str(out_dir),
    }
    if last_snapshot:
        result.update(
            {
                "last_file": f"{prefix}-{start + saved - 1:03d}.json",
                "last_url": last_snapshot.get("url"),
                "last_scrollY": last_snapshot.get("scrollY"),
                "last_scrollHeight": last_snapshot.get("scrollHeight"),
                "last_viewportHeight": last_snapshot.get("viewportHeight"),
            }
        )
    return result


def save_wheel_series(
    page: CdpPage,
    out_dir: Path,
    prefix: str,
    start: int,
    snapshots: int,
    delay: float,
    pixels: int,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    blocked = False
    last_snapshot: dict[str, Any] | None = None

    for index in range(start, start + snapshots):
        page.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": 900,
                "y": 820,
                "deltaX": 0,
                "deltaY": pixels,
            },
        )
        time.sleep(delay)
        snapshot = page_snapshot(page)
        last_snapshot = snapshot
        save_snapshot(out_dir / f"{prefix}-{index:03d}.json", snapshot)
        saved += 1
        if has_access_check(snapshot):
            blocked = True
            break

    result: dict[str, Any] = {
        "saved": saved,
        "blocked": blocked,
        "out_dir": str(out_dir),
    }
    if last_snapshot:
        result.update(
            {
                "last_file": f"{prefix}-{start + saved - 1:03d}.json",
                "last_url": last_snapshot.get("url"),
                "last_scrollY": last_snapshot.get("scrollY"),
                "last_scrollHeight": last_snapshot.get("scrollHeight"),
                "last_viewportHeight": last_snapshot.get("viewportHeight"),
            }
        )
    return result


def select_most_recent_sort(page: CdpPage, wait_seconds: float) -> dict[str, Any]:
    return page.evaluate(
        f"""
(async () => {{
  const bodyText = document.body.innerText || '';
  if (bodyText.includes('Sort by:\\nMost recent')) {{
    return {{changed: false, sort: 'Most recent'}};
  }}

  const current = [...document.querySelectorAll('button')]
    .find(button => (button.innerText || '').trim() === 'Recommended');
  if (!current) {{
    return {{changed: false, sort: 'unknown', reason: 'Recommended sort button not found'}};
  }}

  current.click();
  await new Promise(resolve => setTimeout(resolve, 900));

  const mostRecent = [...document.querySelectorAll('button')]
    .find(button => (button.innerText || '').trim().startsWith('Most recent'));
  if (!mostRecent) {{
    return {{changed: false, sort: 'unknown', reason: 'Most recent option not found'}};
  }}

  mostRecent.click();
  await new Promise(resolve => setTimeout(resolve, {int(wait_seconds * 1000)}));
  return {{
    changed: true,
    sort: (document.body.innerText || '').includes('Sort by:\\nMost recent') ? 'Most recent' : 'unknown',
  }};
}})()
        """,
        await_promise=True,
    )


def save_page_range(
    page: CdpPage,
    url_template: str,
    out_dir: Path,
    prefix: str,
    start: int,
    pages: int,
    wait_seconds: float,
    between_seconds: float,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    blocked = False
    results = []

    for page_number in range(start, start + pages):
        url = url_template.format(page=page_number)
        page.navigate(url, wait_seconds)
        sort_result = select_most_recent_sort(page, wait_seconds)
        snapshot = page_snapshot(page)
        out_path = out_dir / f"{prefix}-{page_number:03d}.json"
        save_snapshot(out_path, snapshot)

        saved += 1
        page_blocked = has_access_check(snapshot)
        blocked = blocked or page_blocked
        result = {
            "page": page_number,
            "out": str(out_path),
            "url": snapshot.get("url"),
            "sort": sort_result,
            "blocked": page_blocked,
            "scrollHeight": snapshot.get("scrollHeight"),
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

        if page_blocked:
            break
        time.sleep(between_seconds)

    return {
        "saved": saved,
        "blocked": blocked,
        "out_dir": str(out_dir),
        "results": results,
    }


def save_apollo_jobs(page: CdpPage, path: Path) -> dict[str, Any]:
    rows = page.evaluate(
        """
(() => {
  const cache = window.__APOLLO_CLIENT__?.cache?.extract?.() || {};
  return Object.entries(cache)
    .filter(([key]) => key.startsWith('JobListingSearchResult:'))
    .map(([key, value]) => ({ cacheKey: key, id: key.split(':')[1], ...value }));
})()
        """
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"apollo_jobs": len(rows), "out": str(path)}


JOB_SEARCH_QUERY = """
query JobSearchResultsX($filterConfigurationInput: FilterConfigurationInput!) {
  talent {
    jobSearchResults(filterConfigurationInput: $filterConfigurationInput) {
      rawQuery
      totalStartupCount
      hasNextPage
      startups {
        edges {
          node {
            __typename
            ... on StartupSearchResult {
              id
              startupId
              name
              slug
              highConcept
              companySize
              highlightedJobListings {
                id
                title
                slug
                liveStartAt
                primaryRoleTitle
                remote
                locationNames
                acceptedRemoteLocationNames
                compensation
                jobType
              }
            }
            ... on PromotedResult {
              id
              promotionId
              promotedStartup {
                id
                startupId
                name
                slug
                highConcept
                companySize
                highlightedJobListings {
                  id
                  title
                  slug
                  liveStartAt
                  primaryRoleTitle
                  remote
                  locationNames
                  acceptedRemoteLocationNames
                  compensation
                  jobType
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def save_graphql_page(page: CdpPage, page_number: int, path: Path) -> dict[str, Any]:
    variables = {
        "filterConfigurationInput": {
            "page": page_number,
            "locationTagIds": ["1904", "535437"],
            "roleTagIds": ["14726", "151718"],
            "equity": {"min": None, "max": None},
            "remotePreference": "REMOTE_OPEN",
            "salary": {"min": None, "max": None},
            "sortBy": "LAST_POSTED",
            "yearsExperience": {"min": None, "max": None},
        }
    }
    expression = f"""
(async () => {{
  const response = await fetch('/graphql', {{
    method: 'POST',
    credentials: 'include',
    headers: {{'content-type': 'application/json', 'accept': 'application/json'}},
    body: {json.dumps(json.dumps({"operationName": "JobSearchResultsX", "query": JOB_SEARCH_QUERY, "variables": variables}))}
  }});
  const text = await response.text();
  return {{status: response.status, text}};
}})()
    """
    result = page.evaluate(expression, await_promise=True)
    check_api_response(result)
    try:
        data = json.loads(result["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        data = result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    search = data.get("data", {}).get("talent", {}).get("jobSearchResults", {}) if isinstance(data, dict) else {}
    edges = search.get("startups", {}).get("edges", []) if isinstance(search, dict) else []
    return {
        "status": result.get("status"),
        "page": page_number,
        "edges": len(edges),
        "hasNextPage": search.get("hasNextPage") if isinstance(search, dict) else None,
        "totalStartupCount": search.get("totalStartupCount") if isinstance(search, dict) else None,
        "out": str(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach to the verified existing Wellfound Chrome tab over CDP.")
    parser.add_argument(
        "command",
        choices=[
            "snapshot",
            "navigate",
            "scroll-snapshot",
            "scroll-series",
            "wheel-series",
            "page-range",
            "eval",
            "apollo-jobs",
            "graphql-page",
        ],
    )
    parser.add_argument("--url")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--expr")
    parser.add_argument("--wait", type=float, default=8.0)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--prefix", default="snapshot")
    parser.add_argument("--url-template")
    parser.add_argument("--between", type=float, default=2.5)
    parser.add_argument("--scrolls-per-snapshot", type=int, default=1)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--pixels", type=int, default=700)
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()

    page = get_page()
    try:
        if args.command == "apollo-jobs":
            if not args.out:
                raise SystemExit("--out is required for apollo-jobs")
            print(json.dumps(save_apollo_jobs(page, args.out), ensure_ascii=False, indent=2))
            return 0
        if args.command == "graphql-page":
            if not args.out:
                raise SystemExit("--out is required for graphql-page")
            print(json.dumps(save_graphql_page(page, args.page, args.out), ensure_ascii=False, indent=2))
            return 0
        if args.command == "scroll-series":
            if not args.out_dir:
                raise SystemExit("--out-dir is required for scroll-series")
            print(
                json.dumps(
                    save_scroll_series(
                        page,
                        args.out_dir,
                        args.prefix,
                        args.start,
                        args.steps,
                        args.scrolls_per_snapshot,
                        args.delay,
                        args.pixels,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "wheel-series":
            if not args.out_dir:
                raise SystemExit("--out-dir is required for wheel-series")
            print(
                json.dumps(
                    save_wheel_series(
                        page,
                        args.out_dir,
                        args.prefix,
                        args.start,
                        args.steps,
                        args.delay,
                        args.pixels,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "page-range":
            if not args.out_dir:
                raise SystemExit("--out-dir is required for page-range")
            if not args.url_template:
                raise SystemExit("--url-template is required for page-range")
            print(
                json.dumps(
                    save_page_range(
                        page,
                        args.url_template,
                        args.out_dir,
                        args.prefix,
                        args.start,
                        args.steps,
                        args.wait,
                        args.between,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "eval":
            if not args.expr:
                raise SystemExit("--expr is required for eval")
            result = page.evaluate(args.expr, await_promise=True)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "navigate":
            if not args.url:
                raise SystemExit("--url is required for navigate")
            page.navigate(args.url, args.wait)
        elif args.command == "scroll-snapshot":
            page.slow_scroll(args.steps, args.delay, args.pixels)

        snapshot = page_snapshot(page)
        if args.out:
            save_snapshot(args.out, snapshot)
        print(json.dumps({k: snapshot[k] for k in ("title", "url", "scrollY", "scrollHeight", "viewportHeight")}, indent=2))
    finally:
        page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
