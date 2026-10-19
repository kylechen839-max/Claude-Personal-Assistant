"""Cross-origin, bearer-token and WebSocket auth middleware (failover/edge.py)."""

import json

import pytest

from assistant.failover import API_VERSION
from assistant.failover.edge import (WS_CLOSE_AUTH_TIMEOUT, WS_CLOSE_UNAUTHORIZED,
                                     EdgeMiddleware, connect_src)
from assistant.failover.peers import parse_backends
from assistant.failover.tokens import LEEWAY, TokenAuthority, bearer_of
from assistant.failover.sync import MemoryEventLog, make_sync_app, new_event
from tests.asgi_harness import request, websocket

SECRET = "s" * 43
APP = "https://hey-claude.onrender.com"
EVIL = "https://evil.example"
PI = "https://pi.tail1234.ts.net"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


async def inner(scope, receive, send):
    """Stands in for the web app: echoes who called."""
    if scope["type"] == "websocket":
        msg = await receive()
        assert msg["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})  # swallowed by the middleware
        while True:
            msg = await receive()
            if msg["type"] == "websocket.disconnect" or msg.get("text") == "bye":
                await send({"type": "websocket.close", "code": 1000})
                return
            await send({"type": "websocket.send",
                        "text": json.dumps({"echo": msg["text"], "sid": scope["auth"].sid})})
    auth = scope.get("auth")
    body = json.dumps({"path": scope["path"], "sid": auth.sid if auth else None}).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json"),
                            (b"access-control-allow-credentials", b"true")]})
    await send({"type": "http.response.body", "body": body})


@pytest.fixture
def setup():
    clock = Clock()
    authority = TokenAuthority(SECRET, clock=clock)
    from assistant.webapp import LoginLimiter

    limiter = LoginLimiter(per_ip=5, window=900, lockout=900, clock=clock)
    app = EdgeMiddleware(inner, authority=authority, role="pi", app_origins=[APP],
                         root_bearer_sid="script",
                         backends=parse_backends([{"role": "pi", "url": PI},
                                                  {"role": "cloud", "url": APP}]),
                         limiter=limiter, ws_auth_timeout=0.3)
    token, _ = authority.mint("app", sid="phone1")
    return app, authority, token, clock


def auth(token):
    return {"authorization": f"Bearer {token}"}


# --- tokens -----------------------------------------------------------------

def test_tokens_roundtrip_expiry_and_scope():
    clock = Clock()
    a = TokenAuthority(SECRET, clock=clock)
    token, claims = a.mint("app", sid="abc", ttl=60)
    assert a.verify(token).sid == "abc"
    assert a.verify(token, scope="sync") is None
    assert SECRET not in token
    clock.t += 61
    assert a.verify(token) is not None  # within the clock leeway
    clock.t += LEEWAY
    assert a.verify(token) is None


def test_tokens_from_another_secret_or_tampered_are_rejected():
    a = TokenAuthority(SECRET)
    b = TokenAuthority("t" * 43)
    token, _ = b.mint("app", sid="abc")
    assert a.verify(token) is None
    good, _ = a.mint("app", sid="abc")
    head, sig = good.rsplit(".", 1)
    assert a.verify(good.replace(".abc.", ".abd.")) is None
    assert a.verify(head + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B")) is None
    for junk in ("", "x", "hc1.....", "hc1.app.abc.notanumber.1.sig", "a" * 1000, None):
        assert a.verify(junk) is None


def test_every_backend_accepts_a_token_minted_by_another():
    pi, cloud = TokenAuthority(SECRET), TokenAuthority(SECRET)
    token, _ = cloud.mint("app")
    assert pi.verify(token) is not None


def test_short_secret_refused():
    with pytest.raises(ValueError):
        TokenAuthority("short")


def test_bearer_header_parsing():
    assert bearer_of("Bearer abc") == "abc"
    assert bearer_of("bearer  abc ") == "abc"
    assert bearer_of("Basic abc") is None
    assert bearer_of("Bearer") is None
    assert bearer_of(None) is None


# --- /healthz ------------------------------------------------------------------

def test_healthz_reveals_only_ok_api_role(setup):
    app, *_ = setup
    r = request(app, "GET", "https://pi/healthz")
    assert r.status == 200
    assert r.json() == {"ok": True, "api": API_VERSION, "role": "pi"}
    assert r.header("cache-control") == "no-store"


def test_healthz_cors_for_app_origin_only(setup):
    app, *_ = setup
    r = request(app, "GET", "https://pi/healthz", {"origin": APP})
    assert r.header("access-control-allow-origin") == APP
    assert "access-control-allow-credentials" not in r.headers
    r = request(app, "GET", "https://pi/healthz", {"origin": EVIL})
    assert r.status == 403
    assert "access-control-allow-origin" not in r.headers


# --- CORS ------------------------------------------------------------------------

def test_preflight_from_app_origin(setup):
    app, *_ = setup
    r = request(app, "OPTIONS", "https://pi/api/turn",
                {"origin": APP, "access-control-request-method": "POST",
                 "access-control-request-headers": "authorization, content-type"})
    assert r.status == 204
    assert r.header("access-control-allow-origin") == APP
    assert r.header("access-control-allow-methods") == "GET, POST"
    assert r.header("access-control-allow-headers") == "authorization, content-type"
    assert "access-control-allow-credentials" not in r.headers
    assert r.header("vary") == "Origin"


@pytest.mark.parametrize("method,hdrs", [("DELETE", ""), ("PUT", ""),
                                         ("POST", "x-custom"), ("POST", "cookie")])
def test_preflight_refuses_unneeded_methods_and_headers(setup, method, hdrs):
    app, *_ = setup
    r = request(app, "OPTIONS", "https://pi/api/turn",
                {"origin": APP, "access-control-request-method": method,
                 "access-control-request-headers": hdrs})
    assert r.status == 403


@pytest.mark.parametrize("origin", [EVIL, "null", "http://hey-claude.onrender.com",
                                    "https://hey-claude.onrender.com.evil.example",
                                    "https://evil.example@hey-claude.onrender.com"])
def test_foreign_origin_rejected_everywhere(setup, origin):
    app, _, token, _ = setup
    for method, path in [("OPTIONS", "/api/turn"), ("GET", "/api/state"),
                         ("POST", "/api/turn"), ("POST", "/api/auth/token"),
                         ("GET", "/healthz"), ("GET", "/")]:
        hdrs = {"origin": origin, **auth(token)}
        if method == "OPTIONS":
            hdrs["access-control-request-method"] = "POST"
        r = request(app, method, "https://pi.tail1234.ts.net" + path, hdrs)
        assert r.status == 403, (origin, method, path)
        assert "access-control-allow-origin" not in r.headers


def test_same_origin_passes_without_cors_headers(setup):
    app, _, token, _ = setup
    r = request(app, "GET", "https://pi.tail1234.ts.net/api/state",
                {"origin": PI, **auth(token)})
    assert r.status == 200
    assert "access-control-allow-origin" not in r.headers


def test_cors_headers_added_and_credentials_stripped(setup):
    app, _, token, _ = setup
    r = request(app, "GET", "https://pi/api/state", {"origin": APP, **auth(token)})
    assert r.status == 200
    assert r.json() == {"path": "/api/state", "sid": "phone1"}
    assert r.header("access-control-allow-origin") == APP
    assert "access-control-allow-credentials" not in r.headers


# --- bearer ------------------------------------------------------------------------

@pytest.mark.parametrize("hdrs", [{}, {"authorization": "Bearer nope"},
                                  {"authorization": "Basic abc"},
                                  {"cookie": "hc_session=whatever"}])
def test_missing_or_wrong_token_is_401(setup, hdrs):
    """From the app origin (cross-origin): a bearer token is required."""
    app, *_ = setup
    for path in ("/api/state", "/api/turn", "/api/backends", "/api/sync/events",
                 "/api/auth/refresh"):
        r = request(app, "POST" if path.endswith(("turn", "refresh")) else "GET",
                    "https://pi" + path, {"origin": APP, **hdrs})
        assert r.status == 401, path
        assert r.header("access-control-allow-origin") == APP  # so the app can read it


def test_expired_token_is_401(setup):
    app, authority, _, clock = setup
    token, _ = authority.mint("app", ttl=10)
    assert request(app, "GET", "https://pi/api/state", auth(token)).status == 200
    clock.t += 11 + LEEWAY
    assert request(app, "GET", "https://pi/api/state", auth(token)).status == 401


def test_scopes_are_separate(setup):
    app, authority, app_token, _ = setup
    sync_token, _ = authority.mint("sync", sid="pc")
    assert request(app, "GET", "https://pi/api/sync/events", auth(app_token)).status == 401
    assert request(app, "GET", "https://pi/api/state", auth(sync_token)).status == 401
    assert request(app, "GET", "https://pi/api/sync/events", auth(sync_token)).status == 200


def test_same_origin_without_bearer_goes_to_the_app_cookie_check(setup):
    app, *_ = setup
    # no Origin (curl) or our own origin, no Authorization: the web app decides
    r = request(app, "GET", "https://pi.tail1234.ts.net/api/state",
                {"origin": PI, "cookie": "hc_session=x"})
    assert r.status == 200 and r.json() == {"path": "/api/state", "sid": None}
    assert request(app, "GET", "https://pi/api/state").json()["sid"] is None
    # but never the sync API or the backends list
    assert request(app, "GET", "https://pi/api/sync/events").status == 401
    assert request(app, "GET", "https://pi/api/backends").status == 401


def test_static_app_shell_needs_no_token(setup):
    app, *_ = setup
    assert request(app, "GET", "https://pi/").status == 200


def test_backends_list(setup):
    app, _, token, _ = setup
    r = request(app, "GET", "https://pi/api/backends", auth(token))
    assert r.json() == {"role": "pi", "api": API_VERSION,
                        "backends": [{"role": "pi", "url": PI}, {"role": "cloud", "url": APP}]}


# --- token endpoint and lockout ----------------------------------------------------------

def test_token_endpoint_trades_web_token_for_derived_token(setup):
    app, authority, _, _ = setup
    r = request(app, "POST", "https://pi/api/auth/token", {"origin": APP},
                {"token": SECRET, "sid": "keepme"})
    assert r.status == 200
    data = r.json()
    assert data["token_type"] == "Bearer" and data["sid"] == "keepme"
    assert SECRET not in r.body.decode()
    assert authority.verify(data["access_token"]).sid == "keepme"
    assert r.header("cache-control") == "no-store"


def test_refresh_keeps_sid(setup):
    app, authority, token, clock = setup
    clock.t += 100
    r = request(app, "POST", "https://pi/api/auth/refresh", auth(token))
    assert r.status == 200
    new = r.json()["access_token"]
    assert new != token and authority.verify(new).sid == "phone1"


def test_refresh_stops_at_the_session_limit(setup):
    from assistant.failover.tokens import MAX_SESSION

    app, authority, token, clock = setup
    signed_in = authority.verify(token).signed_in
    for _ in range(4):  # a stolen token can't be refreshed forever
        clock.t += 25 * 24 * 3600
        r = request(app, "POST", "https://pi/api/auth/refresh", auth(token))
        if r.status != 200:
            break
        token = r.json()["access_token"]
        claims = authority.verify(token)
        assert claims.signed_in == signed_in
        assert claims.expires <= signed_in + MAX_SESSION
    assert r.status == 401
    clock.t = signed_in + MAX_SESSION + LEEWAY + 1
    assert authority.verify(token) is None


def scoped(ip):
    return {"x-test-ip": ip}


def test_wrong_web_token_locks_out_that_address_but_never_the_right_token(setup):
    app, _, token, clock = setup
    app.client_ip = lambda scope: dict(scope["headers"]).get(b"x-test-ip", b"").decode()
    for _ in range(5):
        r = request(app, "POST", "https://pi/api/auth/token", scoped("1.1.1.1"),
                    {"token": "x" * 43})
        assert r.status == 401
    # that address now gets 429 for wrong tokens...
    assert request(app, "POST", "https://pi/api/auth/token", scoped("1.1.1.1"),
                   {"token": "x" * 43}).status == 429
    # a guessed WEB_TOKEN as bearer is refused too; derived-token garbage is
    # just 401 (unguessable, so not counted)
    assert request(app, "GET", "https://pi/api/state",
                   {**auth("y" * 43), **scoped("1.1.1.1")}).status == 429
    assert request(app, "GET", "https://pi/api/state",
                   {**auth("hc1.nope"), **scoped("1.1.1.1")}).status == 401
    # ...but the right WEB_TOKEN and a valid bearer token still work, even from it
    assert request(app, "POST", "https://pi/api/auth/token", scoped("1.1.1.1"),
                   {"token": SECRET}).status == 200
    assert request(app, "GET", "https://pi/api/state",
                   {**auth(token), **scoped("1.1.1.1")}).status == 200
    # and other addresses are not affected
    assert request(app, "POST", "https://pi/api/auth/token", scoped("2.2.2.2"),
                   {"token": "x" * 43}).status == 401


def test_expired_or_bad_derived_tokens_never_lock_out_the_owner(setup):
    """An old phone retrying with an expired token, a Pi with a stale clock
    sending sync tokens, a WebSocket with a bad token: none of it counts."""
    app, authority, _, clock = setup
    old, _ = authority.mint("app", ttl=1)
    clock.t += 10_000
    for _ in range(30):
        assert request(app, "GET", "https://pi/api/state", {"origin": APP, **auth(old)}).status == 401
        assert request(app, "POST", "https://pi/api/auth/refresh", auth(old)).status == 401
        assert request(app, "GET", "https://pi/api/sync/events", auth("hc1.sync.pc.1.1.x")).status == 401
    for _ in range(6):
        websocket(app, "/api/ws", [json.dumps({"type": "auth", "token": old})], {"origin": APP})
    assert request(app, "POST", "https://pi/api/auth/token", body={"token": SECRET}).status == 200
    assert request(app, "POST", "https://pi/api/auth/token", body={"token": "x" * 43}).status == 401


def test_missing_token_does_not_count_toward_lockout(setup):
    app, _, token, _ = setup
    for _ in range(20):  # e.g. an app polling before sign-in
        assert request(app, "GET", "https://pi/api/state", {"origin": APP}).status == 401
    assert request(app, "POST", "https://pi/api/auth/token", body={"token": "x" * 43}).status == 401


def test_ws_same_origin_cookie_client_goes_to_the_app(setup):
    app, *_ = setup
    seen = []

    async def cookie_app(scope, receive, send):
        seen.append((await receive())["type"])  # the replayed connect
        await send({"type": "websocket.close", "code": 1008})  # the app's own check

    app.app = cookie_app
    sent = websocket(app, "/api/ws", [], {"origin": PI, "cookie": "hc_session=x"},
                     host="pi.tail1234.ts.net")
    assert seen == ["websocket.connect"] and sent[0]["code"] == 1008


def test_oversized_token_request(setup):
    app, *_ = setup
    r = request(app, "POST", "https://pi/api/auth/token", body=b"{" + b" " * 5000 + b"}")
    assert r.status == 413


# --- WebSocket ------------------------------------------------------------------------------

def _closes(sent):
    return [m for m in sent if m["type"] == "websocket.close"]


def test_ws_first_message_auth(setup):
    app, _, token, _ = setup
    sent = websocket(app, "/api/ws", [json.dumps({"type": "auth", "token": token}),
                                      "hello", "bye"], {"origin": APP})
    texts = [m["text"] for m in sent if m["type"] == "websocket.send"]
    assert json.loads(texts[0]) == {"type": "auth_ok"}
    assert json.loads(texts[1]) == {"echo": "hello", "sid": "phone1"}
    assert [m["type"] for m in sent].count("websocket.accept") == 1


@pytest.mark.parametrize("first", ["hello", json.dumps({"type": "auth", "token": "nope"}),
                                   json.dumps({"type": "auth"}), json.dumps(["auth"]),
                                   json.dumps({"type": "auth", "token": None})])
def test_ws_wrong_or_missing_token_closed_4401(setup, first):
    app, *_ = setup
    sent = websocket(app, "/api/ws", [first, "hello"], {"origin": APP})
    assert _closes(sent)[0]["code"] == WS_CLOSE_UNAUTHORIZED
    assert not any(m["type"] == "websocket.send" for m in sent)


def test_ws_sync_token_not_accepted(setup):
    app, authority, *_ = setup
    sync_token, _ = authority.mint("sync", sid="pc")
    sent = websocket(app, "/api/ws", [json.dumps({"type": "auth", "token": sync_token})])
    assert _closes(sent)[0]["code"] == WS_CLOSE_UNAUTHORIZED


def test_ws_token_in_url_is_ignored(setup):
    app, _, token, _ = setup
    sent = websocket(app, f"/api/ws?token={token}", ["hello"], {"origin": APP})
    assert _closes(sent)[0]["code"] == WS_CLOSE_UNAUTHORIZED


def test_ws_closed_when_no_auth_arrives(setup):
    app, *_ = setup
    sent = websocket(app, "/api/ws", [], {"origin": APP})
    assert _closes(sent)[0]["code"] == WS_CLOSE_AUTH_TIMEOUT


def test_ws_foreign_origin_refused_at_handshake(setup):
    app, _, token, _ = setup
    sent = websocket(app, "/api/ws", [json.dumps({"type": "auth", "token": token})],
                     {"origin": EVIL})
    assert sent and sent[0]["type"] == "websocket.close"  # before accept: HTTP 403
    assert not any(m["type"] == "websocket.accept" for m in sent)


def test_forwarded_host_counts_only_from_a_trusted_proxy(setup):
    app, _, token, _ = setup
    hdrs = {"origin": "https://attacker.example", "x-forwarded-host": "attacker.example",
            **auth(token)}
    assert request(app, "GET", "https://pi.tail1234.ts.net/api/state", hdrs).status == 403
    app.trusts_proxy = lambda scope: True  # e.g. Render's proxy, set via trusted_proxy
    assert request(app, "GET", "https://pi.tail1234.ts.net/api/state", hdrs).status == 200


def test_connect_src_lists_backends():
    backends = parse_backends([{"role": "pi", "url": PI}, {"role": "pc", "url": ""},
                               {"role": "cloud", "url": APP}])
    assert connect_src(backends) == (f"'self' {PI} wss://pi.tail1234.ts.net "
                                     f"{APP} wss://hey-claude.onrender.com")


def test_backend_urls_validated():
    with pytest.raises(ValueError):
        parse_backends([{"role": "pi", "url": "http://pi.tail1234.ts.net"}])
    with pytest.raises(ValueError):
        parse_backends([{"role": "mac", "url": PI}])
    with pytest.raises(ValueError):
        parse_backends([{"role": "pi", "url": PI + "/app"}])
    assert parse_backends([{"role": "pi", "url": "https://PI.tail1234.ts.net/"}])[0].url == PI


# --- the sync endpoints behind the middleware ---------------------------------

def test_sync_app_behind_edge_middleware():
    store = MemoryEventLog()
    authority = TokenAuthority(SECRET)

    async def nothing(scope, receive, send):
        raise AssertionError("not reached")

    app = EdgeMiddleware(nothing, authority=authority, role="cloud",
                         app_origins=["https://hc.onrender.com"],
                         routes={"/api/sync/": make_sync_app(store)})
    token, _ = authority.mint("sync", sid="pi")
    ev = new_event("message", "c", {"text": "x"}, origin="pi")
    hdr = {"authorization": f"Bearer {token}"}
    r = request(app, "POST", "https://hc/api/sync/events", hdr, {"events": [ev.to_json()]})
    assert r.status == 200 and r.json()["accepted"] == 1
    r = request(app, "GET", "https://hc/api/sync/events?after=0", hdr)
    assert r.json()["events"] == [ev.to_json()] and r.json()["log"] == store.log_id
    app_token, _ = authority.mint("app")
    r = request(app, "GET", "https://hc/api/sync/events",
                {"authorization": f"Bearer {app_token}"})
    assert r.status == 401
    # and the bare app refuses calls that skipped the middleware
    r = request(make_sync_app(store), "GET", "https://hc/api/sync/events")
    assert r.status == 401
