"""python -m assistant --check: everything mocked, no secrets printed."""

import sys
from unittest.mock import MagicMock

from assistant import cli, doctor
from assistant.config import load_config

SECRET = "sk-ant-very-secret-value"


def fake_sd(devices, query_error=None):
    sd = MagicMock()

    def query_devices(device=None, kind=None):
        if device is None:
            return devices
        if query_error:
            raise query_error
        return devices[device]

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
    assert "base.en cached at /cache/base.en" in out


def test_missing_things_reported(tmp_path):
    code, out = run(config(tmp_path, keyword_path="keywords/nope.onnx"), {},
                    download_model=not_cached)
    assert code == 1  # Anthropic key is required
    assert "[FAIL] ANTHROPIC_API_KEY: not set" in out
    assert "[WARN] PINECONE_API_KEY: not set" in out
    assert "[WARN] " in out and "credentials.json: missing" in out
    assert "--headless" in out
    assert "nope.onnx missing; would fall back to 'hey_jarvis'" in out
    assert "[WARN] Whisper model: base.en not cached yet" in out


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
    assert not doctor.check_tts(cfg, which=lambda exe: None).ok


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
