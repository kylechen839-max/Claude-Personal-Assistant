"""Notion tools: list upcoming assignments and update their Status."""

from __future__ import annotations

import difflib
import logging
import os
from datetime import date, datetime, timedelta

from assistant.tools import Tool
from assistant.tools.timeutil import local_zone, parse_datetime, spoken_date, spoken_time

log = logging.getLogger(__name__)

STATUSES = ["Not started", "In progress", "Done"]


class NotionAssignments:
    """Works with a database whose properties are Assignment (title),
    Course (select), Due (date), Status (status), Type (select), Notes."""

    def __init__(self, database_id: str, client=None, token: str | None = None,
                 data_source_id: str | None = None, timezone: str | None = None,
                 title_prop="Assignment", status_prop="Status", due_prop="Due",
                 course_prop="Course", type_prop="Type"):
        self.database_id = database_id
        self._client = client
        self.token = token
        self._data_source_id = data_source_id
        self.tz = local_zone(timezone)
        self.title_prop, self.status_prop, self.due_prop = title_prop, status_prop, due_prop
        self.course_prop, self.type_prop = course_prop, type_prop

    @property
    def client(self):
        if self._client is None:
            from notion_client import Client

            self._client = Client(auth=self.token or os.environ["NOTION_TOKEN"])
        return self._client

    @property
    def data_source_id(self) -> str:
        if self._data_source_id is None:
            db = self.client.databases.retrieve(database_id=self.database_id)
            self._data_source_id = db["data_sources"][0]["id"]
        return self._data_source_id

    def _query(self, filter: dict) -> list[dict]:
        pages, cursor = [], None
        while True:
            kwargs = {"filter": filter,
                      "sorts": [{"property": self.due_prop, "direction": "ascending"}]}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self.client.data_sources.query(
                data_source_id=self.data_source_id, **kwargs)
            pages += resp.get("results", [])
            if not resp.get("has_more") or len(pages) >= 200:
                return pages
            cursor = resp.get("next_cursor")

    def _summarize(self, page: dict) -> dict:
        props = page["properties"]
        title = "".join(t.get("plain_text", "")
                        for t in props.get(self.title_prop, {}).get("title", []))
        info = {"id": page["id"], "name": title or "(untitled)"}
        status = (props.get(self.status_prop, {}).get("status") or {}).get("name")
        info["status"] = status
        for key, prop in [("course", self.course_prop), ("type", self.type_prop)]:
            sel = (props.get(prop, {}).get("select") or {}).get("name")
            if sel:
                info[key] = sel
        due = (props.get(self.due_prop, {}).get("date") or {}).get("start")
        if due:
            if "T" in due:
                dt = parse_datetime(due, self.tz)
                info["due"] = f"{spoken_date(dt.date())} at {spoken_time(dt)}"
                info["due_date"] = dt.date().isoformat()
            else:
                info["due"] = spoken_date(date.fromisoformat(due))
                info["due_date"] = due
        return info

    def list_assignments(self, days_ahead: int = 14, include_done: bool = False,
                         include_overdue: bool = True) -> dict:
        today = datetime.now(self.tz).date()
        conditions = [{"property": self.due_prop, "date": {
            "on_or_before": (today + timedelta(days=days_ahead)).isoformat()}}]
        if not include_overdue:
            conditions.append({"property": self.due_prop,
                               "date": {"on_or_after": today.isoformat()}})
        if not include_done:
            conditions.append({"property": self.status_prop,
                               "status": {"does_not_equal": "Done"}})
        items = [self._summarize(p) for p in self._query({"and": conditions})]
        for item in items:
            if item.get("due_date", "9999") < today.isoformat():
                item["overdue"] = True
        return {"today": spoken_date(today), "count": len(items),
                "assignments": items}

    def find(self, name: str) -> list[dict]:
        """Find assignments by (fuzzy) name, unfinished ones first."""
        pages = self._query({"property": self.title_prop,
                             "title": {"contains": name}})
        items = [self._summarize(p) for p in pages]
        if not items:  # e.g. "homework six" vs "Homework 6"
            pages = self._query({"property": self.status_prop,
                                 "status": {"does_not_equal": "Done"}})
            scored = [(difflib.SequenceMatcher(None, name.lower(),
                                               c["name"].lower()).ratio(), c)
                      for c in map(self._summarize, pages)]
            best = max((score for score, _ in scored), default=0.0)
            items = [c for score, c in scored
                     if score >= 0.6 and score >= best - 0.05]
        return sorted(items, key=lambda i: i.get("status") == "Done")

    def update_status(self, status: str, assignment_id: str | None = None,
                      name: str | None = None) -> dict:
        match = {s.lower(): s for s in STATUSES}.get(status.strip().lower())
        if match is None:
            raise ValueError(f"Status must be one of {STATUSES}")
        if not assignment_id:
            if not name:
                raise ValueError("Give assignment_id or name")
            found = self.find(name)
            if not found:
                return {"status": "not_found", "message": f"No assignment like '{name}'"}
            exact = [f for f in found if f["name"].lower() == name.lower()]
            if len(exact) == 1:
                found = exact
            elif len(found) > 1:
                return {"status": "ambiguous", "matches": found,
                        "message": "Several assignments match; ask which one "
                                   "or call again with assignment_id."}
            assignment_id = found[0]["id"]
        page = self.client.pages.update(
            page_id=assignment_id,
            properties={self.status_prop: {"status": {"name": match}}})
        return {"status": "updated", "assignment": self._summarize(page)}

    def describe_for_update(self, tool_input: dict) -> str:
        what = tool_input.get("name") or "that assignment"
        if not tool_input.get("name") and tool_input.get("assignment_id"):
            try:
                page = self.client.pages.retrieve(page_id=tool_input["assignment_id"])
                what = self._summarize(page)["name"]
            except Exception:
                pass
        return f"Do you want me to mark {what} as {tool_input.get('status')}?"

    def tools(self) -> list[Tool]:
        return [
            Tool("list_assignments",
                 "List Kyle's upcoming (and overdue) school assignments from his "
                 "Notion Assignments database, soonest first.",
                 {"type": "object", "properties": {
                     "days_ahead": {"type": "integer", "minimum": 0, "maximum": 120,
                                    "description": "How many days ahead (default 14)"},
                     "include_done": {"type": "boolean", "description":
                                      "Include finished ones (default false)"},
                     "include_overdue": {"type": "boolean", "description":
                                         "Include past-due unfinished ones (default true)"}}},
                 self.list_assignments),
            Tool("update_assignment_status",
                 "Set an assignment's Status in Notion. Identify it by "
                 "assignment_id (from list_assignments) or by name.",
                 {"type": "object", "properties": {
                     "status": {"type": "string", "enum": STATUSES},
                     "assignment_id": {"type": "string"},
                     "name": {"type": "string", "description": "Assignment name, "
                              "e.g. 'Homework 6'"}},
                  "required": ["status"]},
                 self.update_status, confirm_if_untrusted=self.describe_for_update),
        ]
