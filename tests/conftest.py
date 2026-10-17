from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def make_response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


@pytest.fixture
def fake_client():
    """A mocked anthropic.Anthropic() whose messages.create returns queued replies."""
    client = MagicMock()
    client.queue = []

    def create(**kwargs):
        # snapshot messages: the chat keeps appending to the same list
        client.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return client.queue.pop(0)

    client.calls = []
    client.messages.create.side_effect = create
    return client


@pytest.fixture(scope="session")
def piper_voice():
    """The real en_US-lessac-medium Piper voice, downloaded into voices/ once.

    Skips only when the voice can't be fetched (e.g. huggingface.co blocked).
    """
    from assistant.tts import DEFAULT_PIPER_VOICE, download_voice

    voices = Path(__file__).resolve().parent.parent / "voices"
    try:
        return download_voice(DEFAULT_PIPER_VOICE, voices)
    except Exception as e:
        pytest.skip(f"Piper voice {DEFAULT_PIPER_VOICE} unavailable: {type(e).__name__}")


@pytest.fixture
def restore_root_logging():
    """For tests that call setup_logging() (e.g. through cli.main): put the
    root logger's handlers and level back afterwards."""
    import logging

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        if h not in handlers:
            h.close()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)
