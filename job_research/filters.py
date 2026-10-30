from __future__ import annotations
import re
from datetime import datetime
from typing import Any
from job_research.config import load_config
from job_research.models import JobRecord


def matches_phrase(phrase: str, text: str) -> bool:
    # Literal phrases, never user-supplied regex. Java does not match JavaScript.
    words = re.split(r'[\s\-–—]+', phrase.strip().casefold())
    pattern = r'(?<!\w)' + r'[\s\-–—]+'.join(re.escape(word) for word in words) + r'(?!\w)'
    return bool(re.search(pattern, text.casefold()))


def has_user_excluded_role_keyword(job: JobRecord, config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else load_config()
    text = ' '.join([job.title, job.description, job.notes, ' '.join(job.requirements)])
    return any(matches_phrase(p, text) for p in cfg.get('roles', {}).get('avoid_keywords', []))


def is_recent_job(job: JobRecord, hours: int = 48, now: datetime | None = None) -> bool:
    label = ' '.join((job.posted_at_text or '').casefold().split())
    if re.search(r'\btoday\b|\bjust posted\b', label):
        return True
    if re.search(r'\byesterday\b', label):
        return hours >= 24
    match = re.search(r'\b(\d+)\s+(minute|hour|day|week)s?\s+ago\b', label)
    if not match:
        return False
    factor = {'minute': 1/60, 'hour': 1, 'day': 24, 'week': 168}[match[2]]
    return int(match[1]) * factor <= hours


def is_geographically_allowed(job: JobRecord, config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else load_config()
    allowed = cfg.get('locations', {}).get('include', [])
    exceptions = cfg.get('locations', {}).get('include_if_visible', [])
    # Do not infer residence/work authorization from a company's address in body text.
    location_text = ' '.join([job.location, job.remote_status])
    evidence = ' '.join([location_text, job.description, ' '.join(job.requirements)])
    job.allows_remote = bool(re.search(r'\bremote\b', location_text, re.I))
    job.allows_relocation_or_visa = any(matches_phrase(p, evidence) for p in exceptions)
    return not allowed or any(matches_phrase(p, location_text) for p in allowed) or job.allows_relocation_or_visa


def role_rejection_reason(job: JobRecord, learned_patterns: list[dict[str, Any]] | None = None, config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else load_config()
    text = ' '.join([job.title, job.description, job.notes, ' '.join(job.requirements)])
    for phrase in cfg.get('roles', {}).get('avoid_keywords', []):
        if matches_phrase(phrase, text):
            return f'Matched configured exclusion: "{phrase}"'
    targets = cfg.get('roles', {}).get('primary', [])
    if targets and not any(matches_phrase(p, job.title) for p in targets):
        return f'Title "{job.title}" matched no configured role title'
    return ''


def is_role_allowed(job: JobRecord, learned_patterns: list[dict[str, Any]] | None = None, config: dict[str, Any] | None = None) -> bool:
    return not role_rejection_reason(job, learned_patterns, config)
