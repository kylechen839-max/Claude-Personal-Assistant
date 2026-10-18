"""Voice conversation mode without HTTP: streamed chat, sentence TTS
chunks, barge-in cancellation and history truncation, spoken confirmations,
ending and memory. Anthropic is a fake streaming client; Whisper and TTS are
fakes, except one real eSpeak render."""

import json
import shutil
import sys
import threading
from unittest.mock import MagicMock

import anthropic
import httpx
import numpy as np
import pytest

from assistant.chat import ClaudeChat, has_web_content
from assistant.tools import Tool, ToolRegistry
from assistant.voice_session import (VoiceConversation, fix_stream_wav, synthesize_wav,
                                     wav_duration)
from tests.conftest import make_response, text_block, tool_use_block
from tests.voice_fakes import (Events, FakeSynth, FakeTranscriber, stream_client,
                               streamed)

SPEECH = np.zeros(16000, dtype=np.float32)


def make_conv(client, *heard, tools=None, memory=None, clock=None, synth=None, **kw):
    events = Events()
    holder = {}
    registry = tools or ToolRegistry()
    registry.confirm = lambda q: holder["c"].confirm(q)
    chat = ClaudeChat(client=client, model="test-model", tools=registry, memory=memory)
    conv = VoiceConversation(chat, FakeTranscriber(*heard), synth or FakeSynth(), events,
                             **({"clock": clock} if clock else {}), **kw)
    holder["c"] = conv
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


# --- ClaudeChat.ask_stream -------------------------------------------------------

def test_ask_stream_streams_text_and_keeps_history():
    client = stream_client(streamed("Hi ", "Kyle."))
    chat = ClaudeChat(client=client, model="m")
    got = []
    text, interrupted = chat.ask_stream("hello", got.append)
    assert (text, interrupted) == ("Hi Kyle.", False)
    assert got == ["Hi ", "Kyle."]
    assert chat.history == [{"role": "user", "content": "hello"},
                            {"role": "assistant", "content": [
                                {"type": "text", "text": "Hi Kyle."}]}]
    assert client.stream_calls[0]["model"] == "m"


def test_ask_stream_runs_tools_between_rounds():
    reg = ToolRegistry()
    reg.register(Tool(name="list_events", description="List.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: [{"summary": "Dentist"}]))
    client = stream_client(
        streamed("Let me check.", extra=[tool_use_block("list_events", {})],
                 stop_reason="tool_use"),
        streamed("You have the dentist."))
    chat = ClaudeChat(client=client, model="m", tools=reg)
    calls = []
    text, _ = chat.ask_stream("what's tomorrow", lambda d: calls.append(("text", d)),
                              on_tool=lambda n: calls.append(("tool", n)),
                              on_round_end=lambda: calls.append(("round",)))
    assert calls == [("text", "Let me check."), ("round",), ("tool", "list_events"),
                     ("text", "You have the dentist.")]
    assert text == "Let me check.You have the dentist."
    assert [m["role"] for m in chat.history] == ["user", "assistant", "user", "assistant"]
    assert chat.history[2]["content"][0]["type"] == "tool_result"


def test_ask_stream_error_rolls_back():
    client = MagicMock()
    client.messages.stream.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com"))
    chat = ClaudeChat(client=client, model="m")
    with pytest.raises(anthropic.APIConnectionError):
        chat.ask_stream("hello", lambda d: None)
    assert chat.history == []


def test_stop_mid_stream_keeps_partial_reply_valid():
    s = streamed("One. ", "Two. ", "Three.", hold_after=1)
    chat = ClaudeChat(client=stream_client(s), model="m")
    stop = threading.Event()
    result = {}
    t = threading.Thread(target=lambda: result.update(
        r=chat.ask_stream("count", lambda d: None, stop=stop)))
    t.start()
    assert s.held.wait(2)
    stop.set()
    chat.abort()
    t.join(2)
    assert result["r"] == ("One. ", True)
    assert chat.history[-1] == {"role": "assistant",
                                "content": [{"type": "text", "text": "One."}]}


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
    chat.ask_stream("weather?", lambda d: None)
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
    assert first == {"type": "audio", "turn": 1, "index": 0, "reply": True,
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


@pytest.mark.parametrize("answer, done", [("yes, delete it", ["Dentist"]),
                                          ("no", []), ("what's the weather", [])])
def test_spoken_confirmation_answers_and_is_never_a_request(answer, done):
    deleted = []
    client = stream_client(
        streamed("Okay.", extra=[tool_use_block("delete_event", {"title": "Dentist"})],
                 stop_reason="tool_use"),
        streamed("Done." if done else "Okay, I left it."))
    conv, chat, events = make_conv(client, "delete the dentist", answer,
                                   tools=delete_tool(deleted))
    conv.utterance(1, SPEECH)
    q = events.wait_for("confirm")
    assert q["question"] == "Do you want me to delete Dentist?"
    asked = events.wait_for("audio", index=1)  # "Okay." then the question
    assert asked["text"].endswith("Please say yes or no.") and asked["reply"] is False
    conv.interrupt(1, 1, 0.3)  # Kyle answers over the question: not a cancel
    conv.utterance(2, SPEECH)
    ans = events.wait_for("confirm_answer")
    assert ans["turn"] == 1 and ans["yes"] == bool(done)
    fin = events.wait_for("turn_done", turn=1)
    assert fin["interrupted"] is False
    assert deleted == done
    assert answer not in user_texts(client)  # the answer never went to Claude
    assert not events.of("transcript")[1:]  # and isn't a new turn
    result = json.loads(chat.history[2]["content"][0]["content"])
    assert ("deleted" in result) == bool(done)


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

    data = synthesize_wav(Pyttsx3TTS(), "Your dentist is tomorrow at three thirty.")
    assert data[:4] == b"RIFF"
    assert 1.0 < wav_duration(data) < 6.0
    assert synthesize_wav(Pyttsx3TTS(), "--help")[:4] == b"RIFF"  # text, not an option
