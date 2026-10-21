"""One safe command to put new code live: python -m assistant.deploy.

scripts/deploy.ps1 (Windows) and scripts/deploy.sh (Pi) run it; use it
instead of "git pull" + restarting by hand. Holding the update lock (so
the host supervisors and the watchdog leave HEAD alone meanwhile):

  1. pull: `git fetch` + `git merge --ff-only` from the branch's upstream
     (--no-pull: take what is checked out now, e.g. after a pull by hand);
  2. go-live check (assistant/watch.py gate_check): every instance's test
     conversation on its side port (its port + [watch] side_port_offset);
  3. failed: the checkout goes back (`git reset --keep`) to what was
     running, the commit is never taken again, the coordinator and the
     phone banner are told, and nothing restarts: the old version keeps
     running. Passed: the instances restart on it (the host supervisor
     restarts Kyle's own app, health-checked with rollback; the others
     through their scheduled tasks, each once no turn is running on it,
     waiting up to IDLE_WAIT, else it is skipped and named), and once they
     all answer on it the watchdog runs the test conversation against each
     live app, restarting or rolling back to the last version that passed
     if one fails.

Never touches config, .env or data/. A pull that changes requirements.txt
is undone unless --requirements is given (then pip runs first); with
requirements.txt not installed (a pull by hand, then --no-pull) nothing
switches until pip has run (updater.requirements_problem).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from assistant import watch
from assistant.updater import requirements_problem

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
IDLE_WAIT = 120  # seconds an app in the middle of a turn gets before it is skipped


def running_versions(w: watch.Watch, instances) -> dict[str, str]:
    out = {}
    for inst in instances:
        health, _ = watch.get_health(w.http, inst.local)
        out[inst.name] = str((health or {}).get("version") or "")
    return out


def wait_until_idle(w: watch.Watch, inst, timeout: float = IDLE_WAIT) -> bool:
    """Wait (up to `timeout`) for no turn or voice conversation on `inst`
    (/healthz busy); True once idle or not answering (nothing to cut off)."""
    deadline = w.clock() + timeout
    while True:
        health, _ = watch.get_health(w.http, inst.local)
        if not (health or {}).get("busy"):
            return True
        if w.clock() >= deadline:
            return False
        w.sleep(2)


def deploy(w: watch.Watch, pull: bool = True, restart: bool = False,
           requirements: bool = False, output_fn=print) -> int:
    up = w.up
    instances = w.instances if w.instances is not None else watch.discover(w.root)
    w.instances = instances
    if not up.acquire():
        output_fn("An update or another deploy is running; try again in a minute.")
        return 1
    try:
        old = up.head()
        before = running_versions(w, instances)
        if pull:
            code, out = up.git("fetch", "--quiet")
            if code == 0:
                code, out = up.git("merge", "--ff-only", "--quiet", "@{u}")
            if code != 0:
                output_fn("Pull refused (fast-forward only; nothing changed): "
                          + out.strip()[-300:])
                return 1
        new = up.head()
        if not new:
            output_fn("Can't read HEAD.")
            return 1
        if pull and old and new != old and "requirements.txt" in up.changed_files(old, new):
            if not requirements:
                up.git("reset", "--keep", old)
                output_fn("This update changes requirements.txt: stop the apps, then "
                          "run this again with --requirements. Nothing changed.")
                return 1
            code, out = up.pip_install()
            if code != 0:
                up.rollback(old, new, "pip install failed", requirements=True)
                output_fn("pip install failed; back on the old version.")
                return 1
        missing = requirements_problem(w.root)
        if missing:  # e.g. --no-pull after a pull by hand that bumped requirements
            if pull and old and new != old:
                up.git("reset", "--keep", old)
            output_fn(f"REFUSED: {missing}. Nothing was switched.")
            return 1
        behind = [n for n, v in before.items() if v != new[:7]]
        if not behind:
            output_fn(f"{new[:7]} is already live everywhere.")
        else:
            verdict = up.gated(new)
            if verdict is None and new == up.last_good():
                verdict = True
            if verdict is None and w.cfg["gate"]:
                output_fn(f"Go-live check of {new[:7]}: each app's test conversation "
                          "on a side port (a few minutes)...")
                verdict, why = w.gate(new)
                if verdict is None:
                    if pull and old and new != old:
                        up.rollback(old, new, "go-live check couldn't run", reject=False)
                    output_fn(f"The go-live check couldn't run ({why}); nothing was "
                              "switched. Try again later.")
                    return 1
                up.mark_gated(new, verdict)
                if not verdict:
                    w.refuse(new, instances, why,
                             to=old if pull and old and old != new else None)
                    watch.write_status(w.status_file, w.status)
                    output_fn(f"REFUSED: {new[:7]} failed its go-live check ({why}). "
                              "The running version stays live.")
                    return 1
            elif verdict is False:
                if pull and old and old != new:
                    up.rollback(old, new, "failed its go-live check before")
                output_fn(f"{new[:7]} failed its go-live check before; not switching.")
                return 1
            up._save_state(applied=new)  # the host restarts on it, with rollback
    finally:
        up.release()
    busy = []
    for inst in instances:
        if w.host_runs(inst):
            continue  # its host supervisor restarts it (within a minute), when idle
        if restart or before.get(inst.name) != new[:7]:
            if not wait_until_idle(w, inst):  # never cut off someone's turn
                busy.append(inst.name)
                output_fn(f"{inst.label} is in the middle of a turn; not restarted. "
                          + ("Run this again later." if before.get(inst.name) == new[:7]
                             else "The watchdog switches it once it is idle."))
                continue
            output_fn(f"Restarting {inst.label}...")
            w.restart(inst)
    for inst in instances:
        if inst.name in busy:
            continue
        if not w.wait_for(inst, new, timeout=watch.SWITCH_WAIT + 120):
            output_fn(f"{inst.label} isn't answering on {new[:7]} yet.")
    output_fn("Test conversation with each app...")
    status = w.run(force_functional=True)
    failing = [i for i in (status.get("instances") or {}).values()
               if i.get("status") == watch.FAILING
               and not i.get("remote")]  # the cloud ([watch] cloud_url) isn't deployed here
    watch.print_status(status, output_fn)
    if failing:
        output_fn("Something failed after the switch; the watchdog restarted or "
                  "rolled back as above, and has told the coordinator.")
        return 1
    output_fn(f"{new[:7]} is live and passed.")
    return 0


def main(argv=None, output_fn=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant.deploy",
                                     description="Pull, check on a side port, then "
                                                 "switch over (or refuse)")
    parser.add_argument("--no-pull", action="store_true",
                        help="use the code checked out now (after a pull by hand)")
    parser.add_argument("--restart", action="store_true",
                        help="restart the apps even if they already run this version")
    parser.add_argument("--requirements", action="store_true",
                        help="allow an update that changes requirements.txt "
                             "(stop the apps first)")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    watch.setup_watch_logging(ROOT)
    return deploy(watch.Watch(ROOT), pull=not args.no_pull, restart=args.restart,
                  requirements=args.requirements, output_fn=output_fn)


if __name__ == "__main__":
    sys.exit(main())
