"""The phone web app with failover on (--role): bearer tokens, CORS for the
app origin, the CSP and WebSocket auth, through the real FastAPI app."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi import Depends, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from assistant import webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import DEFAULTS  # noqa: E402
from assistant.failover.peers import settings_from_config  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402

TOKEN = "t" * 20 + "-test-token-0123456789abcdef"
APP = "https://hey-claude.onrender.com"
PI = "https://pi.tail1234.ts.net"
PC = "https://kyle-pc.tail1234.ts.net"
CROSS = {"origin": APP, "sec-fetch-site": "cross-site", "sec-fetch-mode": "cors"}


def settings(role="pi"):
    cfg = {**DEFAULTS, "failover": {"role": role, "app_origin": APP, "sync_interval": 30,
                                   "backends": [{"role": "pi", "url": PI},
                                                {"role": "pc", "url": PC}]}}
    return settings_from_config(cfg)


@pytest.fixture
def make(fake_client):
    def build(role="pi", ws_route=False):
        def chat_factory(registry):
            return ClaudeChat(client=fake_client, model="test-model", tools=registry)

        assistant = webapp.WebAssistant(chat_factory, None, PrintTTS())
        app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                                failover=settings(role) if role else None)
        if ws_route:
            inner = app.app  # the FastAPI app inside EdgeMiddleware

            @inner.websocket("/api/ws")
            async def ws(websocket: WebSocket,
                         session=Depends(webapp.require_session)):
                await websocket.accept()
                await websocket.send_text(json.dumps({"sid": session.sid}))
                await websocket.close()
        base = {"pi": PI, "pc": PC, "cloud": APP}.get(role, "https://testserver")
        return assistant, TestClient(app, base_url=base)
    return build


def sign_in(client, headers=CROSS):
    r = client.post("/api/auth/token", json={"token": TOKEN}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token, **extra):
    return {"authorization": f"Bearer {token}", **extra}


def test_without_a_role_nothing_changes(make):
    _, client = make(role=None)
    assert "role" not in client.get("/healthz").json()
    assert client.post("/api/auth/token", json={"token": TOKEN}).status_code == 404


def test_healthz_reports_role(make):
    _, client = make("pc")
    assert client.get("/healthz", headers=CROSS).json() == {"ok": True, "api": 1, "role": "pc"}


def test_cross_origin_turn_from_the_app(make, fake_client):
    fake_client.queue = [make_response(text_block("Hi from the Pi."))]
    assistant, client = make("pi")
    token = sign_in(client)
    r = client.get("/api/state", headers=bearer(token, **CROSS))
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == APP
    assert "access-control-allow-credentials" not in r.headers
    assert "set-cookie" not in r.headers  # no cookies in bearer mode
    r = client.post("/api/turn", json={"text": "hello"}, headers=bearer(token, **CROSS))
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert [e for e in events if e["type"] == "reply"][0]["text"] == "Hi from the Pi."


def test_preflight_from_the_app(make):
    _, client = make("pi")
    r = client.options("/api/turn", headers={
        **CROSS, "access-control-request-method": "POST",
        "access-control-request-headers": "authorization,content-type"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == APP


def test_same_token_works_on_every_backend(make):
    _, cloud = make("cloud")
    _, pi = make("pi")
    token = sign_in(cloud, headers={})
    assert pi.get("/api/state", headers=bearer(token, **CROSS)).status_code == 200


def test_session_follows_the_token_sid(make):
    assistant, client = make("pi")
    token = sign_in(client)
    client.get("/api/state", headers=bearer(token, **CROSS))
    sid = next(iter(assistant.sessions))
    from assistant.failover.tokens import TokenAuthority

    assert TokenAuthority(TOKEN).verify(token).sid == sid


@pytest.mark.parametrize("headers", [
    {},                                                     # nothing
    {"authorization": "Bearer wrong"},                     # wrong token
    {"cookie": "hc_session=x.y"},                         # cookies aren't used
])
def test_missing_or_wrong_token_is_401(make, headers):
    for method, path in [("GET", "/api/state"), ("POST", "/api/turn"),
                         ("GET", "/api/events"), ("POST", "/api/confirm"),
                         ("GET", "/api/audio/1-x"), ("GET", "/api/backends")]:
        _, client = make("pi")  # fresh: 5 failures in a row lock out (429)
        r = client.request(method, path, json={"text": "hi"}, headers={**CROSS, **headers})
        assert r.status_code == 401, (method, path)


def test_foreign_origin_is_403(make):
    _, client = make("pi")
    token = sign_in(client)
    evil = {"origin": "https://evil.example", "sec-fetch-site": "cross-site"}
    for method, path in [("GET", "/api/state"), ("POST", "/api/auth/token"),
                         ("GET", "/healthz"), ("OPTIONS", "/api/turn")]:
        r = client.request(method, path, headers=bearer(token, **evil))
        assert r.status_code == 403, (method, path)
        assert "access-control-allow-origin" not in r.headers


def test_scripts_may_still_use_web_token_as_bearer(make):
    _, client = make("pi")
    assert client.get("/api/state", headers=bearer(TOKEN)).status_code == 200


def test_csp_allows_exactly_the_backends(make):
    _, client = make("cloud")
    csp = client.get("/").headers["content-security-policy"]
    assert f"connect-src 'self' {PI} wss://pi.tail1234.ts.net {PC} " in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "evil" not in csp


def test_backends_list_for_the_app(make):
    _, client = make("pi")
    token = sign_in(client)
    r = client.get("/api/backends", headers=bearer(token, **CROSS))
    assert [b["role"] for b in r.json()["backends"]] == ["pi", "pc", "cloud"]


def test_router_module_is_served(make):
    _, client = make("cloud")
    r = client.get("/backend-router.js")
    assert r.status_code == 200 and "export class BackendRouter" in r.text


def test_websocket_first_message_auth(make):
    _, client = make("pi", ws_route=True)
    token = sign_in(client)
    with client.websocket_connect("/api/ws", headers={"origin": APP}) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(ws.receive_text()) == {"type": "auth_ok"}
        assert "sid" in json.loads(ws.receive_text())


@pytest.mark.parametrize("first", [json.dumps({"type": "auth", "token": "wrong"}), "hi"])
def test_websocket_without_valid_token_is_closed(make, first):
    _, client = make("pi", ws_route=True)
    with client.websocket_connect("/api/ws", headers={"origin": APP}) as ws:
        ws.send_text(first)
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_websocket_from_foreign_origin_refused(make):
    _, client = make("pi", ws_route=True)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}):
            pass


def test_sign_out_everywhere_survives_a_restart_without_history(fake_client, tmp_path):
    """No history (nothing to sync): the sign-out time is kept in the
    sessions file, so a restarted backend still refuses old tokens."""
    sessions = tmp_path / "web_sessions.json"

    def boot():
        assistant = webapp.WebAssistant(
            lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg), None, PrintTTS())
        app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                                sessions_file=sessions, failover=settings("pi"))
        return TestClient(app, base_url=PI)

    client = boot()
    token = sign_in(client)
    assert client.post("/api/logout-all", headers=bearer(token, **CROSS)).status_code == 200
    client = boot()  # restart
    assert client.get("/api/state", headers=bearer(token, **CROSS)).status_code == 401
