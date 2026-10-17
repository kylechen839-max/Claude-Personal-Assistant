import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from assistant.chat import ClaudeChat
from assistant.cli import is_yes, voice_confirm
from assistant.tools import Tool, ToolRegistry
from assistant.tools.calendar import GoogleCalendar
from assistant.tools.notion import NotionAssignments
from assistant.tools.timeutil import resolve_day
from tests.conftest import make_response, text_block, tool_use_block

TZ = "America/New_York"


# ---------- registry ----------
def test_registry_dispatch_and_errors():
    reg = ToolRegistry()
    reg.register(Tool("add", "add", {"type": "object"}, lambda a, b: {"sum": a + b}),
                 Tool("boom", "x", {"type": "object"},
                      lambda: (_ for _ in ()).throw(ValueError("bad"))))
    assert json.loads(reg.dispatch("add", {"a": 1, "b": 2})[0]) == {"sum": 3}
    assert reg.dispatch("boom", {}) == ("ValueError: bad", True)
    assert reg.dispatch("nope", {})[1] is True
    assert [s["name"] for s in reg.schemas()] == ["add", "boom"]


def test_registry_confirmation_gate():
    ran = []
    tool = Tool("delete", "d", {"type": "object"}, lambda id: ran.append(id) or "ok",
                confirm_prompt=lambda inp: f"Delete {inp['id']}?")
    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(tool)
    result, err = reg.dispatch("delete", {"id": "x"})
    assert not err and "cancelled" in result and ran == []
    assert asked == ["Delete x?"]
    reg.confirm = lambda q: True
    assert reg.dispatch("delete", {"id": "x"}) == ("ok", False) and ran == ["x"]
    # default confirm refuses
    reg2 = ToolRegistry()
    reg2.register(tool)
    assert "cancelled" in reg2.dispatch("delete", {"id": "y"})[0]


def test_is_yes():
    for t in ["Yes.", "yeah go ahead", "Okay", "Go ahead", "yes please"]:
        assert is_yes(t), t
    for t in ["No.", "no, don't", "wait", "", "yes... actually no", "maybe"]:
        assert not is_yes(t), t


def test_voice_confirm_speaks_and_listens():
    said = []
    tts = MagicMock()
    tts.speak.side_effect = said.append
    transcriber = MagicMock()
    transcriber.transcribe.return_value = "Yes, do it."
    confirm = voice_confirm(tts, lambda: [0.0], transcriber, output_fn=lambda *_: None)
    assert confirm("Delete Dentist?") is True
    assert "yes or no" in said[0]


# ---------- chat tool loop ----------
def test_chat_runs_tool_calls(fake_client):
    reg = ToolRegistry()
    reg.register(Tool("list_calendar_events", "x", {"type": "object"},
                      lambda day: {"events": [{"title": "Dentist", "start": "3 PM"}]}))
    fake_client.queue = [
        make_response(text_block("Let me check."),
                      tool_use_block("list_calendar_events", {"day": "tomorrow"}),
                      stop_reason="tool_use"),
        make_response(text_block("You have the dentist at 3 PM tomorrow.")),
    ]
    chat = ClaudeChat(client=fake_client, tools=reg)
    reply = chat.ask("What's on my calendar tomorrow?")
    assert reply == "You have the dentist at 3 PM tomorrow."
    first, second = fake_client.calls
    assert first["tools"][0]["name"] == "list_calendar_events"
    assert "Use your tools" in first["system"]
    assert "search the web" not in first["system"]  # no web tools registered
    tool_result = second["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert "Dentist" in tool_result["content"]
    assert [m["role"] for m in chat.history] == ["user", "assistant", "user", "assistant"]


def test_chat_tool_loop_is_bounded(fake_client):
    reg = ToolRegistry()
    reg.register(Tool("t", "x", {"type": "object"}, lambda: "ok"))
    fake_client.queue = [make_response(tool_use_block("t", {}), stop_reason="tool_use")
                         for _ in range(10)]
    chat = ClaudeChat(client=fake_client, tools=reg, max_tool_rounds=2)
    assert "too many steps" in chat.ask("loop")
    assert len(fake_client.calls) == 3


def test_chat_rolls_back_whole_turn_on_error(fake_client):
    reg = ToolRegistry()
    reg.register(Tool("t", "x", {"type": "object"}, lambda: "ok"))
    fake_client.queue = [make_response(tool_use_block("t", {}), stop_reason="tool_use")]
    chat = ClaudeChat(client=fake_client, tools=reg)
    with pytest.raises(IndexError):  # second API call fails (queue empty)
        chat.ask("hi")
    assert chat.history == []


# ---------- Google Calendar ----------
def make_service(event=None, items=()):
    service = MagicMock()
    events = service.events.return_value
    events.list.return_value.execute.return_value = {"items": list(items)}
    events.get.return_value.execute.return_value = event
    events.insert.return_value.execute.side_effect = lambda: {
        "id": "new1", **events.insert.call_args.kwargs["body"]}
    events.patch.return_value.execute.side_effect = lambda: {
        **event, **events.patch.call_args.kwargs["body"]}
    events.delete.return_value.execute.return_value = ""
    return service, events


DENTIST = {"id": "ev1", "summary": "Dentist",
           "start": {"dateTime": "2026-10-17T15:00:00-04:00"},
           "end": {"dateTime": "2026-10-17T16:00:00-04:00"}}
HOLIDAY = {"id": "ev2", "summary": "Fall break",
           "start": {"date": "2026-10-17"}, "end": {"date": "2026-10-19"}}


def test_calendar_list_events_tomorrow():
    service, events = make_service(items=[DENTIST, HOLIDAY])
    cal = GoogleCalendar(service=service, timezone=TZ)
    out = cal.list_events("tomorrow")
    kw = events.list.call_args.kwargs
    tomorrow = datetime.now(ZoneInfo(TZ)).date() + timedelta(days=1)
    assert kw["timeMin"].startswith(tomorrow.isoformat())
    assert kw["singleEvents"] and kw["orderBy"] == "startTime"
    assert out["count"] == 2
    assert out["events"][0] == {"id": "ev1", "title": "Dentist",
                                "date": "Saturday, October 17", "start": "3 PM",
                                "end": "4 PM"}
    assert out["events"][1]["all_day"] is True


def test_calendar_create_event_defaults_to_one_hour():
    service, events = make_service()
    cal = GoogleCalendar(service=service, timezone=TZ)
    out = cal.create_event("Study group", "2026-10-20T14:30:00")
    body = events.insert.call_args.kwargs["body"]
    assert body["start"] == {"dateTime": "2026-10-20T14:30:00-04:00",
                             "timeZone": TZ}
    assert body["end"]["dateTime"] == "2026-10-20T15:30:00-04:00"
    assert out["status"] == "created" and out["event"]["start"] == "2:30 PM"


def test_calendar_move_keeps_duration_across_dst():
    service, events = make_service(event=DENTIST)
    cal = GoogleCalendar(service=service, timezone=TZ)
    cal.move_event("ev1", "2026-11-02T10:00:00")  # after DST ends
    body = events.patch.call_args.kwargs["body"]
    assert body["start"]["dateTime"] == "2026-11-02T10:00:00-05:00"
    assert body["end"]["dateTime"] == "2026-11-02T11:00:00-05:00"


def test_calendar_create_all_day_event():
    service, events = make_service()
    cal = GoogleCalendar(service=service, timezone=TZ)
    out = cal.create_event("Fall break", "2026-10-22", "2026-10-23")
    body = events.insert.call_args.kwargs["body"]
    # Google's all-day end date is exclusive; the tool's `end` is inclusive.
    assert body["start"] == {"date": "2026-10-22"}
    assert body["end"] == {"date": "2026-10-24"}
    assert out["event"]["all_day"] is True
    cal.create_event("Holiday", "2026-12-25")
    assert events.insert.call_args.kwargs["body"]["end"] == {"date": "2026-12-26"}
    with pytest.raises(ValueError):
        cal.create_event("Bad", "2026-10-22", "2026-10-21")


def test_calendar_move_all_day_event_with_new_end():
    service, events = make_service(event=HOLIDAY)
    GoogleCalendar(service=service, timezone=TZ).move_event(
        "ev2", "2026-10-24", "2026-10-24")
    assert events.patch.call_args.kwargs["body"] == {
        "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-25"}}


def test_calendar_move_all_day_event():
    service, events = make_service(event=HOLIDAY)
    GoogleCalendar(service=service, timezone=TZ).move_event("ev2", "2026-10-24")
    assert events.patch.call_args.kwargs["body"] == {
        "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-26"}}


def test_calendar_delete_requires_confirmation():
    service, events = make_service(event=DENTIST)
    cal = GoogleCalendar(service=service, timezone=TZ)
    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(*cal.tools())
    result, _ = reg.dispatch("delete_calendar_event", {"event_id": "ev1"})
    assert "cancelled" in result and not events.delete.called
    assert asked == ["Do you want me to delete Dentist on Saturday, October 17 at 3 PM?"]
    reg.confirm = lambda q: True
    result, _ = reg.dispatch("delete_calendar_event", {"event_id": "ev1"})
    assert json.loads(result)["status"] == "deleted"
    events.delete.assert_called_with(calendarId="primary", eventId="ev1")


def test_resolve_day():
    tz = ZoneInfo(TZ)
    today = datetime.now(tz).date()
    assert resolve_day("today", tz) == today
    assert resolve_day("Tomorrow", tz) == today + timedelta(days=1)
    assert resolve_day("2026-12-25", tz).month == 12
    assert 0 <= (resolve_day("friday", tz) - today).days < 7


@pytest.mark.parametrize("today_iso,day,expected", [
    ("2026-10-16", "friday", "2026-10-16"),        # Friday: plain name = today
    ("2026-10-16", "this Friday", "2026-10-16"),
    ("2026-10-16", "next friday", "2026-10-23"),   # never today
    ("2026-10-14", "next friday", "2026-10-16"),
    ("2026-10-14", "monday", "2026-10-19"),
    ("2026-10-14", "on  Monday", "2026-10-19"),
])
def test_resolve_day_weekdays(monkeypatch, today_iso, day, expected):
    from assistant.tools import timeutil

    fixed = datetime.fromisoformat(today_iso + "T09:00:00")

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=tz)

    monkeypatch.setattr(timeutil, "datetime", FakeDatetime)
    assert resolve_day(day, ZoneInfo(TZ)).isoformat() == expected
    with pytest.raises(ValueError):
        resolve_day("next week", ZoneInfo(TZ))


# ---------- Notion ----------
def page(id, name, status="Not started", due="2026-10-20", course="CS II"):
    return {"id": id, "properties": {
        "Assignment": {"title": [{"plain_text": name}]},
        "Status": {"status": {"name": status}},
        "Course": {"select": {"name": course}},
        "Type": {"select": {"name": "Homework"}},
        "Due": {"date": {"start": due}},
    }}


def make_notion(pages):
    client = MagicMock()
    client.databases.retrieve.return_value = {"data_sources": [{"id": "ds1"}]}

    def query(data_source_id, filter, sorts, **kw):
        assert data_source_id == "ds1"
        if "title" in filter:
            needle = filter["title"]["contains"].lower()
            hits = [p for p in pages if needle in
                    p["properties"]["Assignment"]["title"][0]["plain_text"].lower()]
        else:
            hits = pages
        return {"results": hits, "has_more": False}

    client.data_sources.query.side_effect = query
    client.pages.update.side_effect = lambda page_id, properties: {
        **next(p for p in pages if p["id"] == page_id)}
    return client


def test_notion_list_assignments_filters():
    client = make_notion([page("p1", "Homework 6", due="2026-10-20T03:59:00.000Z")])
    out = NotionAssignments("db", client=client, timezone=TZ).list_assignments(7)
    flt = client.data_sources.query.call_args.kwargs["filter"]["and"]
    assert {"property": "Status", "status": {"does_not_equal": "Done"}} in flt
    assert any("on_or_before" in c.get("date", {}) for c in flt)
    a = out["assignments"][0]
    assert a["name"] == "Homework 6" and a["course"] == "CS II"
    assert a["due"] == "Monday, October 19 at 11:59 PM"  # UTC -> local


def test_notion_update_status_by_name():
    pages = [page("p1", "Homework 6"), page("p2", "Homework 6 reading", "Done")]
    client = make_notion(pages)
    notion = NotionAssignments("db", client=client, timezone=TZ)
    out = notion.update_status("done", name="homework 6")
    assert out["status"] == "updated"
    client.pages.update.assert_called_with(
        page_id="p1", properties={"Status": {"status": {"name": "Done"}}})


def test_notion_update_status_fuzzy_and_ambiguous():
    client = make_notion([page("p1", "Lab Report 3"), page("p2", "Lab Report 4")])
    notion = NotionAssignments("db", client=client, timezone=TZ)
    assert notion.update_status("In progress", name="lab report")["status"] == "ambiguous"
    assert notion.update_status("In progress", name="lab reprt 4")["status"] == "updated"
    with pytest.raises(ValueError):
        notion.update_status("Finished", assignment_id="p1")


# ---------- optional real read-only calls ----------
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NOTION_TOKEN"), reason="NOTION_TOKEN not set")
def test_live_notion_list_assignments():
    from assistant.config import load_config

    db = load_config()["notion"]["database_id"]
    out = NotionAssignments(db).list_assignments(days_ahead=30)
    assert "assignments" in out


@pytest.mark.live
@pytest.mark.skipif(not Path(os.environ.get("GOOGLE_TOKEN", "token.json")).exists(),
                    reason="Google token.json not present (run calendar sign-in first)")
def test_live_calendar_list_today():
    out = GoogleCalendar(token_path=os.environ.get("GOOGLE_TOKEN", "token.json"),
                         credentials_path=os.environ.get("GOOGLE_CREDENTIALS",
                                                         "credentials.json")
                         ).list_events("today")
    assert "events" in out


def test_headless_oauth_flow(tmp_path):
    from urllib.parse import parse_qs, urlparse

    from assistant.tools import calendar as cal_mod

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"installed": {
        "client_id": "cid.apps.googleusercontent.com", "client_secret": "s",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}))
    state_file = tmp_path / "state.json"
    url = cal_mod.headless_start(str(creds), str(state_file))
    q = parse_qs(urlparse(url).query)
    assert q["redirect_uri"] == [cal_mod.HEADLESS_REDIRECT]
    assert q["access_type"] == ["offline"] and "code_challenge" in q
    saved = json.loads(state_file.read_text())
    assert saved["state"] == q["state"][0] and saved["code_verifier"]

    with pytest.raises(RuntimeError, match="State mismatch"):
        cal_mod.headless_finish("http://localhost:8765/?code=c&state=wrong",
                                str(creds), str(tmp_path / "t.json"), str(state_file))

    from unittest.mock import patch
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as fetch, \
         patch("google_auth_oauthlib.flow.Flow.credentials",
               new=MagicMock(to_json=lambda: '{"token": "x"}')):
        cal_mod.headless_finish(
            f"http://localhost:8765/?state={saved['state']}&code=abc&scope=x",
            str(creds), str(tmp_path / "t.json"), str(state_file))
    fetch.assert_called_once_with(code="abc")
    assert json.loads((tmp_path / "t.json").read_text()) == {"token": "x"}
    assert not state_file.exists()
