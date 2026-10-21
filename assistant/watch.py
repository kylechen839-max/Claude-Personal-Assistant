"""Health and function checks of every web app here: python -m assistant.watch.

Run every 5 minutes by the \\HeyClaude\\Watch task (scripts/install_watch_task.ps1)
or the Pi's hey-claude-watch.timer (scripts/install_watch.sh), and right after
the host supervisor restarts the app on new code (assistant/host.py).

Instances: Kyle's own (config.toml + .env) and every family copy
(config.<name>.toml + .env.<name>, *.local.toml excepted), the same rule as
scripts/install_family_tasks.ps1. `[watch] enabled = false` in an
instance's config leaves it out, and so does an env file without a
WEB_TOKEN (no web app: a wake-only Pi, a family copy that never started).

Each run, per instance:
  quick check   GET /healthz on its port; the page and every script and
                stylesheet it loads; the served version against the
                checkout's HEAD; the address the phone opens (the
                `tailscale serve` entry, or [webapp] public_origin).
  functional    after a restart or a new version, after `[watch]
                functional_hours`, or retried after `retry_minutes` once
                failed: smoke.py's realistic conversation (a quick question
                the fast model answers, text with a read-only tool, a
                follow-up, audio, hands-free voice, text) in a check
                session, which saves nothing and can't write.
  heal          still failing: its scheduled task / service is restarted
                once; if that doesn't fix it either and the checkout moved
                past the last version that passed ("last known good"), the
                checkout goes back to it (`git reset --keep`, as the
                updater's rollback; config, .env and data/ untouched), the
                apps restart, and the bad commit is never tried again.
  switch        an instance still on an older version than HEAD (a pull by
                hand): HEAD must first pass the go-live check, then the
                instance restarts on it (when idle). A HEAD that fails is
                undone the same way and the old version keeps running.

The go-live check (gate_check), also run by the updater before anything
restarts on an update and by scripts/deploy.ps1 / deploy.sh: each instance
is started a second time on a side port (its port + `side_port_offset`,
127.0.0.1) as `--candidate` (no history, no saved sign-ins, no background
jobs, its own log), and must pass the same conversation. With no instance
(no web app here) the updater's import and --check smoke decide; with
requirements.txt not installed nothing goes live until it is.

Results go to `[watch] status_file` (data/watch_status.json, written
privately) and logs/watch.log. Alerts only on a change (an app starts
failing, or recovers) and for one-off events (a version refused or rolled
back): in the owner's phone app (a banner from /api/state, Kyle's own
instance only) and to the dev team coordinator ([devteam], when set up).
An alert names the instance, the check and a fixed reason (an error class,
an HTTP status); never conversation text, never a secret.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MAIN = "main"  # Kyle's own instance: config.toml + .env
_FAMILY = re.compile(r"^config\.([A-Za-z0-9_-]+)\.toml$")
OK, FAILING = "ok", "failing"
# answers on its port, but its env file has no WEB_TOKEN: can't be checked
UNCHECKED = "unchecked"
UNCHECKED_WHAT = "running, but WEB_TOKEN isn't in its env file, so it can't be checked"
MAX_EVENTS = 20
# A run that holds the lock longer was killed: longer than the scheduled
# task's limit (2 h, install_watch_task.ps1 / hey-claude-watch.service),
# which covers the worst go-live check (each instance twice, ~10 min each).
LOCK_STALE_SECONDS = 3 * 3600
# Checks that never trigger a restart or a rollback: an older version still
# running is switched, and the phone address (Tailscale) is outside the app.
NOT_HEALED = ("version", "phone address")
# While an update holds the lock, an app that doesn't answer is reported
# after start_grace_seconds plus this (it's never healed meanwhile).
LOCKED_MARGIN = 15 * 60
WINDOWS = sys.platform == "win32"  # restart_command's Task Scheduler fallback
# A go-live check that keeps being inconclusive is reported after this many
# tries, or this long (Watch.record).
INCONCLUSIVE_ALERT_COUNT = 3
INCONCLUSIVE_ALERT_SECONDS = 30 * 60
HTTP_TIMEOUT = 15
SWITCH_WAIT = 240  # for a restarted instance to answer on the new version
_ASSET = re.compile(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"([^\"]+)\"", re.IGNORECASE)


# ----- instances ---------------------------------------------------------------

@dataclass
class Instance:
    name: str           # "main", or the family member's ("mom")
    config: str         # file name in the checkout
    env_file: str
    port: int
    label: str          # "Your app", "Mom's app"
    public_url: str = ""
    restart_command: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)  # its whole config

    @property
    def main(self) -> bool:
        return self.name == MAIN

    @property
    def local(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def instance_config(root: Path, config: str) -> dict:
    """An instance's settings, without loading its .env into this process."""
    from assistant.config import load_config

    return load_config(root / config, env_file=None, profile="")


def default_label(name: str) -> str:
    return "Your app" if name == MAIN else f"{name[:1].upper()}{name[1:]}'s app"


def discover(root: Path = ROOT, tailscale=None,
             untokened: list[Instance] | None = None) -> list[Instance]:
    """Kyle's instance and each config.<name>.toml with its .env.<name>
    (install_family_tasks.ps1's rule), minus any with [watch] enabled =
    false, and minus any whose env file has no WEB_TOKEN: it has no web app
    to check or gate (a wake-only Pi; a family copy whose app never ran,
    since the app writes its token on first start; those go to
    `untokened` when given: Watch looks whether one answers anyway, its
    token set only in the environment). `tailscale`: port -> https URL
    (tailscale_urls), or None to ask."""
    pairs = []
    if (root / "config.toml").exists():
        pairs.append((MAIN, "config.toml", ".env"))
    for path in sorted(root.glob("config.*.toml")):
        m = _FAMILY.match(path.name)
        if not m or path.name.endswith(".local.toml"):
            continue
        if (root / f".env.{m.group(1)}").exists():
            pairs.append((m.group(1), path.name, f".env.{m.group(1)}"))
        else:
            log.warning("%s has no .env.%s next to it; not checked", path.name, m.group(1))
    found = []
    for name, config, env_file in pairs:
        try:
            cfg = instance_config(root, config)
        except Exception as e:  # noqa: BLE001 - a broken file: say so, check the others
            log.error("Can't read %s (%s); not checked", config, type(e).__name__)
            continue
        watch = cfg["watch"]
        if not watch["enabled"]:
            continue
        port = int(cfg["webapp"]["port"])
        inst = Instance(name, config, env_file, port,
                        watch["label"] or default_label(name),
                        restart_command=list(watch["restart_command"] or []),
                        settings=cfg)
        if not env_token(root, env_file):
            log.info("%s has no WEB_TOKEN (no web app here, or it never ran); "
                     "not checked or gated", env_file)
            if untokened is not None:
                untokened.append(inst)
            continue
        found.append(inst)
    if any(i.settings["watch"]["public_url"] == "auto"
           and not i.settings["webapp"]["public_origin"] for i in found):
        tailscale = tailscale_urls() if tailscale is None else tailscale
    for i in found:
        i.public_url = public_url_for(i.settings, i.port, tailscale or {})
    return found


def public_url_for(cfg: dict, port: int, tailscale: dict) -> str:
    wanted = str(cfg["watch"]["public_url"] or "").strip()
    if wanted != "auto":
        return wanted.rstrip("/")
    return (str(cfg["webapp"]["public_origin"] or "").rstrip("/")
            or tailscale.get(port, ""))


def tailscale_urls(run=None) -> dict[int, str]:
    """Local port -> the https address `tailscale serve` gives it, e.g.
    8766 -> https://kyle.tail44aa39.ts.net:8443. {} without Tailscale."""
    run = run or _run
    code, out = run(["tailscale", "serve", "status", "--json"], ROOT, 20)
    if code != 0:
        return {}
    try:
        web = (json.loads(out or "{}") or {}).get("Web") or {}
    except ValueError:
        return {}
    urls = {}
    for hostport, conf in web.items():
        for handler in ((conf or {}).get("Handlers") or {}).values():
            target = urlsplit(str((handler or {}).get("Proxy") or ""))
            if target.hostname in ("127.0.0.1", "localhost") and target.port:
                host = hostport.removesuffix(":443")
                urls.setdefault(target.port, f"https://{host}")
    return urls


def env_token(root: Path, env_file: str) -> str | None:
    """WEB_TOKEN from `env_file`, read without loading it into this process."""
    from dotenv import dotenv_values

    path = root / env_file
    return (dotenv_values(path).get("WEB_TOKEN") or None) if path.exists() else None


def token_of(root: Path, inst: Instance) -> str | None:
    """Its WEB_TOKEN, read from its env file (never into this process)."""
    return env_token(root, inst.env_file)


# ----- commands ----------------------------------------------------------------

def _run(cmd: list[str], cwd: Path, timeout: float = 120) -> tuple[int, str]:
    flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=flags, check=False)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout:.0f} s"
    except OSError as e:
        return -1, f"could not run {cmd[0]}: {type(e).__name__}"


def restart_command(root: Path, inst: Instance) -> list[str]:
    """How to restart it: [watch] restart_command, else on Windows its Task
    Scheduler task (scripts/restart_instance.ps1), else nothing."""
    if inst.restart_command:
        return [str(a) for a in inst.restart_command]
    if WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", str(root / "scripts" / "restart_instance.ps1"),
                "-Name", inst.name]
    return []


def child_env(environ=os.environ) -> dict:
    """This process's environment minus anyone's keys and data paths: each
    instance reads its own from its env file (as its task does)."""
    from assistant.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS

    drop = set(ISOLATED_ENV_VARS) | set(SECRET_ENV_VARS)
    return {k: v for k, v in environ.items() if k not in drop}


# ----- checks ------------------------------------------------------------------

@dataclass
class Check:
    name: str
    ok: bool
    reason: str = ""  # fixed words only (smoke.classify): safe to show and send
    # inconclusive: says nothing about the code (a busy side port, a copy
    # that didn't start), so it never rejects a commit or rolls one back
    outside: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "reason": self.reason}


def _error(e: Exception) -> str:
    return type(e).__name__


def get_health(http, base: str) -> tuple[dict | None, str]:
    """(/healthz answer, "") or (None, the reason)."""
    try:
        r = http.get(base + "/healthz", timeout=HTTP_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - requests errors, a socket error
        return None, _error(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        body = r.json()
    except ValueError:
        return None, "not JSON"
    if not isinstance(body, dict) or not body.get("ok"):
        return None, "not ok"
    return body, ""


def check_page(http, base: str) -> Check:
    """The page and every script and stylesheet it loads answer 200."""
    try:
        r = http.get(base + "/", timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return Check("page", False, f"HTTP {r.status_code}")
        refs = sorted({ref for ref in _ASSET.findall(r.text)
                       if not urlsplit(ref).scheme and not ref.startswith("//")})
        for ref in refs:
            a = http.get(urljoin(base + "/", ref), timeout=HTTP_TIMEOUT)
            if a.status_code != 200:
                # the file name is ours (index.html), not anyone's text
                return Check("page", False, f"{Path(urlsplit(ref).path).name[:40]}: "
                             f"HTTP {a.status_code}")
    except Exception as e:  # noqa: BLE001 - any failure is a failed check
        return Check("page", False, _error(e))
    return Check("page", True)


def quick_check(inst: Instance, head: str | None, http) -> tuple[list[Check], dict | None]:
    """The 5-minute checks; also returns its /healthz answer (None if down)."""
    health, why = get_health(http, inst.local)
    checks = [Check("healthz", health is not None, why)]
    if health is None:
        return checks, None
    checks.append(check_page(http, inst.local))
    served = str(health.get("version") or "")
    if head and served not in ("", "unknown"):
        checks.append(Check("version", served == head[:7],
                            "" if served == head[:7] else "older version running"))
    if inst.public_url:
        public, why = get_health(http, inst.public_url)
        if public is None:
            checks.append(Check("phone address", False, why))
        elif str(public.get("version") or "") != served:
            checks.append(Check("phone address", False, "serves another version"))
        else:
            checks.append(Check("phone address", True))
    return checks, health


def functional_check(inst: Instance, token: str | None, base: str | None = None,
                     smoke_fn=None) -> list[Check]:
    """The test conversation (smoke.run_conversation) against `base` (its
    own port by default). Each failed step is a Check with its reason."""
    from assistant import smoke

    if not token:
        return [Check("conversation", False, "no WEB_TOKEN in its env file")]
    run = smoke_fn or smoke.run_smoke
    outcomes = run(base or inst.local, token, conversation=True)
    failed = [o for o in outcomes if o.status == "FAIL"]
    if not failed:
        return [Check("conversation", True)]
    return [Check(o.name, False, o.reason) for o in failed]


def external(checks: list[Check]) -> bool:
    """Did everything that failed fail for a reason outside this code (the
    internet or Claude down, rate limits, the API key)? Then the version
    running isn't to blame: nothing is rolled back or refused for it."""
    from assistant.smoke import EXTERNAL_REASONS

    failed = [c for c in checks if not c.ok]
    return bool(failed) and all(c.outside or c.reason in EXTERNAL_REASONS
                                for c in failed)


def describe(checks: list[Check]) -> str:
    """"healthz: ConnectionError; page: app.js: HTTP 404" (fixed words)."""
    return "; ".join(f"{c.name}: {c.reason or 'failed'}" for c in checks if not c.ok)


# ----- the go-live check on a side port -------------------------------------------

def candidate_config(config: dict, config_path: str | Path) -> dict:
    """cli --candidate: a copy that shares nothing it could break with the
    running instance: no history (not even opening the database), no saved
    sign-ins, no PC hub, no resumed lecture jobs (webapp.serve), its own log."""
    config["candidate"] = True
    config["history"]["enabled"] = False
    config["webapp"]["sessions_file"] = ""
    config["devteam"]["hub"] = False
    config["logging"]["file"] = f"logs/candidate-{Path(config_path).stem}.log"
    return config


def console_python(executable: str = sys.executable) -> str:
    from assistant.host import console_python as find

    return find(executable)


def port_answers(port: int, http) -> bool:
    try:
        http.get(f"http://127.0.0.1:{port}/healthz", timeout=3)
        return True
    except Exception:  # noqa: BLE001 - nothing answering
        return False


def kill_stale_candidate(root: Path, inst: Instance, run=None) -> None:
    """Stop a side-port copy of `inst` left over from a killed run (only a
    --candidate with this instance's config; never the app itself)."""
    run = run or _run
    marker = f"--config {inst.config} --env-file {inst.env_file} --candidate"
    if sys.platform == "win32":
        script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                  "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
                  + marker + "') } | ForEach-Object { Stop-Process -Id $_.ProcessId "
                  "-Force -ErrorAction SilentlyContinue }")
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        # "--": the pattern starts with dashes, which pkill would take for
        # options; ERE, so only the dots need escaping
        cmd = ["pkill", "-f", "--", marker.replace(".", r"\.")]
    log.warning("Side port of %s busy: stopping a leftover check copy", inst.name)
    run(cmd, root, 60)


def candidate_check(root: Path, inst: Instance, sha: str, http=None, popen=None,
                    smoke_fn=None, clock=time.monotonic, sleep=time.sleep,
                    wait: float | None = None, run=None) -> list[Check]:
    """Start `inst` on the checked-out code at its side port, run the test
    conversation against it, stop it. Empty list = passed. A busy side port
    (after stopping a leftover copy) is inconclusive (Check.outside), not a
    verdict on `sha`, and so is a copy killed by a signal (out of memory on
    the Pi, a shutdown); a copy that exits before answering on a free port is
    a failure (the new code doesn't start; gate_check tries it twice)."""
    http = http or _session()
    popen = popen or subprocess.Popen
    port = inst.port + int(inst.settings["watch"]["side_port_offset"])
    wait = float(inst.settings["watch"]["candidate_seconds"] if wait is None else wait)
    token = token_of(root, inst)
    if not token:
        return [Check("go-live", False, "no WEB_TOKEN in its env file")]
    if port_answers(port, http):
        kill_stale_candidate(root, inst, run)
        sleep(3)
        if port_answers(port, http):
            return [Check("go-live", False, f"side port {port} is in use", outside=True)]
    cmd = [console_python(), "-m", "assistant", "--mode", "web", "--host", "127.0.0.1",
           "--port", str(port), "--config", inst.config, "--env-file", inst.env_file,
           "--candidate"]
    log.info("Go-live check: starting %s's copy of %s on port %d", inst.name, sha[:7], port)
    flags = 0x08000000 if sys.platform == "win32" else 0
    child = popen(cmd, cwd=root, env=child_env(), stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, creationflags=flags)
    try:
        deadline = clock() + wait
        while True:
            code = child.poll()
            if code is not None and code < 0:  # killed by a signal (the Pi's OOM killer)
                return [Check("go-live", False, f"killed by signal {-code}", outside=True)]
            if code is not None:
                return [Check("go-live", False, f"did not start (exit {code})")]
            health, _ = get_health(http, f"http://127.0.0.1:{port}")
            if health is not None:
                if str(health.get("version")) != sha[:7]:
                    return [Check("go-live", False, "side port serves another version")]
                break
            if clock() >= deadline:
                return [Check("go-live", False, f"no answer in {wait:.0f} s")]
            sleep(2)
        failed = [c for c in functional_check(inst, token, f"http://127.0.0.1:{port}",
                                              smoke_fn) if not c.ok]
        return failed
    finally:
        _stop(child)


def _stop(child) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=20)


def import_smoke(root: Path) -> tuple[bool, str]:
    """The updater's smoke: import, then `python -m assistant --check`."""
    from assistant.updater import Updater

    return Updater(root, python=console_python()).smoke()


def gate_check(sha: str, root: Path = ROOT, instances: list[Instance] | None = None,
               check=candidate_check, smoke=import_smoke,
               requirements=None) -> tuple[bool | None, str]:
    """Does `sha` (checked out now) pass the test conversation for every
    instance, each on its side port? (ok, why); ok is None when it only
    failed for outside reasons (external()): try again later, don't refuse
    it. Doesn't take the update lock: the caller holds it, so nothing
    restarts on `sha` meanwhile.

    requirements.txt not installed (updater.requirements_problem) is None
    too: nothing runs on `sha` until it is, and then it is checked as usual.
    No web app here (discover() skips an instance without a WEB_TOKEN, e.g.
    a wake-only Pi): there is nothing to start on a side port, so `sha`
    passes on the import and --check smoke alone, run here (a pull by hand
    never ran it)."""
    from assistant.updater import (
        SMOKE_CHECK_FAILED,
        SMOKE_IMPORT_FAILED,
        requirements_problem,
    )

    problem = (requirements or requirements_problem)(root)
    if problem:
        log.warning("Go-live check of %s can't run: %s", sha[:7], problem)
        return None, problem
    instances = discover(root, tailscale={}) if instances is None else instances
    if not instances:
        try:
            ok, why = smoke(root)
        except Exception as e:  # never take the host down; says nothing about `sha`
            log.exception("Go-live smoke of %s crashed", sha[:7])
            return None, f"no web app here; smoke crashed ({_error(e)})"
        if ok:
            log.info("Go-live check: no web app here; import and --check smoke "
                     "passed on %s", sha[:7])
            return True, ""
        # the output tail goes to the log only; alerts get fixed words
        log.warning("Go-live check: no web app here; smoke FAILED on %s: %s",
                    sha[:7], why)
        if str(why).startswith(SMOKE_IMPORT_FAILED):  # the code's fault
            return False, f"no web app here; {SMOKE_IMPORT_FAILED}"
        # --check also fails for this machine's reasons (no mic, no audio
        # device, no API key): inconclusive, tried again with the backoff
        return None, f"no web app here; {SMOKE_CHECK_FAILED}"
    problems, outside = [], True
    for inst in instances:
        failed = _checked(check, root, inst, sha)
        if failed and not external(failed):  # once more: never reject on a fluke
            log.warning("Go-live check: %s failed on %s (%s); trying once more",
                        inst.name, sha[:7], describe(failed))
            failed = _checked(check, root, inst, sha)
        if failed:
            outside = outside and external(failed)
            problems.append(f"{inst.label}: {describe(failed)}")
            log.warning("Go-live check: %s FAILED on %s: %s", inst.name, sha[:7],
                        describe(failed))
        else:
            log.info("Go-live check: %s passed on %s", inst.name, sha[:7])
    if problems and outside:
        return None, "; ".join(problems)
    return (not problems), "; ".join(problems)


def _checked(check, root, inst, sha) -> list[Check]:
    try:
        return check(root, inst, sha)
    except Exception as e:  # never take the caller down
        log.exception("Go-live check of %s crashed", inst.name)
        return [Check("go-live", False, _error(e), outside=True)]


def updater_gate(root: Path = ROOT) -> Callable[[str], tuple[bool | None, str]] | None:
    """The Updater's gate (host.py, --update), or None with [watch] gate off."""
    try:
        cfg = instance_config(root, "config.toml")
    except Exception:  # noqa: BLE001 - nothing answering
        return None
    if not cfg["watch"]["gate"]:
        return None
    return lambda sha: gate_check(sha, root)


# ----- going back to the last version that passed ------------------------------------

def rollback_checkout(up, bad: str, to: str | None, why: str) -> tuple[bool, str]:
    """`git reset --keep` from `bad` (HEAD) to `to`, as Updater.rollback
    does: tracked files only (config.toml edits kept; .env, data/ and logs/
    untouched), and `bad` is never applied again. Refused when `to` isn't
    an ancestor of HEAD, or HEAD has commits that only exist here (they
    would drop off the branch), or when HEAD or the branch moved since the
    caller looked (re-read here, under the caller's update lock)."""
    head = up.head()
    if head != bad:
        return False, f"HEAD moved to {(head or '?')[:7]} meanwhile; left as is"
    branch = up.current_branch()
    if branch != up.local_branch:
        return False, (f"checked out {branch or 'a detached HEAD'}, not "
                       f"{up.local_branch}; left as is")
    if not to or to == bad:
        return False, "no earlier version that passed to go back to"
    if up.git("merge-base", "--is-ancestor", to, bad, timeout=60)[0] != 0:
        return False, f"{to[:7]} isn't an ancestor of {bad[:7]}; left as is"
    code, remote = up.git("branch", "-r", "--contains", bad, timeout=60)
    if code != 0 or not remote.strip():
        return False, f"{bad[:7]} isn't on GitHub (local commits); left as is"
    result = up.rollback(to, bad, why)
    return result.status == "rolled-back", result.detail


def resolve(up, short: str | None) -> str | None:
    if not short or short == "unknown":
        return None
    code, out = up.git("rev-parse", "--verify", "--quiet", f"{short}^{{commit}}", timeout=30)
    return out.strip() if code == 0 and out.strip() else None


# ----- status, alerts, banner --------------------------------------------------------

def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec="seconds")


def hhmm(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().strftime("%H:%M")


def read_status(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(path: Path, status: dict) -> None:
    from assistant.tools.google_auth import write_private

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(status, indent=2))


def banner(status: dict, now: float | None = None, stale_minutes: float = 30) -> dict | None:
    """What the owner's phone app shows: {"issues": [...], "stale": bool,
    "events": [...]}, or None when all is well."""
    now = time.time() if now is None else now
    issues = [{"instance": name, "label": i.get("label", name),
               "since": i.get("since_iso", ""), "what": i.get("what", "")}
              for name, i in sorted((status.get("instances") or {}).items())
              if i.get("status") in (FAILING, UNCHECKED)]
    checked = float(status.get("checked") or 0)
    stale = bool(status) and now - checked > stale_minutes * 60
    events = [e for e in (status.get("events") or [])
              if now - float(e.get("at") or 0) < 24 * 3600][-3:]
    if not issues and not stale and not events:
        return None
    return {"issues": issues, "stale": stale,
            "checked": status.get("checked_iso", ""),
            "events": [{"at": e.get("at_iso", ""), "text": e.get("text", "")}
                       for e in events]}


def banner_source(config: dict, env_file) -> Callable[[], dict | None] | None:
    """For webapp.serve: the banner of this machine's status file, on Kyle's
    own instance only (started with .env; never a family copy, never the
    side-port candidate)."""
    w = config.get("watch") or {}
    if not w.get("banner") or config.get("candidate") or config.get("profile") \
            or Path(str(env_file)).name != ".env":
        return None
    path = Path(w["status_file"])
    stale = float(w["stale_minutes"])

    def health():
        return banner(read_status(path), stale_minutes=stale) if path.exists() else None
    return health


class Alerts:
    """Where alerts go: the log (always) and the dev team coordinator."""

    def __init__(self, send: Callable[[str], None] | None = None):
        self.send = send
        self.sent: list[str] = []

    def __call__(self, text: str) -> None:
        log.warning("ALERT: %s", text)
        self.sent.append(text)
        if self.send is None:
            return
        try:
            self.send(text)
        except Exception as e:  # noqa: BLE001 - the coordinator being unreachable isn't fatal
            log.warning("Alert not sent to the coordinator: %s", type(e).__name__)


def coordinator_sender(root: Path, cfg: dict) -> Callable[[str], None] | None:
    """Messages to the dev team coordinator through devteam.CoordinatorLink,
    when [devteam] is set up here and [watch] coordinator is on."""
    team = cfg["devteam"]
    if not (cfg["watch"]["coordinator"] and team["enabled"] and team["coordinator_session"]):
        return None
    from assistant.devteam import link_from_config

    link = link_from_config(cfg, root / team["state_file"])

    def send(text: str) -> None:
        link.send(text + "\n\nPlease find the cause, fix it and open a PR "
                  "(the watchdog keeps checking; logs are on Kyle's PC in logs/).",
                  source="watch")
    return send


# ----- one run -----------------------------------------------------------------------

def _session():
    import requests

    return requests.Session()


class Watch:
    def __init__(self, root: Path = ROOT, instances: list[Instance] | None = None,
                 http=None, run=None, updater=None, alerts: Alerts | None = None,
                 smoke_fn=None, gate=None, clock=time.time, sleep=time.sleep,
                 settings: dict | None = None):
        self.root = Path(root)
        self.settings = settings or instance_config(self.root, "config.toml")
        self.cfg = self.settings["watch"]
        self.instances = instances
        self.untokened: list[Instance] = []  # found without a WEB_TOKEN (discover)
        self.http = http or _session()
        self.run_cmd = run or _run
        if updater is None:
            from assistant.updater import Updater

            updater = Updater.from_config(self.settings["update"], root=self.root,
                                          run=self.run_cmd, say=log.info)
        self.up = updater
        self.alerts = alerts or Alerts(coordinator_sender(self.root, self.settings))
        self.smoke_fn = smoke_fn
        self.gate = gate or (lambda sha: gate_check(sha, self.root, self.instances))
        self.clock = clock
        self.sleep = sleep
        self.status_file = self.root / self.cfg["status_file"]
        self.status = read_status(self.status_file)
        self.lock_file = self.root / "logs" / "watch.lock"

    # --- lock: runs never overlap (a gate can outlast the 5-minute timer) ---
    def acquire(self) -> bool:
        """The run lock (with this PID; a killed run's lock is stale at once)."""
        from assistant.updater import take_lock

        return take_lock(self.lock_file, LOCK_STALE_SECONDS, self.clock)

    def release(self) -> None:
        try:
            self.lock_file.unlink()
        except OSError:
            pass

    # --- helpers ---
    def head(self) -> str | None:
        return self.up.head()

    def event(self, kind: str, text: str) -> None:
        now = self.clock()
        events = (self.status.get("events") or []) + [
            {"at": now, "at_iso": iso(now), "kind": kind, "text": text}]
        self.status["events"] = events[-MAX_EVENTS:]
        self.alerts(text)

    def restart(self, inst: Instance) -> bool:
        cmd = restart_command(self.root, inst)
        if not cmd:
            log.warning("No way to restart %s here ([watch] restart_command)", inst.name)
            return False
        log.info("Restarting %s", inst.name)
        code, _ = self.run_cmd(cmd, self.root, 180)
        if code != 0:
            log.warning("Restarting %s failed (exit %s)", inst.name, code)
        return code == 0

    def host_runs(self, inst: Instance) -> bool:
        """Kyle's instance under a live host supervisor, which restarts it
        on HEAD moves itself (after the go-live check)."""
        return inst.main and "web" in self.up.hosts_running()

    def wait_for(self, inst: Instance, version: str | None, timeout: float = SWITCH_WAIT):
        deadline = self.clock() + timeout
        while True:
            health, _ = get_health(self.http, inst.local)
            if health and (not version or str(health.get("version")) == version[:7]):
                return health
            if self.clock() >= deadline:
                return None
            self.sleep(3)

    def functional_due(self, inst: Instance, health: dict, force: bool) -> str | None:
        st = (self.status.get("instances") or {}).get(inst.name) or {}
        f = st.get("functional") or {}
        now = self.clock()
        if force:
            return "requested"
        if f.get("version") != health.get("version"):
            return "new version"
        if health.get("started") and st.get("started") \
                and float(health["started"]) != float(st["started"]):
            return "restarted"
        if not f.get("ok") and now - float(f.get("at") or 0) >= float(
                self.cfg["retry_minutes"]) * 60:
            return "retry"
        if now - float(f.get("at") or 0) >= float(self.cfg["functional_hours"]) * 3600:
            return "periodic"
        return None

    def check(self, inst: Instance, head: str | None,
              force: bool) -> tuple[list[Check], dict | None]:
        checks, health = quick_check(inst, head, self.http)
        # An older version still running is switched first (then checked);
        # one whose successor was refused keeps being checked as it is.
        refused = self.up.gated(head) is False
        if health is None or not all(c.ok or (c.name == "version" and refused)
                                     for c in checks):
            return checks, health
        why = self.functional_due(inst, health, force)
        if why:
            log.info("Test conversation with %s (%s)", inst.name, why)
            result = functional_check(inst, token_of(self.root, inst), smoke_fn=self.smoke_fn)
            ok = all(c.ok for c in result)
            st = self._state(inst)
            st["functional"] = {"at": self.clock(), "version": health.get("version"),
                                "ok": ok, "what": describe(result)}
            checks += [c for c in result if not c.ok] or [Check("conversation", True)]
            if ok and head and health.get("version") == head[:7]:
                self.up.set_last_good(head)
        return checks, health

    def _state(self, inst: Instance) -> dict:
        return self.status.setdefault("instances", {}).setdefault(inst.name, {})

    # --- new code that isn't live everywhere yet ---
    def switch(self, instances: list[Instance], head: str, behind: list[Instance]) -> None:
        """HEAD is newer than what `behind` run (a pull by hand): it must pass
        the go-live check first; then they restart on it, else HEAD is undone."""
        gated = self.up.gated(head)
        if gated is None and head == self.up.last_good():
            gated = True
        if gated is None and self.cfg["gate"]:
            wait = self.up.gate_wait(head)
            if wait > 0:  # it couldn't tell last time: the hosts' backoff
                log.info("Go-live check of %s is due again in %d min", head[:7],
                         wait // 60 or 1)
                return
            if not self.up.acquire():
                log.info("An update is running; switching later")
                return
            try:
                ok, why = self.gate(head)
                if ok is None:
                    self.up.note_inconclusive(head, why)
                    log.warning("Go-live check of %s couldn't tell (%s); trying "
                                "again later", head[:7], why)
                    return
                self.up.mark_gated(head, ok)
                if not ok:
                    self.refuse(head, behind, why)
                    return
            finally:
                self.up.release()
        elif gated is False:
            return  # refused before; the version check stays red until undone
        for inst in behind:
            if self.host_runs(inst):
                continue  # its host restarts it once the check passed
            h, _ = get_health(self.http, inst.local)
            if h and h.get("busy"):
                log.info("%s is busy; switching it later", inst.name)
                continue
            log.info("Switching %s to %s", inst.name, head[:7])
            if self.restart(inst):
                self.wait_for(inst, head)

    def refuse(self, head: str, behind: list[Instance], why: str,
               to: str | None = None) -> None:
        """HEAD failed the go-live check: back to `to`, else what's running,
        else the last version that passed, so a crash restart can't bring
        it up."""
        running = to or resolve(self.up, self.served_version(behind)) \
            or self.up.last_good()
        undone, detail = rollback_checkout(self.up, head, running,
                                           "go-live check failed")
        self.event("refused", f"New version {head[:7]} failed its go-live check "
                   f"({why}), so it was not switched on; "
                   + (f"the checkout is back on {running[:7]}." if undone
                      else f"the checkout was left as is: {detail}."))

    def served_version(self, instances: list[Instance]) -> str | None:
        """The version the first of `instances` that answers is running."""
        for inst in instances:
            health, _ = get_health(self.http, inst.local)
            if health and health.get("version") not in (None, "", "unknown"):
                return str(health["version"])
        return None

    # --- the run ---
    def run(self, force_functional: bool = False) -> dict:
        if not self.acquire():
            log.info("Another check is running; skipped")
            return self.status
        try:
            return self._run(force_functional)
        finally:
            self.release()

    def _run(self, force: bool) -> dict:
        if self.instances is None:
            self.untokened = []
            self.instances = discover(self.root, untokened=self.untokened)
        instances = self.instances
        unchecked = [i for i in self.untokened
                     if get_health(self.http, i.local)[0] is not None]
        for inst in unchecked:
            log.warning("%s answers on port %d, but WEB_TOKEN isn't in %s, so it "
                        "can't be checked", inst.name, inst.port, inst.env_file)
        head = self.head()
        results = {}
        for inst in instances:
            checks, health = self.check(inst, head, force)
            results[inst.name] = (checks, health)
        behind = [i for i in instances
                  if any(c.name == "version" and not c.ok for c in results[i.name][0])]
        if behind and head:
            self.switch(instances, head, behind)
            head = self.head()
            for inst in behind:
                results[inst.name] = self.check(inst, head, False)
        for inst in instances:
            results[inst.name] = self.heal(inst, head, *results[inst.name])
        head = self.head()
        self.record(instances, results, head, unchecked)
        write_status(self.status_file, self.status)
        return self.status

    def heal(self, inst: Instance, head: str | None, checks: list[Check],
             health: dict | None) -> tuple[list[Check], dict | None]:
        """Restart once per failure; then back to the last good version.

        Never while an update or deploy holds the update lock (it may have
        stopped the app on purpose), never in the middle of someone's turn,
        and an app that doesn't answer gets `start_grace_seconds` first (a
        host restart, a reboot, a slow model load). A rollback needs a
        restart that succeeded and didn't help: the failing code is then
        really the checkout's."""
        st = self._state(inst)
        st.pop("starting", None)
        failed = [c for c in checks if not c.ok and c.name not in NOT_HEALED]
        if not failed:
            st.pop("down_since", None)
            return checks, health
        now = self.clock()
        grace = float(self.cfg["start_grace_seconds"])
        since = float(st.setdefault("down_since", now)) if health is None else now
        if self.up.locked():
            # stopped on purpose, most likely: no alert yet; but a hung update
            # mustn't hide an app that stays down
            if health is None and now - since < grace + LOCKED_MARGIN:
                st["starting"] = True
            log.info("%s failing, but an update or deploy is running; not healing "
                     "it now", inst.name)
            return checks, health
        if health is None:
            if now - since < grace:
                st["starting"] = True  # not an outage yet: no alert, no restart
                log.info("%s not answering for %.0f s; may be starting", inst.name,
                         now - since)
                return checks, health
        else:
            st.pop("down_since", None)
            if health.get("busy"):
                log.info("%s failing but busy (a turn running); healing it later",
                         inst.name)
                return checks, health
        log.warning("%s failing: %s", inst.name, describe(failed))
        if not st.get("restart_tried"):
            st["restart_tried"] = now
            if self.restart(inst):
                st["restarted"] = now  # only a restart that ran counts
                self.wait_for(inst, None, timeout=grace)
                checks, health = self.check(inst, head, force=True)
                failed = [c for c in checks if not c.ok and c.name not in NOT_HEALED]
                if not failed:
                    return checks, health
        if external(failed):
            log.warning("%s: failing for reasons outside this code; not rolling "
                        "back", inst.name)
            return checks, health
        # Back to the last good version only if the failing code is HEAD's:
        # it answers with HEAD's version, or it was restarted on the
        # checkout and still doesn't answer after the grace period.
        on_head = health is None or str(health.get("version")) == (head or "")[:7]
        if not (self.cfg["rollback"] and st.get("restarted") and not st.get("rolled_back")
                and head and on_head):
            return checks, health
        good = self.up.last_good()
        if not self.up.acquire():
            return checks, health  # an update holds it: next run
        try:
            st["rolled_back"] = now
            undone, detail = rollback_checkout(
                self.up, head, good, f"{inst.name} failing after a restart")
        finally:
            self.up.release()
        if not undone:
            log.warning("Not rolling back: %s", detail)
            return checks, health
        self.event("rolled_back", f"{inst.label} kept failing on {head[:7]} "
                   f"({describe(failed)}); rolled back to {good[:7]}, the last "
                   "version that passed, and restarted.")
        for other in self.instances:
            if not self.host_runs(other):
                self.restart(other)
        self.wait_for(inst, good, timeout=grace)
        return self.check(inst, good, force=True)

    def alert_inconclusive(self, head: str | None, now: float) -> None:
        """A go-live check that keeps failing to run means new code never goes
        live: say so once. The record may be on a commit other than HEAD (the
        updater notes it, then rolls back and retries at its next check); such
        a one counts only while it is still pending (neither refused nor
        checked since), and only by its count: a hand pull undone after one
        try isn't stuck."""
        stuck = self.up.last_inconclusive()
        if not stuck or stuck.get("alerted"):
            return
        sha = str(stuck.get("sha") or "")
        tries = int(stuck.get("count") or 0) >= INCONCLUSIVE_ALERT_COUNT
        if sha and sha == head:
            due = tries or now - float(stuck.get("first") or now) >= INCONCLUSIVE_ALERT_SECONDS
        else:
            due = tries and bool(sha) and self.up.gated(sha) is None
        if not due:
            return
        self.up.mark_inconclusive_alerted(sha)
        self.event("inconclusive", f"The go-live check of {sha[:7]} can't run "
                   f"({stuck.get('why') or 'no reason'}), so it isn't live yet; "
                   "the running version stays.")

    def record(self, instances, results, head, unchecked=()) -> None:
        now = self.clock()
        self.status.update(checked=now, checked_iso=iso(now),
                           head=(head or "")[:7],
                           last_good=(self.up.last_good() or "")[:7])
        known = self.status.setdefault("instances", {})
        for gone in set(known) - {i.name for i in [*instances, *unchecked]}:
            known.pop(gone)
        for inst in unchecked:  # reported once, then shown while it lasts
            st = self._state(inst)
            if st.get("status") != UNCHECKED:
                st.clear()
                st.update(status=UNCHECKED, since=now, since_iso=iso(now))
                self.alerts(f"{inst.label} is {UNCHECKED_WHAT} (add its "
                            f"WEB_TOKEN to {inst.env_file}).")
            st.update(label=inst.label, port=inst.port, what=UNCHECKED_WHAT,
                      checks=[], version="")
        self.alert_inconclusive(head, now)
        for inst in instances:
            checks, health = results[inst.name]
            st = self._state(inst)
            was = st.get("status")
            # an older version still running isn't an outage (it's switched
            # once its successor passes), unless that successor was refused
            bad = [c for c in checks if not c.ok and (
                c.name != "version" or self.up.gated(head) is False)]
            st.update(label=inst.label, port=inst.port, checks=[c.to_dict() for c in checks],
                      version=(health or {}).get("version", ""),
                      started=(health or {}).get("started", st.get("started")))
            if st.get("starting"):  # within start_grace_seconds: wait and see
                log.info("%s: not answering yet (starting?)", inst.name)
                continue
            log.info("%s: %s%s", inst.name, "OK" if not bad else "FAILING",
                     f" ({describe(bad)})" if bad else "")
            if bad:
                st["what"] = describe(bad)
                if was != FAILING:
                    st.update(status=FAILING, since=now, since_iso=iso(now))
                    self.alerts(f"{inst.label} is failing since {hhmm(now)}: "
                                f"{st['what']} (version {st['version'] or '?'}).")
            else:
                if was == FAILING:
                    self.alerts(f"{inst.label} recovered at {hhmm(now)} (failing "
                                f"since {hhmm(float(st.get('since') or now))}).")
                st.update(status=OK, since=st.get("since") if was == OK else now,
                          since_iso=st.get("since_iso") if was == OK else iso(now),
                          what="")
                for key in ("restarted", "restart_tried", "rolled_back", "down_since"):
                    st.pop(key, None)


# ----- command line --------------------------------------------------------------

def setup_watch_logging(root: Path) -> None:
    from assistant.config import DEFAULTS, setup_logging

    setup_logging({"logging": {**DEFAULTS["logging"],
                               "file": str(root / "logs" / "watch.log")}})


def print_status(status: dict, output_fn=print) -> None:
    for name, i in sorted((status.get("instances") or {}).items()):
        output_fn(f"{i.get('status', '?').upper():8} {i.get('label', name)} "
                  f"(port {i.get('port', '?')}, version {i.get('version') or '?'})"
                  + (f": {i['what']}" if i.get("what") else ""))
    for e in (status.get("events") or [])[-5:]:
        output_fn(f"  {e.get('at_iso', '')} {e.get('text', '')}")


def main(argv=None, output_fn=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant.watch",
                                     description="Check every web app instance here")
    parser.add_argument("--functional", action="store_true",
                        help="run the test conversation now, due or not")
    parser.add_argument("--status", action="store_true",
                        help="print the last results and exit")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    setup_watch_logging(ROOT)
    if args.status:
        cfg = instance_config(ROOT, "config.toml")["watch"]
        print_status(read_status(ROOT / cfg["status_file"]), output_fn)
        return 0
    status = Watch(ROOT).run(force_functional=args.functional)
    print_status(status, output_fn)
    failing = [i for i in (status.get("instances") or {}).values()
               if i.get("status") == FAILING]
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
