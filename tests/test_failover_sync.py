"""History sync protocol (failover/sync.py): merge, transport, convergence."""

import json
import sys
import random
from urllib.parse import parse_qs, urlsplit

import pytest

from assistant.failover.peers import Backend
from assistant.failover.sync import (MAX_BATCH, MemoryEventLog, Syncer, covered_by,
                                     default_peers,
                                     fingerprint, handle_pull, handle_push,
                                     materialize, new_event, new_id,
                                     parse_event)
from assistant.failover.tokens import TokenAuthority, bearer_of
from assistant.failover.eventlog import SqliteEventLog

make_store = MemoryEventLog


@pytest.fixture(autouse=True, params=["memory", "sqlite"])
def store_kind(request, tmp_path, monkeypatch):
    """Every test runs against both EventLog implementations."""
    if request.param == "sqlite":
        counter = iter(range(10_000))
        monkeypatch.setattr(sys.modules[__name__], "make_store",
                            lambda: SqliteEventLog(tmp_path / f"h{next(counter)}.db"))
    return request.param

SECRET = "s" * 43
URLS = {"pi": "https://pi.t.ts.net", "pc": "https://pc.t.ts.net",
        "cloud": "https://hc.onrender.com"}
BACKENDS = [Backend(r, u) for r, u in URLS.items()]


class Network:
    """In-process transport: routes sync calls to the right store, checks
    the bearer token like the real middleware, and can take hosts down."""

    def __init__(self, stores):
        self.stores = stores
        self.authority = TokenAuthority(SECRET)
        self.down: set[str] = set()
        self.calls = 0

    def transport(self, method, url, headers, body=None):
        self.calls += 1
        parts = urlsplit(url)
        role = next(r for r, u in URLS.items() if u == f"{parts.scheme}://{parts.netloc}")
        if role in self.down:
            raise ConnectionError(f"{role} is down")
        if not self.authority.verify(bearer_of(headers.get("Authorization")), scope="sync"):
            return 401, {"error": "unauthorized"}
        store = self.stores[role]
        if method == "GET":
            q = {k: v[0] for k, v in parse_qs(parts.query).items()}
            return handle_pull(store, q)
        return handle_push(store, json.loads(json.dumps(body)))  # as over the wire

    def syncer(self, role, **kw):
        return Syncer(self.stores[role], default_peers(role, BACKENDS),
                      self.authority, role, transport=self.transport, **kw)


def three():
    stores = {r: make_store() for r in URLS}
    return stores, Network(stores)


def converged(stores):
    prints = {r: fingerprint(materialize(s.all())) for r, s in stores.items()}
    return len(set(prints.values())) == 1, prints


# --- events and merge ------------------------------------------------------------

def test_event_validation():
    e = new_event("message", "c1", {"role": "user", "text": "hi"}, origin="pi")
    assert parse_event(e.to_json()) == e
    bad = [
        {**e.to_json(), "id": "xyz"},
        {**e.to_json(), "ts": -1},
        {**e.to_json(), "ts": True},
        {**e.to_json(), "ts": e.ts + 2 * 86_400_000},
        {**e.to_json(), "origin": "mac"},
        {**e.to_json(), "kind": "drop_table"},
        {**e.to_json(), "conv": "../etc"},
        {**e.to_json(), "data": "text"},
        {**e.to_json(), "data": {"text": "x" * 70_000}},
        {**e.to_json(), "kind": "job", "conv": "c1"},
        "not an object",
    ]
    for raw in bad:
        with pytest.raises(ValueError):
            parse_event(raw)


def test_merge_messages_rename_pin_lww():
    ev = [
        new_event("message", "c", {"role": "user", "text": "a"}, origin="pi", ts=10),
        new_event("message", "c", {"role": "assistant", "text": "b"}, origin="pc", ts=20),
        new_event("rename", "c", {"title": "old"}, origin="pi", ts=30),
        new_event("rename", "c", {"title": "new"}, origin="pc", ts=40),
        new_event("pin", "c", {"pinned": True}, origin="pi", ts=50),
        new_event("pin", "c", {"pinned": False}, origin="cloud", ts=45),
    ]
    for _ in range(20):
        random.shuffle(ev)
        c = materialize(ev + ev[:3])["conversations"]["c"]  # duplicates too
        assert [m["text"] for m in c.messages] == ["a", "b"]
        assert c.title == "new" and c.pinned is True and not c.deleted


def test_lww_tie_breaks_on_id():
    a = parse_event({"id": "0" * 32, "ts": 5, "origin": "pi", "kind": "rename",
                     "conv": "c", "data": {"title": "A"}})
    b = parse_event({"id": "f" * 32, "ts": 5, "origin": "pc", "kind": "rename",
                     "conv": "c", "data": {"title": "B"}})
    assert materialize([a, b])["conversations"]["c"].title == "B"
    assert materialize([b, a])["conversations"]["c"].title == "B"


def test_tombstones_are_permanent_and_order_independent():
    m1 = new_event("message", "c", {"role": "user", "text": "secret"}, origin="pi", ts=10)
    dele = new_event("delete", "c", {}, origin="pc", ts=20)
    late = new_event("message", "c", {"role": "user", "text": "late"}, origin="cloud", ts=30)
    ren = new_event("rename", "c", {"title": "x"}, origin="pi", ts=40)
    for order in ([m1, dele, late, ren], [ren, late, dele, m1], [dele, m1, ren, late]):
        c = materialize(order)["conversations"]["c"]
        assert c.deleted and c.messages == [] and c.title == ""


def test_delete_one_message():
    m1 = new_event("message", "c", {"role": "user", "text": "keep"}, origin="pi", ts=10)
    m2 = new_event("message", "c", {"role": "user", "text": "drop"}, origin="pi", ts=11)
    d = new_event("delete_message", "c", {"target": m2.id}, origin="pc", ts=5)  # skewed clock
    c = materialize([d, m2, m1])["conversations"]["c"]
    assert [m["text"] for m in c.messages] == ["keep"]


def all_text(store):
    """Every string in every event's data, as stored and as sent on."""
    events, _, _ = store.since(0, 10_000)
    return json.dumps([e.data for e in store.all()]) + json.dumps([e.to_json()["data"] for e in events])


@pytest.mark.parametrize("delete_first", [False, True])
def test_delete_redacts_the_text_in_the_store(delete_first):
    s = make_store()
    msgs = [new_event("message", "c", {"role": "user", "text": f"secret {i}"}, origin="pi",
                      ts=10 + i) for i in range(3)]
    ren = new_event("rename", "c", {"title": "Secret plans"}, origin="pc", ts=20)
    other = new_event("message", "d", {"role": "user", "text": "keep me"}, origin="pi", ts=21)
    dele = new_event("delete", "c", {}, origin="pc", ts=30)
    for batch in ([dele], msgs + [ren, other]) if delete_first else (msgs + [ren, other], [dele]):
        s.append(batch)
    text = all_text(s)
    assert "secret" not in text.lower() and "keep me" in text
    kept = {e.id: e for e in s.all()}
    for e in msgs + [ren]:  # ids, timestamps and kinds stay, so sync is unaffected
        assert kept[e.id].ts == e.ts and kept[e.id].kind == e.kind and kept[e.id].data == {}


@pytest.mark.parametrize("delete_first", [False, True])
def test_delete_message_redacts_its_target(delete_first):
    s = make_store()
    keep = new_event("message", "c", {"role": "user", "text": "fine"}, origin="pi", ts=1)
    drop = new_event("message", "c", {"role": "user", "text": "oops my password"}, origin="pi", ts=2)
    d = new_event("delete_message", "c", {"target": drop.id}, origin="pc", ts=3)
    for batch in ([d], [keep, drop]) if delete_first else ([keep, drop], [d]):
        s.append(batch)
    assert "password" not in all_text(s) and "fine" in all_text(s)
    assert [m["text"] for m in materialize(s.all())["conversations"]["c"].messages] == ["fine"]


def test_delete_message_only_applies_in_its_own_conversation():
    target = new_event("message", "c", {"role": "user", "text": "stays"}, origin="pi", ts=1)
    # a delete_message filed under another conversation does nothing to it
    wrong = new_event("delete_message", "d", {"target": target.id}, origin="pc", ts=2)
    s = make_store()
    s.append([target, wrong])
    assert "stays" in all_text(s)
    assert [m["text"] for m in materialize(s.all())["conversations"]["c"].messages] == ["stays"]


def test_redaction_reaches_every_peer():
    stores, net = three()
    msg = new_event("message", "c", {"role": "user", "text": "very private"}, origin="pi")
    stores["pi"].append([msg])
    for role in ("pi", "pc"):
        net.syncer(role).sync_all()  # everyone has the text now
    assert all("private" in all_text(s) for s in stores.values())
    stores["pc"].append([new_event("delete", "c", {}, origin="pc")])
    for _ in range(2):
        for role in ("pc", "pi"):
            net.syncer(role).sync_all()
    for role, s in stores.items():
        assert "private" not in all_text(s), role


def test_store_append_is_idempotent_and_since_pages():
    s = make_store()
    ev = [new_event("message", "c", {"text": str(i)}, origin="pi", ts=i) for i in range(5)]
    assert len(s.append(ev)) == 5
    assert s.append(ev) == []
    page, last, more = s.since(0, 2)
    assert [e.data["text"] for e in page] == ["0", "1"] and last == 2 and more
    page, last, more = s.since(4, 10)
    assert len(page) == 1 and last == 5 and not more


def test_push_rejects_bad_batches():
    s = make_store()
    assert handle_push(s, {"events": "x"})[0] == 400
    assert handle_push(s, [])[0] == 400
    big = [new_event("message", "c", {}, origin="pi").to_json() for _ in range(MAX_BATCH + 1)]
    assert handle_push(s, {"events": big})[0] == 400
    ok = new_event("message", "c", {}, origin="pi").to_json()
    assert handle_push(s, {"events": [ok, {"id": "bad"}]})[0] == 400
    assert s.all() == []  # all or nothing
    assert handle_pull(s, {"after": "x"})[0] == 400


# --- transport ----------------------------------------------------------------

def test_pi_and_pc_sync_through_the_cloud_only():
    stores, net = three()
    net.down = set()
    stores["pi"].append([new_event("message", "c1", {"text": "from pi"}, origin="pi")])
    # Pi and PC can't see each other (e.g. PC on another network): only the cloud
    pi_sync = Syncer(stores["pi"], [BACKENDS[2]], net.authority, "pi", transport=net.transport)
    pc_sync = Syncer(stores["pc"], [BACKENDS[2]], net.authority, "pc", transport=net.transport)
    pi_sync.sync_all()
    pc_sync.sync_all()
    assert converged(stores)[0]


def test_cloud_syncs_with_no_one():
    assert default_peers("cloud", BACKENDS) == []
    assert [b.role for b in default_peers("pi", BACKENDS)] == ["pc", "cloud"]


def test_cursors_avoid_resending():
    stores, net = three()
    stores["pi"].append([new_event("message", "c", {}, origin="pi") for _ in range(10)])
    pi = net.syncer("pi", batch=3)
    pi.sync_all()
    pi.sync_all()  # pulls its own events back once (they are new to the peer's log)
    calls = net.calls
    pi.sync_all()  # nothing new anywhere: just one pull per peer
    assert net.calls - calls == 2
    assert len(stores["cloud"].all()) == 10


def test_peer_database_reset_resends_everything():
    stores, net = three()
    stores["pi"].append([new_event("message", "c", {}, origin="pi") for _ in range(5)])
    pi = net.syncer("pi")
    pi.sync_all()
    stores["cloud"] = net.stores["cloud"] = make_store()  # Render disk wiped
    stores["cloud"].append([new_event("message", "d", {}, origin="cloud")])
    pi.sync_all()
    assert len(stores["cloud"].all()) == 6
    assert len(stores["pi"].all()) == 6


def test_peer_down_is_skipped():
    stores, net = three()
    net.down = {"pc"}
    stores["pi"].append([new_event("message", "c", {}, origin="pi")])
    result = net.syncer("pi").sync_all()
    assert result["pc"].startswith("failed") and result["cloud"].startswith("ok")


def test_wrong_token_refused():
    stores, net = three()
    other = Syncer(stores["pi"], [BACKENDS[2]], TokenAuthority("x" * 43), "pi",
                   transport=net.transport)
    stores["pi"].append([new_event("message", "c", {}, origin="pi")])
    assert other.sync_all()["cloud"].startswith("failed")
    assert stores["cloud"].all() == []


# --- convergence (property-style: many random histories) -------------------------

KIND_GEN = ["message"] * 6 + ["rename", "pin", "delete", "delete_message", "job"]


def random_event(rng, origin, ts, known_msgs):
    conv = f"c{rng.randrange(4)}"
    kind = rng.choice(KIND_GEN)
    data = {}
    if kind == "message":
        data = {"role": rng.choice(["user", "assistant"]), "text": new_id()[:6]}
    elif kind == "rename":
        data = {"title": new_id()[:4]}
    elif kind == "pin":
        data = {"pinned": rng.random() < 0.5}
    elif kind == "delete_message":
        if not known_msgs:
            kind, data = "message", {"role": "user", "text": "x"}
        else:
            data = {"target": rng.choice(known_msgs)}
    elif kind == "job":
        key = f"brief-{rng.randrange(3)}"
        conv, data = f"job:{key}", {"key": key}
    # clocks are skewed and ties happen
    return parse_event({"id": new_id(), "ts": ts + rng.randrange(-50, 50), "origin": origin,
                        "kind": kind, "conv": conv, "data": data})


@pytest.mark.parametrize("seed", range(40))
def test_three_stores_converge_under_random_interleavings(seed):
    rng = random.Random(seed)
    stores, net = three()
    syncers = {r: net.syncer(r, batch=rng.choice([1, 3, 50])) for r in ("pi", "pc")}
    everything = []
    msgs = []
    for step in range(rng.randrange(20, 80)):
        action = rng.random()
        if action < 0.5:
            origin = rng.choice(list(URLS))
            e = random_event(rng, origin, 1000 + step * 10, msgs)
            if e.kind == "message":
                msgs.append(e.id)
            stores[origin].append([e])
            everything.append(e)
        elif action < 0.6:
            # duplicate delivery: a peer re-sends events it already sent
            origin = rng.choice(list(URLS))
            if stores[origin].all():
                target = rng.choice([r for r in URLS if r != origin])
                dupes = rng.sample(stores[origin].all(), min(3, len(stores[origin].all())))
                handle_push(stores[target], {"events": [d.to_json() for d in dupes]})
        elif action < 0.75:
            net.down = {r for r in URLS if rng.random() < 0.3}
        else:
            role = rng.choice(["pi", "pc"])
            if role not in net.down:
                syncers[role].sync_all()
    # the network heals: a couple of full rounds
    net.down = set()
    for _ in range(2):
        for role in ("pi", "pc"):
            syncers[role].sync_all()
    ok, prints = converged(stores)
    assert ok, prints
    expected = fingerprint(materialize(everything))
    assert prints["cloud"] == expected
    tombstones = [e for e in everything if e.kind in ("delete", "delete_message")]
    for s in stores.values():
        assert len(s.all()) == len({e.id for e in everything})  # no duplicates stored
        for e in s.all():  # nothing a tombstone covers keeps its text
            if any(covered_by(t, e) for t in tombstones):
                assert e.data == {}, e


@pytest.mark.parametrize("seed", range(20))
def test_merge_is_order_and_duplicate_independent(seed):
    rng = random.Random(1000 + seed)
    msgs = []
    events = []
    for i in range(60):
        e = random_event(rng, rng.choice(list(URLS)), 1000 + i * 7, msgs)
        if e.kind == "message":
            msgs.append(e.id)
        events.append(e)
    want = fingerprint(materialize(events))
    for _ in range(5):
        shuffled = events + rng.sample(events, 20)
        rng.shuffle(shuffled)
        assert fingerprint(materialize(shuffled)) == want
        # and splitting into stores that each saw a different subset, then merging
        a, b = make_store(), make_store()
        for e in shuffled:
            (a if rng.random() < 0.5 else b).append([e])
        a.append(b.all())
        b.append(a.all())
        assert fingerprint(materialize(a.all())) == fingerprint(materialize(b.all())) == want


def test_extra_data_fields_are_kept_through_sync():
    """The history store adds fields (kind, tools, summary, archived...)
    that materialize ignores; sync must carry them unchanged."""
    stores, net = three()
    extras = [
        new_event("message", "c", {"role": "assistant", "text": "Done.", "kind": "confirm",
                                   "interrupted": False, "tools": ["move_event"],
                                   "source": "voice"}, origin="pi"),
        new_event("rename", "c", {"title": "Dentist", "summary": "Moved the dentist",
                                  "by": "auto", "ended": True}, origin="pi"),
        new_event("pin", "c", {"pinned": True, "archived": False}, origin="pi"),
    ]
    stores["pi"].append(extras)
    net.syncer("pi").sync_all()
    net.syncer("pc").sync_all()
    for role in ("cloud", "pc"):
        got = {e.id: e.data for e in stores[role].all()}
        assert got == {e.id: e.data for e in extras}, role
    c = materialize(stores["pc"].all())["conversations"]["c"]
    assert c.title == "Dentist" and c.pinned and c.messages[0]["text"] == "Done."
