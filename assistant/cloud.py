"""The phone web app on a public cloud server (ASSISTANT_PROFILE=server).

`python -m assistant --mode web` in the Docker image runs the same app as
on the PC (assistant/webapp.py, which has its own headers, generic errors,
cross-site and size checks), wrapped in `CloudGuard`, an outer ASGI layer
for the public internet with no Tailscale in front:

- GET /healthz answers "ok" for the host's health check, and nothing else.
- Every other route needs the session cookie (or `Authorization: Bearer
  <WEB_TOKEN>`), except the login page and its static files, the login call
  itself, and the PWA manifest and icons. Unknown paths answer 401 too.
- Wrong tokens lock out the address they came from (5 per 15 minutes, then
  15 minutes, doubling up to a day), with a global cap across addresses.
  Signed-in phones keep working during a lockout: it only stops new logins.
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
PUBLIC_GET = {"/", "/app.js", "/app.css", "/sw.js", "/manifest.webmanifest",
              "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png",
              "/voice.js", "/voice-core.js", "/voice-worklet.js", "/voice.css",
              "/static/vad/ort.wasm.min.js", "/static/vad/ort-wasm-simd-threaded.mjs",
              "/static/vad/ort-wasm-simd-threaded.wasm",
              "/static/vad/silero_vad_v5.onnx"}
PUBLIC_POST = {"/api/login"}
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


class NeverBlock:
    """Hands login limiting to CloudGuard, whose lockouts escalate (15 min
    doubling to a day) and also cover unknown paths and the WebSocket."""

    def blocked(self, ip: str = "") -> bool:
        return False

    def fail(self, ip: str = "") -> None:
        pass


class LoginLockout:
    """Wrong-token lockouts per client address, plus a global cap."""

    def __init__(self, max_failures=5, window=900, lockout=900,
                 global_max_failures=30, max_lockout=86400, clock=time.monotonic,
                 max_tracked=10000):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self.max_lockout = max_lockout
        self.global_max_failures = global_max_failures
        self.clock = clock
        self.max_tracked = max_tracked
        self.failures: dict[str, deque] = {}
        self.locked_until: dict[str, float] = {}
        self.strikes: dict[str, int] = {}  # lockouts so far, for doubling
        self.global_failures: deque = deque()
        self.global_locked_until = 0.0
        self.lock = threading.Lock()

    def _prune(self, q: deque, now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def retry_after(self, ip: str) -> int:
        """Seconds until `ip` may try again; 0 if it may now."""
        with self.lock:
            now = self.clock()
            until = max(self.locked_until.get(ip, 0.0), self.global_locked_until)
            return max(0, math.ceil(until - now))

    def fail(self, ip: str) -> None:
        with self.lock:
            now = self.clock()
            if len(self.failures) >= self.max_tracked and ip not in self.failures:
                self._forget_idle(now)
            q = self.failures.setdefault(ip, deque())
            self._prune(q, now)
            q.append(now)
            if len(q) >= self.max_failures:
                strikes = self.strikes.get(ip, 0)
                self.strikes[ip] = strikes + 1
                duration = min(self.lockout * 2 ** strikes, self.max_lockout)
                self.locked_until[ip] = now + duration
                q.clear()
                log.warning("Login locked for %s for %d s after %d wrong tokens",
                            ip, duration, self.max_failures)
            self._prune(self.global_failures, now)
            self.global_failures.append(now)
            if len(self.global_failures) >= self.global_max_failures:
                self.global_locked_until = now + self.lockout
                self.global_failures.clear()
                log.warning("All new logins locked for %d s: %d wrong tokens "
                            "from all addresses", self.lockout,
                            self.global_max_failures)

    def succeed(self, ip: str) -> None:
        with self.lock:
            self.failures.pop(ip, None)
            self.strikes.pop(ip, None)

    def _forget_idle(self, now: float) -> None:
        for ip in [ip for ip, q in self.failures.items()
                   if (not q or q[-1] <= now - self.window)
                   and self.locked_until.get(ip, 0) <= now]:
            self.failures.pop(ip, None)
            self.locked_until.pop(ip, None)


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
            q = self.hits.setdefault(ip, deque())
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def parse_networks(cidrs) -> list:
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
                 lockout: LoginLockout | None = None,
                 limiter: RateLimiter | None = None, allowed_origins=()):
        self.app = app
        self.token = token
        self.allowed_origins = tuple(o.rstrip("/") for o in allowed_origins)
        self.trusted = parse_networks(trusted_proxies)
        self.max_upload_bytes = max_upload_bytes
        self.max_body_bytes = max_body_bytes
        self.lockout = lockout or LoginLockout()
        self.limiter = limiter or RateLimiter()

    # where the request really came from
    def client(self, scope) -> tuple[str, str | None]:
        """(client address, scheme the proxy saw or None if not via proxy)."""
        peer = (scope.get("client") or ("", 0))[0] or ""
        if not _in_networks(peer, self.trusted):
            return peer, None  # direct connection: ignore forwarded headers
        headers = _headers(scope)
        proto = headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
        client = peer
        # The proxy appends the address it saw; anything to its left could
        # be made up by the client. Take the rightmost untrusted entry.
        for hop in reversed([h.strip() for h in
                             headers.get("x-forwarded-for", "").split(",") if h.strip()]):
            client = hop
            if not _in_networks(hop, self.trusted):
                break
        return client, (proto or None)

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
        if bearer is not None:
            if self.lockout.retry_after(ip):
                return await refuse("locked out")
            if not webapp.tokens_match(bearer, self.token):
                self.lockout.fail(ip)
                return await refuse("wrong bearer token")
            self.lockout.succeed(ip)
        elif not self.signed_in(scope):
            return await refuse("not signed in")
        await self.app(self.scrub(scope), receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            return await self.websocket(scope, receive, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)  # lifespan
        scope = self.scrub(scope)
        path, method = scope["path"], scope["method"]
        if path == HEALTH and method in ("GET", "HEAD"):
            return await _respond(send, 200, b"ok", "text/plain")
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
        public = ((method in ("GET", "HEAD") and path in PUBLIC_GET)
                  or (method == "POST" and path in PUBLIC_POST))
        bearer = _bearer(scope)
        login = (method == "POST" and path == "/api/login") or bearer is not None
        if login:
            wait = self.lockout.retry_after(ip)
            if wait:
                return await _json(send, 429, {"error": "too many attempts; "
                                               "try again later"},
                                   {"Retry-After": str(wait), **extra})
        if bearer is not None:
            if not webapp.tokens_match(bearer, self.token):
                self.lockout.fail(ip)
                log.warning("Wrong bearer token from %s", ip)
                return await _json(send, 401, {"error": "not signed in"}, extra)
            self.lockout.succeed(ip)
            login = False
        elif not public and not self.signed_in(scope):
            return await _json(send, 401, {"error": "not signed in"}, extra)
        limit = (self.max_upload_bytes if path in UPLOAD_PATHS
                 else self.max_body_bytes)
        body, error = await _read_body(scope, receive, limit)
        if error:
            return await _json(send, *error, extra)
        if method not in ("GET", "HEAD") or body:
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
            await self.app(scope, _replay(body, receive), send_wrapper)
        except Exception:
            log.exception("Unhandled error in %s %s", method, path)
            if "code" not in status:
                return await _json(send, 500, {"error": "internal error"}, extra)
            return
        if login:
            if status.get("code") == 401:
                self.lockout.fail(ip)
                log.warning("Wrong web token from %s", ip)
            elif status.get("code") == 200:
                self.lockout.succeed(ip)

    def signed_in(self, scope) -> bool:
        from http.cookies import CookieError, SimpleCookie

        raw = _headers(scope).get("cookie", "")
        try:
            jar = SimpleCookie(raw)
        except CookieError:
            return False
        morsel = jar.get(webapp.COOKIE)
        return webapp.read_cookie(self.token, morsel.value if morsel else None) is not None


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


async def _read_body(scope, receive, limit: int):
    """The whole body, or (status, payload) if it is too large/malformed."""
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
    app = webapp.create_app(web_assistant, token,
                            allowed_origins=web["allowed_origins"],
                            max_upload_bytes=max_upload,
                            max_audio_seconds=float(web["max_audio_seconds"]),
                            trusted_proxy=trust, limiter=NeverBlock(),
                            voice=config.get("voice"))
    return CloudGuard(
        app, token, trusted_proxies=cloud["trusted_proxies"],
        max_upload_bytes=max_upload, max_body_bytes=int(cloud["max_body_kb"] * 1024),
        lockout=LoginLockout(cloud["login_max_failures"], cloud["login_window"],
                             cloud["login_lockout"],
                             cloud["login_global_max_failures"]),
        limiter=RateLimiter(cloud["requests_per_minute"]),
        allowed_origins=web["allowed_origins"])


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
    uvicorn.run(app, host=host, port=port, log_config=None, proxy_headers=False,
                server_header=False, timeout_keep_alive=30, limit_concurrency=64,
                ws="auto", ws_max_size=WS_MAX_SIZE)
    return 0
