#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))

from job_research.found_jobs_report import (
    load_contacts_by_job,
    load_email_leads_by_job,
    load_found_jobs,
    render_found_jobs_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the current found Wellfound jobs as a static HTML report.")
    parser.add_argument("--run-id", help="Run ID to render. Defaults to the newest run with a supported found-jobs artifact.")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--contacts", type=Path)
    parser.add_argument("--emails", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--also-latest-found", action="store_true", help="Also write reports/html/latest-found/index.html")
    return parser.parse_args()


def candidate_sources(run_dir: Path) -> list[Path]:
    return [
        run_dir / "jobs-41-cache-48h.jsonl",
        run_dir / "jobs-fresh-corrected-current-combined.jsonl",
        run_dir / "jobs.jsonl",
    ]


def is_sample_run(run_dir: Path) -> bool:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return False
    try:
        return bool(json.loads(summary_path.read_text(encoding="utf-8")).get("sample"))
    except json.JSONDecodeError:
        return False


def latest_run_id() -> str:
    runs_dir = WORKSPACE / "runs"
    candidates: list[tuple[float, str]] = []
    for run_dir in runs_dir.iterdir() if runs_dir.exists() else []:
        if not run_dir.is_dir():
            continue
        if is_sample_run(run_dir):
            continue
        sources = [source for source in candidate_sources(run_dir) if source.exists()]
        if not sources:
            continue
        newest_source = max(sources, key=lambda source: source.stat().st_mtime)
        candidates.append((newest_source.stat().st_mtime, run_dir.name))
    if not candidates:
        raise SystemExit("No run with a supported found-jobs artifact exists yet.")
    return max(candidates)[1]


def default_source(run_id: str) -> Path:
    run_dir = WORKSPACE / "runs" / run_id
    for source in candidate_sources(run_dir):
        if source.exists():
            return source
    raise SystemExit(f"No supported found-jobs artifact exists for run {run_id}.")


def load_summary(run_id: str) -> dict:
    path = WORKSPACE / "runs" / run_id / "summary.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def render_one(
    output_dir: Path,
    run_id: str,
    source: Path,
    summary: dict,
    status_note: str,
    contacts_path: Path | None = None,
    emails_path: Path | None = None,
) -> Path:
    jobs = load_found_jobs(source, source_label=source.name)
    default_contacts_path = WORKSPACE / "runs" / run_id / "contacts.jsonl"
    selected_contacts_path = contacts_path or default_contacts_path
    contacts_by_job = load_contacts_by_job(selected_contacts_path if selected_contacts_path.exists() else None)
    default_emails_path = WORKSPACE / "runs" / run_id / "email-leads.jsonl"
    selected_emails_path = emails_path or default_emails_path
    email_leads_by_job = load_email_leads_by_job(selected_emails_path if selected_emails_path.exists() else None)
    result_count = summary.get("current_authoritative_capture", {}).get("visible_result_count", "")
    return render_found_jobs_report(
        output_dir,
        run_id,
        jobs,
        source_path=source,
        contacts_by_job=contacts_by_job,
        email_leads_by_job=email_leads_by_job,
        generated_at=datetime.now(timezone.utc),
        status_note=status_note,
        visible_result_count=str(result_count or ""),
    )


def main() -> int:
    args = parse_args()
    run_id = args.run_id or latest_run_id()
    source = args.source or default_source(run_id)
    output_dir = args.output_dir or (WORKSPACE / "reports" / "html" / run_id)
    summary = load_summary(run_id)
    status_note = (
        "Current verified capture. The Wellfound scan is paused because automated scrolling is not loading the next result slice; "
        "jobs shown here are from the saved current artifact and should be reviewed manually."
    )
    out = render_one(output_dir, run_id, source, summary, status_note, args.contacts, args.emails)
    latest_out = ""
    if args.also_latest_found:
        latest_out = str(
            render_one(
                WORKSPACE / "reports" / "html" / "latest-found",
                run_id,
                source,
                summary,
                status_note,
                args.contacts,
                args.emails,
            )
        )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "source": str(source),
                "dashboard": str(out),
                "latest_found": latest_out,
                "jobs": sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
