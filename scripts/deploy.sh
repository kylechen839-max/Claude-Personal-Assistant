#!/usr/bin/env bash
# Put new code live safely: pull, check it on a side port, then switch over
# or refuse (python -m kwil.deploy; see kwil/deploy.py).
#
#   ./scripts/deploy.sh                  pull, check, switch
#   ./scripts/deploy.sh --no-pull        check and switch what is checked out now
#   ./scripts/deploy.sh --requirements   an update that changes requirements.txt
#                                        (stop the apps first)
#
# Each app is started on the new code on a side port (its port + 100) and
# must pass a test conversation before it replaces the running one; if it
# fails, the checkout goes back and the old version keeps running.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
    echo "No .venv found; run ./scripts/install_pi.sh first." >&2
    exit 2
fi
cd "$REPO_DIR"
exec "$REPO_DIR/.venv/bin/python" -m kwil.deploy "$@"
