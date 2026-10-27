"""Conversation history: every conversation saved to SQLite, searchable,
and resumable.

`HistoryStore` is the database (stdlib sqlite3, WAL mode, one file).
`Recorder` is what `ClaudeChat` writes through: each turn is committed as
it happens, so a crash or power cut loses at most the turn in flight. When
a conversation ends, a short title and summary are generated in a
background thread (one cheap call to a fast model, or the first thing Kyle
said if the API is down), so a reply never waits on it.

Search is SQLite FTS5 (ranked with bm25, with highlighted snippets) over
message text, titles and summaries; if this SQLite build has no FTS5 it
falls back to LIKE matching. `rebuild_history` turns a stored conversation
back into ClaudeChat history for "continue this one".

Ids in URLs are random (`secrets.token_urlsafe`), never row numbers, and
every query is scoped to the store's owner.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

SOURCES = ("wake", "text", "web", "voice")
SOURCE_LABELS = {"wake": "PC / Pi (spoken)", "text": "typed", "web": "phone",
                 "voice": "phone voice"}
DEFAULT_TITLE_MODEL = "claude-haiku-5-5"
TITLE_MAX_WORDS = 6
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
# Snippet highlight markers (control characters never typed by a person).
MARK_START, MARK_END = "\x02", "\x03"
ELLIPSIS = "…"

TITLE_PROMPT = (
    "Below is a conversation between Kyle and his voice assistant. Reply with "
    "JSON only, like {\"title\": \"...\", \"summary\": \"...\"}. The title is "
    "at most six words naming the topic (no quotes, no final period), e.g. "
    "\"Lab report error bars\". The summary is one or two plain sentences "
    "saying what was discussed or decided."
)

# Words that say "a conversation" rather than what it was about; dropped
# from search queries ("our conversation about the lab report").
STOPWORDS = {
    "a", "an", "the", "our", "my", "we", "us", "i", "me", "you", "about",
    "on", "of", "for", "to", "with", "and", "or", "that", "this", "conversation",
    "conversations", "chat", "talk", "talked", "talking", "discuss",
    "discussed", "said", "say", "what", "did", "was", "were", "is", "it",
    "continue", "resume", "one", "thing", "stuff",
}

SCHEMA_VERSION = 1
# Versioned migrations: MIGRATIONS[n] takes a database from version n to
# n + 1. Add new ones at the end; never edit a released one.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE conversations (
        rid INTEGER PRIMARY KEY,
        id TEXT NOT NULL UNIQUE,
        owner TEXT NOT NULL,
        source TEXT NOT NULL,
        started_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        ended_at REAL,
        title TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        title_by_user INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        -- (ts, event id) of the event that set each field: last writer wins
        title_order TEXT NOT NULL DEFAULT '',
        summary_order TEXT NOT NULL DEFAULT '',
        pin_order TEXT NOT NULL DEFAULT '',
        last_ts INTEGER NOT NULL DEFAULT 0  -- newest event ts (unix ms)
    );
    CREATE INDEX conversations_owner_updated ON conversations(owner, updated_at);
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        conversation_id TEXT NOT NULL
            REFERENCES conversations(id) ON DELETE CASCADE,
        ev_ts INTEGER NOT NULL,  -- messages are ordered by (ev_ts, event_id)
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        kind TEXT NOT NULL DEFAULT 'chat',
        text TEXT NOT NULL,
        created_at REAL NOT NULL,
        interrupted INTEGER NOT NULL DEFAULT 0,
        tool_events TEXT
    );
    CREATE INDEX messages_order ON messages(conversation_id, ev_ts, event_id);
    CREATE TABLE history_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE memory_vectors (
        conversation_id TEXT NOT NULL
            REFERENCES conversations(id) ON DELETE CASCADE,
        vector_id TEXT NOT NULL,
        PRIMARY KEY (conversation_id, vector_id)
    );
    """,
]

FTS_SQL = """
CREATE VIRTUAL TABLE messages_fts USING fts5(
    text, content='messages', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE conversations_fts USING fts5(
    title, summary, content='conversations', content_rowid='rid',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER messages_au AFTER UPDATE OF text ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER conversations_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO conversations_fts(rowid, title, summary)
        VALUES (new.rid, new.title, new.summary);
END;
CREATE TRIGGER conversations_ad AFTER DELETE ON conversations BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, title, summary)
        VALUES ('delete', old.rid, old.title, old.summary);
END;
CREATE TRIGGER conversations_au AFTER UPDATE OF title, summary ON conversations
BEGIN
    INSERT INTO conversations_fts(conversations_fts, rowid, title, summary)
        VALUES ('delete', old.rid, old.title, old.summary);
    INSERT INTO conversations_fts(rowid, title, summary)
        VALUES (new.rid, new.title, new.summary);
END;
"""


class HistoryError(Exception):
    pass


class HistoryFull(HistoryError):
    """An import would take the store past its `max_mb` (nothing was kept)."""


def new_id() -> str:
    """16 random URL-safe characters (96 bits): not guessable or countable.
    Never starting with "-" or "_", so an id works as a command-line
    argument (`--continue ID`) and doesn't look like a flag."""
    while True:
        cid = secrets.token_urlsafe(12)
        if cid[0].isalnum():
            return cid


def valid_id(conversation_id) -> bool:
    return isinstance(conversation_id, str) and bool(ID_RE.match(conversation_id))


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except sqlite3.Error:
        return False


def _clean(text: str) -> str:
    """Mask API keys/tokens (same rules as the log file) and drop the
    snippet marker characters, so nothing secret is ever stored."""
    from kwil.config import redact

    text = redact(str(text or ""))
    return text.replace(MARK_START, "").replace(MARK_END, "")


def query_terms(query: str) -> list[str]:
    """Lower-case search words; filler like 'our conversation about' is
    dropped unless nothing else is left."""
    words = re.findall(r"\w+", (query or "").lower())
    kept = [w for w in words if w not in STOPWORDS]
    return (kept or words)[:12]


def _fts_query(terms: list[str], any_term: bool = False) -> str:
    # Every word quoted (no FTS syntax from user input); the last one is a
    # prefix so search-as-you-type matches partial words.
    quoted = [f'"{t}"' for t in terms]
    quoted[-1] += "*"
    return (" OR " if any_term else " ").join(quoted)


MESSAGE_KINDS = ("chat", "confirm", "note")  # note: added by a feature, not said
MAX_TEXT = 8000  # characters per message (an event is at most 64 KB of JSON)


@dataclass(frozen=True)
class _LocalEvent:
    """An event when failover's sync module isn't installed (same fields
    as kwil.failover.sync.Event; nothing to sync it with)."""
    id: str
    ts: int
    origin: str
    kind: str
    conv: str
    data: dict = field(default_factory=dict, hash=False, compare=False)


def _sync_module():
    try:
        from kwil.failover import sync
    except ImportError:
        return None
    return sync


def _open_eventlog(path: str):
    """failover's SqliteEventLog on the same database file, if installed."""
    try:
        from kwil.failover.eventlog import SqliteEventLog
    except ImportError:
        return None
    return SqliteEventLog(path)


class _ProjectingLog:
    """EventLog for the Syncer: SqliteEventLog, plus each newly inserted
    event (as stored, i.e. redacted when a tombstone covers it) projected
    into the history tables right away."""

    def __init__(self, store: "HistoryStore"):
        self._store = store
        self._log = store.eventlog
        self.log_id = self._log.log_id

    def append(self, events):
        with self._store._lock:
            added = self._log.append(events)
            for e in added:
                self._store._apply(e)
        return added

    def since(self, seq, limit):
        return self._log.since(seq, limit)

    def by_conv(self, conv):
        return self._log.by_conv(conv)

    def meta_get(self, key):
        return self._log.meta_get(key)

    def meta_set(self, key, value):
        self._log.meta_set(key, value)


class HistoryStore:
    """SQLite store of conversations and their messages for one owner.

    Thread-safe: one connection guarded by a lock (the web app calls in
    from several threads, titles are written from a background thread)."""

    def __init__(self, path: str | Path = "data/history.sqlite", *,
                 owner: str = "default", retention_days: float = 0,
                 max_mb: float = 0, use_fts: bool | None = None,
                 clock: Callable[[], float] = time.time,
                 origin: str = "pc", eventlog=None):
        """`origin`: this backend's role in events ("pi", "pc", "cloud").
        `eventlog`: None = failover's SqliteEventLog in the same file when
        that module is installed; False = none (history still works, it
        just has nothing to sync); or an EventLog object."""
        self.path = str(path)
        self.owner = owner
        self.origin = origin
        self.retention_days = retention_days or 0
        self.max_mb = max_mb or 0
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.path, check_same_thread=False,
                                    isolation_level=None, timeout=10)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._con.execute("PRAGMA journal_mode = WAL")
            # FULL: a committed turn survives a power cut too (Pi SD card).
            self._con.execute("PRAGMA synchronous = FULL")
        # Deleted rows are overwritten with zeros, not just unlinked.
        self._con.execute("PRAGMA secure_delete = ON")
        self._migrate()
        if use_fts is None:
            use_fts = fts5_available()
        self.fts = self._ensure_fts() if use_fts else self._drop_fts_triggers()
        self.eventlog = _open_eventlog(self.path) if eventlog is None else (
            eventlog or None)
        # Redaction rewrites rows in the event log through its own
        # connection: zero the old bytes there too (SQLite builds differ in
        # the default; Python's on Windows leaves them in the file).
        db = getattr(self.eventlog, "_db", None)
        if isinstance(db, sqlite3.Connection):
            db.execute("PRAGMA secure_delete = ON")
        self._catch_up()
        self.prune()

    # ---------- setup ----------
    def _migrate(self) -> None:
        with self._lock:
            version = self._con.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise HistoryError(
                    f"{self.path} is from a newer version of the assistant "
                    f"(schema {version}, this one knows {len(MIGRATIONS)})")
            if version == 0:
                # Must be set before the first table: lets the size guard
                # give freed pages back to the file system.
                self._con.execute("PRAGMA auto_vacuum = INCREMENTAL")
            for n in range(version, len(MIGRATIONS)):
                self._script(MIGRATIONS[n] + f"\nPRAGMA user_version = {n + 1};")
                log.info("History: schema migrated to version %d", n + 1)

    def _ensure_fts(self) -> bool:
        """Create the FTS5 index if missing (and fill it from existing rows,
        e.g. a database first used on a build without FTS5)."""
        with self._lock:
            names = {r[0] for r in self._con.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%fts' "
                "OR type = 'trigger'")}
            if {"messages_fts", "messages_ai"} <= names:
                return True
            try:
                self._script(
                    "DROP TABLE IF EXISTS messages_fts;\n"
                    "DROP TABLE IF EXISTS conversations_fts;\n" + FTS_SQL + """
                    INSERT INTO messages_fts(messages_fts) VALUES ('rebuild');
                    INSERT INTO conversations_fts(conversations_fts)
                        VALUES ('rebuild');
                    INSERT INTO conversations_fts(conversations_fts, rank)
                        VALUES ('rank', 'bm25(4.0, 1.5)');""")
                self._fts_secure_delete()
                return True
            except sqlite3.Error as e:
                log.warning("History: no FTS5 (%s); using LIKE search", e)
                return False

    def _fts_secure_delete(self) -> None:
        """SQLite 3.42+: deleting from the index removes the words from its
        segments right away (older versions: `_purge` optimizes instead)."""
        for table in ("messages_fts", "conversations_fts"):
            try:
                self._con.execute(f"INSERT INTO {table}({table}, rank) "
                                  "VALUES ('secure-delete', 1)")
            except sqlite3.Error:
                return

    def _drop_fts_triggers(self) -> bool:
        """Without FTS5 the index triggers would make every insert fail
        (a database copied from a build that has it); drop them. The index
        is rebuilt the next time FTS5 is available."""
        with self._lock:
            for (name,) in self._con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' AND "
                    "(name LIKE 'messages_a_' OR name LIKE 'conversations_a_')"
            ).fetchall():
                self._con.execute(f"DROP TRIGGER {name}")
        return False

    def _script(self, sql: str) -> None:
        """Run a multi-statement script in one transaction (all or nothing)."""
        try:
            self._con.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
        except Exception:
            if self._con.in_transaction:
                self._con.execute("ROLLBACK")
            raise

    @property
    def schema_version(self) -> int:
        return self._con.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._con.close()
            if self.eventlog is not None and hasattr(self.eventlog, "close"):
                self.eventlog.close()

    # ---------- events ----------
    # Every change to history is an event (kwil.failover.sync.Event)
    # recorded in failover's SqliteEventLog, which lives in the same file
    # (its own sync_events / sync_meta tables) and syncs between the Pi,
    # the PC and the cloud. The conversations / messages / FTS tables here
    # are a projection of those events: `_apply` is the only code that
    # writes them, for local and synced events alike. Message and title
    # text is never changed in place; a barge-in edit is a delete_message
    # tombstone plus a new message, and a delete is a tombstone, which
    # redacts the covered events' data in the log and removes the rows
    # (and their search index entries) here.
    def _event(self, kind: str, conv: str, data: dict, ts: int | None = None,
               eid: str | None = None):
        if ts is None:
            # Never earlier than the conversation's newest event, so the
            # order of a turn is kept even within one millisecond.
            row = self._con.execute("SELECT last_ts FROM conversations WHERE id = ?",
                                    (conv,)).fetchone()
            ts = max(int(self.clock() * 1000), (row[0] + 1) if row else 0)
        raw = {"id": eid or secrets.token_hex(16), "ts": ts, "origin": self.origin,
               "kind": kind, "conv": conv, "data": data}
        sync = _sync_module()
        return sync.parse_event(raw) if sync else _LocalEvent(**raw)

    def _record(self, kind: str, conv: str, data: dict, ts: int | None = None,
                eid: str | None = None):
        """Make an event, add it to the event log, and project it."""
        with self._lock:
            event = self._event(kind, conv, data, ts, eid)
            if self.eventlog is not None:
                event = self.eventlog.record(event)
            self._apply(event)
            return event

    def _apply(self, e) -> None:
        """Project one event (as stored in the log, so already redacted if
        a tombstone covers it) into the tables. Idempotent: replaying an
        event, or replaying events in another order, gives the same rows."""
        if e.kind in ("message", "rename", "pin") and not e.data:
            return  # redacted: its conversation or message was deleted
        if e.kind == "job":
            return
        order = f"{e.ts:015d}:{e.id}"
        d = e.data
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                if e.kind == "delete":
                    self._con.execute("DELETE FROM conversations WHERE id = ?", (e.conv,))
                elif e.kind == "delete_message":
                    self._con.execute("DELETE FROM messages WHERE event_id = ?",
                                      (str(d.get("target", "")),))
                else:
                    self._ensure_row(e.conv, d.get("source"), e.ts)
                    if e.kind == "message":
                        self._apply_message(e, d)
                    elif e.kind == "rename":
                        self._apply_rename(e, d, order)
                    elif e.kind == "pin":
                        self._con.execute(
                            "UPDATE conversations SET pinned = ?, archived = "
                            "coalesce(?, archived), pin_order = ? "
                            "WHERE id = ? AND pin_order < ?",
                            (int(bool(d.get("pinned"))),
                             None if "archived" not in d else int(bool(d["archived"])),
                             order, e.conv, order))
                    self._con.execute(
                        "UPDATE conversations SET last_ts = max(last_ts, ?) WHERE id = ?",
                        (e.ts, e.conv))
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise

    def _ensure_row(self, conv: str, source, ts: int) -> None:
        source = source if source in SOURCES else "web"
        self._con.execute(
            "INSERT OR IGNORE INTO conversations (id, owner, source, started_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv, self.owner, source, ts / 1000, ts / 1000))

    def _apply_message(self, e, d: dict) -> None:
        role = d.get("role")
        if role not in ("user", "assistant"):
            return
        tools = d.get("tools") or None
        inserted = self._con.execute(
            "INSERT OR IGNORE INTO messages (event_id, conversation_id, ev_ts, role, "
            "kind, text, created_at, interrupted, tool_events) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e.id, e.conv, e.ts, role,
             d.get("kind") if d.get("kind") in MESSAGE_KINDS else "chat",
             str(d.get("text", "")), e.ts / 1000, int(bool(d.get("interrupted"))),
             json.dumps(tools, separators=(",", ":")) if tools else None)).rowcount
        if inserted:
            self._con.execute(
                "UPDATE conversations SET started_at = min(started_at, ?), "
                "updated_at = max(updated_at, ?) WHERE id = ?",
                (e.ts / 1000, e.ts / 1000, e.conv))

    def _apply_rename(self, e, d: dict, order: str) -> None:
        if "title" in d:
            self._con.execute(
                "UPDATE conversations SET title = ?, title_by_user = ?, "
                "title_order = ? WHERE id = ? AND title_order < ?",
                (str(d["title"])[:200], int(d.get("by") == "user"), order,
                 e.conv, order))
        if "summary" in d:
            self._con.execute(
                "UPDATE conversations SET summary = ?, summary_order = ? "
                "WHERE id = ? AND summary_order < ?",
                (str(d["summary"])[:1000], order, e.conv, order))
        if d.get("ended"):
            self._con.execute(
                "UPDATE conversations SET ended_at = max(coalesce(ended_at, 0), ?) "
                "WHERE id = ?", (e.ts / 1000, e.conv))

    def _catch_up(self) -> None:
        """Project events the tables haven't seen yet (a crash between the
        log and the projection, or a database whose tables were rebuilt).
        Replaying is safe: `_apply` is idempotent."""
        if self.eventlog is None:
            return
        with self._lock:
            cursor = int(self._meta_get("projected_seq") or 0)
            while True:
                events, last, more = self.eventlog.since(cursor, 500)
                for e in events:
                    self._apply(e)
                cursor = last
                if not more:
                    break
            self._meta_set("projected_seq", str(cursor))

    def _meta_get(self, key: str) -> str | None:
        row = self._con.execute("SELECT value FROM history_meta WHERE key = ?",
                                (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._con.execute("INSERT INTO history_meta VALUES (?, ?) ON CONFLICT(key) "
                          "DO UPDATE SET value = excluded.value", (key, value))

    @property
    def sync_log(self):
        """The EventLog to give failover's Syncer: the event log, plus
        projection of every event a peer sends (so it shows up at once)."""
        return None if self.eventlog is None else _ProjectingLog(self)

    # ---------- writing ----------
    def create(self, source: str, started_at: float | None = None) -> str:
        """A new conversation id (the row exists locally; it becomes part
        of the synced history with its first message)."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        now = self.clock() if started_at is None else started_at
        cid = new_id()
        with self._lock:
            self._con.execute(
                "INSERT INTO conversations (id, owner, source, started_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?)",
                (cid, self.owner, source, now, now))
        return cid

    def append(self, conversation_id: str, role: str, text: str, **kwargs) -> int:
        """Add one message (see add_message). Returns its position in the
        conversation."""
        with self._lock:
            self.add_message(conversation_id, role, text, **kwargs)
            return self._con.execute("SELECT count(*) FROM messages WHERE "
                                     "conversation_id = ?",
                                     (conversation_id,)).fetchone()[0]

    def add_message(self, conversation_id: str, role: str, text: str, *,
                    kind: str = "chat", interrupted: bool = False,
                    tool_events: list[dict] | None = None) -> str:
        """Add one message (a "message" event, committed at once). Returns
        its event id."""
        if role not in ("user", "assistant"):
            raise sqlite3.IntegrityError("role must be user or assistant")
        with self._lock:
            self._own(conversation_id)
            source = self._con.execute("SELECT source FROM conversations WHERE id = ?",
                                       (conversation_id,)).fetchone()[0]
            data = {"role": role, "text": _clean(text)[:MAX_TEXT], "source": source}
            if kind != "chat":
                data["kind"] = kind
            if interrupted:
                data["interrupted"] = True
            if tool_events:
                data["tools"] = tool_events
            return self._record("message", conversation_id, data).id

    def retract(self, conversation_id: str, event_id: str) -> None:
        """Remove one message (a delete_message tombstone)."""
        with self._lock:
            self._own(conversation_id)
            self._record("delete_message", conversation_id, {"target": event_id})

    def mark_interrupted(self, conversation_id: str,
                         spoken_text: str | None = None) -> bool:
        """Flag the last assistant message as cut off (barge-in), keeping
        only what was actually said if that is known. Written as a
        delete_message tombstone for the old message (which redacts its
        unsaid text everywhere) plus a replacement at the same position."""
        with self._lock:
            self._own(conversation_id)
            row = self._con.execute(
                "SELECT event_id, ev_ts, kind, text, tool_events FROM messages "
                "WHERE conversation_id = ? AND role = 'assistant' "
                "ORDER BY ev_ts DESC, event_id DESC LIMIT 1",
                (conversation_id,)).fetchone()
            if not row:
                return False
            text = row["text"] if spoken_text is None else \
                (spoken_text.strip() or "(interrupted)")
            data = {"role": "assistant", "text": _clean(text)[:MAX_TEXT],
                    "interrupted": True}
            if row["kind"] != "chat":
                data["kind"] = row["kind"]
            if row["tool_events"]:
                data["tools"] = json.loads(row["tool_events"])
            self._record("delete_message", conversation_id,
                         {"target": row["event_id"]})
            self._record("message", conversation_id, data, ts=row["ev_ts"])
        return True

    def finish(self, conversation_id: str, title: str | None = None,
               summary: str | None = None, ended: bool = True) -> None:
        """Mark ended and set the generated title/summary (a "rename"
        event; a title Kyle typed himself is kept)."""
        with self._lock:
            self._own(conversation_id)
            row = self._con.execute(
                "SELECT title, title_by_user FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
            by_user = bool(row["title_by_user"])
            if by_user or title is None:
                title = row["title"]
            # Always the full title: a rename sets it (last writer wins).
            data = {"title": _clean(title)[:200], "by": "user" if by_user else "auto"}
            if ended:
                data["ended"] = True
            if summary is not None:
                data["summary"] = _clean(summary)[:1000]
            self._record("rename", conversation_id, data)

    def update(self, conversation_id: str, *, title: str | None = None,
               pinned: bool | None = None, archived: bool | None = None) -> dict:
        """Rename, pin/unpin, archive/unarchive. Returns the conversation."""
        if title is not None:
            title = " ".join(_clean(title).split())[:120]
            if not title:
                raise ValueError("title can't be empty")
        with self._lock:
            self._own(conversation_id)
            if title is not None:
                self._record("rename", conversation_id, {"title": title, "by": "user"})
            if pinned is not None or archived is not None:
                row = self._con.execute(
                    "SELECT pinned, archived FROM conversations WHERE id = ?",
                    (conversation_id,)).fetchone()
                self._record("pin", conversation_id, {
                    "pinned": bool(row["pinned"] if pinned is None else pinned),
                    "archived": bool(row["archived"] if archived is None else archived)})
            return self.get(conversation_id, messages=False)

    def add_memory_vector(self, conversation_id: str, vector_id: str) -> None:
        """Remember a Pinecone summary's id (local bookkeeping, not synced:
        deletes also find vectors by their conversation_id metadata)."""
        with self._lock:
            self._own(conversation_id)
            self._con.execute(
                "INSERT OR IGNORE INTO memory_vectors VALUES (?, ?)",
                (conversation_id, vector_id))

    def memory_vectors(self, conversation_id: str) -> list[str]:
        with self._lock:
            self._own(conversation_id)
            return [r[0] for r in self._con.execute(
                "SELECT vector_id FROM memory_vectors WHERE conversation_id = ?",
                (conversation_id,))]

    def delete(self, conversation_id: str) -> list[str]:
        """Delete a conversation (a "delete" tombstone, synced to the other
        backends). Returns the Pinecone vector ids of its memory summaries
        known here, for the caller to delete."""
        with self._lock:
            self._own(conversation_id)
            vectors = self.memory_vectors(conversation_id)
            if self._con.execute("SELECT 1 FROM messages WHERE conversation_id = ?",
                                 (conversation_id,)).fetchone() or \
                    self._con.execute("SELECT title_order != '' OR pin_order != '' "
                                      "FROM conversations WHERE id = ?",
                                      (conversation_id,)).fetchone()[0]:
                self._record("delete", conversation_id, {})
            else:  # never had an event: nothing to tell the other backends
                self._con.execute("DELETE FROM conversations WHERE id = ?",
                                  (conversation_id,))
            self._purge()
        return vectors

    def _purge(self) -> None:
        """After a delete: drop the deleted text from the search index's
        segments and from the write-ahead log, not just from the tables."""
        if self.fts:
            for table in ("messages_fts", "conversations_fts"):
                self._con.execute(f"INSERT INTO {table}({table}) VALUES ('optimize')")
        if self.path != ":memory:":
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _own(self, conversation_id: str) -> None:
        """KeyError unless the id exists and belongs to this owner."""
        if not valid_id(conversation_id) or not self._con.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND owner = ?",
                (conversation_id, self.owner)).fetchone():
            raise KeyError("no such conversation")

    # ---------- importing ----------
    def import_conversation(self, conv: dict) -> bool:
        """Add one conversation in the shape `export(conv, "json")` gives
        (parsed), keeping its id, timestamps, title, summary and pin, as
        the same events a live conversation writes (text masked and capped
        as add_message does). False, with nothing written, if the id is
        already here: importing again never duplicates. ValueError if it is
        malformed."""
        plan = _import_plan(conv, self.clock())
        with self._lock:
            return self._import(plan)

    def import_conversations(self, convs) -> tuple[list[str], int]:
        """import_conversation for each: (ids imported, number skipped).
        All checked first (ValueError: nothing written). With `max_mb` set,
        a batch that would leave the store over it is deleted again and
        HistoryFull raised, rather than prune deleting older conversations
        to make room."""
        now = self.clock()
        plans = [_import_plan(c, now) for c in convs]
        done: list[str] = []
        skipped = 0
        with self._lock:
            try:
                for plan in plans:
                    if self._import(plan):
                        done.append(plan["id"])
                    else:
                        skipped += 1
                if done and self.max_mb > 0 and \
                        self._used_bytes() > self.max_mb * 1024 * 1024:
                    raise HistoryFull(f"over the {self.max_mb:g} MB history limit")
            except BaseException:
                for cid in done:
                    self._undo_import(cid)
                if done:
                    self._con.execute("PRAGMA incremental_vacuum")
                raise
        return done, skipped

    def _import(self, plan: dict) -> bool:
        """Write one checked conversation. `import:<id>` in history_meta
        holds a salt while it is written and "done" afterwards: its event
        ids come from the salt, so an import cut off by a crash is finished
        by the next one without writing anything twice, and one imported
        before is skipped even after it was deleted here (deleted means
        gone: running the import again doesn't bring it back)."""
        cid, key = plan["id"], "import:" + plan["id"]
        salt = self._meta_get(key)
        if salt == "done":
            return False
        if salt is None:
            if self._con.execute("SELECT 1 FROM conversations WHERE id = ?",
                                 (cid,)).fetchone():
                return False
            salt = secrets.token_hex(8)
            self._meta_set(key, salt)
        try:
            # as create() makes it, with the original id and start
            self._con.execute(
                "INSERT OR IGNORE INTO conversations (id, owner, source, "
                "started_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (cid, self.owner, plan["source"], plan["started_at"],
                 plan["started_at"]))
            for n, (kind, data, ts) in enumerate(plan["events"]):
                eid = hashlib.sha256(f"{salt}:{cid}:{n}".encode()).hexdigest()[:32]
                self._record(kind, cid, data, ts, eid)
        except BaseException:
            self._undo_import(cid)
            raise
        if not self._con.execute("SELECT 1 FROM messages WHERE conversation_id = ?",
                                 (cid,)).fetchone():
            # an event log's tombstone redacted it: deleted here before
            self._con.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            self._meta_set(key, "done")
            return False
        self._meta_set(key, "done")
        return True

    def _undo_import(self, cid: str) -> None:
        """Delete a conversation this import wrote, and its import mark.
        With an event log (the owner's store) the delete is a tombstone,
        so that id can't be imported again; members' stores have none."""
        try:
            self.delete(cid)
        except KeyError:
            pass
        except Exception:  # noqa: BLE001 - the first error is the one to report
            log.exception("History: undoing an import failed")
        self._con.execute("DELETE FROM history_meta WHERE key = ?", ("import:" + cid,))

    # ---------- housekeeping ----------
    def prune(self) -> int:
        """Apply `retention_days` (pinned conversations are kept) and the
        `max_mb` size guard (oldest unpinned conversations go first).
        Returns how many conversations were removed."""
        removed = 0
        with self._lock:
            if self.retention_days > 0:
                cutoff = self.clock() - self.retention_days * 86400
                old = [r[0] for r in self._con.execute(
                    "SELECT id FROM conversations WHERE owner = ? AND pinned = 0 "
                    "AND updated_at < ?", (self.owner, cutoff))]
                for cid in old:
                    self._tombstone(cid)
                removed += len(old)
            if self.max_mb > 0:
                removed += self._shrink(self.max_mb * 1024 * 1024)
            if removed:
                self._purge()
                self._con.execute("PRAGMA incremental_vacuum")
                log.info("History: pruned %d old conversations", removed)
        return removed

    def _tombstone(self, cid: str) -> None:
        """Delete without the per-delete index compaction (prune does it
        once at the end)."""
        has_events = self._con.execute(
            "SELECT 1 FROM messages WHERE conversation_id = ? UNION ALL "
            "SELECT 1 FROM conversations WHERE id = ? AND "
            "(title_order != '' OR pin_order != '')", (cid, cid)).fetchone()
        if has_events:
            self._record("delete", cid, {})
        else:
            self._con.execute("DELETE FROM conversations WHERE id = ?", (cid,))

    def _shrink(self, limit: float) -> int:
        """Delete the oldest unpinned conversations until the database is
        under `limit` bytes (aiming at 90%, so it doesn't run every time).
        Deletes in proportion to the excess (as synced tombstones), then
        compacts the search index and the file."""
        removed = 0
        for _ in range(20):
            used = self._used_bytes()
            if used <= limit:
                break
            total = self._con.execute(
                "SELECT count(*) FROM conversations WHERE owner = ? AND pinned = 0",
                (self.owner,)).fetchone()[0]
            if not total:
                log.warning("History: over %s MB but only pinned conversations "
                            "are left", self.max_mb)
                break
            n = max(1, int(total * (used - 0.9 * limit) / used) + 1)
            ids = [r[0] for r in self._con.execute(
                "SELECT id FROM conversations WHERE owner = ? AND pinned = 0 "
                "ORDER BY updated_at LIMIT ?", (self.owner, n))]
            for cid in ids:
                self._tombstone(cid)
            removed += len(ids)
            self._purge()
            # Redacted events and deleted rows leave free space inside
            # pages, which only a rebuild gives back (rare: over the limit).
            self._con.execute("VACUUM")
        return removed

    def _used_bytes(self) -> int:
        page = self._con.execute("PRAGMA page_size").fetchone()[0]
        count = self._con.execute("PRAGMA page_count").fetchone()[0]
        free = self._con.execute("PRAGMA freelist_count").fetchone()[0]
        return (count - free) * page

    def unfinished(self, exclude: Iterable[str] = ()) -> list[str]:
        """Conversations never ended (e.g. the program crashed mid-way)."""
        skip = set(exclude)
        with self._lock:
            return [r[0] for r in self._con.execute(
                f"SELECT id FROM conversations c WHERE owner = ? AND {_LIVE} AND "
                f"{_HAS_MESSAGES} ORDER BY updated_at", (self.owner,))
                if r[0] not in skip]

    # ---------- reading ----------
    def get(self, conversation_id: str, messages: bool = True) -> dict:
        with self._lock:
            self._own(conversation_id)
            row = self._con.execute(
                f"SELECT {_CONV_COLS} FROM conversations c WHERE id = ?",
                (conversation_id,)).fetchone()
            conv = _conv_dict(row)
            if messages:
                conv["messages"] = [_msg_dict(r, n) for n, r in enumerate(
                    self._con.execute(
                        "SELECT event_id, role, kind, text, created_at, interrupted, "
                        "tool_events FROM messages WHERE conversation_id = ? "
                        "ORDER BY ev_ts, event_id", (conversation_id,)), 1)]
            return conv

    def list(self, *, limit: int = 20, offset: int = 0, source: str | None = None,
             pinned: bool | None = None, archived: bool | None = False,
             since: float | None = None, until: float | None = None,
             ids: list[str] | None = None) -> list[dict]:
        """Newest activity first. `archived=None` includes archived ones."""
        where, params = self._filters(source, pinned, archived, since, until, ids)
        with self._lock:
            rows = self._con.execute(
                f"SELECT {_CONV_COLS} FROM "
                f"conversations c WHERE {where} ORDER BY c.updated_at DESC, "
                f"c.rid DESC LIMIT ? OFFSET ?", (*params, _limit(limit),
                                                  max(0, int(offset)))).fetchall()
        out = []
        for r in rows:
            conv = _conv_dict(r)
            conv["snippet"] = _truncate(r["preview"] or "", 120)
            out.append(conv)
        return out

    def count(self, **filters) -> int:
        where, params = self._filters(filters.get("source"), filters.get("pinned"),
                                      filters.get("archived", False),
                                      filters.get("since"), filters.get("until"))
        with self._lock:
            return self._con.execute(
                f"SELECT count(*) FROM conversations c WHERE {where}",
                params).fetchone()[0]

    def search(self, query: str, *, limit: int = 20, offset: int = 0,
               source: str | None = None, pinned: bool | None = None,
               archived: bool | None = False, since: float | None = None,
               until: float | None = None) -> list[dict]:
        """Ranked keyword search. Each hit is a conversation (as in `list`)
        with a `snippet` whose matches are wrapped in MARK_START/MARK_END
        and a `score` (higher is better). All words must match; if nothing
        does, any word may."""
        terms = query_terms(query)
        if not terms:
            return self.list(limit=limit, offset=offset, source=source,
                             pinned=pinned, archived=archived, since=since,
                             until=until)
        filters = self._filters(source, pinned, archived, since, until)
        with self._lock:
            ranked = (self._search_fts if self.fts else self._search_like)(
                terms, filters, False)
            if not ranked and len(terms) > 1:
                ranked = (self._search_fts if self.fts else self._search_like)(
                    terms, filters, True)
            page = ranked[max(0, int(offset)): max(0, int(offset)) + _limit(limit)]
            mids = [snip for _, _, snip in page if isinstance(snip, int)]
            snippets = dict(self._con.execute(
                f"SELECT rowid, snippet(messages_fts, 0, char(2), char(3), "
                f"'{ELLIPSIS}', 12) FROM messages_fts WHERE messages_fts MATCH ? "
                f"AND rowid IN ({','.join('?' * len(mids))})",
                (_fts_query(terms, True), *mids)).fetchall()) if mids else {}
            page = [(cid, score, snippets.get(snip, "") if isinstance(snip, int)
                     else snip) for cid, score, snip in page]
            convs = {r["id"]: r for r in self._con.execute(
                f"SELECT {_CONV_COLS} FROM conversations c WHERE c.id IN "
                f"({','.join('?' * len(page))})", [cid for cid, _, _ in page])
            } if page else {}
        hits = []
        for cid, score, snippet in page:
            conv = _conv_dict(convs[cid])
            conv["snippet"] = snippet
            conv["score"] = round(score, 4)
            hits.append(conv)
        return hits

    def _search_fts(self, terms, filters, any_term):
        where, params = filters
        q = _fts_query(terms, any_term)
        scores: dict[str, list] = {}
        # Messages: the best-matching message per conversation (SQLite
        # returns the bare `mid` column from the row with the MIN score).
        # Its snippet is made later, only for the page that is returned.
        for cid, score, mid in self._con.execute(
                f"SELECT cid, min(s), mid FROM (SELECT m.conversation_id AS cid, "
                f"messages_fts.rank AS s, m.id AS mid "
                f"FROM messages_fts CROSS JOIN messages m ON m.id = messages_fts.rowid "
                f"CROSS JOIN conversations c ON c.id = m.conversation_id "
                f"WHERE messages_fts MATCH ? AND {where}) GROUP BY cid",
                (q, *params)):
            scores[cid] = [-score, mid]
        # Titles and summaries count extra (a title match is the topic).
        # CROSS JOIN keeps the FTS index first: otherwise SQLite may scan
        # every conversation and probe the index once per row.
        for cid, score, snip in self._con.execute(
                f"SELECT c.id, conversations_fts.rank, "
                f"snippet(conversations_fts, 1, char(2), char(3), '{ELLIPSIS}', 12) "
                f"FROM conversations_fts CROSS JOIN conversations c "
                f"ON c.rid = conversations_fts.rowid "
                f"WHERE conversations_fts MATCH ? AND {where}", (q, *params)):
            if cid in scores:
                scores[cid][0] += -score
            else:
                has_mark = MARK_START in (snip or "")
                scores[cid] = [-score, snip if has_mark else ""]
        return self._rank(scores)

    def _search_like(self, terms, filters, any_term):
        where, params = filters
        per_term: list[dict[str, int]] = []
        first_text: dict[str, str] = {}
        for term in terms:
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%") \
                .replace("_", "\\_") + "%"
            counts: dict[str, int] = {}
            for cid, n, text in self._con.execute(
                    f"SELECT m.conversation_id, count(*), min(m.text) FROM messages m "
                    f"JOIN conversations c ON c.id = m.conversation_id "
                    f"WHERE m.text LIKE ? ESCAPE '\\' AND {where} "
                    f"GROUP BY m.conversation_id", (pattern, *params)):
                counts[cid] = n
                first_text.setdefault(cid, text)
            for cid, title, summary in self._con.execute(
                    f"SELECT c.id, c.title, c.summary FROM conversations c "
                    f"WHERE (c.title LIKE ? ESCAPE '\\' OR c.summary LIKE ? "
                    f"ESCAPE '\\') AND {where}", (pattern, pattern, *params)):
                counts[cid] = counts.get(cid, 0) + 3
                first_text.setdefault(cid, summary or title)
            per_term.append(counts)
        ids = set.union(*map(set, per_term)) if any_term \
            else set.intersection(*map(set, per_term))
        scores = {cid: [float(sum(c.get(cid, 0) for c in per_term)),
                        _like_snippet(first_text.get(cid, ""), terms)]
                  for cid in ids}
        return self._rank(scores)

    def _rank(self, scores: dict[str, list]) -> list[tuple]:
        if not scores:
            return []
        updated = dict(self._con.execute(
            f"SELECT id, updated_at FROM conversations WHERE id IN "
            f"({','.join('?' * len(scores))})", list(scores)).fetchall())
        return sorted(((cid, s, snip) for cid, (s, snip) in scores.items()),
                      key=lambda h: (-h[1], -updated.get(h[0], 0)))

    def _filters(self, source, pinned, archived, since, until, ids=None):
        where, params = ["c.owner = ?", _HAS_MESSAGES], [self.owner]
        if ids is not None:
            ids = [i for i in ids if valid_id(i)] or [""]
            where.append(f"c.id IN ({','.join('?' * len(ids))})")
            params += ids
        if source:
            sources = [source] if isinstance(source, str) else list(source)
            where.append(f"c.source IN ({','.join('?' * len(sources))})")
            params += sources
        if pinned is not None:
            where.append("c.pinned = ?")
            params.append(int(bool(pinned)))
        if archived is not None:
            where.append("c.archived = ?")
            params.append(int(bool(archived)))
        if since is not None:
            where.append("c.updated_at >= ?")
            params.append(float(since))
        if until is not None:
            where.append("c.started_at < ?")
            params.append(float(until))
        return " AND ".join(where), params


_FIRST_USER = ("SELECT text FROM messages WHERE conversation_id = c.id AND "
               "role = 'user' AND kind = 'chat' ORDER BY ev_ts, event_id LIMIT 1")
# Still going: no "ended" rename yet, or messages after it (continued).
_LIVE = "(c.ended_at IS NULL OR c.ended_at < c.updated_at)"
# Rows for a conversation created but never written to aren't shown.
_HAS_MESSAGES = "EXISTS (SELECT 1 FROM messages WHERE conversation_id = c.id)"
_CONV_COLS = ("c.id, c.source, c.started_at, c.updated_at, c.ended_at, c.title, "
              "c.summary, c.pinned, c.archived, (SELECT count(*) FROM messages "
              f"WHERE conversation_id = c.id) AS message_count, ({_FIRST_USER}) "
              "AS preview")


def _limit(limit) -> int:
    return max(1, min(int(limit), 200))


def _conv_dict(row) -> dict:
    ended = row["ended_at"]
    return {"id": row["id"], "source": row["source"],
            "started_at": row["started_at"], "updated_at": row["updated_at"],
            "ended_at": ended if ended and ended >= row["updated_at"] else None, "title": row["title"] or "",
            "summary": row["summary"] or "", "pinned": bool(row["pinned"]),
            "archived": bool(row["archived"]),
            "message_count": row["message_count"],
            "preview": _truncate(row["preview"] or "", 200)}


def _msg_dict(row, seq: int) -> dict:
    return {"seq": seq, "id": row["event_id"], "role": row["role"], "kind": row["kind"],
            "text": row["text"], "created_at": row["created_at"],
            "interrupted": bool(row["interrupted"]),
            "tool_events": json.loads(row["tool_events"]) if row["tool_events"] else []}


def _truncate(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + ELLIPSIS


def _like_snippet(text: str, terms: list[str], width: int = 90) -> str:
    """A window of `text` around the first match, matches marked."""
    text = " ".join((text or "").split())
    low = text.lower()
    pos = min((p for p in (low.find(t) for t in terms) if p >= 0), default=0)
    start = max(0, pos - width // 3)
    piece = text[start: start + width]
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    piece = pattern.sub(lambda m: MARK_START + m.group(0) + MARK_END, piece)
    return (ELLIPSIS if start else "") + piece + \
        (ELLIPSIS if start + width < len(text) else "")


def snippet_parts(snippet: str) -> list[tuple[str, bool]]:
    """[(text, is_match), ...] for rendering highlights safely."""
    parts, pos = [], 0
    for m in re.finditer(f"{MARK_START}(.*?){MARK_END}", snippet or "", re.DOTALL):
        if m.start() > pos:
            parts.append((snippet[pos:m.start()], False))
        parts.append((m.group(1), True))
        pos = m.end()
    if pos < len(snippet or ""):
        parts.append((snippet[pos:], False))
    return [(t.replace(MARK_START, "").replace(MARK_END, ""), b) for t, b in parts]


def plain_snippet(snippet: str, start: str = "", end: str = "") -> str:
    return "".join(f"{start}{t}{end}" if m else t for t, m in snippet_parts(snippet))


# ---------- importing (HistoryStore.import_conversation) ----------
def _import_time(value, now: float) -> float:
    """An exported ISO time as unix seconds, never later than `now`."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("bad time")
    when = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return min(when.timestamp() if when.tzinfo else when.astimezone().timestamp(), now)


def _import_tools(events) -> list[dict]:
    """Tool events reduced to what `tool_event` keeps."""
    out = []
    for e in events[:20] if isinstance(events, list) else []:
        if not isinstance(e, dict) or not e.get("tool"):
            continue
        args = e.get("args") if isinstance(e.get("args"), dict) else {}
        out.append({"tool": str(e["tool"])[:64],
                    "args": {str(k)[:24]: _truncate(_clean(v), 40)
                             for k, v in list(args.items())[:4]},
                    "status": str(e.get("status") or "")[:16]})
    return out


def _import_plan(conv, now: float) -> dict:
    """A conversation from `export(..., "json")`, checked, as the events
    a live one writes: (kind, data, ts ms), in order. ValueError if it
    is malformed (the message never quotes it)."""
    if not isinstance(conv, dict) or not valid_id(conv.get("id")):
        raise ValueError("a conversation has no valid id")
    messages = conv.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("a conversation has no messages")
    source = conv.get("source") if conv.get("source") in SOURCES else "web"
    events, ts = [], 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") \
                or not isinstance(m.get("text"), str):
            raise ValueError("a message is malformed")
        # exports keep whole seconds: one ms apart keeps their order
        ts = max(int(_import_time(m.get("created_at"), now) * 1000), ts + 1)
        data = {"role": m["role"], "text": _clean(m["text"])[:MAX_TEXT],
                "source": source}
        if m.get("kind") in MESSAGE_KINDS and m["kind"] != "chat":
            data["kind"] = m["kind"]
        if m.get("interrupted") is True:
            data["interrupted"] = True
        tools = _import_tools(m.get("tool_events"))
        if tools:
            data["tools"] = tools
        events.append(("message", data, ts))
    # finish(): the title and summary, and "ended" at the original time
    title, summary = conv.get("title"), conv.get("summary")
    ended = conv.get("ended_at")
    if title or summary or ended:
        data = {"title": _clean(title if isinstance(title, str) else "")[:200],
                "by": "auto"}
        if ended:
            data["ended"] = True
            ts = max(ts, int(_import_time(ended, now) * 1000))
        if isinstance(summary, str) and summary:
            data["summary"] = _clean(summary)[:1000]
        events.append(("rename", data, ts))
    if conv.get("pinned") is True:
        events.append(("pin", {"pinned": True, "archived": False}, ts))
    started = conv.get("started_at")
    return {"id": conv["id"], "source": source, "events": events,
            "started_at": _import_time(started, now) if started else events[0][2] / 1000}


# ---------- dates ----------
def local_dt(ts: float, tz=None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz else \
        datetime.fromtimestamp(ts).astimezone()


def spoken_date(ts: float, now: float | None = None, tz=None) -> str:
    """'today', 'yesterday', 'Tuesday' (this past week), 'October 3'."""
    day = local_dt(ts, tz).date()
    today = local_dt(time.time() if now is None else now, tz).date()
    delta = (today - day).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if 1 < delta < 7:
        return local_dt(ts, tz).strftime("%A")
    d = local_dt(ts, tz)
    label = f"{d:%B} {d.day}"
    return label if d.year == local_dt(now or time.time(), tz).year else f"{label}, {d.year}"


def date_group(ts: float, now: float | None = None, tz=None) -> str:
    """'Today', 'Yesterday', 'This week' (last 7 days) or 'Older'."""
    delta = (local_dt(time.time() if now is None else now, tz).date()
             - local_dt(ts, tz).date()).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return "This week" if delta < 7 else "Older"


def parse_day(value: str | None, end: bool = False, tz=None) -> float | None:
    """'2026-10-14' -> timestamp of local midnight (the next one if `end`)."""
    if not value:
        return None
    d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    if end:
        d += timedelta(days=1)
    return (d.replace(tzinfo=tz) if tz else d.astimezone()).timestamp()


# ---------- title and summary ----------
def fallback_title(messages: list[dict]) -> tuple[str, str]:
    """Title and summary from the first thing Kyle said (no API needed)."""
    first = next((m["text"] for m in messages
                  if m["role"] == "user" and m.get("kind", "chat") == "chat"
                  and m["text"].strip()), "")
    words = re.findall(r"[\w'’-]+", first)
    title = " ".join(words[:TITLE_MAX_WORDS]) or "Conversation"
    return title[:1].upper() + title[1:], _truncate(first, 200)


def clean_title(title: str) -> str:
    title = re.sub(r"[\"“”*#]", "", str(title or "")).strip().rstrip(".!?:")
    words = title.split()
    return " ".join(words[:TITLE_MAX_WORDS])


def generate_title(client, messages: list[dict],
                   model: str = DEFAULT_TITLE_MODEL) -> tuple[str, str]:
    """One cheap call: (title of at most six words, 1-2 sentence summary).
    Falls back to the first user message if the call fails."""
    return ask_title(client, messages, model) or fallback_title(messages)


def ask_title(client, messages: list[dict],
              model: str = DEFAULT_TITLE_MODEL, meter=None) -> tuple[str, str] | None:
    """(title, summary) from the model, or None if that failed. `meter`:
    where the call's usage is counted (None = the owner's usage.METER)."""
    text = message_transcript(messages, max_chars=6000)
    if not text or client is None:
        return None
    try:
        api = client.with_options(max_retries=1, timeout=20) \
            if hasattr(client, "with_options") else client
        response = api.messages.create(
            model=model, max_tokens=200, system=TITLE_PROMPT,
            messages=[{"role": "user", "content": text}])
        from kwil import usage

        (meter or usage).record(model, getattr(response, "usage", None))
        raw = "".join(b.text for b in response.content if b.type == "text")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
        title, summary = clean_title(data.get("title", "")), \
            " ".join(str(data.get("summary", "")).split())
        if not title:
            raise ValueError("no title in reply")
        return title, _truncate(summary, 400) or fallback_title(messages)[1]
    except Exception as e:
        log.warning("History: title generation failed (%s)", e)
        return None


def message_transcript(messages: list[dict], max_chars: int = 0) -> str:
    lines = []
    for m in messages:
        who = "Kyle" if m["role"] == "user" else "Assistant"
        text = m["text"].strip()
        if text:
            lines.append(f"{who}: {text}")
    text = "\n".join(lines)
    if max_chars and len(text) > max_chars:  # keep the start and the end
        text = text[: max_chars // 2] + "\n...\n" + text[-max_chars // 2:]
    return text


# ---------- tool events ----------
def tool_event(name: str, tool_input: dict | None, status: str) -> dict:
    """A compact, privacy-light record of a tool call: its name, a few short
    argument values, and ok / error / cancelled. Results are never kept."""
    args = {}
    for key, value in list((tool_input or {}).items())[:4]:
        if isinstance(value, (dict, list)):
            value = "…"
        args[str(key)[:24]] = _truncate(str(value), 40)
    return {"tool": name, "args": args, "status": status}


def describe_events(events: list[dict]) -> str:
    """'list_calendar_events(day=tomorrow) ok; delete_calendar_event(...) cancelled'"""
    parts = []
    for e in events or []:
        args = ", ".join(f"{k}={v}" for k, v in (e.get("args") or {}).items())
        parts.append(f"{e.get('tool')}({args}) {e.get('status')}")
    return "; ".join(parts)


# ---------- continuing a conversation ----------
def rebuild_history(conv: dict, max_history: int = 40) -> list[dict]:
    """Stored messages -> ClaudeChat history (plain text, alternating roles,
    starting with a user turn). Tool calls aren't replayed: their compact
    events become text. If longer than `max_history`, older turns are
    replaced by a summary prepended as the first exchange."""
    turns: list[dict] = []

    def add(role: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + text
        else:
            turns.append({"role": role, "content": text})

    for m in conv.get("messages", []):
        text = m["text"]
        if m.get("kind") == "confirm":
            text = (f"(I asked: {text})" if m["role"] == "assistant"
                    else f"(Kyle answered: {text})")
        elif m.get("kind") == "note":
            text = f"(Saved note: {text})"
        if m["role"] == "assistant":
            if m.get("tool_events"):
                text = f"[Tools used: {describe_events(m['tool_events'])}]\n{text}"
            if m.get("interrupted"):
                text += " [Kyle interrupted this reply]"
        add(m["role"], text)
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    if turns and turns[-1]["role"] == "user":
        turns.append({"role": "assistant", "content": "(no reply)"})
    max_history = max(4, int(max_history))
    if len(turns) <= max_history:
        return turns
    keep = turns[-(max_history - 2):]
    while keep and keep[0]["role"] != "user":
        keep.pop(0)
    older = turns[: len(turns) - len(keep)]
    return [{"role": "user", "content": older_summary(conv, older)},
            {"role": "assistant", "content": "Got it, I remember that."}] + keep


def older_summary(conv: dict, older: list[dict]) -> str:
    """Text standing in for turns trimmed off a long conversation: the
    stored summary plus what Kyle asked earlier (no API call needed)."""
    asked = [_truncate(t["content"], 100) for t in older if t["role"] == "user"]
    lines = ["(Continuing an earlier conversation"
             + (f" titled \"{conv['title']}\"" if conv.get("title") else "") + ".)"]
    if conv.get("summary"):
        lines.append(f"Summary so far: {conv['summary']}")
    if asked:
        lines.append("Earlier, Kyle had asked: " + " | ".join(asked[-8:]))
    return "\n".join(lines)


# ---------- recording ----------
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _background() -> ThreadPoolExecutor:
    """One shared worker thread for titles/summaries (off the hot path)."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="history")
        return _EXECUTOR


class Recorder:
    """Writes one ClaudeChat's conversation into a HistoryStore.

    The conversation row is created at the first turn. With `incognito`
    nothing is written. Every method swallows and logs storage errors:
    history must never break a reply."""

    def __init__(self, store: HistoryStore | None, source: str, *,
                 incognito: bool = False, client_fn: Callable | None = None,
                 title_model: str = DEFAULT_TITLE_MODEL,
                 run_async: bool = True, meter=None):
        self.store = store
        self.source = source
        self.incognito = incognito
        self.client_fn = client_fn  # returns the Anthropic client for titles
        self.meter = meter  # where title calls are counted (ask_title)
        self.title_model = title_model
        self.run_async = run_async
        self.conversation_id: str | None = None
        self._futures: list[Future] = []

    @property
    def active(self) -> bool:
        return self.store is not None and not self.incognito

    def _safe(self, fn, *args, **kwargs):
        if not self.active:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("History: could not save")
            return None

    def _ensure(self) -> str:
        if self.conversation_id is None:
            self.conversation_id = self.store.create(self.source)
        return self.conversation_id

    def _append(self, role: str, text: str, **kwargs) -> str | None:
        def write():
            try:
                return self.store.add_message(self._ensure(), role, text, **kwargs)
            except KeyError:  # deleted meanwhile (e.g. from the phone)
                self.conversation_id = None
                return self.store.add_message(self._ensure(), role, text, **kwargs)
        return self._safe(write)

    def user(self, text: str, kind: str = "chat") -> str | None:
        """Save what Kyle said; returns the message's event id."""
        return self._append("user", text, kind=kind)

    def assistant(self, text: str, tool_events: list[dict] | None = None,
                  kind: str = "chat", interrupted: bool = False) -> str | None:
        return self._append("assistant", text or "(no reply)", kind=kind,
                            tool_events=tool_events, interrupted=interrupted)

    def retract(self, event_id: str | None, whole: bool = False) -> None:
        """Take back a message saved for a turn that then failed (the user
        will retry). `whole`: the conversation was only that message."""
        if whole:
            self.discard()
        elif event_id and self.conversation_id:
            self._safe(self.store.retract, self.conversation_id, event_id)

    def confirm(self, question: str, answer: str) -> None:
        self.assistant(question, kind="confirm")
        self.user(answer, kind="confirm")

    def mark_interrupted(self, spoken_text: str | None = None) -> None:
        if self.conversation_id:
            self._safe(self.store.mark_interrupted, self.conversation_id, spoken_text)

    def add_memory_vector(self, conversation_id: str | None, vector_id: str) -> None:
        if conversation_id:
            self._safe(self.store.add_memory_vector, conversation_id, vector_id)

    def message_count(self) -> int:
        if not self.conversation_id or not self.active:
            return 0
        conv = self._safe(self.store.get, self.conversation_id, False)
        return conv["message_count"] if conv else 0

    def discard(self) -> None:
        """Forget the current conversation entirely (deleted from the store)."""
        if self.conversation_id:
            self._safe(self.store.delete, self.conversation_id)
        self.conversation_id = None

    def resume(self, conversation_id: str) -> dict:
        """Point at a stored conversation; new turns are added to it."""
        conv = self.store.get(conversation_id)
        self.conversation_id = conversation_id
        return conv

    def finish(self) -> str | None:
        """End the current conversation: title + summary in the background.
        Returns its id (None if nothing was recorded)."""
        cid, self.conversation_id = self.conversation_id, None
        if not cid or not self.active:
            return None
        self.schedule_title(cid)
        return cid

    def schedule_title(self, cid: str) -> None:
        if self.run_async:
            self._futures = [f for f in self._futures if not f.done()]
            self._futures.append(_background().submit(self._title, cid))
        else:
            self._title(cid)

    def _title(self, cid: str) -> None:
        try:
            conv = self.store.get(cid)
            if not conv["messages"]:
                self.store.delete(cid)
                return
            client = self.client_fn() if self.client_fn else None
            asked = ask_title(client, conv["messages"], self.title_model,
                              meter=self.meter)
            # Resumed again before this ran: titled, but not marked ended.
            ended = cid != self.conversation_id
            if conv["title"]:
                # A continued conversation keeps the title it is known by;
                # only the summary is brought up to date.
                self.store.finish(cid, None, asked[1] if asked else None, ended)
            else:
                title, summary = asked or fallback_title(conv["messages"])
                self.store.finish(cid, title, summary, ended)
            log.info("History: saved %s", cid)
        except KeyError:
            pass  # deleted meanwhile
        except Exception:
            log.exception("History: could not title %s", cid)

    def finish_orphans(self) -> None:
        """Title conversations left unfinished by a crash (background)."""
        if not self.active:
            return
        for cid in self._safe(self.store.unfinished,
                              [self.conversation_id or ""]) or []:
            self.schedule_title(cid)

    def flush(self, timeout: float = 30) -> None:
        """Wait for pending titles (call before the program exits)."""
        deadline = time.monotonic() + timeout
        for f in list(self._futures):
            try:
                f.result(max(0.0, deadline - time.monotonic()))
            except Exception:
                log.warning("History: a title was still pending at exit")
        self._futures = []


# ---------- for other features (lecture recorder, quick capture) ----------
_STORE: HistoryStore | None = None  # the store open_store() opened last


def current_conversation_id() -> str | None:
    """The conversation of the turn running now (inside a tool call), or
    None (incognito, history off, or called outside a turn)."""
    from kwil.chat import current_chat

    chat = current_chat()
    return chat.conversation_id if chat is not None else None


def record_event(conversation_id: str | None, kind: str, text: str) -> bool:
    """Add a note to a saved conversation, e.g. a lecture summary or a
    quick-capture note: shown in its transcript and search, and passed to
    Claude when it's continued. `kind` names the feature (stored with the
    note); `conversation_id` None = the current one. False if there is
    nowhere to put it (incognito, history off, unknown id)."""
    store = _STORE
    cid = conversation_id or current_conversation_id()
    if store is None or not cid or not text.strip():
        return False
    try:
        store.add_message(cid, "assistant", f"[{kind}] {text.strip()}", kind="note")
        return True
    except KeyError:
        return False


def origin_of(config: dict) -> str:
    """This backend's role for events: [failover] role / ASSISTANT_ROLE,
    or "pc" when it runs on its own."""
    role = (config.get("failover", {}).get("role")
            or os.environ.get("ASSISTANT_ROLE") or "pc")
    return role if role in ("pi", "pc", "cloud") else "pc"


def open_store(config: dict) -> HistoryStore | None:
    """The configured store, or None if history is off or can't be opened."""
    h = config.get("history", {})
    if not h.get("enabled", True):
        return None
    path = h.get("path") or "data/history.sqlite"  # HISTORY_PATH via config
    global _STORE
    try:
        _STORE = HistoryStore(path, retention_days=h.get("retention_days", 0),
                              max_mb=h.get("max_mb", 0), origin=origin_of(config))
        return _STORE
    except Exception as e:
        log.exception("History: could not open %s", path)
        print(f"(History off: could not open {path}: {e})")
        return None


# ---------- search across keyword + semantic, delete with memory ----------
def search_conversations(store: HistoryStore, query: str, memory=None, *,
                         limit: int = 20, offset: int = 0,
                         **filters) -> list[dict]:
    """Keyword hits (FTS, ranked) first, then semantic hits from Pinecone
    conversation summaries that keyword search missed. Each hit has
    `match` = "keyword" or "semantic"."""
    hits = store.search(query, limit=limit, offset=offset, **filters)
    for h in hits:
        h["match"] = "keyword"
    if memory is None or offset or not query_terms(query) or len(hits) >= limit:
        return hits
    try:
        semantic = memory.search_conversations(query, top_k=limit)
    except Exception as e:  # Pinecone is a bonus: keyword results still count
        log.warning("History: semantic search failed: %s", e)
        return hits
    seen = {h["id"] for h in hits}
    wanted = [s for s in semantic if s["conversation_id"] not in seen]
    if not wanted:
        return hits
    found = {c["id"]: c for c in store.list(
        limit=200, ids=[s["conversation_id"] for s in wanted],
        source=filters.get("source"), pinned=filters.get("pinned"),
        archived=filters.get("archived", False), since=filters.get("since"),
        until=filters.get("until"))}
    for s in wanted:
        conv = found.get(s["conversation_id"])
        if conv and len(hits) < limit:
            conv.update(match="semantic", score=round(s["score"], 4),
                        snippet=_truncate(s["text"], 140))
            hits.append(conv)
    return hits


def delete_conversation(store: HistoryStore, conversation_id: str,
                        memory=None) -> None:
    """Delete a conversation and its Pinecone summary vector(s). Pinecone
    goes first: if that fails, nothing is deleted and the error is raised,
    so a retry can't leave an orphaned summary behind."""
    vectors = store.memory_vectors(conversation_id)  # KeyError if not ours
    if memory is not None:
        # Also summaries written by another backend (found by metadata).
        vectors = sorted(set(vectors) | set(memory.vectors_for(conversation_id)))
        if vectors:
            memory.delete(vectors)
    elif vectors:
        log.warning("History: memory is off; %d summary vector(s) of %s were "
                    "left in Pinecone", len(vectors), conversation_id)
    store.delete(conversation_id)


# ---------- export ----------
def export(conv: dict, fmt: str = "md") -> str:
    """A conversation as Markdown or JSON. Only what was said, the title,
    summary and dates; no memory ids, owner, or secrets (masked again)."""
    from kwil.config import redact

    messages = [{"role": m["role"], "text": redact(m["text"]), "kind": m["kind"],
                 "created_at": _iso(m["created_at"]),
                 "interrupted": m["interrupted"],
                 "tool_events": m["tool_events"]} for m in conv["messages"]]
    if fmt == "json":
        return json.dumps({
            "id": conv["id"], "title": redact(conv["title"]),
            "summary": redact(conv["summary"]), "source": conv["source"],
            "started_at": _iso(conv["started_at"]),
            "ended_at": _iso(conv["ended_at"]) if conv["ended_at"] else None,
            "pinned": conv["pinned"], "messages": messages},
            indent=2, ensure_ascii=False)
    if fmt != "md":
        raise ValueError("format must be md or json")
    lines = [f"# {redact(conv['title']) or 'Conversation'}", "",
             f"*{SOURCE_LABELS.get(conv['source'], conv['source'])}, "
             f"{local_dt(conv['started_at']):%A, %B %d, %Y %I:%M %p}*", ""]
    if conv["summary"]:
        lines += [f"> {redact(conv['summary'])}", ""]
    for m in messages:
        who = "Kyle" if m["role"] == "user" else "Kwil"
        text = m["text"]
        if m["kind"] == "confirm":
            text = f"_{text}_"
        if m["interrupted"]:
            text += " _(interrupted)_"
        lines.append(f"**{who}:** {text}")
        if m["tool_events"]:
            lines.append(f"<sub>tools: {describe_events(m['tool_events'])}</sub>")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _iso(ts: float) -> str:
    return local_dt(ts).isoformat(timespec="seconds")
