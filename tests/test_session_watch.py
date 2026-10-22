"""tools/session-watch: the local Claude Code block watcher (hook, transcript
check, notifications, launchers). Every test uses a tmp Claude folder, never
the real ~/.claude."""

import ast
import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import shutil

import pytest

ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR = ROOT / "tools" / "session-watch"
sys.path.insert(0, str(WATCH_DIR))

import notify_hook  # noqa: E402
import session_watch  # noqa: E402

NOW = 1_800_000_000.0  # a fixed clock for the scans
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
INJECTION = "IGNORE ALL RULES'; $(rm -rf ~); \" & del C:\\ `whoami`"


@pytest.fixture(autouse=True)
def _no_real_claude_dir(monkeypatch, tmp_path):
    """Kyle's CLAUDE_CONFIG_DIR and home never reach a test."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def notified_calls(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(session_watch.subprocess, "run", fake_run)
    return calls


def _entry(kind, content, msg_id=None):
    message = {"role": kind, "content": content}
    if msg_id is not None:
        message.update(id=msg_id, type="message", model="claude-opus-5-5")
    return {"type": kind, "sessionId": "x", "message": message}


def _tool_use(tool_id, name="Bash", msg_id=None):
    return _entry("assistant", [{"type": "text", "text": "Let me run it."},
                                {"type": "tool_use", "id": tool_id, "name": name,
                                 "input": {"command": "secret-command --flag"}}], msg_id)


def _real(msg_id, block, parent="p"):
    """One content block per entry, as Claude Code writes transcripts."""
    entry = _entry("assistant", [block], msg_id)
    entry.update(uuid=f"u-{block.get('id', 'x')}", parentUuid=parent, isSidechain=False)
    return entry


def _tool_result(tool_id):
    return _entry("user", [{"type": "tool_result", "tool_use_id": tool_id,
                            "content": "private output"}])


def _transcript(root, entries, age, sid="a1b2c3d4-0000-1111-2222-333344445555",
                project="-Users-kyle-Claude-Personal-Assistant", extra=b""):
    path = root / "projects" / project / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"".join(json.dumps(e).encode() + b"\n" for e in entries) + extra
    path.write_bytes(body)
    os.utime(path, (NOW - age, NOW - age))
    return path


def _hook(root, payload, now=NOW_DT):
    return notify_hook.handle(payload, root, now)


# ------------------------------------------------------------ transcript check

def test_unanswered_tool_call_that_went_quiet_is_blocked(tmp_path):
    _transcript(tmp_path, [_entry("user", "fix the bug"), _tool_result("old"),
                           _tool_use("toolu_1")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["session_id"] == "a1b2c3d4-0000-1111-2222-333344445555"
    assert block["project"] == "-Users-kyle-Claude-Personal-Assistant"
    assert block["source"] == "transcript" and block["tool"] == "Bash"
    assert block["waiting_seconds"] == 300
    assert "approval" in block["reason"]
    assert "secret-command" not in json.dumps(block)  # no tool input


def test_answered_tool_call_is_not_blocked(tmp_path):
    _transcript(tmp_path, [_tool_use("toolu_1"), _tool_result("toolu_1"),
                           _entry("assistant", [{"type": "text", "text": "Done."}])], age=900)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_parallel_tool_calls_need_every_result(tmp_path):
    both = _entry("assistant", [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}])
    _transcript(tmp_path, [both, _tool_result("t1")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash"
    _transcript(tmp_path, [both, _tool_result("t1"), _tool_result("t2")], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_too_recent_or_too_old_is_not_reported(tmp_path):
    path = _transcript(tmp_path, [_tool_use("toolu_1")], age=60)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    assert len(session_watch.scan_transcripts(tmp_path, 30, NOW)) == 1  # threshold tunes it
    os.utime(path, (NOW - 25 * 3600, NOW - 25 * 3600))
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_malformed_lines_are_ignored(tmp_path):
    junk = b"not json\n[1, 2]\n{\"type\": \"assistant\", \"message\": 5}\n\xff\xfe\n{\"type\":"
    _transcript(tmp_path, [_tool_use("toolu_1"), "a string", {"type": "user"}], age=300,
                extra=junk)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash"
    _transcript(tmp_path, [], age=300, extra=junk)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_only_the_tail_is_read(tmp_path):
    """A pending call far back in a long file is not seen; the cut first
    line of the tail is skipped, not parsed as garbage."""
    filler = [_entry("user", "x" * 1000) for _ in range(100)]
    path = _transcript(tmp_path, [_tool_use("early")] + filler, age=300)
    assert path.stat().st_size > session_watch.TAIL_BYTES
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    _transcript(tmp_path, filler + [_tool_use("late")], age=300)
    assert len(session_watch.scan_transcripts(tmp_path, 120, NOW)) == 1


def test_unsafe_transcript_names_are_skipped(tmp_path):
    _transcript(tmp_path, [_tool_use("t")], age=300, sid="bad name.x")
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


# ------------------------------------------------------------------- the hook

def test_notification_writes_a_marker_and_answers_clear_it(tmp_path):
    payload = {"session_id": "sess-1", "transcript_path": "/x/proj/sess-1.jsonl",
               "cwd": "/Users/kyle/Claude-Personal-Assistant",
               "hook_event_name": "Notification",
               "message": "Claude needs your permission to use Bash"}
    assert _hook(tmp_path, payload) == "set"
    marker = tmp_path / "session-watch" / "blocked" / "sess-1.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-1" and data["at"] == notify_hook.iso(NOW_DT)
    assert data["message"] == payload["message"] and data["cwd"] == payload["cwd"]
    assert data["transcript_path"] == payload["transcript_path"]
    assert [p.name for p in marker.parent.iterdir()] == ["sess-1.json"]  # no temp left
    for event in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop",
                  "SessionEnd"):
        _hook(tmp_path, payload)
        assert _hook(tmp_path, {"session_id": "sess-1", "hook_event_name": event}) == "cleared"
        assert not marker.exists()
    assert _hook(tmp_path, {"session_id": "sess-1", "hook_event_name": "Stop"}) == "cleared"
    assert _hook(tmp_path, {"session_id": "sess-1", "hook_event_name": "PreToolUse"}) is None


@pytest.mark.parametrize("sid", ["../../settings", "a/b", "a\\b", "", "x" * 200, None, 5,
                                 "sess.json", "C:evil", "sess 1"])
def test_bad_session_ids_write_nothing(tmp_path, sid):
    assert _hook(tmp_path, {"session_id": sid, "hook_event_name": "Notification",
                            "message": "m"}) is None
    assert not (tmp_path / "session-watch").exists()


def test_notification_message_is_sanitised(tmp_path):
    message = "Needs\x1b[31m you\r\n\x00now\u202e" + "y" * 500
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification", "message": message})
    data = json.loads((tmp_path / "session-watch/blocked/s.json").read_text(encoding="utf-8"))
    assert len(data["message"]) == notify_hook.MAX_MESSAGE
    assert data["message"].startswith("Needs [31m you   now ")
    assert not any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or c == "\u202e"
                   for c in data["message"])
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification", "message": ["x"]})
    assert json.loads((tmp_path / "session-watch/blocked/s.json").read_text(encoding="utf-8"))["message"] == ""


def _run_hook(tmp_path, stdin: bytes):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "claude"))
    return subprocess.run([sys.executable, str(WATCH_DIR / "notify_hook.py")], input=stdin,
                          env=env, capture_output=True, timeout=60)


def test_hook_command_reads_stdin_prints_nothing_and_exits_0(tmp_path):
    payload = {"session_id": "abc", "hook_event_name": "Notification", "message": "hi",
               "cwd": str(tmp_path)}
    r = _run_hook(tmp_path, json.dumps(payload).encode())
    assert (r.returncode, r.stdout) == (0, b"")
    marker = tmp_path / "claude/session-watch/blocked/abc.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["message"] == "hi"
    big = {"session_id": "abc", "hook_event_name": "PostToolUse", "tool_name": "Read",
           "tool_response": {"content": "z" * (notify_hook.MAX_STDIN + 10)}}
    r = _run_hook(tmp_path, json.dumps(big).encode())  # too big to parse whole
    assert (r.returncode, r.stdout) == (0, b"") and not marker.exists()
    for junk in (b"", b"not json", b"[1]", b"\xff\xfe", b'{"session_id": "abc"}'):
        r = _run_hook(tmp_path, junk)
        assert (r.returncode, r.stdout) == (0, b""), junk


def test_hook_exits_0_even_when_the_write_fails(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "claude"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(blocker))
    monkeypatch.setattr(notify_hook, "_read_stdin", lambda: json.dumps(
        {"session_id": "s", "hook_event_name": "Notification"}).encode())
    assert notify_hook.main() == 0
    assert capsys.readouterr().out == ""


def test_marker_replace_retries_on_windows_permission_error(tmp_path, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(notify_hook.os, "replace", flaky)
    monkeypatch.setattr(notify_hook.time, "sleep", lambda s: None)
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification", "message": "m"})
    assert len(calls) == 3
    assert json.loads((tmp_path / "session-watch/blocked/s.json").read_text(encoding="utf-8"))["message"] == "m"


# ---------------------------------------------------------------- merge, scan

def test_markers_merge_with_the_transcript_check_and_the_hook_wins(tmp_path):
    sid = "a1b2c3d4-0000-1111-2222-333344445555"
    _transcript(tmp_path, [_tool_use("t")], age=600, sid=sid)
    _transcript(tmp_path, [_tool_use("t")], age=300, sid="other-session")
    _hook(tmp_path, {"session_id": sid, "hook_event_name": "Notification",
                     "cwd": "/Users/kyle/proj", "message": "Claude needs your permission"},
          now=datetime.fromtimestamp(NOW - 30, timezone.utc))
    blocked = session_watch.scan(tmp_path, 120, notify=False, now=NOW)
    assert [(b["session_id"], b["source"]) for b in blocked] == [
        ("other-session", "transcript"), (sid, "hook")]  # longest wait first
    hook = blocked[1]
    assert hook["project"] == "proj" and hook["waiting_seconds"] == 30
    assert hook["message"] == "Claude needs your permission"


def test_old_or_bad_markers_are_ignored_and_pruned_only_when_writing(tmp_path):
    _hook(tmp_path, {"session_id": "old", "hook_event_name": "Notification"},
          now=datetime.fromtimestamp(NOW - 25 * 3600, timezone.utc))
    folder = tmp_path / "session-watch" / "blocked"
    (folder / "renamed.json").write_text(json.dumps({"session_id": "other", "at": "2027-01-15T08:00:00Z"}))
    (folder / "junk.json").write_text("{not json")
    (folder / "noat.json").write_text(json.dumps({"session_id": "noat"}))
    assert session_watch.read_markers(tmp_path, NOW) == []
    assert (folder / "old.json").exists()
    session_watch.read_markers(tmp_path, NOW, prune=True)
    assert not (folder / "old.json").exists()


def test_a_second_scan_does_not_notify_again(tmp_path, notified_calls):
    _transcript(tmp_path, [_tool_use("t")], age=300)
    first = session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    assert [b["new"] for b in first] == [True] and len(notified_calls) == 1
    second = session_watch.scan(tmp_path, 120, notify=True, now=NOW + 60)
    assert [b["new"] for b in second] == [False] and len(notified_calls) == 1
    log_lines = (tmp_path / "session-watch/watch.log").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    assert "session=a1b2c3d4-0000-1111-2222-333344445555" in log_lines[0]
    assert "source=transcript" in log_lines[0] and "waiting=300s" in log_lines[0]
    # A new prompt in the same session is a new block.
    _hook(tmp_path, {"session_id": "a1b2c3d4-0000-1111-2222-333344445555",
                     "hook_event_name": "Notification", "message": "again"},
          now=datetime.fromtimestamp(NOW + 100, timezone.utc))
    third = session_watch.scan(tmp_path, 120, notify=True, now=NOW + 120)
    assert [b["new"] for b in third] == [True] and len(notified_calls) == 2


def test_notified_state_is_pruned_after_7_days(tmp_path, notified_calls):
    state = tmp_path / "session-watch" / "notified.json"
    notify_hook.atomic_write_json(state, {"gone|hook|x": NOW - 8 * 86400, "kept|hook|y": NOW - 86400})
    session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    assert json.loads(state.read_text(encoding="utf-8")) == {"kept|hook|y": NOW - 86400}


def test_no_notify_writes_nothing(tmp_path, notified_calls):
    _transcript(tmp_path, [_tool_use("t")], age=300)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    for _ in range(2):
        assert [b["new"] for b in session_watch.scan(tmp_path, 120, notify=False, now=NOW)] == [True]
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert notified_calls == []


def test_notification_text_stays_out_of_info_logs(tmp_path, notified_calls, caplog):
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification",
                     "message": "PRIVATE-PROMPT-TEXT"})
    with caplog.at_level(logging.INFO, logger="session_watch"):
        session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("s" in m and "hook" in m for m in info)
    assert not any("PRIVATE-PROMPT-TEXT" in m for m in info)
    assert "PRIVATE-PROMPT-TEXT" not in (tmp_path / "session-watch/watch.log").read_text(encoding="utf-8")


# --------------------------------------------------------------- notifications

@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_notifier_is_an_argument_list_with_no_untrusted_text_in_a_script(
        monkeypatch, notified_calls, platform):
    monkeypatch.setattr(session_watch.sys, "platform", platform)
    assert session_watch.desktop_notify("Claude Code needs you", INJECTION)
    [(cmd, kwargs)] = notified_calls
    assert isinstance(cmd, list) and all(isinstance(a, str) for a in cmd)
    assert not kwargs.get("shell")
    if platform == "darwin":
        assert cmd[0] == "osascript" and cmd[-1] == INJECTION
        assert all(INJECTION not in a for a in cmd[:-1])  # only ever an argv item
    elif platform == "win32":
        assert cmd[0] == "powershell.exe" and INJECTION not in " ".join(cmd)
        assert cmd[-1] == session_watch.WINDOWS_TOAST
        assert kwargs["env"]["SESSION_WATCH_BODY"] == INJECTION
        assert kwargs["env"]["SESSION_WATCH_TITLE"] == "Claude Code needs you"
    else:
        assert cmd == ["notify-send", "--", "Claude Code needs you", INJECTION]


def test_missing_notifier_is_logged_and_the_scan_goes_on(tmp_path, monkeypatch, caplog):
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(session_watch.subprocess, "run", missing)
    _transcript(tmp_path, [_tool_use("t")], age=300)
    with caplog.at_level(logging.WARNING, logger="session_watch"):
        assert len(session_watch.scan(tmp_path, 120, notify=True, now=NOW)) == 1
    assert "notification failed" in caplog.text
    assert (tmp_path / "session-watch/watch.log").exists()


# ------------------------------------------------------------------------ CLI

def test_json_output_is_stable(tmp_path, monkeypatch, capsys, notified_calls):
    monkeypatch.setattr(session_watch.time, "time", lambda: NOW)
    assert session_watch.main(["--once", "--json", "--no-notify",
                               "--claude-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "[]\n"
    _transcript(tmp_path, [_tool_use("t")], age=300)
    assert session_watch.main(["--json", "--no-notify", "--claude-dir", str(tmp_path)]) == 0
    [item] = json.loads(capsys.readouterr().out)
    assert list(item) == sorted(["session_id", "project", "source", "since", "waiting_seconds",
                                 "reason", "tool", "notification_type", "message", "new"])
    assert item["since"] == "2027-01-15T07:55:00Z" and item["new"] is True


def test_claude_config_dir_env_is_the_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(session_watch.time, "time", lambda: NOW)
    _transcript(tmp_path, [_tool_use("t")], age=300)
    session_watch.main(["--json", "--no-notify"])
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_cli_runs_as_a_script(tmp_path):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(WATCH_DIR / "session_watch.py"), "--json",
                        "--no-notify"], env=env, capture_output=True, timeout=60)
    assert (r.returncode, r.stdout.strip()) == (0, b"[]")


# ------------------------------------------------------------- never touches

def _string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and id(n) not in docstrings]


def test_no_code_path_names_settings_json():
    for name in ("notify_hook.py", "session_watch.py"):
        assert not [s for s in _string_constants(WATCH_DIR / name) if "settings" in s.lower()]
    for name in ("run-codex-watch.sh", "run-codex-watch.ps1"):
        assert "settings" not in (WATCH_DIR / name).read_text(encoding="utf-8").lower()


def test_a_full_run_writes_only_its_own_folder(tmp_path, notified_calls):
    settings = tmp_path / "settings.json"
    settings.write_text('{"permissions": {"allow": []}}')
    os.utime(settings, (NOW - 5000, NOW - 5000))
    _transcript(tmp_path, [_tool_use("t")], age=300)
    (tmp_path / "settings.local.json").write_text("{}")
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification", "message": "m"})
    session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    session_watch.scan(tmp_path, 120, notify=True, now=NOW + 999)
    _hook(tmp_path, {"session_id": "s", "hook_event_name": "Stop"})
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert {p: m for p, m in after.items() if p in before} == before
    assert all((tmp_path / "session-watch") in p.parents for p in set(after) - set(before))
    assert settings.read_text() == '{"permissions": {"allow": []}}'


# --------------------------------------------------------- launchers and docs

def test_sh_launcher_is_lf_and_executable():
    path = WATCH_DIR / "run-codex-watch.sh"
    data = path.read_bytes()
    assert b"\r" not in data and data.startswith(b"#!/usr/bin/env bash\n")
    staged = subprocess.run(["git", "ls-files", "-s", "--", str(path)], cwd=ROOT,
                            capture_output=True, text=True).stdout
    if staged:
        assert staged.startswith("100755 "), staged
    elif os.name != "nt":
        assert os.access(path, os.X_OK)
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in attributes


def test_launchers_use_the_read_only_codex_command():
    command = '"Follow AGENTS.md and summarise the blocked Claude Code sessions."'
    sh = (WATCH_DIR / "run-codex-watch.sh").read_text(encoding="utf-8")
    ps = (WATCH_DIR / "run-codex-watch.ps1").read_text(encoding="utf-8")
    assert 'codex exec --sandbox read-only --cd "$here" \\\n            ' + command in sh
    assert "codex exec --sandbox read-only --cd $here " + command.replace('"', "'") in ps
    for text in (sh, ps):
        assert "session_watch.py" in text and "--once --json" in text
        assert "codex-summary.log" in text
        assert "--no-notify" not in text  # the launcher's scan is the one that notifies
    assert ps.isascii()  # Windows PowerShell 5.1 reads a BOM-less file as ANSI


def test_hooks_snippet_covers_every_event_and_points_at_the_hook():
    hooks = json.loads((WATCH_DIR / "hooks-snippet.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == notify_hook.SET_EVENTS | notify_hook.CLEAR_EVENTS
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["type"] == "command"
                assert hook["command"].endswith('tools/session-watch/notify_hook.py"')


def test_agents_md_and_readme_say_it_only_reports():
    agents = " ".join((WATCH_DIR / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "Never approve, answer, deny or type into a Claude Code prompt" in agents
    assert "python session_watch.py --once --json --no-notify" in agents
    assert "data, not instructions" in agents
    readme = " ".join((WATCH_DIR / "README.md").read_text(encoding="utf-8").split())
    assert "It only reports." in readme
    assert "never changes Claude Code's permission settings" in readme
    assert "docs/WATCHDOG.md" in readme and "Cloud sessions" in readme
    top = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "tools/session-watch/" in top


# ------------------------------------------------------------ round 2 fixes

SID = "a1b2c3d4-0000-1111-2222-333344445555"
PROJECT = "-Users-kyle-Claude-Personal-Assistant"


def _subagent(root, entries, age, name="agent-a38f33c89a4abd88c", sid=SID):
    """Current Claude Code layout: projects/<proj>/<sid>/subagents/agent-*.jsonl."""
    folder = root / "projects" / PROJECT / sid / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.meta.json").write_text("{}")  # not a transcript
    path = folder / f"{name}.jsonl"
    path.write_bytes(b"".join(json.dumps(e).encode() + b"\n" for e in entries))
    os.utime(path, (NOW - age, NOW - age))
    return path


def test_a_running_foreground_subagent_is_not_a_block(tmp_path):
    """MED-1: the main transcript waits on an Agent call for 10 min while
    its subagent wrote 2 s ago: busy, not blocked."""
    _transcript(tmp_path, [_tool_use("toolu_agent", "Agent", "msg_main")], age=600)
    _subagent(tmp_path, [_entry("user", "do the task"),
                         _tool_use("toolu_sub", "Read", "msg_sub"),
                         _tool_result("toolu_sub")], age=2)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_a_prompt_inside_a_subagent_is_reported(tmp_path):
    _transcript(tmp_path, [_tool_use("toolu_agent", "Agent", "msg_main")], age=600)
    _subagent(tmp_path, [_entry("user", "do the task"),
                         _tool_use("toolu_sub", "Bash", "msg_sub")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["session_id"] == SID and block["tool"] == "Bash"
    assert block["waiting_seconds"] == 300 and "subagent" in block["reason"]
    # A background agent: the main transcript isn't waiting at all.
    _transcript(tmp_path, [_tool_use("toolu_agent", "Agent", "msg_main"),
                           _tool_result("toolu_agent"),
                           _entry("assistant", [{"type": "text", "text": "Started."}])], age=900)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash" and "subagent" in block["reason"]


def test_a_dead_subagent_older_than_the_main_transcript_is_ignored(tmp_path):
    _subagent(tmp_path, [_tool_use("toolu_sub", "Bash", "msg_sub")], age=3000)
    _transcript(tmp_path, [_tool_use("t", msg_id="m"), _tool_result("t"),
                           _entry("assistant", [{"type": "text", "text": "Done."}])], age=600)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_parallel_calls_in_the_real_one_block_per_entry_shape(tmp_path):
    """LOW-7: Claude Code stores each parallel call as its own entry with a
    shared message.id. The first call waiting for approval while the last
    one finished is still a block."""
    entries = [
        _real("msg_1", {"type": "text", "text": "Two things at once."}),
        _real("msg_1", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}),
        _real("msg_1", {"type": "tool_use", "id": "t2", "name": "Read", "input": {}}),
        _tool_result("t2"),
    ]
    _transcript(tmp_path, entries, age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash"
    _transcript(tmp_path, entries + [_tool_result("t1")], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    # A later message starts a new group: the old ids don't linger.
    _transcript(tmp_path, entries + [_tool_result("t1"),
                                     _real("msg_2", {"type": "tool_use", "id": "t3",
                                                     "name": "Edit", "input": {}})], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Edit"


def test_one_malformed_transcript_does_not_stop_the_scan(tmp_path, notified_calls):
    """MED-2: an unhashable tool_use_id or a deeply nested line used to
    raise out of the whole scan."""
    bad = _entry("user", [{"type": "tool_result", "tool_use_id": ["x"], "content": "c"}])
    _transcript(tmp_path, [_tool_use("t1"), bad], age=300, sid="aaa-unhashable")
    _transcript(tmp_path, [_tool_use("t1")], age=300, sid="bbb-nested",
                extra=b"[" * 30_000 + b"\n")  # inside the 64 KB tail
    _transcript(tmp_path, [_tool_use("t1")], age=300, sid="ccc-fine")
    found = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert {b["session_id"] for b in found} == {"aaa-unhashable", "bbb-nested", "ccc-fine"}
    folder = tmp_path / "session-watch" / "blocked"
    folder.mkdir(parents=True)
    (folder / "deep.json").write_text("[" * 30_000)  # RecursionError, not ValueError
    _hook(tmp_path, {"session_id": "okay", "hook_event_name": "Notification"})
    assert [b["session_id"] for b in session_watch.read_markers(tmp_path, NOW)] == ["okay"]
    assert len(session_watch.scan(tmp_path, 120, notify=True, now=NOW)) == 4


def test_a_transcript_error_is_logged_at_debug_only(tmp_path, monkeypatch, caplog):
    def boom(lines):
        raise TypeError("PRIVATE-LINE-TEXT")

    monkeypatch.setattr(session_watch, "pending_tool", boom)
    _transcript(tmp_path, [_tool_use("t1")], age=300)
    with caplog.at_level(logging.DEBUG, logger="session_watch"):
        assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert "TypeError" in caplog.text and "PRIVATE-LINE-TEXT" not in caplog.text


def test_interval_mode_survives_a_failed_scan(tmp_path, monkeypatch, capsys, caplog):
    calls, sleeps = [], []

    def flaky_scan(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk hiccup")
        return []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(session_watch, "scan", flaky_scan)
    monkeypatch.setattr(session_watch.time, "sleep", fake_sleep)
    with caplog.at_level(logging.WARNING, logger="session_watch"):
        assert session_watch.main(["--interval", "5", "--json",
                                   "--claude-dir", str(tmp_path)]) == 0
    assert len(calls) == 2 and capsys.readouterr().out == "[]\n"
    assert "scan failed (RuntimeError)" in caplog.text


def test_bad_options_exit_2(tmp_path):
    for bad in (["--interval", "0"], ["--threshold", "-1"], ["--nope"]):
        with pytest.raises(SystemExit) as e:
            session_watch.parse_args(bad)
        assert e.value.code == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; Windows uses the profile's ACLs")
def test_state_folder_and_files_are_private(tmp_path, notified_calls):
    """LOW-3: mkdir(parents=True, mode=...) only gave the last folder 0700
    and watch.log was opened with the umask's 0644."""
    old = os.umask(0o022)
    try:
        _transcript(tmp_path, [_tool_use("t")], age=300)
        _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification"})
        session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    finally:
        os.umask(old)
    state = tmp_path / "session-watch"
    for folder in (state, state / "blocked"):
        assert folder.stat().st_mode & 0o777 == 0o700, folder
    for name in ("watch.log", "notified.json", "blocked/s.json"):
        assert (state / name).stat().st_mode & 0o777 == 0o600, name


@pytest.mark.parametrize("kind", ["auth_success", "something_new"])
def test_other_notification_types_are_not_blocks(tmp_path, kind):
    """LOW-4: auth_success used to write a marker and toast "waiting"."""
    assert _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification",
                            "notification_type": kind, "message": "Signed in"}) is None
    assert not (tmp_path / "session-watch").exists()


@pytest.mark.parametrize("kind,word", [("permission_prompt", "approval"),
                                       ("idle_prompt", "idle"),
                                       ("elicitation_dialog", "question")])
def test_notification_type_reaches_the_json_and_the_notification(
        tmp_path, notified_calls, monkeypatch, kind, word):
    monkeypatch.setattr(session_watch.sys, "platform", "linux")
    assert _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification",
                            "notification_type": kind, "message": "m"}) == "set"
    [block] = session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    assert session_watch.public([block])[0]["notification_type"] == kind
    assert f"{word})" in notified_calls[0][0][-1]
    assert block["reason"] == session_watch.REASONS[kind]


def test_a_marker_without_a_type_still_counts(tmp_path):
    assert _hook(tmp_path, {"session_id": "s", "hook_event_name": "Notification"}) == "set"
    [block] = session_watch.read_markers(tmp_path, NOW)
    assert block["notification_type"] is None
    folder = tmp_path / "session-watch" / "blocked"
    data = json.loads((folder / "s.json").read_text(encoding="utf-8"))
    data["notification_type"] = "made_up"  # an edited marker shows no type
    (folder / "s.json").write_text(json.dumps(data))
    assert session_watch.read_markers(tmp_path, NOW)[0]["notification_type"] is None


def test_hooks_snippet_quotes_the_path_and_filters_notifications():
    """LOW-4, LOW-5, LOW-8."""
    hooks = json.loads((WATCH_DIR / "hooks-snippet.json").read_text(encoding="utf-8"))["hooks"]
    assert "PostToolUseFailure" in hooks and "PostToolUseFailure" in notify_hook.CLEAR_EVENTS
    [notification] = hooks["Notification"]
    assert notification["matcher"] == "|".join(notify_hook.BLOCK_TYPES)
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"] == (
                    'python3 "/ABSOLUTE/PATH/TO/tools/session-watch/notify_hook.py"')
    readme = (WATCH_DIR / "README.md").read_text(encoding="utf-8")
    assert '"command": "python \\"C:/Users/you/' in readme
    assert '-File \\"C:\\Users\\you\\' in readme and 'run-codex-watch.ps1\\""' in readme


def test_notified_state_is_capped(tmp_path, monkeypatch):
    """LOW-6: MAX_NOTIFIED keeps only the newest entries."""
    monkeypatch.setattr(session_watch, "MAX_NOTIFIED", 3)
    session_watch.save_notified(tmp_path, {f"k{i}": NOW - i for i in range(10)}, NOW)
    assert session_watch.load_notified(tmp_path) == {"k0": NOW, "k1": NOW - 1, "k2": NOW - 2}


def test_oversized_markers_are_skipped(tmp_path):
    """LOW-6: MAX_MARKER_BYTES."""
    _hook(tmp_path, {"session_id": "big", "hook_event_name": "Notification"})
    _hook(tmp_path, {"session_id": "small", "hook_event_name": "Notification"})
    big = tmp_path / "session-watch" / "blocked" / "big.json"
    big.write_text(big.read_text(encoding="utf-8") + " " * session_watch.MAX_MARKER_BYTES)
    assert [b["session_id"] for b in session_watch.read_markers(tmp_path, NOW)] == ["small"]


def test_hook_stdin_is_drained_but_only_the_head_kept(monkeypatch):
    """LOW-6: MAX_STDIN."""
    data = b'{"session_id": "s"}' + b"z" * 300_000
    raw = io.BytesIO(data)
    monkeypatch.setattr(notify_hook, "MAX_STDIN", 1000)
    monkeypatch.setattr(notify_hook.sys, "stdin", io.TextIOWrapper(raw))
    kept = notify_hook._read_stdin()
    assert kept == data[:1000] and raw.tell() == len(data)


def test_watch_log_rotates(tmp_path, monkeypatch):
    """LOW-6: watch.log moves to watch.log.1 past MAX_LOG_BYTES."""
    monkeypatch.setattr(session_watch, "MAX_LOG_BYTES", 200)
    block = {"session_id": "s", "project": "p", "waiting_seconds": 5, "source": "hook"}
    for _ in range(4):
        session_watch.append_log(tmp_path, block, NOW)
    log_path = tmp_path / "session-watch" / "watch.log"
    assert log_path.stat().st_size <= 200 + 120
    assert (tmp_path / "session-watch" / "watch.log.1").exists()


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="needs bash")
def test_sh_launcher_rotates_the_codex_summary_and_keeps_it_private(tmp_path):
    """LOW-3, LOW-6: the codex-summary.log rotation and umask live in the
    launcher, so it is run with fake python3, codex and sleep on PATH."""
    fake = tmp_path / "bin"
    fake.mkdir()
    scripts = {"python3": "#!/bin/sh\necho '[{\"new\": true}]'\n",
               "codex": "#!/bin/sh\necho summary-from-codex\n",
               "sleep": "#!/bin/sh\nkill -TERM $PPID\n"}
    for name, body in scripts.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    claude = tmp_path / "claude"
    state = claude / "session-watch"
    state.mkdir(parents=True)
    summary = state / "codex-summary.log"
    summary.write_bytes(b"x" * (1024 * 1024 + 1))
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}",
               CLAUDE_CONFIG_DIR=str(claude))
    subprocess.run(["bash", str(WATCH_DIR / "run-codex-watch.sh"), "1"], env=env,
                   capture_output=True, timeout=60)
    assert (state / "codex-summary.log.1").stat().st_size == 1024 * 1024 + 1
    assert "summary-from-codex" in summary.read_text(encoding="utf-8")
    assert summary.stat().st_mode & 0o777 == 0o600
    assert state.stat().st_mode & 0o777 == 0o700


def test_docs_name_exactly_the_json_keys_codex_gets():
    """LOW-9: the README, AGENTS.md and SECURITY.md describe the --json
    output, which has no working-folder path, and no longer claim the
    command always exits 0."""
    keys = session_watch.public([{k: None for k in (
        "session_id", "project", "source", "since", "waiting_seconds", "reason", "tool",
        "notification_type", "message", "new", "key")}])[0]
    assert "cwd" not in keys and "key" not in keys
    readme = " ".join((WATCH_DIR / "README.md").read_text(encoding="utf-8").split())
    agents = " ".join((WATCH_DIR / "AGENTS.md").read_text(encoding="utf-8").split())
    for key in keys:
        assert f"`{key}`" in readme and f"`{key}`" in agents, key
    assert "`cwd`" not in readme and "`cwd`" not in agents
    assert "always exits 0. " not in readme and "A bad option exits 2" in readme
    assert "never gets transcripts or the working-folder path" in readme.replace("It never", "never")
    security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
    assert "no working-folder path" in security
    assert "on Windows they get the user profile's permissions" in security


# ------------------------------------------------------------ round 3 fixes

def test_a_busy_background_agent_does_not_hide_a_main_prompt(tmp_path):
    """MED-1 (round 3): the main session launched a background agent (its
    result came back at once), then a Bash call waits 10 min for approval
    while the agent wrote 2 s ago. The prompt is reported on the main
    transcript's own quiet time."""
    _transcript(tmp_path, [_tool_use("ag", "Agent", "m1"), _tool_result("ag"),
                           _tool_use("b1", "Bash", "m2")], age=600)
    _subagent(tmp_path, [_entry("user", "task"), _tool_use("s1", "Read", "ms"),
                         _tool_result("s1")], age=2)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash" and block["waiting_seconds"] == 600
    assert "subagent" not in block["reason"]
    # The old name of the Agent tool is masked like Agent.
    _transcript(tmp_path, [_tool_use("ag", "Task", "m1")], age=600)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_a_quiet_subagent_prompt_is_reported_while_a_sibling_works(tmp_path):
    _transcript(tmp_path, [_tool_use("ag", "Agent", "m1")], age=600)
    _subagent(tmp_path, [_tool_use("s1", "Read", "ms"), _tool_result("s1")],
              age=2, name="agent-busy")
    _subagent(tmp_path, [_tool_use("s2", "Bash", "mt")], age=300, name="agent-stuck")
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Bash" and "subagent" in block["reason"]
    assert block["waiting_seconds"] == 300


def test_at_most_max_subagents_are_read(tmp_path, monkeypatch):
    """LOW-3 (round 3): the subagent cap is a real bound."""
    _transcript(tmp_path, [_tool_use("ag", "Agent", "m1")], age=900)
    for i in range(session_watch.MAX_SUBAGENTS + 12):
        _subagent(tmp_path, [_tool_use(f"s{i}", "Read", f"m{i}"), _tool_result(f"s{i}")],
                  age=300 + i, name=f"agent-{i:02d}")
    reads = []
    real = session_watch._tail_lines
    monkeypatch.setattr(session_watch, "_tail_lines",
                        lambda path, *a: reads.append(path) or real(path, *a))
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["tool"] == "Agent"  # every subagent idle: the Agent call is stuck
    subs = [p for p in reads if p.parent.name == "subagents"]
    assert len(subs) == session_watch.MAX_SUBAGENTS
    assert {p.stem for p in subs} == {f"agent-{i:02d}"
                                      for i in range(session_watch.MAX_SUBAGENTS)}


def test_agents_md_lets_codex_read_only_the_command_output():
    """LOW-2 (round 3): markers hold cwd and transcript_path, so Codex may
    not read any file, the watcher's own folder included."""
    agents = " ".join((WATCH_DIR / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "never read any file yourself" in agents
    assert "The output of the command above is the only thing you read" in agents
    assert "not even `session-watch/`" in agents
    assert "files under `~/.claude/session-watch/`." not in agents


def test_an_unwritable_state_folder_exits_3_without_a_traceback_or_storm(
        tmp_path, notified_calls, monkeypatch, capsys, caplog):
    """LOW-4 (round 3): `session-watch` is a file. --once used to raise
    NotADirectoryError after notifying and never saved notified.json, so
    every scan notified again."""
    monkeypatch.setattr(session_watch.time, "time", lambda: NOW)
    _transcript(tmp_path, [_tool_use("t")], age=300)
    (tmp_path / "session-watch").write_text("not a folder")
    for _ in range(2):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="session_watch"):
            assert session_watch.main(["--once", "--json", "--claude-dir", str(tmp_path)]) == 3
        assert [b["tool"] for b in json.loads(capsys.readouterr().out)] == ["Bash"]
        [record] = caplog.records
        assert record.levelno == logging.WARNING
        assert "can't save state" in record.getMessage()
        assert "NotADirectoryError" in record.getMessage()
    assert notified_calls == []  # nothing notified that couldn't be saved


def test_an_unwritable_state_folder_from_the_command_line(tmp_path):
    """The real CLI: one stderr line, no traceback, exit 3."""
    _transcript(tmp_path, [_tool_use("t")], age=300)
    path = next((tmp_path / "projects").rglob("*.jsonl"))
    old = time.time() - 300
    os.utime(path, (old, old))
    (tmp_path / "session-watch").write_text("not a folder")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    env["PATH"] = ""  # no notifier either way
    r = subprocess.run([sys.executable, str(WATCH_DIR / "session_watch.py"), "--once",
                        "--claude-dir", str(tmp_path)], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 3, r.stderr
    assert "Traceback" not in r.stderr and len(r.stderr.strip().splitlines()) == 1
    assert "can't save state" in r.stderr and "secret-command" not in r.stderr
    assert "Bash is waiting" in r.stdout


def test_a_watch_log_failure_notifies_once_and_exits_3(
        tmp_path, notified_calls, monkeypatch, capsys):
    monkeypatch.setattr(session_watch.time, "time", lambda: NOW)
    _transcript(tmp_path, [_tool_use("t")], age=300)

    def broken(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(session_watch, "append_log", broken)
    with pytest.raises(session_watch.StateError) as e:
        session_watch.scan(tmp_path, 120, notify=True, now=NOW)
    assert [b["new"] for b in e.value.blocked] == [True] and len(notified_calls) == 1
    # notified.json was saved first: the next scans don't notify again.
    assert session_watch.main(["--once", "--claude-dir", str(tmp_path)]) == 0
    assert "Bash is waiting" in capsys.readouterr().out and len(notified_calls) == 1


# ------------------------------------------------------- security-check stops

CLASSIFIER = ("Permission for this action was denied by the Claude Code auto mode "
              "classifier. Reason: DENIED-COMMAND-TEXT would delete files.")
RULE = "Permission to use Bash with command DENIED-COMMAND-TEXT has been denied."
HOOK = "PreToolUse:Bash hook error: [guard.sh]: Blocked by policy: DENIED-COMMAND-TEXT"
REJECTED = ("The user doesn't want to proceed with this tool use. The tool use was "
            "rejected (eg. if it was a file edit, the new_string was NOT written to the "
            "file). STOP what you are doing and wait for the user to tell you how to proceed.")


def _error(tool_id, content):
    return _entry("user", [{"type": "tool_result", "tool_use_id": tool_id,
                            "content": content, "is_error": True}])


def _text(text, msg_id="m_end"):
    return _entry("assistant", [{"type": "text", "text": text}], msg_id)


@pytest.mark.parametrize("content", [
    CLASSIFIER, RULE, HOOK, [{"type": "text", "text": CLASSIFIER}],
    "\n  PERMISSION to use WebFetch has been DENIED.", "Hook PreToolUse:Bash denied this tool",
    "Permission to use Bash with command " + "DENIED-COMMAND-TEXT " * 60 + "has been denied."])
def test_a_session_stopped_by_a_security_check_is_reported(tmp_path, content):
    _transcript(tmp_path, [_entry("user", "clean up"), _tool_use("d1", "Bash", "m1"),
                           _error("d1", content), _text("I can't do that here.")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["notification_type"] == "security_denied" and block["tool"] == "Bash"
    assert block["reason"] == "stopped after a security check denied a tool call"
    assert block["waiting_seconds"] == 300 and block["source"] == "transcript"
    assert block["key"] == f"{SID}|denied|d1"
    assert "DENIED-COMMAND-TEXT" not in json.dumps(block)
    # Too recent: the session may still be working on it.
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", content)], age=30)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


@pytest.mark.parametrize("after", [
    [_tool_use("ok", "Read", "m2"), _tool_result("ok")],  # carried on, it worked
    [_entry("user", "fine, skip that")],                    # Kyle answered
    [_entry("user", [{"type": "text", "text": "try another way"}])],
])
def test_a_denial_followed_by_progress_is_not_reported(tmp_path, after):
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", CLASSIFIER),
                           *after, _text("Done.")], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_a_later_failure_or_meta_entry_does_not_clear_a_denial(tmp_path):
    meta = _entry("user", "Caveat: local command output")
    meta["isMeta"] = True
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", RULE),
                           _tool_use("f1", "Read", "m2"), _error("f1", "File does not exist."),
                           meta, _text("Stopping.")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["notification_type"] == "security_denied" and block["tool"] == "Bash"


@pytest.mark.parametrize("content", [
    REJECTED, "user rejected the tool call",
    # Kyle's reason quoted in his own rejection can sound like a check.
    "User rejected the call: permission to use that folder is denied, use /tmp.",
    [{"type": "text", "text": REJECTED + " The user said: blocked by my hook rules."}]])
def test_kyles_own_rejection_is_not_a_security_stop(tmp_path, content):
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", content)], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_a_plain_error_or_matching_text_without_is_error_is_not_a_denial(tmp_path):
    ok = _entry("user", [{"type": "tool_result", "tool_use_id": "d1", "content": CLASSIFIER}])
    _transcript(tmp_path, [_tool_use("d1", "Read", "m1"), ok], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"),
                           _error("d1", "Exit code 1: no such file")], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


@pytest.mark.parametrize("content", [
    # Ordinary failing commands whose output only sounds like a denial.
    "Exit code 1\nhusky > pre-commit (node v20)\ncommit blocked by pre-commit hook: lint errors",
    "Exit code 128\nremote: Permission to user-tools/repo.git denied to bot.\n"
    "fatal: unable to access",
    "remote: Permission to user-tools/repo.git denied to bot.",
    "You do not have permission to use model X. Request denied.",
    "Exit code 1\nError: You do not have permission to use model gpt-x. Request denied.",
    "Exit code 1\n" + CLASSIFIER, "Exit code 2\n" + HOOK,
    "Permission to user-x has been denied.", "commit blocked by the hook"])
def test_failing_command_output_that_mentions_a_denial_is_not_one(tmp_path, content):
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", content),
                           _text("The commit failed.")], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []


def test_denial_patterns_are_anchored_regexes_in_one_constant():
    assert session_watch.DENIAL_PATTERNS and all(
        callable(getattr(p, "match", None)) for p in session_watch.DENIAL_PATTERNS)
    for text in ("  Permission for this action was denied by the Claude Code auto mode "
                 "classifier.", "PreToolUse:Bash hook error: blocked"):
        assert session_watch.is_denial({"is_error": True, "content": text})
        assert not session_watch.is_denial({"is_error": True, "content": "x " + text})


def _idle_marker(root, at):
    assert _hook(root, {"session_id": SID, "hook_event_name": "Notification",
                        "notification_type": "idle_prompt", "cwd": "/x/proj",
                        "message": "Claude is waiting for your input"},
                 datetime.fromtimestamp(at, timezone.utc)) == "set"


def test_a_denial_beats_the_idle_marker_that_follows_it(tmp_path):
    # The turn ends on the denial, Stop clears any marker, and a minute
    # later Claude Code's idle_prompt writes one before --threshold passes.
    _transcript(tmp_path, [_entry("user", "clean up"), _tool_use("d1", "Bash", "m1"),
                           _error("d1", RULE), _text("I can't do that here.")], age=300)
    _hook(tmp_path, {"session_id": SID, "hook_event_name": "Stop"})
    _idle_marker(tmp_path, NOW - 240)
    [block] = session_watch.scan(tmp_path, 120, notify=False, now=NOW)
    assert block["source"] == "transcript" and block["notification_type"] == "security_denied"
    assert block["tool"] == "Bash" and block["key"] == f"{SID}|denied|d1"
    # The idle marker alone (no denial) still reports idle.
    _transcript(tmp_path, [_entry("user", "hi"), _text("Hello.")], age=300)
    [block] = session_watch.scan(tmp_path, 120, notify=False, now=NOW)
    assert block["source"] == "hook" and block["notification_type"] == "idle_prompt"


@pytest.mark.parametrize("kind", ["permission_prompt", "elicitation_dialog"])
def test_an_approval_or_question_marker_still_beats_a_denial(tmp_path, kind):
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", RULE),
                           _text("Stopping.")], age=300)
    assert _hook(tmp_path, {"session_id": SID, "hook_event_name": "Notification",
                            "notification_type": kind, "cwd": "/x/proj"},
                 datetime.fromtimestamp(NOW - 200, timezone.utc)) == "set"
    [block] = session_watch.scan(tmp_path, 120, notify=False, now=NOW)
    assert block["source"] == "hook" and block["notification_type"] == kind


def test_merge_only_lets_a_denial_beat_an_idle_marker():
    def b(sid, source, kind):
        return {"session_id": sid, "source": source, "notification_type": kind,
                "waiting_seconds": 1}
    hook = [b("a", "hook", "idle_prompt"), b("b", "hook", "idle_prompt"),
            b("c", "hook", None), b("d", "hook", "permission_prompt")]
    heuristic = [b("a", "transcript", "security_denied"), b("b", "transcript", None),
                 b("c", "transcript", "security_denied"),
                 b("d", "transcript", "security_denied")]
    got = {x["session_id"]: x["source"] for x in session_watch.merge(hook, heuristic)}
    assert got == {"a": "transcript", "b": "hook", "c": "hook", "d": "hook"}


def test_a_denial_inside_a_subagent_is_reported(tmp_path):
    _transcript(tmp_path, [_tool_use("ag", "Agent", "m1")], age=600)
    _subagent(tmp_path, [_entry("user", "do the task"), _tool_use("d1", "WebFetch", "ms"),
                         _error("d1", HOOK), _text("Blocked; reporting back.")], age=300)
    [block] = session_watch.scan_transcripts(tmp_path, 120, NOW)
    assert block["notification_type"] == "security_denied" and block["tool"] == "WebFetch"
    assert block["reason"].endswith("in a subagent") and block["waiting_seconds"] == 300


def test_denial_text_never_reaches_json_log_or_notification(
        tmp_path, notified_calls, capsys, caplog, monkeypatch):
    monkeypatch.setattr(session_watch.time, "time", lambda: NOW)
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), _error("d1", CLASSIFIER)], age=300)
    with caplog.at_level(logging.DEBUG, logger="session_watch"):
        assert session_watch.main(["--once", "--json", "--claude-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    [item] = json.loads(out)
    assert item["notification_type"] == "security_denied" and item["message"] is None
    [(cmd, kwargs)] = notified_calls
    body = cmd[-1]
    assert "Bash was denied by a security check" in body and "security check)" in body
    log_text = (tmp_path / "session-watch" / "watch.log").read_text(encoding="utf-8")
    for text in (out, body, log_text, caplog.text, json.dumps(cmd)):
        assert "DENIED-COMMAND-TEXT" not in text and "classifier" not in text.lower()
    # Announced once per denied call.
    assert session_watch.main(["--once", "--claude-dir", str(tmp_path)]) == 0
    assert len(notified_calls) == 1


@pytest.mark.parametrize("content", [None, 7, {"text": CLASSIFIER}, [CLASSIFIER, 3, None],
                                     [{"type": "text", "text": ["x"]}], "x" * 100_000])
def test_malformed_denial_shapes_do_not_crash(tmp_path, content):
    weird = _entry("user", [{"type": "tool_result", "tool_use_id": "d1",
                             "content": content, "is_error": True},
                            {"type": "tool_result", "tool_use_id": ["x"], "is_error": True},
                            "not a block"])
    _transcript(tmp_path, [_tool_use("d1", "Bash", "m1"), weird,
                           {"type": "user", "message": "flat"}, {"type": "user"}], age=300)
    assert session_watch.scan_transcripts(tmp_path, 120, NOW) == []
    assert session_watch.denied_tool([b"[", b"null", b"{}"]) is None


def test_docs_describe_security_stops_and_exit_3():
    readme = " ".join((WATCH_DIR / "README.md").read_text(encoding="utf-8").split())
    agents = " ".join((WATCH_DIR / "AGENTS.md").read_text(encoding="utf-8").split())
    security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
    manual = " ".join((ROOT / "MANUAL_TEST.md").read_text(encoding="utf-8").split())
    for doc in (readme, agents, security):
        assert "`security_denied`" in doc
    assert "only the transcript check sees it" in readme
    assert "a `security_denied` block beats an `idle_prompt` marker" in readme
    for doc in (readme, security):
        assert "idle_prompt" in doc and "starts with" in doc
    assert "never suggest a way around the check" in agents
    assert "exits 3" in readme and "A scan exits 0; a file it can't read" not in readme
    assert "was denied by a security check" in manual
