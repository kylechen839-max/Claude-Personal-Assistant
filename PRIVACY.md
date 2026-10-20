# Privacy

Hey Claude is a personal assistant that runs on hardware you control: your
PC, a Raspberry Pi, and optionally a Render server you pay for. There is no
Hey Claude company or shared service behind it, and nothing is sent anywhere
except to the services listed below, using your own keys.

## What leaves your machine, and where it goes

| Service | What is sent | When |
|---|---|---|
| **Anthropic** (Claude API) | What you say (as text, after local speech-to-text), the conversation so far, relevant memory notes, tool results (calendar events, assignments, email you asked about, web pages) | Every turn |
| **Pinecone** (your index) | Short conversation summaries and notes you ask it to remember; search queries built from what you say | Before each reply (search) and after each conversation (summary) |
| **Google Calendar** | Event reads and the changes you ask for | When you ask about or change your calendar |
| **Gmail** (only if `[gmail]` is enabled) | Searches and reads you ask for; **drafts** you approve. Nothing is ever sent: the code blocks every send call | When you ask about email |
| **Notion** | Assignment reads and status changes; lecture notes pages and assignments you add from them, if the lecture recorder is on | When you ask |
| **Web search / fetch** (Anthropic-run tools) | The search query or URL | When Claude decides to look something up |
| **ElevenLabs** (only if `ELEVENLABS_API_KEY` is set) | The reply text to be spoken | Each reply |
| **Hugging Face / GitHub releases** | Nothing about you: model downloads. Kokoro is checksum-verified at setup; Piper voices download at setup, and the Whisper and openWakeWord models on first use, without a checksum check | Once per model |

Speech-to-text (faster-whisper), the wake word (openWakeWord) and the default
voices (Kokoro, Piper, pyttsx3) all run **locally**. Raw audio never leaves
the machine that recorded it, except your phone's recording going to your own
backend (PC, Pi or your Render server) over HTTPS.

## What is stored, and where

All paths are relative to the project folder unless noted. On Render the
`data/` items live on the persistent disk at `/data`.

| Data | Location | Notes |
|---|---|---|
| Conversation history (text, titles, summaries) | `data/history.sqlite` | Search, continue, rename, pin, archive, export, delete from the phone app's History screen or `python -m assistant --history`. Deleting a conversation or message overwrites its text in the file (`secure_delete`) and syncs the deletion to your other backends. `[history] retention_days` and `max_mb` prune automatically; `incognito = true` saves nothing |
| Long-term memory | Your Pinecone index (`project-context`, namespace `voice-assistant` for what the assistant writes) | Delete notes in the Pinecone console |
| Signed-in browsers | `data/web_sessions.json` | Session ids and last-used times only, never the sign-in code. "Sign out everywhere" clears it |
| Google sign-in | `token.json` (+ `credentials.json`, which you download yourself) | The assistant writes `token.json` readable only by you on Linux and the Pi (0600). On Windows it doesn't set permissions itself: the file is as private as the project folder, so keep that folder inside your own user profile. Once Gmail is on, a stolen `token.json` could read and send mail. Revoke at myaccount.google.com/permissions |
| Secrets | `.env` (never committed) | Anthropic, Pinecone, Notion, web sign-in code, optional ElevenLabs |
| Lecture recordings | `data/lectures/` (`[lecture] upload_dir`) | Audio is deleted once transcribed (unless `keep_audio = true`). The transcript goes to Anthropic for summarizing and the notes to your Notion "Lecture Notes" page; once that page exists, the local transcript and notes are deleted, leaving only a small job record (title, course, date, Notion link, suggested assignment names) for the app's list. If a job fails, its transcript (or the audio, if transcription failed) stays so you can retry, and is deleted after 14 days without a retry; abandoned uploads after 48 hours |
| Homework photos | Not stored | Sent to Anthropic with your question, metadata (location, camera, comments) stripped first |
| Chat attachments (the + button) | Not stored | Photos and files you attach in the phone app are sent to Anthropic with that message, like the message itself. They are kept only in the app's memory for the rest of that conversation; saved history and its sync keep only a marker such as `[photo]` or `[file: name.pdf]`, never the file. Photos are re-encoded first, which removes location (GPS) and other EXIF data; PDFs and text files are sent as they are, including any metadata inside a PDF. The log records how many files and their sizes, never names or contents |
| Quick notes ("remember that...") | Your Pinecone index | The text is only in Pinecone; the conversation's history just records that a note was saved. "Delete that note" removes it. Notes saved while outside content was in the conversation are marked untrusted |
| Memory review (phone app) | Your Pinecone index; `data/memory_review.json` | The Memory screen shows and deletes what the assistant saved in your `voice-assistant` namespace (deleting removes the vector for good). The optional weekly review stores only the time of the last offer. Family members never see your memory |
| Spotify (optional) | `data/spotify_token.json` on your computer | Only if turned on: your play/pause/skip/volume/device commands and the words you asked to play (as a search) go to Spotify, and it reads playback state, devices and playlist names. Song and playlist names may go to Claude in the conversation but not to the log at INFO. Delete the file and remove the app at spotify.com → Account → Manage apps to disconnect |
| Logs | `logs/assistant.log` (rotating, ~4 MB max); on Render, the same lines also go to Render's log service (its dashboard and retention) | INFO level records lengths, yes/no answers and tool names, **not** what you said, replies, email text or memory summaries. Those appear only at DEBUG, which is off by default |
| Downloaded models | `models/`, `voices/` | No personal data |
| PC hub (optional) | `data/devhub.json` on the PC (`[devteam] hub_state_file`), a private file | The messages you type to the dev team coordinator and its replies, at most 200 per window. Clear deletes a window's conversation; removing a session from `hub_sessions` deletes its saved conversation at the next start. Messages go to your own Claude Code cloud session through the CLI; replies are read from your Notion "Hey Claude coordinator inbox" |
| Dev team dashboard chat | The dashboard artifact's db on claude.ai, under your private `data/users/<your id>/chat-<session>/msgs` subtree (readable only by you) | Your messages to a `/build-feature` coordinator and its replies, written by the coordinator; the page only reads them. Messages you send from the chat box are also posted as comments on the artifact (claude.ai asks for consent once). Chats older than 30 days are pruned. The page's browser storage holds only the view, window layout, filter and the coordinator you picked (one claude.ai session link); it never leaves your browser |

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
not visible to your account in the app. Removing someone signs them out and
deletes their folder and entry; if a file can't be deleted right away, the
server retries at the next start, the next removal, or when you open the
family list.

## A second instance for someone else

If you run a separate instance for a family member (`config.mom.toml`), it
keeps its own conversation history (`data/mom/history.sqlite`), sign-ins
(`data/mom/web_sessions.json`) and log (`logs/mom.log`). It does not use your
Notion, Pinecone memory or Google account, and it refuses to start if those
settings are inherited from the shell. Her Google sign-in token is stored only
on the PC (`data/mom/google_token.json`, written privately) and gives read
access to her Gmail and Calendar plus creating drafts; nothing is ever sent.
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
