from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LEARNING_PATH = Path(__file__).resolve().parents[1] / "data" / "manual_role_learning.json"
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
_ROLE_NOUNS = {"architect", "builder", "developer", "engineer"}
_GENERIC_TITLE_TOKENS = {
    "associate",
    "chief",
    "full",
    "fullstack",
    "head",
    "junior",
    "lead",
    "mid",
    "principal",
    "senior",
    "software",
    "sr",
    "staff",
    "stack",
    "team",
}
_TOKEN_ALIASES = {
    "angularjs": "angular",
    "javascript": "js",
    "next.js": "nextjs",
    "node.js": "nodejs",
    "typescript": "ts",
}


def _normalized_tokens(title: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall((title or "").lower()):
        token = _TOKEN_ALIASES.get(raw, raw)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def title_signature(title: str) -> dict[str, list[str] | str]:
    tokens = _normalized_tokens(title)
    role_nouns = [token for token in tokens if token in _ROLE_NOUNS]
    domain_tokens = [token for token in tokens if token not in _ROLE_NOUNS and token not in _GENERIC_TITLE_TOKENS]
    return {
        "normalized_title": " ".join(tokens),
        "role_nouns": role_nouns,
        "domain_tokens": domain_tokens,
    }


def load_learned_patterns(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or DEFAULT_LEARNING_PATH
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    patterns = payload.get("patterns") if isinstance(payload, dict) else None
    return [dict(pattern) for pattern in patterns or [] if isinstance(pattern, dict)]


def record_learned_role(job: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or DEFAULT_LEARNING_PATH
    title = " ".join(str(job.get("title") or "").split())
    job_url = str(job.get("job_url") or job.get("url") or "").strip()
    if not title or not job_url:
        raise ValueError("A moved job must include title and job URL")
    signature = title_signature(title)
    if not signature["domain_tokens"]:
        raise ValueError("The moved title has no specific role-family terms to learn")
    now = datetime.now(timezone.utc).isoformat()
    pattern = {
        **signature,
        "example_title": title,
        "example_job_url": job_url,
        "company": " ".join(str(job.get("company") or "").split()),
        "learned_at": now,
    }
    patterns = load_learned_patterns(path)
    for index, existing in enumerate(patterns):
        if existing.get("normalized_title") == pattern["normalized_title"]:
            pattern["learned_at"] = existing.get("learned_at") or now
            pattern["last_confirmed_at"] = now
            patterns[index] = pattern
            break
    else:
        patterns.append(pattern)
    payload = {"version": 1, "patterns": patterns}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return pattern


def matches_learned_role(title: str, patterns: list[dict[str, Any]] | None = None) -> bool:
    candidate = title_signature(title)
    normalized_title = str(candidate["normalized_title"])
    candidate_roles = set(candidate["role_nouns"])
    candidate_domains = set(candidate["domain_tokens"])
    for pattern in patterns if patterns is not None else load_learned_patterns():
        if normalized_title and normalized_title == pattern.get("normalized_title"):
            return True
        learned_roles = set(pattern.get("role_nouns") or [])
        learned_domains = set(pattern.get("domain_tokens") or [])
        if not candidate_roles or not learned_roles or not (candidate_roles & learned_roles):
            continue
        if not learned_domains:
            continue
        required_overlap = 1 if len(learned_domains) <= 2 else max(2, math.ceil(len(learned_domains) / 2))
        if len(candidate_domains & learned_domains) >= required_overlap:
            return True
    return False
