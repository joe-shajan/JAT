#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERMES_HOME:-$HOME/.hermes}/skills/career/jobapplier-generic"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --force ) ]]; then
  printf 'Usage: %s [--force]\n' "$0" >&2; exit 2
fi
if [[ -e "$DEST" || -L "$DEST" ]]; then
  if [[ "${1:-}" != --force ]]; then
    printf 'Skill already exists; use --force to replace only jobapplier-generic: %s\n' "$DEST" >&2; exit 1
  fi
  if [[ -L "$DEST" ]]; then printf 'Refusing symlink destination\n' >&2; exit 1; fi
  BACKUP="${DEST}.backup.$(date +%Y%m%d%H%M%S).$$"
  mv "$DEST" "$BACKUP"
  printf 'Previous generic skill backed up: %s\n' "$BACKUP"
fi
mkdir -p "$(dirname "$DEST")"
cp -R "$ROOT/skills/career/jobapplier-generic" "$DEST"
printf 'Installed: %s\nStart a fresh Hermes session or /reload-skills, then use hermes -s jobapplier-generic\n' "$DEST"
