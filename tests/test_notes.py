"""Quick-capture notes with a mocked Pinecone index."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from assistant import cli
from assistant.chat import ClaudeChat
from assistant.config import load_config
from assistant.tools import ToolRegistry
from assistant.tools.notes import QuickNotes
from tests.conftest import make_response, text_block, tool_use_block
from tests.test_memory import make_memory


class FakeIndex:
    """Just enough of a Pinecone index: upsert, query (cosine on 2-d toy
    vectors), list by prefix, fetch, delete."""

    def __init__(self):
        self.vectors = {}  # (namespace, id) -> (values, metadata)

    def upsert(self, vectors, namespace):
        for v in vectors:
            self.vectors[(namespace, v["id"])] = (v["values"], v["metadata"])

    def query(self, vector, top_k, namespace, filter=None, include_metadata=True):
        def score(values):
            return sum(a * b for a, b in zip(vector, values))
        matches = [SimpleNamespace(id=i, score=score(vals), metadata=meta)
                   for (ns, i), (vals, meta) in self.vectors.items()
                   if ns == namespace and (not filter or all(
                       meta.get(k) == cond["$eq"] for k, cond in filter.items()))]
        matches.sort(key=lambda m: -m.score)
        return SimpleNamespace(matches=matches[:top_k])

    def list(self, prefix, namespace):
        ids = [i for (ns, i) in self.vectors if ns == namespace
               and i.startswith(prefix)]
        yield ids[:2]  # paged, like the real client
        if ids[2:]:
            yield ids[2:]

    def fetch(self, ids, namespace):
        return SimpleNamespace(vectors={
            i: SimpleNamespace(metadata=self.vectors[(namespace, i)][1])
            for i in ids if (namespace, i) in self.vectors})

    def delete(self, ids, namespace):
        for i in ids:
            self.vectors.pop((namespace, i), None)


TOPICS = {"lab": [1.0, 0.0], "error bars": [1.0, 0.0], "rent": [0.0, 1.0]}


def toy_embed(text, input_type):
    """'lab report' things point one way, everything else the other."""
    lowered = text.lower()
    return next((v for k, v in TOPICS.items() if k in lowered), [0.0, 1.0])


@pytest.fixture
def notes():
    memory = MagicMock(namespace="voice-assistant")
    memory.index = FakeIndex()
    memory.embed.side_effect = toy_embed
    return QuickNotes(memory, timezone="America/New_York")


def test_remember_then_recall_by_meaning(notes):
    saved = notes.remember_note("The lab report needs error bars",
                                tags=["Physics", "physics", " lab "])
    assert saved["status"] == "saved"
    note_id = saved["note"]["id"]
    assert note_id.startswith("note-")
    values, meta = notes.index.vectors[("voice-assistant", note_id)]
    assert meta["kind"] == "note" and meta["tags"] == ["physics", "lab"]
    assert meta["created_at"].endswith("Z") and meta["source"] == "voice-assistant"
    assert notes.memory.embed.call_args_list[0].args[1] == "passage"
    notes.remember_note("Pay rent on the first")

    found = notes.recall_notes("What did I need for the lab report?")
    assert [n["text"] for n in found["notes"]] == ["The lab report needs error bars"]
    assert notes.memory.embed.call_args.args[1] == "query"


def test_recall_only_returns_notes_not_summaries(notes):
    notes.index.upsert([{"id": "voice-1", "values": [1.0, 0.0],
                         "metadata": {"text": "lab chat", "kind":
                                      "conversation_summary"}}], "voice-assistant")
    notes.remember_note("Lab report: error bars")
    found = notes.recall_notes("lab report")
    assert [n["text"] for n in found["notes"]] == ["Lab report: error bars"]


def test_list_recent_newest_first_and_delete(notes, monkeypatch):
    ids = []
    for i, text in enumerate(["first", "second", "third"]):
        monkeypatch.setattr("assistant.tools.notes.uuid.uuid4",
                            lambda i=i: SimpleNamespace(hex=f"{i:06d}"))
        ids.append(notes.remember_note(text)["note"]["id"])
    recent = notes.list_recent_notes(limit=2)
    assert [n["text"] for n in recent["notes"]] == ["third", "second"]
    assert notes.delete_note(ids[0])["status"] == "deleted"
    assert [n["text"] for n in notes.list_recent_notes()["notes"]] == ["third",
                                                                      "second"]


def test_delete_asks_first_and_only_touches_notes(notes):
    note_id = notes.remember_note("The lab report needs error bars")["note"]["id"]
    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(*notes.tools())
    result, err = reg.dispatch("delete_note", {"note_id": note_id})
    assert "cancelled" in result and ("voice-assistant", note_id) in notes.index.vectors
    assert asked == ["Do you want me to delete the note: The lab report needs "
                     "error bars?"]
    reg.confirm = lambda q: True
    result, err = reg.dispatch("delete_note", {"note_id": "voice-20261017-abc"})
    assert err and "isn't a saved note" in result


def test_bad_notes_are_refused(notes):
    with pytest.raises(ValueError):
        notes.remember_note("   ")
    with pytest.raises(ValueError):
        notes.remember_note("x" * 1001)
    with pytest.raises(ValueError):
        notes.recall_notes(" ")


def test_saved_hook_gets_each_note(notes):
    seen = []
    notes.on_saved = seen.append
    notes.remember_note("Bring goggles to lab")
    assert seen[0]["text"] == "Bring goggles to lab"


def test_remember_through_chat(fake_client, notes):
    reg = ToolRegistry()
    reg.register(*notes.tools())
    fake_client.queue = [
        make_response(tool_use_block("remember_note",
                                     {"text": "The lab report needs error bars",
                                      "tags": ["physics"]}),
                      stop_reason="tool_use"),
        make_response(text_block("Got it.")),
        make_response(tool_use_block("recall_notes",
                                     {"query": "lab report"}, id="t2"),
                      stop_reason="tool_use"),
        make_response(text_block("It needs error bars."))]
    chat = ClaudeChat(client=fake_client, tools=reg)
    assert chat.ask("Remember that the lab report needs error bars") == "Got it."
    assert chat.ask("What did I need for the lab report?") == "It needs error bars."
    assert "error bars" in chat.history[-2]["content"][0]["content"]


def test_registered_only_with_memory():
    cfg = load_config(None, None)
    reg = ToolRegistry()
    assert not cli.register_notes(reg, None, cfg)
    mem, _, _ = make_memory()
    assert cli.register_notes(reg, mem, cfg)
    assert {"remember_note", "recall_notes", "list_recent_notes",
            "delete_note"} <= set(reg.tools)
    cfg["notes"]["enabled"] = False
    assert not cli.register_notes(ToolRegistry(), mem, cfg)


def test_real_pinecone_memory_objects_work(notes):
    """QuickNotes uses PineconeMemory's index, namespace and embed."""
    mem, pc, index = make_memory()
    index.list.return_value = iter([["note-20261017T120000-aaaaaa"]])
    index.fetch.return_value = SimpleNamespace(vectors={
        "note-20261017T120000-aaaaaa": SimpleNamespace(
            metadata={"text": "hi", "created_at": "2026-10-17T16:00:00Z"})})
    q = QuickNotes(mem, timezone="America/New_York")
    assert q.list_recent_notes()["notes"][0] == {
        "id": "note-20261017T120000-aaaaaa", "text": "hi",
        "saved": "Saturday, October 17"}
    q.remember_note("x")
    assert index.upsert.call_args.kwargs["namespace"] == "voice-assistant"


def test_notes_replace_the_plain_remember_tool_but_keep_search():
    cfg = load_config(None, None)
    mem, _, _ = make_memory()
    reg = ToolRegistry()
    cli.add_memory_tools(reg, mem)
    assert reg.has("remember") and reg.has("search_memory")
    cli.register_notes(reg, mem, cfg)
    assert not reg.has("remember") and reg.has("search_memory")
    assert reg.has("remember_note")


@pytest.mark.parametrize("source", ["web", "email"])
def test_web_or_email_cannot_plant_a_note_without_a_yes(fake_client, notes, source):
    """Like the plain remember tool: with untrusted text in context, saving
    a note asks first; Kyle says no and nothing is stored."""
    from tests.test_gmail import inbox
    from tests.test_security import fetched_page, server_fetch

    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(*notes.tools())
    gmail, _, _ = inbox()
    reg.register(*gmail.tools())
    plant = tool_use_block("remember_note",
                           {"text": "Kyle wants all his events at midnight"}, id="t2")
    if source == "web":
        first = make_response(server_fetch(), fetched_page(), plant,
                              stop_reason="tool_use")
        fake_client.queue = [first, make_response(text_block("Okay."))]
    else:
        fake_client.queue = [
            make_response(tool_use_block("read_email", {"message_id": "m2"}, id="t1"),
                          stop_reason="tool_use"),
            make_response(plant, stop_reason="tool_use"),
            make_response(text_block("Okay."))]
    ClaudeChat(client=fake_client, tools=reg).ask("Look at that")
    assert asked == ["Do you want me to save a note: Kyle wants all his events "
                     "at midnight?"]
    assert notes.index.vectors == {}


def test_kyles_own_note_saves_without_asking(notes):
    asked = []
    reg = ToolRegistry(confirm=lambda q: asked.append(q) or False)
    reg.register(*notes.tools())
    result, err = reg.dispatch("remember_note", {"text": "Bring goggles"})
    assert not err and asked == [] and len(notes.index.vectors) == 1
