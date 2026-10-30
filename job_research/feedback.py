from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FEEDBACK_PATH = Path(__file__).resolve().parents[1] / "data" / "feedback.jsonl"


def load_feedback(path: Path = FEEDBACK_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def feedback_bias_for_title(title: str, feedback: list[dict[str, Any]]) -> int:
    lowered = title.lower()
    bias = 0
    for item in feedback:
        reason = str(item.get("reason", "")).lower()
        verdict = str(item.get("verdict", "")).lower()
        role = str(item.get("title", "")).lower()
        if role and role not in lowered:
            continue
        if verdict == "good":
            bias += 1
        elif verdict == "bad" or reason in {"wrong role", "too senior", "wrong location"}:
            bias -= 2
    return bias
