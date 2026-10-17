# Progress

Run all tests with `pytest` (from the project root, venv active).

## Stage 1 - Core chat ✅
- `assistant/chat.py`: `ClaudeChat` keeps conversation history, adds a short
  spoken-style system prompt plus the current local date/time, and calls the
  Messages API. Model comes from `CLAUDE_MODEL` (default `claude-sonnet-4-5`).
  A failed request is rolled back so history stays valid; long histories are
  trimmed so they always start with a plain user turn.
- `assistant/cli.py`: text-mode loop (`python -m assistant --mode text`).
- Tested by `tests/test_chat.py` with a mocked Anthropic client (history,
  system prompt, error rollback, trimming, exit words, the text loop).

## Stage 2 - Text to speech ✅
- `assistant/tts.py`: a `TTS` interface (`speak`, `save_to_file`) with
  `Pyttsx3TTS` (offline OS voice: SAPI5 on Windows, eSpeak on the Pi),
  `PiperTTS` (offline neural voice via the `piper` CLI, a drop-in upgrade),
  and `PrintTTS` (no audio). `make_tts(name)` picks one.
- `assistant/audio.py`: wav read/write, resampling to 16 kHz, chime, playback.
- `--mode speak`: typed input, spoken replies.
- Tested by `tests/test_tts.py`: renders real pyttsx3 speech to a .wav and
  asserts it exists, is non-empty, and is not silent; Piper CLI invocation is
  mocked; wav round-trip/resample.

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
  `FileSource` (a .wav). `PorcupineDetector` listens for the custom
  "Hey Claude" `.ppn` (falls back to the built-in "computer" keyword).
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
  The real Porcupine test and the Whisper-on-pipeline test skip without
  `PICOVOICE_ACCESS_KEY` / the Whisper model (see BLOCKERS.md).

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

## Test summary
`pytest`: 75 passed, 5 skipped in the build sandbox. The skips are
real-service tests that need things only Kyle can provide (Whisper model
download, Picovoice key, Notion token, Google sign-in); see BLOCKERS.md.

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
- Memory reads note text from the first of `text`, `chunk_text`, `content`,
  `summary` in the metadata (`[memory] text_fields` in config.toml).

## Continuous testing
`.github/workflows/tests.yml` runs `pytest` on every push and pull request,
plus weekly (Mondays), on Ubuntu (Python 3.10 and 3.12, as a stand-in for
the Pi) and Windows (Python 3.12). Live-service tests skip there.

## Known issues / notes
- The wake word itself (Porcupine + your `.ppn`) and the real Whisper model
  have not been exercised in the sandbox; they are the first things to check
  in MANUAL_TEST.md.
- pyttsx3 on some Windows setups stops speaking after the first reply. A
  fresh engine per reply is now used on Windows; if it still happens,
  switch `[tts] engine = "piper"` (and tell me).
- The energy VAD may need `[wake] vad_min_threshold` raised in a noisy room.
- Two Notion databases are named "Assignments"; the Fall 2026 one is used.
