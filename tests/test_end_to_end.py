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


# ---------- the definition-of-done phrase, through `python -m assistant` ----------
import json  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from assistant import audio, cli  # noqa: E402
from assistant.stt import Transcriber, rough_match  # noqa: E402
from tests.test_wakeword import HERE, HEY_CLAUDE_MODEL, tts_clip  # noqa: E402

TZ = "America/New_York"
PHRASE = "what's on my calendar tomorrow"
# SAPI recording: the trained model fires on these, not on eSpeak's voice
WAKE_CLIP = HERE / "fixtures" / "hey_claude_sapi_zira_180.wav"


@pytest.fixture(scope="module")
def dod_wav(tmp_path_factory):
    """[1 s silence]["Hey Claude" (SAPI)][0.4 s][TTS phrase][2 s silence]."""
    tmp = tmp_path_factory.mktemp("dod")
    path = tmp / "hey_claude_calendar_tomorrow.wav"
    audio.write_wav(path, np.concatenate([
        audio.silence(1.0), audio.load_audio(WAKE_CLIP), audio.silence(0.4),
        tts_clip(tmp, PHRASE, "phrase"), audio.silence(2.0)]))
    return path


def _dod_config(tmp_path):
    """The shipped settings, except: no Pinecone, logs in tmp, a fake
    credentials.json so the calendar tools are switched on, and a fixed
    time zone so "tomorrow" is unambiguous."""
    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[assistant]\ntimezone = "{TZ}"\n'
        f'[wake]\nkeyword_path = {json.dumps(str(HEY_CLAUDE_MODEL))}\n'
        f'[calendar]\ncredentials = {json.dumps(str(creds))}\n'
        f'token = {json.dumps(str(tmp_path / "token.json"))}\n'
        f'[memory]\nenabled = false\n'
        f'[logging]\nfile = {json.dumps(str(tmp_path / "assistant.log"))}\n')
    return cfg


@pytest.mark.slow
@pytest.mark.parametrize("stt", ["whisper", "mocked-stt"])
def test_definition_of_done_phrase(stt, dod_wav, tmp_path, monkeypatch, capsys,
                                   fake_client, restore_root_logging):
    """'Hey Claude, what's on my calendar tomorrow?' as a .wav, through
    `python -m assistant --mode wake --audio-file`: real openWakeWord with the
    trained model, real VAD, real Whisper (the "whisper" case; "mocked-stt"
    replaces only Whisper with the known transcript), the real tool loop and
    calendar code. Only Claude and the Google API are mocked."""
    if stt == "whisper":
        try:
            Transcriber().model
        except Exception as e:  # huggingface.co blocked / offline
            pytest.skip(f"Whisper base.en can't be downloaded here "
                        f"({type(e).__name__}); the mocked-stt case covers the "
                        f"rest of the pipeline")
    else:
        heard = []

        class KnownTranscript:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, recording):
                heard.append(len(recording) / audio.SAMPLE_RATE)
                return "What's on my calendar tomorrow?"

        monkeypatch.setattr(cli, "Transcriber", KnownTranscript)

    tomorrow = datetime.now(ZoneInfo(TZ)).date() + timedelta(days=1)
    two_pm = datetime.combine(tomorrow, datetime.min.time(), ZoneInfo(TZ)) \
        + timedelta(hours=14)
    event = {"id": "ev9", "summary": "Physics lab",
             "start": {"dateTime": two_pm.isoformat()},
             "end": {"dateTime": (two_pm + timedelta(minutes=90)).isoformat()}}
    service, events = make_service(items=[event])
    import googleapiclient.discovery

    from assistant.tools import calendar

    monkeypatch.setattr(calendar, "get_credentials", lambda *a, **k: None)
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: service)

    def claude(**kwargs):
        """Asks for tomorrow's events, then reads the tool result back."""
        fake_client.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        last = kwargs["messages"][-1]["content"]
        if isinstance(last, str):
            return make_response(tool_use_block("list_calendar_events",
                                                {"day": "tomorrow"}),
                                 stop_reason="tool_use")
        found = json.loads(last[0]["content"])["events"]
        return make_response(text_block(
            "Tomorrow you have " + ", ".join(f"{e['title']} at {e['start']}"
                                             for e in found) + "."))

    fake_client.messages.create.side_effect = claude
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda **kw: fake_client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)

    code = cli.main(["--mode", "wake", "--audio-file", str(dod_wav), "--tts",
                     "print", "--config", str(_dod_config(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0, out

    # the real wake word fired once and the command reached Claude
    first_user_turns = [c["messages"][0]["content"] for c in fake_client.calls
                        if len(c["messages"]) == 1]
    assert len(first_user_turns) == 1, out
    assert rough_match(PHRASE, first_user_turns[0]), first_user_turns
    if stt == "mocked-stt":
        assert len(heard) == 1 and heard[0] > 1.0  # VAD captured the phrase
    # the calendar tool ran for tomorrow, midnight to midnight, in Kyle's zone
    kw = events.list.call_args.kwargs
    start = datetime.combine(tomorrow, datetime.min.time(), ZoneInfo(TZ))
    assert kw["timeMin"] == start.isoformat()
    assert kw["timeMax"] == (start + timedelta(days=1)).isoformat()
    # and the answer read out loud names the event
    spoken = [line for line in out.splitlines() if line.startswith("[speaking]")]
    assert spoken == ["[speaking] Tomorrow you have Physics lab at 2 PM."], out
