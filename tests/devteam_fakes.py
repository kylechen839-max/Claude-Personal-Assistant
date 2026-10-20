"""Shared fakes for the dev team link and the PC hub: the Claude Code CLI
(subprocess.run) and the Notion client, with the real API shapes."""

import json
from types import SimpleNamespace

from assistant.devteam import INBOX_TITLE

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


def row(rid, reply, status="answered", trashed=False, edited=None):
    return {"in_trash": trashed, "object": "page",
            "last_edited_time": edited or "2026-10-20T15:30:00.000Z", "properties": {
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
