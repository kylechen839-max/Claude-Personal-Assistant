"""The docs only mention flags and settings that exist in the code."""

import argparse
import re
import runpy
import sys
from pathlib import Path

import pytest

from assistant import cli, tts
from assistant.config import DEFAULTS

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "MANUAL_TEST.md"]
# Flags of other programs that the docs show (pip, systemctl, journalctl,
# scripts/install_service.sh).
OTHER_FLAGS = {"--no-deps", "--only-binary", "--platform", "--python-version",
               "--user", "--uninstall", "--bg", "--https"}  # + tailscale serve
# Tool names, not settings.
OTHER_IDENTIFIERS = {"open_in_browser", "email_summary", "read_email",
                     "draft_reply", "remember_note", "recall_notes",
                     "list_recent_notes", "delete_note"}


class _Captured(Exception):
    pass


def _parser_flags(monkeypatch, run) -> set[str]:
    """Option strings of the parser that `run` builds."""
    found = []

    def capture(self, *args, **kwargs):
        found.append(self)
        raise _Captured

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_Captured):
        run()
    return {s for action in found[0]._actions for s in action.option_strings}


@pytest.fixture
def code_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calendar"])
    monkeypatch.delitem(sys.modules, "assistant.tools.calendar", raising=False)
    return (_parser_flags(monkeypatch, lambda: cli.parse_args([]))
            | _parser_flags(monkeypatch, lambda: tts.main([]))
            | _parser_flags(monkeypatch, lambda: runpy.run_module(
                "assistant.tools.calendar", run_name="__main__")))


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", DOCS)
def test_every_flag_in_docs_exists(doc, code_flags):
    flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", _text(doc)))
    assert flags - code_flags - OTHER_FLAGS == set()


@pytest.mark.parametrize("doc", DOCS)
def test_every_section_key_in_docs_exists(doc):
    text = _text(doc)
    for section, key in re.findall(r"\[(\w+)\]\s*`?(\w+)", text):
        if section in DEFAULTS:  # skips e.g. "[index] name"
            assert key in DEFAULTS[section], f"[{section}] {key}"
    for block in re.findall(r"```toml\n(.*?)```", text, re.DOTALL):
        for section, values in tomllib.loads(block).items():
            assert set(values) <= set(DEFAULTS[section]), section


@pytest.mark.parametrize("doc", DOCS)
def test_every_setting_name_in_docs_exists(doc):
    keys = {k for section in DEFAULTS.values() for k in section}
    names = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", _text(doc)))
    assert names - keys - OTHER_IDENTIFIERS == set()


def test_config_toml_keys_have_defaults():
    with open(ROOT / "config.toml", "rb") as f:
        shipped = tomllib.load(f)
    for section, values in shipped.items():
        assert set(values) <= set(DEFAULTS[section]), section


@pytest.mark.parametrize("doc", DOCS + ["config.toml", ".env.example"])
def test_no_stale_names(doc):
    text = _text(doc).lower()
    for stale in ("porcupine", "picovoice", "claude-sonnet-4"):
        assert stale not in text
    assert DEFAULTS["assistant"]["model"] in _text("config.toml")
