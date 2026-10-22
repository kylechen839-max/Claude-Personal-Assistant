#!/usr/bin/env bash
# Preflight on Linux / the Raspberry Pi: every check in one pass, with a
# PASS/FAIL/SKIP table and a report in logs/. Secret values are never shown.
#
#   ./scripts/preflight.sh              # everything except the hardware steps
#   ./scripts/preflight.sh --hardware   # on the Pi: also audio devices + mic level
#   ./scripts/preflight.sh --help       # all options (--skip, --branch, ...)
#
# .env is loaded by the preflight itself, into its own process only.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PY="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "No .venv found in $REPO_DIR; run ./scripts/install_pi.sh first." >&2
    exit 2
fi
exec "$PY" -m kwil.preflight "$@"
