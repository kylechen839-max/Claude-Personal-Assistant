"""History as an event log: every change is a failover sync Event, and the
history tables are a projection of the log (equal to sync.materialize).
Skipped when failover's event log isn't installed."""

import random
import sqlite3

import pytest

eventlog_mod = pytest.importorskip("assistant.failover.eventlog")
sync = pytest.importorskip("assistant.failover.sync")

from assistant.history import HistoryStore  # noqa: E402

NOW = 1_792_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        self.t += 0.01
        return self.t


def make(tmp_path, name="h.sqlite", origin="pc", **kw):
    return HistoryStore(tmp_path / name, clock=Clock(), origin=origin, **kw)


def projection(store):
    """{conv: (title, pinned, [message event ids in order])} from the tables."""
    out = {}
    for (cid,) in store._con.execute("SELECT id FROM conversations WHERE last_ts > 0"):
        row = store._con.execute("SELECT title, pinned FROM conversations WHERE id = ?",
                                 (cid,)).fetchone()
        ids = [r[0] for r in store._con.execute(
            "SELECT event_id FROM messages WHERE conversation_id = ? "
            "ORDER BY ev_ts, event_id", (cid,))]
        out[cid] = (row[0], bool(row[1]), ids)
    return out


def materialized(events):
    state = sync.materialize(events)["conversations"]
    return {k: (c.title, c.pinned, [m["id"] for m in c.messages])
            for k, c in state.items() if not c.deleted}


def kinds(store, cid):
    return [e.kind for e in store.eventlog.by_conv(cid)]


# ---------- every change is an event ----------
def test_each_change_is_an_event(tmp_path):
    s = make(tmp_path)
    assert isinstance(s.eventlog, eventlog_mod.SqliteEventLog)
    cid = s.create("wake")
    assert kinds(s, cid) == []  # nothing to sync until something is said
    s.append(cid, "user", "remind me about the dentist")
    s.append(cid, "assistant", "Done.", tool_events=[{"tool": "x", "args": {},
                                                      "status": "ok"}])
    s.finish(cid, "Dentist reminder", "Kyle asked for a dentist reminder.")
    s.update(cid, title="Dentist", pinned=True)
    s.update(cid, archived=True)
    assert kinds(s, cid) == ["message", "message", "rename", "rename", "pin", "pin"]
    events = s.eventlog.by_conv(cid)
    assert all(len(e.id) == 32 and e.origin == "pc" and isinstance(e.ts, int)
               for e in events)
    assert events[0].data == {"role": "user", "text": "remind me about the dentist",
                              "source": "wake"}
    assert events[2].data["by"] == "auto" and events[2].data["ended"]
    assert events[3].data == {"title": "Dentist", "by": "user"}
    assert events[5].data == {"pinned": True, "archived": True}
    conv = s.get(cid)
    assert (conv["title"], conv["pinned"], conv["archived"]) == ("Dentist", True, True)
    assert conv["summary"] == "Kyle asked for a dentist reminder."
    assert projection(s) == materialized(s.eventlog.all())


def test_delete_is_a_tombstone_that_redacts_and_purges(tmp_path):
    s = make(tmp_path)
    cid = s.create("text")
    s.append(cid, "user", "my locker code is zebra4417")
    s.append(cid, "assistant", "Noted.")
    s.finish(cid, "Locker zebra4417", "Locker code zebra4417.")
    assert [h["id"] for h in s.search("zebra4417")] == [cid]
    s.delete(cid)
    assert kinds(s, cid)[-1] == "delete"
    assert all(e.data == {} for e in s.eventlog.by_conv(cid))  # text is gone
    assert s.search("zebra4417") == [] and s.count(archived=None) == 0
    raw = s._con.execute("SELECT group_concat(data) FROM sync_events").fetchone()[0]
    assert "zebra" not in raw
    with pytest.raises(KeyError):
        s.get(cid)
    s.close()
    # nor anywhere in the database file (secure_delete, FTS purge, WAL checkpoint)
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("h.sqlite*"))
    assert b"zebra4417" not in blob


def test_barge_in_edit_is_delete_message_plus_replacement(tmp_path):
    s = make(tmp_path)
    cid = s.create("voice")
    s.append(cid, "user", "tell me a long story")
    s.append(cid, "assistant", "Once upon a time there was a secret ending.")
    s.append(cid, "user", "stop")  # (in practice the edit comes first)
    s.mark_interrupted(cid, "Once upon a time")
    ev = s.eventlog.by_conv(cid)
    assert [e.kind for e in ev][-2:] == ["delete_message", "message"]
    original = ev[1]
    assert original.data == {}  # the unsaid words are redacted in the log
    assert ev[-2].data == {"target": original.id}
    assert ev[-1].ts == original.ts  # same place in the conversation
    msgs = s.get(cid)["messages"]
    assert [m["text"] for m in msgs] == ["tell me a long story", "Once upon a time", "stop"]
    assert msgs[1]["interrupted"]
    assert projection(s) == materialized(s.eventlog.all())


# ---------- the projection is materialize(events) ----------
@pytest.mark.parametrize("seed", range(8))
def test_projection_equals_materialize_under_random_operations(tmp_path, seed):
    rnd = random.Random(seed)
    s = make(tmp_path)
    convs = []
    for _ in range(120):
        op = rnd.random()
        if op < 0.15 or not convs:
            convs.append(s.create(rnd.choice(["wake", "text", "web", "voice"])))
        cid = rnd.choice(convs)
        try:
            if op < 0.55:
                s.append(cid, rnd.choice(["user", "assistant"]), f"words {rnd.random()}")
            elif op < 0.65:
                s.finish(cid, f"Title {rnd.randint(0, 9)}", "summary")
            elif op < 0.75:
                s.update(cid, title=f"Mine {rnd.randint(0, 9)}")
            elif op < 0.85:
                s.update(cid, pinned=rnd.random() < 0.5)
            elif op < 0.92:
                s.mark_interrupted(cid, "partial")
            else:
                s.delete(cid)
                convs.remove(cid)
        except KeyError:
            convs.remove(cid)
    assert projection(s) == materialized(s.eventlog.all())


# ---------- sync between backends ----------
def push(src, dst, after=0):
    events, last, _ = src.eventlog.since(after, 10_000)
    return dst.sync_log.append(events), last


def test_synced_events_are_projected_at_once(tmp_path):
    pi, pc = make(tmp_path, "pi.sqlite", "pi"), make(tmp_path, "pc.sqlite", "pc")
    cid = pi.create("wake")
    pi.append(cid, "user", "how do error bars work")
    pi.append(cid, "assistant", "Standard error.")
    pi.finish(cid, "Error bars", "About error bars.")
    added, _ = push(pi, pc)
    assert len(added) == 3
    conv = pc.get(cid)
    assert conv["title"] == "Error bars" and conv["source"] == "wake"
    assert [h["id"] for h in pc.search("error bars")] == [cid]
    assert push(pi, pc)[0] == []  # idempotent
    # the PC renames and deletes a message; the Pi gets both
    pc.update(cid, title="Stats", pinned=True)
    pc.mark_interrupted(cid, "Standard")
    push(pc, pi)
    assert projection(pi) == projection(pc) == materialized(pc.eventlog.all())
    assert pi.get(cid)["messages"][-1]["text"] == "Standard"
    # a delete on the Pi reaches the PC: rows, text and index entries go
    pi.delete(cid)
    push(pi, pc)
    assert pc.search("error") == [] and pc.count(archived=None) == 0
    assert all(e.data == {} for e in pc.eventlog.by_conv(cid)
               if e.kind != "delete_message")


def test_any_arrival_order_gives_the_same_history(tmp_path):
    a = make(tmp_path, "a.sqlite")
    cids = []
    for i in range(4):
        cid = a.create("text")
        cids.append(cid)
        for j in range(3):
            a.append(cid, "user" if j % 2 == 0 else "assistant", f"c{i} m{j}")
        a.finish(cid, f"Conv {i}", "s")
    a.update(cids[0], title="Renamed", pinned=True)
    a.mark_interrupted(cids[1], "short")
    a.delete(cids[2])
    events = a.eventlog.all()
    for seed in range(5):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        b = make(tmp_path, f"b{seed}.sqlite")
        for e in shuffled:  # one at a time, in a random order
            b.sync_log.append([e])
        assert projection(b) == projection(a) == materialized(events), seed


def test_message_arriving_after_its_tombstone_is_stored_redacted(tmp_path):
    a, b = make(tmp_path, "a.sqlite"), make(tmp_path, "b.sqlite")
    cid = a.create("text")
    a.append(cid, "user", "late secret")
    a.delete(cid)
    message, tombstone = a.eventlog.by_conv(cid)
    # b hears about the delete first, then gets the original message
    b.sync_log.append([sync.Event(tombstone.id, tombstone.ts, tombstone.origin,
                                  "delete", cid, {})])
    late = sync.Event(message.id, message.ts, "pi", "message", cid,
                      {"role": "user", "text": "late secret"})
    [stored] = b.sync_log.append([late])
    assert stored.data == {}
    assert b.count(archived=None) == 0 and b.search("secret") == []


def test_last_writer_wins_on_titles(tmp_path):
    s = make(tmp_path)
    cid = s.create("text")
    s.append(cid, "user", "hi")
    s.update(cid, title="Newest")
    newest = s.eventlog.by_conv(cid)[-1]
    older = sync.Event("0" * 32, newest.ts - 1000, "pi", "rename", cid,
                       {"title": "Older", "by": "user"})
    s.sync_log.append([older])  # arrives later, but was made earlier
    assert s.get(cid)["title"] == "Newest"
    assert projection(s) == materialized(s.eventlog.all())


def test_events_missed_by_the_tables_are_projected_at_startup(tmp_path):
    s = make(tmp_path)
    cid = s.create("text")
    s.append(cid, "user", "first")
    # as if the program died between the log and the tables
    s.eventlog.record(sync.new_event("message", cid, {"role": "assistant",
                                                       "text": "second"}, origin="pc"))
    assert s.get(cid)["message_count"] == 1
    path = s.path
    s.close()
    again = HistoryStore(path)
    assert [m["text"] for m in again.get(cid)["messages"]] == ["first", "second"]
    assert projection(again) == materialized(again.eventlog.all())
    again.close()


def test_sync_log_is_an_event_log(tmp_path):
    s = make(tmp_path)
    log = s.sync_log
    assert log.log_id == s.eventlog.log_id
    cid = s.create("web")
    s.append(cid, "user", "x")
    events, last, more = log.since(0, 10)
    assert len(events) == 1 and last >= 1 and not more
    assert log.by_conv(cid) == events
    log.meta_set("sync:pi:pull", "5:abc")
    assert log.meta_get("sync:pi:pull") == "5:abc"
    status, body = sync.handle_pull(log, {"after": "0"})
    assert status == 200 and body["events"][0]["data"]["text"] == "x"
    status, body = sync.handle_push(log, {"events": []})
    assert status == 200


def test_history_tables_and_event_tables_share_one_file(tmp_path):
    s = make(tmp_path)
    s.close()
    con = sqlite3.connect(tmp_path / "h.sqlite")
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"conversations", "messages", "sync_events", "sync_meta"} <= names
