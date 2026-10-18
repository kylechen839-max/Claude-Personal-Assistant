"""Voice conversation mode without HTTP: streamed chat, sentence TTS
chunks, barge-in cancellation and history truncation, spoken confirmations,
ending and memory. Anthropic is a fake streaming client; Whisper and TTS are
fakes, except one real eSpeak render."""

import json
import shutil
import sys
import threading
import time
from unittest.mock import MagicMock

import anthropic
import httpx
import numpy as np
import pytest

from assistant.chat import ClaudeChat, has_web_content
from assistant.tools import Tool, ToolRegistry
from assistant.voice_session import (ChatBackend, VoiceConversation, fast_wav,
                                     fix_stream_wav, wav_duration)
from tests.conftest import make_response, text_block, tool_use_block
from tests.voice_fakes import (Events, FakeSynth, FakeTranscriber, stream_client,
                               streamed)

SPEECH = np.zeros(16000, dtype=np.float32)


def make_conv(client, *heard, tools=None, memory=None, clock=None, synth=None, **kw):
    events = Events()
    registry = tools or ToolRegistry()
    chat = ClaudeChat(client=client, model="test-model", tools=registry, memory=memory)
    backend = ChatBackend(chat, confirm_timeout=kw.pop("confirm_timeout", 60))
    registry.confirm = backend.confirm
    conv = VoiceConversation(backend, FakeTranscriber(*heard), synth or FakeSynth(),
                             events, **({"clock": clock} if clock else {}), **kw)
    return conv, chat, events


def delete_tool(deleted):
    reg = ToolRegistry()
    reg.register(Tool(
        name="delete_event", description="Delete an event.",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        handler=lambda title: deleted.append(title) or {"deleted": title},
        confirm_prompt=lambda i: f"Do you want me to delete {i['title']}?"))
    return reg


def user_texts(client):
    return [m["content"] for call in client.stream_calls for m in call["messages"]
            if m["role"] == "user" and isinstance(m["content"], str)]


# --- ClaudeChat streaming (ask with on_text/cancel), abort, mark_interrupted -------

def test_streamed_ask_runs_tools_between_rounds():
    reg = ToolRegistry()
    reg.register(Tool(name="list_events", description="List.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: [{"summary": "Dentist"}]))
    client = stream_client(
        streamed("Let me check.", extra=[tool_use_block("list_events", {})],
                 stop_reason="tool_use"),
        streamed("You have the dentist."))
    chat = ClaudeChat(client=client, model="m", tools=reg)
    got = []
    text = chat.ask("what's tomorrow", on_text=got.append)
    assert got == ["Let me check.", "You have the dentist."]
    assert text == "You have the dentist."
    assert [m["role"] for m in chat.history] == ["user", "assistant", "user", "assistant"]


def test_abort_closes_the_stream_at_once():
    s = streamed("One. ", "Two. ", "Three.", hold_after=1)
    chat = ClaudeChat(client=stream_client(s), model="m")
    cancel = threading.Event()
    result = {}
    t = threading.Thread(target=lambda: result.update(
        r=chat.ask("count", on_text=lambda d: None, cancel=cancel)))
    t.start()
    assert s.held.wait(2)  # stuck waiting for the next delta
    cancel.set()
    chat.abort()
    t.join(2)
    assert not t.is_alive() and s.closed.is_set()
    assert result["r"] == "One."
    assert chat.history[-1] == {"role": "assistant", "content": "One."}


def test_mark_interrupted_keeps_tool_and_web_blocks():
    web = make_response(
        text_block("Searching. "),
        MagicMock(type="web_search_tool_result",
                  model_dump=lambda **k: {"type": "web_search_tool_result",
                                          "tool_use_id": "srv_1", "content": []}),
        text_block("It will rain at 3 PM. Bring an umbrella."))
    from tests.voice_fakes import FakeStream
    client = stream_client(FakeStream(["Searching. ", "It will rain at 3 PM. ",
                                       "Bring an umbrella."], web))
    chat = ClaudeChat(client=client, model="m")
    chat.ask("weather?", on_text=lambda d: None)
    assert has_web_content(chat.history)
    assert chat.mark_interrupted("Searching. It will rain")
    content = chat.history[-1]["content"]
    assert [b["type"] for b in content] == ["web_search_tool_result", "text"]
    assert content[-1]["text"].startswith("Searching. It will rain [interrupted")
    assert has_web_content(chat.history)  # still asks before changes


# --- the conversation pipeline -----------------------------------------------------

def test_turn_streams_sentence_audio_before_reply_finishes():
    # the splitter needs the next word ("Then") to know 3:30 PM. ends a sentence
    s = streamed("Tomorrow you have the dentist at 3:30 PM. ", "Then", " lunch ",
                 "with Sam.", hold_after=2)
    conv, chat, events = make_conv(stream_client(s), "what's on tomorrow")
    conv.utterance(1, SPEECH)
    first = events.wait_for("audio")
    # the first sentence was spoken while Claude was still "writing"
    assert s.held.is_set() and not s.release.is_set()
    assert first == {"type": "audio", "turn": 1, "index": 0, "reply": True, "question": False,
                     "text": "Tomorrow you have the dentist at 3:30 PM.",
                     "duration": 0.5}
    s.release.set()
    done = events.wait_for("turn_done")
    audio = events.of("audio")
    assert [a["text"] for a in audio] == ["Tomorrow you have the dentist at 3:30 PM.",
                                          "Then lunch with Sam."]
    assert [a["index"] for a in audio] == [0, 1]
    assert done["interrupted"] is False and done["chunks"] == 2
    assert set(done["timings"]) >= {"transcript", "first_text", "first_sentence",
                                    "audio", "reply"}
    types = events.types()
    assert types[0] == "transcript" and types[-1] == "turn_done"
    # binary payload is a real WAV
    data = [d for e, d in events.items if e["type"] == "audio"][0]
    assert data[:4] == b"RIFF" and wav_duration(data) == pytest.approx(0.5)
    assert chat.history[-1]["content"][0]["text"].endswith("with Sam.")


def test_barge_in_cancels_generation_and_truncates_history():
    s = streamed("First sentence here. ", "Second one has six words. ", "Third",
                 " never gets said. ", "Fourth neither.", hold_after=3)
    synth = FakeSynth()
    conv, chat, events = make_conv(stream_client(s), "tell me a story", synth=synth)
    conv.utterance(1, SPEECH)
    events.wait_for("audio", index=1)
    # the page played chunk 0 fully and half of chunk 1, then Kyle talked
    conv.interrupt(1, played_index=1, played_fraction=0.5)
    done = events.wait_for("turn_done")
    assert done["interrupted"] is True
    assert s.closed.is_set()  # the Claude stream was aborted
    assert "Third never gets said." not in synth.texts
    assert len(events.of("audio")) == 2
    last = chat.history[-1]
    assert last["role"] == "assistant"
    assert last["content"] == [{"type": "text", "text":
                                "First sentence here. Second one... [interrupted: "
                                "Kyle started talking, so the rest of this reply "
                                "was not said]"}]
    assert chat.history[-2] == {"role": "user", "content": "tell me a story"}


def test_barge_in_after_reply_finished_still_truncates():
    conv, chat, events = make_conv(stream_client(streamed("Okay. ", "This is long.")),
                                   "hi")
    conv.utterance(1, SPEECH)
    events.wait_for("turn_done")
    conv.interrupt(1, 1, 0.0)  # audio was still playing on the phone
    assert chat.history[-1]["content"][0]["text"].startswith("Okay. [interrupted")


def test_interrupt_before_any_audio_and_next_turn_continues():
    s1 = streamed("Long answer. ", "More.", hold_after=0)
    client = stream_client(s1, streamed("Sure, the weather."))
    conv, chat, events = make_conv(client, "first question", "actually, weather?")
    conv.utterance(1, SPEECH)
    assert s1.held.wait(2)
    conv.interrupt(1, 0, 0.0)
    conv.utterance(2, SPEECH)
    events.wait_for("turn_done", turn=2)
    assert [m["role"] for m in chat.history] == ["user", "assistant", "user", "assistant"]
    assert "before I said anything" in chat.history[1]["content"][0]["text"]
    assert user_texts(client)[-1] == "actually, weather?"


def test_stale_interrupt_is_ignored():
    conv, chat, events = make_conv(stream_client(streamed("Hello there.")), "hi")
    conv.utterance(5, SPEECH)
    events.wait_for("turn_done")
    conv.interrupt(4, 0, 0)
    assert chat.history[-1]["content"][0]["text"] == "Hello there."


def ask_to_delete(answer, **kw):
    """A turn whose tool asks to delete Dentist; returns once the question's
    audio was sent. The second transcript is `answer`."""
    deleted = []
    client = stream_client(
        streamed("Okay.", extra=[tool_use_block("delete_event", {"title": "Dentist"})],
                 stop_reason="tool_use"),
        streamed("Done." if answer else "Okay, I left it."),
        streamed("Sure."))
    conv, chat, events = make_conv(client, "delete the dentist", answer, "yes",
                                   tools=delete_tool(deleted), **kw)
    conv.utterance(1, SPEECH)
    q = events.wait_for("confirm")
    assert q["question"] == "Do you want me to delete Dentist?"
    asked = events.wait_for("audio", question=True)
    assert asked["text"].endswith("Please say yes or no.") and asked["reply"] is False
    return conv, chat, events, client, deleted, asked


@pytest.mark.parametrize("answer, yes", [
    ("yes", True), ("Yeah, delete it.", True), ("go ahead", True), ("Yes please.", True),
    ("no", False), ("okay, and what about Friday?", False), ("okay", False),
    ("yes but not that one", False), ("what's the weather", False), ("", False)])
def test_only_an_explicit_yes_confirms(answer, yes):
    conv, chat, events, client, deleted, asked = ask_to_delete(answer)
    conv.chunk_played(1, asked["index"])  # the page heard the whole question
    conv.utterance(2, SPEECH)
    ans = events.wait_for("confirm_answer")
    assert ans["turn"] == 1 and ans["yes"] is yes
    events.wait_for("turn_done", turn=1)
    assert deleted == (["Dentist"] if yes else [])
    spoken = [a["text"] for a in events.of("audio")]
    assert ("OK, I won't delete it." in spoken) is (not yes)
    assert answer not in user_texts(client) or not answer  # never a request
    assert len(events.of("transcript")) == 1  # and not a new turn


def test_talking_over_the_question_cancels_the_action():
    conv, chat, events, client, deleted, asked = ask_to_delete("okay, and Friday?")
    conv.interrupt(1, asked["index"], 0.3)  # barge-in while the question plays
    ans = events.wait_for("confirm_answer")
    assert ans["yes"] is False and ans["reason"] == "interrupted"
    events.wait_for("turn_done", turn=1)
    assert deleted == []
    assert "OK, I won't delete it." in [a["text"] for a in events.of("audio")]
    # what Kyle said over it is a new request, not an answer
    conv.utterance(2, SPEECH)
    events.wait_for("turn_done", turn=2)
    assert deleted == []
    assert user_texts(client)[-1] == "okay, and Friday?"


def test_answer_before_the_question_was_heard_is_no():
    conv, chat, events, client, deleted, asked = ask_to_delete("yes")
    conv.utterance(2, SPEECH)  # page never reported the question as played
    ans = events.wait_for("confirm_answer")
    assert ans["yes"] is False and ans["reason"] == "not heard"
    events.wait_for("turn_done", turn=1)
    assert deleted == []
    assert "yes" not in user_texts(client)


def test_turns_never_overlap_and_confirmations_stay_with_their_turn():
    """Kyle talks again while a tool of turn 1 is still running: turn 2 waits
    for turn 1 to finish (one request at a time), and a confirmation turn 1
    asks after it was cancelled is declined, never attached to turn 2."""
    release, started = threading.Event(), threading.Event()
    running, overlap, late = [0], [0], {}
    reg = ToolRegistry()

    def slow_lookup():
        started.set()
        release.wait(5)
        late["yes"] = backend.confirm("Do you want me to delete Dentist?")
        return {"events": ["Dentist"]}

    reg.register(Tool(name="list_events", description="List.",
                      input_schema={"type": "object", "properties": {}},
                      handler=slow_lookup))
    client = stream_client(
        streamed("Let me look.", extra=[tool_use_block("list_events", {})],
                 stop_reason="tool_use"),
        streamed("The weather is sunny."))
    conv, chat, events = make_conv(client, "find my dentist", "what's the weather",
                                   tools=reg)
    backend = conv.backend
    real_ask = backend.ask

    def counting_ask(*a, **kw):  # how many turns ask Claude at the same time
        running[0] += 1
        overlap[0] = max(overlap[0], running[0])
        try:
            return real_ask(*a, **kw)
        finally:
            running[0] -= 1

    backend.ask = counting_ask
    conv.utterance(1, SPEECH)
    assert started.wait(5)
    conv.utterance(2, SPEECH)  # while list_events is still running
    time.sleep(0.1)
    assert len(client.stream_calls) == 1  # turn 2 hasn't started
    release.set()
    events.wait_for("turn_done", turn=2)
    assert overlap[0] == 1
    assert late["yes"] is False and not events.of("confirm")
    assert [m["content"] for m in chat.history if m["role"] == "user"
            and isinstance(m["content"], str)] == ["find my dentist", "what's the weather"]
    roles = [m["role"] for m in chat.history]
    assert all(a != b for a, b in zip(roles, roles[1:]))  # valid alternation


def test_superseded_utterances_are_not_transcribed():
    release, started = threading.Event(), threading.Event()
    reg = ToolRegistry()
    reg.register(Tool(name="slow", description="Slow.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: started.set() or (release.wait(5) and "ok")))
    client = stream_client(
        streamed(extra=[tool_use_block("slow", {})], stop_reason="tool_use"),
        streamed("Third answer."))
    conv, chat, events = make_conv(client, "first", "third", tools=reg)
    conv.utterance(1, SPEECH)
    assert started.wait(5)
    conv.utterance(2, SPEECH * 2)
    conv.utterance(3, SPEECH * 3)  # replaces 2 before it was transcribed
    release.set()
    events.wait_for("turn_done", turn=3)
    assert conv.dropped == 1
    heard = conv.transcribe.heard
    assert len(heard) == 2 and heard[1] is not None
    assert [e["turn"] for e in events.of("transcript")] == [1, 3]


def test_unanswered_confirmation_times_out_as_no():
    deleted = []
    client = stream_client(
        streamed(extra=[tool_use_block("delete_event", {"title": "X"})],
                 stop_reason="tool_use"),
        streamed("Okay."))
    conv, chat, events = make_conv(client, "delete X", tools=delete_tool(deleted),
                                   confirm_timeout=0.2)
    conv.utterance(1, SPEECH)
    events.wait_for("turn_done")
    assert deleted == []
    assert events.of("status")[0]["text"].startswith("No answer")


def test_goodbye_ends_and_saves_memory():
    memory = MagicMock()
    memory.retrieve.return_value = []
    client = stream_client(streamed("Your exam is Friday."))
    client.create_queue.append(make_response(text_block("Kyle's exam is Friday.")))
    conv, chat, events = make_conv(client, "when is my exam", "goodbye", memory=memory)
    conv.utterance(1, SPEECH)
    events.wait_for("turn_done", turn=1)
    conv.utterance(2, SPEECH)
    ended = events.wait_for("ended")
    assert ended == {"type": "ended", "reason": "goodbye", "saved": True}
    assert "Goodbye!" in [a["text"] for a in events.of("audio")]
    memory.store.assert_called_once()
    assert chat.history == []


def test_idle_and_max_length_expiry():
    now = [100.0]
    conv, chat, events = make_conv(stream_client(streamed("Hi.")), "hello",
                                   clock=lambda: now[0], idle_timeout=120,
                                   max_session=600)
    assert conv.expired() is None
    now[0] += 121
    assert conv.expired() == "idle"
    conv.utterance(1, SPEECH)  # activity resets the idle timer
    events.wait_for("turn_done")
    now[0] += 60
    assert conv.expired() is None
    now[0] += 500
    assert conv.expired() == "max_length"


def test_end_cancels_running_turn():
    s = streamed("A. ", "B. ", "C.", hold_after=1)
    conv, chat, events = make_conv(stream_client(s), "go")
    conv.utterance(1, SPEECH)
    assert s.held.wait(2)
    conv.end("closed")
    assert s.closed.is_set()
    assert events.wait_for("ended")["reason"] == "closed"
    conv.utterance(2, SPEECH)  # after the end: ignored
    assert len(events.of("transcript")) == 1


def test_api_error_is_spoken():
    client = MagicMock()
    client.messages.stream.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    conv, chat, events = make_conv(client, "hello")
    conv.utterance(1, SPEECH)
    events.wait_for("turn_done")
    assert events.of("error")[0]["message"] == "Sorry, I can't reach the internet right now."
    assert events.of("audio")[0]["text"] == "Sorry, I can't reach the internet right now."
    assert chat.history == []


def test_nothing_heard_does_not_call_claude():
    client = stream_client()
    conv, chat, events = make_conv(client, "  ")
    conv.utterance(1, SPEECH)
    events.wait_for("turn_done")
    assert client.stream_calls == [] and events.of("audio") == []


# --- speech synthesis -------------------------------------------------------------

def test_fix_stream_wav_and_duration():
    pcm = b"\x01\x00" * 22050
    header = (b"RIFF\x24\xf0\xff\x7fWAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x22\x56\x00\x00\x44\xac\x00\x00\x02\x00\x10\x00data\x00\xf0\xff\x7f")
    fixed = fix_stream_wav(header + pcm)
    assert int.from_bytes(fixed[40:44], "little") == len(pcm)
    assert wav_duration(fixed) == pytest.approx(1.0)


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("espeak-ng"),
                    reason="needs the espeak-ng program (Linux)")
def test_real_espeak_sentence():
    from assistant.tts import Pyttsx3TTS

    data = fast_wav(Pyttsx3TTS(), "Your dentist is tomorrow at three thirty.")
    assert data[:4] == b"RIFF"
    assert 1.0 < wav_duration(data) < 6.0
    assert fast_wav(Pyttsx3TTS(), "--help")[:4] == b"RIFF"  # text, not an option
    from assistant.webapp import render_wav
    assert render_wav(Pyttsx3TTS(), "Hello.")[:4] == b"RIFF"  # the web app uses it


def test_timings_flag_prints_voice_turn_lines():
    import logging

    from assistant import cli

    out = []
    handler = cli.show_voice_timings(out.append)
    log = logging.getLogger("assistant.voice_session")
    try:
        log.info("Voice heard: %r", "hi")
        log.info("Turn timings from utterance received: transcript +0.40 s")
    finally:
        log.removeHandler(handler)
    assert out == ["(Turn timings from utterance received: transcript +0.40 s)"]


def test_no_tool_runs_after_a_barge_in_before_the_tool_call():
    """Kyle says "stop" while "Okay, I'll add it." plays, after Claude asked
    for the tool but before it ran: the tool must not run, and history
    stays valid (every tool_use has a tool_result)."""
    added = []
    reg = ToolRegistry()
    reg.register(Tool(name="create_event", description="Create.",
                      input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
                      handler=lambda title: added.append(title) or "ok"))
    client = stream_client(
        streamed("Okay, I'll add it.", extra=[tool_use_block("create_event", {"title": "Gym"})],
                 stop_reason="tool_use"))
    chat = ClaudeChat(client=client, model="m", tools=reg)
    cancel = threading.Event()

    def on_text(delta):
        cancel.set()  # the barge-in lands while the reply is streaming

    reply = chat.ask("add gym tomorrow", on_text=on_text, cancel=cancel)
    assert added == []
    assert reply == "Okay, I'll add it."
    roles = [m["role"] for m in chat.history]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert chat.history[2]["content"][0]["is_error"] is True
    assert chat.history[1]["content"][-1]["type"] == "tool_use"


def test_web_chat_stream_sends_turn_events_to_its_caller_only():
    from assistant import webapp
    from assistant.tools import Tool as T

    reg = ToolRegistry()
    reg.register(T(name="list_events", description="List.",
                   input_schema={"type": "object", "properties": {}},
                   handler=lambda: "none"))
    client = stream_client(
        streamed(extra=[tool_use_block("list_events", {})], stop_reason="tool_use"),
        streamed("Nothing tomorrow."))
    wa = webapp.WebAssistant(lambda r: ClaudeChat(client=client, model="m", tools=r),
                             FakeTranscriber(), FakeSynth(), tools=reg)
    s = wa.session("sid-1")
    seen = []
    out = "".join(wa.chat_stream(s, "tomorrow?", on_event=seen.append))
    assert out == "Nothing tomorrow."
    assert seen == [{"type": "tool", "name": "list_events"}]
    assert s.events.empty()  # a page reading /api/events gets nothing of it
    assert s.listener is None


def test_no_conversation_text_in_info_logs(caplog):
    import logging

    memory = MagicMock()
    memory.retrieve.return_value = []
    client = stream_client(streamed("Your secret exam is Friday."))
    client.create_queue.append(make_response(text_block("Kyle's secret exam is Friday.")))
    conv, chat, events = make_conv(client, "when is my secret exam", memory=memory)
    with caplog.at_level(logging.INFO, logger="assistant"):
        conv.utterance(1, SPEECH)
        events.wait_for("turn_done")
        conv.end("user")
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("saved to memory" in m for m in info)
    assert not [m for m in info if "secret" in m]


def test_tool_inputs_and_memory_queries_stay_out_of_info_logs(caplog):
    import logging

    reg = ToolRegistry()
    reg.register(Tool(name="remember", description="Remember.",
                      input_schema={"type": "object", "properties": {"note": {"type": "string"}}},
                      handler=lambda note: "ok"))
    memory = MagicMock()
    memory.retrieve.return_value = []
    client = stream_client(
        streamed(extra=[tool_use_block("remember", {"note": "my locker code is 4512"})],
                 stop_reason="tool_use"),
        streamed("Got it."))
    chat = ClaudeChat(client=client, model="m", tools=reg)
    with caplog.at_level(logging.INFO, logger="assistant"):
        chat.ask("remember my locker code is 4512", on_text=lambda d: None)
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any(m.startswith("Claude called remember") for m in info)  # preflight reads this
    assert not [m for m in info if "4512" in m]


def test_memory_retrieve_logs_no_query_text(caplog):
    import logging

    from tests.test_memory import make_memory, match

    memory, _, _ = make_memory([match(0.9, "Kyle likes tea")])
    with caplog.at_level(logging.INFO, logger="assistant"):
        memory.retrieve("what is my locker code 4512")
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("Memory:" in m for m in info)
    assert not [m for m in info if "4512" in m]
