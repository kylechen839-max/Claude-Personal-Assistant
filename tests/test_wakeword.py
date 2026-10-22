from pathlib import Path

import numpy as np
import pytest

from kwil import audio
from kwil.audio import SAMPLE_RATE
from kwil.tts import Pyttsx3TTS
from kwil.wakeword import (FRAME_LENGTH, EnergyVAD, FileSource,
                                WakeListener, make_detector,
                                phrase_from_model, record_until_silence)

COMMAND = "What is on my calendar tomorrow"


class ScriptedDetector:
    """Stands in for openWakeWord: fires when the file reaches given frames."""

    frame_length = FRAME_LENGTH

    def __init__(self, source, fire_at):
        self.source = source
        self.fire_at = set(fire_at)

    def process(self, frame):
        return self.source.position - 1 in self.fire_at


def tts_clip(tmp_path, text, name):
    """Spoken `text`, trimmed of the silence some voices (Windows SAPI) pad
    their output with, so the clip length is the speech length."""
    path = tmp_path / f"{name}.wav"
    engine = Pyttsx3TTS(rate=160)  # kept alive until the file is read
    for attempt in range(3):
        # pyttsx3's eSpeak driver now and then leaves an empty file (its
        # callback hits a dead weakref), or none at all on a busy machine;
        # rendering again fixes it
        engine.save_to_file(text, path)
        try:
            clip = audio.load_audio(path)
        except (EOFError, FileNotFoundError):
            clip = np.zeros(0, dtype=np.float32)
        if len(clip):
            break
    assert len(clip), f"TTS rendered no audio for {text!r}"
    loud = np.flatnonzero(np.abs(clip) > 0.02)
    return clip[loud[0]:loud[-1] + 1] if len(loud) else clip


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
        # the spoken command plus pre-roll and a little trailing silence
        assert expected - 0.3 < seconds < expected + 1.0, (seconds, expected)


@pytest.mark.slow
def test_pipeline_transcribes_command(session_file):
    from kwil.stt import Transcriber, rough_match

    t = Transcriber()
    try:
        t.model
    except Exception as e:
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    path, fire_at, _ = session_file
    source = FileSource(path)
    woke, utt = WakeListener(ScriptedDetector(source, fire_at)).listen(source)
    assert woke and rough_match(COMMAND, t.transcribe(utt))


def test_phrase_from_model_name():
    assert phrase_from_model("keywords/hey_claude_v0.1.onnx") == "hey claude"
    assert phrase_from_model("Hey-Claude.onnx") == "hey claude"


def test_make_detector_rejects_unknown_builtin():
    with pytest.raises(ValueError, match="hey_jarvis"):
        make_detector(builtin_keyword="computer")


def detections(detector, tmp_path, text=None, clip=None):
    """Wake count for spoken `text` (or a ready `clip`) with 1 s of silence
    on each side."""
    if clip is None:
        clip = tts_clip(tmp_path, text, "kw")
    path = tmp_path / "kw.wav"
    audio.write_wav(path, np.concatenate([audio.silence(1), clip, audio.silence(1)]))
    return sum(detector.process(f) for f in FileSource(path).frames())


@pytest.mark.slow
def test_real_openwakeword_detects_builtin_keyword(tmp_path):
    """Real openWakeWord (downloads a few MB of models on first run)."""
    detector = make_detector(keyword_path="keywords/missing.onnx",
                             builtin_keyword="hey_jarvis")
    assert detector.phrase == "hey jarvis"
    assert detections(detector, tmp_path, "Hey Jarvis") == 1  # fires once
    assert detections(detector, tmp_path, COMMAND) == 0


HERE = Path(__file__).resolve().parent
HEY_CLAUDE_MODEL = HERE.parent / "keywords" / "hey_claude.onnx"
# "Hey Claude" rendered once with Windows SAPI voices (David, Zira at rates
# 140 and 180). Recordings rather than live TTS because eSpeak, the TTS on
# Linux CI and the Pi, is too robotic to trigger a model trained on
# natural-sounding synthetic speech.
HEY_CLAUDE_CLIPS = sorted((HERE / "fixtures").glob("hey_claude_sapi_*.wav"))


def test_hey_claude_fixtures_present():
    assert HEY_CLAUDE_MODEL.exists() and len(HEY_CLAUDE_CLIPS) == 4


@pytest.mark.slow
@pytest.mark.parametrize("clip_path", HEY_CLAUDE_CLIPS, ids=lambda p: p.stem)
def test_real_hey_claude_model(tmp_path, clip_path):
    """The trained "Hey Claude" model wakes once on it, not on other speech."""
    detector = make_detector(keyword_path=str(HEY_CLAUDE_MODEL))
    assert detector.phrase == "hey claude"
    assert detections(detector, tmp_path, clip=audio.load_audio(clip_path)) == 1
    assert detections(detector, tmp_path, "Hey Jarvis") == 0
    assert detections(detector, tmp_path, COMMAND) == 0


def test_cli_wake_mode_with_audio_file(session_file, fake_client):
    """End to end: file -> wake -> VAD -> (mock) STT -> (mock) Claude -> TTS."""
    from types import SimpleNamespace

    from kwil.chat import ClaudeChat
    from kwil.cli import parse_args, run_wake
    from kwil.tts import PrintTTS
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

    from kwil.chat import ClaudeChat
    from kwil.cli import get_reply

    fake_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    reply = get_reply(ClaudeChat(client=fake_client), "hi", lambda *_: None)
    assert "internet" in reply


def test_audio_file_uses_detector_frame_length(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from kwil import cli, wakeword
    from kwil.config import DEFAULTS

    path = tmp_path / "in.wav"
    audio.write_wav(path, audio.silence(1.0))
    fake = SimpleNamespace(frame_length=480, phrase="hey claude",
                           process=lambda frame: False)
    monkeypatch.setattr(wakeword, "make_detector", lambda **_: fake)
    args = cli.parse_args(["--audio-file", str(path)])
    _, source, _ = cli.build_wake_components(args, DEFAULTS)
    assert source.frame_length == 480
    assert all(len(f) == 480 for f in source.frames())
