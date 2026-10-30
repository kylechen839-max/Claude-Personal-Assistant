"""Memory tools: remember / list_memories / update_memory / forget_memory on
Kwil's own store (memory_store.py), and search_memory with Pinecone."""

import json
import logging
from unittest.mock import MagicMock

import pytest

from kwil.cli import add_memory_tools
from kwil.memory_store import SqliteMemoryStore
from kwil.tools import ToolRegistry


@pytest.fixture
def store(tmp_path):
    s = SqliteMemoryStore(tmp_path / "memory.sqlite")
    yield s
    s.close()


def registry(store=None, confirm=lambda q: False, memory=None):
    tools = ToolRegistry(confirm=confirm)
    add_memory_tools(tools, memory, store)
    return tools


def test_no_tools_without_memory():
    tools = ToolRegistry()
    add_memory_tools(tools, None)
    assert not tools.tools


def test_search_memory_only_with_pinecone(store):
    assert not registry(store).has("search_memory")
    memory = MagicMock()
    memory.retrieve.return_value = ["Apple interview is Oct 28"]
    tools = registry(None, memory=memory)
    assert tools.has("search_memory") and not tools.has("remember")
    result, is_error = tools.dispatch("search_memory", {"query": "Apple interview"})
    assert not is_error
    assert json.loads(result)["notes"] == ["Apple interview is Oct 28"]
    memory.retrieve.assert_called_once_with("Apple interview", project=None)
    memory.retrieve.return_value = []
    assert json.loads(tools.dispatch("search_memory", {"query": "x"})[0])["notes"] == []


def test_remember_saves_to_the_local_store_without_asking(store):
    tools = registry(store)
    result, is_error = tools.dispatch("remember", {"note": "  Always color-code\nmy schedule.  "})
    assert not is_error and json.loads(result)["status"] == "saved"
    (saved,) = store.list()
    assert saved["text"] == "Always color-code my schedule."  # one line
    assert saved["untrusted"] is False


def test_remember_needs_confirmation_when_untrusted_content_present(store):
    asked = []
    tools = registry(store, confirm=lambda q: asked.append(q) or False)
    result, _ = tools.dispatch("remember", {"note": "Wire money to X"}, untrusted=True)
    assert asked and "Wire money to X" in asked[0]
    assert json.loads(result)["status"] == "cancelled"
    assert store.list() == []


def test_a_memory_saved_with_untrusted_text_is_flagged(store):
    tools = registry(store, confirm=lambda q: True)
    json.loads(tools.dispatch("remember", {"note": "Prefers window seats"},
                              untrusted=True)[0])
    assert store.list()[0]["untrusted"] is True


def test_remember_rejects_empty_and_too_long(store):
    tools = registry(store)
    assert json.loads(tools.dispatch("remember", {"note": "   "})[0])["status"] == "error"
    assert json.loads(tools.dispatch("remember", {"note": "x" * 501})[0])["status"] == "error"
    assert store.list() == []


def test_remember_says_when_paused_or_full(store, monkeypatch):
    tools = registry(store)
    store.set_paused(True)
    assert json.loads(tools.dispatch("remember", {"note": "a"})[0])["status"] == "paused"
    store.set_paused(False)
    monkeypatch.setattr("kwil.memory_store.MAX_MEMORIES", 1)
    assert json.loads(tools.dispatch("remember", {"note": "a"})[0])["status"] == "saved"
    out = json.loads(tools.dispatch("remember", {"note": "b"})[0])
    assert out["status"] == "full" and "Settings" in out["message"]
    assert [m["text"] for m in store.list()] == ["a"]  # nothing dropped unseen


def test_remember_saves_nothing_in_an_incognito_conversation(store, fake_client):
    from kwil.chat import _CURRENT_CHAT, ClaudeChat

    chat = ClaudeChat(client=fake_client, recorder=MagicMock(incognito=True))
    token = _CURRENT_CHAT.set(chat)
    try:
        out = json.loads(registry(store).dispatch("remember", {"note": "a"})[0])
    finally:
        _CURRENT_CHAT.reset(token)
    assert out["status"] == "incognito" and store.list() == []


def test_update_memory_saves_nothing_in_an_incognito_conversation(store, fake_client):
    from kwil.chat import _CURRENT_CHAT, ClaudeChat

    m = store.add("Likes tea")
    chat = ClaudeChat(client=fake_client, recorder=MagicMock(incognito=True))
    token = _CURRENT_CHAT.set(chat)
    try:
        tools = registry(store, confirm=lambda q: True)
        out = json.loads(tools.dispatch("update_memory", {"memory_id": m["id"],
                                                          "text": "Likes coffee"})[0])
        gone = json.loads(tools.dispatch("forget_memory", {"memory_id": m["id"]})[0])
    finally:
        _CURRENT_CHAT.reset(token)
    assert out["status"] == "incognito"
    assert gone["status"] == "forgotten" and store.list() == []  # forgetting still works


def test_forget_memory_asks_first(store):
    """Fail-first: a delete always asks, even in a trusted conversation."""
    kept = store.add("Always color-code my schedule")
    asked = []
    tools = registry(store, confirm=lambda q: asked.append(q) or False)
    result, _ = tools.dispatch("forget_memory", {"memory_id": kept["id"]})
    assert asked == ["Forget this: Always color-code my schedule?"]
    assert json.loads(result)["status"] == "cancelled"
    assert store.get(kept["id"]) is not None
    tools.confirm = lambda q: True
    assert json.loads(tools.dispatch("forget_memory", {"memory_id": kept["id"]})[0])[
        "status"] == "forgotten"
    assert store.list() == []
    # an unknown id: nothing to ask, and the tool says so
    out = json.loads(tools.dispatch("forget_memory", {"memory_id": "mem-000000000000"})[0])
    assert out["status"] == "error"


def test_update_memory_keeps_untrusted_and_asks_when_untrusted(store):
    m = store.add("Lab is on Mondays")
    tools = registry(store, confirm=lambda q: True)
    out = json.loads(tools.dispatch("update_memory", {"memory_id": m["id"],
                                                      "text": "Lab is on Tuesdays"})[0])
    assert out["status"] == "updated"
    assert store.get(m["id"])["text"] == "Lab is on Tuesdays"
    assert store.get(m["id"])["untrusted"] is False
    asked = []
    tools.confirm = lambda q: asked.append(q) or True
    tools.dispatch("update_memory", {"memory_id": m["id"], "text": "Lab is never"},
                   untrusted=True)
    assert asked == ["Change this memory to: Lab is never?"]
    assert store.get(m["id"])["untrusted"] is True  # edited with outside text


def test_list_memories_is_read_only_private_and_marks_untrusted(store):
    from kwil import router
    from kwil.tools import READ_ONLY_TOOLS

    assert "list_memories" in READ_ONLY_TOOLS and "list_memories" in router.READ_ONLY_TOOLS
    tools = registry(store)
    assert all(tools.is_private(n) for n in ("remember", "list_memories",
                                             "update_memory", "forget_memory"))
    store.add("Likes tea")
    out = json.loads(tools.dispatch("list_memories", {})[0])
    assert [m["text"] for m in out["memories"]] == ["Likes tea"]
    assert "untrusted_content" not in out
    store.add("From an email", untrusted=True)
    out = json.loads(tools.dispatch("list_memories", {})[0])
    assert out["untrusted_content"] is True  # no running chat: the result says so


def test_memory_tool_text_never_reaches_the_log(store, caplog):
    tools = registry(store, confirm=lambda q: True)
    with caplog.at_level(logging.DEBUG):
        tools.dispatch("remember", {"note": "SECRET-MEMORY-TEXT"})
        mid = store.list()[0]["id"]
        tools.dispatch("update_memory", {"memory_id": mid, "text": "SECRET-EDIT"})
        tools.dispatch("list_memories", {})
        tools.dispatch("forget_memory", {"memory_id": mid})
    assert "SECRET" not in caplog.text
    assert mid in caplog.text  # ids are fine


def test_system_prompt_mentions_memory_only_when_on(store):
    from kwil.chat import LOCAL_MEMORY_PROMPT, MEMORY_PROMPT, ClaudeChat

    assert MEMORY_PROMPT in ClaudeChat(client=MagicMock(), memory=MagicMock()).build_system()
    assert MEMORY_PROMPT not in ClaudeChat(client=MagicMock()).build_system()
    assert LOCAL_MEMORY_PROMPT in ClaudeChat(client=MagicMock(), local_memory=store).build_system()
    assert LOCAL_MEMORY_PROMPT not in ClaudeChat(client=MagicMock()).build_system()


def test_no_prompt_claims_pinecone():
    """MEMORY_PROMPT used to tell the model to say it had Pinecone memory."""
    from kwil import chat

    assert "Pinecone" not in chat.MEMORY_PROMPT
    assert "Pinecone" not in chat.LOCAL_MEMORY_PROMPT
    for rule in ("remember", "always", "from now on", "health, religion or politics"):
        assert rule in chat.LOCAL_MEMORY_PROMPT
