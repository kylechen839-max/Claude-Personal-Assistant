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


# --- round 2 (judge F1-F4) -----------------------------------------------------------

def big_calendar(size: int) -> ToolRegistry:
    tools = ToolRegistry(confirm=lambda q: True)
    tools.register(Tool("list_calendar_events", "List Kyle's events for a day.",
                        {"type": "object", "properties": {}},
                        lambda **kw: {"events": [{"notes": "x" * size}]}))
    return tools


def test_big_tool_results_trim_whole_turns_under_the_size_budget(fake_client, caplog):
    """F1: 20 turns of 15 messages with big results would pass the context
    window; a size budget drops the oldest whole turns."""
    from kwil.chat import history_chars

    fake_client.queue = [r for n in range(1, 7) for r in tool_turn(n, rounds=1)]
    chat = ClaudeChat(client=fake_client, model=MAIN, tools=big_calendar(50_000),
                      max_history_chars=120_000)
    with caplog.at_level(logging.INFO, logger="kwil"):
        for n in range(1, 7):
            chat.ask(f"turn {n}")
            assert history_chars(chat.history) <= 120_000
            assert_pairs_complete(chat.history)  # starts with a user turn
    kept = [m["content"] for m in chat.history if is_user_turn(m)]
    assert kept[-1] == "turn 6" and len(kept) < 6
    assert any("size budget" in r.getMessage() for r in caplog.records)
    assert not any("turn " in r.getMessage() for r in caplog.records)  # no words


def test_the_newest_turn_stays_even_over_the_budget(fake_client):
    fake_client.queue = tool_turn(1, rounds=1)
    chat = ClaudeChat(client=fake_client, model=MAIN, tools=big_calendar(50_000),
                      max_history_chars=10_000)
    chat.ask("turn 1")
    assert chat.history[0]["content"] == "turn 1" and len(chat.history) == 4


def test_only_the_newest_turns_send_their_thinking_again():
    """F1: thinking from more than thinking_turns earlier turns back is
    stripped; the cut moves in steps of thinking_turns (the cache prefix
    changes once every few turns, not every turn)."""
    client = ContractClient()
    chat = ClaudeChat(client=client, model=MAIN)  # thinking_turns 3
    for n in range(7):
        chat.ask(f"tell me thing {n}")
    sent = [thinking_before_last_turn(r["messages"]) for r in client.requests]
    assert sent[5] == [f"Planning reply {n}." for n in range(1, 6)]  # 5 earlier: all
    assert sent[6] == [f"Planning reply {n}." for n in range(4, 7)]  # turns 4-6 only


def refusing(fake_client, too_many_turns: int, always=False):
    """A client that answers "prompt is too long" past `too_many_turns`
    user turns."""
    seen = []

    def create(**kwargs):
        turns = sum(1 for m in kwargs["messages"] if is_user_turn(m))
        seen.append(turns)
        if turns > too_many_turns or (always and len(seen) > too_many_turns):
            raise api_bad_request("prompt is too long: 250000 tokens > 200000 maximum")
        return make_response(text_block(f"reply {len(seen)}"))

    fake_client.messages.create.side_effect = create
    return seen


def test_a_prompt_too_long_400_trims_harder_and_retries_once(fake_client, caplog):
    seen = refusing(fake_client, too_many_turns=3)
    chat = ClaudeChat(client=fake_client, model=MAIN, max_history_chars=0)
    for n in range(3):
        chat.ask(f"q{n}")
    with caplog.at_level(logging.WARNING, logger="kwil"):
        assert chat.ask("q3") == "reply 5"
    assert seen == [1, 2, 3, 4, 3]  # refused once, then a shorter retry
    assert [m["content"] for m in chat.history if is_user_turn(m)] == ["q1", "q2", "q3"]
    [warning] = caplog.records
    assert "dropped 1 earlier turn" in warning.getMessage()
    assert "q0" not in warning.getMessage()
    chat.ask("q4")  # unstuck: the next turn goes through
    assert seen[-1] == 3


def test_a_second_prompt_too_long_raises_and_leaves_a_valid_history(fake_client):
    seen = refusing(fake_client, too_many_turns=3, always=True)
    chat = ClaudeChat(client=fake_client, model=MAIN, max_history_chars=0)
    for n in range(3):
        chat.ask(f"q{n}")
    with pytest.raises(Exception, match="too long"):
        chat.ask("q3")
    assert seen[3:] == [4, 3]  # exactly one retry
    # shorter didn't help: the earlier turns come back, and only the failed
    # turn is undone (found by identity)
    assert chat.history[-1]["role"] == "assistant"
    assert [m["content"] for m in chat.history if is_user_turn(m)] == ["q0", "q1", "q2"]


def thinking_reply(text, stop_reason="end_turn", thought="cut-off plan"):
    from types import SimpleNamespace

    block = SimpleNamespace(type="thinking", thinking=thought, signature="SIG")
    return make_response(block, *([text_block(text)] if text else []),
                         stop_reason=stop_reason)


@pytest.mark.parametrize("reply", [
    thinking_reply("Your afternoon is", stop_reason="max_tokens"),
    thinking_reply(None)])
def test_a_cut_off_or_thinking_only_reply_is_not_sent_again(fake_client, reply):
    """F2: unfinished reasoning (a max_tokens cut, or nothing but thinking)
    isn't tagged, so a later turn never re-sends it."""
    fake_client.queue = [reply, make_response(text_block("Sure."))]
    chat = ClaudeChat(client=fake_client, model=MAIN)
    chat.ask("plan my afternoon")
    assert THINKING_TAG not in chat.history[1]
    chat.ask("go on")
    assert "cut-off plan" not in json.dumps(fake_client.calls[-1]["messages"])


def test_a_finished_reply_is_tagged(fake_client):
    fake_client.queue = [thinking_reply("Done."), make_response(text_block("Sure."))]
    chat = ClaudeChat(client=fake_client, model=MAIN)
    chat.ask("plan my afternoon")
    chat.ask("why")
    assert "cut-off plan" in json.dumps(fake_client.calls[-1]["messages"])


def test_model_a_then_b_then_a_sends_none_of_the_first_turns_thinking(caplog):
    """F4: a model change in between counts, even back to the same model
    (the brief: the conversation wasn't switched since)."""
    client = ContractClient()
    chat = chat_with(client)
    chat.ask(REMEMBER)
    chat.model = OTHER
    chat.ask("tell me a joke")
    chat.model = MAIN
    with caplog.at_level(logging.WARNING, logger="kwil"):
        assert "4417" in chat.ask(RECALL)
    assert thinking_before_last_turn(client.requests[-1]["messages"]) == []
    assert not caplog.records


def test_a_family_members_main_model_tool_turn_has_room_to_plan(fake_client, tmp_path,
                                                                 monkeypatch):
    """F4: family.py passes max_tokens_tools (and the history budgets)."""
    from kwil import family as family_mod
    from kwil.config import load_config

    monkeypatch.setenv("USER_DATA_KEY", "s" * 48)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["history"]["enabled"] = False
    config["web"]["enabled"] = False
    config["assistant"].update(model=MAIN, router="off", max_tokens_tools=4321,
                               max_history_chars=12_345, thinking_turns=2)
    fam = family_mod.Family(config, client=fake_client)
    try:
        user, token = fam.invite("Mom", "en", tier="family")  # the owner's key
        fam.redeem(token, lambda uid: uid)
        chat = fam.context(user["id"]).chat_factory(calendar())
        assert (chat.max_history_chars, chat.thinking_turns) == (12_345, 2)
        fake_client.queue = [make_response(text_block("Done."))]
        chat.ask("move my dentist to 3pm")
        assert fake_client.calls[-1]["max_tokens"] == 4321
    finally:
        fam.close()


class TooLongWithAFile(ContractClient):
    """"prompt is too long" for any request holding a document (one PDF
    over the context window, the judge's probe)."""

    def __init__(self):
        super().__init__()
        self.tries = 0

    def respond(self, kwargs):
        self.tries += 1
        if any(isinstance(m["content"], list) and any(
                b.get("type") == "document" for b in m["content"])
               for m in kwargs["messages"]):
            raise api_bad_request("prompt is too long: 250000 tokens > 200000 maximum")
        return super().respond(kwargs)


def test_an_oversize_file_keeps_every_earlier_turn_and_its_thinking():
    """Round 3: a PDF over the context window used to cost the two oldest
    turns (dropped by the too-long retry, which failed anyway). With a
    file in the turn the file is the cause: no shortening, nothing lost."""
    from kwil.chat import has_earlier_thinking

    client = TooLongWithAFile()
    chat = ClaudeChat(client=client, model=MAIN)
    for n in range(4):
        chat.ask(f"turn {n}")
    pdf = {"type": "document", "title": "big.pdf", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "JVBER"}}
    tries = client.tries
    with pytest.raises(Exception, match="too long"):
        chat.ask("summarize this", attachments=[pdf])
    assert client.tries == tries + 1  # not retried shorter
    assert [m["content"] for m in chat.history if is_user_turn(m)] == [
        f"turn {n}" for n in range(4)]
    assert has_earlier_thinking(chat.history + [{"role": "user", "content": "x"}],
                                chat._thinking_tag(MAIN), chat.thinking_turns)
    chat.ask("never mind, what was turn 1 about")
    assert thinking_before_last_turn(client.requests[-1]["messages"]) == [
        f"Planning reply {n}." for n in range(1, 5)]


def test_a_failed_shorter_retry_restores_earlier_thinking():
    """Without a file: the shortened retry fails too, so the history and
    which thinking may be re-sent are as before the first 400."""
    client = ContractClient()
    chat = ClaudeChat(client=client, model=MAIN)
    for n in range(4):
        chat.ask(f"turn {n}")
    respond, tries = client.respond, []

    def too_long(kwargs):
        tries.append(1)
        raise api_bad_request("prompt is too long: 250000 tokens > 200000 maximum")

    client.respond = too_long
    with pytest.raises(Exception, match="too long"):
        chat.ask("one more")
    assert len(tries) == 2  # one shorter retry
    client.respond = respond
    chat.ask("and again")
    assert [m["content"] for m in chat.history if is_user_turn(m)] == [
        "turn 0", "turn 1", "turn 2", "turn 3", "and again"]
    assert thinking_before_last_turn(client.requests[-1]["messages"]) == [
        f"Planning reply {n}." for n in range(1, 5)]
