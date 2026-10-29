"""Conversation history in the phone app: the JSON API, incognito, and
recording of web and voice-mode turns. Offline (TestClient, mocked
Anthropic and Pinecone)."""

import threading
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import history, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.history import HistoryStore, Recorder  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402
from tests.test_webapp import (BASE, TOKEN, Clock, FakeTranscriber, events,  # noqa: E402
                               login, make_app, wav_bytes)

ROUTES = [("GET", "/api/conversations"), ("GET", "/api/conversations/{id}"),
          ("POST", "/api/conversations/{id}/continue"),
          ("PATCH", "/api/conversations/{id}"), ("DELETE", "/api/conversations/{id}"),
          ("GET", "/api/conversations/{id}/export"), ("GET", "/api/incognito"),
          ("POST", "/api/incognito")]


@pytest.fixture
def web(fake_client, tmp_path):
    yield from make_web(fake_client, tmp_path)


def make_web(fake_client, tmp_path, tools=None):
    store = HistoryStore(tmp_path / "h.sqlite")
    memory = MagicMock()
    memory.retrieve.return_value = []
    memory.search_conversations.return_value = []
    memory.vectors_for.return_value = []

    def chat_factory(registry):
        rec = Recorder(store, "web", run_async=False, client_fn=lambda: fake_client)
        return ClaudeChat(client=fake_client, model="test-model", tools=registry,
                          recorder=rec)

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(),
                                    tools=tools, clock=Clock(), history=store,
                                    memory=memory)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    client = TestClient(app, base_url=BASE)
    yield store, assistant, client, memory
    store.close()


def seed(store, source="wake", turns=("How do error bars work?", "Standard error."),
         title="Lab report error bars", **update):
    cid = store.create(source)
    for i, t in enumerate(turns):
        store.append(cid, "user" if i % 2 == 0 else "assistant", t)
    store.finish(cid, title, f"About {title.lower()}.")
    if update:
        store.update(cid, **update)
    return cid


def delete(client, url, **kwargs):
    """DELETE as the page sends it: with a small JSON body, because the
    server requires a Content-Length on every unsafe request."""
    return client.request("DELETE", url, content=b"{}",
                          headers={"Content-Type": "application/json",
                                   **kwargs.pop("headers", {})}, **kwargs)


def session(assistant):
    [s] = assistant.sessions.values()
    return s


# ---------- auth ----------
def test_every_history_route_needs_the_session_cookie(web):
    store, assistant, client, _ = web
    cid = seed(store)
    for method, path in ROUTES:
        r = client.request(method, path.replace("{id}", cid), json={"on": True})
        assert r.status_code == 401, (method, path)
        assert r.json() == {"error": "not signed in"}
    # a forged cookie doesn't work either
    client.cookies.set(webapp.COOKIE, "abc.def", domain="testserver")
    assert client.get("/api/conversations").status_code == 401
    assert assistant.sessions == {}
    assert store.get(cid)  # nothing was changed


def test_history_routes_reject_cross_origin_requests(web):
    store, _, client, _ = web
    cid = seed(store)
    login(client)
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert client.get("/api/conversations", headers=evil).status_code == 403
    assert delete(client, f"/api/conversations/{cid}", headers=evil).status_code == 403
    assert store.get(cid)


def test_bearer_token_works_for_scripts(web):
    store, _, client, _ = web
    seed(store)
    r = client.get("/api/conversations", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and len(r.json()["conversations"]) == 1


# ---------- ids ----------
def test_ids_cant_be_enumerated(web):
    store, _, client, _ = web
    ids = [seed(store, title=f"Chat {i}") for i in range(5)]
    login(client)
    for guess in ["1", "2", "0", "00000000000000000000", ids[0][:-1] + ("y" if ids[0].endswith("x") else "x"),
                  "%27%20OR%201=1", "conversations"]:
        assert client.get(f"/api/conversations/{guess}").status_code == 404, guess
    assert all(history.valid_id(i) and not i.isdigit() for i in ids)
    listed = [c["id"] for c in client.get("/api/conversations").json()["conversations"]]
    assert sorted(listed) == sorted(ids)


def test_another_owners_conversation_is_invisible(web, tmp_path):
    store, _, client, _ = web
    other = HistoryStore(store.path, owner="someone-else")
    theirs = seed(other, title="Their secret")
    login(client)
    assert client.get("/api/conversations").json()["conversations"] == []
    assert client.get(f"/api/conversations/{theirs}").status_code == 404
    assert client.patch(f"/api/conversations/{theirs}", json={"pinned": True}).status_code == 404
    assert delete(client, f"/api/conversations/{theirs}").status_code == 404
    assert client.get(f"/api/conversations/{theirs}/export").status_code == 404
    assert client.post(f"/api/conversations/{theirs}/continue").status_code == 404
    assert other.get(theirs)["title"] == "Their secret"
    other.close()


# ---------- list and search ----------
def test_list_paginates_newest_first(web):
    store, _, client, _ = web
    ids = []
    for i in range(25):
        ids.append(seed(store, title=f"Chat {i}"))
        time.sleep(0.001)
    login(client)
    page = client.get("/api/conversations?limit=10").json()
    assert [c["id"] for c in page["conversations"]] == ids[::-1][:10]
    assert page["next_offset"] == 10
    page = client.get("/api/conversations?limit=10&offset=20").json()
    assert len(page["conversations"]) == 5 and page["next_offset"] is None
    item = page["conversations"][0]
    assert set(item) == {"id", "title", "live", "source", "started_at", "updated_at",
                         "pinned", "archived", "message_count", "match", "snippet"}


def test_a_conversation_in_progress_is_named_by_its_first_words(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    fake_client.queue = [make_response(text_block("Two events."))]
    client.post("/api/turn", json={"text": "what is on my calendar for tomorrow morning"})
    [c] = client.get("/api/conversations").json()["conversations"]
    assert c["title"] == "What is on my calendar for" and c["live"]


def test_search_returns_highlight_parts(web):
    store, _, client, _ = web
    cid = seed(store)
    seed(store, "text", ("What's the weather?", "Sunny."), "Weather")
    login(client)
    data = client.get("/api/conversations", params={"q": "error bars"}).json()
    [hit] = data["conversations"]
    assert hit["id"] == cid and hit["match"] == "keyword"
    assert ["error", True] in hit["snippet"] and ["bars", True] in hit["snippet"]
    assert "".join(t for t, _ in hit["snippet"]) == "How do error bars work?"
    assert data["query"] == "error bars"


def test_search_adds_semantic_hits(web):
    store, _, client, memory = web
    cid = seed(store, title="Uncertainty in graphs", turns=("graph question", "ok"))
    memory.search_conversations.return_value = [
        {"conversation_id": cid, "score": 0.8, "text": "Kyle asked about uncertainty."}]
    login(client)
    [hit] = client.get("/api/conversations?q=error+margins").json()["conversations"]
    assert hit["id"] == cid and hit["match"] == "semantic"
    assert hit["snippet"] == [["Kyle asked about uncertainty.", False]]


def test_list_filters(web):
    store, _, client, _ = web
    wake = seed(store, "wake", title="A")
    phone = seed(store, "web", title="B")
    pinned = seed(store, "text", title="C", pinned=True)
    archived = seed(store, "voice", title="D", archived=True)
    login(client)

    def ids(qs):
        r = client.get(f"/api/conversations?{qs}")
        assert r.status_code == 200, r.text
        return {c["id"] for c in r.json()["conversations"]}
    assert ids("") == {wake, phone, pinned}
    assert ids("source=web") == {phone}
    assert ids("pinned=true") == {pinned}
    assert ids("archived=true") == {archived}
    today = time.strftime("%Y-%m-%d")
    assert ids(f"since={today}&until={today}") == {wake, phone, pinned}
    assert ids("until=2000-01-01") == set()
    for bad in ("source=email", "since=yesterday", "limit=x", "offset=-"):
        assert client.get(f"/api/conversations?{bad}").status_code == 400, bad


# ---------- transcript, rename, pin, archive ----------
def test_get_transcript(web):
    store, _, client, _ = web
    cid = seed(store)
    store.append(cid, "assistant", "Delete Dentist?", kind="confirm")
    store.append(cid, "user", "yes", kind="confirm")
    store.append(cid, "assistant", "Done.", tool_events=[history.tool_event(
        "delete_calendar_event", {"event_id": "abc"}, "ok")])
    store.mark_interrupted(cid)
    login(client)
    data = client.get(f"/api/conversations/{cid}").json()
    assert data["title"] == "Lab report error bars"
    assert data["summary"] == "About lab report error bars." and not data["active"]
    assert [m["kind"] for m in data["messages"]] == ["chat", "chat", "confirm",
                                                     "confirm", "chat"]
    assert data["messages"][-1]["tools"] == [{"tool": "delete_calendar_event",
                                              "status": "ok"}]
    assert data["messages"][-1]["interrupted"]


def test_patch_rename_pin_archive(web):
    store, _, client, _ = web
    cid = seed(store)
    login(client)
    r = client.patch(f"/api/conversations/{cid}", json={"title": "Stats help", "pinned": True})
    assert r.status_code == 200 and r.json()["title"] == "Stats help" and r.json()["pinned"]
    assert client.patch(f"/api/conversations/{cid}", json={"archived": True}).json()["archived"]
    assert client.get("/api/conversations").json()["conversations"] == []
    for bad in ({"title": ""}, {"title": 5}, {"pinned": "yes"}, {"owner": "x"}, ["x"]):
        assert client.patch(f"/api/conversations/{cid}", json=bad).status_code == 400, bad
    assert store.get(cid)["title"] == "Stats help"


# ---------- delete ----------
def test_delete_cascades_to_pinecone(web):
    store, _, client, memory = web
    cid = seed(store)
    store.add_memory_vector(cid, "voice-abc")
    login(client)
    memory.delete.side_effect = RuntimeError("pinecone down")
    r = delete(client, f"/api/conversations/{cid}")
    assert r.status_code == 502 and store.get(cid)  # kept: retry later
    memory.delete.side_effect = None
    assert delete(client, f"/api/conversations/{cid}").json() == {"ok": True}
    memory.delete.assert_called_with(["voice-abc"])
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert delete(client, f"/api/conversations/{cid}").status_code == 404


def test_deleting_the_open_conversation_clears_it_from_the_chat(web, fake_client):
    store, assistant, client, memory = web
    login(client)
    fake_client.queue = [make_response(text_block("Sure."))]
    r = client.post("/api/turn", json={"text": "remember my locker code is 1234"})
    assert '"reply"' in r.text
    s = session(assistant)
    cid = s.chat.conversation_id
    assert client.get("/api/state").json()["conversation"] == cid
    assert delete(client, f"/api/conversations/{cid}").status_code == 200
    assert s.chat.history == [] and s.chat.conversation_id is None
    assistant.reap()
    memory.store.assert_not_called()  # nothing left to summarize


# ---------- export ----------
def test_export_downloads(web, monkeypatch):
    store, _, client, _ = web
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "z" * 30)
    cid = seed(store, turns=("my key is sk-ant-api03-" + "z" * 30, "Don't share it."),
               title="Keys & things")
    login(client)
    r = client.get(f"/api/conversations/{cid}/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'filename="Keys-things.md"' in r.headers["content-disposition"]
    assert "zzzz" not in r.text and "# Keys & things" in r.text
    r = client.get(f"/api/conversations/{cid}/export?format=json")
    assert r.json()["title"] == "Keys & things" and "zzzz" not in r.text
    assert client.get(f"/api/conversations/{cid}/export?format=pdf").status_code == 400


# ---------- continue ----------
def test_continue_loads_the_conversation_into_this_chat(web, fake_client):
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    r = client.post(f"/api/conversations/{cid}/continue")
    assert r.status_code == 200 and r.json()["active"] and r.json()["title"] == \
        "Lab report error bars"
    s = session(assistant)
    assert s.chat.conversation_id == cid
    fake_client.queue = [make_response(text_block("Use the SD."))]
    client.post("/api/turn", json={"text": "and standard deviation?"})
    sent = fake_client.calls[-1]["messages"]
    assert sent[0]["content"] == "How do error bars work?"
    assert store.get(cid)["message_count"] == 4 and store.count() == 1
    assert client.get(f"/api/conversations/{cid}").json()["active"]
    assert client.post("/api/conversations/nope-nope-nope-1/continue").status_code == 404


def test_continue_ends_the_current_conversation_first(web, fake_client):
    store, assistant, client, _ = web
    old = seed(store)
    login(client)
    fake_client.queue = [make_response(text_block("Sunny.")),
                         make_response(text_block('{"title": "Weather", "summary": "s"}'))]
    client.post("/api/turn", json={"text": "weather?"})
    current = session(assistant).chat.conversation_id
    fake_client.with_options.return_value = fake_client
    assert client.post(f"/api/conversations/{old}/continue").status_code == 200
    assert store.get(current)["ended_at"] and store.get(current)["title"] == "Weather"


def test_continue_refused_while_busy_or_incognito(web):
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    client.get("/api/state")
    s = session(assistant)
    s.busy.acquire()
    try:
        assert client.post(f"/api/conversations/{cid}/continue").status_code == 409
    finally:
        s.busy.release()
    assert client.post("/api/incognito", json={"on": True}).json() == {"on": True}
    assert client.post(f"/api/conversations/{cid}/continue").status_code == 409


# ---------- incognito ----------
def test_incognito_toggle_skips_saving(web, fake_client):
    store, assistant, client, memory = web
    login(client)
    fake_client.queue = [make_response(text_block("Hi.")),
                         make_response(text_block('{"title": "Greeting", "summary": "s"}')),
                         make_response(text_block("Shh."))]
    client.post("/api/turn", json={"text": "hello"})
    saved = session(assistant).chat.conversation_id
    assert client.get("/api/incognito").json() == {"on": False}
    assert client.post("/api/incognito", json={"on": True}).json() == {"on": True}
    assert store.get(saved)["ended_at"]  # the saved one was finished first
    assert client.get("/api/state").json()["incognito"] is True
    client.post("/api/turn", json={"text": "a private thing"})
    assert store.count() == 1 and session(assistant).chat.conversation_id is None
    assert client.get("/api/conversations?q=private").json()["conversations"] == []
    assistant.reap()
    s = session(assistant)
    s.last_active = -10_000
    assistant.reap()
    memory.store.assert_not_called()
    assert client.post("/api/incognito", json={"on": False}).json() == {"on": False}
    assert client.post("/api/incognito", json={"on": "yes"}).status_code == 400


# ---------- recording web and voice turns ----------
def test_web_turns_are_recorded_with_source_web(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    fake_client.queue = [make_response(text_block("Two events."))]
    client.post("/api/turn", json={"text": "what's on tomorrow"})
    [conv] = store.list()
    assert conv["source"] == "web" and conv["message_count"] == 2


def test_voice_mode_stream_is_recorded_and_barge_in_marked(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    client.get("/api/state")
    s = session(assistant)
    cancel = threading.Event()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            yield "Once upon a time, "
            cancel.set()  # Kyle starts talking
            yield "there was a very long story."

    fake_client.messages.stream.side_effect = lambda **kw: Stream()
    list(assistant.chat_stream(s, "tell me a story", cancel))
    [conv] = store.list()
    assert conv["source"] == "voice"
    msgs = store.get(conv["id"])["messages"]
    assert msgs[-1]["interrupted"] and msgs[-1]["text"] == "Once upon a time,"


# ---------- typing into an opened conversation (PR 3a) ----------
# The page opens a stored conversation read-only (GET) and binds its chat
# pane to it; every turn typed there carries `conversation_id`, and the
# first one continues that conversation on the server, however long ago it
# ended.
def bound_turn(client, cid, text="and standard deviation?", **extra):
    return client.post("/api/turn", json={"text": text, "conversation_id": cid,
                                          **extra})


def done_of(response):
    [done] = [e for e in events(response) if e["type"] == "done"]
    return done


def test_a_turn_with_conversation_id_continues_that_conversation(web, fake_client):
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    fake_client.queue = [make_response(text_block("Use the SD."))]
    r = bound_turn(client, cid)
    assert done_of(r) == {"type": "done", "conversation": cid}
    assert session(assistant).chat.conversation_id == cid
    assert fake_client.calls[-1]["messages"][0]["content"] == "How do error bars work?"
    assert store.count() == 1 and store.get(cid)["message_count"] == 4
    # the next bound turn just goes on in it (nothing ended, nothing rebuilt)
    fake_client.queue = [make_response(text_block("Yes."))]
    assert done_of(bound_turn(client, cid, "really?"))["conversation"] == cid
    assert store.count() == 1 and store.get(cid)["message_count"] == 6


def test_an_unbound_turns_done_carries_its_conversation(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    fake_client.queue = [make_response(text_block("Sunny."))]
    done = done_of(client.post("/api/turn", json={"text": "weather?"}))
    assert done["conversation"] == session(assistant).chat.conversation_id
    assert done["conversation"] and store.get(done["conversation"])


def test_an_audio_turn_carries_conversation_id_as_a_form_field(web, fake_client):
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    assistant.transcriber = FakeTranscriber("and standard deviation?")
    fake_client.queue = [make_response(text_block("Use the SD."))]
    r = client.post("/api/turn", data={"conversation_id": cid},
                    files={"audio": ("speech.wav", wav_bytes(), "audio/wav")})
    assert done_of(r)["conversation"] == cid
    assert store.count() == 1 and store.get(cid)["message_count"] == 4


def first_turn(client, fake_client, assistant):
    fake_client.queue = [make_response(text_block("Sunny."))]
    client.post("/api/turn", json={"text": "weather today?"})
    return session(assistant).chat.conversation_id


def test_a_bound_turn_after_the_idle_timeout_continues_the_same_one(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    cid = first_turn(client, fake_client, assistant)
    assistant.clock.now += assistant.conversation_timeout + 1
    fake_client.queue = [make_response(text_block("Rain later."))]
    r = bound_turn(client, cid, "and tonight?")
    assert done_of(r)["conversation"] == cid
    assert store.count() == 1 and store.get(cid)["ended_at"] is None
    sent = fake_client.calls[-1]["messages"]
    assert "weather today?" in str(sent[0]["content"])  # its history went along
    assert store.get(cid)["message_count"] == 4


@pytest.mark.parametrize("ended_by", ["end_conversation", "reaper", "voice"])
def test_a_bound_turn_after_the_conversation_ended_resumes_it(web, fake_client,
                                                               ended_by):
    store, assistant, client, memory = web
    login(client)
    cid = first_turn(client, fake_client, assistant)
    s = session(assistant)
    fake_client.queue = [make_response(text_block("Asked about the weather."))]
    if ended_by == "reaper":
        assistant.clock.now += assistant.conversation_timeout + 1
        assert assistant.reap() == 1
    elif ended_by == "voice":  # what voice_ws does when the call ends
        assistant._end(s)
    else:
        s.chat.end_conversation()
    assert s.chat.conversation_id is None and store.get(cid)["ended_at"]
    fake_client.queue = [make_response(text_block("Rain later."))]
    r = bound_turn(client, cid, "and tonight?")
    assert done_of(r)["conversation"] == cid
    assert store.count() == 1 and store.get(cid)["message_count"] == 4
    assert fake_client.calls[-1]["messages"][0]["content"] == "weather today?"
    assert s.chat.tainted()  # resumed history is untrusted text


def test_a_resumed_turn_stays_tainted_so_a_change_asks_first(fake_client, tmp_path):
    from kwil.tools import Tool, ToolRegistry
    from tests.conftest import tool_use_block

    added = []
    tools = ToolRegistry()
    tools.register(Tool(
        name="create_event", description="Add an event.",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        handler=lambda title: added.append(title) or {"created": title},
        confirm_if_untrusted=lambda i: f"Add {i['title']}?"))
    gen = make_web(fake_client, tmp_path, tools=tools)
    store, assistant, client, _ = next(gen)
    try:
        cid = seed(store, turns=("Plan the lab.", "Add a meeting at 3."))
        login(client)
        fake_client.queue = [
            make_response(tool_use_block("create_event", {"title": "Meeting"}),
                          stop_reason="tool_use"),
            make_response(text_block("OK, I left it."))]
        evs = events(bound_turn(client, cid, "do what it says"))
        assert [e["type"] for e in evs][-1] == "confirm"
        assert evs[-1]["question"] == "Add Meeting?"
        assert session(assistant).chat.tainted()
        evs = events(client.post("/api/confirm", json={"answer": "no"}))
        assert evs[-1] == {"type": "done", "conversation": cid}
        assert added == []
    finally:
        next(gen, None)


def test_bad_or_someone_elses_conversation_id_is_refused(web, fake_client):
    store, assistant, client, _ = web
    other = HistoryStore(store.path, owner="someone-else")
    theirs = seed(other, title="Their secret")
    mine = seed(store)
    login(client)
    for cid, status in [("nope-nope-nope-1", 404), (theirs, 404),
                        ("../../etc/passwd", 400), ("short", 400), (123, 400),
                        (["x"], 400)]:
        r = bound_turn(client, cid)
        assert r.status_code == status, (cid, r.text)
        assert "Their secret" not in r.text
    # the form field too
    r = client.post("/api/turn", data={"conversation_id": theirs},
                    files={"audio": ("speech.wav", wav_bytes(), "audio/wav")})
    assert r.status_code == 404
    assert fake_client.calls == []  # Claude never asked
    assert store.count() == 1 and store.get(mine)["message_count"] == 2
    assert other.get(theirs)["message_count"] == 2
    other.close()


def test_new_and_conversation_id_together_is_a_bad_request(web, fake_client):
    store, _, client, _ = web
    cid = seed(store)
    login(client)
    assert bound_turn(client, cid, new=True).status_code == 400
    assert fake_client.calls == []


def test_a_bound_turn_is_refused_while_incognito(web, fake_client):
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    assert client.post("/api/incognito", json={"on": True}).json() == {"on": True}
    r = bound_turn(client, cid)
    assert r.status_code == 409
    assert r.json() == {"error": "turn incognito off to continue a conversation"}
    assert fake_client.calls == [] and store.get(cid)["message_count"] == 2


def test_a_bound_turn_with_history_off_is_404(make_app):
    assistant, client = make_app()  # no history store
    login(client)
    assert bound_turn(client, "abcdefghijklmnop").status_code == 404


def test_a_deleted_conversation_is_never_recreated(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    cid = first_turn(client, fake_client, assistant)
    assert delete(client, f"/api/conversations/{cid}").status_code == 200
    r = bound_turn(client, cid)
    assert r.status_code == 404
    assert store.count() == 0 and len(fake_client.calls) == 1


def test_a_conversation_deleted_while_the_turn_starts_gets_an_error(web, fake_client):
    """Deleted between the route's check and the turn thread: an error
    event, nothing sent to Claude and nothing recorded."""
    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    client.get("/api/state")
    s = session(assistant)
    history.delete_conversation(store, cid, None)
    assert assistant.start_turn(s, "hello?", resume=cid)
    evs = list(assistant.events(s, max_wait=5))
    assert [e["type"] for e in evs] == ["error", "done"]
    assert evs[0]["gone"] is True and evs[-1]["conversation"] is None
    assert store.count() == 0 and fake_client.calls == []
    assert s.chat.history == []


def test_new_true_ends_the_current_conversation_first(web, fake_client):
    store, assistant, client, _ = web
    login(client)
    cid = first_turn(client, fake_client, assistant)
    fake_client.queue = [make_response(text_block("Asked about the weather.")),
                         make_response(text_block("Hello again."))]
    r = client.post("/api/turn", json={"text": "something else", "new": True})
    fresh = done_of(r)["conversation"]
    assert fresh and fresh != cid
    assert store.get(cid)["ended_at"] and store.count() == 2
    sent = fake_client.calls[-1]["messages"]
    assert len(sent) == 1  # the old conversation's history didn't go along


def test_resume_logs_only_ids_at_info(web, fake_client, caplog):
    import logging

    store, assistant, client, _ = web
    cid = seed(store)
    login(client)
    fake_client.queue = [make_response(text_block("Use the SD."))]
    with caplog.at_level(logging.DEBUG, logger="kwil"):
        bound_turn(client, cid, "and standard deviation?")
    info = "\n".join(r.getMessage() for r in caplog.records
                     if r.levelno >= logging.INFO)
    assert f"continuing conversation {cid}" in info
    for text in ("standard deviation", "error bars", "Use the SD", "Lab report"):
        assert text not in info, text
