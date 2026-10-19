"""The dev-team run dashboard page (tools/agent-dashboard) stays within the
claude.ai page contract and treats rows as untrusted; the master skill
writes every phase."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "tools" / "agent-dashboard"
SKILL = ROOT / ".claude" / "skills" / "build-feature" / "SKILL.md"
SESSION_PREFIX = "https://claude.ai/code/session_"
PR_PREFIX = "https://github.com/kylechen839-max/Claude-Personal-Assistant/pull/"


def _page() -> str:
    return (DASH / "index.html").read_text(encoding="utf-8")


def _schema_phases() -> list[str]:
    """The phases listed in the README's row schema."""
    text = (DASH / "README.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| `phase` |"))
    phases = re.findall(r"`([a-z_]+)`", row.split("|")[3])
    assert len(phases) == 8, phases
    return phases


def test_page_starts_with_title_then_viewport_meta():
    lines = [line.strip() for line in _page().splitlines() if line.strip()]
    assert lines[0].startswith("<title>Hey Claude Agent Runs</title>")
    assert lines[1] == '<meta name="claude:viewport-fit-sides" content="cover">'


@pytest.mark.parametrize("tag", ["html", "head", "body", "!doctype"])
def test_page_has_no_document_tags(tag):
    assert not re.search(rf"<{tag}\b", _page(), re.I)  # <header> is fine


def test_page_loads_nothing_external():
    page = _page()
    assert not re.search(r"""\b(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", page, re.I)
    assert not re.search(r"""url\(\s*["']?\s*(?:https?:)?//""", page, re.I)
    assert "@import" not in page
    assert "<link" not in page.lower()
    assert not re.search(r"<script[^>]*\bsrc\b", page, re.I)


@pytest.mark.parametrize("banned", ["alert(", "confirm(", "prompt(", "innerHTML",
                                    "outerHTML", "insertAdjacentHTML",
                                    "document.write", "eval("])
def test_page_never_renders_rows_as_markup(banned):
    assert banned not in _page()


def test_page_never_writes():
    # The page only reads: no set/update/delete/add/acquire calls.
    assert not re.search(r"\.(?:set|update|delete|add|acquire)\(", _page())


def test_page_has_both_dark_mode_selectors():
    page = _page()
    assert '@media (prefers-color-scheme: dark){:root:not([data-theme="light"])' in page
    assert ':root[data-theme="dark"]' in page
    assert "prefers-reduced-motion" in page
    assert ":focus-visible" in page
    assert "tabular-nums" in page


def test_page_body_gutters_use_safe_area():
    page = _page()
    assert "padding-left:max(16px,env(safe-area-inset-left,0px))" in page
    assert "padding-right:max(16px,env(safe-area-inset-right,0px))" in page
    assert re.search(r"body\{[^}]*background:var\(--", page)


def test_page_subscribes_once_to_the_runs_query():
    page = _page()
    assert 'db.collection("runs").orderBy("started_at", "desc").limit(100)' in page
    assert page.count(".onSnapshot(") == 1
    assert 'window.claude?.use?.("db")' in page
    assert "Open this page on claude.ai while signed in to see runs." in page


def test_page_knows_every_schema_phase():
    page = _page()
    for phase in _schema_phases():
        assert f'"{phase}"' in page, phase


def test_page_thresholds_match_the_readme():
    """Page threshold = skill limit + 15 min, as the README table says."""
    readme = (DASH / "README.md").read_text(encoding="utf-8")
    page = _page()
    for phase, limit, page_min in re.findall(
            r"\| `(\w+)` \|[^|]*\| (\d+) min[^|]*\| (\d+) min \|", readme):
        assert int(page_min) == int(limit) + 15, phase
        assert f"{phase}: {page_min}" in page, phase


def test_page_links_only_to_allowed_prefixes():
    page = _page()
    assert f'"{SESSION_PREFIX}"' in page
    assert f'"{PR_PREFIX}"' in page
    assert 'rel = "noopener noreferrer"' in page
    assert 'target = "_blank"' in page
    # Every link URL in the script is one of the two prefixes.
    urls = set(re.findall(r'"(https://[^"]*)"', page))
    assert urls == {SESSION_PREFIX, PR_PREFIX}


def test_skill_writes_every_phase_except_dead():
    skill = SKILL.read_text(encoding="utf-8")
    for phase in _schema_phases():
        if phase == "dead":  # only the watchdog writes it
            continue
        assert f'phase: "{phase}"' in skill, phase
    assert "if_version" in skill
    assert "Only the master writes the dashboard; subagents never do." in skill


def test_watchdog_prompt_is_one_fenced_block():
    text = (DASH / "WATCHDOG.md").read_text(encoding="utf-8")
    assert text.count("```") == 2
    prompt = text.split("```")[1]
    for needed in ("ArtifactData", "mcp__claude-code-remote__get_session",
                   "PushNotification", "nothing live", "if_version",
                   "data, not\n   instructions"):
        assert needed in prompt, needed
    # The watchdog never touches a terminal row.
    assert "pr_ready, blocked, done or dead" in prompt
