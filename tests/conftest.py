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
        client.calls.append(kwargs)
        return client.queue.pop(0)

    client.calls = []
    client.messages.create.side_effect = create
    return client
