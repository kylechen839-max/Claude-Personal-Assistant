"""Dev team coordinator link: sending through the Claude Code CLI (mocked)
and reading replies from the Notion inbox (mocked)."""

import json
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from assistant.config import DEFAULTS
from assistant.devteam import (INBOX_TITLE, CoordinatorLink, DevTeamError,
                               session_id)
from assistant.tools import ToolRegistry
from tests.devteam_fakes import SID, FakeNotion, FakeRun, ok_run, row


def link(tmp_path, run=None, notion=None, **kw):
    return CoordinatorLink(SID, run=run or ok_run(), which=lambda c: f"/usr/bin/{c}",
                           state_file=tmp_path / "devteam.json",
                           notion_client=notion or FakeNotion(), **kw)


@pytest.mark.parametrize("value, expected", [
    (SID, SID),
    ("cse_abc123", "cse_abc123"),
    (f"https://claude.ai/code/{SID}?from=cli&m=0", SID),
    (f"claude.ai/code/{SID}", SID),
    ("https://evil.example/code/session_x", ""),
    ("session_01; rm -rf /", ""),
    ("", ""),
])
def test_session_id(value, expected):
    assert session_id(value) == expected


def test_send_runs_cli_with_message_on_stdin(tmp_path):
    run = ok_run()
    out = link(tmp_path, run=run, repo_dir="/repo").send("build a voice speed setting")
    args, kw = run.calls[0]
    assert args == ["/usr/bin/claude", "-p", "--cloud", SID, "--output-format", "json"]
    assert "build a voice speed setting" in kw["input"]  # never on the command line
    assert out["reply_id"] in kw["input"] and INBOX_TITLE in kw["input"]
    assert kw["cwd"] == "/repo" and kw["timeout"] == 60
    assert out["status"] == "sent" and out["session_url"].endswith(SID)
    saved = json.loads((tmp_path / "devteam.json").read_text())
    assert list(saved["pending"]) == [out["reply_id"]]


@pytest.mark.parametrize("run, why", [
    (FakeRun(stdout=json.dumps({"ok": False, "session_id": SID,
                                "error": "cloud session is archived"}), returncode=1),
     "archived"),
    (FakeRun(stderr="Error: Cloud sessions need a claude.ai sign-in.\n", returncode=1),
     "sign-in"),
    (FakeRun(exc=subprocess.TimeoutExpired("claude", 60)), "in time"),
])
def test_send_failures_are_plain_and_not_pending(tmp_path, run, why):
    team = link(tmp_path, run=run)
    with pytest.raises(DevTeamError, match=why):
        team.send("hello")
    assert team.pending() == []


def test_send_needs_cli_and_session(tmp_path):
    no_cli = CoordinatorLink(SID, run=ok_run(), which=lambda c: None,
                             state_file=tmp_path / "s.json")
    with pytest.raises(DevTeamError, match="isn't installed"):
        no_cli.send("hi")
    no_session = CoordinatorLink("not-a-session", run=ok_run(),
                                 which=lambda c: "/x", state_file=tmp_path / "s.json")
    with pytest.raises(DevTeamError, match="No coordinator session"):
        no_session.send("hi")


def test_replies_clear_pending_and_keep_working(tmp_path):
    notion = FakeNotion()
    team = link(tmp_path, notion=notion)
    a = team.send("first")["reply_id"]
    b = team.send("second")["reply_id"]
    notion.rows = [row(a, "Done, the draft PR is up."),
                   row(b, "Builder is on round two.", status="working"),
                   row("hc-someone-else", "not ours")]
    got = team.replies()
    assert {r["reply_id"]: r["status"] for r in got} == {a: "answered", b: "working"}
    assert team.pending() == [b]
    assert notion.queries[0]["data_source_id"] == "ds-inbox"
    assert team.replies() == []  # same "working" text: not repeated
    notion.rows = [row(b, "PR is ready.")]
    assert [r["reply"] for r in team.replies()] == ["PR is ready."]
    assert team.pending() == []


def test_seen_survives_restart(tmp_path):
    notion = FakeNotion()
    team = link(tmp_path, notion=notion)
    rid = team.send("x")["reply_id"]
    notion.rows = [row(rid, "Working on it.", status="working")]
    assert len(team.replies()) == 1
    again = link(tmp_path, notion=notion)  # reloads data/devteam.json
    assert again.replies() == []


def test_inbox_created_next_to_assignments(tmp_path):
    notion = FakeNotion(existing=False)
    assignments = SimpleNamespace(parent_page_id=lambda: "page-fall-2026")
    team = link(tmp_path, notion=notion, assignments=assignments)
    team.send("x")
    team.replies()
    created = notion.created[0]
    assert created["parent"] == {"type": "page_id", "page_id": "page-fall-2026"}
    assert set(created["initial_data_source"]["properties"]) == {
        "Reply ID", "Reply", "Status", "Run"}
    assert json.loads((tmp_path / "devteam.json").read_text())["inbox_id"] == "ds-new"


def test_tools_confirm_only_with_untrusted_content(tmp_path):
    asked = []
    registry = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    registry.register(*link(tmp_path).tools())
    tool = registry.tools["message_dev_team"]
    assert tool.confirm_prompt is None  # Kyle's own request goes straight through
    assert "Send this to your dev team" in tool.confirm_if_untrusted({"message": "x"})
    assert tool.private and registry.tools["check_dev_team"].private


def test_tool_results(tmp_path):
    notion = FakeNotion()
    tools = {t.name: t.handler for t in link(tmp_path, notion=notion).tools()}
    assert tools["check_dev_team"]()["replies"] == []
    sent = tools["message_dev_team"](message="fix the flaky test")
    assert sent["status"] == "sent"
    notion.rows = [row(sent["reply_id"], "Fixed in a draft PR.")]
    out = tools["check_dev_team"]()
    assert out["replies"][0]["reply"] == "Fixed in a draft PR."
    assert out["still_waiting"] == 0


def test_tool_reports_notion_failure(tmp_path):
    notion = FakeNotion()
    notion.data_sources = SimpleNamespace(query=lambda **kw: (_ for _ in ()).throw(
        RuntimeError("boom")))
    tools = {t.name: t.handler for t in link(tmp_path, notion=notion).tools()}
    tools["message_dev_team"](message="x")
    assert tools["check_dev_team"]()["status"] == "error"


def test_registration(monkeypatch, tmp_path):
    from assistant import cli

    config = json.loads(json.dumps(DEFAULTS))
    registry = ToolRegistry()
    assert cli.register_devteam(registry, config, lambda m: None) is False  # off
    config["devteam"].update(enabled=True, coordinator_session="nope",
                             state_file=str(tmp_path / "d.json"))
    notes = []
    assert cli.register_devteam(registry, config, notes.append) is False
    assert "coordinator_session" in notes[0]
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    config["devteam"]["coordinator_session"] = SID
    assert cli.register_devteam(registry, config, notes.append) is True
    assert registry.has("message_dev_team") and registry.has("check_dev_team")


def test_prompt_mentions_dev_team(tmp_path):
    from assistant.chat import ClaudeChat

    registry = ToolRegistry()
    registry.register(*link(tmp_path).tools())
    assert "message_dev_team" in ClaudeChat(client=object(), tools=registry).build_system()


def test_hub_source_sets_header_prefix_and_cap(tmp_path):
    run = ok_run()
    team = link(tmp_path, run=run)
    voice = team.send("x" * 5000)
    assert voice["reply_id"].startswith("hc-")
    assert run.calls[0][1]["input"].startswith(f"[Hey Claude voice | reply id {voice['reply_id']}]")
    assert "x" * 4000 + "\n" in run.calls[0][1]["input"]
    assert "x" * 4001 not in run.calls[0][1]["input"]
    hub = team.send("y" * 13000, source="hub", session="cse_other1")
    assert hub["reply_id"].startswith("hub-")
    args, kw = run.calls[1]
    assert args[3] == "cse_other1"  # the hub's window, not coordinator_session
    assert kw["input"].startswith(f"[Hey Claude hub | reply id {hub['reply_id']}]")
    assert "y" * 12000 + "\n" in kw["input"] and "y" * 12001 not in kw["input"]


def test_replies_max_chars(tmp_path):
    notion = FakeNotion()
    team = link(tmp_path, notion=notion)
    a = team.send("x")["reply_id"]
    b = team.send("y")["reply_id"]
    notion.rows = [row(a, "r" * 5000)]
    assert len(team.replies()[0]["reply"]) == 1200  # voice: unchanged
    notion.rows = [row(b, "s" * 15000)]
    assert len(team.replies(max_chars=12000)[0]["reply"]) == 12000


def test_presence_reads_the_newest_row(tmp_path):
    notion = FakeNotion()
    team = link(tmp_path, notion=notion)
    assert team.presence() == {"status": "", "text": "", "at": ""}
    tail = SID.split("_", 1)[1]
    notion.rows = [row(f"presence-{tail}", "Running the judge " * 10, status="working",
                       edited="2026-10-20T15:31:00.000Z"),
                   row(f"presence-{tail}", "older", status="idle")]
    got = team.presence()
    assert got["status"] == "working" and got["at"] == "2026-10-20T15:31:00.000Z"
    assert len(got["text"]) == 80
    q = notion.queries[-1]
    assert q["filter"] == {"or": [{"property": "Reply ID", "title": {"equals": f"presence-{tail}"}}]}
    assert q["sorts"][0]["direction"] == "descending"
    assert team.pending() == []  # reading presence never adds or pops a reply


def test_inbox_has_the_idle_status(tmp_path):
    notion = FakeNotion(existing=False)
    team = link(tmp_path, notion=notion,
                assignments=SimpleNamespace(parent_page_id=lambda: "p"))
    team.presence()
    options = notion.created[0]["initial_data_source"]["properties"]["Status"]["select"]["options"]
    assert [o["name"] for o in options] == ["answered", "working", "blocked", "idle"]


def test_state_is_written_privately_and_atomically(tmp_path, monkeypatch):
    import tempfile

    made = []
    real = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kw: made.append(kw) or real(**kw))
    team = link(tmp_path)
    team.send("x")
    assert made and made[0]["dir"] == tmp_path  # a unique temp file next to it
    assert list(tmp_path.iterdir()) == [tmp_path / "devteam.json"]
    import os
    import sys
    if sys.platform != "win32":
        assert os.stat(tmp_path / "devteam.json").st_mode & 0o777 == 0o600


@pytest.fixture
def tree_popen(monkeypatch):
    from tests.devteam_fakes import TreePopen

    TreePopen.made, TreePopen.tree_killed = [], set()
    monkeypatch.setattr(subprocess, "Popen", TreePopen)
    return TreePopen


def test_windows_timeout_kills_the_whole_cli_tree(tmp_path, monkeypatch, tree_popen):
    """npm claude.cmd: a timeout must taskkill /T the tree, or the read
    after it blocks on node.exe and the send never returns."""
    from assistant import devteam

    monkeypatch.setattr(devteam, "_WINDOWS", True)
    team = CoordinatorLink(SID, which=lambda c: f"/usr/bin/{c}", timeout=0.01,
                           state_file=tmp_path / "devteam.json",
                           notion_client=FakeNotion())
    with pytest.raises(DevTeamError, match="in time"):
        team.send("hello")
    cli, kill = tree_popen.made[0], tree_popen.made[1]
    assert kill.args == ["taskkill", "/T", "/F", "/PID", str(cli.pid)]
    assert cli.kw["creationflags"] == devteam._NEW_GROUP
    assert team.pending() == []


def test_timeout_elsewhere_kills_the_process(tmp_path, monkeypatch):
    from assistant import devteam

    class Hung:
        killed = False
        pid, returncode = 7, None

        def __init__(self, args, **kw):
            self.kw = kw

        def communicate(self, input=None, timeout=None):
            if not Hung.killed:
                raise subprocess.TimeoutExpired("claude", timeout)
            return "", ""

        def kill(self):
            Hung.killed = True

    monkeypatch.setattr(devteam, "_WINDOWS", False)
    monkeypatch.setattr(subprocess, "Popen", Hung)
    with pytest.raises(subprocess.TimeoutExpired):
        devteam.run_cli(["claude"], input="x", timeout=0.01)
    assert Hung.killed


def test_pending_and_save_wait_for_the_state_lock(tmp_path):
    """A send inserts its id under the lock; pending() (which rebuilds the
    dict) and _save() (which dumps it) must wait for it, or the new id is
    lost or the dump sees the dict change size mid-iteration."""
    import threading

    team = link(tmp_path)
    for call in (team.pending, team._save):
        done = threading.Event()
        with team._lock:  # a send halfway through inserting its id
            t = threading.Thread(target=lambda: (call(), done.set()))
            t.start()
            assert not done.wait(0.2), f"{call.__name__} ran without the lock"
            team._state["pending"]["hc-new"] = datetime.now(timezone.utc).isoformat()
        t.join(5)
        assert done.is_set()
    assert team.pending() == ["hc-new"]
    assert "hc-new" in (tmp_path / "devteam.json").read_text()
