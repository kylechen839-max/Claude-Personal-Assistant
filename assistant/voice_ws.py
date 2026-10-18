"""The voice conversation WebSocket (/api/voice) and its static files.

Why a WebSocket and not SSE plus chunk fetches: the page and the server
both talk at any moment (utterances and barge-ins up, transcript, text and
audio chunks down), audio chunks go out the instant they're rendered with no
per-chunk request, and a closed socket is an unambiguous "stop": the server
cancels the Claude stream and the TTS right away.

Binary frames, both ways: 4-byte big-endian header length, a UTF-8 JSON
header, then the payload (16 kHz mono PCM16 up, a WAV per sentence down).
Text frames are JSON control messages.

Security: the handshake needs the same signed session cookie as the rest of
the API and a same-origin Origin header (browsers always send one on a
WebSocket); sizes, message rates and the session length are limited, and
closing the socket cancels whatever is running.
"""

import asyncio
import gzip
import json
import logging
import struct
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from assistant.config import DEFAULTS
from assistant.voice_session import VoiceConversation, synthesize_wav

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "web"
VAD_FILES = {
    "ort.wasm.min.js": "text/javascript",
    "ort-wasm-simd-threaded.mjs": "text/javascript",
    "ort-wasm-simd-threaded.wasm": "application/wasm",
    "silero_vad_v5.onnx": "application/octet-stream",
}
PAGE_FILES = {
    "voice.js": "text/javascript",
    "voice-core.js": "text/javascript",
    "voice-worklet.js": "text/javascript",
    "voice.css": "text/css",
}
MAX_TEXT_MESSAGE = 4096
CONTROL_MESSAGES_PER_10S = 60
SAMPLE_RATE = 16000


# --- framing -------------------------------------------------------------------

def pack(header: dict, payload: bytes = b"") -> bytes:
    head = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack(">I", len(head)) + head + payload


def unpack(frame: bytes) -> tuple[dict, bytes]:
    if len(frame) < 4:
        raise ValueError("short frame")
    n = struct.unpack(">I", frame[:4])[0]
    if n > 2048 or 4 + n > len(frame):
        raise ValueError("bad header length")
    header = json.loads(frame[4:4 + n])
    if not isinstance(header, dict):
        raise ValueError("header is not an object")
    return header, frame[4 + n:]


def pcm16_to_float(data: bytes) -> np.ndarray:
    data = data[: len(data) - (len(data) % 2)]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


# --- checks --------------------------------------------------------------------

def origin_allowed(headers, allowed_origins=()) -> bool:
    """A WebSocket handshake must come from this site's own page. Unlike
    plain requests, a missing Origin is refused: browsers always send it."""
    origin = (headers.get("origin") or "").rstrip("/")
    if not origin:
        return False
    if origin in allowed_origins:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("https", "http"):
        return False
    own = {h.strip().lower() for h in (headers.get("host", ""),
                                       headers.get("x-forwarded-host", ""))
           if h.strip()}
    return parts.netloc.lower() in own


class RateLimit:
    """At most `limit` events per `window` seconds (sliding)."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.times: deque[float] = deque()

    def hit(self) -> bool:
        """Count one event; False if that is over the limit."""
        now = self.clock()
        while self.times and self.times[0] <= now - self.window:
            self.times.popleft()
        if len(self.times) >= self.limit:
            return False
        self.times.append(now)
        return True


def voice_settings(voice: dict | None) -> dict:
    return {**DEFAULTS["voice"], **(voice or {})}


def client_settings(v: dict) -> dict:
    """What the page needs to run its VAD and turn-taking."""
    return {"vad": v["vad"], "silence_ms": v["silence_ms"],
            "min_utterance_ms": v["min_utterance_ms"],
            "max_utterance_ms": int(v["max_utterance_s"] * 1000),
            "barge_in_ms": v["barge_in_ms"], "vad_threshold": v["vad_threshold"],
            "idle_timeout": v["idle_timeout"], "debug_panel": v["debug_panel"]}


# --- routes --------------------------------------------------------------------

def add_voice_routes(app, assistant, token: str, allowed_origins=(),
                     voice: dict | None = None, clock=time.monotonic) -> dict:
    """Register /api/voice and the voice page files on the web app. Returns
    the live conversations by session id (for tests)."""
    from fastapi import Request, WebSocket
    from fastapi.responses import FileResponse, Response
    from starlette.concurrency import run_in_threadpool
    from starlette.websockets import WebSocketDisconnect

    from assistant.webapp import COOKIE, SessionTools, read_cookie

    v = voice_settings(voice)
    allowed = tuple(o.rstrip("/") for o in allowed_origins)
    max_utterance_bytes = int(v["max_utterance_s"] * SAMPLE_RATE * 2) + 4096
    live: dict[str, tuple[VoiceConversation, WebSocket]] = {}
    gz_cache: dict[str, bytes] = {}

    for name, media in PAGE_FILES.items():
        def page_file(name=name, media=media):
            return FileResponse(STATIC / name, media_type=media)
        app.add_api_route(f"/{name}", page_file, methods=["GET"])

    @app.get("/static/vad/{name}")
    def vad_file(name: str, request: Request):
        media = VAD_FILES.get(name)
        if media is None:
            return Response(status_code=404)
        headers = {"Cache-Control": "public, max-age=604800"}
        if "gzip" in request.headers.get("accept-encoding", "") and \
                not name.endswith(".js"):
            if name not in gz_cache:
                gz_cache[name] = gzip.compress((STATIC / "vad" / name).read_bytes(), 6)
            return Response(gz_cache[name], media_type=media,
                            headers={**headers, "Content-Encoding": "gzip",
                                     "Vary": "Accept-Encoding"})
        return FileResponse(STATIC / "vad" / name, media_type=media, headers=headers)

    def synthesize(text: str) -> bytes:
        # The web app's single TTS thread (engines aren't thread-safe).
        return assistant._tts_pool.submit(synthesize_wav, assistant.tts,
                                          text).result(timeout=60)

    @app.websocket("/api/voice")
    async def voice_socket(ws: WebSocket):
        if not origin_allowed(ws.headers, allowed):
            log.warning("Rejected voice socket from origin %r",
                        ws.headers.get("origin"))
            await ws.close(code=1008)
            return
        sid = read_cookie(token, ws.cookies.get(COOKIE))
        if not sid:
            await ws.close(code=1008)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        out: asyncio.Queue = asyncio.Queue()

        def send(event: dict, data: bytes | None = None) -> None:
            loop.call_soon_threadsafe(out.put_nowait, (event, data))

        holder: dict = {}
        registry = SessionTools(assistant.tools,
                                confirm=lambda q: holder["conv"].confirm(q),
                                emit=lambda e: None)
        conv = VoiceConversation(
            assistant.chat_factory(registry), assistant.transcribe, synthesize,
            send, clock=clock, idle_timeout=v["idle_timeout"],
            max_session=v["max_session_minutes"] * 60,
            confirm_timeout=v["confirm_timeout"])
        holder["conv"] = conv
        old = live.pop(sid, None)
        live[sid] = (conv, ws)
        if old is not None:  # one voice conversation per browser
            await run_in_threadpool(old[0].end, "replaced")
        log.info("Voice conversation started")
        send({"type": "ready", "settings": client_settings(v)})

        async def writer():
            while True:
                event, data = await out.get()
                if data is not None:
                    await ws.send_bytes(pack(event, data))
                else:
                    await ws.send_text(json.dumps(event))
                if event.get("type") == "ended":
                    await ws.close(code=1000)
                    return

        async def watchdog():
            while True:
                await asyncio.sleep(1)
                reason = conv.expired()
                if reason:
                    log.info("Voice conversation ended: %s", reason)
                    await run_in_threadpool(conv.end, reason)
                    return

        utterances = RateLimit(v["utterances_per_minute"], 60, clock)
        controls = RateLimit(CONTROL_MESSAGES_PER_10S, 10, clock)
        tasks = [asyncio.create_task(writer()), asyncio.create_task(watchdog())]

        async def refuse(code: int, why: str):
            log.warning("Closing voice socket: %s", why)
            await ws.close(code=code, reason=why)

        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    frame = msg["bytes"]
                    if len(frame) > max_utterance_bytes:
                        await refuse(1009, "utterance too large")
                        break
                    if not utterances.hit():
                        await refuse(1008, "too many utterances")
                        break
                    try:
                        header, payload = unpack(frame)
                        if header.get("type") != "utterance" or \
                                int(header.get("sample_rate", 0)) != SAMPLE_RATE:
                            raise ValueError("expected 16 kHz utterance")
                        turn = int(header["turn"])
                    except (ValueError, KeyError, TypeError) as e:
                        await refuse(1003, f"bad audio frame: {e}")
                        break
                    conv.utterance(turn, pcm16_to_float(payload))
                    continue
                text = msg.get("text") or ""
                if len(text) > MAX_TEXT_MESSAGE:
                    await refuse(1009, "message too large")
                    break
                if not controls.hit():
                    await refuse(1008, "too many messages")
                    break
                try:
                    data = json.loads(text)
                    kind = data.get("type")
                except (ValueError, AttributeError):
                    await refuse(1003, "bad message")
                    break
                if kind == "interrupt":
                    conv.interrupt(int(data.get("turn", -1)),
                                   int(data.get("index", 0)),
                                   float(data.get("fraction", 0)))
                elif kind == "end":
                    await run_in_threadpool(conv.end, "user")
                elif kind == "ping":
                    send({"type": "pong", "t": data.get("t")})
                elif kind == "client_timings":
                    log.info("Voice page timings (turn %s): %s", data.get("turn"),
                             {k: data[k] for k in list(data)[:12] if k != "type"})
        except WebSocketDisconnect:
            pass
        except RuntimeError:  # receive after close
            pass
        finally:
            if live.get(sid, (None,))[0] is conv:
                live.pop(sid)
            if not conv.closed:  # closed socket: cancel and save, as on End
                await run_in_threadpool(conv.end, "closed")
            for t in tasks:
                t.cancel()
            log.info("Voice socket closed")

    return live
