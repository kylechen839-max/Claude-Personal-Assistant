# Privacy

Kwil is a personal assistant that runs on hardware you control: your
PC, a Raspberry Pi, and optionally a Render server you pay for. There is no
Kwil company or shared service behind it, and nothing is sent anywhere
except to the services listed below, using your own keys.

Kwil was called Hey Claude until 2026-10-22. The rename changes no data,
storage location or recipient; what is stored and where stays as described
here.

## What leaves your machine, and where it goes

| Service | What is sent | When |
|---|---|---|
| **Anthropic** (Claude API) | What you say (as text, after speech-to-text), the conversation so far, relevant memory notes, tool results (calendar events with their colors, labels, guest counts and the first 300 characters of their notes, your calendars' names, guest addresses you ask to invite; assignments, email you asked about, web pages) | Every turn |
| **Pinecone** (your index) | Short conversation summaries and notes you ask it to remember; search queries built from what you say | Before each reply (search) and after each conversation (summary) |
| **Google Calendar** | Event reads and the changes you ask for. After your spoken yes, Google emails the guests you invite or remove, and the organizer (with your note, if any) when you answer an invite; for other changes, moves and deletes the assistant asks Google not to email anyone (Google says it may still send some notices). Events the assistant creates or edits while outside text is in the conversation carry a private `hc_untrusted` marker, visible only to your account through the API, so they keep asking before changes | When you ask about or change your calendar |
| **Gmail** (only if `[gmail]` is enabled) | Searches and reads you ask for; **drafts** you approve. Nothing is ever sent: the code blocks every send call | When you ask about email |
| **Notion** | Assignment reads and status changes; lecture notes pages and assignments you add from them, if the lecture recorder is on | When you ask |
| **Slack / Discord / GitHub** (only if `[teamchat]` is on) | Reads and searches you ask for, with your token; the message, issue or file text goes to Anthropic so Claude can sum it up, as with email. Nothing is ever posted. Slack direct messages are never passed to Claude or logged. A digest you approve is saved in your Notion. Logs hold only counts and service names. A family member's instance never uses team chat | When you ask about team chat |
| **Web search / fetch** (Anthropic-run tools) | The search query or URL | When Claude decides to look something up |
| **ElevenLabs** (only if `ELEVENLABS_API_KEY` is set) | The reply text to be spoken | Each reply |
| **Sentry** (only if `SENTRY_DSN` is set) | Error reports: the error's type and code location, the request's method and path, the app version and Python version. Never what you said, the error's message, cookies, IP or request contents | When something fails |
| **OpenAI** (optional, off by default) | Speech-to-text, only with `[stt] engine = "openai"` and `OPENAI_API_KEY`: every recording that machine transcribes, plus the vocabulary prompt | Each recording |
| **Deepgram** (optional, off by default) | Speech-to-text, only with `[stt] engine = "deepgram"` and `DEEPGRAM_API_KEY`: every recording that machine transcribes, plus the vocabulary as keyterms | Each recording |
| **OpenAI Codex** (optional, only if you run the session watcher's Codex launcher) | The session watcher's scan output for your own computer: session ids, project folder names, wait times, the kind of block, tool names and Claude Code's short notification text (up to 200 characters). Never transcripts, the denial text or the session's folder path | When a new blocked session is found |
| **Hugging Face / GitHub releases** | Nothing about you: model downloads. Kokoro is checksum-verified at setup; Piper voices download at setup, and the Whisper and openWakeWord models on first use, without a checksum check | Once per model |

Some requests are answered by Claude Haiku instead of Claude Sonnet; both are
Anthropic models under the same API terms, and nothing goes to another
provider. `data/usage.json` (`[assistant] usage_file`; "" = not kept) holds
only monthly API token counts and a cost estimate per model: no conversation
text, notes or names. Only the owner's phone app sees the estimate; Mom's
instance keeps its own file under `data/mom/`.

Beta testers (invited with the Beta tier): if they connect Google, the app
asks only to see and edit their Google Calendar events and to read their
calendar list and settings; it never asks for or reads Gmail. Their Google
sign-in token is kept only in their own folder on the server and is deleted
when they disconnect or are removed. Their conversation history is kept on
the server, at most 20 MB per person (oldest conversations removed first).

Invited people can download or delete their own account at any time from
Settings. Download my data gives a zip of their conversations and profile
(name, language, time zone, account type, date joined); it never includes
Google sign-in tokens or API keys. The zip is built on the server only for
the download and removed when it ends, or when the account is deleted.
Delete my account takes effect at once in the app: it asks Google to revoke Kwil's access, signs them out on every
device and permanently deletes their conversations, Google connection and any
key they added. Render's automatic disk snapshots may keep a copy until they
expire; they are not used to restore individual accounts. A backup the
owner downloaded earlier also keeps its copy until the owner deletes it.

If the owner turns on error reports (`SENTRY_DSN`), a crash or an error the
app logs sends Sentry the error's type, where in the code it happened (file,
function, line), the request's method and path, the app version and profile,
the Python version, and for the app's own error logs the logger's name and its
fixed message template. Error reports never include what you said or typed,
the error's message, your email, cookies, IP address or request contents.
There is no tracing, and no Sentry headers are added to the app's calls to
Claude, Google or Notion.

The owner can download a backup of the cloud server (`GET /api/backup`, the
owner only): every account's conversations, the family list (names,
languages, time zones, account types; no sign-in tokens or API keys) and the
waitlist. A conversation or account deleted after a backup was downloaded
stays in that backup file until the owner deletes it.

Waitlist (cloud, only once `[webapp] public_site` is on): the email address
and optional note (at most 300 characters) people leave on /welcome, and the
date, in a private file (`[webapp] waitlist_file`, `/data/waitlist.json`).
Used only to invite them by hand; never logged, never sent to Claude. The
owner removes entries at /waitlist (also on request). Render's daily disk
snapshots keep a copy until they expire. The /welcome, /privacy and /terms
pages and the waitlist form are public; everything else still needs sign-in.

If you add your own Anthropic API key, we check it once with Anthropic, then
store it encrypted on our server in your account folder, and use it only to
send your conversations to Anthropic. We never show it back (only its last four
characters) or write it to logs. Your Claude usage is billed to your Anthropic
account under Anthropic's terms. Removing the key in Settings, or deleting your
account, deletes it. We also keep a monthly token and cost estimate for your
key in your folder, shown only to you.

If you connect Notion, the server stores, encrypted with a server secret and
readable only for your own requests: a Notion access token and refresh token
for the pages you chose to share, your workspace's id and name, and the id and
name of the Assignments database it found. Notion's record of your email
address is not stored. Your assignment titles, due dates and statuses are read
from Notion when you ask and sent to Anthropic as part of the conversation;
status changes you ask for are written back to Notion. Disconnecting, deleting
your account, or being removed asks Notion to revoke the token and deletes it;
you can also remove Kwil in Notion under Settings, Connections.
Reconnecting to a different workspace also revokes the previous connection at
Notion; reconnecting to the same workspace keeps only the new sign-in.
To keep the shared server fair, the app counts how many messages each invited
person sends and how many seconds of their speech it transcribes, per minute
and per day. Only the counts, and the times of the messages sent in the last
minute, are kept, with their account id, in the server's memory. They are never written to disk, they reset when the server restarts,
and they are deleted when the account is removed. No text or audio is kept for
this.

Speech-to-text (faster-whisper), the wake word (openWakeWord) and the default
voices (Kokoro, Piper, pyttsx3) all run **locally**. By default, raw audio never
leaves the machine that recorded it, except your phone's recording going to
your own backend (PC, Pi or your Render server) over HTTPS.

Cloud speech-to-text is off by default. Only if `[stt] engine` is set to
`"openai"` or `"deepgram"` (on the cloud server, `STT_ENGINE`) and that
service's API key is added, each recording that machine transcribes, including
invited people's recordings on the cloud server and spoken yes/no answers, is
sent over HTTPS as a WAV file, with a short word list (the app's vocabulary and
any names in `[stt] hotwords`), to OpenAI (`api.openai.com`, gpt-4o-transcribe)
or Deepgram (`api.deepgram.com`, nova-3) under their API terms. Recordings over
five minutes, and Chinese-speaking family members' speech, always stay local;
if the service fails, that recording is transcribed locally instead.
Transcripts are written to the log only at DEBUG level.

## What is stored, and where

All paths are relative to the project folder unless noted. On Render the
`data/` items live on the persistent disk at `/data`.

| Data | Location | Notes |
|---|---|---|
| Conversation history (text, titles, summaries) | `data/history.sqlite` | Search, continue, rename, pin, archive, export, delete from the phone app's History screen or `python -m kwil --history`. Deleting a conversation or message overwrites its text in the file (`secure_delete`) and syncs the deletion to your other backends. `[history] retention_days` and `max_mb` prune automatically; `incognito = true` saves nothing |
| Long-term memory | Your Pinecone index (`project-context`, namespace `voice-assistant` for what the assistant writes) | Delete notes in the Pinecone console |
| Session watcher state (only if you run `tools/session-watch/`) | `~/.claude/session-watch/` on that computer | Session ids, project folder names, times, the kind of block and the hook's short notification text; the hook's markers also hold the session's folder path. It reads the last 64 KB of local Claude Code transcripts but never copies transcript or denial text. Private to you on macOS/Linux; on Windows as private as your user profile. Delete the folder to clear it |
| Signed-in browsers | `data/web_sessions.json` | Session ids and last-used times only, never the sign-in code. "Sign out everywhere" clears it |
| Google sign-in | `token.json` (+ `credentials.json`, which you download yourself) | The assistant writes `token.json` readable only by you on Linux and the Pi (0600). On Windows it doesn't set permissions itself: the file is as private as the project folder, so keep that folder inside your own user profile. Once Gmail is on, a stolen `token.json` could read and send mail. Revoke at myaccount.google.com/permissions |
| Secrets | `.env` (never committed) | Anthropic, Pinecone, Notion, web sign-in code, optional ElevenLabs |
| Lecture recordings | `data/lectures/` (`[lecture] upload_dir`) | Audio is deleted once transcribed (unless `keep_audio = true`). The transcript goes to Anthropic for summarizing and the notes to your Notion "Lecture Notes" page; once that page exists, the local transcript and notes are deleted, leaving only a small job record (title, course, date, Notion link, suggested assignment names) for the app's list. If a job fails, its transcript (or the audio, if transcription failed) stays so you can retry, and is deleted after 14 days without a retry; abandoned uploads after 48 hours |
| Homework photos | Not stored | Sent to Anthropic with your question, metadata (location, camera, comments) stripped first |
| Chat attachments (the + button) | Not stored | Photos and files you attach in the phone app are sent to Anthropic with that message, like the message itself. They are kept only in the app's memory for the rest of that conversation; saved history and its sync keep only a marker such as `[photo]` or `[file: name.pdf]`, never the file. Photos are re-encoded first, which removes location (GPS) and other EXIF data; PDFs and text files are sent as they are, including any metadata inside a PDF. The log records how many files and their sizes, never names or contents |
| Quick notes ("remember that...") | Your Pinecone index | The text is only in Pinecone; the conversation's history just records that a note was saved. "Delete that note" removes it. Notes saved while outside content was in the conversation are marked untrusted |
| Memory review (phone app) | Your Pinecone index; `data/memory_review.json` | The Memory screen shows and deletes what the assistant saved in your `voice-assistant` namespace (deleting removes the vector for good). The optional weekly review stores only the time of the last offer. Family members never see your memory |
| Home Assistant (optional, off by default) | Token in `.env` | When turned on, device names, rooms and states go to Claude (Anthropic) during a conversation that uses them, like calendar events. The token stays on the machine and is masked in logs; device names are kept out of the log file at INFO |
| Spotify (optional) | `data/spotify_token.json` on your computer | Only if turned on: your play/pause/skip/volume/device commands and the words you asked to play (as a search) go to Spotify, and it reads playback state, devices and playlist names. Song and playlist names may go to Claude in the conversation but not to the log at INFO. Delete the file and remove the app at spotify.com → Account → Manage apps to disconnect |
| Reply voice choice | `[tts] voice_file` (owner); `<family dir>/<id>/voice.json` | A voice id only, deleted with a removed family member. With ElevenLabs set up, the server lists your ElevenLabs voices, Preview sends a fixed sample sentence, and choosing an ElevenLabs voice sends that person's replies to ElevenLabs |
| Family member's Google (cloud) | `/data/users/<id>/google_token.json` on the server | A family member's own Google sign-in, used only for their own Calendar and Gmail (read and drafts, never send), never combined with Kyle's. Disconnect or removal asks Google to end access and deletes it; you can also check at myaccount.google.com/permissions. A Chinese-speaking member's speech is transcribed on the server with a Chinese Whisper model, like everyone else's |
| Barge-in audio (PC) | Not stored | With `[wake] barge_in` on, the PC's mic also stays open while a reply plays so it can hear you interrupt. That audio is checked in memory for the wake word or speech louder than the reply, never saved or sent unless it becomes your next request (transcribed locally). `barge_in = "off"` keeps the mic closed while it speaks |
| Health checks (watchdog) | `data/watch_status.json` (private file), `logs/watch.log` | Each app's check results: names, versions, times and fixed reasons such as "healthz: HTTP 502", never what anyone said. Its test conversations (a made-up question about your calendar, a number to remember) go to Anthropic like any turn, read your calendar or assignments (read-only), never search or save memory, and are never saved to history. When the dev team coordinator is set up, a change (an app down or recovered, a version refused) is sent to your own Claude Code session with the same fixed wording |
| School deadlines (Canvas) | `[student] dir` (`data/student/deadlines.sqlite`); a family member's in `<family dir>/<id>/deadlines.sqlite`; their Canvas link sealed in their folder | If you connect Canvas, the calendar feed link or access token is stored (encrypted for family members; in `.env` for the owner), never your school password. From it only each deadline's title, course, type and date are kept. If Gmail is connected, the subject and preview of Canvas notification emails from the last two weeks are read (read-only) and Claude extracts only those fields; the email text is not kept. Searching for your school sends the name you type to Instructure (Canvas). Disconnect removes the link and can delete what was captured; deletes overwrite the text (`secure_delete`). Download my data includes your deadlines, never the link |
| Phone notifications (study briefings) | `[push] owner_dir` (`data/student/push.enc`, `proactive.json`); a family member's `<family dir>/<id>/push.enc` | Only if you turn on notifications: your phone's push subscription (an address at Apple's, Google's, Mozilla's or Microsoft's push service and its keys) is stored encrypted and deleted when you turn notifications off or your account is removed. Notification text is encrypted end to end; the push service can't read it. To word a notification, the server sends Claude only the course, title, type and due date of your deadlines, never page or email text or grades. `proactive.json` keeps only which briefings were sent and when; the last brief is kept in memory only |
| Logs | `logs/assistant.log` (rotating, ~4 MB max); on Render, the same lines also go to Render's log service (its dashboard and retention) | INFO level records lengths, yes/no answers and tool names, **not** what you said, replies, email text or memory summaries. Those appear only at DEBUG, which is off by default |
| Downloaded models | `models/`, `voices/` | No personal data |
| Dev team dashboard chat | The dashboard artifact's db on claude.ai (the page now lives in the private kylechen839-max/agent-dashboard repo; this repo's `/build-feature` master writes the chat), under your private `data/users/<your id>/chat-<session>/msgs` subtree (readable only by you) | Your messages to a `/build-feature` coordinator and its replies, written by the coordinator; the page only reads them. Messages you send from the chat box are also posted as comments on the artifact (claude.ai asks for consent once). Chats older than 30 days are pruned. Files you paste or drop into the chat box are stored in the dashboard artifact's asset store, which only you (its owner and editor) can open, and kept until you delete them or the artifact. The page's browser storage holds only the view, window layout (including which windows you hid), filter, the coordinator you picked (one claude.ai session link) and whether auto-close is on; it never leaves your browser |

## Your controls

- **Don't save a conversation:** turn on incognito (`[history] incognito = true`).
- **Forget a conversation:** delete it in History; it's removed on every
  backend that syncs with this one.
- **Sign out a lost phone:** gear menu, "Sign out everywhere", or change
  `WEB_TOKEN` in `.env` (that also cuts off Shortcuts and other bearer clients).
  With failover on, "Sign out everywhere" also signs out every phone's
  access token on all backends that sync history; one that is off applies it
  on its next sync. A backend with history turned off only signs out itself
  (it remembers that across restarts); to cover it too, change `WEB_TOKEN` there. Clients that use `WEB_TOKEN` itself are cut
  off only by changing it.
- **Stop Gmail access:** set `[gmail] enabled = false` and revoke the app at
  myaccount.google.com/permissions.
- **Wipe everything local:** delete `data/`, `logs/`, `token.json`, and the
  Pinecone namespace `voice-assistant`.

## Family accounts (cloud)

When you invite someone, the cloud server stores their name, language, time
zone, invite status and only a SHA-256 hash of their one-time invite link in
`/data/users/users.json`, and their conversations in
`/data/users/<id>/history.sqlite`, on your Render disk (which you, as the
server's admin, can read). Their messages go to Anthropic under your API
key, and their web searches send only their time zone as location. Their
data is never synced to your Pi or PC, never put in Pinecone or Notion, and
not visible to your account in the app, except in the owner's backup
download, which holds every account's conversations. Removing someone first
asks Google and Notion to revoke Kwil's access (best effort), then signs
them out and deletes their folder and entry; if a file can't be deleted right away, the
server retries at the next start, the next removal, or when you open the
family list.

## A second instance for someone else

If you run a separate instance for a family member (`config.mom.toml`), it
keeps its own conversation history (`data/mom/history.sqlite`), sign-ins
(`data/mom/web_sessions.json`) and log (`logs/mom.log`). It does not use your
Notion, Pinecone memory or Google account, and it refuses to start if those
settings are inherited from the shell. Her Google sign-in token is stored only
on the PC (`data/mom/google_token.json`, written privately) and lets the
assistant read and change her Calendar, read her Gmail and create drafts. No email is ever
sent by the assistant; Google itself emails the guests when she invites
someone or answers an invite, after her spoken yes.
Her requests go to Anthropic under your API key. To remove her Google access,
delete that file and revoke the app in her Google account settings.

## Who can reach it

- The PC and Pi web apps listen on `127.0.0.1` only; your phone reaches them
  through Tailscale (`tailscale serve`), which only your own devices can use.
- The optional Render server is on the public internet behind a sign-in code
  (256-bit), per-address lockouts and request limits; see SECURITY.md.
- Content that comes from outside (web pages, emails, calendar invites from
  other people, notes derived from them) is treated as untrusted: while it is
  in the conversation, any change to your calendar or Notion, and any note
  you save, needs your spoken or tapped yes. The end-of-conversation memory
  summary is still written without asking, but then only from your own
  words and marked untrusted. Deletes always ask.
