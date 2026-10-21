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


def make_response(*blocks, stop_reason="end_turn", usage=None):
    """A Messages API response. `usage`: e.g. make_usage(...); left out,
    the response has no usage attribute (as the older tests assume)."""
    response = SimpleNamespace(content=list(blocks), stop_reason=stop_reason)
    if usage is not None:
        response.usage = usage
    return response


def make_usage(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0,
               cache_read_input_tokens=0, web_search_requests=None):
    """`response.usage` in the SDK's shape (anthropic.types.Usage)."""
    server = (SimpleNamespace(web_search_requests=web_search_requests)
              if web_search_requests is not None else None)
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_creation_input_tokens=cache_creation_input_tokens,
                           cache_read_input_tokens=cache_read_input_tokens,
                           server_tool_use=server)


def system_text(call) -> str:
    """The system prompt of a recorded request (a string or text blocks)."""
    system = call.get("system", "")
    if isinstance(system, str):
        return system
    return "\n\n".join(b["text"] for b in system if b.get("type") == "text")


def first_text(content) -> str:
    """A message's own words: the string, or its first text block (a sent
    user turn is [words, context] since the context moved out of system)."""
    if isinstance(content, str):
        return content
    return next((b["text"] for b in content
                 if isinstance(b, dict) and b.get("type") == "text"), "")


def own_blocks(content):
    """A sent user turn without the turn's context block (date and time,
    notes), which request_messages adds after the user's own blocks."""
    if isinstance(content, str):
        return content
    return [b for b in content if not (
        isinstance(b, dict) and b.get("type") == "text"
        and "Current local date and time:" in b.get("text", ""))]


def without_context(history):
    """Chat history as it was before turns carried their context
    (message["context"] and ["nudge"], see ClaudeChat.ask): for comparing
    whole messages."""
    return [{k: v for k, v in m.items() if k not in ("context", "nudge")}
            for m in history]


def sent_text(call) -> str:
    """Everything a recorded request sent: the system prompt and every
    message (all turns' context blocks included), for "never sent" checks."""
    import json

    return system_text(call) + "\n" + json.dumps(call.get("messages", []),
                                                 ensure_ascii=False)


def request_text(call) -> str:
    """Everything a recorded request told the model in text: the system
    prompt plus the last user turn's text blocks, which carry the turn's
    context (date and time, recalled notes, the review offer)."""
    from assistant.chat import is_user_turn

    parts = [system_text(call)]
    turn = next((m for m in reversed(call.get("messages", [])) if is_user_turn(m)),
                None)
    if turn is not None:
        content = turn["content"]
        parts += ([content] if isinstance(content, str) else
                  [b["text"] for b in content
                   if isinstance(b, dict) and b.get("type") == "text"])
    return "\n\n".join(parts)


# Models that answer a forced tool_choice ("tool" or "any") with a 400
# (claude-api skill: shared/tool-use-concepts.md, Tool Choice Options).
NO_FORCED_TOOL_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1",
                         "claude-mythos-5-1")


def api_bad_request(message):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error",
            "error": {"type": "invalid_request_error", "message": message}}
    return anthropic.BadRequestError(
        message, response=httpx.Response(400, request=request, json=body), body=body)


def _strict_schema_problem(schema, path="schema"):
    """What the API would reject in an output_config.format schema: every
    object needs additionalProperties: false."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            return f"{path}: additionalProperties must be false"
        for key, value in schema.items():
            problem = _strict_schema_problem(value, f"{path}.{key}")
            if problem:
                return problem
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            problem = _strict_schema_problem(value, f"{path}[{i}]")
            if problem:
                return problem
    return None


def check_api_rules(kwargs):
    """Raise the 400 the real Messages API returns for these request shapes."""
    choice = (kwargs.get("tool_choice") or {}).get("type")
    if choice in ("tool", "any") and str(kwargs.get("model", "")).startswith(
            NO_FORCED_TOOL_MODELS):
        raise api_bad_request(
            'tool_choice: type "tool" and "any" are not supported for this model.')
    fmt = (kwargs.get("output_config") or {}).get("format")
    if fmt is not None:
        problem = (_strict_schema_problem(fmt.get("schema"))
                   if fmt.get("type") == "json_schema"
                   else "output_config.format.type must be json_schema")
        if problem:
            raise api_bad_request(problem)


# Settings read from the environment (assistant.config.ENV_OVERRIDES, e.g.
# PINECONE_INDEX, TRUSTED_PROXY, PORT) plus WEB_TOKEN. On a PC or server that
# sets them (often through .env) they would leak into tests that check
# defaults; tests that need one set it themselves.
def _env_vars() -> tuple[str, ...]:
    from assistant.config import ENV_OVERRIDES

    from assistant.tools.google_auth import (CLIENT_ENV, PUBLIC_CLIENT_ENV, TOKEN_ENV,
                                             WEB_CLIENT_ENV)

    # and Kyle's Google sign-in (the server's), which family tests must not see
    return (*ENV_OVERRIDES, "WEB_TOKEN", "SPOTIFY_CLIENT_ID", TOKEN_ENV, CLIENT_ENV,
            WEB_CLIENT_ENV, PUBLIC_CLIENT_ENV,
            # team chat tokens (GITHUB_TOKEN is often set in a dev shell or CI)
            "SLACK_USER_TOKEN", "DISCORD_BOT_TOKEN", "GITHUB_TOKEN")


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
    """A mocked anthropic.Anthropic() whose messages.create returns queued
    replies, and raises the API's 400 for request shapes it rejects."""
    client = MagicMock()
    client.queue = []

    def create(**kwargs):
        # snapshot messages: the chat keeps appending to the same list
        client.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        check_api_rules(kwargs)
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


@pytest.fixture(autouse=True)
def _logs_in_tmp(tmp_path, monkeypatch):
    """setup_logging() (e.g. through cli.main) writes into tmp_path, never
    the real logs/assistant.log: on Kyle's PC a test run once left fake
    ERROR lines ("boom ...") in the running app's log."""
    from assistant import config as config_mod

    real = config_mod.setup_logging

    def setup_logging(config):
        log = config.get("logging", {})
        if log.get("file"):
            config = {**config, "logging": {**log, "file": str(
                tmp_path / "logs" / Path(log["file"]).name)}}
        return real(config)
    monkeypatch.setattr(config_mod, "setup_logging", setup_logging)


@pytest.fixture(autouse=True)
def _usage_in_tmp(tmp_path, monkeypatch):
    """Each test gets its own in-memory usage meter (assistant/usage.py),
    and a usage_file configured through cli.main lands in tmp_path, never
    the real data/usage.json."""
    from assistant import usage

    real = usage.configure
    monkeypatch.setattr(usage, "METER", usage.UsageMeter())
    monkeypatch.setattr(usage, "configure", lambda path: real(
        tmp_path / "data" / Path(path).name if path else path))
