# Blockers

Things only Kyle can do. Everything else has been built and tested around them.
None of these block the code; each one switches on a feature.

## Done (2026-10-17, on Kyle's Windows PC)
Full `pytest` with `.env` loaded: **71 passed, 0 skipped**, including the
real Whisper, Notion, Google Calendar and Pinecone tests. Text mode answered
"what's on my calendar tomorrow?" and "what assignments are due this week?"
from the live data.
- **Anthropic API key**: in `.env`. The default model is now
  `claude-sonnet-5-5` (`claude-sonnet-4-5` returns 404).
- **Whisper**: downloaded on first run; `test_stt.py` runs with no skips.
- **Google Calendar**: Cloud project `hey-claude-511904`, Calendar API on,
  External/Testing consent screen with kylechen839@gmail.com as test user,
  Desktop OAuth client saved as `credentials.json`, `token.json` from the
  one-time sign-in (scope: calendar events). Both files are gitignored.
- **Notion**: internal connection "Hey Claude" in Kyle Chen's Space, shared
  with **Fall 2026 Assignments -> Assignments**
  (`7eefabe3-0226-47a8-a287-584be0d2ea9b`, not the second "Assignments"
  database under "Student Planner"). `NOTION_TOKEN` in `.env`. Property
  names (Assignment, Course, Due, Status, Type) are the defaults.
- **Pinecone**: `PINECONE_API_KEY` in `.env`. The assistant writes only to
  the `voice-assistant` namespace; no test writes to the real index.
- The tests read keys from the environment, not from `.env`, so load it into
  the shell first (PowerShell):

  ```
  Get-Content .env | % { if ($_ -match '^([A-Z_]+)=(.+)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
  pytest -rs
  ```

## Still to do: custom "Hey Claude" wake word (openWakeWord)
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
