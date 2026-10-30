from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = WORKSPACE / 'config' / 'wellfound_search.yaml'
EXAMPLE_CONFIG_PATH = WORKSPACE / 'config' / 'search.example.yaml'


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH)
    data = yaml.safe_load(target.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Config must be a YAML mapping: {target}')
    search = data.setdefault('search', {})
    for key, default in [('posted_within_hours', 48), ('max_jobs_per_run', 50), ('max_dashboard_jobs', 50)]:
        value = search.setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'search.{key} must be a positive integer')
    for section, keys in [('roles', ['primary', 'prefer_keywords', 'avoid_keywords']), ('locations', ['include', 'include_if_visible'])]:
        obj = data.setdefault(section, {})
        for key in keys:
            values = obj.setdefault(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f'{section}.{key} must be a list of nonempty strings')
    return data
