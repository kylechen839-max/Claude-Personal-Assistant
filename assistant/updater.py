"""Safe self-update from the `stable` branch: python -m assistant --update.

The host supervisor (assistant/host.py, used by the Windows tasks and the
Pi's systemd unit) runs this every `[update] check_minutes` when `[update]
enabled = true` (best set in the gitignored config.local.toml); `--update`
runs it once, now. `stable` is moved only by the promote-stable workflow,
after master's CI passed on every job.

What has to be true before any new code runs here:
  - `origin` is the expected GitHub repository (`[update] repo`);
  - the target commit, or a `stable-*` tag pointing at it, carries a valid
    SSH signature from a key in `allowed_signers`, a file kept outside the
    checkout (an update can't change who is trusted):
      Windows  %APPDATA%\\HeyClaude\\allowed_signers
      Linux    ~/.config/hey-claude/allowed_signers
    (only `[update] trust_unsigned = true` skips this: then anyone who can
    push to master or stable can run code on this machine);
  - HEAD is on `[update] local_branch` (master) and is an ancestor of
    origin/stable (fast-forward only), there are no local changes to tracked
    files (config.toml excepted), and the commit hasn't failed here before.
Anything else: skip and log. Nothing local is ever reset or discarded.

One update: `git merge --ff-only origin/stable`, smoke (import, then `python
-m assistant --check` with no required FAIL), the go-live check with
`[watch] gate` on (assistant/watch.py: every instance's test conversation on
a side port, while the lock keeps the running apps on the old code), and on
any failure `git reset --keep <old>` and the commit is remembered as
rejected. An update that changes requirements.txt is never applied
automatically (pip would run new, unpinned dependency ranges into the venv
the running assistant uses): it needs `python -m assistant --update` by hand
while the assistant is stopped.

The supervisor restarts the assistant when it sees HEAD move (after the
current voice turn); for a commit this updater applied it then checks
/healthz and rolls back the same way if the new version doesn't come up.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILES = {"config.toml"}  # may differ per machine (as in the preflight)
LOCK_STALE_SECONDS = 3 * 3600  # longer than any fetch + merge + pip + smoke
_STATE_THREADS = threading.Lock()  # one state write at a time in this process
_STATE_HELD = threading.local()    # ... reentrant within a thread
HOST_ALIVE_SECONDS = 15        # a host touches logs/host-<mode>.alive every second
TAG_PATTERN = "stable-*"
SSH_SIG = "-----BEGIN SSH SIGNATURE-----"
OTHER_SIGS = ("-----BEGIN PGP SIGNATURE-----", "-----BEGIN SIGNED MESSAGE-----",
              "-----BEGIN PGP MESSAGE-----")

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


def default_signers_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "HeyClaude" / "allowed_signers"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "hey-claude" / "allowed_signers"


def normalize_remote(url: str) -> str:
    """git@github.com:Owner/Repo.git, https://user:tok@github.com/owner/repo
    -> github.com/owner/repo (lowercase)."""
    url = url.strip()
    m = re.match(r"^[\w.-]+@([\w.-]+):(.+)$", url)  # scp-like ssh
    if m:
        url = f"{m.group(1)}/{m.group(2)}"
    url = re.sub(r"^[a-z+]+://", "", url)          # scheme
    url = re.sub(r"^[^/@]*@", "", url)              # credentials
    url = re.sub(r"\.git/?$", "", url.rstrip("/"))
    return url.lower()


def pid_alive(pid: int) -> bool:
    """Is process `pid` still running? (A lock file's owner: a lock whose
    process was killed is stale at once, not after hours.) True when it
    can't tell. Windows: never os.kill, which would terminate it."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def lock_stale(path: Path, max_age: float, clock=time.time) -> bool:
    """A lock file older than `max_age`, or whose process (the PID in it)
    is gone. A file without a readable PID counts by age only."""
    try:
        age = clock() - path.stat().st_mtime
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if age > max_age:
        return True
    return text.isdigit() and not pid_alive(int(text))


def take_lock(path: Path, max_age: float, clock=time.time) -> bool:
    """Create `path` exclusively with this process's PID, after removing a
    stale one (lock_stale)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if lock_stale(path, max_age, clock):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


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
                 clock: Callable[[], float] = time.time,
                 local_branch: str = "master", repo: str | None = None,
                 trust_unsigned: bool = False, allowed_signers: str | Path | None = None,
                 gate: Callable[[str], tuple[bool | None, str]] | None = None):
        self.root = Path(root)
        self.remote = remote
        self.branch = branch
        self.python = python
        self.run = run
        self.say = say or (lambda text: log.info(text))
        self.clock = clock
        self.local_branch = local_branch
        self.repo = repo
        self.trust_unsigned = trust_unsigned
        self.allowed_signers = Path(allowed_signers) if allowed_signers else default_signers_path()
        self.state_file = self.root / "logs" / "update-state.json"
        self.lock_file = self.root / "logs" / "update.lock"
        # The go-live check (watch.gate_check): the new commit must pass the
        # test conversation on a side port before anything restarts on it.
        self.gate = gate

    @classmethod
    def from_config(cls, cfg: dict, **kw) -> "Updater":
        return cls(branch=cfg["channel"], local_branch=cfg["local_branch"],
                   repo=cfg["repo"] or None, trust_unsigned=cfg["trust_unsigned"],
                   allowed_signers=cfg["allowed_signers"] or None, **kw)

    # --- git ---
    def git(self, *args: str, timeout: float = 300) -> tuple[int, str]:
        return self.run(["git", *args], self.root, timeout)

    def head(self) -> str | None:
        code, out = self.git("rev-parse", "HEAD", timeout=30)
        return out.strip() if code == 0 else None

    def current_branch(self) -> str | None:
        code, out = self.git("symbolic-ref", "--quiet", "--short", "HEAD", timeout=30)
        return out.strip() if code == 0 else None

    def local_changes(self) -> list[str]:
        """Tracked files with local changes, config.toml excepted."""
        code, out = self.git("status", "--porcelain", "--untracked-files=no", timeout=60)
        if code != 0:
            return ["(git status failed)"]
        changed = [line[3:].strip() for line in out.splitlines() if line.strip()]
        return [f for f in changed if f not in LOCAL_FILES]

    def changed_files(self, old: str, new: str) -> list[str]:
        _, files = self.git("diff", "--name-only", old, new, timeout=60)
        return files.split()

    def origin_problem(self) -> str | None:
        if not self.repo:
            return None
        code, url = self.git("remote", "get-url", self.remote, timeout=30)
        if code != 0:
            return f"no remote {self.remote!r}"
        if normalize_remote(url) != normalize_remote(self.repo):
            return (f"{self.remote} is {normalize_remote(url)}, not "
                    f"{normalize_remote(self.repo)}")
        return None

    def signature_problem(self, target: str) -> str | None:
        """None if `target` (or a stable-* tag on it) is signed by a key in
        allowed_signers, else why not."""
        if self.trust_unsigned:
            return None
        if not self.allowed_signers.is_file():
            return (f"updates must be signed and {self.allowed_signers} doesn't exist: "
                    "add your signing key there (README, Automatic updates)")
        # SSH signatures only: git picks the verifier from the signature's
        # type, so an OpenPGP/X.509 signature would be checked against a
        # keyring instead of allowed_signers. Such objects are refused before
        # verifying, and those verifiers are disabled outright. ssh-keygen
        # (git's default) is pinned so a user setting can't swap it.
        trust = ["-c", "gpg.format=ssh", "-c", "gpg.ssh.program=ssh-keygen",
                 "-c", f"gpg.ssh.allowedSignersFile={self.allowed_signers}",
                 "-c", "gpg.openpgp.program=hey-claude-no-openpgp",
                 "-c", "gpg.x509.program=hey-claude-no-x509"]
        if self.ssh_signed("commit", target) and \
                self.git(*trust, "verify-commit", target, timeout=60)[0] == 0:
            return None
        _, tags = self.git("tag", "--list", TAG_PATTERN, "--points-at", target, timeout=30)
        for tag in tags.split():
            if self.ssh_signed("tag", tag) and \
                    self.git(*trust, "verify-tag", tag, timeout=60)[0] == 0:
                return None
        return f"{short(target)} has no valid signature from a key in {self.allowed_signers}"

    def ssh_signed(self, kind: str, ref: str) -> bool:
        """Does this commit/tag object carry an SSH signature (and no other)?"""
        code, raw = self.git("cat-file", kind, ref, timeout=30)
        if code != 0:
            return False
        return SSH_SIG in raw and not any(m in raw for m in OTHER_SIGS)

    # --- state ---
    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @contextmanager
    def _state_lock(self):
        """Read-modify-write of the state file, one at a time: the two hosts,
        the watchdog and --update all write it (a lost write could forget a
        rejected commit or the last known good). Reentrant in a thread; a
        file lock across processes (whose owner may have been killed)."""
        depth = getattr(_STATE_HELD, "depth", 0)
        if depth:
            _STATE_HELD.depth = depth + 1
            try:
                yield
            finally:
                _STATE_HELD.depth -= 1
            return
        lock = self.state_file.with_name(self.state_file.name + ".lock")
        with _STATE_THREADS:
            deadline = time.monotonic() + 10
            held = take_lock(lock, 60)
            while not held and time.monotonic() < deadline:
                time.sleep(0.02)
                held = take_lock(lock, 60)
            if not held:
                log.warning("Update state is locked by another process; writing anyway")
            _STATE_HELD.depth = 1
            try:
                yield
            finally:
                _STATE_HELD.depth = 0
                if held:
                    try:
                        lock.unlink()
                    except OSError:
                        pass

    def _save_state(self, **changes) -> None:
        from assistant.tools.google_auth import write_private

        with self._state_lock():
            state = {**self._state(), **changes}
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            # atomic: the hosts, the watchdog and --update all read it
            write_private(self.state_file, json.dumps(state, indent=2))

    def rejected(self) -> list[str]:
        return list(self._state().get("rejected", []))

    def reject(self, sha: str) -> None:
        with self._state_lock():
            if sha not in self.rejected():
                self._save_state(rejected=(self.rejected() + [sha])[-20:])

    def applied(self) -> str | None:
        """The commit this updater last moved HEAD to (None after rollback)."""
        return self._state().get("applied")

    def gated(self, sha: str | None) -> bool | None:
        """Did `sha` pass the go-live check (True), fail it (False), or
        hasn't it been checked (None)?"""
        if not sha:
            return None
        if sha in self.rejected():
            return False
        g = self._state().get("gated") or {}
        return bool(g.get("ok")) if g.get("sha") == sha else None

    def mark_gated(self, sha: str, ok: bool) -> None:
        with self._state_lock():
            self._save_state(gated={"sha": sha, "ok": bool(ok), "at": self.clock()})
            if not ok:
                self.reject(sha)

    def last_good(self) -> str | None:
        """The last commit whose test conversation passed on this machine."""
        return self._state().get("last_good")

    def set_last_good(self, sha: str) -> None:
        with self._state_lock():
            if sha and sha != self.last_good():
                self._save_state(last_good=sha)

    # --- lock (two supervisors and --update share one checkout) ---
    def acquire(self) -> bool:
        return take_lock(self.lock_file, LOCK_STALE_SECONDS, self.clock)

    def release(self) -> None:
        try:
            self.lock_file.unlink()
        except OSError:
            pass

    def locked(self) -> bool:
        return self.lock_file.exists() and not lock_stale(
            self.lock_file, LOCK_STALE_SECONDS, self.clock)

    def hosts_running(self) -> list[str]:
        """Modes whose host supervisor touched its heartbeat file just now."""
        now = self.clock()
        alive = []
        for f in (self.root / "logs").glob("host-*.alive"):
            try:
                if now - f.stat().st_mtime < HOST_ALIVE_SECONDS:
                    alive.append(f.name[len("host-"):-len(".alive")])
            except OSError:
                pass
        return sorted(alive)

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
    def update(self, smoke: bool = True, allow_requirements: bool = False) -> UpdateResult:
        if not self.acquire():
            return UpdateResult(LOCKED, "another update is running")
        try:
            result = self._update(smoke, allow_requirements)
        except Exception as e:  # never take the supervisor down
            log.exception("Update failed")
            result = UpdateResult(FAILED, f"{type(e).__name__}: {e}")
        finally:
            self.release()
        self._save_state(last={**asdict(result), "at": self.clock()})
        loud = result.status in (FAILED, ROLLED_BACK)
        self.say(("!!! " if loud else "") + result.line())
        return result

    def _update(self, smoke: bool, allow_requirements: bool) -> UpdateResult:
        target_ref = f"{self.remote}/{self.branch}"
        on = self.current_branch()
        if on != self.local_branch:
            return UpdateResult(SKIPPED, f"checked out {on or 'a detached HEAD'}, not "
                                f"{self.local_branch}; only {self.local_branch} is updated")
        problem = self.origin_problem()
        if problem:
            return UpdateResult(SKIPPED, f"not updating: {problem}")
        code, out = self.git("fetch", "--quiet", "--tags", self.remote, self.branch)
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
        problem = self.signature_problem(target)
        if problem:
            return UpdateResult(SKIPPED, f"not updating: {problem}", old, target)
        changes = self.local_changes()
        if changes:
            return UpdateResult(SKIPPED, "local changes to " + ", ".join(changes[:5])
                                + "; commit or undo them (per-machine settings belong "
                                "in config.local.toml)", old, target)
        files = self.changed_files(old, target)
        requirements = "requirements.txt" in files
        if requirements and not allow_requirements:
            return UpdateResult(SKIPPED, "this update changes requirements.txt: stop the "
                                "assistant, then run `python -m assistant --update`",
                                old, target)
        if requirements and self.hosts_running():
            return UpdateResult(SKIPPED, "this update changes requirements.txt and the "
                                f"assistant is running ({', '.join(self.hosts_running())}); "
                                "stop it first (Task Scheduler -> HeyClaude, or "
                                "systemctl --user stop hey-claude)", old, target)
        code, out = self.git("merge", "--ff-only", "--quiet", target_ref)
        if code != 0:
            return UpdateResult(SKIPPED, "merge --ff-only refused (it would overwrite "
                                "your local config.toml edits; move them to "
                                "config.local.toml): " + out.strip()[-200:], old, target)
        new = self.head() or target
        self._save_state(applied=new)
        if requirements:
            code, out = self.pip_install()
            if code != 0:
                return self.rollback(old, new, "pip install failed: " + out.strip()[-300:],
                                     requirements)
        if smoke:
            ok, why = self.smoke()
            if not ok:
                return self.rollback(old, new, why, requirements)
        if self.gate is not None:  # nothing restarts on it while we hold the lock
            ok, why = self.gate(new)
            if ok is None:  # Claude or the internet down: no verdict on `new`
                return self.rollback(old, new, f"go-live check couldn't run ({why}); "
                                     "trying again next time", requirements,
                                     reject=False)
            self.mark_gated(new, ok)
            if not ok:
                return self.rollback(old, new, f"go-live check failed: {why}",
                                     requirements)
        return UpdateResult(UPDATED, f"{len(files)} file(s) changed"
                            + (", requirements installed" if requirements else ""),
                            old, new)

    def rollback(self, old: str, bad: str, why: str,
                 requirements: bool | None = None, reject: bool = True) -> UpdateResult:
        """Back to `old` (keeping config.toml edits) and never retry `bad`
        (with `reject`; else it is tried again at the next check)."""
        if requirements is None:
            requirements = "requirements.txt" in self.changed_files(old, bad)
        code, out = self.git("reset", "--keep", old)
        if code != 0:  # still on `bad`: not rejected, it may come back by hand
            return UpdateResult(FAILED, f"{why}; ROLLBACK FAILED ({out.strip()[-200:]}): "
                                f"run `git reset --keep {short(old)}` by hand", old, bad)
        if reject:
            self.reject(bad)
        self._save_state(applied=None)
        if requirements:
            code, out = self.pip_install()
            if code != 0:
                return UpdateResult(FAILED, f"{why}; back on {short(old)} but reinstalling "
                                    f"its requirements failed ({out.strip()[-200:]}): run "
                                    "`pip install -r requirements.txt` by hand", old, bad)
        return UpdateResult(ROLLED_BACK, f"{why}; back on {short(old)}", old, bad)

    def rollback_locked(self, old: str, bad: str, why: str) -> UpdateResult:
        """rollback() for the supervisor: only for a commit this updater
        applied, still checked out on the updated branch."""
        if not self.acquire():
            return UpdateResult(LOCKED, "another update is running")
        try:
            if self.head() != bad or self.applied() != bad \
                    or self.current_branch() != self.local_branch:
                return UpdateResult(SKIPPED, "HEAD moved meanwhile, or not an update "
                                    "made here; not rolling back")
            result = self.rollback(old, bad, why)
        finally:
            self.release()
        self.say("!!! " + result.line())
        return result


def main(argv=None, output_fn=print) -> int:
    """python -m assistant --update: one update now (requirements included)."""
    from assistant.config import load_config
    from assistant.watch import updater_gate

    up = Updater.from_config(load_config()["update"], say=output_fn, gate=updater_gate())
    result = up.update(allow_requirements=True)
    if result.changed:
        output_fn("A running assistant (HeyClaude task or hey-claude service) "
                  "restarts on the new version within a minute, after any turn "
                  "in progress.")
    return 0 if result.status in (UP_TO_DATE, UPDATED) else 1
