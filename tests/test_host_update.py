"""Auto-update (assistant/updater.py) against real git repos with a bare
"remote", and the host supervisor (assistant/host.py) with fakes."""

import io
import subprocess
from pathlib import Path

import pytest

from assistant import host, updater
from tests.test_webapp import make_app  # noqa: F401 (fixture)
from assistant.updater import (FAILED, LOCKED, ROLLED_BACK, SKIPPED, UP_TO_DATE,
                               UPDATED, Updater)


def git(cwd, *args):
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


def commit(repo, name, text, message=None, sign=True):
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-S" if sign else "--no-gpg-sign", "-m",
        message or f"edit {name}")
    return git(repo, "rev-parse", "HEAD")


def keygen(path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test",
                    "-f", str(path)], check=True)
    return path


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """(dev clone that signs and pushes `stable`, the home host's clone).
    Commits are SSH-signed with a test key listed in tmp allowed_signers."""
    import shutil

    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not available")
    gitconfig = tmp_path / "gitconfig"  # isolate from the machine's git config
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    key = keygen(tmp_path / "ci_key")
    signers = tmp_path / "allowed_signers"
    signers.write_text(f'ci@example.com namespaces="git" {Path(str(key) + ".pub").read_text()}')
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    dev = tmp_path / "dev"
    git(tmp_path, "init", "-q", "-b", "master", str(dev))
    for k, v in (("user.email", "ci@example.com"), ("user.name", "CI"),
                 ("gpg.format", "ssh"), ("gpg.ssh.program", "ssh-keygen"),
                 ("user.signingkey", str(key))):
        git(dev, "config", k, v)
    commit(dev, "requirements.txt", "anthropic\n")
    commit(dev, "config.toml", '[update]\nenabled = true\n')
    commit(dev, "app.py", "v = 1\n")
    git(dev, "remote", "add", "origin", str(remote))
    git(dev, "push", "-q", "origin", "master:stable", "master")
    home = tmp_path / "home"
    git(tmp_path, "clone", "-q", "-b", "master", str(remote), str(home))
    git(home, "config", "user.email", "kyle@example.com")
    git(home, "config", "user.name", "Kyle")
    return dev, home, signers


class Recorder:
    """Real git, fake python (pip and the smoke run)."""

    def __init__(self, smoke_ok=True, pip_ok=True, import_ok=True):
        self.python_calls = []
        self.smoke_ok, self.pip_ok, self.import_ok = smoke_ok, pip_ok, import_ok

    def __call__(self, cmd, cwd, timeout=0):
        if cmd[0] == "git":
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            return p.returncode, p.stdout + p.stderr
        self.python_calls.append(cmd[1:])
        if cmd[1:3] == ["-m", "pip"]:
            ok = self.pip_ok if not callable(self.pip_ok) else self.pip_ok()
            return (0, "") if ok else (1, "ERROR: no matching distribution")
        if "--check" in cmd:
            return (0, "All required checks passed.") if self.smoke_ok else \
                (1, "[FAIL] ANTHROPIC_API_KEY: not set")
        if cmd[1:2] == ["-c"] and not self.import_ok:
            return 1, "ModuleNotFoundError: No module named 'broken'"
        return 0, ""


def make(repos_, rec=None, said=None, **kw):
    _, home, signers = repos_
    rec = rec or Recorder()
    kw.setdefault("allowed_signers", signers)
    return Updater(root=home, python="python", run=rec,
                   say=(said.append if said is not None else lambda s: None), **kw), rec


def publish(dev, name="app.py", text="v = 2\n", sign=True):
    sha = commit(dev, name, text, sign=sign)
    git(dev, "push", "-q", "origin", "master:stable")
    return sha


def pips(rec):
    return sum(c[:2] == ["-m", "pip"] for c in rec.python_calls)


def test_up_to_date(repos):
    up, rec = make(repos)
    assert up.update().status == UP_TO_DATE and rec.python_calls == []


def test_fast_forward_and_smoke(repos):
    dev, home, _ = repos
    new = publish(dev)
    said = []
    up, rec = make(repos, said=said)
    r = up.update()
    assert r.status == UPDATED and git(home, "rev-parse", "HEAD") == new
    assert (home / "app.py").read_text() == "v = 2\n"
    assert ["-m", "assistant", "--check"] in rec.python_calls  # smoke ran
    assert pips(rec) == 0  # no requirements change
    assert said and said[0].startswith("update updated")
    assert up.applied() == new and not up.lock_file.exists()


# ---------- who is trusted ----------

def test_unsigned_commit_is_not_applied(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, sign=False)
    up, rec = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "no valid signature" in r.detail
    assert git(home, "rev-parse", "HEAD") == old and rec.python_calls == []


def test_commit_signed_by_an_unknown_key_is_not_applied(repos, tmp_path):
    dev, home, _ = repos
    other = keygen(tmp_path / "attacker_key")
    git(dev, "config", "user.signingkey", str(other))
    old = git(home, "rev-parse", "HEAD")
    publish(dev)
    up, _ = make(repos)
    assert up.update().status == SKIPPED and git(home, "rev-parse", "HEAD") == old


def test_a_signed_stable_tag_on_an_unsigned_commit_is_enough(repos):
    dev, home, _ = repos
    new = publish(dev, sign=False)
    git(dev, "tag", "-s", "-m", "promoted", f"stable-{new[:7]}", new)
    git(dev, "push", "-q", "origin", "--tags")
    up, _ = make(repos)
    assert up.update().status == UPDATED and git(home, "rev-parse", "HEAD") == new


def test_other_tags_dont_count(repos):
    dev, home, _ = repos
    new = publish(dev, sign=False)
    git(dev, "tag", "-s", "-m", "x", "v1-not-stable", new)
    git(dev, "push", "-q", "origin", "--tags")
    up, _ = make(repos)
    assert up.update().status == SKIPPED


def test_missing_allowed_signers_refuses(repos, tmp_path):
    dev, home, _ = repos
    publish(dev)
    up, _ = make(repos, allowed_signers=tmp_path / "nope")
    r = up.update()
    assert r.status == SKIPPED and "doesn't exist" in r.detail


def test_signatures_can_be_turned_off_explicitly(repos, tmp_path):
    dev, home, _ = repos
    new = publish(dev, sign=False)
    up, _ = make(repos, trust_unsigned=True, allowed_signers=tmp_path / "nope")
    assert up.update().status == UPDATED and git(home, "rev-parse", "HEAD") == new


def test_wrong_origin_is_refused(repos):
    dev, home, _ = repos
    publish(dev)
    up, _ = make(repos, repo="github.com/kylechen839-max/Claude-Personal-Assistant")
    r = up.update()
    assert r.status == SKIPPED and "not github.com/kylechen839-max" in r.detail


@pytest.mark.parametrize("url", [
    "https://github.com/kylechen839-max/Claude-Personal-Assistant.git",
    "https://x-access-token:abc@github.com/kylechen839-max/claude-personal-assistant",
    "git@github.com:kylechen839-max/Claude-Personal-Assistant.git",
    "ssh://git@github.com/kylechen839-max/Claude-Personal-Assistant/"])
def test_normalize_remote(url):
    assert updater.normalize_remote(url) == \
        "github.com/kylechen839-max/claude-personal-assistant"
    assert updater.normalize_remote("https://github.com/evil/Claude-Personal-Assistant") != \
        updater.normalize_remote(url)


# ---------- what is updated ----------

def test_only_the_stable_branch_is_followed(repos):
    dev, home, _ = repos
    commit(dev, "app.py", "v = untested\n")
    git(dev, "push", "-q", "origin", "master")  # master moved, stable didn't
    up, _ = make(repos)
    assert up.update().status == UP_TO_DATE
    assert (home / "app.py").read_text() == "v = 1\n"


def test_only_the_local_branch_is_updated(repos):
    dev, home, _ = repos
    git(home, "checkout", "-q", "-b", "my-feature")
    mine = git(home, "rev-parse", "HEAD")
    publish(dev)
    up, _ = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "my-feature, not master" in r.detail
    assert git(home, "rev-parse", "HEAD") == mine


def test_diverged_is_skipped_and_left_alone(repos):
    dev, home, _ = repos
    mine = commit(home, "notes.txt", "my local commit\n", sign=False)
    publish(dev)
    up, rec = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "not an ancestor" in r.detail
    assert git(home, "rev-parse", "HEAD") == mine and rec.python_calls == []


def test_dirty_tree_is_skipped_and_kept(repos):
    dev, home, _ = repos
    publish(dev)
    (home / "app.py").write_text("v = my experiment\n")
    up, _ = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "app.py" in r.detail
    assert (home / "app.py").read_text() == "v = my experiment\n"


def test_local_config_edits_dont_block_and_survive(repos):
    dev, home, _ = repos
    new = publish(dev)
    (home / "config.toml").write_text('[update]\nenabled = true\n[wake]\nmic_device = 0\n')
    up, _ = make(repos)
    assert up.update().status == UPDATED and git(home, "rev-parse", "HEAD") == new
    assert "mic_device = 0" in (home / "config.toml").read_text()


def test_config_conflict_is_skipped_safely(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, "config.toml", '[update]\nenabled = true\ncheck_minutes = 5\n')
    (home / "config.toml").write_text('[update]\nenabled = true\n# mine\n')
    up, _ = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "config.local.toml" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    assert "# mine" in (home / "config.toml").read_text()


# ---------- requirements: never automatically ----------

def test_requirements_change_is_not_applied_automatically(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, "requirements.txt", "anthropic\nnew-package\n")
    up, rec = make(repos)
    r = up.update()
    assert r.status == SKIPPED and "--update" in r.detail
    assert git(home, "rev-parse", "HEAD") == old and pips(rec) == 0


def test_requirements_change_by_hand_needs_the_assistant_stopped(repos):
    dev, home, _ = repos
    publish(dev, "requirements.txt", "anthropic\nnew-package\n")
    up, rec = make(repos)
    alive = home / "logs" / "host-web.alive"
    alive.parent.mkdir(exist_ok=True)
    alive.touch()
    r = up.update(allow_requirements=True)
    assert r.status == SKIPPED and "running (web)" in r.detail and pips(rec) == 0
    import os
    import time as _t

    os.utime(alive, (_t.time() - 60, _t.time() - 60))  # host stopped a minute ago
    r = up.update(allow_requirements=True)
    assert r.status == UPDATED and "requirements installed" in r.detail
    assert ["-m", "pip", "install", "-q", "-r", "requirements.txt"] in rec.python_calls


def test_failed_smoke_rolls_back_and_rejects(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    bad = publish(dev, "requirements.txt", "anthropic\nbroken\n")
    (home / "config.toml").write_text('[update]\nenabled = true\n# local\n')
    said = []
    up, rec = make(repos, Recorder(import_ok=False), said)
    r = up.update(allow_requirements=True)
    assert r.status == ROLLED_BACK and "No module named" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    assert (home / "requirements.txt").read_text() == "anthropic\n"
    assert "# local" in (home / "config.toml").read_text()  # --keep kept it
    assert pips(rec) == 2  # the update's, then the old requirements back
    assert said[-1].startswith("!!! update rolled-back")
    assert up.applied() is None and bad in up.rejected()
    # the bad commit is not retried; a newer stable is
    assert up.update(allow_requirements=True).status == SKIPPED
    newer = publish(dev, "app.py", "v = 3\n")
    up.run = Recorder()
    assert up.update(allow_requirements=True).status == UPDATED
    assert git(home, "rev-parse", "HEAD") == newer


def test_a_failed_check_rolls_back_but_is_tried_again(repos):
    """--check failing (no mic, no audio device, no key) says nothing about
    the code: the update is undone now, never rejected for good."""
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    new = publish(dev, "app.py", "v = 2\n")
    (home / "config.toml").write_text('[update]\nenabled = true\n')
    up, rec = make(repos, Recorder(smoke_ok=False))
    r = up.update()
    assert r.status == ROLLED_BACK and "ANTHROPIC_API_KEY" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    assert new not in up.rejected()
    up.run = Recorder()  # the mic is back
    assert up.update().status == UPDATED
    assert git(home, "rev-parse", "HEAD") == new


def test_failed_pip_rolls_back(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    publish(dev, "requirements.txt", "anthropic\nnope\n")
    calls = iter([False, True])  # the update's pip fails; the rollback's works
    up, _ = make(repos, Recorder(pip_ok=lambda: next(calls)))
    r = up.update(allow_requirements=True)
    assert r.status == ROLLED_BACK and "pip install failed" in r.detail
    assert git(home, "rev-parse", "HEAD") == old


def test_failed_rollback_pip_is_a_failure(repos):
    dev, home, _ = repos
    publish(dev, "requirements.txt", "anthropic\nnope\n")
    up, _ = make(repos, Recorder(pip_ok=False))
    r = up.update(allow_requirements=True)
    assert r.status == FAILED and "pip install -r requirements.txt` by hand" in r.detail


def test_no_stable_branch_is_skipped(repos):
    up, _ = make(repos, branch="does-not-exist")
    assert up.update().status == SKIPPED


def test_one_update_at_a_time(repos):
    dev, _, _ = repos
    publish(dev)
    up, _ = make(repos)
    assert up.acquire()
    assert up.update().status == LOCKED
    up.release()
    assert up.update().status == UPDATED


def test_rollback_locked_only_for_its_own_update(repos):
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    new = publish(dev)
    up, _ = make(repos)
    assert up.update().status == UPDATED
    assert up.rollback_locked(old, "f" * 40, "x").status == SKIPPED  # HEAD isn't that
    git(home, "checkout", "-q", "-b", "kyles-branch")  # Kyle switched branch
    assert up.rollback_locked(old, new, "x").status == SKIPPED
    assert git(home, "rev-parse", "kyles-branch") == new  # his branch untouched
    git(home, "checkout", "-q", "master")
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

    def applied(self):
        return self.made

    made = None  # the commit "the updater" applied

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
    h.updater.made = new
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
    s = assistant.session(app.state.web.store.create())
    s.busy.acquire()
    assert local.get("/healthz").json()["busy"] is True
    s.busy.release()
    # a voice turn in progress
    conv = type("Conv", (), {})()
    conv.lock = threading.Lock()
    conv.current = type("Turn", (), {"finished": False})()
    conv.pending = None
    conv.waiting = None
    app.state.voice_conversations["sid2"] = (conv, None)
    assert local.get("/healthz").json()["busy"] is True
    conv.current.finished = True
    assert local.get("/healthz").json()["busy"] is False
    conv.waiting = object()  # the next utterance, queued
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


def test_head_moved_by_hand_restarts_without_rollback():
    old, new = "a" * 40, "c" * 40
    h, started, _ = make_host(heads=[old, new, new],
                              health=lambda port: {"ok": True, "version": "zzz"})
    h.start()
    h.tick(100, {"next_update": 1e9, "next_head": 0})  # updater.made is None
    assert len(started) == 2 and h.updater.rollbacks == []
    assert "by hand" in h.log.getvalue()


def pgp_commit(repo, parent, message="pgp-signed"):
    """A commit object carrying an OpenPGP signature block (not SSH)."""
    tree = git(repo, "rev-parse", f"{parent}^{{tree}}")
    body = (f"tree {tree}\nparent {parent}\n"
            "author Eve <eve@example.com> 1700000000 +0000\n"
            "committer Eve <eve@example.com> 1700000000 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n"
            " -----END PGP SIGNATURE-----\n\n" + message + "\n")
    # bytes, not text: on Windows text mode would write \r\n into the object
    p = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo,
                       input=body.encode(), capture_output=True, check=True)
    return p.stdout.decode().strip()


class VerifierSaysYes(Recorder):
    """As if a keyring verified anything: verify-commit/verify-tag exit 0."""

    def __call__(self, cmd, cwd, timeout=0):
        if cmd[0] == "git" and any(c in ("verify-commit", "verify-tag") for c in cmd):
            return 0, "gpg: Good signature"
        return super().__call__(cmd, cwd, timeout)


def test_openpgp_signatures_never_count(repos):
    """Only SSH signatures are checked against allowed_signers; an OpenPGP
    one (that a gpg keyring might accept) is refused before verifying."""
    dev, home, _ = repos
    old = git(home, "rev-parse", "HEAD")
    bad = pgp_commit(dev, git(dev, "rev-parse", "HEAD"))
    git(dev, "push", "-q", "origin", f"{bad}:refs/heads/stable")
    up, _ = make(repos, VerifierSaysYes())
    r = up.update()
    assert r.status == SKIPPED and "no valid signature" in r.detail
    assert git(home, "rev-parse", "HEAD") == old
    # and a stable-* tag whose signature is PGP doesn't count either
    tag_body = (f"object {bad}\ntype commit\ntag stable-{bad[:7]}\n"
                "tagger Eve <eve@example.com> 1700000000 +0000\n\nok\n"
                "-----BEGIN PGP SIGNATURE-----\n\niQEz\n-----END PGP SIGNATURE-----\n")
    p = subprocess.run(["git", "mktag"], cwd=dev, input=tag_body.encode(),
                       capture_output=True)
    if p.returncode == 0:  # newer git validates; the PGP block is fine for mktag
        git(dev, "update-ref", f"refs/tags/stable-{bad[:7]}", p.stdout.decode().strip())
        git(dev, "push", "-q", "origin", "--tags")
        assert up.update().status == SKIPPED


def test_ssh_signed_detection(repos):
    dev, _, _ = repos
    up, _ = make(repos)
    up.root = dev
    assert up.ssh_signed("commit", "HEAD")
    unsigned = commit(dev, "x.txt", "x\n", sign=False)
    assert not up.ssh_signed("commit", unsigned)
    assert not up.ssh_signed("commit", pgp_commit(dev, unsigned))
