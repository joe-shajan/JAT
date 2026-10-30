from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
GENERIC_LOCAL_PARTS = (
    "admin",
    "ask",
    "careers",
    "community",
    "connect",
    "contact",
    "help",
    "hello",
    "hr",
    "info",
    "invest",
    "jobs",
    "legal",
    "ops",
    "partner",
    "partners",
    "pr",
    "press",
    "privacy",
    "recruiting",
    "sales",
    "security",
    "support",
    "talent",
)
PUBLIC_PAGE_PATHS = ("", "/contact", "/careers", "/jobs")
REQUEST_TIMEOUT_SECONDS = 2
MAX_DOMAIN_CANDIDATES = 3
FALSE_EMAIL_PARTS = (
    "company.com",
    "contoso.",
    "example.",
    "email.com",
    "domain.com",
    "github.com",
    "mail.io",
    "sample.",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "yourname@",
)
FALSE_EMAIL_LOCAL_PARTS = ("first.last", "firstname.lastname", "name", "you", "your.name", "yourname")

DOMAIN_OVERRIDES: dict[str, list[str]] = {
    "Abstrabit Technologies": ["abstrabit.com"],
    "Appomni": ["appomni.com"],
    "Correlation One": ["correlation-one.com"],
    "Datakrew": ["datakrew.com"],
    "Galaxy Digital": ["galaxydigital.io"],
    "Housecall": ["housecallpro.com"],
    "Infoxen": ["infoxen.com"],
    "Karat Io": ["karat.com", "karat.io"],
    "Kuvaka_tech": ["kuvaka.tech"],
    "Modemobile": ["modemobile.com"],
    "Pantheon": ["pantheon.io"],
    "Perfectserve": ["perfectserve.com"],
    "Readme": ["readme.com"],
    "Vested Finance": ["vestedfinance.com"],
    "Yipitdata": ["yipitdata.com"],
    "Zerigo Health": ["zerigohealth.com"],
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def newest_run_id() -> str:
    candidates: list[tuple[float, str]] = []
    for run_dir in (WORKSPACE / "runs").iterdir():
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


def compact_text(value: object) -> str:
    return " ".join(str(value or "").split())


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split("?")[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def company_slug(company_url: str) -> str:
    return urllib.parse.urlsplit(company_url).path.rstrip("/").split("/")[-1]


def company_domain_candidates(company_name: str, company_url: str = "", notes: str = "") -> list[str]:
    candidates: list[str] = []
    candidates.extend(DOMAIN_OVERRIDES.get(company_name, []))
    text = f"{company_name} {notes}"
    if "Housecall Pro" in text:
        candidates.append("housecallpro.com")
    if "Mode Mobile" in text:
        candidates.append("modemobile.com")
    slug = company_slug(company_url) if company_url else ""
    raw_names = [company_name, slug.replace("-", " ").replace("_", " ")]
    for raw_name in raw_names:
        base = re.sub(r"\b(inc|llc|ltd|pty|technologies|technology|consulting|group|business|labs|health)\b", "", raw_name, flags=re.I)
        compact = re.sub(r"[^a-z0-9]", "", base.lower())
        hyphen = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
        for stem in (compact, hyphen):
            if len(stem) < 4:
                continue
            for suffix in (".com", ".io", ".ai", ".co", ".tech"):
                candidates.append(stem + suffix)
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        domain = normalize_domain(candidate)
        if domain and domain not in seen:
            seen.add(domain)
            unique.append(domain)
    return unique[:MAX_DOMAIN_CANDIDATES]


def clean_email(email: str) -> str:
    return email.strip(".,;:)>]}'\"").lower()


def extract_public_emails(text: str) -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for raw_email in EMAIL_RE.findall(text):
        email = clean_email(raw_email)
        local = email.split("@", 1)[0]
        if any(part in email for part in FALSE_EMAIL_PARTS):
            continue
        if local in FALSE_EMAIL_LOCAL_PARTS:
            continue
        if email.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".js", ".css")):
            continue
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def request_text(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/149 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def fetch_domain_page(domain: str, page_path: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    for host_prefix in ("www.", ""):
        url = f"https://{host_prefix}{domain}{page_path}"
        try:
            return url, request_text(url, timeout=timeout), errors
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    return "", "", errors


def compact_domain_stem(value: str) -> str:
    domain = normalize_domain(value)
    stem = domain.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]", "", stem)


def compact_company_stem(value: str) -> str:
    base = re.sub(r"\b(inc|llc|ltd|pty|technologies|technology|consulting|group|business|labs|health)\b", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def same_org_email(email: str, domain: str, company_name: str) -> bool:
    if "@" not in email:
        return False
    email_domain = normalize_domain(email.rsplit("@", 1)[1])
    source_domain = normalize_domain(domain)
    if email_domain == source_domain or email_domain.endswith(f".{source_domain}"):
        return True
    source_stem = compact_domain_stem(source_domain)
    email_stem = compact_domain_stem(email_domain)
    company_stem = compact_company_stem(company_name)
    if source_stem and email_stem and (source_stem in email_stem or email_stem in source_stem):
        return True
    if len(company_stem) >= 4 and email_stem and (company_stem in email_stem or email_stem in company_stem):
        return True
    return False


def public_email_rows(text: str, source_url: str, domain: str, company_name: str) -> list[dict[str, str]]:
    return [
        {"email": email, "source_url": source_url}
        for email in extract_public_emails(text)
        if same_org_email(email, domain, company_name)
    ]


def fetch_first_party_emails(
    domain: str,
    company_name: str,
    delay: float = 0.2,
    page_paths: tuple[str, ...] = PUBLIC_PAGE_PATHS,
) -> tuple[list[dict[str, str]], list[str]]:
    found: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for page_path in page_paths:
        url, text, page_errors = fetch_domain_page(domain, page_path)
        errors.extend(page_errors)
        if not text:
            continue
        for email_info in public_email_rows(text, url, domain, company_name):
            email = email_info["email"]
            if email in seen:
                continue
            seen.add(email)
            found.append(email_info)
        if found:
            break
        if delay:
            time.sleep(delay)
    return found, errors


def email_status(email: str, source: str) -> str:
    local = email.split("@", 1)[0].lower()
    if source == "derived":
        return "unverified_candidate"
    if local in GENERIC_LOCAL_PARTS:
        return "generic_public"
    return "verified_public"


def best_domain(company_name: str, company_url: str, notes: str, delay: float) -> tuple[str, list[dict[str, str]], list[str], list[str]]:
    candidates = company_domain_candidates(company_name, company_url, notes)
    errors: list[str] = []
    for domain in candidates:
        homepage_url, homepage_text, homepage_errors = fetch_domain_page(domain, "")
        errors.extend(homepage_errors[:2])
        if not homepage_text:
            continue
        homepage_emails = public_email_rows(homepage_text, homepage_url, domain, company_name)
        if homepage_emails:
            return domain, homepage_emails, errors, candidates
        emails, fetch_errors = fetch_first_party_emails(domain, company_name, delay=delay, page_paths=PUBLIC_PAGE_PATHS[1:])
        errors.extend(fetch_errors[:2])
        return domain, emails, errors, candidates
    return "", [], errors, candidates


def generic_candidates(domain: str) -> list[str]:
    if not domain:
        return []
    return [f"{local}@{domain}" for local in ("careers", "jobs", "recruiting")]


def contact_key(contact: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(contact.get("job_url") or ""),
        str(contact.get("company_name") or ""),
        str(contact.get("name") or ""),
        str(contact.get("linkedin_url") or ""),
    )


def enrich_run(run_id: str, *, max_companies: int, delay: float) -> dict[str, Any]:
    run_dir = WORKSPACE / "runs" / run_id
    contacts_path = run_dir / "contacts.jsonl"
    if not contacts_path.exists():
        raise SystemExit(f"Missing contacts artifact. Run linkedin-leads first: {contacts_path}")
    contacts = read_jsonl(contacts_path)
    contacts_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for contact in contacts:
        contacts_by_company[str(contact.get("company_name") or "not visible")].append(contact)

    email_rows: list[dict[str, Any]] = []
    roadblocks: list[dict[str, Any]] = []
    searched = 0
    domain_cache: dict[str, tuple[str, list[dict[str, str]], list[str], list[str]]] = {}
    emitted: set[tuple[str, str, str, str, str]] = set()

    for company_name, grouped_contacts in contacts_by_company.items():
        if searched >= max_companies:
            for contact in grouped_contacts:
                roadblocks.append(email_roadblock(run_id, contact, "company-limit", "Max company search limit reached."))
            continue
        searched += 1
        company_url = str(grouped_contacts[0].get("company_url") or "")
        notes = " ".join(str(contact.get("notes") or "") for contact in grouped_contacts)
        if company_name not in domain_cache:
            domain_cache[company_name] = best_domain(company_name, company_url, notes, delay)
        domain, public_emails, errors, candidates = domain_cache[company_name]
        for contact in grouped_contacts:
            if public_emails:
                for email_info in public_emails[:4]:
                    email = email_info["email"]
                    key = (*contact_key(contact), email)
                    if key in emitted:
                        continue
                    emitted.add(key)
                    email_rows.append(email_row(run_id, contact, domain, email, email_status(email, "public"), email_info["source_url"], "first_party_website", 0.86))
                continue
            if domain:
                for email in generic_candidates(domain):
                    key = (*contact_key(contact), email)
                    if key in emitted:
                        continue
                    emitted.add(key)
                    email_rows.append(
                        email_row(
                            run_id,
                            contact,
                            domain,
                            email,
                            "unverified_candidate",
                            f"https://{domain}",
                            "domain_candidate",
                            0.35,
                            "Domain resolved but no public email was visible on bounded first-party pages.",
                        )
                    )
                roadblocks.append(email_roadblock(run_id, contact, "email-not-visible", "Domain resolved, but no public email was visible.", "; ".join(errors[:2])))
                continue
            roadblocks.append(
                email_roadblock(
                    run_id,
                    contact,
                    "domain-not-found",
                    "Could not resolve a reachable company domain from safe candidates.",
                    f"tried: {', '.join(candidates[:8])}; errors: {'; '.join(errors[:2])}",
                )
            )

    leads_path = run_dir / "email-leads.jsonl"
    roadblocks_path = run_dir / "email-roadblocks.jsonl"
    write_jsonl(leads_path, email_rows)
    write_jsonl(roadblocks_path, roadblocks)
    summary = {
        "run_id": run_id,
        "contacts": len(contacts),
        "companies": len(contacts_by_company),
        "companies_searched": searched,
        "email_leads": len(email_rows),
        "verified_public": sum(1 for row in email_rows if row.get("status") == "verified_public"),
        "generic_public": sum(1 for row in email_rows if row.get("status") == "generic_public"),
        "unverified_candidate": sum(1 for row in email_rows if row.get("status") == "unverified_candidate"),
        "roadblocks": len(roadblocks),
        "email_leads_path": str(leads_path),
        "email_roadblocks_path": str(roadblocks_path),
    }
    (run_dir / "email-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def email_row(
    run_id: str,
    contact: dict[str, Any],
    domain: str,
    email: str,
    status: str,
    source_url: str,
    source: str,
    confidence: float,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "job_url": contact.get("job_url", ""),
        "job_title": contact.get("job_title", ""),
        "company_name": contact.get("company_name", ""),
        "company_domain": domain,
        "contact_name": contact.get("name", ""),
        "contact_role": contact.get("role", ""),
        "linkedin_url": contact.get("linkedin_url", ""),
        "email": email,
        "status": status,
        "confidence": confidence,
        "source": source,
        "source_url": source_url,
        "notes": notes,
    }


def email_roadblock(run_id: str, contact: dict[str, Any], stage: str, reason: str, detail: str = "") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "job_url": contact.get("job_url", ""),
        "company_name": contact.get("company_name", ""),
        "contact_name": contact.get("name", ""),
        "stage": stage,
        "reason": reason,
        "detail": detail,
        "action_taken": "recorded safe fallback or roadblock; did not send or submit anything",
        "next_fallback": "manual public search or approved email enrichment provider",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find safe public email candidates for a JobApplier run.")
    parser.add_argument("--run-id", default="", help="Run ID. Defaults to newest non-sample run with jobs.jsonl.")
    parser.add_argument("--max-companies", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args(argv)
    run_id = args.run_id or newest_run_id()
    result = enrich_run(run_id, max_companies=args.max_companies, delay=args.delay)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
