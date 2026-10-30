from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Iterable

from job_research.models import Contact


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LINKEDIN_PROFILE_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%\-./]+", re.I)
WELLFOUND_PROFILE_RE = re.compile(r"https?://wellfound\.com/u/[A-Za-z0-9_-]+", re.I)


def extract_public_emails(text: str) -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for email in EMAIL_RE.findall(text):
        lowered = email.lower()
        if lowered not in seen:
            seen.add(lowered)
            emails.append(lowered)
    return emails


def normalize_linkedin_profile_url(url: str) -> str:
    clean_url = url.strip().rstrip('.,)\"\'')
    if clean_url.startswith("//"):
        clean_url = "https:" + clean_url
    elif not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url
    return clean_url.replace("http://", "https://")


def extract_linkedin_profile_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for url in LINKEDIN_PROFILE_RE.findall(text):
        clean_url = normalize_linkedin_profile_url(url)
        if clean_url not in seen:
            seen.add(clean_url)
            urls.append(clean_url)
    return urls


def build_linkedin_search_queries(company: str, names: list[str] | None = None) -> list[str]:
    name_queries = [f'site:linkedin.com/in "{name}" "{company}"' for name in (names or []) if name.strip()]
    return name_queries + [
        f'site:linkedin.com/in "{company}" founder',
        f'site:linkedin.com/in "{company}" co-founder',
        f'site:linkedin.com/in "{company}" recruiter OR talent OR hr',
        f'site:linkedin.com/in "{company}" hiring manager',
    ]


def build_linkedin_search_urls(company: str, names: list[str] | None = None) -> list[str]:
    return ["https://www.google.com/search?q=" + urllib.parse.quote_plus(query) for query in build_linkedin_search_queries(company, names)]


def extract_wellfound_profile_urls_by_name(lines: list[str]) -> dict[str, str]:
    """Map visible person names to Wellfound ``/u/...`` URLs from saved page anchors.

    The browser fetcher saves visible text first, then appends anchors later in
    the artifact. Wellfound job cards often render the person name in the team
    section and put the matching ``/u/<slug>`` anchor in that later dump, so the
    URL is not necessarily near the original name line.
    """
    urls_by_name: dict[str, str] = {}
    for index, line in enumerate(lines):
        normalized = " ".join(line.split())
        if not normalized or re.search(r"\d|https?://|@", normalized):
            continue
        if not re.search(r"[A-Za-z]", normalized) or " " not in normalized:
            continue
        for nearby in lines[index + 1 : index + 4]:
            match = WELLFOUND_PROFILE_RE.search(nearby)
            if match and "/profile/edit" not in match.group(0):
                urls_by_name.setdefault(normalized, match.group(0).rstrip('.,)\"\''))
                break
            if nearby and not WELLFOUND_PROFILE_RE.search(nearby):
                break
    return urls_by_name


def extract_wellfound_contacts(text: str, company: str, source_url: str = "") -> list[Contact]:
    """Extract people visible on the Wellfound job/company page before public web search.

    Wellfound often shows a "Meet your team" / "People you would work with"
    section. When the page exposes linkedin.com/in URLs we keep them as first
    class contacts; otherwise we keep the visible names as Wellfound-sourced
    seeds so later fallback search can target those people instead of only the
    company name.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    contacts: list[Contact] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    profile_urls_by_name = extract_wellfound_profile_urls_by_name(lines)
    for clean_url in extract_linkedin_profile_urls(text):
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        contacts.append(
            Contact(
                name="Wellfound visible LinkedIn profile",
                role="Wellfound associated person",
                linkedin_url=clean_url,
                source="wellfound_visible_profile",
                source_url=source_url,
                confidence=0.85,
                notes="LinkedIn profile URL was visible on Wellfound before public web search.",
            )
        )

    start = next((i for i, line in enumerate(lines) if re.search(r"meet your team|people you would work with|job poster|hiring team|hiring contact", line, re.I)), -1)
    if start == -1:
        return dedupe_contacts(contacts)
    stop_markers = re.compile(r"similar jobs|view more jobs|apply now|skills|remote work policy|company location|^culture$", re.I)
    noisy = {
        "meet your team",
        "people you would work with in this role",
        "people you would work with",
        "hiring contact",
        "employee",
        "india",
        "united states",
        "remote",
        "view all photos and perks",
        "work from home flexibility",
    }
    candidate_lines = lines[start + 1 : start + 25]
    for offset, line in enumerate(candidate_lines):
        if stop_markers.search(line):
            break
        normalized = " ".join(line.split())
        lowered = normalized.lower()
        if lowered in noisy or len(normalized) > 80 or len(normalized.split()) > 5:
            continue
        if re.search(r"\d|https?://|@", normalized):
            continue
        if not re.search(r"[A-Za-z]", normalized) or " " not in normalized:
            continue
        if normalized in seen_names:
            continue
        profile_url = profile_urls_by_name.get(normalized, "")
        for nearby in candidate_lines[offset + 1 : offset + 5]:
            if stop_markers.search(nearby):
                break
            match = WELLFOUND_PROFILE_RE.search(nearby)
            if match and "/profile/edit" not in match.group(0):
                profile_url = match.group(0).rstrip('.,)\"\'')
                break
        seen_names.add(normalized)
        contacts.append(
            Contact(
                name=normalized,
                role="Wellfound associated person",
                source="wellfound_profile_link" if profile_url else "wellfound_job_page",
                source_url=profile_url or source_url,
                confidence=0.45,
                notes=(
                    "Visible in Wellfound Meet your team / associated people section with a Wellfound profile link; "
                    "LinkedIn URL will be resolved from that profile page when visible."
                    if profile_url
                    else "Visible in Wellfound Meet your team / associated people section; LinkedIn URL not visible on Wellfound."
                ),
                manual_search_urls=[] if profile_url else build_linkedin_search_urls(company, [normalized]),
            )
        )
    return dedupe_contacts(contacts)


def enrich_contacts_from_wellfound_profiles(contacts: list[Contact], fetch_profile_text) -> list[Contact]:
    """Resolve Wellfound ``/u/...`` contact pages into real LinkedIn URLs when visible."""
    enriched: list[Contact] = []
    profile_cache: dict[str, str] = {}
    for contact in contacts:
        profile_url = contact.source_url if WELLFOUND_PROFILE_RE.fullmatch(contact.source_url or "") else ""
        if contact.linkedin_url or not profile_url:
            enriched.append(contact)
            continue
        if profile_url not in profile_cache:
            profile_cache[profile_url] = fetch_profile_text(profile_url) or ""
        linkedin_urls = extract_linkedin_profile_urls(profile_cache[profile_url])
        if linkedin_urls:
            contact.linkedin_url = linkedin_urls[0]
            contact.source = "wellfound_profile_page"
            contact.confidence = max(contact.confidence, 0.9)
            contact.notes = "LinkedIn profile URL was retrieved from the visible Wellfound profile page."
            contact.manual_search_urls = []
        else:
            contact.manual_search_urls = contact.manual_search_urls or build_linkedin_search_urls("", [contact.name])
            contact.notes = contact.notes or "Wellfound profile page did not expose a LinkedIn profile URL."
        enriched.append(contact)
    return dedupe_contacts(enriched)


def result_mentions_company(title: str, snippet: str, company: str) -> bool:
    text = f" {title} {snippet} ".lower()
    normalized_text = " ".join(text.replace("-", " ").split())
    normalized_company = " ".join(company.lower().replace("-", " ").split())
    if not normalized_company:
        return False
    normalized_title_segments = [" ".join(segment.replace("-", " ").split()) for segment in re.split(r"\s[-|–—]\s", title.lower())]
    if any(segment == normalized_company for segment in normalized_title_segments[1:]):
        return True
    company_pattern = re.escape(normalized_company).replace(r"\ ", r"[\s-]+")
    suffix = r"\b(?![/.-]|\s+[a-z0-9])" if len(normalized_company) <= 3 else r"\b(?![/.-])"
    employer_patterns = (
        rf"\b(?:at|@|with|for)\s+{company_pattern}{suffix}",
        rf"\b(?:ceo|cto|founder|co-founder|cofounder|recruiter|talent|people|hr)\s+(?:at\s+)?{company_pattern}{suffix}",
        rf"\b(?:ceo|cto|founder|co-founder|cofounder|recruiter|talent|people|hr)\s*,\s*{company_pattern}{suffix}",
    )
    if any(re.search(pattern, normalized_text) for pattern in employer_patterns):
        return True
    company_terms = [term for term in re.split(r"\W+", normalized_company) if len(term) > 2]
    if len(company_terms) >= 2 and all(re.search(rf"\b{re.escape(term)}\b", normalized_text) for term in company_terms):
        return bool(re.search(r"\b(?:founder|co-founder|cofounder|ceo|cto|recruiter|talent|people|hr|hiring)\b", normalized_text))
    return False


def dedupe_contacts(contacts: Iterable[Contact]) -> list[Contact]:
    seen: set[str] = set()
    deduped: list[Contact] = []
    for contact in contacts:
        key = contact.linkedin_url or contact.email or f"{contact.name}:{contact.role}"
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(contact)
    return deduped


def discover_linkedin_contacts(company: str, wellfound_contacts: list[Contact] | None = None, min_wellfound_linkedin_profiles: int = 2) -> list[Contact]:
    """Prefer Wellfound-visible people, then public web search only if needed.

    Logged-in LinkedIn collection is allowed by project policy only when the user
    explicitly authorizes that browser session/task. Public search is a fallback
    only when Wellfound did not provide at least two LinkedIn profile URLs.
    """
    wellfound_contacts = dedupe_contacts(wellfound_contacts or [])
    wellfound_linkedin_count = sum(1 for contact in wellfound_contacts if contact.linkedin_url)
    if wellfound_linkedin_count >= min_wellfound_linkedin_profiles:
        return wellfound_contacts

    wellfound_names = [contact.name for contact in wellfound_contacts if contact.name and not contact.name.startswith("Manual ")]
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return dedupe_contacts(wellfound_contacts + [
            Contact(
                name="Manual LinkedIn search needed",
                role="Founder/recruiter/job poster",
                source="manual_search",
                confidence=0.0,
                notes="Wellfound did not expose at least two LinkedIn profiles; set BRAVE_SEARCH_API_KEY for automatic public fallback.",
                manual_search_urls=build_linkedin_search_urls(company, wellfound_names),
            )
        ])

    contacts: list[Contact] = []
    for query in build_linkedin_search_queries(company, wellfound_names):
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 5})
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        for item in data.get("web", {}).get("results", []):
            result_url = item.get("url", "")
            if "linkedin.com/in/" not in result_url:
                continue
            title = item.get("title") or ""
            snippet = item.get("description") or ""
            if not result_mentions_company(title, snippet, company):
                continue
            contacts.append(
                Contact(
                    name=title.split("|")[0].strip(),
                    role="public LinkedIn search result",
                    linkedin_url=result_url,
                    source="brave_search",
                    source_url=result_url,
                    confidence=0.5,
                    notes=f"Query: {query}",
                )
            )
    return dedupe_contacts(wellfound_contacts + contacts) or [
        Contact(
            name="Manual LinkedIn search needed",
            role="Founder/recruiter/job poster",
            source="manual_search_no_results",
            manual_search_urls=build_linkedin_search_urls(company, wellfound_names),
        )
    ]
