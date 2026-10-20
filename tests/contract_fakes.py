"""A contract-shaped fake of the Messages API, for the conversation tests
that would otherwise need the real API (tests/test_contract.py).

Unlike the MagicMock clients elsewhere, it checks each request the way the
API does and answers with the SDK's own types (anthropic.types.Message,
ThinkingBlock, ToolUseBlock, TextBlock), streamed or not:

- roles alternate, starting with a user turn; every tool_use has its
  tool_result in the next message; content blocks are of known types;
- every reply starts with a thinking block whose signature is bound to this
  client ("this conversation"): one signed by another client fails with the
  API's 400 "Invalid `signature` in `thinking` block" (the bug of
  2026-10-20, when hands-free voice shared the phone's conversation);
- while a tool loop runs, the assistant message before the tool results
  must still start with its thinking block (the API's rule with thinking on).

Its answers are scripted from the request: a calendar question calls
list_calendar_events when offered, a tool result gets a short summary, and
"what number did I ask you to remember" is answered from the number in an
earlier user message actually sent (so lost history fails the check).
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import re
import secrets
import threading

import anthropic
import httpx
from anthropic.types import Message

KNOWN_BLOCKS = {"text", "image", "document", "tool_use", "tool_result", "thinking",
                "redacted_thinking", "server_tool_use", "web_search_tool_result",
                "web_fetch_tool_result"}
_NUMBER = re.compile(r"number (\d{4})")


def bad_request(message: str) -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return anthropic.BadRequestError(message, response=httpx.Response(
        400, request=request, json=body), body=body)


def _blocks(message: dict) -> list[dict]:
    content = message.get("content")
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _is_user_turn(message: dict) -> bool:
    return message["role"] == "user" and not any(
        b.get("type") == "tool_result" for b in _blocks(message))


def _text_of(message: dict) -> str:
    return " ".join(b.get("text", "") for b in _blocks(message) if b.get("type") == "text")


class ContractClient:
    """anthropic.Anthropic() stand-in: .messages.create / .messages.stream."""

    def __init__(self):
        self.key = secrets.token_bytes(16)  # signatures are bound to this client
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self._ids = itertools.count(1)
        self.messages = self  # client.messages.create(...)

    # --- the contract ---
    def sign(self, thinking: str) -> str:
        return hmac.new(self.key, thinking.encode(), hashlib.sha256).hexdigest()

    def validate(self, kwargs: dict) -> None:
        msgs = kwargs.get("messages") or []
        if not msgs or msgs[0]["role"] != "user":
            raise bad_request("messages: the first message must use the user role")
        for i, m in enumerate(msgs):
            if i and m["role"] == msgs[i - 1]["role"]:
                raise bad_request(f"messages.{i}: roles must alternate")
            for j, b in enumerate(_blocks(m)):
                kind = b.get("type")
                if kind not in KNOWN_BLOCKS:
                    raise bad_request(f"messages.{i}.content.{j}: unknown type {kind!r}")
                if kind == "thinking" and b.get("signature") != self.sign(b.get("thinking", "")):
                    raise bad_request(f"messages.{i}.content.{j}: Invalid `signature` "
                                      "in `thinking` block")
            if m["role"] == "assistant":
                calls = {b["id"] for b in _blocks(m) if b.get("type") == "tool_use"}
                answered = ({b.get("tool_use_id") for b in _blocks(msgs[i + 1])
                             if b.get("type") == "tool_result"} if i + 1 < len(msgs) else set())
                if calls - answered:
                    raise bad_request(f"messages.{i}: tool_use ids without a tool_result "
                                      "in the next message")
        last = msgs[-1]
        if not _is_user_turn(last) and len(msgs) >= 2:
            before = _blocks(msgs[-2])
            if not before or before[0].get("type") not in ("thinking", "redacted_thinking"):
                raise bad_request("messages: with thinking on, the assistant message "
                                  "before tool results must start with a thinking block")

    # --- the script ---
    def respond(self, kwargs: dict) -> Message:
        self.validate(kwargs)
        with self.lock:
            self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
            n = next(self._ids)
        msgs = kwargs["messages"]
        tools = {t["name"] for t in kwargs.get("tools") or []}
        last = msgs[-1]
        asked = next((_text_of(m) for m in reversed(msgs) if _is_user_turn(m)), "")
        thinking = f"Planning reply {n}."
        content = [{"type": "thinking", "thinking": thinking, "signature": self.sign(thinking)}]
        stop = "end_turn"
        if not _is_user_turn(last):  # tool results came back
            content.append({"type": "text", "text": "Tomorrow you have two events."})
        elif "calendar" in asked.lower() and "list_calendar_events" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "list_calendar_events", "input": {"day": "tomorrow"}})
            stop = "tool_use"
        elif "what number" in asked.lower():
            numbers = [x for m in msgs if _is_user_turn(m) for x in _NUMBER.findall(_text_of(m))]
            content.append({"type": "text", "text": (f"The number is {numbers[0]}."
                                                     if numbers else "I don't know.")})
        else:
            content.append({"type": "text", "text": "Okay, noted."})
        return Message.model_validate({
            "id": f"msg_{n:04d}", "type": "message", "role": "assistant",
            "model": kwargs.get("model", "test"), "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}})

    # --- the SDK's surface ---
    def create(self, **kwargs) -> Message:
        return self.respond(kwargs)

    def stream(self, **kwargs) -> _Stream:
        return _Stream(self.respond(kwargs))


class _Stream:
    def __init__(self, message: Message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    @property
    def text_stream(self):
        for block in self.message.content:
            if block.type == "text":
                yield block.text

    def get_final_message(self) -> Message:
        return self.message
