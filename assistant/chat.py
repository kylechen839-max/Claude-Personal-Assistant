"""Conversation with Claude: history, system prompt, and the request loop."""

from __future__ import annotations

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = (
    "You are Kyle's personal voice assistant. Your replies are read aloud, so "
    "keep them short and conversational (1-3 sentences). Use plain spoken "
    "English: no markdown, bullet points, code blocks, emoji, or URLs. Say "
    "numbers, dates, and times the way a person would say them."
)

TOOLS_PROMPT = (
    "You can read and change Kyle's Google Calendar and his Notion school "
    "assignments with tools. Use them whenever a question is about his "
    "schedule or assignments instead of guessing. When listing things, "
    "summarize the few that matter most rather than reading everything."
)


def default_model() -> str:
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL


class ClaudeChat:
    """Keeps conversation history and sends it to the Messages API."""

    def __init__(self, client=None, model: str | None = None,
                 max_tokens: int = 500, system_prompt: str = SYSTEM_PROMPT,
                 max_history: int = 40, tools=None, max_tool_rounds: int = 6):
        self._client = client
        self.tools = tools  # a ToolRegistry, or None
        self.max_tool_rounds = max_tool_rounds
        self.model = model or default_model()
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.max_history = max_history
        self.history: list[dict] = []

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    def reset(self) -> None:
        self.history = []

    def build_system(self) -> str:
        now = datetime.now().astimezone()
        parts = [self.system_prompt]
        if self.tools:
            parts.append(TOOLS_PROMPT)
        parts.append(f"Current local date and time: "
                     f"{now:%A, %B %d, %Y, %I:%M %p} ({now:%Z}, UTC{now:%z}).")
        return "\n\n".join(parts)

    def ask(self, user_text: str) -> str:
        """Send one user turn, run any tool calls, and return the text reply."""
        start = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        try:
            reply = self._run()
        except Exception:
            del self.history[start:]  # keep history valid so the user can retry
            raise
        self._trim_history()
        return reply

    def _run(self) -> str:
        system = self.build_system()
        for _ in range(self.max_tool_rounds + 1):
            kwargs = dict(model=self.model, max_tokens=self.max_tokens,
                          system=system, messages=self.history)
            if self.tools:
                kwargs["tools"] = self.tools.schemas()
            response = self.client.messages.create(**kwargs)
            blocks = [_block_dict(b) for b in response.content
                      if not (b.type == "text" and not b.text.strip())]
            self.history.append({"role": "assistant", "content": blocks or
                                 [{"type": "text", "text": "(no reply)"}]})
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls or not self.tools:
                text = "".join(b.text for b in response.content if b.type == "text")
                return text.strip()
            results = []
            for call in calls:
                log.info("Claude called %s %s", call.name, call.input)
                content, is_error = self.tools.dispatch(call.name, dict(call.input))
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": content, "is_error": is_error})
            self.history.append({"role": "user", "content": results})
        return "Sorry, that took too many steps. Could you ask a simpler way?"

    def _trim_history(self) -> None:
        if len(self.history) <= self.max_history:
            return
        trimmed = self.history[-self.max_history:]
        # The API requires the first message to be a plain user turn.
        while trimmed and not (trimmed[0]["role"] == "user"
                               and isinstance(trimmed[0]["content"], str)):
            trimmed.pop(0)
        self.history = trimmed


def _block_dict(block) -> dict:
    """Convert a response content block into a plain dict for history."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name,
                "input": dict(block.input)}
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(vars(block))
