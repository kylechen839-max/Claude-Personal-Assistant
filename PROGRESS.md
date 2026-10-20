# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 1474 passed, 45 skipped (Linux build sandbox,
Python 3.13, 2026-10-19; that sandbox had no eSpeak, PortAudio or
shellcheck, so 25 audio tests failed or errored there, as before this
change, and the launcher shellcheck test skipped), plus 124
JavaScript tests (node:test: `voice-core.test.js` and
`agent-dashboard.test.mjs`, both also run by pytest).
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
| `test_barge_in.py` | 42 | barge-in on the PC loop (talk over a reply), with follow-ups |
| `test_tools.py` | 27 | stage 5 |
| `test_web.py` | 17 | web search / browser |
| `test_security.py` | 36 | tool safety, log redaction |
| `test_memory.py` | 12 | stage 6 |
| `test_memory_tools.py` | 7 | stage 6 (memory tools) |
| `test_config_cli.py` | 14 | stage 7 |
| `test_doctor.py` | 10 | `--check` |
| `test_timing.py` | 9 | `--timings` |
| `test_webapp.py` | 80 | phone web app (`--mode web`) |
| `test_cloud.py` | 78 | cloud server: auth wall, lockouts, proxy headers, limits |
| `test_server_profile.py` | 22 | server profile, Google secrets from env, server `--check` |
| `test_end_to_end.py` | 3 | "Hey Claude, what's on my calendar tomorrow?" |
| `test_docs.py` | 25 | docs match the code |
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
| `test_failover_cloud.py` | 17 | failover: on Render behind CloudGuard (tokens, sync, voice socket) |
| `test_failover_webapp.py` | 18 | failover: the phone web app with a role (bearer, CORS, CSP, WebSocket) |
| `tests/js/backend-router.test.mjs` | 33 | failover: the phone app's backend router (`node --test`) |
| `test_gmail.py` | 28 | Gmail tools, scopes, email as untrusted input |
| `test_notes.py` | 9 | quick-capture notes |
| `test_lecture.py` | 28 | lecture uploads, pipeline, Notion database |
| `test_tutor.py` | 46 | tutor images, vision call, LaTeX read aloud |
| `test_webfeatures.py` | 25 | lecture/tutor routes in the phone app |
| `test_features_deploy.py` | 8 | lecture/tutor auth on local, failover and cloud; history hooks |
| `test_agent_dashboard.py` | 48 | dev team dashboard page, manifest, the one sendToClaude call site, desktop launchers, skill writes (runs, agents and the chat mirror), watchdog prompt |
| `test_attachments.py` | 40 | chat attachments ("+"): sniffing, limits, PDF pages (incremental saves), Claude's 400s, `/api/turn`, history markers, taint, logs, rollback, cloud page files public |
| `tests/js/attachments.test.mjs` | 6 | chat attachments: the tray, chips and shrunk photos (`node --test`) |
| `tests/js/app-send.test.mjs` | 2 | chat attachments: `app.js`'s submit path in node:vm (files never go with yes/no; chips clear only on an ok response) |
| `test_family.py` | 38 | family invites: accounts, one-time links, isolation, sign-out, removal, failed saves, logs |
| `tests/js/family.test.mjs` | 8 | family invites: `#invite=` accept and the owner's menu (`node --test`) |
| `tests/js/agent-dashboard.test.mjs` | 112 | dev team dashboard: the page's own script in node:vm (run and agent states, the window workspace, links, text only, error codes for every query, maximize, filter, the coordinator chat and its markdown, window layout) |

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
  (review cycle 3: the optional CI signature comes from a separate `sign`
  job whose key is only in the `stable-signing` Environment, master-only
  with Kyle as reviewer; the recommended setup is Kyle signing
  `stable-<sha>` tags himself; only SSH signatures are accepted, OpenPGP
  and X.509 objects are refused before verifying). Tests sign with a real
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

## Dev team dashboard
A read-only claude.ai page for checking on `/build-feature` runs at a
glance, apart from the chat list (LLM Council verdict, 2026-10-19). It
is not part of the assistant and loads nothing from it.
- `tools/agent-dashboard/index.html`: the page behind
  https://claude.ai/artifact/CpPTyJkkHZ7CK8jsPYZq4G (the master publishes
  it). One subscription to the `runs` collection (newest 100). A summary
  line ("2 need you · 1 running · 1 silent"), then one card per run with a
  state pill and a coloured edge: needs you (PR ready or blocked), silent
  (a live phase with no update for longer than its limit + 15 min, an
  unreadable time, or marked dead by the watchdog), running, done (hidden
  after 14 days; PR ready, blocked and dead rows with no change for 7
  days count as done), and "needs you (approval)" when the watchdog saw
  the session on an approval prompt after the master's last write. It
  re-checks "silent" every minute from the cached rows
  and never writes. Rows are untrusted: plain text only, links only to
  this repo's PRs and claude.ai sessions. Error codes are handled per the
  db contract (retry after 5–30 s, back off when busy, stop on revoked).
  Light and dark, phone-first, no external assets.
- `tools/agent-dashboard/README.md`: row schema, phases, time limits and
  thresholds, the `set` then `update` with `if_version` write pattern, and
  the rules (only the master writes; no log text; rows are data).
- `tools/agent-dashboard/WATCHDOG.md`: an hourly trigger in a session with
  the repo attached (a session without it stalled on the ArtifactData
  permission prompt) that checks each live run's session and marks dead
  ones. An idle session counts as dead only past the
  page's threshold for the row's phase (failed or archived at once), so a
  master waiting on a long build round isn't marked dead. It records the
  session's status bucket in `watch` whenever it changes. It pushes at
  most once per session per check (piece rows share the master's
  session), naming every affected piece, on a death or a new approval
  prompt.
- `.claude/skills/build-feature/SKILL.md`: the master writes the row at
  start, research, each build and judge round, PR ready (before the ✅
  push), blocked (before the ⛔ push) and done (when the CI watch reports
  the PR merged or closed, since Kyle merges in the GitHub UI). A run split
  into pieces has one row per piece (`run_id-<piece-slug>`, own
  `if_version` chain and limits), set with every start field (piece name,
  piece branch, the shared session_url, its own times, round 0). It now
  has time limits (started 10 min, researching 30, building 75 and
  judging 30 per round, 5 h a run) and stops with a blocker when one runs out.
- Checked in headless Chromium at iPhone size, light and dark, with a fake
  db: sorting, counts, silent and dead rows, a row with markup in its
  title (shown as text), bad link values (no link), rows with missing or
  wrong-typed fields ("?"), the unavailable and revoked error paths, and
  no db at all. No console errors.
- Tests: `test_agent_dashboard.py` (32: page contract, no markup sinks
  (incl. `createContextualFragment`, `DOMParser`, `srcdoc`) or writes, no
  external loads, both dark selectors, every schema phase, exactly the 4
  README threshold rows, the two link prefixes, every phase write in the
  skill, done on merge/close, one row per piece with every start field,
  no dangling step references (§2 sends an approved piece to §3), the
  watchdog prompt and its per-phase idle thresholds, bucket tracking and
  one push per session, and a runner for the JS tests that asks for the
  TAP reporter, accepts the spec reporter's `ℹ pass`, and drops reporter
  flags from NODE_OPTIONS so Node 23+ and a spec override still pass).
  `tests/js/agent-dashboard.test.mjs` (25) runs the page's own `<script>`
  in node:vm with a fake document, fake `window.claude.use("db")` and
  fake timers: silent at each phase limit ±1 min, missing/unreadable/future
  times, dead and unknown phases, the 7-day parked rule and 14-day hide,
  approval prompts, `safeLink` (wrong prefix, prefix mid-string, bad tail,
  `javascript:`), a markup title rendered as text, odd row bodies, the
  summary line, and each db error code (stop with no timer, doubling
  backoff for `resource_exhausted`, one 5–30 s retry otherwise, no loop
  when subscribing throws). Each of the round-1 judge's five page
  mutations (staleness flipped, link tail dropped, `startsWith` to
  `includes`, raw row bodies, `createContextualFragment`) turns it red.

### Session grid, chat panel and desktop window (2026-10-19, replaced)
*Replaced the same day by the coordinator grid below: the Grid and the
chat panel needed a "Claude Code Remote" connector that doesn't exist on
Kyle's account, so they never worked. The desktop launchers stay.*

Kyle wanted every Claude Code session on one screen, a way to talk to a
session from there, and the dashboard in its own window on a PC.
- `index.html` has Runs / Grid buttons at the top. Runs is the default
  and behaves as before (its rows also gain a Chat button when they have
  a session link). The choice is kept in localStorage (a blocked storage
  is ignored; a `#grid` or `#runs` link wins).
- **Grid:** one tile per session from the viewer's own Claude Code
  Remote connector (`list_sessions`, `{limit: 50}`), kept live by one
  `watchTool` (30 s refetch, 15 s stale time) that starts only when Grid
  is first shown and stops when switching back, so a Runs-only visit
  never asks for the connector or calls it. Tiles: needs you (blocked,
  needs action or need input), failed, working, idle (review ready or
  unknown) and done, in that order, newest first; archived sessions and
  done or idle ones untouched for 7 days are hidden and counted. As many
  columns as fit at 260 px, one on a phone; the page widens only in Grid.
  "Updated X ago" comes from the cache's `storedAt`. Every connector error
  code has its own branch: reconnect / add / not-allowed copy (tiles
  cleared, watch kept), an approval button that makes one call per click,
  a "stale (code)" note that keeps the last tiles for transient errors
  (no retry loop of our own), the no-claude.ai message for lifecycle
  codes, and nothing at all when another account signs in. The error's
  message text is never shown.
- **Chat panel:** Chat on a tile or a run card opens a right-side drawer
  (a full-screen sheet on a phone) with the session's newest 40 user and
  assistant events (`list_events`, one call plus a 30 s watch while open,
  stopped on close). Only text shows; tool calls and results, thinking,
  synthetic, meta, replayed and subagent events and background task
  notifications (`origin.kind` not `human`) are skipped, and a repeated
  uuid shows once; long messages fold behind Show more. Send makes exactly one `send_message` per click (or
  Ctrl/Cmd+Enter; Enter alone adds a line), up to 4000 characters, with
  the button disabled until it settles (a held or composing
  Ctrl/Cmd+Enter never sends); the draft is kept on any error and an
  ambiguous failure is never resent by the page. Each session's draft
  and last send result are kept in memory (at most 50 sessions, never in
  storage), so closing the panel or opening another session mid-send
  loses neither; the result shows on reopening, and in another session's
  panel as "Message to “title”: …". A success clears only that
  session's draft, and only if it wasn't edited meanwhile.
- The manifest the master publishes lists only `list_sessions`,
  `list_events` and `send_message` on Claude Code Remote (pinned by a
  test, which also checks the page names no other tool and never calls
  `console`).
- `desktop/open-dashboard.cmd` (Windows, CRLF, pinned in
  `.gitattributes`, as are `*.sh` and `*.command` to LF so a Windows
  clone with `core.autocrlf` doesn't break them) and
  `desktop/open-dashboard.command` (macOS; finds the browser by checking
  `/Applications` and `~/Applications`, so no Finder window) open the
  artifact in its own Chrome or Edge `--app=` window with the normal
  profile (so already signed in), else the default browser. The README
  covers a pinned taskbar shortcut and the browsers' "install as app".
- Checked in headless Chromium at 1400×900 (light) and 390×844 (dark)
  with a fake `window.claude`: Runs, a 5-column grid and a 1-column
  phone grid, markup in a title shown as text, the chat drawer and
  full-screen sheet, a send; no console errors. Not checked against the
  real connector (the sandbox has no claude.ai session); see
  MANUAL_TEST.md.
- Tests: `tests/js/agent-dashboard.test.mjs` grew from 25 to 81 (a fake
  `use("mcp")` with real `list_sessions` / `list_events` shapes and
  made-up values): every bucket and status (prefixed, lower-case,
  unknown), needs_action only under `external_metadata`, microsecond
  times, the four payload wrappers, sorting and hiding, markup as text,
  bad ids getting no link, each error code (retract or keep, unsubscribe
  or not, no timers), the approval button's one call, Runs by default with
  no mcp lookup, view memory and a throwing localStorage, event parsing,
  one send per click, draft kept on errors, Enter vs Ctrl+Enter, and
  closing the chat unsubscribing, and a pending send that still reports
  after the panel is closed and reopened; in round 2, drafts and send
  results kept per session across Close, Escape and switching sessions
  (both outcomes), an edited draft kept on success, a send settling after
  another account signed in, the 50-session bound, task notifications and
  replays skipped, uuid dedupe, and a held Ctrl+Enter. All 56 new tests
  fail on the old page; mutations (no unsubscribe on Runs, chat watch
  kept on close, no in-flight guard, lifecycle codes keeping the watch,
  subagent or tool-result text shown, approval double-click; in round 2
  each of: drafts cleared on reopen, outcome not kept or not named,
  no uuid dedupe, no origin or replay skip, no repeat guard, an edited
  draft cleared, the other-session check dropped, no gone check on a
  late success, no bound) each turn it red.
  `test_agent_dashboard.py` grew from 32 to 42: the manifest block, the
  three tool names, no console logging, the grid layout rules, and the
  launchers (URL, `--app=`, no `--user-data-dir`, CRLF, quoted paths,
  default-browser fallback, the silent Mac app check, `*.sh` and
  `*.command` pinned to LF, `bash -n`, shellcheck when installed).

### Coordinator grid (2026-10-19)
Replaced the connector Grid and the Chat panel with a Grid fed only by
the dashboard's own db, which the master writes. Runs is unchanged and
still the default; the desktop launchers stay.
- **Manifest:** `{"db":{}, "user":{}, "comments":{"composer_only":true}}`.
  All connector and chat code is gone (no `mcp`, no session list, no
  messages, no send).
- **Coordinator panels:** one per master session, from `runs` rows
  grouped by `session_url` (a plain row and its piece rows share one; a
  row without a session is its own). Every panel with a needs you,
  running or silent row shows, newest first, else the newest one as
  "Last run". A panel shows the feature, a status badge, the run-id
  line, each row's phase, round and last update, the elapsed time, the
  newest 5 `status_lines` (a new optional `runs` field, merged across
  the run's plain and piece rows by time, 100 characters each), PR links,
  **Message coordinator** and **Open session**.
- **Agent tiles:** a new `agents` collection (newest 200, subscribed
  only while Grid is shown) with one row per researcher, builder or
  judge, written only by the master: role, piece, round, a state badge,
  start time, duration and an outcome of at most 80 characters. Running
  tiles turn silent past their role's phase threshold (researcher 45,
  builder 90, judge 45 min), queued or running tiles under a finished
  run show "stopped (run <phase>)", tiles older than 7 days are hidden,
  and live agents whose run has no row at all are counted as hidden
  (round 2).
- **One live-query helper** now serves `runs` and `agents` with the
  same error table (stop, doubling back-off, one 5–30 s retry); Runs'
  behaviour and copy are unchanged.
- **Message coordinator** opens claude.ai's own comment composer on a
  Range over the panel's run-id line (the whole panel if a range can't
  be made). One click is one `openComposer`, never at load, on a timer
  or as a retry. `{opened:false}` asks for a click on the page;
  `rate_limited` says to slow down; `unavailable` or a lifecycle code
  hides every Message button for the visit. Kyle presses Send to Claude
  in the composer; the master watches with ArtifactComments
  (pre-approved, like ArtifactData) and answers in the thread.
- **Tile layout (Morphy-like):** drag a tile by its header handle to
  reorder it within its panel's grid, or Enter plus the arrow keys;
  resize it from its corner (or Shift+arrows) to 1–3 columns and 1–2
  rows (panel 2×2, agents 1×1 by default). Each tile has a coloured
  badge dot (running pulses, except under reduced motion). Order and
  sizes are kept per viewer in localStorage
  (`agentDashboard.layout.v1`, at most 200 entries, pruned to tiles
  that still exist, written only when a move or resize finishes, every
  access in try/catch), with a Reset layout button. A phone, a coarse
  pointer or a screen 600 px wide or less gets one column and no
  handles.
- `.claude/skills/build-feature/SKILL.md`: the master writes an agents
  row before and after every researcher, builder and judge (`set` with
  `state: "running"`, then `update` with `if_version`, `state`,
  `finished_at` and `outcome`), preferably in one `batch` with the runs
  phase write; appends one `status_lines` item per phase write; watches
  the artifact's comments (§0 step 5); and treats a comment sent to
  Claude as Kyle's message about the run, within scope, never copying
  it into a row. The watchdog prompt is unchanged and never touches
  `agents`.
- Checked in headless Chromium (Playwright, from a fake `window.claude`
  with made-up rows): 1400×900 light and 390×844 dark, a coordinator
  panel with five agent tiles; a real mouse drag, a keyboard move
  (focus and the picked-up state survive the re-render), a corner
  resize to 3×2, the layout restored after a reload, the Range's quoted
  text being the run-id line, one column with no handles on the phone,
  and no console errors. That check led to one fix: the title now comes
  before the badge in a tile header, which wraps, so a long badge no
  longer hides the title. Not checked against the real claude.ai shell
  (the sandbox has no session); see MANUAL_TEST.md.
- Tests: `tests/js/agent-dashboard.test.mjs` went from 81 to 63: the
  25 Runs tests are unchanged, the 56 grid and chat tests are gone, and
  38 are new. The fake db now has one query per collection, and there
  is a fake `use("comments")` plus a Range fake with throwing variants.
  The new tests cover Runs as the default (only `db` and the runs
  query, no Chat button); the agents query and comments lookup starting
  on Grid, pausing on Runs and resuming once; the panel fields,
  merging and clamping; grouping, "Last run" and the hidden count;
  `classifyAgent` at each limit, stopped, duration, clamps, unknown
  values and retiring; text-only tiles and every badge class; Message
  coordinator (one call, the Range or element target, opened:false,
  rate_limited, the four codes that hide it, a null namespace); the
  agents error table; and drag, resize clamping, keyboard move and
  resize, corrupt, oversized or out-of-range saved layouts, throwing
  storage, narrow mode and Reset.
  All 38 fail on the old page. With the strict harness, the old page
  throws at load because of its removed ids, so every test fails. With
  a temporary lenient-ids harness, the 25 Runs tests pass and all 38
  new ones fail. Targeted mutations of the new page each turn at least
  one test red: no pause on Runs, a 4-column clamp, no "off" guard, no
  retire, 6 status lines, only `unavailable` hiding the button, no
  order-value check, no 200 bound on read, duration from now, and no
  Range.
  `test_agent_dashboard.py` went from 42 to 44: the manifest, the agents
  query string with `.onSnapshot(` still once, no connector code,
  `openComposer` once and only from the click listener, no console or
  timer-driven composer, the only two localStorage keys, the new layout
  ids and CSS, and the skill and README agents schema. Its 5 page tests
  fail on the old page; the skill and README tests failed before the
  doc changes.
- **Round 2 (judge findings):**
  1. A drag or resize no longer gets stuck when a snapshot arrives
     mid-gesture. Previously the re-render re-parented the tiles, which
     dropped the pointer capture, so the drop never arrived and every
     later drag was refused until a reload. Now, while a gesture is on,
     the page listens for its pointermove, pointerup and pointercancel
     on the window (removed when it ends) and doesn't move tiles; the
     order catches up on release, and a gesture whose tile disappears
     is cancelled.
  2. A master session that ran `/build-feature` twice now shows only
     the newest live run family (the plain row plus its pieces): its
     title, elapsed time and status lines. Earlier finished runs are
     left out. The panel used to show "New" with a 27 h elapsed time
     and yesterday's lines.
  3. "N agents hidden" now counts only live agents whose run has no row
     at all. Before, 40 agents of a month-old run showed as permanent
     noise.
  4. New assertions kill the mutations that survived round 1:
     inverted urgency, elapsed time taken from the latest start, no
     200-tile cap on write, no in-flight composer guard, no layout
     version check, the far-future start, and the Escape drop. Further
     assertions cover a paused listener's late snapshot and safe panel
     ids.

  Seven new JS tests (63 → 70) plus extra assertions. The harness now
  has a fake window that hears pointer events, as a captured pointer's
  events bubble there. On the round-1 page, 9 tests fail: the 6 new
  tests for findings 1–3, plus 3 existing drag/resize tests that now
  dispatch pointer events on the window. The judge's mutation runner,
  plus 16 mutations aimed at these fixes, leaves only two survivors.
  Clearing the retry timer at subscribe is equivalent in practice, and
  dragging in narrow mode is still blocked by the second check in
  `dragEnd`. The judge's three browser probes (drag, same session,
  hidden count) pass in Chromium.

### Workspace grid and coordinator chat (2026-10-19)
Kyle wanted the Grid to be a workspace of its own and to work with the
coordinator from it instead of the session chat. Runs is unchanged and
still the default.
- **Part A, the workspace:** `#coords` is now a flat, Morphy-style window
  workspace that fills the viewport (the page itself never scrolls). It
  auto-tiles to ceil(√n) columns, at most 6, none narrower than 260 px,
  and re-tiles on resize. Each window has a light title bar (badge,
  kicker, title, round, a run-colour square, Open session, PR links,
  maximize) over a dark monospace body. The coordinator is 2×2 only with
  3+ columns, and spans are clamped to the columns for display. Maximize
  works with Esc or a Restore button, and focus returns. There is an
  Active only / All recent filter (`agentDashboard.filter`), 8 run hues,
  and the layout is stored as `{v:2}` (a v1 layout keeps its sizes).
  Drag, resize and keyboard moves are the existing code, with the boards
  removed. On the phone, the header and toolbar collapse into one slim
  row; a maximized window hides that row too and shows Restore;
  safe-area padding.
- **Part B, the chat:** the manifest is now
  `{"db":{}, "user":{}, "comments":{}}`. Each coordinator window has a
  box that sends with `comments.sendToClaude`: the first send anchors on
  the window title, later ones go to the same `threadId`. The text gets a
  `[run <id> #<nonce>]` prefix, control characters are removed and it is
  cut to 4000 UTF-8 bytes. One send at a time, and only from Kyle's
  click or Enter. The box shows only when `canSendToClaude` answers
  `available`. Each error code has its own copy, and nothing is retried.
  The transcript is the master's mirror in Kyle's private
  `data/users/<id>/chat-<sessionTail>/msgs` (newest 200, read through
  `liveQuery`; at most 8 subscriptions, none on Runs). Coordinator
  messages render through a hand-written markdown scanner (elements and
  text only, http(s) links only, about 2000 elements per message).
  Kyle's own message goes pending, then sent, and is deduped by the
  nonce. The open question is highlighted, the view sticks to the bottom
  or shows a New messages pill. The skill now mirrors every message it
  gives Kyle, asks as kind `question`, and prunes conversations older
  than 30 days.
- **Shared fix:** `liveQuery` now sets its retry timer before reporting
  and ignores a `start()` made from inside its own subscribe, so a
  `conn` callback that re-renders can't subscribe twice. A regression
  test covers it and fails without the fix.
- **Kyle's mid-run priority change** (work lean) asked to defer the
  filter, hues, question highlight, New messages pill, markdown tables
  and the full screenshot matrix. They were already built and tested by
  then, so they stayed in.
- **Tested:** `tests/js/agent-dashboard.test.mjs` went from 70 to 98. The
  harness gained document listeners, scroll metrics, a fake `user` and a
  full fake `comments`. Against the old page, 54 of the 98 fail (every
  new-behaviour test) and the 44 unchanged Runs and agent tests pass.
  `test_agent_dashboard.py` went from 44 to 48 (one sendToClaude call
  site, three localStorage keys, the CSS pins, Runs markup unchanged,
  the chat schema, the skill mirror rules). I also checked the page in
  headless Chromium with a fake `window.claude` at 1400×900 and 390×844,
  in light and dark, with no console errors. That check led to fixes for
  markdown list bullets, table font size, the coordinator body fitting a
  2×2 window, and the phone layout (a flex column). Not checked in the
  real claude.ai shell; see MANUAL_TEST.md.

- **Follow-up: working indicator.** The master writes
  `status: {state, text, at}` on the chat index document
  (`data/users/me/chat-<sessionTail>`). The page subscribes to that
  document through `liveQuery` (`docsOf` now takes a document snapshot;
  same cap of 8, paused on Runs). It shows "Coordinator · working" with
  three pulsing dots, the step text (at most 80 characters, plain text)
  and the elapsed time while the status is fresh (under 15 min). After a
  send it shows "Sent · waiting for the coordinator" until a coordinator
  message or a newer status arrives, or 10 min pass. Bubbles show the
  date when a message is not from today. The skill sets the status at
  each step and takes every `at` from `date -u`.
- **Follow-up: delivery stages.** Under Kyle's latest message a line
  shows Sending… (animated), Delivered (sendToClaude resolved), Seen by
  coordinator with the time (`status.seen_ref` = its nonce, or its
  mirrored copy), then "Coordinator · working: <step>" (animated, with
  the elapsed time). The reply clears it. Delivered with no pickup for 2
  minutes turns amber with Open session; a failed send shows "Not sent:
  <reason>" in red. This replaced the separate "waiting" row. The skill
  writes `seen_ref` as its first action on a chat message and checks the
  comment threads for missed messages at the end of every turn. Tests:
  98 → 106 JS; all 8 new ones fail on the previous page.
- **Round 2.**
  - Judge findings:
    1. "working" now needs Seen, which is latched from `seen_ref` or
       Kyle's mirrored copy.
    2. `docsOf` accepts `exists()` as a method.
    3. The line clears on a coordinator message that is new since the
       send and comes after Seen or the copy, so Kyle's clock no longer
       matters.

    Each has a test that fails without its fix.
  - Kyle's restyle: the chat now looks like the Claude Code app (its own
    light and dark palette, the system UI font at 15 px, full-width
    coordinator text, grey bubbles for Kyle, app-style markdown with code
    panels and language labels, a ✻ working line, a rounded composer
    with an arrow Send button), and there are new `kind: "activity"`
    rows that collapse into "N steps".
  - The working timer shows minutes ("3m"), refreshed on the existing
    one-minute interval.
  - JS tests: 106 → 111.

### Dashboard chat presence and queued state (2026-10-20)
- A presence pill above the box is always shown, from the index
  document's `status`:
  - In task with the step and minutes (green, pulsing, ≤30 min);
  - In task with no update (amber, 30–120 min);
  - Idle with last active (`idle`, or working older than 120 min);
  - Status unknown (no status or no user id).
- The working row no longer repeats the step.
- A message that is Delivered but not Seen while the coordinator is in
  a task shows "Queued · coordinator is mid-task…". The 2-minute amber
  line now shows only when the coordinator isn't in a task.
- The skill sets the status working at the start of every turn, idle at
  the end, and re-arms a vanished comment watch. TEAM_MEMORY records
  that watches can disappear.
- Tests: JS 111 → 112 (one new pill test with the 30/120-minute edges,
  plus four tests updated for the moved step text and the queued
  state). All five fail on the previous page. `test_agent_dashboard.py`
  pins the two new SKILL phrases.

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

## Voice style (Kyle's VOICE_STYLE_PROMPT.md)

- `assistant/chat.py` `SYSTEM_PROMPT` now carries the "VOICE STYLE" section
  of Kyle's guide: one or two short sentences, answer first, contractions and
  at most one natural reaction, follow-up questions only when useful, no
  unspeakable formatting, lists over three items summarized, natural numbers,
  a quick filler before slow lookups, plain-speech tool results. It is the
  first part of the system prompt; the tool, memory, web and email prompts
  are unchanged and still follow it. Two of the guide's three example
  exchanges are included; the research one is left out, since its
  specifics (J2312, MAST) could leak into real answers.
- Pipeline tips: (1) natural TTS and (2) sentence-by-sentence streaming were
  already done (Kokoro, voice mode). (3) The speech filter
  (`assistant/sentences.py` `_clean`) now also drops URLs, emoji and list
  markers, besides markdown. (4) max_tokens stays 1500: it is a ceiling that
  must leave room for extended thinking and web search, and a 200 cap would
  cut off tool calls; replies stay short through the prompt. (5) VAD and
  barge-in were already done.
- A filler said before a tool call is spoken in streamed modes (phone, voice
  conversation); in the wake-word loop only the final answer is spoken.
- Tests: `tests/test_sentences.py` (URL, emoji, bullet stripping),
  `tests/test_chat.py` prompt check. Full suite: 1431 passed, 45 skipped.

## Frontend refresh (phone app look)

A visual pass over the phone web app; behaviour, the WebSocket/API protocol,
sign-in, confirmations and security logic are unchanged, and every element
id and data attribute the JS and tests use is kept.

- **Palette and type** (`assistant/web/app.css`): warm neutral tokens on
  `:root` (ivory / near-black), one clay accent, separate light and dark sets
  through `prefers-color-scheme`; the system font (`-apple-system`) with a
  clear scale (20 px bold header, 17 px body, 13-15 px secondary).
- **Chat**: Claude's replies are plain text, your messages soft bubbles,
  notes and errors small centred pills; an empty-state greeting
  ("How can I help?"); a pulsing dot while a reply is still empty; a pill
  composer with a round send button; the confirmation card is a warm card
  with a filled Yes and an outlined No.
- **Push-to-talk**: a raised clay mic with a pressed state; while recording
  it turns red with two soft ripples. Compact sizes below 700 px tall
  (iPhone SE).
- **Gear menu**: a floating card with iOS-style switches (CSS on the existing
  checkboxes), grouped sign-out rows, "Sign out everywhere" in red.
- **Features**: segmented Chat / Lecture / Tutor control; lecture reminder
  readable in light mode (it was dark-on-dark), record button with a dot,
  job cards with status pills and a slim progress bar; tutor photo buttons
  with icons, switches for the two options.
- **History**: blurred sticky bar, filled search field, filter chips,
  conversations in rounded inset groups, an icon on empty results, a
  loading placeholder, bottom action sheet and alert-style rename / delete
  dialogs.
- **Voice mode** (`voice.css`): soft background glow, glossier orb with a
  breathing halo while listening, a state pill with a coloured dot, 56 px
  round controls, and a filled Mute when muted.
- **Accessibility**: every tap target is at least 44 px (gear, tabs, chips,
  links and Add buttons were smaller), visible `:focus-visible` rings
  (two-tone on the mic and send button), safe-area insets on every screen,
  and `prefers-reduced-motion` turns animations off (the recording ripple
  becomes a static ring, the orb an outline).
- **Markup** (`index.html` only): SVG gear, send arrow and mic glyph instead
  of text symbols and the app icon image; the app icon on the sign-in
  screen; `theme-color` matches the new backgrounds. Data-URI icons are
  inside the stylesheets (`img-src 'self' data:`), so the CSP is unchanged
  and there are no inline scripts or styles. No third-party assets.
- `sw.js`: cache `hey-claude-shell-v3`, so phones fetch the new CSS.
- Email and quick capture have no screens of their own in the phone app
  (they are voice tools), so there was nothing to restyle for them.

**Tested:** a mocked server (fake Claude, seeded history, fake lecture
jobs, no keys) driven by Playwright/Chromium at 390x844 and 375x667, light
and dark, 16 screens each (sign-in, sign-in error, empty chat, chat, chat
with a confirmation and recording, gear menu, lecture, tutor, voice
listening and paused, history, no results, action sheet, rename, delete,
transcript). A script also checks every screen for sideways overflow and
tap targets under 44 px; before the refresh it flagged the gear, tabs,
chips, Notion link and Add buttons, after it flags nothing. Before / after
images: `docs/screenshots/before/` and `docs/screenshots/after/`
(`<screen>-<width>x<height>-<light|dark>.png`). Voice-mode and confirmation
screens are staged by setting the same attributes the JS sets (no mic).

## Dev team coordinator link (voice -> /build-feature coordinator)

- `assistant/devteam.py`: Hey Claude talks to the same coordinator session
  the agent dashboard shows. `message_dev_team` sends Kyle's words with the
  Claude Code CLI (`claude -p --cloud <id> --output-format json`, message on
  stdin, so it never appears in a process list); `check_dev_team` reads the
  coordinator's replies from the Notion database "Hey Claude coordinator
  inbox", which the app creates next to Assignments. No Claude API agents:
  the coordinator and its team run on Kyle's plan. Contract for the
  coordinator side: docs/COORDINATOR_LINK.md.
- Off by default (`[devteam] enabled`); needs the CLI signed in on the
  machine that sends (the home PC). No confirmation for Kyle's own requests;
  asks first when outside content is in the conversation.
- Tests: `tests/test_devteam.py` (CLI and Notion mocked): session id parsing,
  stdin, CLI error paths, reply status handling and de-duplication across
  restarts, inbox creation, registration, prompt. Not run against the real
  CLI or Notion from this sandbox.

## PC hub, desktop-only dashboard, hide windows, Get desktop app

- **PC hub** (`assistant/devhub.py`, `assistant/web/devteam.{html,js,css}`):
  a desktop-only page at `http://127.0.0.1:8765/devteam` on the PC's web
  app for chatting with the `/build-feature` coordinator(s). One window
  per `[devteam] hub_sessions` entry (at most 8), auto-tiled, maximize/Esc,
  hide/restore. Sending reuses `CoordinatorLink.send` (`claude -p --cloud`,
  stdin) with `source="hub"` (header `[Hey Claude hub | …]`, `hub-` ids,
  12000 characters); replies, the pickup row and a `presence-<sessionTail>`
  row come from the Notion inbox, polled at most every `poll_seconds`
  (one replies query and one presence query) and only while the page is
  visible. Its own link and state file (`data/devhub.json`) keep voice
  `check_dev_team` and the hub from taking each other's replies. Built only
  with `hub = true` on a loopback bind; every route 404s unless the
  request is from the PC itself (no proxy headers, no trusted proxy); the
  `/api` routes need the normal sign-in. Markdown renderer ported from
  the dashboard, with a parity test.
- `assistant/devteam.py`: `send(source=, session=)`, `replies(max_chars=)`,
  `presence()`/`presences()`, the `idle` status option, `link_from_config`
  (now used by `cli.register_devteam` too), and private atomic state
  writes through `google_auth.write_private`. Voice behaviour unchanged.
- **Agent dashboard:** the phone layout is gone (compact/bare chrome,
  safe-area padding, the narrow media query and every narrow() branch).
  Each window has a – hide button; hidden windows are `h: 1` in the
  existing layout entry (per viewer, capped at 200, pruned), listed in a
  "Hidden (N)" toolbar menu with restore and Show all; a hidden
  coordinator keeps its draft and its chat subscription. "N agents hidden"
  is now "N agents without a run". **Get desktop app** saves the
  `desktop/` launchers (dashboard or PC hub, Windows or Mac) through the
  `downloads` capability as a one-file zip (claude.ai doesn't save `.cmd`
  or `.command`); manifest now `{"db":{}, "user":{}, "comments":{}, "downloads":true}`.
  New `desktop/open-hub.cmd` / `open-hub.command`.
- Tests: `tests/test_devhub.py` (31: auth 401, proxied/non-loopback/
  trusted-proxy 404, cross-origin 403, CSP, stdin and `--cloud`, not sent,
  queued → working → answered/blocked, throttling, hub vs voice replies,
  presence mapping, clear deletes from the file, bounds, caplog, off by
  default and off a non-loopback bind, `serve`), 5 new in
  `tests/test_devteam.py`, `tests/js/devhub.test.mjs` (12, incl. markdown
  parity with the dashboard), 9 new dashboard JS tests (hide/restore/Show
  all/reload/caps/drag/maximize/hidden chat; Get desktop app), launcher
  and manifest pins in `tests/test_agent_dashboard.py`. The new tests fail
  on the previous code (11 pytest, 13 JS). Full pytest: 1513 passed, 45
  skipped, 9 failed and 16 errors (the same eSpeak/PortAudio sandbox
  failures as before; baseline 1474 passed). JS: dashboard 122, devhub 12,
  router 33, voice-core 12. Headless Chromium at 1400×900: hub and
  dashboard render with no console errors once signed in. Not run against
  the real CLI, Notion or claude.ai downloads from this sandbox.
- **Review round 2 (four low findings):** a session dropped from
  `hub_sessions` now leaves `data/devhub.json` at startup, with its
  pending and seen reply ids; `CoordinatorLink` uses an RLock that
  `pending()`, `_save()` and `_inbox()` take, so a hub poll can't lose an
  id a send is adding or dump the dict mid-change; `serve` prints and logs
  "the PC hub is disabled while TRUSTED_PROXY is set" instead of the hub
  link; and the CLI runs through `devteam.run_cli` (Popen, a new process
  group on Windows), which on a timeout runs `taskkill /T /F` on Windows
  (`kill()` elsewhere) so npm `claude.cmd`'s node.exe can't keep the
  window's send lock held. `send()`'s results and the voice tools are
  unchanged. Tests: 3 new in `tests/test_devhub.py` (removed window on
  disk, window freed after a Windows timeout, proxy warning), 3 new in
  `tests/test_devteam.py` (taskkill /T and the process-group flag, kill
  elsewhere, pending/_save wait for the lock); each fails on the previous
  code (the Windows timeout one hits the fake's "blocked forever on
  node.exe's pipes" under the old `subprocess.run`).
- **Grid Coordinator picker:** a native `<select id="grid-coord">` in the
  Grid toolbar lists every coordinator session with a visible row
  (grouped by `session_url`, labelled with the newest run's feature and
  the session's last 6 characters, through `text()`/`textContent`), plus
  All (default). Picking one keeps only that session's coordinator window
  and agents, together with Active only / All recent and hide. Stored per
  viewer under `agentDashboard.coordinator` (a validated session link),
  written only on change; a saved session with no visible row falls back
  to All in memory with a note (live rows only; a cached first snapshot
  keeps the pick). The page still never writes the db.
  Tests: 4 new dashboard JS tests (pick/All, reload and stale fallback,
  filter and hide, hostile feature text), all failing on the previous
  page; the localStorage-keys and markup pins in
  `tests/test_agent_dashboard.py` cover the new key. JS: dashboard 126
  (was 122), devhub 12; `tests/test_agent_dashboard.py` +
  `tests/test_docs.py`: 73 passed, 1 skipped (same as before).

## Chinese MVP for a second user (`config.mom.toml`)

- **A separate instance**: `python -m assistant --mode web --config
  config.mom.toml --env-file .env.mom`. New `--env-file` flag (secrets and
  the generated `WEB_TOKEN` go to that file); `config.mom.toml` turns off
  Notion, memory, notes, lecture, tutor, dev team, failover, updates and the
  PC browser tool, and keeps its own history, sessions, Google files
  (`data/mom/`), port 8766 and `logs/mom.log`. `*.local.toml` and `.env.*`
  are gitignored.
- **Language** (`[assistant] language = "zh"`, new `assistant/i18n.py`):
  a Chinese system prompt in the same voice style (`chat.SYSTEM_PROMPT_ZH`;
  the English tool prompts say "the user" instead of Kyle, plus "always
  reply in Simplified Chinese"); Whisper with `language="zh"` and a Mandarin
  initial prompt (the multilingual `small` model); the assistant's own
  sentences (goodbye, errors, "please say yes or no", decline, no answer,
  Calendar and Gmail draft questions with Chinese dates) in Chinese; a
  whole-answer Chinese yes (`cli.zh_is_yes`, used by `cli.is_yes` and
  `voice_session.is_explicit_yes`; see round 2 below) and 再见/拜拜 to end; sentence splitting on
  。！？； without a following space, 」』） closers, Chinese commas for long
  runs and the first clause.
- **Phone page**: the server sends `<html lang="zh-CN">`; new
  `web/i18n.js` translates the fixed English strings (text, placeholder,
  aria-label, title, `confirm()`) at load and as app.js / voice.js change
  them (MutationObserver), exact matches only. History screens stay
  English. `sw.js` cache `hey-claude-shell-v5`, `/i18n.js` in the shell.
- **Google from the phone** (`[webapp] google_connect`, `public_origin`,
  new `assistant/google_web.py`): a menu button runs the OAuth web flow
  (PKCE, offline access) in the phone's browser; the callback is
  authenticated by a single-use, in-memory state (max 4, 10 min) because
  the session cookie is SameSite=Strict; the token is written with
  `write_private` and Calendar + Gmail (drafts only) are added to the
  running app in one dict update. With `google_connect` on, Google tools
  stay off until that token exists (no browser sign-in on the PC).
  `cli.register_google` now holds the Calendar + Gmail registration.
- Tests: new `tests/test_google_web.py` (7: 401 without a session, 403
  cross-origin, consent URL with PKCE and the redirect URI, unknown state /
  `error=` / replay give 400 and no file, good callback saves the token and
  adds the tools to an existing session, bounded and expiring states, INFO
  logs without code/state/token), `tests/test_i18n.py` (Chinese yes/no and
  exit words, sentences, Calendar/Gmail questions, a Chinese voice
  confirmation end to end, the no-answer status), and additions to
  test_config_cli (config.mom.toml with Kyle's keys set: no Notion, no
  memory, no Calendar before sign-in; `--env-file`; language reaches the web
  app), test_sentences, test_stt, test_chat, test_webapp, test_docs and
  `tests/js/i18n.test.mjs` (7, node:vm). Each regression test was checked
  to fail with its source change reverted. The page and the button were
  also checked once in headless Chromium against a local server (Chinese
  labels under the CSP, the button opening Google's consent URL with a
  code challenge); not on a real phone, and no real Google sign-in.
- Counts: pytest 1498 -> 1573 passed, 46 skipped (unchanged; the same
  model-download and live-service tests). JS: i18n 7 new (dashboard 111,
  router 33 unchanged).
- Known gaps: the push-to-talk page's spoken confirmation suffix in phone-
  voice mode stays English ("Please say yes or no", app.js); conversation
  titles and summaries may come out in English (the title prompt is English); Chinese
  speech recognition and the huayan voice are untested on real audio here
  (huggingface.co is blocked in the sandbox).
- Round 2 (review fixes):
  - **Chinese yes is the whole answer.** Round 1 counted any 是/好/对/行
    anywhere, so 还是明天吧, 好像错了, 是谁, 是什么, 好，等一下, 对了还有一个
    and 我是说错的那个 confirmed. Now `cli.zh_is_yes` splits the answer at
    punctuation and spaces, drops leading 嗯/哦/啊/呃 from each part, and
    every remaining part must be one of 是, 是的, 对, 对的, 好, 好的, 好啊,
    好吧, 行, 可以, 确定, 确认, 没问题, 删吧, 删除吧, 加吧 ("嗯，好的" and
    "好，删吧" are yes; a lone 嗯, 不好, 好不好, 可以吗, 没有, 不要 are no).
    Both `cli.is_yes` (buttons/typed and the web confirm) and
    `voice_session.is_explicit_yes` use it; any CJK answer goes through it
    alone, so English yes words cannot rescue a Chinese sentence. English
    is unchanged.
  - **A separate instance refuses the owner's inherited variables.** With
    `--env-file` other than `.env`, or `[webapp] google_connect` on,
    `cli.main` snapshots the environment before `load_dotenv` and refuses
    to start (exit 1, variable names only) if any of
    `config.ISOLATED_ENV_VARS` (GOOGLE_TOKEN_JSON, GOOGLE_CLIENT_JSON,
    GOOGLE_TOKEN, GOOGLE_CREDENTIALS, HISTORY_PATH, WEB_TOKEN, NOTION_TOKEN,
    NOTION_ASSIGNMENTS_DB, PINECONE_API_KEY, PINECONE_INDEX) was already set
    with a value the env file doesn't give it. README says to start her
    instance from a clean shell. (The sandbox itself has PINECONE_API_KEY
    set, which is how an existing `--env-file .env.mom` test found the
    check: that test now clears the caller's variables.)
  - Tests: test_i18n +33 (the judge's eight sentences and nine more are no
    in both checks; every allowlisted answer, with punctuation and leading
    fillers, is yes in both), test_config_cli +12 (each of the ten variables
    refuses `--env-file .env.mom` and the message names it without the
    value; google_connect with the default `.env` refuses an inherited
    GOOGLE_TOKEN_JSON; values from `.env.mom` itself, and Kyle's own
    default start, still run). With the source changes reverted, 12 of the
    new i18n cases fail (the judge's eight sentences, "yes 好像错了", and
    删吧/删除吧/加吧, which were never yes) and all 11 refusal tests fail;
    the rest (already-no answers such as 可以吗, and the start-normally
    test) are guards that pass on both.
  - Counts: pytest 1573 -> 1618 passed, 46 skipped (unchanged); round 3 (a question like 确定？ is never a yes) 1622 passed, 46 skipped. JS: i18n 7
    passed.


## Voice fixes from Kyle's testing (2026-10-19)

- **Slow end of turn.** The endpointer only counted clearly silent frames
  toward the pause; frames the VAD was unsure about (road noise, music)
  held the turn open, up to the 30 s cap. They now count at half weight.
  Frames the phone couldn't run the model on were dropped and didn't count
  either; they are now judged by loudness instead (`voice.js` onFrame).
- **CarPlay / Bluetooth: voice mode heard nothing, push-to-talk worked.**
  Likely cause: the audio context opened at one sample rate and the car's
  mic ran at another, and Safari hands such a context silence (push-to-talk
  records with MediaRecorder, which isn't affected). The audio session type
  is now set before the context opens, and the context is reopened at the
  mic's rate (kept as is if the browser won't start it). If the mic still
  gives pure digital silence for 5 s, voice mode says so and suggests
  push-to-talk. Not verified in a car from this sandbox: MANUAL_TEST item.
- **Subtitles sometimes empty.** The spoken text of each audio chunk now
  fills the subtitles when no reply text showed it, and reply text replaces
  a tool status line ("list events…") instead of being appended to it.
- **New: mic level meter** under the push-to-talk button while recording
  and under the voice state, turning the accent colour while it hears
  speech. Service worker cache bumped to v4 so phones pick it up.
- Tests: `tests/js/voice-core.test.js` (unsure frames end the turn; audio
  text reaches captions), full suite 1494 passed, 46 skipped.

## Mom's app guides her first steps

- `assistant/web/i18n.js` (only on pages with the "Connect Google" button,
  i.e. a family member's instance): the sign-in text asks for "the sign-in
  code you were sent", and after sign-in a card lists only the steps still
  to do: add to the home screen (hidden once opened from it) and connect
  Google, with what to tap on Google's "unverified app" screen (hidden once
  connected). "Got it" hides it for the visit. In Chinese on her page.
- Tests: `tests/js/i18n.test.mjs` +3 (steps shown, only what's left, never
  on Kyle's page or before sign-in). Phone cache v6.

## Family invites, piece A: accounts and isolation (2026-10-20)

On the cloud server the owner taps ⚙ -> **Invite someone** (name,
language, time zone) and gets a one-time link
`https://<site>/#invite=hcinv.<token>`; opening it signs that phone in as
the new person, with their own history and reply language and nothing of
Kyle's. Piece B (their own Google, per-session speech language,
Render/Docker changes) comes separately.

- `assistant/family.py` (new): `Family` keeps `<dir>/users.json` (written
  with `google_auth.write_private`) with each person's id, name, language,
  time zone, status (invited/active), and only the SHA-256 of their invite
  token. Invite, new link, redeem (under the lock, with the session made
  inside it, so a removal can't race in), remove (sign-out, close the
  database, delete `<dir>/<id>/`, retried on Windows). `context(uid)` builds
  their tools (web search/fetch only; `_register_tools` is piece B's hook
  for their Google tools), their own `HistoryStore` (opened directly, not
  `history.open_store`, and with no sync event log) and a chat factory
  (`ClaudeChat` with their language, name and time zone, no memory). Routes
  `POST /api/invites`, `GET /api/users`, `DELETE /api/users/{id}`,
  `POST /api/users/{id}/invite` (owner only) and the public
  `POST /api/invite/accept`, whose wrong/expired/used invites count toward
  the shared `LoginLimiter`.
- `webapp.py`: `SessionStore` maps sessions to family users (persisted;
  `create(user_id)`, `user()`, `revoke_user()`), now saved with
  `write_private` instead of a fixed `.tmp` name. `WebAssistant` builds a
  family member's session from their context (own registry in
  `SessionTools`); `Session` has `.user`, `.language`, `.history`.
  `require_owner` (404 for family) guards invites, lecture, tutor and
  Google connect. `/api/login` from a browser holding a family cookie now
  makes a fresh owner session. Sign out everywhere is per person (the
  owner's keeps family signed in; the epoch only rotates when nobody else
  is signed in). Reply audio ids are bound to their session. The page's
  `lang` follows the signed-in person. `/api/state` adds `owner`, `family`,
  `language`.
- `history_web.py`: a family session uses its own store and no memory.
  `webfeatures.py`: every route is owner-only.
- `chat.py`: `ClaudeChat(user_name=..., timezone=...)`: an English family
  member's prompts (the voice style included) say their name instead of
  Kyle; the date line is in their time zone.
- `cloud.py`: `INVITE_POST` is public with the login call's slots and
  timeout but no WEB_TOKEN body check; `/family.js` is public.
- Page: `family.js` (new) reads `#invite=`, clears it with
  `history.replaceState`, POSTs it, marks the phone as a family account (no
  failover router) and reloads; the owner's menu has Invite someone, the
  list (status, New link, Remove) and the share sheet with a clipboard
  fallback. A family member's page hides the Lecture/Tutor tabs. Chinese
  strings in `i18n.js`; service worker cache v6.
- Config: `[family]` `enabled` (false; the server profile sets true and
  `dir = "/data/users"`), `dir`, `max_users` 10, `invite_days` 30,
  `zh_stt_model`, `zh_piper_voice` (for piece B). Invite tokens and
  `#invite=` links are masked in logs.
- Tests: `test_family.py` (27): invites need the owner (401 signed out, 404
  for family); `users.json` holds the hash, not the token, mode 600; an
  invite works once (used/expired/wrong -> 401, then 429; WEB_TOKEN still
  works); new link replaces the old; family registry and API calls have
  only web tools, no memory, no Kyle in the prompt; family gets 404 on the
  owner's conversation, lecture, tutor, invites, users, Google; family
  sign-out-everywhere leaves the owner in and vice versa; removal signs out
  and deletes the folder and entries; owner sign-in on a family browser;
  another session's audio id -> 404; page language per person; no names,
  tokens or links at INFO. `test_cloud.py` +2 (accept through CloudGuard;
  wrong invites lock out that address, WEB_TOKEN still works),
  `test_docs.py` +1 (`[family]` mirrored in config.toml),
  `tests/js/family.test.mjs` (6, node:vm). Updated: `test_webapp.py`
  (`/api/state` keys, `/family.js` public, cache v6, the save-failure test
  now breaks `os.replace` since the file goes through `write_private`).
  Each fix was checked by restoring only that source from master `f1c2501`
  (or undoing only that line) and rerunning: the history guard (family saw
  the owner's conversation), `require_owner` in webfeatures (5 routes),
  the login fix, the audio binding, family sign-out-everywhere, the page
  language (2), the redaction patterns, `chat.py` (2) and `cloud.py` (2)
  all fail without their change.
- Counts (Linux sandbox, Python 3.13): pytest 1622 -> 1652 passed, 46
  skipped (unchanged). JS: family 6, i18n 7, backend-router 33,
  voice-core 14, agent-dashboard 111, all passing.
- Round 2 (judge findings):
  - A family member removed between the cookie check and the session lookup
    was briefly the owner (`user()` gave None for an unlisted sid).
    `SessionStore.user()` now gives `GONE` for a sid it doesn't list, and
    `WebAuth.user_of()` counts only the bearer sid, failover-token sids and
    listed owner cookies as the owner; `/`, `/api/login` and
    `/api/logout-all` use it too (a `GONE` sign-out-everywhere is a 401, not
    Kyle's). `WebAssistant.session()` drops a family conversation made while
    its person was being removed. Five older tests called
    `assistant.session("abc")` with an unlisted id; they now use a listed one
    (`owner_sid()` in `test_webapp.py`, likewise in `test_google_web.py` and
    `test_host_update.py`).
  - With failover the owner's page signs in with a bearer token, so the
    invite menu's same-origin `fetch` got 401. `family.js` now takes app.js's
    router-aware `request()` (`HeyClaudeFamily.configure`). The link is built
    from this server's own address: `[webapp] public_origin`, else (with a
    failover role) this backend's URL, the cloud's defaulting to
    `APP_ORIGIN`, else the request's host; never the page's `Origin`.
  - The owner's sign-out-everywhere no longer hangs up family members'
    voice calls, and hangs up each call once.
  - `Family` writes `users.json` from a copy and changes memory only after
    the save (invite, new link, redeem, remove), so a failed save can be
    retried; a folder that can't be deleted stays listed under `deleting`
    and is retried at startup, on the next removal and on `GET /api/users`
    (an error line with the id only).
  - `tzdata` was already in requirements.txt (Windows `zoneinfo`).
  - Tests: `test_family.py` +10 (removal between the two steps for
    `/api/state` and `/api/logout-all`; unlisted sids are nobody's; a session
    made mid-removal is dropped; family voice calls survive the owner's
    sign-out-everywhere; failed saves for remove/invite/new link/redeem; a
    folder that can't be deleted is retried, with a caplog check; the
    link's origin, 3 cases), `family.test.mjs` +2 (owner calls go through a
    real `BackendRouter` with the bearer token and `credentials: 'omit'`;
    app.js configures it first). Each fails with only its source change
    undone (the rmtree retry by making it not catch the error).
  - Counts (Linux sandbox, Python 3.13): pytest 1652 -> 1662 passed,
    46 skipped (unchanged). JS: family 8, i18n 7, backend-router 33.
- Merged master (dev-team PC hub, the first-run guide) into this branch:
  both sides kept. Service worker cache v7 (both sides were at v6) with
  `/i18n.js` and `/family.js` in the shell; `i18n.js` has both the family
  and the guide strings; `[devteam]` hub keys and `[family]` both in
  `DEFAULTS`. The hub's `/api/devteam/*` routes now use `require_owner`
  (a family member signed in on the PC's loopback app gets 404, not Kyle's
  coordinator sessions). Tests: `test_family.py` +1 (fails with
  `require_session` back in `devhub.py`); `test_webapp.py` pins v7 and
  `/family.js` in the shell. Counts (Linux sandbox, Python 3.13): pytest
  1708 passed, 46 skipped. JS: family 8, i18n 10, backend-router 33,
  voice-core 14, devhub 12, agent-dashboard 126 (node's count), all passing.
- Review round 3: **Remove now works cross-origin under failover.** The
  failover preflight (`edge.py` `ALLOWED_METHODS`) allows only GET and
  POST, so the page's `DELETE /api/users/{id}` got a 403 preflight when the
  app talked to the cloud through the router. Added
  `POST /api/users/{id}/remove` (the same owner-only handler; DELETE still
  works same-origin) and `family.js` uses it. README "Invite family" and
  MANUAL_TEST now say invites are managed at the cloud server's own
  address: with failover the active backend is normally the Pi or PC,
  where `[family]` is off, so the menu shows only while the cloud is active
  (documented, no routing change). Tests: `test_family.py` +1 (POST remove:
  owner 200, family session 404, no session 401), `test_failover_edge.py`
  +1 (preflight from the app origin for the method and path read out of
  `family.js`'s Remove handler: 403 with the old DELETE), `family.test.mjs`
  +1 (Remove sends POST `.../remove`, never DELETE); all three failed
  before the fix. Counts (Linux sandbox, Python 3.13): pytest 1710 passed,
  46 skipped. JS: family 9, backend-router 33, all 204 node tests passing (190 .mjs + 14 voice-core.test.js).
## Chinese page: every screen translated

- `assistant/web/i18n.js`: the Chat / Lecture / Tutor tabs, the settings
  menu (backends, captions), the History screen (filters, groups, actions,
  counts) and the lecture and tutor screens now have Chinese strings, plus
  patterns for the dynamic ones ("12 messages", "Actions for …",
  "Transcribing 40%"). Only the "Hey Claude" name stays in English.
- Tests: `tests/js/i18n.test.mjs` +1. Phone cache v7.

## Mom's app tells her what it can do

- The first-run card (`assistant/web/i18n.js`) now also lists example
  questions in Chinese (weather, a recipe, translating, a birthday message;
  plus calendar and email ones once Google is connected), shown without
  her asking until she taps 知道了 once.
- Her assistant answers "你能帮我做什么？" with examples of what this
  instance can do right now (`chat.zh_help_prompt`, from the registered
  tools), and tells her how to connect Google if it isn't yet.
- Tests: `tests/test_chat.py` +1, `tests/js/i18n.test.mjs` updated.
  Phone cache v9.

## Family copies start by themselves

- `scripts/install_family_tasks.ps1`: one Task Scheduler task per family
  instance (`config.<name>.toml` + `.env.<name>`; `config.toml` and
  `*.local.toml` never count), `\HeyClaude\Family-<name>`, run hidden at
  sign-in with a minute's restart after a crash; `-Restart` after an update,
  `-Uninstall`. Stops a hand-started copy first. Kyle's own tasks untouched.
- Tests: `tests/ps/install_family_tasks.tests.ps1` (instance discovery and
  command, run by `test_preflight.py` where PowerShell exists, i.e. the
  Windows CI job) and a text check; the existing parse check covers it too.

## Phone page: menu closes on an outside tap; swipe down hides the keyboard

- New `assistant/web/ui.js` (all pages): the settings menu closes on a tap
  anywhere outside it, or Escape, like other apps; a downward swipe of
  40 px or more while typing blurs the field so iOS closes the keyboard.
  Served at `/ui.js` (public on the cloud, like the other page files),
  cached by the service worker (v10).
- Tests: `tests/js/ui.test.mjs` (4), public-path lists updated.

## Chat attachments: the "+" button (2026-10-20)

- **What:** a **+** left of the phone app's text field opens a sheet (Take
  photo / Photo library / Choose file). Picked files show as chips above
  the field (thumbnail or file name, × to remove; + disabled at 4) and go
  with the next typed message or push-to-talk recording as `attachment`
  parts of `/api/turn`'s form. Photo-only messages work too. Hands-free
  voice mode is unchanged (out of scope).
- **Server:** new `assistant/attachments.py` sniffs the type from the first
  bytes (JPEG, PNG, WebP, GIF, PDF, UTF-8 text without control bytes),
  caps the count (4) and sizes (photos `[tutor] max_image_mb`, PDF/text new
  `[webapp] max_attachment_mb` = 10, both under `max_upload_mb`; text 200k
  characters), and builds Anthropic blocks: photos through the tutor's
  `prepare_image` (now with an `allowed` map so GIF becomes PNG; the
  tutor's own behaviour is unchanged) as base64 `image` blocks, PDFs as
  base64 `document` blocks, text as `document` blocks with a text source
  (so the memory summary's `transcript()` never includes file text).
  Bad files get a friendly 400 before anything reaches Claude.
- **Chat:** `ClaudeChat.ask(..., attachments=[blocks])` sends them before
  the text; saved history and the event log get only a marker
  (`[photo]`, `[file: name.pdf]`), so resume and summaries see plain text.
  The turn is tainted (`untrusted_attachments`, before any tool runs; only
  `reset()` clears it), replies stay out of the DEBUG log while an
  attachment is in history, `_trim_history` now accepts a user turn with
  attachments as the first message (`is_user_turn`, also used by
  `/api/state`'s turn count), and older attachments become a text marker
  once the conversation's base64 passes 20 MB (the API caps requests at
  32 MB). INFO logs: count, kinds and sizes only.
- **Page:** `assistant/web/attachments.js` (public like `family.js`, also
  in CloudGuard's `PUBLIC_GET` so it loads before sign-in) holds the tray
  and the 1600 px JPEG `shrinkImage()`, which the tutor's `features.js`
  now uses instead of its own copy. Thumbnails are `data:` URLs (the CSP
  is unchanged). Chinese strings for every new label and the server's
  messages in `i18n.js`. Phone cache v11 with `/attachments.js` in the
  shell.
- **Cloud:** `/api/turn` was already an upload path capped at
  `max_upload_mb` (4 MB there); the form parser now allows 6 files (the
  recording, 4 attachments, one more to say "at most 4").
- **Tests:** `tests/test_attachments.py` (28): sniffing and names, EXIF
  stripped, block shapes, limits; `/api/turn` with photo + text, PDF + text
  file, photo only, push-to-talk + photo; friendly 400s (5 files, wrong
  magic, oversized PDF and photo, a form with 8 files); history marker and
  no bytes in SQLite; summary has Kyle's words only and is stored
  untrusted; a `confirm_if_untrusted` tool asks after an attachment (and
  not before); caplog: no names or contents at INFO, no reply at DEBUG;
  trimming keeps an attachment turn first; old attachments give way;
  family member's own chat and store; CloudGuard accepts a small photo,
  413s an oversized PDF, serves `/attachments.js` before sign-in; page and
  i18n checks. `tests/js/attachments.test.mjs` (6, node:vm): kinds, the
  4-file limit, chips/×/disabled +, shrunk JPEG sent, a broken photo
  dropped. Checked failing with the source change reverted: taint line
  (4 fail), marker (2), trim (1), private log (1), drop-old (1), control
  byte sniff (2), webapp.py from master (18), the `PUBLIC_GET` entry (1),
  JS limit/disable (2).
- **Counts** (Linux sandbox, Python 3.13): pytest 1710 -> 1738 passed,
  46 skipped. JS: attachments 6, agent-dashboard 126, backend-router 33,
  devhub 12, family 9, i18n 11, voice-core 14, all passing. Real iPhone
  camera/picker behaviour is not tested here (MANUAL_TEST "Attachments").
- **Round 2 (judge's five low findings, all fixed):**
  1. A failed turn no longer strips older attachments:
     `_drop_old_attachments` returns what it replaced and `ask()`'s
     rollback puts the blocks back (`test_a_failed_turn_keeps_the_older_
     attachments`; fails with the chat.py change reverted).
  2. Claude's 400 on an attachment turn (`anthropic.BadRequestError`) now
     says "That file is too long for Claude (PDFs up to 100 pages)."
     (`i18n.py` `file_too_long`, Chinese too) instead of the generic API
     error; the turn is still rolled back and a 400 without files is still
     the generic error. INFO/WARNING logs the count and HTTP status only.
     PDFs with more than 100 `/Type /Page` objects are refused before
     upload to Claude ("A PDF is over 100 pages"; a lower bound, see
     SECURITY.md). Tests: `test_a_file_claude_refuses_gets_its_own_
     sentence` (en, zh; fails with webapp.py or i18n.py reverted),
     `test_pdfs_over_100_pages_are_refused_up_front` (fails with
     attachments.py reverted).
  3. A malformed form says plain "bad request"; "attach at most 4 files"
     only when Starlette's error is "Too many files"
     (`test_only_too_many_files_says_attach_at_most`; fails with that hunk
     reverted).
  4. The Chinese page shows the marker line of a message as `[照片]` /
     `[文件：name]` (i18n.js `translateMarkers`, only when the markers are
     the whole last line); history keeps the English marker. Two new
     i18n tests (both fail with i18n.js reverted).
  5. Rows for the attachment test files above, and
     `tests/js/app-send.test.mjs`, which loads the real `app.js` in
     node:vm: picked files go with a message, chips clear only on an ok
     response (fails if `res.ok &&` is removed), and a typed or tapped
     yes/no goes to `/api/confirm` as JSON without files (fails if
     `!confirming &&` is removed from `pendingFiles`). app.js unchanged.
  - Phone cache stays v11 (this branch's new version, not released yet).
  - Counts (Linux sandbox, Python 3.13): pytest 1740 -> 1745 passed,
    48 skipped. JS: attachments 6, i18n 11 -> 13, ui 4, app-send 2 (new),
    all passing.
- **Round 3 (judge's three low findings plus an older cloud bug, all
  fixed):**
  1. Page count from the page tree root, not by counting `/Type /Page`:
     `attachments.pdf_pages()` takes the direct `/Count` of the last
     `/Type /Pages` object without a `/Parent`. An annotated 60-page PDF
     whose incremental save rewrites 45 pages (105 page definitions) is 60
     pages and accepted; an incremental save that trims 150 to 90 pages is
     accepted, one that grows 90 to 120 is refused; a root in a compressed
     object stream or with an indirect count gives None (Claude decides).
     SECURITY.md's "lower bound" sentence is replaced. Tests:
     `test_an_annotated_pdf_is_not_counted_twice` and the reworked
     `test_pdfs_over_100_pages_are_refused_up_front` (two-level tree,
     `/Type/Pages`, indirect count); both fail with the round-2 `findall`
     count.
  2. One `finditer` pass, no list of matches: the root's count is only
     known at the end (the last definition wins), so there is no early
     stop, but memory stays constant. `test_page_counting_streams_its_
     matches`: a 10 MB file of `/Parent ` words peaks under 1 MB in
     tracemalloc (fails if the matches are collected into a list); a
     5000-digit `/Count` is ignored, never parsed. Measured here: 0.5 s
     for that file, 0.7 s for 10 MB of object headers (sandbox, not a Pi).
  3. A 400 on a turn with files says "too long" only when Claude's reason
     mentions pages, "too long" or tokens (`webapp.FILE_TOO_LONG`); any
     other 400 (protected or broken PDF) says "Claude couldn't read that
     file." (`i18n.py` `file_unreadable`, Chinese in i18n.py and i18n.js).
     `test_only_a_length_400_says_too_long` (4 reasons; the two
     unreadable ones fail with that line reverted), plus the i18n.js
     string in the JS test (fails without it).
  4. On the cloud, `/i18n.js` and `/history.js` got a 401 before sign-in
     (the login page stayed English for a Chinese family member until a
     reload); both are page code with no personal data and are now in
     `cloud.PUBLIC_GET`. `/backend-router.js` is only imported by app.js
     with failover on and was already in `FAILOVER_PUBLIC_GET`.
     `test_every_file_the_login_page_loads_is_public_on_the_cloud` reads
     every `src`/`href` in index.html and checks a fresh cloud app never
     401s them (fails with the cloud.py change reverted).
  - Counts (Linux sandbox, Python 3.13): pytest 1745 -> 1752 passed,
    48 skipped. JS: attachments 6, i18n 13, ui 4, app-send 2, all
    passing.

## Memory review: see and delete what it remembers

- `assistant/memory_review.py` (`MemoryReview`): lists the assistant's own
  vectors in its Pinecone namespace by id prefix (`index.list` with
  `voice-` and `note-`, then `index.fetch` in batches of 100, at most
  1000), sorts newest first by `created_at` (both writers already store
  it; vectors without one show as "date unknown", last), groups them by
  week (Monday, in `[assistant] timezone`) with kind (summary / note) and
  the `untrusted` flag. Deletes one (must exist and be ours) or a week
  (exactly the ids the page showed that are still in that week).
- Phone app: `memory.js` + **Memory** in the settings menu (owner with
  memory on; `/api/state` has `memory`), `memory_web.py` routes
  (`GET /api/memory`, `POST /api/memory/{id}/delete`,
  `POST /api/memory/delete-week`), owner-only (family 404). Chinese strings
  in `i18n.js`. `/memory.js` is public on the cloud like the other page
  files and in the service worker shell (cache v12).
- Voice: read-only `review_memory` tool ("what did you remember about me
  this week?"; `week: "last"` too). "Forget that..." reuses `delete_note`,
  which now also takes `voice-` ids (summaries) and asks first; its
  question is in `i18n.py` (Chinese too).
- Weekly nudge: `[memory] weekly_review = false` by default. When on,
  `ClaudeChat` asks `MemoryReview.nudge` at the start of each conversation;
  at most once in 7 days, and only with notes this week, a system prompt
  line asks Claude to open with "I saved N notes this week, want to review
  them?". Last offer time in `[memory] review_state_file` (atomic private
  write; `/data/...` on the cloud). With no notes it looks again after an
  hour at most; a Pinecone error never blocks the reply.
- Tests: `tests/test_memory_review.py` (27: listing, local-week grouping
  and sorting, batching and the cap, delete one, delete week by exact ids,
  the voice tool and its untrusted mark, "forget that" asking through
  `delete_note`, nudge off/on/once a week/restart/no notes/errors, first
  turn only, wiring, caplog for the module and routes, routes 401/403/404,
  family 404 with no Pinecone call, 502 without details),
  `tests/test_notes.py` +2 (summary delete asks first; Chinese question;
  the foreign-id refusal now uses a non-assistant id),
  `tests/js/memory.test.mjs` (8, node:vm: menu visibility, rendering and
  badges, delete and delete-week confirmations and exact POST bodies,
  every string has Chinese). Each failed with its source change reverted
  (owner_of -> session_of, whole-week delete, no chat hook, nudge every
  turn, old note prefix, no untrusted flag, default on, family flag, no
  confirm in the page).
- Review round 2: **listing works with the pinned SDK.** `pinecone>=7`
  (10.0.0 here) yields `ListResponse` pages from `Index.list()` (ids in
  `page.vectors[i].id`), not lists of ids, so the Memory screen,
  `review_memory`, the nudge and delete-week saw nothing, and on master
  `list_recent_notes` was always empty too. New shared
  `memory.list_ids`/`page_ids` read both shapes (and dicts) and are used by
  `memory_review.py` and `QuickNotes._ids`. `FakeIndex.list` now yields
  ListResponse-shaped pages. The 1000 cap now sorts on the save time after
  the prefix (ids without one last), so a quick note newer than some
  summaries is kept. Tests: `test_notes.py` +2 (a real `pinecone` Index
  with a mocked `_http`: recent notes listed; `page_ids` shapes),
  `test_memory_review.py` +1 (the same real Index: 5 listed, this week's
  2) and the cap test now asserts which ids survive. With the old list
  code `test_notes.py` fails 2 and `test_memory_review.py` 15; with the old
  sort the cap test fails. Service worker cache v11.
- Counts (Linux sandbox, Python 3.13): pytest 1743 passed, 48 skipped, plus
  `test_end_to_end.py::test_hey_claude_whats_on_my_calendar_tomorrow`, which
  failed once in the full run (eSpeak-generated audio, shorter than 1 s) and
  passed 4 of 4 reruns; it doesn't touch memory code (master about 1712
  passed, 48 skipped); node 203 .mjs + 14 voice-core, all passing.

## Smart home through Home Assistant (off by default)

- New `assistant/tools/home_assistant.py`: `ha_list` (devices by area or
  domain, with state; the list is cached for `[home_assistant]
  cache_seconds`), `ha_state` (one device; numeric attributes only) and
  `ha_control` (on/off/toggle, brightness, colour temperature, thermostat
  temperature and mode, fan speed, cover open/close/stop/position, media
  play/pause/next/volume, lock/unlock, alarm arm/disarm when allowlisted,
  and `run` for allowlisted scripts/scenes/automations). REST API with a
  long-lived token (`HA_URL`, `HA_TOKEN` in `.env`) via `requests`; areas
  come from one read-only `/api/template` render. Spoken names are matched
  to friendly names, entity ids and areas (exact, then all words, then
  fuzzy; Chinese names too); more than one match asks which one.
- Safety: `[home_assistant] domains` allowlist; a fixed action-to-service
  map (no service parameter, so no `homeassistant.restart`); scripts only
  by entity id in `[home_assistant] scripts`. Locks, alarm panels, garage
  doors/gates (device class or name) and `secure_entities` always ask,
  are excluded from whole-room calls, and the handler re-checks that the
  yes covered this exact call. Unlocking/opening is refused without a
  question while the conversation is tainted; other changes ask then.
  Free-text states (notification sensors) taint the conversation.
- `ToolRegistry.dispatch` (small, additive): a `confirm_prompt` may return
  None (no question for this input), and `UNTRUSTED_CONTEXT` is now set
  while the question is built too. Existing tools are unchanged.
- Registered in `cli.build_tools` (`register_home_assistant`), so the PC,
  Pi and the owner's web/voice sessions get it; family members never do.
  Startup warns about a plain-http `HA_URL` outside the home network.
  `HA_TOKEN` is redacted in logs; `HA_URL`/`HA_TOKEN` are isolated from
  other instances; `config.mom.toml` keeps it off. English and Chinese
  questions and error messages in `i18n.py`.
- Tests: `tests/test_home_assistant.py` (114, all offline with a fake
  `requests.Session` returning real `/api/states` shapes): listing and
  matching, the exact service payload of each action, ambiguity, cache,
  lock/garage/alarm confirmation, refusal while tainted (also end to end
  through `ClaudeChat`), disallowed domains and services, room calls
  skipping secure devices, HA down/401/403/400/502 spoken nicely, Chinese
  strings, caplog (no names or token at INFO), registration off by
  default, family isolation. Plus one dispatch test in `test_tools.py`.
  Checked that the safety tests fail with the gates removed.
- Review round 2 (8 judge findings, each with a test that fails when the
  fix is reverted):
  - While tainted, every change needs a yes checked in the handler too.
    A Home Assistant hiccup while asking still asks, about the words said;
    that yes never covers a lock. The cache expiring mid-question is fine.
  - Friendly and room names are cut to one line of 60 characters, and a
    name that looks like a sentence taints the conversation.
  - A non-admin token works: the admin-only room lookup's 401/403 means
    "no rooms". README recommends a dedicated non-admin user.
  - An exact room match wins ("Bedroom 1" never brings in "Bedroom 2").
  - Locks and garage doors are never reached by spelling similarity.
  - Switches and covers named door, lock, 门 or 门锁 count as secure.
  - New tests: one yes covers one call, and a 3xx from a service call is
    not "done".
  - A room-wide question says "and N more" (Chinese too).
- Review round 3 (4 findings, each with a test that fails when reverted):
  - Run-together names count as secure again ("Garagedoor", "GateOpener",
    "FrontDoor", "doorlock", "Smartlock"); indoor, outdoor, doorbell and
    clock don't.
  - 门 now counts only in 门锁, 大门, 车库门, 闸门 or at the end of a name,
    so 门口灯 and 门铃 aren't secure.
  - Ambiguous answers with a suspicious name are marked untrusted (now
    tested).
  - A yes given while Home Assistant was unreachable never covers a lock;
    the answer now says so and asks Kyle to try again (English and
    Chinese).
  - `tests/test_home_assistant.py` now has 128 tests.
- Full suite: 1845 passed, 48 skipped (baseline 1712 passed, 48 skipped).
- Final review LOWs: 锁, 门禁, deadbolt and 门 ending a word count as
  secure (4 cases, failing on the round-3 pattern); the area is not
  checked, on purpose, and README/SECURITY say to list neutral-named
  relays in `secure_entities`.
  Not tried against a real Home Assistant (none in the sandbox).

## Interrupt by talking (barge-in) and follow-ups, PC and phone (2026-10-20)

- **PC wake-word loop** (`assistant/wakeword.py` `BargeInSpeaker`,
  `EchoGate`): while a reply plays, a helper thread reads the mic. `[wake]
  barge_in = "wake_word"` (default): "Hey Claude" over a reply stops it at
  once, chimes and records the new request. `"voice"`: plain talking does
  too, once there are `barge_in_ms` (400 ms) of speech mostly
  `barge_in_echo_ratio` (2.5x) louder than the reply's own echo; the echo is
  measured over the first 250 ms of each reply's sound and follows a reply
  that gets gradually louder (levels smoothed over ~100 ms so syllable gaps
  don't break "sustained"). `"off"`: as before (mic closed while speaking).
  - Stopping: `audio.play` waits on a stop event instead of `sd.wait()`
    (`audio.stop_playback()` from the mic thread stops the sound and keeps
    the rest of the reply silent); Kokoro stops rendering the remaining
    sentences; pyttsx3 is stopped from its own `started-word` callback (the
    only safe place to call `engine.stop()`), hooked only when barge-in is on.
  - The interrupting request is the next turn of the same conversation
    (`cli.run_wake`), timed as "Turn timings from barge-in". History keeps
    about the part of the reply that was heard (`ClaudeChat.mark_interrupted`,
    estimated at 2.7 words/s of sound). "Hey Claude, stop" / "never mind" /
    "别说了" just stops (Claude isn't asked). Talking over a yes/no question
    is a no and the words aren't used as the answer. A reply that says
    "Claude" itself can't wake the loop with its own voice. The follow-up
    window works as before after the interrupting request's answer.
  - `--audio-file` with barge-in on reads the file in real time
    (`FileSource(pace=1.0)`), so the watcher hears the part of the file that
    goes with each reply, as a mic would.
- **Phone voice mode** (`voice-core.js` `TurnController`, `voice.js`):
  barge-in was already there (it stops playback, tells the server, which
  cancels the Claude stream and the remaining TTS, and history keeps the
  heard part). New: `[voice] barge_in = "off"` for car speakers that defeat
  echo cancellation (the mic is ignored while a reply plays; listening
  resumes right after), and `[voice] follow_up_seconds` (5): after a reply
  has played, a soft low note (the existing "thinking" earcon, the same note
  as the PC's follow-up blip) and **Still listening…** (还在听…) with the
  mic meter lit; silence quietly puts back **Listening**. No cue after a
  barge-in, a yes/no question or a turn without audio. Phone cache v17
  (`i18n.js` changed; master was already at v10).
- Tests: new `tests/test_barge_in.py` (23): eSpeak-rendered speech through
  the real FileSource (paced 8x), WakeListener, VAD and BargeInSpeaker, with
  a fake sounddevice so "playing" takes time and is stopped through
  `audio.play`: talking over a reply stops it (played < 2.4 of 3 s) and is
  sent as the next turn without the wake word; an untouched reply plays in
  full; the reply's own echo (mixed into the "mic" at 0.25x, above the
  VAD's threshold) doesn't stop it while Kyle over the same echo does;
  "wake_word" mode ignores plain talk and stops on the wake word (with the
  chime); a reply saying "Hey Claude" can't wake itself; "stop" just stops;
  a follow-up within the window after a barge-in turn is heard and one
  after it is not; talking over a delete question deletes nothing; "off"
  never opens the mic while speaking; the gate, `audio.play` stopping,
  pyttsx3 and Kokoro stopping, config and wiring, INFO logs without text
  (caplog). `tests/js/voice-core.test.js` +7 (follow-up window open/close
  by silence or speech, not after barge-in/question/no audio, off, pause
  and end, barge-in on/off). `tests/test_voice_ws.py` +1 and the `ready`
  settings check. Existing follow-up tests (`test_follow_up.py`) unchanged
  and passing. Full run (Linux sandbox, Python 3.13): 1736 passed, 48
  skipped (before: 1712 passed, 48 skipped); `voice-core.test.js` 21 (was
  14). `test_barge_in.py` takes ~20 s (the files are paced in real time
  /8); it also passed under full 4-core CPU load. Not measured on real
  hardware: how loud the echo is from Kyle's speakers, so whether "voice"
  works without headphones is MANUAL_TEST step 4b.

### Round 2 (judge findings)

1. A request said over "Bye!" was kept and sent to Claude after the next
   bare "Hey Claude": the goodbye now drops it, and every new wake clears
   any leftover interruption (`cli.run_wake`, `handle_command`).
2. "voice" echo measuring ignored audio latency: the gate now skips the
   streams' output + input latency (`MicSource.latency`,
   `audio.output_latency()`; 0.35 s when unknown) before measuring. The
   pipeline echo test now has the echo arrive ~300 ms after the sound
   starts (calibration margin ~8 frames on the late side, any amount early).
3. pyttsx3 off Windows (eSpeak's blocking `aplay`): with barge-in on, replies
   are rendered to a file and played through `audio.play`, so they stop;
   if rendering fails it logs and plays the usual, unstoppable way. README
   lists which engines can be stopped.
4. Tests now check the stop flag is cleared after a cut reply and the next
   wake chime really reaches the speaker.
5. Chinese replies: the heard part is estimated at ~4 characters/s.
6. The "no request" message says what interrupted (wake word or voice).
7. `[wake] barge_in` is normalised (`false`/"" = "off", `true` =
   "wake_word", any case, "wake-word") and anything else stops startup with
   a clear message (`wakeword.barge_in_mode`).
- CI margins: the pipeline tests now pace at 6x (was 8x) with 6 s replies;
  "cut off" means under 4 s (cuts measured at 1.2-1.7 s), so there is about
  0.4 s of real-time headroom instead of about 0.1 s.
- Tests: `test_barge_in.py` 39 (was 23), each fix's test fails with that
  fix reverted. Full run (Linux sandbox): 1752 passed, 48 skipped (round 1:
  1736; master: 1712); JS files all pass (`voice-core.test.js` 21).
  `test_barge_in.py` passes under full 4-core CPU load; there, eSpeak now
  and then wrote no clip file at all, so the shared `tts_clip` test helper
  also retries on a missing file (it already retried on an empty one).
- Round 3: the echo is measured for 600 ms (was 250) after the reported
  latency, so an echo up to ~0.5 s later than reported (Bluetooth, drivers)
  isn't taken for talking (the judge's Kokoro simulation: all replies played
  through); README says so next to the echo-ratio advice. pyttsx3's
  "Audio saved to ..." is no longer printed on every reply. New tests: an
  echo 250 ms later than reported, the quiet render, and the leftover
  interruption cleared at a new wake (`test_barge_in.py` 42). The pipeline
  echo is now 0.2x (still above the VAD threshold), since a longer
  measurement sees more of the reply's loud parts. Full run: 1755 passed,
  48 skipped; JS files all pass.

