"""Settings: built-in defaults <- config.toml <- profile <- environment.

Secrets live in .env (loaded here) or, on a server, in the environment;
everything else in config.toml. The "server" profile (ASSISTANT_PROFILE=server,
set in the Docker image) adjusts the settings for a cloud box with no audio
devices; see SERVER_PROFILE.
"""

from __future__ import annotations

import copy
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10
    import tomli as tomllib

DEFAULTS: dict = {
    "profile": "",  # set by load_config: "server" or "" (see active_profile)
    "assistant": {
        "model": "claude-sonnet-5-5",
        "max_tokens": 1500,  # room for thinking + web search; replies stay short
        "max_history": 40,
        "max_tool_rounds": 6,
        "conversation_timeout": 300,  # seconds idle before a new conversation
        "api_max_retries": 3,
        "api_timeout": 30,
        "timezone": "",  # IANA name, e.g. "America/New_York"; "" = system
        "language": "en",  # "en", or "zh" (Mandarin: prompt, speech, phone page)
        # simple turns go to fast_model (router.py); "off" = always `model`
        "fast_model": "claude-haiku-5-5",
        "router": "auto",
        "usage_file": "data/usage.json",  # monthly token totals ("" = not kept)
    },
    "tts": {
        "engine": "auto",  # auto = Kokoro, else Piper, else `fallback`
        "rate": 185, "voice": "",  # pyttsx3
        "piper_voice": "",  # .onnx path; its .onnx.json sits beside it
        "fallback": "pyttsx3",  # used when no neural voice is there
        "kokoro_voice": "af_heart",
        "speed": 1.0,  # Kokoro and ElevenLabs
        "kokoro_model": "models/kokoro/kokoro-v1.0.int8.onnx",
        "kokoro_voices": "models/kokoro/voices-v1.0.bin",
        "elevenlabs_voice": "",  # voice ID; "" = a calm premade voice
        "elevenlabs_model": "eleven_flash_v2_5",
        # phone app: each person picks their own reply voice (voice_picker.py)
        "picker": True,
        "voices_dir": "voices",          # the Piper voices offered
        "voice_file": "data/voice_choice.json",  # the owner's pick ("" = not kept)
        "family_paid_voices": False,     # family-tier members (never beta) may pick ElevenLabs
    },
    "stt": {
        "model": "small.en",  # faster-whisper; base.en/tiny.en are faster (Pi, server)
        "device": "cpu", "compute_type": "int8",
        "beam_size": 5,  # 1 = greedy (faster, mishears more); 0 = the default
        # "auto" = the built-in vocabulary prompt for the language; "" = none
        "initial_prompt": "auto",
        "hotwords": "",  # extra words to favor, e.g. names: "Kyle, Kai"
        # "whisper" (local), or "openai" / "deepgram" (paid, needs
        # OPENAI_API_KEY / DEEPGRAM_API_KEY; local Whisper on any error)
        "engine": "whisper",
        "cloud_model": "",  # "" = gpt-4o-transcribe / nova-3
        "cloud_timeout": 10,  # seconds; then local Whisper
    },
    "wake": {
        "keyword_path": "",  # your "Hey Claude" .onnx (openWakeWord)
        "builtin_keyword": "hey_jarvis",  # used if keyword_path is missing
        "sensitivity": 0.5,
        "chime": True,
        "silence_ms": 800,
        "max_seconds": 15,
        "no_speech_timeout": 5,
        "follow_up_seconds": 5,  # listen this long after a reply; 0 = off
        "follow_up_chime": True,  # soft blip when the follow-up window opens
        # talking over a reply stops it: "wake_word" (say the wake word; fine
        # with speakers), "voice" (any speech louder than the reply's echo;
        # headphones or an echo-cancelling speakerphone) or "off"
        "barge_in": "wake_word",
        "barge_in_ms": 400,  # "voice": this much speech over a reply stops it
        "barge_in_echo_ratio": 2.5,  # "voice": that much louder than its echo
        "vad_min_threshold": 0.015,
        "mic_device": "",  # sounddevice name or index; "" = default
    },
    "calendar": {"enabled": True, "credentials": "credentials.json",
                 "token": "token.json", "calendar_id": "primary",
                 # write refreshed tokens back to token.json (never on a server)
                 "save_token": True,
                 # your own names for event colors: name -> a Google color
                 # (e.g. Basil) or one of your event labels
                 "color_labels": {}},
    "gmail": {"enabled": True,  # uses the [calendar] credentials and token
              "max_summary": 15},   # most emails in one summary
    "notion": {"enabled": True,
               "database_id": "7eefabe3-0226-47a8-a287-584be0d2ea9b"},
    "web": {
        "enabled": True,
        "search": True,             # Anthropic web search (server-side)
        "fetch": True,              # Anthropic web fetch (read a page)
        "browser": True,            # open pages in this computer's browser
        "max_searches": 3,          # per request
        "max_fetches": 3,
        "max_fetch_tokens": 8000,   # cap on each fetched page
        "allowed_domains": [],      # restrict to these sites, or
        "blocked_domains": [],      # never use these (not both)
        "location": {"city": "", "region": "", "country": "", "timezone": ""},
    },
    "memory": {"enabled": True, "index": "project-context",
               "namespace": "voice-assistant", "search_namespaces": "*",
               "embed_model": "llama-text-embed-v2", "top_k": 4,
               "min_score": 0.4,
               "text_fields": ["text", "chunk_text", "content", "summary"],
               # once a week, offer to review this week's notes (memory_review.py)
               "weekly_review": False,
               "review_state_file": "data/memory_review.json"},
    "failover": {  # Pi -> PC -> cloud, see FAILOVER.md
        "role": "",                 # "pi", "pc" or "cloud"; "" = standalone
        "app_origin": "",           # where the phone app is installed from
        "backends": [],             # [{role, url}], highest priority first
        "sync_interval": 30,        # seconds between history syncs
    },
    "history": {
        "enabled": True,
        "path": "data/history.sqlite",  # HISTORY_PATH overrides (cloud: /data/...)
        "retention_days": 0,            # delete unpinned conversations older than this; 0 = keep
        "max_mb": 500,                  # size guard: oldest unpinned go first; 0 = no limit
        "incognito": False,             # true = save nothing (no history, no memory summary)
        "title_model": "claude-haiku-5-5",  # fast model for titles and summaries
    },
    "webapp": {
        "host": "127.0.0.1",        # only this computer; use tailscale serve
        "port": 8765,
        "confirm_timeout": 120,     # seconds to wait for a yes before "no"
        "max_upload_mb": 10,
        "max_attachment_mb": 10,    # per PDF or text file attached to a chat
                                    # message (photos: [tutor] max_image_mb)
        "max_audio_seconds": 60,    # longest recording accepted
        "sessions_file": "data/web_sessions.json",  # signed-in browsers
                                    # ("" = forget them on restart)
        "trusted_proxy": 0,         # proxies adding X-Forwarded-*: 0 = none,
                                    # a count (1 on Render), or the proxy's
                                    # address(es) (TRUSTED_PROXY)
        "allowed_origins": [],      # extra https://... origins, if a proxy
                                    # rewrites the Host header
        "google_connect": False,    # "connect Google" button in the phone app
                                    # (needs a Web application OAuth client)
        "public_origin": "",        # https://... the phone opens; Google
                                    # returns to <public_origin>/google/callback
        "backup_max_mb": 200,       # GET /api/backup (owner): refused above this
        # The public pages (/welcome, /privacy, /terms, the waitlist;
        # public_site.py). Off by default on a PC; the cloud server's
        # profile turns it on (public beta, launched 2026-10-22).
        "public_site": False,
        "support_email": "heyclaude.app@gmail.com",  # contact on the privacy and terms pages
                                    # ("" = "contact the owner")
        "legal_draft": False,       # True: a visible "Draft" note on privacy and terms
        "waitlist_file": "",        # POST /api/waitlist entries, a private
                                    # file ("" = waitlist closed; the server
                                    # profile keeps it on the disk)
    },
    # python -m assistant --mode web on a public server (ASSISTANT_PROFILE=server)
    "cloud": {
        # Only peers in these networks may set X-Forwarded-For/-Proto/-Host.
        # None by default: anyone else in a private network (a Docker bridge,
        # the LAN) could otherwise make up client addresses. The server
        # profile sets Render's; CLOUD_TRUSTED_PROXIES overrides it.
        "trusted_proxies": [],
        "client_ip_header": "",         # e.g. "cf-connecting-ip"; "" = X-Forwarded-For
        "login_max_failures": 5,        # wrong tokens per address ...
        "login_window": 900,            # ... in this many seconds -> locked out
        "login_lockout": 900,           # seconds (the right token always works)
        "login_global_free": 20,        # wrong tokens from everyone per window,
        "login_global_delay": 1.0,      # then each further one waits this long
        "requests_per_minute": 120,     # per address, every route but /healthz
        "max_body_kb": 16,              # any request that isn't an audio upload
        "uploads_per_address": 2,       # requests sending a body at once, per address
        "uploads_total": 32,            # ... and from everyone
        "logins_total": 8,              # sign-in attempts at once, from everyone
        "body_timeout": 10,             # seconds (+1 s per 64 KB) to send a body
    },
    "voice": {                      # hands-free voice conversation (web app)
        "vad": "silero",            # "silero" (in the browser), or "energy"
        "silence_ms": 700,          # this long a pause ends your turn
        "min_utterance_ms": 300,    # shorter sounds are ignored
        "max_utterance_s": 30,      # a turn is cut off after this long
        "barge_in": "voice",        # talking over a reply stops it; "off" = never
        "barge_in_ms": 250,         # talk this long over a reply to stop it
        "follow_up_seconds": 5,     # "still listening" cue after a reply; 0 = off
        "vad_threshold": 0.5,       # Silero speech probability, 0-1
        "idle_timeout": 120,        # seconds of quiet before it ends
        "max_session_minutes": 30,
        "utterances_per_minute": 30,  # per connection; more closes it
        "debug_panel": False,       # show latency timings on the page
    },
    "update": {
        "enabled": False,       # host supervisor: update from `channel`
        "channel": "stable",    # git branch; fast-forwarded only after green CI
        "check_minutes": 15,
        "local_branch": "master",  # only this checked-out branch is updated
        # the origin it must come from, and who must have signed it
        "repo": "github.com/kylechen839-max/Claude-Personal-Assistant",
        "allowed_signers": "",  # "" = the per-user default (see updater.py)
        # true = run unsigned updates too: anyone who can push to master or
        # stable can then run code on this machine
        "trust_unsigned": False,
        "health_seconds": 180,  # for the restarted web app to answer /healthz
    },
    # python -m assistant.watch: health and function checks of every web app
    # instance on this machine (assistant/watch.py, README "Health checks").
    # From config.toml for the whole machine; enabled, label, public_url and
    # restart_command also from each instance's own config.<name>.toml.
    "watch": {
        "enabled": True,            # false = this instance isn't checked
        "label": "",                # its name in alerts; "" = "Mom's app" from config.mom.toml
        # the address the phone opens: "auto" = [webapp] public_origin, else
        # the `tailscale serve` entry for its port; "" = don't check one
        "public_url": "auto",
        "functional_hours": 6,      # the full test conversation this often (and
                                    # after every restart or new version)
        "retry_minutes": 30,        # a failed one is tried again after this long
        "gate": True,               # new code must pass that conversation on a
                                    # side port before it replaces the running one
        "side_port_offset": 100,    # side port = the instance's port + this
        "candidate_seconds": 300,   # for the side-port copy to come up
        "start_grace_seconds": 300,  # an app not answering gets this long (a
                                    # restart, a reboot) before it's healed
        "rollback": True,           # still failing after a restart: back to the
                                    # last version that passed (git reset --keep)
        "restart_command": [],      # self-heal; [] = its Task Scheduler task on
                                    # Windows, none elsewhere (e.g. ["systemctl",
                                    # "--user", "restart", "hey-claude-web"])
        "banner": True,             # the owner's phone app shows apps that are down
        "coordinator": True,        # tell the dev team coordinator ([devteam])
        "status_file": "data/watch_status.json",
        "stale_minutes": 30,        # banner: "checks stopped" after this long
        # the cloud copy (Render), e.g. https://hey-claude.onrender.com: its
        # /healthz is checked and alerted on; never restarted from here
        "cloud_url": "",
    },
    "lecture": {
        "enabled": True,
        "model": "base.en",          # faster-whisper model: "tiny.en" is faster
        "upload_dir": "data/lectures",
        "max_upload_mb": 300,        # per recording (75 min of AAC is ~70 MB)
        "chunk_mb": 4,               # the phone uploads in pieces this big
        "keep_audio": False,         # delete audio once it is transcribed
        "notes_database_id": "",     # "" = find or create "Lecture Notes"
    },
    "tutor": {"enabled": True,      # homework photos in the phone app
              "max_image_mb": 8,
              "max_tokens": 1200},
    "notes": {"enabled": True,      # remember_note / recall_notes (needs memory)
              "min_score": 0.3},
    "devteam": {"enabled": False,   # talk to the dev team coordinator (docs/COORDINATOR_LINK.md)
                "coordinator_session": "",  # session_... or its claude.ai/code URL
                "claude_command": "claude",
                "repo_dir": "",             # where to run the CLI; "" = here
                "timeout": 60,
                "state_file": "data/devteam.json"},
    "home_assistant": {             # smart home (README "Smart home"); owner only
        "enabled": False,           # needs HA_URL and HA_TOKEN in .env
        "url": "",                  # HA_URL overrides, e.g. http://homeassistant.local:8123
        # the only kinds of device listed or controlled
        "domains": ["light", "switch", "fan", "climate", "cover", "media_player",
                    "sensor", "lock"],
        "scripts": [],              # script./scene./automation. ids it may run
        "secure_entities": [],      # more entity ids that always ask first
        "cache_seconds": 30,        # device list kept this long
        "timeout": 10,              # seconds per request
    },
    "teamchat": {                   # read Slack, Discord, GitHub (README "Team chat"); owner only
        "enabled": False,
        "slack": False,             # needs SLACK_USER_TOKEN in .env
        "discord": False,           # needs DISCORD_BOT_TOKEN in .env
        "github": False,            # needs GITHUB_TOKEN in .env
        "channels": [],             # Slack/Discord channels it may read; [] = all the token sees
        "github_repos": [],         # owner/name repos it may read; [] = all the token sees
        "max_messages": 100,        # per read or search (at most 100)
        "timeout": 15,              # seconds per request
        "digest_database_id": "",   # Notion database for digests; "" = find/create "Chat Digests"
        "digest_state_file": "data/teamchat_digests.json",  # remembers that database's id
    },
    "spotify": {                    # play, pause, skip by voice (Premium; README "Spotify")
        "enabled": False,           # needs SPOTIFY_CLIENT_ID and a one-time sign-in
        "token": "data/spotify_token.json",  # written privately; delete to disconnect
        # where the browser lands after `python -m assistant.tools.spotify`
        # (add it to your Spotify app's redirect URIs, exactly)
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "pause_while_listening": True,  # wake word: pause music on this computer, resume after
        "local_device": "",         # this computer's Spotify device name; "" = its hostname
        "volume_step": 15,          # "turn it up" / "down" by this much (percent)
        "timeout": 10,              # seconds per Spotify request
    },
    "push": {                       # phone notifications (push.py; README "Study briefings")
        "enabled": True,            # "Enable notifications" in Settings; needs USER_DATA_KEY
        # the owner's subscriptions, briefing state and (without
        # VAPID_PRIVATE_KEY) the generated VAPID key; invited people's
        # are in their own [family] dir folder
        "owner_dir": "data/student",
        "subject": "",              # VAPID contact, mailto: or https:; "" = public_origin
        "max_devices": 5,           # per person (at most 5); the oldest is dropped
        "ttl": 86400,               # seconds a push service keeps an undelivered one
        "timeout": 10,              # seconds per push request
    },
    "proactive": {                  # study briefings (proactive.py); only with a device subscribed
        "enabled": True,
        "check_minutes": 120,       # how often the rules are looked at
        "morning": "07:30",         # today's and tomorrow's deadlines
        "evening": "20:30",         # tomorrow's deadlines
        "weekly_day": "sun",        # the week ahead, on this day ...
        "weekly_time": "18:00",     # ... at this time
        "quiet_start": "22:00",     # nothing between these (the person's time zone)
        "quiet_end": "07:30",
        "max_per_day": 2,           # notifications per person per day
        "exam_days": 14,            # exam reminders from this many days out
        "class_minutes": 75,        # "how did class go?" this long after a class starts
        "model": "",                # wording the message; "" = [assistant] fast_model
        "max_tokens": 1000,         # room for adaptive thinking (Sonnet); the text is cut to 240
    },
    "family": {                   # invite family members (cloud web app)
        "enabled": False,           # the server profile turns it on
        "dir": "data/users",        # users.json + one folder per person
        "max_users": 10,
        "invite_days": 30,          # an invite link works this long, once
        # Their own Google (Calendar, Gmail drafts) from their phone: needs
        # a Web application OAuth client (GOOGLE_WEB_CLIENT_JSON, or this
        # file) and the server's public address (public_origin)
        "google": True,
        "google_client": "",
        # beta testers' (tier "beta", Calendar only) Web client, in its own
        # Google Cloud project (GOOGLE_PUBLIC_CLIENT_JSON, or this file);
        # unset: beta testers can't connect Google (never the family client)
        "public_google_client": "",
        # Their own Notion's Assignments database (notion_web.py, either
        # tier): needs a public Notion integration (NOTION_OAUTH_CLIENT_ID,
        # NOTION_OAUTH_CLIENT_SECRET), the server's address and USER_DATA_KEY
        "notion": True,
        "max_history_mb": 20,       # each person's history (not [history] max_mb)
        # each invited person's limits (quotas.py; never the owner's), in
        # memory: a restart resets them. 0 = no limit
        "turns_per_minute": 6,
        "turns_per_day": 200,       # a day in their own time zone (else UTC)
        "stt_seconds_per_day": 1800,  # seconds of their speech transcribed
        # "Download my data" (account.py): the zip's contents, before
        # compression, at most this big
        "export_max_mb": 50,
        # Chinese speakers' speech on a server whose language is English:
        # loaded on first use (Whisper small ~0.5 GB RAM, the voice ~60 MB)
        "zh_stt_model": "small",
        "zh_piper_voice": "voices/zh_CN-huayan-medium.onnx",
    },
    "student": {                    # deadlines from Canvas, emails, by hand (README "Student deadlines")
        "enabled": True,            # the deadline tools and the phone's "What I captured" card
        "dir": "data/student",      # the owner's deadlines.sqlite (invited people: [family] dir/<id>/)
        "sync_minutes": 15,         # "Sync now" at most this often per person
        "gmail": True,              # also read Canvas's notification emails (read-only) when Gmail is connected
        # reads emails and pages into deadlines; "" = [assistant] fast_model
        "extract_model": "",
        "max_items": 2000,          # deadlines kept per person
        "max_per_source": 500,      # ... and per source (feed, API, emails, ...)
    },
    "canvas": {                     # where deadlines come from (canvas.py); never a school password
        # the owner's school, for CANVAS_TOKEN (CANVAS_BASE_URL overrides),
        # e.g. "https://canvas.school.edu"; invited people paste their own
        "base_url": "",
        "pages": False,             # also read module pages (one Claude call per sync for new pages)
        "max_pages": 20,            # module pages read per sync, at most
        "timeout": 15,              # seconds per Canvas request
    },
    "logging": {"file": "logs/assistant.log", "level": "INFO",
                "console_level": "WARNING"},
}

# Settings for a headless cloud server (the Docker image): no speakers, mic
# or wake word; Piper with the voice baked into the image, then espeak-ng;
# nothing written to disk (Google tokens are refreshed in memory, logs go to
# stdout for the host's log viewer); no PC-only tools.
SERVER_PROFILE: dict = {
    "tts": {"engine": "piper", "piper_voice": "voices/en_US-lessac-medium.onnx",
            "fallback": "espeak", "voice_file": "/data/voice_choice.json"},
    # baked into the image; small.en's RAM on Render is unmeasured
    "stt": {"model": "base.en"},
    "wake": {"chime": False, "follow_up_seconds": 0},
    "calendar": {"save_token": False},
    "web": {"browser": False},
    "webapp": {"host": "0.0.0.0", "max_upload_mb": 4,
               "sessions_file": "/data/web_sessions.json",  # on the disk
               "public_site": True,   # on since the public beta launch (Kyle, 2026-10-22)
               "waitlist_file": "/data/waitlist.json"},  # on the disk
    # Render's TLS proxy reaches the container from its 10.x private network
    # (check the "Proxy check" lines in the log after the first deploy).
    "cloud": {"trusted_proxies": ["10.0.0.0/8"]},
    "family": {"enabled": True, "dir": "/data/users"},  # on the disk
    "student": {"dir": "/data/student"},  # on the disk
    "memory": {"review_state_file": "/data/memory_review.json"},  # on the disk
    "assistant": {"usage_file": "/data/usage.json"},  # on the disk
    "push": {"owner_dir": "/data/student"},  # on the disk
    "logging": {"file": "", "console_level": "INFO"},
}
PROFILES = {"server": SERVER_PROFILE}

# Environment variables that override config values.
ENV_OVERRIDES = {
    "CLAUDE_MODEL": ("assistant", "model"),
    "CLAUDE_FAST_MODEL": ("assistant", "fast_model"),
    "CLAUDE_ROUTER": ("assistant", "router"),  # "off" = always the main model
    "WAKEWORD_MODEL_PATH": ("wake", "keyword_path"),
    "NOTION_ASSIGNMENTS_DB": ("notion", "database_id"),
    "GOOGLE_CREDENTIALS": ("calendar", "credentials"),
    "GOOGLE_TOKEN": ("calendar", "token"),
    "PINECONE_INDEX": ("memory", "index"),
    "ASSISTANT_ROLE": ("failover", "role"),
    "APP_ORIGIN": ("failover", "app_origin"),
    "HISTORY_PATH": ("history", "path"),
    "ASSISTANT_TIMEZONE": ("assistant", "timezone"),
    "WHISPER_MODEL": ("stt", "model"),
    "STT_ENGINE": ("stt", "engine"),  # "openai" / "deepgram" on a server
    "ZH_WHISPER_MODEL": ("family", "zh_stt_model"),
    "PUBLIC_ORIGIN": ("webapp", "public_origin"),  # e.g. https://hey-claude.onrender.com
    "CLOUD_TRUSTED_PROXIES": ("cloud", "trusted_proxies"),  # comma-separated
    "CLOUD_CLIENT_IP_HEADER": ("cloud", "client_ip_header"),
    "PORT": ("webapp", "port"),                # set by hosts such as Render
    "TRUSTED_PROXY": ("webapp", "trusted_proxy"),
    "HA_URL": ("home_assistant", "url"),
    "CANVAS_BASE_URL": ("canvas", "base_url"),
}


# Variables that point at the owner's own data or accounts. A separate
# instance (--env-file other than .env, e.g. .env.mom) or one where someone
# connects their own Google account must not inherit them from the shell:
# load_dotenv never overrides a variable that is already set, and
# google_auth reads GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON before any file.
ISOLATED_ENV_VARS = ("GOOGLE_TOKEN_JSON", "GOOGLE_CLIENT_JSON", "GOOGLE_TOKEN",
                     "GOOGLE_CREDENTIALS", "HISTORY_PATH", "WEB_TOKEN",
                     "NOTION_TOKEN", "NOTION_ASSIGNMENTS_DB", "PINECONE_API_KEY",
                     "PINECONE_INDEX", "HA_URL", "HA_TOKEN", "SENTRY_DSN",
                     "USER_DATA_KEY",
                     "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET",
                     "CANVAS_TOKEN", "CANVAS_ICS_URL",
                     "VAPID_PRIVATE_KEY")
# USER_DATA_KEY too: it opens the Anthropic keys stored under [family] dir,
# so a separate instance sets its own (or none) and never borrows the
# owner's from the shell.
# (Not the cloud speech-to-text keys either, OPENAI_API_KEY and
# DEEPGRAM_API_KEY: OpenAI's quickstart puts its key in the Windows user
# environment, and a separate instance only uses one with its own [stt]
# engine, which config.mom.toml pins to "whisper".)
# (Not the team chat tokens: a separate instance never registers the chat
# tools (cli.main), so an inherited GITHUB_TOKEN, common in a developer's
# environment, must not stop a family member's app from starting.)


def inherited_env_conflicts(inherited, env_file: str | Path | None) -> list[str]:
    """Names (never values) of ISOLATED_ENV_VARS that were already set in the
    environment before env_file was loaded, unless env_file sets the same
    value (a wrapper that loaded this very file first)."""
    from_file = {}
    if env_file and Path(env_file).exists():
        from dotenv import dotenv_values

        from_file = dotenv_values(env_file)
    return [var for var in ISOLATED_ENV_VARS
            if inherited.get(var) and inherited[var] != from_file.get(var)]


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def active_profile(environ=os.environ) -> str:
    """"server" in the cloud image (ASSISTANT_PROFILE), else "" (a PC or Pi)."""
    profile = environ.get("ASSISTANT_PROFILE", "").strip().lower()
    if profile and profile not in PROFILES:
        raise ValueError(f"Unknown ASSISTANT_PROFILE {profile!r} "
                         f"(choose from: {', '.join(PROFILES)})")
    return profile


def local_config_path(path: str | Path) -> Path:
    """config.toml -> config.local.toml (other.toml -> other.local.toml)."""
    p = Path(path)
    return p.with_name(p.stem + ".local" + p.suffix)


def load_config(path: str | Path | None = "config.toml",
                env_file: str | Path | None = ".env",
                profile: str | None = None) -> dict:
    if env_file and Path(env_file).exists():
        from dotenv import load_dotenv

        load_dotenv(env_file)
    config = copy.deepcopy(DEFAULTS)
    if path:
        # config.toml, then this machine's own config.local.toml beside it
        # (gitignored: per-machine settings that never block an update)
        for file in (Path(path), local_config_path(path)):
            if file.exists():
                with open(file, "rb") as f:
                    _merge(config, tomllib.load(f))
    profile = active_profile() if profile is None else profile
    if profile:
        _merge(config, copy.deepcopy(PROFILES[profile]))
    config["profile"] = profile
    for var, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(var):
            config[section][key] = os.environ[var]
    return config


SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "PINECONE_API_KEY", "NOTION_TOKEN",
                   "ELEVENLABS_API_KEY", "WEB_TOKEN", "GOOGLE_TOKEN_JSON",
                   "GOOGLE_CLIENT_JSON", "GOOGLE_WEB_CLIENT_JSON",
                   "GOOGLE_PUBLIC_CLIENT_JSON", "HA_TOKEN", "SLACK_USER_TOKEN",
                   "DISCORD_BOT_TOKEN", "GITHUB_TOKEN", "SENTRY_DSN",
                   # opens the people's own Anthropic keys (userkeys.py)
                   "USER_DATA_KEY",
                   # the public Notion integration (notion_web.py)
                   "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET",
                   # the owner's Canvas (canvas.py): a token, and a feed URL
                   # that is a secret itself
                   "CANVAS_TOKEN", "CANVAS_ICS_URL",
                   # optional cloud speech-to-text (stt_cloud.py)
                   "OPENAI_API_KEY", "DEEPGRAM_API_KEY",
                   # signs this server's phone notifications (push.py)
                   "VAPID_PRIVATE_KEY")
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[\w-]+"),               # Anthropic keys
    re.compile(r"\bsk-(?:proj-)?[\w-]{20,}"),   # OpenAI keys (cloud STT)
    re.compile(r"\b(?:ntn|secret)_\w{20,}"),    # Notion tokens
    re.compile(r"\bya29\.[\w.-]+"),             # Google access tokens
    re.compile(r"\b1//[\w-]{20,}"),             # Google refresh tokens
    re.compile(r"(?i)(bearer\s+)[\w.~+/=-]+"),
    re.compile(r"\bhc1\.[\w.-]+"),             # failover access tokens
    re.compile(r"\bhcinv\.[\w-]+"),            # family invite tokens
    re.compile(r"(#invite=)[^&\s\"']+"),         # ... and invite links
    # OAuth / API fields in JSON, dict reprs, form bodies and URLs
    re.compile(r"(?i)(\b(?:access_token|refresh_token|id_token|client_secret"
               r"|code_verifier|api_key|apikey)[\"']?\s*[:=]\s*[\"']?)"
               r"[^\"'&,\s}]+"),
    re.compile(r"([?&]code=)[^&\s\"']+"),      # OAuth sign-in codes
    re.compile(r"\bxox[abpe]-[\w-]+"),           # Slack tokens
    re.compile(r"\b(?:github_pat_\w+|gh[pousr]_\w{30,})"),  # GitHub tokens
    re.compile(r"\b[\w-]{24,}\.[\w-]{6}\.[\w-]{27,}"),  # Discord bot tokens
    re.compile(r"\b\d{1,6}~[A-Za-z0-9]{32,}"),     # Canvas access tokens
    re.compile(r"(/feeds/calendars/)[\w.-]+"),    # Canvas calendar feed links
]


def redact(text: str) -> str:
    """Mask API keys, tokens and OAuth secrets in a log line."""
    for var in SECRET_ENV_VARS:
        value = os.environ.get(var, "")
        if len(value) >= 8:
            text = text.replace(value, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "")
                           + "[REDACTED]", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formats as usual (traceback included), then masks secrets: with
    level DEBUG, libraries such as requests_oauthlib log whole tokens."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(config: dict) -> Path | None:
    """Detailed log to a rotating file; only warnings on the console.

    With no file (the server profile) everything at `level` goes to the
    console instead, where the host's log viewer picks it up."""
    cfg = config["logging"]
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    log_file = Path(cfg["file"]) if cfg["file"] else None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setLevel(cfg["level"].upper())
        file_handler.setFormatter(RedactingFormatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(file_handler)
    console = logging.StreamHandler()
    if log_file:
        console.setLevel(cfg.get("console_level", "WARNING").upper())
        console.setFormatter(RedactingFormatter("[%(levelname)s] %(message)s"))
    else:
        console.setLevel(cfg["level"].upper())
        console.setFormatter(RedactingFormatter(
            "%(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(console)
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
