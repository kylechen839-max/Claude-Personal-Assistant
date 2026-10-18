"""Setup check: python -m assistant --check.

Reports what is configured and what is missing without starting the
assistant. Secret values are never printed, only whether they are set.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED_KEYS = ("ANTHROPIC_API_KEY",)
OPTIONAL_KEYS = {"PINECONE_API_KEY": "memory", "NOTION_TOKEN": "Notion tools"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True  # a failed optional check is only a warning


def check_keys(environ=os.environ) -> list[Check]:
    checks = [Check(k, bool(environ.get(k)),
                    "set" if environ.get(k) else "not set (add it to .env)")
              for k in REQUIRED_KEYS]
    for key, feature in OPTIONAL_KEYS.items():
        is_set = bool(environ.get(key))
        checks.append(Check(key, is_set, "set" if is_set
                            else f"not set ({feature} will be off)",
                            required=False))
    return checks


def check_google_files(config: dict) -> list[Check]:
    cal = config["calendar"]
    if not cal["enabled"]:
        return [Check("Google Calendar", True, "disabled in config.toml",
                      required=False)]
    creds, token = Path(cal["credentials"]), Path(cal["token"])
    return [
        Check(str(creds), creds.exists(), "found" if creds.exists() else
              "missing (needed to sign in; copy it from your PC)", required=False),
        Check(str(token), token.exists(), "found" if token.exists() else
              "missing (copy it from your PC, or run "
              "`python -m assistant.tools.calendar --headless`)", required=False),
    ]


def check_wake_model(config: dict, keyword_path: str | None = None) -> Check:
    wake = config["wake"]
    path = keyword_path or wake["keyword_path"]
    if not path:
        return Check("Wake word model", True,
                     f"none set; using built-in '{wake['builtin_keyword']}'",
                     required=False)
    if Path(path).exists():
        return Check("Wake word model", True, f"{path} found")
    return Check("Wake word model", False,
                 f"{path} missing; would fall back to '{wake['builtin_keyword']}'",
                 required=False)


def check_audio_devices(config: dict, sd=None) -> list[Check]:
    """Lists input/output devices; `sd` is the sounddevice module (for tests)."""
    if sd is None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:  # OSError: PortAudio not installed
            return [Check("Audio", False, f"sounddevice unavailable: {e}")]
    try:
        devices = list(sd.query_devices())
    except Exception as e:
        return [Check("Audio", False, f"could not list devices: {e}")]
    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    outputs = [(i, d) for i, d in enumerate(devices) if d["max_output_channels"] > 0]

    def describe(found):
        return "; ".join(f"[{i}] {d['name']}" for i, d in found) or "none found"

    checks = [Check("Input devices", bool(inputs), describe(inputs)),
              Check("Output devices", bool(outputs), describe(outputs))]
    mic = config["wake"]["mic_device"]
    if mic != "":
        if isinstance(mic, str) and mic.isdigit():
            mic = int(mic)
        try:
            info = sd.query_devices(mic, "input")
            checks.append(Check("Configured mic", True,
                                f"{mic!r} -> {info['name']}"))
        except Exception as e:
            checks.append(Check("Configured mic", False,
                                f"{mic!r} not usable: {e}"))
    return checks


def check_whisper_model(config: dict, download_model=None) -> Check:
    """Is the faster-whisper model on disk (so startup needs no download)?"""
    name = config["stt"]["model"]
    if Path(name).is_dir():
        return Check("Whisper model", True, f"local folder {name}")
    if download_model is None:
        try:
            from faster_whisper.utils import download_model
        except ImportError as e:
            return Check("Whisper model", False, f"faster-whisper not installed: {e}")
    try:
        path = download_model(name, local_files_only=True)
    except Exception:
        return Check("Whisper model", False,
                     f"{name} not cached yet (downloads on first run, needs internet)",
                     required=False)
    return Check("Whisper model", True, f"{name} cached at {path}")


def check_tts(config: dict, which=shutil.which) -> Check:
    tts = config["tts"]
    engine = tts["engine"]
    if engine == "piper":
        from assistant.tts import DEFAULT_PIPER_VOICE, piper_voice_problem

        # piper_model is the old name of piper_voice
        voice = tts.get("piper_voice") or tts.get("piper_model")
        problem = piper_voice_problem(voice)
        if problem is None:
            return Check("TTS (piper)", True, f"voice {voice} ok")
        # the assistant falls back to pyttsx3, so this is only a warning
        return Check("TTS (piper)", False,
                     f"{problem}; using pyttsx3 instead. Get it with: python -m "
                     f"assistant.tts --download-voice {DEFAULT_PIPER_VOICE}",
                     required=False)
    if engine == "pyttsx3" and sys.platform.startswith("linux"):
        found = which("espeak-ng") or which("espeak")
        return Check("TTS (pyttsx3)", bool(found),
                     "espeak found" if found else "espeak-ng missing "
                     "(sudo apt install espeak-ng libespeak1)")
    return Check(f"TTS ({engine})", True, "ok")


def check_web(environ=os.environ, find_spec=None) -> list[Check]:
    """The phone web app (--mode web): its token and packages."""
    import importlib.util

    find_spec = find_spec or importlib.util.find_spec
    token = environ.get("WEB_TOKEN", "")
    if not token:
        token_check = Check("WEB_TOKEN", False, "not set (created in .env the "
                            "first time you run --mode web)", required=False)
    elif len(token) < 32:
        token_check = Check("WEB_TOKEN", False, "set but too short (use 32+ "
                            "random characters, or delete it to get a new one)",
                            required=False)
    else:
        token_check = Check("WEB_TOKEN", True, "set")
    missing = [m for m in ("fastapi", "uvicorn") if find_spec(m) is None]
    if find_spec("python_multipart") is None and find_spec("multipart") is None:
        missing.append("python-multipart")
    return [token_check,
            Check("Web app packages", not missing,
                  "fastapi, uvicorn, python-multipart installed" if not missing
                  else f"missing {', '.join(missing)} (pip install -r "
                       "requirements.txt)", required=False)]


def run_checks(config: dict, keyword_path: str | None = None,
               environ=os.environ, sd=None, download_model=None,
               which=shutil.which) -> list[Check]:
    return [*check_keys(environ), *check_google_files(config),
            check_wake_model(config, keyword_path),
            *check_audio_devices(config, sd),
            check_whisper_model(config, download_model),
            check_tts(config, which), *check_web(environ)]


def report(checks: list[Check], output_fn=print) -> int:
    """Print one line per check; return 1 if a required check failed."""
    for c in checks:
        mark = "OK  " if c.ok else ("FAIL" if c.required else "WARN")
        output_fn(f"[{mark}] {c.name}: {c.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    output_fn("\nAll required checks passed." if not failed else
              f"\n{len(failed)} required check(s) failed; see README.md "
              "troubleshooting.")
    return 1 if failed else 0
