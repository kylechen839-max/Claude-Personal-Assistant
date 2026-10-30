""""Think harder" (thinkhard.py): one turn on the main model with more room
to think, adaptive thinking and an effort, through the contract fake (it
refuses what the API refuses: budget_tokens, an unknown effort...); never
sticky; capped per person per day; within an invited person's own model;
and earlier turns' thinking stays valid across the switch it makes."""

from __future__ import annotations

import logging

import pytest

from kwil import thinkhard
from kwil.chat import ClaudeChat, has_earlier_thinking
from kwil.config import DEFAULTS
from kwil.quotas import UserQuotas
from tests.contract_fakes import ContractClient

MAIN, FAST = "test-model", "test-fast-model"


@pytest.fixture(autouse=True)
def _owner_count():
    thinkhard.reset_owner()
    yield
    thinkhard.reset_owner()


def chat_for(client, gate=lambda: True, **kw) -> ClaudeChat:
    kw.setdefault("router", "auto")
    kw.setdefault("fast_model", FAST)
    return ClaudeChat(client=client, model=MAIN, max_tokens=1024, think_gate=gate, **kw)


def test_a_think_turn_uses_the_main_model_more_room_and_adaptive_thinking(caplog):
    client = ContractClient()
    chat = chat_for(client)
    with caplog.at_level(logging.INFO, logger="kwil"):
        assert chat.ask("hi there", think=True).strip()
    (sent,) = client.requests
    assert sent["model"] == MAIN  # "hi there" alone would go to the fast model
    assert sent["max_tokens"] == thinkhard.DEFAULT_MAX_TOKENS == 16000
    assert sent["thinking"] == {"type": "adaptive"}  # never budget_tokens
    assert sent["output_config"] == {"effort": "xhigh"}
    assert chat.think_turn is True and chat.think_refused is False
    assert f"Route: {MAIN} (think)" in caplog.text
    assert "Think harder: on for this turn" in caplog.text
    assert "hi there" not in caplog.text  # never the words at INFO


def test_the_flag_is_never_sticky():
    client = ContractClient()
    chat = chat_for(client, router="off")
    chat.ask("first, think about it", think=True)
    chat.ask("and the next one?")
    first, second = client.requests
    assert "thinking" in first and "output_config" in first
    assert "thinking" not in second and "output_config" not in second
    assert second["max_tokens"] == 1024
    assert chat.think_turn is False


@pytest.mark.parametrize("words", [
    "think hard about my study plan", "Think harder about this.",
    "please think really hard about it", "Hey Kwil, think hard about the exam",
    "think it through: should I drop the class?", "仔细想想我该怎么复习",
    "请认真想一想这个问题"])
def test_the_spoken_phrase_asks_for_it(words):
    assert thinkhard.asked(words)
    client = ContractClient()
    chat_for(client).ask(words)
    assert client.requests[0]["model"] == MAIN and "thinking" in client.requests[0]


@pytest.mark.parametrize("words", [
    "I think hard drives fail", "what do you think?", "don't think too hard",
    "hard to think about", "我想想", ""])
def test_ordinary_words_dont(words):
    assert not thinkhard.asked(words)


def test_the_cap_per_day_then_an_ordinary_turn(caplog):
    config = {"assistant": {**DEFAULTS["assistant"], "think_per_day": 2}}
    client = ContractClient()
    chat = chat_for(client, gate=thinkhard.owner_gate(config), router="off")
    for _ in range(2):
        chat.ask("tell me", think=True)
        assert chat.think_turn
    with caplog.at_level(logging.INFO, logger="kwil"):
        chat.ask("tell me", think=True)
    assert chat.think_refused and not chat.think_turn
    assert "thinking" not in client.requests[-1]
    assert client.requests[-1]["max_tokens"] == 1024
    assert "refused (daily limit)" in caplog.text
    # every owner chat of this process shares the count
    other = chat_for(ContractClient(), gate=thinkhard.owner_gate(config))
    other.ask("again", think=True)
    assert other.think_refused


def test_no_gate_means_no_think_turn():
    client = ContractClient()
    chat = chat_for(client, gate=None, router="off")
    chat.ask("tell me", think=True)
    assert chat.think_refused and "thinking" not in client.requests[0]


def test_a_broken_counter_holds_the_cap():
    def broken():
        raise RuntimeError("boom")

    client = ContractClient()
    chat = chat_for(client, gate=broken, router="off")
    assert chat.ask("tell me", think=True).strip()
    assert chat.think_refused and "thinking" not in client.requests[0]


def test_the_count_rolls_over_with_the_day_and_is_per_person():
    now = [1_700_000_000.0]
    q = UserQuotas(0, 0, 0, think_per_day=1, clock=lambda: now[0])
    assert q.admit_think("u_a") and not q.admit_think("u_a")
    assert q.admit_think("u_b")  # someone else's count
    now[0] += 86400
    assert q.admit_think("u_a")
    unlimited = UserQuotas(0, 0, 0, think_per_day=0)
    assert all(unlimited.admit_think("u_a") for _ in range(50))
    removed = UserQuotas(0, 0, 0, think_per_day=5, is_listed=lambda uid: False)
    assert not removed.admit_think("u_gone")


def test_kept_thinking_stays_valid_across_the_switch():
    """Turn 1 on the fast model, turn 2 thinks harder on the main one (a
    model switch: the fast model's signed thinking must not be sent, the
    fake would refuse it), turn 3 stays on the main model and gets turn
    2's thinking back (same model, same chat)."""
    client = ContractClient()
    chat = chat_for(client)
    chat.ask("hi")
    chat.ask("now the plan", think=True)
    chat.ask("ok, and?")
    assert [r["model"] for r in client.requests] == [FAST, MAIN, MAIN]
    assert "Planning reply 1." not in str(client.requests[1]["messages"])
    assert "Planning reply 2." in str(client.requests[2]["messages"])
    assert has_earlier_thinking(chat.history, chat._thinking_tag(MAIN))
    assert "thinking" not in client.requests[2]


def test_not_streamed_the_ceiling_stays_under_the_sdks_limit():
    client = ContractClient()
    chat = chat_for(client, router="off", think_max_tokens=50_000)
    chat.ask("go", think=True)
    assert client.requests[0]["max_tokens"] == thinkhard.MAX_UNSTREAMED_TOKENS
    chat.ask("go", think=True, on_text=lambda d: None)  # streamed: as configured
    assert client.requests[1]["max_tokens"] == 50_000


def test_no_effort_sends_only_the_thinking_shape():
    client = ContractClient()
    chat_for(client, router="off", think_effort="").ask("go", think=True)
    assert "output_config" not in client.requests[0]
    assert client.requests[0]["thinking"] == {"type": "adaptive"}


def test_options_from_config_and_defaults(caplog):
    a = DEFAULTS["assistant"]
    assert (a["think_max_tokens"], a["think_effort"], a["think_per_day"]) == (16000, "xhigh", 30)
    with caplog.at_level(logging.WARNING, logger="kwil"):
        opts = thinkhard.options_from_config({"assistant": {**a, "think_effort": "huge"}})
    assert opts == {"think_max_tokens": 16000, "think_effort": "xhigh"}
    assert "think_effort" in caplog.text


def test_family_members_count_their_own_and_keep_their_model(tmp_path, monkeypatch):
    """An invited person's chat (family.py) thinks on the chat's own model
    (the configured one: no tier gets a model beyond it) and counts in
    Family.quotas under their own id, apart from everyone else's."""
    from kwil import family as family_mod
    from kwil.config import load_config
    from kwil.tools import ToolRegistry

    monkeypatch.setenv("USER_DATA_KEY", "s" * 48)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["history"]["enabled"] = False
    config["web"]["enabled"] = False
    config["assistant"].update(model=MAIN, fast_model=FAST, router="auto",
                               think_per_day=1)
    client = ContractClient()
    fam = family_mod.Family(config, client=client)
    try:
        mom, token = fam.invite("Mom", "en", tier="family")  # the owner's key
        fam.redeem(token, lambda uid: uid)
        dad, token = fam.invite("Dad", "en", tier="family")
        fam.redeem(token, lambda uid: uid)
        chat = fam.context(mom["id"]).chat_factory(ToolRegistry())
        chat.ask("hi", think=True)
        assert chat.think_turn and client.requests[-1]["model"] == MAIN == chat.model
        assert client.requests[-1]["thinking"] == {"type": "adaptive"}
        chat.ask("hi again", think=True)  # her one for today is used
        assert chat.think_refused and "thinking" not in client.requests[-1]
        assert fam.quotas.usage[mom["id"]].think == 1
        other = fam.context(dad["id"]).chat_factory(ToolRegistry())
        other.ask("hello", think=True)  # his own count
        assert other.think_turn
    finally:
        fam.close()


# ---------- the phone app ----------

def web_app(chat_factory):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from kwil import webapp
    from kwil.tts import PrintTTS
    from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(), clock=Clock())
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    client = TestClient(app, base_url=BASE)
    login(client)
    return assistant, client


def test_the_page_asks_for_one_think_turn_and_hears_when_its_over(caplog):
    from tests.test_webapp import events

    api = ContractClient()
    gate_calls = []

    def gate():
        gate_calls.append(1)
        return len(gate_calls) <= 1

    _, client = web_app(lambda registry: ClaudeChat(
        client=api, model=MAIN, fast_model=FAST, router="auto", tools=registry,
        max_tokens=1024, think_gate=gate))
    with caplog.at_level(logging.INFO, logger="kwil"):
        evs = events(client.post("/api/turn", json={"text": "hello", "think": True}))
    reply = next(e for e in evs if e["type"] == "reply")
    assert reply["think"] is True and api.requests[-1]["model"] == MAIN
    assert api.requests[-1]["thinking"] == {"type": "adaptive"}
    assert "hello" not in caplog.text
    evs = events(client.post("/api/turn", json={"text": "hello"}))  # not asked
    assert "think" not in next(e for e in evs if e["type"] == "reply")
    assert "thinking" not in api.requests[-1] and len(gate_calls) == 1
    evs = events(client.post("/api/turn", json={"text": "hello", "think": True}))
    notes = [e["text"] for e in evs if e["type"] == "status"]
    assert notes == ["That's all your think-harder replies for today, so this one was "
                     "answered the usual way."]
    assert "thinking" not in api.requests[-1]
    # a form (push-to-talk with files) says it as "1"; anything else is no
    events(client.post("/api/turn", files={"text": (None, "hello"), "think": (None, "yes")}))
    assert len(gate_calls) == 2
    events(client.post("/api/turn", files={"text": (None, "hello"), "think": (None, "1")}))
    assert len(gate_calls) == 3
