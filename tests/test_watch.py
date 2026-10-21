"""The watchdog (assistant/watch.py), the go-live gate (updater, host,
deploy) and the check session it talks to: all offline, with a fake git,
fake apps and a fake smoke run."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from assistant import deploy, host, smoke, watch
from assistant.config import DEFAULTS, load_config
from assistant.updater import Updater

OLD, NEW, GOOD = "a" * 40, "b" * 40, "c" * 40
TOKEN = "w" * 24 + "-watch-test-token-01234"
SECRET_REPLY = "Your dentist appointment with Dr. Who is at 4 PM"


# ----- fakes -------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_real_commands(monkeypatch):
    """No test here may run a real pkill, PowerShell or restart: every
    command goes through an injected fake `run`."""
    def refuse(cmd, cwd=None, timeout=0):
        raise AssertionError(f"a real command would have run: {cmd[:2]}")
    monkeypatch.setattr(watch, "_run", refuse)


class FakeGit:
    """git for Updater: HEAD, ancestry, what's on GitHub, reset --keep;
    other commands are recorded and succeed."""

    def __init__(self, head=NEW, ancestors=((GOOD, NEW), (OLD, NEW)), remote=(NEW, OLD, GOOD)):
        self.head = head
        self.ancestors = set(ancestors)
        self.remote = set(remote)
        self.calls = []
        self.restarts = []
        self.on_restart = None
        self.branch = "master"

    def __call__(self, cmd, cwd, timeout=0):
        self.calls.append(list(cmd))
        if cmd[0] != "git":
            self.restarts.append(cmd[-1])
            if self.on_restart:
                self.on_restart(cmd[-1])
            return 0, ""
        args = cmd[1:]
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, self.head + "\n"
        if args[:2] == ["rev-parse", "--verify"]:
            short = args[3].split("^")[0]
            full = next((s for s in (OLD, NEW, GOOD) if s.startswith(short)), "")
            return (0, full + "\n") if full else (1, "")
        if args[:2] == ["merge-base", "--is-ancestor"]:
            return (0 if (args[2], args[3]) in self.ancestors else 1), ""
        if args[:3] == ["branch", "-r", "--contains"]:
            return 0, ("  origin/master\n" if args[3] in self.remote else "")
        if args[:2] == ["reset", "--keep"]:
            self.head = args[2]
            return 0, ""
        if args[:1] == ["symbolic-ref"]:
            return 0, self.branch + "\n"
        return 0, ""

    def resets(self):
        return [c[3] for c in self.calls if c[1:3] == ["reset", "--keep"]]


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


PAGE = '<script src="/app.js" defer></script><link rel="stylesheet" href="/app.css">'


class Apps:
    """The web apps on this machine, by port: up or down, their version."""

    def __init__(self, git):
        self.git = git
        self.state = {}  # port -> dict(up, version, started, assets, busy)
        self.public = {}  # url -> status

    def add(self, port, version=NEW, up=True):
        self.state[port] = {"up": up, "version": version[:7], "started": 1.0,
                            "assets": {}, "busy": False, "up_at": None}

    def get(self, url, timeout=0, **kw):
        for public, status in self.public.items():
            if url.startswith(public):
                if status != 200:
                    return Resp(status)
                port = next(iter(self.state))
                return Resp(200, {"ok": True, "version": self.state[port]["version"]})
        port = int(url.split(":")[2].split("/")[0])
        app = self.state.get(port)
        if app is not None and app["up_at"] is not None and self.clock() >= app["up_at"]:
            app.update(up=True, up_at=None)  # a slow start finished
        if app is None or not app["up"]:
            raise ConnectionError("refused")
        path = "/" + url.split("/", 3)[3]
        if path == "/healthz":
            return Resp(200, {"ok": True, "version": app["version"], "busy": app["busy"],
                              "started": app["started"]})
        if path == "/":
            return Resp(200, text=PAGE)
        return Resp(app["assets"].get(path, 200))


def make_instance(name="main", port=8765, label=None):
    cfg = load_config(None, None)
    return watch.Instance(name, "config.toml" if name == "main" else f"config.{name}.toml",
                          ".env" if name == "main" else f".env.{name}", port,
                          label or watch.default_label(name), settings=cfg,
                          restart_command=["restart", name])


def passing_smoke(base, token, conversation=False):
    return [smoke.Outcome("text turn", "PASS", f"replied {SECRET_REPLY!r}")]


def failing_smoke(base, token, conversation=False):
    return [smoke.Outcome("text turn", "PASS", "ok"),
            smoke.Outcome("follow-up turn", "FAIL",
                          f"error reply: Sorry, something went wrong. {SECRET_REPLY} {token}")]


class Clock:
    def __init__(self):
        import time

        self.t = time.time()  # real now: lock files' ages are measured with it

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def world(tmp_path):
    """A checkout with Kyle's app on 8765, a fake git and a Watch on it."""
    (tmp_path / "config.toml").write_text("")
    (tmp_path / ".env").write_text(f"WEB_TOKEN={TOKEN}\n")
    (tmp_path / ".env.mom").write_text(f"WEB_TOKEN={TOKEN}-mom\n")
    git = FakeGit()
    apps = Apps(git)
    clock = Clock()
    apps.clock = clock
    sent = []

    def make(instances=None, smoke_fn=passing_smoke, gate=None, settings=None):
        settings = settings or load_config(None, None)
        up = Updater(root=tmp_path, run=git, clock=clock)
        w = watch.Watch(tmp_path, instances=instances or [make_instance()], http=apps,
                        run=git, updater=up, alerts=watch.Alerts(sent.append),
                        smoke_fn=smoke_fn, gate=gate, clock=clock, sleep=clock.sleep,
                        settings=settings)
        return w
    return SimpleNamespace(root=tmp_path, git=git, apps=apps, clock=clock, sent=sent,
                           make=make)


# ----- discovery -------------------------------------------------------------------

def test_discover_finds_kyles_app_and_each_family_copy(tmp_path):
    """install_family_tasks.ps1's rule: config.<name>.toml with .env.<name>;
    never *.local.toml; [watch] enabled = false leaves one out; each one's
    port (config.<name>.local.toml included), label and phone address."""
    files = {
        "config.toml": "", ".env": f"WEB_TOKEN={TOKEN}\n",
        "config.local.toml": "[webapp]\nport = 8765\n",
        "config.mom.toml": '[watch]\nlabel = "Mom\'s app"\n',
        "config.mom.local.toml": '[webapp]\nport = 8766\npublic_origin = '
                                 '"https://pc.tail1.ts.net:8443"\n',
        ".env.mom": f"WEB_TOKEN={TOKEN}-mom\n",
        "config.kai.toml": "[webapp]\nport = 8767\n", ".env.kai": f"WEB_TOKEN={TOKEN}-kai\n",
        "config.orphan.toml": "",  # no .env.orphan
        "config.off.toml": "[watch]\nenabled = false\n", ".env.off": "",
        # created, never started: its app writes WEB_TOKEN on first start
        "config.new.toml": "[webapp]\nport = 8768\n", ".env.new": "ANTHROPIC_API_KEY=x\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    found = {i.name: i for i in watch.discover(tmp_path, tailscale={
        8765: "https://pc.tail1.ts.net", 8767: "https://pc.tail1.ts.net:8444"})}
    assert sorted(found) == ["kai", "main", "mom"]
    assert (found["main"].port, found["mom"].port, found["kai"].port) == (8765, 8766, 8767)
    assert found["main"].label == "Your app" and found["mom"].label == "Mom's app"
    assert found["kai"].label == "Kai's app"
    assert found["mom"].public_url == "https://pc.tail1.ts.net:8443"  # public_origin
    assert found["main"].public_url == "https://pc.tail1.ts.net"      # tailscale serve
    assert found["kai"].public_url == "https://pc.tail1.ts.net:8444"
    assert (found["mom"].config, found["mom"].env_file) == ("config.mom.toml", ".env.mom")


def test_tailscale_serve_status_maps_local_ports():
    status = {"TCP": {"443": {"HTTPS": True}},
              "Web": {"kyle.tail44aa39.ts.net:443": {"Handlers": {"/": {
                  "Proxy": "http://127.0.0.1:8765"}}},
                      "kyle.tail44aa39.ts.net:8443": {"Handlers": {"/": {
                          "Proxy": "http://127.0.0.1:8766"}}}}}
    urls = watch.tailscale_urls(lambda cmd, cwd, timeout: (0, json.dumps(status)))
    assert urls == {8765: "https://kyle.tail44aa39.ts.net",
                    8766: "https://kyle.tail44aa39.ts.net:8443"}
    assert watch.tailscale_urls(lambda *a: (1, "not installed")) == {}


def test_watch_section_is_mirrored_in_config_toml():
    from tests.test_docs import ROOT
    from tests.test_docs import tomllib as toml  # tomli on Python 3.10

    with open(ROOT / "config.toml", "rb") as f:
        assert toml.load(f)["watch"] == DEFAULTS["watch"]


# ----- quick checks ------------------------------------------------------------------

def test_quick_check_flags_each_kind_of_failure(world):
    inst = make_instance()
    inst.public_url = "https://pc.tail1.ts.net"
    world.apps.add(8765)
    world.apps.public["https://pc.tail1.ts.net"] = 200
    checks, health = watch.quick_check(inst, NEW, world.apps)
    assert all(c.ok for c in checks) and [c.name for c in checks] == [
        "healthz", "page", "version", "phone address"]
    world.apps.state[8765]["assets"]["/app.js"] = 404
    world.apps.public["https://pc.tail1.ts.net"] = 502
    checks, _ = watch.quick_check(inst, OLD, world.apps)
    bad = {c.name: c.reason for c in checks if not c.ok}
    assert bad == {"page": "app.js: HTTP 404", "version": "older version running",
                   "phone address": "HTTP 502"}
    world.apps.state[8765]["up"] = False
    checks, health = watch.quick_check(inst, NEW, world.apps)
    assert health is None and [(c.name, c.ok, c.reason) for c in checks] == [
        ("healthz", False, "ConnectionError")]


# ----- alerts on a change of state only ----------------------------------------------

GRACE = DEFAULTS["watch"]["start_grace_seconds"]


def later(world, seconds=GRACE + 1):
    """The next 5-minute run, past an app's start grace."""
    world.clock.t += seconds


def test_alerts_only_when_the_state_changes(world):
    world.apps.add(8765)
    w = world.make()
    w.cfg["rollback"] = False
    w.run()
    assert world.sent == []  # healthy from the start: nothing to say
    world.apps.state[8765]["up"] = False
    world.git.on_restart = lambda name: None  # the restart doesn't help
    w.run()
    assert world.sent == []  # not answering yet: maybe starting (grace)
    for _ in range(3):
        later(world)
        w.run()
    assert len(world.sent) == 1 and world.sent[0].startswith("Your app is failing since")
    assert "healthz: ConnectionError" in world.sent[0]
    world.apps.state[8765]["up"] = True
    w.run()
    w.run()
    assert len(world.sent) == 2 and world.sent[1].startswith("Your app recovered at")
    status = json.loads((world.root / "data" / "watch_status.json").read_text())
    assert status["instances"]["main"]["status"] == "ok"


def test_cloud_url_is_checked_and_alerted_on_but_never_healed(world):
    """[watch] cloud_url: the Render copy's /healthz, from Kyle's PC. Down:
    one alert, no restart or rollback here; back: one recovery alert."""
    cloud = "https://hey-claude.onrender.com"
    world.apps.add(8765)
    world.apps.public[cloud] = 200
    settings = load_config(None, None)
    settings["watch"]["cloud_url"] = cloud + "/"
    w = world.make(settings=settings)
    status = w.run()
    assert status["instances"]["cloud"]["status"] == "ok"
    assert status["instances"]["cloud"]["label"] == "Cloud app"
    assert world.sent == []
    world.apps.public[cloud] = 502
    for _ in range(3):
        later(world)
        status = w.run()
    assert world.sent == [s for s in world.sent if s.startswith("Cloud app is failing")]
    assert len(world.sent) == 1 and "healthz: HTTP 502" in world.sent[0]
    assert world.git.restarts == [] and world.git.resets() == []
    assert status["instances"]["main"]["status"] == "ok"
    banner = watch.banner(status, now=world.clock())
    assert [i["label"] for i in banner["issues"]] == ["Cloud app"]
    world.apps.public[cloud] = 200
    w.run()
    assert len(world.sent) == 2 and world.sent[1].startswith("Cloud app recovered at")
    # off by default: no cloud entry at all
    assert DEFAULTS["watch"]["cloud_url"] == ""
    assert "cloud" not in world.make().run()["instances"]


def test_self_heal_restarts_once_per_failure(world):
    world.apps.add(8765, up=False)
    w = world.make()
    w.cfg["rollback"] = False
    w.run()
    assert world.git.restarts == []  # within the start grace
    later(world)
    w.run()
    later(world)
    w.run()
    assert world.git.restarts == ["main"]  # once, not every 5 minutes
    world.apps.state[8765]["up"] = True
    w.run()
    world.apps.state[8765]["up"] = False
    w.run()
    later(world)
    w.run()
    assert world.git.restarts == ["main", "main"]  # a new failure: once more


def test_a_restart_that_fixes_it_raises_no_alert(world):
    world.apps.add(8765, up=False)
    world.git.on_restart = lambda name: world.apps.state[8765].update(up=True, started=2.0)
    w = world.make()
    w.run()
    later(world)
    status = w.run()
    assert world.git.restarts == ["main"] and world.sent == []
    assert status["instances"]["main"]["status"] == "ok"


# ----- the test conversation -----------------------------------------------------------

def test_functional_check_after_a_restart_and_when_due(world):
    world.apps.add(8765)
    runs = []

    def counting(base, token, conversation=False):
        runs.append((base, token, conversation))
        return passing_smoke(base, token)

    w = world.make(smoke_fn=counting)
    w.run()
    assert runs == [("http://127.0.0.1:8765", TOKEN, True)]  # first sight
    w.run()
    assert len(runs) == 1  # not due again yet
    world.apps.state[8765]["started"] = 99.0  # it restarted
    w.run()
    assert len(runs) == 2
    world.clock.t += 5 * 3600
    w.run()
    assert len(runs) == 2  # not due at 5 h ([watch] functional_hours = 6)
    world.clock.t += 1 * 3600  # [watch] functional_hours
    w.run()
    assert len(runs) == 3
    assert w.up.last_good() == NEW  # passed on HEAD: the last known good


# ----- rollback to the last known good -----------------------------------------------------

def test_rolls_back_to_the_last_good_version_when_a_restart_doesnt_help(world):
    world.apps.add(8765)
    w = world.make(smoke_fn=failing_smoke)
    w.up.set_last_good(GOOD)
    w.run()
    assert world.git.restarts[0] == "main"
    assert world.git.resets() == [GOOD]  # git reset --keep to the last good
    assert NEW in w.up.rejected()       # never applied again
    assert any("rolled back to ccccccc" in s for s in world.sent)
    assert world.git.restarts == ["main", "main"]  # restarted on it


def test_no_rollback_for_failures_outside_the_code(world):
    """Claude down (the canned "unreachable" reply) says nothing about the
    version: no rollback, no rejected commit."""
    world.apps.add(8765)

    def claude_down(base, token, conversation=False):
        return [smoke.Outcome("text turn", "FAIL", "error reply: Claude is "
                              "unreachable right now. I retried a few times")]

    w = world.make(smoke_fn=claude_down)
    w.up.set_last_good(GOOD)
    w.run()
    assert world.git.resets() == [] and w.up.rejected() == []
    assert "text turn: Claude unreachable" in world.sent[-1]


def test_no_rollback_over_local_commits(world):
    world.apps.add(8765)
    world.git.remote = {GOOD}  # HEAD only exists on this machine
    w = world.make(smoke_fn=failing_smoke)
    w.up.set_last_good(GOOD)
    w.run()
    assert world.git.resets() == []


# ----- switching to new code: the go-live gate ---------------------------------------------

def test_new_code_must_pass_the_gate_before_an_app_switches(world):
    world.apps.add(8766, version=OLD)  # Mom's app still on the old version
    gated = []

    def gate(sha):
        gated.append(sha)
        return True, ""

    world.git.on_restart = lambda name: world.apps.state[8766].update(version=NEW[:7])
    w = world.make(instances=[make_instance("mom", 8766)], gate=gate)
    status = w.run()
    assert gated == [NEW] and world.git.restarts == ["mom"]
    assert w.up.gated(NEW) is True and world.sent == []
    assert status["instances"]["mom"]["version"] == NEW[:7]


def test_refuses_to_switch_when_the_gate_fails(world):
    """The new commit failed on the side port: the app is not restarted,
    the checkout goes back to what it runs, the commit is rejected, and an
    alert names it."""
    world.apps.add(8766, version=OLD)
    w = world.make(instances=[make_instance("mom", 8766)],
                   gate=lambda sha: (False, "Mom's app: follow-up turn: HTTP 500"))
    w.run()
    assert world.git.restarts == []
    assert world.git.resets() == [OLD] and world.git.head == OLD
    assert NEW in w.up.rejected()
    assert any("failed its go-live check" in s and "HTTP 500" in s for s in world.sent)


def test_gate_that_cant_tell_neither_switches_nor_refuses(world):
    world.apps.add(8766, version=OLD)
    w = world.make(instances=[make_instance("mom", 8766)],
                   gate=lambda sha: (None, "Claude unreachable"))
    w.run()
    assert world.git.restarts == [] and world.git.resets() == []
    assert w.up.gated(NEW) is None and w.up.rejected() == []


def test_gate_check_runs_each_instance_on_its_side_port(tmp_path):
    (tmp_path / ".env.mom").write_text(f"WEB_TOKEN={TOKEN}\n")
    inst = make_instance("mom", 8766)
    started = []

    class Child:
        def __init__(self, cmd, **kw):
            started.append((cmd, kw["env"]))
            self.code = None

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        def wait(self, timeout=None):
            return 0

    class Side:
        def get(self, url, timeout=0):
            if ":8866/" not in url:
                raise ConnectionError("nothing there")
            if not started:
                raise ConnectionError("not up yet")
            return Resp(200, {"ok": True, "version": NEW[:7]})

    seen = []
    failed = watch.candidate_check(tmp_path, inst, NEW, http=Side(), popen=Child,
                                   smoke_fn=lambda b, t, conversation: seen.append(b) or [],
                                   sleep=lambda s: None)
    assert failed == [] and seen == ["http://127.0.0.1:8866"]
    cmd, env = started[0]
    assert cmd[cmd.index("--port") + 1] == "8866" and "--candidate" in cmd
    assert cmd[cmd.index("--config") + 1] == "config.mom.toml"
    assert "WEB_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env


def test_candidate_config_shares_nothing_it_could_break():
    cfg = watch.candidate_config(load_config(None, None), "config.mom.toml")
    assert cfg["candidate"] and not cfg["history"]["enabled"]
    assert cfg["webapp"]["sessions_file"] == "" and "hub" not in cfg["devteam"]
    assert cfg["logging"]["file"] == "logs/candidate-config.mom.log"


def test_updater_runs_the_gate_before_handing_over_new_code(tmp_path):
    """A failing gate rolls the update back and rejects the commit; one
    that can't tell (Claude down) rolls back without rejecting it."""
    for verdict, rejected in ((False, True), (None, False)):
        git = FakeGit(head=OLD)
        git.remote = {NEW}

        def run(cmd, cwd, timeout=0, git=git):
            args = cmd[1:]
            if args[:2] == ["rev-parse", "origin/stable"]:
                return 0, NEW + "\n"
            if args[:2] == ["merge", "--ff-only"]:
                git.head = NEW
                return 0, ""
            return git(cmd, cwd, timeout)

        up = Updater(root=tmp_path / str(verdict), run=run, trust_unsigned=True,
                     gate=lambda sha, v=verdict: (v, "Your app: text turn: HTTP 500"))
        up.smoke = lambda: (True, "ok")
        result = up.update()
        assert result.status == "rolled-back" and git.head == OLD
        assert (NEW in up.rejected()) is rejected


# ----- the host supervisor ----------------------------------------------------------

class HostUpdater:
    def __init__(self, gate, verdict=None, hosts=("web",)):
        self.gate = gate
        self.verdict = verdict
        self.marked = []
        self.hosts = list(hosts)

    def gated(self, sha):
        return self.verdict

    def last_good(self):
        return None

    def hosts_running(self):
        return self.hosts

    def acquire(self):
        return True

    def release(self):
        pass

    def mark_gated(self, sha, ok):
        self.marked.append((sha, ok))
        self.verdict = ok


def test_host_restarts_on_a_hand_pull_only_after_the_gate(monkeypatch):
    rolled = []
    monkeypatch.setattr(watch, "rollback_checkout",
                        lambda up, bad, to, why: rolled.append((bad, to)) or (True, ""))
    for ok in (False, True):
        started = []
        h = host.Host("web", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                      popen=lambda cmd, started=started: started.append(cmd) or SimpleNamespace(
                          poll=lambda: None, terminate=lambda: None, wait=lambda timeout: 0),
                      health=lambda port: {"ok": True, "busy": False,
                                           "version": NEW[:7]},
                      sleep=lambda s: None)
        h.updater = HostUpdater(lambda sha, ok=ok: (ok, "x"))
        h.updater.head = lambda: OLD
        h.start()
        h.restart_by_hand(NEW)
        assert h.updater.marked == [(NEW, ok)]
        assert len(started) == (2 if ok else 1)  # refused: the old one keeps running
    assert rolled == [(NEW, OLD)]


def test_wake_host_waits_for_the_web_hosts_verdict():
    gate_calls = []
    h = host.Host("wake", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None))
    h.updater = HostUpdater(lambda sha: gate_calls.append(sha) or (True, ""))
    assert h.gate_allows(NEW) is False and gate_calls == []  # the web host decides
    h.updater.verdict = True
    assert h.gate_allows(NEW) is True


# ----- deploy: pull, check on a side port, then switch or refuse ------------------------------

def test_deploy_refuses_and_keeps_the_old_version(world):
    world.git.head = OLD
    world.apps.add(8766, version=OLD)

    def run(cmd, cwd, timeout=0):
        if cmd[1:3] == ["merge", "--ff-only"]:
            world.git.head = NEW
            return 0, ""
        return world.git(cmd, cwd, timeout)

    w = world.make(instances=[make_instance("mom", 8766)],
                   gate=lambda sha: (False, "Mom's app: voice turn: no audio"))
    w.up.run = run
    out = []
    assert deploy.deploy(w, output_fn=out.append) == 1
    assert world.git.head == OLD and world.git.restarts == []
    assert NEW in w.up.rejected() and any("REFUSED" in line for line in out)


def test_deploy_switches_after_the_gate_passes(world):
    world.git.head = OLD
    world.apps.add(8766, version=OLD)

    def run(cmd, cwd, timeout=0):
        if cmd[1:3] == ["merge", "--ff-only"]:
            world.git.head = NEW
            return 0, ""
        return world.git(cmd, cwd, timeout)

    world.git.on_restart = lambda name: world.apps.state[8766].update(version=NEW[:7])
    w = world.make(instances=[make_instance("mom", 8766)], gate=lambda sha: (True, ""))
    w.up.run = run
    out = []
    assert deploy.deploy(w, output_fn=out.append) == 0, out
    assert world.git.restarts == ["mom"] and w.up.applied() == NEW
    assert w.up.last_good() == NEW


def test_deploy_passes_while_the_cloud_is_down(world):
    """[watch] cloud_url failing (Render down) isn't this PC's deploy failing."""
    world.git.head = OLD
    world.apps.add(8766, version=OLD)
    world.apps.public["https://hey-claude.onrender.com"] = 503

    def run(cmd, cwd, timeout=0):
        if cmd[1:3] == ["merge", "--ff-only"]:
            world.git.head = NEW
            return 0, ""
        return world.git(cmd, cwd, timeout)

    world.git.on_restart = lambda name: world.apps.state[8766].update(version=NEW[:7])
    settings = load_config(None, None)
    settings["watch"]["cloud_url"] = "https://hey-claude.onrender.com"
    w = world.make(instances=[make_instance("mom", 8766)], gate=lambda sha: (True, ""),
                   settings=settings)
    w.up.run = run
    out = []
    assert deploy.deploy(w, output_fn=out.append) == 0, out
    assert w.status["instances"]["cloud"]["status"] == "failing"
    assert w.status["instances"]["cloud"]["remote"] is True
    assert "remote" not in w.status["instances"]["mom"]
    assert any("Cloud app" in line and "FAILING" in line for line in out)


# ----- the banner, the owner's only -----------------------------------------------------

def test_banner_lists_failing_apps_and_stale_checks():
    now = 1_800_000_000.0
    status = {"checked": now - 60, "checked_iso": "x", "instances": {
        "mom": {"status": "failing", "label": "Mom's app", "since_iso": "2026-10-20T06:13:00",
                "what": "healthz: HTTP 502"},
        "main": {"status": "ok", "label": "Your app"}}}
    got = watch.banner(status, now)
    assert got["issues"] == [{"instance": "mom", "label": "Mom's app",
                              "since": "2026-10-20T06:13:00", "what": "healthz: HTTP 502"}]
    assert not got["stale"]
    status["instances"]["mom"]["status"] = "ok"
    assert watch.banner(status, now) is None
    assert watch.banner(status, now + 3600)["stale"]  # the watchdog stopped


def test_banner_only_on_kyles_own_instance(tmp_path):
    cfg = load_config(None, None)
    assert watch.banner_source(cfg, ".env") is not None
    assert watch.banner_source(cfg, ".env.mom") is None  # a family copy
    assert watch.banner_source(watch.candidate_config(load_config(None, None), "x"),
                               ".env") is None


# ----- no secrets or conversation text in alerts, logs or the status file ---------------------

def test_alerts_logs_and_status_hold_no_secrets_or_conversation(world, caplog):
    world.apps.add(8765)
    w = world.make(smoke_fn=failing_smoke)
    w.cfg["rollback"] = False
    with caplog.at_level(logging.DEBUG):
        w.run()
    status = (world.root / "data" / "watch_status.json").read_text()
    everything = "\n".join(world.sent) + caplog.text + status
    assert world.sent and "follow-up turn: error reply" in world.sent[0]
    assert TOKEN not in everything
    assert "dentist" not in everything.lower() and "Sorry, something" not in everything


def test_coordinator_gets_the_alert_through_the_link(world, monkeypatch):
    sent = []

    class Link:
        def send(self, text, source="voice"):
            sent.append((text, source))

    monkeypatch.setattr("assistant.devteam.link_from_config", lambda cfg, state: Link())
    cfg = load_config(None, None)
    cfg["devteam"].update(enabled=True, coordinator_session="session_abc123")
    send = watch.coordinator_sender(world.root, cfg)
    watch.Alerts(send)("Mom's app is failing since 06:13: healthz: HTTP 502 (version abc1234).")
    assert sent and sent[0][1] == "watch" and "healthz: HTTP 502" in sent[0][0]
    cfg["watch"]["coordinator"] = False
    assert watch.coordinator_sender(world.root, cfg) is None


@pytest.mark.parametrize("detail,reason", [
    ("GET /healthz -> 502", "HTTP 502"),
    ("ConnectionError: HTTPConnectionPool(host='x', port=1): refused", "ConnectionError"),
    (f"error event: {SECRET_REPLY}", "error event"),
    (f"error reply: Sorry, something went wrong. {SECRET_REPLY}", "error reply"),
    ("error reply: Sorry, I can't reach the internet right now.", "no internet"),
    (f"forgot the number from the first turn (replied {SECRET_REPLY!r})",
     "lost the conversation"),
    (f"no usable reply ({SECRET_REPLY!r})", "empty reply"),
    (f"something else entirely: {SECRET_REPLY}", "failed"),
])
def test_reasons_are_fixed_words(detail, reason):
    assert smoke.classify(detail) == reason


# ----- the check session (server side) ----------------------------------------------------

def test_check_session_saves_nothing_and_runs_only_read_only_tools(tmp_path, caplog):
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from assistant import webapp
    from assistant.chat import ClaudeChat
    from assistant.history import HistoryStore, Recorder
    from tests.test_contract import calendar_tools

    calls = []
    store = HistoryStore(tmp_path / "h.sqlite", eventlog=False)
    chat_client = MagicMock()

    def chat_factory(registry):
        return ClaudeChat(client=chat_client, tools=registry,
                          recorder=Recorder(store, "web", run_async=False))

    a = webapp.WebAssistant(chat_factory, None, None, tools=calendar_tools(calls),
                            history=store)
    app = webapp.create_app(a, TOKEN, reap_interval=3600)
    http = TestClient(app, base_url="https://testserver")
    assert http.post("/api/check-session").status_code == 401
    assert http.post("/api/login", json={"token": TOKEN}).status_code == 200
    r = http.post("/api/check-session")
    assert r.status_code == 200
    assert r.json()["read_only_tools"] == ["list_calendar_events"]
    s = next(iter(a.sessions.values()))
    assert s.chat.recorder is None and s.chat.incognito and s.chat.check_mode
    assert [t["name"] for t in s.registry.schemas()] == ["list_calendar_events"]
    with caplog.at_level(logging.INFO):
        _, is_error = s.registry.dispatch("create_calendar_event",
                                             {"title": "Secret meeting"})
    assert is_error and calls == []  # refused, never run
    assert "refused create_calendar_event" in caplog.text
    assert "Secret meeting" not in caplog.text  # tool input never logged
    assert s.registry.dispatch("list_calendar_events", {"day": "today"})[1] is False
    # a session that already has a conversation can't become one
    other = TestClient(app, base_url="https://testserver")
    other.post("/api/login", json={"token": TOKEN})
    other.get("/api/state")
    s2 = next(x for x in a.sessions.values() if x is not s)
    s2.chat.history.append({"role": "user", "content": "hi"})
    assert other.post("/api/check-session").status_code == 409


def test_family_member_never_sees_the_health_banner(tmp_path):
    """/api/state's health: the owner's sessions only."""
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from assistant import webapp

    a = webapp.WebAssistant(lambda registry: MagicMock(history=[], incognito=False,
                                                       conversation_id=None,
                                                       language="en"), None, None)
    health = {"issues": [{"label": "Mom's app", "since": "x", "what": "healthz: HTTP 502"}]}
    app = webapp.create_app(a, TOKEN, reap_interval=3600, health=lambda: health)
    owner = TestClient(app, base_url="https://testserver")
    owner.post("/api/login", json={"token": TOKEN})
    assert owner.get("/api/state").json()["health"] == health
    a.user_of = lambda sid: "mom"  # every session is now a family member's
    a.family = SimpleNamespace(context=lambda user: SimpleNamespace(
        registry=webapp.ToolRegistry(), chat_factory=lambda r: MagicMock(
            history=[], incognito=False, conversation_id=None, language="en"),
        history=None), get=lambda user: {"id": user})
    a.sessions.clear()
    assert owner.get("/api/state").json()["health"] is None


# ----- the smoke conversation (client side) ------------------------------------------------

class ConversationHTTP:
    """A server for run_conversation: replies scripted per turn."""

    def __init__(self, check=200, tool=True, remembers=True):
        self.check, self.tool, self.remembers = check, tool, remembers
        self.turns = []
        self.code = None

    def get(self, url, headers=None, timeout=0, **kw):
        from tests.test_preflight import FakeResponse

        return FakeResponse(200 if url.endswith("/healthz") or headers.get("Cookie") else 401)

    def post(self, url, headers=None, timeout=0, json=None, files=None, **kw):
        from tests.test_preflight import FakeResponse

        if url.endswith("/api/login"):
            return FakeResponse(200, headers={"set-cookie": "hc_session=abc; Path=/"})
        if url.endswith("/api/check-session"):
            return FakeResponse(self.check, body={"read_only_tools": ["list_calendar_events"],
                                                  "server_tools": ["web_search"]}
                                if self.check == 200 else None)
        if url.endswith("/api/logout"):
            return FakeResponse(200, body={"ok": True})
        text = (json or {}).get("text", "")
        self.turns.append(text or "(audio)")
        import re as _re
        found = _re.search(r"number (\d{4})", text)
        if found:
            self.code = found.group(1)
        events = []
        if files:
            events.append({"type": "transcript", "text": "what's on my calendar"})
        if "calendar" in text and self.tool:
            events.append({"type": "tool", "name": "list_calendar_events"})
        reply = (f"It's {self.code}." if "What number" in text and self.remembers
                 else "Okay.")
        events += [{"type": "reply", "text": reply}, {"type": "done"}]
        import json as _json
        return FakeResponse(200, [_json.dumps(e) for e in events])


def conversation(http, voice=None):
    url = "http://127.0.0.1:8765"
    return {o.name: o for o in smoke.run_smoke(
        url, TOKEN, client=smoke.WebClient(url, session=http), voice_ws=voice,
        conversation=True)}


def test_conversation_passes_and_keeps_the_voice_socket_open_for_the_last_turn():
    http = ConversationHTTP()
    order = []

    def voice(c, wav, during=None):
        order.append("voice")
        during()
        order.append("end")
        return smoke.Outcome("voice turn", "PASS", "ok")

    got = conversation(http, voice)
    assert all(o.status == "PASS" for o in got.values()), got
    assert list(got)[-2:] == ["voice turn", "last text turn"]
    assert len(http.turns) == 5 and "Thanks." in http.turns[-1]
    assert order == ["voice", "end"]


def test_conversation_starts_with_a_turn_the_fast_model_answers():
    """Judge B3: every turn said "remember" or was long, so neither the gate
    nor the watchdog ever sent a request to Haiku. The first turn is now a
    short one with no main-model word, and it must answer."""
    from assistant import router

    http = ConversationHTTP()
    got = conversation(http)
    assert list(got)[4] == "quick turn" and got["quick turn"].status == "PASS"
    chat = SimpleNamespace(model="main", fast_model="fast", router="auto", history=[],
                           tainted=lambda: False, escalated=False)
    assert router.choose_model(chat, http.turns[0]) == ("fast", "simple")


def test_conversation_fails_without_the_tool_call_or_the_memory():
    got = conversation(ConversationHTTP(tool=False))
    assert got["tool call"].status == "FAIL" and got["tool call"].reason == "no tool call"
    got = conversation(ConversationHTTP(remembers=False))
    assert got["follow-up turn"].status == "FAIL"
    assert got["follow-up turn"].reason == "lost the conversation"


@pytest.mark.parametrize("reply", [
    "It's 4821.", "4,821", "4 821", "４８２１", "号码是四千八百二十一。", "四八二一",
    "是 4821。",
])
def test_the_remembered_number_counts_in_any_way_it_is_written(reply):
    """The family deploy was refused twice: Mom's Chinese instance answered
    the recall turn in Chinese numerals and the gate, looking only for the
    digits, called it "lost the conversation" (Kai once failed on a grouped
    number). The number still counts however it is written."""
    assert smoke.reply_has_number(reply, "4821")


@pytest.mark.parametrize("reply", ["I don't remember.", "4812", "四千八百一十二",
                                   "一二三四", "Okay."])
def test_a_wrong_or_missing_number_still_fails(reply):
    assert not smoke.reply_has_number(reply, "4821")


def test_a_chinese_reply_passes_the_follow_up_turns():
    class Chinese(ConversationHTTP):
        def post(self, url, **kw):
            response = super().post(url, **kw)
            if "What number" in ((kw.get("json") or {}).get("text", "")):
                zh = "".join("零一二三四五六七八九"[int(d)] for d in self.code)
                response = type(response)(200, [json.dumps(e) for e in (
                    {"type": "reply", "text": f"是{zh}。"}, {"type": "done"})])
            return response

    got = conversation(Chinese())
    assert got["follow-up turn"].status == "PASS", got["follow-up turn"]
    assert got["last text turn"].status == "PASS"


def test_conversation_never_runs_without_a_check_session():
    http = ConversationHTTP(check=404)
    got = conversation(http)
    assert got["check session"].status == "FAIL" and http.turns == []


# ----- the API down: a clear sentence ---------------------------------------------------

def test_claude_down_says_so_clearly(caplog):
    import anthropic
    import httpx

    from assistant.cli import friendly_error

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    for status, cls in ((529, anthropic.OverloadedError), (503, anthropic.ServiceUnavailableError),
                        (500, anthropic.InternalServerError)):
        err = cls("upstream said: " + SECRET_REPLY, response=httpx.Response(
            status, request=request), body=None)
        with caplog.at_level(logging.INFO):
            assert friendly_error(err, SECRET_REPLY, lambda m: None).startswith(
                "Claude is unreachable right now")
        assert f"Claude unavailable (HTTP {status}): {cls.__name__}" in caplog.text
    assert SECRET_REPLY not in caplog.text  # neither the request nor the API's text
    assert smoke.is_error_reply("Claude is unreachable right now. I retried a few times.")


def test_host_starts_the_watchdog_after_a_restart_on_new_code():
    """host.py's post-update health check passing triggers the test
    conversation right away (python -m assistant.watch --functional)."""
    watched = []
    h = host.Host("web", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  popen=lambda cmd: SimpleNamespace(poll=lambda: None, terminate=lambda: None,
                                                    wait=lambda timeout: 0),
                  health=lambda port: {"ok": True, "busy": False, "version": NEW[:7]},
                  sleep=lambda s: None)
    h.updater = SimpleNamespace(head=lambda: NEW)
    h.watcher = lambda: watched.append("watch")
    h.start()
    h.restart_on(NEW)
    assert watched == ["watch"]


# ----- round 2: never heal what isn't broken, never reject on a fluke ---------------------

LOCAL = "d" * 40


def test_no_rollback_without_a_restart_that_ran(world, monkeypatch):
    """A Pi with no [watch] restart_command: an app that doesn't answer is
    reported, never rolled back (nothing proves the code is to blame)."""
    monkeypatch.setattr(watch, "WINDOWS", False)  # no Task Scheduler fallback
    world.apps.add(8765, up=False)
    inst = make_instance()
    inst.restart_command = []
    w = world.make(instances=[inst])
    w.up.set_last_good(GOOD)
    for _ in range(3):
        w.run()
        later(world)
    assert world.git.resets() == [] and w.up.rejected() == []
    assert "Your app is failing" in world.sent[0]


def test_windows_falls_back_to_the_task_scheduler_restart(tmp_path, monkeypatch):
    """No [watch] restart_command: Windows restarts the app's task
    (restart_instance.ps1), elsewhere there is no way to restart it."""
    inst = make_instance("mom", 8866)
    inst.restart_command = []
    monkeypatch.setattr(watch, "WINDOWS", True)
    cmd = watch.restart_command(tmp_path, inst)
    assert cmd[0] == "powershell" and cmd[-2:] == ["-Name", "mom"]
    assert cmd[cmd.index("-File") + 1] == str(tmp_path / "scripts" / "restart_instance.ps1")
    monkeypatch.setattr(watch, "WINDOWS", False)
    assert watch.restart_command(tmp_path, inst) == []
    inst.restart_command = ["systemctl", "--user", "restart", "hey-claude"]
    assert watch.restart_command(tmp_path, inst) == inst.restart_command


def test_a_slow_start_after_the_restart_is_waited_for(world):
    """Restarted, it takes 200 s to load its models: that's waited for (up
    to start_grace_seconds), not rolled back."""
    world.apps.add(8765, up=False)
    world.git.on_restart = lambda name: world.apps.state[8765].update(
        up_at=world.clock.t + 200)
    w = world.make()
    w.up.set_last_good(GOOD)
    w.run()
    later(world)
    status = w.run()
    assert world.git.restarts == ["main"] and world.git.resets() == []
    assert status["instances"]["main"]["status"] == "ok" and world.sent == []


def test_down_app_gets_the_start_grace_and_a_long_wait_before_any_rollback(world):
    """Not answering: first the grace (it may be starting), then one restart
    and a grace-long wait; only then, still down on HEAD, a rollback."""
    world.apps.add(8765, up=False)
    w = world.make()
    w.up.set_last_good(GOOD)
    w.run()
    assert world.git.restarts == [] and world.git.resets() == []  # starting?
    later(world)
    start = world.clock.t
    w.run()
    assert world.git.restarts[0] == "main"
    assert world.clock.t - start >= GRACE  # waited for it after the restart
    assert world.git.resets() == [GOOD]


def test_family_copy_down_without_restart_rolls_back_nobody(world, monkeypatch):
    """Mom's copy not answering, no way to restart it: everyone's code stays."""
    monkeypatch.setattr(watch, "WINDOWS", False)  # no Task Scheduler fallback
    world.apps.add(8865, up=False)
    mom = make_instance("mom", 8865)
    mom.restart_command = []
    w = world.make(instances=[mom])
    w.up.set_last_good(GOOD)
    w.run()
    later(world)
    w.run()
    assert world.git.resets() == []


def test_no_restart_or_rollback_while_an_update_holds_the_lock(world):
    """`--update` or a deploy may stop the app on purpose: hands off."""
    world.apps.add(8765, up=False)
    w = world.make()
    w.cfg["rollback"] = False
    assert w.up.acquire()
    w.run()
    later(world)
    w.run()
    assert world.git.restarts == []
    w.up.release()
    later(world)
    w.run()
    assert world.git.restarts == ["main"]


def test_rollback_happens_once_the_lock_is_free(world):
    """rolled_back is only recorded once the lock was taken."""
    world.apps.add(8765)
    w = world.make(smoke_fn=failing_smoke)
    w.up.set_last_good(GOOD)
    assert w.up.acquire()  # an update holds the lock this run
    w.run()
    w.up.release()
    assert world.git.resets() == []
    later(world, 31 * 60)  # the failed test conversation is retried
    w.run()
    assert world.git.resets() == [GOOD]


def test_rollback_lock_taken_after_the_restart_sets_nothing(world, monkeypatch):
    world.apps.add(8765)
    w = world.make(smoke_fn=failing_smoke)
    w.up.set_last_good(GOOD)
    monkeypatch.setattr(w.up, "acquire", lambda: False)  # someone took it mid-run
    w.run()
    assert "rolled_back" not in w.status["instances"]["main"]


def test_phone_address_failure_never_restarts_or_rolls_back(world):
    """Tailscale down isn't the app's fault: reported, not healed."""
    world.apps.add(8765)
    inst = make_instance()
    inst.public_url = "https://kyle.example.ts.net"
    world.apps.public[inst.public_url] = 502
    w = world.make(instances=[inst])
    w.up.set_last_good(GOOD)
    w.run()
    later(world)
    w.run()
    assert world.git.restarts == [] and world.git.resets() == []
    assert w.up.rejected() == []
    assert "phone address: HTTP 502" in world.sent[0]


def test_never_restarts_in_the_middle_of_a_turn(world):
    world.apps.add(8765)
    world.apps.state[8765]["busy"] = True
    w = world.make(smoke_fn=failing_smoke)
    w.cfg["rollback"] = False
    w.run()
    assert world.git.restarts == []
    world.apps.state[8765]["busy"] = False
    later(world, 31 * 60)  # the failed test conversation is retried
    w.run()
    assert world.git.restarts == ["main"]


def test_rollback_rereads_head_and_leaves_a_new_local_commit(world):
    """Kyle commits on top of HEAD while the restart runs: the stale HEAD is
    not reset (his commit would drop off the branch)."""
    world.apps.add(8765)
    world.git.ancestors |= {(GOOD, LOCAL), (NEW, LOCAL)}
    world.git.on_restart = lambda name: setattr(world.git, "head", LOCAL)
    w = world.make(smoke_fn=failing_smoke)
    w.up.set_last_good(GOOD)
    w.run()
    assert world.git.resets() == [] and world.git.head == LOCAL


def test_rollback_checkout_needs_head_on_the_bad_commit_and_the_branch(tmp_path):
    git = FakeGit(head=LOCAL)
    up = Updater(root=tmp_path, run=git)
    ok, why = watch.rollback_checkout(up, NEW, GOOD, "x")
    assert not ok and "moved" in why
    git.head = NEW
    git.branch = "kyles-branch"
    ok, why = watch.rollback_checkout(up, NEW, GOOD, "x")
    assert not ok and "kyles-branch" in why and git.resets() == []


def test_a_busy_side_port_is_inconclusive_not_a_rejection(tmp_path):
    """A leftover copy on the side port: it is stopped first; still busy, the
    gate can't tell (None), so the commit isn't rejected for good."""
    (tmp_path / ".env").write_text(f"WEB_TOKEN={TOKEN}\n")
    inst = make_instance("main", 8765)
    killed = []

    class Orphan:  # a candidate left running from a killed run
        def get(self, url, timeout=0):
            return Resp(200, {"ok": True, "version": OLD[:7]})

    def check(root, i, sha):
        return watch.candidate_check(root, i, sha, http=Orphan(), popen=None,
                                     sleep=lambda s: None,
                                     run=lambda cmd, cwd, timeout=0: killed.append(cmd) or (0, ""))
    ok, why = watch.gate_check(NEW, tmp_path, [inst], check=check)
    assert ok is None and "in use" in why
    assert killed and "--candidate" in killed[0][-1].replace("\\", "")


def test_a_commit_that_crashes_on_start_is_refused(tmp_path):
    """The side port was free and the new code exited before answering (an
    ImportError): that's the code's fault. Tried twice, then refused (not
    inconclusive, which would block every pull forever)."""
    (tmp_path / ".env").write_text(f"WEB_TOKEN={TOKEN}\n")
    started = []

    class Crash:
        def __init__(self, cmd, **kw):
            started.append(cmd)

        def poll(self):
            return 1

    class Nothing:
        def get(self, url, timeout=0):
            raise ConnectionError("nothing")

    def check(root, i, sha):
        return watch.candidate_check(root, i, sha, http=Nothing(), popen=Crash,
                                     sleep=lambda s: None)
    ok, why = watch.gate_check(NEW, tmp_path, [make_instance()], check=check)
    assert ok is False and "did not start (exit 1)" in why and len(started) == 2


def test_gate_retries_once_before_rejecting(tmp_path):
    results = iter([[watch.Check("text turn", False, "HTTP 500")], []])
    ok, _ = watch.gate_check(NEW, tmp_path, [make_instance()],
                             check=lambda root, i, sha: next(results))
    assert ok is True  # a fluke the second time round
    calls = []

    def always(root, i, sha):
        calls.append(sha)
        return [watch.Check("text turn", False, "HTTP 500")]
    ok, _ = watch.gate_check(NEW, tmp_path, [make_instance()], check=always)
    assert ok is False and len(calls) == 2


def no_side_port(root, inst, sha):
    raise AssertionError(f"{inst.name} has no web app to start on a side port")


def wake_only(root):
    """A wake-only Pi (or a family copy that never ran): no WEB_TOKEN."""
    (root / "config.toml").write_text("")
    (root / ".env").write_text("ANTHROPIC_API_KEY=x\n")


def test_no_web_token_means_no_web_app_to_gate(tmp_path):
    """Judge B1: candidate_check said "no WEB_TOKEN" (not outside), so the
    gate refused every commit on a machine without a web app. Now there is
    nothing to start on a side port, and the import/--check smoke decides."""
    wake_only(tmp_path)
    assert watch.discover(tmp_path, tailscale={}) == []
    smoked = []
    ok, why = watch.gate_check(NEW, tmp_path, check=no_side_port,
                               smoke=lambda root: smoked.append(root) or (True, "ok"))
    assert ok is True and why == "" and smoked == [tmp_path]
    ok, why = watch.gate_check(NEW, tmp_path, check=no_side_port,
                               smoke=lambda root: (False, "import failed: Traceback..."))
    assert ok is False and why == "no web app here; import failed"  # fixed words

    def crash(root):
        raise OSError("no python")
    ok, why = watch.gate_check(NEW, tmp_path, check=no_side_port, smoke=crash)
    assert ok is None and why == "no web app here; smoke crashed (OSError)"


def test_a_check_failure_without_a_web_app_is_inconclusive(tmp_path):
    """Judge N1: `--check` also fails for the machine's reasons (an
    unplugged mic, no audio device, no API key), so with no web app it
    must not refuse the commit for good; only an import failure does."""
    wake_only(tmp_path)
    ok, why = watch.gate_check(NEW, tmp_path, check=no_side_port, smoke=lambda root: (
        False, "--check failed: [FAIL] Microphone: no input device"))
    assert ok is None and why == "no web app here; --check failed"
    # the real smoke's words, both steps
    for out, verdict in (((1, "ImportError: x"), False), ((0, ""), None)):
        steps = iter([out, (1, "[FAIL] Microphone: no input device\n")])
        up = Updater(root=tmp_path, run=lambda cmd, cwd, timeout=0, s=steps: next(s))
        ok, _ = watch.gate_check(NEW, tmp_path, check=no_side_port,
                                 smoke=lambda root, u=up: u.smoke())
        assert ok is verdict


def test_a_hand_pull_on_a_pi_with_no_mic_is_not_undone(tmp_path):
    """Judge N1 on the wake host: the pull stays, nothing is rejected for
    good, and the check is tried again later (the hosts' backoff)."""
    wake_only(tmp_path)
    git = FakeGit(head=OLD)
    up = Updater(root=tmp_path, run=git, gate=lambda sha: watch.gate_check(
        sha, tmp_path, check=no_side_port,
        smoke=lambda root: (False, "--check failed: [FAIL] Microphone")))
    started = []
    h = host.Host("wake", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  popen=lambda cmd: started.append(cmd) or SimpleNamespace(
                      poll=lambda: None, terminate=lambda: None, wait=lambda timeout: 0),
                  sleep=lambda s: None)
    h.updater = up
    h.start()
    git.head = NEW
    h.restart_by_hand(NEW)
    assert git.resets() == [] and up.gated(NEW) is None and up.rejected() == []
    assert up.inconclusive(NEW)["count"] == 1 and len(started) == 1


def test_an_app_with_its_token_only_in_the_environment_is_reported(world):
    """Judge N2: ensure_web_token writes nothing to .env when WEB_TOKEN is
    in the environment, so discover() skipped a running app silently. Now
    it is a status entry, alerted once, shown while it lasts."""
    (world.root / ".env").write_text("ANTHROPIC_API_KEY=x\n")  # no WEB_TOKEN
    world.apps.add(8765)
    w = watch.Watch(world.root, http=world.apps, run=world.git,
                    updater=Updater(root=world.root, run=world.git, clock=world.clock),
                    alerts=watch.Alerts(world.sent.append), smoke_fn=passing_smoke,
                    clock=world.clock, sleep=world.clock.sleep,
                    settings=load_config(None, None))
    status = w.run()
    main = status["instances"]["main"]
    assert main["status"] == watch.UNCHECKED and main["what"] == watch.UNCHECKED_WHAT
    assert len(world.sent) == 1 and "WEB_TOKEN" in world.sent[0]
    assert watch.banner(status, now=world.clock())["issues"][0]["instance"] == "main"
    out = []
    watch.print_status(status, out.append)
    assert out[0].startswith("UNCHECKED") and "can't be checked" in out[0]
    world.clock.sleep(300)
    w.run()
    assert len(world.sent) == 1  # no alert storm
    world.apps.state.clear()  # nothing answers any more: nothing to report
    assert "main" not in w.run()["instances"]


def test_a_hand_pull_on_a_wake_only_pi_is_not_undone(tmp_path):
    """Judge B1: the wake host ran the gate on Kyle's pull, it failed for
    the missing token, and refuse() did `git reset --keep` back."""
    wake_only(tmp_path)
    git = FakeGit(head=OLD)
    up = Updater(root=tmp_path, run=git, gate=lambda sha: watch.gate_check(
        sha, tmp_path, check=no_side_port, smoke=lambda root: (True, "ok")))
    started = []
    h = host.Host("wake", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  popen=lambda cmd: started.append(cmd) or SimpleNamespace(
                      poll=lambda: None, terminate=lambda: None, wait=lambda timeout: 0),
                  sleep=lambda s: None)
    h.updater = up
    h.start()
    git.head = NEW  # Kyle's git pull
    h.restart_by_hand(NEW)
    assert git.resets() == [] and up.gated(NEW) is True
    assert len(started) == 2 and h.child_sha == NEW


def test_requirements_problem_reads_the_minimums(tmp_path):
    """Judge LOW 4: a requirements bump the venv never got."""
    from importlib.metadata import PackageNotFoundError

    from assistant.updater import requirements_problem

    assert requirements_problem(tmp_path) is None  # no requirements.txt
    (tmp_path / "requirements.txt").write_text(
        "# a comment\nanthropic>=0.77\nnumpy >= 1.24  # why\npiper-tts==1.8.0\ntzdata\n"
        'uvicorn[standard]>=0.27\nkokoro-onnx>=0.4; python_version < "3"\n'
        "pytest>=8.0\n")
    have = {"anthropic": "0.77.1", "numpy": "1.24.0", "uvicorn": "0.30.0",
            "pytest": "8.0"}

    def version(name):
        if name not in have:
            raise PackageNotFoundError(name)
        return have[name]
    assert requirements_problem(tmp_path, version) is None
    have["anthropic"] = "0.75.0"
    del have["pytest"]
    problem = requirements_problem(tmp_path, version)
    assert problem.startswith("requirements.txt isn't installed: anthropic 0.75.0 "
                              "(needs >=0.77), pytest not installed (needs >=8.0)")
    assert "pip install -r requirements.txt" in problem
    assert "python -m assistant --update" in problem


def test_the_gate_waits_for_requirements_to_be_installed(tmp_path):
    """Not a rejection (that would be for good): None, so nothing runs on
    it and it is checked again once pip has run."""
    ok, why = watch.gate_check(NEW, tmp_path, [make_instance()], check=no_side_port,
                               requirements=lambda root: "requirements.txt isn't "
                               "installed: anthropic 0.75 (needs >=0.77)")
    assert ok is None and "anthropic" in why


def test_deploy_refuses_until_requirements_are_installed(world):
    """install_family_tasks.ps1 -Restart runs deploy --no-pull: it never
    compared requirements."""
    (world.root / "requirements.txt").write_text("hey-claude-no-such-package>=1.0\n")
    world.apps.add(8766, version=NEW)
    w = world.make(instances=[make_instance("mom", 8766)], gate=lambda sha: (True, ""))
    out = []
    assert deploy.deploy(w, pull=False, restart=True, output_fn=out.append) == 1
    assert world.git.restarts == [] and world.git.resets() == []
    assert any(line.startswith("REFUSED: requirements.txt isn't installed: "
                               "hey-claude-no-such-package not installed") for line in out)


def test_deploy_restart_waits_for_a_turn_to_finish(world):
    """Judge LOW 5: -Restart restarted an app in the middle of someone's
    turn. It waits (bounded) for /healthz busy=false, else skips it."""
    world.apps.add(8766, version=NEW)
    world.apps.state[8766]["busy"] = True
    w = world.make(instances=[make_instance("mom", 8766)], gate=lambda sha: (True, ""))
    out = []
    deploy.deploy(w, pull=False, restart=True, output_fn=out.append)
    assert world.git.restarts == []
    assert any("in the middle of a turn; not restarted" in line for line in out)
    naps = []

    def nap(seconds):  # the turn ends after a few seconds
        naps.append(seconds)
        world.clock.sleep(seconds)
        if len(naps) == 3:
            world.apps.state[8766]["busy"] = False
    w.sleep = nap
    out.clear()
    assert deploy.deploy(w, pull=False, restart=True, output_fn=out.append) == 0, out
    assert world.git.restarts == ["mom"] and len(naps) >= 3


def subprocess_dead_pid() -> int:
    import subprocess
    import sys

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_a_killed_runs_lock_is_stale_at_once(tmp_path):
    """watch.lock / update.lock hold a PID; a dead one doesn't block for hours."""
    from assistant.updater import pid_alive

    dead = subprocess_dead_pid()
    assert not pid_alive(dead)
    up = Updater(root=tmp_path, run=FakeGit())
    up.lock_file.parent.mkdir(parents=True, exist_ok=True)
    up.lock_file.write_text(str(dead))
    assert not up.locked() and up.acquire()
    up.release()
    w = watch.Watch(tmp_path, instances=[], http=None, run=FakeGit(), updater=up,
                    alerts=watch.Alerts(), settings=load_config(None, None))
    w.lock_file.parent.mkdir(parents=True, exist_ok=True)
    w.lock_file.write_text(str(dead))
    assert w.acquire()
    w.release()
    up.lock_file.write_text(str(__import__("os").getpid()))  # a live owner
    assert up.locked() and not up.acquire()


def test_the_watch_task_outlives_the_worst_gate():
    from tests.test_docs import ROOT

    task = (ROOT / "scripts" / "install_watch_task.ps1").read_text()
    unit = (ROOT / "deploy" / "hey-claude-watch.service").read_text()
    assert "-ExecutionTimeLimit (New-TimeSpan -Hours 2)" in task
    assert "TimeoutStartSec=2h" in unit
    assert watch.LOCK_STALE_SECONDS > 2 * 3600


def test_reject_only_when_the_reset_worked(tmp_path):
    git = FakeGit()

    def failing_reset(cmd, cwd, timeout=0):
        if cmd[1:3] == ["reset", "--keep"]:
            return 1, "error: Entry 'config.toml' not uptodate. Cannot merge."
        return git(cmd, cwd, timeout)
    up = Updater(root=tmp_path, run=failing_reset)
    result = up.rollback(OLD, NEW, "x", requirements=False)
    assert result.status == "failed" and up.rejected() == []


def test_state_writes_dont_lose_each_other(tmp_path, monkeypatch):
    """The hosts, the watchdog and --update write update-state.json: two
    writes at once both land (rejected and last_good)."""
    import threading
    import time as _time

    up = Updater(root=tmp_path, run=FakeGit())
    read = Updater._state

    def slow_read(self):
        state = read(self)
        _time.sleep(0.05)  # widen the read-modify-write window
        return state
    monkeypatch.setattr(Updater, "_state", slow_read)
    threads = [threading.Thread(target=up.reject, args=(NEW,)),
               threading.Thread(target=up.set_last_good, args=(GOOD,)),
               threading.Thread(target=Updater(root=tmp_path, run=FakeGit()).mark_gated,
                                args=(OLD, True))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.setattr(Updater, "_state", read)
    assert NEW in up.rejected() and up.last_good() == GOOD and up.gated(OLD) is True


def test_family_restart_runs_deploy_from_the_project_folder():
    from tests.test_docs import ROOT

    text = (ROOT / "scripts" / "install_family_tasks.ps1").read_text()
    block = text[text.index("if ($Restart) {"):]
    assert block.index("Push-Location $repoDir") < block.index("-m assistant.deploy")
    assert "Pop-Location" in block


def test_check_session_never_queries_memory():
    from unittest.mock import MagicMock

    from assistant.chat import ClaudeChat

    memory = MagicMock()
    chat = ClaudeChat(client=MagicMock(), memory=memory)
    chat.check_mode = True
    assert chat.recall("what's on my calendar") == []
    memory.retrieve.assert_not_called()


def test_host_starts_the_watchdog_only_once_the_hand_pulled_app_answers():
    watched = []
    answers = {"version": OLD[:7]}
    h = host.Host("web", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  popen=lambda cmd: SimpleNamespace(poll=lambda: None, terminate=lambda: None,
                                                    wait=lambda timeout: 0),
                  health=lambda port: {"ok": True, "busy": False, **answers},
                  sleep=lambda s: None, health_wait=0)
    h.updater = HostUpdater(lambda sha: (True, ""))
    h.updater.head = lambda: OLD
    h.watcher = lambda: watched.append("watch")
    h.start()
    h.restart_by_hand(NEW)
    assert watched == []  # still answering with the old version: not yet
    answers["version"] = NEW[:7]
    h.updater.head = lambda: NEW
    h.health_wait = 60
    h.restart_by_hand(NEW)
    assert watched == ["watch"]


def test_state_writes_wait_for_another_process(tmp_path):
    """Another process (a host, --update) holding the state lock: a write
    waits until it lets go (here: that process exits, so its lock is stale)."""
    import subprocess
    import sys
    import time as _time

    up = Updater(root=tmp_path, run=FakeGit())
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
    lock = up.state_file.with_name(up.state_file.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(other.pid))
    import threading

    threading.Thread(target=other.wait, daemon=True).start()  # reaped when it exits
    start = _time.monotonic()
    up.set_last_good(GOOD)
    waited = _time.monotonic() - start
    assert waited >= 0.5 and up.last_good() == GOOD and not lock.exists()


# ----- round 3 ------------------------------------------------------------------------

def test_repeated_inconclusive_gate_alerts_once(world):
    """A go-live check that keeps failing to run (a busy side port) never
    goes live, so after a few tries the watchdog says so, once."""
    world.apps.add(8765, version=OLD)
    w = world.make(gate=lambda sha: (None, "Your app: go-live: side port 8865 is in use"))
    for _ in range(5):
        w.run()
        world.clock.t += 300
    stuck = [t for t in world.sent if "can't run" in t]
    assert len(stuck) == 1 and "side port 8865 is in use" in stuck[0]
    assert NEW[:7] in stuck[0] and w.up.rejected() == []
    w.up.mark_gated(NEW, True)  # it got through: the count starts over
    assert w.up.inconclusive(NEW) is None


def test_inconclusive_alert_after_half_an_hour_too(world):
    world.apps.add(8765, version=OLD)
    w = world.make(gate=lambda sha: (None, "Claude unreachable"))
    w.run()
    assert not [t for t in world.sent if "can't run" in t]
    world.clock.t += watch.INCONCLUSIVE_ALERT_SECONDS
    w.up.note_inconclusive(NEW, "Claude unreachable")  # (the host tried meanwhile)
    world.git.calls.clear()
    w.cfg["gate"] = False  # this run only records
    w.run()
    assert len([t for t in world.sent if "can't run" in t]) == 1


def test_host_backs_off_an_inconclusive_gate():
    """1, 5, 15, then 60 minutes between tries, not every minute forever."""
    h = host.Host("web", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  clock=lambda: 0.0)
    up = HostUpdater(lambda sha: (None, "side port in use"))
    counts = []
    up.note_inconclusive = lambda sha, why: counts.append(1) or {"count": len(counts)}
    h.updater = up
    waits = []
    for _ in range(5):
        h.gate_retry = 0.0
        assert h.gate_allows(NEW) is False
        waits.append(h.gate_retry)
    assert waits == [60, 300, 900, 3600, 3600]


def test_pkill_pattern_comes_after_double_dash(tmp_path, monkeypatch):
    monkeypatch.setattr(watch.sys, "platform", "linux")
    seen = []
    watch.kill_stale_candidate(tmp_path, make_instance(),
                               run=lambda cmd, cwd, t: seen.append(cmd) or (0, ""))
    cmd = seen[0]
    assert cmd[:3] == ["pkill", "-f", "--"] and cmd[3].startswith("--config config")


def test_failed_reset_clears_applied(tmp_path):
    """A refused commit whose reset failed stays checked out, but is no
    longer the updater's "applied" one, so no host restarts on it as such."""
    def run(cmd, cwd, timeout=0):
        if cmd[1:2] == ["reset"]:
            return 1, "error: Entry 'x' not uptodate. Cannot merge."
        return FakeGit()(cmd, cwd, timeout)
    up = Updater(root=tmp_path, run=run, gate=lambda sha: (False, "x"))
    up._save_state(applied=NEW)
    up.mark_gated(NEW, False)
    assert up.rollback(OLD, NEW, "go-live check failed").status == "failed"
    assert up.applied() is None


def test_host_never_restarts_on_a_refused_commit():
    started = []
    h = host.Host("web", ["python"], SimpleNamespace(write=lambda s: None, flush=lambda: None),
                  popen=lambda cmd: started.append(cmd) or SimpleNamespace(
                      poll=lambda: None, terminate=lambda: None, wait=lambda timeout: 0),
                  health=lambda port: {"ok": True, "busy": False, "version": NEW[:7]},
                  sleep=lambda s: None)
    h.updater = HostUpdater(lambda sha: (False, "x"), verdict=False)
    h.updater.head = lambda: OLD
    h.start()
    h.restart_on(NEW)
    assert len(started) == 1  # still the old one


def test_hung_update_doesnt_hide_a_down_app_forever(world):
    """The update lock held (an update hung): never healed, but reported
    once start_grace_seconds plus a margin have passed."""
    world.apps.add(8765, up=False)
    w = world.make()
    assert w.up.acquire()
    w.run()
    later(world)
    w.run()
    assert world.sent == []  # within grace + margin: maybe stopped on purpose
    later(world, watch.LOCKED_MARGIN)
    w.run()
    assert len(world.sent) == 1 and "Your app is failing" in world.sent[0]
    assert world.git.restarts == []  # still never healed while locked
    w.up.release()


def test_a_fresh_lock_survives_a_late_stale_clear(tmp_path):
    """Two processes found the same dead lock stale; the first already took
    the lock. The second's clear must not remove the first's fresh lock."""
    from assistant.updater import clear_stale, take_lock

    lock = tmp_path / "update.lock"
    dead = str(subprocess_dead_pid())
    lock.write_text(dead)
    assert take_lock(lock, 3600)  # the first process clears it and takes it
    mine = lock.read_text()
    clear_stale(lock, dead)  # the second, acting on what it saw before
    assert lock.exists() and lock.read_text() == mine
    assert not take_lock(lock, 3600)  # still held
    assert not list(tmp_path.glob("*.stale"))


# ----- follow-up: the judge's round-3 LOWs ------------------------------------------------

def test_watchdog_backs_off_an_inconclusive_gate_like_the_hosts(world):
    """Every 5-minute run used to rerun the whole go-live check while it
    couldn't tell (12 an hour); now the watchdog waits the hosts' backoff
    (updater.gate_backoff) after the last try, whoever made it."""
    world.apps.add(8765, version=OLD)
    tried = []
    w = world.make(gate=lambda sha: tried.append(world.clock.t) or (None, "side port in use"))
    start = world.clock.t
    for _ in range(12):  # an hour of watchdog runs
        w.run()
        world.clock.t += 300
    assert [round(t - start) for t in tried] == [0, 300, 600, 1500]
    assert w.up.inconclusive(NEW)["count"] == 4 and w.up.rejected() == []


def test_gate_backoff_is_one_table_for_hosts_and_watchdog(world):
    from assistant import updater

    assert [updater.gate_backoff(n) for n in (0, 1, 2, 3, 4, 9)] == [
        60, 60, 300, 900, 3600, 3600]
    assert not hasattr(host, "GATE_BACKOFF")  # no second copy to drift
    up = world.make().up
    assert up.gate_wait(NEW) == 0
    up.note_inconclusive(NEW, "x")
    up.note_inconclusive(NEW, "x")  # e.g. the host's second try
    world.clock.t += 100
    assert up.gate_wait(NEW) == 200 and up.gate_wait(OLD) == 0


def _updater_on(world, gate):
    """An Updater sharing the watchdog's state, finding NEW on stable."""
    def run(cmd, cwd, timeout=0):
        args = cmd[1:]
        if args[:2] == ["rev-parse", "origin/stable"]:
            return 0, NEW + "\n"
        if args[:2] == ["merge", "--ff-only"]:
            world.git.head = NEW
            return 0, ""
        return world.git(cmd, cwd, timeout)
    up = Updater(root=world.root, run=run, trust_unsigned=True, gate=gate,
                 clock=world.clock)
    up.smoke = lambda: (True, "ok")
    return up


def test_inconclusive_checks_on_the_update_path_alert_once(world):
    """The updater notes an inconclusive check on the new commit and rolls
    HEAD back, so the record is never on HEAD; it still alerts, once."""
    world.git.head = OLD
    world.git.remote = {NEW}
    world.apps.add(8765, version=OLD)
    up = _updater_on(world, lambda sha: (None, "Your app: go-live: Claude unreachable"))
    w = world.make()
    for _ in range(4):  # an hour of [update] checks, a watchdog run after each
        assert up.update().status == "rolled-back" and world.git.head == OLD
        w.run()
        world.clock.t += 15 * 60
    stuck = [t for t in world.sent if "can't run" in t]
    assert len(stuck) == 1 and NEW[:7] in stuck[0] and "Claude unreachable" in stuck[0]
    assert up.rejected() == []


def test_an_off_head_record_alerts_only_while_its_commit_is_pending(world):
    """Not for a commit refused or checked since, nor a hand pull undone after
    one try (count only, never the half-hour rule, off HEAD)."""
    world.apps.add(8765, version=OLD)
    world.git.head = OLD
    w = world.make()
    w.cfg["gate"] = False
    w.up.note_inconclusive(NEW, "x")
    world.clock.t += watch.INCONCLUSIVE_ALERT_SECONDS * 4
    w.run()
    for _ in range(watch.INCONCLUSIVE_ALERT_COUNT):
        w.up.note_inconclusive(NEW, "x")
    w.up._save_state(gated={"sha": NEW, "ok": True, "at": world.clock.t})
    w.run()
    w.up._save_state(gated=None, rejected=[NEW])
    w.run()
    assert not [t for t in world.sent if "can't run" in t]


def test_a_copy_killed_by_a_signal_is_inconclusive(tmp_path):
    """An OOM kill on the Pi (exit -9) says nothing about the code: not a
    "did not start", so the commit is neither retried at once nor refused."""
    (tmp_path / ".env").write_text(f"WEB_TOKEN={TOKEN}\n")
    started = []

    class Killed:
        def __init__(self, cmd, **kw):
            started.append(cmd)

        def poll(self):
            return -9

    class Nothing:
        def get(self, url, timeout=0):
            raise ConnectionError("nothing")

    def check(root, i, sha):
        return watch.candidate_check(root, i, sha, http=Nothing(), popen=Killed,
                                     sleep=lambda s: None)
    ok, why = watch.gate_check(NEW, tmp_path, [make_instance()], check=check)
    assert ok is None and "killed by signal 9" in why and len(started) == 1
