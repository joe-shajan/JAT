from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
LINKEDIN_PROFILE_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%\-./]+", re.I)
JOB_URL_RE = re.compile(r"wellfound\.com/jobs/\d+")
COMPANY_URL_RE = re.compile(r"wellfound\.com/company/")
ROLE_KEYWORDS = (
    "founder",
    "co-founder",
    "cofounder",
    "ceo",
    "cto",
    "chief technology officer",
    "head of engineering",
    "vp engineering",
    "engineering manager",
    "recruiter",
    "talent",
    "people",
    "hr",
    "human resources",
    "hiring",
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def newest_run_id() -> str:
    runs_dir = WORKSPACE / "runs"
    candidates: list[tuple[float, str]] = []
    for run_dir in runs_dir.iterdir() if runs_dir.exists() else []:
        jobs_path = run_dir / "jobs.jsonl"
        summary_path = run_dir / "summary.json"
        if not run_dir.is_dir() or not jobs_path.exists() or not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if summary.get("sample"):
            continue
        candidates.append((jobs_path.stat().st_mtime, run_dir.name))
    if not candidates:
        raise SystemExit("No non-sample JobApplier run with jobs.jsonl exists.")
    return max(candidates)[1]


def clean_linkedin_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(html.unescape(url))
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme or "https", parsed.netloc.lower(), path, "", ""))


def slug_to_company_name(url: str) -> str:
    slug = urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    words = [word for word in slug.split("-") if word]
    return " ".join(word.upper() if word in {"ai", "hr", "api"} else word.capitalize() for word in words)


def compact_text(value: object) -> str:
    return " ".join(str(value or "").split())


def extract_company_map(snapshot_dir: Path) -> dict[str, dict[str, str]]:
    company_by_job: dict[str, dict[str, str]] = {}
    for snapshot_path in sorted(snapshot_dir.glob("snapshot-*.json")):
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        current_company: dict[str, str] | None = None
        for anchor in snapshot.get("anchors", []):
            href = str(anchor.get("href") or "")
            text = compact_text(anchor.get("text"))
            if COMPANY_URL_RE.search(href):
                current_company = {
                    "company_url": href,
                    "company_name": slug_to_company_name(href),
                    "visible_company_text": text,
                    "company_source_snapshot": snapshot_path.name,
                }
                continue
            if JOB_URL_RE.search(href) and current_company:
                company_by_job.setdefault(href, dict(current_company))
    return company_by_job


def build_manual_search_urls(company: str, job_title: str = "") -> list[str]:
    queries = build_search_queries(company, job_title)
    return ["https://www.google.com/search?q=" + urllib.parse.quote_plus(query) for query in queries[:4]]


def build_search_queries(company: str, job_title: str = "") -> list[str]:
    base = company.strip()
    title_hint = ""
    if any(term in job_title.lower() for term in ("ai", "engineering", "software", "full")):
        title_hint = " engineering"
    return [
        f'site:linkedin.com/in "{base}" founder OR co-founder OR CEO OR CTO',
        f'site:linkedin.com/in "{base}" "head of engineering" OR "VP Engineering" OR CTO',
        f'site:linkedin.com/in "{base}" recruiter OR talent OR "people operations" OR HR',
        f'site:linkedin.com/in "{base}" hiring manager{title_hint}',
    ]


def request_text(url: str, *, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def brave_search(query: str, count: int) -> list[SearchResult]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return []
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": count})
    request = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": api_key})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read().decode("utf-8"))
    results: list[SearchResult] = []
    for item in data.get("web", {}).get("results", [])[:count]:
        results.append(
            SearchResult(
                title=compact_text(item.get("title")),
                url=str(item.get("url") or ""),
                snippet=compact_text(item.get("description")),
                source="brave_search",
            )
        )
    return results


def bing_search(query: str, count: int) -> list[SearchResult]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    page = request_text(url)
    blocks = re.findall(r'<li class="b_algo".*?</li>', page, flags=re.I | re.S)
    results: list[SearchResult] = []
    for block in blocks[:count]:
        link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        if not link_match:
            continue
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.I | re.S)
        title = clean_html(link_match.group(2))
        result_url = html.unescape(link_match.group(1))
        snippet = clean_html(snippet_match.group(1) if snippet_match else "")
        results.append(SearchResult(title=title, url=result_url, snippet=snippet, source="bing_html"))
    return results


def duckduckgo_search(query: str, count: int) -> list[SearchResult]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    page = request_text(url)
    blocks = re.findall(r'<div class="result results_links.*?</div>\s*</div>', page, flags=re.I | re.S)
    results: list[SearchResult] = []
    for block in blocks[:count]:
        link_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        if not link_match:
            continue
        snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        result_url = decode_duckduckgo_url(html.unescape(link_match.group(1)))
        results.append(
            SearchResult(
                title=clean_html(link_match.group(2)),
                url=result_url,
                snippet=clean_html(snippet_match.group(1) if snippet_match else ""),
                source="duckduckgo_html",
            )
        )
    return results


def clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return compact_text(html.unescape(value))


def decode_duckduckgo_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    return url


def infer_role(title: str, snippet: str) -> str:
    text = f"{title} {snippet}".lower()
    priority = [
        ("Founder / Co-founder", ("co-founder", "cofounder", "founder")),
        ("CEO", ("chief executive", " ceo", "ceo ")),
        ("CTO / technical executive", ("chief technology", " cto", "cto ")),
        ("Engineering leader", ("head of engineering", "vp engineering", "engineering manager")),
        ("Recruiter / talent", ("recruiter", "talent", "people operations", "human resources", " hr ", "hiring")),
    ]
    padded = f" {text} "
    for label, needles in priority:
        if any(needle in padded for needle in needles):
            return label
    return "public LinkedIn profile candidate"


def infer_name(title: str) -> str:
    title = re.sub(r"\s+\|\s+LinkedIn.*$", "", title, flags=re.I)
    title = re.sub(r"\s+-\s+LinkedIn.*$", "", title, flags=re.I)
    title = title.split(" - ")[0].split(" | ")[0].strip()
    return title or "Public LinkedIn profile candidate"


def score_result(result: SearchResult, company: str) -> float:
    text = f"{result.title} {result.snippet} {result.url}".lower()
    score = 0.35
    if "linkedin.com/in/" in result.url.lower():
        score += 0.25
    company_terms = [term for term in re.split(r"\W+", company.lower()) if len(term) > 2]
    if company_terms and any(term in text for term in company_terms):
        score += 0.2
    if any(keyword in text for keyword in ROLE_KEYWORDS):
        score += 0.2
    return min(score, 0.95)


def candidate_from_result(result: SearchResult, company: str, query: str) -> dict[str, Any] | None:
    match = LINKEDIN_PROFILE_RE.search(result.url)
    if not match:
        return None
    linkedin_url = clean_linkedin_url(match.group(0))
    title = clean_html(result.title)
    snippet = clean_html(result.snippet)
    return {
        "name": infer_name(title),
        "role": infer_role(title, snippet),
        "linkedin_url": linkedin_url,
        "source": result.source,
        "source_url": result.url,
        "confidence": round(score_result(result, company), 2),
        "notes": f"Public search result for query: {query}",
        "result_title": title,
        "result_snippet": snippet,
    }


def search_public_linkedin(company: str, job_title: str, providers: list[str], count: int, delay: float) -> tuple[list[dict[str, Any]], list[str]]:
    leads: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_urls: set[str] = set()
    for query in build_search_queries(company, job_title):
        provider_results: list[SearchResult] = []
        for provider in providers:
            try:
                if provider == "brave":
                    provider_results = brave_search(query, count)
                elif provider == "bing":
                    provider_results = bing_search(query, count)
                elif provider == "duckduckgo":
                    provider_results = duckduckgo_search(query, count)
                else:
                    continue
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
                provider_results = []
            for result in provider_results:
                candidate = candidate_from_result(result, company, query)
                if not candidate:
                    continue
                if candidate["linkedin_url"] in seen_urls:
                    continue
                seen_urls.add(candidate["linkedin_url"])
                leads.append(candidate)
            if provider_results:
                break
        if leads:
            break
        if delay:
            time.sleep(delay)
    leads.sort(key=lambda row: float(row.get("confidence") or 0), reverse=True)
    return leads, errors


def enrich_run(
    run_id: str,
    *,
    providers: list[str],
    max_companies: int,
    results_per_query: int,
    delay: float,
    network: bool,
) -> dict[str, Any]:
    run_dir = WORKSPACE / "runs" / run_id
    jobs_path = run_dir / "jobs.jsonl"
    summary_path = run_dir / "summary.json"
    if not jobs_path.exists():
        raise SystemExit(f"Missing jobs artifact: {jobs_path}")
    jobs = read_jsonl(jobs_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    snapshot_dir = WORKSPACE / str(summary.get("current_authoritative_capture", {}).get("snapshot_dir", ""))
    company_map = extract_company_map(snapshot_dir) if snapshot_dir.exists() else {}

    company_jobs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    roadblocks: list[dict[str, Any]] = []
    for job in jobs:
        job_url = str(job.get("job_url") or "")
        company = company_map.get(job_url)
        if not company:
            roadblocks.append(contact_roadblock(run_id, job, "company-not-visible", "Could not recover company from saved snapshots."))
            continue
        enriched_job = dict(job)
        enriched_job.update(company)
        company_jobs[company["company_url"]].append(enriched_job)

    contacts: list[dict[str, Any]] = []
    searched_companies = 0
    for company_url, grouped_jobs in company_jobs.items():
        if searched_companies >= max_companies:
            for job in grouped_jobs:
                roadblocks.append(contact_roadblock(run_id, job, "company-limit", "Max company search limit reached."))
            continue
        searched_companies += 1
        company_name = grouped_jobs[0]["company_name"]
        job_title = str(grouped_jobs[0].get("title") or grouped_jobs[0].get("title_location_comp") or "")
        leads: list[dict[str, Any]] = []
        errors: list[str] = []
        if network:
            leads, errors = search_public_linkedin(company_name, job_title, providers, results_per_query, delay)
        if not leads:
            manual_urls = build_manual_search_urls(company_name, job_title)
            for job in grouped_jobs:
                roadblocks.append(
                    contact_roadblock(
                        run_id,
                        job,
                        "linkedin-not-found",
                        "No public LinkedIn profile result found automatically.",
                        "; ".join(errors[:3]),
                    )
                )
                contacts.append(manual_contact(run_id, job, manual_urls))
            continue
        for job in grouped_jobs:
            manual_urls = build_manual_search_urls(company_name, str(job.get("title") or ""))
            for lead in leads[:4]:
                row = {
                    "run_id": run_id,
                    "job_url": job.get("job_url", ""),
                    "job_title": job.get("title") or job.get("title_location_comp") or "",
                    "company_name": company_name,
                    "company_url": company_url,
                    "manual_search_urls": manual_urls,
                    **lead,
                }
                contacts.append(row)

    contacts_path = run_dir / "contacts.jsonl"
    roadblocks_path = run_dir / "contact-roadblocks.jsonl"
    write_jsonl(contacts_path, contacts)
    write_jsonl(roadblocks_path, roadblocks)
    summary_out = {
        "run_id": run_id,
        "jobs": len(jobs),
        "jobs_with_company": sum(len(grouped) for grouped in company_jobs.values()),
        "companies": len(company_jobs),
        "companies_searched": searched_companies,
        "contacts": len(contacts),
        "automatic_linkedin_contacts": sum(1 for row in contacts if row.get("linkedin_url")),
        "manual_search_contacts": sum(1 for row in contacts if not row.get("linkedin_url")),
        "roadblocks": len(roadblocks),
        "contacts_path": str(contacts_path),
        "contact_roadblocks_path": str(roadblocks_path),
    }
    (run_dir / "contacts-summary.json").write_text(json.dumps(summary_out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_out


def contact_roadblock(run_id: str, job: dict[str, Any], stage: str, reason: str, detail: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": stage,
        "url": job.get("job_url", ""),
        "company_name": job.get("company_name", ""),
        "job_title": job.get("title") or job.get("title_location_comp") or "",
        "reason": reason,
        "detail": detail,
        "action_taken": "recorded fallback manual search links where possible",
    }


def manual_contact(run_id: str, job: dict[str, Any], manual_urls: list[str]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "job_url": job.get("job_url", ""),
        "job_title": job.get("title") or job.get("title_location_comp") or "",
        "company_name": job.get("company_name", ""),
        "company_url": job.get("company_url", ""),
        "name": "Manual LinkedIn search needed",
        "role": "Founder / senior executive / recruiter / HR",
        "linkedin_url": "",
        "source": "manual_search_fallback",
        "source_url": "",
        "confidence": 0.0,
        "notes": "Automatic public search did not return a confirmed linkedin.com/in profile.",
        "manual_search_urls": manual_urls,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find public LinkedIn contact candidates for a JobApplier run.")
    parser.add_argument("--run-id", default="", help="Run ID. Defaults to newest non-sample run with jobs.jsonl.")
    parser.add_argument("--providers", default="brave,bing,duckduckgo", help="Comma-separated providers: brave,bing,duckduckgo")
    parser.add_argument("--max-companies", type=int, default=50)
    parser.add_argument("--results-per-query", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--no-network", action="store_true", help="Only generate manual search fallbacks.")
    args = parser.parse_args(argv)

    providers = [provider.strip() for provider in args.providers.split(",") if provider.strip()]
    run_id = args.run_id or newest_run_id()
    result = enrich_run(
        run_id,
        providers=providers,
        max_companies=args.max_companies,
        results_per_query=args.results_per_query,
        delay=args.delay,
        network=not args.no_network,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
