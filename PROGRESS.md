# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 190 passed, 7 skipped (Linux build sandbox,
Python 3.13). CI (`.github/workflows/tests.yml`) runs the same suite on every
push and weekly on Ubuntu (Python 3.10, 3.12) and Windows (3.12). The
sandbox skips are the tests that need something it can't reach: the Whisper
model and Piper voice downloads (Hugging Face is blocked; CI downloads both
and runs those tests), and the live Notion and Google calls (no keys). On
Kyle's PC with `.env` loaded, an earlier run had 0 skipped.

| Test file | Tests | Covers |
|---|---|---|
| `test_chat.py` | 9 | stage 1 |
| `test_tts.py` | 16 | stage 2 |
| `test_stt.py` | 4 | stage 3 |
| `test_wakeword.py` | 15 | stage 4 |
| `test_follow_up.py` | 14 | follow-up mode |
| `test_tools.py` | 27 | stage 5 |
| `test_web.py` | 17 | web search / browser |
| `test_security.py` | 36 | tool safety, log redaction |
| `test_memory.py` | 12 | stage 6 |
| `test_config_cli.py` | 14 | stage 7 |
| `test_doctor.py` | 10 | `--check` |
| `test_timing.py` | 9 | `--timings` |
| `test_end_to_end.py` | 3 | "Hey Claude, what's on my calendar tomorrow?" |
| `test_docs.py` | 11 | docs match the code |

## Stage 1 - Core chat ✅
- `assistant/chat.py`: `ClaudeChat` keeps the conversation history, sends a
  short spoken-style system prompt with the current local date/time, and
  calls the Messages API. Model from `CLAUDE_MODEL` / `[assistant] model`,
  default `claude-sonnet-5-5`. A failed request is rolled back so history
  stays valid; long histories are trimmed to start with a plain user turn.
- `python -m assistant --mode text`: typed chat.
- Tests: mocked Anthropic client (history, system prompt, rollback,
  trimming, exit words, the text loop).

## Stage 2 - Text to speech ✅
- `assistant/tts.py`: a `TTS` interface (`speak`, `save_to_file`) with
  `Pyttsx3TTS` (offline OS voice: SAPI5 on Windows, eSpeak on the Pi),
  `PiperTTS` (offline neural voice, `piper-tts` 1.8.0, wheels for Windows
  x64 and Linux x86_64/aarch64) and `PrintTTS`. `make_tts()` picks one from
  `[tts] engine`; if the Piper voice or package is missing it warns and
  falls back to pyttsx3.
- `python -m assistant.tts --download-voice en_US-lessac-medium` downloads a
  voice into `voices/` (gitignored); `--say TEXT` tries it out loud.
- On Windows, files are rendered through SAPI directly (pyttsx3 2.99 writes
  header-only .wav files there) and a fresh pyttsx3 engine is used per reply.
- Tests: real pyttsx3 and Piper speech rendered to .wav (exists, non-empty,
  not silent), engine selection and fallbacks, the download helper (mocked).

## Stage 3 - Speech to text ✅
- `assistant/stt.py`: `Transcriber` lazily loads faster-whisper (`base.en`,
  CPU, int8); `record_push_to_talk()` records between two Enter presses.
- `python -m assistant --mode voice`: push-to-talk with spoken replies.
- Tests: TTS renders "What is on my calendar tomorrow", real Whisper
  transcribes it, and the transcript must roughly match (pyttsx3 and Piper
  speech); mocked-model tests always run.

## Stage 4 - Wake word ✅
- `assistant/wakeword.py`: openWakeWord with the custom model
  `keywords/hey_claude.onnx` (trained in the openWakeWord Colab notebook;
  same file on Windows and the Pi). If it's missing, it falls back to
  `[wake] builtin_keyword` (`hey_jarvis`). After the wake word a chime
  plays and `EnergyVAD` records until `silence_ms` (800 ms) of silence, with
  an adaptive noise floor, a 15 s cap and a 5 s no-speech timeout.
- Wake mode is the default; `--audio-file f.wav` runs the same pipeline on a
  recording instead of the mic.
- Follow-up mode: after each reply it listens `follow_up_seconds` (5 s)
  without the wake word, with a soft blip; silence ends the conversation.
  The mic is closed while the assistant speaks, drained and its first
  250 ms skipped when it reopens, so it doesn't hear itself.
- Tests: generated speech through the real VAD pipeline with a scripted
  detector; the real "Hey Claude" model fires once on committed Windows SAPI
  recordings (`tests/fixtures/`) and not on other speech; follow-up window,
  self-hearing (with a control run that does hear the reply), timings.

## Stage 5 - Tools ✅
- `assistant/tools/`: a `ToolRegistry` (API schemas, dispatch, errors
  reported back to Claude, a confirmation gate that defaults to "no").
- Google Calendar (`calendar.py`): list events for today/tomorrow/a
  weekday/a date, create (timed or all-day), move (keeps the duration,
  handles DST), delete. Delete always asks for a spoken yes; only a clear
  yes proceeds.
- Notion (`notion.py`): upcoming and overdue unfinished assignments from the
  Fall 2026 Assignments database; set Status (Not started / In progress /
  Done) by name (fuzzy, asks if ambiguous).
- Web (`web.py`, extra): Anthropic's server-side web search and web fetch,
  and a local `open_in_browser`. Once web results are in the conversation,
  creating/moving events, changing a status and opening a page also ask
  first (see SECURITY.md).
- Tests: tool-call parsing and dispatch with mocked API responses, every
  calendar and Notion operation (mocked clients), delete confirmation yes/no,
  URL safety; one real read-only call each for Notion and Google when keys
  are present.

## Stage 6 - Long-term memory ✅
- `assistant/memory.py`: `PineconeMemory` on the existing `project-context`
  index with Pinecone's hosted `llama-text-embed-v2` embeddings (the model
  the index was built with). Before each reply, up to 4 notes scoring at
  least 0.4 are added to the system prompt; after each conversation Claude
  writes a 1-3 sentence summary into the `voice-assistant` namespace (small
  talk is skipped). Other namespaces are only read. A memory failure is
  logged and the reply goes ahead without it.
- Tests: mocked Pinecone (score filter, namespaces, upsert shape, failure
  tolerance, summary/skip) and one real read-only query when
  `PINECONE_API_KEY` is set.

## Stage 7 - Polish ✅
- `config.toml` over built-in defaults (`assistant/config.py`); secrets in
  `.env`; `CLAUDE_MODEL` and a few others override from the environment.
- Logging to `logs/assistant.log` (rotating, 1 MB x 3) with API keys and
  OAuth tokens masked; the console shows warnings only.
- Errors: network down, timeouts, bad key, rate limits and other API errors
  each give a short spoken sentence; a failing command never stops wake
  mode; mic errors are retried 5 times; startup problems print one line and
  exit 1.
- Single entry point `python -m assistant` (`claude_assistant.py` is a shim).
- `--check` (`assistant/doctor.py`): keys set (never values), Google files,
  wake model, audio devices with indexes, Whisper cache, TTS dependencies.
- `--timings` (`assistant/timing.py`): one latency line per turn (wake ->
  end of speech -> transcript -> Claude reply -> first audio), always in the
  log, on the console with the flag.
- Raspberry Pi: `scripts/install_pi.sh` (re-runnable setup) and
  `scripts/install_service.sh` (systemd user service, starts at boot). Both
  pass shellcheck; the install was run in an Ubuntu 24.04 container and all
  packages resolve to aarch64 wheels. Not yet run on a real Pi.
- Tests: config merge, CLI wiring, error messages, wake-loop recovery,
  `--check`, timings, and the definition-of-done test: a .wav of "Hey
  Claude, what's on my calendar tomorrow?" through `cli.main(["--mode",
  "wake", "--audio-file", ...])` with the real wake model, VAD, Whisper (in
  CI) and calendar code; only Anthropic and Google are mocked.
- `tests/test_docs.py`: every `--flag` and `[section] key` in README.md and
  MANUAL_TEST.md exists in the code; no stale names (Porcupine, old models).

## Known issues
- **Wake word on real voices is untested.** The "Hey Claude" model was
  trained only on synthetic voices; tests use Windows SAPI recordings.
  eSpeak's voice doesn't trigger it at all, so it can't be tested with
  Linux TTS. "Hey cloud" also wakes it. Hit rate and false wakes need
  MANUAL_TEST.md step 3; tune `[wake] sensitivity` there.
- **Energy VAD** may need `[wake] vad_min_threshold` raised in a noisy room.
- **pyttsx3 on Windows** has been known to go silent after the first reply.
  A fresh engine per reply should fix it but hasn't been tried on Kyle's PC;
  Piper is the fallback.
- **pyttsx3/eSpeak flakiness in tests** (Linux): about 1 run in 10 the eSpeak
  driver writes an empty .wav or crashes. The tests re-render empty clips up
  to 3 times; a crash means re-running.
- **Real Pi not tested:** systemd lingering, real audio devices and Pi
  latency are in MANUAL_TEST.md's Raspberry Pi section.
- Two Notion databases are named "Assignments"; the Fall 2026 one
  (`7eefabe3-...`) is configured.
- `piper-tts` is GPL-3.0; fine for personal use.
