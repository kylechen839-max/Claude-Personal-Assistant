"""Settings: built-in defaults <- config.toml <- environment variables.

Secrets live in .env (loaded here); everything else in config.toml.
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
    "assistant": {
        "model": "claude-sonnet-5-5",
        "max_tokens": 1500,  # room for thinking + web search; replies stay short
        "max_history": 40,
        "max_tool_rounds": 6,
        "conversation_timeout": 300,  # seconds idle before a new conversation
        "api_max_retries": 3,
        "api_timeout": 30,
        "timezone": "",  # IANA name, e.g. "America/New_York"; "" = system
    },
    "tts": {"engine": "pyttsx3", "rate": 185, "voice": "",
            "piper_model": "", "piper_executable": "piper"},
    "stt": {"model": "base.en", "device": "cpu", "compute_type": "int8"},
    "wake": {
        "keyword_path": "",  # your "Hey Claude" .onnx (openWakeWord)
        "builtin_keyword": "hey_jarvis",  # used if keyword_path is missing
        "sensitivity": 0.5,
        "chime": True,
        "silence_ms": 800,
        "max_seconds": 15,
        "no_speech_timeout": 5,
        "vad_min_threshold": 0.015,
        "mic_device": "",  # sounddevice name or index; "" = default
    },
    "calendar": {"enabled": True, "credentials": "credentials.json",
                 "token": "token.json", "calendar_id": "primary"},
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
               "min_score": 0.4},
    "logging": {"file": "logs/assistant.log", "level": "INFO"},
}

# Environment variables that override config values.
ENV_OVERRIDES = {
    "CLAUDE_MODEL": ("assistant", "model"),
    "WAKEWORD_MODEL_PATH": ("wake", "keyword_path"),
    "NOTION_ASSIGNMENTS_DB": ("notion", "database_id"),
    "GOOGLE_CREDENTIALS": ("calendar", "credentials"),
    "GOOGLE_TOKEN": ("calendar", "token"),
    "PINECONE_INDEX": ("memory", "index"),
}


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = "config.toml",
                env_file: str | Path | None = ".env") -> dict:
    if env_file and Path(env_file).exists():
        from dotenv import load_dotenv

        load_dotenv(env_file)
    config = copy.deepcopy(DEFAULTS)
    if path and Path(path).exists():
        with open(path, "rb") as f:
            _merge(config, tomllib.load(f))
    for var, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(var):
            config[section][key] = os.environ[var]
    return config


SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "PINECONE_API_KEY", "NOTION_TOKEN",
                   "ELEVENLABS_API_KEY")
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[\w-]+"),               # Anthropic keys
    re.compile(r"\b(?:ntn|secret)_\w{20,}"),    # Notion tokens
    re.compile(r"\bya29\.[\w.-]+"),             # Google access tokens
    re.compile(r"\b1//[\w-]{20,}"),             # Google refresh tokens
    re.compile(r"(?i)(bearer\s+)[\w.~+/=-]+"),
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


def setup_logging(config: dict) -> Path:
    """Detailed log to a rotating file; only warnings on the console."""
    log_file = Path(config["logging"]["file"])
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(config["logging"]["level"].upper())
    file_handler.setFormatter(RedactingFormatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(RedactingFormatter("[%(levelname)s] %(message)s"))
    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
