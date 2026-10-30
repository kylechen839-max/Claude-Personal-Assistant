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
  additionalProperties false, never with a forced tool_choice; an `effort`
  of low / medium / high / xhigh / max; no other key;
- thinking: {"type": "adaptive"} (optionally "display": "summarized" or
  "omitted"); "enabled" with budget_tokens is the API's 400 on the Claude 5
  models Kwil uses (only the 4.6 and older models took it), as is
  "disabled" on Opus/Sonnet 5.5 (Haiku 5.5 only up to high effort);
- server tools (web_search / web_fetch): only the fields the SDK's
  WebSearchTool20260209Param / WebFetchTool20260209Param list, the right
  name, and a `max_uses` of 1 or more;
- search_result blocks (Kwil's own data, cited like web results): only in
  user messages, top level or as a tool_result's whole content (never
  mixed with other blocks there), each with a source, a title and
  non-empty text blocks, citations enabled on all or none of them and of
  the request's document blocks together, on all when web_search is
  offered, and never with output_config.format;
- image blocks (the "+" photos and the voice view's camera frames): only
  the fields the SDK's ImageBlockParam lists, a base64 source with only
  Base64ImageSourceParam's fields, one of its media types, and data that
  decodes and starts like that type (a camera frame with an extra key, or
  a PNG sent as image/jpeg, is refused).

Sources for the thinking, effort and search_result rules (fetched
2026-10-30): platform.claude.com/docs/en/build-with-claude/thinking (the
per-model `thinking` table), .../extended-thinking (budget_tokens is
deprecated and a 400 on Claude 4.7 and later), .../effort
(output_config.effort levels) and .../search-results; the SDK's own
ThinkingConfigAdaptiveParam and OutputConfigParam types agree.

Its answers are scripted from the request: a calendar question calls
list_calendar_events when offered, a tool result gets a short summary,
"what number did I ask you to remember" is answered from the number in an
earlier user message actually sent (so lost history fails the check), and
"get rid of ... on my calendar" lists the events, then deletes one with
delete_calendar_event when offered, "from now on ..." / "always ..." is
saved with remember when offered, "remind me in N minutes to ..." sets
one with create_reminder and says back the time it gives, "research ..."
or "do a deep dive on ..." starts it with start_research, "what did we
decide about <topic>"
searches past chats with search_conversations, reads the first hit with
read_conversation and answers from its matching message (when offered),
"search the web for ..." (when web_search is offered) runs one server-side
search and answers with web_search_result_location citations, a
read_conversation result made of search_result blocks is answered with
a search_result_location citation of its best message,
deep research (research.py) is scripted too: its plan (a structured output
whose schema has an "outline") gets two parts, each "Research step N"
request runs up to two searches and one fetch (never more than the
request's max_uses; usage.server_tool_use counts them) and answers with
cited findings, and "Write the report" (with search_result blocks) writes a
Markdown report citing them;
"quiz me on <course>" turns study mode on with start_study and asks a
first question, and in study mode (its block in `system`) an answer that
starts "I think" is a wrong one: noted with note_missed (the course from
the earlier start_study), then explained, with the next question;
and a structured-output request (the
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
                "web_fetch_tool_result", "search_result"}
# what a tool_result's content list may hold
TOOL_RESULT_BLOCKS = {"text", "image", "document", "search_result"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# models that still took thinking {"type": "enabled", "budget_tokens"}
_LEGACY_THINKING = re.compile(r"claude-(?:opus|sonnet|haiku)-4-[0-6]")
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


def _topic(asked: str) -> str:
    """'what did we decide about my physics schedule last week?' -> 'physics schedule'"""
    m = re.search(r"about (?:my |the |our )?([\w ]+?)(?: last week| yesterday)?[?.]",
                  asked, re.I)
    return m.group(1) if m else asked[:40]


def _tool_json(message: dict) -> dict:
    """The JSON of the first tool result in `message` ({} if not JSON)."""
    for b in _blocks(message):
        if b.get("type") == "tool_result":
            content = b.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content)
            try:
                return json.loads(content)
            except (TypeError, ValueError):
                return {}
    return {}


# what the fake's web search finds (url, title)
WEB_RESULTS = (("https://physics.example.edu/exams", "PHYS 361 exam schedule"),
               ("https://news.example.org/a?b=1", "Campus news"))
# what deep research finds (url, title)
RESEARCH_PAGES = (("https://physics.example.edu/internships", "Summer physics internships"),
                  ("https://careers.example.org/reu", "NSF REU programs"),
                  ("https://news.example.com/deadlines", "Application deadlines"))


def _search_results(msgs: list[dict]) -> list[tuple[int, dict]]:
    """(search_result_index, block) of every search_result block sent, in
    request order (top level and inside tool results)."""
    out = []
    for m in msgs:
        for b in _blocks(m):
            inner = b.get("content") if b.get("type") == "tool_result" else [b]
            for c in inner if isinstance(inner, list) else []:
                if isinstance(c, dict) and c.get("type") == "search_result":
                    out.append((len(out), c))
    return out


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
        self.validate_thinking(kwargs)
        self.validate_search_results(kwargs)
        self.validate_images(kwargs)
        self.validate_tools(kwargs)
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
    def validate_images(kwargs: dict) -> None:
        """Image blocks as the SDK's ImageBlockParam / Base64ImageSourceParam."""
        import base64
        import binascii
        import typing

        from anthropic.types import Base64ImageSourceParam, ImageBlockParam

        block_keys = set(ImageBlockParam.__annotations__)
        source_keys = set(Base64ImageSourceParam.__annotations__)
        media = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG",
                 "image/gif": b"GIF8", "image/webp": b"RIFF"}
        def literals(t):
            # Python 3.10 wraps the hint as Required[Literal[...]]: unwrap to the strings
            out = set()
            for a in typing.get_args(t):
                out |= {a} if isinstance(a, str) else literals(a)
            return out

        allowed = literals(typing.get_type_hints(Base64ImageSourceParam)["media_type"])
        for i, m in enumerate(kwargs.get("messages") or []):
            for j, b in enumerate(_blocks(m)):
                if not isinstance(b, dict) or b.get("type") != "image":
                    continue
                where = f"messages.{i}.content.{j}"
                extra = sorted(set(b) - block_keys)
                if extra:
                    raise bad_request(f"{where}.{extra[0]}: Extra inputs are not permitted")
                src = b.get("source") or {}
                if src.get("type") != "base64":
                    raise bad_request(f"{where}.source: Input tag does not match 'base64'")
                extra = sorted(set(src) - source_keys)
                if extra:
                    raise bad_request(f"{where}.source.{extra[0]}: Extra inputs are not "
                                      "permitted")
                kind = src.get("media_type")
                if kind not in allowed:
                    raise bad_request(f"{where}.source.media_type: Input should be "
                                      "'image/jpeg', 'image/png', 'image/gif' or 'image/webp'")
                try:
                    data = base64.b64decode(src.get("data") or "", validate=True)
                except (binascii.Error, ValueError):
                    data = b""
                if not data.startswith(media.get(kind, b"\0")):
                    raise bad_request(f"{where}.source: Image does not match the provided "
                                      "media type" if data else
                                      f"{where}.source.data: invalid base64 data")

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
        extra = sorted(set(config or {}) - {"format", "effort"})
        if extra:
            raise bad_request(f"output_config.{extra[0]}: Extra inputs are not permitted")
        if "effort" in config and config["effort"] not in EFFORTS:
            raise bad_request("output_config.effort: Input should be 'low', 'medium', "
                              "'high', 'xhigh' or 'max'")
        if "format" not in config:
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

    @staticmethod
    def validate_thinking(kwargs: dict) -> None:
        """The `thinking` parameter as the Claude 5 models take it: adaptive
        (the default when it is left out), never a budget."""
        thinking = kwargs.get("thinking")
        if thinking is None:
            return
        model = str(kwargs.get("model", ""))
        effort = (kwargs.get("output_config") or {}).get("effort")
        if not isinstance(thinking, dict):
            raise bad_request("thinking: Input should be a valid dictionary")
        kind = thinking.get("type")
        allowed = {"adaptive": {"type", "display"}, "disabled": {"type"},
                   "enabled": {"type", "budget_tokens", "display"},
                   "between_tools": {"type", "display"}}
        if kind not in allowed:
            raise bad_request("thinking.type: Input tag does not match any of the expected "
                              "tags: 'adaptive', 'enabled', 'disabled', 'between_tools'")
        extra = sorted(set(thinking) - allowed[kind])
        if extra:
            raise bad_request(f"thinking.{kind}.{extra[0]}: Extra inputs are not permitted")
        if thinking.get("display", "omitted") not in ("summarized", "omitted"):
            raise bad_request("thinking.display: Input should be 'summarized' or 'omitted'")
        legacy = bool(_LEGACY_THINKING.search(model))
        if kind == "enabled" and not legacy:
            raise bad_request('"thinking.type.enabled" is not supported for this model. '
                              'Use "thinking.type.adaptive" and "output_config.effort" '
                              "to control thinking behavior.")
        if kind == "enabled" and not isinstance(thinking.get("budget_tokens"), int):
            raise bad_request("thinking.enabled.budget_tokens: Field required")
        if kind == "adaptive" and re.search(r"claude-(?:opus|sonnet|haiku)-4-5", model):
            raise bad_request("adaptive thinking is not supported on this model")
        high = effort in ("xhigh", "max")
        if kind == "disabled" and not legacy and ("haiku" not in model or high):
            raise bad_request("thinking.type.disabled is not supported for this model"
                              + (" at this effort" if "haiku" in model else ""))
        if kind == "between_tools" and ("sonnet-5-5" not in model or high):
            raise bad_request("thinking.type.between_tools is not supported for this model"
                              + (" at this effort" if "sonnet-5-5" in model else ""))

    @staticmethod
    def validate_tools(kwargs: dict) -> None:
        """Server tool definitions as the SDK types them (docs:
        agents-and-tools/tool-use/web-search-tool, .../web-fetch-tool)."""
        from anthropic.types import WebFetchTool20260209Param, WebSearchTool20260209Param

        for i, t in enumerate(kwargs.get("tools") or []):
            kind = str(t.get("type") or "")
            if not kind.startswith(("web_search_", "web_fetch_")):
                continue
            search = kind.startswith("web_search_")
            allowed = set((WebSearchTool20260209Param if search
                           else WebFetchTool20260209Param).__annotations__)
            extra = sorted(set(t) - allowed)
            if extra:
                raise bad_request(f"tools.{i}.{kind}.{extra[0]}: Extra inputs are not permitted")
            if t.get("name") != ("web_search" if search else "web_fetch"):
                raise bad_request(f"tools.{i}.{kind}.name: Input should be "
                                  f"'{'web_search' if search else 'web_fetch'}'")
            uses = t.get("max_uses")
            if uses is not None and (not isinstance(uses, int) or isinstance(uses, bool)
                                     or uses < 1):
                raise bad_request(f"tools.{i}.{kind}.max_uses: Input should be greater "
                                  "than 0")

    @staticmethod
    def validate_search_results(kwargs: dict) -> None:
        """search_result blocks (docs: build-with-claude/search-results)."""
        found = []  # (where, block)
        for i, m in enumerate(kwargs.get("messages") or []):
            for j, b in enumerate(_blocks(m)):
                where = f"messages.{i}.content.{j}"
                if b.get("type") == "search_result":
                    if m["role"] != "user":
                        raise bad_request(f"{where}: search_result blocks can only appear "
                                          "in user messages")
                    found.append((where, b))
                elif b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                    inner = b["content"]
                    if not all(isinstance(c, dict) for c in inner):
                        raise bad_request(f"{where}.content: Input should be a valid "
                                          "dictionary")
                    kinds = {c.get("type") for c in inner}
                    unknown = kinds - TOOL_RESULT_BLOCKS
                    if unknown:
                        raise bad_request(f"{where}.content: unknown type "
                                          f"{sorted(map(str, unknown))[0]!r}")
                    if "search_result" in kinds and kinds != {"search_result"}:
                        raise bad_request(f"{where}.content: a tool_result holding "
                                          "search_result blocks may hold nothing else")
                    found += [(f"{where}.content.{k}", c) for k, c in enumerate(inner)
                              if c.get("type") == "search_result"]
        enabled = set()
        # documents share the rule (citations on for all or none of a
        # request's documents; docs: build-with-claude/citations, which
        # calls both document and search_result blocks documents)
        for i, m in enumerate(kwargs.get("messages") or []):
            for b in _blocks(m):
                inner = b.get("content") if b.get("type") == "tool_result" else [b]
                for c in inner if isinstance(inner, list) else []:
                    if isinstance(c, dict) and c.get("type") == "document":
                        enabled.add(bool((c.get("citations") or {}).get("enabled")))
        for where, b in found:
            extra = sorted(set(b) - {"type", "source", "title", "content", "citations",
                                     "cache_control"})
            if extra:
                raise bad_request(f"{where}.{extra[0]}: Extra inputs are not permitted")
            for key in ("source", "title"):
                if not isinstance(b.get(key), str) or not b[key]:
                    raise bad_request(f"{where}.{key}: Field required")
            content = b.get("content")
            if not isinstance(content, list) or not content:
                raise bad_request(f"{where}.content: Field required")
            for k, c in enumerate(content):
                if not isinstance(c, dict) or c.get("type") != "text" \
                        or not isinstance(c.get("text"), str) or not c["text"]:
                    raise bad_request(f"{where}.content.{k}: search results hold "
                                      "non-empty text blocks only")
            citations = b.get("citations")
            if citations is not None and (not isinstance(citations, dict)
                                          or set(citations) - {"enabled"}):
                raise bad_request(f"{where}.citations: Extra inputs are not permitted")
            enabled.add(bool((citations or {}).get("enabled")))
        if len(enabled) > 1:
            raise bad_request("Citations must be enabled or disabled on all search results "
                              "and documents in a request")
        if True in enabled and ((kwargs.get("output_config") or {}).get("format")
                                or kwargs.get("output_format")):
            raise bad_request("Citations are not supported with structured outputs "
                              "(output_config.format)")
        web = any(t.get("name") == "web_search" for t in kwargs.get("tools") or [])
        if web and False in enabled:
            raise bad_request("When web search is enabled, citations must be enabled on "
                              "all search_result blocks")

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
        system = kwargs.get("system")
        system = system if isinstance(system, str) else " ".join(
            b.get("text", "") for b in system or [])
        words = next((b.get("text", "") for b in _blocks(last) if b.get("type") == "text"),
                     "") if _is_user_turn(last) else ""
        quiz = re.search(r"\bquiz me on ([\w ]+?)\s*[.?!]?$", words, re.I)
        research = self._research(kwargs, msgs, n) if _is_user_turn(last) else None
        usage = {"input_tokens": 10, "output_tokens": 5}
        if research is not None:
            blocks, used = research
            content += blocks
            if used:
                usage["server_tool_use"] = used
        elif not _is_user_turn(last) and called == {"start_study"}:
            content.append({"type": "text", "text": (
                "First question: what does Gauss's law say about the field inside "
                "a charged conducting shell?")})
        elif not _is_user_turn(last) and called == {"note_missed"}:
            content.append({"type": "text", "text": (
                "Not quite: inside the conductor the field is zero, because the "
                "enclosed charge is zero. Next: what's the flux through a closed "
                "surface with no charge inside?")})
        elif quiz and "start_study" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "start_study", "input": {"course": quiz.group(1)}})
            stop = "tool_use"
        elif _is_user_turn(last) and "note_missed" in tools \
                and "Study mode is on" in system and re.match(r"\s*I think", words):
            course = next((b["input"].get("course", "") for m in msgs
                           if m["role"] == "assistant" for b in _blocks(m)
                           if b.get("type") == "tool_use" and b.get("name") == "start_study"),
                          "")
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "note_missed",
                            "input": {"course": course,
                                      "concept": "Gauss's law inside a conductor"}})
            stop = "tool_use"
        elif not _is_user_turn(last) and called == {"list_calendar_events"} \
                and "get rid of" in asked.lower() and "delete_calendar_event" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "delete_calendar_event",
                            "input": {"event_id": "dentist-1"}})
            stop = "tool_use"
        elif not _is_user_turn(last) and called == {"delete_calendar_event"}:
            content.append({"type": "text", "text": "Done, it's off your calendar."})
        elif not _is_user_turn(last) and called == {"remember"}:
            content.append({"type": "text", "text": "Got it, I'll remember that."})
        elif not _is_user_turn(last) and called == {"create_reminder"}:
            when = _tool_json(last).get("when")  # said back as the tool worded it
            content.append({"type": "text", "text": (f"I'll remind you {when}." if when
                                                     else "I couldn't set that.")})
        elif not _is_user_turn(last) and called == {"start_research"}:
            started = _tool_json(last).get("status") == "started"
            content.append({"type": "text", "text": (
                "I'll let you know when your research is ready." if started
                else "I couldn't start that research.")})
        elif _is_user_turn(last) and "start_research" in tools and re.match(
                r"\s*(?:research|do a deep dive on)\b", words, re.I):
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "start_research",
                            "input": {"question": words.strip()}})
            stop = "tool_use"
        elif _is_user_turn(last) and "create_reminder" in tools and re.search(
                r"\bremind me in \d+ minutes? to ", asked, re.I):
            words = next(b["text"] for b in _blocks(last) if b.get("type") == "text")
            minutes, what = re.search(r"remind me in (\d+) minutes? to (.+)", words,
                                      re.I).groups()
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "create_reminder",
                            "input": {"text": what.strip().rstrip("."),
                                      "in_minutes": int(minutes)}})
            stop = "tool_use"
        elif _is_user_turn(last) and "remember" in tools and re.search(
                r"\b(from now on|always)\b", asked, re.I):
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "remember",  # the words, not the turn's context block
                            "input": {"note": next(b["text"] for b in _blocks(last)
                                                   if b.get("type") == "text").strip()}})
            stop = "tool_use"
        elif not _is_user_turn(last) and called == {"search_conversations"} \
                and "read_conversation" in tools:
            found = _tool_json(last).get("conversations") or []
            if found:
                content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                                "name": "read_conversation",
                                "input": {"conversation_id": found[0]["id"],
                                          "query": _topic(asked)}})
                stop = "tool_use"
            else:
                content.append({"type": "text", "text": "I couldn't find that chat."})
        elif not _is_user_turn(last) and called == {"read_conversation"} \
                and _search_results(msgs):
            # search_result blocks: the best message, cited (the API's
            # search_result_location: index among all search results sent)
            words = _topic(asked).lower().split()
            index, result = _search_results(msgs)[-1]
            blocks = result["content"]
            k = max(range(len(blocks)), key=lambda n: (
                n > 0, sum(w in blocks[n]["text"].lower() for w in words)))
            said = blocks[k]["text"].split(": ", 1)[-1]
            date = re.search(r"from (\w+day, \w+ \d+, \d{4})", blocks[0]["text"])
            content.append({"type": "text", "text": "In your chat on "
                            f"{date.group(1) if date else 'that day'}: "})
            content.append({"type": "text", "text": said, "citations": [{
                "type": "search_result_location", "cited_text": blocks[k]["text"],
                "source": result["source"], "title": result["title"],
                "search_result_index": index, "start_block_index": k,
                "end_block_index": k + 1}]})
        elif not _is_user_turn(last) and called == {"read_conversation"}:
            read, words = _tool_json(last), _topic(asked).lower().split()
            matches = [m["said"] for m in read.get("messages") or [] if m.get("match")]
            said = max(matches, default="",  # the one with the most topic words
                       key=lambda t: sum(w in t.lower() for w in words))
            content.append({"type": "text", "text": (
                f"In your chat on {read.get('date', 'that day')}: {said}"
                if said else "I couldn't find that in the chat.")})
        elif not _is_user_turn(last):  # tool results came back
            content.append({"type": "text", "text": "Tomorrow you have two events."})
        elif _is_user_turn(last) and "web_search" in tools \
                and re.search(r"\bsearch the web for\b", words, re.I):
            # one server-side search (the API runs it), then a cited answer
            query = re.search(r"search the web for (.+?)[.?!]?$", words, re.I).group(1)
            content += [
                {"type": "server_tool_use", "id": f"srvtoolu_{n:04d}",
                 "name": "web_search", "input": {"query": query}},
                {"type": "web_search_tool_result", "tool_use_id": f"srvtoolu_{n:04d}",
                 "content": [{"type": "web_search_result", "url": url, "title": title,
                              "encrypted_content": "enc", "page_age": None}
                             for url, title in WEB_RESULTS]},
                {"type": "text", "text": "Here's what I found. "},
                {"type": "text", "text": "The exam is on Friday at 9.", "citations": [
                    {"type": "web_search_result_location", "url": url, "title": title,
                     "encrypted_index": "ei", "cited_text": "Final exam: Friday 9:00."}
                    for url, title in WEB_RESULTS]},
            ]
        elif "what did we decide" in asked.lower() \
                and "search_conversations" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "search_conversations",
                            "input": {"query": _topic(asked)}})
            stop = "tool_use"
        elif "calendar" in asked.lower() and "list_calendar_events" in tools:
            content.append({"type": "tool_use", "id": f"toolu_{n:04d}",
                            "name": "list_calendar_events", "input": {"day": "tomorrow"}})
            stop = "tool_use"
        elif (kwargs.get("output_config") or {}).get("format") is not None:
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
            "stop_reason": stop, "stop_sequence": None, "usage": usage})

    @staticmethod
    def _research(kwargs: dict, msgs: list[dict], n: int):
        """Deep research's three requests (see the module doc), or None:
        (content blocks, usage.server_tool_use or None)."""
        tools = {t.get("name"): t for t in kwargs.get("tools") or []}
        fmt = (kwargs.get("output_config") or {}).get("format") or {}
        words = _text_of(msgs[-1])
        if "outline" in ((fmt.get("schema") or {}).get("properties") or {}):
            plan = {"title": "Physics internships, summer 2027", "outline": [
                {"heading": "Programs", "goal": "Which programs take physics majors"},
                {"heading": "Deadlines", "goal": "When to apply"}]}
            return [{"type": "text", "text": json.dumps(plan)}], None
        step = re.search(r"Research step (\d+)", words)
        if step and "web_search" in tools:
            k = int(step.group(1))
            searches = min(2, tools["web_search"].get("max_uses") or 2)
            fetches = min(1, (tools.get("web_fetch") or {}).get("max_uses") or 0)
            blocks = []
            for i in range(searches):
                url, title = RESEARCH_PAGES[(k + i) % len(RESEARCH_PAGES)]
                blocks += [
                    {"type": "server_tool_use", "id": f"srvtoolu_{n:04d}_{i}",
                     "name": "web_search", "input": {"query": f"step {k} query {i}"}},
                    {"type": "web_search_tool_result", "tool_use_id": f"srvtoolu_{n:04d}_{i}",
                     "content": [{"type": "web_search_result", "url": url, "title": title,
                                  "encrypted_content": "enc", "page_age": None}]}]
            url, title = RESEARCH_PAGES[k % len(RESEARCH_PAGES)]
            blocks.append({"type": "text", "text": f"Step {k}: the programs open in "
                           "January.", "citations": [{
                               "type": "web_search_result_location", "url": url,
                               "title": title, "encrypted_index": "ei",
                               "cited_text": f"Applications for step {k} open in January."}]})
            if fetches:
                page, name = RESEARCH_PAGES[(k + 2) % len(RESEARCH_PAGES)]
                blocks += [
                    {"type": "server_tool_use", "id": f"srvtoolu_{n:04d}_f",
                     "name": "web_fetch", "input": {"url": page}},
                    {"type": "web_fetch_tool_result", "tool_use_id": f"srvtoolu_{n:04d}_f",
                     "content": {"type": "web_fetch_result", "url": page,
                                 "retrieved_at": "2026-10-30T12:00:00Z",
                                 "content": {"type": "document", "title": name,
                                             "citations": {"enabled": True},
                                             "source": {"type": "text",
                                                        "media_type": "text/plain",
                                                        "data": "Deadline: March 1."}}}},
                    {"type": "text", "text": "The deadline is March 1.", "citations": [{
                        "type": "char_location", "document_index": 0,
                        "document_title": name, "start_char_index": 0,
                        "end_char_index": 18, "cited_text": "Deadline: March 1."}]}]
            return blocks, {"web_search_requests": searches, "web_fetch_requests": fetches}
        results = _search_results(msgs)
        if "Write the report" in words and results:
            blocks = [{"type": "text", "text": "# Physics internships\n\nSeveral programs "
                       "take physics majors; most deadlines are in early spring.\n\n"
                       "## Programs\n\n"}]
            for index, r in results[:3]:
                blocks.append({"type": "text", "text": f"- {r['title']} is worth a look.",
                               "citations": [{
                                   "type": "search_result_location", "source": r["source"],
                                   "title": r["title"],
                                   "cited_text": r["content"][0]["text"][:100],
                                   "search_result_index": index, "start_block_index": 0,
                                   "end_block_index": 1}]})
                blocks.append({"type": "text", "text": "\n"})
            return blocks, None
        return None

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
