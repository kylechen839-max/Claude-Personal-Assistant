"""Public beta piece G6: an invited person's own Notion through public OAuth
(assistant/notion_web.py, family.Family's notion_* methods, userkeys.seal).

All offline: every Notion call goes through notion_client's real SDK over
an httpx.MockTransport (FakeNotion below) answering with Notion's real
response shapes, and asserting the Basic auth and JSON bodies the OAuth
endpoints expect. Kyle's NOTION_TOKEN is set throughout and must never be
sent."""

import base64
import json
import logging
import threading
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
pytest.importorskip("notion_client")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import family as family_mod, i18n, notion_web, userkeys, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS, load_config  # noqa: E402
from assistant.tools import ToolRegistry  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block, tool_use_block  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, events, login  # noqa: E402

ORIGIN = "https://hey-claude.example.com"
SECRET = "s" * 48                                   # USER_DATA_KEY
CLIENT_ID = "2a8d872b-594c-80a1-b4c5-0037d1a0e4f1"
CLIENT_SECRET = "secret_" + "C" * 43
KYLES = "ntn_" + "K" * 46                            # NOTION_TOKEN: Kyle's own
ACCESS = "ntn_" + "A" * 46
ACCESS_2 = "ntn_" + "B" * 46
REFRESH = "nrt_" + "R" * 40
CODE = "03b2c7e4-code-9d1f-a0b1-5c3d2e1f0a9b"
EMAIL = "tess.private@example.com"
WORKSPACE = "Tess's Workspace"
DB_TITLE = "Fall Semester Assignments"
TASK = "Essay on the Peloponnesian War"
DB_ID = "1a2b3c4d-0000-4000-8000-00000000d0b1"
DS_ID = "1a2b3c4d-0000-4000-8000-0000000000d5"
TEMPLATE = "1a2b3c4d-0000-4000-8000-00000000a9e0"


def token_answer(**over) -> dict:
    """POST /v1/oauth/token, authorization_code grant (Notion's shape)."""
    return {"access_token": ACCESS, "token_type": "bearer", "refresh_token": REFRESH,
            "bot_id": "b0t-1d", "workspace_id": "w0rk-1d", "workspace_name": WORKSPACE,
            "workspace_icon": "https://example.com/icon.png",
            "owner": {"type": "user", "user": {
                "object": "user", "id": "us3r-1d", "name": "Tess", "avatar_url": None,
                "type": "person", "person": {"email": EMAIL}}},
            "duplicated_template_id": None, "request_id": "req-1", **over}


def data_source(ds_id=DS_ID, db_id=DB_ID, title=DB_TITLE, page=None, **props) -> dict:
    """A search result under Notion-Version 2025-09-03."""
    properties = {
        "Assignment": {"id": "title", "name": "Assignment", "type": "title", "title": {}},
        "Status": {"id": "st", "name": "Status", "type": "status", "status": {
            "options": [{"id": "1", "name": "Not started", "color": "default"},
                        {"id": "2", "name": "In progress", "color": "blue"},
                        {"id": "3", "name": "Done", "color": "green"}],
            "groups": []}},
        "Due": {"id": "due", "name": "Due", "type": "date", "date": {}},
        "Course": {"id": "c", "name": "Course", "type": "select", "select": {"options": []}},
    }
    properties.update(props)
    return {"object": "data_source", "id": ds_id,
            "parent": {"type": "database_id", "database_id": db_id},
            "database_parent": {"type": "page_id", "page_id": page or "p4ge-0"},
            "title": [{"type": "text", "plain_text": title, "text": {"content": title}}],
            "properties": properties}


def page(name=TASK, status="Not started", pid="pa9e-1") -> dict:
    return {"object": "page", "id": pid, "properties": {
        "Assignment": {"id": "title", "type": "title",
                       "title": [{"type": "text", "plain_text": name}]},
        "Status": {"id": "st", "type": "status", "status": {"name": status}},
        "Due": {"id": "due", "type": "date", "date": {"start": "2026-10-25"}}}}


def unauthorized():
    return httpx.Response(401, json={"object": "error", "status": 401,
                                     "code": "unauthorized",
                                     "message": "API token is invalid.",
                                     "request_id": "req-x"})


class FakeNotion:
    """api.notion.com, as an httpx MockTransport handler."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answer = token_answer()
        self.refresh_answer = {"access_token": ACCESS_2, "token_type": "bearer",
                               "refresh_token": None, "bot_id": "b0t-1d",
                               "workspace_id": "w0rk-1d", "workspace_name": WORKSPACE}
        self.refresh_status = 200
        self.exchange_status = 200
        self.revoke_status = 200
        self.search = {"object": "list", "results": [data_source()], "next_cursor": None,
                       "has_more": False, "type": "page_or_data_source",
                       "page_or_data_source": {}}
        self.valid = {ACCESS}            # access tokens Notion accepts
        self.on_401 = None               # called (in the request's thread) before a 401
        self.query_status = 200
        self.query_body = None

    def calls(self, path):
        return [r for r in self.requests if r.url.path == path]

    def body(self, request):
        return json.loads(request.content or b"{}")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in ("/v1/oauth/token", "/v1/oauth/revoke"):
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            assert request.headers["Authorization"] == "Basic " + expected
            assert request.headers["Content-Type"] == "application/json"
            body = self.body(request)
            if path == "/v1/oauth/revoke":
                assert set(body) == {"token"}
                if self.revoke_status != 200:
                    return httpx.Response(self.revoke_status, json={
                        "object": "error", "status": self.revoke_status,
                        "code": "internal_server_error", "message": "oops"})
                self.valid.discard(body["token"])
                return httpx.Response(200, json={"request_id": "req-r"})
            if body["grant_type"] == "authorization_code":
                assert body == {"grant_type": "authorization_code", "code": CODE,
                                "redirect_uri": ORIGIN + "/notion/callback"}
                if self.exchange_status != 200:
                    return httpx.Response(400, json={
                        "error": "invalid_grant", "error_description": "bad code"})
                return httpx.Response(200, json=self.answer)
            assert body == {"grant_type": "refresh_token", "refresh_token": REFRESH}
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={
                    "error": "invalid_grant", "error_description": "revoked"})
            self.valid.add(self.refresh_answer["access_token"])
            return httpx.Response(200, json=self.refresh_answer)
        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        if token not in self.valid:
            if self.on_401 is not None:
                self.on_401()
            return unauthorized()
        if path == "/v1/search":
            body = self.body(request)
            assert body["filter"] == {"property": "object", "value": "data_source"}
            assert body["page_size"] <= 100
            if self.search is None:
                return httpx.Response(500, json={"object": "error", "status": 500,
                                                 "code": "internal_server_error",
                                                 "message": "oops"})
            return httpx.Response(200, json=self.search)
        if path == f"/v1/data_sources/{DS_ID}/query":
            if self.query_status != 200:
                return httpx.Response(self.query_status, json=self.query_body)
            return httpx.Response(200, json={"object": "list", "results": [page()],
                                             "next_cursor": None, "has_more": False})
        if path.startswith("/v1/pages/") and request.method == "PATCH":
            status = self.body(request)["properties"]["Status"]["status"]["name"]
            return httpx.Response(200, json=page(status=status))
        return httpx.Response(404, json={"object": "error", "status": 404,
                                         "code": "object_not_found", "message": "nope"})


@pytest.fixture
def n(fake_client, tmp_path, monkeypatch):
    for var in ("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET", "PUBLIC_ORIGIN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("NOTION_TOKEN", KYLES)  # Kyle's, on the server: never used here
    fake = FakeNotion()
    monkeypatch.setattr(notion_web, "TRANSPORT", httpx.MockTransport(fake))
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["webapp"]["public_origin"] = ORIGIN
    config["web"]["enabled"] = False  # only the tools this piece adds
    fam = family_mod.Family(config, client=fake_client, user_client=lambda key: fake_client)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=ToolRegistry(), clock=Clock(), family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")

    class N:
        pass

    e = N()
    e.__dict__.update(config=config, fam=fam, app=app, fake=fake, tmp=tmp_path,
                      client=fake_client, assistant=assistant)

    def member(name="Tess", tier="beta", language="en"):
        _, invite = fam.invite(name, language, tier=tier)
        c = TestClient(app, base_url=BASE)
        assert c.post("/api/invite/accept", json={"invite": invite}).status_code == 200
        uid = next(u["id"] for u in fam.members() if u["name"] == name)
        return c, uid

    e.member = member
    yield e
    fam.close()


def start(c) -> str:
    r = c.post("/api/notion/connect", json={})
    assert r.status_code == 200, r.text
    assert c.cookies.get(notion_web.BIND_COOKIE)
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


def callback(c, state, code=CODE):
    return c.get("/notion/callback", params={"state": state, "code": code},
                 follow_redirects=False)


def connect(n, c):
    r = callback(c, start(c))
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    return r


def files(n, uid):
    folder = n.tmp / "users" / uid
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


def tools_of(n, uid):
    return n.fam.context(uid).registry.tools


# --- connecting ----------------------------------------------------------------------

def test_callback_saves_encrypted_notion_token_and_adds_tools(n):
    tess, uid = n.member()
    assert tess.get("/api/notion/status").json() == {
        "enabled": True, "connected": False, "workspace": None, "database": None}
    r = tess.post("/api/notion/connect", json={})
    url = urlparse(r.json()["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "api.notion.com", "/v1/oauth/authorize")
    q = parse_qs(url.query)
    assert {k: v[0] for k, v in q.items() if k != "state"} == {
        "client_id": CLIENT_ID, "response_type": "code", "owner": "user",
        "redirect_uri": ORIGIN + "/notion/callback"}
    assert CLIENT_SECRET not in r.text and r.headers["Cache-Control"] == "no-store"
    cookie = r.headers["set-cookie"]
    assert "hc_notion=" in cookie and "Path=/notion/callback" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    before = files(n, uid)
    r = callback(tess, q["state"][0])
    assert r.status_code == 303 and r.headers["location"] == "/"
    # one exchange (the handler asserted Basic auth and the JSON body), one search
    assert len(n.fake.calls("/v1/oauth/token")) == 1
    assert len(n.fake.calls("/v1/search")) == 1
    assert n.fake.calls("/v1/search")[0].headers["Authorization"] == "Bearer " + ACCESS
    assert files(n, uid) - before == {"notion.enc"}
    path = n.fam.notion_file(uid)
    raw = path.read_bytes()
    for plain in (ACCESS, REFRESH, EMAIL, WORKSPACE, DB_TITLE, "us3r-1d"):
        assert plain.encode() not in raw
    import sys
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0
    record = userkeys.unseal(userkeys.NOTION_LABEL, uid, raw, {"USER_DATA_KEY": SECRET})
    assert record == {"uid": uid, "access_token": ACCESS, "refresh_token": REFRESH,
                      "bot_id": "b0t-1d", "workspace_id": "w0rk-1d",
                      "workspace_name": WORKSPACE, "database_id": DB_ID,
                      "data_source_id": DS_ID, "database_title": DB_TITLE}
    # never Notion's owner object (their email)
    assert "owner" not in record and EMAIL not in json.dumps(record)
    # not openable as an Anthropic key file, nor as someone else's
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        userkeys.fernet(userkeys.ANTHROPIC_LABEL, {"USER_DATA_KEY": SECRET}).decrypt(raw)
    assert n.fam.keys.load(uid) is None
    assert userkeys.unseal(userkeys.NOTION_LABEL, "u_someoneelse1", raw,
                           {"USER_DATA_KEY": SECRET}) is None
    assert {"list_assignments", "update_assignment_status"} <= set(tools_of(n, uid))
    assert tess.get("/api/notion/status").json() == {
        "enabled": True, "connected": True, "workspace": WORKSPACE, "database": DB_TITLE}
    # the binding cookie is cleared after the callback
    assert not tess.cookies.get(notion_web.BIND_COOKIE)


def test_beta_registry_gets_list_assignments_only_after_connecting(n):
    tess, uid = n.member(tier="beta")
    registry = n.fam.context(uid).registry  # a conversation already open
    assert not {"list_assignments", "update_assignment_status"} & set(registry.tools)
    connect(n, tess)
    # the same (shared) registry: an open conversation sees them at once
    assert set(registry.tools) == {"list_assignments", "update_assignment_status"}
    # never Kyle's other Notion tools or anything else
    assert "create_assignment" not in registry.tools


def test_a_turn_sees_the_tools_described_for_them_not_kyle(n):
    sam, uid = n.member("Sam", tier="family")  # the owner's Claude key: no BYOK needed
    connect(n, sam)
    n.client.queue.append(make_response(text_block("Hi.")))
    assert sam.post("/api/turn", json={"text": "hi"}).status_code == 200
    call = n.client.calls[-1]
    names = {t["name"] for t in call["tools"]}
    assert names == {"list_assignments", "update_assignment_status"}
    assert "Kyle" not in json.dumps(call["tools"]) and " his " not in json.dumps(call["tools"])
    assert "Sam's" in json.dumps(call["tools"])


@pytest.mark.parametrize("case", ["unknown", "reused", "no_cookie", "other_browser",
                                  "signed_out", "removed", "no_code"])
def test_bad_callbacks_are_refused_and_write_nothing(n, case):
    tess, uid = n.member()
    state = start(tess)
    c = tess
    if case == "unknown":
        state = "made-up"
    elif case == "reused":
        connect_state = state
        assert callback(tess, connect_state).status_code == 303
        n.fam.disconnect_notion(uid)
        n.fake.requests.clear()
        state = connect_state
    elif case == "no_cookie":
        c = TestClient(n.app, base_url=BASE)
    elif case == "other_browser":
        dan, _ = n.member("Dan")
        start(dan)  # Dan has a binding cookie of his own
        c = dan
    elif case == "signed_out":
        assert tess.post("/api/logout").status_code == 200
    elif case == "removed":
        n.fam.remove(uid)
    r = callback(c, state, code="" if case == "no_code" else CODE)
    assert r.status_code == 400 and r.text == i18n.EN["notion_failed"]
    assert n.fake.calls("/v1/oauth/token") == []  # never exchanged
    assert not n.fam.notion_file(uid).exists()
    assert "notion.enc" not in files(n, uid)


def test_a_denied_consent_uses_up_the_state(n):
    tess, uid = n.member(language="zh")
    state = start(tess)
    r = tess.get("/notion/callback", params={"state": state, "error": "access_denied"},
                 follow_redirects=False)
    assert r.status_code == 400 and r.text == i18n.ZH["notion_failed"]
    assert callback(tess, state).status_code == 400
    assert n.fake.calls("/v1/oauth/token") == []


def test_a_refused_code_writes_nothing(n):
    tess, uid = n.member()
    n.fake.exchange_status = 400
    assert callback(tess, start(tess)).status_code == 400
    assert not n.fam.notion_file(uid).exists()
    assert tess.get("/api/notion/status").json()["connected"] is False


def test_a_grant_that_cant_be_saved_is_revoked(n, monkeypatch):
    """Removed between the exchange and the save (or the write failed): the
    fresh grant is revoked at once instead of being left at Notion."""
    tess, uid = n.member()
    state = start(tess)
    monkeypatch.setattr(n.fam, "save_notion", lambda uid, record: False)
    assert callback(tess, state).status_code == 400
    assert [n.fake.body(r) for r in n.fake.calls("/v1/oauth/revoke")] == [{"token": ACCESS}]
    assert not n.fam.notion_file(uid).exists()


def test_without_user_data_key_nothing_is_started_or_stored(n, monkeypatch):
    tess, uid = n.member()
    monkeypatch.delenv("USER_DATA_KEY")
    r = tess.post("/api/notion/connect", json={})
    assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert not tess.cookies.get(notion_web.BIND_COOKIE)
    assert not n.app.state.notion.pending
    # and if it goes away between starting and the callback: no exchange either
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    state = start(tess)
    monkeypatch.setenv("USER_DATA_KEY", "too-short")
    assert callback(tess, state).status_code == 400
    assert n.fake.calls("/v1/oauth/token") == []
    assert not n.fam.notion_file(uid).exists()


def test_off_without_the_integration_or_the_switch(n, monkeypatch):
    tess, _ = n.member()
    monkeypatch.delenv("NOTION_OAUTH_CLIENT_SECRET")
    assert tess.get("/api/notion/status").json()["enabled"] is False
    assert tess.post("/api/notion/connect", json={}).status_code == 503
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    n.config["family"]["notion"] = False
    assert tess.get("/api/notion/status").json()["enabled"] is False
    assert tess.post("/api/notion/connect", json={}).status_code == 503
    assert load_config(None, None)["family"]["notion"] is True  # the default


def test_the_owner_gets_404_everywhere(n):
    kyle = TestClient(n.app, base_url=BASE)
    login(kyle)
    assert kyle.get("/api/notion/status").status_code == 404
    assert kyle.post("/api/notion/connect", json={}).status_code == 404
    assert kyle.post("/api/notion/disconnect", json={}).status_code == 404
    stranger = TestClient(n.app, base_url=BASE)
    assert stranger.get("/api/notion/status").status_code == 401
    assert stranger.post("/api/notion/connect", json={}).status_code == 401


def test_pending_sign_ins_are_bounded(n):
    tess, uid = n.member()
    for _ in range(notion_web.MAX_PENDING + 3):
        start(tess)
    pending = n.app.state.notion.pending
    assert sum(1 for p in pending.values() if p.user == uid) == notion_web.MAX_PENDING
    connect_ = n.app.state.notion
    for i in range(notion_web.MAX_PENDING_TOTAL + 5):
        connect_.start(f"u_other{i:05d}x", None)
    assert len(pending) == notion_web.MAX_PENDING_TOTAL


# --- finding the database ------------------------------------------------------------

@pytest.mark.parametrize("results", [
    [],                                                        # nothing shared
    [data_source(), data_source(ds_id="ds-2", db_id="db-2")],  # two: no picker
    [data_source(Status={"id": "st", "type": "select", "select": {"options": []}})],
    [data_source(Status={"id": "st", "type": "status", "status": {
        "options": [{"name": "To do"}, {"name": "Done"}]}})],  # other options
    [data_source(Due=None)],                                   # no Due date
])
def test_no_single_assignments_database_means_database_null(n, results):
    n.fake.search["results"] = [r for r in results]
    for r in n.fake.search["results"]:
        r["properties"] = {k: v for k, v in r["properties"].items() if v is not None}
    tess, uid = n.member()
    connect(n, tess)
    assert tess.get("/api/notion/status").json() == {
        "enabled": True, "connected": True, "workspace": WORKSPACE, "database": None}
    assert "list_assignments" not in tools_of(n, uid)


def test_the_copy_of_the_template_wins_over_another_match(n):
    n.fake.answer = token_answer(duplicated_template_id=TEMPLATE)
    n.fake.search["results"] = [data_source(ds_id="ds-old", db_id="db-old", title="Old"),
                                data_source(page=TEMPLATE.replace("-", ""))]
    tess, uid = n.member()
    connect(n, tess)
    assert tess.get("/api/notion/status").json()["database"] == DB_TITLE
    assert n.fam._notion_record(uid)["data_source_id"] == DS_ID


def test_a_failed_search_still_connects(n):
    n.fake.search = None  # Notion answers 500
    tess, uid = n.member()
    connect(n, tess)
    assert tess.get("/api/notion/status").json()["database"] is None


# --- the tools -----------------------------------------------------------------------

def test_both_tools_are_untrusted_and_private_and_update_asks(n):
    tess, uid = n.member()
    connect(n, tess)
    tools = tools_of(n, uid)
    for name in ("list_assignments", "update_assignment_status"):
        assert tools[name].untrusted_output is True and tools[name].private is True
    assert tools["update_assignment_status"].confirm_if_untrusted is not None
    assert n.fam.context(uid).registry.untrusted_names() == {
        "list_assignments", "update_assignment_status"}
    q = tools["update_assignment_status"].confirm_if_untrusted({"name": "Essay",
                                                                "status": "Done"})
    assert "Essay" in q and "Done" in q


def test_never_kyles_notion_token(n):
    import os

    assert os.environ["NOTION_TOKEN"] == KYLES  # set, as on Kyle's machines
    tess, uid = n.member()
    connect(n, tess)
    registry = n.fam.context(uid).registry
    registry.confirm = lambda q: True
    text, is_error = registry.dispatch("list_assignments", {})
    assert not is_error and TASK in text
    text, is_error = registry.dispatch("update_assignment_status",
                                       {"assignment_id": "pa9e-1", "status": "Done"},
                                       untrusted=True)
    assert not is_error and '"updated"' in text
    sent = [r for r in n.fake.requests if r.url.path not in ("/v1/oauth/token",
                                                             "/v1/oauth/revoke")]
    assert len(sent) >= 3
    for r in n.fake.requests:
        assert KYLES not in r.headers.get("Authorization", "")
        assert KYLES.encode() not in (r.content or b"")
    assert all(r.headers["Authorization"] == "Bearer " + ACCESS for r in sent)


def test_a_401_refreshes_once_and_keeps_the_refresh_token(n):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.valid = set()  # Notion stops taking the first access token
    registry = n.fam.context(uid).registry
    text, is_error = registry.dispatch("list_assignments", {})
    assert not is_error and TASK in text
    assert len([r for r in n.fake.calls("/v1/oauth/token")
                if n.fake.body(r)["grant_type"] == "refresh_token"]) == 1
    record = n.fam._notion_record(uid)
    # Notion sent no new refresh token: the old one is kept
    assert record["access_token"] == ACCESS_2 and record["refresh_token"] == REFRESH
    assert n.fake.calls(f"/v1/data_sources/{DS_ID}/query")[-1].headers[
        "Authorization"] == "Bearer " + ACCESS_2


def test_a_rotated_refresh_token_is_stored(n):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.valid = set()
    n.fake.refresh_answer = {**n.fake.refresh_answer, "refresh_token": "nrt_" + "N" * 40}
    n.fam.context(uid).registry.dispatch("list_assignments", {})
    assert n.fam._notion_record(uid)["refresh_token"] == "nrt_" + "N" * 40


def test_a_failed_refresh_says_reconnect_notion(n):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.valid = set()
    n.fake.refresh_status = 400
    text, is_error = n.fam.context(uid).registry.dispatch("list_assignments", {})
    assert is_error and "Reconnect Notion" in text
    assert len(n.fake.calls("/v1/oauth/token")) == 2  # the exchange + one refresh
    assert n.fam._notion_record(uid)["access_token"] == ACCESS  # nothing overwritten


def test_two_turns_refresh_only_once(n):
    tess, uid = n.member()
    connect(n, tess)
    registry = n.fam.context(uid).registry
    n.fake.valid = set()
    both = threading.Barrier(2, timeout=5)
    seen = []

    def meet():  # the first two 401s wait for each other: both turns got one
        seen.append(1)
        if len(seen) <= 2:
            both.wait()

    n.fake.on_401 = meet
    results = []

    def turn():
        results.append(registry.dispatch("list_assignments", {}))

    threads = [threading.Thread(target=turn) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    n.fake.on_401 = None
    assert [e for _, e in results] == [False, False] and len(seen) == 2
    refreshes = [r for r in n.fake.calls("/v1/oauth/token")
                 if n.fake.body(r)["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1


def test_notions_own_error_text_never_reaches_claude_or_the_log(n, caplog):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.query_status = 400
    n.fake.query_body = {"object": "error", "status": 400, "code": "validation_error",
                         "message": f"body.filter.title.contains should be ... `{TASK}`"}
    with caplog.at_level(logging.DEBUG):
        text, is_error = n.fam.context(uid).registry.dispatch("list_assignments", {})
    assert is_error and "validation_error" in text and TASK not in text
    assert TASK not in caplog.text


# --- disconnect, remove, delete ------------------------------------------------------

def test_disconnect_revokes_and_deletes(n):
    tess, uid = n.member()
    connect(n, tess)
    r = tess.post("/api/notion/disconnect", json={})
    assert r.status_code == 200 and r.json() == {"ok": True, "connected": False}
    revokes = n.fake.calls("/v1/oauth/revoke")
    assert [n.fake.body(r) for r in revokes] == [{"token": ACCESS}]
    assert ACCESS not in str(revokes[0].url)
    assert not n.fam.notion_file(uid).exists()
    assert "list_assignments" not in tools_of(n, uid)
    assert tess.get("/api/notion/status").json()["connected"] is False


def test_a_failed_revoke_still_deletes(n):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.revoke_status = 500
    assert tess.post("/api/notion/disconnect", json={}).status_code == 200
    assert len(n.fake.calls("/v1/oauth/revoke")) == 1  # tried once; not retried for ever
    assert not n.fam.notion_file(uid).exists()


def test_owner_removal_revokes_and_deletes_notion(n):
    tess, uid = n.member()
    dan, dan_id = n.member("Dan")
    connect(n, tess)
    connect(n, dan)
    n.fake.revoke_status = 500  # a failure never blocks the removal
    kyle = TestClient(n.app, base_url=BASE)
    login(kyle)
    assert kyle.post(f"/api/users/{uid}/remove", json={}).status_code == 200
    assert len(n.fake.calls("/v1/oauth/revoke")) == 1
    assert not n.fam.notion_file(uid).exists() and not (n.tmp / "users" / uid).exists()
    assert n.fam.notion_file(dan_id).exists()  # Dan's stays
    assert uid not in n.fam._notion_apis and uid not in n.fam._notion_locks


def test_family_remove_deletes_notion_enc(n):
    tess, uid = n.member()
    connect(n, tess)
    assert n.fam.notion_file(uid).exists()
    assert n.fam.remove(uid)
    assert not n.fam.notion_file(uid).exists()
    assert n.fam.context(uid) is None


def test_deleting_my_account_revokes_notion_first(n):
    tess, uid = n.member()
    connect(n, tess)
    seen = []
    original = n.fake.__call__

    def spy(request):
        if request.url.path == "/v1/oauth/revoke":
            seen.append(n.fam.notion_file(uid).exists())
        return original(request)

    notion_web.TRANSPORT = httpx.MockTransport(spy)  # the fixture's monkeypatch restores it
    r = tess.post("/api/me/delete", json={"confirm": "DELETE"})
    assert r.status_code == 200
    assert seen == [True]  # revoked while the token was still there
    assert not (n.tmp / "users" / uid).exists()


def test_deleting_my_account_goes_ahead_when_revoke_fails(n):
    tess, uid = n.member()
    connect(n, tess)
    n.fake.revoke_status = 500
    assert tess.post("/api/me/delete", json={"confirm": "DELETE"}).status_code == 200
    assert len(n.fake.calls("/v1/oauth/revoke")) == 1
    assert not (n.tmp / "users" / uid).exists()


# --- logs and config -----------------------------------------------------------------

def test_no_token_code_email_or_title_in_the_log(n, caplog):
    caplog.set_level(logging.DEBUG)
    tess, uid = n.member()
    state = start(tess)
    nonce = tess.cookies.get(notion_web.BIND_COOKIE)
    assert callback(tess, state).status_code == 303
    registry = n.fam.context(uid).registry
    registry.confirm = lambda q: True
    n.fake.valid = set()  # and a refresh
    registry.dispatch("list_assignments", {})
    registry.dispatch("update_assignment_status", {"name": TASK, "status": "Done"},
                      untrusted=True)
    tess.post("/api/notion/disconnect", json={})
    # (the test's own HTTP client logs the URLs it calls: "httpx*", WARNING
    # and up only in the app, config.setup_logging)
    ours = [r for r in caplog.records if not r.name.startswith("httpx")]
    assert ours
    text = " ".join(r.getMessage() + str(r.args) + str(r.exc_text or "") for r in ours)
    for secret in (ACCESS, ACCESS_2, REFRESH, CODE, state, nonce, CLIENT_SECRET, EMAIL,
                   WORKSPACE, DB_TITLE, TASK, KYLES):
        assert secret not in text, secret
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
            and r.name.startswith("assistant")]
    assert any(uid in m and "w0rk-1d" in m for m in info)  # ids are logged
    # uvicorn's access log line for the callback: the code is masked
    from assistant.config import redact

    assert CODE not in redact(f'"GET /notion/callback?state={state}&code={CODE} HTTP/1.1"')


def test_config_docs_and_deploy_know_the_new_settings():
    from pathlib import Path

    from assistant import cloud

    root = Path(__file__).resolve().parent.parent
    for var in ("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET"):
        assert var in SECRET_ENV_VARS and var in ISOLATED_ENV_VARS
        assert f"key: {var}\n        sync: false" in (root / "render.yaml").read_text(
            encoding="utf-8")
    assert {"/notion/callback", "/notion.js"} <= cloud.PUBLIC_GET
    web = root / "assistant" / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    assert 'id="notion-connect"' in page and 'id="notion-disconnect"' in page
    assert page.index('id="google-connect"') < page.index('id="notion-connect"')
    assert page.index("/notion.js") < page.index("/app.js")
    assert '"/notion.js"' in (web / "sw.js").read_text(encoding="utf-8")
    assert "HeyClaudeNotion.onState" in (web / "app.js").read_text(encoding="utf-8")
    for table in (i18n.EN, i18n.ZH):
        assert table["notion_failed"] and table["notion_reconnect"]


def test_the_page_code_is_served(n):
    c = TestClient(n.app, base_url=BASE)
    r = c.get("/notion.js")
    assert r.status_code == 200 and "HeyClaudeNotion" in r.text


def test_seal_and_unseal_bind_the_id(tmp_path):
    env = {"USER_DATA_KEY": SECRET}
    text = userkeys.seal(userkeys.NOTION_LABEL, "u_abcdefgh1", {"x": 1}, env)
    assert userkeys.unseal(userkeys.NOTION_LABEL, "u_abcdefgh1", text, env) == {
        "x": 1, "uid": "u_abcdefgh1"}
    assert userkeys.unseal(userkeys.NOTION_LABEL, "u_abcdefgh2", text, env) is None
    assert userkeys.unseal(userkeys.ANTHROPIC_LABEL, "u_abcdefgh1", text, env) is None
    assert userkeys.unseal(userkeys.NOTION_LABEL, "u_abcdefgh1", text, {}) is None
    with pytest.raises(userkeys.KeyProblem) as e:
        userkeys.seal(userkeys.NOTION_LABEL, "u_abcdefgh1", {"x": 1}, {})
    assert e.value.status == 503


def test_tool_use_round_trip_in_a_turn_is_confirmed_when_tainted(n):
    """list_assignments taints the turn, so the status change asks first;
    a "no" changes nothing."""
    sam, uid = n.member("Sam", tier="family")
    connect(n, sam)
    n.client.queue += [
        make_response(tool_use_block("list_assignments", {}, "t1"), stop_reason="tool_use"),
        make_response(tool_use_block("update_assignment_status",
                                     {"assignment_id": "pa9e-1", "status": "Done"}, "t2"),
                      stop_reason="tool_use"),
        make_response(text_block("Okay.")),
    ]
    first = events(sam.post("/api/turn", json={"text": "mark my essay done"}))
    asked = [e for e in first if e["type"] == "confirm"]
    assert len(asked) == 1 and "Done" in asked[0]["question"]  # asked, not run
    assert not [q for q in n.fake.requests if q.method == "PATCH"]
    events(sam.post("/api/confirm", json={"answer": "no"}))
    assert not [q for q in n.fake.requests if q.method == "PATCH"]
    result = n.client.calls[-1]["messages"][-1]["content"][0]["content"]
    assert "cancelled" in result
