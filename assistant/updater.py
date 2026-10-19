"""Safe self-update from the `stable` branch: python -m assistant --update.

The host supervisor (assistant/host.py, used by the Windows tasks and the
Pi's systemd unit) runs this every `[update] check_minutes` when `[update]
enabled = true`; `--update` runs it once, now. `stable` is fast-forwarded
only after master's CI is green on every job, so only tested commits arrive.

One update:
  1. `git fetch origin stable`; nothing to do if HEAD is already there.
  2. Skip (and log) when the checkout has local changes to tracked files
     (config.toml excepted: it holds this machine's settings), when HEAD is
     not an ancestor of origin/stable (diverged, or a local branch with
     commits of its own), or when that commit failed here before. Nothing
     local is ever reset or discarded.
  3. `git merge --ff-only origin/stable`; if requirements.txt changed,
     `pip install -r requirements.txt` in this venv.
  4. Smoke: import the package, then `python -m assistant --check` must
     have no required FAIL.
  5. Any failure: `git reset --keep <old>` (safe: the tree was clean apart
     from config.toml, which --keep preserves), reinstall the old
     requirements if they changed, remember the commit as rejected, and log
     it loudly.
The running assistant is restarted by its supervisor when it sees HEAD move
(after the current voice turn), which then checks /healthz and rolls back
the same way if the new version doesn't come up.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILES = {"config.toml"}  # may differ per machine (as in the preflight)
LOCK_STALE_SECONDS = 1800

# statuses
UP_TO_DATE, UPDATED, SKIPPED, FAILED, ROLLED_BACK, LOCKED = (
    "up-to-date", "updated", "skipped", "failed", "rolled-back", "locked")


@dataclass
class UpdateResult:
    status: str
    detail: str
    old: str | None = None
    new: str | None = None

    @property
    def changed(self) -> bool:
        return self.status == UPDATED

    def line(self) -> str:
        span = f" {short(self.old)} -> {short(self.new)}" if self.old and self.new else ""
        return f"update {self.status}{span}: {self.detail}"


def short(sha: str | None) -> str:
    return (sha or "")[:7]


def _run(cmd: list[str], cwd: Path, timeout: float = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout:.0f} s"
    except OSError as e:
        return -1, f"could not run {cmd[0]}: {e}"


class Updater:
    def __init__(self, root: Path = ROOT, remote: str = "origin",
                 branch: str = "stable", python: str = sys.executable,
                 run: Callable = _run, say: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.remote = remote
        self.branch = branch
        self.python = python
        self.run = run
        self.say = say or (lambda text: log.info(text))
        self.clock = clock
        self.state_file = self.root / "logs" / "update-state.json"
        self.lock_file = self.root / "logs" / "update.lock"

    # --- git ---
    def git(self, *args: str, timeout: float = 300) -> tuple[int, str]:
        return self.run(["git", *args], self.root, timeout)

    def head(self) -> str | None:
        code, out = self.git("rev-parse", "HEAD", timeout=30)
        return out.strip() if code == 0 else None

    def local_changes(self) -> list[str]:
        """Tracked files with local changes, config.toml excepted."""
        code, out = self.git("status", "--porcelain", "--untracked-files=no", timeout=60)
        if code != 0:
            return ["(git status failed)"]
        changed = [line[3:].strip() for line in out.splitlines() if line.strip()]
        return [f for f in changed if f not in LOCAL_FILES]

    # --- state ---
    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, **changes) -> None:
        state = {**self._state(), **changes}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def rejected(self) -> list[str]:
        return list(self._state().get("rejected", []))

    def reject(self, sha: str) -> None:
        self._save_state(rejected=(self.rejected() + [sha])[-20:])

    # --- lock (two supervisors and --update share one checkout) ---
    def acquire(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.clock() - self.lock_file.stat().st_mtime > LOCK_STALE_SECONDS:
                self.lock_file.unlink()
        except OSError:
            pass
        try:
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True

    def release(self) -> None:
        try:
            self.lock_file.unlink()
        except OSError:
            pass

    def locked(self) -> bool:
        return self.lock_file.exists()

    # --- python ---
    def pip_install(self) -> tuple[int, str]:
        return self.run([self.python, "-m", "pip", "install", "-q", "-r",
                         "requirements.txt"], self.root, 1800)

    def smoke(self) -> tuple[bool, str]:
        code, out = self.run([self.python, "-c", "import assistant.cli, assistant.host, "
                              "assistant.updater"], self.root, 300)
        if code != 0:
            return False, "import failed: " + out.strip()[-300:]
        code, out = self.run([self.python, "-m", "assistant", "--check"], self.root, 600)
        fails = [line for line in out.splitlines() if line.startswith("[FAIL]")]
        if code != 0:
            return False, "--check failed: " + ("; ".join(fails) or out.strip()[-300:])
        return True, "import and --check ok"

    # --- the update ---
    def update(self, smoke: bool = True) -> UpdateResult:
        if not self.acquire():
            return UpdateResult(LOCKED, "another update is running")
        try:
            result = self._update(smoke)
        except Exception as e:  # never take the supervisor down
            log.exception("Update failed")
            result = UpdateResult(FAILED, f"{type(e).__name__}: {e}")
        finally:
            self.release()
        self._save_state(last={**asdict(result), "at": self.clock()})
        loud = result.status in (FAILED, ROLLED_BACK)
        self.say(("!!! " if loud else "") + result.line())
        return result

    def _update(self, smoke: bool) -> UpdateResult:
        target_ref = f"{self.remote}/{self.branch}"
        code, out = self.git("fetch", "--quiet", self.remote, self.branch)
        if code != 0:
            return UpdateResult(SKIPPED, f"git fetch {self.remote} {self.branch} failed: "
                                + out.strip()[-200:])
        old = self.head()
        code, target = self.git("rev-parse", target_ref, timeout=30)
        target = target.strip()
        if old is None or code != 0:
            return UpdateResult(FAILED, f"can't read HEAD or {target_ref}")
        if old == target:
            return UpdateResult(UP_TO_DATE, f"at {target_ref}", old, target)
        if self.git("merge-base", "--is-ancestor", old, target, timeout=60)[0] != 0:
            return UpdateResult(SKIPPED, f"HEAD is not an ancestor of {target_ref} "
                                "(diverged or local commits); left as is", old, target)
        if target in self.rejected():
            return UpdateResult(SKIPPED, f"{short(target)} failed here before; "
                                "waiting for a newer stable", old, target)
        changes = self.local_changes()
        if changes:
            return UpdateResult(SKIPPED, "local changes to " + ", ".join(changes[:5])
                                + "; commit or undo them to get updates", old, target)
        code, out = self.git("merge", "--ff-only", "--quiet", target_ref)
        if code != 0:
            return UpdateResult(SKIPPED, "merge --ff-only refused (e.g. your config.toml "
                                "edits touch the same lines): " + out.strip()[-200:],
                                old, target)
        new = self.head()
        _, files = self.git("diff", "--name-only", old, new or target, timeout=60)
        requirements = "requirements.txt" in files.split()
        if requirements:
            code, out = self.pip_install()
            if code != 0:
                return self.rollback(old, new or target, "pip install failed: "
                                     + out.strip()[-300:], requirements)
        if smoke:
            ok, why = self.smoke()
            if not ok:
                return self.rollback(old, new or target, why, requirements)
        return UpdateResult(UPDATED, f"{len(files.split())} file(s) changed"
                            + (", requirements installed" if requirements else ""),
                            old, new)

    def rollback(self, old: str, bad: str, why: str,
                 requirements: bool | None = None) -> UpdateResult:
        """Back to `old` (keeping config.toml edits) and never retry `bad`."""
        if requirements is None:
            _, files = self.git("diff", "--name-only", old, bad, timeout=60)
            requirements = "requirements.txt" in files.split()
        code, out = self.git("reset", "--keep", old)
        self.reject(bad)
        if code != 0:
            return UpdateResult(FAILED, f"{why}; ROLLBACK FAILED ({out.strip()[-200:]}): "
                                f"run `git reset --keep {short(old)}` by hand", old, bad)
        if requirements:
            self.pip_install()
        return UpdateResult(ROLLED_BACK, f"{why}; back on {short(old)}", old, bad)

    def rollback_locked(self, old: str, bad: str, why: str) -> UpdateResult:
        """rollback() for the supervisor: only if HEAD is still `bad`."""
        if not self.acquire():
            return UpdateResult(LOCKED, "another update is running")
        try:
            if self.head() != bad:
                return UpdateResult(SKIPPED, "HEAD moved meanwhile; not rolling back")
            result = self.rollback(old, bad, why)
        finally:
            self.release()
        self.say("!!! " + result.line())
        return result


def main(argv=None, output_fn=print) -> int:
    """python -m assistant --update: one update now."""
    from assistant.config import load_config

    cfg = load_config()["update"]
    up = Updater(branch=cfg["channel"], say=output_fn)
    result = up.update()
    if result.changed:
        output_fn("A running assistant (HeyClaude task or hey-claude service) "
                  "restarts on the new version within a minute, after any turn "
                  "in progress.")
    return 0 if result.status in (UP_TO_DATE, UPDATED) else 1
