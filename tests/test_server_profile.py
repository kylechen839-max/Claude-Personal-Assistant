"""Headless server profile and secrets from the environment (cloud mode).

Google OAuth comes from GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON (fake values
here), is refreshed in memory and never written to disk.
"""

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from assistant import doctor
from assistant.config import DEFAULTS, active_profile, load_config, setup_logging
from assistant.tools import calendar as cal
from assistant.tts import EspeakTTS, PrintTTS, make_tts

FAKE_CLIENT = {"installed": {"client_id": "fake-id.apps.googleusercontent.com",
                             "client_secret": "fake-client-secret-123",
                             "token_uri": "https://oauth2.googleapis.com/token"}}
REFRESH = "1//fake-refresh-token-abcdefghijklmnopqrstuvwxyz"


def fake_token(expired=True, with_client=True):
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        hours=-1 if expired else 1)
    info = {"token": "ya29.fake-old-access-token", "refresh_token": REFRESH,
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": cal.SCOPES, "expiry": expiry.isoformat() + "Z"}
    if with_client:
        info.update(client_id=FAKE_CLIENT["installed"]["client_id"],
                    client_secret=FAKE_CLIENT["installed"]["client_secret"])
    return info


@pytest.fixture
def fake_refresh(monkeypatch):
    """Credentials.refresh without the network: hands out a new token."""
    pytest.importorskip("google.oauth2.credentials")
    from google.oauth2.credentials import Credentials

    calls = []

    def refresh(self, request):
        calls.append(self.refresh_token)
        self.token = "ya29.fake-new-access-token"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    return calls


@pytest.fixture
def failing_refresh(monkeypatch):
    pytest.importorskip("google.oauth2.credentials")
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    def refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.",
                           {"error": "invalid_grant", "refresh_token": REFRESH})

    monkeypatch.setattr(Credentials, "refresh", refresh)


# --- profile ------------------------------------------------------------------

def test_server_profile_settings(monkeypatch):
    monkeypatch.setenv("ASSISTANT_PROFILE", "server")
    cfg = load_config(None, None)
    assert cfg["profile"] == "server"
    assert cfg["tts"]["engine"] == "piper" and cfg["tts"]["fallback"] == "espeak"
    assert cfg["tts"]["piper_voice"] == "voices/en_US-lessac-medium.onnx"
    assert cfg["web"]["browser"] is False  # no browser on a server
    assert cfg["calendar"]["save_token"] is False
    assert cfg["webapp"]["host"] == "0.0.0.0"
    assert cfg["logging"]["file"] == ""
    assert DEFAULTS["web"]["browser"] is True  # PC defaults untouched


def test_profile_beats_config_toml_and_env_beats_profile(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text('[web]\nbrowser = true\n[stt]\nmodel = "small.en"\n')
    monkeypatch.setenv("WHISPER_MODEL", "tiny.en")
    monkeypatch.setenv("ASSISTANT_TIMEZONE", "America/New_York")
    cfg = load_config(f, None, profile="server")
    assert cfg["web"]["browser"] is False
    assert cfg["stt"]["model"] == "tiny.en"
    assert cfg["assistant"]["timezone"] == "America/New_York"


def test_no_profile_on_a_pc_and_unknown_profile_errors():
    assert active_profile({}) == ""
    assert active_profile({"ASSISTANT_PROFILE": "Server"}) == "server"
    with pytest.raises(ValueError):
        active_profile({"ASSISTANT_PROFILE": "cloudy"})


def test_server_logging_goes_to_the_console_only(tmp_path, monkeypatch,
                                                 restore_root_logging):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None, None, profile="server")
    assert setup_logging(cfg) is None
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1 and handlers[0].level == logging.INFO
    assert not (tmp_path / "logs").exists()


# --- TTS fallback ---------------------------------------------------------------

def test_missing_piper_voice_falls_back_to_espeak(monkeypatch):
    monkeypatch.setattr(EspeakTTS, "__init__", lambda self, **kw: None)
    tts = make_tts("piper", piper_voice="nope.onnx", fallback="espeak")
    assert isinstance(tts, EspeakTTS)


def test_no_espeak_either_keeps_the_server_up_text_only(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    tts = make_tts("piper", piper_voice="nope.onnx", fallback="espeak")
    assert isinstance(tts, PrintTTS)


def test_default_fallback_is_still_pyttsx3():
    from assistant.tts import Pyttsx3TTS

    assert isinstance(make_tts("piper", piper_voice="nope.onnx"), Pyttsx3TTS)


@pytest.mark.skipif(not (shutil.which("espeak-ng") or shutil.which("espeak")),
                    reason="espeak-ng not installed")
def test_espeak_renders_a_wav(tmp_path):
    from assistant import audio

    path = tmp_path / "reply.wav"
    EspeakTTS().save_to_file("-- You have two events tomorrow.", path)
    samples, sr = audio.read_wav(path)
    assert len(samples) > sr * 0.5 and abs(samples).max() > 0.01


# --- Google credentials from the environment ----------------------------------

def test_token_from_env_is_refreshed_in_memory_only(tmp_path, monkeypatch,
                                                    fake_refresh):
    monkeypatch.chdir(tmp_path)
    env = {"GOOGLE_TOKEN_JSON": json.dumps(fake_token())}
    creds = cal.get_credentials(environ=env)
    assert creds.token == "ya29.fake-new-access-token" and fake_refresh == [REFRESH]
    assert list(tmp_path.iterdir()) == []  # no token.json written


def test_env_token_wins_over_the_files(tmp_path, monkeypatch, fake_refresh):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "token.json").write_text(json.dumps({**fake_token(),
                                                     "refresh_token": "1//file"}))
    cal.get_credentials(environ={"GOOGLE_TOKEN_JSON": json.dumps(fake_token())})
    assert fake_refresh == [REFRESH]
    assert json.loads((tmp_path / "token.json").read_text())["refresh_token"] == "1//file"


def test_client_secret_comes_from_google_client_json(tmp_path, monkeypatch,
                                                     fake_refresh):
    monkeypatch.chdir(tmp_path)
    env = {"GOOGLE_TOKEN_JSON": json.dumps(fake_token(with_client=False)),
           "GOOGLE_CLIENT_JSON": json.dumps(FAKE_CLIENT)}
    creds = cal.get_credentials(environ=env)
    assert creds.client_id == FAKE_CLIENT["installed"]["client_id"]
    assert creds.client_secret == FAKE_CLIENT["installed"]["client_secret"]


def test_file_token_still_saved_on_a_pc_but_not_on_a_server(tmp_path, monkeypatch,
                                                           fake_refresh):
    monkeypatch.chdir(tmp_path)
    token = tmp_path / "token.json"
    token.write_text(json.dumps(fake_token()))
    cal.get_credentials(environ={})
    assert json.loads(token.read_text())["token"] == "ya29.fake-new-access-token"
    token.write_text(json.dumps(fake_token()))
    cal.get_credentials(environ={}, save_token=False)
    assert json.loads(token.read_text())["token"] == "ya29.fake-old-access-token"


def test_valid_token_needs_no_refresh(fake_refresh):
    env = {"GOOGLE_TOKEN_JSON": json.dumps(fake_token(expired=False))}
    assert cal.get_credentials(environ=env).token == "ya29.fake-old-access-token"
    assert fake_refresh == []


def test_refresh_failure_logs_a_clear_warning_without_secrets(
        tmp_path, monkeypatch, failing_refresh, caplog):
    monkeypatch.chdir(tmp_path)
    env = {"GOOGLE_TOKEN_JSON": json.dumps(fake_token()),
           "GOOGLE_CLIENT_JSON": json.dumps(FAKE_CLIENT)}
    with caplog.at_level(logging.WARNING), pytest.raises(cal.GoogleSignInError) as e:
        cal.get_credentials(environ=env)
    assert "invalid_grant" in caplog.text and "GOOGLE_TOKEN_JSON" in caplog.text
    for secret in (REFRESH, "fake-client-secret-123", "ya29.fake-old-access-token"):
        assert secret not in caplog.text and secret not in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_refresh_failure_inside_an_api_call_is_also_logged(
        monkeypatch, fake_refresh, caplog):
    from google.auth.exceptions import RefreshError

    env = {"GOOGLE_TOKEN_JSON": json.dumps(fake_token(expired=False))}
    creds = cal.get_credentials(environ=env)

    # later, google-auth refreshes inside a request and Google says no
    from google.oauth2.credentials import Credentials

    def refuse(self, request):
        raise RefreshError("invalid_grant: Bad Request", {"refresh_token": REFRESH})

    monkeypatch.setattr(Credentials, "refresh", refuse)
    creds2 = cal._warn_on_refresh_failure(Credentials.from_authorized_user_info(
        fake_token(), cal.SCOPES))
    with caplog.at_level(logging.WARNING), pytest.raises(cal.GoogleSignInError):
        creds2.refresh(None)
    assert "invalid_grant" in caplog.text and REFRESH not in caplog.text
    assert creds.token  # the first one is untouched


def test_bad_json_errors_name_the_variable_not_the_value():
    with pytest.raises(ValueError) as e:
        cal.get_credentials(environ={"GOOGLE_TOKEN_JSON": "{not json " + REFRESH})
    assert "GOOGLE_TOKEN_JSON" in str(e.value) and REFRESH not in str(e.value)


def test_no_interactive_sign_in_on_a_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    info = fake_token()
    del info["refresh_token"]
    with pytest.raises(cal.GoogleSignInError):
        cal.get_credentials(environ={"GOOGLE_TOKEN_JSON": json.dumps(info)})
    with pytest.raises(cal.GoogleSignInError):
        cal.get_credentials(environ={}, save_token=False)


def test_build_tools_registers_calendar_from_env_alone(tmp_path, monkeypatch):
    from assistant.cli import build_tools

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(fake_token()))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None, profile="server")
    registry = build_tools(cfg, output_fn=lambda m: None)
    assert registry.has("list_calendar_events")
    assert not registry.has("open_in_browser")
    monkeypatch.delenv("GOOGLE_TOKEN_JSON")
    assert not build_tools(cfg, output_fn=lambda m: None).has("list_calendar_events")


def test_google_secrets_are_redacted_from_logs(monkeypatch):
    from assistant.config import redact

    value = json.dumps(fake_token())
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", value)
    assert value not in redact(f"oops {value}")
    assert REFRESH not in redact(json.dumps(fake_token()))


# --- --check on a server ------------------------------------------------------

class NoAudio:
    def query_devices(self, *a):
        raise AssertionError("a server has no audio devices to check")


def server_checks(env, which=lambda name: "/usr/bin/" + name):
    cfg = load_config(None, None, profile="server")
    cfg["assistant"]["timezone"] = env.pop("_tz", "")
    return {c.name: c for c in doctor.run_checks(
        cfg, environ=env, sd=NoAudio(), which=which,
        download_model=lambda name, local_files_only: f"/models/{name}")}


def test_server_check_skips_mic_speaker_and_wake_word():
    checks = server_checks({"ANTHROPIC_API_KEY": "k", "WEB_TOKEN": "w" * 40,
                            "GOOGLE_TOKEN_JSON": json.dumps(fake_token()),
                            "_tz": "America/New_York"})
    for name in ("Input devices", "Output devices", "Wake word model",
                 "credentials.json", "token.json"):
        assert name not in checks
    assert checks["WEB_TOKEN"].ok and checks["GOOGLE_TOKEN_JSON"].ok
    assert checks["Time zone"].ok and checks["Whisper model"].ok
    assert checks["TTS fallback (espeak-ng)"].ok


def test_server_check_requires_web_token_and_reports_no_values(capsys):
    env = {"ANTHROPIC_API_KEY": "sk-ant-secret-value",
           "GOOGLE_TOKEN_JSON": json.dumps(fake_token(with_client=False))}
    checks = server_checks(env)
    assert not checks["WEB_TOKEN"].ok and checks["WEB_TOKEN"].required
    assert not checks["Time zone"].ok and not checks["Time zone"].required
    assert "client_id" in checks["GOOGLE_TOKEN_JSON"].detail
    assert doctor.report(list(checks.values())) == 1
    out = capsys.readouterr().out
    assert "sk-ant-secret-value" not in out and REFRESH not in out


def test_server_check_without_piper_or_espeak_fails():
    checks = server_checks({"ANTHROPIC_API_KEY": "k", "WEB_TOKEN": "w" * 40},
                           which=lambda name: None)
    assert not checks["TTS fallback (espeak-ng)"].ok
