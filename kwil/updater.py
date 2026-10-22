"""Safe self-update from the `stable` branch: python -m kwil --update.

The host supervisor (kwil/host.py, used by the Windows tasks and the
Pi's systemd unit) runs this every `[update] check_minutes` when `[update]
enabled = true` (best set in the gitignored config.local.toml); `--update`
runs it once, now. `stable` is moved only by the promote-stable workflow,
after master's CI passed on every job.

What has to be true before any new code runs here:
  - `origin` is the expected GitHub repository (`[update] repo`);
  - the target commit, or a `stable-*` tag pointing at it, carries a valid
    SSH signature from a key in `allowed_signers`, a file kept outside the
    checkout (an update can't change who is trusted):
      Windows  %APPDATA%\\Kwil\\allowed_signers
      Linux    ~/.config/kwil/allowed_signers
    (or the same file in the pre-rebrand folders, HeyClaude and hey-claude,
    while the new folder has none)
    (only `[update] trust_unsigned = true` skips this: then anyone who can
    push to master or stable can run code on this machine);
  - HEAD is on `[update] local_branch` (master) and is an ancestor of
    origin/stable (fast-forward only), there are no local changes to tracked
    files (config.toml excepted), and the commit hasn't failed here before.
Anything else: skip and log. Nothing local is ever reset or discarded.

One update: `git merge --ff-only origin/stable`, smoke (import, then `python
-m kwil --check` with no required FAIL), the go-live check with
`[watch] gate` on (kwil/watch.py: every instance's test conversation on
a side port, while the lock keeps the running apps on the old code), and on
any failure `git reset --keep <old>` and the commit is remembered as
rejected. An update that changes requirements.txt is never applied
automatically (pip would run new, unpinned dependency ranges into the venv
the running assistant uses): it needs `python -m kwil --update` by hand
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
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from kwil.version import package

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
# A go-live check that couldn't tell (busy side port, Claude down) is tried
# again 1, 5, 15, then every 60 minutes after the last try, by the hosts and
# the watchdog alike (the watchdog alerts if it lasts).
GATE_BACKOFF = (60, 300, 900, 3600)


def gate_backoff(count: int) -> int:
    """Seconds to wait after the `count`th inconclusive go-live check in a row."""
    return GATE_BACKOFF[min(max(int(count), 1), len(GATE_BACKOFF)) - 1]


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


# "name>=1.2" (extras and spaces allowed); other pins are left to pip
_MINIMUM = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*>=\s*(\d+(?:\.\d+)*)")
# Updater.smoke()'s reasons start with one of these: an import failure is the
# code's; a --check failure may be this machine's (no mic, no API key)
SMOKE_IMPORT_FAILED, SMOKE_CHECK_FAILED = "import failed", "--check failed"
INSTALL_HINT = ("stop the apps, then run `python -m kwil --update` or "
                "`pip install -r requirements.txt`")


def _release(version: str) -> tuple[int, ...] | None:
    """(1, 24) for "1.24.0", "1.24rc1" or "1.24.post2"; None if unreadable."""
    m = re.match(r"\d+(?:\.\d+)*", version.strip())
    if not m:
        return None
    parts = [int(p) for p in m.group().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _marker_applies(marker: str) -> bool | None:
    """Does an environment marker ("python_version >= '3.11'") hold here?
    None when it can't be read (no `packaging`): the line is skipped."""
    try:
        from packaging.markers import Marker

        return Marker(marker).evaluate()
    except Exception:  # noqa: BLE001 - no packaging, or a marker it can't parse
        return None


def requirements_problem(root: Path = ROOT, version=None) -> str | None:
    """Is each `name>=x.y` in requirements.txt installed here at x.y or
    newer (importlib.metadata)? None if so, or with no requirements.txt;
    else what's missing and what to run. A pull by hand (or deploy
    --no-pull) can bring a requirements bump the venv never got; the new
    code may import fine and then fail on the missing feature. Lines whose
    environment marker doesn't apply here are skipped, and so are other
    pins (==, none): pip handles those."""
    from importlib import metadata

    path = Path(root) / "requirements.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    version = version or metadata.version
    missing = []
    for line in lines:
        spec, _, marker = line.split("#", 1)[0].partition(";")
        m = _MINIMUM.match(spec.strip())
        if not m or (marker.strip() and not _marker_applies(marker.strip())):
            continue
        name, wanted = m.group(1), m.group(2)
        try:
            have = version(name)
        except metadata.PackageNotFoundError:
            missing.append(f"{name} not installed (needs >={wanted})")
            continue
        except Exception as e:  # noqa: BLE001 - broken metadata: pip's problem, not ours
            log.debug("Can't read the installed version of %s: %s", name, type(e).__name__)
            continue
        installed = _release(str(have or ""))
        if installed is not None and installed < _release(wanted):
            missing.append(f"{name} {have} (needs >={wanted})")
    if not missing:
        return None
    more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
    return ("requirements.txt isn't installed: " + ", ".join(missing[:3]) + more
            + "; " + INSTALL_HINT)


def default_signers_path() -> Path:
    """The per-user allowed_signers: in the Kwil folder, or in the folder of
    the old name (HeyClaude / hey-claude) while only that one has the file,
    so a machine set up before the rebrand keeps updating."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        new, old = base / "Kwil", base / "HeyClaude"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        new, old = base / "kwil", base / "hey-claude"
    if not (new / "allowed_signers").is_file() and (old / "allowed_signers").is_file():
        return old / "allowed_signers"
    return new / "allowed_signers"


# The GitHub repository was renamed in the Kwil rebrand; for a release either
# name is the expected origin (a clone keeps its old URL, which GitHub
# redirects), so `[update] repo` may name either one.
REPO_ALIASES = ({"github.com/kylechen839-max/claude-personal-assistant",
                 "github.com/kylechen839-max/kwil"},)


def same_repo(a: str, b: str) -> bool:
    """Are remotes `a` and `b` the same repository (or its renamed self)?"""
    a, b = normalize_remote(a), normalize_remote(b)
    return a == b or any(a in names and b in names for names in REPO_ALIASES)


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


def _lock_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def clear_stale(path: Path, seen: str | None) -> None:
    """Remove the stale lock `path` that held `seen`, without ever removing
    a fresh one: two processes may both have judged it stale, and the first
    may already have taken the lock. The file is renamed aside atomically
    (only one rename of it can succeed); if what was moved isn't the stale
    lock that was seen, it is put back (os.link: never over a newer one)."""
    aside = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.stale")
    try:
        os.replace(path, aside)
    except OSError:
        return  # already gone: someone else cleared it
    try:
        if _lock_text(aside) != seen:  # a fresh lock: give it back
            try:
                os.link(aside, path)
            except OSError:
                pass
    finally:
        try:
            aside.unlink()
        except OSError:
            pass


def take_lock(path: Path, max_age: float, clock=time.time) -> bool:
    """Create `path` exclusively with this process's PID, after clearing a
    stale one (lock_stale, clear_stale)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = _lock_text(path)
    if lock_stale(path, max_age, clock):
        clear_stale(path, seen)
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
        if not same_repo(url, self.repo):
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
                 "-c", "gpg.openpgp.program=kwil-no-openpgp",
                 "-c", "gpg.x509.program=kwil-no-x509"]
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
        from kwil.tools.google_auth import write_private

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
            self._save_state(gated={"sha": sha, "ok": bool(ok), "at": self.clock()},
                             inconclusive=None)
            if not ok:
                self.reject(sha)

    def note_inconclusive(self, sha: str, why: str) -> dict:
        """The go-live check of `sha` couldn't tell (a busy side port, Claude
        down): count it, so the hosts back off and the watchdog alerts once
        it keeps happening (watch.Watch.record). Returns the record."""
        with self._state_lock():
            rec = self._state().get("inconclusive") or {}
            if rec.get("sha") != sha:
                rec = {"sha": sha, "count": 0, "first": self.clock(), "alerted": False}
            rec.update(count=int(rec["count"]) + 1, last=self.clock(), why=why[:300])
            self._save_state(inconclusive=rec)
            return rec

    def inconclusive(self, sha: str | None) -> dict | None:
        rec = self.last_inconclusive()
        return rec if rec and sha and rec.get("sha") == sha else None

    def last_inconclusive(self) -> dict | None:
        """The stored record, whatever its commit (the updater notes one on
        the commit it then rolls back from)."""
        return self._state().get("inconclusive") or None

    def gate_wait(self, sha: str | None) -> float:
        """Seconds until the go-live check of `sha` is due again after an
        inconclusive one (gate_backoff); 0 = now."""
        rec = self.inconclusive(sha)
        if not rec:
            return 0.0
        due = float(rec.get("last") or 0) + gate_backoff(rec.get("count") or 1)
        return max(0.0, due - self.clock())

    def mark_inconclusive_alerted(self, sha: str) -> None:
        with self._state_lock():
            rec = self.inconclusive(sha)
            if rec:
                self._save_state(inconclusive={**rec, "alerted": True})

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
        pkg = package(self.root)
        code, out = self.run([self.python, "-c", f"import {pkg}.cli, {pkg}.host, "
                              f"{pkg}.updater"], self.root, 300)
        if code != 0:
            return False, f"{SMOKE_IMPORT_FAILED}: " + out.strip()[-300:]
        code, out = self.run([self.python, "-m", pkg, "--check"], self.root, 600)
        fails = [line for line in out.splitlines() if line.startswith("[FAIL]")]
        if code != 0:
            return False, f"{SMOKE_CHECK_FAILED}: " + ("; ".join(fails)
                                                       or out.strip()[-300:])
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
                                "assistant, then run `python -m kwil --update`",
                                old, target)
        if requirements and self.hosts_running():
            return UpdateResult(SKIPPED, "this update changes requirements.txt and the "
                                f"assistant is running ({', '.join(self.hosts_running())}); "
                                "stop it first (Task Scheduler -> Kwil or HeyClaude, or "
                                "systemctl --user stop kwil)", old, target)
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
                # A failed --check (no mic, no audio device, no key) says
                # nothing about the code: undo it now but try it again at
                # the next check. Only a failed import rejects it for good.
                return self.rollback(old, new, why, requirements,
                                     reject=not why.startswith(SMOKE_CHECK_FAILED))
        if self.gate is not None:  # nothing restarts on it while we hold the lock
            ok, why = self.gate(new)
            if ok is None:  # Claude or the internet down: no verdict on `new`
                self.note_inconclusive(new, why)
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
            # but no longer "applied": the hosts must not restart on it as an
            # update of theirs (restart_on); a hand restart is gated
            self._save_state(applied=None)
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
    """python -m kwil --update: one update now (requirements included)."""
    from kwil.config import load_config
    from kwil.watch import updater_gate

    up = Updater.from_config(load_config()["update"], say=output_fn, gate=updater_gate())
    result = up.update(allow_requirements=True)
    if result.changed:
        output_fn("A running assistant (Kwil task or kwil service) "
                  "restarts on the new version within a minute, after any turn "
                  "in progress.")
    return 0 if result.status in (UP_TO_DATE, UPDATED) else 1
