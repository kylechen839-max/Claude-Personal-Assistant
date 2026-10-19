"""SqliteEventLog specifics (the sync contract itself runs on it in
test_failover_sync.py): persistence and coexisting with other tables."""

import sqlite3
import threading

from assistant.failover.eventlog import SqliteEventLog
from assistant.failover.sync import new_event


def test_survives_a_restart(tmp_path):
    path = tmp_path / "history.db"
    log = SqliteEventLog(path)
    e = new_event("message", "c", {"role": "user", "text": "hi"}, origin="pi")
    log.append([e])
    log.meta_set("sync:cloud:pull", "7:abc")
    log_id = log.log_id
    log.close()
    again = SqliteEventLog(path)
    assert again.log_id == log_id
    assert [x.id for x in again.all()] == [e.id]
    assert again.meta_get("sync:cloud:pull") == "7:abc"


def test_seq_is_never_reused(tmp_path):
    log = SqliteEventLog(tmp_path / "h.db")
    log.append([new_event("message", "c", {}, origin="pi") for _ in range(3)])
    log._db.execute("DELETE FROM sync_events WHERE seq = 3")  # e.g. a manual cleanup
    log.append([new_event("message", "c", {}, origin="pi")])
    _, last, _ = log.since(0, 10)
    assert last == 4


def test_shares_a_file_with_the_history_tables(tmp_path):
    path = tmp_path / "history.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
    db.execute("INSERT INTO messages (text) VALUES ('history row')")
    db.commit()
    db.close()
    log = SqliteEventLog(path)
    log.append([new_event("message", "c", {"text": "x"}, origin="pc")])
    rows = log._db.execute("SELECT text FROM messages").fetchall()
    assert rows == [("history row",)]


def test_concurrent_appends(tmp_path):
    log = SqliteEventLog(tmp_path / "h.db")
    events = [new_event("message", "c", {"text": str(i)}, origin="pi") for i in range(200)]

    def worker(chunk):
        log.append(chunk)
        log.append(events[:20])  # duplicates from another thread

    threads = [threading.Thread(target=worker, args=(events[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log.all()) == 200


def test_secure_delete_is_on(tmp_path):
    from assistant.failover.eventlog import SqliteEventLog

    log = SqliteEventLog(tmp_path / "events.sqlite")
    assert log._db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    log.close()


def test_deleted_text_leaves_no_trace_in_the_database_files(tmp_path):
    from assistant.failover.sync import new_event as ev

    path = tmp_path / "h.db"
    log = SqliteEventLog(path)
    secret = "zebra-unicorn-password-1234"
    log.append([ev("message", "c", {"role": "user", "text": secret}, origin="pi")])
    log.append([ev("delete", "c", {}, origin="pi")])
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert secret.encode() not in raw
    log.close()
