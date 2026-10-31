"""The docs only mention flags and settings that exist in the code."""

import argparse
import re
import runpy
import sys
from pathlib import Path

import pytest

from kwil import (cli, deploy, family_import, preflight, recall, smoke, tts, tts_kokoro,
                  watch)
from kwil.config import DEFAULTS

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
               "--cloud",  # claude -p --cloud (the dev team voice link)
               "--build-arg"}  # docker build (FAMILY_ZH=0)
OTHER_IDENTIFIERS = {"open_in_browser", "search_conversations", "read_conversation",
                     "resume_conversation",  # tool names, not settings
                     "email_summary", "read_email", "draft_reply",
                     "remember_note", "recall_notes", "list_recent_notes",
                     "delete_note", "ha_list", "ha_state", "ha_control",
                     "month_usd", "cache_read",  # /api/state field, usage log
                     # starter credit and feedback API fields (credit.py, feedback.py)
                     "credit_used_usd", "credit_left_usd", "credit_total_used",
                     "credit_total_cap", "feedback_due",
                     "chat_channels", "chat_read", "chat_search", "chat_files",
                     "chat_read_file", "chat_save_digest",
                     "list_assignments", "update_assignment_status",
                     "list_deadlines", "confirm_deadline", "add_deadline",
                     "delete_deadline", "search_course_files", "read_course_file",
                     "search_project_files", "read_project_file",
                     "calendar_color_habits",
                     "create_reminder", "list_reminders", "update_reminder",
                     "cancel_reminder",  # tools/reminders.py
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
    monkeypatch.delitem(sys.modules, "kwil.tools.calendar", raising=False)
    return (_parser_flags(monkeypatch, lambda: cli.parse_args([]))
            | _parser_flags(monkeypatch, lambda: tts.main([]))
            | _parser_flags(monkeypatch, lambda: preflight.parse_args([]))
            | _parser_flags(monkeypatch, lambda: smoke.main([]))
            | _parser_flags(monkeypatch, lambda: watch.main([]))
            | _parser_flags(monkeypatch, lambda: deploy.main([]))
            | _parser_flags(monkeypatch, lambda: family_import.main([]))
            | _parser_flags(monkeypatch, lambda: recall.main([]))
            | _parser_flags(monkeypatch, lambda: runpy.run_module(
                "kwil.tools.calendar", run_name="__main__")))


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
    default value (piece B's zh_* keys included). One exception: the
    shipped config.toml carries Kyle's published Notion template link
    (public, not a secret) while the code default stays "" (none)."""
    with open(ROOT / "config.toml", "rb") as f:
        shipped = tomllib.load(f)["family"]
    template = shipped.pop("notion_template_url")
    assert DEFAULTS["family"]["notion_template_url"] == ""
    assert template.startswith("https://") and "?" not in template
    assert shipped == {k: v for k, v in DEFAULTS["family"].items()
                       if k != "notion_template_url"}
    assert shipped["invite_days"] == 30


def test_voice_camera_keys_are_mirrored_in_config_toml_and_readme():
    """The voice view's Camera button settings: a default each, the same
    commented line in config.toml, and named in README's voice section."""
    with open(ROOT / "config.toml", "rb") as f:
        shipped = tomllib.load(f)["voice"]
    camera = {k: v for k, v in DEFAULTS["voice"].items() if k.startswith("camera")}
    assert camera == {"camera": True, "camera_max_side": 1024, "camera_max_kb": 400,
                      "camera_keep_frames": 1}
    assert {k: shipped[k] for k in camera} == camera
    toml = _text("config.toml")
    for key in camera:
        assert re.search(rf"^{key} = \S+ +# \S", toml, re.M), key
    readme = _text("README.md")
    for key in camera:
        assert f"[voice] {key}" in readme, key


def test_stt_section_is_mirrored_in_config_toml_and_readme():
    """Every [stt] setting has a line in config.toml with its default and a
    row in README's settings table; the keys the cloud engine reads are
    secrets (not isolated: see test_stt_cloud.py), and Mom's instance stays local with beam search."""
    from kwil.config import ENV_OVERRIDES, ISOLATED_ENV_VARS, SECRET_ENV_VARS

    with open(ROOT / "config.toml", "rb") as f:
        shipped = tomllib.load(f)["stt"]
    assert shipped == DEFAULTS["stt"]
    assert shipped["model"] == "small.en" and shipped["beam_size"] == 5
    readme = _text("README.md")
    for key in DEFAULTS["stt"]:
        if key not in ("device", "compute_type"):
            assert f"| `[stt] {key}` |" in readme, key
    for var in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY"):
        assert var in SECRET_ENV_VARS and var not in ISOLATED_ENV_VARS
        assert var in _text(".env.example") and var in _text("render.yaml")
    assert ENV_OVERRIDES["STT_ENGINE"] == ("stt", "engine")
    assert "STT_ENGINE" in _text("render.yaml")
    with open(ROOT / "config.mom.toml", "rb") as f:
        mom = tomllib.load(f)["stt"]
    assert mom["model"] == "small" and mom["beam_size"] == 5 and mom["engine"] == "whisper"


def test_pi_stt_advice_keeps_config_local_toml_valid():
    """install_pi.sh writes an [stt] table to config.local.toml; the docs
    must say to add settings under it, since a second [stt] header is
    invalid TOML. CI caches small.en for the Pi simulation too."""
    script = _text("scripts/install_pi.sh")
    written = re.search(r"printf '(\\n\[stt\]\\n[^']*)'", script).group(1)
    written = written.replace("\\n", "\n")
    assert tomllib.loads(written)["stt"]["model"] == "base.en"
    assert tomllib.loads(written + "beam_size = 1\n")["stt"]["beam_size"] == 1
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads(written + "[stt]\nbeam_size = 1\n")
    pi = " ".join(_text("PI_MIGRATION.md").split())
    assert "add it under the existing `[stt]` line in `config.local.toml`" in pi
    assert "under its existing `[stt]` line, model" in pi  # the nano line
    assert "`beam_size = 1` in `config.local.toml`" not in pi
    workflow = _text(".github/workflows/pi-arm64.yml")
    assert re.search(r"key: pi-sim-[\w.-]*small\.en", workflow)
    assert re.search(r"faster-whisper>=1\.0\.2\b", _text("requirements.txt"))  # hotwords


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


# The Kwil rebrand (2026-10-22): the old names only where a line says they
# are the old ones (or "Hey Claude" is the wake word, in quotes, or said).
PRE_REBRAND = [re.compile(p) for p in (
    r"-m assistant\b",                        # the package is kwil
    r"(?<![\w.-])assistant/\w",               # its folder
    r"deploy/hey-claude|hey-claude(-watch)?\.(service|timer)",
    r"(systemctl --user( \w+)*|journalctl --user -u) hey-claude\b",  # the Pi unit
    r"\\HeyClaude\\|`HeyClaude`",             # the PC's task folder
    r"(?<![\"'*])\bHey Claude\b(?![,\"'*]| Web\b| Beta\b)",  # the product name
)]
OLD_NAME_NOTE = re.compile(r"rebrand|before the|its name before|\bold\b|kept for one release|"
                           r"until the tasks|was called Hey Claude|renamed from",
                           re.IGNORECASE)


@pytest.mark.parametrize("doc", DOCS + ["SECURITY.md", "FAILOVER.md", "PRIVACY.md",
                                        "config.toml", ".env.example"])
def test_no_pre_rebrand_names(doc):
    stale = [line for line in _text(doc).splitlines()
             if any(p.search(line) for p in PRE_REBRAND) and not OLD_NAME_NOTE.search(line)]
    assert stale == [], doc


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
