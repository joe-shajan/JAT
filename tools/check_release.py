#!/usr/bin/env python3
"""Check distributable source; ignored local data is never a release input."""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'data', 'runs', 'fetched_pages', 'reports', 'logs', '__pycache__', '.venv', '.hermes'}
PATTERNS = [
    ('machine-specific home path', re.compile(r'/(?:home|Users)/[a-zA-Z0-9_.-]+/')),
    ('private key', re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')),
    ('GitHub token', re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}')),
    ('credential assignment', re.compile(r'(?im)^\s*(?:BRAVE_SEARCH_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*[\"\']?[A-Za-z0-9_-]{12,}')),
]

def source_paths() -> list[Path]:
    if (ROOT / '.git').exists():
        result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT, capture_output=True, check=True)
        return sorted({ROOT / p for p in result.stdout.decode().split('\0') if p})
    paths = []
    for path in ROOT.rglob('*'):
        rel = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS or part == '.git' for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            if rel.as_posix() == 'config/wellfound_search.yaml':
                continue
            if rel.parts[0] == 'profile' and rel.name not in {'applicant.example.yaml', 'README.md'}:
                continue
            if rel.name.startswith('.env') and rel.name != '.env.example':
                continue
            paths.append(path)
    return sorted(paths)

def main() -> int:
    paths = source_paths()
    failures = []
    for path in paths:
        rel = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in rel.parts) or path.is_symlink():
            failures.append(f'{rel}: private/runtime path or symlink')
            continue
        if path.suffix in {'.pdf', '.docx', '.db', '.sqlite3', '.pyc'} or (rel.parts[0] == 'profile' and rel.name not in {'applicant.example.yaml', 'README.md'}) or rel.as_posix() == 'config/wellfound_search.yaml' or (rel.name.startswith('.env') and rel.name != '.env.example'):
            failures.append(f'{rel}: private file')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            failures.append(f'{rel}: unexpected binary; review manually')
            continue
        for label, pattern in PATTERNS:
            if pattern.search(text):
                failures.append(f'{rel}: {label}')
    for failure in failures:
        print('FAIL:', failure)
    print(f'Checked {len(paths)} distributable files; {len(failures)} findings.')
    print('Pattern checks are not a guarantee: manually review staged files before publication.')
    return bool(failures)

if __name__ == '__main__':
    sys.exit(main())
