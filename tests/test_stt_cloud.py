"""Optional cloud speech-to-text (stt_cloud.py): a fake HTTP session with the
services' real reply shapes; local Whisper is a fake model. Offline."""

from types import SimpleNamespace

import numpy as np
import pytest
import requests

from kwil import doctor, stt
from kwil.config import DEFAULTS, ISOLATED_ENV_VARS, SECRET_ENV_VARS, redact
from kwil.stt import EN_PROMPT, Transcriber
from kwil.stt_cloud import DEEPGRAM_URL, OPENAI_URL, CloudTranscriber

KEY = "sk-proj-test-not-a-real-key-0123456789"
SAID = "Move my dentist appointment to Friday."


class Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class Session:
    """requests.Session.post: answers in turn (a Response, or an exception
    to raise) and records each request."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class LocalModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kw):
        self.calls += 1
        return iter([SimpleNamespace(text=" local words")]), None


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def cloud(engine, *answers, env=None, hotwords="", **kw):
    model = LocalModel()
    local = Transcriber("small.en", model=model, hotwords=hotwords)
    env = {"OPENAI_API_KEY": KEY, "DEEPGRAM_API_KEY": "dg-test-key-abcdef"} \
        if env is None else env
    clock = Clock()
    t = CloudTranscriber(local, engine, session=Session(*answers), clock=clock,
                         environ=env, **kw)
    return t, model, clock


SPEECH = np.zeros(16000, dtype=np.float32)  # one second


def test_openai_request_and_reply():
    t, local, _ = cloud("openai", Response(200, {"text": SAID}), hotwords="Kyle")
    assert t.transcribe(SPEECH) == SAID and local.calls == 0
    call = t.session.calls[0]
    assert call["url"] == OPENAI_URL
    assert call["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert call["data"]["model"] == "gpt-4o-transcribe"
    assert call["data"]["language"] == "en"
    assert call["data"]["prompt"] == f"{EN_PROMPT}, Kyle"  # vocabulary + names
    name, wav, mime = call["files"]["file"]
    assert wav[:4] == b"RIFF" and mime == "audio/wav" and len(wav) > 32000
    assert 0 < call["timeout"] <= 60  # bounded


def test_deepgram_request_and_reply():
    reply = {"metadata": {"request_id": "r1"},
             "results": {"channels": [{"alternatives": [
                 {"transcript": SAID, "confidence": 0.98, "words": []}]}]}}
    t, local, _ = cloud("deepgram", Response(200, reply), hotwords="Kyle, Kai")
    assert t.transcribe(SPEECH) == SAID and local.calls == 0
    call = t.session.calls[0]
    assert call["url"] == DEEPGRAM_URL
    assert call["headers"]["Authorization"] == "Token dg-test-key-abcdef"
    assert call["headers"]["Content-Type"] == "audio/wav" and call["data"][:4] == b"RIFF"
    params = dict(call["params"])
    assert params["model"] == "nova-3" and params["language"] == "en"
    terms = [v for k, v in call["params"] if k == "keyterm"]
    assert {"Notion", "reschedule", "remind me", "Kyle", "Kai"} <= set(terms)


@pytest.mark.parametrize("failure", [requests.Timeout("read timed out"),
                                     Response(503, {"error": "overloaded"}),
                                     Response(200, {"results": {"channels": []}})])
def test_falls_back_to_local_whisper_then_waits(failure, caplog):
    """A timeout, a 5xx or an odd reply: local Whisper hears that one, and
    the cloud is left alone for the cooldown (no timeout on every turn)."""
    t, local, clock = cloud("deepgram", failure, Response(200, {"results": {
        "channels": [{"alternatives": [{"transcript": "back again"}]}]}}))
    with caplog.at_level("INFO"):
        assert t.transcribe(SPEECH) == "local words" and local.calls == 1
    assert "Cloud speech-to-text (deepgram) failed" in caplog.text
    assert "overloaded" not in caplog.text  # no response bodies
    assert t.transcribe(SPEECH) == "local words" and len(t.session.calls) == 1
    clock.t += 61
    assert t.transcribe(SPEECH) == "back again" and len(t.session.calls) == 2


def test_no_key_means_local_and_one_warning(caplog):
    t, local, _ = cloud("openai", env={})
    with caplog.at_level("WARNING"):
        assert t.transcribe(SPEECH) == "local words"
        assert t.transcribe(SPEECH) == "local words"
    assert t.session.calls == [] and local.calls == 2
    assert caplog.text.count("OPENAI_API_KEY is not set") == 1


def test_long_or_empty_audio_stays_local():
    t, local, _ = cloud("openai")
    assert t.transcribe(np.zeros(16000 * 301, dtype=np.float32)) == "local words"
    assert t.transcribe(np.zeros(0)) == ""
    assert t.session.calls == []


def test_cloud_prompt_echo_is_silence():
    t, _, _ = cloud("openai", Response(200, {"text": EN_PROMPT}))
    assert t.transcribe(SPEECH) == ""


def test_nothing_private_at_info(caplog):
    """INFO has the engine, lengths and times; the transcript only at DEBUG;
    never the key, nor a requests error (its URL quotes the keyterms)."""
    url_error = requests.ConnectionError(
        f"HTTPSConnectionPool: Max retries exceeded with url: /v1/listen?keyterm=Kai {KEY}")
    t, _, clock = cloud("deepgram", url_error, hotwords="Kai")
    with caplog.at_level("INFO"):
        t.transcribe(SPEECH)
    assert "Kai" not in caplog.text and KEY not in caplog.text
    assert "ConnectionError" in caplog.text
    caplog.clear()
    t, _, _ = cloud("openai", Response(200, {"text": SAID}))
    with caplog.at_level("INFO"):
        t.transcribe(SPEECH)
    assert "dentist" not in caplog.text and KEY not in caplog.text
    assert "1.0 s of audio -> 38 chars" in caplog.text
    caplog.clear()
    t, _, _ = cloud("openai", Response(200, {"text": SAID}))
    with caplog.at_level("DEBUG", logger="kwil.stt_cloud"):
        t.transcribe(SPEECH)
    assert any("dentist" in r.getMessage() and r.levelname == "DEBUG"
               for r in caplog.records)


def test_from_config_picks_the_engine(caplog):
    assert isinstance(stt.from_config(DEFAULTS["stt"]), Transcriber)
    t = stt.from_config({**DEFAULTS["stt"], "engine": "Deepgram", "cloud_timeout": 0,
                         "hotwords": "Kai"})
    assert isinstance(t, CloudTranscriber) and t.engine == "deepgram"
    assert t.timeout == 10 and t.cloud_model == "nova-3"  # 0 = the default
    assert t.language == "en" and t.model_name == "small.en"
    assert t.local.beam_size == 5 and t.local.hotwords == "Kai"
    t = stt.from_config({**DEFAULTS["stt"], "engine": "openai", "cloud_timeout": 999,
                         "cloud_model": "gpt-4o-mini-transcribe"})
    assert t.timeout == 60 and t.cloud_model == "gpt-4o-mini-transcribe"
    with caplog.at_level("WARNING"):
        t = stt.from_config({**DEFAULTS["stt"], "engine": "whisperx"})
    assert t.engine is None and "Unknown [stt] engine" in caplog.text


def test_keys_are_secret_but_never_stop_a_separate_instance():
    """Masked in logs, but not ISOLATED_ENV_VARS: OpenAI's quickstart puts
    OPENAI_API_KEY in the Windows user environment, and Mom's or Kai's
    instance must still start (theirs pins [stt] engine = "whisper")."""
    from kwil.config import inherited_env_conflicts

    for var in ("OPENAI_API_KEY", "DEEPGRAM_API_KEY"):
        assert var in SECRET_ENV_VARS and var not in ISOLATED_ENV_VARS
    assert inherited_env_conflicts({"OPENAI_API_KEY": KEY,
                                    "DEEPGRAM_API_KEY": "dg-test-key-abcdef"}, None) == []
    assert KEY not in redact(f"key {KEY} leaked")


def test_doctor_names_the_key_never_its_value(tmp_path):
    cfg = {"stt": {**DEFAULTS["stt"], "engine": "openai"}}
    ok = doctor.check_cloud_stt(cfg, {"OPENAI_API_KEY": KEY})
    assert ok[0].ok and KEY not in ok[0].detail and "OPENAI_API_KEY set" in ok[0].detail
    missing = doctor.check_cloud_stt(cfg, {})
    assert not missing[0].ok and not missing[0].required
    assert doctor.check_cloud_stt({"stt": DEFAULTS["stt"]}, {}) == []
