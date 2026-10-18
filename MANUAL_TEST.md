# Manual test checklist

One sitting on the Windows PC, about 25 minutes. Do the steps in order; each
one says what to do, what should happen, and what to change in `config.toml`
if it doesn't. After changing `config.toml`, restart the assistant (Ctrl+C,
then run it again).

**Already covered automatically, no need to repeat:** CI (`pytest` on Ubuntu
and Windows) checks the chat/tool/memory logic, TTS rendering, Whisper
transcription, the wake-word + VAD pipeline on recorded audio, the follow-up
window, self-hearing, the delete confirmation flow and error messages, and the
end-to-end "what's on my calendar tomorrow?" run with mocked services. Your
keys, Google sign-in, Notion sharing and live text-mode answers were checked
on this PC on 2026-10-17.

Open PowerShell in the project folder with the venv active
(`.\.venv\Scripts\Activate.ps1`). Have Google Calendar and the Notion
Assignments database open in a browser to compare.

## 1. Setup (2 min)
- [ ] `python -m assistant --check`
  - Should: every line `[OK  ]` (a `[WARN]` for Piper is fine), your mic under
    "Input devices", your speakers under "Output devices", no key values
    printed, and "All required checks passed."
  - If not: fix whatever line says FAIL (missing key in `.env`, missing
    `credentials.json`/`token.json`). If the wrong mic is the default, note
    its `[index]` and set `[wake] mic_device = "<index>"` (or part of its
    name), then re-run: "Configured mic: ... -> <your mic>".

## 2. Speaker and mic (3 min)
- [ ] **Speaker:** `python -m assistant --mode speak`, type "say a short
      sentence", then a second question.
  - Should: both replies are spoken clearly at a comfortable speed (the
    second one too: pyttsx3 sometimes went silent after the first reply).
  - If not: speed is `[tts] rate` (default 185); a different Windows voice is
    `[tts] voice = "Zira"` (part of the name). If the second reply is silent,
    use Piper (step 9) and note it in BLOCKERS.md. Type `quit`.
- [ ] **Mic level:** `python -m assistant --mode voice`, press Enter, say
      "What time is it?" at normal volume, press Enter.
  - Should: the printed `You:` line matches what you said.
  - If not: empty or wrong text usually means the wrong mic or a low level.
    Check Windows Settings -> System -> Sound -> Input (the level bar
    should move when you talk; raise the input volume to ~80%), or set
    `[wake] mic_device`. Say "goodbye" to exit.

## 3. Wake word (8 min)
Run `python -m assistant` for steps 3-8 and leave it running.
- [ ] **Banner:** it prints `Listening for 'hey claude'...`
  - If it says "hey jarvis": `keywords/hey_claude.onnx` wasn't found; check
    `[wake] keyword_path`.
- [ ] **Chime:** say "Hey Claude" (then nothing).
  - Should: a two-note chime within about half a second; after ~5 s of
    silence it prints "(heard the wake word but no command)" and listens
    again.
  - If no chime: check `[wake] chime = true` and the speaker. The 5 s wait is
    `[wake] no_speech_timeout`.
- [ ] **Hit rate.** Say "Hey Claude" 5 times in each position and tick how
      many chimed (wait for it to go back to listening between tries):
  - [ ] 1 m, normal voice: __/5
  - [ ] 1 m, soft voice: __/5
  - [ ] 3 m, normal voice: __/5
  - [ ] 3 m, soft voice: __/5
  - Should: at least 4/5 at 1 m and at 3 m normal; soft at 3 m may be lower.
  - If it misses: raise `[wake] sensitivity` in steps of 0.1 (default 0.5;
    higher = fewer misses but more false wakes). The model was trained on
    synthetic voices, so your real-voice result here is the first real data.
- [ ] **False wakes:** 2 minutes of normal talk or TV near the PC, without
      saying "Hey Claude".
  - Should: no chime, or at most one. (Saying "hey cloud" does wake it; that's
    expected.)
  - If more: lower `[wake] sensitivity` by 0.1 and repeat the hit-rate row
    for 1 m normal, to find a value that does both.
- [ ] **Command capture:** "Hey Claude, what's the date today and, um... what
      day of the week is it?" with a short pause in the middle.
  - Should: not cut off at the pause; after you stop it answers within a
    second or two.
  - If cut off: raise `[wake] silence_ms` (default 800). If it waits too long
    or never stops in a noisy room: raise `vad_min_threshold` (default 0.015;
    add the line under `[wake]`, try 0.025). Long commands are capped at
    `max_seconds` (15).

## 4. Follow-up mode (2 min)
- [ ] "Hey Claude, what's on my calendar tomorrow?"
  - Should: it reads tomorrow's events (compare with Google Calendar), then a
    soft, low blip (quieter than the wake chime).
- [ ] Within 5 s of the blip, without the wake word: "and what about Friday?"
  - Should: it answers about Friday's events, so it kept the conversation.
- [ ] After that answer, say nothing.
  - Should: after ~5 s it goes back to waiting for "Hey Claude"; it doesn't
    reply to its own voice (no `You:` line with its own words).
  - If it hears itself or noise (cough, door) starts a turn: raise `[wake]
    vad_min_threshold`, lower the speaker volume or move the mic away from
    it. The window length is `follow_up_seconds` (`0` turns follow-ups off);
    `follow_up_chime = false` drops the blip only.

## 5. Calendar create and spoken delete (3 min)
- [ ] "Hey Claude, add a test event called Manual Test tomorrow at noon."
  - Should: it appears in Google Calendar tomorrow 12:00-13:00 (right time
    zone).
  - If the time is off: set `[assistant] timezone = "America/New_York"` (your
    IANA zone).
- [ ] "Hey Claude, delete Manual Test tomorrow." It asks out loud "... Please
      say yes or no." Say **"no"**.
  - Should: the event is still there.
- [ ] Repeat and say **"yes"**.
  - Should: it confirms and the event is gone from Google Calendar; your "yes"
    isn't answered again as a follow-up.
  - If your answer isn't heard: same mic/VAD settings as step 3 (it records
    the answer like a command).

## 6. Notion status (1 min)
- [ ] "Hey Claude, what assignments are due this week?"
  - Should: matches the Upcoming view in Notion.
- [ ] "Hey Claude, mark <one of them> as in progress." Then "mark it as not
      started" (or whatever it was before).
  - Should: Status changes in Notion, then changes back.
  - If it can't find it: say the name as it appears in Notion; it asks you
    to choose if two names are similar.

## 7. Memory across two conversations (2 min)
- [ ] "Hey Claude, remember that my favorite study spot is the library
      basement." Then say "goodbye".
  - Should: the console prints `(Saved to memory: ...)`.
- [ ] Ctrl+C, run `python -m assistant` again. "Hey Claude, where do I like
      to study?"
  - Should: "the library basement".
  - If not: check the console for "(Memory off: ...)"; `[memory] min_score`
    (default 0.4) can be lowered to 0.3 if notes are missed.

## 8. Web search (1 min)
- [ ] "Hey Claude, what's the weather today?" and "Hey Claude, open YouTube."
  - Should: a short, current answer for your city with no URLs read out;
    YouTube opens in your browser.
  - If the city is wrong: set `[web] location = { city = "...", region =
    "...", country = "US", timezone = "America/New_York" }`.

Ctrl+C to stop.

## 9. Piper vs pyttsx3 voice (3 min)
- [ ] `python -m assistant.tts --download-voice en_US-lessac-medium`, then
      `python -m assistant.tts --voice-file voices/en_US-lessac-medium.onnx --say "Hello Kyle"`
  - Should: a natural-sounding voice.
- [ ] Set `[tts] engine = "piper"`, run `python -m assistant --mode speak`,
      ask two questions.
  - Should: both replies in the Piper voice, starting without a long pause.
  - Keep whichever voice you prefer (`engine = "pyttsx3"` to go back). If
    the voice file is missing, it warns and uses pyttsx3.

## 10. Network down (2 min)
- [ ] Run `python -m assistant`, turn Wi-Fi off (or unplug the cable), then
      "Hey Claude, what time is it?"
  - Should: within ~30 s it **says** it can't reach the internet, and it
    keeps listening.
  - If it takes too long: lower `[assistant] api_timeout` (default 30 s) or
    `api_max_retries` (default 3).
- [ ] Turn Wi-Fi back on and ask again.
  - Should: a normal answer without restarting.

## 11. Latency (1 min)
- [ ] `python -m assistant --timings`, ask "Hey Claude, what's on my
      calendar tomorrow?" twice.
  - Should: after each answer a `(Turn timings from wake: ...)` line. Write
    down the last figure, "end of speech to first audio" (the wait you
    feel): ____ s. Under ~3 s is good.
  - If slow: the `transcript` step is Whisper (try `[stt] model =
    "tiny.en"`); `Claude reply` is the API; `end of speech` includes the
    `silence_ms` pause.

Note anything that failed or any setting you changed in BLOCKERS.md.

---

## Phone web app (optional, ~10 min)
Setup is in README.md ("Phone web app"): Tailscale on the PC and phone.
- [ ] `python -m assistant --mode web --show-token`, then in another window
      `tailscale serve --bg --https=443 http://127.0.0.1:8765`.
  - Should: `.env` now has a `WEB_TOKEN` line; `logs/assistant.log` does not
    contain the token.
- [ ] With the phone on mobile data (Wi-Fi off) and Tailscale on, open the
      `https://<pc>.<tailnet>.ts.net` address. Enter a wrong token, then the
      right one.
  - Should: "wrong token", then the chat page. Turn Tailscale off on the phone
    and reload: the page doesn't load at all (it's not on the internet).
- [ ] Add it to the home screen and open it from there.
  - Should: opens full screen with the Claude mic icon, still signed in.
- [ ] Hold the mic: "What's on my calendar tomorrow?" (allow the mic the first
      time).
  - Should: your words appear, then the answer as text and spoken through the
    phone's speaker within a few seconds.
  - If no sound: check the phone isn't on silent (iPhone switch), or tick
    "Use the phone's voice" in the gear menu.
- [ ] Tap once, ask "Mark Homework 6 as in progress", tap again to send.
- [ ] Create a test event by typing "Add a test event tomorrow at 3", then say
      "Delete the test event tomorrow".
  - Should: Yes/No buttons appear and the question is spoken. Hold the mic and
    say "yes": the event disappears from Google Calendar. Repeat and press No:
    it stays.
- [ ] Leave the page idle for 6 minutes, then ask "What did we just talk
      about?"
  - Should: a new conversation; the earlier one shows up in
    `logs/assistant.log` as "Web conversation saved to memory".


## Voice conversation on the phone (optional, ~12 min)
In the phone web app, tap **Voice conversation**. Turn the latency panel on
with the stopwatch button.
- [ ] First tap: allow the microphone. Ask "What's on my calendar tomorrow?"
      and then just stop talking.
  - Should: the orb moves with your voice, pulses briefly, then the answer
    starts within about 2 seconds of you stopping. The panel says
    "silero VAD" and shows the times.
- [ ] Natural turn-taking: have a 4-5 turn conversation without touching the
      screen, with normal pauses mid-sentence ("Remind me... what's due this
      week?").
  - Should: a short thinking pause mid-sentence doesn't cut you off; a real
    stop does. If it cuts you off, raise `[voice] silence_ms` (e.g. 900).
- [ ] Interrupt it mid-sentence: ask "Tell me about the history of Rome",
      and after its first sentence say "Stop, what time is it?"
  - Should: it goes quiet at once and answers the time. Then ask "What were
    you saying before?": it knows it was cut off and only what you heard.
- [ ] Speaker vs. AirPods: ask a long question on the loudspeaker with the
      phone on a table, and stay silent while it answers. Repeat with
      AirPods.
  - Should: it never interrupts itself. If it does on the speaker, set
    `[voice] vad_threshold = 0.7` and try again; note which one you need.
- [ ] Noisy room (TV or music on): a couple of turns.
  - Should: it still notices when you stop; noise alone doesn't start turns.
    If it keeps hearing the TV, note it (and try `vad_threshold = 0.7`).
- [ ] Delete by voice: "Add a test event tomorrow at 3", then "Delete the
      test event tomorrow". When it asks, say "yes".
  - Should: it deletes it, and "yes" never shows up as its own question.
    Repeat and say "no": the event stays.
- [ ] Lock screen / interruption: mid-answer, lock the phone (or take a call
      / say "Hey Siri"), then come back.
  - Should: it stops talking, shows "Tap to continue", and carries on with
    the same conversation after the tap.
- [ ] Mute, CC and End: tap Mute and talk (nothing happens), unmute; toggle
      CC; tap End.
  - Should: End closes the view; `logs/assistant.log` has "Voice conversation
    saved to memory" (if anything memorable was said).
- [ ] Leave it open and silent for over 2 minutes.
  - Should: it ends by itself ("Ended after a quiet stretch").

---

## Raspberry Pi (optional, ~15 min)
Setup is in README.md ("Raspberry Pi").
- [ ] `./scripts/install_pi.sh` finishes without errors; running it again
      changes nothing.
- [ ] Copy `.env`, `credentials.json`, `token.json` (and `voices/` if you use
      Piper) from the PC, then `.venv/bin/python -m assistant --check`.
  - Should: all required checks pass, with your USB mic and speaker listed.
  - If the mic isn't the default: `[wake] mic_device = "<index>"`. Speaker:
    `sudo raspi-config` -> System Options -> Audio, test with
    `speaker-test -t wav -c 2`.
- [ ] `.venv/bin/python -m assistant --timings`: repeat steps 3 (1 m normal
      only), 4 and 11.
  - If slow: `[stt] model = "tiny.en"` and `[tts] engine = "piper"`.
- [ ] `./scripts/install_service.sh`, reboot without logging in, wait a
      minute, then "Hey Claude, what time is it?"
  - Should: it answers. `journalctl --user -u hey-claude -n 20` shows the
    startup lines.
- [ ] `pkill -f "python -m assistant"`: within ~10 s
      `systemctl --user status hey-claude` shows it running again.
