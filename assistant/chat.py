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


def default_model() -> str:
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL


class ClaudeChat:
    """Keeps conversation history and sends it to the Messages API."""

    def __init__(self, client=None, model: str | None = None,
                 max_tokens: int = 500, system_prompt: str = SYSTEM_PROMPT,
                 max_history: int = 40):
        self._client = client
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
        return (f"{self.system_prompt}\n\nCurrent local date and time: "
                f"{now:%A, %B %d, %Y, %I:%M %p} ({now:%Z}, UTC{now:%z}).")

    def ask(self, user_text: str) -> str:
        """Send one user turn and return Claude's text reply."""
        self.history.append({"role": "user", "content": user_text})
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.build_system(),
                messages=self.history,
            )
        except Exception:
            self.history.pop()  # keep history valid so the user can retry
            raise
        reply = "".join(b.text for b in response.content if b.type == "text")
        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()
        return reply.strip()

    def _trim_history(self) -> None:
        if len(self.history) <= self.max_history:
            return
        trimmed = self.history[-self.max_history:]
        # The API requires the first message to be a plain user turn.
        while trimmed and not (trimmed[0]["role"] == "user"
                               and isinstance(trimmed[0]["content"], str)):
            trimmed.pop(0)
        self.history = trimmed
