from types import SimpleNamespace

import numpy as np
import pytest

from assistant import audio
from assistant.stt import Transcriber, rough_match
from assistant.tts import PiperTTS, Pyttsx3TTS

PHRASE = "What is on my calendar tomorrow"


def test_transcriber_with_mock_model():
    class FakeModel:
        def transcribe(self, audio, **kw):
            assert audio.dtype == np.float32 and kw["language"] == "en"
            return iter([SimpleNamespace(text=" hello "),
                         SimpleNamespace(text="world ")]), None

    t = Transcriber(model=FakeModel())
    assert t.transcribe(np.zeros(1600)) == "hello world"
    assert t.transcribe(np.zeros(0)) == ""


def test_rough_match():
    assert rough_match(PHRASE, "what's on my calendar tomorrow?")
    assert rough_match(PHRASE, "What is on my calender tomorrow")
    assert not rough_match(PHRASE, "play some music")


@pytest.fixture(scope="session")
def whisper():
    t = Transcriber()
    try:
        t.model
    except Exception as e:  # no network / model not cached
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    return t


@pytest.mark.slow
def test_transcribe_generated_speech(tmp_path, whisper):
    wav = tmp_path / "phrase.wav"
    Pyttsx3TTS(rate=160).save_to_file(PHRASE, wav)
    text = whisper.transcribe(audio.load_audio(wav))
    assert rough_match(PHRASE, text), text


@pytest.mark.slow
def test_transcribe_piper_speech(tmp_path, piper_voice, whisper):
    wav = tmp_path / "piper_phrase.wav"
    PiperTTS(piper_voice).save_to_file(PHRASE, wav)
    text = whisper.transcribe(audio.load_audio(wav))
    assert rough_match(PHRASE, text), text
