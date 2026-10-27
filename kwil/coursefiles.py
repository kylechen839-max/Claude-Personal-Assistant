"""Each person's course content from Canvas: the text of their active
courses' syllabus, pages, assignment descriptions, announcements, module
lists and files (PDF, Word, PowerPoint, HTML, text; textextract.py),
searchable, in one SQLite file per person: the owner's
`<[student] dir>/coursefiles.sqlite`, an invited person's
`<[family] dir>/<id>/coursefiles.sqlite` (deleted with their folder).

The API other modules use (`uid` None is the owner, else a family id):

  store = open_store(config)            # the process's store (one per config)
  store.upsert(uid, doc)                # one document's text (idempotent per source_key)
  store.search(uid, query, course)      # ranked snippets [{doc_id, course, title, kind, snippet}]
  store.read(uid, doc_id, offset)       # a stretch of one document's text
  store.delete_course(uid, course_id) / store.delete_all(uid)
  store.export(uid) / store.stats(uid)
  store.dated_texts(uid)                # texts that may hold dates, for the extractor
  sync_content(api, store, uid, ...)    # one person's courses, read over the API

Rules:
- Only extracted text is kept, never a file's bytes (they live in memory
  while one file is read). Text is not sealed at rest, like history.sqlite
  and deadlines.sqlite: access to the disk is the trust boundary.
- Bounded: [canvas] max_course_mb and max_person_mb of text, MAX_DOCS
  documents per person; a file over the bounds is listed, not read.
- Incremental: a file is downloaded again only when Canvas's updated_at,
  its size or whether it is locked changed; other documents are rewritten
  only when their text did. What Canvas no longer lists (a deleted page or
  file) is dropped; a course the person deleted is never read again.
- Every sync stops at the CanvasAPI's deadline ([canvas] content_seconds)
  and after [canvas] max_files downloads (newest first; the rest next run).
- Deleted means gone: secure_delete=ON, the search index's secure-delete
  option (SQLite 3.42+; older: an optimize after each delete), and a
  TRUNCATE checkpoint of the write-ahead log. Disconnecting Canvas deletes
  the whole file.
- A write for an invited person happens only while they are listed
  (member_guard, as deadlines.py), and only while the sync that read it is
  still current (`alive`: Disconnect during a sync wins).

Everything stored here was written by someone else (teachers, Canvas):
the search tools mark it untrusted (tools/coursefiles.py). Logs say ids,
counts, sizes and error codes, never a course name, title, text or URL.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

FILE = "coursefiles.sqlite"
KINDS = ("syllabus", "page", "assignment", "announcement", "file", "module")
ORIGINS = ("token", "helper")
MAX_DOCS = 5000            # documents per person
MAX_TITLE = 300
MAX_COURSE = 200
MAX_KEY = 200
MAX_HITS = 8               # search results
MAX_READ = 6000            # characters returned by one read
ANNOUNCEMENT_DAYS = 120    # announcements read from this long ago
MAX_PAGE_FETCHES = 60      # pages fetched one by one when a listing had no body
MAX_EXCLUDED = 500         # courses a person deleted, remembered so they stay gone
# files whose declared type is never read (no download at all)
_SKIP_MIME = ("image", "video", "audio", "flash", "xls", "zip")
_SKIP_TYPE = re.compile(r"^(image|video|audio)/|spreadsheet|ms-excel|zip|compressed", re.I)
_DATED = re.compile(r"syllabus|schedule|calendar|outline|\bexams?\b|\bdue\b", re.I)
MAX_DATED = 200            # documents dated_texts() offers per sync
# codes a later sync may get past: no row is kept, so the file is tried again
TRANSIENT = ("timed_out", "rate_limited", "no_pdf_reader", "full")

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    course TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    source_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'token',
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_course ON docs (course_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, text, content='docs', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, text)
        VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE OF title, text ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, text)
        VALUES ('delete', old.id, old.title, old.text);
    INSERT INTO docs_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
"""


def _squash(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", "".join(c for c in str(text or "") if c.isprintable()))
    return text.strip()[:limit]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text_etag(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class CourseStore:
    """Each person's course content (see the module docstring). Thread-safe:
    one lock around every read and write; each call opens its own short
    connection, so nothing holds a person's file open (their folder can be
    deleted on Windows)."""

    def __init__(self, owner_dir: str | Path, family_dir: str | Path,
                 max_course_bytes: int = 20_000_000, max_person_bytes: int = 100_000_000,
                 clock: Callable[[], float] = time.time, use_fts: bool | None = None):
        from kwil.history import fts5_available

        self.owner_dir = Path(owner_dir)
        self.family_dir = Path(family_dir)
        self.max_course_bytes = int(max_course_bytes)
        self.max_person_bytes = int(max_person_bytes)
        self.clock = clock
        self.fts = fts5_available() if use_fts is None else bool(use_fts)
        self.config: dict | None = None   # set by open_store
        self.lock = threading.RLock()
        # Family.while_listed (see deadlines.DeadlineStore.member_guard)
        self.member_guard: Callable[[str], object] | None = None

    @classmethod
    def from_config(cls, config: dict, clock=time.time) -> "CourseStore":
        c = config["canvas"]
        return cls(config["student"]["dir"], config["family"]["dir"],
                   max_course_bytes=int(float(c["max_course_mb"]) * 1_000_000),
                   max_person_bytes=int(float(c["max_person_mb"]) * 1_000_000), clock=clock)

    # ---------- files ----------
    def path(self, uid: str | None) -> Path:
        if uid is None:
            return self.owner_dir / FILE
        from kwil.family import valid_id

        if not valid_id(uid):
            raise KeyError("not a person")
        return self.family_dir / uid / FILE

    @contextmanager
    def _db(self, uid: str | None, write: bool, guard: bool = True,
            alive: Callable[[], bool] | None = None):
        """A connection to `uid`'s file, or None when reading a file that
        doesn't exist (a read never creates one) or when `alive()` says
        the sync writing is stale (checked under the lock, before a file is
        made). A guarded write for someone no longer listed raises
        KeyError and writes nothing."""
        path = self.path(uid)
        check = (self.member_guard(uid) if uid is not None and write and guard
                 and self.member_guard is not None else nullcontext(True))
        with check as listed, self.lock:
            if not listed:
                raise KeyError("no longer listed")
            if alive is not None and not alive():
                yield None
                return
            if not path.exists():
                if not write or not guard:
                    yield None
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(path), isolation_level=None, timeout=10)
            try:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA journal_mode = WAL")
                # deleted rows are overwritten with zeros, not just unlinked
                con.execute("PRAGMA secure_delete = ON")
                con.executescript(SCHEMA)
                self._ensure_fts(con)
                yield con
            finally:
                con.close()

    def _ensure_fts(self, con) -> None:
        """The search index (FTS5) when this SQLite has it; built from the
        table when missing (a file first used without FTS5). Without FTS5
        the index triggers are dropped (they would make every insert
        fail) and search falls back to LIKE."""
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'docs_%'")}
        if not self.fts:
            for name in ("docs_ai", "docs_ad", "docs_au"):
                if name in names:
                    con.execute(f"DROP TRIGGER {name}")
            return
        if {"docs_fts", "docs_ai", "docs_ad", "docs_au"} <= names:
            return
        con.executescript("BEGIN IMMEDIATE;\nDROP TABLE IF EXISTS docs_fts;\n" + FTS_SQL
                          + "INSERT INTO docs_fts(docs_fts) VALUES ('rebuild');\n"
                          "INSERT INTO docs_fts(docs_fts, rank) VALUES ('rank', "
                          "'bm25(4.0, 1.0)');\nCOMMIT;")
        try:  # SQLite 3.42+: a delete removes the words from the index at once
            con.execute("INSERT INTO docs_fts(docs_fts, rank) VALUES ('secure-delete', 1)")
        except sqlite3.Error:
            pass  # older: _purge optimizes instead

    def _purge(self, con) -> None:
        """After a delete: the deleted text out of the search index's
        segments and the write-ahead log, not just out of the table."""
        if self.fts:
            con.execute("INSERT INTO docs_fts(docs_fts) VALUES ('optimize')")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ---------- writing ----------
    def versions(self, uid: str | None) -> dict[str, tuple[str, str]]:
        """{source_key: (updated_at, etag)} of what is stored."""
        with self._db(uid, write=False) as con:
            if con is None:
                return {}
            return {r["source_key"]: (r["updated_at"], r["etag"]) for r in con.execute(
                "SELECT source_key, updated_at, etag FROM docs")}

    def room(self, uid: str | None, course_id: int) -> int:
        """Bytes of text that still fit for this course (and person)."""
        with self._db(uid, write=False) as con:
            if con is None:
                return min(self.max_course_bytes, self.max_person_bytes)
            person = con.execute("SELECT COALESCE(SUM(size), 0) FROM docs").fetchone()[0]
            course = con.execute("SELECT COALESCE(SUM(size), 0) FROM docs WHERE "
                                 "course_id = ?", (int(course_id),)).fetchone()[0]
        return max(0, min(self.max_course_bytes - course, self.max_person_bytes - person))

    def upsert(self, uid: str | None, doc: dict,
               alive: Callable[[], bool] | None = None) -> str:
        """Store one document: {course_id, course, kind, source_key, title,
        updated_at, etag, text, note?, origin?}. Returns "added", "updated",
        "unchanged", "skipped" (no room: a bound) or "stale" (`alive()`
        said the sync that read it is no longer current: nothing written).
        KeyError: `uid` was removed."""
        kind = doc.get("kind")
        key = _squash(doc.get("source_key"), MAX_KEY)
        if kind not in KINDS or not key:
            raise ValueError("not a course document")
        text = str(doc.get("text") or "")
        size = len(text.encode("utf-8"))
        origin = doc.get("origin") if doc.get("origin") in ORIGINS else "token"
        row = (int(doc.get("course_id") or 0), _squash(doc.get("course"), MAX_COURSE), kind,
               key, _squash(doc.get("title"), MAX_TITLE), str(doc.get("updated_at") or "")[:40],
               str(doc.get("etag") or "")[:64], size, text, str(doc.get("note") or "")[:40],
               origin)
        with self._db(uid, write=True, alive=alive) as con:
            if con is None:
                return "stale"
            old = con.execute("SELECT id, course_id, size, updated_at, etag, title, note, "
                              "course FROM docs WHERE source_key = ?", (key,)).fetchone()
            if old is not None and (old["updated_at"], old["etag"], old["title"],
                                    old["note"], old["course"], old["size"]) == \
                    (row[5], row[6], row[4], row[9], row[1], size):
                return "unchanged"
            freed = old["size"] if old is not None else 0
            same_course = old is not None and old["course_id"] == row[0]
            person = con.execute("SELECT COALESCE(SUM(size), 0) FROM docs").fetchone()[0]
            course = con.execute("SELECT COALESCE(SUM(size), 0) FROM docs WHERE "
                                 "course_id = ?", (row[0],)).fetchone()[0]
            if size and (person - freed + size > self.max_person_bytes
                         or course - (freed if same_course else 0) + size
                         > self.max_course_bytes):
                return "skipped"
            if old is None and con.execute("SELECT COUNT(*) FROM docs").fetchone()[0] \
                    >= MAX_DOCS:
                return "skipped"
            if old is None:
                con.execute("INSERT INTO docs (course_id, course, kind, source_key, title, "
                            "updated_at, etag, size, text, note, origin, fetched_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (*row, self.clock()))
                return "added"
            con.execute("UPDATE docs SET course_id = ?, course = ?, kind = ?, "
                        "source_key = ?, title = ?, updated_at = ?, etag = ?, size = ?, "
                        "text = ?, note = ?, origin = ?, fetched_at = ? WHERE id = ?",
                        (*row, self.clock(), old["id"]))
            return "updated"

    def forget(self, uid: str | None, keys: list[str],
               alive: Callable[[], bool] | None = None) -> int:
        """Drop the documents Canvas no longer lists."""
        if not keys:
            return 0
        with self._db(uid, write=True, guard=False, alive=alive) as con:
            if con is None:
                return 0
            con.execute("BEGIN IMMEDIATE")
            try:
                gone = sum(con.execute("DELETE FROM docs WHERE source_key = ?",
                                       (k,)).rowcount for k in keys)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            if gone:
                self._purge(con)
        return gone

    def excluded(self, uid: str | None) -> set[int]:
        """The courses the person deleted (delete_course): never synced again."""
        with self._db(uid, write=False) as con:
            if con is None:
                return set()
            return {int(r["key"].split(":", 1)[1]) for r in con.execute(
                "SELECT key FROM meta WHERE key LIKE 'excluded:%'")}

    def note_sync(self, uid: str | None, alive: Callable[[], bool] | None = None) -> None:
        with self._db(uid, write=True, alive=alive) as con:
            if con is None:
                return
            con.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)",
                        (_iso(self.clock()),))

    # ---------- deleting ----------
    def delete_course(self, uid: str | None, course_id: int) -> int:
        """Delete everything of one course for good, and leave it out of
        later syncs (until Canvas is disconnected). Returns the documents."""
        with self._db(uid, write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                gone = con.execute("DELETE FROM docs WHERE course_id = ?",
                                   (int(course_id),)).rowcount
                con.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')",
                            (f"excluded:{int(course_id)}",))
                con.execute("DELETE FROM meta WHERE key LIKE 'excluded:%' AND rowid NOT IN "
                            "(SELECT rowid FROM meta WHERE key LIKE 'excluded:%' ORDER BY "
                            "rowid DESC LIMIT ?)", (MAX_EXCLUDED,))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._purge(con)
        log.info("Course files: %s deleted course %d (%d document(s))", uid or "the owner",
                 int(course_id), gone)
        return gone

    def delete_all(self, uid: str | None) -> int:
        """Delete the person's whole content store (rows overwritten, the
        index and log emptied, then the files removed). Returns the
        documents there were."""
        from kwil.family import _unlink

        path = self.path(uid)
        with self.lock:
            gone = 0
            with self._db(uid, write=True, guard=False) as con:
                if con is not None:
                    gone = con.execute("DELETE FROM docs").rowcount
                    con.execute("DELETE FROM meta")
                    self._purge(con)
            for suffix in ("", "-wal", "-shm", "-journal"):
                _unlink(path.with_name(path.name + suffix))
        log.info("Course files: %s's content store deleted (%d document(s))",
                 uid or "the owner", gone)
        return gone

    # ---------- reading ----------
    def search(self, uid: str | None, query: str, course: str = "",
               limit: int = MAX_HITS) -> list[dict]:
        """The best matches, each {doc_id, course, title, kind, snippet}.
        All words must match; if nothing does, any word may. `course`: part
        of the course's name (or its Canvas id)."""
        from kwil.history import query_terms

        terms = query_terms(query)
        limit = max(1, min(int(limit), MAX_HITS))
        if not terms:
            return []
        want = str(course or "").strip()
        where, params = "d.text != ''", []
        if want.isdigit():
            where += " AND d.course_id = ?"
            params.append(int(want))
        elif want:
            where += " AND lower(d.course) LIKE ? ESCAPE '\\'"
            params.append("%" + _like_escape(want.lower()) + "%")
        with self._db(uid, write=False) as con:
            if con is None:
                return []
            hits = self._search(con, terms, where, params, limit, False)
            if not hits and len(terms) > 1:
                hits = self._search(con, terms, where, params, limit, True)
        return hits

    def _search(self, con, terms, where, params, limit, any_term) -> list[dict]:
        from kwil.history import ELLIPSIS, _fts_query, _like_snippet

        if self.fts:
            rows = con.execute(
                f"SELECT d.id, d.course, d.title, d.kind, snippet(docs_fts, -1, '', '', "
                f"'{ELLIPSIS}', 24) AS snip FROM docs_fts CROSS JOIN docs d "
                f"ON d.id = docs_fts.rowid WHERE docs_fts MATCH ? AND {where} "
                f"ORDER BY docs_fts.rank LIMIT ?",
                (_fts_query(terms, any_term), *params, limit)).fetchall()
            return [{"doc_id": r["id"], "course": r["course"], "title": r["title"],
                     "kind": r["kind"], "snippet": r["snip"]} for r in rows]
        clauses = " OR " if any_term else " AND "
        match = clauses.join("(d.text LIKE ? ESCAPE '\\' OR d.title LIKE ? ESCAPE '\\')"
                             for _ in terms)
        like = []
        for t in terms:
            pattern = "%" + _like_escape(t) + "%"
            like += [pattern, pattern]
        rows = con.execute(f"SELECT d.id, d.course, d.title, d.kind, d.text FROM docs d "
                           f"WHERE ({match}) AND {where} ORDER BY d.fetched_at DESC LIMIT ?",
                           (*like, *params, limit)).fetchall()
        return [{"doc_id": r["id"], "course": r["course"], "title": r["title"],
                 "kind": r["kind"], "snippet": _like_snippet(r["text"][:200_000], terms)
                 .replace("\x02", "").replace("\x03", "")} for r in rows]

    def read(self, uid: str | None, doc_id: int, offset: int = 0,
             max_chars: int = MAX_READ) -> dict | None:
        """{doc_id, course, title, kind, offset, length, text, next_offset}
        (next_offset None at the end), or None if there is no such id."""
        offset = max(0, int(offset))
        max_chars = max(1, min(int(max_chars), MAX_READ))
        with self._db(uid, write=False) as con:
            if con is None:
                return None
            row = con.execute("SELECT id, course, title, kind, length(text) AS n, "
                              "substr(text, ?, ?) AS piece FROM docs WHERE id = ?",
                              (offset + 1, max_chars, int(doc_id))).fetchone()
        if row is None:
            return None
        end = offset + len(row["piece"])
        return {"doc_id": row["id"], "course": row["course"], "title": row["title"],
                "kind": row["kind"], "offset": offset, "length": row["n"],
                "text": row["piece"], "next_offset": end if end < row["n"] else None}

    def dated_texts(self, uid: str | None) -> list[tuple[str, str]]:
        """[(key, text)] of the documents that may hold dates (syllabus,
        pages, announcements, files named like a schedule), newest first,
        for canvas.extract_from_pages; the key changes with the text, so
        DeadlineStore.unseen/mark_seen read each version once."""
        from kwil.canvas import MAX_TEXT

        with self._db(uid, write=False) as con:
            if con is None:
                return []
            rows = con.execute(
                "SELECT source_key, etag, course, kind, title, substr(text, 1, ?) AS t "
                "FROM docs WHERE text != '' AND kind IN ('syllabus', 'page', "
                "'announcement', 'file') ORDER BY fetched_at DESC, id DESC",
                (MAX_TEXT,)).fetchall()
        out = []
        for r in rows:
            if r["kind"] == "file" and not _DATED.search(r["title"]):
                continue
            out.append((f"{r['source_key']}:{r['etag']}",
                        f"Course: {r['course']}\n{r['kind'].title()}: {r['title']}\n"
                        f"{r['t']}"[:MAX_TEXT]))
            if len(out) >= MAX_DATED:
                break
        return out

    def export(self, uid: str | None) -> list[dict]:
        """Every document with its text ("Download my data")."""
        with self._db(uid, write=False) as con:
            if con is None:
                return []
            rows = con.execute("SELECT * FROM docs ORDER BY course_id, id").fetchall()
        return [{"id": r["id"], "course_id": r["course_id"], "course": r["course"],
                 "kind": r["kind"], "title": r["title"], "updated_at": r["updated_at"],
                 "origin": r["origin"], "note": r["note"], "text": r["text"]} for r in rows]

    def stats(self, uid: str | None) -> dict:
        """{courses: [{course_id, course, documents, read, bytes}], documents,
        bytes, last_sync}: what the card shows (no text)."""
        with self._db(uid, write=False) as con:
            if con is None:
                return {"courses": [], "documents": 0, "bytes": 0, "last_sync": None}
            rows = con.execute(
                "SELECT course_id, MAX(course) AS course, COUNT(*) AS n, "
                "SUM(text != '') AS got, COALESCE(SUM(size), 0) AS b FROM docs "
                "GROUP BY course_id ORDER BY course").fetchall()
            last = con.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
        courses = [{"course_id": r["course_id"], "course": r["course"], "documents": r["n"],
                    "read": r["got"] or 0, "bytes": r["b"]} for r in rows]
        return {"courses": courses, "documents": sum(c["documents"] for c in courses),
                "bytes": sum(c["bytes"] for c in courses),
                "last_sync": last["value"] if last is not None else None}


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- syncing
def _html(markup) -> str:
    from kwil.textextract import html_text, tidy

    return tidy(html_text(markup or ""), 400_000)


def _skip_file(f: dict, max_bytes: int) -> str | None:
    """Why a listed file isn't downloaded, from Canvas's metadata (the
    bytes are sniffed anyway when it is)."""
    if f.get("locked_for_user") or f.get("hidden_for_user") or f.get("hidden") \
            or f.get("locked"):
        return "locked"
    if not f.get("url"):
        return "locked"
    if str(f.get("mime_class") or "").lower() in _SKIP_MIME \
            or _SKIP_TYPE.search(str(f.get("content-type") or "")):
        return "unsupported"
    try:
        if int(f.get("size") or 0) > max_bytes:
            return "too_large"
    except (TypeError, ValueError):
        pass
    return None


def sync_content(api, store: CourseStore, uid: str | None, *, max_files: int = 40,
                 max_file_bytes: int = 20_000_000, alive: Callable[[], bool] | None = None,
                 now: datetime | None = None) -> dict:
    """Read one person's active courses over `api` (a canvas.CanvasAPI,
    whose deadline bounds the whole run) into `store`. Returns counts
    {added, updated, unchanged, skipped, removed, files, more, courses}
    (files: downloads this run; more: files left for the next run). A rate
    limit, the deadline or a revoked token stop the run (CanvasError; what
    was stored stays), `alive()` going false stops it quietly; one
    course's hidden tab or a missing page skips only that listing."""
    from kwil import canvas
    from kwil.textextract import ExtractError, extract

    now = now or datetime.now(timezone.utc)
    counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "removed": 0,
              "files": 0, "more": 0, "courses": 0}
    known = store.versions(uid)
    seen: set[str] = set()
    complete: list[tuple[str, int]] = []   # (prefix, course) listings read in full

    class Stale(Exception):
        pass

    def put(doc: dict) -> None:
        result = store.upsert(uid, doc, alive)
        if result == "stale":
            raise Stale()
        counts[result] += 1

    def text_doc(cid, course, kind, key, title, body, updated=""):
        body = body.strip()
        if not body:
            return  # an emptied syllabus or page: its old text is dropped below
        seen.add(key)
        put({"course_id": cid, "course": course, "kind": kind, "source_key": key,
             "title": title, "updated_at": updated, "etag": text_etag(title + "\n" + body),
             "text": body})

    def listing(read):
        """read(), or None when that one listing can't be read (a hidden
        tab, a 404); a rate limit, the deadline or a revoked token stop
        the whole run."""
        try:
            return read()
        except canvas.CanvasError as e:
            if str(e) in ("rate_limited", "timed_out", "reconnect"):
                raise
            counts["skipped"] += 1
            return None

    try:
        skip = store.excluded(uid)
        courses = [c for c in api.courses(syllabus=True) if int(c["id"]) not in skip]
        counts["courses"] = len(courses)
        names = {int(c["id"]): str(c.get("name") or c.get("course_code") or "")
                 for c in courses}
        pending_files: list[tuple[int, dict]] = []
        for c in courses:
            cid, name = int(c["id"]), names[int(c["id"])]
            text_doc(cid, name, "syllabus", f"syllabus:{cid}:0", "Syllabus",
                     _html(c.get("syllabus_body")))
            complete.append(("syllabus:", cid))
            pages = listing(lambda: api.pages(cid))
            if pages is not None:
                fetched = 0
                for p in pages:
                    if not isinstance(p, dict) or p.get("locked_for_user") \
                            or p.get("published") is False or not p.get("url"):
                        continue
                    key = f"page:{cid}:{p.get('page_id') or p['url']}"
                    body = p.get("body")
                    if body is None and known.get(key, ("",))[0] == str(
                            p.get("updated_at") or "") and p.get("updated_at"):
                        seen.add(key)   # unchanged: not fetched again
                        continue
                    if body is None and fetched < MAX_PAGE_FETCHES \
                            and re.fullmatch(r"[\w.-]{1,200}", str(p["url"])):
                        fetched += 1
                        full = listing(lambda: api.page(cid, p["url"]))
                        body = (full or {}).get("body")
                    if body is None and key in known:
                        seen.add(key)   # not read this run: the old text stays
                        continue
                    text_doc(cid, name, "page", key, str(p.get("title") or ""), _html(body),
                             str(p.get("updated_at") or ""))
                complete.append(("page:", cid))
            work = listing(lambda: api.assignments(cid))
            if work is not None:
                for a in work:
                    if not isinstance(a, dict) or a.get("id") is None:
                        continue
                    due = f"Due: {a['due_at']}\n" if a.get("due_at") else ""
                    body = _html(a.get("description"))
                    text_doc(cid, name, "assignment", f"assignment:{cid}:{a['id']}",
                             str(a.get("name") or ""), (due + body) if body else "",
                             str(a.get("updated_at") or ""))
                complete.append(("assignment:", cid))
            modules = listing(lambda: api.modules(cid))
            if modules is not None:
                lines = []
                for m in modules:
                    if not isinstance(m, dict):
                        continue
                    lines.append(f"Module: {m.get('name') or ''}")
                    for item in m.get("items") or []:
                        if isinstance(item, dict):
                            lines.append(f"- {item.get('title') or ''} ({item.get('type')})")
                text_doc(cid, name, "module", f"module:{cid}:0", "Modules", "\n".join(lines))
                complete.append(("module:", cid))
            files = listing(lambda: api.files(cid))
            if files is not None:
                pending_files += [(cid, f) for f in files if isinstance(f, dict)
                                  and f.get("id") is not None]
                complete.append(("file:", cid))
        if courses:
            start = (now - timedelta(days=ANNOUNCEMENT_DAYS)).date()
            news = listing(lambda: api.announcements(list(names), start))
            for n in news or []:
                if not isinstance(n, dict) or n.get("id") is None:
                    continue
                m = re.fullmatch(r"course_(\d+)", str(n.get("context_code") or ""))
                if not m or int(m.group(1)) not in names:
                    continue
                cid = int(m.group(1))
                text_doc(cid, names[cid], "announcement", f"announcement:{cid}:{n['id']}",
                         str(n.get("title") or ""), _html(n.get("message")),
                         str(n.get("posted_at") or ""))
            if news is not None:
                complete += [("announcement:", cid) for cid in names]
        # files last, newest first, at most max_files downloads a run
        pending_files.sort(key=lambda x: str(x[1].get("updated_at") or ""), reverse=True)
        for cid, f in pending_files:
            key = f"file:{cid}:{f['id']}"
            seen.add(key)
            title = str(f.get("display_name") or f.get("filename") or "")
            code = _skip_file(f, max_file_bytes)
            # a new version, or a file locked or unlocked since: read again
            version = (f"{f.get('updated_at') or f.get('modified_at') or ''}|"
                       f"{f.get('size')}|{code or ''}")
            if known.get(key, ("",))[0] == version:
                counts["unchanged"] += 1
                continue
            doc = {"course_id": cid, "course": names[cid], "kind": "file", "source_key": key,
                   "title": title, "updated_at": version, "etag": "", "text": ""}
            if code is None and counts["files"] >= max_files:
                counts["more"] += 1
                continue  # the next run gets it
            if code is None and store.room(uid, cid) <= 0:
                code = "full"
            if code is None:
                counts["files"] += 1
                try:
                    data = api.download(str(f["url"]), max_file_bytes)
                    _, text = extract(data, deadline=api.deadline, clock=api.clock)
                    del data
                    doc.update(text=text, etag=text_etag(text))
                except canvas.CanvasError as e:
                    if str(e) in ("rate_limited", "timed_out"):
                        raise
                    code = "too_large" if str(e) == "too_large" else "unavailable"
                except ExtractError as e:
                    code = str(e)
            if code is not None:
                if code in TRANSIENT:
                    counts["skipped"] += 1
                    continue
                doc["note"] = code
            put(doc)
        # what Canvas no longer lists, from listings read in full
        gone = [k for k in known if k not in seen and any(
            k.startswith(f"{prefix}{cid}:") for prefix, cid in complete)]
        if gone:
            counts["removed"] = store.forget(uid, gone, alive)
        store.note_sync(uid, alive)
    except Stale:
        log.info("Course files: %s disconnected during a sync; stopped", uid or "the owner")
        return counts
    log.info("Course files: %s synced %d course(s): %d added, %d updated, %d unchanged, "
             "%d skipped, %d removed, %d file(s) downloaded", uid or "the owner",
             counts["courses"], counts["added"], counts["updated"], counts["unchanged"],
             counts["skipped"], counts["removed"], counts["files"])
    return counts


# One store per (owner dir, family dir) in this process, shared by the
# owner's tools (cli.register_student), Family and the routes
# (student_web), so they share one lock (as deadlines.open_store).
_STORES: OrderedDict[tuple[str, str], CourseStore] = OrderedDict()
_STORES_LOCK = threading.Lock()
MAX_STORES = 16


def open_store(config: dict) -> CourseStore:
    """The process's CourseStore for `config` (made on first use)."""
    key = (str(Path(config["student"]["dir"]).resolve()),
           str(Path(config["family"]["dir"]).resolve()))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = CourseStore.from_config(config)
            while len(_STORES) > MAX_STORES:
                _STORES.popitem(last=False)
        else:
            _STORES.move_to_end(key)
            c = config["canvas"]
            store.max_course_bytes = int(float(c["max_course_mb"]) * 1_000_000)
            store.max_person_bytes = int(float(c["max_person_mb"]) * 1_000_000)
        store.config = config
        return store
