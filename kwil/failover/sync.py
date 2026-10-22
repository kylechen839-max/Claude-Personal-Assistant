"""History sync between backends: an append-only, id-addressed event log.

Each backend keeps its own history store. Everything that changes history
is an *event* with a random id and its own timestamp:

    {"id": "<32 hex>", "ts": <unix ms>, "origin": "pi|pc|cloud",
     "kind": "...", "conv": "<conversation id>", "data": {...}}

kinds
    message         data {"role": "user"|"assistant", "text": str}
    rename          data {"title": str}             last writer wins
    pin             data {"pinned": bool}           last writer wins
    delete          data {}                         tombstone for the conversation
    delete_message  data {"target": "<event id>"}   tombstone for one message
    job             data {"key": str}               a scheduled job fired
                    (conv is "job:<key>", see leader.py)

"Last writer" is the largest (ts, id) pair, so ties break the same way
everywhere. A tombstone is permanent: once a delete is in the log, the
conversation (or message) stays deleted, whatever arrives later and in
whatever order. Deleting also *redacts*: the store keeps the id, ts,
origin, kind and conv of every event the tombstone covers but drops its
data (the text), whether the tombstone arrives before or after them, so
the words are gone from every backend's log and from what it sends on.
So the materialized state is a pure function of the *set*
of events: merging is idempotent (same id = same event, ignored) and
order-independent, and stores that have seen the same events agree.

Transport: a backend pushes its events to each reachable peer and pulls the
peer's, over HTTPS with a sync-scope bearer token (tokens.py):

    GET  /api/sync/events?after=<seq>&limit=<n>
         -> {"log": <log id>, "events": [...], "next": <seq>, "more": bool}
    POST /api/sync/events  {"events": [...]}
         -> {"log": <log id>, "accepted": <new events>}

`seq` is the serving store's local insertion counter, so a puller only
asks for what it hasn't seen; events learned from a third backend get a
local seq too, so they travel on (Pi -> cloud -> PC). `log` is a random id
per store; if it changes (the peer's database was reset) the cursor starts
again from 0. Pi and PC sync with the cloud over its public URL and with
each other over the tailnet; the cloud only answers.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Protocol
from urllib.parse import parse_qs

from kwil.failover import ROLES

if TYPE_CHECKING:
    from kwil.failover.peers import Backend

log = logging.getLogger(__name__)

KINDS = ("message", "rename", "pin", "delete", "delete_message", "job")
MAX_BATCH = 500
MAX_EVENT_BYTES = 64 * 1024
MAX_PUSH_BYTES = 4 * 1024 * 1024
MAX_FUTURE_MS = 24 * 3600 * 1000  # reject timestamps more than a day ahead
_ID = re.compile(r"^[0-9a-f]{32}$")
_CONV = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")


@dataclass(frozen=True)
class Event:
    id: str
    ts: int
    origin: str
    kind: str
    conv: str
    data: dict = field(default_factory=dict, hash=False, compare=False)

    def to_json(self) -> dict:
        return {"id": self.id, "ts": self.ts, "origin": self.origin,
                "kind": self.kind, "conv": self.conv, "data": self.data}

    @property
    def order(self) -> tuple[int, str]:
        return (self.ts, self.id)


def new_id() -> str:
    return secrets.token_hex(16)


def now_ms() -> int:
    return int(time.time() * 1000)


def new_event(kind: str, conv: str, data: dict | None = None, *, origin: str,
              ts: int | None = None) -> Event:
    return parse_event({"id": new_id(), "ts": now_ms() if ts is None else ts,
                        "origin": origin, "kind": kind, "conv": conv,
                        "data": data or {}})


def parse_event(raw, now: int | None = None) -> Event:
    """A validated Event from untrusted JSON, or ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("event must be an object")
    eid, ts, origin = raw.get("id"), raw.get("ts"), raw.get("origin")
    kind, conv, data = raw.get("kind"), raw.get("conv"), raw.get("data", {})
    if not isinstance(eid, str) or not _ID.match(eid):
        raise ValueError("bad id")
    if not isinstance(ts, int) or isinstance(ts, bool) or ts < 0:
        raise ValueError("bad ts")
    if ts > (now if now is not None else now_ms()) + MAX_FUTURE_MS:
        raise ValueError("ts too far in the future")
    if origin not in ROLES:
        raise ValueError("bad origin")
    if kind not in KINDS:
        raise ValueError("bad kind")
    if not isinstance(conv, str) or not _CONV.match(conv):
        raise ValueError("bad conv")
    if not isinstance(data, dict):
        raise ValueError("bad data")
    if len(json.dumps(data)) > MAX_EVENT_BYTES:
        raise ValueError("event too large")
    if kind == "job" and not conv.startswith("job:"):
        raise ValueError("job events use conv 'job:<key>'")
    return Event(eid, ts, origin, kind, conv, data)


# --- the store API (the history store implements this) ------------------------

class EventLog(Protocol):
    """What sync needs from a history store. The SQLite history store
    implements the same methods; MemoryEventLog is the reference."""

    log_id: str  # random, fixed for the life of the database

    def append(self, events: Iterable[Event]) -> list[Event]:
        """Insert events whose id is new; return the ones inserted.
        Atomic per call; known ids are ignored (idempotent). Must apply
        redactions (see `covered_by` / `redacted`): an event under an
        existing tombstone is stored redacted, and a new tombstone redacts
        the stored events it covers."""

    def since(self, seq: int, limit: int) -> tuple[list[Event], int, bool]:
        """Events with local seq > `seq`, oldest first, at most `limit`:
        (events, seq of the last one returned (or `seq`), more left?)."""

    def by_conv(self, conv: str) -> list[Event]:
        """All events of one conversation (or job key)."""

    def meta_get(self, key: str) -> str | None: ...

    def meta_set(self, key: str, value: str) -> None: ...


def redacted(e: Event) -> Event:
    """The event without its data (text, title...): what is kept of an
    event a tombstone covers."""
    return Event(e.id, e.ts, e.origin, e.kind, e.conv, {}) if e.data else e


def covered_by(tombstone: Event, e: Event) -> bool:
    """Does this delete / delete_message tombstone cover event `e`?"""
    if tombstone.kind == "delete":
        # tombstones themselves hold no text (only ids) and must survive
        return e.conv == tombstone.conv and e.kind not in ("delete", "delete_message", "job")
    if tombstone.kind == "delete_message":  # one message of its own conversation
        return (e.id == tombstone.data.get("target") and e.conv == tombstone.conv
                and e.kind not in ("delete", "delete_message", "job"))
    return False


class MemoryEventLog:
    def __init__(self, log_id: str | None = None):
        self.log_id = log_id or new_id()
        self._events: list[Event] = []      # index = seq - 1
        self._seq: dict[str, int] = {}      # id -> index in _events
        self._tombstones: list[Event] = []
        self._meta: dict[str, str] = {}
        self._lock = threading.Lock()

    def append(self, events):
        added = []
        with self._lock:
            for e in events:
                if e.id in self._seq:
                    continue
                if any(covered_by(t, e) for t in self._tombstones):
                    e = redacted(e)
                self._seq[e.id] = len(self._events)
                self._events.append(e)
                added.append(e)
                if e.kind in ("delete", "delete_message"):
                    self._tombstones.append(e)
                    for i, old in enumerate(self._events):
                        if old.data and covered_by(e, old):
                            self._events[i] = redacted(old)
        return added

    def since(self, seq, limit):
        with self._lock:
            seq = max(0, min(seq, len(self._events)))
            chunk = self._events[seq:seq + limit]
            last = seq + len(chunk)
            return list(chunk), last, last < len(self._events)

    def by_conv(self, conv):
        with self._lock:
            return [e for e in self._events if e.conv == conv]

    def all(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def meta_get(self, key):
        return self._meta.get(key)

    def meta_set(self, key, value):
        self._meta[key] = value


# --- merge: the state a set of events describes -------------------------------

@dataclass
class Conversation:
    conv: str
    title: str = ""
    pinned: bool = False
    deleted: bool = False
    messages: list[dict] = field(default_factory=list)
    updated: int = 0  # ts of the newest event


def materialize(events: Iterable[Event]) -> dict:
    """{"conversations": {conv: Conversation}, "jobs": {key: [fire ids]}}.
    A pure function of the set of events (duplicates and order don't
    matter). Deleted conversations are kept as tombstones, with no
    messages."""
    unique = {e.id: e for e in events}
    ordered = sorted(unique.values(), key=lambda e: e.order)
    convs: dict[str, Conversation] = {}
    jobs: dict[str, list[str]] = {}
    dead_msgs = {(e.conv, e.data.get("target")) for e in ordered
                 if e.kind == "delete_message"}
    dead_convs = {e.conv for e in ordered if e.kind == "delete"}
    for e in ordered:
        if e.kind == "job":
            jobs.setdefault(e.conv[4:], []).append(e.id)
            continue
        c = convs.setdefault(e.conv, Conversation(e.conv))
        c.updated = max(c.updated, e.ts)
        if not e.data and e.kind in ("message", "rename", "pin"):
            continue  # redacted by a tombstone that may not have arrived yet
        if e.kind == "message" and (e.conv, e.id) not in dead_msgs:
            c.messages.append({"id": e.id, "ts": e.ts, "origin": e.origin,
                               "role": e.data.get("role"), "text": e.data.get("text", "")})
        elif e.kind == "rename":
            c.title = str(e.data.get("title", ""))[:200]  # later (ts, id) wins
        elif e.kind == "pin":
            c.pinned = bool(e.data.get("pinned"))
    for conv in dead_convs:
        c = convs[conv]
        c.deleted, c.messages, c.title, c.pinned = True, [], "", False
    return {"conversations": convs, "jobs": jobs}


def fingerprint(state: dict) -> str:
    """A comparable summary of a materialized state (for tests and logs)."""
    convs = {k: [c.title, c.pinned, c.deleted, [m["id"] for m in c.messages]]
             for k, c in sorted(state["conversations"].items())}
    return json.dumps({"c": convs, "j": dict(sorted(state["jobs"].items()))},
                      sort_keys=True)


# --- server side ----------------------------------------------------------------

def handle_pull(store: EventLog, query: dict) -> tuple[int, dict]:
    try:
        after = int(query.get("after", 0))
        limit = int(query.get("limit", MAX_BATCH))
    except (TypeError, ValueError):
        return 400, {"error": "bad cursor"}
    limit = max(1, min(limit, MAX_BATCH))
    events, last, more = store.since(max(0, after), limit)
    return 200, {"log": store.log_id, "events": [e.to_json() for e in events],
                 "next": last, "more": more}


def handle_push(store: EventLog, body) -> tuple[int, dict]:
    raw = body.get("events") if isinstance(body, dict) else None
    if not isinstance(raw, list) or len(raw) > MAX_BATCH:
        return 400, {"error": f"events must be a list of at most {MAX_BATCH}"}
    try:
        events = [parse_event(r) for r in raw]
    except ValueError as e:
        return 400, {"error": f"bad event: {e}"}
    added = store.append(events)
    return 200, {"log": store.log_id, "accepted": len(added)}


def make_sync_app(store: EventLog):
    """Raw ASGI app for /api/sync/events. Mount it behind EdgeMiddleware
    (routes={"/api/sync/": app}), which checks the sync-scope token; it
    refuses requests that didn't come through it."""

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        auth = scope.get("auth")
        if getattr(auth, "scope", None) != "sync":
            return await _respond(send, 401, {"error": "unauthorized"})
        if auth.sid in ROLES:  # the leader check counts this as a sign of life
            store.meta_set(f"heard:{auth.sid}", str(now_ms()))
        if scope.get("path") != "/api/sync/events":
            return await _respond(send, 404, {"error": "not found"})
        if scope["method"] == "GET":
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            status, payload = handle_pull(store, {k: v[0] for k, v in qs.items()})
        elif scope["method"] == "POST":
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if len(body) > MAX_PUSH_BYTES:
                    return await _respond(send, 413, {"error": "too large"})
                if not message.get("more_body"):
                    break
            try:
                parsed = json.loads(body or b"{}")
            except ValueError:
                return await _respond(send, 400, {"error": "bad json"})
            # sqlite writes are blocking: keep them off the event loop
            import asyncio

            status, payload = await asyncio.to_thread(handle_push, store, parsed)
        else:
            return await _respond(send, 405, {"error": "method not allowed"})
        await _respond(send, status, payload)

    return app


async def _respond(send, status, payload):
    data = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": data})


# --- client side ----------------------------------------------------------------

def default_peers(role: str, backends: list[Backend]) -> list[Backend]:
    """Who a backend syncs with: Pi and PC with everyone else, the cloud
    with no one (it can't reach the tailnet; the others come to it)."""
    if role == "cloud":
        return []
    return [b for b in backends if b.role != role]


def requests_transport(method, url, headers, body=None, timeout=10.0):
    import requests

    r = requests.request(method, url, headers=headers, json=body, timeout=timeout,
                         allow_redirects=False)
    try:
        payload = r.json()
    except ValueError:
        payload = None
    return r.status_code, payload


class SyncError(Exception):
    pass


class Syncer:
    """Pushes local events to peers and pulls theirs.

    transport(method, url, headers, body) -> (status, json); the default
    uses requests. Cursors are kept in the store's meta table, so a restart
    doesn't re-send everything."""

    def __init__(self, store: EventLog, peers: list[Backend], authority, role: str,
                 transport=requests_transport, batch: int = 200):
        self.store = store
        self.peers = list(peers)
        self.authority = authority
        self.role = role
        self.transport = transport
        self.batch = max(1, min(batch, MAX_BATCH))

    def _headers(self) -> dict:
        token, _ = self.authority.mint("sync", sid=self.role)
        return {"Authorization": f"Bearer {token}"}

    def _cursor(self, peer: Backend, kind: str) -> tuple[int, str | None]:
        raw = self.store.meta_get(f"sync:{peer.role}:{kind}")
        if not raw:
            return 0, None
        seq, _, log_id = raw.partition(":")
        return int(seq), (log_id or None)

    def _set_cursor(self, peer, kind, seq, log_id):
        self.store.meta_set(f"sync:{peer.role}:{kind}", f"{seq}:{log_id or ''}")

    def _call(self, method, url, body=None):
        status, payload = self.transport(method, url, self._headers(), body)
        if status != 200 or not isinstance(payload, dict):
            raise SyncError(f"{method} {url.split('?')[0]} -> HTTP {status}")
        return payload

    def push(self, peer: Backend) -> int:
        """Send local events the peer may not have. Returns how many it took."""
        url = peer.url + "/api/sync/events"
        seq, peer_log = self._cursor(peer, "push")
        accepted = 0
        restarted = False
        while True:
            events, last, more = self.store.since(seq, self.batch)
            if not events:
                break
            payload = self._call("POST", url, {"events": [e.to_json() for e in events]})
            log_id = payload.get("log")
            if peer_log and log_id != peer_log and not restarted:
                # the peer's database was replaced: send everything again
                restarted, seq, peer_log = True, 0, log_id
                continue
            peer_log = log_id
            accepted += int(payload.get("accepted", 0))
            seq = last
            self._set_cursor(peer, "push", seq, peer_log)
            if not more:
                break
        return accepted

    def pull(self, peer: Backend) -> int:
        """Fetch the peer's events we don't have. Returns how many were new."""
        seq, peer_log = self._cursor(peer, "pull")
        added = 0
        restarted = False
        while True:
            payload = self._call(
                "GET", f"{peer.url}/api/sync/events?after={seq}&limit={self.batch}")
            log_id = payload.get("log")
            if peer_log and log_id != peer_log and not restarted:
                # the peer's database was replaced: read it all, send it all
                restarted, seq, peer_log = True, 0, log_id
                self._set_cursor(peer, "push", 0, None)
                continue
            peer_log = log_id
            raw = payload.get("events")
            if not isinstance(raw, list):
                raise SyncError("bad pull response")
            events = []
            for r in raw:
                try:
                    events.append(parse_event(r))
                except ValueError as e:
                    log.warning("Skipping a bad event from %s: %s", peer.role, e)
            added += len(self.store.append(events))
            nxt = payload.get("next")
            if not isinstance(nxt, int) or nxt < seq:
                raise SyncError("bad cursor from peer")
            seq = nxt
            self._set_cursor(peer, "pull", seq, peer_log)
            if not payload.get("more") or not raw:
                break
        return added

    def sync_peer(self, peer: Backend) -> tuple[int, int]:
        pulled = self.pull(peer)  # first: notices a reset peer
        return self.push(peer), pulled

    def sync_all(self) -> dict[str, str]:
        """One round with every peer; unreachable peers are skipped."""
        result = {}
        for peer in self.peers:
            try:
                pushed, pulled = self.sync_peer(peer)
                result[peer.role] = f"ok +{pulled}/{pushed}"
            except Exception as e:  # network down, peer down: next round
                log.info("History sync with %s failed: %s", peer.role, e)
                result[peer.role] = f"failed: {type(e).__name__}"
        return result

    def run_forever(self, stop: threading.Event, interval: float = 30.0) -> None:
        while not stop.is_set():
            self.sync_all()
            stop.wait(interval)


# --- "sign out everywhere" for access tokens ----------------------------------
# Carried as a job event (conv "job:auth-revoke"), which history stores and
# older backends already ignore, so it needs no new event kind.

REVOKE_CONV = "job:auth-revoke"


def revoke_event(not_before: int, origin: str) -> Event:
    """App tokens signed in at or before `not_before` (unix s) stop working."""
    return new_event("job", REVOKE_CONV, {"key": "auth-revoke",
                                          "not_before": int(not_before)}, origin=origin)


def revoked_before(store: EventLog) -> int:
    """The latest sign-out-everywhere time in the log (0 if none)."""
    times = [e.data.get("not_before") for e in store.by_conv(REVOKE_CONV)]
    return max((t for t in times if isinstance(t, int)), default=0)
