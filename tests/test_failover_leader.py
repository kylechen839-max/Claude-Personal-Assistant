"""Leader check for scheduled jobs (failover/leader.py)."""

import itertools
import random

import pytest

from assistant.failover.leader import (FIRED, FIRE_TOLERANCE_MS, SKIPPED_DONE,
                                       SKIPPED_NOT_LEADER, SUPPRESSED, JobRunner,
                                       LeaderCheck, claims, fires, store_heard_from, winner)
from assistant.failover.peers import Backend, Health, parse_health
from assistant.failover.sync import MemoryEventLog
from assistant.failover import API_VERSION

PI = Backend("pi", "https://pi.t.ts.net")
PC = Backend("pc", "https://pc.t.ts.net")
CLOUD = Backend("cloud", "https://hc.onrender.com")
ALL = [PI, PC, CLOUD]


def prober(up):
    """probe(backend) answering from a dict role -> bool, or a callable."""
    def probe(b):
        state = up(b.role) if callable(up) else up.get(b.role, False)
        return Health(True, API_VERSION, b.role) if state else Health(False, reason="down")
    return probe


@pytest.mark.parametrize("up,leader", [
    ({"pi": True, "pc": True, "cloud": True}, "pi"),
    ({"pi": False, "pc": True, "cloud": True}, "pc"),
    ({"pi": False, "pc": False, "cloud": True}, "cloud"),
])
def test_exactly_the_highest_healthy_backend_leads(up, leader):
    probe = prober(up)
    leaders = [role for role, me in (("pi", PI), ("pc", PC), ("cloud", CLOUD))
               if up[role] and LeaderCheck(role, [b for b in ALL if b != me], probe).is_leader()]
    assert leaders == [leader]


def test_incompatible_peer_does_not_count():
    old = lambda b: parse_health(200, {"ok": True, "api": API_VERSION + 1, "role": b.role})
    assert LeaderCheck("pc", [PI, CLOUD], old).is_leader()


def test_pi_never_probes():
    calls = []
    LeaderCheck("pi", [PC, CLOUD], lambda b: calls.append(b) or Health(True, API_VERSION)).is_leader()
    assert calls == []


def test_one_dropped_probe_of_a_flapping_peer_is_retried():
    answers = iter([False, True])
    check = LeaderCheck("pc", [PI, CLOUD], prober(lambda r: next(answers)))
    assert not check.is_leader()


def test_runner_skips_a_job_already_fired():
    store = MemoryEventLog()
    ran = []
    r = JobRunner(store, LeaderCheck("pc", [PI, CLOUD], prober({"pi": False})))
    assert r.run("brief-2026-10-19", lambda: ran.append(1)) == FIRED
    assert r.run("brief-2026-10-19", lambda: ran.append(1)) == SKIPPED_DONE
    assert ran == [1] and len(claims(fires(store, "brief-2026-10-19"))) == 1


def test_runner_stands_down_then_takes_over_when_leader_stays_away():
    store = MemoryEventLog()
    states = iter([True, True, False, False])  # 2 tries per check
    sleeps = []
    r = JobRunner(store, LeaderCheck("pc", [PI, CLOUD], prober(lambda role: next(states))),
                  sleep=sleeps.append)
    ran = []
    assert r.run("k", lambda: ran.append(1), recheck_delays=(30, 30)) == FIRED
    assert ran == [1] and sleeps == [30, 30]


def test_runner_not_leader():
    r = JobRunner(MemoryEventLog(), LeaderCheck("cloud", [PI, PC], prober({"pc": True})))
    assert r.run("k", lambda: 1 / 0) == SKIPPED_NOT_LEADER


class Cluster:
    """Three backends with their own stores, a simulated network and
    flapping health answers, syncing all-to-all when reachable."""

    def __init__(self, rng, alive, flap, sync_loss=0.0):
        self.rng = rng
        self.alive = alive        # process running (can fire, can sync)
        self.flap = flap          # chance a probe of a live peer is lost
        self.sync_loss = sync_loss  # chance a sync with a live peer fails
        self.stores = {b.role: MemoryEventLog() for b in ALL}
        self.ran = []
        self.ts = itertools.count(1000, 7)

    def probe(self, b):
        ok = self.alive[b.role] and self.rng.random() >= self.flap
        return Health(True, API_VERSION, b.role) if ok else Health(False)

    def sync(self, role):
        def go():
            for other, store in self.stores.items():
                if other != role and self.alive[other] and self.rng.random() >= self.sync_loss:
                    store.append(self.stores[role].all())
                    self.stores[role].append(store.all())
        return go

    def runner(self, role):
        me = next(b for b in ALL if b.role == role)
        return JobRunner(self.stores[role],
                         LeaderCheck(role, [b for b in ALL if b != me], self.probe),
                         sync=self.sync(role), sleep=lambda s: None,
                         clock_ms=lambda: next(self.ts))


def run_cluster(seed, sync_loss):
    rng = random.Random(seed)
    alive = {"pi": rng.random() < 0.7, "pc": rng.random() < 0.8, "cloud": True}
    c = Cluster(rng, alive, flap=rng.choice([0.0, 0.2, 0.5]), sync_loss=sync_loss)
    roles = [r for r in ("pi", "pc", "cloud") if alive[r]]
    rng.shuffle(roles)  # each backend's scheduler wakes up in some order
    outcomes = {}
    for role in roles:
        outcomes[role] = c.runner(role).run(
            "reminder-42", lambda role=role: c.ran.append(role), recheck_delays=(10, 10))
    c.sync_loss = 0.0  # the network heals; logs converge
    for role in roles:
        c.sync(role)()
    fired = {e.id: e for s in c.stores.values() for e in claims(fires(s, "reminder-42"))}
    return c, alive, outcomes, sorted(fired.values(), key=lambda e: e.order)


@pytest.mark.parametrize("seed", range(80))
def test_flapping_peer_job_runs_exactly_once(seed):
    """A peer's /healthz flaps (probes get lost), the backends themselves
    can sync: the job's action runs exactly once, by the first fire."""
    c, alive, outcomes, fired = run_cluster(seed, sync_loss=0.0)
    assert len(c.ran) == 1, outcomes
    assert c.ran == [winner(fired).origin]
    if alive["pi"]:
        assert outcomes["pi"] in (FIRED, SKIPPED_DONE, SUPPRESSED)  # never "not leader"
    for role, outcome in outcomes.items():
        assert outcome in (FIRED, SKIPPED_DONE, SKIPPED_NOT_LEADER, SUPPRESSED)


@pytest.mark.parametrize("seed", range(80))
def test_partition_double_fire_is_recorded(seed):
    """Probes and syncs both get lost (a partition): two backends may each
    act, but never silently: every action has its own fire in the merged
    log, so the duplicate is visible to the job afterwards."""
    c, alive, outcomes, fired = run_cluster(seed, sync_loss=0.5)
    assert len(c.ran) >= 1, outcomes
    assert sorted(c.ran) == sorted(e.origin for e in fired if e.origin in c.ran)
    assert len(fired) >= len(c.ran)
    assert len(fired) <= len(outcomes)


def test_concurrent_double_fire_is_suppressed():
    """Pi and PC both believe they lead (PC's probe of the Pi was lost) and
    record a fire; after syncing, only the earlier fire acts."""
    pi_store, pc_store = MemoryEventLog(), MemoryEventLog()
    ran = []

    def sync_both():
        pi_store.append(pc_store.all())
        pc_store.append(pi_store.all())

    pc = JobRunner(pc_store, LeaderCheck("pc", [PI, CLOUD], prober({})),
                   clock_ms=lambda: 2000)
    assert pc.run("k", lambda: ran.append("pc")) == FIRED  # couldn't sync yet
    pi = JobRunner(pi_store, LeaderCheck("pi", [PC, CLOUD], prober({})),
                   sync=sync_both, clock_ms=lambda: 1000)
    # the Pi recorded its fire earlier (ts 1000 < 2000) but syncs only now:
    # it sees PC's fire already in the log and skips
    assert pi.run("k", lambda: ran.append("pi")) == SKIPPED_DONE
    assert ran == ["pc"]


# --- the real topology: the cloud can't reach the tailnet ---------------------

def test_cloud_stands_down_when_a_peer_synced_recently():
    store = MemoryEventLog()
    now = [10_000_000]
    check = LeaderCheck("cloud", [PI, PC], prober({}),  # tailnet probes always fail
                        heard_from=store_heard_from(store), clock_ms=lambda: now[0])
    assert check.is_leader()
    store.meta_set("heard:pi", str(now[0] - 30_000))  # the Pi synced 30 s ago
    assert not check.is_leader()
    now[0] += 200_000  # nothing from the Pi for over 2 minutes
    assert check.is_leader()


def test_sync_endpoint_records_who_called():
    import asyncio

    from assistant.failover.sync import make_sync_app
    from assistant.failover.tokens import Claims

    store = MemoryEventLog()
    app = make_sync_app(store)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "method": "GET", "path": "/api/sync/events", "query_string": b"",
             "auth": Claims("sync", "pi", 0, 0)}
    asyncio.run(app(scope, receive, send))
    assert store_heard_from(store)("pi") is not None
    assert store_heard_from(store)("pc") is None


class Topology:
    """Pi and PC on the tailnet (they probe each other), the cloud on the
    internet: it can't probe them; they push/pull to it every sync interval,
    which is how it hears from them."""

    SYNC_MS = 30_000

    def __init__(self, rng, alive):
        self.rng = rng
        self.alive = alive
        self.stores = {b.role: MemoryEventLog() for b in ALL}
        self.now = int(__import__("time").time() * 1000)
        self.ran = []

    def clock(self):
        return self.now

    def probe_from(self, me):
        def probe(b):
            reachable = me != "cloud" and b.role != "cloud" or (me != "cloud" and b.role == "cloud")
            return Health(True, API_VERSION, b.role) if reachable and self.alive[b.role] \
                else Health(False)
        return probe

    def sync_round(self, role):
        """What Syncer.sync_all does for role (Pi/PC only)."""
        if role == "cloud" or not self.alive[role]:
            return
        for other in ("pi", "pc", "cloud"):
            if other != role and self.alive[other]:
                self.stores[other].append(self.stores[role].all())
                self.stores[role].append(self.stores[other].all())
                if other == "cloud":
                    self.stores["cloud"].meta_set(f"heard:{role}", str(self.now))

    def tick(self, ms):
        """Time passes; Pi and PC run their sync rounds."""
        end = self.now + ms
        while self.now < end:
            self.now += self.SYNC_MS
            for role in ("pi", "pc"):
                self.sync_round(role)

    def runner(self, role):
        me = next(b for b in ALL if b.role == role)
        store = self.stores[role]
        return JobRunner(store, LeaderCheck(role, [b for b in ALL if b != me],
                                            self.probe_from(role),
                                            heard_from=store_heard_from(store),
                                            clock_ms=self.clock),
                         sync=lambda: self.sync_round(role), sleep=lambda s: self.tick(s * 1000),
                         clock_ms=self.clock)


@pytest.mark.parametrize("seed", range(40))
def test_real_topology_reminder_goes_out_once(seed):
    rng = random.Random(seed)
    alive = {"pi": rng.random() < 0.6, "pc": rng.random() < 0.6, "cloud": True}
    t = Topology(rng, alive)
    t.tick(5 * 60_000)  # running for a while: the cloud has heard from the live ones
    roles = [r for r in ("pi", "pc", "cloud") if alive[r]]
    rng.shuffle(roles)  # all schedulers wake for the 08:00 reminder in some order
    outcomes = {}
    for role in roles:
        # the cloud waits longer than a sync interval before deciding
        delays = (45, 45) if role == "cloud" else ()
        outcomes[role] = t.runner(role).run("reminder-0800",
                                            lambda role=role: t.ran.append(role),
                                            recheck_delays=delays)
    top = next(r for r in ("pi", "pc", "cloud") if alive[r])
    assert t.ran == [top], (alive, outcomes)


def fire(origin, ts, id_, **extra):
    from assistant.failover.sync import parse_event

    return parse_event({"id": id_ * 32, "ts": ts, "origin": origin, "kind": "job",
                        "conv": "job:k", "data": {"key": "k", **extra}})


def test_winner_is_the_highest_priority_claim_whatever_the_clocks():
    pc_early = fire("pc", 1_000, "a")
    pi_late = fire("pi", 3_000, "b")  # the Pi's clock is a bit off
    assert winner([pc_early, pi_late]) == pi_late
    assert winner([pi_late, pc_early]) == pi_late


def test_winner_always_elects_someone_whatever_each_backend_has_seen():
    """Review case: cloud at -50 s, PC at 0, Pi at +55 s. Anchoring the
    window on the earliest claim seen let every backend elect another one."""
    cloud, pc, pi = fire("cloud", -50_000 + 10**6, "c"), fire("pc", 10**6, "a"), \
        fire("pi", 55_000 + 10**6, "b")
    views = {"pc": [pc, pi], "pi": [cloud, pc, pi], "cloud": [cloud, pc]}
    elected = {who: winner(seen).origin for who, seen in views.items()}
    acting = [who for who, w in elected.items() if w == who]
    assert acting == ["pi"]  # the Pi elects itself; only the cloud's view lacks it
    assert elected["cloud"] == "pc"  # (and the cloud doesn't act either)


def test_due_time_prefers_on_time_claims():
    due = 10**6
    late_pi = fire("pi", due + FIRE_TOLERANCE_MS + 1, "b", due=due)  # Pi came back late
    pc = fire("pc", due + 1_000, "a", due=due)
    assert winner([late_pi, pc]) == pc
    assert winner([fire("pi", due + 5_000, "c", due=due), pc]).origin == "pi"


def test_a_backend_that_crashed_after_claiming_does_not_block_the_job():
    store = MemoryEventLog()
    now = [10**12]
    # the Pi claimed it and died before acting (no "done")
    store.append([fire("pi", now[0], "d")])
    pc = JobRunner(store, LeaderCheck("pc", [PI, CLOUD], prober({})),
                   clock_ms=lambda: now[0])
    ran = []
    assert pc.run("k", lambda: ran.append(1)) == SKIPPED_DONE  # maybe still running
    now[0] += 11 * 60_000  # past the claim TTL
    assert pc.run("k", lambda: ran.append(1)) == FIRED
    assert ran == [1]
    # and after that it's done for everyone
    assert JobRunner(store, LeaderCheck("cloud", [PI, PC], prober({})),
                     clock_ms=lambda: now[0]).run("k", lambda: 1 / 0) == SKIPPED_DONE
