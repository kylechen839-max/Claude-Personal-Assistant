"""Tools Claude can call, plus the registry that dispatches them."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]
    # Given the tool input, return a yes/no question to ask before running.
    confirm_prompt: Callable[[dict], str] | None = None
    # Same, but asked only when untrusted web content is in the conversation
    # (a fetched page could be trying to steer Claude into this action).
    confirm_if_untrusted: Callable[[dict], str] | None = None
    # The result holds text written by someone else (an email): once it is
    # in the conversation, confirm_if_untrusted questions are asked too.
    untrusted_output: bool = False
    # Inputs and results stay out of the log (email text, personal notes).
    private: bool = False

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


class ToolRegistry:
    def __init__(self, confirm: Callable[[str], bool] | None = None):
        self.tools: dict[str, Tool] = {}
        # Anthropic-run tools (web search/fetch): sent with the request,
        # executed by the API, never dispatched here.
        self.server_tools: list[dict] = []
        # Default to refusing: never run a destructive tool unconfirmed.
        self.confirm = confirm or (lambda prompt: False)

    def register(self, *tools: Tool) -> None:
        for tool in tools:
            self.tools[tool.name] = tool

    def add_server_tools(self, *definitions: dict) -> None:
        self.server_tools.extend(definitions)

    def __bool__(self) -> bool:
        return bool(self.tools or self.server_tools)

    def has(self, name: str) -> bool:
        return name in self.tools or any(t["name"] == name
                                         for t in self.server_tools)

    def untrusted_names(self) -> set[str]:
        """Tools whose results count as untrusted content (see Tool)."""
        return {t.name for t in self.tools.values() if t.untrusted_output}

    def is_private(self, name: str) -> bool:
        tool = self.tools.get(name)
        return bool(tool and tool.private)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()] + self.server_tools

    def dispatch(self, name: str, tool_input: dict,
                 untrusted: bool = False) -> tuple[str, bool]:
        """Run a tool. Returns (result text for Claude, is_error).

        `untrusted`: web or email content the user didn't write is in the
        context, so actions that change things need a spoken yes too."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True
        try:
            ask = tool.confirm_prompt or (tool.confirm_if_untrusted
                                          if untrusted else None)
            if ask is not None:
                question = ask(tool_input)
                if not self.confirm(question):
                    log.info("User declined %s", name)
                    return json.dumps({"status": "cancelled",
                                       "message": "The user said no, so nothing "
                                                  "was changed."}), False
            result = tool.handler(**tool_input)
            log.info("Tool %s(%s) ok", name,
                     "<private>" if tool.private else tool_input)
            return (result if isinstance(result, str)
                    else json.dumps(result, default=str)), False
        except Exception as e:  # report the error to Claude, don't crash
            log.exception("Tool %s failed", name)
            return f"{type(e).__name__}: {e}", True
