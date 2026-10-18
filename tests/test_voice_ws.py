"""The voice conversation WebSocket (/api/voice): auth and Origin checks,
limits, a whole streamed turn, barge-in, ending, the vendored VAD files,
and the JavaScript unit tests (node:test) when node is installed."""

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from assistant import voice_ws, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402
from tests.voice_fakes import stream_client, streamed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "v" * 20 + "-voice-test-token-0123456789"
BASE = "https://testserver"
ORIGIN = {"origin": BASE}


class Transcriber:
    def __init__(self, *texts):
        self.texts = list(texts)

    def transcribe(self, recording):
        return self.texts.pop(0)


@pytest.fixture
def make_voice(monkeypatch):
    def make(*streams, heard=("what's on my calendar tomorrow",), memory=None,
             synth=None, **voice):
        client = stream_client(*streams)

        def chat_factory(registry):
            return ClaudeChat(client=client, model="test-model", tools=registry,
                              memory=memory)

        assistant = webapp.WebAssistant(chat_factory, Transcriber(*heard), PrintTTS())
        if synth:
            monkeypatch.setattr(voice_ws, "synthesize_wav", lambda tts, text: synth(text))
        app = webapp.create_app(assistant, TOKEN, reap_interval=3600, voice=voice)
        http = TestClient(app, base_url=BASE)
        return client, http
    return make


def login(http):
    assert http.post("/api/login", json={"token": TOKEN}).status_code == 200


def connect(http, headers=ORIGIN):
    """TestClient talks ws://, which wouldn't carry the Secure cookie that a
    browser sends over wss://; pass it on explicitly."""
    headers = dict(headers)
    cookie = http.cookies.get(webapp.COOKIE)
    if cookie:
        headers["cookie"] = f"{webapp.COOKIE}={cookie}"
    return http.websocket_connect("/api/voice", headers=headers)


def utterance(turn, seconds=1.0):
    pcm = (np.zeros(int(16000 * seconds))).astype("<i2").tobytes()
    return voice_ws.pack({"type": "utterance", "turn": turn, "sample_rate": 16000}, pcm)


def receive(ws, until, limit=50):
    """Messages (dicts; audio frames as {"type": "audio", ..., "wav": bytes})
    up to and including the first of type `until`."""
    out = []
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            header, payload = voice_ws.unpack(msg["bytes"])
            out.append({**header, "wav": payload})
        elif msg.get("text") is not None:
            out.append(json.loads(msg["text"]))
        else:
            raise AssertionError(f"socket closed: {msg}")
        if out[-1]["type"] == until:
            return out
    raise AssertionError(f"no {until} in {[m['type'] for m in out]}")


# --- handshake ---------------------------------------------------------------------

def test_socket_needs_the_session_cookie(make_voice):
    _, http = make_voice()
    with pytest.raises(WebSocketDisconnect) as e:
        with connect(http):
            pass
    assert e.value.code == 1008
    http.cookies.set(webapp.COOKIE, "forged.abc")
    with pytest.raises(WebSocketDisconnect):
        with connect(http):
            pass


@pytest.mark.parametrize("headers", [{}, {"origin": "https://evil.example"},
                                     {"origin": "null"},
                                     {"origin": "https://testserver.evil.example"}])
def test_socket_needs_same_origin(make_voice, headers):
    _, http = make_voice()
    login(http)
    with pytest.raises(WebSocketDisconnect) as e:
        with connect(http, headers):
            pass
    assert e.value.code == 1008


def test_origin_rules():
    ok = voice_ws.origin_allowed
    assert ok({"origin": "https://pc.tail1234.ts.net", "host": "pc.tail1234.ts.net"})
    assert ok({"origin": "https://app.onrender.com", "host": "10.0.0.5:8765",
               "x-forwarded-host": "app.onrender.com"})
    assert ok({"origin": "https://phone.example/"}, ("https://phone.example",))
    assert not ok({"host": "x"})
    assert not ok({"origin": "file://", "host": ""})
    assert not ok({"origin": "https://x.example", "host": "y.example"})


# --- a whole turn ----------------------------------------------------------------

def test_streamed_turn_over_the_socket(make_voice):
    client, http = make_voice(streamed("Tomorrow you have the dentist at 3:30 PM. ",
                                       "Then lunch with Sam."))
    login(http)
    with connect(http) as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready"
        assert ready["settings"]["silence_ms"] == 700
        assert ready["settings"]["barge_in_ms"] == 250
        ws.send_bytes(utterance(1))
        msgs = receive(ws, "turn_done")
        ws.send_text(json.dumps({"type": "end"}))
        ended = receive(ws, "ended")[-1]
    types = [m["type"] for m in msgs]
    assert types[0] == "transcript" and msgs[0]["text"] == "what's on my calendar tomorrow"
    audio = [m for m in msgs if m["type"] == "audio"]
    assert [a["text"] for a in audio] == ["Tomorrow you have the dentist at 3:30 PM.",
                                          "Then lunch with Sam."]
    assert all(a["wav"][:4] == b"RIFF" for a in audio)
    assert types.index("audio") < types.index("turn_done")
    assert msgs[-1]["interrupted"] is False
    assert ended["reason"] == "user"
    assert client.stream_calls[0]["messages"][0]["content"] == \
        "what's on my calendar tomorrow"


def test_first_audio_latency(make_voice, capsys):
    """Server side, utterance in -> first sentence's audio out, with fake
    Whisper/Claude and the real TTS route (eSpeak) when it is installed."""
    from assistant.tts import Pyttsx3TTS

    real = voice_ws.synthesize_wav
    tts = Pyttsx3TTS() if shutil.which("espeak-ng") else PrintTTS()
    _, http = make_voice(streamed("Tomorrow you have the dentist at 3:30 PM. ",
                                  "Then lunch with Sam at noon."),
                         synth=lambda text: real(tts, text))
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        t0 = time.perf_counter()
        ws.send_bytes(utterance(1, 2.0))
        first = None
        for m in receive(ws, "turn_done"):
            if m["type"] == "audio" and first is None:
                first = time.perf_counter() - t0
        server = m["timings"]
    assert first is not None and first < 2.0
    assert server["audio"] < 2.0
    with capsys.disabled():
        print(f"\n[voice latency, {type(tts).__name__}] utterance -> first audio "
              f"frame {first * 1000:.0f} ms (server marks, s: {server})")


def test_barge_in_over_the_socket(make_voice):
    s = streamed("First one here. ", "Second one here. ", "Third", " not said.",
                 hold_after=3)
    client, http = make_voice(s, streamed("Okay, the weather."),
                              heard=("tell me something", "what's the weather"))
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1))
        got = []
        while len([m for m in got if m["type"] == "audio"]) < 2:
            got += receive(ws, "audio")
        ws.send_text(json.dumps({"type": "interrupt", "turn": 1, "index": 1,
                                 "fraction": 0.0}))
        done = receive(ws, "turn_done")[-1]
        assert done["interrupted"] is True and s.closed.is_set()
        ws.send_bytes(utterance(2))
        receive(ws, "turn_done")
    second = client.stream_calls[1]["messages"]
    assert second[1]["content"][0]["text"].startswith("First one here. [interrupted")
    assert second[-1]["content"] == "what's the weather"


def test_closing_the_socket_cancels_and_saves(make_voice):
    memory = MagicMock()
    memory.retrieve.return_value = []
    s = streamed("Okay. ", "Working on it. ", "Still", " going.", hold_after=3)
    client, http = make_voice(s, memory=memory)
    client.create_queue.append(make_response(text_block("Kyle asked about tomorrow.")))
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1))
        receive(ws, "audio")
    deadline = time.monotonic() + 5
    while not memory.store.called and time.monotonic() < deadline:
        time.sleep(0.02)
    assert s.closed.is_set()  # generation aborted server-side
    memory.store.assert_called_once()


@pytest.mark.parametrize("frame, code", [
    (voice_ws.pack({"type": "utterance", "turn": 1, "sample_rate": 16000},
                   b"\0" * (2 * 16000 * 31)), 1009),  # over max_utterance_s
    (voice_ws.pack({"type": "utterance", "turn": 1, "sample_rate": 44100}, b"\0\0"), 1003),
    (b"\xff\xff\xff\xff junk", 1003),
], ids=["too-long", "wrong-rate", "junk"])
def test_bad_or_oversized_frames_close_the_socket(make_voice, frame, code):
    _, http = make_voice()
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(frame)
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == code


def test_text_message_limits(make_voice):
    _, http = make_voice()
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_text("x" * 5000)
        assert ws.receive()["code"] == 1009
    with connect(http) as ws:
        ws.receive_text()
        for _ in range(voice_ws.CONTROL_MESSAGES_PER_10S + 1):
            ws.send_text(json.dumps({"type": "ping"}))
        msgs = [ws.receive() for _ in range(voice_ws.CONTROL_MESSAGES_PER_10S + 1)]
        assert msgs[-1]["type"] == "websocket.close" and msgs[-1]["code"] == 1008


def test_utterance_rate_limit(make_voice):
    _, http = make_voice(streamed("One."), streamed("Two."),
                         heard=("a", "b", "c"), utterances_per_minute=2)
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1, 0.2))
        receive(ws, "turn_done")
        ws.send_bytes(utterance(2, 0.2))
        receive(ws, "turn_done")
        ws.send_bytes(utterance(3, 0.2))
        msg = ws.receive()
        while msg["type"] != "websocket.close":
            msg = ws.receive()
        assert msg["code"] == 1008


def test_max_session_length_ends_it(make_voice):
    _, http = make_voice(max_session_minutes=0.02)  # 1.2 s
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        assert receive(ws, "ended")[-1]["reason"] == "max_length"


# --- page and vendored files ---------------------------------------------------

def test_vendored_vad_files_are_served_and_match_notice(make_voice):
    _, http = make_voice()
    notice = (ROOT / "assistant" / "web" / "vad" / "NOTICE.md").read_text()
    hashes = dict((name, h) for h, name in re.findall(r"([0-9a-f]{64})  (\S+)", notice))
    assert set(hashes) == set(voice_ws.VAD_FILES)
    for name, media in voice_ws.VAD_FILES.items():
        r = http.get(f"/static/vad/{name}")  # httpx decodes gzip
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith(media)
        assert hashlib.sha256(r.content).hexdigest() == hashes[name], name
    wasm = http.get("/static/vad/ort-wasm-simd-threaded.wasm",
                    headers={"accept-encoding": "gzip"})
    assert wasm.headers["content-encoding"] == "gzip"
    assert http.get("/static/vad/../webapp.py").status_code == 404
    assert http.get("/static/vad/nope.js").status_code == 404


def test_voice_page_files_and_csp(make_voice):
    _, http = make_voice()
    page = http.get("/")
    assert 'id="voice-open"' in page.text and 'src="/voice.js"' in page.text
    assert "<script>" not in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval';" in csp
    assert "connect-src 'self'" in csp and "unsafe-inline" not in csp
    for name in voice_ws.PAGE_FILES:
        assert http.get(f"/{name}").status_code == 200, name


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_javascript_unit_tests():
    result = subprocess.run(["node", "--test", "tests/js/voice-core.test.js"], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
