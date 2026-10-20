# Hey Claude

An Echo-style voice assistant on the Anthropic API. Say **"Hey Claude"**, ask
something, and it answers out loud. It can read and change your Google
Calendar, check and update your Notion assignments, and remembers past
conversations in Pinecone.

Version 1.0.0: see CHANGELOG.md for what's included and PRIVACY.md for what
is stored and sent where. On your phone, it runs as a home-screen web app
("Install on iPhone" below).

```
mic -> openWakeWord wake word -> chime -> record until silence (VAD)
    -> faster-whisper (speech to text) -> Claude (+ tools, + memory)
    -> Kokoro / Piper / pyttsx3 (text to speech) -> speaker
```

## Quick start (Windows 11, PowerShell)

1. Install **Python 3.12** from python.org (tick "Add python.exe to PATH").
   3.10+ works, but the natural Kokoro voice needs 3.11+.
2. In the project folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   # If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   pip install -r requirements.txt
   copy .env.example .env
   notepad .env        # paste your keys
   ```
3. Get the keys and files:
   | What | Where | Goes in |
   |---|---|---|
   | Anthropic API key | console.anthropic.com | `.env` `ANTHROPIC_API_KEY` |
   | "Hey Claude" wake word model | already included: `keywords/hey_claude.onnx` | `config.toml` `[wake] keyword_path` |
   | Pinecone API key | app.pinecone.io | `.env` `PINECONE_API_KEY` |
   | Notion integration secret | notion.so/profile/integrations; share the Fall 2026 Assignments database with it | `.env` `NOTION_TOKEN` |
   | Google OAuth Desktop client | console.cloud.google.com: enable the Calendar API, add yourself as a test user, create a Desktop OAuth client | `credentials.json` |
4. Sign in to Google once: `python -m assistant.tools.calendar` (saves `token.json`).
5. Check the setup: `python -m assistant --check` (one line per key, file,
   audio device and model; never prints key values).
6. Run it:
   ```powershell
   python -m assistant              # always listening (wake word)
   python -m assistant --mode text  # type instead of talking
   ```
7. Optional: `pytest`. The live tests (real Notion, Google, Pinecone calls)
   read keys from the environment, not `.env`, so load it first:
   ```powershell
   Get-Content .env | % { if ($_ -match '^([A-Z_]+)=(.+)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
   pytest -rs
   ```

Any service without credentials is simply switched off (the app tells you
which), so you can start with only the Anthropic key in text mode.

## Modes and flags

| Command | What it does |
|---|---|
| `python -m assistant` | Wake-word mode (default): "Hey Claude", chime, speak, hear the reply |
| `--mode text` | Type, read replies |
| `--mode speak` | Type, replies spoken |
| `--mode voice` | Push-to-talk (Enter to start/stop) |
| `--audio-file f.wav` | Wake mode on a recording instead of the mic, then exit |
| `--tts print` | No audio output (prints what it would say); also `kokoro`, `piper`, `pyttsx3`, `elevenlabs`, `auto` |
| `--download-voice kokoro` | Download a voice at setup time, then exit (see "Voices") |
| `--timings` | Also print each turn's latency summary (always in the log) |
| `--keyword-path f.onnx` | Override the wake-word model |
| `--config other.toml` | Use a different settings file |
| `--check` | Check keys, files, audio devices and models, then exit |
| `--update` | Update now from the tested `stable` branch, then exit |
| `--mode web` | Private phone web app (see "Phone web app" below) |
| `--host`, `--port` | Web mode: where to listen (default 127.0.0.1:8765) |
| `--show-token` | Web mode: print the access token to the console once |
| `--role pi` | Which backend this is in the phone app's Pi -> PC -> cloud failover (`pi`, `pc` or `cloud`; see FAILOVER.md) |
| `--candidate` | Web mode: the go-live check's side-port copy (no history, no saved sign-ins, no background jobs, its own log); started by the watchdog, not by hand (see "Health checks and safe updates") |

**Follow-up mode:** after a reply, the assistant plays a soft, low blip
and keeps listening for 5 seconds without the wake word, so you can just
keep talking ("...and what about Friday?"). The follow-up is the next turn
of the same conversation. If you say nothing in that window it goes back to
waiting for "Hey Claude" and the conversation ends (it's summarized into
memory). "Goodbye" also ends it. Saying "Hey Claude" again during the window
is fine (it's stripped from what you said). The window opens only after the
reply has finished playing, and the mic is closed while the assistant
speaks, so it doesn't hear its own voice as a follow-up. Settings in
`config.toml` `[wake]`: `follow_up_seconds` (default 5; `0` turns follow-up
mode off, and then a conversation ends on "goodbye" or after
`[assistant] conversation_timeout`, 5 quiet minutes) and `follow_up_chime`
(`false` keeps the window but drops the blip; `chime = false` silences both
chimes). Ctrl+C quits. Deleting a calendar event always asks "yes or no?"
first; that answer goes to the question, not to the follow-up window.

**Interrupting a reply (barge-in):** say "Hey Claude" while it is talking
and it stops at once, plays the chime and listens to your new request, like
an Echo. "Hey Claude, stop" (or "never mind", "别说了") just stops it
without asking Claude anything. Setting `[wake] barge_in`:
- `"wake_word"` (default): only the wake word interrupts. Safe with
  ordinary speakers: the wake model doesn't fire on the assistant's voice.
- `"voice"`: just start talking. It listens while it speaks and stops when
  it hears `barge_in_ms` (400 ms) of speech that is `barge_in_echo_ratio`
  (2.5) times louder than its own voice coming back from the speakers
  (measured at the start of every reply, so it adapts to the volume). Best
  with headphones or an echo-cancelling USB speakerphone; with speakers next
  to the mic it may not hear you over itself (raise the volume of your voice,
  not the speaker), or may cut itself off (raise `barge_in_echo_ratio`).
  The echo is measured over the first 600 ms after the sound card's reported
  latency, so it allows for an echo that comes back up to about half a
  second later than reported. Bluetooth speakers often lag more than that
  (and more than they report): if a reply on a Bluetooth speaker cuts
  itself off within its first second, use wired speakers, headphones or
  `"wake_word"`; raising `barge_in_echo_ratio` doesn't fix a late echo.
- `"off"`: the mic stays closed while it speaks, as before (`false` works
  too; the value isn't case-sensitive).

Every voice can be stopped mid-reply: Kokoro, Piper, eSpeak and ElevenLabs
play through the app's own player; pyttsx3 is stopped from its word
callback on Windows (SAPI), and on Linux / the Pi each reply is first
rendered to a file and then played (eSpeak's own playback can't be cut
short), which adds a moment before it starts. The echo is measured after
the sound card's reported output + input latency (0.35 s if it reports
none), so a slow card doesn't make the reply's echo count as you talking.

What you said takes over as the next turn of the same conversation, and
Claude remembers only about the part of its answer you heard (estimated from
how long it played). Talking over a "yes or no?" question is always a no;
ask again if you meant yes. A reply that itself says "Claude" (reading out
an email, say) can't wake the assistant with its own voice: the wake word is
ignored for that reply. With `--audio-file` and barge-in on, the file is
read in real time while replies play, the way a mic would hear it.

**Timings:** every turn writes one line to `logs/assistant.log`, e.g.
`Turn timings from wake: end of speech +2.10 s, transcript +0.60 s, Claude
reply +1.80 s, first audio +0.10 s; wake to first audio 4.60 s (end of
speech to first audio 2.50 s)`. "End of speech" includes the time you spent
talking and the `silence_ms` pause that tells it you're done; the last
figure is the wait you notice. Run with `--timings` to see it on the
console too.

Things to try: "What's on my calendar tomorrow?", "What did we talk about yesterday?", "Move my dentist
appointment to Friday at 10", "What assignments are due this week?",
"Mark Homework 6 as done", "Delete the study group tomorrow",
"Make my dentist appointment red", "Add gym every Tuesday and Thursday at
7 AM", "When am I free for an hour tomorrow?", "Accept the project sync
invite",
"What's the weather this afternoon?", "Who won the Braves game last night?",
"What does this page say: emory.edu/...", "Open YouTube",
"Pull up the Canvas page for Classical Mechanics".

**Internet access:** web search and reading web pages run on Anthropic's
servers as part of the Claude request (no extra keys; usage is billed to
your Anthropic account per search). "Open ..." requests open pages in this
computer's default browser. Set `[web] location` in `config.toml` for local
answers like the weather, and `allowed_domains` / `blocked_domains` to limit
which sites are used. On a Raspberry Pi with no screen, set `browser = false`.

## Conversation history

Every conversation is saved: wake word and push-to-talk on the PC or Pi,
text mode, the phone app's push-to-talk and its hands-free voice mode. You
can search it and pick a conversation up later.

**By voice or text:** "Hey Claude, what did we talk about yesterday?",
"Find our conversation about the lab report", "Continue our conversation
about the lab report". Claude searches your history and asks *"Continue
'Lab report error bars' from Tuesday?"*; say yes and the old conversation
is loaded, so "and what about standard deviation?" is answered in context.
This works mid-conversation too (the current one is saved first).

**On the phone:** the clock button at the top of the app opens History:
a search bar (results update as you type, matching words highlighted),
filters (Pinned, Phone, Voice, PC, Typed, Archived), and conversations
grouped by Today / Yesterday / This week / Older with a source icon, time
and snippet. Swipe a row left for Pin / Rename / Delete, or long-press it
(or tap its "..." button) for all actions including Archive and Export.
Tap a conversation to read it; **Continue this conversation** loads it and
returns to the chat, and the voice view continues it too (it uses the same
conversation). Delete always asks first. **Incognito** in the settings
menu (gear) stops saving for this phone until you turn it off; the header
shows an "Incognito" badge meanwhile. It follows the phone's light or dark
setting and works as a home-screen app on iOS.

**From the command line:**

| Command | What it does |
|---|---|
| `python -m assistant --history list` | Newest 50 conversations (id, date, source, title); add `--source wake`, `--since 2026-10-01`, `--until 2026-10-14`, `--pinned` |
| `python -m assistant --history search lab report` | Ranked keyword search with the matching words highlighted (same filters) |
| `python -m assistant --history show ID` | The whole transcript |
| `python -m assistant --history export ID --format json` | Export as Markdown (`--format md`, default) or JSON, to the console; redirect with `> file.md` |
| `python -m assistant --history delete ID` | Delete (asks first; `--yes` skips the question) |
| `python -m assistant --mode text --continue ID` | Continue a conversation in text mode (`--continue last` for the most recent) |
| `python -m assistant --incognito` | Save nothing this session: no history and no memory summary |

**What is saved:** what you said and what Claude replied, as text, turn by
turn as it happens (a crash or power cut loses at most the turn in
progress), including spoken "yes/no" confirmations and replies you cut off
(marked "interrupted"). For tool use only the tool name, a few short
arguments and whether it worked are kept (e.g. "list calendar events, day =
tomorrow, ok"), never the results, so calendar details or web pages are not
copied into history. API keys and tokens are masked before saving. When a
conversation ends, a fast model (`[history] title_model`, Haiku) writes a
title of up to six words and a one or two sentence summary in the
background, so replies never wait for it; if the API is unreachable, the
first thing you said becomes the title.

**Search:** keyword search uses SQLite full-text search (FTS5) over messages,
titles and summaries, ranked by relevance, with prefix matching (typing
"chem" finds "chemistry"); if your Python's SQLite lacks FTS5 it falls back
to simpler matching. When Pinecone memory is on, the conversation's memory
summary carries the conversation id, so a semantic match ("that thing about
uncertainty in graphs") also links back to the full conversation. Searching
5,000 conversations takes well under 100 ms.

**Where it lives:** one SQLite file, `data/history.sqlite` (gitignored) on
the PC's disk or the Pi's SD card; on the Render deploy the `HISTORY_PATH`
environment variable points it at the persistent disk
(`/data/history.sqlite`). Settings in `config.toml` `[history]`:

| Setting | Default | What it does |
|---|---|---|
| `[history] enabled` | true | Save and search conversations |
| `[history] path` | "data/history.sqlite" | Where the database file goes (`HISTORY_PATH` overrides) |
| `[history] retention_days` | 0 | Delete unpinned conversations older than this; 0 = keep forever |
| `[history] max_mb` | 500 | Size guard: above this, the oldest unpinned conversations are deleted |
| `[history] incognito` | false | Never save (same as always passing `--incognito`) |
| `[history] title_model` | "claude-haiku-5-5" | Model for titles and summaries |

Deleting a conversation also deletes its memory summary in Pinecone.
Privacy details are in SECURITY.md.

**How it's stored:** every change (a message, a title, a pin, a delete) is
an event in the history sync log (failover's event log, in the same file),
and the searchable tables are built from those events. With the failover
setup the Pi, the PC and the cloud exchange events, so all three show the
same history, and a delete anywhere deletes everywhere (the deleted text is
wiped from the log too). Retention and the size guard delete the same way,
so they also apply on every backend.

## Settings

`config.toml` holds all non-secret settings; anything left out uses the
default in `assistant/config.py`. `CLAUDE_MODEL` in the environment
overrides `[assistant] model` (default `claude-sonnet-5-5`). Logs go to
`logs/assistant.log` (rotating); the console only shows warnings. The ones
you are most likely to tune:

| Setting | Default | What it does |
|---|---|---|
| `[wake] sensitivity` | 0.5 | Higher = fewer missed wakes, more false wakes |
| `[wake] silence_ms` | 800 | Pause that ends your command |
| `[wake] vad_min_threshold` | 0.015 | Minimum loudness counted as speech; raise in a noisy room |
| `[wake] no_speech_timeout` | 5 | Seconds to wait for a command after the wake word |
| `[wake] follow_up_seconds` | 5 | Follow-up window after a reply; 0 = off |
| `[wake] barge_in` | "wake_word" | Interrupt a reply: "wake_word", "voice" (just talk; headphones) or "off" |
| `[wake] mic_device` | "" | Mic index (0 included) or part of its name, for wake and push-to-talk; `--check` lists them and shows the one that will open |
| `[tts] engine` | "auto" | "auto", "kokoro", "piper", "pyttsx3", "espeak", "elevenlabs" or "print"; `[tts] fallback` is the last resort (pyttsx3, or espeak on a server) |
| `[tts] kokoro_voice` | "af_heart" | Kokoro voice (see "Voices") |
| `[tts] speed` | 1.0 | Speaking speed for Kokoro and ElevenLabs |
| `[tts] picker` | true | Phone app: everyone picks their own reply voice (see "Voices") |
| `[stt] model` | "base.en" | faster-whisper model; "tiny.en" is faster |
| `[assistant] timezone` | "" | IANA name, e.g. "America/New_York"; "" = system |
| `[update] enabled` | false | Home host (PC tasks, Pi service): follow the tested, signed `stable` branch; set it in `config.local.toml`, see [Automatic updates](#automatic-updates) |
| `[assistant] api_timeout` | 30 | Seconds before a Claude request gives up |
| `[assistant] fast_model` | "claude-haiku-5-5" | Cheaper model for short, simple questions; `CLAUDE_FAST_MODEL` overrides it |
| `[assistant] router` | "auto" | "auto": simple turns go to `fast_model`, changes, lookups, reasoning, attachments and untrusted content stay on `model`; "off" (or `CLAUDE_ROUTER=off`): always `model` |
| `[assistant] usage_file` | "data/usage.json" | Monthly API token totals and a rough cost estimate (the owner's phone app gets `month_usd`); "" = not kept |
| `[spotify] enabled` | false | Spotify playback by voice (Premium); see [Spotify by voice](#spotify-by-voice) |

**Cost: model routing and prompt caching.** Each turn picks its model
before the request, with no extra API call (`assistant/router.py`): a short,
simple question ("what time is it") goes to `fast_model` (Claude Haiku).
Words that usually mean a change (add, move, book, set, turn off, lock,
pause, forget, save, ...), a web lookup or reasoning, a long request, an
attachment, or untrusted text (web, email, invites) earlier in the
conversation send it to `model`. Words can miss a phrasing, so there is a
second check: if Haiku answers with any tool call that isn't read-only
(the list is `READ_ONLY_TOOLS` in `router.py`: calendar, assignment, email,
notes and home *reads*), that answer is dropped before anything runs and
`model` answers instead. Every change is therefore made by `model` (words
Haiku said before its tool call may already have been spoken). Once a
conversation needed `model` it stays there until it ends. Confirmations are
the same on either model. The tools and system prompt are the same bytes on
every request and are cached by the API (5 minutes); the date and recalled
notes travel with each of your messages instead, and the weekly review
offer with the first one only. Each Claude API call's token counts (chat,
summaries, history titles, tutor, lecture notes: input, output, cache
read/write, web searches) are logged at INFO in `logs/assistant.log`,
without any text.

### Voices

With `[tts] engine = "auto"` (the default) the assistant uses the best voice
you have downloaded, and logs one line saying which and why:

1. **Kokoro** (most natural; [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
   Apache-2.0, run by the `kokoro-onnx` package). The default voice was
   picked to be close to the calm, warm feel of the Claude app's voice
   conversations (the app's own voices aren't available outside it). About
   120 MB: the int8 model (88 MB) and the voice pack (28 MB). Needs Python
   3.11+ (it relies on onnxruntime 1.25+, which has no 3.10 build); on 3.10
   "auto" uses Piper.
2. **Piper** (fast neural voice, the best choice on a Raspberry Pi; about 60 MB).
3. **pyttsx3** (the OS voice: SAPI5 on Windows, eSpeak on Linux), always there.

Download them once, at setup (never while answering). Each Kokoro file's
SHA-256 is checked, and a bad download is deleted:

```powershell
python -m assistant --download-voice kokoro                # into models/kokoro/
python -m assistant --download-voice en_US-lessac-medium   # Piper, into voices/
python -m assistant.tts --voice-file voices/en_US-lessac-medium.onnx --say "Hello Kyle"
```

`models/` and `voices/` are not committed. On a Raspberry Pi, "auto"
prefers Piper even if Kokoro is there, because Kokoro is several times
slower on ARM (PROGRESS.md has the numbers). `--check` shows which voice
is in use.

**Switching voices.** In `config.toml`:

```toml
[tts]
engine = "auto"            # or "kokoro" / "piper" / "pyttsx3" to force one
kokoro_voice = "af_heart"  # the default
speed = 1.0                # 0.9 = a little slower and calmer
```

| Kokoro voice | Sounds like |
|---|---|
| `af_heart` (default) | American, female: warm, calm, the most natural of the set |
| `af_bella` | American, female: brighter and more animated |
| `bf_emma` | British, female: soft and composed |
| `am_michael` | American, male: relaxed, low |
| `af_nicole`, `af_sarah`, `bm_george`, `bm_lewis`, ... | the rest of the voice pack (`am_`/`bm_` = male, `bf_` = British female) |

A Kokoro voice name that doesn't exist logs a warning and uses `af_heart`.
If a forced engine's files are missing, it falls back down the list (Kokoro
-> Piper -> pyttsx3) with a warning instead of crashing. For Piper, set
`piper_voice` to another voice from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) (e.g.
`en_US-amy-medium`). `[tts] rate` and `voice` apply to pyttsx3 only. `--tts
kokoro` on the command line tries an engine for one run.

**Optional cloud voice: ElevenLabs (paid).** Put `ELEVENLABS_API_KEY=...` in
`.env` and set `[tts] engine = "elevenlabs"` (optionally `elevenlabs_voice`,
a voice ID from your ElevenLabs library; the default is the premade
"Rachel", and `elevenlabs_model` is `eleven_flash_v2_5`, its fastest). Every
sentence goes to ElevenLabs' servers and counts against your plan. Each one
is streamed with a timeout; if the request fails (network, quota, a bad
key) that sentence is spoken by the local voice above, and the cloud is
skipped for a minute. Without the key the local voice is used. The key is
never logged.

**Pick your voice in the phone app.** The settings menu (gear) has **Reply
voice**: a list of the voices this server really has, in your language,
with **Preview** to hear a short sample. It offers the Piper voices in
`[tts] voices_dir` (`voices/`), the Kokoro voices if Kokoro is set up (not
on the cloud server, which has too little memory for it), the system voice
(pyttsx3, or espeak-ng on a server) and, only with `ELEVENLABS_API_KEY`
set, your ElevenLabs voices (listed from your account, refreshed hourly,
tagged **paid**; you can also type a voice ID). Chinese speakers see the
Chinese voices (e.g. `zh_CN-huayan-medium`: `python -m assistant
--download-voice zh_CN-huayan-medium`). The choice is per person: yours is
saved on that server in `[tts] voice_file` (`data/voice_choice.json`;
`/data/` on the cloud, `data/mom/` for `config.mom.toml`), each family
member's in their own folder (deleted when you remove them), and it is used
from the next reply on, in push-to-talk and voice conversations alike.
Family members can't pick ElevenLabs voices (your bill) unless you set
`[tts] family_paid_voices = true`. A voice that has gone away (file deleted,
key removed) falls back to the default with one log line. "Use the phone's
voice" in the same menu still wins over all of these. `[tts] picker =
false` turns the picker off.

**Sounds.** Voice mode on the phone plays three soft cues: a gentle two-note
rise when it starts listening, a very quiet low note when your turn has
been heard ("thinking"), and a falling pair when the conversation ends. The
desktop wake chime uses the same listening sound. They are original
sine-wave sounds generated by `assistant/earcons.py` (`python -m
assistant.earcons` rewrites `assistant/web/earcons/*.wav`).

## Phone web app

`python -m assistant --mode web` runs the same assistant (Calendar, Notion,
memory, web search, delete confirmations) as a private web page for your
phone. You talk into the phone's own mic; the PC transcribes with Whisper,
asks Claude, and sends back the reply as text and audio (in the voice from
[Voices](#voices); or tick "Use the phone's voice" in the page's settings). Hold the big mic button to talk (or tap once to start
and again to stop), or type. When Claude wants to delete something, Yes/No
buttons appear; saying "yes" into the mic works too. Each phone/browser
gets its own conversation; after `[assistant] conversation_timeout` (5
minutes) of quiet it ends and is summarized to memory, like the voice loop.
Opening pages in the PC's browser is off in this mode.

**It is private by design.** The server listens on 127.0.0.1 only, so nothing
outside the PC can reach it. Every request needs a session cookie that you
get only by entering the access token once (`WEB_TOKEN`, a 43-character
random value that is created in `.env` the first time you start web mode and
is never written to the log). Wrong tokens are rate-limited; requests from
other sites are refused; the page sends strict security headers.

The phone's mic only works over HTTPS, so reach the PC through
[Tailscale](https://tailscale.com/download), which gives it a private
HTTPS address that only your own devices can open:

1. Install Tailscale on the PC and on the phone, and sign in to the same
   account on both. In the Tailscale admin console (DNS page), make sure
   MagicDNS and HTTPS certificates are enabled.
2. On the PC, start the web app and print the token once:
   ```powershell
   python -m assistant --mode web --show-token
   ```
3. In a second PowerShell window, publish it to your tailnet (not the
   internet; that would be `tailscale funnel`, don't use it):
   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve status    # shows https://<pc-name>.<tailnet>.ts.net
   ```
4. On the phone (Tailscale on), open that `https://...ts.net` address, paste
   the token, and allow the microphone. Add it to the home screen (see
   below; on Android, Chrome menu -> Add to Home screen).

### Attach photos and files

The **+** left of the text field opens a sheet: **Take photo** (the camera),
**Photo library**, or **Choose file** (PDF or a plain text / Markdown file).
What you pick shows as chips above the field, each with an **×** to remove
it, and goes to Claude with your next typed message or push-to-talk
recording ("what does this say?"); a photo on its own can be sent too. Up to
4 per message (the **+** greys out at 4). The phone shrinks photos to 1600
px first; the server checks every file by its first bytes (not its name):
JPEG, PNG, WebP or GIF photos up to `[tutor] max_image_mb` (8), re-encoded
without EXIF/location; PDFs and UTF-8 text files up to `[webapp]
max_attachment_mb` (10); a PDF may have at most 100 pages (Claude's limit),
and a file Claude still finds too long gets "That file is too long for
Claude" (a password-protected or broken PDF: "Claude couldn't read that
file.") instead of a generic error. The whole message must also fit in `[webapp]
max_upload_mb` (10; 4 on the cloud server, so send big PDFs through the PC).
The files are sent to Anthropic with the message and kept only in memory for
the conversation; saved history gets a marker such as `[photo]` or
`[file: syllabus.pdf]`, never the file. Because an attached file is written
by someone else, Claude asks before changing anything (calendar, Notion)
for the rest of that conversation, as after reading a web page or an email.
Family members can attach files too; theirs go only to their own
conversation. The hands-free voice view doesn't take attachments.

### Install on iPhone

1. Install the Tailscale app on the iPhone and sign in to the same account
   as the PC (leave it connected).
2. Open the `https://<pc-name>.<tailnet>.ts.net` address in **Safari**.
3. Share -> **Add to Home Screen** -> Add. A "Claude" icon appears.
4. Open it **from the icon** and enter the token once. The home-screen app
   keeps its own cookies, separate from Safari, so sign in inside the app
   even if you already did in Safari. It stays signed in for 90 days, renewed
   each time you use it.
5. Tap the mic and allow the microphone. If you said no by mistake: Settings
   -> Safari -> Microphone -> Allow (or in Safari, aA -> Website Settings ->
   Microphone), then reopen the app.

Replies play through the phone's speaker even with the ringer switch on
silent. The app shell (page, script, icons) is cached so it opens quickly;
conversations and audio are never cached.

`tailscale serve reset` stops publishing it. "Sign out" in the gear menu
revokes that phone's cookie on the server; "Sign out everywhere" (e.g. after
losing a phone) revokes every browser's (with failover, every phone's access
token on every backend too, see FAILOVER.md). Signed-in phones are listed in
`[webapp] sessions_file` (`data/web_sessions.json`, ids only, no token) so
they stay signed in across restarts; on a host without a persistent disk a
redeploy signs everyone out. Scripts using the token as a Bearer token are
cut off only by changing it: delete `WEB_TOKEN` from `.env` and restart, and
a new one is created. Settings live in `config.toml` `[webapp]`: `port`
(8765), `confirm_timeout` (seconds to wait for a yes, 120), `max_upload_mb`
(10), `max_attachment_mb` (10, per attached PDF or text file),
`max_audio_seconds` (60), and `allowed_origins` (only needed if a proxy
other than Tailscale rewrites the Host header). Keep `host` at "127.0.0.1";
`--check` reports whether the token is set. On the Pi it is the same: install
Tailscale there and run the same two commands.

**On a public server** the ready-made way is the Docker image and Render
Blueprint in the next section (it adds an auth wall in front of every route
and escalating lockouts). By hand, behind an HTTPS proxy: set `WEB_TOKEN`
as a secret in the host's environment (the app refuses to listen on anything
but 127.0.0.1 without it), set `TRUSTED_PROXY=1` (or `[webapp]
trusted_proxy`) so it uses the proxy's X-Forwarded-For/Proto headers (it
also takes the proxy's address instead, e.g. `TRUSTED_PROXY=127.0.0.1` for
`tailscale serve` on the same PC: the headers then count only from there), and
run `python -m assistant --mode web --host 0.0.0.0` (it reads `PORT` from the
environment). With a trusted proxy, plain-http requests are redirected to
https and HSTS is sent. Five wrong tokens from one address lock that address
out of further guesses for 15 minutes; past 20 wrong tokens from everyone
together, each further wrong one is answered a second later. The right token
always works, so nobody can lock you out by guessing. `GET /healthz` answers `{"ok":
true}` without signing in. Scripts can send `Authorization: Bearer
<WEB_TOKEN>` instead of the cookie.

### Voice conversation (hands-free)

Tap **Voice conversation** under the mic button and just talk, like voice
mode in the Claude apps. A full-screen orb shows what it is doing: it moves
with your voice while **listening**, pulses while **thinking**, and moves with
the reply while **speaking**. When you stop talking for `[voice]
silence_ms` (700 ms) it sends what you said, and the answer starts playing
as soon as its first sentence is ready (the rest is still being written).
Talk over it and it stops at once (barge-in, after `barge_in_ms` = 250 ms of
speech); what you say next is the new question, and Claude remembers only
the part of its answer you actually heard. Barge-in only counts once the
answer has started playing: anything you say while it is still thinking
(a last word, "and on Friday") never cancels it; it is asked as the next
question once the answer has played (but never while a yes/no question is
open: then only the answer after the question counts). Talking in the
short pause between two of its sentences doesn't count as a barge-in
either. The app's own sounds can't start or stop a turn. The conversation keeps going
turn after turn until you tap **End**, say "goodbye", or stay quiet for
`idle_timeout` (2 minutes); then it is summarized to memory like the voice
loop. Deletes still ask out loud. Let the question finish, then say
"yes" (or "yeah, delete it", "go ahead"); anything else, or talking over the
question, means no ("OK, I won't delete it"). The answer is never sent to
Claude as a new request.

- Buttons: **End**, **Mute** (stops listening; the reply still plays),
  **CC** (captions of what it heard and what it is saying, remembered on
  this phone) and a stopwatch for the latency panel (also on by default with
  `debug_panel = true`).
- Hearing itself: the page asks the browser for echo cancellation, noise
  suppression and auto gain, and needs a more confident detection while a
  reply plays. If it still interrupts itself on the speaker, raise
  `vad_threshold` (e.g. 0.7) or use earphones; in a car whose speakers it
  keeps hearing, `barge_in = "off"` makes replies play to the end (the mic
  is ignored while one plays, and listens again right after).
- Follow-ups: when a reply has finished playing it plays a soft low note and
  shows **Still listening…** (还在听…) with the mic meter lit for
  `follow_up_seconds` (5; `0` = no cue). Just ask the next thing. If you say
  nothing the label quietly goes back to **Listening**; the conversation
  stays open until `idle_timeout`. There is no cue after a yes/no question
  (it is waiting for your answer) or after you interrupted a reply.
- Voice detection runs on the phone: the Silero VAD neural model in
  WebAssembly (onnxruntime-web, served by the app itself from
  `assistant/web/vad/`, about 14 MB, cached after the first load). If it
  can't load, or with `vad = "energy"`, a simpler loudness-based detector is
  used. The latency panel says which one is active.
- Backgrounding the app, a phone call or Siri pauses the conversation (the
  current answer is cancelled); tap **Tap to continue** to go on. The screen
  stays awake while it is open, where the browser allows it.
- The connection is a WebSocket on the same address and sign-in as the rest
  of the page (Tailscale or the hosted setup work unchanged). With
  `--timings`, each turn's latency line is printed on the PC's console too.

**On an iPhone** (Safari, or the app added to the home screen):
1. Open the page, tap **Voice conversation**, and tap **Allow** when iOS
   asks for the microphone. That one tap also unlocks the speaker, so keep
   the ringer switch off silent mode if you hear nothing.
2. Talk normally and stop: the orb pulses while it thinks, then the answer
   plays from the loudspeaker (or AirPods). The orb moving with the reply
   means it is speaking; just talk to interrupt it.
3. **Mute** keeps the conversation open without listening (handy while
   someone else talks); **End** closes it.
4. If it says the microphone is unavailable, or never asks: in Safari tap
   **aA** -> **Website Settings** -> **Microphone** -> **Allow**; or go to
   iPhone **Settings -> Apps -> Safari -> Microphone** -> Ask or Allow. For
   the home-screen app, remove it and add it again after allowing. The page
   must be opened over `https://` (the Tailscale or hosted address), never
   `http://`.
5. Locking the phone, switching apps, a call or Siri pauses it; tap **Tap to
   continue** when you're back.

Other `[voice]` settings: `min_utterance_ms` (300, shorter sounds are
ignored), `max_utterance_s` (30), `max_session_minutes` (30) and
`utterances_per_minute` (30); a yes/no question waits `[webapp]
confirm_timeout`. Voice and push-to-talk share this phone's conversation.
Replies use the voice from [Voices](#voices) (Kokoro once downloaded),
rendered sentence by sentence so the first one plays while the rest are
made; the model is warmed up when the server starts.

## A Chinese instance for someone else (`config.mom.toml`)

A second, separate copy of the phone app in Mandarin Chinese, for a family
member to try. It runs on the same PC next to yours, on its own port, and
uses **none of your connections**: no Notion, no Pinecone memory or notes,
no dev team, its own history (`data/mom/`), sign-ins and log
(`logs/mom.log`). She connects her own Google Calendar and Gmail (read and
drafts only; nothing is ever sent) from her phone with a menu button.

`[assistant] language = "zh"` makes Claude answer in Simplified Chinese,
Whisper listen for Mandarin (the multilingual `small` model; `.en` models
can't hear Chinese), yes/no work in Chinese, and the page show in Chinese.
A Chinese yes is the whole answer, from a short list (是 / 是的 / 对 / 对的 /
好 / 好的 / 好啊 / 好吧 / 行 / 可以 / 确定 / 确认 / 没问题 / 删吧 / 删除吧 /
加吧, optionally after 嗯 / 哦 / 啊 / 呃, e.g. "嗯，好的" or "好，删吧"); any
other answer is a no, including sentences that merely contain 是 or 好
(还是明天吧, 是谁, 好，等一下), a lone 嗯, 不好 and questions such as 可以吗？. History screens stay in English.

Her app guides her itself: the sign-in screen asks for "the sign-in code you
were sent", and after signing in a card lists only the steps still left for
her (add the app to the home screen; connect Google, including what to tap
on Google's "unverified app" screen). Each step disappears once done, and
"知道了" (Got it) hides the card until she next opens the app. Steps before
she can reach the app (installing Tailscale, accepting your share, opening
the link) are yours to send her.

One-time setup on the PC (PowerShell, in the project folder):

1. **A Google OAuth client for the web** (your `credentials.json` is a
   Desktop client, which only allows localhost): console.cloud.google.com ->
   your project -> APIs & Services -> Credentials -> Create credentials ->
   OAuth client ID -> **Web application**. Authorized redirect URI:
   `https://<pc-name>.<tailnet>.ts.net:8443/google/callback`. Download the
   JSON as `data/mom/google_client.json`. On the OAuth consent screen ->
   Audience, add her Gmail address as a test user.
2. **Secrets**: create `.env.mom` with `ANTHROPIC_API_KEY=...` (your key is
   fine; `.env.mom` is gitignored). Her `WEB_TOKEN` is added to it on the
   first start.
3. **Voice**: `python -m assistant --download-voice zh_CN-huayan-medium`
   (Piper, into `voices/`). Without it the phone's own Chinese voice reads
   the replies. The Whisper `small` model downloads on the first request.
4. **Address**: create `config.mom.local.toml` (gitignored):
   ```toml
   [webapp]
   public_origin = "https://<pc-name>.<tailnet>.ts.net:8443"
   ```
5. **Run it from a clean shell** (a new PowerShell window that has not
   loaded your own `.env` or set any of your variables) and publish it on
   your tailnet. It refuses to start, naming the variables (never their
   values), if `GOOGLE_TOKEN_JSON`, `GOOGLE_CLIENT_JSON`, `GOOGLE_TOKEN`,
   `GOOGLE_CREDENTIALS`, `HISTORY_PATH`, `WEB_TOKEN`, `NOTION_TOKEN`,
   `NOTION_ASSIGNMENTS_DB`, `PINECONE_API_KEY`, `PINECONE_INDEX`, `HA_URL`
   or `HA_TOKEN` is set in the shell but not by `.env.mom`: `.env.mom`
   cannot override them, so her instance would use your Google account,
   history, token or smart home. The refusal is printed and also written
   to her log (`logs/mom.log`), since the sign-in task runs hidden. The
   same check runs whenever `[webapp] google_connect` is on. Your team chat
   tokens don't matter here: an instance started with an `--env-file`
   other than `.env` never turns team chat on.
   ```powershell
   python -m assistant --mode web --config config.mom.toml --env-file .env.mom --show-token
   tailscale serve --bg --https=8443 http://127.0.0.1:8766
   ```
   Share the PC with her (Tailscale admin console -> Machines -> the PC ->
   Share), so her phone's Tailscale can open that address and nothing else.
6. **On her phone**: open the address, enter the token once, Share -> Add to
   Home Screen. Then ⚙ -> **连接 Google 账户**, sign in, allow Calendar and
   Gmail. Google shows "Google hasn't verified this app" while the consent
   screen is in Testing: tap Advanced -> continue. She lands back in the app
   and can ask "我明天有什么安排？".

While the consent screen is in Testing, Google expires the sign-in after 7
days; she taps the button again (it then reads 重新连接 Google 账户). To
disconnect her account, delete `data/mom/google_token.json`, restart, and
remove the app at myaccount.google.com -> Security -> Third-party access.
`[webapp] google_connect` and `public_origin` are off and empty in your own
`config.toml`, so your instance is unchanged.


**Start them automatically.** Each family copy (any `config.<name>.toml`
with its `.env.<name>`, e.g. Mom's and Kai's) can start by itself when you
sign in to Windows and restart after a crash:

```powershell
.\scripts\install_family_tasks.ps1            # once: installs \HeyClaude\Family-<name> tasks
.\scripts\install_family_tasks.ps1 -Restart   # after updating the code
.\scripts\install_family_tasks.ps1 -Uninstall
```

A copy already running in a window is stopped first (you can then close the
window). Your own app is `install_windows_service.ps1`'s job and isn't touched.

## Invite family (cloud)

On the Render server you can give someone their own account on your
assistant, with their own conversation history and language (English or
Chinese), without sharing `WEB_TOKEN`. The server profile turns this on
(`[family] enabled`); on a PC it is off unless you set it.

Invites are managed from the cloud server's own address: open
`https://<cloud>/` (e.g. `https://hey-claude.onrender.com/`) directly. With
failover the app talks to whichever backend is active, normally your Pi or
PC, where `[family]` is off, so the **Invite someone** menu only shows
while the cloud is the active backend.

1. Signed in with your token at the cloud's address, open ⚙ -> **Invite
   someone**, enter their
   name and language (and their time zone, e.g. `Asia/Shanghai`), then
   **Create invite link**. The phone's share sheet opens (or the link is
   copied). Send it to them however you like. The link points at the
   server itself (`[webapp] public_origin`, else with failover the cloud's
   address, `APP_ORIGIN`).
2. They open the link on their phone: it signs that phone in as them and
   reloads the page in their language. Share -> Add to Home Screen.
3. The link works once and for `invite_days` (30 days). If it expired, or
   they get a new phone, tap **New link** next to their name (the old link
   stops working).
4. **Remove** (also at the cloud's address) signs them out everywhere and
   deletes their conversations (if a file can't be deleted right away, it
   is retried later).

What they get: chat and voice with Claude, web search, their own
history (in `<dir>/<id>/history.sqlite`, `/data/users` on Render, never
synced to your Pi or PC), and everything in their language: replies, the
assistant's own sentences ("Please say yes or no", "I didn't catch that")
and, for Chinese, speech recognition with the multilingual Whisper model
(`zh_stt_model`, `small`) and the Mandarin Piper voice (`zh_piper_voice`).
Those load the first time a Chinese-speaking person talks and are shared by
everyone who speaks Chinese; English speakers keep the server's own.

**Their own Google.** Once it is set up (below), they tap ⚙ -> **Connect
Google account** on their own phone and sign in to *their* Google account.
Then they can ask about their calendar (add, move, delete; deletes ask
first) and their Gmail (read, and draft replies, never send), exactly like
yours. Their token is kept in their own folder
(`<dir>/<id>/google_token.json`), is never mixed with yours (your Google on
the server stays `GOOGLE_TOKEN_JSON`), and **Disconnect Google account**
or removing them deletes it. One-time setup for you:

1. Google Cloud console, the same project as your own sign-in -> APIs &
   Services -> Credentials -> **Create credentials -> OAuth client ID ->
   Web application**. Authorized redirect URI:
   `https://<cloud>/google/callback` (e.g.
   `https://hey-claude.onrender.com/google/callback`). Download its JSON.
2. In Render -> hey-claude -> Environment, set `PUBLIC_ORIGIN` to
   `https://<cloud>` and paste that JSON's whole contents into
   `GOOGLE_WEB_CLIENT_JSON`, then Save.
3. The app must be **published** (step 3 of "Run it in the cloud" below):
   in Testing mode only listed test users can sign in, and only for 7 days.
   They see Google's "unverified app" warning once; their guide card tells
   them to tap Advanced -> Go to ... (unsafe).

Without step 2 everything else works and their Connect button stays hidden.

What they never get: your Notion, Pinecone memory and notes, your Google
Calendar and Gmail, the dev team, the lecture recorder and tutor, your
history, failover, or the invite menu. Their **Sign out everywhere** signs
out only their own phones; yours signs out only yours. Settings: `[family]`
in `config.toml` (`dir`, `max_users`, `invite_days`, `google`,
`google_client`, `zh_stt_model`, `zh_piper_voice`).

## Run it in the cloud (Render)

The phone web app can also run 24/7 on a small paid server at
[Render](https://render.com), so it works from your iPhone with the PC off.
The repo has everything: a `Dockerfile` (Whisper and the Piper voice are
baked into the image, so a restart downloads nothing) and a `render.yaml`
Blueprint with a `hey-claude` service. In the cloud it runs with
`ASSISTANT_PROFILE=server`: no mic, speakers or wake word, Piper voice (or
espeak-ng if the voice is missing), no "open in browser" tool, and logs go
to Render's log viewer. Secrets live only in Render's settings, never in the
image or on its disk. It is on the public internet, so it is hardened
(SECURITY.md, "Cloud server"): only the login page, `/healthz` and the app
icon work without the token, and wrong tokens slow the sender down and lock
it out (the right token always works).

Cost: about $25.25 a month: the Standard plan (2 GB RAM, $25) plus a 1 GB
persistent disk ($0.25/GB). Speech recognition with
Whisper `base.en` plus the Piper voice was measured at up to 564 MB, which
doesn't fit the $7 Starter plan's 512 MB (PROGRESS.md, "Cloud hosting").
For Chinese-speaking family members the image also holds Whisper `small`
(about 480 MB) and the Mandarin Piper voice (about 60 MB); they load into
memory only once one of them talks (estimated at 0.5-0.7 GB more, not yet
measured on Render), which the Standard plan has room for. Build with
`--build-arg FAMILY_ZH=0` for a smaller image without them.
Render's free Hobby workspace is enough; you pay only for the service. To
save money you can switch the service to Starter and set `WHISPER_MODEL` to
`tiny.en` (peaks around 470 MB: it fits, with little room to spare, and
mishears more often).

**Persistent disk.** The service has a 1 GB disk mounted at `/data`, and
the image sets `DATA_DIR=/data` and `HISTORY_PATH=/data/history.sqlite` for
the conversation-history and lecture features. Until those are merged
nothing is written there yet: conversations live in memory and end with
each deploy (long-term memory in Pinecone is unaffected). Everything outside
`/data` is wiped on each deploy. Render's free instance type can't have a disk, which is one more
reason the service is on a paid plan. Render takes daily snapshots of the
disk, but that is the only backup: export your history now and then with
the history export command (it arrives with the conversation-history
feature) and keep the file somewhere else. With a disk, Render runs exactly
one instance and a deploy has a short gap of downtime.

1. **Push** this branch to GitHub (merged into master).
2. **Create the service.** On render.com: sign up (GitHub login), add a
   payment card, then **New -> Blueprint**, pick this repository and the
   `master` branch. Render reads `render.yaml` and shows two services,
   `hey-claude` and `pinecone-mcp`. It asks for each secret marked
   `sync: false`; paste them in yourself (they are never in the repo):
   - `ANTHROPIC_API_KEY`, `PINECONE_API_KEY`, `NOTION_TOKEN`: the same values
     as in your `.env`.
   - `GOOGLE_TOKEN_JSON`: open `token.json` (in the project folder on the PC)
     in Notepad, select all, copy, paste. The whole file, braces included.
   - `GOOGLE_CLIENT_JSON`: the same for `credentials.json`.
   - `ASSISTANT_TIMEZONE`: your time zone, e.g. `America/New_York`, so
     "tomorrow" means your tomorrow (the server runs on UTC).
   - `WEB_TOKEN` is generated by Render; leave it.
   - `PUBLIC_ORIGIN`, `GOOGLE_WEB_CLIENT_JSON`: only for family members'
     own Google (README "Invite family"); leave them empty for now if you
     like.
   Click **Apply**. The first build takes about 10 minutes.
3. **Publish the Google app.** Google Cloud console -> APIs & Services ->
   OAuth consent screen (Google Auth Platform -> Audience) -> **Publish
   app**. In "Testing" mode Google expires refresh tokens after 7 days, which
   would break the calendar every week. Google then shows an "unverified
   app" warning when you sign in: that is expected for a personal app (click
   Advanced -> Go to Hey Claude). After publishing, sign in once more on the
   PC so the token is a long-lived one: delete `token.json`, run
   `python -m assistant.tools.calendar`, and paste the new `token.json` into
   `GOOGLE_TOKEN_JSON` in Render (Environment -> Save; it restarts).
4. **Check it.** In Render -> hey-claude -> Shell, run
   `python -m assistant --check` (no mic or speaker checks on a server).
   The Logs tab should show "Speech models loaded".
5. **On the iPhone.** Render -> hey-claude -> Environment -> `WEB_TOKEN` ->
   reveal and copy it (e.g. AirDrop or a note to yourself). Open
   `https://hey-claude.onrender.com` (the address at the top of the service
   page; Render may add a suffix) in **Safari**, paste the token once,
   allow the microphone, then Share -> **Add to Home Screen**. Open it from
   the home screen from now on; it stays signed in (90 days, renewed
   each time you use it).

If Google stops working (Render logs say "Google Calendar sign-in could not
be refreshed"), sign in again on the PC as in step 3 and paste the new
`token.json`. To sign every phone out, change `WEB_TOKEN` in Render (any 32+
random characters) and save. `WHISPER_MODEL` picks the speech model:
`base.en` (default, more accurate) or `tiny.en` (smaller, faster, more
mistakes). Run the same image anywhere with Docker:
`docker build -t hey-claude .` then `docker run -p 10000:10000 -e WEB_TOKEN=...
-e ANTHROPIC_API_KEY=... hey-claude` (it expects a TLS proxy in front, and
trusts forwarded headers only from 10.x addresses, Render's; set
`CLOUD_TRUSTED_PROXIES` to your proxy's network, e.g. `172.18.0.0/16`, or to
nothing). After the first Render deploy, the log's first five "Proxy check"
lines show what Render's proxy sends; see BLOCKERS.md.

## Email, notes, lectures and homework help

### Google Calendar by voice
Beyond "what's on my calendar", the calendar tools cover most of what the
Google Calendar app does:

- **Colors and labels.** "Make it red", "Color the lab meeting Basil",
  "Label it School". Google's 11 event colors work by name (Lavender, Sage,
  Grape, Flamingo, Banana, Tangerine, Peacock, Graphite, Blueberry, Basil,
  Tomato) or plain words like red or light blue. The named **event labels**
  you made in Google Calendar work by their names too. Labels are read
  from Google; create and rename them in the Google Calendar app.
  `[calendar] color_labels` in `config.toml` adds your own words, e.g.
  `color_labels = { Homework = "School", Gym = "Tangerine" }`.
- **Repeating events.** "Add gym every Tuesday and Thursday at 7 AM for
  ten weeks". To move, change or delete a repeating event, say whether you
  mean just this one or all of them; deleting a whole series asks "delete
  every ...?" first. Moving a weekly series to another weekday works; a
  series on several weekdays or fixed dates of the month can only change
  its time (otherwise it offers to make a new series).
- **Reminders, busy/free and privacy.** "Remind me 10 minutes before and
  at the start", "no reminders", "show me as free", "make it private".
- **Guests and Meet.** "Invite ana@uni.edu", "Add a Meet link". Google
  emails an invite, so adding or removing guests always asks first and
  names every address. Moving or deleting an event doesn't ask Google to
  email its guests; tell them yourself, or do it in Google Calendar.
- **Invites.** "Accept the project sync invite", "Tell them maybe". This
  always asks first, and reads out any note it would send.
- **Free time and search.** "When am I free for an hour tomorrow
  afternoon?", "When is my next dentist appointment?"
- **Other calendars.** "What's on my Classes calendar?", "What's on all my
  calendars this week?" (up to 10 calendars), "Which calendars do I have?"

Labels and other calendars need two read-only permissions that a
`token.json` from before this feature doesn't have. Everything else keeps
working without them; to add them, sign in once more (the startup line and
`--check` remind you):

```powershell
python -m assistant.tools.google_auth            # or add --headless without a browser
```

This needs `google-api-python-client` 2.199 or newer (event labels); the
automatic updater doesn't install new requirements, so run
`pip install -r requirements.txt` on the PC and the Pi after updating.

Not covered: focus time, out of office and working location (Google only
allows them on work or school accounts), file attachments, and Google's
"quick add" text box. Creating, renaming or deleting labels and calendars
is left to the Google Calendar app.

### Email triage by voice (Gmail)
"Anything important in my email?", "Read the one from Professor Smith",
"Reply that I'll add error bars." The assistant lists unread or important
mail from the last day (sender, subject, a one-line gist, at most
`[gmail] max_summary` messages), reads one message in plain text, and saves
a reply as a **Gmail draft** after you say yes. You review and send drafts
in Gmail yourself. The sign-in asks for `gmail.readonly` and
`gmail.compose`. Google has no drafts-only permission, and `gmail.compose`
would also allow sending, so the code blocks it instead: there is no send
tool, and the Gmail connection refuses any send (or delete, or settings)
call. Gmail access is requested only while `[gmail] enabled = true`; with
it off, sign-in asks for Calendar alone.

Gmail uses the same Google OAuth client as Calendar, but a `token.json`
from before this feature only has the Calendar permission. Sign in once
more so Google can ask for the new ones (Calendar keeps working until you
do; the startup line and `--check` remind you):

```powershell
python -m assistant.tools.google_auth            # or add --headless without a browser
```

In the Google Cloud console the Gmail API must be enabled for the
"Hey Claude" project (APIs & Services -> Library -> Gmail API -> Enable).
For a cloud host, put the whole contents of `token.json` in the
`GOOGLE_TOKEN_JSON` secret instead of copying the file.

Email is written by other people, so it is treated like web pages: while
an email is in the conversation, anything that changes something
(calendar, Notion, notes, drafts) asks for a spoken yes first, and email
text never goes into the log file (see SECURITY.md).

### Quick notes
"Remember that the lab report needs error bars" saves a short note in
Pinecone (your `voice-assistant` namespace, tagged as a note);
"What did I need for the lab report?" finds it by meaning. You can also ask
for your recent notes or to delete one (it asks first). With conversation
history on, a saved note also shows up in that conversation. Notes need
`[memory]` (Pinecone); `[notes] enabled` turns them off.

### Review what it remembers
"What did you remember about me this week?" gives a short spoken summary of
this week's conversation summaries and notes ("...last week?" works too).
"Forget the one about cilantro" deletes it after a spoken yes (the same
`delete_note` as for notes, which now also takes conversation summaries).

In the phone app, **Memory** in the settings menu lists everything the
assistant saved in Pinecone, newest first and grouped by week, with the
date, whether it is a conversation summary or a note, and an **Untrusted**
badge on ones saved while a web page or email was in the conversation.
Delete removes one; "Delete all from this week" removes that week's notes
(both ask first). Only what the assistant wrote itself (ids starting with
`voice-` or `note-` in `[memory] namespace`) is listed or deleted; your
other Pinecone notes are never touched. Old vectors without a date show
under "Date unknown". It is the owner's only: family members have no memory
and don't see it.

Optional weekly nudge: with `[memory] weekly_review = true`, once a week
the first conversation that has notes from this week starts with "I saved N
notes this week, want to review them?". Off by default; the time of the
last offer is kept in `[memory] review_state_file`
(`data/memory_review.json`). With failover, each backend keeps its own, so
you may hear it once per backend.

### Record a lecture (phone app)
In the phone app, open the **Lecture** tab, pick the course (the options
come from the Course property of your Notion Assignments database), and
tap Start. Ask the professor first: the screen shows a reminder. The page
keeps the screen awake, shows the elapsed time, and uploads the audio every
10 seconds in small pieces; if the network drops, it keeps recording and
resends from where the PC left off, so a 75-minute lecture survives Wi-Fi
blips (keep the app open in the foreground). Tap Stop when the lecture ends.

The PC then transcribes the recording with faster-whisper (`[lecture]
model`: "base.en", or "tiny.en" for speed; progress shows in the app),
asks Claude for notes (Summary, Key concepts, Equations in LaTeX, Action
items, Questions to ask), and creates a page in a **Lecture Notes**
database inside your Fall 2026 Assignments page, with Title, Course, Date
and Duration. The database is created automatically the first time (with
the same Course options); if the integration isn't allowed to, the job says
so and BLOCKERS.md has the two-minute fix. Action items that look like
assignments are listed under the lecture with an **Add** button; nothing is
added to Assignments unless you tap it. A failed job can be retried without
transcribing again.

With conversation history on, the summary and the Notion link are also
added to the phone conversation you were in when you started recording.
With failover (FAILOVER.md) a recording stays on the backend it started
on; if that backend goes away mid-lecture, the page says the recording
stopped there.

The audio is deleted as soon as it is transcribed (`[lecture] keep_audio =
true` keeps it), and the transcript and notes once the Notion page exists. Recordings
and job state live in `[lecture] upload_dir` (`data/lectures`; point it at
the `/data` disk on a cloud host). Other settings: `max_upload_mb` (300 per
recording), `chunk_mb` (4), `notes_database_id` (use an existing database
instead of finding/creating one).

### Homework tutor (phone app)
In the **Tutor** tab, take or choose a photo of a problem, add a question
("I got x = -3, where did I go wrong?") or just tap Ask. Claude looks at
the photo and tutors Socratically: hints and questions, checking your
reasoning step by step. It gives the full worked solution only when you tick
"Show the full solution" or ask for it. Equations are drawn with KaTeX
(bundled with the app, no CDN); with "Read explanations aloud" on, the
reply is spoken with the maths read naturally ("x squared over 2"). The
phone shrinks photos to 1600 px before uploading; the PC accepts only JPEG,
PNG or WebP up to `[tutor] max_image_mb` (8) and removes the photo's
metadata (EXIF, including location) before sending it to Claude.

## Smart home (Home Assistant)

"Turn off the bedroom lights", "set the living room light to 50%", "is the
front door locked?", "set the thermostat to 70", "turn on the fan". Off
by default; it needs a running [Home Assistant](https://www.home-assistant.io/)
on your network:

1. In Home Assistant, create a dedicated user for the assistant
   (Settings -> People -> Add person, allow login, **not** an
   administrator), sign in as that user, then profile -> Security ->
   Long-lived access tokens -> Create token. Copy it once. A non-admin
   token can still switch devices but can't change Home Assistant's
   settings; it can't read rooms, so the assistant then matches rooms by
   device name ("Bedroom Lamp").
2. In `.env`: `HA_URL=http://homeassistant.local:8123` (your address) and
   `HA_TOKEN=<the token>`. The token is masked in the log.
3. In `config.local.toml`:

```toml
[home_assistant]
enabled = true
```

Restart; the startup line says so if the URL or token is missing.
Claude gets three tools: `ha_list` (devices with their state, by room or
kind, cached for `[home_assistant] cache_seconds`), `ha_state` (one
device) and `ha_control`. Spoken names are matched to Home Assistant's
friendly names and areas, loosely ("kitchen lite" still finds the
kitchen light); if two devices fit ("the lamp"), it asks which one.

Safety, in code rather than in the prompt:
- Only the kinds of device in `[home_assistant] domains` (lights,
  switches, fans, climate, covers, media players, sensors and locks by
  default) are listed or touched, and only a fixed set of actions is
  called (on, off, brightness, colour temperature, thermostat, fan speed,
  open/close, play/pause/volume, lock/unlock). There is no way to call
  any other service: no restart, no arbitrary scripts. A script, scene or
  automation runs only if you list its entity id in `[home_assistant]
  scripts`.
- Locks, alarm panels, garage doors and gates (by Home Assistant's device
  class, or a switch or cover with garage, gate, door or lock in its name,
  even run together as in "Garagedoor", but not indoor, outdoor, doorbell
  or clock; deadbolt; in Chinese 车库, 锁, 门禁, 大门, 闸门 or 门 ending
  the name or a word), plus anything in `[home_assistant]
  secure_entities` (put a relay there if its name says none of these: a
  room called "Garage" doesn't count, nor non-English words like "Tor"), always ask for a spoken yes first, are never switched
  for a whole room at once, and are only found by their exact name or all
  of its words (never by a similar spelling).
- Unlocking or opening one of those is refused outright while a web page
  or email is in the conversation (start a new one). Other changes ask
  first in that case, as everywhere else.
- Home Assistant is Kyle's alone: family members never get these tools.
  The Render cloud host can't reach a home network anyway.

**Plain http.** Home Assistant usually serves http on the LAN, so the
token travels unencrypted. That is fine inside your home network or over
Tailscale; anywhere else use https (Nabu Casa or your own certificate).
The assistant warns at startup when `HA_URL` is a plain http address
outside the home network.

## Team chat (Slack, Discord, GitHub)

"What happened in #eng today?", "summarize the release thread", "any
decisions about the launch in Discord this week?", "what's new in the
issues on hey-claude?", "read the spec file Bob shared". Claude reads the
channel, thread, search results or a shared text file and sums up the
ideas, decisions, open questions and files. It never posts: there is no
tool that writes to Slack, Discord or GitHub. "Save that as a digest"
files Claude's summary as a page in your Notion (a "Chat Digests"
database it creates beside Assignments, or `[teamchat]
digest_database_id`), after a spoken yes, once per service, channel and
day (asking again that day finds the same page and adds nothing). Off by
default; owner only (family members never get these tools).

LinkedIn isn't here: LinkedIn doesn't allow apps to read your messages or
feed.

Set up the services you use (each token is read only and goes in `.env`):

1. **Slack** (a user token, so it reads what you can read): at
   api.slack.com/apps, Create New App -> From scratch, in your team's
   workspace. Under OAuth & Permissions -> User Token Scopes add
   `channels:read`, `channels:history`, `groups:read`, `groups:history`,
   `users:read`, `files:read` and `search:read` (no direct-message
   scopes: DMs are never passed to Claude). Install to Workspace, then copy the
   User OAuth Token (`xoxp-...`) into `.env` as `SLACK_USER_TOKEN=`.
   **Never turn on Manage Distribution:** a distributed app that isn't in
   the Slack Marketplace is limited to one history read a minute.
2. **Discord** (a bot that a server admin adds): at
   discord.com/developers/applications, New Application -> Bot -> Reset
   Token, and copy it into `.env` as `DISCORD_BOT_TOKEN=`. On the same
   page turn **on** Message Content Intent (without it Discord sends
   messages with no text; the assistant says so) and turn **off** Public
   Bot. Under OAuth2 -> URL Generator tick `bot` with View Channels and
   Read Message History (permissions=66560) and have a server admin open
   that link to add the bot.
3. **GitHub**: github.com/settings/personal-access-tokens -> Generate new
   token (fine-grained), only the repositories you want, with read-only
   Issues, Pull requests, Discussions and Metadata. Put it in `.env` as
   `GITHUB_TOKEN=`.
4. In `config.local.toml`, turn it on with the services you set up:

```toml
[teamchat]
enabled = true
slack = true
discord = false
github = true
channels = ["#eng", "general"]
github_repos = ["kylechen839-max/Claude-Personal-Assistant"]
```

Restart; the startup line names any turned-on service whose token is
missing. Claude gets `chat_channels`, `chat_read` (a channel or one
thread, the last 24 hours by default, or a day such as "yesterday"),
`chat_search`, `chat_files`, `chat_read_file` and, with Notion set up,
`chat_save_digest`.

Limits, in code:
- `[teamchat] channels` lists the Slack and Discord channels it may read
  (by name or id, Discord also as server/channel); `[]` means every
  channel the token can see. `[teamchat] github_repos` does the same for
  GitHub. Slack DMs and group DMs are never passed to Claude or logged
  (Slack's search and file list return some; they are dropped). A search
  that had to be cut (Discord: 20 channels in each of 3 servers; GitHub:
  5 repos) says so; name a channel or repo to search it.
- At most `[teamchat] max_messages` (100) messages per read, 300
  characters of each, about 12,000 characters per answer, 20 files per
  list, and only text files up to 50 KB, downloaded only from Slack's or
  Discord's own file servers (the Slack token goes to files.slack.com
  only; Discord's file links need none).
- Everything read is other people's text, so once Claude has read chat,
  any tool that changes something asks you first. Message text, file
  text and tokens never go in the log.
- After a refused token it stops calling that service until restart, and
  when a service asks it to slow down it waits that long without calling
  (Discord bans addresses that make too many bad requests).
- `[teamchat] timeout` is the seconds per request.

## Spotify by voice

"Play my study playlist", "play some lo-fi", "pause", "skip", "go back",
"what's playing?", "turn it up", "play it on my phone". The assistant
remote-controls a Spotify app that is already open somewhere (the PC's
desktop app, your phone, a speaker) through the Spotify Web API; it plays
no audio itself. Your own playlists are searched first, then Spotify's
catalog (a mood or genre plays a playlist, a song name plays that song).

- **Spotify Premium is required.** Spotify only lets apps control playback
  on Premium accounts; without it every command answers "Spotify only lets
  apps control playback with a Premium account."
- **Spotify has to be open on a device.** With nothing playing anywhere it
  uses this computer's Spotify if it is open, or the only device that is;
  with several it asks which one.
- **Nothing changes your library.** Sign-in asks only for
  user-read-playback-state, user-modify-playback-state and
  playlist-read-private: no tool can edit
  playlists, saved songs or follows. Playback control never asks for a
  yes (it is harmless and easy to undo).
- **Pauses while you talk.** With `[spotify] pause_while_listening = true`
  (the default) the wake word pauses Spotify if it is playing on this
  computer, and it resumes when the assistant goes back to waiting for
  "Hey Claude" (not if you said "pause" meanwhile). This computer is the
  Spotify device whose name is `[spotify] local_device`, or the computer's
  hostname when that is empty. Only the wake-word mode does this; the phone
  app plays its replies on the phone.
- Family members (cloud invites) never get Spotify: not in this version.
  Spotify is meant for the PC or Pi (wake word and its phone web app);
  the cloud server's sign-in wall doesn't pass Spotify's redirect back, so
  connect it on the PC.

Setup, once (about 5 minutes):

1. At developer.spotify.com -> Dashboard -> Create app (any name, "Web
   API"). Under Redirect URIs add exactly `http://127.0.0.1:8888/callback`
   (the `[spotify] redirect_uri` default), and, for the phone app's button,
   `<public_origin>/spotify/callback` too (e.g.
   `https://your-pc.your-tailnet.ts.net/spotify/callback`). In Development
   mode, add your Spotify account's email under User Management.
2. Copy the app's **Client ID** into `.env` as `SPOTIFY_CLIENT_ID=...`. No
   client secret is used (the sign-in is PKCE).
3. Turn it on in `config.local.toml`:

   ```toml
   [spotify]
   enabled = true
   ```
4. Sign in, either on the PC or Pi:

   ```powershell
   python -m assistant.tools.spotify
   ```

   It prints a link (and opens it); after you allow access the browser
   lands on a 127.0.0.1 address. If that page doesn't load (for example
   the link was opened on another device), that's fine: copy the full
   address from the address bar and paste it back. Or, in the phone app,
   gear -> **Connect Spotify** (owner only; needs `[webapp] public_origin`).
5. Restart the assistant. The sign-in is saved privately in
   `[spotify] token` (`data/spotify_token.json`); delete that file to
   disconnect, and remove the app's access at spotify.com -> Account ->
   Manage apps.

## Raspberry Pi (Pi 4/5, 64-bit Raspberry Pi OS Bookworm)

The code is the same as on Windows; two scripts do the Pi-specific setup.

### 1. Install

```bash
git clone <this repo> hey-claude && cd hey-claude
./scripts/install_pi.sh
```

The script is safe to re-run. It apt-installs the system packages
(`python3-venv python3-dev libportaudio2 espeak-ng libespeak1 alsa-utils`),
creates `.venv`, installs `requirements.txt` plus openWakeWord (with
`--no-deps`, because its Linux metadata wants `tflite-runtime`, which has no
wheels for newer Pythons; only the ONNX backend is used), creates `.env` from
`.env.example` if it doesn't exist, and pre-downloads the Whisper and
wake-word models. `SKIP_APT=1`, `SKIP_MODELS=1` and `PYTHON=python3.11`
change those steps.

### 2. Copy your keys and files from the PC

From the project folder on your PC (PowerShell; replace `raspberrypi` with
the Pi's host name or IP):

```powershell
scp .env credentials.json token.json pi@raspberrypi:~/hey-claude/
scp -r voices pi@raspberrypi:~/hey-claude/   # the Piper voice, if you have it
```

Or type the keys into `nano .env` on the Pi. The "Hey Claude" wake-word
model (`keywords/hey_claude.onnx`) is in the repo and works on both
(openWakeWord models are not platform-specific), so there's nothing to copy
for it.

**Google sign-in without a browser:** if you don't copy `token.json`, run
`.venv/bin/python -m assistant.tools.calendar --headless` on the Pi. Open the
printed link on your phone or PC, sign in, then paste back the
`localhost:8765/...` address the browser ends up on (the page itself will
fail to load; that's expected). This needs `credentials.json` on the Pi.

### 3. Check, try, and run at boot

```bash
.venv/bin/python -m assistant --check   # keys set?, files, mic/speaker, models
.venv/bin/python -m assistant           # try it; Ctrl+C to stop
./scripts/install_service.sh            # start at boot (systemd)
```

`--check` prints which keys are set (never their values), whether
`credentials.json`, `token.json` and the wake-word model exist, the audio
devices with their index numbers, whether the Whisper model is cached, and,
which voice will speak (Kokoro, Piper or pyttsx3).
It exits with 1 if something required is missing.

`install_service.sh` installs `deploy/hey-claude.service` as a systemd
**user** service (the Pi's sound server runs in your user session, which a
system service can't reach) and turns on "lingering" so it starts at boot
without logging in. It waits for the network, restarts on failure, and logs
to the journal as well as `logs/assistant.log`:

```bash
systemctl --user status hey-claude
journalctl --user -u hey-claude -f
systemctl --user restart hey-claude     # after changing .env or config.toml
./scripts/install_service.sh --uninstall
```

If the Pi also serves the phone web app, `./scripts/install_watch.sh` adds
the watchdog (see "Health checks and safe updates"); if it runs only the
wake word, set `[watch] enabled = false` in `config.local.toml` there.

**Voice:** Piper sounds much better than eSpeak and runs well on a Pi 4/5
(the `piper-tts` wheel for 64-bit ARM ships its own espeak-ng data). Run
`.venv/bin/python -m assistant --download-voice en_US-lessac-medium` on
the Pi (or copy `voices/` from your PC); `[tts] engine = "auto"` then uses
it (see [Voices](#voices)). Kokoro also installs on a Pi but takes well over
a second per sentence there, so "auto" keeps Piper on ARM. Without a voice
file the assistant falls back to eSpeak.

**Speed:** set `[stt] model = "tiny.en"` in `config.toml` if `base.en` feels
slow. On a Pi with no screen, set `[web] browser = false`.

### Troubleshooting

- **No mic found** (`--check` shows "Input devices: none found"): plug the
  USB mic in before booting or re-run `--check`; `arecord -l` should list
  it. If it's missing there too, it's a hardware/driver problem, not the app.
- **Wrong mic or speaker:** `.venv/bin/python -m sounddevice` (or `--check`)
  lists devices as `[index] name`. Put the index, or part of the name, in
  `config.toml`:
  ```toml
  [wake]
  mic_device = "1"        # or "USB PnP"
  ```
  For the speaker, pick the default output with `sudo raspi-config` ->
  System Options -> Audio, and test it with `speaker-test -t wav -c 2`.
- **ALSA errors** (`ALSA lib pcm.c: ... Unknown PCM cards.pcm.rear` and
  similar) are printed by PortAudio while it probes devices and are usually
  harmless. If audio really fails: check `arecord -l` / `aplay -l`, make sure
  your user is in the `audio` group (`groups`; fix with
  `sudo usermod -aG audio $USER` and log in again), and that nothing else
  holds the device (`fuser -v /dev/snd/*`).
- **`PortAudio library not found`:** `sudo apt install libportaudio2`.
- **No voice from pyttsx3:** `sudo apt install espeak-ng libespeak1`.
- **Service keeps restarting / stopped:** `journalctl --user -u hey-claude -n 50`
  shows why (most often a missing `ANTHROPIC_API_KEY` in `.env`). After 5
  failures in 5 minutes it stops; fix the cause, then
  `systemctl --user restart hey-claude`.
- **Service doesn't start at boot:** `loginctl show-user $USER -p Linger`
  must say `Linger=yes` (`sudo loginctl enable-linger $USER`).

## Phone app failover: Pi, then PC, then cloud

The phone app (installed from the cloud host) uses the Raspberry Pi when it
can reach it, the PC when it can't, and the cloud host when neither
answers, and moves back up when a better one returns (never mid-reply).
FAILOVER.md has the whole protocol. To set it up:

1. Run the web app on each machine you want in the list, with the same
   `WEB_TOKEN` in every `.env` (or the Render environment), and expose the Pi
   and PC with `tailscale serve`.
2. In each machine's `config.toml`, fill in `[failover]`: `role` (or pass
   `--role pi` / `--role pc` / `--role cloud`), `app_origin` (the cloud
   URL the phone app is installed from) and the three `backends` URLs:
   ```toml
   [failover]
   role = "pi"
   app_origin = "https://hey-claude.onrender.com"
   backends = [
     { role = "pi", url = "https://pi.tail1234.ts.net" },
     { role = "pc", url = "https://kyle-pc.tail1234.ts.net" },
     { role = "cloud", url = "https://hey-claude.onrender.com" },
   ]
   ```
3. Copy the same `.env`, `credentials.json` and `token.json` to each machine
   (one Google sign-in works on all of them; see FAILOVER.md section 6).
4. On the phone, open the app's Settings -> Backends to check the URLs and
   the badge (Pi / PC / Cloud), or force one backend.

Conversation history is synced between the machines every
`sync_interval` seconds (30); memory is shared through Pinecone as before.
Tests: `pytest` (server side) and `node --test tests/js/*.test.mjs` (the
router; Node 18+).

## Windows PC as the always-on home host

No Pi needed if the PC stays on (see "Do I need a Pi?" in
[PI_MIGRATION.md](PI_MIGRATION.md) for the trade-offs). In PowerShell, in the
project folder:

```powershell
.\scripts\install_windows_service.ps1             # install and start
.\scripts\install_windows_service.ps1 -NoWeb      # wake word only, no phone app
.\scripts\install_windows_service.ps1 -PowerOnly  # just the power check
.\scripts\install_windows_service.ps1 -Uninstall  # stop and remove
```

It registers two Task Scheduler tasks under `HeyClaude` (no admin rights
needed): **Wake** (`--mode wake`) and **Web** (`--mode web` on
127.0.0.1:8765, the address `tailscale serve` points at; `-Port` changes it).
They start 20 s after you sign in, run hidden, and are kept running by
`python -m assistant.host`, which waits for the network, restarts the
assistant 10 s after a crash (5 minutes after 5 crashes in 5 minutes) and
logs to `logs/host-wake.log` / `logs/host-web.log` (the assistant's own log
is still `logs/assistant.log`). After a reboot they start when you sign in;
for no sign-in at all, turn on automatic sign-in for your account.

**Already have a hand-made task for the web app** (e.g. "Hey Claude Web" in
the Task Scheduler Library, running `python -m assistant --mode web --host
127.0.0.1 --port 8765`)? The script finds it, shows its command and asks
before replacing it with `\HeyClaude\Web` (type `y`), so two servers never
fight over port 8765. Your `tailscale serve` setup keeps working unchanged
because the port is the same. If you answer no, only the wake-word task is
installed. If something else holds the port, it says what and skips the web
task. `-Uninstall` removes only the `HeyClaude` tasks and leaves `tailscale
serve` and the power settings as they are.

Then it shows the power plan and offers, after you type `y`, to set **never
sleep** and **never hibernate** when plugged in and to turn off **USB
selective suspend** (which can power down a USB mic). The display may still
turn off. The preflight's `power` row warns if sleep comes back (Windows
updates sometimes reset power plans).

### Automatic updates

**Off by default, and it stays off until you set up signing (below).** With
it on, changes reach the PC (and the phone app it serves) or the Pi by
themselves. Turn it on in `config.local.toml`, a gitignored file next to
`config.toml` for this machine's own settings (so `config.toml` stays
unchanged and never blocks an update):

```toml
[update]
enabled = true
```

What it does: the host supervisor (`python -m assistant.host`, which the
`HeyClaude` tasks and the Pi's `hey-claude` service run) checks every
`check_minutes` (15) for a new commit on the `stable` branch. Only the
**promote-stable** workflow moves `stable`, and only to a master commit on
which both `tests` and `pi-arm64` passed. Before any new code runs here:

- `origin` must be this GitHub repository (`[update] repo`);
- the new commit, or a `stable-<sha>` tag on it, must carry a valid SSH
  signature from a key in your **allowed_signers** file, which lives outside
  the checkout so an update can't change whom you trust:
  `%APPDATA%\HeyClaude\allowed_signers` on Windows,
  `~/.config/hey-claude/allowed_signers` on Linux/the Pi. No file, no
  valid signature: no update, and `logs/host-<mode>.log` says why;
- HEAD must be on `master` with no local commits (fast-forward only), and no
  local edits to tracked files. Nothing local is ever reset or discarded;
  anything else is skipped and logged.

Then `git merge --ff-only`, a smoke check (the package imports, `--check`
passes) and a restart: the web app only when no request or voice turn is
running (at most ~2 minutes), and the update only counts once `/healthz`
reports the new version within `health_seconds` (180). If anything fails it
goes back with `git reset --keep`, restarts the old version and never
retries that commit. An update that **changes requirements.txt is never
applied automatically** (pip would install new, unpinned dependency ranges
into the venv the assistant is running from): stop the assistant, then run
`python -m assistant --update`. Everything is logged to
`logs/host-<mode>.log` (failures start with `!!!`), with the exact range
(`old -> new`); `logs/update-state.json` has the last result. With
`[watch] gate` on (the default) an update also has to pass the go-live
check before anything restarts on it (see "Health checks and safe
updates"). Moving HEAD yourself (`git checkout`, `git pull`) restarts the
assistant once the go-live check passed, without the `/healthz` rollback;
use `.\scripts\deploy.ps1` instead of `git pull`. The supervisor itself keeps running its starting version until
the task or service restarts (next sign-in or reboot, or `systemctl --user
restart hey-claude` on the Pi).

`python -m assistant --update` updates right away (same checks, also with
`enabled = false`). The phone page notices the new version through
`/healthz` and reloads itself once, never in the middle of a turn, a
recording or the voice conversation.

**Setting up signing (once). Recommended: you approve each update** with
a key that exists only on your PC. The workflow moves `stable` after green
CI; nothing installs until you sign that commit.

1. Make the key and let git sign with it (choose a passphrase):
   ```powershell
   ssh-keygen -t ed25519 -C "kyle hey-claude approvals" -f "$env:USERPROFILE\.ssh\hey_claude_approve"
   git config gpg.format ssh
   git config user.signingkey "$env:USERPROFILE\.ssh\hey_claude_approve"
   ```
2. Trust its public half on each machine that updates:
   ```powershell
   $dir = "$env:APPDATA\HeyClaude"; New-Item -ItemType Directory -Force $dir | Out-Null
   $pub = Get-Content "$env:USERPROFILE\.ssh\hey_claude_approve.pub"
   [IO.File]::WriteAllText("$dir\allowed_signers", "kylechen839@gmail.com namespaces=`"git`" $pub`n")
   ```
   On the Pi: `mkdir -p ~/.config/hey-claude` and put the same line in
   `~/.config/hey-claude/allowed_signers` (only the public key goes there).
3. Each update you approve: look at what changed, then sign and push a tag:
   ```powershell
   git fetch origin stable
   git log --oneline HEAD..origin/stable
   $sha = git rev-parse origin/stable
   git tag -s "stable-$($sha.Substring(0,7))" -m "approved" $sha
   git push origin "stable-$($sha.Substring(0,7))"
   ```
   The hosts update on their next check (`git verify-tag` against
   allowed_signers; only SSH signatures count, never OpenPGP).

**Optional, weaker: CI signs for you.** A CI-signed tag only proves **"CI
passed"**, not **"Kyle approved"**. If you want it anyway, the key must live
in a GitHub **Environment**, never as a repository secret (a repository
secret can be read by a workflow pushed on any branch, which could sign its
own code):

1. GitHub -> the repo -> Settings -> Environments -> New environment
   `stable-signing`: Deployment branches and tags -> Selected -> `master`
   only; Required reviewers -> you.
2. Make a key without a passphrase (`ssh-keygen -t ed25519 -N '""' -C
   "hey-claude stable" -f "$env:TEMP\stable_signing"`), add the private
   key as the environment secret `STABLE_SIGNING_KEY` of `stable-signing`,
   add the `.pub` line to allowed_signers as in step 2 above (with
   `stable-promotion@users.noreply.github.com`), then delete both files.
3. Each promotion then waits for your approval in GitHub (Actions ->
   promote-stable -> Review deployments) before the `sign` job signs
   `stable-<sha>`. With both keys trusted, either one is enough.

The only way around signatures is `[update] trust_unsigned = true`: then
**anyone who can push to master or stable can run code on this machine**.
Use it only for a repository only you can push to.

**Still running the hand-made "Hey Claude Web" task?** It has no supervisor,
so it never updates. Switch with this, in PowerShell (5.1 or 7):

```powershell
cd C:\Users\kylec\Claude-Personal-Assistant
# 1. Up to date: no changes in `git status` (config.toml edits: move them
#    to config.local.toml first), then
git status --short
git checkout master
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Turn on automatic updates in config.local.toml (UTF-8 without a BOM,
#    which the TOML reader needs); it only updates once signing is set up
$local = Join-Path (Get-Location) 'config.local.toml'
if (Test-Path $local) { $text = [IO.File]::ReadAllText($local) } else { $text = '' }
$nl = [Environment]::NewLine
if ($text -notmatch '(?m)^\[update\]') { $text += $nl + '[update]' + $nl + 'enabled = true' + $nl }
[IO.File]::WriteAllText($local, $text, (New-Object Text.UTF8Encoding $false))
Get-Content config.local.toml

# 3. Replace the "Hey Claude Web" task (answer y), then the power check
#    (answer y to stay awake when plugged in). Add -NoWake to install only
#    the phone web app, without the always-listening wake word.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_service.ps1

# 4. Check: both tasks Running, the web app answers with its version
Get-ScheduledTask -TaskPath '\HeyClaude\' | Format-Table TaskName, State
Start-Sleep -Seconds 40
Invoke-RestMethod http://127.0.0.1:8765/healthz        # ok: True, version: <sha>
Get-Content logs\host-web.log -Tail 5
.\.venv\Scripts\python.exe -m assistant --update       # up-to-date, or why it skipped
```

The phone keeps working throughout, apart from a few seconds while the web
app restarts on the same `127.0.0.1:8765` (`tailscale serve` is untouched).
To undo: `.\scripts\install_windows_service.ps1 -Uninstall`, then
re-create the old task by hand.

## Health checks and safe updates

So a broken app is found and fixed before you notice it, a watchdog checks
every web app on the machine (yours and each family copy) and new code only
goes live after it has passed a real conversation.

**Install (once).** On the PC, in PowerShell in the project folder:

```powershell
.\scripts\install_watch_task.ps1             # \HeyClaude\Watch: every 5 minutes, hidden
.\scripts\install_family_tasks.ps1 -Watch    # or together with the family copies' tasks
```

On the Pi: `./scripts/install_watch.sh` (a systemd user timer,
`hey-claude-watch.timer`). `python -m assistant.watch --status` prints the
last results; `python -m assistant.watch --functional` runs everything now.

**What it checks.** Every 5 minutes, for each instance (`config.toml` with
`.env`, and every `config.<name>.toml` with its `.env.<name>`, the same rule
as `install_family_tasks.ps1`): `/healthz` on its port, the page and every
script and stylesheet it loads, that it runs the checked-out version, and
the address the phone opens (`[webapp] public_origin`, else the `tailscale
serve` entry for its port; set `[watch] public_url` to override, `""` to
skip). After every restart or new version, every `functional_hours` (6) and,
once failed, every `retry_minutes` (30), it also has a **test conversation**
with it, the way people use it, in one session: a text turn that makes it
use a read-only tool (the calendar, else the assignments list, else web
search), a follow-up that depends on the first turn, a spoken turn
(`tests/fixtures/hey_claude_calendar_tomorrow.wav`), a hands-free voice turn
over `/api/voice`, and a last text turn typed while the voice view is still
open. Every turn must answer, and the follow-ups must still remember a
number from the first. The conversation runs in a **check session**: no
history, no memory summary, and only read-only tools are offered or run, so
a check never writes to your calendar, Notion, Gmail, memory or history.
Each one costs a few short Claude requests; `functional_hours` sets how
often (estimated, not measured: roughly 30,000 input tokens per instance
per check).

**What it does about a problem.** An app that doesn't answer first gets
`start_grace_seconds` (300) in case it is starting (a restart, a reboot,
loading its models); nothing is done while an update or deploy holds the
update lock, or while someone's turn is running. Then it is restarted once
(its Task Scheduler task; on Linux `[watch] restart_command`, e.g.
`["systemctl", "--user", "restart", "hey-claude-web"]`) and given the grace
period again. If that restart ran and it still fails on code newer than the
**last known good** version (the last one whose test conversation passed
here), the checkout goes back to that version (`git reset --keep`, as the
updater does: config, `.env` and `data/` untouched; not when the newer
commits exist only on this machine, nor when HEAD or the branch moved
meanwhile), the apps restart, and that commit is never taken again. Without
a restart that ran (no restart command), it is only reported. Failures that
aren't the code's (no internet, Claude unreachable, rate limits, a rejected
API key) and a phone address (Tailscale) that doesn't answer are reported
but never restarted for or rolled back.

**New code goes live only after the go-live check** (`[watch] gate`, on by
default): each instance is started a second time on a side port (its port
+ `side_port_offset`, 100: 8765 -> 8865, on 127.0.0.1) and must pass the same
test conversation first (a failure, including new code that exits before
it answers, is tried once more before the commit is refused; a busy side
port, after stopping a leftover check copy, a check copy killed by the
system (out of memory on the Pi) or Claude being unreachable says nothing
about the code and is tried again later: for a pull by hand, 1, 5, 15, then
every 60 minutes after the last try (the host supervisor and the watchdog
share that schedule); for an automatic update, at the next `[update]`
check. You are told once if it keeps happening for 3 tries, or for half an
hour on a pull by hand). The automatic updater does this before the running
apps see the new commit; for a pull by hand the host supervisor or the
watchdog does it before restarting anything. A commit that fails is undone
and the old version keeps running. **Update with one command** instead of
`git pull` and restarting:

```powershell
.\scripts\deploy.ps1            # pull, go-live check, switch over (or refuse)
.\scripts\deploy.ps1 -NoPull    # the code checked out now (after a pull by hand)
```

(`./scripts/deploy.sh` on the Pi; `--no-pull`, `--restart`, `--requirements`.)
`install_family_tasks.ps1 -Restart` goes through the same check.

**Where you hear about it.** Only when something changes (an app starts
failing, or recovers) and for one-off events (a version refused or rolled
back), never repeatedly:
- your phone app shows a banner ("Mom's app is down since 06:13 (healthz:
  HTTP 502)", or that the checks stopped running) on your own instance only;
  family members never see it;
- the dev team coordinator gets a message through the PC hub's link
  (`[devteam]`, when set up; `[watch] coordinator = false` turns it off), so
  it can find the cause and open a PR;
- `logs/watch.log` has every result, and `data/watch_status.json` the
  current state.

An alert names the app, the check and a fixed reason (an error class or an
HTTP status), never conversation text or a key. When the Claude API itself
is down, the app says "Claude is unreachable right now. I retried a few
times; please try again in a minute." instead of a vague error.

## PC hub: chat with the dev team coordinator (desktop only)

The PC hub is a page on this PC's web app where you type to your
`/build-feature` coordinator session(s) and read their replies, Claude Code
style (markdown replies, your messages in bubbles, an "In task" / "Idle"
pill and a sending → queued → working → answered line under your latest
message). It runs on your Claude plan: messages go out with the Claude
Code CLI (`claude -p --cloud <id>`), and replies come back through the
Notion "Hey Claude coordinator inbox" (docs/COORDINATOR_LINK.md).

1. Sign in to Claude Code on the PC once: `claude auth login`.
2. Put NOTION_TOKEN in `.env` (the integration must reach the page that
   holds the Assignments database).
3. In `config.local.toml`:

   ```toml
   [devteam]
   hub = true
   hub_sessions = ["session_..."]   # one window per coordinator, at most 8
   poll_seconds = 4                 # read Notion at most this often
   hub_state_file = "data/devhub.json"
   ```

4. Start the web app (`python -m assistant --mode web`) and open
   http://127.0.0.1:8765/devteam, or double-click
   `tools/agent-dashboard/desktop/open-hub.cmd` (Windows) or
   `open-hub.command` (Mac) for its own app window. Sign in with the web
   access token once.

The hub only exists when the web app listens on 127.0.0.1, and every hub
page and route answers 404 to a request that came through a proxy
(`tailscale serve` included), so it is never on your phone or the cloud
server. Each window has a – button to hide it (the toolbar's Hidden menu
brings it back; the choice stays in this browser) and Clear, which deletes
that window's transcript from `data/devhub.json`.

The hub is off while `TRUSTED_PROXY` (`[webapp] trusted_proxy`) is set:
every hub request answers 404, because a trusted proxy could vouch for a
request from elsewhere, and the web app prints "the PC hub is disabled
while TRUSTED_PROXY is set" at startup instead of the hub link. Open it on
this PC from a web app started without the proxy setting. Dropping a
session from `hub_sessions` deletes that window's transcript from the
file at the next start.

## Preflight: check everything in one pass

```powershell
.\scripts\preflight.ps1                 # Windows (PowerShell 5.1 or 7)
```
```bash
./scripts/preflight.sh --hardware       # Linux / Raspberry Pi
```

Each step prints `PASS`, `FAIL`, `WARN` or `SKIP` (with the reason), then a
table and `GO` (no FAIL; warnings don't block) or `NO-GO`. The full output is saved to
`logs/preflight-<date>-<time>.txt` (gitignored). Values from `.env` are
loaded into the preflight's own process only and are masked as `[REDACTED]`
everywhere, in the console and the report.

| Step | What it checks |
|---|---|
| `git` | on `master`, no uncommitted changes (`config.toml` may differ per machine) |
| `env` | `.env` exists and has `ANTHROPIC_API_KEY` (names only, never values) |
| `check` | `python -m assistant --check` passes |
| `pytest`, `pytest-live` | the full suite, then the live read-only Pinecone/Notion/Google tests, which must not skip |
| `wake-e2e` | `--mode wake --audio-file tests/fixtures/hey_claude_calendar_tomorrow.wav --timings`: a wake, the transcript and a real reply |
| `text-tools` | text mode answers a calendar and a Notion question, and the log shows both tools were called |
| `web` | starts `--mode web` on 127.0.0.1, then the web checks below (including a voice-mode WebSocket turn) |
| `tts-pyttsx3`, `tts-piper` | renders a sentence to a .wav with each engine |
| `audio-devices`, `mic-level` | with `--hardware` only: lists devices, records 3 s and reports the level |
| `power` | Windows only: `WARN` if the PC sleeps or hibernates on AC power (that stops the assistant) or USB selective suspend is on |

`--skip pytest,web` skips steps; `--help` lists the options.

**Web smoke check** (after a deploy, or against the Pi):

```powershell
.\scripts\cloud_smoke.ps1 https://<your-service>.onrender.com
```
```bash
./scripts/cloud_smoke.sh https://<your-service>.onrender.com
```

It checks `/healthz`, that a protected route refuses a request without a
session, logs in with the token in the `WEB_TOKEN` environment variable (or
`WEB_TOKEN` from `.env`; `--token-env` picks another variable), then runs
one text turn, one audio turn and, where the server has voice conversation
mode, one voice turn over the `/api/voice` WebSocket. It never prints the
token. It sends the token only over `https://` (or plain http to this
machine); `--allow-http` overrides that on a network you trust.

**Moving to a Raspberry Pi:** follow [PI_MIGRATION.md](PI_MIGRATION.md).
CI's **pi-arm64** job runs `install_pi.sh`, the tests and the preflight on
Debian Bookworm arm64 with Python 3.11, i.e. a simulated Pi.

## Project layout

```
assistant/
  cli.py        modes, main loop, error handling   (python -m assistant)
  config.py     config.toml + .env loading, logging
  doctor.py     setup check                       (python -m assistant --check)
  chat.py       Claude conversation, tool loop, memory hooks
  router.py     per-turn model choice: Haiku for simple turns, else the main model
  usage.py      API token counts per call, monthly totals and cost estimate
  tts.py        TTS interface, engine choice (auto), pyttsx3, Piper, espeak-ng, print
  tts_kokoro.py Kokoro voice + checksummed download (--download-voice kokoro)
  tts_elevenlabs.py  optional ElevenLabs cloud voice
  voice_picker.py  each person's reply voice in the phone app (/api/voices)
  earcons.py    soft listening / thinking / ended sounds
  stt.py        push-to-talk recording, faster-whisper
  wakeword.py   openWakeWord, energy VAD, mic/file sources
  memory.py     Pinecone recall + conversation summaries
  history.py    conversation history: SQLite store, search, resume
  history_cli.py  python -m assistant --history ...
  audio.py      wav I/O, resampling, chimes
  timing.py     per-turn latency               (--timings)
  webapp.py     private phone web app (FastAPI)  (--mode web)
  history_web.py  phone app history API (/api/conversations)
  cloud.py      the web app on a public server (auth wall, lockout, /healthz)
  voice_ws.py   voice conversation WebSocket (/api/voice)
  voice_session.py  voice turns: streamed reply, sentence TTS, barge-in
  sentences.py  sentence splitter for streamed speech
  web/          the page: index.html, app.js, app.css, sw.js, attachments.js; voice.js,
                voice-core.js, voice-worklet.js, voice.css; vad/ (vendored);
                earcons/ (generated by earcons.py)
  preflight.py  one-pass readiness check        (scripts/preflight.ps1 / .sh)
  smoke.py      web app smoke check             (scripts/cloud_smoke.ps1 / .sh)
  host.py       home host supervisor: restarts, updates (python -m assistant.host)
  updater.py    safe update from the stable branch  (--update)
  watch.py      health checks, test conversation, go-live gate (python -m assistant.watch)
  deploy.py     pull, go-live check, switch or refuse (scripts/deploy.ps1 / .sh)
  version.py    git short SHA for /healthz
  webfeatures.py lecture recorder + tutor routes for the phone app
  lecture.py    chunked uploads, transcription job, Notion lecture notes
  tutor.py      homework photos: checks, EXIF removal, Socratic vision call
  attachments.py  files attached with the chat's "+": type sniffing, limits
  mathspeech.py LaTeX read aloud
  web/          the page: index.html, app.js, app.css, sw.js;
                static/ features.js/css, vendored KaTeX
  tools/        calendar.py, gmail.py, google_auth.py (shared sign-in),
                notion.py, notes.py, web.py, history.py, spotify.py, tool registry
  spotify_web.py  the phone app's "Connect Spotify" (owner only)
scripts/        install_pi.sh, install_service.sh  (Raspberry Pi)
                install_windows_service.ps1               (PC as the always-on host)
                preflight.ps1/.sh, cloud_smoke.ps1/.sh; ci/ (the arm64 Pi simulation)
                measure_memory.py, container_smoke.sh, ws_smoke.py (cloud image)
                install_watch_task.ps1, install_watch.sh, restart_instance.ps1,
                deploy.ps1/.sh                            (health checks, safe updates)
Dockerfile      the cloud image; render.yaml deploys it on Render
deploy/         hey-claude.service                 (systemd user unit)
                hey-claude-watch.service/.timer    (the watchdog every 5 minutes)
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows),
                              the Docker image, the Pinecone MCP connector,
                              api-contract (real-API conversation, with the secret)

tools/agent-dashboard/  dev-team run dashboard + coordinator grid (claude.ai page), watchdog,
                        desktop launchers
tests/          pytest suite (offline; live tests skip without keys);
                tests/js/ node:test unit tests (run by pytest if node is installed)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
.github/workflows/pi-arm64.yml  CI: a simulated Pi (Debian Bookworm arm64, Python 3.11)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for what is still open for you, `MANUAL_TEST.md` for the final
hands-on checklist, and [BACKLOG.md](BACKLOG.md) for planned work (such as
the hybrid model, before a public release).
