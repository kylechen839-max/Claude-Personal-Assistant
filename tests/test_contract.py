"""API-contract tests: realistic multi-turn conversations, the kind mocked
unit tests can't vouch for (the 2026-10-20 "Invalid signature in thinking
block" bug passed every one of them).

Each runs offline against the contract-shaped fake (tests/contract_fakes.py)
and, marked `live`, against the real Anthropic API when ANTHROPIC_API_KEY
is set: CI's `api-contract` job runs them with the cheapest model
(CONTRACT_MODEL, default claude-haiku-5-5) and with the configured default
model, the one that thinks, and the web conversation once more with the
router on (main model plus CONTRACT_MODEL as the fast one), so Haiku's own
requests (its tool versions, cache_control, max_tokens) meet the real API.
Without the key the live ones skip. Read-only: the only tool is a fake
calendar list; nothing is saved anywhere.
"""

from __future__ import annotations

import functools
import os

import pytest

from assistant import smoke, webapp
from assistant.chat import DEFAULT_MODEL, ClaudeChat
from assistant.tools import Tool, ToolRegistry
from tests.contract_fakes import ContractClient

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

TOKEN = "c" * 24 + "-contract-test-token-0123"
BASE = "https://testserver"
CHEAP_MODEL = os.environ.get("CONTRACT_MODEL", "claude-haiku-5-5")
HAVE_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


FAKE_MAIN, FAKE_FAST = "test-model", "test-fast-model"


def _live(model, fast=None):
    return pytest.param(("live", model, fast),
                        id=f"live-{model}" + (f"-routed-{fast}" if fast else ""), marks=[
                            pytest.mark.live,
                            pytest.mark.skipif(not HAVE_KEY,
                                               reason="ANTHROPIC_API_KEY not set")])


PLAIN = [pytest.param(("fake", FAKE_MAIN, None), id="contract-fake"),
         _live(CHEAP_MODEL), _live(DEFAULT_MODEL)]
# [assistant] router = "auto": simple turns go to the fast model
ROUTED = [pytest.param(("fake", FAKE_MAIN, FAKE_FAST), id="contract-fake-routed"),
          _live(DEFAULT_MODEL, CHEAP_MODEL)]


def _make(param):
    """(make_client, model, fast_model): a new client per call. Live: real
    clients, each its own connection pool, as the phone and voice paths
    have."""
    kind, model, fast = param
    if kind == "fake":
        return ContractClient, model, fast
    import anthropic

    return (lambda: anthropic.Anthropic(max_retries=3, timeout=60)), model, fast


@pytest.fixture(params=PLAIN)
def api(request):
    make, model, _fast = _make(request.param)
    return make, model


@pytest.fixture(params=PLAIN + ROUTED)
def web_api(request):
    return _make(request.param)


def calendar_tools(calls: list) -> ToolRegistry:
    """A read-only calendar list (the shape of the real tool), plus a write
    tool that must never be offered to or run by a check session."""
    def list_events(day: str = "today") -> dict:
        calls.append(("list", day))
        return {"day": day, "events": [{"title": "Physics lab", "start": "14:00"},
                                       {"title": "Dentist", "start": "16:30"}]}

    def create_event(**kw) -> dict:
        calls.append(("create", kw))
        return {"status": "created"}

    tools = ToolRegistry()
    tools.register(
        Tool("list_calendar_events", "List Kyle's calendar events for a day.",
             {"type": "object", "properties": {"day": {
                 "type": "string", "enum": ["today", "tomorrow"]}}},
             list_events),
        Tool("create_calendar_event", "Add an event to Kyle's calendar.",
             {"type": "object", "properties": {"title": {"type": "string"}},
              "required": ["title"]}, create_event,
             confirm_if_untrusted=lambda i: "Add it?"))
    return tools


REMEMBER = ("Please remember the number 4417 for this conversation. Also: "
            "what's on my calendar tomorrow? Keep it short.")
RECALL = "What number did I ask you to remember? Reply with just the number."


def test_two_chats_sharing_one_history_then_a_follow_up(api):
    """The bug that broke Kyle's app: hands-free voice continued the
    phone's conversation, so one request's history held a thinking block
    from another. Two ClaudeChat instances (two clients) share one
    history, each takes turns, and every turn must still answer."""
    make, model = api
    calls = []
    tools = calendar_tools(calls)
    phone = ClaudeChat(client=make(), model=model, tools=tools, max_tokens=1024)
    voice = ClaudeChat(client=make(), model=model, tools=tools, max_tokens=1024)
    first = phone.ask(REMEMBER)
    assert first.strip() and any(c[0] == "list" for c in calls)  # the tool loop ran
    voice.history = phone.history  # the voice view continues it
    assert "4417" in voice.ask(RECALL)
    phone.history = voice.history  # back to typing on the phone
    assert "4417" in phone.ask("Thanks. " + RECALL)
    assert not [c for c in calls if c[0] == "create"]


def test_a_mid_turn_escalation_sends_the_main_model_none_of_the_fast_models_turn():
    """Judge B2: the fast model reads the calendar, then asks to delete. Its
    thinking is signed for it, and the API (like this fake) refuses it in
    the main model's request; the old code kept that first round in
    history. The whole turn now runs again on the main model: it reads
    again, asks, and the delete runs once, the main model's."""
    client = ContractClient()
    calls, asked = [], []
    tools = calendar_tools(calls)
    tools.confirm = lambda q: asked.append(q) or True
    tools.register(Tool("delete_calendar_event", "Delete an event from Kyle's calendar.",
                        {"type": "object", "properties": {"event_id": {"type": "string"}},
                         "required": ["event_id"]},
                        lambda event_id: calls.append(("delete", event_id, len(asked)))
                        or {"status": "deleted"},
                        confirm_prompt=lambda i: "Delete the dentist?"))
    chat = ClaudeChat(client=client, model=FAKE_MAIN, fast_model=FAKE_FAST, router="auto",
                      tools=tools, max_tokens=1024)
    reply = chat.ask("Get rid of the dentist thing on my calendar tomorrow.")
    assert reply == "Done, it's off your calendar."
    models = [r["model"] for r in client.requests]
    assert models == [FAKE_FAST, FAKE_FAST, FAKE_MAIN, FAKE_MAIN, FAKE_MAIN]
    for r in client.requests[2:]:  # nothing of the fast model's rounds
        assert len(r["messages"]) in (1, 3, 5)
        assert r["messages"][0]["content"][0]["text"].startswith("Get rid of")
    assert [c[0] for c in calls] == ["list", "list", "delete"]
    assert calls[-1] == ("delete", "dentist-1", 1) and asked == ["Delete the dentist?"]
    assert chat.escalated
    # the next turn stays on the main model and its history is valid
    assert "4417" not in chat.ask("Thanks. " + RECALL)
    assert client.requests[-1]["model"] == FAKE_MAIN


def _request(**overrides):
    kwargs = {"model": FAKE_MAIN, "max_tokens": 64,
              "messages": [{"role": "user", "content": "hi"}]}
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("overrides, error", [
    ({"system": {"type": "text", "text": "x"}}, "system"),
    ({"system": [{"type": "image", "text": "x"}]}, "system.0"),
    ({"messages": [{"role": "user", "content": "hi", "context": "notes"}]}, "Extra inputs"),
    ({"messages": [{"role": "user", "content": "hi", "nudge": "review"}]}, "Extra inputs"),
    ({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "", "cache_control": {"type": "ephemeral"}}]}]},
     "empty text"),
    ({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "t", "signature": "s",
         "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": "more"}]}, "thinking blocks"),
    ({"system": [{"type": "text", "text": str(i), "cache_control": {"type": "ephemeral"}}
                 for i in range(5)]}, "maximum of 4"),
])
def test_the_fake_refuses_what_the_api_refuses(overrides, error):
    """Judge LOW 6: the fake was looser than the API."""
    import anthropic

    with pytest.raises(anthropic.BadRequestError, match=error):
        ContractClient().create(**_request(**overrides))


def test_the_fake_binds_thinking_signatures_to_the_model():
    client = ContractClient()
    first = client.create(**_request(model=FAKE_FAST))
    thinking = first.content[0]
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": [thinking.model_dump(exclude_none=True)]},
               {"role": "user", "content": "and?"}]
    client.create(**_request(model=FAKE_FAST, messages=history))  # its own model: fine
    import anthropic

    with pytest.raises(anthropic.BadRequestError, match="Invalid `signature`"):
        client.create(**_request(model=FAKE_MAIN, messages=history))
    # a well-formed request with the four breakpoints the chat uses at most
    client.create(**_request(system=[{"type": "text", "text": "s",
                                      "cache_control": {"type": "ephemeral"}}]))


class _ModelLog:
    """client.messages (the fake's or the real SDK's), noting each request's
    model: the live run can't see its requests otherwise."""

    def __init__(self, messages, seen: list):
        self._messages, self._seen = messages, seen

    def create(self, **kwargs):
        self._seen.append(kwargs.get("model"))
        return self._messages.create(**kwargs)

    def stream(self, **kwargs):
        self._seen.append(kwargs.get("model"))
        return self._messages.stream(**kwargs)


class _Response:
    """A requests.Response look-alike over an httpx (TestClient) response."""

    def __init__(self, r):
        self.r, self.status_code = r, r.status_code
        self.cookies, self.headers = r.cookies, r.headers

    def json(self):
        return self.r.json()

    def iter_lines(self, decode_unicode=True):
        return iter(self.r.text.splitlines())


class _HTTP:
    """smoke.WebClient's requests.Session, over the in-process app."""

    def __init__(self, client: TestClient):
        self.client = client

    def get(self, url, headers=None, timeout=None, **kw):
        return _Response(self.client.get(url.replace(BASE, ""), headers=headers))

    def post(self, url, headers=None, timeout=None, json=None, files=None,
             allow_redirects=False, stream=False):
        return _Response(self.client.post(url.replace(BASE, ""), headers=headers,
                                          json=json, files=files,
                                          follow_redirects=allow_redirects))


class _Socket:
    def __init__(self, session):
        self.session = session

    def recv(self, timeout=None):
        msg = self.session.receive()
        if msg.get("type") == "websocket.close":
            raise ConnectionError("closed")
        return msg["text"] if msg.get("text") is not None else msg["bytes"]

    def send(self, data):
        (self.session.send_bytes if isinstance(data, bytes) else self.session.send_text)(data)

    def close(self):
        self.session.__exit__(None, None, None)


def _connect(client: TestClient):
    def connect(url, origin, open_timeout, additional_headers):
        headers = {"origin": origin, **{k.lower(): v for k, v in additional_headers.items()}}
        return _Socket(client.websocket_connect("/api/voice", headers=headers).__enter__())
    return connect


def test_realistic_web_conversation_saves_and_writes_nothing(web_api, tmp_path, monkeypatch):
    """What the watchdog runs (smoke --conversation), through the real web
    app, voice socket included: a quick question, text with a read-only
    tool, a follow-up, audio, hands-free voice, then text typed while the
    voice view is open. Every turn answers; nothing reaches history or
    memory; the write tool is neither offered nor run. Routed: the quick
    question goes to the fast model (the check session routes as usual)."""
    from unittest.mock import MagicMock

    from assistant.history import HistoryStore, Recorder
    from tests.voice_fakes import FakeSynth

    make, model, fast = web_api
    client = make()
    seen = []  # the model of each request, in order
    client.messages = _ModelLog(client.messages, seen)
    calls = []
    store = HistoryStore(tmp_path / "history.sqlite", eventlog=False)
    memory = MagicMock()
    memory.retrieve.return_value = []
    routing = {"fast_model": fast, "router": "auto"} if fast else {}

    def chat_factory(registry):
        return ClaudeChat(client=client, model=model, tools=registry, memory=memory,
                          max_tokens=1024, recorder=Recorder(store, "web", run_async=False),
                          **routing)

    class Heard:
        def transcribe(self, recording):
            return "Hey Claude, what's on my calendar tomorrow?"

    assistant = webapp.WebAssistant(chat_factory, Heard(), None,
                                    tools=calendar_tools(calls), history=store,
                                    memory=memory)
    synth = FakeSynth()
    monkeypatch.setattr(assistant, "synthesize",
                        lambda text, session=None, language=None: synth(text))
    monkeypatch.setattr(assistant, "render", lambda engine, text: synth(text))
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600)
    voice_open = []  # live voice conversations at each text or audio turn
    turn = smoke.WebClient.turn
    monkeypatch.setattr(smoke.WebClient, "turn", lambda self, *a, **kw: (
        voice_open.append(len(app.state.voice_conversations)), turn(self, *a, **kw))[1])
    with TestClient(app, base_url=BASE) as http:
        outcomes = smoke.run_smoke(
            BASE, TOKEN, client=smoke.WebClient(BASE, session=_HTTP(http)),
            voice_ws=functools.partial(smoke.voice_turn, connect=_connect(http)),
            conversation=True)
    got = {o.name: o for o in outcomes}
    assert [o.status for o in outcomes if o.status != "PASS"] == [], [
        (o.name, o.status, o.detail) for o in outcomes]
    assert list(got) == ["healthz", "auth required", "login", "check session",
                         "quick turn", "text turn", "tool call", "follow-up turn",
                         "audio turn", "voice turn", "last text turn"]
    # the last text turn is typed while the voice view is still open
    assert voice_open == [0, 0, 0, 0, 1]
    assert store.count() == 0  # no history
    memory.store.assert_not_called()  # no memory summary, signed out included
    memory.retrieve.assert_not_called()  # and no Pinecone search either
    assert not [c for c in calls if c[0] == "create"]
    # the quick question went to the fast model, the rest to the main one
    assert seen[0] == (fast or model) and model in seen
    if isinstance(client, ContractClient):
        offered = {t["name"] for r in client.requests for t in r.get("tools") or []}
        assert offered == {"list_calendar_events"}
