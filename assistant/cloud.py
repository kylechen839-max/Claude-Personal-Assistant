"""The phone web app on a public cloud server (ASSISTANT_PROFILE=server).

`python -m assistant --mode web` in the Docker image runs the same app as
on the PC (assistant/webapp.py, which has its own headers, generic errors,
cross-site and size checks), wrapped in `CloudGuard`, an outer ASGI layer
for the public internet with no Tailscale in front:

- GET /healthz answers {"ok": true, "version": <commit>} for the host's
  health check and the page's reload-after-deploy, and nothing else.
- Every other route needs the session cookie (or `Authorization: Bearer
  <WEB_TOKEN>`), except the login page and its static files, the login call
  itself, and the PWA manifest and icons. Unknown paths answer 401 too.
- Wrong tokens go through the web app's own LoginLimiter (one instance,
  shared): the right token is always accepted; 5 wrong ones in 15 minutes
  lock their address's wrong tokens out for 15 minutes; past 20 from
  everyone, each wrong token waits a second. Login bodies are checked here,
  keyed by the real client address, so the web app never sees wrong ones.
  Cookies are checked against the web app's session store (revocable).
- Each address gets a request budget per minute.
- X-Forwarded-For / -Proto count only when the connection comes from the
  host's TLS proxy (a private address); then plain http is redirected to
  https and HSTS is sent. Cookies are always Secure, HttpOnly, SameSite=Strict.
- Request bodies are capped: audio uploads at [webapp] max_upload_mb,
  everything else at [cloud] max_body_kb, chunked uploads included.
- The voice WebSocket (/api/voice) gets the same checks before the
  handshake: rate limit, cookie or Bearer token (wrong ones count toward the
  lockout) and an Origin naming this site. X-Forwarded-Host, which the web
  app's own same-site checks read, is removed unless the proxy sent it.
- Errors never reach the client as stack traces.
- With failover on ([failover] role = "cloud", FAILOVER.md) it also accepts
  the derived `hc1.` bearer tokens (checked here, never counted as wrong
  WEB_TOKENs: they can't be guessed), keeps POST /api/auth/token public
  like /api/login (its body is checked the same way), lets the router
  module load before sign-in, answers /healthz with the API version and
  role too, and lets a same-site voice socket without a cookie through so
  its first message can carry the token (the failover layer closes it if
  no valid token arrives).

Secrets come from the environment only; nothing is written to disk.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from assistant import webapp

log = logging.getLogger(__name__)

# Reachable without signing in. "/" is the login page (and, once signed in,
# the app); its scripts, styles and service worker, and the voice view's
# open-source VAD files (ONNX Runtime, Silero), hold no secrets.
PUBLIC_GET = {"/", "/app.js", "/app.css", "/sw.js", "/update-watch.js",
              "/manifest.webmanifest",
              "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png",
              "/voice.js", "/voice-core.js", "/voice-worklet.js", "/voice.css",
              "/static/vad/ort.wasm.min.js", "/static/vad/ort-wasm-simd-threaded.mjs",
              "/static/vad/ort-wasm-simd-threaded.wasm",
              "/static/vad/silero_vad_v5.onnx"}
PUBLIC_POST = {"/api/login"}
# with failover on: the router module (loaded before sign-in) and the token
# trade, whose body (WEB_TOKEN) is checked here like /api/login's
FAILOVER_PUBLIC_GET = {"/backend-router.js"}
FAILOVER_PUBLIC_POST = {"/api/auth/token"}
HEALTH = "/healthz"
UPLOAD_PATHS = {"/api/turn", "/api/confirm"}
WEBSOCKETS = {"/api/voice"}  # the hands-free voice conversation
WS_MAX_SIZE = 2_000_000  # largest WebSocket message: a 30 s utterance is ~1 MB
HSTS = "max-age=31536000; includeSubDomains"


@dataclass(frozen=True)
class ProxyNetworks(webapp.ProxyTrust):
    """The web app's proxy trust, by network instead of exact address: the
    host's proxy reaches the container from a private address that changes
    (`[cloud] trusted_proxies`). One proxy hop, as on Render."""

    networks: tuple = ()

    def trusts(self, conn) -> bool:
        peer = conn.client.host if conn.client else ""
        return _in_networks(peer, self.networks)


class BoundedLoginLimiter(webapp.LoginLimiter):
    """The web app's sign-in limiter (the right token is always accepted;
    wrong ones lock their address out, and past `global_free` everyone's
    wrong tokens are slowed down), shared by CloudGuard and the web app,
    with its tables hard-capped: a flood from fresh addresses (one per IPv6
    /64 at most, see client_key) can't grow them without bound."""

    def __init__(self, *args, max_tracked: int = 10000, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_tracked = max_tracked

    def fail(self, ip: str = "") -> float:
        delay = super().fail(ip)
        with self.lock:
            if len(self.failures) > self.max_tracked:
                # addresses that aren't locked out go first
                now = self.clock()
                for key in [k for k in self.failures
                            if self.locked_until.get(k, 0) <= now]:
                    if len(self.failures) <= self.max_tracked:
                        break
                    del self.failures[key]
            for table in (self.failures, self.locked_until):
                if len(table) > self.max_tracked:
                    _cap(table, self.max_tracked)
        return delay

    def retry_after(self, ip: str) -> int:
        """Seconds until `ip`'s lockout ends; 0 if it isn't locked."""
        with self.lock:
            return max(0, math.ceil(self.locked_until.get(ip, 0.0) - self.clock()))


def _cap(d: dict, size: int) -> None:
    """Drop the oldest entries (insertion order) until `d` has `size` left:
    memory stays bounded however many addresses a flood uses."""
    for key in list(d)[:max(0, len(d) - size)]:
        del d[key]


def client_key(ip: str) -> str:
    """What limits are counted by: the address, or its /64 for IPv6 (one
    machine can rotate through a whole /64)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class Slots:
    """Requests in flight (reading a body, or held after a wrong token):
    at most `per_key` per address (IPv6: per /56, so one network can't
    spread over many /64s) and `total` in all."""

    def __init__(self, per_key: int, total: int):
        self.per_key = per_key
        self.total = total
        self.busy: dict[str, int] = {}
        self.count = 0

    @staticmethod
    def key(ip: str) -> str:
        if ip.endswith("/64"):  # client_key's IPv6 form
            return str(ipaddress.ip_network(ip, strict=False).supernet(new_prefix=56))
        return ip

    def take(self, ip: str, limit: int | None = None) -> bool:
        """Claim a slot for `ip`; False if none is free."""
        key = self.key(ip)
        per_key = self.per_key if limit is None else min(limit, self.per_key)
        if self.busy.get(key, 0) >= per_key or self.count >= self.total:
            return False
        self.busy[key] = self.busy.get(key, 0) + 1
        self.count += 1
        return True

    def release(self, ip: str) -> None:
        key = self.key(ip)
        self.count -= 1
        if self.busy.get(key, 0) <= 1:
            self.busy.pop(key, None)
        else:
            self.busy[key] -= 1


class RateLimiter:
    """At most `per_minute` requests per address in any 60 s window."""

    def __init__(self, per_minute=120, clock=time.monotonic, max_tracked=10000):
        self.per_minute = per_minute
        self.clock = clock
        self.max_tracked = max_tracked
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        with self.lock:
            now = self.clock()
            if len(self.hits) >= self.max_tracked and ip not in self.hits:
                for k in [k for k, q in self.hits.items() if not q or q[-1] <= now - 60]:
                    del self.hits[k]
                _cap(self.hits, self.max_tracked - 1)
            q = self.hits.setdefault(ip, deque())
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def parse_networks(cidrs) -> list:
    """Networks from a list, or a comma-separated string (env override)."""
    if isinstance(cidrs, str):
        cidrs = [c for c in cidrs.replace(" ", "").split(",") if c]
    return [ipaddress.ip_network(c, strict=False) for c in cidrs]


def _in_networks(host: str, networks) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks)


class CloudGuard:
    """ASGI wrapper around the web app; see the module docstring."""

    def __init__(self, app, token: str, trusted_proxies=(),
                 max_upload_bytes=4_000_000, max_body_bytes=16_384,
                 lockout: webapp.LoginLimiter | None = None,
                 limiter: RateLimiter | None = None, allowed_origins=(),
                 max_busy_per_ip=2, max_busy=32, body_timeout=10.0,
                 max_login_busy=8, login_timeout=2.0,
                 min_upload_rate=65536, sleep=None, client_ip_header="",
                 log_proxy_headers=5, web=None, authority=None,
                 failover_role: str | None = None):
        import asyncio

        self.app = app
        self.token = token
        # Requests reading a body (or held after a wrong token), per address
        # and in all; a slow client can't tie up more than that.
        # Two pools, so callers without the token (sign-in attempts, wrong
        # Bearer tokens) can never use up the slots of signed-in ones.
        self.slots = Slots(max_busy_per_ip, max_busy)
        self.login_slots = Slots(max_busy_per_ip, max_login_busy)
        # failover voice sockets waiting for their first-message token
        self.preauth_slots = Slots(max_busy_per_ip, max_login_busy * 4)
        self.login_timeout = login_timeout  # a 16 KB sign-in body is quick
        # a body must arrive within body_timeout + size / min_upload_rate s
        self.body_timeout = body_timeout
        self.min_upload_rate = min_upload_rate
        self.sleep = sleep or asyncio.sleep
        # The web app's WebAuth (app.state.web): its session store decides
        # whether a cookie is still signed in.
        self.web = web if web is not None else getattr(getattr(app, "state", None),
                                                       "web", None)
        # e.g. "cf-connecting-ip" if the host's edge sets it; read only from
        # a trusted proxy. "" = the rightmost untrusted X-Forwarded-For hop.
        self.client_ip_header = client_ip_header.strip().lower()
        # Log what the proxy sends on the first few requests, to check
        # trusted_proxies / client_ip_header against the real host.
        self.log_proxy_headers = log_proxy_headers
        self.allowed_origins = tuple(o.rstrip("/") for o in allowed_origins)
        self.trusted = parse_networks(trusted_proxies)
        self.max_upload_bytes = max_upload_bytes
        self.max_body_bytes = max_body_bytes
        self.lockout = lockout or BoundedLoginLimiter()
        # failover (assistant/failover): token checker and this backend's role
        self.authority = authority
        self.failover_role = failover_role if authority is not None else None
        self.limiter = limiter or RateLimiter()

    # where the request really came from
    def client(self, scope) -> tuple[str, str | None]:
        """(client key for limits, scheme the proxy saw or None if not via
        a trusted proxy)."""
        peer = (scope.get("client") or ("", 0))[0] or ""
        headers = _headers(scope)
        if self.log_proxy_headers > 0:
            self.log_proxy_headers -= 1
            log.info("Proxy check: peer %s (%s), X-Forwarded-For %r, -Proto %r, "
                     "-Host %r, CF-Connecting-IP %r, True-Client-IP %r", peer,
                     "trusted" if _in_networks(peer, self.trusted) else "not trusted",
                     *(headers.get(h, "") for h in (
                         "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
                         "cf-connecting-ip", "true-client-ip")))
        if not _in_networks(peer, self.trusted):
            return client_key(peer), None  # not the proxy: ignore its headers
        proto = headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
        client = peer
        if self.client_ip_header and headers.get(self.client_ip_header):
            client = headers[self.client_ip_header].split(",")[-1].strip()
        else:
            # The proxy appends the address it saw; anything to its left
            # could be made up by the client: take the rightmost untrusted.
            for hop in reversed([h.strip() for h in
                                 headers.get("x-forwarded-for", "").split(",") if h.strip()]):
                client = hop
                if not _in_networks(hop, self.trusted):
                    break
        return client_key(client), (proto or None)

    def from_proxy(self, scope) -> bool:
        return _in_networks((scope.get("client") or ("", 0))[0] or "", self.trusted)

    def scrub(self, scope):
        """Drop X-Forwarded-Host unless the proxy sent it: the web app's
        same-site checks accept it as the site's own name."""
        if self.from_proxy(scope):
            return scope
        return {**scope, "headers": [(k, v) for k, v in scope.get("headers", [])
                                     if k.lower() != b"x-forwarded-host"]}

    def same_site(self, scope) -> bool:
        """The WebSocket's Origin names this site (browsers always send one)."""
        headers = _headers(scope)
        origin = headers.get("origin", "").rstrip("/")
        if not origin:
            return False
        if origin in self.allowed_origins:
            return True
        scheme, _, host = origin.partition("://")
        own = {headers.get("host", "").lower()}
        if self.from_proxy(scope) and headers.get("x-forwarded-host"):
            own.add(headers["x-forwarded-host"].split(",")[-1].strip().lower())
        return scheme in ("https", "http") and host.lower() in own - {""}

    async def websocket(self, scope, receive, send):
        """Checks before the handshake; a refusal closes with 1008 (the
        server answers the handshake with 403)."""
        ip, _ = self.client(scope)

        async def refuse(why: str):
            log.warning("Refused voice socket from %s: %s", ip, why)
            await send({"type": "websocket.close", "code": 1008})

        if scope["path"] not in WEBSOCKETS:
            return await refuse("unknown path")
        if not self.limiter.allow(ip):
            return await refuse("too many requests")
        if not self.same_site(scope):
            return await refuse("origin is not this site")
        bearer = _bearer(scope)
        if bearer is not None and self.derived(bearer):
            if self.authority.verify(bearer, scope="app") is None:
                return await refuse("bad access token")
        elif bearer is not None:
            if not webapp.tokens_match(bearer, self.token):
                if not self.login_slots.take(ip):
                    return await refuse("too many requests at once")
                try:
                    await self.wrong_token(ip, "bearer token")
                finally:
                    self.login_slots.release(ip)
                return await refuse("wrong bearer token")
        elif not self.signed_in(scope):
            if not self.failover_role:
                return await refuse("not signed in")
            # With failover, a socket with no cookie sends its token as the
            # first message (the failover layer closes it otherwise, within
            # ws_auth_timeout). Until then it holds one of a few pre-auth
            # slots, so unauthenticated sockets can't pile up.
            if not self.preauth_slots.take(ip):
                return await refuse("too many unauthenticated sockets")
            released = False

            def release():
                nonlocal released
                if not released:
                    released = True
                    self.preauth_slots.release(ip)

            async def first_receive():
                message = await receive()
                if message["type"] != "websocket.connect":
                    release()  # the auth message (or a disconnect) arrived
                return message

            async def watched_send(message):
                if message["type"] == "websocket.close":
                    release()
                await send(message)

            try:
                return await self.app(self.scrub(scope), first_receive, watched_send)
            finally:
                release()
        await self.app(self.scrub(scope), receive, send)

    def derived(self, bearer: str) -> bool:
        """A failover access token (hc1....), not WEB_TOKEN itself."""
        return self.failover_role is not None and bearer.startswith("hc1.")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            return await self.websocket(scope, receive, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)  # lifespan
        scope = self.scrub(scope)
        path, method = scope["path"], scope["method"]
        if path == HEALTH and method in ("GET", "HEAD"):
            # Before any limit. The version lets open pages reload after a
            # deploy (update-watch.js); nothing else (no "busy": that is for
            # a supervisor on the same machine, and there is none here).
            from assistant.version import version

            health = {"ok": True, "version": version()}
            if self.failover_role:
                from assistant.failover import API_VERSION

                health.update(api=API_VERSION, role=self.failover_role)
            return await _json(send, 200, health)
        ip, proto = self.client(scope)
        extra = {"Strict-Transport-Security": HSTS} if proto == "https" else {}
        if proto == "http":
            host = _headers(scope).get("host", "")
            if host and all(c.isalnum() or c in ".-:" for c in host):
                query = scope.get("query_string", b"").decode("latin-1")
                location = f"https://{host}{path}" + (f"?{query}" if query else "")
                return await _respond(send, 308, b"", "text/plain",
                                      {"Location": location})
            return await _json(send, 400, {"error": "use https"})
        if not self.limiter.allow(ip):
            return await _json(send, 429, {"error": "too many requests"},
                               {"Retry-After": "60", **extra})
        public_get = PUBLIC_GET | (FAILOVER_PUBLIC_GET if self.failover_role else set())
        public_post = PUBLIC_POST | (FAILOVER_PUBLIC_POST if self.failover_role else set())
        public = ((method in ("GET", "HEAD") and path in public_get)
                  or (method == "POST" and path in public_post))
        bearer = _bearer(scope)
        login = method == "POST" and path in public_post and bearer is None
        if bearer is not None and self.derived(bearer):
            # checked here; the failover layer checks scope (app / sync)
            need = "sync" if path.startswith("/api/sync/") else "app"
            if self.authority.verify(bearer, scope=need) is None:
                return await _json(send, 401, {"error": "not signed in"}, extra)
            bearer = None
            public = True  # signed in with an access token
        # Who it is comes first: nothing reads a body for an unknown caller
        # except the login call (16 KB at most).
        if bearer is not None:
            if not webapp.tokens_match(bearer, self.token):
                return await self.refuse_token(send, ip, "bearer token", extra)
        elif not public and not self.signed_in(scope):
            return await _json(send, 401, {"error": "not signed in"}, extra)
        has_body = method not in ("GET", "HEAD", "OPTIONS") or \
            _headers(scope).get("content-length", "0") != "0"
        if not has_body:
            body = None  # nothing read: the app gets the server's receive
        else:
            pool = self.login_slots if login else self.slots
            # a locked-out address may still sign in (right token), one try at a time
            if not pool.take(ip, limit=1 if login and self.lockout.blocked(ip) else None):
                return await _json(send, 429, {"error": "too many requests at once"},
                                   {"Retry-After": "5", **extra})
            try:
                limit = (self.max_upload_bytes if path in UPLOAD_PATHS
                         else self.max_body_bytes)
                timeout = (self.login_timeout if login else
                           self.body_timeout + limit / self.min_upload_rate)
                body, error = await _read_body(scope, receive, limit, timeout)
                if error:
                    return await _json(send, *error, extra)
                if login and not webapp.tokens_match(_login_token(body), self.token):
                    return await self.refuse_token(send, ip, "token", extra,
                                                   held=True)
            finally:
                pool.release(ip)
        if body is not None:
            # the body is complete now: pass it on with its real length
            # (a chunked upload under the cap is fine)
            headers = [(k, v) for k, v in scope.get("headers", [])
                       if k.lower() not in (b"content-length", b"transfer-encoding")]
            scope = {**scope, "headers": headers + [
                (b"content-length", str(len(body)).encode())]}
        status = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                for k, v in {**webapp.SECURITY_HEADERS, **extra}.items():
                    if k.lower().encode() not in names:
                        headers.append((k.lower().encode(), v.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive if body is None else _replay(body, receive),
                           send_wrapper)
        except Exception:
            log.exception("Unhandled error in %s %s", method, path)
            if "code" not in status:
                return await _json(send, 500, {"error": "internal error"}, extra)
            return

    async def wrong_token(self, ip: str, what: str) -> bool:
        """A wrong token, as the web app treats it: from a locked-out
        address it is refused at once (True), otherwise it is counted and
        its answer held as long as the limiter says."""
        log.warning("Wrong %s from %s", what, ip)
        if self.lockout.blocked(ip):
            return True
        delay = self.lockout.fail(ip)
        if delay:
            await self.sleep(delay)
        return False

    async def refuse_token(self, send, ip: str, what: str, extra, held=False):
        if not held:
            if not self.login_slots.take(ip):
                return await _json(send, 429, {"error": "too many requests at once"},
                                   {"Retry-After": "5", **extra})
        try:
            locked = await self.wrong_token(ip, what)
        finally:
            if not held:
                self.login_slots.release(ip)
        if locked:
            return await _json(send, 429, {"error": "too many attempts; try again later"},
                               {"Retry-After": str(self.lockout.retry_after(ip)), **extra})
        return await _json(send, 401, {"error": "sign-in failed"}, extra)

    def signed_in(self, scope) -> bool:
        from http.cookies import CookieError, SimpleCookie

        raw = _headers(scope).get("cookie", "")
        try:
            jar = SimpleCookie(raw)
        except CookieError:
            return False
        morsel = jar.get(webapp.COOKIE)
        value = morsel.value if morsel else None
        store = getattr(self.web, "store", None)
        if store is None:  # not the web app (tests)
            return webapp.read_cookie(self.token, value) is not None
        return store.valid(webapp.read_cookie(self.token, value, store.epoch))


def _bearer(scope) -> str | None:
    auth = _headers(scope).get("authorization", "")
    return auth[7:] if auth[:7].lower() == "bearer " else None


def _headers(scope) -> dict[str, str]:
    """Request headers, lower-cased; repeated ones joined with commas."""
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        name, value = k.decode("latin-1").lower(), v.decode("latin-1")
        out[name] = f"{out[name]},{value}" if name in out else value
    return out


def _login_token(body: bytes) -> str:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return ""
    token = data.get("token", "") if isinstance(data, dict) else ""
    return token if isinstance(token, str) else ""


async def _read_body(scope, receive, limit: int, timeout: float | None = None):
    """The whole body, or (status, payload) if it is too large, too slow
    (408 after `timeout` seconds) or malformed."""
    import asyncio

    try:
        return await asyncio.wait_for(_read_all(scope, receive, limit), timeout)
    except asyncio.TimeoutError:
        return None, (408, {"error": "request took too long"})


async def _read_all(scope, receive, limit: int):
    length = _headers(scope).get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                return None, (413, {"error": "request too large"})
        except ValueError:
            return None, (400, {"error": "bad request"})
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None, (400, {"error": "bad request"})
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None, (413, {"error": "request too large"})
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks), None


def _replay(body: bytes, receive):
    """The body already read, then the server's own receive (which reports
    the client disconnecting, so an abandoned stream stops)."""
    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _respond(send, status: int, body: bytes, ctype: str, headers=None):
    hdrs = {"Content-Type": ctype, "Content-Length": str(len(body)),
            "Cache-Control": "no-store", **webapp.SECURITY_HEADERS,
            **(headers or {})}
    await send({"type": "http.response.start", "status": status,
                "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()]})
    await send({"type": "http.response.body", "body": body})


async def _json(send, status: int, payload: dict, headers=None):
    await _respond(send, status, json.dumps(payload).encode(), "application/json",
                   headers)


# --- startup ------------------------------------------------------------------

def cloud_token(environ=os.environ) -> str:
    """WEB_TOKEN from the environment; a server never generates or saves one."""
    token = environ.get(webapp.TOKEN_ENV, "").strip()
    if len(token) < webapp.MIN_TOKEN_LENGTH:
        raise ValueError(f"{webapp.TOKEN_ENV} must be set to at least "
                         f"{webapp.MIN_TOKEN_LENGTH} random characters (the "
                         "Render Blueprint generates one)")
    return token


def build_app(config: dict, web_assistant, token: str):
    web, cloud = config["webapp"], config["cloud"]
    max_upload = int(web["max_upload_mb"] * 1_000_000)
    # The web app trusts forwarded headers from the same networks as the
    # guard, so its own checks (e.g. the voice socket's Origin against
    # X-Forwarded-Host) agree with it.
    trust = ProxyNetworks(hops=1, networks=tuple(parse_networks(cloud["trusted_proxies"])))
    limiter = BoundedLoginLimiter(per_ip=cloud["login_max_failures"],
                                  window=cloud["login_window"],
                                  lockout=cloud["login_lockout"],
                                  global_free=cloud["login_global_free"],
                                  global_delay=cloud["login_global_delay"])
    from assistant.failover.peers import settings_from_config
    from assistant.failover.tokens import TokenAuthority

    failover = settings_from_config(config)
    app = webapp.create_app(web_assistant, token,
                            allowed_origins=web["allowed_origins"],
                            max_upload_bytes=max_upload,
                            max_audio_seconds=float(web["max_audio_seconds"]),
                            trusted_proxy=trust, limiter=limiter,
                            sessions_file=web["sessions_file"] or None,
                            voice=config.get("voice"), failover=failover)
    return CloudGuard(
        app, token, trusted_proxies=cloud["trusted_proxies"],
        max_upload_bytes=max_upload, max_body_bytes=int(cloud["max_body_kb"] * 1024),
        lockout=limiter, web=_web_auth(app),
        authority=TokenAuthority(token) if failover.role else None,
        failover_role=failover.role or None,
        limiter=RateLimiter(cloud["requests_per_minute"]),
        allowed_origins=web["allowed_origins"],
        max_busy_per_ip=cloud["uploads_per_address"], max_busy=cloud["uploads_total"],
        body_timeout=cloud["body_timeout"], max_login_busy=cloud["logins_total"],
        client_ip_header=cloud["client_ip_header"])


def _web_auth(app):
    """The web app's WebAuth. Without it the guard couldn't check cookies
    against the session store (every request would get 401), so fail now."""
    web = getattr(getattr(app, "state", None), "web", None)
    if web is None or not hasattr(web, "store"):
        raise RuntimeError("CloudGuard needs the web app's WebAuth (app.state.web); "
                           "if create_app now wraps the app, expose its state")
    return web


def warm_up(web_assistant) -> None:
    """Load Whisper and the voice now, not on Kyle's first question."""
    def run():
        try:
            from assistant.tts import PiperTTS

            _ = web_assistant.transcriber.model
            if isinstance(web_assistant.tts, PiperTTS):
                _ = web_assistant.tts.voice
            log.info("Speech models loaded")
        except Exception:
            log.exception("Loading speech models failed (will retry on first use)")

    threading.Thread(target=run, name="warm-up", daemon=True).start()


# No uvicorn limit_concurrency: it counts every open connection and answers
# 503 to all, /healthz included, once a slow client holds enough of them.
# CloudGuard limits what each address can hold instead.
UVICORN_OPTIONS = {"proxy_headers": False, "server_header": False,
                   "timeout_keep_alive": 30, "ws": "auto", "ws_max_size": WS_MAX_SIZE}


def serve(config: dict, port: int | None = None, tts_name: str | None = None,
          output_fn=print) -> int:
    import uvicorn

    token = cloud_token()
    host = config["webapp"]["host"]
    port = int(port or os.environ.get("PORT") or config["webapp"]["port"])
    web_assistant = webapp.build_assistant(config, tts_name, output_fn)
    app = build_app(config, web_assistant, token)
    warm_up(web_assistant)
    logging.getLogger("uvicorn.access").addFilter(
        lambda record: HEALTH not in record.getMessage())  # every few seconds
    log.info("Cloud web app listening on %s:%s", host, port)
    # proxy_headers off: CloudGuard decides which forwarded headers to trust.
    uvicorn.run(app, host=host, port=port, log_config=None, **UVICORN_OPTIONS)
    return 0
