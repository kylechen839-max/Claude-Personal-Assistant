# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 1410 passed, 43 skipped (Linux build sandbox,
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
| `test_webapp.py` | 67 | phone web app (`--mode web`) |
| `test_cloud.py` | 76 | cloud server: auth wall, lockouts, proxy headers, limits |
| `test_server_profile.py` | 22 | server profile, Google secrets from env, server `--check` |
| `test_end_to_end.py` | 3 | "Hey Claude, what's on my calendar tomorrow?" |
| `test_docs.py` | 11 | docs match the code |
| `test_sentences.py` | 17 | voice mode: sentence splitter |
| `test_voice.py` | 33 | voice mode: turn pipeline, barge-in |
| `test_voice_ws.py` | 31 | voice mode: WebSocket, limits, files |
| `test_voices.py` | 38 | natural voice: Kokoro, ElevenLabs, engine choice, download, earcons |
| `test_history.py` | 67 | conversation history: store, search, resume, CLI |
| `test_history_web.py` | 21 | history in the phone app (API, incognito, voice) |
| `test_history_events.py` | 18 | history as sync events (skips without failover) |
| `test_failover_edge.py` | 51 | failover: /healthz, CORS, bearer tokens, WebSocket auth |
| `test_failover_sync.py` | 162 | failover: history event log merge, redaction and sync (memory and SQLite logs) |
| `test_failover_leader.py` | 216 | failover: leader check for scheduled jobs (incl. the real network topology) |
| `test_failover_eventlog.py` | 6 | failover: the SQLite event log |
| `test_failover_config.py` | 8 | failover: `[failover]` settings, `--role` |
| `test_failover_cloud.py` | 13 | failover: on Render behind CloudGuard (tokens, sync, voice socket) |
| `test_failover_webapp.py` | 18 | failover: the phone web app with a role (bearer, CORS, CSP, WebSocket) |
| `tests/js/backend-router.test.mjs` | 33 | failover: the phone app's backend router (`node --test`) |
| `test_gmail.py` | 28 | Gmail tools, scopes, email as untrusted input |
| `test_notes.py` | 9 | quick-capture notes |
| `test_lecture.py` | 28 | lecture uploads, pipeline, Notion database |
| `test_tutor.py` | 46 | tutor images, vision call, LaTeX read aloud |
| `test_webfeatures.py` | 25 | lecture/tutor routes in the phone app |
| `test_features_deploy.py` | 8 | lecture/tutor auth on local, failover and cloud; history hooks |

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
  there; a per-address lockout for wrong tokens (5 per 15 min, also wrong
  Bearer tokens) and, past 20 wrong tokens from everyone, a one-second delay
  on each further wrong one. The right token is always accepted (a review
  found the earlier global lockout let anyone lock Kyle out). Sessions are
  listed server-side (`[webapp] sessions_file`): sign-out revokes the cookie,
  "sign out everywhere" (`/api/logout-all`) revokes all and rotates the
  signing epoch; constant-time compare and generic errors (no
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
  itself is unchanged): `/healthz` -> `{"ok": true, "version": ...}` (no `busy`); every other route needs the
  cookie or Bearer token except the login page, its static files, login, the
  manifest and icons (unknown paths 401 too); sign-in limits through the web
  app's shared `LoginLimiter` (`BoundedLoginLimiter` caps its tables), keyed
  by the guard's client address, the right token always accepted; cookies
  checked against the web app's `SessionStore` (`sessions_file` on `/data`
  in the server profile); at most 2 body uploads per address / 32 in all, bodies
  due within 10 s + 1 s per 64 KB (408), no uvicorn `limit_concurrency`
  (it let one slow client 503 everything, `/healthz` included); sign-ins
  and wrong Bearer tokens in a separate pool (8, 2 s deadline) so they can't
  take signed-in slots, IPv6 slots per /56; no trusted
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
- **Confirmations** (tightened after a code review found that talking over
  a delete question left it pending, so the next "okay, and what about
  Friday?" deleted the event): the session's "confirm" event makes the
  voice view speak the question, bound to the turn that asked. It can only
  be answered after the page reports that the question's audio played to
  the end (`played` message). Talking over the question, or saying anything
  before it was heard, is a **no**, and it says "OK, I won't delete it."
  Only a short explicit yes counts (`is_explicit_yes`: "yes", "yeah, delete
  it", "go ahead"); anything with other content ("okay, and what about
  Friday?", "yes but not that one", a new question) is a no, spoken the
  same way. The answer goes through `answer(session, yes)` and is never sent
  to Claude. No answer within `[webapp] confirm_timeout` is "no".
- **One turn at a time**: turns run on one worker thread per conversation.
  A new utterance cancels the running turn and waits for it (a tool that is
  mid-call finishes first, then the turn stops before asking Claude again);
  a confirmation from a cancelled turn is declined, never attached to the
  next one. Only the newest waiting utterance is kept: older ones are
  dropped before Whisper runs, so transcription work can't pile up. A
  barge-in after Claude asked for a tool but before it ran stops it: the
  chat loop checks `cancel` before every tool call (skipped calls get an
  error tool_result, so history stays valid). A voice turn's tool, confirm
  and status events go straight to it (`chat_stream(on_event=...)`), never
  through the session queue a page may be reading. Server-wide at most 4
  voice conversations; rate limits are per browser session, so
  reconnecting doesn't reset them. Transcripts are logged only at DEBUG.
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
- `tests/test_voice.py` (33, on a plain-ClaudeChat backend): streamed
  tool rounds, `abort()` closing a stalled stream, `mark_interrupted`
  keeping web blocks; the chunker sends
  the first sentence's audio while the mocked Claude stream is still held
  open; barge-in mid-reply aborts the stream, skips unsynthesized sentences
  and truncates history to "First sentence here. Second one..."; barge-in
  after the reply finished; interrupt before any audio; spoken confirmation
  yes / no / unrelated answer (never sent to Claude), timeout; goodbye and
  memory; idle/max-length; End during a turn; API error spoken; real eSpeak
  render; only an explicit yes confirms (10 answers incl. "okay, and what
  about Friday?"), barge-in over the question cancels the delete and says
  so, an answer before the question was heard is a no, turns never overlap
  and a cancelled turn's confirmation is declined, superseded utterances are
  never transcribed; no tool runs after a barge-in that lands before the
  tool call; `chat_stream(on_event)` keeps the turn's events off the shared
  queue.
- `tests/test_voice_ws.py` (31): no/forged cookie and missing/foreign/`null`/
  look-alike Origin rejected (1008), X-Forwarded-Host only from a trusted
  proxy; a whole streamed turn over the socket;
  barge-in over the socket and the next request's history; spoken yes/no
  through the web app's confirmation (yes, no, "okay, and what about
  Friday?", barge-in over the question; the answer is never sent as a
  request);
  voice continuing a push-to-talk conversation; closing the
  socket aborts generation and saves the summary; oversize/wrong-rate/junk
  frames (1009/1003), long text and message floods, utterance rate limit
  (1008), and across a reconnect, malformed interrupt/played (1003), the
  server-wide conversation cap (1013), max session length; vendored files served byte-exact (SHA-256 in
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
  energy fallback worked too; End closed the view; a delete confirmation
  was answered "yes" only after the page reported the question as played,
  and the event was deleted; tapping End 0-800 ms after Voice left no
  socket (or mic) open.
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

## Email, notes, lectures and homework tutor ✅
Four features on top of the seven stages (branch `claude/features`).

- **Gmail** (`assistant/tools/gmail.py`, `google_auth.py`): `email_summary`
  (unread/important in the last N hours, capped, sender/subject/one-line
  gist), `read_email` (plain text, quoted thread and HTML stripped,
  trimmed), `draft_reply` (a draft in the same thread, after a yes). No send
  tool; `gmail.compose` (needed for drafts) could send, so the service is
  wrapped in `NoSendGmail` (send/delete/settings raise) and a test fails on
  any send call; Gmail scopes are requested only when `[gmail]` is enabled.
  Email text is never logged (any mode) and never stored in memory
  (summaries of such conversations use Kyle's words only). Sign-in moved to `google_auth.py` (one token for
  Calendar + Gmail; `GOOGLE_TOKEN_JSON` for the cloud). An old token keeps
  Calendar working; Gmail stays off with a message naming
  `python -m assistant.tools.google_auth`, also in `--check`. Email results
  count as untrusted content (`Tool.untrusted_output`,
  `chat.has_untrusted_content`), so changes ask first; `Tool.private`
  keeps email text and replies out of the log.
  Tested: mocked Gmail service, prompt-injection emails x 5 actions x 2
  readers, scope detection, cloud token, log check.
- **Quick notes** (`assistant/tools/notes.py`): `remember_note`,
  `recall_notes`, `list_recent_notes`, `delete_note` in Pinecone
  (`voice-assistant`, `kind=note`, `created_at`, tags). When notes are on
  they replace the plain `remember` memory tool; `search_memory` stays.
  Deletes ask and
  only touch `note-` ids. `QuickNotes.on_saved` is the hook for showing
  notes in conversation history once that lands (via the history module's
  public functions, not its store). Tested with an in-memory fake index.
- **Lecture recorder** (`assistant/lecture.py`, `webfeatures.py`, Lecture
  tab): offset-based resumable uploads (gap -> 409 with the server's
  offset, repeats ignored, size caps), a background job (one at a time,
  resumes after restart) that decodes with PyAV in 10-minute windows
  (faster-whisper's own file decoder fails with PyAV 19, and windows keep
  memory flat), transcribes with progress, summarizes with a forced Claude
  tool call, finds or creates "Lecture Notes" next to Assignments, and
  files a page with inline/block equations, idempotently (Lecture ID
  property + local page record; a retry appends only missing blocks). Assignment-like action items
  are offered with Add buttons. Tested: resume after a lost chunk,
  concurrent resends, pipeline with mocked Whisper/Claude/Notion, retry
  without re-transcribing, DB find/create/refused paths, real WebM/Opus and
  MP4/AAC decoding, and (CI) real Whisper on a generated WebM lecture.
  The page was also driven in headless Chromium with a fake microphone.
- **Homework tutor** (`assistant/tutor.py`, `mathspeech.py`, Tutor tab):
  client-side resize to 1600 px, server allowlist (JPEG/PNG/WebP by
  content), size and pixel caps, EXIF stripped by re-encoding, Socratic
  prompt with full solutions only on request, KaTeX vendored under
  `/static/katex` and drawn with `katex.render` (CSP-safe), spoken replies
  with LaTeX read aloud. Tested: auth on every route, type/size rejection,
  EXIF removal, mocked vision call, 27 LaTeX-to-speech forms.
- Integration (after history, cloud-host and failover landed): a saved
  note is added to the current conversation's history and a filed lecture's
  summary to the phone conversation it was recorded from, both through
  `history.record_event` only. The lecture/tutor routes are installed inside
  `webapp.create_app` (before the failover wrapper), so they need the same
  sign-in on the local web app, a failover backend (bearer token, CORS from
  the app origin; chunks are POSTs) and the cloud server (CloudGuard: page
  code public, API not; lecture chunks and tutor photos get the upload size
  cap, and `chunk_mb`/`max_image_mb` are lowered to fit `[webapp]
  max_upload_mb`). With failover the page pins the backend while a
  recording uploads. Google sign-in from cloud-host and this branch now
  live together in `tools/google_auth.py`. Tests:
  `tests/test_features_deploy.py`.

## Pi preflight ✅
Everything testable before the move to a Raspberry Pi, in one pass per
machine, with a go/no-go (`PI_MIGRATION.md`).

- **Simulated Pi in CI** (`.github/workflows/pi-arm64.yml`,
  `scripts/ci/pi_simulation.sh`): Debian Bookworm linux/arm64 with Python
  3.11.2 (what 64-bit Raspberry Pi OS is) on GitHub's native
  `ubuntu-24.04-arm` runner, in a `debian:bookworm` container, as a normal
  user `pi` with sudo. It runs `install_pi.sh` end to end (apt, venv, pip,
  openWakeWord `--no-deps`, models), then again (must be a no-op), checks
  the aarch64 wheels import (onnxruntime, ctranslate2, piper, openwakeword,
  sounddevice), downloads the Piper voice, runs the **full pytest suite**,
  `python -m assistant --check`, `install_service.sh` (systemctl/loginctl
  stubbed and their calls checked) and `preflight.sh`, which runs the real
  wake word -> VAD -> Whisper -> reply on the committed fixture, both TTS
  engines, and `--mode web` with a text and an audio turn. Stubbed only:
  systemd, the sound card (an ALSA `null` device) and Claude
  (`scripts/ci/fake_claude.py`, no key in CI). Runs on every push to
  master, on pushes that touch the Pi files, and by hand (with a QEMU
  option). First green run: about 1.5 minutes in total (install 42 s,
  pytest 17 s, preflight 8 s) with downloads cached.
- **Preflight** (`assistant/preflight.py`, `scripts/preflight.ps1` for
  Windows PowerShell 5.1+, `scripts/preflight.sh`): git, .env, `--check`,
  pytest offline and live (live skips are a FAIL), the wake-word end to end
  on `tests/fixtures/hey_claude_calendar_tomorrow.wav` (asserts a wake, a
  transcript matching the phrase and a non-error reply), text-mode calendar
  and Notion questions (the log must show both tools called), web mode on
  127.0.0.1 (healthz, auth refusal, login with `WEB_TOKEN`, text and audio
  turn), pyttsx3 and Piper renders, and with `--hardware` the audio device
  list and a 3 s mic level. Table + `logs/preflight-<timestamp>.txt`.
  Every `.env` value is masked in the console and the report.
- **Smoke check** (`assistant/smoke.py`, `scripts/cloud_smoke.ps1`/`.sh`):
  the same web checks against any base URL (Render, or the Pi), plus a
  hands-free turn over the voice WebSocket where the server has it.
- **The PC as the always-on host** (`scripts/install_windows_service.ps1`,
  `scripts/windows_host.py`): Task Scheduler tasks `\HeyClaude\Wake` and
  `\HeyClaude\Web` at sign-in, hidden (pythonw), kept running by a small
  supervisor (network wait, restart after 10 s, 5-minute pause after 5
  crashes in 5 minutes; Task Scheduler's restart-on-failure doesn't see the
  assistant's exit codes), `-Uninstall`; a power check that, after a typed
  `y`, sets never-sleep/never-hibernate on AC and turns off USB selective
  suspend. The preflight's `power` row WARNs on Windows when sleep is on.
  The supervisor is `assistant/host.py` (`python -m assistant.host`), also
  used by the Pi's systemd unit.
- **Automatic updates** (`assistant/updater.py`, `assistant/host.py`,
  `assistant/version.py`, `assistant/web/update-watch.js`): opt-in `[update]
  enabled`, follows the `stable` branch (fast-forwarded only after master's
  CI is green on all jobs). Fast-forward only; skips with local changes to
  tracked files (config.toml excepted), local commits or a commit that
  failed before; pip install when requirements.txt changed; smoke (import +
  `--check`); restart when idle (loopback-only `busy` in /healthz) and
  verify /healthz `version`; any failure -> `git reset --keep <old>`, old
  requirements back, commit rejected, loud log. A lock file keeps the two
  PC hosts and `--update` from updating at once; each host restarts its own
  child when HEAD moves. The page reloads once when /healthz's version
  changes, only when quiet. Tested with real git repos and a bare remote
  (fast-forward, up to date, master-only push ignored, diverged, dirty,
  config.toml edits kept, config conflict, failed smoke/pip -> rollback,
  rejected commit, lock), the host's restart/verify/rollback with fakes,
  /healthz version/busy, and update-watch.js in node.
- **Update hardening (review cycle 2)**: fail-closed signatures (the new
  stable commit or a `stable-<sha>` tag must verify against an
  allowed_signers file outside the checkout; only `trust_unsigned = true`
  skips it), origin URL check, only `local_branch` is updated, no automatic
  pip (a requirements change needs `--update` by hand with the assistant
  stopped, detected via `logs/host-*.alive`), failed rollback pip = FAILED,
  HEAD moves made by hand only restart, `config.local.toml` (gitignored)
  for per-machine settings, `.github/workflows/promote-stable.yml` moves
  `stable` only when `tests` and `pi-arm64` both passed on that commit
  (signed tag with the `STABLE_SIGNING_KEY` secret). Tests sign with a real
  SSH key: unsigned, unknown key, signed tag, non-stable tag, missing
  allowed_signers, trust_unsigned, wrong origin, other branch. Not done:
  hash-pinned requirements (no single lock fits Windows x64, Linux x86_64
  and aarch64 with this dependency set); mitigated by never running pip
  automatically.
  Tested: supervisor restart/back-off/network wait and the powercfg parsing
  in pytest; the .ps1 parses and is PSScriptAnalyzer-clean (checked with
  PowerShell 7 here and Windows PowerShell 5.1 in CI). Not run on a real
  Windows Task Scheduler yet: MANUAL_TEST.md.
- **Tests** (`tests/test_preflight.py`): result parsing (junit, wake and
  text output, mic levels), verdicts, every step's PASS/FAIL/SKIP rules with
  faked commands, the smoke HTTP flow against a fake app, and redaction: a
  fake `.env` whose values are echoed by every command never appears in the
  console or the report. shellcheck and `bash -n` on every .sh; the .ps1
  files are parsed by Windows PowerShell 5.1 and PSScriptAnalyzer in the
  Windows CI job. `tests/test_docs.py` also covers `PI_MIGRATION.md`, the
  preflight/smoke flags, step names and script paths.

## Natural voice (Kokoro, optional ElevenLabs, earcons) ✅
Kyle asked for replies and voice-mode sounds that feel like the Claude
app's voice conversations. The app's voices and sounds are proprietary
(no API, not extracted or imitated), so this uses the closest open
equivalent and original sounds.
- **Kokoro-82M** (`assistant/tts_kokoro.py`, `kokoro-onnx`, Apache-2.0):
  `KokoroTTS.synthesize(text) -> (float32, 24000)`, so the web app's
  `fast_wav`/`render_wav` render it in memory with no change to the voice
  pipeline. Model: **int8** (`kokoro-v1.0.int8.onnx`, 88 MB, + 28 MB voice
  pack), the smallest; resident memory ~300 MB for a Python process with the
  model loaded (fp16: ~420 MB, fp32: ~570 MB, measured here), which fits a
  Render instance with Whisper. `speak()` (desktop modes) renders sentence
  by sentence in a thread while the previous one plays. Calls are
  serialized by a lock; the language comes from the voice prefix (`a` =
  en-us, `b` = en-gb).
- **Default voice: `af_heart`**, speed 1.0. It is the highest-rated voice
  on the Kokoro model card and warm, unhurried and conversational, the
  closest match to the Claude app's calm voice-conversation feel among the
  candidates. Alternatives, documented in README "Voices": `af_bella`
  (brighter, more animated), `bf_emma` (British, soft and composed),
  `am_michael` (male, relaxed). The choice was made from the model card's
  quality grades and descriptions plus intelligibility (Whisper transcribes
  af_heart's render correctly in CI); I could not listen in the sandbox, so
  MANUAL_TEST.md step 9 has a listen check with the alternatives to try.
- **Setup-time download**: `python -m assistant --download-voice kokoro`
  (or `kokoro-fp16`; a Piper voice name works too) fetches the files from
  the kokoro-onnx GitHub release into `models/kokoro/` (gitignored). Each
  file's SHA-256 is pinned in `tts_kokoro.FILES`: data goes to `.part`
  while hashing and is renamed only on a match, so a mismatch or an
  interrupted download leaves nothing behind; verified files are kept,
  corrupt ones re-fetched. Nothing is downloaded at request time or by the
  browser.
- **Engine choice**: `[tts] engine = "auto"` (new default, in DEFAULTS and
  config.toml): Kokoro if its files and package are there, else Piper, else
  pyttsx3, with one log line saying which and why. On ARM Linux (the Pi)
  "auto" prefers Piper when both exist. Forcing `kokoro`/`piper` falls down
  the same list with a warning. New settings: `kokoro_voice`, `speed`,
  `kokoro_model`, `kokoro_voices`, `elevenlabs_voice`, `elevenlabs_model`.
  `--tts` accepts every engine. `--check` reports the voice in use.
- **Warm-up**: the web app queues `tts.warm_up()` in its TTS thread when it
  starts (logged with its time), and the desktop modes warm up in a
  background thread, so the first reply doesn't pay the model load
  (0.8-2 s here). Piper got a `warm_up()` too.
- **ElevenLabs** (`assistant/tts_elevenlabs.py`, optional, paid): only with
  `engine = "elevenlabs"` *and* `ELEVENLABS_API_KEY`; streams raw 24 kHz
  PCM per sentence from `/v1/text-to-speech/{voice}/stream`
  (`eleven_flash_v2_5`), 8 s timeout. On any error that sentence is spoken
  by the local engine (Kokoro -> Piper -> pyttsx3) and the cloud is skipped
  for 60 s. The key is sent only in the `xi-api-key` header; log lines give
  only the error type and message (and the key was already redacted from
  logs).
- **Earcons** (`assistant/earcons.py`): original sounds from sine waves
  (a little octave overtone, 12 ms attack, exponential decay, 20 ms fade to
  zero, so no clicks): *listening* rises a fifth (D5 -> A5, 0.35 s, peak
  0.25), *thinking* is one quiet G4 (0.16 s, peak 0.08), *ended* falls a
  fifth (0.41 s, peak 0.2). The WAVs are committed in
  `assistant/web/earcons/` (8-20 KB) and served by the web app at
  `/earcons/<name>.wav` (whitelisted names, same origin, so the self-only
  CSP is untouched). `voice.js` fetches and decodes them when the view
  opens and plays them on state changes: listening when the mic first opens
  or after a pause, thinking when a turn is sent, ended in `finish()`. They
  go straight to the speakers, not through the orb's level meter, and a
  failed load just means no cue. The desktop wake chime and follow-up blip
  now use the listening and thinking sounds (the old 880/1320 Hz beeps are
  gone). `voice_session.py` and `voice_ws.py` are unchanged.

**Latency** (time to render a reply's first sentence, "Tomorrow you have
the dentist at three thirty." = 2.4 s of audio, warm model, median of 5;
this is what voice mode waits for before the first audio, on top of
Whisper and Claude):

| Where | Kokoro int8 | Piper lessac-medium |
|---|---|---|
| Build sandbox, 4 x86 vCPUs | 0.57-0.93 s (best 0.54 s) | (voice download blocked) |
| CI, Ubuntu runner (4 vCPUs, Python 3.12) | 1.09 s (load + warm-up 1.3 s) | 0.11 s (load + warm-up 1.7 s) |
| CI, Windows runner (4 vCPUs) | 1.25 s (load + warm-up 2.2 s) | 0.115 s (load + warm-up 3.9 s) |

Shared runners vary a lot: on one slow run the same sentence took 5.0 s
with Kokoro (Piper 0.13 s), so the test only checks that the model works,
not a speed limit. So on CI-class CPUs Kokoro adds about 1 s to time-to-first-audio compared
with Piper (~10x the render time). In voice mode, first audio becomes roughly
Whisper 0.6 s + Claude's first sentence + ~1 s of Kokoro, about 2.5-3 s
after Kyle stops talking, against ~1.5-2 s with Piper. It stays the default
because it sounds far more natural, and a desktop CPU is usually faster
than a CI runner. If the wait feels too long, `[tts] engine = "piper"`
brings it back to ~0.1 s (MANUAL_TEST.md step 9 compares the two).

- **First piece split at a clause** (`SentenceSplitter(first_clause_words=4)`,
  the default for streaming, so voice mode gets it with no change to
  `voice_session.py`): a reply's first piece also ends at a comma,
  semicolon or spaced dash once it has 4+ words ("3,000" and "9, 10, and
  11" are not breaks), so Kokoro renders a short piece first; later
  sentences are unchanged, and `split_sentences()` (whole texts) keeps whole
  sentences. Kokoro adds a 0.12 s pause after a piece ending at a clause
  break, since its trimming removes the comma's pause. A long first sentence
  ("Tomorrow you have the dentist at three thirty, and after that you have
  a study group at the library until six.") then renders its first audio
  in 0.56 s instead of 1.32 s in the build sandbox, and 0.42 s instead of 1.08 s on the CI Ubuntu runner (Piper: 0.05 s instead of 0.12 s).
- Kokoro's render time is about 0.15 s + 0.2 x the audio length ("Sure.":
  0.24 s; a 6 s sentence: 1.3 s), so a long first sentence costs more; the
  sentences after it render while the first plays (real-time factor ~0.25),
  so there are no gaps. Threads: 1 thread 1.66 s, 2 threads 0.92 s, 4
  threads 0.71 s for the same sentence; fp16 was ~20% faster here but twice
  the size and memory, so int8 stays the default.
- Browser smoke test (headless Chromium, fake mic, energy VAD, real page and
  WebSocket, fake Whisper, Claude mocked at 30 ms per word, real Kokoro on
  the same 4 vCPUs as the browser): states connecting -> listening ->
  thinking -> speaking -> listening; the listening cue (0.35 s) played when
  the mic opened, the thinking cue (0.16 s) when the turn was sent, then two
  Kokoro chunks (2.41 s, 1.30 s), and the ended cue on End. All three
  earcons were fetched with 200, no CSP errors. End of turn -> first audio
  was ~1.5 s there (vs ~0.2 s with eSpeak), mostly Kokoro's render with
  the browser competing for the CPU.
- **Raspberry Pi**: not measured on a real Pi. A Pi 5's cores are roughly
  half as fast as these, which puts Kokoro at ~1.2-1.5 s for a first
  sentence (near real time on one core), against ~0.2 s for Piper. So
  **Piper stays the Pi default** ("auto" prefers it on ARM). MANUAL_TEST.md
  has an optional step to measure Kokoro on the Pi.

Tested (`tests/test_voices.py`, all offline): auto picks Kokoro -> Piper ->
pyttsx3 with one log line, Piper on ARM, forced-engine fallback, missing
package/files; config defaults and switching voice/speed through
config.toml; KokoroTTS with a fake model (voice, language, speed clamp,
unknown voice, sentence-by-sentence `speak` with one `on_audio_start`, a
render error re-raised, WAV files, the web app's in-memory render and the
warm-up queued before the first request, the CLI's warm-up); the download
with a mocked network (checksum verified, mismatch and HTTP error leave no
files, kept/corrupt files, fp16, bad variant, the CLI command and its
wiring without an API key); ElevenLabs with a fake session (request shape,
PCM decoding, speed clamp, fallback on 401/network/empty with the key never
logged, cooldown, no key -> local voice, file-only fallback engines);
`--check`; earcons (files exist, 0.1-0.5 s, < 30 KB, gentle peaks, start
and end at zero, match the generator, served and wired into voice.js with
no external URLs). With the real model (downloaded and cached in CI,
skipped when it can't be had): a WAV render (American and British), every
listed voice is in the voice pack, the latency bench above, and Whisper
transcribing Kokoro's "What is on my calendar tomorrow" correctly
(`test_stt.py`). The real-model tests run in a child process (see Known
issues).
## Conversation history ✅
- `assistant/history.py`: `HistoryStore` on stdlib SQLite (WAL,
  `synchronous=FULL`, versioned migrations in `MIGRATIONS` via
  `PRAGMA user_version`). Tables `conversations` (random 16-char id, owner,
  source wake/text/web/voice, timestamps, title, summary, pinned, archived),
  `messages` (seq, role, kind chat/confirm, text, interrupted, compact
  `tool_events` JSON) and `memory_vectors` (Pinecone ids). FTS5 index over
  messages, titles and summaries kept in sync by triggers, bm25 ranking
  (titles weigh more), snippets with highlights, prefix matching, filler
  words dropped, any-word fallback; LIKE search if FTS5 is missing.
  Retention (`retention_days`) and a size guard (`max_mb`) skip pinned
  conversations.
- Recording: `ClaudeChat(recorder=...)` saves the user turn before calling
  the API and the reply after, with spoken/typed delete confirmations
  (question and the heard answer) and `mark_interrupted()` for barge-in.
  `end_conversation()` hands the conversation to a background thread that
  asks `[history] title_model` for a title (<= 6 words) and summary, with the
  first user message as the fallback. Conversations left open by a crash are
  titled at the next start. The Pinecone summary now stores
  `conversation_id` in its metadata, and its vector id is kept for deletes.
- Search: FTS ranked keyword hits, then Pinecone semantic hits that link to
  a conversation; filters by date range, source, pinned, archived.
- Continue: `rebuild_history()` turns stored messages into plain-text turns
  (tool events and confirmations as text, interrupted replies noted),
  trimmed to `max_history` with a summary of older turns first.
  `ClaudeChat.resume(id)`; tools `search_conversations` (titles, dates,
  snippets) and `resume_conversation` (asks "Continue '...' from Tuesday?",
  switches after the reply; a conversation that was only the "continue..."
  request is dropped, a real one is saved first).
- CLI: `--history list|search|show|export|delete`, `--format md|json`,
  filters `--source/--since/--until/--pinned`, `--yes`, `--continue ID|last`,
  `--incognito`.
- Tests (`test_history.py`): CRUD, owner scoping, id randomness, migrations
  (upgrade, rollback, newer schema refused, FTS built later), a real crash
  in a subprocess then reopen, FTS and LIKE ranking/snippets/filters (each
  test runs on both), retention and size guard, titles with a mocked API and
  the fallback, off-the-hot-path titling, recording through ClaudeChat
  (tool events, confirmations, errors, interruptions, incognito), resume
  rebuilding and trimming, the tools via mocked Claude tool calls (search,
  resume yes/no, mid-conversation, unknown id), semantic merge, delete
  cascading to a mocked Pinecone (and not deleting when Pinecone fails),
  export redaction, and the CLI commands. Performance: 5,000 seeded
  conversations (50,000 messages), median search under 100 ms (about
  30-55 ms in the sandbox even on a tiny vocabulary where every word
  matches most messages).
- Events: every change is a failover sync `Event` (message, rename with
  the auto title/summary or Kyle's title, pin with archived, delete,
  delete_message for barge-in edits) recorded in failover's
  `SqliteEventLog` in the same file; the tables above are a projection of
  the log, written only by one idempotent `_apply()` used for local and
  synced events (`HistoryStore.sync_log` is the EventLog to give the
  Syncer). Last writer wins on title/summary/pin by (ts, id); tombstones
  redact and purge the search index. Startup replays events after a saved
  cursor, so a crash between the log and the tables heals. Without the
  failover modules it runs standalone (nothing to sync).
- Review fixes: resumed text and history tool results count as untrusted
  (changes ask first); a turn whose API call fails takes its saved user
  message back (a retry isn't saved twice); a title written in the
  background after the conversation was resumed doesn't mark it ended.
- For other features: `history.current_conversation_id()` (inside a turn)
  and `history.record_event(conversation_id, kind, text)` add a note (e.g. a
  lecture summary or quick capture) to a conversation, shown in its
  transcript and search and passed to Claude when it's continued.
- Phone app: `assistant/history_web.py` adds `GET /api/conversations`
  (paginated, `q`, source/pinned/archived/date filters, highlight parts),
  `GET /api/conversations/{id}`, `POST .../continue`, `PATCH` (rename, pin,
  archive), `DELETE` (with the Pinecone cascade; also clears the
  conversation from any open session), `GET .../export?format=md|json`, and
  `GET/POST /api/incognito`, all behind the web sign-in. Push-to-talk turns
  are saved as `web`, voice-mode turns (`chat_stream`) as `voice`, and a
  barge-in keeps only the spoken part, flagged interrupted.
  `assistant/web/history.js`: History screen (debounced search, day groups,
  filter chips, swipe and long-press actions, transcript, Continue),
  rename/delete dialogs, incognito switch and badge, light theme.
- Tests: `test_history_web.py` (401 on every route, cross-site 403, id
  guessing and other owners get 404, pagination, highlights, semantic hits,
  filters, rename/pin/archive, delete cascade and failure, export, continue,
  incognito, web and voice recording with barge-in) and
  `test_history_events.py` (each change is an event, projection ==
  `materialize(events)` over random operations, two-store sync in any
  arrival order, late events under tombstones, last-writer-wins, startup
  catch-up, no deleted text left in the database file). Driven in headless
  Chromium at phone size, dark and light: search highlights, groups,
  transcript, continue then a turn, pin filter, rename, delete dialog,
  swipe, infinite scroll, incognito; no console errors.

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
- **Real Pi not tested:** the arm64 CI job proves the install, packages,
  models and pipeline on Bookworm arm64, but not real audio devices,
  systemd lingering or Pi latency; those are `preflight.sh --hardware` and
  MANUAL_TEST.md's Pi part.
- **Fixed (2026-10-18): `[wake] mic_device = 0`** silently opened the
  default mic (`device or None`), and push-to-talk ignored `mic_device`
  altogether, while `--check` reported the configured device as fine. Now
  one resolver (`audio.resolve_mic_device`) is used by wake mode,
  push-to-talk, `--check` and the preflight; `--check` shows the device
  that will actually open and fails if an index resolves to another one.
- Two Notion databases are named "Assignments"; the Fall 2026 one
  (`7eefabe3-...`) is configured.
- `piper-tts` is GPL-3.0; fine for personal use. Kokoro's model is
  Apache-2.0, `kokoro-onnx` is MIT, and it pulls in `phonemizer` (GPL-3.0)
  and a bundled espeak-ng (GPL-3.0); also fine for personal use.
- **Kokoro and pyttsx3 can't share a process on Linux**: kokoro-onnx loads
  its own libespeak-ng, and pyttsx3's eSpeak driver then crashed the process
  in the test suite. The app only ever builds one engine (and the web app's
  pyttsx3 path runs the espeak-ng program, not the library), so this only
  affects tests; the real-Kokoro tests run in a child process.
- **Kokoro needs onnxruntime 1.25+, so Python 3.11+.** With onnxruntime
  1.23 (the last build for Python 3.10) and 1.24, the int8 model's output on
  some CPUs is noise (peaks around 1e29) that kokoro-onnx's silence trimming
  cuts down to nothing, and it renders ~4x slower; reproduced here, and it
  made the Python 3.10 CI job flaky (an empty WAV on one run, 5 s renders on
  another). 1.25, 1.27 and 1.31 are fine. requirements.txt installs Kokoro
  only on 3.11+ with `onnxruntime>=1.25`, `kokoro_problem()` reports an
  older onnxruntime (so "auto" falls back to Piper), and `synthesize()`
  raises rather than return empty, non-finite or out-of-range audio.
- **Kokoro's voice hasn't been heard by a person yet** (no audio in the
  sandbox): MANUAL_TEST.md step 9 and the voice-mode listen check.

## Failover: Pi -> PC -> cloud (phase A) ✅
The phone app's backend failover, documented in FAILOVER.md.
- `assistant/web/backend-router.js`: the client router as a standalone ES
  module: parallel `/healthz` probes (1.5 s tailnet, 3 s cloud), highest
  priority healthy backend with a 300 ms grace for a slower better one, API
  version check, one retry of idempotent requests on the next backend,
  writes never replayed (`NeedsUserRetry`), 60 s background re-probe with
  fail-back after 2 good probes, no switch mid-turn, Auto / forced override,
  IndexedDB token store.
- `assistant/failover/edge.py` + `tokens.py`: ASGI middleware for every
  backend: `/healthz` (ok, api, role only), CORS for the app origin only (no
  credentials), derived bearer tokens (`POST /api/auth/token`, refresh,
  scopes app/sync, 90-day session limit), WebSocket first-message auth
  (4401 / 4408), per-backend lockout.
- `assistant/failover/sync.py`: history as an append-only, id-addressed
  event log; `materialize()` is order-independent and idempotent (LWW
  renames/pins on (ts, id), permanent tombstones); push/pull endpoints and
  client with cursors and peer-reset detection; `MemoryEventLog` reference
  store (the SQLite store from claude/history implements the same API).
- `assistant/failover/leader.py`: leader check (probe higher-priority peers
  right before firing) and a run-once job runner with a per-job dedupe key
  in the shared log.
- `--role pi|pc|cloud`, `[failover]` settings (`ASSISTANT_ROLE`,
  `APP_ORIGIN` env overrides).
- Tests (offline): three-fake-backend simulation on a virtual clock (node),
  CORS rejection and 401s everywhere incl. the WebSocket, property-style
  sync convergence with three stores, random interleavings, duplicates and
  outages, tombstones, leader election with flapping peers and partitions.
  CI runs the node tests in a `backend-router` job.
- Phase B, phone web app (done): with a `[failover] role`, `--mode web` is
  wrapped in the edge middleware, its CSP lists the backends, and the page
  switches to bearer tokens + the router, with a Pi / PC / Cloud badge and
  a Backends section in Settings. Checked in headless Chromium against
  three live local backends: Pi; Pi killed mid-conversation -> "Pi didn't
  answer; tap to try again" (not replayed) -> the tap goes to the PC; PC
  killed -> Cloud; Pi back -> Pi. That run found a real bug the node tests
  couldn't (window.fetch called as a method), now fixed and tested.
- Phase B, the rest (done): history sync runs on the Pi and PC against
  the history store's `sync_log`, and every backend serves the sync
  endpoints on it; the voice view connects to the active backend and sends
  the token as its first message (`?auth=first`), with no backend switch
  during a voice session; on Render, `CloudGuard` accepts the derived tokens, keeps the
  token trade public and passes first-message sockets through
  (`ASSISTANT_ROLE`, `APP_ORIGIN`, `FAILOVER_BACKENDS` in render.yaml,
  empty = off). Tests: `test_failover_cloud.py`, incl. a real Pi
  HistoryStore syncing into the cloud's over HTTP through the guard.
- Review fixes: jobs elect the highest-priority claim (optionally preferring
  on-time ones), always someone; a claim without `done` expires after 10
  minutes; a `delete_message` can't hit a tombstone or job; the SQLite log
  checkpoints its WAL after a delete and waits on a busy database.

Known issue: a conversation in progress lives in each backend's memory, so
after a failover mid-conversation Claude starts it fresh (it is in the
synced history list; Pinecone memory still works).
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

- **Lectures need the app in the foreground.** Phones pause web pages that
  are in the background or locked; the page holds a screen wake lock, but
  switching apps during a lecture can stop the recording on iPhone.
- **Gmail and lectures untested with real accounts** here (no keys in the
  sandbox): MANUAL_TEST.md "Email, notes, lectures, tutor".
