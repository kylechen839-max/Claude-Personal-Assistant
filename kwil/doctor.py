"""Setup check: python -m kwil --check.

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


def check_google_files(config: dict, verify=None,
                       environ=os.environ) -> list[Check]:
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
              "`python -m kwil.tools.calendar --headless`)", required=False),
    ] + check_calendar_scopes(config) + check_gmail_scopes(config) + (
        check_google_sign_in(config, verify, environ) if token.exists() else [])


def check_google_sign_in(config: dict, verify=None,
                         environ=os.environ) -> list[Check]:
    """Does Google still accept the saved sign-in? `verify`
    (google_auth.verify_sign_in, one token refresh) is passed only by
    `python -m kwil --check`; None (tests, other callers): no network, no
    line. A revoked or expired sign-in is said in plain words."""
    if verify is None:
        return []
    from kwil.tools.google_auth import owner_connected, owner_environ

    cal = config["calendar"]
    problem = verify(cal["credentials"], cal["token"],
                     environ=owner_environ(config, environ),
                     in_app=owner_connected(config))
    return [Check("Google sign-in", problem is None,
                  "works (Google accepted it just now)" if problem is None
                  else problem, required=False)]


def check_calendar_scopes(config: dict) -> list[Check]:
    """An older token.json lacks the read-only calendar list and labels
    scopes: the core Calendar tools work, labels and other calendars don't."""
    from kwil.tools.google_auth import (CALENDAR_EXTRA_SCOPES,
                                             SIGN_IN_COMMAND, missing_scopes)

    try:
        lacking = missing_scopes(CALENDAR_EXTRA_SCOPES, config["calendar"]["token"])
    except (OSError, ValueError) as e:
        return [Check("Calendar labels access", False, f"token unreadable: {e}",
                      required=False)]
    return [Check("Calendar labels access", not lacking,
                  "granted (read-only calendar list and labels)" if not lacking
                  else "not granted yet (re-run the Google sign-in: "
                  f"`{SIGN_IN_COMMAND}`)", required=False)]


def check_gmail_scopes(config: dict) -> list[Check]:
    """An older token.json has only the Calendar scope; Gmail needs more."""
    from kwil.tools.google_auth import (GMAIL_SCOPES, SIGN_IN_COMMAND,
                                             missing_scopes)

    if not config.get("gmail", {}).get("enabled", True):
        return []
    try:
        lacking = missing_scopes(GMAIL_SCOPES, config["calendar"]["token"])
    except (OSError, ValueError) as e:
        return [Check("Gmail access", False, f"token unreadable: {e}",
                      required=False)]
    return [Check("Gmail access", not lacking, "granted (read + drafts)"
                  if not lacking else "not granted yet (re-run the Google "
                  f"sign-in: `{SIGN_IN_COMMAND}`)", required=False)]


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
    if not inputs:
        return checks
    # The device the assistant will open, resolved by the same code it uses
    # (so mic_device = 0 is index 0, not "the default").
    from kwil.audio import mic_that_opens, resolve_mic_device

    configured = config["wake"]["mic_device"]
    wanted = resolve_mic_device(configured)
    try:
        info = mic_that_opens(configured, sd)
    except Exception as e:
        label = "Configured mic" if wanted is not None else "Default mic"
        checks.append(Check(label, False, f"{configured!r} not usable: {e}"))
        return checks
    opened = f"[{info.get('index', '?')}] {info['name']}"
    if wanted is None:
        checks.append(Check("Mic that will open", True,
                            f"{opened} (the default; set [wake] mic_device to change)"))
    elif isinstance(wanted, int) and info.get("index", wanted) != wanted:
        checks.append(Check("Configured mic", False,
                            f"{configured!r} asked for index {wanted} but {opened} "
                            "would open"))
    else:
        checks.append(Check("Configured mic", True, f"{configured!r} -> {opened}"))
    return checks


def check_whisper_model(config: dict, download_model=None) -> Check:
    """Is the faster-whisper model on disk (so startup needs no download)?
    Also names the beam size (1 = greedy, which mishears more)."""
    stt = config["stt"]
    name = stt["model"]
    beam = int(stt.get("beam_size") or 5)  # 0 = the default
    how = f"beam {beam}" + (" (greedy: faster, mishears more)" if beam == 1 else "")
    if Path(name).is_dir():
        return Check("Whisper model", True, f"local folder {name}, {how}")
    if download_model is None:
        try:
            from faster_whisper.utils import download_model
        except ImportError as e:
            return Check("Whisper model", False, f"faster-whisper not installed: {e}")
    try:
        path = download_model(name, local_files_only=True)
    except Exception:
        return Check("Whisper model", False,
                     f"{name} not cached yet (downloads on first run, needs internet; "
                     "small.en is about 480 MB)", required=False)
    return Check("Whisper model", True, f"{name} cached at {path}, {how}")


def check_cloud_stt(config: dict, environ=os.environ) -> list[Check]:
    """[stt] engine = "openai" / "deepgram": is its key set (never the value)?"""
    from kwil.stt_cloud import KEY_ENV

    engine = str(config["stt"].get("engine") or "whisper").strip().lower()
    if engine == "whisper":
        return []
    if engine not in KEY_ENV:
        return [Check(f"STT ({engine})", False, "unknown engine; using local Whisper "
                      "(choose whisper, openai or deepgram)", required=False)]
    var = KEY_ENV[engine]
    if environ.get(var):
        return [Check(f"STT ({engine})", True,
                      f"{var} set; local Whisper is the fallback")]
    return [Check(f"STT ({engine})", False, f"{var} not set in .env; using local "
                  "Whisper instead", required=False)]


def check_tts(config: dict, which=shutil.which, environ=os.environ) -> Check:
    from kwil import tts_kokoro
    from kwil.tts import DEFAULT_PIPER_VOICE, piper_voice_problem

    tts = config["tts"]
    engine = tts["engine"]
    kokoro_hint = "python -m kwil --download-voice kokoro"
    if engine == "elevenlabs":
        if environ.get("ELEVENLABS_API_KEY"):  # never the value
            return Check("TTS (elevenlabs)", True,
                         "ELEVENLABS_API_KEY set; the local voice is the fallback")
        return Check("TTS (elevenlabs)", False, "ELEVENLABS_API_KEY not set in "
                     ".env; using the local voice instead", required=False)
    if engine in ("auto", "kokoro"):
        problem = tts_kokoro.kokoro_problem(tts.get("kokoro_model"),
                                            tts.get("kokoro_voices"))
        if problem is None:
            return Check(f"TTS ({engine})", True,
                         f"Kokoro, voice {tts.get('kokoro_voice') or 'af_heart'}")
        if engine == "kokoro":
            return Check("TTS (kokoro)", False, f"{problem}; using Piper or "
                         f"pyttsx3 instead. Get it with: {kokoro_hint}",
                         required=False)
        engine = "piper" if tts.get("piper_voice") else "pyttsx3"
        if engine == "piper" and piper_voice_problem(tts["piper_voice"]) is None:
            return Check("TTS (auto)", True, f"Piper voice {tts['piper_voice']}; "
                         f"for a more natural voice: {kokoro_hint}")
        engine = "pyttsx3"
    if engine == "piper":
        # piper_model is the old name of piper_voice
        voice = tts.get("piper_voice") or tts.get("piper_model")
        problem = piper_voice_problem(voice)
        if problem is None:
            return Check("TTS (piper)", True, f"voice {voice} ok")
        # the assistant falls back to pyttsx3, so this is only a warning
        return Check("TTS (piper)", False,
                     f"{problem}; using pyttsx3 instead. Get it with: python -m "
                     f"kwil.tts --download-voice {DEFAULT_PIPER_VOICE}",
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


def check_google_env(config: dict, environ=os.environ, verify=None) -> list[Check]:
    """Server variant: the OAuth files come from GOOGLE_*_JSON secrets, or
    the owner connected Google in the app ([webapp] owner_google), which
    then wins (google_auth.owner_environ)."""
    from kwil.tools.calendar import CLIENT_ENV, TOKEN_ENV, token_info
    from kwil.tools.google_auth import owner_connected, owner_environ

    if not config["calendar"]["enabled"]:
        return [Check("Google Calendar", True, "disabled in config.toml",
                      required=False)]
    if owner_connected(config):
        return [Check("Google account", True, "connected in the app (token on "
                      "the server's disk, refreshed in memory)"),
                *check_google_sign_in(config, verify, environ)]
    try:
        info, from_env = token_info(config["calendar"]["token"],
                                    config["calendar"]["credentials"],
                                    owner_environ(config, environ))
    except ValueError as e:  # the message names the variable, not its value
        return [Check(TOKEN_ENV, False, str(e), required=False)]
    if not from_env:
        how = ("tap Connect Google account in the app, or paste the contents "
               "of token.json" if config["webapp"].get("owner_google")
               else "paste the contents of token.json")
        return [Check(TOKEN_ENV, False, f"not set (calendar tools will be off; "
                      f"{how})", required=False)]
    missing = [k for k in ("refresh_token", "client_id", "client_secret")
               if not info.get(k)]
    if missing:
        hint = (f"; set {CLIENT_ENV} too" if "client_secret" in missing
                or "client_id" in missing else "")
        return [Check(TOKEN_ENV, False, f"set, but has no {', '.join(missing)}"
                      f"{hint}", required=False)]
    return [Check(TOKEN_ENV, True, "set (refreshed in memory, never saved)"),
            *check_google_sign_in(config, verify, environ)]


def check_server(config: dict, environ=os.environ) -> list[Check]:
    """Things a public server needs that a PC doesn't."""
    token = environ.get("WEB_TOKEN", "")
    checks = [Check("WEB_TOKEN", len(token) >= 32,
                    "set" if len(token) >= 32 else
                    ("too short (32+ random characters)" if token else
                     "not set (the Render Blueprint generates it)"))]
    tz = config["assistant"]["timezone"]
    checks.append(Check("Time zone", bool(tz), tz or "not set: \"today\" and "
                        "\"tomorrow\" would be in UTC (set ASSISTANT_TIMEZONE, "
                        "e.g. America/New_York)", required=False))
    return checks


def check_server_tts(config: dict, which=shutil.which) -> list[Check]:
    checks = [check_tts(config, which)]
    found = which("espeak-ng") or which("espeak")
    checks.append(Check("TTS fallback (espeak-ng)", bool(found),
                        "found" if found else "missing (apt-get install espeak-ng)",
                        required=checks[0].ok is False))
    return checks


def run_checks(config: dict, keyword_path: str | None = None,
               environ=os.environ, sd=None, download_model=None,
               which=shutil.which, verify=None) -> list[Check]:
    """`verify`: google_auth.verify_sign_in to ask Google whether the
    saved sign-in still works (the CLI passes it); None: offline."""
    if config.get("profile") == "server":
        # No microphone, speakers or wake word on a server.
        return [*check_keys(environ), *check_server(config, environ),
                *check_google_env(config, environ, verify),
                check_whisper_model(config, download_model),
                *check_cloud_stt(config, environ),
                *check_server_tts(config, which), *check_web(environ)[1:]]
    return [*check_keys(environ), *check_google_files(config, verify, environ),
            check_wake_model(config, keyword_path),
            *check_audio_devices(config, sd),
            check_whisper_model(config, download_model),
            *check_cloud_stt(config, environ),
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
