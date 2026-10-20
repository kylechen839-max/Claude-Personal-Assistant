import logging
import os
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
    from assistant.config import ENV_OVERRIDES

    for var in ENV_OVERRIDES:  # whatever this machine has set
        monkeypatch.delenv(var, raising=False)
    assert load_config(None, None) == DEFAULTS
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[assistant]\nmax_tokens = 300\n[wake]\nsensitivity = 0.8\n')
    cfg = load_config(cfg_file, None)
    assert cfg["assistant"]["max_tokens"] == 300
    assert cfg["assistant"]["model"] == "claude-sonnet-5-5"  # default kept
    assert cfg["wake"]["sensitivity"] == 0.8 and cfg["wake"]["chime"] is True
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-1")
    assert load_config(cfg_file, None)["assistant"]["model"] == "claude-opus-4-1"
    assert DEFAULTS["assistant"]["max_tokens"] == 1500  # defaults not mutated


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
    # load_dotenv set it outside monkeypatch; a second delenv would record
    # "abc123" and restore it at teardown, leaking into later tests.
    os.environ.pop("NOTION_ASSIGNMENTS_DB", None)


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
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    (tmp_path / "config.toml").write_text('[wake]\nbuiltin_keyword = "computer"\n')
    assert cli.main(["--mode", "wake", "--tts", "print"]) == 1
    assert "Unknown wake word 'computer'" in capsys.readouterr().out


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
    cfg["web"]["enabled"] = False
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


def test_config_local_toml_overrides_config_toml(tmp_path):
    from assistant.config import load_config

    (tmp_path / "config.toml").write_text('[update]\nenabled = false\n[wake]\nsensitivity = 0.4\n')
    assert load_config(tmp_path / "config.toml", None)["update"]["enabled"] is False
    (tmp_path / "config.local.toml").write_text('[update]\nenabled = true\n')
    cfg = load_config(tmp_path / "config.toml", None)
    assert cfg["update"]["enabled"] is True and cfg["wake"]["sensitivity"] == 0.4
    assert cfg["update"]["channel"] == "stable"  # the rest stays default


def test_config_local_toml_is_gitignored():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert "config.local.toml" in (root / ".gitignore").read_text().splitlines()


def test_mom_config_turns_every_connection_off(monkeypatch, tmp_path):
    """config.mom.toml: a separate Chinese instance with none of Kyle's
    connections, even when his keys are in the environment."""
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    cfg = load_config("config.mom.toml", None)
    assert cfg["assistant"]["language"] == "zh" and cfg["stt"]["model"] == "small"
    for section in ("notion", "memory", "notes", "lecture", "tutor", "devteam", "update"):
        assert cfg[section]["enabled"] is False, section
    assert cfg["failover"]["role"] == "" and cfg["web"]["browser"] is False
    assert cfg["webapp"]["google_connect"] is True and cfg["webapp"]["port"] == 8766
    for path in (cfg["calendar"]["credentials"], cfg["calendar"]["token"],
                 cfg["history"]["path"], cfg["webapp"]["sessions_file"]):
        assert path.startswith("data/mom/"), path
    assert cfg["logging"]["file"] == "logs/mom.log"
    # On a PC where she already connected Google, data/mom/google_token.json
    # exists; point at a token that doesn't, for the "not signed in yet" case.
    cfg["calendar"]["token"] = str(tmp_path / "google_token.json")
    assert cli.build_memory(cfg, lambda *_: None) is None
    names = {s["name"] for s in cli.build_tools(cfg, lambda *_: None).schemas()}
    assert not names & {"list_assignments", "update_assignment_status",
                        "message_dev_team", "open_in_browser"}
    assert "list_calendar_events" not in names  # no Google sign-in yet


def test_google_connect_without_a_token_never_signs_in_here(tmp_path, monkeypatch):
    """With google_connect, a client file but no token means no Google tools
    (Calendar would otherwise open a browser sign-in on this computer)."""
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None, None)
    (tmp_path / "credentials.json").write_text("{}")
    cfg["webapp"]["google_connect"] = True
    out = []
    names = {s["name"] for s in cli.build_tools(cfg, out.append).schemas()}
    assert "list_calendar_events" not in names and "email_summary" not in names
    assert any("connect Google" in m for m in out)
    (tmp_path / "token.json").write_text('{"token": "x", "scopes": ['
                                         '"https://www.googleapis.com/auth/calendar.events"]}')
    assert "list_calendar_events" in {s["name"] for s in cli.build_tools(cfg, out.append).schemas()}
    cfg["webapp"]["google_connect"] = False  # Kyle's setup: unchanged
    (tmp_path / "token.json").unlink()
    assert "list_calendar_events" in {s["name"] for s in cli.build_tools(cfg, out.append).schemas()}


def test_env_file_flag(tmp_path, monkeypatch, capsys):
    import assistant.config

    assert cli.parse_args([]).env_file == ".env"
    _no_inherited(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    real, seen = assistant.config.load_config, []

    def load(path, env_file=".env"):
        seen.append((path, env_file))
        return real(None, None)
    monkeypatch.setattr(assistant.config, "load_config", load)
    assert cli.main(["--mode", "text", "--config", "config.mom.toml",
                     "--env-file", ".env.mom"]) == 1  # no key in the test
    assert seen == [("config.mom.toml", ".env.mom")]


def test_main_passes_env_file_and_language_to_the_web_app(tmp_path, monkeypatch):
    from assistant import i18n, webapp

    _no_inherited(monkeypatch)  # a separate instance refuses the caller's keys
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    (tmp_path / "zh.toml").write_text('[assistant]\nlanguage = "zh"\n')
    seen = {}
    monkeypatch.setattr(webapp, "serve", lambda config, **kw: seen.update(kw) or 0)
    assert cli.main(["--mode", "web", "--config", "zh.toml", "--env-file", ".env.mom"]) == 0
    assert seen["env_file"] == ".env.mom" and i18n.LANG == "zh"


ISOLATED = ("GOOGLE_TOKEN_JSON", "GOOGLE_CLIENT_JSON", "GOOGLE_TOKEN",
            "GOOGLE_CREDENTIALS", "HISTORY_PATH", "WEB_TOKEN", "NOTION_TOKEN",
            "NOTION_ASSIGNMENTS_DB", "PINECONE_API_KEY", "PINECONE_INDEX")


def _no_inherited(monkeypatch):
    for var in ISOLATED:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("var", ISOLATED)
def test_separate_instance_refuses_the_owners_inherited_env(tmp_path, monkeypatch,
                                                            capsys, var):
    """--env-file .env.mom started from Kyle's shell: his token, history,
    Notion or Pinecone settings must not reach her instance (load_dotenv never
    overrides them). It refuses to start and names the variable, not its value."""
    from assistant import webapp

    _no_inherited(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv(var, "kyles-value-123456789012345678901234567890")
    (tmp_path / ".env.mom").write_text("ANTHROPIC_API_KEY=test\n")
    served = []
    monkeypatch.setattr(webapp, "serve", lambda config, **kw: served.append(kw) or 0)
    assert cli.main(["--mode", "web", "--config", "none.toml",
                     "--env-file", ".env.mom"]) == 1
    out = capsys.readouterr().out
    assert var in out and "clean shell" in out and "kyles-value" not in out
    assert served == []


def test_google_connect_refuses_inherited_google_token_with_default_env(
        tmp_path, monkeypatch, capsys):
    from assistant import webapp

    _no_inherited(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", '{"token": "x"}')
    (tmp_path / "c.toml").write_text("[webapp]\ngoogle_connect = true\n")
    monkeypatch.setattr(webapp, "serve", lambda config, **kw: 0)
    assert cli.main(["--mode", "web", "--config", "c.toml"]) == 1
    assert "GOOGLE_TOKEN_JSON" in capsys.readouterr().out


def test_separate_instance_starts_with_its_own_env_file(tmp_path, monkeypatch):
    """Values from .env.mom itself, or a clean shell, start normally; Kyle's
    own instance (default .env, no google_connect) is unchanged."""
    from assistant import webapp

    _no_inherited(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    token = "m" * 43
    (tmp_path / ".env.mom").write_text(f"WEB_TOKEN={token}\nHISTORY_PATH=data/mom/h.db\n")
    seen = []
    monkeypatch.setattr(webapp, "serve", lambda config, **kw: seen.append(config) or 0)
    assert cli.main(["--mode", "web", "--config", "none.toml", "--env-file", ".env.mom"]) == 0
    assert seen[-1]["history"]["path"] == "data/mom/h.db"
    # a wrapper that already loaded the same .env.mom: same values, not Kyle's
    assert cli.main(["--mode", "web", "--config", "none.toml", "--env-file", ".env.mom"]) == 0
    monkeypatch.setenv("NOTION_TOKEN", "kyles")  # Kyle's own instance: unchanged
    assert cli.main(["--mode", "web", "--config", "none.toml"]) == 0
