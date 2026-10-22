"""Fakes for the voice conversation tests: a streaming Anthropic client
whose replies can be paused mid-way, a transcriber and a synthesizer."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from kwil import audio
from kwil.voice_session import wav_bytes
from tests.conftest import make_response, text_block


class StreamClosed(Exception):
    pass


class FakeStream:
    """One streamed response: yields text deltas, then the final message.
    `hold_after`: block after that many deltas until `release` is set or the
    stream is closed (to simulate a barge-in mid-reply). `quiet_close`: a
    closed stream just ends, as a closed TLS socket can read as end of
    stream (then the final message is whatever had arrived)."""

    def __init__(self, parts, final, hold_after=None, delay=0.0, quiet_close=False):
        self.parts = parts
        self.quiet_close = quiet_close
        self.final = final
        self.hold_after = hold_after
        self.delay = delay
        self.release = threading.Event()
        self.closed = threading.Event()
        self.held = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed.set()
        return False

    def __iter__(self):
        for i, part in enumerate(self.parts):
            if self.hold_after is not None and i == self.hold_after:
                self.held.set()
                while not (self.release.is_set() or self.closed.is_set()):
                    time.sleep(0.005)
            if self.closed.is_set():
                if self.quiet_close:
                    return
                raise StreamClosed("stream closed")
            if self.delay:
                time.sleep(self.delay)
            yield SimpleNamespace(type="content_block_delta",
                                  delta=SimpleNamespace(type="text_delta", text=part))

    @property
    def text_stream(self):
        for event in self:
            yield event.delta.text

    def get_final_message(self):
        return self.final

    def close(self):
        self.closed.set()


def streamed(*parts, extra=(), stop_reason="end_turn", **kw):
    """A FakeStream whose final message has the joined text (plus `extra` blocks)."""
    blocks = ([text_block("".join(parts))] if parts else []) + list(extra)
    return FakeStream(list(parts), make_response(*blocks, stop_reason=stop_reason), **kw)


def stream_client(*streams):
    """A mocked anthropic client: messages.stream returns the queued streams
    in order; messages.create (summaries) returns queued responses."""
    client = MagicMock()
    client.streams = list(streams)
    client.stream_calls = []
    client.create_queue = []

    def stream(**kwargs):
        client.stream_calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return client.streams.pop(0)

    client.messages.stream.side_effect = stream
    client.messages.create.side_effect = lambda **kw: client.create_queue.pop(0)
    return client


class FakeTranscriber:
    def __init__(self, *texts, delay=0.0):
        self.texts = list(texts)
        self.delay = delay
        self.heard = []

    def __call__(self, samples):
        self.heard.append(samples)
        if self.delay:
            time.sleep(self.delay)
        return self.texts.pop(0)


class FakeSynth:
    """Each sentence becomes 0.5 s of tone; records what it rendered."""

    def __init__(self, delay=0.0):
        self.texts = []
        self.delay = delay

    def __call__(self, text):
        self.texts.append(text)
        if self.delay:
            time.sleep(self.delay)
        t = np.arange(int(audio.SAMPLE_RATE * 0.5)) / audio.SAMPLE_RATE
        return wav_bytes((0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32),
                         audio.SAMPLE_RATE)


class Events:
    """Collects what the conversation sends, thread-safely."""

    def __init__(self):
        self.items = []
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)

    def __call__(self, event, data=None):
        with self.changed:
            self.items.append((event, data))
            self.changed.notify_all()

    def types(self):
        with self.lock:
            return [e["type"] for e, _ in self.items]

    def of(self, kind):
        with self.lock:
            return [e for e, _ in self.items if e["type"] == kind]

    def wait_for(self, kind, count=1, timeout=5.0, **match):
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                found = [e for e, _ in self.items if e["type"] == kind
                         and all(e.get(k) == v for k, v in match.items())]
                if len(found) >= count:
                    return found[count - 1]
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no {kind} {match} in {self.types()}")
                self.changed.wait(left)
