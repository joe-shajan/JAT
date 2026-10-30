#!/usr/bin/env bash
set -euo pipefail

# Optional JOBAPPLIER_OUTREACH_COMMAND adapter.
# Reads the outreach prompt from stdin and asks Hermes to return strict JSON.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hermes-jobapplier-lib.sh
source "$SCRIPT_DIR/hermes-jobapplier-lib.sh"

WORKSPACE="$(jobapplier_workspace)"
TOOLSETS="${HERMES_OUTREACH_TOOLSETS:-file,web}"
SKILLS="${HERMES_JOBAPPLIER_SKILLS:-jobapplier-generic}"

if ! jobapplier_has_hermes; then
  echo '{"email_subject":"","email_body":"","cover_letter":"","linkedin_dm":"","linkedin_connection":""}'
  exit 1
fi

PROMPT="$(cat)

Return only one valid JSON object with exactly these keys:
email_subject, email_body, cover_letter, linkedin_dm, linkedin_connection.
Do not include Markdown fences, commentary, or tool-output summaries."

cd "$WORKSPACE"
jobapplier_run_hermes_chat "$TOOLSETS" "$PROMPT" "$SKILLS"
