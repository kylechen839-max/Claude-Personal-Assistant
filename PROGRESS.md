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
