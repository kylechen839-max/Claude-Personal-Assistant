"""Prompt caching, Haiku routing and usage tracking (chat.py, router.py,
usage.py, tools.ToolRegistry.schemas). Offline: the mocked client records
every request; usage objects have the SDK's field names."""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from assistant import chat as chat_mod, router, usage
from assistant.chat import ClaudeChat, request_messages
from assistant.tools import Tool, ToolRegistry
from assistant.tools.web import server_tools
from tests.conftest import (first_text, make_response, make_usage, request_text,
                            system_text, text_block, tool_use_block)

MAIN, FAST = "claude-sonnet-5-5", "claude-haiku-5-5"


def registry(main=MAIN):
    reg = ToolRegistry(confirm=lambda q: True)
    reg.register(Tool(name="list_calendar_events", description="d",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda **kw: {"events": []}))
    reg.add_server_tools(*server_tools(main, {}))
    return reg


def routed(fake_client, **kw):
    return ClaudeChat(client=fake_client, model=MAIN, fast_model=FAST,
                      router="auto", **kw)


def pin_clock(monkeypatch, *moments):
    times = iter(moments)
    monkeypatch.setattr(chat_mod, "_now", lambda tz=None: next(times))


def strip_cache_control(value):
    if isinstance(value, dict):
        return {k: strip_cache_control(v) for k, v in value.items()
                if k != "cache_control"}
    if isinstance(value, list):
        return [strip_cache_control(v) for v in value]
    return value


def markers(value) -> int:
    return json.dumps(value).count('"cache_control"')


# --- caching --------------------------------------------------------------------

def test_system_is_byte_identical_across_turns_with_a_cache_marker(fake_client, monkeypatch):
    """The proof: the date and time used to be in the system prompt, so
    every turn (every minute) rewrote the whole cached prefix."""
    t0 = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    pin_clock(monkeypatch, t0, t0 + timedelta(minutes=1))
    fake_client.queue = [make_response(text_block("a")), make_response(text_block("b"))]
    chat = ClaudeChat(client=fake_client, tools=registry())
    chat.ask("hello")
    chat.ask("and again")
    first, second = fake_client.calls
    assert json.dumps(first["system"]) == json.dumps(second["system"])
    assert first["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "Current local date" not in system_text(first)
    assert json.dumps(first["tools"]) == json.dumps(second["tools"])
    # the time rides in each turn's own context block instead
    assert "09:30 AM" in request_text(first) and "09:31 AM" in request_text(second)


def test_the_conversation_is_append_only_on_the_wire(fake_client):
    """Each request starts with the previous one's messages, byte for byte
    (cache markers aside), across a tool round and a second turn; exactly
    one marker sits at the end; the history keeps none."""
    fake_client.queue = [
        make_response(text_block("checking"), tool_use_block("list_calendar_events", {}),
                      stop_reason="tool_use"),
        make_response(text_block("Nothing today.")),
        make_response(text_block("Sure.")),
    ]
    chat = ClaudeChat(client=fake_client, tools=registry())
    chat.ask("what's on today")
    chat.ask("thanks")
    sent = [json.dumps(strip_cache_control(c["messages"])) for c in fake_client.calls]
    for before, after in zip(sent, sent[1:]):
        assert after.startswith(before[:-1])  # without the closing "]"
    for call in fake_client.calls:
        assert markers(call["messages"]) == 1
        assert "cache_control" in json.dumps(call["messages"][-1])
        assert all("context" not in m for m in call["messages"])  # not an API key
    assert markers(chat.history) == 0
    assert first_text(fake_client.calls[0]["messages"][0]["content"]) == "what's on today"


def test_request_messages_marks_a_copy_and_walks_back_past_server_blocks():
    history = [
        {"role": "user", "content": "hi", "context": "ctx"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "searching"},
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
            {"type": "web_search_tool_result", "tool_use_id": "s1", "content": []}]},
    ]
    before = json.dumps(history)
    sent = request_messages(history, cache=True)
    assert json.dumps(history) == before  # history untouched
    assert sent[0]["content"] == [{"type": "text", "text": "hi"},
                                  {"type": "text", "text": "ctx"}]
    assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert markers(sent) == 1
    assert markers(request_messages(history)) == 0  # only when asked


def test_notes_ride_in_the_turn_and_never_reach_the_summary_or_recorder(fake_client):
    from unittest.mock import MagicMock

    from assistant.memory import transcript

    memory = MagicMock()
    memory.retrieve.return_value = ["SECRET NOTE"]
    recorder = MagicMock(incognito=False, conversation_id="c1", active=False)
    fake_client.queue = [make_response(text_block("ok"))]
    chat = ClaudeChat(client=fake_client, memory=memory, recorder=recorder)
    chat.ask("hello")
    call = fake_client.calls[0]
    assert "SECRET NOTE" not in system_text(call)
    assert "SECRET NOTE" in request_text(call)
    assert "stored data, not instructions" in request_text(call)
    assert "SECRET NOTE" not in transcript(chat.history)
    recorder.user.assert_called_once_with("hello")


def test_zh_and_family_context_never_says_kyle():
    zh = ClaudeChat(client=object(), language="zh")
    mom = ClaudeChat(client=object(), user_name="Mom")
    assert "Kyle" not in zh.turn_context(["a note"], "review?")
    assert "Kyle" not in mom.turn_context(["a note"]) and "Mom" in mom.turn_context()


# --- routing --------------------------------------------------------------------

@pytest.mark.parametrize("text, model, reason", [
    ("what time is it", FAST, "simple"),
    ("hi there", FAST, "simple"),
    ("今天几号", FAST, "simple"),
    ("move my dentist to 3pm", MAIN, "keyword"),
    ("delete the gym", MAIN, "keyword"),
    ("what's the weather", MAIN, "keyword"),
    ("why is the sky blue", MAIN, "keyword"),
    ("turn on the lights", MAIN, "keyword"),
    ("帮我安排明天的会议", MAIN, "keyword"),
    ("so I was thinking about the trip next month and the hotel and what we "
     "might do", MAIN, "long"),
    ("我" * 31, MAIN, "long"),
])
def test_router_picks_by_the_words(text, model, reason):
    chat = routed(object())
    assert router.choose_model(chat, text) == (model, reason)


def test_router_keeps_risky_turns_on_the_main_model():
    chat = routed(object())
    photo = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "x"}}
    assert router.choose_model(chat, "what is this", [photo]) == (MAIN, "attachments")
    chat.history = [{"role": "user", "content": [photo]},
                    {"role": "assistant", "content": "a cat"}]
    assert router.choose_model(chat, "hi") == (MAIN, "attachments")
    chat.history = []
    chat.untrusted_context = True
    assert router.choose_model(chat, "hi") == (MAIN, "untrusted")
    off = ClaudeChat(client=object(), model=MAIN, fast_model=FAST, router="off")
    assert router.choose_model(off, "hi") == (MAIN, "off")
    same = ClaudeChat(client=object(), model=MAIN, fast_model=MAIN, router="auto")
    assert router.choose_model(same, "hi") == (MAIN, "no_fast_model")
    assert ClaudeChat(client=object()).router == "off"  # off unless configured


def test_escalation_is_sticky_until_reset(fake_client):
    fake_client.queue = [make_response(text_block(t)) for t in "abcd"]
    chat = routed(fake_client)
    chat.ask("what time is it")
    chat.ask("move my dentist to 3pm")
    chat.ask("what time is it")
    assert [c["model"] for c in fake_client.calls] == [FAST, MAIN, MAIN]
    chat.reset()
    chat.ask("what time is it")
    assert fake_client.calls[-1]["model"] == FAST


def test_one_model_for_the_whole_tool_loop(fake_client):
    fake_client.queue = [
        make_response(tool_use_block("list_calendar_events", {}), stop_reason="tool_use"),
        make_response(text_block("Nothing.")),
    ]
    chat = routed(fake_client, tools=registry())
    chat.ask("what's today")
    assert [c["model"] for c in fake_client.calls] == [FAST, FAST]


def test_a_haiku_turn_sends_the_web_tool_versions_haiku_supports(fake_client):
    fake_client.queue = [make_response(text_block("a")), make_response(text_block("b"))]
    chat = routed(fake_client, tools=registry())
    chat.ask("what time is it")
    chat.ask("search the news")
    types = [{t["name"]: t.get("type") for t in c["tools"]} for c in fake_client.calls]
    assert types[0]["web_search"] == "web_search_20250305"
    assert types[0]["web_fetch"] == "web_fetch_20250910"
    assert types[1]["web_search"] == "web_search_20260209"
    # the registry itself is unchanged
    assert chat.tools.server_tools[0]["type"] == "web_search_20260209"


def test_the_route_log_has_no_words(fake_client, caplog):
    fake_client.queue = [make_response(text_block("It's noon."))]
    chat = routed(fake_client)
    with caplog.at_level(logging.DEBUG, logger="assistant.router"):
        chat.ask("what time is it SECRETWORD")
    lines = [r.getMessage() for r in caplog.records if r.name == "assistant.router"]
    assert lines == [f"Route: {FAST} (simple)"]


def test_summaries_use_the_fast_model(fake_client):
    from unittest.mock import MagicMock

    fake_client.queue = [make_response(text_block("hi")),
                         make_response(text_block("Kyle said hi."))]
    chat = ClaudeChat(client=fake_client, model=MAIN, fast_model=FAST,
                      memory=MagicMock(retrieve=MagicMock(return_value=[])))
    chat.ask("hello")
    chat.end_conversation()
    assert fake_client.calls[-1]["model"] == FAST


# --- usage ----------------------------------------------------------------------

def test_usage_is_logged_as_numbers_only(fake_client, caplog):
    fake_client.queue = [make_response(
        text_block("REPLYWORDS"), usage=make_usage(1200, 40, 300, 900, 2))]
    chat = ClaudeChat(client=fake_client, model=MAIN)
    with caplog.at_level(logging.DEBUG, logger="assistant.usage"):
        chat.ask("SECRETWORD please")
    lines = [r.getMessage() for r in caplog.records if r.name == "assistant.usage"]
    assert len(lines) == 1 and all(r.levelno == logging.INFO for r in caplog.records
                                   if r.name == "assistant.usage")
    assert ("input=1200 output=40 cache_read=900 cache_write=300 web_searches=2"
            in lines[0]) and MAIN in lines[0]
    assert "SECRETWORD" not in caplog.text and "REPLYWORDS" not in caplog.text


def test_cost_uses_the_list_prices():
    sonnet = usage.counts(make_usage(1_000_000, 1_000_000, 1_000_000, 1_000_000))
    assert usage.cost(MAIN, sonnet) == pytest.approx(2 + 10 + 2.5 + 0.2)
    haiku = usage.counts(make_usage(10_000, 1_000_000))
    assert usage.cost(FAST, haiku) == pytest.approx(0.001 + 0.5)
    long = usage.counts(make_usage(100_001, 0))  # over 100K: 5x
    assert usage.cost(FAST, long) == pytest.approx(5 * 0.1 * 100_001 / 1e6)
    assert usage.cost("claude-unknown", sonnet) == 0.0


def test_usage_file_round_trip_and_twelve_month_cap(tmp_path):
    path = tmp_path / "data" / "usage.json"
    old = {f"2025-{m:02d}": {MAIN: {"usd": 1.0}} for m in range(1, 13)}
    path.parent.mkdir()
    path.write_text(json.dumps({"months": old}))
    meter = usage.UsageMeter(path)
    meter.record(MAIN, make_usage(1_000_000, 0))
    meter.record(FAST, make_usage(100_000, 0, web_search_requests=1))
    again = usage.UsageMeter(path)
    month = datetime.now().strftime("%Y-%m")
    assert again.month_usd() == pytest.approx(2.01)
    assert again.months[month][FAST]["web_searches"] == 1
    assert again.months[month][MAIN]["calls"] == 1
    assert len(again.months) == 12 and "2025-01" not in again.months
    assert not list(path.parent.glob("*.tmp"))


def test_empty_usage_file_keeps_nothing_on_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    meter = usage.configure("")
    assert meter.path is None  # not Path("") (the current folder)
    meter.record(MAIN, make_usage(10, 10))
    assert meter.path is None and not list(tmp_path.iterdir())
    assert usage.month_usd() > 0


def test_every_api_call_is_counted_with_its_model(fake_client):
    fake_client.queue = [
        make_response(tool_use_block("list_calendar_events", {}), stop_reason="tool_use",
                      usage=make_usage(100, 10)),
        make_response(text_block("Nothing."), usage=make_usage(5, 10, 0, 100)),
    ]
    chat = routed(fake_client, tools=registry())
    chat.ask("what's today")
    totals = usage.METER.months[datetime.now().strftime("%Y-%m")][FAST]
    assert (totals["calls"], totals["input"], totals["cache_read"]) == (2, 105, 100)


def test_build_chat_and_factories_wire_the_router(monkeypatch):
    from assistant import cli
    from assistant.config import load_config

    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: None)
    chat = cli.build_chat(load_config(None, None))
    assert (chat.fast_model, chat.router) == (FAST, "auto")
    monkeypatch.setenv("CLAUDE_ROUTER", "off")  # the kill switch
    monkeypatch.setenv("CLAUDE_FAST_MODEL", "claude-other")
    chat = cli.build_chat(load_config(None, None))
    assert (chat.fast_model, chat.router) == ("claude-other", "off")
    assert router.choose_model(chat, "hi") == (MAIN, "off")


# --- round 2: changes always on the main model ------------------------------------

CHANGE_PHRASES = [
    "Put dentist on my calendar Friday at 3", "Book lunch with Sam Friday at noon",
    "Push my meeting to 4", "Push the meeting with Sam to Thursday",
    "Clear my afternoon", "Turn the lights off", "Lock the front door",
    "Unlock the door", "Set the bedroom lights to 50 percent", "Pause the music",
    "Skip this song", "Next song", "Volume up", "Forget that note",
    "Forget the note about my locker", "Remember that my locker code is 1234",
    "Save my lecture notes", "Message the dev team to ship it",
    "Tell the dev team to merge the PR", "Open YouTube", "I finished my essay",
    "Read my latest email",
    "明天下午三点提醒我吃药", "帮我记住我的密码是1234", "打开客厅的灯", "关灯",
    "播放音乐", "把会议推到周四", "删掉明天的会",
]


@pytest.mark.parametrize("text", CHANGE_PHRASES)
def test_change_requests_go_to_the_main_model(text):
    """The judge's router probe: each of these went to Haiku."""
    assert router.choose_model(routed(object()), text) == (MAIN, "keyword")


@pytest.mark.parametrize("text", ["what time is it", "what's on my calendar tomorrow",
                                  "tell me a joke", "good morning", "今天几号"])
def test_simple_questions_still_go_to_haiku(text):
    assert router.choose_model(routed(object()), text) == (FAST, "simple")


def calendar_registry(created):
    reg = registry()
    reg.register(Tool(name="create_calendar_event", description="d",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda **kw: created.append(kw) or {"id": "ev1"}))
    return reg


def test_a_haiku_change_never_runs_and_the_main_model_answers(fake_client, caplog):
    """Words can miss a phrasing; a Haiku response that calls a tool that
    isn't read-only is dropped before dispatch and Sonnet takes the turn."""
    created = []
    fake_client.queue = [
        make_response(tool_use_block("create_calendar_event", {"title": "x"},
                                     id="toolu_haiku"), stop_reason="tool_use"),
        make_response(tool_use_block("create_calendar_event", {"title": "Dentist"},
                                     id="toolu_main"), stop_reason="tool_use"),
        make_response(text_block("Added.")),
    ]
    chat = routed(fake_client, tools=calendar_registry(created))
    with caplog.at_level(logging.INFO):
        assert chat.ask("dentist friday at three") == "Added."
    assert [c["model"] for c in fake_client.calls] == [FAST, MAIN, MAIN]
    assert created == [{"title": "Dentist"}]  # only the main model's call ran
    assert "toolu_haiku" not in json.dumps(chat.history)
    assert "toolu_haiku" not in json.dumps(fake_client.calls[1]["messages"])
    assert chat.escalated
    assert f"Route: {MAIN} (escalated_tool)" in caplog.messages
    # the main model's request carries its own web tool versions
    assert {t["name"]: t.get("type") for t in fake_client.calls[1]["tools"]}[
        "web_search"] == "web_search_20260209"


def test_haiku_may_run_read_only_tools():
    assert not router.needs_main_for(["list_calendar_events", "read_email", "ha_state"])
    assert router.needs_main_for(["list_calendar_events", "ha_control"])
    assert router.needs_main_for(["some_new_tool"])  # unknown: not read-only


# --- round 2: the review offer once, deleted notes gone, chunked trimming ---------

def test_the_review_offer_is_sent_only_for_its_own_reply(fake_client):
    """probe_nudge: the offer used to ride along for the whole conversation
    in turn 1's replayed context."""
    from tests.conftest import sent_text

    fake_client.queue = [
        make_response(tool_use_block("list_calendar_events", {}), stop_reason="tool_use"),
        make_response(text_block("a")), make_response(text_block("b"))]
    chat = ClaudeChat(client=fake_client, tools=registry())
    chat.review_nudge = lambda: "OFFER A REVIEW"
    chat.ask("one")
    chat.ask("two")
    first, loop, second = (sent_text(c) for c in fake_client.calls)
    assert "OFFER A REVIEW" in first and "OFFER A REVIEW" in loop  # its own turn
    assert "OFFER A REVIEW" not in second


class NoteMemory:
    last_untrusted = False

    def __init__(self):
        self.last_matches = []

    def retrieve(self, query):
        self.last_matches = [("note-20261020-locker", "locker code 1234")]
        return ["locker code 1234"]


def delete_registry(confirm):
    reg = ToolRegistry(confirm=lambda q: confirm)
    reg.register(Tool(name="delete_note", description="d",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda note_id: {"status": "deleted", "id": note_id},
                      confirm_prompt=lambda inp: "Delete it?"))
    return reg


@pytest.mark.parametrize("confirm", [True, False])
def test_a_deleted_note_is_never_sent_again(fake_client, confirm):
    from tests.conftest import sent_text

    fake_client.queue = [
        make_response(text_block("ok")),
        make_response(tool_use_block("delete_note", {"note_id": "note-20261020-locker"}),
                      stop_reason="tool_use"),
        make_response(text_block("Deleted.")), make_response(text_block("sure"))]
    chat = ClaudeChat(client=fake_client, tools=delete_registry(confirm))
    chat.memory = NoteMemory()
    chat.ask("what's my locker code")
    assert "locker code 1234" in sent_text(fake_client.calls[0])
    chat.memory.retrieve = lambda q: []  # the next turns recall nothing
    chat.ask("forget it")
    chat.ask("thanks")
    last = sent_text(fake_client.calls[-1])
    if confirm:
        assert "locker code 1234" not in last
        assert "(a note that was deleted)" in last
    else:  # he said no: the note is still there, and so is its text
        assert "locker code 1234" in last


def test_a_note_recalled_once_stays_in_its_own_turn(fake_client):
    """probe_nudge, the other half: a note recalled for turn 1 is part of
    that turn and is replayed with it (append-only) until it is deleted."""
    from tests.conftest import sent_text

    fake_client.queue = [make_response(text_block("a")), make_response(text_block("b"))]
    chat = ClaudeChat(client=fake_client)
    chat.memory = NoteMemory()
    chat.ask("one")
    chat.memory.retrieve = lambda q: []
    chat.ask("two")
    assert "locker code 1234" in sent_text(fake_client.calls[1])
    assert "locker code 1234" not in request_text(fake_client.calls[1])  # not turn 2's


def test_trimming_in_chunks_keeps_the_prefix(fake_client):
    """Past max_history the history is cut to about half, so the next
    requests still start with the same bytes (one cache write, not one per
    turn)."""
    fake_client.queue = [make_response(text_block(f"r{i}")) for i in range(7)]
    chat = ClaudeChat(client=fake_client, max_history=8)
    for i in range(7):
        chat.ask(f"q{i}")
    sent = [json.dumps(strip_cache_control(c["messages"])) for c in fake_client.calls]
    # turn 5 overflowed (10 > 8) and was cut to 4; turns 6 and 7 extend it
    assert len(fake_client.calls[5]["messages"]) == 5
    assert sent[6].startswith(sent[5][:-1])
    assert len(chat.history) <= 8 and chat.history[0]["role"] == "user"


# --- round 2: shared usage file, every API call metered ---------------------------

def test_two_processes_add_up_in_one_usage_file(tmp_path):
    """usage_probe: host wake and host web share data/usage.json; the last
    save used to overwrite the other's totals (2.0 instead of 4.0)."""
    path = tmp_path / "usage.json"
    wake, web = usage.UsageMeter(path), usage.UsageMeter(path)
    wake.record(MAIN, make_usage(1_000_000))
    web.record(MAIN, make_usage(1_000_000))
    assert web.month_usd() == pytest.approx(4.0)
    assert usage.UsageMeter(path).month_usd() == pytest.approx(4.0)
    wake.record(MAIN, make_usage(1_000_000))
    assert usage.UsageMeter(path).month_usd() == pytest.approx(6.0)
    assert not (tmp_path / "usage.json.lock").exists()


def test_a_held_lock_defers_the_save_and_a_stale_one_is_taken_over(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(usage, "LOCK_WAIT_SECONDS", 0)
    path = tmp_path / "usage.json"
    lock = tmp_path / "usage.json.lock"
    meter = usage.UsageMeter(path)
    lock.write_text("1")  # another process is saving
    meter.record(MAIN, make_usage(1_000_000))
    assert not path.exists()  # deferred, not lost
    old = time.time() - usage.LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))  # that process died
    meter.record(MAIN, make_usage(1_000_000))
    assert usage.UsageMeter(path).month_usd() == pytest.approx(4.0)


def test_a_lock_being_deleted_on_windows_is_waited_for(tmp_path, monkeypatch):
    """Windows answers O_EXCL with PermissionError while a lock file is still
    being deleted: wait for it; never stop saving for good."""
    import os

    real_open, failed = os.open, []

    def flaky_open(path, flags, *args):
        if str(path).endswith(".lock") and not failed:
            failed.append(path)
            raise PermissionError(13, "delete pending")
        return real_open(path, flags, *args)

    monkeypatch.setattr(usage.os, "open", flaky_open)
    path = tmp_path / "usage.json"
    meter = usage.UsageMeter(path)
    meter.record(MAIN, make_usage(1_000_000))
    meter.record(MAIN, make_usage(1_000_000))
    assert failed and meter.path == path
    assert usage.UsageMeter(path).month_usd() == pytest.approx(4.0)


def test_titles_tutor_and_lecture_calls_are_metered(fake_client):
    from assistant.history import ask_title
    from assistant.lecture import summarize
    from assistant.tutor import TutorSession
    from tests.test_lecture import NOTES

    fake_client.queue = [
        make_response(text_block('{"title": "Dentist", "summary": "s"}'),
                      usage=make_usage(100, 10)),
        make_response(text_block("Try the chain rule."), usage=make_usage(200, 20)),
        make_response(tool_use_block("save_lecture_notes", NOTES), stop_reason="tool_use",
                      usage=make_usage(300, 30)),
    ]
    fake_client.with_options.return_value = fake_client  # as the SDK's client
    assert ask_title(fake_client, [{"role": "user", "text": "move the dentist"}],
                     FAST) == ("Dentist", "s")
    TutorSession(client=fake_client, model=MAIN).ask("how do I start?")
    summarize(fake_client, MAIN, "the transcript")
    month = usage.METER.months[datetime.now().strftime("%Y-%m")]
    assert (month[FAST]["calls"], month[FAST]["input"]) == (1, 100)
    assert (month[MAIN]["calls"], month[MAIN]["input"]) == (2, 500)


def test_a_web_session_types_its_server_tools_for_the_routed_model():
    """The web app's per-session registry (SessionTools) takes the model too,
    in a health check's session as well, so a Haiku turn never gets Sonnet's
    web tool versions."""
    from assistant.tools import ToolRegistry
    from assistant.tools.web import tool_versions
    from assistant.webapp import SessionTools

    shared = ToolRegistry()
    shared.server_tools = [{"type": "web_search_20260209", "name": "web_search"}]
    tools = SessionTools(shared, confirm=lambda q: False, emit=lambda e: None)
    haiku = tool_versions(FAST)[0]
    for check in (False, True):
        tools.check_mode = check
        assert tools.schemas(FAST)[-1]["type"] == haiku
        assert tools.schemas()[-1]["type"] == "web_search_20260209"
