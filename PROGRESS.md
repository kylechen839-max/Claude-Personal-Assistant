# Progress

All seven stages are done. Run the tests with `pytest` from the project root
(venv active). The final hands-on checks are in `MANUAL_TEST.md`.

**Current test count:** 1611 passed, 44 skipped (Linux build sandbox,
Python 3.13, 2026-10-20; that sandbox had no eSpeak or PortAudio, so 25
audio tests failed or errored there, as before this change), plus 67
JavaScript tests (node:test: `voice-core.test.js` 14, run by pytest too,
and `backend-router` 33, `family` 9, `i18n` 11).
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
| `test_stt.py` | 17 | stage 3; beam search, vocabulary prompt, prompt echo, short answers on small.en |
| `test_stt_cloud.py` | 12 | optional OpenAI / Deepgram speech-to-text, local fallback |
| `test_wakeword.py` | 15 | stage 4 |
| `test_follow_up.py` | 14 | follow-up mode |
| `test_barge_in.py` | 43 | barge-in on the PC loop (talk over a reply), with follow-ups |
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
| `test_calendar_features.py` | 39 | calendar colors, labels, repeats, guests, RSVP, free time, other calendars |
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
| `test_drive.py` | 17 | read-only Google Drive tools, the scope, an old token, Drive text as untrusted input |
| `test_notes.py` | 9 | quick-capture notes |
| `test_lecture.py` | 28 | lecture uploads, pipeline, Notion database |
| `test_tutor.py` | 46 | tutor images, vision call, LaTeX read aloud |
| `test_webfeatures.py` | 25 | lecture/tutor routes in the phone app |
| `test_features_deploy.py` | 8 | lecture/tutor auth on local, failover and cloud; history hooks |
| `test_build_feature_skill.py` | 8 | the `/build-feature` skill's dashboard writes (runs, agents, the chat mirror) and the watchdog prompt (`docs/WATCHDOG.md`) |
| `test_attachments.py` | 40 | chat attachments ("+"): sniffing, limits, PDF pages (incremental saves), Claude's 400s, `/api/turn`, history markers, taint, logs, rollback, cloud page files public |
| `tests/js/attachments.test.mjs` | 6 | chat attachments: the tray, chips and shrunk photos (`node --test`) |
| `tests/js/app-send.test.mjs` | 2 | chat attachments: `app.js`'s submit path in node:vm (files never go with yes/no; chips clear only on an ok response) |
| `test_family.py` | 38 | family invites: accounts, one-time links, isolation, sign-out, removal, failed saves, logs |
| `test_account.py` | 37 | a family member's own "Download my data" / "Delete my account": own session only, Google revoke, whole folder, cap, temp files, one at a time, rate limit, logs |
| `test_family_import.py` | 33 | moving a PC family copy's history to the cloud: owner-only import route, idempotent retries, size cap (413), no sync log, logs, delete and removal, the CLI (batches, Bearer, dry run, https only) |
| `test_confirm_ux.py` | 123 | yes/no answers: misheard yeses, one more try then cancel (text, PC voice, phone voice, `/api/confirm`), calendar moves without a question |
| `tests/js/family.test.mjs` | 19 | family invites: `#invite=` accept and the owner's menu (`node --test`) |

## Stage 1 - Core chat ✅
- `kwil/chat.py`: `ClaudeChat` keeps the conversation history, sends a
  short spoken-style system prompt with the current local date/time, and
  calls the Messages API. Model from `CLAUDE_MODEL` / `[assistant] model`,
  default `claude-sonnet-5-5`. A failed request is rolled back so history
  stays valid; long histories are trimmed to start with a plain user turn.
- `python -m kwil --mode text`: typed chat.
- Tests: mocked Anthropic client (history, system prompt, rollback,
  trimming, exit words, the text loop).

## Stage 2 - Text to speech ✅
- `kwil/tts.py`: a `TTS` interface (`speak`, `save_to_file`) with
  `Pyttsx3TTS` (offline OS voice: SAPI5 on Windows, eSpeak on the Pi),
  `PiperTTS` (offline neural voice, `piper-tts` 1.8.0, wheels for Windows
  x64 and Linux x86_64/aarch64) and `PrintTTS`. `make_tts()` picks one from
  `[tts] engine`; if the Piper voice or package is missing it warns and
  falls back to pyttsx3.
- `python -m kwil.tts --download-voice en_US-lessac-medium` downloads a
  voice into `voices/` (gitignored); `--say TEXT` tries it out loud.
- On Windows, files are rendered through SAPI directly (pyttsx3 2.99 writes
  header-only .wav files there) and a fresh pyttsx3 engine is used per reply.
- Tests: real pyttsx3 and Piper speech rendered to .wav (exists, non-empty,
  not silent), engine selection and fallbacks, the download helper (mocked).

## Stage 3 - Speech to text ✅
- `kwil/stt.py`: `Transcriber` lazily loads faster-whisper (`base.en`,
  CPU, int8); `record_push_to_talk()` records between two Enter presses.
- `python -m kwil --mode voice`: push-to-talk with spoken replies.
- Tests: TTS renders "What is on my calendar tomorrow", real Whisper
  transcribes it, and the transcript must roughly match (pyttsx3 and Piper
  speech); mocked-model tests always run.

## Stage 4 - Wake word ✅
- `kwil/wakeword.py`: openWakeWord with the custom model
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
- `kwil/tools/`: a `ToolRegistry` (API schemas, dispatch, errors
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
- `kwil/memory.py`: `PineconeMemory` on the existing `project-context`
  index with Pinecone's hosted `llama-text-embed-v2` embeddings (the model
  the index was built with). Before each reply, up to 4 notes scoring at
  least 0.4 are added to the system prompt; after each conversation Claude
  writes a 1-3 sentence summary into the `voice-assistant` namespace (small
  talk is skipped). Other namespaces are only read. A memory failure is
  logged and the reply goes ahead without it.
- Memory tools (`kwil/tools/memory_tools.py`, all modes): `search_memory`
  lets Claude look notes up when asked ("what do my notes say about...");
  `remember` saves a note to the `voice-assistant` namespace when asked
  ("remember that..."), with a confirmation if web/email content is in the
  conversation. Tests: `test_memory_tools.py` (mocked memory).
- Tests: mocked Pinecone (score filter, namespaces, upsert shape, failure
  tolerance, summary/skip) and one real read-only query when
  `PINECONE_API_KEY` is set.

## Stage 7 - Polish ✅
- `config.toml` over built-in defaults (`kwil/config.py`); secrets in
  `.env`; `CLAUDE_MODEL` and a few others override from the environment.
- Logging to `logs/assistant.log` (rotating, 1 MB x 3) with API keys and
  OAuth tokens masked; the console shows warnings only.
- Errors: network down, timeouts, bad key, rate limits and other API errors
  each give a short spoken sentence; a failing command never stops wake
  mode; mic errors are retried 5 times; startup problems print one line and
  exit 1.
- Single entry point `python -m kwil` (`claude_assistant.py` is a shim).
- `--check` (`kwil/doctor.py`): keys set (never values), Google files,
  wake model, audio devices with indexes, Whisper cache, TTS dependencies.
- `--timings` (`kwil/timing.py`): one latency line per turn (wake ->
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
- `python -m kwil --mode web` (`kwil/webapp.py`, page in
  `kwil/web/`): FastAPI + uvicorn serving one mobile-first PWA page
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
- **`kwil/cloud.py`** (`CloudGuard`, an outer ASGI layer; the web module
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
- Files: `kwil/voice_ws.py` (WebSocket route, limits, static files),
  `kwil/voice_session.py` (turn pipeline), `kwil/sentences.py`,
  `ClaudeChat.abort / mark_interrupted` in `chat.py`;
  `kwil/web/voice.js` (mic, playback, socket, UI),
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

- **Gmail** (`kwil/tools/gmail.py`, `google_auth.py`): `email_summary`
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
  `python -m kwil.tools.google_auth`, also in `--check`. Email results
  count as untrusted content (`Tool.untrusted_output`,
  `chat.has_untrusted_content`), so changes ask first; `Tool.private`
  keeps email text and replies out of the log.
  Tested: mocked Gmail service, prompt-injection emails x 5 actions x 2
  readers, scope detection, cloud token, log check.
- **Quick notes** (`kwil/tools/notes.py`): `remember_note`,
  `recall_notes`, `list_recent_notes`, `delete_note` in Pinecone
  (`voice-assistant`, `kind=note`, `created_at`, tags). When notes are on
  they replace the plain `remember` memory tool; `search_memory` stays.
  Deletes ask and
  only touch `note-` ids. `QuickNotes.on_saved` is the hook for showing
  notes in conversation history once that lands (via the history module's
  public functions, not its store). Tested with an in-memory fake index.
- **Lecture recorder** (`kwil/lecture.py`, `webfeatures.py`, Lecture
  tab): offset-based resumable uploads (gap -> 409 with the server's
  offset, repeats ignored, size caps), a background job (one at a time,
  resumes after restart) that decodes with PyAV in 10-minute windows
  (faster-whisper's own file decoder fails with PyAV 19, and windows keep
  memory flat), transcribes with progress, summarizes with Claude
  structured outputs (a JSON schema; see "Fix: lecture summaries 400 on
  Sonnet 5.5"), finds or creates "Lecture Notes" next to Assignments, and
  files a page with inline/block equations, idempotently (Lecture ID
  property + local page record; a retry appends only missing blocks). Assignment-like action items
  are offered with Add buttons. Tested: resume after a lost chunk,
  concurrent resends, pipeline with mocked Whisper/Claude/Notion, retry
  without re-transcribing, DB find/create/refused paths, real WebM/Opus and
  MP4/AAC decoding, and (CI) real Whisper on a generated WebM lecture.
  The page was also driven in headless Chromium with a fake microphone.
- **Homework tutor** (`kwil/tutor.py`, `mathspeech.py`, Tutor tab):
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
  `python -m kwil --check`, `install_service.sh` (systemctl/loginctl
  stubbed and their calls checked) and `preflight.sh`, which runs the real
  wake word -> VAD -> Whisper -> reply on the committed fixture, both TTS
  engines, and `--mode web` with a text and an audio turn. Stubbed only:
  systemd, the sound card (an ALSA `null` device) and Claude
  (`scripts/ci/fake_claude.py`, no key in CI). Runs on every push to
  master, on pushes that touch the Pi files, and by hand (with a QEMU
  option). First green run: about 1.5 minutes in total (install 42 s,
  pytest 17 s, preflight 8 s) with downloads cached.
- **Preflight** (`kwil/preflight.py`, `scripts/preflight.ps1` for
  Windows PowerShell 5.1+, `scripts/preflight.sh`): git, .env, `--check`,
  pytest offline and live (live skips are a FAIL), the wake-word end to end
  on `tests/fixtures/hey_claude_calendar_tomorrow.wav` (asserts a wake, a
  transcript matching the phrase and a non-error reply), text-mode calendar
  and Notion questions (the log must show both tools called), web mode on
  127.0.0.1 (healthz, auth refusal, login with `WEB_TOKEN`, text and audio
  turn), pyttsx3 and Piper renders, and with `--hardware` the audio device
  list and a 3 s mic level. Table + `logs/preflight-<timestamp>.txt`.
  Every `.env` value is masked in the console and the report.
- **Smoke check** (`kwil/smoke.py`, `scripts/cloud_smoke.ps1`/`.sh`):
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
  The supervisor is `kwil/host.py` (`python -m kwil.host`), also
  used by the Pi's systemd unit.
- **Automatic updates** (`kwil/updater.py`, `kwil/host.py`,
  `kwil/version.py`, `kwil/web/update-watch.js`): opt-in `[update]
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
- **Kokoro-82M** (`kwil/tts_kokoro.py`, `kokoro-onnx`, Apache-2.0):
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
- **Setup-time download**: `python -m kwil --download-voice kokoro`
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
- **ElevenLabs** (`kwil/tts_elevenlabs.py`, optional, paid): only with
  `engine = "elevenlabs"` *and* `ELEVENLABS_API_KEY`; streams raw 24 kHz
  PCM per sentence from `/v1/text-to-speech/{voice}/stream`
  (`eleven_flash_v2_5`), 8 s timeout. On any error that sentence is spoken
  by the local engine (Kokoro -> Piper -> pyttsx3) and the cloud is skipped
  for 60 s. The key is sent only in the `xi-api-key` header; log lines give
  only the error type and message (and the key was already redacted from
  logs).
- **Earcons** (`kwil/earcons.py`): original sounds from sine waves
  (a little octave overtone, 12 ms attack, exponential decay, 20 ms fade to
  zero, so no clicks): *listening* rises a fifth (D5 -> A5, 0.35 s, peak
  0.25), *thinking* is one quiet G4 (0.16 s, peak 0.08), *ended* falls a
  fifth (0.41 s, peak 0.2). The WAVs are committed in
  `kwil/web/earcons/` (8-20 KB) and served by the web app at
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
- `kwil/history.py`: `HistoryStore` on stdlib SQLite (WAL,
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
- Phone app: `kwil/history_web.py` adds `GET /api/conversations`
  (paginated, `q`, source/pinned/archived/date filters, highlight parts),
  `GET /api/conversations/{id}`, `POST .../continue`, `PATCH` (rename, pin,
  archive), `DELETE` (with the Pinecone cascade; also clears the
  conversation from any open session), `GET .../export?format=md|json`, and
  `GET/POST /api/incognito`, all behind the web sign-in. Push-to-talk turns
  are saved as `web`, voice-mode turns (`chat_stream`) as `voice`, and a
  barge-in keeps only the spoken part, flagged interrupted.
  `kwil/web/history.js`: History screen (debounced search, day groups,
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
  https://claude.ai/artifact/BWYCUWxJxBvG6MJvnsaqvn (the master publishes
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
  and a bundled espeak-ng (GPL-3.0); also fine for personal use. The cloud's
  Docker image (with piper-tts) is built on Render from this repo and never
  published; serving replies over a network to beta testers is not
  distribution under GPL-3.0 (it isn't the AGPL), so no source offer is
  owed. Publishing the image or shipping it to someone would be.
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
- `kwil/web/backend-router.js`: the client router as a standalone ES
  module: parallel `/healthz` probes (1.5 s tailnet, 3 s cloud), highest
  priority healthy backend with a 300 ms grace for a slower better one, API
  version check, one retry of idempotent requests on the next backend,
  writes never replayed (`NeedsUserRetry`), 60 s background re-probe with
  fail-back after 2 good probes, no switch mid-turn, Auto / forced override,
  IndexedDB token store.
- `kwil/failover/edge.py` + `tokens.py`: ASGI middleware for every
  backend: `/healthz` (ok, api, role only), CORS for the app origin only (no
  credentials), derived bearer tokens (`POST /api/auth/token`, refresh,
  scopes app/sync, 90-day session limit), WebSocket first-message auth
  (4401 / 4408), per-backend lockout.
- `kwil/failover/sync.py`: history as an append-only, id-addressed
  event log; `materialize()` is order-independent and idempotent (LWW
  renames/pins on (ts, id), permanent tombstones); push/pull endpoints and
  client with cursors and peer-reset detection; `MemoryEventLog` reference
  store (the SQLite store from claude/history implements the same API).
- `kwil/failover/leader.py`: leader check (probe higher-priority peers
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

- `kwil/chat.py` `SYSTEM_PROMPT` now carries the "VOICE STYLE" section
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
  (`kwil/sentences.py` `_clean`) now also drops URLs, emoji and list
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

- **Palette and type** (`kwil/web/app.css`): warm neutral tokens on
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

- `kwil/devteam.py`: Hey Claude talks to the same coordinator session
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

- **PC hub** (`kwil/devhub.py`, `kwil/web/devteam.{html,js,css}`):
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
- `kwil/devteam.py`: `send(source=, session=)`, `replies(max_chars=)`,
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

- **A separate instance**: `python -m kwil --mode web --config
  config.mom.toml --env-file .env.mom`. New `--env-file` flag (secrets and
  the generated `WEB_TOKEN` go to that file); `config.mom.toml` turns off
  Notion, memory, notes, lecture, tutor, dev team, failover, updates and the
  PC browser tool, and keeps its own history, sessions, Google files
  (`data/mom/`), port 8766 and `logs/mom.log`. `*.local.toml` and `.env.*`
  are gitignored.
- **Language** (`[assistant] language = "zh"`, new `kwil/i18n.py`):
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
  new `kwil/google_web.py`): a menu button runs the OAuth web flow
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

## Dashboard: close idle agents, ⤡ restore, chat Reply and attachments (2026-10-20)

- **Close idle agents** (Grid toolbar): one click hides every agent window
  on screen that is done, failed, stopped or silent (never running,
  queued or a coordinator) with the existing `h: 1` layout flag, saved
  once, announced "Closed N idle agent windows"; follows the Coordinator
  picker and Active/All filter; disabled when nothing is idle.
  **Auto-close idle agents** is an opt-in toggle (`agentDashboard.autoClose`,
  "1"/"0"); a window restored by hand keeps `k: <state>` in its layout
  entry and stays shown until its state changes.
- **Maximize restore** is now an icon button (⤡) beside ⤢, same class and
  size, `aria-label="Restore <title>"`, `aria-pressed="true"`.
- **Reply**: selecting text inside one transcript message shows a Reply
  button; the quote chip (200 shown, 1000 sent, × and Esc clear) is sent
  as `> ` lines before Kyle's text and shown as a blockquote in his
  bubble; a failed send restores it with the draft.
- **Attachments**: manifest now
  `{"db":{}, "user":{}, "comments":{}, "downloads":true, "assets":{}}`.
  Paste or drop up to 5 files per message; each is uploaded with
  `assets.upload` at that gesture (type and size checked first, never
  retried), shown as a chip (thumbnail for images from `upload()`'s url
  only), and sent as `[attachment: <name> (<type>, <size>) asset:<id>]`
  lines counted inside the 4000-byte cap. × deletes an unsent asset;
  after a send that may have been posted it only drops the chip.
  Leaving a maximized idle window counts as keeping it shown, so
  auto-close doesn't hide what Kyle was reading. The coordinator opens them with Artifact `read path=<asset id>` as
  untrusted data (SKILL.md Chat mirror).
- Tests: 23 new or updated node:vm tests in
  `tests/js/agent-dashboard.test.mjs` (6 close-idle/auto-close, 9
  attachments, 3 Reply, the maximize/desktop tests for ⤡); 20 fail on the
  previous page. pytest pins: manifest, the localStorage keys (now five),
  one `assets.upload`/`assets.delete` call site each, thumbnails only from
  `upload()`. JS: agent-dashboard 144 passed (was 126).

## Mom's app guides her first steps

- `kwil/web/i18n.js` (only on pages with the "Connect Google" button,
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

- `kwil/family.py` (new): `Family` keeps `<dir>/users.json` (written
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
    router-aware `request()` (`KwilFamily.configure`). The link is built
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

- `kwil/web/i18n.js`: the Chat / Lecture / Tutor tabs, the settings
  menu (backends, captions), the History screen (filters, groups, actions,
  counts) and the lecture and tutor screens now have Chinese strings, plus
  patterns for the dynamic ones ("12 messages", "Actions for …",
  "Transcribing 40%"). Only the "Hey Claude" name stays in English.
- Tests: `tests/js/i18n.test.mjs` +1. Phone cache v7.

## Mom's app tells her what it can do

- The first-run card (`kwil/web/i18n.js`) now also lists example
  questions in Chinese (weather, a recipe, translating, a birthday message;
  plus calendar and email ones once Google is connected), shown without
  her asking until she taps 知道了 once.
- Her assistant answers "你能帮我做什么？" with examples of what this
  instance can do right now (`chat.zh_help_prompt`, from the registered
  tools), and tells her how to connect Google if it isn't yet.
- Tests: `tests/test_chat.py` +1, `tests/js/i18n.test.mjs` updated.
  Phone cache v9.

## Dashboard moved out, PC hub removed (2026-10-20)

- The agent dashboard page (`tools/agent-dashboard/`: the page, its README
  with the row schemas, desktop launchers) and its tests
  (`tests/js/agent-dashboard.test.mjs`, the page checks in
  `tests/test_agent_dashboard.py`) now live in the private
  kylechen839-max/agent-dashboard repo (`dashboard/`). The watchdog prompt
  moved to `docs/WATCHDOG.md`, since the watchdog runs in a session on this
  repo; SKILL.md points at both.
- Kyle scrapped the PC hub: `kwil/devhub.py`, `web/devteam.*`, its
  `create_app(hub=)` hook and startup lines, the `[devteam] hub`,
  `hub_sessions`, `poll_seconds` and `hub_state_file` settings, the hub
  sections of README, MANUAL_TEST, SECURITY, PRIVACY and
  docs/COORDINATOR_LINK.md, and the skill's hub and presence-row steps are
  gone. `kwil/devteam.py` keeps only the voice link
  (`message_dev_team` / `check_dev_team`): the hub's message source, window
  sessions, 12000-character replies and presence reads were dropped; the
  private atomic state file, the state lock and the whole-tree kill on a
  CLI timeout stay (they protect the voice link too). A new Notion inbox
  gets the statuses answered, working and blocked (no `idle`). An old
  `config.local.toml` with the hub keys still loads (they are ignored).
- Tests: the skill and watchdog checks moved to
  `tests/test_build_feature_skill.py` (8; the phase list and the page's
  +15 min thresholds are pinned there, from the agent-dashboard repo's
  README); `tests/test_devteam.py` lost the hub/presence tests and gained
  `test_voice_message_header_prefix_and_cap`,
  `test_replies_are_capped_for_speech`, `test_inbox_statuses`,
  `test_pc_hub_is_gone` and `test_old_hub_settings_still_load` (29 tests).
  `test_inbox_statuses` and `test_pc_hub_is_gone` fail with the old
  `devteam.py` and `devhub.py` restored. Full suite: 9 failed, 1611 passed,
  44 skipped, 16 errors (the 25 are the sandbox's eSpeak/PortAudio/wake
  model tests, as in the baseline); JS 53/53 (`tests/js/*.test.mjs`).
- Merged master (health watchdog, safe updates, Spotify): `devteam.py`
  keeps `SOURCES` for "voice" and the watchdog's "watch" (header
  `[Hey Claude watchdog | …]`, `hc-` ids), `send(text, source="voice")`
  without the hub's `session=`; `create_app` keeps `health=` and
  `serve` the candidate guard on `resume_jobs()`, without `hub=`;
  `watch.candidate_config` no longer sets the removed `[devteam] hub`.
  New `test_watchdog_alerts_have_their_own_header_and_no_hub_source`
  (fails on both sides' old `devteam.py`). Full suite: 9 failed,
  2096 passed, 56 skipped, 26 errors (all 35 the sandbox's
  eSpeak/PortAudio/wake model set); JS 94/94.

## Three flaky CI tests, root-caused (2026-10-20)

- **Midnight in New York** (`test_end_to_end.py::test_definition_of_done_phrase`):
  the test worked out "tomorrow" from the clock, and the assistant read the
  clock again seconds later (after the wake-word scan and Whisper). The
  assistant had the same race inside one turn: the system prompt's date
  (`ClaudeChat.build_system`) and the calendar tool's "tomorrow"
  (`timeutil.resolve_day`) were two readings, so a question at 23:59:59
  could get the day after tomorrow. Now "now" is read once per turn:
  `ClaudeChat.ask` sets `timeutil.TURN_NOW`, and the prompt, `resolve_day`
  and Notion's "today" (`list_assignments`) all use it; outside a turn they
  read the clock as before. The end-to-end test pins the clock to 23:59:59
  New York time. Tests: `test_tools.py` +2 (a turn at 23:59:59 whose clock
  ticks past midnight before the tool runs: "tomorrow" stays Tuesday, not
  Wednesday; Notion's "today" inside a turn); both, and the pinned
  end-to-end test, fail on the old code.
- **Voice latency on Windows** (`test_first_audio_latency_with_real_whisper`):
  the 2 s budget is for our pipeline (Whisper, streaming, sentence split,
  framing); Windows SAPI, a test-only voice, took up to 3.3 s per sentence
  on the runner. c5c428a subtracted the first sentence's SAPI time; now the
  OS voice is timed where the product calls it (`webapp.render_wav` on the
  web app's TTS thread, with its COM set-up), and every render before the
  first audio is subtracted, including a first sentence whose render
  failed and was skipped. Kokoro's ~0.4 s first audio isn't what this test
  measures. Test: `test_voice_ws.py` +3 (fake slow voice and slow Whisper:
  OS-voice time is excluded, a slow Whisper still counts; the failed-first-
  render case fails with c5c428a's formula).
- **Whisper "!!!!" on Windows** (`test_lecture.py::test_pipeline_transcribes_a_real_webm`):
  "!" is token 0, so "!!!!" is a degenerate decode: NaN logits (int8
  kernels misbehave on some CPUs) or a bad sampled temperature fallback.
  Every Whisper path uses faster-whisper's default fallback, voice
  `Transcriber.transcribe` included; what sets this test apart is
  `vad_filter=True` and the WebM/Opus re-encode, which weakens the
  sampling theory. The cause wasn't reproduced (no Whisper model in the
  sandbox, CI logs can't be fetched), so the fix is a product guard that
  holds whatever the cause: `stt.is_degenerate` (3+ characters, all "!";
  "..." and "♪♪♪" are normal non-speech output and aren't retried) and `stt.redecode`, which decodes again greedily (temperature
  0, no conditioning on the bad text) on a float32 copy of our own model,
  loaded only then, and drops segments still degenerate. The lecture job
  applies it per window, voice `Transcriber.transcribe` per utterance; INFO
  logs give seconds and counts, never text. The webm test now uses the
  pipeline's own model (so the float32 retry is in play), has no fixed
  seed (it should still catch a product failure), and on a miss prints the
  fallback steps faster-whisper logged. The decode path was checked: a
  SAPI fixture through WebM/Opus and `audio_windows` comes back at 16 kHz,
  same length, lag 0, correlation 0.986, no NaN (3 dB quieter: the test's
  encoder opens a stereo stream). Tests: `test_lecture.py` +2 (a "!!!!"
  window is decoded again and filed clean; one still garbage is dropped),
  `test_stt.py` +3 (`is_degenerate`; retry on an injected model; retry in
  float32 on our own, loaded once); all fail without the guard.
- Counts (Linux sandbox, Python 3.13, no eSpeak or Whisper model, so not
  the CI numbers): 9 failed, 1697 passed, 45 skipped, 16 errors, against
  master's 9 failed, 1687 passed, 45 skipped, 16 errors; the same eSpeak
  and wake-model audio set fails on both. The end-to-end test's mocked-stt
  case passed here with two SAPI fixtures standing in for the eSpeak phrase.
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

- New `kwil/web/ui.js` (all pages): the settings menu closes on a tap
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
- **Server:** new `kwil/attachments.py` sniffs the type from the first
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
- **Page:** `kwil/web/attachments.js` (public like `family.js`, also
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

- `kwil/memory_review.py` (`MemoryReview`): lists the assistant's own
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

- New `kwil/tools/home_assistant.py`: `ha_list` (devices by area or
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

## Spotify by voice (2026-10-20)

- "Play my study playlist", "play some lo-fi", "pause", "skip", "go back",
  "what's playing?", "turn it up", "play it on my phone". New
  `kwil/tools/spotify.py` drives the Spotify Web API player (Premium
  only) with nine tools: spotify_play (Kyle's own playlists first, then
  the catalog; a mood or genre plays a playlist, a song name plays that
  song), spotify_pause, spotify_resume, spotify_next, spotify_previous,
  spotify_volume, spotify_now_playing, spotify_devices and
  spotify_transfer (by name, or "phone", "PC", 手机, 电脑, "this
  computer"). No tool changes playlists or the library. Playback control
  never asks for a yes; catalog names and device names are marked
  untrusted.
- Sign-in: Authorization Code with PKCE, no client secret
  (`SPOTIFY_CLIENT_ID` in `.env`), minimum scopes. Once with
  `python -m kwil.tools.spotify` (paste back the 127.0.0.1 address)
  or the phone's owner-only "Connect Spotify" (`kwil/spotify_web.py`,
  `kwil/web/spotify.js`, modelled on `google_web.py`: single-use
  state map, bounded, 10-minute expiry). Token written with
  `write_private`; refreshed a minute before expiry, re-read from the
  file first (the wake-word process and the web app share it). If a
  refresh is refused because the other process just rotated the refresh
  token, the file is read again and its token (or, once, its new refresh
  token) is used, so the race no longer reads as "sign-in expired"; a
  token file briefly locked by a Windows replace is read again too. A 401
  is retried once after a refresh; nothing else is retried. Three scopes:
  user-read-playback-state, user-modify-playback-state,
  playlist-read-private.
- Errors read nicely in English and Chinese (i18n.py): no Premium, nothing
  open anywhere, which device (several open), volume not allowed (phones),
  sign-in expired, busy (429), offline. With no active device it plays on
  this computer's Spotify, or the only one open.
- Wake mode pauses Spotify on this computer at the wake word (in the
  background, so recording isn't delayed) and resumes it when back to
  waiting for "Hey Claude", unless Kyle used a Spotify control meanwhile
  (`[spotify] pause_while_listening`, `local_device`; `cli.run_wake`
  gained an `on_idle` hook, and the wake-mode wiring moved from `main`
  into `cli.run_wake_mode` so it is tested).
- `[spotify]` is off by default (config.py and config.toml); tools are
  registered only when enabled and signed in. Family members never get
  them (their own registry) and get 404 from the connect routes.
- `requests` was already a dependency, so no Spotify library was added.
- Tests: `tests/test_spotify.py` (63: PKCE, consent URL scopes, CLI
  sign-in, private token file, refresh and rotation, a 401 retry, the
  other process's token, each tool's request and result, Premium / no
  device / which device / volume / 429 / offline errors, no double skip,
  Chinese strings and device words, registration on and off, INFO logs
  without the query or names, pause-while-listening and its races, the
  two-process refresh race and a locked token file, untrusted device
  names, volume up from 0, and the wake loop's wiring: idle between two
  wakes with and without a follow-up window, a failing idle hook, pause
  before the chime, `run_wake_mode`),
  `tests/test_spotify_web.py` (10: owner only, state/PKCE, token saved
  privately, tools added live, replay and refusal write nothing, bounded
  states, family gets nothing, page and shell files),
  `tests/js/spotify.test.mjs` (7). Measured in the sandbox, not real
  hardware: full suite 1785 passed, 48 skipped (+73 on the master baseline of 1712 / 48); JS 202
  passed. Phone cache v13 (`/spotify.js` added to the shell).
- Final review LOWs: idle between wakes with `follow_up_seconds = 0` is
  now tested; a token file that is unreadable for permission reasons is
  retried only on Windows (a root-owned file on the Pi used to cost 1 s per
  call and log "locked"). +2 tests, each failing with its fix reverted.

## Phone app: each person picks their reply voice

- New `kwil/voice_picker.py` and `kwil/web/voice-picker.js`: the
  settings menu has **Reply voice**, with the voices this server really has
  in the person's language (Piper files in `[tts] voices_dir` with their
  language from the file name, Kokoro if set up and never with the server
  profile, the system voice from `[tts] fallback`, and ElevenLabs only with
  `ELEVENLABS_API_KEY`: listed from `GET /v1/voices`, cached an hour,
  tagged "paid", a voice ID can be typed in). **Preview** plays a short
  sample sentence (English or Chinese), cached.
- Saved per person: the owner's in `[tts] voice_file`
  (`data/voice_choice.json`; `/data/` on the cloud, `data/mom/` for the
  Chinese instance), a family member's in `<family dir>/<id>/voice.json`
  (written only while they are listed; deleted with their folder). Family
  members get ElevenLabs only with `[tts] family_paid_voices = true`.
- The session's voice is used from the next reply on: `WebAssistant.synthesize(text,
  session)` (reply audio and the voice WebSocket) asks
  `VoicePicker.engine_for(user, language)`. Engines are built once per voice
  and kept (4 at most, least recently used out); Kokoro voices share one
  loaded model (`KokoroTTS(shared=...)`). A voice that is gone falls back
  to the default with one INFO line; a chosen engine that fails mid-reply
  falls back to the default for that sentence.
- Endpoints: `GET /api/voices`, `POST /api/voices`, `POST
  /api/voices/preview` (session-bound, same-site, CloudGuard sign-in wall;
  `/api/voice` is already the voice WebSocket, hence the plural).
  `/api/state` has `voice_picker`. New settings `[tts] picker`,
  `voices_dir`, `voice_file`, `family_paid_voices`. Phone cache v15
  (`/voice-picker.js` added to the shell and to CloudGuard's public page
  files).
- Tests: `tests/test_voice_picker.py` (27: listing with a fake voices dir,
  Kokoro and a mocked ElevenLabs API, per-person saving where member A
  doesn't change B or the owner, preview audio and its cache, fallback on
  an unknown or removed voice with one log line, engine cache and shared
  Kokoro model, removal, routes behind CloudGuard), `tests/test_voice_ws.py`
  +1 (voice mode renders in the person's voice), `tests/js/voice-picker.test.mjs`
  (8). Each fails with its source change reverted. Full suite: 1740 passed,
  48 skipped (was 1712 / 48); JS: 217 node tests pass (sandbox, not real
  hardware).
- Review round 2:
  - Picking the default Piper voice reuses the default engine (no second
    model load).
  - Previews: callers asking for the same clip at once share one render;
    each session may have one preview rendering at a time and 10 a minute
    (429 past that).
  - A chosen voice whose engine fails is dropped and skipped for 5 minutes
    (one warning), rather than retried on every sentence; picking it again
    retries it.
  - A typed-in ElevenLabs ID that isn't in the cached list is checked once
    with `GET /v1/voices/{id}`: unknown -> 400, ElevenLabs unreachable ->
    503, nothing saved.
  - The new tests read and write text as UTF-8 (Windows), with a test that
    checks it.
  - Tests: `tests/test_voice_picker.py` +9 (36). Each fails with its fix
    reverted. Full suite: 1749 passed, 48 skipped; JS 217 pass.
- Round 3: a preview of a typed-in ElevenLabs ID is checked with ElevenLabs
  first (unknown: 404 like any unknown preview voice, unreachable: 503;
  nothing rendered or cached), and a test covers a second caller waiting on
  a failing render. Tests +2 (38); full suite 1751 passed, 48 skipped.

## Family invites, piece B: their own Google and speech language (2026-10-20)

A family member is now self-service: they connect their own Google from
their own phone, and a Chinese speaker's voice turns use Chinese speech
recognition and the Mandarin voice, with the assistant's own sentences in
their language, on the same shared cloud server as Kyle.

- **Their own Google** (`google_web.py`, `family.py`): one sign-in flow for
  the owner ([webapp] google_connect, as before) and family members.
  `GoogleConnect(family=..., owner=...)` answers `/api/google/status` and
  `/connect` about the caller's own account; `POST /api/google/disconnect`
  (family only) deletes their token and tools. A member's token goes to
  `<dir>/<id>/google_token.json` (`Family.save_google`, `write_private`,
  under the family lock, refused once removed) and their Calendar and
  Gmail tools (`Family._google_tools`, through the piece-A hook
  `_register_tools`) read only that file: `environ={}` (never
  `GOOGLE_TOKEN_JSON`), `save_token=False` (never a sign-in on the server),
  their `primary` calendar, their time zone, descriptions about them. Gmail
  stays drafts-only (`NoSendGmail`), deletes and drafts ask first. Tools
  join their open conversations in place (`_refresh_tools`: add, then
  remove). The OAuth state is bound to who started it and their session,
  and to the browser by `hc_google` (Lax, path `/google/callback`, 10 min,
  nonce hashed with the state); the callback also needs the session still
  signed in as that person. Per-person cap 4 pending, 32 in all. Family
  sign-in uses a separate *Web application* client
  (`GOOGLE_WEB_CLIENT_JSON` or `[family] google_client`) and the server's
  own address (`PUBLIC_ORIGIN`, new env override). On the cloud the owner
  gets 404 on these routes (his Google is `GOOGLE_TOKEN_JSON`).
- **Speech per session** (`webapp.SpeechByLanguage`): language ->
  (Whisper model, Piper voice) for sessions whose language isn't the
  server's; made on first use and cached per model/voice, not per session.
  `WebAssistant.transcribe/transcribe_upload/synthesize(..., language)`;
  push-to-talk, `/api/confirm`, reply audio (the language stored with each
  reply id in piece A) and the voice socket (`voice_ws`) pass the
  session's. Still one STT lock and one TTS thread. `family_speech(config)`
  uses `[family] zh_stt_model` (`small`, `ZH_WHISPER_MODEL`) and
  `zh_piper_voice`, fallback "print" (the phone's own voice, never English
  eSpeak reading Chinese).
- **i18n per session** (`i18n.py`): `t(key, lang)`, `is_zh(lang)`,
  `current()`, `using(lang)` (a ContextVar). Each push-to-talk turn,
  `chat_stream` run and voice-conversation worker runs in its session's
  language, so error sentences and tool confirmation questions (calendar,
  Gmail) are in the language of the person answering; `VoiceConversation
  (language=...)`; the busy-turn sentence and the failed-callback page are
  translated too. Fixes the piece-A limitation that `i18n.t` was
  server-wide.
- **Page**: a family member's page gets `data-google` when their Google is
  set up; the first-run guide's step now says to connect *their own*
  account; a **Disconnect Google account** button (family, connected).
  Service worker cache v11.
- **Cloud/Render/Docker**: CloudGuard lets `/google/callback` through
  without the session cookie; `cloud.build_app` passes Google and the
  language. Dockerfile `FAMILY_ZH=1` bakes in Whisper `small` (about
  480 MB) and `zh_CN-huayan-medium` (about 60 MB; published sizes, not
  measured here: Hugging Face is blocked in the sandbox); `render.yaml`
  adds `PUBLIC_ORIGIN`, `GOOGLE_WEB_CLIENT_JSON` (secrets) and
  `ZH_WHISPER_MODEL`; `/data/users` is on the disk (server profile). Memory
  with a Chinese speaker active is estimated at 0.5-0.7 GB on top of the
  measured 564 MB, not measured; Standard (2 GB) stays the plan.
- Config: `[family] google` (true), `google_client` (""), comments for
  `zh_stt_model` / `zh_piper_voice`; `PUBLIC_ORIGIN`, `ZH_WHISPER_MODEL`
  env overrides. `client_config("")` no longer reads the current folder.
- Tests: `tests/test_family_google.py` (23): A's token separate from B's
  and Kyle's, and their tools build credentials from their own refresh
  token with `GOOGLE_TOKEN_JSON` set; the owner has no connect on the
  cloud; another session's state, a browser without the binding cookie, a
  signed-out session and a removed person are refused (nothing written,
  token endpoint never called); failed page in her language; tools only
  after connecting (and in her open session at once), no owner tool or
  "Kyle" in what Claude is sent; Gmail drafts-only, deletes ask; disconnect;
  removal deletes the token, not others'; no secrets, state, code or names
  at INFO (caplog); off without a web client or with a Desktop one; zh
  push-to-talk and reply audio use the zh model and voice, en uses the
  server's, two zh speakers share one; zh voice socket; lazy and cached
  models; `i18n.t` follows the session language (threads isolated); push-
  to-talk and calendar-question sentences per person; voice sentences;
  build_assistant wiring; busy sentence; `client_config("")`. A test-only
  guard fails any Google token refresh (a first draft without `expiry` in
  the fake token made one real refresh request with a fake client id; no
  account was involved). `test_cloud.py` +2 (the callback passes the guard
  and family status works there; Dockerfile/render.yaml match the config).
  `tests/js/i18n.test.mjs` +3 (Disconnect, its failure and "no", the guide
  wording). Updated: `test_family.py` (the owner-only Google test now
  checks a family member never gets Kyle's connected Google),
  `test_google_web.py` (status has `disconnect`), `test_voice_ws.py` and
  `test_family.py` fakes take the language, `test_webapp.py` cache v18,
  `test_docs.py` allows `--build-arg`, `conftest.py` also clears
  `GOOGLE_TOKEN_JSON` / `GOOGLE_CLIENT_JSON` / `GOOGLE_WEB_CLIENT_JSON`.
  Each source change was undone alone and its tests failed: `environ={}`,
  the nonce check, the session check, registering and refreshing the
  tools, the turn's `i18n.using`, transcribe and synthesize per language,
  `voice_ws` language, the CloudGuard path, the disconnect delete, the
  callback language, `question_suffix` language, the description rewrite,
  the Dockerfile and render.yaml entries, `family_speech` wiring, the page
  tag, the busy sentence, the `client_config` guard, and the JS button and
  guide text.
- Counts (Linux sandbox, Python 3.13): pytest 1712 -> 1737 passed, 48
  skipped (unchanged). JS: i18n 14, family 9, backend-router 33, ui 4,
  devhub 12, voice-core 14, agent-dashboard 126, all passing.
- Round 2 (judge, no security defect):
  - `owner_of` is back in `create_app` (other branches' owner-only routes
    use it; `session_of` there would hand family Kyle's data), also as
    `app.state.owner_of`.
  - A family member's expired, revoked, scope-short or missing token now
    says "Reconnect Google from the app's settings" (EN/ZH,
    `i18n` `google_reconnect`, in the turn's language) instead of Kyle's PC
    sign-in, token.json or `GOOGLE_TOKEN_JSON` advice, and logs a warning
    without that advice: `get_credentials(..., in_app=True)`,
    `MissingScopes(in_app=)`, `GoogleCalendar`/`GmailInbox(in_app=)`, set
    by `Family._google_tools`. Kyle's messages are unchanged.
  - New tests (each fails with only its change undone): `owner_of` (owner
    200, family 404, nobody 401); the in-app refresh message and log (EN and
    ZH), scope and missing-token messages; `save_google`'s re-check (the
    person removed while Google answers: 400, no file); the binding
    cookie's HttpOnly, Secure, SameSite=lax, Path, Max-Age=600; the voice
    socket twin of the Chinese calendar question (`chat_stream`'s
    `using`); `VoiceConversation._work`'s `using` (kept: a backend that
    answers in the worker thread, ChatBackend, needs it).
  - Docs: BLOCKERS says `PUBLIC_ORIGIN` must be the exact https address
    family members open and that the zh memory figure is to be measured;
    MANUAL_TEST marks the home-screen-app sign-in as release-gating.
  - Counts (Linux sandbox, Python 3.13): pytest 1737 -> 1744 passed, 48
    skipped. JS unchanged (i18n 14, family 9, all files passing).

## Interrupt by talking (barge-in) and follow-ups, PC and phone (2026-10-20)

- **PC wake-word loop** (`kwil/wakeword.py` `BargeInSpeaker`,
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
  barge-in, a yes/no question or a turn without audio. Phone cache v19
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
  interruption cleared at a new wake (`test_barge_in.py` 42; 43 after the merge with Spotify, which adds `test_idle_hook_waits_for_the_barge_in_turn`: the back-to-idle hook (Spotify resume) fires only after a barge-in turn). The pipeline
  echo is now 0.2x (still above the VAD threshold), since a longer
  measurement sees more of the reply's loud parts. Full run: 1755 passed,
  48 skipped; JS files all pass.


## Health and function checks, and safe go-live (2026-10-20)

Kyle: "The app should not have undiscovered bugs that are discovered by the
users when the app restarts", and "it should never get to the point where
the app does not work when I try to use it". Three things went wrong that
day and are now caught automatically: Mom's and Kai's apps died with a
Claude session and returned 502 for hours; a real multi-turn conversation
failed with an API 400 (thinking-block signatures) that no mocked test
saw; hands-free barge-in misfired (fixed on its own branch; voice-core.js
is untouched here).

- **Watchdog** (`kwil/watch.py`, `python -m kwil.watch`; the
  `\HeyClaude\Watch` task every 5 minutes via
  `scripts/install_watch_task.ps1` or `install_family_tasks.ps1 -Watch`,
  `deploy/hey-claude-watch.{service,timer}` via `scripts/install_watch.sh`
  on the Pi). Finds every instance with install_family_tasks.ps1's rule.
  Quick check: `/healthz`, the page and every script/stylesheet it loads,
  served version vs HEAD, the phone address (`[webapp] public_origin` or
  the `tailscale serve status --json` entry for the port). Test
  conversation (smoke.py `--conversation`, `run_conversation`) after a
  restart (new `/healthz` `started`, loopback only) or a new version, every
  `functional_hours` (6), retried after `retry_minutes` (30): text with a
  read-only tool, a follow-up that must recall a number from the first
  turn, an audio turn (the existing fixture), a hands-free turn over
  `/api/voice`, and a last text turn sent while the voice socket is still
  open (the path that broke). Self-heal: restart once per failure
  (`scripts/restart_instance.ps1`, or `[watch] restart_command`); still
  failing on code newer than the last known good (the last commit whose
  test conversation passed here, `logs/update-state.json` `last_good`):
  `git reset --keep` to it (only an ancestor, never over local-only
  commits; config/.env/data untouched), restart, reject the commit.
  Outside causes (no internet, Claude down, rate limit, bad key) never roll
  back. State in `data/watch_status.json` (private, atomic), every result
  in `logs/watch.log`. Alerts only on a change of state and for one-off
  events: the owner's banner (`/api/state` `health`, Kyle's own instance
  and owner sessions only; `ui.js renderHealth`) and the dev team
  coordinator (`CoordinatorLink.send(source="watch")`). Alert text is
  labels, check names and fixed reasons (`smoke.Outcome.reason`), never
  reply text or keys.
- **Check session** (`POST /api/check-session`, `WebAssistant.start_check`):
  a fresh session becomes one for good: no recorder (no history),
  `ClaudeChat.check_mode` (no memory summary or review offer),
  `SessionTools.check_mode` (only `tools.READ_ONLY_TOOLS` offered and run).
  The smoke client uses it whenever the server has it and signs out at the
  end; the conversation mode refuses to run without it.
- **Go-live gate** (`watch.gate_check`, `candidate_check`): each instance is
  started again on port + `side_port_offset` (100) as `--candidate` (no
  history database, no saved sign-ins, no hub, no resumed lecture jobs,
  its own log, keys stripped from its environment) and must pass the test
  conversation before anything restarts on the commit. Used by the updater
  (inside its lock, after the import/`--check` smoke), by the host
  supervisor for a pull by hand (the web host checks; the wake host waits
  for its verdict), by the watchdog when an app runs an older version than
  HEAD, and by `scripts/deploy.ps1` / `deploy.sh` (`kwil/deploy.py`:
  pull, gate, switch or refuse, then a live check). A failing commit is
  undone and rejected; one that fails only for outside reasons is neither.
  `install_family_tasks.ps1 -Restart` now goes through it. After the host
  restarts on new code it starts the watchdog's test conversation at once.
- **Claude down**: 5xx/overloaded after the retries now says "Claude is
  unreachable right now. I retried a few times; please try again in a
  minute." (`cli.CLAUDE_DOWN`, `i18n claude_down`), and the smoke check
  counts it as an error reply.
- **CI**: new `api-contract` job runs `tests/test_contract.py` with the
  `ANTHROPIC_API_KEY` secret (skips cleanly without it) on pushes and PRs,
  against the cheapest model (`CONTRACT_MODEL`, default claude-haiku-5-5)
  and the default model; name it in master's required checks.

How it was tested (offline, Linux sandbox, Python 3.13):
- `tests/contract_fakes.py`: a contract-shaped Messages API fake (validates
  role order, tool_use/tool_result pairing, thinking signatures bound to the
  client, thinking first before tool results; answers with the SDK's own
  `Message`/`ThinkingBlock` types, streamed or not).
- `tests/test_contract.py` (2 offline + 4 live, skipped here): two
  ClaudeChat instances sharing one history then a follow-up (fails with
  the thinking fix reverted: 400 "Invalid `signature`"); the whole web
  app conversation through smoke, voice socket included, checking that the
  last text turn ran with the voice conversation open, nothing reached
  history or memory and the write tool was never offered or run.
- `tests/test_watch.py` (40): discovery, Tailscale mapping, `[watch]`
  mirrored in config.toml, each quick-check failure, alerts only on a
  change, restart once per failure, a restart that fixes it raises no
  alert, test conversation after a restart and when due (and sets the last
  known good), rollback to the last good, no rollback for outside causes or
  over local commits, gate before switching, refusal when the gate fails,
  a gate that can't tell, side-port candidate (port, flags, stripped
  environment), the updater's gate (reject vs retry), host gating of a hand
  pull and the wake host waiting, host starting the watchdog after a
  restart, deploy refuse and switch paths, banner contents and staleness,
  banner only on Kyle's instance and for the owner, no token or reply text
  in alerts/logs/status (caplog), the coordinator channel, fixed reasons,
  the check session server side, the smoke conversation (passes, keeps the
  socket open, fails without the tool call or the recall, never runs
  without a check session), the "Claude unreachable" sentence.
- Revert proof: each of 25 source changes (plus the banner in ui.js) was reverted alone and its test
  failed (list in the round's report); then restored.
- `tests/js/ui.test.mjs` +1 (banner lines and element); `tests/ps/
  restart_instance.tests.ps1` (7 checks, runs on Windows CI);
  `deploy.sh`/`install_watch.sh` added to the bash parse check.
- Full run (Linux sandbox, Python 3.13): 2157 passed, 58 skipped (master
  b1e40b9: 2112 passed, 48 skipped). The 10 new skips: the 4 live contract
  tests (no key here), and PowerShell/shellcheck checks of the new scripts
  (not installed here; they run in CI). JS: 253 pass (was 252). The test
  that changed: `test_get_reply_error_messages`' "other" case is now a 400
  (still "went wrong") and a 500 case expects the new "unreachable" sentence.
- Not measured: the test conversation's real cost and duration (no API key
  in the sandbox; estimated ~30k input tokens per instance per check), the
  side-port copy's startup time and memory on the PC (Whisper + Kokoro
  loaded twice for a minute or two), and the Task Scheduler / systemd
  installs (no Windows or systemd here). MANUAL_TEST "Health checks and
  safe updates" covers them.

### Round 2 (judge findings)

1. Healing an app that is only starting: the host starts the watchdog only
   after the hand-pulled app answers on the new version (`healthy`); no
   heal while the update lock is held; an app that doesn't answer gets
   `[watch] start_grace_seconds` (300, new setting) before a restart and
   again after it; `restarted` is recorded only for a restart that ran, and
   a rollback needs one. Tests: `test_host_starts_the_watchdog_only_once_the_hand_pulled_app_answers`,
   `test_no_restart_or_rollback_while_an_update_holds_the_lock`,
   `test_no_rollback_without_a_restart_that_ran`,
   `test_down_app_gets_the_start_grace_and_a_long_wait_before_any_rollback`,
   `test_a_slow_start_after_the_restart_is_waited_for`,
   `test_family_copy_down_without_restart_rolls_back_nobody`.
2. Phone-address (Tailscale) failures are reported but never heal or roll
   back; no restart while a turn runs (`busy`). Tests:
   `test_phone_address_failure_never_restarts_or_rolls_back`,
   `test_never_restarts_in_the_middle_of_a_turn`.
3. `rollback_checkout` re-reads HEAD and the branch under the lock and needs
   HEAD on the bad commit. Tests: `test_rollback_rereads_head_and_leaves_a_new_local_commit`,
   `test_rollback_checkout_needs_head_on_the_bad_commit_and_the_branch`.
4. A busy side port (after stopping a leftover `--candidate`) and a copy
   that doesn't start are inconclusive (`Check.outside`, gate None); a
   failure is retried once before a commit is refused. Tests:
   `test_a_busy_side_port_is_inconclusive_not_a_rejection`,
   `test_a_copy_that_does_not_start_is_inconclusive`,
   `test_gate_retries_once_before_rejecting`.
5. `updater.take_lock`/`lock_stale`/`pid_alive`: a lock whose PID is dead is
   stale at once (watch.lock and update.lock; `locked()` too); the watch
   task's limit is 2 h (systemd 2h), the watch lock's age limit 3 h. Tests:
   `test_a_killed_runs_lock_is_stale_at_once`, `test_the_watch_task_outlives_the_worst_gate`.
6. `rolled_back` is set only after the lock was taken. Tests:
   `test_rollback_lock_taken_after_the_restart_sets_nothing`,
   `test_rollback_happens_once_the_lock_is_free`.
7. `Updater.rollback` rejects only after the reset worked. Test:
   `test_reject_only_when_the_reset_worked`.
8. `Updater._state_lock`: state writes take a file lock (PID, stale when its
   process is gone) plus a thread lock. Tests: `test_state_writes_wait_for_another_process`,
   `test_state_writes_dont_lose_each_other`.
9. `install_family_tasks.ps1 -Restart` runs deploy inside `Push-Location
   $repoDir`. Test: `test_family_restart_runs_deploy_from_the_project_folder`.
10. Check sessions skip the Pinecone search (`ClaudeChat.recall`). Tests:
    `test_check_session_never_queries_memory`, and the contract web test
    now asserts `memory.retrieve` was never called.
11. `test_functional_check_after_a_restart_and_when_due` asserts the test
    conversation is not due at 5 h and is at 6 h.
- The judge's probes are folded into `tests/test_watch.py` with the
  corrected expectations (the tests above). Each fix was reverted alone and
  its test failed (22 reverts, bytecode caches cleared between them; the
  functional_hours check by setting the default to 4). Two older tests now
  pass the start grace between runs (`later()`), and the fake clock starts
  at the real time so lock ages are measured right.
- Full run after round 2 (Linux sandbox, Python 3.13): 2179 passed, 58
  skipped (round 1: 2157 / 58; master b1e40b9: 2112 / 48); JS 253 pass.
  `tests/test_watch.py` has 62 tests. ruff: no findings in the new files,
  and none added to the files this work touched (the repo-wide count, 527,
  is the same as master's).

### Round 3 (judge findings on round 2)

- N1: new code that exits before answering on a free side port is a
  failure again (tried twice, then refused), not inconclusive; only a busy
  side port (and outside causes) stay inconclusive. Inconclusive results are
  counted per commit (`Updater.note_inconclusive`, cleared by `mark_gated`):
  the host backs off 1, 5, 15, then 60 minutes (`host.GATE_BACKOFF`), and the
  watchdog alerts once after 3 tries or 30 minutes ("The go-live check of X
  can't run (...)"). Tests: `test_a_commit_that_crashes_on_start_is_refused`,
  `test_repeated_inconclusive_gate_alerts_once`,
  `test_inconclusive_alert_after_half_an_hour_too`,
  `test_host_backs_off_an_inconclusive_gate`.
- N2: `pkill -f -- <pattern>` (the pattern starts with dashes). Test:
  `test_pkill_pattern_comes_after_double_dash`. An autouse fixture in
  `tests/test_watch.py` now fails any test that would run a real command
  (pkill, PowerShell, a restart) through `watch._run`.
- N3: a rollback whose reset fails still clears `applied`, and
  `Host.restart_on` refuses a commit the gate refused. Tests:
  `test_failed_reset_clears_applied`, `test_host_never_restarts_on_a_refused_commit`.
- N4: while the update lock is held a down app is still never healed, but
  it is reported after `start_grace_seconds` + 15 minutes
  (`watch.LOCKED_MARGIN`). Test: `test_hung_update_doesnt_hide_a_down_app_forever`.
- N5: `updater.clear_stale` renames a stale lock aside atomically and puts
  it back (os.link, never over a newer one) if it turned out to be a fresh
  lock another process took meanwhile. Test:
  `test_a_fresh_lock_survives_a_late_stale_clear`.
- The judge's round-3 probes are folded in with the corrected expectations
  (the tests above). Each fix was reverted alone, with `__pycache__`
  cleared, and its test failed (10 reverts); the round-2 reverts were rerun
  against the new code and all 21 still fail.

### Follow-up (judge round 3 LOWs)

- The watchdog now honours the hosts' backoff: `Watch.switch` skips the
  go-live check until the last inconclusive try plus `gate_backoff(count)`
  (`Updater.gate_wait`). The table moved from `host.GATE_BACKOFF` to
  `updater.GATE_BACKOFF` / `updater.gate_backoff`, used by both. An hour of
  5-minute watchdog runs now gates 4 times (0, 5, 10, 25 min), not 12.
  Tests: `test_watchdog_backs_off_an_inconclusive_gate_like_the_hosts`,
  `test_gate_backoff_is_one_table_for_hosts_and_watchdog`.
- The automatic-update path alerts too: `Watch.alert_inconclusive` reads the
  stored record whatever its commit (`Updater.last_inconclusive`). Off HEAD
  it counts only while the commit is neither refused nor checked since
  (`gated(sha) is None`), and only by tries (3), never the half-hour rule, so
  a hand pull undone after one try doesn't alert. Tests:
  `test_inconclusive_checks_on_the_update_path_alert_once`,
  `test_an_off_head_record_alerts_only_while_its_commit_is_pending`.
- A check copy killed by a signal (negative exit code, e.g. the Pi's OOM
  killer) is inconclusive ("killed by signal 9"), not "did not start", so it
  isn't refused after two tries. Test:
  `test_a_copy_killed_by_a_signal_is_inconclusive`.
- Windows CI fix (PR #28, windows-latest / 3.12: 2 failed): two tests used an
  empty `restart_command` to model "no way to restart", but on Windows
  `restart_command()` falls back to `restart_instance.ps1`, so a restart ran
  and the rollback followed. `watch.WINDOWS` now holds the platform; those
  tests pin it to False, and
  `test_windows_falls_back_to_the_task_scheduler_restart` covers the
  fallback. Reproduced on Linux by forcing the Windows branch: the same two
  tests failed with the same reset to ccccccc; with the fix they pass.
- README and SECURITY now say: 1, 5, 15, then 60 minutes after the last try
  for a pull by hand (hosts and watchdog), the next `[update]` check for an
  automatic update.
- Each fix was reverted alone with `__pycache__` cleared and its test failed
  (6 reverts). Full run (Linux sandbox, Python 3.13): 2205 passed, 58
  skipped (HEAD e49c3a5: 2199 / 58, i.e. minus the 6 new tests); `tests/test_watch.py` has 76 tests. ruff:
  no new findings in the touched files.

## Fix: phone barge-in cancelled replies before any audio (2026-10-20)

Kyle's phone log (12:47 and 12:52 UTC): "Voice barge-in on turn N after 0
chunk(s) + 0%" on almost every turn, each turn then "replied 0 chars".
- **Root cause (page):** `voice-core.js` `TurnController` treated any
  confirmed speech in the **thinking** state as a barge-in
  (`onVad` "speechConfirmed" and `_utterance`, there since the MVP). So
  Kyle's trailing words, room noise or the echo of the app's own "thinking"
  note / follow-up cue cancelled the turn before its first audio chunk.
  `[voice] barge_in = "off"` didn't help: it only ignored the mic in the
  **speaking** state.
- **Root cause (server):** the cancel closes the Claude stream from another
  thread. A closed socket can read as a plain end of stream rather than an
  error (reproduced against a local SSE server: the SDK's `text_stream`
  ends quietly and `get_final_message()` returns `stop_reason=None` with
  only the thinking block received so far, signature ""). `ClaudeChat`
  stored that fragment as the reply, so the next request carried an
  unsigned thinking block (the 400s that PR #26 works around).
- **Fix, page:** barge-in needs the reply's audio to have started and
  speech that began after that, with the same `barge_in_ms` rule as before.
  Speech while it is still thinking (or with barge-in off, between
  chunks) is **held and sent as the next turn** once the reply has played
  (one slot, newest wins; a real barge-in, pause or End drops it; never
  sent while a yes/no question is open). Chosen over "ignore" so "...and
  on Friday" isn't lost, and over "append to the turn" because that needs
  the running turn cancelled. Audio that arrives while Kyle is still
  talking plays on and the state catches up when he stops. The
  `Endpointer` gets `holdOff(ms)`: `voice.js` calls it for every earcon and
  the follow-up cue (sound length + 250 ms), so they can't start an
  utterance. Pausing (app in the background) still stops a thinking turn.
  Phone cache v20.
- **Fix, server:** `ClaudeChat._create` checks the final message: no
  `stop_reason` means the stream ended early; when cancelled that is the
  normal `_Cancelled` path (history gets "[interrupted: Kyle started talking
  before I said anything]", no thinking block), otherwise `StreamEndedEarly`
  drops the turn from history like any failed request. The barge-in log
  line also says how many chunks had been sent ("(0 sent)").
- Tests (round 1): `tests/js/voice-core.test.js` 28 (was 21): "talking while it
  thinks cancels that turn" is replaced by its opposite (held, sent after
  the reply, no interrupt), plus barge-in only by speech begun after the
  audio started, speech running past the end of the reply (no stuck
  "thinking"), speech after a yes, barge-in off while thinking, pause/End
  drop a held utterance, `Endpointer.holdOff`, and the earcon wiring in
  `voice.js`. `tests/test_voice.py` +2: a 0-chunk cancel whose stream ends
  quietly with a thinking-only fragment (`FakeStream(quiet_close=True)`)
  leaves valid history and a clean next request, with the INFO line
  checked by caplog; a stream that ends early without a cancel is not
  kept. Each fails with its source change reverted. (Final counts are
  under round 3.) Not checked on a real phone: how often trailing words or room noise now get
  held as a next turn (MANUAL_TEST "Talk while it is still thinking").

### Round 2 (judge findings)

1. Muting while Kyle was talking over a reply that then finished left the
   state "thinking", and his next words sent an interrupt for a reply he
   had heard in full: `setMuted(true)` now runs the same catch-up as the
   end of speech. Speech only counts as talking over the reply while its
   audio is audible (`replyAudible`: playing, or "speaking"); speech in a
   gap between chunks is held like speech while it thinks.
2. Words held before a yes/no question could be sent as a new turn after
   it (an early "yes" agreeing to whatever came next): a `confirm` drops
   the held words, and while a question is open (asked, or just answered)
   speech that isn't the answer is dropped, not held. Speech right after
   the answer no longer barges in either (that is covered by 1 and the
   drop; a separate `audioStarted` reset on the answer turned out to be
   redundant, so it isn't in).
3. The stricter VAD threshold now applies whenever reply audio plays
   (`controller.replyAudible`, read per frame in `voice.js`), also while
   the state shows "thinking" because he was talking when it started.
- Merged origin/master (b1e40b9, #25 and #26) by fast-forward; CHANGELOG
  keeps both Fix entries.
- Tests: `voice-core.test.js` 34 (+6), each failing with its fix reverted
  (the after-the-answer test fails with fixes 1 and 2 reverted together).

### Round 3 (judge findings)

1. A yes/no question that played under Kyle's speech (he started just
   before its audio) left the page on "Thinking…": the question's end and
   his speech's end both did nothing, so his real "yes" was dropped until
   the 60 s timeout. New `_settle()` rule, run when speech ends, on mute
   and when playback goes idle: in "thinking" with nothing playing and him
   not talking, a heard question with no answer sent yet goes to
   "listening", and a finished turn ends the reply. The page now knows the
   question's audio was played (`questionAudio`) and that an answer is on
   its way (`answerSent`, so it stays "thinking" until `confirm_answer`).
   Sweep: a turn that ends with its question unanswered (the server gave
   up) now clears `awaitingAnswer`, so the next words are a new turn
   instead of an answer to nothing.
2. The reply's own echo in a pause between sentences could be queued as
   the next turn. Each time playback goes idle the page calls `echoTail`
   (`voice.js`: `endpointer.holdOff(CUE_TAIL_MS)`, as for the earcons), and
   speech that began in such a pause is dropped when reply audio played
   under more than half of it (timed with `performance.now()`).
3. PROGRESS: the stale round-1 counts are gone; README says a pause between
   sentences doesn't count as barge-in either.
- Tests: `voice-core.test.js` 40 (+6): the question-under-speech sequence
  (speechEnd and misfire), an unanswered question closing with its turn,
  echo in a pause dropped, real speech in a pause still asked, and the
  `echoTail` wiring. Each fails with its fix reverted, except the
  `_settle()` call in `onPlaybackIdle`, which is defensive: no reachable
  sequence leaves "thinking" with playback ending and him silent, so no
  test fails without it.
- Final counts (Linux sandbox, Python 3.13): 2114 passed, 48 skipped
  (master b1e40b9: 2112 passed, 48 skipped); JS files 271 pass (master:
  252).

## Google Calendar: colors, labels, repeats, guests, invites, free time (2026-10-20)

Kyle asked for "all the features that Google Calendar offers", starting
with event colors and the color labels. `tools/calendar.py` now has 8
tools (the 4 existing names are kept; `preflight.py` and the family tests
use them):
- `list_calendar_events`: also search words (`query`, up to 100
  characters) and `calendar` (a name, an id, or "all": up to 10 calendars,
  50 events each, merged by time; one unreadable calendar is skipped and
  counted). Each event now says its color or label name, whether it
  repeats, Kyle's own RSVP, the number of other guests, the Meet link,
  "free" when it doesn't block time, and the first 300 characters of its
  notes.
- `create_calendar_event` and the new `update_calendar_event`: color
  (Google's 11 classic colors by name, plain words like "red", or a bare
  colorId), Google's named **event labels** (`eventLabelId` with
  `eventLabelVersion=1`, sent only when setting a label, since that
  parameter without a label removes it; switching from a label to a
  classic color clears the label first, then sets `colorId`), repeat rules
  (RRULE/EXDATE/RDATE, 5 lines of 200 characters; timed repeats need a
  named time zone, else a clear error), reminders (popup minutes 0-40320,
  at most 5; `[0]` is a reminder at the start, `[]` none, omitted the
  calendar default), guests (20 per call), a Meet link (fresh `requestId`
  per call, `conferenceDataVersion=1`), busy/free, visibility, and on
  updates title/place/notes, removing guests and "guests can modify".
- `move_calendar_event`, `update_calendar_event`, `delete_calendar_event`:
  `scope` "this" (the occurrence id that the listing returns) or "all"
  (the series, through `recurringEventId`). A series move shifts the
  first occurrence by the same wall-clock amount, so 10 AM stays 10 AM
  across DST; moving a "BYDAY" series to another weekday is refused with
  a hint to change the repeat instead.
- New `respond_to_calendar_invite` (accepted/declined/tentative, optional
  note): the full guest list goes back with only Kyle's entry changed.
- New `find_free_time` (up to 14 days, a minimum length, from/to hours):
  built from events.list (no new scope). Events shown as free and declined
  invites don't count, all-day busy events block their day, and the math
  is in UTC so DST days come out right.
- New `list_calendars` (names, ids, main, access role, and the color
  choices: classic, Kyle's labels, `[calendar] color_labels`). Only
  registered when the token has the two new read-only scopes.

Color names resolve in this order: `[calendar] color_labels` aliases (new
setting, e.g. `{ School = "Basil" }`), the calendar's label names,
Google's classic names, everyday words, a colorId. An unknown color is an
error listing the choices. Labels and the calendar list are cached for 5
minutes (20 entries).

Scopes: `CALENDAR_EXTRA_SCOPES` (`calendar.calendarlist.readonly`,
`calendar.calendars.readonly`) are added to the sign-in, but
`CALENDAR_SCOPES` (what `get_credentials` requires) is still only
`calendar.events`, so existing tokens keep working. `cli.register_google`
passes `extras` and Kyle's `color_labels`; without the scopes it prints a
re-sign-in hint, `--check` shows "Calendar labels access", and label
names, calendars by name and `list_calendars` wait. `family.py` gives a
member the extras only if her own token has them, and never Kyle's
`color_labels`. `requirements.txt`: `google-api-python-client>=2.199`
(the first release with `eventLabelVersion`).

Safety: adding or removing guests and answering invites always ask, naming
the addresses (or the organizer); Google is told to email guests
(`sendUpdates="all"`) only on those calls, otherwise `"none"`. Deleting a
series asks "delete every ..., the whole series?". Other changes ask when
untrusted text is in the conversation. Event notes, other people's
invites, RSVP results and calendars Kyle doesn't own (`accessRole` not
"owner", or an id not in his list) mark results untrusted. INFO logs have
counts and field names only. SECURITY.md has the details.

Not covered (the README says so): focus time, out of office and working
location (work or school accounts only), attachments and quickAdd, and
creating or renaming labels and calendars.

Tests: `tests/test_calendar_features.py` (26, new) covers every option
above on a `MagicMock` client with real shapes (invites without
`organizer.self`, occurrences with `recurringEventId` and
`originalStartTime`, `labelProperties.eventLabels`, `calendarList` items
with `accessRole`), the DST cases (a series move across the November
change; free time on Nov 1, when midnight to 6 AM is seven hours), a
caplog test that the new tools log no titles, notes or addresses at INFO,
and a real-shape guard: the calls the tools make are replayed on
`googleapiclient.discovery.build("calendar", "v3", static_discovery=True)`
and constructed, never executed (an unknown parameter raises TypeError).
`test_security.py` +6 (update in INJECTED_CALLS, invites ask without web
content, RSVP asks, series delete says "every", a series update after a
fetched page asks), `test_i18n.py` +1 (the Chinese questions),
`test_family_google.py` +1 (flags, the extra-scope gate, no
`color_labels` leak, no "Kyle" in the schemas), `test_gmail.py` +1
(`test_old_token_keeps_calendar_core_tools`; the two sign-in scope pins
now include the read-only extras), `test_doctor.py` +1. `make_service`
in `test_tools.py` gained `by_id`, labels, the calendar list and
instances. With the source change reverted, all 38 new or changed tests
fail; separate mutations (no non-owner taint, reminder 0 rejected, RSVP
dropping other guests, free time in wall-clock time, no
`eventLabelVersion`, no invite question) are each caught.
- Counts (Linux sandbox, Python 3.13): 2160 passed, 48 skipped (master
  f1eb136: 2124 passed, 48 skipped). JS unchanged. Not checked against
  the real Google API (mocks only); MANUAL_TEST 5b covers that.

### Round 2 (judge findings)

1. A series move from an occurrence that had been moved on its own
   shifted by the wrong amount (Tuesdays at 10, Nov 10 moved alone to
   2 PM, "move all to 11" set the series to 7 AM). The shift and the
   day check now measure from `originalStartTime` (falling back to
   `start`).
2. The invite and remove questions said "and 1 more" past three
   addresses, so an injected 4th guest could hide. They now name every
   address (at most 20 per call), in English and Chinese. When the event
   can't be read, they still name them ("an invite for that event to ...").
3. The RSVP question didn't read out the note that Google emails to the
   organizer. New `cal_rsvp_note` / `cal_rsvp_note_any` (EN, ZH): "...and
   send the note: {comment}?".
4. A weekly series couldn't move to another weekday, and the refusal
   pointed at a tool that can't do it. `follow_new_day`: a one-weekday
   BYDAY is rewritten to the new weekday in the same patch. Several
   weekdays, an nth weekday (`2TU`), BYMONTHDAY/BYYEARDAY/BYMONTH/BYWEEKNO/
   BYSETPOS with a day change, and EXDATE/RDATE with any shift (their
   dates would stop matching) are refused plainly: "Say the new days and
   time and I'll make a new series, then delete the old one." A rule
   with no BY part follows the start by itself.
5. `find_free_time` offered hours already past. It now starts at the
   turn's "now" (`timeutil.now`, so TURN_NOW pins it), rounded up to the
   next 5 minutes, and skips days that are over.
6. Bad input reached the log through the ERROR traceback (`Not an email
   address: 'Ana Lopez'`, the reminder value, the unknown color). The
   messages no longer repeat the value ("Guest 2 is not an email
   address").
7. PRIVACY.md: the Calendar row says Google emails invited or removed
   guests and organizers (with the note) after a spoken yes, and the
   Anthropic row lists colors, labels, guest counts, 300 characters of
   notes, calendar names and guest addresses. The family paragraph no
   longer says "nothing is ever sent".
- Also: move and update results are marked untrusted on a calendar Kyle
  doesn't own; if the second step of a label-to-color change fails, the
  error says "the label was removed but the color wasn't set"; README,
  SECURITY and MANUAL_TEST say moves and deletes don't ask Google to email
  guests (Google's docs say some notices may still go out).
- Tests: `test_calendar_features.py` 39 (+13): the judge's probes as
  tests with the correct expectations (an already-moved occurrence; a
  weekday move rewriting BYDAY, replacing round 1's "refused"
  expectation), each refused rule kind (5 cases), EXDATE refused and a
  daily rule following, every address named (6 guests, EN and ZH), the
  RSVP note, free time with a pinned clock, a caplog test over the
  formatted ERROR tracebacks, taint on another person's calendar, and the
  label message. A file-wide fixture pins TURN_NOW so free time never
  reads the real clock. Each change undone alone fails its test
  (and only its own); all of round 2 undone fails all 14.
- Counts (Linux sandbox, Python 3.13): 2173 passed, 48 skipped (master
  f1eb136: 2124 passed, 48 skipped).
## Team chat connectors: Slack, Discord, GitHub (off by default)

Kyle asked for connectors where Claude "can look through something like a
team chat or an engineering chat" and summarize the ideas and files. This
is phase A (read and file a digest) plus phase B (registration, config,
i18n, docs).

- New `kwil/tools/teamchat.py`: `TeamChat` over one provider per
  service, with `chat_channels`, `chat_read` (a channel or one thread;
  the last 24 hours, `hours`, or a `day` from `timeutil.resolve_day` on
  the turn's clock), `chat_search`, `chat_files`, `chat_read_file` and
  `chat_save_digest`. Claude does the summarizing. Providers in
  `teamchat_slack.py` (Web API, user token: history, replies, search,
  files, user names), `teamchat_discord.py` (REST, bot token: guild
  channels, messages, threads, guild search, attachments) and
  `teamchat_github.py` (REST issues, pull requests and comments; GraphQL
  for Discussions). Plain `requests`, injectable `session=`, `timeout`,
  redirects off, one typed `ChatError` per failure, logging only status
  codes and exception types. No new dependencies.
- No posting: there is no tool that writes to Slack, Discord or GitHub.
  `chat_save_digest` files Claude's summary in Notion ("Chat Digests",
  found or created beside Assignments like Lecture Notes, or `[teamchat]
  digest_database_id`), always after a spoken yes, keyed
  `chat:<service>:<channel>:<date>` in a Digest ID property so a retry
  finds the page instead of filing a second. `ChatDigestsDB` subclasses
  `lecture.LectureNotesDB`; `lecture.py` got a small additive refactor
  (`key_prop`, `blocked_help` and `_schema()` on the class, same
  behaviour for lectures).
- Safety: every read tool is `untrusted_output` and `private`, and each
  result carries the `untrusted_content` mark and a note, so a chat read
  makes later changes ask. Bounds: 100 messages, 300 characters each,
  12,000 characters per result (oldest dropped first), 20 files, text
  files up to 50 KB from files.slack.com / cdn.discordapp.com /
  media.discordapp.net only (Slack token to files.slack.com only, none
  to Discord's CDN). `[teamchat] channels` and `github_repos` allowlists
  (a TOML string is one entry); Slack DMs dropped from search and files;
  a Discord thread must belong to the named channel. A 401 stops that
  service until restart; a 429 is waited out without calling. Discord's
  empty-content messages (Message Content Intent off) are reported as
  `intent_off`, not "no messages"; guild search's 202 "still indexing"
  says to retry.
- `cli.register_teamchat` (called from `build_tools`, so PC, Pi and the
  owner's web sessions; never family members) prints `(Slack tools off:
  SLACK_USER_TOKEN not set)` only for a service that is turned on.
  `config.py` `[teamchat]` defaults and `config.toml` mirror; the three
  tokens are in `SECRET_ENV_VARS` (round 2: not `ISOLATED_ENV_VARS`), and the log
  redaction masks Slack, GitHub and Discord token shapes. EN/ZH digest
  questions in `i18n.py`. `.env.example`, README "Team chat" (setup per
  service; one sentence on LinkedIn, which doesn't let apps read
  messages or feeds), SECURITY, MANUAL_TEST, BLOCKERS.
- Tests: new `tests/test_teamchat.py` (72, offline, fake `requests`
  sessions with the services' real response shapes: Slack `ok:false`
  errors and 429 Retry-After, `has_more`/`next_cursor`, Discord
  newest-first arrays, empty-content messages, 202 code 110000, 401/403,
  GitHub REST plus a mocked GraphQL POST): end-to-end taint through
  `ClaudeChat` (`test_chat_read_marks_conversation_untrusted`), untrusted
  marks on every read, a DEBUG caplog check with no message text, file
  text, names or tokens at any level, clamped bounds (including
  `max_messages=0` and `hours=0`), the TOML-string allowlist, file host,
  type, size, redirect and sign-in-page refusals, digest retry by key
  across a restart, a declined digest writing nothing, registration and
  family isolation, token redaction and isolation. `conftest.py` also
  clears the three token variables (GITHUB_TOKEN is often set in a dev
  shell or CI). `test_family.py` OWNER_TOOLS and `test_docs.py`
  OTHER_IDENTIFIERS list the new tool names.
- Each source change was reverted one at a time (with `__pycache__`
  cleared) and its tests failed: 32 reverts, all failing as expected.
  Family isolation needs no code (family registries are built from an
  allowlist), so that test pins the behaviour rather than a change.
- Counts (Linux sandbox, Python 3.13): 2196 passed, 48 skipped (master
  f1eb136 here: 2124 passed, 48 skipped).
- Review round 2 (8 judge findings, each with a test that fails when its
  fix is reverted, `__pycache__` cleared between reverts):
  1. The chat tokens are out of `ISOLATED_ENV_VARS` (still secret): a
     `GITHUB_TOKEN` in Kyle's user environment would silently stop the
     family apps. An instance with an `--env-file` other than `.env` turns
     team chat off instead. The existing refusal (a really isolated
     variable inherited) is now also written to that instance's log, as
     its task runs hidden under pythonw. README "A Chinese instance" lists
     `HA_URL`/`HA_TOKEN` too; `install_family_tasks.ps1`'s header is true
     again.
  2. Search hits and file lists arrive newest first; they are sorted
     oldest first before the 12,000-character cut, so the newest survive.
  3. An unknown service name is logged as "?", not as typed.
  4. The digest key uses the resolved service and channel id, so "#eng",
     "eng" and "C111" (service implied) file one page.
  5. GitHub issue search sends two queries, with `is:issue` and
     `is:pull-request` (GitHub's issue search now needs one), merged
     without duplicates. MANUAL_TEST has a GitHub search check.
  6. Slack `no_permission` is "no access" for that channel or file, no
     longer a dead token.
  7. A Discord search hit inside a thread counts for its parent channel
     (from the response's `threads`), allowlist included. A search cut to
     3 servers x 20 channels (Discord) or 5 repos (GitHub) says
     `"partial": true` with the reason. Plus a test for the GitHub
     no-repos guard.
  8. A streamed download answered with an error status is closed.
  Docs: Slack DMs are "never passed to Claude or logged" (search and
  files.list do return some to the process, which drops them).
- Counts after round 2 (Linux sandbox, `GITHUB_TOKEN` unset): 2213
  passed, 48 skipped; `tests/test_teamchat.py` has 89.
- Next (phase C, not built): a scheduled daily digest that files itself
  and pushes a notification to the phone, run on one machine only (the
  leader check).
## Fix: lecture summaries 400 on Sonnet 5.5 (2026-10-20)

- **Bug:** `lecture.summarize()` forced its `save_lecture_notes` tool with
  `tool_choice={"type": "tool"}`. Claude Sonnet 5.5 (the default
  `[assistant] model`, which the lecture job uses), Opus 5.5, Fable 5.1 and Mythos 5.1
  answer a forced `tool_choice` (`tool` or `any`) with a 400, so every
  lecture failed at the summary step with `BadRequestError`.
- **Fix:** the summary uses structured outputs instead
  (`output_config.format` with a JSON schema, `NOTES_SCHEMA`; every object
  sets `additionalProperties: false`), which the claude-api docs name as
  the replacement when a forced call only existed to get JSON back. No
  tools are sent. The reply's text block is parsed; a refusal, a
  `max_tokens` cut or text that isn't a JSON object raises a RuntimeError
  without quoting the reply, so the job fails as before with the
  transcript kept, and a retry re-runs only the summary. `max_tokens` is
  now 16000 (adaptive thinking, on by default on Sonnet 5.5, shares it)
  with a 600 s timeout for this one background request.
  `requirements.txt` now needs `anthropic>=0.77` (the first SDK whose
  `messages.create` takes `output_config`; 0.76 doesn't).
- **Other call sites:** no other `tool_choice` in `assistant/` (chat, tutor,
  history, memory and family all leave it at the default `auto`).
- **Tests:** the `fake_client` fixture (`tests/conftest.py`) now raises the
  API's 400 for a forced `tool_choice` on the models that reject it, and
  for an `output_config` schema with an object lacking
  `additionalProperties: false`. New in `test_lecture.py`: summarize on
  Sonnet 5.5 / Opus 5.5 / Fable 5.1, the whole pipeline on the default
  model, six bad replies (not JSON, not an object, no text, tool block
  only, `max_tokens`, refusal) failing without echoing the reply, a failed
  summary kept for a retry that doesn't re-transcribe, the schema check,
  and the fake's own 400. Lecture replies in `test_lecture.py`,
  `test_webfeatures.py` and `test_features_deploy.py` now have the
  structured-output shape (thinking block, then JSON text). With only
  `lecture.py` reverted, the model tests and the default-model pipeline
  test fail with `BadRequestError: tool_choice: type "tool" and "any" are
  not supported for this model.`
- Counts (Linux sandbox, Python 3.13): 2137 passed, 48 skipped (master
  f1eb136: 2124 passed, 48 skipped).
- Deploy: this update changes `requirements.txt` (anthropic>=0.77), so the
  auto-updater skips it. Stop the assistant and run `python -m kwil
  --update`, or `.\.venv\Scripts\python.exe -m pip install -r
  requirements.txt`, then restart.

## Cost: prompt caching, Haiku routing and usage tracking (2026-10-20)

Kyle: "build Haiku routing and caching right now."
- **Caching.** The tools and the system prompt are now the same bytes on
  every request of a chat (`ClaudeChat.stable_system()`), sent as one
  system text block with `cache_control` (it caches the tools too: they
  render first). What changed per turn moved out: the date and time,
  recalled notes and the weekly review offer are `turn_context()`, kept on
  the user message under `message["context"]` (not in its content) and
  sent by `request_messages()` as a text block after Kyle's words, the
  same bytes on every later request, so the conversation is append-only on
  the wire. A second, rolling breakpoint goes on a copy of the last block
  that can take one (walking back past server-tool blocks); `chat.history`
  never gets a marker. Two breakpoints of the four allowed. No `thinking`
  parameter is sent, as before. `build_system()` stays as
  system + context for the callers and tests that read it. Messages-cache
  misses, each once when it happens (accepted): trimming past
  `max_history` (round 2: trimmed to about half, so once per
  `max_history / 2` messages instead of every turn), a barge-in's
  `mark_interrupted`, an old attachment turned into a marker, a model
  switch (caches are per model), and (round 2) the request after a weekly
  review offer and after a `delete_note`.
- **Routing** (`kwil/router.py`): one model per user turn, picked in
  `ask()` before the request and kept for its whole tool loop. The main
  model when the router is off, there's no separate fast model, the turn
  or the history has an attachment, the conversation is tainted, it was
  already escalated (sticky until `reset()`), the words are long (over 15
  words or 100 characters; Chinese over 30 characters) or hit a keyword
  (changes, current information, reasoning, in English and Chinese).
  Otherwise `fast_model` (Claude Haiku 5.5). The route is logged at INFO as
  the model and a reason code only. `ToolRegistry.schemas(model)` gives a
  Haiku turn the web search/fetch versions Haiku supports
  (`web_search_20250305` / `web_fetch_20250910`). Conversation summaries
  use the fast model. Confirmations don't change: the same tools and
  questions on either model.
- **Usage** (`kwil/usage.py`): every Messages API call of the chat
  (and its summaries) logs its model and token counts (input, output,
  cache read, cache write, web searches) at INFO, no text. Monthly totals
  per model, with a rough $ estimate from the list prices (Sonnet 5.5
  $2/$10, Haiku 5.5 $0.10/$0.50 up to 100K-token prompts and 5x above;
  cache reads 0.1x, 5-minute writes 1.25x; web search fees counted, not
  priced), go to `[assistant] usage_file` (default `data/usage.json`, the
  server profile `/data/usage.json`, "" = in memory only), last 12 months,
  through `google_auth.write_private`. `/api/state` has `month_usd` for the
  owner only.
- **Settings:** `[assistant] fast_model = "claude-haiku-5-5"`,
  `router = "auto"`, `usage_file`; `CLAUDE_FAST_MODEL`, `CLAUDE_ROUTER`
  (`off` is the kill switch). Wired in `cli.build_chat`, the web app's and
  family's chat factories; `cli.main` configures the usage file.
- **BACKLOG.md** (new): "Before public release: Hybrid model"; this
  routing is its first step. Linked from README.
- Tests: `tests/test_routing_caching.py` (28): byte-identical system with a
  cache marker across two turns a minute apart (fails on master: the time
  was in it), append-only wire across a tool round and a second turn with
  exactly one rolling marker and none in history, copy-only marking that
  walks back past server blocks, notes in the turn and never in system,
  `transcript()` or the recorder, no "Kyle" in a zh/family context, 11
  router word cases (EN and ZH, keyword and length), attachments / taint /
  off / no fast model, stickiness until `reset()`, one model per tool loop,
  Haiku web tool versions, a route log with no words (caplog), summaries
  on the fast model, a usage log with numbers and no text (caplog), list
  prices and the long-prompt tier, the usage file round trip with the
  12-month cap, `usage_file = ""`, every call counted with its model, the
  config and env wiring. `tests/test_family.py` +1: only the owner's
  `/api/state` has `month_usd`, and family chats get the router settings.
  `conftest.py`: `make_response(usage=)`, `make_usage`, `system_text`,
  `request_text`, `first_text`, `own_blocks`, `without_context`, and an
  autouse fixture that gives each test its own meter and keeps a
  configured usage file in `tmp_path`. 30 existing tests changed shape
  only (system is a block list; a sent user turn is [words, context]; the
  history's user turns carry `context`); no assertion was dropped.
  `test_docs.py` knows `month_usd` and `cache_read` (a state field and a
  log field, not settings).
- Revert check: 27 single-change reverts (each source change undone alone,
  `__pycache__` cleared, its tests run, restored) all make their tests
  fail.
- Counts (Linux sandbox, Python 3.13): 2153 passed, 48 skipped (master
  f1eb136: 2124 passed, 48 skipped). Not measured against the real API
  (offline): the cache hit rate and Haiku's answers are MANUAL_TEST
  "Model routing and prompt caching".

### Round 2 (judge findings)

1. **Changes reached Haiku.** Two layers now. (a) More words send a turn
   to the main model: put, book, push, clear, set, lock/unlock, open,
   pause, resume, stop, skip, next, volume, dim, forget, remember, save,
   message, tell (not "tell me"), send, finish, done, change, update,
   "turn/switch ... on/off" with words between; Chinese 提醒 记住 打开 关
   播放 推 加 设置 删. (b) The safety net: a Haiku response that calls any
   tool not in `router.READ_ONLY_TOOLS` (calendar, assignment, email,
   notes, memory, history, dev-team, home and Spotify *reads*; unknown
   tools count as changes) is dropped before dispatch, never enters
   history, and the main model answers the turn (`escalated` set; INFO
   `Route: <model> (escalated_tool)`). Haiku words streamed before its
   tool call may already have been shown or spoken (README says so).
   Tests: the probe's 29 phrases (EN and ZH, plus 删掉明天的会) go to the
   main model, 5 simple questions still go to Haiku, a Haiku
   `create_calendar_event` never runs and the main model's does, the
   read-only list. `test_family_google` voice test: queue shape only (the
   dropped Haiku call, then the main model's).
2. **Two processes overwrote each other's usage.** Each save takes an
   O_EXCL lock file (as `updater.Updater.acquire`; stale after 10 s),
   re-reads the file and adds only this process's calls since its last
   save; a lock held past 2 s defers the save to the next call. Tests:
   two meters on one file add up to 4.0 then 6.0 (the probe got 2.0), a
   held lock defers without losing, a stale one is taken over.
3. **Mom's instance shared Kyle's usage file.** `config.mom.toml` now puts
   every data file under `data/mom/` (usage, memory review state,
   lectures, dev team state, Spotify token, family users). Test: every
   setting whose default is under `data/` points under
   `data/<name>/` in each `config.<name>.toml` and differs from Kyle's.
4. **The review offer was replayed** (and a test had been weakened). It is
   kept as `message["nudge"]`, rendered only while its turn is the latest
   (its tool loop included): one messages-cache miss on the next request,
   once a week. The memory-review tests check the full sent messages
   again (`conftest.sent_text`).
5. **A deleted note kept going to the API.** `PineconeMemory.retrieve` keeps
   `last_matches` (vector id, text); the chat remembers recalled notes per
   conversation (bounded, cleared by `reset()`), and a successful
   `delete_note` replaces that note's line in every turn's context with
   "(a note that was deleted)" (one cache miss). A declined delete keeps
   it. Notes otherwise stay in their own turn (append-only), checked by a
   test. Not covered (SECURITY.md): a delete from the phone's Memory
   screen during a live conversation, and note text inside a
   `recall_notes` tool result.
6. **Trimming rewrote the cache every turn.** Past `max_history` the
   history is cut to about half (falling back to the old cut if one tool
   loop fills the half), so the prefix holds for `max_history / 2`
   messages. The round-1 "one-time miss" wording and the
   `request_messages` docstring (the 5.5 models do keep earlier thinking;
   it is stripped for the signature 400s) are corrected; the stripping
   itself is unchanged.
7. **Docs and metering.** History titles, the tutor and lecture notes now
   call `usage.record` (lecture.py: only that line and its import);
   usage.py and README name what is metered. SECURITY.md no longer says
   notes come back "in later system prompts".
- Revert check: 20 round-2 reverts (each fix undone alone, `__pycache__`
  cleared) and the 27 round-1 reverts, re-run on this tree, all fail.
- Counts (Linux sandbox, Python 3.13): 2199 passed, 48 skipped (round 1:
  2153; master f1eb136: 2124 passed, 48 skipped).
- Merge with master (team chat connectors, structured-output lecture
  notes): `summarize()` now records usage before its refusal/max_tokens
  checks, so a cut-off summary's tokens are still counted (new test
  `test_a_cut_off_lecture_summary_is_still_metered`, fails with the record
  after the checks); the metering test uses the JSON-text reply; Mom's
  config keeps `[teamchat] digest_state_file` under `data/mom/`; the chat
  tools are not in `READ_ONLY_TOOLS`, so a Haiku turn that calls one goes
  to the main model. Counts (Linux sandbox): 2352 passed, 57 skipped, plus
  the sandbox's 9 audio failures and 26 audio errors (eSpeak, PortAudio,
  wake model); JS 232 pass.

## Release bug fixes: go-live gate, mid-turn escalation, Haiku in the checks (2026-10-21)

A judge's sweep of master (b96e6ed) returned NO-GO; every item is fixed,
each with an offline test that fails when only its source change is
reverted (`__pycache__` cleared). Master b96e6ed measured here first:
2435 passed, 58 skipped; JS 232 pass.

- **B1: the gate refused every commit on a machine without a web app.**
  An env file without `WEB_TOKEN` (a wake-only Pi, a family copy whose app
  never ran) made `candidate_check` fail ("no WEB_TOKEN", not outside), so
  the updater rejected the commit forever, the wake host's `refuse()` did
  `git reset --keep` on Kyle's pull and deploy said REFUSED. Now
  `watch.discover` leaves such an instance out (it has no web app to check
  or gate; INFO line with the env file name), and `gate_check` with no
  instance runs the updater's import and `--check` smoke itself
  (`watch.import_smoke`; a pull by hand never ran it) and passes only if
  that passes (a failure's reason is fixed words, "import failed" or
  "--check failed", the output tail goes to the log only; a smoke that
  crashes is inconclusive). Tests: `test_no_web_token_means_no_web_app_to_gate` (the
  gate isn't False, the smoke ran, a failing smoke refuses),
  `test_a_hand_pull_on_a_wake_only_pi_is_not_undone` (a wake Host with a
  real Updater: no `reset --keep`, restarted on the pull);
  `test_discover_finds_kyles_app_and_each_family_copy` gets tokens in its
  env files and a never-started copy that is left out.
- **B2: mid-turn escalation sent Haiku's signed thinking to Sonnet.** Only
  the escalating response was dropped; Haiku's earlier rounds of the same
  turn (thinking signed for Haiku, its read-only tool_use and results)
  stayed, and `request_messages` keeps current-turn thinking, so the real
  API would refuse Sonnet's request. Now `_run` rewinds the history to
  Kyle's message (its context kept; `_rewind_turn`, INFO "Rewound N
  message(s)") and reruns the whole turn on the main model with a fresh
  set of tool rounds. Only read-only tools could have run (every fast
  response is checked before dispatch); untrusted text read in the
  rewound rounds keeps the conversation tainted; the turn's tool events
  are cleared (the reads run again). Haiku's words already streamed stay
  the documented limit (README, router.py, SECURITY.md); the reply and
  history are the main model's only. Tests:
  `test_a_round_two_escalation_reruns_the_whole_turn_on_the_main_model`
  (the judge's probe: no Haiku signature, tool_use id or text in any of
  Sonnet's requests, Sonnet starts from one message, the delete runs once
  after its one confirmation, the streamed words),
  `test_a_rewound_turn_keeps_the_conversation_tainted`, and the contract
  test `test_a_mid_turn_escalation_sends_the_main_model_none_of_the_fast_models_turn`
  (fails on master with the fake's "Invalid `signature`" 400).
- **B3: neither the gate nor the watchdog ever reached Haiku.** The test
  conversation's turns were long or said "remember". `run_conversation`
  now starts with "What is two plus two?" ("quick turn", must answer);
  the check session routes as usual. `tests/test_contract.py`'s web
  conversation also runs routed (`contract-fake-routed`, and live
  `DEFAULT_MODEL` + `CONTRACT_MODEL` as the fast model in the
  `api-contract` job) and asserts the first request went to the fast
  model, so Haiku's tool versions, cache_control and max_tokens meet the
  real API. Test: `test_conversation_starts_with_a_turn_the_fast_model_answers`.
- **LOW 4: an uninstalled requirements bump.** `updater.requirements_problem`
  reads every `name>=x.y` in requirements.txt (markers evaluated with
  `packaging` when present, else that line is skipped; `==` and unpinned
  names left to pip) against `importlib.metadata`. `gate_check` returns
  None ("can't tell yet", never a rejection for good) and deploy refuses
  with "stop the apps, then run `python -m kwil --update` or
  `pip install -r requirements.txt`". Tests: the parser
  (`test_requirements_problem_reads_the_minimums`), the gate
  (`test_the_gate_waits_for_requirements_to_be_installed`), deploy
  --no-pull (`test_deploy_refuses_until_requirements_are_installed`).
  The two `gate_check(NEW, None, ...)` tests now pass `tmp_path` (the
  check reads the root's requirements.txt; the sandbox's own matches).
- **LOW 5: `-Restart` restarted apps mid-turn.** Deploy waits up to
  `deploy.IDLE_WAIT` (120 s) for `/healthz` busy=false, else skips the app
  and names it. README and `install_family_tasks.ps1` say `-Restart`
  covers every app here, Kyle's own included. Test:
  `test_deploy_restart_waits_for_a_turn_to_finish`.
- **LOW 6: the contract fake was looser than the API.** It now refuses a
  `system` that isn't a string or text blocks, extra message keys
  (`context`, `nudge`), cache_control on thinking/server blocks or empty
  text, more than 4 breakpoints, and thinking signed by another model.
  Tests: `test_the_fake_refuses_what_the_api_refuses` (7 cases),
  `test_the_fake_binds_thinking_signatures_to_the_model`.
- **LOW 7:** `config.mom.toml` no longer sets `[watch] status_file` (only
  config.toml's is read). The data-path test exempts it as machine-wide;
  `test_other_instances_set_no_machine_wide_watch_setting` keeps it out.
- **LOW 8:** `router.READ_ONLY_TOOLS` adds `find_free_time` and
  `list_calendars`.
- Revert check: 13 single-change reverts (B1 discover skip, B1 smoke on
  no instance, B2 rewind, B2 taint, B3 quick turn, LOW 4 gate, LOW 4
  deploy, LOW 5, LOW 6 per-model signature, LOW 6 system/cache checks,
  LOW 6 extra keys, LOW 7, LOW 8) each make their tests fail.
- Counts (Linux sandbox, Python 3.13): 2455 passed, 59 skipped; master b96e6ed: 2435
  passed, 58 skipped. JS 232 pass. The extra skip is the live routed
  contract run (no key here).

### Round 3 (judge findings on the B1 fix)

- **N1: with no web app, a `--check` failure refused the commit for good.**
  `--check` also fails for the machine's reasons (an unplugged mic, no
  audio device, no API key). The no-web-app branch of `gate_check` now
  refuses only when the import step fails (`updater.SMOKE_IMPORT_FAILED`,
  the prefix `Updater.smoke()` already gives each step); a `--check`
  failure is None: inconclusive, retried with the hosts' backoff, alerted
  after 3 tries. The automatic updater's own smoke (`Updater._update`) is
  a separate path and is unchanged: it still rolls back and rejects on a
  `--check` failure, as on master. Tests:
  `test_a_check_failure_without_a_web_app_is_inconclusive` (also through
  the real `Updater.smoke()` words for both steps) and
  `test_a_hand_pull_on_a_pi_with_no_mic_is_not_undone` (no reset, not
  rejected, one inconclusive try noted).
- **N2: an app with WEB_TOKEN only in the environment wasn't watched.**
  `discover(untokened=...)` hands back the instances it skipped for a
  missing token; the watchdog asks each one's `/healthz`, and one that
  answers gets a WARNING line and a status entry "unchecked: running, but
  WEB_TOKEN isn't in its env file, so it can't be checked" (in `--status`
  and the owner's banner), alerted once when it starts, dropped when it no
  longer answers. Test:
  `test_an_app_with_its_token_only_in_the_environment_is_reported` (the
  fake HTTP world: status, one alert over two runs, banner, `--status`
  line, gone once down).
- **N3 (docs):** README "Health checks and safe updates" says a `>=` line
  without a marker that a machine can't install makes its gate "can't
  tell yet" (the alert names the package), and to give such a line an
  environment marker.
- Revert check: N1 and N2 reverts fail their tests; the 13 round-2 reverts
  re-run on this tree still fail theirs.
- Counts (Linux sandbox, Python 3.13): 2458 passed, 59 skipped (round 2: 2455 passed, 59
  skipped; master b96e6ed: 2435 passed, 58 skipped). JS 232 pass.
## Public beta G5: public pages and waitlist (2026-10-21)

What it does:
- New `kwil/public_site.py` with static `web/landing.html`,
  `privacy.html`, `terms.html`, `waitlist.html` and `site.css`. The pages
  are `/welcome`, `/privacy` and `/terms`, public in CloudGuard
  (`cloud.PUBLIC_GET`). They have no JavaScript, no inline CSS, no CDN
  and no web fonts, and the app's CSP is unchanged. They are semantic
  HTML, fit a phone's width, and follow light/dark through
  `prefers-color-scheme`.
- `/` stays the app. Signed out, it is still the sign-in page, now with a
  "What is Hey Claude?" link to `/welcome` (translated on the Chinese
  page). Invited people and installed home-screen icons keep working
  unchanged, and a marketing page never sits between the owner and the
  app. Because index.html, app.css and i18n.js changed, the sw.js cache is
  now v22.
- `POST /api/waitlist` takes a form (303 back to
  `/welcome?waitlist=<state>`, so it works with no JS) or JSON. CloudGuard
  gives it its own body pool (`WAITLIST_POST`, `waitlist_slots`) with the
  sign-in body deadline. The app then applies:
  - 5 posts per address an hour (`cloud.RateLimiter` gained a `window`);
  - a honeypot field;
  - strict, lower-cased and de-duplicated addresses;
  - a note of at most 300 characters;
  - at most 2000 entries.

  Entries go to `[webapp] waitlist_file` via `google_auth.write_private`:
  `/data/waitlist.json` in the server profile, `data/waitlist.json`
  locally, "" on Mom's instance (closed). A file that can't be read is
  never overwritten. Logs hold counts only.
- Owner only (`require_owner`: 401 when signed out, 404 for a family
  member): `GET /api/waitlist`, the `GET /waitlist` HTML list with Remove
  buttons, and `DELETE /api/waitlist/<id>` / `POST .../<id>/remove`. The
  brief named `DELETE /api/waitlist/<email>`; this deletes by a random
  entry id instead, so an address never appears in a URL or the uvicorn
  access log.
- New settings: `[webapp] support_email` (fills `{{SUPPORT_EMAIL}}` on
  privacy/terms; "" = "the person who runs this server") and
  `legal_draft = true` (a visible "Draft: Kyle to review" note), plus
  `waitlist_file`. All three have commented lines in config.toml.
- The privacy page follows PRIVACY.md and the real beta data flows:
  - who gets what: Anthropic under the user's own key, Calendar-only
    Google, Notion once connected, Anthropic-run web search with the time
    zone, Render as host;
  - speech is transcribed on the server and not kept;
  - what is stored, including the key ("stored encrypted on the server
    and used only for your requests", for G2);
  - IP addresses in the logs;
  - deletion on request, and Render's daily snapshots keeping a copy until
    they expire.

  The terms are short: beta, as is, your own API costs, acceptable use,
  and that the beta can end at any time.

Tested: `tests/test_public_site.py`, 36 tests, offline, behind a real
CloudGuard:
- the pages are public and carry the CSP, and every other route still
  answers 401;
- an HTML parse finds no inline script or style, no event handlers and no
  outside assets;
- normalizing and de-duplication, and a private file with no temp
  leftovers;
- the note cap and bad addresses;
- the honeypot (same answer as a person, nothing stored);
- the per-address hourly limit and the 2000 cap;
- a corrupt file is never overwritten, and the waitlist is closed without
  a file;
- caplog: no address or note at any level;
- owner list/delete with escaped notes, and a family member gets 404;
- separate pools for sign-in and waitlist posts, and the body cap;
- support email and draft note on/off, and a malformed support email is
  dropped;
- `build_app` serves it, and the config/config.toml mirror.

Revert check: 14 source reverts, each undone alone with `__pycache__`
cleared, and each fails its test. The reverts were the PUBLIC_GET
entries, the WAITLIST_POST public flag, the waitlist pool, the
RateLimiter window, the honeypot, de-duplication, the rate limit, the
cap, lower-casing, the note cap, the support email, `require_owner`, the
login link, and a debug log of the address.

Counts (Linux sandbox): 2471 passed, 58 skipped (master b96e6ed baseline
2435 passed, 58 skipped); JS 232 pass.

Known gaps: no CAPTCHA and no confirmation email, both on purpose
(SECURITY.md). The page wording is a draft for Kyle.

### Round 2 (judge: CHANGES_NEEDED, 5 findings)
1. **The waitlist limit counted the proxy, not the visitor.** It used the
   web app's `client_ip` (the rightmost X-Forwarded-For hop, no
   `client_ip_header`). CloudGuard now puts the client key it already
   worked out into `scope["state"]["client_key"]`, and the waitlist uses
   that, falling back to the web app's address off the cloud. Tests: two
   private hops, `client_ip_header = "cf-connecting-ip"`, and off the cloud.
2. **A full list told who was on it.** At the cap, every post now gets
   "full", whether or not the address is already listed. Test: a listed
   and a new address get the same answer at 2000.
3. **The pages promise pieces that aren't on master yet** (G1 Calendar
   only, G2 BYOK, G6 Notion), and master auto-deploys. New
   `[webapp] public_site = false` (default, and in the server profile).
   While it is off, the four pages and every waitlist route answer 404,
   and the sign-in page drops its /welcome link. A comment at the top of
   landing.html and privacy.html lists each dependent claim for the
   release check. Tests: site off (404 and no link, en and zh, and through
   `cloud.build_app`'s server profile); site on works as before.
4. **Untested protections.** New tests: a slow waitlist body gets 408
   after the short sign-in deadline (ASGI-level, about 0.2 s); a
   cross-site `POST /api/waitlist`, `.../remove` and `DELETE` each get 403.
5. **`waitlist_file` defaulted to `data/waitlist.json`,** which opened a
   public write endpoint on a PC. The default is now "" (closed); only
   the server profile sets `/data/waitlist.json`. Mom's `""` line is gone,
   since the default covers it.

Revert check: each fix undone alone (`__pycache__` cleared), and each
fails its test. That covers the scope key in cloud.py, its use in
public_site.py, the cap/duplicate order, `from_config`'s switch, the
`public_site` default, the login-link removal, the `or waitlist` deadline,
and the `waitlist_file` default. The cross-site tests fail with the web
app's same-site check removed.

Known issue, not fixed here: the web app's own sign-in `LoginLimiter`
(`/api/login`, `/api/invite/accept`) has the same split. It counts the
web app's `client_ip` (rightmost hop), while CloudGuard counts its own
client key. Behind two private hops or Cloudflare, the app-side lockout
groups visitors by proxy. CloudGuard's own wrong-token checks are keyed
correctly, and the right token is never locked out. The fix is the same
scope key, in webapp.py.

Counts (Linux sandbox): 2480 passed, 58 skipped (round 1: 2471; master
b96e6ed: 2435 passed, 58 skipped); JS 232 pass.
## Public beta G1: account tiers and Calendar-only Google (2026-10-21)

Kyle: get the app ready for an invite-only public beta (bring-your-own
key, Calendar-only Google, no funding). This is piece G1 of that plan.
- **Tiers.** Each person in `users.json` has `tier`: `"family"` or
  `"beta"`. People listed before tiers load as family, without a rewrite
  (the field is written on the next save). Any other stored value counts
  as beta, the narrower tier. `POST /api/invites` takes `tier`; a new
  invite is **beta** unless the owner picks family, and a bad value gets a
  400. `public()` (the owner's list) shows the tier. The invite form has an
  **Account** select (Beta preselected), and the list marks beta people
  "· Beta", in English and Chinese. The INFO log line gives the id and the
  tier, never the name.
- **Beta testers never see or get Gmail.**
  - `_google_tools` adds Gmail only for tier family, even with `[gmail]
    enabled` and a token that has the scopes.
  - The consent URL's scopes come from `GoogleConnect.gmail(uid)` → `Family.gmail_allowed(uid)`: for beta,
    `calendar.events` plus the read-only calendar extras. The same scopes
    are used for the code exchange.
  - `/api/google/status` now also returns `gmail` (false for beta) and
    `note`. With `gmail` false, i18n.js uses a Calendar-only guide step
    and examples, and a disconnect question with no "email".
- **A separate OAuth client.** Beta testers sign in only through
  `GOOGLE_PUBLIC_CLIENT_JSON` / `[family] public_google_client` (its own
  Google Cloud project). Without it they get no Connect button, the note
  "Google isn't set up for beta accounts yet." (en/zh, `i18n
  google_beta_off`), and a 503 with the same text from
  `/api/google/connect`. They never fall back to the family client. The
  page keeps `data-google` for them, so the note can show.
  - `web_client_config` takes an `env` name.
  - Both Web client JSON variables are now in `SECRET_ENV_VARS` (masked;
    `watch.py` keeps them from child processes).
  - render.yaml adds `GOOGLE_PUBLIC_CLIENT_JSON` (sync: false), and
    conftest clears it.
- **Gap (c).** `[family] max_history_mb` (20) replaces the global
  `[history] max_mb` (500) for every person's HistoryStore. It is mirrored
  in config.toml.
- `sw.js` cache is now v22.
- **Tests.**
  - New, in test_family.py: invite defaults to beta / family on request /
    bad tier 400; pre-tier users load as family and odd values as beta;
    `max_history_mb` applied (20, then 7); tier logged without the name.
  - New, in test_family_google.py:
    - a beta consent URL has only calendar scopes and the public
      client_id (start and exchange), while family keeps Gmail on the
      family client;
    - a beta registry has no Gmail tools with Gmail enabled and Gmail
      scopes on the token, and no "Kyle" in schemas or the request;
    - no public client means no Google, the note and a 503, even with
      the family client set in both env and file; it then works from
      `[family] public_google_client`.
  - New JS: family.test.mjs (tier sent, beta by default; "· Beta" in the
    list) and i18n.test.mjs (no mail wording for beta, en and zh; the
    note; the zh labels).
  - Existing family Google tests now invite with `tier="family"` (the
    default changed), and four status-dict checks gained `gmail`/`note`.
  - Revert check: 8 Python and 3 JS source reverts, each made alone with
    `__pycache__` cleared, all fail their tests.
  - The "no Kyle" check already held before this change; it guards beta
    schemas and is not a fix.
- **Counts** (Linux sandbox, Python 3.13): 2442 passed, 58 skipped
  (master b96e6ed: 2435 passed, 58 skipped); JS 236 pass (was 232).

## Public beta G4: account self-service (2026-10-21)
Kyle: let invited people manage their own account. New module
`kwil/account.py`; small additive hooks in `family.py`
(`app.state.family_sign_out`) and `webapp.py` (`account.register`, and a
shared `clear_session_cookie` that sign-out now uses too).
- **`POST /api/me/delete`** (a family member's own cookie session; the
  owner gets 403, signed out 401, `[family]` off 404, cross-site 403 from
  the existing guard). Needs `{"confirm": "DELETE"}` or their name (any
  case). First a best-effort Google revoke (`Account.revoke`, default
  `revoke_google`: POST to `oauth2.googleapis.com/revoke` with the
  refresh token in the body); a failure is logged with the error type and
  the delete goes on. Then `Family.remove(uid, sign_out)`, the owner's
  Remove path: every session and voice call of theirs signed out, the whole
  `<dir>/<id>/` deleted (history, Google token, and G2's
  `anthropic_key.enc` / `notion.enc`), with the existing retry. The
  response deletes the cookie and sends `Clear-Site-Data: "cookies"`.
- **`GET /api/me/export`**: a zip with `profile.json` (id, name,
  language, time zone, tier, created) and `conversations/<id>.json`
  (`history.export(conv, "json")`, archived included). Nothing is copied
  from their folder, so no token or key file can get in. Bounded by
  `[family] export_max_mb` (new, 50; content before compression, 413),
  one at a time per person (409), 3 per hour per person (429). Built in a
  `mkdtemp` folder, streamed, removed when sent; one left by a dropped
  download is swept by the next export after 10 minutes.
- **UI** (`family.js` `setupAccount`, markup in `index.html`): a family
  member's ⚙ has **Download my data** (fetched and saved as a file, so an
  error never replaces the page) and **Delete my account** (typed
  confirmation, a confirm() saying it can't be undone, then back to the
  sign-in page). English and Chinese (`i18n.js`). `sw.js` cache v24 (v23
  is taken by PR #35).
- **Tests.** `tests/test_account.py` (28): whole folder gone with planted
  key, Notion and Google files and history; every session signed out;
  revoke called with the refresh token before the folder goes; delete
  succeeds when revoke raises (no token in the log); revoke posts the
  token in the body; confirmation required (6 bad values) and the name
  works; owner (cookie and Bearer) refused; another member's id in body or
  query ignored; cross-site and Origin-mismatch 403; export holds
  conversations and profile, no tokens or keys, never another member's or
  the owner's text; cap (413) with nothing left behind; temp folder
  removed; dropped downloads swept; one at a time; rate limit per person;
  rate map bounded; caplog shows ids only. JS: family.test.mjs +5 (shown
  only to family members, download saved as a file, refusals explained,
  delete needs the word and a yes then signs out, no/failed cases) and
  i18n.test.mjs +1 (every account string in both languages).
  `test_webapp.py`'s sw.js check now expects v24.
  - Revert check: 18 source reverts (account.py pieces and the route
    hook), each alone with `__pycache__` cleared, all fail their tests;
    family.js and i18n.js reverted alone fail the 6 and 1 JS tests.
- **Counts** (Linux sandbox, Python 3.13): 2495 passed, 33 skipped,
  2528 collected (master 5d18d7e: 2442 passed, 58 skipped, 2500
  collected; +28 new tests; the other 25 were skips that passed in this
  run because the sandbox's model and network checks differed between
  runs, not because of this change). JS 242 pass (was 236).
### Round 2 (judge findings, after merging master e45ee95)
- **No export outlives the account.** Exports now live in
  `<family dir>/.exports/export-<id>.<random>` (made 0700), not the shared
  `<system temp>/hey-claude-exports`. `.exports` can never be an id
  (`u_...`), and the "." separator keeps one id's folders apart from
  another's. Account tracks each person's export folder; **Delete my
  account** removes all their `export-<id>.*` folders and frees their
  slot. Cleanup no longer depends on the generator starting: the export is
  an `ExportResponse` (a `StreamingResponse` whose `__call__` cleans up in
  `finally`), because with ASGI 2.4 Starlette raises ClientDisconnect
  before the body and runs neither the generator's `finally` nor a
  BackgroundTask. Folders older than 10 minutes are swept at startup (when
  `register` makes the Account) as well as on every export. No backup
  download exists on master, and `Family.remove`/`_load` only touch valid
  ids, so neither sees `.exports` (tested).
- **Archived conversations** are in the export test now (one of the two
  planted ones is archived).
- **/privacy and /terms** say people can download their data and delete
  their account themselves in Settings; the support-email route stays.
  Still `legal_draft` until Kyle approves. (Not in the sw.js shell, so no
  cache bump.)
- Docs: SECURITY's rate-limit wording (only an export that starts counts,
  one ending in 413 too; 409/429 refusals don't); the table's
  `family.test.mjs` count is 15.
- **Tests.** `test_account.py` +5 (33): an export built and never
  streamed is gone after the delete (and an earlier run's, but not another
  person's); a raw ASGI 2.4 request whose `send` raises OSError at the
  response start leaves no folder and no busy slot; the startup sweep
  removes an old folder and keeps a fresh one; exports sit in
  `<family dir>/.exports` at 0700 and survive another person's removal and
  a reload of the list; the pages' self-service sentences (and that
  family.js calls both routes).
  - Revert check (each alone, `__pycache__` cleared): dropping the delete's
    folder removal, using a plain StreamingResponse, dropping the startup
    sweep, `archived=False`, the old temp root, and the old privacy.html
    or terms.html each fail their test.
- **Counts** (Linux sandbox, Python 3.13): 2569 passed, 34 skipped (the
  merge commit abf3b0f: 2564 passed, 34 skipped; +5). JS 242 pass.
### Round 3 (judge's two low findings)
- **An old export can't free a newer one's slot.** The slot is now
  `_busy[uid] = (started, token, folder)`: `_start` makes a fresh token,
  `build` binds the folder to it once `mkdtemp` returns (only if the slot
  is still that token's), a build error frees by token, and `finish` frees
  only when the slot's folder is its own. Before, the slot was set first
  but the folder map still named the old folder (and an unset one counted
  as a match), so a stale download ending during the next export's build
  freed that export's slot and let a third in. The separate `_folders`
  map is gone.
- **A mid-body disconnect closes the zip before cleanup.**
  `ExportResponse.__call__`'s `finally` now closes the body first: it calls
  `aclose()` on Starlette's iterator, then `close()` on our own generator.
  Both are needed, because `aclose()` only ends Starlette's thread-pool
  wrapper and leaves a sync generator suspended with the file open.
  Closing the generator runs its `with` and `finally`, so the zip is closed
  before the folder is removed (Windows can't remove an open file), and
  the folder is gone when the app returns.
- **Tests.** `test_account.py` +2 (35): export 1's download goes stale,
  export 2 starts and export 1 ends inside export 2's build (before its
  folder exists) and again afterwards; export 2 keeps the slot (a third
  gets 409) until its own download ends. A raw ASGI 2.4 request whose
  `send` raises OSError on the first body chunk (`more_body` true): at the
  first `finish` the zip's file handle is already closed, and the exports
  folder is empty inside the event loop with gc off.
  - Revert check (each alone, `__pycache__` cleared): HEAD's account.py
    fails both. `_done` as an unconditional pop fails the overlap test.
    Dropping `close_body()` fails the disconnect test, and so does
    `aclose()` alone without closing the generator (the file is still open).
- **Counts** (Linux sandbox, Python 3.13): 2571 passed, 34 skipped (round
  2: 2569 passed, 34 skipped; +2). JS 242 pass.
- **Round 3 review** (APPROVED, 1 LOW test gap): two more tests pin the
  token guards: a delete during a build leaves no slot behind, and a build
  failing before its folder exists never frees a newer export's slot
  (each fails with its guard removed). `test_account.py` 37.
## Dev team dashboard: new artifact URL (2026-10-21)
- The old dashboard artifact stopped delivering chat wakes to the
  coordinator; a fresh copy of the same page wakes it reliably. Every
  reference now points at https://claude.ai/artifact/BWYCUWxJxBvG6MJvnsaqvn:
  the page's `LAUNCHER_KINDS`, both desktop launchers (CRLF and LF/exec bit
  kept), the dashboard README and watchdog prompt, MANUAL_TEST, the
  Dev team dashboard section above, TEAM_MEMORY (plus a dated lesson) and
  the test that pins the URL. The page itself lives on in the
  agent-dashboard repo, which also gets a static `moved.html` for the old
  artifact.
- **Tests.** `test_agent_dashboard.py` pins the new URL; with the old
  launchers and page it fails 4 tests (launcher URL and the page/desktop
  byte-for-byte checks). test_docs + test_agent_dashboard: 75 passed,
  1 skipped (Linux sandbox, Python 3.13).

## Public beta G7: ops (error tracking, backup download, uptime) (2026-10-21)

- **Error tracking** (`kwil/errortracking.py`): Sentry, optional. On
  only with `SENTRY_DSN` set (`render.yaml` offers it); `sentry-sdk` is in
  requirements.txt since round 2 (round 1 had it optional behind a Docker
  build argument). Without the package: one warning, the app runs. Started from `cli.main` (every mode,
  the cloud's included) and `webapp.serve`, once. Options:
  `send_default_pii=False`, `include_local_variables=False`,
  `max_breadcrumbs=0`, no tracing (round 2, below), no source context, no
  request bodies. `before_send` rebuilds each event from an allowlist
  (exception types and frames' file/function/line/module, a log record's
  format string, the request's method and path, the runtime/os contexts),
  runs every string through `config.redact` and masks email-shaped text;
  an event it can't clean is dropped. `SENTRY_DSN` joins
  `SECRET_ENV_VARS` and `ISOLATED_ENV_VARS`.
- **Backup download** (`kwil/backup.py`, `GET /api/backup`, owner
  only via `owner_of`): a zip of `history.sqlite`, `users/users.json`
  (each entry `family.public`, round 2), each listed member's
  `users/<id>/history.sqlite`, `waitlist.json` if present and a
  `MANIFEST.json`. An allowlist: no tokens, `*.enc`, sessions, `.env`,
  logs or removed people. Databases are copied with
  `sqlite3.Connection.backup` (read-only source), switched to
  `journal_mode=DELETE` so each is one file. Built in a private temp
  folder, one at a time (409), refused above `[webapp] backup_max_mb`
  (200; checked before copying and on the zip as it grows), streamed with
  `FileResponse` and deleted by its background task; a crashed one's
  folder is swept (after an hour) by the next backup and, since round 2,
  at startup. INFO logs the size
  only. Wired in `build_assistant` (`assistant.backup`) and registered in
  `create_app`, so the PC and the cloud (`cloud.build_app` reuses
  `create_app`) both have it; `cloud.py` is untouched.
- **Uptime:** `[watch] cloud_url` (default `""`): the PC watchdog also
  checks the cloud copy's `/healthz` and alerts on a change like the other
  instances (banner, coordinator); it is never restarted, rolled back or
  gated from the PC. README "Uptime, error tracking and backups":
  UptimeRobot on `/healthz`, Sentry setup, and how Render's daily disk
  snapshots relate to `/api/backup`.
- Tests: `tests/test_errortracking.py` (8, a fake `sentry_sdk` in
  `sys.modules`; nothing is sent), `tests/test_backup.py` (10, real SQLite
  in tmp_path, plus one through CloudGuard), and
  `test_cloud_url_is_checked_and_alerted_on_but_never_healed` in
  `tests/test_watch.py`. Revert check: 28 source mutations (each undone
  alone, `__pycache__` cleared), every one fails its test.
- Counts (Linux sandbox, Python 3.13): 2454 passed, 58 skipped (master
  b96e6ed: 2435 passed, 58 skipped; +19 new tests). No JS changed.
- Measured (Linux sandbox, not Render): a 22.0 MB history (20,000
  messages of repetitive text) zips to 2.3 MB in 0.27 s; real
  conversations compress less.
- Deviation from the brief: a family member gets **404** from
  `/api/backup`, not 403, because `owner_of` answers 404 for every
  owner-only route (as if it didn't exist). The waitlist path:
  round 2 below.

### Round 2 (judge findings)

1. **The DSN's public key went out in trace headers.** Even
   `traces_sample_rate=0` counts as tracing enabled, and the default
   `trace_propagation_targets` is `.*`: every outgoing call during a
   request carried `sentry-trace` and `baggage` (the DSN's public key,
   the route, the release) to Anthropic, Google, Notion. Now
   `traces_sample_rate` is not passed, `trace_propagation_targets=[]`,
   `auto_enabling_integrations=False`, with FastAPI/Starlette added
   explicitly so route crashes are still reported;
   `max_request_body_size="never"` stays. Tests: the init options, and the
   real SDK in a child process (capturing transport, a local HTTP server
   standing in for the APIs): no `sentry-trace`/`baggage` on httpx (sync
   and async) or http.client calls, crashes still reported, no
   transactions, nothing personal in any envelope.
2. **A cloud outage failed a PC deploy.** The cloud's entry is marked
   `remote` (`Instance.remote`, written to the status), and `deploy.py`
   leaves remote entries out of its failing count (one condition). Test:
   `test_deploy_passes_while_the_cloud_is_down`.
3. **`logentry.message` isn't always a format string.** It is kept only
   for the app's own `kwil.*` loggers; for others (asyncio's "Task
   exception was never retrieved", libraries' `log.error(exc)`) only the
   logger name goes. Tests: the asyncio event shape, and the real SDK with
   an unretrieved task exception.
4. **`sentry-sdk` is always installed** (requirements.txt,
   `sentry-sdk>=2,<3`, pure Python); `INSTALL_SENTRY` is gone from the
   Dockerfile and render.yaml. Test: with the real SDK installed and no
   DSN, nothing starts, nothing is sent, no headers are added and the app
   answers as before.
5. **Family entries came from a denylist.** Each is now
   `family.public(u)` (it includes `tier` from #34). Test: an unknown
   field and an invite hash in users.json don't reach the zip.
6. **The waitlist path was hard-coded.** It is
   now `[webapp] waitlist_file` (from the public-site PR; the server profile
   sets `/data/waitlist.json`); `""`, the default, leaves the waitlist out.
   Tests: the setting's path, and `""`.
7. **A crash-left plaintext copy lingered in the temp folder.**
   `build_assistant` calls `backup.sweep()` once. Test: a stale
   `hey-claude-backup-*` folder is gone after `build_assistant`.
- Merged origin/master (#34, beta tiers): conflicts in `SECRET_ENV_VARS`
  (both additions kept) and here.
- Revert check: 13 round-2 mutations (each alone, `__pycache__`
  cleared), every one fails its test. Dropping only
  `auto_enabling_integrations=False` fails the options test but not the
  real-SDK one: with no tracing and no propagation targets it adds no
  header by itself (defense in depth). The 27 round-1 mutations still all fail (the 28th
  targeted the removed `traces_sample_rate=0`).

- **After round 2:** merged master #35 (public site; `[webapp]
  waitlist_file` now decides the backup's waitlist, "" leaves it out).
  Round-2 review (CHANGES: 1 MED, 4 LOW, all docs): PRIVACY.md and the
  public privacy page now describe the owner's backup and Sentry
  accurately (a Sentry row in both tables). Counts: 2539 passed, 33
  skipped; JS 236.
## Public beta G2: bring your own Anthropic key (2026-10-21)

Kyle has no funding, so public beta testers pay for their own Claude use.
Gap (a) of the beta brief: every family member's chat and history titles
ran on the owner's `anthropic.Anthropic()`.
- **`kwil/userkeys.py`.** Each person's key is kept in
  `<[family] dir>/<id>/anthropic_key.enc` (`write_private`) as a Fernet
  token (`cryptography>=44`, already a google-auth dependency; abi3 wheels
  for win_amd64, manylinux x86_64/aarch64, Python 3.9+). The Fernet key is
  HKDF-SHA256 of the env secret `USER_DATA_KEY` with the label
  `hey-claude/anthropic-api-key/v1` (a later Notion token gets its own
  label). The person's id is inside the ciphertext. Without `USER_DATA_KEY`
  (or under 32 characters) nothing is stored and the reason is given; a
  changed `USER_DATA_KEY` makes the file not open, which counts as "no key"
  (Settings asks to enter it again), never a crash. `check_key` makes one
  `models.list(limit=1)` call (timeout 10, one retry): 401/403/other 4xx ->
  400, 408/409/429/5xx and connection errors -> 503, SDK exceptions not
  chained.
- **Routes (`kwil/userkeys_web.py`, one additive line in
  `create_app`).** `GET/PUT/DELETE /api/me/anthropic-key`, each about the
  signed-in person only. PUT checks the shape (`sk-ant-`, at most 256
  characters), then `USER_DATA_KEY` (503 before any call), then the live
  check, then saves; at most 5 PUTs a minute per person
  (`cloud.RateLimiter`). GET is `{set, last4, unreadable, required,
  available, month_usd?}`. **The owner gets 404** (decision: the server's
  own `ANTHROPIC_API_KEY` is theirs; a second personal key would only blur
  whose bill a turn is on). `/api/state` gives a family session `api_key`:
  `"required"` (beta) or `"optional"` (family).
- **Per-person client (`family.py`).** `Family.client(uid)` /
  `api_for(uid)`: a cached `anthropic.Anthropic(api_key=..., max_retries,
  timeout)` per person (the owner client's options; an explicit key, so
  the SDK reads no `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` from the
  server's environment). Family tier without a key -> the owner's client
  (as before); beta -> `userkeys.MissingKey` before any request; someone
  no longer listed -> `MissingKey` (never the owner's key). Their
  ClaudeChat and Recorder get a `UserApi`, which looks the client up on
  every call. **Deviation from the brief:** a saved, replaced or removed key
  drops the cached client instead of `_contexts.pop`: popping the context
  would leave open conversations on the old key (and a second HistoryStore
  on the same file), while the per-call lookup makes the change apply at
  the very next call, in open conversations too.
- **Errors.** `cli.friendly_error` maps `MissingKey` to the new i18n
  `no_key` ("Add your Anthropic API key in Settings to start.") and a 401 on
  their own key (`UserApi.own_key`) to `bad_user_key` ("Your Anthropic API
  key was rejected. Check it in Settings."); the owner's 401 still says
  `bad_key`. en and zh. Same in the attachment path (`_ask_with_files`) and
  in `voice_session._run_turn`'s catch-all (it said only "error").
- **Cost.** ClaudeChat and Recorder/`ask_title` take a `meter`; calls on a
  person's own key go to their `usage.UsageMeter` at `<dir>/<id>/usage.json`
  (not written once they are removed), never the owner's `month_usd`.
  Family members without a key still count on the owner's. Their GET shows
  their own estimate.
- **Settings UI.** ⚙ -> "Your Anthropic API key" (`index.html`,
  `family.js setupKey`, styles in `app.css`): paste, Save / Replace /
  Remove (asks first), the last four, this month's estimate, and a short
  how-to with the console address as plain text (no external link or
  script). All strings in Chinese too (`i18n.js`). `sw.js` cache v23.
- **Config.** `USER_DATA_KEY` is in `SECRET_ENV_VARS` and
  `ISOLATED_ENV_VARS` (decision: a separate instance such as `.env.mom`
  must not inherit the owner's from the shell; it would open the keys
  under the owner's family folder; it sets its own), `render.yaml`
  (`generateValue: true`), conftest's cleared vars, and a comment in
  config.toml `[family]` (no config setting: env only).
- **Tests.** New `tests/test_userkeys.py` (29): encryption at rest (no
  plaintext, other folder, other label), fail closed (unset/empty/short),
  a rotated `USER_DATA_KEY` (no crash, "enter it again", re-entering works),
  key shapes, `check_key` against the real SDK over an httpx MockTransport
  (200, 401/403/404 -> 400, 429/500/529/connection -> 503), the person's
  client ignoring the server's credentials, two people -> two clients for
  chats and titles, beta without a key -> `no_key` with nothing sent and
  the turn rolled back, family without a key -> the owner's, replace and
  delete applying to an open conversation, a removed person never falling
  back, a rejected own key -> `bad_user_key` (owner still `bad_key`), the
  file path too, `month_usd` untouched by their calls, no usage file after
  removal, the routes (owner 404, signed-out 401, another person's key
  invisible, 400/503, rate limit 429), the voice path (en/zh), and the key
  never in caplog at DEBUG, users.json, responses or errors. A contract
  test (`test_contract.py`) runs a beta tester's multi-turn conversation
  with a tool round through `UserApi` (the fake here; live in CI with the
  secret). JS: family.test.mjs (+4: shown only to family sessions, PUT once
  and the box cleared, last four and estimate, a refused key's reason,
  remove asks first, "can't be read" and "can't store" states) and
  i18n.test.mjs (+1: every string of the section in Chinese). Existing
  tests that had a beta-by-default person talk on the owner's key now
  invite `tier="family"` (4 lines); the G1 beta Gmail test now saves a key
  for its tester first; the sw.js pin is v25 (after the master merge).
  - Revert check: 20 Python and 3 JS source reverts, each alone with
    `__pycache__` cleared, all fail their tests. (The empty-secret check
    is also covered by the length check; only its message test fails.)
- **Counts after merging master and the review fixes:** 2543 passed, 35
  skipped; JS 241. Review round 1 (APPROVED, 3 LOW): an SDK before 0.98
  sent the server's `ANTHROPIC_AUTH_TOKEN` next to a person's key, so
  `userkeys.user_client` clears it (test with an old-SDK stand-in); the
  public privacy page now mentions the usage estimate and Remove key.
- **Counts before the merge** (Linux sandbox, Python 3.13): 2497 passed, 35 skipped; G1
  366eab5 in the same sandbox at the same time: 2467 passed, 33 skipped
  (+29 test_userkeys.py, +1 contract fake; the 2 new skips are the
  contract test's live variants). Earlier the same day the G1 tree gave
  2442 passed, 58 skipped: 25 environment-dependent tests skipped then and
  run now, the same on both trees. JS 241 pass (was 236).
## Public beta G6: Notion sign-in for invited people (2026-10-21)
Built on `claude/account-self-service` (G4, PR #41), which revokes on
self-delete. Any invited person (beta or family) can connect their own
Notion workspace and gets exactly two tools on their own token.
- **`kwil/notion_web.py`** (new), mirroring `google_web.GoogleConnect`:
  `GET /api/notion/status` (`{enabled, connected, workspace, database}`),
  `POST /api/notion/connect` (`{url}` of
  `api.notion.com/v1/oauth/authorize?owner=user`), `POST
  /api/notion/disconnect`, `GET /notion/callback`. The same state + nonce
  cookie (`hc_notion`, Lax, path `/notion/callback`, 10 min) + session +
  still-listed checks, 4 pending per person and 32 in all; the owner gets
  404. The code is exchanged with notion-client's own `oauth.token`
  (Basic auth, JSON body); no PKCE (Notion documents none). Then one
  data-source search (page_size 100, one page): exactly one data source
  with the Assignments schema (`Assignment` title, `Status` status with the
  three options, `Due` date) is used, preferring the copy of the
  integration's template (`duplicated_template_id` = the database's parent
  page); none or several -> `database: null` and Settings says to share
  only that database and reconnect (no picker, by decision). A grant that
  can't be saved (removed meanwhile, write failed) is revoked at once.
  The SDK gets its own logger at ERROR (its default adds a console handler
  per client, and its warnings quote Notion's messages, which can echo a
  title); a 10 s timeout per request.
- **`kwil/userkeys.py`**: generic `seal(label, uid, payload)` /
  `unseal(...)` (Fernet/HKDF under a label, the uid inside the ciphertext);
  `KeyStore` now uses them (same behaviour and messages); `NOTION_LABEL =
  hey-claude/notion-token/v1`. `<dir>/<id>/notion.enc` holds `{uid,
  access_token, refresh_token, bot_id, workspace_id, workspace_name,
  database_id, data_source_id, database_title}`; never Notion's `owner`
  (their email). Without a usable `USER_DATA_KEY` connect answers 503 and
  the callback never exchanges the code.
- **`kwil/family.py`**: `notion_enabled/notion_status/save_notion/
  disconnect_notion/revoke_notion/refresh_notion` and `_notion_tools`
  (registered next to Google's; `_refresh_tools` swaps them in place).
  The tools are `NotionAssignments.tools()` re-bound: a client built on
  their token (never `token=`, whose falsy fallback is `NOTION_TOKEN`),
  `untrusted_output` and `private` on both, "Kyle's ... his" rewritten,
  and only `list_assignments` / `update_assignment_status` (the update
  keeps `confirm_if_untrusted`). A 401 refreshes once under a per-person
  lock (a second turn that waited uses the new token); a refresh token is
  replaced only if Notion sends one; a failed refresh says "reconnect
  Notion" (i18n `notion_reconnect`, en/zh). Notion's error text is replaced
  by its code before it reaches Claude or the tool-failure log.
- **Revoke** (`POST /v1/oauth/revoke`, token in the body) on Disconnect, on
  "Delete my account" (`Account.revoke_token`, before Google's) and on the
  owner's Remove (`remove_user`, before `Family.remove`). Best effort: a
  failure is logged by type and never blocks; `notion.enc` goes with the
  folder (`_finish_deletes` rmtree).
- **Wiring**: one line in `create_app` after `userkeys_web.register`;
  `/notion.js` in the static routes, `sw.js` SHELL (cache v27) and
  CloudGuard `PUBLIC_GET` with `/notion/callback`. Config: `[family] notion
  = true` (DEFAULTS and config.toml), `NOTION_OAUTH_CLIENT_ID` /
  `NOTION_OAUTH_CLIENT_SECRET` in `SECRET_ENV_VARS`, `ISOLATED_ENV_VARS`,
  render.yaml (`sync: false`) and conftest's cleared variables. No redirect
  setting: it is `own_origin + /notion/callback`.
- **UI**: `kwil/web/notion.js` (new): Connect / Reconnect / Disconnect
  Notion beside Google's, shown only for an invited person's session, the
  workspace and database names as text only, a confirm before disconnect,
  and only Notion's authorize URL is followed. en and zh strings in
  i18n.js. The public privacy page now describes the Notion sign-in (what
  is stored, revoke on disconnect/delete, the Connections page); its and
  landing.html's release-check comments say G6 is built and goes live
  with the Render variables.
- **Tests.** New `tests/test_notion_web.py` (42): every Notion call
  through the real notion-client over an httpx MockTransport with Notion's
  response shapes (the token answer with `owner.user.person.email`, the
  2025-09-03 data-source search), asserting Basic auth and the JSON bodies:
  the full connect (URL params, cookie flags, only `notion.enc` written, no
  plaintext, private mode, not openable under the Anthropic label or as
  someone else), tools only after connecting (in place, in an open
  conversation), a real turn's tool list without "Kyle", seven refused
  callbacks (unknown, reused, no cookie, other browser, signed out,
  removed, no code) with no exchange and no file, a denied consent in
  Chinese, a refused code, a grant that can't be saved being revoked, no
  `USER_DATA_KEY` (503 and no exchange), off without the integration or the
  switch, owner 404 / signed-out 401, pending bounds, five no-single-match
  schemas, the template preference, a failed search, untrusted + private
  flags, `NOTION_TOKEN` set and never sent, refresh once keeping the
  refresh token, a rotated one stored, a failed refresh -> "Reconnect
  Notion", two concurrent turns -> one refresh, Notion's error text kept
  out of Claude and the log, disconnect revoke + delete, a failed revoke
  still deleting, owner removal and self-delete revoking first (and
  deleting when it fails), `Family.remove` deleting `notion.enc`, a
  caplog at DEBUG with no token, code, nonce, secret, email, workspace,
  database or assignment title, config/deploy/page wiring, and
  seal/unseal. `test_cloud.py` +1 (a made-up state on the public callback
  -> 400 through CloudGuard, status still needs sign-in). JS: new
  `tests/js/notion.test.mjs` (9).
  - Revert check: 34 Python and 6 JS source reverts, each alone with
    `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1`, all fail their
    tests (the two best-effort revoke guards are reverted together: either
    one alone still catches the failure). Not
    revert-checked: passing `token=` instead of a client (it would make a
    real Notion call), and the owner 404 (structural: the owner's session
    has no id).
- **Counts** (Linux sandbox, Python 3.13): 2647 passed, 36 skipped (+42
  test_notion_web.py, +1 test_cloud.py); JS 256 pass (+9). Baseline on this
  branch: 2604 passed, 36 skipped; JS 247. Existing tests touched: the
  sw.js pin is v27, `/notion.js` joins test_webapp's public list, and
  test_docs knows the two tool names.
### G6 Round 2 (review fixes, 2026-10-21)
Merged `claude/account-self-service` (6f09947, with master 0005342: the
ops PR, design skills and #9, which removed the PC hub and moved the
dashboard); the only conflict was `ISOLATED_ENV_VARS` (both `SENTRY_DSN`
and the Notion OAuth variables kept). sw.js stays v27 (master: v26).
- **Reconnect revokes the old grant.** `Family.replace_notion` reads the
  stored record and saves the new one under the per-person Notion lock;
  `NotionConnect.finish` then revokes the old access token (quietly) if it
  differs from the new one.
- **A disconnect can't be undone by a refresh in flight.**
  `disconnect_notion` holds the per-person Notion lock around revoke +
  delete, `revoke_notion` (used by "Delete my account") takes it too, and
  the owner's Remove holds it around revoke + `Family.remove`; a refresh
  in flight finishes first and its new token is the one revoked. Belt and
  braces: `save_notion(expect=...)` saves a refreshed record only if the
  file still holds the token the refresh started from.
- **Rate limits are bounded.** `notion_web.api()` passes notion-client's
  `RetryOptions(max_retries=1, max_retry_delay_ms=2000)` (3.1's default
  honours `Retry-After` up to 60 s, twice: about 2.5 min of stall on a
  voice turn, disconnect or delete). requirements.txt: `notion-client>=3.1`
  (a 3.0 install, which has no retries, is left as it is).
- **Tests**: `tests/test_notion_web.py` +7 (49): reconnect A -> B revokes
  A only and B stays live; the same token again revokes nothing;
  disconnect and revoke while a refresh is blocked inside `refresh_grant`
  (file gone, status not connected, the refreshed token revoked); a
  refresh never saves over a changed or deleted sign-in; the owner's
  Remove runs `Family.remove` under the Notion lock; a 429 with
  `retry-after: 60` sleeps once, at most 2 s (`time.sleep` patched), for
  a search and a revoke.
  - Revert check (`__pycache__` cleared each time): the finish revoke,
    the disconnect/revoke lock, the `expect` check, the Remove lock and the
    retry options, each reverted alone, fail their tests.
- **Counts** (Linux sandbox, Python 3.13): 2616 passed, 35 skipped; JS
  118 pass. Baseline (the merge, before these fixes): 2609 passed, 35
  skipped; JS 118 (master's #9 removed the dashboard and hub tests).
### G6 Round 3 (two low findings, 2026-10-21)
- **"Delete my account" holds the Notion lock around revoke and removal.**
  `Account.delete` takes `Family._notion_lock(uid)` around
  `Family._revoke_notion` and `Family.remove`, as the owner's Remove does.
  Before, `revoke_notion` let the lock go before `remove`, so a refresh in
  that gap could save a new token that the folder then took away without
  revoking it. `Family.revoke_notion` had no other caller and is removed;
  the lock isn't reentrant, so callers hold it and call `_revoke_notion`.
  The Google revoke stays outside the lock.
- **A refreshed token that isn't saved is revoked.** If `refresh_notion`'s
  `save_notion` returns False (they were removed, or the sign-in changed)
  or raises (KeyProblem/OSError), the new access token is revoked quietly
  instead of being left live at Notion.
- **A reconnect to the same bot revokes nothing.** `replace_notion` returns
  no old token when `notion_web.same_bot(old, new)`: the same `bot_id`, or,
  if either record has none, the same `workspace_id`. Notion may reuse the
  bot for a workspace, and revoking its old token could end the new grant.
  A different bot (another workspace) still has its old token revoked.
  SECURITY.md records this as a deliberate non-fix; README and MANUAL_TEST
  (same workspace, then another workspace) updated.
- **Tests**: `tests/test_notion_web.py` +6 (55): "Delete my account" runs
  `Family.remove` under the Notion lock, and a refresh started in that gap
  waits, finds nothing and never calls Notion's token endpoint; a refresh
  whose save is refused (False, or OSError) revokes the new token, logs no
  token, and leaves the stored one; reconnecting the same bot (with and
  without a stored `bot_id`) revokes nothing and the new token stays live;
  `same_bot` cases. The reconnect A -> B test now uses another bot and
  workspace. The race test's "revoke" case now runs `Account.delete`.
  - Revert check (`__pycache__` cleared each time): `Account.delete`
    releasing the lock before `remove` fails the delete-lock test; dropping
    the refresh revoke fails both refused-save cases; dropping the
    `same_bot` check fails both same-bot cases.
- **Counts** (Linux sandbox, Python 3.13): 2622 passed, 35 skipped; JS 118
  pass. Baseline (round 2): 2616 passed, 35 skipped; JS 118.

## Public beta G3: per-person limits (2026-10-21)

Gap (d) of the beta brief: nothing stopped one invited account from running
turns or Whisper without end (CPU on the shared server; cost on the owner's
key for family members without their own).
- **`kwil/quotas.py`** (`UserQuotas`, shaped like
  `cloud.RateLimiter`): per person (user id, across all their browsers and
  the voice view) a sliding 60-second window of turns, turns per day and
  seconds of speech transcribed per day. New `[family]` keys
  `turns_per_minute` (6), `turns_per_day` (200) and `stt_seconds_per_day`
  (1800), in `DEFAULTS` and config.toml; 0 = no limit. A day is the
  person's own calendar day (their `timezone` from users.json), UTC when
  they have none or it doesn't load. **In memory only** (decision: a
  restart resets the counts; nothing personal is written). Bounded: people
  with nothing counted in the current minute or their day are pruned when
  room is needed, at most 1000 kept (oldest dropped); a removed person's
  counts are forgotten (`Family.remove`).
- **Where it is checked.** `Family.quotas` is the one instance; the owner
  (session user None) is never looked up.
  - `WebAssistant.start_turn` (typed, push-to-talk and attachment turns,
    all through `/api/turn`): `limit_message` counts the turn or refuses
    it before the thread starts; a refusal puts `{"type": "error",
    "message": <sentence>}` and "done" (the page already shows server
    sentences), so nothing is sent to Claude, the conversation and history
    are untouched and the stale-conversation end doesn't run. A recording
    also needs speech time left; typing still works when it is used up.
  - `VoiceConversation._turn_body` (hands-free, via a `limit` callable
    from `voice_ws.py`): checked before Whisper (nothing counted), then the
    turn is counted just before Claude is asked, so noise heard as nothing
    and "goodbye" don't use up turns. The refusal is sent as an error event
    and spoken (`Chunk(reply=False)`).
  - Speech seconds: `WebAssistant.transcribe(..., user=)` adds the
    recording's length (samples / 16 kHz, measured from the audio) after
    Whisper ran, also when it failed. Every path that transcribes for a
    person passes it: push-to-talk, the voice socket (turns and spoken
    yes/no), `/api/confirm`'s recorded answer, `transcribe_upload`. A spoken
    yes/no in the voice view is counted but never refused (it belongs to
    a running turn; refusing would only make it a "no"); a recorded answer
    to the page's question is checked for speech time first (round 2). A
    clip already started finishes, so the day's total can go past the
    limit by one recording per session in flight. The lecture recorder
    and tutor are owner-only (`require_owner`), so not limited.
- **Sentences** (i18n.py, en + zh): `limit_minute`, `limit_day` (with the
  day's number), `limit_speech` (minutes, "you can still type"). No JS
  change: the page and voice view show the server's sentence; sw.js not
  bumped.
- **Logs:** INFO `Limits: refused a turn for <id> (<limit>)` and
  `Voice turn <n>: refused (a limit)`; the speech total at DEBUG. No text.
- **Tests.** New `tests/test_quotas.py` (19): each limit refuses at the
  boundary and lets the next window through (minute, UTC day, the
  person's own Asia/Shanghai day, an unknown zone as UTC, speech for
  recordings only, 0 = off); bounded memory and pruning; defaults and
  config; en/zh sentences; the web path (refused with no Anthropic call,
  chat history and saved messages unchanged, the lock released, allowed
  after the minute), the zh refusal, the owner never limited (web and
  voice, nothing even tracked), recordings refused before Whisper while
  typing still works, every transcription counted (failed Whisper,
  `transcribe_upload`, `/api/confirm`), removal forgets, caplog (id and
  limit only; no utterance, name or sentence), and the voice socket (minute
  limit before Whisper, empty speech not counted, zh speech limit before
  Whisper with no call to Claude).
  - Revert check: 13 single source reverts, each alone with `__pycache__`
    cleared (the Family attribute, `forget`, the start_turn refusal, the
    speech count in `transcribe`, `/api/confirm`'s user, both voice checks,
    the voice socket's `limit` and `user`, the zh sentence, a config
    default, the owner guard, pruning): every one fails at least one test.
- **Counts** (Linux sandbox, Python 3.13): 2586 passed, 36 skipped; master
  7d55f8e in the same sandbox at the same time: 2567 passed, 36 skipped
  (+19). JS 241 pass (unchanged).
- **Round 2** (review fixes, after merging master 770e642):
  - `/api/confirm` with a recording: checked for speech time first
    (`WebAssistant.speech_refusal`, speech only: a spoken answer to a turn
    already counted isn't a new turn). Out of time: 429 with the
    `limit_speech` sentence in their language, Whisper doesn't run and the
    question stays open (the page shows the sentence and the buttons again;
    a tap or a typed yes still answers it; a refusal is never a "no").
    Then the question is claimed (`PendingConfirm.claim`, under its own
    lock, like the voice view's `pending.answering`): a second recording
    for the same question gets 409 without running Whisper, and a failed
    decode or transcription releases the claim so they can try again.
  - A turn in flight when someone is removed can't bring their counters
    back: `UserQuotas(is_listed=...)` (Family: `uid in self.users`, so
    people being deleted are out too), checked under the quota lock by
    `check`, `admit` and `add_speech`. `Family.remove` swaps the list before
    `forget`, so either order of the race ends with no entry.
  - Docs: the overshoot is "one recording per session in flight"
    (quotas.py, SECURITY, above); PRIVACY says the times of the last
    minute's messages are kept too. `tzdata` was already in
    requirements.txt (unconditional), so zoneinfo works on Windows.
  - Tests (+6 in `tests/test_quotas.py`): five concurrent recordings with
    speech used up (all 429, Whisper never ran, question still pending and
    unclaimed, a tap then answers it; caplog: INFO `Limits: refused a
    spoken answer for <id> (limit_speech)`, no name or question); one slow transcription holding the
    claim while four more get 409 (Whisper ran once, 1 s counted); a failed
    transcription and an unreadable upload both release the claim; a
    spoken yes at the turns-per-minute limit still answers; admit, remove,
    add_speech leaves no entry; unlisted ids never counted. Revert check,
    each alone with `__pycache__` cleared: the whole webapp change (3
    fail), the claim (1), the releases (1), the speech check (1),
    `turns=False` (1), the Family `is_listed` (1), the quota guards (2),
    the `add_speech` guard alone (2), the new INFO log line (1).
  - Counts (Linux sandbox): 2619 passed, 36 skipped; the merge commit
    142a0b2 in the same sandbox: 2613 passed, 36 skipped (+6). JS 241 pass.

## Public beta launch prep (2026-10-21)

From the public beta go/no-go review. Prepares the launch; does not turn
it on (`public_site`, `legal_draft` and `support_email` are unchanged:
Kyle approves the wording and picks the address first, in a separate PR).

- **Integrated count on master** 4b770b5 (G1-G7 all merged), Linux
  sandbox, Python 3.13: 2649 passed, 35 skipped (measured on a clean
  export of 4b770b5); JS 118 pass.
- **Paid voices never reach beta testers.** `voice_picker.Catalog.paid_ok`
  allowed any invited person ElevenLabs voices once `[tts]
  family_paid_voices` was on, whatever their tier, so a beta tester could
  run up Kyle's ElevenLabs bill. It now also asks the Family
  (`Family.tier(uid) == "family"`; someone unknown or no longer listed
  counts as beta, and without a Family nobody but the owner gets them).
  `paid_ok` gates the listing (`voices`, the `custom` box) and `find`,
  which `choose`, the preview and the reply voice (`voice_of` ->
  `engine_for`) all go through, so a paid voice saved earlier falls back
  to the default for a beta tester. The `config.toml` comment says
  "family-tier members only (never beta testers)".
- **The owner's Remove revokes Google too.** `family.py`'s
  `revoke_and_remove` revoked only Notion; "Delete my account" revoked
  both. The Google revoke moved from `Account.revoke_token` into
  `account.revoke_google_grant(family, uid, revoke=None, label=...)`,
  which both now call (Account passes its own `revoke`, so its behaviour
  and log lines are unchanged). Remove runs it first, only for a listed
  id, best effort (a failure is logged with the exception type, never
  the token, and the removal goes ahead), then the Notion revoke and
  removal under the Notion lock as before.
- **Docs.** BLOCKERS has one ordered "Public beta launch" item (Render
  env, legal approval, the launch PR before Google and Notion since they
  need `/privacy` and `/terms`, then Google, verification, Notion,
  Sentry/UptimeRobot, `family_paid_voices`, a dry run, invites, the tag),
  pointing to the existing items. README's Render step lists
  `GOOGLE_PUBLIC_CLIENT_JSON`, `NOTION_OAUTH_CLIENT_ID`/`_SECRET`,
  `SENTRY_DSN`, `PUBLIC_ORIGIN` and `USER_DATA_KEY`; its `[family]` key
  list is one complete list matching `DEFAULTS` (it had a broken
  duplicate and lacked `enabled`, `export_max_mb`); "Persistent disk" says
  to watch Render's disk graph and drops a stale "until merged" sentence.
  The release-check comments in `landing.html` and `privacy.html` list the
  remaining deploy checks instead of G1/G2/G6 (still HTML comments; the
  pages are served by the server, not the service worker, so no cache
  bump). CHANGELOG Unreleased: the dashboard, dev team, launcher, hub and
  design-skill bullets are in a "Dev tooling" subsection at its end, the
  dashboard ones folded into one that says it lives in its own repo (the
  old ones named `tools/agent-dashboard/`, a hub and a phone layout that
  are gone). MANUAL_TEST: Remove also clears "Hey Claude" from their
  Google permissions; a Beta account never sees paid voices.
- **Tests** (+3, 1 changed):
  - `test_voice_picker.py`: `test_paid_voices_are_family_tier_only_even_when_allowed`
    (through the routes: a beta tester with `family_paid_voices` on sees
    no paid voice and no ID box, `choose` gets 400, a paid voice saved for
    them falls back to the default engine; a family-tier member and the
    owner can list and choose it). `test_family_members_get_paid_voices_only_if_allowed`
    now passes a Family stand-in and checks beta, unlisted and no-Family.
  - `test_account.py`: the owner's Remove revokes the refresh token before
    the folder goes (INFO line has the id, no token; an unknown id revokes
    nothing); a revoke failure still removes them.
  - `test_family_google.py::test_removing_a_member_deletes_their_token`
    now fakes the revoke (it would otherwise call Google) and checks it.
  - Revert checks (source only, `__pycache__` cleared): the
    `voice_picker.py` change fails both voice tests (`assert True is False`:
    the beta tester was offered the ID box); the `family.py` call fails
    all three Remove tests.
- **Counts** (Linux sandbox, Python 3.13): 2652 passed, 35 skipped (+3
  over master's 2649 passed, 35 skipped); JS 118 pass.

## Confirmations that don't nag (2026-10-21)

Kyle: moving a calendar event asked for confirmation, and the app asked up
to three times because speech-to-text misheard his "yes".

- **Why a move asked.** Not a `confirm_prompt`: `move_calendar_event` has
  only `confirm_if_untrusted`, and create/update ask without taint only for
  guest changes. Two causes remained: (1) Claude asking "are you sure?" on
  its own (only the delete tool told it not to), then the app's own
  question on top when the conversation was tainted; (2) taint from the
  listing every move starts with. `list_events` marks the result untrusted
  when any listed event has notes (`description`), even Kyle's own
  (`organizer.self` True), or is an invite, a Gmail-made event, a
  `guestsCanModify` event, or comes from a calendar he doesn't own
  (`calendar="all"` takes in subscribed holiday calendars). From then on
  `ClaudeChat.tainted()` (`has_marked_results`) makes every change ask.
  The taint rules are unchanged (SECURITY.md "Calendar data from Kyle's
  own events"); cause 1 is fixed.
- **Tool descriptions.** `create_calendar_event`, `move_calendar_event`
  and `update_calendar_event` end with `JUST_DO_IT` ("just call the tool:
  don't ask him to confirm first yourself"). Their `confirm_if_untrusted`,
  guest questions and every delete question are unchanged.
- **One answer classifier.** `cli.classify_answer` returns "yes", "no" or
  "unclear", ignoring case and punctuation (and the iPhone's curly
  apostrophe). Yes: yes/yeah/yep/yup/ya/yah/yea, sure, ok/okay, do it, go
  ahead, please, correct, right, yes please, affirmative, sounds good,
  alright, 是/对/好/可以/行 (the existing whole-answer Chinese rule), in a
  short answer (at most 8 words) of yes words and filler. Any negation is a
  no. Everything else is unclear. `cli.is_yes` and
  `voice_session.is_explicit_yes` are now this one rule (they were two
  lists; "okay" alone used to be a no in the voice view).
- **One more try, then cancel.** An unclear answer gets "Sorry, I didn't
  catch that. Please say yes or no." once; a second unclear one cancels
  with "I still didn't catch that, so I left it." A clear no cancels at
  once. Same in all four places: `text_confirm`, `voice_confirm` (PC; a
  barge-in over the second question is still a no), the voice view
  (`VoiceConversation._ask_again`: same turn, same pending question, a new
  "confirm" event with `again: true` and a new question chunk; only that
  chunk's own "played" opens it, so a stale "played" or an early answer is
  "not heard" = no, and talking over it is "interrupted" = no), and
  `/api/confirm` (`WebAssistant.ask_again`: the same confirmation id is
  asked again; no page change needed, `app.js` already shows a new
  "confirm"). The cancelled tool result now tells Claude to say so in one
  short sentence and not to call the tool or ask again, so it doesn't
  re-ask by itself.
- **Logs.** INFO has lengths and the verdict ("Voice confirmation (11
  chars) -> unclear", "Confirmation of turn 1 unclear: asking once more");
  the answer text stays at DEBUG (caplog tests).
- **Tests** (+125): new `tests/test_confirm_ux.py` (123: 50 yeses incl.
  misheard ones, 25 noes, 26 unclear; a move with no taint runs with no
  question; a delete asks exactly once; a misheard delete answer asks once
  more then cancels; a move after listing an outside invite still asks;
  own-event notes still taint (pins the reported cause); calendar edit
  descriptions; text, PC voice, hands-free voice and `/api/confirm`
  re-ask flows, including a barge-in on the second question, a stale
  "played", and a clear no that never re-asks), plus
  `test_voice_ws.py::test_unclear_answer_is_asked_again_over_the_socket`.
  Changed: `test_voice.py::test_only_an_explicit_yes_confirms` ("okay" is
  now a yes; "ya", "Yep!", "Cancel.", "stop" added; the unclear answers
  moved to the re-ask tests) and one `test_voice_ws.py` case ("okay, and
  what about Friday?" now re-asks, so the clear-no case says "Nope, cancel
  that.").
  - Revert check (old source, with only `classify_answer`/`JUST_DO_IT`/
    `text_confirm(output_fn=)` shims so the file imports): 6 calendar/text
    tests fail as expected (descriptions, misheard delete, text re-ask) and
    every classifier, re-ask and voice test fails; the move-without-taint,
    delete-asks-once, tainted-move and own-notes tests pass on the old code
    too, as they should (they guard behaviour that must not change).
- **Round 2 (judge, 5 low findings).** Digits are words (`[a-z0-9']+`), so
  "Yes, 11" to "move it to 10?" is unclear, not a yes; an answer with "?"
  or "？" ("Right?", "Okay?") is unclear; 没错 and a repeated yes
  (是的是的, 对对对, 好的好的) are yeses while 不对 and 错了 stay noes; the
  stale-"played" test now delivers the late report inside `_ask_again`
  (before the new question's audio exists) and fails without the
  `pending.chunk = None` guard; SECURITY, README and MANUAL_TEST no longer
  say any other answer is a no. Revert check: the old `cli.py` fails the
  15 new classifier cases ("okay 4 pm" was already unclear), and removing
  the guard fails the stale-"played" test.
- **Counts** (Linux sandbox, Python 3.13): 2793 passed, 35 skipped (+125
  over master 5f42072's 2668 passed, 35 skipped); JS 158 pass (every file
  in `tests/js`, none changed).

## Speech accuracy: small.en, beam search, vocabulary prompt, optional cloud STT (2026-10-21)

Kyle: the app "always mishears me" in voice conversation. Every path
(wake word, push-to-talk, the phone's mic button, hands-free) goes through
`stt.Transcriber`, which ran `base.en` with greedy decoding (`beam_size=1`)
and no English prompt.

- **`stt.py`.** `Transcriber(beam_size=5, initial_prompt=None,
  hotwords=None)`: beam search 5 (falsy = the default, never greedy by
  accident); `initial_prompt` None = the language's built-in prompt (new
  `EN_PROMPT`: "calendar, assignment, Notion, reschedule, tomorrow, remind
  me, delete, move, Canvas, exam, quiz"; Chinese keeps `ZH_PROMPT`), "" =
  none; `hotwords` (names; only sent when set, so faster-whisper < 1.0.2
  still works). `options()` feeds both the first decode and `redecode`,
  which keeps beam, prompt and hotwords (temperature 0, no conditioning,
  as before). **Prompt-echo guard:** a transcript that is the whole prompt,
  or a run of it across two or more of its items, is silence (""); one
  item alone ("Tomorrow.", "Remind me.") and a single hotword ("Kai") are
  kept. The transcript is logged at DEBUG only; the echo line has its
  length. `stt.from_config(stt, language, make=None)` builds it from
  `[stt]` (cli.py and webapp.py now use it; cli passes its own
  `Transcriber` so tests that patch `cli.Transcriber` still work).
  `family_speech` (Chinese members on an English server) passes
  `beam_size` but not the owner's prompt or hotwords.
- **Defaults.** `[stt] model = "small.en"` (int8) for the PC, new keys
  `beam_size = 5`, `initial_prompt = "auto"`, `hotwords = ""`, `engine =
  "whisper"`, `cloud_model = ""`, `cloud_timeout = 10` (DEFAULTS and
  config.toml). `SERVER_PROFILE` pins `stt.model = "base.en"` (baked into
  the image; small.en's RAM on Render unmeasured), so Render stays on
  base.en with beam 5; `WHISPER_MODEL` still overrides. `config.mom.toml`
  keeps `small`, with beam 5, the Mandarin prompt and `engine = "whisper"`.
  The Pi: `install_pi.sh` appends `[stt] model = "base.en"` to
  `config.local.toml` unless it already has an `[stt]` section, so the Pi
  downloads and runs base.en; the pi-arm64 simulation checks it (once, and
  that `load_config()` gives base.en). `doctor.check_whisper_model` names
  the beam (and warns that 1 is greedy), mentions small.en's ~480 MB;
  `check_cloud_stt` says whether the engine's key is set, never its value.
- **Cloud engine `stt_cloud.py`** (mirrors `tts_elevenlabs.py`).
  `CloudTranscriber(local, engine, ...)` with `[stt] engine = "openai"`
  (`gpt-4o-transcribe`, multipart WAV, `{"text": ...}`, the prompt plus
  hotwords as `prompt`) or `"deepgram"` (`nova-3`, raw WAV, `keyterm`
  query params from the prompt's items and hotwords, max 50,
  `results.channels[0].alternatives[0].transcript`). Active only with
  `OPENAI_API_KEY` / `DEEPGRAM_API_KEY` (both added to `SECRET_ENV_VARS`
  and `ISOLATED_ENV_VARS`; an OpenAI `sk-...` pattern added to the log
  redaction). Timeout 1-60 s (default 10; 0 = default), then local
  Whisper; any error, 5xx or odd reply also goes local and the cloud is
  skipped for 60 s. Over 5 minutes of audio stays local. Logs: INFO has
  engine, model, seconds, length, time; transcript at DEBUG; failures
  give the HTTP status or exception type only (a `requests` error quotes
  the URL, whose query holds keyterms). Invited people's audio is already
  counted against `stt_seconds_per_day` before any engine runs
  (quotas.py). `STT_ENGINE` env override for Render; render.yaml offers
  `STT_ENGINE`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY` (all `sync: false`).
- **Endpointer** (`web/voice-core.js`): `Endpointer.answerPending` and
  `answerMinUtteranceMs` (150): while a yes/no question is open, only
  sounds shorter than 150 ms (instead of `min_utterance_ms`, 300) are
  misfires, so a quick "yes" reaches the server. A lower
  `min_utterance_ms` is never raised. `voice.js` sets the flag from
  `controller.awaitingAnswer` before each frame. The confirmation logic
  (voice_session.py, `is_yes`, `/api/confirm`, TurnController) is
  untouched. `sw.js` cache v27 -> v28.
- **CI.** `tests.yml` caches `whisper-base.en-small.en-<os>` and has a
  "Download Whisper models" step (base.en and small.en) before pytest, so
  a download failure shows instead of the real-model tests skipping. A
  cold cache fetches ~550 MB (Windows too).
- **Not done (left as found):** the 3-tap box downsample in
  `web/voice-worklet.js` and the linear-interpolation resample in
  `audio.py` (the researcher ranked them last); `lecture.py` keeps its
  own Whisper settings.
- **Measured:** nothing about accuracy; Hugging Face is blocked here, so
  no Whisper model ran in this sandbox (the slow tests skip and run in CI).
  The speed cost of small.en + beam 5 over base.en greedy is an estimate
  (README says "about two to three times", not measured). MANUAL_TEST
  11b is the real measurement: ten phrases and five quick "yes" answers,
  phone push-to-talk, phone hands-free and the PC.
- **Tests:**
  - `test_stt.py`: `test_beam_size_and_prompt_come_from_config` (beam 5,
    `EN_PROMPT`, `small.en`/int8 from DEFAULTS; custom beam, prompt,
    hotwords; 0/None/"" beam = 5; "" prompt = none; Chinese "auto" =
    `ZH_PROMPT`), `test_whisper_echoing_its_prompt_is_silence`,
    `test_transcript_logged_at_debug_only`; the redecode test now checks
    beam 5 and the prompt on the retry; `test_transcriber_language`
    expects the English prompt. Slow (CI): the `whisper` fixture is now
    the shipped transcriber (small.en, beam 5, prompt);
    `test_short_answers_through_the_shipped_model` ("yes", "yeah go
    ahead", "no", "move my dentist appointment to Friday", TTS-generated)
    and `test_near_silence_never_comes_back_as_the_prompt`.
  - `test_stt_cloud.py` (new, 12): both request/reply shapes, fallback
    and 60 s cooldown on a timeout, a 503 and a reply without a
    transcript, no key (one warning), long or empty audio stays local,
    the echo guard, INFO/DEBUG caplog (no transcript, key or keyterm at
    INFO), `from_config` (engine case, timeout bounds, unknown engine),
    keys secret and isolated, doctor never prints the key.
  - `test_docs.py`: `[stt]` mirrored in config.toml and README's table,
    the keys in `.env.example` and render.yaml, Mom's `[stt]`.
    `test_server_profile.py`: the server keeps base.en under the shipped
    config.toml; `STT_ENGINE`. `test_family_google.py`: family speech
    gets `beam_size`, not the owner's hotwords. `test_doctor.py`,
    `test_end_to_end.py` (skip guard checks the shipped model),
    `test_webapp.py` (sw v28) updated.
  - JS: `voice-core.test.js` "a short yes to an open question isn't a
    misfire".
  - Revert checks (one source change at a time, restored after): greedy
    beam fails 2 tests; no English prompt fails 7; no echo guard fails
    the echo test; the transcript at INFO fails 2; `beam_size is not None`
    fails the config test; logging the exception message fails the
    cloud caplog test; no cooldown fails the 3 fallback cases; ignoring
    `answerPending` fails the JS test; no `stt` in SERVER_PROFILE fails
    the server test; family speech without `beam_size` fails its test.
- **Counts** (Linux sandbox, Python 3.13): 2685 passed, 40 skipped (+17
  passed, +5 skipped over master 5f42072's 2668 passed, 35 skipped,
  measured on a clean export; the 5 new skips are the slow small.en tests,
  Hugging Face being blocked here). JS: 118 pass in the `.mjs` files plus
  41 in `voice-core.test.js` (+1).

### Round 2 (judge: CHANGES_NEEDED, 6 findings; merged master 515ca4e)

1. **A blip over the question no longer cuts it off.** The 150 ms minimum
   applied while the yes/no question was still playing, so a 150-299 ms
   sound over it became a barge-in that cut the question and declined
   the action. New `TurnController.answerWindow` (`awaitingAnswer &&
   !replyAudible`) feeds `endpointer.answerPending` in `voice.js`, and the
   Endpointer latches it per utterance (`answerShort`: true only if the
   speech began in the window and it never closed while speaking).
   PR #51 changed no web code; its re-ask replays question audio, which
   closes the window. Tests: a TurnController + Endpointer test (a 190 ms
   blip during question audio sends no interrupt and no utterance; speech
   that began over it and ended after it keeps the 300 ms minimum; a
   190 ms "yes" after it is sent), and the window closing mid-utterance.
2. **Separate instances start with the keys in the environment.**
   `OPENAI_API_KEY` and `DEEPGRAM_API_KEY` left `ISOLATED_ENV_VARS` (still
   in `SECRET_ENV_VARS`): OpenAI's quickstart does `setx OPENAI_API_KEY`,
   which would stop Mom's and Kai's instances, and `config.mom.toml` pins
   `engine = "whisper"`. SECURITY.md records it as deliberate.
3. **Two-item answers are kept.** The echo guard now needs the whole
   prompt (a 1-2 item one only if it is the initial prompt sentence) or
   a run across 3+ of its items. "Reschedule tomorrow.", "Canvas exam.",
   "Tomorrow, remind me.", "Exam, quiz.", "Kyle, Kai." are in the test's
   kept list.
4. PRIVACY.md wording sent to the master.
5. `pi-arm64.yml` cache key `pi-sim-small.en-<requirements hash>`, so the
   Pi simulation saves small.en once.
6. PI_MIGRATION, README and MANUAL_TEST say to add `[stt]` settings
   under the existing `[stt]` line of `config.local.toml` (a second header
   is invalid TOML). `test_pi_stt_advice_keeps_config_local_toml_valid`
   parses the installer's block with an added key, checks the wording, the
   pi-sim key and the pin below.
- `requirements.txt`: `faster-whisper>=1.0.2` (hotwords).
- Each new test fails on the round-1 code: the TurnController test with
  `answerWindow = awaitingAnswer`; the mid-utterance case without the
  per-frame latch reset; the key test with round-1 `config.py`; the echo
  test with round-1 `stt.py` ("Reschedule tomorrow." dropped); the docs
  test with the old PI_MIGRATION sentence or the old cache key.
- **Counts** (Linux sandbox, Python 3.13, after merging 515ca4e): 2811
  passed, 40 skipped (+18 passed, +5 skipped over 515ca4e's 2793 passed,
  35 skipped, measured on a clean export). JS: 118 in the `.mjs` files
  plus 42 in `voice-core.test.js` (+2 over master).
## Student assistant P1: school deadlines from Canvas (2026-10-21)
The data foundation of the student assistant: each person's deadlines
(exams, assignments, quizzes, classes), captured from Canvas, Canvas's
notification emails or said by hand, with conflict handling, for the owner
and every invited person. P2 (scheduler and push) and P3 (planner, quiz,
screenshot import) read it through `DeadlineStore.list(uid)`.
- **`kwil/deadlines.py`** (new): `DeadlineStore`, one SQLite per
  person (the owner's `[student] dir`/deadlines.sqlite, an invited
  person's `<[family] dir>/<id>/deadlines.sqlite`), `secure_delete=ON`, WAL
  with a TRUNCATE checkpoint after every delete, one short connection per
  call (nothing holds a file open), reads never create a file. API (uid
  None = the owner): `list(uid)` -> `{id, course, title, kind, due,
  status, source, source_due, captured_at}` soonest first, copies that
  agree shown once; `propose(uid, candidates, source)` upserts on
  (source, source_key); the same item (course code + title) with another
  date from another source is a `conflict` on both; a confirmed date is
  never overwritten (only `source_due` follows the feed); dismissed stays
  dismissed; a deleted synced row leaves a SHA-256 tombstone of its key so
  it doesn't come back; `resolve(uid, id, "confirm"|"dismiss"|date)`,
  `add` (by hand, confirmed), `delete`, `delete_source`, `export`.
  Bounded by `[student] max_items` / `max_per_source`; rows over 60 days
  past are pruned and never re-added. `open_store(config)` gives the
  process one store per config (owner tools, Family, routes share it);
  Family sets `member_guard` so a write for someone just removed is
  refused under Family's lock.
- **`kwil/canvas.py`** (new): a bounded RFC 5545 reader (unfolding,
  VEVENT/VTODO, VALARM skipped, DTSTART/DUE with `VALUE=DATE`, `Z`, `TZID`
  or floating, SUMMARY's `[course]`, UID kinds; 2 MB, 2000 events, no new
  dependency); `public_url` (https, port 443, no userinfo, every resolved
  address `is_global`); `fetch_ics` (each redirect hop checked, size cap
  while streaming); `CanvasAPI(base, token, http)` on httpx (planner items,
  courses, assignments, module pages; `Link rel="next"` on the same host
  only; 403/`X-Rate-Limit-Remaining` and 429 backoff 1, 2, 4 s);
  `find_school` (Canvas's own lookup, else a guess from a domain);
  `extract_from_pages` (structured output like `lecture.summarize`, never a
  forced tool call; only {title, course, kind, due} kept, keyed by text so
  a re-read doesn't duplicate; usage on the person's meter).
- **`kwil/student_web.py`** (new): `StudentSync` (credentials, sync,
  throttle) and the routes: `GET /api/student/items`, `POST
  /api/student/canvas` ({ics_url} or {base_url, token}; invited people
  only, sealed under `canvas.CANVAS_LABEL` in `<id>/canvas.enc`, 503
  without `USER_DATA_KEY`; the owner gets 409 and uses `CANVAS_ICS_URL` /
  `CANVAS_TOKEN` + `[canvas] base_url`), `POST /api/student/disconnect`
  ({delete_items}), `POST /api/student/sync` (429 + Retry-After within
  `sync_minutes`, 409 while one runs), `POST .../items/{id}/resolve`,
  `DELETE` (and `POST .../delete`) for an item or a source, `GET
  /api/student/schools` (10 a minute). Sync reads the feed or the planner
  (and module pages with `[canvas] pages`), then with `[student] gmail`
  the Canvas emails of the last 14 days (`GmailInbox.search`, read-only,
  each email once), on the person's own key when they have one
  (`family.UserApi`); errors come back as codes only.
- **`kwil/tools/deadlines.py`** (new): `list_deadlines`
  (`untrusted_output`, `private`), `confirm_deadline` and `add_deadline`
  (`confirm_if_untrusted`), `delete_deadline` (`confirm_prompt` always);
  questions in English and Chinese (i18n `deadline_*`).
- **Phase B wiring**: `cli.register_student` in `build_tools`;
  `cli.owner_gmail` split out of `register_gmail` (same behaviour);
  `Family._deadline_tools` in `_register_tools`, `Family.gmail_inbox`
  (now also used by `_google_tools`), `Family.while_listed`; `account`
  export adds `deadlines.json` (never the Canvas link); one
  `student_web.register` line in `create_app`; `[student]` and `[canvas]`
  in `DEFAULTS` and config.toml, `[student] dir = "/data/student"` in the
  server profile, `config.mom.toml` off with its own folder;
  `CANVAS_BASE_URL` override; `CANVAS_TOKEN`/`CANVAS_ICS_URL` in
  `SECRET_ENV_VARS`, `ISOLATED_ENV_VARS`, render.yaml, .env.example and
  conftest's cleared variables; redaction patterns for feed links and
  Canvas tokens.
- **UI**: `kwil/web/student.js` (new, self-contained: builds its
  own "School deadlines" card in `#student` or before the API key section,
  text only). Not yet on the page: `/student.js` still needs the static
  route, `sw.js` SHELL (cache bump), `cloud.py` `PUBLIC_GET` and a script
  tag in index.html, which the P2 branch owns. Chinese strings for the card
  (i18n.js) are left for that merge too.
- **Tests.** New `tests/test_deadlines.py` (23), `tests/test_canvas.py`
  (31), `tests/test_student_web.py` (27), `tests/js/student.test.mjs` (7),
  `tests/test_contract.py` +3 (the extraction request against the
  contract fake, plus two `output_config` refusals; live variants run in
  CI's api-contract job), and the fixture `tests/fixtures/canvas_feed.ics`
  in Canvas's shape (CRLF, folded lines, `VALUE=DATE`, `TZID`, a VALARM,
  `event-assignment-123`; kept byte-exact by .gitattributes). The fake
  Canvas answers with Canvas's planner JSON, `Link rel="next"`, the 403
  rate limit; Claude with the Messages API's shape. Existing tests
  updated for the new default tools: test_family's isolation test now
  expects exactly the member's own four deadline tools bound to her id;
  the Notion and family-Google fixtures turn `[student]` off (only the
  tools that piece adds); test_config_cli turns it off with the rest;
  test_account expects `deadlines.json` in the export; test_docs knows the
  tool names.
  - Failing first: `test_resyncing_the_same_feed_adds_no_duplicates` and
    `test_a_confirmed_date_survives_a_changed_feed`. Reverting the
    (source, source_key) upsert fails both (`added 3 != 0`, a second copy);
    reverting the confirmed-date guard fails the second
    (`2026-11-11T04:59:00Z != 2026-11-04T04:59:00Z`).
  - Found while testing: a feed's long-past item was re-added on every
    sync and pruned again (now skipped at proposal).
- **Counts** (Linux sandbox, Python 3.13, not real hardware): 2739
  passed, 37 skipped (+84 passed, +2 skipped: the live variants of the
  extraction contract test, which need `ANTHROPIC_API_KEY`) over master
  3b7ae54's 2655 passed, 35 skipped; JS 125 pass (+7).
### P1 Round 2 (judge: CHANGES_NEEDED, 7 findings)
- **HIGH 1, repeated titles:** a title that one source repeats under other
  keys (a weekly "Lecture", a lab) was a conflict, and confirming one
  dismissed the others. Now only rows from *different* sources can
  conflict or be dismissed, and only when each is its source's only copy
  of the item (`DeadlineStore._singles`). Several Canvas emails about one
  item never conflict: the newest email's date wins, older ones that
  disagree are dismissed and ones that agree follow it. `list` collapses
  same-date copies across sources only (two same-day "Lab" rows from one
  feed both show). The judge's probe now gives 0 conflicts, and
  confirming one lecture leaves the other two drafts.
- **MED 2, unbounded API answers:** every Canvas API answer (and the
  school lookup) is now streamed up to `MAX_API_BYTES` (2 MB); a listing
  keeps at most `MAX_API_ITEMS` (1000).
- **MED 3, separate instances:** `cli.main` turns `[student]` off for an
  instance started with its own `--env-file` unless its config sets its
  own `[student] dir` (logged); README's family-copy section says so.
- **LOW 4, no overall time limit:** a feed fetch has `FETCH_SECONDS` (60)
  in all and a whole sync `SYNC_SECONDS` (180), checked between chunks,
  requests and backoff sleeps; past it the remaining sources (Gmail too)
  report `timed_out`.
- **LOW 5, rate limit and module pages:** a 403 is a rate limit only with
  "Rate Limit Exceeded" or no quota left (Canvas sends
  `X-Rate-Limit-Remaining` on every answer; FakeCanvas now does too); a
  course or page that can't be read is skipped, and only a rate limit or
  the deadline stops the modules step.
- **LOW 6, SECURITY wording:** a password field is ignored and never
  stored (it isn't a 400). The "Bounded" bullet lists the new caps.
- **LOW 7, page wiring:** `/student.js` added (one line each) to the
  static routes, `sw.js` SHELL (cache version left for the merge),
  CloudGuard `PUBLIC_GET`, index.html and test_webapp's public list;
  Chinese for every fixed string of the card in i18n.js (the dynamic
  pieces became separate elements, confirm() questions are translated).
- **Tests** (+10 Python, +1 JS): test_deadlines (repeated titles, newest
  email), test_canvas (plain 403, skipped page/course, size and item caps,
  slow-drip deadline), test_student_web (one sync deadline, password not
  stored + SECURITY wording, page wiring), test_config_cli (separate
  instance), student.test.mjs (every fixed string translated). Each was
  revert-checked against its source change alone (`__pycache__` cleared):
  all fail without it.
- **Counts** (Linux sandbox, Python 3.13): 2749 passed, 37 skipped (+94
  over master 3b7ae54's 2655 / 35); JS 126 pass.
### P1 Round 3 (judge: CHANGES_NEEDED; findings 1, 2, 4 here, 3 at merge)
- **1, the newest email by Gmail's arrival time:** rows from one sync
  share `captured_at`, and Gmail lists newest first, so the oldest email
  was taken as the newest and the right date was dismissed for good. The
  arrival time (Gmail's `internalDate`, set on arrival, not the sender's
  Date header) now goes `GmailInbox.search` (`received`) ->
  `email_texts` -> `extract_from_pages` (`sent_at`) -> a new `sent_at`
  column (added to older files on open). The newest is the highest
  `sent_at` (else `captured_at`, then the earlier row of one sync). Older
  emails are now `superseded`, hidden from `list()` but not dismissed:
  deleting a bad newest email brings the older one back.
- **2, a confirmed email date is always an anchor:** a confirmed gmail row
  that isn't the newest still takes part, so a newer email that disagrees
  shows as `conflict`; confirming the newer one dismisses the old answer.
- **4, the separate-instance off-switch compares resolved paths**
  (`./data/student`, `data/../data/student` or the absolute path are all
  the owner's folder).
- **Tests** (+4, 2 extended): test_deadlines (both emails in one
  `propose`, newest first, and delete brings the older back; a confirmed
  email date vs a newer email; a round-2 file without `sent_at`),
  test_canvas (arrival time carried through extraction; the search and
  `email_texts` shapes), test_config_cli (three spellings of the owner's
  folder). Revert-checked: the round-2 store logic fails the two store
  tests; dropping the time from `email_texts` and the plain string compare
  each fail theirs. The judge's r2a.py probe now gives Nov 9 draft / Nov 2
  superseded, and confirmed + newer -> conflict.
- **Counts** (Linux sandbox, Python 3.13): 2753 passed, 37 skipped (+98
  over master 3b7ae54's 2655 / 35); JS 126 pass.

## Kyle's own calendar data doesn't ask (2026-10-22)

Kyle: moving an event still asked for confirmation after listing his
calendar, though the events were his own. Cause: `list_events` tainted the
listing whenever any event had notes (`description`), even on his own
events, and whenever a calendar he doesn't own was read (with
`calendar="all"`, Google's subscribed holiday calendar); `list_calendars`
tainted whenever a shared calendar existed. Kyle decided his own data must
not trigger confirmations.

- **What changed** (`kwil/tools/calendar.py`): one rule,
  `GoogleCalendar._outside`, now decides taint for listings, moves and
  updates. An event is someone else's text when it is on a calendar Kyle
  doesn't own or `is_from_others` (no `organizer.self is True`, an
  `eventType` other than default/focusTime/outOfOffice/workingLocation/
  birthday, or `guestsCanModify`). Notes alone no longer taint. Google's own
  read-only calendars (`is_google_public`: ids ending
  `@group.v.calendar.google.com` with reader or freeBusyReader access, e.g.
  holidays) never taint, and their events aren't flagged `from_others`.
  Birthday events (`eventType` "birthday") count as Kyle's.
  `list_calendars` no longer taints (names and access only, never a
  description); another person's calendar name is cut to 60 characters.
- **Still taints:** invites from others, `fromGmail` events,
  `guestsCanModify` events (notes included), any calendar shared with Kyle.
  Remaining risk (SECURITY.md): text Kyle pastes into his own event notes
  is trusted, and so is a note edited by someone he gave write access to
  one of his own calendars.
- **Tests** (+5, 5 changed): `tests/test_confirm_ux.py`:
  `test_own_event_notes_taint_the_conversation_unchanged` now pins the new
  behaviour (own event with notes, then a move: no question, patch runs);
  `test_others_text_on_the_calendar_still_taints_and_the_move_asks`
  (invite, fromGmail, guestsCanModify: tainted, the move asks);
  `test_holidays_and_birthdays_do_not_taint` ("all calendars" with a
  birthday and Google's holidays calendar, then a move with no question).
  Fixtures are full events.list item shapes (kind, etag, creator,
  organizer, eventType, birthdayProperties). `tests/test_calendar_features.py`:
  new `test_list_calendars_does_not_taint_and_cuts_a_shared_name_short`;
  the own-notes, other-calendar, all-calendars, `list_calendars` and
  changes-on-a-calendar tests now use a friend's shared calendar for the
  tainting case and expect no taint from holidays.
  - Revert check: with the old `calendar.py`, 7 tests fail (the own-notes
    pin, holidays/birthdays, own-notes listing, other-calendar,
    all-calendars, both `list_calendars` tests). Taking only "birthday" out
    of the allowed types fails the holidays/birthdays test. The three
    still-taints cases pass on the old code by design; an over-broad fix
    (dropping `is_from_others` from `_outside`) fails all three.
- **Round 2 (judge: CHANGES_NEEDED, 3 findings).**
  1. No laundering through Kyle's own events: when `create_event` or
     `update_event` writes a title, place or notes while
     `UNTRUSTED_CONTEXT` is set, the event gets
     `extendedProperties.private.hc_untrusted = "1"` (the event's other
     private properties are sent with it, so the patch can't drop them).
     `is_from_others` treats a marked event as someone else's, so later
     listings, moves and updates taint. A clean change never sends the
     field, so the marker stays. No events.list `fields` mask is used, so
     the property comes back as is. Tests:
     `test_confirm_ux.py::test_notes_written_in_a_tainted_conversation_taint_the_next_one`
     (a tainted create with notes, then a clean conversation: listing
     taints and the move asks) and
     `test_calendar_features.py::test_text_written_in_a_tainted_conversation_is_marked_for_good`
     (clean update keeps the marker; tainted color-only update adds none;
     tainted update merges the private map; tainted/clean create).
  2. Whitespace in `SERIES = {` restored.
  3. `test_a_google_public_calendar_id_without_the_extras_does_not_taint`:
     a `...@group.v.calendar.google.com` id with no calendar list doesn't
     taint, a `...@group.calendar.google.com` id does.
  - Revert check: with the round-1 `calendar.py` both finding-1 tests fail.
    The finding-3 test passes on round-1 code (it covers an untested
    branch); it fails if the `entry is None` branch is dropped, and fails
    if the suffix check also accepts `@group.calendar.google.com`.
- **Counts** (Linux sandbox, Python 3.13): 2917 passed, 42 skipped (+8
  over master 6ea4081's 2909 passed, 42 skipped). No JS touched.

## Session watcher for local Claude Code sessions (2026-10-22)

Kyle: a Codex-driven watcher on his own computer (Windows 11 PC, MacBook)
that notices when a Claude Code session is blocked on him. New folder
`tools/session-watch/` (stdlib-only Python 3.10+, not part of the
assistant package):

- **What it does.** `notify_hook.py` is a Claude Code hook: a
  `Notification` writes `<claude dir>/session-watch/blocked/<id>.json`
  (atomic, sanitised message, safe ids only); `PostToolUse`,
  `UserPromptSubmit`, `Stop` and `SessionEnd` delete it. It prints nothing
  and always exits 0. `session_watch.py` merges those markers (24 h) with a
  transcript check (newest tool call in the last 64 KB of a recent
  `projects/*/*.jsonl` has no result and the file is quiet for
  `--threshold`, 120 s), notifies each new block once (osascript /
  PowerShell toast / notify-send, argument lists only), logs one line per
  block to `watch.log` and prints text or `--json`. `--no-notify` writes
  nothing. `run-codex-watch.ps1`/`.sh` loop every N minutes and, on a new
  block with `codex` installed, run `codex exec --sandbox read-only` with
  `AGENTS.md` (report only, never approve) into `codex-summary.log`.
  `hooks-snippet.json` is pasted by Kyle; nothing edits `settings.json`.
  Cloud sessions are out of scope (docs/WATCHDOG.md covers those).
- **Tests** (`tests/test_session_watch.py`, 41 in round 1, all offline in tmp
  folders): transcript blocked / answered / parallel calls / too recent or
  old / malformed lines / tail only / unsafe names; hook set and clear for
  every event, bad ids (`../../settings`, slashes, too long), message
  sanitising, the real hook command via stdin (oversized and junk input,
  exit 0, empty stdout), exit 0 when the write fails, Windows replace
  retry; merge with the hook winning; no re-notify on a second scan and a
  new prompt notifying again; 7-day pruning; `--no-notify` writes nothing;
  caplog keeps the notification text out of INFO and `watch.log`; the
  notifier on darwin/win32/linux is an argument list without `shell=True`
  and the untrusted body never inside a script; a missing notifier is
  logged and the scan continues; stable `--json`; `CLAUDE_CONFIG_DIR`;
  no `settings` string in the code or launchers and a full run touching
  nothing outside `session-watch/`; the `.sh` launcher LF and 100755 in
  git; the launchers' Codex command; the hook snippet's events; AGENTS.md's
  never-approve rule.
  - Mutation checks: interpolating the body into the Windows script,
    dropping the notified-state check, the message sanitising, the id
    check, writing state under `--no-notify`, or the hook's catch-all each
    fail their test.
- Not checked here (no Windows, macOS or Codex in the sandbox): the toast,
  the osascript banner, a real Claude Code hook run and the Codex summary;
  they are in MANUAL_TEST.md. PowerShell syntax was not run (no pwsh).
- **Round 2 (judge findings).**
  - Subagents: current Claude Code writes them to
    `projects/<proj>/<sid>/subagents/agent-*.jsonl` (checked in this
    sandbox's own `~/.claude`), and the main transcript sits on an
    unanswered `Agent` call meanwhile. The newest subagent write now counts
    as the session's activity, and up to 8 subagent transcripts written
    since the main one last moved are checked for an unanswered call
    (reported "... in a subagent").
  - Parallel calls are grouped by `message.id` (the real shape is one
    content block per entry); only string `tool_use_id`s are kept; deep
    JSON is skipped; any error is caught per transcript and per marker
    (type name at DEBUG); `--interval` logs a failed scan and retries.
  - The hook keeps only `permission_prompt`, `idle_prompt`,
    `elicitation_dialog` (or no type), clears on `PostToolUseFailure`, and
    the snippet adds that matcher and quotes the path (README and
    `schtasks` example too). `notification_type` is in `--json` and the
    notification text; `cwd` is no longer in `--json` (the project name is),
    so Codex never sees the working-folder path.
  - Folders are created 0700 one by one, `watch.log` is opened 0600, the
    `.sh` launcher runs under `umask 077`.
  - 22 new tests (63 now): the real subagent layout (busy, prompt inside,
    background agent, dead subagent ignored), the real parallel shape,
    unhashable ids, deep nesting in a transcript and a marker, DEBUG-only
    error logging, `--interval` surviving a failed scan, exit 2 on bad
    options, POSIX modes, `auth_success`/unknown types ignored, the type in
    `--json` and the notification, the snippet's quoting and matcher, one
    test per bound (`MAX_NOTIFIED`, `MAX_MARKER_BYTES`, `MAX_STDIN`,
    `watch.log` rotation) and the `.sh` launcher run with fake
    `python3`/`codex`/`sleep` (summary rotation and 0600), and the docs
    naming exactly the `--json` keys. 16 of them (and 3 updated ones) fail
    on the round-1 code; the dead-subagent and exit-2 tests pin behaviour;
    the bound tests (which guard round-1 code) each fail when their bound
    is removed.
- **Round 3 (judge findings, plus Kyle's security-check request).**
  - A busy background agent no longer hides a main-session prompt: each
    transcript is judged on its own mtime, and only an open `Agent`/`Task`
    call in the main transcript counts its subagents' writes as activity.
    A quiet subagent prompt is reported even while a sibling still works.
  - AGENTS.md now tells Codex to read no file at all (only the command's
    output); the markers hold `cwd` and `transcript_path`.
  - `MAX_SUBAGENTS` (8, newest first) has a test counting tail reads.
  - A state folder that can't be written: `notified.json` is saved before
    any notification, the scan raises `StateError`, `--once` prints the
    result, logs one warning (exception type only) and exits 3; no
    traceback and no notification storm. A `watch.log` failure after the
    save notifies once and exits 3. README documents it.
  - New block kind `security_denied` (transcript check only, main and
    subagent transcripts): an `is_error` tool result matching
    `DENIAL_PATTERNS` (auto mode classifier, permission deny rule,
    PreToolUse hook), not Kyle's own rejection, with no later successful
    tool result and no new message from Kyle, quiet for `--threshold`.
    Only the tool name and a fixed reason are kept (the denial text can hold
    the command); keyed per denied tool_use id so it is announced once.
    AGENTS.md has Codex list it and send Kyle to decide, never suggesting
    a way around the check.
  - 31 new tests (94 now): the probe's background-agent shape, a stuck
    subagent beside a busy one, the subagent cap, AGENTS.md's no-files
    rule, exit 3 in-process and from the real command line (one stderr
    line, no traceback), the save-before-notify order, security stops for
    each pattern and content shape, progress or Kyle's reply clearing it, a
    later failure or meta entry not clearing it, Kyle's rejection (also one
    quoting check-like words) and plain errors not counting, a denial in a
    subagent, the denial text absent from `--json`, `watch.log`, logs and
    the notification, malformed shapes, and the docs. On the round-2 code
    14 of them fail (the rest: the cap test fails when the cap is removed,
    the AGENTS.md test on the old AGENTS.md, the rejection test when the
    rejection exclusion is removed, the progress test when a later success
    no longer clears a denial; the docs test on the old docs).
- **Round 4 (judge findings on round 3; Kyle approved one extra round).**
  - With the hook installed a denial no longer shows as "idle": after the
    denied turn ends, Stop clears the marker and Claude Code's
    `idle_prompt` writes a new one about a minute later, before
    `--threshold` passes. `merge` now lets a transcript `security_denied`
    block beat an `idle_prompt` marker (`DENIAL_BEATS`); a
    `permission_prompt` or `elicitation_dialog` marker still wins.
  - `DENIAL_PATTERNS` are now anchored regexes matched at the start of the
    error result (leading whitespace stripped), which is where Claude
    Code's own denial texts sit; a failing command's output starts with
    "Exit code N" and no longer counts. The deny-rule pattern needs
    "permission to use <tool>" as whole words and the exact "has been
    denied"; the hook pattern needs the `PreToolUse:` prefix. The matched
    head grew from 500 to 2000 characters so a deny rule quoting a long
    command still matches. The comment now says what the code does.
  - Docs: the README's merge line and security-stop item, SECURITY.md and
    MANUAL_TEST.md's denial check state the denial-vs-idle rule and the
    anchoring.
  - 15 new test cases (109 in the file): the three judge strings and six
    more failing-output shapes as negatives, two realistic positives
    replacing the loose ones (a leading-whitespace deny rule, "Hook
    PreToolUse:Bash denied this tool") plus a long-command deny rule, the
    anchoring, the probe's Stop-then-idle sequence, approval/question
    markers still winning, and `merge` directly. On the round-3 code 13
    of them fail; the long-command and approval-marker cases pin
    behaviour that already held; the docs test fails on the round-3 docs.
- **Counts** (Linux sandbox, Python 3.13, no eSpeak installed in this
  sandbox): round 4 2964 passed, 69 skipped, 9 failed, 26 errors (the
  same eSpeak-missing tests). Round 3: 2949 passed, 69 skipped, 9 failed, 26 errors, against the
  same tree without this change's 2855 passed, 69 skipped, 9 failed, 26
  errors; the failures and errors are the same eSpeak-missing tests on
  both. Round 2 was 2918 passed. No JS
  touched.

## Student assistant P2: study briefings and phone notifications (2026-10-21)
Branch `claude/student-proactive` from master 3b7ae54. The app speaks up
first: rules over the student deadline store (P1's `DeadlineStore`, built
in parallel) decide when a notification is worth sending; Claude only
words it.
- **`kwil/push.py`** (new): Web Push. The VAPID key comes from
  `VAPID_PRIVATE_KEY` or is generated once into `[push] owner_dir`
  (`write_private`); `GET /api/push/key` serves the public key. Each
  person's subscriptions (at most 5, oldest dropped, same endpoint
  replaced) are sealed with `userkeys.seal(PUSH_LABEL, uid)` in their own
  folder (`<family dir>/<id>/push.enc`, the owner's in `owner_dir`); no
  usable `USER_DATA_KEY` -> 503 and nothing stored. Endpoints must be
  https:443 at a known push service (Apple, FCM, Mozilla, Windows), so a
  subscription can't aim the server at an internal address. `send()` uses
  pywebpush (aes128gcm, VAPID `sub` = `[push] subject` or the public
  https origin); a 404/410 forgets that device.
- **`kwil/proactive.py`** (new): `evaluate()` is pure Python over
  the store's rows: morning (07:30, due today/tomorrow and exams this
  week), evening (20:30, due tomorrow), weekly (Sunday 18:00, the week),
  exam (14/7/3/1 days out while no review blocks are planned; P3's
  planner can pass `reviews`), new items since the last run (not on the
  first run; not when another rule in the same push already lists them),
  and after-class (class-kind items, `class_minutes` after the start).
  `Scheduler.run_person` skips anyone without a device before reading
  anything, respects quiet hours in the person's time zone and
  `max_per_day`, records the keys `student:<uid>:<rule>:<date>` in
  `proactive.json` (private, ids and dates only, bounded) before sending,
  sends rules due together as one push, and asks Claude once (fast model,
  no tools, a JSON of course/title/kind/due/days_left only), falling back
  to a template (a beta tester without a key, an API error). The owner's
  keys also go through `failover.leader.JobRunner` on a failover setup.
  The thread wakes every `check_minutes` or at the next morning / evening
  / weekly / end-of-quiet time, whichever is first. `GET
  /api/student/brief` returns the last message (memory only) or a
  template summary of the week, plus an `/api/audio/<id>` link (the reply
  audio route, bound to the session).
- **Wiring**: `build_assistant` attaches `assistant.push` and
  `kwil.proactive` (none on a `--candidate` copy); `create_app`'s
  lifespan starts and stops the scheduler, taking `assistant.deadlines`
  (P1) as its store, and registers the four routes. `brief.js` is in the
  static routes, `sw.js` SHELL (cache v28) and CloudGuard `PUBLIC_GET`.
  `sw.js` has `push` (always shows a notification; text cut, url only a
  path on this site) and `notificationclick` (focus the open app and
  post it a message, else open `/#brief`). `index.html`: the menu's
  "Enable notifications" button and status line, and the `#brief-view`
  screen; `app.js` four hook lines (show, refreshState, configure).
  Config `[push]` and `[proactive]` in DEFAULTS and config.toml (server
  profile: `owner_dir = /data/student`; `config.mom.toml` its own
  folder); `VAPID_PRIVATE_KEY` in `SECRET_ENV_VARS` and
  `ISOLATED_ENV_VARS`; requirements `pywebpush>=2.5`, `cryptography>=47`.
- **UI** (`kwil/web/brief.js`, new): the permission is asked first
  thing in the tap (iOS asks only from a gesture), then
  `pushManager.subscribe` with the VAPID key and `POST
  /api/push/subscribe`; "Turn off notifications" unsubscribes at the
  server and in the browser; outside the Home Screen app an iPhone is
  told to add it. The brief shows text with `textContent` and a Play
  button that plays only when tapped. Chinese strings in i18n.js.
- **Tests** (+55 pytest, +17 JS):
  - `tests/test_push.py` (30): sealed at rest (no endpoint or key in the
    file), fails closed without `USER_DATA_KEY`, refused endpoints
    (metadata address, localhost, look-alike host, userinfo, port, http,
    too long), the real push services, 5-device cap, VAPID made once
    (0600 on Linux) and from the env, the payload and claims passed to
    pywebpush (patched), 410/404 forget the device and 500 keeps it, real
    pywebpush in curl mode signs and encrypts our subscription, per-person
    files (a copied file doesn't open; removal deletes it), caplog,
    requirements pins.
  - `tests/test_proactive.py` (24), on a pinned clock with a stub store:
    **failing-first `test_two_ticks_on_the_same_day_send_exactly_one_morning_push`**,
    no repeat after a restart, no Claude call and no push when no rule
    fires (and without a store or a device the store isn't read), quiet
    hours in each person's own zone, the daily cap and one push for rules
    due together, exam marks and planned reviews, new items, after-class,
    only extracted fields (no description, grade, URL, source key; no
    tools), Claude's text vs the template, a beta tester without a key
    (no call on Kyle's key), JobRunner with a shared `MemoryEventLog`
    (PC stands down while the Pi is up; the cloud sees it done), caplog,
    bounded private state, settings validation, the next wake time, no
    scheduler on a candidate copy, and the routes (401 signed out; the
    owner's key/subscribe/brief/audio/unsubscribe; a member's own brief
    whose audio the owner's session can't fetch; 404 with push off; the
    lifespan starts the scheduler with `assistant.deadlines`).
  - `tests/test_contract.py`: the briefing request against the contract
    fake (and live models in CI's api-contract job).
  - `tests/js/sw-push.test.mjs` (7) and `tests/js/brief.test.mjs` (10).
  - Changed: `test_webapp.py` cache version v28 and `/brief.js` in its
    public list.
  - Revert checks (source only): without the fired-key check both
    same-day and restart tests fail (2 pushes); without the host check 4
    endpoint cases fail; without the 404/410 branch the gone-device test
    fails; without quiet hours or the cap their tests fail.
- **Counts** (Linux sandbox, Python 3.13): 2710 passed, 37 skipped (master
  3b7ae54: 2655 passed, 35 skipped; the 2 new skips are the live
  contract runs without `ANTHROPIC_API_KEY`); JS 135 pass (was 118).
- **Open:** subscriptions live on the backend that saved them (SECURITY
  "Not done: per-backend subscriptions"); the owner's export/delete of
  `data/student` is P1's `account.py` piece.

### Round 2: the judge's five findings (2026-10-21)
1. **The owner's briefings were lost when the backend holding the phone
   wasn't the leader** (MED). The owner's `JobRunner` now has no leader
   gate (`proactive.owner_jobs`: `LeaderCheck(role, [])`): the backend
   that holds a device sends to it. Each device's push is claimed in the
   shared log (`student:owner:<date>:<device hash>:<rules hash>`), so a
   device saved on two backends gets it once; the claims sync through the
   history `Syncer` (`webapp.start_history_sync` sets `jobs.sync`).
   SECURITY and README corrected. Tests (separate `owner_dir` per
   backend, shared `MemoryEventLog`): Pi up with no device + PC with the
   phone -> exactly one push all morning; one device on both -> one push;
   different devices -> one each; the Syncer wiring. The old test shared
   one folder across backends, which hid the bug; it is replaced.
2. **A tap could open another site** (LOW). `sw.js` `safeUrl` resolves
   with `new URL(url, self.location.origin)` and requires the same
   origin; `push.safe_path` refuses control characters, spaces,
   backslashes, a scheme or a host. Tab, newline and CRLF cases in
   `sw-push.test.mjs` and `test_push.py`.
3. **A bad port answered 500** (LOW). `.port` is read inside the `try`:
   `:99999` and `:abc` are 400 (unit cases and through the route).
4. **A failed push lost the day's brief** (LOW). `Push.deliver` reports
   delivered / failed / gone; with nothing delivered, something failed and
   nothing gone, the keys, the day's slot and the "seen" ids are taken
   back and the next check sends it. A claimed send that fails leaves no
   "done" marker; it is tried again after `CLAIM_TTL`. Tests: 503 then
   sent at the next check (and the evening slot still free); 410 is not
   retried; a failed claimed send is retried once the claim expires.
5. **An old brief stayed in memory after a delete** (LOW). The kept brief
   remembers its item ids; a read drops it when one is no longer in the
   store, the next check drops briefs of people no longer listed, a read
   for a removed person drops it, and `Scheduler.forget(uid)` is there
   for callers.
- Python 3.10: `proactive.py` and `push.py` import and run `evaluate()`
  under CPython 3.10.20 (uv). No `from __future__ import annotations` in
  `proactive.py` on purpose: FastAPI must see `Request` in its routes.
- Revert checks against the round-1 source: all 10 new or changed
  Python cases fail (bad port: `ValueError` -> 500; tab/newline url
  passes; failed push not retried; brief kept). With only the leader gate
  put back into `owner_jobs`, the Pi-leads and different-devices tests
  fail. The old `safeUrl` fails 2 sw-push tests.
- **Counts** (Linux sandbox, Python 3.13): 2720 passed, 37 skipped; JS
  135 pass.

### Round 3: the judge's two LOW findings (2026-10-21)
1. **A permanent push error was retried at every check**, with a Claude
   call each time. `push.retryable(status)`: only no answer (network),
   429 and 5xx are "failed" (retried); any other 4xx is "refused": kept
   device, no take-back, a WARNING with the status. A retry reuses the
   words it already had (`Scheduler._retry_text`, keyed by the fire keys,
   bounded, dropped on success, `forget` or removal), so it makes no new
   Claude call. Tests: 400/403/413 over five checks -> one push attempt
   and one Claude call; 429 twice then OK -> three attempts, one Claude
   call, the same words.
2. **The production wiring was untested.** `test_from_config_gives_the_owner_jobs_no_leader_gate`
   builds the scheduler through `proactive.from_config` (`[failover]
   role = "pc"`, a Pi peer, a real `HistoryStore`): the owner's
   `LeaderCheck` has no higher peers, and with the Pi probed healthy the
   PC still sends to the phone it holds.
- Revert checks: the round-2 `push.py`/`proactive.py` fail the three 4xx
  cases and the reuse test; the round-1 leader gate put back into
  `from_config` fails the wiring test.
- **Counts** (Linux sandbox, Python 3.13): 2725 passed, 37 skipped; JS
  135 pass.

### Round 4: merged master, the real deadline store, deleted means gone (2026-10-22)
- **Merged origin/master 6ea4081** (`--no-ff`): P1's deadline store
  (#50), confirmations (#51), #52 and speech-to-text (#53). Conflicts
  resolved by keeping both sides: `sw.js` (SHELL has `/student.js` and
  `/brief.js`, cache **v30**), `webapp.py` (`student_web.register` then
  the push/brief routes), `config.py` (`SECRET_ENV_VARS` and
  `ISOLATED_ENV_VARS` keep CANVAS_*, OPENAI/DEEPGRAM and
  `VAPID_PRIVATE_KEY`), `i18n.js` (both string blocks), README (project
  layout and the "Student deadlines" then "Study briefings" sections),
  PROGRESS (both sections), and `test_webapp.py` (cache pin v30; the
  public list already had both scripts). `cloud.py`, `index.html`,
  `app.js`, `config.toml`, `requirements.txt` and SECURITY merged
  cleanly.
- **The real store, end to end.** `build_assistant` sets
  `assistant.deadlines = deadlines.open_store(config)` (`[student]` on;
  the same store as the owner's tools, Family and `student_web`) and
  hands it to `proactive.from_config(..., deadlines=)`. Its `list(uid)`
  returns draft, confirmed and conflict rows only.
  `test_the_real_deadline_store_drives_a_morning_push` builds the
  assistant, adds two items through `DeadlineStore.add`, and runs the
  app's lifespan: one morning push naming the item due today, not the
  far one. Without the wiring it fails.
- **Judge round 3 (MED, rule 4): a deadline deleted after a failed push
  went out on the retry.** Held retry words are now `(fire keys, item
  ids, text)` and are reused only if the keys match and every held id is
  among the ids now firing; otherwise they are dropped and the message is
  written again. Every run first drops held words naming an item no
  longer in the store (also in quiet hours, or with no device left), and
  a run with nothing firing drops them. Tests: 503 with a1+a2, a1
  deleted, the retry and the brief don't name a1 and nothing is held;
  every item deleted -> nothing held and the brief doesn't name it (also
  across quiet hours). Both fail on the round-3 code.
- `sw-push.test.mjs` pins v30.
- **Counts** (Linux sandbox, Python 3.13, merged tree): 2982 passed, 44 skipped; JS 143 pass.
- **Judge MED: a separate instance shared the owner's `[push] owner_dir`**
  (Kai's config has no `[push]`): its sealed `push.enc` replaced Kyle's
  subscriptions, and it shared his VAPID key and `proactive.json`.
  `cli.main` now turns push off for a separate `--env-file` instance whose
  `owner_dir` resolves to the default (same check as the `[student] dir`
  guard), logged at INFO; `config.mom.toml` has its own folder. Test
  `test_separate_instance_never_shares_the_owners_push_folder` (fails with
  the guard disabled). Counts: 2983 passed, 44 skipped; JS 143 pass.

## Kwil rebrand (2026-10-22)

Kyle renamed the product before the public launch: **Kwil** (said
"quill"), wake phrase "Hey Kwil". "Claude" stays only where it names the
model or Anthropic ("powered by Claude", `CLAUDE_MODEL`, the Claude API).

- **What people see.** The manifest (`name`/`short_name` "Kwil"), every
  page title and brand line (`index`, `landing`, `privacy`, `terms`,
  `waitlist`), the "What is Kwil?" link (`public_site.py`, i18n.js, Chinese
  "Kwil 是什么？" with the name untranslated), push titles
  (`proactive.TITLE`, `sw.js` fallback), the iPhone home-screen label
  (`apple-mobile-web-app-title`), the console's reply prefix (`Kwil: ...`;
  the preflight parses `Claude: ...` too), the speaker in exported
  transcripts (`**Kwil:**`), the preflight report header, the account
  export (`kwil-data.zip`) and the backup (`kwil-backup-<day>.zip`).
  The system prompt says "Kyle's personal voice assistant, Kwil (said like
  "quill")", and the Chinese one "私人语音助手 Kwil（读作 quill）".
- **The package is `kwil/`** (`git mv assistant kwil`; every import,
  `-m` command, path, logger name and caplog filter, script, unit, workflow
  and doc). `assistant/` is now an alias kept for at least one stable
  release, in two layers:
  - `assistant/_alias.py`, a `sys.meta_path` finder (installed by
    `assistant/__init__.py`): `import assistant.X` returns the `kwil.X`
    module object itself (its `__spec__` stays `kwil.X`; only names that
    exist under `kwil` resolve), and its loader hands runpy the real
    code (`get_code`, `get_source`, `is_package`, `get_filename`), so
    `python -m assistant.host` / `assistant.watch` / `assistant.deploy` /
    `assistant.tools.google_auth` run as before. `assistant/__main__.py`
    runs `kwil.cli.main` for `python -m assistant`.
  - A one-line file shim for each of 610017b's 81 modules
    (`assistant/X.py`, `assistant/tools/*.py`, `assistant/failover/*.py`:
    `shim(__name__)`), which installs the finder and puts `kwil.X` in
    `sys.modules`. Judge round 1 (HIGH) found that a process started on
    the old code, such as the old `deploy.ps1` running the old
    `assistant.deploy`, has the old package (no finder) in memory when
    `git pull` swaps the tree; its lazy `from assistant.host import ...`,
    `from assistant import smoke`, `assistant.tools.google_auth`,
    `assistant.devteam` would have crashed the deploy halfway. Now they
    find these files and get the same module objects as `kwil.X`.
  So the PC's `\HeyClaude\` tasks, a Pi's old units, any start command
  that runs `python -m assistant`, and old processes mid-update keep
  working, and `monkeypatch`/`mock.patch` through either name hit the same
  object.
- **Running across the rename.** The host supervisor, the go-live
  check and the updater's smoke run `python -m <package()>`
  (`version.package`): `kwil` when `kwil/cli.py` exists, else `assistant`
  in a checkout from before the rename (judge MED: not the `kwil/` folder,
  which a rollback leaves behind holding `__pycache__`), so a rollback past
  it doesn't crash-loop a supervisor started on the new code.
  `requirements.txt` is byte-identical to 610017b (judge MED: any change
  makes the old deploy and the updater demand `--requirements`, which would
  stop the automatic update that carries the rename). `restart_instance.ps1` looks for `\Kwil\<task>`,
  then `\HeyClaude\<task>`, then the hand-made `\Hey Claude Web`, and its
  process match (like `install_windows_service.ps1`'s and
  `install_family_tasks.ps1`'s) accepts `-m kwil` and `-m assistant`, so
  the next `deploy.ps1` on Kyle's PC restarts the old tasks. The install
  scripts register under `\Kwil\` and remove the matching `\HeyClaude\`
  tasks first; `install_service.sh` / `install_watch.sh` install
  `kwil.service` / `kwil-watch.timer` and disable and delete the
  `hey-claude*` units (the pi-arm64 simulation plants an old unit and
  checks it is replaced).
- **Env vars:** `KWIL_VERSION` first, `HEY_CLAUDE_VERSION` still read.
  `CLAUDE_MODEL`, `CLAUDE_FAST_MODEL`, `CLAUDE_ROUTER` unchanged.
- **Phone:** `HeyClaude*` page globals are `Kwil*` (`kwilFailover`,
  `KwilI18n`, ...). The service-worker cache is `kwil-shell-v1`, so every
  phone reloads its assets once. Nothing branded lived in localStorage
  (its keys are `failover`, `backendSettings`, ...), so there was nothing
  to migrate; the failover token's IndexedDB keeps its name `hey-claude`
  (`backend-router.js` `TOKEN_DB`), like the `hc_session` cookie, so nobody
  is signed out. A JS test opens a database written before the rebrand.
- **GitHub rename:** `[update] repo` defaults to
  `github.com/kylechen839-max/Kwil`, and the origin check accepts the old
  `Claude-Personal-Assistant` name too (`updater.REPO_ALIASES`, one pair
  only). `allowed_signers` moves to `%APPDATA%\Kwil\` /
  `~/.config/kwil/`; the old folder is read while the new one has none.
- **Kept on purpose** (SECURITY.md "Kwil rebrand"): HKDF labels
  `hey-claude/...` (a new label can't open stored secrets), `render.yaml`
  `name: hey-claude` and disk `hey-claude-data` (a renamed Blueprint
  service is a new one with an empty disk; commented), `data/` and DB
  names, `logs/assistant.log`, the `[assistant]` config section, the
  Notion "Hey Claude coordinator inbox" and its message headers (found by
  title; docs/COORDINATOR_LINK.md), and `.claude/settings.json` (agents
  don't edit permissions; `python -m assistant --check` still works).
- **Wake word:** `keywords/hey_claude.onnx` stays until a "Hey Kwil" model
  is trained (BLOCKERS.md). Follow-up transcripts drop a leading "Hey Kwil",
  "Hey Quill" (Whisper's usual spelling) or "Hey Claude"
  (`cli.BRAND_WAKE_PHRASES`); the phone's voice mode defaults to
  "Hey Kwil".
- **Docker:** the image copies `kwil/` and the `assistant/` alias, runs
  `python -m kwil`, is tagged `kwil` (CI `kwil:ci`), and the container
  smoke also runs `python -m assistant --help`.
- **Tests.** New `tests/test_rebrand.py`: the alias is the same module
  object (and keeps the real `__spec__`), monkeypatching either name,
  `python -m kwil --help` and `python -m assistant --help` in a subprocess,
  `python -m assistant.host|watch|deploy|tools.google_auth --help` (same
  output as the `kwil.` names), a file shim for every 610017b module, a
  file shim and the finder giving the same object, an old process (old
  package in memory, no finder) whose tree is swapped and whose lazy
  imports then resolve to the `kwil` modules, `package()` with a leftover
  `kwil/__pycache__`, requirements.txt unchanged since 610017b (skips
  where the clone lacks that commit), `KWIL_VERSION`/`HEY_CLAUDE_VERSION`, both repo names accepted (and
  others refused) through `Updater.origin_problem`, the old signers
  folder, `version.package`, no user-facing "Hey Claude" in the pages,
  manifest, i18n, system prompts and page globals (allowlist: the
  wake-phrase note), the kept labels/cookie/Render names, the wake-phrase
  stripping, the units and install scripts, the Dockerfile and the backup
  sweep of old temp folders. `tests/test_docs.py::test_no_pre_rebrand_names`
  fails on an old package path, unit, task folder or an unquoted "Hey
  Claude" in README, MANUAL_TEST, PI_MIGRATION, SECURITY, FAILOVER,
  PRIVACY, config.toml or .env.example unless the line says it is the old
  name. `backend-router.test.mjs` checks the pre-rebrand token database.
  The PowerShell scenario tests gained the `\HeyClaude\` and
  `-m assistant` cases (restart_instance 11 checks, install_windows_service
  9); PowerShell isn't available in the sandbox, so those run on CI's
  Windows job only. Every new Python test was checked to fail with its
  source change reverted (one at a time).
- **Counts** (Linux sandbox, Python 3.13, shellcheck installed): 3066
  passed, 37 skipped (baseline 610017b on the same machine: 2998 passed,
  37 skipped; without shellcheck its 7 lint tests skip: 2991/44). JS
  (`node --test tests/js/*.test.mjs`, CI's command) 144 pass (143 before). The PowerShell scenario tests skip here (no
  PowerShell); CI's Windows job runs them.
- **Lesson** (TEAM_MEMORY): the first text rewrite of `assistant.` also
  renamed attributes of variables called `assistant` (`assistant.tools`,
  `assistant.backup`); they were restored and the rename redone by import
  shape.

## Disconnect Google account revokes the grant (2026-10-27)

Needed before recording Google's verification demo: a member's
**Disconnect Google account** only deleted their token file, so "Kwil"
stayed listed at myaccount.google.com/permissions. "Delete my account" and
the owner's Remove already revoked it.

- **What changed.** `Family.disconnect_google` (behind
  `POST /api/google/disconnect` via `GoogleConnect.disconnect`) now calls
  `account.revoke_google_grant(family, uid, label="Family")` first, the
  same helper as the other two (refresh token, else access token, form
  body, 5 s timeout), then deletes the file and the tools as before. Best
  effort: a failure is logged at WARNING by exception type only, never the
  token, and the delete goes ahead. The revoke runs outside the family lock
  (a slow Google never holds up other people), with the "still listed"
  check repeated under the lock before deleting. Both tiers: Google's
  revoke endpoint needs only the token, so the beta's public client and
  the family's web client work the same.
- **Tests** (`tests/test_family_google.py`, +3): disconnect revokes with
  the saved refresh token while the file still exists, then deletes it,
  logs "revoked" at INFO with the id and no token, and a second disconnect
  sends nothing (parametrized over the family and beta tiers); a revoke
  that raises (with the token in its message) still disconnects, logs the
  type at WARNING and never the token. The module's autouse
  `no_google_network` fixture now also stubs `account.revoke_google`, so
  the existing disconnect tests stay offline. All three new tests fail
  with the `family.py` change reverted (`seen == []`).
- **Docs.** SECURITY.md's family Google paragraph (the "not revoked on
  disconnect" non-fix is gone), README's family Google section,
  MANUAL_TEST's disconnect check (Kwil gone from the permissions page).
- **Counts** (Linux sandbox, Python 3.13): 3188 passed, 37 skipped
  (3185/37 before: the three new tests). No JS changed.
## Move a PC family copy's history to the cloud (2026-10-27)

Mom and Kai move from their own PC copies (`config.mom.toml`,
`data/mom/history.sqlite`) to the cloud as Family members; their
conversations come with them.

- **`HistoryStore.import_conversation` / `import_conversations`**
  (`history.py`): takes `export(conv, "json")`'s shape, keeps the
  original id, start, message times (one ms apart within an exported
  second, so the order holds), title, summary, "ended" and pin, and writes
  them as the same events a live conversation makes (`_record`: text
  through `_clean` and `MAX_TEXT`, tool events cut to `tool_event`'s
  shape). An imported conversation exports exactly as it did on the PC.
  Everything is checked before anything is written (400 otherwise). An
  `import:<id>` mark in `history_meta` holds a salt for the event ids
  while it is written and "done" afterwards: a retry skips it (also after
  the member deleted it, so it never comes back), and an import cut off
  by a crash is finished without duplicates. With `max_mb` set, a batch
  that leaves the store over it (measured like prune, used pages) is
  deleted again and raises `HistoryFull`. Never touches `sync_events`;
  member stores have no event log.
- **`kwil/family_import.py`**: `POST /api/users/{id}/history/import`
  (owner only: member 404, anonymous 401; unknown id 404, invite not
  accepted 409, over `[family] max_history_mb` 413), written under
  `Family.while_listed` into `family.context(uid).history`; logs the id
  and counts only. Registered beside `account.register` in
  `webapp.create_app`. And the PC CLI, `python -m kwil.family_import
  --from <history.sqlite> --to <https origin> --user <id or name>
  [--dry-run]`: reads a `backup.copy_database` copy in a temp folder
  (deleted after), posts batches under 3.5 MB with `KWIL_CLOUD_TOKEN` as
  `Authorization: Bearer` (no redirects followed), prints counts only,
  refuses non-https unless localhost. `--user` may be the invite-list
  name (looked up with GET /api/users).
- **Decision: the upload list, not tiny batches.** CloudGuard caps every
  body that isn't an upload at `[cloud] max_body_kb` (16 KB), smaller than
  one long message, so batching under it would mean splitting
  conversations. `cloud.is_upload` now also matches
  `/api/users/<id>/history/import`, so it gets `[webapp] max_upload_mb`
  (4 MB on the server), only after CloudGuard's sign-in check.
- New modules need no `assistant/` shim (`test_rebrand.py` covers
  610017b's modules only).
- **Tests:** `tests/test_family_import.py` (30): the owner imports and the
  member's History lists it with its turns and an identical export
  (failed first: 404 without the route), native-looking fields, owner only
  (401/404), unknown and not-yet-accepted members, bad bodies write
  nothing, retries skip, a crash-interrupted import finishes without
  duplicates, a failed import is undone, no `sync_events` rows in the
  member's file, over the cap is 413 with nothing kept, caplog has counts
  and no text or title at INFO, a delete leaves no text in the file and a
  re-import doesn't restore it, a deleted id stays deleted on an
  event-log store, removal after (no folder; a later import 404) and
  during an import (waits; no folder), `[family]` off 404, CloudGuard
  lets a 50 KB import body through but not to another owner route and not
  before sign-in; the CLI through an httpx MockTransport: batches under
  the limit, the Bearer header, the token never in the URL or output,
  the live database untouched and the copy deleted, `--dry-run` posts
  nothing, https-only `--to`, name lookup. `test_docs.py` now reads the
  CLI's flags. Each was checked to fail with its source change reverted
  (route registration, `is_upload`, salted ids, size check,
  `while_listed`, the "done" mark, the empty-row cleanup).
- **Counts** (Linux sandbox, Python 3.13): 3214 passed, 37 skipped
  (baseline 48cb030 on the same machine: 3183 passed, 38 skipped; the
  extra skip there is a git-history test, as the baseline is a `git
  archive` copy). No JS changed.

### Round 2 (judge: APPROVED with four LOW findings; #4, PRIVACY wording, by the master)

1. **A half-imported conversation the member deleted came back.** Fixed
   with the smaller change: `delete()` and prune's `_tombstone` set an
   existing `import:<id>` mark to "done" (`_imported_gone`), so a re-run
   skips it. Test: crash mid-conversation, then delete (or prune), then
   re-import: stays gone (both cases fail without the fix).
2. **The family lock is held longer than SECURITY.md said.** Locking
   unchanged; SECURITY.md now gives the judge's measurement (about 6 s
   for a full 3.5 MB batch, about 4 s more for a 413 undo, sandbox) and
   says the owner's own page waits too.
3. **A temp copy could be left on Windows.** The CLI now checks the
   folder after `backup.cleanup`; if it is still there it prints its path
   (never contents) and exits 1, the import itself still done. Test with
   cleanup stubbed (fails without the check).
- **Counts** (Linux sandbox, Python 3.13): 3217 passed, 37 skipped
  (baseline 48cb030: 3183 passed, 38 skipped); `test_family_import.py` 33.
## Title transcripts no longer say "Kyle" (2026-10-27)
- **Bug:** `history.message_transcript` labelled every user turn
  "Kyle:", and `TITLE_PROMPT` said "a conversation between Kyle and his
  voice assistant". The same `Recorder`/`HistoryStore` code records family
  members and beta testers (`family.py` builds a per-member Recorder), so
  their words reached Anthropic's title/summary call as Kyle's.
- **Fix:** user turns are now "User:" and the prompt says "a person and
  their voice assistant" for everyone, the owner included (a title doesn't
  need his name, and no display name is plumbed into `Recorder`).
  `ask_title` is the only caller. Out of scope and unchanged: the owner-only
  Pinecone summary in `memory.py` (family chats run with `memory=None`; the
  zh `config.mom.toml` instance has no Pinecone) and the Markdown export's
  "**Kyle:**" label.
- **Tests:** `tests/test_history.py::test_a_members_title_request_never_says_kyle`
  (a family-style chat with `user_name="Mom"` ends; the title call's text is
  "User: ...\nAssistant: ..." and neither it nor the system prompt says
  "Kyle"). It fails with the `history.py` change reverted (`'Kyle: how
  lo...' == 'User: how lo...'`). Full suite: 3186 passed, 37 skipped.

## Calm blue theme (2026-10-27)
- **What:** Kyle asked to retheme the web app and the public site from
  Claude's orange and warm cream to a calm, darker blue with cool neutrals.
  Only colours changed: the tokens in `web/app.css` and `web/site.css`, the
  voice view's orb and panels (`web/voice.css`), the cool shadows
  (`static/features.css`), the mic button's highlight (now a soft blue
  `color-mix` of `--accent` with white), the theme-color metas (now also on
  `waitlist.html`), the PWA manifest (theme and background are the light
  `--bg`, `#f5f7fa`, as in index.html's light meta) and the generated icons
  (`webapp.ICON_BG`, the light `--accent`, used by both `ICON_SVG` and
  `icon_png`). The PC backend badge moved from blue to teal so it is not
  confused with the cloud badge, which uses the accent. Semantic green, red
  and amber are unchanged. `sw.js` cache is now `kwil-shell-v5`, so phones
  reload the CSS and icons.
- **New logo:** a quill whose barbs are a sound wave, on a diagonal blue
  gradient (`ICON_TOP` to `ICON_BOTTOM`): a pale, gently bowed shaft, ten
  swept-back barbs fading toward both ends and a plain triangle nib (Kyle
  picked "S1"). `webapp._icon_marks()` lists the shapes ("line" polylines
  and a "fill" triangle); `ICON_SVG` and the numpy `icon_png` both draw it,
  each mark only inside its bounding box (512 renders in about 1 s). The
  512 PNG is plain `any`; a separate `/icon-maskable.png` draws the quill at
  0.68 scale so it stays inside Android's safe circle (radius 0.4 x size).
  Icons render once per process under a lock.
- **Judge follow-ups:** dark-mode graphics (voice button, progress fills,
  tutor glyphs) use a new `--accent-graphic` (light `--accent`, dark
  `--accent-ink`), 3:1 or better on bg, surface and surface-2. The three
  data-URI icons (select chevron, search, empty history) use `#738094`,
  which clears 3:1 on all three backgrounds in both modes (a data URI can't
  follow the dark-mode query).
- **Palette:** light bg `#f5f7fa`, surface `#ffffff`, text `#1a2330`,
  muted `#5d6878`, accent `#2f5d8a` / strong `#244a70` / ink `#234a6e`;
  dark bg `#121a24`, surface `#1b2532`, text `#e8edf3`, muted `#8d9aab`,
  accent `#3f74b3` / strong `#335f94` / ink `#8fb6e3`. The suggested dark
  accent `#4f86c6` gives white text only 3.78:1, so it was darkened to
  `#3f74b3` (4.82:1); `#4f86c6` stays in the translucent soft/mark tints.
- **Contrast (WCAG, computed):** white on accent 6.88 light / 4.82 dark;
  accent-ink on bg 8.60 / 8.31; text on bg 14.75 / 14.88, on surface
  15.82 / 13.14; muted on bg 5.26 / 6.12, on surface 5.65 / 5.41.
- **Tests:** `tests/test_theme.py` (11): no old orange in the web CSS/HTML,
  icon SVG or manifest; manifest and theme-color metas equal the CSS `--bg`;
  the PNG has the gradient corners and white where the marks are; the SVG
  and the PNG draw the same marks; the contrast thresholds above, parsed
  from `app.css` and `site.css` in light and dark; the data-URI grey and
  `--accent-graphic` at 3:1 in both modes; and the maskable icon inside the
  safe zone. The cache pins in `test_webapp.py`, `test_owner_connect.py` and
  `sw-push.test.mjs` expect `kwil-shell-v5`.

## Owner's connected services: Notion and Home Assistant by sign-in (2026-10-27)
- **What:** on the cloud app, ⚙ -> **Connected services** lets Kyle sign in
  to his own services instead of pasting their secrets into Render
  (`kwil/owner_connect.py`, `kwil/web/connect.js`, README "Connect your
  services in the app (cloud)"). A small shared framework (sealed storage
  per service under `[connect] dir`, owner-only routes, bounded single-use
  OAuth state bound to the browser by the `hc_connect` cookie, revoke ->
  delete -> drop tools on Disconnect) with two connectors:
  - **Notion**, through the public integration invited people already use
    (same client and redirect URI; `notion_web`'s `/notion/callback` hands
    an owner state to `owner_connect`). `owner_notion_client()` replaces the
    `NOTION_TOKEN` readers in `cli.build_tools`, `register_devteam`,
    `register_teamchat`, `webfeatures.Features.assignments` and
    `devteam.link_from_config`: with `NOTION_TOKEN` set they build exactly
    the client they built before; otherwise the client looks the token up
    on every request (an httpx.Auth that also refreshes once on a 401 and
    writes the new token back sealed), so lecture notes, digests and the
    dev team inbox work as soon as he connects and stop when he
    disconnects.
  - **Home Assistant**, through HA's own IndieAuth-style OAuth (client id =
    `PUBLIC_ORIGIN/`, redirect `/ha/callback`, PKCE S256). The typed
    address must pass `canvas.public_url` (https:443, public addresses
    only). The refresh token is sealed; access tokens are kept and renewed
    in memory (`HAAuth`), and `HomeAssistant(token_fn=...)` refreshes once
    on a 401 (not on the admin-only template API).
  - Precedence: `NOTION_TOKEN` / `HA_TOKEN` in the environment win
    (`managed: "env"`, Connect refused with 409 and hidden).
- **HA facts:** checked against developers.home-assistant.io/docs/auth_api
  and Home Assistant core/frontend sources (2026.10): `/auth/authorize`
  with `client_id`, `redirect_uri`, `state` (+ optional `code_challenge`,
  S256 only), `/auth/token` form posts (`authorization_code` with
  `client_id`, `code`, `code_verifier`; `refresh_token` with `client_id`),
  `expires_in` 1800, refresh tokens `secrets.token_hex(64)`, access
  tokens JWTs, `/auth/revoke` with `token` (always 200; also ends that
  refresh token's access tokens). Not checked against a real HA instance.
- **Redaction:** `config._SECRET_PATTERNS` now also masks HA's JWT access
  tokens and 128-hex refresh tokens (Notion's `ntn_`/`secret_` were
  already covered).
- **Tests:** `tests/test_owner_connect.py` (56, all offline: Notion through
  `notion_web.TRANSPORT` with the real SDK, HA through
  `owner_connect.TRANSPORT`, names through a fake `canvas.RESOLVE`) and
  `tests/js/connect.test.mjs` (13, node:vm). Each fails with its source
  change reverted: the `notion_web` hand-over (10 failures, incl. the
  connect test with family accounts on), `webfeatures` (lecture notes: 3),
  `cli.build_tools` (startup: 1), `HomeAssistant(token_fn=)` (4), the
  admin-only guard (1), `devteam` (1), the IPv6 brackets (1) and the
  redaction patterns (2). `sw.js` cache bumped to `kwil-shell-v2`.
- **Counts:** full suite 3283 passed, 37 skipped (master 5e83590: 3224 passed,
  38 skipped, run from a `git archive` copy, where one git-dependent test
  skips); JavaScript 157 passed (master: 144). Linux sandbox, Python 3.13.
- **Judge LOW fixed:** `cli.main` now turns `[connect]` off for a separate
  instance (`--env-file .env.<name>`) whose `[connect] dir` resolves to the
  default `data/owner` (as for `[student]` and `[push]`), so Kai's copy
  can't open, replace or delete Kyle's sealed sign-ins; `config.mom.toml`
  also sets `[connect] home_assistant = false`. Tests
  `test_separate_instance_never_shares_the_owners_connected_services` and
  `test_the_mom_instance_never_connects_kyles_home` (tests/test_config_cli.py)
  each fail with their change reverted.
## The owner connects Canvas in the app, with a guided connect (2026-10-27)
- **Behaviour change:** the owner no longer has to put `CANVAS_ICS_URL` /
  `CANVAS_TOKEN` in Render's environment. Without them, the owner's
  **School deadlines** card shows the same connect form as an invited
  person's (feed link, or school + access token), and the link is sealed
  with `userkeys.seal` under `canvas.CANVAS_LABEL` with the id `"owner"`
  (push.py's convention) in `<[student] dir>/canvas.enc` (Render:
  `/data/student/canvas.enc`), written with `write_private`. Same
  `canvas.public_url` checks, 503 without a usable `USER_DATA_KEY`, and
  **Disconnect Canvas** deletes the file (and, asked, what came from
  Canvas). `test_owner_uses_the_environment_never_the_app` was replaced on
  purpose by `test_owner_connects_in_the_app_sealed_at_rest_and_syncs`.
- **Env still wins while set** (`StudentSync.env_managed`, read on every
  request): the status says `managed: "env"`, connect and disconnect are
  409, sync uses the env and never the sealed file. Deleting the env vars
  hands the link to the app with no restart.
- **Guided connect (student.js, owner and members):** **Find my school**
  (the existing `/api/student/schools` lookup), **Open my Canvas
  calendar** (`window.open("https://<host>/calendar", "_blank",
  "noopener")` straight from the tap, so iOS allows it; hosts must pass a
  page-side copy of `public_url`'s rules: https, port 443, no
  `user:pass@`, a dotted DNS name, no IP literal, `localhost` or LAN-only
  suffix), the hint "Tap Calendar Feed (bottom right), copy the link, come
  back.", and **Paste** (`navigator.clipboard.readText()` on the tap only;
  fills the feed field with an `https://`/`webcal://` link, silent if
  permission is denied, never submits). EN + ZH strings in `i18n.js`.
  `sw.js` cache `kwil-shell-v2`.
- **Tests:** `tests/test_student_web.py` +7 (owner connect sealed at rest
  and synced through `canvas.TRANSPORT`; owner link checks; env wins incl.
  `CANVAS_TOKEN` alone, then app takes over; owner Disconnect deletes the
  file and the source purge works; 503 without `USER_DATA_KEY`; a member
  never reads or touches the owner's file and the sealed files don't open
  across ids; caplog: no feed URL, token or host in any log, ids at INFO),
  replacing 1. All 7 fail with only `kwil/student_web.py` reverted.
  `tests/js/student.test.mjs` +5 (owner form visible with `managed:
  "app"`; guided lookup filters unsafe hosts and opens the calendar from
  the tap; a lookup with nothing usable says so; Paste fills and never
  connects; denied is silent, non-links aren't filled, no clipboard hides
  the button) and the i18n scenario covers the new strings; the four
  guided tests and the i18n test fail with `student.js` / `i18n.js`
  reverted (the owner-form test passes on the old page too: the page
  already keyed on `managed`, the server change is what makes it true).
- **Counts** (Linux sandbox, Python 3.13): 3231 passed, 37 skipped
  (baseline 5e83590 as a `git archive` copy on the same machine: 3224
  passed, 38 skipped; the extra skip there is a git-history test). JS:
  149 passed (baseline 144).

### Round 2 (judge: APPROVED with four LOWs)
1. **PRIVACY "School deadlines (Canvas)" row** said the owner's link was in
   `.env`: now encrypted with USER_DATA_KEY for everyone (the owner's in
   `[student] dir`), `.env` the older way for the owner.
2. **A typed Canvas address found nothing** (`https://canvas.school.edu/`,
   `canvas.school.edu/`): with no spaces, `schoolHost(text)` is chosen
   directly when it was typed with `https://` or is a `canvas.` /
   `.instructure.` host; a bare domain (`school.edu`) still goes to the
   lookup (it knows the Canvas host or guesses `canvas.<domain>`, which a
   direct `school.edu/calendar` would miss), sent as the clean host. JS
   test fails without the change.
3. **An older app-stored owner link came back once the env was removed.**
   While env-managed, Disconnect now deletes `<[student] dir>/canvas.enc`
   if it exists (never the env; 409 with no file), and status has
   `app_link` so the card shows Disconnect in that state.
   `test_owner_environment_wins_while_it_is_set` no longer asserts the
   file stays; new `test_owner_can_forget_an_older_app_link_while_the_env_wins`
   and a JS test; both Python tests and the JS test fail without the change.
4. **Family-less (PC owner) path:** two tests (`family=None`): connect
   seals to "owner", sync adds 3, disconnect deletes the file; 503 without
   USER_DATA_KEY. Coverage only: they pass on the round-1 code too.
- **Counts** (Linux sandbox, Python 3.13): 3234 passed, 37 skipped (round
  1: 3231; baseline 5e83590: 3224 passed, 38 skipped). JS: 151 passed
  (round 1: 149).
## The owner connects Google in the app on the cloud server (2026-10-27)

On https://heykwil.com Kyle, signed in with `WEB_TOKEN`, can now tap ⚙ ->
**Connect Google account** himself; before, the cloud built the flow with
`owner=False` (family members only) and his Google was the
`GOOGLE_TOKEN_JSON` secret alone.

- **Setting.** New `[webapp] owner_google` (default false, true in the
  server profile, commented in config.toml). The server profile's
  `[calendar] token` is now `/data/google_token.json` (`save_token` stays
  off). `google_web.from_config` turns the owner's flow on for
  `google_connect` or `owner_google`; `cli.main`'s refusal of inherited
  secrets still keys off `google_connect` only, so Render's
  `GOOGLE_TOKEN_JSON` is not refused.
- **Which token.** `google_auth.owner_connected(config)` /
  `owner_environ(config)`: with `owner_google` on and a readable token in
  that file, the owner's Google is read with `GOOGLE_TOKEN_JSON` hidden;
  otherwise the environment as before (a missing, empty or corrupt file
  falls back to the env token). Used by `register_google` (also
  `has_credentials`), `calendar_extras`, `owner_gmail` (and so the student
  sync), `GoogleConnect.connected` and `doctor.check_google_env`. Tools on
  the connected token get `in_app=True` (errors say "reconnect in the
  app"). With `owner_google` on and no token at all, the Google tools stay
  off instead of failing at first use.
- **Client.** The owner's sign-in uses `[calendar] credentials` /
  `GOOGLE_CLIENT_JSON` only if it is a Web client, else the family's Web
  client (`GOOGLE_WEB_CLIENT_JSON`); the Desktop client can't take the
  https redirect. Google returns to `PUBLIC_ORIGIN`/google/callback. The
  saved token carries the Web client's id and secret.
- **Disconnect.** `POST /api/google/disconnect` now also serves the owner
  when that file exists (still 404 otherwise). It revokes the grant
  outside the lock (`account.revoke_google_token_file`, the path-based
  helper `revoke_google_grant` now wraps), deletes the file
  (`family._unlink`) and rebuilds Calendar and Gmail on the env token (or
  removes them), adding before removing in the shared registry.
- **Status and page.** `/api/google/status` has a new `server_owner`
  field; `"disconnect"` is true for a family member or the owner's file.
  i18n.js hides the first-run guide for the server owner and asks a
  different confirm question (EN and ZH) that says Google may also sign
  out the PC copy (grants in one Google project may be revoked together;
  not confirmed). sw.js cache `kwil-shell-v2`. Side effect: the server's
  signed-out page now has `data-google`, so its login line reads "Enter
  the sign-in code you were sent..." instead of the WEB_TOKEN wording.
- **Tests.** `tests/test_google_web.py` +10: the owner connects on the
  server profile (consent URL has the Web client id and
  `<origin>/google/callback`, the saved token has the Web client's id and
  secret and mode 0600, the built Calendar and Gmail credentials carry the
  connected refresh token though `GOOGLE_TOKEN_JSON` is set, no code,
  state, token or client secret at INFO; Flow.fetch_token is faked at
  Google's token-response shape so the real Flow builds the credentials);
  the env fallback with no file and with junk files; disconnect revokes
  then deletes and falls back to the env token; without an env token the
  tools go, in the same dict; a failing revoke and a corrupt file still
  delete; a family member's Connect and Disconnect never touch the
  owner's file; another browser's callback with the owner's state is a
  400; `--check`; with `owner_google` off `GOOGLE_TOKEN_JSON` still wins
  over token.json; `cli.main` with the server profile and an inherited
  `GOOGLE_TOKEN_JSON` starts. `tests/js/i18n.test.mjs` +2 (the owner's
  disconnect wording, EN and ZH; the guide off for the server owner).
  Updated: the exact status dicts (+`server_owner`), the cache version
  pins, and `test_from_config_builds_it_for_family_only_servers` (the
  server now builds `owner=True`). Run against origin/master's source, 12
  of the 12 new or changed google_web tests and both new JS tests fail
  (two of them, the flag-off and `cli.main` guards, only on the missing
  `owner_google` key: they pin behaviour that must not change).
- **Judge follow-ups.** (1) A Connect that finishes while Disconnect is
  revoking is kept: Disconnect snapshots the file before the revoke and,
  under the lock, deletes it only if unchanged (else returns without
  deleting; the tools stay on the new token). Test
  `test_a_connect_finishing_during_the_disconnects_revoke_is_kept` fails
  without it (the file is gone). (2) The signed-out page polled
  `/api/google/status` every 3 s for 10 minutes, and each 401 counts
  against CloudGuard's per-address request limit: i18n.js now stops
  polling on a 401 (or 404) and starts again when the sign-in form is
  submitted. JS test "signed out: a 401 stops the status polling..."
  fails on the old i18n.js (a timer still running); test_cloud pins the
  signed-out page's `data-google` tag. (3) README and MANUAL_TEST say the
  `GOOGLE_TOKEN_JSON` fallback is the same PC token, to re-paste if
  Google ends it.
- **Counts** (Linux sandbox, Python 3.13): 3236 passed, 37 skipped
  (3225/37 before); JavaScript 147 (144 before).

## Canvas course content, PR (a): store, extraction, token sync (2026-10-27)

With a Canvas access token, Kwil now keeps the text of each active
course's syllabus, pages, assignment descriptions, announcements, module
list and files (PDF, DOCX, PPTX, HTML, text) per person, and syncs it in
the background with no taps (Kyle: "pull syllabus and files without
needing input from the user"). The browser helper and sync keys (PRs b to
d) are not in this change.

- **Store** (`kwil/coursefiles.py`): one SQLite per person
  (`<[student] dir>/coursefiles.sqlite`, an invited person's in their
  folder), `docs` + an FTS5 external-content index (porter, bm25 with the
  title weighted 4x) with history.py's query builder and LIKE fallback.
  `secure_delete=ON`, the FTS `secure-delete` option, an `optimize` and a
  TRUNCATE checkpoint after deletes; Family's `while_listed` guards member
  writes; a generation check stops a sync's writes once the person
  disconnects. Bounds: `[canvas] max_course_mb` 20, `max_person_mb` 100,
  5000 documents.
- **Text** (`kwil/textextract.py`): the type sniffed from bytes; pypdf page
  by page (300 pages, time checked between pages); DOCX/PPTX with zipfile
  + ElementTree (declared size and part count checked first, each part
  capped, DTDs refused); HTML through `html_to_text`. `pypdf>=6.21.3,<7`
  added to requirements.txt (deploys need `--requirements`; without it a
  PDF is left for a later sync).
- **Canvas** (`kwil/canvas.py`): `courses(syllabus=True)`, `pages`,
  `page`, `modules`, `files`, `announcements` and `download`: redirects by
  hand (3), `public_url` on every hop, the token only while every hop was
  the school's host, streamed with `[canvas] max_file_mb` (20; server
  profile 4). Redaction now masks `verifier=`, `token=`,
  `X-Amz-Signature=` and `X-Amz-Credential=`.
- **Sync** (`kwil/student_web.py`): a content step after the planner and
  Gmail, on its own `content_seconds` (300) budget, up to `max_files` (40)
  downloads a run, incremental by `updated_at`/size/lock state; dates in
  syllabus, pages, announcements and schedule-like files go to the new
  deadline source `course_text` as drafts (each text version read once;
  `_extract` now sends and marks at most `MAX_TEXTS` per sync, which also
  fixes `[canvas] max_pages` above 20 marking unread pages as read).
  `CourseSync` runs in the webapp lifespan: every `auto_sync_hours` (6)
  for everyone connected, right after a connect or "Sync now" (which now
  answers `"content": "queued"`), the owner only where the failover
  `LeaderCheck` leads, never on a `--candidate` copy. Disconnect always
  deletes the person's content store. New routes: `GET
  /api/student/courses` (counts), `POST /api/student/courses/{id}/delete`
  (+ DELETE alias; the course stays out of later syncs). Export adds
  `coursefiles.json`.
- **Tools** (`kwil/tools/coursefiles.py`): `search_course_files` (8
  snippets) and `read_course_file` (6000 characters), `untrusted_output`
  and `private`, in `READ_ONLY_TOOLS` and the router's read-only set;
  registered for the owner (`cli.register_student`) and each member
  (`family._deadline_tools`). The phone card labels the new source
  "Course files" (sw.js `kwil-shell-v5`).
- **Tests.** New `tests/test_coursefiles.py` (29): the whole path through
  the background tick with a Canvas fake in real shapes (File objects, the
  302 to an inst-fs host with a signed URL, a committed two-page PDF
  fixture) and `search_course_files("late policy")`; the token never on
  a cross-host hop (headers and URL recorded) and a 10.x hop refused;
  streaming size cap, zip and XML bombs, page and time caps, per-course
  and per-person caps, files per run; incremental and a changed file
  replacing its text; delete-course and Disconnect purge (file and WAL
  bytes grepped, with the FTS option off and another connection holding
  the log); member isolation; export; caplog; taint; a prompt-injection
  syllabus never confirming; leader, candidate and retry policy; no
  pypdf; the FTS-less fallback. Each guarded change was reverted alone
  and its test failed (25 reversions); against origin/master's source the
  file doesn't import. Updated: the family tool set and export file list,
  the cache version pins, `test_docs` identifiers, and
  `test_rebrand`'s requirements check (now the v1.1.0 release against
  610017b, since a dependency is added after that release).
- **Counts** (Linux sandbox, Python 3.13, not real hardware): 3332
  passed, 37 skipped (baseline 9003902: 3302 passed, 38 skipped, run from
  a `git archive` copy where `test_rebrand`'s git-history test skips; it
  runs here). JavaScript 167 passed (167 before).

### Round 2: the judge's findings (2026-10-27)

1. HIGH, a Disconnect during the planner step let the content step
   rewrite the deleted store: `sync()` now reads the person's generation
   before their link and passes it down, and `alive()` also needs the link
   (the app's file, or the owner's environment) to still be there.
2. MED, `CourseSync` busy-looped while a person's sync was running (the
   judge's probe: 227k `try_sync` calls a second): the request is kept
   without waking the thread, which looks again after `BUSY_SECONDS`
   (30). The probe now makes 2 calls a second.
3. MED, a course deleted during a sync came back: `upsert` refuses a
   course marked excluded, in the same connection as the write.
4. LOW, the page's sync right after connecting got a 409 when the
   background held the lock: the connect no longer queues anything (the
   page's own sync queues the content), "Sync now" while the background
   holds the person's sync answers `{content: "queued", background: true}`
   (the card says "Syncing in the background.", with Chinese in i18n.js),
   and the background's runs don't count toward the tap throttle
   (`try_sync(record=False)`), so no "synced recently" right after one.
5. LOW, a UTF-16 DTD got past the byte check: an expat pre-pass with
   DOCTYPE and entity handlers that raise sees the prolog in the part's
   own encoding; an encoding expat lacks is refused.
6. LOW, a 29 KB DOCX could take 350 MB: parts are streamed (64 KB
   chunks, still at most `MAX_XML_BYTES`) through `XMLPullParser`, and
   each element is dropped once read; a paragraph holds at most 50,000
   elements. On the judge's probe peak RSS no longer moves (3.7 s);
   tracemalloc on a 400,000-paragraph part: about 3 MB against 34 MB for
   the whole tree.
7. LOW, one file's transport error stopped every later file on every run:
   `httpx.HTTPError` (and a 5xx) is now a per-file transient skip.
8. Windows: `delete_all` removes the files with `family._unlink` (retried
   on PermissionError) while holding the store's lock, and every store call
   closes its own connection; a test checks both.

Tests: +8 in `tests/test_coursefiles.py` (37 now) and +1 in
`tests/js/student.test.mjs`; the headline test's first assertion changed
with item 4. Reverting each fix alone fails its test (10 reversions,
including the old generation read for 1, the waking request for 2 and
the missing exclusion check for 3). The judge's `probe_races.py` passes.
Counts (Linux sandbox, Python 3.13, not real hardware): 3340 passed, 37
skipped; JavaScript 168 passed.

### Round 3: the judge's two remaining findings (2026-10-27)

1. MED, unbounded nesting: an 18 KB DOCX of about 6.3 million nested
   elements took 1.9 GB and 138 s (the judge's measurement). The parse now
   refuses more than `MAX_XML_DEPTH` (256) open elements at once
   ("too_large"); with the cap held, the per-chunk drop loop scans at most
   256 elements. The judge's `probe_xml2.py` under `ulimit -v 1500000`:
   the 20 MB and 2 MB nested parts are refused in under 0.1 s with peak
   RSS +6 MB; the flat 20 MB part still reads in 3.9 s with no rise. Test
   `test_deeply_nested_xml_is_refused_at_once_with_little_memory` (a 5 s
   deadline and a 20 MB tracemalloc bound; 20 nested tables still read);
   without the cap it fails (runs out of its time).
2. LOW, the start-of-sync generation read had no test of its own: new
   `test_a_disconnect_and_new_link_during_the_planner_get_nothing_from_the_old_run`
   disconnects and connects a fresh link inside the planner step; the old
   run writes nothing into the new store. With the read moved back into
   `_content` it fails.

Counts (Linux sandbox, Python 3.13, not real hardware): 3342 passed, 37
skipped; JavaScript 168 passed.

## Canvas course content, PR (b): the helper's push endpoint, sync keys and pairing (2026-10-27)

For schools that block Canvas access tokens ("many schools block
[tokens]"), a browser helper in the student's own signed-in Canvas tab
can now push course content to Kwil, with one tap in the app to pair it.
The helper itself (an MV3 extension) is PR (c); this change is the
server side and the card.

- **Sync keys and pairing** (`kwil/canvas_helper.py`, new). "Set up Canvas
  helper" (signed in, `POST /api/student/canvas/helper`) makes a one-time
  `kcp1.` code valid 10 minutes; the card shows it as text in a
  `data-kwil-pair` element (never in a URL) for the extension's content
  script to read, and drops it once paired or expired. `POST
  /api/student/canvas/pair` {code} returns a `kcs1.` key once. Per
  person, owner included; only SHA-256 hashes are stored
  (`family.hash_token`, `hmac.compare_digest`), in `canvas_helper.json`
  (owner: `[student] dir`; members: their folder) via `write_private`.
  Pairing again replaces the key; **Revoke helper** (`POST
  .../helper/revoke`, DELETE alias), **Disconnect Canvas** and removing the
  person end it; signing out doesn't (documented). Wrong keys and codes
  count on the web app's shared LoginLimiter (CloudGuard's client key);
  120 pushes an hour per key; one push at a time per person.
- **Push routes.** `POST /api/student/canvas/push/manifest` takes Canvas's
  own JSON (courses with `syllabus_body`, pages, assignments, modules,
  files; top-level announcements), validated, then run through
  `coursefiles.sync_content` with a `PushedCanvas` adapter: the token
  sync's pipeline unchanged (HTML to text, bounds, excluded courses,
  generation/alive, forget-what-is-gone for listings sent in full),
  origin "helper". It answers which files it wants (incremental by
  `updated_at`/size, at most `[canvas] max_files`). `POST .../push/file`
  (multipart) is accepted only for a file a manifest of that person asked
  for within the hour, read with the same extraction (`coursefiles.read_file`,
  now shared with the token sync); a bigger file is kept as "too_large".
  The helper's file URLs are dropped (Kwil fetches nothing). Dates in
  pushed text are read by `CourseSync` at its next tick as drafts
  (`StudentSync.course_dates`, source `course_text`).
- **Outer layers.** `canvas_helper.push_request` (POST, the two push
  paths, a `kcs1.` bearer) is the one exemption: webapp's cross-site check
  and the failover edge's origin/bearer checks let it through (an
  extension's `chrome-extension://` origin; no cookie read, no CORS
  headers); CloudGuard passes it as public with its own slot pool and
  `[webapp] max_upload_mb`, answers 401 to a `kcs1.` key anywhere else,
  and treats pairing like accepting an invite. WebAuth no longer counts a
  `kcs1.` bearer on other routes as a wrong WEB_TOKEN.
- **Settings and logs.** `[canvas] helper` (true) in DEFAULTS and
  config.toml; `_SECRET_PATTERNS` masks `kcs1.`/`kcp1.`. Logs: ids,
  counts, sizes, codes.
- **Card** (`web/student.js`, `i18n.js` Chinese, sw.js `kwil-shell-v7`):
  the Canvas helper section with the AUP note, **Set up Canvas helper**,
  the code, **Revoke helper** (asks first); Disconnect shows for a
  helper-only person.
- **Tests.** New `tests/test_canvas_helper.py` (29): the whole path
  (pair, manifest, the wanted PDF, `search_course_files("late policy")`,
  drafts at the next tick); incremental; files not asked for, oversized,
  bounds, manifest shapes, partial listings; the key hashed and private,
  shown once; the owner; a code single-use and expiring; re-pairing;
  the key only on the push routes (401 elsewhere, not counted); cookies
  never standing in; wrong keys/codes on the shared lockout; revoke,
  Disconnect and removal; signing out keeps it; Disconnect or revoke
  mid-push writing nothing; member isolation; rate limit; busy; helper
  off; an extension origin accepted on push only, no CORS; CloudGuard
  (body cap, pool, 401 elsewhere, pairing public); the failover edge;
  caplog and redaction. `tests/js/student.test.mjs` +5. Failing first:
  against the base the new file doesn't import and the 5 JS tests fail;
  17 source changes were each reverted alone and their test failed (one,
  the generation check, needed a new test first), plus 3 in student.js.
- **Counts** (Linux sandbox, Python 3.13, not real hardware): 3383
  passed, 37 skipped (baseline 63d2ee7: 3353 passed, 38 skipped, run
  from a `git archive` copy where `test_rebrand`'s git-history test
  skips; it runs here). JavaScript 173 passed (168 before).

### Round 2: the judge's findings (2026-10-27)

1. MED, CloudGuard read up to `max_upload_mb` for any `kcs1.`-prefixed
   bearer, from a locked-out address too. It now checks the key before
   any body: the exact `kcs1.` shape, then `HelperKeys.verify` (a few
   small file reads, off the event loop); a malformed or wrong key goes
   through `refuse_token` (counted, 429 at once when locked out, no slot
   of the helper pool, nothing read); `[canvas] helper` off is a 404
   there. The right key still works from a locked-out address. Test
   `test_cloud_guard_reads_no_body_for_a_malformed_or_wrong_key` (an
   ASGI call recording `receive`); without the check it fails, and with
   only the shape check removed too.
2. LOW, a module's non-list `items` gave a TypeError (500 after a partial
   write). `clean_manifest` now makes `items` a list of objects (or
   empty) and HTML fields (`body`, `description`, `message`) strings or
   none; a shape the pipeline still can't take answers 400. Tests
   `test_odd_nested_shapes_are_dropped_not_a_crash` and
   `test_a_shape_the_checks_miss_is_a_400`; each of the three changes
   reverted alone fails its test.
3. LOW, the failover edge counted a `kcs1.` bearer on other `/api/`
   routes as a wrong WEB_TOKEN; it now skips it as it does `hc1.`. The
   edge test adds the no-Origin case (and that a plain wrong bearer is
   still counted); reverted, it fails.

Counts (Linux sandbox, Python 3.13, not real hardware): 3386 passed, 37
skipped; JavaScript 173 passed.

## Canvas course content, PR (c): the Kwil Canvas helper extension (2026-10-27)

For schools that block Canvas tokens: a Manifest V3 extension for Chrome
and Edge in `tools/canvas-helper/` (plain JS, no build step, Load
unpacked) that reads the student's own Canvas with their signed-in
browser session and pushes to PR (b)'s routes. No server change.

- **Pairing, one tap.** `content/kwil-pair.js` on `https://heykwil.com`
  watches for the card's `data-kwil-pair` code (MutationObserver), POSTs
  it same-origin (no cookies) to `/api/student/canvas/pair`, and hands the
  `kcs1.` key to the service worker, which accepts it only from a tab of
  this extension on heykwil.com or a site the person enabled (origin from
  Chrome's `sender.origin`, never a Canvas origin), stores it in
  `chrome.storage.local` with that origin pinned, and schedules a sync in
  30 s plus the 6-hourly `chrome.alarms` one.
- **Canvas found from browsing.** `content/canvas-seen.js` on
  `*.instructure.com` tells the worker; it probes
  `/api/v1/courses?per_page=1` with the session and remembers the origin.
  A school domain of its own: the popup's **Enable on this site** asks
  Chrome for that one origin (`optional_host_permissions`), registers the
  two page scripts there (`chrome.scripting`) and injects them once.
- **Sync** (`lib/sync.js`, `lib/canvas.js`, `lib/manifest.js`,
  `lib/kwil.js`): active courses with the syllabus, pages (bodies; up to
  30 read one by one when the listing has none), assignments, modules
  with items, files, and 120 days of announcements; Link pagination
  (100 a page, 10 pages, 1000 items, as canvas.py), `while(1);` stripped,
  Canvas's rate limit waited out 1/2/4 s then the run stops. Canvas's 401
  "unauthenticated" ends the run (signed out; resumes after a Canvas
  visit), a 401 "unauthorized" or 403/404 sends that listing as null
  (Kwil keeps what it has). Only the fields Kwil reads are sent; no grades,
  submissions, enrollments, people or file URLs. Big manifests split by
  course under Kwil's size limit. Then only the files Kwil's answers asked
  for, from this run's listing on the Canvas origin, at most 40 a run,
  read to `max_file_bytes` + 1. Kwil: the pinned origin only, Bearer key,
  `credentials: "omit"`, `redirect: "error"`; 401 drops the key, 429/409
  and unreachable sides back off 15 min doubling to 6 h (Retry-After
  honoured). One run at a time, 4-minute budget; an Unpair during a run
  stays unpaired.
- **Popup:** status only (paired, Kwil and Canvas hosts, last sync, last
  result), **Unpair this browser** (two clicks), **Revoke in Kwil** (opens
  Kwil: the server has no key-authenticated revoke), and **Enable on this
  site** on a custom https domain.
- **Permissions:** `storage`, `alarms`, `activeTab`, `scripting`; hosts
  heykwil.com, `*.instructure.com`, `*.inscloudgate.net`; optional
  `https://*/*` one origin at a time. Strict extension CSP. No
  `<all_urls>`, `cookies`, `tabs`, remote code or eval.
- **Docs:** `tools/canvas-helper/README.md` (install on Chrome and Edge,
  permissions table), README's Canvas helper paragraph, SECURITY.md
  "The helper extension" (with three deliberate non-fixes), MANUAL_TEST.md
  "Canvas helper extension" (real-school run), BLOCKERS.md (load it,
  real-school run; a store listing costs money, not done).
- **Tests.** New `tests/js/canvas-helper.test.mjs` (28) and
  `tests/js/canvas-helper-ui.test.mjs` (4), with fakes in
  `tests/js/canvas-helper-fakes.mjs` (Canvas's own shapes with scores and
  submissions in them, Link pagination, `while(1);`, rate limits, the file
  store redirect; a fake Kwil and `chrome`): the whole run; the key only
  to Kwil and the session only to Canvas; only wanted files, max_files,
  the +1-byte cut, an unreachable file, an off-host file link; pagination
  and the page cap; retries and a lasting limit; signed out; Kwil's
  401/429/409; origin pinning; pairing acceptance and the content script
  (in node:vm); back-off between alarms; Canvas detection; enabling a
  site; missing permissions; unpair mid-run; one run at a time; batching;
  logs; the popup and background wiring. New
  `tests/test_canvas_helper_extension.py` (4): the manifest's permissions
  and CSP pinned, every named file exists, no eval/remote code/HTML
  injection or text in log calls, and a round trip: the extension's real
  sync in node (`tests/js/canvas-helper-roundtrip.mjs`, with the fixture
  PDF) produces a manifest that `clean_manifest` keeps whole and the real
  routes store, and a multipart body that the real file route reads
  ("late policy" found). Skips without node.
- **Failing first:** the files are new, so against the base every new
  test fails to import. Then 26 single reverts in the extension, each
  making its test fail (e.g. assignments sent whole, the file url sent,
  cookies on Kwil pushes, redirects followed, off-origin downloads (two
  guards: `download`'s and every request's; removing one alone is caught
  by the other), next links on any host, no retry, no page cap, unwanted files
  pushed, max_files ignored, one unreachable file stopping the run,
  pairing from any page, the key in the popup's status, no back-off,
  signed-out not marked, a site enabled without permission, a sync without
  permission, the pairing POST cross-origin, pairing on Canvas pages,
  batches over budget, a 401 "unauthorized" ending the run, unpair undone
  mid-run, a looser CSP, a cookies permission, module items renamed).
- **Not verified from the sandbox** (MANUAL_TEST/BLOCKERS): Canvas's file
  store hosts and CORS for extension downloads (inst-fs on
  `*.inscloudgate.net` is in host_permissions; an S3-served file would be
  skipped, not stop the run), that Chrome sends the Canvas session with a
  service worker's credentialed fetch to a host it has permission for,
  and the `while(1);` prefix (stripped when present).
- **Counts** (Linux sandbox, Python 3.13, Node 22, not real hardware):
  3390 passed, 37 skipped (baseline 61c409f from a `git archive` copy:
  3385 passed, 38 skipped, where `test_rebrand`'s git-history test skips;
  it runs here). JavaScript 205 passed (173 before).

### Round 2: the judge's findings (2026-10-28)

1. MED, "Enable on this site" injected the pairing script on every enabled
   origin, and a pairing from any enabled origin silently replaced the
   current one. The roles are split now: **Use as my school's Canvas**
   registers and injects only `canvas-seen.js`; a Kwil on its own domain
   needs **This is my Kwil**, whose second click answers "Send your Canvas
   courses to <host>?", and only `kwil-pair.js` runs there (a site is one
   or the other; the built-in hosts can't be enabled as the other kind).
   The pairing script asks the worker first (`kwil-may-pair`); a pairing
   from a Kwil other than the paired one is refused, the code left unused,
   until the popup's **Switch to <host>** (shown on that Kwil's page,
   naming both hosts) allows it for 10 minutes and pokes the page's
   script. A visited Canvas no longer replaces a pinned, signed-in one.
   Tests: a Canvas site never gets `kwil-pair.js` (registered or injected)
   and can't pair; a Kwil site gets only `kwil-pair.js`, after the
   permission; re-pairing from another Kwil is refused until the popup's
   switch, which expires; the script leaves a refused code unused until
   poked by this extension; an enabled Canvas site can't take over the
   pinned one; the popup's two-step "This is my Kwil" and Switch.
2. MED, the service worker could be stopped mid-run (no chrome.* call
   while reading), and every read came before the first push. A run now
   writes `status.running`/`lastRun` before its first read, calls
   `chrome.runtime.getPlatformInfo()` every 20 s (cleared at the end) and
   notes progress in storage after each course and push; it pushes a
   manifest whenever a batch fills (with that batch's announcements),
   then that batch's wanted files. Tests: keepalive (timer, call, clear,
   running written at start, progress before the first push); courses
   pushed before the last one is read; a rate limit part-way still pushes
   the courses read in full. MANUAL_TEST: a many-course sync with DevTools
   closed.
3. LOW, the needs-permission message pointed at a hidden button. On the
   paired Kwil or the Canvas without its permission the popup shows
   **Allow on this site** (custom domains) or where Chrome's Site access
   setting is (heykwil.com, instructure.com); the message says to open
   that site and click the icon. Test for both and for a granted site.
4. LOW, `storage.local.setAccessLevel` only works on `storage.session`.
   The call is gone; background.js and SECURITY.md now say plainly that
   Chrome lets the extension's own content scripts read `storage.local`
   (ours never do; page scripts can't), as a deliberate non-fix
   (`storage.session` would unpair at every browser restart).
5. LOW, tests for: Kwil's 401 dropping the key through `onAlarm`; the
   instructure/Canvas-origin refusal; `canvasSeen` ignoring the paired
   Kwil's origin; pairing without `sender.tab`; `sender.origin` over the
   frame URL; the request-level and `download` off-origin checks each on
   its own (a spy shows `download` refuses before any request).
6. LOW, redirects: `getJSON` now refuses an answer whose final URL left
   the Canvas origin (`off_origin`; a run reading courses that way pushes
   nothing; test). SECURITY says plainly that the browser follows Canvas's
   redirects for file downloads (to the file store, no key), and that
   off-origin API answers are errors.

Failing first: 30 round-2 reverts, each failing its test (the
keepalive-clear revert hangs the run), and the judge's 20-mutation script
now fails a test for every one (its `credentials: "omit"` mutation had hit
a comment in kwil.js; the comment no longer matches). Counts (Linux
sandbox, Python 3.13, Node 22, not real hardware): 3390 passed, 37
skipped; JavaScript 220 passed (205 before; the helper's files 47, was
32).

## Google sign-in status tells the truth: "expired, reconnect" (2026-10-28)

Live bug on heykwil.com: the owner's `GOOGLE_TOKEN_JSON` was revoked
(Calendar logged "could not be refreshed (invalid_grant)"), yet ⚙ still
said "Google account connected" and offered nothing to fix it:
`/api/google/status` only checked that a token existed. A family member
whose own token was revoked got the same.

- **Who must reconnect** (`google_auth.needs_reconnect`): the refresh
  wrapper every Google credential gets (`_warn_on_refresh_failure`) now
  records the account when Google refuses (a non-retryable
  `RefreshError`, e.g. `invalid_grant`), and when a saved token is
  unusable; a later successful refresh clears it. In memory only: who
  (`OWNER` = None, or the family member's id) and when, bounded
  (`MAX_RECONNECT` 1024, oldest first); no token, no Google message.
  `GoogleCalendar`/`GmailInbox`/`get_credentials` take `account=`;
  `family.py` passes the member's id (their tools and the student sync's
  Gmail). Connect (owner or member) and Disconnect clear it.
- **Status** (`google_web.py`): `needs_reconnect` (true only while a token
  is saved and Google refused it). No Google call per status request: the
  flag comes from the tools' own refreshes. The cloud owner's Connect also
  works with only the server's own address (`family.own_origin`, as family
  members already did), so the button isn't hidden for want of
  `PUBLIC_ORIGIN` when `APP_ORIGIN` is known.
- **App** (`i18n.js`): with `needs_reconnect` the Google row says "Google
  sign-in expired. Reconnect to keep using it." (Chinese too) and the
  button reads **Reconnect Google account** and runs the same connect
  flow; without a way to connect it says "Google sign-in expired.".
  `sw.js` cache `kwil-shell-v8`.
- **Doctor**: `python -m kwil --check` adds a "Google sign-in" line: one
  token refresh with a 10 s timeout (`google_auth.verify_sign_in`), "works"
  or "Google refused it (invalid_grant): the sign-in expired or was
  revoked, so sign in to Google again" (or "couldn't check with Google
  (<type>)"). Only the CLI passes `verify`; `run_checks` stays offline for
  every other caller and test.

Tests: `tests/test_google_reconnect.py` (10: per-account flag set and
cleared, retryable errors mark no one, bounded, INFO logs without token
or Google text; the owner's status on the server after a refused refresh
of `GOOGLE_TOKEN_JSON`, cleared by Connect and by Disconnect; Connect
without `PUBLIC_ORIGIN`; a member's status and Reconnect, another member
unaffected; `verify_sign_in` wording, timeout and offline; doctor on the
server and on a PC), a JS test for the expired row (English, Chinese, no
button, cleared), the CLI passing `verify`, and the status dicts in five
tests gained `needs_reconnect: false`. A conftest fixture clears the
process-wide flags around every test. Failing first: with `google_web.py`
and `family.py` reverted 4 of the new tests fail; with the wrapper's
mark/clear calls removed and `doctor.py` reverted 7 fail; with `cli.py`
reverted the CLI test fails; with `i18n.js`/`sw.js` reverted the JS
expired-row and cache-version tests fail.
Counts (Linux sandbox, Python 3.13, Node 22, not real hardware, `-k "not
live_pinecone"`): 3399 passed, 37 skipped (master: 3389 passed, 37
skipped); JavaScript 221 passed (220 before).

### Round 2: the judge's findings (2026-10-28)

1. HIGH, `failover/peers.py` `settings_from_config`: the
   `FAILOVER_BACKENDS` loop reused `role`, so the last entry's role
   overwrote this machine's: the cloud with `pi=...,pc=...` became "pc".
   That role fed `family.own_origin` (a member's Google redirect, and now
   the owner's fallback, went to the PC's address), the edge's role, the
   history sync's peers, the leader check (student sync, proactive jobs).
   Fixed with its own loop variable. Tests: `test_backends_from_the_environment`
   now checks `role == "cloud"` and the peers; the owner's and a member's
   redirect (and the owner's real consent URL) are `APP_ORIGIN/google/callback`
   with `FAILOVER_BACKENDS` set.
2. LOW, Disconnect wiped the owner's flag and fell back to a revoked
   `GOOGLE_TOKEN_JSON` shown as "connected". The flag is now kept per
   token source (`(account, "env"|"file")`); a connect or disconnect in
   the app clears only the file's, a refresh only its own token's, and the
   owner's status reads the flag of the token in use. Test: env token
   refused -> Reconnect -> Disconnect says Reconnect again.

Failing first: `peers.py` reverted fails both failover tests;
`google_web.py` and `google_auth.py` reverted fail the Disconnect test.
Counts: 3400 passed, 37 skipped (round 1: 3399); JavaScript 221 passed.
## Canvas: Kwil picks the way in by itself (2026-10-28)
Kyle's ask: "automate this selection process so there needs no input from
the user side": the School deadlines card picks the calendar feed, an
access token or the Canvas helper for the school and shows only that one
step.

- **The policy (`kwil/canvas_schools.py`).** Nothing public says whether a
  school lets students make a token (Canvas shows the two root-account
  settings to admins only), so per Canvas host it combines a sourced seed,
  `kwil/data/canvas_schools.json` (schema in `check_entry`: an `https`
  source and a `checked` date per entry; `*.instructure.com` only by exact
  host), with what this server learned in `<[student] dir>/canvas_policy.json`
  (`write_private`; counts and dates per host, no ids, at most 1000 hosts).
  "allowed": a token sync that worked, or the helper reading that a
  student-only account may make one; "blocked": the helper reading that
  it may not, or "My Canvas has no New access token button". A 401/403
  records nothing. A learned result beats the seed only when it is dated
  after the entry was checked. **The seed ships with no schools:** the
  school pages the research found couldn't be opened from the sandbox,
  and unverified policies must not ship; adding one is a data change.
- **The flow (`web/student.js`).** Find the school (the lookup now returns
  each school's `tokens`; a typed address asks the new
  `GET /api/student/canvas/policy?host=`), then `pickMethod`: allowed ->
  the token step (**Open my Canvas settings** opens `/profile/settings`
  from the tap, pick an expiry date, paste, Connect); blocked or unknown on
  desktop Chrome/Edge -> the helper; blocked on a phone -> the feed plus a
  pointer to the helper on a computer; unknown on a phone -> the feed,
  with **Get course files too** (the token step). The device comes from
  `userAgentData.mobile`, else the user agent, with iPadOS (a Mac UA with
  touch points) as a tablet. **Use the calendar feed instead** sits under
  the token and helper steps and holds through refreshes; "no New access
  token button" posts `POST /api/student/canvas/report` and falls back at
  once. Connected or helper paired: no connect step. "Use an access token
  instead" and the school-address field are gone (the chosen host is
  used).
- **The helper's reading.** Each run the extension GETs
  `/profile/settings` once (`CanvasReader.tokenPolicy`: same-origin,
  2 MB, no retry) and reads `"can_update_tokens"` / `"user_is_only_student"`
  from the page's ENV; any doubt is unknown and nothing is sent. The first
  manifest carries `canvas: {host, can_create_tokens, only_student}`;
  `clean_manifest` checks it strictly (`clean_canvas`, else a 400) and the
  push records it while still alive, plus the host in the person's own
  `canvas_helper.json` for the status (`policy`, `helper.canvas_host`).
  Kwil never makes a token.
- **`[canvas] tokens_for`** (`"everyone"`, the default and today's
  behaviour, or `"owner"`): with `"owner"` invited people get
  `tokens_offered: false`, the card never offers them the token step, and
  connecting with a token answers 403 `{code: "tokens_owner_only"}`; the
  owner always keeps it. Anything but `"everyone"` counts as `"owner"`.
- **Logs:** the host is never logged at any level (it names the person's
  school); ids, outcomes and counts only. `sw.js` cache `kwil-shell-v8`.

Tests: `tests/test_canvas_schools.py` (new: the shipped seed's schema and
a fixture seed, malformed entries refused, host/domain matching, newer
learned beats the seed and older never does, same-day ties, the 1000-host
bound, no ids in the file, `write_private` and a failed write keeping the
old file, a tampered file, no host in any log line);
`tests/test_student_web.py` (schools carry `tokens`, the policy route,
a working token sync records allowed and a 401 nothing, the report route:
400/429/411 and 404 when off, a report never beats a later-checked seed,
`tokens_for = "owner"`, no host in the logs); `tests/test_canvas_helper.py`
(the manifest's `canvas` recorded, ten wrong shapes refused, nothing after
a revoke, no host logged); the extension round trip now carries a
"blocked" reading through the real routes; `tests/js/student.test.mjs`
(one step per policy x six devices, device detection, the deep link opens
synchronously from the tap, report-and-fall-back, owner-only tokens, the
feed choice holding through refreshes, a late policy answer for an earlier
school ignored, Chinese for every new string);
`tests/js/canvas-helper.test.mjs` (the ENV parse on a fixture page of
Canvas's shape, one GET with `Accept: text/html`, unknown on 404 /
sign-in redirect / other origin / rate limit / network / no keys / out of
time, the first manifest only, every Canvas request a GET and none to the
token API).

Failing first: with the six source files back at 17b063b (and
`canvas_schools.py` removed), every new pytest test fails (22, plus
`test_canvas_schools.py` at import) and every new student.js test fails
(11 of 11 then); with only `sync.js` reverted, the manifest test fails;
two mutations (no feed-choice memory, the late answer written before the
school check) each fail their test.

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware): 3452
passed, 37 skipped, 1 failed: `test_live_pinecone_retrieve_read_only`, a
live test that hit the Pinecone organisation's monthly embedding quota
(429), failing the same way at the 17b063b baseline (3388 passed, 38
skipped from a `git archive` copy, where `test_rebrand`'s git-history test
skips). JavaScript 237 passed (220 before).

### Round 2: the judge's findings (2026-10-28)
1. MED, the school host in uvicorn's access log (both servers run with
   `log_config=None`, so `?host=` and the lookup's `?q=` reached the log
   file and Render at INFO with the client address).
   `config.AccessQueryFilter` cuts the query of every `/api/student/*`
   path to `?…`; `setup_logging` installs it, and so do `webapp.serve` and
   `cloud.serve` before `uvicorn.run`. Test: real uvicorn (h11) on a free
   port, reading its own access records for both routes (other paths
   keep their query). The routes stay GET (the filter covers both).
2. MED, one working token pinned a school to "allowed". Only the first
   working sync of a token connected in the app counts (per link; the
   background's syncs never, a disconnect forgets it); same-day outcomes
   are compared on that day's counts (`day_allowed` / `day_blocked`), a
   tie is "blocked". Each person's claim counts once: a report once per
   person and host, the helper's reading when it is new or changed for the
   pairing. Test shaped like p2.py at both levels (thirty "allowed" then a
   report: blocked, and one more "allowed" that day doesn't flip it).
3. LOW, a host the helper takes but the server doesn't made the manifest
   400. `clean_canvas` now drops the reading (the courses still count)
   for a host string that doesn't normalize, normalizes the rest (case,
   trailing dot, an https URL), and keeps the 400 for wrong shapes and
   types; the helper sends a reading only for a host passing the same rule
   (`policyHost` in canvas.js). Tests on both sides (`.internal`, an IP,
   a punycode TLD, `localhost`, `.local`).
4. LOW, members could probe which schools others use. A learned result is
   revealed only once it rests on `MIN_EVENTS` (2) events, or for the
   asker's own Canvas host (their link's or their helper's), in the policy
   route, the lookup's `tokens` and the report's answer. Test with two
   members.

Failing first: each fix's source files back at 0d6b571 with the new tests:
1 (config/webapp/cloud) 2 of 2 fail; canvas_schools.py 5 fail;
student_web.py 3 fail; canvas_helper.py 9 fail; sync.js the helper's
host test fails. All pass restored.

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware), with
`-k "not live_pinecone"` (the Pinecone quota is used up): 3464 passed, 37
skipped, 1 deselected (round 1: 3452 passed); JavaScript 238 passed
(round 1: 237).

## Settings: the Connections list redesign (2026-10-28)
Kyle: "fix the UI for the dropdown menu with all the connectors... right
now its way too clunky." Each service was its own stacked card with its own
explanation and forms (Google buttons, the owner's "Connected services",
Notion's buttons, Spotify's button, the "School deadlines" card, the API
key section). Now, following the approved design direction
(`.claude/DESIGN_SYSTEM.md`, new):
- **One grouped list, "Connections".** One 52px row per service (Google,
  Notion, Canvas, Home Assistant, Spotify, an invited person's Anthropic
  key): the service's real icon (Simple Icons, CC0, vendored in
  `web/static/icons/` with a README and drawn from an inline `<symbol>`
  sprite; white on a brand-colour tile), the name, a subtitle (their
  workspace or school, else what it is), and on the right a status dot and
  word (`.state.on` "Connected", `.state.warn` "Sign-in expired" / "Key
  needed", `.state.off`) or the plain action "Connect", plus a chevron.
  Connected rows first (needing attention first), then the rest; services
  not enabled for that person have no row.
- **A bottom sheet per service.** Tapping a row opens a modal `<dialog>`
  (grab handle, 44px icon, title, Close, one-paragraph explanation, then
  that service's own controls and Disconnect in red text). One at a time;
  Close, Esc, a tap outside and closing the menu shut it, focus returns to
  the row; Esc no longer closes the whole menu while a sheet is open
  (`ui.js`).
- **Shared component** `web/connections.js` (new; static route, `sw.js`
  SHELL, `cloud.PUBLIC_GET`): each service script keeps its own controls
  and ids and just reports `set(id, {state, status, action, detail, sub,
  alert})` (`i18n.js` Google, `notion.js`, `student.js`, `spotify.js`,
  `family.js` key); `connect.js` (the owner's Notion and Home Assistant)
  now fills those rows' sheets instead of its own "Connected services"
  section (`#connector-list` and `#connectors-status` are gone; a message
  goes in that service's sheet). The Google `needs_reconnect` and Canvas
  step-picking work in flight drop in as a `set()` status and action.
- **Design primitives** in `app.css`: type, spacing and row-height tokens
  (`--t-*`, `--s1`..`--s6`, `--row-h`), `.btn.primary/.secondary/.plain/
  .destructive`, `.group`, `.group-head`, `.state`. `sw.js` cache
  `kwil-shell-v8`.
- Every id, route and hook kept: `google-connect/-disconnect/-status`,
  `notion-*`, `spotify-*`, `#student` (and `data-kwil-pair`), `api-key-*`.
  The guide's Google step and `ha_reconnect` now point at "Connections".

Tested: `tests/js/connections.test.mjs` (new, 12: the page's rows parsed
from index.html, grouping and order, the right side's status or action,
the subtitle as text, one sheet at a time with showModal, Close, backdrop,
Esc, menu close, focus kept when a row moves, a built row for an unknown
service); `connect.test.mjs` rewritten for rows (16, was 13); a Connections-row
test each in `notion`, `spotify`, `student`, `family`, `i18n` (plus the
guide step) and `ui` (Esc); `tests/test_connection_icons.py` (new, 3:
every row has an icon file drawn by the sprite, every file in the README
with the pinned SHA and CC0, nothing fetched); `test_theme.py`: each white
mark 3:1 on its tile, status and row text 4.5:1 in both modes. Each source
change was reverted on its own and its test failed (18 reverts). A
Playwright run against the real page with fake API answers (no server, no
keys) checked grouping, 52px rows, icons drawing, the modal sheet, Esc and
backdrop closing only the sheet, keyboard order, the Google connect POST,
and the Chinese page; before/after screenshots at 390x844 and desktop in
light and dark, as the owner and as an invited beta tester.
Tests updated because the markup legitimately changed: `connect.test.mjs`
(rows instead of `#connector-list`), `test_owner_connect.py` (page ids,
script order), `test_webapp.py` (public list), the `kwil-shell-v8` pins.
Counts (Linux sandbox, Python 3.13, Node 22, not real hardware): 3393
passed, 37 skipped (master 4c8775d: 3388 passed, 38 skipped from a git
archive, which skips one git test); JavaScript 242 passed (220 before).

### Round 2: master merged, the expired Google row, the design review (2026-10-28)
- **Merged master** (b4fc523: the Canvas auto-method picker, #74, and
  Google's "sign-in expired", #76), keeping both sides; `sw.js` cache
  `kwil-shell-v10` (master was at v9) and its pins.
- **Google `needs_reconnect`** now drives the row: an attention row at the
  top, a red "Sign-in expired" (Chinese "登录已过期"), the action
  "Reconnect" (none when the server can't connect), and the sheet's
  **Reconnect Google account**. Test in `i18n.test.mjs` (fails with the
  branch removed).
- **The Canvas picker in its sheet:** the step Kwil picked (token, helper or
  feed) is a white card (`.conn-step`); its one way forward is
  `.btn.primary` (Connect Canvas, Connect with token, Set up Canvas helper),
  the first step is `.btn.secondary` (Find my school, Open my Canvas
  calendar/settings, Paste), the alternatives are `.btn.plain` (Get course
  files too, My Canvas has no New access token button, Use the calendar
  feed instead). Classes only: every id, text and behaviour unchanged; all
  `student.test.mjs` tests pass.
- **The designer's review:** `.group` and the cards in sheets are a tone
  step with no shadow (only the sheet keeps `--shadow-2`); the sheet slides
  in over `--t-sheet` 240ms on `--ease-sheet` `cubic-bezier(.32,.72,0,1)`
  over a `--scrim` fade of `--t-scrim` 200ms; a pressed row turns
  `--surface-2`, buttons keep the .97 press; a reduced-motion path;
  `:focus-visible` rings from `--ring` / `--ring-inset`; no raw hex or rgb()
  in the primitives or the component (`--brand-*`, `--on-brand`, `--scrim`
  tokens). `test_theme.py` checks all of it (it fails on the round-1 CSS).
  DESIGN_SYSTEM.md gained depth, motion and focus.
- Screenshots retaken: the list, the Google expired row and sheet, and the
  Canvas sheet in each picker state (token on a phone, helper in desktop
  Chrome, feed with "Get course files too", feed pointing at the helper on
  a computer), light and dark; every button in those sheets is 44px+.

Counts (Linux sandbox, not real hardware, `-k "not live_pinecone"`): 3480
passed, 37 skipped; JavaScript 263 passed (master b4fc523: 239). After
merging master again (641f1a5): 3499 passed, 37 skipped; JavaScript 263.

### Round 3: the judge's findings (2026-10-28)
1. MED, a row changing group while its sheet was open broke the modal:
   `arrange()` re-appended every row, and moving an open `<dialog>` takes
   it out of the top layer (still open, no backdrop, focus trap or Esc).
   That happened on every in-sheet change (Disconnect, saving the key,
   Canvas connect, the helper's pairing poll) and whenever another row's
   status arrived. Now `arrange()` waits while any sheet is open and runs
   when it closes (the row's status line still updates at once), and moves
   only the rows that are out of place (`insertBefore`). Tests: the fake DOM
   clears `modal` when an open dialog is moved and counts moves; an
   in-sheet disconnect keeps the sheet modal with nothing moved, then
   regroups on close with focus on the row; another row's status arriving
   while a sheet is open moves nothing; only out-of-place rows move. All
   three fail on the old `connections.js`. In real Chromium the judge's
   probes now show the sheet still `:modal` after Disconnect and after
   another row's status, and the row regrouped after Esc.
2. LOW, the env-managed owner's Canvas row said "Connect" with no way to
   connect in its sheet: `canvasRow` now says "Set on the server" with no
   action, as `connect.js` does for Notion. Tests: the row cases, and
   "Connect" exactly when the sheet shows the find step (both fail on the
   old `student.js`).
3. LOW, wording: PRIVACY ("Settings → Connections", "⚙ → Connections →
   Google"), the dev-team console line in `cli.py`, BLOCKERS, and the
   MANUAL_TEST cloud section, which now says what the rows and sheets
   show. (`cli.py`'s "Connected services off: a separate instance" log
   line stays: `test_config_cli.py` pins it and it names the feature.)
4. iOS: `::backdrop` gets `var(--scrim, <the light scrim>)` and a literal
   200ms (it inherits no custom properties before Safari 17.4); while a
   sheet is open `connections.js` sets `conn-sheet-open` on `<html>`, which
   stops the page and the menu behind it scrolling (CSS, no inline style).
   `test_theme.py` allows only that one fallback literal and checks the
   lock rule; the fake DOM test and a Playwright run check the class and
   the computed `overflow: hidden`. MANUAL_TEST: the scroll check and the
   scrim on iOS 17.3 or older.

Counts (Linux sandbox, not real hardware, `-k "not live_pinecone"`): 3499
passed, 37 skipped (Python test count unchanged: the round's checks went
into existing tests); JavaScript 266 passed (263 before).
## Type while Kwil is thinking: queued messages and Stop (2026-10-28)
Kyle's ask: "let me type to the assistant when its thinking."

Before: `setBusy` disabled the text box (and the mic) for the whole turn,
the submit handler dropped a Send while busy, and the server answers a
second `/api/turn` with 409 ("still working on the last request"). There
was no way to stop a page turn (only the voice view's barge-in).

- **Typing (`app.js`).** The text box is never disabled or cleared
  mid-turn; the mic keeps today's rule (off while a turn runs, on for a
  yes/no answer).
- **Queue.** Send while a reply runs puts the message in a "Queued"
  bubble with an × (`#queue`, under the chat, `aria-live="polite"`), at
  most 3 (a 4th gets "You can queue up to 3 messages." and stays in the
  box). Kept in the page's memory only, cleared on sign-out. After each
  turn's end (`send`'s and the resume path's `finally`) the next one goes
  as a normal turn, one at a time, through the same path as a typed
  message (picked files still go with the next message sent). Nothing goes
  while a yes/no question is open, or once the page shows the sign-in.
- **Stop.** A Stop button next to Send, shown while a turn runs (not
  during a question), posts `/api/stop`. The server (`WebAssistant.stop`)
  sets the page turn's new cancel event (`Session.cancel`, set per
  `start_turn`, cleared before the lock is released) and closes the open
  stream (`ClaudeChat.abort`): the same barge-in path as the voice view, so
  no tool runs after it and history stays valid; a tool already running
  finishes first. The turn's stream ends with its reply marked
  `"stopped": true` (shown, not spoken, with a "Stopped." note) and
  "done", and the queue goes. Stop is refused (`{"stopped": false}`) with
  no page turn running, during a question, and for a voice-view turn. If
  the stop request fails (e.g. an older backend), the reply runs to its
  end and the queue goes then; the server never runs two turns.
- **Questions.** Typed text while asked answers the question as before.
  Once the answer is sent, the page drops the question at once (it used to
  stay "confirming" until the reply), so text typed during the rest of
  that turn is queued instead of going to `/api/confirm` (409); a failed
  answer brings the question back.
- Strings in `i18n.js` (English and Chinese); `sw.js` cache `kwil-shell-v11`.

Tests: `tests/js/app-queue.test.mjs` (8: typing stays enabled and Send
queues, order one at a time, × removes, the limit, Stop then the queue, a
question takes the typed answer and the queue waits, a failed answer, the
page markup), an i18n test, and in `tests/test_webapp.py` three Stop tests
(the turn ends after the running tool with no further API call and the
next turn runs on valid history, caplog: one INFO line, no conversation
text; refused when idle, during a question and for a voice-view turn).
Failing first: with the source files back at 8ae6c41, 7 of the 8 queue
tests, the i18n test, the sw pin and all 3 Stop tests fail (the "failed
answer" test passes there too: it guards today's behaviour).

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware), with
`-k "not live_pinecone"`: 3497 passed, 37 skipped, 1 deselected (master
8ae6c41 extracted with `git archive`: 3492 passed, 38 skipped, and
`test_design_skills` failing only because an extract isn't a checkout;
3 new tests); JavaScript 248 passed (master: 239).

### Round 2: the judge's findings (2026-10-28)
1. MED, a Stop landing while the yes/no question was being built (a
   `confirm_prompt` that fetches first, e.g. the calendar's delete) still
   asked it, and a "yes" ran the tool. `_confirm` now asks nothing in a
   stopped turn (the answer is no), and `stop()` and `_confirm()` decide
   under one per-session lock (`Session.stop_lock`): Stop either finds the
   question open (refused) or lands before it is asked. Test: Stop while
   a gated `confirm_prompt` runs gives no "confirm" event, a later yes is
   a 409 and nothing is deleted.
2. LOW, Stop after a tool round showed the first round's words twice
   (the stopped reply carries text that was already streamed). A stopped
   reply now only removes an empty bubble and never adds one. Test.
3. LOW, an answer to an expired question (409 "nothing to confirm")
   brought the dead question back and blocked the queue. The question
   comes back only for other failures (network, 429 speech limit, 5xx),
   and `refreshState` sends the queue when the server says nothing is
   running or asked. Tests for the 409, the 429 and coming back to the app.

Failing first (source files back at 6f2b27c): the new webapp test fails
(it gets "confirm"); the new JS tests for 2, 3 (409) and the return to
the app fail; the 429 test passes there too (it guards the kept
behaviour). Counts: 3498 passed, 37 skipped, 1 deselected; JavaScript
252 passed.

## Typing into an old conversation continues it (PR 3a, 2026-10-29)
Kyle's decision: "typing into an old conversation should continue it."

Before: History's **Continue** posted `/api/conversations/{id}/continue`,
which ended the current conversation and loaded the stored one into the
browser's chat. But `/api/turn` knew nothing of it: once the 5-minute idle
timeout passed (or the reaper or a voice call ended it), the next typed
message quietly started a new conversation while the page still showed
the old one.

- **Server (`webapp.py`).** `/api/turn` takes an optional
  `conversation_id` (JSON field, or form field for audio and attachment
  turns). It is checked before anything else runs: malformed 400, history
  off 404, incognito 409 (`/continue`'s wording), not in this person's
  store 404 (another owner's, or the owner's for a family member: the
  history routes' ownership 404). `start_turn(..., resume=cid)`: a bound
  turn never takes the idle-timeout end; when the id is the chat's current
  conversation the turn just goes on, otherwise `ClaudeChat.resume` runs in
  the turn thread under `s.busy` (its summary of the current conversation
  is a Claude call). A conversation deleted after the route's check gives
  an error event (`"gone": true`) and nothing is recorded or recreated.
  Every `done` event, the quota
  refusal's included, carries `"conversation"`. Resuming keeps the taint
  (`untrusted_context`), so `confirm_if_untrusted` tools ask. INFO logs:
  the id only. `/continue` stays for the voice view and older pages.
- **Page (`app.js`).** The chat pane has `{cid, epoch}`; turns typed into
  a bound pane carry `conversation_id` (JSON and FormData), `/api/confirm`
  never does. Queue items are `{text, el, cid}` and go into the pane they
  were typed in, even after a switch. A late delta, transcript or reply
  from a turn sent in another pane is not drawn (questions, errors and
  `done` always are), and typed text answers a yes/no question only in the
  pane it was asked in (else it is queued; the buttons still answer). A 404
  or a `gone` error unbinds the pane and drops its queued messages.
  `window.KwilApp` exposes `bindPane`, `forgetConversation`, `pane` and
  `request`.
- **History (`history.js`).** Continue is a read-only GET: it renders the
  last messages into the chat and binds the pane. Deleting the pane's
  conversation unbinds it and drops its queue; turning Incognito on
  unbinds it. **Voice (`voice.js`).** With a bound pane that isn't the
  server's current conversation (`/api/state`), `start()` posts
  `/continue` after `getUserMedia` and before the socket; a 409 shows
  "Still working on the last request."
- New server sentence `conversation_gone` (English and Chinese); page
  strings in `i18n.js`; `sw.js` cache `kwil-shell-v13`.

Tests: `tests/test_history_web.py` +14 after round 2 (a bound turn continues the
conversation, no second one; after the idle timeout the same id; after
`end_conversation`, the reaper and a voice call's end it is resumed; a
resumed turn is tainted so a `confirm_if_untrusted` tool asks; an unknown,
foreign or malformed id 404/404/400 with nothing sent or saved, JSON and
form; incognito 409; history off 404; a deleted
conversation is not recreated, by the route and by the turn thread; `done`
carries the id; caplog: INFO has the id
and no text), `tests/test_family.py` +1 (a family member's turn into the
owner's conversation and the reverse are 404), `tests/test_contract.py`
+1 (contract fakes: a stored conversation with a tool round, continued
after the idle timeout in memory and resumed in another browser after it
ended), `tests/test_quotas.py` (the refusal's `done` carries the id),
`tests/js/app-resume.test.mjs` (9: body and FormData carry the id, an
answer never does, a queued item keeps its cid across a pane switch, text
typed in another pane during a question is queued, a late delta stays out
of the new pane, a 404/`gone` unbinds and drops the queue, history.js's
Continue is a GET that binds, delete and incognito unbind) and one i18n
test. Failing first: with `webapp.py` and `i18n.py` back at 5775b41 all
19 new or changed Python tests fail; with `app.js` and `history.js` back
all 9 JS tests fail, and with the epoch, queue-cid and FormData lines
mutated 6 of them still fail; the i18n test fails with `i18n.js` back. A
resume that dropped the taint fails the taint test and the three
"ended" variants.

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware), with
`-k "not live_pinecone"`: 3523 passed, 39 skipped, 1 deselected (master
5775b41 extracted with `git archive`: 3503 passed, 38 skipped, and
`test_design_skills` failing only because an extract isn't a checkout);
the two live runs of the new contract test skip without a key.
JavaScript 290 passed (master: 280).

### Round 2: the judge's findings (2026-10-29)
1. MED, a queued message and its reply were drawn in the pane being
   looked at, not the one it was typed in (`sendQueued` passed the cid but
   not the pane). Queue items now keep their `epoch` too, and `send()`
   uses it for the turn: the "me" bubble (and the files summary bubble)
   and the reply are drawn only while that pane is shown. Test: queued in
   the lab report, sent after Continue on another conversation, nothing
   lands in the new pane; one queued in the shown pane is drawn as usual.
2. MED, "goodbye" in a bound pane didn't unbind it, so the next message
   resumed the ended conversation again (tainted, a second memory
   summary). A reply with `ended: true`, or a `done` with `conversation:
   null`, after a bound turn shown in its pane now unbinds it (the log
   stays; messages queued for it go into a new conversation). An unbound
   pane still never binds to `done`'s id, so a fresh chat keeps the
   5-minute rule. Tests: goodbye then the next and the queued turn carry
   no `conversation_id`; a `done` with null unbinds; a fresh chat's
   `done` id is not adopted. README sentence.
3. LOW, the server's `"new": true` flag had no client. Removed from
   `/api/turn` and `start_turn` with its two tests and the docs (it comes
   back with the desktop "New chat" in 3b).

Failing first: with `app.js` at 0114a71 the 3 new JS tests fail and the
judge's probe shows "second in the lab report" and "Lab answer two." in
the other pane and `conversation_id` on the turn after goodbye; with the
fix both are gone. Counts: 3521 passed, 39 skipped, 1 deselected (the two
`new` tests removed); JavaScript 293 passed. Judge round 3 (LOW, fixed by the master): an ended conversation frees its queued messages even when its pane is no longer shown; JavaScript 294 passed.

## The chat's clock is Kyle's time zone, not the server's (PR 1, 2026-10-29)
Live bug: on Render (UTC) Kyle asked at about 11:30 EDT to schedule physics
in his free time. Kwil blocked only 3:30-4 PM and later said "it's already
after seven" at 3 PM. The owner's `ClaudeChat` was built without
`timezone=` (`cli.build_chat`, and its copy in `webapp.build_assistant`'s
`chat_factory`, the cloud's path); only family members got one. So the
turn's "now" was the server's UTC clock while the calendar tools used
`[assistant] timezone`, and the model mixed the two.

- **One builder.** `cli.build_chat` passes `[assistant] timezone` (None
  when empty: this machine's zone, as before) and takes `client=`;
  `chat_factory` now calls it with the shared client, so the PC and cloud
  chats can't drift again.
- **Turn context.** Still starts "Current local date and time:", now with
  the IANA name and a pointer: "Thursday, October 29, 2026, 11:30 AM (EDT,
  America/New_York, UTC-0400). Calendar times are in this zone." A pinned
  `TURN_NOW` is shown in the chat's zone. Nothing parses this line (only
  `tests/conftest.py` matches its prefix).
- **Other "today"s.** The memory summary's date stamp uses the chat's zone;
  lecture notes are dated in `[assistant] timezone` (the phone sends
  `started_at` in UTC, so a 9 PM class in New York was dated the next day;
  `webfeatures` now passes the zone to `LecturePipeline`).
- **`find_free_time`** (its maths was already right) adds
  `"searched_from"`, the spoken local time the search began (e.g.
  "Thursday, October 29, 11:30 AM"), so the tool also anchors "now".
- **Startup warning.** On the server profile with no time zone,
  `build_assistant` logs one WARNING (no personal data) and keeps today's
  behaviour; `--check`'s "Time zone" line says the time itself would be in
  UTC too.

Tests: `tests/test_chat.py` +2 (through `cli.build_chat(cfg, client=fake)`
with `chat._now` faked as a UTC server clock that honours its zone: the
request has "11:30 AM (EDT, America/New_York, UTC-0400)" and no "03:30 PM";
the summary stamp at 02:00Z on Jan 2 is "2027-01-01"),
`tests/test_webapp.py` +2 (the same through `webapp.build_assistant` on the
server profile and its session's chat; no zone: exactly one WARNING),
`tests/test_calendar_features.py` +1 (TURN_NOW 15:30Z, events ending 2:15
PM and starting 4 PM EDT: one slot 2:15-4 PM and `searched_from` 11:30 AM),
`tests/test_lecture.py` +2 (01:00Z is dated the 29th in New York; the web
pipeline gets the configured zone). Failing first: with `kwil/` back at
788180f all 7 fail; with only `timezone=` dropped from `build_chat` both
chat-time tests still fail, and with only `chat.py` back the two context
tests and the summary test fail.

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware), with
`-k "not live_pinecone"`: 3528 passed, 39 skipped, 1 deselected (master
788180f extracted with `git archive`: 3519 passed, 40 skipped, and
`test_design_skills` failing only because an extract isn't a checkout).
JavaScript 294 passed (unchanged).

## Kwil keeps its reasoning across turns and plans carefully (PR 2, 2026-10-29)

Kyle's report: after Kwil planned his day, "why did you block only 3:30 to
4 for physics?" got "I can't see my reasoning from that earlier chat".
Earlier turns' thinking was dropped from every request (the fix for the
2026-10-20 signature 400s), and the history was trimmed by raw messages, so
three tool-heavy scheduling turns could push turn 1 out entirely.

- **Earlier thinking, while it is safe** (`kwil/chat.py`). Each assistant
  message holding thinking gets a private `_thinking_from` tag: this chat
  (a random id), its "thinking epoch" and the model. `request_messages`
  re-sends an earlier turn's thinking only when its tag matches the
  current one, and always strips the tag. Thinking from before a resume or
  a model change isn't sent again; later turns' thinking is kept. The
  epoch moves on when `history` is assigned (it is now a property:
  resume, `_switch_to`, `reset`), when the turn's model differs from the
  last one (router picks, the fast→main escalation, a changed `model`),
  and after a refused signature. A barge-in's rewritten reply loses its
  tag.
- **One retry.** A 400 that names a thinking `signature`, while earlier
  thinking was sent, is retried once without any (one WARNING, no text);
  a second refusal, another 400, or a 400 with nothing to strip raises as
  before.
- **Trim by whole turns.** `max_history` keeps its meaning in plain chat
  (messages, two a turn): past `max_history / 2` turns, the newest half of
  them is kept, each with all its tool rounds. A trim starts at one of
  Kyle's messages, so no tool_result is left without its tool_use. Plain
  conversations trim exactly as before (`test_trimming_in_chunks_keeps_the_prefix`
  unchanged). A turn is bounded by `max_tool_rounds`, so the history still is.
- **Room to plan.** New `[assistant] max_tokens_tools` (4000): the ceiling
  for main-model turns that offer tools (at least `max_tokens`).
  Fast-model and tool-less turns keep `max_tokens` (1500). Wired through
  `cli.build_chat` and the family chat factory.
- **Planning prompt.** `TOOLS_PROMPT` now says keep it short but reason
  carefully; a new `SCHEDULE_PROMPT` (only with `find_free_time`) says use
  `find_free_time` for gaps, exact edges, every free gap asked for, and say
  the window used ("from now, eleven thirty, to five"). Both are in
  `stable_system`, the same bytes every turn.
- **Thinking parameter.** Not added: `tests/contract_fakes.py` doesn't
  check a `thinking=` shape for this model family, so adaptive thinking
  still relies on the model's default.
- Nothing new is stored: history.sqlite still gets reply text and tool
  names only; thinking and tool results live in memory for the
  conversation. Taint (`has_untrusted_content`) reads the same blocks.

Tests: new `tests/test_chat_context.py` (15): turn 2 re-sends turn 1's
thinking (same chat and model) and the tag is never sent; none after a
history set from outside, a resume (and history.sqlite has no thinking or
tool result), a model change, or router escalation (the main model's own
turn 2 is kept, the fast model's turn 1 never); taint holds with kept
thinking; a barge-in's reply isn't re-sent; a refused signature is retried
exactly once without earlier thinking (one WARNING without conversation
text), a second refusal raises and undoes the turn, and other cases aren't
retried; three 6-round tool turns keep turn 1's tool_result (42 messages,
`max_history` 40); trims never orphan a tool_result; `cli.build_chat`
sends 1500 (fast), 4000 (main with tools), 1500 (main, no tools); the
schedule prompt is in a byte-identical system. `tests/test_contract.py`
+2: a two-turn-plus follow-up re-sends earlier thinking and the fake API
accepts it (also `live` with a key), and a re-keyed fake refuses it once,
then accepts the retry. Failing first: with `kwil/` and `config.toml` back
at a016a19, 12 of the 17 fail (the other 5 are guards that hold on the old
code because it never re-sent anything); with only the tag check loosened
(`tag is None`) 6 fail, and with only the barge-in untagging removed 1
fails.

Counts (Linux sandbox, Python 3.13, Node 22, not real hardware), with
`-k "not live_pinecone"`: 3545 passed, 41 skipped, 1 deselected (a016a19:
3528 passed, 39 skipped; +17 new tests, +2 skipped are the new contract
test's `live` runs without a key). JavaScript 294 passed (unchanged).

### Round 2 (judge F1-F4)

- **F1, size.** New `[assistant] max_history_chars` (300000, about 100k
  tokens; `chat.history_chars` counts each block as JSON and a photo or
  file as 8000): past it `_trim_history` drops the oldest whole turns until
  the rest is under half (the newest turn always stays; INFO logs the count
  only). New `[assistant] thinking_turns` (3): only the newest 3 to 5
  earlier turns re-send their thinking; the cut moves in steps of 3, so the
  cached prefix changes once every three turns, not every turn. A
  "prompt is too long" 400 drops the older half of the earlier turns (at
  least one) and all earlier thinking, and retries once (one WARNING,
  counts only); if that fails too, the turn is undone by finding its
  message by identity (the old index was wrong after the retry's trim).
- **F2.** A reply cut off by `max_tokens`, or one that is only thinking,
  isn't tagged, so its thinking is never sent again.
- **F3.** README, SECURITY and this entry now say: thinking from before a
  resume or a model change isn't sent again; later turns' thinking is
  kept. The shared-view example is gone (voice uses the session's chat).
- **F4.** Tests for model A→B→A and for the family chat factory's
  `max_tokens_tools` (and the two new keys).

Tests: `tests/test_chat_context.py` +10 (25): big tool results trim whole
turns under the budget and start with a user turn; the newest turn stays
over the budget; at turn 7 only turns 4-6 send their thinking (all 5 at
turn 6); a "prompt is too long" 400 retries once with one turn fewer and
the next turn goes through; a second one raises with exactly one retry and
leaves a valid history; a `max_tokens` cut and a thinking-only reply aren't
re-sent, a finished one is; A→B→A sends none of turn 1's thinking; a
family member's tool turn gets the configured `max_tokens_tools`. Failing
first: with `kwil/` and `config.toml` back at bfbc62d, 8 fail (the others
are guards). One mutation at a time on the new code: without the
model-change `_forget_thinking` A→B→A fails; without family's
`max_tokens_tools` the family test fails; rolling back by the old index
fails the second too-long test; tagging every thinking reply fails both F2
cases; no thinking floor, no size budget and no too-long retry each fail
their tests.

Counts after round 2 (Linux sandbox, not real hardware), `-k "not
live_pinecone"`: 3555 passed, 41 skipped, 1 deselected (bfbc62d: 3545 /
41); JavaScript 294 passed.

### Round 3 (judge R1)

- **R1.** A "prompt is too long" 400 caused by one oversize PDF used to cost
  the two oldest turns: the shortened retry dropped them and then failed
  anyway. Now `_run` snapshots the history and the thinking epoch before
  `_shorten`; if the shorter retry fails too, both are put back before the
  error is raised (so `ask()` undoes only the failed turn). A turn whose
  message carries a photo or file isn't shortened at all.
- SECURITY.md: thinking is re-sent for "at least" the last
  `thinking_turns` turns (the floor moves in steps).

Tests: `tests/test_chat_context.py` +2 (27): four turns, then an oversize
document turn raises after one request, all four turns stay and turn 5
re-sends their thinking; without a file, a failed shorter retry leaves all
four turns and their thinking eligible. The round-2 second-too-long test
now expects the earlier turns back (q0-q2). Failing first: with
`kwil/chat.py` back at dfeb817 all 3 fail; without only the attachment
check the PDF test fails; without only the restore the other two fail.
The judge's probe (`probe_pdf.py`) now prints the same four turns before
and after.

Counts after round 3 (Linux sandbox, not real hardware), `-k "not
live_pinecone"`: 3557 passed, 41 skipped, 1 deselected (dfeb817: 3555 /
41); JavaScript 294 passed.

## Kwil colors new events the way Kyle already colors them (PR 3, 2026-10-29)

Kyle's report: Kwil built his schedule without color-coding it, and an
earlier time it colored events with new colors instead of the labels he
already uses. No code creates labels or calendars (the only write is
`events().insert`/`patch`); the causes were that nothing told the model to
reuse past colors, and without the extra Google scope a labelled event
read as a bare "a label", which the model couldn't name or reuse.

- **`calendar_color_habits(days=14, calendar=None)`** (`kwil/tools/calendar.py`,
  read-only, in `tools.READ_ONLY_TOOLS` and `router.READ_ONLY_TOOLS`, no
  extra scope needed). One `events.list` of the past `days` (1-28) up to
  now, at most 250 events. Groups Kyle's own timed events by course code
  ("PHYS 361 Lecture" and "phys 361 lab" are both "PHYS 361") or by the
  tidied title ("Tennis", "tennis!"), and returns the color or label used
  most (a tie goes to the latest), its count and, if mixed, the total
  (`{"PHYS 361": {"label": "Physics", "count": 6, "of": 7}}`); uncolored
  events show as `"color": "default"`. At most 30 groups (`more_titles`
  counts the rest), names cut to 60 characters, plus the classic colors,
  the label names and `[calendar] color_labels` aliases. Invites, Gmail,
  guest-editable or taint-marked events and shared calendars are skipped
  (the `_outside` rule), and label names come only from Kyle's own
  calendars (round 2), so the result holds only Kyle's own text and
  doesn't taint; all-day and untitled events are skipped and counted.
  INFO logs counts only.
- **Labels that can't be read.** `_label_state` says whether labels were
  read (no extra scope, or `calendars.get` failed, means no). A labelled
  event then shows `"label_unreadable": true` (not "a label"), and
  `list_calendar_events` and the habits add `"labels_unavailable": true`
  with `labels_hint`: don't name or invent a label, plus how to grant the
  permission (`MissingScopes`' own text: the sign-in command on the PC,
  "reconnect Google" in the app).
- **Unknown names refused.** A color that matches no classic color, label
  or alias was already refused before any insert/patch; the error now
  reads "No color or event label by that name. Choices: ..." and, when
  labels can't be read, adds the hint.
- **Prompt.** New `COLOR_PROMPT` in `stable_system` (only when
  `calendar_color_habits` is registered): call it first when creating
  schedule events, color each like Kyle's similar past events, use only
  existing colors and labels, and if unsure leave the default and say so.
- The label API shape (`labelProperties.eventLabels` from `calendars.get`,
  `eventLabelId` + `eventLabelVersion=1` on events) is unchanged. Nothing
  new is stored.

Tests: new `tests/test_calendar_colors.py` (14): a week of events (PHYS
361 under a Physics label, one under School, tennis in Sage, errands,
untitled, all-day, an invite, a cancelled one) gives the expected mapping,
skips and choices without the invite's title; a tie goes to the latest
use; days clamp and another calendar can be named; output bounds with 300
events; the list request builds against the real discovery document;
without the extra scope (and when Google fails) labels are flagged, never
named; in the app the hint is the reconnect text; an unlabelled listing
has no flag; an unknown label on create is a tool error with no insert,
and a label name without the scope is refused on create and update; the
tool is in both read-only lists; the prompt is in a byte-identical system
across turns and only with the tool; INFO logs no titles or colors.
Failing first: with the four source files back at 2317311 (constants
inlined in a scratch copy of the test so it imports), 13 of 14 fail (the
14th, "no flag without labelled events", is a guard); reverting only
`chat.py` fails the prompt test, only `router.py` or only
`tools/__init__.py` fails the read-only test. On the old code an unknown
color was already refused without an insert; that test fails there only
on the new message.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3571 passed, 41 skipped, 1 deselected (2317311: 3557 / 41; +14
new tests). `tests/test_docs.py` now lists `calendar_color_habits` as a
tool name. JavaScript 294 passed (unchanged).

### Round 2 (judge findings 1-3)

- **1 (high).** `calendar_color_habits(calendar=<a shared calendar>)`
  handed the model that calendar's label names, written by its owner,
  uncut and unmarked. Now label names are returned only for Kyle's own
  calendars or Google's public ones (`_owned`/`is_google_public`); a
  shared one gives `"labels": []` and `not_own_calendar: true` (its events
  were already all skipped). Every label name handed back (habits,
  `list_calendars`' choices, a listing's `label`, a refusal's choices) is
  cut to 60 characters (`MAX_CALENDAR_NAME`) and at most 50 are listed
  (`MAX_LABELS_SHOWN`). A refused color on another person's calendar names
  no labels, only "or one of that calendar's own event labels"
  (`_own_calendar` looks the id up in the cached calendar list; unknown
  counts as not Kyle's).
- **2 (low).** In lower or mixed case, a four-digit time of day (HHMM,
  00:00-23:59) or an everyday word (Room, Call, Lunch, Bus, Gym, Mom, ...)
  isn't a course code: "Lunch 1230", "Call Mom 2100", "Room 1234 meeting"
  and "Bus 101" stay whole titles. "phys 361 lab" is still PHYS 361; in
  capitals ("SAT 1500") a code counts.
- **3 (low).** The tool's description has no pronoun ("The colors already
  used on Kyle's own past events ... the same way"), so a family member's
  copy reads "Mom's own past events".
- SECURITY.md now says which label names are returned, the caps, and the
  accepted risk (Kyle's own label names are his text).

Tests: `tests/test_calendar_colors.py` +14 (28): a writer-access calendar
with a 1,240-character injection label gives no label name, no
`untrusted_content`, and a refusal that doesn't quote it, with no insert;
on Kyle's own calendar a 1,240-character label and 80 more come back as
60 characters plus "..." and 50 names (habits, listing, refusal); 11
titles for the course-code rule; the description has no pronoun after the
family swap. `tests/test_family_google.py`: Mom's copy of the description
reads "Mom's own past events" with no " his ". Failing first: with
`kwil/tools/calendar.py` back at 7c07bcc, 10 fail (the five titles that
must not become codes, both label tests, both description tests; the
course-code titles that already worked are guards). One mutation at a
time: without the own-calendar check in the refusal, or without dropping
a shared calendar's labels in the habits, the shared-calendar test fails.

Counts after round 2 (Linux sandbox, not real hardware), `-k "not
live_pinecone"`: 3585 passed, 41 skipped, 1 deselected (7c07bcc: 3571 /
41; +14 new tests); JavaScript 294 passed (unchanged).
## Memory layer 1: your instructions and things Kwil remembered, without Pinecone (2026-10-29)

Kyle's report: he told Kwil "color code my schedule every time", and in a
later conversation it didn't know. Long-term memory was Pinecone-only, the
Pinecone quota was used up, and the 429 was swallowed with a WARNING on
every turn. He asked for memory like ChatGPT's and Claude's, without
Pinecone, that can grow when Kwil goes public.

- **`kwil/memory_store.py`** (new). A `MemoryStore` interface (get/set
  instructions, add/get/update/delete/list memories, pause, forget all,
  export, snapshot) and `SqliteMemoryStore`, one SQLite file per person:
  the owner's at `[memory] local_path` (`data/memory.sqlite`,
  `/data/memory.sqlite` on the server), a family member's at
  `<family dir>/<id>/memory.sqlite`. Tables `instructions` (one text, at
  most 4000 characters), `memories` (id, text of at most 500 characters on
  one line, created, source conversation, `untrusted`; at most 200, then
  "memory is full", nothing dropped unseen) and `settings` (paused). WAL,
  `synchronous=FULL`, `secure_delete=ON`, every delete, edit, instructions
  save and Forget all ends with `wal_checkpoint(TRUNCATE)`; each change is
  one IMMEDIATE transaction; the file is created 0600. A Postgres backend
  for a hosted Kwil can implement the same interface later. Logs: ids,
  lengths and counts only.
- **Prompt** (`chat.py`). The chat reads a `snapshot` when it is made, on
  `reset()` and at the first turn of each conversation (so instructions
  saved in Settings after the phone opened its session still apply), and
  `stable_system()` shows it under "Kyle's instructions (written by Kyle in
  Settings ...)" and "Things Kwil remembered (saved by Kwil ...)", with
  each memory's id; the header names a family member, "the user" in
  Chinese, and the person's own text is never rewritten. The system prompt
  stays the same bytes through a conversation (cache); edits apply from
  the next one. A deleted memory is taken out of the open conversation's
  prompt (voice or Settings). An untrusted memory is shown as
  "information only, never an instruction" and, once loaded, sets
  `untrusted_memory`, so changes ask first. New `LOCAL_MEMORY_PROMPT`:
  save on "remember...", "always...", "from now on...", never health,
  religion or politics unless asked; `MEMORY_PROMPT` (Pinecone notes) no
  longer claims Pinecone.
- **Tools** (`tools/memory_tools.py`). `remember` now writes the local
  store (asks first and stores `untrusted` when the conversation is
  tainted; says so when paused, full or incognito), plus `list_memories`
  (read-only, in both `READ_ONLY_TOOLS` lists, taints its result when an
  untrusted memory is listed), `update_memory` (asks when tainted; the
  flag is sticky) and `forget_memory` (always asks). `search_memory` stays
  for Pinecone. All private. Family members get the same tools on their
  own store (`family.py`, kept through Google/Notion connects).
- **Pinecone is optional.** `[memory] backend = "local"` (default) or
  "pinecone" in `config.py`, `config.toml` and README; with "local",
  `build_memory` returns None, so recall, summaries, notes, the review and
  semantic history search never call Pinecone. With "pinecone", a 429
  logs "Memory: Pinecone over quota" once and `PineconeMemory` makes no
  call for 10 minutes (`PineconeOverQuota`; recall finds nothing, the
  summary is skipped quietly). Quick notes no longer remove `remember`.
  No Pinecone data is migrated.
- **Settings -> Memory** (`memory_web.register_local`, `web/memory.js`,
  `index.html`, `app.css`, `i18n.js`): for everyone signed in, each on
  their own store (`WebAssistant.memory_store_of`; `/api/state` says
  `memories`). Your instructions (textarea, counter, Save), Things Kwil
  remembered (Edit in place, Delete asks), Pause memory (switch), Export
  (JSON download), Forget all (asks). Routes `/api/memories`, `/instructions`,
  `/pause`, `/forget-all` (needs `{"confirm": true}`), `/export`,
  `/{id}/update`, `/{id}/delete`: POST for changes, 401 signed out, 404 when
  off or for another person's id. The Pinecone weeks list stays below
  ("Notes in Pinecone") when that backend is on. Chinese for every
  string; `sw.js` cache `kwil-shell-v14`.
- **Account.** "Download my data" adds `memory.json`; deleting the account
  closes the store and removes the file with the folder. A separate
  instance (`--env-file`) without its own `local_path` has memory off.

Tests: new `tests/test_memory_local.py` (25): bounds (200 / 500 / 4000);
delete, edit, instructions overwrite and Forget all leave none of the text
in the db or WAL bytes; the file is 0600; pause; export; store logs; config;
instructions apply in the next conversation, never mid-way, with a
byte-identical system across turns; a session opened before the save gets
it; Pinecone raising its real 429 (`PineconeApiException`): `remember`,
then a new chat still has the rule in `stable_system()` and one WARNING;
local backend never builds Pinecone; pause stops saving and loading; an
untrusted memory taints and a change asks; a trusted one doesn't; names
and Chinese headers; forget mid-conversation drops it from the next
request; a health check loads nothing; no text at INFO; a broken store
never blocks a reply; a separate instance; the Settings routes; 401/404;
Forget all needs the confirm and reaches the open session; family
isolation both ways (404); account export includes and delete removes the
file. `tests/test_memory_tools.py` rewritten for the local store (14, was 7),
`tests/test_contract.py` +1 (a "from now on..." turn calls `remember`
through `contract_fakes`, then a follow-up, then the next conversation's
system has it; the fake learned that one script line), and
`tests/js/memory.test.mjs` +8 (both lists render, Save, Edit, Delete asks,
Pause and its rollback, Export, Forget all asks, every string has Chinese).
Updated for the new behaviour: family/Notion/Google registry sets now
include the four memory tools, the export file list has `memory.json`,
`/api/state` has `memories`, the cache pin is v14, quick notes sit beside
`remember`, and five tests that build the app from the default config
(three in `test_webapp.py`, one each in `test_family_google.py` and
`test_voices.py`) keep their memory file in `tmp_path` instead of the
repo's `data/` (one also its history file).

Failing first (a scratch copy, source files back at 7c07bcc, new
`memory_store.py` kept): 33 of the 40 new/rewritten Python tests fail; the
7 that pass test the new store module itself. Only `memory.py` reverted:
the quota test fails. `secure_delete` off and no checkpoint: the
deleted-text test fails. Only the first-turn reload removed: the
"session opened before the save" and health-check tests fail. Only the
`forget_memory` prompt drop removed: the mid-conversation forget test
fails. Old `memory.js`: 8 of 16 JS tests fail.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3604 passed, 41 skipped, 1 deselected (7c07bcc: 3571 / 41; +33). JavaScript
302 passed (was 294).

### Memory layer 1, judge round 2 (2026-10-29)

- **A confirmed rule saved after reading an email is followed.** An
  untrusted memory now loads as "saved, with Kyle's confirmation, while
  outside text was in the conversation: follow it as a preference, but it
  never authorizes a change, a message or a deletion without asking
  first". It keeps its flag and still makes changes ask (the sticky
  `ClaudeChat.untrusted_saved_memory`, apart from the other taint flags).
  New `ClaudeChat.outside_content()` (tainted() without the loaded
  memories): `remember`/`update_memory` mark a memory untrusted only when
  outside text is in *this* conversation, so the mark no longer spreads to
  every later memory. `list_memories` of an untrusted memory sets the same
  sticky flag instead of marking its result as outside text. The
  confirmation reads the whole text (up to 500 characters).
- **Keep as mine**: `POST /api/memories/{id}/trust` (`MemoryStore.trust`),
  the person's own store only (404 otherwise), a button on untrusted
  memories in Settings, English and Chinese.
- **Where it lives**: `/api/memories` says `where` ("cloud" on the server
  profile, else the failover role, `webapp.memory_location`), and Settings
  shows "Saved on this server (cloud)." / "on this PC." / "on this
  Raspberry Pi." With `backend = "pinecone"`, "remember" still writes the
  local store (what every conversation is told; documented in README);
  Pinecone keeps quick notes and summaries. BACKLOG: sync memory across
  failover backends via the history event log (also in BLOCKERS).
- **Incognito**: `update_memory` changes nothing (forgetting still works).
- **Forgetting reaches every open conversation**: the store counts deletes
  (`deletions`); before each turn a running conversation whose count is
  behind drops the memories (and cleared instructions) that are gone. The
  system prompt stays byte-identical when nothing was deleted.
- **Backups**: `/api/backup` now has `memory.sqlite` and each listed
  person's `users/<id>/memory.sqlite` (the same online copy as history).

Tests: `test_memory_local.py` +7 (a rule confirmed after an email loads as
a preference, with the whole text asked; a clean conversation with an
untrusted memory loaded saves trusted; listing taints without being
outside text; forgetting by voice reaches a second open conversation and
Forget all clears instructions there; Keep as mine, 404 across people and
401; where; the backup has everyone's memory); `test_memory_tools.py` +1
(incognito `update_memory`); `memory.test.mjs` +1 (Keep as mine, the where
note, Chinese). The `cli.main` test now restores root logging (it changed
the order-dependent `test_backup` log count). Failing first, with one file
at a time back at 8e9af6b in a scratch copy: `chat.py` fails 5,
`tools/memory_tools.py` 4, `memory_web.py` + `memory_store.py` 3,
`backup.py` + `webapp.py` 2, `memory.js` 2 JS tests.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3626 passed, 41 skipped, 1 deselected (8e9af6b: 3618 / 41; +8).
JavaScript 303 passed (was 302).

### Memory layer 1, judge round 3 (2026-10-29)
- MED, fixed by the master: trimming history (by turn count, by the size
  budget, or `_shorten` after "prompt is too long") dropped the turn that
  held untrusted tool output, so the conversation looked clean again and a
  memory saved next was trusted (and changes stopped asking). `_trim_history`
  and `_drop_oldest_turns` now call `_keep_taint` on what they drop, as
  `_rewind_turn` already did: untrusted text read in a conversation keeps it
  tainted until `reset()`.
- Test: `test_trimming_the_email_turn_keeps_the_conversation_tainted`
  (count and size); both fail without the fix.

## Memory layer 2: search and read past chats, without Pinecone (2026-10-29)

"What did we decide about my physics schedule last week?" now works with
the default `[memory] backend = "local"`.

- **`kwil/recall.py`**: a `Retriever` interface (`index`, `search`,
  `delete`, plus `index_soon`), attached per store (`store.recall`,
  `recall.attach` in `cli.add_history_tools` and `family._build`).
  - `FtsRetriever` wraps `HistoryStore.search` (FTS5 over titles,
    summaries and messages). Ranking: a conversation whose title or
    summary matches now always comes before one that only mentions the
    words in a message (`_rank`'s `topical` tier, FTS and LIKE alike),
    then bm25 (titles 4.0, summaries 1.5, plus the best message), then the
    newest. Hits are conversation-level: title, date, summary and the best
    snippet (24 words for `search_conversations`; the phone's History
    search keeps 12: `snippet_tokens`, judge round 2).
  - `HybridRetriever` (`[memory] embeddings = "fastembed"`, off by
    default): one unit float32 vector per conversation (title + summary,
    or the first thing said before there is a summary) in the person's own
    history file, table `recall_vectors` (created IF NOT EXISTS like the
    FTS tables, so a rollback to an older Kwil still opens the file; ON
    DELETE CASCADE, so every delete path removes the vector). Brute-force
    cosine with numpy (at most `MAX_RECALL_SCAN` = 20000 vectors), min
    score 0.5, fused with the keyword hits by reciprocal rank fusion
    (k = 60). Vector-only hits are `match = "semantic"` with the summary as
    snippet. Pluggable `Embedder`; `FastEmbedder` imports fastembed lazily
    and opens the model with `local_files_only` (download once with
    `python -m kwil.recall --download`). A failing or missing embedder
    logs one warning (type name only) and search stays keyword-only.
  - Backfill: at startup, after a conversation's title and summary are
    written (`Recorder._title` -> `index_soon`), and on a search that finds
    vectors missing; on a background thread, 16 per batch, 500 per run,
    each batch saved as it is made, so it resumes after a crash or restart.
    A vector is stale when the title or summary changed (`title_order |
    summary_order`). Logs counts only.
  - The module docstring says how to add a Postgres + pgvector Retriever.
- **Wiring**: `history.search_conversations` uses the store's Retriever
  when there is no Pinecone memory (backend local); the Pinecone path is
  unchanged for `backend = "pinecone"`. `delete_conversation` also tells
  the Retriever (first, as with Pinecone).
- **`read_conversation(conversation_id, query="")`** (tools/history.py):
  read-only, never switches the conversation. With a query, the messages
  around the best matches (each window centered on its match, +/- 3), else
  the summary and the last messages; at most 20 messages of 500
  characters (long ones clipped around the match). Taints like a resume
  (`chat.HISTORY_TOOLS`, and `untrusted_output=True`), `private=True`. In
  both read-only lists (`tools.READ_ONLY_TOOLS`, `router.READ_ONLY_TOOLS`).
  Only the store it was built with.
- **`search_conversations`** results: id, title, when, date (in
  `[assistant] timezone`), where, summary (300 chars), snippet (240), at
  most 5; archived conversations are included.
- **Prompt**: `RECALL_PROMPT`, added to `stable_system` only when
  `read_conversation` is registered (same bytes every turn): search, read
  the best hit, cite it briefly ("in your chat on Tuesday").
- **Config**: `[memory] embeddings`, `embedding_model`, `embedding_dir`,
  `embedding_min_score` (DEFAULTS and config.toml).

Measured (Linux sandbox, not real hardware; 5000 conversations where every
one matches the words, i.e. the worst case, fake 384-dim embedder):
keyword search 58-87 ms median, hybrid 117-127 ms median; backfill of
5000 with the fake embedder 3.4 s (a real model's time is not measured:
Hugging Face is blocked here).

Tests: new `tests/test_recall.py` (27, 1 skipped: the real fastembed model,
`slow`, needs the package and a download): local search by summary words
and message words with Pinecone not importable; title/summary hits above
body-only hits; archived found by the tool; `read_conversation` bounded,
window centered on the match (and cut at the end), long messages clipped,
bad ids; it keeps `chat.conversation_id`, adds nothing to the old one,
taints (a later move asks); both read-only lists; the prompt only with the
tool and byte-stable; hybrid finds a paraphrase FTS misses; fusion;
re-index after a new summary; indexed after finish; backfill resumes after
a crash mid-run and is bounded per run; a search never waits for it; an
embedder failure or no fastembed falls back to keywords; delete (and
prune) removes vectors and nothing comes back; a Retriever with its own
index is told about a delete; incognito never indexed; owner and member
stores isolated both ways; no conversation text at INFO (and the private
tool's input not even at DEBUG); the CLI attaches recall.
`test_family.py` +1 (with embeddings on, a member's own index finds her
paraphrase over the web route and never the owner's, and the owner's
never hers). `test_contract.py` +1 (search, then read, then a follow-up
turn through `contract_fakes`, whose script learned "what did we decide
about ..."). `test_history.py`: the search-tool shape test now expects
`date` and `summary` (renamed), and one test takes the first tool instead
of unpacking two.

Failing first (scratch copies, restored after each): with all source
files back at 62ed940, `test_recall.py` fails to import, and the family,
contract and search-shape tests fail. One mutation at a time: dropping the
title tier, the CASCADE, the taint (HISTORY_TOOLS + untrusted_output),
either read-only list entry, `archived=None`, the Retriever path, the
centered window, the "already indexed" condition, the prompt, the 500-char
clip, adding the query at INFO, incognito in `Recorder.active`, the
embedder fallback, the background thread, the delete hook or the summary
field each makes its test fail.
`test_docs.py` now also reads `python -m kwil.recall`'s flags and knows
the `read_conversation` tool name.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3656 passed, 42 skipped, 1 deselected (62ed940: about 3628 / 41; +28
passed, +1 skipped: the fastembed model test). JavaScript 303 passed
(unchanged; no JS in this change).

### Memory layer 2, judge round 2 (2026-10-29)
1. **Health checks can't read past chats.** `read_conversation` left
   `tools.READ_ONLY_TOOLS` (that list is for tools that read nothing
   private); it stays in `router.READ_ONLY_TOOLS`. Test: in the router set,
   not the check set, and a `SessionTools` check session neither offers
   nor runs it.
2. **Phone snippets stay at 12 words.** `history.search_conversations`,
   `Retriever.search` and `HybridRetriever.search` take `snippet_tokens`
   (default 12, what history_web and the CLI get); only
   `search_conversations` the tool asks for `recall.TOOL_SNIPPET_TOKENS`
   (24). Test with embeddings off and on.
3. **Deep pages with embeddings on.** Fused results stop at `FUSE_MAX`
   (200); when `offset + k` is past it, the page is keyword search with the
   offset (meaning hits are on the first pages). Test: 230 matching
   conversations, limit 20, offset 210 returns 20 (the same as keyword
   paging), and paging through all of them sees 220+ distinct ones.
4. **A failed embedder is retried.** The `_broken` flag is now a time
   (injectable `clock`): cleared after `RETRY_AFTER` (600 s), or at once
   when the model folder's modification time changes (`--download` ran).
   README and MANUAL_TEST say meaning search starts after `--download`.
   Test with a fake clock: not retried at 599 s, retried at 601 s, and
   retried at once when the folder appears.

Failing first (scratch copies, restored after each): with the four source
files back at 6c8513e all four new tests fail. One change at a time: the
tool back in `tools.READ_ONLY_TOOLS`, the phone default back at 24, and
never clearing the failure (by time or by folder) each fail their test.
Removing only the deep-page fallback while keeping the uncapped candidate
list still passes (vectors then cover the deep pages), so the 6c8513e run is
that test's evidence.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3659 passed, 42 skipped, 1 deselected (6c8513e: 3656 / 42; +3). JavaScript
303 passed (unchanged).

## Reminders and scheduled tasks (2026-10-30)
"Remind me at 9 to submit HW7", "remind me in 20 minutes to stretch",
"every Monday at 8am tell me what's due this week": reminders arrive as web
push notifications on the person's phone (the briefings' push.py).

- **Store** (`kwil/reminders.py`, `SqliteReminderStore`): one SQLite file
  per person, the memory store's pattern (private file, WAL,
  `secure_delete`, `wal_checkpoint(TRUNCATE)` after deletes and edits).
  Owner: `[reminders] path` (`data/reminders.sqlite`, Render
  `/data/reminders.sqlite`); family: `<dir>/<id>/reminders.sqlite`
  (`Family.reminders_of`, closed on removal). Fields: id, text (300),
  kind once/recurring, due_at or RRULE + zone, next_run (UTC), created,
  untrusted, paused, last_run, prompt_task, explicit, state
  active/done/missed. Bounds: 100 active, 200 runs a day, 24 times a day
  per rule, finished rows 7 days / 100, a run log of 2 days (ids only).
- **RRULE subset** written by hand (no new dependency): DAILY/WEEKLY,
  BYDAY, BYHOUR, BYMINUTE, INTERVAL=1; anything else refused with a
  sentence. Next times are wall-clock in the reminder's zone: 9:00 stays
  9:00 across DST; 2:30 on the spring-forward night runs at 3:30; 1:30 on
  the fall-back night runs once.
- **Tools** (`kwil/tools/reminders.py`): `create_reminder` (when, in_minutes
  or rrule; time_given; prompt_task), `list_reminders` (in
  `router.READ_ONLY_TOOLS` only, not the health-check list),
  `update_reminder`, `cancel_reminder` (always asks). Times are read in the
  conversation's zone (the configured one, a family member's own) and said
  back in it ("Thursday at 9:00 PM"). With outside text in the
  conversation, create and update ask first and save it untrusted. All
  private. Registered for the owner (cli and web) and each family member.
- **Firing** (`ReminderScheduler`, a thread started in create_app's
  lifespan, every `[reminders] check_seconds`): each run's key is written
  in the transaction that moves the reminder on, before the push; the
  owner's runs on a failover setup are also claimed in the shared log
  (`JobRunner`, `reminder:owner:<key>`, no leader gate: each backend's
  store is its own, as `proactive.owner_jobs`). Quiet hours hold back only
  a time the person didn't say; a run at most `grace_minutes` late fires
  once, older ones are marked missed (repeats go on). A push no device
  took (network, 5xx) is retried at the next checks, 3 tries.
- **Phase 2, scheduled prompts** (`PromptTasks`, `TaskTools`): a fresh,
  unrecorded conversation with only `TASK_TOOLS` (calendar lists,
  deadlines, Notion assignments, memories, course search, reminders list;
  no web tools), confirmations always no, any other call refused before
  it runs; routed by router.py (fast model unless the words or a tool need
  the main one; a tainted task goes to the main one). Counted in family
  quotas and on their own key. The answer, cut to 300 characters, is the
  notification; the whole answer goes to History as a conversation
  titled with the task. Built in this PR (not deferred).
- **Settings, Reminders** (`web/reminders.js`, `/api/reminders`, pause,
  delete): active ones with the next time and repeat in the person's own
  language and zone, Task and Untrusted badges, Pause/Resume, Delete (asks
  first), Missed ones listed apart; a reminder notification opens it
  (`/#reminders`). English and Chinese strings. `sw.js` v15; a reminder
  notification has no tag, so one never replaces another.
- **Export, delete, backup:** `reminders.json` in an invited person's
  "Download my data"; their file goes with their account; the owner's
  `/api/backup` has `reminders.sqlite` and `users/<id>/reminders.sqlite`.

Tests: `tests/test_reminders.py` (48): RRULE subset, DST on Nov 1 and
Mar 8 2026 (New York) in the rule and through the scheduler, bounds,
deletes leave no text in the file or WAL, create/list/cancel with the
confirmation, time parsing in New York on a TZ=UTC process with a pinned
turn clock, a family member's zone, untrusted creation asks (yes and
no), once on one file (two schedulers) and once across two backends with
a copied store (shared log), a run already recorded never goes again,
quiet hours, missed runs and missed repeats, the 200-a-day cap, retries
(and none for a reminder deleted meanwhile), one bad send doesn't stop
the rest, the push payload size (through the real push.py), the prompt
task's tools (a write is refused, never run; no web tools; fast then main
model), taint, quotas, History, routes, family isolation (404 both ways,
each person's pushes to their own uid), export/delete/backup, the app
starts and stops the scheduler, its claims sync with peers, and no
reminder text at INFO. `tests/test_contract.py`: a reminder in a
multi-turn exchange on contract_fakes (new script line). JS:
`tests/js/reminders.test.mjs` (9) and a sw-push test (each reminder its
own notification). Existing tests updated for the new tools, `/api/state`
key, public `/reminders.js`, `sw.js` v15 and `reminders.json`; tests that
build the app now point `[reminders] path` into tmp (not the repo's
`data/`).

Failing first (scratch copies, restored after each; never git stash): 33
single source reverts, each failing its test(s): the DST computation, the
run-key check, the job claim, quiet hours (off, and applied to a said
time), the grace check, the daily cap, the active bound, secure_delete,
TaskTools' filter and registration, task taint, quotas, both cuts, text
at INFO (push and task answer), the same-tick retry (a real bug found
while building), the per-send guard, confirm_if_untrusted,
confirm_prompt, naive times in the server zone, the router entry, the
per-person store lookup, family tools, export, closing on removal,
backup, the lifespan start, the sync wiring, sw.js's tag, reminders.js's
delete question and a missing Chinese string.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3708 passed, 42 skipped, 1 deselected (5997dda: 3659 / 42; +49).
JavaScript 313 passed (303; +10).

### Reminders, judge round 2 (2026-10-30)
1. **Quiet hours in the machine's zone.** With `[assistant] timezone` ""
   (a PC or Pi) the scheduler read quiet hours in UTC; it now resolves the
   person's zone with `zone_name_or_default`, the same zone the reminder
   was saved in. Test: a local New York machine (patched local zone), a
   19:00 reminder with no stated time fires at 19:00 and a 23:00 one waits
   for 07:30 local.
2. **A queued task deleted or paused before it runs** is not run: `_task`
   re-reads the reminder (a closed store counts as gone), so its text
   never reaches Claude or History. Tests: delete and pause.
3. **`[history] incognito`**: task answers aren't saved to History (the
   notification still goes), for the owner and family members. Test.
4. **Setting a finished reminder again** counts against the 100-active
   bound (`ReminderFull`; the tool says "full"). Test: 100 active + 50
   done, refused; allowed again after one delete.
5. **Task cost and fairness:** `[reminders] max_tasks_per_day` (48, the
   owner too) in any 24 hours, counted in the run log (new `task` column,
   schema 2, migrated); one person holds at most 5 of the 20 waiting
   tasks; a skipped task records "skipped" (not counted) and the person
   gets one localized "Skipped: too many scheduled tasks today" a day.
   Tests: the cap and the once-a-day notice, the per-person share with a
   real worker, the migration.
- Minor: removed the unused `ReminderScheduler.forget`; a notification for
  a reminder or task made from outside text says "(from outside text)"
  in its title (test).

Failing first (scratch copies, restored after each): each of the eight
fixes reverted alone fails its test(s): the quiet zone, the queued
re-check (2), incognito, the reactivation bound, the daily cap, the
once-a-day notice, the per-person share, the title suffix.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3717 passed, 42 skipped, 1 deselected (dd1c3a0: 3708 / 42; +9).
JavaScript 313 passed (unchanged).

## Study mode in chat and voice (2026-10-30)
"Quiz me on PHYS 361", "help me study for the midterm", typed or spoken:
Kwil becomes a Socratic tutor for the rest of the conversation.

- **State** (`kwil/study.py`, `ClaudeChat.study`): per conversation,
  cleared by `reset()`. Turned on by the `start_study(course, topic,
  review)` tool, the chat header's book button (`POST /api/study`), and
  off by `end_study` ("stop studying") or the pill's x.
- **Prompt cache rule:** `ask()` applies the wanted state before the
  turn's first request (`_apply_study`), so every request of a turn sends
  the same system bytes. The study block (STUDY_PROMPT, the course and
  topic as quoted names, grounding for the tools this conversation has,
  the review note) is a second system text block after the unchanged
  stable one, each with a cache breakpoint (3 in all with the messages
  one): a toggle rewrites only the tail after the stable prompt, the
  study turns in between hit. The turn that calls `start_study` gets the
  same rules in the tool result, so its first question follows them.
  `stable_system()` returns both blocks joined (one change per toggle).
- **Rules:** one question at a time, wait, hint before answer, a short
  explanation after each attempt, `note_missed` after a wrong one, maths
  said in words (the chat shows plain text and speaks replies, so no
  LaTeX; the Tutor tab keeps KaTeX). Grounding: `search_course_files` /
  `read_course_file` first when course content is synced,
  `list_deadlines` and `list_assignments` for "what's on the midterm";
  else from general knowledge. No study turn reaches the fast model
  (router reason `study`; "quiz", "study", "review", "test me", 复习,
  考考我, 测验 are main-model words).
- **Missed concepts:** a sibling table `missed` in the person's memory
  file (memory_store.py; `CREATE TABLE IF NOT EXISTS` outside the
  numbered migrations, so `user_version` stays 1 and a rollback still
  opens the file). Course (80), concept (120), count, first/last seen,
  untrusted; the same course and concept counts up; at most 300 (the one
  seen longest ago is deleted, then the WAL is truncated). `note_missed`
  (incognito and pause save nothing; marked untrusted when outside text
  is in the conversation, no question: SECURITY.md says why),
  `list_missed` (read-only, router-allowed), `start_study(review=true)`
  returns the list (that course, most missed first, 30 at most). In the
  export, the backup and "Download my data" through the memory file;
  Forget all clears it too.
- **Settings, Memory, "Study: things to review"** (`memory.js`,
  `GET /api/memories/missed`, `POST /api/memories/missed/{id}/delete`):
  course, concept, "Missed 3 times", Untrusted badge, Delete (asks).
- **Header** (`web/study.js`): the book button (with `[study] enabled`
  and the session's tools), the "Studying PHYS 361" pill with its x, kept
  in sync by `/api/state` and each turn's `done` (`"study"` only while
  on). English and Chinese strings; `sw.js` v16.
- **Family:** each member has study mode in their own conversation and
  their list in their own memory file; routes are 404 across people.
- **Config:** `[study] enabled` (DEFAULTS and config.toml).
- Not done: Lecture Notes pages in Notion aren't read (no chat tool reads
  them yet); the voice view has no pill (the chat's shows once you're
  back).

Tests: `tests/test_study.py` (20): the block only from the next turn
(same system bytes inside the starting turn, rules in the tool result),
one change per toggle by a trailing cached block (the stable block
byte-identical, no change between study turns or for the same course,
the old bytes back after end, at most 4 breakpoints), end_study and
reset, start_study asks first with outside text, bounded names, the store
(count-up, the 120 bound, eviction of the oldest at 300, course filter),
real deletes (file and WAL bytes, Forget all), the schema version left at
1, export, the tool (stored, marked untrusted, paused), incognito,
review from the list (filtered, most missed first, empty), course-file
grounding with a fake CourseStore, the router (reason study, keywords,
read-only list), no study text at INFO (DEBUG only for the course),
config, the toggle and x routes and the done event, 404 without study
mode, the Settings routes, family isolation (her note goes to her file;
404 both ways; her study state is hers). `tests/test_contract.py`: "quiz
me on PHYS 361" -> start_study -> a question -> a wrong answer ->
note_missed -> an explanation, on contract_fakes (new script lines),
every request accepted. JS: `tests/js/study.test.mjs` (5: button and
pill, toggle and x POSTs, done redraws, text not HTML, errors, Chinese,
wiring) and 3 in `memory.test.mjs` (the list, delete asks, empty and off,
Chinese). Existing tests updated for the new tools, `/api/state` keys,
public `/study.js`, `sw.js` v16 and the Forget all wording.

Failing first (scratch copy of the tree, one source change reverted at a
time, restored after each; never git stash): all new Python tests fail
on the base commit (the JS ones too); 19 single mutations each fail
their test: chat.py reverted, the block applied at once inside the turn,
one merged system block, reset keeping study, router.py reverted,
memory_store.py reverted, no WAL purge after a delete, Forget all keeping
the list, no eviction, the table bumping the schema version, incognito
ignored, no confirm_if_untrusted on start_study, review ignoring the
list, no course-file grounding, the course logged at INFO, family without
study tools, no toggle route, no list routes.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3738 passed, 42 skipped, 1 deselected (250da33: 3717 / 42; +21).
JavaScript 321 passed (313; +8).

### Study mode, judge round 2 (2026-10-30)
1. **Taint of the missed list tested:** a concept noted untrusted, then
   `list_missed` or a review: the result is marked untrusted, the
   conversation is tainted, and a later `confirm_if_untrusted` tool asks
   (both paths, parametrized).
2. **A review stays in its course:** courses match ignoring spaces and
   case ("PHYS361" = "PHYS 361", also for `list_missed`); a course with
   nothing noted gets an empty list and "Nothing to review for Chem 101
   yet", never other courses' concepts.
3. **Pause memory:** `list_missed` returns "paused" with no list, and a
   review starts without one (`status_review: "paused"`).
4. SECURITY: an older Kwil after a rollback leaves the list on Forget all.
5. **Resumed conversations:** `_switch_to` clears study mode (it belonged
   to the conversation that was left).
6. Router: 学习, 考试 and 测试 are main-model words too (学习 also
   matches everyday sentences; that only costs the main model).

Failing first (scratch copy, one change reverted at a time): 7 mutations
(each mark_untrusted removed, the all-courses fallback, the old
space-sensitive match, the pause checks, `_switch_to` keeping study, the
Chinese words) each fail their new test.

Counts (Linux sandbox): 3743 passed, 42 skipped, 1 deselected (4c00e06:
3738 / 42; +5). JavaScript 321 passed (unchanged).

## Try again, edit and resend, branch (2026-10-30)
The phone chat gets ChatGPT/Claude-style controls. **↻** under Kwil's
last reply answers the same message again; the **pencil** on Kyle's last
message puts it back in the text box ("Editing your last message", × or
Esc cancels) and sends the new words; **Branch from here** (long-press a
message in a stored transcript, or its hover "…" button) copies the
conversation up to that message into a new one, "<title> (branch)", and
binds the chat pane to it.

- `ClaudeChat.regenerate(text=None, on_text, cancel, on_start)` (chat.py):
  refuses (`RedoRefused`) with no turn, or when the turn used a tool outside
  `tools.REDO_SAFE_TOOLS` (an allowlist of tools that only read, plus web
  search/fetch), from its tool_use blocks and from the saved tool events
  (a resumed conversation is text). Otherwise the turn is rewound
  (`_keep_taint` first: taint survives), `ask()` runs it again (Kyle's
  message not saved twice: `record_user=False`; attachments kept), and
  once the new reply is in, the old reply (and for an edit the old message)
  is superseded in history. A failed request puts the old turn back.
- History (history.py): `last_turn`, `supersede` (a `delete_message`
  tombstone marked `"superseded": true` per message: no new event kind, so
  every backend applies it and redacts the text in either order; search
  index and WAL purged), `branch` (a new id, at most `MAX_BRANCH_MESSAGES`
  = 200 of the newest messages up to the index, text and compact tool
  events only, the title kept as if Kyle set it; not the summary).
  Feature notes (kind "note") are never superseded.
- Web (webapp.py): `POST /api/regenerate` and `POST /api/edit {"text"}`
  (both take `conversation_id`, checked like `/api/turn`'s) go through
  `WebAssistant.start_redo`: the turn lock taken without waiting (409 "Wait
  for the reply to finish first." / "Answer the question first."), the
  redo check (for a bound conversation the chat isn't in yet, from the
  store before resuming it), quotas, then a normal turn thread that emits
  `{"type": "redo"}` once rewound and `{"type": "error", "restored": true}`
  if the request then failed. Every page turn's "done" says `"redo"`:
  whether to offer the buttons. `POST /api/conversations/{id}/branch
  {"message_index"}` (history_web.py): owner-checked 404, 409 incognito,
  400 for a bad index; the transcript's detail carries `"redo"` too.
- Page (app.js, history.js, index.html, app.css, i18n.js): the pane
  tracks its last turn (Kyle's bubble, what he said, what came after);
  the buttons are 44px icon buttons (line SVGs), disabled while busy or
  asked, gone when anything else is drawn; the "redo" event hides the old
  reply (and rewrites the bubble for an edit), "restored" brings it back,
  "done" removes it. Continue and Branch share `openInChat` and offer the
  buttons on the stored last turn when the server says so. English and
  Chinese strings; `sw.js` cache `kwil-shell-v17`.
- Deviation: superseded messages are hidden (deleted), not shown
  collapsed. "Branch from here" is in the History transcript (stored
  messages have indices), not on the live chat bubbles.

Tested: `tests/test_regenerate.py` (32): try again replaces the reply and
the old one is gone from the store, search, both exports and the event
log (redacted); edit resends the new text; refused after a write tool
(also once resumed as text), while busy, while a question is open (also a
question asked by the redone turn), with nothing to redo, for another
owner's id; read-only tool turns redo; taint survives; the old reply's
thinking and the `_thinking_from` tag never go out, earlier thinking only
with the same model; a failed request restores the turn (chat, store and
the page's "restored"); a Stop keeps the partial reply and still
supersedes; feature notes stay; no text at INFO (chat, web, branch);
branch copies up to the index, new id, title, the pane's first turn
resumes it text-only and tainted, tool names but no tool calls, bounded,
input checks, 401, 403 cross-site, incognito 409, family and owner 404
both ways (her own branch works); sync: superseded tombstones applied on a
peer in order and tombstone-first, and a delete there removes it all.
`tests/test_contract.py`: try again and edit in a multi-turn exchange with
a tool round on contract_fakes (plain and routed; live in CI). JS:
`tests/js/app-redo.test.mjs` (12): buttons only with `"redo": true`,
placement, POST bodies (bound id), the redo/restored/done drawing, edit
flow and cancel, disabled while busy or asked, edit refused while busy,
409 note, new pane drops them, Continue offers them (or not), Branch posts
the index and binds the pane, waits for idle, shows refusals. Existing
tests updated: "done" now carries `"redo"` (3 exact matches), the branch
route in the 401 list, `sw.js` v17 pins.

Failing first (scratch copy, one source change reverted at a time,
restored after each; never git stash): all new tests fail on the base
commit (no routes or methods); 27 single mutations each fail their test:
no `_keep_taint` on the rewind, no write-tool refusal, saved tool events
ignored, the old reply left in history, no restore on failure, no
supersede, the edit keeping the old message, notes superseded, edited
text at INFO, no confirming check, waiting for the lock, no store check
for a bound conversation, no "restored", `done.redo` always true, branch
unbounded, negative index accepted, branching while incognito, the title
at INFO, a deny-list instead of the allowlist; JS: no disabled state,
buttons without `redo`, no restore, the old reply kept, no bound id,
branch not binding, branch while busy, Continue without the buttons.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3777 passed, 45 skipped, 1 deselected (d75b206: 3743 / 42; +34 passed,
+3 live contract params skipped without a key). JavaScript 333 passed
(321; +12).

### Try again, edit, branch: judge round 2 (2026-10-30)
1. **A write plus a resume in one turn is never redone.** `_switch_to`
   saved the exchange it carries into the resumed conversation without
   its tool events, and the chat history became plain text, so "add 'Lab
   due' to my calendar and continue our lab chat" could be tried again
   and ran `create_calendar_event` twice. The carried reply is now saved
   with the turn's tool events (History shows them), and the chat
   remembers the switch (`_switched`, cleared by the next turn and by
   `reset()`): either one alone refuses the redo.
2. **`check_dev_team` left `REDO_SAFE_TOOLS`:** `devteam.replies()`
   takes answered replies out of the pending list and saves that. Every
   other entry was re-read for hidden state: Gmail reads use
   `messages.get` (no read mark; `modify` is blocked); notes, memory
   searches, the missed list, deadlines, reminders and course files only
   query; calendar, Home Assistant, Spotify and team-chat reads fill
   in-memory caches; history search may start recall's own idempotent
   vector backfill. Nothing else moved.
3. **Stop before the first word keeps the old answer.** If a redo is
   stopped before any text or tool of the new reply, the old turn is put
   back (chat history, and the empty reply plus an edit's new message are
   taken out of saved history; nothing superseded) and `RedoStopped`
   gives the page `{"type": "error", "restored": true}` ("Stopped. The
   earlier reply stays."). The page already brought the old reply back on
   "restored"; a JS test now covers it through the Stop button.

Tests: 5 new Python (the judge's exact path, both try again and edit,
plus the switch flag alone, a later chat reading the saved tools and the
flag clearing; check_dev_team refused; Stop before a word for try again
and for an edit, and on the page) and 1 JS (Stop, then "restored").
Failing first: the 5 Python tests fail on 2c0701c (the JS one passes
there: the page's handling didn't change); 7 single mutations each fail
their test (switch saved without tool events, the switch not remembered,
the flag not cleared, check_dev_team allowed, Stop superseding, a stopped
edit's new message left, the page not told "restored").

Counts (Linux sandbox): 3782 passed, 45 skipped, 1 deselected (2c0701c:
3777 / 45; +5). JavaScript 334 passed (333; +1).

## Projects, one per class (2026-10-30)
Like Projects in ChatGPT and Claude: a space per class (PHYS 361, CS 171)
with its own instructions, files, memories and conversations.

- **Storage** (`memory_store.py`): sibling tables `projects`,
  `project_memories` and `project_files` in each person's own memory file
  (`CREATE TABLE IF NOT EXISTS`, schema version still 1, so a rollback
  opens the file and never sees a project memory as a global one). Chosen
  over a separate file: one per-person home that already has the isolation
  (`memory_store_of`), the backup, "Download my data", account deletion,
  Pause and Forget all. Memory ids share one space, so update, trust and
  delete (tools and Settings routes) work on both kinds. Files are BLOBs
  plus their extracted text (pypdf via `textextract.py`), so a delete is
  zeroed by `secure_delete` and checkpointed, with no files on disk to
  replace atomically on Windows. Bounds: 30 projects, 100 memories each,
  `[projects] max_files` / `max_file_mb` / `max_person_mb`, checked inside
  the write transaction.
- **Conversations** (`history.py`): `conversation_projects`, a local table
  in history.sqlite (`ON DELETE CASCADE`, like `recall_vectors`; not a
  synced event: projects are per backend). `list`/`search`/`count`/
  `recall_vectors` take `project=`; each conversation dict says its
  `project`; `branch` copies it; a project's delete unfiles its chats.
- **Chat** (`chat.py`, `projects.py`): `ClaudeChat.project_id`, chosen
  before a conversation starts (`set_project`) or taken from the stored
  conversation it resumes (`resume`, `_switch_to`); `reset()` clears it,
  regenerate keeps it. The project is part of the per-conversation
  snapshot (`snapshot(project=...)`): its instructions and memories go into
  the stable system block, the same bytes all conversation. Its files go
  as document blocks at the start of the first message, added at request
  time (never in history), one cache breakpoint after the last: with the
  stable block, study mode's and the end, four at most. Only while they
  fit `inline_tokens` and `inline_mb`; past that the system prompt lists
  them and `search_project_files` / `read_project_file` (new, read-only,
  untrusted, private) find them. Attached files taint the conversation
  (`untrusted_project_files`, sticky); deleted files, memories and
  projects leave a running conversation before its next request.
- **Remember** saves to the conversation's project unless `general: true`;
  list/update/forget see global memories and that project's only. Global
  memories and "Your instructions" still load everywhere.
- **History tools:** `search_conversations` and `read_conversation` take
  `project_only` in a project's conversation.
- **Web** (`projects_web.py`, `webapp.py`): `/api/projects` list/create
  (one tap from a suggestion; the same course twice makes one),
  `/suggestions` (the person's Canvas course content and deadline courses,
  the owner's Notion Course options), `/{id}` (page data with its chats),
  update, delete (`{"confirm": true}`), one-file upload and file delete;
  `/api/turn {"project_id"}` starts or keeps a new chat in a project ("":
  leave it); `done` and `/api/state` carry `project`;
  `/api/conversations?project=`. All through `memory_store_of`: 404 across
  people.
- **Page** (`projects.js`, `index.html`, `app.js`, `app.css`, `i18n.js`):
  Settings -> Projects (rows, New project in a sheet, suggestions with
  Create), a project page (New chat in this project, instructions, files
  with Add file, memories, chats, Rename, Delete project), the header's
  project name (opens the page; x leaves). English and Chinese; `sw.js`
  `kwil-shell-v18`; `projects.js` in the static routes, `SHELL` and
  `PUBLIC_GET`.
- **Export and backup:** the owner's backup has them (memory.sqlite); an
  invited person's export has `memory.json` with projects and each file
  under `projects/<project>/`.

Tests: `tests/test_projects.py` (34: store bounds, real deletes of files
and projects in the database and WAL bytes, the rollback-safe schema,
Forget all, export; the system prompt only in its project and byte-stable,
global memories still there, pause; remember scoping and other projects'
ids unknown; files as cached documents with taint, the budget switching to
search, the file tools keeping to their project, four breakpoints with two
files + study mode + an attachment on the contract fake, deletes leaving a
running conversation; tagging, the History filter, search/read
`project_only`, try again and edit, branch, resume; no project text at
INFO; the routes, upload validation and bounds (a photo never decoded),
a new chat from the page, incognito, family isolation on every route both
ways, suggestions, her export and the backup, account deletion), a
contract test (`test_a_project_chat_with_a_file_and_a_remember`), and
`tests/js/projects.test.mjs` (9: menu, list and suggestions, the page's
actions, rename/delete, the pill, the pane's `project_id`, a bound pane,
`/api/state`, a 404'd project, Chinese strings and wiring). Existing
tests updated for the new tools and fields (family/Notion tool sets, the
untrusted set, `/api/state`, the History item keys, `sw.js` v18 pins).

Failing first (scratch copy, never git stash): on b539432's source every
new Python test errors (no module) and the contract and JS tests fail; 52
single mutations each fail their test (project memories in the global
list, reset keeping the project, reloading each turn, no project block,
remember ignoring the project or `general`, other projects' ids visible,
no taint, a breakpoint per document, none, the budget never switching,
another project's file read, deleted files/projects kept, delete without
purge, files left by a project delete, Forget all leaving project
memories, no person total, no project cap, photos accepted or decoded, no
tagging, no History filter, `project_only` ignored twice, branch dropping
the project, regenerate or resume losing it, the name or query at INFO,
the owner's store for everyone, unchecked turn and History ids, the turn
ignoring `project_id`, chats left filed, Kyle's Notion for family members,
two taps making two, the export without files, incognito refused, "his"
in a family member's prompt; JS: no `project_id`, no "" on leaving, a
bound pane sending one, `done` or `/api/state` not adopted, the pill as
HTML or shown when off, a file delete without asking, a 404 kept).

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3817 passed, 45 skipped, 1 deselected (b539432: 3782 / 45; +35).
JavaScript 343 passed (334; +9).

### Projects, judge round 2 (2026-10-30)
1. **M1, the inline budget.** A PDF whose page tree sits in a compressed
   object stream (pdfTeX's default) had no page count, so a 150-page one
   was attached and every turn of its chats failed. Now pypdf counts when
   `pdf_pages` can't, over 100 pages is refused at upload, and a count that
   stays unknown is never attached (searched). Text is estimated as
   max(chars / 4, UTF-8 bytes / 3) (Chinese was 4x under). Safety net: a
   project chat's request refused with the files attached ("prompt is too
   long", "A maximum of 100 PDF pages") stops attaching them for that
   conversation, switches its system prompt to search, retries once and
   logs a warning. SECURITY.md and README corrected.
2. **L1:** `search_project_files` reads `file_texts` (id, name, text), never
   the BLOBs.
3. **L2:** uploads check the size, then (read-only `check_room`) the
   project, its file count and the person's total, before any parsing;
   `add_file` checks again in its transaction.
4. **L3:** a test deletes project A's file through project B's URL: 404,
   the file stays.
5. **L4:** an invited person's export counts project files against
   `[projects] max_person_mb`, not `[family] export_max_mb` (simplest: the
   upload bound already holds them). The owner's backup leaves project
   files out of every memory.sqlite copy (rows deleted, vacuumed, the count
   in MANIFEST.json): with them it would be everyone's `max_person_mb`
   together (11 x 20 MB on Render's 1 GB disk), over `backup_max_mb`, so
   there would be no backup at all; raising the cap would need disk the
   server doesn't have. Projects, instructions and memories stay in it.
6. **L5:** with Pinecone, a project chat's summary carries `project_id`
   and is recalled (before a reply and by `search_memory`) only in that
   project's chats.
7. **Extraction deadline:** left as is and documented in SECURITY.md (not
   fixed, on purpose): it runs in the thread pool, size-capped; a killable
   subprocess would cost a Python start per upload on the Pi.

Tests: 6 new (object-stream PDF built in the test, CJK, the safety net for
both refusals, search without bytes, the export allowance, Pinecone
tagging and filtering) and 4 extended (unknown pages never fit, the
upload precheck before parsing, cross-project file delete, the backup
without files). Existing fakes updated for `retrieve(query, project=)`
(test_memory_tools, test_routing_caching). Failing first: 15 single
mutations each fail their test (no pypdf count, no 100-page refusal,
unknown pages attached, chars/4 only, no safety net, the page-limit 400
not recognised, the prompt not switched, search loading bytes, no
precheck, the file deleted through another project's URL, files under the
export cap, no files allowance, the backup keeping files, recall
unfiltered, the summary untagged).

Counts (Linux sandbox): 3828 passed, 45 skipped, 1 deselected after the [auto-review] braces fix (+4) and judge round 3's Pinecone over-fetch test (+1) (3823 before them; 16cf97d:
3817 / 45; +6). JavaScript 343 passed (unchanged).

## Think harder and citations (2026-10-30)
Two items from Kyle's ChatGPT/Claude roadmap, in one PR.

**API shapes, checked first** (the docs fetched from the sandbox on
2026-10-30, `.md` pages): `build-with-claude/thinking` (the per-model
`thinking` table: on Opus/Sonnet/Haiku 5.5 no `thinking` field already
means adaptive thinking; `{"type": "adaptive"}` is accepted; `"enabled"`
with `budget_tokens` is a 400; `"disabled"` a 400 on Opus/Sonnet 5.5),
`.../extended-thinking` (budget_tokens deprecated, a 400 on Claude 4.7 and
later), `.../effort` (`output_config.effort`: low, medium, high, xhigh,
max on all three 5.5 models; Sonnet 5.5 defaults to high, Opus/Haiku 5.5
to medium; a top-level effort change misses the prompt cache once) and
`.../search-results` (GA, no beta header; `search_result` blocks in user
messages or as a tool result's whole content; citations all on or all
off, on when web_search is offered). The installed SDK's own
`ThinkingConfigAdaptiveParam` and `OutputConfigParam` types agree.
`tests/contract_fakes.py` now refuses everything else in those shapes
(and a `search_result` in an assistant message, mixed into a tool result,
with empty text, or with citations off next to web search), and its
structured-output script now needs `output_config.format`, not just any
`output_config`.

- **Think harder** (`thinkhard.py`, `chat.py`, `router.py`, `quotas.py`):
  `ClaudeChat.ask(think=True)` (the page's toggle or a long press on Send:
  `/api/turn {"think": true}` or the form field `think=1`), or a message
  starting "think hard about...", "think it through", "仔细想想",
  "认真想一想" (typed or spoken: the CLI, push-to-talk and the voice view
  all go through `ask`). That turn: the router's reason "think" sends it
  to `[assistant] model`; `max_tokens` = `think_max_tokens` (16000, never
  under the turn's usual ceiling; 21000 at most when not streamed, the
  SDK's limit); `thinking={"type": "adaptive"}` and
  `output_config={"effort": think_effort}` ("xhigh"; "" sends none).
  Never sticky: reset at the start of every turn. Capped per person per
  day (`think_per_day`, 30) by `UserQuotas.admit_think`: an invited
  person's in `Family.quotas` (their chat's `think_gate`), the owner's in
  one UserQuotas per process (`thinkhard.owner_gate`, his `[assistant]
  timezone`'s day, UTC when unset); no gate or a failing one means an
  ordinary turn. Over the cap the turn is ordinary and the page gets a
  "status" note (`think_limit`, English and Chinese). Tiers: the turn
  uses the chat's own model (an invited person's chat has the configured
  one), so nobody gets a model beyond their chat's. Kept thinking (#84):
  a think turn after a fast-model turn is a model switch (the earlier
  thinking is forgotten, as for any switch); after it the conversation
  stays on the main model (the router's existing rule), and the next turn
  gets the think turn's thinking back.
- **Citations** (`citations.py`, `chat.py`): web_search's
  `web_search_result_location` citations become numbered web sources (a
  page cited twice is one number) with a mark (character offset) after
  each cited text block of the final reply. `read_conversation`,
  `search_project_files`, `read_project_file`, `search_course_files` and
  `read_course_file` results go to Claude as `search_result` blocks
  (`Sources.offer`; errors and empty results stay JSON text) with a
  `kwil:<kind>/<id>` source, so a `search_result_location` citation maps
  back to the chat ("Chat on Tue 27 Oct", "10月27日的聊天"), project file
  or course file; a source id no tool of this turn returned is ignored. A
  read tool that ran is listed even when not cited. A system prompt line
  asks Claude to say in words where an answer came from (chats, files,
  memories) and never to read web addresses. Bounds: 20 sources, titles
  200, URLs 2000 (http/https with a host only), cited text 300, 50 marks
  of at most 5 numbers, 12 KB stored. INFO logs counts and kinds only.
- **History** (`history.py`, `history_web.py`): the message event's
  `cites` and a `messages.citations` column, added with `ALTER TABLE`
  when missing (not a numbered migration: an older Kwil after a rollback
  still opens the file and its inserts leave it NULL). `get`, the History
  detail route, JSON and Markdown export, import (checked again), branch
  and `mark_interrupted` (sources kept, marks dropped) carry them; a
  delete, a message delete and a supersede remove them with the row.
- **Web** (`webapp.py`, `web/sources.js` new, `app.js`, `history.js`,
  `projects.js`, `index.html`, `app.css`, `i18n.js`): the reply event
  has `citations` (and `think` for a think turn). `sources.js` draws the
  superscripts (web pages only, renumbered 1..n) and the list (own data
  as one "Sources: ... · ..." line of buttons opening the chat or the
  project; web pages as numbered rows, `target="_blank"`,
  `rel="noopener noreferrer"`), textContent only, URLs checked again. The
  composer has a 44px light-bulb toggle (`#think`, `aria-pressed`) left
  of Send (`#send`; holding it 500 ms turns think on and sends on
  release); a queued message keeps its own flag, a yes/no answer never
  has one. `sources.js` is in the static routes, `SHELL` and `PUBLIC_GET`;
  `sw.js` `kwil-shell-v19`. DESIGN_SYSTEM.md has both components.
- **Speech:** `sentences.without_urls` in `WebAssistant.synthesize` and
  `cli.speak`; the voice view's splitter already dropped URLs; the
  phone's own voice uses `KwilSources.speakable`.

Tests: `tests/test_thinkhard.py` (25: the main model, 16000 and the
adaptive/effort shape through the contract fake with the router on; not
sticky; the phrases and non-phrases; the per-day cap and the shared owner
count; no gate and a failing gate; per-person day rollover and a removed
person; kept thinking across the switch; the unstreamed ceiling; no
effort; config defaults; a family member on the chat's model with her own
count; the page's flag, status note and form field; no words at INFO),
`tests/test_citations.py` (35: unsafe URLs, bounds, control characters,
uncited/unknown sources, each tool's search_result blocks, the fake
accepting them with web search on, a read listed without a citation,
web citations numbered and marked, reset per turn, no text at INFO,
history storage, export/import cleaning, supersede and delete, try
again, interrupted, branch, the rollback-safe column, URLs never spoken
by `cli.speak` or the web audio, the phone's reply event), contract tests
(`test_a_think_harder_turn_the_api_accepts` and
`test_kwils_own_data_as_search_results_the_api_accepts`, both live-capable
in CI's api-contract job; `test_a_web_search_reply_brings_its_cited_pages`;
11 new refusals in `test_the_fake_refuses_what_the_api_refuses`; the
recall contract test now checks the search_result blocks and the stored
source), `tests/js/sources.test.mjs` (5) and `tests/js/app-think.test.mjs`
(5). Existing tests updated for the new result shape
(`test_recall.py`, `test_projects.py`: read_conversation and the project
file tools now send search_result blocks), the `sw.js` v19 pins and the
public file list; two JS fake DOMs got `setAttribute`.

Failing first (scratch copies, never git stash): on 8a50a9d's source the
new Python test files error (no modules), the four new contract tests and
all ten JS tests fail; the old contract fake accepts all 11 new refusals.
Single mutations each fail their tests: no "think" route, budget_tokens
instead of adaptive (the fake's 400), no cap, no family gate, the flag
kept across turns, no search_result conversion, any URL scheme, no
storage, TTS reading URLs, no citations on the reply event, no marks;
JS: `javascript:` allowed.

Counts (Linux sandbox, not real hardware), `-k "not live_pinecone"`:
3902 passed, 49 skipped, 1 deselected (8a50a9d in a `git archive` copy:
3826 passed, 46 skipped, plus `test_design_skills` failing there only
because the archive leaves its data out; +75 passed). The new skips are
the live contract runs (no key here). JavaScript 353 passed (343; +10).

### Think harder and citations, judge round 2 (2026-10-30)
1. **Marks after an emoji.** Marks were Python code points; the page
   slices UTF-16 units, so after an emoji a superscript landed one unit
   early (inside the next character, or splitting a surrogate pair). The
   server now counts UTF-16 units (`citations.utf16_len`), and
   `sources.js` moves a mark that would split a surrogate pair past it.
   No stored marks need converting: no release ever wrote code-point
   marks (the feature was only on this branch). Tests: an emoji before
   the mark in Python and JS, and a mark inside a pair in JS.
2. PRIVACY: the master's row.
3. **The think flag** is now only JSON `true` or a form's `"1"` (it also
   took `"true"` in any case); SECURITY.md says so; the web test sends
   `"true"`, `"TRUE"`, `"1"`, `1`, `"yes"` and an object as JSON and
   `"true"`, `"True"`, `"on"`, `"yes"` as a form, and none counts.
4. **Citations all or none.** The citations doc
   (build-with-claude/citations, fetched 2026-10-30) says citations must
   be on for all or none of a request's documents, and calls both
   `document` and `search_result` blocks documents; the search-results
   page says the same for search results. So every document block (an
   attached file, a project file) now goes with citations on
   (`chat.cite_documents`, at request time, copies; history unchanged,
   cache breakpoints kept, the same bytes each request). The doc also
   says citations can't be combined with `output_config.format` (the
   chat never sends one). `contract_fakes` enforces both across
   documents and search results, and a new contract test attaches a PDF,
   then reads a past chat (`test_a_pdf_attached_then_a_past_chat_read`).

Failing first (scratch copy): code-point marks fail the Python emoji
test, the old JS fails the surrogate test, the old flag parsing fails the
web test, no `cite_documents` fails the new contract test (the fake's
400), and the old fake accepts both new refusals.

Counts (Linux sandbox, not real hardware): 3907 passed, 49 skipped, 1
deselected (ee6acb6: 3902 / 49; +5). JavaScript 354 passed (353; +1).

### Think harder and citations, judge round 3 (2026-10-30)
1. **LOW: inline files never listed.** Answers from a project file sent
   in full, or from an attached file, came back with
   `char_location` / `page_location` / `content_block_location`
   citations, which `Sources.cite` ignored. Each request now maps its
   document blocks in `document_index` order (`Sources.set_documents`,
   from the messages as sent): the project's files, which come first in
   the first message with known ids, become `project_file` sources (with
   the conversation's project; a tap opens it). Attached files become a
   new kind, `attachment`, named "plan.pdf (attached)" / "plan.pdf（附件）"
   and not clickable: the file isn't kept, so there is nothing to open.
   They have the same bounds, and an index out of range is ignored.
   `sources.js` accepts the kind and draws it as plain text.
2. **Edge: an empty text document with citations on.** The store already
   refuses zero bytes, but a file that is only a byte-order mark or blanks
   decodes to no text. Rather than guess whether the API takes it,
   `chat.cite_documents` sends such a document as a
   "[file: name (empty)]" text note (its cache breakpoint kept), so it is
   never a citable document and never counts in `document_index`.

Tests: `test_an_answer_from_an_inline_file_lists_it` (and the out-of-range
index) and `test_an_empty_text_file_goes_as_a_note_not_a_document` in
`test_projects.py`, `test_an_attached_file_is_named_not_linked` in
`test_citations.py`, and a JS test for the attachment kind. Failing first
(scratch copy): document citations ignored, no document map, empty text
cited, no range check, and the JS kind missing each fail their tests.

Counts (Linux sandbox, not real hardware): 3910 passed, 49 skipped, 1
deselected (51e82fc: 3907 / 49; +3). JavaScript 355 passed (354; +1).

### Think harder and sources, judge round 4 (2026-10-30)
- MED, fixed by the master: an attachment cited more than once was listed
  once per citation (`Sources._number` compared without the title fallback
  its key used), which could fill MAX_SOURCES and drop later web pages. Test
  `test_an_attachment_cited_twice_is_listed_once`; fails without the fix.

## Deep research (2026-10-30)
"Research the best internships for physics majors this summer" / "do a
deep dive on X": a background job that plans, searches and reads the web
in several rounds and writes a cited report (`research.py`,
`tools/research.py`, `web/research.js`).

**API shapes, checked first** (docs fetched from the sandbox, `.md`
pages): `agents-and-tools/tool-use/web-search-tool` (`max_uses` is per
request; `max_uses_exceeded` past it; `usage.server_tool_use.
web_search_requests`; `pause_turn` is continued by sending the paused
assistant content back as it came), `.../web-fetch-tool` (`citations`
optional and off by default; fetched pages cited by `char_location`
`document_index`; `web_fetch_requests`) and `.../server-tools`. The SDK's
`WebSearchTool20260209Param` / `WebFetchTool20260209Param` list the
fields; `tests/contract_fakes.py` now refuses any other field, a wrong
name or a `max_uses` under 1, and scripts the research plan, steps and
write.

- **start_research(question, depth)** always asks first ("Start research
  on: ...? It takes about 5 to 10 minutes and does up to 10 web searches
  (a small cost)."), the whole question when the conversation is tainted;
  returns at once. Bounds: one job queued or running per person,
  `[research] per_day` (3) started in any 24 hours (from the job files, so
  a restart keeps the count), 10 waiting or running across everyone, 2 at
  once. Refused without asking when it would be refused anyway; refused
  with History off or in incognito (the report goes there) and for a beta
  tester without a key. **research_status** (read-only) for "is my
  research done?".
- **The session** (`ResearchRunner`): the main model, adaptive thinking,
  `[research] effort` "high". Plan (structured output, at most 3 / 5
  parts) -> one step per part with only web_search and web_fetch (fetch
  citations on), `max_uses` = what is left of the job's cap divided over
  the parts left (a paused step goes on with the remainder, the whole turn
  so far sent back, or stops) -> write with the findings as one
  search_result block per page (source = its address; `Sources.offer_web`
  added to citations.py) so the report's citations become the
  conversation's web sources. Time and token budgets keep their last 25%
  for the write; past that no more parts, and the report ends "Partial
  report: ...". A failed write saves the findings as they are.
- **Nothing of the person's** reaches it: its own requests with only the
  question, no ToolRegistry, no client tools.
- **Delivery:** a History conversation "Research: <question>" (title kept
  as the person's), the report with its sources, untrusted when continued
  (resume taints); a push "Your research on X is ready" opening
  `/#research=<id>`; the card's Save to Notion (`ResearchNotesDB`, the
  lecture database pattern, the job id as "Research ID") only when the
  person's Notion is connected. Owner jobs run under the failover
  JobRunner (`research:owner:<id>`); a job cut off by a restart is marked
  failed, not rerun.
- **UI:** a card above the composer ("Researching… (3/10 searches)",
  polls every 4 s while a job runs, Cancel asks first; Open report opens
  the History transcript with its sources; Save to Notion; dismiss), in
  English and Chinese. `sw.js` `kwil-shell-v20`.
- **Config:** `[research] enabled, dir, per_day, standard_searches,
  standard_fetches, standard_minutes, standard_tokens, deep_*, effort,
  report_tokens` (DEFAULTS, config.toml, the server profile's
  `/data/research`, config.mom.toml's own folder).

**Tests:** `tests/test_research.py` (31), `tests/js/research.test.mjs`
(8), `tests/test_contract.py` (+4 refusal cases, +1 research request
test, live variants marked `live`). Each new behaviour was checked to
fail with its source change reverted (a scratch copy, 27 mutations). Cost
not measured on the real API (no key in the sandbox).
Full suite: 3947 passed, 51 skipped (baseline 3911 passed); JS 363 passed (baseline 355).

### Deep research, judge round 1 (2026-10-30)
All eight findings fixed, each with a test that fails with its fix reverted:
- MED 1: research_status is `untrusted_output` and marks its result
  untrusted (a tainted job's question can't enter a clean chat as trusted).
  `test_research_status_is_outside_text`.
- MED 2: a failed research step ends the research there; the report is
  written from what was found, partial "error" ("a search step failed",
  en/zh). `test_a_failed_step_keeps_what_was_found`.
- MED 3: the confirmation always shows the whole question (never cut);
  the tool description says not to include personal details unless asked;
  README/SECURITY say "only the question, exactly as you confirmed it".
  `test_the_question_is_always_shown_whole`.
- LOW 4: a confirmation skipped because the job would be refused makes
  that call refuse with the same reason, and the handler starts only the
  question that was confirmed. `test_a_refusal_that_clears_never_starts_unasked`.
- LOW 5: a cancel landing during the save deletes the saved conversation
  and sends nothing. `test_cancel_while_saving_keeps_and_sends_nothing`.
- LOW 6: research requests time out by the 75% mark; the write gets at
  least its 25% share and one retry at most. `test_timeouts_keep_the_write_its_share`.
- LOW 7 (docs only, code kept): a family member without a key runs on the
  owner's key and bill, within the per-person limits.
  `test_docs_say_whose_key_family_research_uses`.
- LOW 8: job files go with the retrying unlink (`family._unlink`), and a
  failed prune never stops a new job. `test_a_failed_prune_never_stops_a_new_job`,
  `test_prune_retries_a_file_windows_holds`.

### Deep research, judge round 2 (2026-10-30)
The four LOW items, each with a test that fails with its fix reverted:
- N1: a research step that fails before anything was found fails the job
  (no empty report, no History conversation, no push).
  `test_a_failed_first_step_fails_the_job`.
- N2: tests for the two untested branches: `MissingKey` in step 2 fails
  the job with `no_key` and saves nothing
  (`test_a_missing_key_mid_research_fails_the_job`); a cancel followed by a
  failing step stays a cancel, saves nothing and logs no failure
  (`test_cancel_then_a_failed_step_is_cancelled`).
- N3: a report saved while a cancel landed is removed with
  `history.delete_conversation` (its recall vector too); the cancel-in-save
  test checks `recall.delete`.
- N4: `ResearchRunner.run`'s docstring and SECURITY's "Failing safe" say
  a failed step fails the job when nothing was found.

## Camera in voice mode (2026-10-30)
A **Camera** button in the phone app's hands-free voice view: the back
camera's live picture on screen, and each spoken question carries one frame
of it, so Kwil answers out loud with the picture in context ("what does
this sign say?"). **Send this frame** freezes the picture for the next
question.

- **Page** (`web/voice-core.js`, `web/voice.js`, `index.html`, `voice.css`):
  pure helpers `frameSize` (long side at most `camera_max_side`, aspect
  kept, never upscaled), `encodeFrame` (JPEG at quality 0.8, 0.7, 0.6, 0.5
  until it is under `camera_max_bytes`, else rejected; the turn goes
  without it), `packUtterance` (one binary message: header with
  `"image": n`, the PCM, then the JPEG) and `CameraPolicy` (off: nothing;
  a pinned frame once; never with the answer to a yes/no question). The
  camera is a video-only `getUserMedia` inside the button's tap (the mic is
  never asked again), shown with `srcObject`; it stops on a second tap, in
  the background, at End and when its track ends. Utterances go out in
  order even while a frame encodes. A denial says to close and reopen Kwil.
  English and Chinese strings (`i18n.js`). `sw.js` `kwil-shell-v21`.
- **Server** (`voice_ws.py`): `split_image` checks `n` (a positive integer
  within the payload: else 1003; over `[voice] camera_max_kb`: 1009;
  `[voice] camera = false`: 1008). The message cap grows by the camera cap
  only; the words are still held to `max_utterance_s`. `ready` tells the
  page `camera`, `camera_max_side` and `camera_max_bytes`. Only the byte
  count is logged at INFO. `Permissions-Policy` is now `camera=(self)`.
- **Turn** (`voice_session.py`): `VoiceTurn.image`; on the worker, after the
  transcript and the person's limits, `attachments.sniff` (JPEG only) and
  `tutor.prepare_image` (re-encoded, EXIF dropped, at most
  `camera_max_side`), then an image block to `backend.ask(...,
  attachments=, camera_keep=)`. An answer to a pending question, a
  superseded, cancelled, refused or failed turn drops its frame. `WebBackend`
  goes through `WebAssistant.chat_stream(attachments=, camera_keep=)`,
  which uses `_ask_with_files` (a 400 is "Claude couldn't read that file.").
- **Chat** (`chat.py`): frames are attachments (taint, `[photo]` in saved
  history, the 20 MB budget). New: `ask(camera_keep=N)` keeps only the
  newest N camera frames attached (`_drop_old_frames`, tracked by identity
  in `_camera_blocks`, put back if the turn fails, cleared by `reset()`);
  "+" photos are never counted. API blocks carry no extra key.
- **Config:** `[voice] camera, camera_max_side, camera_max_kb,
  camera_keep_frames` (DEFAULTS, config.toml).
- `tests/contract_fakes.py` now checks image blocks against the SDK's
  `ImageBlockParam` / `Base64ImageSourceParam` (no extra field, a known
  media type, bytes that match it).

**Tests:** `tests/test_voice_ws.py` +10 (14 cases; a Pillow JPEG reaches Claude as an
image block, re-encoded, no EXIF; over cap 1009, bad lengths 1003,
camera off 1008; the cap grows by the camera only; an answer drops its
frame and is still a yes; a frame makes a `confirm_if_untrusted` tool ask;
INFO has only the size; after two camera turns only the newest is live and
a "+" photo stays; saved history has `[photo]` and no base64; the page's
packing matches `voice_ws.unpack` + `split_image` via node;
Permissions-Policy in the CSP test). `tests/test_voice.py` +9 (10
cases; VoiceConversation and `ClaudeChat.ask(camera_keep=)`).
`tests/test_contract.py` +1 two-turn camera conversation (offline fake, and
`live` variants), +4 fake refusal cases. `tests/test_docs.py` +1 (config
keys mirrored, README names them). JS: `voice-core.test.js` +6 (48, run by
pytest and now by CI's node job too), `i18n.test.mjs` +1. Each new test
was run with the source changes reverted: 26 Python tests and the 7 new JS
tests failed, the rest are controls (a tool that runs without asking when
no frame was sent) or check the fake itself.
Full suite: 3988 passed, 53 skipped (baseline about 3958 passed); JS
`tests/js/*.test.mjs` 364 passed (baseline 363), `voice-core.test.js` 48
(baseline 42). Frame sizes and timing on a real iPhone not measured (no
hardware here): MANUAL_TEST "Voice conversation on the phone".

### Camera in voice mode, judge round 1 (2026-10-30)
- MED 1: leaving the app while a frame was still encoding sent the
  "interrupt" before its utterance, so the server ran a turn the page had
  dropped. Everything the page sends now goes through one
  `VoiceCore.OrderedSender` (in order; at once when nothing waits), and a
  delayed utterance is sent only if `TurnController.stillWanted(turn)`
  (not cancelled, not paused or ended). Tests (`voice-core.test.js`):
  `leaving the app while a camera frame encodes: that utterance is never
  sent`, `a frame that finished first: the utterance goes, then the
  interrupt`, `OrderedSender sends at once when nothing waits, and keeps
  the order`. All three fail with the source reverted; the first also
  fails with only `stillWanted` made to return true.
- LOW 2: a `camera_max_kb` or `camera_max_side` of 0 or less is "camera
  off" (`voice_ws.camera_on`: `ready` says `camera: false`, frames are
  refused with 1008); the page uses `??` and hides the button for a 0 cap.
  `test_a_camera_cap_of_zero_turns_the_camera_off` (3 cases) fails with the
  source reverted and with `client_settings` / `split_image` back on
  `[voice] camera` alone.
- Both node-driven pytest tests skip cleanly without node ("node is not
  installed"), checked with node off the PATH.
- Full suite: 3991 passed, 53 skipped (round 0: 3988); JS `*.test.mjs` 364,
  `voice-core.test.js` 51 (round 0: 48).

## Settings as one grouped page (redesign phase 2, 2026-10-30)
- The gear no longer opens a popover: `<details class="menu">` (kept, with
  `<summary aria-label="Settings">`, which twelve scripts and the JS tests
  find) now opens a full-screen page like iOS Settings. A large "Settings"
  title (`#settings-title`) and a **Done** button (`#settings-done`), then
  eight sections in this order, each a `<div class="set-sec">` with a
  `.group-head` over one `.group` of `.row`s: Voice (phone voice, mute,
  reply voice), Memory and projects, Notifications (enable, Reminders, the
  push status as a `.group-foot`), Connections (unchanged), Privacy and
  data (Incognito, a family member's own export/delete), People
  (unchanged), Backends (the fieldset; its legend is the head), Your
  account (sign out, sign out everywhere). No About section.
- Markup, CSS and i18n only, plus `ui.js`: every id, endpoint, script and
  show/hide rule is as before; google-connect stays before notion-connect,
  `#connections` before `#api-key`, the script order unchanged.
- CSS (`app.css`): the popover rules (`.menu-body > ...`, the `pop`
  keyframes, the bands behind Connections and People) are gone; a new
  "Settings page" block after the voice picker's: `position: fixed;
  inset: 0` on `--bg` with all four safe-area insets, a 40rem centred
  column, no shadow/border, tokens only (light and dark), inset hairlines
  between rows, `.row-chevron` on rows that open another page, a section
  with no visible row hidden through `:has` (child combinators, so the
  Connections rows hidden inside their own list don't count), the page
  behind it locked (`html:has(.menu[open])`), 44px targets, a focus ring
  on Done, and a reduced-motion path for its fade.
- `ui.js`: opening the page focuses the title (VoiceOver starts there);
  Done closes it like Esc, and both give focus back to the gear.
- i18n: 记忆和项目, 通知, 隐私和数据, 你的账户 (Done, Settings, Voice,
  Backends and People were there). "Account" is never a head: it is the
  invite form's field (账户类型).
- `sw.js` cache `kwil-shell-v22`.
- Tests: `tests/js/settings-page.test.mjs` (4: the heads in order; each
  stable id inside its own section and only there; title and Done above
  the sections; every head, Done and Settings in Chinese and no head
  "Account"); `tests/js/ui.test.mjs` +2 (opening focuses the title, Done
  closes and focuses the gear; Esc focuses the gear);
  `tests/test_settings_page.py` (2: 70 stable ids each exactly once inside
  the menu; the order constraints); `tests/test_theme.py` +1 (the settings
  block: no hex/rgb/`--shadow`, fixed with the four safe-area insets, the
  40rem column, the `:has` rule, the scroll lock, Done's 44px and ring,
  reduced motion, the old popover and bands gone). With the four source
  files reverted to ccfbde4 all 9 fail; with only `i18n.js` reverted the
  Chinese test fails, with only `ui.js` reverted both ui tests fail.
- Screenshots (Playwright, Chromium in the sandbox, not a real iPhone):
  390x844 and 1280x860, light and dark, English and Chinese. Focus was on
  the title after opening, `html` overflow hidden while open, and after
  Done the menu was closed with focus on the gear, in all eight runs.
- Full suite: 3994 passed, 53 skipped (ccfbde4 collects 3 fewer: 4044 vs 4047); JS
  `tests/js/*.test.mjs` + `voice-core.test.js` 422 passed (baseline 416).
  Safe areas, VoiceOver and the scroll lock on a real iPhone: MANUAL_TEST
  "Settings page".

## One-row composer (redesign phase 4, 2026-10-30)
Kyle wants speed: the chat's bottom is now one row, like the Claude app:
`[+] [field, 1 to about 6 lines] [mic, or Stop while busy] [Voice
conversation, or Send when there is something to send]`. Layout and
interaction only; nothing on the server changed.
- `index.html`: `#text` is a `<textarea rows="1" enterkeyhint="send"
  aria-label="Message">`; the mic and Voice conversation (now an icon
  button) moved into `#text-form` in the order attach, text, stop, mic,
  voice-open, send; every button but Send is `type="button"`. The project
  and study pills left the header for a `#composer-modes` line above the
  row, with a new `#think-chip` ("Think harder is on"; tapping it turns
  think harder off). `#think` (same markup) and `#study-toggle` are now
  rows in the "+" sheet after the pickers, before Cancel, with
  `data-close`. `#mic-meter` and `#mic-hint` (`role="status"`) show only
  while recording. Every listed id is kept.
- `app.js` `syncComposer()` (also `window.KwilApp.syncComposer`): Send
  shows when the field has words, files are picked (not while a yes/no
  question is open) or an edit is open, else Voice; the mic hides while
  Stop shows; a control that hides while focused hands focus to the field.
  Called on input, after every submit, in startEdit/cancelEdit, setBusy
  (so setConfirm), and when the tray changes. Enter sends unless Shift is
  held or an IME is composing (`isComposing`, or keyCode 229 on Safari).
  The field grows with CSS `field-sizing: content`; a script fallback sets
  its height only where `CSS.supports` says no. `visualViewport` (guarded)
  sets `--app-h`, toggles `html.kb-open` and undoes Safari's page scroll.
  The think chip follows `setThink`; the long press on Send and the mic's
  tap/hold are unchanged.
- `attachments.js`: at 4 files the sheet's pickers are disabled, not "+",
  so Think harder and Study mode stay reachable; `[data-close]` rows close
  the sheet; `render()` tells app.js (`opts.changed`).
- `app.css`: the row (`align-items: flex-end`, `--r-lg` card), the textarea
  overriding the generic one (no border/ring, `field-sizing`, a ~6-line
  max-height, `resize: none`, scrolls), the 84px mic and its 700px rules
  gone (a 44px line mic, `--bad` with a ripple while recording), Voice a
  44px accent circle with the waveform mask, `#send` with no callout or
  selection, `.app { height: var(--app-h, 100%) }`, less footer padding
  under `html.kb-open`, the modes line hidden when empty (`:has`), the
  sheet's toggle rows, and a reduced-motion line. Tokens only.
- `ui.js`: a swipe down that starts in a `#text` taller than its box (it
  scrolls) doesn't close the keyboard.
- i18n: 消息, 已开启仔细想想, 关闭仔细想想. `sw.js` cache `kwil-shell-v23`.
- Tests: new `tests/js/app-composer.test.mjs` (9: Send/Voice swap with
  the text; Enter sends, Shift+Enter, `isComposing` and keyCode 229 don't;
  busy shows Stop and hides the mic, a question does the reverse; files
  alone show Send, never for an answer; an edit shows Send; a long press
  still sends with think on and the chip hides after, the chip turns it
  off and gives focus to the field; a hidden control's focus goes to the
  field; visualViewport sets `--app-h`, `kb-open` and scrolls back; the
  markup and its order); `attachments.test.mjs` +1 and the tray test
  updated (pickers disabled at 4, "+" still opens, data-close rows close,
  app.js told of changes); `ui.test.mjs` +1 (a swipe inside a scrollable
  field doesn't blur it); `i18n.test.mjs` +1 (the new strings; the
  textarea's placeholder and aria-label). The pins kept: the queue and
  Stop markup, the think and Send markup, "+" before the field,
  `id="voice-open"`. With `app.js`, `attachments.js`, `ui.js`, `i18n.js`
  and `index.html` reverted to 451a8d2, all 9 composer tests, the 2 tray
  tests, the ui test and the i18n test fail.
- Screenshots (Playwright, Chromium in the sandbox, not a real iPhone):
  390x844 and 1280x800, light and dark, English and Chinese, empty and
  typed, plus a 10-line message (it stops at 6 lines and scrolls), the
  modes line with all three pills, and the sheet in both languages.
- Full suite: 3994 passed, 53 skipped (unchanged from 451a8d2: no Python
  tests added, only the cache pins moved to v23); JS `tests/js/*.test.mjs`
  + `voice-core.test.js` 434 passed (baseline 422). The keyboard, the
  safe area, the Chinese IME and the long press on a real iPhone: MANUAL_TEST
  "The one-row composer".

### One-row composer, judge round 1 (2026-10-30)
- HIGH, fixed: the attachment chip rules (`.attach-chips`, `.attach-chip`,
  its `img`, the photo placeholder, `.attach-name`, `.attach-remove`) had
  been cut with the old composer block, so picked files stacked as 96x72
  images and pushed the chat up. Restored from 9935872 into the composer
  section (`.5rem` -> `var(--s2)`). Pinned by an `attachments.test.mjs`
  CSS test. Measured in Chromium at 390x844 with 2 photos and a PDF:
  56x56 tiles in one 60px-high row.
- LOW, fixed: pinch-zoom no longer squashes the app. While
  `visualViewport.scale` isn't 1, `--app-h` and `kb-open` are removed and
  the page isn't scrolled back.
- LOW, fixed: the comments in `app.js`, `study.js` and `projects.js` that
  still placed the pills and the study button in the header.
- Asked for too: Send's `pointerdown` prevents the default, so focus stays
  in the field and the iPhone keyboard stays up after Send. Checked in
  Chromium with a mouse and with touch: a tap sends once with focus still
  on `#text`, and a 650 ms hold sends once with think on.
- Tests: `attachments.test.mjs` +1 (the chip CSS), `app-composer.test.mjs`
  +3 (pinch-zoom with scale 2; Send's pointerdown is prevented while a tap
  and a long press each still send once; no "header" left in the three
  scripts' comments). Each failed before its fix.
- Full suite: 3994 passed, 53 skipped; JS 438 passed (was 434).

## Today screen (redesign phase 5, 2026-10-30)
- **What:** a read-only, per-person **Today** view. The header's new
  calendar button (`#open-today`, before History) or `/#today` opens it;
  `/api/state` says `"today": true` for everyone listed. Sections: the
  brief (opens `#brief`), today's and tomorrow's events on the primary
  calendar ("Ask Kwil"), what's due by tomorrow or overdue (deadline store,
  at most 14 days back, classes left out; Notion `list_assignments`
  days_ahead=1; opens Settings), today's reminders (opens `#reminders`),
  the latest 3 research jobs (a finished one opens its conversation; a
  running one has a breathing `.state` dot) and the 5 most missed study
  concepts ("Ask Kwil"). At most 10 rows a section.
- **Server:** `kwil/today.py`, `GET /api/today` (registered after
  `proactive_mod.register`). One `chat._now(tz)` reading, set as
  `timeutil.TURN_NOW` while the sections run; calendar and Notion run side
  by side in a 4-thread pool (`contextvars.copy_context().run`, 8 s wait
  each). The calendar handler is called directly (never `dispatch`), and
  skipped with state `reconnect` while `google_auth.needs_reconnect`.
  `outside` = the event's `from_others` or the result's untrusted mark;
  every deadline and Notion row is outside; reminders and study rows by
  their `untrusted` flag. Each section is `{state, items}`, and one
  failure stays in its section. 401 signed out, 404 for a member removed
  mid-request, `Cache-Control: no-store`. INFO logs one line of section
  names, states and counts; warnings carry exception type names only.
- **Client:** `kwil/web/today.js` (`KwilToday = {configure, onState,
  setup, question, TEXT}`), everything by `textContent`. Reads only when
  the view opens; offline shows a line and Retry. "Ask Kwil" calls
  `newChat()` then app.js's `prefill` (text in `#text`, `syncComposer()`,
  focus); it never sends. An outside item's question names it by kind and
  time only. Chinese for every fixed string and question (`i18n.js`; the
  question templates are translated before the time/title go in). CSS in
  `app.css` (tokens only, light/dark, reduced motion); `sw.js`
  `kwil-shell-v24` with `/today.js`; `webapp.py` static route and
  `cloud.py` `PUBLIC_GET`.
- **Tests:** `tests/test_today.py` (14): 401 signed out; the owner's full
  screen and no-store; invites outside and own events not, every deadline
  outside; Notion outside with days_ahead=1; a family member sees only her
  own and a removed one gets 404; caplog (no titles or text at INFO);
  a calendar error, a sign-in error, a timeout and `needs_reconnect` stay
  in the calendar section (and `needs_reconnect` makes no Calendar call);
  a broken deadline store stays in its section; one clock reading across
  midnight (23:59:59 then 00:00:01: one `_now` call, Thursday's and
  Friday's items under the right day); only `events().list` on `primary`
  and no taint on the open chat; rows capped at 10; off sections say off.
  `tests/js/today.test.mjs` (11): the button only with `today`, no read
  until opened; textContent rendering of every section; Ask Kwil prefills
  without sending and an outside row's question has no title; rows open
  their places; `/#today` opens and Back returns; off/reconnect/empty
  lines and actions; offline + Retry; Chinese page; zh for every TEXT
  string; page/app.js/sw.js wiring. Fail-first: 9 source mutations of
  `today.py` (outside forced off/on, a title in the INFO line, no
  reconnect skip, no per-section try, a second clock read, every
  calendar, no 404, no cap) each fail their test; 10 of `today.js`,
  `i18n.js`, `index.html` and `sw.js` each fail theirs. The no-store
  assertion also holds without the route's header (the app's middleware
  sets it on every `/api/` answer), so it is a guard, not a regression
  test.
- **Screenshots** (Playwright Chromium with a mocked `/api/today`, not a
  real iPhone): 390x844 and 1280x900, light and dark, English and Chinese,
  top and scrolled to the end, plus empty (Google not connected) and
  offline.
- Full suite: 4008 passed, 53 skipped (baseline 3994 + 14); JS
  `tests/js/*.test.mjs` + `voice-core.test.js` 449 passed (baseline 438 +
  11).

### Today screen, judge round 1 (2026-10-30)
- MED, fixed (1): slow reads are bounded. Calendar and Notion each run on
  a thread of their own (`today.Reads`), never queued, so nothing waits to
  start after its screen gave up; one shared wait of `SLOW_SECONDS` (8 s)
  for both. Per person: one screen in flight (`today.Gate`; a second
  request gets the same answer), one calendar and one Notion read alive
  (a new screen while one hangs shows that section as an error, no second
  call), 20 new screens a minute (429). At most 16 reads alive in all.
  SECURITY.md reworded.
- LOW, fixed (2): reminders "today" runs local midnight to midnight (23-
  and 25-hour days).
- LOW, fixed (3): the calendar is capped 10 a day, deadlines and Notion 10
  each (the page no longer cuts the merged list); `more` when cut.
- LOW, fixed (4): times and dates go as local ISO and the page words them
  (`toLocaleTimeString`): "3:00 PM" in English, "15:00" in Chinese, in rows
  and in Ask Kwil's question. Notion rows show Today / Tomorrow / Overdue
  like deadlines.
- LOW, fixed (5): each Ask Kwil has `aria-describedby` pointing at its
  row's title and time.
- LOW, fixed (6): the family test gives the owner a calendar tool with a
  distinctive event; the member never sees it (fails with
  `registry = assistant.tools`).
- LOW, fixed (7): README's Ask Kwil wording.
- Tests: `test_today.py` +7 (clock-change days; one person's hung Notion
  vs another's calendar; one shared wait; a second request joins the one
  in flight with one Calendar call; a hung calendar isn't asked again; 429
  past the rate; a full today keeps tomorrow) and 3 updated (ISO times,
  Notion day, `more`, the owner's event in the family test); with
  9912ce7's `today.py` 10 fail. `today.test.mjs` +3 (no AM/PM on a
  Chinese page; deadlines and Notion each keep their rows; each Ask Kwil
  names its row) and 4 updated; with 9912ce7's `today.js` 7 fail.
- Full suite: 4015 passed, 53 skipped (was 4008); JS 452 passed (was
  449). zh phone screenshot retaken (Chromium, not a real iPhone): times
  read 15:00, 23:59, no "PM".

### Today screen, judge round 2 LOWs (2026-10-30)
- (1) `wallTime` builds and prints in UTC, so a time in the phone's own
  clock-change gap (2027-03-14 02:30 in New York) stays 2:30.
- (2) An event that started before today comes with `continues` and no
  start time: "until 1:00 AM" (timed) or "All day", and Ask Kwil says
  "...which continues today" with no time (zh too).
- (3) A request joining the screen in flight waits with
  `asyncio.wait_for(shield(wrap_future(...)))` on the event loop, no
  worker thread; a timeout or the first request's failure or cancellation
  is a plain 503; at most `MAX_WAITERS` (4) wait per person, then 429. The
  page shows a 429 as "Too many requests; try again in a minute." (zh too).
- (4) A test for the all-people cap (`Reads(limit=2)`: a third person is
  refused, then gets a slot once one ends).
- (5) `Reads.start` removes its entry and fails the future if the thread
  can't start.
- Tests: `test_today.py` +5, `today.test.mjs` +3. With 44db255's
  `today.py`, `today.js` and `i18n.js`, 4 Python and 3 JS of them fail;
  the cap test (4) passes there (the code was right, only untested) and
  fails with the cap check removed.
- Full suite: 4020 passed, 53 skipped (was 4015); JS 455 passed (was 452).

## Phone tab bar (redesign phase 6, 2026-10-30)
- **What:** below 48rem the phone app has a fixed bottom bar, Chat ·
  Today · History · Settings (`<nav id="tabbar" aria-label="Main
  navigation">`, four `button.tabbar-item`s with a 22px line glyph and a
  label; no `role=tab`). It shows only on the root screens (the chat with
  its Settings page, Today, History), never on a screen reached with Back
  (transcript, Memory, Projects, a project, Reminders, the brief) or the
  sign-in, and CSS hides it with the keyboard (`html.kb-open`), in voice
  mode (`body.voice-open`) and at 48rem and wider, where nothing changes.
  On a phone the header's calendar, clock and gear and the Back of Today
  and History are hidden. No backend change; Lecture and Tutor stay in
  the segmented control.
- **Client:** `app.js` `syncTabs(view)` after every `show()`, after the
  `onState` calls in `refreshState` and on the menu's `toggle`: the bar's
  `hidden`, exactly one `aria-current="page"` (Settings while the menu is
  open), and Today/History mirror `#open-today`/`#open-history`'s
  `hidden`. Each tab works through the header's own control: Today and
  History click their buttons (closing Settings first), Settings shows the
  chat and opens the menu, Chat closes the menu and shows the chat without
  focusing the field; from Today every tab goes through `#today-back`
  first, so a `/#today` link is cleared. The current tab does nothing. `ui.js`: a
  pointerdown inside `#tabbar` doesn't close Settings; Done/Esc focus the
  Settings tab on a phone (`matchMedia("(max-width: 47.99rem)")`, guarded),
  else the gear. CSS (`app.css`, `--tabbar-h: 49px` incl. the hairline;
  tokens only, light/dark, reduced motion): while the bar shows, `.app`,
  `.history`, the Settings page and `.panel` (`features.css`) leave room
  for it and the composer drops its own safe-area gap. `i18n.js` zh
  "Main navigation" 主导航; `sw.js` `kwil-shell-v25`.
- **Tests:** `tests/js/tabbar.test.mjs` (10): aria-current per root view;
  hidden on every Back-reached view and login; Settings current while the
  menu is open; the Settings tab opens the menu (from History and Today,
  via Today's Back); Chat closes the menu and never focuses `#text`;
  Today/History click their header buttons and the current tab does
  nothing; tab-today/tab-history follow `/api/state`; markup (order,
  hidden, no tab roles, body level, ids kept); the CSS hide/room rules;
  zh. `tests/js/ui.test.mjs` +2: a tap in the bar keeps Settings open;
  Done focuses `tab-settings` on a phone and the gear otherwise (wide,
  bar hidden, tab hidden, no matchMedia). Fail-first: with 4d00c12's
  `app.js`, `ui.js`, `index.html`, `app.css`, `features.css` and `i18n.js`
  all 12 fail. Cache pins moved to v25 in six test files.
- **Screenshots** (Playwright Chromium, mocked APIs, not a real iPhone):
  390x844 and 1280x800, light and dark, English and Chinese, on Chat,
  Today, History and Settings, plus the keyboard open (bar hidden) and a
  transcript (bar hidden). Measured in the page: the bar is 49px, hidden
  (`display: none`) on desktop, with kb-open and on the transcript.
- Full suite: 4020 passed, 53 skipped (baseline 4020; no Python change,
  only the v25 pins); JS 467 passed (baseline 455 + 12).

### Phone tab bar, judge LOWs (2026-10-30)
- (1) Every tab, not only Chat and Settings, leaves Today through
  `#today-back` first (`leaveToday()` in `onTab`), so History from Today
  clears `/#today` too and setting `#today` again opens it.
- (2) Done/Esc with the keyboard up: the bar is `display:none`
  (`html.kb-open`) but not `[hidden]`, so the tab has no client rects and
  can't take focus. `ui.js` now blurs the field and focuses the Settings
  tab once it is drawn again (a `visualViewport` resize or a
  `requestAnimationFrame` check, at most about 120 frames; not if Settings
  was opened again meanwhile); with neither API, the gear as before.
- (3) MANUAL_TEST History section: "Settings → Incognito", "the tab bar and
  the dialogs work".
- Tests: `tabbar.test.mjs` +1 (History, Chat and Settings from Today each
  click Today's Back once), `ui.test.mjs` +1 (no rects: gear without
  rAF/visualViewport; rAF focuses once drawn and stops; resize focuses and
  removes its listener; reopened Settings keeps focus; gives up). With
  0bd3cdd's `app.js` and `ui.js` both fail.
- Full suite: 4020 passed, 53 skipped (unchanged); JS 469 passed (was 467).

## Copy and polish pass (redesign phase 7, 2026-10-30)
A copy, Chinese and small-visual pass over `kwil/web`; no API or server
change, every id and hook kept.
- **Bugs:** Reminders' list head said "Active" (People's word, 已加入 in
  Chinese): now "Upcoming" (即将提醒). No hairline above "New project"
  with no projects (`.projects-list:not([hidden]) + .projects-new`).
  Memory: space between a card's footnote and the next card
  (`.mem-local .group-foot + .group`); "Long-term notes" no longer shows
  History's clock empty state (that rule is now `#history-list:empty`),
  and `#memory-status` reads like a `.group-foot` (left, `--t-cap`,
  `--muted`, under its head). The first-run guide says "tap Settings"
  (点“设置”) instead of the gear at the top right, which the phone no
  longer has.
- **Copy:** no exclamation marks ("Welcome." / 欢迎。); "Notes in
  Pinecone" → "Long-term notes" (长期笔记); the "Untrusted" badge →
  "Outside source" (外部来源) in Memory, Reminders and Projects; one-line
  empty states "No reminders yet." and "No projects yet." (the examples
  stay in the footers); "This browser can't record here. Open Kwil from its
  https:// address."; "For example, Asia/Shanghai"; "This app's address";
  "Leave empty to use the address the server suggests.". Every ZH key is
  the new English byte for byte.
- **Chinese gaps:** "Course files" 课程文件, "Source" 来源, "This is the
  conversation you're in now." 这就是你现在所在的对话。, "Disconnect this
  service?" 要断开这个服务吗？. A garbled `"Auto"` key (a merge leftover;
  the real "Auto: Pi, then PC, then cloud" key stays) is removed. History
  rows lose their English-only template aria-label: the row's own text
  (source icon label, title, "Pinned", time) names it, all translated.
- **Accessibility:** while Settings is open, `ui.js` sets `inert` on
  `#app > :not(header)` (the tab bar is outside `#app`), cleared by Done,
  Escape and the menu's toggle; guarded for a document without
  `querySelectorAll`. The invite form's language options carry
  `lang="en"`/`lang="zh"`.
- **Visual and tokens:** a History row with no snippet centres its icon;
  day heads look like `.group-head` (`--t-cap`, 500, `--muted`); both sheet
  backdrops read `var(--scrim)` (the Safari < 17.4 literal fallback on
  `.conn-sheet::backdrop` is dropped: raw scrim hex lives only in
  `:root`); the two red buttons' white text is `var(--on-accent)`.
  `sw.js` `kwil-shell-v26`. DESIGN_SYSTEM: "empty state = one line;
  examples live in the footer".
- **Tests:** new `tests/js/i18n-coverage.test.mjs` (10): runs `i18n.js` in
  node:vm and checks every text node, aria-label, placeholder and title in
  `index.html` (script, style, svg stripped) and every string in the
  `TEXT`/`*_TEXT`/`SOURCES`/`KIND` maps translates (allowlist: Kwil,
  Google, Notion, Canvas, Home Assistant, Spotify, Anthropic, WEB_TOKEN,
  .env, sk-ant-…, the console URL, English, 中文 and https:// placeholders;
  an unused entry fails); "Source 1" is Chinese but a reply starting
  "Source of…" isn't touched; no template aria-label in history.js; the
  Reminders head; the renamed strings match their ZH keys; no "!"/"！".
  `ui.test.mjs` +2 (inert on open, cleared by Done and by the toggle; a
  fake DOM without `querySelectorAll`). `test_theme.py` +4: `--text-2` and
  `--muted` ≥ 4.5:1 on bg, surface and surface-2 in light and dark
  (already true: a guard); scrim hex only in `:root` and `--on-accent` on
  the red buttons; the P1 selectors. Fail-first at c6df6bd: 7 of the 10
  coverage tests, the inert test, and 2 of the theme tests fail; the
  history test fails again with only c6df6bd's `history.js` restored.
  Pins moved: v26 in six test files, "Outside source", "Long-term notes",
  "Upcoming", 欢迎。.
- **Screenshots** (Playwright Chromium 390x844, light, mocked APIs, not a
  real iPhone): Memory, Reminders, Projects and History in English and
  Chinese, before and after.
- Full suite: 4024 passed, 53 skipped (baseline 4020 + 4); JS 481 passed
  (baseline 469 + 12).

## Beta starter credit and feedback (PR-A, backend, 2026-10-31)

- **Starter credit** (`kwil/credit.py`, `CreditLedger`): a beta tester
  without their own key now runs on the owner's key until they have used
  `[family] beta_credit_usd` ($5), all testers together at most
  `beta_credit_total_usd` ($50); then `userkeys.CreditSpent` (a MissingKey)
  and the new i18n `credit_spent` / `credit_spent_help` sentence (en, zh;
  the key guide's link when `[webapp] public_site` is on and the origin is
  known), in text, voice (`cli.user_error_text`) and files. Hook:
  `Family.api_of(uid, credit)` (`api_for` keeps its two values); a call on
  the credit goes through `family.CreditMessages`, which reserves that
  request's worst case (`credit.request_bound`; with web search and fetch
  on about $1.80 on Sonnet, $0.46 on Haiku for a short chat, measured in
  the sandbox) and settles it in a `finally`: the response's usage, a
  cut-short stream's snapshot input plus its whole `max_tokens`, the whole
  reservation with no usage, nothing for an HTTP error or a connection
  never made. Everyone's cap must fit whole reservations; a person needs
  something left and at most 2 calls in flight (`MAX_IN_FLIGHT`), so one
  person can be over by at most those calls' cost. A reservation unsettled
  after `api_timeout * (api_max_retries + 1)` (120 s) is charged in full.
  An unpriced model (model, fast_model, title_model) turns the credit off;
  `call_cost` prices one at the dearest known rates. Book:
  `<dir>/beta_credit.json` (`write_private`, under a lock); Remove drops a
  row, never the total. An unreadable book fails closed. Never on
  the credit: deep research (`credit=False`), briefings (proactive.py) and
  "Sync now"'s email extraction (student_web.py). Unchanged: paid voices,
  quotas. `key_status` adds `credit_usd`/`credit_left_usd` (beta, credit
  on); `/api/users` adds each person's `credit_used_usd` and
  `credit_total_used`/`credit_total_cap`. `beta_credit_usd = 0` is off.
- **Feedback** (`kwil/feedback.py`): `POST /api/me/feedback`, `POST
  /api/me/feedback/dismiss`, owner-only `GET /api/feedback`; sealed per
  person (`kwil/feedback/v1`) in `<dir>/<id>/feedback.enc` (the owner's in
  `[connect] dir`), in the export as `feedback.json`, deleted with the
  folder; 8 KB, 2,000 characters, 50 notes, 5 an hour; INFO id/kind/lengths
  only. `/api/state.feedback_due` for a beta tester after
  `feedback_after_days` (3) since `joined` (new, set at redeem) or
  `feedback_after_conversations` (10); answered or dismissed (users.json
  `survey`) ends it. `/api/state` also gets `notion_template_url` and
  `beta_chat_url` when https.
- **Invite text:** `POST /api/invites` and a new link answer with `message`
  (i18n `invite_family`, `invite_beta`, `invite_credit`, `invite_template`,
  `invite_chat`; the invitee's language). config.toml carries Kyle's
  published Notion template link; the DEFAULTS value stays "".
- **Round 3 (judge):** credit calls run on `with_options(max_retries=0)`
  and CreditMessages retries itself (the SDK's rules: timeouts, connection
  errors, 408/409/429/5xx, `x-should-retry`, `retry-after`; 0.5 s doubling
  to 8 s with jitter), each try admitted and settled; on the credit only
  `messages` / `count_tokens` / `with_options` work (anything else raises);
  `UserApi`, `api_for` and `client(uid)` default to `credit=False` and
  `_build`'s chat and title UserApi pass `credit=True` (reminders use that
  factory); `admit` refuses (CreditSpent) only when money used blocks, and
  otherwise waits on the Condition for calls in flight.
- **Tests:** new `test_credit.py` (29), `test_feedback.py` (9),
  `test_invite_message.py` (5); +1 each in `test_research.py`,
  `test_voice_picker.py`; `test_userkeys.py`'s app is now `build_env`
  (credit off in its fixture); `test_proactive` and `test_student_web`'s
  beta tests also check the ledger is untouched; `test_docs` allows only `notion_template_url` to differ from DEFAULTS.
  Fail-first: with the source changes reverted (new modules kept), 30 of
  the new/changed tests fail; the pure ledger tests need `credit.py`
  itself.
- Full suite: 4069 passed, 53 skipped (baseline 4024 + 45); JS 481 passed
  (unchanged; no web files touched).
## Beta onboarding UI (PR-B, help page and web UI, 2026-10-31)

- **Key guide** `/help/anthropic-key` (`kwil/web/help-anthropic-key.html`,
  site.css; in `public_site.PAGES` and `cloud.PUBLIC_GET`, so it needs no
  sign-in and 404s with the public site off): the console steps (account,
  Billing -> add credit, monthly spend limit, API keys -> Create key, paste
  into Settings -> Connections -> Anthropic), five `<!-- SCREENSHOT -->`
  spots for Kyle, and a cost estimate from usage.PRICES (Sonnet, cache
  read $0.20): about $0.02 a message, $3 / $6 / $18 a month at 5 / 10 / 30
  a day. English only, like /privacy. `/api/state` gets `key_help` (the
  path) when the public site is on, the only backend change.
- **Settings:** a beta tester on the credit sees "$X.XX of $5 starter
  credit left" (and as the Anthropic row's subtitle, unflagged until it is
  used up) and the guide link; the owner's People rows show "$1.20 used"
  per tester and "Starter credit: $12.40 of $50 used" under the card.
- **Invites:** the share sheet gets `{text: message}` (the message holds the link; a separate `url` showed it twice); the clipboard
  copy is the message (the link alone when the server sent none).
- **First-run checklist** (i18n.js `renderChecklist`, beta testers only,
  called from family.js on every `/api/state`): connect Canvas or Notion
  (the template link), ask "What's due this week?", voice mode, and the
  testers' chat when set; ticks and Hide in localStorage (try/catch). At
  most one card over the chat: the survey, then the guide, then this.
- **Feedback** (family.js `setupFeedback`): "Send feedback" (Settings ->
  Your account) opens a bottom sheet with three labelled textareas; the
  one-time survey card (Answer / No thanks -> `POST
  /api/me/feedback/dismiss`); the owner's "Feedback" row in People lists
  `GET /api/feedback`, newest first, set with textContent only. 429, 409,
  413 and 503 each get their own sentence; answers are kept on a failure.
- zh for every new string (three new PATTERNS for the dollar lines); 44px
  targets, labelled fields, focus back to the opener; tokens only, light and
  dark. sw cache `kwil-shell-v30` (six pins updated).
- **Tests:** new `tests/js/onboarding.test.mjs` (17), +1 in
  `i18n-coverage.test.mjs`, +4 in `test_public_site.py` (the guide is
  public, its steps/links/estimate, the price table behind it, `key_help`)
  and `/help/anthropic-key` added to its public/404 lists. Fail-first: with
  family.js, i18n.js and index.html reverted all 17 JS tests and the
  coverage test fail; with the route wiring reverted the 6 public-page
  tests fail, and with webapp.py reverted the `key_help` test fails.
- Full suite: 4105 passed, 53 skipped (baseline 4097 passed, 54 skipped
  in a clean archive of 1d3b0cf); JS 536 passed (baseline 518).
  Screenshots (390x844, light/dark, en/zh, mocked APIs) were reviewed in
  the sandbox, not on a real phone.
## Google Drive, read-only (2026-10-31)
"Summarize my essay draft": two new private, untrusted-output tools in
`kwil/tools/drive.py`, on the same Google sign-in as Calendar and Gmail.
- **`search_drive(query, max_results<=10)`:** `files.list` with
  `(name contains '…' or fullText contains '…') and mimeType != folder
  and trashed = false` (`\` and `'` escaped, the query capped at 200
  characters), the fields `files(id,name,mimeType,modifiedTime,
  owners(displayName,me),size)`; with no words, the newest files first
  (`orderBy=modifiedTime desc`, which Drive refuses with fullText).
- **`read_doc(file_id, offset=0)`:** 6000 characters a call with
  `next_offset`. Docs and Slides through `files.export_media`
  (`text/plain`), Sheets `text/csv`; uploaded PDF, .docx, .pptx and .txt
  refused over 10 MB by Drive's size, downloaded with a `Range` cap, then
  `textextract.extract`. Anything else, and Google's export limit, get a
  plain "too large" / "can't read this kind of file". File ids are checked
  before they reach Drive.
- **Read-only twice over:** the `drive.readonly` scope (no Docs scope),
  and `ReadOnlyDrive`, an allowlist of `files().list/get/export/
  export_media/get_media`; anything else raises `DriveWriteBlocked`.
  Google's `HttpError` (its text holds the request URL, so the query) is
  replaced by a plain `DriveError` before the registry logs it. INFO says
  counts and lengths only. Both tools are in `REDO_SAFE_TOOLS`.
- **Scope and registration:** `google_auth.DRIVE_SCOPES`,
  `sign_in_scopes(gmail_enabled, drive_enabled=False)`; the sign-in CLI,
  the phone's Connect (`GoogleConnect.drive/scopes`) and the family
  client ask for it with `[drive] enabled` (new, default on). The public
  beta client never does, and `Family.drive_allowed` / `drive_files`
  are family tier only. `cli.register_drive` / `owner_drive` (Gmail's
  pattern): a token without the scope keeps Calendar and Gmail and prints
  `(Drive tools off: … python -m kwil.tools.google_auth …)`, which
  preflight's parser reads; `doctor` has a "Google Drive access" line. A
  Drive call on such a token raises `MissingScopes`; in the app its words
  are the new `drive_reconnect` (en, zh). `/api/google/status` has
  `missing` (the asked-for scopes the saved token lacks); with Drive's in
  it, Settings' Google row says "Reconnect to add Google Drive
  (read-only)" and the button runs the same Connect flow
  (`kwil/web/i18n.js`, en and zh). Disconnect removes the Drive tools
  (`_google_tool_names`). `sw.js` `kwil-shell-v29`.
- **Tests:** new `tests/test_drive.py` (17), on a fake Drive v3 service
  with the real answer shapes and real `HttpError`s; fake tokens expire in
  2099 and `Credentials.refresh` fails the test. Exports and the cap and
  paging, CSV, uploaded text and .docx, too large (never downloaded),
  unsupported, the export limit, 404, bad ids; the query escaping (a quote
  can't close the string), caps and orderBy; only read methods reachable;
  a shared Doc's injection makes calendar and Notion changes ask (6
  cases); nothing of the query, names or text at INFO, nor at DEBUG, nor
  in an error's traceback; an old token (Drive off with the preflight
  line, doctor, `MissingScopes` on the PC and in the app); sign-in scopes;
  beta never asked and never given Drive; status `missing` for the owner
  and a family member; Disconnect removes the tools for both.
  `tests/js/i18n.test.mjs` +1 (the row, the sheet line, zh, the Connect
  flow, and no Drive line for other missing scopes). Status dict pins
  (+`missing`) in test_google_web, test_family_google, test_family and
  test_cloud; test_gmail's sign-in CLI test now expects Drive by default
  and none with `[drive] enabled = false`; sw v29 in six files. Fail-first:
  `test_drive.py` can't import at e5fbb70; each test also fails with only
  its piece of the source reverted (16 one-change reverts, scripted: the
  escape, the allowlist, `untrusted_output`, `private`, the error
  replacement, the cap, the Range header, the scope check, the drive flag,
  both tier gates, `missing`, the Drive tool names, the flow's scopes);
  the JS test fails with `lacksDrive` returning false.
- Full suite: 4041 passed, 53 skipped (baseline 4024 + 17); JS 482 passed
  (baseline 481 + 1). Sandbox numbers, no real Google call.

### Google Drive, judge round 1 (2026-10-31)
- **MED, taint lost when a tool leaves the registry:** `tainted()` reread
  the history with the registry's *current* untrusted tools, and
  `GoogleConnect._register_tools` / `Family._refresh_tools` pop Drive (or
  Gmail) in place after a Disconnect or a Reconnect without the scope, so a
  conversation that had read an injected Doc stopped asking. Fixed
  generically: `ClaudeChat._dispatch` sets the sticky `untrusted_context`
  when the dispatched tool is untrusted at that moment; `ToolRegistry.
  dispatch` adds `"untrusted_content": true` to every dict result of an
  `untrusted_output` tool (a copy, never the handler's dict; once); and
  `outside_content` / `_remember` check web and marked results with an
  empty registry too (they skipped them when `bool(self.tools)` was
  false). Earlier history bytes are unchanged, so the prompt cache holds.
  Tests (+5 in `test_drive.py`): a Drive read, the Drive tools popped, a
  move still asks; the same for Gmail; a family member's turn reads a Doc,
  her reconnect without Drive rebuilds her tools, her chat is still
  tainted; the result carries the mark once and a chat with only that
  history and an empty registry is tainted; a course file sent as
  `search_result` blocks (no mark) still makes a move ask after its tools
  leave. Fail-first (scripted reverts): without the flag the course-file
  test fails; without the mark, or with the empty-registry check reverted,
  the mark test fails; without both, all five fail.
- **LOW:** `sw.js` `kwil-shell-v29` (v27 and v28 are taken on master by
  #103 and #105), six pins.
- Full suite: 4046 passed, 53 skipped (round 0's 4041 + 5); JS 482 passed.
## Cloud-only, stage 1: failover off on the cloud (2026-10-31)
Kyle approved making Kwil cloud-only. Stage 1 is reversible: nothing is
deleted, the cloud just stops being part of a failover setup.
- **`[failover] enabled`** (new, default true; `config.toml` documents it)
  and `SERVER_PROFILE` sets it false. With it false,
  `peers.settings_from_config` returns no role and no backends, whatever
  `[failover]`, `ASSISTANT_ROLE`, `APP_ORIGIN` or `FAILOVER_BACKENDS` say,
  and logs one WARNING per process naming those settings (never their
  values), only while one of them is still set. So `cloud.build_app`
  mounts no `EdgeMiddleware`: no `hc1.` access tokens or
  `/api/auth/token`, no `/api/sync` (404 once signed in), no public
  `/backend-router.js` (401 before sign-in), and `/healthz` has no `role`,
  so the app shows no Backends section. Every other reader of `[failover]`
  (proactive, research, reminders, student_web, family) goes through the
  same function.
- **`history.origin_of`** is "cloud" under the server profile even
  without `ASSISTANT_ROLE` (events would otherwise say "pc").
- **The phone app** (`app.js` `detectFailover`) no longer trusts a cached
  `failover` flag when `/healthz` can't be reached; whenever failover is
  off it removes `failover`, `backendsCache`, `backendSettings` and
  `backendOverride` from localStorage. `sw.js` `kwil-shell-v28` (v27 is
  the desktop-layout branch's). The Backends fieldset in `index.html`
  stays until stage 2.
- **Render:** `render.yaml` drops `ASSISTANT_ROLE`, `APP_ORIGIN` and
  `FAILOVER_BACKENDS`. Without failover, `APP_ORIGIN` no longer stands in
  for `PUBLIC_ORIGIN` (Google, Notion, push and invite links); Kyle
  confirmed `PUBLIC_ORIGIN` = https://heykwil.com.
- **Docs:** README's phone app, iPhone, Mom's PC copy, failover, Home
  Assistant and Windows-host text point at https://heykwil.com and cloud
  family invites instead of Tailscale; FAILOVER.md and PI_MIGRATION.md
  carry a "retired" banner; MANUAL_TEST's phone and failover checks;
  SECURITY "Failover: off on the cloud"; BLOCKERS (Kyle's Render, ruleset
  and optional PC/Pi steps, heykwil.com URLs, Mom on the cloud, the PC
  watchdog no longer needed for the cloud).
- **Kept:** `eventlog.py`, sync.py's `Event`/`revoke_event`/
  `revoked_before`, `smoke.py`, the check session, `update-watch.js`,
  `kwil/router.py` and the canvas helper; a PC or Pi with `[failover]
  role` still runs the failover code.
- **Tests:** `test_failover_cloud.py` +2 (the server profile with
  `ASSISTANT_ROLE=cloud`, `APP_ORIGIN` and `FAILOVER_BACKENDS` set: no
  role, router 401, `hc1.` app and sync bearers refused, token sign-in 401,
  `/api/sync` 404 signed in, cookies work, one WARNING without values; the
  warning only once and only when something is set), `test_failover_config`
  +1 (on by default, off in the profile), `test_history` +1 (origin),
  `test_google_reconnect` +1 (without failover only `PUBLIC_ORIGIN` gives
  the cloud its address), new `tests/js/app-failover-off.test.mjs` (4:
  `/healthz` unreachable with a cached `failover=true` never imports the
  router and clears the four keys, also with no role, and a server with a
  role still imports it). Existing failover tests opt in with `enabled =
  true`. Fail-first: with only the source reverted (config.py, peers.py,
  history.py, app.js) every new test fails except the two JS guard tests
  (the single import site, and the role case that already worked).
- **Judge round 1's three LOW findings, fixed:**
  1. An old phone's `hc1.` bearer on the cloud (failover off) was counted
     as a wrong `WEB_TOKEN` and could lock that address out. CloudGuard
     (HTTP and the voice socket) and `WebAuth.sid` now answer it "not
     signed in" without counting, as for a Canvas helper key (unless
     `WEB_TOKEN` itself starts with `hc1.`). Tests: 8 old-token requests
     plus 3 more, then a login works and the address isn't blocked, a
     socket with one isn't counted, a real wrong token still is
     (`test_failover_cloud.py`); the same on the plain web app
     (`test_webapp.py`).
  2. The old access token in IndexedDB (`hey-claude`, backend-router.js
     `TOKEN_DB`) was never deleted: `detectFailover` now deletes that
     database too when failover is off (guarded: no IndexedDB, private
     mode). JS tests: the database is deleted with `/healthz` down or
     roleless, kept with a role, the keys are still cleared without
     IndexedDB, and the page's name matches the router's export.
  3. "Sign out everywhere" labelled its revoke event "pc" on the cloud:
     `WebAuth.revoke_tokens` now uses the history store's origin (else
     "pc"). Test: a server-profile app's revoke event says "cloud".
  Fail-first: with only `cloud.py` and `webapp.py` reverted the three
  Python tests fail (`sign-in failed` instead of `not signed in`, origin
  'pc', the address blocked); with only `app.js` reverted 3 of the 6 JS
  tests fail.
- Full suite: 4032 passed, 53 skipped (baseline e5fbb70 4024 + 8); JS 487
  passed (baseline 481 + 6).
## Desktop layout v1 (2026-10-31)
- **What:** at 48rem and up a 4.5rem rail (`<nav id="rail">`, the body's
  first element): Search (its shortcut under it, "Ctrl K" or "⌘K"), Chat,
  Today, History, Settings at the bottom; on every screen but the sign-in
  and voice mode. Like the phone's tab bar each item clicks the header's
  own control (`app.js` `onTab` now wires `tab-*` and `rail-*` alike;
  `syncTabs` marks `aria-current="page"` and mirrors `#open-today` /
  `#open-history`'s `hidden`), and the header's calendar, clock and gear
  hide while the rail shows. `body:has(#rail:not([hidden]))` is a grid
  (rail, then every `<main>` in row 1 column 2); the chat is a centred
  46rem column; the Settings page starts right of the rail, which stays on
  top. Below 48rem nothing changes.
- **Two panes at 64rem and up:** `show()` sets `body.dataset.split`: Today
  beside the chat, a transcript beside the History list (its row
  `aria-current="true"`, `history.js` `markOpen`; the transcript's Back
  hidden, Escape still goes back). `currentView` is kept and `show()` runs
  again on the media query's `change`. No `document.body` or no
  `matchMedia` (the vm test DOMs): one screen at a time, as before.
- **Ctrl+K / Cmd+K switcher** (`ui.js` `KwilUI.switcher`, no new script):
  a `<dialog id="switcher">` with a combobox over a listbox. Mac uses
  `metaKey`, others `ctrlKey`; no Alt/Shift; works while typing; again
  closes it; ignored on the sign-in, in voice mode and over any other open
  dialog. Up/Down wrap, Home/End jump, Enter or a click picks, Esc closes
  it (only it) and gives focus back. Commands (Chat, New chat when
  `KwilApp.idle()`, Today, History, Settings, Memory, Projects, Reminders,
  Start voice conversation) are listed only while their control is not
  `[hidden]`, filtered by a case-insensitive substring of the translated
  label, and click that control. With a query, up to 6 conversations from
  `GET /api/conversations?q=&limit=6` (150 ms debounce, the previous
  request aborted, a sequence number drops stale answers), opened through
  the new `window.KwilHistory.openTranscript`.
- **Voice mode:** still a full-window overlay; `voice.js` now calls
  `KwilUI.voice.opened()`/`closed()`: the screens, rail and tab bar behind
  it are `inert`, focus goes to the orb, Escape ends it (`#voice-end`;
  not over an open dialog), focus returns to `#voice-open` or `#text`.
  Captions widen to 40rem from 64rem. On a desktop, Settings' Done (and
  Escape) focus the rail's Settings; a click in the rail doesn't close
  Settings first.
- `sw.js` `kwil-shell-v27`; zh: 搜索, 前往, 前往页面或对话, 没有匹配项,
  新对话, 开始语音对话, 对话 ("Results" already had 搜索结果).
  DESIGN_SYSTEM: "Desktop layout" section, Tab bar and sheet notes.
- **Tests:** new `tests/js/desktop.test.mjs` (29 with judge round 1): app.js in node:vm with a
  matchMedia stub (rail visibility and `aria-current` on every screen,
  mirroring after /api/state, item clicks, both splits, narrow widths
  unchanged, a resize re-laying out, no matchMedia/body), app.js +
  history.js (the open row), ui.js (shortcut on Windows and Mac, ignored
  cases, ARIA, filter in English and Chinese, keys, Esc focus return and
  stopPropagation, conversation debounce/abort/stale answer/6 cap),
  voice.js + ui.js (inert, orb focus, Esc ends, focus return), markup, CSS
  (rail and grid only from 48rem, splits only from 64rem, tokens only) and
  zh. `ui.test.mjs` +1 (desktop Done/Escape focus the rail's Settings; a
  tap in the rail keeps Settings open). Fail-first against e5fbb70's
  sources: 22 of the 25 and the ui case fail (the 3 that pass are the
  "nothing changes" guards: narrow widths, the old fake DOM, the ignored
  shortcut). Mutations: dropping the sequence check, the abort, the
  debounce, Esc's stopPropagation or the `change` listener each fail their
  test. v27 pinned in six test files.
- **Screenshots** (Playwright Chromium, mocked APIs, not real hardware):
  1280x800 and 1440x900, light and dark, English and Chinese: chat, the
  Today split, History + transcript, Settings, the switcher open; plus
  390x844 (rail not drawn, tab bar shown). The same run checked in the
  browser: Esc returns focus to the message field, Settings' Esc focuses
  the rail's Settings, a resize to 900 drops the split and back restores it.
- **Judge round 1 (4 fixes, each fail-first: 4 new tests fail at
  f5dc4b2):** the switcher's keys do nothing while an IME is composing
  (`isComposing` or keyCode 229: Enter, Escape and the arrows pick the
  pinyin candidate); after a pick, if focus was left on something not
  drawn (Chat from History left it on History's hidden title) it moves to
  the command's own place (`#text` for Chat, the rail item for Today and
  History, the screen's title for Memory, Projects and Reminders); a
  picked command whose control went `[hidden]` or `disabled`, or New chat
  once a reply started, runs nothing and the list is redrawn (disabled
  controls aren't listed); `#switcher-status` styles are scoped
  `dialog.switcher .switcher-status` so `dialog.sheet p` no longer
  overrides them. The judge's Playwright probes now show Enter during
  composition doing nothing, focus on `#text` after Chat, and the status
  padded.
- Full suite: 4024 passed, 53 skipped, as at e5fbb70 (no Python test
  added; a `git archive` copy of e5fbb70 runs 4022 passed, 54 skipped and
  1 git-only failure, `test_ui_ux_pro_max_data_is_committed`, outside a
  repo); JS 511 passed (baseline 481 + 30).
