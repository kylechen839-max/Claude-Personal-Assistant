#!/usr/bin/env bash
# Session watch with a Codex summary (macOS / Linux).
#
#   ./run-codex-watch.sh [MINUTES]     (default 5)
#
# Every MINUTES it runs session_watch.py once (detection, desktop
# notification, watch.log). When a block is new and the Codex CLI is on
# PATH, Codex (read-only sandbox) follows AGENTS.md to summarise the blocked
# sessions into codex-summary.log. Without Codex it just skips the summary.
# Nothing here answers or approves a Claude Code prompt.
set -u
umask 077  # codex-summary.log and the state folder are private

minutes="${1:-5}"
case "$minutes" in
  ''|*[!0-9]*|0) echo "usage: $0 [MINUTES]  (a whole number, at least 1)" >&2; exit 2 ;;
esac

here="$(cd "$(dirname "$0")" && pwd)"
py="$(command -v python3 || command -v python || true)"
if [ -z "$py" ]; then
  echo "run-codex-watch: Python 3.10+ is not on PATH" >&2
  exit 1
fi
state="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/session-watch"
summary="$state/codex-summary.log"
mkdir -p "$state"
chmod 700 "$state" 2>/dev/null || true

while :; do
  out="$("$py" "$here/session_watch.py" --once --json)" || out="[]"
  case "$out" in
    *'"new": true'*)
      if command -v codex >/dev/null 2>&1; then
        if [ -f "$summary" ] && [ "$(wc -c < "$summary")" -gt 1048576 ]; then
          mv -f "$summary" "$summary.1"
        fi
        {
          printf '\n== %s ==\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
          codex exec --sandbox read-only --cd "$here" \
            "Follow AGENTS.md and summarise the blocked Claude Code sessions." 2>&1
        } >> "$summary"
      fi
      ;;
  esac
  sleep $((minutes * 60))
done
