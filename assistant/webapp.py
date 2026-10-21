"""Private phone web app: python -m assistant --mode web.

A small FastAPI server with one mobile page (a PWA) and a JSON API. The
phone records with its own microphone and uploads the clip; the server
transcribes it with the same Whisper model as the voice loop, answers with
the same ClaudeChat (tools, memory), and renders the reply with the same TTS
engine. Each browser gets its own conversation, which ends (and is summarized
to memory, as in the voice loop) after `conversation_timeout` seconds idle.

A turn runs in a worker thread and reports what happens as NDJSON events
(transcript, tool, confirm, reply, error, done). When a tool needs a yes
(e.g. deleting an event), the stream ends with a "confirm" event; the page
answers with POST /api/confirm (buttons, or a spoken/typed "yes"), and that
response carries the rest of the turn.

Privacy: binds to 127.0.0.1; every API call needs the session cookie, which
is only given for the WEB_TOKEN from .env (generated on first run, never
logged). Reach it from the phone through `tailscale serve` (see README). It
is also hardened for a public server behind a TLS proxy (`trusted_proxy`):
per-address and global login lockouts, upload size/length limits, generic
errors, strict headers, and an unauthenticated /healthz that says nothing.

Reusable pieces (for other routes, e.g. a WebSocket voice mode):
  WebAssistant.transcribe_upload(bytes, language=None) -> text
  WebAssistant.chat_stream(session, text, cancel) -> iterator of text deltas
  WebAssistant.synthesize(text, session=None, language=None) -> WAV bytes
  (in that person's picked voice, voice_picker.py, else their
  language's voice, e.g. a Chinese-speaking family member's; None =
  the server's)
  app.state.web.session_for(connection) -> Session | None (HTTP or WebSocket)
  app.state.web.cross_origin(connection) -> bool
  require_session (FastAPI dependency; 401, or close 1008 on a WebSocket)
"""

import asyncio
import hashlib
import hmac
import io
import ipaddress
import itertools
import json
import logging
import os
import queue
import re
import secrets
import struct
import tempfile
import threading
import time
import zlib
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import numpy as np
from starlette.requests import HTTPConnection

from assistant import audio, i18n, usage
from assistant.attachments import MAX_ATTACHMENTS
from assistant.chat import is_user_turn
from assistant.tools import READ_ONLY_TOOLS, ToolRegistry

log = logging.getLogger(__name__)

# A turn's form: the recording plus attachments; one extra so "too many"
# gets its own message (attachments.prepare_all)
MAX_FORM_FILES = 1 + MAX_ATTACHMENTS + 1
# Claude's 400 reasons that mean a file is too long ("A maximum of 100 PDF
# pages may be provided", "prompt is too long: 250000 tokens > 200000
# maximum"); any other 400 on a turn with files: the file couldn't be read
FILE_TOO_LONG = re.compile(r"\bpages?\b|too long|\btokens?\b", re.IGNORECASE)

STATIC = Path(__file__).resolve().parent / "web"
COOKIE = "hc_session"
COOKIE_MAX_AGE = 90 * 24 * 3600  # renewed on every authenticated API call
TOKEN_ENV = "WEB_TOKEN"
MIN_TOKEN_LENGTH = 32
# Tools that act on the PC itself make no sense from the phone.
PC_ONLY_TOOLS = {"open_in_browser"}

# 'wasm-unsafe-eval': the voice view runs the Silero VAD in WebAssembly.
EARCONS = ("listening", "thinking", "ended")  # assistant/earcons.py
CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
       "img-src 'self' data:; font-src 'self'; media-src 'self' blob:; connect-src 'self'; "
       "manifest-src 'self'; worker-src 'self'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


# --- access token -----------------------------------------------------------

def ensure_web_token(env_file: str | Path = ".env", environ=os.environ,
                     output_fn=print) -> str:
    """WEB_TOKEN from the environment, or a new one appended to .env.

    The value is never printed or logged; only that it was created."""
    token = environ.get(TOKEN_ENV, "").strip()
    if token:
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"{TOKEN_ENV} in .env is too short (use at least "
                             f"{MIN_TOKEN_LENGTH} random characters, or delete "
                             "it to have one generated)")
        return token
    token = secrets.token_urlsafe(32)  # 43 characters, 256 bits
    path = Path(env_file)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{prefix}# Phone web app access token (python -m assistant "
                f"--mode web)\n{TOKEN_ENV}={token}\n")
    if not existing:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    environ[TOKEN_ENV] = token
    output_fn(f"Created a new web access token ({TOKEN_ENV} in {path}). "
              "Run with --show-token to print it for your phone.")
    return token


def _sign(token: str, sid: str) -> str:
    return hmac.new(token.encode(), f"hc-web-session:{sid}".encode(),
                    hashlib.sha256).hexdigest()


def make_cookie(token: str, sid: str, epoch: str = "") -> str:
    return f"{sid}.{_sign(token, f'{epoch}:{sid}')}"


def read_cookie(token: str, value: str | None, epoch: str = "") -> str | None:
    """The session id in a correctly signed cookie, else None. (Whether that
    session is still signed in is SessionStore's call.)"""
    if not value or "." not in value:
        return None
    sid, sig = value.rsplit(".", 1)
    if sid and hmac.compare_digest(sig, _sign(token, f"{epoch}:{sid}")):
        return sid
    return None


# SessionStore.user() of a session that isn't listed (signed out, removed or
# expired a moment ago): never the owner, and no family member either
GONE = "!gone"


class SessionStore:
    """Signed-in browsers, kept on the server so a cookie can be revoked.

    A cookie is valid only while its session id is listed here: signing out
    removes that one, "sign out everywhere" clears the list and rotates the
    epoch that every cookie signature includes. Sessions unused for
    `max_age` seconds expire. With `path` the list survives restarts (a small
    JSON file, rewritten on sign-in/out and at most daily on use); without it
    a restart signs everyone out.

    A session made with `create(user_id)` belongs to that family member
    (family.py); all others are the owner's."""

    def __init__(self, path: str | Path | None = None,
                 max_age: float = COOKIE_MAX_AGE, max_sessions: int = 100,
                 clock=time.time):
        self.path = Path(path) if path else None
        self.max_age = max_age
        self.max_sessions = max_sessions
        self.clock = clock
        self.lock = threading.Lock()
        self.epoch = secrets.token_hex(8)
        self.sessions: dict[str, float] = {}  # sid -> last used (wall clock)
        self.users: dict[str, str] = {}  # sid -> family user id (absent = owner)
        self._saved_at: dict[str, float] = {}
        self._unsaved = False  # a save failed; retry before trusting the file
        # failover access tokens signed in at or before this unix time are
        # refused ("Sign out everywhere"); kept here so a restart remembers
        self.tokens_not_before = 0
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.epoch = str(data["epoch"])
            self.sessions = {str(k): float(v) for k, v in data["sessions"].items()}
            self._saved_at = dict(self.sessions)
            self.users = {str(k): str(v) for k, v in data.get("users", {}).items()
                          if str(k) in self.sessions}
            self.tokens_not_before = int(data.get("tokens_not_before", 0))
        except Exception as e:  # unreadable: start over (everyone signs in again)
            log.warning("Ignoring unreadable web session file %s: %s", self.path, e)
            # fail closed for access tokens too: a lost "Sign out everywhere"
            # must not bring revoked phones back
            self.tokens_not_before = int(time.time())

    def _save(self) -> None:
        if not self.path:
            return
        from assistant.tools.google_auth import write_private

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # private and atomic (a unique temp file, retried on Windows)
            write_private(self.path, json.dumps({
                "epoch": self.epoch, "sessions": self.sessions,
                "users": self.users,
                "tokens_not_before": self.tokens_not_before}))
            self._saved_at = dict(self.sessions)
            if self._unsaved:
                log.warning("Web sessions saved to %s again", self.path)
            self._unsaved = False
        except OSError as e:
            # A sign-out not on disk would come back after a restart: say so
            # loudly and retry on every request until it is saved.
            log.error("Could not save web sessions to %s (%s); a sign-out may "
                      "be undone by a restart until this works", self.path, e)
            self._unsaved = True

    def create(self, user_id: str | None = None) -> str:
        """A new signed-in session: the owner's, or `user_id`'s (family)."""
        sid = secrets.token_urlsafe(16)
        with self.lock:
            self.sessions[sid] = self.clock()
            if user_id is not None:
                self.users[sid] = user_id
            while len(self.sessions) > self.max_sessions:
                oldest = min(self.sessions, key=self.sessions.get)
                self._drop(oldest)
            self._save()
        return sid

    def _drop(self, sid: str) -> bool:
        self.users.pop(sid, None)
        return self.sessions.pop(sid, None) is not None

    def user(self, sid: str | None) -> str | None:
        """The family member this session belongs to; None = the owner's;
        GONE for one that isn't listed (e.g. removed between the cookie
        check and this call), so it fails closed instead of counting as
        the owner's. Bearer and access-token sessions: WebAuth.user_of."""
        with self.lock:
            if sid and sid in self.users:
                return self.users[sid]
            return None if sid and sid in self.sessions else GONE

    def revoke_user(self, user_id: str) -> list[str]:
        """Sign out every session of one family member; returns their ids."""
        with self.lock:
            sids = [sid for sid, uid in self.users.items() if uid == user_id]
            for sid in sids:
                self._drop(sid)
            if sids:
                self._save()
        return sids

    def valid(self, sid: str | None) -> bool:
        """Is this session signed in? Counts as use (pushes back expiry)."""
        if not sid:
            return False
        now = self.clock()
        with self.lock:
            if self._unsaved:
                self._save()
            last = self.sessions.get(sid)
            if last is None:
                return False
            if now - last > self.max_age:
                self._drop(sid)
                self._save()
                return False
            self.sessions[sid] = now
            if now - self._saved_at.get(sid, 0) > 86400:
                self._save()
            return True

    def listed(self, sid: str) -> bool:
        """Still signed in? (A read-only check, e.g. for a live WebSocket.)"""
        with self.lock:
            last = self.sessions.get(sid)
        return last is not None and self.clock() - last <= self.max_age

    def revoke(self, sid: str) -> None:
        with self.lock:
            if self._drop(sid):
                self._save()

    def revoke_tokens(self, not_before: int) -> None:
        """Remember a "Sign out everywhere" for access tokens (persisted)."""
        with self.lock:
            self.tokens_not_before = max(self.tokens_not_before, int(not_before))
            self._save()

    def revoke_all(self) -> list[str]:
        """Sign out every one of the owner's browsers; returns their ids.
        Family members stay signed in (they have no token to sign in again
        with; remove them to cut them off). The cookie epoch is rotated
        when nobody else is left signed in."""
        with self.lock:
            sids = [sid for sid in self.sessions if sid not in self.users]
            for sid in sids:
                self._drop(sid)
            if not self.sessions:
                self.epoch = secrets.token_hex(8)
            self._save()
        return sids


class LoginLimiter:
    """Limits on WRONG tokens (the right token is always accepted, so
    nobody can lock Kyle out by guessing).

    - Per client address: `per_ip` wrong tokens within `window` seconds lock
      that address's further wrong guesses out for `lockout` seconds.
    - Everyone together: past `global_free` wrong tokens within `window`,
      each further wrong guess waits `global_delay` seconds before its answer
      (a slowdown, not a lockout). WEB_TOKEN is 256 random bits, so guessing
      is hopeless anyway; this only keeps a flood cheap to serve.

    Behind `tailscale serve` without `trusted_proxy`, all requests share one
    address, so there the per-address limit covers everyone."""

    def __init__(self, per_ip: int = 5, window: float = 900,
                 lockout: float = 900, global_free: int = 20,
                 global_delay: float = 1.0, clock=time.monotonic):
        self.per_ip = per_ip
        self.window = window
        self.lockout = lockout
        self.global_free = global_free
        self.global_delay = global_delay
        self.clock = clock
        self.failures: dict[str, deque] = {}
        # Only the newest global_free + 1 times matter: more than global_free
        # within the window means all of those are inside it.
        self.all_failures: deque = deque(maxlen=global_free + 1)
        self.locked_until: dict[str, float] = {}
        self.lock = threading.Lock()
        self._sweep_at = 10_000  # tracked addresses before the next cleanup

    def _prune(self, q: deque, now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def blocked(self, ip: str = "") -> bool:
        """Should a wrong token from this address get "too many attempts"?"""
        with self.lock:
            return self.locked_until.get(ip, 0) > self.clock()

    def fail(self, ip: str = "") -> float:
        """Record a wrong token; returns how long to wait before answering."""
        now = self.clock()
        with self.lock:
            q = self.failures.setdefault(ip, deque(maxlen=self.per_ip))
            q.append(now)
            self.all_failures.append(now)
            self._prune(q, now)
            self._prune(self.all_failures, now)
            if len(q) >= self.per_ip and self.locked_until.get(ip, 0) <= now:
                self.locked_until[ip] = now + self.lockout
                log.warning("Web sign-in: one address locked out after %d "
                            "wrong tokens", len(q))
            if len(self.failures) > self._sweep_at:  # bound memory under a flood
                for key in [k for k, v in self.failures.items()
                            if not v or v[-1] <= now - self.window]:
                    self.failures.pop(key, None)
                for key in [k for k, t in self.locked_until.items() if t <= now]:
                    self.locked_until.pop(key, None)
                # sweep again only once it has doubled: amortized O(1)
                self._sweep_at = max(10_000, 2 * len(self.failures))
            if len(self.all_failures) > self.global_free:
                return self.global_delay
            return 0.0


@dataclass(frozen=True)
class ProxyTrust:
    """Which proxy in front of us may set X-Forwarded-* headers.

    `hops`: how many proxies add an X-Forwarded-For entry (0 = trust none).
    `addresses`: if given, the headers count only when the direct caller is
    one of them (e.g. 127.0.0.1 for `tailscale serve` on the same PC)."""

    hops: int = 0
    addresses: frozenset = frozenset()

    def __bool__(self) -> bool:
        return self.hops > 0

    def trusts(self, conn) -> bool:
        if not self.hops:
            return False
        peer = conn.client.host if conn.client else ""
        return not self.addresses or peer in self.addresses


def parse_trusted_proxy(value) -> ProxyTrust:
    """`trusted_proxy` / TRUSTED_PROXY: 0/false (none), true or a count of
    proxies, or the proxy's address(es), e.g. "127.0.0.1" or "127.0.0.1, ::1"
    (then one proxy, trusted only when the request comes from it)."""
    if isinstance(value, ProxyTrust):
        return value
    if isinstance(value, bool):
        return ProxyTrust(int(value))
    if isinstance(value, int):
        return ProxyTrust(max(0, value))
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = [v.strip() for v in str(value or "").split(",") if v.strip()]
    if not items:
        return ProxyTrust()
    if len(items) == 1:
        text = items[0].lower()
        if text in ("0", "false", "no", "off"):
            return ProxyTrust()
        if text in ("true", "yes", "on"):
            return ProxyTrust(1)
        if text.isdigit():
            return ProxyTrust(int(text))
    addresses = set()
    for item in items:
        try:
            addresses.add(str(ipaddress.ip_address(item)))
        except ValueError:
            raise ValueError(f"trusted_proxy: {item!r} is not a number of proxies "
                             "or an IP address") from None
    return ProxyTrust(1, frozenset(addresses))


def client_ip(conn, trusted_proxy=0) -> str:
    """The caller's address. X-Forwarded-For is used only behind a trusted
    proxy, and then the entry that proxy added (counting from the right),
    since anything further left can be forged by the client."""
    trust = parse_trusted_proxy(trusted_proxy)
    if trust.trusts(conn):
        hops = [h.strip() for h in conn.headers.get("x-forwarded-for", "").split(",")
                if h.strip()]
        if hops:
            return hops[-min(trust.hops, len(hops))]
    return conn.client.host if conn.client else ""


# --- request origin -----------------------------------------------------------

def is_cross_origin(headers, allowed_origins=(), trust_forwarded: bool = True) -> bool:
    """True for a browser request made by another site.

    Uses Sec-Fetch-Site when the browser sends it, else compares Origin with
    the Host (or X-Forwarded-Host, as set by a proxy, if `trust_forwarded`).
    Requests with neither header (curl, tests) pass; they still need the
    session cookie. An Origin in `allowed_origins` (e.g. the failover app
    origin) is never cross-origin."""
    if headers.get("origin") and headers.get("origin") in allowed_origins:
        return False
    site = headers.get("sec-fetch-site")
    if site in ("cross-site", "same-site"):
        return True
    if site == "same-origin":
        return False
    origin = headers.get("origin")
    if not origin:
        return False
    if origin in allowed_origins:
        return False
    host = urlsplit(origin).netloc.lower()
    hosts = [headers.get("host", "")]
    if trust_forwarded:
        hosts.append(headers.get("x-forwarded-host", ""))
    own = {h.strip().lower() for h in hosts if h.strip()}
    return host not in own


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --- uploaded audio -----------------------------------------------------------

class AudioTooLong(ValueError):
    pass


def decode_upload(data: bytes, max_seconds: float | None = None) -> np.ndarray:
    """Any recording a browser makes (WAV, WebM/Opus, MP4/AAC from iOS) as
    16 kHz mono float32. Non-WAV goes through PyAV (installed with
    faster-whisper, FFmpeg built in). Raises AudioTooLong past `max_seconds`
    (checked while decoding, so a tiny file can't expand into hours)."""
    limit = int(max_seconds * audio.SAMPLE_RATE) if max_seconds else None
    if not data:
        return np.zeros(0, dtype=np.float32)
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            samples, sr = audio.read_wav(io.BytesIO(data))
        except Exception:  # e.g. float WAV: let PyAV try
            samples = None
        if samples is not None:
            if limit and len(samples) > max_seconds * sr:
                raise AudioTooLong("recording too long")
            return audio.resample(samples, sr)
    import av  # faster-whisper's own decode_audio breaks on newer PyAV

    resampler = av.AudioResampler(format="s16", layout="mono",
                                  rate=audio.SAMPLE_RATE)
    parts, total = [], 0
    with av.open(io.BytesIO(data), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("no audio in the upload")
        for frame in container.decode(stream):
            frame.pts = None  # avoid resampler timestamp errors on odd files
            for r in resampler.resample(frame):
                parts.append(r.to_ndarray().reshape(-1))
                total += len(parts[-1])
            if limit and total > limit:
                raise AudioTooLong("recording too long")
        parts.extend(r.to_ndarray().reshape(-1) for r in resampler.resample(None))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


# --- icons (generated, so there are no binary files to ship) ------------------

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#c96442"/>
<rect x="24" y="12" width="16" height="26" rx="8" fill="#fff"/>
<path d="M17 31a15 15 0 0 0 30 0" fill="none" stroke="#fff" stroke-width="4"
 stroke-linecap="round"/>
<path d="M32 46v7M25 53h14" stroke="#fff" stroke-width="4"
 stroke-linecap="round"/>
</svg>"""


def icon_png(size: int) -> bytes:
    """The app icon (a white microphone on terracotta) as a PNG."""
    s = size / 64.0
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / s + 0.5 / s
    img = np.empty((size, size, 3), dtype=np.uint8)
    img[:] = (0xC9, 0x64, 0x42)
    # capsule: x 24..40, y 12..38, radius 8
    cy = np.clip(y, 20, 30)
    mic = (x - 32) ** 2 + (y - cy) ** 2 <= 64
    # arc: radius 15 around (32, 31), lower half, 4 wide
    r = np.hypot(x - 32, y - 31)
    arc = (np.abs(r - 15) <= 2) & (y >= 31)
    stem = (np.abs(x - 32) <= 2) & (y >= 44) & (y <= 55)
    base = (np.abs(y - 53) <= 2) & (x >= 23) & (x <= 41)
    img[mic | arc | stem | base] = 255
    raw = b"".join(b"\x00" + row.tobytes() for row in img)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


MANIFEST = {
    "name": "Hey Claude",
    "short_name": "Claude",
    "description": "Kyle's private voice assistant",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#1f1e1d",
    "theme_color": "#1f1e1d",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}


# --- conversations ------------------------------------------------------------

@dataclass
class PendingConfirm:
    question: str
    # True when asked during chat_stream(on_event=...) (the voice view):
    # only that caller may answer it, not the page's /api/confirm.
    stream: bool = False
    id: str = field(default_factory=lambda: secrets.token_urlsafe(8))
    answered: threading.Event = field(default_factory=threading.Event)
    answer: bool = False


class Session:
    """One browser's conversation and the turn that is running in it."""

    def __init__(self, sid: str, chat, registry: ToolRegistry, now: float,
                 user: str | None = None, history=None):
        self.sid = sid
        self.chat = chat
        self.registry = registry
        # None: the owner (WEB_TOKEN). Else a family member's id (family.py),
        # whose conversations use their own tools and `history` store only.
        self.user = user
        self.history = history
        self.language = getattr(chat, "language", "en")  # "en" or "zh"
        self.last_active = now
        self.events: queue.Queue = queue.Queue()
        self.busy = threading.Lock()
        self.pending: PendingConfirm | None = None
        self.thread: threading.Thread | None = None
        # While chat_stream(on_event=...) runs, that caller gets this turn's
        # tool/confirm/status events instead of the shared queue.
        self.listener: Callable[[dict], None] | None = None

    def emit(self, event: dict) -> None:
        (self.listener or self.events.put)(event)


class SessionTools(ToolRegistry):
    """The shared tools, with this session's confirmation and a "tool" event
    for each call (so the page can show what's happening)."""

    def __init__(self, shared: ToolRegistry, confirm, emit):
        super().__init__(confirm=confirm)
        self.tools = shared.tools
        self.server_tools = shared.server_tools
        self._emit = emit
        # A health check's session (WebAssistant.start_check): only
        # READ_ONLY_TOOLS are offered or run, so a check never writes.
        self.check_mode = False

    def read_only(self) -> list[str]:
        """The read-only tools this session has (READ_ONLY_TOOLS order)."""
        return [n for n in READ_ONLY_TOOLS if n in self.tools]

    def schemas(self, model: str | None = None) -> list[dict]:
        if not self.check_mode:
            return super().schemas(model)
        return ([self.tools[n].schema() for n in self.read_only()]
                + self.server_schemas(model))

    def dispatch(self, name, tool_input, untrusted=False):
        self._emit({"type": "tool", "name": name})
        if self.check_mode and name not in READ_ONLY_TOOLS:
            log.warning("Check session: refused %s (not read-only)", name)
            return "Not available in a health check: it could change something.", True
        return super().dispatch(name, tool_input, untrusted=untrusted)


class SpeechByLanguage:
    """Whisper and the voice for sessions in another language than the
    server's (a Chinese-speaking family member on an English server).
    `models`: language -> (Whisper model, Piper voice). Each is made on
    first use and cached per model and voice, not per session: everyone
    speaking that language shares one."""

    def __init__(self, server_language: str, models: dict,
                 make_transcriber: Callable[[str, str], object],
                 make_tts: Callable[[str], object]):
        self.language = i18n.normalize(server_language)
        self.models = {i18n.normalize(k): v for k, v in models.items()}
        self.make_transcriber = make_transcriber  # (model, language)
        self.make_tts = make_tts                  # (voice)
        self._transcribers: dict[tuple, object] = {}
        self._voices: dict[str, object] = {}
        self.lock = threading.Lock()

    def _spec(self, language: str | None):
        lang = i18n.normalize(language) if language else self.language
        return None if lang == self.language else self.models.get(lang)

    def transcriber(self, language: str | None):
        """The Transcriber for `language`; None = the server's own."""
        spec = self._spec(language)
        if spec is None:
            return None
        key = (spec[0], i18n.normalize(language))
        with self.lock:
            found = self._transcribers.get(key)
            if found is None:
                log.info("Speech: Whisper %s for %s sessions", *key)
                found = self._transcribers[key] = self.make_transcriber(*key)
        return found

    def tts(self, language: str | None):
        """The voice for `language`; None = the server's own."""
        spec = self._spec(language)
        if spec is None:
            return None
        with self.lock:
            found = self._voices.get(spec[1])
            if found is None:
                log.info("Speech: voice %s for %s sessions", spec[1],
                         i18n.normalize(language))
                found = self._voices[spec[1]] = self.make_tts(spec[1])
        return found


class WebAssistant:
    """Everything the HTTP layer needs, without any HTTP (easy to test)."""

    def __init__(self, chat_factory: Callable[[ToolRegistry], object],
                 transcriber, tts, tools: ToolRegistry | None = None,
                 conversation_timeout: float = 300, confirm_timeout: float = 120,
                 max_sessions: int = 20, clock=time.monotonic,
                 history=None, memory=None, family=None, memory_review=None,
                 speech: SpeechByLanguage | None = None):
        self.chat_factory = chat_factory
        self.history = history  # a HistoryStore (conversation history) or None
        self.memory = memory    # PineconeMemory, for semantic search and deletes
        # memory_review.MemoryReview (the Memory screen); made from `memory`
        # on first use when not given
        self.memory_review = memory_review
        self.family = family    # family.Family (invited people), or None
        # sid -> family user id or None (owner); create_app sets it to the
        # session store's user()
        self.user_of: Callable[[str], str | None] = lambda sid: None
        self.transcriber = transcriber
        self.tts = tts
        # other languages' Whisper and voice (family members), or None
        self.speech = speech
        # voice_picker.VoicePicker (each person's own reply voice), or None:
        # everyone hears `tts`
        self.voices = None
        self.tools = tools or ToolRegistry()
        self.conversation_timeout = conversation_timeout
        self.confirm_timeout = confirm_timeout
        self.max_sessions = max_sessions
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        # id -> (session id, text to speak, language): only that session
        # may fetch it
        self.replies: OrderedDict[str, tuple] = OrderedDict()
        self.audio_cache: OrderedDict[str, bytes] = OrderedDict()
        self._ids = itertools.count(1)
        self._stt_lock = threading.Lock()
        # TTS engines aren't thread-safe and SAPI needs COM set up in its
        # thread, so all rendering happens in one dedicated thread.
        self._tts_pool = ThreadPoolExecutor(1, thread_name_prefix="web-tts",
                                            initializer=_init_com)
        if callable(getattr(tts, "warm_up", None)):
            # load the voice model now, so the first reply isn't the slow one
            self._tts_pool.submit(_warm_up, tts)

    # sessions
    def session(self, sid: str) -> Session | None:
        """This browser's conversation (made on first use). None for a family
        member who is no longer listed."""
        ended = []
        user = self.user_of(sid)  # webapp.GONE (signed out): no session
        ctx = None
        if user is not None:
            ctx = self.family.context(user) if self.family is not None else None
            if ctx is None:
                return None
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                if len(self.sessions) >= self.max_sessions:
                    oldest = min((x for x in self.sessions.values()
                                  if not x.busy.locked()),
                                 key=lambda x: x.last_active, default=None)
                    if oldest is not None:
                        ended.append(self.sessions.pop(oldest.sid))
                s = self._new_session(sid, user, ctx)
                self.sessions[sid] = s
        for old in ended:
            self._end(old)
        if user is not None and self.family.get(user) is None:
            # removed while this was being made (after drop_user ran)
            with self.lock:
                if self.sessions.get(sid) is s:
                    del self.sessions[sid]
            return None
        return s

    def _new_session(self, sid: str, user: str | None = None, ctx=None) -> Session:
        holder: dict = {}
        # a family member's own tools (web only), never the owner's
        registry = SessionTools(ctx.registry if ctx is not None else self.tools,
                                confirm=lambda q: self._confirm(holder["s"], q),
                                emit=lambda e: holder["s"].emit(e))
        if ctx is not None:
            s = Session(sid, ctx.chat_factory(registry), registry, self.clock(),
                        user=user, history=ctx.history)
        else:
            s = Session(sid, self.chat_factory(registry), registry, self.clock(),
                        history=self.history)
        holder["s"] = s
        return s

    def history_of(self, s: Session):
        """(history store, memory) a session may see: its own family store
        and no memory, or the owner's."""
        if s.user is not None:
            return s.history, None
        return self.history, self.memory

    def drop_user(self, user: str) -> list[Session]:
        """Forget a removed family member's conversations without saving or
        summarizing them (their history is being deleted)."""
        with self.lock:
            gone = [x for x in self.sessions.values() if x.user == user]
            for x in gone:
                self.sessions.pop(x.sid, None)
        for x in gone:
            if x.pending:
                x.pending.answered.set()  # a waiting yes/no is a no
            abort = getattr(x.chat, "abort", None)
            if callable(abort):  # stop a reply being streamed
                abort()
        return gone

    def reap(self) -> int:
        """End conversations idle longer than the timeout. Returns how many."""
        now = self.clock()
        with self.lock:
            idle = [s for s in self.sessions.values()
                    if s.chat.history and not s.busy.locked()
                    and now - s.last_active > self.conversation_timeout]
        for s in idle:
            self._end(s)
        return len(idle)

    def _end(self, s: Session) -> str | None:
        if not s.chat.history or not s.busy.acquire(blocking=False):
            return None
        try:
            summary = s.chat.end_conversation()
            if summary:
                log.info("Web conversation saved to memory (%d chars)", len(summary))
                log.debug("Saved summary: %s", summary)
            return summary
        except Exception:
            log.exception("Ending a web conversation failed")
            return None
        finally:
            s.busy.release()

    def end_session(self, sid: str) -> None:
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s is not None:
            self._end(s)

    # conversation history
    def forget_conversation(self, cid: str) -> None:
        """A conversation was deleted: drop it from any browser that has it
        open, so it isn't summarized into memory or saved again."""
        with self.lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            if s.chat.recorder and s.chat.conversation_id == cid:
                s.chat.reset()
                s.chat.recorder.conversation_id = None

    def set_incognito(self, s: Session, on: bool) -> bool:
        """Switch this browser's incognito mode. The current conversation
        ends first (saved if it was being saved), so nothing said while
        incognito ends up in history or memory. False if a turn is running."""
        if s.chat.recorder is None or s.chat.incognito == on:
            return True
        if not s.busy.acquire(timeout=2):
            return False
        try:
            if s.chat.history or s.chat.conversation_id:
                s.chat.end_conversation()
            s.chat.recorder.incognito = on
        finally:
            s.busy.release()
        return True

    def start_check(self, s: Session) -> list[str] | None:
        """Make this session a health check's (assistant/watch.py, smoke.py),
        for good: no history (its recorder is dropped), no memory summary or
        review offer (ClaudeChat.check_mode), and only read-only tools
        (SessionTools.check_mode). Only a fresh session can be one, so
        nothing said before is affected. Returns the read-only tools it
        has, or None (a turn running, or it already has a conversation)."""
        if not s.busy.acquire(timeout=2):
            return None
        try:
            if not getattr(s.chat, "check_mode", False):
                if s.chat.history or s.chat.conversation_id:
                    return None
                s.chat.recorder = None
                s.chat.review_nudge = None
                s.chat.check_mode = True
                s.registry.check_mode = True
            log.info("Web: session %s is a health check", s.sid[:6])
            return s.registry.read_only()
        finally:
            s.busy.release()

    def shutdown(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for s in sessions:
            if s.pending:  # a turn waiting on a yes: say no and let it finish
                s.pending.answered.set()
            if s.thread:
                s.thread.join(timeout=10)
            self._end(s)
        if self.family is not None:
            self.family.close()  # family members' history files
        self._tts_pool.shutdown(wait=False)

    # turns
    def start_turn(self, s: Session, text: str | None = None,
                   recording: np.ndarray | None = None,
                   attachments: list[dict] | None = None) -> bool:
        """Start a turn in the background. False if one is already running.
        `attachments`: content blocks of files sent with it (attachments.py).

        Waits briefly for the lock: a turn puts "done" just before releasing
        it, so a page that starts the next turn right away isn't refused."""
        if not s.busy.acquire(timeout=2):
            return False
        while not s.events.empty():  # leftovers of an abandoned turn
            s.events.get_nowait()
        if (s.chat.history
                and self.clock() - s.last_active > self.conversation_timeout):
            s.busy.release()
            self._end(s)  # a new conversation starts with this turn
            if not s.busy.acquire(blocking=False):
                return False
        s.last_active = self.clock()
        s.thread = threading.Thread(target=self._run_turn,
                                    args=(s, text, recording, attachments),
                                    name=f"web-turn-{s.sid[:6]}", daemon=True)
        s.thread.start()
        return True

    def _run_turn(self, s: Session, text, recording, attachments=None) -> None:
        rec = s.chat.recorder
        if rec is not None and rec.conversation_id is None:
            rec.source = "web"  # a new conversation started with push-to-talk
        with i18n.using(s.language):  # its sentences and tool questions
            self._turn_body(s, text, recording, attachments)

    def _turn_body(self, s: Session, text, recording, attachments=None) -> None:
        from assistant.cli import get_reply, is_exit, loggable_reply

        try:
            if recording is not None:
                text = self.transcribe(recording, s.language)
                s.events.put({"type": "transcript", "text": text})
                log.info("Web heard %d chars", len(text))
                log.debug("Web heard: %r", text)
            text = (text or "").strip()
            if not text and not attachments:
                s.events.put({"type": "error", "message": i18n.t("not_caught")})
                return
            if is_exit(text) and not attachments:
                s.chat.end_conversation()
                self._reply(s, i18n.t("goodbye"), ended=True)
                return
            on_text = lambda d: s.events.put({"type": "delta", "text": d})  # noqa: E731
            if attachments:
                reply = self._ask_with_files(s, text, attachments, on_text)
            else:
                reply = get_reply(  # it logs errors itself
                    s.chat, text, output_fn=lambda m: None, on_text=on_text)
            log.info("Web replied %d chars", len(reply))
            log.debug("Web replied: %r", loggable_reply(s.chat, reply))
            self._reply(s, reply)
        except Exception:
            log.exception("Web turn failed")
            s.events.put({"type": "error", "message": i18n.t("error")})
        finally:
            s.pending = None
            s.last_active = self.clock()
            s.events.put({"type": "done"})  # before release: see start_turn
            s.busy.release()

    @staticmethod
    def _ask_with_files(s: Session, text: str, attachments: list[dict], on_text) -> str:
        """get_reply for a turn with files: Claude refusing the request gets
        a sentence about the file instead of the generic API error, "too
        long" when its reason is about length (a PDF over 100 pages, a file
        over the context window), else "couldn't read" (a protected or
        broken PDF). ask() has already rolled the turn back, so Kyle can
        retry without the file."""
        import anthropic

        from assistant.cli import friendly_error

        try:
            return s.chat.ask(text, on_text=on_text, attachments=attachments)
        except anthropic.BadRequestError as e:
            log.warning("Claude refused a turn with %d attachment(s) (HTTP %s)",
                        len(attachments), e.status_code)
            log.debug("Claude's reason: %s", e)
            too_long = FILE_TOO_LONG.search(str(getattr(e, "message", "") or e))
            return i18n.t("file_too_long" if too_long else "file_unreadable")
        except Exception as e:  # noqa: BLE001 - as get_reply
            return friendly_error(e, text, lambda m: None)

    def _reply(self, s: Session, text: str, ended: bool = False) -> None:
        s.events.put({"type": "reply", "text": text,
                      "audio": f"/api/audio/{self.remember(text, s)}",
                      "ended": ended})

    def _confirm(self, s: Session, question: str) -> bool:
        """Called in the turn's thread: ask the page and wait for the answer."""
        pending = PendingConfirm(question, stream=s.listener is not None)
        s.pending = pending
        s.emit({"type": "confirm", "question": question, "id": pending.id,
                "audio": f"/api/audio/{self.remember(question, s)}"})
        answered = pending.answered.wait(self.confirm_timeout)
        s.pending = None
        if not answered:
            log.info("No answer to a confirmation; treating it as no")
            s.emit({"type": "status", "text": i18n.t("no_answer", s.language)})
        return answered and pending.answer

    def answer(self, s: Session, yes: bool, confirm_id: str | None = None) -> bool:
        """Answer the pending question; False if there is none, or it isn't
        the one meant. With `confirm_id` (from the "confirm" event) only that
        question is answered. Without it, only a question asked during
        chat_stream(on_event=...) is: a late answer from the voice view must
        never answer a newer push-to-talk question."""
        pending = s.pending
        if pending is None:
            return False
        if (pending.id != confirm_id) if confirm_id is not None else not pending.stream:
            return False
        pending.answer = yes
        s.last_active = self.clock()
        pending.answered.set()
        return True

    def events(self, s: Session, max_wait: float = 600):
        """Events of the running turn, up to "done" or a "confirm" (whose
        answer continues the turn in a new request)."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                event = s.events.get(timeout=0.5)
            except queue.Empty:
                if not s.busy.locked() and s.events.empty():
                    return  # the turn ended and someone else read "done"
                continue
            yield event
            if event["type"] in ("done", "confirm"):
                return

    def chat_stream(self, s: Session, text: str,
                    cancel: threading.Event | None = None, on_event=None):
        """Ask Claude in this session's conversation and yield the reply's
        text deltas as they arrive. Setting `cancel` stops it (barge-in);
        what was said so far stays in the conversation. Tool calls and
        confirmations work as in a normal turn (a confirmation is answered
        with `answer()`). With `on_event`, this turn's tool, confirm and status
        events go to it rather than to the session's queue (which a page may
        be reading). Raises RuntimeError if a turn is already running."""
        from assistant.cli import get_reply

        if not s.busy.acquire(timeout=2):
            raise RuntimeError("a turn is already running in this session")
        s.listener = on_event
        rec = s.chat.recorder
        if rec is not None and rec.conversation_id is None:
            rec.source = "voice"  # a new conversation started in voice mode
        deltas: queue.Queue = queue.Queue()
        end = object()

        def on_text(delta):
            streamed.append(delta)
            deltas.put(delta)

        def run():
            try:
                with i18n.using(s.language):  # error sentences, tool questions
                    reply = get_reply(s.chat, text, output_fn=lambda m: None,
                                      on_text=on_text, cancel=cancel)
                if not streamed and reply:  # an error sentence
                    deltas.put(reply)
            finally:
                s.last_active = self.clock()
                s.listener = None
                s.busy.release()
                deltas.put(end)

        streamed = []
        s.last_active = self.clock()
        threading.Thread(target=run, name=f"web-stream-{s.sid[:6]}",
                         daemon=True).start()
        while True:
            item = deltas.get()
            if item is end:
                return
            if cancel is None or not cancel.is_set():  # nothing after a barge-in
                yield item

    # speech
    def transcribe(self, recording: np.ndarray, language: str | None = None) -> str:
        """Whisper for `language` (a session's; None = the server's)."""
        found = self.speech.transcriber(language) if self.speech and language else None
        with self._stt_lock:  # one at a time, whichever model (CPU, memory)
            return (found or self.transcriber).transcribe(recording)

    def transcribe_upload(self, data: bytes, max_seconds: float | None = 60,
                          language: str | None = None) -> str:
        """A browser recording (any format) to text."""
        return self.transcribe(decode_upload(data, max_seconds), language)

    def _language_tts(self, language: str | None):
        """The voice for `language` (a family member's, e.g. Mandarin), else
        the configured TTS engine."""
        found = self.speech.tts(language) if self.speech and language else None
        return found or self.tts

    def tts_for(self, session: Session | None, language: str | None = None):
        """The engine for this person's replies: the voice they picked if
        this server has it, else their language's, else the configured one."""
        if language is None and session is not None:
            language = session.language
        default = self._language_tts(language)
        if self.voices is None or session is None:
            return default
        try:
            engine = self.voices.engine_for(session.user, session.language)
        except Exception:
            log.exception("Choosing a voice failed; using the default")
            return default
        return default if engine is self.tts else engine

    def render(self, engine, text: str) -> bytes:
        """`text` spoken by `engine`, as WAV bytes (in the TTS thread)."""
        return self._tts_pool.submit(render_wav, engine, text).result(timeout=120)

    def synthesize(self, text: str, session: Session | None = None,
                   language: str | None = None) -> bytes:
        """`text` spoken in `session`'s voice (see tts_for), as WAV bytes.
        Every voice renders on the one TTS thread. A picked voice that
        fails falls back to the language's (or the configured) one."""
        engine = self.tts_for(session, language)
        default = self._language_tts(language if language is not None
                                     else getattr(session, "language", None))
        if engine is default:
            return self.render(engine, text)
        try:
            return self.render(engine, text)
        except Exception as e:
            # dropped and skipped for a while (one log line), not retried
            # on every sentence
            self.voices.failed(engine, e)
            return self.render(default, text)

    def remember(self, text: str, session: Session | None = None) -> str:
        """An id for /api/audio/<id>, which only `session` may fetch."""
        rid = f"{next(self._ids)}-{secrets.token_urlsafe(6)}"
        sid = session.sid if session is not None else None
        lang = session.language if session is not None else None
        with self.lock:
            self.replies[rid] = (sid, text, lang)
            while len(self.replies) > 50:
                rid_old, _ = self.replies.popitem(last=False)
                self.audio_cache.pop(rid_old, None)
        return rid

    def audio_for(self, rid: str, session: Session | None = None) -> bytes | None:
        """The reply's WAV; None if unknown or another session's."""
        with self.lock:
            entry = self.replies.get(rid)
            if entry is None or (entry[0] is not None and (
                    session is None or entry[0] != session.sid)):
                return None
            if rid in self.audio_cache:
                return self.audio_cache[rid]
            text, language = entry[1], entry[2]
        data = self.synthesize(text, session, language)
        with self.lock:
            self.audio_cache[rid] = data
            while len(self.audio_cache) > 10:
                self.audio_cache.popitem(last=False)
        return data


def _init_com() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:  # not Windows / no comtypes: nothing to set up
        pass


def _warm_up(tts) -> None:
    started = time.perf_counter()
    try:
        tts.warm_up()
        log.info("TTS %s warmed up in %.2f s", type(tts).__name__,
                 time.perf_counter() - started)
    except Exception:
        log.exception("TTS warm-up failed")


def render_wav(tts, text: str, attempts: int = 3) -> bytes:
    """Render `text` with the TTS engine to WAV bytes (retries an empty file,
    which eSpeak occasionally writes)."""
    from assistant.voice_session import fast_wav

    data = fast_wav(tts, text)  # Piper in memory / eSpeak directly
    if data:
        return data
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reply.wav"
        for _ in range(attempts):
            tts.save_to_file(text, path)
            if path.exists() and path.stat().st_size > 44:
                return path.read_bytes()
    raise RuntimeError("the TTS engine wrote no audio")


# --- HTTP ---------------------------------------------------------------------

def tokens_match(given: str, token: str) -> bool:
    """Constant-time comparison (hashing first hides the length too)."""
    return hmac.compare_digest(hashlib.sha256(given.strip().encode()).digest(),
                               hashlib.sha256(token.encode()).digest())


class WebAuth:
    """Who is calling: works for HTTP requests and WebSockets alike
    (anything with .cookies and .headers). Kept on app.state.web.

    Browsers use the session cookie. Other clients (scripts, a native
    shortcut) may send `Authorization: Bearer <WEB_TOKEN>` instead; they
    share one conversation, and wrong tokens count toward the lockouts."""

    def __init__(self, assistant: WebAssistant, token: str, allowed_origins=(),
                 trusted_proxy=0, limiter: "LoginLimiter | None" = None,
                 store: "SessionStore | None" = None):
        self.assistant = assistant
        self.token = token
        self.allowed_origins = tuple(o.rstrip("/") for o in allowed_origins)
        self.trusted_proxy = parse_trusted_proxy(trusted_proxy)
        self.limiter = limiter or LoginLimiter()
        self.store = store or SessionStore()
        self.bearer_sid = "bearer-" + _sign(token, "bearer")[:16]
        # failover access tokens seen (sid -> Claims), the local "sign out
        # everywhere" time for them, and this backend's role (wrap_failover)
        self.token_sessions: dict = {}
        self.failover_role = "pc"

    def sid(self, conn) -> str | None:
        claims = getattr(conn, "scope", {}).get("auth")  # set by failover.edge.EdgeMiddleware
        if claims is not None:
            if claims.scope == "app" and claims.expires:
                self.token_sessions[claims.sid] = claims  # for signed_in()
            return claims.sid
        auth = conn.headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            if tokens_match(auth[7:], self.token):
                return self.bearer_sid
            ip = self.client_ip(conn)
            if not self.limiter.blocked(ip):
                # the HTTP middleware waits this long before answering
                conn.state.auth_delay = self.limiter.fail(ip)
            log.warning("Web request with a wrong bearer token")
            return None
        sid = read_cookie(self.token, conn.cookies.get(COOKIE), self.store.epoch)
        return sid if self.store.valid(sid) else None

    def signed_in(self, sid: str) -> bool:
        """Is this session still allowed in? For connections that outlive the
        request that checked them (voice WebSockets): a sign-out revokes the
        cookie session; a Bearer session lasts as long as the token (a
        failover access token: until it expires or "sign out everywhere")."""
        if sid == self.bearer_sid or self.store.listed(sid):
            return True
        claims = self.token_sessions.get(sid)
        if claims is None:
            return False
        from assistant.failover.tokens import LEEWAY

        return (claims.expires + LEEWAY > time.time()
                and claims.signed_in > self.token_not_before())

    # --- failover access tokens (assistant/failover) ---
    def _sync_log(self):
        return getattr(getattr(self.assistant, "history", None), "sync_log", None)

    def token_not_before(self) -> int:
        """App tokens signed in at or before this unix time are refused:
        the latest "sign out everywhere" here or on any synced backend."""
        nb = self.store.tokens_not_before  # persisted in the sessions file
        log_ = self._sync_log()
        if log_ is not None:
            from assistant.failover.sync import revoked_before

            nb = max(nb, revoked_before(log_))
        return nb

    def revoke_tokens(self, role: str = "pc") -> None:
        """Sign out every phone holding an access token: on this backend
        (kept in the sessions file, so it survives a restart) and on every
        other one through the synced history log (if history is on)."""
        now = int(time.time())
        self.store.revoke_tokens(now)
        self.token_sessions.clear()
        log_ = self._sync_log()
        if log_ is not None:
            from assistant.failover.sync import revoke_event

            log_.append([revoke_event(now, role)])

    def user_of(self, sid: str | None) -> str | None:
        """Whose session this is: None = the owner (WEB_TOKEN as bearer, a
        failover access token, or an owner's cookie session); else a family
        member's id, or GONE (not signed in any more, never the owner)."""
        if sid and (sid == self.bearer_sid or sid in self.token_sessions):
            return None
        return self.store.user(sid)

    def session_for(self, conn) -> Session | None:
        sid = self.sid(conn)
        return self.assistant.session(sid) if sid else None

    def cross_origin(self, conn) -> bool:
        return is_cross_origin(conn.headers, self.allowed_origins,
                               trust_forwarded=self.trusted_proxy.trusts(conn))

    def client_ip(self, conn) -> str:
        return client_ip(conn, self.trusted_proxy)


def require_session(conn: HTTPConnection):
    """FastAPI dependency: the caller's Session, or 401 (HTTP) / close with
    1008 (WebSocket) if not signed in or from another site. Use as
    `session: Session = Depends(require_session)` on HTTP or WebSocket routes."""
    from fastapi import HTTPException, WebSocketException

    web: WebAuth = conn.app.state.web
    session = None if web.cross_origin(conn) else web.session_for(conn)
    if session is None:
        if conn.scope["type"] == "websocket":
            raise WebSocketException(code=1008)
        raise HTTPException(401, "not signed in")
    return session


def require_owner(conn: HTTPConnection):
    """FastAPI dependency: like require_session, but only for the owner
    (WEB_TOKEN). A family member's session gets 404, as if the route
    didn't exist (Kyle's Notion, lecture notes, tutor, invites)."""
    from fastapi import HTTPException, WebSocketException

    session = require_session(conn)
    if session.user is not None:
        if conn.scope["type"] == "websocket":
            raise WebSocketException(code=1008)
        raise HTTPException(404, "not found")
    return session


def page_tag(language: str = "en", google: bool = False) -> str:
    """The page's <html> tag: lang="zh-CN" makes i18n.js translate it,
    data-google shows the "connect Google" button (google_web.py). "" = the
    file as it is."""
    zh = str(language or "en").lower().startswith("zh")
    if not (zh or google):
        return ""
    return (f'<html lang="{"zh-CN" if zh else "en"}"'
            f'{" data-google" if google else ""}>')


def create_app(assistant: WebAssistant, token: str, allowed_origins=(),
               max_upload_bytes: int = 10_000_000, max_audio_seconds: float = 60,
               max_text_chars: int = 4000, trusted_proxy=0,
               max_attachment_bytes: int = 10_000_000,
               max_image_bytes: int = 8_000_000,
               limiter: LoginLimiter | None = None, reap_interval: float = 30,
               sessions_file: str | Path | None = None,
               voice: dict | None = None, failover=None, features=None,
               language: str = "en", google=None, hub=None, spotify=None,
               health=None, public=None):
    """The ASGI app. With `failover` (a failover.peers.FailoverSettings
    with a role), it is wrapped in failover.edge.EdgeMiddleware: bearer
    tokens as well as cookies, CORS for the app origin (FAILOVER.md).
    `features`: a webfeatures.Features (lecture recorder, tutor), whose
    routes are added before any wrapping, so they get the same auth.
    `language` "zh" serves the page as lang="zh-CN" (i18n.js translates it);
    `google`: a google_web.GoogleConnect for the "connect Google" button.
    `hub`: a devhub.Hub (the PC hub at /devteam, loopback requests only).
    `max_attachment_bytes` / `max_image_bytes`: per file attached to a
    turn (PDF or text / photo); the whole request stays under
    `max_upload_bytes`.
    `spotify`: a spotify_web.SpotifyConnect for "Connect Spotify" (owner).
    `health`: a function returning the watchdog's issues for the owner's
    banner (watch.banner), or None: /api/state has no "health" then.
    `public`: a public_site.PublicSite (/welcome, /privacy, /terms, the
    waitlist), or None."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                                   Response, StreamingResponse)
    from starlette.concurrency import run_in_threadpool

    limiter = limiter or LoginLimiter()
    failover = failover if failover is not None and failover.role else None
    security_headers = dict(SECURITY_HEADERS)
    if failover:
        from assistant.failover.edge import connect_src

        allowed_origins = tuple(allowed_origins) + tuple(
            o for o in (failover.app_origin,) if o)
        security_headers["Content-Security-Policy"] = CSP.replace(
            "connect-src 'self'", "connect-src " + connect_src(list(failover.backends)))
        # the phone app reads replies cross-origin from the Pi and PC
        security_headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    store = SessionStore(sessions_file)
    web = WebAuth(assistant, token, allowed_origins, trusted_proxy, limiter, store)
    family = getattr(assistant, "family", None)  # family.Family, or None
    if hasattr(assistant, "user_of"):
        assistant.user_of = web.user_of  # which family member a session is

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()

        def reaper():
            while not stop.wait(reap_interval):
                try:
                    assistant.reap()
                except Exception:
                    log.exception("Reaping idle conversations failed")

        thread = threading.Thread(target=reaper, name="web-reaper", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            await run_in_threadpool(assistant.shutdown)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.web = web

    def error(message: str, status: int):
        return JSONResponse({"error": message}, status)

    # Generic errors only: no stack traces or validation details.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        messages = {401: "not signed in", 404: "not found",
                    405: "not allowed", 413: "too large"}
        return error(messages.get(exc.status_code, "request failed"),
                     exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc):
        return error("bad request", 400)

    @app.exception_handler(Exception)
    async def crash(request, exc):
        log.exception("Unhandled error in %s %s", request.method, request.url.path)
        return error("something went wrong", 500)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        api = path.startswith("/api/")
        unsafe = request.method not in ("GET", "HEAD", "OPTIONS")
        length = request.headers.get("content-length")
        proxied = web.trusted_proxy.trusts(request)
        if proxied and request.headers.get("x-forwarded-proto") == "http":
            url = request.url.replace(scheme="https", port=None)
            if request.headers.get("x-forwarded-host"):
                url = url.replace(netloc=request.headers["x-forwarded-host"])
            response = RedirectResponse(str(url), 308)
        elif (unsafe or api) and web.cross_origin(request):
            log.warning("Rejected cross-origin %s %s", request.method, path)
            response = error("cross-origin request", 403)
        elif unsafe and length is None:
            response = error("length required", 411)
        elif length is not None and (not length.isdigit()
                                     or int(length) > max_upload_bytes):
            response = error("too large", 413)
        else:
            response = await call_next(request)
            delay = getattr(request.state, "auth_delay", 0)
            if delay:  # a wrong bearer token while many are failing
                await _wait(delay)
        for k, v in security_headers.items():
            response.headers.setdefault(k, v)
        if proxied:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000")
        if api:
            response.headers["Cache-Control"] = "no-store"
            sid = None if "authorization" in request.headers else web.sid(request)
            if sid and "set-cookie" not in response.headers:
                set_session_cookie(response, token, sid, store.epoch)  # keep it fresh
        return response

    def stream(s: Session):
        def lines():
            for event in assistant.events(s):
                yield json.dumps(event) + "\n"
        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"X-Accel-Buffering": "no"})

    async def read_upload(request: Request, files: list | None = None):
        """(fields, audio bytes, error response) from JSON or a multipart form.
        With a `files` list, the form's "attachment" files are added to it
        as (name, bytes), up to one more than attachments.MAX_ATTACHMENTS."""
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            try:
                body = await request.json()
            except ValueError:
                return None, None, error("bad request", 400)
            fields = body if isinstance(body, dict) else {}
            data = None
        elif ctype.startswith("multipart/form-data"):
            try:
                form = await request.form(max_files=MAX_FORM_FILES, max_fields=5)
            except Exception as e:  # noqa: BLE001 - any malformed body
                # Starlette raises HTTPException(400, "Too many files. Maximum
                # number of files is 6.") (MultiPartException outside an app)
                reason = str(getattr(e, "detail", "") or e)
                return None, None, error(
                    f"bad request (attach at most {MAX_ATTACHMENTS} files)"
                    if reason.startswith("Too many files") else "bad request", 400)
            upload = form.get("audio")
            data = None
            if upload is not None and hasattr(upload, "read"):
                data = await upload.read(max_upload_bytes + 1)
                if len(data) > max_upload_bytes:
                    return None, None, error("too large", 413)
            attached = [f for f in form.getlist("attachment") if hasattr(f, "read")]
            if attached and files is None:
                return None, None, error("attachments go with a message", 400)
            for f in attached[:MAX_ATTACHMENTS + 1]:
                files.append((f.filename, await f.read(max_upload_bytes + 1)))
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
        else:
            return None, None, error("send JSON or a form", 415)
        for value in fields.values():
            if isinstance(value, str) and len(value) > max_text_chars:
                return None, None, error("too large", 413)
        return fields, data, None

    async def read_attachments(files: list):
        """(content blocks, error response) for a turn's attached files."""
        from assistant.attachments import (AttachmentRejected, describe,
                                           prepare_all)
        if not files:
            return None, None
        try:
            prepared = await run_in_threadpool(
                prepare_all, files, max_image_bytes=max_image_bytes,
                max_file_bytes=max_attachment_bytes)
        except AttachmentRejected as e:
            log.info("Web turn: attachments refused (%d file(s))", len(files))
            return None, error(str(e), 400)
        # kinds and sizes only: names and contents stay out of the log
        log.info("Web turn with %d attachment(s): %s", len(prepared),
                 describe(prepared))
        return [a.block() for a in prepared], None

    async def decode(data: bytes):
        try:
            return await run_in_threadpool(decode_upload, data,
                                           max_audio_seconds), None
        except AudioTooLong:
            return None, error("recording too long", 413)
        except Exception as e:
            log.warning("Could not decode uploaded audio: %s", e)
            return None, error("could not read that recording", 400)

    def session_of(request) -> Session | None:
        return web.session_for(request)

    def unauthorized():
        return error("not signed in", 401)

    def owner_of(request) -> Session | None:
        """session_of for owner-only routes: 404 for a family member. Not
        used in this file any more (the Google routes answer each person
        about their own account), but owner-only routes added here use it
        (never session_of: that would hand a family member Kyle's data)."""
        from fastapi import HTTPException

        s = session_of(request)
        if s is not None and s.user is not None:
            raise HTTPException(404, "not found")
        return s

    app.state.owner_of = owner_of  # for owner-only routes added by others (and tests)

    # --- public: the page, PWA files, health check ---
    @app.get("/")
    def index(request: Request):
        # in the language of whoever is signed in: a family member's own,
        # else [assistant] language; "connect Google" for their own account
        sid = web.sid(request) if family is not None else None
        user = family.get(web.user_of(sid)) if sid else None
        # (or why it is off: a beta tester before the public client is set up)
        tag = (page_tag(user["language"], google is not None
                        and (google.enabled(user["id"])
                             or google.note(user["id"]) is not None)) if user is not None
               else page_tag(language, google is not None and bool(google.owner)))
        if not tag and public is not None:
            return FileResponse(STATIC / "index.html", media_type="text/html")
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        if public is None:  # no public site: no link to it
            from assistant.public_site import LOGIN_LINK

            html = html.replace(LOGIN_LINK, "", 1)
        return Response(html.replace('<html lang="en">', tag, 1) if tag else html,
                        media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

    for name, media in (("app.js", "text/javascript"), ("history.js", "text/javascript"),
                        ("app.css", "text/css"), ("sw.js", "text/javascript"),
                        ("update-watch.js", "text/javascript"),
                        ("i18n.js", "text/javascript"),
                        ("backend-router.js", "text/javascript"),
                        ("family.js", "text/javascript"),
                        ("attachments.js", "text/javascript"),
                        ("memory.js", "text/javascript"),
                        ("spotify.js", "text/javascript"),
                        ("voice-picker.js", "text/javascript"),
                        ("ui.js", "text/javascript")):
        app.add_api_route(f"/{name}", _static_route(name, media), methods=["GET"])

    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon_svg():
        return Response(ICON_SVG, media_type="image/svg+xml")

    icons = {}

    @app.get("/icon-{size}.png")
    def icon(size: int):
        if size not in ICON_SIZES:
            return error("not found", 404)
        if size not in icons:
            icons[size] = icon_png(size)
        return Response(icons[size], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/silence.wav")
    def silence():
        """Played on the first tap so iOS lets the page play replies later."""
        buf = io.BytesIO()
        audio.write_wav(buf, audio.silence(0.05))
        return Response(buf.getvalue(), media_type="audio/wav")

    @app.get("/earcons/{name}.wav")
    def earcon(name: str):
        """The voice view's soft listening / thinking / ended sounds."""
        if name not in EARCONS:
            return error("not found", 404)
        return FileResponse(STATIC / "earcons" / f"{name}.wav", media_type="audio/wav",
                            headers={"Cache-Control": "public, max-age=86400"})

    from assistant.version import version

    @app.get("/healthz")
    def healthz(request: Request):
        # The version lets the page reload after an update. Only a request
        # from this machine itself (the host supervisor waiting to restart,
        # not one relayed by a proxy) also learns whether a turn is running.
        body = {"ok": True, "version": version()}
        client = request.client.host if request.client else ""
        if is_loopback(client) and "x-forwarded-for" not in request.headers:
            body["busy"] = assistant_busy(assistant, app)
            # when this process started: the watchdog's "it restarted" cue
            body["started"] = STARTED
        return body

    # --- sign-in ---
    @app.post("/api/login")
    async def login(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        given = str(body.get("token", "")) if isinstance(body, dict) else ""
        if not tokens_match(given, token):  # the right token always works
            ip = web.client_ip(request)
            if limiter.blocked(ip):
                return error("too many attempts; try again later", 429)
            delay = limiter.fail(ip)
            log.warning("Web sign-in with a wrong token")
            if delay:
                await _wait(delay)
            return error("sign-in failed", 401)
        sid = web.sid(request)
        if sid is None or sid == web.bearer_sid or web.user_of(sid) is not None:
            # (a browser holding a family member's cookie gets a new owner
            # session, never that member's turned into the owner's)
            sid = store.create()
        response = JSONResponse({"ok": True})
        set_session_cookie(response, token, sid, store.epoch)
        log.info("Web sign-in ok")
        return response

    def signed_out():
        response = JSONResponse({"ok": True})
        clear_session_cookie(response)
        return response

    async def end_voice(sids) -> None:
        """Hang up the voice conversations of signed-out sessions (their
        WebSockets close when the conversation ends)."""
        live = getattr(app.state, "voice_conversations", {})
        for sid in list(sids):
            entry = live.get(sid)
            if entry is not None:
                await run_in_threadpool(entry[0].end, "signed_out")

    @app.post("/api/logout")
    async def logout(request: Request):
        """Sign this browser out; its cookie stops working everywhere."""
        sid = web.sid(request)
        if sid:
            store.revoke(sid)
            await end_voice([sid])
            await run_in_threadpool(assistant.end_session, sid)
        return signed_out()

    @app.post("/api/logout-all")
    async def logout_all(request: Request):
        """Sign out every browser (e.g. a lost phone), and with failover every
        phone's access token on every synced backend. Clients sending
        WEB_TOKEN itself as bearer: change WEB_TOKEN to cut those off."""
        sid = web.sid(request)
        if sid is None:
            return unauthorized()
        user = web.user_of(sid)
        if user == GONE:  # signed out (or removed) a moment ago
            return unauthorized()
        if user is not None:  # a family member: only their own browsers
            revoked = store.revoke_user(user)
            await end_voice(revoked)
            for sid in revoked:
                await run_in_threadpool(assistant.end_session, sid)
            log.info("Web: family member %s signed out everywhere", user)
            return signed_out()
        # every live voice call but a family member's (they stay signed in)
        live = [s for s in list(getattr(app.state, "voice_conversations", {}))
                if web.user_of(s) in (None, GONE)]
        revoked = store.revoke_all()
        # failover access tokens (phones) too, on every synced backend
        revoked += list(web.token_sessions)
        web.revoke_tokens(web.failover_role)
        await end_voice(dict.fromkeys([*revoked, *live]))  # each once
        for sid in revoked:
            await run_in_threadpool(assistant.end_session, sid)
        log.warning("Web: signed out everywhere")
        return signed_out()

    # --- signed in ---
    @app.get("/api/state")
    def state(request: Request):
        s = session_of(request)
        if s is None:
            return unauthorized()
        out = {"busy": s.busy.locked(),
               "confirm": (s.pending.question if s.pending
                           and not s.pending.stream else None),
               "incognito": s.chat.incognito,
               "history": assistant.history_of(s)[0] is not None,
               "owner": s.user is None,
               "family": s.user is None and family is not None,
               "memory": s.user is None and getattr(assistant, "memory", None) is not None,
               "language": s.language,
               "voice_picker": getattr(assistant, "voices", None) is not None,
               "conversation": s.chat.conversation_id,
               "turns": sum(1 for m in s.chat.history if is_user_turn(m)),
               # the watchdog's issues (watch.py): the owner's only, never
               # a family member's
               "health": owner_health(s)}
        if s.user is None:  # the API bill is the owner's business only
            out["month_usd"] = usage.month_usd()
        return out

    def owner_health(s: Session):
        if health is None or s.user is not None:
            return None
        try:
            return health()
        except Exception:  # a broken status file must not break the page
            log.exception("Reading the health status failed")
            return None

    @app.post("/api/check-session")
    def check_session(request: Request):
        """Make this (fresh) session a health check's: nothing saved, only
        read-only tools (WebAssistant.start_check). One way: it ends with
        sign-out. Used by assistant/smoke.py."""
        s = session_of(request)
        if s is None:
            return unauthorized()
        tools = assistant.start_check(s)
        if tools is None:
            return error("only a new, idle session can be a check session", 409)
        return {"ok": True, "read_only_tools": tools,
                "server_tools": [t["name"] for t in s.registry.server_tools]}

    @app.post("/api/turn")
    async def turn(request: Request):
        s = session_of(request)
        if s is None:
            return unauthorized()
        files: list = []
        fields, data, err = await read_upload(request, files)
        if err:
            return err
        blocks, err = await read_attachments(files)
        if err:
            return err
        recording = None
        if data:
            recording, err = await decode(data)
            if err:
                return err
        text = fields.get("text")
        if recording is None and not blocks and not (
                isinstance(text, str) and text.strip()):
            return error("send text or audio", 400)
        if not await run_in_threadpool(
                assistant.start_turn, s,
                text if recording is None else None, recording, blocks):
            return error("still working on the last request", 409)
        return stream(s)

    @app.post("/api/confirm")
    async def confirm(request: Request):
        """Answer a pending question: {"answer": "yes"} or a recording."""
        from assistant.cli import is_yes

        s = session_of(request)
        if s is None:
            return unauthorized()
        pending = s.pending
        if pending is None or pending.stream:  # the voice view's question
            return error("nothing to confirm", 409)
        fields, data, err = await read_upload(request)
        if err:
            return err
        answer = str(fields.get("answer") or fields.get("text") or "")
        if data:
            recording, err = await decode(data)
            if err:
                return err
            answer = await run_in_threadpool(assistant.transcribe, recording,
                                             s.language)
            s.events.put({"type": "transcript", "text": answer})
        yes = is_yes(answer)
        log.info("Web confirmation -> %s", "yes" if yes else "no")
        log.debug("Confirmation answer: %r", answer)
        if not assistant.answer(s, yes, confirm_id=pending.id):
            return error("nothing to confirm", 409)
        return stream(s)

    @app.get("/api/events")
    def resume(request: Request):
        """Re-attach to a running turn (e.g. after the phone slept)."""
        s = session_of(request)
        if s is None:
            return unauthorized()
        return stream(s)

    @app.get("/api/audio/{rid}")
    async def reply_audio(rid: str, request: Request):
        s = session_of(request)
        if s is None:
            return unauthorized()
        try:
            data = await run_in_threadpool(assistant.audio_for, rid, s)
        except Exception:
            log.exception("Rendering reply audio failed")
            return error("could not make audio", 500)
        if data is None:
            return error("not found", 404)
        return Response(data, media_type="audio/wav")

    from assistant.voice_ws import add_voice_routes

    app.state.voice_conversations = add_voice_routes(
        app, assistant, allowed_origins=web.allowed_origins, voice=voice)

    from assistant import history_web

    history_web.register(app, assistant, session_of, error)
    from assistant import memory_web

    memory_web.register(app, assistant, owner_of, error)  # Kyle's memory only

    if getattr(assistant, "voices", None) is not None:
        from assistant import voice_picker

        voice_picker.register(app, assistant, error)  # everyone's own reply voice
    if google is not None:
        from assistant import google_web

        # each person's own account: the owner's, or a family member's
        google_web.register(app, google, session_of, error, web)
    if spotify is not None:
        from assistant import spotify_web

        spotify_web.register(app, spotify, owner_of, error)  # Kyle's only
    if features is not None:
        from assistant import webfeatures

        webfeatures.install(app, features)
    from assistant import family as family_mod

    family_mod.register(app, family, web, assistant, error)
    from assistant import account

    account.register(app, family, web, error)  # their own export / delete
    if public is not None:
        from assistant import public_site

        public_site.register(app, public, web, error)
    if hub is not None:
        from assistant import devhub

        devhub.install(app, hub)
    if failover:
        return wrap_failover(app, token, failover, web)
    return app


def wrap_failover(app, token: str, failover, web: WebAuth):
    """EdgeMiddleware around the web app (FAILOVER.md section 3). It uses
    this app's LoginLimiter and client address, and cookie clients keep
    working (same-origin requests without a bearer token pass through)."""
    from starlette.requests import HTTPConnection

    from assistant.failover.edge import EdgeMiddleware
    from assistant.failover.tokens import TokenAuthority

    web.failover_role = failover.role
    routes = {}
    history = getattr(web.assistant, "history", None)
    sync_log = getattr(history, "sync_log", None)
    if sync_log is not None:
        from assistant.failover.sync import make_sync_app

        # pulled/pushed events are projected into the history tables at once
        routes["/api/sync/"] = make_sync_app(sync_log)
    return EdgeMiddleware(app, authority=TokenAuthority(token), role=failover.role,
                          app_origins=[o for o in (failover.app_origin,) if o],
                          backends=list(failover.backends), routes=routes,
                          limiter=web.limiter,
                          client_ip=lambda scope: web.client_ip(HTTPConnection(scope)),
                          trusts_proxy=lambda scope: web.trusted_proxy.trusts(
                              HTTPConnection(scope)),
                          root_bearer_sid=web.bearer_sid,
                          not_before=web.token_not_before)


def start_history_sync(config: dict, assistant, token: str, failover):
    """Pi and PC: push/pull history with the other backends every
    [failover] sync_interval seconds, in a daemon thread. The cloud only
    answers (it can't reach the tailnet). Returns the thread or None."""
    from assistant.failover.sync import Syncer, default_peers
    from assistant.failover.tokens import TokenAuthority

    history = getattr(assistant, "history", None)
    sync_log = getattr(history, "sync_log", None)
    peers = default_peers(failover.role, list(failover.backends))
    if sync_log is None or not peers:
        return None
    syncer = Syncer(sync_log, peers, TokenAuthority(token), failover.role)
    stop = threading.Event()
    interval = float(config["failover"]["sync_interval"])
    thread = threading.Thread(target=syncer.run_forever, args=(stop, interval),
                              name="history-sync", daemon=True)
    thread.start()
    log.info("History sync with %s every %.0f s",
             ", ".join(p.role for p in peers), interval)
    return thread


def assistant_busy(assistant: "WebAssistant", app) -> bool:
    """Is any web turn, pending confirmation or voice turn in progress?"""
    with assistant.lock:
        sessions = list(assistant.sessions.values())
    if any(s.busy.locked() or s.pending is not None for s in sessions):
        return True
    for conv, _ in list(getattr(app.state, "voice_conversations", {}).values()):
        with conv.lock:
            if (conv.current is not None and not conv.current.finished) \
                    or getattr(conv, "waiting", None) is not None \
                    or conv.pending is not None:
                return True
    return False


ICON_SIZES = (180, 192, 512)
STARTED = time.time()  # this process's start, in /healthz for this machine only


def _static_route(name: str, media_type: str):
    from fastapi.responses import FileResponse

    def serve_file():
        return FileResponse(STATIC / name, media_type=media_type)
    serve_file.__name__ = f"static_{name.replace('.', '_')}"
    return serve_file


def clear_session_cookie(response) -> None:
    """Sign-out's cookie removal (also account.py's "Delete my account")."""
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True,
                           samesite="strict")


async def _wait(seconds: float) -> None:
    """The slowdown on wrong tokens (a function so tests can record it)."""
    await asyncio.sleep(seconds)


def set_session_cookie(response, token: str, sid: str, epoch: str = "") -> None:
    response.set_cookie(COOKIE, make_cookie(token, sid, epoch), max_age=COOKIE_MAX_AGE,
                        httponly=True, secure=True, samesite="strict", path="/")


# --- startup ------------------------------------------------------------------

def build_assistant(config: dict, tts_name: str | None = None,
                    output_fn=print) -> WebAssistant:
    from assistant.chat import ClaudeChat
    from assistant.cli import (add_history_tools, add_memory_tools, build_memory,
                               build_memory_review, build_tools,
                               register_memory_review, register_notes)
    from assistant.history import Recorder, open_store
    import anthropic

    from assistant.stt import Transcriber
    from assistant.tts import make_tts

    a = config["assistant"]
    tools = build_tools(config, output_fn)
    for name in PC_ONLY_TOOLS:
        tools.tools.pop(name, None)
    memory = build_memory(config, output_fn)
    add_memory_tools(tools, memory)
    register_notes(tools, memory, config)
    review = build_memory_review(config, memory)
    register_memory_review(tools, review)
    store = open_store(config)  # conversation history (None if off)
    add_history_tools(tools, config, store, memory)
    client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                 timeout=a["api_timeout"])

    def chat_factory(registry):
        recorder = Recorder(store, "web", incognito=config["history"]["incognito"],
                            client_fn=lambda: client,
                            title_model=config["history"]["title_model"])
        chat = ClaudeChat(client=client, model=a["model"], max_tokens=a["max_tokens"],
                          max_history=a["max_history"], tools=registry,
                          max_tool_rounds=a["max_tool_rounds"], memory=memory,
                          recorder=recorder, language=a["language"],
                          fast_model=a["fast_model"], router=a["router"])
        if review is not None:  # [memory] weekly_review (off by default)
            chat.review_nudge = review.nudge
        return chat

    family = None
    if config["family"]["enabled"]:
        from assistant.family import Family

        family = Family(config, client=client)  # invited people (family.py)
    stt = config["stt"]
    tts_cfg = config["tts"]
    assistant = WebAssistant(chat_factory,
                             Transcriber(stt["model"], stt["device"], stt["compute_type"],
                                         language=a["language"]),
                             make_tts(tts_name or tts_cfg["engine"], **tts_cfg),
                             tools=tools,
                             conversation_timeout=a["conversation_timeout"],
                             confirm_timeout=config["webapp"]["confirm_timeout"],
                             history=store, memory=memory, family=family,
                             memory_review=review,
                             speech=family_speech(config) if family is not None else None)
    if tts_cfg["picker"]:
        from assistant.voice_picker import VoicePicker

        assistant.voices = VoicePicker(config, assistant.tts, family)
    return assistant


def family_speech(config: dict) -> SpeechByLanguage:
    """Chinese Whisper and voice for Chinese-speaking family members
    ([family] zh_stt_model, zh_piper_voice); an English server's own
    models stay for everyone else. No neural voice: "print", so the phone
    reads the reply with its own Chinese voice rather than eSpeak's."""
    from assistant.stt import Transcriber
    from assistant.tts import make_tts

    stt, f = config["stt"], config["family"]

    def transcriber(model: str, language: str):
        return Transcriber(model, stt["device"], stt["compute_type"], language=language)

    def voice(path: str):
        return make_tts("piper", piper_voice=path, fallback="print")

    return SpeechByLanguage(config["assistant"]["language"],
                            {"zh": (f["zh_stt_model"], f["zh_piper_voice"])},
                            transcriber, voice)


HUB_PROXY_WARNING = ("WARNING: the PC hub is disabled while TRUSTED_PROXY is "
                     "set; open it on this PC without the proxy.")


def serve(config: dict, host: str | None = None, port: int | None = None,
          tts_name: str | None = None, show_token: bool = False,
          env_file: str | Path = ".env", output_fn=print) -> int:
    import uvicorn

    web = config["webapp"]
    language = i18n.set_language(config["assistant"]["language"])
    host = host or web["host"]
    port = int(port or web["port"])
    trusted_proxy = parse_trusted_proxy(web["trusted_proxy"])
    local = is_loopback(host)
    if not local and not os.environ.get(TOKEN_ENV):
        # A token generated inside a server's container would be unknown to
        # you (and lost on redeploy): require one from the environment.
        raise ValueError(f"{TOKEN_ENV} must be set (32+ random characters) "
                         f"to listen on {host}")
    token = ensure_web_token(env_file, output_fn=output_fn)
    if show_token:
        # On request only, to the console; never to the log file.
        output_fn(f"Web access token: {token}")
    if not local and not trusted_proxy:
        output_fn(f"WARNING: listening on {host} without a TLS proxy in front "
                  "(trusted_proxy). The safe local setup is the default "
                  "127.0.0.1 behind `tailscale serve` (README).")
    from assistant.attachments import limits as attachment_limits
    from assistant.failover.peers import settings_from_config

    failover = settings_from_config(config)
    assistant = build_assistant(config, tts_name, output_fn)
    from assistant import webfeatures  # lecture recorder + homework tutor

    features = webfeatures.Features(config, assistant)
    from assistant import devhub  # the PC hub: only on a loopback bind

    hub = devhub.Hub(config) if devhub.enabled(config, host) else None
    from assistant import google_web  # the owner's and family members' Google

    google = google_web.from_config(config, assistant)
    spotify = None
    if config["spotify"]["enabled"]:
        from assistant.spotify_web import SpotifyConnect

        spotify = SpotifyConnect(config, assistant.tools)
    from assistant import watch  # the owner's banner when an app is down

    health = watch.banner_source(config, env_file)
    from assistant import public_site  # /welcome, /privacy, /terms, the waitlist

    app = create_app(assistant, token, allowed_origins=web["allowed_origins"],
                     max_upload_bytes=int(float(web["max_upload_mb"]) * 1_000_000),
                     max_audio_seconds=float(web["max_audio_seconds"]),
                     trusted_proxy=trusted_proxy,
                     sessions_file=web["sessions_file"] or None,
                     voice=config.get("voice"), failover=failover,
                     features=features, language=language, google=google,
                     hub=hub, spotify=spotify, health=health,
                     public=public_site.from_config(config),
                     **attachment_limits(config))
    if failover.role:
        log.info("Failover role %s; backends: %s", failover.role,
                 ", ".join(b.role for b in failover.backends) or "none")
        start_history_sync(config, assistant, token, failover)
    if not config.get("candidate"):  # a side-port check copy (cli --candidate)
        features.resume_jobs()  # lecture jobs cut off by a restart
    if hub is not None and trusted_proxy:
        # Every hub route answers 404 to a request a trusted proxy vouches
        # for (devhub.from_this_machine), so say so instead of a dead link.
        output_fn(HUB_PROXY_WARNING)
        log.warning(HUB_PROXY_WARNING)
    elif hub is not None:
        output_fn(f"PC hub on http://{host}:{port}/devteam "
                  f"({len(hub.sessions)} coordinator window(s)).")
    output_fn(f"Web app on http://{host}:{port} (Ctrl+C to stop)."
              + (f" On your phone, use `tailscale serve --https=443 "
                 f"http://127.0.0.1:{port}`." if local else ""))
    log.info("Web app listening on %s:%s (trusted proxy: %s)", host, port,
             trusted_proxy)
    uvicorn.run(app, host=host, port=port, log_config=None, proxy_headers=False,
                server_header=False, limit_concurrency=64,
                timeout_keep_alive=30,
                ws_max_size=2_000_000)  # a 30 s voice utterance is ~1 MB
    return 0
