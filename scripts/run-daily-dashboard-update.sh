#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# No sourcing arbitrary .env shell code; optional keys can be exported normally.
# Validate attachment without connecting before invoking any live acquisition.
if [[ " $* " != *" --sample "* && " $* " != *" --help "* ]]; then
  python3 -m job_research.browser_attach check-config
fi
exec python3 -m job_research.cli --dashboard-dir "$ROOT/reports/html/latest" "$@"
