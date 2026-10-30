from __future__ import annotations
from job_research.browser_attach import BrowserAttachError

import argparse
import sys
import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from job_research.action_ledger import ActionLedger, TERMINAL_JOB_STATUSES
from job_research.canonicalize import canonical_job_identity, canonical_job_url
from job_research.config import load_config
from job_research.contacts import (
    discover_linkedin_contacts,
    enrich_contacts_from_wellfound_profiles,
    extract_public_emails,
    extract_wellfound_contacts,
)
from job_research.filters import has_user_excluded_role_keyword, is_geographically_allowed, is_recent_job, is_role_allowed, role_rejection_reason
from job_research.html_report import render_dashboard
from job_research.linkedin_jobs import (
    LinkedInBlocked,
    LinkedInCdpFetcher,
    build_linkedin_search_specs,
    extract_linkedin_job_urls,
    parse_linkedin_job_snapshot,
    resolve_linkedin_cdp_port,
)
from job_research.message_generation import generate_outreach
from job_research.models import Contact, JobRecord, Roadblock
from job_research.roadblocks import record_roadblock
from job_research.storage import append_jsonl, create_run_paths
from job_research.wellfound import (
    ChromeDumpFetcher,
    WellfoundBlocked,
    build_search_urls,
    extract_recent_wellfound_job_urls,
    extract_wellfound_job_urls,
    find_posted_marker,
    guess_title_company,
)


WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = WORKSPACE / "data" / "jobapplier.sqlite3"
MASTER_CSV_COLUMNS = [
    "job_url",
    "company",
    "job_title",
    "location",
    "remote_status",
    "compensation",
    "fit_score",
    "status",
    "first_seen",
    "last_seen",
    "source",
    "contacts",
    "linkedin_urls",
    "emails",
    "dashboard_run_id",
    "notes",
]


def make_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research configured jobs and prepare review drafts")
    parser.add_argument("--headful", action="store_true", help="Run Chrome visibly instead of headless")
    parser.add_argument("--limit", type=int, default=None, help="Maximum Wellfound job URLs to analyze (default: config)")
    parser.add_argument("--skip-outreach", action="store_true", help="Use template fallback drafts only")
    parser.add_argument("--use-ai-outreach", action="store_true", help="Use JOBAPPLIER_OUTREACH_COMMAND for draft generation with template fallback")
    parser.add_argument("--sample", action="store_true", help="Render one local sample job without external fetching, for dashboard verification")
    parser.add_argument("--use-current-wellfound-filters", action="store_true", default=False, help="Use the currently logged-in Wellfound jobs page/filter state instead of building local role/location search URLs")
    parser.add_argument("--skip-local-filters", action="store_true", help="Do not apply local role/freshness/geography filters after fetching Wellfound job pages")
    parser.add_argument("--skip-linkedin", action="store_true", help="Run only the Wellfound source")
    parser.add_argument("--linkedin-limit", type=int, help="Maximum LinkedIn job detail pages to fetch/analyze")
    parser.add_argument("--dashboard-dir", type=Path, help="Write the HTML dashboard to this directory instead of reports/html/<run_id>")
    parser.add_argument("--ledger-path", type=Path, default=DEFAULT_LEDGER_PATH, help="SQLite ledger used for duplicate-safe jobs, drafts, and action approvals")
    return parser.parse_args()


def load_existing_job_urls(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {canonical_job_url(row.get("job_url", "")) for row in csv.DictReader(f) if row.get("job_url")}


def load_job_urls_by_status(csv_path: Path, statuses: set[str]) -> set[str]:
    if not csv_path.exists():
        return set()
    normalized_statuses = {status.lower() for status in statuses}
    urls: set[str] = set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            status = (row.get("status") or "").strip().lower()
            job_url = row.get("job_url") or ""
            if status in normalized_statuses and job_url:
                urls.add(canonical_job_url(job_url))
    return urls


def append_jobs_csv(csv_path: Path, jobs: list[JobRecord], run_id: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing_job_urls(csv_path)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MASTER_CSV_COLUMNS, lineterminator="\n")
        if not file_exists:
            writer.writeheader()
        for job in jobs:
            if job.job_url in existing:
                continue
            contacts = "; ".join(filter(None, [c.name for c in job.contacts]))
            linkedin_urls = "; ".join(filter(None, [c.linkedin_url for c in job.contacts]))
            emails = "; ".join(filter(None, [c.email for c in job.contacts]))
            writer.writerow(
                {
                    "job_url": job.job_url,
                    "company": job.company,
                    "job_title": job.title,
                    "location": job.location,
                    "remote_status": job.remote_status,
                    "compensation": job.compensation,
                    "fit_score": "",
                    "status": job.status,
                    "first_seen": job.first_seen,
                    "last_seen": job.last_seen,
                    "source": job.source,
                    "contacts": contacts,
                    "linkedin_urls": linkedin_urls,
                    "emails": emails,
                    "dashboard_run_id": run_id,
                    "notes": job.notes.strip(),
                }
            )


def is_compensation_line(line: str) -> bool:
    normalized = " ".join((line or "").split())
    lowered = normalized.lower()
    if not normalized:
        return False
    if re.search(r"\bearn\s+[$₹€£]?\s*\d+\b", lowered):
        return False
    if any(token in lowered for token in ("bounty", "referral bonus", "reward", "cash bonus")):
        return False
    if "salary" in lowered or "compensation" in lowered:
        return True
    has_currency_amount = re.search(r"[$₹€£]\s*\d+(?:[,.]\d+)?\s*[kKmM]?\b", normalized)
    has_range = re.search(r"[$₹€£]\s*\d+(?:[,.]\d+)?\s*[kKmM]?\s*(?:-|–|—|to)\s*[$₹€£]?\s*\d", normalized)
    has_pay_context = re.search(r"\b(?:/\s*(?:yr|year|mo|month|hr|hour)|per\s+(?:year|month|hour)|equity|no\s+equity)\b", lowered)
    if has_currency_amount and (has_range or has_pay_context):
        return True
    return False


def is_location_line(line: str) -> bool:
    normalized = " ".join((line or "").split())
    lowered = normalized.lower()
    if not normalized or len(normalized) > 120:
        return False
    if is_compensation_line(normalized):
        return False
    if any(token in lowered for token in ("compensation", "salary", "equity", "lpa", "ctc")):
        return False
    punctuation_check = re.sub(r"\bU\.S\.", "US", normalized, flags=re.I)
    if re.search(r"[.!?]", punctuation_check):
        return False
    from job_research.filters import matches_phrase
    locations = load_config().get('locations', {}).get('include', [])
    explicit_label = bool(re.match(r'^(?:location|work location|job location)\s*[:\-]', lowered) or re.search(r'\bmust be .{0,20}(?:based|located)\b', lowered))
    if not explicit_label and not re.search(r'\b(remote|worldwide|global|everywhere)\b', lowered) and not any(matches_phrase(loc, lowered) for loc in locations):
        return False
    if lowered in {"hires remotely", "hires remotely in", "company location", "remote work policy"}:
        return False
    summary_markers = ("about ", "mission", "platform", "software", "product", "team", "build", "building", "helps", "customers", "clients", "brand")
    if any(marker in lowered for marker in summary_markers) and not re.search(r"\b(?:location|hires remotely in|remote work policy|company location|based)\b", lowered):
        return False
    return True


def infer_location_and_remote(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    location = ""
    remote_status = ""
    compensation = ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    label_candidates = {"location", "hires remotely", "hires remotely in", "company location", "remote work policy"}
    for line in lines:
        if not compensation and is_compensation_line(line):
            compensation = line[:180]
    for index, line in enumerate(lines):
        l = line.lower()
        if re.match(r"^(?:location|work location|job location)\s*[:\-]", l) and is_location_line(line):
            location = line[:120]
            break
        if l in label_candidates:
            following = [candidate for candidate in lines[index + 1 : index + 4] if is_location_line(candidate)]
            if following:
                location = ", ".join(following)[:120]
                break
        if re.search(r"\bmust be .{0,20}(?:based|located)\b", l) and is_location_line(line):
            location = line[:120]
            break
    if not location:
        for line in lines:
            if is_location_line(line):
                location = line[:120]
                break
    if "remote" in lowered or "worldwide" in lowered or "global" in lowered:
        remote_status = "Remote/remote-friendly visible"
    return location, remote_status, compensation


def build_sample_job(run_id: str) -> JobRecord:
    now = datetime.now(timezone.utc)
    job = JobRecord(
        job_url="https://example.com/jobs/fictional-demo",
        title="Fictional Demo Role",
        company="SampleCo",
        location="Worldwide",
        remote_status="Remote",
        compensation="not visible",
        posted_at_text="Posted today",
        posted_at=now,
        description="Fictional local verification record. Not a real job posting.",
        source_urls=["https://example.com/jobs/fictional-demo"],
        first_seen=now.date().isoformat(),
        last_seen=now.date().isoformat(),
        notes="Sample row generated with --sample; not a real fetched job.",
    )
    job.contacts = [
        Contact(
            name="Manual LinkedIn search needed",
            role="Founder/recruiter/job poster",
            source="sample_manual_search",
            manual_search_urls=["https://www.google.com/search?q=site%3Alinkedin.com%2Fin+SampleCo+founder"],
        )
    ]
    job.outreach = generate_outreach(job, use_ai=False)
    return job


def read_roadblocks(path: Path) -> list[Roadblock]:
    if not path.exists():
        return []
    roadblocks: list[Roadblock] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        roadblocks.append(
            Roadblock(
                run_id=data.get("run_id", ""),
                stage=data.get("stage", ""),
                url=data.get("url", ""),
                title=data.get("title", ""),
                company=data.get("company", ""),
                location=data.get("location", ""),
                posted_at_text=data.get("posted_at_text", ""),
                reason=data.get("reason", ""),
                action_taken=data.get("action_taken", ""),
                next_fallback=data.get("next_fallback", ""),
            )
        )
    return roadblocks


def run() -> dict:
    args = parse_args()
    cfg = load_config()
    if args.sample and args.ledger_path == DEFAULT_LEDGER_PATH:
        args.ledger_path = WORKSPACE / 'data' / 'sample-ledger.sqlite3'
    # Direct CLI runs must not bypass the runner's existing-session gate.
    if not args.sample:
        from job_research.browser_attach import attach_page
        sources = ["wellfound.com"] + ([] if args.skip_linkedin or not cfg.get("linkedin", {}).get("enabled", True) else ["linkedin.com"])
        for source in sources:
            page = attach_page(source)
            page.close()
    cfg = load_config()
    args.limit = args.limit if args.limit is not None else cfg["search"]["max_jobs_per_run"]
    if args.limit <= 0:
        raise ValueError("--limit must be positive")
    run_id = make_run_id()
    paths = create_run_paths(WORKSPACE, run_id, html_dir=args.dashboard_dir)
    master_csv = WORKSPACE / cfg["output"]["master_jobs_csv"]

    with ActionLedger(args.ledger_path) as ledger:
        terminal_urls = load_job_urls_by_status(master_csv, TERMINAL_JOB_STATUSES) | ledger.terminal_job_urls()

        if args.sample:
            jobs = [build_sample_job(run_id)]
            pending_actions = ledger.create_pending_actions_for_job(jobs[0], run_id)
            append_jsonl(paths.run_dir / "jobs.jsonl", jobs[0].to_dict())
            stats = {
                "jobs_checked": 1,
                "jobs_displayed": 1,
                "jobs_not_shown": 0,
                "job_urls_discovered": 1,
                "skipped_terminal_jobs_from_data": 0,
                "pending_actions": len(pending_actions),
                "action_counts": ledger.action_counts(),
            }
            dashboard = render_dashboard(paths.html_dir, run_id, jobs, roadblocks=[], stats=stats)
            summary = {"run_id": run_id, "jobs": 1, "dashboard": str(dashboard), "fetched_pages": str(paths.fetched_pages_dir), "sample": True, "ledger": str(args.ledger_path), **stats}
            (paths.run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary

        fetcher = ChromeDumpFetcher(WORKSPACE, paths, headless=not args.headful)
        job_urls: list[str] = []

        search_targets = [("current-wellfound-filters", "https://wellfound.com/jobs")] if args.use_current_wellfound_filters else [
            (role, search_url)
            for role in ([" ".join([title] + cfg["roles"]["prefer_keywords"]) for title in cfg["roles"]["primary"]] or [" ".join(cfg["roles"]["prefer_keywords"])])
            for search_url in build_search_urls(role)
        ]

        for role, search_url in search_targets:
            try:
                text = fetcher.fetch_url(search_url, "search", f"search-{role}")
                urls = extract_wellfound_job_urls(text) if args.use_current_wellfound_filters else extract_recent_wellfound_job_urls(text, hours=cfg["search"]["posted_within_hours"])
                if not urls and extract_wellfound_job_urls(text) and not args.use_current_wellfound_filters:
                    record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Skipped search-page jobs because no nearby posted label matched: posted today, posted yesterday, posted 1 day ago, posted 2 days ago, or 2 days ago", url=search_url, action_taken="Excluded before fetching job detail", next_fallback="Inspect saved search page if recent jobs seem missing")
                job_urls.extend(urls)
            except WellfoundBlocked as exc:
                record_roadblock(paths.roadblocks_path, run_id, "wellfound_search", str(exc), url=search_url, action_taken="Stopped for manual help", next_fallback="Ask the user to resolve the check in the existing Chrome")
                raise
            except BrowserAttachError:
                raise
            except Exception as exc:
                record_roadblock(paths.roadblocks_path, run_id, "wellfound_search", str(exc), url=search_url, action_taken="Skipped this search URL", next_fallback="Inspect fetched_pages/roadblocks; never launch a browser")

        unique_urls: list[str] = []
        seen: set[str] = set()
        skipped_terminal_jobs = 0
        for url in job_urls:
            canonical_url = canonical_job_url(url)
            if canonical_url in seen:
                continue
            seen.add(canonical_url)
            if canonical_url in terminal_urls:
                skipped_terminal_jobs += 1
                record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Skipped because this job is already marked applied/rejected/stale/cancelled in local data or ledger", url=canonical_url, action_taken="Excluded before fetching job detail", next_fallback="Change status in data/jobs.csv or data/jobapplier.sqlite3 if this should be reconsidered")
                continue
            unique_urls.append(canonical_url)

        if not unique_urls:
            record_roadblock(paths.roadblocks_path, run_id, "job_discovery", "No eligible Wellfound job URLs were extracted after local duplicate/applied-job checks", action_taken="Rendered empty dashboard", next_fallback="Use --headful, login to Wellfound in chrome-profile, or inspect local applied/rejected statuses")

        accepted_jobs: list[JobRecord] = []
        pending_action_count = 0
        jobs_checked = 0
        for url in unique_urls[: args.limit]:
            jobs_checked += 1
            try:
                text = fetcher.fetch_url(url, "job", url.rsplit("/", 1)[-1])
            except BrowserAttachError:
                raise
            except Exception as exc:
                record_roadblock(paths.roadblocks_path, run_id, "job_fetch", str(exc), url=url, action_taken="Skipped job", next_fallback="Open URL manually from roadblock log")
                continue

            title, company = guess_title_company(text, url)
            posted_text, posted_at = find_posted_marker(text)
            location, remote_status, compensation = infer_location_and_remote(text)
            today = datetime.now(timezone.utc).date().isoformat()
            job = JobRecord(
                job_url=url,
                title=title,
                company=company,
                location=location,
                remote_status=remote_status,
                compensation=compensation,
                posted_at_text=posted_text,
                posted_at=posted_at,
                description=text[:15000],
                source_urls=[url],
                fetched_page_paths=[str(paths.job_pages_dir)],
                first_seen=today,
                last_seen=today,
            )
            if has_user_excluded_role_keyword(job, config=cfg):
                record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Matched a configured excluded keyword", url=url, action_taken="Excluded from dashboard", next_fallback="Edit config/wellfound_search.yaml or job_research/filters.py if this role should be allowed", job=job)
                continue
            if not args.skip_local_filters:
                if not is_recent_job(job, hours=cfg["search"]["posted_within_hours"]):
                    record_roadblock(paths.roadblocks_path, run_id, "job_filter", f"Skipped because posted label did not match the allowed labels: {posted_text or 'missing'}", url=url, action_taken="Excluded from dashboard", next_fallback="Inspect saved job page and improve posted-label parser if visible", job=job)
                    continue
                if not is_role_allowed(job, config=cfg):
                    record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Title did not match configured role titles", url=url, action_taken="Excluded from dashboard", next_fallback="Edit config/wellfound_search.yaml or job_research/filters.py if this role should be allowed", job=job)
                    continue
                if not is_geographically_allowed(job, config=cfg):
                    record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Location did not match configured geography", url=url, action_taken="Excluded from dashboard", next_fallback="Inspect saved job page if this seems wrong", job=job)
                    continue

            public_emails = extract_public_emails(text)
            wellfound_contacts = extract_wellfound_contacts(text, company, url)
            profile_text_cache: dict[str, str] = {}

            def fetch_wellfound_profile_text(profile_url: str) -> str:
                if profile_url not in profile_text_cache:
                    try:
                        profile_text_cache[profile_url] = fetcher.fetch_url(profile_url, "contact", profile_url.rstrip("/").rsplit("/", 1)[-1])
                    except BrowserAttachError:
                        raise
                    except Exception as exc:
                        record_roadblock(
                            paths.roadblocks_path,
                            run_id,
                            "contact_enrichment",
                            f"Skipped Wellfound profile LinkedIn extraction: {exc}",
                            url=profile_url,
                            action_taken="Kept Wellfound-visible name and fallback search",
                            next_fallback="Open the Wellfound profile manually if the LinkedIn icon is visible",
                        )
                        profile_text_cache[profile_url] = ""
                return profile_text_cache[profile_url]

            wellfound_contacts = enrich_contacts_from_wellfound_profiles(wellfound_contacts, fetch_wellfound_profile_text)
            job.contacts = discover_linkedin_contacts(company, wellfound_contacts=wellfound_contacts)
            if public_emails:
                if not job.contacts:
                    job.contacts.append(Contact(name="Visible page contact", role="Contact", source="visible_job_page"))
                job.contacts[0].email = public_emails[0]
                job.contacts[0].email_status = "verified_public"
                job.contacts[0].source = job.contacts[0].source or "visible_job_page"
                job.contacts[0].confidence = max(job.contacts[0].confidence, 0.7)
            job.outreach = generate_outreach(job, use_ai=args.use_ai_outreach and not args.skip_outreach)
            actions = ledger.create_pending_actions_for_job(job, run_id)
            pending_action_count += sum(1 for action in actions if action.status == "pending_approval")
            append_jsonl(paths.run_dir / "jobs.jsonl", job.to_dict())
            accepted_jobs.append(job)

        linkedin_urls_found = 0
        linkedin_jobs_checked = 0
        linkedin_jobs_displayed = 0
        if not args.skip_linkedin and not args.sample and cfg.get("linkedin", {}).get("enabled", True):
            linkedin_cfg = cfg.get("linkedin") or {}
            linkedin_limit = args.linkedin_limit if args.linkedin_limit is not None else int(linkedin_cfg.get("max_jobs_per_run", 25))
            linkedin_urls: list[str] = []
            try:
                with LinkedInCdpFetcher(
                    paths,
                    cdp_port=resolve_linkedin_cdp_port(linkedin_cfg),
                    delay_seconds=float(linkedin_cfg.get("delay_seconds", 3.0)),
                ) as linkedin_fetcher:
                    for spec in build_linkedin_search_specs(cfg):
                        try:
                            snapshot = linkedin_fetcher.fetch_search(spec)
                            linkedin_urls.extend(extract_linkedin_job_urls(snapshot))
                        except LinkedInBlocked as exc:
                            record_roadblock(
                                paths.roadblocks_path,
                                run_id,
                                "linkedin_search",
                                f"LinkedIn discovery stopped: {exc}",
                                url=spec.url,
                                action_taken="Stopped LinkedIn browsing without bypassing the login/access check",
                                next_fallback="Ask the user to clear the visible LinkedIn access check in the existing Chrome",
                            )
                            raise
                        except BrowserAttachError:
                            raise
                        except Exception as exc:
                            record_roadblock(
                                paths.roadblocks_path,
                                run_id,
                                "linkedin_search",
                                str(exc),
                                url=spec.url,
                                action_taken="Skipped this LinkedIn search",
                                next_fallback="Inspect the saved LinkedIn search snapshot and shared JobApplier Chrome profile",
                            )

                    linkedin_urls_found = len(linkedin_urls)
                    linkedin_unique_urls: list[str] = []
                    for raw_url in linkedin_urls:
                        canonical_url = canonical_job_url(raw_url)
                        if canonical_url in seen:
                            continue
                        seen.add(canonical_url)
                        if canonical_url in terminal_urls:
                            skipped_terminal_jobs += 1
                            record_roadblock(
                                paths.roadblocks_path,
                                run_id,
                                "job_filter",
                                "Skipped LinkedIn job because it is already marked applied/rejected/stale/cancelled",
                                url=canonical_url,
                                action_taken="Excluded before fetching job detail",
                                next_fallback="Change the local job status if this role should be reconsidered",
                            )
                            continue
                        linkedin_unique_urls.append(canonical_url)

                    accepted_identity_keys = {canonical_job_identity(job) for job in accepted_jobs}
                    for url in linkedin_unique_urls[:linkedin_limit]:
                        linkedin_jobs_checked += 1
                        jobs_checked += 1
                        try:
                            snapshot, saved_path = linkedin_fetcher.fetch_job(url)
                            job = parse_linkedin_job_snapshot(snapshot)
                            job.fetched_page_paths = [str(saved_path)]
                        except LinkedInBlocked as exc:
                            record_roadblock(
                                paths.roadblocks_path,
                                run_id,
                                "linkedin_job_fetch",
                                f"LinkedIn detail fetch stopped: {exc}",
                                url=url,
                                action_taken="Stopped LinkedIn browsing without bypassing the access check",
                                next_fallback="Clear the visible LinkedIn login/CAPTCHA/access check and rerun",
                            )
                            raise
                        except BrowserAttachError:
                            raise
                        except Exception as exc:
                            record_roadblock(paths.roadblocks_path, run_id, "linkedin_job_fetch", str(exc), url=url, action_taken="Skipped LinkedIn job", next_fallback="Open the saved URL manually")
                            continue

                        identity_key = canonical_job_identity(job)
                        if identity_key in accepted_identity_keys:
                            record_roadblock(
                                paths.roadblocks_path,
                                run_id,
                                "cross_source_dedupe",
                                "Skipped duplicate company/title already found from another source",
                                url=url,
                                title=job.title,
                                action_taken="Kept the first source record in the unified dashboard",
                                next_fallback="Open the URL manually if the LinkedIn posting has materially different details",
                            )
                            continue
                        if has_user_excluded_role_keyword(job, config=cfg):
                            record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Matched a configured excluded keyword", url=url, title=job.title, action_taken="Excluded from dashboard", next_fallback="Edit the shared role filters if this role should be allowed", job=job)
                            continue
                        if not is_recent_job(job, hours=cfg["search"]["posted_within_hours"]):
                            record_roadblock(paths.roadblocks_path, run_id, "job_filter", f"Skipped LinkedIn role because it is not visibly within the configured posting window: {job.posted_at_text or 'missing'}", url=url, title=job.title, action_taken="Excluded from dashboard", next_fallback="Inspect the saved LinkedIn job snapshot if the age is visibly different", job=job)
                            continue
                        if not is_role_allowed(job, config=cfg):
                            record_roadblock(paths.roadblocks_path, run_id, "job_filter", role_rejection_reason(job, config=cfg), url=url, title=job.title, action_taken="Excluded from dashboard", next_fallback="Update shared role filters only if this role family should be included", job=job)
                            continue
                        if not is_geographically_allowed(job, config=cfg):
                            record_roadblock(paths.roadblocks_path, run_id, "job_filter", "Location did not match configured geography", url=url, title=job.title, action_taken="Excluded from dashboard", next_fallback="Inspect the saved LinkedIn snapshot if geography is visibly eligible", job=job)
                            continue

                        job.contacts = discover_linkedin_contacts(job.company, wellfound_contacts=job.contacts)
                        public_emails = extract_public_emails(job.description)
                        if public_emails:
                            if not job.contacts:
                                job.contacts.append(Contact(name="Visible LinkedIn job contact", role="Contact", source="linkedin_job_page"))
                            job.contacts[0].email = public_emails[0]
                            job.contacts[0].email_status = "verified_public"
                        job.outreach = generate_outreach(job, use_ai=args.use_ai_outreach and not args.skip_outreach)
                        actions = ledger.create_pending_actions_for_job(job, run_id)
                        pending_action_count += sum(1 for action in actions if action.status == "pending_approval")
                        append_jsonl(paths.run_dir / "jobs.jsonl", job.to_dict())
                        accepted_jobs.append(job)
                        accepted_identity_keys.add(identity_key)
                        linkedin_jobs_displayed += 1
            except BrowserAttachError:
                raise
            except Exception as exc:
                record_roadblock(
                    paths.roadblocks_path,
                    run_id,
                    "linkedin_setup",
                    str(exc),
                    action_taken="Continued with Wellfound results in the same dashboard",
                    next_fallback="Open LinkedIn in the shared JobApplier Chrome profile and log in if prompted",
                )

        append_jobs_csv(master_csv, accepted_jobs, run_id)
        roadblocks = read_roadblocks(paths.roadblocks_path)
        stats = {
            "jobs_checked": jobs_checked,
            "jobs_displayed": len(accepted_jobs),
            "jobs_not_shown": max(0, jobs_checked - len(accepted_jobs)) + skipped_terminal_jobs,
            "job_urls_discovered": len(unique_urls),
            "job_urls_found_total": len(job_urls),
            "wellfound_jobs_displayed": sum(1 for job in accepted_jobs if job.source == "wellfound"),
            "linkedin_job_urls_found_total": linkedin_urls_found,
            "linkedin_jobs_checked": linkedin_jobs_checked,
            "linkedin_jobs_displayed": linkedin_jobs_displayed,
            "skipped_terminal_jobs_from_data": skipped_terminal_jobs,
            "job_limit": args.limit,
            "pending_actions": pending_action_count,
            "action_counts": ledger.action_counts(),
        }
        dashboard = render_dashboard(paths.html_dir, run_id, accepted_jobs, roadblocks=roadblocks, stats=stats)
        summary = {
            "run_id": run_id,
            "jobs": len(accepted_jobs),
            "dashboard": str(dashboard),
            "fetched_pages": str(paths.fetched_pages_dir),
            "roadblocks": str(paths.roadblocks_path),
            "ledger": str(args.ledger_path),
            **stats,
        }
        (paths.run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


def main() -> int:
    try:
        summary = run()
    except BrowserAttachError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
