"""A contract-shaped fake of the Messages API, for the conversation tests
that would otherwise need the real API (tests/test_contract.py).

Unlike the MagicMock clients elsewhere, it checks each request the way the
API does and answers with the SDK's own types (anthropic.types.Message,
ThinkingBlock, ToolUseBlock, TextBlock), streamed or not:

- roles alternate, starting with a user turn; every tool_use has its
  tool_result in the next message; content blocks are of known types;
- every reply starts with a thinking block whose signature is bound to this
  client ("this conversation") and to the model that wrote it: one signed
  by another client, or by another model (Haiku's thinking sent to Sonnet
  after a mid-turn escalation), fails with the API's 400 "Invalid
  `signature` in `thinking` block" (the bug of 2026-10-20, when hands-free
  voice shared the phone's conversation);
- while a tool loop runs, the assistant message before the tool results
  must still start with its thinking block (the API's rule with thinking on);
- `system` is a string or a list of text blocks; a message has only `role`
  and `content` (a leaked "context" or "nudge" key is refused);
- cache_control: at most 4 breakpoints in a request (tools, system and
  messages together), never on a thinking or server tool block, never on
  an empty text block;
- output_config: a json_schema format whose objects all say
  additionalProperties false, never with a forced tool_choice.

Its answers are scripted from the request: a calendar question calls
list_calendar_events when offered, a tool result gets a short summary,
"what number did I ask you to remember" is answered from the number in an
earlier user message actually sent (so lost history fails the check), and
"get rid of ... on my calendar" lists the events, then deletes one with
delete_calendar_event when offered, and a structured-output request (the
student deadlines' extractor) gets one deadline per "<Title> is due
<YYYY-MM-DD>" sentence in its <text> blocks.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
import secrets
import threading

import anthropic
import httpx
from anthropic.types import Message

KNOWN_BLOCKS = {"text", "image", "document", "tool_use", "tool_result", "thinking",
                "redacted_thinking", "server_tool_use", "web_search_tool_result",
                "web_fetch_tool_result"}
# blocks that may not carry cache_control
NOT_CACHEABLE = {"thinking", "redacted_thinking", "server_tool_use",
                 "web_search_tool_result", "web_fetch_tool_result"}
MAX_CACHE_BREAKPOINTS = 4
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
    def sign(self, thinking: str, model: str) -> str:
        """Bound to this client and to the model that thought it."""
        return hmac.new(self.key, f"{model}\n{thinking}".encode(),
                        hashlib.sha256).hexdigest()

    def validate(self, kwargs: dict) -> None:
        msgs = kwargs.get("messages") or []
        model = str(kwargs.get("model", ""))
        self.validate_system(kwargs.get("system"))
        self.validate_cache_control(kwargs)
        self.validate_output_config(kwargs)
        if not msgs or msgs[0]["role"] != "user":
            raise bad_request("messages: the first message must use the user role")
        for i, m in enumerate(msgs):
            extra = sorted(set(m) - {"role", "content"})
            if extra:
                raise bad_request(f"messages.{i}.{extra[0]}: Extra inputs are not permitted")
            if i and m["role"] == msgs[i - 1]["role"]:
                raise bad_request(f"messages.{i}: roles must alternate")
            for j, b in enumerate(_blocks(m)):
                kind = b.get("type")
                if kind not in KNOWN_BLOCKS:
                    raise bad_request(f"messages.{i}.content.{j}: unknown type {kind!r}")
                if kind == "thinking" and b.get("signature") != self.sign(
                        b.get("thinking", ""), model):
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

    @staticmethod
    def validate_system(system) -> None:
        if system is None or isinstance(system, str):
            return
        if not isinstance(system, list):
            raise bad_request("system: Input should be a valid list")
        for i, b in enumerate(system):
            if not isinstance(b, dict) or b.get("type") != "text" \
                    or not isinstance(b.get("text"), str):
                raise bad_request(f"system.{i}: Input tag does not match the expected "
                                  "tag 'text'")

    @staticmethod
    def validate_cache_control(kwargs: dict) -> None:
        blocks = [("tools", t) for t in kwargs.get("tools") or []]
        system = kwargs.get("system")
        if isinstance(system, list):
            blocks += [("system", b) for b in system]
        for i, m in enumerate(kwargs.get("messages") or []):
            if isinstance(m.get("content"), list):
                blocks += [(f"messages.{i}", b) for b in m["content"]]
        marked = [(where, b) for where, b in blocks
                  if isinstance(b, dict) and b.get("cache_control") is not None]
        for where, b in marked:
            if b.get("type") in NOT_CACHEABLE:
                raise bad_request(f"{where}: cache_control cannot be set for "
                                  f"{b.get('type')} blocks")
            if b.get("type") == "text" and not b.get("text"):
                raise bad_request(f"{where}: cache_control cannot be set for empty "
                                  "text blocks")
        if len(marked) > MAX_CACHE_BREAKPOINTS:
            raise bad_request(f"A maximum of {MAX_CACHE_BREAKPOINTS} blocks with "
                              f"cache_control may be provided. Found {len(marked)}.")

    @staticmethod
    def validate_output_config(kwargs: dict) -> None:
        """Structured outputs: a json_schema format whose every object has
        additionalProperties false and lists only known properties as
        required; no forced tool call alongside it."""
        config = kwargs.get("output_config")
        if config is None:
            return
        fmt = (config or {}).get("format") or {}
        if fmt.get("type") != "json_schema" or not isinstance(fmt.get("schema"), dict):
            raise bad_request("output_config.format: Input tag does not match 'json_schema'")
        if (kwargs.get("tool_choice") or {}).get("type") in ("tool", "any"):
            raise bad_request("tool_choice: forced tool use can't be combined with "
                              "output_config.format")

        def check(node, where):
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    raise bad_request(f"{where}: For 'object' type, 'additionalProperties' "
                                      "must be explicitly set to false")
                unknown = set(node.get("required") or []) - set(node.get("properties") or {})
                if unknown:
                    raise bad_request(f"{where}: required lists unknown properties")
                for name, child in (node.get("properties") or {}).items():
                    check(child, f"{where}.properties.{name}")
            elif node.get("type") == "array":
                check(node.get("items") or {}, f"{where}.items")

        check(fmt["schema"], "output_config.format.schema")

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
        model = str(kwargs.get("model", ""))
        thinking = f"Planning reply {n}."
        content = [{"type": "thinking", "thinking": thinking,
                    "signature": self.sign(thinking, model)}]
        stop = "end_turn"
        called = ({b.get("name") for b in _blocks(msgs[-2]) if b.get("type") == "tool_use"}
                  if len(msgs) >= 2 else set())
        if not _is_user_turn(last) and called == {"list_calendar_events"} \
                and "get rid of" in asked.lower() and "delete_calendar_event" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "delete_calendar_event",
                            "input": {"event_id": "dentist-1"}})
            stop = "tool_use"
        elif not _is_user_turn(last) and called == {"delete_calendar_event"}:
            content.append({"type": "text", "text": "Done, it's off your calendar."})
        elif not _is_user_turn(last):  # tool results came back
            content.append({"type": "text", "text": "Tomorrow you have two events."})
        elif "calendar" in asked.lower() and "list_calendar_events" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "list_calendar_events", "input": {"day": "tomorrow"}})
            stop = "tool_use"
        elif kwargs.get("output_config") is not None:
            # structured output (student deadlines' extractor): one deadline
            # per "<title> is due <YYYY-MM-DD>" sentence in each <text>
            items = [{"text_index": int(i), "title": title, "course": "",
                      "kind": "assignment", "due": due}
                     for i, body in re.findall(r'<text index="(\d+)">\n(.*?)\n</text>',
                                               asked, re.S)
                     for title, due in re.findall(r"([A-Z][\w ]{1,40}?) is due "
                                                  r"(\d{4}-\d{2}-\d{2})", body)]
            content.append({"type": "text", "text": json.dumps({"items": items})})
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
