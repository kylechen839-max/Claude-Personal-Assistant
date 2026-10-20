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


@pytest.mark.slow
def test_transcribe_kokoro_speech(tmp_path, kokoro_files, whisper):
    from tests.test_voices import render_kokoro  # in a child process

    wav = tmp_path / "kokoro_phrase.wav"
    render_kokoro(kokoro_files, wav, PHRASE)
    text = whisper.transcribe(audio.load_audio(wav))
    assert rough_match(PHRASE, text), text


def test_transcriber_language():
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kw):
            calls.append(kw)
            return iter([SimpleNamespace(text="明天"), SimpleNamespace(text="有什么安排？")]), None

    assert Transcriber(model=FakeModel()).language == "en"
    Transcriber(model=FakeModel()).transcribe(np.zeros(1600))
    assert calls[-1]["language"] == "en" and "initial_prompt" not in calls[-1]
    zh = Transcriber(model=FakeModel(), language="zh")
    assert zh.transcribe(np.zeros(1600)) == "明天有什么安排？"  # no spaces in Chinese
    assert calls[-1]["language"] == "zh"
    assert calls[-1]["initial_prompt"] == "以下是普通话的句子。"


def test_degenerate_text():
    from assistant.stt import is_degenerate

    for bad in ("!!!!", " !!!!!!!!!!!! ", "! ! !"):
        assert is_degenerate(bad), bad
    # Whisper's usual non-speech output is not retried: noise and music
    # would otherwise cost a second decode and a float32 model load.
    for ok in ("", "!", "OK.", "Hi!!!", "3!!", "明天有什么安排？", "..",
               "...", " ...", "…", "♪♪♪", "- - -", "。。。", "?? ?"):
        assert not is_degenerate(ok), ok


class GarbageThenWords:
    """A Whisper model that decodes to "!!!!" (token 0 repeated) until
    asked greedily, or always when `stuck`."""

    def __init__(self, stuck=False, compute_type="int8"):
        self.calls, self.stuck, self.compute_type = [], stuck, compute_type

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if self.stuck or kw.get("temperature") != 0.0:
            return iter([SimpleNamespace(text=" !!!!!!!!")]), None
        return iter([SimpleNamespace(text=" What's on my calendar tomorrow?")]), None


def test_degenerate_utterance_is_decoded_again_on_the_same_model(caplog):
    model = GarbageThenWords()
    with caplog.at_level("INFO"):
        text = Transcriber(model=model).transcribe(np.zeros(16000))
    assert text == "What's on my calendar tomorrow?"
    assert len(model.calls) == 2 and model.calls[1]["temperature"] == 0.0
    assert model.calls[1]["condition_on_previous_text"] is False
    assert model.calls[1]["language"] == "en" and model.calls[1]["vad_filter"] is False
    assert "calendar" not in caplog.text and "!!" not in caplog.text  # lengths only


def test_degenerate_utterance_retries_in_float32(monkeypatch):
    """Our own int8 model gave "!!!!" (NaN logits on some CPUs): the retry
    runs on a float32 copy, loaded once; if that is garbage too, nothing."""
    import faster_whisper

    made = []

    def fake_model(name, device, compute_type):
        made.append(GarbageThenWords(stuck=compute_type == "int8",
                                     compute_type=compute_type))
        return made[-1]

    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_model)
    t = Transcriber("base.en")
    assert t.transcribe(np.zeros(16000)) == "What's on my calendar tomorrow?"
    assert t.transcribe(np.zeros(16000)) == "What's on my calendar tomorrow?"
    assert [m.compute_type for m in made] == ["int8", "float32"]
    assert made[1].calls[0]["temperature"] == 0.0
    made[1].stuck = True
    assert t.transcribe(np.zeros(16000)) == ""  # dropped, never "!!!!"
