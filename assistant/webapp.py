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
logged). Reach it from the phone through `tailscale serve` (see README).
"""

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

from assistant import audio
from assistant.tools import ToolRegistry

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "web"
COOKIE = "hc_session"
COOKIE_MAX_AGE = 30 * 24 * 3600
TOKEN_ENV = "WEB_TOKEN"
MIN_TOKEN_LENGTH = 32
# Tools that act on the PC itself make no sense from the phone.
PC_ONLY_TOOLS = {"open_in_browser"}

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
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


def make_cookie(token: str, sid: str) -> str:
    return f"{sid}.{_sign(token, sid)}"


def read_cookie(token: str, value: str | None) -> str | None:
    """The session id in a valid cookie, else None."""
    if not value or "." not in value:
        return None
    sid, sig = value.rsplit(".", 1)
    if sid and hmac.compare_digest(sig, _sign(token, sid)):
        return sid
    return None


class LoginLimiter:
    """At most `max_failures` wrong tokens per `window` seconds, in total.

    Behind `tailscale serve` every request comes from 127.0.0.1, so a
    per-address limit would mean nothing; one global limit is simpler."""

    def __init__(self, max_failures: int = 5, window: float = 300,
                 clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self.failures: deque[float] = deque()
        self.lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = self.clock() - self.window
        while self.failures and self.failures[0] <= cutoff:
            self.failures.popleft()

    def blocked(self) -> bool:
        with self.lock:
            self._prune()
            return len(self.failures) >= self.max_failures

    def fail(self) -> None:
        with self.lock:
            self.failures.append(self.clock())


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

def decode_upload(data: bytes) -> np.ndarray:
    """Any recording a browser makes (WAV, WebM/Opus, MP4/AAC from iOS) as
    16 kHz mono float32. Non-WAV goes through PyAV (installed with
    faster-whisper, FFmpeg built in)."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            samples, sr = audio.read_wav(io.BytesIO(data))
            return audio.resample(samples, sr)
        except Exception:  # e.g. float WAV: let PyAV try
            pass
    import av  # faster-whisper's own decode_audio breaks on newer PyAV

    resampler = av.AudioResampler(format="s16", layout="mono",
                                  rate=audio.SAMPLE_RATE)
    parts = []
    with av.open(io.BytesIO(data), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("no audio in the upload")
        for frame in container.decode(stream):
            frame.pts = None  # avoid resampler timestamp errors on odd files
            parts.extend(r.to_ndarray().reshape(-1) for r in resampler.resample(frame))
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
                log.info("Web conversation saved to memory: %s", summary)
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
                log.info("Web heard: %r", text)
            text = (text or "").strip()
            if not text:
                s.events.put({"type": "error",
                              "message": "I didn't catch that. Try again?"})
                return
            if is_exit(text):
                s.chat.end_conversation()
                self._reply(s, "Goodbye!", ended=True)
                return
            reply = get_reply(s.chat, text, output_fn=lambda m: None)  # it logs
            log.info("Web replied: %r", reply)
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
            log.info("No answer to %r; treating it as no", question)
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

    # speech
    def transcribe(self, recording: np.ndarray) -> str:
        with self._stt_lock:
            return self.transcriber.transcribe(recording)

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
        data = self._tts_pool.submit(render_wav, self.tts, text).result(timeout=120)
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
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reply.wav"
        for _ in range(attempts):
            tts.save_to_file(text, path)
            if path.exists() and path.stat().st_size > 44:
                return path.read_bytes()
    raise RuntimeError("the TTS engine wrote no audio")


# --- HTTP ---------------------------------------------------------------------

def create_app(assistant: WebAssistant, token: str, allowed_origins=(),
               max_upload_bytes: int = 10_000_000,
               limiter: LoginLimiter | None = None, reap_interval: float = 30):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Request
    from fastapi.responses import (FileResponse, JSONResponse, Response,
                                   StreamingResponse)
    from starlette.concurrency import run_in_threadpool

    limiter = limiter or LoginLimiter()
    allowed = tuple(o.rstrip("/") for o in allowed_origins)

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

    @app.middleware("http")
    async def guard(request: Request, call_next):
        unsafe = request.method not in ("GET", "HEAD", "OPTIONS")
        if (unsafe or request.url.path.startswith("/api/")) and \
                is_cross_origin(request.headers, allowed):
            log.warning("Rejected cross-origin %s %s", request.method,
                        request.url.path)
            response = JSONResponse({"error": "cross-origin request"}, 403)
        else:
            response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_of(request: Request) -> Session | None:
        sid = read_cookie(token, request.cookies.get(COOKIE))
        return assistant.session(sid) if sid else None

    def unauthorized():
        return JSONResponse({"error": "not signed in"}, 401)

    def stream(s: Session):
        def lines():
            for event in assistant.events(s):
                yield json.dumps(event) + "\n"
        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"X-Accel-Buffering": "no"})

    async def read_upload(request: Request):
        """(text, audio bytes) from a JSON body or a multipart form."""
        if int(request.headers.get("content-length") or 0) > max_upload_bytes:
            return None, None, JSONResponse({"error": "upload too large"}, 413)
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            try:
                body = await request.json()
            except ValueError:
                return None, None, JSONResponse({"error": "bad JSON"}, 400)
            return (body if isinstance(body, dict) else {}), None, None
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("audio")
            data = None
            if upload is not None and hasattr(upload, "read"):
                data = await upload.read(max_upload_bytes + 1)
                if len(data) > max_upload_bytes:
                    return None, None, JSONResponse({"error": "upload too large"}, 413)
            return {k: v for k, v in form.items() if isinstance(v, str)}, data, None
        return None, None, JSONResponse({"error": "send JSON or a form"}, 415)

    async def decode(data: bytes):
        try:
            return await run_in_threadpool(decode_upload, data), None
        except Exception as e:
            log.warning("Could not decode uploaded audio: %s", e)
            return None, JSONResponse({"error": "could not read that recording"}, 400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/app.js")
    def app_js():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    def app_css():
        return FileResponse(STATIC / "app.css", media_type="text/css")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript")

    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon_svg():
        return Response(ICON_SVG, media_type="image/svg+xml")

    icons = {}

    @app.get("/icon-{size}.png")
    def icon(size: int):
        if size not in (180, 192, 512):
            return Response(status_code=404)
        if size not in icons:
            icons[size] = icon_png(size)
        return Response(icons[size], media_type="image/png")

    @app.post("/api/login")
    async def login(request: Request):
        if limiter.blocked():
            return JSONResponse({"error": "too many attempts; wait a few minutes"},
                                429)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        given = str(body.get("token", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(given.strip().encode(), token.encode()):
            limiter.fail()
            log.warning("Web login with a wrong token")
            return JSONResponse({"error": "wrong token"}, 401)
        sid = read_cookie(token, request.cookies.get(COOKIE)) or secrets.token_urlsafe(16)
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, make_cookie(token, sid), max_age=COOKIE_MAX_AGE,
                            httponly=True, secure=True, samesite="strict", path="/")
        log.info("Web login ok")
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        sid = read_cookie(token, request.cookies.get(COOKIE))
        if sid:
            await run_in_threadpool(assistant.end_session, sid)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True,
                               samesite="strict")
        return response

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
        fields, data, error = await read_upload(request)
        if error:
            return error
        recording = None
        if data:
            recording, error = await decode(data)
            if error:
                return error
        text = fields.get("text")
        if recording is None and not (isinstance(text, str) and text.strip()):
            return JSONResponse({"error": "send text or audio"}, 400)
        if not assistant.start_turn(s, text=text if recording is None else None,
                                    recording=recording):
            return JSONResponse({"error": "still working on the last request"}, 409)
        return stream(s)

    @app.post("/api/confirm")
    async def confirm(request: Request):
        """Answer a pending question: {"answer": "yes"} or a recording."""
        from assistant.cli import is_yes

        s = session_of(request)
        if s is None:
            return unauthorized()
        if s.pending is None:
            return JSONResponse({"error": "nothing to confirm"}, 409)
        fields, data, error = await read_upload(request)
        if error:
            return error
        answer = str(fields.get("answer") or fields.get("text") or "")
        if data:
            recording, error = await decode(data)
            if error:
                return error
            answer = await run_in_threadpool(assistant.transcribe, recording)
            s.events.put({"type": "transcript", "text": answer})
        yes = is_yes(answer)
        log.info("Web confirmation %r -> %s", answer, "yes" if yes else "no")
        if not assistant.answer(s, yes):
            return JSONResponse({"error": "nothing to confirm"}, 409)
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
            return JSONResponse({"error": "could not make audio"}, 500)
        if data is None:
            return JSONResponse({"error": "unknown reply"}, 404)
        return Response(data, media_type="audio/wav")

    @app.get("/silence.wav")
    def silence():
        """Played on the first tap so iOS lets the page play replies later."""
        buf = io.BytesIO()
        audio.write_wav(buf, audio.silence(0.05))
        return Response(buf.getvalue(), media_type="audio/wav")

    return app


# --- startup ------------------------------------------------------------------

def build_assistant(config: dict, tts_name: str | None = None,
                    output_fn=print) -> WebAssistant:
    from assistant.chat import ClaudeChat
    from assistant.cli import build_memory, build_tools
    import anthropic

    from assistant.stt import Transcriber
    from assistant.tts import make_tts

    a = config["assistant"]
    tools = build_tools(config, output_fn)
    for name in PC_ONLY_TOOLS:
        tools.tools.pop(name, None)
    memory = build_memory(config, output_fn)
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
    token = ensure_web_token(env_file, output_fn=output_fn)
    if show_token:
        # On request only, to the console; never to the log file.
        output_fn(f"Web access token: {token}")
    if not is_loopback(host):
        output_fn(f"WARNING: listening on {host}, not just this computer. "
                  "Anyone who can reach it can try to log in. The safe setup is "
                  "the default 127.0.0.1 behind `tailscale serve` (README).")
    assistant = build_assistant(config, tts_name, output_fn)
    app = create_app(assistant, token, allowed_origins=web["allowed_origins"],
                     max_upload_bytes=int(web["max_upload_mb"] * 1_000_000))
    output_fn(f"Web app on http://{host}:{port} (Ctrl+C to stop). On your phone, "
              f"use `tailscale serve --https=443 http://127.0.0.1:{port}`.")
    log.info("Web app listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_config=None,
                proxy_headers=False, server_header=False)
    return 0
