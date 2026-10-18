"""Natural voices: Kokoro, the optional ElevenLabs voice, engine selection
and fallback, the checksummed download, warm-up, earcons."""

import hashlib
import io
import logging
import os
import re
import statistics
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

from assistant import audio, cli, earcons, tts, tts_elevenlabs, tts_kokoro
from assistant.config import DEFAULTS, load_config
from assistant.tts import PiperTTS, PrintTTS, Pyttsx3TTS, make_tts
from assistant.tts_elevenlabs import ElevenLabsTTS
from assistant.tts_kokoro import KokoroTTS

ROOT = Path(__file__).resolve().parent.parent


# --- fakes ---------------------------------------------------------------------

@pytest.fixture
def fake_kokoro(monkeypatch):
    """A stand-in kokoro_onnx package; records every create() call."""
    calls = []

    class Kokoro:
        def __init__(self, model, voices):
            calls.append(("load", model, voices))

        def get_voices(self):
            return ["af_heart", "af_bella", "bf_emma", "am_michael"]

        def create(self, text, voice, speed, lang):
            calls.append(("create", text, voice, speed, lang))
            return np.full(2400, 0.25, dtype=np.float32), 24000

    module = types.ModuleType("kokoro_onnx")
    module.Kokoro = Kokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)
    return calls


@pytest.fixture
def kokoro_paths(tmp_path):
    model, voices = tmp_path / "k.onnx", tmp_path / "v.bin"
    model.write_bytes(b"onnx")
    voices.write_bytes(b"voices")
    return {"kokoro_model": str(model), "kokoro_voices": str(voices)}


@pytest.fixture
def piper_paths(tmp_path, monkeypatch):
    voice = tmp_path / "en_US-test-medium.onnx"
    voice.write_bytes(b"onnx")
    voice.with_name(voice.name + ".json").write_text("{}")
    monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
    return {"piper_voice": str(voice)}


@pytest.fixture
def no_kokoro(tmp_path):
    return {"kokoro_model": str(tmp_path / "none.onnx"),
            "kokoro_voices": str(tmp_path / "none.bin")}


def tts_logs(caplog):
    return [r for r in caplog.records if r.name.startswith("assistant.tts")]


# --- selection and fallback ------------------------------------------------------

def test_auto_prefers_kokoro(fake_kokoro, kokoro_paths, piper_paths, monkeypatch, caplog):
    monkeypatch.setattr(tts, "_is_pi_class", lambda: False)
    with caplog.at_level(logging.INFO):
        engine = make_tts("auto", **kokoro_paths, **piper_paths, kokoro_voice="bf_emma",
                          speed=1.1)
    assert isinstance(engine, KokoroTTS)
    assert engine.voice == "bf_emma" and engine.speed == 1.1
    assert fake_kokoro == []  # loads lazily (or on warm_up)
    assert len(tts_logs(caplog)) == 1 and "Kokoro" in caplog.text


def test_auto_falls_back_to_piper_then_pyttsx3(no_kokoro, piper_paths, caplog):
    with caplog.at_level(logging.INFO):
        assert isinstance(make_tts("auto", **no_kokoro, **piper_paths), PiperTTS)
    assert len(tts_logs(caplog)) == 1 and "Piper" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO):
        engine = make_tts("auto", **no_kokoro, piper_voice="", rate=150)
    assert isinstance(engine, Pyttsx3TTS) and engine.rate == 150
    [line] = tts_logs(caplog)  # one line saying why
    assert "not found" in line.getMessage()
    assert "--download-voice kokoro" in line.getMessage()


def test_auto_on_a_pi_keeps_piper(fake_kokoro, kokoro_paths, piper_paths, monkeypatch):
    monkeypatch.setattr(tts, "_is_pi_class", lambda: True)
    assert isinstance(make_tts("auto", **kokoro_paths, **piper_paths), PiperTTS)
    # Kokoro on a Pi is still used when it's the only neural voice
    assert isinstance(make_tts("auto", **kokoro_paths, piper_voice=""), KokoroTTS)


def test_explicit_kokoro_falls_back(no_kokoro, piper_paths, caplog):
    with caplog.at_level(logging.WARNING):
        assert isinstance(make_tts("kokoro", **no_kokoro, **piper_paths), PiperTTS)
    assert "Kokoro unavailable" in caplog.text and "--download-voice kokoro" in caplog.text


def test_kokoro_problem(kokoro_paths, fake_kokoro, monkeypatch):
    m, v = kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"]
    assert tts_kokoro.kokoro_problem(m, v) is None
    assert "not found" in tts_kokoro.kokoro_problem(m + "x", v)
    assert "no model set" in tts_kokoro.kokoro_problem("", v)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    assert "not installed" in tts_kokoro.kokoro_problem(m, v)


def test_engine_names_and_cli_choices():
    assert set(tts.ENGINES) == {"auto", "kokoro", "piper", "pyttsx3", "elevenlabs", "print"}
    assert cli.parse_args(["--tts", "kokoro"]).tts == "kokoro"
    assert isinstance(make_tts("print"), PrintTTS)


def test_config_defaults_and_file():
    t = DEFAULTS["tts"]
    assert t["engine"] == "auto" and t["kokoro_voice"] == "af_heart" and t["speed"] == 1.0
    assert t["kokoro_model"].endswith("kokoro-v1.0.int8.onnx")
    cfg = load_config(ROOT / "config.toml", None)["tts"]
    assert cfg["engine"] == "auto" and cfg["kokoro_voice"] == "af_heart"
    assert cfg["kokoro_model"] == t["kokoro_model"]
    assert cfg["kokoro_voices"] == t["kokoro_voices"]


def test_config_switches_voice(tmp_path, fake_kokoro, kokoro_paths):
    toml = tmp_path / "config.toml"
    toml.write_text('[tts]\nengine = "kokoro"\nkokoro_voice = "am_michael"\nspeed = 0.9\n'
                    f'kokoro_model = "{Path(kokoro_paths["kokoro_model"]).as_posix()}"\n'
                    f'kokoro_voices = "{Path(kokoro_paths["kokoro_voices"]).as_posix()}"\n')
    cfg = load_config(toml, None)["tts"]
    engine = make_tts(cfg["engine"], **cfg)
    assert isinstance(engine, KokoroTTS)
    engine.synthesize("Hi.")
    assert fake_kokoro[-1] == ("create", "Hi.", "am_michael", 0.9, "en-us")


# --- Kokoro engine (fake model) ------------------------------------------------

def test_kokoro_synthesize_voice_lang_speed(fake_kokoro, kokoro_paths):
    k = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"],
                  voice="bf_emma", speed=5)
    samples, sr = k.synthesize("Good morning.")
    assert sr == 24000 and samples.dtype == np.float32 and len(samples) == 2400
    assert fake_kokoro[-1] == ("create", "Good morning.", "bf_emma", 2.0, "en-gb")
    assert len(k.synthesize("   ")[0]) == 0  # nothing to say: no model call
    assert sum(c[0] == "load" for c in fake_kokoro) == 1


def test_kokoro_unknown_voice_uses_default(fake_kokoro, kokoro_paths, caplog):
    k = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"],
                  voice="xx_nobody")
    with caplog.at_level(logging.WARNING):
        k.synthesize("Hi.")
    assert k.voice == "af_heart" and "no voice 'xx_nobody'" in caplog.text


def test_kokoro_speaks_sentence_by_sentence(fake_kokoro, kokoro_paths, monkeypatch, tmp_path):
    played, started = [], []
    monkeypatch.setattr(audio, "play", lambda s, sr=16000: played.append(sr))
    k = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"])
    k.on_audio_start = lambda: started.append(1)
    k.speak("It's sunny today. Tomorrow it rains at 3 p.m. in Atlanta. Bring a coat!")
    texts = [c[1] for c in fake_kokoro if c[0] == "create"]
    assert texts == ["It's sunny today.", "Tomorrow it rains at 3 p.m. in Atlanta.",
                     "Bring a coat!"]
    assert played == [24000] * 3 and started == [1]
    out = tmp_path / "k.wav"
    k.save_to_file("Hello.", out)
    samples, sr = audio.read_wav(out)
    assert sr == 24000 and len(samples) == 2400


def test_kokoro_speak_reraises_render_errors(fake_kokoro, kokoro_paths, monkeypatch):
    k = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"])
    monkeypatch.setattr(k, "synthesize", lambda t: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        k.speak("Hello there.")


def test_web_renders_kokoro_in_memory_and_warms_up(fake_kokoro, kokoro_paths):
    from assistant.webapp import WebAssistant, render_wav

    k = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"])
    data = render_wav(k, "Hi there.")
    samples, sr = audio.read_wav(io.BytesIO(data))
    assert data[:4] == b"RIFF" and sr == 24000 and len(samples) == 2400
    fake_kokoro.clear()
    web = WebAssistant(lambda registry: None, transcriber=None, tts=k)
    try:
        web.synthesize("Ready.")  # queued behind the warm-up in the TTS thread
    finally:
        web.shutdown()
    assert [c[1] for c in fake_kokoro if c[0] == "create"] == ["Hello.", "Ready."]


def test_cli_warms_up_the_voice(monkeypatch, tmp_path, restore_root_logging):
    warmed = []

    class Engine(PrintTTS):
        def warm_up(self):
            warmed.append(1)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(cli, "make_tts", lambda *a, **k: Engine())
    monkeypatch.setattr(cli, "build_tools", lambda *a, **k: cli.ToolRegistry())
    monkeypatch.setattr(cli, "build_memory", lambda *a, **k: None)
    monkeypatch.setattr(cli, "build_chat", lambda *a, **k: object())
    monkeypatch.setattr(cli, "Transcriber", lambda *a, **k: object())
    monkeypatch.setattr(cli, "run_voice", lambda *a, **k: None)
    toml = tmp_path / "c.toml"
    toml.write_text(f'[logging]\nfile = "{(tmp_path / "a.log").as_posix()}"\n')
    assert cli.main(["--mode", "voice", "--config", str(toml)]) in (0, None)
    for _ in range(100):
        if warmed:
            break
        time.sleep(0.01)
    assert warmed == [1]


# --- download ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body: bytes, status=200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status != 200:
            raise OSError(f"HTTP {self.status}")

    def iter_content(self, n):
        for i in range(0, len(self.body), 7):
            yield self.body[i:i + 7]


@pytest.fixture
def fake_release(monkeypatch):
    """Kokoro release files with known bodies; requests.get mocked."""
    import requests

    bodies = {"kokoro-v1.0.int8.onnx": b"model-bytes" * 10,
              "kokoro-v1.0.fp16.onnx": b"fp16-bytes" * 10,
              "voices-v1.0.bin": b"voice-bytes" * 10}
    monkeypatch.setattr(tts_kokoro, "FILES", {
        n: (hashlib.sha256(b).hexdigest(), len(b)) for n, b in bodies.items()})
    fetched = []

    def get(url, stream, timeout):
        assert stream and timeout and url.startswith(tts_kokoro.RELEASE + "/")
        name = url.rsplit("/", 1)[1]
        fetched.append(name)
        return FakeResponse(bodies.get(name, b"?"))

    monkeypatch.setattr(requests, "get", get)
    return bodies, fetched


def test_download_verifies_checksums(tmp_path, fake_release):
    bodies, fetched = fake_release
    model, voices = tts_kokoro.download(tmp_path / "k", output_fn=lambda m: None)
    assert model.read_bytes() == bodies["kokoro-v1.0.int8.onnx"]
    assert voices.read_bytes() == bodies["voices-v1.0.bin"]
    assert fetched == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
    # verified files are kept; a corrupted one is fetched again
    tts_kokoro.download(tmp_path / "k", output_fn=lambda m: None)
    assert len(fetched) == 2
    voices.write_bytes(b"truncated")
    tts_kokoro.download(tmp_path / "k", output_fn=lambda m: None)
    assert fetched[2:] == ["voices-v1.0.bin"] and voices.read_bytes() == bodies["voices-v1.0.bin"]
    m2, _ = tts_kokoro.download(tmp_path / "k", variant="fp16", output_fn=lambda m: None)
    assert m2.name == "kokoro-v1.0.fp16.onnx"
    with pytest.raises(ValueError):
        tts_kokoro.download(tmp_path / "k", variant="fp64")


def test_download_rejects_a_bad_checksum(tmp_path, fake_release, monkeypatch):
    bodies, _ = fake_release
    files = dict(tts_kokoro.FILES)
    files["kokoro-v1.0.int8.onnx"] = ("0" * 64, 1)
    monkeypatch.setattr(tts_kokoro, "FILES", files)
    with pytest.raises(ValueError, match="checksum mismatch"):
        tts_kokoro.download(tmp_path / "k", output_fn=lambda m: None)
    assert list((tmp_path / "k").iterdir()) == []  # no .part, no model


def test_download_http_error_leaves_nothing(tmp_path, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(b"", 404))
    with pytest.raises(OSError):
        tts_kokoro.download(tmp_path / "k", output_fn=lambda m: None)
    assert list((tmp_path / "k").iterdir()) == []


def test_pinned_checksums_look_right():
    for name, (digest, size) in tts_kokoro.FILES.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest) and size > 10_000_000, name
    assert tts_kokoro.RELEASE.startswith("https://github.com/")


def test_download_voice_command(monkeypatch, tmp_path, restore_root_logging):
    toml = tmp_path / "c.toml"
    toml.write_text(f'[logging]\nfile = "{(tmp_path / "a.log").as_posix()}"\n')
    calls = []
    monkeypatch.setattr(tts_kokoro, "download", lambda variant, output_fn: (
        calls.append(variant) or (Path("models/kokoro/m.onnx"), Path("models/kokoro/v.bin"))))
    monkeypatch.setattr(tts, "download_voice", lambda name: (
        calls.append(name) or Path(f"voices/{name}.onnx")))
    out = []
    assert cli.download_voice_command("kokoro", out.append) == 0
    assert cli.download_voice_command("kokoro-fp16", out.append) == 0
    assert cli.download_voice_command("en_US-lessac-medium", out.append) == 0
    assert calls == ["int8", "fp16", "en_US-lessac-medium"]
    assert "checksums ok" in out[0] and 'engine = "piper"' in out[-1]
    monkeypatch.setattr(tts_kokoro, "download", lambda **k: (_ for _ in ()).throw(
        ValueError("checksum mismatch for x")))
    assert cli.download_voice_command("kokoro", out.append) == 1
    assert "checksum mismatch" in out[-1]
    # wired to `python -m assistant --download-voice kokoro`, no API key needed
    monkeypatch.setattr(cli, "download_voice_command", lambda name: calls.append(name) or 0)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["--download-voice", "kokoro", "--config", str(toml)]) == 0 and calls[-1] == "kokoro"


# --- ElevenLabs (optional cloud voice) ---------------------------------------------

class Resp(FakeResponse):
    status_code = 200


class Unauthorized(Resp):
    status_code = 401


class FakeSession:
    """requests.Session stand-in: post() returns `response` or raises `error`."""

    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.error:
            raise self.error
        return self.response


def eleven(session, fallback=None, **kw):
    return ElevenLabsTTS(fallback or PrintTTS(), session=session, **kw)


def test_elevenlabs_streams_pcm(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret-key-123456")
    pcm = (np.array([0, 16384, -16384, 32767], dtype="<i2")).tobytes()
    session = FakeSession(Resp(pcm))
    samples, sr = eleven(session, voice_id="abc", speed=3).synthesize("Hello there.")
    assert sr == 24000 and np.allclose(samples, [0, 0.5, -0.5, 32767 / 32768])
    url, kw = session.calls[0]
    assert url.endswith("/text-to-speech/abc/stream") and kw["stream"] is True
    assert kw["timeout"] and kw["params"] == {"output_format": "pcm_24000"}
    assert kw["headers"]["xi-api-key"] == "el-secret-key-123456"
    assert kw["json"]["text"] == "Hello there." and kw["json"]["model_id"] == "eleven_flash_v2_5"
    assert kw["json"]["voice_settings"]["speed"] == 1.2  # clamped to the API range


@pytest.mark.parametrize("failure", ["status", "network", "empty"])
def test_elevenlabs_falls_back_to_local(failure, monkeypatch, caplog, fake_kokoro, kokoro_paths):
    key = "el-secret-key-123456"
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    session = {"status": FakeSession(Unauthorized(b"")),
               "network": FakeSession(error=OSError("connection reset")),
               "empty": FakeSession(Resp(b""))}[failure]
    now = [0.0]
    local = KokoroTTS(kokoro_paths["kokoro_model"], kokoro_paths["kokoro_voices"])
    engine = eleven(session, fallback=local, clock=lambda: now[0], cooldown=60)
    with caplog.at_level(logging.WARNING):
        samples, sr = engine.synthesize("Hi.")
    assert sr == 24000 and len(samples) == 2400  # the local Kokoro voice
    assert "ElevenLabs failed" in caplog.text and key not in caplog.text
    tries = len(session.calls)
    engine.synthesize("Again.")  # cooling down: straight to local
    assert len(session.calls) == tries
    now[0] = 61
    engine.synthesize("Later.")
    assert len(session.calls) == tries + 1


def test_elevenlabs_needs_the_key(monkeypatch, no_kokoro, caplog):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        engine = make_tts("elevenlabs", **no_kokoro, piper_voice="")
    assert isinstance(engine, Pyttsx3TTS) and "ELEVENLABS_API_KEY is not set" in caplog.text
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret-key-123456")
    engine = make_tts("elevenlabs", **no_kokoro, piper_voice="", elevenlabs_voice="v1")
    assert isinstance(engine, ElevenLabsTTS) and isinstance(engine.fallback, Pyttsx3TTS)
    assert engine.voice_id == "v1"
    assert "ELEVENLABS_API_KEY" in __import__("assistant.config").config.SECRET_ENV_VARS


def test_elevenlabs_fallback_through_a_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)  # cloud fails at once
    samples, sr = eleven(FakeSession(Resp(b"x"))).synthesize("Hi.")  # PrintTTS: file only
    assert sr == 16000 and len(samples) == 1600


# --- earcons -----------------------------------------------------------------------

@pytest.mark.parametrize("name", list(earcons.EARCONS))
def test_earcon_files_are_short_soft_and_match_the_code(name):
    path = earcons.WEB_DIR / f"{name}.wav"
    assert path.is_file() and path.stat().st_size < 30_000
    samples, sr = audio.read_wav(path)
    assert sr == earcons.SAMPLE_RATE and 0.1 < len(samples) / sr < 0.5
    peak = np.abs(samples).max()
    assert 0.03 < peak <= 0.3  # gentle, not a beep
    assert abs(samples[0]) < 0.01 and abs(samples[-1]) < 0.01  # no clicks
    # the committed file is what assistant/earcons.py makes
    expected = earcons.EARCONS[name]()
    assert len(expected) == len(samples) and np.abs(expected - samples).max() < 3 / 32768


def test_earcon_loudness_order_and_desktop_chime():
    peak = {n: np.abs(f()).max() for n, f in earcons.EARCONS.items()}
    assert peak["thinking"] < peak["ended"] <= peak["listening"]
    assert np.allclose(audio.make_chime(), earcons.listening(audio.SAMPLE_RATE))


def test_earcons_are_served_locally_and_wired_into_the_voice_view():
    js = (ROOT / "assistant" / "web" / "voice.js").read_text()
    for name in earcons.EARCONS:
        assert f'"{name}"' in js
    assert "/earcons/${name}.wav" in js and 'play("ended")' in js
    assert not re.search(r"https?://", js)  # nothing from a CDN
    from assistant.webapp import EARCONS

    assert set(EARCONS) == set(earcons.EARCONS)


# --- the real model (CI caches it; skipped when it can't be downloaded) -------------
# Run in a child process: kokoro-onnx loads its own libespeak-ng, and the
# pyttsx3 tests load the system one; two copies in one process can crash
# (the app itself only ever uses one engine).

def run_kokoro(code: str, *args) -> str:
    import subprocess

    r = subprocess.run([sys.executable, "-c", code, *map(str, args)], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


RENDER = """
import sys
from assistant.tts_kokoro import KokoroTTS
KokoroTTS(sys.argv[1], sys.argv[2], voice=sys.argv[4]).save_to_file(sys.argv[5], sys.argv[3])
"""


def render_kokoro(kokoro_files, out, text, voice="af_heart"):
    run_kokoro(RENDER, *kokoro_files, out, voice, text)


@pytest.mark.slow
def test_kokoro_renders_wav(tmp_path, kokoro_files):
    out = tmp_path / "kokoro.wav"
    render_kokoro(kokoro_files, out, "Hello Kyle, this is your assistant.")
    samples, sr = audio.read_wav(out)
    assert out.stat().st_size > 10_000 and sr == 24000
    assert len(samples) > sr * 0.8 and np.abs(samples).max() > 0.05
    render_kokoro(kokoro_files, out, "Cheers.", voice="bf_emma")  # British voice
    assert np.abs(audio.read_wav(out)[0]).max() > 0.05


BENCH = """
import statistics, sys, time
from assistant.tts import PiperTTS
from assistant.tts_kokoro import KokoroTTS
from assistant.webapp import render_wav

first = "Tomorrow you have the dentist at three thirty."
name, paths = sys.argv[1], sys.argv[2:]
engine = KokoroTTS(*paths) if name == "kokoro" else PiperTTS(*paths)
started = time.perf_counter()
engine.warm_up()
cold = time.perf_counter() - started
runs = []
for _ in range(5):
    started = time.perf_counter()
    assert len(render_wav(engine, first)) > 10000
    runs.append(time.perf_counter() - started)
print(cold, statistics.median(runs), min(runs))
"""


@pytest.mark.slow
def test_time_to_first_audio_kokoro_vs_piper(kokoro_files, request):
    """Time to render a reply's first sentence (what voice mode waits for
    before the first audio), warm model, on this CPU. Printed for PROGRESS.md."""
    engines = {"kokoro": kokoro_files}
    try:
        engines["piper"] = (request.getfixturevalue("piper_voice"),)
    except pytest.skip.Exception:
        pass
    results = {name: [float(x) for x in run_kokoro(BENCH, name, *paths).split()]
               for name, paths in engines.items()}
    line = "; ".join(f"{n}: load+warm-up {c:.2f} s, first sentence median {m:.3f} s "
                     f"(best {b:.3f} s)" for n, (c, m, b) in results.items())
    with request.config.pluginmanager.get_plugin("capturemanager").global_and_fixture_disabled():
        print(f"\nTTS first-audio bench ({os.cpu_count()} CPUs, {sys.platform}): {line}")
    assert results["kokoro"][1] < 5  # sanity: usable for voice mode on a PC


def test_check_reports_the_voice(fake_kokoro, kokoro_paths, piper_paths, no_kokoro):
    from assistant import doctor

    def cfg(**tts_cfg):
        return {"tts": {**DEFAULTS["tts"], **tts_cfg}}

    check = doctor.check_tts(cfg(**kokoro_paths))
    assert check.ok and "Kokoro, voice af_heart" in check.detail
    check = doctor.check_tts(cfg(**no_kokoro, **piper_paths))
    assert check.ok and "Piper" in check.detail and "--download-voice kokoro" in check.detail
    check = doctor.check_tts(cfg(engine="kokoro", **no_kokoro))
    assert not check.ok and not check.required and "--download-voice kokoro" in check.detail
    check = doctor.check_tts(cfg(engine="elevenlabs"), environ={})
    assert not check.ok and not check.required
    check = doctor.check_tts(cfg(engine="elevenlabs"),
                             environ={"ELEVENLABS_API_KEY": "el-secret-key-123456"})
    assert check.ok and "el-secret" not in check.detail
