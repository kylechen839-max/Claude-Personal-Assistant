# Moving Hey Claude to a Raspberry Pi

Step by step, with a go/no-go check before and after. The PC setup is not
touched, so you can go back to it at any time (see [Rollback](#8-rollback)).

Allow about 1.5 hours: 30 min hardware and flashing, 30 min install (mostly
waiting), 30 min checks.

## Do I need a Pi?

Three ways to run it. All of them use the same keys, Google Calendar,
Notion and Pinecone memory; they differ in where the always-listening mic is
and what has to stay on.

| | **PC always on** | **Raspberry Pi** | **Cloud only (phone)** |
|---|---|---|---|
| Setup | `.\scripts\install_windows_service.ps1` (10 min) | this guide (~1.5 h) plus about 100-150 USD of hardware | the Render service; nothing at home |
| "Hey Claude" across the room | yes, where the PC is | yes, wherever you put the Pi | no: you open the app and talk (voice mode) |
| Power and cost | desktop idle about 30-80 W, i.e. roughly 20-90 USD a year at 0.15 USD/kWh (measure yours with a plug meter) | about 3-5 W, roughly 5 USD a year | Render plan cost; your phone's battery while in use |
| Noise | the PC's fans, around the clock | silent (with the small fan of the Pi 5 cooler mostly off) | none |
| Mic placement | the PC's mic: often a desk or headset mic, aimed at you at the desk, weaker across the room | a speakerphone in the middle of the room | the phone's mic, in your hand |
| Reliability | Windows Update reboots it now and then (it comes back when you sign in, or by itself with automatic sign-in); sleep must be off; a game or app holding the mic in exclusive mode blocks it | an appliance: restarts itself (systemd) after crashes, reboots and network drops; rarely updated | depends on the service and your phone's connection |
| Conflicts | while you game or call, the wake word hears your voice chat and game audio (false wakes, or "Hey Claude" mid-call); Discord/Teams noise suppression may change what it hears; turn it off with Task Scheduler -> HeyClaude -> Wake -> Disable | none: its mic is its own | none |
| Privacy | always-on mic in your PC | always-on mic in a box you control | the mic is on only while the app listens |

**Choosing the PC, coming from a hand-made task:** if Task Scheduler
already has a "Hey Claude Web" task that runs `--mode web` on
127.0.0.1:8765, run `.\scripts\install_windows_service.ps1`; it asks before
replacing that task with `\HeyClaude\Web` on the same port, so `tailscale
serve` keeps working, and it adds `\HeyClaude\Wake` for the wake word. To
go back, `-Uninstall` removes only the `HeyClaude` tasks (tailscale serve
is untouched); re-create your old task by hand if you want it back.

**Rule of thumb:** if the PC is already on most of the day in the room where
you'd talk to it, start with the PC (it costs nothing extra and takes ten
minutes), and run the preflight once to check the power settings. If it
sleeps, is noisy, is in another room, or you game a lot, the Pi is the
better home host. Either way the phone uses the cloud host. Run only **one**
wake-word listener per room (see [section 7](#7-what-runs-where)).

## 0. Before you start: go/no-go on the PC side

Don't buy or flash anything until all four are true:

1. **Preflight on the PC is all PASS.** In PowerShell, in the project folder:
   ```powershell
   git checkout master; git pull
   .\scripts\preflight.ps1
   ```
   It prints a table and saves `logs\preflight-<date>-<time>.txt`. Every row
   must be `PASS`. The rows `audio-devices` and `mic-level` say `SKIP` on the
   PC (they need `--hardware`, which is for the Pi); that is expected. Any
   other `SKIP` must say why, and is a no-go unless it's a feature that isn't
   on master yet.
2. **The arm64 CI job is green** on the latest master commit: GitHub ->
   Actions -> **pi-arm64**. It installs everything on Debian Bookworm arm64
   with Python 3.11 (what Raspberry Pi OS is), runs all the tests there, and
   runs the wake word -> Whisper -> reply pipeline on the test recording. So
   the packages and models are known to work on a Pi's CPU before you start.
3. **The cloud smoke check is PASS** (once the cloud host is deployed):
   ```powershell
   .\scripts\cloud_smoke.ps1 https://<your-service>.onrender.com
   ```
   It reads `WEB_TOKEN` from `$env:WEB_TOKEN`, or from `.env` if that isn't
   set, and never prints it.
4. **`MANUAL_TEST.md` Part A is done** (the human checks on the PC).

## 1. Hardware

| Part | Recommended | Why |
|---|---|---|
| Board | **Raspberry Pi 5, 4 GB** (8 GB if you want headroom) | See below. A Pi 4 (4 GB) works but is slower. |
| Power | **Official 27 W USB-C PSU** (Pi 5) / 15 W (Pi 4) | Phone chargers sag under load; the Pi then throttles or the USB mic drops out. |
| Storage | **NVMe SSD on an M.2 HAT (Pi 5) or a USB 3 SSD**; else a good A2 microSD (32 GB+, e.g. SanDisk Extreme) | An always-on device writes logs all day; SD cards wear out and are 3-5x slower to load the models at boot. |
| Cooling | Pi 5 **Active Cooler** (about 5 USD) | Whisper uses all four cores for a second or two per request; without it the Pi 5 throttles. |
| Mic + speaker | See below | |

**Pi 5 vs Pi 4, and Whisper `base.en` vs `tiny.en`.** The slow step on a Pi
is speech-to-text. `base.en` (74 M parameters) is clearly more accurate than
`tiny.en` (39 M), especially for names, but takes roughly twice as long.
A Pi 5 (Cortex-A76, 2.4 GHz) is about 2-3x faster than a Pi 4 (Cortex-A72,
1.8 GHz) for this kind of work. In practice, for a short command:

- **Pi 5 + `base.en`**: about 1-2 s to transcribe; total "end of speech to
  first audio" around 3-4 s (the Claude reply is the same on any machine).
  Keep `base.en`.
- **Pi 4 + `base.en`**: about 3-5 s to transcribe, which feels slow.
  You'd likely switch to `[stt] model = "tiny.en"` and accept more
  mis-heard words.

These are estimates; step 6 measures the real numbers with `--timings`.
Memory is not the limit: Whisper, openWakeWord and Piper together use well
under 1 GB, so 4 GB is enough; 8 GB only matters if the Pi does other work.

**Mic and speaker options:**

| Option | Pros | Cons |
|---|---|---|
| **USB conference speakerphone** (e.g. Anker PowerConf, Jabra Speak) | One plug; hardware echo cancellation, so it doesn't hear itself and follow-ups work well; good pickup across a room | 40-100 USD |
| **USB mic + powered USB/3.5 mm speaker** | Cheap, easy to replace either part | No echo cancellation (the follow-up window may hear the reply; keep the speaker away from the mic); 3.5 mm needs a Pi 4 (the Pi 5 has no audio jack) |
| **ReSpeaker-style mic-array HAT** (2- or 4-mic) | Neat, mounts on the Pi, far-field mics | Needs a driver (an out-of-tree kernel module that often breaks with OS updates); check Pi 5 support before buying; you still need a speaker |

For the least trouble: a USB speakerphone.

## 2. Flash Raspberry Pi OS Bookworm (64-bit)

1. On the PC, install **Raspberry Pi Imager** and choose: your Pi model ->
   **Raspberry Pi OS (64-bit)** (Bookworm; "Lite" is fine and lighter, there's
   no screen needed) -> your SD card or SSD.
2. Click **Edit settings** (the gear) before writing:
   - host name: `raspberrypi` (used in the commands below)
   - user `pi` with a strong password
   - your Wi-Fi network and country, and your time zone
   - **Services: enable SSH** (password, or better your PC's public key)
3. Write, put the card/SSD in the Pi, power it on, wait 2 minutes, then from
   PowerShell on the PC: `ssh pi@raspberrypi.local`.
4. On the Pi: `sudo apt update && sudo apt full-upgrade -y && sudo reboot`.

## 3. Install

On the Pi (over ssh):

```bash
sudo apt install -y git
git clone https://github.com/kylechen839-max/Claude-Personal-Assistant.git hey-claude
cd hey-claude
./scripts/install_pi.sh
```

This is the same script the arm64 CI job runs. It installs the system
packages, creates `.venv`, installs the Python packages (openWakeWord with
`--no-deps`), creates an empty `.env`, and downloads the Whisper and
wake-word models. The first run takes 10-20 minutes on a Pi; running it again
changes nothing.

## 4. Copy the secrets securely

Three files hold secrets: `.env` (API keys, `WEB_TOKEN`), `token.json`
(your Google sign-in) and `credentials.json` (the Google OAuth client). Copy
them **over the local network with scp** (encrypted), from the project folder
on the PC:

```powershell
scp .env token.json credentials.json pi@raspberrypi.local:~/hey-claude/
```

Then on the Pi, make them readable only by you:

```bash
cd ~/hey-claude && chmod 600 .env token.json credentials.json
```

- Don't email them, put them in a cloud drive, or paste them in a chat.
- If you used a USB stick instead, **delete the files from the stick**
  afterwards and empty the trash on the PC (on Windows, files deleted from a
  USB stick skip the Recycle Bin, but check). Better: don't use one.
- `token.json` lets anyone read and change your calendar; treat it like a
  password. If it's ever exposed: Google Account -> Security -> Third-party
  access -> remove "Hey Claude", then sign in again.
- **Google sign-in expiry:** while the Google Cloud consent screen is in
  "Testing" mode, Google expires the sign-in (`token.json`) after 7 days, and
  the calendar stops working until you sign in again. For a device that runs
  for months, open the Google Cloud console -> APIs & Services -> OAuth
  consent screen -> **Publish app** (for your own use no review is needed;
  you'll see an "unverified app" warning once when you sign in again), then
  sign in once more on the PC and copy the new `token.json`.

## 5. Piper voice, preflight with hardware, and the service

```bash
cd ~/hey-claude
.venv/bin/python -m assistant.tts --download-voice en_US-lessac-medium
nano config.toml        # [tts] engine = "piper" ; and on a Pi 4: [stt] model = "tiny.en"
./scripts/preflight.sh --hardware
```

`--hardware` adds two steps: the audio device listing, and a 3 second mic
level test (it tells you when to speak: say "Hey Claude, testing one two
three" from where you'll normally stand). Every row must be `PASS`. The
report is in `logs/preflight-*.txt`. Common fixes:

- `mic-level` too quiet: wrong mic (set `[wake] mic_device` to the index from
  `audio-devices`) or the gain is low (`alsamixer`, F4, raise Capture).
- `mic-level` clipping: lower the capture gain.
- `check` fails on audio: the USB device isn't plugged in, or another
  program has it.
- `git` fails: a file other than `config.toml` was changed on the Pi
  (`git status` shows which); `git checkout -- <file>` undoes it. Changes to
  `config.toml` are fine: they are this machine's settings, and the row says
  "local settings in config.toml".

Then start it at boot:

```bash
./scripts/install_service.sh
```

The service runs `python -m assistant.host wake`, which can also keep the
Pi up to date: put `[update] enabled = true` in `config.local.toml` and the
signing key's line in `~/.config/hey-claude/allowed_signers` (README,
"Automatic updates"). It follows the tested, signed `stable` branch,
restarts on the new version and rolls back if it doesn't come up. Pi and
PC can both have it on; each updates its own checkout.

## 6. Manual voice checks

Do `MANUAL_TEST.md` **Part C** (wake word across the room, latency with
`--timings` compared with the PC, and the 24 h soak with a reboot and a
network drop).

## 7. What runs where

Once the cloud host exists there are three places the assistant can run:

| Where | What it does | Mode |
|---|---|---|
| **Pi (in the room)** | Always-listening wake word, spoken replies | `--mode wake` (the systemd service) |
| **Cloud host (Render)** | Phone app and hands-free voice conversation | `--mode web` |
| **PC** | Development, and the fallback (or the home host instead of a Pi, see [Do I need a Pi?](#do-i-need-a-pi)) | any, by hand; or the HeyClaude tasks |

All of them share the same Pinecone memory (same index; summaries go to the
`voice-assistant` namespace), the same Google Calendar and the same Notion
database, so a note saved from the phone is recalled by the Pi.

**Double-firing risks and the rule:**

- *Two wake-word loops in one room.* If the PC and the Pi both run `--mode
  wake` within earshot, both answer, and a "delete" could be confirmed twice.
  Rule: **only one machine runs the wake word.** With a Pi, on the PC
  don't start `python -m assistant` (wake mode) and remove the HeyClaude
  tasks (`.\scripts\install_windows_service.ps1 -Uninstall`); use
  `--mode text`/`--mode web` for development.
- *Duplicate memories.* Each conversation is summarized once, by the instance
  that had it, so there is no duplication as long as one conversation happens
  in one place.
- *Scheduled things (reminders, a morning briefing), when they're added.*
  These would fire once per running instance: the Pi and the cloud host would
  both remind you. Rule: **exactly one instance is the primary, and only the
  primary runs scheduled or proactive jobs; every other instance only
  answers requests.** Make the Pi the primary (it is the one that can speak
  in the room). When that feature is built, give it an explicit setting
  (e.g. a `role` of `primary`/`secondary`, default `secondary`, set to
  primary only in the Pi's own `config.toml`) so a fresh install can never
  double-fire. If the Pi is down for long, set the cloud host to primary
  instead; never both. Until then there is nothing scheduled, so there is no
  double-firing today.
- *Calendar writes.* Creating or moving an event happens only when you ask,
  in the one place you asked, so there's no duplication.

## 8. Rollback

The PC setup keeps working as-is: nothing in the move changes it, and the
same keys work on both. To go back:

```bash
systemctl --user disable --now hey-claude   # on the Pi
```

then run `python -m assistant` on the PC as before. To retire the Pi for
good, also delete the secrets from it: `rm ~/hey-claude/{.env,token.json,credentials.json}`.

## 9. Go/no-go summary

**Go** for the Pi as the room's assistant when all of these are true:

- [ ] PC: `.\scripts\preflight.ps1` all PASS (hardware rows SKIP)
- [ ] GitHub Actions **pi-arm64** green on the master commit you installed
- [ ] `.\scripts\cloud_smoke.ps1 <url>` all PASS (once the cloud host exists)
- [ ] Pi: `./scripts/preflight.sh --hardware` all PASS
- [ ] Pi: `MANUAL_TEST.md` Part C done (wake word across the room, latency,
      24 h soak with reboot and network drop)

Any FAIL: fix it, re-run that preflight, or roll back (section 8).
