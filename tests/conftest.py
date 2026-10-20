from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def pytest_configure(config):
    """Windows: import comtypes on the main thread first. Importing it sets up
    COM for the importing thread only; if a web-app TTS worker thread were
    first (test_cloud.py renders reply audio), the main-thread SAPI tests
    would fail with "CoInitialize has not been called"."""
    import sys

    if sys.platform == "win32":
        try:
            import comtypes  # noqa: F401
        except ImportError:
            pass


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def make_response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


# Settings read from the environment (assistant.config.ENV_OVERRIDES, e.g.
# PINECONE_INDEX, TRUSTED_PROXY, PORT) plus WEB_TOKEN. On a PC or server that
# sets them (often through .env) they would leak into tests that check
# defaults; tests that need one set it themselves.
def _env_vars() -> tuple[str, ...]:
    from assistant.config import ENV_OVERRIDES

    return (*ENV_OVERRIDES, "WEB_TOKEN")


@pytest.fixture(autouse=True)
def clear_settings_env(monkeypatch):
    for var in _env_vars():
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def english_by_default(monkeypatch):
    """i18n.LANG is process-wide (cli.main and webapp.serve set it): every
    test starts in English and none leaks Chinese into the next."""
    from assistant import i18n

    monkeypatch.setattr(i18n, "LANG", "en")


@pytest.fixture
def fake_client():
    """A mocked anthropic.Anthropic() whose messages.create returns queued replies."""
    client = MagicMock()
    client.queue = []

    def create(**kwargs):
        # snapshot messages: the chat keeps appending to the same list
        client.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return client.queue.pop(0)

    class FakeStream:
        """messages.stream(...): the queued reply, its text in two pieces."""

        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def text_stream(self):
            for block in self.response.content:
                if block.type == "text" and block.text:
                    half = len(block.text) // 2 or 1
                    yield block.text[:half]
                    if block.text[half:]:
                        yield block.text[half:]

        def get_final_message(self):
            return self.response

    def stream(**kwargs):
        response = client.messages.create(**kwargs)  # same queue and errors
        return FakeStream(response)

    client.calls = []
    client.messages.create.side_effect = create
    client.messages.stream.side_effect = stream
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


@pytest.fixture(scope="session")
def kokoro_files():
    """The real Kokoro int8 model and voices in models/kokoro/, downloaded
    (checksum-verified) once; CI caches them. Skips when they can't be had."""
    from assistant import tts_kokoro

    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        pytest.skip("kokoro-onnx not installed")
    dest = Path(__file__).resolve().parent.parent / tts_kokoro.DEFAULT_DIR
    try:
        return tts_kokoro.download(dest, output_fn=lambda m: None)
    except Exception as e:
        pytest.skip(f"Kokoro model unavailable: {type(e).__name__}")


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
