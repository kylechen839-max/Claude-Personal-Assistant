# Changelog

## Unreleased

- Reply voice in the phone app's settings: each person (you, each family
  member, separate instances like Mom's) picks their voice from what that
  server has in their language (Piper, Kokoro, the system voice, and your
  ElevenLabs voices tagged paid if `ELEVENLABS_API_KEY` is set; family
  only with `[tts] family_paid_voices`). Preview plays a sample. New
  `[tts]` settings `picker`, `voices_dir`, `voice_file`, `family_paid_voices`.
- Family invites on the cloud server (`[family]`, on in the server
  profile): the owner invites someone from the phone menu with a one-time
  link (`#invite=`, 30 days). They get their own account, history and reply
  language (English or Chinese), with web search only and none of the
  owner's Notion, memory, Google, history, lecture/tutor, dev team or PC hub.
  Removing someone signs them out and deletes their data (retried later if a
  file is locked). Invites and removals are managed at the cloud server's own
  address. "Sign out everywhere" is now per person. A session the server
  doesn't list is never treated as the owner.
- Agent dashboard Grid: a Coordinator picker shows only the chosen
  coordinator session's windows (default All); the choice is kept per
  viewer, and Forget clears a pick whose session has no recent runs.
- PC hub: chat with your dev team coordinator(s) at
  http://127.0.0.1:8765/devteam on the PC (`[devteam] hub = true`).
  Messages go through Claude Code on your plan; markdown replies, step
  updates and an In task / Idle pill come back through the Notion inbox.
  Desktop- and loopback-only (off while TRUSTED_PROXY is set, with a
  startup warning); launchers in `tools/agent-dashboard/desktop/open-hub.*`.
- Agent dashboard: desktop-only (the phone layout is gone). Windows can be
  hidden and restored from "Hidden (N)" or Show all, the Runs view and
  window chrome got a calmer unified look, and "Get desktop app" saves the
  Windows or Mac launcher (as a small zip) for the dashboard or the hub.
- Dev team link: a timed-out Claude Code CLI on Windows is stopped with its
  whole process tree.
- Dev team dashboard: the coordinator chat always shows whether the
  coordinator is in a task or idle, and a message sent mid-task shows as
  queued until the coordinator reads it.
- Chinese (Mandarin) mode (`[assistant] language = "zh"`) and a `config.mom.toml`
  template for a separate family-member instance (new `--env-file` flag):
  Chinese replies, Mandarin speech recognition (Whisper `small`), a Mandarin
  Piper voice, a Chinese phone page and Chinese yes/no. A Chinese yes must be
  the whole answer from a short list (是的, 好的, 没问题, 删吧...); sentences
  that merely contain 是 or 好, and questions back like 确定？, are a no.
- "Connect Google account" in the phone app (`[webapp] google_connect`,
  `public_origin`): signs in to Calendar and Gmail (drafts only) from the
  phone. A separate instance refuses to start if the owner's Google, history,
  web-token, Notion or Pinecone variables are inherited from the shell.
- Phone app cache v5.
- Dev team dashboard: the coordinator chat looks like the Claude Code app
  (full-width replies, your messages in bubbles, collapsible activity
  rows, an animated working line) and shows each message's delivery:
  Sending, Delivered, Seen by coordinator, Working, or an amber warning
  when the coordinator hasn't picked it up after 2 minutes.
- Dev team dashboard: the Grid is now a full-screen window workspace.
  Windows auto-tile, with a dark monospace body, run colours, maximize
  (Esc or Restore), an Active only / All recent filter, and a slim
  one-row layout on the phone.
- Dev team dashboard: each coordinator window has a chat box. Type to
  the coordinator (sent as a comment to Claude) and read everything it
  tells you, rendered as markdown; the coordinator mirrors its messages
  to your private dashboard storage.
- Dev team dashboard: the Grid view shows one panel per `/build-feature`
  coordinator (phase, round, elapsed time and its latest status lines)
  with a tile for each researcher, builder and judge, all written by the
  master. Tiles can be dragged and resized on a desktop, each with a
  status badge, and the layout is remembered on that device; a panel
  shows only its newest run when a session ran several. Message
  coordinator opens claude.ai's own comment box (press Send to Claude);
  Open session is the fallback.
- Dev team: `/build-feature` masters may watch the dashboard for
  comments (ArtifactComments is pre-approved in `.claude/settings.json`).
- Dev team dashboard: desktop launchers open it in its own Chrome or Edge
  window on Windows and macOS (`tools/agent-dashboard/desktop/`).
- Dev team: a read-only agent run dashboard (a private claude.ai page,
  source in `tools/agent-dashboard/`). One row per `/build-feature` run;
  the page itself flags runs that have gone quiet past their phase's limit.
  PR-ready, blocked and dead runs untouched for 7 days count as done.
- Dev team: `/build-feature` writes a status row at each phase, marks a run
  done when its PR is merged or closed, and stops a phase that runs past
  its time limit with a blocker note.
- Dev team: an hourly watchdog marks runs whose session died and sends at
  most one phone push per session, including "needs you (approval)" when a
  run waits on a permission prompt.

## 1.0.2 (2026-10-19)

- If the saved sign-in file can't be read, phone access tokens are refused
  (fail closed), the same as browser cookies.

## 1.0.1 (2026-10-19)

- Failover with history turned off now remembers "Sign out everywhere"
  across restarts (saved in `[webapp] sessions_file`).

## 1.0.0 (2026-10-19)

The first full release: the voice assistant from the seven original stages,
plus a phone app, hands-free conversation, conversation history, a cloud
server and Pi -> PC -> cloud failover. Details and test notes per feature are
in PROGRESS.md; security decisions in SECURITY.md; data handling in
PRIVACY.md.

### Voice assistant (stages 1-7)
- Claude chat with conversation history and a short spoken-style system
  prompt; model from `CLAUDE_MODEL` (default `claude-sonnet-5-5`).
- "Hey Claude" wake word (openWakeWord, custom `keywords/hey_claude.onnx`),
  chime, record until silence, faster-whisper `base.en` on CPU, follow-up
  window without repeating the wake word.
- Tools: Google Calendar (list, create, move, delete), Notion assignments
  (list upcoming, set Status), web search and page reading. Deletes always
  ask for a spoken yes.
- Long-term memory in Pinecone: relevant notes before each reply, a short
  summary after each conversation, `search_memory` and `remember` on request.
- `config.toml`, rotating log file, friendly errors when the network or the
  API is down, `python -m assistant` entry point, `--check` setup doctor.

### Phone app (iPhone home-screen web app)
- `python -m assistant --mode web`: push-to-talk with the phone's real mic,
  typed questions, streamed replies, spoken answers, tap-to-confirm deletes.
- Hands-free voice conversation like Claude's voice mode: on-device voice
  activity detection, sentence-by-sentence streamed speech, interrupt by
  talking over it, spoken confirmations that only count after the question
  was heard.
- Sign-in code with per-address lockouts (the right code always works),
  server-side sessions, "Sign out everywhere", strict CSP, no third-party
  scripts.
- History screen: every conversation saved; search, continue, rename, pin,
  archive, export, delete; voice commands to find and reopen conversations.

### Voices
- Kokoro (`af_heart`) as the default natural voice when downloaded
  (`--download-voice kokoro`), first audio in about 0.4 s by speaking the
  first clause early; Piper and pyttsx3 fallbacks; optional ElevenLabs.
- Original soft earcons for listening, thinking and ended.

### Where it runs
- Windows PC: `scripts/install_windows_service.ps1` installs supervised
  startup tasks (web app, optional wake word) and power settings.
- Raspberry Pi: systemd service, preflight check, arm64 CI, migration plan
  (PI_MIGRATION.md).
- Render: Docker image running as a non-root user, CloudGuard auth wall,
  request and upload limits, persistent disk for history and sessions
  (render.yaml Blueprint).
- Failover for the phone app: Pi first, then PC, then cloud, with history
  synced between them as an event log (deletes are permanent everywhere).
- Signed automatic updates from the `stable` branch, which CI promotes only
  when every test passes. Off by default; requires a signature from a key in
  your own allowed_signers file.

### Extras
- Pinecone MCP connector for Claude (pinecone-mcp/).
- Coordinator voice page and Hey Claude Pocket artifact for quick use from
  the Claude app.
