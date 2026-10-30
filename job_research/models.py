from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


@dataclass
class Contact:
    contact_id: str = ""
    name: str = ""
    role: str = ""
    linkedin_url: str = ""
    email: str = ""
    email_status: str = "not_found"  # not_found|verified_public|verified_api|unverified_candidate
    email_action_status: str = ""
    linkedin_dm_status: str = ""
    linkedin_connection_status: str = ""
    source: str = ""
    source_url: str = ""
    confidence: float = 0.0
    notes: str = ""
    manual_search_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class OutreachDraft:
    email_subject: str = ""
    email_body: str = ""
    cover_letter: str = ""
    linkedin_dm: str = ""
    linkedin_connection: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class SavedPage:
    run_id: str
    kind: str
    url: str
    saved_path: str
    status: str
    fetched_at: datetime
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class Roadblock:
    run_id: str
    stage: str
    url: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    posted_at_text: str = ""
    reason: str = ""
    action_taken: str = ""
    next_fallback: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class JobRecord:
    job_url: str
    title: str
    company: str
    source: str = "wellfound"
    location: str = ""
    remote_status: str = ""
    compensation: str = ""
    posted_at_text: str = ""
    posted_at: datetime | None = None
    allows_remote: bool = False
    allows_india: bool = False
    allows_relocation_or_visa: bool = False
    description: str = ""
    requirements: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    fit_score: int = 0
    source_urls: list[str] = field(default_factory=list)
    fetched_page_paths: list[str] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    outreach: OutreachDraft = field(default_factory=OutreachDraft)
    status: str = "new"
    ledger_status: str = ""
    action_summary: str = ""
    first_seen: str = ""
    last_seen: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)
