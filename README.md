# Hey Claude

An Echo-style voice assistant on the Anthropic API. Say **"Hey Claude"**, ask
something, and it answers out loud. It can read and change your Google
Calendar, check and update your Notion assignments, and remembers past
conversations in Pinecone.

```
mic -> openWakeWord wake word -> chime -> record until silence (VAD)
    -> faster-whisper (speech to text) -> Claude (+ tools, + memory)
    -> pyttsx3 / Piper (text to speech) -> speaker
```

## Quick start (Windows 11, PowerShell)

1. Install **Python 3.10+** from python.org (tick "Add python.exe to PATH").
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
| `--tts print` | No audio output (prints what it would say) |
| `--timings` | Also print each turn's latency summary (always in the log) |
| `--keyword-path f.onnx` | Override the wake-word model |
| `--config other.toml` | Use a different settings file |
| `--check` | Check keys, files, audio devices and models, then exit |
| `--update` | Update now from the tested `stable` branch, then exit |
| `--mode web` | Private phone web app (see "Phone web app" below) |
| `--host`, `--port` | Web mode: where to listen (default 127.0.0.1:8765) |
| `--show-token` | Web mode: print the access token to the console once |

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

Things to try: "What's on my calendar tomorrow?", "Move my dentist
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
| `[tts] engine` | "pyttsx3" | "pyttsx3", "piper" or "print" |
| `[stt] model` | "base.en" | faster-whisper model; "tiny.en" is faster |
| `[assistant] timezone` | "" | IANA name, e.g. "America/New_York"; "" = system |
| `[update] enabled` | false | Home host (PC tasks, Pi service): follow the tested, signed `stable` branch; set it in `config.local.toml`, see [Automatic updates](#automatic-updates) |
| `[assistant] api_timeout` | 30 | Seconds before a Claude request gives up |

### Better voice: Piper

The default voice is pyttsx3 (the OS voice: SAPI5 on Windows, eSpeak on the
Pi). [Piper](https://github.com/OHF-Voice/piper1-gpl) is an offline neural
voice that sounds far more natural and is fast enough for a Pi 4/5. It is
installed by `requirements.txt` (`piper-tts`); you only need to download a
voice once (about 60 MB, from the official
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) repo):

```powershell
# Windows (PowerShell, venv active) - same command on the Pi
python -m assistant.tts --download-voice en_US-lessac-medium
python -m assistant.tts --voice-file voices/en_US-lessac-medium.onnx --say "Hello Kyle"
```

That saves `voices/en_US-lessac-medium.onnx` and its `.onnx.json` (the
`voices/` folder is not committed). Then switch the engine in `config.toml`:

```toml
[tts]
engine = "piper"
piper_voice = "voices/en_US-lessac-medium.onnx"
```

To go back, set `engine = "pyttsx3"`. If the voice file is missing, the
assistant logs a warning and uses pyttsx3 instead of crashing. Other voices:
pick a name from the voices repo (e.g. `en_US-amy-medium`,
`en_GB-alan-medium`); `-low` voices are faster on an older Pi. `--tts piper`
on the command line tries Piper for one run without editing the config.
`[tts] rate` and `voice` apply to pyttsx3 only.

## Phone web app

`python -m assistant --mode web` runs the same assistant (Calendar, Notion,
memory, web search, delete confirmations) as a private web page for your
phone. You talk into the phone's own mic; the PC transcribes with Whisper,
asks Claude, and sends back the reply as text and audio (Piper if
`[tts] engine = "piper"`, else pyttsx3; or tick "Use the phone's voice" in
the page's settings). Hold the big mic button to talk (or tap once to start
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
losing a phone) revokes every browser's. Signed-in phones are listed in
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
For the most natural voice set `[tts] engine = "piper"`; with pyttsx3 on
Linux it uses eSpeak directly.

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
scp -r voices pi@raspberrypi:~/hey-claude/   # only if you use Piper
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
with `[tts] engine = "piper"`, whether the Piper voice is there.
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
`.venv/bin/python -m assistant.tts --download-voice en_US-lessac-medium` on
the Pi (or copy `voices/` from your PC) and set `[tts] engine = "piper"`, as
in [Better voice: Piper](#better-voice-piper). Without the voice file the
assistant falls back to eSpeak.

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

**Setting up signing (once).** The promotion workflow signs each promotion
with a key only GitHub Actions holds; your machines trust that key.

1. On the PC, make a key just for this (no passphrase; it never leaves
   GitHub):
   ```powershell
   ssh-keygen -t ed25519 -N '""' -C "hey-claude stable" -f "$env:TEMP\stable_signing"
   ```
2. GitHub -> the repo -> Settings -> Secrets and variables -> Actions -> New
   repository secret: name `STABLE_SIGNING_KEY`, value: the whole content of
   `$env:TEMP\stable_signing` (the private key).
3. Trust the public half on each machine that updates:
   ```powershell
   $dir = "$env:APPDATA\HeyClaude"; New-Item -ItemType Directory -Force $dir | Out-Null
   $pub = Get-Content "$env:TEMP\stable_signing.pub"
   [IO.File]::WriteAllText("$dir\allowed_signers", "stable-promotion@users.noreply.github.com namespaces=`"git`" $pub`n")
   ```
   On the Pi: `mkdir -p ~/.config/hey-claude` and put the same line in
   `~/.config/hey-claude/allowed_signers`.
4. Delete both key files from `$env:TEMP`. From the next promotion on, the
   workflow pushes a signed `stable-<sha>` tag that the hosts verify
   (`git verify-tag`).

Because the signing key sits in GitHub, it protects you from a bad push to
`stable` or a changed `origin`, not from someone who can change the workflow
on master; that's what branch protection on `master` and `stable` is for
(BLOCKERS.md). The only way around signatures is `[update] trust_unsigned =
true`: then **anyone who can push to master or stable can run code on this
machine**. Use it only for a repository only you can push to.

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
  tts.py        TTS interface: pyttsx3, Piper, espeak-ng, print; voice download
  stt.py        push-to-talk recording, faster-whisper
  wakeword.py   openWakeWord, energy VAD, mic/file sources
  memory.py     Pinecone recall + conversation summaries
  audio.py      wav I/O, resampling, chime
  timing.py     per-turn latency               (--timings)
  webapp.py     private phone web app (FastAPI)  (--mode web)
  cloud.py      the web app on a public server (auth wall, lockout, /healthz)
  web/          the page: index.html, app.js, app.css, sw.js

  voice_ws.py   voice conversation WebSocket (/api/voice)
  voice_session.py  voice turns: streamed reply, sentence TTS, barge-in
  sentences.py  sentence splitter for streamed speech
  web/          the page: index.html, app.js, app.css, sw.js; voice.js,
                voice-core.js, voice-worklet.js, voice.css; vad/ (vendored)
  preflight.py  one-pass readiness check        (scripts/preflight.ps1 / .sh)
  smoke.py      web app smoke check             (scripts/cloud_smoke.ps1 / .sh)
  host.py       home host supervisor: restarts, updates (python -m assistant.host)
  updater.py    safe update from the stable branch  (--update)
  version.py    git short SHA for /healthz
  tools/        calendar.py, notion.py, web.py, tool registry
scripts/        install_pi.sh, install_service.sh  (Raspberry Pi)
                install_windows_service.ps1               (PC as the always-on host)
                preflight.ps1/.sh, cloud_smoke.ps1/.sh; ci/ (the arm64 Pi simulation)
                measure_memory.py, container_smoke.sh, ws_smoke.py (cloud image)
Dockerfile      the cloud image; render.yaml deploys it on Render
deploy/         hey-claude.service                 (systemd user unit)
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows),
                              the Docker image, the Pinecone MCP connector

tests/          pytest suite (offline; live tests skip without keys);
                tests/js/ node:test unit tests (run by pytest if node is installed)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
.github/workflows/pi-arm64.yml  CI: a simulated Pi (Debian Bookworm arm64, Python 3.11)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for what is still open for you, and `MANUAL_TEST.md` for the final
hands-on checklist.
