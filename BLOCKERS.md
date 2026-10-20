# Blockers

Only what is still open for Kyle. None of it blocks the code.

## Open
- [ ] **Optional: a Chinese voice in the cloud image (decision).** The
      reply-voice picker shows a Chinese-speaking family member only Chinese
      voices, and the Render image bakes in just `en_US-lessac-medium`, so
      on the cloud their list is "Default" alone until a `zh_CN` Piper voice
      is added to the image (~60 MB more; Dockerfile `PIPER_VOICE_URL`
      step). Nothing breaks without it.
- [ ] **Chinese instance for Mom (~15 min, README "A Chinese instance for
      someone else").** Only you can: (1) create a **Web application** OAuth
      client in console.cloud.google.com with redirect URI
      `https://<pc-name>.<tailnet>.ts.net:8443/google/callback`, save its
      JSON as `data/mom/google_client.json`, and add her Gmail address as a
      test user on the consent screen; (2) create `.env.mom` with
      `ANTHROPIC_API_KEY`; (3) put the address in `config.mom.local.toml`
      (`[webapp] public_origin`); (4) `tailscale serve --bg --https=8443
      http://127.0.0.1:8766` and share the PC with her in the Tailscale
      admin console; (5) `python -m assistant --download-voice
      zh_CN-huayan-medium`. Then MANUAL_TEST.md "Chinese instance for Mom".
- [ ] **Google sign-in again for Gmail (one time, ~1 min).** The current
      `token.json` only allows Calendar, so the email tools stay off until
      you grant Gmail access:
      1. console.cloud.google.com -> project `hey-claude-511904` -> APIs &
         Services -> Library -> **Gmail API** -> Enable.
      2. (OAuth consent screen -> Data access: adding the scopes
         `gmail.readonly` and `gmail.compose` is optional while the app is in
         Testing; Google still asks for them at sign-in.)
      3. On the PC: `python -m assistant.tools.google_auth` and accept
         Calendar, "Read your email" and "Manage drafts and send emails".
         Google words the drafts permission that way (there is no
         drafts-only one); the assistant blocks sending in code. This
         replaces `token.json`. (With `[gmail] enabled = false` it asks for
         Calendar only.) Keep `token.json` private: it could send mail if
         stolen. To take Gmail access back later, see SECURITY.md (turning
         `[gmail]` off is not enough; revoke it in your Google account).
      4. Copy the new `token.json` to the Pi, or paste its contents into
         `GOOGLE_TOKEN_JSON` on the cloud host.
      `python -m assistant --check` shows "Gmail access: granted" afterwards.
- [ ] **Lecture Notes database (only if the first lecture job fails with
      "Notion refused to create the Lecture Notes database").** The
      assistant creates it automatically inside "Fall 2026 Assignments". If
      Notion refuses: open notion.so/my-integrations -> "Hey Claude" ->
      Capabilities and tick Read, Update and **Insert content**; then on the
      Fall 2026 Assignments page, ... -> Connections -> make sure "Hey
      Claude" is connected. Then tap "Try again" on the lecture. (Or create
      a full-page database "Lecture Notes" there with Title, Course (select),
      Date (date) and Duration (number) properties, connect it, and put its
      id in `[lecture] notes_database_id`.)
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
- [ ] **Protect `master` and `stable` on GitHub** before turning on
      automatic updates. GitHub -> the repo -> Settings -> Rules ->
      Rulesets -> New branch ruleset: target `stable`, restrict updates and
      deletions, block force pushes (the promote-stable workflow moves it;
      updates are only installed when signed, so moving `stable` alone
      can't run code on your machines). A second ruleset for `master`:
      block force pushes and deletions (and ideally require pull requests).
      Until then keep `[update] enabled = false` (the default).
- [ ] **Signing key for updates** (only if you want automatic updates):
      README, "Automatic updates" -> "Setting up signing": recommended, your
      own key and you sign a `stable-<sha>` tag per update. If you also want
      CI to sign: create the Environment `stable-signing` (deployments from
      `master` only, you as required reviewer) and store
      `STABLE_SIGNING_KEY` there, **never as a repository secret**.

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
