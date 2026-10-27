"""ASGI middleware in front of every backend (Pi, PC, cloud).

It owns everything that is the same on all three:

- GET /healthz -> {"ok": true, "api": <int>, "role": "pi|pc|cloud"}, nothing
  more, no auth (the app probes it to pick a backend).
- CORS for the app origin only (the cloud host the PWA is installed from):
  methods GET/POST, headers Authorization/Content-Type, never credentials.
  A request or WebSocket handshake whose Origin is neither the app origin
  nor this server's own origin is refused with 403. Requests with no Origin
  (other backends, curl) pass the origin check and still need a token.
- POST /api/auth/token: trade WEB_TOKEN for a derived bearer token (see
  tokens.py). Wrong tokens count toward the web app's LoginLimiter (per
  address); the right one is always accepted. POST /api/auth/refresh
  renews a valid app token.
- Bearer auth on /api/ paths: a wrong token -> 401 (429 once that address
  is locked out); a valid one is never locked out. App tokens can't use
  /api/sync/, sync tokens can only use /api/sync/. A same-origin request
  with no Authorization header goes on to the web app, whose session
  cookie decides (cookie clients keep working); cross-origin requests
  always need a bearer token (browsers send no cookies there).
- GET /api/backends: this backend's configured list, for the app to cache.
- The Canvas helper's pushes (canvas_helper.push_request: POST to its two
  push routes with a `kcs1.` sync key) go straight to the web app, which
  checks the key: no origin check (they come from a browser extension),
  no CORS headers, and the key is never taken for a guess at WEB_TOKEN.
- WebSockets: the token comes in the first message after the socket opens,
  {"type": "auth", "token": "..."}, never in the URL. No valid token within
  `ws_auth_timeout` seconds -> closed (4401 wrong/missing, 4408 too slow).
  The wrapped app then sees an authenticated socket (scope["auth"]).

The wrapped app reads the caller from scope["auth"] (a tokens.Claims).
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit

from kwil.canvas_helper import push_request
from kwil.failover import API_VERSION
from kwil.failover.peers import Backend, normalize_url
from kwil.failover.tokens import MAX_SESSION, Claims, TokenAuthority, bearer_of

log = logging.getLogger(__name__)

ALLOWED_METHODS = ("GET", "POST")
ALLOWED_HEADERS = ("authorization", "content-type")
PREFLIGHT_MAX_AGE = "600"
MAX_AUTH_BODY = 4096
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_AUTH_TIMEOUT = 4408
WS_CLOSE_FORBIDDEN_ORIGIN = 1008  # before accept: the server answers 403


def default_client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else ""


def _headers(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        out[k.decode("latin-1").lower()] = v.decode("latin-1")
    return out


def own_origins(headers: dict[str, str], trust_forwarded: bool = False) -> set[str]:
    """host[:port] values this request was addressed to. X-Forwarded-Host
    counts only from a trusted proxy (anyone can send the header)."""
    hosts = [headers.get("host", "")]
    if trust_forwarded:
        hosts.append(headers.get("x-forwarded-host", ""))
    return {h.strip().lower() for h in hosts if h.strip()}


def connect_src(backends: list[Backend]) -> str:
    """CSP connect-src value letting the app reach every backend."""
    srcs = ["'self'"]
    for b in backends:
        srcs.append(b.url)
        srcs.append("wss://" + b.url.split("://", 1)[1])
    return " ".join(dict.fromkeys(srcs))


class EdgeMiddleware:
    def __init__(self, app, *, authority: TokenAuthority, role: str,
                 app_origins=(), backends: list[Backend] = (),
                 routes: dict | None = None,
                 limiter=None, client_ip=default_client_ip,
                 trusts_proxy=lambda scope: False,
                 ws_auth_timeout: float = 5.0, protected_prefix: str = "/api/",
                 root_bearer_sid: str | None = None,
                 not_before=lambda: 0):
        self.app = app
        self.authority = authority
        self.role = role
        self.app_origins = {normalize_url(o) for o in app_origins}
        self.backends = list(backends)
        # extra ASGI apps by path prefix, e.g. {"/api/sync/": sync_app};
        # those under /api/sync/ need a sync-scope token
        self.routes = dict(routes or {})
        # The web app's own LoginLimiter (per address, plus a global limit
        # that only slows wrong attempts); the right token is never refused.
        if limiter is None:
            from kwil.webapp import LoginLimiter

            limiter = LoginLimiter()
        self.limiter = limiter
        self.client_ip = client_ip
        self.trusts_proxy = trusts_proxy  # scope -> bool (webapp's trusted_proxy)
        self.ws_auth_timeout = ws_auth_timeout
        self.protected_prefix = protected_prefix
        # Scripts may also send WEB_TOKEN itself as the bearer token (as the
        # web app allows without failover); they share this one session.
        self.root_bearer_sid = root_bearer_sid
        # "Sign out everywhere": app tokens from a sign-in at or before this
        # unix time are refused (synced between backends, see webapp)
        self.not_before = not_before

    @property
    def state(self):
        """The wrapped app's state (e.g. app.state.web for CloudGuard)."""
        return getattr(self.app, "state", None)

    # --- origin -------------------------------------------------------------

    def origin_verdict(self, headers: dict[str, str],
                       scope=None) -> tuple[bool, str | None]:
        """(allowed, origin to echo in CORS headers or None)."""
        origin = headers.get("origin")
        if origin is None:
            return True, None
        try:
            norm = normalize_url(origin)
        except ValueError:
            return False, None  # "null", garbage
        trusted = bool(scope is not None and self.trusts_proxy(scope))
        if urlsplit(norm).netloc in own_origins(headers, trusted):
            # same origin (also the cloud host, whose origin is the app's):
            # no CORS headers needed, and cookie clients stay cookie clients
            return True, None
        if norm in self.app_origins:
            return True, norm
        return False, None

    async def _fail(self, ip: str) -> None:
        """Count a wrong token. Newer LoginLimiters return a delay that
        slows wrong attempts once many arrive; honor it (capped)."""
        delay = self.limiter.fail(ip)
        if isinstance(delay, (int, float)) and delay > 0:
            await asyncio.sleep(min(float(delay), 5.0))

    @staticmethod
    def needed_scope(path: str) -> str:
        return "sync" if path.startswith("/api/sync/") else "app"

    def check_bearer(self, headers: dict[str, str], path: str) -> Claims | None:
        given = bearer_of(headers.get("authorization"))
        scope = self.needed_scope(path)
        claims = self.app_claims(self.authority.verify(given, scope=scope))
        if claims is None and given and self.root_bearer_sid and scope == "app" \
                and not given.startswith("hc1.") and self.authority.check_root(given):
            claims = Claims("app", self.root_bearer_sid, 0, 0)
        return claims

    def app_claims(self, claims: Claims | None) -> Claims | None:
        """None for an app token signed in before the last sign-out-everywhere."""
        if claims is not None and claims.scope == "app" and claims.signed_in <= self.not_before():
            return None
        return claims

    # --- ASGI ---------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _http(self, scope, receive, send):
        headers = _headers(scope)
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if push_request(method, path, headers.get("authorization")):
            # the Canvas helper's push: its extension's origin, its own sync
            # key (the web app checks it), no cookie and no CORS headers
            return await self.app(scope, receive, send)
        allowed, cors_origin = self.origin_verdict(headers, scope)
        if not allowed:
            log.warning("Refused a request from another origin")
            return await _json(send, 403, {"error": "origin not allowed"})

        def cors(extra=()):
            out = list(extra)
            if cors_origin:
                out += [("access-control-allow-origin", cors_origin),
                        ("vary", "Origin")]
            return out

        if method == "OPTIONS" and cors_origin and \
                "access-control-request-method" in headers:
            want = headers["access-control-request-method"].upper()
            want_headers = {h.strip().lower() for h in
                            headers.get("access-control-request-headers", "").split(",")
                            if h.strip()}
            if want not in ALLOWED_METHODS or not want_headers <= set(ALLOWED_HEADERS):
                return await _json(send, 403, {"error": "not allowed"}, cors())
            return await _empty(send, 204, cors([
                ("access-control-allow-methods", ", ".join(ALLOWED_METHODS)),
                ("access-control-allow-headers", ", ".join(ALLOWED_HEADERS)),
                ("access-control-max-age", PREFLIGHT_MAX_AGE)]))

        if path == "/healthz":
            if method not in ("GET", "HEAD"):
                return await _json(send, 405, {"error": "method not allowed"}, cors())
            return await _json(send, 200, {"ok": True, "api": API_VERSION,
                                           "role": self.role},
                               cors([("cache-control", "no-store")]))

        if path in ("/api/auth/token", "/api/auth/refresh"):
            if method != "POST":
                return await _json(send, 405, {"error": "method not allowed"}, cors())
            return await self._auth(scope, path, headers, receive, send, cors)

        cookie_client = ("authorization" not in headers and not cors_origin
                         and not path.startswith("/api/sync/")
                         and path != "/api/backends")
        if path.startswith(self.protected_prefix) and not cookie_client:
            claims = self.check_bearer(headers, path)
            if claims is None:
                # Derived tokens are 256-bit HMACs: guessing them is hopeless,
                # so bad, expired or missing ones are not counted (an old
                # phone retrying must not lock out its owner). Only a bearer
                # that could be a guess at WEB_TOKEN itself counts.
                given = bearer_of(headers.get("authorization"))
                if given and self.root_bearer_sid and not given.startswith("hc1.") \
                        and self.needed_scope(path) == "app":
                    ip = self.client_ip(scope)
                    if self.limiter.blocked(ip):
                        return await _json(send, 429, {"error": "too many attempts"},
                                           cors([("retry-after", "900")]))
                    await self._fail(ip)
                return await _json(send, 401, {"error": "unauthorized"},
                                   cors([("www-authenticate", "Bearer")]))
            scope = dict(scope, auth=claims)
            if path == "/api/backends" and method == "GET":
                return await _json(send, 200, {
                    "role": self.role, "api": API_VERSION,
                    "backends": [{"role": b.role, "url": b.url} for b in self.backends]},
                    cors([("cache-control", "no-store")]))

        target = self.app
        for prefix, app in self.routes.items():
            if path.startswith(prefix):
                target = app
                break

        async def send_with_cors(message):
            if message["type"] == "http.response.start" and cors_origin:
                message = dict(message)
                hdrs = [(k, v) for k, v in message.get("headers", [])
                        if k.lower() not in (b"access-control-allow-origin",
                                             b"access-control-allow-credentials")]
                hdrs += [(k.encode(), v.encode()) for k, v in cors()]
                message["headers"] = hdrs
            await send(message)

        await target(scope, receive, send_with_cors)

    async def _auth(self, scope, path, headers, receive, send, cors):
        body = await _read_body(receive)
        if body is None:
            return await _json(send, 413, {"error": "too large"}, cors())
        if path == "/api/auth/refresh":
            claims = self.app_claims(self.authority.verify(
                bearer_of(headers.get("authorization")), scope="app"))
            if claims is None:  # not counted: see the bearer check above
                return await _json(send, 401, {"error": "unauthorized"},
                                   cors([("www-authenticate", "Bearer")]))
            if claims.expires >= claims.signed_in + MAX_SESSION:
                # already at the session limit: sign in with WEB_TOKEN again
                return await _json(send, 401, {"error": "sign in again"},
                                   cors([("www-authenticate", "Bearer")]))
            return await self._issue(send, cors, claims.sid, claims.signed_in)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = None
        given = str(data.get("token", "")) if isinstance(data, dict) else ""
        if not given or not self.authority.check_root(given):
            ip = self.client_ip(scope)
            if self.limiter.blocked(ip):
                return await _json(send, 429, {"error": "too many attempts, wait a few minutes"},
                                   cors([("retry-after", "900")]))
            await self._fail(ip)
            log.warning("Token request with a wrong WEB_TOKEN")
            return await _json(send, 401, {"error": "wrong token"}, cors())
        sid = data.get("sid") if isinstance(data.get("sid"), str) else None
        return await self._issue(send, cors, sid)

    async def _issue(self, send, cors, sid, signed_in=None):
        try:
            token, claims = self.authority.mint("app", sid=sid, signed_in=signed_in)
        except ValueError:
            token, claims = self.authority.mint("app")
        return await _json(send, 200, {"access_token": token, "token_type": "Bearer",
                                       "expires_at": claims.expires, "sid": claims.sid},
                           cors([("cache-control", "no-store")]))

    async def _websocket(self, scope, receive, send):
        headers = _headers(scope)
        first = await receive()
        if first["type"] != "websocket.connect":
            return
        allowed, cors_origin = self.origin_verdict(headers, scope)
        if not allowed:
            log.warning("Refused a WebSocket from another origin")
            return await send({"type": "websocket.close",
                               "code": WS_CLOSE_FORBIDDEN_ORIGIN})
        first_message = b"auth=first" in scope.get("query_string", b"")
        if "cookie" in headers and not cors_origin and not first_message:
            # same-origin page with the session cookie: the web app's own
            # check (require_session) decides, as without failover
            async def replay():
                nonlocal first
                if first is not None:
                    message, first = first, None
                    return message
                return await receive()
            return await self.app(scope, replay, send)
        await send({"type": "websocket.accept"})
        try:
            msg = await asyncio.wait_for(receive(), self.ws_auth_timeout)
        except asyncio.TimeoutError:
            return await send({"type": "websocket.close", "code": WS_CLOSE_AUTH_TIMEOUT,
                               "reason": "auth timeout"})
        if msg["type"] == "websocket.disconnect":
            return
        claims = None
        try:
            data = json.loads(msg.get("text") or "")
            if isinstance(data, dict) and data.get("type") == "auth":
                claims = self.app_claims(
                    self.authority.verify(str(data.get("token", "")), scope="app"))
        except ValueError:
            pass
        if claims is None:
            return await send({"type": "websocket.close",
                               "code": WS_CLOSE_UNAUTHORIZED, "reason": "unauthorized"})
        await send({"type": "websocket.send", "text": json.dumps({"type": "auth_ok"})})

        replayed = False

        async def inner_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "websocket.connect"}
            return await receive()

        async def inner_send(message):
            if message["type"] == "websocket.accept":
                return  # already accepted above
            await send(message)

        await self.app(dict(scope, auth=claims), inner_receive, inner_send)


async def _read_body(receive) -> bytes | None:
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return body
        body += message.get("body", b"")
        if len(body) > MAX_AUTH_BODY:
            return None
        if not message.get("more_body"):
            return body


async def _json(send, status: int, payload, headers=()):
    data = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]
                + [(k.encode(), v.encode()) for k, v in headers]})
    await send({"type": "http.response.body", "body": data})


async def _empty(send, status: int, headers=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(k.encode(), v.encode()) for k, v in headers]})
    await send({"type": "http.response.body", "body": b""})
