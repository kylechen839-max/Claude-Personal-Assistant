"""Dev team coordinator link: sending through the Claude Code CLI (mocked)
and reading replies from the Notion inbox (mocked)."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from assistant.config import DEFAULTS
from assistant.devteam import (INBOX_TITLE, CoordinatorLink, DevTeamError,
                               session_id)
from assistant.tools import ToolRegistry

SID = "session_01DiUkqY2kzbUbDmW1w96rfi"


class FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0, exc=None):
        self.calls = []
        self.result = SimpleNamespace(stdout=stdout, stderr=stderr,
                                      returncode=returncode)
        self.exc = exc

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.exc:
            raise self.exc
        return self.result


def ok_run():
    return FakeRun(stdout=json.dumps({"ok": True, "session_id": SID,
                                      "url": f"https://claude.ai/code/{SID}"}))


def row(rid, reply, status="answered", trashed=False):
    return {"in_trash": trashed, "properties": {
        "Reply ID": {"title": [{"plain_text": rid}]},
        "Reply": {"rich_text": [{"plain_text": reply}]},
        "Status": {"select": {"name": status}},
        "Run": {"url": None}}}


class FakeNotion:
    def __init__(self, rows=(), existing=True):
        self.rows = list(rows)
        self.queries = []
        self.created = []
        self.existing = existing
        self.data_sources = SimpleNamespace(query=self._query)
        self.databases = SimpleNamespace(create=self._create)

    def search(self, **kw):
        if not self.existing:
            return {"results": []}
        return {"results": [{"id": "ds-inbox", "title": [{"plain_text": INBOX_TITLE}]}]}

    def _query(self, **kw):
        self.queries.append(kw)
        return {"results": self.rows}

    def _create(self, **kw):
        self.created.append(kw)
        return {"id": "db-new", "data_sources": [{"id": "ds-new"}]}


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
