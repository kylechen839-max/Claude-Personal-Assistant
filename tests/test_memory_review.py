"""Memory review (memory_review.py, memory_web.py): listing what the
assistant saved by week, deleting one or a week (exact ids), the owner-only
Memory screen API, the review_memory voice tool, and the weekly nudge.

Offline: a fake Pinecone index (tests.test_notes.FakeIndex: list by id
prefix in pages, fetch, delete) behind a real PineconeMemory, and the
mocked Anthropic client."""

import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from assistant import cli
from assistant.chat import ClaudeChat
from assistant.config import DEFAULTS, load_config
from assistant.memory import PineconeMemory
from assistant.memory_review import MemoryReview
from assistant.tools import ToolRegistry
from assistant.tools.notes import QuickNotes
from tests.conftest import (make_response, request_text, sent_text, text_block,
                            tool_use_block)
from tests.test_notes import FakeIndex

NS = "voice-assistant"
TZ = "America/New_York"
# Tuesday 2026-10-20, 11:00 in New York: this week began Monday 2026-10-19
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc).timestamp()
SECRET = "Kyle hates cilantro"  # note text that must never reach INFO logs

SEED = [
    # (namespace, id, metadata)
    (NS, "voice-20261020T140000-aaaaaa",
     {"text": f"2026-10-20: {SECRET}.", "kind": "conversation_summary",
      "created_at": "2026-10-20T14:00:00Z", "untrusted": True,
      "from_user_only": True}),
    (NS, "note-20261019T130000-bbbbbb",
     {"text": "Lab report needs error bars", "kind": "note",
      "created_at": "2026-10-19T13:00:00Z", "tags": ["lab"]}),
    # Monday 02:00 UTC is still Sunday evening in New York: last week
    (NS, "note-20261019T020000-cccccc",
     {"text": "Rent is due on the first", "kind": "note",
      "created_at": "2026-10-19T02:00:00Z"}),
    (NS, "voice-20261005T100000-dddddd",
     {"text": "2026-10-05: Kyle starts the gym plan.", "kind": "conversation_summary",
      "created_at": "2026-10-05T10:00:00Z"}),
    # an old vector with no timestamp: "date unknown", listed last
    (NS, "voice-legacy-eeeeee", {"summary": "Old summary without a date"}),
    # not the assistant's: another id in its namespace, and another namespace
    (NS, "project-chunk-1", {"text": "Thesis outline", "created_at": "2026-10-20T10:00:00Z"}),
    ("projects", "voice-20261020T120000-ffffff",
     {"text": "Someone else's", "created_at": "2026-10-20T12:00:00Z"}),
]


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def make_memory():
    index = FakeIndex()
    for ns, vid, meta in SEED:
        index.upsert([{"id": vid, "values": [1.0, 0.0], "metadata": dict(meta)}], ns)
    return PineconeMemory(pc=MagicMock(), index=index, namespace=NS)


@pytest.fixture
def memory():
    return make_memory()


@pytest.fixture
def review(memory):
    return MemoryReview(memory, timezone=TZ, clock=Clock())


def ids_of(group):
    return [n["id"] for n in group["notes"]]


# ---------- listing ----------
def test_lists_own_notes_newest_first_grouped_by_local_week(review):
    data = review.weeks()
    assert data["this_week"] == "2026-10-19" and data["last_week"] == "2026-10-12"
    assert data["count"] == 5 and data["truncated"] is False
    assert [g["week"] for g in data["weeks"]] == ["2026-10-19", "2026-10-12",
                                                 "2026-10-05", None]
    assert ids_of(data["weeks"][0]) == ["voice-20261020T140000-aaaaaa",
                                        "note-20261019T130000-bbbbbb"]
    assert ids_of(data["weeks"][1]) == ["note-20261019T020000-cccccc"]
    assert ids_of(data["weeks"][3]) == ["voice-legacy-eeeeee"]
    listed = [i for g in data["weeks"] for i in ids_of(g)]
    assert "project-chunk-1" not in listed and "voice-20261020T120000-ffffff" not in listed


def test_entries_have_kind_date_and_untrusted_badge(review):
    weeks = review.weeks()["weeks"]
    summary, note = weeks[0]["notes"]
    assert summary == {"id": "voice-20261020T140000-aaaaaa", "kind": "summary",
                       "created_at": "2026-10-20T14:00:00Z", "date": "2026-10-20",
                       "week": "2026-10-19", "untrusted": True,
                       "text": f"2026-10-20: {SECRET}."}
    assert note["kind"] == "note" and note["untrusted"] is False
    assert weeks[1]["notes"][0]["date"] == "2026-10-18"  # Sunday evening in New York
    legacy = weeks[3]["notes"][0]
    assert legacy["created_at"] is None and legacy["week"] is None
    assert legacy["text"] == "Old summary without a date"  # from a text_fields field


def test_long_lists_are_cut_newest_first(memory, monkeypatch):
    """The cap keeps the newest by save time across both prefixes: a quick
    note newer than some summaries survives, an undated id goes first."""
    monkeypatch.setattr("assistant.memory_review.MAX_NOTES", 3)
    data = MemoryReview(memory, timezone=TZ, clock=Clock()).weeks()
    assert data["truncated"] is True and data["count"] == 3
    kept = [n["id"] for g in data["weeks"] for n in g["notes"]]
    assert kept == ["voice-20261020T140000-aaaaaa", "note-20261019T130000-bbbbbb",
                    "note-20261019T020000-cccccc"]
    # dropped: an older summary and the undated legacy one
    assert "voice-20261005T100000-dddddd" not in kept and "voice-legacy-eeeeee" not in kept


def test_lists_through_the_real_sdk_list_pages():
    """With the pinned SDK (pinecone 7+), Index.list() yields ListResponse
    pages, not lists of ids; the screen used to show nothing."""
    from tests.test_notes import real_index

    meta = {vid: m for ns, vid, m in SEED if ns == NS}
    pages = {"voice-": [i for i in meta if i.startswith("voice-")],
             "note-": [i for i in meta if i.startswith("note-")]}
    memory = PineconeMemory(pc=MagicMock(), index=real_index(pages, meta), namespace=NS)
    review = MemoryReview(memory, timezone=TZ, clock=Clock())
    assert type(next(iter(memory.index.list(prefix="voice-", namespace=NS)))).__name__ \
        == "ListResponse"
    data = review.weeks()
    assert data["count"] == 5
    assert [n["id"] for n in data["weeks"][0]["notes"]] == [
        "voice-20261020T140000-aaaaaa", "note-20261019T130000-bbbbbb"]
    assert review.review_memory()["count"] == 2


def test_fetches_in_batches(memory, monkeypatch):
    monkeypatch.setattr("assistant.memory_review.FETCH_BATCH", 2)
    calls = []
    fetch = memory.index.fetch
    memory.index.fetch = lambda ids, namespace: calls.append(list(ids)) or fetch(ids, namespace)
    assert MemoryReview(memory, timezone=TZ, clock=Clock()).weeks()["count"] == 5
    assert all(len(c) <= 2 for c in calls) and len(calls) == 3


# ---------- deleting ----------
def test_delete_one(review, memory):
    assert review.delete("note-20261019T130000-bbbbbb") is True
    assert (NS, "note-20261019T130000-bbbbbb") not in memory.index.vectors
    assert review.delete("note-20261019T130000-bbbbbb") is False  # already gone
    # never anything that isn't the assistant's own
    assert review.delete("project-chunk-1") is False
    assert (NS, "project-chunk-1") in memory.index.vectors
    assert ("projects", "voice-20261020T120000-ffffff") in memory.index.vectors


def test_delete_week_deletes_exactly_the_ids_shown(review, memory):
    shown = ids_of(review.weeks()["weeks"][0])
    # saved after the page loaded: same week, but never shown
    memory.index.upsert([{"id": "note-20261020T150000-zzzzzz", "values": [1.0, 0.0],
                          "metadata": {"text": "new", "kind": "note",
                                       "created_at": "2026-10-20T15:00:00Z"}}], NS)
    # an id from another week, and a foreign one, sent along: ignored
    deleted = review.delete_week("2026-10-19", shown + ["voice-20261005T100000-dddddd",
                                                        "project-chunk-1"])
    assert deleted == shown
    left = {vid for (ns, vid) in memory.index.vectors if ns == NS}
    assert not left & set(shown)
    assert {"note-20261020T150000-zzzzzz", "voice-20261005T100000-dddddd",
            "project-chunk-1"} <= left


def test_delete_week_of_undated_notes(review, memory):
    assert review.delete_week("unknown", ["voice-legacy-eeeeee"]) == ["voice-legacy-eeeeee"]
    assert (NS, "voice-legacy-eeeeee") not in memory.index.vectors
    with pytest.raises(ValueError):
        review.delete_week("last tuesday", ["voice-legacy-eeeeee"])


# ---------- voice tool ----------
def test_review_memory_tool_reads_this_week(review):
    result = review.review_memory()
    assert result["count"] == 2 and result["week_of"] == "Monday, October 19"
    assert [n["id"] for n in result["notes"]] == ["voice-20261020T140000-aaaaaa",
                                                 "note-20261019T130000-bbbbbb"]
    assert result["notes"][0]["day"] == "Tuesday, October 20"
    assert result["untrusted_content"] is True  # one was saved from untrusted text
    last = review.review_memory("last")
    assert [n["id"] for n in last["notes"]] == ["note-20261019T020000-cccccc"]
    assert "untrusted_content" not in last


def test_review_tool_is_read_only_and_private(review):
    [tool] = review.tools()
    assert tool.name == "review_memory" and tool.private
    assert tool.confirm_prompt is None and tool.confirm_if_untrusted is None


def test_forget_that_asks_first_through_delete_note(fake_client, memory):
    """'What did you remember this week?' then 'forget the cilantro one':
    review_memory, then delete_note, which asks out loud; a no deletes
    nothing, and the review result taints the turn (untrusted note)."""
    review = MemoryReview(memory, timezone=TZ, clock=Clock())
    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(*QuickNotes(memory, timezone=TZ).tools(), *review.tools())
    vid = "voice-20261020T140000-aaaaaa"
    fake_client.queue += [
        make_response(tool_use_block("review_memory", {}), stop_reason="tool_use"),
        make_response(tool_use_block("delete_note", {"note_id": vid}, id="toolu_2"),
                      stop_reason="tool_use"),
        make_response(text_block("OK, I kept it."))]
    chat = ClaudeChat(client=fake_client, model="m", tools=reg)
    assert chat.ask("What did you remember about me this week? Forget the cilantro one.")
    assert asked == [f"Do you want me to delete the note: 2026-10-20: {SECRET}.?"]
    assert (NS, vid) in memory.index.vectors
    assert chat.tainted()  # an untrusted note was read back


def test_review_tool_is_registered_with_memory_only():
    config = load_config(None, None)
    reg = ToolRegistry()
    cli.register_memory_review(reg, cli.build_memory_review(config, None))
    assert not reg.has("review_memory")
    review = cli.build_memory_review(config, make_memory())
    cli.register_memory_review(reg, review)
    assert reg.has("review_memory") and reg.is_private("review_memory")
    assert review.weekly_review is False  # off by default


# ---------- weekly nudge ----------
def test_nudge_is_off_by_default(memory, tmp_path):
    assert DEFAULTS["memory"]["weekly_review"] is False
    memory._index = MagicMock()
    review = MemoryReview(memory, timezone=TZ, clock=Clock(),
                          state_file=tmp_path / "review.json")
    assert review.nudge() is None
    memory.index.list.assert_not_called()  # not even a Pinecone call
    assert not (tmp_path / "review.json").exists()


def test_nudge_once_a_week(memory, tmp_path):
    clock = Clock()
    state = tmp_path / "data" / "review.json"
    review = MemoryReview(memory, timezone=TZ, clock=clock, weekly_review=True,
                          state_file=state)
    text = review.nudge()
    assert "I saved 2 notes this week, want to review them?" in text
    assert "review_memory" in text and SECRET not in text
    assert json.loads(state.read_text()) == {"last_nudge": NOW}
    assert review.nudge() is None  # once
    # a restart remembers it (the state file)
    again = MemoryReview(memory, timezone=TZ, clock=clock, weekly_review=True,
                         state_file=state)
    clock.now += 6 * 86400
    assert again.nudge() is None
    clock.now += 2 * 86400  # Wednesday of the next week, with no notes yet
    assert again.nudge() is None
    memory.index.upsert([{"id": "note-20261028T150000-yyyyyy", "values": [1.0, 0.0],
                          "metadata": {"text": "x", "kind": "note",
                                       "created_at": "2026-10-28T15:00:00Z"}}], NS)
    clock.now += 2 * 3600  # looks again after an hour
    assert "I saved 1 note this week" in again.nudge()


def test_nudge_without_notes_this_week_waits(memory, tmp_path):
    clock = Clock(datetime(2026, 11, 3, 15, tzinfo=timezone.utc).timestamp())
    review = MemoryReview(memory, timezone=TZ, clock=clock, weekly_review=True,
                          state_file=tmp_path / "review.json")
    assert review.nudge() is None
    assert not (tmp_path / "review.json").exists()
    memory._index = MagicMock()
    assert review.nudge() is None  # checked a minute ago: no Pinecone call
    memory.index.list.assert_not_called()


def test_nudge_never_blocks_a_reply(memory, tmp_path):
    memory._index = MagicMock()
    memory._index.list.side_effect = ConnectionError("Pinecone is down")
    review = MemoryReview(memory, timezone=TZ, clock=Clock(), weekly_review=True,
                          state_file=tmp_path / "review.json")
    assert review.nudge() is None


def test_first_turn_of_a_conversation_carries_the_nudge(fake_client, memory, tmp_path):
    review = MemoryReview(memory, timezone=TZ, clock=Clock(), weekly_review=True,
                          state_file=tmp_path / "review.json")
    chat = ClaudeChat(client=fake_client, model="m")
    chat.review_nudge = review.nudge
    fake_client.queue += [make_response(text_block("I saved 2 notes this week, want "
                                                   "to review them? It's sunny.")),
                          make_response(text_block("Sure."))]
    chat.ask("What's the weather?")
    chat.ask("Thanks")
    first = request_text(fake_client.calls[0])
    # the offer is for the first reply only: not replayed in later requests
    second = sent_text(fake_client.calls[1])
    assert "I saved 2 notes this week, want to review them?" in first
    assert "Weekly memory review" not in second
    chat.reset()  # a new conversation the same week: no second nudge
    fake_client.queue.append(make_response(text_block("Hi.")))
    chat.ask("Hi")
    assert "Weekly memory review" not in sent_text(fake_client.calls[-1])


def test_build_chat_wires_the_nudge(monkeypatch, memory):
    monkeypatch.setattr(cli.anthropic, "Anthropic", MagicMock())
    config = load_config(None, None)
    review = cli.build_memory_review(config, memory)
    chat = cli.build_chat(config, memory=memory, review=review)
    assert chat.review_nudge == review.nudge
    assert cli.build_chat(config).review_nudge is None


# ---------- logs ----------
def test_no_note_text_at_info(review, memory, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    review.weekly_review, review.state_file = True, tmp_path / "r.json"
    review.weeks()
    review.nudge()
    review.review_memory()
    review.delete("voice-20261020T140000-aaaaaa")
    review.delete_week("2026-10-12", ["note-20261019T020000-cccccc"])
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("listed 5 notes" in m for m in info)
    assert any("deleted voice-20261020T140000-aaaaaa" in m for m in info)
    for message in info:
        for text in (SECRET, "Rent is due", "error bars", "gym plan"):
            assert text not in message, message


# ---------- the Memory screen's API (memory_web.py) ----------
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import webapp  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.test_family import env, owner  # noqa: E402,F401  (fixtures)
from tests.test_webapp import BASE, TOKEN, FakeTranscriber, login  # noqa: E402
from tests.test_webapp import Clock as WebClock  # noqa: E402

ROUTES = [("GET", "/api/memory"), ("POST", "/api/memory/note-x/delete"),
          ("POST", "/api/memory/delete-week")]


@pytest.fixture
def web(fake_client, memory):
    review = MemoryReview(memory, timezone=TZ, clock=Clock())

    def chat_factory(registry):
        return ClaudeChat(client=fake_client, model="test-model", tools=registry)

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(),
                                    clock=WebClock(), memory=memory,
                                    memory_review=review)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    return TestClient(app, base_url=BASE), memory


def test_memory_routes_need_the_owner_session(web):
    client, memory = web
    for method, path in ROUTES:
        r = client.request(method, path, json={"week": "unknown", "ids": ["note-x"]})
        assert r.status_code == 401, (method, path)
    login(client)
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    r = client.post("/api/memory/voice-legacy-eeeeee/delete", json={}, headers=evil)
    assert r.status_code == 403
    assert (NS, "voice-legacy-eeeeee") in memory.index.vectors


def test_owner_lists_and_state_says_memory(web):
    client, _ = web
    login(client)
    assert client.get("/api/state").json()["memory"] is True
    r = client.get("/api/memory")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert [g["week"] for g in data["weeks"]] == ["2026-10-19", "2026-10-12",
                                                 "2026-10-05", None]
    assert data["weeks"][0]["notes"][0]["untrusted"] is True


def test_owner_deletes_one_by_post(web):
    client, memory = web
    login(client)
    r = client.post("/api/memory/note-20261019T130000-bbbbbb/delete", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert (NS, "note-20261019T130000-bbbbbb") not in memory.index.vectors
    assert client.post("/api/memory/note-20261019T130000-bbbbbb/delete",
                       json={}).status_code == 404
    assert client.post("/api/memory/project-chunk-1/delete", json={}).status_code == 404
    assert (NS, "project-chunk-1") in memory.index.vectors
    # DELETE isn't a route: deletes are POST
    assert client.request("DELETE", "/api/memory/voice-legacy-eeeeee", content=b"{}",
                          headers={"Content-Type": "application/json"}).status_code in (404, 405)


def test_owner_deletes_a_week_by_exact_ids(web):
    client, memory = web
    login(client)
    week = client.get("/api/memory").json()["weeks"][0]
    shown = [n["id"] for n in week["notes"]]
    memory.index.upsert([{"id": "note-20261020T150000-zzzzzz", "values": [1.0, 0.0],
                          "metadata": {"text": "new", "kind": "note",
                                       "created_at": "2026-10-20T15:00:00Z"}}], NS)
    r = client.post("/api/memory/delete-week", json={"week": "2026-10-19", "ids": shown})
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": 2, "ids": shown}
    assert (NS, "note-20261020T150000-zzzzzz") in memory.index.vectors
    for bad in ({"week": "2026-10-19"}, {"week": "2026-10-19", "ids": []},
                {"week": "soon", "ids": shown}, {"week": "2026-10-19", "ids": [1]},
                {"week": "2026-10-19", "ids": ["note-x"] * 301}, ["x"]):
        assert client.post("/api/memory/delete-week", json=bad).status_code == 400, bad


def test_memory_off_is_not_found(fake_client):
    assistant = webapp.WebAssistant(
        lambda r: ClaudeChat(client=fake_client, tools=r), FakeTranscriber(),
        PrintTTS(), clock=WebClock())
    client = TestClient(webapp.create_app(assistant, TOKEN, reap_interval=3600),
                        base_url=BASE)
    login(client)
    assert client.get("/api/state").json()["memory"] is False
    assert client.get("/api/memory").status_code == 404
    assert client.post("/api/memory/note-x/delete", json={}).status_code == 404


def test_pinecone_down_is_a_502_without_details(web, caplog):
    client, memory = web
    login(client)
    memory.index.list = MagicMock(side_effect=ConnectionError(f"boom {SECRET}"))
    r = client.get("/api/memory")
    assert r.status_code == 502 and SECRET not in r.text


def test_web_routes_log_no_note_text_at_info(web, caplog):
    client, _ = web
    caplog.set_level(logging.DEBUG)
    login(client)
    week = client.get("/api/memory").json()["weeks"][0]
    client.post("/api/memory/delete-week",
                json={"week": week["week"], "ids": [n["id"] for n in week["notes"]]})
    client.post("/api/memory/voice-legacy-eeeeee/delete", json={})
    for r in caplog.records:
        if r.levelno >= logging.INFO:
            assert SECRET not in r.getMessage() and "Old summary" not in r.getMessage()


def test_family_members_get_404_and_never_touch_memory(env, owner):  # noqa: F811
    memory = make_memory()
    env.assistant.memory_review = MemoryReview(memory, timezone=TZ, clock=Clock())
    assert owner.get("/api/memory").status_code == 200
    assert owner.get("/api/state").json()["memory"] is True
    mom, _ = env.member(owner)
    assert mom.get("/api/state").json()["memory"] is False
    memory.index.list = MagicMock(side_effect=AssertionError("listed for family"))
    assert mom.get("/api/memory").status_code == 404
    assert mom.post("/api/memory/voice-legacy-eeeeee/delete", json={}).status_code == 404
    assert mom.post("/api/memory/delete-week",
                    json={"week": "unknown", "ids": ["voice-legacy-eeeeee"]}).status_code == 404
    assert (NS, "voice-legacy-eeeeee") in memory.index.vectors


def test_nudge_is_asked_for_only_at_the_start_of_a_conversation(fake_client):
    calls = []
    chat = ClaudeChat(client=fake_client, model="m")
    chat.review_nudge = lambda: calls.append(1) or "OFFER A REVIEW"
    fake_client.queue += [make_response(text_block("a")), make_response(text_block("b"))]
    chat.ask("one")
    chat.ask("two")
    assert len(calls) == 1
    assert "OFFER A REVIEW" in request_text(fake_client.calls[0])
    assert "OFFER A REVIEW" not in sent_text(fake_client.calls[1])
    chat.review_nudge = MagicMock(side_effect=RuntimeError("down"))
    chat.reset()
    fake_client.queue.append(make_response(text_block("c")))
    assert chat.ask("three") == "c"  # a failing nudge never blocks a reply
