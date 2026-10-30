#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hermes-jobapplier-lib.sh
source "$SCRIPT_DIR/hermes-jobapplier-lib.sh"

WORKSPACE="$(jobapplier_workspace)"
OUTPUT_FILE="$WORKSPACE/reports/wellfound-report-$(date +%F).md"
TOOLSETS="${HERMES_JOBAPPLIER_TOOLSETS:-terminal,file,web,browser,todo}"
SKILLS="${HERMES_JOBAPPLIER_SKILLS:-jobapplier-generic}"

mkdir -p "$WORKSPACE/reports"
cd "$WORKSPACE"

PROMPT="$(cat "$WORKSPACE/prompts/daily_wellfound_report.md")

Use Hermes Agent style:
- Use file/search/read/patch tools for repository files.
- Use terminal for scripts and verification.
- Use web/browser tools only when needed for current facts or visible browser interaction.
- Preserve fetched artifacts before analysis.

Write the final Markdown report to:
$OUTPUT_FILE
"

jobapplier_run_hermes_chat "$TOOLSETS" "$PROMPT" "$SKILLS" | tee "$OUTPUT_FILE"

echo "Report written to $OUTPUT_FILE"
