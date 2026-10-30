"""The owner's Connected services (kwil/owner_connect.py): his own Notion
through the public integration, and his Home Assistant through its built-in
OAuth, signed in from the phone app instead of pasted into Render's env.

All offline: Notion through notion_client's real SDK over
notion_web.TRANSPORT (tests/test_notion_web.FakeNotion, Notion's real
shapes); Home Assistant's /auth/token and /auth/revoke over
owner_connect.TRANSPORT, answering in the shapes of HA's auth API docs
(developers.home-assistant.io/docs/auth_api); names resolve through a fake
canvas.RESOLVE. NOTION_TOKEN, HA_TOKEN and HA_URL are cleared unless a test
sets them."""

import base64
import hashlib
import json
import logging
import socket
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
pytest.importorskip("notion_client")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import canvas, cli, i18n, notion_web, owner_connect, userkeys, webapp  # noqa: E402
from kwil import family as family_mod  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import DEFAULTS, load_config, redact  # noqa: E402
from kwil.tools import ToolRegistry  # noqa: E402
from kwil.tools.home_assistant import HAError  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.test_notion_web import (ACCESS, ACCESS_2, CLIENT_ID, CLIENT_SECRET, CODE,  # noqa: E402
                                   DB_ID, DS_ID, KYLES, ORIGIN, REFRESH, SECRET,
                                   WORKSPACE, FakeNotion, unauthorized)
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

HA_HOST = "home.example.com"
HA = "https://" + HA_HOST
HA_CODE = "7c8f2a3b9d4e4f1a8b6c5d2e1f0a9b8c"
HA_ACCESS = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI0ZjFhOGI2YzVkMmUifQ."
             "Zm9vYmFyYmF6cXV4c2lnbmF0dXJl")
HA_ACCESS_2 = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI5YTlhOWE5YTlhOWEifQ."
               "c2Vjb25kc2lnbmF0dXJlZm9yeW91")
HA_REFRESH = "3f" * 64          # secrets.token_hex(64), as HA makes them
ADDRESSES = {HA_HOST: "93.184.216.34", "lan.example.com": "192.168.1.20",
             "meta.example.com": "169.254.169.254", "ten.example.com": "10.0.0.5"}


def fake_resolve(host, port, *args, **kwargs):
    if host in ADDRESSES:
        address = ADDRESSES[host]
    else:
        try:
            socket.inet_aton(host)
            address = host
        except OSError:
            raise socket.gaierror("unknown host") from None
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


class OwnerNotion(FakeNotion):
    """FakeNotion plus GET /v1/databases/<id> (the owner's [notion]
    database_id resolves to its data source) and the Course options."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v1/databases/{DB_ID}":
            self.requests.append(request)
            auth = request.headers.get("Authorization", "")
            if auth.removeprefix("Bearer ") not in self.valid:
                if self.on_401 is not None:
                    self.on_401()
                return unauthorized()
            return httpx.Response(200, json={
                "object": "database", "id": DB_ID,
                "data_sources": [{"id": DS_ID, "name": "Assignments"}],
                "parent": {"type": "page_id", "page_id": "p4ge-0"}})
        return super().__call__(request)


class FakeHAAuth:
    """Home Assistant's /auth/token and /auth/revoke (form bodies)."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.challenge = None          # from the authorize URL
        self.token_status = 200
        self.refresh_status = 200
        self.refreshes = 0

    def form(self, request):
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    def calls(self, path):
        return [r for r in self.requests if r.url.path == path]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == HA_HOST and request.method == "POST"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert "Authorization" not in request.headers
        form = self.form(request)
        if request.url.path == "/auth/revoke":
            assert set(form) == {"token"}
            return httpx.Response(200, content=b"")
        assert request.url.path == "/auth/token"
        assert form["client_id"] == ORIGIN + "/"
        if form["grant_type"] == "authorization_code":
            assert set(form) == {"grant_type", "code", "client_id", "code_verifier"}
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            if form["code"] != HA_CODE or self.token_status != 200 or \
                    base64.urlsafe_b64encode(digest).decode().rstrip("=") != self.challenge:
                return httpx.Response(400, json={"error": "invalid_request",
                                                 "error_description": "Invalid code"})
            return httpx.Response(200, json={"access_token": HA_ACCESS, "expires_in": 1800,
                                             "refresh_token": HA_REFRESH,
                                             "token_type": "Bearer"})
        assert form == {"grant_type": "refresh_token", "refresh_token": HA_REFRESH,
                        "client_id": ORIGIN + "/"}
        if self.refresh_status != 200:
            return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
        self.refreshes += 1
        return httpx.Response(200, json={"access_token": HA_ACCESS_2, "expires_in": 1800,
                                         "token_type": "Bearer"})


class HAStates:
    """requests.Session stand-in for HA's REST API: accepts `valid` tokens."""

    def __init__(self, valid=(HA_ACCESS,)):
        self.valid = set(valid)
        self.seen = []

    def request(self, method, url, headers=None, json=None, timeout=None,
                allow_redirects=True):
        from tests.test_home_assistant import STATES, Resp

        assert url.startswith(HA + "/api/") and allow_redirects is False
        token = headers["Authorization"].removeprefix("Bearer ")
        self.seen.append(token)
        if token not in self.valid:
            return Resp(401, {"message": "Invalid access token"})
        if url.endswith("/api/states"):
            return Resp(200, STATES)
        return Resp(200, text="")


@pytest.fixture
def o(fake_client, tmp_path, monkeypatch, request):
    """An owner's web app; `family` (indirect param) turns on invited people
    (then /notion/callback is notion_web's route, handing the owner's
    states over); default on."""
    family_on = getattr(request, "param", True)
    for var in ("NOTION_TOKEN", "HA_TOKEN", "HA_URL", "PUBLIC_ORIGIN",
                "NOTION_ASSIGNMENTS_DB", "NOTION_OAUTH_CLIENT_ID",
                "NOTION_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    fake = OwnerNotion()
    monkeypatch.setattr(notion_web, "TRANSPORT", httpx.MockTransport(fake))
    ha_fake = FakeHAAuth()
    monkeypatch.setattr(owner_connect, "TRANSPORT", httpx.MockTransport(ha_fake))
    monkeypatch.setattr(canvas, "RESOLVE", fake_resolve)
    config = load_config(None, None)
    config["family"].update(enabled=family_on, dir=str(tmp_path / "users"))
    config["connect"]["dir"] = str(tmp_path / "owner")
    config["webapp"]["public_origin"] = ORIGIN
    config["notion"]["database_id"] = DB_ID
    config["web"]["enabled"] = False
    config["student"]["enabled"] = False
    fam = family_mod.Family(config, client=fake_client,
                            user_client=lambda key: fake_client) if family_on else None
    tools = ToolRegistry()
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=tools, clock=Clock(), family=fam)
    assistant.connectors = owner_connect.from_config(config, tools)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")

    class O:
        pass

    e = O()
    e.__dict__.update(config=config, fam=fam, app=app, fake=fake, ha=ha_fake,
                      tmp=tmp_path, tools=tools, assistant=assistant,
                      connect=assistant.connectors)

    def kyle():
        c = TestClient(app, base_url=BASE)
        login(c)
        return c

    e.kyle = kyle
    yield e
    if fam is not None:
        fam.close()


def start(c, svc="notion", **body):
    r = c.post(f"/api/connect/{svc}/start", json=body)
    assert r.status_code == 200, r.text
    assert c.cookies.get(owner_connect.BIND_COOKIE)
    return r


def state_of(r) -> str:
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


def callback(c, state, code=CODE, path="/notion/callback"):
    return c.get(path, params={"state": state, "code": code}, follow_redirects=False)


def connect_notion(o, c):
    r = callback(c, state_of(start(c)))
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    return r


def ha_start(o, c, url=HA_HOST):
    r = start(c, "home_assistant", url=url)
    q = parse_qs(urlparse(r.json()["url"]).query)
    o.ha.challenge = q["code_challenge"][0]
    return r


def connect_ha(o, c):
    r = callback(c, state_of(ha_start(o, c)), HA_CODE, "/ha/callback")
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    return r


def status(c, svc):
    r = c.get("/api/connect/status")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    return next(s for s in r.json()["services"] if s["id"] == svc)


def sealed(o, svc):
    raw = (o.tmp / "owner" / owner_connect.FILES[svc]).read_bytes()
    return raw, userkeys.unseal(owner_connect.LABELS[svc], "owner", raw,
                                {"USER_DATA_KEY": SECRET})


# --- Notion ---------------------------------------------------------------------------

@pytest.mark.parametrize("o", [True, False], indirect=True, ids=["family", "no-family"])
def test_connecting_notion_registers_the_tools_and_lecture_notes_find_a_token(o):
    """Failing first: no NOTION_TOKEN on the server, the owner signs in from
    the app, and his Assignments tools, lecture notes and token appear."""
    from kwil import webfeatures

    features = webfeatures.Features(o.config, o.assistant)
    assert features.assignments is None  # nothing set yet
    assert owner_connect.owner_notion_token(o.config) is None
    kyle = o.kyle()
    assert status(kyle, "notion") == {"id": "notion", "name": "Notion", "managed": "none",
                                      "problem": None, "needs_url": False, "detail": None}
    r = start(kyle)
    url = urlparse(r.json()["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "api.notion.com",
                                                  "/v1/oauth/authorize")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q.pop("state") and q == {"client_id": CLIENT_ID, "response_type": "code",
                                    "owner": "user",
                                    "redirect_uri": ORIGIN + "/notion/callback"}
    cookie = r.headers["set-cookie"]
    assert "hc_connect=" in cookie and "Path=/notion/callback" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    assert CLIENT_SECRET not in r.text and r.headers["Cache-Control"] == "no-store"
    r = callback(kyle, state_of(r))
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert not kyle.cookies.get(owner_connect.BIND_COOKIE)  # cleared
    assert len(o.fake.calls("/v1/oauth/token")) == 1
    # sealed, private, the owner's id inside, never Notion's owner object
    raw, record = sealed(o, "notion")
    for plain in (ACCESS, REFRESH, WORKSPACE):
        assert plain.encode() not in raw
    assert record == {"uid": "owner", "access_token": ACCESS, "refresh_token": REFRESH,
                      "bot_id": "b0t-1d", "workspace_id": "w0rk-1d",
                      "workspace_name": WORKSPACE}
    assert userkeys.unseal(userkeys.NOTION_LABEL, "owner", raw,
                           {"USER_DATA_KEY": SECRET}) is None  # not a member's label
    import sys
    if sys.platform != "win32":
        assert (o.tmp / "owner" / "notion.enc").stat().st_mode & 0o077 == 0
    # the tools, in the registry every owner browser shares
    assert {"list_assignments", "update_assignment_status"} <= set(o.tools.tools)
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, is_error = reg.dispatch("list_assignments", {})
    assert not is_error, content
    assert json.loads(content)["count"] == 1
    query = o.fake.calls(f"/v1/data_sources/{DS_ID}/query")[-1]
    assert query.headers["Authorization"] == "Bearer " + ACCESS
    # lecture notes find his Notion now (the same Features object, built before)
    assert owner_connect.owner_notion_token(o.config) == ACCESS
    assert features.assignments is not None
    assert features.assignments.data_source_id == DS_ID
    assert o.fake.calls(f"/v1/databases/{DB_ID}")[-1].headers["Authorization"] == \
        "Bearer " + ACCESS
    s = status(kyle, "notion")
    assert s["managed"] == "app" and s["detail"] == WORKSPACE and s["problem"] is None


def test_the_tools_keep_their_flags(o):
    """Taint unchanged: the same tools (and confirmations) as with NOTION_TOKEN."""
    connect_notion(o, o.kyle())
    update = o.tools.tools["update_assignment_status"]
    assert update.confirm_if_untrusted is not None
    from kwil.tools.notion import NotionAssignments

    env_made = {t.name: t for t in NotionAssignments(DB_ID, client=object()).tools()}
    for name, tool in env_made.items():
        ours = o.tools.tools[name]
        assert (ours.untrusted_output, ours.private, ours.confirm_prompt is None) == \
            (tool.untrusted_output, tool.private, tool.confirm_prompt is None)


def test_digests_and_the_dev_team_pick_up_the_sign_in_lazily(o, monkeypatch):
    from kwil import devteam

    link = devteam.link_from_config(o.config, o.tmp / "devteam.json")
    digests = owner_connect.owner_assignments(o.config)
    assert link.client is not None and digests is not None  # built before connecting
    with pytest.raises(Exception, match="isn't connected"):
        link.client.search(query="x")
    connect_notion(o, o.kyle())
    link.client.search(filter={"property": "object", "value": "data_source"}, page_size=10)
    assert o.fake.calls("/v1/search")[-1].headers["Authorization"] == "Bearer " + ACCESS
    assert digests.parent_page_id() == "p4ge-0"
    o.kyle().post("/api/connect/notion/disconnect", headers={"Content-Length": "0"})
    with pytest.raises(Exception, match="isn't connected"):
        link.client.search(query="x")


def test_disconnect_revokes_deletes_and_drops_the_tools(o):
    from kwil import webfeatures

    kyle = o.kyle()
    connect_notion(o, kyle)
    features = webfeatures.Features(o.config, o.assistant)
    assert features.assignments is not None
    r = kyle.post("/api/connect/notion/disconnect", headers={"Content-Length": "0"})
    assert r.status_code == 200 and r.json() == {"ok": True, "managed": "none"}
    assert [o.fake.body(x) for x in o.fake.calls("/v1/oauth/revoke")] == [{"token": ACCESS}]
    assert not (o.tmp / "owner" / "notion.enc").exists()
    assert not {"list_assignments", "update_assignment_status"} & set(o.tools.tools)
    assert owner_connect.owner_notion_token(o.config) is None
    assert features.assignments is None
    assert status(kyle, "notion")["managed"] == "none"


def test_a_failed_revoke_still_deletes(o):
    kyle = o.kyle()
    connect_notion(o, kyle)
    o.fake.revoke_status = 500
    r = kyle.post("/api/connect/notion/disconnect", headers={"Content-Length": "0"})
    assert r.status_code == 200
    assert not (o.tmp / "owner" / "notion.enc").exists()


def test_a_401_refreshes_the_owners_token_once_and_writes_it_back_sealed(o):
    connect_notion(o, o.kyle())
    o.fake.valid = set()  # ACCESS stopped working; the refresh makes ACCESS_2 valid
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, is_error = reg.dispatch("list_assignments", {})
    assert not is_error, content
    assert len(o.fake.calls("/v1/oauth/token")) == 2  # the exchange, one refresh
    _, record = sealed(o, "notion")
    assert record["access_token"] == ACCESS_2 and record["refresh_token"] == REFRESH
    reg.dispatch("list_assignments", {})
    assert len(o.fake.calls("/v1/oauth/token")) == 2  # no second refresh


def test_a_failed_refresh_says_connect_again(o):
    connect_notion(o, o.kyle())
    o.fake.valid = set()
    o.fake.refresh_status = 400
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, is_error = reg.dispatch("list_assignments", {})
    assert is_error, content
    assert len(o.fake.calls("/v1/oauth/token")) == 2  # one refresh tried
    _, record = sealed(o, "notion")
    assert record["access_token"] == ACCESS  # nothing written


def test_a_member_gets_404_and_a_stranger_401(o):
    _, invite = o.fam.invite("Tess", "en", tier="beta")
    tess = TestClient(o.app, base_url=BASE)
    assert tess.post("/api/invite/accept", json={"invite": invite}).status_code == 200
    for method, path in (("GET", "/api/connect/status"),
                         ("POST", "/api/connect/notion/start"),
                         ("POST", "/api/connect/home_assistant/start"),
                         ("POST", "/api/connect/notion/disconnect")):
        assert tess.request(method, path, json={}).status_code == 404, path
        stranger = TestClient(o.app, base_url=BASE)
        assert stranger.request(method, path, json={}).status_code == 401, path
    assert not o.connect.pending


def test_env_notion_token_wins_and_connect_is_refused(o, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", KYLES)
    kyle = o.kyle()
    assert status(kyle, "notion")["managed"] == "env"
    r = kyle.post("/api/connect/notion/start", json={})
    assert r.status_code == 409 and "NOTION_TOKEN" in r.json()["error"]
    assert kyle.post("/api/connect/notion/disconnect",
                     headers={"Content-Length": "0"}).status_code == 409
    assert not o.connect.pending and not (o.tmp / "owner").exists()
    assert owner_connect.owner_notion_token(o.config) == KYLES
    # every caller gets exactly the client it made before
    client = owner_connect.owner_notion_client(o.config)
    assert client.options.auth == KYLES
    assert owner_connect.owner_assignments(o.config).token is None  # env fallback, as before


@pytest.mark.parametrize("case", ["unknown", "reused", "no_cookie", "other_browser",
                                  "signed_out", "other_service", "no_code"])
def test_state_is_single_use_and_bound_to_the_cookie(o, case):
    kyle = o.kyle()
    r = start(kyle)
    state, path, c = state_of(r), "/notion/callback", kyle
    if case == "unknown":
        state = "made-up"
    elif case == "reused":
        assert callback(kyle, state).status_code == 303
        (o.tmp / "owner" / "notion.enc").unlink()  # (to see that nothing is written again)
        o.tools.tools.clear()
    elif case == "no_cookie":
        c = TestClient(o.app, base_url=BASE)
    elif case == "other_browser":
        c = o.kyle()
        start(c)  # its own cookie, for another state
    elif case == "signed_out":
        assert kyle.post("/api/logout", headers={"Content-Length": "0"}).status_code == 200
    elif case == "other_service":
        path = "/ha/callback"
    r = callback(c, state, "" if case == "no_code" else CODE, path)
    assert r.status_code == 400
    assert not (o.tmp / "owner" / "notion.enc").exists()
    assert "list_assignments" not in o.tools.tools
    # and it can't be used again either
    assert callback(kyle, state).status_code == 400


def test_a_denied_consent_uses_up_the_state(o):
    kyle = o.kyle()
    state = state_of(start(kyle))
    r = kyle.get("/notion/callback", params={"state": state, "error": "access_denied"},
                 follow_redirects=False)
    assert r.status_code == 400 and r.text == i18n.EN["connect_failed"]
    assert callback(kyle, state).status_code == 400
    assert o.fake.calls("/v1/oauth/token") == []


def test_a_grant_that_cant_be_stored_is_revoked(o, monkeypatch):
    kyle = o.kyle()
    state = state_of(start(kyle))

    def refuse(self, svc, record):
        raise OSError("disk full")
    monkeypatch.setattr(owner_connect.Vault, "save", refuse)
    assert callback(kyle, state).status_code == 400
    assert [o.fake.body(x) for x in o.fake.calls("/v1/oauth/revoke")] == [{"token": ACCESS}]
    assert "list_assignments" not in o.tools.tools


def test_without_user_data_key_nothing_is_started_or_stored(o, monkeypatch):
    kyle = o.kyle()
    monkeypatch.delenv("USER_DATA_KEY")
    assert status(kyle, "notion")["problem"] == "key"
    for svc, body in (("notion", {}), ("home_assistant", {"url": HA_HOST})):
        r = kyle.post(f"/api/connect/{svc}/start", json=body)
        assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert not kyle.cookies.get(owner_connect.BIND_COOKIE) and not o.connect.pending
    # and if it goes away between starting and the callback: no exchange
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    state = state_of(start(kyle))
    monkeypatch.setenv("USER_DATA_KEY", "too-short")
    assert callback(kyle, state).status_code == 400
    assert o.fake.calls("/v1/oauth/token") == []
    assert not (o.tmp / "owner").exists()


def test_not_set_up_without_the_integration_or_an_https_origin(o, monkeypatch):
    kyle = o.kyle()
    monkeypatch.delenv("NOTION_OAUTH_CLIENT_SECRET")
    assert status(kyle, "notion")["problem"] == "setup"
    assert kyle.post("/api/connect/notion/start", json={}).status_code == 503
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    o.config["webapp"]["public_origin"] = "http://127.0.0.1:8000"
    assert status(kyle, "home_assistant")["problem"] == "setup"
    assert kyle.post("/api/connect/home_assistant/start",
                     json={"url": HA_HOST}).status_code == 503
    o.config["connect"]["notion"] = False
    names = [s["id"] for s in kyle.get("/api/connect/status").json()["services"]]
    assert names == ["home_assistant"]
    assert kyle.post("/api/connect/notion/start", json={}).status_code == 404


def test_pending_sign_ins_and_bodies_are_bounded(o):
    kyle = o.kyle()
    for _ in range(owner_connect.MAX_PENDING + 3):
        start(kyle)
    assert len(o.connect.pending) == owner_connect.MAX_PENDING
    r = kyle.post("/api/connect/home_assistant/start",
                  content=b'{"url": "' + b"a" * 3000 + b'"}',
                  headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_unknown_service_is_404(o):
    kyle = o.kyle()
    assert kyle.post("/api/connect/slack/start", json={}).status_code == 404
    assert kyle.post("/api/connect/slack/disconnect", json={}).status_code == 404


# --- Home Assistant -------------------------------------------------------------------

def test_ha_connect_with_a_public_https_address_registers_the_tools(o):
    kyle = o.kyle()
    s = status(kyle, "home_assistant")
    assert s["managed"] == "none" and s["needs_url"] is True and s["problem"] is None
    r = ha_start(o, kyle, "  home.example.com/  ")
    url = urlparse(r.json()["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", HA_HOST, "/auth/authorize")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q.pop("state") and q.pop("code_challenge")
    assert q == {"client_id": ORIGIN + "/", "redirect_uri": ORIGIN + "/ha/callback",
                 "code_challenge_method": "S256"}
    assert "Path=/ha/callback" in r.headers["set-cookie"]
    r = callback(kyle, state_of(r), HA_CODE, "/ha/callback")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert len(o.ha.calls("/auth/token")) == 1
    raw, record = sealed(o, "home_assistant")
    assert HA_REFRESH.encode() not in raw and HA_HOST.encode() not in raw
    assert HA_ACCESS.encode() not in raw
    assert record == {"uid": "owner", "url": HA, "refresh_token": HA_REFRESH}
    assert {"ha_list", "ha_state", "ha_control"} <= set(o.tools.tools)
    # the tools talk to that address with the access token kept in memory
    ha = o.tools.tools["ha_list"].handler.__self__
    ha._session = HAStates()
    assert ha.url == HA and ha.token == ""
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, is_error = reg.dispatch("ha_list", {})
    assert not is_error, content
    assert ha._session.seen[0] == HA_ACCESS
    s = status(kyle, "home_assistant")
    assert s["managed"] == "app" and s["detail"] == HA_HOST


@pytest.mark.parametrize("url", [
    "http://home.example.com",           # plain http
    "https://192.168.1.10",              # the LAN
    "https://lan.example.com",           # a name for the LAN
    "https://ten.example.com",
    "https://169.254.169.254",           # cloud metadata
    "https://meta.example.com",
    "https://127.0.0.1", "https://localhost",
    "https://homeassistant.local",       # doesn't resolve here
    "https://user:pw@home.example.com",  # credentials in the URL
    "https://home.example.com:8123",     # another port
    "https://home.example.com/lovelace/0",
    "", "x" * 400,
])
def test_ha_refuses_lan_metadata_and_odd_addresses(o, url):
    kyle = o.kyle()
    r = kyle.post("/api/connect/home_assistant/start", json={"url": url})
    assert r.status_code == 400
    assert url not in r.text or not url  # never echoed
    assert not o.connect.pending and not kyle.cookies.get(owner_connect.BIND_COOKIE)
    assert o.ha.requests == []


def test_ha_access_tokens_refresh_in_memory(o):
    connect_ha(o, o.kyle())
    auth = owner_connect.ha_auth(o.config)
    now = [5000.0]
    auth.clock = lambda: now[0]
    auth.expires = now[0] + 1800
    assert auth.token() == HA_ACCESS and o.ha.refreshes == 0
    now[0] += 1800 - owner_connect.HA_SKEW + 1  # about to end
    assert auth.token() == HA_ACCESS_2 and o.ha.refreshes == 1
    assert auth.token() == HA_ACCESS_2 and o.ha.refreshes == 1
    _, record = sealed(o, "home_assistant")
    assert record["refresh_token"] == HA_REFRESH  # HA keeps it; nothing rewritten


def test_a_401_from_ha_refreshes_once_and_retries(o):
    connect_ha(o, o.kyle())
    ha = o.tools.tools["ha_list"].handler.__self__
    ha._session = HAStates(valid=[HA_ACCESS_2])  # HA_ACCESS ended early
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, is_error = reg.dispatch("ha_list", {})
    assert not is_error, content
    assert ha._session.seen[:2] == [HA_ACCESS, HA_ACCESS_2] and o.ha.refreshes == 1


def test_a_refused_ha_refresh_token_says_connect_again(o):
    connect_ha(o, o.kyle())
    ha = o.tools.tools["ha_list"].handler.__self__
    ha._session = HAStates(valid=[])
    o.ha.refresh_status = 400
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    content, _ = reg.dispatch("ha_list", {})
    assert i18n.EN["ha_reconnect"] in content
    with pytest.raises(HAError) as e:
        owner_connect.ha_auth(o.config).token(failed=HA_ACCESS)
    assert e.value.key == "ha_reconnect"


def test_ha_disconnect_revokes_and_deletes(o):
    kyle = o.kyle()
    connect_ha(o, kyle)
    r = kyle.post("/api/connect/home_assistant/disconnect", headers={"Content-Length": "0"})
    assert r.status_code == 200 and r.json()["managed"] == "none"
    revokes = o.ha.calls("/auth/revoke")
    assert [o.ha.form(x) for x in revokes] == [{"token": HA_REFRESH}]
    assert not (o.tmp / "owner" / "home_assistant.enc").exists()
    assert not {"ha_list", "ha_state", "ha_control"} & set(o.tools.tools)
    assert owner_connect.ha_auth(o.config).access is None
    with pytest.raises(HAError):
        owner_connect.ha_auth(o.config).token()


def test_a_refused_ha_code_writes_nothing(o):
    kyle = o.kyle()
    o.ha.token_status = 400
    r = callback(kyle, state_of(ha_start(o, kyle)), HA_CODE, "/ha/callback")
    assert r.status_code == 400
    assert not (o.tmp / "owner" / "home_assistant.enc").exists()
    assert "ha_list" not in o.tools.tools


def test_a_wrong_pkce_verifier_is_refused_by_ha(o):
    kyle = o.kyle()
    r = ha_start(o, kyle)
    o.ha.challenge = "not-the-challenge"
    assert callback(kyle, state_of(r), HA_CODE, "/ha/callback").status_code == 400


def test_env_ha_token_wins(o, monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "llat-" + "x" * 40)
    kyle = o.kyle()
    assert status(kyle, "home_assistant")["managed"] == "env"
    r = kyle.post("/api/connect/home_assistant/start", json={"url": HA_HOST})
    assert r.status_code == 409 and "HA_TOKEN" in r.json()["error"]


# --- startup, logs, config ------------------------------------------------------------

def test_build_tools_registers_app_sign_ins_at_startup(o, monkeypatch):
    kyle = o.kyle()
    connect_notion(o, kyle)
    connect_ha(o, kyle)
    out = []
    registry = cli.build_tools(o.config, out.append)
    assert {"list_assignments", "update_assignment_status", "ha_list", "ha_state",
            "ha_control"} <= set(registry.tools)
    assert not any("Notion tools off" in line for line in out)
    assert not any("Home Assistant tools off" in line for line in out)
    # NOTION_TOKEN on the server: exactly as before, the sign-in is not used
    monkeypatch.setenv("NOTION_TOKEN", KYLES)
    registry = cli.build_tools(o.config, out.append)
    assert registry.tools["list_assignments"].handler.__self__.token is None
    assert registry.tools["list_assignments"].handler.__self__._client is None


def test_no_tokens_codes_or_addresses_in_the_log(o, caplog):
    caplog.set_level(logging.DEBUG)
    kyle = o.kyle()
    notion_state = state_of(start(kyle))
    nonce = kyle.cookies.get(owner_connect.BIND_COOKIE)
    assert callback(kyle, notion_state).status_code == 303
    o.fake.valid = set()
    reg = ToolRegistry()
    reg.tools = o.tools.tools
    reg.dispatch("list_assignments", {})  # and a Notion refresh
    connect_ha(o, kyle)
    owner_connect.ha_auth(o.config).token(failed=HA_ACCESS)  # an HA refresh
    kyle.post("/api/connect/notion/disconnect", headers={"Content-Length": "0"})
    kyle.post("/api/connect/home_assistant/disconnect", headers={"Content-Length": "0"})
    ours = [r for r in caplog.records if not r.name.startswith("httpx")]
    text = " ".join(r.getMessage() + str(r.exc_text or "") for r in ours)
    for secret in (ACCESS, ACCESS_2, REFRESH, CODE, HA_CODE, HA_ACCESS, HA_ACCESS_2,
                   HA_REFRESH, notion_state, nonce, CLIENT_SECRET, WORKSPACE, HA_HOST):
        assert secret not in text, secret
    info = [r.getMessage() for r in ours if r.levelno >= logging.INFO]
    assert any("connected Notion" in m and "w0rk-1d" in m for m in info)  # ids are
    assert any("connected Home Assistant" in m for m in info)


@pytest.mark.parametrize("secret", [HA_ACCESS, HA_REFRESH, "ntn_" + "Z" * 46,
                                    "secret_" + "Q" * 43])
def test_the_stored_token_formats_are_redacted(secret):
    line = f"Home Assistant said {secret} (and more)"
    assert secret not in redact(line) and "[REDACTED]" in redact(line)


def test_redaction_leaves_short_hex_and_ordinary_text_alone():
    line = "Commit 3f2a1b0 changed 12 files; sha256 " + "ab" * 32
    assert redact(line) == line


def test_config_docs_and_deploy_know_the_new_settings():
    from pathlib import Path

    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    from kwil import cloud

    root = Path(__file__).resolve().parent.parent
    with open(root / "config.toml", "rb") as f:
        assert tomllib.load(f)["connect"] == DEFAULTS["connect"]
    assert load_config(None, None, profile="server")["connect"]["dir"] == "/data/owner"
    assert {"/ha/callback", "/connect.js", "/connections.js",
            "/notion/callback"} <= cloud.PUBLIC_GET
    web = root / "kwil" / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    # its rows live in the menu's Connections list (connections.js)
    assert 'id="connections"' in page and 'id="conn-available"' in page
    assert page.index("/connections.js") < page.index("/i18n.js") \
        < page.index("/connect.js") < page.index("/app.js")
    sw = (web / "sw.js").read_text(encoding="utf-8")
    assert '"/connect.js"' in sw and '"/connections.js"' in sw and "kwil-shell-v16" in sw
    assert "KwilConnect.onState" in (web / "app.js").read_text(encoding="utf-8")
    for table in (i18n.EN, i18n.ZH):
        assert table["connect_failed"] and table["ha_reconnect"]
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Connect your services in the app" in readme
    render = (root / "render.yaml").read_text(encoding="utf-8")
    assert "optional" in render[render.index("key: NOTION_TOKEN") - 400:
                                render.index("key: NOTION_TOKEN")].lower()


def test_the_page_code_is_served(o):
    r = TestClient(o.app, base_url=BASE).get("/connect.js")
    assert r.status_code == 200 and "KwilConnect" in r.text


def test_a_non_admin_401_from_the_template_api_never_refreshes():
    """A non-admin user's token gets 401 from /api/template by design: that
    must not cost a token refresh every time the device list is read."""
    from kwil.tools.home_assistant import HomeAssistant
    from tests.test_home_assistant import Resp

    asked = []

    def token_fn(failed=None):
        asked.append(failed)
        return HA_ACCESS

    class Session:
        def request(self, method, url, **kwargs):
            return Resp(401, {"message": "Unauthorized"})

    ha = HomeAssistant(url=HA, token_fn=token_fn, session=Session())
    assert ha._areas() == {}
    assert asked == [None]


def test_an_ipv6_address_keeps_its_brackets(monkeypatch):
    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, port, 0, 0))]
    monkeypatch.setattr(canvas, "RESOLVE", resolve)
    assert owner_connect.ha_origin("https://[2606:4700::1111]") == "https://[2606:4700::1111]"
