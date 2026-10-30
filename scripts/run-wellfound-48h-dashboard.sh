#!/usr/bin/env bash
# Portable research-only entry point; browser attachment is handled by the CLI.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'python3 is required' >&2
  exit 1
fi
exec python3 -m job_research.cli "$@"
