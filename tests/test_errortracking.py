"""Optional error tracking (assistant/errortracking.py): off without
SENTRY_DSN, the privacy options when on, before_send keeping nothing
personal, and a missing sentry-sdk never stopping the app. A fake
sentry_sdk module stands in for the real one: nothing is ever sent."""

import json
import logging
import sys
import types

import pytest

from assistant import errortracking
from assistant.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS, load_config

DSN = "https://0123456789abcdef0123456789abcdef@o0.ingest.example.invalid/1"
KEY = "sk-ant-api03-errortracking-test-key-0123456789"
EMAIL = "mom.chen+family@example.com"
SAID = "remind me about my appointment with Dr. Lee"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test starts with error tracking off and no DSN from the
    caller's environment."""
    monkeypatch.setattr(errortracking, "_started", False)
    monkeypatch.setattr(errortracking, "_warned", False)
    monkeypatch.delenv(errortracking.DSN_ENV, raising=False)


@pytest.fixture
def sentry(monkeypatch):
    """A fake sentry_sdk recording its init() call."""
    fake = types.ModuleType("sentry_sdk")
    fake.calls = []
    fake.init = lambda **kw: fake.calls.append(kw)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    return fake


def test_off_without_a_dsn(sentry, caplog):
    with caplog.at_level(logging.DEBUG, logger="assistant.errortracking"):
        assert errortracking.init(load_config(None, None)) is False
        assert errortracking.init(None, environ={errortracking.DSN_ENV: "  "}) is False
    assert sentry.calls == [] and caplog.records == []


def test_with_a_dsn_init_gets_the_privacy_options(sentry, monkeypatch, caplog):
    monkeypatch.setenv(errortracking.DSN_ENV, DSN)
    with caplog.at_level(logging.DEBUG, logger="assistant.errortracking"):
        assert errortracking.init({"profile": "server"}) is True
        assert errortracking.init({"profile": "server"}) is True  # cli + serve: once
    assert len(sentry.calls) == 1
    kw = sentry.calls[0]
    assert kw["dsn"] == DSN
    assert kw["send_default_pii"] is False
    assert kw["include_local_variables"] is False
    assert kw["max_breadcrumbs"] == 0
    assert kw["traces_sample_rate"] == 0
    assert kw["include_source_context"] is False
    assert kw["max_request_body_size"] == "never"
    assert kw["before_send"] is errortracking.before_send
    assert kw["before_breadcrumb"]({"message": SAID}, {}) is None
    assert kw["environment"] == "server"
    assert DSN not in caplog.text  # the DSN is never logged


def test_a_missing_sentry_sdk_leaves_the_app_working(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import raises ImportError
    monkeypatch.setenv(errortracking.DSN_ENV, DSN)
    with caplog.at_level(logging.WARNING, logger="assistant.errortracking"):
        assert errortracking.init({}) is False
        assert errortracking.init({}) is False
    warnings = [r for r in caplog.records if r.name == "assistant.errortracking"]
    assert len(warnings) == 1 and "sentry-sdk" in warnings[0].getMessage()
    assert DSN not in caplog.text


def sentry_event() -> dict:
    """An event as sentry-sdk 2 builds it for an exception in a FastAPI
    route (with everything the integrations can add)."""
    return {
        "event_id": "a" * 32, "level": "error", "platform": "python",
        "timestamp": "2026-10-21T12:00:00Z", "logger": "assistant.webapp",
        "server_name": "KYLE-PC", "release": "hey-claude-1.0.2",
        "environment": "server", "transaction": "/api/turn",
        "exception": {"values": [{
            "type": "ValueError", "module": None,
            "value": f"couldn't parse {SAID!r} for {EMAIL} with key {KEY}",
            "mechanism": {"type": "starlette", "handled": False,
                          "data": {"note": SAID}},
            "stacktrace": {"frames": [{
                "filename": "assistant/chat.py", "function": "ask", "lineno": 120,
                "module": "assistant.chat", "in_app": True,
                "abs_path": "/home/kyle/Claude-Personal-Assistant/assistant/chat.py",
                "pre_context": ["    text = user_text"],
                "context_line": "    raise ValueError(text)",
                "post_context": [""], "vars": {"text": SAID, "key": KEY}}]},
        }]},
        "logentry": {"message": f"Turn failed (key {KEY}, mail {EMAIL})",
                     "params": [SAID], "formatted": f"Turn failed: {SAID}"},
        "message": SAID,
        "request": {"method": "POST", "url": "https://hey-claude.onrender.com/api/turn",
                    "query_string": f"code=abc&email={EMAIL}",
                    "data": {"text": SAID}, "cookies": {"hc_session": "s" * 40},
                    "headers": {"Authorization": f"Bearer {KEY}", "Cookie": "x"},
                    "env": {"REMOTE_ADDR": "203.0.113.9"}},
        "user": {"ip_address": "203.0.113.9", "email": EMAIL},
        "tags": {"user": EMAIL}, "extra": {"sys.argv": ["--env-file", ".env.mom"]},
        "breadcrumbs": {"values": [{"message": SAID}]},
        "modules": {"anthropic": "0.70.0"},
        "threads": {"values": [{"stacktrace": {"frames": [{"vars": {"t": SAID}}]}}]},
        "contexts": {"runtime": {"name": "CPython", "version": "3.11.9"},
                     "os": {"name": "Linux"}, "device": {"hostname": "KYLE-PC"}},
    }


def test_before_send_keeps_nothing_personal():
    out = errortracking.before_send(sentry_event(), {"exc_info": None})
    text = json.dumps(out)
    for leaked in (SAID, "Dr. Lee", EMAIL, KEY, "KYLE-PC", "/home/kyle", "code=abc",
                   "203.0.113.9", "hc_session", ".env.mom", "user_text", "Bearer"):
        assert leaked not in text, leaked
    assert set(out) <= {"event_id", "timestamp", "level", "platform", "logger",
                        "release", "environment", "transaction", "exception",
                        "logentry", "request", "contexts"}
    assert out["request"] == {"method": "POST", "url": "/api/turn"}  # method and path only
    (exc,) = out["exception"]["values"]
    assert exc["type"] == "ValueError" and "value" not in exc
    assert exc["mechanism"] == {"type": "starlette", "handled": False}
    assert exc["stacktrace"]["frames"] == [{
        "filename": "assistant/chat.py", "function": "ask", "lineno": 120,
        "module": "assistant.chat", "in_app": True}]
    # the format string is kept, its secrets masked
    assert out["logentry"] == {"message": "Turn failed (key [REDACTED], mail [EMAIL])"}
    assert out["contexts"] == {"runtime": {"name": "CPython", "version": "3.11.9"},
                               "os": {"name": "Linux"}}
    assert out["release"] == "hey-claude-1.0.2"


def test_before_send_drops_an_event_it_cannot_clean(monkeypatch):
    def broken(text):
        raise RuntimeError("redaction failed")
    monkeypatch.setattr("assistant.config.redact", broken)
    assert errortracking.before_send(sentry_event()) is None  # never sent as it is


def test_the_dsn_is_a_secret_no_other_instance_inherits():
    assert "SENTRY_DSN" in SECRET_ENV_VARS and "SENTRY_DSN" in ISOLATED_ENV_VARS


def test_render_offers_the_dsn_as_a_secret():
    import re

    from tests.test_cloud import _render_yaml

    assert re.search(r"key: SENTRY_DSN\s*(#.*)?\n\s*sync: false", _render_yaml())


def test_serve_and_cli_start_it(sentry, monkeypatch, tmp_path):
    """webapp.serve (and cli.main for every mode) turn it on with a DSN."""
    import uvicorn

    from assistant import cli, webapp

    monkeypatch.setenv(errortracking.DSN_ENV, DSN)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(webapp, "build_assistant", lambda *a, **k: object())
    monkeypatch.setattr(webapp, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr("assistant.webfeatures.Features.__init__", lambda *a, **k: None)
    monkeypatch.setattr("assistant.webfeatures.Features.resume_jobs", lambda self: None)
    monkeypatch.setenv(webapp.TOKEN_ENV, "x" * 40)
    cfg = load_config(None, None)
    cfg["devteam"]["hub"] = False
    cfg["watch"]["banner"] = False
    webapp.serve(cfg, host="127.0.0.1", port=8765, env_file=tmp_path / ".env",
                 output_fn=lambda m: None)
    assert len(sentry.calls) == 1

    monkeypatch.setattr(errortracking, "_started", False)
    monkeypatch.setattr("assistant.config.setup_logging", lambda config: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # main stops right after
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("")
    cli.main(["--mode", "text", "--config", str(tmp_path / "config.toml"),
              "--env-file", str(tmp_path / ".env")])
    assert len(sentry.calls) == 2
