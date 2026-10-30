from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunPaths:
    workspace: Path
    run_id: str
    run_dir: Path
    fetched_pages_dir: Path
    search_pages_dir: Path
    job_pages_dir: Path
    company_pages_dir: Path
    contact_pages_dir: Path
    manifest_path: Path
    roadblocks_path: Path
    html_dir: Path


def slugify(value: str, max_len: int = 90) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (value or "artifact")[:max_len]


def create_run_paths(workspace: Path, run_id: str, html_dir: Path | None = None) -> RunPaths:
    run_dir = workspace / "runs" / run_id
    fetched = workspace / "fetched_pages" / run_id
    paths = RunPaths(
        workspace=workspace,
        run_id=run_id,
        run_dir=run_dir,
        fetched_pages_dir=fetched,
        search_pages_dir=fetched / "search-pages",
        job_pages_dir=fetched / "job-pages",
        company_pages_dir=fetched / "company-pages",
        contact_pages_dir=fetched / "contact-pages",
        manifest_path=fetched / "manifest.jsonl",
        roadblocks_path=run_dir / "roadblocks.jsonl",
        html_dir=html_dir or workspace / "reports" / "html" / run_id,
    )
    for directory in (
        paths.run_dir,
        paths.fetched_pages_dir,
        paths.search_pages_dir,
        paths.job_pages_dir,
        paths.company_pages_dir,
        paths.contact_pages_dir,
        paths.html_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def save_text_artifact(directory: Path, title: str, text: str, suffix: str = ".md") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slugify(title)}{suffix}"
    counter = 2
    while path.exists():
        path = directory / f"{slugify(title)}-{counter}{suffix}"
        counter += 1
    path.write_text(text, encoding="utf-8")
    return path


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
