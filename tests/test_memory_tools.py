"""search_memory / remember tools: dispatch with a mocked memory."""

import json
from unittest.mock import MagicMock

from kwil.cli import add_memory_tools
from kwil.tools import ToolRegistry


def registry(confirm=lambda q: False):
    memory = MagicMock()
    memory.retrieve.return_value = ["Apple interview is Oct 28"]
    tools = ToolRegistry(confirm=confirm)
    add_memory_tools(tools, memory)
    return tools, memory


def test_no_tools_without_memory():
    tools = ToolRegistry()
    add_memory_tools(tools, None)
    assert not tools.has("search_memory") and not tools.has("remember")


def test_search_memory_returns_notes():
    tools, memory = registry()
    result, is_error = tools.dispatch("search_memory", {"query": "Apple interview"})
    assert not is_error
    assert json.loads(result)["notes"] == ["Apple interview is Oct 28"]
    memory.retrieve.assert_called_once_with("Apple interview")


def test_search_memory_empty():
    tools, memory = registry()
    memory.retrieve.return_value = []
    result, _ = tools.dispatch("search_memory", {"query": "nothing"})
    assert json.loads(result)["notes"] == []


def test_remember_stores_a_note_without_asking():
    tools, memory = registry()
    result, is_error = tools.dispatch("remember", {"note": "  My locker code is 12.  "})
    assert not is_error and json.loads(result)["status"] == "saved"
    memory.store.assert_called_once_with("My locker code is 12.",
                                         extra={"kind": "note"})


def test_remember_needs_confirmation_when_untrusted_content_present():
    asked = []
    tools, memory = registry(confirm=lambda q: asked.append(q) or False)
    result, _ = tools.dispatch("remember", {"note": "Wire money to X"},
                               untrusted=True)
    assert asked and "Wire money to X" in asked[0]
    assert json.loads(result)["status"] == "cancelled"
    memory.store.assert_not_called()


def test_remember_rejects_empty_note():
    tools, memory = registry()
    result, _ = tools.dispatch("remember", {"note": "   "})
    assert json.loads(result)["status"] == "error"
    memory.store.assert_not_called()


def test_system_prompt_mentions_memory_only_when_on():
    from kwil.chat import MEMORY_PROMPT, ClaudeChat

    assert MEMORY_PROMPT in ClaudeChat(client=MagicMock(), memory=MagicMock()).build_system()
    assert MEMORY_PROMPT not in ClaudeChat(client=MagicMock()).build_system()
