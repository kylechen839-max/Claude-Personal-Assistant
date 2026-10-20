"""API-contract tests: realistic multi-turn conversations, the kind mocked
unit tests can't vouch for (the 2026-10-20 "Invalid signature in thinking
block" bug passed every one of them).

Each runs offline against the contract-shaped fake (tests/contract_fakes.py)
and, marked `live`, against the real Anthropic API when ANTHROPIC_API_KEY
is set: CI's `api-contract` job runs them with the cheapest model
(CONTRACT_MODEL, default claude-haiku-5-5) and with the configured default
model, the one that thinks. Without the key the live ones skip. Read-only:
the only tool is a fake calendar list; nothing is saved anywhere.
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


def _live(model):
    return pytest.param(("live", model), id=f"live-{model}", marks=[
        pytest.mark.live,
        pytest.mark.skipif(not HAVE_KEY, reason="ANTHROPIC_API_KEY not set")])


@pytest.fixture(params=[pytest.param(("fake", "test-model"), id="contract-fake"),
                        _live(CHEAP_MODEL), _live(DEFAULT_MODEL)])
def api(request):
    """(make_client, model): a new client per call. Live: real clients,
    each its own connection pool, as the phone and voice paths have."""
    kind, model = request.param
    if kind == "fake":
        return ContractClient, model
    import anthropic

    return (lambda: anthropic.Anthropic(max_retries=3, timeout=60)), model


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


def test_realistic_web_conversation_saves_and_writes_nothing(api, tmp_path, monkeypatch):
    """What the watchdog runs (smoke --conversation), through the real web
    app, voice socket included: text with a read-only tool, a follow-up,
    audio, hands-free voice, then text typed while the voice view is open.
    Every turn answers; nothing reaches history or memory; the write tool
    is neither offered nor run."""
    from unittest.mock import MagicMock

    from assistant.history import HistoryStore, Recorder
    from tests.voice_fakes import FakeSynth

    make, model = api
    client = make()
    calls = []
    store = HistoryStore(tmp_path / "history.sqlite", eventlog=False)
    memory = MagicMock()
    memory.retrieve.return_value = []

    def chat_factory(registry):
        return ClaudeChat(client=client, model=model, tools=registry, memory=memory,
                          max_tokens=1024, recorder=Recorder(store, "web", run_async=False))

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
                         "text turn", "tool call", "follow-up turn", "audio turn",
                         "voice turn", "last text turn"]
    # the last text turn is typed while the voice view is still open
    assert voice_open == [0, 0, 0, 1]
    assert store.count() == 0  # no history
    memory.store.assert_not_called()  # no memory summary, signed out included
    memory.retrieve.assert_not_called()  # and no Pinecone search either
    assert not [c for c in calls if c[0] == "create"]
    if isinstance(client, ContractClient):
        offered = {t["name"] for r in client.requests for t in r.get("tools") or []}
        assert offered == {"list_calendar_events"}
