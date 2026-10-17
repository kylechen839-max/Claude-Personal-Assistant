# Progress

Run all tests with `pytest` (from the project root, venv active).

## Stage 1 - Core chat ✅
- `assistant/chat.py`: `ClaudeChat` keeps conversation history, adds a short
  spoken-style system prompt plus the current local date/time, and calls the
  Messages API. Model comes from `CLAUDE_MODEL` (default `claude-sonnet-5-5`).
  A failed request is rolled back so history stays valid; long histories are
  trimmed so they always start with a plain user turn.
- `assistant/cli.py`: text-mode loop (`python -m assistant --mode text`).
- Tested by `tests/test_chat.py` with a mocked Anthropic client (history,
  system prompt, error rollback, trimming, exit words, the text loop).

## Stage 2 - Text to speech ✅
- `assistant/tts.py`: a `TTS` interface (`speak`, `save_to_file`) with
  `Pyttsx3TTS` (offline OS voice: SAPI5 on Windows, eSpeak on the Pi),
  `PiperTTS` (offline neural voice via the `piper-tts` package, a drop-in
  upgrade), and `PrintTTS` (no audio). `make_tts(name)` picks one.
- `assistant/audio.py`: wav read/write, resampling to 16 kHz, chime, playback.
- `--mode speak`: typed input, spoken replies.
- Tested by `tests/test_tts.py`: renders real pyttsx3 speech to a .wav and
  asserts it exists, is non-empty, and is not silent; wav round-trip/resample.
  Piper: see "Piper voice" below.

### Piper voice (2026-10-17)
- `PiperTTS` now uses the `piper-tts` Python package (pinned 1.8.0: abi3
  wheels for Windows x64, Linux x86_64 and Linux aarch64, any Python 3.9+,
  with espeak-ng data bundled) instead of shelling out to a `piper`
  executable. The voice loads once on first use; `save_to_file` writes a
  .wav, `speak` plays through sounddevice like the other engines.
- `config.toml` `[tts]`: `engine = "pyttsx3" | "piper" | "print"` (default
  still pyttsx3) and `piper_voice` (path to the `.onnx`, with its
  `.onnx.json` next to it; default `voices/en_US-lessac-medium.onnx`). The old
  `piper_model` key is still read.
- `python -m assistant.tts --download-voice en_US-lessac-medium` downloads a
  voice from the official rhasspy/piper-voices Hugging Face repo into
  `voices/` (gitignored), via `.part` files so an interrupted download never
  leaves a broken voice. `--say TEXT` tries a voice out loud.
- If the engine is piper but the voice, its `.onnx.json`, or the package is
  missing, `make_tts` logs a warning naming the problem and the download
  command, and falls back to pyttsx3 (it used to raise).
- Tests (`tests/test_tts.py`): engine selection from `config.toml`, fallback
  for each missing piece, save/speak with a fake `piper` module, voice URLs,
  the download helper with a mocked `requests`. Real tests: Piper renders a
  non-empty, non-silent .wav (`test_piper_renders_wav`), and Whisper
  transcribes Piper output (`tests/test_stt.py::test_transcribe_piper_speech`).
  Both skip only when the voice can't be downloaded. The old
  `test_piper_invokes_cli` (mocked `piper` CLI) was replaced by the mocked
  package test, and `test_make_tts_factory` no longer expects
  `make_tts("piper")` without a voice to raise, since it now falls back.
- In the sandbox huggingface.co is blocked, so the real tests were run
  against the same lessac-medium voice from the rhasspy/piper GitHub release
  (v0.0.2), put in `voices/` by hand: the .wav test passed, and synthesis ran
  at about 40x real time on the sandbox CPU. Whisper is also on Hugging Face,
  so the Piper -> Whisper round-trip runs only in CI (which caches the voice).
- License: `piper-tts` 1.3+ is GPL-3.0-or-later (the Home Assistant
  piper1-gpl fork). Fine for personal use; matters only if this project is
  ever distributed under another license. Voices have their own licenses
  (lessac: see its MODEL_CARD in the voices repo).

## Stage 3 - Speech to text ✅
- `assistant/stt.py`: `Transcriber` lazily loads faster-whisper (`base.en`,
  CPU, int8); `record_push_to_talk()` records between two Enter presses with
  sounddevice; `rough_match()` for fuzzy transcript checks.
- `--mode voice`: push-to-talk, spoken replies.
- Tested by `tests/test_stt.py`: mocked-model transcription and fuzzy
  matching always run; the real test renders "What is on my calendar
  tomorrow" with pyttsx3, transcribes it with Whisper, and asserts a rough
  match.
- Known issue: in the build sandbox the Whisper model can't be downloaded
  (huggingface.co blocked), so the real test is skipped there. See BLOCKERS.md.

## Stage 4 - Wake word ✅
- `assistant/wakeword.py`: audio arrives as 512-sample int16 frames from
  `MicSource` (live mic, paused while the assistant is thinking/speaking) or
  `FileSource` (a .wav). `OpenWakeWordDetector` listens for the custom
  "Hey Claude" model `keywords/hey_claude.onnx`, trained in the openWakeWord
  Colab notebook (falls back to the built-in "hey jarvis" if it's missing).
  Switched from Picovoice Porcupine, whose console now needs a company email.
  After the wake word a chime plays, ~250 ms is skipped so the chime isn't
  recorded, and `EnergyVAD` + `record_until_silence` capture the command
  until 0.8 s of silence (adaptive noise floor, pre-roll, 15 s cap, 5 s
  no-speech timeout).
- `--mode wake` (now the default) and `--audio-file path.wav` to run the
  whole pipeline on a recording. In wake mode "goodbye" ends the
  conversation but keeps listening; Ctrl+C quits.
- Tested by `tests/test_wakeword.py`: builds a .wav containing two
  "Hey Claude, what is on my calendar tomorrow" sessions with pyttsx3 and
  runs it through the real VAD pipeline with a scripted detector (asserts two
  wakes, two chimes, and command lengths); an end-to-end CLI wake-mode test
  over the file with mocked STT/Claude; network error handling.
  Real openWakeWord tests check that the built-in "Hey Jarvis" and the
  trained "Hey Claude" model each fire exactly once on generated speech of
  their phrase and not on other speech. The Whisper-on-pipeline test
  skips without the Whisper model (see BLOCKERS.md).

## Stage 5 - Tools ✅
- `assistant/tools/__init__.py`: `Tool` + `ToolRegistry` (schemas for the
  API, dispatch, errors reported back to Claude instead of crashing, and a
  confirmation gate that defaults to "no").
- `ClaudeChat` now runs the tool-use loop (bounded rounds; a failed turn is
  rolled back entirely so history stays valid).
- `assistant/tools/calendar.py` (Google Calendar): list events for
  today/tomorrow/a weekday/a date, create, move (keeps duration, handles
  all-day events and DST), delete. Delete asks for **spoken confirmation**
  ("Do you want me to delete Dentist on Saturday, October 17 at 3 PM? Please
  say yes or no.") and only a clear yes proceeds.
- `assistant/tools/notion.py`: list upcoming/overdue unfinished assignments
  from the Fall 2026 Assignments database; set Status (Not started /
  In progress / Done) by id or by (fuzzy) name, asking to clarify if
  ambiguous. Uses the Notion 2025-09-03 data-sources API.
- Tools are only enabled when their credentials are present.
- Tested by `tests/test_tools.py` with mocked Anthropic, Google, and Notion
  clients: tool-call parsing and dispatch, tool results threaded back, loop
  bound, rollback, every calendar operation, delete confirmation (declined and
  accepted), yes/no parsing, Notion filters/time-zone conversion/fuzzy and
  ambiguous names. One real read-only call per service runs automatically
  when `NOTION_TOKEN` / `token.json` exist (skipped in the sandbox).

## Stage 6 - Long-term memory ✅
- `assistant/memory.py`: `PineconeMemory` on the existing `project-context`
  index. Embeddings use Pinecone's hosted `llama-text-embed-v2` (1024-d) —
  verified to be the model the index was built with (relevant notes score
  ~0.5-0.6, unrelated ~0.15; `multilingual-e5-large` scored ~0.07 for
  everything). Default relevance cutoff 0.4.
- Before each reply: the user's words are matched across all namespaces and
  up to 4 good notes are added to the system prompt. A memory failure is
  logged and the reply goes ahead without notes.
- After each conversation (exit, "goodbye", or 5 idle minutes in wake mode)
  Claude writes a 1-3 sentence note, which is stored in the
  `voice-assistant` namespace. Small talk ("NOTHING") is not stored. Other
  namespaces are never written.
- Tested by `tests/test_memory.py` with a mocked Pinecone client (score
  filtering, namespaces, upsert shape, prompt injection, failure tolerance,
  summarize/skip, transcript excludes tool data, wake-mode idle timeout),
  plus one real read-only query against your index (runs when
  `PINECONE_API_KEY` is set; it passed during the build).

## Stage 7 - Polish ✅
- `config.toml` + `assistant/config.py`: every setting in one file, merged
  over built-in defaults; secrets from `.env`; `CLAUDE_MODEL` (and a few
  others) override from the environment. Python 3.10 uses `tomli`.
- Logging to `logs/assistant.log` (rotating, 1 MB x 3): startup, what was
  heard, tool calls, replies, errors with tracebacks. Console shows warnings.
- Error handling: network down / timeouts, bad API key, rate limits and
  other API errors each produce a short spoken sentence; tool errors are
  reported to Claude, which explains them; memory failures are skipped;
  in wake mode a failing command never stops the loop, and mic errors are
  retried (gives up with a clear message after 5 in a row); startup problems
  (missing key, no mic, bad config) print one clear line and exit 1.
- Single entry point `python -m assistant` (wake mode by default);
  `claude_assistant.py` is now a thin shim to it.
- Tested by `tests/test_config_cli.py` (config merge/env/.env, shipped
  config.toml valid, log file written, missing-key exit, full `main()` text
  run, startup error message, tools/memory enabled per config, wake-loop
  recovery and give-up, error messages) and `tests/test_end_to_end.py`:
  "Hey Claude, what's on my calendar tomorrow?" from a .wav through the
  real wake/VAD pipeline, the Claude tool loop, the real calendar tool code
  (Google API mocked), to the spoken answer.

## Extra - Internet and browser access ✅
- `assistant/tools/web.py`: Anthropic's server-side **web search** and
  **web fetch** tools (they run inside the API call; versions picked per
  model: `_20260209` for Sonnet 4.6+/Opus 4.6+/Fable, `web_search_20250305`
  / `web_fetch_20250910` for older models), and a local **open_in_browser**
  tool (default browser via `webbrowser`; http/https only, or a Google
  search).
- `ToolRegistry` now carries server tool definitions alongside client
  tools; they are sent with every request but never dispatched locally.
- `ClaudeChat` keeps server blocks (`server_tool_use`,
  `web_search_tool_result`, `web_fetch_tool_result`, thinking) in history
  unchanged, resumes `pause_turn` without adding a user message (bounded by
  `max_tool_rounds`), and speaks a short fallback on `refusal`. Citations are
  dropped from history text since replies are spoken.
- The system prompt adds web guidance only when web tools are on: use the
  web for anything current, answer in 1-3 spoken sentences, no URLs, and
  treat web page text as information, never as instructions.
- `[web]` in `config.toml`: on/off per tool, per-request limits (3 searches,
  3 page reads, 8000 tokens per page), allowed/blocked domains, approximate
  location. `[assistant] max_tokens` raised 500 -> 1500 so thinking plus
  search results don't cut replies off (spoken replies are still 1-3
  sentences).
- Tested by `tests/test_web.py`: tool versions per model, definitions from
  config (location, domain lists), browser URL safety (blocks `file:`,
  `javascript:` etc.), server tools sent but not dispatched, server blocks
  kept in history, `pause_turn` resume and its bound, search + browser in
  one turn, refusal fallback, CLI wiring. Real SDK block objects were also
  round-tripped through the history conversion. Not yet run against the real
  API (no Anthropic key in the build environment).

## Extra - Raspberry Pi deployment ✅
- `scripts/install_pi.sh`: one-step, re-runnable Pi setup (Pi 4/5, 64-bit
  Raspberry Pi OS Bookworm). Installs only the missing apt packages
  (`python3-venv python3-dev libportaudio2 espeak-ng libespeak1
  alsa-utils`), creates `.venv` (needs Python 3.10+), installs
  `requirements.txt` then `openwakeword --no-deps` (tflite-runtime has no
  wheels for newer Pythons; only ONNX is used), copies `.env.example` to
  `.env` if missing (chmod 600; contents never printed), pre-downloads the
  Whisper and openWakeWord models (non-fatal if offline) and prints the next
  steps. `SKIP_APT`, `SKIP_MODELS`, `PYTHON` override steps.
- `deploy/hey-claude.service` + `scripts/install_service.sh`: a systemd
  **user** unit, because the Bookworm sound server (PipeWire) lives in the
  user session and a system service can't reach it; lingering is enabled so
  it starts at boot without a login. It runs
  `.venv/bin/python -m assistant --mode wake`, orders after
  PipeWire/WirePlumber/PulseAudio, waits up to 2 minutes for DNS of
  api.anthropic.com (a user manager can't depend on the system's
  `network-online.target`), restarts on failure (10 s delay, gives up after
  5 failures in 5 minutes) and logs to the journal plus `logs/assistant.log`.
  `--uninstall` removes it.
- `python -m assistant --check` (`assistant/doctor.py`): reports whether each
  key is set (never the value), credentials.json/token.json, the wake-word
  model, audio input/output devices with indexes (and the configured
  `mic_device`), whether the Whisper model is cached (checked with
  `local_files_only`, so it never downloads), and the TTS engine's
  dependencies. Exits 1 if a required check fails. It runs before the
  API-key check so it works on a half-configured Pi.
- README: Pi section rewritten around the scripts (copying files from the PC
  with scp, headless Google sign-in, service commands) plus troubleshooting
  (no mic, choosing the device index, ALSA messages, audio group, service
  restarts, lingering).
- Tested: `tests/test_doctor.py` (9 tests, all mocked: each check's
  pass/fail path, no secret in the output, CLI wiring without an API key).
  Both scripts pass `shellcheck` 0.9; the unit (with the path filled in)
  passes `systemd-analyze verify`. `install_pi.sh` was run in a fresh copy of
  the repo with `PYTHON=python3.11` in an Ubuntu 24.04 container (Debian
  family, real apt): it installed the missing apt packages, created `.venv`,
  installed everything and created `.env` (mode 600). The model pre-download
  was blocked by the sandbox's network proxy, which exercised the "download
  on first run instead" fallback. A second run was a no-op ("already
  installed", existing `.venv` and `.env` kept, pip "already satisfied"). In
  that `.venv`: full `pytest` 93 passed, 4 skipped (before merging master); a real `--check` printed
  the expected report (no audio devices in the container, so it exited 1)
  and did not print the test key from `.env`. Not tested: a real Pi, a real
  systemd user session and lingering (no systemd running in the sandbox),
  real audio devices; see MANUAL_TEST.md section 8.
- After merging master (trained "Hey Claude" model, tool safety, Piper TTS):
  `--check` reports the bundled `keywords/hey_claude.onnx` and, with
  `[tts] engine = "piper"`, whether the voice `.onnx`, its `.onnx.json` and
  the `piper` package are there (a warning, since the assistant falls back
  to pyttsx3), with the `--download-voice` command. `install_pi.sh` lists
  the Piper voice download as an optional next step; the README no longer
  says to copy the wake-word model. aarch64 check: `pip download
  --only-binary=:all: --platform manylinux_2_28_aarch64 --python-version
  3.11 -r requirements.txt` resolves all 79 packages to prebuilt wheels,
  including `piper_tts-1.8.0-cp39-abi3-manylinux_2_17_aarch64` (needs glibc
  2.17+; Bookworm has 2.36), onnxruntime, ctranslate2, av, scipy and
  scikit-learn, so nothing compiles on the Pi. Re-running the merged
  `install_pi.sh` was again a no-op. `pytest`: 150 passed, 6 skipped,
  1 failed: `test_wakeword.py::test_real_hey_claude_model` (known: the
  model doesn't fire on eSpeak's voice on Linux; being fixed separately
  with SAPI fixture files, test left unchanged).

## Extra - Voice loop: end-to-end test, timings, follow-up mode
### Definition-of-done test (2026-10-17)
- `tests/test_end_to_end.py::test_definition_of_done_phrase` builds one .wav:
  1 s of silence, a committed SAPI "Hey Claude" recording
  (`tests/fixtures/hey_claude_sapi_zira_180.wav`; the trained model doesn't
  fire on eSpeak), 0.4 s, pyttsx3 saying "what's on my calendar tomorrow",
  then 2 s of silence. It runs `cli.main(["--mode", "wake", "--audio-file",
  ...])`, i.e. `python -m assistant` with the shipped settings (memory off),
  so the real openWakeWord model, VAD, tool loop and calendar code run. Only
  the Anthropic client (asks for `list_calendar_events {"day": "tomorrow"}`,
  then answers from the tool result it gets back) and the Google API are
  mocked. Asserts: one wake, the transcript reaches Claude, the calendar
  is queried from tomorrow 00:00 to the next midnight in America/New_York,
  and the spoken answer names the mocked event.
- Two cases: `whisper` (real faster-whisper) and `mocked-stt` (only the
  transcription replaced by the known transcript). In the sandbox
  huggingface.co is blocked, so the `whisper` case skips with that reason;
  CI downloads the model and runs it.

### Latency timings (2026-10-17)
- `assistant/timing.py`: `TurnTimer` stamps each stage of a turn with one
  monotonic clock: wake word detected -> end of speech (VAD) -> transcript
  ready -> Claude's reply -> first audio played. Each stage is logged at
  INFO as it happens (`Timing: transcript +0.42 s (2.31 s after wake)`), and
  every turn ends with one summary line in `logs/assistant.log`:
  `Turn timings from wake: end of speech +1.50 s, transcript +0.40 s, Claude
  reply +1.10 s, first audio +0.20 s; wake to first audio 3.20 s (end of
  speech to first audio 1.70 s)`. `--timings` prints that line to the
  console too.
- "Claude reply" is the whole reply, tool calls included: the assistant
  doesn't stream, so there is no separate first-token time. "First audio"
  is when sound starts: pyttsx3 reports it through its `started-utterance`
  callback, Piper after synthesis just before playback; engines without the
  `on_audio_start` hook are stamped when `speak()` is called.
- "Wake -> end of speech" includes the time spent talking plus the
  `[wake] silence_ms` pause (0.8 s) the VAD waits for, so the latency Kyle
  feels after he stops talking is roughly 0.8 s + the last three stages
  (the "end of speech to first audio" figure in the summary).
- Tested by `tests/test_timing.py` with a fake clock: step and summary
  maths, first-mark-wins, INFO records, the summary in the log file and on
  the console only with `--timings`, and the audio-start hook for PrintTTS,
  Piper (before playback) and pyttsx3 (from the engine callback).
- Numbers from the build sandbox (4-core cloud VM, no audio devices; **not**
  real hardware, and Claude/Whisper weren't reachable, so those stages
  aren't measured): openWakeWord + VAD process audio at about 50x real time
  (0.6 ms per 32 ms frame; loading the model takes 0.7 s at startup); eSpeak
  renders a 3.5 s reply in 0.02 s, so first audio comes right after the
  reply; Piper was measured earlier at about 40x real time. On a real
  turn expect the time to be dominated by the 0.8 s end-of-speech pause,
  Whisper base.en on CPU and the Claude round trip(s); run with
  `--timings` on the PC and the Pi to see them.

## Test summary
`pytest`: see the latest run noted in "Extra - Raspberry Pi deployment" (Linux
build sandbox). The skips are real-service tests that need things the
sandbox lacks (Whisper model download, Notion token, Google sign-in); see
BLOCKERS.md.

## Fixes after review
- A reply cut off by `max_tokens` mid tool call left an unanswered
  `tool_use` in history, so every later request failed until the
  conversation reset. Unrun tool calls are now dropped from history.
- `[memory] search_namespaces = "name"` (a single string) was split into
  letters; it is now treated as one namespace.
- Unexpected (non-API) errors in text/voice mode crashed the session; they
  now produce a spoken "Sorry, something went wrong" and the loop goes on.
- Tests: one regression test per fix (`test_chat.py`, `test_memory.py`,
  `test_config_cli.py`).
- Weekday names: "friday" (or "this friday") is the coming Friday, or
  today on a Friday; "next friday" is never today. The tool description
  asks Claude to prefer YYYY-MM-DD for other days.
- All-day events can be created (date-only start/end; `end` is the last
  day, inclusive). Moving an all-day event with `new_end` is inclusive too.
- `--audio-file` cuts audio into the detector's frame length instead of a
  fixed 512 samples.
- pyttsx3 builds a fresh engine for each reply on Windows, the usual fix
  for it going silent after the first reply (not yet tried on Windows).
- pyttsx3 2.99's `save_to_file` writes an empty (header-only) .wav on
  Windows; found by the Windows CI job. Files are now rendered through SAPI
  directly on Windows (live speech still goes through pyttsx3).
- Memory reads note text from the first of `text`, `chunk_text`, `content`,
  `summary` in the metadata (`[memory] text_fields` in config.toml).

## Continuous testing
`.github/workflows/tests.yml` runs `pytest` on every push and pull request,
plus weekly (Mondays), on Ubuntu (Python 3.10 and 3.12, as a stand-in for
the Pi) and Windows (Python 3.12). Live-service tests skip there (no
secrets are given to CI); the Whisper tests do run, since CI can download
the model. On Linux the job installs espeak-ng for pyttsx3 and then
openWakeWord with `pip install --no-deps`, the same as the Pi setup in the
README, because its tflite-runtime dependency has no wheels for newer Pythons.
The Piper voice is downloaded into `voices/` by the tests and cached
between runs, like the Whisper model.
The wake-word pipeline test measures command length from the TTS clip
trimmed once in `tts_clip`, so SAPI's silence padding on Windows does not
skew it.

## Security review (tool safety)
See SECURITY.md for the attack paths checked and the ones left alone.
- Once web search/fetch results are in the conversation, moving or creating
  calendar events, changing a Notion status, and opening a browser page all
  ask for a spoken yes first (deletes always did).
- `open_in_browser` now refuses user@host addresses, backslashes, spaces and
  control characters, bad ports and very long addresses; the confirmation
  names the real host (punycode for look-alike international domains).
- The log file masks API keys, OAuth tokens and client secrets, which
  `requests_oauthlib` logs at DEBUG level.
- Tests: `tests/test_security.py`; `pytest`: 120 passed, 4 skipped.

## Known issues / notes
- The wake word itself (openWakeWord + your `.onnx`) and the real Whisper model
  have not been exercised in the sandbox; they are the first things to check
  in MANUAL_TEST.md.
- pyttsx3 on some Windows setups stops speaking after the first reply. A
  fresh engine per reply is now used on Windows; if it still happens,
  switch `[tts] engine = "piper"` (and tell me).
- The pyttsx3/eSpeak tests occasionally crash (segfault in pyttsx3's eSpeak
  driver) or fail in the Linux sandbox, about 1 run in 10. This happens on
  master too, without Piper loaded, so it is a pyttsx3/eSpeak issue, not a
  Piper one.
- The energy VAD may need `[wake] vad_min_threshold` raised in a noisy room.
- The "Hey Claude" model was trained only on synthetic voices. Tests use
  Windows SAPI recordings (`tests/fixtures/`), and eSpeak's robotic voice
  doesn't trigger it at all. Real-voice accuracy and the false-wake rate in
  normal conversation still need Kyle's MANUAL_TEST check (section 3); tune
  `[wake] sensitivity` there if needed. "Hey cloud" also wakes it.
- Two Notion databases are named "Assignments"; the Fall 2026 one is used.
- Default model changed from `claude-sonnet-4-5` to `claude-sonnet-5-5`
  (2026-10-17): the API now returns 404 for `claude-sonnet-4-5`, so every
  request failed. Found during the first live end-to-end check on Kyle's PC.
