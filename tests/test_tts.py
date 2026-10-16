import numpy as np
import pytest

from assistant import audio
from assistant.tts import PiperTTS, PrintTTS, Pyttsx3TTS, make_tts


def test_make_tts_factory():
    assert isinstance(make_tts("pyttsx3"), Pyttsx3TTS)
    assert isinstance(make_tts("print"), PrintTTS)
    assert isinstance(make_tts("piper", piper_model="v.onnx"), PiperTTS)
    with pytest.raises(ValueError):
        make_tts("piper")
    with pytest.raises(ValueError):
        make_tts("nope")


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


def test_piper_invokes_cli(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, input, check, capture_output):
        calls.append((cmd, input))
        audio.write_wav(cmd[-1], audio.silence(0.2))

    monkeypatch.setattr("assistant.tts.subprocess.run", fake_run)
    out = tmp_path / "p.wav"
    PiperTTS("voice.onnx").save_to_file("hi", out)
    assert calls[0][0][1:3] == ["--model", "voice.onnx"] and calls[0][1] == b"hi"
    assert out.exists()


def test_wav_roundtrip_and_resample(tmp_path):
    tone = np.sin(np.linspace(0, 100, 22050)).astype(np.float32) * 0.5
    path = tmp_path / "t.wav"
    audio.write_wav(path, tone, 22050)
    back = audio.load_audio(path)
    assert abs(len(back) - 16000) <= 1
    assert len(audio.make_chime()) > 0
