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
    },
    "stt": {"model": "base.en", "device": "cpu", "compute_type": "int8"},
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
        "vad_min_threshold": 0.015,
        "mic_device": "",  # sounddevice name or index; "" = default
    },
    "calendar": {"enabled": True, "credentials": "credentials.json",
                 "token": "token.json", "calendar_id": "primary",
                 # write refreshed tokens back to token.json (never on a server)
                 "save_token": True},
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
               "text_fields": ["text", "chunk_text", "content", "summary"]},
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
        "barge_in_ms": 250,         # talk this long over a reply to stop it
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
                "state_file": "data/devteam.json",
                # The PC hub: chat with the coordinator at /devteam on this
                # PC's web app (loopback only; docs/COORDINATOR_LINK.md).
                "hub": False,
                "hub_sessions": [],         # coordinator session ids; [] = coordinator_session
                "poll_seconds": 4,          # how often the hub reads Notion, at most
                "hub_state_file": "data/devhub.json"},
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
    "family": {                     # invite family members (cloud web app)
        "enabled": False,           # the server profile turns it on
        "dir": "data/users",        # users.json + one folder per person
        "max_users": 10,
        "invite_days": 30,          # an invite link works this long, once
        "zh_stt_model": "small",    # Whisper for Chinese speakers (piece B)
        "zh_piper_voice": "voices/zh_CN-huayan-medium.onnx",  # (piece B)
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
            "fallback": "espeak"},
    "wake": {"chime": False, "follow_up_seconds": 0},
    "calendar": {"save_token": False},
    "web": {"browser": False},
    "webapp": {"host": "0.0.0.0", "max_upload_mb": 4,
               "sessions_file": "/data/web_sessions.json"},  # on the disk
    # Render's TLS proxy reaches the container from its 10.x private network
    # (check the "Proxy check" lines in the log after the first deploy).
    "cloud": {"trusted_proxies": ["10.0.0.0/8"]},
    "family": {"enabled": True, "dir": "/data/users"},  # on the disk
    "logging": {"file": "", "console_level": "INFO"},
}
PROFILES = {"server": SERVER_PROFILE}

# Environment variables that override config values.
ENV_OVERRIDES = {
    "CLAUDE_MODEL": ("assistant", "model"),
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
    "CLOUD_TRUSTED_PROXIES": ("cloud", "trusted_proxies"),  # comma-separated
    "CLOUD_CLIENT_IP_HEADER": ("cloud", "client_ip_header"),
    "PORT": ("webapp", "port"),                # set by hosts such as Render
    "TRUSTED_PROXY": ("webapp", "trusted_proxy"),
}


# Variables that point at the owner's own data or accounts. A separate
# instance (--env-file other than .env, e.g. .env.mom) or one where someone
# connects their own Google account must not inherit them from the shell:
# load_dotenv never overrides a variable that is already set, and
# google_auth reads GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON before any file.
ISOLATED_ENV_VARS = ("GOOGLE_TOKEN_JSON", "GOOGLE_CLIENT_JSON", "GOOGLE_TOKEN",
                     "GOOGLE_CREDENTIALS", "HISTORY_PATH", "WEB_TOKEN",
                     "NOTION_TOKEN", "NOTION_ASSIGNMENTS_DB", "PINECONE_API_KEY",
                     "PINECONE_INDEX")


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
                   "GOOGLE_CLIENT_JSON")
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[\w-]+"),               # Anthropic keys
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
