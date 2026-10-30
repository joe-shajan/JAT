from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from job_research.models import JobRecord, Roadblock


def _short(value: str, limit: int = 360) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _roadblock_data(rb: Roadblock | dict[str, Any]) -> dict[str, Any]:
    if isinstance(rb, Roadblock):
        return rb.to_dict()
    return dict(rb)


def _contact_data(job: JobRecord) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for contact in job.contacts:
        contacts.append(contact.to_dict())
    return contacts


def _job_data(job: JobRecord, index: int) -> dict[str, Any]:
    email_text = ""
    if job.outreach.email_subject or job.outreach.email_body:
        email_text = f"Subject: {job.outreach.email_subject}\n\n{job.outreach.email_body}".strip()
    brief = "\n".join(
        part
        for part in [
            f"{job.title} — {job.company}",
            job.job_url,
            f"Location: {job.location or 'not visible'}",
            f"Remote: {job.remote_status or 'not visible'}",
            f"Compensation: {job.compensation or 'not visible'}",
            f"Posted: {job.posted_at_text or 'not visible'}",
            f"Status: {job.ledger_status or job.status or 'new'}",
        ]
        if part
    )
    return {
        "index": index,
        "job_url": job.job_url,
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location or "not visible",
        "remote_status": job.remote_status or "not visible",
        "compensation": job.compensation or "not visible",
        "posted_at_text": job.posted_at_text or "not visible",
        "status": job.ledger_status or job.status or "new",
        "action_summary": job.action_summary or "Pending manual review; nothing sent automatically.",
        "notes": _short(job.description or job.notes or "No notes captured."),
        "matched_skills": job.matched_skills,
        "gaps": job.gaps,
        "fit_score": job.fit_score,
        "contacts": _contact_data(job),
        "outreach": job.outreach.to_dict(),
        "email_text": email_text,
        "brief": brief,
        "search_text": " ".join(
            [
                job.title,
                job.company,
                job.location,
                job.remote_status,
                job.compensation,
                job.posted_at_text,
                job.description,
                job.notes,
                " ".join(job.matched_skills),
                " ".join(c.name for c in job.contacts),
            ]
        ).lower(),
    }


def _dashboard_payload(
    run_id: str,
    jobs: list[JobRecord],
    roadblocks: list[Roadblock] | list[dict[str, Any]] | None = None,
    stats: dict[str, object] | None = None,
) -> dict[str, Any]:
    roadblocks = roadblocks or []
    stats = stats or {}
    def stat_int(name: str, default: int = 0) -> int:
        value = cast(Any, stats.get(name, default))
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    jobs_checked = stat_int("jobs_checked", len(jobs))
    jobs_displayed = stat_int("jobs_displayed", len(jobs))
    jobs_not_shown = max(0, jobs_checked - jobs_displayed)
    pending_actions = stat_int("pending_actions")
    skipped_terminal_jobs = stat_int("skipped_terminal_jobs_from_data")
    contacts_count = sum(len(job.contacts) for job in jobs)
    source_counts: dict[str, int] = {}
    for job in jobs:
        source_counts[job.source] = source_counts.get(job.source, 0) + 1
    return {
        "run_summary": {
            "run_id": run_id,
            "jobs_checked": jobs_checked,
            "jobs_displayed": jobs_displayed,
            "jobs_not_shown": jobs_not_shown,
            "pending_actions": pending_actions,
            "skipped_terminal_jobs_from_data": skipped_terminal_jobs,
            "contacts": contacts_count,
            "roadblocks": len(roadblocks),
            "source_counts": source_counts,
            "mode": "unranked",
        },
        "jobs": [_job_data(job, index) for index, job in enumerate(jobs, start=1)],
        "roadblocks": [_roadblock_data(rb) for rb in roadblocks],
    }


def render_dashboard(
    output_dir: Path,
    run_id: str,
    jobs: list[JobRecord],
    roadblocks: list[Roadblock] | list[dict[str, Any]] | None = None,
    stats: dict[str, object] | None = None,
) -> Path:
    """Publish dashboard data without rewriting the static HTML shell.

    The dashboard architecture is intentionally split:
    - reports/html/latest/index.html is a stable, hand-designed shell.
    - each workflow run only updates data.json and data.js.

    data.js mirrors the JSON payload as a window variable so the dashboard can
    be opened directly from the filesystem. Browsers generally block
    fetch('data.json') from file:// pages, but allow a sibling script file.

    Set JOBAPPLIER_ALLOW_HTML_BOOTSTRAP=1 for one-off bootstrapping in a new
    output directory; routine scheduled workflows leave HTML untouched.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _dashboard_payload(run_id, jobs, roadblocks=roadblocks, stats=stats)
    data_path = output_dir / "data.json"
    payload_text = json.dumps(payload, indent=2, ensure_ascii=False)
    data_path.write_text(payload_text + "\n", encoding="utf-8")
    (output_dir / "data.js").write_text(f"window.JOBAPPLIER_DASHBOARD_DATA = {payload_text};\n", encoding="utf-8")

    index_path = output_dir / "index.html"
    if not index_path.exists():
        template = Path(__file__).resolve().parents[1] / 'templates' / 'dashboard.html'
        index_path.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
    return index_path
