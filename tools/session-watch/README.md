# Session watch: which Claude Code sessions are waiting on you

A small local watcher for Windows 11 and macOS (Linux works too). It
notices when one of your Claude Code sessions on this computer is blocked
on you, most often an approval prompt, and tells you with a desktop
notification. Optionally the OpenAI Codex CLI writes a short summary of
what is blocked and which session to open first.

Stdlib-only Python 3.10+. Nothing to `pip install`.

**It only reports.** It never approves, answers, denies or types into a
Claude Code prompt, never changes Claude Code's permission settings or
`settings.json`, and never writes inside your Claude folder except its own
`session-watch/` folder there. Codex runs in a read-only sandbox with the
same rules (`AGENTS.md`).

## What it looks at

Two sources, merged per session (the hook wins when both see one):

1. **Notification hook (best).** Claude Code runs `notify_hook.py` as a
   hook. On a `Notification` of type `permission_prompt` (an approval
   prompt), `idle_prompt` ("Claude is waiting for your input", sent after a
   finished turn has sat idle for a minute) or `elicitation_dialog` (an MCP
   server asks you something) it writes
   `session-watch/blocked/<session id>.json`; other types such as
   `auth_success` are ignored. When you answer (`PostToolUse`,
   `PostToolUseFailure`, `UserPromptSubmit`) or the session moves on or
   ends (`Stop`, `SessionEnd`) the marker is deleted. The hook
   prints nothing, always exits 0 and ignores bad input, so it can't
   disturb Claude Code. Markers older than 24 hours are ignored and
   cleaned up.
2. **Transcript check (no setup).** For each transcript under
   `projects/*/*.jsonl` changed in the last 24 hours it reads only the last
   64 KB. If the newest tool call has no result yet and the session has
   been quiet for `--threshold` seconds (default 120), the session is
   reported as "waiting (probably an approval prompt)". A session's
   subagents (`projects/*/<session id>/subagents/*.jsonl`) count too: a
   subagent that is still writing keeps the session "busy", and an
   unanswered tool call in a subagent that wrote since the main transcript
   last moved is reported ("... in a subagent"). Each transcript is judged
   on its own last write: a busy background agent never hides a prompt in
   the main session; only an open `Agent`/`Task` call in the main
   transcript counts its subagents' writes as activity.
3. **Security-check stops (transcript check only).** When a tool result
   says a security check refused the call (the auto mode classifier, a
   permission deny rule, or a PreToolUse hook that blocked it) and the
   session has done nothing successful since (no later tool result that
   worked, no new message from you) and has been quiet for `--threshold`
   seconds, it is reported with `notification_type` `security_denied` and
   the denied tool's name: the session gave up or ended its turn and needs
   you to decide. Your own "no" to a prompt doesn't count. There is no hook
   event for this, so only the transcript check sees it. The denial text
   (which can hold the command) is never copied anywhere; each denied call
   is announced once.

Each new block gets one notification and one line in
`session-watch/watch.log` (session id, project, wait, source; never any
transcript text). `session-watch/notified.json` remembers what was already
announced (entries expire after 7 days), so a block that stays open isn't
announced again every scan.

## Setup

### Windows 11
1. Python 3.10+ from python.org (tick "Add python.exe to PATH").
2. Try it: `python tools\session-watch\session_watch.py`
3. Optional, Codex summaries: install Node.js, then
   `npm i -g @openai/codex` and run `codex` once to sign in.
4. Optional, the hook (recommended): see "Add the hook" below. Use
   `python` instead of `python3` in the commands, with the full path in
   quotes (it may contain spaces). In `settings.json` the quotes are
   escaped:
   `"command": "python \"C:/Users/you/Claude-Personal-Assistant/tools/session-watch/notify_hook.py\""`.

Run it in a window: `powershell -ExecutionPolicy Bypass -File tools\session-watch\run-codex-watch.ps1 -Minutes 5`

Start it at sign-in (Task Scheduler), from a Command Prompt (`cmd.exe`;
Windows PowerShell 5.1 mangles the inner `\"` quotes):

```
schtasks /Create /SC ONLOGON /IT /TN "Claude session watch" /TR "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"C:\Users\you\Claude-Personal-Assistant\tools\session-watch\run-codex-watch.ps1\""
```

Remove it with `schtasks /Delete /TN "Claude session watch"`.

The Windows notification is a toast shown through Windows PowerShell 5.1
(`powershell.exe`, built into Windows).

### macOS
1. Python 3.10+ (`brew install python` or python.org); `python3 --version`.
2. Try it: `python3 tools/session-watch/session_watch.py`
3. Optional, Codex summaries: `npm i -g @openai/codex` (or
   `brew install codex`), then run `codex` once to sign in.
4. Optional, the hook: see below.

Run it in a terminal: `tools/session-watch/run-codex-watch.sh 5`

Start it at login with launchd: save this as
`~/Library/LaunchAgents/com.kyle.session-watch.plist` (fix the paths), then
`launchctl load ~/Library/LaunchAgents/com.kyle.session-watch.plist`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kyle.session-watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/you/Claude-Personal-Assistant/tools/session-watch/run-codex-watch.sh</string>
    <string>5</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

Notifications come from `osascript`; the first one may ask you to allow
notifications for Script Editor in System Settings > Notifications.

### Add the hook
`hooks-snippet.json` holds the `hooks` block. Paste it into your own
`~/.claude/settings.json` yourself (nothing here edits that file):
replace `/ABSOLUTE/PATH/TO` with where this repo lives (keep the `\"`
quotes around the path so a folder name with a space works), and if you already
have a `hooks` block, add these entries to the matching event lists instead
of replacing them. Restart Claude Code sessions (or check with `/hooks`).

## The command

```
python session_watch.py                 # one scan, a line per blocked session
python session_watch.py --json          # the same as a JSON list
python session_watch.py --interval 60   # keep scanning every 60 s
python session_watch.py --threshold 300 # a tool call must be quiet 5 min first
python session_watch.py --no-notify     # no notification, writes nothing
python session_watch.py --claude-dir D:\claude   # default: $CLAUDE_CONFIG_DIR or ~/.claude
```

`--json` prints one line: a list sorted longest wait first, each item with
`session_id`, `project`, `source` (`hook` or `transcript`), `since`,
`waiting_seconds`, `reason`, `tool`, `notification_type`
(`permission_prompt`, `idle_prompt` or `elicitation_dialog` for a hook
block, `security_denied` for a transcript block stopped by a security
check, else `null`), `message` and `new` (not announced before). The
desktop notification and the text output show the type too (`approval`,
`idle`, `question`, `security check`). A scan exits 0; a transcript or
marker it can't read or parse is skipped. If its own state folder
(`session-watch/`) can't be written, `--once` still prints the scan, logs
one warning line and exits 3; no notification goes out until the state can
be saved (it is saved before notifying), so a broken folder never repeats
a notification every scan. A bad option exits 2 (argparse), and with
`--interval` a scan that fails anyway is logged and retried next time.

The launchers (`run-codex-watch.ps1`, `run-codex-watch.sh`) run one
notifying scan every N minutes (default 5). When a block is new and `codex`
is on PATH they run

```
codex exec --sandbox read-only --cd <this folder> "Follow AGENTS.md and summarise the blocked Claude Code sessions."
```

and append its answer to `session-watch/codex-summary.log` (rotated at
1 MB). Without Codex they just notify.

## Limitations
- **Local sessions only:** Claude Code in a terminal, an IDE or the desktop
  app on this computer, which keep transcripts under `~/.claude/projects/`.
  Cloud sessions on claude.ai have no public API to watch; for the dev
  team's cloud runs use the hourly watchdog in `docs/WATCHDOG.md`.
- The transcript check can't tell an approval prompt from a tool call that
  is simply slow (a long build or test run): such a call is reported once it
  has run longer than `--threshold`. Raise the threshold, or add the hook,
  which knows. A session killed in the middle of a tool call also shows as
  waiting until its transcript is a day old.
- **Subagents:** current Claude Code keeps a subagent's transcript in
  `projects/<project>/<session id>/subagents/agent-*.jsonl`, and the main
  transcript stays quiet while a foreground subagent runs. While the main
  transcript waits on an `Agent`/`Task` call the check treats the newest
  subagent write as its activity; any other open call is judged on the
  main transcript alone. It looks
  for an unanswered or denied call in up to 8 subagent transcripts (the
  newest) written since the
  main one last moved. An older layout or a renamed folder would make a
  long subagent run look like a prompt once it passes `--threshold`.
- The hook reports Claude Code's "waiting for your input" (`idle_prompt`)
  notification as well as permission prompts: both mean the session is
  waiting on you, and `notification_type` tells them apart.

## Privacy
Everything runs on your computer. It reads transcript tails locally and
keeps only ids, project folder names, times, the hook's short notification
text (cut to 200 characters) and, in the marker files only, the session's
working folder and transcript path. Nothing is sent anywhere, except that
when Codex summaries are on, Codex (OpenAI) gets the `--json` scan output:
session ids, project folder names, times and waits, the reason, tool names,
the notification type and that notification text. It never gets
transcripts or the working-folder path: `AGENTS.md` tells it to read
nothing but that output (the marker files, which hold the path, are off
limits too). The state folder is created 0700
and its files 0600 on macOS/Linux; on Windows they get your user profile's
permissions.
