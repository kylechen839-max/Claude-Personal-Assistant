"""Auto-update (assistant/updater.py) against real git repos with a bare
"remote", and the host supervisor (assistant/host.py) with fakes."""

import io
import subprocess
from pathlib import Path

import pytest

from assistant import host, updater
from tests.test_webapp import make_app  # noqa: F401 (fixture)
from assistant.updater import (LOCKED, ROLLED_BACK, SKIPPED, UP_TO_DATE, UPDATED,
                               Updater)


def git(cwd, *args):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


def commit(repo, name, text, message=None):
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message or f"edit {name}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """(dev clone that pushes `stable`, the home host's clone)."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    dev = tmp_path / "dev"
    git(tmp_path, "init", "-q", "-b", "master", str(dev))
    for repo in (dev,):
        git(repo, "config", "user.email", "ci@example.com")
        git(repo, "config", "user.name", "CI")
    commit(dev, "requirements.txt", "anthropic\n")
    commit(dev, "config.toml", '[update]\nenabled = true\n')
    commit(dev, "app.py", "v = 1\n")
    git(dev, "remote", "add", "origin", str(remote))
    git(dev, "push", "-q", "origin", "master:stable", "master")
    home = tmp_path / "home"
    git(tmp_path, "clone", "-q", "-b", "master", str(remote), str(home))
    git(home, "config", "user.email", "kyle@example.com")
    git(home, "config", "user.name", "Kyle")
    return dev, home


class Recorder:
    """Real git, fake python (pip and the smoke run)."""

    def __init__(self, smoke_ok=True, pip_ok=True):
        self.python_calls = []
        self.smoke_ok, self.pip_ok = smoke_ok, pip_ok

    def __call__(self, cmd, cwd, timeout=0):
        if cmd[0] == "git":
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        self.python_calls.append(cmd[1:])
        if cmd[1:3] == ["-m", "pip"]:
            return (0, "") if self.pip_ok else (1, "ERROR: no matching distribution")
        if "--check" in cmd:
            return (0, "All required checks passed.") if self.smoke_ok else \
                (1, "[FAIL] ANTHROPIC_API_KEY: not set")
        return 0, ""


def make(home, rec=None, said=None):
    rec = rec or Recorder()
    return Updater(root=home, python="python", run=rec,
                   say=(said.append if said is not None else lambda s: None)), rec


def publish(dev, name="app.py", text="v = 2\n"):
    sha = commit(dev, name, text)
    git(dev, "push", "-q", "origin", "master:stable")
    return sha


def test_up_to_date(repos):
    _, home = repos
    up, rec = make(home)
    assert up.update().status == UP_TO_DATE and rec.python_calls == []


def test_fast_forward_and_smoke(repos):
    dev, home = repos
    new = publish(dev)
    said = []
    up, rec = make(home, said=said)
    r = up.update()
    assert r.status == UPDATED and git(home, "rev-parse", "HEAD") == new
    assert (home / "app.py").read_text() == "v = 2\n"
    assert ["-m", "assistant", "--check"] in rec.python_calls  # smoke ran
    assert not any(c[:2] == ["-m", "pip"] for c in rec.python_calls)  # no reqs change
    assert said and said[0].startswith("update updated")
    assert not up.lock_file.exists()


def test_requirements_change_runs_pip(repos):
    dev, home = repos
    publish(dev, "requirements.txt", "anthropic\nnew-package\n")
    up, rec = make(home)
    r = up.update()
    assert r.status == UPDATED and "requirements installed" in r.detail
    assert ["-m", "pip", "install", "-q", "-r", "requirements.txt"] in rec.python_calls


def test_only_the_stable_branch_is_followed(repos):
    dev, home = repos
    commit(dev, "app.py", "v = untested\n")
    git(dev, "push", "-q", "origin", "master")  # master moved, stable didn't
    up, _ = make(home)
    assert up.update().status == UP_TO_DATE
    assert (home / "app.py").read_text() == "v = 1\n"


def test_diverged_is_skipped_and_left_alone(repos):
    dev, home = repos
    mine = commit(home, "notes.txt", "my local commit\n")
    publish(dev)
    up, rec = make(home)
    r = up.update()
    assert r.status == SKIPPED and "not an ancestor" in r.detail
    assert git(home, "rev-parse", "HEAD") == mine and rec.python_calls == []


def test_dirty_tree_is_skipped_and_kept(repos):
    dev, home = repos
    publish(dev)
    (home / "app.py").write_text("v = my experiment\n")
    up, _ = make(home)
    r = up.update()
    assert r.status == SKIPPED and "app.py" in r.detail
    assert (home / "app.py").read_text() == "v = my experiment\n"


def test_local_config_edits_dont_block_and_survive(repos):
    dev, home = repos
    new = publish(dev)
    (home / "config.toml").write_text('[update]\nenabled = true\n[wake]\nmic_device = 0\n')
    up, _ = make(home)
    assert up.update().status == UPDATED and git(home, "rev-parse", "HEAD") == new
    assert "mic_device = 0" in (home / "config.toml").read_text()


def test_config_conflict_is_skipped_safely(repos):
    dev, home = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, "config.toml", '[update]\nenabled = true\ncheck_minutes = 5\n')
    (home / "config.toml").write_text('[update]\nenabled = true\n# mine\n')
    up, _ = make(home)
    r = up.update()
    assert r.status == SKIPPED and "ff-only refused" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    assert "# mine" in (home / "config.toml").read_text()


def test_failed_smoke_rolls_back_and_rejects(repos):
    dev, home = repos
    old = git(home, "rev-parse", "HEAD")
    bad = publish(dev, "requirements.txt", "anthropic\nbroken\n")
    (home / "config.toml").write_text('[update]\nenabled = true\n# local\n')
    said = []
    up, rec = make(home, Recorder(smoke_ok=False), said)
    r = up.update()
    assert r.status == ROLLED_BACK and "ANTHROPIC_API_KEY" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    assert (home / "requirements.txt").read_text() == "anthropic\n"
    assert "# local" in (home / "config.toml").read_text()  # --keep kept it
    # pip ran for the update and again to put the old requirements back
    assert sum(c[:2] == ["-m", "pip"] for c in rec.python_calls) == 2
    assert said[-1].startswith("!!! update rolled-back")
    # the bad commit is not retried; a newer stable is
    assert up.update().status == SKIPPED
    newer = publish(dev, "app.py", "v = 3\n")
    up.run = Recorder()
    assert up.update().status == UPDATED and git(home, "rev-parse", "HEAD") == newer


def test_failed_pip_rolls_back(repos):
    dev, home = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, "requirements.txt", "anthropic\nnope\n")
    up, _ = make(home, Recorder(pip_ok=False))
    r = up.update()
    assert r.status == ROLLED_BACK and "pip install failed" in r.detail
    assert git(home, "rev-parse", "HEAD") == old


def test_no_stable_branch_is_skipped(repos, tmp_path):
    _, home = repos
    up = Updater(root=home, branch="does-not-exist", run=Recorder())
    assert up.update().status == SKIPPED


def test_one_update_at_a_time(repos):
    dev, home = repos
    publish(dev)
    up, _ = make(home)
    assert up.acquire()
    assert up.update().status == LOCKED
    up.release()
    assert up.update().status == UPDATED


def test_rollback_locked_only_if_head_is_still_bad(repos):
    dev, home = repos
    old = git(home, "rev-parse", "HEAD")
    new = publish(dev)
    up, _ = make(home)
    assert up.update().status == UPDATED
    assert up.rollback_locked(old, "f" * 40, "x").status == SKIPPED  # HEAD isn't that
    r = up.rollback_locked(old, new, "web did not come up")
    assert r.status == ROLLED_BACK and git(home, "rev-parse", "HEAD") == old
    assert new in up.rejected()


def test_cli_update_flag(monkeypatch):
    from assistant import cli

    called = []
    monkeypatch.setattr(updater, "main", lambda: called.append(1) or 0)
    assert cli.main(["--update", "--config", "nope.toml"]) == 0 and called


# ---------- the host supervisor ----------

class FakeChild:
    def __init__(self, codes=None):
        self.codes = list(codes or [])  # poll() results, then None (running)
        self.terminated = False

    def poll(self):
        if self.terminated:
            return -15
        return self.codes.pop(0) if self.codes else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return -15


class FakeUpdater:
    def __init__(self, heads):
        self.heads = list(heads)
        self.rollbacks, self.updates = [], 0

    def head(self):
        return self.heads[0] if len(self.heads) == 1 else self.heads.pop(0)

    def locked(self):
        return False

    def update(self):
        self.updates += 1

    def rollback_locked(self, old, bad, why):
        self.rollbacks.append((old, bad))
        self.heads = [old]
        return updater.UpdateResult(ROLLED_BACK, why, old, bad)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make_host(mode="web", heads=("a" * 40,), children=None, health=None,
              settings=None):
    clock = Clock()
    started = []
    children = list(children or [FakeChild() for _ in range(5)])

    def popen(cmd):
        started.append(cmd)
        return children.pop(0)

    h = host.Host(mode, ["py", "-m", "assistant", "--mode", mode], io.StringIO(),
                  updater=FakeUpdater(heads), popen=popen,
                  health=health or (lambda port: {"ok": True, "version": "a" * 7}),
                  settings=settings or (lambda: {"enabled": True, "check_minutes": 15}),
                  sleep=clock.sleep, clock=clock)
    return h, started, clock


def test_host_restarts_after_crash_and_stops_on_clean_exit():
    h, started, clock = make_host(children=[FakeChild([1]), FakeChild([1]), FakeChild([0])])
    assert h.loop(max_ticks=10) == 0
    assert len(started) == 3 and "exited cleanly" in h.log.getvalue()


def test_host_backs_off_after_a_burst_of_crashes():
    h, _, clock = make_host(children=[FakeChild([3]) for _ in range(host.BURST + 1)])
    slept = []
    h.sleep = lambda s: (slept.append(s), clock.sleep(s))
    h.loop(max_ticks=host.BURST)
    assert [s for s in slept if s > 1] == [host.RESTART_DELAY] * (host.BURST - 1) \
        + [host.BURST_PAUSE]


def test_host_checks_for_updates_only_when_enabled():
    on, _, clock = make_host()
    state = {"next_update": 0, "next_head": 1e9}
    on.start()
    on.tick(0, state)
    assert on.updater.updates == 1 and state["next_update"] == 15 * 60
    off, _, _ = make_host(settings=lambda: {"enabled": False, "check_minutes": 15})
    off.start()
    off.tick(0, {"next_update": 0, "next_head": 1e9})
    assert off.updater.updates == 0


def test_head_change_restarts_web_when_idle_on_the_new_version():
    old, new = "a" * 40, "b" * 40
    busy = iter([{"ok": True, "busy": True}, {"ok": True, "busy": True},
                 {"ok": True, "busy": False}])
    checks = []

    def health(port):
        checks.append(port)
        try:
            return next(busy)
        except StopIteration:
            return {"ok": True, "version": new[:7]}

    h, started, clock = make_host(heads=[old, new, new], health=health)
    h.start()
    assert h.child_sha == old
    h.tick(100, {"next_update": 1e9, "next_head": 0})
    assert len(started) == 2 and h.child_sha == new
    assert clock.t >= 4  # waited for the two busy answers
    assert h.updater.rollbacks == [] and f"running {new[:7]}" in h.log.getvalue()


def test_new_version_that_wont_come_up_is_rolled_back():
    old, new = "a" * 40, "b" * 40
    h, started, clock = make_host(heads=[old, new, new],
                                  health=lambda port: {"ok": True, "version": old[:7]})
    h.start()
    h.restart_on(new)
    assert h.updater.rollbacks == [(old, new)]
    assert len(started) == 3  # old, new (unhealthy), old again
    assert "!!!" in h.log.getvalue()


def test_wake_child_must_stay_up_after_an_update():
    old, new = "a" * 40, "b" * 40
    dies = FakeChild()
    dies.codes = [1]
    h, started, _ = make_host("wake", heads=[old, new, new],
                              children=[FakeChild(), dies, FakeChild()])
    h.start()
    h.restart_on(new)
    assert h.updater.rollbacks == [(old, new)] and len(started) == 3


def test_host_waits_for_the_network():
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


def test_host_command_line(tmp_path):
    exe = tmp_path / "pythonw.exe"
    exe.write_text("")
    (tmp_path / "python.exe").write_text("")
    assert host.console_python(str(exe)) == str(tmp_path / "python.exe")
    assert host.console_python("/usr/bin/python3") == "/usr/bin/python3"
    assert host.log_path("wake", tmp_path) == tmp_path / "logs" / "host-wake.log"
    assert host.parse(["web"]) == ("web", "8765")  # what tailscale serve points at
    assert host.parse(["web", "9000"]) == ("web", "9000")
    assert host.parse(["wake", "8765"]) is None and host.parse(["web", "x"]) is None
    assert host.main(["nope"]) == 2
    assert host.build_command("web")[1:] == ["-m", "assistant", "--mode", "web",
                                             "--host", "127.0.0.1", "--port", "8765"]
    assert host.build_command("wake")[1:] == ["-m", "assistant", "--mode", "wake"]


def test_version_from_env_or_git(monkeypatch):
    from assistant import version as v

    v.version.cache_clear()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef")
    assert v.version() == "0123456"
    v.version.cache_clear()
    monkeypatch.delenv("RENDER_GIT_COMMIT")
    monkeypatch.delenv("HEY_CLAUDE_VERSION", raising=False)
    got = v.version()
    assert got == "unknown" or len(got) == 7
    v.version.cache_clear()


# ---------- /healthz: version, and busy for the local supervisor only ----------

def test_healthz_version_and_local_busy(make_app):
    import threading

    from starlette.testclient import TestClient

    from assistant.version import version
    from tests.test_webapp import BASE

    assistant, remote = make_app()
    app = remote.app
    assert remote.get("/healthz").json() == {"ok": True, "version": version()}
    local = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))
    assert local.get("/healthz").json()["busy"] is False
    # a turn in progress
    s = assistant.session("sid1")
    s.busy.acquire()
    assert local.get("/healthz").json()["busy"] is True
    s.busy.release()
    # a voice turn in progress
    conv = type("Conv", (), {})()
    conv.lock = threading.Lock()
    conv.current = type("Turn", (), {"finished": False})()
    conv.pending = None
    app.state.voice_conversations["sid2"] = (conv, None)
    assert local.get("/healthz").json()["busy"] is True
    # relayed by a proxy on this machine (tailscale serve): not told
    proxied = local.get("/healthz", headers={"X-Forwarded-For": "100.64.0.9"})
    assert "busy" not in proxied.json()
    # and never from elsewhere
    assert "busy" not in remote.get("/healthz").json()


def test_page_reloads_once_and_only_when_quiet(tmp_path):
    """update-watch.js in node with a fake page: the first /healthz sets the
    version; a new one reloads only when quiet, once per version."""
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    script = Path(host.ROOT / "assistant" / "web" / "update-watch.js").read_text()
    harness = tmp_path / "harness.js"
    harness.write_text(r"""
const vm = require("vm");
const results = [];
function run(versions, quiet) {
  let reloads = 0, i = 0, timer = null, listener = null;
  const store = {};
  const ctx = {
    busy: !quiet.idle, confirming: false, recorder: null,
    document: {
      visibilityState: "visible",
      getElementById: () => ({ hidden: !quiet.voiceOpen ? true : false }),
      querySelectorAll: () => quiet.draft ? [{ value: "half a question" }] : [],
      addEventListener: (_, fn) => { listener = fn; },
    },
    fetch: async () => ({ json: async () => ({ ok: true, version: versions[Math.min(i++, versions.length - 1)] }) }),
    setInterval: (fn) => { timer = fn; },
    sessionStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    location: { reload: () => { reloads++; } },
  };
  vm.createContext(ctx);
  vm.runInContext(SCRIPT, ctx);
  return (async () => {
    for (let n = 0; n < 4; n++) { await new Promise((r) => setTimeout(r, 0)); await timer(); }
    return reloads;
  })();
}
const SCRIPT = process.argv[2];
(async () => {
  const out = {
    same: await run(["abc1234"], { idle: true }),
    changed: await run(["abc1234", "def5678"], { idle: true }),
    busy: await run(["abc1234", "def5678"], { idle: false }),
    voice: await run(["abc1234", "def5678"], { idle: true, voiceOpen: true }),
    draft: await run(["abc1234", "def5678"], { idle: true, draft: true }),
  };
  console.log(JSON.stringify(out));
})();
""")
    p = subprocess.run([node, str(harness), script], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    import json

    got = json.loads(p.stdout)
    assert got == {"same": 0, "changed": 1, "busy": 0, "voice": 0, "draft": 0}, got
