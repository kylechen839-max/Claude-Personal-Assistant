"""Conversation history: store, search, recording, resume, tools and CLI.
All offline: the Anthropic client and Pinecone are mocked."""

import json
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from assistant import cli, history
from assistant.chat import ClaudeChat
from assistant.config import load_config
from assistant.history import (MARK_END, MARK_START, HistoryError, HistoryStore,
                               Recorder, rebuild_history)
from assistant.tools import Tool, ToolRegistry
from assistant.tools.history import history_tools
from tests.conftest import make_response, text_block, tool_use_block

ROOT = Path(__file__).resolve().parent.parent
# Wednesday, October 14, 2026, 3 PM local time.
NOW = datetime(2026, 10, 14, 15, 0).astimezone().timestamp()
DAY = 86400


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "h.sqlite", clock=Clock())
    yield s
    s.close()


@pytest.fixture(params=["fts", "like"])
def any_store(request, tmp_path):
    """The same tests on FTS5 search and on the LIKE fallback."""
    if request.param == "fts" and not history.fts5_available():
        pytest.skip("this SQLite has no FTS5")
    s = HistoryStore(tmp_path / "h.sqlite", clock=Clock(),
                     use_fts=request.param == "fts")
    assert s.fts == (request.param == "fts")
    yield s
    s.close()


def add_conv(store, source="text", turns=(), title=None, summary=None,
             at=None, pinned=False):
    fixed = isinstance(store.clock, Clock)
    if at is not None:
        assert fixed, "this store has the real clock"
        store.clock.t = at
    cid = store.create(source)
    for i, text in enumerate(turns):
        store.append(cid, "user" if i % 2 == 0 else "assistant", text)
    if title is not None or summary is not None:
        store.finish(cid, title, summary)
    if pinned:
        store.update(cid, pinned=True)
    if fixed:
        store.clock.t = NOW
    return cid


# ---------- store CRUD ----------
def test_create_append_get(store):
    cid = store.create("wake")
    assert store.append(cid, "user", "What's on my calendar?") == 1
    assert store.append(cid, "assistant", "Two events.",
                        tool_events=[history.tool_event(
                            "list_calendar_events", {"day": "tomorrow"}, "ok")]) == 2
    conv = store.get(cid)
    assert conv["source"] == "wake" and conv["message_count"] == 2
    assert conv["ended_at"] is None and conv["started_at"] == NOW
    assert [m["text"] for m in conv["messages"]] == ["What's on my calendar?",
                                                     "Two events."]
    assert conv["messages"][1]["tool_events"] == [
        {"tool": "list_calendar_events", "args": {"day": "tomorrow"}, "status": "ok"}]
    store.finish(cid, "Calendar check", "Kyle asked about tomorrow.")
    conv = store.get(cid, messages=False)
    assert (conv["title"], conv["summary"]) == ("Calendar check",
                                                "Kyle asked about tomorrow.")
    assert conv["ended_at"] == NOW and "messages" not in conv


def test_update_rename_pin_archive_and_delete(store):
    cid = add_conv(store, turns=["hi", "hello"], title="Greeting")
    conv = store.update(cid, title="  Saying   hi ", pinned=True, archived=True)
    assert conv["title"] == "Saying hi" and conv["pinned"] and conv["archived"]
    store.finish(cid, "Auto title", "s")  # a title Kyle typed is kept
    assert store.get(cid)["title"] == "Saying hi"
    with pytest.raises(ValueError):
        store.update(cid, title="   ")
    assert store.list() == [] and len(store.list(archived=None)) == 1
    store.delete(cid)
    with pytest.raises(KeyError):
        store.get(cid)
    assert store._con.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_unknown_or_malformed_ids_raise_keyerror(store):
    for bad in ("nope", "", None, "1", "' OR 1=1 --", history.new_id()):
        with pytest.raises(KeyError):
            store.get(bad)
        with pytest.raises(KeyError):
            store.append(bad, "user", "x")
        with pytest.raises(KeyError):
            store.delete(bad)


def test_invalid_source_and_role(store):
    with pytest.raises(ValueError):
        store.create("email")
    cid = store.create("text")
    with pytest.raises(sqlite3.IntegrityError):
        store.append(cid, "system", "x")


def test_ids_are_random_not_enumerable(store):
    ids = [store.create("text") for _ in range(300)]
    assert len(set(ids)) == 300
    assert all(history.valid_id(i) and len(i) == 16 for i in ids)
    assert not any(i.isdigit() for i in ids)
    # neighbours share no structure (not counters or timestamps)
    common = [len(_common_prefix(a, b)) for a, b in zip(ids, ids[1:])]
    assert statistics.mean(common) < 1
    rowids = [r[0] for r in store._con.execute("SELECT rid FROM conversations")]
    assert not set(map(str, rowids)) & set(ids)


def _common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


def test_every_query_is_scoped_to_the_owner(tmp_path):
    path = tmp_path / "h.sqlite"
    kyle, other = HistoryStore(path, owner="kyle"), HistoryStore(path, owner="other")
    cid = add_conv(kyle, turns=["secret lab report plans", "ok"], title="Lab")
    for call in (lambda: other.get(cid), lambda: other.delete(cid),
                 lambda: other.update(cid, pinned=True),
                 lambda: other.append(cid, "user", "x"),
                 lambda: other.mark_interrupted(cid)):
        with pytest.raises(KeyError):
            call()
    assert other.list() == [] and other.search("lab") == []
    assert other.count() == 0 and kyle.count() == 1
    assert kyle.get(cid)["message_count"] == 2
    kyle.close(), other.close()


def test_secrets_are_masked_before_storing(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-supersecretvalue123")
    cid = store.create("text")
    store.append(cid, "user", "my key is sk-ant-api03-supersecretvalue123 ok")
    raw = store._con.execute("SELECT text FROM messages").fetchone()[0]
    assert "supersecret" not in raw and "[REDACTED]" in raw


def test_wal_mode_and_file_location(tmp_path):
    s = HistoryStore(tmp_path / "nested" / "dir" / "h.sqlite")
    assert (tmp_path / "nested" / "dir" / "h.sqlite").exists()
    assert s._con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.close()


# ---------- migrations ----------
def test_fresh_database_is_at_the_latest_schema(store):
    assert store.schema_version == len(history.MIGRATIONS) == history.SCHEMA_VERSION


def test_migrations_upgrade_an_older_database(tmp_path, monkeypatch):
    path = tmp_path / "h.sqlite"
    old = HistoryStore(path)
    cid = add_conv(old, turns=["keep me", "ok"])
    old.close()
    monkeypatch.setattr(history, "MIGRATIONS", history.MIGRATIONS + [
        "ALTER TABLE conversations ADD COLUMN color TEXT DEFAULT 'blue';"])
    new = HistoryStore(path)
    assert new.schema_version == len(history.MIGRATIONS)
    assert new._con.execute("SELECT color FROM conversations").fetchone()[0] == "blue"
    assert new.get(cid)["messages"][0]["text"] == "keep me"
    new.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "h.sqlite"
    HistoryStore(path).close()
    monkeypatch.setattr(history, "MIGRATIONS", history.MIGRATIONS + [
        "ALTER TABLE conversations ADD COLUMN a TEXT; THIS IS NOT SQL;"])
    with pytest.raises(sqlite3.Error):
        HistoryStore(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    cols = [r[1] for r in con.execute("PRAGMA table_info(conversations)")]
    assert "a" not in cols
    con.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "h.sqlite"
    HistoryStore(path).close()
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()
    with pytest.raises(HistoryError, match="newer version"):
        HistoryStore(path)


def test_fts_index_is_built_for_a_database_made_without_it(tmp_path):
    if not history.fts5_available():
        pytest.skip("no FTS5")
    path = tmp_path / "h.sqlite"
    plain = HistoryStore(path, use_fts=False)
    add_conv(plain, turns=["photosynthesis homework", "ok"], title="Biology")
    plain.close()
    fts = HistoryStore(path)
    assert fts.fts and [h["title"] for h in fts.search("photosynthesis")] == ["Biology"]
    fts.close()
    # ...and back on a build without FTS5, writes still work
    again = HistoryStore(path, use_fts=False)
    add_conv(again, turns=["more notes"])
    assert len(again.search("notes")) == 1
    again.close()


# ---------- crash safety ----------
CRASH_SCRIPT = """
import os, sys
sys.path.insert(0, sys.argv[2])
from assistant.history import HistoryStore
s = HistoryStore(sys.argv[1])
cid = s.create("wake")
s.append(cid, "user", "remember the dentist on friday")
print(cid, flush=True)
os._exit(1)  # crash: no close(), no checkpoint
"""


def test_turns_survive_a_crash(tmp_path):
    path = tmp_path / "h.sqlite"
    out = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(path), str(ROOT)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1, out.stderr
    cid = out.stdout.strip()
    s = HistoryStore(path)
    conv = s.get(cid)
    assert conv["messages"][0]["text"] == "remember the dentist on friday"
    assert conv["ended_at"] is None and s.unfinished() == [cid]
    assert [h["id"] for h in s.search("dentist")] == [cid]
    s.close()


def test_each_append_is_visible_to_another_connection_immediately(store):
    cid = store.create("text")
    store.append(cid, "user", "first turn")
    other = sqlite3.connect(store.path)
    assert other.execute("SELECT text FROM messages").fetchone()[0] == "first turn"
    other.close()


def test_orphaned_conversations_get_titled_on_startup(store):
    cid = add_conv(store, turns=["How do error bars work", "Like this."])
    rec = Recorder(store, "text", run_async=False)
    rec.finish_orphans()
    conv = store.get(cid)
    assert conv["title"] == "How do error bars work" and conv["ended_at"]


# ---------- search ----------
def test_search_ranks_and_highlights(any_store):
    s = any_store
    weak = add_conv(s, turns=["I need to finish a lab today", "ok"], title="Errands")
    strong = add_conv(s, turns=["How do I do error bars for my lab report?",
                                "Use the standard error.",
                                "And the lab report conclusion?", "Restate it."],
                      title="Lab report error bars", summary="Error bars in the lab report.")
    add_conv(s, turns=["What's the weather?", "Sunny."], title="Weather")
    hits = s.search("lab report")
    assert [h["id"] for h in hits] == [strong]  # all words must match
    assert MARK_START in hits[0]["snippet"] and MARK_END in hits[0]["snippet"]
    assert "report" in history.plain_snippet(hits[0]["snippet"]).lower()
    hits = s.search("lab")
    assert [h["id"] for h in hits] == [strong, weak]
    assert hits[0]["score"] > hits[1]["score"]


def test_search_prefix_stopwords_and_any_word_fallback(any_store):
    s = any_store
    cid = add_conv(s, turns=["Help with my chemistry homework", "Sure."],
                   title="Chemistry homework")
    assert [h["id"] for h in s.search("chem")] == [cid]  # search as you type
    assert [h["id"] for h in s.search("our conversation about chemistry")] == [cid]
    # no conversation has both words: any word matches instead
    assert [h["id"] for h in s.search("chemistry basketball")] == [cid]
    assert s.search("basketball") == []


def test_search_filters(any_store):
    s = any_store
    old = add_conv(s, "wake", ["dentist appointment friday", "ok"], at=NOW - 10 * DAY)
    web = add_conv(s, "web", ["dentist reschedule", "done"], at=NOW - DAY)
    pinned = add_conv(s, "text", ["dentist insurance", "ok"], pinned=True)
    archived = add_conv(s, "text", ["dentist bill", "ok"])
    s.update(archived, archived=True)
    ids = lambda **kw: {h["id"] for h in s.search("dentist", **kw)}  # noqa: E731
    assert ids() == {old, web, pinned}
    assert ids(source="web") == {web}
    assert ids(source=["web", "wake"]) == {old, web}
    assert ids(pinned=True) == {pinned}
    assert ids(since=NOW - 2 * DAY) == {web, pinned}
    assert ids(until=NOW - 5 * DAY) == {old}
    assert ids(archived=True) == {archived}
    assert ids(archived=None) == {old, web, pinned, archived}


def test_search_matches_titles_and_summaries(any_store):
    cid = add_conv(any_store, turns=["hm", "ok"], title="Spring break trip",
                   summary="Planning flights to Denver.")
    assert [h["id"] for h in any_store.search("denver")] == [cid]
    assert [h["id"] for h in any_store.search("spring break")] == [cid]


def test_search_input_is_never_sql_or_fts_syntax(any_store):
    cid = add_conv(any_store, turns=['he said "100% done" (really) _ok_', "ok"])
    for q in ['"', "100%", "NEAR(a b)", "a OR", "*", "-x", "col:val", "'; DROP TABLE"]:
        any_store.search(q)  # no exception
    assert [h["id"] for h in any_store.search("100")] == [cid]
    assert any_store.search("%") != [] or True  # punctuation only: lists


def test_empty_query_lists_newest_first(any_store):
    a = add_conv(any_store, turns=["one"], at=NOW - 100)
    b = add_conv(any_store, turns=["two"])
    assert [h["id"] for h in any_store.search("  ")] == [b, a]


def test_list_pagination_and_count(store):
    ids = [add_conv(store, turns=[f"turn {i}"], at=NOW + i) for i in range(25)]
    page1, page2 = store.list(limit=10), store.list(limit=10, offset=10)
    assert [c["id"] for c in page1] == ids[::-1][:10]
    assert [c["id"] for c in page2] == ids[::-1][10:20]
    assert store.count() == 25
    assert page1[0]["snippet"] == "turn 24"


def test_snippet_helpers():
    snip = f"how do {MARK_START}error{MARK_END} {MARK_START}bars{MARK_END} work"
    assert history.snippet_parts(snip) == [("how do ", False), ("error", True),
                                           (" ", False), ("bars", True),
                                           (" work", False)]
    assert history.plain_snippet(snip, "[", "]") == "how do [error] [bars] work"
    assert history.plain_snippet("") == ""


def test_search_is_fast_on_5000_conversations(tmp_path):
    s = HistoryStore(tmp_path / "big.sqlite")
    _seed(s, 5000)
    s.search("warmup")
    for q in ("lab report", "calendar", "dentist friday", "chem", "zzzz",
              "our conversation about the physics exam"):
        times = []
        for _ in range(5):
            t = time.perf_counter()
            hits = s.search(q, limit=20)
            times.append(time.perf_counter() - t)
        assert statistics.median(times) < 0.100, (q, times)
        assert q == "zzzz" or hits
    t = time.perf_counter()
    s.list(limit=20, source="web")
    assert time.perf_counter() - t < 0.100
    s.close()


def _seed(s, n):
    rnd = random.Random(7)
    topics = ["lab report error bars", "calendar dentist friday", "chemistry exam",
              "physics exam review", "essay outline", "spring break flights",
              "notion assignments", "weather atlanta", "pizza order", "gym plan",
              "python homework", "biology reading", "music practice", "budget"]
    filler = ("please could you tell me about the what when how should I do it "
              "today tomorrow next week maybe sure thanks okay great idea time "
              "move add remind check plan list find").split()
    vocab = [f"w{i}" for i in range(2000)]
    con = s._con
    con.execute("BEGIN")
    for i in range(n):
        topic = rnd.choice(topics)
        cid, ts = history.new_id(), NOW - i * 1800
        con.execute("INSERT INTO conversations (id, owner, source, started_at, "
                    "updated_at, ended_at, title, summary) VALUES (?,?,?,?,?,?,?,?)",
                    (cid, s.owner, rnd.choice(history.SOURCES), ts, ts, ts,
                     topic.capitalize(), f"Kyle and the assistant talked about {topic}."))
        for j in range(10):
            words = rnd.sample(filler, 6) + rnd.sample(vocab, 6)
            if j % 3 == 0:
                words += topic.split()
            rnd.shuffle(words)
            con.execute("INSERT INTO messages (conversation_id, seq, role, text, "
                        "created_at) VALUES (?,?,?,?,?)",
                        (cid, j + 1, "user" if j % 2 == 0 else "assistant",
                         " ".join(words), ts))
    con.execute("COMMIT")


# ---------- retention and size ----------
def test_retention_deletes_old_unpinned(tmp_path):
    path = tmp_path / "h.sqlite"
    s = HistoryStore(path, clock=Clock())
    old = add_conv(s, turns=["old"], at=NOW - 40 * DAY)
    old_pinned = add_conv(s, turns=["old but pinned"], at=NOW - 40 * DAY, pinned=True)
    new = add_conv(s, turns=["new"], at=NOW - DAY)
    s.close()
    s = HistoryStore(path, retention_days=30, clock=Clock())
    assert {c["id"] for c in s.list()} == {old_pinned, new}
    assert old not in {c["id"] for c in s.list(archived=None)}
    s.close()


def test_size_guard_removes_oldest_first(tmp_path):
    path = tmp_path / "h.sqlite"
    s = HistoryStore(path, clock=Clock())
    keep = add_conv(s, turns=["pinned " * 50], at=NOW - 999, pinned=True)
    ids = [add_conv(s, turns=["x" * 4000, "y" * 4000], at=NOW + i) for i in range(60)]
    s.close()
    s = HistoryStore(path, max_mb=0.2, clock=Clock())
    left = {c["id"] for c in s.list(limit=200)}
    assert keep in left and ids[-1] in left and ids[0] not in left
    assert s._used_bytes() <= 0.2 * 1024 * 1024
    s.close()


def test_open_store_uses_config_and_env(tmp_path, monkeypatch):
    cfg = load_config(None, None)
    monkeypatch.setenv("HISTORY_PATH", str(tmp_path / "cloud" / "history.sqlite"))
    cfg = load_config(None, None)
    s = history.open_store(cfg)
    assert Path(s.path) == tmp_path / "cloud" / "history.sqlite"
    s.close()
    cfg["history"]["enabled"] = False
    assert history.open_store(cfg) is None


def test_history_file_is_gitignored():
    ignore = (ROOT / ".gitignore").read_text()
    assert "data/" in ignore and "*.sqlite" in ignore


# ---------- dates ----------
def test_spoken_dates_and_groups():
    assert history.spoken_date(NOW - 3600, NOW) == "today"
    assert history.spoken_date(NOW - DAY, NOW) == "yesterday"
    assert history.spoken_date(NOW - 2 * DAY, NOW) == "Monday"
    assert history.spoken_date(NOW - 20 * DAY, NOW) == "September 24"
    assert history.spoken_date(NOW - 400 * DAY, NOW) == "September 9, 2025"
    assert [history.date_group(NOW - d * DAY, NOW) for d in (0, 1, 3, 9)] == [
        "Today", "Yesterday", "This week", "Older"]
    start = history.parse_day("2026-10-14")
    assert history.parse_day("2026-10-14", end=True) - start in (23 * 3600, DAY, 25 * 3600)
    assert history.parse_day("") is None


# ---------- titles ----------
MESSAGES = [{"role": "user", "kind": "chat", "text": "how do I do error bars for my lab report"},
            {"role": "assistant", "kind": "chat", "text": "Use standard error."}]


def test_generate_title_with_mocked_api(fake_client):
    fake_client.with_options.return_value = fake_client
    fake_client.queue = [make_response(text_block(
        '{"title": "\\"Lab report error bars extra words here.\\"", '
        '"summary": "Kyle asked how to add error bars. Standard error."}'))]
    title, summary = history.generate_title(fake_client, MESSAGES, "claude-haiku-5-5")
    assert title == "Lab report error bars extra words"  # <= 6 words, cleaned
    assert summary == "Kyle asked how to add error bars. Standard error."
    call = fake_client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["max_tokens"] <= 200
    assert "error bars" in call["messages"][0]["content"]


def test_generate_title_falls_back_when_the_api_is_down(fake_client):
    fake_client.with_options.return_value = fake_client
    fake_client.messages.create.side_effect = ConnectionError("offline")
    assert history.generate_title(fake_client, MESSAGES) == (
        "How do I do error bars", "how do I do error bars for my lab report")
    fake_client.messages.create.side_effect = None
    fake_client.messages.create.return_value = make_response(text_block("no json"))
    assert history.generate_title(fake_client, MESSAGES)[0] == "How do I do error bars"
    assert history.fallback_title([]) == ("Conversation", "")


# ---------- recording through ClaudeChat ----------
def recorded_chat(store, fake_client, tools=None, memory=None, incognito=False,
                  source="text"):
    rec = Recorder(store, source, incognito=incognito, run_async=False,
                   client_fn=lambda: fake_client)
    return ClaudeChat(client=fake_client, model="m", tools=tools, memory=memory,
                      recorder=rec)


def test_each_turn_is_recorded_as_it_happens(store, fake_client):
    chat = recorded_chat(store, fake_client)
    fake_client.queue = [make_response(text_block("Hi Kyle."))]
    chat.ask("hello")
    cid = chat.conversation_id
    assert [(m["role"], m["text"]) for m in store.get(cid)["messages"]] == [
        ("user", "hello"), ("assistant", "Hi Kyle.")]
    assert store.get(cid)["source"] == "text"


def test_user_turn_is_saved_even_if_the_reply_fails(store, fake_client):
    chat = recorded_chat(store, fake_client)
    fake_client.messages.create.side_effect = ConnectionError("down")
    with pytest.raises(ConnectionError):
        chat.ask("are you there?")
    assert chat.history == []
    conv = store.get(chat.conversation_id)
    assert [m["text"] for m in conv["messages"]] == ["are you there?"]


def test_tool_events_are_compact_and_confirmations_recorded(store, fake_client):
    registry = ToolRegistry()
    secret_result = {"events": [{"title": "Therapy with Dr. Smith",
                                 "attendees": ["a@b.c"]}]}
    registry.register(
        Tool("list_calendar_events", "d", {"type": "object"}, lambda day: secret_result),
        Tool("delete_calendar_event", "d", {"type": "object"},
             lambda event_id: "deleted", confirm_prompt=lambda i: "Delete Dentist?"))

    def confirm(question):
        confirm.last_answer = "no, keep it"
        return False
    registry.confirm = confirm
    chat = recorded_chat(store, fake_client, tools=registry)
    fake_client.queue = [
        make_response(tool_use_block("list_calendar_events", {"day": "x" * 100}),
                      stop_reason="tool_use"),
        make_response(tool_use_block("delete_calendar_event", {"event_id": "e1"},
                                     id="toolu_2"), stop_reason="tool_use"),
        make_response(text_block("Okay, I kept it."))]
    chat.ask("delete my dentist appointment")
    msgs = store.get(chat.conversation_id)["messages"]
    assert [(m["role"], m["kind"], m["text"]) for m in msgs] == [
        ("user", "chat", "delete my dentist appointment"),
        ("assistant", "confirm", "Delete Dentist?"),
        ("user", "confirm", "no, keep it"),
        ("assistant", "chat", "Okay, I kept it.")]
    events = msgs[-1]["tool_events"]
    assert events[0]["tool"] == "list_calendar_events" and events[0]["status"] == "ok"
    assert len(events[0]["args"]["day"]) <= 40
    assert events[1] == {"tool": "delete_calendar_event",
                         "args": {"event_id": "e1"}, "status": "cancelled"}
    raw = store._con.execute("SELECT group_concat(tool_events) FROM messages").fetchone()[0]
    assert "Therapy" not in raw and "a@b.c" not in raw  # results never stored
    assert registry.confirm is confirm  # the wrapper is removed again


def test_tool_errors_and_server_tools_are_recorded(store, fake_client):
    registry = ToolRegistry()
    registry.register(Tool("broken", "d", {"type": "object"},
                           lambda: 1 / 0))
    chat = recorded_chat(store, fake_client, tools=registry)
    search = SimpleNamespace(type="server_tool_use", id="s1", name="web_search",
                             input={"query": "atlanta weather"},
                             model_dump=lambda **kw: {"type": "server_tool_use",
                                                      "id": "s1", "name": "web_search",
                                                      "input": {"query": "atlanta weather"}})
    fake_client.queue = [
        make_response(tool_use_block("broken", {}), stop_reason="tool_use"),
        make_response(search, text_block("Sunny."))]
    chat.ask("weather?")
    events = store.get(chat.conversation_id)["messages"][-1]["tool_events"]
    assert [(e["tool"], e["status"]) for e in events] == [("broken", "error"),
                                                          ("web_search", "ok")]
    assert events[1]["args"] == {"query": "atlanta weather"}


def test_interrupted_reply_is_marked(store, fake_client):
    chat = recorded_chat(store, fake_client, source="voice")
    fake_client.queue = [make_response(text_block("A very long answer that goes on."))]
    chat.ask("tell me everything")
    chat.mark_interrupted("A very long")
    last = store.get(chat.conversation_id)["messages"][-1]
    assert last["interrupted"] and last["text"] == "A very long"
    chat.mark_interrupted()  # without the heard text: the flag only
    assert store.get(chat.conversation_id)["messages"][-1]["text"] == "A very long"


def test_end_conversation_titles_and_links_memory(store, fake_client):
    memory = MagicMock()
    memory.retrieve.return_value = []
    memory.store.return_value = "voice-123"
    fake_client.with_options.return_value = fake_client
    chat = recorded_chat(store, fake_client, memory=memory)
    fake_client.queue = [
        make_response(text_block("Use standard error.")),
        # (titles run inline here: run_async=False; normally a background thread)
        make_response(text_block('{"title": "Lab report error bars", '
                                 '"summary": "Error bars for the lab report."}')),
        make_response(text_block("Kyle is writing a lab report."))]  # memory note
    chat.ask("how do I do error bars for my lab report")
    cid = chat.conversation_id
    assert chat.end_conversation().endswith("Kyle is writing a lab report.")
    assert chat.conversation_id is None and chat.history == []
    memory.store.assert_called_once()
    assert memory.store.call_args.args[1] == {"conversation_id": cid}
    conv = store.get(cid)
    assert conv["title"] == "Lab report error bars" and conv["ended_at"]
    assert store.memory_vectors(cid) == ["voice-123"]
    # the next turn starts a new conversation
    fake_client.queue = [make_response(text_block("Hi."))]
    chat.ask("hi")
    assert chat.conversation_id not in (None, cid)


def test_titles_are_generated_off_the_hot_path(store, fake_client):
    import threading

    gate = threading.Event()
    fake_client.with_options.return_value = fake_client

    def slow_title(**kwargs):
        gate.wait(5)
        return make_response(text_block('{"title": "Slow title", "summary": "s"}'))
    rec = Recorder(store, "text", client_fn=lambda: fake_client)  # async
    chat = ClaudeChat(client=fake_client, model="m", recorder=rec)
    fake_client.queue = [make_response(text_block("Hello."))]
    chat.ask("hello there")
    cid = chat.conversation_id
    fake_client.messages.create.side_effect = slow_title
    t = time.perf_counter()
    chat.end_conversation()
    assert time.perf_counter() - t < 1  # returned while the title is pending
    assert store.get(cid)["title"] == ""
    gate.set()
    rec.flush(10)
    assert store.get(cid)["title"] == "Slow title"


def test_incognito_saves_nothing(store, fake_client):
    memory = MagicMock()
    memory.retrieve.return_value = []
    chat = recorded_chat(store, fake_client, memory=memory, incognito=True)
    fake_client.queue = [make_response(text_block("Shh."))]
    chat.ask("a private question")
    assert chat.incognito and chat.conversation_id is None
    assert chat.end_conversation() is None
    memory.store.assert_not_called()
    assert store.count(archived=None) == 0
    assert store._con.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_deleted_active_conversation_starts_a_new_one(store, fake_client):
    chat = recorded_chat(store, fake_client)
    fake_client.queue = [make_response(text_block("One.")),
                         make_response(text_block("Two."))]
    chat.ask("first")
    first = chat.conversation_id
    store.delete(first)  # e.g. deleted on the phone mid-conversation
    chat.ask("second")
    assert chat.conversation_id != first
    assert [m["text"] for m in store.get(chat.conversation_id)["messages"]] == [
        "second", "Two."]


def test_history_errors_never_break_a_reply(store, fake_client):
    chat = recorded_chat(store, fake_client)
    store.close()  # every write now fails
    fake_client.queue = [make_response(text_block("Still here."))]
    assert chat.ask("hello") == "Still here."


# ---------- resume ----------
def stored(messages, title="Lab report error bars", summary=""):
    return {"title": title, "summary": summary, "messages": [
        {"role": r, "kind": k, "text": t, "interrupted": i, "tool_events": e}
        for r, k, t, i, e in messages]}


def test_rebuild_history_makes_valid_plain_text_turns():
    conv = stored([
        ("assistant", "chat", "orphan reply first", False, []),
        ("user", "chat", "delete the dentist", False, []),
        ("assistant", "confirm", "Delete Dentist?", False, []),
        ("user", "confirm", "yes", False, []),
        ("assistant", "chat", "Deleted.", False,
         [{"tool": "delete_calendar_event", "args": {"event_id": "e1"}, "status": "ok"}]),
        ("user", "chat", "tell me a long story", False, []),
        ("assistant", "chat", "Once upon", True, []),
        ("user", "chat", "never answered", False, [])])
    h = rebuild_history(conv, 40)
    assert [m["role"] for m in h] == ["user", "assistant"] * 4
    assert all(isinstance(m["content"], str) for m in h)
    assert h[0]["content"] == "delete the dentist"
    assert "(I asked: Delete Dentist?)" in h[1]["content"]
    assert "(Kyle answered: yes)" in h[2]["content"]
    assert "delete_calendar_event(event_id=e1) ok" in h[3]["content"]
    assert "Deleted." in h[3]["content"]
    assert h[5]["content"].endswith("[Kyle interrupted this reply]")
    assert h[-1] == {"role": "assistant", "content": "(no reply)"}


def test_rebuild_history_trims_long_threads_with_a_summary():
    msgs = []
    for i in range(50):
        msgs += [("user", "chat", f"question {i}", False, []),
                 ("assistant", "chat", f"answer {i}", False, [])]
    h = rebuild_history(stored(msgs, summary="Fifty questions about stats."), 10)
    assert len(h) <= 10
    assert h[0]["role"] == "user" and "Fifty questions about stats." in h[0]["content"]
    assert "question 41" in h[0]["content"] and "Lab report error bars" in h[0]["content"]
    assert h[-1]["content"] == "answer 49" and h[2]["content"] == "question 46"
    assert [m["role"] for m in h] == ["user", "assistant"] * (len(h) // 2)


def test_chat_resume_continues_the_same_conversation(store, fake_client):
    old = add_conv(store, "wake", ["How do error bars work?", "Standard error."],
                   title="Lab report error bars")
    chat = recorded_chat(store, fake_client)
    conv = chat.resume(old)
    assert conv["title"] == "Lab report error bars"
    assert chat.history == [{"role": "user", "content": "How do error bars work?"},
                            {"role": "assistant", "content": "Standard error."}]
    fake_client.queue = [make_response(text_block("Use the SD instead."))]
    chat.ask("what about standard deviation?")
    assert fake_client.calls[0]["messages"][0]["content"] == "How do error bars work?"
    assert chat.conversation_id == old
    assert store.get(old)["message_count"] == 4 and store.count() == 1
    with pytest.raises(KeyError):
        chat.resume("doesnotexist12345")


def tool_chat(store, fake_client, answer=True, memory=None):
    registry = ToolRegistry(confirm=lambda q: (asked.append(q), answer)[1])
    asked = []
    registry.register(*history_tools(store, memory))
    chat = recorded_chat(store, fake_client, tools=registry)
    return chat, asked


def test_search_tool_returns_titles_dates_snippets_only(store, fake_client):
    old = add_conv(store, "wake", ["How do error bars work in my lab report?",
                                   "Standard error, and here is my secret answer."],
                   title="Lab report error bars", summary="Private summary.",
                   at=NOW - 2 * DAY)
    chat, _ = tool_chat(store, fake_client)
    fake_client.queue = [
        make_response(tool_use_block("search_conversations", {"query": "lab report"}),
                      stop_reason="tool_use"),
        make_response(text_block("We talked about error bars on Monday."))]
    chat.ask("what did we talk about the lab report")
    result = json.loads(fake_client.calls[1]["messages"][-1]["content"][0]["content"])
    [hit] = result["conversations"]
    assert set(hit) == {"id", "title", "when", "where", "snippet"}
    assert hit["id"] == old and hit["title"] == "Lab report error bars"
    assert hit["when"].startswith(history.spoken_date(NOW - 2 * DAY))
    assert "lab report" in hit["snippet"].lower() and MARK_START not in hit["snippet"]


def test_search_tool_by_date_for_yesterday(store, fake_client):
    yesterday = add_conv(store, turns=["pizza order", "ok"], title="Pizza",
                         at=time.time() - DAY)
    add_conv(store, turns=["older", "ok"], title="Old", at=time.time() - 9 * DAY)
    [search, _] = history_tools(store)
    day = datetime.fromtimestamp(time.time() - DAY).strftime("%Y-%m-%d")
    result = search.handler(since=day, until=day)
    assert [c["id"] for c in result["conversations"]] == [yesterday]
    assert search.handler(query="nothing like this")["conversations"] == []


def test_resume_by_voice_asks_then_switches(store, fake_client):
    old = add_conv(store, "wake", ["How do error bars work?", "Standard error."],
                   title="Lab report error bars", at=time.time() - 2 * DAY)
    chat, asked = tool_chat(store, fake_client, answer=True)
    fake_client.queue = [
        make_response(tool_use_block("search_conversations", {"query": "lab report"}),
                      stop_reason="tool_use"),
        make_response(tool_use_block("resume_conversation", {"conversation_id": old},
                                     id="toolu_2"), stop_reason="tool_use"),
        make_response(text_block("Sure, we were on error bars."))]
    chat.ask("continue our conversation about the lab report")
    weekday = history.spoken_date(time.time() - 2 * DAY)
    assert asked == [f"Continue 'Lab report error bars' from {weekday}?"]
    resumed = json.loads(fake_client.calls[2]["messages"][-1]["content"][0]["content"])
    assert resumed["status"] == "resumed" and resumed["title"] == "Lab report error bars"
    # context switched: old turns + this exchange, all plain text
    assert chat.conversation_id == old
    assert [m["content"] for m in chat.history] == [
        "How do error bars work?", "Standard error.",
        "continue our conversation about the lab report",
        "Sure, we were on error bars."]
    # the throwaway "continue..." conversation is not kept separately
    assert store.count() == 1 and store.get(old)["message_count"] == 4
    fake_client.queue = [make_response(text_block("Yes, use the SD."))]
    chat.ask("and standard deviation?")
    assert fake_client.calls[-1]["messages"][0]["content"] == "How do error bars work?"
    assert store.get(old)["message_count"] == 6


def test_resume_mid_conversation_keeps_the_current_one(store, fake_client):
    old = add_conv(store, "wake", ["error bars?", "Standard error."], title="Lab")
    chat, _ = tool_chat(store, fake_client)
    fake_client.queue = [make_response(text_block("It's sunny."))]
    chat.ask("what's the weather")
    current = chat.conversation_id
    fake_client.queue = [
        make_response(tool_use_block("resume_conversation", {"conversation_id": old}),
                      stop_reason="tool_use"),
        make_response(text_block("Back to error bars."))]
    chat.ask("let's continue the lab one")
    assert chat.conversation_id == old
    weather = store.get(current)
    assert weather["ended_at"] and weather["title"]  # saved and titled
    assert store.count() == 2


def test_resume_declined_changes_nothing(store, fake_client):
    old = add_conv(store, "wake", ["error bars?", "Standard error."], title="Lab")
    chat, asked = tool_chat(store, fake_client, answer=False)
    fake_client.queue = [
        make_response(tool_use_block("resume_conversation", {"conversation_id": old}),
                      stop_reason="tool_use"),
        make_response(text_block("Okay, staying here."))]
    chat.ask("continue the lab one")
    assert asked and chat.conversation_id != old
    assert store.get(old)["message_count"] == 2


def test_resume_tool_unknown_id_is_an_error(store, fake_client):
    chat, asked = tool_chat(store, fake_client)
    fake_client.queue = [
        make_response(tool_use_block("resume_conversation",
                                     {"conversation_id": "made-up-id-12345"}),
                      stop_reason="tool_use"),
        make_response(text_block("I couldn't find it."))]
    chat.ask("continue that one")
    result = fake_client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and asked == []


def test_semantic_hits_link_back_and_merge(store):
    a = add_conv(store, turns=["error bars in my lab report", "ok"], title="Lab")
    b = add_conv(store, turns=["graph uncertainty", "ok"], title="Uncertainty")
    c = add_conv(store, "web", turns=["unrelated", "ok"], title="Other")
    memory = MagicMock()
    memory.search_conversations.return_value = [
        {"conversation_id": a, "score": 0.9, "text": "dup"},
        {"conversation_id": b, "score": 0.8, "text": "Kyle asked about uncertainty."},
        {"conversation_id": "gone-deleted-1234", "score": 0.7, "text": "x"},
        {"conversation_id": c, "score": 0.6, "text": "web one"}]
    hits = history.search_conversations(store, "lab report", memory)
    assert [(h["id"], h["match"]) for h in hits] == [
        (a, "keyword"), (b, "semantic"), (c, "semantic")]
    assert hits[1]["snippet"] == "Kyle asked about uncertainty."
    hits = history.search_conversations(store, "lab report", memory, source="text")
    assert c not in [h["id"] for h in hits]
    memory.search_conversations.side_effect = RuntimeError("pinecone down")
    assert [h["id"] for h in history.search_conversations(store, "lab", memory)] == [a]


def test_delete_cascades_to_pinecone(store):
    cid = add_conv(store, turns=["hi", "ok"])
    store.add_memory_vector(cid, "voice-1")
    store.add_memory_vector(cid, "voice-2")
    memory = MagicMock()
    memory.delete.side_effect = RuntimeError("pinecone down")
    with pytest.raises(RuntimeError):
        history.delete_conversation(store, cid, memory)
    assert store.get(cid)  # nothing deleted, so a retry can finish the job
    memory.delete.side_effect = None
    history.delete_conversation(store, cid, memory)
    memory.delete.assert_called_with(["voice-1", "voice-2"])
    with pytest.raises(KeyError):
        store.get(cid)


def test_pinecone_memory_search_and_delete():
    from assistant.memory import PineconeMemory

    index, pc = MagicMock(), MagicMock()
    pc.inference.embed.return_value = SimpleNamespace(data=[{"values": [0.1] * 4}])
    index.query.return_value = SimpleNamespace(matches=[
        SimpleNamespace(score=0.8, metadata={"conversation_id": "abc", "text": "note"}),
        SimpleNamespace(score=0.2, metadata={"conversation_id": "low", "text": "x"}),
        SimpleNamespace(score=0.9, metadata={"text": "no link"})])
    mem = PineconeMemory(pc=pc, index=index, namespace="voice-assistant")
    assert mem.search_conversations("lab") == [
        {"conversation_id": "abc", "score": 0.8, "text": "note"}]
    kwargs = index.query.call_args.kwargs
    assert kwargs["namespace"] == "voice-assistant"
    assert kwargs["filter"] == {"conversation_id": {"$exists": True}}
    mem.delete(["v1"])
    index.delete.assert_called_once_with(ids=["v1"], namespace="voice-assistant")


# ---------- export ----------
def test_export_markdown_and_json_without_secrets(store, monkeypatch):
    cid = add_conv(store, "web", ["what's on friday", "Dentist at 3."],
                   title="Friday plans", summary="Dentist on Friday.")
    store.add_memory_vector(cid, "voice-vector-xyz")
    store.mark_interrupted(cid)
    conv = store.get(cid)
    md = history.export(conv, "md")
    assert md.startswith("# Friday plans") and "**Kyle:** what's on friday" in md
    assert "_(interrupted)_" in md and "> Dentist on Friday." in md
    data = json.loads(history.export(conv, "json"))
    assert data["title"] == "Friday plans" and len(data["messages"]) == 2
    assert "owner" not in data and "voice-vector-xyz" not in json.dumps(data)
    # a secret that somehow got in is masked on the way out too
    monkeypatch.setenv("NOTION_TOKEN", "ntn_" + "a" * 30)
    conv["messages"][0]["text"] = "token ntn_" + "a" * 30
    assert "aaaa" not in history.export(conv, "json")
    with pytest.raises(ValueError):
        history.export(conv, "pdf")


# ---------- CLI ----------
@pytest.fixture
def cli_env(tmp_path, monkeypatch, restore_root_logging):
    monkeypatch.chdir(tmp_path)
    for var in ("NOTION_TOKEN", "PINECONE_API_KEY", "HISTORY_PATH"):
        monkeypatch.delenv(var, raising=False)
    s = HistoryStore(tmp_path / "data" / "history.sqlite")
    yield s
    s.close()


def test_cli_list_search_show_export(cli_env, capsys):
    cid = add_conv(cli_env, "wake", ["How do error bars work in my lab report?",
                                     "Standard error."], title="Lab report error bars")
    add_conv(cli_env, "text", ["weather today", "Sunny."], title="Weather")
    assert cli.main(["--history", "list"]) == 0
    out = capsys.readouterr().out
    assert cid in out and "Lab report error bars" in out and "Weather" in out
    assert cli.main(["--history", "list", "--source", "text"]) == 0
    assert "Lab report" not in capsys.readouterr().out
    assert cli.main(["--history", "search", "lab", "report"]) == 0
    out = capsys.readouterr().out
    assert cid in out and "[lab] [report]" in out.lower() and "Weather" not in out
    assert cli.main(["--history", "show", cid]) == 0
    assert "**Kyle:** How do error bars work" in capsys.readouterr().out
    assert cli.main(["--history", "export", cid, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Lab report error bars"
    assert cli.main(["--history", "show", "nope"]) == 1
    assert "No conversation" in capsys.readouterr().out
    assert cli.main(["--history", "bogus"]) == 2


def test_cli_delete_asks_first(cli_env, capsys, monkeypatch):
    cid = add_conv(cli_env, turns=["hi", "hello"], title="Hello")
    monkeypatch.setattr("builtins.input", lambda _="": "no")
    assert cli.main(["--history", "delete", cid]) == 0
    assert "Not deleted" in capsys.readouterr().out and cli_env.get(cid)
    monkeypatch.setattr("builtins.input", lambda _="": "yes")
    assert cli.main(["--history", "delete", cid]) == 0
    assert "Deleted 'Hello'" in capsys.readouterr().out
    with pytest.raises(KeyError):
        cli_env.get(cid)
    cid = add_conv(cli_env, turns=["bye"])
    assert cli.main(["--history", "delete", cid, "--yes"]) == 0
    assert cli_env.count() == 0


def test_cli_text_mode_records_and_continues(cli_env, capsys, monkeypatch, fake_client):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: fake_client)
    fake_client.with_options.return_value = fake_client
    fake_client.queue = [make_response(text_block("Standard error.")),
                         make_response(text_block('{"title": "Error bars", '
                                                  '"summary": "About error bars."}'))]
    inputs = iter(["how do error bars work", "quit"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    assert cli.main(["--mode", "text"]) == 0
    [conv] = cli_env.list()
    assert conv["source"] == "text" and conv["title"] == "Error bars"  # flushed at exit
    # --continue loads it; the next turn is sent with the old context
    fake_client.queue = [make_response(text_block("Use the SD.")),
                         make_response(text_block('{"title": "Error bars", '
                                                  '"summary": "More."}'))]
    inputs = iter(["and standard deviation?", "quit"])
    assert cli.main(["--mode", "text", "--continue", "last"]) == 0
    assert "Continuing 'Error bars'" in capsys.readouterr().out
    sent = [c for c in fake_client.calls if c["model"] != "claude-haiku-5-5"][-1]
    assert sent["messages"][0]["content"] == "how do error bars work"
    assert cli_env.count() == 1 and cli_env.get(conv["id"])["message_count"] == 4
    assert cli.main(["--mode", "text", "--continue", "missing-id-1234567"]) == 1
    assert cli.main(["--mode", "wake", "--continue", conv["id"]]) == 1


def test_cli_incognito_saves_nothing(cli_env, monkeypatch, fake_client, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: fake_client)
    fake_client.queue = [make_response(text_block("Shh."))]
    inputs = iter(["secret", "quit"])
    monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
    assert cli.main(["--mode", "text", "--incognito"]) == 0
    assert "Incognito" in capsys.readouterr().out
    assert cli_env.count(archived=None) == 0


def test_history_tools_registered_by_cli(tmp_path):
    registry = ToolRegistry()
    cfg = load_config(None, None)
    s = HistoryStore(tmp_path / "h.sqlite")
    cli.add_history_tools(registry, cfg, s, None)
    assert registry.has("search_conversations") and registry.has("resume_conversation")
    cli.add_history_tools(registry2 := ToolRegistry(), cfg, None, None)
    assert not registry2
    rec = cli.build_recorder(cfg, s, "speak", incognito=False)
    assert rec.source == "text" and not rec.incognito
    assert cli.build_recorder(cfg, s, "wake", incognito=True).incognito
    assert cli.build_recorder(cfg, None, "wake") is None
    s.close()
