"""python -m kwil --check: everything mocked, no secrets printed."""

import sys
from unittest.mock import MagicMock

import pytest

from kwil import cli, doctor
from kwil.config import load_config

SECRET = "sk-ant-very-secret-value"


def fake_sd(devices, query_error=None, default_input=None):
    """Like sounddevice: a list with no arguments; one device's info (with
    its 'index') for an index or name fragment; kind="input" alone is the
    default input device."""
    sd = MagicMock()
    inputs = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0]

    def info(i):
        return {**devices[i], "index": i}

    def query_devices(device=None, kind=None):
        if device is None and kind is None:
            return devices
        if query_error:
            raise query_error
        if device is None:
            if not inputs:
                raise ValueError("no default input device")
            return info(inputs[0] if default_input is None else default_input)
        if isinstance(device, int):
            return info(device)
        found = [i for i, d in enumerate(devices) if device.lower() in d["name"].lower()]
        if not found:
            raise ValueError(f"No input device matching {device!r}")
        return info(found[0])

    sd.query_devices.side_effect = query_devices
    return sd


DEVICES = [
    {"name": "USB PnP Sound Device", "max_input_channels": 1, "max_output_channels": 0},
    {"name": "bcm2835 Headphones", "max_input_channels": 0, "max_output_channels": 2},
]


def config(tmp_path, **wake):
    cfg = load_config(None, None)
    cfg["calendar"]["credentials"] = str(tmp_path / "credentials.json")
    cfg["calendar"]["token"] = str(tmp_path / "token.json")
    cfg["wake"].update(wake)
    return cfg


def cached(name, local_files_only):
    assert local_files_only  # the check must never download
    return f"/cache/{name}"


def not_cached(name, local_files_only):
    raise FileNotFoundError(name)


def run(cfg, environ, sd=None, download_model=cached, keyword_path=None):
    lines = []
    checks = doctor.run_checks(cfg, keyword_path, environ=environ,
                               sd=sd or fake_sd(DEVICES),
                               download_model=download_model,
                               which=lambda exe: f"/usr/bin/{exe}")
    code = doctor.report(checks, lines.append)
    return code, "\n".join(lines)


def test_all_good_and_no_secret_printed(tmp_path):
    (tmp_path / "credentials.json").write_text("{}")
    (tmp_path / "token.json").write_text("{}")
    model = tmp_path / "hey_claude.onnx"
    model.write_bytes(b"x")
    env = {"ANTHROPIC_API_KEY": SECRET, "PINECONE_API_KEY": SECRET + "2",
           "NOTION_TOKEN": SECRET + "3"}
    code, out = run(config(tmp_path, keyword_path=str(model)), env)
    assert code == 0
    assert SECRET not in out
    assert "[OK  ] ANTHROPIC_API_KEY: set" in out
    assert "[OK  ] NOTION_TOKEN: set" in out
    assert "token.json: found" in out
    assert f"{model} found" in out
    assert "[0] USB PnP Sound Device" in out
    assert "[1] bcm2835 Headphones" in out
    assert "small.en cached at /cache/small.en, beam 5" in out


def test_missing_things_reported(tmp_path):
    code, out = run(config(tmp_path, keyword_path="keywords/nope.onnx"), {},
                    download_model=not_cached)
    assert code == 1  # Anthropic key is required
    assert "[FAIL] ANTHROPIC_API_KEY: not set" in out
    assert "[WARN] PINECONE_API_KEY: not set" in out
    assert "[WARN] " in out and "credentials.json: missing" in out
    assert "--headless" in out
    assert "nope.onnx missing; would fall back to 'hey_jarvis'" in out
    assert "[WARN] Whisper model: small.en not cached yet" in out


def test_builtin_wake_word_when_no_model_set(tmp_path):
    check = doctor.check_wake_model(config(tmp_path))
    assert check.ok and "built-in 'hey_jarvis'" in check.detail


def test_no_audio_devices_fails(tmp_path):
    code, out = run(config(tmp_path), {"ANTHROPIC_API_KEY": "x"}, sd=fake_sd([]))
    assert code == 1
    assert "[FAIL] Input devices: none found" in out
    assert "[FAIL] Output devices: none found" in out


def test_portaudio_missing(tmp_path):
    sd = MagicMock()
    sd.query_devices.side_effect = OSError("PortAudio library not found")
    checks = doctor.check_audio_devices(config(tmp_path), sd)
    assert not checks[0].ok and "PortAudio" in checks[0].detail


def test_configured_mic_index(tmp_path):
    checks = doctor.check_audio_devices(config(tmp_path, mic_device="0"),
                                        fake_sd(DEVICES))
    assert checks[-1].ok and "USB PnP" in checks[-1].detail
    bad = doctor.check_audio_devices(
        config(tmp_path, mic_device="7"),
        fake_sd(DEVICES, query_error=ValueError("No input device matching 7")))
    assert not bad[-1].ok and "not usable" in bad[-1].detail


def test_calendar_labels_scope_check(tmp_path, monkeypatch):
    """An older token (events scope only) warns, with the sign-in command;
    one with the read-only list/labels scopes passes. Never required."""
    import json

    from kwil.tools import google_auth

    monkeypatch.delenv(google_auth.TOKEN_ENV, raising=False)
    cfg = config(tmp_path)
    token = tmp_path / "token.json"
    cfg["calendar"]["token"] = str(token)
    token.write_text(json.dumps({"token": "a", "scopes": [google_auth.CALENDAR_SCOPE]}))
    (check,) = doctor.check_calendar_scopes(cfg)
    assert not check.ok and not check.required
    assert google_auth.SIGN_IN_COMMAND in check.detail
    assert check in doctor.check_google_files(cfg)
    token.write_text(json.dumps({"token": "a", "scopes": google_auth.SCOPES}))
    (check,) = doctor.check_calendar_scopes(cfg)
    assert check.ok and "read-only" in check.detail


def test_calendar_disabled(tmp_path):
    cfg = config(tmp_path)
    cfg["calendar"]["enabled"] = False
    assert doctor.check_google_files(cfg)[0].detail == "disabled in config.toml"


def test_tts_checks(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg["tts"]["engine"] = "piper"
    voice = tmp_path / "voice.onnx"
    cfg["tts"]["piper_voice"] = str(voice)
    missing = doctor.check_tts(cfg)
    assert not missing.ok and not missing.required
    assert "voice file not found" in missing.detail
    assert "--download-voice en_US-lessac-medium" in missing.detail
    voice.write_bytes(b"x")
    (tmp_path / "voice.onnx.json").write_text("{}")
    monkeypatch.setitem(sys.modules, "piper", MagicMock())
    assert doctor.check_tts(cfg).ok
    cfg["tts"]["engine"] = "pyttsx3"
    # pyttsx3 needs espeak only on Linux; Windows uses the built-in SAPI voices
    monkeypatch.setattr(sys, "platform", "linux")
    assert not doctor.check_tts(cfg, which=lambda exe: None).ok
    monkeypatch.setattr(sys, "platform", "win32")
    assert doctor.check_tts(cfg, which=lambda exe: None).ok


def test_bundled_wake_model_is_found(monkeypatch):
    monkeypatch.delenv("WAKEWORD_MODEL_PATH", raising=False)
    check = doctor.check_wake_model(load_config("config.toml", None))
    assert check.ok and "keywords/hey_claude.onnx found" in check.detail


def test_cli_check_runs_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env / config.toml here
    seen = {}

    def fake_run_checks(cfg, keyword_path):
        seen["keyword_path"] = keyword_path
        return [doctor.Check("thing", True, "fine")]

    monkeypatch.setattr(doctor, "run_checks", fake_run_checks)
    assert cli.main(["--check", "--keyword-path", "k.onnx"]) == 0
    assert seen["keyword_path"] == "k.onnx"


# ---------- mic_device = 0 is device 0, not "the default" ----------

@pytest.mark.parametrize("value,want", [
    ("", None), (None, None), ("   ", None), (0, 0), ("0", 0), (" 3 ", 3), (2, 2),
    ("USB PnP", "USB PnP"), (" USB ", "USB")])
def test_resolve_mic_device(value, want):
    from kwil.audio import resolve_mic_device

    assert resolve_mic_device(value) == want


# Device 0 is an input that is NOT the default (the default is index 2).
MICS = [{"name": "Front Mic", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Headset Mic", "max_input_channels": 1, "max_output_channels": 0}]


@pytest.mark.parametrize("mic", [0, "0"])
def test_check_mic_index_zero(tmp_path, mic):
    checks = doctor.check_audio_devices(config(tmp_path, mic_device=mic),
                                        fake_sd(MICS, default_input=2))
    assert checks[-1].ok and checks[-1].detail == f"{mic!r} -> [0] Front Mic"


def test_check_names_the_default_mic(tmp_path):
    checks = doctor.check_audio_devices(config(tmp_path),
                                        fake_sd(MICS, default_input=2))
    assert checks[-1].name == "Mic that will open"
    assert checks[-1].detail.startswith("[2] Headset Mic (the default")


def test_check_fails_when_another_device_would_open(tmp_path):
    sd = fake_sd(MICS)
    sd.query_devices.side_effect = lambda device=None, kind=None: (
        MICS if device is None and kind is None else {**MICS[2], "index": 2})
    checks = doctor.check_audio_devices(config(tmp_path, mic_device=0), sd)
    assert not checks[-1].ok and "asked for index 0" in checks[-1].detail


def test_wake_mode_opens_device_zero(monkeypatch):
    """The bug: `device or None` turned index 0 into the default mic."""
    from types import SimpleNamespace

    from kwil import cli, wakeword

    opened = {}
    monkeypatch.setattr(wakeword, "make_detector", lambda **_: SimpleNamespace(
        frame_length=1280, phrase="hey claude", process=lambda f: False))
    monkeypatch.setattr(wakeword, "MicSource",
                        lambda frame_length, device=None: opened.setdefault("device", device))
    cfg = load_config(None, None)
    for value, want in ((0, 0), ("0", 0), ("", None), ("USB", "USB")):
        opened.clear()
        cfg["wake"]["mic_device"] = value
        cli.build_wake_components(cli.parse_args([]), cfg)
        assert opened["device"] == want, value


def test_push_to_talk_uses_the_configured_mic(monkeypatch):
    import sys
    from types import SimpleNamespace

    from kwil import stt

    seen = {}

    class Stream:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))
    assert stt.record_push_to_talk(input_fn=lambda prompt: "", device=0) is None
    assert seen["device"] == 0
