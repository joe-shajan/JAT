#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


POSTED_RE = re.compile(
    r"\bPOSTED\s+(TODAY|YESTERDAY|1 DAY AGO|2 DAYS AGO|\d+\s+(?:DAYS?|WEEKS?|MONTHS?)\s+AGO)\b",
    re.IGNORECASE,
)


def posted_bucket(text: str) -> str:
    match = POSTED_RE.search(text)
    if not match:
        return "not visible"
    return "POSTED " + match.group(1).upper()


def is_job_href(href: str) -> bool:
    return "wellfound.com/jobs/" in href and not any(
        suffix in href for suffix in ("/jobs/home", "/jobs/applications", "/jobs/messages", "/jobs/starred", "/jobs/hidden")
    )


def normalize_title(text: str) -> str:
    before_posted = POSTED_RE.split(text, maxsplit=1)[0]
    before_flags = re.split(r"\b(?:RECRUITER RECENTLY ACTIVE|POSTED)\b", before_posted, maxsplit=1, flags=re.I)[0]
    return " ".join(before_flags.split())


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Wellfound job links from saved browser snapshots.")
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    jobs: dict[str, dict[str, Any]] = {}
    snapshots = sorted(args.snapshot_dir.glob("*.json"))
    for snapshot_path in snapshots:
        snapshot = load_snapshot(snapshot_path)
        for anchor in snapshot.get("anchors", []):
            href = anchor.get("href", "")
            text = " ".join(str(anchor.get("text", "")).split())
            if not href or not text or not is_job_href(href):
                continue
            existing = jobs.get(href)
            row = {
                "job_url": href,
                "visible_text": text,
                "title_location_comp": normalize_title(text),
                "posted_label": posted_bucket(text),
                "first_snapshot": snapshot_path.name,
                "last_snapshot": snapshot_path.name,
            }
            if existing:
                existing["last_snapshot"] = snapshot_path.name
                if len(text) > len(str(existing.get("visible_text", ""))):
                    existing["visible_text"] = text
                    existing["title_location_comp"] = normalize_title(text)
                    existing["posted_label"] = posted_bucket(text)
            else:
                jobs[href] = row

    ordered = list(jobs.values())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in ordered:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    buckets = Counter(row["posted_label"] for row in ordered)
    fresh_labels = {"POSTED TODAY", "POSTED YESTERDAY", "POSTED 1 DAY AGO", "POSTED 2 DAYS AGO"}
    summary = {
        "snapshots": len(snapshots),
        "unique_jobs": len(ordered),
        "fresh_or_boundary_jobs": sum(1 for row in ordered if row["posted_label"] in fresh_labels),
        "posted_buckets": dict(buckets),
        "out": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
