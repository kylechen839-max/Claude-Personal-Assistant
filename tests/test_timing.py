"""Per-turn latency logging, driven by a fake clock."""

import logging
from types import SimpleNamespace

import numpy as np

from assistant import cli, tts
from assistant.chat import ClaudeChat
from assistant.timing import TurnTimer
from tests.conftest import make_response, text_block


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_turn_timer_steps_and_summary(caplog):
    clock = FakeClock()
    timer = TurnTimer(clock, "wake")
    with caplog.at_level(logging.INFO, logger="assistant.timing"):
        for stage, dt in [("speech_end", 2.0), ("transcript", 0.5),
                          ("reply", 1.25), ("audio", 0.3)]:
            clock.advance(dt)
            timer.mark(stage)
    clock.advance(9)
    timer.mark("audio")  # only the first mark of a stage counts
    assert [(label, round(dt, 6)) for label, dt in timer.steps()] == [
        ("end of speech", 2.0), ("transcript", 0.5), ("Claude reply", 1.25),
        ("first audio", 0.3)]
    assert timer.summary() == (
        "Turn timings from wake: end of speech +2.00 s, transcript +0.50 s, "
        "Claude reply +1.25 s, first audio +0.30 s; wake to first audio 4.05 s "
        "(end of speech to first audio 2.05 s)")
    stage_logs = [r.getMessage() for r in caplog.records]
    assert stage_logs[1] == "Timing: transcript +0.50 s (2.50 s after wake)"
    assert all(r.levelno == logging.INFO for r in caplog.records)


def test_turn_timer_partial_turn_and_start_time():
    clock = FakeClock(50.0)
    timer = TurnTimer(clock, "wake", at=48.0)  # wake fired 2 s ago
    timer.mark("speech_end")
    assert timer.summary() == "Turn timings from wake: end of speech +2.00 s"


class TimedTTS:
    """Takes `delay` seconds (fake) before sound starts, then reports it."""

    def __init__(self, clock, delay):
        self.clock, self.delay = clock, delay
        self.on_audio_start = None
        self.said = []

    def speak(self, text):
        self.clock.advance(self.delay)  # e.g. Piper synthesizing
        if self.on_audio_start:
            self.on_audio_start()
        self.clock.advance(2.0)  # playback; must not count
        self.said.append(text)


def run_one_turn(fake_client, clock, show_timings, output):
    """One wake-mode turn; each stage advances the fake clock by a known amount."""
    listens = iter([(True, np.zeros(16000, dtype=np.float32)), (False, None)])

    def listen(source):
        woke, rec = next(listens)
        if woke:
            listener.wake_time = clock()
            clock.advance(1.5)  # user speaks, then VAD hears the silence
        return woke, rec

    listener = SimpleNamespace(listen=listen, wake_time=None)

    def transcribe(rec):
        clock.advance(0.4)
        return "what's on my calendar tomorrow"

    def create(**kwargs):
        clock.advance(1.1)
        return make_response(text_block("Nothing tomorrow."))

    fake_client.messages.create.side_effect = create
    speaker = TimedTTS(clock, 0.2)
    cli.run_wake(ClaudeChat(client=fake_client), speaker,
                 SimpleNamespace(transcribe=transcribe), listener, None,
                 output_fn=output.append, show_timings=show_timings,
                 timing_clock=clock)
    assert speaker.said == ["Nothing tomorrow."]


EXPECTED = ("Turn timings from wake: end of speech +1.50 s, transcript +0.40 s, "
            "Claude reply +1.10 s, first audio +0.20 s; wake to first audio 3.20 s "
            "(end of speech to first audio 1.70 s)")


def test_run_wake_logs_turn_timings(fake_client, caplog):
    output = []
    with caplog.at_level(logging.INFO):
        run_one_turn(fake_client, FakeClock(), False, output)
    assert EXPECTED in [r.getMessage() for r in caplog.records]
    assert not any("timings" in line for line in output)  # log only


def test_timings_flag_prints_summary(fake_client):
    assert cli.parse_args(["--timings"]).timings
    assert not cli.parse_args([]).timings
    output = []
    run_one_turn(fake_client, FakeClock(), True, output)
    assert f"({EXPECTED})" in output


def test_turn_timings_reach_the_log_file(fake_client, tmp_path,
                                         restore_root_logging):
    from assistant.config import load_config, setup_logging

    cfg = load_config(None, None)
    cfg["logging"]["file"] = str(tmp_path / "assistant.log")
    path = setup_logging(cfg)
    run_one_turn(fake_client, FakeClock(), False, [])
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text()
    assert EXPECTED in text
    assert "Timing: Claude reply +1.10 s (3.00 s after wake)" in text


def test_speak_marks_call_time_for_engines_without_hook():
    clock = FakeClock()
    timer = TurnTimer(clock)
    calls = []
    plain = SimpleNamespace(speak=lambda text: (clock.advance(3), calls.append(text)))
    clock.advance(1)
    cli.speak(plain, "hi", timer)
    assert timer.marks["audio"] - timer.t0 == 1  # before the 3 s of speaking
    assert calls == ["hi"]


def test_print_tts_reports_audio_start():
    started = []
    engine = tts.PrintTTS(lambda line: started.append(line))
    engine.on_audio_start = lambda: started.append("start")
    engine.speak("hello")
    assert started == ["start", "[speaking] hello"]


def test_piper_reports_audio_start_before_playback(monkeypatch):
    events = []
    engine = tts.PiperTTS("voice.onnx")
    monkeypatch.setattr(engine, "synthesize",
                        lambda text: (np.ones(10, dtype=np.float32), 22050))
    monkeypatch.setattr(tts.audio, "play", lambda *a: events.append("play"))
    engine.on_audio_start = lambda: events.append("start")
    engine.speak("hello")
    assert events == ["start", "play"]


def test_pyttsx3_reports_audio_start_from_engine_callback():
    events = []

    class FakeEngine:
        def __init__(self):
            self.callbacks = []

        def connect(self, topic, cb):
            assert topic == "started-utterance"
            self.callbacks.append(cb)
            return "token"

        def disconnect(self, token):
            events.append(f"disconnect {token}")
            self.callbacks.clear()

        def say(self, text):
            events.append("say")

        def runAndWait(self):
            for cb in self.callbacks:
                cb(name=None)  # pyttsx3 passes the utterance name
            events.append("done")

    engine = tts.Pyttsx3TTS(fresh_engine=False)
    engine._engine = FakeEngine()
    engine.on_audio_start = lambda: events.append("start")
    engine.speak("hello")
    assert events == ["say", "start", "done", "disconnect token"]
