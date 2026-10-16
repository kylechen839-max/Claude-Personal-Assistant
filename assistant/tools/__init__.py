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

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


class ToolRegistry:
    def __init__(self, confirm: Callable[[str], bool] | None = None):
        self.tools: dict[str, Tool] = {}
        # Default to refusing: never run a destructive tool unconfirmed.
        self.confirm = confirm or (lambda prompt: False)

    def register(self, *tools: Tool) -> None:
        for tool in tools:
            self.tools[tool.name] = tool

    def __bool__(self) -> bool:
        return bool(self.tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def dispatch(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Run a tool. Returns (result text for Claude, is_error)."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True
        try:
            if tool.confirm_prompt is not None:
                question = tool.confirm_prompt(tool_input)
                if not self.confirm(question):
                    log.info("User declined %s", name)
                    return json.dumps({"status": "cancelled",
                                       "message": "The user said no, so nothing "
                                                  "was changed."}), False
            result = tool.handler(**tool_input)
            log.info("Tool %s(%s) ok", name, tool_input)
            return (result if isinstance(result, str)
                    else json.dumps(result, default=str)), False
        except Exception as e:  # report the error to Claude, don't crash
            log.exception("Tool %s failed", name)
            return f"{type(e).__name__}: {e}", True
