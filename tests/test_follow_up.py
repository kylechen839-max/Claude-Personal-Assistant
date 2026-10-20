"""Follow-up mode: after a reply, keep listening a few seconds without the
wake word. Audio comes from .wav fixtures through the real VAD; Claude,
Calendar and Whisper are mocked.

The files are played with a fake clock (`FileSource(clock=...)`), so audio
that goes by while the assistant is "speaking" is lost, exactly like the
real mic, which is closed during playback."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from assistant import audio, cli
from assistant.audio import SAMPLE_RATE
from assistant.chat import ClaudeChat
from assistant.tools import ToolRegistry
from assistant.tools.calendar import GoogleCalendar
from assistant.wakeword import FRAME_LENGTH, FileSource, WakeListener
from tests.conftest import first_text, make_response, text_block, tool_use_block
from tests.test_tools import DENTIST, make_service
from tests.test_wakeword import ScriptedDetector, tts_clip


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TimedTTS:
    """Speaking takes `seconds` of (fake) time; records what was said."""

    def __init__(self, clock, seconds=3.0):
        self.clock, self.seconds = clock, seconds
        self.on_audio_start = None
        self.said = []

    def speak(self, text):
        if self.on_audio_start:
            self.on_audio_start()
        self.said.append(text)
        self.clock.t += self.seconds


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("followup")
    return {name: tts_clip(tmp, text, name) for name, text in [
        ("wake", "Hey Claude"), ("cmd", "What is on my calendar tomorrow"),
        ("again", "And what about Friday"), ("reply", "You have the dentist at three."),
        ("delete", "Delete my dentist appointment"), ("yes", "Yes")]}


def build(tmp_path, parts):
    """Write parts [(name or seconds of silence)] to a .wav; return its path,
    the frame where the wake word ends, and each clip's (start, end) frame."""
    chunks, spans, pos = [], {}, 0
    for part in parts:
        chunk = audio.silence(part) if isinstance(part, (int, float)) else part[1]
        if not isinstance(part, (int, float)):
            spans[part[0]] = (pos // FRAME_LENGTH, (pos + len(chunk)) // FRAME_LENGTH)
        chunks.append(chunk)
        pos += len(chunk)
    path = tmp_path / "session.wav"
    audio.write_wav(path, np.concatenate(chunks))
    return path, spans


class Pipeline:
    """run_wake over a file with the real WakeListener/VAD and fake time."""

    def __init__(self, path, spans, transcripts, follow_up=5.0, clock_files=True,
                 speak_seconds=3.0):
        self.clock = FakeClock()
        self.source = FileSource(path, clock=self.clock if clock_files else None)
        self.chimes = []
        self.listener = WakeListener(
            ScriptedDetector(self.source, [spans["wake"][1]]),
            on_follow_up=lambda: self.chimes.append(self.clock.t))
        self.tts = TimedTTS(self.clock, speak_seconds)
        self.heard = []  # (start frame, seconds) of each recording transcribed
        self.transcripts = list(transcripts)
        self.output = []

    def transcribe(self, recording):
        self.heard.append(round(len(recording) / SAMPLE_RATE, 2))
        return self.transcripts.pop(0)

    def run(self, chat, follow_up=5.0):
        cli.run_wake(chat, self.tts, SimpleNamespace(transcribe=self.transcribe),
                     self.listener, self.source, output_fn=self.output.append,
                     clock=self.clock, timing_clock=self.clock,
                     follow_up_seconds=follow_up)


def memory_chat(fake_client):
    memory = MagicMock()
    memory.retrieve.return_value = []
    return ClaudeChat(client=fake_client, memory=memory), memory


def user_texts(fake_client):
    """The plain-text user turns Claude was sent (not summaries)."""
    from assistant.chat import is_user_turn

    return [[first_text(m["content"]) for m in c["messages"] if is_user_turn(m)]
            for c in fake_client.calls if "tools" in c or c["max_tokens"] != 200]


def test_follow_up_continues_the_conversation(tmp_path, clips, fake_client):
    # the reply takes 3 s (fake), so the follow-up starts 1 s after it ends
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 4.8,
                                   ("again", clips["again"]), 8.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "And what about Friday?"])
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Nothing on Friday.")),
                         make_response(text_block("Kyle checked his week."))]
    chat, memory = memory_chat(fake_client)
    p.run(chat)

    assert p.tts.said == ["The dentist at three.", "Nothing on Friday."]
    # one conversation: the second request was sent with the first exchange
    second = fake_client.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert first_text(second[2]["content"]) == "And what about Friday?"
    # a window opened after each reply; the second one timed out, which
    # ended the conversation and stored its summary
    assert len(p.chimes) == 2
    assert memory.store.call_count == 1
    assert memory.store.call_args.args[0].endswith("Kyle checked his week.")
    assert chat.history == []
    assert len(p.heard) == 2 and p.heard[1] > 0.8  # "And what about Friday"


def test_no_follow_up_goes_back_to_the_wake_word(tmp_path, clips, fake_client):
    """Speech after the window closes needs the wake word again."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 10.0,
                                   ("again", clips["again"]), 3.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"])
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Kyle asked about tomorrow."))]
    chat, memory = memory_chat(fake_client)
    p.run(chat)
    assert p.tts.said == ["The dentist at three."]
    assert len(p.heard) == 1  # "And what about Friday" was not recorded
    assert memory.store.call_count == 1  # summary stored when the window closed


def test_follow_up_off_keeps_old_behavior(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 4.8,
                                   ("again", clips["again"]), 8.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"])
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Kyle asked about tomorrow."))]
    chat, memory = memory_chat(fake_client)
    p.run(chat, follow_up=0)
    assert len(p.heard) == 1 and p.chimes == []
    assert memory.store.call_count == 1  # only at exit, as before


@pytest.mark.parametrize("mic_closed_while_speaking", [True, False])
def test_assistant_does_not_hear_itself(tmp_path, clips, fake_client,
                                        mic_closed_while_speaking):
    """The reply's own sound (here: in the file while the TTS 'plays') must
    not come back as a follow-up. The control case keeps the file running
    through playback, as if the mic stayed open, and does pick it up, which
    shows the fixture really contains the echo."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 1.0,
                                   ("reply", clips["reply"]), 8.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "You have the dentist at three."],
                 clock_files=mic_closed_while_speaking)
    fake_client.queue = [make_response(text_block("You have the dentist at three.")),
                         make_response(text_block("Says the same again.")),
                         make_response(text_block("NOTHING"))]
    chat, _ = memory_chat(fake_client)
    p.run(chat)
    if mic_closed_while_speaking:
        assert len(p.heard) == 1 and p.tts.said == ["You have the dentist at three."]
    else:
        assert len(p.heard) == 2  # heard its own reply


def test_follow_up_window_starts_after_playback(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 10.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?"],
                 speak_seconds=2.5)
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    p.run(ClaudeChat(client=fake_client))
    assert p.chimes == [2.5]  # opened when the 2.5 s of speech were over


def test_spoken_delete_confirmation_is_not_a_follow_up(tmp_path, clips, fake_client):
    """'Delete my dentist appointment' -> 'Do you want me to delete...?'
    -> 'Yes'. The yes answers the confirmation (it isn't sent to Claude as a
    new turn), the event is deleted, and then the follow-up window opens."""
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("delete", clips["delete"]), 4.5,
                                   ("yes", clips["yes"]), 9.0])
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
        make_response(text_block("Done, it's deleted.")),
        make_response(text_block("Kyle deleted his dentist appointment."))]
    chat, memory = memory_chat(fake_client)
    chat.tools = tools
    p.run(chat)

    assert p.tts.said[0].startswith("Do you want me to delete Dentist")
    assert p.tts.said[1:] == ["Done, it's deleted."]
    events.delete.assert_called_once()
    assert len(p.heard) == 2  # the command and the yes, nothing else
    assert user_texts(fake_client) == [["Delete my dentist appointment."],
                                       ["Delete my dentist appointment."]]
    assert len(p.chimes) == 1  # one follow-up window, after the final reply
    assert memory.store.call_count == 1


def test_no_answer_to_confirmation_cancels(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("delete", clips["delete"]), 12.0])
    p = Pipeline(path, spans, ["Delete my dentist appointment."])
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
    assert p.tts.said[-1] == "Okay, I kept it."


def test_goodbye_in_follow_up_ends_without_another_window(tmp_path, clips, fake_client):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 4.8,
                                   ("again", clips["again"]), 8.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?", "Goodbye."])
    fake_client.queue = [make_response(text_block("The dentist at three."))]
    p.run(ClaudeChat(client=fake_client))
    assert p.tts.said == ["The dentist at three.", "Goodbye!"]
    assert len(p.chimes) == 1


def test_follow_up_turn_is_timed(tmp_path, clips, fake_client, caplog):
    path, spans = build(tmp_path, [1.0, ("wake", clips["wake"]), 0.4,
                                   ("cmd", clips["cmd"]), 4.8,
                                   ("again", clips["again"]), 8.0])
    p = Pipeline(path, spans, ["What is on my calendar tomorrow?",
                               "And what about Friday?"])
    fake_client.queue = [make_response(text_block("The dentist at three.")),
                         make_response(text_block("Nothing on Friday."))]
    with caplog.at_level(logging.INFO, logger="assistant.cli"):
        p.run(ClaudeChat(client=fake_client))
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("Turn timings")]
    assert len(lines) == 2
    assert lines[0].startswith("Turn timings from wake:")
    assert lines[1].startswith("Turn timings from follow-up window:")


def test_strip_wake_phrase():
    assert cli.strip_wake_phrase("Hey Claude, what time is it?",
                                 "hey claude") == "what time is it?"
    assert cli.strip_wake_phrase("hey, Claude. Thanks", "hey claude") == "Thanks"
    assert cli.strip_wake_phrase("Hey Claudette", "hey claude") == "Hey Claudette"
    assert cli.strip_wake_phrase("I said hey claude", "hey claude") == "I said hey claude"


def test_only_the_wake_phrase_keeps_the_window_open(fake_client):
    said = []
    from assistant.tts import PrintTTS

    keep = cli.handle_command(ClaudeChat(client=fake_client), PrintTTS(said.append),
                              SimpleNamespace(transcribe=lambda r: "Hey Claude."),
                              [0.1], lambda *_: None, wake_phrase="hey claude")
    assert keep is True and said == [] and not fake_client.calls


def test_mic_drain_discards_buffered_audio():
    from assistant.wakeword import MicSource

    mic = MicSource()
    mic._stream = MagicMock(read_available=1234)
    mic.drain()
    mic._stream.read.assert_called_once_with(1234)


def test_follow_up_config_and_soft_chime(monkeypatch):
    from assistant import wakeword
    from assistant.config import DEFAULTS, load_config

    assert DEFAULTS["wake"]["follow_up_seconds"] == 5
    assert load_config("config.toml", None)["wake"]["follow_up_seconds"] == 5
    soft, wake = audio.make_follow_up_chime(), audio.make_chime()
    assert np.abs(soft).max() < np.abs(wake).max() / 2 and len(soft) < len(wake)
    # the soft chime is wired to the listener, and only for the live mic
    fake = SimpleNamespace(frame_length=FRAME_LENGTH, phrase="hey claude",
                           process=lambda frame: False)
    monkeypatch.setattr(wakeword, "make_detector", lambda **_: fake)
    monkeypatch.setattr(wakeword, "MicSource", lambda *a, **k: None)
    played = []
    monkeypatch.setattr(audio, "play", lambda samples, sr=16000: played.append(samples))
    listener, _, _ = cli.build_wake_components(cli.parse_args([]), DEFAULTS)
    listener.on_follow_up()
    assert played[0] is not None and len(played[0]) == len(soft)
    off = {**DEFAULTS, "wake": {**DEFAULTS["wake"], "follow_up_chime": False}}
    assert cli.build_wake_components(cli.parse_args([]), off)[0].on_follow_up is None
