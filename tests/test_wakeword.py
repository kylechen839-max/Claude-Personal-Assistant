import os

import numpy as np
import pytest

from assistant import audio
from assistant.audio import SAMPLE_RATE
from assistant.tts import Pyttsx3TTS
from assistant.wakeword import (FRAME_LENGTH, EnergyVAD, FileSource,
                                WakeListener, make_detector,
                                record_until_silence)

COMMAND = "What is on my calendar tomorrow"


class ScriptedDetector:
    """Stands in for Porcupine: fires when the file reaches given frames."""

    frame_length = FRAME_LENGTH

    def __init__(self, source, fire_at):
        self.source = source
        self.fire_at = set(fire_at)

    def process(self, frame):
        return self.source.position - 1 in self.fire_at


def tts_clip(tmp_path, text, name):
    path = tmp_path / f"{name}.wav"
    Pyttsx3TTS(rate=160).save_to_file(text, path)
    return audio.load_audio(path)


@pytest.fixture(scope="module")
def session_file(tmp_path_factory):
    """[1s silence]["hey claude"][0.4s][command][1.5s silence] x2."""
    tmp = tmp_path_factory.mktemp("wake")
    wake = tts_clip(tmp, "Hey Claude", "wake")
    cmd = tts_clip(tmp, COMMAND, "cmd")
    parts, fire_at, cmd_lengths = [], [], []
    pos = 0
    for _ in range(2):
        for chunk, is_wake in [(audio.silence(1.0), False), (wake, True),
                               (audio.silence(0.4), False), (cmd, False),
                               (audio.silence(1.5), False)]:
            parts.append(chunk)
            pos += len(chunk)
            if is_wake:
                fire_at.append(pos // FRAME_LENGTH)
        cmd_lengths.append(len(cmd) / SAMPLE_RATE)
    path = tmp / "session.wav"
    audio.write_wav(path, np.concatenate(parts))
    return path, fire_at, cmd_lengths


def test_vad_detects_speech_vs_silence():
    vad = EnergyVAD()
    quiet = (np.random.default_rng(0).normal(0, 50, FRAME_LENGTH)).astype(np.int16)
    loud = (np.sin(np.arange(FRAME_LENGTH) / 3) * 8000).astype(np.int16)
    assert not vad.is_speech(quiet)
    assert vad.is_speech(loud)


def test_record_until_silence_times_out_without_speech():
    frames = iter([np.zeros(FRAME_LENGTH, np.int16)] * 400)
    assert record_until_silence(frames, EnergyVAD(), no_speech_timeout=1.0) is None


def test_pipeline_on_audio_file(session_file):
    path, fire_at, cmd_lengths = session_file
    chimes = []
    source = FileSource(path)
    listener = WakeListener(ScriptedDetector(source, fire_at),
                            on_wake=lambda: chimes.append(1))
    results = []
    while True:
        woke, utterance = listener.listen(source)
        if not woke:
            break
        results.append(utterance)
    assert len(results) == 2 and len(chimes) == 2
    for utt, expected in zip(results, cmd_lengths):
        assert utt is not None
        seconds = len(utt) / SAMPLE_RATE
        # the command plus pre-roll and a little trailing silence
        assert expected - 0.3 < seconds < expected + 1.0, (seconds, expected)


@pytest.mark.slow
def test_pipeline_transcribes_command(session_file):
    from assistant.stt import Transcriber, rough_match

    t = Transcriber()
    try:
        t.model
    except Exception as e:
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    path, fire_at, _ = session_file
    source = FileSource(path)
    woke, utt = WakeListener(ScriptedDetector(source, fire_at)).listen(source)
    assert woke and rough_match(COMMAND, t.transcribe(utt))


def test_make_detector_requires_key(monkeypatch):
    monkeypatch.delenv("PICOVOICE_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PICOVOICE_ACCESS_KEY"):
        make_detector()


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("PICOVOICE_ACCESS_KEY"),
                    reason="PICOVOICE_ACCESS_KEY not set")
def test_real_porcupine_detects_builtin_keyword(tmp_path):
    clip = tts_clip(tmp_path, "Computer", "kw")
    path = tmp_path / "kw.wav"
    audio.write_wav(path, np.concatenate([audio.silence(1), clip, audio.silence(1)]))
    detector = make_detector(builtin_keyword="computer", sensitivity=0.8)
    hits = sum(detector.process(f) for f in FileSource(path).frames())
    detector.close()
    assert hits >= 1


def test_cli_wake_mode_with_audio_file(session_file, fake_client):
    """End to end: file -> wake -> VAD -> (mock) STT -> (mock) Claude -> TTS."""
    from types import SimpleNamespace

    from assistant.chat import ClaudeChat
    from assistant.cli import parse_args, run_wake
    from assistant.tts import PrintTTS
    from tests.conftest import make_response, text_block

    args = parse_args(["--mode", "wake", "--audio-file", str(session_file[0])])
    assert args.audio_file and args.mode == "wake"

    path, fire_at, _ = session_file
    source = FileSource(path)
    listener = WakeListener(ScriptedDetector(source, fire_at))
    heard = []

    def transcribe(utt):
        heard.append(len(utt))
        return COMMAND if len(heard) == 1 else "Goodbye."

    fake_client.queue = [make_response(text_block("You have a dentist at 3."))]
    said = []
    chat = ClaudeChat(client=fake_client)
    run_wake(chat, PrintTTS(said.append), SimpleNamespace(transcribe=transcribe),
             listener, source, output_fn=lambda *_: None)
    assert said == ["[speaking] You have a dentist at 3.", "[speaking] Goodbye!"]
    assert chat.history == []  # goodbye resets the conversation


def test_get_reply_handles_network_error(fake_client):
    import anthropic
    import httpx

    from assistant.chat import ClaudeChat
    from assistant.cli import get_reply

    fake_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    reply = get_reply(ClaudeChat(client=fake_client), "hi", lambda *_: None)
    assert "internet" in reply
