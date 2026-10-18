"""The preflight (python -m assistant.preflight) and the web smoke check:
result parsing, verdicts, and that no .env value ever reaches the output."""

import re
import subprocess
from pathlib import Path

import pytest

from assistant import preflight, smoke
from assistant.preflight import (FAIL, PASS, SKIP, WARN, Context, Redactor, Result,
                                 judge_level, parse_junit, parse_text_replies,
                                 parse_wake_output, run_preflight)

ROOT = Path(__file__).resolve().parent.parent
SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-FAKEfakeFAKEfake0123456789",
    "PINECONE_API_KEY": "pcsk_5xFake_0123456789abcdefFAKE",
    "NOTION_TOKEN": "ntn_FAKE0123456789abcdefghijklmnop",
    "WEB_TOKEN": "w3b-t0ken-FAKE-0123456789abcdef0123",
    "ODD_VALUE": "plain words with spaces",  # not key-shaped: still masked
}


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    for k in SECRETS:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("# fake .env for the preflight tests\n" + "".join(
        f'{k}="{v}"\n' for k, v in SECRETS.items()), encoding="utf-8")
    return env


def leaks(text: str) -> list[str]:
    return [v for v in SECRETS.values() if v in text]


class FakeRun:
    """Stands in for run_command: answers by the command line, and echoes
    the secrets (as a careless tool might) so redaction is exercised."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd, cwd, env, input=None, timeout=0):
        self.calls.append((cmd, env, input))
        line = " ".join(cmd)
        for pattern, (code, out) in self.answers.items():
            if re.search(pattern, line):
                return code, out(env) if callable(out) else out
        return 0, ""


def ctx_for(env_file, run, tmp_path, **kw):
    return Context(root=tmp_path, python="python", env={"PATH": "/bin"},
                   env_file=env_file, run=run, **kw)


# ---------- secret redaction ----------

def test_redactor_masks_values_and_key_shapes():
    r = Redactor(SECRETS.values())
    text = " | ".join(SECRETS.values()) + " sk-ant-other-key-xyz"
    out = r(text)
    assert leaks(out) == [] and "sk-ant-other" not in out
    assert out.count("[REDACTED]") == len(SECRETS) + 1


def test_redactor_ignores_tiny_values():
    assert Redactor(["", "ab", "on"])("on a tab") == "on a tab"


def echo_secrets(env):
    """Output that contains every secret value the step was given."""
    return "ok " + " ".join(env.get(k, "") for k in SECRETS) + "\nAll required checks passed."


def test_no_env_value_in_console_or_report(fake_env, tmp_path, capsys):
    run = FakeRun({
        r"git rev-parse --abbrev-ref": (0, "master\n"),
        r"git status": (0, ""),
        r"git rev-parse --short": (0, "abc1234\n"),
        r"--check": (0, echo_secrets),
        r"-c ": (1, echo_secrets),  # the TTS renders fail and print the env
    })
    ctx = ctx_for(fake_env, run, tmp_path)
    lines = []
    code, report = run_preflight(ctx, report_dir=tmp_path / "logs",
                                 output_fn=lines.append,
                                 steps=("git", "env", "check", "tts-pyttsx3"))
    console = "\n".join(lines)
    saved = report.read_text(encoding="utf-8")
    assert leaks(console) == [] and leaks(saved) == []
    assert "[REDACTED]" in saved  # the echoed values were there, masked
    # the commands did get the real values (.env is loaded for them)
    assert run.calls[-1][1]["ANTHROPIC_API_KEY"] == SECRETS["ANTHROPIC_API_KEY"]
    # names are shown, values never
    assert "ANTHROPIC_API_KEY" in console and "(values hidden)" in console
    assert code == 1  # the TTS step failed
    assert re.fullmatch(r"preflight-\d{8}-\d{6}\.txt", report.name)


def test_redacts_before_env_step_too(fake_env, tmp_path, monkeypatch, capsys):
    """main() loads the masks first, so even a step that runs before 'env'
    (or with --skip env) can't print a value."""
    def git(cmd, cwd, env, input=None, timeout=0):
        return 0, SECRETS["WEB_TOKEN"] + "\n"

    monkeypatch.setattr(preflight, "run_command", git)
    preflight.main(["--env-file", str(fake_env), "--report-dir",
                    str(tmp_path / "logs"), "--skip", ",".join(
                        s for s in preflight.STEPS if s != "git")])
    out = capsys.readouterr().out
    report = next((tmp_path / "logs").glob("preflight-*.txt")).read_text("utf-8")
    assert leaks(out) == [] and leaks(report) == []


def test_smoke_cli_never_prints_the_token(monkeypatch, capsys, fake_env):
    token = SECRETS["WEB_TOKEN"]
    monkeypatch.setenv("WEB_TOKEN", token)
    monkeypatch.setattr(smoke, "run_smoke", lambda url, tok, wav, allow_http=False: [
        smoke.Outcome("login", "FAIL", f"server said: bad token {tok}")])
    assert smoke.main(["http://127.0.0.1:1"]) == 1
    out = capsys.readouterr().out
    assert token not in out and "[REDACTED]" in out


# ---------- steps ----------

def test_env_step_missing_file(tmp_path):
    r = preflight.step_env(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path))
    assert r.status == FAIL and ".env not found" in r.detail


def test_env_step_loads_into_children_not_the_file(fake_env, tmp_path):
    ctx = ctx_for(fake_env, FakeRun({}), tmp_path)
    r = preflight.step_env(ctx)
    assert r.status == PASS
    assert ctx.env["NOTION_TOKEN"] == SECRETS["NOTION_TOKEN"]
    assert leaks(r.detail) == []


@pytest.mark.parametrize("branch,status,code,want", [
    ("master", "", PASS, "clean"),
    ("claude/x", "", FAIL, "not master"),
    ("master", " M README.md\n?? new.txt\n", FAIL, "2 uncommitted"),
    ("master", " M config.toml\n", PASS, "local settings in config.toml"),
    ("master", "?? config.toml\n", FAIL, "1 uncommitted"),
])
def test_git_step(tmp_path, branch, status, code, want):
    run = FakeRun({r"--abbrev-ref": (0, branch + "\n"), r"status": (0, status),
                   r"--short": (0, "abc1234\n")})
    r = preflight.step_git(ctx_for(tmp_path / ".env", run, tmp_path))
    assert r.status == code and want in r.detail


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="2" tests="6">
<testcase classname="tests.test_tools" name="test_a"/>
<testcase classname="tests.test_tools" name="test_b"><failure message="boom"/></testcase>
<testcase classname="tests.test_tools" name="test_live_notion_list_assignments">
  <skipped type="pytest.skip" message="NOTION_TOKEN not set"/></testcase>
<testcase classname="tests.test_memory" name="test_live_pinecone">
  <skipped type="pytest.skip" message="PINECONE_API_KEY not set"/></testcase>
<testcase classname="tests.test_x" name="test_c"/><testcase classname="tests.test_x" name="test_d"/>
</testsuite></testsuites>"""


def test_parse_junit(tmp_path):
    path = tmp_path / "j.xml"
    path.write_text(JUNIT)
    r = parse_junit(path)
    assert (r["tests"], r["passed"], r["failures"], r["skipped"]) == (6, 3, 1, 2)
    assert r["failed"] == ["tests.test_tools::test_b"]
    assert ("tests.test_memory::test_live_pinecone", "PINECONE_API_KEY not set") in r["skips"]
    assert preflight.summarize_junit(r) == "3 passed, 1 failed, 2 skipped"


def junit_run(xml, code=0):
    def run(cmd, cwd, env, input=None, timeout=0):
        junit = next(a for a in cmd if a.startswith("--junitxml="))
        Path(junit.split("=", 1)[1]).write_text(xml)
        return code, "pytest output"
    return run


def test_live_step_fails_when_live_tests_skip(tmp_path):
    xml = JUNIT.replace('failures="1"', 'failures="0"').replace(
        '<failure message="boom"/>', "")
    r = preflight.step_pytest_live(ctx_for(tmp_path / ".env", junit_run(xml), tmp_path))
    assert r.status == FAIL
    assert "NOTION_TOKEN not set" in r.detail and "PINECONE_API_KEY not set" in r.detail


def test_live_step_passes_when_all_ran(tmp_path):
    xml = ('<testsuites><testsuite errors="0" failures="0" skipped="0" tests="3">'
           + '<testcase classname="t" name="a"/>' * 3 + "</testsuite></testsuites>")
    r = preflight.step_pytest_live(ctx_for(tmp_path / ".env", junit_run(xml), tmp_path))
    assert r.status == PASS and r.detail.startswith("3 passed")


def test_pytest_step_reports_failures(tmp_path):
    r = preflight.step_pytest(ctx_for(tmp_path / ".env", junit_run(JUNIT, 1), tmp_path))
    assert r.status == FAIL and "tests.test_tools::test_b" in r.detail


WAKE_OUT = """Listening for 'hey claude'... (Ctrl+C to quit)
You: Hey Claude, what's on my calendar tomorrow?
Claude: Tomorrow you have Physics lab at 2 PM.
[speaking] Tomorrow you have Physics lab at 2 PM.
(Turn timings from wake: end of speech +1.20 s, transcript +0.90 s, Claude reply +1.50 s, first audio +0.00 s; wake to first audio 3.60 s (end of speech to first audio 2.40 s))
Bye!
"""


def test_parse_wake_output():
    w = parse_wake_output(WAKE_OUT)
    assert w["woke"] and w["heard"] == ["Hey Claude, what's on my calendar tomorrow?"]
    assert w["replies"] == ["Tomorrow you have Physics lab at 2 PM."]
    assert w["timings"][0].startswith("Turn timings from wake: end of speech")
    assert not parse_wake_output("Listening for 'hey claude'...\nBye!\n")["woke"]


def wake_ctx(tmp_path, out, code=0):
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"--mode wake": (code, out)}), tmp_path)
    ctx.env["ANTHROPIC_API_KEY"] = "x" * 10
    return ctx


def test_wake_step_pass_and_command(tmp_path):
    ctx = wake_ctx(tmp_path, WAKE_OUT)
    r = preflight.step_wake_e2e(ctx)
    assert r.status == PASS and "wake to first audio" in r.detail
    cmd = ctx.run.calls[0][0]
    assert cmd[cmd.index("--audio-file") + 1] == str(preflight.FIXTURE)
    assert "--timings" in cmd and cmd[cmd.index("--mode") + 1] == "wake"


@pytest.mark.parametrize("out,want", [
    ("Listening...\nBye!\n", "no wake"),
    (WAKE_OUT.replace("what's on my calendar tomorrow", "what's the weather"),
     "heard"),
    (WAKE_OUT.replace("Tomorrow you have Physics lab at 2 PM.",
                      "Sorry, I can't reach the internet right now."), "error reply"),
    ("You: what's on my calendar tomorrow\n(Something went wrong; still listening)\n",
     "raised an error"),
])
def test_wake_step_failures(tmp_path, out, want):
    r = preflight.step_wake_e2e(wake_ctx(tmp_path, out))
    assert r.status == FAIL and want in r.detail


def test_wake_step_skips_without_key(tmp_path):
    r = preflight.step_wake_e2e(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path))
    assert r.status == SKIP


def test_wake_fixture_is_committed():
    from assistant import audio

    clip = audio.load_audio(preflight.FIXTURE)
    assert 4 < len(clip) / audio.SAMPLE_RATE < 10


TEXT_OUT = ("Claude assistant (text mode). Type 'quit' to exit.\n\n"
            "You: Claude: Tomorrow you have Physics lab at 2 PM.\n\n"
            "You: Claude: Your essay is due Thursday.\n\nYou: Bye!\n")


def text_ctx(tmp_path, out, log_lines):
    (tmp_path / "config.toml").write_text('[logging]\nfile = "logs/a.log"\n')
    log = tmp_path / "logs" / "a.log"
    log.parent.mkdir()
    log.write_text("old line: Claude called list_assignments {}\n")

    def write_log(env):
        with open(log, "a") as f:
            f.writelines(line + "\n" for line in log_lines)
        return out
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"--mode text": (0, write_log)}), tmp_path)
    ctx.env["ANTHROPIC_API_KEY"] = "x" * 10
    return ctx


def test_parse_text_replies():
    assert parse_text_replies(TEXT_OUT) == [
        "Tomorrow you have Physics lab at 2 PM.", "Your essay is due Thursday."]


def test_text_step_pass(tmp_path):
    ctx = text_ctx(tmp_path, TEXT_OUT, [
        "INFO chat: Claude called list_calendar_events {'day': 'tomorrow'}",
        "INFO chat: Claude called list_assignments {'days_ahead': 7}"])
    r = preflight.step_text_tools(ctx)
    assert r.status == PASS, r.detail
    assert ctx.run.calls[0][2].splitlines() == [*preflight.TEXT_QUESTIONS, "quit"]


def test_text_step_needs_both_tools_called_this_run(tmp_path):
    ctx = text_ctx(tmp_path, TEXT_OUT, [
        "INFO chat: Claude called list_calendar_events {'day': 'tomorrow'}"])
    r = preflight.step_text_tools(ctx)  # list_assignments only in the old log
    assert r.status == FAIL and "list_assignments" in r.detail


def test_text_step_tools_off(tmp_path):
    out = "(Notion tools off: NOTION_TOKEN not set)\n" + TEXT_OUT
    r = preflight.step_text_tools(text_ctx(tmp_path, out, []))
    assert r.status == FAIL and "Notion tools off" in r.detail


@pytest.mark.parametrize("peak,status,why", [
    (-12.0, PASS, ""), (-60.0, FAIL, "too quiet"), (0.0, FAIL, "clipping")])
def test_judge_level(peak, status, why):
    got, detail = judge_level(peak, peak - 10)
    assert got == status and why in detail


def test_hardware_steps_skip_without_flag(tmp_path):
    ctx = ctx_for(tmp_path / ".env", FakeRun({}), tmp_path)
    assert preflight.step_audio_devices(ctx).status == SKIP
    assert preflight.step_mic_level(ctx).status == SKIP


def test_audio_devices_step(tmp_path):
    out = "DEVICE 0 in=1 out=0 USB Mic\nDEVICE 1 in=0 out=2 Speaker\nDEFAULT [0, 1]\n"
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"-c": (0, out)}), tmp_path, hardware=True)
    r = preflight.step_audio_devices(ctx)
    assert r.status == PASS and "[0] USB Mic" in r.detail and "[1] Speaker" in r.detail


@pytest.mark.parametrize("out,status", [
    ("ENGINE=Pyttsx3TTS FRAMES=48000 RATE=22050\n", PASS),
    ("ENGINE=Pyttsx3TTS FRAMES=0 RATE=22050\n", FAIL),
    ("Traceback ...", FAIL)])
def test_tts_step(tmp_path, out, status):
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"-c": (0, out)}), tmp_path)
    assert preflight.step_tts_pyttsx3(ctx).status == status


def test_piper_step_fallback_is_a_fail(tmp_path):
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "en_US-lessac-medium.onnx").write_bytes(b"x")
    ctx = ctx_for(tmp_path / ".env", FakeRun(
        {r"-c": (0, "ENGINE=Pyttsx3TTS FRAMES=100 RATE=22050")}), tmp_path)
    r = preflight.step_tts_piper(ctx)
    assert r.status == FAIL and "fell back" in r.detail


def test_piper_step_missing_voice(tmp_path):
    r = preflight.step_tts_piper(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path))
    assert r.status == FAIL and "--download-voice" in r.detail


@pytest.mark.parametrize("help_text,mode,want", [
    ("--mode {text,speak,voice,wake}", "web", False),
    ("--mode {text,speak,voice,wake,web}", "web", True)])
def test_mode_detection(tmp_path, help_text, mode, want):
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"--help": (0, help_text)}), tmp_path)
    assert ctx.has_mode(mode) is want


def test_web_step_skips_without_web_mode(tmp_path):
    ctx = ctx_for(tmp_path / ".env", FakeRun({r"--help": (0, "--mode {text,wake}")}),
                  tmp_path)
    assert preflight.step_web(ctx)[0].status == SKIP


def test_tts_snippet_renders_for_real(tmp_path):
    """The snippet the TTS steps run, with the real pyttsx3 (eSpeak/SAPI)."""
    path = tmp_path / "out.wav"
    p = subprocess.run([preflight.sys.executable, "-c", preflight.TTS_SNIPPET,
                        "pyttsx3", str(path)], cwd=ROOT, capture_output=True,
                       text=True, timeout=120)
    assert re.search(r"FRAMES=[1-9]\d*", p.stdout), p.stdout + p.stderr


def test_a_crashing_step_is_a_fail_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setitem(preflight.STEP_FUNCS, "git", lambda ctx: 1 / 0)
    code, _ = run_preflight(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path),
                            output_fn=lambda s: None, steps=("git",))
    assert code == 1


def test_skip_and_verdict(tmp_path):
    lines = []
    code, path = run_preflight(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path),
                               skip={"git"}, output_fn=lines.append, steps=("git",))
    assert code == 0 and path is None and "GO: 0 passed, 0 failed, 1 skipped" in lines[-1]
    assert preflight.verdict([Result("a", FAIL, "")]).startswith("NO-GO")


def test_unknown_skip_name(capsys):
    assert preflight.main(["--skip", "nope"]) == 2


# ---------- smoke check (HTTP) ----------

class FakeResponse:
    def __init__(self, status=200, lines=(), cookies=None, headers=None):
        self.status_code = status
        self._lines = lines
        self.cookies = cookies or {}
        self.headers = headers or {}

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


class FakeHTTP:
    """A web app: /healthz, a protected /api/state, login and turns."""

    def __init__(self, token, health=200, protected=401):
        self.token, self.health, self.protected = token, health, protected
        self.requests = []

    def get(self, url, headers=None, timeout=0, **kw):
        self.requests.append(("GET", url, headers))
        if url.endswith("/healthz"):
            return FakeResponse(self.health)
        return FakeResponse(200 if headers.get("Cookie") else self.protected)

    def post(self, url, headers=None, timeout=0, json=None, files=None, **kw):
        self.requests.append(("POST", url, headers))
        if url.endswith("/api/login"):
            ok = json["token"] == self.token
            return FakeResponse(200 if ok else 401,
                                headers={"set-cookie": "hc_session=abc.def; Path=/; Secure"})
        if not headers.get("Cookie"):
            return FakeResponse(401)
        events = ([b'{"type": "transcript", "text": "hey claude what\'s on my calendar"}']
                  if files else [])
        events += [b'{"type": "reply", "text": "Physics lab at 2 PM.", "audio": "/a/1"}',
                   b'{"type": "done"}', b'{"type": "never read"}']
        return FakeResponse(200, [e.decode() for e in events])


def smoke_with(http, token="t" * 32, voice_ws=None, url="http://127.0.0.1:8765/",
               allow_http=False):
    return {o.name: o for o in smoke.run_smoke(
        url, token, preflight.FIXTURE,
        client=smoke.WebClient(url, session=http),
        voice_ws=voice_ws, allow_http=allow_http)}


def test_smoke_all_pass():
    http = FakeHTTP("t" * 32)
    got = smoke_with(http)
    assert [o.status for o in got.values()] == [PASS] * 5, got
    assert "heard" in got["audio turn"].detail
    # the session cookie went back by hand (it's Secure; plain http here)
    turn_headers = [h for m, u, h in http.requests if u.endswith("/api/turn")]
    assert turn_headers and all(h == {"Cookie": "hc_session=abc.def"} for h in turn_headers)


def test_smoke_runs_the_voice_check_after_login():
    seen = []

    def voice(client, wav):
        seen.append(client.cookie)
        return smoke.Outcome("voice turn", PASS, "ok")

    got = smoke_with(FakeHTTP("t" * 32), voice_ws=voice)
    assert got["voice turn"].status == PASS and seen == ["abc.def"]


def ws_frame(header, payload=b""):
    import json
    import struct

    head = json.dumps(header).encode()
    return struct.pack(">I", len(head)) + head + payload


class FakeSocket:
    """The server side of /api/voice for one turn."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def recv(self, timeout=None):
        return self.replies.pop(0)

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


GOOD_TURN = ['{"type": "ready", "settings": {}}',
             '{"type": "transcript", "turn": 1, "text": "Hey Claude, what\'s on my calendar tomorrow?"}',
             '{"type": "text", "turn": 1, "delta": "Tomorrow you have "}',
             '{"type": "text", "turn": 1, "delta": "Physics lab."}',
             ws_frame({"type": "audio", "turn": 1, "index": 0}, b"RIFF....WAVE"),
             '{"type": "turn_done", "turn": 1, "chunks": 1}']


def test_voice_turn_over_the_socket():
    import json
    import struct

    sock, opened = FakeSocket(GOOD_TURN), {}

    def connect(url, origin, open_timeout, additional_headers):
        opened.update(url=url, origin=origin, headers=additional_headers)
        return sock

    c = smoke.WebClient("https://hey.example.com/")
    c.cookie = "abc.def"
    got = smoke.voice_turn(c, preflight.FIXTURE, connect=connect)
    assert got.status == PASS, got.detail
    assert "1 audio chunk" in got.detail and "Physics lab" in got.detail
    assert opened == {"url": "wss://hey.example.com/api/voice",
                      "origin": "https://hey.example.com",
                      "headers": {"Cookie": "hc_session=abc.def"}}
    frame = sock.sent[0]
    n = struct.unpack(">I", frame[:4])[0]
    assert json.loads(frame[4:4 + n]) == {"type": "utterance", "turn": 1,
                                          "sample_rate": 16000}
    assert len(frame) - 4 - n > 16000 * 2 * 4  # > 4 s of 16 kHz PCM16
    assert json.loads(sock.sent[-1]) == {"type": "end"}


def test_voice_turn_skips_without_the_route():
    class NotFound(Exception):
        response = type("R", (), {"status_code": 404})()

    def connect(*a, **k):
        raise NotFound("404")

    got = smoke.voice_turn(smoke.WebClient("http://pi:8765"), preflight.FIXTURE,
                           connect=connect)
    assert got.status == SKIP


@pytest.mark.parametrize("events,chunks,why", [
    ([{"type": "error", "message": "Sorry, something went wrong."}], 0, "error event"),
    ([{"type": "transcript", "text": "hi"}, {"type": "text", "delta": "Hello."}], 1,
     "no turn_done"),
    ([{"type": "transcript", "text": ""}, {"type": "turn_done"}], 0, "no transcript"),
    ([{"type": "transcript", "text": "hi"}, {"type": "text", "delta": "Hello."},
      {"type": "turn_done"}], 0, "no audio"),
])
def test_judge_voice_failures(events, chunks, why):
    status, detail = smoke.judge_voice(events, chunks)
    assert status == FAIL and why in detail


def test_smoke_unprotected_route_fails():
    assert smoke_with(FakeHTTP("t" * 32, protected=200))["auth required"].status == FAIL


def test_smoke_wrong_token_stops_after_login():
    got = smoke_with(FakeHTTP("right" * 8), token="wrong" * 8)
    assert got["login"].status == FAIL and "text turn" not in got


def test_smoke_health_down_stops():
    got = smoke_with(FakeHTTP("t" * 32, health=503))
    assert list(got) == ["healthz"] and got["healthz"].status == FAIL


def test_smoke_no_token_skips_login():
    assert smoke_with(FakeHTTP("t" * 32), token=None)["login"].status == SKIP


def test_smoke_connection_error_is_a_fail():
    import requests

    class Down:
        def get(self, *a, **k):
            raise requests.ConnectionError("refused")

    got = smoke_with(Down())
    assert got["healthz"].status == FAIL and "ConnectionError" in got["healthz"].detail


@pytest.mark.parametrize("events,status,why", [
    ([{"type": "error", "message": "I didn't catch that."}, {"type": "done"}],
     FAIL, "error event"),
    ([{"type": "done"}], FAIL, "no reply"),
    ([{"type": "reply", "text": "Sorry, something went wrong."}], FAIL, "error reply"),
    ([{"type": "http", "status": 409}], FAIL, "HTTP 409"),
])
def test_judge_turn(events, status, why):
    got, detail = smoke.judge_turn(events, want_transcript=False)
    assert got == status and why in detail


def test_audio_turn_needs_a_transcript():
    got, detail = smoke.judge_turn([{"type": "reply", "text": "Hi."}], True)
    assert got == FAIL and "no transcript" in detail


def test_error_replies_match_the_cli():
    """The canned failure sentences the checks look for are the ones the
    assistant actually says."""
    source = (ROOT / "assistant" / "cli.py").read_text() + \
        (ROOT / "assistant" / "chat.py").read_text()
    for reply in smoke.ERROR_REPLIES:
        assert reply in source, reply


# ---------- the wrapper scripts ----------

SCRIPTS = ["scripts/preflight.sh", "scripts/cloud_smoke.sh", "scripts/install_pi.sh",
           "scripts/install_service.sh", "scripts/ci/pi_simulation.sh"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_shell_scripts_parse(script):
    import shutil

    bash = shutil.which("bash")
    if not bash or preflight.sys.platform == "win32":
        pytest.skip("bash not available")
    subprocess.run([bash, "-n", str(ROOT / script)], check=True)


@pytest.mark.parametrize("script", SCRIPTS)
def test_shellcheck(script):
    import shutil

    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    p = subprocess.run(["shellcheck", str(ROOT / script)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout


@pytest.mark.parametrize("script", sorted(p.relative_to(ROOT).as_posix()
                                          for p in (ROOT / "scripts").rglob("*.ps1")))
def test_powershell_scripts_parse(script):
    import shutil

    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell not available")
    path = str(ROOT / script).replace("'", "''")
    p = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                        "$e = $null; [System.Management.Automation.Language.Parser]::"
                        f"ParseFile('{path}', [ref]$null, [ref]$e) | Out-Null; "
                        "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 }"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.slow
def test_wake_fixture_fires_the_real_model_once():
    """The preflight's fixture through the real "Hey Claude" model and VAD:
    one wake, then a command recording of the phrase, then nothing."""
    from assistant.wakeword import EnergyVAD, FileSource, WakeListener, make_detector

    detector = make_detector(keyword_path=str(ROOT / "keywords" / "hey_claude.onnx"),
                             builtin_keyword="hey_jarvis", sensitivity=0.5)
    source = FileSource(preflight.FIXTURE, detector.frame_length)
    listener = WakeListener(detector, vad=EnergyVAD(0.015))
    woke, recording = listener.listen(source)
    assert woke and recording is not None and len(recording) > 16000
    assert listener.listen(source)[0] is False


# ---------- power (Windows as the always-on host) ----------

POWERCFG = """Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
  Subgroup GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Sleep)
    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)
      Minimum Possible Setting: 0x00000000
      Maximum Possible Setting: 0xffffffff
      Possible Settings increment: 0x00000001
      Possible Settings units: Seconds
    Current AC Power Setting Index: 0x{ac:08x}
    Current DC Power Setting Index: 0x00000384
"""


def test_parse_ac_index():
    assert preflight.parse_ac_index(POWERCFG.format(ac=1800)) == 1800
    assert preflight.parse_ac_index("Invalid Parameters") is None


@pytest.mark.parametrize("sleep,hib,usb,status,why", [
    (0, 0, 0, PASS, "sleep never"),
    (0, 0, None, PASS, "hibernate never"),
    (1800, 0, 0, WARN, "sleep after 30 min"),
    (0, 10800, 0, WARN, "hibernate"),
    (0, 0, 1, WARN, "USB selective suspend"),
    (None, None, None, WARN, "couldn't read"),
])
def test_judge_power(sleep, hib, usb, status, why):
    got, detail = preflight.judge_power(sleep, hib, usb)
    assert got == status and why in detail
    if status == WARN:
        assert "install_windows_service.ps1 -PowerOnly" in detail


def test_power_step_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    run = FakeRun({r"STANDBYIDLE": (0, POWERCFG.format(ac=900)),
                   r"HIBERNATEIDLE": (0, POWERCFG.format(ac=0)),
                   r"48e6b7a6": (0, POWERCFG.format(ac=0))})
    r = preflight.step_power(ctx_for(tmp_path / ".env", run, tmp_path))
    assert r.status == WARN and "sleep after 15 min" in r.detail


def test_power_step_skips_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    assert preflight.step_power(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path)).status == SKIP


def test_a_warning_is_still_go(tmp_path, monkeypatch):
    monkeypatch.setitem(preflight.STEP_FUNCS, "power",
                        lambda ctx: Result("power", WARN, "sleeps"))
    lines = []
    code, _ = run_preflight(ctx_for(tmp_path / ".env", FakeRun({}), tmp_path),
                            output_fn=lines.append, steps=("power",))
    assert code == 0 and "GO: 0 passed, 0 failed, 1 warning(s), 0 skipped" in lines[-1]


# ---------- the Windows host supervisor (scripts/windows_host.py) ----------

def load_host():
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_host",
                                                  ROOT / "scripts" / "windows_host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_restarts_after_errors_and_stops_on_clean_exit():
    import io

    host = load_host()
    codes, slept = [1, 1, 0], []
    log = io.StringIO()
    sup = host.Supervisor(["py", "-m", "assistant", "--mode", "wake"], log,
                          run=lambda cmd: codes.pop(0), sleep=slept.append,
                          clock=lambda: 0.0)
    assert sup.loop() == 0
    assert slept == [host.RESTART_DELAY] * 2
    assert "exited cleanly" in log.getvalue()


def test_host_backs_off_after_a_burst_of_crashes():
    import io

    host = load_host()
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    sup = host.Supervisor(["py"], io.StringIO(), run=lambda cmd: 3, sleep=sleep,
                          clock=lambda: t[0], max_runs=host.BURST + 1)
    sup.loop()
    assert slept[:host.BURST] == [host.RESTART_DELAY] * (host.BURST - 1) + [host.BURST_PAUSE]


def test_host_waits_for_the_network():
    host = load_host()
    t, calls = [0.0], []

    def resolve(name):
        calls.append(name)
        if len(calls) < 3:
            raise OSError("no DNS")
        return "1.2.3.4"

    def sleep(s):
        t[0] += s

    assert host.wait_for_network(resolve=resolve, sleep=sleep, clock=lambda: t[0])
    assert host.wait_for_network(resolve=lambda n: (_ for _ in ()).throw(OSError()),
                                 sleep=sleep, clock=lambda: t[0], timeout=5) is False


def test_host_uses_console_python_and_a_log(tmp_path):
    host = load_host()
    exe = tmp_path / "pythonw.exe"
    exe.write_text("")
    (tmp_path / "python.exe").write_text("")
    assert host.console_python(str(exe)) == str(tmp_path / "python.exe")
    assert host.console_python("/usr/bin/python3") == "/usr/bin/python3"
    assert host.log_path("wake", tmp_path) == tmp_path / "logs" / "host-wake.log"
    assert host.main(["nope"]) == 2
    assert host.main(["wake", "8765"]) == 2 and host.main(["web", "port"]) == 2


def test_host_web_command_keeps_port_8765_on_localhost():
    """`tailscale serve` on Kyle's PC points at 127.0.0.1:8765."""
    host = load_host()
    cmd = host.build_command("web")
    assert cmd[1:] == ["-m", "assistant", "--mode", "web", "--host", "127.0.0.1",
                       "--port", "8765"]
    assert host.build_command("web", "9000")[-1] == "9000"
    assert host.build_command("wake")[1:] == ["-m", "assistant", "--mode", "wake"]


def test_windows_service_script_registers_both_tasks():
    text = (ROOT / "scripts" / "install_windows_service.ps1").read_text(encoding="utf-8")
    assert "windows_host.py" in text and "-AtLogOn" in text
    for needle in ("standby-timeout-ac 0", "hibernate-timeout-ac 0",
                   preflight.USB_SUSPEND[1], "Read-Host", "-Uninstall",
                   "'Hey Claude Web'", "$Port = 8765", "Get-NetTCPConnection"):
        assert needle in text, needle
    # -Uninstall leaves tailscale serve alone
    uninstall = text[text.index("if ($Uninstall)"):]
    uninstall = uninstall[:uninstall.index("exit 0")]
    assert "tailscale serve" in uninstall
    assert not re.search(r"&\s*tailscale|tailscale(\.exe)?\s+serve\s+(reset|off|--https=\d+\s+off)",
                         text, re.IGNORECASE)


def test_mic_level_snippet_opens_device_zero(monkeypatch, tmp_path, capsys):
    """The mic-level step records from the configured device, index 0 too,
    and names the device it opened."""
    import sys
    from types import SimpleNamespace

    import numpy as np

    recorded = {}
    devices = [{"name": "Front Mic", "max_input_channels": 1, "index": 0},
               {"name": "Default Mic", "max_input_channels": 1, "index": 1}]

    def query_devices(device=None, kind=None):
        return devices[1] if device is None else devices[device]

    def rec(frames, samplerate, channels, dtype, device=None):
        recorded["device"] = device
        return np.full((frames, 1), 0.1, dtype="float32")

    fake = SimpleNamespace(query_devices=query_devices, rec=rec, wait=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    (tmp_path / "config.toml").write_text("[wake]\nmic_device = 0\n")
    monkeypatch.chdir(tmp_path)
    exec(compile(preflight.LEVEL_SNIPPET, "level", "exec"), {})
    out = capsys.readouterr().out
    assert recorded["device"] == 0 and "MIC [0] Front Mic" in out
    run = FakeRun({r"-c": (0, out)})
    r = preflight.step_mic_level(ctx_for(tmp_path / ".env", run, tmp_path, hardware=True))
    assert r.status == PASS and r.detail.startswith("[0] Front Mic: peak -20.0 dBFS")


def test_windows_service_legacy_task_scenarios():
    """The legacy web task flow of install_windows_service.ps1, run in
    PowerShell with Task Scheduler, the port and processes faked."""
    import shutil

    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell not available")
    p = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(ROOT / "tests" / "ps" / "install_windows_service.tests.ps1"),
                        "-Script", str(ROOT / "scripts" / "install_windows_service.ps1")],
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.count("PASS ") == 7, p.stdout


@pytest.mark.parametrize("url,unsafe", [
    ("https://hey-claude.onrender.com", False), ("http://127.0.0.1:8765", False),
    ("http://localhost:8765/", False), ("http://[::1]:8765", False),
    ("http://raspberrypi.local:8765", True), ("http://100.64.1.2:8765", True),
    ("ftp://example.com", True), ("hey-claude.onrender.com", True)])
def test_token_only_over_https_or_to_this_machine(url, unsafe):
    assert (smoke.insecure_for_token(url) is not None) is unsafe


def test_smoke_never_posts_the_token_over_remote_http():
    http = FakeHTTP("t" * 32)
    got = smoke_with(http, url="http://raspberrypi.local:8765")
    assert got["login"].status == FAIL and "refusing" in got["login"].detail
    assert not any(u.endswith("/api/login") for _, u, _ in http.requests)
    allowed = smoke_with(FakeHTTP("t" * 32), url="http://raspberrypi.local:8765",
                         allow_http=True)
    assert allowed["login"].status == PASS
