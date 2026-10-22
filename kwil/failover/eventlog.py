"""A persistent EventLog (see sync.py) in SQLite.

Its own two tables, `sync_events` and `sync_meta`, so it can share the
history database file without touching the history store's tables or
names (the history store's own `append(conv, role, text)` is a different
thing). The history store records each change it makes as an event here
(`record`), and applies events that arrive from peers (`append` returns
the new ones, already redacted where a tombstone covers them).

- `seq` is INTEGER PRIMARY KEY AUTOINCREMENT, so it is never reused.
- Ids are the protocol's 32-hex strings, timestamps unix ms.
- Tombstones redact on insert, in either order (sync.covered_by).
- Thread-safe: one connection, one lock (the web app runs turns in
  worker threads).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from kwil.failover.sync import Event, covered_by, new_id, redacted

COLS = "seq, id, ts, origin, kind, conv, data"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_events (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    id     TEXT NOT NULL UNIQUE,
    ts     INTEGER NOT NULL,
    origin TEXT NOT NULL,
    kind   TEXT NOT NULL,
    conv   TEXT NOT NULL,
    data   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sync_events_conv ON sync_events (conv);
CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class SqliteEventLog:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        # Redaction rewrites rows in place; without this, SQLite (e.g. the
        # Windows build) can leave the deleted text in free pages.
        self._db.execute("PRAGMA secure_delete = ON")
        # another connection (the history store) may hold the write lock briefly
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            row = self._db.execute(
                "SELECT value FROM sync_meta WHERE key = 'log_id'").fetchone()
            if row is None:
                self.log_id = new_id()
                self._db.execute("INSERT INTO sync_meta VALUES ('log_id', ?)", (self.log_id,))
            else:
                self.log_id = row[0]

    @staticmethod
    def _event(row) -> Event:
        _, eid, ts, origin, kind, conv, data = row
        return Event(eid, ts, origin, kind, conv, json.loads(data))

    def _tombstones(self, conv: str) -> list[Event]:
        rows = self._db.execute(
            f"SELECT {COLS} FROM sync_events WHERE conv = ? AND kind IN ('delete', 'delete_message')",
            (conv,)).fetchall()
        return [self._event(r) for r in rows]

    def append(self, events: Iterable[Event]) -> list[Event]:
        added = []
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for e in events:
                    if self._db.execute("SELECT 1 FROM sync_events WHERE id = ?",
                                        (e.id,)).fetchone():
                        continue
                    if any(covered_by(t, e) for t in self._tombstones(e.conv)):
                        e = redacted(e)
                    self._db.execute(
                        "INSERT INTO sync_events (id, ts, origin, kind, conv, data) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (e.id, e.ts, e.origin, e.kind, e.conv, json.dumps(e.data)))
                    added.append(e)
                    if e.kind in ("delete", "delete_message"):
                        for row in self._db.execute(
                                f"SELECT {COLS} FROM sync_events WHERE conv = ? AND data != '{{}}'",
                                (e.conv,)).fetchall():
                            old = self._event(row)
                            if covered_by(e, old):
                                self._db.execute("UPDATE sync_events SET data = '{}' WHERE id = ?",
                                                 (old.id,))
                self._db.execute("COMMIT")
                if any(e.kind in ("delete", "delete_message") for e in added):
                    # redacted text must not linger in the write-ahead log
                    self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return added

    def record(self, event: Event) -> Event:
        """Add one locally made event; returns it as stored."""
        added = self.append([event])
        return added[0] if added else event

    def since(self, seq: int, limit: int) -> tuple[list[Event], int, bool]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLS} FROM sync_events WHERE seq > ? ORDER BY seq LIMIT ?",
                (max(0, seq), limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        last = rows[-1][0] if rows else seq
        return [self._event(r) for r in rows], last, more

    def by_conv(self, conv: str) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLS} FROM sync_events WHERE conv = ? ORDER BY seq", (conv,)).fetchall()
        return [self._event(r) for r in rows]

    def all(self) -> list[Event]:
        with self._lock:
            rows = self._db.execute(f"SELECT {COLS} FROM sync_events ORDER BY seq").fetchall()
        return [self._event(r) for r in rows]

    def meta_get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM sync_meta WHERE key = ?",
                                   (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO sync_meta VALUES (?, ?) ON CONFLICT(key) "
                             "DO UPDATE SET value = excluded.value", (key, value))

    def close(self) -> None:
        with self._lock:
            self._db.close()
