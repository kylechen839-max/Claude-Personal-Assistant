# Blockers

Things only Kyle can do. Everything else has been built and tested around them.
None of these block the code; each one switches on a feature.

## 0. Anthropic API key
- No key was available in the build sandbox, so every Claude call in the
  tests is mocked. **To do:** put `ANTHROPIC_API_KEY=...` in `.env`.

## 1. Whisper model could not be downloaded in the build sandbox
- The build environment's network policy blocks `huggingface.co`, so the
  real transcription test (`tests/test_stt.py::test_transcribe_generated_speech`)
  is **skipped** there. It is not faked: on your PC the model downloads on
  first use (~150 MB) and the test runs for real.
- **To do:** run `pytest` once on your PC; confirm 0 skips in `test_stt.py`.

## 2. Custom "Hey Claude" wake word (openWakeWord)
- The wake word engine is openWakeWord: open source, offline, no account or
  key. (Picovoice Porcupine was dropped because its console now requires a
  company email and only offers a trial.)
- **Until a custom model exists, say "Hey Jarvis"** (the built-in fallback;
  `[wake] builtin_keyword` can also be `alexa`, `hey_mycroft`, `hey_rhasspy`).
- **To do:** train "hey claude" with openWakeWord's Google Colab notebook
  (https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb,
  linked from https://github.com/dscripka/openWakeWord; needs a Google
  sign-in, under an hour). Download the `.onnx` file, save it as
  `keywords/hey_claude.onnx`, and add `WAKEWORD_MODEL_PATH=keywords/hey_claude.onnx`
  to `.env`. The same `.onnx` works on Windows and the Raspberry Pi.

## 3. Google Calendar OAuth (credentials.json)
- **To do:** in https://console.cloud.google.com create a project, enable the
  **Google Calendar API**, configure the OAuth consent screen (External,
  add yourself as a test user), then Credentials -> Create OAuth client ID ->
  **Desktop app** -> download as `credentials.json` into the project folder.
  Then run `python -m assistant.tools.calendar` once to sign in (creates
  `token.json`).

## 4. Notion integration token
- **To do:** at https://www.notion.so/profile/integrations create an internal
  integration, copy its secret into `.env` as `NOTION_TOKEN=...`, then open
  the **Fall 2026 Assignments -> Assignments** database in Notion, click
  `...` -> Connections -> add your integration.
- The database id (`7eefabe3-0226-47a8-a287-584be0d2ea9b`) and its property
  names (Assignment, Course, Due, Status, Type) were read from your
  workspace and are the defaults. Note: there is a second "Assignments"
  database under "Student Planner"; the Fall 2026 one is used.

## 5. Pinecone
- Nothing to do: `PINECONE_API_KEY` was available during the build and a real
  read-only query against `project-context` passed. Put the same key in your
  `.env`. The assistant writes only to the new `voice-assistant` namespace;
  no test writes to the real index.
