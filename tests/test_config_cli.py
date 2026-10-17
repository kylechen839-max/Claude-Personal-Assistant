import logging
from types import SimpleNamespace

import numpy as np
import pytest

from assistant import cli
from assistant.config import DEFAULTS, load_config, setup_logging
from tests.conftest import make_response, text_block


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)


def test_load_config_defaults_file_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    assert load_config(None, None) == DEFAULTS
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[assistant]\nmax_tokens = 300\n[wake]\nsensitivity = 0.8\n')
    cfg = load_config(cfg_file, None)
    assert cfg["assistant"]["max_tokens"] == 300
    assert cfg["assistant"]["model"] == "claude-sonnet-4-5"  # default kept
    assert cfg["wake"]["sensitivity"] == 0.8 and cfg["wake"]["chime"] is True
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-1")
    assert load_config(cfg_file, None)["assistant"]["model"] == "claude-opus-4-1"
    assert DEFAULTS["assistant"]["max_tokens"] == 500  # defaults not mutated


def test_repo_config_toml_is_valid(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    cfg = load_config("config.toml", None)
    assert set(cfg) == set(DEFAULTS)
    for section, values in cfg.items():
        assert set(values) <= set(DEFAULTS[section]), section


def test_env_file_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_ASSIGNMENTS_DB", raising=False)
    env = tmp_path / ".env"
    env.write_text("NOTION_ASSIGNMENTS_DB=abc123\n")
    assert load_config(None, env)["notion"]["database_id"] == "abc123"
    monkeypatch.delenv("NOTION_ASSIGNMENTS_DB", raising=False)


def test_setup_logging_writes_file(tmp_path):
    cfg = load_config(None, None)
    cfg["logging"]["file"] = str(tmp_path / "logs" / "a.log")
    path = setup_logging(cfg)
    logging.getLogger("assistant.test").info("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "hello log" in path.read_text()


def test_main_without_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["--mode", "text"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out
    assert (tmp_path / "logs" / "assistant.log").exists()


def test_main_text_mode_end_to_end(tmp_path, monkeypatch, fake_client, capsys):
    monkeypatch.chdir(tmp_path)
    for var in ("NOTION_TOKEN", "PINECONE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: fake_client)
    fake_client.queue = [make_response(text_block("Hello Kyle."))]
    inputs = iter(["hi", "quit"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    assert cli.main(["--mode", "text"]) == 0
    out = capsys.readouterr().out
    assert "Claude: Hello Kyle." in out
    assert "Calendar tools off" in out and "Memory off" in out
    assert "Starting in text mode" in (tmp_path / "logs" / "assistant.log").read_text()


def test_main_reports_startup_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("PICOVOICE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    assert cli.main(["--mode", "wake", "--tts", "print"]) == 1
    assert "PICOVOICE_ACCESS_KEY" in capsys.readouterr().out


def test_build_tools_and_memory_respect_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    cfg = load_config(None, None)
    (tmp_path / "credentials.json").write_text("{}")
    names = {s["name"] for s in cli.build_tools(cfg, lambda *_: None).schemas()}
    assert {"list_calendar_events", "delete_calendar_event", "list_assignments",
            "update_assignment_status"} <= names
    cfg["calendar"]["enabled"] = cfg["notion"]["enabled"] = False
    assert not cli.build_tools(cfg, lambda *_: None)
    assert cli.build_memory(cfg).index_name == "project-context"
    cfg["memory"]["enabled"] = False
    assert cli.build_memory(cfg) is None


def test_wake_loop_survives_errors(fake_client):
    from assistant.chat import ClaudeChat
    from assistant.tts import PrintTTS

    events = iter([OSError("mic gone"), (True, np.ones(5)), (True, np.ones(5)),
                   (False, None)])

    def listen(source):
        e = next(events)
        if isinstance(e, Exception):
            raise e
        return e

    texts = iter([RuntimeError("whisper crashed"), "hello"])

    def transcribe(a):
        t = next(texts)
        if isinstance(t, Exception):
            raise t
        return t

    fake_client.queue = [make_response(text_block("Hi there."))]
    said, out = [], []
    cli.run_wake(ClaudeChat(client=fake_client), PrintTTS(said.append),
                 SimpleNamespace(transcribe=transcribe),
                 SimpleNamespace(listen=listen), None, output_fn=out.append,
                 retry_delay=0)
    assert said == ["[speaking] Sorry, something went wrong.", "[speaking] Hi there."]
    assert any("Microphone problem" in o for o in out)


def test_wake_loop_gives_up_after_repeated_mic_failures():
    from assistant.tts import PrintTTS

    def listen(source):
        raise OSError("no mic")

    with pytest.raises(OSError):
        cli.run_wake(SimpleNamespace(history=[]), PrintTTS(lambda *_: None), None,
                     SimpleNamespace(listen=listen), None,
                     output_fn=lambda *_: None, max_failures=3, retry_delay=0)


@pytest.mark.parametrize("exc,expected", [
    ("auth", "API key"), ("rate", "too many requests"), ("other", "went wrong")])
def test_get_reply_error_messages(fake_client, exc, expected):
    import anthropic
    import httpx

    from assistant.chat import ClaudeChat

    req = httpx.Request("POST", "https://api.anthropic.com")
    status = {"auth": 401, "rate": 429, "other": 500}[exc]
    cls = {"auth": anthropic.AuthenticationError, "rate": anthropic.RateLimitError,
           "other": anthropic.InternalServerError}[exc]
    fake_client.messages.create.side_effect = cls(
        "err", response=httpx.Response(status, request=req), body=None)
    reply = cli.get_reply(ClaudeChat(client=fake_client), "hi", lambda *_: None)
    assert expected in reply


def test_text_mode_survives_unexpected_errors(fake_client):
    from assistant.chat import ClaudeChat

    fake_client.messages.create.side_effect = [ValueError("weird"),
                                               make_response(text_block("Hi."))]
    inputs, out = iter(["hello", "again", "quit"]), []
    cli.run_text(ClaudeChat(client=fake_client), input_fn=lambda _: next(inputs),
                 output_fn=out.append)
    assert "Claude: Sorry, something went wrong." in out
    assert "Claude: Hi." in out
