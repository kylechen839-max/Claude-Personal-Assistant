# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 246 passed, 8 skipped (Linux build sandbox,
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
| `test_webapp.py` | 57 | phone web app (`--mode web`) |
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

## Phone web app ✅
- `python -m assistant --mode web` (`assistant/webapp.py`, page in
  `assistant/web/`): FastAPI + uvicorn serving one mobile-first PWA page
  (manifest, SVG/PNG icons generated in code, apple-mobile-web-app tags, a
  no-cache service worker) and a small JSON API. Reuses `ClaudeChat` (tools,
  Pinecone recall and summaries), `Transcriber` and the configured TTS engine.
- The phone records with getUserMedia + MediaRecorder (WebM/Opus on
  Android/Chrome, MP4/AAC on iOS Safari); the server decodes any of them with
  PyAV (faster-whisper's own `decode_audio` breaks on PyAV 19, so it isn't
  used), transcribes, answers, and streams NDJSON events (transcript, tool,
  confirm, reply, done). Reply audio is a WAV rendered on demand in one
  dedicated TTS thread (COM initialized for SAPI on Windows); the page can use
  the browser's speechSynthesis instead. The reply text arrives whole, not
  token by token (ClaudeChat doesn't stream).
- Delete confirmation: the tool's confirm callback blocks the turn's thread
  and ends the stream with a "confirm" event; Yes/No buttons, a typed answer
  or a spoken "yes" (same strict `is_yes`) answer it via `/api/confirm`, whose
  response carries the rest of the turn. No answer in `confirm_timeout`
  seconds means no.
- One conversation per browser (signed session cookie); idle longer than
  `conversation_timeout` -> summarized to memory and reset (background reaper,
  also on "goodbye" and on server shutdown). The PC-only `open_in_browser`
  tool is not offered.
- Privacy: binds 127.0.0.1; `WEB_TOKEN` (256-bit) generated into `.env` on
  first run, never logged (also added to log redaction), printed only with
  `--show-token`; login sets an HttpOnly, Secure, SameSite=Strict cookie
  holding an HMAC of a session id (not the token); 5 wrong tokens per 5
  minutes locks login; cross-origin requests rejected (Sec-Fetch-Site /
  Origin vs Host); strict CSP with no inline script, nosniff, DENY framing,
  no-store on the API; 10 MB upload cap. HTTPS for the phone via
  `tailscale serve` (README). `--check` reports `WEB_TOKEN` and the packages.
- Tests (`test_webapp.py`, FastAPI TestClient): 401 without/with a forged
  cookie, wrong token 401 then 429, cookie attributes, cross-origin 403,
  security headers, token generation and redaction, text and voice round
  trips (mocked Whisper), tool call round trip with mocked Anthropic, delete
  confirmation by button yes/no, by voice, unclear voice = no and timeout =
  no, concurrent turn refused, idle summary to memory, per-browser
  conversations, reply audio is a non-empty WAV (PrintTTS and real pyttsx3),
  MP4/AAC (iOS) and WAV decoding, the CLI starting the server, and a real
  Whisper run on an uploaded TTS .wav (skipped without the model, as the
  other STT tests are). Also driven by hand in headless Chromium with a fake
  mic: sign-in, typed turn, Yes button, and a held-mic WebM upload.
- Public-server hardening (for a host like Render): `PORT` and
  `TRUSTED_PROXY` from the environment; X-Forwarded-For/Proto used only
  behind a trusted proxy (rightmost entry); http -> https redirect and HSTS
  there; per-address (5 per 15 min) and global (50) sign-in lockouts that also
  cover wrong Bearer tokens; constant-time compare and generic errors (no
  stack traces, no FastAPI docs, no directory listing); 411 without
  Content-Length, 413 over `max_upload_mb`, over `max_audio_seconds` (checked
  while decoding, so a small file can't expand) or over 4000 characters of
  text; `/healthz`; refuses to listen beyond 127.0.0.1 without `WEB_TOKEN` in
  the environment; uvicorn concurrency limit. Web mode never imports
  sounddevice, openWakeWord or pyttsx3 unless pyttsx3 is the TTS (tested in a
  subprocess).
- Streaming: `ClaudeChat.ask(on_text=, cancel=)` streams replies with
  `messages.stream`; the page shows the text as it arrives. Cancelling keeps
  the words already said in history (for barge-in).
- Reusable by other routes (the hands-free voice mode): `transcribe_upload`,
  `chat_stream(session, text, cancel)`, `synthesize`, `require_session`
  (HTTP 401 / WebSocket close 1008, rejects other sites) and
  `app.state.web.session_for`. `Authorization: Bearer <WEB_TOKEN>` works
  instead of the cookie.
- iPhone: 90-day cookie renewed on use (home-screen apps have their own
  cookie jar), mic requested straight from the tap and released after each
  recording, help text when permission is denied, replies through one
  unlocked `<audio>` element, a service worker that caches only the app shell
  (network first).
- Not yet tried on a real phone: MANUAL_TEST.md "Phone web app".

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
