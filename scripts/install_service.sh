#!/usr/bin/env bash
# Install and start Hey Claude as a systemd user service that runs at boot.
# Safe to re-run (it rewrites the unit and restarts the service).
#
#   ./scripts/install_service.sh            install, enable and start
#   ./scripts/install_service.sh --uninstall
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="hey-claude.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Run this as your normal user (e.g. pi), not with sudo." >&2
    exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload
    echo "Removed $UNIT_FILE"
    exit 0
fi

if [[ ! -x "$REPO_DIR/.venv/bin/python" ]]; then
    echo "No .venv found; run ./scripts/install_pi.sh first." >&2
    exit 1
fi
if [[ ! -f "$REPO_DIR/.env" ]]; then
    echo "Warning: $REPO_DIR/.env is missing; the service will fail until you add your keys." >&2
fi

mkdir -p "$UNIT_DIR"
# '|' as the sed delimiter because the path contains '/'.
sed "s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/deploy/$UNIT_NAME" > "$UNIT_FILE"
echo "Wrote $UNIT_FILE"

# Lingering starts the user's systemd (and this service) at boot, before login.
if [[ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || true)" != "yes" ]]; then
    echo "Enabling start at boot (loginctl enable-linger $USER; may ask for your password)"
    sudo loginctl enable-linger "$USER"
fi

systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME"
systemctl --user restart "$UNIT_NAME"

cat <<EOM

Hey Claude is running and will start at boot.
  Status:   systemctl --user status hey-claude
  Live log: journalctl --user -u hey-claude -f   (details: $REPO_DIR/logs/assistant.log)
  Stop:     systemctl --user stop hey-claude
  Remove:   ./scripts/install_service.sh --uninstall
EOM
