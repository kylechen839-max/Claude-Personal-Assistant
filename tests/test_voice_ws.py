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
             synth=None, transcriber=None, tools=None, **voice):
        client = stream_client(*streams)

        def chat_factory(registry):
            return ClaudeChat(client=client, model="test-model", tools=registry,
                              memory=memory)

        assistant = webapp.WebAssistant(chat_factory, transcriber or Transcriber(*heard),
                                         PrintTTS(), tools=tools)
        if synth:
            # (the session, and its language, are passed too: one voice here)
            monkeypatch.setattr(assistant, "synthesize",
                                lambda text, session=None, language=None: synth(text))
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


def utterance(turn, seconds=1.0, samples=None):
    if samples is None:
        samples = np.zeros(int(16000 * seconds))
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
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


def test_forwarded_host_needs_a_trusted_proxy(make_voice):
    _, http = make_voice()
    login(http)
    spoofed = {"origin": "https://evil.example", "x-forwarded-host": "evil.example"}
    with pytest.raises(WebSocketDisconnect) as e:
        with connect(http, spoofed):
            pass
    assert e.value.code == 1008
    http.app.state.web.trusted_proxy = webapp.parse_trusted_proxy(1)
    with connect(http, {"origin": "https://app.example",
                        "x-forwarded-host": "app.example"}) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"


def test_origin_rules():
    ok = voice_ws.origin_allowed
    assert ok({"origin": "https://pc.tail1234.ts.net", "host": "pc.tail1234.ts.net"})
    behind_proxy = {"origin": "https://app.onrender.com", "host": "10.0.0.5:8765",
                    "x-forwarded-host": "app.onrender.com"}
    assert ok(behind_proxy, trust_forwarded=True)
    assert not ok(behind_proxy)  # X-Forwarded-Host from an untrusted caller
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


def test_voice_mode_speaks_in_the_persons_own_voice(make_voice):
    """The voice picker (voice_picker.py): each sentence is rendered with
    the engine chosen for this socket's session, not the server default."""
    from types import SimpleNamespace

    class Engine:
        def __init__(self):
            self.said, self.on_audio_start = [], None

        def synthesize(self, text):
            self.said.append(text)
            return np.zeros(2205, dtype=np.float32), 22050

    _, http = make_voice(streamed("Hello there. ", "Bye now."))
    engine, asked = Engine(), []
    http.app.state.web.assistant.voices = SimpleNamespace(
        engine_for=lambda user, language: asked.append((user, language)) or engine)
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1))
        audio = [m for m in receive(ws, "turn_done") if m["type"] == "audio"]
        ws.send_text(json.dumps({"type": "end"}))
    assert all(a["wav"][:4] == b"RIFF" for a in audio)
    assert engine.said == ["Hello there.", "Bye now."]
    assert asked and asked[0] == (None, "en")  # the owner's session


def test_first_audio_latency(make_voice, capsys):
    """Server side, utterance in -> first sentence's audio out, with fake
    Whisper/Claude and the real TTS route (eSpeak) when it is installed."""
    from assistant.tts import Pyttsx3TTS

    real = webapp.render_wav
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


@pytest.mark.slow
def test_first_audio_latency_with_real_whisper(make_voice, capsys, tmp_path):
    """The definition-of-done question as a voice-mode utterance, through
    real Whisper (base.en, CPU) and the real TTS route; Claude is mocked.
    Runs in CI, where the model is downloaded."""
    from assistant import audio
    from assistant.stt import Transcriber, rough_match
    from assistant.tts import Pyttsx3TTS

    whisper = Transcriber()
    try:
        whisper.model
    except Exception as e:
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    wav = tmp_path / "q.wav"
    Pyttsx3TTS(rate=160).save_to_file("What is on my calendar tomorrow", wav)
    speech = audio.load_audio(wav)
    if not len(speech):
        pytest.skip("the OS voice wrote an empty clip")
    real = webapp.render_wav
    tts = Pyttsx3TTS()
    synth_s = []  # how long the OS voice took per sentence

    def synth(text):
        t = time.perf_counter()
        try:
            return real(tts, text)
        finally:
            synth_s.append(time.perf_counter() - t)

    _, http = make_voice(streamed("Tomorrow you have the dentist at 3:30 PM. ",
                                  "Then lunch with Sam."),
                         transcriber=whisper, synth=synth)
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        t0 = time.perf_counter()
        ws.send_bytes(utterance(1, samples=speech))
        msgs = receive(ws, "turn_done")
    first = next(time.perf_counter() - t0 for m in msgs if m["type"] == "audio")
    heard = next(m["text"] for m in msgs if m["type"] == "transcript")
    assert rough_match("What is on my calendar tomorrow", heard), heard
    with capsys.disabled():
        print(f"\n[voice latency, real Whisper + {type(tts).__name__}] utterance -> "
              f"first audio {msgs[-1]['timings']['audio']:.2f} s server-side "
              f"(transcript {msgs[-1]['timings']['transcript']:.2f} s, "
              f"OS voice for the first sentence {synth_s[0]:.2f} s)")
    # The budget is for our pipeline (Whisper, streaming, sentence split,
    # framing), not for the test's OS voice: Windows SAPI on a shared CI
    # runner alone took 3.3 s once. The phone's real voice is Kokoro/Piper.
    assert msgs[-1]["timings"]["audio"] - synth_s[0] < 2.0


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


@pytest.mark.parametrize("answer, deleted, barge_in", [
    ("yes", ["Dentist"], False), ("no", [], False),
    ("okay, and what about Friday?", [], False), ("yes", [], True)])
def test_spoken_confirmation_over_the_socket(make_voice, answer, deleted, barge_in):
    from assistant.tools import Tool, ToolRegistry
    from tests.conftest import tool_use_block

    done = []
    tools = ToolRegistry()
    tools.register(Tool(
        name="delete_event", description="Delete an event.",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        handler=lambda title: done.append(title) or {"deleted": title},
        confirm_prompt=lambda i: f"Do you want me to delete {i['title']}?"))
    client, http = make_voice(
        streamed("Okay.", extra=[tool_use_block("delete_event", {"title": "Dentist"})],
                 stop_reason="tool_use"),
        streamed("All done." if deleted else "Okay, I kept it."),
        heard=("delete my dentist appointment", answer), tools=tools)
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1))
        msgs = receive(ws, "confirm")
        assert msgs[-1]["question"] == "Do you want me to delete Dentist?"
        question = [m for m in receive(ws, "audio") if m["type"] == "audio"]
        while not question[-1]["text"].endswith("Please say yes or no."):
            question += [m for m in receive(ws, "audio") if m["type"] == "audio"]
        assert question[-1]["question"] is True
        if barge_in:  # Kyle talks over the question: the delete is cancelled
            ws.send_text(json.dumps({"type": "interrupt", "turn": 1,
                                     "index": question[-1]["index"], "fraction": 0.4}))
            rest = receive(ws, "turn_done")
        else:
            ws.send_text(json.dumps({"type": "played", "turn": 1,
                                     "index": question[-1]["index"]}))
            ws.send_bytes(utterance(2))
            rest = receive(ws, "turn_done")
    spoken = [m["text"] for m in question + rest if m["type"] == "audio"]
    assert ("OK, I won't delete it." in spoken) is (not deleted)
    ans = next(m for m in rest if m["type"] == "confirm_answer")
    assert ans["yes"] == bool(deleted) and ans["turn"] == 1
    assert done == deleted
    assert rest[-1]["turn"] == 1 and rest[-1]["interrupted"] is False
    asked = [m["content"] for call in client.stream_calls for m in call["messages"]
             if m["role"] == "user" and isinstance(m["content"], str)]
    assert answer not in asked  # the answer never went to Claude as a request


def test_voice_continues_the_push_to_talk_conversation(make_voice):
    client, http = make_voice(streamed("Hi Kyle."), streamed("You said hello."),
                              heard=("what did I just say",))
    login(http)
    r = http.post("/api/turn", json={"text": "hello"})
    assert r.status_code == 200
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1))
        receive(ws, "turn_done")
    second = client.stream_calls[-1]["messages"]
    assert [m["content"] for m in second if m["role"] == "user"] == \
        ["hello", "what did I just say"]


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


@pytest.mark.parametrize("msg", [{"type": "interrupt", "turn": "x"},
                                 {"type": "interrupt", "turn": 1, "fraction": [1]},
                                 {"type": "played", "turn": None}])
def test_malformed_control_message_closes_with_1003(make_voice, msg):
    _, http = make_voice()
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_text(json.dumps(msg))
        assert ws.receive()["code"] == 1003


def test_rate_limit_survives_reconnecting(make_voice):
    _, http = make_voice(streamed("One."), heard=("a", "b"), utterances_per_minute=1)
    login(http)
    with connect(http) as ws:
        ws.receive_text()
        ws.send_bytes(utterance(1, 0.2))
        receive(ws, "turn_done")
    with connect(http) as ws:  # a new socket doesn't get a fresh allowance
        ws.receive_text()
        ws.send_bytes(utterance(2, 0.2))
        msg = ws.receive()
        while msg["type"] != "websocket.close":
            msg = ws.receive()
        assert msg["code"] == 1008


def test_server_wide_cap_on_voice_conversations(make_voice, monkeypatch):
    monkeypatch.setattr(voice_ws, "MAX_CONVERSATIONS", 1)
    _, http = make_voice()
    login(http)
    other = TestClient(http.app, base_url=BASE)
    login(other)
    with connect(http) as ws:
        ws.receive_text()
        with pytest.raises(WebSocketDisconnect) as e:
            with connect(other):
                pass
        assert e.value.code == 1013


def test_conversation_slot_is_taken_before_accepting(make_voice, monkeypatch):
    """Two handshakes at once can't both pass the server-wide cap: the slot
    is reserved before the first await (the accept)."""
    from starlette.websockets import WebSocket

    _, http = make_voice()
    login(http)
    live = http.app.state.voice_conversations
    seen = []
    real_accept = WebSocket.accept

    async def accept(self, *a, **kw):
        seen.append(len(live))
        return await real_accept(self, *a, **kw)

    monkeypatch.setattr(WebSocket, "accept", accept)
    with connect(http) as ws:
        ws.receive_text()
    assert seen == [1]
    assert live == {}


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
