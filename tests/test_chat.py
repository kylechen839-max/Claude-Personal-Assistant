import pytest

from kwil.chat import DEFAULT_MODEL, ClaudeChat, default_model
from kwil.cli import is_exit, run_text
from tests.conftest import make_response, request_text, system_text, text_block


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
    assert "turned into speech" in system_text(call)
    assert "Current local date" in request_text(call)
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
    assert "Kwil: Four." in out
    assert out[-1] == "Bye!"


def test_run_speak_mode_speaks_reply(fake_client):
    from kwil.tts import PrintTTS

    fake_client.queue = [make_response(text_block("Hello."))]
    said = []
    inputs = iter(["hi", "bye"])
    run_text(ClaudeChat(client=fake_client), input_fn=lambda _: next(inputs),
             output_fn=lambda *_: None, tts=PrintTTS(said.append))
    assert said == ["[speaking] Hello."]


def test_run_voice_loop(fake_client):
    from types import SimpleNamespace

    import numpy as np

    from kwil.cli import run_voice
    from kwil.tts import PrintTTS

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
    from kwil.tools import Tool, ToolRegistry
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


def test_chinese_system_prompt(fake_client):
    from kwil.tools import Tool, ToolRegistry

    tools = ToolRegistry()
    tools.register(Tool("email_summary", "Summarize Kyle's inbox.",
                        {"type": "object", "properties": {}}, handler=lambda: {}))
    chat = ClaudeChat(client=fake_client, tools=tools, language="zh")
    system = chat.build_system()
    assert "简体中文" in system and "the user's Gmail" in system
    assert "Kyle" not in system
    # the reply rule is the system prompt's last word (the turn's context
    # follows in the user message)
    assert chat.stable_system().rstrip().endswith("even when tool results are English.")
    english = ClaudeChat(client=fake_client, tools=tools).build_system()
    assert "Kyle" in english and "简体中文" not in english


def test_build_chat_passes_the_language(monkeypatch):
    from kwil import cli
    from kwil.config import load_config

    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: None)
    cfg = load_config(None, None)
    assert cli.build_chat(cfg).language == "en"
    cfg["assistant"]["language"] = "zh"
    assert cli.build_chat(cfg).language == "zh"


def test_chinese_prompt_says_what_it_can_do(fake_client):
    """"你能帮我做什么？" gets an answer that matches what this instance has."""
    from kwil.chat import ZH_HELP_CONNECT, ZH_HELP_EMAIL
    from kwil.tools import Tool, ToolRegistry

    bare = ClaudeChat(client=fake_client, tools=ToolRegistry(), language="zh").build_system()
    assert "你能帮我做什么" in bare and ZH_HELP_CONNECT in bare and ZH_HELP_EMAIL not in bare
    tools = ToolRegistry()
    for name in ("list_calendar_events", "email_summary"):
        tools.register(Tool(name, "x", {"type": "object", "properties": {}}, handler=lambda: {}))
    full = ClaudeChat(client=fake_client, tools=tools, language="zh").build_system()
    assert ZH_HELP_EMAIL in full and ZH_HELP_CONNECT not in full
    assert "你能帮我做什么" not in ClaudeChat(client=fake_client, tools=tools).build_system()


FOREIGN = {"type": "thinking", "thinking": "...", "signature": "sig-from-another-conversation"}


def test_earlier_thinking_blocks_are_not_sent_back():
    """A thinking block signed for another conversation (hands-free voice
    sharing the phone's conversation, a continued history) made every later
    request fail: 400 "Invalid `signature` in `thinking` block". Earlier
    turns go without them; the current turn's tool loop keeps its own."""
    from kwil.chat import request_messages

    own = {"type": "thinking", "thinking": "plan", "signature": "this-turn"}
    history = [
        {"role": "user", "content": "what's on tomorrow?"},
        {"role": "assistant", "content": [FOREIGN, {"type": "text", "text": "Two things."}]},
        {"role": "user", "content": "move the first one"},
        {"role": "assistant", "content": [own, {"type": "tool_use", "id": "t1",
                                                 "name": "move_event", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "ok"}]},
    ]
    sent = request_messages(history)
    assert sent[1]["content"] == [{"type": "text", "text": "Two things."}]
    assert sent[3]["content"][0] == own  # this turn's tool loop needs it
    assert history[1]["content"][0] == FOREIGN  # stored history untouched
    only = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [FOREIGN]},
            {"role": "user", "content": "again"}]
    assert request_messages(only)[1]["content"] == [{"type": "text", "text": "(no reply)"}]


def test_a_conversation_with_a_foreign_thinking_block_keeps_working(fake_client):
    from kwil.chat import ClaudeChat
    from tests.conftest import make_response, text_block

    chat = ClaudeChat(client=fake_client, model="m")
    chat.history = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": [FOREIGN, {"type": "text", "text": "Hello."}]}]
    fake_client.queue.append(make_response(text_block("Sure.")))
    assert chat.ask("and tomorrow?") == "Sure."
    sent = fake_client.calls[0]["messages"]
    assert not any(isinstance(b, dict) and b.get("type") == "thinking"
                   for m in sent if isinstance(m["content"], list) for b in m["content"])
