"""Kwil's own memory, kept on this machine (no Pinecone needed).

Two things per person, both shown in Settings -> Memory (memory_web.py):

- **Your instructions**: one text the person writes in Settings ("always
  color-code my schedule"), up to MAX_INSTRUCTIONS characters. Typed by
  the person, so trusted.
- **Things Kwil remembered**: short memories (MAX_MEMORY_CHARS each, at
  most MAX_MEMORIES) saved with the `remember` tool when the person says
  "remember...", "always..." or "from now on...". Each keeps when it was
  saved, the conversation it came from, and an `untrusted` flag: saved
  while a web page, email or other outside text was in the conversation,
  so it is shown to the model as data, never as an instruction.

Both are read once when a conversation starts (`snapshot`) and put in the
system prompt (ClaudeChat.stable_system), so the cached prompt stays the
same bytes for the whole conversation; edits apply from the next one.
"Pause memory" keeps everything but saves nothing new and loads no
memories (the instructions still apply: they are settings, not memories).

- **Study: things to review** (study mode, study.py): concepts the person
  got wrong while being quizzed (course, concept of at most
  MAX_CONCEPT_CHARS, how often, when last), saved with `note_missed` and
  used by "review what I missed". At most MAX_MISSED: past that the one
  seen longest ago is deleted (they're noted by Kwil, not written by the
  person). Kept in the sibling table `missed`, made with CREATE TABLE IF
  NOT EXISTS outside the numbered migrations, so an older Kwil (a
  rollback) still opens the file and simply ignores it.

One SQLite file per person: the owner's at [memory] local_path
(`data/memory.sqlite`; `/data/memory.sqlite` on the server), a family
member's at `<family dir>/<id>/memory.sqlite` (family.py), so one person's
routes and tools can never name another's rows.

`MemoryStore` is the interface the chat, the tools, the Settings routes
and account export use; `SqliteMemoryStore` is the only backend so far.
A hosted Kwil for many people can add another (e.g. Postgres, rows keyed
by person) with the same methods, without changing any caller.

Deleted means gone: `secure_delete` is on (freed bytes are overwritten
with zeros) and every delete, edit and "Forget all" ends with
`wal_checkpoint(TRUNCATE)`, so the old text is in neither the database
file nor its write-ahead log. Each change is one transaction (all or
nothing), so the bounds hold with several threads writing. Logs carry ids
and counts only, never the text.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MAX_INSTRUCTIONS = 4000   # characters of "Your instructions"
MAX_MEMORY_CHARS = 500    # characters of one memory
MAX_MEMORIES = 200        # memories per person
MAX_SOURCE_CHARS = 64     # a conversation id
ID_PREFIX = "mem-"
ID_RE = re.compile(r"^mem-[0-9a-f]{12}$")
MAX_CONCEPT_CHARS = 120   # one missed concept (study mode)
MAX_COURSE_CHARS = 80     # its course, as the person named it
MAX_MISSED = 300          # missed concepts per person
MISS_PREFIX = "miss-"
MISS_RE = re.compile(r"^miss-[0-9a-f]{12}$")

# Study mode's list (see the module doc): not a numbered migration, so the
# schema version (and an older Kwil's check of it) stays as it was.
STUDY_TABLE = """
CREATE TABLE IF NOT EXISTS missed (
    rid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    course TEXT NOT NULL,
    concept TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    untrusted INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA_VERSION = 1
MIGRATIONS: list[str] = [
    """
    CREATE TABLE instructions (
        one INTEGER PRIMARY KEY CHECK (one = 1),
        text TEXT NOT NULL,
        updated REAL NOT NULL
    );
    CREATE TABLE memories (
        rid INTEGER PRIMARY KEY,
        id TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL,
        created REAL NOT NULL,
        source TEXT,
        untrusted INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


class MemoryFull(Exception):
    """MAX_MEMORIES are saved already: nothing old is dropped unseen."""


class MemoryPaused(Exception):
    """"Pause memory" is on: nothing new is saved."""


def valid_id(memory_id) -> bool:
    return isinstance(memory_id, str) and bool(ID_RE.match(memory_id))


def clean_memory(text) -> str:
    """One line (a memory is shown as one "- " line in the prompt), or
    ValueError when empty or longer than MAX_MEMORY_CHARS."""
    if not isinstance(text, str):
        raise ValueError("a memory is text")
    text = " ".join(text.split())
    if not text:
        raise ValueError("nothing to remember")
    if len(text) > MAX_MEMORY_CHARS:
        raise ValueError(f"a memory is at most {MAX_MEMORY_CHARS} characters")
    return text


def valid_missed_id(missed_id) -> bool:
    return isinstance(missed_id, str) and bool(MISS_RE.match(missed_id))


def _one_line(text, what: str, limit: int, required: bool = True) -> str:
    if not isinstance(text, str):
        raise ValueError(f"a {what} is text")
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    if required and not text:
        raise ValueError(f"no {what} given")
    if len(text) > limit:
        raise ValueError(f"a {what} is at most {limit} characters: say it in a few words")
    return text


def clean_missed(course, concept) -> tuple[str, str]:
    """(course, concept) on one line each, or ValueError (an empty concept,
    or one longer than MAX_CONCEPT_CHARS)."""
    return (_one_line(course or "", "course", MAX_COURSE_CHARS, required=False),
            _one_line(concept, "concept", MAX_CONCEPT_CHARS))


def clean_instructions(text) -> str:
    """Trimmed, line breaks kept, or ValueError when too long."""
    if not isinstance(text, str):
        raise ValueError("instructions are text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"instructions are at most {MAX_INSTRUCTIONS} characters")
    return text


def _iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


@dataclass(frozen=True)
class Snapshot:
    """What one conversation is given: read once at its start."""

    instructions: str = ""
    memories: tuple = ()   # ({"id", "text", "untrusted"}, ...), oldest first
    paused: bool = False

    @property
    def untrusted(self) -> bool:
        """A memory saved from outside text is in it: changes ask first."""
        return any(m["untrusted"] for m in self.memories)

    def without(self, memory_ids) -> "Snapshot":
        gone = set(memory_ids)
        return Snapshot(self.instructions,
                        tuple(m for m in self.memories if m["id"] not in gone),
                        self.paused)


EMPTY = Snapshot()


class MemoryStore:
    """The interface (see the module doc). Memories are dicts:
    {"id", "text", "created" (ISO, UTC), "source", "untrusted"}.
    `deletions` counts deletes (and cleared instructions) in this process,
    so an open conversation can drop what was deleted elsewhere
    (ClaudeChat._drop_deleted_memories)."""

    deletions = 0

    def instructions(self) -> str:
        raise NotImplementedError

    def set_instructions(self, text: str) -> str:
        """Save (trimmed) and return them; ValueError when too long."""
        raise NotImplementedError

    def add(self, text: str, *, source: str | None = None,
            untrusted: bool = False) -> dict:
        """Save one memory; ValueError, MemoryFull or MemoryPaused."""
        raise NotImplementedError

    def get(self, memory_id: str) -> dict | None:
        raise NotImplementedError

    def update(self, memory_id: str, text: str, *,
               untrusted: bool | None = None) -> dict | None:
        raise NotImplementedError

    def delete(self, memory_id: str) -> bool:
        raise NotImplementedError

    def trust(self, memory_id: str) -> dict | None:
        """The person said "Keep as mine" (Settings): no longer untrusted."""
        raise NotImplementedError

    def list(self) -> list[dict]:
        """Every memory, newest first."""
        raise NotImplementedError

    @property
    def paused(self) -> bool:
        raise NotImplementedError

    def set_paused(self, paused: bool) -> None:
        raise NotImplementedError

    def forget_all(self) -> int:
        raise NotImplementedError

    # study mode's "things to review" (dicts: {"id", "course", "concept",
    # "count", "last_seen" (ISO, UTC), "untrusted"})
    def note_missed(self, course: str, concept: str, *,
                    untrusted: bool = False) -> dict:
        """Count one miss (the same course and concept again adds one);
        ValueError or MemoryPaused."""
        raise NotImplementedError

    def missed(self, course: str | None = None) -> list[dict]:
        """Most missed first (then most recent); `course`: only those whose
        course contains it (any case)."""
        raise NotImplementedError

    def delete_missed(self, missed_id: str) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> Snapshot:
        """Instructions and (unless paused) memories, oldest first."""
        paused = self.paused
        memories = () if paused else tuple(
            {"id": m["id"], "text": m["text"], "untrusted": m["untrusted"]}
            for m in reversed(self.list()))
        return Snapshot(self.instructions(), memories, paused)

    def export(self) -> dict:
        """Everything kept, for a download (Settings, account export)."""
        return {"instructions": self.instructions(), "memories": self.list(),
                "paused": self.paused, "missed": self.missed()}


class SqliteMemoryStore(MemoryStore):
    """One person's memory in one SQLite file. Thread-safe: one connection
    under a lock."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            p = Path(self.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():  # private from the start (0600 does nothing on Windows)
                os.close(os.open(p, os.O_CREAT | os.O_WRONLY, 0o600))
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.path, check_same_thread=False,
                                    isolation_level=None, timeout=10)
        self._con.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._con.execute("PRAGMA journal_mode = WAL")
            self._con.execute("PRAGMA synchronous = FULL")
        # Deleted rows are overwritten with zeros, not just unlinked.
        self._con.execute("PRAGMA secure_delete = ON")
        self._migrate()

    # ---------- setup ----------
    def _migrate(self) -> None:
        with self._lock:
            version = self._con.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError(f"{self.path} is from a newer version of Kwil "
                                   f"(schema {version})")
            for n in range(version, len(MIGRATIONS)):
                self._con.executescript("BEGIN IMMEDIATE;\n" + MIGRATIONS[n]
                                        + f"\nPRAGMA user_version = {n + 1};\nCOMMIT;")
            self._con.executescript("BEGIN IMMEDIATE;\n" + STUDY_TABLE + "\nCOMMIT;")

    def _write(self, fn):
        """Run fn(connection) in one IMMEDIATE transaction."""
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                result = fn(self._con)
                self._con.execute("COMMIT")
            except BaseException:
                if self._con.in_transaction:
                    self._con.execute("ROLLBACK")
                raise
            return result

    def _purge(self) -> None:
        """After a delete or an edit: the old text out of the write-ahead
        log too (secure_delete already zeroed it in the pages)."""
        if self.path != ":memory:":
            with self._lock:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ---------- instructions ----------
    def instructions(self) -> str:
        with self._lock:
            row = self._con.execute("SELECT text FROM instructions").fetchone()
        return row["text"] if row else ""

    def set_instructions(self, text: str) -> str:
        text = clean_instructions(text)

        def write(db):
            if text:
                db.execute("INSERT INTO instructions (one, text, updated) VALUES (1, ?, ?) "
                           "ON CONFLICT(one) DO UPDATE SET text = excluded.text, "
                           "updated = excluded.updated", (text, self.clock()))
            else:
                db.execute("DELETE FROM instructions")
        self._write(write)
        self._purge()  # the old text: gone
        if not text:
            self.deletions += 1
        log.info("Memory: instructions saved (%d chars)", len(text))
        return text

    # ---------- memories ----------
    @staticmethod
    def _row(r) -> dict:
        return {"id": r["id"], "text": r["text"], "created": _iso(r["created"]),
                "source": r["source"], "untrusted": bool(r["untrusted"])}

    def add(self, text: str, *, source: str | None = None,
            untrusted: bool = False) -> dict:
        text = clean_memory(text)
        source = str(source)[:MAX_SOURCE_CHARS] if source else None
        memory_id = ID_PREFIX + secrets.token_hex(6)

        def write(db):
            if self._paused(db):
                raise MemoryPaused()
            count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if count >= MAX_MEMORIES:
                raise MemoryFull()
            db.execute("INSERT INTO memories (id, text, created, source, untrusted) "
                       "VALUES (?, ?, ?, ?, ?)",
                       (memory_id, text, self.clock(), source, int(bool(untrusted))))
            return self._row(db.execute("SELECT * FROM memories WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        log.info("Memory: saved %s (%d chars%s)", memory_id, len(text),
                 ", untrusted" if untrusted else "")
        return saved

    def get(self, memory_id: str) -> dict | None:
        if not valid_id(memory_id):
            return None
        with self._lock:
            row = self._con.execute("SELECT * FROM memories WHERE id = ?",
                                    (memory_id,)).fetchone()
        return self._row(row) if row else None

    def update(self, memory_id: str, text: str, *,
               untrusted: bool | None = None) -> dict | None:
        """New text for one memory (None if there is no such id). `untrusted`
        None keeps its flag."""
        text = clean_memory(text)
        if not valid_id(memory_id):
            return None

        def write(db):
            row = db.execute("SELECT untrusted FROM memories WHERE id = ?",
                             (memory_id,)).fetchone()
            if row is None:
                return None
            flag = row["untrusted"] if untrusted is None else int(bool(untrusted))
            db.execute("UPDATE memories SET text = ?, untrusted = ? WHERE id = ?",
                       (text, flag, memory_id))
            return self._row(db.execute("SELECT * FROM memories WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        if saved is not None:
            self._purge()
            log.info("Memory: edited %s (%d chars)", memory_id, len(text))
        return saved

    def delete(self, memory_id: str) -> bool:
        if not valid_id(memory_id):
            return False
        found = self._write(lambda db: db.execute(
            "DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0)
        if found:
            self._purge()
            self.deletions += 1
            log.info("Memory: deleted %s", memory_id)
        return found

    def trust(self, memory_id: str) -> dict | None:
        if not valid_id(memory_id):
            return None

        def write(db):
            if db.execute("UPDATE memories SET untrusted = 0 WHERE id = ?",
                          (memory_id,)).rowcount == 0:
                return None
            return self._row(db.execute("SELECT * FROM memories WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        if saved is not None:
            log.info("Memory: %s kept as the person's own (trusted)", memory_id)
        return saved

    def list(self) -> list[dict]:
        """Every memory, newest first."""
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM memories ORDER BY created DESC, rid DESC").fetchall()
        return [self._row(r) for r in rows]

    # ---------- pause, forget all ----------
    @staticmethod
    def _paused(db) -> bool:
        row = db.execute("SELECT value FROM settings WHERE key = 'paused'").fetchone()
        return bool(row and row["value"] == "1")

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused(self._con)

    def set_paused(self, paused: bool) -> None:
        self._write(lambda db: db.execute(
            "INSERT INTO settings (key, value) VALUES ('paused', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("1" if paused else "0",)))
        log.info("Memory: %s", "paused" if paused else "resumed")

    def forget_all(self) -> int:
        """Delete every memory and the instructions (the pause setting
        stays). Returns how many memories went."""
        def write(db):
            n = db.execute("DELETE FROM memories").rowcount
            db.execute("DELETE FROM instructions")
            db.execute("DELETE FROM missed")  # the study list too
            return n
        count = self._write(write)
        self._purge()
        self.deletions += 1
        log.info("Memory: forgot everything (%d memories)", count)
        return count

    # ---------- study mode: things to review ----------
    @staticmethod
    def _missed_row(r) -> dict:
        return {"id": r["id"], "course": r["course"], "concept": r["concept"],
                "count": r["count"], "last_seen": _iso(r["last_seen"]),
                "untrusted": bool(r["untrusted"])}

    def note_missed(self, course: str, concept: str, *,
                    untrusted: bool = False) -> dict:
        course, concept = clean_missed(course, concept)
        key = course.casefold() + "\n" + concept.casefold()
        now = self.clock()

        def write(db):
            if self._paused(db):
                raise MemoryPaused()
            row = db.execute("SELECT id FROM missed WHERE key = ?", (key,)).fetchone()
            evicted = 0
            if row is not None:
                missed_id = row["id"]
                db.execute("UPDATE missed SET count = count + 1, last_seen = ?, "
                           "untrusted = MAX(untrusted, ?) WHERE id = ?",
                           (now, int(bool(untrusted)), missed_id))
            else:
                total = db.execute("SELECT COUNT(*) FROM missed").fetchone()[0]
                if total >= MAX_MISSED:  # the one seen longest ago makes room
                    evicted = db.execute(
                        "DELETE FROM missed WHERE rid IN (SELECT rid FROM missed "
                        "ORDER BY last_seen ASC, rid ASC LIMIT ?)",
                        (total - MAX_MISSED + 1,)).rowcount
                missed_id = MISS_PREFIX + secrets.token_hex(6)
                db.execute("INSERT INTO missed (id, course, concept, key, count, "
                           "first_seen, last_seen, untrusted) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                           (missed_id, course, concept, key, now, now,
                            int(bool(untrusted))))
            saved = self._missed_row(db.execute("SELECT * FROM missed WHERE id = ?",
                                                (missed_id,)).fetchone())
            return saved, evicted
        saved, evicted = self._write(write)
        if evicted:
            self._purge()  # the dropped one's text: gone
        log.info("Study: noted %s (count %d, %d chars%s)%s", saved["id"], saved["count"],
                 len(concept), ", untrusted" if untrusted else "",
                 f"; {evicted} oldest dropped" if evicted else "")
        return saved

    def missed(self, course: str | None = None) -> list[dict]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM missed ORDER BY count DESC, last_seen DESC, rid DESC"
            ).fetchall()
        out = [self._missed_row(r) for r in rows]
        want = " ".join(str(course or "").split()).casefold()
        if want:
            out = [m for m in out if want in m["course"].casefold()]
        return out

    def delete_missed(self, missed_id: str) -> bool:
        if not valid_missed_id(missed_id):
            return False
        found = self._write(lambda db: db.execute(
            "DELETE FROM missed WHERE id = ?", (missed_id,)).rowcount > 0)
        if found:
            self._purge()
            log.info("Study: deleted %s", missed_id)
        return found


def open_store(config: dict) -> SqliteMemoryStore | None:
    """The owner's store ([memory] local_path), or None when memory is off
    or the file can't be opened (Kwil still answers, without memory)."""
    mem = config.get("memory", {})
    if not mem.get("enabled", True):
        return None
    path = mem.get("local_path") or "data/memory.sqlite"
    try:
        return SqliteMemoryStore(path)
    except Exception as e:
        log.error("Memory: could not open %s (%s)", path, type(e).__name__)
        return None
