#!/usr/bin/env bash
# Shared helpers for JobApplier scripts that invoke Hermes Agent.
# Source this file from Bash scripts; do not execute it directly.

jobapplier_script_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

jobapplier_workspace() {
  cd "$(jobapplier_script_dir)/.." && pwd
}

jobapplier_has_hermes() {
  command -v hermes >/dev/null 2>&1
}

jobapplier_require_hermes() {
  if ! jobapplier_has_hermes; then
    echo "Hermes Agent CLI is not on PATH. Install/configure Hermes first." >&2
    echo "See: https://hermes-agent.nousresearch.com/docs" >&2
    return 1
  fi
}

jobapplier_build_hermes_args() {
  local skills="${1:-jobapplier-generic}"
  JOBAPPLIER_HERMES_ARGS=()

  local skill
  local -a skill_array=()
  IFS=',' read -r -a skill_array <<< "$skills"
  for skill in "${skill_array[@]}"; do
    skill="${skill//[[:space:]]/}"
    if [[ -n "$skill" ]]; then
      JOBAPPLIER_HERMES_ARGS+=("-s" "$skill")
    fi
  done
}

jobapplier_run_hermes_chat() {
  local toolsets="$1"
  local prompt="$2"
  local skills="${3:-jobapplier-generic}"

  jobapplier_require_hermes
  jobapplier_build_hermes_args "$skills"
  hermes "${JOBAPPLIER_HERMES_ARGS[@]}" chat -Q --toolsets "$toolsets" -q "$prompt"
}
