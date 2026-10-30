from __future__ import annotations

from datetime import datetime

from job_research.models import JobRecord, Roadblock
from job_research.storage import append_jsonl


def record_roadblock(path, run_id: str, stage: str, reason: str, url: str = "", title: str = "", company: str = "", location: str = "", posted_at_text: str = "", action_taken: str = "", next_fallback: str = "", job: JobRecord | None = None) -> Roadblock:
    if job is not None:
        title = title or job.title
        company = company or job.company
        location = location or job.location
        posted_at_text = posted_at_text or job.posted_at_text
    roadblock = Roadblock(
        run_id=run_id,
        stage=stage,
        url=url,
        title=title,
        company=company,
        location=location,
        posted_at_text=posted_at_text,
        reason=reason,
        action_taken=action_taken,
        next_fallback=next_fallback,
        created_at=datetime.now(),
    )
    append_jsonl(path, roadblock.to_dict())
    return roadblock
