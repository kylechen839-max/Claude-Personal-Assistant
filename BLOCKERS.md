# Blockers

Only what is still open for Kyle. None of it blocks the code.

## Open
- [ ] **Canvas helper extension: the steps only Kyle can do (2026-10-27).**
      The code is in `tools/canvas-helper/` (its README).
      1. Load it unpacked in Chrome or Edge on your computer and run the
         "Canvas helper extension" checks in MANUAL_TEST.md with your real
         school (this also confirms Canvas's file store host and that
         Chrome sends your Canvas session with the extension's requests;
         both were unverifiable from the sandbox).
      2. **Optional, costs money:** a Chrome Web Store listing needs a
         developer account (a one-time registration fee) and a privacy
         policy page; Edge Add-ons is free. Not done; nobody needs it while
         Load unpacked works.
- [ ] **Connected services: the steps only Kyle can do (2026-10-27).**
      The code is done (README "Connect your services in the app (cloud)").
      1. **Notion integration capabilities:** notion.so/profile/integrations
         -> the public Kwil integration -> Capabilities: also tick
         **Insert content** (lecture notes, chat digests and the dev team
         inbox create pages; invited people's tools still never create
         anything). Its redirect URIs already cover `/notion/callback`.
      2. **Render:** make sure `PUBLIC_ORIGIN` is the address you use
         (`https://heykwil.com`), then delete `NOTION_TOKEN` (Environment
         -> Save). While it is set, Notion says "Set on the server."
      3. **Phone:** ⚙ -> Connections -> Notion -> **Connect**; on
         Notion's page tick the Assignments database and the pages for
         Lecture Notes, Chat Digests and the "Hey Claude coordinator
         inbox". Then Home Assistant: type its Nabu Casa (or tunnel) https
         address -> **Connect** -> sign in as the non-admin Kwil user.
      4. Run the "Connected services on the cloud" checks in MANUAL_TEST.md.
- [ ] **Kwil rebrand: the steps only Kyle can do (2026-10-22).** The code
      and docs say Kwil and run under both names; these are UI clicks.
      1. **Rename the GitHub repo** to `Kwil` (Settings -> General ->
         Repository name). GitHub redirects the old URL, and the updater
         accepts either name as `origin` for this release
         (`updater.REPO_ALIASES`). Afterwards, on the PC (and a Pi):
         `git remote set-url origin https://github.com/kylechen839-max/Kwil.git`.
         Re-point anything that names the repo by hand (the dashboard
         repo's links, Render's connected repo if it doesn't follow).
      2. **Render display name:** Dashboard -> `hey-claude` -> Settings ->
         Name -> "Kwil". Leave `render.yaml`'s `name: hey-claude` and the
         disk `hey-claude-data` alone: a renamed Blueprint service is a
         new service with an empty disk. The URL stays
         `hey-claude.onrender.com` (a custom domain is the way to change it,
         see the launch item below).
      3. **Optional: re-register the PC tasks** so they are under `\Kwil\`
         and run `python -m kwil`: `.\scripts\install_windows_service.ps1`,
         then `.\scripts\install_family_tasks.ps1` and
         `.\scripts\install_watch_task.ps1`. Each removes its old
         `\HeyClaude\` task first. Not urgent: until then the old tasks run
         `python -m assistant` (an alias of `kwil`) and `deploy.ps1` and the
         watchdog still find and restart them. Do it before the release
         that drops the `assistant/` alias.
      4. **The names other services show:** Google Cloud -> OAuth consent
         screen -> App name "Kwil" (both projects), and Notion ->
         my-integrations -> the integration's name "Kwil" (people see it in
         Settings -> Connections). Leave the Notion database "Hey Claude
         coordinator inbox" as it is: the code finds it by that title.
      5. **Train the "Hey Kwil" wake word** (item below).
      6. **Trademark check** for "Kwil" (USPTO TESS / EUIPO search, and the
         App Store name) before the public launch.
- [ ] **"Hey Kwil" wake-word model.** Until it exists the wake word stays
      "Hey Claude" (`keywords/hey_claude.onnx`, `[wake] keyword_path`).
      huggingface.co is blocked in the cloud sandbox, so training needs
      Kyle: run the openWakeWord "automatic model training" Colab with the
      phrase "hey kwil" (also try "hey quill" as a spelling for the TTS),
      download the `.onnx`, commit it as `keywords/hey_kwil.onnx`, record
      four real "Hey Kwil" clips (like `tests/fixtures/hey_claude_sapi_*.wav`)
      for its test, and point `[wake] keyword_path` at it. Alternative: a
      manual GitHub Actions job (`workflow_dispatch`) running the same
      training script on a GitHub runner, which can reach huggingface.co.
- [ ] **Public beta launch (in this order; each step points to its own
      item below rather than repeating it).** Nothing in code is missing;
      these are Render, Google, Notion and wording steps only Kyle can do.
      1. **Render environment.** `USER_DATA_KEY` exists (item
         "`USER_DATA_KEY` on Render") and is never changed again: a new
         value makes every tester's stored key and Notion token unreadable.
         `PUBLIC_ORIGIN` is the exact https origin people open (no
         trailing slash). Decide on a custom domain *now*: changing it
         later changes the Google and Notion redirect URIs and the cookies
         (everyone signs in again).
      2. **Done (2026-10-22): Kyle approved the wording; support email
         heyclaude.app@gmail.com.** **Approve the legal pages.** Read `kwil/web/privacy.html`
         and `terms.html` (MANUAL_TEST "Public site and waitlist"), fix any
         wording, and pick a support email address.
      3. **Done (2026-10-22, the launch PR).** **The launch PR** (one small PR, after step 2): `"public_site":
         True` in `SERVER_PROFILE` `"webapp"` (`kwil/config.py`), plus
         `support_email = "<address>"` and `legal_draft = false` in
         `config.toml` `[webapp]`. After Render redeploys, open `/welcome`,
         `/privacy` and `/terms` signed out (a private tab). Until this PR
         `/privacy` and `/terms` answer 404, and Google's consent screen
         and Notion's integration form both need those URLs, so it must be
         live before steps 4 and 6.
      4. **Google for beta testers:** item "Beta testers' Google" below: a
         *new* GCP project, consent screen with only `calendar.events`,
         `calendar.calendarlist.readonly` and `calendar.calendars.readonly`,
         published "In production" unverified (100-user cap), a Web
         client with redirect `<PUBLIC_ORIGIN>/google/callback`, then its
         JSON in `GOOGLE_PUBLIC_CLIENT_JSON`.
      5. **Optional: Google verification.** Verify the domain in Search
         Console (Google's review wants a domain you own, so this goes
         with a custom domain), then submit the project for
         verification with a demo video (consent, then a calendar
         question). Lifts the warning screen and the 100-user cap.
      6. **Notion:** item "Testers' Notion" below: the public integration,
         then `NOTION_OAUTH_CLIENT_ID` / `NOTION_OAUTH_CLIENT_SECRET`.
      7. **Optional:** item "Cloud uptime alerts and error tracking"
         (UptimeRobot on `/healthz`, Sentry's `SENTRY_DSN`).
      8. **`[tts] family_paid_voices`:** leave it off unless you want
         family-tier members on your ElevenLabs bill. Since this release it
         never reaches beta testers (they are refused in the list, when
         choosing and at reply time), so turning it on is safe for them.
      9. **Dry run on a second phone** with a test invite of each tier:
         MANUAL_TEST "Cloud server on Render" (invite, Beta tester, own
         Anthropic key, own Notion, Download my data, Delete my account,
         limits) and "Public site and waitlist". Watch Render's disk graph
         (README "Persistent disk").
      10. **Invite** the first testers from `/waitlist` (README "Inviting
          someone from the waitlist").
      11. **Tag 1.1.0** in the GitHub UI after the judge's final GO on
          that exact SHA (the version bump and CHANGELOG date land in the
          release PR first).
- [ ] **Install the watchdog on the PC (~1 min).** In PowerShell in the
      project folder: `.\scripts\install_watch_task.ps1` (or
      `.\scripts\install_family_tasks.ps1 -Watch`). It registers the hidden
      `\Kwil\Watch` task (every 5 minutes) that checks your app and
      Mom's and Kai's, heals them, and shows a banner in your phone app when
      one is down (README "Health checks and safe updates"). The PC
      session's permission check has blocked scheduled-task installs
      before, so run it yourself. From now on update with
      `.\scripts\deploy.ps1` instead of `git pull` + restarting.
- [ ] **Add the `ANTHROPIC_API_KEY` repository secret and make
      `api-contract` a required check (~3 min).** GitHub -> the repo ->
      Settings -> Secrets and variables -> Actions -> New repository
      secret `ANTHROPIC_API_KEY` (a key with a low spend limit is enough:
      a few short requests per run). Then Settings -> Branches -> master's
      rule -> Require status checks -> add `api-contract`. Until the
      secret is there the job passes with the live tests skipped (only the
      contract-shaped fake runs). The key is only given to that job and
      never printed.
- [ ] **Optional: a Chinese voice in the cloud image (decision).** The
      reply-voice picker shows a Chinese-speaking family member only Chinese
      voices, and the Render image bakes in just `en_US-lessac-medium`, so
      on the cloud their list is "Default" alone until a `zh_CN` Piper voice
      is added to the image (~60 MB more; Dockerfile `PIPER_VOICE_URL`
      step). Nothing breaks without it.
- [ ] **Family members' own Google on the cloud (~10 min, README "Invite
      family" -> "Their own Google").** Only you can: (1) in
      console.cloud.google.com, project `hey-claude-511904`, create an OAuth
      client of type **Web application** with redirect URI
      `https://hey-claude.onrender.com/google/callback` (your real Render
      address); (2) in Render -> hey-claude -> Environment set
      `PUBLIC_ORIGIN` to that address and paste the client's JSON into
      `GOOGLE_WEB_CLIENT_JSON`; (3) make sure the consent screen is
      **published** (Testing mode allows only listed test users, for 7
      days). Until then invites work and the Connect button stays hidden.
      Redeploy after merging so the image has the Chinese Whisper model and
      voice (the build takes a few minutes longer).
      `PUBLIC_ORIGIN` must be exactly the https address family members
      open (scheme, host and port, no trailing path): the sign-in cookie
      is set there and Google returns there, so a LAN `http://` address or
      a second hostname (e.g. a custom domain next to `*.onrender.com`)
      makes their sign-in fail.
- [ ] **Your own Google on the cloud, connected in the app (~3 min; README
      "Run it in the cloud" step 3).** Only you can: (1) in
      console.cloud.google.com, project `hey-claude-511904` -> APIs &
      Services -> Credentials -> the **Web application** client (the one in
      `GOOGLE_WEB_CLIENT_JSON`), confirm
      `https://heykwil.com/google/callback` is an authorized redirect URI
      and `PUBLIC_ORIGIN` in Render is exactly `https://heykwil.com`; (2)
      after the deploy, open https://heykwil.com signed in with `WEB_TOKEN`,
      tap ⚙ -> **Connect Google account** and allow every box. From then on
      the server uses that sign-in (`/data/google_token.json`) instead of
      `GOOGLE_TOKEN_JSON`, which you may then leave as a fallback or clear.
      If Calendar on the PC stops afterwards (Google may tie the grants of
      one project together), run `python -m kwil.tools.google_auth` there
      again.
- [ ] **Beta testers' Google: a Calendar-only OAuth client (~20 min plus
      Google's review; README "Invite family" -> "Account types").** Only
      you can: (1) create a *new* Google Cloud project (e.g. "Hey Claude
      Beta"), enable the Calendar API, and set up its OAuth consent screen
      with only the `calendar.events`, `calendar.calendarlist.readonly` and
      `calendar.calendars.readonly` scopes, a support email and the privacy
      policy link; publish it ("In production", unverified: up to 100
      users see a warning screen); (2) create a **Web application** client
      there with redirect URI `https://hey-claude.onrender.com/google/callback`;
      (3) paste its JSON into `GOOGLE_PUBLIC_CLIENT_JSON` in Render ->
      Environment; (4) submit the project for verification (demo video of
      the consent and a calendar question). Never add Gmail scopes to that
      project. Until (3), beta testers can't connect Google (the app says
      so); family members are unaffected.
- [ ] **`USER_DATA_KEY` on Render (~2 min; README "Invite family" -> "Their
      own Anthropic key").** It encrypts the testers' own Anthropic keys.
      `render.yaml` has Render generate it; if the service isn't synced from
      the Blueprint, add it in Render -> hey-claude -> Environment yourself:
      a random value of 32+ characters (e.g. the output of
      `python -c "import secrets; print(secrets.token_urlsafe(48))"`), then
      Save. Never change or lose it afterwards (every tester would have to
      enter their key again). Until it is set, beta testers can't save a key
      (the app says why) and so can't talk to Claude; family members keep
      using your key.
- [ ] **Testers' Notion: a public Notion integration (~10 min; README
      "Invite family" -> "Notion for beta testers").** Only you can: (1) at
      notion.so/profile/integrations create a **Public** integration ("Any
      workspace"), capabilities Read content + Update content, no user
      information with email; (2) fill in the company name, website, privacy
      policy `https://hey-claude.onrender.com/privacy`, terms
      `https://hey-claude.onrender.com/terms` and a support email, and the
      redirect URI `https://hey-claude.onrender.com/notion/callback`;
      (3) paste its OAuth client ID and secret (shown once) into
      `NOTION_OAUTH_CLIENT_ID` / `NOTION_OAUTH_CLIENT_SECRET` in Render ->
      Environment. Optional: publish an "Assignments" template page and set
      it as the integration's template. No Marketplace listing is needed
      (Notion's review is for listings; unverified from the sandbox, where
      developers.notion.com is blocked). Needs `PUBLIC_ORIGIN` and
      `USER_DATA_KEY` (above). Until (3) the "Connect Notion" button stays
      hidden.
- [ ] **Measure Render memory with a Chinese-speaking family member.** The
      +0.5-0.7 GB for Whisper `small` and the Mandarin voice is an
      estimate, not measured (Hugging Face is blocked in the build
      sandbox). After the first Chinese voice turn, check Render ->
      hey-claude -> Metrics (memory peak) and note it in PROGRESS.md
      ("Family invites, piece B"); if it nears 2 GB, set `FAMILY_ZH=0` or
      a larger plan.
- [ ] **Chinese instance for Mom (~15 min, README "A Chinese instance for
      someone else").** Only you can: (1) create a **Web application** OAuth
      client in console.cloud.google.com with redirect URI
      `https://<pc-name>.<tailnet>.ts.net:8443/google/callback`, save its
      JSON as `data/mom/google_client.json`, and add her Gmail address as a
      test user on the consent screen; (2) create `.env.mom` with
      `ANTHROPIC_API_KEY`; (3) put the address in `config.mom.local.toml`
      (`[webapp] public_origin`); (4) `tailscale serve --bg --https=8443
      http://127.0.0.1:8766` and share the PC with her in the Tailscale
      admin console; (5) `python -m kwil --download-voice
      zh_CN-huayan-medium`. Then MANUAL_TEST.md "Chinese instance for Mom".
- [ ] **Google sign-in again for Gmail (one time, ~1 min).** The current
      `token.json` only allows Calendar, so the email tools stay off until
      you grant Gmail access:
      1. console.cloud.google.com -> project `hey-claude-511904` -> APIs &
         Services -> Library -> **Gmail API** -> Enable.
      2. (OAuth consent screen -> Data access: adding the scopes
         `gmail.readonly` and `gmail.compose` is optional while the app is in
         Testing; Google still asks for them at sign-in.)
      3. On the PC: `python -m kwil.tools.google_auth` and accept
         Calendar, "Read your email" and "Manage drafts and send emails".
         Google words the drafts permission that way (there is no
         drafts-only one); the assistant blocks sending in code. This
         replaces `token.json`. (With `[gmail] enabled = false` it asks for
         Calendar only.) Keep `token.json` private: it could send mail if
         stolen. To take Gmail access back later, see SECURITY.md (turning
         `[gmail]` off is not enough; revoke it in your Google account).
      4. Copy the new `token.json` to the Pi, or paste its contents into
         `GOOGLE_TOKEN_JSON` on the cloud host.
      `python -m kwil --check` shows "Gmail access: granted" afterwards.
- [ ] **Google sign-in again for calendar labels and other calendars (one
      time, ~2 min).** The fuller calendar tools (colors, repeats, guests,
      invites, free time) work with the current `token.json`; Google's
      named event labels, other calendars by name and "which calendars do I
      have?" need two more read-only permissions:
      1. On the PC and the Pi: `pip install -r requirements.txt` (needs
         `google-api-python-client` 2.199+ for labels; the automatic
         updater skips updates that change requirements).
      2. On the PC: `python -m kwil.tools.google_auth` and accept the
         new "See the list of Google calendars you're subscribed to" and
         "See the title, description, default time zone ... of your
         calendars" boxes (read-only). (OAuth consent screen -> Data access:
         adding `calendar.calendarlist.readonly` and
         `calendar.calendars.readonly` is optional while the app is in
         Testing.)
      3. Copy the new `token.json` to the Pi, or paste its contents into
         `GOOGLE_TOKEN_JSON` on the cloud host.
      `python -m kwil --check` shows "Calendar labels access: granted"
      afterwards. Family members get the new boxes the next time they
      press "Connect Google account".
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
- [ ] **Smart home (only if you have Home Assistant).** The tools are
      built and stay off until you: (1) have a Home Assistant install on
      your network; (2) create a long-lived access token (HA profile ->
      Security); (3) put `HA_URL` and `HA_TOKEN` in `.env`; (4) set
      `[home_assistant] enabled = true` in `config.local.toml`. Then
      MANUAL_TEST.md "Smart home". No Home Assistant: nothing to do.
- [ ] **Team chat (Slack, Discord, GitHub; only the ones you want).** The
      tools are built and stay off until you make the tokens (README "Team
      chat"): Slack: api.slack.com/apps -> Create New App -> From scratch in
      the team workspace, add the user scopes, install, put the `xoxp-`
      token in `.env` as `SLACK_USER_TOKEN` (never turn on Manage
      Distribution). Discord: create an app -> Bot -> Reset Token into
      `DISCORD_BOT_TOKEN`, turn on Message Content Intent, turn off Public
      Bot, and have a server admin add it with the `bot` scope and
      permissions=66560. GitHub: a fine-grained read-only token (Issues,
      Pull requests, Discussions, Metadata) as `GITHUB_TOKEN`. Then
      `[teamchat] enabled = true` plus `slack`/`discord`/`github = true` in
      `config.local.toml`, and MANUAL_TEST.md "Team chat".
- [ ] **Real-voice wake-word tuning.** The "Hey Claude" model was trained on
      synthetic voices only. Run MANUAL_TEST.md step 3 and set `[wake]
      sensitivity` (and if needed `vad_min_threshold`, `silence_ms`) to what
      works for your voice and room.
- [ ] **Speech accuracy on your own voice (~6 min, MANUAL_TEST.md 11b).**
      The PC now uses Whisper `small.en` with beam search 5 and a
      vocabulary prompt (first start downloads ~480 MB; the go-live
      check's voice turn waits 180 s, so on a slow line fetch it first:
      `.venv\Scripts\python -c "from faster_whisper.utils import
      download_model; download_model('small.en')"`). Nothing was
      measured on a real voice: say the ten phrases and five quick "yes"
      answers on the phone (push-to-talk and hands-free) and on the PC.
      If it is too slow on the PC, `[stt] model = "base.en"` in
      `config.local.toml`.
- [ ] **Optional: cloud speech-to-text (decision + key, ~5 min; only
      Kyle).** For the best accuracy, especially on Render (which stays on
      `base.en`): get an OpenAI or Deepgram API key, put
      `OPENAI_API_KEY` or `DEEPGRAM_API_KEY` in `.env` (PC) or the Render
      service's environment, and set `[stt] engine = "openai"` /
      `"deepgram"` (on Render: `STT_ENGINE`). Paid per minute, and every
      recording (invited people's too) then goes to that company; off by
      default. README "Cloud speech-to-text".
- [ ] **The rest of MANUAL_TEST.md** (mic, speaker, chime, follow-ups,
      barge-in (step 4b: whether `barge_in = "voice"` works on your
      speakers or needs headphones), spoken delete, Notion, memory, web, Piper, network down, latency).
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
- [ ] **Spotify app (only if you want Spotify by voice, ~5 min; needs
      Premium).** Only you can: at developer.spotify.com create an app
      (Web API) with redirect URIs `http://127.0.0.1:8888/callback` and
      `https://<your-pc>.<tailnet>.ts.net/spotify/callback` (the phone
      button; your `[webapp] public_origin` + `/spotify/callback`), add
      your account under User Management, put its Client ID in `.env` as
      `SPOTIFY_CLIENT_ID`, set `[spotify] enabled = true` in
      `config.local.toml`, and run `python -m kwil.tools.spotify` once
      (README "Spotify by voice"). Until then the Spotify tools stay off.
- [ ] **Cloud uptime alerts and error tracking (optional, free, ~10 min).**
      Only you can sign up: an UptimeRobot monitor on
      `https://<your service>.onrender.com/healthz`, and (if you want crash
      reports) a Sentry Python project whose DSN goes into Render's
      `SENTRY_DSN` (README "Uptime, error tracking and backups"). Without them nothing changes: no reports
      are sent and the PC watchdog's `[watch] cloud_url` still works.

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
