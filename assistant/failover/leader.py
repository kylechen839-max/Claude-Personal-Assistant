"""Which backend runs a scheduled job (reminders, briefings)?

Rule: the highest-priority healthy backend. Each backend decides alone,
right before firing: it probes the /healthz of every higher-priority peer
and stands down if any of them answers (and speaks our API version). The
Pi never stands down; the cloud fires only when neither Pi nor PC answers.

The cloud can't reach the tailnet, so its probes of the Pi and PC always
fail. It also counts a peer as alive if that peer synced with it recently
(`heard_from`: the sync endpoint records each peer's last call), and it
waits longer than one sync interval before firing, so a fire the Pi
already recorded has time to arrive.

Probes can be wrong (a peer that flaps, a partition where both sides think
they lead), so a fire is also recorded in the shared history log as a
`job` event under conv "job:<dedupe key>":

1. If the log already has a fire for the key, skip.
2. Leader check (above); if not leader, optionally re-check after a delay,
   in case the leader was mid-restart.
3. Record our fire, sync with peers, then look again: if another backend's
   fire wins (`winner`: the earliest, but among fires within
   FIRE_TOLERANCE of each other, i.e. clock skew, the highest-priority
   role), ours is the duplicate and the action is suppressed.

Step 3 needs the peer reachable to see its fire; across a real partition a
duplicate can still happen, and is then visible in the log (two fires for
one key) for the job to handle, e.g. "already reminded".
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from assistant.failover.peers import Backend, Health, probe as http_probe
from assistant.failover.sync import EventLog, new_event

log = logging.getLogger(__name__)

FIRED = "fired"
SKIPPED_DONE = "skipped: already fired"
SKIPPED_NOT_LEADER = "skipped: a higher-priority backend is healthy"
SUPPRESSED = "suppressed: another backend fired first"
FIRE_TOLERANCE_MS = 60_000  # fires this close together: priority decides, not clocks
HEARD_FRESH_MS = 120_000    # a peer that synced this recently counts as alive


class LeaderCheck:
    def __init__(self, role: str, peers: list[Backend],
                 probe: Callable[[Backend], Health] = http_probe, attempts: int = 2,
                 heard_from: Callable[[str], int | None] = lambda role: None,
                 clock_ms: Callable[[], int] | None = None,
                 fresh_ms: int = HEARD_FRESH_MS):
        from assistant.failover import ROLES

        self.role = role
        self.rank = ROLES.index(role)
        self.higher = sorted((p for p in peers if p.priority < self.rank),
                             key=lambda p: p.priority)
        self.probe = probe
        self.attempts = max(1, attempts)
        self.heard_from = heard_from   # role -> unix ms of its last sync call
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.fresh_ms = fresh_ms

    def healthy_higher_peer(self) -> Backend | None:
        """The first higher-priority peer that answers now, or None. A peer
        gets `attempts` tries, so a single dropped probe of a flapping peer
        doesn't make us fire next to it."""
        for peer in self.higher:
            heard = self.heard_from(peer.role)
            if heard is not None and self.clock_ms() - heard < self.fresh_ms:
                return peer
            for _ in range(self.attempts):
                if self.probe(peer).usable:
                    return peer
        return None

    def is_leader(self) -> bool:
        peer = self.healthy_higher_peer()
        if peer is not None:
            log.debug("Not leader: %s is healthy", peer.role)
        return peer is None


def job_conv(key: str) -> str:
    return f"job:{key}"


def fires(store: EventLog, key: str) -> list:
    return sorted((e for e in store.by_conv(job_conv(key)) if e.kind == "job"),
                  key=lambda e: e.order)


def winner(fire_events: list, tolerance_ms: int = FIRE_TOLERANCE_MS):
    """The fire that acts: among those within `tolerance_ms` of the
    earliest (skewed clocks), the highest-priority role's; ties by (ts, id).
    The same everywhere, since it depends only on the set of fires."""
    from assistant.failover import ROLES

    earliest = min(fire_events, key=lambda e: e.order)
    close = [e for e in fire_events if e.ts <= earliest.ts + tolerance_ms]
    return min(close, key=lambda e: (ROLES.index(e.origin), e.ts, e.id))


def store_heard_from(store: EventLog) -> Callable[[str], int | None]:
    """heard_from for LeaderCheck, from what the sync endpoint records."""
    def heard(role: str) -> int | None:
        raw = store.meta_get(f"heard:{role}")
        return int(raw) if raw and raw.isdigit() else None
    return heard


class JobRunner:
    """Runs `action()` for a job at most once across backends (see module
    doc). `sync()` should push and pull history with peers (Syncer.sync_all);
    `sleep` is injectable for tests."""

    def __init__(self, store: EventLog, leader: LeaderCheck,
                 sync: Callable[[], object] = lambda: None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock_ms: Callable[[], int] | None = None):
        self.store = store
        self.leader = leader
        self.sync = sync
        self.sleep = sleep
        self.clock_ms = clock_ms

    def run(self, key: str, action: Callable[[], object],
            recheck_delays: tuple[float, ...] = ()) -> str:
        self._sync()
        if fires(self.store, key):
            return SKIPPED_DONE
        if not self.leader.is_leader():
            for delay in recheck_delays:
                self.sleep(delay)
                self._sync()
                if fires(self.store, key):
                    return SKIPPED_DONE
                if self.leader.is_leader():
                    break
            else:
                return SKIPPED_NOT_LEADER
        ts = self.clock_ms() if self.clock_ms else None
        mine = new_event("job", job_conv(key), {"key": key},
                         origin=self.leader.role, ts=ts)
        self.store.append([mine])
        self._sync()
        first = winner(fires(self.store, key))
        if first.id != mine.id:
            log.info("Job %s: %s fired first; not running it again", key, first.origin)
            return SUPPRESSED
        action()
        return FIRED

    def _sync(self):
        try:
            self.sync()
        except Exception as e:  # sync is best effort; the leader check still holds
            log.info("Sync before a job failed: %s", e)
