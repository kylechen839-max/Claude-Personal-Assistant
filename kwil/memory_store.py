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

- **Projects** (projects.py; one per class, like ChatGPT and Claude
  Projects): a name, the person's own instructions for it (trusted, as
  "Your instructions"), its own memories and its own files, in the
  sibling tables `projects`, `project_memories` and `project_files`
  (also outside the numbered migrations). A project memory is loaded only
  into that project's conversations (`snapshot(project=...)`), never
  anywhere else, and an older Kwil never sees it as a global memory
  (it is in its own table). Memory ids share one space ("mem-..."), so
  update, trust and delete work on either kind. A file's bytes are kept
  here as a BLOB (at most the [projects] bounds the caller passes), with
  its extracted text for search_project_files; secure_delete zeroes both
  when it goes. Deleting a project deletes its memories and files in the
  same transaction.

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
from dataclasses import dataclass, replace
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
MAX_PROJECTS = 30            # projects per person
MAX_PROJECT_NAME = 80        # characters of a project's name
MAX_PROJECT_MEMORIES = 100   # memories per project
MAX_COURSE_KEY = 40          # the course a suggested project came from ("phys361")
MAX_FILE_NAME = 80           # attachments.MAX_NAME_CHARS
MAX_FILE_TEXT = 1_000_000    # characters of a file's extracted text kept
PROJECT_PREFIX = "prj-"
PROJECT_RE = re.compile(r"^prj-[0-9a-f]{12}$")
FILE_PREFIX = "pfile-"
FILE_RE = re.compile(r"^pfile-[0-9a-f]{12}$")
FILE_KINDS = ("pdf", "text")

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

# Projects (see the module doc): not numbered migrations either. Rows
# that belong to a project are deleted with it explicitly (no foreign
# keys: older files never turned them on).
PROJECT_TABLES = """
CREATE TABLE IF NOT EXISTS projects (
    rid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    course TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_memories (
    rid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    project TEXT NOT NULL,
    text TEXT NOT NULL,
    created REAL NOT NULL,
    source TEXT,
    untrusted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS project_memories_project ON project_memories(project);
CREATE TABLE IF NOT EXISTS project_files (
    rid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    project TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    pages INTEGER,
    data BLOB NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS project_files_project ON project_files(project);
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


class ProjectsFull(Exception):
    """MAX_PROJECTS projects exist already."""


class NoSuchProject(KeyError):
    """The project is gone (deleted meanwhile) or was never this person's."""


class FileRefused(ValueError):
    """A project file over a bound (count, size, the person's total): the
    message says which, for the page."""


def valid_project_id(project_id) -> bool:
    return isinstance(project_id, str) and bool(PROJECT_RE.match(project_id))


def valid_file_id(file_id) -> bool:
    return isinstance(file_id, str) and bool(FILE_RE.match(file_id))


def clean_project_name(name) -> str:
    """One line, at most MAX_PROJECT_NAME characters, or ValueError."""
    return _one_line(name, "project name", MAX_PROJECT_NAME)


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
    # The conversation's project (projects.py), or None: {"id", "name",
    # "instructions"}; its memories (as `memories`) and its files'
    # metadata ({"id", "name", "kind", "size", "pages", "chars"}, oldest
    # first; the bytes are read by the chat, ClaudeChat._load_project_docs)
    project: dict | None = None
    project_memories: tuple = ()
    project_files: tuple = ()

    @property
    def untrusted(self) -> bool:
        """A memory saved from outside text is in it: changes ask first."""
        return any(m["untrusted"] for m in self.memories + self.project_memories)

    def without(self, memory_ids) -> "Snapshot":
        gone = set(memory_ids)
        return replace(self,
                       memories=tuple(m for m in self.memories if m["id"] not in gone),
                       project_memories=tuple(m for m in self.project_memories
                                              if m["id"] not in gone))

    def without_files(self, file_ids) -> "Snapshot":
        gone = set(file_ids)
        return replace(self, project_files=tuple(f for f in self.project_files
                                                 if f["id"] not in gone))

    def without_project(self) -> "Snapshot":
        """The project was deleted: none of it is sent again."""
        return replace(self, project=None, project_memories=(), project_files=())


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
            untrusted: bool = False, project: str | None = None) -> dict:
        """Save one memory (`project`: that project's only); ValueError,
        MemoryFull, MemoryPaused or NoSuchProject."""
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

    def list(self, project: str | None = None) -> list[dict]:
        """Every global memory, newest first (`project`: that project's
        memories instead)."""
        raise NotImplementedError

    # projects (dicts: {"id", "name", "instructions", "course", "created",
    # "updated" (ISO, UTC), "memories", "files"})
    def projects(self) -> list[dict]:
        raise NotImplementedError

    def project(self, project_id: str) -> dict | None:
        raise NotImplementedError

    def create_project(self, name: str, instructions: str = "",
                       course: str = "") -> dict:
        """ValueError or ProjectsFull. `course` (a suggestion's course key):
        an existing project made from it is returned instead (one tap twice
        makes one project)."""
        raise NotImplementedError

    def update_project(self, project_id: str, *, name: str | None = None,
                       instructions: str | None = None) -> dict | None:
        raise NotImplementedError

    def delete_project(self, project_id: str) -> bool:
        """The project, its memories and its files: gone."""
        raise NotImplementedError

    def add_file(self, project_id: str, name: str, kind: str, data: bytes, *,
                 text: str = "", pages: int | None = None, max_files: int,
                 max_bytes: int, max_total_bytes: int) -> dict:
        """FileRefused over a bound, NoSuchProject."""
        raise NotImplementedError

    def files(self, project_id: str) -> list[dict]:
        """Metadata only, oldest first ({"id", "name", "kind", "size",
        "pages", "chars", "created"})."""
        raise NotImplementedError

    def file(self, file_id: str) -> dict | None:
        """Metadata plus "data" (bytes) and "text"."""
        raise NotImplementedError

    def delete_file(self, file_id: str) -> bool:
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
        course contains it (any case, spaces ignored)."""
        raise NotImplementedError

    def delete_missed(self, missed_id: str) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def snapshot(self, project: str | None = None) -> Snapshot:
        """Instructions and (unless paused) memories, oldest first; with
        `project`, that project's instructions, memories (unless paused)
        and files too (a gone project: none)."""
        paused = self.paused

        def short(memories):
            return () if paused else tuple(
                {"id": m["id"], "text": m["text"], "untrusted": m["untrusted"]}
                for m in reversed(memories))
        snap = Snapshot(self.instructions(), short(self.list()), paused)
        found = self.project(project) if project else None
        if found is None:
            return snap
        files = tuple({k: f[k] for k in ("id", "name", "kind", "size", "pages", "chars")}
                      for f in self.files(project))
        return replace(snap, project={k: found[k] for k in ("id", "name", "instructions")},
                       project_memories=short(self.list(project)), project_files=files)

    def export(self) -> dict:
        """Everything kept, for a download (Settings, account export): a
        project's files as metadata (account.py adds the files themselves)."""
        projects = [{**p, "memories": self.list(p["id"]), "files": self.files(p["id"])}
                    for p in self.projects()]
        return {"instructions": self.instructions(), "memories": self.list(),
                "paused": self.paused, "missed": self.missed(), "projects": projects}


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
            self._con.executescript("BEGIN IMMEDIATE;\n" + STUDY_TABLE
                                    + PROJECT_TABLES + "\nCOMMIT;")

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
        keys = r.keys()
        return {"id": r["id"], "text": r["text"], "created": _iso(r["created"]),
                "source": r["source"], "untrusted": bool(r["untrusted"]),
                "project": r["project"] if "project" in keys else None}

    @staticmethod
    def _find(db, memory_id: str):
        """(table, row) of a memory, global or a project's; (None, None)."""
        for table in ("memories", "project_memories"):
            row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (memory_id,)).fetchone()
            if row is not None:
                return table, row
        return None, None

    def add(self, text: str, *, source: str | None = None,
            untrusted: bool = False, project: str | None = None) -> dict:
        text = clean_memory(text)
        source = str(source)[:MAX_SOURCE_CHARS] if source else None
        memory_id = ID_PREFIX + secrets.token_hex(6)
        if project is not None and not valid_project_id(project):
            raise NoSuchProject(project)

        def write(db):
            if self._paused(db):
                raise MemoryPaused()
            if project is None:
                count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if count >= MAX_MEMORIES:
                    raise MemoryFull()
                db.execute("INSERT INTO memories (id, text, created, source, untrusted) "
                           "VALUES (?, ?, ?, ?, ?)",
                           (memory_id, text, self.clock(), source, int(bool(untrusted))))
                return self._row(db.execute("SELECT * FROM memories WHERE id = ?",
                                            (memory_id,)).fetchone())
            if db.execute("SELECT 1 FROM projects WHERE id = ?", (project,)).fetchone() is None:
                raise NoSuchProject(project)
            count = db.execute("SELECT COUNT(*) FROM project_memories WHERE project = ?",
                               (project,)).fetchone()[0]
            if count >= MAX_PROJECT_MEMORIES:
                raise MemoryFull()
            db.execute("INSERT INTO project_memories (id, project, text, created, source, "
                       "untrusted) VALUES (?, ?, ?, ?, ?, ?)",
                       (memory_id, project, text, self.clock(), source,
                        int(bool(untrusted))))
            return self._row(db.execute("SELECT * FROM project_memories WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        log.info("Memory: saved %s (%d chars%s%s)", memory_id, len(text),
                 ", untrusted" if untrusted else "",
                 f", project {project}" if project else "")
        return saved

    def get(self, memory_id: str) -> dict | None:
        if not valid_id(memory_id):
            return None
        with self._lock:
            _table, row = self._find(self._con, memory_id)
        return self._row(row) if row else None

    def update(self, memory_id: str, text: str, *,
               untrusted: bool | None = None) -> dict | None:
        """New text for one memory (None if there is no such id). `untrusted`
        None keeps its flag."""
        text = clean_memory(text)
        if not valid_id(memory_id):
            return None

        def write(db):
            table, row = self._find(db, memory_id)
            if row is None:
                return None
            flag = row["untrusted"] if untrusted is None else int(bool(untrusted))
            db.execute(f"UPDATE {table} SET text = ?, untrusted = ? WHERE id = ?",
                       (text, flag, memory_id))
            return self._row(db.execute(f"SELECT * FROM {table} WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        if saved is not None:
            self._purge()
            log.info("Memory: edited %s (%d chars)", memory_id, len(text))
        return saved

    def delete(self, memory_id: str) -> bool:
        if not valid_id(memory_id):
            return False
        found = self._write(lambda db: sum(db.execute(
            f"DELETE FROM {table} WHERE id = ?", (memory_id,)).rowcount
            for table in ("memories", "project_memories")) > 0)
        if found:
            self._purge()
            self.deletions += 1
            log.info("Memory: deleted %s", memory_id)
        return found

    def trust(self, memory_id: str) -> dict | None:
        if not valid_id(memory_id):
            return None

        def write(db):
            table, row = self._find(db, memory_id)
            if row is None:
                return None
            db.execute(f"UPDATE {table} SET untrusted = 0 WHERE id = ?", (memory_id,))
            return self._row(db.execute(f"SELECT * FROM {table} WHERE id = ?",
                                        (memory_id,)).fetchone())
        saved = self._write(write)
        if saved is not None:
            log.info("Memory: %s kept as the person's own (trusted)", memory_id)
        return saved

    def list(self, project: str | None = None) -> list[dict]:
        """Every global memory, newest first (`project`: that project's)."""
        with self._lock:
            if project is None:
                rows = self._con.execute(
                    "SELECT * FROM memories ORDER BY created DESC, rid DESC").fetchall()
            else:
                rows = self._con.execute(
                    "SELECT * FROM project_memories WHERE project = ? "
                    "ORDER BY created DESC, rid DESC", (str(project),)).fetchall()
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
        """Delete every memory (every project's too) and the instructions
        (the pause setting, the projects themselves, their instructions and
        files stay). Returns how many memories went."""
        def write(db):
            n = db.execute("DELETE FROM memories").rowcount
            n += db.execute("DELETE FROM project_memories").rowcount
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
        # spaces and case ignored: "PHYS361" finds "PHYS 361"
        want = "".join(str(course or "").split()).casefold()
        if want:
            out = [m for m in out if want in "".join(m["course"].split()).casefold()]
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

    # ---------- projects (projects.py) ----------
    @staticmethod
    def _project_row(r, db) -> dict:
        counts = db.execute(
            "SELECT (SELECT COUNT(*) FROM project_memories WHERE project = ?), "
            "(SELECT COUNT(*) FROM project_files WHERE project = ?)",
            (r["id"], r["id"])).fetchone()
        return {"id": r["id"], "name": r["name"], "instructions": r["instructions"],
                "course": r["course"], "created": _iso(r["created"]),
                "updated": _iso(r["updated"]), "memories": counts[0], "files": counts[1]}

    def projects(self) -> list[dict]:
        """Every project, the most recently changed first."""
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM projects ORDER BY updated DESC, rid DESC").fetchall()
            return [self._project_row(r, self._con) for r in rows]

    def project(self, project_id: str) -> dict | None:
        if not valid_project_id(project_id):
            return None
        with self._lock:
            row = self._con.execute("SELECT * FROM projects WHERE id = ?",
                                    (project_id,)).fetchone()
            return self._project_row(row, self._con) if row else None

    def create_project(self, name: str, instructions: str = "",
                       course: str = "") -> dict:
        name = clean_project_name(name)
        instructions = clean_instructions(instructions or "")
        course = _one_line(course or "", "course", MAX_COURSE_KEY, required=False)
        project_id = PROJECT_PREFIX + secrets.token_hex(6)
        now = self.clock()

        def write(db):
            if course:
                row = db.execute("SELECT * FROM projects WHERE course = ?",
                                 (course,)).fetchone()
                if row is not None:  # made from this suggestion already
                    return self._project_row(row, db), False
            if db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= MAX_PROJECTS:
                raise ProjectsFull()
            db.execute("INSERT INTO projects (id, name, instructions, course, created, "
                       "updated) VALUES (?, ?, ?, ?, ?, ?)",
                       (project_id, name, instructions, course, now, now))
            return self._project_row(db.execute("SELECT * FROM projects WHERE id = ?",
                                                (project_id,)).fetchone(), db), True
        saved, made = self._write(write)
        if made:
            log.info("Projects: created %s (name %d chars, instructions %d chars%s)",
                     saved["id"], len(name), len(instructions),
                     ", suggested" if course else "")
        return saved

    def update_project(self, project_id: str, *, name: str | None = None,
                       instructions: str | None = None) -> dict | None:
        if name is not None:
            name = clean_project_name(name)
        if instructions is not None:
            instructions = clean_instructions(instructions)
        if not valid_project_id(project_id):
            return None

        def write(db):
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE projects SET name = ?, instructions = ?, updated = ? "
                       "WHERE id = ?",
                       (row["name"] if name is None else name,
                        row["instructions"] if instructions is None else instructions,
                        self.clock(), project_id))
            return self._project_row(db.execute("SELECT * FROM projects WHERE id = ?",
                                                (project_id,)).fetchone(), db)
        saved = self._write(write)
        if saved is not None:
            self._purge()  # the old name and instructions: gone
            log.info("Projects: %s changed (name %d chars, instructions %d chars)",
                     project_id, len(saved["name"]), len(saved["instructions"]))
        return saved

    def delete_project(self, project_id: str) -> bool:
        if not valid_project_id(project_id):
            return False

        def write(db):
            memories = db.execute("DELETE FROM project_memories WHERE project = ?",
                                  (project_id,)).rowcount
            files = db.execute("DELETE FROM project_files WHERE project = ?",
                               (project_id,)).rowcount
            found = db.execute("DELETE FROM projects WHERE id = ?",
                               (project_id,)).rowcount > 0
            return found, memories, files
        found, memories, files = self._write(write)
        if found:
            self._purge()
            self.deletions += 1
            log.info("Projects: deleted %s (%d memories, %d files)", project_id,
                     memories, files)
        return found

    # ---------- a project's files ----------
    @staticmethod
    def _file_row(r) -> dict:
        return {"id": r["id"], "project": r["project"], "name": r["name"],
                "kind": r["kind"], "size": r["size"], "pages": r["pages"],
                "chars": r["chars"], "created": _iso(r["created"])}

    _FILE_COLS = ("id, project, name, kind, size, pages, length(text) AS chars, created")

    def add_file(self, project_id: str, name: str, kind: str, data: bytes, *,
                 text: str = "", pages: int | None = None, max_files: int,
                 max_bytes: int, max_total_bytes: int) -> dict:
        if not valid_project_id(project_id):
            raise NoSuchProject(project_id)
        if kind not in FILE_KINDS or not isinstance(data, (bytes, bytearray)) or not data:
            raise FileRefused("That file can't be added")
        name = _one_line(name or "file", "file name", MAX_FILE_NAME * 2)[:MAX_FILE_NAME] \
            or "file"
        text = str(text or "")[:MAX_FILE_TEXT]
        size = len(data)
        if size > max_bytes:
            raise FileRefused(f"A project file is at most {max_bytes / 1_000_000:g} MB")
        file_id = FILE_PREFIX + secrets.token_hex(6)

        def write(db):
            self._room(db, project_id, size, max_files, max_total_bytes)
            now = self.clock()
            db.execute("INSERT INTO project_files (id, project, name, kind, size, pages, "
                       "data, text, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (file_id, project_id, name, kind, size,
                        int(pages) if pages else None, bytes(data), text, now))
            db.execute("UPDATE projects SET updated = ? WHERE id = ?", (now, project_id))
            return self._file_row(db.execute(
                f"SELECT {self._FILE_COLS} FROM project_files WHERE id = ?",
                (file_id,)).fetchone())
        saved = self._write(write)
        log.info("Projects: file %s added to %s (%s, %d bytes, %d chars of text)",
                 file_id, project_id, kind, size, len(text))
        return saved

    @staticmethod
    def _room(db, project_id: str, size: int, max_files: int, max_total_bytes: int) -> None:
        """NoSuchProject or FileRefused unless a file of `size` bytes fits."""
        if db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise NoSuchProject(project_id)
        count = db.execute("SELECT COUNT(*) FROM project_files WHERE project = ?",
                           (project_id,)).fetchone()[0]
        if count >= max_files:
            raise FileRefused(f"A project holds at most {max_files} files")
        used = db.execute("SELECT COALESCE(SUM(size), 0) FROM project_files").fetchone()[0]
        if used + size > max_total_bytes:
            raise FileRefused("Your project files are at their limit "
                              f"({max_total_bytes / 1_000_000:g} MB in all): "
                              "delete one first")

    def check_room(self, project_id: str, size: int, *, max_files: int,
                   max_total_bytes: int) -> None:
        """Before an upload is read (projects_web): would a file of `size`
        bytes fit? Read-only; add_file checks again in its transaction."""
        if not valid_project_id(project_id):
            raise NoSuchProject(project_id)
        with self._lock:
            self._room(self._con, project_id, int(size), max_files, max_total_bytes)

    def file_texts(self, project_id: str) -> list[dict]:
        """[{"id", "name", "text"}] of a project's files, oldest first, for
        search_project_files: never their bytes."""
        if not valid_project_id(project_id):
            return []
        with self._lock:
            rows = self._con.execute(
                "SELECT id, name, text FROM project_files WHERE project = ? "
                "ORDER BY created, rid", (project_id,)).fetchall()
        return [{"id": r["id"], "name": r["name"], "text": r["text"]} for r in rows]

    def files(self, project_id: str) -> list[dict]:
        if not valid_project_id(project_id):
            return []
        with self._lock:
            rows = self._con.execute(
                f"SELECT {self._FILE_COLS} FROM project_files WHERE project = ? "
                "ORDER BY created, rid", (project_id,)).fetchall()
        return [self._file_row(r) for r in rows]

    def file(self, file_id: str) -> dict | None:
        if not valid_file_id(file_id):
            return None
        with self._lock:
            row = self._con.execute(
                f"SELECT {self._FILE_COLS}, data, text FROM project_files WHERE id = ?",
                (file_id,)).fetchone()
        if row is None:
            return None
        return {**self._file_row(row), "data": bytes(row["data"]), "text": row["text"]}

    def file_bytes(self) -> int:
        """All of this person's project files, in bytes."""
        with self._lock:
            return self._con.execute("SELECT COALESCE(SUM(size), 0) FROM project_files"
                                     ).fetchone()[0]

    def delete_file(self, file_id: str) -> bool:
        if not valid_file_id(file_id):
            return False
        found = self._write(lambda db: db.execute(
            "DELETE FROM project_files WHERE id = ?", (file_id,)).rowcount > 0)
        if found:
            self._purge()
            self.deletions += 1
            log.info("Projects: file %s deleted", file_id)
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
