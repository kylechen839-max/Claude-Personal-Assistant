import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from assistant.chat import ClaudeChat
from assistant.memory import PineconeMemory, transcript
from tests.conftest import make_response, request_text, text_block, tool_use_block


def match(score, text, ns="projects"):
    return SimpleNamespace(score=score, metadata={"text": text}, namespace=ns)


def make_memory(matches=(), namespaces=("projects", "interview-prep")):
    pc = MagicMock()
    pc.inference.embed.return_value = SimpleNamespace(data=[{"values": [0.1] * 1024}])
    index = MagicMock()
    index.describe_index_stats.return_value = SimpleNamespace(
        namespaces={n: {} for n in namespaces})
    index.query_namespaces.return_value = SimpleNamespace(matches=list(matches))
    index.query.return_value = SimpleNamespace(matches=list(matches))
    return PineconeMemory(pc=pc, index=index), pc, index


def test_retrieve_filters_by_score_across_namespaces():
    mem, pc, index = make_memory([match(0.6, "Apple interview is Oct 28"),
                                  match(0.2, "unrelated chunk"),
                                  match(0.5, "")])
    assert mem.retrieve("when is my Apple interview") == ["Apple interview is Oct 28"]
    params = pc.inference.embed.call_args.kwargs["parameters"]
    assert params["input_type"] == "query" and params["dimension"] == 1024
    assert pc.inference.embed.call_args.kwargs["model"] == "llama-text-embed-v2"
    kw = index.query_namespaces.call_args.kwargs
    assert kw["namespaces"] == ["interview-prep", "projects", "voice-assistant"]
    assert mem.retrieve("   ") == []


def test_retrieve_single_namespace_uses_query():
    mem, _, index = make_memory([match(0.9, "note")])
    mem.search_namespaces = ["voice-assistant"]
    assert mem.retrieve("x") == ["note"]
    assert index.query.call_args.kwargs["namespace"] == "voice-assistant"


def test_store_upserts_into_own_namespace():
    mem, pc, index = make_memory()
    vid = mem.store("Kyle prefers morning study sessions.")
    kw = index.upsert.call_args.kwargs
    assert kw["namespace"] == "voice-assistant"
    vec = kw["vectors"][0]
    assert vec["id"] == vid and vid.startswith("voice-")
    assert vec["metadata"]["text"] == "Kyle prefers morning study sessions."
    assert vec["metadata"]["kind"] == "conversation_summary"
    assert pc.inference.embed.call_args.kwargs["parameters"]["input_type"] == "passage"


def test_chat_injects_notes_into_system_prompt(fake_client):
    mem, _, _ = make_memory([match(0.7, "Kyle's Apple interview is Oct 28")])
    fake_client.queue = [make_response(text_block("It's on October 28th."))]
    chat = ClaudeChat(client=fake_client, memory=mem)
    chat.ask("When is my Apple interview?")
    sent = request_text(fake_client.calls[0])  # system + the turn's context
    assert "long-term memory" in sent and "Oct 28" in sent


def test_memory_failure_does_not_block_reply(fake_client):
    mem = MagicMock()
    mem.retrieve.side_effect = ConnectionError("offline")
    fake_client.queue = [make_response(text_block("Hi."))]
    assert ClaudeChat(client=fake_client, memory=mem).ask("hi") == "Hi."


def test_end_conversation_summarizes_and_stores(fake_client):
    mem = MagicMock()
    fake_client.queue = [make_response(text_block("Sure, noted.")),
                         make_response(text_block("Kyle wants to study Fridays at 9."))]
    chat = ClaudeChat(client=fake_client, memory=mem)
    chat.ask("Remember I study on Fridays at 9")
    summary = chat.end_conversation()
    assert summary.endswith("Kyle wants to study Fridays at 9.")
    mem.store.assert_called_once_with(summary)
    sent = fake_client.calls[1]
    assert "Kyle: Remember I study on Fridays at 9" in sent["messages"][0]["content"]
    assert "tools" not in sent
    assert chat.history == []


def test_end_conversation_skips_nothing_and_empty(fake_client):
    mem = MagicMock()
    chat = ClaudeChat(client=fake_client, memory=mem)
    assert chat.end_conversation() is None  # empty history: no API call
    fake_client.queue = [make_response(text_block("Hello!")),
                         make_response(text_block("NOTHING"))]
    chat.ask("hi")
    assert chat.end_conversation() is None
    mem.store.assert_not_called()


def test_transcript_skips_tool_data():
    history = [
        {"role": "user", "content": "What's tomorrow?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                      "content": "{...}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Dentist at 3."}]},
    ]
    assert transcript(history) == ("Kyle: What's tomorrow?\nAssistant: Checking.\n"
                                   "Assistant: Dentist at 3.")


def test_wake_mode_idle_timeout_ends_conversation(fake_client):
    from assistant.cli import run_wake
    from assistant.tts import PrintTTS

    mem = MagicMock()
    fake_client.queue = [make_response(text_block("A.")),
                         make_response(text_block("Kyle asked about A.")),
                         make_response(text_block("B."))]
    chat = ClaudeChat(client=fake_client, memory=mem)
    times = iter([0, 0, 1000, 1000])  # second request comes 1000 s later
    listens = iter([(True, [1]), (True, [1]), (False, None)])
    listener = SimpleNamespace(listen=lambda src: next(listens))
    texts = iter(["question A", "question B"])
    run_wake(chat, PrintTTS(lambda *_: None),
             SimpleNamespace(transcribe=lambda a: next(texts)), listener, None,
             output_fn=lambda *_: None, conversation_timeout=300,
             clock=lambda: next(times, 1000))
    assert mem.store.call_args_list[0].args[0].endswith("Kyle asked about A.")
    # the exit summary for B fails (no queued reply): logged, not raised
    assert len(mem.store.call_args_list) == 1


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("PINECONE_API_KEY"),
                    reason="PINECONE_API_KEY not set")
def test_live_pinecone_retrieve_read_only():
    notes = PineconeMemory().retrieve("Apple interview preparation")
    assert isinstance(notes, list)


def test_single_namespace_string_is_not_split():
    mem, _, index = make_memory([match(0.9, "note")])
    mem.search_namespaces = "projects"
    assert mem.namespaces() == ["projects"]
    assert mem.retrieve("x") == ["note"]
    assert index.query.call_args.kwargs["namespace"] == "projects"


def test_retrieve_reads_other_text_fields():
    m = [SimpleNamespace(score=0.9, metadata={"chunk_text": "from chunk"}),
         SimpleNamespace(score=0.9, metadata={"content": "from content"}),
         SimpleNamespace(score=0.9, metadata={"other": "ignored"})]
    mem, _, _ = make_memory(m)
    assert mem.retrieve("x") == ["from chunk", "from content"]
    mem.text_fields = ["content"]
    assert mem.retrieve("x") == ["from content"]


def test_retrieve_remembers_which_vector_each_note_came_from():
    """ClaudeChat.forget_note needs the id of a recalled note to take its
    text out of the conversation once delete_note removes it."""
    found = match(0.9, "locker code 1234")
    found.id = "note-20261020T101010-abc123"  # Pinecone matches carry the id
    mem, _, _ = make_memory([found, match(0.1, "too far")])
    assert mem.retrieve("locker") == ["locker code 1234"]
    assert mem.last_matches == [("note-20261020T101010-abc123", "locker code 1234")]
