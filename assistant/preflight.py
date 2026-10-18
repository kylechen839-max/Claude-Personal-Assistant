"""One-pass readiness check: python -m assistant.preflight [--hardware].

Run through scripts/preflight.ps1 (Windows) or scripts/preflight.sh (Linux,
Pi). Every step ends PASS, FAIL or SKIP; the table is printed and saved with
each step's output to logs/preflight-<timestamp>.txt. Values from .env are
loaded into this process (and the commands it starts) only, and are masked
in everything printed or saved.

Go means no FAIL. A WARN is worth fixing but doesn't block (e.g. the PC
is set to sleep, which matters only if it's the always-on host). A SKIP says why (e.g. --hardware not given, a feature not
on this branch yet).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from assistant import smoke

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = smoke.FIXTURE
WAKE_PHRASE = "what's on my calendar tomorrow"
TEXT_QUESTIONS = ("What's on my calendar tomorrow?",
                  "What assignments are due this week?")
TEXT_TOOLS = ("list_calendar_events", "list_assignments")
PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
STEPS = ("git", "env", "check", "pytest", "pytest-live", "wake-e2e",
         "text-tools", "web", "tts-pyttsx3", "tts-piper", "audio-devices",
         "mic-level", "power")
LOCAL_FILES = {"config.toml"}  # may differ per machine without failing "git"
MIN_SECRET_LENGTH = 4
MIC_MIN_PEAK_DB = -35.0  # speech near the mic peaks around -20..-6 dBFS


@dataclass
class Result:
    name: str
    status: str
    detail: str
    output: str = ""  # command output, for the report


# ---------- secrets ----------

class Redactor:
    """Masks every .env value (and anything that looks like a key)."""

    def __init__(self, values=()):
        self.values: list[str] = []
        self.add(values)

    def add(self, values) -> None:
        found = {v.strip() for v in values if v and len(v.strip()) >= MIN_SECRET_LENGTH}
        self.values = sorted(set(self.values) | found, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        from assistant.config import redact

        for value in self.values:
            text = text.replace(value, "[REDACTED]")
        return redact(text)


def read_env_file(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


# ---------- running commands ----------

def run_command(cmd: list[str], cwd: Path, env: dict, input: str | None = None,
                timeout: float = 900) -> tuple[int, str]:
    """(exit code, stdout+stderr); a timeout or missing program is code -1."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, input=input, text=True,
                           encoding="utf-8", errors="replace",
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return -1, out + f"\n(timed out after {timeout:.0f} s)"
    except OSError as e:
        return -1, f"could not run {cmd[0]}: {e}"


@dataclass
class Context:
    root: Path = ROOT
    python: str = sys.executable
    env: dict = field(default_factory=lambda: {**os.environ,
                                               "PYTHONIOENCODING": "utf-8",
                                               "PYTHONUNBUFFERED": "1"})
    env_file: Path = ROOT / ".env"
    branch: str = "master"
    hardware: bool = False
    run: Callable | None = None  # default run_command
    redact: Redactor = field(default_factory=Redactor)
    say: Callable = print  # progress lines (redacted by the caller)
    _help: str | None = None

    def __post_init__(self):
        self.run = self.run or run_command

    def py(self, *args: str, **kw) -> tuple[int, str]:
        return self.run([self.python, *args], cwd=self.root, env=self.env, **kw)

    def has_key(self, name: str) -> bool:
        return bool(self.env.get(name))

    def cli_help(self) -> str:
        if self._help is None:
            self._help = self.py("-m", "assistant", "--help", timeout=120)[1]
        return self._help

    def has_mode(self, mode: str) -> bool:
        m = re.search(r"--mode \{([^}]*)\}", self.cli_help())
        return bool(m) and mode in m.group(1).split(",")


# ---------- parsing ----------

def parse_junit(path: Path) -> dict:
    """{'tests', 'failures', 'errors', 'skipped', 'skips': [(test, reason)]}."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {k: sum(int(s.get(k, 0)) for s in suites)
              for k in ("tests", "failures", "errors", "skipped")}
    skips, failed = [], []
    for case in root.iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        sk = case.find("skipped")
        if sk is not None:
            skips.append((name, sk.get("message", "")))
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(name)
    totals["passed"] = (totals["tests"] - totals["failures"] - totals["errors"]
                        - totals["skipped"])
    return {**totals, "skips": skips, "failed": failed}


def summarize_junit(r: dict) -> str:
    parts = [f"{r['passed']} passed"]
    for key in ("failures", "errors", "skipped"):
        if r[key]:
            parts.append(f"{r[key]} {'failed' if key == 'failures' else key}")
    return ", ".join(parts)


def parse_wake_output(out: str) -> dict:
    """What `--mode wake --audio-file --timings` printed: did it wake, what
    was heard, and what did Claude say."""
    heard = re.findall(r"^You: (.*)$", out, re.MULTILINE)
    replies = re.findall(r"^Claude: (.*)$", out, re.MULTILINE)
    return {"woke": bool(re.search(r"\(Turn timings from wake:", out))
            or bool(heard),
            "timings": re.findall(r"\((Turn timings from wake: .*)\)$", out,
                                  re.MULTILINE),
            "heard": [h.strip() for h in heard if h.strip()],
            "replies": [r.strip() for r in replies if r.strip()]}


def parse_text_replies(out: str) -> list[str]:
    """Claude's replies in text-mode output (the 'You: ' prompt shares their
    line when input is piped)."""
    return [m.strip() for m in re.findall(r"(?:^|You: )Claude: (.*)$", out,
                                          re.MULTILINE)]


def judge_level(peak_db: float, rms_db: float,
                min_peak_db: float = MIC_MIN_PEAK_DB) -> tuple[str, str]:
    detail = f"peak {peak_db:.1f} dBFS, rms {rms_db:.1f} dBFS"
    if peak_db >= -0.1:
        return FAIL, detail + " (clipping: lower the mic gain)"
    if peak_db < min_peak_db:
        return FAIL, detail + f" (too quiet: under {min_peak_db:.0f} dBFS; " \
                              "wrong mic or gain too low)"
    return PASS, detail


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


# ---------- steps ----------

def step_git(ctx: Context) -> Result:
    code, branch = ctx.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           cwd=ctx.root, env=ctx.env, timeout=60)
    if code != 0:
        return Result("git", FAIL, "not a git checkout (or git missing)", branch)
    branch = branch.strip()
    _, status = ctx.run(["git", "status", "--porcelain"], cwd=ctx.root,
                        env=ctx.env, timeout=60)
    _, commit = ctx.run(["git", "rev-parse", "--short", "HEAD"], cwd=ctx.root,
                        env=ctx.env, timeout=60)
    changed = [line for line in status.splitlines() if line.strip()]
    # Per-machine settings (mic, voice, Whisper size) live in config.toml.
    local = [c for c in changed if c[3:].strip() in LOCAL_FILES and c[1] == "M"]
    changed = [c for c in changed if c not in local]
    problems = []
    if branch != ctx.branch:
        problems.append(f"on {branch}, not {ctx.branch}")
    if changed:
        problems.append(f"{len(changed)} uncommitted change(s)")
    detail = f"{branch} @ {commit.strip()}"
    note = (f" (local settings in {', '.join(c[3:] for c in local)})" if local else "")
    return Result("git", FAIL if problems else PASS,
                  detail + ("; " + "; ".join(problems) if problems else ", clean")
                  + note, status)


def step_env(ctx: Context) -> Result:
    if not ctx.env_file.exists():
        return Result("env", FAIL, f"{ctx.env_file.name} not found (copy "
                      ".env.example to .env and fill it in)")
    values = read_env_file(ctx.env_file)
    ctx.redact.add(values.values())
    for k, v in values.items():
        ctx.env.setdefault(k, v)  # like load_dotenv: the real environment wins
        os.environ.setdefault(k, v)  # this process only (for the web token)
    names = sorted(k for k, v in values.items() if v)
    missing = [k for k in ("ANTHROPIC_API_KEY",) if not ctx.has_key(k)]
    detail = f"{len(names)} set: {', '.join(names) or 'none'} (values hidden)"
    if missing:
        return Result("env", FAIL, detail + f"; missing {', '.join(missing)}")
    return Result("env", PASS, detail)


def step_check(ctx: Context) -> Result:
    code, out = ctx.py("-m", "assistant", "--check", timeout=300)
    fails = [line for line in out.splitlines() if line.startswith("[FAIL]")]
    warns = sum(line.startswith("[WARN]") for line in out.splitlines())
    if code == 0:
        return Result("check", PASS, f"all required checks passed ({warns} "
                      "warning(s))", out)
    return Result("check", FAIL, "; ".join(fails) or f"exit code {code}", out)


def _pytest(ctx: Context, name: str, marker: str, timeout: float) -> tuple[int, str, dict | None]:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        code, out = ctx.py("-m", "pytest", "-m", marker, "-q", "-rs",
                           "-p", "no:cacheprovider", f"--junitxml={junit}",
                           timeout=timeout)
        parsed = parse_junit(junit) if junit.exists() else None
    return code, out, parsed


def step_pytest(ctx: Context) -> Result:
    code, out, r = _pytest(ctx, "pytest", "not live", timeout=1800)
    if r is None:
        return Result("pytest", FAIL, f"pytest did not run (exit {code})", out)
    detail = summarize_junit(r)
    if code != 0:
        return Result("pytest", FAIL, detail + "; " + ", ".join(r["failed"][:3]), out)
    return Result("pytest", PASS, detail, out)


def step_pytest_live(ctx: Context) -> Result:
    code, out, r = _pytest(ctx, "pytest-live", "live", timeout=600)
    if r is None:
        return Result("pytest-live", FAIL, f"pytest did not run (exit {code})", out)
    detail = summarize_junit(r) + " (read-only calls to Pinecone, Notion, Google)"
    if code != 0:
        return Result("pytest-live", FAIL, detail, out)
    if r["tests"] == 0:
        return Result("pytest-live", FAIL, "no live tests found", out)
    if r["skipped"]:
        reasons = "; ".join(sorted({reason for _, reason in r["skips"]}))
        return Result("pytest-live", FAIL, detail + f": {reasons}", out)
    return Result("pytest-live", PASS, detail, out)


def step_wake_e2e(ctx: Context) -> Result:
    from assistant.stt import rough_match

    if not ctx.has_key("ANTHROPIC_API_KEY"):
        return Result("wake-e2e", SKIP, "ANTHROPIC_API_KEY not set")
    code, out = ctx.py("-m", "assistant", "--mode", "wake", "--audio-file",
                       str(FIXTURE), "--timings", "--tts", "print", timeout=600)
    w = parse_wake_output(out)
    problems = []
    if code != 0:
        problems.append(f"exit code {code}")
    if not w["woke"]:
        problems.append("no wake")
    if "(Something went wrong" in out:
        problems.append("the turn raised an error (see the report)")
    if not w["heard"]:
        problems.append("no transcript")
    elif not rough_match(WAKE_PHRASE, w["heard"][0]):
        problems.append(f"heard {w['heard'][0]!r}")
    if not w["replies"]:
        problems.append("no reply")
    elif smoke.is_error_reply(w["replies"][0]):
        problems.append(f"error reply {w['replies'][0]!r}")
    timing = w["timings"][0].split(": ", 1)[-1] if w["timings"] else ""
    if problems:
        return Result("wake-e2e", FAIL, "; ".join(problems), out)
    return Result("wake-e2e", PASS, f"woke, heard {w['heard'][0]!r}, replied "
                  f"{smoke.shorten(w['replies'][0], 60)!r}"
                  + (f" [{timing}]" if timing else ""), out)


def _log_path(ctx: Context) -> Path:
    from assistant.config import load_config

    path = Path(load_config(ctx.root / "config.toml", None)["logging"]["file"])
    return path if path.is_absolute() else ctx.root / path


def step_text_tools(ctx: Context) -> Result:
    if not ctx.has_key("ANTHROPIC_API_KEY"):
        return Result("text-tools", SKIP, "ANTHROPIC_API_KEY not set")
    log_file = _log_path(ctx)
    start = log_file.stat().st_size if log_file.exists() else 0
    stdin = "\n".join(TEXT_QUESTIONS) + "\nquit\n"
    code, out = ctx.py("-m", "assistant", "--mode", "text", input=stdin,
                       timeout=300)
    replies = parse_text_replies(out)
    new_log = ""
    if log_file.exists():
        with open(log_file, encoding="utf-8", errors="replace") as f:
            if log_file.stat().st_size >= start:
                f.seek(start)  # (a rotated log is read whole)
            new_log = f.read()
    problems = []
    if code != 0:
        problems.append(f"exit code {code}")
    off = re.findall(r"^\((\w+ tools off: [^)]*)\)", out, re.MULTILINE)
    problems += off
    if len(replies) < len(TEXT_QUESTIONS):
        problems.append(f"{len(replies)} of {len(TEXT_QUESTIONS)} replies")
    problems += [f"error reply {r!r}" for r in replies if smoke.is_error_reply(r)]
    unused = [t for t in TEXT_TOOLS if f"Claude called {t}" not in new_log]
    if unused and not off:
        problems.append(f"tool(s) not called: {', '.join(unused)}")
    if problems:
        return Result("text-tools", FAIL, "; ".join(problems), out)
    return Result("text-tools", PASS, "calendar and Notion answered: "
                  + " | ".join(smoke.shorten(r, 50) for r in replies), out)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step_web(ctx: Context) -> list[Result]:
    if not ctx.has_mode("web"):
        return [Result("web", SKIP, "--mode web is not on this branch yet")]
    token = ctx.env.get("WEB_TOKEN")
    if not token:
        return [Result("web", SKIP, "WEB_TOKEN not in .env (run `python -m "
                       "assistant --mode web` once to create it)")]
    if not ctx.has_key("ANTHROPIC_API_KEY"):
        return [Result("web", SKIP, "ANTHROPIC_API_KEY not set")]
    import requests

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as log:
        server = subprocess.Popen(
            [ctx.python, "-m", "assistant", "--mode", "web", "--host",
             "127.0.0.1", "--port", str(port)], cwd=ctx.root, env=ctx.env,
            stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            up, deadline = False, time.monotonic() + 180
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    requests.get(base + "/healthz", timeout=2)
                    up = True
                    break
                except requests.RequestException:
                    time.sleep(1)
            if not up:
                log.seek(0)
                why = (f"exited with {server.returncode}" if server.poll()
                       is not None else "not answering after 180 s")
                return [Result("web", FAIL, f"server {why}", log.read())]
            outcomes = smoke.run_smoke(base, token, FIXTURE)
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
            log.seek(0)
            server_log = log.read()
    return [Result(f"web: {o.name}", o.status, o.detail,
                   server_log if i == 0 else "")
            for i, o in enumerate(outcomes)]


TTS_SNIPPET = r"""
import sys, wave
from assistant.tts import make_tts
engine, path = sys.argv[1], sys.argv[2]
opts = {"piper_voice": sys.argv[3]} if engine == "piper" else {}
tts = make_tts(engine, **opts)
for attempt in range(3):  # pyttsx3's eSpeak driver now and then writes nothing
    tts.save_to_file("Preflight check. This is the assistant's voice.", path)
    try:
        with wave.open(path) as w:
            frames, rate = w.getnframes(), w.getframerate()
    except (EOFError, wave.Error):
        frames, rate = 0, 1
    if frames:
        break
print(f"ENGINE={type(tts).__name__} FRAMES={frames} RATE={rate}")
"""


def _tts_render(ctx: Context, name: str, engine: str, *extra: str) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / f"{engine}.wav")
        code, out = ctx.py("-c", TTS_SNIPPET, engine, path, *extra, timeout=300)
    m = re.search(r"ENGINE=(\w+) FRAMES=(\d+) RATE=(\d+)", out)
    if code != 0 or not m:
        return Result(name, FAIL, f"render failed (exit {code})", out)
    cls, frames, rate = m.group(1), int(m.group(2)), int(m.group(3))
    if engine == "piper" and cls != "PiperTTS":
        return Result(name, FAIL, f"fell back to {cls}", out)
    if frames == 0:
        return Result(name, FAIL, "empty .wav", out)
    return Result(name, PASS, f"{cls}: {frames / rate:.1f} s of audio", out)


def step_tts_pyttsx3(ctx: Context) -> Result:
    return _tts_render(ctx, "tts-pyttsx3", "pyttsx3")


def step_tts_piper(ctx: Context) -> Result:
    from assistant.config import load_config
    from assistant.tts import DEFAULT_PIPER_VOICE

    voice = load_config(ctx.root / "config.toml", None)["tts"]["piper_voice"] \
        or f"voices/{DEFAULT_PIPER_VOICE}.onnx"
    path = Path(voice) if Path(voice).is_absolute() else ctx.root / voice
    if not path.exists():
        return Result("tts-piper", FAIL, f"{voice} missing; get it with: python "
                      f"-m assistant.tts --download-voice {DEFAULT_PIPER_VOICE}")
    return _tts_render(ctx, "tts-piper", "piper", str(path))


DEVICES_SNIPPET = r"""
import sounddevice as sd
for i, d in enumerate(sd.query_devices()):
    print(f"DEVICE {i} in={d['max_input_channels']} out={d['max_output_channels']} {d['name']}")
print("DEFAULT", sd.default.device)
"""

LEVEL_SNIPPET = r"""
import numpy as np, sounddevice as sd
from assistant.audio import mic_that_opens, resolve_mic_device
from assistant.config import load_config
mic = load_config("config.toml", None)["wake"]["mic_device"]
info = mic_that_opens(mic, sd)
print(f"MIC [{info.get('index', '?')}] {info['name']}")
rec = sd.rec(3 * 16000, samplerate=16000, channels=1, dtype="float32",
             device=resolve_mic_device(mic))
sd.wait()
x = rec[:, 0]
db = lambda v: 20 * np.log10(max(float(v), 1e-9))
print(f"LEVEL peak={db(np.max(np.abs(x))):.1f} rms={db(np.sqrt(np.mean(x ** 2))):.1f}")
"""


def step_audio_devices(ctx: Context) -> Result:
    if not ctx.hardware:
        return Result("audio-devices", SKIP, "needs --hardware (on the Pi)")
    code, out = ctx.py("-c", DEVICES_SNIPPET, timeout=60)
    devices = re.findall(r"^DEVICE (\d+) in=(\d+) out=(\d+) (.*)$", out, re.MULTILINE)
    ins = [f"[{i}] {n}" for i, a, _, n in devices if int(a) > 0]
    outs = [f"[{i}] {n}" for i, _, b, n in devices if int(b) > 0]
    if code != 0 or not ins or not outs:
        return Result("audio-devices", FAIL, f"inputs: {ins or 'none'}; outputs: "
                      f"{outs or 'none'}", out)
    return Result("audio-devices", PASS, f"in: {'; '.join(ins)} | out: "
                  f"{'; '.join(outs)}", out)


def step_mic_level(ctx: Context) -> Result:
    if not ctx.hardware:
        return Result("mic-level", SKIP, "needs --hardware (on the Pi)")
    ctx.say("    Mic level test: say 'Hey Claude, testing one two three' now (3 s)...")
    code, out = ctx.py("-c", LEVEL_SNIPPET, timeout=60)
    m = re.search(r"LEVEL peak=(-?[\d.]+) rms=(-?[\d.]+)", out)
    if code != 0 or not m:
        return Result("mic-level", FAIL, f"recording failed (exit {code})", out)
    status, detail = judge_level(float(m.group(1)), float(m.group(2)))
    mic = re.search(r"^MIC (.*)$", out, re.MULTILINE)
    return Result("mic-level", status, (f"{mic.group(1)}: " if mic else "") + detail, out)


USB_SUSPEND = ("2a737441-1930-4402-8d77-b2bebba308a3",  # powercfg: USB settings,
               "48e6b7a6-50f5-4782-a5d4-53bb8f07e226")  # selective suspend


def parse_ac_index(out: str) -> int | None:
    """The AC value of one `powercfg /query` setting (seconds, or 0/1)."""
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if m else None


def judge_power(sleep: int | None, hibernate: int | None,
                usb_suspend: int | None) -> tuple[str, str]:
    def after(v):
        return "unknown" if v is None else ("never" if v == 0 else f"after {v // 60} min")
    detail = (f"plugged in: sleep {after(sleep)}, hibernate {after(hibernate)}"
              + {0: ", USB suspend off", 1: ", USB suspend on"}.get(usb_suspend, ""))
    problems = []
    if sleep:
        problems.append("sleep stops the assistant completely")
    if hibernate:
        problems.append("so does hibernate")
    if usb_suspend == 1:
        problems.append("USB selective suspend can power down a USB mic")
    if sleep is None or hibernate is None:
        problems.append("couldn't read the power plan")
    if problems:
        return WARN, (detail + " - " + "; ".join(problems) + " (fix: "
                      ".\\scripts\\install_windows_service.ps1 -PowerOnly)")
    return PASS, detail


def step_power(ctx: Context) -> Result:
    """Windows: the always-on settings (sleep would stop everything)."""
    if sys.platform != "win32":
        return Result("power", SKIP, "Windows only (the PC as the always-on host)")

    def query(*setting):
        return ctx.run(["powercfg", "/query", "SCHEME_CURRENT", *setting],
                       cwd=ctx.root, env=ctx.env, timeout=60)[1]
    outs = [query("SUB_SLEEP", "STANDBYIDLE"), query("SUB_SLEEP", "HIBERNATEIDLE"),
            query(*USB_SUSPEND)]
    status, detail = judge_power(*(parse_ac_index(o) for o in outs))
    return Result("power", status, detail, "\n".join(outs))


STEP_FUNCS = {
    "git": step_git, "env": step_env, "check": step_check,
    "pytest": step_pytest, "pytest-live": step_pytest_live,
    "wake-e2e": step_wake_e2e, "text-tools": step_text_tools, "web": step_web,
    "tts-pyttsx3": step_tts_pyttsx3, "tts-piper": step_tts_piper,
    "audio-devices": step_audio_devices, "mic-level": step_mic_level,
    "power": step_power,
}


# ---------- report ----------

def format_table(results: list[Result]) -> str:
    width = max((len(r.name) for r in results), default=4)
    return "\n".join(f"{r.status:<4}  {r.name:<{width}}  {r.detail}"
                     for r in results)


def verdict(results: list[Result]) -> str:
    failed = sum(r.status == FAIL for r in results)
    skipped = sum(r.status == SKIP for r in results)
    passed = sum(r.status == PASS for r in results)
    warned = sum(r.status == WARN for r in results)
    word = "NO-GO" if failed else "GO"
    return (f"{word}: {passed} passed, {failed} failed, "
            + (f"{warned} warning(s), " if warned else "") + f"{skipped} skipped")


def build_report(results: list[Result], started: datetime, args_text: str) -> str:
    lines = [f"Hey Claude preflight, {started:%Y-%m-%d %H:%M:%S}",
             f"Machine: {platform.node()} ({platform.system()} "
             f"{platform.release()}, {platform.machine()}), Python "
             f"{platform.python_version()}",
             f"Arguments: {args_text or '(none)'}", "",
             format_table(results), "", verdict(results), ""]
    for r in results:
        if r.output.strip():
            lines += [f"---- {r.name} ({r.status}) ----", tail(r.output, 60), ""]
    return "\n".join(lines)


def run_preflight(ctx: Context, skip=(), report_dir: Path | None = None,
                  output_fn=print, args_text: str = "",
                  steps=STEPS) -> tuple[int, Path | None]:
    """Run the steps; print (redacted) as it goes; write the report."""
    started = datetime.now()

    def say(text: str) -> None:
        output_fn(ctx.redact(text))

    ctx.say = say
    results: list[Result] = []
    for name in steps:
        if name in skip:
            results.append(Result(name, SKIP, "skipped with --skip"))
            say(f"{SKIP:<4}  {name}: skipped with --skip")
            continue
        say(f"....  {name}")
        t0 = time.monotonic()
        try:
            got = STEP_FUNCS[name](ctx)
        except Exception as e:  # a broken step must not hide the others
            got = Result(name, FAIL, f"{type(e).__name__}: {e}")
        for r in got if isinstance(got, list) else [got]:
            r.detail, r.output = ctx.redact(r.detail), ctx.redact(r.output)
            results.append(r)
            say(f"{r.status:<4}  {r.name}: {r.detail} ({time.monotonic() - t0:.0f} s)")
    say("\n" + format_table(results) + "\n\n" + verdict(results))
    path = None
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"preflight-{started:%Y%m%d-%H%M%S}.txt"
        path.write_text(ctx.redact(build_report(results, started, args_text)),
                        encoding="utf-8")
        say(f"Report: {path}")
    return (1 if any(r.status == FAIL for r in results) else 0), path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant.preflight",
                                     description="Check everything in one pass")
    parser.add_argument("--hardware", action="store_true",
                        help="also list audio devices and test the mic level (Pi)")
    parser.add_argument("--skip", default="",
                        help=f"comma-separated steps to skip: {', '.join(STEPS)}")
    parser.add_argument("--branch", default="master",
                        help="branch the checkout must be on (default master)")
    parser.add_argument("--env-file", default=".env", help="secrets file (.env)")
    parser.add_argument("--report-dir", default="logs",
                        help="where the report goes (default logs/)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = skip - set(STEPS)
    if unknown:
        print(f"Unknown step(s) for --skip: {', '.join(sorted(unknown))}")
        return 2
    env_file = Path(args.env_file)
    ctx = Context(env_file=env_file if env_file.is_absolute() else ROOT / env_file,
                  branch=args.branch, hardware=args.hardware)
    if ctx.env_file.exists():  # mask its values from the very first line
        ctx.redact.add(read_env_file(ctx.env_file).values())
    report_dir = Path(args.report_dir)
    code, _ = run_preflight(ctx, skip, report_dir if report_dir.is_absolute()
                            else ROOT / report_dir,
                            args_text=" ".join(argv if argv is not None
                                               else sys.argv[1:]))
    return code


if __name__ == "__main__":
    sys.exit(main())
