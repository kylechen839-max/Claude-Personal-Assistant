#!/usr/bin/env bash
# Check every Kwil web app on this machine every 5 minutes: a systemd
# user timer running `python -m kwil.watch` (see kwil/watch.py).
# Safe to re-run. The timer from before the Kwil rebrand (hey-claude-watch)
# is stopped and removed.
#
#   ./scripts/install_watch.sh              install, enable and run once now
#   ./scripts/install_watch.sh --uninstall
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(kwil-watch.service kwil-watch.timer)
OLD_UNITS=(hey-claude-watch.service hey-claude-watch.timer)  # before the Kwil rebrand

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run this as your normal user (e.g. pi), not with sudo." >&2
    exit 1
fi

remove_old_units() {
    # Two timers would double every check and alert.
    if [[ -f "$UNIT_DIR/hey-claude-watch.timer" ]]; then
        systemctl --user disable --now hey-claude-watch.timer 2>/dev/null || true
    fi
    for unit in "${OLD_UNITS[@]}"; do
        rm -f "${UNIT_DIR:?}/${unit:?}"
    done
}

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now kwil-watch.timer 2>/dev/null || true
    for unit in "${UNITS[@]}"; do
        rm -f "${UNIT_DIR:?}/${unit:?}"
    done
    remove_old_units
    systemctl --user daemon-reload
    echo "Removed the watchdog timer"
    exit 0
fi

if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
    echo "No .venv found; run ./scripts/install_pi.sh first." >&2
    exit 1
fi

mkdir -p "$UNIT_DIR"
remove_old_units
for unit in "${UNITS[@]}"; do
    # '|' as the sed delimiter because the path contains '/'.
    sed "s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/deploy/$unit" > "$UNIT_DIR/$unit"
    echo "Wrote $UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now kwil-watch.timer
systemctl --user start kwil-watch.service || true

cat <<EOM

The watchdog runs every 5 minutes (and 2 minutes after boot).
  Results:  $REPO_DIR/.venv/bin/python -m kwil.watch --status
  Log:      $REPO_DIR/logs/watch.log
  Remove:   ./scripts/install_watch.sh --uninstall
EOM
