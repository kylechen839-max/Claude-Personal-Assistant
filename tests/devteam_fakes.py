"""Fakes for the dev team voice link: the Claude Code CLI (subprocess.run
and Popen) and the Notion client, with the real API shapes."""

import json
from types import SimpleNamespace

from kwil.devteam import INBOX_TITLE

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
    return {"in_trash": trashed, "object": "page",
            "last_edited_time": "2026-10-20T15:30:00.000Z", "properties": {
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


class TreePopen:
    """subprocess.Popen for npm `claude.cmd` on Windows that never answers:
    cmd.exe runs node.exe, and node.exe holds the pipes. Killing only the
    top process (proc.kill()) leaves them held, so the read after it would
    block forever (raised here instead of hanging the test); `taskkill /T`
    frees them. Patch it over subprocess.Popen: subprocess.run's own
    taskkill call comes here too."""
    made = []
    tree_killed = set()

    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        self.pid = 4000 + len(TreePopen.made)
        self.returncode = None
        TreePopen.made.append(self)
        if args[0] == "taskkill":
            TreePopen.tree_killed.add(int(args[args.index("/PID") + 1]))
            self.returncode = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _blocked(self, timeout):
        if self.args[0] == "taskkill" or self.pid in TreePopen.tree_killed:
            self.returncode = 1
            return "", ""
        if timeout is not None:
            import subprocess

            raise subprocess.TimeoutExpired(self.args, timeout)
        raise RuntimeError("blocked forever on node.exe's pipes")

    def communicate(self, input=None, timeout=None):
        return self._blocked(timeout)

    def wait(self, timeout=None):
        self._blocked(timeout)
        return self.returncode

    def kill(self):
        pass  # only cmd.exe: node.exe keeps the pipes

    def poll(self):
        return self.returncode
