# Blockers

Only what is still open for Kyle. None of it blocks the code.

## Open
- [ ] **Delete the extra Google Cloud project.** In console.cloud.google.com,
      check whether a project `strange-bird-511904-u7` exists (the
      auto-suggested ID before the project was named "Hey Claude"). If it
      exists and is empty, delete it. The real one is `hey-claude-511904`.
- [ ] **Real-voice wake-word tuning.** The "Hey Claude" model was trained on
      synthetic voices only. Run MANUAL_TEST.md step 3 and set `[wake]
      sensitivity` (and if needed `vad_min_threshold`, `silence_ms`) to what
      works for your voice and room.
- [ ] **The rest of MANUAL_TEST.md** (mic, speaker, chime, follow-ups,
      spoken delete, Notion, memory, web, Piper, network down, latency).
      Note anything that fails here.
- [ ] **Raspberry Pi move:** follow PI_MIGRATION.md (go/no-go in its last
      section). Before it, consider **publishing the Google OAuth app**
      (consent screen -> Publish app): in "Testing" mode Google expires
      `token.json` after 7 days, which an always-on Pi would hit.

## Resolved (2026-10-17)
- Anthropic API key in `.env`; default model `claude-sonnet-5-5`
  (`claude-sonnet-4-5` now returns 404).
- Google Calendar: project `hey-claude-511904`, Calendar API on,
  External/Testing consent screen with kylechen839@gmail.com as test user,
  Desktop OAuth client as `credentials.json`, `token.json` from sign-in.
- Notion: integration "Hey Claude" shared with Fall 2026 Assignments ->
  Assignments; `NOTION_TOKEN` in `.env`.
- Pinecone: `PINECONE_API_KEY` in `.env`; writes go only to the
  `voice-assistant` namespace.
- Whisper model downloads on first run.
- Custom "Hey Claude" wake word trained with openWakeWord (no account or key)
  and committed as `keywords/hey_claude.onnx`. To retrain: in the
  openWakeWord Colab notebook pick Runtime version 2025.07 (Python 3.11),
  set `target_word`, Run all, accept the restart, Run all again.
- Full `pytest` on Kyle's PC with keys loaded: 0 skipped; text mode answered
  calendar and assignment questions from live data.
