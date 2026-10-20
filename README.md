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
| `[wake] mic_device` | "" | Mic index (0 included) or part of its name, for wake and push-to-talk; `--check` lists them and shows the one that will open |
| `[tts] engine` | "auto" | "auto", "kokoro", "piper", "pyttsx3", "espeak", "elevenlabs" or "print"; `[tts] fallback` is the last resort (pyttsx3, or espeak on a server) |
| `[tts] kokoro_voice` | "af_heart" | Kokoro voice (see "Voices") |
| `[tts] speed` | 1.0 | Speaking speed for Kokoro and ElevenLabs |
| `[stt] model` | "base.en" | faster-whisper model; "tiny.en" is faster |
| `[assistant] timezone` | "" | IANA name, e.g. "America/New_York"; "" = system |
| `[update] enabled` | false | Home host (PC tasks, Pi service): follow the tested, signed `stable` branch; set it in `config.local.toml`, see [Automatic updates](#automatic-updates) |
| `[assistant] api_timeout` | 30 | Seconds before a Claude request gives up |

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
(10), `max_audio_seconds` (60), and `allowed_origins` (only needed if a proxy
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
the part of its answer you actually heard. The conversation keeps going
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
  `vad_threshold` (e.g. 0.7) or use earphones.
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
   `NOTION_ASSIGNMENTS_DB`, `PINECONE_API_KEY` or `PINECONE_INDEX` is set in
   the shell but not by `.env.mom`: `.env.mom` cannot override them, so her
   instance would use your Google account, history or token. The same check
   runs whenever `[webapp] google_connect` is on.
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

## Invite family (cloud)

On the Render server you can give someone their own account on your
assistant, with their own conversation history and language (English or
Chinese), without sharing `WEB_TOKEN`. The server profile turns this on
(`[family] enabled`); on a PC it is off unless you set it.

1. Signed in with your token, open ⚙ -> **Invite someone**, enter their
   name and language (and their time zone, e.g. `Asia/Shanghai`), then
   **Create invite link**. The phone's share sheet opens (or the link is
   copied). Send it to them however you like. The link points at the
   server itself (`[webapp] public_origin`, else with failover the cloud's
   address, `APP_ORIGIN`), even when your phone is talking to the Pi.
2. They open the link on their phone: it signs that phone in as them and
   reloads the page in their language. Share -> Add to Home Screen.
3. The link works once and for `invite_days` (30 days). If it expired, or
   they get a new phone, tap **New link** next to their name (the old link
   stops working).
4. **Remove** signs them out everywhere and deletes their conversations
   (if a file can't be deleted right away, it is retried later).

What they get: chat and voice with Claude, web search, and their own
history (in `<dir>/<id>/history.sqlite`, `/data/users` on Render, never
synced to your Pi or PC). What they never get: your Notion, Pinecone memory
and notes, Google Calendar and Gmail, the dev team, the lecture recorder and
tutor, your history, failover, or the invite menu. Their **Sign out
everywhere** signs out only their own phones; yours signs out only yours.
Settings: `[family]` in `config.toml` (`dir`, `max_users`, `invite_days`).
Their own Google account and Chinese speech recognition and voice come
later (`zh_stt_model`, `zh_piper_voice` are reserved for that).

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
(`old -> new`); `logs/update-state.json` has the last result. Moving HEAD
yourself (`git checkout`, `git pull`) only restarts the assistant, never
rolls back. The supervisor itself keeps running its starting version until
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
  tts.py        TTS interface, engine choice (auto), pyttsx3, Piper, espeak-ng, print
  tts_kokoro.py Kokoro voice + checksummed download (--download-voice kokoro)
  tts_elevenlabs.py  optional ElevenLabs cloud voice
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
  web/          the page: index.html, app.js, app.css, sw.js; voice.js,
                voice-core.js, voice-worklet.js, voice.css; vad/ (vendored);
                earcons/ (generated by earcons.py)
  preflight.py  one-pass readiness check        (scripts/preflight.ps1 / .sh)
  smoke.py      web app smoke check             (scripts/cloud_smoke.ps1 / .sh)
  host.py       home host supervisor: restarts, updates (python -m assistant.host)
  updater.py    safe update from the stable branch  (--update)
  version.py    git short SHA for /healthz
  webfeatures.py lecture recorder + tutor routes for the phone app
  lecture.py    chunked uploads, transcription job, Notion lecture notes
  tutor.py      homework photos: checks, EXIF removal, Socratic vision call
  mathspeech.py LaTeX read aloud
  web/          the page: index.html, app.js, app.css, sw.js;
                static/ features.js/css, vendored KaTeX
  tools/        calendar.py, gmail.py, google_auth.py (shared sign-in),
                notion.py, notes.py, web.py, history.py, tool registry
scripts/        install_pi.sh, install_service.sh  (Raspberry Pi)
                install_windows_service.ps1               (PC as the always-on host)
                preflight.ps1/.sh, cloud_smoke.ps1/.sh; ci/ (the arm64 Pi simulation)
                measure_memory.py, container_smoke.sh, ws_smoke.py (cloud image)
Dockerfile      the cloud image; render.yaml deploys it on Render
deploy/         hey-claude.service                 (systemd user unit)
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows),
                              the Docker image, the Pinecone MCP connector

tools/agent-dashboard/  dev-team run dashboard + coordinator grid (claude.ai page), watchdog,
                        desktop launchers
tests/          pytest suite (offline; live tests skip without keys);
                tests/js/ node:test unit tests (run by pytest if node is installed)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
.github/workflows/pi-arm64.yml  CI: a simulated Pi (Debian Bookworm arm64, Python 3.11)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for what is still open for you, and `MANUAL_TEST.md` for the final
hands-on checklist.
