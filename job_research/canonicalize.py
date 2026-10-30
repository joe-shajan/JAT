from __future__ import annotations

import hashlib
import re
import urllib.parse

from job_research.models import JobRecord


def _clean_url(url: str) -> urllib.parse.SplitResult:
    value = (url or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("/"):
        value = "https://wellfound.com" + value
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    return urllib.parse.urlsplit(value)


def canonical_job_url(url: str) -> str:
    parsed = _clean_url(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path)
    linkedin_match = re.search(r"/jobs/view/(\d+)", path)
    if "linkedin.com" in host and linkedin_match:
        return f"https://www.linkedin.com/jobs/view/{linkedin_match.group(1)}"
    wellfound_match = re.search(r"/jobs/([^/?#]+)", path)
    if "wellfound.com" in host and wellfound_match:
        return f"https://wellfound.com/jobs/{wellfound_match.group(1)}"
    return urllib.parse.urlunsplit((parsed.scheme.lower() or "https", host, path, "", ""))


def canonical_linkedin_url(url: str) -> str:
    parsed = _clean_url(url)
    host = parsed.netloc.lower()
    if host in {"linkedin.com", "www.linkedin.com"}:
        host = "www.linkedin.com"
    path = re.sub(r"/+$", "", parsed.path)
    for prefix in ("/in/", "/company/"):
        if prefix in path:
            slug = path.split(prefix, 1)[1].split("/", 1)[0]
            return f"https://{host}{prefix}{slug}"
    return urllib.parse.urlunsplit(("https", host, path, "", ""))


def canonical_job_identity(job: JobRecord) -> tuple[str, str]:
    """Stable cross-source identity for the same company/title posting."""
    normalized_company = re.sub(r"[^a-z0-9]+", " ", job.company.lower()).strip()
    normalized_title = re.sub(r"[^a-z0-9]+", " ", job.title.lower()).strip()
    return normalized_company, normalized_title


def canonical_email(email: str) -> str:
    return (email or "").strip().strip(".,;:<>[](){}'\"").lower()


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def stable_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
