"""Keep `python -m assistant --mode <mode>` running on the Windows PC.

Started hidden (pythonw.exe) at logon by the Task Scheduler tasks that
scripts/install_windows_service.ps1 registers:

    pythonw.exe scripts\\windows_host.py wake
    pythonw.exe scripts\\windows_host.py web [PORT]    (default 8765)

It waits for the network (up to 2 minutes, like the Pi's systemd unit),
starts the assistant without a console window, writes its output to
logs/host-<mode>.log, and restarts it when it exits with an error: after 10
seconds, or after 5 minutes once it has crashed 5 times in 5 minutes (a
missing key, the network down for long). A clean exit (code 0) ends it.
Task Scheduler's own "restart on failure" only covers this supervisor
itself; it doesn't see the assistant's exit codes.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = ("wake", "web")
DEFAULT_PORT = 8765  # what `tailscale serve` on Kyle's PC points at
RESTART_DELAY = 10
BURST, BURST_WINDOW, BURST_PAUSE = 5, 300, 300
MAX_LOG_BYTES = 5_000_000
CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for the child


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


class Supervisor:
    """Runs `command` until it exits cleanly, restarting it after errors."""

    def __init__(self, command, log, run=None, sleep=time.sleep,
                 clock=time.monotonic, max_runs: int | None = None):
        self.command = command
        self.log = log
        self.run = run or self._run
        self.sleep = sleep
        self.clock = clock
        self.max_runs = max_runs  # tests
        self.crashes: list[float] = []

    def _run(self, command) -> int:
        flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(command, cwd=ROOT, stdout=self.log,
                              stderr=subprocess.STDOUT, creationflags=flags).returncode

    def say(self, text: str) -> None:
        self.log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [host] {text}\n")
        self.log.flush()

    def loop(self) -> int:
        runs = 0
        while True:
            runs += 1
            self.say(f"starting: {' '.join(self.command[1:])}")
            code = self.run(self.command)
            if code == 0:
                self.say("exited cleanly; not restarting")
                return 0
            now = self.clock()
            self.crashes = [t for t in self.crashes if now - t < BURST_WINDOW] + [now]
            if self.max_runs is not None and runs >= self.max_runs:
                return code
            if len(self.crashes) >= BURST:
                self.say(f"exited with {code}; {BURST} crashes in "
                         f"{BURST_WINDOW // 60} min, waiting {BURST_PAUSE // 60} min "
                         "(see logs/assistant.log)")
                self.crashes.clear()
                self.sleep(BURST_PAUSE)
            else:
                self.say(f"exited with {code}; restarting in {RESTART_DELAY} s")
                self.sleep(RESTART_DELAY)


def build_command(mode: str, port: str = str(DEFAULT_PORT)) -> list[str]:
    command = [console_python(), "-m", "assistant", "--mode", mode]
    if mode == "web":  # this PC only; `tailscale serve` points at this port
        command += ["--host", "127.0.0.1", "--port", port]
    return command


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    port = argv[1] if len(argv) == 2 else str(DEFAULT_PORT)
    if mode not in MODES or len(argv) > 2 or not port.isdigit() \
            or (mode == "wake" and len(argv) == 2):
        print("usage: windows_host.py wake | web [PORT]", file=sys.stderr)
        return 2
    os.chdir(ROOT)
    command = build_command(mode, port)
    with open(log_path(mode), "a", encoding="utf-8", buffering=1) as log:
        sup = Supervisor(command, log)
        if not wait_for_network():
            sup.say("network not up after 120 s; starting anyway")
        return sup.loop()


if __name__ == "__main__":
    sys.exit(main())
