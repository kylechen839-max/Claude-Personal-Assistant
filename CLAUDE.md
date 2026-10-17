# Project: "Hey Claude" Personal Voice Assistant

## Goal
Build an Echo-style voice assistant in Python on top of the Anthropic API.
I say "Hey Claude", ask something, and it answers out loud. It runs on my
Windows PC first and should later move to a Raspberry Pi with minimal changes.

## How to work (IMPORTANT)
- Work autonomously through ALL stages below without stopping to ask me.
- Do NOT ask me to test anything between stages. Verify each stage yourself
  with automated tests (see "Self-testing"), then move straight on.
- Only stop and ask me if you are truly blocked (e.g. a missing API key or
  account signup only I can do). Log these in BLOCKERS.md and keep working on
  anything else that is not blocked.
- Commit to git after each stage passes its tests, with a clear message.
- Keep a running PROGRESS.md: what each stage does, how it was tested, and
  any known issues.
- When ALL stages are done, give me one final manual test checklist
  (MANUAL_TEST.md) covering things only a human can check (mic, speaker,
  wake word). That is the only time I test.

## Environment
- Windows 11, Python 3.10+, PowerShell.
- Use a virtual environment (.venv) and keep requirements.txt updated.
- Secrets go in a .env file (never commit it; add to .gitignore):
  ANTHROPIC_API_KEY, PINECONE_API_KEY, NOTION_TOKEN,
  plus Google OAuth credentials.json for Calendar.
- Starting point: claude_assistant.py (already has text, speak, and
  push-to-talk voice modes). Refactor it into a package as needed.
- Default model: read from CLAUDE_MODEL env var, default "claude-sonnet-5-5".

## Stages
1. **Core chat**: Clean module that sends messages to the Anthropic API with
   conversation history and a short, spoken-style system prompt. Text-mode CLI.
2. **Text to speech**: Speak replies. Start with pyttsx3 (offline); design an
   interface so a better voice (Piper or ElevenLabs) can be swapped in.
3. **Speech to text**: Push-to-talk recording with sounddevice, transcribed
   with faster-whisper (base.en, CPU, int8).
4. **Wake word**: Replace push-to-talk with an always-listening loop using
   openWakeWord (open source, no key) and a custom "Hey Claude" .onnx model
   (built-in "hey jarvis" until it's trained). After the wake word,
   record until silence (simple voice-activity detection), then transcribe.
   Play a short chime when listening starts.
5. **Tools**: Add Claude tool use for:
   - Google Calendar: list today's/tomorrow's events, create, move, delete.
   - Notion: list upcoming assignments from my Assignments database, and
     update an assignment's Status (Not started / In progress / Done).
   Ask for spoken confirmation before any delete.
6. **Long-term memory**: Use my existing Pinecone index. Before each reply,
   retrieve relevant notes; after each conversation, store a short summary.
7. **Polish**: Config file for settings, logging to a file, graceful error
   handling (network down, API errors), and a single entry point:
   `python -m assistant`.

## Self-testing (no human needed)
- Unit tests with pytest for every module; mock the Anthropic, Google,
  Notion, and Pinecone clients so tests run offline.
- Stage 2: render TTS to a .wav file and assert it exists and is non-empty.
- Stage 3: generate a test .wav of a known phrase (using the TTS engine),
  transcribe it, and assert the transcript roughly matches.
- Stage 4: feed a recorded/generated audio file through the wake-word +
  VAD pipeline instead of the live mic; add a --audio-file flag for this.
- Stage 5-6: test tool-call parsing and dispatch with mocked API responses;
  do at most one real read-only call per service if keys are present.
- All tests must pass (`pytest`) before moving to the next stage.

## Definition of done
- All seven stages implemented, tests passing, PROGRESS.md and
  MANUAL_TEST.md written, README.md explains setup on Windows and on a
  Raspberry Pi.
- Saying "Hey Claude, what's on my calendar tomorrow?" should work end to end.
