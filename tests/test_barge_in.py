"""Barge-in on the PC wake-word loop: talking over a reply stops it and the
new request is heard. Audio comes from .wav files through the real
FileSource (paced like a mic, `SPEED` times faster than real time), the real
WakeListener/VAD and BargeInSpeaker; the speaker is a fake sounddevice, so
"playing" a reply takes (sped-up) time and is stopped the way a real one is,
through audio.play. Claude, Calendar and Whisper are mocked."""

import logging
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from assistant import audio, cli
from assistant.audio import SAMPLE_RATE
from assistant.chat import ClaudeChat
from assistant.tools import ToolRegistry
from assistant.tools.calendar import GoogleCalendar
from assistant.wakeword import (FRAME_LENGTH, BargeInSpeaker, EchoGate,
                                EnergyVAD, FileSource, Interruption, WakeListener, heard_part)
from tests.conftest import first_text, make_response, text_block, tool_use_block
from tests.test_follow_up import build, memory_chat, user_texts
from tests.test_tools import DENTIST, make_service
from tests.test_wakeword import ScriptedDetector, tts_clip

SPEED = 6.0  # file seconds per real second
REPLY = 6.0  # seconds a reply takes; cut off means < CUT
CUT = 4.0
CHIME = 1234  # samples of the fake wake chime


@pytest.fixture(autouse=True)
def speaker(monkeypatch):
    """A fake sounddevice: play() returns at once, as the real one does."""
    class Calls(list):
        lengths: list = []

    calls = Calls()
    calls.lengths = []

    def play(samples, sr):
        calls.append("play")
        calls.lengths.append(len(samples))

    fake = SimpleNamespace(play=play, get_stream=lambda: SimpleNamespace(latency=0.0),
                           wait=lambda: calls.append("wait"),
                           stop=lambda: calls.append("stop"))
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake)
    audio.allow_playback()
    yield calls
    audio.allow_playback()


class PlayingTTS:
    """Says each reply with audio.play, taking `seconds` of file time; records
    how much of each one played (in file seconds) and where the file was."""

    def __init__(self, source, seconds=3.0):
        self.source, self.seconds = source, seconds
        self.on_audio_start = None
        self.said, self.played, self.at = [], [], []

    def speak(self, text):
        self.said.append(text)
        self.at.append(self.source.position)
        if self.on_audio_start:
            self.on_audio_start()
        start = time.monotonic()
        audio.play(np.zeros(int(self.seconds / SPEED * SAMPLE_RATE), np.float32))
        self.played.append(round((time.monotonic() - start) * SPEED, 2))


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bargein")
    return {name: tts_clip(tmp, text, name) for name, text in [
        ("wake", "Hey Claude"), ("cmd", "What is on my calendar tomorrow"),
        ("again", "And what about Friday"), ("stop", "Stop"),
        ("reply", "You have the dentist at three and then a meeting with Sam "
                  "at five. After that you are free."),
        ("delete", "Delete my dentist appointment"), ("yes", "Yes")]}


class Pipeline:
    def __init__(self, path, spans, transcripts, mode="voice", fire_at=(),
                 speak_seconds=REPLY):
        self.source = FileSource(path, pace=SPEED)
        self.wakes = []
        self.listener = WakeListener(
            ScriptedDetector(self.source, [spans["wake"][1], *fire_at]),
            on_wake=self.chime)
        self.listener.detector.phrase = "hey claude"  # as OpenWakeWordDetector
        self.voice = PlayingTTS(self.source, speak_seconds)
        self.tts = BargeInSpeaker(self.voice, self.listener, self.source, mode=mode)
        self.heard = []  # seconds of each recording transcribed
        self.transcripts = list(transcripts)
        self.output = []

    def chime(self):
        self.wakes.append(self.source.position)
        audio.play(np.zeros(CHIME, np.float32))

    def transcribe(self, recording):
        self.heard.append(round(len(recording) / SAMPLE_RATE, 2))
        return self.transcripts.pop(0)

    def run(self, chat, follow_up=0.0):
        cli.run_wake(chat, self.tts, SimpleNamespace(transcribe=self.transcribe),
                     self.listener, self.source, output_fn=self.output.append,
                     follow_up_seconds=follow_up)


def frames_of(seconds):
    return int(seconds * SAMPLE_RATE / FRAME_LENGTH)


# --- the gate on its own ---------------------------------------------------

def tone(level, n=1):
    """n frames of a sine at RMS `level`."""
    t = np.arange(FRAME_LENGTH * n) / SAMPLE_RATE
    wave = np.sin(2 * np.pi * 220 * t) * level * np.sqrt(2)
    return (wave * 32767).astype(np.int16).reshape(n, FRAME_LENGTH)


def first_trigger(gate, frames):
    for i, frame in enumerate(frames):
        if gate.process(frame):
            return i
    return None


def test_gate_ignores_the_replys_echo_and_hears_louder_speech():
    gate = EchoGate(min_level=0.015, ratio=2.5, sustain_ms=400)
    gate.audio_started()
    echo = tone(0.05, 60)  # well above the room's threshold
    assert first_trigger(gate, echo) is None
    assert first_trigger(gate, tone(0.2, 30)) is not None  # 4x the echo


def test_gate_needs_sustained_speech():
    gate = EchoGate(min_level=0.015, sustain_ms=400)
    gate.audio_started()
    first_trigger(gate, tone(0.01, 20))  # measure a quiet reply (600 ms)
    short = np.concatenate([tone(0.3, 5), tone(0.01, 20)])  # a 160 ms cough
    assert first_trigger(gate, short) is None
    i = first_trigger(gate, tone(0.3, 30))
    assert i is not None and i * 32 >= 200  # needs most of 400 ms


def test_gate_without_calibration_would_trigger_on_the_echo():
    """Control: the same echo counts as speech for a gate that never
    measured it, so the test above really depends on the echo level."""
    gate = EchoGate(min_level=0.015, sustain_ms=400)
    assert first_trigger(gate, tone(0.05, 60)) is not None


def test_gate_waits_for_a_late_echo():
    """With output + input latency the echo reaches the mic ~300 ms after
    the sound starts; the echo level is measured on it, not on the silence
    before it."""
    late = np.concatenate([tone(0.0, 10), tone(0.05, 60)])  # 320 ms of nothing first
    gate = EchoGate(min_level=0.015, ratio=2.5, sustain_ms=400)
    gate.audio_started(delay=0.3)  # the stream latencies
    assert first_trigger(gate, late) is None
    assert first_trigger(gate, tone(0.2, 30)) is not None
    # control: measured (250 ms) without the delay, the silence is taken as the echo
    blind = EchoGate(min_level=0.015, ratio=2.5, sustain_ms=400, calibrate_ms=250)
    blind.audio_started(delay=0.0)
    assert first_trigger(blind, late) is not None


def test_gate_allows_an_echo_later_than_reported():
    """Bluetooth or a driver adds delay the sound card doesn't report: the
    echo comes 250 ms after the reported 100 ms. It is still measured as
    echo (600 ms of measuring), and louder talking still counts."""
    late = np.concatenate([tone(0.0, 11), tone(0.05, 60)])  # 350 ms of nothing
    gate = EchoGate(min_level=0.015, ratio=2.5, sustain_ms=400)
    gate.audio_started(delay=0.1)
    assert first_trigger(gate, late) is None
    assert first_trigger(gate, tone(0.2, 30)) is not None
    # control: measuring only 250 ms takes the silence for the echo
    short = EchoGate(min_level=0.015, ratio=2.5, sustain_ms=400, calibrate_ms=250)
    short.audio_started(delay=0.1)
    assert first_trigger(short, late) is not None


def test_echo_delay_comes_from_the_streams(monkeypatch):
    from assistant import wakeword

    speaker = BargeInSpeaker(SimpleNamespace(speak=print), SimpleNamespace(),
                             SimpleNamespace(latency=0.12), mode="voice")
    monkeypatch.setattr(wakeword.audio, "output_latency", lambda: 0.2)
    assert speaker._echo_delay() == pytest.approx(0.32)
    monkeypatch.setattr(wakeword.audio, "output_latency", lambda: None)
    assert speaker._echo_delay() == wakeword.DEFAULT_ECHO_DELAY
    from assistant.wakeword import MicSource

    mic = MicSource()
    assert mic.latency is None
    mic._stream = SimpleNamespace(latency=0.09)
    assert mic.latency == 0.09


def test_heard_part_in_chinese_counts_characters():
    text = "明天下午三点你要去看牙医，然后五点和同事开会。"
    assert heard_part(text, 1.0) == "明天下午..."
    assert heard_part(text, 60) == text and heard_part(text, 0) == ""


def test_heard_part_estimates_what_was_said():
    text = "You have the dentist at three and then a meeting with Sam at five."
    assert heard_part(text, 0) == ""
    assert heard_part(text, 1.5) == "You have the dentist..."
    assert heard_part(text, 60) == text


# --- through the pipeline --------------------------------------------------

def test_talking_over_a_reply_stops_it_and_is_the_next_request(
        tmp_path, clips, fake_client, speaker, caplog):
    # the reply starts ~0.8 s after the command (end of turn) and lasts 3 s;
    # "And what about Friday" starts about 1 s into it
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("again", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "And what about Friday?"])
    fake_client.queue = [
        make_response(text_block("You have the dentist at three and then a "
                                 "meeting with Sam at five.")),
        make_response(text_block("Nothing on Friday.")),
        make_response(text_block("summary"))]
    chat = ClaudeChat(client=fake_client)
    with caplog.at_level(logging.DEBUG):
        p.run(chat)

    assert len(p.voice.said) == 2
    assert p.voice.played[0] < CUT  # cut off (it would take REPLY s)
    assert "stop" in speaker  # the sound was stopped
    assert p.voice.played[1] >= REPLY - 0.05  # the answer to Friday, in full
    # the interruption was sent to Claude as the next turn, no wake word
    second = fake_client.calls[1]["messages"]
    assert first_text(second[-1]["content"]) == "And what about Friday?"
    # history keeps only (about) what was heard, marked as interrupted
    assert "[interrupted" in second[1]["content"][-1]["text"]
    assert "Sam at five" not in second[1]["content"][-1]["text"]
    assert len(p.heard) == 2 and p.heard[1] > 0.8  # all of "And what about Friday"
    assert p.wakes == [spans["wake"][1] + 1]  # no second chime
    # INFO says what happened, never what was said
    info = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "Barge-in (voice)" in info
    assert "Friday" not in info and "dentist" not in info


def test_a_reply_nobody_talks_over_plays_to_the_end(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 5.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"])
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    p.run(ClaudeChat(client=fake_client))
    assert len(p.voice.played) == 1 and p.voice.played[0] >= REPLY - 0.05  # in full
    assert p.tts.interruption is None and len(p.heard) == 1


def test_the_replys_own_echo_does_not_stop_it(tmp_path, clips, fake_client):
    """The reply comes back through the mic (here: in the file while it
    plays, at a level well above the room's speech threshold): it plays on.
    Kyle talking over the same echo still stops it. The echo reaches the mic
    ~300 ms after the sound starts (the reply starts ~0.8 s after the
    command, the echo at 1.1 s), as with a high-latency sound card."""
    reply = clips["reply"] * 0.2
    for kyle in (False, True):
        mic = reply.copy()
        if kyle:
            mic[SAMPLE_RATE:SAMPLE_RATE + len(clips["again"])] += clips["again"]
        path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                       ("cmd", clips["cmd"]), 1.1,
                                       ("echo", mic), 3.0])
        assert EnergyVAD.rms(reply) > 0.015  # the echo alone is "speech" to the VAD
        p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                                   "And what about Friday?"])
        p.source.latency = 0.3  # the sound card's: echo heard 0.3 s after
        fake_client.queue = [make_response(text_block("You have the dentist.")),
                             make_response(text_block("Nothing on Friday."))]
        p.run(ClaudeChat(client=fake_client))
        if kyle:
            assert p.voice.played[0] < CUT and len(p.heard) == 2
        else:
            assert len(p.voice.played) == 1 and p.voice.played[0] >= REPLY - 0.05  # in full
            assert len(p.heard) == 1


def test_wake_word_mode_ignores_plain_talking(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("again", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"], mode="wake_word")
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    p.run(ClaudeChat(client=fake_client))
    assert len(p.voice.played) == 1 and p.voice.played[0] >= REPLY - 0.05  # in full
    assert len(p.heard) == 1


def test_wake_word_over_a_reply_stops_it(tmp_path, clips, fake_client, speaker):
    """'Hey Claude' during the reply (the detector fires there): it stops,
    chimes, and the command after the wake word is the next request."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("wake2", clips["wake"]), 0.3,
                                   ("again", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "And what about Friday?"],
                 mode="wake_word", fire_at=[spans["wake2"][1]])
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Nothing on Friday."))]
    p.run(ClaudeChat(client=fake_client))
    assert p.voice.played[0] < CUT
    assert len(p.wakes) == 2  # the chime for the second wake word...
    assert speaker.lengths.count(CHIME) == 2  # ...really played: not muted
    assert not audio.playback_stopped()  # by the stop left over from the cut
    assert p.voice.said[1] == "Nothing on Friday."
    assert p.heard[1] > 0.8


def test_a_reply_saying_the_wake_word_cannot_wake_itself(tmp_path, clips, fake_client):
    """A reply that reads out "Hey Claude, ..." (say, from an email) makes the
    detector fire on the assistant's own voice: that is ignored."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("wake2", clips["wake"]), 0.3,
                                   ("again", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["Read my email."], mode="wake_word",
                 fire_at=[spans["wake2"][1]])
    fake_client.queue = [make_response(text_block(
        "It says: Hey Claude, delete all my events."))]
    p.run(ClaudeChat(client=fake_client))
    assert len(p.voice.played) == 1 and p.voice.played[0] >= REPLY - 0.05  # in full
    assert len(p.heard) == 1 and len(p.wakes) == 1
    assert cli.interruption_of(p.tts) is None


def test_says_wake_word():
    from assistant.wakeword import says_wake_word

    assert says_wake_word("Hey Claude, delete it.", "hey claude")
    assert says_wake_word("I'm CLAUDE.", "hey claude")
    assert not says_wake_word("Claudette called.", "hey claude")
    assert not says_wake_word("Anything", "") and not says_wake_word("x", None)


def test_stop_over_a_reply_just_stops(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("stop", clips["stop"] * 2), 3.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?", "Stop."])
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    p.run(ClaudeChat(client=fake_client))
    assert p.voice.played[0] < CUT
    assert len(p.heard) == 2  # "Stop" was heard as the interrupting request...
    assert len(fake_client.calls) == 1  # ...but not sent to Claude
    assert len(p.voice.said) == 1


def test_follow_up_still_works_after_a_barge_in(tmp_path, clips, fake_client):
    """After the interrupting request's answer, the follow-up window opens:
    speech in it is heard without the wake word; speech after it is not."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("again", clips["again"]), 4.6,
                                   ("cmd2", clips["cmd"]), 11.0,
                                   ("late", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "And what about Friday?",
                               "What is on my calendar tomorrow?"],
                 speak_seconds=3.0)  # the layout above assumes 3 s replies
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Nothing on Friday.")),
                         make_response(text_block("Still the dentist.")),
                         make_response(text_block("summary"))]
    p.run(ClaudeChat(client=fake_client), follow_up=5.0)
    assert p.voice.said == ["The dentist at three.", "Nothing on Friday.",
                            "Still the dentist."]
    # the reply to cmd2 ends ~3.8 s after it, so its window closes ~9 s after
    # it: the late phrase (11 s) needed the wake word and wasn't heard
    assert len(p.heard) == 3


def test_talking_over_a_confirmation_question_is_never_a_yes(
        tmp_path, clips, fake_client):
    """'Delete my dentist appointment' -> the question plays -> 'Yes' said
    over it: the question was cut, so it's a no; nothing is deleted and the
    early 'yes' isn't sent to Claude either."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("delete", clips["delete"]), 1.8,
                                   ("yes", np.tile(clips["yes"], 3)), 4.0])
    p = Pipeline(path, spans, ["Delete my dentist appointment.", "Yes."])
    service, events = make_service(event=DENTIST)
    tools = ToolRegistry()
    tools.register(*GoogleCalendar(service=service,
                                   timezone="America/New_York").tools())
    tools.confirm = cli.voice_confirm(
        p.tts, lambda: p.listener.record_reply(p.source),
        SimpleNamespace(transcribe=p.transcribe), output_fn=p.output.append)
    fake_client.queue = [
        make_response(tool_use_block("delete_calendar_event", {"event_id": "ev1"}),
                      stop_reason="tool_use"),
        make_response(text_block("Okay, I kept it."))]
    p.run(ClaudeChat(client=fake_client, tools=tools))
    events.delete.assert_not_called()
    assert p.voice.said[0].startswith("Do you want me to delete Dentist")
    assert p.voice.played[0] < CUT
    assert "(Interrupted the question, so it's a no)" in p.output
    assert p.voice.said[-1] == "Okay, I kept it."
    assert len(p.heard) == 1  # the "yes" was never transcribed as an answer
    assert user_texts(fake_client) == [["Delete my dentist appointment."]] * 2


def test_barge_in_off_keeps_the_mic_closed(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.8,
                                   ("again", clips["again"]), 2.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"], mode="off")
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    started = []
    real_start = p.source.start
    p.source.start = lambda: (started.append(p.source.position), real_start())
    p.run(ClaudeChat(client=fake_client))
    assert len(p.voice.played) == 1 and p.voice.played[0] >= REPLY - 0.05  # in full
    assert len(started) == 2  # listening for the wake word, twice; never while speaking


# --- the parts ---------------------------------------------------------------

def test_play_stops_when_told(speaker):
    threading.Timer(0.05, audio.stop_playback).start()
    start = time.monotonic()
    audio.play(np.zeros(SAMPLE_RATE * 5, np.float32))
    assert time.monotonic() - start < 2 and speaker == ["play", "stop"]
    audio.play(np.zeros(100, np.float32))  # the rest of the reply stays silent
    assert speaker == ["play", "stop"]
    audio.allow_playback()
    audio.play(np.zeros(16, np.float32))
    assert speaker[-2:] == ["play", "wait"]


def test_pyttsx3_stops_from_its_own_word_callback(monkeypatch):
    """On Windows (SAPI) from the started-word callback."""
    from assistant import tts

    monkeypatch.setattr(tts.sys, "platform", "win32")

    class Engine:
        def __init__(self):
            self.callbacks, self.spoken, self.stopped = {}, [], False

        def connect(self, topic, cb):
            self.callbacks[topic] = cb
            return topic

        def disconnect(self, token):
            self.callbacks.pop(token)

        def say(self, text):
            self.words = text.split()

        def stop(self):
            self.stopped = True

        def runAndWait(self):
            for i, word in enumerate(self.words):
                if self.stopped:
                    return
                self.callbacks["started-word"](name=None, location=i, length=1)
                if self.stopped:  # stopped as the word started
                    return
                self.spoken.append(word)
                if i == 1:
                    audio.stop_playback()  # the mic thread heard Kyle

    engine = Engine()
    voice = tts.Pyttsx3TTS(fresh_engine=False, sapi_files=False)
    voice._engine = engine
    BargeInSpeaker(voice, SimpleNamespace(), None, mode="voice")  # sets stoppable
    try:
        voice.speak("one two three four five")
    finally:
        audio.allow_playback()
    assert engine.spoken == ["one", "two"] and engine.callbacks == {}


def test_kokoro_stops_without_rendering_the_rest(monkeypatch, tmp_path):
    from assistant import tts_kokoro

    rendered, played = [], []
    voice = tts_kokoro.KokoroTTS(tmp_path / "m.onnx", tmp_path / "v.bin")

    def synthesize(text):
        rendered.append(text)
        return np.ones(10, np.float32), 24000

    def play(samples, sr):
        played.append(len(samples))
        audio.stop_playback()  # Kyle talks during the first sentence

    monkeypatch.setattr(voice, "synthesize", synthesize)
    monkeypatch.setattr(tts_kokoro.audio, "play", play)
    try:
        voice.speak(" ".join(f"Sentence number {i}." for i in range(20)))
    finally:
        audio.allow_playback()
    assert played == [10]
    time.sleep(0.3)  # the render thread gives up too
    assert len(rendered) <= 4


def test_take_interruption_is_one_shot():
    holder = SimpleNamespace(interruption=Interruption("voice", None, 1.0, ""))
    assert cli.take_interruption(holder).by == "voice"
    assert cli.take_interruption(holder) is None
    assert cli.take_interruption(SimpleNamespace()) is None


def test_is_stop():
    assert cli.is_stop("Stop.") and cli.is_stop("never mind") and cli.is_stop("别说了！")
    assert not cli.is_stop("stop the timer") and not cli.is_stop("And what about Friday?")


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="barge_in"):
        BargeInSpeaker(object(), SimpleNamespace(), None, mode="always")


def test_config_and_wiring(monkeypatch, tmp_path):
    from assistant import wakeword
    from assistant.config import DEFAULTS, load_config

    wake = load_config("config.toml", None)["wake"]
    assert wake["barge_in"] == DEFAULTS["wake"]["barge_in"] == "wake_word"
    assert wake["barge_in_ms"] == 400 and wake["barge_in_echo_ratio"] == 2.5
    fake = SimpleNamespace(frame_length=FRAME_LENGTH, phrase="hey claude",
                           process=lambda frame: False)
    monkeypatch.setattr(wakeword, "make_detector", lambda **_: fake)
    monkeypatch.setattr(wakeword, "MicSource", lambda *a, **k: "mic")
    config = {**DEFAULTS, "wake": {**DEFAULTS["wake"], "barge_in": "voice",
                                   "barge_in_ms": 500}}
    listener, source, _ = cli.build_wake_components(cli.parse_args([]), config)
    voice = SimpleNamespace(speak=lambda text: None, on_audio_start=None)
    speaker = cli.barge_in_speaker(voice, listener, source, config)
    assert isinstance(speaker, BargeInSpeaker)
    assert speaker.mode == "voice" and speaker.barge_in_ms == 500
    off = {**DEFAULTS, "wake": {**DEFAULTS["wake"], "barge_in": "off"}}
    assert cli.barge_in_speaker(voice, listener, source, off) is voice
    # --audio-file: read in real time while replies play (barge-in on), as before when off
    wav = tmp_path / "in.wav"
    audio.write_wav(wav, audio.silence(0.5))
    args = cli.parse_args(["--audio-file", str(wav)])
    assert cli.build_wake_components(args, config)[1].pace == 1.0
    assert cli.build_wake_components(args, off)[1].pace == 0


def test_paced_file_comes_in_real_time(tmp_path):
    wav = tmp_path / "in.wav"
    audio.write_wav(wav, audio.silence(1.0))
    source = FileSource(wav, tail_silence=0, pace=4.0)  # 1 s of audio in 0.25 s
    start = time.monotonic()
    n = sum(1 for _ in source.frames())
    took = time.monotonic() - start
    assert n == frames_of(1.0) and 0.2 < took < 1.0


def test_a_request_said_over_goodbye_is_not_replayed_later():
    """'Goodbye' -> 'Bye!' with 'Hey Claude, delete my 3 pm' said over it;
    the next bare 'Hey Claude' must not send that old recording to Claude."""
    from assistant import i18n

    class TTS:
        def __init__(self):
            self.interruption, self.said = None, []

        def speak(self, text):
            self.interruption = None
            self.said.append(text)
            if text == i18n.t("goodbye"):
                self.interruption = Interruption("wake word",
                                                 np.full(16000, 0.1, np.float32), 0.3, "")

    class Listener:
        n = 0

        def listen(self, source):
            self.n += 1
            return (True, np.ones(16000, np.float32)) if self.n <= 2 else (False, None)

    script = iter(["Goodbye", "Hey Claude", "Delete my 3 pm meeting"])
    sent = []

    class Chat:
        history = []
        recorder = None

        def end_conversation(self):
            return None

    tts = TTS()
    original = cli.get_reply
    cli.get_reply = lambda chat, text, out=print, **k: sent.append(text) or "ok"
    try:
        cli.run_wake(Chat(), tts, SimpleNamespace(transcribe=lambda r: next(script)),
                     Listener(), None, output_fn=lambda *a: None)
    finally:
        cli.get_reply = original
    assert sent == []
    assert tts.interruption is None


def test_goodbye_drops_what_was_said_over_it(fake_client):
    from assistant import i18n

    class TTS:
        interruption = None

        def speak(self, text):
            self.interruption = Interruption("voice", np.ones(10, np.float32), 0.2, "")

    tts = TTS()
    keep = cli.handle_command(ClaudeChat(client=fake_client), tts,
                              SimpleNamespace(transcribe=lambda r: "Goodbye."),
                              [0.1], lambda *_: None)
    assert keep is False and tts.interruption is None and i18n.t("goodbye")


def test_no_request_message_names_what_interrupted(fake_client):
    out = []

    class TTS:
        interruption = None
        n = 0

        def speak(self, text):
            self.n += 1
            if self.n == 1:  # voice barge-in, but nothing recorded after it
                self.interruption = Interruption("voice", None, 1.0, "")

    class Listener:
        n = 0

        def listen(self, source):
            self.n += 1
            return (True, np.ones(16000, np.float32)) if self.n == 1 else (False, None)

    fake_client.queue = [make_response(text_block("The dentist at three."))]
    cli.run_wake(ClaudeChat(client=fake_client), TTS(),
                 SimpleNamespace(transcribe=lambda r: "What's on tomorrow?"),
                 Listener(), None, output_fn=out.append)
    assert "(stopped talking, but heard no request)" in out
    assert "(heard the wake word but no command)" not in out


@pytest.mark.parametrize("value, mode", [(False, "off"), ("", "off"), (None, "off"),
                                         ("Voice", "voice"), (" WAKE_WORD ", "wake_word"),
                                         ("wake-word", "wake_word"), (True, "wake_word"),
                                         ("off", "off")])
def test_barge_in_setting_is_normalised(value, mode, monkeypatch, tmp_path):
    from assistant import wakeword
    from assistant.config import DEFAULTS

    assert wakeword.barge_in_mode(value) == mode
    fake = SimpleNamespace(frame_length=FRAME_LENGTH, phrase="hey claude",
                           process=lambda frame: False)
    monkeypatch.setattr(wakeword, "make_detector", lambda **_: fake)
    wav = tmp_path / "in.wav"
    audio.write_wav(wav, audio.silence(0.5))
    config = {**DEFAULTS, "wake": {**DEFAULTS["wake"], "barge_in": value}}
    listener, source, _ = cli.build_wake_components(
        cli.parse_args(["--audio-file", str(wav)]), config)
    assert source.pace == (0 if mode == "off" else 1.0)
    voice = SimpleNamespace(speak=lambda text: None, on_audio_start=None)
    speaker = cli.barge_in_speaker(voice, listener, source, config)
    assert (speaker is voice) if mode == "off" else speaker.mode == mode


def test_bad_barge_in_setting_is_a_clear_error():
    from assistant.wakeword import barge_in_mode

    for bad in ("always", 3):
        with pytest.raises(ValueError, match=r"\[wake\] barge_in must be one of"):
            barge_in_mode(bad)


def test_pyttsx3_off_windows_renders_then_plays_so_it_can_be_stopped(monkeypatch):
    """eSpeak's driver plays through a blocking aplay that engine.stop()
    can't cut short: with barge-in on (not Windows) a reply is rendered to a
    file and played through audio.play, which stop_playback() stops."""
    from assistant import tts

    monkeypatch.setattr(tts.sys, "platform", "linux")
    played, said = [], []

    class Engine:
        def save_to_file(self, text, path):
            self.text = text
            self.path = path

        path = None

        def runAndWait(self):
            if self.path:  # rendering a file
                audio.write_wav(self.path, np.full(800, 0.1, np.float32))
                self.path = None

        def say(self, text):
            said.append(text)

        def connect(self, *a):
            return "t"

        def disconnect(self, token):
            pass

    voice = tts.Pyttsx3TTS(fresh_engine=False, sapi_files=False)
    voice._engine = Engine()
    monkeypatch.setattr(tts.audio, "play", lambda samples, sr: played.append(len(samples)))
    voice.speak("not interruptible")  # barge-in off: the usual way
    assert said == ["not interruptible"] and played == []
    BargeInSpeaker(voice, SimpleNamespace(), None, mode="voice")  # sets stoppable
    voice.speak("hello there")
    assert played == [800] and said == ["not interruptible"]


def test_pyttsx3_rendering_prints_nothing(monkeypatch, capsys):
    """eSpeak's driver prints "Audio saved to ..." on every save_to_file:
    that must not reach the console on every reply."""
    from assistant import tts

    monkeypatch.setattr(tts.sys, "platform", "linux")

    class Engine:
        path = None

        def save_to_file(self, text, path):
            self.path = path

        def runAndWait(self):
            print(f"Audio saved to {self.path}")
            audio.write_wav(self.path, np.full(800, 0.1, np.float32))

    voice = tts.Pyttsx3TTS(fresh_engine=False, sapi_files=False)
    voice._engine = Engine()
    voice.stoppable = True
    monkeypatch.setattr(tts.audio, "play", lambda samples, sr: None)
    voice.speak("hello there")
    assert "Audio saved" not in capsys.readouterr().out


def test_a_leftover_interruption_never_reaches_a_new_wake():
    """However an interruption was left on the TTS (here: before the first
    wake), a bare "Hey Claude" must not send its old recording to Claude."""
    stale = Interruption("voice", np.full(16000, 0.1, np.float32), 1.0, "")
    tts = SimpleNamespace(interruption=stale, speak=lambda text: None)

    class Listener:
        n = 0

        def listen(self, source):
            self.n += 1
            return (True, np.ones(16000, np.float32)) if self.n == 1 else (False, None)

    class Chat:
        history = []
        recorder = None

        def end_conversation(self):
            return None

    heard, sent = [], []
    original = cli.get_reply
    cli.get_reply = lambda chat, text, out=print, **k: sent.append(text) or "ok"
    try:
        cli.run_wake(Chat(), tts,
                     SimpleNamespace(transcribe=lambda r: heard.append(r) or "Hey Claude"),
                     Listener(), None, output_fn=lambda *a: None)
    finally:
        cli.get_reply = original
    assert sent == [] and len(heard) == 1 and tts.interruption is None


def test_idle_hook_waits_for_the_barge_in_turn(fake_client):
    """on_idle (resume Spotify) runs only when the loop is really back to
    waiting for the wake word: never between a barge-in and its turn."""
    events = []

    class TTS:
        interruption = None

        def speak(self, text):
            events.append(f"say {text}")
            if text == "First answer.":  # talked over: a new request
                self.interruption = Interruption("voice", np.ones(16000, np.float32),
                                                 1.0, "")

    class Listener:
        n = 0

        def listen(self, source):
            self.n += 1
            return (True, np.ones(16000, np.float32)) if self.n == 1 else (False, None)

    texts = iter(["First question", "Second question"])
    fake_client.queue = [make_response(text_block("First answer.")),
                         make_response(text_block("Second answer."))]
    cli.run_wake(ClaudeChat(client=fake_client), TTS(),
                 SimpleNamespace(transcribe=lambda r: next(texts)), Listener(), None,
                 output_fn=lambda *a: None, on_idle=lambda: events.append("idle"))
    assert events[:3] == ["say First answer.", "say Second answer.", "idle"]
