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
   | "Hey Claude" `.onnx` model (optional; until then say "Hey Jarvis") | openWakeWord training notebook (see BLOCKERS.md) | `keywords/` + `WAKEWORD_MODEL_PATH` in `.env` |
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
| `--keyword-path f.onnx` | Override the wake-word model |
| `--config other.toml` | Use a different settings file |

In wake mode, "goodbye" ends the conversation (it's summarized into memory)
and the assistant keeps listening; a conversation also ends after 5 quiet
minutes. Ctrl+C quits. Deleting a calendar event always asks "yes or no?"
first.

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
sensitivity, silence length, Notion database, Pinecone index, time zone,
log file). `CLAUDE_MODEL` in the environment overrides the model. Logs go to
`logs/assistant.log` (rotating); the console only shows warnings.

Better voice: install [Piper](https://github.com/rhasspy/piper), download a
voice (`.onnx` + `.onnx.json`), and set `[tts] engine = "piper"` and
`piper_model = "path/to/voice.onnx"`.

## Raspberry Pi (Pi 4/5, 64-bit Raspberry Pi OS)

The code is the same; only system packages, the wake-word file, and maybe a
smaller Whisper model change.

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev libportaudio2 espeak-ng libespeak1
git clone <this repo> hey-claude && cd hey-claude
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps "openwakeword>=0.6"   # skips tflite-runtime (no wheels for Python 3.12+)
cp .env.example .env && nano .env
```

- **Wake word:** copy the same `keywords/hey_claude.onnx` over; openWakeWord
  models are not platform-specific.
- **Google sign-in:** the Pi usually has no browser. Either copy `token.json`
  from your PC, or run `python -m assistant.tools.calendar --headless`: open
  the printed link on your phone/PC, sign in, then paste back the
  `localhost:8765/...` address the browser ends up on.
- **Speed:** set `[stt] model = "tiny.en"` if `base.en` feels slow; Piper
  sounds much better than eSpeak and runs well on a Pi 4/5.
- **Audio devices:** `python -m sounddevice` lists them; set
  `[wake] mic_device` if the default isn't your USB mic. Test the speaker
  with `speaker-test -t wav -c 2`.
- **Run at boot** (optional) with a systemd service:
  ```ini
  # /etc/systemd/system/hey-claude.service
  [Unit]
  Description=Hey Claude
  After=network-online.target sound.target
  [Service]
  WorkingDirectory=/home/pi/hey-claude
  ExecStart=/home/pi/hey-claude/.venv/bin/python -m assistant
  Restart=on-failure
  User=pi
  [Install]
  WantedBy=multi-user.target
  ```
  then `sudo systemctl enable --now hey-claude`.

## Project layout

```
assistant/
  cli.py        modes, main loop, error handling   (python -m assistant)
  config.py     config.toml + .env loading, logging
  chat.py       Claude conversation, tool loop, memory hooks
  tts.py        TTS interface: pyttsx3, Piper, print
  stt.py        push-to-talk recording, faster-whisper
  wakeword.py   openWakeWord, energy VAD, mic/file sources
  memory.py     Pinecone recall + conversation summaries
  audio.py      wav I/O, resampling, chime
  tools/        calendar.py, notion.py, tool registry
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for setup only you can do, and `MANUAL_TEST.md` for the final
hands-on checklist.
