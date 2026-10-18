# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 409 passed, 9 skipped (Linux build sandbox,
Python 3.13), plus 12 JavaScript tests (node:test, also run by pytest).
CI (`.github/workflows/tests.yml`) runs the same suite on every
push and weekly on Ubuntu (Python 3.10, 3.12) and Windows (3.12), and
builds and smoke-tests the cloud Docker image. The
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
| `test_memory_tools.py` | 7 | stage 6 (memory tools) |
| `test_config_cli.py` | 14 | stage 7 |
| `test_doctor.py` | 10 | `--check` |
| `test_timing.py` | 9 | `--timings` |
| `test_webapp.py` | 60 | phone web app (`--mode web`) |
| `test_cloud.py` | 71 | cloud server: auth wall, lockouts, proxy headers, limits |
| `test_server_profile.py` | 22 | server profile, Google secrets from env, server `--check` |
| `test_end_to_end.py` | 3 | "Hey Claude, what's on my calendar tomorrow?" |
| `test_docs.py` | 11 | docs match the code |
| `test_sentences.py` | 17 | voice mode: sentence splitter |
| `test_voice.py` | 22 | voice mode: turn pipeline, barge-in |
| `test_voice_ws.py` | 24 | voice mode: WebSocket, limits, files |

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
- Memory tools (`assistant/tools/memory_tools.py`, all modes): `search_memory`
  lets Claude look notes up when asked ("what do my notes say about...");
  `remember` saves a note to the `voice-assistant` namespace when asked
  ("remember that..."), with a confirmation if web/email content is in the
  conversation. Tests: `test_memory_tools.py` (mocked memory).
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
  behind a trusted proxy (rightmost entry; `trusted_proxy` is a count, or the
  proxy's address such as 127.0.0.1 for `tailscale serve`, and then the
  headers count only from that address); http -> https redirect and HSTS
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

## Cloud hosting (Render) ✅
Runs the phone web app 24/7 on Render so it works with the PC off.
- **Secrets from the environment.** `GOOGLE_TOKEN_JSON` / `GOOGLE_CLIENT_JSON`
  (the contents of `token.json` / `credentials.json`) replace the files when
  set; a token from the environment is refreshed in memory and never written
  to disk (nor any token with `[calendar] save_token = false`). A refused
  refresh logs one clear warning with only the OAuth error code (e.g.
  `invalid_grant`) and tells Claude the sign-in expired. The JSON values are
  in log redaction too. No interactive sign-in on a server.
- **Server profile** (`ASSISTANT_PROFILE=server`, set in the image; config
  `SERVER_PROFILE`): Piper with the voice baked in, then espeak-ng (new
  `EspeakTTS`, the `espeak-ng` command), then text only; no browser tool, no
  wake word or chimes, logs to stdout only, listens on 0.0.0.0:`$PORT`; web
  is the default and only audio-free mode. `--check` has a server variant: no
  mic/speaker/wake-word checks; `WEB_TOKEN` required; time zone,
  `GOOGLE_TOKEN_JSON` contents, Whisper cache, Piper and espeak-ng checked.
  New env overrides: `ASSISTANT_TIMEZONE`, `WHISPER_MODEL`.
- **`assistant/cloud.py`** (`CloudGuard`, an outer ASGI layer; the web module
  itself is unchanged): `/healthz` -> `ok`; every other route needs the
  cookie or Bearer token except the login page, its static files, login, the
  manifest and icons (unknown paths 401 too); per-address lockout 5 wrong
  tokens / 15 min -> 15 min, doubling to a day (wrong tokens then 429 after
  2 s), global brake after 30 (wrong tokens held 1 s), the right token
  always accepted; at most 2 body uploads per address / 32 in all, bodies
  due within 10 s + 1 s per 64 KB (408), no uvicorn `limit_concurrency`
  (it let one slow client 503 everything, `/healthz` included); no trusted
  proxy by default, 10.0.0.0/8 in the server profile (`CLOUD_TRUSTED_PROXIES`;
  trusting all private ranges let a Docker-bridge or LAN peer forge client
  addresses), optional `[cloud] client_ip_header`, IPv6 counted per /64,
  limit tables capped, "Proxy check" log lines for the first 5 requests; forwarded
  headers only from a private-address peer (`[cloud] trusted_proxies`),
  rightmost untrusted `X-Forwarded-For` hop as the client; https redirect and
  HSTS; bodies buffered and capped (4 MB audio, 16 KB otherwise, chunked
  included); 120 requests/min per address; generic 500s. Whisper and the
  voice load at startup in the background. The voice WebSocket
  (`/api/voice`) passes the same checks before its handshake (rate limit,
  cookie or Bearer with lockout, same-site Origin); `X-Forwarded-Host` is
  dropped unless the proxy sent it. The web app inside trusts forwarded
  headers from the same networks (`ProxyNetworks`, a `ProxyTrust` by CIDR),
  so its own proxy-aware checks agree with the guard. uvicorn runs with `ws="auto"` (the
  `websockets` sans-I/O implementation) and `ws_max_size` 2 MB.
- **Docker image** (`Dockerfile`, python:3.11-slim + espeak-ng + ffmpeg):
  Whisper `base.en` and `tiny.en` and the Piper `en_US-lessac-medium` voice
  downloaded at build time (`HF_HUB_OFFLINE=1` at run time), non-root user,
  no openWakeWord. `.dockerignore` keeps secrets, logs, voices and the venv
  out. `render.yaml` adds the `hey-claude` service (docker, `standard`,
  `/healthz`, secrets `sync: false`, `WEB_TOKEN` generated) next to
  `pinecone-mcp`.
- **Persistent disk**: 1 GB at `/data` (`DATA_DIR=/data`,
  `HISTORY_PATH=/data/history.sqlite`) for conversation history, lecture
  audio and job state from the history/features branches. Nothing on this
  branch reads them yet: until those land, conversations stay in memory. Disks need a paid
  instance type (already the case) and cost $0.25/GB a month, so the total
  is about $25.25. `deploy/docker-entrypoint.sh` chowns the mounted disk to
  the app user and drops root with `setpriv --no-new-privs`; CI checks the
  server's uid is 10001 and that it owns and can write a root-mounted `/data`.
- **Memory, measured** in CI (`scripts/container_smoke.sh` +
  `scripts/measure_memory.py`, GitHub runner, 4 CPUs, image built from this
  branch), 5 s Piper clip of "Hey Claude, what's on my calendar tomorrow? And
  do I have any assignments due this week?":

  | Run | Peak RSS | Container (cgroup) peak | Transcribe 5 s |
  |---|---|---|---|
  | Server after one spoken turn, base.en + Piper | 465 MB | 395 MB | - |
  | Script: imports + Piper + base.en + Piper reply | 564 MB | 507 MB | 0.71 s |
  | Same, capped at 512 MB | 569 MB | 511 MB (at the cap) | - |
  | Script with tiny.en | 467 MB | 410 MB | 0.43 s |

  The imports alone are about 120 MB, the Piper voice adds about 160 MB, and
  loading base.en briefly peaks about 150 MB above its steady size. Both
  models transcribed the clip correctly ("...assignments to this week").
  **Plan: Render Standard (2 GB, 1 CPU, $25/month; $25.25 with the disk)**: base.en needs up to
  about 565 MB, more than Starter's 512 MB (it only stayed under the cap by
  the kernel reclaiming file cache, with nothing left for a second session).
  Starter ($7, 512 MB, 0.5 CPU) would work with `WHISPER_MODEL=tiny.en` at
  about 410-470 MB, but with under 100 MB to spare and more misheard words;
  documented in README as the cheaper option. Prices from Render's pricing
  as listed in October 2026 (render.com itself was unreachable from the build
  sandbox; check it when signing up).
- **CI**: new `Docker image (cloud)` job builds the image, starts it as
  Render would, checks `/healthz`, 401 on `/api/state` and `/silence.wav`,
  wrong and right logins, a signed-in `/api/state`, a 413 upload, the
  server's uid and `/data` owner, the voice WebSocket (`scripts/ws_smoke.py`:
  opens with cookie + Origin, 1009 over 2 MB, refused without the cookie or
  from another site), `--check`,
  one real spoken turn (Whisper + Piper inside the container) and prints the
  memory table to the job summary.
- Tests: `test_cloud.py`, `test_server_profile.py` (offline, fake Google
  credentials, mocked refresh). Not yet run on Render itself: README "Run it
  in the cloud (Render)" and MANUAL_TEST.md "Cloud server on Render".

## Voice conversation mode (phone web app) ✅
Hands-free, turn-by-turn conversation in the phone page, like voice mode in
the Claude apps. Tap "Voice conversation" once; End, "goodbye" or 2 minutes
of quiet ends it.
- Files: `assistant/voice_ws.py` (WebSocket route, limits, static files),
  `assistant/voice_session.py` (turn pipeline), `assistant/sentences.py`,
  `ClaudeChat.abort / mark_interrupted` in `chat.py`;
  `assistant/web/voice.js` (mic, playback, socket, UI),
  `voice-core.js` (DOM-free: endpointer, energy VAD, turn state machine,
  framing), `voice-worklet.js` (16 kHz capture), `voice.css`, and the
  vendored `web/vad/` (onnxruntime-web 1.22.0 + Silero v5, see its NOTICE).
  It is built on the web app's own pieces: auth through `require_session`
  (cookie or Bearer, lockouts, cross-site check), the browser's session and
  conversation through `WebAssistant.chat_stream(session, text, cancel)` and
  `answer(session, yes)`, and `transcribe` / `synthesize`. Edits to the
  phone-web files are additive: CSP `'wasm-unsafe-eval'`, a `voice=`
  argument and one `add_voice_routes()` call in `create_app`, `ws_max_size`
  in `serve`, eSpeak/Piper fast paths at the top of `render_wav`, and the
  button + view in `index.html`.

Design decisions:
- **Capture**: `getUserMedia` with echo cancellation, noise suppression and
  auto gain; one AudioContext for mic and playback, created and resumed
  inside the tap (iOS unlock), `navigator.audioSession = "play-and-record"`
  where supported. An AudioWorklet downsamples to 16 kHz 512-sample frames.
- **VAD**: Silero v5 in onnxruntime-web (single-threaded WASM, no
  cross-origin isolation needed), a ~20-line wrapper following
  @ricky0123/vad-web's (so its whole bundle isn't needed); everything served
  from `/static/vad/`, wasm/onnx gzipped on the fly and cached a week. If it
  fails to load, an adaptive energy VAD (noise floor x3) feeds the same
  endpointer. The endpointer: speech above `vad_threshold`, end of turn after
  `silence_ms` (700) below threshold-0.15, `min_utterance_ms` (300) or it's
  a misfire, `max_utterance_s` (30) cut, 320 ms of pre-roll kept. While a
  reply plays the threshold is raised by 0.25 so the speaker's echo doesn't
  count.
- **Transport: one WebSocket** rather than SSE + chunk fetches: both sides
  talk at any time (utterances and barge-ins up; transcript, text deltas
  and audio down), chunks go out the moment they're rendered with no
  per-chunk round trip (a phone on mobile data pays ~100+ ms per request),
  and a closed socket is an unambiguous cancel. Binary frames carry a small
  JSON header + payload (16 kHz PCM16 up, one WAV per sentence down).
- **Pipeline**: Whisper (shared, locked) -> `chat_stream` (Claude's
  `messages.stream`) -> sentence
  splitter (waits one character past a period, so "3:30 p.m. tomorrow",
  "Dr. Lee", "e.g.", "U.S.", "$3.50" stay whole, and "at 3 PM. Then"
  splits; long runs are cut at a comma) -> a per-turn TTS thread (through
  `WebAssistant.synthesize`, the web app's single TTS thread: Piper in
  memory, eSpeak directly on Linux for the pyttsx3 engine, SAPI to a file on
  Windows) -> WAV chunks. The page
  schedules chunks back to back in Web Audio. Text before a tool call ("Let
  me check.") is spoken while the tool runs.
- **Barge-in**: ~250 ms of confident speech while thinking or speaking stops
  playback immediately and sends `interrupt` with how much was heard (whole
  chunks + fraction of the current one). The server sets the turn's
  `cancel` event and closes the Claude stream at once (`ClaudeChat.abort()`,
  rather than waiting for its next delta), skips remaining TTS, and
  rewrites the turn's assistant text to just the heard part plus
  "[interrupted: Kyle started talking, so the rest of this reply was not
  said]"; tool calls/results and web-result blocks are kept, so the
  web-content confirmation rule still applies. Late chunks of a cancelled
  turn are dropped on the page by turn id.
- **Confirmations**: the session's "confirm" event (from the web app's
  confirmation gate) makes the voice view speak the question; the next
  utterance is transcribed and answers it through `answer(session, yes)`
  (strict `is_yes`) and is never sent to Claude. Talking over the question
  doesn't cancel the turn. No answer within `[webapp] confirm_timeout` is
  "no".
- **Conversation**: the phone's one web-app conversation (so push-to-talk
  and voice continue each other; tools and memory as in the web app); End,
  "goodbye", idle (2 min) or the 30 min cap ends it and stores the Pinecone
  summary; so does closing the socket. One voice socket per browser.
- **iOS**: start from the tap, Wake Lock (rejections ignored, re-requested
  when visible), pause on `visibilitychange`, AudioContext "interrupted"
  (calls, Siri) and mic track mute; "Tap to continue" resumes.
- **UI**: one orb (CSS radial gradient scaled by the live mic or output
  level), state label, captions (toggle remembered), Mute, End, latency
  panel. `prefers-reduced-motion` turns the scaling/pulse off; colors follow
  the light/dark preference.
- **Timings**: server `TurnTimer` stages from "utterance received"
  (transcript, first words, first sentence, first audio sent, whole reply;
  `TurnTimer` now takes custom stages and orders steps by time), logged
  always and printed with `--timings` in web mode; the page's panel adds
  its own clock (end of turn -> transcript -> first words -> first audio
  playing, and "after you stopped talking" = + `silence_ms`) and reports it
  back to the log.

Tested:
- `tests/test_sentences.py` (17): abbreviations, initials, decimals, times
  ("3:30 PM.", "p.m. tomorrow"), quotes, streaming token by token.
- `tests/test_voice.py` (22, on a plain-ClaudeChat backend): streamed
  tool rounds, `abort()` closing a stalled stream, `mark_interrupted`
  keeping web blocks; the chunker sends
  the first sentence's audio while the mocked Claude stream is still held
  open; barge-in mid-reply aborts the stream, skips unsynthesized sentences
  and truncates history to "First sentence here. Second one..."; barge-in
  after the reply finished; interrupt before any audio; spoken confirmation
  yes / no / unrelated answer (never sent to Claude), timeout; goodbye and
  memory; idle/max-length; End during a turn; API error spoken; real eSpeak
  render.
- `tests/test_voice_ws.py` (24): no/forged cookie and missing/foreign/`null`/
  look-alike Origin rejected (1008), X-Forwarded-Host only from a trusted
  proxy; a whole streamed turn over the socket;
  barge-in over the socket and the next request's history; spoken yes/no
  through the web app's confirmation (answer never sent as a request);
  voice continuing a push-to-talk conversation; closing the
  socket aborts generation and saves the summary; oversize/wrong-rate/junk
  frames (1009/1003), long text and message floods, utterance rate limit
  (1008), max session length; vendored files served byte-exact (SHA-256 in
  NOTICE) and gzipped; CSP; first-audio latency (fake Whisper/Claude, real
  eSpeak) and, in CI, with real Whisper; the JS tests via node.
- `tests/js/voice-core.test.js` (12, node:test, no packages): listening ->
  thinking -> speaking -> listening; barge-in (stop, position report, late
  chunks dropped, next turn); talking while it thinks; confirmation turn and
  cutting the question short; mute/pause/resume/end; endpointer silence,
  misfire, max length, strict mode; energy VAD in steady noise; framing.
  pytest runs them when `node` is on PATH (GitHub's Ubuntu and Windows
  runners have it), so CI needs no extra step.
- Browser smoke test (headless Chromium with a fake mic playing an eSpeak
  question, real page/CSP/WebSocket/Silero, fake Whisper and Claude, real
  eSpeak): states went listening -> thinking -> speaking -> listening for
  each turn; Silero loaded; with a mic clip that talks again over the reply,
  playback stopped and the next request's history held only the heard part
  ("Tomorrow you have the dentist at 3:30... [interrupted: ...]"); the
  energy fallback worked too; End closed the view.
- **Measured first-audio latency** (mocked Claude streaming at ~50 ms per
  delta): server, utterance received -> first audio sent 0.13-0.15 s; page,
  end of turn (VAD) -> first audio playing 0.17-0.21 s, i.e. ~0.9 s after
  the speech actually stopped (700 ms of that is the end-of-turn silence).
  pytest WebSocket round trip with real eSpeak: 27-74 ms. With **real
  Whisper** (base.en, CPU int8, GitHub Ubuntu runner, CI
  `test_first_audio_latency_with_real_whisper`): utterance -> first audio
  sent **0.60 s** (transcript 0.58 s). Add Claude's time to first token
  (typically 0.5-1 s) and the network: about 1.5-2 s after Kyle stops
  talking, within the ~2 s target on a decent connection.

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
- **Voice mode on a real phone is untested** (no iPhone in the sandbox):
  iOS audio routing with the mic open (speaker vs. earpiece), echo
  cancellation of the reply on the loudspeaker, and the interruption
  handling are in MANUAL_TEST.md. Chrome on desktop only cancels echo of
  WebRTC audio on some platforms, so there use earphones or a higher
  `[voice] vad_threshold`.
- Whisper sometimes "hears" a short phrase in noise; such a turn just gets a
  short answer. `min_utterance_ms` filters the shortest blips.
- Voice replies are sent as WAV (~44 KB per second of Piper speech); fine
  on Tailscale or LTE, could be Opus later.
