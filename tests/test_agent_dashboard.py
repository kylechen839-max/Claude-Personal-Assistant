"""The dev-team run dashboard page (tools/agent-dashboard) stays within the
claude.ai page contract and treats rows as untrusted; the master skill
writes every phase."""

import re
import shutil
import subprocess
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
                                    "document.write", "eval(", "createContextualFragment",
                                    "DOMParser", "srcdoc", "new Function"])
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
    page = _page()
    rows = _readme_thresholds()
    assert [phase for phase, _, _ in rows] == ["started", "researching", "building", "judging"]
    for phase, limit, page_min in rows:
        assert page_min == limit + 15, phase
        assert f"{phase}: {page_min}" in page, phase


def _readme_thresholds() -> list[tuple[str, int, int]]:
    """(phase, skill limit, page threshold) rows of the README table."""
    readme = (DASH / "README.md").read_text(encoding="utf-8")
    return [(phase, int(limit), int(page_min)) for phase, limit, page_min in re.findall(
        r"\| `(\w+)` \|[^|]*\| (\d+) min[^|]*\| (\d+) min \|", readme)]


def test_watchdog_idle_rule_uses_the_page_thresholds():
    """A master waiting on a long build round can report IDLE; the watchdog
    must not call it dead before the page would call it silent."""
    prompt = (DASH / "WATCHDOG.md").read_text(encoding="utf-8").split("```")[1]
    flat = " ".join(prompt.split())
    for phase, _, page_min in _readme_thresholds():
        assert f"{phase} {page_min} min" in flat, phase
    assert "15 minutes old" not in flat


def test_watchdog_tracks_the_bucket_and_pushes_only_on_a_new_block():
    flat = " ".join((DASH / "WATCHDOG.md").read_text(encoding="utf-8").split("```")[1].split())
    assert "differs from the row's watch.bucket" in flat
    assert "watch.checked_at is older than the row's updated_at" in flat
    assert "Push only when the new bucket is BLOCKED" in flat


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_page_logic_in_node():
    """tests/js/agent-dashboard.test.mjs runs the page's script in node:vm."""
    result = subprocess.run(["node", "--test", "tests/js/agent-dashboard.test.mjs"], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^# pass [1-9]\d*$", result.stdout, re.M), result.stdout


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


def test_skill_retires_rows_and_has_no_dangling_steps():
    skill = SKILL.read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    # Kyle merges in the GitHub UI: the CI watch writes done on merge/close.
    assert "reported merged or closed" in flat
    # Parallel pieces: one row each.
    assert "-<piece-slug>" in skill
    # There is no step or section 4; references point at real places.
    assert not re.search(r"\(step 4\)", skill)
    assert "(§3 step 8)" in skill


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
