#!/usr/bin/env bash
# Smoke-check a deployed web app (the Render service, or the Pi):
#
#   WEB_TOKEN=... ./scripts/cloud_smoke.sh https://hey-claude.onrender.com
#
# Checks /healthz, that a protected route refuses a request without a
# session, logs in with $WEB_TOKEN (or WEB_TOKEN from .env; never printed),
# then runs one text turn and one audio turn. Exit code 0 = all PASS.
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 BASE_URL [--token-env NAME] [--audio FILE.wav]" >&2
    exit 2
fi
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
PY="$REPO_DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"
exec "$PY" -m assistant.smoke "$@"
