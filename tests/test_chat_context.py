"""Kwil keeps its reasoning within a conversation (Kyle, 2026-10-29: "why
did you block only 3:30 to 4 for physics?" got "I can't see my reasoning
from that earlier chat"): earlier turns' thinking is sent again while it is
safe, the history is trimmed by whole turns, main-model tool turns have
room to plan, and the prompt says how to plan a day."""

from __future__ import annotations

import json
import logging

import pytest

from kwil.chat import (SCHEDULE_PROMPT, THINKING_TAG, ClaudeChat, is_user_turn,
                       request_messages)
from kwil.tools import Tool, ToolRegistry
from tests.conftest import api_bad_request, make_response, text_block, tool_use_block
from tests.contract_fakes import ContractClient

MAIN, FAST, OTHER = "test-model", "test-fast-model", "test-model-2"
REMEMBER = "Please remember the number 4417. Also: what's on my calendar tomorrow?"
RECALL = "What number did I ask you to remember?"


def calendar(untrusted=False) -> ToolRegistry:
    tools = ToolRegistry(confirm=lambda q: True)
    tools.register(Tool("list_calendar_events", "List Kyle's events for a day.",
                        {"type": "object", "properties": {"day": {"type": "string"}}},
                        lambda day="today": {"events": [{"title": "Physics lab",
                                                         "start": "14:00"}]},
                        untrusted_output=untrusted))
    return tools


def thinking_before_last_turn(messages) -> list[str]:
    """Thinking texts a request sent from turns before Kyle's latest one."""
    last = max(i for i, m in enumerate(messages) if is_user_turn(m))
    return [b["thinking"] for m in messages[:last] if m["role"] == "assistant"
            and isinstance(m["content"], list) for b in m["content"]
            if b.get("type") == "thinking"]


def chat_with(client, **kw) -> ClaudeChat:
    return ClaudeChat(client=client, model=MAIN, tools=calendar(), **kw)


# --- 1. earlier thinking ----------------------------------------------------------

def test_turn_two_sends_turn_ones_thinking_from_the_same_chat_and_model():
    client = ContractClient()
    chat = chat_with(client)
    chat.ask(REMEMBER)  # a tool round: two replies, each with its thinking
    assert "4417" in chat.ask(RECALL)
    sent = client.requests[-1]["messages"]
    assert thinking_before_last_turn(sent) == ["Planning reply 1.", "Planning reply 2."]
    assert THINKING_TAG not in json.dumps(sent)  # the private tag is never sent
    assert any(THINKING_TAG in m for m in chat.history)  # history keeps it


def test_a_history_set_from_outside_sends_none_of_its_thinking():
    """Hands-free voice continuing the phone's conversation (another chat),
    or the phone taking it back: thinking from before is not sent."""
    phone, voice = ContractClient(), ContractClient()
    a, b = chat_with(phone), chat_with(voice)
    a.ask(REMEMBER)
    b.history = a.history
    assert "4417" in b.ask(RECALL)
    assert thinking_before_last_turn(voice.requests[-1]["messages"]) == []
    a.history = b.history  # back on the phone: its own turn 1 was before
    assert "4417" in a.ask("Thanks. " + RECALL)
    assert thinking_before_last_turn(phone.requests[-1]["messages"]) == []
    assert len(phone.requests) == 3 and len(voice.requests) == 1  # no retries


def test_a_resumed_conversation_sends_no_earlier_thinking(tmp_path):
    from kwil.history import HistoryStore, Recorder

    store = HistoryStore(tmp_path / "history.sqlite", eventlog=False)
    try:
        client = ContractClient()
        chat = chat_with(client, recorder=Recorder(store, "web", run_async=False))
        chat.ask(REMEMBER)
        cid = chat.conversation_id
        chat.end_conversation()
        chat.resume(cid)
        chat.ask(RECALL)
        assert thinking_before_last_turn(client.requests[-1]["messages"]) == []
        # neither thinking nor tool results reach history.sqlite
        stored = json.dumps(store.get(cid))
        assert "Planning reply" not in stored and "Physics lab" not in stored
    finally:
        store.close()


def test_a_model_switch_sends_no_earlier_thinking(caplog):
    client = ContractClient()
    chat = chat_with(client)
    chat.ask(REMEMBER)
    chat.model = OTHER  # its signatures are bound to the model that thought
    with caplog.at_level(logging.WARNING, logger="kwil"):
        assert "4417" in chat.ask(RECALL)
    assert thinking_before_last_turn(client.requests[-1]["messages"]) == []
    assert len(client.requests) == 3 and not caplog.records  # no refused retry


def test_router_escalation_is_a_model_switch(caplog):
    client = ContractClient()
    chat = ClaudeChat(client=client, model=MAIN, fast_model=FAST, router="auto",
                      tools=calendar())
    chat.ask("what time is it")  # the fast model
    with caplog.at_level(logging.WARNING, logger="kwil"):
        chat.ask("move my dentist to 3pm")  # the main model from now on
        chat.ask("why did you do that")
    assert [r["model"] for r in client.requests] == [FAST, MAIN, MAIN]
    assert thinking_before_last_turn(client.requests[1]["messages"]) == []
    # the main model's own turn 2 is kept, never the fast model's turn 1
    assert thinking_before_last_turn(client.requests[2]["messages"]) == [
        "Planning reply 2."]
    assert not caplog.records


def test_taint_holds_with_kept_thinking():
    client = ContractClient()
    chat = ClaudeChat(client=client, model=MAIN, tools=calendar(untrusted=True))
    chat.ask(REMEMBER)
    assert chat.tainted()
    chat.ask(RECALL)
    assert thinking_before_last_turn(client.requests[-1]["messages"])
    assert chat.tainted()


def test_an_interrupted_reply_does_not_send_its_thinking_again():
    client = ContractClient()
    chat = chat_with(client)
    chat.ask("tell me a story")
    chat.mark_interrupted("Once upon")
    chat.ask(RECALL)
    assert thinking_before_last_turn(client.requests[-1]["messages"]) == []


class RekeyedClient(ContractClient):
    """The API refuses every earlier signature after turn 1 (a new key on
    its side); `refuse_always`: even a request without them."""

    def __init__(self, refuse_always=False):
        super().__init__()
        self.refuse_always = refuse_always
        self.attempts = []

    def respond(self, kwargs):
        self.attempts.append(thinking_before_last_turn(kwargs["messages"]))
        if self.refuse_always and len(self.attempts) > 2:
            from tests.contract_fakes import bad_request

            raise bad_request("messages.1.content.0: Invalid `signature` in "
                              "`thinking` block")
        return super().respond(kwargs)


def test_a_refused_signature_is_retried_once_without_earlier_thinking(caplog):
    client = RekeyedClient()
    chat = chat_with(client)
    chat.ask(REMEMBER)
    client.key = b"k" * 16  # turn 1's signatures no longer verify
    with caplog.at_level(logging.INFO, logger="kwil"):
        assert "4417" in chat.ask(RECALL)
    assert client.attempts[2:] == [["Planning reply 1.", "Planning reply 2."], []]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "4417" not in warnings[0].getMessage()
    assert "remember" not in warnings[0].getMessage().lower()
    # the reply is signed with the new key: turn 3 sends it again
    chat.ask("Thanks. " + RECALL)
    assert client.attempts[-1] == ["Planning reply 3."]


def test_a_refused_retry_is_not_retried_again():
    client = RekeyedClient(refuse_always=True)
    chat = chat_with(client)
    chat.ask(REMEMBER)
    with pytest.raises(Exception, match="signature"):
        chat.ask(RECALL)
    assert len(client.attempts) == 4  # turn 1's two, then one try and one retry
    assert client.attempts[3] == []
    assert sum(1 for m in chat.history if is_user_turn(m)) == 1  # turn 2 undone


def test_other_400s_and_no_earlier_thinking_are_not_retried(fake_client):
    fake_client.messages.create.side_effect = api_bad_request(
        "messages: Invalid `signature` in `thinking` block")
    chat = ClaudeChat(client=fake_client, model=MAIN)
    with pytest.raises(Exception):
        chat.ask("hi")  # nothing earlier to strip: the error stands
    assert fake_client.messages.create.call_count == 1


# --- 2. trimming by whole turns ------------------------------------------------------

def tool_turn(n: int, rounds: int = 6) -> list:
    """`rounds` tool calls, then a reply: 2 + 2 * rounds messages."""
    return [make_response(tool_use_block("list_calendar_events", {"day": "today"},
                                         id=f"toolu_{n}_{r}"), stop_reason="tool_use")
            for r in range(rounds)] + [make_response(text_block(f"Plan {n}."))]


def assert_pairs_complete(history):
    assert is_user_turn(history[0])
    for i, m in enumerate(history):
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    before = history[i - 1]
                    assert i and before["role"] == "assistant" and any(
                        c.get("id") == b["tool_use_id"] for c in before["content"])


def test_three_tool_heavy_turns_keep_turn_ones_tool_result(fake_client):
    fake_client.queue = tool_turn(1) + tool_turn(2) + tool_turn(3) + [
        make_response(text_block("Because physics was the only gap."))]
    chat = ClaudeChat(client=fake_client, model=MAIN, tools=calendar())  # max_history 40
    for n in (1, 2, 3):
        chat.ask(f"plan block {n}")
    assert len(chat.history) == 42
    chat.ask("why did you block only 3:30 to 4 for physics")
    sent = json.dumps(fake_client.calls[-1]["messages"])
    assert '"toolu_1_0"' in sent and "plan block 1" in sent
    assert_pairs_complete(chat.history)


def test_a_trim_never_leaves_an_orphan_tool_result(fake_client):
    fake_client.queue = [r for n in range(1, 7) for r in tool_turn(n, rounds=n % 3 + 1)]
    chat = ClaudeChat(client=fake_client, model=MAIN, tools=calendar(), max_history=6)
    for n in range(1, 7):
        chat.ask(f"turn {n}")
        assert sum(1 for m in chat.history if is_user_turn(m)) <= 3
        assert_pairs_complete(chat.history)
    # past 3 turns, the newest half (whole turns) stays: turns 4 to 6
    assert [m["content"] for m in chat.history if is_user_turn(m)] == [
        "turn 4", "turn 5", "turn 6"]
    assert len(chat.history) == 6 + 8 + 4  # every tool round of each


# --- 3. room to plan -----------------------------------------------------------------

def test_main_model_tool_turns_get_room_to_plan(fake_client):
    from kwil import cli
    from kwil.config import DEFAULTS, load_config

    assert DEFAULTS["assistant"]["max_tokens"] == 1500
    assert DEFAULTS["assistant"]["max_tokens_tools"] == 4000
    fake_client.queue = [make_response(text_block(t)) for t in "abcd"]
    cfg = load_config(None, None)
    cfg["assistant"].update(model=MAIN, fast_model=FAST, router="auto")
    chat = cli.build_chat(cfg, tools=calendar(), client=fake_client)
    chat.ask("what time is it")  # fast
    chat.ask("move my dentist to 3pm")  # main, with tools
    plain = cli.build_chat(cfg, client=fake_client)
    plain.ask("move my dentist to 3pm")  # main, no tools
    assert [(c["model"], c["max_tokens"]) for c in fake_client.calls] == [
        (FAST, 1500), (MAIN, 4000), (MAIN, 1500)]


# --- 4. the planning prompt ------------------------------------------------------------

def test_the_scheduling_prompt_is_in_a_stable_system(fake_client):
    tools = calendar()
    tools.register(Tool("find_free_time", "Find free gaps.",
                        {"type": "object", "properties": {}}, lambda **kw: {}))
    fake_client.queue = [make_response(text_block("a")), make_response(text_block("b"))]
    chat = ClaudeChat(client=fake_client, model=MAIN, tools=tools)
    chat.ask("plan my afternoon")
    chat.ask("and tomorrow")
    first, second = (json.dumps(c["system"]) for c in fake_client.calls)
    assert first == second
    system = chat.stable_system()
    assert SCHEDULE_PROMPT in system
    for words in ("find_free_time", "never round", "all the free time",
                  "from now, eleven thirty, to five", "reason carefully"):
        assert words in system
    assert SCHEDULE_PROMPT not in ClaudeChat(client=fake_client, tools=calendar()
                                             ).stable_system()


def test_request_messages_without_a_tag_drops_earlier_thinking():
    own = {"type": "thinking", "thinking": "plan", "signature": "s"}
    history = [{"role": "user", "content": "a"},
               {"role": "assistant", "content": [own, {"type": "text", "text": "b"}],
                THINKING_TAG: "x"},
               {"role": "user", "content": "c"}]
    assert request_messages(history)[1] == {"role": "assistant", "content": [
        {"type": "text", "text": "b"}]}
    assert request_messages(history, thinking="y")[1]["content"][0]["type"] == "text"
    assert request_messages(history, thinking="x")[1] == {
        "role": "assistant", "content": [own, {"type": "text", "text": "b"}]}
