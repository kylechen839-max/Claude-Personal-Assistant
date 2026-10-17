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
3. Get the keys and files (details in `BLOCKERS.md`):
   | What | Where | Goes in |
   |---|---|---|
   | Anthropic API key | console.anthropic.com | `.env` `ANTHROPIC_API_KEY` |
   | "Hey Claude" wake word model | already included: `keywords/hey_claude.onnx` (retraining: see BLOCKERS.md) | `config.toml [wake] keyword_path` |
   | Pinecone API key | app.pinecone.io | `.env` `PINECONE_API_KEY` |
   | Notion integration secret (share the Assignments DB with it) | notion.so/profile/integrations | `.env` `NOTION_TOKEN` |
   | Google OAuth Desktop client | console.cloud.google.com | `credentials.json` |
4. Sign in to Google once: `python -m assistant.tools.calendar`
5. Check everything: `pytest` (live tests run when the matching keys exist).
6. Run it:
   ```powershell
   python -m assistant              # always listening (wake word)
   python -m assistant --mode text  # type instead of talking
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

`config.toml` holds all non-secret settings (model, voice, wake word
sensitivity, silence length, follow-up window, Notion database, Pinecone index, time zone,
log file). `CLAUDE_MODEL` in the environment overrides the model. Logs go to
`logs/assistant.log` (rotating); the console only shows warnings.

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

## Project layout

```
assistant/
  cli.py        modes, main loop, error handling   (python -m assistant)
  config.py     config.toml + .env loading, logging
  doctor.py     setup check                       (python -m assistant --check)
  chat.py       Claude conversation, tool loop, memory hooks
  tts.py        TTS interface: pyttsx3, Piper, print; Piper voice download
  stt.py        push-to-talk recording, faster-whisper
  wakeword.py   openWakeWord, energy VAD, mic/file sources
  memory.py     Pinecone recall + conversation summaries
  audio.py      wav I/O, resampling, chime
  tools/        calendar.py, notion.py, tool registry
scripts/        install_pi.sh, install_service.sh  (Raspberry Pi)
deploy/         hey-claude.service                 (systemd user unit)
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for setup only you can do, and `MANUAL_TEST.md` for the final
hands-on checklist.
