#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/reports/html"
printf 'Serving review files only at http://127.0.0.1:%s/ — no action APIs\n' "${1:-8765}"
exec python3 -m http.server "${1:-8765}" --bind 127.0.0.1 --directory "$ROOT/reports/html"
