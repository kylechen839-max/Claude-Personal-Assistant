"""Memory layer 2 (kwil/recall.py, tools/history.py read_conversation):
finding and reading past conversations without Pinecone.

All offline: real SQLite history in tmp_path, the mocked Anthropic client,
and a fake embedder (Hugging Face downloads are blocked in the sandbox;
fastembed's own test skips without the package)."""

import json
import logging
import sys
import threading

import pytest

from kwil import chat as chat_mod, cli, history, recall, router
from kwil.chat import ClaudeChat
from kwil.config import load_config
from kwil.history import HistoryStore, Recorder
from kwil.tools import READ_ONLY_TOOLS, Tool, ToolRegistry
from kwil.tools.history import READ_CHARS, READ_MESSAGES, history_tools
from tests.conftest import make_response, text_block, tool_use_block
from tests.test_history import DAY, NOW, Clock, add_conv

# Words that mean the same thing share a dimension, so a paraphrase with
# no word in common still points the same way (what a real model does).
CONCEPTS = {"physics": 0, "mechanics": 0, "exam": 1, "test": 1, "midterm": 1,
            "schedule": 2, "timetable": 2, "plan": 2, "study": 3, "revision": 3,
            "pizza": 4, "dinner": 4, "dentist": 5, "teeth": 5, "guitar": 6,
            "music": 6}


class FakeEmbedder:
    name = "fake:concepts-v1"

    def __init__(self):
        self.calls = 0
        self.fail_after = None  # raise on the Nth embed call (a crash)
        self.gate = None        # threading.Event to wait on (a slow model)

    def _vec(self, text):
        v = [0.0] * 8
        for w in history.query_terms(text) + text.lower().split():
            w = w.strip(".,?!")
            if w in CONCEPTS:
                v[CONCEPTS[w]] += 1
        v[7] = 0.05  # never all zero
        return v

    def embed(self, texts):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("model crashed")
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "h.sqlite", clock=Clock())
    yield s
    s.close()


@pytest.fixture
def no_pinecone(monkeypatch):
    """Pinecone isn't installed: importing it fails."""
    monkeypatch.setitem(sys.modules, "pinecone", None)


def hybrid(store, embedder=None, **kw):
    r = recall.HybridRetriever(store, embedder or FakeEmbedder(), run_async=False, **kw)
    store.recall = r
    return r


def vectors_of(store, cid):
    return store._con.execute("SELECT count(*) FROM recall_vectors WHERE "
                              "conversation_id = ?", (cid,)).fetchone()[0]


def ids(hits):
    return [h["id"] for h in hits]


PHYSICS = dict(turns=["Can you help me plan when to study for physics?",
                      "Sure. Mondays and Thursdays after lab work best.",
                      "Okay, let's do that.", "Done: Mondays and Thursdays."],
               title="Weekly study plan",
               summary="Decided to study physics on Mondays and Thursdays.")


# ---------- keyword search, backend local, no Pinecone ----------
def test_local_search_finds_by_summary_words_and_by_message_words(store, no_pinecone):
    cid = add_conv(store, **PHYSICS)
    add_conv(store, turns=["order a pizza", "Done."], title="Pizza", summary="Pizza.")
    hits = history.search_conversations(store, "physics Thursdays", None)
    assert ids(hits) == [cid] and hits[0]["match"] == "keyword"
    # only in a message, not in the title or summary
    hits = history.search_conversations(store, "after lab", None)
    assert ids(hits) == [cid]
    assert "lab" in history.plain_snippet(hits[0]["snippet"]).lower()
    # title, date, a short summary and the best snippet
    assert {"title", "updated_at", "summary", "snippet"} <= set(hits[0])
    assert "pinecone" not in sys.modules or sys.modules["pinecone"] is None


def test_title_and_summary_hits_rank_above_body_only_hits(store):
    """A conversation about the topic (its title or summary says so) comes
    before ones where the words were only said in passing, even when the
    passing mention repeats them."""
    topic = add_conv(store, turns=["hm", "ok"], title="Physics schedule",
                     summary="Long talk that settled a lot of things about the "
                             "week, the term, the lab and many other matters, "
                             "including the physics schedule in the end.",
                     at=NOW - 5 * DAY)
    passing = add_conv(store, turns=["physics schedule physics schedule physics "
                                     "schedule", "ok"], title="Random", summary="Misc.")
    hits = history.search_conversations(store, "physics schedule", None)
    assert ids(hits) == [topic, passing]


def test_retriever_interface_and_default(store):
    assert isinstance(recall.retriever_for(store), recall.FtsRetriever)
    for cls in (recall.FtsRetriever, recall.HybridRetriever):
        for name in ("index", "search", "delete", "index_soon"):
            assert callable(getattr(cls, name)), (cls, name)
    cfg = load_config(None, None)
    assert cfg["memory"]["embeddings"] == "off"
    assert isinstance(recall.attach(store, cfg), recall.FtsRetriever)
    assert recall.embedder_from_config(cfg) is None


def test_archived_conversations_are_searchable_by_the_tool(store):
    cid = add_conv(store, **PHYSICS)
    store.update(cid, archived=True)
    search = history_tools(store)[0]
    assert [c["id"] for c in search.handler(query="physics")["conversations"]] == [cid]


# ---------- read_conversation ----------
def read_tool(store, tz=None):
    return next(t for t in history_tools(store, tz=tz) if t.name == "read_conversation")


def long_conv(store, n=60, size=2000, at=None):
    turns = [f"message {i} " + ("filler words here " * (size // 18)) for i in range(n)]
    return add_conv(store, turns=turns, title="Long one", summary="A long chat.", at=at)


def test_read_conversation_is_bounded(store):
    cid = long_conv(store)
    result = read_tool(store).handler(conversation_id=cid)
    assert len(result["messages"]) == READ_MESSAGES == 20
    assert all(len(m["said"]) <= READ_CHARS == 500 for m in result["messages"])
    # no query: the summary and the last messages
    assert result["summary"] == "A long chat."
    assert [m["n"] for m in result["messages"]] == list(range(41, 61))
    assert result["message_count"] == 60
    assert len(json.dumps(result)) < 20 * 600 + 2000


def test_read_query_window_is_centered_on_the_match(store):
    turns = [f"turn {i} about nothing much" for i in range(60)]
    turns[30] = "we decided the physics schedule: Mondays"
    cid = add_conv(store, turns=turns, title="t")
    result = read_tool(store).handler(conversation_id=cid, query="physics schedule")
    ns = [m["n"] for m in result["messages"]]
    assert ns == list(range(28, 35))  # message 31 (index 30) +/- 3
    assert [m["n"] for m in result["messages"] if m.get("match")] == [31]
    # at the very end: the window is cut there, still around the match
    turns[30], turns[59] = "turn 30", "physics schedule at last"
    cid = add_conv(store, turns=turns, title="t2")
    ns = [m["n"] for m in read_tool(store).handler(
        conversation_id=cid, query="physics schedule")["messages"]]
    assert ns == [57, 58, 59, 60]


def test_read_query_with_many_matches_stays_bounded_best_first():
    msgs = [{"text": "physics" if i % 2 else "other"} for i in range(100)]
    msgs[70]["text"] = "physics schedule"  # both words: the best match
    shown, matched = recall.excerpt(msgs, "physics schedule", limit=20)
    assert len(shown) == 20 and 70 in matched
    assert {67, 68, 69, 70, 71, 72, 73} <= set(shown)


def test_long_message_is_clipped_around_the_match():
    text = "x " * 1000 + "the physics schedule is Mondays " + "y " * 1000
    piece = recall.clip(text, "physics schedule", 500)
    assert len(piece) <= 500 and "physics schedule is Mondays" in piece
    assert piece.startswith(history.ELLIPSIS) and piece.endswith(history.ELLIPSIS)


def recorded_chat(store, fake_client, registry, incognito=False):
    rec = Recorder(store, "text", incognito=incognito, run_async=False,
                   client_fn=lambda: fake_client)
    return ClaudeChat(client=fake_client, model="m", tools=registry, recorder=rec)


def test_read_conversation_keeps_the_conversation_and_taints(store, fake_client):
    old = add_conv(store, **PHYSICS, at=NOW - 3 * DAY)
    asked, moved = [], []
    registry = ToolRegistry(confirm=lambda q: (asked.append(q), False)[1])
    registry.register(*history_tools(store))
    registry.register(Tool("move_calendar_event", "d", {"type": "object"},
                           lambda **kw: moved.append(kw) or "moved",
                           confirm_if_untrusted=lambda i: "Move it?"))
    chat = recorded_chat(store, fake_client, registry)
    fake_client.queue = [make_response(text_block("Hi."))]
    chat.ask("hello")
    current = chat.conversation_id
    assert not chat.tainted()
    fake_client.queue = [
        make_response(tool_use_block("read_conversation",
                                     {"conversation_id": old, "query": "physics"}),
                      stop_reason="tool_use"),
        make_response(text_block("In your chat on Sunday: Mondays and Thursdays."))]
    chat.ask("what did we decide about physics?")
    result = json.loads(fake_client.calls[-1]["messages"][-1]["content"][0]["content"])
    assert result["title"] == "Weekly study plan" and result["messages"]
    assert chat.conversation_id == current != old  # not switched
    assert store.get(old)["message_count"] == 4   # nothing added to the old one
    assert chat.tainted() and chat.untrusted_context
    assert "read_conversation" in registry.untrusted_names()
    assert registry.is_private("read_conversation")
    fake_client.queue = [
        make_response(tool_use_block("move_calendar_event", {"id": "e1"}),
                      stop_reason="tool_use"),
        make_response(text_block("I didn't move it."))]
    chat.ask("ok move my lab to Monday")
    assert asked == ["Move it?"] and moved == []


def test_read_conversation_unknown_or_bad_id(store):
    tool = read_tool(store)
    for bad in ("made-up-id-12345", "", "../../etc", "x" * 500):
        assert tool.handler(conversation_id=bad)["status"] == "error"


def test_read_tool_is_in_both_read_only_lists():
    assert "read_conversation" in READ_ONLY_TOOLS
    assert "read_conversation" in router.READ_ONLY_TOOLS
    assert not router.needs_main_for(["search_conversations", "read_conversation"])
    assert "read_conversation" in chat_mod.HISTORY_TOOLS


def test_recall_prompt_only_with_the_tool_and_byte_stable(store, fake_client):
    registry = ToolRegistry()
    registry.register(*history_tools(store))
    chat = ClaudeChat(client=fake_client, model="m", tools=registry)
    first = chat.stable_system()
    assert chat_mod.RECALL_PROMPT in first
    fake_client.queue = [make_response(text_block("Hi."))]
    chat.ask("hello")
    assert chat.stable_system() == first
    without = ToolRegistry()
    without.register(*[t for t in history_tools(store) if t.name != "read_conversation"])
    assert chat_mod.RECALL_PROMPT not in ClaudeChat(
        client=fake_client, model="m", tools=without).stable_system()


# ---------- embeddings: hybrid ranking ----------
def test_hybrid_finds_a_paraphrase_keyword_search_misses(store):
    plan = add_conv(store, turns=["help me get ready", "Sure."],
                    title="Mechanics exam plan",
                    summary="Agreed to study mechanics every Monday.")
    add_conv(store, turns=["food", "ok"], title="Pizza night", summary="Ordered dinner.")
    add_conv(store, turns=["teeth", "ok"], title="Dentist", summary="Booked the dentist.")
    query = "physics revision timetable"
    assert recall.FtsRetriever(store).search(query) == []
    r = hybrid(store)
    assert r.backfill() == 3
    hits = history.search_conversations(store, query, None)
    assert ids(hits) == [plan] and hits[0]["match"] == "semantic"
    assert hits[0]["snippet"] == "Agreed to study mechanics every Monday."


def test_hybrid_fuses_both_lists(store):
    both = add_conv(store, turns=["physics", "ok"], title="Physics timetable",
                    summary="Physics schedule.")
    words = add_conv(store, turns=["physics homework question", "ok"], title="Homework",
                     summary="A question.")
    meaning = add_conv(store, turns=["x", "y"], title="Mechanics plan",
                       summary="Study mechanics.")
    hybrid(store).backfill()
    hits = store.recall.search("physics timetable", k=10)
    assert ids(hits)[0] == both  # first in both lists
    assert set(ids(hits)) >= {both, meaning}
    assert words not in ids(hits) or ids(hits).index(words) > 0


def test_a_new_summary_is_indexed_again(store):
    cid = add_conv(store, turns=["hi", "ok"], title="Chat", summary="Small talk.")
    r = hybrid(store)
    r.backfill()
    assert store.recall_pending_count(r.model) == 0
    store.finish(cid, None, "Planned the physics exam study timetable.")
    assert store.recall_pending_count(r.model) == 1
    assert r.index(store.get(cid)) and store.recall_pending_count(r.model) == 0
    assert ids(r.search("mechanics revision")) == [cid]


def test_a_finished_conversation_is_indexed_off_the_turn(store, fake_client):
    r = hybrid(store)
    chat = recorded_chat(store, fake_client, ToolRegistry())
    fake_client.queue = [make_response(text_block("Sure.")),
                         make_response(text_block('{"title": "Physics timetable", '
                                                  '"summary": "Study physics Mondays."}'))]
    chat.ask("plan my physics study")
    cid = chat.conversation_id
    chat.end_conversation()  # Recorder: title, then recall.index_soon
    assert vectors_of(store, cid) == 1 and store.recall_pending_count(r.model) == 0


def test_backfill_indexes_old_conversations_and_is_resumable(store, caplog):
    old = [add_conv(store, turns=[f"chat {i}", "ok"], title=f"Old {i}",
                    summary=f"Summary {i}.", at=NOW - (40 - i) * DAY) for i in range(40)]
    embedder = FakeEmbedder()
    embedder.fail_after = 2  # crashes on the third batch
    r = hybrid(store, embedder)
    caplog.set_level(logging.INFO, logger="kwil.recall")
    assert r.backfill() == 2 * recall.BACKFILL_BATCH  # two batches were saved
    assert store.recall_pending_count(r.model) == 40 - 32
    # restart: a new retriever goes on from there, never redoing the done ones
    embedder2 = FakeEmbedder()
    r2 = hybrid(store, embedder2)
    assert r2.backfill() == 8 and embedder2.calls == 1
    assert store.recall_pending_count(r2.model) == 0
    assert all(vectors_of(store, cid) == 1 for cid in old)
    # bounded per run
    more = [add_conv(store, turns=[f"new {i}", "ok"]) for i in range(5)]
    assert hybrid(store).backfill(limit=3) == 3
    assert store.recall_pending_count(r2.model) == 2 and more
    assert any("indexed" in rec.getMessage() for rec in caplog.records)


def test_search_never_waits_for_the_backfill(store):
    cid = add_conv(store, **PHYSICS)
    embedder = FakeEmbedder()
    embedder.gate = threading.Event()  # the model is busy
    r = recall.HybridRetriever(store, embedder, run_async=True)
    store.recall = r
    try:
        hits = history.search_conversations(store, "physics", None)  # starts it
        assert ids(hits) == [cid]  # keyword hits, right away
        assert r._thread is not None and r._thread.is_alive()
    finally:
        embedder.gate.set()
        r.wait()
    assert vectors_of(store, cid) == 1


def test_embedder_failure_falls_back_to_keywords(store, caplog):
    cid = add_conv(store, **PHYSICS)
    embedder = FakeEmbedder()
    embedder.fail_after = 0
    r = hybrid(store, embedder)
    caplog.set_level(logging.WARNING, logger="kwil.recall")
    assert r.backfill() == 0
    assert ids(r.search("physics")) == [cid]
    assert any("keyword search only" in rec.getMessage() for rec in caplog.records)


def test_fastembed_is_optional(store, monkeypatch):
    """Without the package, embeddings on means keyword search, not a crash."""
    monkeypatch.setitem(sys.modules, "fastembed", None)
    cfg = load_config(None, None)
    cfg["memory"]["embeddings"] = "fastembed"
    cid = add_conv(store, **PHYSICS)
    embedder = recall.embedder_from_config(cfg)
    assert isinstance(embedder, recall.FastEmbedder)
    r = recall.HybridRetriever(store, embedder, run_async=False)
    store.recall = r
    assert r.backfill() == 0
    assert ids(history.search_conversations(store, "physics", None)) == [cid]


@pytest.mark.slow
def test_fastembed_real_model(tmp_path):
    """Needs fastembed and a model download (blocked in the sandbox)."""
    pytest.importorskip("fastembed")
    e = recall.FastEmbedder(cache_dir=str(tmp_path / "models"))
    try:
        e.download()
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"model download unavailable: {type(err).__name__}")
    [v] = e.embed(["physics study plan"])
    assert len(v) == 384 and len(e.embed_query("physics")) == 384


# ---------- deleted means gone; incognito never indexed ----------
def test_a_deleted_conversation_is_never_returned_and_its_vectors_are_gone(store):
    gone = add_conv(store, **PHYSICS)
    pruned = add_conv(store, turns=["mechanics exam", "ok"], title="Mechanics exam",
                      summary="Study mechanics.", at=NOW - 400 * DAY)
    r = hybrid(store)
    r.backfill()
    assert vectors_of(store, gone) == vectors_of(store, pruned) == 1
    history.delete_conversation(store, gone)
    store.retention_days = 365
    store.prune()  # a tombstone from prune (same path as a synced delete)
    for cid in (gone, pruned):
        assert vectors_of(store, cid) == 0
    for q in ("physics", "Mondays Thursdays", "physics timetable", "mechanics"):
        assert history.search_conversations(store, q, None) == []
    assert read_tool(store).handler(conversation_id=gone)["status"] == "error"
    assert store._con.execute("SELECT count(*) FROM recall_vectors").fetchone()[0] == 0
    # a backfill racing the delete stores nothing
    assert not store.put_recall_vector(gone, r.model, "|", b"\0" * 32, 8)


def test_delete_tells_a_retriever_with_its_own_index(store):
    """A Retriever whose index lives elsewhere (e.g. pgvector) is told."""
    cid = add_conv(store, **PHYSICS)
    told = []

    class Remote(recall.FtsRetriever):
        def delete(self, conversation_id):
            told.append(conversation_id)
    store.recall = Remote(store)
    history.delete_conversation(store, cid)
    assert told == [cid]


def test_incognito_is_never_indexed(store, fake_client):
    r = hybrid(store)
    chat = recorded_chat(store, fake_client, ToolRegistry(), incognito=True)
    fake_client.queue = [make_response(text_block("Sure."))]
    chat.ask("secret physics plan")
    chat.end_conversation()
    r.backfill()
    assert store.count(archived=None) == 0
    assert store._con.execute("SELECT count(*) FROM recall_vectors").fetchone()[0] == 0
    assert history.search_conversations(store, "physics", None) == []


# ---------- isolation ----------
def test_each_persons_recall_reads_only_their_own_store(tmp_path):
    """Owner and family member, both ways: search and read (with vectors)."""
    owner = HistoryStore(tmp_path / "owner.sqlite", clock=Clock())
    member = HistoryStore(tmp_path / "users" / "u1" / "history.sqlite", clock=Clock(),
                          eventlog=False)
    try:
        kyle = add_conv(owner, turns=["physics exam plan", "ok"], title="Physics plan",
                        summary="Kyle's physics study timetable.")
        mom = add_conv(member, turns=["guitar lessons", "ok"], title="Guitar",
                       summary="Mom's music schedule.")
        for s in (owner, member):
            hybrid(s).backfill()
        o_search, _, o_read = history_tools(owner)
        m_search, _, m_read = history_tools(member)
        for q in ("physics", "mechanics revision", "Kyle"):
            assert m_search.handler(query=q)["conversations"] == []
        for q in ("guitar", "music lessons", "Mom"):
            assert o_search.handler(query=q)["conversations"] == []
        assert m_read.handler(conversation_id=kyle)["status"] == "error"
        assert o_read.handler(conversation_id=mom)["status"] == "error"
        assert [c["id"] for c in o_search.handler(query="physics")["conversations"]] == [kyle]
        assert [c["id"] for c in m_search.handler(query="guitar")["conversations"]] == [mom]
    finally:
        owner.close()
        member.close()


# ---------- logs ----------
def test_no_conversation_text_at_info(store, fake_client, caplog):
    secret = "zanzibar"
    cid = add_conv(store, turns=[f"the {secret} physics plan", "ok"],
                   title=f"{secret} plan", summary=f"About {secret}.")
    caplog.set_level(logging.DEBUG)
    r = hybrid(store)
    r.backfill()
    registry = ToolRegistry()
    registry.register(*history_tools(store))
    chat = recorded_chat(store, fake_client, registry)
    fake_client.queue = [
        make_response(tool_use_block("search_conversations", {"query": secret}),
                      stop_reason="tool_use"),
        make_response(tool_use_block("read_conversation",
                                     {"conversation_id": cid, "query": secret},
                                     id="toolu_2"), stop_reason="tool_use"),
        make_response(text_block("Found it."))]
    chat.ask("what about it")
    history.delete_conversation(store, cid)
    infos = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.INFO]
    assert any("read_conversation" in m for m in infos)
    assert not [m for m in infos if secret in m.lower()]
    # read_conversation is private: its input isn't even at DEBUG
    debug = [rec.getMessage() for rec in caplog.records
             if "read_conversation" in rec.getMessage()]
    assert not [m for m in debug if secret in m.lower()]


def test_cli_wires_recall_into_the_store(tmp_path):
    cfg = load_config(None, None)
    s = HistoryStore(tmp_path / "h.sqlite")
    try:
        registry = ToolRegistry()
        cli.add_history_tools(registry, cfg, s, None)
        assert isinstance(s.recall, recall.FtsRetriever)
        assert {"search_conversations", "resume_conversation",
                "read_conversation"} <= set(registry.tools)
    finally:
        s.close()
