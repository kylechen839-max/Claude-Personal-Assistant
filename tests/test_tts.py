import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from kwil import audio
from kwil.tts import (PiperTTS, PrintTTS, Pyttsx3TTS, download_voice, make_tts,
                           voice_urls)
from kwil.tts import main as tts_main


def test_make_tts_factory():
    assert isinstance(make_tts("pyttsx3"), Pyttsx3TTS)
    assert isinstance(make_tts("print"), PrintTTS)
    with pytest.raises(ValueError):
        make_tts("nope")


@pytest.fixture
def fake_voice_files(tmp_path):
    model = tmp_path / "en_US-test-medium.onnx"
    model.write_bytes(b"onnx")
    model.with_name(model.name + ".json").write_text("{}")
    return model


@pytest.fixture
def fake_piper(monkeypatch):
    """A stand-in `piper` package whose voice returns two 'sentences' of audio."""
    loaded = []

    class FakeVoice:
        config = SimpleNamespace(sample_rate=22050)

        @staticmethod
        def load(path):
            loaded.append(Path(path))
            return FakeVoice()

        def synthesize(self, text):
            for _ in text.split(". "):
                yield SimpleNamespace(
                    sample_rate=22050,
                    audio_float_array=np.full(2205, 0.25, dtype=np.float32))

    module = types.ModuleType("piper")
    module.PiperVoice = FakeVoice
    monkeypatch.setitem(sys.modules, "piper", module)
    return loaded


def test_make_tts_picks_piper_when_voice_present(fake_voice_files, fake_piper):
    tts = make_tts("piper", piper_voice=str(fake_voice_files))
    assert isinstance(tts, PiperTTS) and tts.voice_path == fake_voice_files
    # the old config key still works
    assert isinstance(make_tts("piper", piper_model=str(fake_voice_files)), PiperTTS)
    assert fake_piper == []  # the model loads lazily, on first use


@pytest.mark.parametrize("problem", ["no_voice", "no_model", "no_json", "no_package"])
def test_make_tts_piper_falls_back_to_pyttsx3(problem, fake_voice_files, fake_piper,
                                              monkeypatch, caplog):
    voice = str(fake_voice_files)
    if problem == "no_voice":
        voice = ""
    elif problem == "no_model":
        voice = str(fake_voice_files.with_name("missing.onnx"))
    elif problem == "no_json":
        fake_voice_files.with_name(fake_voice_files.name + ".json").unlink()
    else:
        monkeypatch.setitem(sys.modules, "piper", None)  # import piper -> ImportError
    with caplog.at_level(logging.WARNING, logger="kwil.tts"):
        tts = make_tts("piper", piper_voice=voice, rate=150)
    assert isinstance(tts, Pyttsx3TTS) and tts.rate == 150
    assert "using pyttsx3 instead" in caplog.text
    assert "--download-voice" in caplog.text


def test_engine_selection_from_config(tmp_path, fake_voice_files, fake_piper):
    from kwil.config import load_config

    cfg = load_config(tmp_path / "none.toml", env_file=None)
    assert cfg["tts"]["engine"] == "auto"
    cfg["tts"]["kokoro_model"] = str(tmp_path / "missing.onnx")  # no Kokoro, no Piper
    assert isinstance(make_tts(cfg["tts"]["engine"], **cfg["tts"]), Pyttsx3TTS)
    toml = tmp_path / "config.toml"
    voice = fake_voice_files.as_posix()
    toml.write_text(f'[tts]\nengine = "piper"\npiper_voice = "{voice}"\n')
    cfg = load_config(toml, env_file=None)
    assert isinstance(make_tts(cfg["tts"]["engine"], **cfg["tts"]), PiperTTS)
    toml.write_text('[tts]\nengine = "piper"\npiper_voice = "voices/missing.onnx"\n')
    cfg = load_config(toml, env_file=None)
    assert isinstance(make_tts(cfg["tts"]["engine"], **cfg["tts"]), Pyttsx3TTS)


def test_pyttsx3_renders_wav(tmp_path):
    out = tmp_path / "hello.wav"
    Pyttsx3TTS().save_to_file("Hello Kyle, this is your assistant.", out)
    assert out.exists() and out.stat().st_size > 1000
    samples = audio.load_audio(out)
    assert len(samples) > audio.SAMPLE_RATE * 0.5  # at least half a second
    assert np.abs(samples).max() > 0.05  # not silent


def test_print_tts():
    said = []
    PrintTTS(said.append).speak("hi")
    assert said == ["[speaking] hi"]


def test_piper_save_and_speak(tmp_path, fake_voice_files, fake_piper, monkeypatch):
    tts = PiperTTS(fake_voice_files)
    out = tmp_path / "p.wav"
    tts.save_to_file("Hello. World", out)
    samples, sr = audio.read_wav(out)
    assert sr == 22050 and len(samples) == 2 * 2205
    assert abs(samples.max() - 0.25) < 0.01

    played = []
    monkeypatch.setattr("kwil.tts.audio.play", lambda s, sr: played.append((s, sr)))
    tts.speak("Hi there")
    assert len(played) == 1 and played[0][1] == 22050 and len(played[0][0]) == 2205
    assert fake_piper == [fake_voice_files]  # loaded once, then reused


def test_voice_urls():
    urls = voice_urls("en_US-lessac-medium")
    assert urls == {
        "en_US-lessac-medium.onnx": "https://huggingface.co/rhasspy/piper-voices/"
        "resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true",
        "en_US-lessac-medium.onnx.json": "https://huggingface.co/rhasspy/piper-voices/"
        "resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true",
    }
    assert "/en_GB/northern_english_male/x_low/" in next(iter(
        voice_urls("en_GB-northern_english_male-x_low").values()))
    with pytest.raises(ValueError):
        voice_urls("lessac")


def test_download_voice(tmp_path, monkeypatch, capsys):
    fetched = []

    class FakeResponse:
        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield f"data from {self.url}".encode()

    def fake_get(url, stream, timeout):
        fetched.append(url)
        return FakeResponse(url)

    monkeypatch.setattr("requests.get", fake_get)
    model = download_voice("en_US-lessac-medium", tmp_path / "voices")
    assert model == tmp_path / "voices" / "en_US-lessac-medium.onnx"
    assert model.read_bytes().startswith(b"data from https://huggingface.co/")
    assert (tmp_path / "voices" / "en_US-lessac-medium.onnx.json").is_file()
    assert not list((tmp_path / "voices").glob("*.part"))
    assert len(fetched) == 2
    download_voice("en_US-lessac-medium", tmp_path / "voices")
    assert len(fetched) == 2  # already there: nothing fetched again

    assert tts_main(["--download-voice", "--dir", str(tmp_path / "voices")]) == 0
    printed = capsys.readouterr().out
    assert 'engine = "piper"' in printed and "en_US-lessac-medium.onnx" in printed


@pytest.mark.slow
def test_piper_renders_wav(tmp_path, piper_voice):
    """Stage 2 self-test with the real Piper engine and voice."""
    out = tmp_path / "hello.wav"
    make_tts("piper", piper_voice=str(piper_voice)).save_to_file(
        "Hello Kyle, this is your assistant.", out)
    assert out.exists() and out.stat().st_size > 1000
    samples = audio.load_audio(out)
    assert len(samples) > audio.SAMPLE_RATE * 0.5  # at least half a second
    assert np.abs(samples).max() > 0.05  # not silent


def test_wav_roundtrip_and_resample(tmp_path):
    tone = np.sin(np.linspace(0, 100, 22050)).astype(np.float32) * 0.5
    path = tmp_path / "t.wav"
    audio.write_wav(path, tone, 22050)
    back = audio.load_audio(path)
    assert abs(len(back) - 16000) <= 1
    assert len(audio.make_chime()) > 0


def test_pyttsx3_fresh_engine_per_reply(monkeypatch):
    import pyttsx3.engine

    made = []

    class FakeEngine:
        def __init__(self):
            made.append(self)
            self.props = {}

        def setProperty(self, k, v):
            self.props[k] = v

        def say(self, text):
            pass

        def runAndWait(self):
            pass

    monkeypatch.setattr(pyttsx3.engine, "Engine", FakeEngine)
    tts = Pyttsx3TTS(rate=150, fresh_engine=True)
    tts.speak("one")
    tts.speak("two")
    assert len(made) == 2 and made[1].props["rate"] == 150
    reused = Pyttsx3TTS(fresh_engine=False)
    reused.speak("one")
    reused.speak("two")
    assert len(made) == 3


def test_pyttsx3_windows_file_output_uses_sapi(tmp_path, monkeypatch):
    """pyttsx3's save_to_file writes an empty file on Windows, so SAPI is
    driven directly there; check the calls with a fake comtypes."""
    import sys
    import types

    calls = []

    class Token:
        def __init__(self, name):
            self.name = name

        def GetDescription(self):
            return self.name

    class Tokens:
        items = [Token("Microsoft David Desktop"), Token("Microsoft Zira Desktop")]
        Count = 2

        def Item(self, i):
            return self.items[i]

    class SpVoice:
        Voice = Rate = AudioOutputStream = None

        def GetVoices(self):
            return Tokens()

        def Speak(self, text, flags):
            calls.append(("speak", text, flags, self.Voice.name, self.Rate,
                          self.AudioOutputStream))

    class SpFileStream:
        def Open(self, path, mode):
            calls.append(("open", path, mode))

        def Close(self):
            calls.append(("close",))

    client = types.SimpleNamespace(CreateObject=lambda name: {
        "SAPI.SpVoice": SpVoice, "SAPI.SpFileStream": SpFileStream}[name]())
    monkeypatch.setitem(sys.modules, "comtypes", types.SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "comtypes.client", client)

    out = tmp_path / "x.wav"
    Pyttsx3TTS(rate=185, voice="zira", sapi_files=True).save_to_file("hi", out)
    assert calls[0] == ("open", str(out.resolve()), 3)
    _, text, flags, voice, rate, stream = calls[1]
    assert (text, flags, voice) == ("hi", 0, "Microsoft Zira Desktop")
    assert rate == 1 and isinstance(stream, SpFileStream)
    assert calls[2] == ("close",)
