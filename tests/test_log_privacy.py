"""INFO logs carry no conversation text: not what was heard, not the reply,
not tool inputs (event titles, queries, URLs). PRIVACY.md promises this."""

import logging
from unittest.mock import MagicMock

from kwil import cli
from kwil.chat import ClaudeChat
from kwil.tools import Tool, ToolRegistry
from tests.conftest import make_response, text_block, tool_use_block

SECRET_WORDS = "dentist appointment with Dr Zhivago"
TOOL_TEXT = "Zhivago follow-up visit"


def info_text(caplog):
    return "\n".join(r.getMessage() for r in caplog.records
                     if r.levelno >= logging.INFO)


def test_voice_cli_turn_logs_lengths_only(fake_client, caplog):
    caplog.set_level(logging.DEBUG)
    fake_client.queue.append(make_response(text_block(f"Booked your {SECRET_WORDS}.")))
    chat = ClaudeChat(client=fake_client)
    transcriber = MagicMock()
    transcriber.transcribe.return_value = f"book my {SECRET_WORDS}"
    tts = MagicMock()
    assert cli.handle_command(chat, tts, transcriber, b"audio",
                              output_fn=lambda *_: None)
    assert "Zhivago" not in info_text(caplog)
    assert any("Zhivago" in r.getMessage() for r in caplog.records
               if r.levelno == logging.DEBUG)  # still there for debugging


def test_unexpected_error_does_not_log_the_request(caplog):
    caplog.set_level(logging.DEBUG)
    chat = MagicMock()
    chat.ask.side_effect = RuntimeError("boom")
    cli.get_reply(chat, f"cancel my {SECRET_WORDS}", output_fn=lambda *_: None)
    assert "Zhivago" not in info_text(caplog)


def test_tool_inputs_stay_out_of_info_logs(fake_client, caplog):
    caplog.set_level(logging.DEBUG)
    tools = ToolRegistry()
    tools.register(Tool("create_event", "Create an event.",
                        {"type": "object", "properties": {"title": {"type": "string"}}},
                        lambda title: {"status": "created"}))
    fake_client.queue += [
        make_response(tool_use_block("create_event", {"title": TOOL_TEXT}),
                      stop_reason="tool_use"),
        make_response(text_block("Done.")),
    ]
    ClaudeChat(client=fake_client, tools=tools).ask("add it")
    logged = info_text(caplog)
    assert "create_event" in logged and "title" in logged
    assert "Zhivago" not in logged
