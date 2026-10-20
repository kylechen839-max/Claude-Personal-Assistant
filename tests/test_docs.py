"""The docs only mention flags and settings that exist in the code."""

import argparse
import re
import runpy
import sys
from pathlib import Path

import pytest

from assistant import cli, deploy, preflight, smoke, tts, tts_kokoro, watch
from assistant.config import DEFAULTS

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "MANUAL_TEST.md", "PI_MIGRATION.md"]
# Flags of other programs that the docs show (pip, systemctl, journalctl,
# scripts/install_service.sh).
OTHER_FLAGS = {"--no-deps", "--only-binary", "--platform", "--python-version",
               "--user", "--uninstall", "--now", "--bg", "--https",  # + tailscale serve
               "--keep", "--ff-only", "--short", "--oneline",  # git (updates)
               "--test",  # node --test
               "--cloud",  # claude -p --cloud (the PC hub)
               "--build-arg"}  # docker build (FAMILY_ZH=0)
OTHER_IDENTIFIERS = {"open_in_browser", "search_conversations",
                     "resume_conversation",  # tool names, not settings
                     "email_summary", "read_email", "draft_reply",
                     "remember_note", "recall_notes", "list_recent_notes",
                     "delete_note", "ha_list", "ha_state", "ha_control",
                     "month_usd", "cache_read",  # /api/state field, usage log
                     "chat_channels", "chat_read", "chat_search", "chat_files",
                     "chat_read_file", "chat_save_digest",
                     *tts_kokoro.ENGLISH_VOICES}  # Kokoro voice names


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
            | _parser_flags(monkeypatch, lambda: preflight.parse_args([]))
            | _parser_flags(monkeypatch, lambda: smoke.main([]))
            | _parser_flags(monkeypatch, lambda: watch.main([]))
            | _parser_flags(monkeypatch, lambda: deploy.main([]))
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


def test_family_section_is_mirrored_in_config_toml():
    """Every [family] setting has a commented line in config.toml with the
    default value (piece B's zh_* keys included)."""
    with open(ROOT / "config.toml", "rb") as f:
        shipped = tomllib.load(f)["family"]
    assert shipped == DEFAULTS["family"]
    assert shipped["invite_days"] == 30


@pytest.mark.parametrize("name", ["config.toml", "config.mom.toml"])
def test_config_toml_keys_have_defaults(name):
    with open(ROOT / name, "rb") as f:
        shipped = tomllib.load(f)
    for section, values in shipped.items():
        assert set(values) <= set(DEFAULTS[section]), section


@pytest.mark.parametrize("doc", DOCS + ["config.toml", ".env.example"])
def test_no_stale_names(doc):
    text = _text(doc).lower()
    for stale in ("porcupine", "picovoice", "claude-sonnet-4"):
        assert stale not in text
    assert DEFAULTS["assistant"]["model"] in _text("config.toml")


@pytest.mark.parametrize("doc", DOCS)
def test_every_script_in_docs_exists(doc):
    for path in re.findall(r"scripts[/\\][\w/\\]+\.(?:sh|ps1|py)", _text(doc)):
        assert (ROOT / path.replace("\\", "/")).exists(), path


@pytest.mark.parametrize("doc", DOCS)
def test_preflight_steps_in_docs_exist(doc):
    """`--skip a,b` lists and the step names in backticks are real steps."""
    text = _text(doc)
    for names in re.findall(r"--skip ([\w,-]+)", text):
        assert set(names.split(",")) <= set(preflight.STEPS), names
    for step in re.findall(r"`((?:tts|pytest|audio|mic|wake|text)-[a-z0-9]+)`", text):
        assert step in preflight.STEPS, step


def test_readme_lists_every_preflight_step():
    text = _text("README.md")
    assert [s for s in preflight.STEPS if f"`{s}`" not in text] == []


def test_pi_migration_go_no_go():
    """The go/no-go list names all four gates."""
    text = _text("PI_MIGRATION.md")
    go = text[text.index("## 9. Go/no-go"):]
    for gate in ("preflight.ps1", "pi-arm64", "cloud_smoke", "preflight.sh --hardware",
                 "MANUAL_TEST.md"):
        assert gate in go, gate
