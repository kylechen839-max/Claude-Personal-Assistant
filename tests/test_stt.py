from types import SimpleNamespace

import numpy as np
import pytest

from assistant import audio, stt
from assistant.config import DEFAULTS
from assistant.stt import EN_PROMPT, Transcriber, rough_match
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
    """The PC's shipped transcriber: [stt] defaults (small.en, int8, beam 5,
    the vocabulary prompt), local Whisper."""
    t = stt.from_config({**DEFAULTS["stt"], "engine": "whisper"})
    try:
        t.model
    except Exception as e:  # no network / model not cached
        pytest.skip(f"Whisper {t.model_name} model unavailable: {type(e).__name__}")
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
    assert calls[-1]["language"] == "en" and calls[-1]["initial_prompt"] == EN_PROMPT
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
    # the retry keeps the beam search, prompt and hotwords
    assert model.calls[1]["beam_size"] == 5 and model.calls[1]["initial_prompt"] == EN_PROMPT
    assert model.calls[0]["beam_size"] == 5
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


class Recording:
    """A Whisper model that records its options and says `text`."""

    def __init__(self, text=" Yes."):
        self.calls, self.text = [], text

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter([SimpleNamespace(text=self.text)]), None


def made_from(cfg, language="en", text=" Yes."):
    model = Recording(text)
    t = stt.from_config(cfg, language,
                        make=lambda *a, **k: Transcriber(*a, model=model, **k))
    return t, model


def test_beam_size_and_prompt_come_from_config():
    """Beam search 5 (not greedy) and the app's vocabulary by default;
    [stt] settings change them; falsy beam sizes mean the default."""
    t, model = made_from(DEFAULTS["stt"])
    assert t.model_name == "small.en" and t.compute_type == "int8"
    assert t.transcribe(np.zeros(1600)) == "Yes."
    kw = model.calls[-1]
    assert kw["beam_size"] == 5 and kw["initial_prompt"] == EN_PROMPT
    assert "Notion" in EN_PROMPT and "reschedule" in EN_PROMPT
    assert "hotwords" not in kw  # none set: not sent at all
    t, model = made_from({**DEFAULTS["stt"], "beam_size": 1, "hotwords": "Kyle, Kai",
                          "initial_prompt": "Physics 201, Professor Lee"})
    t.transcribe(np.zeros(1600))
    kw = model.calls[-1]
    assert kw["beam_size"] == 1 and kw["hotwords"] == "Kyle, Kai"
    assert kw["initial_prompt"] == "Physics 201, Professor Lee"
    for falsy in (0, None, ""):  # falsy zero: the default, never greedy by accident
        assert made_from({**DEFAULTS["stt"], "beam_size": falsy})[0].beam_size == 5
    t, model = made_from({**DEFAULTS["stt"], "initial_prompt": ""})  # "" = none
    t.transcribe(np.zeros(1600))
    assert "initial_prompt" not in model.calls[-1]
    zh, model = made_from({**DEFAULTS["stt"], "model": "small"}, "zh", "明天")
    zh.transcribe(np.zeros(1600))  # "auto" in Chinese: the Mandarin prompt
    assert model.calls[-1]["initial_prompt"] == stt.ZH_PROMPT
    assert model.calls[-1]["beam_size"] == 5


def test_whisper_echoing_its_prompt_is_silence(caplog):
    """On near-silent audio Whisper can "hear" its own prompt back: that is
    nothing said, not a request for Claude. Up to two of its items said
    together are a real answer ("Canvas exam", "Kyle, Kai")."""
    names = {**DEFAULTS["stt"], "hotwords": "Kyle, Kai, Mia"}
    for echo in (EN_PROMPT, " calendar, assignment, Notion, reschedule, tomorrow, "
                 "remind me, delete, move, Canvas, exam, quiz.",
                 "Assignment, Notion, reschedule.", "Canvas exam quiz",
                 "tomorrow remind me delete", "Kyle, Kai, Mia"):
        t, _ = made_from(names, text=echo)
        with caplog.at_level("DEBUG"):
            assert t.transcribe(np.zeros(16000)) == "", echo
    assert "Notion" not in caplog.text and "Kyle" not in caplog.text  # lengths only
    for real in ("Tomorrow.", "Delete", "Remind me.", "Move my exam to Friday",
                 "Yes, delete it", "Kai", "Reschedule tomorrow.", "Canvas exam.",
                 "Tomorrow, remind me.", "Exam, quiz.", "Kyle, Kai."):
        t, _ = made_from(names, text=real)
        assert t.transcribe(np.zeros(16000)) == real, real
        t, _ = made_from({**DEFAULTS["stt"], "hotwords": "Kyle, Kai"}, text=real)
        assert t.transcribe(np.zeros(16000)) == real, real  # both names: still kept
    zh, _ = made_from({**DEFAULTS["stt"], "model": "small"}, "zh", "以下是普通话的句子。")
    assert zh.transcribe(np.zeros(16000)) == ""
    assert stt.is_prompt_echo("anything", "") is False


def test_transcript_logged_at_debug_only(caplog):
    t, _ = made_from(DEFAULTS["stt"], text=" Move my dentist appointment to Friday.")
    with caplog.at_level("INFO"):
        t.transcribe(np.zeros(16000))
    assert "dentist" not in caplog.text
    with caplog.at_level("DEBUG", logger="assistant.stt"):
        t.transcribe(np.zeros(16000))
    assert any("dentist" in r.getMessage() and r.levelname == "DEBUG"
               for r in caplog.records)


# Short answers to "Should I delete it?" and a typical request: spoken by
# the local TTS, heard by the shipped model (small.en, beam 5, prompt).
SHORT_ANSWERS = ["yes", "yeah go ahead", "no",
                 "move my dentist appointment to Friday"]


@pytest.mark.slow
@pytest.mark.parametrize("phrase", SHORT_ANSWERS)
def test_short_answers_through_the_shipped_model(phrase, tmp_path, whisper):
    assert whisper.model_name == "small.en" and whisper.beam_size == 5
    wav = tmp_path / "answer.wav"
    Pyttsx3TTS(rate=160).save_to_file(phrase, wav)
    clip = audio.load_audio(wav)
    pad = audio.silence(0.3)  # as the endpointer sends it: a little room around
    text = whisper.transcribe(np.concatenate([pad, clip, pad]))
    assert rough_match(phrase, text), (phrase, text)


@pytest.mark.slow
def test_near_silence_never_comes_back_as_the_prompt(whisper):
    rng = np.random.default_rng(0)
    hiss = (rng.standard_normal(16000) * 0.001).astype(np.float32)
    text = whisper.transcribe(hiss)
    assert not whisper.is_echo(text) and "assignment, notion" not in text.lower(), text
