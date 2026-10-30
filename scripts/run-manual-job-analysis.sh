#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hermes-jobapplier-lib.sh
source "$SCRIPT_DIR/hermes-jobapplier-lib.sh"

WORKSPACE="$(jobapplier_workspace)"
LINK_FILE="${1:-}"

if [[ -z "$LINK_FILE" || ! -f "$LINK_FILE" ]]; then
  echo "Usage: $0 /path/to/job-links.txt" >&2
  exit 2
fi

TOOLSETS="${HERMES_JOBAPPLIER_TOOLSETS:-terminal,file,web,browser,todo}"
SKILLS="${HERMES_JOBAPPLIER_SKILLS:-jobapplier-generic}"

DATE_STAMP="$(date +%F-%H%M%S)"
OUTPUT_FILE="$WORKSPACE/reports/manual-job-analysis-$DATE_STAMP.md"
mkdir -p "$WORKSPACE/reports"

PROMPT="$(cat "$WORKSPACE/prompts/manual_job_links.md")

Job URLs to analyze:
$(cat "$LINK_FILE")

Write the final Markdown report to:
$OUTPUT_FILE
"

cd "$WORKSPACE"
jobapplier_run_hermes_chat "$TOOLSETS" "$PROMPT" "$SKILLS" | tee "$OUTPUT_FILE"

echo "Report written to $OUTPUT_FILE"
