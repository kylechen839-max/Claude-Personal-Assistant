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
            tools=tools, clock=clock or Clock())
        kwargs.setdefault("limiter", webapp.LoginLimiter(per_ip=3, global_delay=0))
        app_options = {k: kwargs.pop(k) for k in list(kwargs)
                       if k in ("limiter", "trusted_proxy", "max_audio_seconds",
                                "sessions_file",
                                "max_text_chars", "max_upload_bytes")}
        assistant.__dict__.update(kwargs)  # e.g. conversation_timeout
        app = webapp.create_app(assistant, TOKEN, reap_interval=3600, **app_options)
        return assistant, TestClient(app, base_url=BASE)
    return make


def login(client):
    r = client.post("/api/login", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return r


def events(response, deltas=False):
    """The NDJSON events of a turn (text deltas left out unless asked)."""
    assert response.status_code == 200, response.text
    evs = [json.loads(line) for line in response.text.splitlines() if line]
    return evs if deltas else [e for e in evs if e["type"] != "delta"]


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
        r = client.post("/api/login", json={"token": "nope"})
        assert r.status_code == 401 and r.json() == {"error": "sign-in failed"}
    # this address is locked out for wrong guesses...
    assert client.post("/api/login", json={"token": "nope"}).status_code == 429
    assert webapp.COOKIE not in client.cookies
    # ...but the right token always gets in
    login(client)


def test_login_sets_a_strict_cookie(make_app):
    _, client = make_app()
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", "path=/"):
        assert attr in cookie
    assert TOKEN.lower() not in cookie  # the cookie is signed, not the token
    assert client.get("/api/state").json() == {
        "busy": False, "confirm": None, "turns": 0, "incognito": False,
        "history": False, "conversation": None}
    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_lockout_is_per_address_and_expires():
    clock = Clock()
    limiter = webapp.LoginLimiter(per_ip=2, window=60, lockout=300, clock=clock)
    limiter.fail("1.1.1.1"), limiter.fail("1.1.1.1")
    assert limiter.blocked("1.1.1.1") and not limiter.blocked("2.2.2.2")
    clock.now += 299
    assert limiter.blocked("1.1.1.1")
    clock.now += 2
    assert not limiter.blocked("1.1.1.1")


def test_many_wrong_tokens_slow_down_but_never_lock_everyone_out():
    clock = Clock()
    limiter = webapp.LoginLimiter(per_ip=5, global_free=4, global_delay=1.5,
                                  window=60, clock=clock)
    delays = [limiter.fail(f"10.0.0.{i}") for i in range(6)]  # many addresses
    assert delays == [0, 0, 0, 0, 1.5, 1.5]
    assert not limiter.blocked("9.9.9.9")  # nobody else is locked out
    clock.now += 61
    assert limiter.fail("10.0.1.1") == 0


def test_right_token_signs_in_after_a_flood_of_wrong_ones(make_app):
    """The review's case: 60 wrong tokens from many addresses must not
    lock Kyle out."""
    _, client = make_app(trusted_proxy=1)
    for i in range(60):
        r = client.post("/api/login", json={"token": f"guess-{i}"},
                        headers={"X-Forwarded-For": f"203.0.113.{i}"})
        assert r.status_code == 401
    r = client.post("/api/login", json={"token": TOKEN},
                    headers={"X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 200
    bearer = {"Authorization": f"Bearer {TOKEN}", "X-Forwarded-For": "198.51.100.8"}
    assert client.get("/api/state", headers=bearer).status_code == 200


def test_wrong_tokens_are_slowed_down_when_many_fail(make_app, monkeypatch):
    waits = []

    async def record(seconds):
        waits.append(seconds)

    monkeypatch.setattr(webapp, "_wait", record)
    _, client = make_app(limiter=webapp.LoginLimiter(per_ip=100, global_free=2,
                                                     global_delay=0.3))
    for _ in range(4):
        assert client.post("/api/login", json={"token": "nope"}).status_code == 401
    assert waits == [0.3, 0.3]  # only past the first two
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/api/state", headers=bad).status_code == 401
    assert waits == [0.3, 0.3, 0.3]  # wrong Bearer tokens too
    login(client)  # the right token isn't slowed down
    assert client.get("/api/state", headers={"Authorization": f"Bearer {TOKEN}"}
                      ).status_code == 200
    assert len(waits) == 3


def test_per_address_lockout_behind_a_trusted_proxy(make_app):
    _, client = make_app(trusted_proxy=1)
    attacker = {"X-Forwarded-For": "6.6.6.6"}
    for _ in range(3):
        r = client.post("/api/login", json={"token": "nope"}, headers=attacker)
        assert r.status_code == 401 and r.json() == {"error": "sign-in failed"}
    assert client.post("/api/login", json={"token": "guess"},
                       headers=attacker).status_code == 429
    # a forged left-hand entry doesn't dodge it: the proxy's entry counts
    forged = {"X-Forwarded-For": "1.2.3.4, 6.6.6.6"}
    assert client.post("/api/login", json={"token": "guess"},
                       headers=forged).status_code == 429
    kyle = {"X-Forwarded-For": "5.5.5.5"}
    assert client.post("/api/login", json={"token": "typo"},
                       headers=kyle).status_code == 401  # not locked out
    assert client.post("/api/login", json={"token": TOKEN},
                       headers=kyle).status_code == 200


def test_client_ip():
    conn = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"},
                           client=SimpleNamespace(host="127.0.0.1"))
    assert webapp.client_ip(conn, 0) == "127.0.0.1"  # headers not trusted
    assert webapp.client_ip(conn, 1) == "10.0.0.1"
    assert webapp.client_ip(conn, 2) == "1.2.3.4"
    assert webapp.client_ip(conn, 5) == "1.2.3.4"
    for value, hops in (("", 0), ("0", 0), ("false", 0), (None, 0),
                        ("true", 1), ("1", 1), ("2", 2), (True, 1), (3, 3)):
        assert webapp.parse_trusted_proxy(value).hops == hops


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
    r = client.post("/api/turn", json={"text": "hello"})
    evs = events(r)
    assert [e["type"] for e in evs] == ["reply", "done"]
    assert evs[0]["text"] == "Hi Kyle." and evs[0]["audio"].startswith("/api/audio/")
    deltas = [e["text"] for e in events(r, deltas=True) if e["type"] == "delta"]
    assert len(deltas) == 2 and "".join(deltas) == "Hi Kyle."
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
    sid = webapp.read_cookie(TOKEN, client.cookies[webapp.COOKIE],
                             client.app.state.web.store.epoch)
    s = assistant.session(sid)
    assert assistant.start_turn(s, text="go")
    assert client.post("/api/turn", json={"text": "again"}).status_code == 409
    gate.set()
    evs = events(client.get("/api/events"))
    assert evs[-2]["text"] == "Done." and evs[-1]["type"] == "done"


# --- public server hardening -------------------------------------------------------

PUBLIC = ["/", "/app.js", "/history.js", "/app.css", "/sw.js", "/manifest.webmanifest",
          "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png",
          "/silence.wav", "/healthz", "/backend-router.js",
          # voice conversation page files and the vendored VAD (voice_ws.py)
          "/voice.js", "/voice-core.js", "/voice-worklet.js", "/voice.css",
          "/static/vad/silero_vad_v5.onnx",
          # the voice view's earcons (assistant/earcons.py)
          "/earcons/listening.wav", "/earcons/thinking.wav", "/earcons/ended.wav",
          "/update-watch.js"]  # reloads the page after an update (host.py)


def test_only_the_page_pwa_files_and_healthz_are_public(make_app):
    assistant, client = make_app()
    for path in PUBLIC:
        assert client.get(path).status_code == 200, path
    # every other route needs the session cookie
    from fastapi.routing import APIRoute

    private = [(r.path, m) for r in client.app.routes if isinstance(r, APIRoute)
               for m in r.methods if r.path not in PUBLIC
               and not r.path.startswith("/icon-")
               and r.path not in ("/static/vad/{name}", "/earcons/{name}.wav")
               and r.path not in ("/api/login", "/api/logout")]
    assert len(private) >= 5
    for path, method in private:
        url = path.replace("{rid}", "1-x")
        r = client.request(method, url, json={"text": "hi"})
        assert r.status_code == 401, (method, path)
    assert assistant.sessions == {}


def test_healthz_says_nothing(make_app):
    _, client = make_app()
    r = client.get("/healthz")
    # only the code version (for the page's reload after an update)
    assert set(r.json()) == {"ok", "version"} and r.json()["ok"] is True
    assert "set-cookie" not in r.headers


def test_cookie_is_long_lived_and_renewed(make_app):
    _, client = make_app()
    login(client)
    r = client.get("/api/state")
    cookie = r.headers["set-cookie"].lower()
    assert f"max-age={90 * 24 * 3600}" in cookie and "samesite=strict" in cookie
    assert "set-cookie" not in client.get("/").headers  # only on the API


def test_https_redirect_and_hsts_behind_a_trusted_proxy(make_app):
    _, client = make_app(trusted_proxy=1)
    r = client.get("/", headers={"X-Forwarded-Proto": "http",
                                 "X-Forwarded-Host": "hey.example.com"},
                   follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "https://hey.example.com/"
    ok = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert ok.status_code == 200 and "max-age" in ok.headers["strict-transport-security"]
    # without a trusted proxy the header is ignored
    _, plain = make_app()
    assert plain.get("/", headers={"X-Forwarded-Proto": "http"},
                     follow_redirects=False).status_code == 200


def test_upload_limits(make_app):
    _, client = make_app(max_audio_seconds=1, max_text_chars=50)
    login(client)
    long = client.post("/api/turn", files={"audio": ("a.wav", wav_bytes(1.5), "audio/wav")})
    assert long.status_code == 413 and long.json() == {"error": "recording too long"}
    assert client.post("/api/turn", json={"text": "x" * 51}).status_code == 413
    big = client.post("/api/turn", content=b"{}",
                      headers={"Content-Type": "application/json",
                               "Content-Length": "999999999"})
    assert big.status_code == 413


def test_long_compressed_audio_is_cut_off_while_decoding():
    av = pytest.importorskip("av")
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="webm") as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        silence = np.zeros((1, 960), dtype=np.float32)
        for _ in range(50 * 5):  # 5 s of silence: compresses to almost nothing
            frame = av.AudioFrame.from_ndarray(silence, format="fltp", layout="mono")
            frame.rate = 48000
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    with pytest.raises(webapp.AudioTooLong):
        webapp.decode_upload(buf.getvalue(), max_seconds=2)
    assert len(webapp.decode_upload(buf.getvalue(), max_seconds=10)) > 4 * 16000


def test_errors_are_generic(make_app, monkeypatch):
    assistant, client = make_app()
    login(client)

    def boom(sid):
        raise RuntimeError("secret internals at /home/kyle")

    monkeypatch.setattr(assistant, "session", boom)
    client_no_raise = TestClient(client.app, base_url=BASE, raise_server_exceptions=False)
    client_no_raise.cookies.update(client.cookies)
    r = client_no_raise.get("/api/state")
    assert r.status_code == 500 and r.json() == {"error": "something went wrong"}
    assert client.get("/nope").json() == {"error": "not found"}
    assert client.get("/api/").status_code == 404
    assert client.get("/web/").status_code == 404  # no directory listing
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/icon-abc.png").json() == {"error": "bad request"}


def test_post_without_length_is_refused(make_app):
    _, client = make_app()

    def body():
        yield b'{"token": "x"}'

    r = client.post("/api/login", content=body(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 411


def test_public_host_needs_a_token_from_the_environment(monkeypatch, tmp_path):
    from assistant.config import load_config

    monkeypatch.delenv("WEB_TOKEN", raising=False)
    cfg = load_config(None, None)
    with pytest.raises(ValueError, match="WEB_TOKEN must be set"):
        webapp.serve(cfg, host="0.0.0.0", env_file=tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_port_and_trusted_proxy_from_environment(monkeypatch):
    from assistant.config import load_config

    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("TRUSTED_PROXY", "true")
    cfg = load_config(None, None)
    assert int(cfg["webapp"]["port"]) == 10000
    assert webapp.parse_trusted_proxy(cfg["webapp"]["trusted_proxy"]).hops == 1


def test_serve_behind_a_proxy(monkeypatch, tmp_path):
    from assistant.config import load_config

    monkeypatch.setenv("WEB_TOKEN", TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None)
    cfg["calendar"]["enabled"] = False
    cfg["webapp"]["trusted_proxy"] = "1"
    seen = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    out = []
    assert webapp.serve(cfg, host="0.0.0.0", port=10000, tts_name="print",
                        output_fn=out.append) == 0
    assert seen["host"] == "0.0.0.0" and seen["port"] == 10000
    assert seen["app"].state.web.trusted_proxy.hops == 1
    assert not any("WARNING" in line for line in out)
    assert TOKEN not in "\n".join(out)


def test_web_mode_does_not_load_audio_devices():
    """The server has no sound card: building the web app must not import
    sounddevice (PortAudio) or the wake-word stack."""
    import subprocess
    import sys

    code = (
        "import sys, os\n"
        "os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-test'\n"
        "for k in ('PINECONE_API_KEY', 'NOTION_TOKEN'): os.environ.pop(k, None)\n"
        "from assistant import cli, webapp\n"
        "from assistant.config import load_config\n"
        "cfg = load_config(None, None)\n"
        "cfg['calendar']['enabled'] = False\n"
        "a = webapp.build_assistant(cfg, 'print', output_fn=lambda m: None)\n"
        "webapp.create_app(a, 'x' * 40)\n"
        "bad = [m for m in ('sounddevice', 'openwakeword', 'pyttsx3') if m in sys.modules]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n")
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# --- pieces for other routes (voice mode) ---------------------------------------------

def test_chat_stream_yields_deltas(make_app, fake_client):
    fake_client.queue = [make_response(text_block("Sure, it's at three."))]
    assistant, _ = make_app()
    s = assistant.session("abc")
    assert "".join(assistant.chat_stream(s, "when?")) == "Sure, it's at three."
    assert s.chat.history[-1]["role"] == "assistant"
    assert not s.busy.locked()


def test_chat_stream_can_be_cancelled(make_app, fake_client):
    cancel = threading.Event()

    class SlowStream:  # the second piece only comes after the barge-in
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def text_stream(self):
            yield "One two t"
            cancel.wait(5)
            yield "hree four."

    fake_client.messages.stream.side_effect = lambda **kw: SlowStream()
    assistant, _ = make_app()
    s = assistant.session("abc")
    got = []
    for delta in assistant.chat_stream(s, "count", cancel=cancel):
        got.append(delta)
        cancel.set()  # barge in after the first piece
    assert got == ["One two t"]
    assert s.chat.history == [{"role": "user", "content": "count"},
                              {"role": "assistant", "content": "One two t"}]


def test_chat_stream_error_is_a_sentence(make_app, fake_client):
    import anthropic
    import httpx

    fake_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    assistant, _ = make_app()
    s = assistant.session("abc")
    assert list(assistant.chat_stream(s, "hi")) == [
        "Sorry, I can't reach the internet right now."]


def test_transcribe_upload_and_synthesize(make_app):
    stt = FakeTranscriber("hello")
    assistant, _ = make_app(transcriber=stt)
    assert assistant.transcribe_upload(wav_bytes()) == "hello"
    wav = assistant.synthesize("Hello there.")
    assert wav[:4] == b"RIFF" and len(wav) > 44


def test_require_session_on_http_and_websocket(make_app):
    from fastapi import Depends, WebSocket
    from starlette.websockets import WebSocketDisconnect

    _, client = make_app()
    app = client.app

    @app.get("/api/whoami")
    def whoami(session=Depends(webapp.require_session)):
        return {"sid": session.sid[:4]}

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket, session=Depends(webapp.require_session)):
        await websocket.accept()
        await websocket.send_json({"sid": session.sid[:4]})
        await websocket.close()

    assert client.get("/api/whoami").status_code == 401
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/ws") as conn:
            conn.receive_json()
    assert closed.value.code == 1008
    login(client)
    assert client.get("/api/whoami").status_code == 200
    # TestClient opens WebSockets as ws://, which won't carry a Secure cookie
    cookie = {"cookie": f"{webapp.COOKIE}={client.cookies[webapp.COOKIE]}"}
    with client.websocket_connect("/api/ws", headers=cookie) as conn:
        assert "sid" in conn.receive_json()
    # a page on another site can't open it, even with the cookie
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
                "/api/ws", headers={**cookie, "origin": "https://evil.example"}) as conn:
            conn.receive_json()
    assert closed.value.code == 1008


def test_bearer_token_for_other_clients(make_app, fake_client):
    fake_client.queue = [make_response(text_block("Hi from the API."))]
    _, client = make_app()
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/state", headers=good).status_code == 200
    evs = events(client.post("/api/turn", json={"text": "hi"}, headers=good))
    assert evs[0]["text"] == "Hi from the API."
    assert "set-cookie" not in client.get("/api/state", headers=good).headers
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(4):
        assert client.get("/api/state", headers=bad).status_code == 401
    assert client.app.state.web.limiter.blocked("testclient")  # counted
    # the right token still works
    assert client.get("/api/state", headers=good).status_code == 200


def test_trusted_proxy_by_address():
    """tailscale serve on the same PC: trust forwarded headers only from it."""
    trust = webapp.parse_trusted_proxy("127.0.0.1")
    assert trust.hops == 1 and trust.addresses == {"127.0.0.1"}
    both = webapp.parse_trusted_proxy("127.0.0.1, ::1")
    assert both.addresses == {"127.0.0.1", "::1"}
    assert webapp.parse_trusted_proxy(["127.0.0.1"]).addresses == {"127.0.0.1"}
    headers = {"x-forwarded-for": "100.64.0.7"}
    local = SimpleNamespace(headers=headers, client=SimpleNamespace(host="127.0.0.1"))
    other = SimpleNamespace(headers=headers, client=SimpleNamespace(host="192.168.1.9"))
    assert webapp.client_ip(local, trust) == "100.64.0.7"
    assert webapp.client_ip(other, trust) == "192.168.1.9"  # not the proxy: ignored
    with pytest.raises(ValueError, match="trusted_proxy"):
        webapp.parse_trusted_proxy("localhost-ish")


def test_redirect_only_from_the_trusted_address(make_app):
    # TestClient's peer address is "testclient"
    _, client = make_app(trusted_proxy="10.9.9.9")
    r = client.get("/", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 200 and "strict-transport-security" not in r.headers


def test_serve_accepts_a_proxy_address(monkeypatch, tmp_path):
    from assistant.config import load_config

    monkeypatch.setenv("WEB_TOKEN", TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TRUSTED_PROXY", "127.0.0.1")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None)
    cfg["calendar"]["enabled"] = False
    seen = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert webapp.serve(cfg, tts_name="print", env_file=tmp_path / ".env",
                        output_fn=lambda m: None) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["app"].state.web.trusted_proxy.addresses == {"127.0.0.1"}



# --- sign-out and revocation -----------------------------------------------------

def test_logout_revokes_the_cookie(make_app):
    _, client = make_app()
    login(client)
    stolen = client.cookies[webapp.COOKIE]
    assert client.post("/api/logout").status_code == 200
    thief = TestClient(client.app, base_url=BASE)
    thief.cookies.set(webapp.COOKIE, stolen)
    assert thief.get("/api/state").status_code == 401


def test_sign_out_everywhere(make_app):
    _, phone = make_app()
    laptop = TestClient(phone.app, base_url=BASE)
    login(phone), login(laptop)
    old_epoch = phone.app.state.web.store.epoch
    assert phone.post("/api/logout-all").status_code == 200
    assert phone.app.state.web.store.epoch != old_epoch
    assert laptop.get("/api/state").status_code == 401
    assert phone.get("/api/state").status_code == 401
    login(laptop)  # signing in again works
    assert laptop.get("/api/state").status_code == 200
    assert TestClient(phone.app, base_url=BASE).post("/api/logout-all").status_code == 401


def test_a_cookie_for_an_unknown_session_is_refused(make_app):
    """A correctly signed cookie isn't enough: the session must be listed."""
    _, client = make_app()
    store = client.app.state.web.store
    client.cookies.set(webapp.COOKIE, webapp.make_cookie(TOKEN, "made-up", store.epoch))
    assert client.get("/api/state").status_code == 401


def test_sessions_survive_a_restart(make_app, tmp_path):
    path = tmp_path / "data" / "web_sessions.json"
    _, client = make_app(sessions_file=path)
    login(client)
    cookie = client.cookies[webapp.COOKIE]
    assert TOKEN not in path.read_text()
    _, restarted = make_app(sessions_file=path)
    restarted.cookies.set(webapp.COOKIE, cookie)
    assert restarted.get("/api/state").status_code == 200
    restarted.post("/api/logout")
    _, again = make_app(sessions_file=path)
    again.cookies.set(webapp.COOKIE, cookie)
    assert again.get("/api/state").status_code == 401


def test_session_store_expiry_and_bad_file(tmp_path):
    clock = Clock()
    store = webapp.SessionStore(max_age=100, clock=clock)
    sid = store.create()
    clock.now += 90
    assert store.valid(sid)  # use pushes the expiry back
    clock.now += 90
    assert store.valid(sid)
    clock.now += 101
    assert not store.valid(sid)
    bad = tmp_path / "s.json"
    bad.write_text("not json")
    assert webapp.SessionStore(bad).sessions == {}


# --- review cycle 2 ------------------------------------------------------------

class FakeVoiceConversation:
    def __init__(self):
        self.ended = []

    def end(self, reason="user"):
        self.ended.append(reason)


def test_logout_hangs_up_that_browsers_voice_conversation(make_app):
    _, client = make_app()
    login(client)
    sid = client.app.state.web.sid(client)  # TestClient has .cookies/.headers
    mine, other = FakeVoiceConversation(), FakeVoiceConversation()
    client.app.state.voice_conversations.update({sid: (mine, None),
                                                 "someone-else": (other, None)})
    client.post("/api/logout")
    assert mine.ended == ["signed_out"] and other.ended == []


def test_sign_out_everywhere_hangs_up_every_voice_conversation(make_app):
    _, client = make_app()
    login(client)
    convs = [FakeVoiceConversation() for _ in range(3)]
    client.app.state.voice_conversations.update(
        {f"sid{i}": (c, None) for i, c in enumerate(convs)})
    assert client.post("/api/logout-all").status_code == 200
    assert all(c.ended == ["signed_out"] for c in convs)


def test_answers_are_bound_to_their_question(make_app):
    assistant, _ = make_app()
    s = assistant.session("abc")
    page_q = webapp.PendingConfirm("Delete the dentist?")
    s.pending = page_q
    # a late "no" from the voice view (no id) must not answer the page's question
    assert not assistant.answer(s, False)
    assert not assistant.answer(s, True, confirm_id="wrong")
    assert not page_q.answered.is_set()
    assert assistant.answer(s, True, confirm_id=page_q.id)
    assert page_q.answered.is_set() and page_q.answer
    voice_q = webapp.PendingConfirm("Delete lunch?", stream=True)
    s.pending = voice_q
    assert assistant.answer(s, False) and voice_q.answered.is_set()


def test_page_cannot_answer_the_voice_views_question(make_app):
    assistant, client = make_app()
    login(client)
    sid = client.app.state.web.sid(client)
    s = assistant.session(sid)
    s.pending = webapp.PendingConfirm("Delete lunch?", stream=True)
    assert client.post("/api/confirm", json={"answer": "yes"}).status_code == 409
    assert client.get("/api/state").json()["confirm"] is None
    assert not s.pending.answered.is_set()


def test_confirm_events_carry_an_id(make_app, fake_client):
    deleted = []
    fake_client.queue = [
        make_response(tool_use_block("delete_event", {"title": "Dentist"}),
                      stop_reason="tool_use"),
        make_response(text_block("Okay.")),
    ]
    assistant, client = make_app(tools=delete_registry(deleted))
    login(client)
    first = events(client.post("/api/turn", json={"text": "delete the dentist"}))
    sid = client.app.state.web.sid(client)
    assert first[-1]["id"] == assistant.session(sid).pending.id
    events(client.post("/api/confirm", json={"answer": "yes"}))
    assert deleted == ["Dentist"]


def test_limiter_memory_is_bounded_under_a_flood():
    clock = Clock()
    limiter = webapp.LoginLimiter(per_ip=3, global_free=20, window=900, clock=clock)
    for i in range(25_000):
        limiter.fail(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}")
        clock.now += 0.01
    assert len(limiter.all_failures) <= 21
    assert limiter.fail("1.1.1.1") > 0  # still slowing down
    for _ in range(10):
        limiter.fail("2.2.2.2")
    assert len(limiter.failures["2.2.2.2"]) == 3
    clock.now += 1000  # everything is old now: the next sweep drops it
    limiter._sweep_at = 0
    limiter.fail("3.3.3.3")
    assert len(limiter.failures) == 1


def test_a_failed_save_of_a_sign_out_is_retried(tmp_path, caplog):
    from pathlib import Path

    path = tmp_path / "sessions.json"
    store = webapp.SessionStore(path)
    keep, gone = store.create(), store.create()
    real_replace = Path.replace
    broken = {"on": True}

    def flaky_replace(self, target):
        if broken["on"]:
            raise OSError("disk full")
        return real_replace(self, target)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "replace", flaky_replace)
        with caplog.at_level("ERROR"):
            store.revoke(gone)
        assert "sign-out may be undone" in caplog.text
        assert gone in json.loads(path.read_text())["sessions"]  # not on disk yet
        broken["on"] = False
        assert store.valid(keep)  # the next request retries the save
    assert gone not in json.loads(path.read_text())["sessions"]
    assert not webapp.SessionStore(path).valid(gone)


@pytest.mark.parametrize("path", ["/api/logout", "/api/logout-all"])
def test_sign_out_closes_an_open_voice_socket(path, monkeypatch):
    """The real /api/voice socket: signing out ends it at once, and the
    revoked cookie can't open a new one."""
    from starlette.websockets import WebSocketDisconnect

    from tests.test_voice_ws import connect, make_voice, receive
    from tests.test_voice_ws import login as voice_login

    make = make_voice.__wrapped__(monkeypatch)
    _, http = make()
    voice_login(http)
    with connect(http) as ws:
        ws.receive_text()  # ready
        cookie = http.cookies.get(webapp.COOKIE)
        assert http.post(path).status_code == 200
        got = {}

        def read():  # in a thread, so a missing "ended" fails instead of hanging
            try:
                got["ended"] = receive(ws, "ended")[-1]
                ws.receive_text()
            except WebSocketDisconnect as e:
                got["closed"] = e.code

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(5)
        assert got.get("ended", {}).get("reason") == "signed_out", got
        assert "closed" in got
    http.cookies.set(webapp.COOKIE, cookie)  # the stolen-cookie case
    with pytest.raises(WebSocketDisconnect) as closed:
        with connect(http) as ws:
            ws.receive_text()
    assert closed.value.code == 1008
