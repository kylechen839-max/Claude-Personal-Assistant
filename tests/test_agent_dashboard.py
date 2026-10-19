"""The dev-team run dashboard page (tools/agent-dashboard) stays within the
claude.ai page contract and treats rows as untrusted; the master skill
writes every phase."""

import os
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
    # The page never writes rows: no set/update/delete/add/acquire calls.
    # (Its only write anywhere is the chat panel's send_message, below.)
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


def test_watchdog_pushes_once_per_session_not_per_row():
    """Piece rows share the master's session_url: one push per session."""
    flat = " ".join((DASH / "WATCHDOG.md").read_text(encoding="utf-8").split("```")[1].split())
    assert "at most one PushNotification per session per check" in flat
    assert "naming every affected feature" in flat
    assert "push per row" not in flat
    assert "Send one PushNotification" not in flat


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_page_logic_in_node():
    """tests/js/agent-dashboard.test.mjs runs the page's script in node:vm."""
    # Node 23+ defaults to the spec reporter ("ℹ pass 25"); ask for TAP and
    # accept either prefix. Reporters from NODE_OPTIONS add to ours and node
    # then refuses two reporters without destinations, so drop those there.
    env = dict(os.environ)
    env["NODE_OPTIONS"] = re.sub(r"--test-reporter(-destination)?(=|\s+)\S+", "",
                                 env.get("NODE_OPTIONS", ""))
    result = subprocess.run(["node", "--test", "--test-reporter=tap",
                             "tests/js/agent-dashboard.test.mjs"], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^(#|ℹ) pass [1-9]\d*$", result.stdout, re.M), result.stdout


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
    # A piece row is a full set: it names every field §0 step 4 writes.
    piece = flat.split("When you split,", 1)[1].split("the piece rows take over", 1)[0]
    for field in ("§0 step 4", "`feature` = the piece name", "`branch` = the piece's branch",
                  "same `session_url`", "`started_at`", "`updated_at`",
                  '`phase: "started"`', "`round: 0`"):
        assert field in piece, field
    # §2 sends an approved piece on to §3, not to its own step 3.
    section2 = skill.split("## 2.", 1)[1].split("## 3.", 1)[0]
    assert "go to step 3" not in section2
    assert "go to §3" in section2
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


# ---- Grid view, chat panel and desktop launchers ----

DASHBOARD_URL = "https://claude.ai/artifact/CpPTyJkkHZ7CK8jsPYZq4G"
DESKTOP = DASH / "desktop"
MANIFEST = ('{"db":{}, "user":{}, "mcp":{"servers":[{"server":"Claude Code Remote",'
            '"tools":["list_sessions","list_events","send_message"]}]}}')
TOOLS = {"list_sessions", "list_events", "send_message"}


def test_readme_pins_the_manifest():
    readme = (DASH / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", readme, re.S)
    assert [b.strip() for b in blocks] == [MANIFEST]
    import json
    servers = json.loads(MANIFEST)["mcp"]["servers"]
    assert servers == [{"server": "Claude Code Remote", "tools": sorted(TOOLS, key=MANIFEST.index)}]


def test_page_names_only_the_manifest_tools():
    page = _page()
    assert '"Claude Code Remote"' in page
    for banned in ("archive_session", "unarchive_session", "interrupt_session", "create_session",
                   "set_session_", "_trigger", "get_session", "mine:", '"mine"'):
        assert banned not in page, banned
    # Every connector call goes through the TOOLS table, which names exactly
    # the manifest's tools, and no tool name appears anywhere else.
    table = re.search(r"const TOOLS = Object\.freeze\(\{([^}]*)\}\)", page)
    assert table and set(re.findall(r'"([a-z_]+)"', table.group(1))) == TOOLS
    for tool in TOOLS:
        assert page.count(f'"{tool}"') == 1, tool
    calls = re.findall(r"\.(callTool|watchTool|invalidate)\(\s*([^,)]*),\s*([^,)]*)", page)
    assert calls
    for _, server, tool in calls:
        assert server == "MCP_SERVER", server
        assert re.fullmatch(r"TOOLS\.(sessions|events|send)", tool), tool
    assert 'window.claude?.use?.("mcp")' in page


def test_page_never_logs_and_sends_only_on_click():
    page = _page()
    assert "console." not in page
    assert page.count("TOOLS.send") == 1
    # The send happens in sendChat only, which only a click, Ctrl/Cmd+Enter
    # or the approval button calls; never a timer.
    send_fn = page.split("function sendChat()", 1)[1].split("\n  }\n", 1)[0]
    assert "TOOLS.send" in send_fn
    assert not re.search(r"set(?:Timeout|Interval)\([^)]*sendChat", page)
    assert 'maxlength="4000"' in page
    assert "localStorage.setItem(VIEW_KEY, view)" in page  # the only thing stored


def test_grid_layout_rules():
    page = _page()
    assert "repeat(auto-fill,minmax(260px,1fr))" in page
    assert 'main[data-view="grid"]{max-width:none}' in page
    for needed in ('id="view-runs"', 'id="view-grid"', 'aria-pressed', 'id="grid-view" hidden',
                   'id="grid-summary"', 'id="grid-conn"', 'id="grid"', 'id="chat" hidden'):
        assert needed in page, needed
    # Runs keeps its ids.
    for kept in ('id="summary"', 'id="conn"', 'id="runs"', 'id="empty"'):
        assert kept in page, kept


def test_launchers_open_the_dashboard_as_an_app_window():
    readme = (DASH / "README.md").read_text(encoding="utf-8")
    assert DASHBOARD_URL in readme
    for name in ("open-dashboard.cmd", "open-dashboard.command"):
        text = (DESKTOP / name).read_text(encoding="utf-8")
        urls = set(re.findall(r"https://[^\s\"']+", text))
        assert urls == {DASHBOARD_URL}, (name, urls)
        assert "--app=" in text, name
        assert "--user-data-dir" not in text, name


def test_windows_launcher_is_crlf_with_a_default_browser_fallback():
    raw = (DESKTOP / "open-dashboard.cmd").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("ascii")
    lines = text.splitlines()
    browsers = [line for line in lines if line.startswith("if exist ")]
    assert len(browsers) == 5
    for line in browsers:
        # Quoted paths: an unquoted (x86) would close the ( ... ) block early.
        path = re.match(r'if exist "([^"]+)" \(start "" "([^"]+)" --app=%URL% & exit /b 0\)$', line)
        assert path and path.group(1) == path.group(2), line
    # After the browsers, the first command is the default-browser fallback.
    after = [line for line in lines[lines.index(browsers[-1]) + 1:] if not line.startswith("rem ")]
    assert after[0] == 'start "" "%URL%"'
    gitattributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.cmd text eol=crlf" in gitattributes


def test_mac_launcher_tries_chrome_then_edge_then_default():
    text = (DESKTOP / "open-dashboard.command").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\r" not in text
    assert 'for app in "Google Chrome" "Microsoft Edge"' in text
    # A silent check for the app: `open -R` reveals it in Finder each launch.
    assert "open -R" not in text
    assert '[ -d "/Applications/$app.app" ] || [ -d "$HOME/Applications/$app.app" ]' in text
    assert 'open -na "$app" --args --app="$URL"' in text
    assert text.rstrip().endswith('exec open "$URL"')


def test_shell_scripts_are_pinned_to_lf():
    # core.autocrlf=true (Git for Windows' default) would check them out
    # with CRLF, and bash then fails on "bash\r" in the shebang.
    rules = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.command text eol=lf" in rules
    assert "*.sh text eol=lf" in rules


def test_mac_launcher_parses():
    bash = shutil.which("bash")
    if not bash or os.name == "nt":
        pytest.skip("bash not available")
    subprocess.run([bash, "-n", str(DESKTOP / "open-dashboard.command")], check=True)


def test_mac_launcher_shellcheck():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    p = subprocess.run(["shellcheck", str(DESKTOP / "open-dashboard.command")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout
