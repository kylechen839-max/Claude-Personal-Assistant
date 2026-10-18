"""Keep the assistant running on a home host: python -m assistant.host MODE.

Used by the Windows Task Scheduler tasks (scripts/install_windows_service.ps1,
started hidden with pythonw.exe) and the Pi's systemd unit:

    python -m assistant.host wake
    python -m assistant.host web [PORT]      (127.0.0.1, default 8765)

It waits for the network (up to 2 minutes), starts `python -m assistant
--mode MODE` without a console window, writes its output to
logs/host-<mode>.log, and:

- restarts it after a crash: after 10 s, or after 5 minutes once it has
  crashed 5 times in 5 minutes (a missing key, the network down for long).
  A clean exit (code 0) ends the host. Task Scheduler's own "restart on
  failure" can't see the assistant's exit codes, hence this.
- with `[update] enabled = true`, updates from the `stable` branch every
  `check_minutes` (assistant/updater.py), and whenever HEAD moves (its own
  update, the other host's, or `python -m assistant --update`) restarts the
  assistant on the new code: the web app only once no turn is running (at
  most ~2 minutes), then it must answer /healthz with the new version (the
  wake loop must stay up for 20 s). If it doesn't, the update is rolled
  back and the old version restarted.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = ("wake", "web")
DEFAULT_PORT = 8765  # what `tailscale serve` on Kyle's PC points at
RESTART_DELAY = 10
BURST, BURST_WINDOW, BURST_PAUSE = 5, 300, 300
MAX_LOG_BYTES = 5_000_000
CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for the child
HEAD_POLL = 30          # seconds between "did HEAD move?" checks
IDLE_WAIT = 120         # at most this long for the web app to go quiet
HEALTH_WAIT = 120       # for the new web app to answer /healthz
WAKE_SETTLE = 20        # the new wake loop must still run after this


def log_path(mode: str, root: Path = ROOT) -> Path:
    path = root / "logs" / f"host-{mode}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_name(path.name + ".1"))
    return path


def console_python(executable: str = sys.executable) -> str:
    """python.exe next to pythonw.exe (the child needs real stdout)."""
    exe = Path(executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return executable


def wait_for_network(host: str = "api.anthropic.com", timeout: float = 120,
                     resolve=socket.gethostbyname, sleep=time.sleep,
                     clock=time.monotonic) -> bool:
    deadline = clock() + timeout
    while True:
        try:
            resolve(host)
            return True
        except OSError:
            if clock() >= deadline:
                return False
            sleep(2)


def build_command(mode: str, port: str = str(DEFAULT_PORT)) -> list[str]:
    command = [console_python(), "-m", "assistant", "--mode", mode]
    if mode == "web":  # this PC only; `tailscale serve` points at this port
        command += ["--host", "127.0.0.1", "--port", port]
    return command


def get_health(port: int | str, timeout: float = 3) -> dict | None:
    """/healthz of the local web app, or None if it doesn't answer."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                    timeout=timeout) as r:
            return json.loads(r.read() or b"{}") if r.status == 200 else None
    except (OSError, ValueError):
        return None


def update_settings(root: Path = ROOT) -> dict:
    from assistant.config import load_config

    return load_config(root / "config.toml", None)["update"]


class Host:
    """Runs one assistant child; restarts, updates and rolls back."""

    def __init__(self, mode: str, command: list[str], log, port: str = str(DEFAULT_PORT),
                 updater=None, settings=update_settings, popen=None,
                 health=get_health, sleep=time.sleep, clock=time.monotonic):
        self.mode = mode
        self.command = command
        self.log = log
        self.port = port
        self.updater = updater
        self.settings = settings
        self.popen = popen or self._popen
        self.health = health
        self.sleep = sleep
        self.clock = clock
        self.child = None
        self.child_sha: str | None = None
        self.crashes: list[float] = []

    def say(self, text: str) -> None:
        self.log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [host] {text}\n")
        self.log.flush()

    def _popen(self, command):
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.Popen(command, cwd=ROOT, stdout=self.log,
                                stderr=subprocess.STDOUT, creationflags=flags)

    # --- child ---
    def start(self) -> None:
        self.child_sha = self.updater.head() if self.updater else None
        self.say(f"starting: {' '.join(self.command[1:])}"
                 + (f" at {self.child_sha[:7]}" if self.child_sha else ""))
        self.child = self.popen(self.command)

    def stop(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        self.child.terminate()
        try:
            self.child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=20)

    def wait_until_idle(self) -> None:
        """Web: until no turn or voice conversation is running (or 2 min)."""
        if self.mode != "web":
            return
        deadline = self.clock() + IDLE_WAIT
        while self.clock() < deadline:
            h = self.health(self.port)
            if h is None or not h.get("busy"):
                return
            self.sleep(2)
        self.say(f"still busy after {IDLE_WAIT} s; restarting anyway")

    def healthy(self, sha: str | None) -> bool:
        """Did the new child come up on `sha`?"""
        if self.mode == "web":
            deadline = self.clock() + HEALTH_WAIT
            while self.clock() < deadline:
                if self.child.poll() is not None:
                    return False
                h = self.health(self.port)
                if h and h.get("ok") and (not sha or h.get("version") == sha[:7]):
                    return True
                self.sleep(2)
            return False
        self.sleep(WAKE_SETTLE)
        return self.child.poll() is None

    def restart_on(self, new: str) -> None:
        """HEAD moved to `new`: restart on it; roll back if it won't run."""
        old = self.child_sha
        self.say(f"code changed {(old or '?')[:7]} -> {new[:7]}; restarting "
                 + ("when idle" if self.mode == "web" else "now"))
        self.wait_until_idle()
        self.stop()
        self.start()
        if self.healthy(new):
            self.say(f"running {new[:7]}")
            return
        self.say(f"!!! {new[:7]} did not come up")
        if old and self.updater:
            result = self.updater.rollback_locked(old, new, f"{self.mode} did not "
                                                  "come up after the update")
            self.say(result.line())
            self.stop()
            self.start()

    # --- main loop ---
    def crashed(self, code: int) -> None:
        now = self.clock()
        self.crashes = [t for t in self.crashes if now - t < BURST_WINDOW] + [now]
        if len(self.crashes) >= BURST:
            self.say(f"exited with {code}; {BURST} crashes in {BURST_WINDOW // 60} "
                     f"min, waiting {BURST_PAUSE // 60} min (see logs/assistant.log)")
            self.crashes.clear()
            self.sleep(BURST_PAUSE)
        else:
            self.say(f"exited with {code}; restarting in {RESTART_DELAY} s")
            self.sleep(RESTART_DELAY)

    def tick(self, now: float, state: dict) -> int | None:
        """One pass of the loop; an exit code once the host should stop."""
        code = self.child.poll()
        if code is not None:
            if code == 0:
                self.say("exited cleanly; not restarting")
                return 0
            self.crashed(code)
            self.start()
            return None
        if self.updater is None:
            return None
        if now >= state["next_update"]:
            try:
                cfg = self.settings()
            except Exception as e:  # a broken config.toml mustn't stop it
                self.say(f"can't read [update] settings: {e}")
                cfg = {"enabled": False, "check_minutes": 15}
            state["next_update"] = now + max(1, float(cfg["check_minutes"])) * 60
            if cfg["enabled"]:
                self.updater.update()
        if now >= state["next_head"]:
            state["next_head"] = now + HEAD_POLL
            head = self.updater.head()
            if head and self.child_sha and head != self.child_sha \
                    and not self.updater.locked():
                self.restart_on(head)
        return None

    def loop(self, max_ticks: int | None = None) -> int:
        self.start()
        now = self.clock()
        state = {"next_update": now + 60, "next_head": now + HEAD_POLL}
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            ticks += 1
            code = self.tick(self.clock(), state)
            if code is not None:
                return code
            self.sleep(1)
        return 0


def parse(argv: list[str]) -> tuple[str, str] | None:
    mode = argv[0] if argv else ""
    port = argv[1] if len(argv) == 2 else str(DEFAULT_PORT)
    if mode not in MODES or len(argv) > 2 or not port.isdigit() \
            or (mode == "wake" and len(argv) == 2):
        return None
    return mode, port


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parsed = parse(argv)
    if parsed is None:
        print("usage: python -m assistant.host wake | web [PORT]", file=sys.stderr)
        return 2
    mode, port = parsed
    os.chdir(ROOT)
    from assistant.updater import Updater

    with open(log_path(mode), "a", encoding="utf-8", buffering=1) as log:
        host = Host(mode, build_command(mode, port), log, port)
        try:
            channel = update_settings()["channel"]
        except Exception:
            channel = "stable"
        host.updater = Updater(branch=channel, python=console_python(),
                               say=host.say)
        if not wait_for_network():
            host.say("network not up after 120 s; starting anyway")
        try:
            return host.loop()
        finally:
            host.stop()


if __name__ == "__main__":
    sys.exit(main())
