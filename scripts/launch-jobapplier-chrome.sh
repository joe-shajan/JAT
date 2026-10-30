#!/usr/bin/env bash
set -euo pipefail
# Retained only to fail safely for old shortcuts/docs. JobApplier never launches Chrome.
echo "STOP: Browser launching is disabled. Ask the user to enable remote debugging in their already-open Chrome at chrome://inspect/#remote-debugging and approve the connection manually. See docs/browser.md." >&2
exit 1
