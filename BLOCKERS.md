# Blockers

Things only Kyle can do. Everything else has been built and tested around them.

## 1. Whisper model could not be downloaded in the build sandbox
- The build environment's network policy blocks `huggingface.co`, so the
  real transcription test (`tests/test_stt.py::test_transcribe_generated_speech`)
  is **skipped** there. It is not faked: on your PC the model downloads on
  first use (~150 MB) and the test runs for real.
- **To do:** run `pytest` once on your PC; confirm 0 skips in `test_stt.py`.

## 2. Picovoice access key and custom "Hey Claude" wake word
- Porcupine needs a free account key, and a custom phrase must be trained in
  the Picovoice Console (only you can sign up).
- **To do:**
  1. Sign up at https://console.picovoice.ai, copy your AccessKey into `.env`
     as `PICOVOICE_ACCESS_KEY=...`.
  2. In the console go to Porcupine, type the phrase "Hey Claude", train it
     for **Windows** (and later again for **Raspberry Pi**), download the
     `.ppn` files, and put them in `keywords/` (e.g.
     `keywords/Hey-Claude_en_windows.ppn`). Point `--keyword-path` (or the
     config file) at it.
- Until then the pipeline was tested with a scripted stand-in detector, and
  the app falls back to the built-in keyword "computer" if no `.ppn` is found
  (it still needs the access key).

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
