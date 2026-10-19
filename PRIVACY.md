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
| Quick notes ("remember that...") | Your Pinecone index | The text is only in Pinecone; the conversation's history just records that a note was saved. "Delete that note" removes it. Notes saved while outside content was in the conversation are marked untrusted |
| Logs | `logs/assistant.log` (rotating, ~4 MB max); on Render, the same lines also go to Render's log service (its dashboard and retention) | INFO level records lengths, yes/no answers and tool names, **not** what you said, replies, email text or memory summaries. Those appear only at DEBUG, which is off by default |
| Downloaded models | `models/`, `voices/` | No personal data |

## Your controls

- **Don't save a conversation:** turn on incognito (`[history] incognito = true`).
- **Forget a conversation:** delete it in History; it's removed on every
  backend that syncs with this one.
- **Sign out a lost phone:** gear menu, "Sign out everywhere", or change
  `WEB_TOKEN` in `.env` (that also cuts off Shortcuts and other bearer clients).
  With failover on, "Sign out everywhere" also signs out every phone's
  access token on all backends that sync history; one that is off applies it
  on its next sync. A backend with history turned off only signs out itself,
  so there change `WEB_TOKEN`. Clients that use `WEB_TOKEN` itself are cut
  off only by changing it.
- **Stop Gmail access:** set `[gmail] enabled = false` and revoke the app at
  myaccount.google.com/permissions.
- **Wipe everything local:** delete `data/`, `logs/`, `token.json`, and the
  Pinecone namespace `voice-assistant`.

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
