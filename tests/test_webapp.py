"""The phone web app (--mode web): auth, upload, chat, TTS, confirmations.

Everything runs offline through FastAPI's TestClient: Anthropic is the
mocked client, Whisper a fake model (plus one real run when the model is
available), and TTS writes a real .wav.
"""

import io
import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import audio, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.tools import Tool, ToolRegistry  # noqa: E402
from assistant.tts import PrintTTS, Pyttsx3TTS  # noqa: E402
from tests.conftest import make_response, text_block, tool_use_block  # noqa: E402

TOKEN = "t" * 20 + "-test-token-0123456789abcdef"
BASE = "https://testserver"  # the session cookie is Secure


class FakeTranscriber:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.heard = []

    def transcribe(self, recording):
        self.heard.append(recording)
        return self.texts.pop(0)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def wav_bytes(seconds=0.5):
    buf = io.BytesIO()
    t = np.arange(int(audio.SAMPLE_RATE * seconds)) / audio.SAMPLE_RATE
    audio.write_wav(buf, 0.3 * np.sin(2 * np.pi * 440 * t))
    return buf.getvalue()


def delete_registry(deleted):
    reg = ToolRegistry()
    reg.register(Tool(
        name="delete_event", description="Delete an event.",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        handler=lambda title: deleted.append(title) or {"deleted": title},
        confirm_prompt=lambda i: f"Delete {i['title']}?"))
    return reg


@pytest.fixture
def make_app(fake_client, tmp_path):
    def make(transcriber=None, tts=None, tools=None, memory=None, clock=None,
             **kwargs):
        def chat_factory(registry):
            return ClaudeChat(client=fake_client, model="test-model",
                              tools=registry, memory=memory)

        assistant = webapp.WebAssistant(
            chat_factory, transcriber or FakeTranscriber(), tts or PrintTTS(),
            tools=tools, clock=clock or Clock(), **kwargs)
        app = webapp.create_app(assistant, TOKEN, limiter=webapp.LoginLimiter(
            max_failures=3, window=300), reap_interval=3600)
        return assistant, TestClient(app, base_url=BASE)
    return make


def login(client):
    r = client.post("/api/login", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return r


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


# --- auth -----------------------------------------------------------------------

def test_api_needs_a_token(make_app):
    _, client = make_app()
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/turn", json={"text": "hi"}).status_code == 401
    assert client.post("/api/confirm", json={"answer": "yes"}).status_code == 401
    assert client.get("/api/audio/1-x").status_code == 401
    assert client.get("/api/events").status_code == 401
    client.cookies.set(webapp.COOKIE, "forged.0123")
    assert client.get("/api/state").status_code == 401


def test_page_and_pwa_files_are_public(make_app):
    _, client = make_app()
    page = client.get("/")
    assert page.status_code == 200
    assert 'rel="manifest"' in page.text and "apple-mobile-web-app-capable" in page.text
    assert "<script>" not in page.text  # no inline script (CSP)
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    png = client.get("/icon-180.png")
    assert png.content.startswith(b"\x89PNG") and png.headers["content-type"] == "image/png"
    assert client.get("/app.js").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/icon-7.png").status_code == 404


def test_wrong_token_is_rejected_and_rate_limited(make_app):
    _, client = make_app()
    for _ in range(3):
        assert client.post("/api/login", json={"token": "nope"}).status_code == 401
    # locked now, even for the right token
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 429
    assert webapp.COOKIE not in client.cookies


def test_login_sets_a_strict_cookie(make_app):
    _, client = make_app()
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", "path=/"):
        assert attr in cookie
    assert TOKEN.lower() not in cookie  # the cookie is signed, not the token
    assert client.get("/api/state").json() == {"busy": False, "confirm": None,
                                                "turns": 0}
    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_rate_limit_window_expires():
    clock = Clock()
    limiter = webapp.LoginLimiter(max_failures=2, window=60, clock=clock)
    limiter.fail(), limiter.fail()
    assert limiter.blocked()
    clock.now += 61
    assert not limiter.blocked()


def test_cross_origin_requests_are_rejected(make_app):
    _, client = make_app()
    login(client)
    evil = client.post("/api/turn", json={"text": "hi"},
                       headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    site = client.post("/api/login", json={"token": TOKEN},
                       headers={"Sec-Fetch-Site": "cross-site"})
    assert site.status_code == 403
    assert client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"}
                      ).status_code == 403
    ok = client.get("/api/state", headers={"Origin": BASE})
    assert ok.status_code == 200


def test_is_cross_origin_behind_a_proxy():
    h = {"host": "127.0.0.1:8765", "origin": "https://pc.tail1234.ts.net"}
    assert webapp.is_cross_origin(h)
    assert not webapp.is_cross_origin({**h, "x-forwarded-host": "pc.tail1234.ts.net"})
    assert not webapp.is_cross_origin({**h, "sec-fetch-site": "same-origin"})
    assert not webapp.is_cross_origin(h, ("https://pc.tail1234.ts.net",))
    assert webapp.is_cross_origin({**h, "sec-fetch-site": "same-site"})


def test_security_headers(make_app):
    _, client = make_app()
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    login(client)
    assert client.get("/api/state").headers["cache-control"] == "no-store"


# --- token in .env ------------------------------------------------------------------

def test_token_is_generated_into_env_and_not_printed(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=x")  # no trailing newline
    environ, out = {}, []
    token = webapp.ensure_web_token(env_file, environ, out.append)
    assert len(token) >= 32 and environ["WEB_TOKEN"] == token
    text = env_file.read_text()
    assert "ANTHROPIC_API_KEY=x\n" in text and f"WEB_TOKEN={token}\n" in text
    assert token not in "".join(out)
    # second run: reuses it, nothing appended
    assert webapp.ensure_web_token(env_file, environ, out.append) == token
    assert env_file.read_text() == text


def test_short_token_is_refused(tmp_path):
    with pytest.raises(ValueError, match="too short"):
        webapp.ensure_web_token(tmp_path / ".env", {"WEB_TOKEN": "abc"})


def test_token_is_redacted_from_logs(monkeypatch):
    from assistant.config import redact

    monkeypatch.setenv("WEB_TOKEN", TOKEN)
    assert TOKEN not in redact(f"login with {TOKEN}")


# --- turns --------------------------------------------------------------------------

def test_text_chat_round_trip(make_app, fake_client):
    fake_client.queue = [make_response(text_block("Hi Kyle."))]
    _, client = make_app()
    login(client)
    evs = events(client.post("/api/turn", json={"text": "hello"}))
    assert [e["type"] for e in evs] == ["reply", "done"]
    assert evs[0]["text"] == "Hi Kyle." and evs[0]["audio"].startswith("/api/audio/")
    assert fake_client.calls[0]["messages"][0] == {"role": "user", "content": "hello"}
    assert client.get("/api/state").json()["turns"] == 1


def test_voice_upload_is_transcribed_and_answered(make_app, fake_client):
    fake_client.queue = [make_response(text_block("It's sunny."))]
    stt = FakeTranscriber("what's the weather")
    _, client = make_app(transcriber=stt)
    login(client)
    r = client.post("/api/turn", files={"audio": ("speech.wav", wav_bytes(), "audio/wav")})
    evs = events(r)
    assert evs[0] == {"type": "transcript", "text": "what's the weather"}
    assert evs[1]["text"] == "It's sunny."
    heard = stt.heard[0]
    assert heard.dtype == np.float32 and abs(len(heard) - 8000) < 50  # 0.5 s @ 16 kHz


def test_tool_call_round_trip(make_app, fake_client):
    reg = ToolRegistry()
    reg.register(Tool(name="list_events", description="List events.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: [{"summary": "Dentist", "start": "9:00"}]))
    fake_client.queue = [
        make_response(tool_use_block("list_events", {}), stop_reason="tool_use"),
        make_response(text_block("You have the dentist at nine.")),
    ]
    _, client = make_app(tools=reg)
    login(client)
    evs = events(client.post("/api/turn", json={"text": "what's tomorrow?"}))
    assert evs[0] == {"type": "tool", "name": "list_events"}
    assert evs[1]["text"] == "You have the dentist at nine."
    assert fake_client.calls[0]["tools"][0]["name"] == "list_events"
    result = fake_client.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "Dentist" in result["content"]


def test_api_error_gives_a_spoken_sentence(make_app, fake_client):
    import anthropic
    import httpx

    fake_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    _, client = make_app()
    login(client)
    evs = events(client.post("/api/turn", json={"text": "hello"}))
    assert evs[0]["text"] == "Sorry, I can't reach the internet right now."


def test_bad_requests(make_app):
    _, client = make_app()
    login(client)
    assert client.post("/api/turn", json={"text": "  "}).status_code == 400
    assert client.post("/api/turn", files={"audio": ("x.m4a", b"not audio", "audio/mp4")}
                       ).status_code == 400
    assert client.post("/api/turn", content=b"x",
                       headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/confirm", json={"answer": "yes"}).status_code == 409


def test_upload_size_limit():
    small = webapp.create_app(
        webapp.WebAssistant(lambda r: None, FakeTranscriber(), PrintTTS()), TOKEN,
        max_upload_bytes=1000)
    c = TestClient(small, base_url=BASE)
    login(c)
    r = c.post("/api/turn", files={"audio": ("speech.wav", wav_bytes(), "audio/wav")})
    assert r.status_code == 413


def test_goodbye_ends_the_conversation(make_app, fake_client):
    memory = SimpleNamespace(retrieve=lambda q: [], stored=[])
    memory.store = memory.stored.append
    fake_client.queue = [make_response(text_block("Sure.")),
                         make_response(text_block("Kyle asked something."))]
    _, client = make_app(memory=memory)
    login(client)
    events(client.post("/api/turn", json={"text": "remember the milk"}))
    evs = events(client.post("/api/turn", json={"text": "goodbye"}))
    assert evs[0]["text"] == "Goodbye!" and evs[0]["ended"]
    assert memory.stored and memory.stored[0].endswith("Kyle asked something.")
    assert client.get("/api/state").json()["turns"] == 0


def test_idle_conversation_is_summarized_to_memory(make_app, fake_client):
    memory = SimpleNamespace(retrieve=lambda q: [], stored=[])
    memory.store = memory.stored.append
    clock = Clock()
    fake_client.queue = [make_response(text_block("Done.")),
                         make_response(text_block("Kyle set a reminder."))]
    assistant, client = make_app(memory=memory, clock=clock,
                                 conversation_timeout=300)
    login(client)
    events(client.post("/api/turn", json={"text": "remind me"}))
    assert assistant.reap() == 0
    clock.now += 301
    assert assistant.reap() == 1
    assert memory.stored == [memory.stored[0]] and "reminder" in memory.stored[0]
    assert client.get("/api/state").json()["turns"] == 0


def test_each_browser_has_its_own_conversation(make_app, fake_client):
    fake_client.queue = [make_response(text_block("A.")), make_response(text_block("B."))]
    assistant, first = make_app()
    second = TestClient(first.app, base_url=BASE)
    login(first), login(second)
    events(first.post("/api/turn", json={"text": "one"}))
    events(second.post("/api/turn", json={"text": "two"}))
    assert len(assistant.sessions) == 2
    assert fake_client.calls[1]["messages"] == [{"role": "user", "content": "two"}]


# --- delete confirmation --------------------------------------------------------------

def confirm_flow(make_app, fake_client, answer, transcriber=None):
    deleted = []
    fake_client.queue = [
        make_response(tool_use_block("delete_event", {"title": "Dentist"}),
                      stop_reason="tool_use"),
        make_response(text_block("Okay.")),
    ]
    _, client = make_app(tools=delete_registry(deleted), transcriber=transcriber)
    login(client)
    first = events(client.post("/api/turn", json={"text": "delete the dentist"}))
    assert [e["type"] for e in first] == ["tool", "confirm"]
    assert first[1]["question"] == "Delete Dentist?"
    assert client.get("/api/state").json()["confirm"] == "Delete Dentist?"
    assert client.get(first[1]["audio"]).content[:4] == b"RIFF"
    # a new turn can't start while the question is open
    assert client.post("/api/turn", json={"text": "hi"}).status_code == 409
    rest = events(answer(client))
    result = fake_client.calls[1]["messages"][-1]["content"][0]["content"]
    return deleted, rest, result


def test_delete_confirmed_with_the_yes_button(make_app, fake_client):
    deleted, rest, result = confirm_flow(
        make_app, fake_client, lambda c: c.post("/api/confirm", json={"answer": "yes"}))
    assert deleted == ["Dentist"]
    assert [e["type"] for e in rest] == ["reply", "done"]


def test_delete_declined_with_the_no_button(make_app, fake_client):
    deleted, rest, result = confirm_flow(
        make_app, fake_client, lambda c: c.post("/api/confirm", json={"answer": "no"}))
    assert deleted == [] and "cancelled" in result


def test_delete_confirmed_by_voice(make_app, fake_client):
    stt = FakeTranscriber("yes, go ahead")
    deleted, rest, _ = confirm_flow(
        make_app, fake_client,
        lambda c: c.post("/api/confirm",
                         files={"audio": ("yes.wav", wav_bytes(), "audio/wav")}),
        transcriber=stt)
    assert deleted == ["Dentist"]
    assert rest[0] == {"type": "transcript", "text": "yes, go ahead"}


def test_unclear_voice_answer_is_no(make_app, fake_client):
    stt = FakeTranscriber("yes but no wait")
    deleted, _, _ = confirm_flow(
        make_app, fake_client,
        lambda c: c.post("/api/confirm",
                         files={"audio": ("a.wav", wav_bytes(), "audio/wav")}),
        transcriber=stt)
    assert deleted == []


def test_unanswered_confirmation_times_out_as_no(make_app, fake_client):
    deleted = []
    fake_client.queue = [
        make_response(tool_use_block("delete_event", {"title": "Dentist"}),
                      stop_reason="tool_use"),
        make_response(text_block("Okay, I left it.")),
    ]
    assistant, client = make_app(tools=delete_registry(deleted), confirm_timeout=0.2)
    login(client)
    events(client.post("/api/turn", json={"text": "delete the dentist"}))
    rest = events(client.get("/api/events"))
    assert [e["type"] for e in rest] == ["status", "reply", "done"]
    assert deleted == []


# --- audio ----------------------------------------------------------------------------

def test_reply_audio_is_a_wav(make_app, fake_client):
    fake_client.queue = [make_response(text_block("Hello there."))]
    _, client = make_app()
    login(client)
    evs = events(client.post("/api/turn", json={"text": "hi"}))
    r = client.get(evs[0]["audio"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF" and len(r.content) > 44
    assert client.get("/api/audio/999-nope").status_code == 404


@pytest.mark.slow
def test_reply_audio_with_the_real_tts(make_app, fake_client):
    fake_client.queue = [make_response(text_block("You have two meetings tomorrow."))]
    _, client = make_app(tts=Pyttsx3TTS(rate=170))
    login(client)
    evs = events(client.post("/api/turn", json={"text": "tomorrow?"}))
    r = client.get(evs[0]["audio"])
    assert r.status_code == 200
    samples, _ = audio.read_wav(io.BytesIO(r.content))
    assert len(samples) > 1000 and np.abs(samples).max() > 0.01


def test_decode_mp4_upload_like_ios_safari():
    """iOS records AAC in MP4; PyAV (via faster-whisper) decodes it."""
    av = pytest.importorskip("av")
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as out:
        stream = out.add_stream("aac", rate=44100)
        stream.layout = "mono"
        t = np.arange(44100) / 44100
        samples = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        for i in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(samples[None, i:i + 1024],
                                               format="fltp", layout="mono")
            frame.rate = 44100
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    data = buf.getvalue()
    assert data[4:8] == b"ftyp"
    decoded = webapp.decode_upload(data)
    assert decoded.dtype == np.float32
    assert abs(len(decoded) - 16000) < 2000 and np.abs(decoded).max() > 0.1


def test_decode_wav_upload():
    decoded = webapp.decode_upload(wav_bytes(1.0))
    assert len(decoded) == 16000
    assert len(webapp.decode_upload(b"")) == 0


@pytest.mark.slow
def test_real_whisper_transcribes_an_uploaded_recording(make_app, fake_client):
    from assistant.stt import Transcriber, rough_match

    whisper = Transcriber()
    try:
        whisper.model
    except Exception as e:  # no network / model not cached
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    phrase = "What is on my calendar tomorrow"
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "phrase.wav"
        Pyttsx3TTS(rate=160).save_to_file(phrase, wav)
        data = wav.read_bytes()
    fake_client.queue = [make_response(text_block("Nothing tomorrow."))]
    _, client = make_app(transcriber=whisper)
    login(client)
    evs = events(client.post("/api/turn",
                             files={"audio": ("speech.wav", data, "audio/wav")}))
    assert evs[0]["type"] == "transcript"
    assert rough_match(phrase, evs[0]["text"]), evs[0]["text"]


# --- startup --------------------------------------------------------------------------

def test_icon_png_is_valid():
    import struct
    import zlib

    png = webapp.icon_png(32)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (32, 32)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert len(raw) == 32 * (1 + 32 * 3)


def test_loopback_detection():
    assert webapp.is_loopback("127.0.0.1") and webapp.is_loopback("localhost")
    assert webapp.is_loopback("::1")
    assert not webapp.is_loopback("0.0.0.0") and not webapp.is_loopback("192.168.1.5")


def test_cli_web_mode_starts_the_server(monkeypatch, tmp_path, restore_root_logging):
    from assistant import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    seen = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        client = TestClient(app, base_url=BASE)
        assert client.get("/api/state").status_code == 401
        token = (tmp_path / ".env").read_text().split("WEB_TOKEN=")[1].strip()
        assert client.post("/api/login", json={"token": token}).status_code == 200

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    out = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a))))
    assert cli.main(["--mode", "web", "--port", "9999", "--tts", "print"]) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9999
    token = (tmp_path / ".env").read_text().split("WEB_TOKEN=")[1].strip()
    assert token not in "\n".join(out)
    log_text = (tmp_path / "logs" / "assistant.log").read_text()
    assert token not in log_text


def test_open_in_browser_is_not_offered(monkeypatch):
    from assistant.config import load_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None)
    cfg["calendar"]["enabled"] = False
    assistant = webapp.build_assistant(cfg, "print", output_fn=lambda m: None)
    assert not assistant.tools.has("open_in_browser")
    assert assistant.tools.has("web_search")


def test_doctor_reports_web_token():
    from assistant import doctor

    checks = {c.name: c for c in doctor.check_web({"WEB_TOKEN": TOKEN})}
    assert checks["WEB_TOKEN"].ok and checks["WEB_TOKEN"].detail == "set"
    assert TOKEN not in str(checks)
    assert not doctor.check_web({})[0].ok
    assert not doctor.check_web({"WEB_TOKEN": "short"})[0].ok
    missing = doctor.check_web({}, find_spec=lambda name: None)[1]
    assert not missing.ok and not missing.required


def test_concurrent_turns_are_refused(make_app, fake_client):
    gate = threading.Event()
    reg = ToolRegistry()
    reg.register(Tool(name="slow", description="Slow.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: gate.wait(5) and "ok"))
    fake_client.queue = [make_response(tool_use_block("slow", {}), stop_reason="tool_use"),
                         make_response(text_block("Done."))]
    assistant, client = make_app(tools=reg)
    login(client)
    sid = webapp.read_cookie(TOKEN, client.cookies[webapp.COOKIE])
    s = assistant.session(sid)
    assert assistant.start_turn(s, text="go")
    assert client.post("/api/turn", json={"text": "again"}).status_code == 409
    gate.set()
    evs = events(client.get("/api/events"))
    assert evs[-2]["text"] == "Done." and evs[-1]["type"] == "done"
