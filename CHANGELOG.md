# Changelog

## Unreleased

- Google Calendar by voice now covers most of Google Calendar: event colors
  (Google's 11 colors or plain words) and your named event labels, plus your
  own color names in `[calendar] color_labels`; repeating events, edited one
  occurrence or the whole series (a weekly series can move to another
  weekday); reminders; busy/free and private; inviting guests and adding a
  Meet link (always asks first and names every address); accepting,
  declining or "maybe" on invites (asks first and reads out any note);
  finding free time (never hours already past); search; other calendars or
  all of them; and "which calendars do I have?". Labels and other
  calendars need a one-time Google sign-in again (two read-only
  permissions). This update changes `requirements.txt`
  (google-api-python-client 2.199+), so the auto-updater skips it: run
  `python -m assistant --update`.
- Health checks: a watchdog (`python -m assistant.watch`, the
  `\HeyClaude\Watch` task every 5 minutes, or the Pi's
  `hey-claude-watch.timer`) checks your app and every family copy:
  `/healthz`, the page and its scripts, the running version and the phone
  address; after every restart or new version and every 6 hours, a real
  test conversation (text with a read-only tool, a follow-up, voice
  message, hands-free voice, text) that saves nothing and changes nothing.
  An app that fails is restarted once, then rolled back to the last
  version that passed. You hear about it once, when it breaks and when it
  recovers: a banner in your phone app and a message to the dev team
  coordinator. New `[watch]` settings.
- Safe updates: new code goes live only after it passed that test
  conversation on a side port (port + 100); otherwise the old version
  keeps running. `scripts/deploy.ps1` / `deploy.sh` pull, check and switch
  in one command; `install_family_tasks.ps1 -Restart` checks first too.
- When the Claude API itself is down or overloaded, the app now says
  "Claude is unreachable right now. I retried a few times; please try
  again in a minute."
- The watchdog waits the same 1/5/15/60-minute backoff as the hosts before
  re-running a go-live check that couldn't tell, alerts once when the
  automatic updater's go-live check keeps failing to run, and a check copy
  killed by the system (e.g. out of memory on a Pi) no longer gets a commit
  refused.
- CI: `api-contract` runs a real multi-turn conversation (tool use,
  thinking, two requests sharing a history, the voice socket) against the
  Anthropic API when the `ANTHROPIC_API_KEY` secret is set.
- Fixed: asking about "tomorrow" just before midnight could list the day
  after tomorrow. The date is now read once per question, so the system
  prompt and the calendar and Notion tools agree.
- Fixed: Whisper occasionally produced "!!!!" instead of words. Lecture
  transcription and voice commands now detect it, decode that piece again
  (greedily, in float32) and drop it if it is still garbage, so it never
  reaches notes or Claude.
- Tests: the Windows voice-latency test excludes only the OS test voice,
  and the end-to-end calendar test pins the clock.
- Fix: a conversation could break for good ("Sorry, something went wrong
  talking to Claude" on every turn) after hands-free voice shared it: a
  thinking block signed for another request was sent back. Earlier turns'
  thinking blocks are no longer sent.
- Fix: hands-free voice on the phone cancelled replies before a word of
  them was heard ("barge-in after 0 chunk(s)"): any speech, noise or the
  app's own cue while Claude was still thinking counted as an interruption,
  even with `[voice] barge_in = "off"`. Barge-in now needs speech that
  starts after the answer's audio has started; what you say while it is
  thinking is asked next, once the answer has played; the app's sounds
  can't start a turn. A reply cut off that early no longer leaves a
  fragment (an unsigned thinking block) in the conversation.
- Attach photos (camera or library), PDFs and text files to a chat message
  with the new + button left of the text field. Up to 4 per message, shown
  as removable chips, sent with the next typed or push-to-talk message.
  Files are checked by content, photos lose EXIF, history keeps only a
  marker, and after an attachment Claude asks before changing anything.
  PDFs up to 100 pages. New setting `[webapp] max_attachment_mb` (10).
  A file Claude can't open (password-protected or broken PDF) says so;
  long PDFs are counted by their page tree, so annotated PDFs aren't refused.
- Cloud: the sign-in page now loads its translations before sign-in, so a
  Chinese family member's login page is in Chinese right away.
- Memory review: a Memory screen in the phone app (settings menu, owner
  only) lists conversation summaries and notes saved in Pinecone, newest
  first, grouped by week, with date, kind and an "untrusted" badge, plus
  Delete and "Delete all from this week" (both confirm). By voice, "what
  did you remember about me this week?" and "forget that..." (spoken yes
  first). Optional weekly nudge (`[memory] weekly_review`, off).
- Fix: "list my recent notes" was always empty with pinecone 7+
  (`Index.list` pages are `ListResponse` objects).
- Smart home control through Home Assistant (off by default): "turn off
  the bedroom lights", "set the thermostat to 70", "is the front door
  locked?". New `ha_list`, `ha_state`, `ha_control` tools use the Home
  Assistant REST API with a long-lived token (`HA_URL`/`HA_TOKEN` in `.env`,
  `[home_assistant] enabled = true`). Only allowlisted device kinds and a
  fixed set of actions; scripts only if allowlisted. Locks, garage doors
  and alarms always ask first, and unlocking or opening is refused while
  web or email content is in the conversation. Owner only.
- Spotify by voice (off by default, Premium required): "play my study
  playlist", "pause", "skip", "what's playing?", "turn it up", "play it
  on my phone". Your own playlists are searched first. Sign in once with
  `python -m assistant.tools.spotify` or the owner-only Connect Spotify
  (PKCE, read-and-control scopes only; nothing changes playlists or the
  library). In wake-word mode, music on this PC pauses at "Hey Claude"
  and resumes after. Family members don't get Spotify yet.
- Reply voice in the phone app's settings: each person (you, each family
  member, separate instances like Mom's) picks their voice from what that
  server has in their language (Piper, Kokoro, the system voice, and your
  ElevenLabs voices tagged paid if `ELEVENLABS_API_KEY` is set; family
  only with `[tts] family_paid_voices`). Preview plays a sample. New
  `[tts]` settings `picker`, `voices_dir`, `voice_file`, `family_paid_voices`.
- Family members are self-service on the cloud server: each can connect
  their own Google Calendar and Gmail (drafts only; deletes ask first) from
  their own phone, kept separate from Kyle's and deleted on Disconnect or
  removal; the sign-in is bound to the person's session and browser.
  Chinese-speaking members get Chinese speech recognition (Whisper small)
  and the Mandarin voice, loaded on first use, and the assistant's own
  sentences follow each person's language on a shared server. The cloud
  image bundles the Chinese models (`FAMILY_ZH=0` leaves them out);
  render.yaml adds `PUBLIC_ORIGIN` and `GOOGLE_WEB_CLIENT_JSON`.
  A family member whose Google sign-in expires is told, in their language,
  to reconnect Google from the app's settings.
- Interrupt by talking: on the PC, say "Hey Claude" while it is answering
  and it stops and listens (`[wake] barge_in = "wake_word"`, default);
  `barge_in = "voice"` lets you just talk (echo gate; best with headphones
  or an echo-cancelling speakerphone). "Hey Claude, stop" just stops;
  Claude remembers roughly the part you heard; talking over a yes/no
  question is a no; a reply saying "Claude" can't wake itself. On the
  phone, a soft cue and "Still listening…" (还在听…) show the follow-up
  window; `[voice] barge_in = "off"` for car speakers.
  Every voice can be stopped mid-reply (pyttsx3 on Linux/Pi renders each
  reply first), and `[wake] barge_in` accepts `false` or any case.
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
