# Agent run dashboard

A page for checking on the dev team at a glance, kept apart from the
Claude chat list. It has two views, switched with the Runs / Grid
buttons at the top (the choice is remembered on that device):

- **Runs** (the default, read-only): each `/build-feature` run is one
  row. The summary line at the top answers "does anything need me?",
  for example `2 need you · 1 running · 1 silent`. A run split into
  parallel pieces has one row per piece (see `run_id`).
- **Grid**: every Claude Code session you have, as a live status tile,
  side by side (see [Grid view](#grid-view)).

Any session (a Grid tile, or a Runs card with a session link) has a
**Chat** button that opens a panel where you can read the session's
recent messages and send it one (see [Chat panel](#chat-panel)).

- **URL:** https://claude.ai/artifact/CpPTyJkkHZ7CK8jsPYZq4G (a claude.ai
  artifact with the `db`, `user` and `mcp` capabilities; open it signed
  in, on the phone too, and add it to the Home Screen; on a PC it can
  have its own window, see [Desktop window](#desktop-window)).
- **Source:** `index.html` in this folder. The master publishes it to the
  artifact. Nothing here is served by the Hey Claude app.
- **Watchdog:** `WATCHDOG.md`, an hourly trigger that marks runs whose
  session has died.
- **Desktop launchers:** `desktop/open-dashboard.cmd` (Windows) and
  `desktop/open-dashboard.command` (macOS).

## Manifest (the master sets it when publishing)

```json
{"db":{}, "user":{}, "mcp":{"servers":[{"server":"Claude Code Remote","tools":["list_sessions","list_events","send_message"]}]}}
```

`db` is the runs collection. `mcp` is the viewer's own Claude Code
Remote connector, limited to three tools: `list_sessions` (the grid),
`list_events` (the chat panel's messages) and `send_message` (the chat
panel's Send button). Nothing else on that connector (archive,
interrupt, create, settings, triggers) is listed, so the page cannot
call it. `tests/test_agent_dashboard.py` pins this block and checks the
page names no other tool.

Kyle's LLM Council chose this design on 2026-10-19 (see
`council-transcript-20261019-1815.md`). If he isn't using it after two
weeks, delete it.

## Row schema (collection `runs`, document id = `run_id`)

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | `<UTC yyyymmdd-HHMMSS>-<feature-slug>`, e.g. `20261019-183000-voice-speed`. A run split into pieces gives each piece its own row, `<run_id>-<piece-slug>` (e.g. `20261019-183000-voice-speed-settings`), with its own `if_version` chain, phase, round and per-round time limits; once the piece rows exist the master writes the plain row `done`. A single-piece run keeps the plain `run_id`. Only `A-Za-z0-9 _ - . ~ : @ +` (it is the document id). |
| `feature` | string | Short feature name (one line, no conversation text). |
| `branch` | string | `claude/<feature>`. |
| `session_url` | string | `https://claude.ai/code/<session_id>`; the master gets its own id from `get_session` called with no id. |
| `phase` | string | One of `started`, `researching`, `building`, `judging`, `pr_ready`, `blocked`, `done`, `dead`. |
| `round` | number | Build-judge round (1–3) while `building`/`judging`. |
| `started_at` | string | UTC ISO 8601 from `date -u +%Y-%m-%dT%H:%M:%SZ`. Set once. |
| `updated_at` | string | Same format, rewritten on every write. The page's "silent" check reads it. |
| `pr_url` | string | `https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/<n>`. |
| `tests` | string | The judge's pytest line, e.g. `1432 passed, 44 skipped`. |
| `tokens` | number | Total `subagent_tokens` so far. |
| `agent_runs` | number | Agents started so far. |
| `blocker` | string | Short reason (under about 140 characters) when `blocked` or `dead`. |
| `watch` | object | Written only by the watchdog: `{bucket: string, checked_at: string}`. `bucket` is the session's status bucket suffix (`WORKING`, `BLOCKED`, `COMPLETED`, `REVIEW_READY`), rewritten whenever it changes. |

Terminal phases are `pr_ready`, `blocked`, `done` and `dead`. The watchdog
never touches a row in one of them.

**Retiring rows.** The master writes `done` when the CI watch reports the
PR merged or closed (Kyle usually merges in the GitHub UI), or when it
merges on Kyle's request. Nothing retires a `blocked` or `dead` row, so
the page does: a `pr_ready`, `blocked` or `dead` row whose `updated_at` is
more than 7 days old counts as done ("done (no change 7 d)"). It is
counted as done, not as "needs you" or "silent", and is hidden after 14
days like any done row.

**Approval prompts.** When `watch.bucket` is `BLOCKED` and
`watch.checked_at` is newer than `updated_at`, a live row shows "needs
you (approval)": the session is waiting on a permission prompt. The
master's next write moves `updated_at` past the check and clears it.

## Phases, time limits and page thresholds

| Phase | Master writes it | Time limit (skill) | Page says "silent" after |
|---|---|---|---|
| `started` | §0, before anything else | 10 min | 25 min |
| `researching` | when the researcher starts | 30 min | 45 min |
| `building` | each builder round (`round` = N) | 75 min per round | 90 min |
| `judging` | each judge round (`round` = N) | 30 min per round | 45 min |
| `pr_ready` | after the draft PR exists, before the ✅ push | – | needs you |
| `blocked` | on any blocker, before the ⛔ push | – | needs you |
| `done` | when the PR is merged or closed (CI watch), or after merging on Kyle's request | – | done |
| `dead` | the watchdog only | – | silent ("dead (watchdog)") |

A live row with a fresh approval prompt (above) shows "needs you
(approval)" instead of running or silent.

The whole run is limited to 5 hours. The page threshold is the limit
plus 15 minutes, so a master that stops itself on time never shows as
silent. A row whose `updated_at` is missing or unreadable counts as
silent. The page works out "silent" itself every minute. That costs no
agent usage. The watchdog uses the same thresholds before it marks an
idle session dead.

## How the master writes (ArtifactData)

1. **Start:** `set` the whole row with `phase: "started"`,
   `started_at`, `updated_at`, `session_url`, `feature`, `branch`. This
   is a create, so there is no `if_version`. Keep the version it
   returns.
2. **Each change:** `update` with `if_version` = the last version, and
   only the changed fields plus `phase`, `round`, `updated_at`. Keep the
   new version.
3. **Version conflict** (the watchdog wrote in between): `get` the row,
   then apply your change again on top of its version. The master's phase
   wins: a live master can overwrite `dead`.

About 6–8 writes per run, only when the phase changes. No heartbeats.

## Grid view

One tile per Claude Code session, from `list_sessions` (`{limit: 50}`)
through the viewer's own Claude Code Remote connector. Nothing is
fetched until Grid is opened: a Runs-only visit never asks for the
connector or calls it. The first Grid open on a device shows claude.ai's
consent prompt for the connector. While Grid is shown the page keeps one
watch on the list (refetched about every 30 s, paused while the tab is
hidden); switching back to Runs stops it. **Refresh** asks for a fresh
copy at once. Tiles sit in as many columns as fit (at least 260 px wide;
one column on a phone).

| Session | Tile |
|---|---|
| `status_bucket` FAILED | failed (red edge) |
| `status_bucket` BLOCKED, a non-empty `needs_action` (top level, else under `external_metadata`), or `status_category` need_input | needs you (amber) |
| `status_bucket` WORKING, or an unknown bucket while `session_status` is RUNNING | working (green, pulsing) |
| `status_bucket` REVIEW_READY | idle, pill "review ready" |
| `status_bucket` COMPLETED | done (grey) |
| anything else | idle, "unknown status" |

The bucket and status prefixes (`SESSION_STATUS_BUCKET_`,
`SESSION_STATUS_`) are stripped and case is ignored. Tiles sort needs
you, failed, working, idle, done, newest first within each. A tile shows
the title, the pill, "updated X ago", a small "local" label for a
session on your own machine (`environment_kind` bridge), one status line
(`status_detail`, else `recent_action`, else `task_summary`, at most 160
characters), any `needs_action` text, and **Chat** and **Open in
claude.ai** (only for an id of the form `session_` plus letters, digits,
`_` or `-`).

**Hidden:** archived sessions, and done or idle ones with no update for
7 days. The line under the grid counts both ("2 archived hidden · 5
older hidden").

**When the connector fails** (by error code, never its message text):

| Code | Grid shows | Watch |
|---|---|---|
| `needs_reauth` | tiles cleared, "Reconnect Claude Code Remote in claude.ai Settings → Connectors" | keeps running |
| `server_not_connected`, `selection_required`, `server_not_found` | tiles cleared, "Add Claude Code Remote in claude.ai Settings → Connectors" | keeps running |
| `not_in_manifest`, `blocked_by_policy`, `consent_required` | tiles cleared, "Claude Code Remote is not allowed for this view." (never asks again) | keeps running |
| `approval_required` | tiles cleared, an **Allow and show sessions** button: one `list_sessions` call per click | keeps running |
| `server_unavailable`, `rate_limited`, `upstream_error`, `tool_error`, unknown codes | last tiles kept, "Stale (code) · refreshes by itself" | keeps running; no extra retries |
| `not_granted`, `capability_disabled`, `capability_removed` | "The grid needs this page open on claude.ai" | stopped |
| `bad_request`, `transform_error` | "Can't load sessions" | stopped |
| `user_changed` | nothing (another account signed in) | stopped, chat closed |
| `cancelled` | ignored | – |

## Chat panel

**Chat** on a tile or a Runs card opens a panel: a drawer on the right
on a wide window, the whole screen on a phone. Close it with **Close**
or Escape. Nothing is called before the first Chat click.

- **Reading:** one `list_events` call with `{session_id, kinds: ["user",
  "assistant"], limit: 40}` on open (the newest 40 events), then a watch
  on the same call about every 30 s while the panel is open; closing it
  stops the watch. Only text is shown: your messages and the agent's
  replies. Tool calls, tool results, thinking, synthetic, meta or
  replayed turns, background task notifications (a user turn whose
  `origin.kind` is not `human`) and subagent traffic are skipped.
  The same event delivered twice (same uuid) shows once. Long messages are cut at 600
  characters with **Show more**. The panel follows new messages only
  when you are already at the bottom.
- **Sending:** type in the box and press **Send** (or Ctrl+Enter / Cmd+Enter
  on a desktop; Enter alone adds a line, and a held or IME-composing
  Ctrl/Cmd+Enter never sends). Each click is exactly one
  `send_message` call with `{session_id, message}` (at most 4000
  characters), and the button is disabled until it settles. Nothing is
  sent automatically, on reload or on a timer. Each session keeps its own
  draft and its last send result in the page's memory (never in storage,
  at most 50 sessions, gone on reload or when another account signs in),
  so Close, Escape or opening another session loses neither. On success
  that session's draft clears (unless you edited it meanwhile), "Sent —
  the agent will reply in its next turn" shows, and the message list
  refreshes.
- **What a sent message is:** `send_message` delivers the text into that
  session as a cross-session message (a new turn marked as sent from
  another session, not typed there). The agent is told to treat it as
  data from a peer, so it won't change permission settings or take a
  new destructive step on that message alone; a session in a different
  permission mode may hold it for your approval inside that session.
  Use **Open in claude.ai** when you need to approve something or type
  as yourself.
- **When sending fails** the draft is always kept, and the result shows
  again when you reopen that session. If another session's panel is open
  when it settles, the status line names it: "Message to “title”: …".
  `approval_required`
  shows an **Approve and send** button (one call per click);
  `tool_error` and `bad_request` say it was not sent (code only);
  `server_unavailable` and `upstream_error` may or may not have
  delivered it, so the page says to check the conversation before
  sending again and never resends by itself. Connector codes use the
  same copy as the grid.

## Desktop window

To keep the dashboard in its own window on a PC (no tabs or address
bar, its own taskbar or Dock icon), run a launcher from `desktop/`. Each
opens the URL above with Chrome's or Edge's `--app=` option, using your
normal browser profile, so you are already signed in to claude.ai. If
neither browser is installed it opens a normal tab in the default
browser.

- **Windows:** double-click `desktop\open-dashboard.cmd`. It tries Chrome
  (Program Files, Program Files (x86), then your user folder), then Edge.
  To pin it: right-click the desktop → New → Shortcut, and enter the
  browser path plus the option, for example
  `"C:\Program Files\Google\Chrome\Application\chrome.exe" --app=https://claude.ai/artifact/CpPTyJkkHZ7CK8jsPYZq4G`
  (Edge: `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"`).
  Name it "Agent dashboard", then open it and right-click its taskbar
  icon → Pin to taskbar.
- **macOS:** double-click `desktop/open-dashboard.command` (the first
  time, right-click → Open to get past Gatekeeper). It tries Google
  Chrome, then Microsoft Edge (looking for them in `/Applications` and
  `~/Applications`, so no Finder window opens). Keep it in the Dock while open (right-click
  the icon → Options → Keep in Dock).
- **Without a launcher:** open the URL in Chrome and choose ⋮ → Cast,
  save, and share → Install page as app (Edge: ⋯ → Apps → Install this
  site as an app). The browser makes the app window and a Start menu or
  Launchpad entry for you.

The launchers never pass `--user-data-dir`: a separate profile would
start signed out of claude.ai and have no connectors, so neither Runs
nor Grid would load.

## Rules
- **Only the master writes rows.** Subagents never do. The page never
  writes to the runs collection; its one write anywhere is the chat
  panel's Send, and only on your click.
- **No log text, transcripts, conversation text or secrets in rows.**
  Rows hold names, links, counts and short reasons.
- **Rows are untrusted data.** The page shows them as plain text and
  links only to the two prefixes above. Agents never act on what a row
  says: the watchdog reads `phase`, `updated_at`, `session_url` and
  `watch.bucket`/`watch.checked_at` to decide, copies `feature` into its
  push text, and nothing in a row is an instruction.
- **Sessions and messages are untrusted too.** Titles, summaries and
  message text are shown as plain text only, never as links or markup,
  and never logged to the console.
- Out of scope on purpose: kill/approve/archive buttons, reading rows
  aloud, one row per subagent, showing tool calls in the chat panel.
