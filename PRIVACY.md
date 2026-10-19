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
| **Notion** | Assignment reads and status changes; lecture and note pages if those features are on | When you ask |
| **Web search / fetch** (Anthropic-run tools) | The search query or URL | When Claude decides to look something up |
| **ElevenLabs** (only if `ELEVENLABS_API_KEY` is set) | The reply text to be spoken | Each reply |
| **Hugging Face / GitHub releases** | Nothing about you: model downloads (Whisper, Piper, Kokoro), checksum-verified | Once, at setup |

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
| Google sign-in | `token.json` (+ `credentials.json`) | Readable only by you (0600 on Linux; private file on Windows). Revoke at myaccount.google.com/permissions |
| Secrets | `.env` (never committed) | Anthropic, Pinecone, Notion, web sign-in code, optional ElevenLabs |
| Logs | `logs/assistant.log` (rotating, ~4 MB max) | INFO level records lengths, yes/no answers and tool names, **not** what you said, replies, email text or memory summaries. Those appear only at DEBUG, which is off by default |
| Downloaded models | `models/`, `voices/` | No personal data |

## Your controls

- **Don't save a conversation:** turn on incognito (`[history] incognito = true`).
- **Forget a conversation:** delete it in History; it's removed on every
  backend that syncs with this one.
- **Sign out a lost phone:** gear menu, "Sign out everywhere", or change
  `WEB_TOKEN` in `.env` (that also cuts off Shortcuts and other bearer clients).
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
  in the conversation, any change to your calendar, Notion or memory needs
  your spoken or tapped yes. Deletes always ask.
