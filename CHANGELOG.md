# Changelog

## Unreleased

## 1.1.0 (2026-10-26)

- The server's `/` and `/healthz` also answer HEAD (uptime monitors and link
  checkers got 405), and the cloud profile's lecture piece and photo sizes
  now fit its 4 MB upload cap, so the startup log no longer warns about them.
- Session watcher (`tools/session-watch/`): a local, report-only tool for
  Windows and macOS that notices when a Claude Code session on your computer
  is waiting on you: an approval prompt, a question, an idle session, or a
  session that stopped because a security check (the auto mode classifier,
  the built-in safety check, a permission deny rule, an MCP tool deny or a
  PreToolUse hook) refused a tool call. It uses an optional Notification hook
  and the session transcripts (including subagents), shows one desktop
  notification per block, and can have the Codex CLI (read-only sandbox,
  scan output only) summarise which session to open first. It never answers
  prompts or edits Claude Code settings, and never keeps transcript or
  denial text.
- Public beta launch: the cloud server now serves /welcome, /privacy,
  /terms and the waitlist (`public_site` on in the server profile), with
  the support address heyclaude.app@gmail.com and without the "Draft"
  note.
- **Renamed to Kwil** (said "quill"). The app, pages, notifications and the
  assistant's own name say Kwil; it still runs on Claude. The package is
  `kwil` (`python -m kwil`); `python -m assistant`, `-m assistant.host` /
  `.watch` / `.deploy` and `import assistant` keep working for at least one
  release (an alias plus file shims, so even an app mid-update finds its
  modules), so existing PC tasks, Pi services and the Render service keep
  running, and nobody is signed out. New Task Scheduler folder `\Kwil\` and
  systemd units `kwil.service` / `kwil-watch.timer`; the install scripts
  replace the old ones. `KWIL_VERSION` replaces `HEY_CLAUDE_VERSION` (still
  read). Updates accept the repo under its old or new name. The PC wake word
  is still "Hey Claude" until a "Hey Kwil" model is trained; the phone's
  voice mode already understands "Hey Kwil".

- Calendar: listing your own events no longer makes the next move or change
  ask for confirmation. Notes on events you organize, your contacts'
  birthdays, Google's holiday calendars and the calendar list now count as
  your own data; invites from others, events Gmail made, events your guests
  may edit and calendars shared with you still make changes ask. An event the
  assistant wrote while outside text was in the conversation stays marked, so
  it keeps asking.
- Study briefings: optional phone notifications (morning, evening, weekly,
  exam reminders, new deadlines, after class) with a tap-to-play brief; at
  most 2 a day, quiet 22:00-07:30 in your time zone, built from your School
  deadlines. Claude writes a message only when a rule fires, from the
  deadline fields alone. Settings > Enable notifications (iPhone: Home
  Screen app, iOS 16.4+). Each backend sends to the phones subscribed
  through it. New `[push]` and `[proactive]` settings; `pywebpush`
  dependency; `cryptography>=47`. A separate instance without its own
  `[push] owner_dir` has notifications off.

- Speech recognition is much more accurate on the PC: Whisper small.en with
  beam search and a vocabulary prompt (calendar, assignment, Notion, ...),
  plus `[stt] hotwords` for names. The Pi and the cloud server stay on
  base.en, now with beam search. Whisper "hearing" its prompt back on silence
  is ignored.
- Optional paid cloud speech-to-text (OpenAI gpt-4o-transcribe or Deepgram
  nova-3), off by default, with local Whisper as the fallback.
- Hands-free: a quick "yes" to a confirmation is no longer dropped as a
  misfire, and a short sound while the question is still playing no longer
  cuts it off.

- Confirmations: a misheard yes ("ya", "yep", "okay", "sure", "right",
  没错, 好, 可以, 对对对) now counts; an unclear answer is asked once more
  ("Sorry, I didn't catch that"), then cancelled, never a third time. Numbers
  and questions back ("Yes, 11", "Right?") are asked again, not taken as a
  yes. Claude no longer asks "are you sure?" itself before adding, moving or
  changing a calendar event; deletes, guest changes, invite answers and
  changes after untrusted content still ask.
- Student deadlines: connect Canvas (calendar feed link or access token,
  never a password) and Hey Claude keeps track of exams, assignments and
  quizzes, plus Canvas notification emails (newest email by Gmail's arrival
  time wins). A "School deadlines" card lists what was captured, to confirm,
  fix conflicting dates or delete. Re-syncs never duplicate, and confirmed
  dates are never overwritten. New voice tools: list, confirm, add and delete
  deadlines. New settings: `[student]`, `[canvas]`; `CANVAS_ICS_URL` /
  `CANVAS_TOKEN` / `CANVAS_BASE_URL`. A separate family instance without its
  own `[student] dir` has deadlines turned off.

- Dev team: every coordinator mirrors all its replies (comment-thread replies
  too), its in-between updates and step lines to the dashboard chat, and
  keeps the live status on the current step, like the Claude app.
- Claude settings: pre-approve `mcp__claude-code-remote__create_session` (Kyle, 2026-10-21) so the coordinator can start sessions on his own machines, such as the trusted relay.
- Fixed: CI went red when anthropic 2.0 came out (it sends API keys as a
  Bearer token, so people's own keys no longer went out as X-Api-Key).
  requirements.txt now keeps the SDK below 2.
- Fixed: with `[tts] family_paid_voices` on, beta testers could pick
  ElevenLabs voices on the owner's bill. Paid voices now go only to
  family-tier members (and the owner); a beta tester never sees them, can't
  choose one, a paid voice saved earlier falls back to the default, and an
  ElevenLabs server default speaks to them with its local fallback.
- Fixed: the owner's Remove now revokes the person's Google sign-in at Google
  (best effort, before their folder is deleted), as Delete my account already
  did; it already revoked Notion.
- Invited people (beta and family) can connect their own Notion from Settings:
  Connect Notion signs in with Notion's own consent page, and their
  Assignments database gives them two tools on their own account: list
  assignments and mark one Not started / In progress / Done (asks first once
  the conversation holds Notion text). The sign-in is stored encrypted on the
  server; Disconnect, Delete my account and the owner's Remove revoke it at
  Notion. Needs a public Notion integration (`NOTION_OAUTH_CLIENT_ID` /
  `NOTION_OAUTH_CLIENT_SECRET`, `[family] notion`). Reconnecting to another
  workspace revokes the previous grant; Notion rate limits delay a request by at most 2 s (one
  retry). Requires notion-client 3.1 or later.
- Invited people (family and beta) now have per-person limits: 6 messages a
  minute, 200 a day and 30 minutes of speech recognition a day by default
  (`[family] turns_per_minute`, `turns_per_day`, `stt_seconds_per_day`; 0 =
  off). Over a limit, the app says so in their language and nothing is sent to
  Claude. When speech time runs out they can still type. The owner is never
  limited. Counts are kept in memory, so a restart resets them. A spoken
  yes/no is only transcribed once per question, and when speech time is used
  up it isn't heard; the question stays open for the buttons or typing.
- Invited people can download their data (a zip of their conversations and
  profile) and delete their account (revokes Google access, signs out
  everywhere, deletes everything) from Settings, in English and Chinese. New
  setting `[family] export_max_mb` (default 50). Downloads are built in the
  family data folder and deleted when the download ends, even if the phone
  disconnects first; deleting your account also removes any download still
  being made. An old, stalled download finishing late no longer frees a newer
  export's one-at-a-time slot.
- Ops: optional error reports (only with `SENTRY_DSN` set; sentry-sdk is now
  installed with the app and stays off without it), an owner-only backup
  download (`GET /api/backup`), and the PC's watchdog can also watch the cloud
  app (`[watch] cloud_url`). Setup for an UptimeRobot monitor on /healthz is
  in the README.
- Beta testers bring their own Anthropic API key: Settings -> Your Anthropic
  API key (checked with Anthropic, stored encrypted with `USER_DATA_KEY`).
  Without one a beta tester is told to add it; family members still use the
  owner's key unless they add their own. Their usage no longer counts toward
  the owner's monthly estimate.
- Fixed: the go-live gate refused every update on a machine without a web
  app (a wake-only Pi, or a family copy that never started), and the wake
  host undid a hand pull there. Such machines now go live after the import
  smoke; a failing `--check` (no mic, no audio device, no key) is retried
  and reported instead of refusing for good, in the gate and the automatic
  updater alike.
- Fixed: when Haiku moved to a change mid-turn, Sonnet received Haiku's
  signed thinking, which the API rejects; the whole turn now reruns on Sonnet
  from your message.
- The watchdog and go-live test conversation start with a quick question
  Haiku answers, and the api-contract CI job also runs with routing on.
- The gate and deploy refuse until the packages in requirements.txt are
  installed; `install_family_tasks.ps1 -Restart` waits for a running turn
  and restarts every app, your own included. A web app whose WEB_TOKEN is
  only in the environment shows as "unchecked".
- Public beta pages (off until launch, `[webapp] public_site`): /welcome
  (landing page with a waitlist), /privacy and /terms (drafts, `[webapp]
  legal_draft`), and an owner-only /waitlist list. The waitlist is
  rate-limited per visitor, de-duplicated, capped at 2000 entries and never
  logged; `waitlist_file` is empty (closed) except on the cloud server. New
  settings: `[webapp] public_site`, `support_email`, `legal_draft`,
  `waitlist_file`.
- Invite tiers: new invites are Beta (Calendar only) by default; Family keeps
  Calendar and Gmail. Beta testers never get Gmail tools or Gmail scopes and
  sign in through a separate Calendar-only Google client
  (`GOOGLE_PUBLIC_CLIENT_JSON` / `[family] public_google_client`); without it
  they see "Google isn't set up for beta accounts yet." Each person's history
  is now capped at `[family] max_history_mb` (20 MB). Existing people stay
  Family.
- Cheaper replies: short, simple questions are now answered by Claude Haiku
  5.5 (`[assistant] fast_model`, `router = "auto"`). Anything that changes
  your calendar, notes, email or home, needs a web lookup or real reasoning,
  has an attachment or follows untrusted content stays on the main model,
  and any change Haiku tries to make is handed to the main model before
  anything runs. `CLAUDE_ROUTER=off` turns routing off. The tools and system
  prompt are now cached by the API: the date, recalled notes and the weekly
  review offer travel with each message instead. Each API call's token
  counts are logged without any text, and your phone app's state shows a
  monthly cost estimate (`month_usd`, owner only). New `BACKLOG.md` lists
  the work left before a public release.
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
- Team chat connectors (off by default, owner only): Claude can read Slack
  channels and threads (user token), Discord channels and threads (bot) and
  GitHub issues, pull requests and discussions, search them, list and read
  shared text files (up to 50 KB) and sum them up. Nothing is ever posted.
  "Save that as a digest" files the summary as a page in a Notion "Chat
  Digests" database after a spoken yes, once per service, channel and day.
  New `[teamchat]` settings; tokens `SLACK_USER_TOKEN`, `DISCORD_BOT_TOKEN`,
  `GITHUB_TOKEN` in `.env`. A family instance never uses them, and a
  refusal to start is now written to that instance's log.
- Fixed: lecture notes failed on the default model (Claude Sonnet 5.5) with
  an HTTP 400, because it rejects forced tool calls. Summaries now use
  structured outputs (a JSON schema), with more room for the model's
  thinking and a longer timeout for this background request. This update
  changes `requirements.txt` (anthropic 0.77 or newer), so the auto-updater
  skips it: stop the assistant and run `python -m assistant --update`.
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
  owner's Notion, memory, Google, history, lecture/tutor or dev team.
  Removing someone signs them out and deletes their data (retried later if a
  file is locked). Invites and removals are managed at the cloud server's own
  address. "Sign out everywhere" is now per person. A session the server
  doesn't list is never treated as the owner.
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

### Dev tooling

- The agent run dashboard (the private claude.ai page that shows each
  `/build-feature` run) moved, with its row schemas, desktop launchers and
  their tests, to its own private repo, kylechen839-max/agent-dashboard;
  its changes are recorded there. This repo keeps the master's side
  (`.claude/skills/build-feature/SKILL.md`) and the dashboard watchdog
  prompt (`docs/WATCHDOG.md`). During this release the dashboard gained one
  row per run with quiet runs flagged, a desktop-only window workspace (a
  window per coordinator, researcher, builder and judge, a coordinator
  picker, hide and restore, close idle agents), a chat box per coordinator
  with delivery states and attachments, and "Get desktop app" launchers
  for Windows and macOS. It also moved to a new artifact URL (the old one
  stopped waking the coordinator); re-download the desktop launcher from
  Get desktop app.
- Dev team: `/build-feature` writes a status row at each phase, marks a run
  done when its PR is merged or closed, and stops a phase that runs past
  its time limit with a blocker note.
- Dev team: masters may watch the dashboard for comments (ArtifactComments
  is pre-approved in `.claude/settings.json`), re-arm that watch after every
  publish and container restart, and never stop it. A stopped-and-remade
  watch was dropped on restart, which is why dashboard messages kept going
  unanswered.
- Dev team: an hourly watchdog marks runs whose session died and sends at
  most one phone push per session, including "needs you (approval)" when a
  run waits on a permission prompt.
- Dev team: the master merges its own PRs (merge commit) once CI is green
  and the judge approved, or turns on GitHub auto-merge where allowed.
- Removed the PC hub (`/devteam` on the PC's web app) and its `[devteam]
  hub`, `hub_sessions`, `poll_seconds` and `hub_state_file` settings (old
  values in `config.local.toml` are ignored). Talk to the dev team by voice
  (`message_dev_team`) or from the dashboard. You can delete an old
  `data/devhub.json`.
- Dev team link: a timed-out Claude Code CLI on Windows is stopped with its
  whole process tree.
- Design skills: `.claude/skills/` now has three vendored, pinned skills
  for UI work (Anthropic `frontend-design`, Vercel `web-design-guidelines`
  with its rules read locally, Addy Osmani `accessibility`), plus
  `DESIGN_SKILLS.md` with their sources and where this repo's rules win.

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
