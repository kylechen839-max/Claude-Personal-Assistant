# Manual test checklist

Everything else is covered by `pytest`. These need a human, a mic, and a
speaker. Do them on the Windows PC after finishing `BLOCKERS.md`.

## 0. Setup check
- [x] Notion integration ("Hey Claude", Kyle Chen's Space, shared with
      Fall 2026 Assignments -> Assignments) and `NOTION_TOKEN` in `.env`.
      Done 2026-10-17.
- [x] Google Calendar OAuth: project `hey-claude-511904`, Calendar API on,
      External/Testing consent screen with kylechen839@gmail.com as test
      user, Desktop client as `credentials.json`, `token.json` from sign-in.
      Done 2026-10-17.
- [x] `pytest -rs` passes. The only skips should be services you haven't set
      up yet. With all keys present: **0 skipped** (this confirms Whisper
      downloaded and the real openWakeWord, Notion, Google and Pinecone calls
      work). 71 passed, 0 skipped on 2026-10-17 (tests read keys from the
      environment, so load `.env` into the shell first).
- [x] `python -m assistant --mode text`: startup prints no "(... off: ...)"
      lines; "hello" gets a short reply. Checked 2026-10-17 with "what's on
      my calendar tomorrow?" and "what assignments are due this week?".
- [ ] Cleanup: in the Google Cloud console, check whether a project
      `strange-bird-511904-u7` exists. It was the auto-suggested ID before the
      project was named "Hey Claude", and only `hey-claude-511904` showed up
      afterwards, so it probably doesn't. If it does exist and is empty, delete it.

## 1. Speaker (TTS)
- [ ] `python -m assistant --mode speak`, type "say a short sentence":
      you hear it clearly at a comfortable speed (adjust `[tts] rate`).
- [ ] Ask two questions in a row: the **second** reply is also spoken
      (pyttsx3 on Windows sometimes goes silent after the first; if so,
      report it).

## 2. Microphone (STT)
- [ ] `python -m assistant --mode voice`: press Enter, say "What time is
      it?", press Enter. The printed "You:" text matches what you said.
- [ ] Try from across the room / with background music; note accuracy.

## 3. Wake word
- [ ] `python -m assistant`: the banner names your wake phrase ("hey claude",
      not "hey jarvis" — if it says hey jarvis, the `.onnx` path is wrong).
- [ ] Say "Hey Claude": the **chime** plays within ~half a second.
- [ ] Ten tries from normal distance: it wakes at least 9 times.
- [ ] Talk normally / play a video for 5 minutes: no (or very rare) false
      wakes. Tune `[wake] sensitivity` if needed.
- [ ] After the chime, say a command with a short pause in the middle: it
      isn't cut off. Then stop talking: it responds in about a second
      (tune `[wake] silence_ms`).
- [ ] Say "Hey Claude" then nothing: after ~5 s it goes back to listening.
- [ ] The assistant does not wake itself up from its own voice.

## 4. The main scenario
- [ ] "Hey Claude, what's on my calendar tomorrow?" — it reads tomorrow's
      events correctly (compare with Google Calendar).
- [ ] "Hey Claude, add lunch with Sam tomorrow at noon" — appears in Google
      Calendar at the right time (time zone correct).
- [ ] "Hey Claude, move lunch with Sam to 1 PM" — moved, still 1 hour.
- [ ] "Hey Claude, delete lunch with Sam" — it **asks you out loud** to
      confirm. Say "no": event is still there. Repeat and say "yes": it's gone.

## 5. Notion
- [ ] "Hey Claude, what assignments are due this week?" — matches the
      Upcoming view in Notion.
- [ ] "Hey Claude, mark <an assignment> as in progress" — Status changes in
      Notion. Set it back afterwards.

## 6. Memory
- [ ] "Hey Claude, remember that my favorite study spot is the library
      basement", then "Goodbye". The console prints "(Saved to memory: ...)".
- [ ] Restart the app. "Hey Claude, where do I like to study?" — it knows.

## 6b. Internet and browser
- [ ] Set `[web] location` in `config.toml` to your city first.
- [ ] "Hey Claude, what's the weather today?" — a short, correct answer for
      your city; no URLs or "according to source 1" read aloud.
- [ ] "Hey Claude, what's the latest news about <something from today>?" —
      it's current (not from the model's training data).
- [ ] "Hey Claude, open YouTube" — YouTube opens in your default browser.
- [ ] "Hey Claude, search for Emory library hours and open it" — a Google
      results page (or the library page) opens.
- [ ] `logs/assistant.log` shows `open_in_browser` calls; web searches show
      up as part of Claude's reply (they run on Anthropic's side).

## 7. Failure handling
- [ ] Turn Wi-Fi off, ask something: it **says** it can't reach the internet
      and keeps listening. Turn Wi-Fi on: next question works.
- [ ] Unplug the mic while running: it prints "Microphone problem; retrying"
      (and exits with a clear message if it stays unplugged).
- [ ] `logs/assistant.log` shows what was heard and replied.

## 8. Raspberry Pi (later)
- [ ] Repeat sections 1-4 on the Pi with the same `.onnx` model.
- [ ] Note response time from end of speech to start of reply; if slow, try
      `[stt] model = "tiny.en"` and Piper.
