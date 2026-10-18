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
  WebAssistant.transcribe_upload(bytes) -> text
  WebAssistant.chat_stream(session, text, cancel) -> iterator of text deltas
  WebAssistant.synthesize(text) -> WAV bytes
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

from assistant import audio
from assistant.tools import ToolRegistry

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "web"
COOKIE = "hc_session"
COOKIE_MAX_AGE = 90 * 24 * 3600  # renewed on every authenticated API call
TOKEN_ENV = "WEB_TOKEN"
MIN_TOKEN_LENGTH = 32
# Tools that act on the PC itself make no sense from the phone.
PC_ONLY_TOOLS = {"open_in_browser"}

# 'wasm-unsafe-eval': the voice view runs the Silero VAD in WebAssembly.
CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
       "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
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


class SessionStore:
    """Signed-in browsers, kept on the server so a cookie can be revoked.

    A cookie is valid only while its session id is listed here: signing out
    removes that one, "sign out everywhere" clears the list and rotates the
    epoch that every cookie signature includes. Sessions unused for
    `max_age` seconds expire. With `path` the list survives restarts (a small
    JSON file, rewritten on sign-in/out and at most daily on use); without it
    a restart signs everyone out."""

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
        self._saved_at: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.epoch = str(data["epoch"])
            self.sessions = {str(k): float(v) for k, v in data["sessions"].items()}
            self._saved_at = dict(self.sessions)
        except Exception as e:  # unreadable: start over (everyone signs in again)
            log.warning("Ignoring unreadable web session file %s: %s", self.path, e)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"epoch": self.epoch,
                                       "sessions": self.sessions}), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.path)
            self._saved_at = dict(self.sessions)
        except OSError as e:
            log.warning("Could not save web sessions to %s: %s", self.path, e)

    def create(self) -> str:
        sid = secrets.token_urlsafe(16)
        with self.lock:
            self.sessions[sid] = self.clock()
            while len(self.sessions) > self.max_sessions:
                oldest = min(self.sessions, key=self.sessions.get)
                del self.sessions[oldest]
            self._save()
        return sid

    def valid(self, sid: str | None) -> bool:
        """Is this session signed in? Counts as use (pushes back expiry)."""
        if not sid:
            return False
        now = self.clock()
        with self.lock:
            last = self.sessions.get(sid)
            if last is None:
                return False
            if now - last > self.max_age:
                del self.sessions[sid]
                self._save()
                return False
            self.sessions[sid] = now
            if now - self._saved_at.get(sid, 0) > 86400:
                self._save()
            return True

    def revoke(self, sid: str) -> None:
        with self.lock:
            if self.sessions.pop(sid, None) is not None:
                self._save()

    def revoke_all(self) -> list[str]:
        """Sign out every browser; returns the ids that were signed in."""
        with self.lock:
            sids = list(self.sessions)
            self.sessions.clear()
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
        self.all_failures: deque = deque()
        self.locked_until: dict[str, float] = {}
        self.lock = threading.Lock()

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
            q = self.failures.setdefault(ip, deque())
            q.append(now)
            self.all_failures.append(now)
            self._prune(q, now)
            self._prune(self.all_failures, now)
            if len(q) >= self.per_ip and self.locked_until.get(ip, 0) <= now:
                self.locked_until[ip] = now + self.lockout
                log.warning("Web sign-in: one address locked out after %d "
                            "wrong tokens", len(q))
            if len(self.failures) > 10_000:  # bound memory under a flood
                for key in [k for k, v in self.failures.items()
                            if not v or v[-1] <= now - self.window]:
                    self.failures.pop(key, None)
                for key in [k for k, t in self.locked_until.items() if t <= now]:
                    self.locked_until.pop(key, None)
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

def is_cross_origin(headers, allowed_origins=()) -> bool:
    """True for a browser request made by another site.

    Uses Sec-Fetch-Site when the browser sends it, else compares Origin with
    the Host (or X-Forwarded-Host, as set by a proxy). Requests with neither
    header (curl, tests) pass; they still need the session cookie."""
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
    own = {h.strip().lower() for h in (headers.get("host", ""),
                                       headers.get("x-forwarded-host", ""))
           if h.strip()}
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
    answered: threading.Event = field(default_factory=threading.Event)
    answer: bool = False


class Session:
    """One browser's conversation and the turn that is running in it."""

    def __init__(self, sid: str, chat, registry: ToolRegistry, now: float):
        self.sid = sid
        self.chat = chat
        self.registry = registry
        self.last_active = now
        self.events: queue.Queue = queue.Queue()
        self.busy = threading.Lock()
        self.pending: PendingConfirm | None = None
        self.thread: threading.Thread | None = None


class SessionTools(ToolRegistry):
    """The shared tools, with this session's confirmation and a "tool" event
    for each call (so the page can show what's happening)."""

    def __init__(self, shared: ToolRegistry, confirm, emit):
        super().__init__(confirm=confirm)
        self.tools = shared.tools
        self.server_tools = shared.server_tools
        self._emit = emit

    def dispatch(self, name, tool_input, untrusted=False):
        self._emit({"type": "tool", "name": name})
        return super().dispatch(name, tool_input, untrusted=untrusted)


class WebAssistant:
    """Everything the HTTP layer needs, without any HTTP (easy to test)."""

    def __init__(self, chat_factory: Callable[[ToolRegistry], object],
                 transcriber, tts, tools: ToolRegistry | None = None,
                 conversation_timeout: float = 300, confirm_timeout: float = 120,
                 max_sessions: int = 20, clock=time.monotonic):
        self.chat_factory = chat_factory
        self.transcriber = transcriber
        self.tts = tts
        self.tools = tools or ToolRegistry()
        self.conversation_timeout = conversation_timeout
        self.confirm_timeout = confirm_timeout
        self.max_sessions = max_sessions
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.replies: OrderedDict[str, str] = OrderedDict()  # id -> text to speak
        self.audio_cache: OrderedDict[str, bytes] = OrderedDict()
        self._ids = itertools.count(1)
        self._stt_lock = threading.Lock()
        # TTS engines aren't thread-safe and SAPI needs COM set up in its
        # thread, so all rendering happens in one dedicated thread.
        self._tts_pool = ThreadPoolExecutor(1, thread_name_prefix="web-tts",
                                            initializer=_init_com)

    # sessions
    def session(self, sid: str) -> Session:
        ended = []
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                if len(self.sessions) >= self.max_sessions:
                    oldest = min((x for x in self.sessions.values()
                                  if not x.busy.locked()),
                                 key=lambda x: x.last_active, default=None)
                    if oldest is not None:
                        ended.append(self.sessions.pop(oldest.sid))
                s = self._new_session(sid)
                self.sessions[sid] = s
        for old in ended:
            self._end(old)
        return s

    def _new_session(self, sid: str) -> Session:
        holder: dict = {}
        registry = SessionTools(self.tools,
                                confirm=lambda q: self._confirm(holder["s"], q),
                                emit=lambda e: holder["s"].events.put(e))
        s = Session(sid, self.chat_factory(registry), registry, self.clock())
        holder["s"] = s
        return s

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
        self._tts_pool.shutdown(wait=False)

    # turns
    def start_turn(self, s: Session, text: str | None = None,
                   recording: np.ndarray | None = None) -> bool:
        """Start a turn in the background. False if one is already running.

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
        s.thread = threading.Thread(target=self._run_turn, args=(s, text, recording),
                                    name=f"web-turn-{s.sid[:6]}", daemon=True)
        s.thread.start()
        return True

    def _run_turn(self, s: Session, text, recording) -> None:
        from assistant.cli import get_reply, is_exit

        try:
            if recording is not None:
                text = self.transcribe(recording)
                s.events.put({"type": "transcript", "text": text})
                log.info("Web heard %d chars", len(text))
                log.debug("Web heard: %r", text)
            text = (text or "").strip()
            if not text:
                s.events.put({"type": "error",
                              "message": "I didn't catch that. Try again?"})
                return
            if is_exit(text):
                s.chat.end_conversation()
                self._reply(s, "Goodbye!", ended=True)
                return
            reply = get_reply(  # it logs errors itself
                s.chat, text, output_fn=lambda m: None,
                on_text=lambda d: s.events.put({"type": "delta", "text": d}))
            log.info("Web replied %d chars", len(reply))
            log.debug("Web replied: %r", reply)
            self._reply(s, reply)
        except Exception:
            log.exception("Web turn failed")
            s.events.put({"type": "error", "message": "Sorry, something went wrong."})
        finally:
            s.pending = None
            s.last_active = self.clock()
            s.events.put({"type": "done"})  # before release: see start_turn
            s.busy.release()

    def _reply(self, s: Session, text: str, ended: bool = False) -> None:
        s.events.put({"type": "reply", "text": text,
                      "audio": f"/api/audio/{self.remember(text)}",
                      "ended": ended})

    def _confirm(self, s: Session, question: str) -> bool:
        """Called in the turn's thread: ask the page and wait for the answer."""
        pending = PendingConfirm(question)
        s.pending = pending
        s.events.put({"type": "confirm", "question": question,
                      "audio": f"/api/audio/{self.remember(question)}"})
        answered = pending.answered.wait(self.confirm_timeout)
        s.pending = None
        if not answered:
            log.info("No answer to a confirmation; treating it as no")
            log.debug("Unanswered: %r", question)
            s.events.put({"type": "status", "text": "No answer, so I didn't do it."})
        return answered and pending.answer

    def answer(self, s: Session, yes: bool) -> bool:
        """Answer the pending question. False if there is none."""
        pending = s.pending
        if pending is None:
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
                    cancel: threading.Event | None = None):
        """Ask Claude in this session's conversation and yield the reply's
        text deltas as they arrive. Setting `cancel` stops it (barge-in);
        what was said so far stays in the conversation. Tool calls and
        confirmations work as in a normal turn (a confirmation is answered
        with `answer()`). Raises RuntimeError if a turn is already running."""
        from assistant.cli import get_reply

        if not s.busy.acquire(timeout=2):
            raise RuntimeError("a turn is already running in this session")
        deltas: queue.Queue = queue.Queue()
        end = object()

        def on_text(delta):
            streamed.append(delta)
            deltas.put(delta)

        def run():
            try:
                reply = get_reply(s.chat, text, output_fn=lambda m: None,
                                  on_text=on_text, cancel=cancel)
                if not streamed and reply:  # an error sentence
                    deltas.put(reply)
            finally:
                s.last_active = self.clock()
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
    def transcribe(self, recording: np.ndarray) -> str:
        with self._stt_lock:
            return self.transcriber.transcribe(recording)

    def transcribe_upload(self, data: bytes, max_seconds: float | None = 60) -> str:
        """A browser recording (any format) to text."""
        return self.transcribe(decode_upload(data, max_seconds))

    def synthesize(self, text: str) -> bytes:
        """`text` spoken by the configured TTS engine, as WAV bytes."""
        return self._tts_pool.submit(render_wav, self.tts, text).result(timeout=120)

    def remember(self, text: str) -> str:
        rid = f"{next(self._ids)}-{secrets.token_urlsafe(6)}"
        with self.lock:
            self.replies[rid] = text
            while len(self.replies) > 50:
                self.replies.popitem(last=False)
        return rid

    def audio_for(self, rid: str) -> bytes | None:
        with self.lock:
            if rid in self.audio_cache:
                return self.audio_cache[rid]
            text = self.replies.get(rid)
        if text is None:
            return None
        data = self.synthesize(text)
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

    def sid(self, conn) -> str | None:
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

    def session_for(self, conn) -> Session | None:
        sid = self.sid(conn)
        return self.assistant.session(sid) if sid else None

    def cross_origin(self, conn) -> bool:
        return is_cross_origin(conn.headers, self.allowed_origins)

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


def create_app(assistant: WebAssistant, token: str, allowed_origins=(),
               max_upload_bytes: int = 10_000_000, max_audio_seconds: float = 60,
               max_text_chars: int = 4000, trusted_proxy=0,
               limiter: LoginLimiter | None = None, reap_interval: float = 30,
               sessions_file: str | Path | None = None,
               voice: dict | None = None):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                                   Response, StreamingResponse)
    from starlette.concurrency import run_in_threadpool

    limiter = limiter or LoginLimiter()
    store = SessionStore(sessions_file)
    web = WebAuth(assistant, token, allowed_origins, trusted_proxy, limiter, store)

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
        for k, v in SECURITY_HEADERS.items():
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

    async def read_upload(request: Request):
        """(fields, audio bytes, error response) from JSON or a multipart form."""
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
                form = await request.form(max_files=1, max_fields=5)
            except Exception:
                return None, None, error("bad request", 400)
            upload = form.get("audio")
            data = None
            if upload is not None and hasattr(upload, "read"):
                data = await upload.read(max_upload_bytes + 1)
                if len(data) > max_upload_bytes:
                    return None, None, error("too large", 413)
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
        else:
            return None, None, error("send JSON or a form", 415)
        for value in fields.values():
            if isinstance(value, str) and len(value) > max_text_chars:
                return None, None, error("too large", 413)
        return fields, data, None

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

    # --- public: the page, PWA files, health check ---
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    for name, media in (("app.js", "text/javascript"), ("app.css", "text/css"),
                        ("sw.js", "text/javascript")):
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

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

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
        if sid is None or sid == web.bearer_sid:
            sid = store.create()
        response = JSONResponse({"ok": True})
        set_session_cookie(response, token, sid, store.epoch)
        log.info("Web sign-in ok")
        return response

    def signed_out():
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True,
                               samesite="strict")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        """Sign this browser out; its cookie stops working everywhere."""
        sid = web.sid(request)
        if sid:
            store.revoke(sid)
            await run_in_threadpool(assistant.end_session, sid)
        return signed_out()

    @app.post("/api/logout-all")
    async def logout_all(request: Request):
        """Sign out every browser (e.g. a lost phone). Bearer clients use the
        token itself: change WEB_TOKEN to cut those off."""
        if web.sid(request) is None:
            return unauthorized()
        for sid in store.revoke_all():
            await run_in_threadpool(assistant.end_session, sid)
        log.warning("Web: signed out everywhere")
        return signed_out()

    # --- signed in ---
    @app.get("/api/state")
    def state(request: Request):
        s = session_of(request)
        if s is None:
            return unauthorized()
        return {"busy": s.busy.locked(),
                "confirm": s.pending.question if s.pending else None,
                "turns": sum(1 for m in s.chat.history
                             if m["role"] == "user" and isinstance(m["content"], str))}

    @app.post("/api/turn")
    async def turn(request: Request):
        s = session_of(request)
        if s is None:
            return unauthorized()
        fields, data, err = await read_upload(request)
        if err:
            return err
        recording = None
        if data:
            recording, err = await decode(data)
            if err:
                return err
        text = fields.get("text")
        if recording is None and not (isinstance(text, str) and text.strip()):
            return error("send text or audio", 400)
        if not await run_in_threadpool(
                assistant.start_turn, s,
                text if recording is None else None, recording):
            return error("still working on the last request", 409)
        return stream(s)

    @app.post("/api/confirm")
    async def confirm(request: Request):
        """Answer a pending question: {"answer": "yes"} or a recording."""
        from assistant.cli import is_yes

        s = session_of(request)
        if s is None:
            return unauthorized()
        if s.pending is None:
            return error("nothing to confirm", 409)
        fields, data, err = await read_upload(request)
        if err:
            return err
        answer = str(fields.get("answer") or fields.get("text") or "")
        if data:
            recording, err = await decode(data)
            if err:
                return err
            answer = await run_in_threadpool(assistant.transcribe, recording)
            s.events.put({"type": "transcript", "text": answer})
        yes = is_yes(answer)
        log.info("Web confirmation -> %s", "yes" if yes else "no")
        log.debug("Confirmation answer: %r", answer)
        if not assistant.answer(s, yes):
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
        if session_of(request) is None:
            return unauthorized()
        try:
            data = await run_in_threadpool(assistant.audio_for, rid)
        except Exception:
            log.exception("Rendering reply audio failed")
            return error("could not make audio", 500)
        if data is None:
            return error("not found", 404)
        return Response(data, media_type="audio/wav")

    from assistant.voice_ws import add_voice_routes

    app.state.voice_conversations = add_voice_routes(
        app, assistant, allowed_origins=web.allowed_origins, voice=voice)
    return app


ICON_SIZES = (180, 192, 512)


def _static_route(name: str, media_type: str):
    from fastapi.responses import FileResponse

    def serve_file():
        return FileResponse(STATIC / name, media_type=media_type)
    serve_file.__name__ = f"static_{name.replace('.', '_')}"
    return serve_file


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
    from assistant.cli import add_memory_tools, build_memory, build_tools
    import anthropic

    from assistant.stt import Transcriber
    from assistant.tts import make_tts

    a = config["assistant"]
    tools = build_tools(config, output_fn)
    for name in PC_ONLY_TOOLS:
        tools.tools.pop(name, None)
    memory = build_memory(config, output_fn)
    add_memory_tools(tools, memory)
    client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                 timeout=a["api_timeout"])

    def chat_factory(registry):
        return ClaudeChat(client=client, model=a["model"], max_tokens=a["max_tokens"],
                          max_history=a["max_history"], tools=registry,
                          max_tool_rounds=a["max_tool_rounds"], memory=memory)

    stt = config["stt"]
    tts_cfg = config["tts"]
    return WebAssistant(chat_factory,
                        Transcriber(stt["model"], stt["device"], stt["compute_type"]),
                        make_tts(tts_name or tts_cfg["engine"], **tts_cfg),
                        tools=tools,
                        conversation_timeout=a["conversation_timeout"],
                        confirm_timeout=config["webapp"]["confirm_timeout"])


def serve(config: dict, host: str | None = None, port: int | None = None,
          tts_name: str | None = None, show_token: bool = False,
          env_file: str | Path = ".env", output_fn=print) -> int:
    import uvicorn

    web = config["webapp"]
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
    assistant = build_assistant(config, tts_name, output_fn)
    app = create_app(assistant, token, allowed_origins=web["allowed_origins"],
                     max_upload_bytes=int(float(web["max_upload_mb"]) * 1_000_000),
                     max_audio_seconds=float(web["max_audio_seconds"]),
                     trusted_proxy=trusted_proxy,
                     sessions_file=web["sessions_file"] or None,
                     voice=config.get("voice"))
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
