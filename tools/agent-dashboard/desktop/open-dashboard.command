#!/usr/bin/env bash
# Opens the agent dashboard in its own Chrome or Edge window (no tabs or
# address bar), signed in with your normal browser profile. Falls back to
# the default browser. See tools/agent-dashboard/README.md.
set -euo pipefail

URL="https://claude.ai/artifact/BWYCUWxJxBvG6MJvnsaqvn"

# Checked silently: asking `open` to find the app (-Ra) shows it in Finder
# on every launch.
for app in "Google Chrome" "Microsoft Edge"; do
  if [ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]; then
    exec open -na "$app" --args --app="$URL"
  fi
done

# No Chrome or Edge found: open it as a normal tab in the default browser.
exec open "$URL"
