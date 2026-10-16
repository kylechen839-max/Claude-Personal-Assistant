"""'Hey Claude, what's on my calendar tomorrow?' through every real stage,
with only the external services (Whisper model, Claude, Google) mocked."""

from types import SimpleNamespace

from assistant.chat import ClaudeChat
from assistant.cli import run_wake
from assistant.tools import ToolRegistry
from assistant.tools.calendar import GoogleCalendar
from assistant.tts import PrintTTS
from assistant.wakeword import FileSource, WakeListener
from tests.conftest import make_response, text_block, tool_use_block
from tests.test_tools import DENTIST, make_service
from tests.test_wakeword import COMMAND, ScriptedDetector, session_file  # noqa: F401


def test_hey_claude_whats_on_my_calendar_tomorrow(session_file, fake_client):
    path, fire_at, _ = session_file
    source = FileSource(path)
    listener = WakeListener(ScriptedDetector(source, fire_at[:1]))

    service, events = make_service(items=[DENTIST])
    tools = ToolRegistry()
    tools.register(*GoogleCalendar(service=service,
                                   timezone="America/New_York").tools())
    fake_client.queue = [
        make_response(tool_use_block("list_calendar_events", {"day": "tomorrow"}),
                      stop_reason="tool_use"),
        make_response(text_block("Tomorrow you have the dentist at 3 PM.")),
    ]
    chat = ClaudeChat(client=fake_client, tools=tools)
    heard = []

    def transcribe(utterance):
        heard.append(len(utterance))
        return "What's on my calendar tomorrow?"

    said = []
    run_wake(chat, PrintTTS(said.append), SimpleNamespace(transcribe=transcribe),
             listener, source, output_fn=lambda *_: None)

    assert len(heard) == 1 and heard[0] > 16000  # >1 s of command audio captured
    assert events.list.called  # Claude's tool call reached Google Calendar
    tool_result = fake_client.calls[1]["messages"][2]["content"][0]["content"]
    assert "Dentist" in tool_result
    assert said == ["[speaking] Tomorrow you have the dentist at 3 PM."]
