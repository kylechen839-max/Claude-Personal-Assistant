import pytest

from assistant.chat import DEFAULT_MODEL, ClaudeChat, default_model
from assistant.cli import is_exit, run_text
from tests.conftest import make_response, text_block


def test_default_model(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    assert default_model() == DEFAULT_MODEL == "claude-sonnet-5-5"
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-1")
    assert default_model() == "claude-opus-4-1"


def test_ask_keeps_history_and_sends_system_prompt(fake_client):
    fake_client.queue = [make_response(text_block("Hi Kyle.")),
                         make_response(text_block("Sure."))]
    chat = ClaudeChat(client=fake_client, model="m")
    assert chat.ask("hello") == "Hi Kyle."
    assert chat.ask("again") == "Sure."
    call = fake_client.calls[-1]
    assert call["model"] == "m"
    assert "read aloud" in call["system"]
    assert "Current local date" in call["system"]
    assert [m["role"] for m in chat.history] == ["user", "assistant"] * 2


def test_failed_request_does_not_corrupt_history(fake_client):
    fake_client.messages.create.side_effect = RuntimeError("boom")
    chat = ClaudeChat(client=fake_client)
    with pytest.raises(RuntimeError):
        chat.ask("hello")
    assert chat.history == []


def test_history_trimmed_to_user_first(fake_client):
    chat = ClaudeChat(client=fake_client, max_history=4)
    fake_client.queue = [make_response(text_block(f"r{i}")) for i in range(5)]
    for i in range(5):
        chat.ask(f"q{i}")
    assert len(chat.history) <= 4
    assert chat.history[0]["role"] == "user"


def test_is_exit():
    assert is_exit("Goodbye.")
    assert is_exit(" quit ")
    assert not is_exit("what time is it")


def test_run_text_loop(fake_client):
    fake_client.queue = [make_response(text_block("Four."))]
    chat = ClaudeChat(client=fake_client)
    inputs = iter(["", "what is 2+2", "quit"])
    out = []
    run_text(chat, input_fn=lambda _: next(inputs), output_fn=out.append)
    assert "Claude: Four." in out
    assert out[-1] == "Bye!"


def test_run_speak_mode_speaks_reply(fake_client):
    from assistant.tts import PrintTTS

    fake_client.queue = [make_response(text_block("Hello."))]
    said = []
    inputs = iter(["hi", "bye"])
    run_text(ClaudeChat(client=fake_client), input_fn=lambda _: next(inputs),
             output_fn=lambda *_: None, tts=PrintTTS(said.append))
    assert said == ["[speaking] Hello."]


def test_run_voice_loop(fake_client):
    from types import SimpleNamespace

    import numpy as np

    from assistant.cli import run_voice
    from assistant.tts import PrintTTS

    fake_client.queue = [make_response(text_block("It's sunny."))]
    recordings = iter([None, np.ones(10), np.ones(10)])
    texts = iter(["How's the weather?", "Goodbye."])
    transcriber = SimpleNamespace(transcribe=lambda a: next(texts))
    said = []
    run_voice(ClaudeChat(client=fake_client), PrintTTS(said.append),
              transcriber, record_fn=lambda: next(recordings),
              output_fn=lambda *_: None)
    assert said == ["[speaking] It's sunny."]


def test_cut_off_tool_call_is_dropped_from_history(fake_client):
    from assistant.tools import Tool, ToolRegistry
    from tests.conftest import tool_use_block

    tools = ToolRegistry()
    tools.register(Tool("t", "d", {"type": "object", "properties": {}},
                        lambda: "ok"))
    fake_client.queue = [
        make_response(text_block("Let me check"), tool_use_block("t", {}),
                      stop_reason="max_tokens"),
        make_response(text_block("Done."))]
    chat = ClaudeChat(client=fake_client, tools=tools)
    assert chat.ask("hi") == "Let me check"
    assert chat.history[-1]["content"] == [{"type": "text", "text": "Let me check"}]
    chat.ask("again")  # history sent here must hold no unanswered tool_use
    sent = fake_client.calls[-1]["messages"]
    assert not any(b.get("type") == "tool_use" for m in sent
                   if isinstance(m["content"], list) for b in m["content"])
