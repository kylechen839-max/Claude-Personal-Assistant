"""[failover] settings and the --role flag."""

import copy

import pytest

from assistant import cli
from assistant.config import DEFAULTS, load_config
from assistant.failover.peers import settings_from_config


def cfg(**failover):
    c = copy.deepcopy(DEFAULTS)
    c["failover"].update(failover)
    return c


def test_role_flag():
    assert cli.parse_args(["--role", "pi"]).role == "pi"
    assert cli.parse_args([]).role is None
    with pytest.raises(SystemExit):
        cli.parse_args(["--role", "mac"])


def test_role_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_ROLE", "pc")
    assert load_config(tmp_path / "none.toml", env_file=None)["failover"]["role"] == "pc"


def test_shipped_config_is_standalone():
    s = settings_from_config(load_config("config.toml", env_file=None))
    assert s.role == "" and s.backends == ()


def test_settings_cloud_defaults_to_app_origin():
    s = settings_from_config(cfg(role="pi", app_origin="https://hc.onrender.com",
                                 backends=[{"role": "pc", "url": "https://pc.t.ts.net"},
                                           {"role": "pi", "url": "https://pi.t.ts.net"},
                                           {"role": "cloud", "url": ""}]))
    assert [(b.role, b.url) for b in s.backends] == [
        ("pi", "https://pi.t.ts.net"), ("pc", "https://pc.t.ts.net"),
        ("cloud", "https://hc.onrender.com")]
    assert [b.role for b in s.peers] == ["pc", "cloud"]


def test_settings_validation():
    with pytest.raises(ValueError):
        settings_from_config(cfg(role="mac"))
    with pytest.raises(ValueError):
        settings_from_config(cfg(app_origin="http://hc.onrender.com"))


def test_main_applies_role(monkeypatch, tmp_path):
    seen = {}

    def fake_setup_logging(config):
        seen["role"] = config["failover"]["role"]
        raise SystemExit(0)

    monkeypatch.setattr("assistant.config.setup_logging", fake_setup_logging)
    with pytest.raises(SystemExit):
        cli.main(["--role", "cloud", "--config", str(tmp_path / "none.toml")])
    assert seen["role"] == "cloud"


def test_failover_tokens_are_redacted_in_logs(monkeypatch):
    from assistant.config import redact
    from assistant.failover.tokens import TokenAuthority

    secret = "w" * 43
    monkeypatch.setenv("WEB_TOKEN", secret)
    token, _ = TokenAuthority(secret).mint("app")
    line = redact(f"login with {secret}, got {token} in a message body")
    assert secret not in line and token not in line
